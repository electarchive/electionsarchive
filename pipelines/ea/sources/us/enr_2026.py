import csv
import difflib
import html
import io
import itertools
import json
import os
import re
import time
import zipfile
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.clarity_local import COUNTIES, STATES
from ea.sources.us.county_changes import ct_regions
from ea.sources.us.oe_primaries_2026 import ALIASES, OFFICIAL, plain, town_counties
from ea.sources.us.oe_primaries_2026 import FIPS as COUNTY_FIPS
from ea.store import DATA, Connection, connect

SOURCE = "us.enr_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date.fromisoformat(os.environ.get("ENR_2026_DAY", "2026-11-03"))
FIPS = {**COUNTY_FIPS, "AK": "02"}
ONLY = set(filter(None, os.environ.get("ENR_2026_STATES", "").split(",")))
HEADERS = {"User-Agent": "electionsarchive.com"}
EV_EXPORTS = {
    "AZ": "https://app.enhancedvoting.com/cdn/results/Arizona/export-110326GeneralElection.zip",
    "GA": "https://results.sos.ga.gov/cdn/results/Georgia/export-11032026GeneralAndSpecialElection.zip",
    "ID": "https://results.voteidaho.gov/cdn/results/id/export-nov2026.zip",
    "RI": "https://electionresults.ri.gov/cdn/results/RhodeIsland/export-2026StatewideGeneralElection.zip",
    "TN": "https://app.enhancedvoting.com/cdn/results/shelby-county-tn/export-11032026Midtermelections.zip",
    "UT": "https://electionresults.utah.gov/cdn/results/Utah/export-General11032026.zip",
    "VA": "https://app.enhancedvoting.com/cdn/results/virginia/export-2026-November-General.zip",
    "WA": "https://results.votewa.gov/cdn/results/washington/export-20261103.zip",
}
CLARITY_EIDS = {"SC": ["127300"], "WV": ["127496"]}
CLARITY = {
    "CO": "https://results.enr.clarityelections.com/CO/",
    "IA": "https://electionresults.iowa.gov/IA/",
    "SC": "https://www.enr-scvotes.org/SC/",
    "WV": "https://results.enr.clarityelections.com/WV/",
}
EV_OFFICES = re.compile(
    r"^(?:(?P<party0>REP|DEM) )?(?:(?P<senate>(?:U\.? ?S\.? |United States )Senat(?:e|or)|Senator in Congress)"
    r"|(?P<governor>(?:(?:For|State|KS)\s+)?Gov(?:ernor|\.)?(?:(?:\s+(?:and|&)\s+|\s*/\s*)(?:Lieutenant|Lt\.?)\s+Gov(?:ernor|\.)?(?:\s+State)?)?)"
    r"|(?:(?:U\.? ?S\.? (?:House|Representative)s?|United States Representative|Representative in Congress|Representative to the \d+(?:st|nd|rd|th) United States Congress"
    r"|Rep\. in Congress|Congress Dist|(?:For )?Representative to Congress)\b[^\d]*"
    r"|Congressional District (?=\d+ - U\.? ?S\.? Representative$)|Congressional (?=\d+(?:st|nd|rd|th) District Representative$))"
    r"(?P<district>\d+)(?:st|nd|rd|th)?(?:\s+Congressional)?(?:\s+District)?(?: - U\.? ?S\.? Representative| Representative)?\)?)"
    r"\s*(?:-\s*(?P<party1>Rep|Dem|Democratic|Republican|Libertarian))?\s*$",
    re.IGNORECASE,
)
LISTINGS = (
    "elections.json",
    "cid=arkansas",
    "/ballot-items",
    "Georgia",
    "/id",
    "/utah",
    "/RhodeIsland",
    "/washington",
    "Multiparish.htm",
    "Elections.json",
    "Version.json",
)
TN_LEGISLATURE = re.compile(r"(?:Tennessee|TN) (Senate|House of Representatives),? District (\d+)")
TN_AMENDMENT = re.compile(r"Constitutional Amendment\s*#\s*\d+")
LIVE_COUNTIES = {
    "48113": ("TX", "Dallas", "127396"),
    "26125": ("MI", "Oakland", "127284"),
    "26099": ("MI", "Macomb", "127293"),
    "26049": ("MI", "Genesee", "127354"),
}
COUNTY_FEEDS = {"KY", "MI", "MO", "NJ", "NY", "TN", "WI"}
EV_LOCAL = re.compile(r"[A-Z][\w .'-]{1,30}: ")
EV_MEMBER = re.compile(r"Member, (?:United States Senate|House of Representatives \((\d+)(?:st|nd|rd|th) District\))")
SUFFIXES = ("jr", "sr", "ii", "iii", "iv")
YESNO = {"for": "yes", "against": "no", "approve": "yes", "reject": "no"}
SYNONYMS = {"id": "identification", "sell": "sale"}
TENANT = re.compile(r"/[A-Z]{2}/([A-Za-z_]+)/\d+/\d+/reports")
STOP = {"the", "of", "and", "for", "district", "county", "city", "town", "board", "office"}
PLACE_WORDS = {"township", "charter", "village", "public", "schools", "school", "community", "area", "consolidated"}
SCHOOL_WORDS = {
    "district",
    "elementary",
    "unified",
    "union",
    "joint",
    "unit",
    "number",
    "corporation",
    "metropolitan",
    "county",
    "city",
    "borough",
    "regional",
    "independent",
    "high",
}
GENERIC = {
    "an",
    "to",
    "in",
    "on",
    "by",
    "be",
    "or",
    "is",
    "as",
    "at",
    "with",
    "shall",
    "vote",
    "yes",
    "no",
    "prop",
    "proposition",
    "proposal",
    "proposed",
    "question",
    "issue",
    "measure",
    "referendum",
    "bond",
    "bonds",
    "millage",
    "levy",
    "tax",
    "renewal",
    "additional",
    "increase",
    "rate",
    "limiting",
    "operating",
    "purposes",
    "general",
    "new",
    "election",
    "judge",
    "court",
    "retain",
    "retained",
    "retention",
    "justice",
    "appeals",
    "supreme",
    "associate",
    "township",
    "village",
}
NOBODY = r"(?i)^no candidat"
COURTS = {"supreme", "criminal", "civil"}
ORDINALS = {
    word: str(number)
    for number, word in enumerate(
        [
            "first",
            "second",
            "third",
            "fourth",
            "fifth",
            "sixth",
            "seventh",
            "eighth",
            "ninth",
            "tenth",
            "eleventh",
            "twelfth",
            "thirteenth",
            "fourteenth",
            "fifteenth",
            "sixteenth",
            "seventeenth",
            "eighteenth",
            "nineteenth",
        ],
        1,
    )
}
TENS = {
    word: number
    for number, word in enumerate(["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], 2)
}
FL_OFFICES = {"USS": "senate", "USR": "house", "GOV": "governor"}
IL_ELECTIONS = {2022: 62, 2024: 66, 2026: 70}
IL_CITIES = {
    "CITY OF CHICAGO": "Cook",
    "CITY OF BLOOMINGTON": "McLean",
    "CITY OF DANVILLE": "Vermilion",
    "CITY OF EAST ST. LOUIS": "St. Clair",
    "CITY OF GALESBURG": "Knox",
    "CITY OF ROCKFORD": "Winnebago",
}
APPEALS = re.compile(r"\bcourt of appeals?\b|\bappellate\b|\bsupreme court\b.*\bretain", re.IGNORECASE)
SEAT = re.compile(
    r"(?:(?:district|seat)\s+0*(\d+)|\b0*(\d+)(?:st|nd|rd|th)\s+district)(?:\s*[,-]\s*office\s+(\d+))?", re.IGNORECASE
)
IL_STATEWIDE = ("190-ATTORNEY%20GENERAL", "200-SECRETARY%20OF%20STATE", "210-COMPTROLLER", "220-TREASURER")
IL_CONTEST = re.compile(r"UNITED STATES SENATOR|GOVERNOR.*|(\d+)(?:ST|ND|RD|TH) CONGRESS")
KS_CONTEST = re.compile(
    r"United States (?P<senate>Senate)|United States House of Representatives (?P<district>\d+)|(?P<governor>Governor / Lt\. Governor)"
)
CT_OFFICE = re.compile(r"Governor and Lieutenant Governor|United States Senator|Representative in Congress (\d+)")
MN_CONTEST = re.compile(r"(U\.S\. Representative|U\.S\. Senator|Governor & Lt Governor)(?: District (\d+))?")
MN_OFFICES = {"U.S. Representative": "house", "U.S. Senator": "senate", "Governor & Lt Governor": "governor"}
LA_BASE = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob="
LA_RACE = re.compile(
    r"U\. S\. (?:(?P<senate>Senator)|Representative -- (?P<district>\d+)(?:st|nd|rd|th) Congressional District)"
    r"(?: -- (?P<party>\w+) Party)?"
)
NC_CONTEST = re.compile(r"US SENATE|NC GOVERNOR|US HOUSE OF REPRESENTATIVES DISTRICT (\d+)")
AR_CONTEST = re.compile(r"U\.S\. Senate|Governor|U\.S\. Congress District (\d+)")
AR_HEADERS = {"User-Agent": "Mozilla/5.0", "Origin": "https://arkansas.tally-enr.com"}
OK_BASE = "https://results.okelections.gov/OKERS/enrapi/"
OK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
OK_RACE = re.compile(r"FOR (GOVERNOR|UNITED STATES SENATOR|UNITED STATES REPRESENTATIVE DISTRICT (\d+))")
PA_API = "https://www.electionreturns.pa.gov/api/ElectionReturn/"
PA_ELECTION: dict[date, str] = {}
PA_OFFICES = {"2": "senate", "3": "governor", "11": "house"}
NOTE = (
    "Unofficial election night results as the publisher's reporting site showed them at the time given. Counts change as "
    "ballots are counted and until the canvass certifies them; the certified canvass will publish as a separate result set."
)

TAGS = re.compile(r"(?:\s*(?:\([A-Za-z]{1,4}\)|\*))+$")
WRITE_IN = re.compile(r"write-?in|scattering|\(W\)$", re.IGNORECASE)
NO_CANDIDATE = re.compile(r"^no candidate", re.IGNORECASE)


def office_name(text: str) -> str:
    found = EV_MEMBER.fullmatch(text.strip())
    if found is None:
        return text.strip()
    return f"United States Representative {found[1]}" if found[1] else "United States Senator"


def stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word


def terms(label: str, joined: bool = True) -> list[str]:
    text = re.sub(r",?\s+(?:M\.?D\.?|Ph\.?D\.?|D\.?D\.?S\.?|Esq\.?)$", "", label.replace("’", "'"))
    if joined:
        text = re.sub(r"(?<=[a-z])-(?=[a-z])", "", text, flags=re.IGNORECASE)
    words = [
        YESNO.get(w, w) for w in re.sub(r"[^a-z ]", " ", slug(text).replace("-", " ")).split() if w not in SUFFIXES
    ]
    return words[:-1] if len(words) > 2 and words[-1] == "i" else words


def district_words(key: str) -> set[str]:
    parts = key.split(":")
    if len(parts) < 6 or not re.fullmatch(r"\d{5}", parts[2]):
        return set()
    words = set(parts[4].split("-")) - set(parts[3].split("-")) - STOP
    return words - {"no"} if any(not w.isdigit() for w in words - {"no"}) else words


OFFICE_TEXT = re.compile(
    r"^.*\b(?:clerk|judge|sheriff|treasurer|collector|assessor|coroner|surveyor)\b\s+(?=\S+\s+\S)", re.IGNORECASE
)


def person(label: str) -> str:
    label = re.sub(r"\s+jp(?:\s+district)?\s*\d*$", "", re.sub(r"\([^)]*\)", " ", label), flags=re.IGNORECASE)
    label = OFFICE_TEXT.sub("", label)
    words = terms(label)
    if words and words[0] in ("yes", "no"):
        return words[0]
    if words[-2:-1] == ["retained"] and words[-1] in ("yes", "no"):
        return words[-1]
    head, comma, rest = label.partition(",")
    if comma and terms(rest):
        words = terms(f"{rest} {head}")
    return words[0][0] + words[-1] if len(words) > 1 else words[0] if words else ""


def edits(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


SCHOOL_COUNTIES = {
    "1737170": {"17043", "17089"},
    "1740350": {"17043"},
    "1741980": {"17043"},
    "0400813": {"04025"},
}


def placed(name: list[str], words: set[str], strict: bool, whole: bool = False, apart: bool = False) -> bool:
    found = set(name) & words
    core = {w for w in name if not w.isdigit()} - SCHOOL_WORDS
    if strict and whole and core:
        return core <= words or not apart and any(w.isdigit() for w in found)
    if strict:
        return not name or name[0] in words and 2 * len(found) > len(set(name)) or any(w.isdigit() for w in found)
    return (
        not name or bool(found) or any(a in b or len(b) > 4 and edits(a, b) for a in name for b in words if len(a) > 4)
    )


def snap(people: frozenset[str], wanted: set[str], words: set[str]) -> frozenset[str]:
    lacking = wanted - people
    agreed = bool(people & wanted - {"yes", "no"})
    swaps: dict[str, str] = {}
    for name in people - wanted - {"yes", "no"}:
        close = [
            other
            for other in lacking
            if other not in {"yes", "no"}
            and len(name) > 4
            and len(other) > 4
            and (
                name[0] == other[0]
                and (edits(name, other) and min(len(name), len(other)) > 5 or agreed and name[1:] in words)
                or agreed
                and name[1:] == other[1:]
            )
        ]
        if len(close) == 1:
            swaps[name] = close[0]
    if len(set(swaps.values())) != len(swaps):
        return people
    return frozenset(swaps.get(name, name) for name in people)


def get(client: httpx.Client, url: str, headers: dict[str, str] | None = None) -> bytes | None:
    for attempt in range(3):
        try:
            response = client.get(url, headers=headers)
        except httpx.TransportError:
            time.sleep(2 + attempt * 3)
            continue
        if response.status_code == 404:
            return None
        if response.status_code < 400 and response.status_code != 202:
            return response.content
        time.sleep(1 + attempt)
    return None


def fetch() -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    pages: dict[str, dict[str, str]] = {}

    def store(state: str, url: str, content: bytes, suffix: str) -> None:
        key = f"{url}@{stamp}"
        raw.store(
            SOURCE,
            key,
            content,
            suffix=suffix,
            retrieved_at=datetime.now(UTC),
            media_type={
                ".json": "application/json",
                ".html": "text/html",
                ".csv": "text/csv",
                ".xml": "application/xml",
            }.get(suffix, "text/tab-separated-values"),
        )
        pages.setdefault(state, {})[url] = key

    collect(httpx.Client(follow_redirects=True, timeout=120, headers=HEADERS), ONLY, store)
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "pages.json").write_text(json.dumps(pages))
    print({state: len(urls) for state, urls in pages.items()})


def collect(client: httpx.Client, only: set[str], store: Callable[[str, str, bytes, str], None]) -> None:
    for state, url in EV_EXPORTS.items():
        if only and state not in only:
            continue
        content = get(client, url)
        if content is None:
            print(f"{state}: no export")
        else:
            store(state, url, content, ".zip")
    for state, base in CLARITY.items():
        if only and state not in only:
            continue
        listing = get(client, f"{base}elections.json")
        if listing is None or not listing.lstrip().startswith(b"["):
            print(f"{state}: no election listing")
            continue
        store(state, f"{base}elections.json", listing, ".json")
        ids = (
            [os.environ[f"ENR_2026_{state}_EID"]]
            if f"ENR_2026_{state}_EID" in os.environ
            else [
                e["EID"]
                for e in json.loads(listing)
                if datetime.strptime(e["Date"].split()[0], "%m/%d/%Y").date() == DAY
                and not re.search(r"TEST|MOCK|TRAINING", e["ElectionName"].upper())
            ]
            or CLARITY_EIDS.get(state, [])
        )
        if not ids:
            print(f"{state}: no election on {DAY}")
            continue
        version = get(client, f"{base}{ids[0]}/current_ver.txt")
        content = version and get(client, f"{base}{ids[0]}/{version.decode().strip()}/reports/detailxml.zip")
        if content is None:
            print(f"{state}: no detail file")
        else:
            store(state, f"{base}{ids[0]}/{version.decode().strip()}/reports/detailxml.zip", content, ".zip")
    if not only or "OK" in only:
        login = client.put(
            f"{OK_BASE}login/",
            json={"Username": "appuser", "Password": "X098de!22k098Mgfd"},
            headers=OK_HEADERS,
        )
        auth = {**OK_HEADERS, "Authorization": f"Bearer {login.json()[1:]}"}
        listing = get(client, f"{OK_BASE}getelec/{DAY:%Y%m%d}/SW/xx", auth)
        races = json.loads(listing or "{}").get("races") or []
        if not races:
            print("OK: no races")
        for race in races:
            url = f"{OK_BASE}GetCntyResults/{DAY:%Y%m%d}/{race['raceID']}"
            content = get(client, url, auth)
            if content:
                store(
                    "OK",
                    url,
                    json.dumps(
                        {
                            "race": race,
                            "lastUpdate": json.loads(listing)["lastUpdate"],
                            "results": json.loads(content),
                        }
                    ).encode(),
                    ".json",
                )
    if not only or "AR" in only:
        url = "https://enr-results-api.totalresults.com/Election/GetElectionList?cid=arkansas"
        listing = get(client, url, AR_HEADERS)
        ids = [e["electionID"] for e in json.loads(listing or "[]") if e["electionDate"][:10] == DAY.isoformat()]
        content = ids and get(
            client, f"https://results.tally-enr.com/results/arkansas/{ids[0]}/FullDataFile.json", AR_HEADERS
        )
        if not content:
            print("AR: no data file")
        else:
            store("AR", url, listing, ".json")
            store("AR", f"https://results.tally-enr.com/results/arkansas/{ids[0]}/FullDataFile.json", content, ".json")
    if not only or "CA" in only:
        from ea.sources.us import enr_ca

        enr_ca.collect(client, get, lambda url, content: store("CA", url, content, ".json"))
    if not only or "NV" in only:
        from ea.sources.us import enr_nv

        enr_nv.collect(client, get, lambda url, content: store("NV", url, content, ".html"))
    if not only or "TN" in only:
        from ea.sources.us import enr_tn

        enr_tn.collect(client, get, lambda url, content, suffix: store("TN", url, content, suffix))
    if not only or "KY" in only:
        from ea.sources.us import enr_ky

        enr_ky.collect(client, get, lambda url, content, suffix: store("KY", url, content, suffix))
    if not only or "MO" in only:
        from ea.sources.us import enr_mo

        enr_mo.collect(client, get, lambda url, content, suffix: store("MO", url, content, suffix))
    if not only or "WI" in only:
        from ea.sources.us import enr_wi

        enr_wi.collect(client, get, lambda url, content, suffix: store("WI", url, content, suffix))
    if not only or "NY" in only:
        from ea.sources.us import enr_ny

        enr_ny.collect(client, get, lambda url, content, suffix: store("NY", url, content, suffix))
    if not only or "NJ" in only:
        from ea.sources.us import enr_nj

        enr_nj.collect(client, get, lambda url, content, suffix: store("NJ", url, content, suffix))
    if not only or "VT" in only:
        from ea.sources.us import enr_vt

        enr_vt.collect(client, get, lambda url, content, suffix: store("VT", url, content, suffix))
    if not only or "DE" in only:
        from ea.sources.us import enr_de

        enr_de.collect(client, get, lambda url, content, suffix: store("DE", url, content, suffix))
    if not only or "WY" in only:
        from ea.sources.us import enr_wy

        enr_wy.collect(client, get, lambda url, content, suffix: store("WY", url, content, suffix))
    if not only or "AK" in only:
        from ea.sources.us import enr_ak

        enr_ak.collect(client, get, lambda url, content, suffix: store("AK", url, content, suffix))
    if not only or "AL" in only:
        from ea.sources.us import enr_al

        enr_al.collect(client, get, lambda url, content, suffix: store("AL", url, content, suffix))
    if not only or "HI" in only:
        from ea.sources.us import enr_hi

        enr_hi.collect(client, get, lambda url, content, suffix: store("HI", url, content, suffix))
    for state in ("NM", "SD", "ND", "NE", "MT"):
        if not only or state in only:
            from ea.sources.us import enr_ajax

            enr_ajax.collect(state, client, get, lambda url, content, suffix: store(state, url, content, suffix))
    if not only or "OH" in only:
        from ea.sources.us import enr_oh

        enr_oh.collect(client, get, lambda url, content: store("OH", url, content, ".xml"))
    if not only or "OR" in only:
        from ea.sources.us import enr_or

        enr_or.collect(client, get, lambda url, content: store("OR", url, content, ".json"))
    missing: list[str] = []
    for state, tenant, eid in [
        *((STATES[fips[:2]], tenant, None) for fips, (tenant, *_) in COUNTIES.items()),
        *LIVE_COUNTIES.values(),
    ]:
        if only and state not in only:
            continue
        base = f"https://results.enr.clarityelections.com/{state}/{tenant}/"
        listing = None if eid else get(client, f"{base}elections.json")
        ids = (
            [os.environ[f"ENR_2026_EID_{state}_{tenant}"]]
            if f"ENR_2026_EID_{state}_{tenant}" in os.environ
            else [eid]
            if eid
            else [
                e["EID"]
                for e in json.loads(listing or "[]")
                if datetime.strptime(e["Date"].split()[0], "%m/%d/%Y").date() == DAY
                and not re.search(r"TEST|MOCK|TRAINING", e["ElectionName"].upper())
            ]
        )
        version = ids and get(client, f"{base}{ids[0]}/current_ver.txt")
        content = version and get(client, f"{base}{ids[0]}/{version.decode().strip()}/reports/detailxml.zip")
        if content:
            store(state, f"{base}{ids[0]}/{version.decode().strip()}/reports/detailxml.zip", content, ".zip")
        else:
            missing.append(f"{state}/{tenant}")
    if missing:
        print(f"county Clarity sites with no {DAY} file: {len(missing)}", ", ".join(missing[:12]))
    if not only or "FL" in only:
        url = f"https://flelectionfiles.floridados.gov/enightfilespublic/{DAY:%Y%m%d}_ElecResultsFL.txt"
        content = get(client, f"{url}?v={int(time.time())}")
        if content is None or len(content.splitlines()) < 2:
            official = "Y" if DAY < date.today() - timedelta(days=7) else "N"
            url = f"https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate={DAY.month}/{DAY.day}/{DAY.year}&OfficialResults={official}"
            content = get(client, url)
        if content is None:
            print("FL: no extract")
        else:
            store("FL", url, content, ".txt")
    if not only or "IL" in only:
        election = IL_ELECTIONS[DAY.year]
        base = f"https://elections.il.gov/Downloads/ElectionOperations/ElectionResults/ByOffice/{election}/{election}-"
        files = [
            f"150-UNITED%20STATES%20SENATOR-{DAY.year}GE",
            f"180-GOVERNOR%20AND%20LIEUTENANT%20GOVERNOR-{DAY.year}GE",
            *(f"{office}-{DAY.year}GE" for office in IL_STATEWIDE),
        ]
        for number in range(1, 18):
            ordinal = (
                f"{number}{'TH' if 10 <= number % 100 <= 20 else {1: 'ST', 2: 'ND', 3: 'RD'}.get(number % 10, 'TH')}"
            )
            files.append(f"{240 + 10 * number}-{ordinal}%20CONGRESS-{DAY.year}GE")
        for name in files:
            content = get(client, f"{base}{name}.csv")
            if content is None:
                print(f"IL: no file {name}")
            else:
                store("IL", f"{base}{name}.csv", content, ".csv")
    if not only or "MN" in only:
        base = f"https://electionresultsfiles.sos.mn.gov/{DAY:%Y%m%d}/"
        for names in (("USSenateCty.txt",), ("USHouseCty.txt",), ("GovernorCty.txt", "GovernorPct.txt")):
            for name in names:
                content = get(client, base + name)
                if content is not None:
                    store("MN", base + name, content, ".txt")
                    break
            else:
                print(f"MN: no file {names[0]}")
    if not only or "LA" in only:
        listing_url = f"{LA_BASE}{DAY:%Y%m%d}/RacesCandidates_Multiparish.htm"
        listing = get(client, listing_url)
        if listing is None:
            print("LA: no race listing")
        else:
            store("LA", listing_url, listing, ".json")
            for race in json.loads(listing)["Races"]["Race"]:
                url = f"{LA_BASE}{DAY:%Y%m%d}/csv/ByParish_{race['ID']}.csv"
                content = get(client, url)
                if content is None:
                    print(f"LA: no file for race {race['ID']}")
                else:
                    store("LA", url, content, ".csv")
    if not only or "CT" in only:
        root = "https://ctemspublic.pcctg.net/ng-app/data/"
        listing = get(client, root + "Elections.json")
        if listing is None:
            print("CT: no election listing")
        else:
            store("CT", root + "Elections.json", listing, ".json")
            ids = [e["ID"] for e in json.loads(listing) if e["Name"].startswith(f"{DAY:%m/%d/%Y}")]
            if not ids:
                print(f"CT: no election on {DAY}")
            for id in ids:
                version = get(client, f"{root}election/{id}/Version.json")
                if version is None:
                    print(f"CT: no version for election {id}")
                    continue
                store("CT", f"{root}election/{id}/Version.json", version, ".json")
                base = f"{root}election/{id}/{json.loads(version)['Version']}/"
                for name in ("Lookupdata", "election_Electiondata", "townVotes_Electiondata"):
                    content = get(client, f"{base}{name}.json")
                    if content is None:
                        print(f"CT: no file {name} for election {id}")
                    else:
                        store("CT", f"{base}{name}.json", content, ".json")
    if not only or "KS" in only:
        for name in ("national.html", "statewide.html"):
            url = f"https://www.sos.ks.gov/ent/{name}"
            content = get(client, url)
            if content is None or b"race-details" not in content:
                print(f"KS: no file {name}")
            else:
                store("KS", url, content, ".html")
    if not only or "MD" in only:
        base = f"https://elections.maryland.gov/elections/{DAY.year}/general_results/"
        for name in (
            f"gen_detail_results_{DAY.year}_1_1.html",
            *(f"gen_detail_results_{DAY.year}_4_{n}_District%20{n}.html" for n in range(1, 9)),
        ):
            content = get(client, base + name)
            if content is None or b"Vote for" not in content:
                print(f"MD: no file {name}")
            else:
                store("MD", base + name, content, ".html")
    if not only or "NC" in only:
        url = f"https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{DAY:%Y_%m_%d}/results_pct_{DAY:%Y%m%d}.zip"
        content = get(client, url)
        if content is None:
            print("NC: no zip")
        else:
            store("NC", url, content, ".zip")
    if not only or "IN" in only:
        for base in (
            "https://enr.indianavoters.in.gov/site/data/",
            f"https://enr.indianavoters.in.gov/archive/{DAY.year}General/data/",
        ):
            settings = get(client, f"{base}settings.json")
            found = json.loads(settings.decode("utf-8-sig"))["Root"] if settings else {}
            if found.get("CurrentElection") == f"{DAY:%m/%d/%Y}" and found.get("ElectionType") == "G":
                url = f"{base}OffCatC_1005_{found['VersionType']}.json"
                content = get(client, url)
                if content is not None:
                    store("IN", url, content, ".json")
                break
        else:
            print("IN: no general settings")
    if not only or "PA" in only:
        if DAY not in PA_ELECTION:
            listing = get(client, f"{PA_API}GetAllElections?methodName=GetAllElections")
            for entry in json.loads(json.loads(json.loads(listing))[0]["ElectionData"]) if listing else []:
                if entry["ElectionType"] == "G" and entry["ElectionDate"] == f"{DAY:%m/%d/%Y}":
                    PA_ELECTION[DAY] = f"electionid={entry['Electionid']}&electiontype=G&isactive={entry['ISActive']}"
        election = PA_ELECTION.get(DAY)
        if election is None:
            print("PA: no general election id")
        for office in PA_OFFICES if election else ():
            url = f"{PA_API}GetOfficeData?officeId={office}&methodName=GetOfficeData&{election}"
            content = get(client, url)
            if not content or content == b'""':
                continue
            store("PA", url, content, ".json")
            for district in re.findall(r"\$\$(\d+)\$\$", json.loads(content)):
                url = f"{PA_API}GetCountyBreak?officeId={office}&districtId={district}&methodName=GetCountyBreak&{election}"
                detail = get(client, url)
                if detail:
                    store("PA", url, detail, ".json")
    if not only or "TX" in only:
        from ea.sources.us import enr_tx

        enr_tx.collect(client, store)
    if not only or "MI" in only:
        from ea.sources.us import enr_mi

        enr_mi.collect(client, store)


def ev_seat(state: str, name: str) -> dict[str, str]:
    name = name.strip()
    found = EV_OFFICES.fullmatch(office_name(name))
    seat = TN_LEGISLATURE.fullmatch(name) if state == "TN" else None
    return {
        "office": ("senate" if seat[1] == "Senate" else "house")
        if seat
        else ("state" if state == "TN" and TN_AMENDMENT.fullmatch(name) else "other")
        if found is None
        else "senate"
        if found["senate"]
        else "governor"
        if found["governor"]
        else "house",
        "contest": name if found is None and seat is None else "",
        "district": f"{seat[2]}:g" if seat else (found["district"] if found else None) or "",
        "scope": (found["party0"] or found["party1"] or "") if found else "",
    }


def ev_export(state: str, path: Path):
    with zipfile.ZipFile(path) as archive:
        export = json.loads(archive.read("export.json"))
    localities = export["localResults"] or [export["results"]]
    names = {item["id"]: item["name"] for item in (export["results"] or {}).get("ballotItems") or []}
    precincts: dict[str, list[int]] = {}
    for locality in localities:
        for item in locality["ballotItems"]:
            tally = precincts.setdefault(item["id"], [0, 0])
            tally[0] += item["precinctsReporting"] or 0
            tally[1] += item["precinctsParticipating"] or 0
    for locality in localities:
        county = (
            f"51{locality['id']}"
            if state == "VA"
            else town_counties("RI").get(
                plain(re.sub(r"\s+(city|town)$", "", locality["name"].lower())), locality["name"]
            )
            if state == "RI"
            else locality["name"].removesuffix(" County")
        )
        for item in locality["ballotItems"]:
            name = names.get(item["id"], item["name"])
            if EV_LOCAL.match(name):
                continue
            seat = ev_seat(state, name)
            reporting, total = precincts[item["id"]]
            for option in item["ballotOptions"]:
                yield {
                    **seat,
                    "party": option["politicalParty"] or "",
                    "county": county,
                    "candidate": TAGS.sub("", option["name"]).strip(),
                    "write_in": bool(WRITE_IN.search(option["name"])),
                    "votes": int(option["voteCount"] or 0),
                    "as_of": export["createdAt"],
                    "reporting": reporting,
                    "total": total,
                }


def rows(state: str, url: str, path: Path, urls: dict[str, Path]):
    if url.startswith("https://liveresults.ohiosos.gov/"):
        from ea.sources.us import enr_oh

        yield from enr_oh.rows(url, path, urls)
        return
    if state == "OR":
        from ea.sources.us import enr_or

        yield from enr_or.rows(url, path, urls)
        return
    if state == "TN" and url.endswith("/data.js"):
        from ea.sources.us import enr_tn

        yield from enr_tn.rows(url, path)
        return
    if state == "KY":
        from ea.sources.us import enr_ky

        yield from enr_ky.rows(url, path)
        return
    if state == "MI" and url.startswith(
        ("https://app.enhancedvoting.com/", "https://michigan.totalvote.com/", "https://electionresults.ewashtenaw.org/")
    ):
        from ea.sources.us import enr_mi

        yield from enr_mi.rows(url, path)
        return
    if state == "TX" and not url.endswith("detailxml.zip"):
        from ea.sources.us import enr_tx

        yield from enr_tx.rows(url, path, urls)
        return
    if state == "NV":
        from ea.sources.us import enr_nv

        yield from enr_nv.rows(url, path, urls)
        return
    if state == "MO":
        from ea.sources.us import enr_mo

        yield from enr_mo.rows(url, path, urls)
        return
    if state == "WI":
        from ea.sources.us import enr_wi

        yield from enr_wi.rows(url, path, urls)
        return
    if state == "VT":
        from ea.sources.us import enr_vt

        yield from enr_vt.rows(url, path, urls)
        return
    if state == "DE":
        from ea.sources.us import enr_de

        yield from enr_de.rows(url, path, urls)
        return
    if state == "WY":
        from ea.sources.us import enr_wy

        yield from enr_wy.rows(url, path, urls)
        return
    if state == "AK":
        from ea.sources.us import enr_ak

        yield from enr_ak.rows(url, path, urls)
        return
    if state == "AL" and "alabamavotes.gov" in url:
        from ea.sources.us import enr_al

        yield from enr_al.rows(url, path)
        return
    if state == "HI":
        from ea.sources.us import enr_hi

        yield from enr_hi.rows(url, path, urls)
        return
    if state in ("NM", "SD", "ND", "NE", "MT"):
        from ea.sources.us import enr_ajax

        yield from enr_ajax.rows(url, path)
        return
    if state == "NJ":
        from ea.sources.us import enr_nj

        yield from enr_nj.rows(url, path)
        return
    if state == "NY":
        from ea.sources.us import enr_ny

        yield from enr_ny.rows(url, path, urls)
        return
    if url.endswith("detailxml.zip"):
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("detail.xml"))
        stamped = re.fullmatch(r"(.+) ([ECMP])([SD])T", (root.findtext("Timestamp") or "").strip())
        tenant = TENANT.search(url)
        as_of = (
            datetime.strptime(stamped[1], "%m/%d/%Y %I:%M:%S %p")
            .replace(
                tzinfo=timezone(timedelta(hours={"E": -5, "C": -6, "M": -7, "P": -8}[stamped[2]] + (stamped[3] == "D")))
            )
            .isoformat()
            if stamped
            else None
        )
        for contest in root.iter("Contest"):
            found = EV_OFFICES.fullmatch(re.sub(r"\s+", " ", contest.get("text") or "").strip())
            if tenant and state != "MI" and (found is None if state == "TX" else found is not None):
                continue
            for choice in contest.iter("Choice"):
                for kind in choice.iter("VoteType"):
                    for place in kind:
                        yield {
                            "office": "other"
                            if found is None
                            else "senate"
                            if found["senate"]
                            else "governor"
                            if found["governor"]
                            else "house",
                            "contest": (contest.get("text") or "").strip() if found is None else "",
                            "district": (found["district"] if found else None) or "",
                            "party": choice.get("party") or "",
                            "county": tenant[1].replace("_", " ")
                            if tenant
                            else (place.get("name") or "").removesuffix(" County"),
                            "candidate": re.sub(r"\s*(\(I\)|\*)$", "", choice.get("text") or "").strip(),
                            "write_in": "write-in" in (choice.get("text") or "").lower(),
                            "votes": int(place.get("votes") or 0),
                            "as_of": as_of,
                            "reporting": int(contest.get("precinctsReported") or 0),
                            "total": int(contest.get("precinctsParticipating") or 0),
                            "scope": (found["party0"] or found["party1"] or "") if found else "",
                        }
        return
    if state == "CA":
        from ea.sources.us import enr_ca

        yield from enr_ca.rows(url, path, urls)
        return
    if state == "FL":
        seen = set()
        content = path.read_bytes()
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp1252")
        for row in csv.DictReader(text.splitlines(), delimiter="\t"):
            office = FL_OFFICES.get(row["RaceCode"])
            key = (
                row["RaceCode"],
                row["PartyCode"],
                row["Juris1num"],
                row["CountyCode"],
                row["CanNameLast"],
                row["CanNameFirst"],
                row["CanNameMiddle"],
            )
            if key in seen:
                continue
            seen.add(key)
            yield {
                "office": office or "other",
                "contest": ""
                if office
                else " ".join(
                    part
                    for part in (
                        row.get("OfficeDesc") or row.get("RaceName"),
                        row["Juris1num"].strip(),
                        row["Juris2num"].strip(),
                    )
                    if part
                ),
                "district": row["Juris1num"].strip().lstrip("0"),
                "party": row["PartyName"],
                "county": row["CountyName"],
                "candidate": " ".join(
                    part
                    for part in (row["CanNameFirst"], row["CanNameMiddle"], row["CanNameLast"])
                    if part and part != "/"
                ),
                "write_in": row["PartyCode"] == "WRI",
                "votes": int(row["CanVotes"] or 0),
                "as_of": None,
                "reporting": int(row["PrecinctsReporting"] or 0),
                "total": int(row["Precincts"] or 0),
            }
        return
    if state == "IL":
        for row in csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()):
            found = IL_CONTEST.fullmatch(row["ContestName"].strip())
            if row["EISCandidateID"] == "0":
                continue
            yield {
                "office": "state"
                if found is None
                else "senate"
                if "SENATOR" in row["ContestName"]
                else "governor"
                if found[1] is None
                else "house",
                "contest": row["ContestName"].strip() if found is None else "",
                "district": (found[1] if found else None) or "",
                "party": row["PartyName"].title(),
                "county": IL_CITIES.get(row["JurisName"], row["JurisName"].title()),
                "candidate": row["CandidateName"].strip(),
                "write_in": "write-in" in row["CandidateName"].lower(),
                "votes": int(row["VoteCount"] or 0),
                "as_of": None,
                "reporting": None,
                "total": None,
            }
        return
    if state == "LA":
        table = csv.reader(path.read_text(encoding="utf-8-sig").splitlines())
        header = next(table)
        for row in table:
            found = LA_RACE.fullmatch(row[0].strip())
            for label, votes in zip(header[2:], row[2:]):
                named = re.fullmatch(r"(.*?)\s*\((\w+)\)", label.strip())
                yield {
                    "office": "other" if found is None else "senate" if found["senate"] else "house",
                    "contest": row[0].strip() if found is None else "",
                    "district": (found["district"] if found else None) or "",
                    "party": named[2] if named else "",
                    "county": row[1],
                    "candidate": named[1] if named else label.strip(),
                    "write_in": "write-in" in label.lower(),
                    "votes": int(votes or 0),
                    "as_of": None,
                    "reporting": None,
                    "total": None,
                    "scope": (found["party"] if found else None) or "",
                }
        return
    if state == "MN":
        totals: dict[tuple[str, str, str, str, str], list[int]] = {}
        for line in path.read_text(encoding="latin-1").splitlines():
            cells = [cell.strip().strip('"').strip() for cell in line.split(";")]
            found = MN_CONTEST.fullmatch(cells[4]) if len(cells) > 13 else None
            if found is None:
                continue
            key = (
                f"27{2 * int(cells[1]) - 1:03}",
                MN_OFFICES[found[1]],
                found[2] or "",
                cells[10],
                re.sub(r"\s+", " ", cells[7].split(" and ")[0]),
            )
            tally = totals.setdefault(key, [0, 0, 0])
            tally[0] += int(cells[13] or 0)
            if url.endswith("Cty.txt"):
                tally[1], tally[2] = int(cells[11] or 0), int(cells[12] or 0)
        for (county, office, district, party, candidate), (votes, reporting, total) in totals.items():
            yield {
                "office": office,
                "district": district,
                "party": party,
                "county": county,
                "candidate": candidate,
                "write_in": candidate.upper().startswith("WRITE-IN"),
                "votes": votes,
                "as_of": None,
                "reporting": reporting or None,
                "total": total or None,
            }
        return
    if state == "CT":
        if not url.endswith("townVotes_Electiondata.json"):
            return
        base = url.rsplit("/", 1)[0]
        lookup = json.loads(urls[f"{base}/Lookupdata.json"].read_bytes())
        status = json.loads(urls[f"{base}/election_Electiondata.json"].read_bytes())
        counted = re.match(r"(\d+) of (\d+)", status["PR"])
        regions = ct_regions()
        region = {
            entry["TownID"]: regions[re.sub(r"[^a-z]", "", entry["TownName"].lower())]
            for entry in lookup["countyTowns"]
        }
        offices = {key: value["NM"] for entry in lookup["officeList"] for key, value in entry.items()}
        for town, contests in json.loads(path.read_bytes()).items():
            for office_id, results in contests.items():
                found = CT_OFFICE.fullmatch(offices[office_id])
                for result in results:
                    for candidate_id, tally in result.items():
                        candidate = lookup["candidateIds"][candidate_id]
                        yield {
                            "office": "state"
                            if found is None
                            else "house"
                            if found[1]
                            else "senate"
                            if offices[office_id].startswith("United")
                            else "governor",
                            "contest": offices[office_id] if found is None else "",
                            "district": (found[1] if found else None) or "",
                            "party": lookup["partyIds"].get(candidate["P"], {}).get("NM", "").removesuffix(" Party"),
                            "county": region[town],
                            "candidate": candidate["NM"].split(" and ")[0],
                            "write_in": lookup["partyIds"].get(candidate["P"], {}).get("NM") == "Write In",
                            "votes": int(tally["V"]),
                            "as_of": None,
                            "reporting": int(counted[1]),
                            "total": int(counted[2]),
                        }
        return
    if state == "KS":
        text = path.read_text(encoding="utf-8")
        stamp = re.search(r"id = 'time-stamp' >\s*([^<]*?)\s*<", text)
        as_of = (
            datetime.strptime(stamp[1], "%m/%d/%Y %I:%M %p").replace(tzinfo=ZoneInfo("America/Chicago")).isoformat()
            if stamp
            else None
        )
        for block in text.split("<div class='race'>")[1:]:
            title = re.fullmatch(r"(.*?)(?: \((\w)\))?", html.unescape(re.search(r"<h3>(.*?)</h3>", block)[1]).strip())
            found = KS_CONTEST.fullmatch(title[1])
            if title[2]:
                continue
            counted = re.search(r"Precincts Reporting: (\d+) of (\d+)", block)
            for county in re.findall(r"<area [^>]*title=\"(.*?)\" />", block, re.DOTALL):
                tooltip = html.unescape(county)
                for label, votes in re.findall(
                    r"<td style='padding-right:15px'>(.*?)</td><td style='text-align:right'>([\d,]+)</td>", tooltip
                ):
                    named = re.fullmatch(r"([A-Z]{1,3})-(.*)", html.unescape(label).strip())
                    yield {
                        "office": "state"
                        if found is None
                        else "senate"
                        if found["senate"]
                        else "governor"
                        if found["governor"]
                        else "house",
                        "contest": title[1] if found is None else "",
                        "district": (found["district"] if found else None) or "",
                        "party": named[1] if named else "",
                        "county": re.search(r"<span class='county'>(.*?) County</span>", tooltip)[1],
                        "candidate": named[2] if named else html.unescape(label).strip(),
                        "write_in": False,
                        "votes": int(votes.replace(",", "")),
                        "as_of": as_of,
                        "reporting": int(counted[1]) if counted else None,
                        "total": int(counted[2]) if counted else None,
                    }
        return
    if state == "MD":
        text = path.read_text(encoding="utf-8")
        office = re.search(r"<h2>\s*([^<]*?)\s*</h2>", text)[1]
        district = re.search(r"<h3>\s*District (\d+)", text)
        refreshed = re.search(r"Last refreshed: (\S+ \S+ [AP]M)", text)
        counted = re.search(r"\((\d+) of (\d+) election day precincts reported\)", text)
        as_of = (
            datetime.strptime(refreshed[1], "%m/%d/%Y %I:%M:%S %p")
            .replace(tzinfo=ZoneInfo("America/New_York"))
            .isoformat()
            if refreshed
            else None
        )
        for table in re.findall(r"<table.*?</table>", text, re.DOTALL):
            heads = []
            for head in re.findall(r"<th[^>]*scope=\"col\"[^>]*>(.*?)</th>", table, re.DOTALL)[1:]:
                parts = [
                    part for part in (re.sub(r"<[^>]*>", "", p).strip() for p in re.split(r"<br\s*/?>", head)) if part
                ]
                heads.append((parts[0].split(" and ")[0], parts[-1] if len(parts) > 1 else "", "(Write In)" in parts))
            for county_row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL):
                cells = [
                    re.sub(r"<[^>]*>", "", cell).strip()
                    for cell in re.findall(r"<td[^>]*>(.*?)</td>", county_row, re.DOTALL)
                ]
                if not cells or cells[0].startswith("Total"):
                    continue
                for (name, party, write_in), votes in zip(heads, cells[1:]):
                    yield {
                        "office": "governor" if office.startswith("Governor") else "house",
                        "district": district[1] if district else "",
                        "party": party,
                        "county": "24510" if cells[0] == "Baltimore City" else cells[0],
                        "candidate": name,
                        "write_in": write_in or name.startswith("Other Write-Ins"),
                        "votes": int(votes.replace(",", "")) if votes[:1].isdigit() else 0,
                        "as_of": as_of,
                        "reporting": int(counted[1]) if counted else None,
                        "total": int(counted[2]) if counted else None,
                    }
        return
    if state == "NC":
        totals: dict[tuple[str, str, str, str], int] = {}
        with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as handle:
            for row in csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8"), delimiter="\t"):
                key = (row["Contest Name"].strip(), row["County"], row["Choice"].strip(), row["Choice Party"].strip())
                totals[key] = totals.get(key, 0) + int(row["Total Votes"] or 0)
        for (contest, county, choice, party), votes in totals.items():
            found = NC_CONTEST.fullmatch(contest)
            yield {
                "office": "other"
                if found is None
                else "house"
                if found[1]
                else "senate"
                if contest == "US SENATE"
                else "governor",
                "contest": contest if found is None else "",
                "district": ((found[1] if found else None) or "").lstrip("0"),
                "party": party,
                "county": county.title(),
                "candidate": choice,
                "write_in": choice.lower().startswith("write-in"),
                "votes": votes,
                "as_of": None,
                "reporting": None,
                "total": None,
            }
        return
    if state == "IN":
        for region in json.loads(path.read_bytes().decode("utf-8-sig"))["Root"]["OfficeCategory"]["Regions"]["Region"]:
            races = region["Races"]["Race"]
            for race in races if isinstance(races, list) else [races]:
                for candidate in race["Candidates"]["Candidate"]:
                    yield {
                        "office": "house",
                        "district": region["MAP_JURISDICTION_NAME"],
                        "party": candidate["PARTY_ABBREV"],
                        "county": race["Jurisdiction"]["FIPS"],
                        "candidate": re.sub(r"\s*\(W/I\)$", "", candidate["NAME_ON_BALLOT"]),
                        "write_in": "(W/I)" in candidate["NAME_ON_BALLOT"],
                        "votes": int(candidate["TOTAL_VOTES"] or 0),
                        "as_of": None,
                        "reporting": None,
                        "total": None,
                    }
        return
    if state == "PA":
        found = re.search(r"officeId=(\d+)&districtId=\d+&methodName=GetCountyBreak", url)
        if found is None:
            return
        for district, counties in json.loads(json.loads(path.read_bytes()))["Election"].items():
            number = re.match(r"(\d+)", district)
            for county in counties:
                for name, candidates in county.items():
                    for candidate in candidates:
                        yield {
                            "office": PA_OFFICES[found[1]],
                            "district": number[1] if number else "",
                            "party": candidate["PartyName"],
                            "county": name.title(),
                            "candidate": candidate["CandidateName"].strip(),
                            "write_in": "write-in" in candidate["CandidateName"].lower(),
                            "votes": int(candidate["Votes"] or 0),
                            "as_of": None,
                            "reporting": None,
                            "total": None,
                        }
        return
    if state == "OK":
        stored = json.loads(path.read_bytes())
        race = stored["race"]
        found = OK_RACE.fullmatch(race["raceTitle"])
        for county in stored["results"]["summaryCandResults"]:
            for candidate, result in zip(race["raceCandidates"], county["candResults"], strict=True):
                yield {
                    "office": "state"
                    if found is None
                    else "senate"
                    if found[1] == "UNITED STATES SENATOR"
                    else "governor"
                    if found[1] == "GOVERNOR"
                    else "house",
                    "contest": race["raceTitle"].strip() if found is None else "",
                    "district": ((found[2] if found else None) or "").lstrip("0"),
                    "party": race["raceTitle2"],
                    "county": f"40{2 * int(county['countyID']) - 1:03}",
                    "candidate": TAGS.sub("", candidate["candName"]).title(),
                    "write_in": False,
                    "votes": int(result["totalVotes"]),
                    "as_of": updated.isoformat()
                    if (updated := datetime.fromisoformat(stored["lastUpdate"])).tzinfo
                    else updated.replace(tzinfo=ZoneInfo("America/Chicago")).isoformat(),
                    "reporting": None,
                    "total": None,
                    "scope": race["raceTitle2"]
                    if race["raceTitle2"] in ("Republican", "Democrat", "Libertarian")
                    else "",
                }
        return
    if state == "AR":
        for contest in json.loads(path.read_bytes())["ContestData"]:
            found = AR_CONTEST.fullmatch(contest["ContestName"])
            places = contest.get("Locations") or {contest.get("DistrictID") or "": contest}
            for fips, location in places.items():
                for choice in [*location["Choices"], *location["WriteinChoices"]]:
                    named = re.fullmatch(r"([A-Z]{3}) (.+)", choice["Name"])
                    yield {
                        "office": "other"
                        if found is None
                        else "senate"
                        if contest["ContestName"] == "U.S. Senate"
                        else "governor"
                        if found[1] is None
                        else "house",
                        "contest": contest["ContestName"] if found is None else "",
                        "district": ((found[1] if found else None) or "").lstrip("0"),
                        "party": named[1] if named else "",
                        "county": fips,
                        "candidate": named[2] if named else choice["Name"],
                        "write_in": "write-in" in choice["Name"].lower(),
                        "votes": choice["TotalVotes"]
                        if "TotalVotes" in choice
                        else sum(vote["Votes"] for vote in choice["Votes"]),
                        "as_of": None,
                        "reporting": contest.get("PrecinctsReporting"),
                        "total": contest.get("TotalPrecincts"),
                    }
        return
    if "/cdn/results/" in url:
        yield from ev_export(state, path)


