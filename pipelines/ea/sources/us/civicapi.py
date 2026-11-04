import json
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import polars as pl

from ea.sources.us.enr_2026 import DAY, FIPS, person, table
from ea.sources.us.oe_primaries_2026 import plain
from ea.store import connect

BASE = "https://civicapi.org/api/v2/"
HEADERS = {"User-Agent": "electionsarchive.com live results fallback (https://electionsarchive.com)"}
LIMIT = 500
DETAILS = 40
MINUTE = 55
IDLE = 600
RANK = {"General": 0, "Statewide": 1}
CHAMBERS = {
    "State House": {"house", "assembly", "house-of-delegates", "legislature"},
    "State Senate": {"senate", "legislature"},
}
CONGRESS = re.compile(r"-(\d+|AL)$")
CACHE: dict[int, tuple[tuple, float, bytes]] = {}
QUEUE: dict[str, list[dict[str, str]]] = {}
RACES: dict[str, dict[int, dict]] = {}
TOWNS: dict[str, dict[str, str]] = {}
EQUIVALENTS: dict[str, dict[str, str]] = {}
SEATS: dict[str, dict[str, set[str]]] = {}


def search(client: httpx.Client, state: str, extra: dict[str, str]) -> list[dict]:
    found: dict[int, dict] = {}
    offset = 0
    while True:
        response = client.get(
            f"{BASE}race/search",
            params={
                "startDate": str(DAY),
                "endDate": str(DAY),
                "province": state,
                "country": "US",
                "limit": LIMIT,
                "offset": offset,
                **extra,
            },
            headers=HEADERS,
        )
        response.raise_for_status()
        races = response.json().get("races") or []
        found.update((race["id"], race) for race in races)
        if len(races) < LIMIT or offset >= 4 * LIMIT:
            return list(found.values())
        offset += LIMIT


def discover(client: httpx.Client, state: str) -> None:
    queue = QUEUE.setdefault(state, [])
    if not queue:
        queue.extend([{"election_type": "General"}, {"election_type": "Statewide"}, {"election_type": "Local"}])
    extra = queue.pop(0)
    try:
        races = search(client, state, extra)
    except httpx.HTTPError as error:
        queue.insert(0, extra)
        print(f"civicAPI {state}: search {extra} failed: {type(error).__name__}")
        return
    found = RACES.setdefault(state, {})
    found.update((race["id"], race) for race in races if race.get("province") == state)
    if extra == {"election_type": "Local"} and len(races) >= LIMIT:
        queue.extend(
            {"election_type": "Local", "type": kind}
            for kind in sorted({race["type"] for race in races if race.get("type")})
        )


def collect(client: httpx.Client, state: str, store: Callable[[str, bytes, str], None]) -> None:
    discover(client, state)
    now = time.time()
    due = []
    for id, race in RACES.get(state, {}).items():
        rank = RANK.get(race.get("election_type") or "", 2)
        cached = CACHE.get(id)
        if rank < 2:
            counted = cached is None or any(
                c.get("votes") for c in json.loads(cached[2])["race"].get("candidates") or []
            )
            if cached is None or now - cached[1] >= (MINUTE if counted else IDLE):
                due.append((rank, cached[1] if cached else 0.0, id, ()))
            continue
        candidates = race.get("candidates") or []
        if not any(c.get("votes") for c in candidates):
            continue
        signature = (race.get("percent_reporting"), tuple((c.get("name"), c.get("votes")) for c in candidates))
        if cached is None or cached[0] != signature:
            due.append((rank, cached[1] if cached else 0.0, id, signature))
    for _, _, id, signature in sorted(due)[:DETAILS]:
        try:
            response = client.get(f"{BASE}race/{id}", headers=HEADERS, timeout=15)
        except httpx.TransportError as error:
            print(f"civicAPI {state}: race {id} failed: {type(error).__name__}")
            continue
        if response.status_code < 400:
            CACHE[id] = (signature, time.time(), wrap(RACES[state][id], response.json()))
    for id in RACES.get(state, {}):
        if id in CACHE and any(c.get("votes") for c in json.loads(CACHE[id][2])["race"].get("candidates") or []):
            store(f"{BASE}race/{id}", CACHE[id][2], ".json")


def wrap(search: dict, race: dict) -> bytes:
    return json.dumps({"search": search, "race": race}).encode()


def towns(state: str) -> dict[str, str]:
    if state not in TOWNS:
        with connect() as conn:
            found: dict[str, str] = {}
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.town' and u.code like %s""",
                [f"{FIPS[state]}%"],
            ).fetchall():
                found.setdefault(plain(text), code[:5])
        TOWNS[state] = found
    return TOWNS[state]


def equivalents(state: str) -> dict[str, str]:
    if state not in EQUIVALENTS:
        with connect() as conn:
            EQUIVALENTS[state] = {
                plain(text.rsplit(",", 1)[0]): code
                for code, text in conn.execute(
                    """select u.code, n.text from reporting_unit u
                       join name n on n.entity_id = u.area_id and n.kind = 'official'
                       where u.scheme_code = 'us.county' and u.code ~ %s""",
                    [rf"^{FIPS[state]}\d{{3}}$"],
                ).fetchall()
            }
    return EQUIVALENTS[state]


def seats(state: str) -> dict[str, set[str]]:
    if state not in SEATS:
        with connect() as conn:
            found: dict[str, set[str]] = {}
            for key, label in conn.execute(
                """select x.value, coalesce(ca.ballot_name, o.ballot_label, '')
                   from external_id x
                   join contest_option o on o.contest_id = x.entity_id and o.parent_option_id is null and o.kind <> 'write_in'
                   left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                   where x.scheme = 'us.contest' and x.value ~ %s""",
                [rf"^{DAY}:{state}:(house|assembly|house-of-delegates|legislature|senate)(:[^:]+){{1,2}}:gs?$"],
            ).fetchall():
                found.setdefault(key, set()).add(person(label))
        SEATS[state] = found
    return SEATS[state]


