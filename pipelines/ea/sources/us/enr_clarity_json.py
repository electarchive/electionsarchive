import json
import re
from collections.abc import Iterator, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ea import portals, raw
from ea.sources.us import enr_clarity

SOURCE = "us.enr_clarity_json"
VERSION = "1"
PLATFORM = "clarity_json"
SETTLED = timedelta(days=14)


def _listing(row: Mapping[str, str]) -> Iterator[tuple[date, str]]:
    tenant = row["tenant"]
    listed = json.loads(raw.cached(SOURCE, f"{tenant}elections.json?v=1").path.read_bytes())
    found = {listed_row["EID"]: datetime.strptime(listed_row["Date"].split()[0], "%m/%d/%Y").date() for listed_row in listed}
    for token in re.findall(r"eids=([\d@,-]+)", row["evidence"]):
        for item in token.split(","):
            eid, _, when = item.partition("@")
            found.setdefault(eid, date.fromisoformat(when))
    for eid, day in found.items():
        yield day, eid


def fetch() -> None:
    for fips in sorted({row["county_fips"][:2] for row in enr_clarity.tenants(PLATFORM)}):
        for layer in enr_clarity.LAYERS:
            raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=fips, layer=layer), suffix=".zip")
    for row in enr_clarity.tenants(PLATFORM):
        tenant = row["tenant"]
        raw.fetch(SOURCE, f"{tenant}elections.json?v=1", suffix=".json")
        fetched = 0
        for day, eid in _listing(row):
            if day > date.today() - SETTLED:
                continue
            status, version, _ = portals.get(f"{tenant}{eid}/current_ver.txt")
            if status != 200 or not version.strip().isdigit():
                print(f"{tenant}{eid}: version {status}")
                continue
            link = f"{tenant}{eid}/{version.decode().strip()}/json/sum.json"
            try:
                raw.fetch(SOURCE, link, suffix=".json")
                fetched += 1
            except httpx.HTTPError as error:
                print(f"{link}: {str(error).splitlines()[0]}")
        print(f"{row['state']} {row['county']}: {fetched} fetched", flush=True)


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    _, index = raw._index(SOURCE)
    for tenant, row in registry.items():
        for day, eid in sorted(_listing(row)):
            links = [key for key in index if key.startswith(f"{tenant}{eid}/") and key.endswith("/json/sum.json")]
            if not links:
                continue
            link = max(links, key=lambda key: int(key.split("/")[-3]))
            summary = json.loads(raw.cached(SOURCE, link).path.read_bytes())
            name = next(
                (listed["ElectionName"] for listed in json.loads(raw.cached(SOURCE, f"{tenant}elections.json?v=1").path.read_bytes()) if listed["EID"] == eid),
                "",
            )
            yield {
                "tenant": tenant,
                "election": eid,
                "day": day,
                "title": name,
                "official": False,
                "primary": False,
                "contests": [
                    {
                        "key": node["K"],
                        "text": node["C"],
                        "vote_for": int(node["VF"] or 1),
                        "choices": [
                            {"text": choice, "party": party or None, "total": str(votes), "places": []}
                            for choice, party, votes in zip(node["CH"], node["P"], node["V"], strict=True)
                        ],
                    }
                    for node in summary["Contests"]
                ],
            }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant) and link.endswith("sum.json"))
