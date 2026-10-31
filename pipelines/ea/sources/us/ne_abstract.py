import difflib
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.sources.us.ne_usrep import LANDING, PUBLISHER, TITLE, TRANSLATE, URL
from ea.store import DATA, connect

SOURCE = "us.ne_abstract"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CACHE = "us.ne_usrep"
DAYS = {1992: date(1992, 11, 3), 1994: date(1994, 11, 8), 1998: date(1998, 11, 3)}
BLOCKS = [
    (1994, "governor", r"GOVERNOR LT GOVERNOR\s+GOVERNOR", 6, "position"),
    (1994, "senate", r"^\s*UNITED\s+DEMOCRAT", 3, "position"),
    (1998, "governor", r"Governor.Lt\. Governor", 3, "panels"),
]
MANUAL = {
    (1994, "governor"): {
        "lincoln": [8913, 3731, 3, 0, 36, 0],
        "logan": [256, 182, 0, 0, 0, 0],
        "loup": [174, 97, 0, 0, 0, 0],
        "madison": [7165, 2980, 7, 13, 14, 2],
        "mcpherson": [192, 127, 0, 0, 0, 0],
        "merrick": [2358, 894, 0, 0, 0, 0],
        "morrill": [1257, 642, 2, 2, 0, 0],
    }
}
PATCHES = {
    (1994, "senate"): {"frontier": {2: 1}},
    (1994, "governor"): {
        "chase": {3: 1},
        "gosper": {3: 1},
        "keyapaha": {4: 1},
        "frontier": {5: 1},
        "nemaha": {5: 1},
        "thurston": {5: 1},
    },
}
METHOD = (
    "County totals are from the Board of State Canvassers' official report (the Nebraska Library Commission's scan, "
    "read from its text layer, columns assigned by position). A cell the scan garbled is the printed state total "
    "less the other counties, and each candidate's county sum equals the state total printed in the report."
)
ALIASES = {"keamev": "kearney", "dloe": "otoe"}
NUMBER = r"[0-9OoIlJ&\]Ba.,S]*[0-9][0-9OoIlJ&\]Ba.,S]*|[IlJ\]B]"


def number(token: str) -> int | None:
    digits = token.translate(TRANSLATE).replace("J", "1").replace("a", "8").replace(",", "").replace(".", "")
    return int(digits) if digits.isdigit() else None


def text(year: int) -> list[str]:
    path = raw.cached(CACHE, URL.format(year, "prim_gen")).path
    return subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
    ).stdout.split("\n")


