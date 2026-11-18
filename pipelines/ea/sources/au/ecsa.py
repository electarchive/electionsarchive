"""South Australian elections from the Electoral Commission SA (ECSA) and its published history of elections."""

import csv
import difflib
import functools
import html
import io
import json
import re
import subprocess
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid5

import pymupdf

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "au.ecsa"
HOST = "https://www.ecsa.sa.gov.au"
AUSTRALIA = str(uuid5(package.NAMESPACE, "polity\x1fAU"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
LICENSE = "Creative Commons Attribution 3.0 Australia"
LICENSE_URL = "https://creativecommons.org/licenses/by/3.0/au/"
PAGE = f"{HOST}/elections/past-state-election-results"

PARTIES = {
    "ALP": "Australian Labor Party",
    "LIB": "Liberal Party of Australia",
    "GRN": "The Greens",
    "NAT": "National Party of Australia",
    "ONP": "Pauline Hanson's One Nation",
    "FFP": "Family First Party",
    "AFP": "Australian Family Party",
    "AJP": "Animal Justice Party",
    "RCH": "Responsible Capitalist Party",
    "SAB": "SA Best",
    "FGA": "Sarah Game Fair Go for Australians",
    "LCP": "Legalise Cannabis South Australia Party",
    "UVA": "United Voice Australia",
    "JLP": "Jing Lee - Better Community",
    "LDP": "Liberal Democratic Party",
    "DEM": "Australian Democrats",
    "DIG": "Danig",
    "ANL": "Australian National League",
    "COMM": "Communist Party",
    "CP": "Country Party",
    "F&PCP": "Farmers and Producers Country Party",
    "F&S": "Farmers and Settlers Party",
    "FPPU": "Farmers and Producers Political Union",
    "FTA": "Free Trade Association",
    "LANG": "Lang Labor Party",
    "LCL": "Liberal and Country League",
    "LDU": "Liberal and Democratic Union",
    "LU": "Liberal Union",
    "NDL": "National Defence League",
    "NLP": "National Labor Party",
    "PCP": "Progressive Country Party",
    "PEOP": "Peoples Party",
    "PPEOP": "Peninsula Peoples Party",
    "PPLAB": "Premiers Plan Labor Party",
    "PROT": "Protestant Labor Party",
    "SOC": "Socialist Party",
    "SOCLAB": "Socialist Labor Party",
    "ST": "Single Tax",
    "ULP": "United Labor Party",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECSA = key("source", "ecsa")
HISTORY = key("source", "history")
ASSEMBLY = key("body", "house-of-assembly")
SYSTEM = key("electoral_system", "house-of-assembly", "irv")
FPTP = key("electoral_system", "house-of-assembly", "fptp")
BLOCK = key("electoral_system", "house-of-assembly", "block")
CONTINGENT = key("electoral_system", "house-of-assembly", "contingent")


def party_id(tag: str) -> str:
    return key("party", tag)


def independent(tag: str) -> bool:
    return tag in ("", "IND")


def known_party(tag: str) -> bool:
    return tag not in ("", "IND", "?")


@dataclass
class Candidate:
    name: str
    party: str
    incumbent: bool = False
    display: str | None = None


@dataclass
class Place:
    """One leaf of a district's count: a polling place, a declaration-vote aggregate, or the district itself."""

    name: str
    votes: list[int | None]
    formal: int | None = None
    informal: int | None = None
    total: int | None = None
    kind: str = "standard"
    runoff: list[int | None] | None = None  # votes for the two final candidates, in `District.finalists` order


@dataclass
class District:
    name: str
    candidates: list[Candidate]
    enrolled: int | None
    places: list[Place]
    declared: Place
    finalists: list[int] | None = None
    note: str | None = None
    seats: int = 1
    winners: list[int] | None = None  # the candidates the history prints in capitals, where it does
    unopposed: bool = False
    system: str | None = None  # the key of the electoral system, where it is not the default
    after: list[tuple[str, int]] | None = None  # votes after preferences in a district that elects several members
    misprinted: int = 0  # polling places whose formal votes the source misprints and the archive leaves out


@dataclass
class Event:
    day: str  # election day, yyyy-mm-dd
    kind: str  # general or by_election
    files: list[str]  # raw urls this election cites
    districts: list[District] = field(default_factory=list)
    last: date | None = None  # the last polling day of an election whose districts did not all vote on one day
    dated: list[str] = field(default_factory=list)  # raw urls of the pages that give the polling day


def title(name: str) -> str:
    return " ".join(w if w.isupper() and len(w) <= 2 else w.capitalize() for w in name.split())


def person_name(raw_name: str) -> str:
    """'SANDERSON, Rachel' or 'SANDERSON Rachel' to 'Rachel Sanderson'."""
    if "," in raw_name:
        surname, _, given = raw_name.partition(",")
        return f"{given.strip()} {surname.strip().title()}".strip()
    words = raw_name.split()
    split = next((i for i, w in enumerate(words) if not w.replace("'", "").replace("-", "").isupper()), len(words))
    split = max(split, 1)
    surname = " ".join(w.title() if w.isupper() else w for w in words[:split])
    return f"{' '.join(words[split:])} {surname}".strip()


def number(text: str) -> int | None:
    text = text.replace(",", "").replace("\xa0", "").replace(" ", "").strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


# 2022 state election and 2022 by-election: comma-separated files by district and polling place.

ELECTION_2022 = (
    f"{HOST}/component/edocman/244-2022se-house-of-assembly-first-preference-results-by-district-and-polling-place"
    "/download?Itemid=0"
)
TCP_2022 = (
    f"{HOST}/component/edocman/244-2022se-house-of-assembly-2cp-results-by-district-and-polling-place/download?Itemid=0"
)


def zip_files(url: str) -> dict[str, str]:
    """District name to the text of its file, for a zip of one comma-separated file per district."""
    out: dict[str, str] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as z:
        for info in z.infolist():
            if info.filename.endswith(".csv"):
                out[info.filename.rsplit("/", 1)[1].removesuffix(".csv")] = z.read(info).decode("utf-8-sig")
    return dict(sorted(out.items()))


CANDIDATE = re.compile(r"^(?P<name>.*?)(?: \((?P<party>[^()]*)\))?$")


def candidate(text: str) -> Candidate:
    found = CANDIDATE.match(text.strip())
    assert found
    return Candidate(found["name"].strip(), (found["party"] or "").strip())


def csv_number(cell: str) -> int | None:
    cell = cell.strip().replace(",", "")
    return int(cell) if re.fullmatch(r"\d+", cell) else None


def parse_2022(name: str, first: str, runoff: str | None) -> District:
    rows = list(csv.reader(io.StringIO(first)))
    enrolled = next(csv_number(r[3]) for r in rows if len(r) > 3 and r[1].strip() == "No. enrolled")
    head = next(i for i, r in enumerate(rows) if r and r[0].strip() in ("Polling Place", "Polling Location"))
    header = rows[head - 1]
    stop = next(i for i, c in enumerate(header) if c.strip() == "Formal Votes")
    candidates = [candidate(c) for c in header[1:stop] if c.strip()]
    width = len(candidates)
    places: list[Place] = []
    declared: Place | None = None
    for r in rows[head + 1 :]:
        if not r or not r[0].strip():
            continue
        cells = r[1:]
        votes = [csv_number(cells[2 * i]) for i in range(width)]
        base = 2 * width
        place = Place(
            r[0].strip(), votes, csv_number(cells[base]), csv_number(cells[base + 2]), csv_number(cells[base + 4])
        )
        label = place.name
        if label == "District Total":
            declared = place
        elif label in ("Ordinary Votes", "Polling Place Totals"):
            continue
        elif label in ("Declaration Votes", "Declaration Ballot Papers"):
            place.name = "Declaration Votes"
            place.kind = "aggregate"
            places.append(place)
        else:
            places.append(place)
    assert declared is not None
    district = District(name, candidates, enrolled, places, declared)
    if runoff is not None:
        _runoff(district, runoff)
    return district


def _runoff(district: District, text: str) -> None:
    rows = list(csv.reader(io.StringIO(text)))
    head = next(i for i, r in enumerate(rows) if r and r[0].strip() == "Polling Location")
    first, second = candidate(rows[head][1]), candidate(rows[head][3])
    names = [c.name for c in district.candidates]
    finalists = [names.index(first.name), names.index(second.name)]
    district.finalists = finalists
    by_name = {p.name: p for p in district.places}
    for r in rows[head + 1 :]:
        if not r or not r[0].strip():
            continue
        label = r[0].strip()
        pair: list[int | None] = [csv_number(r[1]), csv_number(r[3])]
        if label == "Declaration Ballot Papers":
            label = "Declaration Votes"
        if label in by_name:
            by_name[label].runoff = pair
        elif label not in ("Polling Place Totals", "District Total"):
            raise ValueError(f"{district.name}: 2CP place {label!r} is not in the first preference file")
    district.declared.runoff = next(
        [csv_number(r[1]), csv_number(r[3])] for r in rows[head + 1 :] if r and r[0].strip() == "District Total"
    )


def event_2022() -> Event:
    first, runoff = zip_files(ELECTION_2022), zip_files(TCP_2022)
    event = Event("2022-03-19", "general", [ELECTION_2022, TCP_2022])
    for name, text in first.items():
        event.districts.append(parse_2022(name, text, runoff.get(name)))
    return event


# 2026 state election: the commission's results site reads JSON from a public API, one static file of candidates and
# one file of counts for the House of Assembly, and the same pair for the Legislative Council.

API = "https://apim-ecsa-production.azure-api.net/results-display"
STATIC_2026 = f"{API}/HAStatic/2026-03-21"
COUNTS_2026 = f"{API}/HAChange/2026-03-21/0"
COUNCIL_STATIC_2026 = f"{API}/LCStatic/2026-03-21"
COUNCIL_COUNTS_2026 = f"{API}/LCChange/2026-03-21/0"


def api_json(url: str) -> dict[str, Any]:
    out = json.loads(raw.cached(SOURCE, url).path.read_text())
    assert isinstance(out, dict)
    return out


def name_key(text: str) -> str:
    """The words of 'SANDERSON, Rachel' or 'Rachel SANDERSON', lowercased and sorted, to match one name to the other."""
    return " ".join(sorted(text.replace(",", " ").lower().split()))


def parse_2026(static: dict[str, Any], counts: dict[str, Any]) -> District:
    ids = [c["candidateId"] for c in static["candidates"]]
    candidates = [Candidate(c["candidateName"], c["partyId"]) for c in static["candidates"]]
    width = len(ids)
    runoff_ids = [
        i
        for i in ids
        if any(
            c["candidateId"] == i and c["twoCandidatePref"] is not None
            for place in counts["pollingPlaces"]
            for c in place["pollingCandidates"]
        )
    ]
    finalists = [ids.index(i) for i in runoff_ids] if len(runoff_ids) == 2 else None
    places: list[Place] = []

    def leaf(name: str, votes: dict[int, int], informal: int, runoff: dict[int, int | None], kind: str) -> Place:
        row = [votes.get(i) for i in ids]
        formal = sum(v or 0 for v in row)
        pair = [runoff.get(i) for i in runoff_ids] if finalists else None
        return Place(name, row, formal, informal, formal + informal, kind, pair)

    for place in counts["pollingPlaces"]:
        places.append(
            leaf(
                place["pollingPlaceName"],
                {c["candidateId"]: c["formalVotes"] for c in place["pollingCandidates"]},
                place["informalVotes"],
                {c["candidateId"]: c["twoCandidatePref"] for c in place["pollingCandidates"]},
                "standard",
            )
        )
    for group, label in (("declarations", "declarationType"), ("absentOrdinary", "absentOrdinaryType")):
        for item in counts[group]:
            places.append(
                leaf(
                    item[label],
                    {c["candidateId"]: c["votes"] for c in item["candidateVotes"]},
                    item["informalVotes"],
                    {
                        c["candidateId"]: c.get("twoCandidatePrefVoteCount", c.get("twoCandidatePrefVotes"))
                        for c in item["candidateVotes"]
                    },
                    "aggregate",
                )
            )
    rounds = counts["finalDistribution"]
    by_name = {name_key(c.name): n for n, c in enumerate(candidates)}
    first = [0] * width
    for r in rounds[0]["candidateResults"]:
        first[by_name[name_key(r["candidateName"])]] = r["progressiveTotal"]
    formal = sum(first)
    informal = sum(p.informal or 0 for p in places)
    declared = Place("District Total", list(first), formal, informal, formal + informal)
    elected = [by_name[name_key(r["candidateName"])] for r in rounds[-1]["candidateResults"] if r["isElected"]]
    if finalists:
        last = {by_name[name_key(r["candidateName"])]: r["progressiveTotal"] for r in rounds[-1]["candidateResults"]}
        declared.runoff = [last[i] for i in finalists]
    return District(
        static["districtName"], candidates, static["districtEnrolled"], places, declared, finalists, winners=elected
    )


def event_2026() -> Event:
    static, counts = api_json(STATIC_2026), api_json(COUNTS_2026)
    event = Event("2026-03-21", "general", [STATIC_2026, COUNTS_2026])
    by_district = {d["districtId"]: d for d in counts["districts"]}
    for d in static["districts"]:
        event.districts.append(parse_2026(d, by_district[d["districtName"]]))
    return event


# 2018 state election: one page per district and a second page per district for its polling places.

INDEX_2018 = f"{HOST}/elections/past-state-election-results?view=article&id=124:2018-state-election-results-breakdown&catid=12:elections"
NOT_DISTRICTS = {"House_of_Assembly", "Legislative_Council"}


def district_pages(index: str) -> list[str]:
    found = set(re.findall(r"/html/results/2018/([A-Za-z_]+)2?\.html", index)) - NOT_DISTRICTS
    return sorted(found)


def cells(row: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", " ", c)).strip()
        for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.DOTALL)
    ]


def tables(text: str) -> list[list[list[str]]]:
    out = []
    for table in re.findall(r"<table class=\"table table-striped\">(.*?)</table>", text, re.DOTALL):
        out.append([cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL)])
    return out


def html_candidate(cell: str) -> Candidate:
    parts = cell.split()
    return Candidate(" ".join(parts[:-1]), parts[-1])


def parse_html(name: str, main: str, booths: str) -> District:
    found = re.search(r'electorsEnrolledNumber">([\d,]+)<', main)
    if not found:
        raise ValueError(f"{name}: no enrolment")
    enrolled = number(found[1])
    first, runoff = tables(booths)
    head = first[0]
    stop = next(i for i, c in enumerate(head) if c.startswith(("Frml", "Formal")))
    candidates = [html_candidate(c) for c in head[1:stop]]
    width = len(candidates)
    places: list[Place] = []
    declared: Place | None = None
    for r in first[1:]:
        label = r[0]
        if not label or len(r) < width + 5 or label.startswith("District Total %"):
            continue
        place = Place(
            label,
            [number(c) for c in r[1 : 1 + width]],
            number(r[1 + width]),
            number(r[2 + width]),
            number(r[4 + width]),
        )
        if label == "District Total":
            declared = place
        elif label == "Polling Place Totals":
            continue
        elif label.startswith("Declaration"):
            place.name = "Declaration Votes"
            place.kind = "aggregate"
            places.append(place)
        else:
            places.append(place)
    assert declared is not None
    district = District(name, candidates, enrolled, places, declared)
    rhead = runoff[0]
    pair = [html_candidate(c.removesuffix("Number")) for c in rhead[1:] if c.endswith("Number")]
    names = [c.name for c in candidates]
    district.finalists = [names.index(pair[0].name), names.index(pair[1].name)]
    by_name = {p.name: p for p in places}
    for r in runoff[1:]:
        label = r[0]
        if len(r) < 5 or not label:
            continue
        counts = [number(r[1]), number(r[3])]
        label = "Declaration Votes" if label.startswith("Declaration") else label
        if label == "District Total":
            declared.runoff = counts
        elif label in by_name:
            by_name[label].runoff = counts
    return district


def event_2018() -> Event:
    index = raw.cached(SOURCE, INDEX_2018).path.read_text()
    pages = district_pages(index)
    event = Event("2018-03-17", "general", [INDEX_2018])
    for page in pages:
        main, booths = (f"{HOST}/html/results/2018/{page}{n}.html" for n in ("", "2"))
        event.files += [main, booths]
        event.districts.append(
            parse_html(
                page.replace("_", " "),
                raw.cached(SOURCE, main).path.read_text(),
                raw.cached(SOURCE, booths).path.read_text(),
            )
        )
    return event


# 1857 to 2006: the history of South Australian elections, by district.

JAENSCH = f"{HOST}/component/edocman/1857-2006-house-of-assembly-v1-by-dean-jaensch/download?Itemid=0"
FIRST_PAGE, LAST_PAGE = 25, 363  # the first page of the 1857 section and the last page before the appendices
PLURALITY_END = 1933  # the last general election of the sections that print polling places or contingency results
MONTHS = [
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December",
]  # fmt: skip
# Each general election's first polling day, as the section's introduction states it.
GENERAL = {
    1857: "03-09", 1860: "03-13", 1862: "11-10", 1865: "03-01", 1868: "04-06", 1870: "03-28", 1871: "12-14",
    1875: "02-10", 1878: "04-02", 1881: "04-08", 1884: "04-08", 1887: "03-19", 1890: "04-09", 1893: "04-15",
    1896: "04-25", 1899: "04-29", 1902: "05-03", 1905: "05-27", 1906: "11-03", 1910: "04-02", 1912: "02-10",
    1915: "03-27", 1918: "04-06", 1921: "04-09", 1924: "04-05", 1927: "03-26", 1930: "04-05", 1933: "04-08",
    1938: "03-19", 1941: "03-29", 1944: "04-29", 1947: "03-08", 1950: "03-04", 1953: "03-07", 1956: "03-03",
    1959: "03-07", 1962: "03-03", 1965: "03-06", 1968: "03-02", 1970: "05-30", 1973: "03-10", 1975: "07-12",
    1977: "09-17", 1979: "09-15", 1982: "11-06", 1985: "12-07", 1989: "11-25", 1993: "12-11", 1997: "10-11",
    2002: "02-09", 2006: "03-18",
}  # fmt: skip
# The last polling day of the general elections whose districts did not all vote on one day.
LAST = {
    1860: "04-03", 1862: "11-24", 1865: "03-13", 1868: "05-04", 1870: "04-05", 1871: "12-27", 1875: "03-01",
    1878: "04-30", 1881: "04-27", 1884: "04-23", 1887: "04-21", 1890: "04-23", 1893: "05-06", 1896: "05-02",
    1899: "05-06", 1906: "11-10", 1921: "04-16",
}  # fmt: skip
DECIMAL = re.compile(r"\d+\.\d")
TITLE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z' .&-]+?)(?: [–-] (?P<day>\d{1,2}) (?P<month>[A-Z][a-z]+) (?P<year>\d{4}))?$"
)


