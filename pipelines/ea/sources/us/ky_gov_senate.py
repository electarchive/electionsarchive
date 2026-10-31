import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ky_gov_senate"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Kentucky State Board of Elections"
TITLE = "Official election night tally results by county"
BASE = "https://web.archive.org/web/2020id_/https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/2000-2009/"
PDF_2015 = "https://web.archive.org/web/2020id_/https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/2010-2019/2015/2015%20General%20Election%20Results.pdf"
FILES = {
    date(2002, 11, 5): ("2002/General%20Election/2002statebycounty.txt", "A03", "senate"),
    date(2004, 11, 2): ("2004/General%20Election/2004statebyCOUNTY.txt", "A03", "senate"),
    date(2007, 11, 6): ("2007/General%20Election/statewidebyCOUNTY.txt", "D05", "governor"),
    date(2008, 11, 4): (
        "2008/General%20Election/STATEwide%20by%20candidate%20by%20county%20gen%2008.txt",
        "A03",
        "senate",
    ),
}


def fetch() -> None:
    for path, _, _ in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".txt")
    raw.fetch(SOURCE, PDF_2015, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        kentucky = conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '21%%'"""
        ).fetchall()
        names = sorted((re.sub(r"^mc", "m!", re.sub(r"[^a-z]", "", text.lower())), code) for code, text in kentucky)
    rows = []
    for day, (path, wanted, chamber) in FILES.items():
        office = None
        columns: list[str] = []
        abbreviations: list[str] = []
        totals: dict[str, int] = {}
        year_rows = []
        for line in raw.cached(SOURCE, BASE + path).path.read_text(errors="replace").splitlines():
            if match := re.search(r"OFFICE: (\w\d\d)/", line):
                office = match[1]
                columns = []
                continue
            if "*" in line and not line[:30].strip():
                columns = re.findall(r"\*(\S+)", line)
                if office == wanted:
                    abbreviations.extend(columns)
                continue
            if office != wanted or not line.strip():
                continue
            split = re.match(r"\s*(.*?\S)\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$", line)
            if split is None:
                if re.search(r"KENTUCKY STATE BOARD|REPORT OF|TIME OF REPORT", line):
                    continue
                raise ValueError(f"{day}: unreadable line {line!r}")
            cells = split[2].split()
            candidate = re.sub(r"\s*&.*$", "", re.sub(r"\s+", " ", split[1])).upper()
            if len(cells) == 1 and len(columns) > 1:
                totals[candidate] = int(cells[0].replace(",", ""))
                continue
            if len(cells) != len(columns):
                raise ValueError(f"{day} {candidate}: {len(cells)} cells for {len(columns)} counties")
            for abbreviation, votes in zip(columns, cells, strict=True):
                year_rows.append((candidate, abbreviation, int(votes.replace(",", ""))))
        if len(abbreviations) != 120 or len(set(abbreviations)) != 120:
            raise ValueError(f"{day}: {len(abbreviations)} county columns")
        for index, abbreviation in enumerate(abbreviations):
            if not names[index][0].replace("m!", "mc", 1).startswith(abbreviation[:3].lower()):
                raise ValueError(f"{day}: {abbreviation} lines up with {names[index][0]}")
        mapped = {abbreviation: names[index][1] for index, abbreviation in enumerate(abbreviations)}
        for candidate, total in totals.items():
            summed = sum(votes for name, _, votes in year_rows if name == candidate)
            if summed != total:
                raise ValueError(f"{day} {candidate}: counties sum to {summed}, printed total {total}")
        rows.extend(
            (day, chamber, mapped[abbreviation], candidate, votes) for candidate, abbreviation, votes in year_rows
        )
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, PDF_2015).path), "-"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    surnames: list[str] = []
    counties = []
    for line in lines[: next(n for n, line in enumerate(lines) if "Total Votes" in line) + 1]:
        if not surnames and re.fullmatch(r"\s+[A-Z' ]+(?:\s{2,}[A-Z' ]+){4}\s*", line):
            surnames = re.split(r"\s{2,}", line.strip())
        elif row := re.fullmatch(r"\s+([A-Za-z.' ]+?)\s{2,}([\d,]+(?:\s+[\d,]+){4})\s*", line):
            cells = [int(cell.replace(",", "")) for cell in row[2].split()]
            if row[1] == "Total Votes":
                for column, surname in enumerate(surnames):
                    if sum(cells_[column] for _, cells_ in counties) != cells[column]:
                        raise ValueError(f"2015 {surname}: counties do not add up to {cells[column]}")
            else:
                counties.append((row[1], cells))
    if len(counties) != 120:
        raise ValueError(f"2015: {len(counties)} counties")
    codes = {re.sub(r"[^a-z]", "", text.lower()): code for code, text in kentucky}
    for name, cells in counties:
        rows.extend(
            (date(2015, 11, 3), "governor", codes[re.sub(r"[^a-z]", "", name.lower())], surname, votes)
            for surname, votes in zip(surnames, cells, strict=True)
        )
    frame = pl.DataFrame(rows, schema=["day", "chamber", "code", "candidate", "votes"], orient="row")
    with pl.Config(tbl_rows=80):
        print(frame.group_by("day", "chamber", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("day", "votes"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("KY"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("KY"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kentucky's 2002, 2004, and 2008 U.S. Senate and 2007 and 2015 governor generals by county from the State "
            "Board of Elections' official results",
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
                url="https://elect.ky.gov/results/",
                license="Public record",
            )
        for path, _, _ in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        cs.artifact(raw.cached(SOURCE, PDF_2015), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"KY": source}, strict=True, extend=True)
        return cs.write()
