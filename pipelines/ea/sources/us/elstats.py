import io
import json
import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.county_changes import ct_regions
from ea.sources.us.openelections import HOUSE, SENATE
from ea.store import DATA, connect

SOURCE = "us.elstats"
VERSION = "7"
PARSED = DATA / "parsed" / SOURCE
FIRST, LAST = 1982, 2015
PRIMARIES = 2022
EARLY = {"CO": 1902, "CT": 1789, "VA": 1924, "VT": 1789}
ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
SEARCH = "https://{tenant}.civera.com/api/download_search.csv?"
TABLE = "https://{tenant}.civera.com/api/download_contest/{contest}_table.csv?split_party=false"
GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_cousubs_national.zip"
TENANTS = {
    "CO": ("co.elstats", "Colorado Secretary of State", "https://historicalelectiondata.coloradosos.gov"),
    "CT": ("ct.elstats", "Connecticut Secretary of the State", "https://electionhistory.ct.gov"),
    "ID": ("id.elstats", "Idaho Secretary of State", "https://idaho.electionstats.com"),
    "NH": ("nh2.elstats", "New Hampshire Public Radio", "https://electiondatabase.nhpr.org"),
    "NM": ("nm.elstats3", "New Mexico Secretary of State", "https://electionresults.sos.nm.gov"),
    "NY": ("ny.elstats3", "New York State Board of Elections", "https://results.elections.ny.gov"),
    "VT": ("vt2.elstats", "Vermont Secretary of State", "https://electionarchive.vermont.gov"),
    "VA": ("va2.elstats", "Virginia Department of Elections", "https://historical.elections.virginia.gov"),
}
HELD = {"NH": "No terms stated; held out of releases while NHPR's permission to redistribute is pending"}
MA_PUBLISHER = "Massachusetts Secretary of the Commonwealth"
TITLE = "Election statistics archive"
MASSACHUSETTS = "https://electionstats.state.ma.us/elections/"
MA_OFFICES = {5: "house", 6: "senate"}
MA_ROW = re.compile(
    r'id="election-id-(\d+)".*?<td[^>]*>(\d{4})</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>',
    re.DOTALL,
)
MA_PRIMARIES = {
    1992: date(1992, 9, 15),
    1994: date(1994, 9, 20),
    1996: date(1996, 9, 17),
    1998: date(1998, 9, 15),
    2000: date(2000, 9, 19),
    2002: date(2002, 9, 17),
    2004: date(2004, 9, 14),
    2006: date(2006, 9, 19),
    2008: date(2008, 9, 16),
    2010: date(2010, 9, 14),
    2012: date(2012, 9, 6),
    2014: date(2014, 9, 9),
    2016: date(2016, 9, 8),
    2018: date(2018, 9, 4),
    2020: date(2020, 9, 1),
    2022: date(2022, 9, 6),
}
MA_FIRST = 1970
OFFICES = ["U.S. House", "U.S. Senate", "United States", "Congress"]
DIVISIONS = {"County": "county", "Locality": "county", "City/Town": "town"}
FORMER = {
    ("VA", "bedfordcity"): "51515",
    ("VA", "southbostoncity"): "51083",
    ("VA", "cliftonforgecity"): "51005",
    ("VT", "sherburne"): "50021",
    ("NH", "wentworthslocation"): "33007",
    ("CT", "saybrook"): "09007",
    ("CT", "chatham"): "09007",
    ("CT", "huntington"): "09001",
    ("CT", "southlyme"): "09011",
    ("VT", "salem"): "50019",
    ("VT", "mansfield"): "50015",
    ("VT", "sterling"): "50015",
    ("VT", "acton"): "50025",
}
SUFFIXES = r"\b(town|city|gore|grant|location|purchase|plantation|township|village)\b"


def url(tenant: str, year: int) -> str:
    search = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {
            "candidates": [],
            "divisions": [],
            "offices": [{"free": office} for office in [*OFFICES, *(["President"] if year < FIRST else [])]],
        },
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return SEARCH.format(tenant=tenant) + urlencode({"search": json.dumps(search, separators=(",", ":"))})


