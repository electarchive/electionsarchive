import difflib
import json
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.leh"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/2026if_/https://commonslibrary.parliament.uk/content/uploads"
WORKBOOKS = {
    2021: f"{WAYBACK}/2023/02/local_elections_2021_results-2.xlsx",
    2022: f"{WAYBACK}/2023/02/local-elections-2022.xlsx",
    2023: "manual:MF-065/LEH-Candidates-2023.xlsx",
    2024: "manual:MF-065/LEH-2024-results-HoC-version.xlsx",
    2025: "manual:MF-065/LEH-2025-results-HoC.xlsx",
}
PAGES = {
    2021: "https://commonslibrary.parliament.uk/data-tools-and-resources/parliament-elections-data/2021-local-elections-handbook-and-dataset/",
    2022: "https://commonslibrary.parliament.uk/data/parliament-elections-data/2022-local-elections-handbook-and-dataset/",
    2023: "https://commonslibrary.parliament.uk/data-tools-and-resources/parliament-elections-data/",
    2024: "https://commonslibrary.parliament.uk/data-tools-and-resources/parliament-elections-data/",
    2025: "https://commonslibrary.parliament.uk/data-tools-and-resources/parliament-elections-data/",
}
ONS = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
LOOKUPS = {
    2021: ("20", f"{ONS}/WD20_LAD20_CTY20_OTH_UK_LU_v2_27715a77546b4b5a9746baf703dd9a05/FeatureServer/0/query"),
    2022: ("22", f"{ONS}/WD22_LAD22_CTY22_OTH_UK_LU/FeatureServer/0/query"),
    2023: ("23", f"{ONS}/WD23_LAD23_CTY23_OTH_UK_LU/FeatureServer/0/query"),
    2024: ("23", f"{ONS}/WD23_LAD23_UK_LU_DEC/FeatureServer/0/query"),
    2025: ("25", f"{ONS}/WD25_LAD25_CTYUA25_RGN25_CTRY25_UK_LU/FeatureServer/0/query"),
}
PAGE = 1000
RENAMES = {
    "County name": "county",
    "Local authority code": "la_code",
    "Local authority name": "la_name",
    "Ward/ED code": "ward_code",
    "Ward code": "ward_code",
    "Ward/ED name": "ward_name",
    "Ward name": "ward_name",
    "Candidate name": "name",
    "Candidate gender": "gender",
    "Incumbent": "incumbent",
    "Inumbent": "incumbent",
    "Votes": "votes",
    "Elected": "elected",
    "Party name": "party",
    "Vacancies": "seats",
    "Type": "type",
    "Electorate": "electorate",
    "Turnout (%)": "turnout",
    "Candidate number": "number",
}


def sheet(year: int, name: str) -> pl.DataFrame:
    path = raw.cached(SOURCE, WORKBOOKS[year]).path
    return pl.read_excel(path, sheet_name=name, has_header=True, read_options={"header_row": 1}, infer_schema_length=0)


def lookup_urls(year: int) -> list[str]:
    suffix, base = LOOKUPS[year]
    return [
        f"{base}?where=1%3D1&outFields=*&returnGeometry=false&orderByFields=WD{suffix}CD&resultOffset={offset}"
        f"&resultRecordCount={PAGE}&f=json"
        for offset in range(0, 10 * PAGE, PAGE)
    ]


def lookup(year: int) -> pl.DataFrame:
    suffix, _ = LOOKUPS[year]
    rows = []
    for url in lookup_urls(year):
        try:
            artifact = raw.cached(SOURCE, url)
        except LookupError:
            break
        features = json.loads(Path(artifact.path).read_text())["features"]
        rows.extend(f["attributes"] for f in features)
    frame = pl.DataFrame(rows, infer_schema_length=None)
    cty = f"CTY{'UA' if suffix == '25' else ''}{suffix}"
    return frame.select(
        ward=pl.col(f"WD{suffix}CD"),
        ward_name=pl.col(f"WD{suffix}NM"),
        la=pl.col(f"LAD{suffix}CD"),
        la_name=pl.col(f"LAD{suffix}NM"),
        county=pl.col(f"{cty}CD") if f"{cty}CD" in frame.columns else pl.lit(None, pl.String),
        county_name=pl.col(f"{cty}NM") if f"{cty}NM" in frame.columns else pl.lit(None, pl.String),
    )


