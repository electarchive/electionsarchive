"""Liberia's National Elections Commission: polling-place results of the 2005 general election and runoff, of the 2014
Senate election, and of the presidential elections of 2011, 2017, and 2023."""

import functools
import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from uuid import uuid5

import shapely
from shapely.geometry import shape

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.lr import president

SOURCE = "lr.nec"
LIBERIA = str(uuid5(package.NAMESPACE, "polity\x1fLR"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fLBR"))
BROWSER = "browser://20261116-222842-8ee07a/"
PORTAL = "https://results.necliberia.org/"
REPORT = "https://necliberia.org/results/results2005/PDFs/NationalProgressiveresultsReport.pdf"
RUNOFF_REPORT = "https://necliberia.org/results/results2005/PDFs/RunoffProgressiveResultsReport.pdf"
HOUSE_PAGES = BROWSER + "lr-2005-house-results-pages.html"
SENATE_PAGES = BROWSER + "lr-2005-senate-results-pages.html"
BY_BROWSER = "browser://20261117-222523-00a527/"
BY_HOUSE_PAGES = BY_BROWSER + "lr-2005-portal-house-by-elections-2006-2010.html"
BY_SENATE_PAGES = BY_BROWSER + "lr-2005-portal-senate-by-elections-2007-2009.html"
DECLARATION_INDEX = "https://web.archive.org/web/20240417131011id_/https://results.necliberia.org/results2005/Declaration/DeclarationIndex.html"
BOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/LBR/ADM1/geoBoundaries-LBR-ADM1.geojson"
FIRST_DAY = date(2005, 10, 11)
RUNOFF_DAY = date(2005, 11, 8)
SENATE_DAY = date(2014, 12, 20)

# county code, name, file stem of the 2005 polling-place reports, slug of the 2014 county bundle
COUNTIES: dict[str, tuple[str, str, str]] = {
    "03": ("Bomi", "Bomi", "bomi"),
    "06": ("Bong", "Bong", "bong"),
    "09": ("Grand Bassa", "GrandBassa", "grand-bassa"),
    "12": ("Grand Cape Mount", "GrandCapeMount", "grand-cape-mount"),
    "15": ("Grand Gedeh", "GrandGedeh", "grand-gedeh"),
    "18": ("Grand Kru", "GrandKru", "grand-kru"),
    "21": ("Lofa", "Lofa", "lofa"),
    "24": ("Margibi", "Margibi", "margibi"),
    "27": ("Maryland", "Maryland", "maryland"),
    "30": ("Montserrado", "Montserrado", "montserrado"),
    "33": ("Nimba", "Nimba", "nimba"),
    "36": ("River Cess", "RiverCess", "rivercess"),
    "39": ("Sinoe", "Sinoe", "sinoe"),
    "42": ("River Gee", "RiverGee", "river-gee"),
    "45": ("Gbarpolu", "Gbarpolu", "gbarpolu"),
}
PARTIES_2005 = {
    "LDP": "Liberia Destiny Party",
    "LP": "Liberty Party",
    "PRODEM": "Progressive Democratic Party",
    "FDP": "Free Democratic Party",
    "NPL": "National Party of Liberia",
    "UP": "Unity Party",
    "NATVIPOL": "National Vision Party of Liberia",
    "NDM": "New Deal Movement",
    "LERP": "Liberia Equal Rights Party",
    "ULD": "Union of Liberian Democrats",
    "ALCOP": "All Liberian Coalition Party",
    "NPP": "National Patriotic Party",
    "UDA": "United Democratic Alliance",
    "NRP": "National Reformation Party",
    "COTOL": "Coalition for Transformation of Liberia",
    "APD": "Alliance for Peace and Democracy",
    "FAPL": "Freedom Alliance Party of Liberia",
    "RULP": "Reformed United Liberia Party",
    "NDPL": "National Democratic Party of Liberia",
    "CDC": "Congress for Democratic Change",
    "LPL": "Labor Party of Liberia",
}
INDEPENDENT = "Ind"
SENATE_PERIOD = "The 2005 Senate ballot let each voter mark up to two candidates"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NEC_SOURCE = key("source", SOURCE)
BOUNDARY_SOURCE = key("source", "geoboundaries-adm1")
NEW_DISTRICTS_FROM = date(2011, 10, 11)
SENATE_BODY = key("body", "senate")
HOUSE_BODY = key("body", "house")
PRESIDENCY = key("office", "president")
PRESIDENT_SYSTEM = key("electoral_system", "president")
BLOCK_SYSTEM = key("electoral_system", "senate", "block")
SENATE_SYSTEM = key("electoral_system", "senate", "single")
HOUSE_SYSTEM = key("electoral_system", "house")


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def pdf_url(stem: str, runoff: bool) -> str:
    return f"{BROWSER}{'Runoff' if runoff else ''}{stem}ByPollingPlace.pdf"


def county_url(code: str) -> str:
    _, _, slug = COUNTIES[code]
    return f"{BROWSER}lr-2014-senate-county-{code}-{slug}.html"


def urls() -> list[str]:
    found = [REPORT, RUNOFF_REPORT, HOUSE_PAGES, SENATE_PAGES, *president.urls()]
    for code, (_, stem, _) in COUNTIES.items():
        found += [pdf_url(stem, False), pdf_url(stem, True), county_url(code)]
    return found


def fetch() -> None:
    # The official results host answers only some clients; files it refused are supplied to the cache with
    # raw.store under the keys above, so fetching asks only for what is not cached.
    for url in urls():
        if url.startswith("http"):
            raw.fetch(SOURCE, url, suffix=".html" if url in president.urls() else ".pdf")
    raw.fetch(SOURCE, BOUNDARIES, suffix=".geojson")


def text_of(url: str) -> str:
    return _text(url)


@functools.cache
def _text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True, text=True).stdout


@functools.cache
def bundle(url: str) -> dict[str, str]:
    html = raw.cached(SOURCE, url).path.read_bytes().decode("utf-8", errors="replace")
    parts = re.split(r"<!-- SOURCE(?: URL)?: (.*?) -->", html)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def cells(html: str, lines: bool = True) -> list[str]:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.DOTALL)
    body = re.sub(r"</t[dh]>", "|", body)
    body = re.sub(r"<br\s*/?>", "\n" if lines else " ", body)
    body = re.sub(r"</tr>|</p>|</div>|</h\d>", "\n", body)
    body = re.sub(r"<[^>]+>", "", body).replace("&nbsp;", " ").replace("&amp;", "&")
    return [re.sub(r"\s+", " ", part).strip() for part in re.split(r"[|\n]", body)]


def number(text: str) -> int:
    return int(text.replace(",", ""))


@dataclass
class Tally:
    candidates: list[tuple[str, int]] = field(default_factory=list)
    valid: int | None = None
    invalid: int | None = None
    total: int | None = None


@dataclass
class Place:
    precinct: str
    number: int
    name: str
    registered: int | None
    district: int | None = None
    president: Tally | None = None
    senate: Tally | None = None
    house: Tally | None = None

    @property
    def county(self) -> str:
        return self.precinct[:2]

    @property
    def code(self) -> str:
        return f"{self.precinct}-{self.number}"


