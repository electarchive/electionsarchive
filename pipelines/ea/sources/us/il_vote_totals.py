import csv
import io
import re
import unicodedata
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, state_primaries
from ea.store import DATA, connect

SOURCE = "us.il_vote_totals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CACHE = state_primaries.SOURCE
YEARS = [year for year in state_primaries.IL_FILES if year <= 2014]
FEDERAL = re.compile(r"(?:(\d+)(?:ST|ND|RD|TH) CONGRESS|(UNITED STATES SENATOR)|(PRESIDENT))")
PUBLISHER = "Illinois State Board of Elections"
TITLE = "Election vote totals"
PARTIES = {"DEM": "democratic", "REP": "republican"}


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
    for year in YEARS:
        text = raw.cached(CACHE, state_primaries.IL_DOWNLOAD.format(state_primaries.IL_FILES[year][0])).path.read_text(
            encoding="latin-1"
        )
        table = list(csv.reader(io.StringIO(text), delimiter="\t" if "\t" in text.split("\n", 1)[0] else ","))
        heads = [cell.strip() for cell in table[0]]
        for row in table[1:]:
            if not any(row):
                continue
            line = dict(zip(heads, [cell.strip() for cell in row], strict=False))
            found = FEDERAL.fullmatch(line["OfficeName"])
            party = PARTIES.get(line["PartyAbbrev"])
            if found is None or party is None:
                continue
            kind = "house" if found[1] else "senate" if found[2] else "president"
            name = re.sub(r"\s+", " ", f"{line['CanFirstName']} {line['CanLastName']}").strip().upper()
            rows.append(
                (
                    state_primaries.IL_DAYS[year],
                    f"{kind}:{party}",
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
            summary="Illinois's 2000–2014 House, Senate, and presidential primaries by county",
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
        for year in YEARS:
            cs.artifact(
                raw.cached(CACHE, state_primaries.IL_DOWNLOAD.format(state_primaries.IL_FILES[year][0])), source
            )
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"IL": source}, strict=True)
        return cs.write()
