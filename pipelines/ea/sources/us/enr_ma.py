import csv
import io
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from ea.sources.us import elstats
from ea.sources.us.enr_2026 import DAY
from ea.sources.us.oe_primaries_2026 import plain, town_counties

BASE = "https://electionstats.state.ma.us/elections/"
LISTING = f"{BASE}search/year_from:{DAY.year}/year_to:{DAY.year}/stage:General"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; electionsarchive.com)"}
STATEWIDE = {"Attorney General", "Secretary of the Commonwealth", "Treasurer", "Auditor", "Governor", "U.S. Senate"}
SKIP = {"Blanks", "Total Votes Cast"}
ROW = re.compile(
    r'id="election-id-(\d+)".*?<td[^>]*>\d{4}</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>',
    re.DOTALL,
)


def download(election: str) -> str:
    return f"{BASE}download/{election}/precincts_include:0/"


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    listing = get(client, LISTING, HEADERS)
    if listing is None:
        print(f"MA: no {DAY.year} general election listing")
        return
    found = 0
    for election, office, place, stage in ROW.findall(listing.decode(errors="replace")):
        place = place.strip()
        if stage != "General Election" or not (office == "U.S. House" or office in STATEWIDE and place == "Statewide"):
            continue
        content = get(client, download(election), HEADERS)
        if content:
            store(f"{download(election)}#{office}|{place}", content, ".csv")
            found += 1
    if not found:
        print(f"MA: no {DAY.year} general election results")


def rows(url: str, path: Path, urls: dict[str, Path]):
    if not url.startswith(f"{BASE}download/"):
        return
    title, _, place = url.partition("#")[2].partition("|")
    table = csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig")))
    header = next(table)
    parties = next(table)
    kind, seat, contest = (
        ("house", re.match(r"\d+", place)[0], "")
        if title == "U.S. House"
        else ("senate", "", "")
        if title == "U.S. Senate"
        else ("governor", "", "")
        if title == "Governor"
        else ("state", "", title)
    )
    towns = town_counties("MA")
    counted = []
    for line in table:
        town = re.sub(
            r"^([NSEW])\. ", lambda m: {"N": "North ", "S": "South ", "E": "East ", "W": "West "}[m[1]], line[0]
        )
        if not town or town == "TOTALS":
            continue
        county = towns.get(plain(re.sub(elstats.SUFFIXES, "", town.lower())))
        if county is not None:
            counted.append((county, line))
    reporting = sum(1 for _, line in counted if int(line[-1].replace(",", "") or 0) > 0)
    for county, line in counted:
        for column, name in enumerate(header[3:-2], start=3):
            if name and name not in SKIP:
                yield {
                    "office": kind,
                    "contest": contest,
                    "district": seat,
                    "party": parties[column] if column < len(parties) else "",
                    "county": county,
                    "candidate": "Write-ins" if name == "All Others" else " ".join(name.split()),
                    "write_in": name == "All Others",
                    "votes": int(line[column].replace(",", "") or 0),
                    "as_of": None,
                    "reporting": reporting,
                    "total": len(counted),
                }