CANDIDATE = re.compile(r"([A-Za-z][^:\d]*?\s\(\s*[A-Za-z]+\s*\))\s+(\d+)(?=\s{2,}|\s*$)")
TOTALS = re.compile(r"(Valid Votes \+ Invalid Ballots|Valid Votes|Invalid Votes|Invalid Ballots|Total Votes):\s+(\d+)")


def _fill(tally: Tally, label: str, count: int) -> None:
    if label == "Valid Votes":
        tally.valid = count
    elif label.startswith("Invalid"):
        tally.invalid = count
    elif label == "Total Votes":
        tally.total = count


def first_round(code: str) -> list[Place]:
    stem = COUNTIES[code][1]
    places: list[Place] = []
    for page in _text(pdf_url(stem, False)).split("\f"):
        found = re.search(r"Precinct:\s*(\d+)\s+Polling Place:?\s*(\d+)", page)
        if found is None:
            continue
        lines = page.split("\n")
        header = next(line for line in lines if "Candidate" in line and "Votes" in line)
        ends = [m.end() for m in re.finditer("Votes", header)]

        def column(position: int, ends: list[int] = ends) -> int:
            return next((i for i, end in enumerate(ends) if position <= end + 4), len(ends) - 1)

        at = next(i for i, line in enumerate(lines) if "Precinct:" in line)
        name = next(
            (line.strip() for line in lines[at + 1 :] if line.strip() and len(line) - len(line.lstrip()) <= 5), ""
        )
        name = re.split(r"\s{3,}", name)[0]
        registered = re.search(r"Total:\s+([\d,]+)", page)
        district = re.search(r"Electoral District (\d+)", page)
        place = Place(
            found.group(1),
            int(found.group(2)),
            name,
            number(registered.group(1)) if registered else None,
            int(district.group(1)) if district else None,
        )
        tallies = [Tally(), Tally(), Tally()]
        for line in lines:
            for m in TOTALS.finditer(line):
                _fill(tallies[column(m.end())], m.group(1), int(m.group(2)))
            if "Candidate" in line:
                continue
            for m in CANDIDATE.finditer(line):
                tallies[column(m.end())].candidates.append((m.group(1).strip(), int(m.group(2))))
        place.president, place.senate, place.house = (t if t.candidates else None for t in tallies)
        places.append(place)
    return places


def runoff(code: str) -> list[Place]:
    stem = COUNTIES[code][1]
    places: list[Place] = []
    for page in _text(pdf_url(stem, True)).split("\f"):
        lines = page.split("\n")
        heads = [i for i, line in enumerate(lines) if "Precinct:" in line]
        for n, at in enumerate(heads):
            block = lines[at : heads[n + 1] if n + 1 < len(heads) else len(lines)]
            starts = [m.start() for m in re.finditer("Precinct:", lines[at])]

            def column(position: int, starts: list[int] = starts) -> int:
                return max((i for i, start in enumerate(starts) if start - 10 <= position), default=0)

            found = [re.search(r"Precinct:\s*(\d+)\s+Polling Place:?\s*(\d+)", lines[at][s:]) for s in starts]
            names = [block[1][s : starts[i + 1] if i + 1 < len(starts) else None].strip() for i, s in enumerate(starts)]
            registered = [None] * len(starts)
            tallies = [Tally() for _ in starts]
            for line in block:
                for m in re.finditer(r"Registered Voters:\s*([\d,]+)", line):
                    registered[column(m.start())] = number(m.group(1))
                for m in TOTALS.finditer(line):
                    _fill(tallies[column(m.start())], m.group(1), int(m.group(2)))
                if "Candidate" in line:
                    continue
                for m in CANDIDATE.finditer(line):
                    tallies[column(m.start())].candidates.append((m.group(1).strip(), int(m.group(2))))
            for i, match in enumerate(found):
                if match is None:
                    raise ValueError(f"{code}: no polling place in {block[0]!r}")
                places.append(Place(match.group(1), int(match.group(2)), names[i], registered[i], president=tallies[i]))
    return places


def party_of(label: str) -> str:
    return label.rsplit("(", 1)[1].rstrip(")").strip()


def ballot_name(label: str) -> str:
    return label.rsplit("(", 1)[0].strip()


