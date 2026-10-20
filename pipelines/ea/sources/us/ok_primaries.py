import io
import re
import unicodedata
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ok_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://results.okelections.gov/OKER/?elecDate="
DAYS = [
    date(2020, 3, 3),
    date(2020, 6, 30),
    date(2020, 8, 25),
    date(2022, 6, 28),
    date(2022, 8, 23),
    date(2024, 3, 5),
    date(2024, 6, 18),
    date(2024, 8, 27),
]
PUBLISHER = "Oklahoma State Election Board"
TITLE = "Election results: county level results"
DOWNLOADS = Path.home() / "Downloads"


def url(day: date) -> str:
    return f"{BASE}{day:%Y%m%d}"


def fetch() -> None:
    for day in DAYS:
        try:
            raw.cached(SOURCE, url(day))
        except LookupError:
            raw.store(
                SOURCE,
                url(day),
                (DOWNLOADS / f"{day:%Y%m%d}_CountyResults_csv.zip").read_bytes(),
                suffix=".zip",
                retrieved_at=datetime.now(UTC),
                media_type="application/zip",
            )


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%%'"""
            ).fetchall()
        }
    frames = []
    for day in DAYS:
        with zipfile.ZipFile(raw.cached(SOURCE, url(day)).path) as archive:
            text = archive.read(archive.namelist()[0])
        description = pl.col("race_description").str.to_uppercase()
        frames.append(
            pl.read_csv(io.BytesIO(text), infer_schema=False, encoding="utf8-lossy", truncate_ragged_lines=True)
            .filter(
                description.str.contains(r"^FOR (UNITED STATES (SENATOR|REPRESENTATIVE)|PRESIDENT)"),
                pl.col("race_party").is_in(["DEM", "REP", "LIB"]),
            )
            .select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.when(description.str.contains("UNEXPIRED"))
                .then(pl.lit("senate:special"))
                .when(description.str.contains("SENATOR"))
                .then(pl.lit("senate"))
                .when(description.str.contains("REPRESENTATIVE"))
                .then(pl.lit("house"))
                .otherwise(pl.lit("president"))
                + pl.lit(":")
                + pl.col("race_party").replace_strict({"DEM": "democratic", "REP": "republican", "LIB": "libertarian"}),
                day=pl.lit(day),
                special=pl.lit(False),
                state=pl.lit("OK"),
                district=description.str.extract(r"DISTRICT (\d+)").cast(pl.Int16),
                code=pl.col("county")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                candidate=pl.col("cand_name").str.to_uppercase().str.strip_chars(),
                votes=pl.col("cand_tot_votes").cast(pl.Int64),
            )
        )
    frame = pl.concat(frames)
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
        .with_columns(kind=pl.lit("county"), source=pl.lit("OK"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's 2020, 2022, and 2024 president, U.S. Senate, and House primaries and runoffs by county",
            polity="US-OK",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.okelections.gov/OKER/",
            license="Public record",
        )
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, url(day)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"OK": source}, strict=True)
        return cs.write()
