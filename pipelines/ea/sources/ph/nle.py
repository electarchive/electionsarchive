"""The Philippines' Commission on Elections (COMELEC): the 2016 and 2019 national and local elections.

The Commission's results sites for these two elections are offline. Their JSON survives in public copies made
while the sites were live, and this module reads those copies file by file:

- 2019: every file of the results site (regions, contests, and the electronic return of each clustered precinct),
  saved in June 2019 with a SHA-256 manifest of the files.
- 2016: the city and municipal tallies of the results site, saved on 14 May 2016 while the count was still coming in.
"""

import functools
import json
import re
import shutil
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid5

import duckdb
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "ph.nle"
PHILIPPINES = str(uuid5(package.NAMESPACE, "polity\x1fPH"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fPHL"))

MIRROR_2019 = "https://raw.githubusercontent.com/aahilario/nle2019-raw/db596c71c54ce16702cc9afe0daccb5b67fde44f/data"
MANIFEST_2019 = f"{MIRROR_2019}/SHA256SUM"
SITE_2019 = "https://2019electionresults.comelec.gov.ph/"
MIRROR_2016 = (
    "https://raw.githubusercontent.com/avsolatorio/pilipinaselectionresults2016-scraper/"
    "7eb8c2703dce63b0c004f6828308527ca44b0125/Elections/data"
)
COUNTRY_2016 = f"{MIRROR_2016}/PHILIPPINES/PHILIPPINES.json"
SITE_2016 = "https://www.pilipinaselectionresults2016.com/"
DAYS = {2016: date(2016, 5, 9), 2019: date(2019, 5, 13)}
# The 2016 copy was saved from the site at this time (UTC), while the count was still coming in.
AS_OF_2016 = datetime(2016, 5, 13, 17, 42, 31, tzinfo=UTC)
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


SOURCES = {year: key("source", year) for year in DAYS}


@functools.cache
def _json(url: str) -> Any:
    return json.loads(raw.cached(SOURCE, url).path.read_bytes())


def contest_url(code: int) -> str:
    return f"{MIRROR_2019}/contests/{code}.json"


def folder_name(name: str) -> str:
    """The folder the 2016 copy saved a place under: its name folded to ASCII without apostrophes, with each run of
    other characters turned into a hyphen."""
    plain = unicodedata.normalize("NFKD", name.replace("'", "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", plain).strip("-")


def walk_2016() -> list[tuple[str, dict[str, Any]]]:
    """Every place file of the 2016 copy that the cache holds, from the country down to cities and municipalities,
    as (folder url, file)."""
    found: list[tuple[str, dict[str, Any]]] = []
    stack = [(f"{MIRROR_2016}/PHILIPPINES", COUNTRY_2016)]
    keys = raw.keys(SOURCE)
    while stack:
        base, url = stack.pop()
        if url not in keys:
            continue
        data = _json(url)
        found.append((base, data))
        if data.get("level", 0) < 4:
            for child in data.get("subRegions", {}).values():
                name = folder_name(child["name"])
                stack.append((f"{base}/{name}", f"{base}/{name}/{name}.json"))
    return found


def fetch_2016() -> int:
    """Crawl the 2016 copy level by level, as its files link to each other, then fetch the tallies each city and
    municipality lists."""
    raw.fetch(SOURCE, COUNTRY_2016, suffix=".json")
    seen: set[str] = set()
    while True:
        places = walk_2016()
        wanted = []
        for base, data in places:
            if data.get("level", 0) < 4:
                for child in data.get("subRegions", {}).values():
                    name = folder_name(child["name"])
                    wanted.append(f"{base}/{name}/{name}.json")
        wanted = [url for url in wanted if url not in seen]
        if not wanted:
            break
        seen.update(wanted)
        raw.fetch_many(SOURCE, wanted, suffix=".json", workers=32)
    tallies = [
        f"{base}/{contest['url']}"
        for base, data in walk_2016()
        if data.get("level") == 4
        for contest in data.get("contests") or []
    ]
    got = raw.fetch_many(SOURCE, tallies, suffix=".json", workers=32)
    print(json.dumps({"2016 places": len(walk_2016()), "2016 tallies": len(got), "listed": len(tallies)}))
    return len(got)


def manifest_2019() -> dict[str, str]:
    """The path of each file the 2019 copy holds, relative to its data folder, with its SHA-256."""
    found: dict[str, str] = {}
    for line in raw.cached(SOURCE, MANIFEST_2019).path.read_text().splitlines():
        if line.strip():
            digest, path = line.split(maxsplit=1)
            path = path.removeprefix("./")
            if path != "SHA256SUM":  # the manifest lists itself, hashed before it was complete
                found[path] = digest
    return found


def fetch() -> None:
    fetch_2016()
    raw.fetch(SOURCE, MANIFEST_2019, suffix=".txt")
    files = manifest_2019()
    urls = [f"{MIRROR_2019}/{path}" for path in sorted(files)]
    got = raw.fetch_many(SOURCE, urls, suffix=".json", workers=32)
    missing = [url for url in urls if url not in got]
    wrong = [url for url, artifact in got.items() if artifact.sha256 != files[url.removeprefix(MIRROR_2019 + "/")]]
    if missing or wrong:
        raise ValueError(f"2019: {len(missing)} files missing and {len(wrong)} not matching the manifest")
    # The manifest leaves out the contest files; every contest a place lists has one.
    codes = sorted({c for path in files if path.startswith("regions/") for c in _json(f"{MIRROR_2019}/{path}")["cs"]})
    contests = raw.fetch_many(SOURCE, [contest_url(c) for c in codes], suffix=".json", workers=32)
    # The copy lacks the files of 20 contests (the mayor, vice mayor, and council of seven municipalities); their
    # lines in the returns are left out.
    if len(codes) - len(contests) > 20:
        raise ValueError(f"2019: {len(codes) - len(contests)} contest files missing")
    print(json.dumps({"2019": len(got), "contests": len(contests)}))


def results_url(path: str) -> str:
    return f"{MIRROR_2019}/results/{path}.json"


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
    """Split a ballot line such as "DUTERTE, RODY (PDPLBN)" into the name and the party abbreviation."""
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
    ("REGIONAL VICE-GOVERNOR", "regional_vice_governor"),
    ("REGIONAL GOVERNOR", "regional_governor"),
    ("ASSEMBLYMAN", "assembly"),
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
    "regional_vice_governor": "Regional vice governor",
    "regional_governor": "Regional governor",
    "assembly": "Member of the Regional Legislative Assembly",
    "vice_governor": "Provincial vice governor",
    "governor": "Provincial governor",
    "vice_president": "Vice president",
    "president": "President",
    "vice_mayor": "Vice mayor",
    "mayor": "Mayor",
    "senator": "Senators",
    "party_list": "Party-list representatives",
}
NATIONAL = ("president", "vice_president", "senator", "party_list")


def contest_kind(text: str) -> tuple[str, str]:
    """Return the kind of contest a printed name stands for and the place part of the name."""
    upper = clean(text).upper()
    for prefix, kind in KINDS:
        if upper.startswith(prefix):
            return kind, clean(re.sub(r"^OF\s+", "", upper[len(prefix) :].strip(), flags=re.IGNORECASE))
    raise ValueError(f"unknown contest: {text}")


def district(label: str) -> tuple[str, str] | None:
    """Split "ABRA - SECOND PROVDIST" or "QUEZON CITY - FIRST DIST" into the place and the district part."""
    match = re.match(
        r"^(.*?)\s+-\s+((?:LONE|FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|[0-9A-Z]+)\s+\w*DIST)$", label
    )
    return (match.group(1), match.group(2)) if match else None


def district_name(label: str) -> str:
    return title(re.sub(r"\b(?:LEG|PROV|REG)?DIST\b", "DISTRICT", label))


def fold(name: str) -> str:
    return re.sub(r"[^A-Z0-9Ñ]+", " ", re.sub(r"\(.*?\)", " ", name.upper())).strip()


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


# ---------------------------------------------------------------------------------------------------------------
# Places

SCHEMES = {"Region": "ph.region", "Province": "ph.province", "City/Municipality": "ph.city", "Barangay": "ph.barangay"}
DISTRICTS = {
    "house": "ph.legdist",
    "provincial_board": "ph.provdist",
    "city_council": "ph.citydist",
    "municipal_council": "ph.citydist",
    "assembly": "ph.regdist",
}
EXISTING = ["ph.region", "ph.province", "ph.city", "ph.barangay", "ph.legdist", "ph.provdist", "ph.citydist"]
PRECINCT = "ph.precinct"
REGDIST = "ph.regdist"
OVERSEAS = {"OAV"}
Place = tuple[str, str]
Tree = dict[Place, tuple[str, Place | None]]


@functools.cache
def regions_2019() -> dict[int, dict[str, Any]]:
    """Every place file of the 2019 site, by its number."""
    found = {}
    for path in manifest_2019():
        if path.startswith("regions/"):
            node = _json(f"{MIRROR_2019}/{path}")
            found[node["rc"]] = node
    return found


@functools.cache
def tree_2019() -> tuple[Tree, dict[int, Place | None]]:
    """The 2019 places as (scheme, code) -> (name, parent), without the overseas posts, and the place of each node
    number (None for an overseas node)."""
    nodes: Tree = {}
    places: dict[int, Place | None] = {}
    regions = regions_2019()

    def visit(rc: int, parent: Place | None, abroad: bool) -> None:
        node = regions[rc]
        abroad = abroad or node["rcc"] in OVERSEAS
        place = None if abroad else (SCHEMES[node["can"]], node["rcc"])
        places[rc] = place
        if place is not None:
            nodes[place] = (clean(node["rn"]), parent)
        for child in node["srs"]:
            visit(int(child), place, abroad)

    for rc, node in regions.items():
        if node["cl"] == 1:
            places[rc] = None
        elif node["cl"] == 2:
            visit(rc, None, False)
    return nodes, places


@functools.cache
def precincts_2019() -> list[dict[str, Any]]:
    """Every clustered precinct the 2019 site lists, with its barangay (None abroad) and voting center."""
    _, places = tree_2019()
    found = []
    for rc, node in regions_2019().items():
        for center in node["pps"]:
            for box in center["vbs"]:
                found.append(
                    {
                        "vbc": box["vbc"],
                        "code": box["pre"],
                        "url": box["url"],
                        "type": box["type"],
                        "center": clean(center["ppn"]),
                        "place": places[rc],
                        "contests": box["cs"],
                    }
                )
    return found


@functools.cache
def contests_2019() -> dict[int, dict[str, Any]]:
    """The 2019 contests whose file the copy holds, with the place each is held in (None for the country)."""
    _, places = tree_2019()
    home: dict[int, Place | None] = {}
    for rc, node in regions_2019().items():
        for c in node["tcs"]:
            home[c] = places[rc]
    keys = raw.keys(SOURCE)
    found: dict[int, dict[str, Any]] = {}
    codes = sorted({c for node in regions_2019().values() for c in node["cs"]})
    for c in codes:
        if contest_url(c) not in keys:
            continue
        contest = _json(contest_url(c))
        kind, label = contest_kind(contest["cn"])
        found[c] = {"kind": kind, "label": label, "name": contest["cn"], "bos": contest["bos"], "home": home.get(c)}
    return found


def missing_contests_2019() -> list[int]:
    codes = {c for node in regions_2019().values() for c in node["cs"]}
    return sorted(codes - set(contests_2019()))


# ---------------------------------------------------------------------------------------------------------------
# Scanning the 2019 returns into Parquet chunks of integer keys (unit, contest, option, measure, value).

COLUMNS = {"u": pl.Int32, "c": pl.Int32, "o": pl.Int32, "m": pl.Int16, "v": pl.Int64}
STATS = {
    "expected-voters": REGISTERED,
    "number-of-voters-who-actually-voted": CAST,
    "votes-obtained-by-all-candidates": VALID,
    "overvote-count": INVALID,
    "undervote-count": BLANK,
}


def _chunk(folder: Path, name: str, rows: dict[str, list[int]]) -> None:
    pl.DataFrame(rows, schema=COLUMNS).write_parquet(folder / f"{name}.parquet", compression="zstd", statistics=False)


def scan_2019() -> Path:
    folder = PARSED / "2019"
    stamp = raw.cached(SOURCE, MANIFEST_2019).sha256
    meta = folder / "meta.json"
    if meta.exists() and json.loads(meta.read_text())["stamp"] == stamp:
        return folder
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    contests = contests_2019()
    options = {c: {b["boc"] for b in contest["bos"]} for c, contest in contests.items()}
    files = manifest_2019()
    ratios: dict[int, Counter[int]] = defaultdict(Counter)
    present: list[int] = []
    sheets: Counter[int] = Counter()
    odd: Counter[str] = Counter()
    rows: dict[str, list[int]] = {name: [] for name in COLUMNS}
    part = 0
    for index, box in enumerate(sorted(precincts_2019(), key=lambda b: b["vbc"])):
        if f"results/{box['url']}.json" not in files:
            odd["return not saved"] += 1
            continue
        result = json.loads(raw.cached(SOURCE, results_url(box["url"])).path.read_bytes())
        if "errorResponse" in result:
            odd["return not published (the site answered 404)"] += 1
            continue
        unit = box["vbc"]
        if result["vbc"] != unit:
            raise ValueError(f"{box['url']} holds the return of {result['vbc']}")
        present.append(unit)
        stats: dict[int, dict[str, int | float]] = defaultdict(dict)
        for stat in result["cos"]:
            stats[stat["cc"]][stat["cn"]] = stat["ct"]
        for contest, found in stats.items():
            if contest not in contests:
                odd["contest lines of contests without a file"] += 1
                continue
            sheets[contest] += 1
            processed = found.get("processed-ballots") or 0
            valid = found.get("valid-votes") or 0
            if processed and valid % processed == 0:
                ratios[contest][int(valid // processed)] += 1
            for name, measure in STATS.items():
                if name in found:
                    for column, item in zip(rows.values(), (unit, contest, -1, measure, int(found[name])), strict=True):
                        column.append(item)
        for item in result["rs"]:
            if item["cc"] not in contests:
                continue
            if item["bo"] not in options[item["cc"]]:
                raise ValueError(f"option {item['bo']} is not in contest {item['cc']}")
            for column, value in zip(rows.values(), (unit, item["cc"], item["bo"], VOTES, item["v"]), strict=True):
                column.append(value)
        if len(rows["u"]) > 5_000_000:
            _chunk(folder, f"values-{part:02d}", rows)
            rows = {name: [] for name in COLUMNS}
            part += 1
            print(f"2019: {index + 1:,} precincts read", flush=True)
    _chunk(folder, f"values-{part:02d}", rows)
    meta.write_text(
        json.dumps(
            {
                "stamp": stamp,
                "present": sorted(present),
                "sheets": {str(c): n for c, n in sheets.items()},
                "votes_per_ballot": {str(c): r.most_common(1)[0][0] for c, r in ratios.items()},
                "ratios": {str(c): dict(r) for c, r in ratios.items()},
                "odd": dict(odd),
            }
        )
    )
    return folder


def meta_2019() -> dict[str, Any]:
    return json.loads((scan_2019() / "meta.json").read_text())


# ---------------------------------------------------------------------------------------------------------------
# The 2016 tallies


def _int(text: str) -> int:
    return int(text.replace(",", ""))


def _rows(stats: dict[str, Any], group: str) -> dict[str, str]:
    return {row["label"]: row["value"] for row in stats.get(group, {}).get("rows", [])}


@functools.cache
def tree_2016() -> tuple[Tree, list[dict[str, Any]]]:
    """The 2016 places as (scheme, code) -> (name, parent), without the overseas posts, and each city or
    municipality (or overseas post) with its tallies."""
    nodes: Tree = {}
    cities: list[dict[str, Any]] = []
    levels = {2: "ph.region", 3: "ph.province", 4: "ph.city"}
    places: dict[str, tuple[Place | None, bool]] = {}
    keys = raw.keys(SOURCE)
    for base, data in sorted(walk_2016(), key=lambda item: item[1]["level"]):
        level = data["level"]
        if level == 1:
            continue
        parent, abroad = places.get(base.rsplit("/", 1)[0], (None, False))
        abroad = abroad or data["customCode"] in OVERSEAS
        place = None if abroad else (levels[level], data["customCode"])
        places[base] = (place, abroad)
        if place is not None:
            nodes[place] = (clean(data["name"]), parent)
        if level == 4:
            tallies = [_json(f"{base}/{c['url']}") for c in data.get("contests") or [] if f"{base}/{c['url']}" in keys]
            cities.append({"code": data["customCode"], "name": clean(data["name"]), "place": place, "tallies": tallies})
    return nodes, cities


@functools.cache
def contests_2016() -> dict[str, dict[str, Any]]:
    """The 2016 contests by the site's code, with the cities whose tallies hold them and their options."""
    found: dict[str, dict[str, Any]] = {}
    for city in tree_2016()[1]:
        for tally in city["tallies"]:
            kind, label = contest_kind(tally["name"])
            contest = found.setdefault(
                tally["code"], {"kind": kind, "label": label, "name": clean(tally["name"]), "cities": [], "options": {}}
            )
            if city["place"] is not None:
                contest["cities"].append(city["place"])
            for item in tally["results"]:
                contest["options"].setdefault(item["canCode"], clean(item["bName"]))
    return found


# ---------------------------------------------------------------------------------------------------------------
# Where each contest is held

SPLIT = date(2017, 8, 7)  # Negros Island Region dissolved; a place whose parent changed takes the later one here


@functools.cache
def parents() -> dict[Place, Place | None]:
    found: dict[Place, Place | None] = {}
    for year in sorted(DAYS):
        for place, (_, parent) in trees()[year].items():
            found[place] = parent
    return found


@functools.cache
def trees() -> dict[int, Tree]:
    return {2016: tree_2016()[0], 2019: tree_2019()[0]}


def _common(places: list[Place], year: int) -> Place | None:
    """The single city of a list of cities, or else the province they share."""
    counted = Counter(places)
    if len(counted) == 1:
        return next(iter(counted))
    provinces = {trees()[year].get(city, ("", None))[1] for city in counted}
    return provinces.pop() if len(provinces) == 1 else None


@functools.cache
def contest_places() -> dict[tuple[int, str], Place | None]:
    """The area each contest elects in, as (scheme, code): None for the country, else a region, province, city, or
    district."""
    found: dict[tuple[int, str], Place | None] = {}
    for c, contest in contests_2019().items():
        found[2019, str(c)] = _place(contest["kind"], contest["label"], contest["home"])
    for code, contest in contests_2016().items():
        home: Place | None = None
        if contest["kind"] in ("regional_governor", "regional_vice_governor"):
            home = ("ph.region", "ARMM")
        elif contest["kind"] in ("governor", "vice_governor", "assembly"):
            provinces = Counter(parents().get(city) for city in contest["cities"])
            home = provinces.most_common(1)[0][0] if provinces else None
        elif contest["cities"]:
            home = _common(contest["cities"], 2016)
        found[2016, code] = _place(contest["kind"], contest["label"], home)
    return found


def _place(kind: str, label: str, home: Place | None) -> Place | None:
    if kind in NATIONAL:
        return None
    if kind not in DISTRICTS:
        return home
    split = district(label)
    if split is None or (kind in ("city_council", "municipal_council") and split[1].startswith("LONE")):
        return home
    return (DISTRICTS[kind], label)


@functools.cache
def districts() -> dict[Place, Place | None]:
    """The parent of each district: the place whose contests it belongs to."""
    found: dict[Place, Place | None] = {}
    for c, contest in contests_2019().items():
        place = contest_places()[2019, str(c)]
        if place is not None and place[0] in DISTRICTS.values():
            found.setdefault(place, contest["home"])
    for code, contest in contests_2016().items():
        place = contest_places()[2016, code]
        if place is not None and place[0] in DISTRICTS.values() and place not in found:
            if contest["kind"] == "assembly":
                provinces = Counter(parents().get(city) for city in contest["cities"])
                found[place] = provinces.most_common(1)[0][0] if provinces else None
            else:
                found[place] = _common(contest["cities"], 2016)
    return found


def region_of(year: int, place: Place | None) -> str | None:
    tree = trees()[year]
    if place is not None and place[0] in DISTRICTS.values():
        place = districts().get(place)
    while place is not None and place[0] != "ph.region":
        found = tree.get(place)
        place = found[1] if found else parents().get(place)
    return place[1] if place else None


def _source(p: Package, year: int) -> None:
    p.add(
        "source",
        id=SOURCES[year],
        kind="official_body",
        publisher="Commission on Elections",
        title=f"{year} National and Local Elections results",
        url=SITE_2019 if year == 2019 else SITE_2016,
        license=None,
        license_url=None,
        attribution="Commission on Elections",
        notes=(
            "The results site is offline; its files are read from a copy saved while it was live: "
            + (
                "every file of the site, saved in June 2019."
                if year == 2019
                else "the tallies of each city and municipality, saved on 14 May 2016 while the count was coming in."
            )
        ),
    )


# ---------------------------------------------------------------------------------------------------------------
# Geography


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The Philippines' regions, provinces, cities and municipalities, and barangays, and the House, provincial "
        "board, council, and regional assembly districts, as the Commission on Elections lists them for 2016 and 2019",
    )
    p.external |= {PHILIPPINES, COUNTRY, *EXISTING, PRECINCT}
    for year in DAYS:
        _source(p, year)
    p.artifact(raw.cached(SOURCE, MANIFEST_2019), SOURCES[2019])
    p.artifact(raw.cached(SOURCE, COUNTRY_2016), SOURCES[2016])
    for path in sorted(manifest_2019()):
        if path.startswith("regions/"):
            p.uses(raw.cached(SOURCE, f"{MIRROR_2019}/{path}"))
    for c in sorted(contests_2019()):
        p.uses(raw.cached(SOURCE, contest_url(c)))
    for base, data in walk_2016():
        p.uses(raw.cached(SOURCE, f"{base}/{base.rsplit('/', 1)[1]}.json"))
    p.add("scheme", code=REGDIST, polity_id=PHILIPPINES, kind="electoral_district", rank=6, noun="regional district")
    places: dict[Place, dict[int, tuple[str, Place | None]]] = defaultdict(dict)
    for year in DAYS:
        for place, found in trees()[year].items():
            places[place][year] = found
    for place, seen in sorted(places.items()):
        scheme, code = place
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        latest = seen[max(seen)]
        p.name(id, latest[0], "fil", "official")
        for name, _ in seen.values():
            if fold(name) != fold(latest[0]):
                p.name(id, name, "fil", "historical", False)
        edges = {year: found[1] for year, found in seen.items()}
        if len(set(edges.values())) == 1:
            parent = next(iter(edges.values()))
            p.add("area_parent", area_id=id, parent_id=area(*parent) if parent else COUNTRY)
        else:
            for year, parent in edges.items():
                p.add(
                    "area_parent",
                    area_id=id,
                    parent_id=area(*parent) if parent else COUNTRY,
                    valid_from=None if year == 2016 else SPLIT,
                    valid_to=SPLIT if year == 2016 else None,
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
BODIES = {
    "senator": ("senate", "upper_house", "Senate of the Philippines", "Senado ng Pilipinas"),
    "house": ("house", "lower_house", "House of Representatives of the Philippines", "Kapulungan ng mga Kinatawan"),
    "party_list": (
        "house",
        "lower_house",
        "House of Representatives of the Philippines",
        "Kapulungan ng mga Kinatawan",
    ),
    "provincial_board": ("provincial board", "council", "Sangguniang Panlalawigan (provincial board)", None),
    "city_council": ("city council", "council", "Sangguniang Panlungsod (city council)", None),
    "municipal_council": ("municipal council", "council", "Sangguniang Bayan (municipal council)", None),
    "assembly": ("armm assembly", "unicameral", "Regional Legislative Assembly of the ARMM", None),
}
OFFICES = {
    "president": ("president", "head_of_state", "President of the Philippines", "Pangulo ng Pilipinas"),
    "vice_president": ("vice president", "other", "Vice President of the Philippines", None),
    "regional_governor": ("regional governor", "governor", "Regional Governor of the ARMM", None),
    "regional_vice_governor": ("regional vice governor", "other", "Regional Vice Governor of the ARMM", None),
    "governor": ("governor", "governor", "Provincial governor", "Gobernador"),
    "vice_governor": ("vice governor", "other", "Provincial vice governor", None),
    "mayor": ("mayor", "mayor", "Mayor of a city or municipality", "Alkalde"),
    "vice_mayor": ("vice mayor", "deputy_mayor", "Vice mayor of a city or municipality", None),
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
    """The party abbreviations printed on the ballots of both years, with a long name where 2019 gives one, and the
    names of the party-list organizations."""
    modal: dict[int, Counter[str]] = defaultdict(Counter)
    long: dict[str, Counter[tuple[int, str]]] = defaultdict(Counter)
    abbreviations: dict[str, str | None] = {}
    lists: set[str] = set()
    for contest in contests_2019().values():
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
        if modal[pc].most_common(1)[0][0] == abbreviation and fold(name) != fold(abbreviation):
            abbreviations[abbreviation] = name
    for contest in contests_2016().values():
        for text in contest["options"].values():
            if contest["kind"] == "party_list":
                lists.add(party_list_key(text))
                continue
            _, abbreviation = candidate(text)
            if abbreviation and abbreviation not in INDEPENDENT:
                abbreviations.setdefault(abbreviation, None)
    return abbreviations, lists


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Philippine Senate, House, provincial boards and councils, the ARMM assembly, local executive offices, and "
        "the parties on the 2016 and 2019 ballots",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {PHILIPPINES}
    p.uses(raw.cached(SOURCE, MANIFEST_2019))
    p.uses(raw.cached(SOURCE, COUNTRY_2016))
    for name, kind, english, native in dict.fromkeys(BODIES.values()):
        id = key("body", name)
        p.add("body", id=id, polity_id=PHILIPPINES, kind=kind)
        p.name(id, english, "en", "official")
        if native:
            p.name(id, native, "fil", "native", False)
    for name, kind, english, native in OFFICES.values():
        id = key("office", name)
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


def method(year: int) -> str:
    if year == 2016:
        return (
            "Counted from the tally of each city and municipality on the Commission on Elections' 2016 results site, as "
            "a copy saved on 14 May 2016 holds it, while precincts were still transmitting their returns. Each tally "
            "sums the returns its city or municipality had transmitted by then; the completeness of each result set is "
            "the share of clustered precincts whose returns the tallies count. Ballots cast are the ballots counted in "
            "those returns, and registered voters are given only for a city or municipality whose every precinct had "
            "reported. These are unofficial partial counts, not the canvass of the boards of canvassers. A count of "
            "zero votes for an option is not stored. Proclamations are not recorded here."
        )
    return (
        "Counted from the electronic return of each clustered precinct, as the Commission on Elections published them "
        "on its 2019 results site and a copy of the site saved in June 2019 holds them. A clustered precinct is the "
        "group of precincts that share one counting machine. Registered voters are the voters the return lists as "
        "expected, ballots cast are the ballots the machine processed, and valid votes are the votes the return gives "
        "to candidates, counted for contests where each ballot carries one vote. Invalid ballots are the ballots "
        "overvoted in the contest, and blank ballots are the ballots with no vote for it. Where a ballot carries "
        "several votes, the return counts votes in the contest totals, so valid votes and blank ballots are left out "
        "and only the votes of each candidate are stored. A count of zero votes for an option is not stored: an option "
        "with no value in a unit that reports the contest received no votes there. Returns the site did not publish "
        "are missing. Proclamations are made by the boards of canvassers and are not recorded here."
    )


class Entry:
    """One option of a contest."""

    def __init__(self, o: int, id: str, text: str, name: str, ordinal: int | None, party: str | None, listed: bool):
        self.o, self.id, self.text, self.name = o, id, text, name
        self.ordinal, self.party, self.listed = ordinal, party, listed


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
    def result_set(self) -> str:
        return key("result_set", self.year, self.source)

    @property
    def place(self) -> Place | None:
        return contest_places()[self.year, self.source]


def _entry(year: int, contest: str, o: int, text: str, kind: str, ordinal: int | None) -> Entry:
    option = key("option", year, contest, o)
    if kind == "party_list":
        number_, name = number(text)
        return Entry(o, option, clean(text), party_list_key(text), ordinal or number_, None, True)
    name, abbreviation = candidate(text)
    return Entry(o, option, clean(text), name, ordinal, abbreviation, False)


@functools.cache
def entered(year: int) -> list[Entered]:
    found: list[Entered] = []
    if year == 2019:
        votes = meta_2019()["votes_per_ballot"]
        for c, contest in sorted(contests_2019().items()):
            entries = [
                _entry(2019, str(c), option["boc"], option["bon"], contest["kind"], option["to"])
                for option in sorted(contest["bos"], key=lambda o: o["to"])
            ]
            found.append(Entered(2019, c, str(c), contest["kind"], contest["label"], votes.get(str(c)), entries))
        return found
    for index, (code, contest) in enumerate(sorted(contests_2016().items())):
        entries = [
            _entry(2016, code, int(o), text, contest["kind"], None)
            for o, text in sorted(contest["options"].items(), key=lambda item: number(item[1])[0] or 0)
        ]
        vpb = 1 if contest["kind"] not in DISTRICTS or contest["kind"] in ("house", "assembly") else None
        found.append(Entered(2016, index, code, contest["kind"], contest["label"], vpb, entries))
    return found


def group(year: int, c: Entered) -> str:
    """The package a 2019 contest goes in: `senate`, `partylist`, or the region a local contest is held in."""
    if c.kind in NATIONAL:
        return {"senator": "senate", "party_list": "partylist"}.get(c.kind, "national")
    return region_of(year, c.place) or "other"


@functools.cache
def regions_of(year: int) -> list[str]:
    return sorted({group(year, c) for c in entered(year)} - {"national"})


def packages_of(year: int) -> list[str]:
    if year == 2016:
        return [f"{SOURCE}/{DAYS[year]}"]
    return [f"{SOURCE}/{DAYS[year]}", *(f"{SOURCE}/{DAYS[year]}-{r.lower()}" for r in regions_of(year))]


def _contest(p: Package, c: Entered, election_id: str, path: str, used: set[str]) -> None:
    office = OFFICES.get(c.kind)
    body = BODIES.get(c.kind)
    place = c.place
    district_id: str | None = COUNTRY if c.kind in NATIONAL else area(*place) if place else None
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
        office_id=key("office", office[0]) if office else None,
        body_id=key("body", body[0]) if body else None,
        district_area_id=district_id,
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    label = LABELS[c.kind] + ("" if c.label in ("PHILIPPINES", "") else f", {title(c.label)}")
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
        person = key("person", entry.id)
        p.add("person", id=person)
        p.name(person, entry.name, "fil", "ballot")
        p.add(
            "candidacy",
            id=key("candidacy", entry.id),
            person_id=person,
            option_id=entry.id,
            role="head",
            ballot_name=entry.name,
            member_of_party_id=member,
            is_independent=independent,
        )


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


def _unit_dim(folder: Path) -> None:
    day = DAYS[2019]
    present = set(meta_2019()["present"])
    found = [(box["vbc"], key("unit", day, box["code"])) for box in precincts_2019() if box["vbc"] in present]
    pl.DataFrame(
        {"u": [u for u, _ in found], "uid": [i for _, i in found]}, schema={"u": pl.Int32, "uid": pl.String}
    ).write_parquet(folder / "units.dim.parquet")


def _units_2019(p: Package) -> None:
    day = DAYS[2019]
    present = set(meta_2019()["present"])
    known = set(trees()[2019])
    for box in sorted(precincts_2019(), key=lambda b: b["code"]):
        if box["vbc"] not in present:
            continue
        id = key("unit", day, box["code"])
        home = box["place"]
        local = home in known
        p.add(
            "reporting_unit",
            id=id,
            polity_id=PHILIPPINES,
            scheme_code=PRECINCT,
            code=f"{day}:{box['code']}",
            area_relation="within" if local else "unlocated",
            area_id=area(*home) if local and home else None,
            kind="standard" if local else "abroad_mission",
        )
        p.name(id, f"{box['center']} (clustered precinct {box['code']})", "fil", "official")


def _dims(folder: Path, name: str, contests: list[Entered], areas: list[tuple[int, str]]) -> Path:
    out = folder / f"dims-{name}"
    out.mkdir(exist_ok=True)
    pl.DataFrame(
        {"c": [c.c for c in contests], "rid": [c.result_set for c in contests], "vpb": [c.vpb for c in contests]},
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
              order by r.rid, n.uid
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
            where v.m = {REGISTERED}
            order by 1, 2"""
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


def _election(p: Package, year: int) -> str:
    day = DAYS[year]
    election_id = key("election", day)
    p.add("election", id=election_id, polity_id=PHILIPPINES, kind="general", date_from=day, date_to=day, status="held")
    p.add("slug", path=f"ph/{day}", entity_id=election_id, is_current=True)
    p.name(election_id, f"{year} Philippine national and local elections", "en", "official")
    return election_id


def _completeness_2019() -> dict[int, float]:
    expected: Counter[int] = Counter()
    for box in precincts_2019():
        expected.update(box["contests"])
    sheets = meta_2019()["sheets"]
    return {c: min(1.0, sheets.get(str(c), 0) / n) for c, n in expected.items()}


def election_2019(name: str = "national") -> Package:
    """One package of the 2019 election: `national` holds the election and its precincts; `senate`, `partylist`, and
    each region's package hold the contests of that group."""
    year, day = 2019, DAYS[2019]
    folder = scan_2019()
    main = name == "national"
    p = Bulk(
        f"{SOURCE}/{day}" if main else f"{SOURCE}/{day}-{name.lower()}",
        SOURCE,
        "The 2019 Philippine national and local elections by clustered precinct, from the Commission on Elections"
        + (
            ", the election and its precincts"
            if main
            else ", Senate contest"
            if name == "senate"
            else ", party-list contest"
            if name == "partylist"
            else f", local contests in region {name}"
        ),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"] + ([] if main else [f"{SOURCE}/{day}"]),
    )
    p.external |= {PHILIPPINES, COUNTRY} | ({PRECINCT} if main else set())
    election_id = key("election", day)
    path = f"ph/{day}"
    if main:
        p.uses(raw.cached(SOURCE, MANIFEST_2019))
        _election(p, year)
        _units_2019(p)
    _unit_dim(folder)
    contests = [c for c in entered(year) if group(year, c) == name]
    completeness = _completeness_2019()
    used: set[str] = set()
    districts_of: list[tuple[int, str]] = []
    present = set(meta_2019()["present"])
    for c in contests:
        _contest(p, c, election_id, path, used)
        if c.place and c.place[0] in DISTRICTS.values():
            districts_of.append((c.c, area(*c.place)))
        p.add(
            "result_set",
            id=c.result_set,
            election_id=election_id,
            contest_id=c.id,
            stage="official",
            kind="counted",
            source_id=SOURCES[year],
            status="active",
            is_default=True,
            completeness=completeness.get(c.c),
        )
        p.add(
            "note",
            id=key("note", c.result_set),
            entity_id=c.result_set,
            kind="methodology",
            body_md=method(year),
            lang="en",
        )
    for box in precincts_2019():
        if box["vbc"] in present and any(c.c in box["contests"] for c in contests):
            p.uses(raw.cached(SOURCE, results_url(box["url"])))
    for c in contests:
        p.uses(raw.cached(SOURCE, contest_url(c.c)))
    dims = _dims(folder, name, contests, districts_of)
    if districts_of:
        p.frame("reporting_unit_area", _district_units(folder, dims))

    def finish(out: Path) -> None:
        _write_values(folder, dims, out)

    if contests:
        p.later.append(finish)
    return p


def election_2016() -> Package:
    year, day = 2016, DAYS[2016]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "The 2016 Philippine national and local elections by city and municipality, from the Commission on Elections' "
        "partial tallies of 14 May 2016",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PHILIPPINES, COUNTRY, "ph.city"}
    p.uses(raw.cached(SOURCE, COUNTRY_2016))
    election_id = _election(p, year)
    path = f"ph/{day}"
    contests = {c.source: c for c in entered(year)}
    units: dict[str, str] = {}
    known = set(trees()[2016])
    reported: Counter[str] = Counter()
    expected: Counter[str] = Counter()
    keys = raw.keys(SOURCE)

    def value(c: Entered, unit: str, option: str | None, measure: int, figure: int) -> None:
        p.add(
            "result_value",
            result_set_id=c.result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=figure,
        )

    for base, data in walk_2016():
        if data.get("level") == 4:
            for item in data.get("contests") or []:
                if f"{base}/{item['url']}" in keys:
                    p.uses(raw.cached(SOURCE, f"{base}/{item['url']}"))
        elif data.get("level", 0) < 4:
            p.uses(raw.cached(SOURCE, f"{base}/{base.rsplit('/', 1)[1]}.json"))
    for city in sorted(tree_2016()[1], key=lambda c: c["code"]):
        if not city["tallies"]:
            continue
        id = key("unit", day, city["code"])
        units[city["code"]] = id
        local = city["place"] in known
        p.add(
            "reporting_unit",
            id=id,
            polity_id=PHILIPPINES,
            scheme_code="ph.city",
            code=f"{day}:{city['code']}",
            area_relation="equals" if local else "unlocated",
            area_id=area(*city["place"]) if local else None,
            kind="standard" if local else "abroad_mission",
        )
        p.name(id, city["name"], "fil", "official")
        for tally in city["tallies"]:
            c = contests[tally["code"]]
            region = _rows(tally["stats"], "regionInfo")
            total = sum(_int(v) for k, v in region.items() if k.startswith("tally.total"))
            done = sum(_int(v) for k, v in region.items() if k.startswith("tally.reported"))
            expected[c.source] += total
            reported[c.source] += done
            ballots = _rows(tally["stats"], "ballots").get("ballots.total")
            if ballots is not None:
                value(c, id, None, CAST, _int(ballots))
            voters = _rows(tally["stats"], "voters").get("voters.registered")
            if voters is not None and total and done == total:
                value(c, id, None, REGISTERED, _int(voters))
            for item in tally["results"]:
                votes = _int(item["votes"])
                if votes:
                    option = key("option", year, c.source, int(item["canCode"]))
                    value(c, id, option, VOTES, votes)
    used: set[str] = set()
    for c in contests.values():
        _contest(p, c, election_id, path, used)
        if c.place and c.place[0] in DISTRICTS.values():
            for city in tree_2016()[1]:
                if city["code"] in units and any(t["code"] == c.source for t in city["tallies"]):
                    p.add(
                        "reporting_unit_area",
                        unit_id=units[city["code"]],
                        area_id=area(*c.place),
                        method="official_list",
                    )
        p.add(
            "result_set",
            id=c.result_set,
            election_id=election_id,
            contest_id=c.id,
            stage="preliminary",
            kind="counted",
            source_id=SOURCES[year],
            status="active",
            is_default=True,
            completeness=reported[c.source] / expected[c.source] if expected[c.source] else None,
            as_of=AS_OF_2016,
        )
        p.add(
            "note",
            id=key("note", c.result_set),
            entity_id=c.result_set,
            kind="methodology",
            body_md=method(year),
            lang="en",
        )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(k for year in sorted(DAYS) for k in packages_of(year))]


def parse() -> None:
    geo().write()
    registry().write()
    election_2016().write()
    for name in ("national", *regions_of(2019)):
        election_2019(name).write()


def emit() -> None:
    package.run("load", packages())