def fold(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _given_word(word: str) -> str:
    """A given name printed in capitals, in capitals and small letters; initials stay as printed."""
    if len(word.rstrip(".")) <= 2 or re.fullmatch(r"(?:[A-Z]\.)+[A-Z]?\.?", word):
        return word
    return "-".join(part.capitalize() for part in word.split("-"))


def person_name(printed: str) -> str:
    """'DIVINE, SR., Samuel Raymond' to 'Samuel Raymond Divine, Sr.'"""
    parts = [part.strip() for part in printed.split(",")]
    if len(parts) < 2:
        return printed.strip()
    surname, rest = parts[0], parts[1:]
    suffix = [part for part in rest if re.fullmatch(r"(SR|JR|II|III|IV)\.?", part, re.IGNORECASE)]
    given = ", ".join(part for part in rest if part not in suffix)
    if given.isupper():
        given = " ".join(_given_word(word) for word in given.split())
    family = "-".join(" ".join(word.capitalize() for word in piece.split()) for piece in surname.split("-"))
    text = f"{given} {family}".strip()
    return f"{text}, {suffix[0].capitalize()}" if suffix else text


@functools.cache
def general() -> dict[str, list[Place]]:
    """The polling places of the 11 October 2005 election, by county, with each contest's tally."""
    return {code: first_round(code) for code in COUNTIES}


@functools.cache
def runoffs() -> dict[str, list[Place]]:
    return {code: runoff(code) for code in COUNTIES}


@dataclass
class Declared:
    """Totals the Commission published for one contest."""

    options: dict[str, int] = field(default_factory=dict)
    valid: int | None = None
    invalid: int | None = None
    total: int | None = None


def _page_totals(items: list[str], declared: Declared) -> None:
    for i, item in enumerate(items):
        value = items[i + 1].split()[0] if i + 1 < len(items) and items[i + 1] else ""
        if not re.fullmatch(r"[\d,]+", value):
            continue
        if item == "Total Valid Votes":
            declared.valid = number(value)
        elif item.startswith("Invalid Votes"):
            declared.invalid = number(value)
        elif item == "Total Votes":
            declared.total = number(value)


def result_page(html: str) -> Declared:
    """One contest page of the 2005 portal: a candidate, party, and votes cell per candidate."""
    items = [item for item in cells(html, lines=False) if item]
    declared = Declared()
    start = next(i for i, item in enumerate(items) if item == "Votes") + 1
    stop = next(i for i, item in enumerate(items) if item == "Total Valid Votes")
    row = items[start:stop]
    i = 0
    while i + 2 < len(row):
        name, party, votes = row[i], row[i + 1], row[i + 2]
        count = re.match(r"([\d,]+)(?:\s*\([\d.]+%\))?$", votes)
        if count is None:
            raise ValueError(f"unreadable candidate row {row[i : i + 4]}")
        abbr = re.search(r"\(\s*(\w+)\s*\)\s*$", party)
        declared.options[f"{name} ({abbr.group(1) if abbr else INDEPENDENT})"] = number(count.group(1))
        i += 3
        if i < len(row) and re.fullmatch(r"[\d.]+%", row[i]):
            i += 1
    _page_totals(items, declared)
    return declared


@functools.cache
def senate_pages() -> dict[str, Declared]:
    pages = bundle(SENATE_PAGES)
    return {
        code: result_page(pages[f"{PORTAL}results2005/Senate/Results_{code}00.php"])
        for code in COUNTIES
        if f"{PORTAL}results2005/Senate/Results_{code}00.php" in pages
    }


@functools.cache
def house_pages() -> dict[str, Declared]:
    pages = bundle(HOUSE_PAGES)
    found: dict[str, Declared] = {}
    for source, html in pages.items():
        m = re.fullmatch(re.escape(PORTAL) + r"results2005/House/Results_(\d{4})\.php", source)
        if m:
            found[m.group(1)] = result_page(html)
    return found


def match_declared(declared: dict[str, int], printed: list[str], where: str) -> dict[str, int]:
    """Pair the portal's candidate lines with the polling-place reports' candidate lines."""
    keyed = {fold(ballot_name(label)) + party_of(label).lower(): label for label in printed}
    out: dict[str, int] = {}
    for label, votes in declared.items():
        name = fold(ballot_name(label)) + party_of(label).lower()
        hit = keyed.get(name)
        if hit is None:
            ranked = sorted(keyed, key=lambda other: -SequenceMatcher(None, name, other).ratio())
            if not ranked or SequenceMatcher(None, name, ranked[0]).ratio() < 0.8:
                raise LookupError(f"{where}: {label!r} has no candidate in the polling-place reports")
            hit = keyed[ranked[0]]
        out[hit] = votes
    return out


@dataclass
class Report:
    candidates: list[tuple[str, str, str, int]]
    valid: int
    invalid: int
    total: int


@functools.cache
def president_report() -> Report:
    text = _text(REPORT)
    start = text.index("Presidential and Vice-Presidential Election (Ordered by Votes Obtained)")
    section = text[start : text.index("Ballot Order", start)]
    found = []
    for line in section.split("\n"):
        m = re.match(r"(\S.*?)\s{2,}(\S.*?)\s{2,}(\S.*?)\s{2,}([\d,]+)\s+[\d.]+%\s*$", line)
        if m and not m.group(1).startswith("Presidential"):
            found.append((m.group(1), m.group(2), m.group(3), number(m.group(4))))
    totals = {
        label: number(m.group(1))
        for label in ("Total Valid Votes", "Invalid Votes", "Total Votes")
        if (m := re.search(re.escape(label) + r"\s*\*?\s+([\d,]+)", section))
    }
    return Report(found, totals["Total Valid Votes"], totals["Invalid Votes"], totals["Total Votes"])


@functools.cache
def runoff_report() -> dict[str, int]:
    text = _text(RUNOFF_REPORT)
    start = text.index("Votes Obtained by Candidates")
    figures: dict[str, int] = {}
    for line in text[start : text.index("Percent of Valid Votes", start)].split("\n"):
        m = re.match(r"(JOHNSON-SIRLEAF|WEAH|Total Valid Votes|Invalid Votes|Total Votes)\b[^\d]*([\d,\s]+)$", line)
        if m:
            figures[m.group(1)] = number(m.group(2).split()[-1])
    return figures


@dataclass
class Precinct:
    code: str
    name: str


@dataclass
class SenateCounty:
    code: str
    precincts: dict[str, str]
    declared: Declared
    places: list[Place]
    parties: dict[str, str]
    leader: str
    reported: tuple[int, int]


def _county_figures(items: list[str]) -> tuple[dict[str, int], int]:
    i = next(i for i, item in enumerate(items) if item == "Candidate") + 3
    votes: dict[str, int] = {}
    while items[i] != "Total":
        votes[items[i]] = number(items[i + 1])
        i += 3
    return votes, number(items[i + 1])


@functools.cache
def senate_county(code: str) -> SenateCounty:
    pages = bundle(county_url(code))
    base = f"{PORTAL}senate2014/"
    summary = [item for item in cells(pages[f"{base}county_{int(code)}_2.html"]) if item]
    votes, total = _county_figures(summary)
    declared = Declared(options=votes, valid=total)
    got = re.search(r"Received [\d.]+% \((\d+) of (\d+) Polling Places\)", " ".join(summary))
    if got is None:
        raise LookupError(f"county {code}: no polling-place count")
    precincts = {}
    listing = [item for item in cells(pages[f"{base}county_{int(code)}_vpr.html"]) if item]
    for i, item in enumerate(listing):
        if re.fullmatch(r"\d{4,5}", item) and item.zfill(5).startswith(code) and i + 1 < len(listing):
            precincts[item.zfill(5)] = listing[i + 1]
    places: list[Place] = []
    for source, html in pages.items():
        found = re.fullmatch(re.escape(base) + r"pp_results/(\d+)\.html", source)
        if found is None:
            continue
        heads = [
            re.sub(r"<[^>]+>", "", x).strip()
            for x in re.findall(r"<th[^>]*>(.*?)</th>", html[html.find("Senate Results") :], re.DOTALL)
        ]
        table: list[list[str]] = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html[html.find("Senate Results") :], flags=re.DOTALL):
            row = [
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).replace("&nbsp;", " ").strip()
                for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.DOTALL)
            ]
            if len(row) > 1:
                table.append(row)
            if row and row[0] == "Total":
                break
        if heads[0] != "Position" or any(len(row) != len(heads) for row in table):
            raise ValueError(f"{source}: unexpected table shape")
        for j, label in enumerate(heads[1:], 1):
            if all(row[j] == "" for row in table):
                continue
            if any(row[j] == "" for row in table):
                raise ValueError(f"{source} {label}: partly empty column")
            tally = Tally()
            for row in table:
                count = number(row[j])
                if row[0] == "Total Valid":
                    tally.valid = count
                elif row[0] == "Invalid":
                    tally.invalid = count
                elif row[0] == "Total":
                    tally.total = count
                else:
                    tally.candidates.append((row[0], count))
            place = Place(found.group(1), int(label.removeprefix("PP")), precincts.get(found.group(1), ""), None)
            place.senate = tally
            places.append(place)
    parties = {party_of(label): label for label in votes}
    leader = max(votes, key=lambda label: votes[label])
    return SenateCounty(code, precincts, declared, places, parties, leader, (int(got.group(1)), int(got.group(2))))


