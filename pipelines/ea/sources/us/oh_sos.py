import html
import re
import sys
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import house_books
from ea.store import DATA

SOURCE = "us.oh_sos"
RAW = SOURCE
VERSION = "1"
PUBLISHER = "Ohio Secretary of State"
TITLE = "Official tabulation, U.S. House of Representatives, {day:%B %-d, %Y}"
LICENSE = "Public record"
SUMMARY = "Ohio's House results by county from the Secretary of State's official tabulations"
STATE = "OH"
FIPS = "39"
REVISION = 1
RENAMED: str | None = None
COMMAS = False
ERRATA = {(2000, 13)}
TURNED: set[tuple[int, int]] = set()
RESULTS = "https://www.ohiosos.gov/elections/election-results-and-data/"
FILES = {
    1996: (
        date(1996, 11, 5),
        "20210205200725",
        RESULTS + "1990-1999-official-election-results/u.s.-house-of-representatives-november-5-1996/",
    ),
    2000: (
        date(2000, 11, 7),
        "20211203154643",
        RESULTS + "2000-elections-results/u.s.-house-of-representatives-november-7-2000/",
    ),
    2004: (
        date(2004, 11, 2),
        "20210802060503",
        RESULTS + "2004-elections-results/u.s.-house-of-representatives-november-2-2004/",
    ),
    2006: (
        date(2006, 11, 7),
        "20211203164309",
        RESULTS + "2006-elections-results/u.s.-house-of-representatives-november-7-2006/",
    ),
    2012: (
        date(2012, 11, 6),
        "20201129062133",
        "https://www.ohiosos.gov/globalassets/elections/2012/gen/2012statewidebycounty.xlsx",
    ),
}
VOLUMES = {
    year: (f"http://web.archive.org/web/{stamp}id_/{url}", url, day, range(0))
    for year, (day, stamp, url) in FILES.items()
}


def fetch() -> None:
    for url, *_ in VOLUMES.values():
        raw.fetch(RAW, url, suffix=".xlsx" if url.endswith(".xlsx") else ".html")


def parse() -> None:
    rows: list[dict[str, object]] = []
    failed: list[str] = []

    for year, (url, *_) in VOLUMES.items():
        book = raw.cached(RAW, url)
        tables: list[tuple[int, list[str], list[tuple[str, list[int]]], list[int] | None]] = []
        if year == 2012:
            sheet = pl.read_excel(book.path, sheet_name="U.S. Congress", has_header=False, infer_schema_length=0).rows()
            starts = [
                (c, int(found[1]))
                for c, text in enumerate(sheet[0])
                if text is not None and (found := re.match(r"Representative to Congress - District (\d+)", str(text)))
            ]
            for (start, district), (end, _) in zip(starts, [*starts[1:], (len(sheet[0]), 0)], strict=True):
                names = [str(sheet[1][c]).strip() for c in range(start, end) if sheet[1][c] is not None]
                counties = [
                    (str(line[0]), [int(float(line[c] or 0)) for c in range(start, start + len(names))])
                    for line in sheet[4:]
                    if line[0] is not None and any(float(line[c] or 0) for c in range(start, start + len(names)))
                ]
                tables.append(
                    (
                        district,
                        names,
                        counties,
                        [int(float(sheet[2][c] or 0)) for c in range(start, start + len(names))],
                    )
                )
        else:
            lines = [
                cells
                for tr in re.findall(r"<tr.*?</tr>", book.path.read_text(errors="replace"), flags=re.DOTALL)
                if (
                    cells := [
                        text
                        for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.DOTALL)
                        if (text := re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip())
                    ]
                )
            ]
            for cells in lines:
                numeric = [re.fullmatch(r"[\d,]+", cell) is not None for cell in cells[1:]]
                if found := re.fullmatch(r"district(?: number:)?\s*0*(\d+)", cells[0], flags=re.IGNORECASE):
                    tables.append((int(found[1]), cells[1:], [], None))
                elif not tables:
                    continue
                elif not tables[-1][1]:
                    tables[-1] = (tables[-1][0], [c for c in cells if c.upper() != "COUNTY"], [], None)
                elif cells[0].upper() in ("TOTAL", "TOTALS") and all(numeric):
                    tables[-1] = (*tables[-1][:3], [int(c.replace(",", "")) for c in cells[1:]])
                elif numeric and all(numeric) and not re.match(r"perc", cells[0], flags=re.IGNORECASE):
                    tables[-1][2].append((cells[0], [int(c.replace(",", "")) for c in cells[1:]]))
        for district, names, counties, total in tables:
            for county, votes in counties:
                if len(votes) != len(names):
                    failed.append(f"{year} district {district} {county}: {len(votes)} votes for {len(names)} names")
                    continue
                for c, (name, value) in enumerate(zip(names, votes, strict=True)):
                    rows.append(
                        {
                            "year": year,
                            "page": None,
                            "district": district,
                            "county": re.sub(r"\*", "", county).strip().title(),
                            "column": c,
                            "label": [
                                word
                                for word in re.sub(r"\(.*?\)|\*", " ", name).replace(",", " ").split()
                                if re.sub(r"[^a-z]", "", word.lower()) not in ("jr", "sr", "ii", "iii", "iv")
                            ][-1].title(),
                            "name": name,
                            "votes": value,
                        }
                    )
            sums = [sum(votes[c] for _, votes in counties) for c in range(len(names))]
            if not counties or (total is not None and sums != total and (year, district) not in ERRATA):
                failed.append(f"{year} district {district}: {sums} by county, {total} printed")
    if failed:
        for line in failed:
            print(line)
        raise ValueError(f"{len(failed)} tables do not reconcile")
    frame = pl.DataFrame(rows, infer_schema_length=None)
    print(frame.group_by("year").agg(pl.col("district").n_unique(), pl.len()).sort("year"))
    folder = DATA / "parsed" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(folder / "values.parquet")


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