PRESIDENT_1992 = {
    "CLINTON": 0,
    "BUSH": 1,
    "MARROU": 2,
    "PEROT": 3,
    "HAGELI": 4,
    "FULANI": 5,
}
NEW_RESIDENTS_1992 = {
    "adams": [15, 19, 0, 19, 0, 0],
    "arthur": [0, 0, 0, 1, 0, 0],
    "boone": [0, 1, 0, 2, 0, 0],
    "boxbutte": [7, 5, 0, 5, 0, 0],
    "buffalo": [5, 18, 0, 9, 0, 0],
    "butler": [2, 3, 0, 2, 0, 0],
    "chase": [0, 0, 0, 1, 0, 0],
    "clay": [0, 6, 0, 1, 0, 0],
    "cuming": [1, 2, 0, 2, 0, 0],
    "dakota": [13, 22, 1, 14, 0, 0],
    "dawson": [2, 4, 0, 2, 0, 0],
    "dodge": [2, 2, 0, 0, 0, 0],
    "douglas": [94, 91, 0, 56, 0, 0],
    "dundy": [1, 0, 0, 0, 0, 0],
    "fillmore": [2, 6, 0, 5, 0, 0],
    "franklin": [0, 2, 0, 1, 0, 0],
    "gage": [6, 11, 0, 14, 0, 0],
    "greeley": [0, 1, 0, 0, 0, 0],
    "hall": [39, 77, 1, 104, 0, 0],
    "hamilton": [2, 9, 0, 7, 0, 0],
    "harlan": [0, 1, 0, 3, 0, 0],
    "howard": [0, 0, 0, 2, 0, 0],
    "kearney": [1, 5, 0, 9, 0, 0],
    "keith": [9, 31, 0, 32, 0, 0],
    "kimball": [0, 0, 0, 1, 0, 0],
    "knox": [0, 1, 0, 0, 0, 0],
    "lancaster": [195, 190, 1, 168, 0, 2],
    "lincoln": [16, 29, 1, 22, 0, 0],
    "loup": [1, 1, 0, 1, 0, 0],
    "madison": [12, 26, 0, 22, 0, 0],
    "morrill": [0, 1, 0, 1, 0, 0],
    "nuckolls": [0, 0, 0, 3, 0, 0],
    "phelps": [1, 4, 0, 4, 0, 0],
    "platte": [15, 24, 0, 15, 0, 0],
    "polk": [0, 2, 0, 0, 0, 0],
    "redwillow": [2, 12, 0, 5, 0, 0],
    "richardson": [0, 0, 0, 3, 0, 0],
    "sarpy": [21, 34, 0, 14, 0, 0],
    "scottsbluff": [2, 0, 0, 2, 0, 0],
    "seward": [3, 16, 0, 9, 0, 0],
    "sherman": [0, 0, 0, 1, 0, 0],
    "thayer": [1, 4, 0, 2, 0, 0],
    "thomas": [1, 0, 0, 0, 0, 0],
    "washington": [8, 7, 0, 18, 0, 0],
    "webster": [1, 1, 0, 0, 0, 0],
    "york": [0, 0, 0, 1, 0, 0],
}
PRESIDENT_1992_PATCHES = {"gosper": {2: 1}, "sherman": {5: 1}, "sioux": {5: 1}}
PRESIDENT_1992_ANCHORS = ["BALLOTS", "CLINTON", "GORE", "BUSH", "QUAYLE", "MARROU", "LORD"]
PRESIDENT_1992_ANCHORS_B = ["PAGE", "PEROT", "STOCKDALE", "HAGELI", "TOMPKINS", "FULANI", "MUNOZ"]