def parties() -> dict[str, str]:
    """Every party abbreviation the ballots print, with the full name where the Commission prints one."""
    used: Counter[str] = Counter()
    for places in general().values():
        for place in places:
            for tally in (place.president, place.senate, place.house):
                for label, _ in tally.candidates if tally else []:
                    used[party_of(label)] += 1
    for places in runoffs().values():
        for place in places:
            for label, _ in place.president.candidates if place.president else []:
                used[party_of(label)] += 1
    for code in COUNTIES:
        for place in senate_county(code).places:
            for label, _ in place.senate.candidates if place.senate else []:
                used[party_of(label)] += 1
    for round in president.ROUNDS:
        for label in president.tallies(round.tally)[0].candidates:
            used[party_of(label)] += 1
    return {abbr: PARTIES_2005.get(abbr, abbr) for abbr in sorted(used) if party_id(abbr) is not None}


def party_id(abbr: str) -> str | None:
    return None if abbr == INDEPENDENT or abbr.upper() == "IND" else key("party", abbr)


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        f"{SOURCE}/2005-10-11",
        f"{SOURCE}/2005-11-08",
        *(f"{SOURCE}/{round.day}" for round in president.ROUNDS if round.day < "2014"),
        f"{SOURCE}/2014-12-20",
        *(f"{SOURCE}/{by.day}" for by in BY_ELECTIONS),
        *(f"{SOURCE}/{round.day}" for round in president.ROUNDS if round.day > "2014"),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    general_election().write()
    runoff_election().write()
    senate_election().write()
    for by in BY_ELECTIONS:
        by_election(by).write()
    for round in president.ROUNDS:
        president_election(round).write()


def emit() -> None:
    package.run("load", packages())


def county_name(code: str) -> str:
    return COUNTIES[code][0]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Liberia's 15 counties with their outlines, the electoral districts of the House of Representatives, and the "
        "voting-precinct and polling-place schemes",
    )
    p.external |= {LIBERIA, TERRITORY}
    for code, rank, kind, noun in (
        ("lr.county", 1, "administrative", "county"),
        ("lr.house_district", 2, "electoral_district", "House electoral district"),
        ("lr.precinct", 3, "precinct", "voting precinct"),
        ("lr.polling_place", 4, "precinct", "polling place"),
    ):
        p.add("scheme", code=code, polity_id=LIBERIA, kind=kind, rank=rank, noun=noun, code_system=None)
    for code, (name, _, _) in COUNTIES.items():
        id = area("lr.county", code)
        p.add("area", id=id, scheme_code="lr.county")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, f"{name} County", "en", "official")
    earlier = districts_2005()
    for code, number_ in districts():
        id = area("lr.house_district", f"{code}{number_:02d}")
        # Nine districts first appear in the 2011 results; they hold no results of 2005.
        since = None if (code, number_) in earlier else NEW_DISTRICTS_FROM
        p.add("area", id=id, scheme_code="lr.house_district")
        p.add("area_code", area_id=id, code=f"{code}{number_:02d}", valid_from=since)
        p.add("area_parent", area_id=id, parent_id=area("lr.county", code), valid_from=since)
        p.name(id, f"{county_name(code)} County, Electoral District {number_}", "en", "official")
    outlines(p)
    return p


def outlines(p: Package) -> None:
    """The county outlines: geoBoundaries' release of UNMIL and OCHA's second-level boundaries (the counties)."""
    artifact = raw.cached(SOURCE, BOUNDARIES)
    p.artifact(artifact, BOUNDARY_SOURCE)
    p.add(
        "source",
        id=BOUNDARY_SOURCE,
        kind="academic",
        publisher="geoBoundaries (UNMIL and OCHA ROWCA boundaries)",
        title="geoBoundaries gbOpen Liberia ADM1, 2021",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 IGO (UNMIL, OCHA ROWCA) through geoBoundaries",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="UNMIL and OCHA ROWCA, through geoBoundaries (William & Mary geoLab)",
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", "lr.county", 2021),
        scheme_code="lr.county",
        vintage="2021",
        valid_from=None,
        valid_to=None,
        as_of=date(2021, 1, 1),
    )
    features = json.loads(artifact.path.read_bytes())["features"]
    by_name = {fold(feature["properties"]["shapeName"]): feature for feature in features}
    if len(by_name) != len(COUNTIES):
        raise ValueError(f"{len(by_name)} county outlines for {len(COUNTIES)} counties")
    for code, (name, _, _) in COUNTIES.items():
        geometry = shape(by_name[fold(name)]["geometry"])
        if not geometry.is_valid:
            geometry = shapely.make_valid(geometry)
        polygons = [g for g in shapely.get_parts(geometry) if g.geom_type == "Polygon" and not g.is_empty]
        whole = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
        p.add(
            "area_geometry",
            area_id=area("lr.county", code),
            valid_from=None,
            valid_to=None,
            geom=shapely.to_wkb(whole, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(whole), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=BOUNDARY_SOURCE,
        )


def districts_2005() -> set[tuple[str, int]]:
    return {
        (place.county, place.district)
        for places in general().values()
        for place in places
        if place.district is not None
    }


def districts() -> list[tuple[str, int]]:
    return sorted(districts_2005() | later_districts())


@functools.cache
def later_districts() -> set[tuple[str, int]]:
    """The electoral districts the presidential results of 2011 to 2023 name."""
    return {
        (precinct.county, precinct.district)
        for round in president.ROUNDS
        for precinct in president.precincts(round.day, round.number)
    }


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Liberian parties, the Senate, the House of Representatives, the presidency, and their electoral systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {LIBERIA, TERRITORY}
    p.add(
        "source",
        id=NEC_SOURCE,
        kind="official_body",
        publisher="National Elections Commission of Liberia",
        title="Election results",
        url="https://necliberia.org/results/",
    )
    for url in urls():
        p.artifact(raw.cached(SOURCE, url), NEC_SOURCE)
    p.add("body", id=SENATE_BODY, polity_id=LIBERIA, kind="upper_house")
    p.name(SENATE_BODY, "Senate", "en", "official")
    p.add("body", id=HOUSE_BODY, polity_id=LIBERIA, kind="lower_house")
    p.name(HOUSE_BODY, "House of Representatives", "en", "official")
    p.add("office", id=PRESIDENCY, polity_id=LIBERIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Liberia", "en", "official")
    for id, since, until, rules, text in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=LIBERIA, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for abbr, name in parties().items():
        id = party_id(abbr)
        if id is None:
            continue
        p.add("party", id=id, kind="party", polity_id=LIBERIA)
        p.add("external_id", entity_id=id, scheme="lr.nec.party", value=abbr)
        p.name(id, name, "en", "official")
        if name != abbr:
            p.name(id, abbr, "en", "abbreviation", False)
    return p


SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        PRESIDENT_SYSTEM,
        FIRST_DAY,
        None,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A candidate who wins an absolute majority of valid votes is elected President; otherwise the two leading "
            "candidates go to a runoff. The dates bound the elections this archive holds, not the period the law applied."
        ),
    ),
    (
        BLOCK_SYSTEM,
        FIRST_DAY,
        SENATE_DAY,
        {"formula": "block"},
        (
            "Two senators are elected for each county, and a voter marks up to two candidates, so a county's valid "
            "votes can exceed its ballots. The two candidates with the most votes win. The dates bound the elections "
            "this archive holds, not the period the law applied."
        ),
    ),
    (
        SENATE_SYSTEM,
        SENATE_DAY,
        None,
        {"formula": "fptp"},
        (
            "One senator is elected for each county at the half-term election, and the candidate with the most votes "
            "wins. The dates bound the elections this archive holds, not the period the law applied."
        ),
    ),
    (
        HOUSE_SYSTEM,
        FIRST_DAY,
        None,
        {"formula": "fptp"},
        (
            "One representative is elected in each electoral district, and the candidate with the most votes wins. The "
            "date bounds the elections this archive holds, not the period the law applied."
        ),
    ),
]
METHOD = (
    "Counted by the National Elections Commission at each polling place and published as the Commission's "
    "polling-place tally. Registered voters are the figure printed for the polling place; valid and invalid "
    "are the Commission's own counts."
)
SENATE_METHOD_2005 = (
    "Counted by the National Elections Commission at each polling place. Each voter could mark up to two candidates, "
    "so the valid votes are votes, not ballots, and the Commission's invalid figure counts ballots with no valid "
    "vote. Ballots cast are therefore not stored for this contest."
)
OUTCOME_NOTE = (
    "The elected candidates are those the Commission's tally reports list as leading in each contest, which "
    "agrees with the Commission's seat counts by party."
)