def legislature(state: str, chambers: set[str], race: dict) -> tuple[str, str] | None:
    people = {person(c.get("name") or "") for c in race.get("candidates") or []} - {""}
    if not people:
        return None
    numbers = {
        n.lstrip("0") for n in re.findall(r"\d+", f"{race.get('district') or ''} {race.get('election_name') or ''}")
    }
    special = bool(re.search(r"unexpired|special", race.get("election_name") or "", re.IGNORECASE))
    scored = sorted(
        (
            (
                len(people & wanted) / len(people | wanted),
                bool(numbers & set(re.findall(r"\d+", key.split(":")[3]))),
                key.endswith(":gs") == special,
                key,
            )
            for key, wanted in seats(state).items()
            if key.split(":")[2] in chambers
        ),
        reverse=True,
    )
    for picks in ([s for s in scored if s[0] >= 0.5], [s for s in scored if s[1] and s[0] > 0]):
        if picks and (len(picks) == 1 or picks[1][:3] != picks[0][:3]):
            _, _, chamber, seat = picks[0][3].split(":", 3)
            return chamber, seat
    return None


def office(state: str, race: dict) -> tuple[str, str, str] | None:
    kind = race.get("election_type") or ""
    title = race.get("type") or ""
    if title in CHAMBERS:
        seat = legislature(state, CHAMBERS[title], race) if kind != "Statewide" else None
        return (*seat, "") if seat else None
    if state == "DC" and title == "Mayor":
        kind = "Statewide"
    if title == "Governor's Council":
        return "state", "", race.get("election_name") or title
    if kind == "General":
        if title == "Delegate":
            return "house", "0", ""
        if title == "US Senate":
            return "senate", "", ""
        found = CONGRESS.search(race.get("district") or "")
        if title == "House of Representatives" and found:
            return "house", "0" if found[1] == "AL" else str(int(found[1])), ""
        return None
    if kind == "Statewide":
        return ("governor", "", "") if title == "Governor" else ("state", "", race.get("election_name") or title)
    return "other", "", race.get("election_name") or title


def rows(state: str, page: dict):
    race = page["race"]
    if race.get("test_data") or race.get("province") != state:
        return
    seat = office(state, page["search"])
    if seat is None:
        return
    regions = list((race.get("region_results") or {}).values())
    counties: list[dict] = [r for r in regions if r.get("type") == "County" and r.get("fips")]
    known = equivalents(state)
    for region in regions:
        name = plain(region.get("name") or "")
        code = (
            known.get(name) or known.get(f"{name}city")
            if region.get("type") == "City"
            else known.get(f"{name}county")
            if region.get("type") == "County" and not region.get("fips")
            else None
        )
        if code is not None and code not in {r["fips"] for r in counties}:
            counties.append({**region, "fips": code})
    if state == "AK":
        counties = [
            {**r, "fips": f"AK:{found[1]}"}
            for r in regions
            if (found := re.fullmatch(r"House District (\d+)", r.get("name") or ""))
        ]
    if state == "DC" and any(r.get("type") == "Ward" for r in regions):
        counties = [{**r, "fips": "11001"} for r in regions if r.get("type") == "Ward"]
    if not counties and all(r.get("type") in ("State", "All") for r in regions):
        counties = [{**r, "fips": FIPS[state]} for r in regions] or [
            {"fips": FIPS[state], "candidates": race.get("candidates") or []}
        ]
    names = {} if counties else towns(state)
    reporting = round(float(race.get("percent_reporting") or 0) * 100)
    for region in counties or regions:
        county = (
            region["fips"]
            if counties
            else names.get(plain(region.get("name") or "")) or region.get("fips") or region.get("name") or ""
        )
        for candidate in region.get("candidates") or []:
            name = " ".join((candidate.get("name") or "").split())
            yield {
                "office": seat[0],
                "contest": seat[2],
                "district": seat[1],
                "party": candidate.get("party") or None,
                "county": county,
                "candidate": name,
                "write_in": bool(re.search(r"write-?in", name, re.IGNORECASE)),
                "votes": int(candidate.get("votes") or 0),
                "as_of": race.get("last_updated"),
                "reporting": reporting,
                "total": 10000,
            }


def frame(state: str, paths: dict[str, Path]) -> pl.DataFrame:
    pages: dict[tuple, tuple[tuple[int, str], str, dict]] = {}
    for url, path in paths.items():
        if not url.startswith(BASE):
            continue
        page = json.loads(path.read_bytes())
        race = page["race"]
        candidates = race.get("candidates") or []
        regions = list((race.get("region_results") or {}).values())
        same = (
            page["search"].get("type"),
            race.get("election_name"),
            tuple(sorted(c.get("name") or "" for c in candidates)),
            tuple(sorted(r.get("name") or "" for r in regions)),
            (race.get("district"), race.get("municipality"))
            if all(r.get("type") in ("State", "All") for r in regions)
            else None,
        )
        rank = (sum(int(c.get("votes") or 0) for c in candidates), race.get("last_updated") or "")
        if same not in pages or rank > pages[same][0]:
            pages[same] = (rank, url, page)
    found = [
        {"state": state, "page": url, "scope": "", **row}
        for _, url, page in pages.values()
        for row in rows(state, page)
    ]
    return pl.DataFrame(found, schema=table({}).schema)
