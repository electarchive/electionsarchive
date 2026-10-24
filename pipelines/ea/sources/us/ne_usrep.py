import difflib
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.ne_usrep"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Nebraska Secretary of State"
TITLE = "Official Report of the Board of State Canvassers of the State of Nebraska"
LANDING = "https://govdocs.nebraska.gov/epubs/S1200/S001.html"
URL = "https://govdocs.nebraska.gov/epubs/S1200/S001-{}{}.pdf"
EARLY = [
    (header.split(), block)
    for header, *block in (
        chunk.splitlines() for chunk in Path(__file__).with_name("ne_usrep_early.txt").read_text().split("@ ")[1:]
    )
]
EARLY_DAYS = {1944: date(1944, 11, 7), 1946: date(1946, 11, 5), 1948: date(1948, 11, 2), 1950: date(1950, 11, 7), 1952: date(1952, 11, 4), 1954: date(1954, 11, 2), 1956: date(1956, 11, 6), 1958: date(1958, 11, 4), 1960: date(1960, 11, 8), 1978: date(1978, 11, 7), 1962: date(1962, 11, 6), 1964: date(1964, 11, 3), 1966: date(1966, 11, 8), 1968: date(1968, 11, 5), 1970: date(1970, 11, 3), 1972: date(1972, 11, 7), 1974: date(1974, 11, 5), 1976: date(1976, 11, 2), 1982: date(1982, 11, 2), 1984: date(1984, 11, 6), 1986: date(1986, 11, 4), 1988: date(1988, 11, 8), 1990: date(1990, 11, 6)}
DAYS = {1992: date(1992, 11, 3), 1994: date(1994, 11, 8), 1996: date(1996, 11, 5), 1998: date(1998, 11, 3)}
STARTS = {1992: 3361, 1994: 3194, 1996: 1859, 1998: 3033}
METHOD = (
    "County totals are from the Board of State Canvassers' official report (the Nebraska Library Commission's scan, "
    "read from its text layer), one table per congressional district. Misread cells and lines were settled from the "
    "page images, and each candidate's county sum equals the district total printed in the report."
)
ALIASES = {"lolip": "loup", "kihsall": "kimball", "bun": "burt"}
TOKEN = r"[0-9OoIl&\]B.,S·]+"
TRANSLATE = str.maketrans({"O": "0", "o": "0", "&": "6", "]": "1", "I": "1", "l": "1", "B": "8", "S": "5", "~": "", "_": "", "·": ""})
TOTALS = {(1994, 3): [41943, 154919, 41]}
ROWS = {
    (1994, 1, "York"): {1: 3828},
    (1994, 3, "Blaine"): {1: 377},
    (1998, 1, "Dixon"): {0: 486, 1: 1790, 2: 1},
    (1998, 1, "Stanton"): {0: 294, 1: 1762, 2: 1},
    (1998, 1, "Dodge"): {2: 27},
    (1998, 3, "Greeley"): {0: 899, 1: 249, 2: 0, 3: 0},
    (1998, 3, "Sioux"): {0: 606, 1: 59, 2: 0, 3: 0},
    (1998, 3, "Hall"): {3: 27},
}
WRITEINS = {
    (1992, 1): "Cass 8 Cuming 5 Dodge 16 Johnson 1 Madison 15 Nemaha 2 Otoe 4 Richardson 2 Saunders 12 Seward 6 Thurston 2 York 13",
    (1992, 3): "Adams 18 Clay 1 Dawes 1 Deuel 1 Lincoln 10 Platte 3 Polk 4 Red Willow 3",
    (1994, 1): "Cass 4 Cuming 7 Dakota 2 Dixon 8 Dodge 12 Johnson 1 Lancaster 139 Madison 5 Nemaha 2 Otoe 5 Saunders 9 Seward 3 Stanton 1 Washington 10 York 6",
    (1994, 3): "Adams 9 Chase 1 Deuel 1 Fillmore 1 Hall 8 Kimball 1 Knox 2 Lincoln 8 Platte 10",
}


def fetch() -> None:
    for year in [*EARLY_DAYS, *DAYS]:
        raw.fetch(SOURCE, URL.format(year, "gen" if year < 1938 else "prim_gen"), suffix=".pdf")


