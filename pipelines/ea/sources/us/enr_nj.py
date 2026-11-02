import csv
import io
import json
import re
import zipfile
from collections.abc import Callable
from datetime import datetime
from functools import cache
from pathlib import Path

import httpx

from ea.sources.us.enr_2026 import DAY, WRITE_IN, person
from ea.store import connect

CLARITY = "https://results.enr.clarityelections.com/NJ/"
TENANTS = (
    "Atlantic",
    "Burlington",
    "Cape_May",
    "Cumberland",
    "Essex",
    "Gloucester",
    "Hudson",
    "Mercer",
    "Middlesex",
    "Monmouth",
    "Morris",
    "Ocean",
    "Passaic",
    "Somerset",
    "Union",
    "Warren",
)
SENATE = re.compile(r"(?i)\b(?:U\.? ?S\.?|United States)\b.*\bSenat")
HOUSE = re.compile(r"(?i)House of Representatives|\bCongress")
DISTRICT = re.compile(r"(?i)(\d+)(?:st|nd|rd|th)\b|District (\d+)")


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    missing = []
    for tenant in TENANTS:
        base = f"{CLARITY}{tenant}/"
        listing = get(client, f"{base}elections.json")
        ids = [
            e["EID"]
            for e in json.loads(listing if listing and listing.lstrip().startswith(b"[") else "[]")
            if datetime.strptime(e["Date"].split()[0], "%m/%d/%Y").date() == DAY
            and not re.search(r"TEST|MOCK|TRAINING|SCHOOL|RUN-?OFF", e["ElectionName"].upper())
        ]
        version = get(client, f"{base}{ids[0]}/current_ver.txt") if ids else None
        url = f"{base}{ids[0]}/{version.decode().strip()}/reports/summary.zip" if ids and version else None
        content = get(client, url) if url else None
        if url and content:
            store(url, content, ".zip")
        else:
            missing.append(tenant)
    if missing:
        print(f"NJ: no {DAY} Clarity file for {', '.join(missing)}")


@cache
def districts() -> dict[str, str]:
    with connect() as conn:
        return {
            person(name): key.rsplit(":", 1)[1]
            for key, name in conn.execute(
                """select x.value, coalesce(ca.ballot_name, o.ballot_label)
                   from external_id x join contest_option o on o.contest_id = x.entity_id
                   left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                   where x.scheme = 'us.contest' and x.value like %s and o.kind <> 'write_in'""",
                [f"{DAY}:NJ:house:%"],
            ).fetchall()
            if name
        }


def rows(url: str, path: Path):
    tenant = re.search(r"/NJ/([A-Za-z_]+)/\d+/", url)
    if tenant is None:
        return
    with zipfile.ZipFile(path) as archive:
        lines = list(csv.DictReader(io.StringIO(archive.read("summary.csv").decode("utf-8-sig"))))
    races: dict[str, list[dict[str, str]]] = {}
    for line in lines:
        races.setdefault(re.sub(r"\s*\(Vote For \d+\)$", "", line["contest name"].strip()), []).append(line)
    for name, entries in races.items():
        office = "senate" if SENATE.search(name) else "house" if HOUSE.search(name) else "other"
        district = ""
        if office == "house":
            found = DISTRICT.search(name)
            district = (found[1] or found[2]) if found else ""
            if not district:
                hits = [
                    districts().get(person(e["choice name"])) for e in entries if not WRITE_IN.search(e["choice name"])
                ]
                district = max(set(hits) - {None}, key=hits.count, default="") or ""
        for e in entries:
            yield {
                "office": office,
                "contest": name if office == "other" else "",
                "district": district,
                "party": e["party name"],
                "county": tenant[1].replace("_", " "),
                "candidate": e["choice name"].strip(),
                "write_in": bool(WRITE_IN.search(e["choice name"])),
                "votes": int(e["total votes"] or 0),
                "as_of": None,
                "reporting": int(e[next(k for k in e if k.endswith(" rptg"))] or 0),
                "total": int(e[next(k for k in e if k.endswith(" total") and k.startswith("num "))] or 0),
            }