class Builder:
    """Shared steps for the packages of one election day."""

    def __init__(self, p: Package) -> None:
        self.p = p
        self.units: dict[tuple[str, str, int], str] = {}
        self.over: list[tuple[str, int, int]] = []
        self.people: dict[str, str] = {}

    def unit(self, day: date, place: Place) -> str:
        code = f"{day}:{place.code}"
        id = key("unit", code)
        if (day.isoformat(), place.precinct, place.number) in self.units:
            return id
        self.units[(day.isoformat(), place.precinct, place.number)] = id
        district = (
            area("lr.house_district", f"{place.county}{place.district:02d}")
            if place.district is not None
            else area("lr.county", place.county)
        )
        self.p.add(
            "reporting_unit",
            id=id,
            polity_id=LIBERIA,
            scheme_code="lr.polling_place",
            code=code,
            area_relation="within",
            area_id=district,
            kind="standard",
        )
        if place.name:
            self.p.name(id, f"{place.name}, polling place {place.number}", "en", "official")
        return id

    def result_set(self, election: str, contest: str, tag: str, methodology: str, notes: str = "") -> str:
        id = key("result_set", tag)
        self.p.add(
            "result_set",
            id=id,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=NEC_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        self.p.add(
            "note",
            id=key("note", id),
            entity_id=id,
            kind="methodology",
            body_md=(methodology + " " + notes).strip(),
            lang="en",
        )
        return id

    def option(self, contest: str, tag: str, position: int | None, label: str, vice: str | None = None) -> str:
        abbr = party_of(label)
        id = key("option", tag, label)
        party = by_party_id(abbr)
        p = self.p
        p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_position=position,
            ballot_label=ballot_name(label),
        )
        person = key("person", tag, label)
        p.add("person", id=person)
        p.name(person, person_name(ballot_name(label)), "en", "official")
        p.add(
            "candidacy",
            id=key("candidacy", tag, label),
            person_id=person,
            option_id=id,
            role="head",
            ballot_name=ballot_name(label),
            member_of_party_id=party,
            is_independent=party is None,
        )
        if vice:
            mate = key("person", tag, label, "running mate")
            p.add("person", id=mate)
            p.name(mate, person_name(vice), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", tag, label, "running mate"),
                person_id=mate,
                option_id=id,
                role="running_mate",
                ballot_name=vice,
                member_of_party_id=party,
                is_independent=party is None,
            )
        return id

    def values(
        self,
        result_set: str,
        unit: str,
        tally: Tally,
        options: dict[str, str],
        registered: int | None,
        ballots: bool = True,
    ) -> None:
        p = self.p

        def add(option: str | None, measure: int, value: int | None) -> None:
            if value is None:
                return
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )

        add(None, 2, registered)
        if ballots and registered is not None and tally.total is not None and tally.total > registered:
            self.over.append((unit, registered, tally.total))
        if ballots:
            add(None, 3, tally.total)
        add(None, 4, tally.valid)
        add(None, 5, tally.invalid)
        for label, count in tally.candidates:
            add(options[label], 1, count)

    def overcount(self) -> None:
        if self.over:
            self.p.accept(
                "accounting.turnout",
                f"The Commission's tally prints more ballots cast than registered voters in {len(self.over):,} "
                f"polling-place results (the largest excess is {max(t - r for _, r, t in self.over):,} ballots); "
                "both figures are the Commission's own and are stored as printed.",
            )

    def declare(self, result_set: str, options: dict[str, str], declared: Declared, ballots: bool = True) -> None:
        p = self.p
        for label, count in declared.options.items():
            p.add("declared", result_set_id=result_set, option_id=options[label], measure=1, value=count)
        for measure, value in ((4, declared.valid), (5, declared.invalid), (3, declared.total if ballots else None)):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)


def labels(places: list[Place], pick: str) -> list[str]:
    return sorted({label for place in places for label, _ in getattr(place, pick).candidates})


def leaders(tally: dict[str, int], seats: int) -> list[str]:
    ranked = sorted(tally, key=lambda label: -tally[label])
    if len(ranked) > seats and tally[ranked[seats - 1]] == tally[ranked[seats]]:
        raise ValueError(f"tie at the last seat: {ranked[: seats + 1]}")
    return ranked[:seats]


