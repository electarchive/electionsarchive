from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.me_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://www.maine.gov/sos/sites/maine.gov.sos/files/"
DOWNLOADS = Path.home() / "Downloads" / "me_federal"
PUBLISHER = "Maine Secretary of State"
TITLE = "Election results: primary tabulations by municipality"
COUNTIES = {
    "AND": "23001",
    "ARO": "23003",
    "CUM": "23005",
    "FRA": "23007",
    "HAN": "23009",
    "KEN": "23011",
    "KNO": "23013",
    "LIN": "23015",
    "OXF": "23017",
    "PEN": "23019",
    "PIS": "23021",
    "SAG": "23023",
    "SOM": "23025",
    "WAL": "23027",
    "WAS": "23029",
    "YOR": "23031",
}
D20, P20, D22 = date(2020, 3, 3), date(2020, 7, 14), date(2022, 6, 14)
FILES = [
    ("content/assets/demprim320.xlsx", "me20_demprim320.xlsx", D20, "president:democratic", None),
    ("content/assets/reppres320.xlsx", "me20_reppres320.xlsx", D20, "president:republican", None),
    ("content/assets/sendemnr.xlsx", "me20_sendemnr.xlsx", P20, "senate:democratic", None),
    ("content/assets/senrepnr.xlsx", "me20_senrepnr.xlsx", P20, "senate:republican", None),
    ("content/assets/cg1demnr.xlsx", "me20_cg1demnr.xlsx", P20, "house:democratic", 1),
    ("content/assets/cg1repnr.xlsx", "me20_cg1repnr.xlsx", P20, "house:republican", 1),
    ("content/assets/cg2demnr.xlsx", "me20_cg2demnr.xlsx", P20, "house:democratic", 2),
    ("inline-files/dist1reptocong622.xlsx", "me_dist1reptocong622.xlsx", D22, "house:democratic", 1),
    ("inline-files/dist1reptocongr622.xlsx", "me_dist1reptocongr622.xlsx", D22, "house:republican", 1),
    ("inline-files/dist2reptocong622.xlsx", "me_dist2reptocong622.xlsx", D22, "house:democratic", 2),
    ("inline-files/dist2reptocongr622.xlsx", "me_dist2reptocongr622.xlsx", D22, "house:republican", 2),
]
SKIP = {"", "BLANK", "TBC", "TOTAL BALLOTS CAST"}


def fetch() -> None:
    for path, name, *_ in FILES:
        try:
            raw.cached(SOURCE, SITE + path)
        except LookupError:
            raw.store(
                SOURCE,
                SITE + path,
                (DOWNLOADS / name).read_bytes(),
                suffix=".xlsx",
                retrieved_at=datetime.now(UTC),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def parse() -> None:
    frames = []
    for path, _, day, chamber, district in FILES:
        sheet = pl.read_excel(raw.cached(SOURCE, SITE + path).path, has_header=False, infer_schema_length=0)
        rows = sheet.rows()
        head = next(
            i
            for i, row in enumerate(rows[:4])
            if any(str(cell).strip().upper() in ("CTY", "COUNTY") for cell in row[:3])
        )
        county_col = next(i for i, cell in enumerate(rows[head][:3]) if str(cell).strip().upper() in ("CTY", "COUNTY"))
        names = {
            i: " ".join(str(cell).split()).upper()
            for i, cell in enumerate(rows[head - 1 if head > 0 else head])
            if i > county_col + 1 and cell is not None
        }
        for row in rows[head + 1 :]:
            town = " ".join(str(row[county_col + 1] or "").split()).upper()
            county = str(row[county_col] or "").strip().upper()
            if county in COUNTIES:
                code, kind = COUNTIES[county], "county"
            elif "UOCAVA" in town and "TOTAL" not in town:
                code, kind = "ME:uocava", "aggregate"
            else:
                continue
            for i, name in names.items():
                if name in SKIP or name.startswith("TOTAL") or row[i] in (None, ""):
                    continue
                frames.append(
                    {
                        "year": day.year,
                        "chamber": chamber,
                        "day": day,
                        "special": False,
                        "state": "ME",
                        "district": district,
                        "code": code,
                        "candidate": name,
                        "votes": int(float(row[i])),
                        "kind": kind,
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(frames, schema_overrides={"district": pl.Int16, "year": pl.Int32})
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "kind", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(source=pl.lit("ME"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's 2020 and 2022 president, U.S. Senate, and House primaries by county",
            polity="US-ME",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.maine.gov/sos/elections-voting/election-results-data/previous-election-results",
            license="Public record",
        )
        for path, *_ in FILES:
            cs.artifact(raw.cached(SOURCE, SITE + path), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"ME": source}, strict=True)
        return cs.write()