def gaps(tenant: str, address: str, fetching: bool = False, source: str = SOURCE) -> list[str]:
    frame = pl.read_csv(
        (raw.fetch(source, address, suffix=".csv") if fetching else raw.cached(source, address)).path,
        infer_schema=False,
        truncate_ragged_lines=True,
    )
    if "contest_id" not in frame.columns or frame.height == 0:
        return []
    links = [
        TABLE.format(tenant=tenant, contest=contest)
        for contest in frame.group_by("contest_id")
        .agg(pl.col("votes").cast(pl.Float64, strict=False).sum())
        .filter((pl.col("votes") == 0) & pl.col("contest_id").str.contains(r"^\d+$"))["contest_id"]
        .sort()
    ]
    if fetching:
        for link in links:
            raw.fetch(source, link, suffix=".csv")
    return links


def searched(tenant: str, address: str, source: str = SOURCE) -> pl.DataFrame:
    frame = pl.read_csv(raw.cached(source, address).path, infer_schema=False, truncate_ragged_lines=True)
    squeeze = pl.col("candidate_name").fill_null("").str.replace_all(r"\s+", " ").str.strip_chars()
    for link in gaps(tenant, address, source=source):
        table = pl.read_csv(raw.cached(source, link).path, infer_schema=False, has_header=False)
        names = [re.sub(r"\s+", " ", name or "").strip() for name in table.row(0)]
        filled = pl.DataFrame(
            [
                {"division_type": row[0], "division_name": row[1], "key": names[index], "votes": value}
                for row in table.slice(2).iter_rows()
                for index, value in enumerate(row)
                if index >= 2 and names[index]
            ],
            schema={"division_type": pl.String, "division_name": pl.String, "key": pl.String, "votes": pl.String},
        )
        filled = filled.filter(pl.col("votes").cast(pl.Float64, strict=False).sum().over("division_type") > 0)
        if filled.height == 0:
            continue
        contest = link.rsplit("/", 1)[1].split("_", 1)[0]
        rows = frame.filter(pl.col("contest_id") == contest)
        rows = (
            filled.join(
                rows.with_columns(key=squeeze)
                .unique("key", keep="first", maintain_order=True)
                .drop("division_type", "division_name", "division_id", "votes"),
                on="key",
            )
            .join(
                rows.select("division_type", "division_name", "division_id").unique(
                    ["division_type", "division_name"], keep="first"
                ),
                on=["division_type", "division_name"],
                how="left",
            )
            .drop("key")
            .with_columns(vote_channel=pl.lit("Total"))
            .select(frame.columns)
        )
        frame = pl.concat([frame.filter(pl.col("contest_id") != contest), rows])
    return frame


def ma_search(office: int) -> str:
    return f"{MASSACHUSETTS}search/year_from:{MA_FIRST}/year_to:{LAST}/office_id:{office}/stage:General"


def ma_primary_search(office: int, year: int) -> str:
    return f"{MASSACHUSETTS}search/year_from:{year}/year_to:{year}/office_id:{office}"


def ma_primary_rows(listing: str) -> list[tuple[str, ...]]:
    return [row for row in MA_ROW.findall(listing) if re.fullmatch(r"(Democratic|Republican) Primary", row[3])]


def plain(text: str) -> str:
    return re.sub(
        r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower().replace("saint ", "st ").replace("&", "and")
    )


def fetch() -> None:
    raw.fetch(SOURCE, GAZETTEER, suffix=".zip")
    for office in MA_OFFICES:
        listing = raw.fetch(SOURCE, ma_search(office), suffix=".html").path.read_text()
        for election, *_ in MA_ROW.findall(listing):
            raw.fetch(SOURCE, f"{MASSACHUSETTS}download/{election}/precincts_include:0/", suffix=".csv")
        for year in MA_PRIMARIES:
            listing = raw.fetch(SOURCE, ma_primary_search(office, year), suffix=".html").path.read_text()
            for election, *_ in ma_primary_rows(listing):
                raw.fetch(SOURCE, f"{MASSACHUSETTS}download/{election}/precincts_include:0/", suffix=".csv")
    for state, (tenant, _, _) in TENANTS.items():
        for year in range(EARLY.get(state, FIRST), PRIMARIES + 1):
            gaps(tenant, url(tenant, year), fetching=True)


