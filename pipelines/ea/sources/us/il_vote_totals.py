import csv
import io
import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, state_primaries
from ea.store import DATA, connect

SOURCE = "us.il_vote_totals"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
CACHE = state_primaries.SOURCE
FEDERAL = re.compile(r"(?:(\d+)(?:ST|ND|RD|TH) CONGRESS|(UNITED STATES SENATOR)|(PRESIDENT))")
GENERALS = {1998: (date(1998, 11, 3), "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZbxtiJ%2fosJgrbL31aBh9d%2f%2bNJIzsDGBT%2bfoloSjs%2f6ALb494jITMllVoS7%2btZRrxqEsxCBlttI4l9csZTGqYG0g%3d")}
FILES = [
    *((state_primaries.IL_DAYS[year], state_primaries.IL_FILES[year][0], False) for year in state_primaries.IL_FILES if year <= 2014),
    *((day, token, True) for day, token in GENERALS.values()),
]
PUBLISHER = "Illinois State Board of Elections"
TITLE = "Election vote totals"
PARTIES = {"DEM": "democratic", "REP": "republican"}


def fetch() -> None:
    for _, token in GENERALS.values():
        raw.fetch(CACHE, state_primaries.IL_DOWNLOAD.format(token), suffix=".txt")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '17%%'"""
            ).fetchall()
        }
    rows: list[tuple[object, str, int | None, str, str, int]] = []
    for day, token, general in FILES:
        text = raw.cached(CACHE, state_primaries.IL_DOWNLOAD.format(token)).path.read_text(encoding="latin-1")
        table = list(csv.reader(io.StringIO(text), delimiter="\t" if "\t" in text.split("\n", 1)[0] else ","))
        heads = [cell.strip() for cell in table[0]]
        for row in table[1:]:
            if not any(row):
                continue
            line = dict(zip(heads, [cell.strip() for cell in row], strict=False))
            found = FEDERAL.fullmatch(line["OfficeName"])
            party = line["PartyAbbrev"] if general else PARTIES.get(line["PartyAbbrev"])
            if found is None or party is None:
                continue
            kind = "house" if found[1] else "senate" if found[2] else "president"
            name = re.sub(r"\s+", " ", f"{line['CanFirstName']} {line['CanLastName']}").strip().upper()
            rows.append(
                (
                    day,
                    kind if general else f"{kind}:{party}",
                    int(found[1]) if found[1] else None,
                    name,
                    fips[re.sub(r"[^a-z]", "", line["County"].lower())],
                    int(line["Votes"] or 0),
                )
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .group_by("day", "chamber", "district", "candidate", "code")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("IL"),
            measure=pl.lit(1, dtype=pl.Int16),
            writein=pl.col("candidate").str.contains("WRITE"),
            kind=pl.lit("county"),
            source=pl.lit("IL"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois's 2000–2014 primaries and 1998 general House and Senate results by county",
            polity="US-IL",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.elections.il.gov/ElectionOperations/DownloadVoteTotals.aspx",
            license="Public record",
        )
        for _, token, _ in FILES:
            cs.artifact(raw.cached(CACHE, state_primaries.IL_DOWNLOAD.format(token)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"IL": source}, strict=True)
        return cs.write()