def general_election() -> Package:
    p = Package(
        f"{SOURCE}/2005-10-11",
        SOURCE,
        "The 11 October 2005 Liberian general election (President, Senate, House of Representatives) by polling place",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {LIBERIA, TERRITORY}
    for url in (REPORT, HOUSE_PAGES, SENATE_PAGES, *(pdf_url(stem, False) for _, stem, _ in COUNTIES.values())):
        p.uses(raw.cached(SOURCE, url))
    b = Builder(p)
    election = key("election", "2005-10-11")
    path = "lr/2005-10-11-general"
    p.add(
        "election",
        id=election,
        polity_id=LIBERIA,
        kind="general",
        date_from=FIRST_DAY,
        date_to=FIRST_DAY,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, "2005 Liberian general election", "en", "official")
    report = president_report()
    places = [place for county in general().values() for place in county]
    # President
    contest = key("contest", "2005-10-11", "president")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=TERRITORY,
        magnitude=1,
        round_number=1,
        electoral_system_id=PRESIDENT_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, "2005 Liberian presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = b.result_set(election, contest, "2005-10-11:president", METHOD)
    printed = labels([x for x in places if x.president], "president")
    options: dict[str, str] = {}
    by_name = {fold(name): (vp, count) for name, vp, _, count in report.candidates}
    for position, label in enumerate(printed, 1):
        vp, _ = by_name[fold(ballot_name(label))]
        options[label] = b.option(contest, "2005-10-11:president", position, label, vp)
    national: dict[str, int] = {}
    for name, _, _, count in report.candidates:
        label = next(label for label in printed if fold(ballot_name(label)) == fold(name))
        national[label] = count
    for place in places:
        if place.president:
            b.values(result_set, b.unit(FIRST_DAY, place), place.president, options, place.registered)
    b.declare(result_set, options, Declared(national, report.valid, report.invalid, report.total))
    standing = sorted(national, key=lambda label: -national[label])
    for label in printed:
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=options[label],
            kind="advanced" if label in standing[:2] else "eliminated",
        )
    # Senate, by county
    senate_declared = senate_pages()
    house_declared = house_pages()
    for code in COUNTIES:
        county = [x for x in general()[code] if x.senate]
        contest = key("contest", "2005-10-11", "senate", code)
        slug = changeset.slug(county_name(code))
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=SENATE_BODY,
            district_area_id=area("lr.county", code),
            magnitude=2,
            round_number=1,
            electoral_system_id=BLOCK_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{county_name(code)} County, Senate", "en", "official")
        p.add("slug", path=f"{path}/senate-{slug}", entity_id=contest, is_current=True)
        result_set = b.result_set(election, contest, f"2005-10-11:senate:{code}", SENATE_METHOD_2005, OUTCOME_NOTE)
        printed = labels(county, "senate")
        options = {
            label: b.option(contest, f"2005-10-11:senate:{code}", position, label)
            for position, label in enumerate(printed, 1)
        }
        for place in county:
            assert place.senate is not None
            b.values(result_set, b.unit(FIRST_DAY, place), place.senate, options, place.registered, ballots=False)
        sums: Counter[str] = Counter()
        for place in county:
            assert place.senate is not None
            sums.update(dict(place.senate.candidates))
        if code in senate_declared:
            page = senate_declared[code]
            matched = match_declared(page.options, printed, f"senate {code}")
            b.declare(result_set, options, Declared(matched, page.valid, page.invalid), ballots=False)
        won = leaders(dict(sums), 2)
        for label in printed:
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[label],
                kind="elected" if label in won else "lost",
            )
    # House, by district
    for code, number_ in sorted(districts_2005()):
        county = [x for x in general()[code] if x.house and x.district == number_]
        contest = key("contest", "2005-10-11", "house", code, number_)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=HOUSE_BODY,
            district_area_id=area("lr.house_district", f"{code}{number_:02d}"),
            magnitude=1,
            round_number=1,
            electoral_system_id=HOUSE_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{county_name(code)} County, Electoral District {number_}", "en", "official")
        p.add(
            "slug",
            path=f"{path}/house-{changeset.slug(county_name(code))}-{number_}",
            entity_id=contest,
            is_current=True,
        )
        tag = f"2005-10-11:house:{code}:{number_}"
        result_set = b.result_set(election, contest, tag, METHOD, OUTCOME_NOTE)
        printed = labels(county, "house")
        options = {label: b.option(contest, tag, position, label) for position, label in enumerate(printed, 1)}
        sums = Counter()
        for place in county:
            assert place.house is not None
            b.values(result_set, b.unit(FIRST_DAY, place), place.house, options, place.registered)
            sums.update(dict(place.house.candidates))
        page = house_declared.get(f"{code}{number_:02d}")
        if page:
            matched = match_declared(page.options, printed, f"house {code}{number_:02d}")
            b.declare(result_set, options, Declared(matched, page.valid, page.invalid, page.total))
        won = leaders(dict(sums), 1)
        for label in printed:
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[label],
                kind="elected" if label in won else "lost",
            )
    b.overcount()
    return p


def runoff_election() -> Package:
    p = Package(
        f"{SOURCE}/2005-11-08",
        SOURCE,
        "The 8 November 2005 Liberian presidential runoff by polling place",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/2005-10-11"],
    )
    p.external |= {LIBERIA, TERRITORY}
    for url in (RUNOFF_REPORT, *(pdf_url(stem, True) for _, stem, _ in COUNTIES.values())):
        p.uses(raw.cached(SOURCE, url))
    b = Builder(p)
    election = key("election", "2005-11-08")
    path = "lr/2005-11-08-runoff"
    p.add(
        "election",
        id=election,
        polity_id=LIBERIA,
        kind="runoff",
        date_from=RUNOFF_DAY,
        date_to=RUNOFF_DAY,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, "2005 Liberian presidential runoff", "en", "official")
    contest = key("contest", "2005-11-08", "president")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=TERRITORY,
        magnitude=1,
        round_number=2,
        previous_round_id=key("contest", "2005-10-11", "president"),
        electoral_system_id=PRESIDENT_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, "2005 Liberian presidential election, runoff", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = b.result_set(election, contest, "2005-11-08:president", METHOD)
    places = [place for county in runoffs().values() for place in county]
    printed = labels(places, "president")
    report = president_report()
    vice = {fold(name): vp for name, vp, _, _ in report.candidates}
    options = {
        label: b.option(contest, "2005-11-08:president", position, label, vice.get(fold(ballot_name(label))))
        for position, label in enumerate(printed, 1)
    }
    for place in places:
        assert place.president is not None
        b.values(result_set, b.unit(RUNOFF_DAY, place), place.president, options, place.registered)
    figures = runoff_report()
    declared = Declared(
        {label: figures["JOHNSON-SIRLEAF" if "SIRLEAF" in label else "WEAH"] for label in printed},
        figures["Total Valid Votes"],
        figures["Invalid Votes"],
        figures["Total Votes"],
    )
    b.declare(result_set, options, declared)
    winner = max(declared.options, key=lambda label: declared.options[label])
    for label in printed:
        p.add(
            "outcome", result_set_id=result_set, option_id=options[label], kind="elected" if label == winner else "lost"
        )
    b.overcount()
    return p


