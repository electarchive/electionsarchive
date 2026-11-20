"""Bangladesh Election Commission: pourashava (municipality) mayor and councillor elections.

The Commission posts a pourashava's results as the returning officer's consolidated statement of the presiding
officers' counts (Form ঠ under rule 42(6) of the pourashava election rules), one row per polling centre, on a page
of ecs.gov.bd. Two sets are posted there:

- the general election of Jhenaidah pourashava on 11 September 2022, as three PDFs: the mayor, the nine general
  ward councillors, and the three councillors for the seats reserved for women. Each statement ends with the
  returning officer's declaration of the candidate elected. The PDFs carry a text layer whose numbers read cleanly,
  but their legacy Bengali font drops the conjuncts and reorders the vowel signs of every name, so the names come
  from the Commission's candidate portal (`http://103.183.38.66/`), which lists each contest's final candidates,
  parties, and symbols as text. The statement prints each candidate with their symbol in its header, and the
  candidates are put in that order by finding each one's name and symbol there.
- the mayors elected on 9 March 2024 in six pourashavas, as one workbook each, with the candidates' names and
  symbols as text in its header. The candidate portal lists no candidates for that day, and the workbooks name no
  party.

The Commission's results portal (`http://103.183.38.66:81/`) lists the pourashava elections of 2019 to 2023 but
returns no result sheet or polling centre for any of them, and ecs.gov.bd posts no results for the 2011, 2015-16,
and 2020-21 rounds.
"""

import functools
import itertools
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlencode
from uuid import uuid5

import fastexcel
import polars as pl
import pymupdf

from ea import package, raw
from ea.package import Package