@functools.cache
def pdf_pages() -> dict[int, list[str]]:
    """The text of each page from FIRST_PAGE on, as its left and right columns."""
    path = str(raw.cached(SOURCE, JAENSCH).path)
    halves = []
    for x, w in ((0, 300), (300, 295)):
        out = subprocess.run(
            ["pdftotext", "-layout", "-f", str(FIRST_PAGE), "-l", str(LAST_PAGE), "-x", str(x), "-y", "0", "-W", str(w),
             "-H", "842", path, "-"],
            check=True, capture_output=True, text=True,
        ).stdout.split("\f")  # fmt: skip
        halves.append(out)
    return {FIRST_PAGE + i: [halves[0][i], halves[1][i]] for i in range(LAST_PAGE - FIRST_PAGE + 1)}


def group(tokens: list[str]) -> int:
    return int("".join(tokens))


def whole(tokens: list[str]) -> bool:
    """Whether the tokens are one number grouped in thousands, as '24 000' is."""
    return bool(re.fullmatch(r"[1-9]\d{0,2}", tokens[0])) and all(re.fullmatch(r"\d{3}", t) for t in tokens[1:])


def header(line: str) -> tuple[int, int, int, int] | None:
    """Enrolled, voted, formal, and informal from a line like '19 116 17 098  89.4 16 640 97.3 458 2.7'."""
    tokens = line.split()
    if sum(1 for t in tokens if DECIMAL.fullmatch(t)) != 3 or not all(re.fullmatch(r"[\d.,]+", t) for t in tokens):
        return None
    marks = [i for i, t in enumerate(tokens) if DECIMAL.fullmatch(t)]
    head, formal, informal = tokens[: marks[0]], tokens[marks[0] + 1 : marks[1]], tokens[marks[1] + 1 : marks[2]]
    fit = [
        (group(head[:cut]), group(head[cut:])) for cut in range(1, len(head)) if whole(head[:cut]) and whole(head[cut:])
    ]
    # Voted is the sum of the formal and informal ballot papers; the printed percentage is sometimes wrong.
    for enrolled, voted in fit:
        if voted == group(formal) + group(informal):
            return enrolled, voted, group(formal), group(informal)
    for enrolled, voted in fit:
        if enrolled and abs(100 * voted / enrolled - float(tokens[marks[0]])) < 0.15:
            return enrolled, voted, group(formal), group(informal)
    return None


def pdf_candidate(fields: list[str]) -> tuple[Candidate, int] | None:
    """A candidate from a line split at runs of spaces: name, an optional affiliation, votes, and a percentage."""
    if len(fields) < 2:
        return None
    if DECIMAL.fullmatch(fields[-1]):
        fields = fields[:-1]
    if not fields or not re.fullmatch(r"\d[\d ]*", fields[-1]):
        return None
    votes = int(fields[-1].replace(" ", ""))
    rest = fields[:-1]
    if not rest or len(rest) > 2:
        return None
    party = rest[1] if len(rest) == 2 else ""
    if party and not re.fullmatch(r"[A-Za-z][A-Za-z0-9&]{1,6}", party):
        if not re.fullmatch(r"\S{2,7}", party) or re.search(r"\w{3}", party):
            return None
        party = "?"  # a party code that the document prints as garbled symbols
    return label_candidate(rest[0], party), votes


def label_candidate(label: str, party: str) -> Candidate:
    """A candidate from a printed label like 'McINTOSH M*', where the asterisk marks a sitting member."""
    incumbent = label.endswith("*")
    label = label.rstrip("*").strip()
    words = label.split()
    surname, initials = (" ".join(words[:-1]), words[-1]) if len(words) > 1 else (label, "")
    shown = " ".join(
        w.title() if w.isupper() else re.sub(r"^(Mc|Mac)([A-Z]{2,})", lambda m: m[1] + m[2].capitalize(), w)
        for w in surname.split()
    )
    return Candidate(label, party, incumbent, f"{initials} {shown}".strip())


def elected(candidate: Candidate) -> bool:
    """Whether the history prints the surname in capitals, as it does for each elected candidate."""
    words = candidate.name.split()
    surname = " ".join(words[:-1] if len(words) > 1 else words)
    letters = re.sub(r"[^A-Za-z]", "", re.sub(r"^(Mc|Mac)(?=[A-Z])", "", surname))
    return len(letters) > 1 and letters.isupper()


@dataclass
class Block:
    title: str
    when: date | None
    stats: Stats | None
    first: list[tuple[Candidate, int | None]]
    after: list[tuple[Candidate, int]]
    note: str | None = None
    total: int | None = None  # the total of the candidates' votes where the history prints one


def blocks(text: str) -> list[Block]:
    lines = text.splitlines()
    out: list[Block] = []
    last_title = ""
    note = None
    current: Block | None = None
    mode = "first"
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        supplementary = re.match(r"([A-Z][A-Za-z ]+) – supplementary election (\d{1,2} [A-Z][a-z]+ \d{4})", stripped)
        if supplementary:
            last_title = f"{supplementary[1]} – {supplementary[2]}"
            note = (
                "The poll was a supplementary election, held because a candidate in the general election's poll died."
            )
            current = None
            continue
        stats = header(stripped)
        if (
            stats is None
            and re.fullmatch(r"[\d ]+(?:\.\d)?(?: +[\d ]+\.\d){2,}", stripped)
            and len(stripped.split()) >= 7
        ):
            raise ValueError(f"unreadable district header {stripped!r}")
        if stats:
            found = TITLE.match(last_title)
            if not found:
                raise ValueError(f"no district title before {stripped!r}: {last_title!r}")
            when = (
                date(int(found["year"]), MONTHS.index(found["month"]) + 1, int(found["day"])) if found["day"] else None
            )
            current = Block(found["name"].strip(), when, stats, [], [], note)
            note = None
            out.append(current)
            mode = "first"
            continue
        if stripped.startswith("Result after preferences"):
            mode = "after"
            continue
        if current is not None:
            got = pdf_candidate(re.split(r"\s{2,}", stripped))
            if got:
                (current.first if mode == "first" else current.after).append(got)
                continue
        if re.fullmatch(r"[A-Z][A-Za-z' .-]+?(?: [–-] \d{1,2} [A-Z][a-z]+ \d{4})?", stripped):
            last_title = stripped
    return out


Stats = tuple[int | None, int | None, int | None, int | None]
MISSING = re.compile(r"na", re.IGNORECASE)
PRINTED_DATE = re.compile(r"\d{1,2} [A-Z][a-z]+ \d{4}")


# A header line that prints the turnout percentage without its decimal point.
PRINTED_WRONG = {
    "3 238 2 126 6539 2 096 98.6 30 1.4": "3 238 2 126 65.7 2 096 98.6 30 1.4",
    "29 335 20 859 71.1 20 568 98 6 291 1.4": "29 335 20 859 71.1 20 568 98.6 291 1.4",
}


def early_header(line: str) -> Stats | None:
    """Enrolled, voted, formal, and informal from a header line of the colonial layout, where figures can be 'na'."""
    text = " ".join(line.split())
    text = re.sub(r" 100 0(?: 0(?:\.0)?)?$", " 100.0 0 0.0", PRINTED_WRONG.get(text, text))  # a poll with no informal
    tokens = text.split()
    if len(tokens) < 3 or not all(re.fullmatch(r"[\d.,]+|na|Na", t) for t in tokens) or not tokens[0].isdigit():
        return None
    full = header(line)
    if full:
        return full
    marks = [i for i, t in enumerate(tokens) if DECIMAL.fullmatch(t) or MISSING.fullmatch(t)]
    if not marks:
        return None
    head = tokens[: marks[0]]

    def grouped(part: list[str]) -> bool:  # some figures are printed without the space for thousands
        return whole(part) or (len(part) == 1 and part[0].isdigit())

    if (
        len(marks) == 2
        and all(DECIMAL.fullmatch(tokens[m]) for m in marks)
        and grouped(tokens[marks[0] + 1 : marks[1]])
    ):
        # A header without its roll: voted, formal, the formal share, informal, and the informal share.
        informal = group(tokens[marks[0] + 1 : marks[1]])
        for cut in range(1, len(head)):
            if grouped(head[:cut]) and grouped(head[cut:]) and group(head[:cut]) == group(head[cut:]) + informal:
                return None, group(head[:cut]), group(head[cut:]), informal
    fit = [
        (group(head[:cut]), group(head[cut:]))
        for cut in range(1, len(head))
        if grouped(head[:cut]) and grouped(head[cut:]) and group(head[cut:]) <= group(head[:cut])
    ]
    if DECIMAL.fullmatch(tokens[marks[0]]) and len(fit) != 1:
        fit = [f for f in fit if abs(100 * f[1] / f[0] - float(tokens[marks[0]])) < 0.6]
    enrolled: int | None
    voted: int | None
    if len(fit) == 1:
        enrolled, voted = fit[0]
    elif not fit and grouped(head):
        enrolled, voted = group(head), None
    else:
        return None
    segments = [tokens[a + 1 : b] for a, b in zip(marks, [*marks[1:], len(tokens)], strict=True)]
    formal = group(segments[0]) if len(marks) >= 2 and segments[0] and grouped(segments[0]) else None
    informal = group(segments[1]) if len(marks) >= 3 and segments[1] and grouped(segments[1]) else None
    if len(marks) >= 2 and not any(MISSING.fullmatch(t) for t in tokens) and (formal is None or informal is None):
        return None
    return enrolled, voted, formal, informal


def surnames(names: str) -> list[Candidate]:
    """Candidates from a list like 'BUTTERFIELD T* (ALP), HARVEY W (ALP)'."""
    out = []
    for part in re.split(r",\s*", re.sub(r"\(([^()]*)\)\*", r"*(\1)", names)):
        found = re.fullmatch(r"(?P<label>.*?)(?:\s*\((?P<party>[^()]*)\))?", part.strip())
        if found and found["label"]:
            out.append(label_candidate(found["label"], (found["party"] or "").strip()))
    return out


def printed_date(text: str) -> date:
    day, month, year = text.split()
    return date(int(year), MONTHS.index(month) + 1, int(day))


def early_blocks(pages: list[list[str]]) -> list[Block]:
    """Every block of the electorate results of a general election's section from 1857 to 1933, in page order.

    The section's polling place tables follow its electorate results. Where their heading falls in a page's left
    column, the right column can still end the electorate results above its own part of the tables, so it is read
    leniently: a line that is not part of a block ends the block."""
    out: list[Block] = []
    title = ""
    current: Block | None = None
    mode = "first"
    entries: list[list[str]] | None = None  # the uncontested table: district, names, date, and a shared party
    party = ""

    def finish() -> None:
        nonlocal entries
        for district, names, day, shared in entries or []:
            people = [c if c.party else Candidate(c.name, shared, c.incumbent, c.display) for c in surnames(names)]
            if not people:
                raise ValueError(f"no members returned for {district!r}: {names!r}")
            out.append(Block(district, printed_date(day) if day else None, None, [(c, None) for c in people], []))
        entries = None

    def step(raw_line: str, line: str, lenient: bool) -> None:
        nonlocal title, current, mode, entries, party
        if re.fullmatch(r"\d{1,3}|\d{4} GENERAL ELECTION|ELECTORATE RESULTS", line) or line.startswith(
            ("Enrolled Voted", "Candidates ")
        ):
            return
        if line == "Uncontested":
            finish()
            entries, current, party = [], None, ""
            return
        if entries is not None:
            if line.startswith(("District", "Division")):
                everyone = re.search(r"all (\w+)$", line)
                party = everyone[1] if everyone else ""
                return
            fields = re.split(r"\s{2,}", line.rstrip("."))
            if PRINTED_DATE.fullmatch(line) and entries:
                entries[-1][2] = line
            elif raw_line.startswith(" " * 6) and entries and len(fields) == 1:
                entries[-1][1] += ("" if entries[-1][1].endswith(",") else ",") + " " + fields[0]
            elif len(fields) >= 2:
                day = fields[-1] if len(fields) == 3 and PRINTED_DATE.fullmatch(fields[-1]) else ""
                entries.append([fields[0], fields[1], day, party])
            elif lenient:
                finish()
            else:
                raise ValueError(f"unreadable uncontested line {line!r}")
            return
        if re.match(r"\d+ BY-ELECTION", line) or line.startswith("BY-ELECTION"):
            current = None
            return
        stats = early_header(line)
        if stats is None and not lenient and re.fullmatch(r"[\d.,]+(?: +[\d.,]+| +na){3,}", line):
            raise ValueError(f"unreadable district header {line!r}")
        found = TITLE.match(title)
        if stats is not None and (found or not lenient):
            if not found:
                raise ValueError(f"no district title before {line!r}: {title!r}")
            when = (
                date(int(found["year"]), MONTHS.index(found["month"]) + 1, int(found["day"])) if found["day"] else None
            )
            current = Block(found["name"].strip(), when, stats, [], [])
            out.append(current)
            mode = "first"
            return
        if line.startswith("Result after preferences"):
            mode = "after"
            return
        if current is not None:
            fields = re.split(r"\s{2,}", line.replace("#", " ").strip())
            if len(fields) >= 3 and fields[-1] == "0" and re.fullmatch(r"\d[\d ]*", fields[-2]):
                fields = fields[:-1]  # a candidate with no votes has no percentage
            if fields[0] == "Total" and len(fields) == 2 and number(fields[1]) is not None:
                current.total = number(fields[1])
                return
            unknown = fields[-1].lower() == "na" and len(fields) > 1
            if (
                not unknown
                and current.stats is not None
                and current.when is not None
                and re.fullmatch(r"[A-Za-z'’ -]+ [A-Z]{1,4}\*?", fields[0])
                and (len(fields) == 1 or (len(fields) == 2 and re.fullmatch(r"[A-Za-z&]{2,6}`?", fields[1])))
            ):
                # A by-election whose votes the history does not print.
                found = pdf_candidate([fields[0], *(f.rstrip("`") for f in fields[1:]), "0"])
                if found:
                    current.first.append((found[0], None))
                    return
            got = pdf_candidate([*fields[:-1], "0"] if unknown else fields)
            if got and re.match(r"[A-Za-z]", got[0].name):
                if mode == "first":
                    current.first.append((got[0], None) if unknown else got)
                else:
                    current.after.append(got)
                return
        if re.fullmatch(r"[A-Z][A-Za-z' .&-]+?(?: [–-] \d{1,2} [A-Z][a-z]+ \d{4})?", line):
            title = line
            current = None
        elif current is not None and not re.fullmatch(r"\(([\d ,]+|na)\)|Plumpers.*|#.*|[a-z].*|.*;.*", line):
            if not lenient:
                raise ValueError(f"unread line in {current.title!r}: {line!r}")
            current = None

    for halves in pages:
        heading = False
        for side, text in enumerate(halves):
            lenient = side == 1 and any("POLLING PLACE RESULTS" in line for line in halves[0].splitlines())
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    if entries is not None:
                        finish()
                elif "POLLING PLACE RESULTS" in line:
                    finish()
                    heading = True
                    break
                else:
                    step(raw_line, line, lenient)
        if heading:
            break
    finish()
    return out