def senate_election() -> Package:
    p = Package(
        f"{SOURCE}/2014-12-20",
        SOURCE,
        "The 20 December 2014 Liberian Senate election, one seat in each county, by polling place",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {LIBERIA, TERRITORY}
    for code in COUNTIES:
        p.uses(raw.cached(SOURCE, county_url(code)))
    b = Builder(p)
    election = key("election", "2014-12-20")
    path = "lr/2014-12-20-senate"
    p.add(
        "election",
        id=election,
        polity_id=LIBERIA,
        kind="general",
        date_from=SENATE_DAY,
        date_to=SENATE_DAY,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, "2014 Liberian Senate election", "en", "official")
    gaps: list[str] = []
    for code in COUNTIES:
        county = senate_county(code)
        contest = key("contest", "2014-12-20", "senate", code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=SENATE_BODY,
            district_area_id=area("lr.county", code),
            magnitude=1,
            round_number=1,
            electoral_system_id=SENATE_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{county_name(code)} County, Senate", "en", "official")
        p.add("slug", path=f"{path}/{changeset.slug(county_name(code))}", entity_id=contest, is_current=True)
        tag = f"2014-12-20:senate:{code}"
        printed = sorted({label for place in county.places if place.senate for label, _ in place.senate.candidates})
        summed: Counter[str] = Counter()
        for place in county.places:
            assert place.senate is not None
            summed.update(dict(place.senate.candidates))
        notes = "The Commission's final results page names the leading candidate in each county, who is recorded as elected."
        if any(summed[label] != votes for label, votes in county.declared.options.items()):
            absent = county.reported[0] - len(county.places)
            total = sum(summed.values())
            gaps.append(
                f"{county_name(code)} ({total:,} votes in the polling-place pages, {county.declared.valid:,} declared)"
            )
            notes += (
                f" The county total the Commission declared differs from the sum of its polling-place pages: "
                f"{len(county.places)} of {county.reported[0]} polling places appear in those pages"
                f"{' and the rest are blank' if absent else ''}, and both sets of figures are stored as published."
            )
        result_set = b.result_set(
            election,
            contest,
            tag,
            "Counted by the National Elections Commission at each polling place and published for each voting "
            "precinct as a column per polling place. The Commission published no registration figure for a polling "
            "place.",
            notes,
        )
        options = {label: b.option(contest, tag, position, label) for position, label in enumerate(printed, 1)}
        for place in county.places:
            assert place.senate is not None
            b.values(result_set, b.unit(SENATE_DAY, place), place.senate, options, None)
        declared = Declared(dict(county.declared.options), county.declared.valid)
        b.declare(result_set, options, declared, ballots=False)
        for label in printed:
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[label],
                kind="elected" if label == county.leader else "lost",
            )
    if gaps:
        p.accept(
            "accounting.declared",
            "The Commission's declared county totals differ from the sum of its polling-place pages in "
            + "; ".join(gaps)
            + ". The pages leave some polling-place columns blank, and both sets of figures are stored as published.",
        )
    return p


@dataclass(frozen=True)
class ByElection:
    """A House by-election the portal reports at district total only: its polling day, the portal page, and the
    electoral district."""

    day: date
    county: str
    district: int
    page: str  # file name of the portal's first-round page
    name: str


# The polling days are those the Commission's declaration index prints for each by-election.
BY_ELECTIONS = [
    ByElection(date(2006, 5, 13), "36", 1, "Results_36012006.php", "River Cess County, Electoral District 1"),
    ByElection(date(2007, 2, 6), "33", 6, "Results_3306200706.php", "Nimba County, Electoral District 6"),
]
# Parties that stood in a by-election but not in the 2005 general election, so the registry does not hold them.
BY_PARTIES = {"LAP": "Liberia Action Party"}
BY_SYSTEM = key("electoral_system", "house", "by-election")


def by_party_id(abbr: str) -> str | None:
    return key("party", abbr, "by-election") if abbr in BY_PARTIES else party_id(abbr)


def _ballot_name(cells_: list[str]) -> str:
    if len(cells_) >= 2:
        name = f"{cells_[0].rstrip(',')}, {cells_[1]}"
    else:
        name = cells_[0]
        if "," not in name:
            words = name.split()
            at = next((i for i, word in enumerate(words) if not re.fullmatch(r"[A-Z'\-.]+", word)), len(words))
            if 0 < at < len(words):
                name = " ".join(words[:at]) + ", " + " ".join(words[at:])
    return re.sub(r"^([^,]+), (SR|JR|II|III)\.? (?=\S)", r"\1, \2., ", name)


def by_election_page(html: str) -> Declared:
    """A by-election page of the 2005 portal. The cells of a candidate vary from page to page (surname and given
    name in one cell or two, the party's abbreviation alone or after its name), so each candidate is every cell up
    to its vote count."""
    items = [item for item in cells(html, lines=False) if item]
    start = next(i for i, item in enumerate(items) if item == "Votes") + 1
    stop = next(i for i, item in enumerate(items) if item == "Total Valid Votes")
    declared = Declared()
    buffer: list[str] = []
    for item in items[start:stop]:
        if re.fullmatch(r"\(?[\d.]+%\)?", item):
            continue
        if not re.fullmatch(r"[\d,]+", item) or not buffer:
            buffer.append(item)
            continue
        text = " ".join(buffer)
        found = re.search(r"\(\s*(\w+)\s*\)?\s*\)?\s*$", text)
        abbr = found.group(1).upper() if found else INDEPENDENT
        abbr = INDEPENDENT if abbr == "IND" else abbr
        opened = next((i for i, cell in enumerate(buffer) if "(" in cell), len(buffer))
        party_at = opened if opened < len(buffer) and buffer[opened].split("(")[0].strip() else opened - 1
        if found is None:
            party_at = len(buffer) - 1
        declared.options[f"{_ballot_name(buffer[:party_at])} ({abbr})"] = number(item)
        buffer = []
    if buffer:
        raise ValueError(f"unread cells {buffer}")
    _page_totals(items, declared)
    return declared


def by_election(by: ByElection) -> Package:
    p = Package(
        f"{SOURCE}/{by.day}",
        SOURCE,
        f"The {by.day:%-d %B %Y} Liberian House of Representatives by-election, {by.name}, at district total",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {LIBERIA, TERRITORY}
    for url in (DECLARATION_INDEX, BY_HOUSE_PAGES):
        p.artifact(raw.cached(SOURCE, url), NEC_SOURCE)
    page = by_election_page(bundle(BY_HOUSE_PAGES)[f"{PORTAL}results2005/House/{by.page}"])
    if page.total is None or page.valid is None:
        raise ValueError(f"{by.page}: no totals")
    b = Builder(p)
    election = key("election", str(by.day))
    path = f"lr/{by.day}-house-by-election"
    district = f"{by.county}{by.district:02d}"
    p.add(
        "election",
        id=election,
        polity_id=LIBERIA,
        kind="by_election",
        date_from=by.day,
        date_to=by.day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{by.day.year} Liberian House of Representatives by-election, {by.name}", "en", "official")
    p.add(
        "electoral_system",
        id=BY_SYSTEM,
        polity_id=LIBERIA,
        valid_from=by.day,
        valid_to=None,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", BY_SYSTEM),
        entity_id=BY_SYSTEM,
        kind="methodology",
        body_md=(
            "A House seat vacated between general elections is filled at a by-election. A candidate who wins an "
            "absolute majority of valid votes is elected; otherwise the two leading candidates go to a runoff. The "
            "date bounds the by-elections this archive holds, not the period the law applied."
        ),
        lang="en",
    )
    contest = key("contest", str(by.day), "house", district)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=HOUSE_BODY,
        district_area_id=area("lr.house_district", district),
        magnitude=1,
        round_number=1,
        electoral_system_id=BY_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, by.name, "en", "official")
    p.add("slug", path=f"{path}/house", entity_id=contest, is_current=True)
    tag = f"{by.day}:house:{district}"
    result_set = b.result_set(
        election,
        contest,
        tag,
        "Counted by the National Elections Commission and published by the Commission as one total for the "
        "district; the portal's page names no polling-place results.",
        "The Commission's page does not say which candidates advance; the two with the most votes are recorded as "
        "advancing because no candidate won a majority.",
    )
    unit = key("unit", f"{by.day}:{district}")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=LIBERIA,
        scheme_code="lr.house_district",
        code=f"{by.day}:{district}",
        area_relation="equals",
        area_id=area("lr.house_district", district),
        kind="aggregate",
    )
    p.name(unit, by.name, "en", "official")
    printed = list(page.options)
    options = {}
    for position, label in enumerate(printed, 1):
        id = b.option(contest, tag, position, label)
        options[label] = id
    # parties the registry lacks are minted here, under a key of their own
    for abbr, name in BY_PARTIES.items():
        if any(party_of(label) == abbr for label in printed):
            id = by_party_id(abbr)
            assert id is not None
            p.add("party", id=id, kind="party", polity_id=LIBERIA)
            p.add("external_id", entity_id=id, scheme="lr.nec.party", value=abbr)
            p.name(id, name, "en", "official")
            p.name(id, abbr, "en", "abbreviation", False)
    b.values(result_set, unit, Tally(list(page.options.items()), page.valid, page.invalid, page.total), options, None)
    b.declare(result_set, options, page)
    if sum(page.options.values()) != page.valid:
        p.accept(
            "accounting.votes_valid",
            f"The Commission's page prints {page.valid:,} valid votes, but its candidates' votes sum to "
            f"{sum(page.options.values()):,}; both are stored as printed.",
        )
    standing = sorted(printed, key=lambda label: -page.options[label])
    for label in printed:
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=options[label],
            kind="advanced" if label in standing[:2] else "eliminated",
        )
    return p


