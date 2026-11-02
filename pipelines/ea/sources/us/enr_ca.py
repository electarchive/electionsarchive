import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

API = "https://api.sos.ca.gov/returns/"
OFFICES = (
    "governor",
    "lieutenant-governor",
    "secretary-of-state",
    "controller",
    "treasurer",
    "attorney-general",
    "insurance-commissioner",
    "superintendent-of-public-instruction",
)
CHAMBERS = {"us-rep": ("house", 52), "state-senate": ("senate", 40), "state-assembly": ("assembly", 80)}
COUNTIES = [
    "alameda",
    "alpine",
    "amador",
    "butte",
    "calaveras",
    "colusa",
    "contra-costa",
    "del-norte",
    "el-dorado",
    "fresno",
    "glenn",
    "humboldt",
    "imperial",
    "inyo",
    "kern",
    "kings",
    "lake",
    "lassen",
    "los-angeles",
    "madera",
    "marin",
    "mariposa",
    "mendocino",
    "merced",
    "modoc",
    "mono",
    "monterey",
    "napa",
    "nevada",
    "orange",
    "placer",
    "plumas",
    "riverside",
    "sacramento",
    "san-benito",
    "san-bernardino",
    "san-diego",
    "san-francisco",
    "san-joaquin",
    "san-luis-obispo",
    "san-mateo",
    "santa-barbara",
    "santa-clara",
    "santa-cruz",
    "shasta",
    "sierra",
    "siskiyou",
    "solano",
    "sonoma",
    "stanislaus",
    "sutter",
    "tehama",
    "trinity",
    "tulare",
    "tuolumne",
    "ventura",
    "yolo",
    "yuba",
]
DISTRICTS = Path(__file__).with_name("enr_ca_districts.tsv")
REPORTING = re.compile(r"\(([\d,]+) of ([\d,]+)\)")
PLACE = re.compile(r" - (.+?)(?: County)? Results$")
PACIFIC = ZoneInfo("America/Los_Angeles")


def number(text: str | int | None) -> int:
    return int(str(text or 0).replace(",", "") or 0)


def stamp(text: str) -> str | None:
    text = text.replace("a.m.", "AM").replace("p.m.", "PM").replace("noon", "12:00 PM").replace("midnight", "12:00 AM")
    for layout in ("%B %d, %Y, %I:%M %p", "%B %d, %Y, %I %p"):
        try:
            return datetime.strptime(text.strip(), layout).replace(tzinfo=PACIFIC).isoformat()
        except ValueError:
            continue
    return None


def pages(offices: tuple[str, ...] = OFFICES) -> list[str]:
    urls = [f"{API}{office}/county/{county}" for office in offices for county in COUNTIES]
    urls += [f"{API}ballot-measures", *(f"{API}ballot-measures/county/{county}" for county in COUNTIES)]
    for line in DISTRICTS.read_text().splitlines()[1:]:
        path, counties = line.split("\t")
        urls += [f"{API}{path}/county/{county}" for county in counties.split(",")]
    return urls


def collect(
    client: httpx.Client, get: Callable[[httpx.Client, str], bytes | None], store: Callable[[str, bytes], None]
):
    with ThreadPoolExecutor(8) as pool:
        for url, content in zip(urls := pages(), pool.map(lambda url: get(client, url), urls), strict=True):
            if content and content.lstrip()[:1] in (b"[", b"{"):
                store(url, content)


def districts(client: httpx.Client) -> None:
    def probe(path: str) -> tuple[str, list[str]]:
        found = []
        for county in COUNTIES:
            response = client.get(f"{API}{path}/county/{county}")
            if response.status_code == 200 and response.content.lstrip()[:1] == b"[":
                found.append(county)
        return path, found

    paths = []
    for chamber, (_, seats) in CHAMBERS.items():
        for seat in range(1, seats + 1):
            if client.get(f"{API}{chamber}/district/{seat}").status_code == 200:
                paths.append(f"{chamber}/district/{seat}")
    with ThreadPoolExecutor(8) as pool:
        found = list(pool.map(probe, paths))
    DISTRICTS.write_text("path\tcounties\n" + "".join(f"{path}\t{','.join(c)}\n" for path, c in found if c))
    print({path: len(c) for path, c in found if len(c) != 1 or not c})


def rows(url: str, path: Path, urls: dict[str, Path]):
    data = json.loads(path.read_bytes())
    entries = data if isinstance(data, list) else [data]
    route = url.removeprefix(API).split("/")
    whole = next((e for e in entries if not e["raceTitle"].endswith(" County Results")), entries[-1])
    if route[0] == "ballot-measures" and f"{API}ballot-measures" in urls:
        whole = json.loads(urls[f"{API}ballot-measures"].read_bytes())
    found = REPORTING.search(whole.get("Reporting") or "")
    reporting, total = (number(found[1]), number(found[2])) if found else (None, None)
    if route[0] == "ballot-measures":
        if len(route) == 1:
            return
        county = PLACE.search(data["raceTitle"])
        for measure in data["ballot-measures"]:
            for answer in ("yes", "no"):
                yield {
                    "office": "question",
                    "district": f"proposition-{int(measure['Number'])}",
                    "party": "",
                    "county": county[1] if county else route[-1].replace("-", " ").title(),
                    "candidate": answer.title(),
                    "write_in": False,
                    "votes": number(measure[f"{answer}Votes"]),
                    "as_of": stamp(data.get("ReportingTime") or ""),
                    "reporting": reporting,
                    "total": total,
                }
        return
    office, district = (
        (CHAMBERS[route[0]][0], route[2] + ("" if route[0] == "us-rep" else ":g"))
        if route[0] in CHAMBERS
        else (route[0], "")
    )
    for entry in entries:
        county = PLACE.search(entry["raceTitle"])
        if county is None or not entry["raceTitle"].endswith(" County Results"):
            continue
        for candidate in entry["candidates"]:
            yield {
                "office": office,
                "district": district,
                "party": candidate.get("Party") or "",
                "county": county[1],
                "candidate": candidate["Name"].strip(),
                "write_in": False,
                "votes": number(candidate["Votes"]),
                "as_of": stamp(entry.get("ReportingTime") or ""),
                "reporting": reporting,
                "total": total,
            }
