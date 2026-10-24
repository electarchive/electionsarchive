import itertools
import json
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.mo_usrep_manual"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Missouri Secretary of State"
TITLE = "Official Manual of the State of Missouri, general election returns"
API = "https://mdh.contentdm.oclc.org/digital/api/collections/bluebook/items/{}/false"
PAGES = {
    1992: [58404, 58405, 58406, 58407, 58408],
    1994: [59737, 59738, 59739],
    1998: [62626, 62627, 62628],
}
PRINTED = {1994: [530, 531, 532], 1998: [561, 562, 563]}
DAYS = {1992: date(1992, 11, 3), 1994: date(1994, 11, 8), 1998: date(1998, 11, 3)}
PRINTED_SUMS = {("1972-11-07", "3"): [124365, 53523, 465]}
PAGE_FIXES = {(62628, "868"): "888"}
TOTALS = {(1998, 7): [43416, 129746, 5639]}
EARLY = [
    (header.split(), block)
    for header, *block in (
        chunk.splitlines()
        for chunk in Path(__file__).with_name("mo_usrep_manual_early.txt").read_text().split("@ ")[1:]
    )
]
FIXES = {"JL782": "1,782", "lackson": "Jackson", "RaHs": "Ralls", "lllyes": "Illyes"}
METHOD = (
    "County totals are from the Missouri Official Manual's general election returns (volumes 1993-1994, 1995-1996, and "
    "1999-2000, scanned by Missouri Digital Heritage), read from the page OCR and checked against the page images. Each "
    "candidate's county sum equals the district total printed in the Manual, except where noted. Kansas City, which "
    "reports apart, is added into Jackson County."
)
NOTES = {
    "1972-11-07:MO:house:3": " The Manual prints 54,523 as the Republican total, which the FEC also reports, but its two printed parts (30,034 in the city and 23,489 in the county) sum to 53,523.",
    "1992-11-03:MO:house:8": " The county rows fall short of the printed district total by 270 Republican votes; every cell was checked against the page image."
}


def fetch() -> None:
    for pointers in [*PAGES.values(), sorted({int(header[2]) for header, _ in EARLY})]:
        for pointer in pointers:
            try:
                raw.cached(SOURCE, API.format(pointer))
                continue
            except LookupError:
                pass
            body = subprocess.run(
                ["curl", "-s", "-m", "60", "-A", "Mozilla/5.0", API.format(pointer)], capture_output=True, check=True
            ).stdout
            raw.store(
                SOURCE,
                API.format(pointer),
                body,
                suffix=".json",
                retrieved_at=datetime.now(UTC),
                media_type="application/json",
            )


def read_page(year: int, pointer: int) -> list[str]:
    path = raw.cached(SOURCE, API.format(pointer)).path
    text = json.loads(Path(path).read_text())["text"]
    lines = []
    for index, line in enumerate(text.splitlines()):
        line = line.strip()
        if (
            not line
            or "MANUAL" in line
            or "GENERAL ELECTION" in line
            or line.startswith("U.S. Congress")
            or line.startswith("November")
        ):
            continue
        if line.isdigit() and int(line) in PRINTED[year] and index < 4:
            continue
        lines.append(PAGE_FIXES.get((pointer, line)) or FIXES.get(line, line))
    return lines


def tables(year: int, keys: dict[str, str]) -> dict[int, list[tuple[str, list[int]]]]:
    lines = list(itertools.chain.from_iterable(read_page(year, pointer) for pointer in PAGES[year]))
    blocks: dict[int, list[str]] = {}
    district = 0
    for line in lines:
        heading = re.fullmatch(r"District (\d)|Districts", line)
        if heading:
            district = int(heading[1]) if heading[1] else district + 1
            blocks[district] = []
        elif district:
            blocks[district].append(line)
    found: dict[int, list[tuple[str, list[int]]]] = {}
    for district, block in blocks.items():
        cut = next(index for index, line in enumerate(block) if line == "TOTALS")
        total = [int(value.replace(",", "")) for value in block[cut + 1 :] if re.fullmatch(r"[\d,]+", value)]
        total = TOTALS.get((year, district), total)
        width = len(total)
        flushes: list[tuple[list[str], list[int]]] = []
        names: list[str] = []
        numbers: list[int] = []
        started = False
        for line in block[:cut]:
            key = re.sub(r"[^a-z]", "", re.sub(r"\(part of\)", "", re.sub(r"^S[Ll]\b", "St.", line)).lower())
            if key in keys:
                if numbers:
                    flushes.append((names, numbers))
                    names, numbers = [], []
                names.append(key)
                started = True
            elif started and re.fullmatch(r"[\d,]+", line):
                numbers.append(int(line.replace(",", "")))
        flushes.append((names, numbers))
        options = [(False, True) if len(group) > 1 else (False,) for group, _ in flushes]
        for choice in itertools.product(*options):
            rows: list[tuple[str, list[int]]] = []
            for (group, values), column_major in zip(flushes, choice, strict=True):
                if len(values) != len(group) * width:
                    break
                for index, name in enumerate(group):
                    if column_major:
                        rows.append((name, [values[column * len(group) + index] for column in range(width)]))
                    else:
                        rows.append((name, values[index * width : (index + 1) * width]))
            else:
                if [sum(row[1][column] for row in rows) for column in range(width)] == total:
                    found[district] = rows
                    break
        else:
            raise ValueError(f"{year} district {district}: no reading ties the printed total {total}")
    return found


def parse() -> None:
    with connect() as conn:
        keys = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%%'"""
            ).fetchall()
        }
    keys.update({"stlouiscity": "29510", "stlouis": "29189", "stlouiscounty": "29189", "kansascity": keys["jackson"]})
    rows = []
    for line in Path(__file__).with_name("mo_usrep_manual_1992.tsv").read_text().splitlines():
        day, district, name, column, votes = line.split("\t")
        rows.append((day, int(district), keys[re.sub(r"[^a-z]", "", name.lower())], column, int(votes)))
    for year in (1994, 1998):
        for district, table in tables(year, keys).items():
            for name, values in table:
                for column, votes in enumerate(values):
                    rows.append((str(DAYS[year]), district, keys[name], str(column), votes))
    for (day, district, _), block in EARLY:
        total = PRINTED_SUMS.get((day, district)) or [int(value) for value in block[-1].split()[1:]]
        table = [
            (line.rsplit(" ", len(total))[0], [int(value) for value in line.rsplit(" ", len(total))[1:]])
            for line in block[:-1]
        ]
        if [sum(values[column] for _, values in table) for column in range(len(total))] != total:
            raise ValueError(f"{day} district {district}: rows do not tie the printed totals {total}")
        for name, values in table:
            for column, votes in enumerate(values):
                rows.append((day, int(district), keys[re.sub(r"[^a-z]", "", name.lower())], str(column), votes))
    frame = (
        pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
        .group_by("day", "district", "county", "column", maintain_order=True)
        .agg(pl.col("votes").sum())
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "district").agg(pl.col("county").n_unique()).sort("day", "district"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Missouri's U.S. House general elections of 1992, 1994, and 1998 by county from the Official Manual",
        PUBLISHER,
        TITLE,
        "https://mdh.contentdm.oclc.org/digital/collection/bluebook",
        "MO",
        "29",
        pl.read_parquet(PARSED / "lines.parquet"),
        [
            raw.cached(SOURCE, API.format(pointer))
            for pointers in [*PAGES.values(), sorted({int(header[2]) for header, _ in EARLY})]
            for pointer in pointers
        ],
        lambda key: METHOD + NOTES.get(key, ""),
    )
