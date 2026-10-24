import re
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.ga_usrep"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Georgia Secretary of State"
TITLE = "Official State of Georgia Tabulation by Counties, general election"
URL = "https://dlg.galileo.usg.edu/data/dlg/ggpd/pdfs/dlg_ggpd_y-ga-bs700-b-ps1-bs4-b{}-bfolio.pdf"
PAGES = DATA / "pages" / SOURCE
YEARS = range(1956, 1990, 2)
ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
}
OVERRIDES: dict[tuple[int, int, str, int], int] = {}
METHOD = (
    "County totals are from the Secretary of State's consolidated returns for U.S. Representative in the "
    "'Tabulation by Counties' for the general election, as digitized by the Digital Library of Georgia (machine "
    "text). Each candidate's county sum equals the district total printed in the table."
)


def election_day(year: int) -> date:
    first = date(year, 11, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7 + 1)


def fetch() -> None:
    for year in YEARS:
        url = URL.format(year)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            for attempt in range(5):
                try:
                    raw.fetch(SOURCE, url, suffix=".pdf")
                    break
                except httpx.HTTPError:
                    if attempt == 4:
                        raise
                    time.sleep(10 * (attempt + 1))
            time.sleep(1)


def page_text(year: int) -> str:
    pdf = raw.cached(SOURCE, URL.format(year)).path
    layer = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    if len(layer) > 20000:
        return layer
    pages = int(
        re.search(
            r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
        )[1]
    )
    folder = PAGES / str(year)
    folder.mkdir(parents=True, exist_ok=True)
    parts = []
    for page in range(1, pages + 1):
        target = folder / f"{page:02d}.txt"
        if not target.exists():
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-r",
                    "250",
                    "-gray",
                    "-png",
                    str(pdf),
                    str(folder / "tmp"),
                ],
                check=True,
            )
            image = next(folder.glob("tmp-*.png"))
            subprocess.run(
                ["tesseract", str(image), str(folder / f"{page:02d}"), "--psm", "6"], capture_output=True, check=True
            )
            image.unlink()
        parts.append(target.read_text(errors="replace"))
    return "\n".join(parts)


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '13%%'"""
            ).fetchall()
        }
    rows: list[tuple[str, int, str, int, int]] = []
    for year in YEARS:
        text = page_text(year)
        start = re.search(r"CONSOLIDATED\s+RETURNS\s+FOR\s+U\.?\s*S\.?\s+REPRESENTATIVE", text, re.IGNORECASE)
        if start is None:
            print(year, "no consolidated returns")
            continue
        body = text[start.start() :]
        heads = list(re.finditer(r"(\w+)\s+Congres+ional\s+District", body, re.IGNORECASE))
        for index, head in enumerate(heads):
            district = ORDINALS.get(head[1].lower())
            if district is None:
                continue
            block = body[head.end() : heads[index + 1].start() if index + 1 < len(heads) else len(body)]
            total_at = re.search(r"TOTAL", block)
            if total_at is None:
                print(year, district, "no total")
                continue
            tail = block[total_at.start() :]
            write = re.search(r"WRITE", tail, re.IGNORECASE)
            tail = re.sub(r"(\d)[,.]\s*(\d{3})(?!\d)", r"\1\2", tail[: write.start() if write else len(tail)])
            totals = [int(value) for value in re.findall(r"\b\d{2,}\b", tail)]
            width = len(totals)
            table = re.sub(
                r"(\d)[,.]\s*(\d{3})(?!\d)", r"\1\2", re.sub(r"\b\d{1,2},\s*19\d\d\b", " ", block[: total_at.start()])
            )
            cells: list[tuple[str, list[int]]] = []
            words: list[str] = []
            for token in re.sub(r"[_\-~:'\"=|.,()]+", " ", table).split():
                if token.isdigit():
                    if words:
                        for size in (3, 2, 1):
                            name = re.sub(r"(in)?part$", "", "".join(words[-size:]).lower())
                            name = re.sub(r"[^a-z]", "", name)
                            if name in counties:
                                cells.append((counties[name], []))
                                break
                        else:
                            cells.append(("?", []))
                        words = []
                    if cells:
                        cells[-1][1].append(int(token))
                else:
                    words.append(token)
            sums = [0] * width
            bad = [(county, values) for county, values in cells if len(values) != width or county == "?"]
            for county, values in cells:
                for column, value in enumerate(values[:width]):
                    sums[column] += value
            if bad or sums != totals:
                print(
                    year, district, "columns sum to", sums, "printed", totals, "bad rows", bad[:6], "rows", len(cells)
                )
                continue
            for county, values in cells:
                for column, value in enumerate(values):
                    rows.append((str(election_day(year)), district, county, column, value))
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day").agg(pl.col("district").n_unique()).sort("day"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Georgia's U.S. House general elections of 1956 through 1988 by county from the Secretary of State's tabulations",
        PUBLISHER,
        TITLE,
        "https://dlg.usg.edu/collection/dlg_ggpd",
        "GA",
        "13",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL.format(year)) for year in YEARS],
        lambda key: METHOD,
    )
