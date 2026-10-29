import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wa_pres_pri"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGE = "https://results.vote.wa.gov/results/20160524/president-{party}-party_bycounty.html"
DAY = date(2016, 5, 24)
PARTIES = ("democratic", "republican")
PUBLISHER = "Washington Secretary of State"
TITLE = "Election results: 2016 presidential primary"


def fetch() -> None:
    for party in PARTIES:
        raw.fetch(SOURCE, PAGE.format(party=party), suffix=".html")


def parse() -> None:
    with connect() as conn:
        counties = {
            name.removesuffix(", WA").removesuffix(" County").lower(): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '53%%' and u.valid @> '2016-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for party in PARTIES:
        text = raw.cached(SOURCE, PAGE.format(party=party)).path.read_text(errors="replace")
        table = re.findall(r"<table.*?</table>", text, re.S)[0]
        county = None
        for line in re.findall(r"<tr.*?</tr>", table, re.S):
            cells = [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
                for cell in re.findall(r"<t[hd].*?</t[hd]>", line, re.S)
            ]
            if len(cells) == 1 and cells[0].lower() in counties:
                county = counties[cells[0].lower()]
            elif len(cells) == 3 and county is not None and not cells[0].startswith("Total Votes"):
                rows.append((party, county, cells[0], int(cells[1].replace(",", ""))))
    frame = pl.DataFrame(
        rows, schema={"party": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    )
    if frame.select("party", "code", "candidate").is_duplicated().any():
        raise ValueError("a county appears twice for a candidate")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("party").agg(pl.col("code").n_unique(), pl.col("candidate").n_unique(), pl.col("votes").sum()).rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's 2016 Democratic and Republican presidential primaries by county from the Secretary of State's results site",
            polity="US-WA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.vote.wa.gov/results/20160524/",
            license="Public record",
        )
        for party in PARTIES:
            cs.artifact(raw.cached(SOURCE, PAGE.format(party=party)), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.lit(DAY.year, pl.Int32),
                chamber=pl.lit("president:") + pl.col("party"),
                day=pl.lit(DAY),
                special=pl.lit(False),
                state=pl.lit("WA"),
                district=pl.lit(None, pl.Int16),
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("candidate").str.contains("(?i)write"),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("WA"),
            ),
            {"WA": source},
            strict=True,
        )
        return cs.write()