def district_of(block: Block) -> District:
    assert block.stats is not None
    candidates = [c for c, _ in block.first]
    district = District(
        block.title,
        candidates,
        block.stats[0],
        [],
        Place(block.title, [v for _, v in block.first], block.stats[2], block.stats[3], block.stats[1]),
        note=block.note,
    )
    if len(block.after) >= 2:
        # The distribution sometimes prints a candidate's initials or a surname differently, so candidates match on
        # the surname, allowing a one-letter misspelling.
        def surname(label: str) -> str:
            words = label.split()
            return " ".join(words[:-1] if len(words) > 1 else words).lower()

        names = [surname(c.name) for c in candidates]
        found = []
        for c, _ in block.after:
            close = difflib.get_close_matches(surname(c.name), names, n=2, cutoff=0.8)
            if len(close) != 1 or names.count(close[0]) != 1:
                raise ValueError(f"{block.title}: cannot match {c.name!r} in the distribution of preferences")
            found.append(names.index(close[0]))
        if len(set(found)) != len(found):
            raise ValueError(f"{block.title}: the distribution of preferences repeats a candidate")
        district.finalists = found
        district.declared.runoff = [v for _, v in block.after]
    return district


PRINTED_SPELLING = {"Gumeracka": "Gumeracha"}
# Elected candidates whom the history prints in lower case, with the reason that each was elected.
ELECTED = {
    (1921, "Adelaide"): ["Edwards AA"],  # 6,503 votes against 2,352 for the next candidate, in a three-member district
}


def system_for(day: date, seats: int) -> str:
    """The key of the electoral system that counted a poll before 1938."""
    if day >= date.fromisoformat(f"1930-{GENERAL[1930]}"):
        return CONTINGENT
    return FPTP if seats == 1 else BLOCK


def early_district(block: Block, year: int, day: date) -> District:
    """A district from the history's sections for 1857 to 1933, where the elected candidates' surnames are in capitals."""
    names = [c.name for c, _ in block.first]
    extra = ELECTED.get((year, block.title), [])
    winners = [i for i, (c, _) in enumerate(block.first) if elected(c) or c.name in extra]
    if len(extra) != sum(1 for c, _ in block.first if c.name in extra and not elected(c)):
        raise ValueError(f"{block.title}: elected {extra} are not all candidates in {names}")
    if not winners:
        raise ValueError(f"{block.title}: no candidate is elected in {names}")
    title = PRINTED_SPELLING.get(block.title, block.title)
    if block.stats is None:
        candidates = [c for c, _ in block.first]
        declared = Place(title, [None] * len(candidates), None, None, None)
        district = District(title, candidates, None, [], declared, unopposed=True)
    else:
        district = district_of(block)
        district.name = title
        district.declared.name = title
        if len(winners) > 1:
            if district.finalists:
                district.after = [
                    (district.candidates[i].name, v or 0)
                    for i, v in zip(district.finalists, district.declared.runoff or [], strict=True)
                ]
            district.finalists = district.declared.runoff = None
    if block.stats is not None:
        found = polling_tables().get(year, {}).get((block.title, block.when))
        if found is None:
            found = next(
                (t for (name, when), t in polling_tables().get(year, {}).items()
                 if when == block.when and letters(name) == letters(block.title)),
                None,
            )  # fmt: skip
        if found:
            with_places(district, found)
    district.seats = len(winners)
    district.winners = winners
    district.system = system_for(day, district.seats)
    return district


LAST_POLLING_PAGE = 237  # the last page of the polling place tables, which end with the 1918 section
PARTY_LINE = re.compile(r"[A-Z&]{2,6}")


@dataclass
class Table:
    """A district's polling place table: the candidate names above its columns, and a row for each polling place."""

    columns: list[str]
    rows: list[tuple[str, list[int | None]]] = field(default_factory=list)


@dataclass
class Text:
    """A line of a page, with where it sits and whether it runs upward, as the column headings of the tables do."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    upward: bool = False
    italic: bool = False


def letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def page_texts(page: pymupdf.Page) -> tuple[list[Text], list[Text]]:
    """A page's lines and its words, the words without those inside a rotated line."""
    lines: list[Text] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                x0, y0, x1, y1 = line["bbox"]
                italic = all(span["flags"] & 2 for span in line["spans"])
                lines.append(Text(x0, y0, x1, y1, text, abs(line["dir"][1]) > 0.5, italic))
    turned = [t for t in lines if t.upward]
    words = [
        Text(w[0], w[1], w[2], w[3], w[4])
        for w in page.get_text("words")
        if not any(t.x0 <= (w[0] + w[2]) / 2 <= t.x1 and t.y0 <= (w[1] + w[3]) / 2 <= t.y1 for t in turned)
    ]
    return lines, words


def read_row(words: list[Text], edges: list[float]) -> list[int | None] | None:
    """A row's numbers by column, where a column's numbers end at the right of its heading and a number of thousands
    prints as two words, such as '2 006'. A blank cell is None, and None is returned where the words are not numbers."""
    if not words or not all(re.fullmatch(r"\d+|-|na|Na", w.text) for w in words):
        return None

    @functools.cache
    def best(i: int, k: int) -> tuple[float, tuple[tuple[str, int], ...]] | None:
        if i == len(words):
            return 0.0, ()
        options: list[tuple[float, tuple[tuple[str, int], ...]]] = []
        for j in range(k, len(edges)):
            skipped = 100 * (j - k)
            rest = best(i + 1, j + 1)
            if rest:
                miss = (words[i].x1 - (edges[j] + 3)) ** 2 + skipped
                options.append((rest[0] + miss, ((words[i].text, j), *rest[1])))
            if (
                i + 1 < len(words)
                and re.fullmatch(r"[1-9]\d{0,2}", words[i].text)
                and re.fullmatch(r"\d{3}", words[i + 1].text)
                and words[i + 1].x0 - words[i].x1 < 6
                and (rest := best(i + 2, j + 1))
            ):
                # A first part that stands where a column's numbers end is more likely a number of its own.
                lone = 100 if any(abs(words[i].x1 - (edge + 3)) < 4 for edge in edges) else 0
                miss = (words[i + 1].x1 - (edges[j] + 3)) ** 2 + skipped + lone
                options.append((rest[0] + miss, ((words[i].text + words[i + 1].text, j), *rest[1])))
        return min(options, key=lambda o: o[0]) if options else None

    found = best(0, 0)
    if found is None:
        return None
    cells: list[int | None] = [None] * len(edges)
    for text, j in found[1]:
        cells[j] = None if text in ("-", "na", "Na") else int(text)
    return cells


def page_tables(page: pymupdf.Page) -> list[tuple[str | None, Table]]:
    """The tables on a page in the order the document draws them, with the title above each, which a continued table
    lacks."""
    lines, words = page_texts(page)
    labels = [t for t in lines if t.text == "Polling Place" and not t.upward]
    found: list[tuple[str | None, Table]] = []
    for label in labels:
        middle = (label.y0 + label.y1) / 2
        beside = [t.x0 for t in labels if t.x0 > label.x0 + 30 and abs((t.y0 + t.y1) / 2 - middle) < 130]
        reach = min(beside, default=700) - 5
        headings = sorted(
            (t for t in lines if t.upward and label.x1 < t.x0 < reach and abs((t.y0 + t.y1) / 2 - middle) < 75),
            key=lambda t: t.x0,
        )
        if not headings:
            continue
        top = min(t.y0 for t in headings)
        left = label.x0 - 10
        titles = [
            t
            for t in lines
            if t.italic and not t.upward and abs(t.x0 - label.x0) < 15 and label.y0 - 130 < t.y1 < top
            and not any(abs(o.x0 - label.x0) < 20 and t.y1 < o.y0 < label.y0 for o in labels)
        ]  # fmt: skip
        title = max(titles, key=lambda t: t.y1).text if titles else None
        groups: list[list[Text]] = []
        for t in headings:
            if groups and PARTY_LINE.fullmatch(t.text) and t.x0 - groups[-1][-1].x0 < 16:
                groups[-1].append(t)
            else:
                groups.append([t])
        edges = [max(t.x1 for t in g) for g in groups]
        start = max(t.y1 for t in headings)
        end = min(
            [t.y0 for t in lines if t.italic and not t.upward and abs(t.x0 - label.x0) < 15 and t.y0 > start] + [800]
        )
        rows: dict[int, list[Text]] = {}
        for w in sorted(words, key=lambda w: (w.y0, w.x0)):
            if left <= w.x0 and w.x1 <= max(edges) + 12 and start - 1 <= w.y0 < end - 1:
                same = next((y for y in rows if abs(y - w.y0) < 3), None)
                rows.setdefault(round(w.y0) if same is None else same, []).append(w)
        table = Table([g[0].text for g in groups])
        first = min(t.x0 for t in headings)
        for y in sorted(rows):
            row = sorted(rows[y], key=lambda w: w.x0)
            split = sum(1 for w in row if w.x1 < first - 6)
            if (
                0 < split < len(row)
                and re.fullmatch(r"[1-9]\d{0,2}", row[split - 1].text)
                and re.fullmatch(r"\d{3}", row[split].text)
                and row[split].x0 - row[split - 1].x1 < 6
            ):
                split -= 1  # the first part of a number of thousands, printed left of the column's heading
            name = " ".join(w.text for w in row[:split])
            cells = read_row(row[split:], edges)
            if name and cells is not None:
                table.rows.append((name, cells))
        found.append((title, table))
    return found


@functools.cache
def polling_tables() -> dict[int, dict[tuple[str, date | None], list[Table]]]:
    """Every polling place table of each general election's section, by district and by-election date."""
    document = pymupdf.open(str(raw.cached(SOURCE, JAENSCH).path))
    out: dict[int, dict[tuple[str, date | None], list[Table]]] = {}
    year = 0
    key: tuple[str, date | None] | None = None
    for number, halves in pdf_pages().items():
        if number > LAST_POLLING_PAGE:
            break
        found = re.match(r"\s*THE (\d{4}) GENERAL ELECTION", halves[0])
        if found:
            year, key = int(found[1]), None
            out[year] = {}
            continue
        for title, table in page_tables(document[number - 1]):
            if title is not None:
                match = TITLE.match(re.sub(r"([–-])(\d)", r"\1 \2", re.sub(r"\s*\((?i:continued)\)$", "", title)))
                if not match:
                    raise ValueError(f"page {number}: polling place table for {title!r}")
                when = (
                    date(int(match["year"]), MONTHS.index(match["month"]) + 1, int(match["day"]))
                    if match["day"]
                    else None
                )
                key = (match["name"].strip(), when)
            if key is None:
                raise ValueError(f"page {number}: a polling place table with no district")
            out[year].setdefault(key, []).append(table)
    return out


def align(columns: list[str], candidates: list[Candidate]) -> list[int] | None:
    """The candidate above each column, from the names the table prints, or None where a name is not a candidate's."""
    found: list[int] = []
    for column in columns:
        scores = [
            (difflib.SequenceMatcher(None, letters(column), letters(c.name)).ratio(), index)
            for index, c in enumerate(candidates)
            if index not in found
        ]
        best = max(scores, default=(0.0, -1))
        if best[0] < 0.8:
            return None
        found.append(best[1])
    return found


def with_places(district: District, tables: list[Table]) -> None:
    """Add the polling places of a district's tables, where the tables print the district's candidates and no others.

    A district's candidates can be split over several tables that list the same places, and a table can continue on
    another page, so a place is one row of the first table that names it."""
    places: dict[tuple[str, int], Place] = {}
    for table in tables:
        found = align(table.columns, district.candidates)
        if found is None or not table.rows:
            return
        seen: Counter[str] = Counter()
        for name, cells in table.rows:
            label = name.rstrip("*").strip()
            if label in ("Total", "Totals"):
                continue
            seen[label] += 1
            kind = "aggregate" if label in ("Absent", "Absent Voters", "Absent votes", "Declaration") else "standard"
            place = places.setdefault((label, seen[label]), Place(label, [None] * len(district.candidates), kind=kind))
            for column, index in enumerate(found):
                place.votes[index] = cells[column]
    covered = {index for table in tables for index in align(table.columns, district.candidates) or []}
    sums = [sum(p.votes[i] or 0 for p in places.values()) for i in range(len(district.candidates))]
    totals = [v or 0 for v in district.declared.votes]
    # Tables that leave out a candidate, or whose columns add up to more than the district's votes, or to well less
    # than them, are not read as places: the district keeps its total alone.
    if (
        covered != set(range(len(district.candidates)))
        or any(got > want * 1.02 + 3 for got, want in zip(sums, totals, strict=True))
        or sum(sums) < 0.6 * sum(totals)
    ):
        return
    district.places = list(places.values())


@functools.cache
def sections() -> dict[int, list[Block]]:
    """Every block of results in each general election's section of the history, in page order."""
    out: dict[int, list[Block]] = {}
    texts: dict[int, list[list[str]]] = {}
    year = 0
    for halves in pdf_pages().values():
        found = re.match(r"\s*THE (\d{4}) GENERAL ELECTION", halves[0])
        if found:
            year = int(found[1])
            out[year] = []
            if year <= PLURALITY_END:
                texts[year] = []
            continue
        if year:
            if year <= PLURALITY_END:
                texts[year].append(halves)
            else:
                for half in halves:
                    out[year].extend(blocks(half))
    for year, parts in texts.items():
        out[year] = early_blocks(parts)
    return out


@functools.cache
def appendix() -> dict[int, tuple[int | None, ...]]:
    """The history's own statewide totals by election: seats, roll, seats contested, contested roll, voted,
    informal, and formal."""
    path = str(raw.cached(SOURCE, JAENSCH).path)
    text = subprocess.run(
        ["pdftotext", "-layout", "-f", "365", "-l", "365", path, "-"], check=True, capture_output=True, text=True
    ).stdout
    out: dict[int, tuple[int | None, ...]] = {}
    for line in text.split("HOUSE OF ASSEMBLY: ENROLMENT AND TURNOUT")[-1].splitlines():
        tokens = line.split()
        if len(tokens) >= 5 and re.fullmatch(r"\d{4}", tokens[0]):
            cells = [None if t == "Na" else int(t) for t in tokens[1:]]
            if len(cells) == 6:
                cells.insert(3, None)  # the contested roll is blank in some years
            out[int(tokens[0])] = tuple(cells)
    return out


def history_district(year: int, day: date, block: Block) -> District:
    return early_district(block, year, day) if year <= PLURALITY_END else district_of(block)


def history_event(year: int) -> Event:
    day = date.fromisoformat(f"{year}-{GENERAL[year]}")
    event = Event(day.isoformat(), "general", [JAENSCH])
    if year in LAST:
        event.last = date.fromisoformat(f"{year}-{LAST[year]}")
    event.districts = [history_district(year, day, b) for b in sections()[year] if b.when is None]
    return event


def history_by_elections(year: int, when: date) -> Event:
    event = Event(when.isoformat(), "by_election", [JAENSCH])
    event.districts = [history_district(year, when, b) for b in sections()[year] if b.when == when]
    return event


def history_events() -> dict[str, functools.partial[Event]]:
    out: dict[str, functools.partial[Event]] = {}
    for year, monthday in GENERAL.items():
        out[f"{year}-{monthday}"] = functools.partial(history_event, year)
        for when in sorted({b.when for b in sections()[year] if b.when}):
            out[f"{when.isoformat()}-by-election"] = functools.partial(history_by_elections, year, when)
    return out


@functools.cache
def events() -> dict[str, Callable[[], Event]]:
    """Every election the pipeline builds, by package name, oldest first."""
    out: dict[str, Callable[[], Event]] = {
        **history_events(),
        **article_events(),
        "2018-03-17": event_2018,
        "2022-03-19": event_2022,
        "2026-03-21": event_2026,
    }
    return dict(sorted(out.items()))


def packages() -> list[str]:
    return ["au.ecsa/registry", *(f"au.ecsa/{name}" for name in events()), *council_packages()]


def fetch() -> None:
    for url in (STATIC_2026, COUNTS_2026, COUNCIL_STATIC_2026, COUNCIL_COUNTS_2026):
        raw.fetch(SOURCE, url, suffix=".json")
    for url in (COUNCIL_ELECTED_2026, COUNCIL_SEATS_2026):
        raw.fetch(SOURCE, url, suffix=".csv")
    for url in (ELECTION_2022, TCP_2022, COUNCIL_2022_PLACES):
        raw.fetch(SOURCE, url, suffix=".zip")
    for url in (JAENSCH, COUNCIL_HISTORY):
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in (COUNCIL_2022_GROUPED, COUNCIL_2022_UNGROUPED, COUNCIL_2022_ELECTED):
        raw.fetch(SOURCE, url, suffix=".csv")
    for url in (COUNCIL_2014, COUNCIL_2018):
        raw.fetch(SOURCE, url, suffix=".html")
    fetch_articles()
    index = raw.fetch(SOURCE, INDEX_2018, suffix=".html")
    for page in district_pages(index.path.read_text()):
        for n in ("", "2"):
            raw.fetch(SOURCE, f"{HOST}/html/results/2018/{page}{n}.html", suffix=".html")


def parse() -> None:
    registry().write()
    for name in events():
        build(name).write()
    for name in council_events():
        build_council(name).write()


def emit() -> None:
    package.run("load", packages())


@functools.cache
def event(name: str) -> Event:
    return events()[name]()