def number(text: str) -> int | None:
    digits = text.translate(TRANSLATE).replace(",", "").replace(".", "")
    return int(digits) if digits.isdigit() else None


def districts(lines: list[str]) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    block: list[str] | None = None
    district = 0
    for line in lines:
        heading = re.search(r"\b(FIRST|SECOND|THIRD|First|Second|Third)\s+(DISTRICT|District)\b", line)
        if heading and (block is None or any("TOTAL" in text for text in block)):
            district = {"first": 1, "second": 2, "third": 3}[heading[1].lower()]
            block = [line]
        elif block is not None:
            block.append(line)
            if re.search(r"\bTOTAL\b", line):
                found[district] = block
                block = None
                if district == 3:
                    break
    return found


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    rows = []
    for year, day in DAYS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, URL.format(year, "gen" if year < 1938 else "prim_gen")).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for district, block in districts(text.splitlines()[STARTS[year] :]).items():
            total = TOTALS.get((year, district))
            if total is None:
                line = next(line for line in block if re.search(r"\bTOTAL\b", line))
                total = [number(token) for token in re.findall(TOKEN, line.split("TOTAL", 1)[1])]
            width = len(total)
            table: dict[str, list[int | None]] = {}
            for line in block:
                if "TOTAL" in line:
                    line = line.split("TOTAL", 1)[0]
                pattern = r"([A-Za-z][A-Za-z .'~]*?)\s{2,}((?:%s\s+){0,%d}%s)(?=\s{2,}|\s*$)" % (TOKEN, width - 1, TOKEN)
                for match in re.finditer(pattern, line):
                    key = ALIASES.get(re.sub(r"[^a-z]", "", match[1].lower()), re.sub(r"[^a-z]", "", match[1].lower()))
                    close = difflib.get_close_matches(key, codes, 1, 0.7) if key else []
                    if close:
                        table[close[0]] = [number(token) for token in re.findall(TOKEN, match[2])]
            for (fix_year, fix_district, county), cells in ROWS.items():
                if (fix_year, fix_district) == (year, district):
                    row = table.setdefault(re.sub(r"[^a-z]", "", county.lower()), [None] * width)
                    row.extend([None] * (width - len(row)))
                    for column, value in cells.items():
                        row[column] = value
            if (year, district) in WRITEINS:
                items = WRITEINS[(year, district)].split()
                counts: dict[str, int] = {}
                while items:
                    words = []
                    while not items[0].isdigit():
                        words.append(items.pop(0))
                    counts[re.sub(r"[^a-z]", "", "".join(words).lower())] = int(items.pop(0))
                for county, row in table.items():
                    del row[2:]
                    if county in counts:
                        row.extend([None] * (2 - len(row)) + [counts.pop(county)])
                if counts:
                    raise ValueError(f"{year} {district}: write-ins for unknown {counts}")
            sums = [sum(row[column] or 0 for row in table.values() if len(row) > column) for column in range(width)]
            if sums != total:
                raise ValueError(f"{year} district {district}: columns sum to {sums}, printed {total}")
            for county, row in table.items():
                for column, value in enumerate(row):
                    if value is not None:
                        rows.append((str(day), district, codes[county], str(column), value))
    for (day, district, _), block in EARLY:
        table = {
            re.sub(r"[^a-z]", "", name.lower()): cells
            for name, cells in (
                (" ".join(word for word in line.split() if not word.isdigit() and word != "-"), [None if word == "-" else int(word) for word in line.split() if word.isdigit() or word == "-"])
                for line in block
            )
        }
        total = table.pop("t")
        for column, printed in enumerate(total):
            if sum(row[column] or 0 for row in table.values() if len(row) > column) != printed:
                raise ValueError(f"{day} district {district}: column {column} does not sum to {printed}")
        for county, row in table.items():
            for column, value in enumerate(row):
                if value is not None:
                    rows.append((day, int(district), codes[county], str(column), value))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "district").agg(pl.col("county").n_unique()).sort("day", "district"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Nebraska's U.S. House general elections of 1962 through 1998 by county from the Board of State Canvassers' reports",
        PUBLISHER,
        TITLE,
        LANDING,
        "NE",
        "31",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL.format(year, "gen" if year < 1938 else "prim_gen")) for year in [*EARLY_DAYS, *DAYS]],
        lambda key: METHOD,
    )
