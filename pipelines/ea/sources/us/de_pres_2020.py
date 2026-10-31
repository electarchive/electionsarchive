import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.de_pres_2020"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://elections.delaware.gov/reports/PP2020.html"
DAY = date(2020, 7, 7)
PUBLISHER = "Delaware Department of Elections"
TITLE = "Official results of the July 7, 2020 presidential primary election"
COUNTIES = {"New Castle": "10003", "Kent": "10001", "Sussex": "10005"}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def parse() -> None:
    text = raw.cached(SOURCE, URL).path.read_text()
    block = text[text.index('id="bycounty"') : text.index('id="bycountyw"')]
    rows = []
    for party, table in re.findall(r'<h4 class="President (\w+)">.*?<tbody>(.*?)</tbody>', block, re.DOTALL):
        for line in re.findall(r"<tr>(.*?)</tr>", table, re.DOTALL):
            cells = [
                html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                for cell in re.findall(r"<td>(.*?)</td>", line, re.DOTALL)
            ]
            votes = [int(cell.replace(",", "")) for cell in cells[2:6]]
            if sum(votes[:3]) != votes[3]:
                raise ValueError(f"{cells[0]}: counties sum to {sum(votes[:3])}, state {votes[3]}")
            rows.extend(
                (party.lower(), cells[0].replace('"', ""), code, count)
                for code, count in zip(COUNTIES.values(), votes[:3], strict=True)
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["party", "candidate", "code", "votes"], orient="row").select(
        year=pl.lit(2020, pl.Int32),
        chamber=pl.concat_str(pl.lit("president:"), "party"),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("DE"),
        district=pl.lit(None, pl.Int16),
        code="code",
        candidate=pl.col("candidate").str.to_uppercase(),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        votes=pl.col("votes").cast(pl.Int64),
        kind=pl.lit("county"),
        source=pl.lit("DE"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Delaware's July 7, 2020 Democratic and Republican presidential primaries by county from the Department of Elections",
            polity="US-DE",
        )
        source = cs.source(PUBLISHER, TITLE)
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
        medsl.attach(cs, conn, frame, {"DE": source}, strict=True)
        return cs.write()
