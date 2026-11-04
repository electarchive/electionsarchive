import html
import itertools
import json
import os
import re
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from ea.sources.us.enr_2026 import DAY, EV_OFFICES

CDN = "https://app.enhancedvoting.com/cdn/results/"
EXPORTS = {
    "Westchester": "westchester-county-ny/export-2026GeneralElection.zip",
    "Suffolk": "suffolk-county-ny/export-GE2026_20261028145605.zip",
    "Monroe": "monroe-county-ny/export-GE2026.zip",
}
NASSAU = "https://app.nassaucountyny.gov/BOE/results/election2.html"
ERIE = "https://erie.resultscaster.com/api/resultscasts"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; electionsarchive.com)"}
LEGISLATURE = re.compile(
    r"(?P<chamber>State Senator|Member of (?:the )?Assembly),? (?:District )?(?P<district>\d+)(?:st|nd|rd|th)?(?: Senatorial| Assembly)?(?: District)?",
    re.IGNORECASE,
)
STATEWIDE = re.compile(r"(?:State )?Comptroller|Attorney General", re.IGNORECASE)
MONROE_DISTRICT = 25
CODE = re.compile(r"^[A-Z]{3} (?=\S+ )")
NASSAU_TEXT = re.compile(r'<text[^>]*transform="matrix\(1 0 0 -1 ([\d.]+) ([\d.]+)\)"[^>]*>([^<]*)</text>')
NASSAU_MERGED = re.compile(r"^(\D+?)\s*(\d+)$")
NASSAU_PRECINCTS = re.compile(r"(\d+) precincts reported out of (\d+) total")
NEW_YORK = ZoneInfo("America/New_York")


def classify(title: str) -> tuple[str, str, str]:
    office = EV_OFFICES.fullmatch(title)
    seat = LEGISLATURE.fullmatch(title)
    if seat:
        return ("senate" if seat["chamber"].lower().startswith("state") else "assembly"), "", f"{seat['district']}:g"
    if office:
        return (
            "senate" if office["senate"] else "governor" if office["governor"] else "house",
            "",
            office["district"] or "",
        )
    return ("state" if STATEWIDE.fullmatch(title) else "other"), title, ""


def person(label: str) -> str:
    label = html.unescape(label).replace(" AND ", " / ")
    label = CODE.sub("", label) if label != label.upper() else label
    return re.sub(r"\s*/\s*", " / ", " ".join(label.split()))


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    for county, export in EXPORTS.items():
        content = get(client, f"{CDN}{export}", HEADERS)
        if not content:
            print(f"NY: no export for {county}")
            continue
        store(f"{CDN}{export}", content, ".zip")
    erie(client, get, store)
    page = get(client, NASSAU, HEADERS)
    if page and f"{DAY:%B} {DAY.day}, {DAY.year}".upper() in page.decode(errors="replace").upper():
        store(NASSAU, page, ".html")
    else:
        print(f"NY: no {DAY} page for Nassau")


