import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.sd_leg_pri_2014"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "South Dakota Secretary of State"
TITLE = "2014 primary election results by county: state legislative races"
DAY = date(2014, 6, 3)
CAPTURES = [
    ("20140609024608", 104, "REP", 310),
    ("20140609034436", 1101, "REP", 300),
    ("20140609033116", 141, "REP", 310),
    ("20140609024612", 240, "REP", 300),
    ("20140609033122", 252, "REP", 310),
    ("20140609034441", 253, "REP", 310),
    ("20140609025345", 274, "REP", 310),
    ("20140609034446", 279, "DEM", 310),
    ("20140609034451", 295, "REP", 310),
    ("20140609034457", 353, "DEM", 310),
    ("20140609025350", 396, "DEM", 310),
    ("20140609025355", 866, "REP", 310),
    ("20140609034512", 889, "REP", 300),
    ("20140609034517", 962, "REP", 310),
    ("20140609024617", 397, "DEM", 300),
    ("20140609024622", 397, "REP", 300),
    ("20140609033126", 400, "REP", 310),
    ("20140609024627", 423, "REP", 310),
    ("20140707225039", 430, "DEM", 300),
    ("20140609024632", 574, "REP", 300),
    ("20140609033131", 625, "REP", 310),
    ("20140609034507", 707, "REP", 310),
]
CANDIDATE = re.compile(r"([^|]+)\|([\d,]+)\|[\d.]+%\|\d+\|\d+")


def url(capture: tuple[str, int, str, int]) -> str:
    stamp, rid, party, osn = capture
    return f"https://web.archive.org/web/{stamp}id_/http://electionresults.sd.gov:80/resultsCTY.aspx?type=LEG&rid={rid}&pty={party}&osn={osn}"


def fetch() -> None:
    for capture in CAPTURES:
        raw.fetch(SOURCE, url(capture), suffix=".html")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '46%%'"""
            ).fetchall()
        }
    rows = []
    for capture in CAPTURES:
        text = raw.cached(SOURCE, url(capture)).path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", text)
        text = re.sub(r"\s*\|[\s|]*", "|", html.unescape(re.sub(r"<[^>]+>", "|", text)))
        found = re.search(r"\|State (Senator|Representatives?) - District 0*(\d+)([AB]?)\|", text)
        seat = f"{found[2]}-{found[3].lower()}" if found[3] else found[2]
        chamber = f"{'senate' if found[1] == 'Senator' else 'house'}:{seat}:{capture[2][0].lower()}p"
        blocks = re.split(r"\|([A-Z][A-Za-z ]+?) County\|Precincts Fully Reporting", text[found.end() :])
        totals = {
            name.strip(): int(votes.replace(",", ""))
            for name, votes in CANDIDATE.findall(blocks[0].split("Recount %|", 1)[1])
        }
        parts = [
            (counties[re.sub(r"[^a-z]", "", county.lower())], name.strip(), int(votes.replace(",", "")))
            for county, block in zip(blocks[1::2], blocks[2::2], strict=True)
            for name, votes in CANDIDATE.findall(block.split("Recount %|", 1)[1])
        ]
        sums = {name: sum(votes for _, other, votes in parts if other == name) for name in totals}
        if sums != totals:
            print(f"{chamber}: counties sum to {sums}, printed {totals}, skipped")
            continue
        rows.extend((chamber, code, re.sub(r"\s+", " ", name), votes) for code, name, votes in parts)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows, schema={"chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    )
    print(frame.select(pl.col("chamber").n_unique(), pl.col("code").n_unique()))
    frame.with_columns(
        candidate=pl.col("candidate").str.to_uppercase().str.replace(r",?\s+(JR|SR|II|III|IV)\.?$", ""),
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("SD"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("SD"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Dakota's 2014 State House and Senate primaries by county from the Secretary of State's final election-night results (via the Wayback Machine)",
            polity="US-SD",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://electionresults.sd.gov/",
                license="Public record",
            )
        for capture in CAPTURES:
            cs.artifact(raw.cached(SOURCE, url(capture)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"SD": source}, strict=True)
        return cs.write()