def parse() -> None:
    with connect() as conn:
        counties: dict[str, dict[str, str]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515' order by u.code"""
        ).fetchall():
            names = counties.setdefault(iso.removeprefix("US-"), {})
            if int(code[2:]) >= 500:
                names[f"{plain(text)}city"] = code
            names.setdefault(plain(text), code)
        days: dict[tuple[str, str, int, int | None], list[date]] = {}
        months: dict[tuple[str, str, int, int, int | None], date] = {}
        for (value,) in conn.execute(
            "select value from external_id where scheme = 'us.contest' and value ~ '^\\d{4}-\\d\\d(-\\d\\d)?:(CO|CT|VA|VT):'"
        ).fetchall():
            held, state, chamber, *rest = value.split(":")
            if chamber in ("house", "senate", "president") and (not rest or (len(rest) == 1 and rest[0].isdigit())):
                day = date.fromisoformat(held if len(held) > 7 else f"{held}-01")
                district = int(rest[0]) if rest else None
                months.setdefault((state, chamber, day.year, day.month, district), day)
                if len(held) > 7:
                    days.setdefault((state, chamber, day.year, district), []).append(day)
    with zipfile.ZipFile(raw.cached(SOURCE, GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS").is_in([*TENANTS, "MA"]))
    towns: dict[str, dict[str, str]] = {}
    for state, geoid, name in (
        gazetteer.sort(pl.col("NAME").str.contains(r" (gore|grant|location|purchase|UT)$"))
        .select("USPS", "GEOID", "NAME")
        .iter_rows()
    ):
        names = towns.setdefault(state, {})
        names.setdefault(plain(name), geoid[:5])
        names.setdefault(plain(re.sub(SUFFIXES, "", name.lower())), geoid[:5])
    for (state, name), code in FORMER.items():
        counties.setdefault(state, {})[name] = code
        towns.setdefault(state, {})[name] = code
    regions = ct_regions()
    frames = []
    for state, (tenant, _, _) in TENANTS.items():
        for year in range(EARLY.get(state, FIRST), PRIMARIES + 1):
            frame = searched(tenant, url(tenant, year))
            if "office_name" not in frame.columns or frame.height == 0:
                continue
            office = pl.col("office_name").fill_null("").str.to_lowercase()
            division = pl.col("division_name").fill_null("").str.replace(r"\s*\(.*\)$", "")
            frames.append(
                frame.with_columns(
                    chamber=pl.when(office.is_in(["president", "presidential electors"]))
                    .then(pl.lit("president"))
                    .when(office.str.contains(SENATE.pattern) & ~office.str.contains(r"^state\b"))
                    .then(pl.lit("senate"))
                    .when(office.str.contains(HOUSE.pattern) & ~office.str.contains(r"\bstate\b"))
                    .then(pl.lit("house"))
                )
                .filter(
                    pl.col("chamber").is_not_null()
                    & pl.col("division_type").is_in(list(DIVISIONS))
                    & (
                        pl.col("election_type").str.contains("(?i)general") & (year <= LAST)
                        | (
                            pl.col("election_type").str.contains("(?i)primary")
                            & pl.col("chamber").is_in(["senate", "house"])
                            & pl.col("primary_party").is_not_null()
                        )
                    )
                )
                .with_columns(
                    chamber=pl.when(pl.col("election_type").str.contains("(?i)primary"))
                    .then(
                        pl.concat_str(
                            "chamber",
                            pl.lit(":"),
                            pl.col("primary_party").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", "-"),
                        )
                    )
                    .otherwise("chamber")
                )
                .select(
                    year=pl.col("election_date").str.slice(0, 4).cast(pl.Int32),
                    month=pl.col("election_date").str.slice(5, 2).cast(pl.Int8),
                    chamber="chamber",
                    day=pl.when(pl.col("election_date").str.slice(5, 2) == "11")
                    .then(
                        pl.date(pl.col("election_date").str.slice(0, 4).cast(pl.Int32), 11, 2)
                        + pl.duration(
                            days=(
                                1 - pl.date(pl.col("election_date").str.slice(0, 4).cast(pl.Int32), 11, 1).dt.weekday()
                            )
                            % 7
                        )
                    )
                    .otherwise(pl.col("election_date").str.slice(0, 10).str.to_date()),
                    special=pl.concat_str(
                        pl.col("election_type").fill_null(""), pl.col("office_modifier").fill_null("")
                    ).str.contains("(?i)special|unexpired"),
                    state=pl.lit(state),
                    district=pl.when(pl.col("chamber").str.starts_with("house")).then(
                        pl.coalesce(
                            pl.col("district_name").str.extract(r"(\d+)").cast(pl.Int16, strict=False),
                            pl.col("district_name")
                            .str.to_lowercase()
                            .replace_strict(
                                {word: n for n, word in enumerate(ORDINALS, 1)}, default=None, return_dtype=pl.Int16
                            ),
                        )
                    ),
                    code=pl.when(
                        (pl.lit(state) == "CT") & (pl.col("election_date").str.slice(0, 4).cast(pl.Int32) >= 2022)
                    )
                    .then(division.map_elements(plain, return_dtype=pl.String).replace_strict(regions, default=None))
                    .when(pl.col("division_type").replace_strict(DIVISIONS) == "county")
                    .then(
                        division.str.to_lowercase()
                        .str.replace_all(r"saint ", "st ")
                        .str.replace_all("&", "and")
                        .str.replace_all(r"\b(county|parish|borough)\b", "")
                        .str.replace_all(r"[^a-z]", "")
                        .replace_strict(counties[state], default=None)
                    )
                    .otherwise(
                        pl.coalesce(
                            division.map_elements(plain, return_dtype=pl.String).replace_strict(
                                towns.get(state, {}), default=None
                            ),
                            division.str.to_lowercase()
                            .str.replace_all(SUFFIXES, "")
                            .map_elements(plain, return_dtype=pl.String)
                            .replace_strict(towns.get(state, {}), default=None),
                        )
                    ),
                    county=pl.concat_str(pl.lit(f"{state}:"), division),
                    candidate=pl.col("candidate_name").fill_null("").str.to_uppercase().str.strip_chars(),
                    votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
                )
            )
    for office, chamber in MA_OFFICES.items():
        entries = MA_ROW.findall(raw.cached(SOURCE, ma_search(office)).path.read_text())
        for primary_year in MA_PRIMARIES:
            entries += ma_primary_rows(raw.cached(SOURCE, ma_primary_search(office, primary_year)).path.read_text())
        for election, year, district, stage in entries:
            if "Special" in stage:
                continue
            party = stage.removesuffix(" Primary").lower() if stage.endswith(" Primary") else None
            table = pl.read_csv(
                raw.cached(SOURCE, f"{MASSACHUSETTS}download/{election}/precincts_include:0/").path,
                infer_schema=False,
                skip_rows_after_header=1,
            )
            held = (
                MA_PRIMARIES[int(year)] if party else date(int(year), 11, 2 + (-date(int(year), 11, 1).weekday()) % 7)
            )
            frames.append(
                table.filter(pl.col("City/Town").is_not_null() & (pl.col("City/Town") != "TOTALS"))
                .with_columns(
                    pl.col("City/Town")
                    .str.replace(r"^N\. ", "North ")
                    .str.replace(r"^S\. ", "South ")
                    .str.replace(r"^E\. ", "East ")
                    .str.replace(r"^W\. ", "West ")
                )
                .unpivot([column for column in table.columns[3:] if column], index="City/Town", variable_name="name")
                .select(
                    year=pl.lit(int(year), pl.Int32),
                    chamber=pl.lit(f"{chamber}:{party}" if party else chamber),
                    day=pl.lit(held),
                    special=pl.lit(False),
                    state=pl.lit("MA"),
                    district=pl.lit(int(district.split()[0][:-2]) if chamber == "house" else None, pl.Int16),
                    code=pl.col("City/Town")
                    .map_elements(plain, return_dtype=pl.String)
                    .replace_strict(towns["MA"], default=None),
                    county=pl.concat_str(pl.lit("MA:"), "City/Town"),
                    candidate=pl.col("name").str.to_uppercase().str.strip_chars(),
                    votes=pl.col("value").str.replace_all(",", "").cast(pl.Int64, strict=False),
                )
            )
    frame = pl.concat(frames, how="diagonal_relaxed").filter(pl.col("votes").is_not_null())
    snapped = {
        (state, chamber, year, district): held[0]
        for (state, chamber, year, district), held in days.items()
        if year < FIRST and len(held) == 1
    }
    frame = frame.with_columns(
        day=pl.when((pl.col("year") < FIRST) & (pl.col("state") != "MA"))
        .then(
            pl.struct("state", "chamber", "year", "month", "district").map_elements(
                lambda row: (
                    months.get((row["state"], row["chamber"], row["year"], row["month"], row["district"]))
                    or (
                        months.get((row["state"], row["chamber"], row["year"], row["month"], 0))
                        if row["district"] is None
                        else None
                    )
                    or snapped.get((row["state"], row["chamber"], row["year"], row["district"]))
                    or (
                        snapped.get((row["state"], row["chamber"], row["year"], 0)) if row["district"] is None else None
                    )
                ),
                return_dtype=pl.Date,
            )
        )
        .otherwise("day")
    ).filter(pl.col("day").is_not_null())
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        print(lost.group_by("county").agg(pl.col("votes").sum()).sort("votes", descending=True).head(40))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.filter(pl.col("code").is_not_null())
        .with_columns(
            measure=pl.col("candidate").replace_strict(
                {**medsl.PSEUDO, "BLANK/VOID/SCATTERING": 0}, default=1, return_dtype=pl.Int16
            ),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE|ALL OTHER"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source="state")
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House, Senate, and presidential general elections and House and Senate primaries by county from state "
            "election archives",
            polity="US",
        )
        sources: dict[str, UUID] = {}
        for state, (tenant, publisher, site) in TENANTS.items():
            if state not in frame["state"]:
                continue
            sources[state] = cs.source(publisher, TITLE)
            cs.add(
                "source",
                id=sources[state],
                kind="aggregator" if state in HELD else "official_body",
                publisher=publisher,
                title=TITLE,
                url=site,
                license=HELD.get(state, "Public record"),
            )
            for year in frame.filter(pl.col("state") == state)["year"].unique().sort():
                cs.artifact(raw.cached(SOURCE, url(tenant, year)), sources[state])
                for link in gaps(tenant, url(tenant, year)):
                    cs.artifact(raw.cached(SOURCE, link), sources[state])
        sources["MA"] = cs.source(MA_PUBLISHER, TITLE)
        cs.add(
            "source",
            id=sources["MA"],
            kind="official_body",
            publisher=MA_PUBLISHER,
            title=TITLE,
            url="https://electionstats.state.ma.us",
            license="Public record",
        )
        for office in MA_OFFICES:
            listing = raw.cached(SOURCE, ma_search(office))
            cs.artifact(listing, sources["MA"])
            for election, *_ in MA_ROW.findall(listing.path.read_text()):
                cs.artifact(
                    raw.cached(SOURCE, f"{MASSACHUSETTS}download/{election}/precincts_include:0/"), sources["MA"]
                )
            for year in MA_PRIMARIES:
                listing = raw.cached(SOURCE, ma_primary_search(office, year))
                cs.artifact(listing, sources["MA"])
                for election, *_ in ma_primary_rows(listing.path.read_text()):
                    cs.artifact(
                        raw.cached(SOURCE, f"{MASSACHUSETTS}download/{election}/precincts_include:0/"),
                        sources["MA"],
                    )
        gazetteer = cs.source("U.S. Census Bureau", "2020 Gazetteer Files")
        if gazetteer in cs.minted:
            cs.add(
                "source",
                id=gazetteer,
                kind="official_body",
                publisher="U.S. Census Bureau",
                title="2020 Gazetteer Files",
                url="https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html",
                license="Public domain",
            )
        cs.artifact(raw.cached(SOURCE, GAZETTEER), gazetteer)
        medsl.attach(cs, conn, frame, sources)
        return cs.write()