def parse() -> None:
    pages = json.loads((PARSED / "pages.json").read_text())
    frame = table(
        {state: {url: raw.cached(SOURCE, key).path for url, key in urls.items()} for state, urls in pages.items()}
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "results.parquet")
    print(frame.group_by("state").agg(pl.len(), pl.col("votes").sum()).sort("state"))


def table(pages: dict[str, dict[str, Path]]) -> pl.DataFrame:
    found = []
    for state, urls in pages.items():
        for url, path in urls.items():
            if url.endswith(LISTINGS):
                continue
            for row in rows(state, url, path, urls):
                found.append({"state": state, "page": url, "scope": "", "contest": "", **row})
    return pl.DataFrame(
        found,
        schema={
            "state": pl.String,
            "page": pl.String,
            "scope": pl.String,
            "contest": pl.String,
            "office": pl.String,
            "district": pl.String,
            "party": pl.String,
            "county": pl.String,
            "candidate": pl.String,
            "write_in": pl.Boolean,
            "votes": pl.Int64,
            "as_of": pl.String,
            "reporting": pl.Int64,
            "total": pl.Int64,
        },
    )


def countywide(key: str) -> bool:
    rest = key.split(":")[2:]
    return rest[0].isdigit() or rest[-1] == "g" or any("judicial" in part for part in rest)


