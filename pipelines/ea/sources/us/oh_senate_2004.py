import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.oh_senate_2004"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Ohio Secretary of State"
TITLE = "Official 2004 general election results, U.S. Senator"
URL = "https://web.archive.org/web/20120706163312id_/http://www.sos.state.oh.us/sos/elections/Research/electResultsMain/2004ElectionsResults/04-1102USSenator.aspx"
TOTALS = {"FINGERHUT": 1961249, "MEYERS": 296, "VOINOVICH": 3464651}
CANDIDATES = list(TOTALS)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%%'"""
            ).fetchall()
        }
    tallies: dict[tuple[str, str], int] = {}
    tables = 0
    for row in re.findall(
        r"<tr[^>]*>(.*?)</tr>", raw.cached(SOURCE, URL).path.read_text(errors="replace"), re.DOTALL | re.IGNORECASE
    ):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
        ]
        if cells[0] == "Total":
            tables += 1
        key = re.sub(r"[^a-z]", "", cells[0].lower())
        if key not in counties or tables > 0:
            continue
        for name, votes in zip(CANDIDATES, cells[1:], strict=True):
            tallies[(counties[key], name)] = int(votes.replace(",", ""))
    frame = pl.DataFrame(
        [(code, name, votes) for (code, name), votes in tallies.items()],
        schema=["code", "candidate", "votes"],
        orient="row",
    )
    print(frame["code"].n_unique(), frame.group_by("candidate").agg(pl.col("votes").sum()))
    sums = dict(frame.group_by("candidate").agg(pl.col("votes").sum()).iter_rows())
    for name, expected in TOTALS.items():
        if sums[name] != expected:
            raise ValueError(f"{name}: counties add up to {sums[name]}, not {expected}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        day=pl.lit(date(2004, 11, 2)),
        chamber=pl.lit("senate"),
        year=pl.lit(2004, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("OH"),
        writein=pl.col("candidate") == "MEYERS",
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("OH"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's 2004 U.S. Senate general by county from the Secretary of State's official election returns",
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
                url="https://www.sos.state.oh.us/elections/election-results-and-data/",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"OH": source}, strict=True, extend=True)
        return cs.write()