def erie(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    slug = os.environ.get("EA_ERIE_SLUG")
    if slug is None:
        listing = get(client, ERIE, HEADERS)
        slug = next(
            (
                cast["slug"]
                for cast in json.loads(listing or "{}").get("Items") or []
                if (cast.get("electionDate") or {})
                .get("localDateTimeDisplay", "")
                .startswith(f"{DAY.month}/{DAY.day}/{DAY.year} ")
                and not cast.get("isTesting")
                and "test" not in f"{cast['slug']} {cast.get('title')}".lower()
            ),
            None,
        )
    if slug is None:
        print(f"NY: no {DAY} results cast for Erie")
        return
    base = f"{ERIE}/{slug}"
    parts = {"status": get(client, f"{base}/status", HEADERS), "shell": get(client, f"{base}/shell", HEADERS)}
    for group in json.loads(parts["shell"] or "{}").get("groups") or []:
        parts[f"results/{quote(group)}"] = get(client, f"{base}/results/{quote(group)}", HEADERS)
    if len(parts) < 3 or not all(parts.values()):
        print(f"NY: incomplete Erie results cast {slug}")
        return
    for name, content in parts.items():
        store(f"{base}/{name}", content, ".json")


def erie_rows(url: str, urls: dict[str, Path]):
    base = url.removesuffix("/status")
    shell = json.loads(urls[f"{base}/shell"].read_bytes())
    status = json.loads(urls[url].read_bytes())
    stamp = status.get("lastUploadDTTM")
    as_of = datetime.fromisoformat(stamp[:19] + "+00:00").isoformat() if stamp else None
    totals: dict[str, dict[str, int]] = {}
    seen: dict[str, tuple[int | None, int | None]] = {}
    for n, group in enumerate(shell["groups"]):
        path = urls.get(f"{base}/results/{quote(group)}")
        if path is None:
            return
        for contest in json.loads(path.read_bytes())["election"]["contests"]:
            tally = totals.setdefault(contest["key"], {})
            for choice in contest["choices"]:
                tally[choice["key"]] = tally.get(choice["key"], 0) + (choice["votes"] or 0)
            if n == 0:
                seen[contest["key"]] = (contest.get("progress"), contest.get("progressTotal"))
    for contest in shell["contests"]:
        key = contest["contestAltTallyKey"]
        title = " ".join(contest["title"].replace(" & ", " and ").split())
        for choice in contest["choices"]:
            parties = [
                a["name"].replace("<NL>", " ") for a in choice.get("affiliations") or [] if a["abbreviation"] != "NON"
            ]
            yield row(
                "Erie",
                title,
                person(choice["name"]),
                "/".join(parties),
                totals.get(key, {}).get(choice["choiceAltTallyKey"], 0),
                as_of,
                seen.get(key, (None, None)),
            )


def row(county: str, title: str, candidate: str, party: str, votes: int, as_of: str | None, seen: tuple) -> dict:
    office, contest, district = classify(title)
    return {
        "office": office,
        "contest": contest,
        "district": district,
        "party": party,
        "county": county,
        "candidate": candidate,
        "write_in": candidate.lower().startswith("write-in"),
        "votes": votes,
        "as_of": as_of,
        "reporting": seen[0],
        "total": seen[1],
    }


def rows(url: str, path: Path, urls: dict[str, Path]):
    if url == NASSAU:
        yield from nassau(path)
        return
    if url.startswith(ERIE) and url.endswith("/status"):
        yield from erie_rows(url, urls)
        return
    county = next((name for name, export in EXPORTS.items() if url == f"{CDN}{export}"), None)
    if county is None:
        return
    with zipfile.ZipFile(path) as archive:
        export = json.loads(archive.read("export.json"))
    for item in export["results"]["ballotItems"]:
        title = " ".join(html.unescape(item["name"]).split())
        if title == "Representative in Congress":
            title = f"{title} {MONROE_DISTRICT}"
        seen = (item["precinctsReporting"], item["precinctsParticipating"])
        votes: dict[str, list] = {}
        for option in item["ballotOptions"]:
            entry = votes.setdefault(person(option["name"]), [option["politicalParty"] or "", 0])
            entry[1] += int(option["voteCount"] or 0)
        for candidate, (party, count) in votes.items():
            yield row(county, title, candidate, party, count, export["createdAt"], seen)


def nassau(path: Path):
    text = path.read_text(errors="replace")
    tokens = [(float(x), float(y), html.unescape(label)) for x, y, label in NASSAU_TEXT.findall(text)]
    day = next((label for _, _, label in tokens if re.fullmatch(r"\d{4}-\d\d-\d\d", label)), None)
    clock = next((label for _, _, label in tokens if re.fullmatch(r"\d\d:\d\d:\d\d", label)), None)
    stamp = (
        datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=NEW_YORK).astimezone(UTC).isoformat()
        if day and clock
        else None
    )
    title = ""
    seen: tuple[int | None, int | None] = (None, None)
    candidate = ""
    pending: list[tuple[str, int]] = []

    def flush():
        if candidate:
            for party, votes in pending[1:] or pending:
                yield row("Nassau", title, candidate, party, votes, stamp, seen)

    for _, line in itertools.groupby(tokens, key=lambda token: token[1]):
        cells = list(line)
        x, _, label = cells[0]
        number = next((int(c[2]) for c in cells[1:] if c[0] > 200 and c[2].isdigit()), None)
        if number is None and (merged := NASSAU_MERGED.match(label)):
            label, number = merged[1].strip(), int(merged[2])
        if x == 54:
            if "(Vote for" in label:
                yield from flush()
                title, candidate, pending = label.split(" (Vote for")[0].strip(), "", []
            elif found := NASSAU_PRECINCTS.match(label):
                seen = (int(found[1]), int(found[2]))
        elif x == 72 and number is not None and not label.startswith("Total"):
            yield from flush()
            candidate, pending = label, [("", number)]
        elif x == 87 and number is not None and candidate:
            pending.append((label, number))
    yield from flush()
