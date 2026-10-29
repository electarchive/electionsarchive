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


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year, spec in YEARS.items():
        groups: dict[tuple[str, int], dict] = {}
        headings: dict[str, list[str]] = {}
        for page in spec["pages"]:
            path = INBOX / f"{spec['volume']}-p{page}-haiku.json"
            if not path.exists():
                continue
            data = json.loads(path.read_text())
            borough = next((name for name in COUNTY if name in data["title"].upper()), None)
            if borough is None:
                continue
            headings.setdefault(borough, data["columns"])
            for panel in data["panels"]:
                ad = ordinal(re.sub(r"(?i)assembly|dist(rict)?|continued|a\. ?d\.", "", panel["ad"]))
                if ad is None:
                    continue
                group = groups.setdefault((borough, ad), {"eds": [], "total": None, "width": len(data["columns"])})
                for row in panel["rows"]:
                    group["eds"].append({"ed": row["ed"], "cells": [number(cell) for cell in row["cells"]], "page": page})
                if panel["total"]:
                    group["total"] = [number(cell) for cell in panel["total"]]
        accepted = []
        failures = {}
        for (borough, ad), group in sorted(groups.items()):
            eds, total, width = group["eds"], group["total"], group["width"]
            if total is None:
                failures[f"{borough}:{ad}"] = "no total row"
            elif any(None in r["cells"] or len(r["cells"]) != width for r in eds) or None in total or len(total) != width:
                failures[f"{borough}:{ad}"] = "an unreadable or misaligned cell"
            elif len({r["ed"] for r in eds}) != len(eds):
                failures[f"{borough}:{ad}"] = "a repeated election district number"
            elif any(sum(r["cells"][i] for r in eds) != total[i] for i in range(width)):
                failures[f"{borough}:{ad}"] = "rows that do not add up to the printed total"
            else:
                accepted.append((borough, ad, eds, total))
        out = []
        for borough, ad, eds, total in accepted:
            for r in eds:
                for index, value in enumerate(r["cells"]):
                    out.append({"county": borough, "ad": ad, "ed": r["ed"], "column": index, "value": value, "page": r["page"]})
            for index, value in enumerate(total):
                out.append({"county": borough, "ad": ad, "ed": 0, "column": index, "value": value, "page": eds[0]["page"]})
        pl.DataFrame(out).write_parquet(PARSED / f"{year}.parquet")
        (PARSED / f"{year}-failures.json").write_text(json.dumps({"left": failures, "columns": headings}, indent=1))
        print(year, "assembly districts loaded", len(accepted), "left out", failures)
