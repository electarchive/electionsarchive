import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.md_gov_2002"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Maryland Secretary of State"
TITLE = "Official 2002 general election results, governor"
URL = "https://elections.maryland.gov/elections/archive/2002/results/g_governor.html"
TOTALS = {"TOWNSEND": 813422, "EHRLICH": 879592, "LANCASTER": 11546}
CANDIDATES = ["TOWNSEND", "EHRLICH", "LANCASTER"]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '24%%' and u.code <> '24510'"""
            ).fetchall()
        }
    counties["baltimorecity"] = "24510"
    counties["stmarys"] = counties["saintmarys"] if "saintmarys" in counties else counties["stmarys"]
    tallies: dict[tuple[str, str], int] = {}
    tables = 0
    for row in re.findall(
        r"<tr[^>]*>(.*?)</tr>", raw.cached(SOURCE, URL).path.read_text(errors="replace"), re.DOTALL | re.IGNORECASE
    ):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
        ]
        if cells[0] == "Totals":
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
        day=pl.lit(date(2002, 11, 5)),
        chamber=pl.lit("governor"),
        year=pl.lit(2002, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("MD"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("MD"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's 2002 governor general by county from the Secretary of State's official "
            "election returns",
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
                url="https://elections.maryland.gov/elections/archive/2002/results/",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MD": source}, strict=True, extend=True)
        return cs.write()
