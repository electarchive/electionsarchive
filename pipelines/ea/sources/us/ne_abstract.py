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
DAYS = {1994: date(1994, 11, 8), 1998: date(1998, 11, 3)}
BLOCKS = [
    (1994, "senate", r"^\s*UNITED\s+DEMOCRAT", 3, "position"),
    (1998, "governor", r"Governor.Lt\. Governor", 3, "panels"),
]
METHOD = (
    "County totals are from the Board of State Canvassers' official report (the Nebraska Library Commission's scan, "
    "read from its text layer, columns assigned by position). A cell the scan garbled is the printed state total "
    "less the other counties, and each candidate's county sum equals the state total printed in the report."
)
ALIASES = {"keamev": "kearney", "dloe": "otoe"}
NUMBER = r"[0-9OoIlJ&\]Ba.,S]*[0-9][0-9OoIlJ&\]Ba.,S]*|[IlJ\]]"


def number(token: str) -> int | None:
    digits = token.translate(TRANSLATE).replace("J", "1").replace("a", "8").replace(",", "").replace(".", "")
    return int(digits) if digits.isdigit() else None


def text(year: int) -> list[str]:
    path = raw.cached(CACHE, URL.format(year, "prim_gen")).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout.split("\n")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", name.lower()): code
            for name, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    rows = []
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
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Nebraska's 1994 Senate and governor and 1998 governor general elections by county from the Board of State Canvassers' reports",
        PUBLISHER,
        TITLE,
        LANDING,
        "NE",
        "31",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(CACHE, URL.format(year, "prim_gen")) for year in DAYS],
        lambda key: METHOD,
        official=True,
        statewide=True,
    )