METHOD = (
    "Counted by the Electoral Commission of South Australia and published by polling place. Each district elects one "
    "member of the House of Assembly by full preferential voting. First preference votes are stored for each "
    "candidate. The commission distributes preferences to the final two candidates, and their votes after that "
    "distribution are given in a note on each district's result set, because the archive stores a count only at the first preference stage. Votes cast away from a polling place on the day are published as one "
    "line of declaration votes. Informal ballot papers are those that do not count, and valid votes are the formal "
    "ballot papers. Official outcomes are stored as declared."
)


METHOD_PLURALITY = (
    "Published in the history of South Australian elections by district. Each elector marked the candidates they "
    "supported, up to the number of members the district elected, and the candidates with the most votes were "
    "elected. First votes are stored for each candidate as the history prints them. Votes cast away from a polling "
    "place are not separated. The history prints the elected candidates' surnames in capitals, and the outcomes are "
    "stored as it prints them. Informal ballot papers are those that do not count, and valid votes are the formal "
    "ballot papers."
)
METHOD_CONTINGENT = (
    "Published in the history of South Australian elections by district. Each elector numbered candidates in order "
    "of preference. A candidate with an absolute majority of the formal votes was elected; if none had one, the "
    "candidate with the fewest first preference votes was excluded and the ballot papers passed on by the next "
    "preference, until one did. A district that elected several members repeated the count, returning the elected "
    "candidates' first preference votes by their second preferences, for each further seat. First preference "
    "votes are stored for each candidate, and the votes the history prints after the distribution of preferences "
    "are given in a note on each district's result set, because the archive stores a count only at the first "
    "preference stage. The history prints the elected candidates' surnames in capitals, and the outcomes are "
    "stored as it prints them. Informal ballot papers are those that do not count, and valid votes are the formal "
    "ballot papers."
)


def registry() -> Package:
    p = Package(
        "au.ecsa/registry",
        SOURCE,
        "South Australian parties, both houses of Parliament, and their electoral systems, from the Electoral "
        "Commission SA",
    )
    p.external.add(AUSTRALIA)
    p.add(
        "source",
        id=ECSA,
        kind="official_body",
        publisher="Electoral Commission SA",
        title="South Australian election results published by the Electoral Commission SA",
        url=PAGE,
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Electoral Commission SA",
    )
    files = {url for name in events() for url in event(name).files}
    files |= {url for name in council_events() for url in council_event(name).files}
    for url in sorted(files):
        p.artifact(raw.cached(SOURCE, url), ECSA)
    p.add(
        "source",
        id=WIKIPEDIA,
        kind="community",
        publisher="Wikipedia",
        title="Wikipedia articles on South Australian elections",
        url="https://en.wikipedia.org/",
        license="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="Wikipedia contributors",
        notes="Read from each article's wikitext, pinned to the revision current at retrieval. Used only for the "
        "polling days of the elections whose results pages do not state them.",
    )
    for url in sorted({url for name in events() for url in event(name).dated}):
        p.artifact(raw.cached(SOURCE, url), WIKIPEDIA)
    p.add("scheme", code="au-sa.district", polity_id=AUSTRALIA, kind="electoral_district", rank=1, noun="district")
    p.add("scheme", code="au-sa.place", polity_id=AUSTRALIA, kind="precinct", rank=2, noun="polling place")
    p.add(
        "scheme",
        code="au-sa.council",
        polity_id=AUSTRALIA,
        kind="electoral_district",
        rank=1,
        noun="Legislative Council district",
    )
    p.add("body", id=ASSEMBLY, polity_id=AUSTRALIA, kind="lower_house")
    p.name(ASSEMBLY, "South Australian House of Assembly", "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=AUSTRALIA, rules={"formula": "irv"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each of the 47 districts elects one member by full preferential voting: voters number every box, "
        "and the candidate with the fewest votes is excluded and their ballots passed on until one candidate holds "
        "a majority.",
        lang="en",
    )
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=AUSTRALIA,
        valid_to=date(1929, 12, 31),
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="From 1857 to 1929 a district that elected one member returned the candidate with the most votes. "
        "Electors marked the candidates they supported, except in 1857, when they deleted the names of the "
        "candidates they did not support.",
        lang="en",
    )
    p.add(
        "electoral_system",
        id=BLOCK,
        polity_id=AUSTRALIA,
        valid_to=date(1929, 12, 31),
        rules={"formula": "block"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", BLOCK),
        entity_id=BLOCK,
        kind="methodology",
        body_md="From 1857 to 1929 a district that elected several members gave each elector as many votes as there "
        "were members, and the candidates with the most votes were elected. An elector could use fewer votes, "
        "and so could vote for one candidate alone, which the history calls plumping.",
        lang="en",
    )
    p.add(
        "electoral_system",
        id=CONTINGENT,
        polity_id=AUSTRALIA,
        valid_from=date(1930, 1, 1),
        valid_to=date(1937, 12, 31),
        rules={"formula": "irv"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", CONTINGENT),
        entity_id=CONTINGENT,
        kind="methodology",
        body_md="Contingency voting, used for the elections of 1930 and 1933: electors numbered the candidates, and "
        "in a district that elected one member the count was the instant runoff. In a district of several members "
        "the count was repeated for each seat, and the votes of the candidates already elected returned to the "
        "count by their second preferences. The rules stored here are those of the single-member count; the "
        "engine does not recompute the repeated count.",
        lang="en",
    )
    council(p)
    tags = {c.party for name in events() for d in event(name).districts for c in d.candidates if known_party(c.party)}
    tags |= {
        o.party for name in council_events() for c in council_event(name).contests for o in c.options
        if known_party(o.party)
    }  # fmt: skip
    for tag in sorted(tags):
        p.add("party", id=party_id(tag), kind="party", polity_id=AUSTRALIA)
        if tag in PARTIES or tag in COUNCIL_PARTIES:
            p.name(party_id(tag), PARTIES.get(tag) or COUNCIL_PARTIES[tag], "en", "official")
            p.name(party_id(tag), tag, "en", "abbreviation", False)
        else:
            p.name(party_id(tag), tag, "en", "abbreviation")
    return p


def _put(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None, count_round: int = 0
) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=count_round,
        flags=0,
        value=value,
    )


def build(name: str) -> Package:
    ev = event(name)
    day = ev.day
    first = date.fromisoformat(day)
    general = ev.kind == "general"
    p = Package(
        f"au.ecsa/{name}",
        SOURCE,
        f"The South Australian {'state election' if general else 'by-election'} of {first} for the House of Assembly",
        depends=["au.ecsa/registry"],
    )
    p.external.add(AUSTRALIA)
    for url in [*ev.files, *ev.dated]:
        p.uses(raw.cached(SOURCE, url))
    election = key("election", day)
    path = f"au-sa/{day}-{'state' if general else 'by-election'}"
    if general:
        p.add(
            "election",
            id=election,
            polity_id=AUSTRALIA,
            kind="general",
            date_from=first,
            date_to=ev.last or first,
            status="held",
        )
        p.add("slug", path=path, entity_id=election, is_current=True)
        p.name(election, f"{first.year} South Australian state election", "en", "official")
    off: list[str] = []
    cast: list[str] = []
    turnout: list[str] = []
    unprinted: list[str] = []
    apart: list[str] = []
    dropped: list[str] = []
    itemized: list[str] = []
    slugs: set[str] = set()
    for d in ev.districts:
        name = title(d.name) if d.name.isupper() else d.name
        slug = changeset.slug(name)
        if slug in slugs:
            raise ValueError(f"{day}: district {name} appears twice")
        slugs.add(slug)
        if not general:
            election = key("election", day, slug)
            path = f"au-sa/{day}-by-election-{slug}"
            p.add(
                "election",
                id=election,
                polity_id=AUSTRALIA,
                kind="by_election",
                date_from=first,
                date_to=first,
                status="held",
            )
            p.add("slug", path=path, entity_id=election, is_current=True)
            p.name(election, f"{first.year} {name} by-election", "en", "official")
        contest = key("contest", day, slug)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=ASSEMBLY,
            magnitude=d.seats,
            round_number=1,
            electoral_system_id=d.system or SYSTEM,
            default_result_kind="tacit" if d.unopposed else "counted",
        )
        p.name(contest, f"{name}, House of Assembly", "en", "official")
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, slug)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="tacit" if d.unopposed else "counted",
            source_id=ECSA,
            status="active",
            is_default=True,
            completeness=None if d.unopposed else 1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md={FPTP: METHOD_PLURALITY, BLOCK: METHOD_PLURALITY, CONTINGENT: METHOD_CONTINGENT}.get(
                d.system or SYSTEM, METHOD
            ),
            lang="en",
        )
        caveats: list[str] = []
        if d.unopposed:
            caveats.append(
                "There was no poll: "
                + ("the only candidate" if d.seats == 1 else "as many candidates as members")
                + " stood, and the history lists the returned members without votes."
            )
        elif d.seats > 1 and d.system == BLOCK:
            caveats.append(
                f"The district elected {d.seats} members and each elector could vote for up to {d.seats} candidates, "
                "so the candidates' votes add up to more than the formal ballot papers."
            )
        if (
            d.winners is not None
            and not d.unopposed
            and any(c.name and v is None for c, v in zip(d.candidates, d.declared.votes, strict=True))
        ):
            caveats.append("The history does not print the candidates' votes in this contest.")
        for index, caveat in enumerate(caveats):
            p.add(
                "note",
                id=key("note", result_set, "district", index),
                entity_id=result_set,
                kind="caveat",
                body_md=caveat,
                lang="en",
            )
        if d.note:
            p.add(
                "note",
                id=key("note", result_set, "caveat"),
                entity_id=result_set,
                kind="caveat",
                body_md=d.note,
                lang="en",
            )
        if any(c.party == "?" for c in d.candidates):
            p.add(
                "note",
                id=key("note", result_set, "party"),
                entity_id=result_set,
                kind="caveat",
                body_md="The history prints the party codes of some candidates in this contest as unreadable symbols, "
                "and an erratum says the affiliations were amended in July 2014 without giving the corrected codes. "
                "Their parties are left blank.",
                lang="en",
            )
        options: list[str] = []
        for position, c in enumerate(d.candidates, 1):
            person = key("person", day, slug, position)
            p.add("person", id=person)
            p.name(person, c.display or person_name(c.name), "en", "official")
            option = key("option", day, slug, position)
            options.append(option)
            party = party_id(c.party) if known_party(c.party) else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=c.name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", day, slug, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.name,
                member_of_party_id=party,
                is_independent=c.party in ("", "IND"),
                is_incumbent=c.incumbent,
            )
        if d.unopposed:
            for index, option in enumerate(options):
                kind = "elected" if index in (d.winners or []) else "lost"
                p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
            continue
        district_unit = key("unit", day, slug)
        p.add(
            "reporting_unit",
            id=district_unit,
            polity_id=AUSTRALIA,
            scheme_code="au-sa.district",
            code=f"{day}:{slug}",
            area_relation="unlocated",
            kind="aggregate" if d.places else "standard",
        )
        p.name(district_unit, f"District of {name}", "en", "official")
        leaves = d.places or [d.declared]
        seen: Counter[str] = Counter()
        for place in leaves:
            if d.places:
                seen[place.name] += 1
                code = f"{day}:{slug}:{changeset.slug(place.name)}" + (
                    f"-{seen[place.name]}" if seen[place.name] > 1 else ""
                )
                unit = key("unit", code)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=AUSTRALIA,
                    scheme_code="au-sa.place",
                    code=code,
                    parent_id=district_unit,
                    area_relation="unlocated",
                    kind=place.kind,
                )
                p.name(unit, f"{place.name} ({name})", "en", "official")
            else:
                unit = district_unit
            for option, value in zip(options, place.votes, strict=True):
                _put(p, result_set, unit, option, VOTES, value)
            _put(p, result_set, unit, None, VALID, place.formal)
            _put(p, result_set, unit, None, INVALID, place.informal)
            _put(p, result_set, unit, None, CAST, place.total)

        if d.places and all(pl.formal is None and pl.informal is None and pl.total is None for pl in d.places):
            # The polling place tables itemize candidates' votes only, so the district's ballot papers sit on a unit
            # of their own.
            ballots = key("unit", day, slug, "ballots")
            p.add(
                "reporting_unit",
                id=ballots,
                polity_id=AUSTRALIA,
                scheme_code="au-sa.district",
                code=f"{day}:{slug}:ballots",
                parent_id=district_unit,
                area_relation="unlocated",
                kind="aggregate",
            )
            p.name(ballots, f"District of {name}: ballot papers", "en", "official")
            _put(p, result_set, ballots, None, VALID, d.declared.formal)
            _put(p, result_set, ballots, None, INVALID, d.declared.informal)
            _put(p, result_set, ballots, None, CAST, d.declared.total)
            if d.seats == 1 and d.declared.formal is not None:
                itemized.append(f"{name} ({d.declared.formal:,} formal votes)")
        if d.misprinted:
            dropped.append(f"{name} ({d.misprinted} polling places)")
        if d.places:
            sums = [sum(pl.votes[i] or 0 for pl in d.places) for i in range(len(options))]
            differ = [i for i, v in enumerate(d.declared.votes) if v is not None and sums[i] != v]
            if differ:
                apart.append(f"{name} ({len(differ)} of {len(options)} candidates)")
                p.add(
                    "note",
                    id=key("note", result_set, "places"),
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The polling place table adds up to a different number of votes than the "
                    f"district total for {len(differ)} of the {len(options)} candidates"
                    + (
                        ", because it leaves some votes out"
                        if all(sums[i] < (d.declared.votes[i] or 0) for i in differ)
                        else ""
                    )
                    + ". Both are stored as printed.",
                    lang="en",
                )
        if d.enrolled is not None:
            if d.places:
                roll = key("unit", day, slug, "roll")
                p.add(
                    "reporting_unit",
                    id=roll,
                    polity_id=AUSTRALIA,
                    scheme_code="au-sa.district",
                    code=f"{day}:{slug}:roll",
                    parent_id=district_unit,
                    area_relation="unlocated",
                    kind="aggregate",
                )
                p.name(roll, f"District of {name}: electoral roll", "en", "official")
            else:
                roll = district_unit
            _put(p, result_set, roll, None, REGISTERED, d.enrolled)
            p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=d.enrolled)
        total = d.declared
        if None not in (total.formal, total.informal, total.total) and (
            (total.formal or 0) + (total.informal or 0) != total.total
        ):
            cast.append(f"{name} ({total.total:,} against {(total.formal or 0) + (total.informal or 0):,})")
        if d.enrolled is not None and total.total is not None and total.total > d.enrolled:
            turnout.append(f"{name} ({total.total:,} against {d.enrolled:,} enrolled)")
        if d.seats == 1 and total.formal is not None and all(v is None for v in total.votes):
            unprinted.append(f"{name} ({total.formal:,} formal votes)")
        elif d.seats == 1 and total.formal is not None and sum(v or 0 for v in total.votes) != total.formal:
            off.append(f"{name} ({sum(v or 0 for v in total.votes):,} against {total.formal:,})")
        for measure, value in ((CAST, total.total), (INVALID, total.informal), (VALID, total.formal)):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option, value in zip(options, total.votes, strict=True):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
        ranked = d.finalists
        if ranked and total.runoff:
            held = [(d.candidates[i].name, v or 0) for i, v in zip(ranked, total.runoff, strict=True)]
            listing = ", ".join(f"{label} {v:,}" for label, v in held)
            p.add(
                "note",
                id=key("note", result_set, "final"),
                entity_id=result_set,
                kind="context",
                body_md=f"After the distribution of preferences the {len(held)} remaining candidates held {listing} votes.",
                lang="en",
            )
            win = {max(zip(ranked, total.runoff, strict=True), key=lambda pair: pair[1] or 0)[0]}
        else:
            win = {max(range(len(options)), key=lambda i: total.votes[i] or 0)}
        if d.after:
            listing = ", ".join(f"{label} {v:,}" for label, v in d.after)
            p.add(
                "note",
                id=key("note", result_set, "final"),
                entity_id=result_set,
                kind="context",
                body_md=f"After the distribution of preferences the history prints these votes for the {len(d.after)} "
                f"candidates it shows: {listing}.",
                lang="en",
            )
        if d.winners is not None:
            win = set(d.winners)
        for index, option in enumerate(options):
            if index in win:
                kind = "elected"
            elif ranked and index in ranked:
                kind = "lost"
            else:
                kind = "eliminated" if ranked else "lost"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    if cast:
        p.accept(
            "accounting.ballots_sum",
            "The source prints a number of ballots cast that is not its formal plus its informal ballot papers in "
            + ", ".join(cast),
        )
    if turnout:
        p.accept(
            "accounting.turnout",
            "The source prints a number of ballots cast that is more than its electors enrolled in "
            + ", ".join(turnout),
        )
    reasons = []
    if apart:
        reasons.append(
            "The source's polling place tables add up to different votes than its district totals in "
            + ", ".join(apart)
        )
    if dropped:
        reasons.append(
            "The source's polling place table prints the informal count in its formal column, so the archive leaves "
            "those formal votes out and the places add up to fewer formal votes than the district total in "
            + ", ".join(dropped)
        )
    if reasons:
        p.accept("accounting.declared", "; ".join(reasons))
    valid = [
        (
            itemized,
            (
                "The source prints a district's ballot papers apart from its polling place tables, which itemize "
                "candidates' votes only, so the unit that holds the ballot papers holds no candidate votes in "
            ),
        ),
        (unprinted, "The source prints no candidate votes, only formal votes, for "),
        (off, "The source prints candidate votes that do not add up to its own formal votes in "),
    ]
    if any(found for found, _ in valid):
        p.accept(
            "accounting.votes_valid",
            "; ".join(lead + ", ".join(found) for found, lead in valid if found),
        )
    return p


