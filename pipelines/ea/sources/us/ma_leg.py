import io
import re
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl
from ea.store import DATA, connect

SOURCE = "us.ma_leg"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
OFFICES = {8: "house", 9: "senate"}
YEARS = (*range(1990, 2000, 2), 2002, 2004, 2006, 2010, 2012, 2016)
DROPPED = {"", "ALL OTHERS", "BLANKS", "TOTAL VOTES CAST"}


def listing(office: int, year: int) -> str:
    return f"{elstats.MASSACHUSETTS}search/year_from:{year}/year_to:{year}/office_id:{office}/stage:General"


def download(election: str) -> str:
    return f"{elstats.MASSACHUSETTS}download/{election}/precincts_include:0/"


def fetch() -> None:
    raw.fetch(SOURCE, elstats.GAZETTEER, suffix=".zip")
    for office in OFFICES:
        for year in YEARS:
            page = raw.fetch(SOURCE, listing(office, year), suffix=".html").path.read_text()
            for election, *_ in elstats.MA_ROW.findall(page):
                raw.fetch(SOURCE, download(election), suffix=".csv")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False)
    towns: dict[str, str] = {}
    for geoid, name in gazetteer.filter(pl.col("USPS") == "MA").select("GEOID", "NAME").iter_rows():
        towns.setdefault(elstats.plain(name), geoid[:5])
        towns.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
    with connect() as conn:
        contests = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.contest'").fetchall()
        }
    frames = []
    unmatched: set[str] = set()
    for office, chamber in OFFICES.items():
        for year in YEARS:
            page = raw.cached(SOURCE, listing(office, year)).path.read_text()
            for election, _, district, stage in elstats.MA_ROW.findall(page):
                if "Special" in stage:
                    continue
                ordinal = re.match(r"(\d+)(?:st|nd|rd|th) (.+)$", district.strip())
                name = re.sub(r"\band\b", "", ordinal[2] if ordinal else district)
                slug = f"{elstats.plain(name)}-{ordinal[1]}" if ordinal else elstats.plain(name)
                day = date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)
                key = f"{chamber}:{slug}:g"
                if f"{day}:MA:{key}" not in contests:
                    unmatched.add(f"{day}:{key}")
                    continue
                table = pl.read_csv(
                    raw.cached(SOURCE, download(election)).path, infer_schema=False, skip_rows_after_header=1
                )
                table = table.rename({table.columns[0]: "City/Town"}).filter(
                    pl.col("City/Town").is_not_null() & (pl.col("City/Town") != "TOTALS")
                )
                table = table.with_columns(
                    pl.col("City/Town")
                    .str.replace(r"^N\. ", "North ")
                    .str.replace(r"^S\. ", "South ")
                    .str.replace(r"^E\. ", "East ")
                    .str.replace(r"^W\. ", "West ")
                )
                long = table.unpivot(
                    [column for column in table.columns[3:] if column.upper().strip() not in DROPPED],
                    index="City/Town",
                    variable_name="candidate",
                ).select(
                    pl.lit(year, pl.Int32).alias("year"),
                    pl.lit(key).alias("chamber"),
                    pl.lit(day).alias("day"),
                    pl.col("City/Town")
                    .map_elements(elstats.plain, return_dtype=pl.String)
                    .replace_strict(towns, default=None)
                    .alias("code"),
                    pl.col("candidate").str.to_uppercase().str.strip_chars().alias("candidate"),
                    pl.col("value").str.replace_all(",", "").cast(pl.Int64, strict=False).alias("votes"),
                )
                frames.append(long)
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
    print(
        frame.filter(pl.col("code").is_null()).height, "rows without a county,", len(unmatched), "districts unmatched"
    )
    print(sorted(unmatched)[:12])
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.filter(pl.col("code").is_not_null())
        .group_by("year", "chamber", "day", "code", "candidate")
        .agg(pl.col("votes").sum())
        .with_columns(
            district=pl.lit(None, pl.Int16),
            special=pl.lit(False),
            state=pl.lit("MA"),
            measure=pl.lit(1, dtype=pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("county"),
            source=pl.lit("MA"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Massachusetts's 1990–2016 State Representative and State Senate generals by county",
            polity="US-MA",
        )
        source = cs.source(elstats.MA_PUBLISHER, "Massachusetts election statistics")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=elstats.MA_PUBLISHER,
            title="Massachusetts election statistics",
            url=elstats.MASSACHUSETTS,
            license="Public record",
        )
        for office in OFFICES:
            for year in YEARS:
                cs.artifact(raw.cached(SOURCE, listing(office, year)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MA": source}, strict=True)
        return cs.write()
