import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.openelections import HOUSE, SENATE
from ea.store import DATA, connect

SOURCE = "us.elstats"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FIRST, LAST = 1982, 2015
SEARCH = "https://{tenant}.civera.com/api/download_search.csv?"
GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_cousubs_national.zip"
TENANTS = {
    "CO": ("co.elstats", "Colorado Secretary of State", "https://historicalelectiondata.coloradosos.gov"),
    "CT": ("ct.elstats", "Connecticut Secretary of the State", "https://electionhistory.ct.gov"),
    "ID": ("id.elstats", "Idaho Secretary of State", "https://idaho.electionstats.com"),
    "NH": ("nh2.elstats", "New Hampshire Secretary of State", "https://nh.electionstats.com"),
    "NM": ("nm.elstats3", "New Mexico Secretary of State", "https://electionresults.sos.nm.gov"),
    "NY": ("ny.elstats3", "New York State Board of Elections", "https://results.elections.ny.gov"),
    "VT": ("vt2.elstats", "Vermont Secretary of State", "https://electionarchive.vermont.gov"),
    "VA": ("va2.elstats", "Virginia Department of Elections", "https://historical.elections.virginia.gov"),
}
TITLE = "Election statistics archive"
OFFICES = ["U.S. House", "U.S. Senate", "United States", "Congress"]
DIVISIONS = {"County": "county", "Locality": "county", "City/Town": "town"}
FORMER = {
    ("VA", "bedfordcity"): "51515",
    ("VA", "southbostoncity"): "51083",
    ("VA", "cliftonforgecity"): "51005",
    ("VT", "sherburne"): "50021",
    ("NH", "wentworthslocation"): "33007",
}
SUFFIXES = r"\b(town|city|gore|grant|location|purchase|plantation|township|village)\b"


def url(tenant: str, year: int) -> str:
    search = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"free": office} for office in OFFICES]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return SEARCH.format(tenant=tenant) + urlencode({"search": json.dumps(search, separators=(",", ":"))})


def plain(text: str) -> str:
    return re.sub(
        r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower().replace("saint ", "st ").replace("&", "and")
    )


def fetch() -> None:
    raw.fetch(SOURCE, GAZETTEER, suffix=".zip")
    for tenant, _, _ in TENANTS.values():
        for year in range(FIRST, LAST + 1):
            raw.fetch(SOURCE, url(tenant, year), suffix=".csv")


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
    with zipfile.ZipFile(raw.cached(SOURCE, GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS").is_in(list(TENANTS)))
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
    frames = []
    for state, (tenant, _, _) in TENANTS.items():
        for year in range(FIRST, LAST + 1):
            frame = pl.read_csv(raw.cached(SOURCE, url(tenant, year)).path, infer_schema=False)
            if "office_name" not in frame.columns or frame.height == 0:
                continue
            office = pl.col("office_name").fill_null("").str.to_lowercase()
            division = pl.col("division_name").fill_null("").str.replace(r"\s*\(.*\)$", "")
            frames.append(
                frame.with_columns(
                    chamber=pl.when(office.str.contains(SENATE.pattern) & ~office.str.contains("state"))
                    .then(pl.lit("senate"))
                    .when(office.str.contains(HOUSE.pattern) & ~office.str.contains(r"\bstate\b"))
                    .then(pl.lit("house"))
                )
                .filter(
                    pl.col("chamber").is_not_null()
                    & pl.col("division_type").is_in(list(DIVISIONS))
                    & pl.col("election_type").str.contains("(?i)general")
                )
                .select(
                    year=pl.col("election_date").str.slice(0, 4).cast(pl.Int32),
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
                    district=pl.when(pl.col("chamber") == "house").then(
                        pl.col("district_name").str.extract(r"(\d+)").cast(pl.Int16, strict=False)
                    ),
                    code=pl.when(pl.col("division_type").replace_strict(DIVISIONS) == "county")
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
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
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
            summary="House and Senate general elections by county from state election archives, 1982–2015",
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
                kind="official_body",
                publisher=publisher,
                title=TITLE,
                url=site,
                license="Public record",
            )
            for year in frame.filter(pl.col("state") == state)["year"].unique().sort():
                cs.artifact(raw.cached(SOURCE, url(tenant, year)), sources[state])
        medsl.attach(cs, conn, frame, sources)
        return cs.write()