# The Legislative Council: the history's second volume from 1857 to 2006, and the commission's own results since.

COUNCIL_HISTORY = f"{HOST}/component/edocman/1857-2006-legislative-council-v2-by-dean-jaensch/download?Itemid=0"
COUNCIL = key("body", "legislative-council")
COUNCIL_BLOCK = key("electoral_system", "legislative-council", "block")
COUNCIL_PREFERENTIAL = key("electoral_system", "legislative-council", "preferential")
COUNCIL_LIST = key("electoral_system", "legislative-council", "list")
COUNCIL_STV = key("electoral_system", "legislative-council", "stv")
COUNCIL_FIRST, COUNCIL_LAST = 25, 161  # the first page of the 1857 section and the last page of the 1973 section
COUNCIL_TITLE = re.compile(
    r"^(?P<name>THE PROVINCE|MIDLAND|(?:NORTH[- ]EASTERN|MIDLAND|SOUTHERN|NORTHERN|CENTRAL) DISTRICT(?: NO\.? ?\d)?)\s*"
    r"(?:[–-]\s*(?P<date>\d{1,2} [A-Z][a-z]+ \d{4})\*?)?$",
    re.IGNORECASE,
)
COUNCIL_DISTRICT = re.compile(
    r"^(?P<name>The Province|North[- ]Eastern|Midland|Southern|Northern|Central(?: No\.? ?\d)?)(?:\s{2,}(?P<rest>.*))?$"
)
ANCHOR = re.compile(r"^(Enrolled|Candidates|Uncontested|\d+ BY-ELECTIONS?|BY-ELECTIONS?\b)")


def council_split(page: pymupdf.Page) -> float:
    """Where a page's right column begins: the left edge of the leftmost heading, title, or table header that sits
    right of the middle, or the page's width where the page has one column."""
    starts = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            x0 = line["bbox"][0]
            if x0 > 220 and (ANCHOR.match(text) or COUNCIL_TITLE.match(text)):
                starts.append(x0)
    return min(starts) - 4 if starts else page.rect.width