def plan(
    conn: Connection, frame: pl.DataFrame, scatter: Callable[[str, UUID], UUID | None]
) -> tuple[list[tuple[str, str, UUID, dict[tuple[UUID, UUID], int], tuple[int, int], str | None]], list[str]]:
    frame = frame.filter(~pl.col("candidate").fill_null("").str.contains(NOBODY)).with_columns(
        candidate=pl.when(pl.col("party").is_not_null() & pl.col("candidate").str.starts_with(pl.col("party") + " "))
        .then(pl.col("candidate").str.slice(pl.col("party").str.len_chars() + 1))
        .otherwise(pl.col("candidate"))
    )
    tenant = pl.col("page").str.contains(TENANT.pattern)
    race = pl.concat_str(
        [pl.col(name).fill_null("") for name in ("state", "office", "contest", "district", "county")], separator="|"
    )
    frame = (
        frame.with_columns(key=race, tenant=tenant)
        .join(
            frame.with_columns(key=race, tenant=tenant)
            .group_by("key", "tenant")
            .agg(pl.col("votes").sum())
            .sort(["votes", "tenant"], descending=[True, False])
            .unique("key", keep="first", maintain_order=True)
            .select("key", "tenant"),
            on=["key", "tenant"],
        )
        .drop("key", "tenant")
    )
    local = frame.filter(pl.col("office").is_in(["other", "state"]))
    frame = frame.filter(~pl.col("office").is_in(["other", "state"]))
    counties: dict[tuple[str, str], UUID] = {}
    codes: dict[tuple[str, str], str] = {}
    for code, text, id in conn.execute(
        """select u.code, n.text, u.id from reporting_unit u
           join name n on n.entity_id = u.area_id and n.kind = 'short'
           where u.scheme_code = 'us.county' and u.code ~ %s order by u.code""",
        [f"^({'|'.join(FIPS.values())})"],
    ).fetchall():
        counties.setdefault((code[:2], plain(text)), id)
        counties[(code[:2], code)] = id
        codes.setdefault((code[:2], plain(text)), code)
    for code, id in conn.execute(
        "select code, id from reporting_unit where scheme_code in ('us.sldl', 'us.sldu') and code like 'AK:%'"
    ).fetchall():
        counties[("02", code)] = id
    for code, id in conn.execute("select code, id from reporting_unit where scheme_code = 'us.state'").fetchall():
        counties[(code, code)] = id
    unmatched: list[str] = []
    work: list[tuple[str, str, UUID, pl.DataFrame, UUID | None]] = []
    local_keys: set[str] = set()
    for (state, office, district), group in frame.group_by("state", "office", "district", maintain_order=True):
        contest_key = f"{DAY}:{state}:{office}" + (f":{district}" if district else "")
        found = conn.execute(
            "select entity_id from external_id where scheme = 'us.contest' and value = %s limit 1", [contest_key]
        ).fetchone()
        if found is None:
            unmatched.append(f"{contest_key}: no contest")
            continue
        whole = (
            conn.execute(
                """select u.id from reporting_unit u
                   where u.area_id = (select district_area_id from contest where id = %s)
                   order by u.scheme_code limit 1""",
                [found[0]],
            ).fetchone()
            if set(group["county"]) == {FIPS[state]}
            else None
        )
        work.append((state, contest_key, found[0], group, whole[0] if whole else None))
    for state in local["state"].unique(maintain_order=True):
        published: dict[UUID, tuple[list[str], set[str]]] = {}
        titles: dict[UUID, set[str]] = {}
        held: dict[UUID, tuple[UUID | None, datetime]] = {}
        statewide: set[UUID] = set()
        judicial: set[UUID] = set()
        courts: set[UUID] = set()
        federal: dict[UUID, set[str]] = {}
        for key, id, area, recorded, kind in conn.execute(
            """select x.value, x.entity_id, c.district_area_id, c.recorded_from, p.kind
               from external_id x
               join contest c on c.id = x.entity_id
               join election e on e.id = c.election_id
               left join body b on b.id = c.body_id
               left join office o on o.id = c.office_id
               join polity p on p.id = coalesce(b.polity_id, o.polity_id, e.polity_id)
               where x.scheme = 'us.contest' and x.value like %s""",
            [f"{DAY}:{state}:%"],
        ).fetchall():
            if re.fullmatch(rf"{DAY}:{state}:(house:\d+|senate|governor)", key):
                federal.setdefault(id, set())
            else:
                published.setdefault(id, ([], set()))[0].append(key)
                held[id] = (area, recorded)
                if kind in ("federal_unit", "judicial_district"):
                    statewide.add(id)
                if kind == "judicial_district" or ":judicial-district-" in key:
                    judicial.add(id)
                elif re.search(r"judge|justice|court|chancellor", key.split(":")[2]):
                    courts.add(id)
        for contest, label in conn.execute(
            """select o.contest_id, coalesce(case when o.kind = 'answer' then o.ballot_label end, ca.ballot_name, o.ballot_label, '')
               from contest_option o
               left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
               where o.parent_option_id is null and o.kind <> 'write_in' and o.contest_id = any(%s)
                 and (o.kind = 'answer' or not exists (
                   select from contest_option a where a.contest_id = o.contest_id and a.kind = 'answer' and a.parent_option_id is null))""",
            [list(published) + list(federal)],
        ).fetchall():
            if not WRITE_IN.search(label):
                (federal[contest] if contest in federal else published[contest][1]).add(person(label))
        seats: dict[UUID, tuple[str, str]] = {}
        heads: dict[UUID, set[str]] = {}
        for contest, text in conn.execute(
            "select entity_id, text from name where entity_id = any(%s)", [list(published)]
        ).fetchall():
            titles.setdefault(contest, set()).update(slug(text).split("-"))
            if ":" in text:
                heads.setdefault(contest, set()).update(
                    w for w in slug(text.split(":")[0]).split("-") if not w.isdigit() and w not in STOP | GENERIC
                )
            if found := SEAT.search(text):
                seats[contest] = (found[1] or found[2], found[3] or "")
        twins: dict[tuple[UUID | None, frozenset[str], frozenset[str]], list[UUID]] = {}
        for contest, (_, wanted) in published.items():
            if len(wanted) > 1 and not wanted <= {"yes", "no"}:
                twins.setdefault(
                    (held[contest][0], frozenset(wanted), frozenset(titles.get(contest, set()))), []
                ).append(contest)
        for same in twins.values():
            for contest in sorted(same, key=lambda id: held[id][1])[:-1]:
                del published[contest]
        towns: dict[str, list[list[str]]] = {}
        schools: dict[str, set[str]] = {}
        for owner, text, scheme in conn.execute(
            """select n.entity_id::text, n.text, null from name n where n.entity_id = any(%s)
               union all
               select u.code, n.text, u.scheme_code from reporting_unit u join name n on n.entity_id = u.area_id
               where u.scheme_code in ('us.place', 'us.school_district') and u.code = any(%s)""",
            [
                list({held[contest][0] for contest in published if held[contest][0] is not None}),
                list({key.split(":")[2] for keys, _ in published.values() for key in keys}),
            ],
        ).fetchall():
            if scheme == "us.school_district":
                schools.setdefault(owner, set())
            towns.setdefault(owner, []).append(
                [w for w in slug(text).split("-") if len(w) > 2 and w not in STOP | PLACE_WORDS]
            )
        for code, county in conn.execute(
            """select u.code, c.code from reporting_unit u
               join area_geometry g on g.area_id = u.area_id and g.valid @> current_date
               join reporting_unit c on c.scheme_code = 'us.county' and c.code like %s
               join area_geometry cg on cg.area_id = c.area_id and cg.valid @> current_date and cg.geom && g.geom
               where u.scheme_code = 'us.school_district' and u.code = any(%s)
                 and st_area(st_intersection(g.geom, cg.geom)) > 0.02 * st_area(g.geom)""",
            [FIPS[state] + "%", list(schools)],
        ).fetchall():
            schools[code].add(county)
        for code, county in SCHOOL_COUNTIES.items():
            if code in schools:
                schools[code] = set(county)
        races: dict[tuple[str, str, frozenset[str]], list[str]] = {}
        for (office, name, county), group in local.filter(pl.col("state") == state).group_by(
            "office", "contest", "county"
        ):
            people = frozenset(
                person(candidate)
                for candidate in group.filter(~pl.col("write_in"))["candidate"].unique()
                if candidate and not WRITE_IN.search(candidate) and not NO_CANDIDATE.match(candidate)
            )
            races.setdefault((office, name, people), []).append(county)
        matched = {"other": 0, "state": 0}
        picks = []

        def choose(
            people: frozenset[str],
            words: set[str],
            places: list[str],
            wide: bool,
            seat: tuple[str, str] | None = None,
            lead: str | None = None,
        ):
            scored = []
            for contest, key, wanted in (
                (contest, key, wanted) for contest, (keys, wanted) in published.items() for key in keys
            ):
                segment = key.split(":")[2]
                named_seat = {w for w in re.split(r"[:-]", key)[3:] if w.isdigit()}
                named = district_words(key)
                judge = {w for w in key.split(":")[-1].split("-") if len(w) > 1}
                if wide:
                    if contest not in statewide or (seat and seats.get(contest, seat) != seat):
                        continue
                    if people <= {"yes", "no"} and (
                        words & {"retain", "retained", "retention"}
                        and re.search(r":(question|measure):", key)
                        or (contest in judicial or contest in courts)
                        and len(judge & words) < min(2, len(judge), len(words))
                        and not (
                            seat and seats.get(contest) == seat and COURTS & set(segment.split("-")) == COURTS & words
                        )
                    ):
                        continue
                elif (
                    re.fullmatch(r"\d{5}|\d{10}", segment)
                    and (len(places) != 1 or codes.get((FIPS[state], plain(places[0])), places[0]) != segment[:5])
                    and not (named and named <= words)
                    and "regional-superintendent" not in key
                    and places != [FIPS[state]]
                    or people <= {"yes", "no"}
                    and ":measure:" in key
                    and named
                    and not named & words
                    or len(places) == 1
                    and not re.match(r"\d|sd|local$|house$|senate$", segment)
                    and contest not in judicial
                    and not (contest in courts and (people - {"yes", "no"} or len(judge & words) >= 2))
                    or people <= {"yes", "no"}
                    and (contest in judicial or contest in courts)
                    and len(judge & words) < min(2, len(judge), len(words))
                    or people <= {"yes", "no"}
                    and places == [FIPS[state]]
                    and re.fullmatch(r"\d{5}|\d{10}", segment)
                    and not (
                        heads.get(contest, set()) & words
                        and (titles.get(contest, set()) - heads[contest]) & words - GENERIC
                    )
                    or re.fullmatch(r"\d{7}|\d\d-[a-z0-9-]+", segment)
                    and (names := towns.get(str(held[contest][0]), []) + towns.get(segment, []))
                    and len(people & wanted - {"yes", "no"}) < 2
                    and not any(
                        placed(
                            name,
                            words,
                            not people & wanted - {"yes", "no"},
                            segment in schools,
                            bool(schools.get(segment))
                            and {codes.get((FIPS[state], plain(place)), place)[:5] for place in places}.isdisjoint(
                                schools[segment]
                            ),
                        )
                        for name in names
                    )
                    and not (people & wanted - {"yes", "no"} and named_seat & {w for w in words if w.isdigit()})
                    and (
                        len({w for w in key.split(":")[-1].split("-") if len(w) > 3} & words)
                        < (6 if segment in schools else 5)
                    )
                ):
                    continue
                if ":measure:public-measure-" in key:
                    code = key.rsplit("public-measure-", 1)[1]
                    if code not in words or not (
                        titles.get(contest, set()) & words - GENERIC - {code, "public", "school", "community"}
                    ):
                        continue
                crowd = snap(people, wanted, words) if not wide else people
                overlap = len(crowd & wanted) / len(crowd | wanted) if crowd | wanted else 1
                if (
                    overlap < 0.6
                    and crowd
                    and crowd <= wanted
                    and not crowd <= {"yes", "no"}
                    and (re.match(r"\d|sd", segment) or contest in judicial)
                ):
                    overlap = 0.6
                if overlap >= 0.6:
                    parts = re.split(r"[:-]", key)[2:]
                    tokens = titles.get(contest, set()) | set(parts) | {a + b for a, b in itertools.pairwise(parts)}
                    found = words & tokens
                    found |= {w for w in words if stem(w) in {stem(t) for t in tokens}}
                    scored.append(
                        (
                            round(overlap, 2),
                            len({w for w in found - GENERIC if len(w) > 1 or w.isdigit()}),
                            len(found),
                            contest,
                            key,
                        )
                    )
            scored.sort(key=lambda hit: (-hit[0], -hit[1], -hit[2]))
            best = [hit for hit in scored if hit[:3] == scored[0][:3]] if scored else []
            if (
                len(best) > 1
                and len({frozenset(published[hit[3]][1]) for hit in best}) == 1
                and len(published[best[0][3]][1]) > 1
                and not published[best[0][3]][1] <= {"yes", "no"}
                and len({hit[4].split(":")[2] for hit in best}) == 1
            ):
                best = [max(best, key=lambda hit: held[hit[3]][1])]
            numbers = {lead} if lead else {w for w in words if w.isdigit()}
            if len(best) > 1 and numbers:
                kept = [
                    hit for hit in best if numbers & (titles.get(hit[3], set()) | set(re.split(r"[:-]", hit[4])[2:]))
                ]
                if len(kept) == 1 or not kept:
                    best = kept
            if len(best) > 1:
                homes = {codes.get((FIPS[state], plain(place)), place) for place in places}
                kept = [hit for hit in best if hit[4].split(":")[2][:5] in homes]
                if kept:
                    best = kept
            if len(best) == 1 and not ((people <= {"yes", "no"} or len(people) < 2) and best[0][1] < 1):
                return best, True
            return best, False

        for (office, name, people), places in races.items():
            numbered = re.match(r"\s*(?:proposition|prop)\.?\s+(\d+)\b", name, re.IGNORECASE)
            spelled = re.sub(
                rf"\b({'|'.join(TENS)})[- ]({'|'.join(ORDINALS)})\b",
                lambda found: f"{TENS[found[1]]}{ORDINALS[found[2]]}",
                (f"proposition {numbered[1]}" if numbered else name).lower(),
            )
            spelled = re.sub(rf"\b({'|'.join(ORDINALS)})\b", lambda found: ORDINALS[found[1]], spelled)
            words = {
                SYNONYMS.get(w, re.sub(r"^(?:p|pos|d|0+)(?=\d)|(?<=\d)(st|nd|rd|th)$", "", w))
                for w in slug(spelled).split("-")
                if w not in STOP
            }
            retention = state == "IL" and bool(
                re.search(r"^retain\b|^retention\b|\bjudge retention\b", name, re.IGNORECASE)
            )
            if retention:
                words = set(terms(name)[-1:])
            wide = office == "state" or bool(APPEALS.search(name)) or retention
            seat = (found[1] or found[2], found[3] or "") if wide and (found := SEAT.search(name)) else None
            lead = re.match(
                r"\s*(?:constitutional\s+)?(?:amendment|question)\s+(?:no\.?\s*)?(\d+)\s*:", name, re.IGNORECASE
            )
            best, ok = choose(people, words, places, wide, seat, lead[1] if lead else None)
            if not ok and retention and len(best) > 1:
                best, ok = choose(
                    people,
                    set(terms(name)) - {"retention", "retain", "judicial", "court"},
                    places,
                    wide,
                    seat,
                    lead[1] if lead else None,
                )
            if ok:
                picks.append((best[0], name, places, office))
                continue
            split = (
                [([place], choose(people, words, [place], wide, seat)) for place in places]
                if len(places) > 1 and not wide
                else []
            )
            if split and all(found[1] for _, found in split):
                picks.extend((found[0][0], name, place, office) for place, found in split)
                continue
            if wide and len(people) > 1 and not people <= {"yes", "no"}:
                parts = [
                    (contest, keys[0], wanted)
                    for contest, (keys, wanted) in published.items()
                    if contest in statewide
                    and wanted
                    and wanted <= people
                    and contest in seats
                    and (seat is None or seats[contest][0] == seat[0])
                ]
                if (
                    len({seats[contest][0] for contest, _, _ in parts}) == 1
                    and sum(len(wanted) for _, _, wanted in parts) == len(people)
                    and set().union(*(wanted for _, _, wanted in parts)) == people
                ):
                    picks.extend(
                        ((1.0, 0, 0, contest, key), name, places, office, wanted) for contest, key, wanted in parts
                    )
                    continue
            if (
                not wide
                and people - {"yes", "no"}
                and any(
                    len(people & wanted) >= 0.6 * len(people | wanted)
                    for wanted in itertools.chain(
                        federal.values(),
                        (
                            wanted
                            for contest, (keys, wanted) in published.items()
                            if contest in statewide and not re.search(r"judge|justice|court", keys[0])
                        ),
                    )
                )
            ):
                continue
            unmatched.append(
                f"{'state' if wide else 'local'} {state}: {name} ({', '.join(sorted(places)[:2])}): {'ambiguous' if len(best) > 1 and best[0][1] else 'no contest'}"
            )
        for (_, _, _, contest, key), name, places, office, *only in sorted(
            picks, key=lambda pick: (-pick[0][0], -pick[0][1], -pick[0][2])
        ):
            segment = key.split(":")[2]
            place = None
            numbered = bool(re.match(r"\d|sd", segment))
            if re.fullmatch(r"\d{5}", segment):
                if places == [FIPS[state]]:
                    place = counties.get((FIPS[state], segment))
            elif numbered or segment == "local" or places == [FIPS[state]]:
                found = conn.execute(
                    """select u.id from reporting_unit u
                       where u.area_id = (select district_area_id from contest where id = %s)
                          or u.scheme_code = 'us.place' and u.code = %s
                       order by u.scheme_code = 'us.place' and u.code = %s, u.scheme_code limit 1""",
                    [contest, segment, segment],
                ).fetchone()
                if found is None and numbered:
                    found = conn.execute(
                        "select id from reporting_unit where scheme_code = 'us.school_district' and code = %s",
                        [segment.removeprefix("sd")],
                    ).fetchone()
                if found is None and numbered and re.search(r":(measure|question):", key):
                    unmatched.append(f"local {state}: {name}: no unit for {key}")
                    continue
                place = found[0] if found else None
            part = local.filter(
                (pl.col("state") == state)
                & (pl.col("office") == office)
                & (pl.col("contest") == name)
                & pl.col("county").is_in(places)
            )
            if only:
                part = part.filter(
                    ~pl.col("write_in")
                    & pl.col("candidate").map_elements(lambda c: person(c) in only[0], return_dtype=pl.Boolean)
                )
            earlier = next((i for i, entry in enumerate(work) if entry[1] == key), None)
            if earlier is not None:
                old = work[earlier]
                if key in local_keys and old[4] == place and not set(old[3]["county"]) & set(part["county"]):
                    base = old[3] if "piece" in old[3].columns else old[3].with_columns(piece=pl.lit(0))
                    work[earlier] = (
                        *old[:3],
                        pl.concat([base, part.with_columns(piece=pl.lit(base["piece"].max() + 1))]),
                        place,
                    )
                    matched[office] += 1
                    continue
                unmatched.append(
                    f"{'state' if office == 'state' else 'local'} {state}: {name} ({', '.join(sorted(places)[:2])}): duplicate of {key}"
                )
                continue
            matched[office] += 1
            local_keys.add(key)
            work.append((state, key, contest, part, place))
        for office, label in (("other", "local"), ("state", "state")):
            total = sum(1 for race in races if race[0] == office)
            if total:
                print(f"{label} {state}: {matched[office]} of {total} feed races matched")
    tallies = []
    for state, contest_key, contest, group, place in work:
        options = conn.execute(
            """select o.id, o.kind, coalesce(case when o.kind = 'answer' then o.ballot_label end, ca.ballot_name, o.ballot_label, '')
               from contest_option o
               left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
               where o.contest_id = %s and o.parent_option_id is null order by o.id""",
            [contest],
        ).fetchall()
        named: list[tuple[UUID, list[str]]] = []
        scattered = None
        for option, kind, label in options:
            if kind == "write_in":
                scattered = option
            elif all(words != terms(label) for _, words in named):
                named.append((option, terms(label)))
        ballots: dict[str, UUID] = {}
        for name, write_in in group.select("candidate", "write_in").unique().iter_rows():
            if re.match(r"(?i)no nomination", name):
                continue
            if write_in or WRITE_IN.search(name):
                if scattered is None:
                    scattered = scatter(contest_key, contest)
                if scattered is not None:
                    ballots[name] = scattered
                continue
            tokens = {*terms(name), *terms(name, joined=False)}
            hits = sorted(
                (
                    (len(tokens & set(words)), option)
                    for option, words in named
                    if words and (words[-1] in tokens or "".join(words[-2:]) in tokens)
                ),
                key=lambda hit: -hit[0],
            )
            answer = person(name)
            if answer in ("yes", "no"):
                hits = [(1, option) for option, words in named if words and words[0] == answer] or hits
            if not hits:
                spelled = [
                    option
                    for option, words in named
                    if len(words) > 1
                    and len(tokens) > 1
                    and words[0] == terms(name)[0]
                    and len(words[-1]) > 4
                    and (
                        difflib.SequenceMatcher(None, words[-1], terms(name)[-1]).ratio() >= 0.9
                        or words[-1].replace("l", "i") == terms(name)[-1].replace("l", "i")
                    )
                ]
                hits = [(1, option) for option in spelled] if len(spelled) == 1 else []
            if hits and (
                len(hits) == 1
                or hits[0][0] > hits[1][0]
                or len(
                    {tuple(w for w in words if len(w) > 1) for option, words in named if option in {h[1] for h in hits}}
                )
                == 1
            ):
                ballots[name] = hits[0][1]
            else:
                unmatched.append(f"{contest_key}: no option for {name}")
        reporting = (
            group.group_by("piece")
            .agg(pl.col("reporting").max(), pl.col("total").max())
            .select(pl.col("reporting").sum(), pl.col("total").sum())
            .row(0)
            if "piece" in group.columns
            else group.group_by("county")
            .agg(pl.col("reporting").max(), pl.col("total").max())
            .select(pl.col("reporting").sum(), pl.col("total").sum())
            .row(0)
            if state in ("AK", "AL", "FL", "NJ", "NM", "SD", "ND", "NE", "MT")
            else group.select(pl.col("reporting").max(), pl.col("total").max()).row(0)
        )
        sums: dict[tuple[UUID, UUID], int] = {}
        for county, name, votes in group.select("county", "candidate", "votes").iter_rows():
            option = ballots.get(name)
            unit = place or (
                counties.get((FIPS[state], plain(ALIASES.get((state, plain(county)), county))))
                or counties.get((FIPS[state], plain(county)))
                or counties.get((FIPS[state], county))
            )
            if option is None:
                continue
            if unit is None:
                unmatched.append(f"{contest_key}: no county {county}")
                continue
            sums[(unit, option)] = sums.get((unit, option), 0) + votes
        tallies.append((state, contest_key, contest, sums, reporting, group["as_of"].drop_nulls().max()))
    return tallies, unmatched


