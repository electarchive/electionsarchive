import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.in_leg_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Indiana Secretary of State"
TITLE = "Indiana General Elections 2000: U.S. Representative, State Representative, and State Senator by district"
DAY = date(2000, 11, 7)
URL = "https://web.archive.org/web/20021122id_/http://www.state.in.us/serv/sos_ge2000v?act=district&district={:03}&office={}"
LANDING = "http://www.state.in.us/serv/sos_ge2000v"
DISTRICTS = {
    "us_house": ("US+Representative", range(1, 11)),
    "house": ("State+Representative", range(1, 101)),
    "senate": (
        "State+Senator",
        (2, 3, 5, 7, 8, 9, 10, 12, 13, 16, 18, 20, 24, 28, 30, 32, 33, 34, 35, 36, 37, 40, 42, 44, 50),
    ),
}
URLS = {
    (chamber, district): URL.format(district, office)
    for chamber, (office, districts) in DISTRICTS.items()
    for district in districts
}


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(SOURCE, url, suffix=".html")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '18%%'"""
            ).fetchall()
        }
    rows = []
    for (chamber, district), url in URLS.items():
        text = raw.cached(SOURCE, url).path.read_text(encoding="latin-1")
        table = re.search(r"(?is)<th[^>]*>\s*<font[^>]*>COUNTY</font>.*?</table>", text)
        if table is None:
            print(f"{chamber} {district}: no table, skipped")
            continue
        cells = [
            [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip() for cell in row]
            for row in (
                re.findall(r"(?is)<t[hd][^>]*>(.*?)(?=<t[hd][\s>]|</tr>|<tr[\s>]|$)", block)
                for block in re.split(r"(?i)<tr[\s>]", table[0])
            )
            if row
        ]
        names = [re.sub(r"\s*\([^()]*\)$", "", name) for name in cells[0][1:-2]]
        totals: list[int] = []
        parts: list[tuple[str, list[int]]] = []
        for row in cells[1:]:
            if len(row) != len(names) + 3:
                continue
            votes = [int(cell) for cell in row[1 : len(names) + 1]]
            if row[0] == "TOTAL:":
                totals = votes
            else:
                parts.append((counties[re.sub(r"[^a-z]", "", row[0].lower())], votes))
        sums = [sum(votes[k] for _, votes in parts) for k in range(len(names))]
        if sums != (totals or (sums if len(parts) == 1 else [])):
            print(f"{chamber} {district}: {names} counties sum to {sums}, printed {totals}, skipped")
            continue
        rows.extend(
            ("house", district, code, name, votes[k])
            if chamber == "us_house"
            else (f"{chamber}:{district}:g", None, code, name, votes[k])
            for code, votes in parts
            for k, name in enumerate(names)
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows,
        schema={
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    print(
        frame.group_by(pl.col("chamber").str.split(":").list.first().alias("office")).agg(
            pl.concat_str("chamber", "district", ignore_nulls=True).n_unique()
        )
    )
    frame.with_columns(
        candidate=pl.col("candidate")
        .str.to_uppercase()
        .str.replace_all("-", "")
        .str.replace(r",?\s+(JR|SR|II|III|IV)\.?$", ""),
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("IN"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("IN"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indiana's 2000 U.S. House, State House, and State Senate generals by county (Secretary of State results pages, via the Wayback Machine)",
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
        for url in URLS.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"IN": source}, strict=True)
        return cs.write()