SOURCE = "bd.ecs_pourashava"
BANGLADESH = str(uuid5(package.NAMESPACE, "polity\x1fBD"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBGD"))
CANDIDATES = "http://103.183.38.66/api/"
SITE = "https://ecs.gov.bd/page/"
FILES = "https://asset.ecs.gov.bd/media/uploaded_files/package/filemanager/"
MUNICIPALITY_TYPE = 5
MAYOR, GENERAL, RESERVED = 11, 12, 13  # the portals' candidate types
FINAL = 11  # the candidate portal's status of a candidate allotted a symbol
INDEPENDENT = "স্বতন্ত্র"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
POURASHAVA, WARD, RESERVED_WARD, CENTRE = "bd.pourashava", "bd.ward", "bd.reserved_ward", "bd.polling_center"

JHENAIDAH_DAY = date(2022, 9, 11)
JHENAIDAH_PAGE = SITE + "jhenaidah-municipality-election-2022"
JHENAIDAH = {
    MAYOR: FILES + "2022/09/Jhinaidah Pourashova Election (Mayor) -11-09-22.pdf",
    GENERAL: FILES + "2022/09/Jhinaidah Pourashova Election (General Ward)-11-09-22.pdf",
    RESERVED: FILES + "2022/09/Jhinaidah Pourashova Election (Reserved Ward)-11-09-22.pdf",
}
DAY_2024 = date(2024, 3, 9)
PAGE_2024 = SITE + "municipality-election-2024"
# The six workbooks of 9 March 2024, by the pourashava's name as the file names it, with the candidate portal's
# district id.
WORKBOOKS_2024 = {"আমতলী": "04", "ত্রিশাল": "61", "পটুয়াখালী": "78", "বকশিগঞ্জ": "39", "মুন্সীগঞ্জ": "59", "শিবগঞ্জ": "10"}
# The candidate portal's election ids, and the districts whose lists the packages read.
ELECTIONS = {279: ["44"], 340: sorted(WORKBOOKS_2024.values())}
JHENAIDAH_ELECTION, JHENAIDAH_ZILLA, JHENAIDAH_CODE = 279, "44", "16"

# English names of the pourashavas and their districts, by the candidate portal's district and pourashava ids.
ENGLISH = {
    ("44", "16"): ("Jhenaidah", "Jhenaidah"),
    ("04", "20"): ("Amtali", "Barguna"),
    ("61", "88"): ("Trishal", "Mymensingh"),
    ("78", "75"): ("Patuakhali", "Patuakhali"),
    ("39", "11"): ("Bakshiganj", "Jamalpur"),
    ("59", "66"): ("Munshiganj", "Munshiganj"),
    ("10", "82"): ("Shibganj", "Bogura"),
}

DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
NUMBER = re.compile(r"^[০-৯]+(\.[০-৯]+)?$")
CENTRE_END = re.compile(r"\(([০-৯]+)\)\s*$")


@dataclass(frozen=True)
class Post:
    slug: str
    english: str
    bengali: str


POSTS = {
    MAYOR: Post("mayor", "mayor", "মেয়র"),
    GENERAL: Post("ward", "ward councillor", "সাধারণ ওয়ার্ড কাউন্সিলর"),
    RESERVED: Post("reserved-seat", "councillor for the seat reserved for women", "সংরক্ষিত ওয়ার্ড কাউন্সিলর"),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECS = key("source", "ecs")
FPTP = key("electoral_system", "fptp")


def url(base: str, endpoint: str, /, **params: object) -> str:
    return f"{base}{endpoint}?{urlencode({k: str(v) for k, v in params.items()})}"


def workbook_url(name: str) -> str:
    return f"{FILES}2024/03/akevuto_biboroni_{name} পৌরসভা.xlsx"


def candidates_url(election: int, zilla: str, post: int) -> str:
    return url(CANDIDATES, "candidates", electionID=election, zillaID=zilla, candidate_TypeID=post)


def municipalities_url(zilla: str) -> str:
    return url(CANDIDATES, "municipalities", zillaID=zilla)


def schedules_url() -> str:
    return url(CANDIDATES, "elections", election_typeID=MUNICIPALITY_TYPE)


def load(link: str) -> Any:
    return json.loads(raw.cached(SOURCE, link).path.read_bytes())


def fetch() -> None:
    raw.fetch(SOURCE, JHENAIDAH_PAGE, suffix=".html")
    raw.fetch(SOURCE, PAGE_2024, suffix=".html")
    for link in JHENAIDAH.values():
        raw.fetch(SOURCE, link, suffix=".pdf")
    for name in WORKBOOKS_2024:
        raw.fetch(SOURCE, workbook_url(name), suffix=".xlsx")
    raw.fetch(SOURCE, schedules_url(), suffix=".json")
    links = [url(CANDIDATES, "zillas", electionID=e) for e in ELECTIONS]
    links += [candidates_url(e, z, post) for e, zillas in ELECTIONS.items() for z in zillas for post in POSTS]
    links += [municipalities_url(z) for z in sorted({z for zillas in ELECTIONS.values() for z in zillas})]
    raw.fetch_many(SOURCE, links, suffix=".json", workers=2)


# ----------------------------------------------------------------------------------------------------------------
# The statements.


def clean(text: str) -> str:
    return " ".join(text.replace("‌", "").replace("‍", "").replace("﻿", "").split())


def bare(name: str) -> str:
    """A pourashava's name without the word পৌরসভা, with the long and short i read as one."""
    return clean(name).removesuffix("পৌরসভা").strip().replace("ী", "ি")


@dataclass(frozen=True)
class Place:
    zilla: str
    code: str  # the candidate portal's pourashava id, unique within its district
    bengali: str  # without the word পৌরসভা

    @property
    def tag(self) -> str:
        return f"{self.zilla}-{self.code}"

    @property
    def english(self) -> str:
        return ENGLISH[(self.zilla, self.code)][0]

    @property
    def district(self) -> str:
        return ENGLISH[(self.zilla, self.code)][1]


@dataclass(frozen=True)
class Candidate:
    ident: str  # the candidate portal's id, or the workbook column for 2024
    name: str
    party: str | None
    symbol: str


@dataclass
class Row:
    centre: int  # the centre's number within the pourashava
    name: str | None  # the centre's name, where the source prints it legibly
    registered: int
    votes: list[int]
    valid: int
    invalid: int
    cast: int


@dataclass
class Contest:
    day: date
    place: Place
    post: int
    seat: int | None  # the ward or reserved seat number
    candidates: list[Candidate]
    rows: list[Row]
    links: list[str]  # the statement first, then the candidate list
    winner: int | None  # the declared winner, by index into candidates

    @property
    def tag(self) -> tuple[object, ...]:
        return (self.day, self.place.tag, self.post, self.seat or 0)


def number(text: str) -> int:
    return int(text.translate(DIGITS))


@dataclass
class Sheet:
    seat: int | None
    header: str
    rows: list[tuple[int, list[int]]]
    total: list[int]
    declared: str


GLYPHS = re.compile(r"[-ঁ-ঃ়া-ৌ্ৗ]")


def statement_lines(path: Any) -> list[str]:
    """The statement's text lines without vowel signs, viramas, and the private-use glyphs its font maps conjuncts
    and some vowel signs to, which is how its words are compared."""
    lines: list[str] = []
    for page in pymupdf.open(path):
        lines += [GLYPHS.sub("", line).strip() for line in page.get_text().splitlines()]
    return [line for line in lines if line]


def sheets(path: Any) -> list[Sheet]:
    """Read a Form ঠ statement PDF: one sheet per contest, each row a centre's registered voters, the candidates'
    votes, and the valid, cancelled and cast votes (the turnout percentage that closes each row is dropped)."""
    found: list[Sheet] = []
    sheet: Sheet | None = None
    state, label, numbers, header = "skip", None, [], []
    for line in statement_lines(path):
        if "সরবরহকত গণনর" in line:  # the title of a new statement
            seat = re.search(r"(?:ন|আসন)-([০-৯]+)", line)
            sheet = Sheet(number(seat.group(1)) if seat else None, "", [], [], "")
            found.append(sheet)
            state = "title"
            continue
        if sheet is None:
            continue
        if line == "কর নম":  # the column heads, repeated on each page
            state, header = "header", []
            continue
        if state == "header":
            if line.endswith("হর(%)"):
                sheet.header = sheet.header or " ".join(header)
                state, label, numbers = "rows", None, []
            else:
                header.append(line)
            continue
        if state != "rows" or (line.isascii() and line.isdigit()):  # outside a table, or a page number
            continue
        if NUMBER.match(line):
            if label is None:  # a row's serial number printed on a line of its own
                continue
            numbers.append(line)
            if "." in line:
                values = [number(n) for n in numbers[:-1]]
                if label == "total":
                    sheet.total = values
                else:
                    sheet.rows.append((int(label), values))
                label, numbers = None, []
            continue
        if line == "সবমট":
            label, numbers = "total", []
        elif "ঘষণ কর" in line:
            sheet.declared = line
            state = "done"
        elif centre := CENTRE_END.search(line):
            label, numbers = str(number(centre.group(1))), []
    return found


def skeleton(text: str) -> str:
    """The letters of a Bengali text that survive the statements' legacy font: its consonants and independent
    vowels, without vowel signs or nuktas, and without the consonants of a conjunct, which the font draws as one
    private-use glyph."""
    chars = [ch for ch in unicodedata.normalize("NFD", text) if ch != "়"]
    keep = [
        ch
        for i, ch in enumerate(chars)
        if "অ" <= ch <= "হ" and not (i + 1 < len(chars) and chars[i + 1] == "্") and not (i > 0 and chars[i - 1] == "্")
    ]
    return "".join(keep)


def located(header: str, candidates: list[Candidate]) -> list[Candidate]:
    """The candidates in the order the statement's header prints them, found by their names and symbols."""
    text = skeleton(header)
    place: dict[str, float] = {}
    for c in candidates:
        target = skeleton(f"{c.name}{c.symbol}")
        blocks = [b for b in SequenceMatcher(None, text, target, autojunk=False).get_matching_blocks() if b.size >= 2]
        weight = sum(b.size for b in blocks)
        if weight < len(target) / 2:
            raise ValueError(f"{SOURCE}: {c.name} ({c.symbol}) not found in the header {header!r}")
        place[c.ident] = sum((b.a + b.size / 2) * b.size for b in blocks) / weight
    ordered = sorted(candidates, key=lambda c: place[c.ident])
    spots = [place[c.ident] for c in ordered]
    if any(b - a < 2 for a, b in itertools.pairwise(spots)):
        raise ValueError(f"{SOURCE}: candidates too close to order in the header {header!r}")
    return ordered


def declared(sheet: Sheet, candidates: list[Candidate]) -> int:
    """The candidate the returning officer declares elected, matched by name."""
    found = re.search(r"বগম\s*(.*?),\s*পত", sheet.declared)
    if not found:
        raise ValueError(f"{SOURCE}: no declaration in {sheet.declared!r}")
    text = skeleton(found.group(1))
    scores = [SequenceMatcher(None, text, skeleton(c.name), autojunk=False).ratio() for c in candidates]
    best = max(range(len(candidates)), key=lambda i: scores[i])
    if scores[best] < 0.6 or sorted(scores)[-2:][0] == scores[best] and len(scores) > 1:
        raise ValueError(f"{SOURCE}: the declared winner {found.group(1)!r} matches no one candidate: {scores}")
    return best


def checked_rows(sheet: Sheet, k: int, where: str) -> list[Row]:
    rows: list[Row] = []
    for centre, values in sheet.rows:
        if len(values) != k + 4:
            raise ValueError(f"{SOURCE}: {where} centre {centre} has {len(values)} figures for {k} candidates")
        registered, votes, (valid, invalid, cast) = values[0], values[1 : k + 1], values[k + 1 :]
        if sum(votes) != valid or valid + invalid != cast:
            raise ValueError(f"{SOURCE}: {where} centre {centre} does not add up: {values}")
        rows.append(Row(centre, None, registered, votes, valid, invalid, cast))
    sums = [sum(column) for column in zip(*(values for _, values in sheet.rows), strict=True)]
    if sums != sheet.total:
        raise ValueError(f"{SOURCE}: {where} rows add to {sums}, the statement prints {sheet.total}")
    return rows


def portal_candidates(election: int, zilla: str, post: int, code: str) -> dict[int, list[Candidate]]:
    """The final candidates of a pourashava by seat (0 for the mayor), from the candidate portal, whose `unionID`
    carries the ward or reserved seat number for councillors."""
    out: dict[int, list[Candidate]] = {}
    for row in load(candidates_url(election, zilla, post))["candidates"]:
        if str(row["municipalityID"]) != code or row["nomination_status"] != FINAL:
            continue
        party = clean(row["party_name"] or "") or None
        seat = 0 if post == MAYOR else int(row["unionID"])
        out.setdefault(seat, []).append(
            Candidate(str(row["id"]), clean(row["candidate_name"]), party, clean(row["symbolbn"]))
        )
    return out


def place_of(zilla: str, name: str) -> Place:
    matches = [
        m for m in load(municipalities_url(zilla))["municipalities"] if bare(m["municipality_name"]) == bare(name)
    ]
    if len(matches) != 1:
        raise ValueError(f"{SOURCE}: {name} matches {len(matches)} pourashavas of district {zilla}")
    return Place(
        zilla, str(matches[0]["municipalityID"]), clean(matches[0]["municipality_name"]).removesuffix("পৌরসভা").strip()
    )


def jhenaidah() -> list[Contest]:
    place = place_of(JHENAIDAH_ZILLA, "ঝিনাইদহ")
    assert place.code == JHENAIDAH_CODE
    contests: list[Contest] = []
    registered: dict[int, int] = {}
    for post, link in JHENAIDAH.items():
        roster = portal_candidates(JHENAIDAH_ELECTION, JHENAIDAH_ZILLA, post, place.code)
        found = sheets(raw.cached(SOURCE, link).path)
        if sorted(s.seat or 0 for s in found) != sorted(roster):
            raise ValueError(f"{SOURCE}: Jhenaidah {post} statements {[s.seat for s in found]} vs {sorted(roster)}")
        for sheet in found:
            seat = sheet.seat or 0
            where = f"Jhenaidah {POSTS[post].slug} {seat}"
            candidates = located(sheet.header, roster[seat])
            rows = checked_rows(sheet, len(candidates), where)
            for row in rows:
                if registered.setdefault(row.centre, row.registered) != row.registered:
                    raise ValueError(f"{SOURCE}: {where} centre {row.centre} registered {row.registered}")
            winner = declared(sheet, candidates)
            totals = [sum(r.votes[i] for r in rows) for i in range(len(candidates))]
            if totals[winner] != max(totals) or totals.count(max(totals)) > 1:
                raise ValueError(f"{SOURCE}: {where} declares {candidates[winner].name}, not the leader")
            contests.append(
                Contest(
                    JHENAIDAH_DAY,
                    place,
                    post,
                    sheet.seat,
                    candidates,
                    rows,
                    [link, candidates_url(JHENAIDAH_ELECTION, JHENAIDAH_ZILLA, post)],
                    winner,
                )
            )
    return contests


SYMBOLED = re.compile(r"^(.*)\(([^()]*)\)\s*$", re.DOTALL)


def workbook(name: str, zilla: str) -> Contest:
    link = workbook_url(name)
    frame = fastexcel.read_excel(str(raw.cached(SOURCE, link).path)).load_sheet(0, header_row=None).to_polars()
    table = [[clean(str(c)) if c is not None else "" for c in row] for row in frame.iter_rows()]
    head = table[0]
    if head[:5] != ["ক্রম", "জেলা", "পৌরসভা", "কেন্দ্রের নাম", "ভোটার সংখ্যা"] or head[-4:] != [
        "বৈধ ভোট",
        "বাতিলকৃত ভোট",
        "প্রদত্ত ভোট",
        "শতকরা হার(%)",
    ]:
        raise ValueError(f"{SOURCE}: unexpected columns in {name}: {head}")
    candidates: list[Candidate] = []
    for column, text in enumerate(head[5:-4], start=5):
        parts = SYMBOLED.match(text)
        if not parts:
            raise ValueError(f"{SOURCE}: no symbol in {text!r}")
        candidates.append(Candidate(f"{name}:{column}", clean(parts.group(1)), None, clean(parts.group(2))))
    k = len(candidates)
    rows: dict[int, Row] = {}
    places = set()
    for cells in table[1:]:
        if not any(cells):
            continue
        places.add(cells[2])
        centre = int(cells[0])
        values = [int(c) if c else 0 for c in cells[4 : 5 + k + 3]]
        registered, votes, (valid, invalid, cast) = values[0], values[1 : k + 1], values[k + 1 :]
        label = re.sub(r"\s*-?\s*\(\d+\)\s*$", "", cells[3])
        if not cells[3].rstrip().endswith(f"({centre})"):
            raise ValueError(f"{SOURCE}: {name} row {centre} names centre {cells[3]!r}")
        row = Row(centre, label, registered, votes, valid, invalid, cast)
        if centre in rows:
            if rows[centre] != row:  # a few workbooks repeat their last row
                raise ValueError(f"{SOURCE}: {name} centre {centre} appears twice with different figures")
            continue
        if sum(votes) != valid or valid + invalid != cast:
            raise ValueError(f"{SOURCE}: {name} centre {centre} does not add up: {values}")
        rows[centre] = row
    if sorted(rows) != list(range(1, len(rows) + 1)) or len(places) != 1:
        raise ValueError(f"{SOURCE}: {name} centres {sorted(rows)} in {places}")
    return Contest(DAY_2024, place_of(zilla, places.pop()), MAYOR, None, candidates, list(rows.values()), [link], None)


@functools.cache
def contests() -> tuple[Contest, ...]:
    return (*jhenaidah(), *(workbook(name, zilla) for name, zilla in WORKBOOKS_2024.items()))


# ----------------------------------------------------------------------------------------------------------------
# Packages.

DAYS = {JHENAIDAH_DAY: "Jhenaidah pourashava general election", DAY_2024: "pourashava mayoral elections"}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def parse() -> None:
    held = contests()
    print(geo(held).write())
    print(registry(held).write())
    for day in DAYS:
        print(election(day, [c for c in held if c.day == day]).write())


def emit() -> None:
    package.run("load", packages())


def slugify(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower().replace("'", "")).strip("-")


def pourashava_area(place: Place) -> str:
    return key("area", POURASHAVA, place.tag)


def seat_area(place: Place, post: int, seat: int) -> str:
    return key("area", WARD if post == GENERAL else RESERVED_WARD, place.tag, seat)


def polity(place: Place) -> str:
    return key("polity", place.tag)


def centre_unit(place: Place, centre: int) -> str:
    return key("unit", CENTRE, place.tag, centre)


def wards_of(held: tuple[Contest, ...]) -> dict[tuple[str, int], int]:
    """Each centre's ward, from the general ward statements that list it."""
    seen: dict[tuple[str, int], set[int]] = {}
    for c in held:
        if c.post == GENERAL and c.seat is not None:
            for row in c.rows:
                seen.setdefault((c.place.tag, row.centre), set()).add(c.seat)
    return {centre: wards.pop() for centre, wards in seen.items() if len(wards) == 1}


def geo(held: tuple[Contest, ...]) -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The pourashavas whose mayor or councillor results the Election Commission posts, with their wards, the "
        "wards of the seats reserved for women, and the polling centres of each election",
    )
    p.external.update({BANGLADESH, COUNTRY})
    for link in [municipalities_url(z) for z in sorted({c.place.zilla for c in held})]:
        p.uses(raw.cached(SOURCE, link))
    p.add(
        "scheme", code=POURASHAVA, polity_id=BANGLADESH, kind="administrative", rank=4, noun="pourashava",
        code_system="ecs",
    )  # fmt: skip
    p.add("scheme", code=WARD, polity_id=BANGLADESH, kind="administrative", rank=5, noun="ward", code_system="ecs")
    p.add(
        "scheme", code=RESERVED_WARD, polity_id=BANGLADESH, kind="electoral_district", rank=5,
        noun="reserved seat ward", code_system="ecs",
    )  # fmt: skip
    p.add("scheme", code=CENTRE, polity_id=BANGLADESH, kind="precinct", rank=4, noun="polling centre")
    places = sorted({c.place for c in held}, key=lambda place: place.tag)
    for place in places:
        first = min(c.day for c in held if c.place == place)
        area = pourashava_area(place)
        p.add("area", id=area, scheme_code=POURASHAVA)
        p.add("area_code", area_id=area, code=place.tag, valid_from=first)
        p.add("area_parent", area_id=area, parent_id=COUNTRY, valid_from=first)
        p.name(area, f"{place.english} Pourashava", "en", "official")
        p.name(area, f"{place.bengali} পৌরসভা", "bn", "official", False)
        p.add("polity", id=polity(place), kind="municipality", iso_code=None)
        p.add("polity_parent", polity_id=polity(place), parent_id=BANGLADESH, valid_from=first)
        p.add("polity_territory", polity_id=polity(place), area_id=area, valid_from=first)
        p.name(polity(place), f"{place.english} Pourashava", "en", "official")
        p.name(polity(place), f"{place.bengali} পৌরসভা", "bn", "official", False)
    for c in held:
        if c.seat is None:
            continue
        area = seat_area(c.place, c.post, c.seat)
        p.add("area", id=area, scheme_code=WARD if c.post == GENERAL else RESERVED_WARD)
        p.add("area_code", area_id=area, code=f"{c.place.tag}-{c.seat}", valid_from=c.day)
        p.add("area_parent", area_id=area, parent_id=pourashava_area(c.place), valid_from=c.day)
        if c.post == GENERAL:
            p.name(area, f"{c.place.english} Pourashava Ward {c.seat}", "en", "official")
            p.name(area, f"{c.place.bengali} পৌরসভা {c.seat:02d} নং ওয়ার্ড".translate(BENGALI), "bn", "official", False)
        else:
            p.name(area, f"{c.place.english} Pourashava Reserved Seat {c.seat}", "en", "official")
            p.name(
                area, f"{c.place.bengali} পৌরসভা সংরক্ষিত ওয়ার্ড {c.seat:02d}".translate(BENGALI), "bn", "official", False
            )
    wards = wards_of(held)
    seen: set[str] = set()
    for c in held:
        for row in c.rows:
            unit = centre_unit(c.place, row.centre)
            if unit in seen:
                continue
            seen.add(unit)
            ward = wards.get((c.place.tag, row.centre))
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=BANGLADESH,
                scheme_code=CENTRE,
                code=f"{c.place.tag}-{row.centre}",
                area_relation="within",
                area_id=seat_area(c.place, GENERAL, ward) if ward else pourashava_area(c.place),
                kind="standard",
                valid_from=c.day,
            )
            p.name(unit, f"{c.place.english} Pourashava polling centre {row.centre}", "en", "official")
            serial = str(row.centre).translate(BENGALI)
            label = f"{serial}. {row.name}" if row.name else f"{c.place.bengali} পৌরসভা ভোটকেন্দ্র {serial}"
            p.name(unit, label, "bn", "official", False)
    return p


BENGALI = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def party_id(name: str) -> str:
    return key("party", name)


def body_id(place: Place) -> str:
    return key("body", place.tag)


def mayor_office(place: Place) -> str:
    return key("office", place.tag, MAYOR)


def election_id(day: date) -> str:
    return key("election", day)


def registry(held: tuple[Contest, ...]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Election Commission's pourashava results, the councils and mayors of those pourashavas, the parties "
        "their candidates stood for, the plurality system that elects them, and the elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BANGLADESH)
    p.add(
        "source",
        id=ECS,
        kind="official_body",
        publisher="Bangladesh Election Commission",
        title="Pourashava election results (consolidated statements of the polling centres' counts) and the "
        "candidate information portal",
        url="https://ecs.gov.bd/",
        license="No license stated; published by the electoral authority for public use",
        attribution="Bangladesh Election Commission",
    )
    p.artifact(raw.cached(SOURCE, schedules_url()), ECS)
    p.add("electoral_system", id=FPTP, polity_id=BANGLADESH, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="Under the Local Government (Pourashava) Act 2009 each pourashava elects its mayor, one councillor "
        "for each ward, and one woman councillor for each seat reserved for women (a group of about three wards), "
        "each on a separate ballot; on each, the candidate with the most votes is elected.",
        lang="en",
    )
    parties = sorted({c.party for x in held for c in x.candidates if c.party and c.party != INDEPENDENT})
    for name in parties:
        p.add("party", id=party_id(name), kind="party", polity_id=BANGLADESH)
        p.name(party_id(name), name, "bn", "official")
    for place in sorted({c.place for c in held}, key=lambda place: place.tag):
        body = body_id(place)
        p.add("body", id=body, polity_id=polity(place), kind="council")
        p.name(body, f"{place.english} Pourashava", "en", "official")
        p.name(body, f"{place.bengali} পৌরসভা", "bn", "official", False)
        office = mayor_office(place)
        p.add("office", id=office, polity_id=polity(place), body_id=body, kind="mayor")
        p.name(office, f"Mayor of {place.english} Pourashava", "en", "official")
        p.name(office, f"{place.bengali} পৌরসভার মেয়র", "bn", "official", False)
    for day, title in DAYS.items():
        e = election_id(day)
        p.add("election", id=e, polity_id=BANGLADESH, kind="local", date_from=day, date_to=day, status="held")
        p.add("slug", path=f"bd/{day}-pourashava", entity_id=e, is_current=True)
        p.name(e, f"{day:%-d %B %Y} {title}", "en", "official")
    return p


METHOD = {
    JHENAIDAH_DAY: "From the returning officer's consolidated statement (Form ঠ) of the presiding officers' counts, "
    "posted as a PDF by the Bangladesh Election Commission: each polling centre's registered voters, each "
    "candidate's votes, and the valid, cancelled and cast votes. Each centre's figures add up, and their sums "
    "equal the statement's printed totals. The PDF's font garbles the names, so the candidates' names, parties "
    "and symbols come from the Commission's candidate portal, and each was placed in the statement's column that "
    "prints their name and symbol. The winner is the candidate the returning officer declares elected at the foot "
    "of the statement.",
    DAY_2024: "From the consolidated statement of the polling centres' counts, posted as a workbook by the "
    "Bangladesh Election Commission: each polling centre's registered voters, each candidate's votes, and the "
    "valid, cancelled and cast votes; a blank cancelled count is read as zero, and a last row the workbook repeats "
    "is read once. Each centre's figures add up. The workbook names each candidate with their symbol and no party, "
    "and declares no winner, so none is stored.",
}


def election(day: date, held: list[Contest]) -> Package:
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {DAYS[day]} of {day:%-d %B %Y} in Bangladesh: "
        + (
            "the mayor, the nine ward councillors, and the three councillors for seats reserved for women"
            if day == JHENAIDAH_DAY
            else f"the mayors of {len(held)} pourashavas"
        )
        + ", by polling centre, from the Election Commission's consolidated statements",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(BANGLADESH)
    root = f"bd/{day}-pourashava"
    artifacts: dict[str, str] = {}

    def cite(link: str) -> str:
        if link not in artifacts:
            artifacts[link] = p.artifact(raw.cached(SOURCE, link), ECS)
        return artifacts[link]

    cite(JHENAIDAH_PAGE if day == JHENAIDAH_DAY else PAGE_2024)
    values: list[tuple[str, str, str | None, int, int]] = []
    for c in held:
        for link in c.links:
            cite(link)
        post = POSTS[c.post]
        place = c.place
        contest = key("contest", *c.tag)
        year = f"{day:%Y}".translate(BENGALI)
        if c.post == MAYOR:
            p.add(
                "contest", id=contest, election_id=election_id(day), kind="office", office_id=mayor_office(place),
                district_area_id=pourashava_area(place), magnitude=1, round_number=1, electoral_system_id=FPTP,
                default_result_kind="counted",
            )  # fmt: skip
            english = f"{place.english} Pourashava mayoral election, {day:%Y}"
            bengali = f"{place.bengali} পৌরসভা মেয়র নির্বাচন {year}"
            path = f"{root}/{slugify(place.english)}/mayor"
        else:
            assert c.seat is not None
            p.add(
                "contest", id=contest, election_id=election_id(day), kind="body_seats", body_id=body_id(place),
                district_area_id=seat_area(place, c.post, c.seat), magnitude=1, round_number=1,
                electoral_system_id=FPTP, default_result_kind="counted",
            )  # fmt: skip
            seat = f"{c.seat:02d}".translate(BENGALI)
            if c.post == GENERAL:
                english = f"{place.english} Pourashava Ward {c.seat} councillor election, {day:%Y}"
                bengali = f"{place.bengali} পৌরসভা {seat} নং ওয়ার্ড কাউন্সিলর নির্বাচন {year}"
            else:
                english = f"{place.english} Pourashava Reserved Seat {c.seat} councillor election, {day:%Y}"
                bengali = f"{place.bengali} পৌরসভা সংরক্ষিত মহিলা আসন {seat} কাউন্সিলর নির্বাচন {year}"
            path = f"{root}/{slugify(place.english)}/{post.slug}-{c.seat}"
        p.name(contest, english, "en", "official")
        p.name(contest, bengali, "bn", "official", False)
        p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = key("result_set", *c.tag)
        p.add(
            "result_set", id=result_set, election_id=election_id(day), contest_id=contest, stage="official",
            kind="counted", source_id=ECS, status="active", is_default=True, completeness=1.0,
        )  # fmt: skip
        p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD[day],
            lang="en",
        )  # fmt: skip
        options = []
        for position, candidate in enumerate(c.candidates, start=1):
            option = key("option", *c.tag, candidate.ident)
            person = key("person", *c.tag, candidate.ident)
            party = None if candidate.party in (None, INDEPENDENT) else party_id(candidate.party)
            p.add(
                "contest_option", id=option, contest_id=contest, kind="candidate", party_id=party,
                ballot_position=position, ballot_label=f"{candidate.name} ({candidate.symbol})",
            )  # fmt: skip
            p.add("person", id=person)
            p.name(person, candidate.name, "bn", "official")
            p.add(
                "candidacy", id=key("candidacy", *c.tag, candidate.ident), person_id=person, option_id=option,
                role="head",
                ballot_name=candidate.name, member_of_party_id=party,
                is_independent=True if candidate.party == INDEPENDENT else (False if party else None),
            )  # fmt: skip
            if candidate.ident.isdigit():
                p.add("external_id", entity_id=person, scheme=f"{SOURCE}.candidate", value=candidate.ident)
            options.append(option)
        if c.winner is not None:
            p.add("outcome", result_set_id=result_set, option_id=options[c.winner], kind="elected")
        for row in c.rows:
            unit = centre_unit(place, row.centre)
            for option, votes in zip(options, row.votes, strict=True):
                values.append((result_set, unit, option, VOTES, votes))
            values += [
                (result_set, unit, None, REGISTERED, row.registered),
                (result_set, unit, None, VALID, row.valid),
                (result_set, unit, None, INVALID, row.invalid),
                (result_set, unit, None, CAST, row.cast),
            ]
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    return p