def emit():
    frame = pl.read_parquet(PARSED / "results.parquet").filter(pl.col("scope") == "")
    pages = json.loads((PARSED / "pages.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Unofficial election night results of {DAY} for U.S. Senate, U.S. House, and governor by county, from the states' reporting sites",
            polity="US",
        )

        def scatter(contest_key: str, contest: UUID) -> UUID:
            scattered = cs.key("us.option", f"{contest_key}:write_in:scattered")
            if scattered in cs.minted:
                cs.add("contest_option", id=scattered, contest_id=contest, kind="write_in", ballot_label="Scattered")
            return scattered

        tallies, unmatched = plan(conn, frame, scatter)
        sources: dict[str, UUID] = {}
        for state, contest_key, contest, sums, reporting, stamp in tallies:
            election = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()[0]
            if state not in sources:
                publisher, site, _ = OFFICIAL[state]
                title = f"Unofficial Election Night Results {DAY}: {state}"
                sources[state] = cs.source(publisher, title)
                if sources[state] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[state],
                        kind="official_body",
                        publisher=publisher,
                        title=title,
                        url=site,
                        license="Public record",
                    )
                for key in pages[state].values():
                    cs.artifact(raw.cached(SOURCE, key), sources[state])
            as_of = (
                datetime.fromisoformat(str(stamp))
                if stamp
                else datetime.strptime(next(iter(pages[state].values())).rsplit("@", 1)[1], "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=UTC
                )
            )
            frames = conn.execute(
                """select r.id, r.k, r.is_default, r.as_of, r.completeness from result_set r
                   where r.contest_id = %s and r.source_id = %s and r.stage = 'preliminary' and r.status = 'active'
                   order by r.as_of desc""",
                [contest, sources[state]],
            ).fetchall()
            if not any(sums.values()) or (
                frames
                and (
                    frames[0][3] > as_of
                    or {
                        (unit, option): int(value)
                        for unit, option, value in conn.execute(
                            """select u.id, o.id, v.value from result_value v join reporting_unit u on u.k = v.unit_k
                           join contest_option o on o.k = v.option_k where v.result_set_k = %s and v.measure = 1""",
                            [frames[0][1]],
                        ).fetchall()
                    }
                    == sums
                )
            ):
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}@{as_of.astimezone(UTC):%Y%m%dT%H%M%SZ}")
            is_default = (
                conn.execute(
                    "select 1 from result_set where contest_id = %s and is_default and source_id <> %s limit 1",
                    [contest, sources[state]],
                ).fetchone()
                is None
            )
            if is_default:
                for frame, _, was_default, frame_as_of, completeness in frames:
                    if was_default and frame != result_set:
                        cs.add(
                            "result_set",
                            id=frame,
                            election_id=election,
                            contest_id=contest,
                            stage="preliminary",
                            kind="counted",
                            status="active",
                            source_id=sources[state],
                            as_of=frame_as_of,
                            is_default=False,
                            completeness=completeness,
                        )
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="preliminary",
                kind="counted",
                status="active",
                source_id=sources[state],
                as_of=as_of,
                is_default=is_default,
                completeness=reporting[0] / reporting[1] if reporting[1] else None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            for (unit, option), votes in sums.items():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            if reporting[1]:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"{reporting[0]} of {reporting[1]} reporting units had reported.",
                )
        for line in sorted(set(unmatched)):
            print("unmatched:", line)
        return cs.write()
