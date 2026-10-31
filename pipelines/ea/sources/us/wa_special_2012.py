import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wa_special_2012"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Washington Secretary of State"
TITLE = "November 6, 2012 general election results: Congressional District 1, one-month short term"
URL = "https://results.vote.wa.gov/results/20121106/Congressional-District-1-One-Month-Short-Term-US-Representative_ByCounty.html"
BLOCK = re.compile(
    r"\|(King|Kitsap|Snohomish|Skagit|Whatcom)\|((?:[^|]+\|\(Prefers [^|]+\)\|[\d,]+\|[\d.]+%\|)+)Total Votes\|([\d,]+)\|"
)
ROW = re.compile(r"([^|]+)\|\(Prefers [^|]+\)\|([\d,]+)\|")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def parse() -> None:
    text = re.sub(
        r"<script.*?</script>|<style.*?</style>",
        "",
        raw.cached(SOURCE, URL).path.read_text(errors="ignore"),
        flags=re.DOTALL,
    )
    text = re.sub(r"\s+", " ", re.sub(r"\|+", "|", re.sub(r"<[^>]+>", "|", text)))
    text = text[text.index("Results Summary") :]
    with connect() as conn:
        fips = {
            name.lower(): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '53%%'"""
            ).fetchall()
        }
    rows = []
    for county, body, total in BLOCK.findall(text):
        found = [(name.strip(), int(votes.replace(",", ""))) for name, votes in ROW.findall(body)]
        if sum(votes for _, votes in found) != int(total.replace(",", "")):
            raise ValueError(f"{county}: candidates sum to {sum(v for _, v in found)}, printed {total}")
        rows.extend((date(2012, 11, 6), fips[county.lower()], name, votes) for name, votes in found)
    frame = pl.DataFrame(rows, schema=["day", "code", "candidate", "votes"], orient="row")
    print(frame)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        year=pl.lit(2012, pl.Int32),
        chamber=pl.lit("house"),
        district=pl.lit(1, pl.Int16),
        special=pl.lit(True),
        state=pl.lit("WA"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("WA"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's 2012 special general election for U.S. House District 1 (one-month short term) by county",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=URL,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WA": source}, strict=True, extend=True)
        return cs.write()