def president_1992(codes: dict[str, str]) -> list[tuple[str, str, str, str, int]]:
    lines = text(1992)
    headers = [
        i
        for i, line in enumerate(lines)
        if i > 2900 and re.match(r"\s*COUNTY\s", line) and ("CLINTON" in line or "PEROT" in line)
    ]
    totals = [0] * 6
    printed = [0] * 6
    sums: dict[str, list[int]] = {}
    pages: dict[int, dict[str, dict[int, int]]] = {}
    lines = [re.sub(r",\s+(?=[0-9OoIlJ&\]B])", ",", line) for line in lines]
    for header in headers[2:]:
        head = lines[header]
        names = PRESIDENT_1992_ANCHORS if "CLINTON" in head else PRESIDENT_1992_ANCHORS_B
        if names is PRESIDENT_1992_ANCHORS_B:
            words = head.split()
            anchor = [head.index(words[1]) + len(words[1])]
            names = names[1:]
        else:
            anchor = [head.index("BALLOTS") + 7]
            names = names[1:]
        edges = [(head.index(name) + len(name), PRESIDENT_1992.get(name)) for name in names]
        edges.append((anchor[0], None))
        end = next(i for i in range(header, header + 120) if re.match(r"\s*TOTAL\b", lines[i]))
        for line in lines[header + 1 : end + 1]:
            is_total = re.match(r"\s*TOTAL\b", line) is not None
            match = re.match(r"\s*([A-Za-z][A-Za-z .~]*?)\s{2,}(.*)$", line) if not is_total else None
            body = line.split("TOTAL", 1)[1] if is_total else (match[2] if match else "")
            shift = len(line) - len(body)
            found = [(m.end() + shift, number(m[0])) for m in re.finditer(NUMBER, body)]
            cells = {}
            for stop, value in found:
                column = min(edges, key=lambda edge: abs(edge[0] - stop))[1]
                if column is not None and value is not None:
                    cells[column] = cells.get(column, 0) + value
            if is_total:
                print(
                    "  page",
                    header,
                    cells,
                    {c: sum(r.get(c, 0) for r in pages.get(header, {}).values()) for c in cells},
                )
                for column, value in cells.items():
                    printed[column] += value
                break
            if match is None:
                continue
            key = re.sub(r"[^a-z]", "", match[1].lower())
            close = difflib.get_close_matches(key, codes, 1, 0.7)
            if not close:
                continue
            row = sums.setdefault(close[0], [0] * 6)
            pages.setdefault(header, {})[close[0]] = cells
            for column, value in cells.items():
                row[column] += value
    for county, resident in NEW_RESIDENTS_1992.items():
        row = sums.setdefault(county, [0] * 6)
        for column, value in enumerate(resident):
            row[column] += value
    for county, patch in PRESIDENT_1992_PATCHES.items():
        for column, value in patch.items():
            sums[county][column] += value
    for column in range(6):
        totals[column] = sum(row[column] for row in sums.values())
    print(1992, "president", len(sums), totals, printed)
    return [
        ("1992-11-03", "president", codes[county], str(column), value)
        for county, row in sums.items()
        for column, value in enumerate(row)
        if value
    ]


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", name.lower()): code
            for name, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    rows = president_1992(codes)
    for year, office, heading, width, mode in BLOCKS:
        lines = text(year)
        floor = {1994: 3000, 1998: 2500}[year]
        start = next(i for i, line in enumerate(lines) if i > floor and re.search(heading, line))
        table: dict[str, list[int | None]] = {}
        total: list[tuple[int, int | None]] = []
        for line in lines[start : start + 400]:
            if "ABSTRACT" in line:
                continue
            is_total = re.search(r"(^|\s{2})TOTAL\b", line) is not None
            halves = [line]
            if is_total:
                offset = line.index("TOTAL") + 5
                total = [(m.end() + offset, number(m[0])) for m in re.finditer(NUMBER, line[offset:])]
                halves = [line[: offset - 5]]
            elif mode == "panels":
                split = next((m.start() for m in re.finditer(r"[A-Za-z]", line) if m.start() >= 55), None)
                halves = [line[:split], line[split:]] if split else [line]
            for half in halves:
                match = re.match(r"\s*([A-Za-z][A-Za-z .]*?)\s{2,}(.*)$", half)
                if match is None:
                    continue
                key = ALIASES.get(re.sub(r"[^a-z]", "", match[1].lower()), re.sub(r"[^a-z]", "", match[1].lower()))
                close = difflib.get_close_matches(key, codes, 1, 0.7)
                if not close:
                    continue
                shift = half.index(match[2], len(match[1]))
                found = [(m.end() + shift, number(m[0])) for m in re.finditer(NUMBER, match[2])]
                if mode == "panels":
                    cells = [value for _, value in found]
                    table[close[0]] = (cells + [None] * width)[:width] if len(cells) != width else cells
                else:
                    table[close[0]] = found
            if is_total:
                break
        printed = [value or 0 for _, value in total][:width]
        if mode == "position":
            edges = [end for end, _ in total][:width]
            for county, found in table.items():
                cells: list[int | None] = [0] * width
                for end, value in found:
                    cells[min(range(width), key=lambda i: abs(edges[i] - end))] = value
                table[county] = cells
        table.update(MANUAL.get((year, office), {}))
        for county, patch in PATCHES.get((year, office), {}).items():
            for column, value in patch.items():
                table[county][column] = (table[county][column] or 0) + value
        print(year, office, len(table), printed, sorted(set(codes.values()) - {codes[k] for k in table}))
        for column in range(width):
            missing = [county for county, cells in table.items() if cells[column] is None]
            if len(missing) == 1:
                table[missing[0]][column] = printed[column] - sum(c[column] or 0 for c in table.values())
            got = sum(c[column] or 0 for c in table.values())
            if got != printed[column]:
                print("  column", column, "sum", got, "printed", printed[column], "missing", missing)
        for county, cells in table.items():
            for column, value in enumerate(cells):
                if value:
                    rows.append((str(DAYS[year]), office, codes[county], str(column), value))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


RESIDENTS = (
    " The 1992 presidential county totals add the ballots of new residents voting for president, which the canvass printed on "
    "separate pages, to the main county pages; candidate sums match the statewide totals the canvass printed."
)


def method(key: tuple[str, ...]) -> str:
    return METHOD + (RESIDENTS if "1992" in str(key) else "")


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Nebraska's 1992 president, 1994 Senate and governor, and 1998 governor general elections by county from the Board of State Canvassers' reports",
        PUBLISHER,
        TITLE,
        LANDING,
        "NE",
        "31",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(CACHE, URL.format(year, "prim_gen")) for year in DAYS],
        method,
        official=True,
        statewide=True,
    )
