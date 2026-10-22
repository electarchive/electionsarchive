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
PDFS = {
    "http://web.archive.org/web/2016id_/https://sdsos.gov/elections-voting/assets/2012%20Assets/2012generalelectionlegislaturebycounty.pdf": date(2012, 11, 6),
    SITE + "2014/legislativeracesbycounty%20.pdf": date(2014, 11, 4),
}
TITLE_LINE = re.compile(r"State (Senator|Representatives?) - (?:District )?0*(\d+)([AB]?)")


def fetch() -> None:
    for path in LONG:
        raw.fetch(SOURCE, url(path), suffix=".html")
    for pdf in PDFS:
        raw.fetch(SOURCE, pdf, suffix=".pdf")


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
    for pdf, day in PDFS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, pdf).path), "-"], capture_output=True, check=True, text=True
        ).stdout
        starts = [0, 0]
        blocks: list[dict | None] = [None, None]
        for line in text.splitlines():
            titles = list(TITLE_LINE.finditer(line))
            if any(found.start() > 20 for found in titles):
                starts[1] = next(found.start() for found in titles if found.start() > 20)
            for column in (0, 1):
                piece = line[starts[column] : starts[1] if column == 0 and starts[1] else None]
                if found := TITLE_LINE.search(piece):
                    blocks[column] = {"chamber": chamber(*found.groups()), "header": [], "rows": []}
                    continue
                block = blocks[column]
                row = re.match(r"\s*(\S.*?)\s{2,}(\d+(?:\s+\d+)*)\s*$", piece)
                if block is None or not piece.strip():
                    continue
                if row is None:
                    block["header"].append(piece)
                elif row[1] != "TOTAL":
                    block["rows"].append((fips[re.sub(r"[^a-z]", "", row[1].lower())], [int(cell) for cell in row[2].split()]))
                else:
                    blocks[column] = None
                    printed = [int(cell) for cell in row[2].split()]
                    places = [match.start() for match in re.finditer(r"\d+", piece)]
                    words: dict[int, list[str]] = {}
                    for text_line in block["header"]:
                        for word in re.finditer(r"\S+", text_line):
                            if word[0] != "County" and (column_at := max((i for i, x in enumerate(places) if x <= word.start() + 1), default=None)) is not None:
                                words.setdefault(column_at, []).append(word[0])
                    names = [" ".join(words.get(i, [])) for i in range(len(printed) - 1)]
                    sums = [sum(values[i] for _, values in block["rows"]) for i in range(len(printed))]
                    if sums != printed or any(len(values) != len(printed) for _, values in block["rows"]) or not all(names):
                        print(f"{day.year} {block['chamber']}: {names} counties sum to {sums}, printed {printed}, skipped")
                        continue
                    rows.extend(
                        (day, block["chamber"], code, name.upper(), values[i])
                        for code, values in block["rows"]
                        for i, name in enumerate(names)
                    )
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
            summary="South Dakota's legislative generals by county, 2008-2014 (Secretary of State election history)",
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
        for pdf in PDFS:
            cs.artifact(raw.cached(SOURCE, pdf), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"SD": source}, strict=True)
        return cs.write()
