import csv
import html
import io
import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nc_gov"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Official general election results by county and precinct"
PAGE = "https://web.archive.org/web/2008id_/http://www.app.sboe.state.nc.us/NCSBE/Elec/Results/y2000elect/enrs_contest_by_county/gov.htm"
ZIPS = {
    date(2008, 11, 4): ("https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/2008_11_04/results_pct_20081104.zip", "GOVERNOR"),
    date(2012, 11, 6): ("https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/2012_11_06/results_pct_20121106.zip", "NC GOVERNOR"),
}
SURNAMES = {"Bev Perdue": "PERDUE", "Pat McCrory": "MCCRORY", "Michael C. Munger": "MUNGER"}


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".htm")
    for url, _ in ZIPS.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%%'"""
            ).fetchall()
        }
    rows = []
    table = []
    for row in re.findall(r"<TR[^>]*>(.*?)</TR>", raw.cached(SOURCE, PAGE).path.read_text(errors="replace"), re.S | re.I):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in re.findall(r"<TD[^>]*>(.*?)</TD>", row, re.S | re.I)]
        if cells := [re.sub(r"\s+", " ", cell) for cell in cells if cell]:
            table.append(cells)
    names = table[4]
    printed = [
        int(cell.replace(",", ""))
        for cell in next(cells for cells in table if cells[0] == "Contest Total:")[1:]
        if not cell.endswith("%")
    ]
    seen = set()
    for cells in table[5:]:
        key = re.sub(r"[^a-z]", "", cells[0].lower()).replace("tyrell", "tyrrell")
        if key not in counties:
            continue
        seen.add(key)
        votes = [int(cell.replace(",", "")) for cell in cells[1:] if not cell.endswith("%")]
        rows.extend(
            (date(2000, 11, 7), counties[key], name.split()[-1].upper(), count, False)
            for name, count in zip(names, votes, strict=True)
        )
    if len(seen) != 100:
        raise ValueError(f"2000: {len(seen)} counties")
    for index, name in enumerate(names):
        if sum(row[3] for row in rows if row[2] == name.split()[-1].upper()) != printed[index]:
            raise ValueError(f"2000 {name}: counties do not add up to {printed[index]}")
    for day, (url, contest) in ZIPS.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            reader = csv.reader(io.TextIOWrapper(archive.open(archive.namelist()[0]), encoding="latin-1"))
            next(reader)
            tallies: dict[tuple[str, str, bool], int] = {}
            for row in reader:
                if row[5] != contest:
                    continue
                writein = "write-in" in row[6].lower()
                name = "WRITE-INS" if writein else row[6].split()[-1].upper() if day.year == 2012 else SURNAMES[row[6]]
                key = (counties[re.sub(r"[^a-z]", "", row[0].lower())], name, writein)
                tallies[key] = tallies.get(key, 0) + int(row[-2])
        rows.extend((day, code, name, votes, writein) for (code, name, writein), votes in tallies.items())
    frame = pl.DataFrame(rows, schema=["day", "code", "candidate", "votes", "writein"], orient="row")
    with pl.Config(tbl_rows=40):
        print(frame.group_by("day", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("day", "votes"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        chamber=pl.lit("governor"),
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("NC"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("NC"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's 2000, 2008, and 2012 governor generals by county from the State Board of "
            "Elections' official results",
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
                url="https://www.ncsbe.gov/results-data",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, PAGE), source)
        for url, _ in ZIPS.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NC": source}, strict=True, extend=True)
        return cs.write()
