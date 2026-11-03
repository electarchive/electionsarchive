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
CHAMBERS = {"State House": "house", "State Senate": "senate"}
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
        response = client.get(f"{BASE}race/{id}", headers=HEADERS)
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
                [rf"^{DAY}:{state}:(house|senate):[^:]+:g"],
            ).fetchall():
                found.setdefault(key, set()).add(person(label))
        SEATS[state] = found
    return SEATS[state]


def legislature(state: str, chamber: str, race: dict) -> str | None:
    people = {person(c.get("name") or "") for c in race.get("candidates") or []} - {""}
    if not people:
        return None
    numbers = {
        n.lstrip("0") for n in re.findall(r"\d+", f"{race.get('district') or ''} {race.get('election_name') or ''}")
    }
    scored = sorted(
        (
            (
                len(people & wanted) / len(people | wanted),
                bool(numbers & set(re.findall(r"\d+", key.split(":")[3]))),
                key,
            )
            for key, wanted in seats(state).items()
            if key.split(":")[2] == chamber
        ),
        reverse=True,
    )
    if not scored or scored[0][0] < 0.5 or len(scored) > 1 and scored[1][:2] == scored[0][:2]:
        return None
    return scored[0][2].split(":", 3)[3]


def office(state: str, race: dict) -> tuple[str, str, str] | None:
    kind = race.get("election_type") or ""
    title = race.get("type") or ""
    if kind == "General":
        if title == "US Senate":
            return "senate", "", ""
        found = CONGRESS.search(race.get("district") or "")
        if title == "House of Representatives" and found:
            return "house", "0" if found[1] == "AL" else str(int(found[1])), ""
        return None
    if title in CHAMBERS:
        seat = legislature(state, CHAMBERS[title], race) if kind == "Local" else None
        return (CHAMBERS[title], seat, "") if seat else None
    if kind == "Statewide":
        return ("governor", "", "") if title == "Governor" else ("state", "", race.get("election_name") or title)
    return "other", "", race.get("election_name") or title


def rows(state: str, path: Path):
    page = json.loads(path.read_bytes())
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
    if not counties and all(r.get("type") == "State" for r in regions):
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
    found = [
        {"state": state, "page": url, "scope": "", **row}
        for url, path in paths.items()
        if url.startswith(BASE)
        for row in rows(state, path)
    ]
    return pl.DataFrame(found, schema=table({}).schema)
