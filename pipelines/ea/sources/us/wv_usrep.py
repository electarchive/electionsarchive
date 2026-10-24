import difflib
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.wv_usrep"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "West Virginia Legislature"
TITLE = "West Virginia Blue Book, General Election Returns"
LANDING = "https://www.wvlegislature.gov/legisdocs/publications/bluebook/"
URL = "https://www.wvlegislature.gov/legisdocs/publications/bluebook/WVS_Bluebook_{}.pdf"
EDITIONS = {
    1993: date(1992, 11, 3),
    1995: date(1994, 11, 8),
    1997: date(1996, 11, 5),
    1999: date(1998, 11, 3),
}
NAMELESS = {(1997, 3): "Wyoming"}
METHOD = (
    "County totals are from the West Virginia Blue Book's general election returns, read from the scan's text layer, "
    "one table per congressional district. Each candidate's county sum equals the district total printed in the book."
)


def fetch() -> None:
    for edition in EDITIONS:
        url = URL.format(edition)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sL", "-m", "1500", "-A", "Mozilla/5.0", url], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url, body, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
    rows = []
    for edition, day in EDITIONS.items():
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, URL.format(edition)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        start = next(
            index
            for index, line in enumerate(lines)
            if re.search(r"U\.\s?S\.\s?HOUSE OF REPRESENTATIVES", line)
            and any("COUNTY" in text for text in lines[index : index + 8])
        )
        blocks: dict[int, list[str]] = {}
        district = 0
        for line in lines[start : start + 400]:
            heading = re.search(r"(First|Second|Third|Fourth) Congressional District", line)
            if heading:
                district = ["First", "Second", "Third", "Fourth"].index(heading[1]) + 1
                blocks[district] = []
            elif district and re.match(r"\s*(GOVERNOR|STATE SENATE|U\.\s?S\. SENATE|JUSTICE)", line):
                break
            elif district:
                blocks[district].append(line)
        for district, block in blocks.items():
            table: dict[str, list[int]] = {}
            total: list[int] = []
            nameless = []
            for line in block:
                bare = re.fullmatch(r"\s*(\d[\d.,]*(?:\s+\d[\d.,]*)*)\s*", line)
                if bare:
                    nameless.append([int(re.sub(r"[.,]", "", token)) for token in bare[1].split()])
                match = re.match(r"^\s*([A-Z][A-Za-z. ]*?)[\s.·•,~_-]*(\d[\d.,]*(?:\s+\d[\d.,]*)*)\s*$", line)
                if match and "BLUE BOOK" not in line and "RETURNS" not in line:
                    values = [int(re.sub(r"[.,]", "", token)) for token in match[2].split()]
                    if match[1].strip().upper().startswith("TOTAL"):
                        total = values
                    else:
                        key = re.sub(r"[^a-z]", "", match[1].lower())
                        close = [key] if key in counties else difflib.get_close_matches(key, counties, 1, 0.75)
                        if not close:
                            raise ValueError(f"{edition} district {district}: unknown county {line!r}")
                        key = close[0]
                        table[key] = values
            if nameless:
                total = nameless.pop()
                for values in nameless:
                    table[NAMELESS[(edition, district)].lower()] = values
            sums = [sum(row[column] for row in table.values()) for column in range(len(total))]
            if sums != total or any(len(row) != len(total) for row in table.values()):
                raise ValueError(f"{edition} district {district}: columns sum to {sums}, printed {total}")
            for county, values in table.items():
                for column, votes in enumerate(values):
                    rows.append((str(day), district, counties[county], str(column), votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "district").agg(pl.col("county").n_unique()).sort("day", "district"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "West Virginia's U.S. House general elections of 1992 through 1998 by county from the Blue Book",
        PUBLISHER,
        TITLE,
        LANDING,
        "WV",
        "54",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL.format(edition)) for edition in EDITIONS],
        lambda key: METHOD,
    )
