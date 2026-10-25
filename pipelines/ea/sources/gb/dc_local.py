import json
import time
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

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
RESULTS = "https://candidates.democracyclub.org.uk/media/csv-archives/results-{day}.csv"
ARCHIVED = {2018, 2019, 2023}
SAMPLED = {2018, 2019, 2026}
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
    2023: ("WD23_LAD23_CTY23_OTH_UK_LU", "23", 8441, 1000),
    2024: ("WD24_LAD24_UK_LU", "24", 8396, 1000),
    2025: ("WD25_LAD25_UK_LU_v2", "25", 8405, 1000),
    2026: ("WD26_LAD26_UK_LU", "26", 8413, 1000),
}
LAYERS = {
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
}
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
    "ballots whose source is a newspaper, broadcaster, social media post, Wikipedia, or the Local Elections Archive "
    "Project, ballots that cite no page, and ballots whose winners do not equal the seats contested, are not loaded. Votes for the rejected ballots are the printed spoilt ballots."
)
EE_TITLE = "EveryElection"
VOTES, INVALID = 1, 5


def result_pages(year: int) -> list[str]:
    if year in ARCHIVED:
        return [RESULTS.format(day=DAYS[year])]
    first = f"{API.format(day=DAYS[year])}&page_size=200&page=1"
    count = json.loads(raw.cached(SOURCE, first).path.read_text())["count"]
    return [f"{API.format(day=DAYS[year])}&page_size=200&page={page}" for page in range(1, (count - 1) // 200 + 2)]


def result_lines(year: int) -> pl.DataFrame:
    if year in ARCHIVED:
        frame = pl.read_csv(raw.cached(SOURCE, RESULTS.format(day=DAYS[year])).path, infer_schema_length=0)
        return frame.rename({"elected": "is_winner"}) if "is_winner" not in frame.columns else frame
    rows = []
    for url in result_pages(year):
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
    frame = pl.DataFrame(rows, schema=dict.fromkeys(rows[0], pl.String))
    if frame.select("ballot_paper_id", "person_id").is_duplicated().any():
        raise ValueError(f"{year}: a candidate appears twice on a ballot")
    return frame


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


def lookup_pages(year: int) -> list[str]:
    service, suffix, count, size = LOOKUPS[year]
    return [
        f"{ONS}/{service}/FeatureServer/0/query?where=1%3D1&outFields=*&returnGeometry=false&orderByFields=WD{suffix}CD"
        f"&resultOffset={offset}&resultRecordCount={size}&f=json"
        for offset in range(0, count, size)
    ]


def layer_pages(key: str) -> list[str]:
    service, field, count, size, _ = LAYERS[key]
    return [
        f"{ONS}/{service}/FeatureServer/0/query?where=1%3D1&outFields={field}&outSR=27700&orderByFields={field}"
        f"&resultOffset={offset}&resultRecordCount={size}&f=geojson"
        for offset in range(0, count, size)
    ]


def fetch() -> None:
    for year in LOOKUPS:
        for url in lookup_pages(year):
            raw.fetch(SOURCE, url, suffix=".json")
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
        for key in (f"wd{year % 100}", f"lad{year % 100}"):
            for url in layer_pages(key):
                raw.fetch(SOURCE, url, suffix=".geojson")


def parse() -> None:
    wards, candidates, lookups = [], [], []
    for year, (_, suffix, _, _) in LOOKUPS.items():
        rows = [
            feature["attributes"]
            for url in lookup_pages(year)
            for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
        ]
        lookups.append(
            pl.DataFrame(rows, infer_schema_length=None).select(
                year=pl.lit(year),
                ward_code=pl.col(f"WD{suffix}CD"),
                ons_name=pl.col(f"WD{suffix}NM"),
                la_code=pl.col(f"LAD{suffix}CD"),
                la_name=pl.col(f"LAD{suffix}NM"),
            )
        )
    lookup = pl.concat(lookups)
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
        )
        press = pl.col("source").str.to_lowercase().str.contains("|".join(PRESS).replace(".", r"\."))
        found = found.with_columns(
            status=pl.when(pl.col("system").is_null())
            .then(pl.lit("no EveryElection ballot"))
            .when(pl.col("territory").is_in(["ENG", "WLS"]).not_())
            .then(pl.lit("outside England and Wales"))
            .when(pl.col("system") != "FPTP")
            .then(pl.lit("not first past the post"))
            .when(pl.col("cancelled"))
            .then(pl.lit("cancelled"))
            .when(pl.col("gss").is_null() | pl.col("gss").str.starts_with("gss:").not_())
            .then(pl.lit("no GSS code"))
            .when(pl.col("la_code").is_null())
            .then(pl.lit("code not in the ONS ward lookup"))
            .when(pl.col("source").str.contains(r"(?i)andrewteale|\bLEAP\b"))
            .then(pl.lit("source is LEAP"))
            .when(
                (press | pl.col("source").str.contains(r"https?://|www\.").not_())
                & (pl.col("ballot").str.starts_with("local.croydon.") & pl.lit(year in CROYDON)).not_()
            )
            .then(pl.lit("source is not the council"))
            .when(pl.col("winners") != pl.col("seats"))
            .then(pl.lit("winners do not equal seats"))
            .otherwise(pl.lit("loaded"))
        )
        print(year, found.group_by("status").len().sort("len", descending=True).rows())
        for row in found.filter(
            pl.col("status").is_in(["no GSS code", "code not in the ONS ward lookup", "winners do not equal seats"])
        ).iter_rows(named=True):
            print("  ", row["status"], row["ballot"], row["gss"], row["winners"], row["seats"])
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
    loaded = wards.filter((pl.col("status") == "loaded") | pl.col("status").str.starts_with("source is"))
    leeds = []
    for (code, year), url in sorted(
        ((code, year), url) for code, files in FILES.items() for year, url in files.items() if year in DAYS
    ):
        frame = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema_length=0, encoding="utf8-lossy")
        if code == CROYDON_CODE:
            frame = frame.select(
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
    with connect() as conn:
        conn.execute("create temp table feature (layer text, code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for year in DAYS:
                for key, codes in (
                    (f"wd{year % 100}", set(loaded.filter(pl.col("year") == year)["ward_code"])),
                    (f"lad{year % 100}", set(loaded.filter(pl.col("year") == year)["la_code"])),
                ):
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
    shapes = pl.DataFrame(
        rows,
        schema={"layer": pl.String, "code": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
    print(shapes.group_by("layer").len().sort("layer").rows())
    PARSED.mkdir(parents=True, exist_ok=True)
    wards.write_parquet(PARSED / "wards.parquet")
    pl.concat(candidates).write_parquet(PARSED / "candidates.parquet")
    pl.concat(leeds).write_parquet(PARSED / "leeds.parquet")
    shapes.write_parquet(PARSED / "shapes.parquet")


def emit() -> Path:
    days = {year: day for year, day in DAYS.items() if year in SAMPLED}
    wards = pl.read_parquet(PARSED / "wards.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    shapes = {
        (layer, code): (geom, point) for layer, code, geom, point in pl.read_parquet(PARSED / "shapes.parquet").rows()
    }
    with connect() as conn:
        published = [
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'gb.dc_local.result_set'"
            ).fetchall()
        ]
        wards = wards.filter(
            (pl.col("status") == "loaded")
            | pl.col("status").str.starts_with("source is")
            & pl.format(
                "{}:local:{}{}",
                pl.col("year").replace_strict({year: str(day) for year, day in days.items()}, default=None),
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
            summary=f"English and Welsh council elections {', '.join(map(str, days))} by ward, compiled by Democracy "
            "Club from the councils' declarations, with ONS ward and council shapes",
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
        for key, (service, _, _, _, title) in LAYERS.items():
            if int(key[-2:]) + 2000 not in days:
                continue
            ons[key] = cs.source("Office for National Statistics", title)
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
            for url in lookup_pages(int(key[-2:]) + 2000):
                cs.artifact(raw.cached(SOURCE, url), ons[key])

        paths = {path for (path,) in conn.execute("select path from slug where path like 'gb/%%'").fetchall()}
        councils: dict[str, tuple[UUID, UUID, UUID, str, str]] = {}
        national: dict[int, UUID] = {}
        elections: set[UUID] = set()
        seen: set[UUID] = set()
        geometry: dict[UUID, tuple[str, str, date]] = {}

        for year, day in days.items():
            national[year] = cs.key("gb.election", f"{day}:local:gb")
            if national[year] in cs.minted:
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
            lines = candidates.join(wards.filter(pl.col("year") == year).select("ballot"), on="ballot")
            for ward in wards.filter(pl.col("year") == year).sort("ballot").iter_rows(named=True):
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
                            "area_parent", area_id=area, parent_id=countries[code[0]], valid_from=None, valid_to=None
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
                            parent_id=cs.polity("GB-WLS" if code[0] == "W" else "GB-ENG"),
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
                    geometry.setdefault(area, (f"lad{year % 100}", code, day))
                area, polity, body, path, council_name = councils[code]
                election = cs.key("gb.election", f"{day}:local:{code}")
                if election not in elections:
                    elections.add(election)
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polity,
                        parent_id=national[year],
                        kind="local",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(election, f"{year} {council_name} election")
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
                    geometry.setdefault(ward_area, (f"wd{year % 100}", ward_code, day))
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
                    default_result_kind="counted",
                )
                by = " by-election" if ward["by"] else ""
                name(contest, f"{display} ward{by}, {council_name}, {year}")
                cs.add(
                    "slug",
                    path=f"{path}/{day}-local/{slug(display)}-{ward_code.lower()}{'-by-election' if ward['by'] else ''}",
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
                withdrawn = ward["status"] != "loaded"
                if not differing:
                    sets.append(
                        (
                            cs.key("gb.dc_local.result_set", contest_key),
                            sources[year],
                            council is None and not withdrawn and contest not in handbook,
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
                        kind="counted",
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
                    cs.add("citation", entity_id=result_set, source_id=source, locator=locator)
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
                        found = known.get(leh.fold(ALIASES.get(label, label)))
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
            [list(geometry), list(ons.values())],
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
        if 2019 in days and defaults.get(2019) != 4953:
            raise ValueError(f"{defaults.get(2019)} default result sets for 2019, not 4,953")
        return cs.write()