@functools.cache
def council_pages() -> dict[int, list[str]]:
    """The text of each page of the electorate results to 1973, as its left and right columns."""
    path = str(raw.cached(SOURCE, COUNCIL_HISTORY).path)
    document = pymupdf.open(path)
    out: dict[int, list[str]] = {}
    for number in range(COUNCIL_FIRST, COUNCIL_LAST + 1):
        page = document[number - 1]
        split = council_split(page)
        halves = []
        for x, w in ((0.0, split), (split, page.rect.width - split)):
            if w < 1:
                halves.append("")
                continue
            halves.append(
                subprocess.run(
                    [
                        "pdftotext",
                        "-layout",
                        "-f",
                        str(number),
                        "-l",
                        str(number),
                        "-x",
                        str(int(x)),
                        "-y",
                        "0",
                        "-W",
                        str(int(w)),
                        "-H",
                        str(int(page.rect.height)),
                        path,
                        "-",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
        out[number] = halves
    return out


def council_days(heading: str, year: int) -> list[date]:
    """The polling days of a heading like 'ELECTORATE RESULTS – 9, 16, 23 MAY' or '27 MAY, 3 JUNE'."""
    found = re.findall(r"(\d{1,2})(?:\s+([A-Z]+))?", heading.split("RESULTS", 1)[1])
    days: list[date] = []
    month = 0
    for day, name in reversed(found):
        if name:
            month = MONTHS.index(name.title()) + 1
        days.append(date(year, month, int(day)))
    return sorted(days)


@dataclass
class CouncilSection:
    """A general election's section of the history: its polling days, and its blocks of electorate results."""

    year: int
    days: list[date] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    notes: dict[tuple[str, date], str] = field(default_factory=dict)


def council_members(text: str) -> list[Candidate]:
    """Members from an uncontested table, where some names print a comma before the initials, as 'MORROW, W'."""
    return surnames(re.sub(r"([A-Za-z]), ([A-Z]{1,4}\*?)(?=$|\s*[(,])", r"\1 \2", text.strip().rstrip(".")))


@functools.cache
def council_sections() -> dict[int, CouncilSection]:
    out: dict[int, CouncilSection] = {}
    section: CouncilSection | None = None
    on = False
    title: tuple[str, date | None] | None = None
    current: Block | None = None
    mode = "first"
    entries: list[list[object]] | None = None  # the uncontested table: district, members, and date

    def finish() -> None:
        nonlocal entries
        assert section is not None
        for district, names, when in entries or []:
            people = council_members(str(names))
            if not people:
                raise ValueError(f"{section.year}: no members returned for {district!r}: {names!r}")
            assert when is None or isinstance(when, date)
            section.blocks.append(Block(str(district), when, None, [(c, None) for c in people], []))
        entries = None

    for halves in council_pages().values():
        for text in halves:
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line or re.fullmatch(r"\d{1,3}", line):
                    continue
                found = re.fullmatch(r"(?:THE )?(\d{4}) GENERAL ELECTION", line)
                if found:
                    if section is None or section.year != int(found[1]):
                        if section is not None:
                            finish()
                        section = out[int(found[1])] = CouncilSection(int(found[1]))
                        on, title, current = False, None, None
                    continue
                if section is None:
                    continue
                if "POLLING PLACE RESULTS" in line or "DIVISION RESULTS" in line:
                    finish()
                    on, title, current = False, None, None
                    continue
                if "ELECTORATE RESULTS" in line or re.fullmatch(r"(?:\d+ )?BY-ELECTIONS?", line):
                    finish()
                    on, title, current = True, None, None
                    if "BY-ELECTION" not in line:
                        section.days = council_days(line, section.year)
                    continue
                if not on:
                    continue
                if line == "Uncontested":
                    finish()
                    entries, current = [], None
                    continue
                if entries is not None:
                    if line.startswith(("District", "Division")):
                        continue
                    district = COUNCIL_DISTRICT.match(line)
                    dated = re.fullmatch(r"(\d{1,2} [A-Z][a-z]+ \d{4})\*?(?:\s{2,}(.*))?", line)
                    if district and not COUNCIL_TITLE.match(line):
                        entries.append([district["name"], district["rest"] or "", None])
                        continue
                    if dated and entries:
                        entries[-1][2] = printed_date(dated[1])
                        if dated[2]:
                            entries[-1][1] = f"{entries[-1][1]}, {dated[2]}".strip(", ")
                        continue
                    if entries and re.fullmatch(
                        r"[A-Z][A-Za-z’' -]+,? [A-Z]{1,4}\s?\*?(?:\s?\*?\([A-Za-z&]+\))?", line
                    ):
                        entries[-1][1] = f"{entries[-1][1]}, {line}".strip(", ")
                        continue
                    if line.startswith("*") and entries:
                        last = entries[-1]
                        if isinstance(last[2], date):
                            section.notes[(str(last[0]), last[2])] = line.lstrip("*")
                        continue
                    if re.match(r"[a-z]", line):
                        continue  # the rest of a footnote
                    finish()
                found = COUNCIL_TITLE.match(line)
                if found:
                    title = (found["name"], printed_date(found["date"]) if found["date"] else None)
                    current = None
                    continue
                if line.startswith(("Enrolled", "Candidates", "Plumpers")) or re.fullmatch(
                    r"\((?:[\d ]+|na)\)|na", line
                ):
                    continue
                stats = council_header(COUNCIL_PRINTED_WRONG.get(" ".join(line.split()), line))
                if stats is None and title is not None and re.fullmatch(r"[\d.,]+(?: +[\d.,]+| +na)+", line):
                    raise ValueError(f"{section.year}: unreadable header {line!r} under {title[0]!r}")
                if stats is not None and title is not None:
                    current = Block(title[0], title[1], stats, [], [])
                    section.blocks.append(current)
                    title, mode = None, "first"
                    continue
                if current is None:
                    continue
                if line.startswith("Result after preferences"):
                    mode = "after"
                    continue
                # A figure of thousands printed with a point, as '3.085' is.
                line = re.sub(r"(?<=\s)(\d)\.(\d{3})(?=\s)", r"\1 \2", line)
                fields = re.split(r"\s{2,}", line.replace("#", " ").strip())
                if fields[0] == "Total" and len(fields) == 2 and number(fields[1]) is not None:
                    current.total = number(fields[1])
                    current = None
                    continue
                got = pdf_candidate(fields)
                if got and re.match(r"[A-Za-z’']", got[0].name):
                    (current.first if mode == "first" else current.after).append(got)
                    continue
                current = None
    if section is not None:
        finish()
    return out


# Header lines that print a percentage without its decimal point.
COUNCIL_PRINTED_WRONG = {"9 841 4 163 42.3 4 044 9701 119 2.9": "9 841 4 163 42.3 4 044 97.1 119 2.9"}


def council_header(line: str) -> Stats | None:
    """A header line, or one that prints neither the roll nor the turnout, as 'na 4 312 na 4 217 97.8 95 2.2'."""
    found = re.fullmatch(r"na\s+([\d ]+?)\s+na\s+([\d ]+?)\s+\d+\.\d\s+([\d ]+?)\s+\d+\.\d", " ".join(line.split()))
    if found:
        voted, formal, informal = (int(found[i].replace(" ", "")) for i in (1, 2, 3))
        return None, voted, formal, informal
    return early_header(line)


def council_elected(candidate: Candidate) -> bool:
    """Whether the history prints the surname in capitals, where a prefix like 'De' in 'DeGARIS' stays mixed."""
    words = candidate.name.split()
    surname = " ".join(words[:-1] if len(words) > 1 else words)
    letters = re.sub(r"[^A-Za-z]", "", re.sub(r"^(Mc|Mac|De|Le|O’|O')(?=[A-Z]{2})", "", surname))
    return len(letters) > 1 and letters.isupper()


STATE_FIRST, STATE_LAST = 163, 173  # the pages of the State-wide elections from 1975 to 2006


@dataclass
class Group:
    """A ballot group: its above-the-line or list votes, where it has any, and its candidates with their own votes."""

    letter: str
    party: str
    ticket: int | None
    members: list[tuple[Candidate, int | None]] = field(default_factory=list)
    ungrouped: bool = False


@dataclass
class StateCount:
    """A State-wide election of the Legislative Council, as one district."""

    day: date
    stats: Stats
    quota: int | None
    groups: list[Group]
    system: str
    files: list[str]
    note: str | None = None


@functools.cache
def state_pages() -> dict[int, str]:
    path = str(raw.cached(SOURCE, COUNCIL_HISTORY).path)
    out = subprocess.run(
        ["pdftotext", "-layout", "-f", str(STATE_FIRST), "-l", str(STATE_LAST), path, "-"],
        check=True, capture_output=True, text=True,
    ).stdout.split("\f")  # fmt: skip
    return {STATE_FIRST + i: text for i, text in enumerate(out[: STATE_LAST - STATE_FIRST + 1])}


def state_head(text: str, year: int) -> tuple[date, Stats, int | None]:
    heading = re.search(r"ELECTORATE RESULTS\s*–\s*(\d{1,2}) ([A-Z]+)", text)
    assert heading, year
    day = date(year, MONTHS.index(heading[2].title()) + 1, int(heading[1]))
    stats = next((found for line in text.splitlines() if (found := header(line.strip()))), None)
    if stats is None:
        raise ValueError(f"{year}: no State-wide header")
    quota = re.search(r"Quota\s+([\d ]+?)\s*$", text, re.MULTILINE)
    return day, stats, number(quota[1]) if quota else None


def list_groups(number_: int) -> list[Group]:
    """The party lists of 1975 and 1979: each group's line, '(H)' or 'ALP(A)', with its votes beside it or beside its
    only candidate, and the candidates listed below it in the same column."""
    document = pymupdf.open(str(raw.cached(SOURCE, COUNCIL_HISTORY).path))
    lines: list[Text] = []
    for block in document[number_ - 1].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                x0, y0, x1, y1 = line["bbox"]
                lines.append(Text(x0, y0, x1, y1, text))
    start = next(t.y0 for t in lines if t.text.startswith("Candidates")) + 2
    end = next(t.y0 for t in lines if t.text.startswith("*The prescribed"))
    lines = [t for t in lines if start < t.y0 < end]
    groups: list[tuple[Text, Group]] = []
    for t in sorted(lines, key=lambda t: (t.x0 > 290, t.y0)):
        found = re.fullmatch(r"([A-Z]+)? ?\(([A-Z])\)", t.text)
        column = t.x0 > 290
        if found:
            groups.append((t, Group(found[2], found[1] or "", None)))
            continue
        if not re.fullmatch(r"[A-Z][A-Za-z’' -]+ [A-Z]{1,4}\*?", t.text):
            continue
        owner = [g for head, g in groups if (head.x0 > 290) == column and head.y0 < t.y0]
        if not owner:
            raise ValueError(f"page {number_}: candidate {t.text!r} has no group")
        owner[-1].members.append((label_candidate(t.text, owner[-1].party), None))
    for head, group in groups:
        beside = [
            t for t in lines
            if (t.x0 > 290) == (head.x0 > 290) and t.x0 > head.x1 and re.fullmatch(r"\d{1,3}(?: \d{3})*", t.text)
            and (abs(t.y0 - head.y0) < 3 or (not group.ticket and abs(t.y0 - head.y0) < 14 and t.y0 > head.y0))
        ]  # fmt: skip
        if not beside:
            raise ValueError(f"page {number_}: no votes for group {group.letter}")
        group.ticket = number(min(beside, key=lambda t: (t.y0, t.x0)).text)
    return sorted((g for _, g in groups), key=lambda g: g.letter)


def stv_1982(text: str) -> list[Group]:
    groups: dict[str, Group] = {}
    body = text.split("Candidates", 1)[1]
    for line in body.splitlines():
        found = re.fullmatch(
            r"\s*(?P<name>\S.*?)\s{2,}(?:(?P<party>[A-Z]+) )?\((?P<letter>[A-Z])\)\s+(?P<votes>[\d ]+?)\s+[<\d.]+\s*",
            line,
        )
        if not found:
            continue
        group = groups.setdefault(found["letter"], Group(found["letter"], found["party"] or "", None))
        group.members.append((label_candidate(found["name"], found["party"] or ""), number(found["votes"])))
    return [groups[letter] for letter in sorted(groups)]


def numbers(fields: list[str]) -> list[str]:
    return [f for f in fields if re.fullmatch(r"<?[\d .]+", f)]


def stv_tickets(text: str, year: int) -> list[Group]:
    """The grouped and ungrouped candidates of 1985 to 2006: a group's first line gives its ticket votes and the
    first candidate's own votes, and each further line a candidate's own votes."""
    groups: list[Group] = []
    lettered = year >= 2002
    ungrouped = False
    body = text.split("Formal Vote Details", 1)[1]
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("Ungrouped candidates"):
            ungrouped = True
            continue
        fields = re.split(r"\s{2,}", stripped)
        if (
            len(fields) < 2
            or not re.match(r"[A-Z][A-Za-z’']", fields[0])
            or fields[0].startswith(("Total", "Candidate", "Grouped"))
        ):
            continue
        name = fields[0].replace(", ", " ")
        rest = fields[1:]
        codes = [f.rstrip(".") for f in rest if re.fullmatch(r"[A-Z0-9&]{1,6}\.?", f) and not f[0].isdigit()]
        values = [int(f.replace(" ", "")) for f in numbers(rest) if re.fullmatch(r"\d[\d ]*", f)]
        decimals = [i for i, f in enumerate(numbers(rest)) if not re.fullmatch(r"\d[\d ]*", f)]
        if not values:
            continue
        has_ticket = bool(decimals) and decimals[0] == 1
        if lettered and has_ticket:
            party, letter = (codes[0], codes[1]) if len(codes) == 2 else ("", codes[0])
        else:
            party, letter = (codes[0] if codes else ""), ""
        if party in ("IND",):
            party = ""
        whole = len(numbers(rest)) in (5, 6, 7) and not has_ticket  # an ungrouped candidate without ticket votes
        candidate = label_candidate(name, party)
        if has_ticket or whole or ungrouped:
            ticket = values[0] if has_ticket else None
            own = values[1] if has_ticket else values[0]
            groups.append(Group(letter, party, ticket, [(candidate, own)], ungrouped or whole))
        else:
            if not groups:
                raise ValueError(f"{year}: {name!r} has no group")
            groups[-1].members.append((candidate, values[0]))
    return groups


@functools.cache
def state_counts() -> dict[int, StateCount]:
    out: dict[int, StateCount] = {}
    texts: dict[int, list[tuple[int, str]]] = {}
    for page, text in state_pages().items():
        found = re.match(r"\s*(\d{4}) GENERAL ELECTION", text)
        assert found, page
        texts.setdefault(int(found[1]), []).append((page, text))
    for year, parts in texts.items():
        text = "\n".join(t for _, t in parts)
        day, stats, quota = state_head(text, year)
        if year < 1982:
            groups = [g for page, _ in parts for g in list_groups(page)]
            system = COUNCIL_LIST
        elif year == 1982:
            groups, system = stv_1982(text), COUNCIL_STV
        else:
            groups, system = stv_tickets(text, year), COUNCIL_STV
        out[year] = StateCount(day, stats, quota, groups, system, [COUNCIL_HISTORY])
    return out


# The commission's own Legislative Council results: State-wide first preferences by group for 2014 and 2018, and
# first preferences by division and polling place for 2022.

COUNCIL_2014 = (
    f"{HOST}/elections/past-state-election-results?view=article&id=661:2014-legislative-council-state-summary"
    "&catid=12:elections"
)
COUNCIL_2018 = f"{HOST}/html/results/2018/Legislative_Council.html"
COUNCIL_2022_PLACES = (
    f"{HOST}/component/edocman/110-2022se-legislative-council-first-preference-votes-by-polling-place-division"
    "/download?Itemid=0"
)
COUNCIL_2022_GROUPED = f"{HOST}/component/edocman/63-2022se-lc-grouped-candidates-1/download?Itemid=0"
COUNCIL_2022_UNGROUPED = f"{HOST}/component/edocman/63-2022se-lc-ungrouped-candidates-1/download?Itemid=0"
COUNCIL_2022_ELECTED = f"{HOST}/component/edocman/51-2022se-lc-order-of-election-of-members/download?Itemid=0"
# The party each group or summary line names, by its printed name in capitals.
GROUP_PARTIES = {
    "AUSTRALIAN LABOR PARTY": "ALP",
    "LIBERAL PARTY": "LIB",
    "THE GREENS": "GRN",
    "THE NATIONALS": "NAT",
    "FAMILY FIRST": "FFP",
    "FAMILY FIRST PARTY": "FFP",
    "PAULINE HANSON'S ONE NATION": "ONP",
    "SABEST": "SAB",
    "SA-BEST": "SAB",
    "NICK XENOPHON'S SA-BEST": "SAB",
    "ANIMAL JUSTICE PARTY": "AJP",
    "LIBERAL DEMOCRATS": "LDP",
    "LIBERAL DEMOCRATS LESS GOVERNMENT MORE FREEDOM": "LDP",
    "AUST FAMILY PARTY": "AFP",
    "ADVANCE SA": "ADV",
    "REAL CHANGE SA": "RCSA",
    "SA PARTY - STOP OVERDEVELOPMENT & CORRUPTION": "SAP",
    "LEGALISE CANNABIS SOUTH AUSTRALIA PARTY": "LCSA",
    "STOP POPULATION GROWTH NOW": "SPGN",
    "DIGNITY PARTY INC": "DPA",
    "DIGNITY FOR DISABILITY": "D4D",
    "CHILD PROTECTION PARTY": "CPP",
    "AUSTRALIAN CONSERVATIVES (SA)": "AC",
    "DANIG PARTY OF AUSTRALIA (SA DIVISION)": "DIG",
    "F.R.E.E. AUSTRALIA PARTY": "FREE",
    "FAIR LAND TAX - TAX PARTY": "FLT",
    "SOUTH AUSTRALIAN FISHING & LIFESTYLE PARTY": "SAFL",
    "KATTER'S SOUTH AUSTRALIAN PARTY": "KAP",
    "MULTICULTURAL PARTY": "MCP",
    "SHOOTERS AND FISHERS PARTY SA": "SFP",
}
# Names for the party codes the council's history and the commission use, beyond those of the House of Assembly.
COUNCIL_PARTIES = {
    "ABP": "Aboriginal Political Party",
    "AC": "Australian Conservatives",
    "ACLP": "Anti Communist Labor Party",
    "ACP": "Australian Conservative Party",
    "ADV": "Advance SA",
    "AFM": "Australian Family Movement",
    "AFR": "Australian Federal Republican Party",
    "AIA": "Australian Independent Alliance",
    "AP": "Australia Party",
    "ARF": "Australian Recreation and Fishing Party",
    "ASCP": "Australian Social Credit Party",
    "CLIC": "Community Leadership Independent Coalition",
    "COMB": "Combined Party",
    "CPP": "Child Protection Party",
    "CTA": "Call To Australia",
    "D4D": "Dignity for Disability",
    "DLP": "Democratic Labor Party",
    "DPA": "Dignity Party",
    "DES": "Democratic Socialist Party",
    "DSE": "Democratic Socialist Electoral League",
    "FAM": "Family First Party",
    "FEG": "Free Enterprise Group",
    "FLT": "Fair Land Tax - Tax Party",
    "FREE": "F.R.E.E. Australia Party",
    "GA": "Green Alliance",
    "GP": "Grey Power",
    "GRP": "Green Party SA",
    "HBP": "Happy Birthday Party",
    "KAP": "Katter's South Australian Party",
    "LCSA": "Legalise Cannabis South Australia Party",
    "LIBERT": "Libertarian Party",
    "LM": "Liberal Movement",
    "MAR": "Marijuana Party",
    "MCP": "Multicultural Party",
    "MLR": "Marijuana Law Reform",
    "NA": "National Action",
    "NATCP": "National Country Party",
    "NATS": "The Nationals",
    "NCP": "National Centre Party",
    "NDP": "Nuclear Disarmament Party",
    "NES": "No Emergency Services Levy Party",
    "NHH": "No Hoo Haa Party",
    "NLW": "Natural Law Party",
    "NND": "No Nuclear Dump in South Australia Party",
    "NPC": "No Pokies Campaign",
    "OMA": "Overtaxed Motorists Association",
    "OMD": "Overtaxed Motorists, Drinkers, Smokers Association",
    "OPP": "Overtaxed Pokies Party",
    "OSA": "Overtaxed Smokers Association",
    "PROG": "Progressive Labor Party",
    "RCSA": "Real Change SA",
    "SAF": "SA First",
    "SAFL": "South Australian Fishing & Lifestyle Party",
    "SAN": "SA Nuclear Free Future Party",
    "SAP": "SA Party - Stop Overdevelopment & Corruption",
    "SC": "Social Credit",
    "SCL": "Social Credit League",
    "SFP": "Shooters and Fishers Party SA",
    "SOCA": "Socialist Alliance",
    "SP": "Shooters Party",
    "SPA": "Socialist Party of Australia",
    "SPGN": "Stop Population Growth Now",
    "SRA": "Smokers Rights Association",
    "SRM": "Save the River Murray Party",
    "SSR": "Stormy Summers Reform Party",
    "UAP": "United Australia Party",
    "VIA": "Voters Independent Association",
    "WP": "Workers Party",
}


@dataclass
class Option:
    label: str
    kind: str  # candidate, legend (votes above the line for a group), or list
    party: str
    person: Candidate | None = None
    members: list[Candidate] = field(default_factory=list)  # a list's candidates in order


@dataclass
class Leaf:
    name: str
    votes: list[int | None]
    formal: int | None = None
    informal: int | None = None
    total: int | None = None
    kind: str = "standard"
    parent: str = ""  # the division a polling place is in


@dataclass
class CouncilContest:
    name: str
    seats: int
    system: str
    options: list[Option]
    declared: Leaf
    enrolled: int | None = None
    places: list[Leaf] = field(default_factory=list)
    winners: list[int] = field(default_factory=list)
    won: dict[int, int] = field(default_factory=dict)  # seats won by each list
    unopposed: bool = False
    after: list[tuple[str, int]] | None = None
    notes: list[str] = field(default_factory=list)
    scheme: str = "au-sa.council"
    code: str = ""


@dataclass
class CouncilEvent:
    day: date
    kind: str  # general or by_election
    files: list[str]
    contests: list[CouncilContest] = field(default_factory=list)
    last: date | None = None


DISTRICT_NAMES = {
    "province": "The Province",
    "central": "Central District",
    "central1": "Central District No. 1",
    "central2": "Central District No. 2",
    "northeastern": "North-Eastern District",
    "northern": "Northern District",
    "southern": "Southern District",
    "midland": "Midland District",
}


def council_district(printed: str) -> str:
    text = re.sub(r"^the|district|no(?=\d)", "", re.sub(r"[^a-z0-9]", "", printed.lower()))
    return DISTRICT_NAMES[text]


def candidate_options(candidates: list[Candidate]) -> list[Option]:
    return [Option(c.name, "candidate", c.party, c) for c in candidates]


def history_contest(block: Block, day: date) -> CouncilContest:
    name = council_district(block.title)
    candidates = [c for c, _ in block.first]
    winners = [i for i, c in enumerate(candidates) if council_elected(c)]
    if not winners:
        raise ValueError(f"{day} {name}: no candidate is elected")
    system = COUNCIL_BLOCK if day.year < 1930 else COUNCIL_PREFERENTIAL
    if block.stats is None:
        declared = Leaf(name, [None] * len(candidates))
        return CouncilContest(
            name, len(winners), system, candidate_options(candidates), declared, winners=winners, unopposed=True
        )
    enrolled, voted, formal, informal = block.stats
    declared = Leaf(name, [v for _, v in block.first], formal, informal, voted)
    contest = CouncilContest(
        name, len(winners), system, candidate_options(candidates), declared, enrolled=enrolled, winners=winners
    )
    if block.after:
        contest.after = [(c.name, v) for c, v in block.after]
    return contest


def state_contest(count: StateCount) -> CouncilContest:
    options: list[Option] = []
    votes: list[int | None] = []
    winners: list[int] = []
    won: dict[int, int] = {}
    for group in count.groups:
        party = "" if group.party in ("", "IND") else group.party
        if count.system == COUNCIL_LIST:
            members = [c for c, _ in group.members]
            options.append(
                Option(f"Group {group.letter}" + (f" ({party})" if party else ""), "list", party, None, members)
            )
            votes.append(group.ticket)
            seats = sum(1 for c in members if council_elected(c))
            if seats:
                won[len(options) - 1] = seats
            continue
        if group.ticket is not None:
            label = f"Group {group.letter}" if group.letter else f"Group of {group.members[0][0].display}"
            options.append(Option(f"{label}{f' ({party})' if party else ''} (above the line)", "legend", party))
            votes.append(group.ticket)
        for c, v in group.members:
            if council_elected(c):
                winners.append(len(options))
            options.append(Option(c.name, "candidate", c.party, c))
            votes.append(v)
    enrolled, voted, formal, informal = count.stats
    name = "State of South Australia"
    declared = Leaf(name, votes, formal, informal, voted)
    seats = len(winners) + sum(won.values())
    contest = CouncilContest(name, seats, count.system, options, declared, enrolled, winners=winners, won=won)
    if count.quota:
        contest.notes.append(f"The quota for election was {count.quota:,} votes.")
    return contest


def history_council_events() -> dict[str, Callable[[], CouncilEvent]]:
    out: dict[str, Callable[[], CouncilEvent]] = {}
    for year, section in council_sections().items():
        first = section.days[0]
        out[first.isoformat()] = functools.partial(history_general, year)
        for when in sorted({b.when for b in section.blocks if b.when}):
            out[f"{when.isoformat()}-by-election"] = functools.partial(history_by_election, year, when)
    for year, count in state_counts().items():
        out[count.day.isoformat()] = functools.partial(state_event, year)
    return out


def history_general(year: int) -> CouncilEvent:
    section = council_sections()[year]
    first, last = section.days[0], section.days[-1]
    event = CouncilEvent(first, "general", [COUNCIL_HISTORY], last=last if last != first else None)
    event.contests = [history_contest(b, first) for b in section.blocks if b.when is None]
    return event


def history_by_election(year: int, when: date) -> CouncilEvent:
    section = council_sections()[year]
    event = CouncilEvent(when, "by_election", [COUNCIL_HISTORY])
    for block in section.blocks:
        if block.when == when:
            contest = history_contest(block, when)
            note = section.notes.get((block.title, when))
            if note:
                contest.notes.append(note)
            event.contests.append(contest)
    return event


def state_event(year: int) -> CouncilEvent:
    count = state_counts()[year]
    return CouncilEvent(count.day, "general", count.files, [state_contest(count)])


def summary_rows(text: str) -> tuple[list[tuple[str, int]], dict[str, int]]:
    """The party rows of a State summary page, with their ballot papers, and its totals."""
    rows: list[tuple[str, int]] = []
    totals: dict[str, int] = {}
    text = re.sub(r"<table style=\"width: 100%; height: 1em;\">.*?</table>", "", text, flags=re.DOTALL)  # bar charts
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL):
        found = [c for c in cells(row) if c]
        if len(found) < 2:
            continue
        value = number(found[-1])
        if value is None:
            continue
        label = re.sub(r"\s+", " ", found[0]).strip()
        if label.startswith("Total"):
            totals[label.rstrip(":").strip()] = value
        elif label not in ("Party",):
            rows.append((label, value))
    return rows, totals


def summary_event(day: str, url: str) -> CouncilEvent:
    text = raw.cached(SOURCE, url).path.read_text()
    rows, totals = summary_rows(text)
    options = []
    for label, _ in rows:
        independent = label.startswith("Independent")
        party = "" if independent else GROUP_PARTIES[label.upper()]
        options.append(Option(label, "list", party))
    electors = re.search(r"Number of Electors:\s*</?[^>]*>?\s*([\d,]+)", text) or re.search(
        r"Number of Electors:\s*([\d,]+)", re.sub(r"<[^>]+>", " ", text)
    )
    name = "State of South Australia"
    declared = Leaf(
        name,
        [v for _, v in rows],
        totals["Total Formal"],
        totals["Total Informal"],
        totals["Total Ballot Papers"],
    )
    contest = CouncilContest(
        name, 11, COUNCIL_STV, options, declared, enrolled=number(electors[1]) if electors else None
    )
    contest.notes.append(
        "The commission's State summary gives the first preference votes of each group and of each ungrouped "
        "candidate's line, above and below the line together, and not those of each candidate. The members elected "
        "are not part of the summary, so no outcome is stored."
    )
    return CouncilEvent(date.fromisoformat(day), "general", [url], [contest])


def csv_text(url: str) -> str:
    data = raw.cached(SOURCE, url).path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def event_council_2022() -> CouncilEvent:
    grouped = list(csv.reader(io.StringIO(csv_text(COUNCIL_2022_GROUPED))))[2:]
    ungrouped = list(csv.reader(io.StringIO(csv_text(COUNCIL_2022_UNGROUPED))))[2:]
    people: dict[tuple[str, str], tuple[str, str]] = {}  # (group letter, 'SURNAME I.') to (given names, surname)
    order: dict[str, list[tuple[str, str]]] = {}
    group_names: dict[str, str] = {}
    for row in [*grouped, *[["", "UG", *r[2:]] for r in ungrouped]]:
        if len(row) < 5 or not row[3].strip():
            continue
        letter = row[1].strip() or "UG"
        surname, given = row[3].strip(), " ".join(p.strip() for p in row[4:6] if p.strip())
        short = f"{surname} {given[0]}."
        people[(letter, short.upper())] = (given, surname)
        order.setdefault(letter, []).append((given, surname))
        if row[0].strip():
            group_names[letter] = row[0].strip()
    elected_rows = list(csv.reader(io.StringIO(csv_text(COUNCIL_2022_ELECTED))))
    elected = {
        re.sub(r"\s+", " ", r[1].lstrip("*").strip()).upper() for r in elected_rows if r and re.fullmatch(r"\d+", r[0])
    }
    places: dict[tuple[str, str], dict[tuple[str, str], int]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, COUNCIL_2022_PLACES).path) as z:
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            if not info.filename.endswith(".csv"):
                continue
            for row in list(csv.reader(io.StringIO(z.read(info).decode("utf-8-sig"))))[3:]:
                if len(row) < 5 or not row[0].strip():
                    continue
                division, place, group, label, value = (c.strip() for c in row[:5])
                letter = group.split(".", 1)[0].strip()
                places.setdefault((division, place), {})[(letter, label)] = int(value.replace(",", ""))
    # Options in ballot order: each group's votes above the line, then its candidates; ungrouped candidates last.
    options: list[Option] = []
    keys: list[tuple[str, str]] = []
    winners: list[int] = []
    for letter in [*sorted((g for g in order if g != "UG"), key=lambda g: (len(g), g)), "UG"]:
        if letter not in order:
            continue
        name = group_names.get(letter, "")
        independent = letter == "UG" or name.upper().startswith("INDEPENDENT")
        party = "" if independent else GROUP_PARTIES[name.upper()]
        if letter != "UG":
            options.append(Option(f"Group {letter}: {name} (above the line)", "legend", party))
            keys.append((letter, "GROUP VOTES"))
        for given, surname in order[letter]:
            label = f"{surname} {given[0]}."
            person = Candidate(
                f"{surname}, {given}", party, display=f"{given} {title(surname) if surname.isupper() else surname}"
            )
            if f"{given} {surname}".upper() in elected:
                winners.append(len(options))
            options.append(Option(f"{surname}, {given}", "candidate", party, person))
            keys.append((letter, label.upper()))
    if len(winners) != 11:
        raise ValueError(f"2022: matched {len(winners)} of 11 members elected")
    leaves: dict[str, list[Leaf]] = {}
    for (division, place), values in places.items():
        upper = {(g, label.upper()): v for (g, label), v in values.items()}
        votes: list[int | None] = []
        for k in keys:
            if k not in upper:
                raise ValueError(f"2022 {division} {place}: no votes for {k}")
            votes.append(upper[k])
        formal = values[("Z", "Total Formal")]
        if sum(v or 0 for v in votes) != formal:
            raise ValueError(f"2022 {division} {place}: options add up to {sum(v or 0 for v in votes)}, not {formal}")
        declaration = place.startswith("Declaration")
        leaves.setdefault(division, []).append(
            Leaf(
                "Declaration votes" if declaration else place,
                votes,
                formal,
                values.get(("Z", "Total Informal")),
                values.get(("Z", "Total Votes")),
                "aggregate" if declaration else "standard",
                division,
            )
        )
    every = [leaf for division in sorted(leaves) for leaf in leaves[division]]
    declared = Leaf(
        "State of South Australia",
        [sum(leaf.votes[i] or 0 for leaf in every) for i in range(len(options))],
        sum(leaf.formal or 0 for leaf in every),
        sum(leaf.informal or 0 for leaf in every),
        sum(leaf.total or 0 for leaf in every),
    )
    contest = CouncilContest("State of South Australia", 11, COUNCIL_STV, options, declared, winners=winners)
    contest.places = every
    contest.notes.append(
        "Places are the polling places of each House of Assembly district, which the commission calls divisions "
        "for the Legislative Council, and one line of declaration votes for each division."
    )
    files = [COUNCIL_2022_PLACES, COUNCIL_2022_GROUPED, COUNCIL_2022_UNGROUPED, COUNCIL_2022_ELECTED]
    return CouncilEvent(date(2022, 3, 19), "general", files, [contest])


ASSETS = "https://result.ecsa.sa.gov.au/assets"
COUNCIL_ELECTED_2026 = f"{ASSETS}/Elected_Members_of_Parliament_2026.csv"
COUNCIL_SEATS_2026 = f"{ASSETS}/lc_quotas_seats_won_2026.csv"


def member_2026(cell: str, party: str) -> Candidate:
    """'* Mira EL DANNAWI' (a star marks an existing member) to a candidate."""
    incumbent = cell.startswith("*")
    words = cell.lstrip("*").split()
    split = next((i for i, w in enumerate(words) if w.replace("'", "").replace("-", "").isupper()), len(words))
    given, surname = " ".join(words[:split]), " ".join(words[split:])
    return Candidate(f"{surname}, {given}", party, incumbent, f"{given} {surname.title()}")


def event_council_2026() -> CouncilEvent:
    static, counts = api_json(COUNCIL_STATIC_2026), api_json(COUNCIL_COUNTS_2026)
    elected = list(csv.reader(io.StringIO(csv_text(COUNCIL_ELECTED_2026))))
    seats = {
        r[0].strip(): int(r[2])
        for r in csv.reader(io.StringIO(csv_text(COUNCIL_SEATS_2026)))
        if len(r) > 2 and re.fullmatch(r"\d+", r[2].strip())
    }
    votes = {p["id"]: p["votes"] for p in counts["parties"]}
    group = {p["id"]: p["groupId"] for p in counts["parties"]}
    options: list[Option] = []
    won: dict[int, int] = {}
    winners: list[int] = []
    for p in sorted(static["parties"], key=lambda p: group[p["id"]]):
        members = [
            member_2026(r[1], p["partyId"])
            for r in elected
            if len(r) > 3 and re.fullmatch(r"\d+", r[3].strip()) and r[2].strip() == p["partyName"]
        ]
        if seats.get(p["partyName"], 0) != len(members):
            raise ValueError(
                f"2026: {p['partyName']} won {seats.get(p['partyName'])} seats and has {len(members)} members"
            )
        label = f"Group {group[p['id']]}: {p['partyName']}"
        if members:
            won[len(options)] = len(members)
        options.append(Option(label, "list", p["partyId"], None, members))
    ids = [p["id"] for p in sorted(static["parties"], key=lambda p: group[p["id"]])]
    row: list[int | None] = [votes[i] for i in ids]
    formal, informal = counts["totalFormalVotes"], counts["totalInformalVotes"]
    if sum(v or 0 for v in row) != formal:
        raise ValueError("2026: group votes do not add up to the formal votes")
    declared = Leaf("State of South Australia", row, formal, informal, formal + informal)
    contest = CouncilContest("State of South Australia", static["lcSeatsContested"], COUNCIL_STV, options, declared)
    contest.won = won
    contest.winners = winners
    contest.notes.append(
        "The commission publishes the votes of each group, above and below the line together, and not the votes of "
        "individual candidates. Each list shows only the members it elected, in order of election. The commission's "
        "tables by division omit the votes of some pools, so only the statewide totals are stored."
    )
    return CouncilEvent(
        date(2026, 3, 21),
        "general",
        [COUNCIL_STATIC_2026, COUNCIL_COUNTS_2026, COUNCIL_ELECTED_2026, COUNCIL_SEATS_2026],
        [contest],
    )


@functools.cache
def council_events() -> dict[str, Callable[[], CouncilEvent]]:
    out: dict[str, Callable[[], CouncilEvent]] = {
        **history_council_events(),
        "2014-03-15": functools.partial(summary_event, "2014-03-15", COUNCIL_2014),
        "2018-03-17": functools.partial(summary_event, "2018-03-17", COUNCIL_2018),
        "2022-03-19": event_council_2022,
        "2026-03-21": event_council_2026,
    }
    return dict(sorted(out.items()))


@functools.cache
def council_event(name: str) -> CouncilEvent:
    return council_events()[name]()


COUNCIL_METHOD = {
    COUNCIL_BLOCK: (
        "Published in the history of South Australian elections, Legislative Council volume, by district. Each "
        "elector could vote for as many candidates as there were seats to fill, and the candidates with the most "
        "votes were elected, so the candidates' votes add up to more than the formal ballot papers where several "
        "seats were filled. Votes are stored as the history prints them for the district; its tables by polling "
        "place and by House of Assembly division are not stored here. The history prints the elected candidates' "
        "surnames in capitals, and the outcomes are stored as it prints them. Informal ballot papers are those that "
        "do not count, and valid votes are the formal ballot papers."
    ),
    COUNCIL_PREFERENTIAL: (
        "Published in the history of South Australian elections, Legislative Council volume, by district. Electors "
        "numbered the candidates in order of preference. Each seat was filled by its own count, in which a candidate "
        "with an absolute majority was elected, and the ballot papers of the candidates already elected passed on by "
        "their next preference to the count for the next seat. First preference votes are stored for each candidate, "
        "and the votes the history prints after the distribution of preferences are given in a note. The history "
        "prints the elected candidates' surnames in capitals, and the outcomes are stored as it prints them."
    ),
    COUNCIL_LIST: (
        "Published in the history of South Australian elections, Legislative Council volume, for the State as one "
        "district. Electors numbered the party lists, not candidates, and the votes are stored for each list. A list "
        "with less than half a quota was excluded and its votes passed on by second preference; seats went by full "
        "quotas and then by the largest remainders. The candidates of each list are stored in their printed order, "
        "and the history prints the elected candidates' surnames in capitals."
    ),
    COUNCIL_STV: (
        "The State is one district that elects eleven members by the single transferable vote. Votes for a group "
        "above the line, which adopt the group's order of preferences, are stored on one option for the group, and "
        "first preferences below the line on each candidate, so the options together hold every formal vote. The "
        "distribution of preferences is not stored; the outcomes are the members the source shows elected."
    ),
}


def council_key(name: str) -> str:
    return f"au.ecsa/council-{name}"


def joint(day: date) -> str | None:
    """The House of Assembly package whose general election fell on the same day, if the archive holds one."""
    name = day.isoformat()
    return name if name in events() and event(name).kind == "general" else None


def build_council(name: str) -> Package:
    ev = council_event(name)
    day = ev.day
    general = ev.kind == "general"
    shared = joint(day) if general else None
    depends = ["au.ecsa/registry", *([f"au.ecsa/{shared}"] if shared else [])]
    whole = general and day.year >= 1975
    p = Package(
        council_key(name),
        SOURCE,
        f"The South Australian Legislative Council {'election' if general else 'by-election'} of {day}",
        depends=depends,
    )
    p.external.add(AUSTRALIA)
    for url in ev.files:
        p.uses(raw.cached(SOURCE, url))
    election = key("election", day.isoformat())
    path = f"au-sa/{day}-state" if shared or whole else f"au-sa/{day}-council"
    if general and not shared:
        p.add(
            "election",
            id=election,
            polity_id=AUSTRALIA,
            kind="general",
            date_from=day,
            date_to=ev.last or day,
            status="held",
        )
        p.add("slug", path=path, entity_id=election, is_current=True)
        label = "state election" if whole else "Legislative Council election"
        p.name(election, f"{day.year} South Australian {label}", "en", "official")
    off: list[str] = []
    cast: list[str] = []
    for c in ev.contests:
        slug = changeset.slug(c.name)
        if not general:
            election = key("election", "council", day.isoformat(), slug)
            path = f"au-sa/{day}-council-by-election-{slug}"
            p.add(
                "election",
                id=election,
                polity_id=AUSTRALIA,
                kind="by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            p.add("slug", path=path, entity_id=election, is_current=True)
            p.name(election, f"{day.year} {c.name} Legislative Council by-election", "en", "official")
        tag = (day.isoformat(), "council", slug)
        contest = key("contest", *tag)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=COUNCIL,
            magnitude=c.seats,
            round_number=1,
            electoral_system_id=c.system,
            default_result_kind="tacit" if c.unopposed else "counted",
        )
        p.name(contest, f"{c.name}, Legislative Council", "en", "official")
        p.add("slug", path=f"{path}/{'council' if whole else f'council-{slug}'}", entity_id=contest, is_current=True)
        result_set = key("result_set", *tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="tacit" if c.unopposed else "counted",
            source_id=ECSA,
            status="active",
            is_default=True,
            completeness=None if c.unopposed else 1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=COUNCIL_METHOD[c.system],
            lang="en",
        )
        caveats = list(c.notes)
        if c.unopposed:
            caveats.append(
                "There was no poll: "
                + ("the only candidate" if c.seats == 1 else "as many candidates as members")
                + " stood, and the history lists the returned members without votes."
            )
        if not c.unopposed and any(v is None for v in c.declared.votes):
            caveats.append("The history does not print the votes of every candidate in this contest.")
        for index, caveat in enumerate(caveats):
            p.add(
                "note",
                id=key("note", result_set, "caveat", index),
                entity_id=result_set,
                kind="caveat",
                body_md=caveat,
                lang="en",
            )
        options: list[str] = []
        for position, option in enumerate(c.options, 1):
            option_id = key("option", *tag, position)
            options.append(option_id)
            party = party_id(option.party) if known_party(option.party) else None
            p.add(
                "contest_option",
                id=option_id,
                contest_id=contest,
                kind=option.kind,
                party_id=party,
                ballot_position=position,
                ballot_label=option.label,
            )
            people = [option.person] if option.person else option.members
            for rank, person in enumerate(people, 1):
                assert person is not None
                person_id = key("person", *tag, position, rank)
                p.add("person", id=person_id)
                p.name(person_id, person.display or person_name(person.name), "en", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", *tag, position, rank),
                    person_id=person_id,
                    option_id=option_id,
                    role="list_member" if option.kind == "list" else "head",
                    list_position=rank if option.kind == "list" else None,
                    ballot_name=person.name,
                    member_of_party_id=party,
                    is_independent=person.party in ("", "IND"),
                    is_incumbent=person.incumbent,
                )
        if c.unopposed:
            for index, option_id in enumerate(options):
                p.add("outcome", result_set_id=result_set, option_id=option_id, kind="elected")
            continue
        unit = key("unit", *tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRALIA,
            scheme_code="au-sa.council",
            code=f"{day}:{slug}",
            area_relation="unlocated",
            kind="aggregate" if c.places else "standard",
        )
        p.name(unit, c.name, "en", "official")
        if c.places:
            divisions: dict[str, str] = {}
            seen: Counter[str] = Counter()
            for leaf in c.places:
                if leaf.parent not in divisions:
                    division = divisions[leaf.parent] = key("unit", *tag, "division", leaf.parent)
                    p.add(
                        "reporting_unit",
                        id=division,
                        polity_id=AUSTRALIA,
                        scheme_code="au-sa.district",
                        code=f"{day}:council:{changeset.slug(leaf.parent)}",
                        parent_id=unit,
                        area_relation="unlocated",
                        kind="aggregate",
                    )
                    p.name(division, f"Division of {leaf.parent} (Legislative Council)", "en", "official")
                code = f"{day}:council:{changeset.slug(leaf.parent)}:{changeset.slug(leaf.name)}"
                seen[code] += 1
                code += f"-{seen[code]}" if seen[code] > 1 else ""
                place = key("unit", code)
                p.add(
                    "reporting_unit",
                    id=place,
                    polity_id=AUSTRALIA,
                    scheme_code="au-sa.place",
                    code=code,
                    parent_id=divisions[leaf.parent],
                    area_relation="unlocated",
                    kind=leaf.kind,
                )
                p.name(place, f"{leaf.name} ({leaf.parent}, Legislative Council)", "en", "official")
                for option_id, value in zip(options, leaf.votes, strict=True):
                    _put(p, result_set, place, option_id, VOTES, value)
                _put(p, result_set, place, None, VALID, leaf.formal)
                _put(p, result_set, place, None, INVALID, leaf.informal)
                _put(p, result_set, place, None, CAST, leaf.total)
        else:
            for option_id, value in zip(options, c.declared.votes, strict=True):
                _put(p, result_set, unit, option_id, VOTES, value)
            _put(p, result_set, unit, None, VALID, c.declared.formal)
            _put(p, result_set, unit, None, INVALID, c.declared.informal)
            _put(p, result_set, unit, None, CAST, c.declared.total)
        if c.enrolled is not None:
            roll = unit
            if c.places:
                roll = key("unit", *tag, "roll")
                p.add(
                    "reporting_unit",
                    id=roll,
                    polity_id=AUSTRALIA,
                    scheme_code="au-sa.council",
                    code=f"{day}:{slug}:roll",
                    parent_id=unit,
                    area_relation="unlocated",
                    kind="aggregate",
                )
                p.name(roll, f"{c.name}: electoral roll", "en", "official")
            _put(p, result_set, roll, None, REGISTERED, c.enrolled)
            p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=c.enrolled)
        total = c.declared
        for measure, value in ((CAST, total.total), (INVALID, total.informal), (VALID, total.formal)):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option_id, value in zip(options, total.votes, strict=True):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=option_id, measure=VOTES, value=value)
        if None not in (total.formal, total.informal, total.total) and (
            (total.formal or 0) + (total.informal or 0) != total.total
        ):
            cast.append(f"{c.name} ({total.total:,} against {(total.formal or 0) + (total.informal or 0):,})")
        if c.seats == 1 and total.formal is not None and sum(v or 0 for v in total.votes) != total.formal:
            off.append(f"{c.name} ({sum(v or 0 for v in total.votes):,} against {total.formal:,})")
        if c.after:
            listing = ", ".join(f"{label} {v:,}" for label, v in c.after)
            p.add(
                "note",
                id=key("note", result_set, "final"),
                entity_id=result_set,
                kind="context",
                body_md=f"After the distribution of preferences the history prints these votes: {listing}.",
                lang="en",
            )
        if c.winners or c.won:
            for index, option_id in enumerate(options):
                option = c.options[index]
                if option.kind == "list":
                    seats = c.won.get(index)
                    if seats:
                        p.add("outcome", result_set_id=result_set, option_id=option_id, kind="won", seats=seats)
                    else:
                        p.add("outcome", result_set_id=result_set, option_id=option_id, kind="lost")
                elif option.kind == "candidate":
                    kind = "elected" if index in c.winners else "lost"
                    p.add("outcome", result_set_id=result_set, option_id=option_id, kind=kind)
    if cast:
        p.accept(
            "accounting.ballots_sum",
            "The source prints a number of ballots cast that is not its formal plus its informal ballot papers in "
            + ", ".join(cast),
        )
    if off:
        p.accept(
            "accounting.votes_valid",
            "The source prints candidate votes that do not add up to its own formal votes in " + ", ".join(off),
        )
    return p


