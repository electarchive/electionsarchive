import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import statewide_totals
from ea.sources.us.pa_dos_precincts import BASE, GENERALS
from ea.sources.us.pa_dos_returns import LANDING, PUBLISHER
from ea.store import DATA

SOURCE = "us.pa_dos_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = [1992, 2000, 2004, 2008, 2012]
OFFICES = {"ATT": ("attorney-general", "Attorney General"), "AUD": ("auditor-general", "Auditor General"), "TRE": ("treasurer", "Treasurer")}
PARTIES = {
    "DEM": "Democratic",
    "REP": "Republican",
    "LIB": "Libertarian",
    "GRN": "Green",
    "CST": "Constitution",
    "REF": "Reform",
}
SPELLINGS = {"DEPASQUALE": "DePasquale"}
NOTE = "Statewide total summed from the Department of State's precinct returns."


def url(year: int) -> str:
    return BASE + GENERALS[year][0]


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, url(year), suffix=".txt")


def spell(word: str) -> str:
    if word in SPELLINGS:
        return SPELLINGS[word]
    word = word.title()
    word = re.sub(r"^Mc([a-z])", lambda match: "Mc" + match.group(1).upper(), word)
    return word + "." if len(word) == 1 else word


def parse() -> None:
    frames = []
    for year in YEARS:
        frames.append(
            pl.read_parquet(raw.parsed("us.pa_dos_precincts") / f"{year}.parquet")
            .filter(pl.col("office").is_in(list(OFFICES)), ~pl.col("last").is_in(["WRITE-IN", "OTHER"]))
            .filter((pl.col("office") != "ATT") | (year == 1992))
            .group_by("office", "party", "first", "middle", "last", "suffix")
            .agg(pl.col("votes").sum())
            .with_columns(year=pl.lit(year))
        )
    frame = pl.concat(frames).with_columns(
        name=pl.concat_str("first", "middle", "last", "suffix", separator=" ", ignore_nulls=True)
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
        .map_elements(lambda text: " ".join(spell(word) for word in text.split()), return_dtype=pl.String)
    )
    lines = frame.select(
        source=pl.format("pa-dos-{}", pl.col("year")),
        state=pl.lit("PA"),
        date=pl.col("year").map_elements(lambda year: str(GENERALS[year][1]), return_dtype=pl.String),
        office=pl.col("office").replace_strict({code: slug for code, (slug, _) in OFFICES.items()}),
        title=pl.col("office").replace_strict({code: title for code, (_, title) in OFFICES.items()}),
        name=pl.col("name"),
        party=pl.col("party").replace_strict(PARTIES),
        votes=pl.col("votes"),
        winner=(pl.col("votes") == pl.col("votes").max().over("year", "office")).cast(pl.Int64),
        page=pl.lit(None, pl.Int64),
        stage=pl.lit("general"),
        scope=pl.lit(None, pl.String),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    lines.write_parquet(PARSED / "lines.parquet")
    print(lines.group_by("date", "office").agg(pl.col("votes").sum(), pl.col("name").len()).sort("date", "office"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    book = {
        f"pa-dos-{year}": {
            "publisher": PUBLISHER,
            "title": f"Precinct Election Returns {year}",
            "url": url(year),
            "landing": LANDING,
            "license": "Public record",
            "page_offset": 0,
            "kind": "official_body",
        }
        for year in YEARS
    }
    return statewide_totals.emit(
        SOURCE,
        VERSION,
        "Pennsylvania's attorney general, auditor general, and treasurer generals of 1992 and the auditor general and treasurer generals of 2000, 2004, 2008, and 2012 by state total from the Department of State's precinct returns",
        frame,
        book,
        NOTE,
    )
