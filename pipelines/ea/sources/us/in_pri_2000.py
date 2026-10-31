import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.in_federal import CODES, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.in_pri_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "http://web.archive.org/web/2005id_/http://www.in.gov:80/sos/elections/results/pe2000v!action=County&county={}&party={}.html"
LANDING = "http://www.in.gov/sos/elections/results/"
TITLE = "Primary Elections 2000, results by county"
DAY = date(2000, 5, 2)
PARTIES = {1: "democratic", 2: "republican"}
OFFICES = {"US Representative": "house", "US Senator": "senate", "President": "president"}
CELL = re.compile(
    r"go to (office|district) ([^']+)'|<TD[^>]*align=\"center\">([^<]+)</TD>\s*<TD[^>]*align=\"right\">([\d,]+)"
)


def fetch() -> None:
    for county in range(1, 93):
        for party in PARTIES:
            raw.fetch(SOURCE, URL.format(county, party), suffix=".html")


def parse() -> None:
    rows = []
    for county in range(1, 93):
        for party, name in PARTIES.items():
            text = raw.cached(SOURCE, URL.format(county, party)).path.read_text(encoding="latin-1")
            code = CODES[re.sub(r"[^a-z]", "", re.search(r"<H2>(.+?) County</H2>", text)[1].lower())]
            office = district = None
            for found in CELL.finditer(text):
                if found[1] == "office":
                    office = OFFICES.get(found[2])
                elif found[1] == "district":
                    district = int(found[2]) if found[2].isdigit() else None
                elif office is not None:
                    rows.append(
                        (
                            f"{office}:{name}",
                            district,
                            code,
                            html.unescape(found[3]).strip().upper(),
                            int(found[4].replace(",", "")),
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("IN"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("IN"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indiana's U.S. House and Senate primaries of 2000 by county (Secretary of State results pages, via the Wayback Machine)",
            polity="US-IN",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for county in range(1, 93):
            for party in PARTIES:
                cs.artifact(raw.cached(SOURCE, URL.format(county, party)), source)
        medsl.attach(cs, conn, frame, {"IN": source}, strict=True)
        return cs.write()
