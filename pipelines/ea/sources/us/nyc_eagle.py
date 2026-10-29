import json
import re

import polars as pl

from ea.sources.us.nyc_redbook import number, ordinal
from ea.store import DATA

SOURCE = "us.nyc_eagle"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "eagle"
COUNTY = {"MANHATTAN": "36061", "BRONX": "36005", "BROOKLYN": "36047", "QUEENS": "36081", "RICHMOND": "36085"}
YEARS = {
    1909: {"volume": "eagle1910", "item": "brooklyndailyeag00unse_0", "pages": range(554, 565), "recap": 569},
    1917: {"volume": "eagle1918", "item": "brooklyndailyeag00unse", "pages": range(511, 528), "recap": None},
}


def add_groups(groups: dict, path, page: int, borough: str) -> None:
    data = json.loads(path.read_text())
    for panel in data["panels"]:
        label = re.sub(r"(?i)assembly|dist(rict)?|continued|a\. ?d\.", "", panel["ad"])
        digits = re.search(r"\d+", label)
        ad = int(digits.group()) if digits else ordinal(label)
        if ad is None:
            continue
        group = groups.setdefault((borough, ad), {"eds": {}, "total": None, "width": len(data["columns"]), "page": page})
        for row in panel["rows"]:
            group["eds"].setdefault(row["ed"], [number(cell) for cell in row["cells"]])
        if panel["total"]:
            group["total"] = [number(cell) for cell in panel["total"]]


def settle(reads: list, width: int):
    eds: dict[int, list] = {}
    for read in reads:
        for ed in read["eds"]:
            eds.setdefault(ed, [])
    totals = [read["total"] for read in reads if read["total"] is not None and len(read["total"]) == width]
    if not totals:
        return None, "no total row"
    settled = {ed: [None] * width for ed in eds}
    total = [None] * width
    for column in range(width):
        options = {}
        for ed in eds:
            values = {read["eds"][ed][column] for read in reads if ed in read["eds"] and len(read["eds"][ed]) == width and read["eds"][ed][column] is not None}
            if not values:
                return None, "an unreadable or misaligned cell"
            options[ed] = sorted(values)
        target = {t[column] for t in totals if t[column] is not None}
        if not target:
            return None, "an unreadable or misaligned cell"
        free = [ed for ed in eds if len(options[ed]) > 1]
        if len(free) > 12:
            return None, "too many disagreeing cells"
        base = sum(options[ed][0] for ed in eds if ed not in free)
        found = []
        for mask in range(2 ** len(free)):
            picks = {ed: options[ed][(mask >> i) & 1] for i, ed in enumerate(free)}
            if any(picks[ed] is None for ed in free):
                continue
            subtotal = base + sum(picks.values())
            for t in target:
                if subtotal == t:
                    found.append((picks, t))
        if len(found) != 1:
            return None, "rows that do not add up to the printed total" if not found else "more than one reading ties"
        picks, t = found[0]
        for ed in eds:
            settled[ed][column] = picks[ed] if ed in picks else options[ed][0]
        total[column] = t
    return (settled, total), None


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year, spec in YEARS.items():
        volume = spec["volume"]
        kinds: dict[str, dict] = {"page": {}, "strip": {}}
        borough = None
        for page in spec["pages"]:
            first = INBOX / f"{volume}-p{page}-{volume}.json"
            strips = [INBOX / f"{volume}-p{page}-c{k}-{volume}c.json" for k in range(3)]
            files = [("page", path) for path in [first] if path.exists()] + [("strip", path) for path in strips if path.exists()]
            for _, path in files:
                try:
                    title = json.loads(path.read_text())["title"].upper()
                except json.JSONDecodeError:
                    continue
                found = next((name for name in COUNTY if name in title), None)
                if found:
                    borough = found
            if borough is None:
                continue
            for kind, path in files:
                try:
                    add_groups(kinds[kind], path, page, borough)
                except json.JSONDecodeError:
                    print(year, "page", page, path.name, "truncated")
        accepted = []
        failures = {}
        for key in sorted(set(kinds["page"]) | set(kinds["strip"])):
            group_reads = [kinds[kind][key] for kind in kinds if key in kinds[kind]]
            borough, ad = key
            result, why = settle(group_reads, max(read["width"] for read in group_reads))
            if result is None:
                failures[f"{borough}:{ad}"] = why
            else:
                accepted.append((borough, ad, result[0], result[1], group_reads[0]["page"]))
        out = []
        for borough, ad, eds, total, page in accepted:
            for ed, cells in eds.items():
                for index, value in enumerate(cells):
                    out.append({"county": borough, "ad": ad, "ed": ed, "column": index, "value": value, "page": page})
            for index, value in enumerate(total):
                out.append({"county": borough, "ad": ad, "ed": 0, "column": index, "value": value, "page": page})
        pl.DataFrame(out, schema={"county": pl.String, "ad": pl.Int64, "ed": pl.Int64, "column": pl.Int64, "value": pl.Int64, "page": pl.Int64}).write_parquet(PARSED / f"{year}.parquet")
        (PARSED / f"{year}-failures.json").write_text(json.dumps({"left": failures}, indent=1))
        print(year, "assembly districts loaded", len(accepted), "left out", len(failures), failures)