PRESIDENT_METHOD = (
    "Counted by the National Elections Commission at each polling place and published on its results portal as one "
    "page for each voting precinct, with a column for each polling place. The portal prints the registered voters of "
    "a precinct, not of a polling place, so they are stored on the precinct's own unit. Ballots cast are the "
    "portal's total of valid and invalid votes, and the candidates' votes are the valid votes."
)


def county_code(name: str) -> str:
    wanted = fold(name.removesuffix(" County"))
    return next(code for code, (county, _, _) in COUNTIES.items() if fold(county) == wanted)


def president_election(round: president.Round) -> Package:
    day = date.fromisoformat(round.day)
    first = round.number == 1
    year = day.year
    earlier = next(r for r in president.ROUNDS if r.number == 1 and r.day[:4] == round.day[:4])
    p = Package(
        f"{SOURCE}/{round.day}",
        SOURCE,
        f"The {day.day} {day:%B} {year} Liberian presidential "
        + ("election" if first else "runoff")
        + " by polling place",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", *([] if first else [f"{SOURCE}/{earlier.day}"])],
    )
    p.external |= {LIBERIA, TERRITORY}
    for url in (*round.files, round.tally):
        p.uses(raw.cached(SOURCE, url))
    b = Builder(p)
    election = key("election", round.day)
    path = f"lr/{round.day}-{'general' if first else 'runoff'}"
    p.add(
        "election",
        id=election,
        polity_id=LIBERIA,
        kind="general" if first else "runoff",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(
        election,
        f"{year} Liberian general election" if first else f"{year} Liberian presidential runoff",
        "en",
        "official",
    )
    contest = key("contest", round.day, "president")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=TERRITORY,
        magnitude=1,
        round_number=round.number,
        previous_round_id=None if first else key("contest", earlier.day, "president"),
        electoral_system_id=PRESIDENT_SYSTEM,
        default_result_kind="counted",
    )
    p.name(
        contest,
        f"{year} Liberian presidential election" + ("" if first else ", runoff"),
        "en",
        "official",
    )
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    precincts = president.precincts(round.day, round.number)
    national, counties = president.tallies(round.tally)
    places = sum(len(precinct.places) for precinct in precincts)
    if national.places != places or national.total is None:
        raise ValueError(f"{round.day}: {places} polling places, the portal counts {national.places}")
    notes = ""
    if len(precincts) < 2080:
        notes = (
            f"The portal lists 2,080 voting precincts. {len(precincts):,} of them have polling places in this election, "
            f"and together they hold the {places:,} polling places the portal's national tally counts; the portal "
            "answers with an error for the other precincts."
        )
    tag = f"{round.day}:president"
    result_set = b.result_set(election, contest, tag, PRESIDENT_METHOD, notes)
    options = {label: b.option(contest, tag, None, label) for label in national.candidates}
    sums: dict[str, Counter[str]] = {}
    totals: Counter[str] = Counter()
    for precinct in precincts:
        district = area("lr.house_district", f"{precinct.county}{precinct.district:02d}")
        code = f"{round.day}:{precinct.code}"
        parent = key("unit", code)
        p.add(
            "reporting_unit",
            id=parent,
            polity_id=LIBERIA,
            scheme_code="lr.precinct",
            code=code,
            area_relation="within",
            area_id=district,
            kind="standard",
        )
        p.name(parent, precinct.name, "en", "official")
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=parent,
            option_id=None,
            channel=0,
            measure=2,
            count_round=0,
            flags=0,
            value=precinct.registered,
        )
        county = sums.setdefault(precinct.county, Counter())
        for i, column in enumerate(precinct.places):
            found = re.fullmatch(r"PP(\d+)", column)
            if found is None:
                raise ValueError(f"precinct {precinct.code}: polling place {column!r}")
            number_ = int(found.group(1))
            unit = key("unit", f"{code}-{number_}")
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=LIBERIA,
                scheme_code="lr.polling_place",
                code=f"{code}-{number_}",
                parent_id=parent,
                area_relation="within",
                area_id=district,
                kind="standard",
            )
            p.name(unit, f"{precinct.name}, polling place {number_}", "en", "official")
            tally = Tally(
                [(label, votes[i]) for label, votes in precinct.candidates.items()],
                precinct.valid[i],
                precinct.invalid[i],
                precinct.total[i],
            )
            b.values(result_set, unit, tally, options, None)
            for label, votes in tally.candidates:
                county[label] += votes
                totals[label] += votes
            county["total"] += precinct.total[i]
    for name, page in counties.items():
        mine = sums[county_code(name)]
        if mine["total"] != page.total or any(mine[label] != votes for label, votes in page.candidates.items()):
            raise ValueError(f"{round.day}: {name} differs from the polling-place sums")
    if dict(totals) != national.candidates:
        raise ValueError(f"{round.day}: the polling-place sums differ from the portal's national tally")
    b.declare(result_set, options, Declared(dict(national.candidates), None, None, national.total))
    if first and national.registered is not None:
        p.add("declared", result_set_id=result_set, option_id=None, measure=2, value=national.registered)
    ranked = sorted(national.candidates, key=lambda label: -national.candidates[label])
    for label in national.candidates:
        if first:
            kind = "advanced" if label in ranked[:2] else "eliminated"
        else:
            kind = "elected" if label == ranked[0] else "lost"
        p.add("outcome", result_set_id=result_set, option_id=options[label], kind=kind)
    return p
