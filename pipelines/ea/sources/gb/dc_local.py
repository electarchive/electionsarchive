import io
import json
import os
import re
import time
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import leh
from ea.store import DATA, connect

SOURCE = "gb.dc_local"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = {
    2016: date(2016, 5, 5),
    2017: date(2017, 5, 4),
    2018: date(2018, 5, 3),
    2019: date(2019, 5, 2),
    2023: date(2023, 5, 4),
    2024: date(2024, 5, 2),
    2025: date(2025, 5, 1),
    2026: date(2026, 5, 7),
}
POLLING_DAYS = DAYS | {2020: date(2020, 5, 7), 2021: date(2021, 5, 6), 2022: date(2022, 5, 5)}
BY_BALLOTS = (
    "https://elections.democracyclub.org.uk/api/elections/?election_id_regex="
    "%5Elocal%5C.{council}%5B%5E.%5D%2A%5C.%5B%5E.%5D%2B%5C.by%5C.{year}-{month}&limit=100"
)
BY_ELSEWHERE = ("local.city-of-london.", "local.isles-of-scilly.")
BY = PARSED / "by"
RESULTS = "https://candidates.democracyclub.org.uk/media/csv-archives/results-{day}.csv"
ARCHIVED = {2018, 2019, 2023}
SAMPLED = {2026}
TACIT = {2018, 2019}
BALLOT = "https://candidates.democracyclub.org.uk/api/next/ballots/{ballot}/"
CROSS_CHECKS: set[int] = set()
API = "https://candidates.democracyclub.org.uk/api/next/results/?election_date={day}"
BALLOTS = (
    "https://elections.democracyclub.org.uk/api/elections/?poll_open_date={day}"
    "&election_id_regex=%5Elocal%5C.{council}%5C.&limit=100"
)
LEEDS = {
    2016: "https://datamillnorth.org/download/20jwj/5a20b38b-e3e6-474d-9c66-5826f77cffd9/Elections.csv",
    2018: "https://datamillnorth.org/download/20jwj/a06a323c-2a4c-4678-a6ed-abadbebec457/Copy%20of%20ResultsLocal2018xlsx.csv",
    2019: "https://datamillnorth.org/download/20jwj/8333f34b-f819-4d1a-8ed2-f63c31486a8c/Copy%20of%20Results%20Local%202019.csv",
}
LEEDS_CODE = "E08000035"
CROYDON = {
    2018: "https://docs.google.com/spreadsheets/d/e/2PACX-1vQrPyR0SkO96jeaAn6_MlJtPGlRfek6ooqzMmbk8hzFvTHPbhhSjx1R2JDVWG58KhfNWTGk5xyu1af0/pub?output=csv"
}
CROYDON_CODE = "E09000008"
CROYDON_PAGE = "https://www.croydon.gov.uk/council-and-elections/voting-and-elections/previous-election-results/local-election-results-3-may-2018"
FILES = {LEEDS_CODE: LEEDS, CROYDON_CODE: CROYDON}
ONS = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
PAGE = 2000
LOOKUPS = {
    2016: ("WD16_LAD16_CTY16_OTH_UK_LU_486872e2469d4a6eb50d6e69d869b560", "16", 9130, PAGE),
    2017: ("WD17_LAD17_CTY17_OTH_UK_LUv2_c8956eca906348fd9e3bb1f6af54f2ce", "17", 9131, PAGE),
    2018: ("WD18_LAD18_CTY18_OTH_UK_LU_971f977f4a444d09842fcfbfd51f8982", "18", 9114, PAGE),
    2019: ("WD19_LAD19_CTY19_OTH_UK_LU_89ea1f028be347e7a44d71743c96b60d", "19", 8887, PAGE),
    2020: ("WD20_LAD20_UK_LU_v2_3784d8d58a134af091cf9601bd36acdf", "20", 8887, PAGE),
    2022: ("WD22_LAD22_UK_LU", "22", 8483, 1000),
    2023: ("WD23_LAD23_CTY23_OTH_UK_LU", "23", 8441, 1000),
    2024: ("WD24_LAD24_UK_LU", "24", 8396, 1000),
    2025: ("WD25_LAD25_UK_LU_v2", "25", 8405, 1000),
    2026: ("WD26_LAD26_UK_LU", "26", 8413, 1000),
}
LAYERS = {
    "wd15": (
        "Wards_December_2015_GCB_in_Great_Britain_2022",
        "wd15cd",
        8734,
        2000,
        "Wards (December 2015) Boundaries GB BGC",
    ),
    "lad15": (
        "LAD_Dec_2015_GCB_GB_2022",
        "lad15cd",
        380,
        2000,
        "Local Authority Districts (December 2015) Boundaries GB BGC",
    ),
    "wd20": ("Wards_Dec_2020_UK_BGC_2022", "WD20CD", 8887, 2000, "Wards (December 2020) Boundaries UK BGC"),
    "lad20": (
        "LAD_DEC_2020_UK_BGC",
        "LAD20CD",
        379,
        2000,
        "Local Authority Districts (December 2020) Boundaries UK BGC",
    ),
    "wd21": ("Wards_December_2021_UK_BGC_2022", "WD21CD", 8694, 2000, "Wards (December 2021) Boundaries UK BGC"),
    "lad21": (
        "Local_Authority_Districts_December_2021_UK_BGC_2022",
        "LAD21CD",
        374,
        2000,
        "Local Authority Districts (December 2021) Boundaries UK BGC",
    ),
    "wd22": ("Wards_December_2022_Boundaries_UK_BGC", "WD22CD", 8483, 1000, "Wards (December 2022) Boundaries UK BGC"),
    "lad22": (
        "Local_Authority_Districts_December_2022_UK_BGC_V2",
        "LAD22CD",
        374,
        2000,
        "Local Authority Districts (December 2022) Boundaries UK BGC",
    ),
    "wd16": (
        "Wards_December_2016_GCB_in_Great_Britain_2022",
        "wd16cd",
        8668,
        1000,
        "Wards (December 2016) Boundaries GB BGC",
    ),
    "wd17": (
        "Wards_December_2017_GCB_in_Great_Britain_2022",
        "wd17cd",
        8669,
        1000,
        "Wards (December 2017) Boundaries GB BGC",
    ),
    "lad16": (
        "LAD_Dec_2016_GB_BGC_2022",
        "LAD16CD",
        380,
        2000,
        "Local Authority Districts (December 2016) Boundaries GB BGC",
    ),
    "lad17": (
        "Local_Authority_Districts_December_2017_Boundaries_GB_BGC_2022",
        "LAD17CD",
        380,
        2000,
        "Local Authority Districts (December 2017) Boundaries GB BGC",
    ),
    "wd18": ("Wards_December_2018_GCB_UK_2022", "wd18cd", 9114, 1000, "Wards (December 2018) Boundaries UK BGC"),
    "wd19": (
        "Wards_December_2019_Boundaries_UK_BGC_2022",
        "WD19CD",
        8887,
        1000,
        "Wards (December 2019) Boundaries UK BGC",
    ),
    "lad18": (
        "LAD_December_2018_Boundaries_UK_BGC_2022",
        "lad18cd",
        391,
        2000,
        "Local Authority Districts (December 2018) Boundaries UK BGC",
    ),
    "lad19": (
        "Local_Authority_Districts_December_2019_GCB_UK_2022",
        "lad19cd",
        382,
        2000,
        "Local Authority Districts (December 2019) Boundaries UK BGC",
    ),
    "wd23": ("Wards_December_2023_Boundaries_UK_BGC", "WD23CD", 8441, 1000, "Wards (December 2023) Boundaries UK BGC"),
    "lad23": (
        "Local_Authority_Districts_December_2023_Boundaries_UK_BGC",
        "LAD23CD",
        361,
        2000,
        "Local Authority Districts (December 2023) Boundaries UK BGC",
    ),
    "wd24": ("Wards_May_2024_Boundaries_UK_BGC", "WD24CD", 8396, 1000, "Wards (May 2024) Boundaries UK BGC"),
    "lad24": (
        "Local_Authority_Districts_May_2024_Boundaries_UK_BGC",
        "LAD24CD",
        361,
        2000,
        "Local Authority Districts (May 2024) Boundaries UK BGC",
    ),
    "wd25": ("WD_MAY_2025_UK_BGC_V2", "WD25CD", 8405, 1000, "Wards (May 2025) Boundaries UK BGC (V2)"),
    "lad25": (
        "LAD_MAY_2025_UK_BGC_V2",
        "LAD25CD",
        361,
        2000,
        "Local Authority Districts (May 2025) Boundaries UK BGC (V2)",
    ),
    "wd26": ("WD_MAY_2026_UK_BGC", "WD26CD", 8413, 1000, "Wards (May 2026) Boundaries UK BGC"),
    "lad26": (
        "Local_Authority_Districts_May_2026_Boundaries_UK_BGC",
        "LAD26CD",
        361,
        2000,
        "Local Authority Districts (May 2026) Boundaries UK BGC",
    ),
    "ced26": (
        "CED_MAY_2026_EN_BGC",
        "CED26CD",
        1396,
        1000,
        "County Electoral Division (May 2026) Boundaries EN BGC",
    ),
}
COUNTIES = {
    "east-sussex": ("E10000011", "East Sussex"),
    "essex": ("E10000012", "Essex"),
    "gloucestershire": ("E10000013", "Gloucestershire"),
    "hampshire": ("E10000014", "Hampshire"),
    "hertfordshire": ("E10000015", "Hertfordshire"),
    "norfolk": ("E10000020", "Norfolk"),
    "suffolk": ("E10000029", "Suffolk"),
    "surrey": ("E10000030", "Surrey"),
    "west-sussex": ("E10000032", "West Sussex"),
}
SHADOWS = {"east-surrey": ("ESY", "East Surrey"), "west-surrey": ("WSY", "West Surrey")}
SURREY = (
    "https://1897589978-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2FcNpJpLP8RROUaWVQo5ea"
    "%2Fuploads%2FFBcfLB0R9hLKW4Du7OnN%2FPreoperative_District_Borough_Unitary_Ward_Region%20Shapefile.zip"
    "?alt=media&token=e7f34d3f-22ae-48a4-b57c-c683d58b27ff"
)
SURREY_AUTHORITIES = {"EAST_SURREY": "ESY", "WEST_SURREY": "WSY"}
SURREY_TITLE = "Boundary-Line: East and West Surrey preoperative unitary ward boundaries"
SURREY_LAYER = "sy26"
DIVISION_SPELLINGS = {"harlow parndon and toddbrook": "harlow parndon and todbrook"}
PRESS = (
    "birminghammail.co.uk",
    "chroniclelive.co.uk",
    "kentonline.co.uk",
    "eveshamjournal.co.uk",
    "brixtonbuzz.com",
    "romfordrecorder.co.uk",
    "thecomet.net",
    "irishnews.com",
    "bbc.co.uk",
    "google.co",
    "google.com",
    "facebook.com",
    "twitter.com",
    "democracyclub.org.uk",
    "rochdaleonline.co.uk",
    "rossendalenews.org.uk",
    "oxfordmail.co.uk",
    "sthelensstar.co.uk",
    "yorkshirepost.co.uk",
    "swindonnews.org.uk",
    "watfordobserver.co.uk",
    "pembrokeshire-herald.com",
    "cambrian-news.co.uk",
    "burtonmail.co.uk",
    "onthewight.com",
    "westerntelegraph.co.uk",
    "iwcp.co.uk",
    "brecon-radnor.co.uk",
    "shropshirelive.com",
    "stratfordobserver.co.uk",
    "barryanddistrictnews.co.uk",
    "windsorexpress.co.uk",
    "wrexham.com",
    "wikipedia.org",
)
ALIASES = {
    "Conservative and Unionist Party": "Conservative Party",
    "UK Independence Party (UKIP)": "UK Independence Party",
    "Labour and Co-operative Party": "Labour Party",
}
DC = "Democracy Club"
LICENSE = "Creative Commons Attribution 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = "Contains data from Democracy Club (https://democracyclub.org.uk), licensed under CC BY 4.0."
METHODOLOGY = (
    "Compiled by Democracy Club volunteers from each council's declaration of results; every ballot records the council "
    "page it was copied from, kept on the result set as a citation. Not an official publication: an official set "
    "supersedes it where one is loaded. Seats and ward GSS codes come from Democracy Club's EveryElection register; "
    "county electoral divisions are matched to ONS's May 2026 division list by code or, where EveryElection gives none, by "
    "name within the county; the Surrey shadow unitary wards have no ONS code, keep EveryElection's identifier, and take their shapes from Ordnance Survey's Boundary-Line; "
    "ballots whose source is a newspaper, broadcaster, social media post, Wikipedia, or the Local Elections Archive "
    "Project, ballots that cite no page, and ballots whose winners do not equal the seats contested, are not loaded. Votes for the rejected ballots are the printed spoilt ballots."
)
EE_TITLE = "EveryElection"
VOTES, INVALID = 1, 5


def day_pages(day: date) -> list[str]:
    first = f"{API.format(day=day)}&page_size=200&page=1"
    count = json.loads(raw.cached(SOURCE, first).path.read_text())["count"]
    return [f"{API.format(day=day)}&page_size=200&page={page}" for page in range(1, max(count - 1, 0) // 200 + 2)]


def result_pages(year: int) -> list[str]:
    return [RESULTS.format(day=DAYS[year])] if year in ARCHIVED else day_pages(DAYS[year])


def result_lines(year: int) -> pl.DataFrame:
    if year in ARCHIVED:
        frame = pl.read_csv(raw.cached(SOURCE, RESULTS.format(day=DAYS[year])).path, infer_schema_length=0)
        return frame.rename({"elected": "is_winner"}) if "is_winner" not in frame.columns else frame
    frame = day_lines(DAYS[year])
    if frame.is_empty():
        raise ValueError(f"{year}: no results")
    return frame


def day_lines(day: date) -> pl.DataFrame:
    rows = []
    for url in day_pages(day):
        for record in json.loads(raw.cached(SOURCE, url).path.read_text())["results"]:
            ballot = record["ballot"]["ballot_paper_id"]
            for line in record["candidate_results"]:
                rows.append(
                    {
                        "ballot_paper_id": ballot,
                        "election_id": ballot,
                        "is_winner": str(line["elected"]),
                        "source": record["source"] or "",
                        "spoilt_ballots": None
                        if record["num_spoilt_ballots"] is None
                        else str(record["num_spoilt_ballots"]),
                        "person_id": str(line["person"]["id"]),
                        "person_name": line["person"]["name"],
                        "party_name": (line["party"] or {}).get("name"),
                        "ballots_cast": str(line["num_ballots"]),
                    }
                )
    columns = (
        "ballot_paper_id",
        "election_id",
        "is_winner",
        "source",
        "spoilt_ballots",
        "person_id",
        "person_name",
        "party_name",
        "ballots_cast",
    )
    frame = pl.DataFrame(rows, schema=dict.fromkeys(columns, pl.String))
    if frame.select("ballot_paper_id", "person_id").is_duplicated().any():
        raise ValueError(f"{day}: a candidate appears twice on a ballot")
    return frame


def polite(url: str, suffix: str) -> raw.Artifact:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    for _ in range(9):
        try:
            artifact = raw.fetch(SOURCE, url, suffix=suffix)
            time.sleep(2)
            return artifact
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 429 and error.response.status_code < 500:
                raise
            time.sleep(30)
    return raw.fetch(SOURCE, url, suffix=suffix)


def by_years() -> list[int]:
    return sorted({int(year) for year in os.environ["DC_BY"].split(",")}, reverse=True)


def by_ballot_pages(year: int, get: Callable[[str], raw.Artifact] = lambda url: raw.cached(SOURCE, url)) -> list[str]:
    pages = []
    for month in range(1, 13):
        url = BY_BALLOTS.format(council="", year=year, month=f"{month:02}")
        if json.loads(get(url).path.read_text())["count"] <= 100:
            pages.append(url)
            continue
        for council in "abcdefghijklmnopqrstuvwxyz0123456789":
            part = BY_BALLOTS.format(council=council, year=year, month=f"{month:02}")
            if json.loads(get(part).path.read_text())["count"] > 100:
                raise ValueError(f"{part}: more than 100 ballots")
            pages.append(part)
    return pages


def by_records(year: int) -> list[dict[str, object]]:
    records = []
    for url in by_ballot_pages(year):
        page = json.loads(raw.cached(SOURCE, url).path.read_text())
        if page["count"] != len(page["results"]):
            raise ValueError(f"{url}: {page['count']} elections, {len(page['results'])} on the page")
        records += [
            {
                "ballot": record["election_id"],
                "day": date.fromisoformat(record["poll_open_date"]),
                "seats": record["seats_contested"],
                "system": (record["voting_system"] or {}).get("slug"),
                "gss": (record["division"] or {}).get("official_identifier"),
                "division": (record["division"] or {}).get("name"),
                "territory": (record["organisation"] or {}).get("territory_code"),
                "cancelled": record["cancelled"],
            }
            for record in page["results"]
            if record["identifier_type"] == "ballot"
            and date.fromisoformat(record["poll_open_date"]) != POLLING_DAYS[year]
            and not record["election_id"].startswith(BY_ELSEWHERE)
        ]
    return records


def by_days(year: int) -> list[date]:
    return sorted({record["day"] for record in by_records(year) if isinstance(record["day"], date)})


def ballot_pages(year: int) -> list[str]:
    day = DAYS[year]
    ids = result_lines(year)["ballot_paper_id"]
    councils = sorted({id.split(".")[1] for id in ids if id.startswith("local.")})
    pages = []
    for council in councils:
        url = BALLOTS.format(day=day, council=council)
        if json.loads(raw.fetch(SOURCE, url, suffix=".json").path.read_text())["count"] <= 100:
            pages.append(url)
        else:
            pages += [url.replace("%5C.&limit", f"%5C.{c}&limit") for c in "abcdefghijklmnopqrstuvwxyz0123456789"]
    return pages


def tacit_urls(year: int) -> list[str]:
    held = set(result_lines(year)["ballot_paper_id"])
    ids = []
    for url in ballot_pages(year):
        for record in json.loads(raw.cached(SOURCE, url).path.read_text())["results"]:
            if record["identifier_type"] == "ballot" and record["cancelled"] and record["election_id"] not in held:
                ids.append(record["election_id"])
    return [BALLOT.format(ballot=id) for id in sorted(ids)]


def lookup_pages(year: int) -> list[str]:
    service, suffix, count, size = LOOKUPS[year]
    return [
        f"{ONS}/{service}/FeatureServer/0/query?where=1%3D1&outFields=*&returnGeometry=false&orderByFields=WD{suffix}CD"
        f"&resultOffset={offset}&resultRecordCount={size}&f=json"
        for offset in range(0, count, size)
    ]


def layer_pages(key: str) -> list[str]:
    service, field, count, size, _ = LAYERS[key]
    fields = f"{field},{field[:-2]}NM" if key.startswith("ced") else field
    return [
        f"{ONS}/{service}/FeatureServer/0/query?where=1%3D1&outFields={fields}&outSR=27700&orderByFields={field}"
        f"&resultOffset={offset}&resultRecordCount={size}&f=geojson"
        for offset in range(0, count, size)
    ]


def fetch() -> None:
    if "DC_BY" in os.environ:
        for year in by_years():
            by_ballot_pages(year, lambda url: polite(url, ".json"))
            for day in by_days(year):
                polite(f"{API.format(day=day)}&page_size=200&page=1", ".json")
                for url in day_pages(day):
                    polite(url, ".json")
        for year in LOOKUPS:
            for url in lookup_pages(year):
                raw.fetch(SOURCE, url, suffix=".json")
        for key in LAYERS:
            for url in layer_pages(key):
                raw.fetch(SOURCE, url, suffix=".geojson")
        return
    for year in LOOKUPS:
        for url in lookup_pages(year):
            raw.fetch(SOURCE, url, suffix=".json")
    raw.fetch(SOURCE, SURREY, suffix=".zip")
    for year, day in DAYS.items():
        if year in ARCHIVED:
            raw.fetch(SOURCE, RESULTS.format(day=day), suffix=".csv")
        else:
            raw.fetch(SOURCE, f"{API.format(day=day)}&page_size=200&page=1", suffix=".json")
            for url in result_pages(year):
                for _ in range(10):
                    try:
                        raw.fetch(SOURCE, url, suffix=".json")
                        break
                    except httpx.HTTPStatusError as error:
                        if error.response.status_code != 429:
                            raise
                        time.sleep(30)
                time.sleep(4)
        for files in FILES.values():
            if year in files:
                raw.fetch(SOURCE, files[year], suffix=".csv")
        for url in ballot_pages(year):
            raw.fetch(SOURCE, url, suffix=".json")
            time.sleep(1)
        for url in tacit_urls(year) if year in TACIT else []:
            for _ in range(12):
                try:
                    raw.fetch(SOURCE, url, suffix=".json")
                    break
                except httpx.HTTPStatusError as error:
                    if error.response.status_code != 429:
                        raise
                    time.sleep(30)
            time.sleep(3)
        for key in (f"wd{year % 100}", f"lad{year % 100}", *(["ced26"] if year == 2026 else [])):
            for url in layer_pages(key):
                raw.fetch(SOURCE, url, suffix=".geojson")


def surrey_features() -> list[tuple[str, str]]:
    with zipfile.ZipFile(raw.cached(SOURCE, SURREY).path) as z:
        parts = {
            name.rsplit(".", 1)[1].lower(): io.BytesIO(z.read(name))
            for name in z.namelist()
            if name.lower().endswith((".shp", ".shx", ".dbf"))
        }
    reader = shapefile.Reader(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], encoding="utf-8")
    features = []
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        name = re.sub(r" Ward( \(DET\))?$", "", record["NAME"])
        slug_ = re.sub(r"[^a-z0-9]+", "-", name.replace("&", "").lower()).strip("-")
        features.append((f"{SURREY_AUTHORITIES[record['FILE_NAME']]}:{slug_}", json.dumps(shape.__geo_interface__)))
    if len(features) != 82 or len({code for code, _ in features}) != 81:
        raise ValueError(f"surrey: {len(features)} shapes, expected 82 over 81 wards")
    return features


def division_key(text: str) -> str:
    key = leh.fold(text.replace("-", " ").replace("&", "and"))
    return DIVISION_SPELLINGS.get(key, key)


def divisions() -> pl.DataFrame:
    features = [
        feature
        for url in layer_pages("ced26")
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
    ]
    if len(features) != LAYERS["ced26"][2]:
        raise ValueError(f"ced26: {len(features)} features")
    with connect() as conn:
        conn.execute("create temp table division (code text, name text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy division from stdin") as copy:
            for feature in features:
                properties = feature["properties"]
                copy.write_row((properties["CED26CD"], properties["CED26NM"], json.dumps(feature["geometry"])))
        rows = conn.execute(
            """select distinct d.code, d.name, c.code
               from division d join (
                 select ac.code, g.geom from area a join area_code ac on ac.area_id = a.id
                 join area_geometry g on g.area_id = a.id
                 where a.scheme_code = 'gb.lad' and ac.code = any(%s) and upper_inf(g.valid)) c
               on st_within(st_pointonsurface(st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(d.geojson), 27700)))),
                            st_transform(c.geom, 27700))""",
            [[code for code, _ in COUNTIES.values()]],
        ).fetchall()
    frame = pl.DataFrame(rows, schema={"code": pl.String, "name": pl.String, "county": pl.String}, orient="row")
    if frame["code"].is_duplicated().any():
        raise ValueError("a county division lies in two counties")
    frame = frame.with_columns(name=pl.col("name").str.strip_suffix(" ED"))
    return frame.with_columns(key=pl.col("name").map_elements(division_key, return_dtype=pl.String))


def locate_divisions(found: pl.DataFrame, divisions: pl.DataFrame) -> pl.DataFrame:
    council = pl.col("ballot").str.split(".").list.get(1)
    shadow = council.is_in(list(SHADOWS)) & pl.col("gss").str.contains(r"^[A-Z]{3}:")
    found = (
        found.with_columns(
            council=council,
            key=pl.col("division").map_elements(division_key, return_dtype=pl.String),
            county=council.replace_strict({slug: code for slug, (code, _) in COUNTIES.items()}, default=None),
        )
        .join(
            divisions.select(match="code", county="county", key="key"),
            on=["county", "key"],
            how="left",
            maintain_order="left",
        )
        .with_columns(
            ward_code=pl.when(pl.col("match").is_not_null() & pl.col("coded").not_())
            .then("match")
            .otherwise("ward_code")
        )
        .join(
            divisions.select(ward_code="code", ced_name="name", ced_county="county"),
            on="ward_code",
            how="left",
            maintain_order="left",
        )
    )
    in_county = (pl.col("ced_county") == pl.col("county")).fill_null(False)
    return found.with_columns(
        la_code=pl.when(in_county)
        .then("county")
        .when(shadow)
        .then(council.replace_strict({slug: code for slug, (code, _) in SHADOWS.items()}, default=None))
        .otherwise("la_code"),
        la_name=pl.when(in_county)
        .then(council.replace_strict({slug: name for slug, (_, name) in COUNTIES.items()}, default=None))
        .when(shadow)
        .then(council.replace_strict({slug: name for slug, (_, name) in SHADOWS.items()}, default=None))
        .otherwise("la_name"),
        ons_name=pl.when(in_county).then("ced_name").when(shadow).then("division").otherwise("ons_name"),
        shape=pl.when(in_county).then(pl.lit("ced26")).when(shadow).then(pl.lit(SURREY_LAYER)).otherwise("shape"),
        coded=pl.col("coded") | in_county | shadow,
    ).drop("council", "key", "county", "match", "ced_name", "ced_county")


def statuses(council_file: pl.Expr) -> pl.Expr:
    press = pl.col("source").str.to_lowercase().str.contains("|".join(PRESS).replace(".", r"\."))
    return (
        pl.when(pl.col("system").is_null())
        .then(pl.lit("no EveryElection ballot"))
        .when(pl.col("territory").is_in(["ENG", "WLS"]).not_())
        .then(pl.lit("outside England and Wales"))
        .when(pl.col("system") != "FPTP")
        .then(pl.lit("not first past the post"))
        .when(pl.col("cancelled"))
        .then(pl.lit("cancelled"))
        .when(pl.col("coded").not_())
        .then(pl.lit("no GSS code"))
        .when(pl.col("la_code").is_null())
        .then(pl.lit("code not in the ONS ward lookup"))
        .when(pl.col("source").str.contains(r"(?i)andrewteale|\bLEAP\b"))
        .then(pl.lit("source is LEAP"))
        .when((press | pl.col("source").str.contains(r"https?://|www\.").not_()) & council_file.not_())
        .then(pl.lit("source is not the council"))
        .when(pl.col("winners") != pl.col("seats"))
        .then(pl.lit("winners do not equal seats"))
        .otherwise(pl.lit("loaded"))
    )


def lookup_frame() -> pl.DataFrame:
    frames = []
    for year, (_, suffix, _, _) in LOOKUPS.items():
        rows = [
            feature["attributes"]
            for url in lookup_pages(year)
            for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
        ]
        frames.append(
            pl.DataFrame(rows, infer_schema_length=None).select(
                year=pl.lit(year),
                ward_code=pl.col(f"WD{suffix}CD"),
                ons_name=pl.col(f"WD{suffix}NM"),
                la_code=pl.col(f"LAD{suffix}CD"),
                la_name=pl.col(f"LAD{suffix}NM"),
            )
        )
    return pl.concat(frames)


def layer_codes(key: str) -> set[str]:
    field = LAYERS[key][1]
    return {
        feature["properties"][field]
        for url in layer_pages(key)
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
    }


def nearest(vintage: int, code: str, held: dict[int, set[str]]) -> int | None:
    return min(
        (year for year, codes in held.items() if code in codes),
        key=lambda year: (abs(year - vintage), -year),
        default=None,
    )


def parse_by() -> None:
    lookup = lookup_frame()
    lookups_held = {held: set(codes) for held, codes in lookup.group_by("year").agg("ward_code").iter_rows()}
    wards_held = {int(key[2:]) + 2000: layer_codes(key) for key in LAYERS if key.startswith("wd")}
    councils_held = {int(key[3:]) + 2000: layer_codes(key) for key in LAYERS if key.startswith("lad")}
    BY.mkdir(parents=True, exist_ok=True)
    for year in by_years():
        records = by_records(year)
        count = sum(json.loads(raw.cached(SOURCE, url).path.read_text())["count"] for url in by_ballot_pages(year))
        ballots = pl.DataFrame(records, infer_schema_length=None)
        if ballots["ballot"].is_duplicated().any():
            raise ValueError(f"{year}: EveryElection lists a by-election twice")
        results = pl.concat([day_lines(day) for day in by_days(year)]).join(
            ballots.select(ballot_paper_id="ballot"), on="ballot_paper_id"
        )
        found = (
            ballots.join(
                results.group_by("ballot_paper_id", maintain_order=True)
                .agg(
                    winners=(pl.col("is_winner") == "True").sum(),
                    source=pl.col("source").first(),
                    spoilt=pl.col("spoilt_ballots").cast(pl.Int64, strict=False).max(),
                )
                .rename({"ballot_paper_id": "ballot"}),
                on="ballot",
                how="left",
            )
            .with_columns(
                year=pl.lit(year),
                vintage=pl.when(pl.col("day") < POLLING_DAYS[year]).then(pl.lit(year - 1)).otherwise(pl.lit(year)),
                ward_code=pl.col("gss").str.strip_prefix("gss:"),
                by=pl.lit(True),
                coded=pl.col("gss").str.starts_with("gss:").fill_null(False),
            )
            .with_columns(
                lookup_year=pl.struct("vintage", "ward_code").map_elements(
                    lambda row: nearest(row["vintage"], row["ward_code"] or "", lookups_held),
                    return_dtype=pl.Int64,
                )
            )
            .join(lookup.rename({"year": "lookup_year"}), on=["lookup_year", "ward_code"], how="left")
            .with_columns(
                shape=pl.struct("vintage", "ward_code").map_elements(
                    lambda row: (
                        None
                        if (held := nearest(row["vintage"], row["ward_code"] or "", wards_held)) is None
                        else f"wd{held % 100}"
                    ),
                    return_dtype=pl.String,
                ),
                la_layer=pl.struct("vintage", "la_code").map_elements(
                    lambda row: (
                        None
                        if (held := nearest(row["vintage"], row["la_code"] or "", councils_held)) is None
                        else f"lad{held % 100}"
                    ),
                    return_dtype=pl.String,
                ),
            )
        )
        found = found.with_columns(
            status=pl.when(pl.col("winners").is_null())
            .then(pl.lit("no Democracy Club result"))
            .otherwise(statuses(pl.lit(False)))
        )
        print(year, count, "ballots,", len(found), "off the polling day")
        print("  ", found.group_by("status").len().sort("len", descending=True).rows())
        print(
            "   Scottish with results:",
            found.filter(pl.col("territory") == "SCT", pl.col("winners").is_not_null()).height,
            "Scottish without:",
            found.filter(pl.col("territory") == "SCT", pl.col("winners").is_null()).height,
        )
        for row in found.filter(
            pl.col("status").is_in(["no GSS code", "code not in the ONS ward lookup", "winners do not equal seats"])
        ).iter_rows(named=True):
            print("  ", row["status"], row["ballot"], row["gss"], row["winners"], row["seats"])
        loaded = found.filter(pl.col("status") == "loaded")
        needed: dict[str, set[str]] = {}
        for layer, code in [
            *loaded.select("shape", "ward_code").rows(),
            *loaded.select("la_layer", "la_code").rows(),
        ]:
            if layer is not None:
                needed.setdefault(layer, set()).add(code)
        found.write_parquet(BY / f"{year}-wards.parquet")
        results.rename({"ballot_paper_id": "ballot"}).join(loaded.select("ballot"), on="ballot").select(
            "ballot",
            "person_id",
            "person_name",
            "party_name",
            votes=pl.col("ballots_cast").cast(pl.Int64),
            elected=pl.col("is_winner") == "True",
        ).write_parquet(BY / f"{year}-candidates.parquet")
        shapes_for(needed).write_parquet(BY / f"{year}-shapes.parquet")


def parse() -> None:
    if "DC_BY" in os.environ:
        return parse_by()
    wards, candidates = [], []
    lookup = lookup_frame()
    ced = divisions()
    for year in DAYS:
        records = []
        for url in ballot_pages(year):
            page = json.loads(raw.cached(SOURCE, url).path.read_text())
            if page["count"] != len(page["results"]):
                raise ValueError(f"{url}: {page['count']} elections, {len(page['results'])} on the page")
            records += [
                {
                    "ballot": record["election_id"],
                    "seats": record["seats_contested"],
                    "system": (record["voting_system"] or {}).get("slug"),
                    "gss": (record["division"] or {}).get("official_identifier"),
                    "division": (record["division"] or {}).get("name"),
                    "territory": (record["organisation"] or {}).get("territory_code"),
                    "cancelled": record["cancelled"],
                }
                for record in page["results"]
                if record["identifier_type"] == "ballot"
            ]
        ballots = pl.DataFrame(records, infer_schema_length=None)
        if ballots["ballot"].is_duplicated().any():
            raise ValueError(f"{year}: EveryElection lists a ballot twice")
        results = result_lines(year).filter(pl.col("election_id").str.starts_with("local."))
        found = (
            results.group_by("ballot_paper_id", maintain_order=True)
            .agg(
                winners=(pl.col("is_winner") == "True").sum(),
                source=pl.col("source").first(),
                spoilt=pl.col("spoilt_ballots").cast(pl.Int64, strict=False).max(),
            )
            .rename({"ballot_paper_id": "ballot"})
            .join(ballots, on="ballot", how="left")
            .with_columns(
                year=pl.lit(year),
                ward_code=pl.col("gss").str.strip_prefix("gss:"),
                by=pl.col("ballot").str.contains(r"\.by\."),
            )
            .join(lookup.filter(pl.col("year") == year).drop("year"), on="ward_code", how="left")
            .with_columns(
                leeds=pl.col("ballot").str.starts_with("local.leeds.")
                & pl.lit(year in LEEDS)
                & pl.col("la_code").is_null()
                & pl.col("gss").str.starts_with("gss:")
            )
            .with_columns(
                la_code=pl.when("leeds").then(pl.lit(LEEDS_CODE)).otherwise("la_code"),
                la_name=pl.when("leeds").then(pl.lit("Leeds")).otherwise("la_name"),
                ons_name=pl.when("leeds").then("division").otherwise("ons_name"),
            )
            .drop("leeds")
            .with_columns(coded=pl.col("gss").str.starts_with("gss:"), shape=pl.lit(f"wd{year % 100}"))
        )
        if year == 2026:
            found = locate_divisions(found, ced)
        found = found.with_columns(
            status=statuses(pl.col("ballot").str.starts_with("local.croydon.") & pl.lit(year in CROYDON))
        )
        print(year, found.group_by("status").len().sort("len", descending=True).rows())
        for row in found.filter(
            pl.col("status").is_in(["no GSS code", "code not in the ONS ward lookup", "winners do not equal seats"])
        ).iter_rows(named=True):
            print("  ", row["status"], row["ballot"], row["gss"], row["winners"], row["seats"])
        if year in TACIT:
            uncontested = []
            for url in tacit_urls(year):
                record = json.loads(raw.cached(SOURCE, url).path.read_text())
                elected = [line for line in record["candidacies"] if line["elected"]]
                if record["uncontested"] and record["replaced_by"] is None and elected:
                    uncontested.append((record, elected))
            tacit = (
                pl.DataFrame(
                    [
                        {
                            "ballot": record["ballot_paper_id"],
                            "winners": len(elected),
                            "source": (record["sopn"] or {}).get("source_url")
                            or (record["sopn"] or {}).get("uploaded_file")
                            or "",
                            "seats": record["winner_count"],
                        }
                        for record, elected in uncontested
                    ],
                    schema={"ballot": pl.String, "winners": pl.UInt32, "source": pl.String, "seats": pl.Int64},
                )
                .join(ballots.drop("seats"), on="ballot", how="left")
                .with_columns(
                    year=pl.lit(year),
                    ward_code=pl.col("gss").str.strip_prefix("gss:"),
                    by=pl.lit(False),
                    coded=pl.col("gss").str.starts_with("gss:"),
                    shape=pl.lit(f"wd{year % 100}"),
                )
                .join(lookup.filter(pl.col("year") == year).drop("year"), on="ward_code", how="left")
                .with_columns(
                    status=pl.when(pl.col("coded").not_())
                    .then(pl.lit("no GSS code"))
                    .when(pl.col("la_code").is_null())
                    .then(pl.lit("code not in the ONS ward lookup"))
                    .when(pl.col("winners") != pl.col("seats"))
                    .then(pl.lit("winners do not equal seats"))
                    .when(pl.col("source") == "")
                    .then(pl.lit("no statement of persons nominated"))
                    .otherwise(pl.lit("tacit"))
                )
            )
            print(year, "uncontested", tacit.group_by("status").len().rows())
            found = pl.concat([found, tacit], how="diagonal_relaxed")
            candidates.append(
                pl.DataFrame(
                    [
                        {
                            "ballot": record["ballot_paper_id"],
                            "person_id": str(line["person"]["id"]),
                            "person_name": line["person"]["name"],
                            "party_name": line["party_name"],
                            "votes": None,
                            "elected": True,
                        }
                        for record, elected in uncontested
                        for line in elected
                    ],
                    schema={
                        "ballot": pl.String,
                        "person_id": pl.String,
                        "person_name": pl.String,
                        "party_name": pl.String,
                        "votes": pl.Int64,
                        "elected": pl.Boolean,
                    },
                ).join(tacit.filter(pl.col("status") == "tacit").select("ballot"), on="ballot")
            )
        wards.append(found)
        loaded = found.filter(pl.col("status") == "loaded").select("ballot")
        candidates.append(
            results.rename({"ballot_paper_id": "ballot"})
            .join(loaded, on="ballot")
            .select(
                "ballot",
                "person_id",
                "person_name",
                "party_name",
                votes=pl.col("ballots_cast").cast(pl.Int64),
                elected=pl.col("is_winner") == "True",
            )
        )
    wards = pl.concat(wards)
    loaded = wards.filter(pl.col("status").is_in(["loaded", "tacit"]) | pl.col("status").str.starts_with("source is"))
    leeds = []
    for (code, year), url in sorted(
        ((code, year), url) for code, files in FILES.items() for year, url in files.items() if year in DAYS
    ):
        frame = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema_length=0, encoding="utf8-lossy")
        if code == CROYDON_CODE:
            frame = frame.with_columns(
                Name=pl.col("Name").str.strip_chars().str.replace(r"^((?:[A-Z'’-]+ )*[A-Z'’-]+) ([A-Z][a-z])", "$1, $2")
            ).select(
                ward=pl.col("Ward").str.strip_chars(),
                surname=pl.col("Name").str.split(", ").list.first(),
                name=pl.concat_str(
                    pl.col("Name").str.split(", ").list.last(),
                    pl.col("Name").str.split(", ").list.first(),
                    separator=" ",
                ),
                votes="Votes",
                elected=pl.col("Elected").str.strip_chars() == "Elected",
                spoilt=pl.lit(None, dtype=pl.String),
            )
        elif year == 2016:
            frame = frame.select(
                ward=pl.col("ConstituencyURL").str.extract(r"/(E05\d{6})$"),
                surname=pl.col("CandidateName").str.extract(r"^((?:[A-Z'-]+ )*[A-Z'-]+) "),
                name=pl.concat_str(
                    pl.col("CandidateName").str.replace(r"^((?:[A-Z'-]+ )*[A-Z'-]+) ", "").str.strip_chars(),
                    pl.col("CandidateName").str.extract(r"^((?:[A-Z'-]+ )*[A-Z'-]+) "),
                    separator=" ",
                ),
                votes="VotesWon",
                elected=(pl.col("Elected") == "Elected").fill_null(False),
                spoilt="Rejected-Invalid-Votes",
            )
        else:
            frame = frame.filter(pl.col("Wardname").is_not_null()).select(
                ward="Wardname",
                surname="Surname",
                name=pl.concat_str("Forename", "Surname", separator=" "),
                votes="Votes",
                elected=pl.col("Elected").str.to_uppercase() == "TRUE",
                spoilt="SpoiltPapers",
            )
        leeds.append(
            frame.select(
                council=pl.lit(code),
                year=pl.lit(year),
                ward=pl.col("ward")
                if code == LEEDS_CODE and year == 2016
                else pl.col("ward").map_elements(
                    lambda text: " ".join(word for word in leh.fold(text.replace("-", " ")).split() if word != "and"),
                    return_dtype=pl.String,
                ),
                surname=pl.col("surname").map_elements(lambda text: leh.fold(text).split()[-1], return_dtype=pl.String),
                name="name",
                votes=pl.col("votes").cast(pl.Int64),
                elected="elected",
                spoilt=pl.col("spoilt").cast(pl.Int64, strict=False),
            )
        )
    shapes = shapes_for(
        {
            key: codes
            for year in DAYS
            for key, codes in (
                *(
                    (layer, set(loaded.filter((pl.col("year") == year) & (pl.col("shape") == layer))["ward_code"]))
                    for layer in (f"wd{year % 100}", *([SURREY_LAYER, "ced26"] if year == 2026 else []))
                ),
                (f"lad{year % 100}", set(loaded.filter(pl.col("year") == year)["la_code"])),
            )
        }
    )
    print(shapes.group_by("layer").len().sort("layer").rows())
    PARSED.mkdir(parents=True, exist_ok=True)
    wards.write_parquet(PARSED / "wards.parquet")
    pl.concat(candidates).write_parquet(PARSED / "candidates.parquet")
    pl.concat(leeds).write_parquet(PARSED / "leeds.parquet")
    shapes.write_parquet(PARSED / "shapes.parquet")


def shapes_for(needed: dict[str, set[str]]) -> pl.DataFrame:
    with connect() as conn:
        conn.execute("create temp table feature (layer text, code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for key, codes in needed.items():
                if key == SURREY_LAYER:
                    for code, geojson in surrey_features():
                        if code in codes:
                            copy.write_row((key, code, geojson))
                            copy.write_row((key, code.split(":")[0], geojson))
                    continue
                _, field, count, _, _ = LAYERS[key]
                features = [
                    feature
                    for url in layer_pages(key)
                    for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
                ]
                if len(features) != count:
                    raise ValueError(f"{key}: {len(features)} features, expected {count}")
                for feature in features:
                    if feature["properties"][field] in codes:
                        copy.write_row((key, feature["properties"][field], json.dumps(feature["geometry"])))
        rows = conn.execute(
            """select layer, code,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select layer, code, st_multi(st_collectionextract(st_makevalid(st_union(
                       st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700))))), 3)) as geom
                     from feature group by layer, code) whole"""
        ).fetchall()
    return pl.DataFrame(
        rows,
        schema={"layer": pl.String, "code": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )


def emit() -> Path:
    by_mode = "DC_BY" in os.environ
    years = by_years() if by_mode else []
    days = {} if by_mode else {year: day for year, day in DAYS.items() if year in SAMPLED | CROSS_CHECKS}
    if by_mode:
        wards = pl.concat(
            [pl.read_parquet(BY / f"{year}-wards.parquet") for year in years], how="diagonal_relaxed"
        ).filter(pl.col("status") == "loaded")
        candidates = pl.concat([pl.read_parquet(BY / f"{year}-candidates.parquet") for year in years])
        shape_rows = pl.concat([pl.read_parquet(BY / f"{year}-shapes.parquet") for year in years]).unique(
            ["layer", "code"]
        )
    else:
        wards = (
            pl.read_parquet(PARSED / "wards.parquet")
            .filter(pl.col("year").is_in(list(days)))
            .with_columns(
                day=pl.col("year").replace_strict(days, return_dtype=pl.Date),
                la_layer=pl.when(pl.col("la_code").str.starts_with("E10"))
                .then(None)
                .when(pl.col("la_code").is_in([code for code, _ in SHADOWS.values()]))
                .then(pl.lit(SURREY_LAYER))
                .otherwise(pl.format("lad{}", pl.col("year") % 100)),
            )
        )
        candidates = pl.read_parquet(PARSED / "candidates.parquet")
        shape_rows = pl.read_parquet(PARSED / "shapes.parquet")
    shapes = {(layer, code): (geom, point) for layer, code, geom, point in shape_rows.rows()}
    polls = sorted(set(wards.select("year", "day").rows()))
    with connect() as conn:
        published = [
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'gb.dc_local.result_set'"
            ).fetchall()
        ]
        wards = wards.filter(
            pl.col("status").is_in(["loaded", "tacit"])
            | pl.col("status").str.starts_with("source is")
            & pl.format(
                "{}:local:{}{}",
                pl.col("day").cast(pl.String),
                "ward_code",
                pl.when("by").then(pl.lit(":by")).otherwise(pl.lit("")),
            ).is_in(published)
        )
        defaults: dict[int, int] = {}
        handbook = {
            contest
            for (contest,) in conn.execute(
                "select rs.contest_id from result_set rs join changeset c on c.id = rs.changeset_id "
                "where c.pipeline = 'gb.leh' and rs.is_default"
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"English and Welsh council by-elections {', '.join(map(str, years))} off the May polling day, "
            "by ward, compiled by Democracy Club from the councils' declarations, with ONS ward and council shapes"
            if by_mode
            else f"English and Welsh council elections {', '.join(map(str, days))} by ward and county division, "
            "compiled by Democracy Club from the councils' declarations, with ONS ward, division, and council shapes",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        countries = {"E": cs.area("gb.country", "ENG"), "W": cs.area("gb.country", "WLS")}
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if cs.minted & {*countries.values(), *systems.values()}:
            raise ValueError("publish gb.umit and gb.parliament first")
        if conn.execute("select count(*) from scheme where code in ('gb.lad', 'gb.ward')").fetchone()[0] != 2:
            raise ValueError("publish gb.leh first: the gb.lad and gb.ward schemes must exist")
        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        party_ids: dict[str, UUID | None] = {"Independent": None}

        sources: dict[int, UUID] = {}
        for year, day in days.items():
            title = f"Candidate results, {day}"
            sources[year] = cs.source(DC, title)
            cs.add(
                "source",
                id=sources[year],
                kind="compendium",
                publisher=DC,
                title=title,
                url=RESULTS.format(day=day) if year in ARCHIVED else API.format(day=day),
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
                notes=METHODOLOGY,
            )
            for url in result_pages(year):
                cs.artifact(raw.cached(SOURCE, url), sources[year])
            for url in ballot_pages(year):
                cs.artifact(raw.cached(SOURCE, url), sources[year])
        for year in years:
            title = f"Candidate results, council by-elections {year}"
            sources[year] = cs.source(DC, title)
            cs.add(
                "source",
                id=sources[year],
                kind="compendium",
                publisher=DC,
                title=title,
                url=BY_BALLOTS.format(council="", year=year, month=""),
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
                notes=f"{METHODOLOGY} Ballots come from EveryElection's by-election register for the year; results "
                "from Democracy Club's results API, one request per polling day.",
            )
            for day in by_days(year):
                for url in day_pages(day):
                    cs.artifact(raw.cached(SOURCE, url), sources[year])
            for url in by_ballot_pages(year):
                cs.artifact(raw.cached(SOURCE, url), sources[year])
        tacit_sources: dict[int, UUID] = {}
        for year, day in days.items():
            if year in TACIT:
                title = f"Candidate records for uncontested wards, {day}"
                tacit_sources[year] = cs.source(DC, title)
                cs.add(
                    "source",
                    id=tacit_sources[year],
                    kind="compendium",
                    publisher=DC,
                    title=title,
                    url=f"https://candidates.democracyclub.org.uk/api/next/ballots/?election_date={day}",
                    license=LICENSE,
                    license_url=LICENSE_URL,
                    attribution=ATTRIBUTION,
                    notes="Wards where the council's statement of persons nominated listed no more candidates than "
                    "seats, so the poll was cancelled and the candidates were elected unopposed. Democracy Club's "
                    "ballot record marks them elected; each citation links the council's statement.",
                )
                for url in tacit_urls(year):
                    cs.artifact(raw.cached(SOURCE, url), tacit_sources[year])
        leeds_sources: dict[tuple[str, int], UUID] = {}
        for year, day in days.items():
            if year in LEEDS:
                title = f"Local election results, {day}"
                leeds_sources[(LEEDS_CODE, year)] = cs.source("Leeds City Council", title)
                cs.add(
                    "source",
                    id=leeds_sources[(LEEDS_CODE, year)],
                    kind="official_body",
                    publisher="Leeds City Council",
                    title=title,
                    url=LEEDS[year],
                    license="Open Government Licence v3.0",
                    license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
                    attribution="Contains public sector information licensed under the Open Government Licence v3.0.",
                    notes="Leeds City Council's results file on Data Mill North, one row per candidate.",
                )
                cs.artifact(raw.cached(SOURCE, LEEDS[year]), leeds_sources[(LEEDS_CODE, year)])
            if year in CROYDON:
                title = f"Local election results, {day}: results data"
                leeds_sources[(CROYDON_CODE, year)] = cs.source("Croydon Council", title)
                cs.add(
                    "source",
                    id=leeds_sources[(CROYDON_CODE, year)],
                    kind="official_body",
                    publisher="Croydon Council",
                    title=title,
                    url=CROYDON_PAGE,
                    notes="Croydon Council's results spreadsheet, one row per candidate, which the council's results "
                    "page links as its data (a published Google Sheet, read as CSV). The council's scanned "
                    "declaration notices are linked from the same page.",
                )
                cs.artifact(raw.cached(SOURCE, CROYDON[year]), leeds_sources[(CROYDON_CODE, year)])
        council_rows: dict[tuple[str, int, str], list[dict[str, object]]] = {}
        for row in (
            pl.read_parquet(PARSED / "leeds.parquet").filter(pl.col("year").is_in(list(days))).iter_rows(named=True)
        ):
            council_rows.setdefault((row["council"], row["year"], row["ward"]), []).append(row)
        used: set[tuple[str, int, str]] = set()
        ons: dict[str, UUID] = {}
        layers = {*wards["shape"].drop_nulls(), *wards["la_layer"].drop_nulls()}
        foreign = {
            id
            for (id,) in conn.execute(
                "select s.id from source s join changeset c on c.id = s.changeset_id where c.pipeline <> %s",
                [SOURCE],
            ).fetchall()
        }
        for key, (service, _, _, _, title) in LAYERS.items():
            if key not in layers:
                continue
            ons[key] = cs.source("Office for National Statistics", title)
            if ons[key] in foreign:
                continue
            cs.add(
                "source",
                id=ons[key],
                kind="official_body",
                publisher="Office for National Statistics",
                title=title,
                url=f"{ONS}/{service}/FeatureServer",
                license="Open Government Licence v3.0",
                license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
                attribution=(
                    "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
                    "Contains OS data © Crown copyright and database right"
                ),
                notes="Generalized boundaries (20 m) clipped to the coastline, as of December of the election year.",
            )
            for url in layer_pages(key):
                cs.artifact(raw.cached(SOURCE, url), ons[key])
            if key.startswith("wd") and int(key[-2:]) + 2000 in LOOKUPS:
                for url in lookup_pages(int(key[-2:]) + 2000):
                    cs.artifact(raw.cached(SOURCE, url), ons[key])

        if SURREY_LAYER in layers:
            ons[SURREY_LAYER] = cs.source("Ordnance Survey", SURREY_TITLE)
            if ons[SURREY_LAYER] not in foreign:
                cs.add(
                    "source",
                    id=ons[SURREY_LAYER],
                    kind="official_body",
                    publisher="Ordnance Survey",
                    title=SURREY_TITLE,
                    url="https://docs.os.uk/os-downloads/products/areas-and-zones-portfolio/boundary-line/boundary-line-downloads",
                    license="Open Government Licence v3.0",
                    license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
                    attribution="Contains OS data © Crown copyright and database right, licensed under the Open Government Licence v3.0.",
                    notes=(
                        "Boundaries of the 81 wards of East and West Surrey under the Surrey (Structural Changes) Order 2026; "
                        "each council's shape is the union of its wards."
                    ),
                )
                cs.artifact(raw.cached(SOURCE, SURREY), ons[SURREY_LAYER])

        paths = {path for (path,) in conn.execute("select path from slug where path like 'gb/%%'").fetchall()}
        councils: dict[str, tuple[UUID, UUID, UUID, str, str]] = {}
        national: dict[int, UUID] = {}
        elections: set[UUID] = set()
        seen: set[UUID] = set()
        geometry: dict[UUID, tuple[str, str, date]] = {}
        elsewhere = {
            id
            for (id,) in conn.execute(
                "select x.entity_id from external_id x join changeset c on c.id = x.changeset_id "
                "where x.scheme = 'gb.election' and c.pipeline <> %s",
                [SOURCE],
            ).fetchall()
        }

        for year, day in polls:
            if year in days and year not in national:
                national[year] = cs.key("gb.election", f"{day}:local:gb")
            if year in days and national[year] in cs.minted:
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
            lines = candidates.join(wards.filter(pl.col("day") == day).select("ballot"), on="ballot")
            only_by = set(
                wards.filter(pl.col("day") == day)
                .group_by("la_code")
                .agg(pl.col("by").all())
                .filter(pl.col("by"))["la_code"]
            )
            for ward in wards.filter(pl.col("day") == day).sort("ballot").iter_rows(named=True):
                code, ward_code = ward["la_code"], ward["ward_code"]
                if code not in councils:
                    area = cs.area("gb.lad", code)
                    polity = cs.key("gb.polity", code)
                    body = cs.key("gb.body", code)
                    label = ward["la_name"]
                    place = label.removesuffix(", City of").removesuffix(", County of")
                    if area in cs.minted:
                        cs.add("area", id=area, scheme_code="gb.lad")
                        cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                        cs.add(
                            "area_parent",
                            area_id=area,
                            parent_id=countries["W" if code.startswith("W0") else "E"],
                            valid_from=None,
                            valid_to=None,
                        )
                        name(area, label)
                    if polity in cs.minted:
                        council_name = leh.COUNCIL_NAMES.get(
                            code, f"{place} City Council" if label.endswith(", City of") else f"{place} Council"
                        )
                        path = f"gb/{slug(place)}"
                        if path in paths:
                            path = f"{path}-{code.lower()}"
                        paths.add(path)
                        cs.add("polity", id=polity, kind="municipality", iso_code=None)
                        cs.add(
                            "polity_parent",
                            polity_id=polity,
                            parent_id=cs.polity("GB-WLS" if code.startswith("W0") else "GB-ENG"),
                        )
                        cs.add("polity_territory", polity_id=polity, area_id=area)
                        name(polity, council_name)
                        cs.add("slug", path=path, entity_id=polity)
                    else:
                        path = conn.execute(
                            "select path from slug where entity_id = %s order by is_current desc limit 1", [polity]
                        ).fetchone()[0]
                        council_name = conn.execute(
                            "select text from name where entity_id = %s and is_preferred limit 1", [polity]
                        ).fetchone()[0]
                    if body in cs.minted:
                        cs.add("body", id=body, polity_id=polity, kind="council")
                        name(body, council_name)
                    councils[code] = (area, polity, body, path, council_name)
                    if ward["la_layer"] is not None:
                        geometry.setdefault(area, (ward["la_layer"], code, day))
                area, polity, body, path, council_name = councils[code]
                election = cs.key("gb.election", f"{day}:local:{code}")
                if election not in elections and (year in days or election not in elsewhere):
                    elections.add(election)
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polity,
                        parent_id=national.get(year) if year in days else None,
                        kind="by_election" if code in only_by else "local",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(
                        election,
                        f"{council_name} by-election, {day.day} {day:%B %Y}"
                        if year not in days
                        else f"{year} {council_name} {'by-election' if code in only_by else 'election'}",
                    )
                    cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
                display = ward["ons_name"]
                display = " ".join(
                    word.lower() if index and word in leh.SMALL_WORDS else word
                    for index, word in enumerate(display.split())
                )
                ward_area = cs.area("gb.ward", ward_code)
                if ward_area not in seen:
                    seen.add(ward_area)
                    if ward_area in cs.minted:
                        cs.add("area", id=ward_area, scheme_code="gb.ward")
                        cs.add("area_code", area_id=ward_area, code=ward_code, valid_from=None, valid_to=None)
                        cs.add("area_parent", area_id=ward_area, parent_id=area, valid_from=None, valid_to=None)
                        name(ward_area, display)
                    if ward["shape"] is not None:
                        geometry.setdefault(ward_area, (ward["shape"], ward_code, day))
                unit = cs.unit("gb.ward", ward_code)
                if unit in cs.minted and unit not in seen:
                    seen.add(unit)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="gb.ward",
                        code=ward_code,
                        area_relation="equals",
                        area_id=ward_area,
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, display)
                seats = ward["seats"]
                contest_key = f"{day}:local:{ward_code}{':by' if ward['by'] else ''}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=ward_area,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=systems["fptp" if seats == 1 else "block"],
                    default_result_kind="tacit" if ward["status"] == "tacit" else "counted",
                    term="remainder" if ward["by"] else None,
                )
                by = " by-election" if ward["by"] else ""
                part = "division" if code.startswith("E10") else "ward"
                name(contest, f"{display} {part}{by}, {council_name}, {year}")
                cs.add(
                    "slug",
                    path=f"{path}/{day}-local/{slug(display)}-{slug(ward_code)}{'-by-election' if ward['by'] else ''}",
                    entity_id=contest,
                )
                cs.add("external_id", entity_id=contest, scheme="democracyclub.ballot", value=ward["ballot"])
                ballot_lines = lines.filter(pl.col("ballot") == ward["ballot"]).rows(named=True)
                place = (
                    (code, year, ward_code)
                    if (code, year, ward_code) in council_rows
                    else (
                        code,
                        year,
                        " ".join(
                            word for word in leh.fold(ward["division"].replace("-", " ")).split() if word != "and"
                        ),
                    )
                )
                council = council_rows.get(place)
                tallies = {line["person_id"]: (line["votes"], line["elected"]) for line in ballot_lines}
                counted = tallies
                if council is not None:
                    used.add(place)
                    counted = {}
                    for row in council:
                        hits = [
                            line for line in ballot_lines if leh.fold(line["person_name"]).split()[-1] == row["surname"]
                        ]
                        if len(hits) != 1:
                            hits = [
                                line
                                for line in hits or ballot_lines
                                if leh.fold(line["person_name"]).split()[0] == leh.fold(str(row["name"])).split()[0]
                            ]
                        if len(hits) != 1:
                            raise ValueError(f"{ward['ballot']}: {row['name']} matches {len(hits)} candidates")
                        counted[hits[0]["person_id"]] = (row["votes"], row["elected"])
                    if counted.keys() != tallies.keys():
                        raise ValueError(
                            f"{ward['ballot']}: the council's file and Democracy Club list different candidates"
                        )
                differing = [line for line in ballot_lines if tallies[line["person_id"]] != counted[line["person_id"]]]
                sets = []
                tacit = ward["status"] == "tacit"
                withdrawn = ward["status"] not in ("loaded", "tacit")
                if not differing:
                    sets.append(
                        (
                            cs.key("gb.dc_local.result_set", contest_key),
                            tacit_sources[year] if tacit else sources[year],
                            council is None and not withdrawn and contest not in handbook and year not in CROSS_CHECKS,
                            {"ballot_paper_id": ward["ballot"], "council_source": ward["source"]},
                            ward["spoilt"],
                            tallies,
                        )
                    )
                if council is not None:
                    leeds_set = cs.key("gb.dc_local.leeds_result_set", contest_key)
                    sets.append(
                        (
                            leeds_set,
                            leeds_sources[(code, year)],
                            True,
                            {"ward": ward["division"]},
                            council[0]["spoilt"],
                            counted,
                        )
                    )
                    for line in differing:
                        cs.add(
                            "note",
                            entity_id=leeds_set,
                            kind="caveat",
                            body_md=f"Democracy Club gives {line['person_name']} {line['votes']} votes; "
                            f"{council_name}'s results file gives {counted[line['person_id']][0]}.",
                        )
                for result_set, source, default, locator, spoilt, _ in sets:
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="tacit" if tacit else "counted",
                        status="withdrawn" if withdrawn else "active",
                        source_id=source,
                        is_default=default,
                        completeness=1,
                    )
                    defaults[year] = defaults.get(year, 0) + default
                    if withdrawn:
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md="Democracy Club's record cites the Local Elections Archive, not the council; "
                            "withdrawn under the source rule."
                            if ward["status"] == "source is LEAP"
                            else "Democracy Club's record cites no council page; withdrawn under the source rule.",
                        )
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=source,
                        locator={"ballot_paper_id": ward["ballot"], "statement_of_persons_nominated": ward["source"]}
                        if tacit
                        else locator,
                    )
                    if spoilt is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=INVALID,
                            count_round=0,
                            flags=0,
                            value=spoilt,
                        )
                for line in ballot_lines:
                    label = line["party_name"]
                    if label not in party_ids:
                        found = (
                            cs.key(*leh.RESOLVED[label])
                            if label in leh.RESOLVED
                            else known.get(leh.fold(ALIASES.get(label, label)))
                        )
                        if found is None:
                            found = cs.key("gb.party", slug(label))
                            if found in cs.minted and found not in party_ids.values():
                                cs.add("party", id=found, kind="party", polity_id=gb)
                                name(found, label)
                        party_ids[label] = found
                    owner = party_ids[label]
                    option_key = f"{contest_key}:{line['person_id']}"
                    option = cs.key("gb.dc_local.option", option_key)
                    person = cs.key("gb.dc_local.person", line["person_id"])
                    if person in cs.minted and person not in seen:
                        seen.add(person)
                        cs.add("person", id=person)
                        name(person, line["person_name"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=line["person_name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.dc_local.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=line["person_name"],
                        member_of_party_id=owner,
                        is_independent=label == "Independent",
                    )
                    for result_set, _, _, _, _, values in sets:
                        votes, elected = values[line["person_id"]]
                        if tacit:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                            continue
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=0,
                            value=votes,
                        )
                        if elected:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        if unused := set(council_rows) - used:
            raise ValueError(f"council rows with no Democracy Club ballot: {sorted(unused)}")

        others: dict[UUID, list[tuple[date, date | None]]] = {}
        for area, start, end in conn.execute(
            "select area_id, lower(valid), upper(valid) from area_geometry where area_id = any(%s) and source_id <> all(%s)",
            [list(geometry), [id for id in ons.values() if id not in foreign]],
        ).fetchall():
            others.setdefault(area, []).append((start, end))
        missing: list[str] = []
        for area, (layer, code, day) in geometry.items():
            if (layer, code) not in shapes:
                missing.append(f"{code} ({layer})")
                continue
            spans = others.get(area, [])
            if any((start is None or start <= day) and (end is None or end > day) for start, end in spans):
                continue
            later = [start for start, _ in spans if start is not None and start > day]
            geom, point = shapes[(layer, code)]
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=day,
                valid_to=min(later) if later else None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=ons[layer],
            )
        print(f"{len(missing)} areas without a shape: {', '.join(sorted(missing))}")
        plans = {
            (scheme, vintage): id
            for id, scheme, vintage in conn.execute(
                "select id, scheme_code, vintage from boundary_set where scheme_code in ('gb.ward', 'gb.lad')"
            ).fetchall()
        }
        starts = sorted(
            {
                *days.values(),
                *(
                    start
                    for (start,) in conn.execute(
                        "select lower(valid) from boundary_set where scheme_code in ('gb.ward', 'gb.lad') "
                        "and lower(valid) is not null"
                    ).fetchall()
                ),
            }
        )
        for scheme in ("gb.ward", "gb.lad"):
            for year, day in sorted(days.items()):
                end = next((start for start in starts if start > day), None)
                cs.add(
                    "boundary_set",
                    **({"id": plans[(scheme, str(year))]} if (scheme, str(year)) in plans else {}),
                    scheme_code=scheme,
                    vintage=str(year),
                    valid_from=day,
                    valid_to=end,
                    as_of=day,
                )
        if 2019 in days and defaults.get(2019) != 5044:
            raise ValueError(f"{defaults.get(2019)} default result sets for 2019, not 5,044")
        return cs.write()
