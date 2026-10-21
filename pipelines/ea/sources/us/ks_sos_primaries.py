import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ks_sos_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://web.archive.org/web/{stamp}id_/http://www.kssos.org/elections/"
FILES = {
    (date(2000, 8, 1), "house"): ("20140113113855", "00elec/2000_primary_uscongress_official_results.xls"),
    (date(2002, 8, 6), "house"): ("20041011130211", "02elec/ELUSHSE02P.xls"),
    (date(2002, 8, 6), "senate"): ("20041011130211", "02elec/ELUSSEN02P.xls"),
    (date(2004, 8, 3), "house"): ("20041217021415", "04elec/ELUSHOU04P.xls"),
    (date(2004, 8, 3), "senate"): ("20041217002506", "04elec/ELUSSEN04P.xls"),
    (date(2006, 8, 1), "house"): ("20060906191042", "06elec/ELUSHOU06P.xls"),
    (date(2008, 8, 5), "house"): ("20100713114754", "08elec/2008_primary_us_house_official_results.xls"),
    (date(2008, 8, 5), "senate"): ("20100613050750", "08elec/2008_primary_us_senate_official_results.xls"),
    (date(2010, 8, 3), "house"): ("20101202033458", "10elec/2010_primary_us_house_official_results.xls"),
    (date(2010, 8, 3), "senate"): ("20101011005454", "10elec/2010_primary_us_senate_official_results.xls"),
    (date(2014, 8, 5), "senate"): ("20141014113038", "14elec/2014_primary_us_senate_official_results.xls"),
}
PUBLISHER = "Kansas Secretary of State"
TITLE = "Primary election official results by precinct"
LANDING = "https://sos.ks.gov/elections/election-results.html"


def url(key: tuple[date, str]) -> str:
    stamp, name = FILES[key]
    return BASE.format(stamp=stamp) + name


def fetch() -> None:
    for key in FILES:
        raw.fetch(SOURCE, url(key), suffix=".xls")


def long_rows(path: Path) -> pl.DataFrame:
    frame = pl.read_excel(path, sheet_id=1, infer_schema_length=0)
    if "DISTRICT" not in frame.columns:
        frame = frame.with_columns(DISTRICT=pl.lit(None, pl.String))
    return frame.select(
        county=pl.col("COUNTY"),
        party=pl.col("PARTY"),
        district=pl.col("DISTRICT"),
        candidate=pl.concat_str(["FIRST", "MIDDLE", "LAST", "SUFFIX"], separator=" ", ignore_nulls=True)
        .str.replace_all(r"\s+", " ")
        .str.strip_chars(),
        votes=pl.col("VOTES"),
    )


def wide_rows(path: Path) -> pl.DataFrame:
    grid = pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0).rows()
    out = []
    for row in grid[3:]:
        if row[1] is None or row[2] is not None or row[3] is not None:
            continue
        for i in range(4, len(row)):
            if row[i] is None or grid[0][i] is None:
                continue
            district = re.search(r"District (\d+)", grid[1][i]).group(1)
            if district == row[0]:
                out.append((row[1].removesuffix(" County"), grid[2][i], district, grid[0][i], row[i]))
    return pl.DataFrame(out, schema=["county", "party", "district", "candidate", "votes"], orient="row")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '20%%'"""
            ).fetchall()
        }
    frames = []
    for key in FILES:
        day, chamber = key
        path = raw.cached(SOURCE, url(key)).path
        rows = wide_rows(path) if day.year == 2000 else long_rows(path)
        frames.append(
            rows.select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.lit(chamber)
                + pl.lit(":")
                + pl.col("party").str.to_lowercase().str.replace(r"^democrat$", "democratic"),
                day=pl.lit(day),
                special=pl.lit(False),
                state=pl.lit("KS"),
                district=pl.col("district").cast(pl.Int16),
                code=pl.col("county")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                candidate=pl.col("candidate").str.to_uppercase().str.strip_chars(),
                votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
            )
        )
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['candidate'].head(5).to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source=pl.lit("KS"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas's House and Senate primaries by county, 2000-2014 (Secretary of State)",
            polity="US-KS",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license="Public record",
        )
        for key in FILES:
            cs.artifact(raw.cached(SOURCE, url(key)), source)
        medsl.attach(cs, conn, frame, {"KS": source}, strict=True)
        return cs.write()