def council_packages() -> list[str]:
    return [council_key(name) for name in council_events()]


def council(p: Package) -> None:
    """The Legislative Council and the electoral systems that have filled it."""
    p.add("body", id=COUNCIL, polity_id=AUSTRALIA, kind="upper_house")
    p.name(COUNCIL, "South Australian Legislative Council", "en", "official")
    systems = [
        (
            COUNCIL_BLOCK,
            None,
            date(1929, 12, 31),
            {"formula": "block"},
            (
                "From 1857 to 1929 each elector could vote for as many candidates as there were seats to fill in the "
                "district, and the candidates with the most votes were elected. In 1857 electors deleted the names of "
                "the candidates they did not support; from 1861 they marked the candidates they supported. Until 1882 "
                "the whole Province was one district, and from 1882 there were four, then five, districts."
            ),
        ),
        (
            COUNCIL_PREFERENTIAL,
            date(1930, 1, 1),
            date(1974, 12, 31),
            {"formula": "irv"},
            (
                "From 1930 to 1973 electors numbered the candidates, and each seat in a district was filled by a "
                "preferential count of its own; the ballot papers of a candidate already elected passed on by their next "
                "preference to the count for the next seat. The rules stored here are those of one count; the engine "
                "does not recompute the repeated count."
            ),
        ),
        (
            COUNCIL_LIST,
            date(1975, 1, 1),
            date(1981, 12, 31),
            {"formula": "droop"},
            (
                "In 1975 and 1979 the State was one district of eleven members. Electors numbered party lists, a list "
                "with less than half a quota was excluded and its votes passed on by second preferences, and seats went "
                "by full quotas and then by the largest remainders."
            ),
        ),
        (
            COUNCIL_STV,
            date(1982, 1, 1),
            None,
            {"formula": "stv", "quota": "droop", "surplus": "gregory"},
            (
                "Since 1982 the State has been one district that elects eleven members by the single transferable vote "
                "with a Droop quota. From 1985 electors could vote above the line for a group, adopting its registered "
                "order of preferences, or number candidates below the line."
            ),
        ),
    ]
    for system, start, end, rules, body in systems:
        p.add(
            "electoral_system",
            id=system,
            polity_id=AUSTRALIA,
            valid_from=start,
            valid_to=end,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=body, lang="en")


