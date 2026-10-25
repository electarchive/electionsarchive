import json
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from ea import raw
from ea.sources.us import enr_clarity
from ea.store import DATA

SOURCE = "us.tx_citysum"
VERSION = "1"
PLATFORM = "tx_citysum"
FIPS = "48"
SOURCES = (
    "us.enr_clarity",
    "us.tx_collin",
    "us.tx_tarrant",
    "us.tx_harris",
    "us.tx_hidalgo",
    "us.bexar_local",
    "us.tx_ellis",
    "us.tx_elpaso",
)
THRESHOLD = 0.01
WRITE_IN = re.compile(r"(?i)write[- ]?ins?")
COLUMNS = [
    "county",
    "election",
    "contest",
    "day",
    "scheme",
    "code",
    "runoff",
    "special",
    "stage",
    "key",
    "text",
    "magnitude",
    "contest_key",
    "party",
    "primary",
]


def fetch() -> None:
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    shares: dict[str, dict[str, float]] = {}
    parts: dict[str, dict[str, dict[str, Any]]] = {}
    for name in SOURCES:
        folder = DATA / "parsed" / name
        if not (folder / "contests.parquet").exists():
            continue
        contests = pl.read_parquet(folder / "contests.parquet")
        if not set(COLUMNS) <= set(contests.columns):
            continue
        contests = contests.filter(
            (pl.col("state") == "TX")
            & (pl.col("scheme") == "us.place")
            & (pl.col("key") != "question")
            & ~pl.col("primary")
        )
        for scheme, code, value in (
            pl.read_parquet(folder / "polities.parquet").select("scheme", "code", "shares").iter_rows()
        ):
            if scheme == "us.place":
                shares[code] = json.loads(value)
        grouped = {
            keys: frame
            for keys, frame in pl.read_parquet(folder / "lines.parquet")
            .filter(pl.col("place").is_null())
            .group_by("county", "election", "contest")
        }
        for row in contests.select(COLUMNS).iter_rows(named=True):
            chosen = grouped.get((row["county"], row["election"], row["contest"]))
            if chosen is None:
                continue
            totals = {
                re.sub(r"[^a-z0-9]+", "", choice.lower()): (choice, party, votes)
                for choice, party, votes in chosen.select("choice", "party", "votes").iter_rows()
            }
            size = sum(votes for _, _, votes in totals.values())
            held = parts.setdefault(row["contest_key"], {}).get(row["county"])
            if held is None or size > held["size"]:
                parts[row["contest_key"]][row["county"]] = {**row, "totals": totals, "size": size}
    skipped: Counter[str] = Counter()
    blocked: Counter[tuple[str, str]] = Counter()
    summed: Counter[str] = Counter()
    elections: dict[tuple[date, bool, bool, bool, str], list[dict[str, Any]]] = {}
    tenants = {row["county_fips"]: tenant for tenant, row in registry.items()}
    for contest_key, by_county in sorted(parts.items()):
        first = next(iter(by_county.values()))
        required = {county for county, share in shares.get(first["code"], {}).items() if share >= THRESHOLD}
        if len(required) < 2:
            continue
        if not required <= set(by_county):
            blocked[(first["code"], ",".join(sorted(required - set(by_county))))] += 1
            continue
        names = [
            {key for key in part["totals"] if not WRITE_IN.search(part["totals"][key][0])}
            for part in by_county.values()
        ]
        if any(group != names[0] for group in names):
            skipped["candidate sets differ"] += 1
            print(f"{contest_key}: candidate sets differ across {sorted(by_county)}; skipped")
            continue
        extras = [
            {key for key in part["totals"] if WRITE_IN.search(part["totals"][key][0])} for part in by_county.values()
        ]
        keep = names[0] | (set.intersection(*extras) if all(extras) else set())
        choices = [
            {
                "text": next(iter(by_county.values()))["totals"][key][0],
                "party": next(iter(by_county.values()))["totals"][key][1],
                "total": str(sum(part["totals"][key][2] for part in by_county.values())),
                "places": [],
            }
            for key in sorted(keep)
        ]
        official = all(part["stage"] == "official" for part in by_county.values())
        home = max(required, key=lambda county: shares[first["code"]][county])
        if home not in tenants:
            skipped["no registry row for the main county"] += 1
            continue
        flags = (first["day"], official, first["runoff"], first["special"], home)
        elections.setdefault(flags, []).append(
            {
                "key": str(len(elections.get(flags, []))),
                "text": first["text"],
                "vote_for": int(first["magnitude"] or 1),
                "choices": choices,
                "cast": None,
                "whole": True,
                "summed": True,
            }
        )
        summed[first["code"]] += 1
    for (code, missing), count in blocked.most_common(30):
        print(f"blocked: {count:4} contests of {code}, no part for county {missing}")
    print(f"summed {sum(summed.values())} contests in {len(summed)} places: {dict(summed.most_common(40))}")
    for (day, official, runoff, special, home), nodes in sorted(elections.items()):
        yield {
            "tenant": tenants[home],
            "election": f"{day}-{home}-{int(official)}{int(runoff)}{int(special)}",
            "day": day,
            "title": "Texas "
            + ("special " if special else "")
            + ("runoff " if runoff else "")
            + "city election, county results summed",
            "official": official,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
