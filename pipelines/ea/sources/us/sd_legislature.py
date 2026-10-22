import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.sources.us.sd_sos import PUBLISHER, SITE, TITLE, table, url
from ea.store import DATA, connect

SOURCE = "us.sd_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LONG = {
    "2008/2008_general_election_legislature_county_totals.aspx": date(2008, 11, 4),
    "2010/2010_general_election_legislature_by_county.aspx": date(2010, 11, 2),
}
PDF = "http://web.archive.org/web/2016id_/https://sdsos.gov/elections-voting/assets/2012%20Assets/2012generalelectionlegislaturebycounty.pdf"
TITLE_LINE = re.compile(r"State (Senator|Representatives?) - 0*(\d+)([AB]?)")


def fetch() -> None:
    for path in LONG:
        raw.fetch(SOURCE, url(path), suffix=".html")
    raw.fetch(SOURCE, PDF, suffix=".pdf")


def chamber(office: str, district: str, part: str) -> str:
    return f"{'senate' if office == 'Senator' else 'house'}:{district}" + (f"-{part.lower()}" if part else "") + ":g"


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["SD"]
    rows = []
    for path, day in LONG.items():
        for row in table(path, SOURCE):
            if len(row) < 6 or not row[5].isdigit():
                continue
            race, county, _, district, candidate, votes = row[:6]
            found = re.fullmatch(r"0*(\d+)([AB]?)", district)
            rows.append(
                (
                    day,
                    chamber(race.split()[1], found[1], found[2]),
                    fips[re.sub(r"[^a-z]", "", county.lower())],
                    candidate.upper(),
                    int(votes),
                )
            )
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, PDF).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    blocks: list[dict] = []
    starts: list[int] = []
    for line in text.splitlines():
        titles = list(TITLE_LINE.finditer(line))
        if titles:
            starts = [found.start() for found in titles]
            blocks = [{"chamber": chamber(*found.groups()), "names": [], "rows": []} for found in titles]
            continue
        for block, start, end in zip(blocks, starts, [*starts[1:], None], strict=True):
            cells = re.split(r"\s{2,}", line[start:end].strip())
            if cells[0] == "County":
                block["names"] = cells[1:-1]
            elif cells[0] == "TOTAL" and block["names"]:
                sums = [sum(row[1][i] for row in block["rows"]) for i in range(len(block["names"]) + 1)]
                if sums != [int(cell) for cell in cells[1:]]:
                    print(f"2012 {block['chamber']}: counties sum to {sums}, printed {cells[1:]}, skipped")
                    continue
                rows.extend(
                    (date(2012, 11, 6), block["chamber"], code, name.upper(), values[i])
                    for code, values in block["rows"]
                    for i, name in enumerate(block["names"])
                )
            elif block["names"] and len(cells) == len(block["names"]) + 2 and all(cell.isdigit() for cell in cells[1:]):
                block["rows"].append((fips[re.sub(r"[^a-z]", "", cells[0].lower())], [int(cell) for cell in cells[1:]]))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"day": pl.Date, "chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    ).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("SD"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("SD"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Dakota's legislative generals by county, 2008-2012 (Secretary of State election history)",
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
                url=SITE,
                license="Public record",
            )
        for path in LONG:
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        cs.artifact(raw.cached(SOURCE, PDF), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"SD": source}, strict=True)
        return cs.write()