def mend(text: str) -> str:
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeEncodeError, UnicodeDecodeError:
        return text


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", mend(text)).encode("ascii", "ignore").decode().lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"['’‘`æ]", "", text)).strip()


def resolve(wards: pl.DataFrame, candidates: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    wards = wards.filter(pl.col("ward_code") != "NA").with_columns(
        pl.col("ward_name").map_elements(mend, return_dtype=pl.String)
    )
    candidates = candidates.filter(pl.col("ward_code") != "NA").with_columns(
        pl.col("ward_name").map_elements(mend, return_dtype=pl.String)
    )
    by_name: dict[tuple[str, str], set[str]] = {}
    for year in LOOKUPS:
        for row in lookup(year).iter_rows(named=True):
            by_name.setdefault((row["la"], fold(row["ward_name"])), set()).add(row["ward"])
    keys = ["year", "la_code", "ward_code", "ward_name"]
    seen = pl.concat([wards.select(keys), candidates.select(keys)]).unique()
    clashes = (
        seen.with_columns(folded=pl.col("ward_name").map_elements(fold, return_dtype=pl.String))
        .group_by("year", "la_code", "ward_code")
        .agg(pl.col("folded").n_unique().alias("names"))
        .filter(pl.col("names") > 1)
    )
    fixes: list[tuple[int, str, str, str, str]] = []
    for year, la, code in clashes.select("year", "la_code", "ward_code").iter_rows():
        names = seen.filter((pl.col("year") == year) & (pl.col("la_code") == la) & (pl.col("ward_code") == code))[
            "ward_name"
        ]
        for name in names:
            found = by_name.get((la, fold(name)), set())
            fixes.append((year, la, code, name, found.pop() if len(found) == 1 else code))
    print("clashing codes:", fixes)
    fixed = pl.DataFrame(
        fixes, schema=["year", "la_code", "ward_code", "ward_name", "fixed"], orient="row"
    ).with_columns(pl.col("year").cast(pl.Int32))
    wards = (
        wards.join(fixed, on=keys, how="left").with_columns(ward_code=pl.coalesce("fixed", "ward_code")).drop("fixed")
    )
    candidates = (
        candidates.join(fixed, on=keys, how="left")
        .with_columns(ward_code=pl.coalesce("fixed", "ward_code"))
        .drop("fixed")
    )
    return wards, candidates


def fetch() -> None:
    for url in WORKBOOKS.values():
        if url.startswith("manual:"):
            file = DATA / "inbox" / "manual" / url.removeprefix("manual:")
            raw.store(
                SOURCE,
                url,
                file.read_bytes(),
                suffix=".xlsx",
                retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            raw.fetch(SOURCE, url, suffix=".xlsx")
    for year in LOOKUPS:
        for url in lookup_urls(year):
            artifact = raw.fetch(SOURCE, url, suffix=".json")
            if not json.loads(Path(artifact.path).read_text())["features"]:
                break


def tidy(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.rename({column: column.strip() for column in frame.columns})


def table(year: int, name: str, header: int) -> pl.DataFrame:
    path = raw.cached(SOURCE, WORKBOOKS[year]).path
    return tidy(
        pl.read_excel(
            path, sheet_name=name, has_header=True, read_options={"header_row": header}, infer_schema_length=0
        )
    )


def parties_of(frame: pl.DataFrame, skip: set[str]) -> list[str]:
    return [column for column in frame.columns if column.isupper() and column not in skip and column != "NA"]


def best_of(frame: pl.DataFrame, year: int, key: str, parties: list[str]) -> pl.DataFrame:
    return (
        frame.select(pl.lit(year).alias("year"), pl.col(key).alias("ward_code"), *parties)
        .unpivot(parties, index=["year", "ward_code"], variable_name="party", value_name="votes")
        .with_columns(pl.col("votes").cast(pl.Int64, strict=False))
        .filter(pl.col("votes") > 0)
    )


def load_2024() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    found = table(2024, "Candidates results", 1)
    ward = table(2024, "Wards results", 1)
    candidates = found.select(
        year=pl.lit(2024),
        county=pl.lit(None, pl.String),
        la_code=pl.col("ONS local authority code"),
        ward_code=pl.col("Ward code"),
        ward_name=pl.col("Ward name"),
        number=pl.col("Candidate number").cast(pl.Int64),
        name=pl.col("Name").str.replace_all(",", "", literal=True),
        given=pl.lit(None, pl.String),
        gender=pl.col("Gender"),
        incumbent=pl.col("Incumbent").cast(pl.Int64) == 1,
        votes=pl.col("Votes").cast(pl.Int64),
        elected=pl.col("Elected").cast(pl.Int64) == 1,
        party=pl.col("Party name"),
    )
    wards = ward.select(
        year=pl.lit(2024),
        la_code=pl.col("Local authority code"),
        la_name=pl.col("Local authority name"),
        county=pl.lit(None, pl.String),
        ward_code=pl.col("Ward code"),
        ward_name=pl.col("Ward name"),
        seats=pl.col("Vacancies").cast(pl.Int64, strict=False),
        type=pl.col("Local authority type"),
        electorate=pl.col("Electorate").cast(pl.Int64, strict=False),
        turnout=pl.col("Turnout (%)").cast(pl.Float64, strict=False) * 100,
    )
    return candidates, wards, best_of(ward, 2024, "Ward code", parties_of(ward, set()))


def load_2025() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    found = table(2025, "Candidates result", 1)
    ward = table(2025, "Ward results", 1)
    found = found.rename({column: "Ward name" for column in found.columns if column.startswith("Ward/ County")})
    ward = ward.rename({column: "Ward name" for column in ward.columns if column.startswith("Ward/ County")})
    labels = {
        fold(label): abbreviation
        for label, abbreviation, *_ in pl.read_excel(
            raw.cached(SOURCE, WORKBOOKS[2025]).path, sheet_name="Party names", has_header=False, infer_schema_length=0
        ).iter_rows()
        if abbreviation and label and label != "Party name"
    }
    places = lookup(2025).unique("ward")
    lad = dict(zip(places["ward"], places["la"], strict=True))
    by_name = {fold(name): code for name, code in zip(places["la_name"], places["la"], strict=True)}
    for frame in (found, ward):
        frame.insert_column(
            0,
            pl.Series(
                "la",
                [
                    lad.get(code) or by_name.get(fold(lower)) or by_name.get(fold(upper)) or upper
                    for code, lower, upper in frame.select(
                        "ONS ward code", "Lower tier authority", "Upper tier authority"
                    ).iter_rows()
                ],
            ),
        )
    candidates = found.select(
        year=pl.lit(2025),
        county=pl.when(pl.col("ONS ward code").str.starts_with("E58")).then(pl.col("Upper tier authority")),
        la_code=pl.col("la"),
        ward_code=pl.col("ONS ward code"),
        ward_name=pl.col("Ward name"),
        number=pl.int_range(1, pl.len() + 1, dtype=pl.Int64).over("ONS ward code"),
        name=pl.col("Last names") + " " + pl.col("First names"),
        given=pl.col("First names"),
        gender=pl.col("Gender").replace_strict({"male": "M", "female": "F"}, default=None),
        incumbent=pl.col("Incumbent").cast(pl.Int64, strict=False) == 1,
        votes=pl.col("Votes cast").cast(pl.Int64),
        elected=pl.col("Elected") == "true",
        party=pl.col("Party name").map_elements(lambda label: labels.get(fold(label), label), return_dtype=pl.String),
    )
    wards = ward.select(
        year=pl.lit(2025),
        la_code=pl.col("la"),
        la_name=pl.col("Lower tier authority"),
        county=pl.when(pl.col("ONS ward code").str.starts_with("E58")).then(pl.col("Upper tier authority")),
        ward_code=pl.col("ONS ward code"),
        ward_name=pl.col("Ward name"),
        seats=pl.col("Seats").cast(pl.Int64, strict=False),
        type=pl.col("Local authority type"),
        electorate=pl.col("Electorate").cast(pl.Int64, strict=False),
        turnout=pl.col("Turnout (EC method)").cast(pl.Float64, strict=False) * 100,
    )
    return candidates, wards, best_of(ward, 2025, "ONS ward code", parties_of(ward, set()))


def load_2023() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    path = raw.cached(SOURCE, WORKBOOKS[2023]).path
    found = pl.read_excel(path, sheet_name="Cand_Table", has_header=True, infer_schema_length=0)
    ward = pl.read_excel(path, sheet_name="Ward_Level", has_header=True, infer_schema_length=0)

    def district(label: str) -> str:
        return fold(re.sub(r",\s*(?:City|County) of$", "", mend(label)))

    wanted = {}
    for year in (2023, 2024, 2022, 2025, 2021):
        for row in lookup(year).iter_rows(named=True):
            wanted.setdefault((district(row["la_name"]), fold(row["ward_name"])), (row["ward"], row["la"]))
    by_district: dict[str, list[str]] = {}
    for dist, label in wanted:
        by_district.setdefault(dist, []).append(label)

    def find(dist: str, label: str) -> tuple[str | None, str | None]:
        hit = wanted.get((dist, label))
        if hit is None:
            close = difflib.get_close_matches(label, by_district.get(dist, []), n=1, cutoff=0.8)
            hit = wanted[dist, close[0]] if close else (None, None)
            if close:
                print("2023 ward matched by closest name:", dist, label, "->", close[0])
        return hit

    def coded(frame: pl.DataFrame) -> pl.DataFrame:
        return frame.with_columns(
            hit=pl.struct("DISTRICTNAME", "WARDNAME").map_elements(
                lambda row: find(district(row["DISTRICTNAME"]), fold(mend(row["WARDNAME"]))),
                return_dtype=pl.List(pl.String),
            )
        ).with_columns(ward_code=pl.col("hit").list.get(0), la_code=pl.col("hit").list.get(1))

    found = coded(found)
    ward = coded(ward)
    missing = found.filter(pl.col("ward_code").is_null()).select("DISTRICTNAME", "WARDNAME").unique()
    print("2023 wards without a code:", missing.height, missing.head(30).rows())
    found = found.filter(pl.col("ward_code").is_not_null())
    ward = ward.filter(pl.col("ward_code").is_not_null())
    candidates = found.select(
        year=pl.lit(2023),
        county=pl.lit(None, pl.String),
        la_code=pl.col("la_code"),
        ward_code=pl.col("ward_code"),
        ward_name=pl.col("WARDNAME"),
        number=pl.col("NUMCAND").cast(pl.Int64),
        name=pl.col("NAME").str.replace_all(",", "", literal=True),
        given=pl.lit(None, pl.String),
        gender=pl.col("GENDER"),
        incumbent=pl.col("INCUMB").cast(pl.Int64) == 1,
        votes=pl.col("VOTE").cast(pl.Int64),
        elected=pl.col("WINNER").cast(pl.Int64) == 1,
        party=pl.col("PARTYNAME"),
    )
    wards = ward.select(
        year=pl.lit(2023),
        la_code=pl.col("la_code"),
        la_name=pl.col("DISTRICTNAME"),
        county=pl.lit(None, pl.String),
        ward_code=pl.col("ward_code"),
        ward_name=pl.col("WARDNAME"),
        seats=pl.col("VACS").cast(pl.Int64, strict=False),
        type=pl.col("TYPE"),
        electorate=pl.col("ELECT").cast(pl.Int64, strict=False),
        turnout=pl.col("TURNOUT").cast(pl.Float64, strict=False),
    )
    return candidates, wards, best_of(ward, 2023, "ward_code", parties_of(ward, {"VACS", "TYPE", "ELECT", "TURNOUT"}))


def parse() -> None:
    candidates, wards, best = [], [], []
    for year in (2021, 2022):
        found = sheet(year, "Candidates-results")
        found = found.rename({old: new for old, new in RENAMES.items() if old in found.columns})
        found = found.select(
            year=pl.lit(year),
            county=pl.col("county"),
            la_code=pl.col("la_code"),
            ward_code=pl.col("ward_code"),
            ward_name=pl.col("ward_name"),
            number=pl.col("number").cast(pl.Int64),
            name=pl.col("name"),
            given=pl.lit(None, pl.String),
            gender=pl.col("gender"),
            incumbent=pl.col("incumbent").cast(pl.Int64) == 1,
            votes=pl.col("votes").cast(pl.Int64),
            elected=pl.col("elected").cast(pl.Int64) == 1,
            party=pl.col("party"),
        )
        candidates.append(found)
        ward = sheet(year, "Wards-results")
        ward = ward.rename({old: new for old, new in RENAMES.items() if old in ward.columns})
        parties = [column for column in ward.columns if column.isupper() and column != "NA"]
        full = ward.with_columns(year=pl.lit(year))
        ward = ward.select(
            year=pl.lit(year),
            la_code=pl.col("la_code"),
            la_name=pl.col("la_name"),
            county=pl.col("county"),
            ward_code=pl.col("ward_code"),
            ward_name=pl.col("ward_name"),
            seats=pl.col("seats").cast(pl.Int64, strict=False),
            type=pl.col("type"),
            electorate=pl.col("electorate").cast(pl.Int64, strict=False),
            turnout=pl.col("turnout").cast(pl.Float64, strict=False),
        )
        wards.append(ward)
        best.append(
            full.select("year", "ward_code", *parties)
            .unpivot(parties, index=["year", "ward_code"], variable_name="party", value_name="votes")
            .with_columns(pl.col("votes").cast(pl.Int64, strict=False))
            .filter(pl.col("votes") > 0)
        )
    for load in (load_2023, load_2024, load_2025):
        found, ward, tally = load()
        candidates.append(found)
        wards.append(ward)
        best.append(tally)
    PARSED.mkdir(parents=True, exist_ok=True)
    wards, candidates = resolve(pl.concat(wards), pl.concat(candidates))
    wards = [wards]
    candidates = [candidates]
    pl.concat(candidates).write_parquet(PARSED / "candidates.parquet")
    pl.concat(wards).write_parquet(PARSED / "wards.parquet")
    pl.concat(best).write_parquet(PARSED / "best.parquet")
    print(pl.concat(candidates).group_by("year").len(), pl.concat(wards).group_by("year").len())


OPL = "https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/"
DAYS = {
    2021: date(2021, 5, 6),
    2022: date(2022, 5, 5),
    2023: date(2023, 5, 4),
    2024: date(2024, 5, 2),
    2025: date(2025, 5, 1),
}
POSTPONED = {(2025, "E05016214"): date(2025, 6, 12)}
POSTPONED_RESULTS = "https://northnorthants.moderngov.co.uk/mgElectionAreaResults.aspx?ID=144"
POSTPONED_NOTE = (
    "The poll in Higham Ferrers was postponed from 1 May to 12 June 2025 after the death of a candidate, and the "
    "notice of election was published again. North Northamptonshire Council declared the result on 13 June."
)
DECLARED: dict[tuple[int, str], tuple[str, str, str, str | None, dict[str, int]]] = {
    (2023, "E05005531"): (
        "Oadby and Wigston Borough Council",
        "Oadby Brocks Hill Ward Results - Borough Council Elections on 4 May 2023",
        "https://www.oadby-wigston.gov.uk/news/2023/05/15/oadby_brocks_hill_ward_results_borough_council_elections_on_4_may",
        (
            "Oadby and Wigston Borough Council republished these figures on 15 May 2023. The Returning Officer found "
            "that block votes had been entered as batches rather than bundles. The result declared on 5 May gave "
            "Kaufman 342, Darr 286, Chander 231, and Alam 171. The same two candidates were elected."
        ),
        {},
    ),
    (2023, "E05007469"): (
        "North Warwickshire Borough Council",
        "Borough Election Results - 4 May 2023",
        "https://www.northwarks.gov.uk/elections-voting/election-results-1/8",
        None,
        {"Jenns A.": 589},
    ),
    (2024, "E05014819"): (
        "Bolton Council",
        "Local election results 2024",
        "https://www.bolton.gov.uk/news/article/1727/local-election-results-2024",
        None,
        {"Schofield J.": 1436},
    ),
}
VOTES, REGISTERED, BALLOTS = 1, 2, 3
SMALL_WORDS = {"And", "Of", "The", "Upon", "On", "In", "With", "By", "Cum", "Next", "At", "To", "Under"}
COUNCIL_NAMES = {"E06000010": "Hull City Council"}
CYCLES = {
    "SDA": "Shire district: the whole council was up for election.",
    "SDT": "Shire district: elected by thirds.",
    "UAA": "Unitary authority: the whole council was up for election.",
    "UAT": "Unitary authority: elected by thirds.",
    "MB": "Metropolitan borough: elected by thirds.",
    "MBA": "Metropolitan borough: the whole council was up for election.",
    "LB": "London borough: the whole council was up for election.",
    "SC": "Shire county: the whole council was up for election.",
}
GENDERS = {"M": "male", "F": "female"}
NOT_PARTIES = {"IND", "ND"}
ALIASES = {
    "UKIP": "UK Independence Party",
    "TUSC": "Trade Unionist and Socialist Coalition",
}
CODES = {
    (2024, "THE OFFICIAL MONSTER RAVING LO"): "OMRLP",
    (2024, "NORK RES"): "Nork RA",
    (2024, "NEWCASTLE INDEPENDENTS"): "Newc Ind",
    (2024, "COMMUNIST FUTURE"): "Comm Fut",
    (2024, "FANT and OAKWOOD INDEPENDENTS"): "F & O Ind",
}
UNLISTED = {(2024, "AGS"): "Alliance For Green Socialism", (2024, "JUSTICE"): "Justice Party"}
RESIDENTS_2023 = {
    "E07000209": "Residents for Guildford and Villages",
    "E07000077": "Residents for Uttlesford",
    "E06000049": "Residents of Wilmslow",
    "E07000075": "Rochford District Residents",
    "E07000107": "Swanscombe and Greenhithe Residents' Association",
    "E07000116": "Royal Tunbridge Wells Residents Party",
    "E07000208": "Residents Associations of Epsom and Ewell",
    "E07000211": "Tattenham & Preston Residents",
    "E07000212": "Runnymede Independent Residents' Group",
    ("E07000207", "Molesey East"): "The Molesey Residents Association",
    ("E07000207", "Molesey West"): "The Molesey Residents Association",
    ("E07000207", "Thames Ditton"): "Thames Ditton / Weston Green Residents' Association",
    ("E07000208", "Auriol"): "Stoneleigh and Auriol Residents' Association",
    ("E07000208", "Stoneleigh"): "Stoneleigh and Auriol Residents' Association",
    ("E07000208", "Cuddington"): "Residents' Association of Cuddington",
}
RESOLVED = {
    "Conservative": ("gb.parliament.party", "7"),
    "Conservative and Unionist Party": ("gb.parliament.party", "7"),
    "Labour & Co-Operative Party": ("wikidata", "Q9630"),
    "Reform UK": ("gb.party", "reform-uk"),
    "Liberal Party": ("gb.party", "liberal-party-1989"),
    "Green": ("gb.party", "green-party"),
    "Green Party": ("gb.party", "green-party"),
    "Heritage": ("gb.party", "heritage-party"),
    "Voice": ("gb.party", "uk-voice"),
    "Home": ("gb.party", "homeland-party"),
}
METHODOLOGY = (
    "Compiled by the Elections Centre (Rallings, Thrasher, and Bunting) from council websites and correspondence with "
    "councils and released by the House of Commons Library. The workbook gives the year, not the poll day; the poll day "
    "is the one on which the year's local elections were held. Candidate names are printed as surname and initials. "
    "Wards without a vote count were won without a poll. The workbook prints turnout as a percentage to one decimal "
    "place; ballots cast are that percentage of the electorate, rounded, and flagged as derived."
)


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    wards = pl.read_parquet(PARSED / "wards.parquet")
    names: dict[str, str] = {}
    people: set[UUID] = set()
    for year in LOOKUPS:
        for row in lookup(year).sort("ward").iter_rows(named=True):
            names[row["ward"]] = row["ward_name"]
    parties: dict[tuple[int, str], str] = {}
    for year, workbook in WORKBOOKS.items():
        sheet_ = pl.read_excel(
            raw.cached(SOURCE, workbook).path,
            sheet_name="Party_Names" if year == 2023 else "Party names",
            has_header=False,
            infer_schema_length=0,
        )
        for first, second, *_ in sheet_.iter_rows():
            abbreviation, label = (second, first) if year >= 2023 else (first, second)
            if abbreviation and label and abbreviation != "Party abbreviation" and label != "Party name":
                parties[year, re.sub(r"\s+", "", re.sub(r"(?<![a-z])and(?![a-z])|&", "&", abbreviation)).upper()] = (
                    ALIASES.get(abbreviation, mend(label))
                )
    people = {fold(line) for line in candidates["name"].unique().to_list()}
    clashes = sorted(label for label in set(parties.values()) if fold(label) in people)
    if clashes:
        raise ValueError(f"party labels that are candidate names: {clashes}")
    counties: dict[str, tuple[str, str]] = {}
    lads: dict[str, str] = {}
    for year in LOOKUPS:
        for row in lookup(year).iter_rows(named=True):
            lads[row["la"]] = row["la_name"]
            if row["county"] is not None:
                counties[fold(row["county_name"])] = (row["county"], row["county_name"])
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="English and Welsh council elections 2021 and 2022 by ward, from the Elections Centre's handbook datasets",
            polity="GB",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        gb = cs.polity("GB")
        for code, label, rank, noun, kind in (
            ("gb.lad", "council area", 2, "council area", "administrative"),
            ("gb.ward", "ward", 3, "ward", "electoral_district"),
        ):
            if conn.execute("select 1 from scheme where code = %s", [code]).fetchone() is None:
                cs.add("scheme", code=code, polity_id=gb, kind=kind, rank=rank, noun=noun)
        countries = {"E": cs.area("gb.country", "ENG"), "W": cs.area("gb.country", "WLS")}
        if cs.minted & set(countries.values()):
            raise ValueError("publish gb.umit first: the countries must exist")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("publish gb.parliament first: the electoral systems must exist")

        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB'"
            " and n.kind in ('official', 'english', 'native') and not exists (select from entity_merge m where m.entity_id = p.id) order by p.id desc"
        ).fetchall():
            known[fold(text)] = id
        party_ids: dict[tuple[int, str, str | None], UUID | None] = {}
        own = dict(
            conn.execute(
                """select x.value, p.id from external_id x join party p on p.id = x.entity_id
                   join changeset c on c.id = p.changeset_id
                   where x.scheme = 'gb.party' and c.pipeline = 'gb.leh'
                     and not exists (select from entity_merge m where m.entity_id = p.id)"""
            ).fetchall()
        )
        merged: set[UUID] = set()
        conservative = cs.key("gb.parliament.party", "7")
        name(conservative, "Conservative", kind="short")

        def party_of(year: int, abbreviation: str, shared: str | None) -> UUID | None:
            if abbreviation.upper() in NOT_PARTIES:
                return None
            if (year, abbreviation, shared) not in party_ids:
                code = CODES.get((year, abbreviation), abbreviation)
                label = (
                    shared
                    or UNLISTED.get((year, abbreviation))
                    or parties.get((year, re.sub(r"\s+", "", re.sub(r"(?<![a-z])and(?![a-z])|&", "&", code)).upper()))
                )
                if label is None:
                    raise ValueError(f"{year}: party code {abbreviation!r} is not in the workbook's party names")
                found = cs.key(*RESOLVED[label]) if label in RESOLVED else known.get(fold(label))
                if found is None:
                    found = cs.key("gb.party", slug(label))
                    if found in cs.minted and found not in party_ids.values():
                        cs.add("party", id=found, kind="party", polity_id=gb)
                        name(found, label)
                    known[fold(label)] = found
                if found in cs.minted and label in RESOLVED:
                    raise ValueError(f"{year}: {label} resolves to no existing party")
                for raw_key in (slug(label),) if shared else (slug(abbreviation), slug(label)):
                    stale = own.get(raw_key)
                    if stale is not None and stale != found and stale not in merged and stale not in party_ids.values():
                        merged.add(stale)
                        cs.merge(stale, found)
                if found in merged:
                    raise ValueError(f"{year} {abbreviation}: {label} resolves to a merged party")
                party_ids[year, abbreviation, shared] = found
            return party_ids[year, abbreviation, shared]

        sources = {}
        for year, url in WORKBOOKS.items():
            sources[year] = cs.source("House of Commons Library", f"Local election results {year}")
            cs.add(
                "source",
                id=sources[year],
                kind="compendium",
                publisher="House of Commons Library",
                title=f"Local election results {year}",
                url=PAGES[year],
                license="Open Parliament Licence v3.0",
                license_url=OPL,
                attribution="Contains Parliamentary information licensed under the Open Parliament Licence v3.0. Data collected by the Elections Centre.",
                notes=METHODOLOGY,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[year])
        council_results = cs.source("North Northamptonshire Council", "Election results for Higham Ferrers, 1 May 2025")
        cs.add(
            "source",
            id=council_results,
            kind="official_body",
            publisher="North Northamptonshire Council",
            title="Election results for Higham Ferrers, 1 May 2025",
            url=POSTPONED_RESULTS,
        )
        declared: dict[tuple[int, str], UUID] = {}
        for place, (publisher, title, url, _, _) in DECLARED.items():
            declared[place] = cs.source(publisher, title)
            cs.add("source", id=declared[place], kind="official_body", publisher=publisher, title=title, url=url)

        slugs: set[str] = set()
        council_paths: dict[str, str] = {}
        council_names: dict[str, str] = {}
        seen_elections: set[UUID] = set()
        areas: dict[str, UUID] = {}
        polities: dict[str, UUID] = {}
        bodies: dict[str, UUID] = {}
        seen_wards: set[str] = set()
        named: set[str] = set()

        def council_of(la_code: str, ward_code: str, county: str) -> tuple[str, str, bool]:
            if ward_code.startswith("E58"):
                code, label = counties[fold(county)]
                return code, label, True
            return la_code, lads.get(la_code, la_code), False

        def council(code: str, label: str, is_county: bool) -> None:
            if code in polities:
                return
            place = label.removesuffix(", City of").removesuffix(", County of")
            council_name = COUNCIL_NAMES.get(
                code,
                f"{label} County Council"
                if is_county
                else f"{place} City Council"
                if label.endswith(", City of")
                else f"{place} Council",
            )
            areas[code] = cs.area("gb.lad", code)
            if areas[code] in cs.minted:
                cs.add("area", id=areas[code], scheme_code="gb.lad")
                cs.add("area_code", area_id=areas[code], code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=areas[code], parent_id=countries[code[0]], valid_from=None, valid_to=None)
            name(areas[code], label)
            polities[code] = cs.key("gb.polity", code)
            path = f"gb/{slug(place)}"
            if path in slugs:
                path = f"{path}-{code.lower()}"
            slugs.add(path)
            council_paths[code] = path
            council_names[code] = council_name
            if polities[code] in cs.minted:
                cs.add("polity", id=polities[code], kind="county" if is_county else "municipality", iso_code=None)
                cs.add(
                    "polity_parent",
                    polity_id=polities[code],
                    parent_id=cs.polity("GB-WLS" if code.startswith("W") else "GB-ENG"),
                )
                cs.add("polity_territory", polity_id=polities[code], area_id=areas[code])
            name(polities[code], council_name)
            cs.add("slug", path=path, entity_id=polities[code])
            bodies[code] = cs.key("gb.body", code)
            if bodies[code] in cs.minted:
                cs.add("body", id=bodies[code], polity_id=polities[code], kind="council")
            name(bodies[code], council_name)

        national: dict[int, UUID] = {}
        for year, day in DAYS.items():
            national[year] = cs.key("gb.election", f"{day}:local:gb")
            cs.add(
                "election",
                id=national[year],
                polity_id=gb,
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(national[year], f"{year} local elections in England and Wales")
            cs.add("slug", path=f"gb/{day}-local", entity_id=national[year])

        metro = (
            wards.filter(pl.col("type") == "MB")
            .group_by("year", "la_code")
            .agg(seats=pl.col("seats").sum(), n=pl.len())
            .filter(pl.col("seats") / pl.col("n") > 1.3)
        )
        all_out = {(row["year"], row["la_code"]) for row in metro.iter_rows(named=True)}
        for year, day in DAYS.items():
            held = wards.filter(pl.col("year") == year)
            ballots = candidates.filter(pl.col("year") == year)
            ward_rows = {(row["la_code"], row["ward_code"]): row for row in held.iter_rows(named=True)}
            groups = ballots.group_by("la_code", "ward_code", "ward_name", maintain_order=True)
            for (la_code, ward_code, ward_name), lines in groups:
                lines = lines.sort("number")
                corrected = DECLARED.get((year, ward_code), (None, None, None, None, {}))[4]
                if missing := corrected.keys() - set(lines["name"]):
                    raise ValueError(f"{year} {ward_code}: no candidates {sorted(missing)} to correct")
                originals = dict(zip(lines["name"], lines["votes"], strict=True))
                lines = lines.with_columns(
                    votes=pl.col("name").replace_strict(corrected, default=pl.col("votes"), return_dtype=pl.Int64)
                )
                ward_row = ward_rows.get((la_code, ward_code))
                code, label, is_county = council_of(la_code, ward_code, lines["county"][0])
                council(code, label, is_county)
                path = council_paths[code]
                election = cs.key("gb.election", f"{day}:local:{code}")
                if election not in seen_elections:
                    seen_elections.add(election)
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polities[code],
                        parent_id=national[year],
                        kind="local",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(election, f"{year} {council_names[code]} election")
                    cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
                    if ward_row and ward_row["type"] in CYCLES:
                        cycle = "MBA" if ward_row["type"] == "MB" and (year, la_code) in all_out else ward_row["type"]
                        cs.add(
                            "external_id",
                            entity_id=election,
                            scheme="gb.leh.type",
                            value=f"{cycle}:{year}:{code}",
                        )
                        cs.add("note", entity_id=election, kind="context", body_md=CYCLES[cycle])
                poll = POSTPONED.get((year, ward_code), day)
                if poll != day:
                    parent = election
                    election = cs.key("gb.election", f"{poll}:local:{ward_code}")
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polities[code],
                        parent_id=parent,
                        kind="local",
                        date_from=poll,
                        date_to=poll,
                        status="held",
                    )
                    name(election, f"{year} {council_names[code]} election, postponed poll in {ward_name}")
                    cs.add("slug", path=f"{path}/{poll}-local", entity_id=election)
                    cs.add("note", entity_id=election, kind="context", body_md=POSTPONED_NOTE)
                    cs.add("citation", entity_id=election, source_id=council_results)
                ward_area = cs.area("gb.ward", ward_code)
                display = names.get(ward_code)
                display = display if display and fold(display) == fold(ward_name) else ward_name
                display = display.title() if display.isupper() else display
                display = " ".join(
                    word.lower() if index and word in SMALL_WORDS else word
                    for index, word in enumerate(display.split())
                )
                if ward_code not in areas:
                    areas[ward_code] = ward_area
                    if ward_area in cs.minted:
                        cs.add("area", id=ward_area, scheme_code="gb.ward")
                        cs.add("area_code", area_id=ward_area, code=ward_code, valid_from=None, valid_to=None)
                        cs.add("area_parent", area_id=ward_area, parent_id=areas[code], valid_from=None, valid_to=None)
                    name(ward_area, display)
                unit = cs.unit("gb.ward", ward_code)
                if unit in cs.minted and ward_code not in seen_wards:
                    seen_wards.add(ward_code)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polities[code],
                        scheme_code="gb.ward",
                        code=ward_code,
                        area_relation="equals",
                        area_id=ward_area,
                        kind="standard",
                        valid_from=None,
                    )
                if ward_code not in named:
                    named.add(ward_code)
                    name(unit, display)
                marked = int(lines["elected"].sum())
                seats = (ward_row["seats"] if ward_row else None) or marked
                if marked > seats:
                    ranked = lines["votes"].sort(descending=True)
                    if ranked[seats - 1] == ranked[seats]:
                        raise ValueError(
                            f"{year} {ward_code}: {marked} marked elected for {seats} seats, tied at the cut"
                        )
                    lines = lines.with_columns(elected=pl.col("votes") >= ranked[seats - 1])
                elif marked != seats:
                    raise ValueError(f"{year} {ward_code}: {marked} elected for {seats} seats")
                tacit = ward_row is None or int(lines["votes"].sum()) == 0
                contest_key = f"{day}:local:{ward_code}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=bodies[code],
                    district_area_id=ward_area,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=systems["fptp" if seats == 1 else "block"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{display} {'division' if is_county else 'ward'}, {council_names[code]}, {year}")
                ward_slug = slug(display)
                cs.add("slug", path=f"{path}/{poll}-local/{ward_slug}-{ward_code.lower()}", entity_id=contest)
                if (year, ward_code) in DECLARED:
                    publisher, _, _, note, _ = DECLARED[(year, ward_code)]
                    if corrected:
                        figures = [f"{text.split()[0]} {originals[text]:,}" for text in corrected]
                        listed = (
                            figures[0]
                            if len(figures) == 1
                            else " and ".join(figures)
                            if len(figures) == 2
                            else f"{', '.join(figures[:-1])}, and {figures[-1]}"
                        )
                        note = (
                            f"The House of Commons Library workbook gives {listed}. The figures shown here are from "
                            f"{publisher}'s declaration."
                        )
                    cs.add("note", entity_id=contest, kind="context", body_md=note)
                    cs.add("citation", entity_id=contest, source_id=declared[(year, ward_code)])
                result_set = cs.key("gb.leh.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=sources[year],
                    is_default=True,
                    completeness=1,
                )
                if marked > seats:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=f"The Elections Centre's handbook marks {marked} candidates elected for {seats} "
                        + (
                            "seat; the candidate with the most votes is shown elected."
                            if seats == 1
                            else f"seats; the {seats} candidates with the most votes are shown elected."
                        ),
                    )
                if ward_row and ward_row["electorate"]:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=REGISTERED,
                        count_round=0,
                        flags=0,
                        value=ward_row["electorate"],
                    )
                if ward_row and ward_row["electorate"] and ward_row["turnout"] is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=BALLOTS,
                        count_round=0,
                        flags=5,
                        value=round(ward_row["electorate"] * ward_row["turnout"] / 100),
                    )
                for line in lines.iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    option = cs.key("gb.leh.option", option_key)
                    if line["given"] is not None:
                        surname = line["name"].removesuffix(f" {line['given']}")
                        surname_first = f"{surname}, {line['given']}"
                        full = f"{line['given']} {surname}"
                    else:
                        surname_first = re.sub(r"(?<=\.)/\.$", "", line["name"])
                        parts = surname_first.split()
                        initials = []
                        while len(parts) > 1 and re.fullmatch(r"(?:[A-Z]\.?)+", parts[-1]):
                            initials.insert(0, parts.pop())
                        full = " ".join([*initials, *parts])
                    person = cs.key("gb.leh.person", option_key)
                    if person not in people:
                        people.add(person)
                        cs.add("person", id=person, gender=GENDERS.get(line["gender"]))
                        name(person, full)
                    owner = party_of(
                        year,
                        line["party"],
                        RESIDENTS_2023.get((la_code, ward_name)) or RESIDENTS_2023[la_code]
                        if (year, line["party"]) == (2023, "RES")
                        else None,
                    )
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=full,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.leh.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=surname_first,
                        member_of_party_id=owner,
                        is_independent=line["party"].upper() == "IND",
                        is_incumbent=line["incumbent"],
                    )
                    if not tacit:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=0,
                            value=line["votes"],
                        )
                    if line["elected"]:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="elected",
                            notes=f"Derived: the handbook marks more winners than seats; the top {seats} by votes are "
                            "shown elected."
                            if marked > seats
                            else None,
                        )
        return cs.write()