# 2010 and 2014 and the by-elections from 2009 to 2015: the commission's results articles, one for each district's
# results and one for its polling places.

ARTICLE = f"{PAGE}?view=article&id="
WIKIPEDIA = key("source", "wikipedia")
SECTIONS = {
    "2010 State Election Results": ("2010-03-20", "general", ["2010_South_Australian_state_election"]),
    "2014 State Election Results": ("2014-03-15", "general", ["2014_South_Australian_state_election"]),
    "2015 By-election Results": ("2015-01-31", "by_election", ["2015_Davenport_state_by-election"]),
    "2014 By-election Results": ("2014-12-06", "by_election", ["2014_Fisher_state_by-election"]),
    "2012 By-election Results": (
        "2012-02-11",
        "by_election",
        ["2012_Port_Adelaide_state_by-election", "2012_Ramsay_state_by-election"],
    ),
    "2009 By-election Results": ("2009-01-17", "by_election", ["2009_Frome_state_by-election"]),
}
PAGE_KIND = re.compile(
    r"District of ([A-Za-z' -]+?) - (Results|Electoral Results|Polling place Results|Polling Booth Results)"
)


def wiki_url(title: str) -> str:
    return f"https://en.wikipedia.org/w/index.php?title={title}&action=raw"


def article_url(article: str | int) -> str:
    return f"{ARTICLE}{article}"


def article_body(text: str) -> str:
    found = re.search(r'<div itemprop="articleBody".*?<footer', text, re.DOTALL)
    return found[0] if found else text


def lines(cell: str) -> list[str]:
    cell = re.sub(r'<div class="progress">.*?</div>\s*</div>', "", cell, flags=re.DOTALL)
    parts = (
        " ".join(html.unescape(re.sub(r"<[^>]+>", " ", p)).replace("\xa0", " ").split())
        for p in re.split(r"<br\s*/?>", cell)
    )
    return [part for part in parts if part]


def grid(body: str) -> list[list[list[list[str]]]]:
    """Each table's rows, each row's cells, and each cell's lines."""
    return [
        [
            [lines(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.DOTALL)]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL)
        ]
        for table in re.findall(r"<table[^>]*>(.*?)</table>", body, re.DOTALL)
    ]


def flat(cell: list[str]) -> str:
    return " ".join(cell)


def apostrophe(text: str) -> str:
    return text.replace("`", "'")


def classify(text: str, district: str) -> str | None:
    """Whether an article holds a district's results ('main') or its polling places ('places'), or neither, as when
    the commission's link points to a page of another district or election."""
    body = article_body(text)
    found = PAGE_KIND.search(re.sub(r"<[^>]+>", "", body))
    if found:
        if found[1].lower() != district.lower():
            return None
        return "places" if "Polling" in found[2] else "main"
    if "Declaration Ballot Papers" in body:
        return "places"
    return "main" if "Total Formal" in body else None


def describe(text: str) -> tuple[str, str, str] | None:
    """The section title, district, and kind ('main' or 'places') an article holds, from its own headings."""
    body = article_body(text)
    plain = re.sub(r"<[^>]+>", " ", body)
    head = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.DOTALL)
    title = " ".join(lines(head[1])) if head else ""
    if not re.search(r"\b20\d\d\b", title):
        head = re.search(r"<title>(.*?)</title>", text, re.DOTALL)
        title = " ".join(lines(head[1])).rpartition("Electoral Commission - ")[2] if head else ""
    shown = PAGE_KIND.search(plain)
    year = re.search(r"\b(20\d\d)\b", title)
    if not year:
        return None
    sections = [k for k in SECTIONS if k.startswith(year[1])]
    if len(sections) > 1:
        sections = [k for k in sections if ("By-election" in k) == ("by-election" in title.lower())]
    if len(sections) != 1:
        return None
    section = sections[0]
    if shown:
        return section, shown[1], "places" if "Polling" in shown[2] else "main"
    named = re.search(r"- ([A-Za-z' -]+)$", title)
    if named and ("Total Formal" in body or "Declaration Ballot Papers" in body):
        return section, named[1].strip(), "places" if "Declaration Ballot Papers" in body else "main"
    return None


@functools.cache
def article_index() -> dict[str, dict[str, dict[str, str]]]:
    """Section title, then district, then 'main' and 'places' to the article url, from the headings of the articles
    themselves, because the commission's index links some districts to the articles of another district or
    election and leaves some articles unlinked. Two articles of one district and kind must hold the same tables."""
    found: dict[tuple[str, str, str], list[str]] = {}
    for number_ in range(656, 862):
        url = article_url(number_)
        try:
            text = raw.cached(SOURCE, url).path.read_text()
        except LookupError:
            continue
        info = describe(text)
        if info:
            found.setdefault(info, []).append(url)
    out: dict[str, dict[str, dict[str, str]]] = {}
    for (section, district, kind), urls in found.items():
        if len(urls) > 1:
            grids = {repr(grid(article_body(raw.cached(SOURCE, u).path.read_text()))) for u in urls}
            if len(grids) > 1:
                raise ValueError(f"{section} {district} {kind}: {len(urls)} articles with different tables")
        out.setdefault(section, {}).setdefault(district, {})[kind] = urls[0]
    return out


def fetch_articles() -> None:
    raw.fetch(SOURCE, article_url(655), suffix=".html")
    ids = range(656, 862)
    raw.fetch_many(SOURCE, [article_url(i) for i in ids], suffix=".html", workers=6)
    for _, _, titles in SECTIONS.values():
        for title in titles:
            raw.fetch(SOURCE, wiki_url(title), suffix=".wiki")


def modern_names(cell: list[str]) -> Candidate:
    """A heading cell of surname, given names, and party, on separate lines."""
    if len(cell) == 1:
        return Candidate(apostrophe(cell[0]), "")
    return Candidate(apostrophe(" ".join(cell[:-1])), cell[-1])


def votes_of(cells: list[list[str]]) -> list[int | None]:
    return [number(flat(c)) for c in cells]


def modern_district(name: str, main: str | None, places: str | None) -> District:
    """The layout of the 2014 articles and the by-elections after it."""
    first: list[list[list[str]]] = []
    runoff_rows: list[list[list[str]]] = []
    enrolled = None
    candidates: list[Candidate] = []
    declared: Place | None = None
    if main:
        body = article_body(main)
        found = re.search(r"Electors Enrolled\s+([\d,]+)", re.sub(r"<[^>]+>", " ", body))
        enrolled = number(found[1]) if found else None
        mt = grid(body)
        first = [r for r in mt[0] if len(r) == 5 or (r and flat(r[0]).startswith("Total"))]
        runoff_rows = [r for r in mt[1] if len(r) == 5 and number(flat(r[4])) is not None][:2] if len(mt) > 1 else []
        candidates = [
            Candidate(apostrophe(" ".join(r[0])), flat(r[1]))
            for r in first
            if len(r) == 5 and number(flat(r[4])) is not None
        ]
        formal, informal, total = (
            next((number(flat(r[-1])) for r in first if flat(r[0]).startswith(label)), None)
            for label in ("Total Formal", "Total Informal", "Total Ballot")
        )
        declared = Place(
            "District Total",
            [number(flat(r[4])) for r in first if len(r) == 5 and number(flat(r[4])) is not None],
            formal,
            informal,
            total,
        )
    leaves: list[Place] = []
    pair: list[list[str]] | None = None
    runoff_places: dict[str, list[int | None]] = {}
    if places:
        pt = grid(article_body(places))
        head = pt[0][0]
        stop = next(i for i, c in enumerate(head) if flat(c).startswith("Frml"))
        listed = [modern_names(c) for c in head[1:stop]]
        if candidates and [c.name for c in candidates] != [c.name for c in listed]:
            raise ValueError(f"{name}: the polling place table lists other candidates than the results")
        candidates = candidates or listed
        width = len(candidates)
        for row in pt[0][1:]:
            label = flat(row[0]) if row else ""
            if len(row) < width + 5 or not label or label.startswith(("District Total %", "Polling Place Totals")):
                continue
            place = Place(
                label,
                votes_of(row[1 : 1 + width]),
                number(flat(row[1 + width])),
                number(flat(row[2 + width])),
                number(flat(row[4 + width])),
            )
            if label == "District Total":
                if declared is None:
                    declared = place
                continue
            if label.startswith("Declaration"):
                place.name = "Declaration Votes"
                place.kind = "aggregate"
            leaves.append(place)
        if len(pt) > 1:
            pair = pt[1][0][1:]
            for row in pt[1][2:]:
                if len(row) == 5:
                    runoff_places[flat(row[0]).rstrip(":")] = [number(flat(row[1])), number(flat(row[3]))]
    assert declared is not None
    district = District(name, candidates, enrolled, leaves, declared)
    names = [c.name for c in candidates]
    if runoff_rows:
        finalists = [apostrophe(" ".join(r[0])) for r in runoff_rows]
        counts = [number(flat(r[4])) for r in runoff_rows]
    elif pair:
        finalists = [apostrophe(" ".join(c[:-1])) for c in pair]
        counts = runoff_places.get("District Total", [None, None])
    else:
        finalists, counts = [], []
    if finalists:
        district.finalists = [names.index(f) for f in finalists]
        declared.runoff = counts
        if places and pair and not runoff_rows:
            district.note = (
                "The commission's results page for this district is not published, so its figures come from "
                "the polling place page."
            )
    for place in leaves:
        if place.name in runoff_places and finalists:
            place.runoff = runoff_places[place.name]
        elif place.name == "Declaration Votes" and "Declaration Ballot Papers" in runoff_places and finalists:
            place.runoff = runoff_places["Declaration Ballot Papers"]
    return district


def legacy_candidate(cell: list[str]) -> Candidate:
    """A heading cell of 'SURNAME, Given' and the party code on the next line."""
    return Candidate(cell[0], cell[1] if len(cell) > 1 else "")


def legacy_numbers(row: list[list[str]]) -> list[int | None]:
    return [number(flat(c)) for c in row[1:] if flat(c)]


def legacy_district(name: str, main: str | None, places: str | None) -> District:
    """The layout of the 2009 to 2012 articles."""
    enrolled = None
    candidates: list[Candidate] = []
    declared: Place | None = None
    final: list[tuple[str, int | None]] = []
    updated = None
    provisional = False
    if main:
        body = article_body(main)
        plain = re.sub(r"<[^>]+>", " ", body)
        found = re.search(r"Electors Enrolled\s+([\d,]+)", plain)
        enrolled = number(found[1]) if found else None
        updated = re.search(r"Last updated on (\d{1,2} [A-Z][a-z]+ \d{4})", plain, re.IGNORECASE)
        provisional = "provisional and subject to recheck" in plain
        mt = [t for t in grid(body) if t and flat(t[0][0]) == "Candidate"]
        rows = [r for r in mt[0] if len(r) == 5 and number(flat(r[2])) is not None]
        candidates = [
            Candidate(r[0][0], flat(r[1]) if flat(r[1]) not in ("", "-") else "") for r in rows if flat(r[1]) != "-"
        ]
        stats = {flat(r[0]): number(flat(r[2])) for r in rows if flat(r[1]) == "-"}
        declared = Place(
            "District Total",
            [number(flat(r[2])) for r in rows if flat(r[1]) != "-"],
            stats.get("Total Formal"),
            stats.get("Informal"),
            stats.get("Total Ballot Papers"),
        )
        if len(mt) > 1:
            last = [r for r in mt[-1] if len(r) == 5 and number(flat(r[2])) is not None]
            final = [(r[0][0], number(flat(r[2]))) for r in last]
    leaves: list[Place] = []
    misprinted = 0
    if places:
        pt = grid(article_body(places))
        head = pt[0]
        listed = [legacy_candidate(c) for c in head[0] if c]
        width = len(listed)
        if candidates and [c.name for c in candidates] != [c.name for c in listed]:
            raise ValueError(f"{name}: the polling place table lists other candidates than the results")
        candidates = candidates or listed
        for row in head[2:]:
            label = flat(row[0]) if row else ""
            nums = legacy_numbers(row)
            if not label or label.startswith(("%age", "Polling Place Totals")):
                continue
            if len(nums) == 2 * width + 5:
                values = nums[0 : 2 * width : 2]
                tail = [nums[2 * width], nums[2 * width + 2], nums[2 * width + 4]]
            elif len(nums) == width + 3:
                values, tail = nums[:width], nums[width:]
            else:
                raise ValueError(f"{name}: unreadable row {label}: {nums}")
            place = Place(label, values, tail[0], tail[1], tail[2])
            if (
                label not in ("District Total", "Declaration Ballot Papers")
                and tail[0] == tail[1]
                and None not in (*values, tail[2])
                and sum(v or 0 for v in values) + (tail[1] or 0) == tail[2]
                and sum(v or 0 for v in values) != tail[0]
            ):
                place.formal = None  # the table prints the informal count in its formal column
                misprinted += 1
            if label == "District Total":
                if declared is None:
                    declared = place
                continue
            if label.startswith("Declaration"):
                place.name = "Declaration Votes"
                place.kind = "aggregate"
            leaves.append(place)
    assert declared is not None
    district = District(name, candidates, enrolled, leaves, declared)
    names = [c.name for c in candidates]
    notes: list[str] = []
    if final:
        district.finalists = [names.index(n) for n, _ in final]
        declared.runoff = [v for _, v in final]
    if not main:
        notes.append(
            "The commission's results page for this district is not published, so its figures come from the "
            "polling place page" + ", and the commission's final two candidate preferred count is not published."
        )
    elif updated and provisional:
        notes.append(
            f"The commission's results page was last updated on {updated[1]} and still describes its figures as "
            "provisional and subject to recheck until verification was complete."
        )
    district.misprinted = misprinted
    if misprinted:
        notes.append(
            f"The commission's polling place table prints the informal count in its formal column for {misprinted} "
            "of this district's polling places, so their formal votes are left out."
        )
    district.note = " ".join(notes) or None
    return district


@functools.cache
def article_events() -> dict[str, Callable[[], Event]]:
    """The elections of the commission's index of past results, by package name."""
    index = article_index()
    out: dict[str, Callable[[], Event]] = {}
    for section, (day, kind, _) in SECTIONS.items():
        name = day if kind == "general" else f"{day}-by-election"
        out[name] = functools.partial(article_event, section)
    return out if index else {}


def article_event(section: str) -> Event:
    day, kind, titles = SECTIONS[section]
    found = article_index()[section]
    ev = Event(day, kind, [article_url(655)], dated=[wiki_url(t) for t in titles])
    for pagename, urls in sorted(found.items()):
        main = raw.cached(SOURCE, urls["main"]).path.read_text() if "main" in urls else None
        places = raw.cached(SOURCE, urls["places"]).path.read_text() if "places" in urls else None
        ev.files += [urls[k] for k in ("main", "places") if k in urls]
        legacy = section.startswith(("2010", "2012", "2009"))
        district = (legacy_district if legacy else modern_district)(pagename, main, places)
        if places is None:
            gap = (
                "The commission publishes no polling place results for this district, so only its district total "
                "is stored."
            )
            district.note = f"{district.note} {gap}" if district.note else gap
        ev.districts.append(district)
    for title in titles:
        text = raw.cached(SOURCE, wiki_url(title)).path.read_text()
        when = date.fromisoformat(day)
        if f"{when.day} {MONTHS[when.month - 1]} {when.year}" not in text:
            raise ValueError(f"{title} does not give the polling day {day}")
    return ev
