"""The Philippines' Commission on Elections (COMELEC): the 2022 and 2025 national and local elections, by clustered
precinct, from the results sites the Commission runs for each election.

Each site serves one file per clustered precinct (the electronic return), one per city, municipality, and province
(the canvass), and one per contest. A person saved the files as gzipped bundles of newline-delimited JSON, so this
module reads those bundles from `data/inbox/manual/ph.comelec/` and keeps them in the raw cache under the names
the site served them as.
"""

import functools
import gzip
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid5

import duckdb
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "ph.comelec"
INBOX = DATA / "inbox" / "manual" / SOURCE
PHILIPPINES = str(uuid5(package.NAMESPACE, "polity\x1fPH"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fPHL"))
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6

SITE_2022 = "https://2022electionresults.comelec.gov.ph/data"
SITE_2025 = "https://2025electionresults.comelec.gov.ph/data"
BUNDLE = "browser://20261116-231949-e71a85"
CONTESTS_2022 = f"{SITE_2022}/contests/<id>.json"
REGIONS_2022 = f"{SITE_2022}/regions/root.json"
BARANGAYS_2022 = f"{SITE_2022}/regions/{{49/49074"
CANVASS_2022 = f"{SITE_2022}/results/<url>.json"
PRECINCTS_2022 = [f"{BUNDLE}/ph-2022-results-clustered-precinct-{n:03d}.ndjson.gz" for n in range(1, 14)]
CANVASS_2025 = f"{SITE_2025}/coc/<code>.json"
REGIONS_2025 = f"{SITE_2025}/regions/local/<code>.json"
APP_2025 = "https://2025electionresults.comelec.gov.ph/js/app.248fef28.js"
PRECINCTS_2025 = [f"{BUNDLE}/ph-2025-er-clustered-precinct-{n:03d}.ndjson.gz" for n in range(1, 8)]
FILES = {
    2022: [CONTESTS_2022, REGIONS_2022, BARANGAYS_2022, CANVASS_2022, *PRECINCTS_2022],
    2025: [CANVASS_2025, REGIONS_2025, APP_2025, *PRECINCTS_2025],
}
DAYS = {2022: date(2022, 5, 9), 2025: date(2025, 5, 12)}
# A province or city may move between regions between the two elections; the edge that held in 2022 ends here.
SPLIT = date(2025, 1, 1)
PARSED = DATA / "parsed" / SOURCE
CHUNK = 250_000


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


SOURCES = {2022: key("source", 2022), 2025: key("source", 2025)}


# ---------------------------------------------------------------------------------------------------------------
# Fetching


def fetch() -> None:
    """Add the saved bundles to the raw cache, each under the name the site served it as."""
    index = json.loads((INBOX / "index.json").read_text())
    for urls in FILES.values():
        for url in urls:
            entry = index[url]
            file = INBOX / entry["file"]
            content = file.read_bytes()
            if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise ValueError(f"{file} does not match its recorded hash")
            raw.store(
                SOURCE,
                entry.get("source", url),
                content,
                suffix=file.suffix,
                retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
                media_type=entry["media_type"],
            )


def _path(url: str) -> Path:
    return raw.cached(SOURCE, url).path


@functools.cache
def _json(url: str) -> Any:
    return json.loads(_path(url).read_bytes())


def _lines(url: str) -> Iterator[dict[str, Any]]:
    with gzip.open(_path(url), "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


# ---------------------------------------------------------------------------------------------------------------
# Names


SMALL = {"of", "del", "de", "ng", "sa", "y", "the", "and", "at", "ni", "la", "los", "las"}
ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii"}


def title(text: str) -> str:
    """Turn the Commission's capitals into title case for a contest name; place names stay as printed."""
    words = []
    for index, word in enumerate(text.lower().split()):
        if word in ROMAN:
            words.append(word.upper())
        elif word in SMALL and index:
            words.append(word)
        else:
            words.append(re.sub(r"(^|[-'/(.])([^\W\d_])", lambda m: m.group(1) + m.group(2).upper(), word))
    return " ".join(words).replace("'S", "'s")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


BALLOT = re.compile(r"^(.*?)\s*\(([^()]*?)\)?\s*$")


def candidate(text: str) -> tuple[str, str | None]:
    """Split a ballot line such as "PACQUIAO, MANNY PACMAN(PROMDI)" into the name and the party abbreviation."""
    match = BALLOT.match(text)
    if match is None or not match.group(1):
        return clean(text), None
    return clean(match.group(1)), clean(match.group(2)).upper() or None


def number(text: str) -> tuple[int | None, str]:
    """Split a leading ballot number off a name: "29 BATANG QUIAPO" or "1. ABALOS, BENHUR (PFP)"."""
    match = re.match(r"^(\d+)\.?\s+(.*)$", text.strip())
    return (int(match.group(1)), match.group(2)) if match else (None, text.strip())


KINDS = (
    ("MEMBER, HOUSE OF REPRESENTATIVES", "house"),
    ("MEMBER, SANGGUNIANG PANLALAWIGAN", "provincial_board"),
    ("MEMBER, SANGGUNIANG PANLUNGSOD", "city_council"),
    ("MEMBER, SANGGUNIANG BAYAN", "municipal_council"),
    ("MEMBER, SANGUNIANG BAYAN", "municipal_council"),
    ("PROVINCIAL VICE-GOVERNOR", "vice_governor"),
    ("PROVINCIAL GOVERNOR", "governor"),
    ("VICE-PRESIDENT", "vice_president"),
    ("VICE PRESIDENT", "vice_president"),
    ("PRESIDENT", "president"),
    ("VICE-MAYOR", "vice_mayor"),
    ("MAYOR", "mayor"),
    ("SENATOR", "senator"),
    ("PARTY LIST", "party_list"),
)
LABELS = {
    "house": "Member of the House of Representatives",
    "provincial_board": "Member of the Provincial Board",
    "city_council": "Member of the City Council",
    "municipal_council": "Member of the Municipal Council",
    "vice_governor": "Provincial vice governor",
    "governor": "Provincial governor",
    "vice_president": "Vice president",
    "president": "President",
    "vice_mayor": "Vice mayor",
    "mayor": "Mayor",
    "senator": "Senators",
    "party_list": "Party-list representatives",
}


def contest_kind(text: str) -> tuple[str, str]:
    """Return the kind of contest a printed name stands for and the place part of the name."""
    upper = clean(text).upper()
    for prefix, kind in KINDS:
        if upper.startswith(prefix):
            return kind, re.sub(r"^OF\s+", "", upper[len(prefix) :].strip(), flags=re.IGNORECASE)
    raise ValueError(f"unknown contest: {text}")


def district(label: str) -> tuple[str, str] | None:
    """Split "ABRA - SECOND PROVDIST" or "QUEZON CITY - FIRST DIST" into the place and the district part."""
    match = re.match(
        r"^(.*?)\s+-\s+((?:LONE|FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|[0-9A-Z]+)\s+\w*DIST)$", label
    )
    return (match.group(1), match.group(2)) if match else None


# ---------------------------------------------------------------------------------------------------------------
# Scanning the precinct bundles
#
# A precinct bundle holds about 8,000 returns of about 300 figures each, so the scan turns each bundle into a
# Parquet chunk of integer keys (unit, contest, option, measure, value) and keeps the small tables aside.
# `build` turns the chunks into the package's `result_value` table without holding them in memory.

COLUMNS = {"u": pl.Int32, "c": pl.Int32, "o": pl.Int32, "m": pl.Int16, "v": pl.Int64}
STATS_2022 = {
    "expected-voters": REGISTERED,
    "number-of-voters-who-actually-voted": CAST,
    "votes-obtained-by-all-candidates": VALID,
    "overvote-count": INVALID,
    "undervote-count": BLANK,
}


def _chunk(folder: Path, name: str, rows: dict[str, list[int]]) -> None:
    pl.DataFrame(rows, schema=COLUMNS).write_parquet(folder / f"{name}.parquet", compression="zstd", statistics=False)


def _stamp(urls: list[str]) -> str:
    return hashlib.sha256("".join(raw.cached(SOURCE, url).sha256 for url in urls).encode()).hexdigest()


def scan_2022() -> Path:
    """Read the 106,026 clustered-precinct returns the bundles hold."""
    folder = PARSED / "2022"
    stamp = _stamp([CONTESTS_2022, BARANGAYS_2022, *PRECINCTS_2022])
    meta = folder / "meta.json"
    if meta.exists() and json.loads(meta.read_text())["stamp"] == stamp:
        return folder
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    options = {int(c): {b["boc"] for b in contest["bos"]} for c, contest in _json(CONTESTS_2022)["contests"].items()}
    ratios: dict[int, Counter[int]] = defaultdict(Counter)
    present: set[int] = set()
    sheets: Counter[int] = Counter()
    for index, url in enumerate(PRECINCTS_2022):
        rows: dict[str, list[int]] = {name: [] for name in COLUMNS}
        for line in _lines(url):
            result = line["result"]
            unit = int(result["vbc"])
            if unit in present:
                raise ValueError(f"precinct {unit} appears twice")
            present.add(unit)
            stats: dict[int, dict[str, int | float]] = defaultdict(dict)
            for stat in result["cos"]:
                stats[stat["cc"]][stat["cn"]] = stat["ct"]
            for contest, found in stats.items():
                sheets[contest] += 1
                processed = found.get("processed-ballots") or 0
                valid = found.get("valid-votes") or 0
                if processed and valid % processed == 0:
                    ratios[contest][int(valid // processed)] += 1
                for name, measure in STATS_2022.items():
                    if name in found:
                        for column, item in zip(
                            rows.values(), (unit, contest, -1, measure, int(found[name])), strict=True
                        ):
                            column.append(item)
            for item in result["rs"]:
                if item["bo"] not in options[item["cc"]]:
                    raise ValueError(f"option {item['bo']} is not in contest {item['cc']}")
                for column, value in zip(rows.values(), (unit, item["cc"], item["bo"], VOTES, item["v"]), strict=True):
                    column.append(value)
        _chunk(folder, f"values-{index:02d}", rows)
        print(f"2022 bundle {index + 1}/{len(PRECINCTS_2022)}: {len(present):,} precincts", flush=True)
    meta.write_text(
        json.dumps(
            {
                "stamp": stamp,
                "present": sorted(present),
                "sheets": {str(c): n for c, n in sheets.items()},
                "votes_per_ballot": {str(c): r.most_common(1)[0][0] for c, r in ratios.items()},
                "ratios": {str(c): dict(r) for c, r in ratios.items()},
            }
        )
    )
    return folder


def scan_2025() -> Path:
    """Read the electronic returns of the 75,343 clustered precincts the bundles hold."""
    folder = PARSED / "2025"
    stamp = _stamp(PRECINCTS_2025)
    meta = folder / "meta.json"
    if meta.exists() and json.loads(meta.read_text())["stamp"] == stamp:
        return folder
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    contests: dict[str, int] = {}
    names: dict[str, str] = {}
    groups: dict[str, str] = {}
    options: dict[tuple[str, str], int] = {}
    ratios: dict[str, Counter[int]] = defaultdict(Counter)
    cities: dict[str, Counter[str]] = defaultdict(Counter)
    units: list[dict[str, object]] = []
    odd: Counter[str] = Counter()
    unit_of: dict[str, int] = {}
    for index, url in enumerate(PRECINCTS_2025):
        rows: dict[str, list[int]] = {name: [] for name in COLUMNS}

        def add(*item: int) -> None:
            for column, value in zip(rows.values(), item, strict=True):
                column.append(value)

        for line in _lines(url):
            if "precinct" not in line:
                continue
            code = line["precinct"]
            if code in unit_of:
                raise ValueError(f"precinct {code} appears twice")
            unit = unit_of[code] = len(unit_of)
            er = line["er"]
            info = er["information"]
            registered, cast = info["numberOfRegisteredVoters"], info["numberOfActuallyVoters"]
            units.append(
                {
                    "u": unit,
                    "code": code,
                    "location": info["location"],
                    "center": info["votingCenter"],
                    "cluster": info["precinctInCluster"],
                    "registered": registered,
                    "cast": cast,
                }
            )
            for group in ("national", "local"):
                for item in er.get(group, []):
                    if item["contestName"].startswith("BARMM "):
                        # The returns carry placeholder lines for the Bangsamoro Parliament, which did not vote on
                        # this day; every figure on them is zero.
                        if any(item["statistic"][k] for k in ("obtainedVotes", "overVotes", "underVotes")):
                            raise ValueError(f"{code}: {item['contestName']} holds votes")
                        odd["Bangsamoro Parliament placeholder lines left out"] += 1
                        continue
                    name = item["contestCode"]
                    contest = contests.setdefault(name, len(contests))
                    names[name] = item["contestName"]
                    groups[name] = group
                    stat = item["statistic"]
                    obtained, over, under = stat["obtainedVotes"], stat["overVotes"], stat["underVotes"]
                    if group == "local":
                        cities[name][code[:4]] += 1
                    add(unit, contest, -1, REGISTERED, registered)
                    add(unit, contest, -1, CAST, cast)
                    add(unit, contest, -1, VALID, obtained)
                    add(unit, contest, -1, BLANK, under)
                    per, remainder = divmod(obtained + over + under, cast) if cast else (0, 0)
                    if cast and not remainder and per:
                        ratios[name][per] += 1
                    elif cast and remainder:
                        # Two returns total one vote more than the ballots they processed; the votes per ballot are
                        # the nearest whole number.
                        odd["votes do not add to a whole number of ballots"] += 1
                        per = round((obtained + over + under) / cast)
                    elif cast:
                        odd["ballots cast but no votes of any kind"] += 1
                    if not cast or not per:
                        add(unit, contest, -1, INVALID, over)
                    elif over % per == 0:
                        add(unit, contest, -1, INVALID, over // per)
                    else:
                        odd["over votes are not a whole number of ballots"] += 1
                    total = 0
                    for found in item["candidates"]["candidates"]:
                        slot = options.setdefault((name, found["name"]), len(options))
                        add(unit, contest, slot, VOTES, found["votes"])
                        total += found["votes"]
                    if total != obtained:
                        odd["candidate votes differ from the obtained total"] += 1
        _chunk(folder, f"values-{index:02d}", rows)
        print(f"2025 bundle {index + 1}/{len(PRECINCTS_2025)}: {len(unit_of):,} precincts", flush=True)
    pl.DataFrame(units).write_parquet(folder / "units.parquet")
    meta.write_text(
        json.dumps(
            {
                "stamp": stamp,
                "contests": [{"c": c, "code": n, "name": names[n], "group": groups[n]} for n, c in contests.items()],
                "options": [{"o": o, "contest": n, "name": t} for (n, t), o in options.items()],
                "votes_per_ballot": {n: r.most_common(1)[0][0] for n, r in ratios.items()},
                "ratios": {n: dict(r) for n, r in ratios.items()},
                "cities": {n: dict(c) for n, c in cities.items()},
                "odd": dict(odd),
            }
        )
    )
    return folder


# ---------------------------------------------------------------------------------------------------------------
# Places


SCHEMES = (
    ("ph.region", 1, "administrative", "region", None),
    ("ph.province", 2, "administrative", "province", None),
    ("ph.city", 3, "administrative", "city or municipality", None),
    ("ph.barangay", 4, "administrative", "barangay", None),
)
DISTRICTS = (
    ("ph.legdist", 5, "House district"),
    ("ph.provdist", 6, "provincial board district"),
    ("ph.citydist", 7, "council district"),
)
PRECINCT = "ph.precinct"
OVERSEAS = {"OAV", "R0OAV00", "LAV", "R00LAV0", "9706000"}


def _tree_2022() -> dict[tuple[str, str], tuple[str, tuple[str, str] | None]]:
    """The 2022 places: (scheme, code) -> (name, parent), without the overseas posts."""
    regions = _json(REGIONS_2022)["regions"]
    nodes: dict[tuple[str, str], tuple[str, tuple[str, str] | None]] = {}
    for region in regions["44/44021"]["srs"].values():
        if region["rcc"] in OVERSEAS:
            continue
        nodes["ph.region", region["rcc"]] = (region["rn"], None)
        for province in regions[region["url"]]["srs"].values():
            nodes["ph.province", province["rcc"]] = (province["rn"], ("ph.region", region["rcc"]))
            for city in regions[province["url"]]["srs"].values():
                nodes["ph.city", city["rcc"]] = (city["rn"], ("ph.province", province["rcc"]))
                for barangay in regions[city["url"]]["srs"].values():
                    nodes["ph.barangay", barangay["rcc"]] = (barangay["rn"], ("ph.city", city["rcc"]))
    return nodes


def _tree_2025() -> dict[tuple[str, str], tuple[str, tuple[str, str] | None]]:
    """The 2025 places, coded as the 2022 ones are: regions by the 2022 code of the same name."""
    regions = _json(REGIONS_2025)["regions"]
    names = {name: code for (scheme, code), (name, _) in _tree_2022().items() if scheme == "ph.region"}
    nodes: dict[tuple[str, str], tuple[str, tuple[str, str] | None]] = {}
    for region in regions["local/0"]["regions"]:
        if region["code"] in OVERSEAS:
            continue
        code = names.get(region["name"], region["name"])
        nodes["ph.region", code] = (region["name"], None)
        for province in regions[f"local/{region['code']}"]["regions"]:
            nodes["ph.province", province["code"][:4]] = (province["name"], ("ph.region", code))
            for city in regions.get(f"local/{province['code']}", {"regions": []})["regions"]:
                nodes["ph.city", city["code"][:4]] = (city["name"], ("ph.province", province["code"][:4]))
                for barangay in regions.get(f"local/{city['code']}", {"regions": []})["regions"]:
                    nodes["ph.barangay", barangay["code"]] = (barangay["name"], ("ph.city", city["code"][:4]))
    return nodes


@functools.cache
def trees() -> dict[int, dict[tuple[str, str], tuple[str, tuple[str, str] | None]]]:
    return {2022: _tree_2022(), 2025: _tree_2025()}


@functools.cache
def parents() -> dict[tuple[str, str], tuple[str, str] | None]:
    """The parent of each place, as the later election has it."""
    found: dict[tuple[str, str], tuple[str, str] | None] = {}
    for year in (2022, 2025):
        for place, (_, parent) in trees()[year].items():
            found[place] = parent
    return found


@functools.cache
def _only_city(year: int) -> dict[str, str]:
    """The province codes that hold exactly one city in a year's tree, with that city: the 2025 site files all of the
    City of Manila under one city, though its precinct codes still carry the code of one of its old districts."""
    kids: dict[str, list[str]] = defaultdict(list)
    for (scheme, code), (_, parent) in trees()[year].items():
        if scheme == "ph.city" and parent:
            kids[parent[1]].append(code)
    return {province: found[0] for province, found in kids.items() if len(found) == 1}


def resolve_city(year: int, code: str) -> str:
    """The city a precinct code's first four digits stand for."""
    if ("ph.city", code) in trees()[year]:
        return code
    return _only_city(year).get(code[:2] + "00", code)


@functools.cache
def barangays_2022() -> list[dict[str, Any]]:
    """Every clustered precinct the 2022 site lists, with the barangay, voting center, and contests of each."""
    found = []
    for line in _lines(BARANGAYS_2022):
        city = line["parent"]
        for center in line["pps"]:
            for box in center["vbs"]:
                found.append(
                    {
                        "vbc": box["vbc"],
                        "code": box["pre"],
                        "cluster": box["cpre"],
                        "type": box["type"],
                        "center": clean(center["ppn"]),
                        "barangay": line["rcc"],
                        "city": city,
                        "contests": box["cs"],
                    }
                )
    return found


@functools.cache
def _city_codes() -> dict[str, str]:
    """The 2022 city code of each node url."""
    return {v["url"]: v["rcc"] for v in _json(REGIONS_2022)["regions"].values() if v["can"] == "City/Municipality"}


@functools.cache
def contests_2022() -> dict[int, dict[str, Any]]:
    found: dict[int, dict[str, Any]] = {}
    for code, contest in _json(CONTESTS_2022)["contests"].items():
        kind, label = contest_kind(contest["cn"])
        found[int(code)] = {"c": int(code), "kind": kind, "label": label, "name": contest["cn"], "bos": contest["bos"]}
    return found


@functools.cache
def contests_2025() -> dict[str, dict[str, Any]]:
    meta = json.loads((scan_2025() / "meta.json").read_text())
    options: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in meta["options"]:
        options[item["contest"]].append(item)
    found: dict[str, dict[str, Any]] = {}
    for item in meta["contests"]:
        kind, label = contest_kind(item["name"])
        found[item["code"]] = {
            "c": item["c"],
            "code": item["code"],
            "kind": kind,
            "label": label,
            "name": item["name"],
            "options": options[item["code"]],
            "vpb": meta["votes_per_ballot"].get(item["code"]),
            "cities": meta["cities"].get(item["code"], {}),
        }
    return found


def _cities_2022() -> dict[int, Counter[str]]:
    """For each 2022 contest, the cities whose precincts vote in it."""
    cities = _city_codes()
    found: dict[int, Counter[str]] = defaultdict(Counter)
    for box in barangays_2022():
        for contest in box["contests"]:
            found[contest][cities[box["city"]]] += 1
    return found


def _cities_2025() -> dict[str, Counter[str]]:
    found: dict[str, Counter[str]] = {}
    for code, contest in contests_2025().items():
        counted: Counter[str] = Counter()
        for city, n in contest["cities"].items():
            counted[resolve_city(2025, city)] += n
        found[code] = counted
    return found


@functools.cache
def contest_places() -> dict[tuple[int, str | int], tuple[str, str] | None]:
    """The area each contest elects in, as (scheme, code): the country, a province, a city, or a district."""
    cities = {2022: _cities_2022(), 2025: _cities_2025()}
    found: dict[tuple[int, str | int], tuple[str, str] | None] = {}
    listed: dict[int, list[tuple[str | int, str, str]]] = {
        2022: [(c, v["kind"], v["label"]) for c, v in contests_2022().items()],
        2025: [(c, v["kind"], v["label"]) for c, v in contests_2025().items()],
    }
    for year, items in listed.items():
        for contest, kind, label in items:
            seen = cities[year].get(contest, Counter())
            city = seen.most_common(1)[0][0] if seen else None
            province = None
            if city is not None and ("ph.city", city) in parents():
                province = (parents()[("ph.city", city)] or ("", ""))[1]
            if kind in ("president", "vice_president", "senator", "party_list"):
                found[year, contest] = None
            elif kind in ("governor", "vice_governor"):
                found[year, contest] = ("ph.province", province) if province else None
            elif kind in ("mayor", "vice_mayor"):
                found[year, contest] = ("ph.city", city) if city else None
            else:
                split = district(label)
                scheme = {"house": "ph.legdist", "provincial_board": "ph.provdist"}.get(kind, "ph.citydist")
                if split is None or (kind not in ("house", "provincial_board") and split[1].startswith("LONE")):
                    found[year, contest] = ("ph.city", city) if city else None
                else:
                    found[year, contest] = (scheme, label)
    return found


@functools.cache
def districts() -> dict[tuple[str, str], tuple[str, str] | None]:
    """The parent (scheme, code) of each district area: its city when all its precincts lie in one, else its province."""
    seen: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    cities = {2022: _cities_2022(), 2025: _cities_2025()}
    for (year, contest), place in contest_places().items():
        if place is not None and place[0] in {s for s, _, _ in DISTRICTS}:
            seen[place].update(cities[year].get(contest, Counter()))
    found: dict[tuple[str, str], tuple[str, str] | None] = {}
    for place, counted in seen.items():
        if not counted:
            found[place] = None
        elif len(counted) == 1:
            found[place] = ("ph.city", next(iter(counted)))
        else:
            provinces = Counter()
            for city, n in counted.items():
                parent = parents().get(("ph.city", city))
                if parent:
                    provinces[parent[1]] += n
            found[place] = ("ph.province", provinces.most_common(1)[0][0]) if provinces else None
    return found


def fold(name: str) -> str:
    """Compare place names across the two sites, which differ in spacing, a "(POB.)" suffix, and a few spellings."""
    return re.sub(r"[^A-Z0-9Ñ]+", " ", re.sub(r"\(.*?\)", " ", name.upper())).strip()


def district_name(label: str) -> str:
    return title(re.sub(r"\b(?:LEG|PROV|REG)?DIST\b", "DISTRICT", label))


def _source(p: Package, year: int) -> None:
    site = f"https://{year}electionresults.comelec.gov.ph/"
    p.add(
        "source",
        id=SOURCES[year],
        kind="official_body",
        publisher="Commission on Elections",
        title=f"{year} National and Local Elections results",
        url=site,
        license=None,
        license_url=None,
        attribution="Commission on Elections",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The Philippines' regions, provinces, cities and municipalities, and barangays, and the House, provincial "
        "board, and council districts, as the Commission on Elections lists them for 2022 and 2025",
    )
    p.external |= {PHILIPPINES, COUNTRY}
    for year in DAYS:
        _source(p, year)
    for url in (REGIONS_2022, BARANGAYS_2022, CONTESTS_2022, REGIONS_2025):
        p.artifact(
            raw.cached(SOURCE, url), SOURCES[2022 if url in (REGIONS_2022, BARANGAYS_2022, CONTESTS_2022) else 2025]
        )
    for code, rank, kind, noun, system in SCHEMES:
        p.add("scheme", code=code, polity_id=PHILIPPINES, kind=kind, rank=rank, noun=noun, code_system=system)
    for code, rank, noun in DISTRICTS:
        p.add("scheme", code=code, polity_id=PHILIPPINES, kind="electoral_district", rank=rank, noun=noun)
    p.add("scheme", code=PRECINCT, polity_id=PHILIPPINES, kind="precinct", rank=8, noun="clustered precinct")
    nodes = trees()
    places: dict[tuple[str, str], dict[int, tuple[str, tuple[str, str] | None]]] = defaultdict(dict)
    for year in DAYS:
        for place, found in nodes[year].items():
            places[place][year] = found
    for place, seen in sorted(places.items()):
        scheme, code = place
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        latest = seen[max(seen)]
        p.name(id, clean(latest[0]), "fil", "official")
        for year, (name, _) in seen.items():
            if fold(name) != fold(latest[0]):
                p.name(id, clean(name), "fil", "historical", False)
        edges = {year: found[1] for year, found in seen.items()}
        if len(set(edges.values())) == 1:
            parent = next(iter(edges.values()))
            p.add(
                "area_parent",
                area_id=id,
                parent_id=area(*parent) if parent else COUNTRY,
                valid_from=None,
                valid_to=None,
            )
        else:
            for year, parent in edges.items():
                p.add(
                    "area_parent",
                    area_id=id,
                    parent_id=area(*parent) if parent else COUNTRY,
                    valid_from=None if year == 2022 else SPLIT,
                    valid_to=SPLIT if year == 2022 else None,
                )
    for place, parent in sorted(districts().items()):
        scheme, code = place
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area(*parent) if parent else COUNTRY)
        p.name(id, district_name(code), "en", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------
# Parties, bodies, offices, and electoral systems

INDEPENDENT = {"IND", "INDEPENDENT"}
SENATE = key("body", "senate")
HOUSE = key("body", "house")
PROVINCIAL_BOARD = key("body", "provincial board")
CITY_COUNCIL = key("body", "city council")
MUNICIPAL_COUNCIL = key("body", "municipal council")
BODIES = {
    "senator": (SENATE, "upper_house", "Senate of the Philippines", "Senado ng Pilipinas"),
    "house": (HOUSE, "lower_house", "House of Representatives of the Philippines", "Kapulungan ng mga Kinatawan"),
    "party_list": (HOUSE, "lower_house", "House of Representatives of the Philippines", "Kapulungan ng mga Kinatawan"),
    "provincial_board": (PROVINCIAL_BOARD, "council", "Sangguniang Panlalawigan (provincial board)", None),
    "city_council": (CITY_COUNCIL, "council", "Sangguniang Panlungsod (city council)", None),
    "municipal_council": (MUNICIPAL_COUNCIL, "council", "Sangguniang Bayan (municipal council)", None),
}
OFFICES = {
    "president": (key("office", "president"), "head_of_state", "President of the Philippines", "Pangulo ng Pilipinas"),
    "vice_president": (key("office", "vice president"), "other", "Vice President of the Philippines", None),
    "governor": (key("office", "governor"), "governor", "Provincial governor", "Gobernador"),
    "vice_governor": (key("office", "vice governor"), "other", "Provincial vice governor", None),
    "mayor": (key("office", "mayor"), "mayor", "Mayor of a city or municipality", "Alkalde"),
    "vice_mayor": (key("office", "vice mayor"), "deputy_mayor", "Vice mayor of a city or municipality", None),
}
FPTP = key("electoral_system", "fptp")
BLOCK = key("electoral_system", "block")
SYSTEMS = (
    (
        FPTP,
        {"formula": "fptp"},
        "Each voter casts one vote for one candidate, and the candidate with the most votes is elected.",
    ),
    (
        BLOCK,
        {"formula": "block"},
        (
            "Each voter casts as many votes as there are seats, at most one per candidate, and the candidates with "
            "the most votes fill the seats. A ballot with more votes than seats in a contest is an overvote and "
            "counts for no candidate in that contest."
        ),
    ),
)
PARTY_LIST_NOTE = (
    "Voters cast one vote for a party-list organization, in addition to their votes for candidates. Under the party-list "
    "law, organizations that win at least 2% of the party-list vote are entitled to seats, with at most three seats "
    "each, so that party-list members make up 20% of the House. The site publishes votes only; seats are not recorded "
    "here."
)


def party_list_key(name: str) -> str:
    return re.sub(r"\s+", " ", number(name)[1]).strip().upper()


def party(abbreviation: str) -> str:
    return key("party", abbreviation)


def list_party(name: str) -> str:
    return key("party", "list", party_list_key(name))


@functools.cache
def parties() -> tuple[dict[str, str | None], set[str]]:
    """The party abbreviations printed on the ballots of both years, with a long name where 2022 gives one, and the
    names of the party-list organizations."""
    modal: dict[int, Counter[str]] = defaultdict(Counter)
    long: dict[str, Counter[tuple[int, str]]] = defaultdict(Counter)
    abbreviations: dict[str, str | None] = {}
    lists: set[str] = set()
    for contest in contests_2022().values():
        for option in contest["bos"]:
            if contest["kind"] == "party_list":
                lists.add(party_list_key(option["bon"]))
                continue
            _, abbreviation = candidate(option["bon"])
            if abbreviation and abbreviation not in INDEPENDENT:
                modal[option["pc"]][abbreviation] += 1
                long[abbreviation][(option["pc"], clean(option["pn"]))] += 1
                abbreviations[abbreviation] = None
    for abbreviation, found in long.items():
        (pc, name), _ = found.most_common(1)[0]
        if modal[pc].most_common(1)[0][0] == abbreviation:
            abbreviations[abbreviation] = name
    for contest in contests_2025().values():
        for option in contest["options"]:
            _, text = number(option["name"])
            if contest["kind"] == "party_list":
                lists.add(party_list_key(option["name"]))
                continue
            _, abbreviation = candidate(text)
            if abbreviation and abbreviation not in INDEPENDENT:
                abbreviations.setdefault(abbreviation, None)
    return abbreviations, lists


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Philippine Senate, House, provincial boards and councils, local executive offices, and the parties on "
        "the 2022 and 2025 ballots",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {PHILIPPINES, COUNTRY}
    for year in DAYS:
        for url in FILES[year][:2]:
            p.uses(raw.cached(SOURCE, url))
    for id, kind, english, native in dict.fromkeys(BODIES.values()):
        p.add("body", id=id, polity_id=PHILIPPINES, kind=kind)
        p.name(id, english, "en", "official")
        if native:
            p.name(id, native, "fil", "native", False)
    for id, kind, english, native in OFFICES.values():
        p.add("office", id=id, polity_id=PHILIPPINES, kind=kind)
        p.name(id, english, "en", "official")
        if native:
            p.name(id, native, "fil", "native", False)
    for id, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=PHILIPPINES,
            valid_from=None,
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    abbreviations, lists = parties()
    for abbreviation, long in sorted(abbreviations.items()):
        id = party(abbreviation)
        p.add("party", id=id, kind="party", polity_id=PHILIPPINES)
        p.name(id, abbreviation, "fil", "abbreviation", long is None)
        if long:
            p.name(id, long, "fil", "official")
    for name in sorted(lists):
        id = list_party(name)
        p.add("party", id=id, kind="list_vehicle", polity_id=PHILIPPINES)
        p.name(id, name, "fil", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------
# Elections


def slug(text: str) -> str:
    import unicodedata

    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def method(year: int) -> str:
    return (
        f"Counted from the electronic return of each clustered precinct, as the Commission on Elections published them "
        f"on its {year} results site. A clustered precinct is the group of precincts that share one counting machine. "
        "Registered voters are the voters the return lists as expected, ballots cast are the ballots the machine "
        "processed, and valid votes are the votes the return gives to candidates, counted for contests where each "
        "ballot carries one vote. Invalid ballots are the ballots overvoted in the contest, and blank ballots are the "
        "ballots with no vote for it. Where a ballot carries several votes, the return counts votes in the contest "
        "totals, so valid votes and blank ballots are left out and only the votes of each candidate are stored. "
        "A count of zero votes for an option is not stored: an option with no value in a unit that reports the "
        "contest received no votes there. "
        "Proclamations are made by the boards of canvassers and are not recorded here."
    )


class Entry:
    """One option of a contest, whichever site it came from."""

    def __init__(self, o: int, id: str, text: str, name: str, ordinal: int | None, party: str | None, listed: bool):
        self.o, self.id, self.text, self.name, self.ordinal, self.party, self.listed = (
            o,
            id,
            text,
            name,
            ordinal,
            party,
            listed,
        )


class Entered:
    """One contest of one election: what it elects, where, and its options."""

    def __init__(self, year: int, c: int, source: str, kind: str, label: str, vpb: int | None, entries: list[Entry]):
        self.year, self.c, self.source, self.kind, self.label, self.vpb, self.entries = (
            year,
            c,
            source,
            kind,
            label,
            vpb,
            entries,
        )

    @property
    def id(self) -> str:
        return key("contest", self.year, self.source)

    @property
    def place(self) -> tuple[str, str] | None:
        return contest_places()[self.year, int(self.source) if self.year == 2022 else self.source]


def entered(year: int) -> list[Entered]:
    found: list[Entered] = []
    if year == 2022:
        votes = json.loads((scan_2022() / "meta.json").read_text())["votes_per_ballot"]
        for c, contest in sorted(contests_2022().items()):
            entries = []
            for option in sorted(contest["bos"], key=lambda o: o["to"]):
                if contest["kind"] == "party_list":
                    text, name, abbreviation = clean(option["bon"]), number(option["bon"])[1], None
                else:
                    name, abbreviation = candidate(option["bon"])
                    text = clean(option["bon"])
                entries.append(
                    Entry(
                        option["boc"],
                        key("option", 2022, option["boc"]),
                        text,
                        name,
                        option["to"],
                        abbreviation,
                        contest["kind"] == "party_list",
                    )
                )
            found.append(Entered(2022, c, str(c), contest["kind"], contest["label"], votes.get(str(c)), entries))
    else:
        for code, contest in sorted(contests_2025().items()):
            entries = []
            for option in sorted(contest["options"], key=lambda o: number(o["name"])[0] or 0):
                ordinal, text = number(option["name"])
                if contest["kind"] == "party_list":
                    name, abbreviation = party_list_key(option["name"]), None
                else:
                    name, abbreviation = candidate(text)
                entries.append(
                    Entry(
                        option["o"],
                        key("option", 2025, code, option["name"]),
                        text,
                        name,
                        ordinal,
                        abbreviation,
                        contest["kind"] == "party_list",
                    )
                )
            found.append(Entered(2025, contest["c"], code, contest["kind"], contest["label"], contest["vpb"], entries))
    return found


def _completeness(year: int) -> dict[int, float | None]:
    if year == 2022:
        expected: Counter[int] = Counter()
        for box in barangays_2022():
            expected.update(box["contests"])
        sheets = json.loads((scan_2022() / "meta.json").read_text())["sheets"]
        return {c: min(1.0, sheets.get(str(c), 0) / n) for c, n in expected.items()}
    return {}


def _dashboard() -> dict[str, Any]:
    for line in _lines(APP_2025):
        if line.get("url") == "/data/common/dashboard.json":
            return line["body"]
    return {}


def _as_of_2025() -> datetime | None:
    for line in _lines(APP_2025):
        if line.get("url") == "/data/common/latestTime.json":
            body = line["body"]
            stamp = datetime.strptime(f"{body['date']} {body['time']}", "%d %B %Y %I:%M:%S %p")
            return stamp.replace(tzinfo=UTC) - timedelta(hours=8)
    return None


def _unit_dim(year: int, folder: Path) -> None:
    """Write each precinct's source number next to the id its unit gets."""
    day = DAYS[year]
    if year == 2022:
        present = set(json.loads((folder / "meta.json").read_text())["present"])
        found = [(box["vbc"], key("unit", day, box["code"])) for box in barangays_2022() if box["vbc"] in present]
    else:
        found = [
            (row["u"], key("unit", day, row["code"]))
            for row in pl.read_parquet(folder / "units.parquet").iter_rows(named=True)
        ]
    pl.DataFrame(
        {"u": [u for u, _ in found], "uid": [i for _, i in found]}, schema={"u": pl.Int32, "uid": pl.String}
    ).write_parquet(folder / "units.dim.parquet")


def _units(year: int, p: Package) -> list[tuple[int, str]]:
    """Add the reporting units of an election and return each one's (source number, id)."""
    day = DAYS[year]
    made: list[tuple[int, str]] = []
    if year == 2022:
        present = set(json.loads((scan_2022() / "meta.json").read_text())["present"])
        known = {place for place in trees()[2022]}
        for box in barangays_2022():
            if box["vbc"] not in present:
                continue
            id = key("unit", day, box["code"])
            made.append((box["vbc"], id))
            home = ("ph.barangay", box["barangay"])
            local = home in known
            p.add(
                "reporting_unit",
                id=id,
                polity_id=PHILIPPINES,
                scheme_code=PRECINCT,
                code=f"{day}:{box['code']}",
                area_relation="within" if local else "unlocated",
                area_id=area(*home) if local else None,
                kind="standard" if local else "abroad_mission",
            )
            p.name(id, f"{box['center']} (clustered precinct {box['code']})", "fil", "official")
        return made
    frame = pl.read_parquet(scan_2025() / "units.parquet")
    barangays: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for (scheme, code), (name, parent) in trees()[2025].items():
        if scheme == "ph.barangay" and parent:
            barangays[parent[1]][fold(name)].append(code)
    for row in frame.iter_rows(named=True):
        id = key("unit", day, row["code"])
        made.append((row["u"], id))
        city = resolve_city(2025, row["code"][:4])
        place: tuple[str, str] | None = ("ph.city", city) if ("ph.city", city) in trees()[2025] else None
        parts = [clean(x) for x in row["location"].split(",")]
        for cut in range(3, len(parts)):
            found = barangays[city].get(fold(", ".join(parts[cut:])), [])
            if len(found) == 1:
                place = ("ph.barangay", found[0])
                break
        p.add(
            "reporting_unit",
            id=id,
            polity_id=PHILIPPINES,
            scheme_code=PRECINCT,
            code=f"{day}:{row['code']}",
            area_relation="within" if place else "unlocated",
            area_id=area(*place) if place else None,
            kind="standard",
        )
        p.name(id, f"{clean(row['center'])} (clustered precinct {row['code']})", "fil", "official")
    return made


class Bulk(Package):
    """A package whose `result_value` table is too large to hold in memory: it is written from Parquet chunks."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.later: list[Any] = []

    def write(self) -> Path:
        out = super().write()
        for step in self.later:
            step(out)
        if not (out / "result_value.parquet").exists():
            return out
        manifest = json.loads((out / "package.json").read_text())
        con = duckdb.connect()
        found = con.execute(f"select count(*) from read_parquet('{out / 'result_value.parquet'}')").fetchone()
        manifest["tables"]["result_value"] = 0 if found is None else int(found[0])
        manifest["digest"] = package._digest(out, manifest["tables"])
        (out / "package.json").write_text(json.dumps(manifest, indent=1) + "\n")
        print(f"{self.key}: result_value {manifest['tables']['result_value']:,}", flush=True)
        return out


def _dims(folder: Path, name: str, contests: list[Entered], areas: list[tuple[int, str]]) -> Path:
    """Write the lookup tables that turn a package's integer keys into ids."""
    out = folder / f"dims-{name}"
    out.mkdir(exist_ok=True)
    pl.DataFrame(
        {
            "c": [c.c for c in contests],
            "rid": [key("result_set", c.year, c.source) for c in contests],
            "vpb": [c.vpb for c in contests],
        },
        schema={"c": pl.Int32, "rid": pl.String, "vpb": pl.Int32},
    ).write_parquet(out / "contests.parquet")
    pl.DataFrame(
        {"o": [e.o for c in contests for e in c.entries], "oid": [e.id for c in contests for e in c.entries]},
        schema={"o": pl.Int32, "oid": pl.String},
    ).write_parquet(out / "options.parquet")
    pl.DataFrame(
        {"c": [c for c, _ in areas], "aid": [a for _, a in areas]}, schema={"c": pl.Int32, "aid": pl.String}
    ).write_parquet(out / "districts.parquet")
    return out


def _write_values(folder: Path, dims: Path, out: Path) -> None:
    spill = PARSED / ".duckdb"
    spill.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(config={"memory_limit": "6GB", "threads": 4, "temp_directory": str(spill)})
    con.execute(
        f"""copy (
              select r.rid as result_set_id, n.uid as unit_id, op.oid as option_id, 0::smallint as channel,
                     v.m::smallint as measure, 0::smallint as count_round, 0::smallint as flags, v.v::bigint as value
              from read_parquet('{folder}/values-*.parquet') v
              join read_parquet('{folder}/units.dim.parquet') n on n.u = v.u
              join read_parquet('{dims}/contests.parquet') r on r.c = v.c
              left join read_parquet('{dims}/options.parquet') op on op.o = v.o and v.o >= 0
              where not (v.m in ({VALID}, {BLANK}) and r.vpb is distinct from 1)
                and not (v.m = {VOTES} and v.v = 0)
            ) to '{out / "result_value.parquet"}' (format parquet, compression zstd, row_group_size 1000000)"""
    )


def _district_units(folder: Path, dims: Path) -> pl.DataFrame:
    """Link each unit to the House, provincial board, and council districts it votes in."""
    con = duckdb.connect(config={"memory_limit": "4GB", "threads": 4})
    rows = con.execute(
        f"""select distinct n.uid, d.aid
            from read_parquet('{folder}/values-*.parquet') v
            join read_parquet('{folder}/units.dim.parquet') n on n.u = v.u
            join read_parquet('{dims}/districts.parquet') d on d.c = v.c
            where v.m = {REGISTERED}"""
    ).fetchall()
    return pl.DataFrame(
        {
            "unit_id": [unit for unit, _ in rows],
            "area_id": [place for _, place in rows],
            "approximate": [False] * len(rows),
            "partial": [False] * len(rows),
            "method": ["official_list"] * len(rows),
        },
        schema={
            "unit_id": pl.String,
            "area_id": pl.String,
            "approximate": pl.Boolean,
            "partial": pl.Boolean,
            "method": pl.String,
        },
    )


NATIONAL = ("president", "vice_president", "senator", "party_list")
UNBALANCED = {"56190025": "Gumaca, Quezon", "46230005": "Valencia, Negros Oriental"}


def unbalanced_groups() -> set[str]:
    """The packages that hold the contests of the two returns whose totals exceed their ballots by one."""
    found = {"partylist"}
    for code in UNBALANCED:
        region = region_of(2025, ("ph.city", resolve_city(2025, code[:4])))
        if region:
            found.add(region)
    return found


def region_of(year: int, place: tuple[str, str] | None) -> str | None:
    """The region code a place lies in, following the parents the election's own site lists."""
    tree = trees()[year]
    while place is not None and place[0] != "ph.region":
        found = tree.get(place)
        place = found[1] if found else parents().get(place)
    return place[1] if place else None


def group(year: int, c: Entered) -> str:
    """The package a contest goes in: `national` (president and vice president), `senate`, `partylist`, or the region
    a local contest is held in."""
    if c.kind in NATIONAL:
        return {"senator": "senate", "party_list": "partylist"}.get(c.kind, "national")
    place = c.place
    if place is not None and place[0] in {s for s, _, _ in DISTRICTS}:
        place = districts().get(place)
    return region_of(year, place) or "other"


def packages_of(year: int) -> list[str]:
    return [f"{SOURCE}/{DAYS[year]}", *(f"{SOURCE}/{DAYS[year]}-{r.lower()}" for r in regions_of(year))]


@functools.cache
def regions_of(year: int) -> list[str]:
    found = {group(year, c) for c in entered(year)} - {"national"}
    return sorted(found)


def _contest(p: Package, year: int, c: Entered, election_id: str, path: str, used: set[str]) -> None:
    office = OFFICES.get(c.kind)
    body = BODIES.get(c.kind)
    place = c.place
    if c.kind in NATIONAL:
        district_id: str | None = COUNTRY
    else:
        district_id = area(*place) if place else None
    if c.kind == "party_list":
        kind, magnitude, system = "list_allocation", None, None
    elif office:
        kind, magnitude, system = "office", 1, FPTP
    else:
        kind, magnitude = "body_seats", c.vpb
        system = None if c.vpb is None else FPTP if c.vpb == 1 else BLOCK
    p.add(
        "contest",
        id=c.id,
        election_id=election_id,
        kind=kind,
        office_id=office[0] if office else None,
        body_id=body[0] if body else None,
        district_area_id=district_id,
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    label = LABELS[c.kind] + ("" if c.label == "PHILIPPINES" else f", {title(c.label)}")
    p.name(c.id, label, "en", "official")
    base = f"{path}/{slug(label)}"
    unique = base if base not in used else f"{base}-{c.source}"
    used.add(unique)
    p.add("slug", path=unique, entity_id=c.id, is_current=True)
    if c.kind == "party_list":
        p.add("note", id=key("note", c.id), entity_id=c.id, kind="context", body_md=PARTY_LIST_NOTE, lang="en")
    for entry in c.entries:
        if entry.listed:
            p.add(
                "contest_option",
                id=entry.id,
                contest_id=c.id,
                kind="list",
                party_id=list_party(entry.name),
                ballot_position=entry.ordinal,
                ballot_label=entry.name,
            )
            continue
        independent = entry.party in INDEPENDENT
        member = party(entry.party) if entry.party and not independent else None
        p.add(
            "contest_option",
            id=entry.id,
            contest_id=c.id,
            kind="candidate",
            party_id=member,
            ballot_position=entry.ordinal,
            ballot_label=entry.text,
        )
        person = key("person", year, entry.id)
        p.add("person", id=person)
        p.name(person, entry.name, "fil", "ballot")
        p.add(
            "candidacy",
            id=key("candidacy", year, entry.id),
            person_id=person,
            option_id=entry.id,
            role="head",
            ballot_name=entry.name,
            member_of_party_id=member,
            is_independent=independent,
        )


def election(year: int, name: str = "national") -> Package:
    """One package of an election: `national` holds the election, its precincts, and the contests of the whole
    country; each region's package holds the local contests held there. Splitting keeps every package's checks
    small, since the check that each option belongs to the contest of its result set compares every option with
    every result set."""
    day = DAYS[year]
    folder = scan_2022() if year == 2022 else scan_2025()
    main = name == "national"
    p = Bulk(
        f"{SOURCE}/{day}" if main else f"{SOURCE}/{day}-{name.lower()}",
        SOURCE,
        f"The {year} Philippine national and local elections by clustered precinct, from the Commission on Elections"
        + (
            ", presidential and vice presidential contests"
            if main
            else ", Senate contest"
            if name == "senate"
            else ", party-list contest"
            if name == "partylist"
            else f", local contests in region {name}"
        ),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"] + ([] if main else [f"{SOURCE}/{day}"]),
    )
    p.external |= {PHILIPPINES, COUNTRY}
    election_id = key("election", day)
    path = f"ph/{day}"
    for url in FILES[year]:
        if "clustered-precinct" in url or url == APP_2025:
            if main:
                p.artifact(raw.cached(SOURCE, url), SOURCES[year])
            else:
                p.uses(raw.cached(SOURCE, url))
    if main:
        p.uses(raw.cached(SOURCE, CANVASS_2022 if year == 2022 else CANVASS_2025))
        p.add(
            "election", id=election_id, polity_id=PHILIPPINES, kind="general", date_from=day, date_to=day, status="held"
        )
        p.add("slug", path=path, entity_id=election_id, is_current=True)
        p.name(election_id, f"{year} Philippine national and local elections", "en", "official")
        _units(year, p)
    _unit_dim(year, folder)
    contests = [c for c in entered(year) if group(year, c) == name]
    completeness = _completeness(year)
    dashboard = _dashboard() if year == 2025 else {}
    expected = dashboard.get("localErExpected", 0) + dashboard.get("overseasErExpected", 0)
    as_of = _as_of_2025() if year == 2025 else None
    present = len(pl.read_parquet(folder / "units.dim.parquet"))
    used: set[str] = set()
    districts_of: list[tuple[int, str]] = []
    for c in contests:
        _contest(p, year, c, election_id, path, used)
        place = c.place
        if place and place[0] in {s for s, _, _ in DISTRICTS}:
            districts_of.append((c.c, area(*place)))
        if year == 2022:
            share = completeness.get(c.c)
        else:
            share = present / expected if expected and c.kind in NATIONAL else None
        result_set = key("result_set", c.year, c.source)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=c.id,
            stage="official",
            kind="counted",
            source_id=SOURCES[year],
            status="active",
            is_default=True,
            completeness=share,
            as_of=as_of,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=method(year),
            lang="en",
        )
    if year == 2025 and name in unbalanced_groups():
        p.accept(
            "accounting.ballots_sum",
            "The returns of precincts "
            + " and ".join(f"{code} ({place})" for code, place in UNBALANCED.items())
            + " print contest totals (candidate votes, overvotes, and undervotes) that exceed the ballots they report as "
            "processed by one in every single-vote contest; the figures come from the Commission's returns.",
        )
    dims = _dims(folder, name, contests, districts_of)
    if districts_of:
        p.frame("reporting_unit_area", _district_units(folder, dims))

    def finish(out: Path) -> None:
        _write_values(folder, dims, out)

    if contests:
        p.later.append(finish)
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(k for year in DAYS for k in packages_of(year))]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        for name in ("national", *regions_of(year)):
            election(year, name).write()


def emit() -> None:
    package.run("load", packages())
