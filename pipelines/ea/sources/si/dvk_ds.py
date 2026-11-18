"""The Slovenian National Council (Državni svet): the electors' votes for each candidate, 1997-2024.

The National Council has 40 members. Twenty-two represent local interests, one for each of 22 electoral units that
group municipalities; the municipal councils choose electors, who vote for one candidate at an electoral assembly of
the unit. Eighteen represent functional interests (employers, employees, farmers, craftsmen and independent
professions, and the non-commercial activities), and each group's organizations choose electors who vote for as many
candidates as the group has seats. The State Election Commission (Državna volilna komisija) publishes a report of
each election as a PDF, with the electors on the roll, the ballots cast, and the votes of every candidate in each
contest. Most of the older reports are scans; `dvk_ds_transcribe` turns them into the lines this module parses.
"""

import difflib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry
import shapely.ops

from ea import package, raw
from ea.package import Package

SOURCE = "si.dvk"
SLOVENIA = str(uuid5(package.NAMESPACE, "polity\x1fSI"))
BASE = "https://www.dvk-rs.si/fileadmin/user_upload"
ARCHIVE_PAGE = "https://www.dvk-rs.si/volitve-in-referendumi/drzavni-svet-rs/arhiv-volitev-drzavnega-sveta-rs/"
GEOBOUNDARIES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/SVN/ADM2/"
    "geoBoundaries-SVN-ADM2_simplified.geojson"
)
LINES = Path(__file__).with_name("dvk_ds_lines.json")
UNIT = "si.ds_unit"
UNITS = range(1, 23)
GEO_KEY = f"{SOURCE}/ds-geo"
REGISTRY_KEY = f"{SOURCE}/ds-registry"

# The interest groups, in the order the reports print them: code, English name, native name, seats, keyword in the
# reports' headings.
GROUPS = [
    ("employers", "Employers", "Delodajalci", 4, "delodajalc"),
    ("employees", "Employees", "Delojemalci", 4, "delojemalc"),
    ("farmers", "Farmers", "Kmetje", 2, "kmet"),
    ("craftsmen", "Craftsmen", "Obrtniki", 1, "obrtnik"),
    ("professions", "Independent professions", "Samostojni poklici", 1, "samostojn"),
    ("universities", "Universities and higher education", "Univerze, visoke in višje šole", 1, "univerz"),
    ("education", "Education and training", "Vzgoja in izobraževanje", 1, "vzgoj"),
    ("research", "Research", "Raziskovalna dejavnost", 1, "raziskoval"),
    ("culture", "Culture and sport", "Kultura in šport", 1, "kultur"),
    ("health", "Health", "Zdravstvo", 1, "zdravstv"),
    ("welfare", "Social welfare", "Socialno varstvo", 1, "socialn"),
]
GROUP_SEATS = {code: seats for code, _, _, seats, _ in GROUPS}
TITLES = {"dr", "mag", "prof", "red", "doc", "izr", "ing", "dipl", "univ", "l", "r", "roj"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DS_SOURCE = key("source", "ds")
GEO_SOURCE = key("source", "geoboundaries-svn")
BODY = key("body", "national-council")
SYSTEM_LOCAL = key("electoral_system", "ds-local")
SYSTEM_FUNCTIONAL = key("electoral_system", "ds-functional")


def area(unit: int) -> str:
    return key("area", UNIT, unit)


def fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.replace("Đ", "D").replace("đ", "d")).encode("ascii", "ignore")
    return re.sub(r"[^a-z0-9]+", " ", folded.decode().lower()).strip()


# the reports --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    """One published report: its election, its file, and what it covers."""

    id: str
    kind: str
    days: tuple[date, date]
    path: str
    title: str
    native: str
    note: str = ""
    contests: tuple[str, ...] = ()
    select: tuple[str, ...] = ()  # only these contests of the file
    skip: tuple[str, ...] = ()  # all but these
    first: str = ""  # the report whose package holds the file's artifact and pages, where two elections share a file
    order: tuple[int, ...] = ()  # the pages in reading order, where the file binds them out of order
    year: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "year", self.days[0].year)

    @property
    def url(self) -> str:
        return self.path if self.path.startswith("https://") else f"{BASE}/{self.path}"


D = "dokumenti/volitve"
REPORTS: list[Report] = [
    Report(
        "2002",
        "general",
        (date(2002, 11, 27), date(2002, 11, 28)),
        f"{D}/drzavni_svet_2002/koncno-porocilo-o-izidu-volitev-clanov-Drzavnega-sveta.pdf",
        "2002 Slovenian National Council election",
        "Volitve v državni svet 2002",
    ),
    Report(
        "2007",
        "general",
        (date(2007, 11, 21), date(2007, 11, 22)),
        f"{D}/drzavni_svet_2007/Porocilo-o-delnem-izidu-volitev-clanov-drzavnega-sveta---2007.pdf",
        "2007 Slovenian National Council election",
        "Volitve v državni svet 2007",
        note=(
            " The report is of the partial result: the Constitutional Court held back the election of the member for "
            "the 18th unit and of the member for the independent professions, which were held in 2008."
        ),
    ),
    Report(
        "2008-professions",
        "by-election",
        (date(2008, 3, 12), date(2008, 3, 12)),
        f"{D}/drzavni_svet_2008/Porocilo-o-izidu-naknadnih-volitev---samostojni-poklici.pdf",
        "2008 Slovenian National Council by-election, independent professions",
        "Naknadne volitve člana državnega sveta - predstavnika samostojnih poklicev 2008",
        contests=("professions",),
    ),
    Report(
        "2008-unit-18",
        "by-election",
        (date(2008, 4, 9), date(2008, 4, 9)),
        f"{D}/drzavni_svet_2008/Porocilo-o-izidu-ponovnih-volitev-clana-DS---lokalni-interesi.pdf",
        "2008 Slovenian National Council repeat election, 18th electoral unit",
        "Ponovne volitve člana državnega sveta - predstavnika lokalnih interesov 2008",
        contests=("unit-18",),
    ),
    Report(
        "2012",
        "general",
        (date(2012, 11, 21), date(2012, 11, 22)),
        f"{D}/drzavni_svet_2012/Porociloo-o-izidu---anonim.pdf",
        "2012 Slovenian National Council election",
        "Volitve v državni svet 2012",
        order=(1, 2, 3, 4, 5, 6, 7, 8, 14, 12, 11, 10, 9, 13),
        note=" The pages of the file are bound out of order; they are read in the order the text follows.",
    ),
]
REPORTS += [
    Report(
        "2017",
        "general",
        (date(2017, 11, 22), date(2017, 11, 23)),
        f"{D}/drzavni_svet_2017/Konc__ni_izzid_-_volitve_c__lanov_DS_2017.pdf",
        "2017 Slovenian National Council election",
        "Volitve v državni svet 2017",
        skip=("culture",),
    ),
    Report(
        "2018-culture",
        "by-election",
        (date(2018, 3, 29), date(2018, 3, 29)),
        f"{D}/drzavni_svet_2017/Konc__ni_izzid_-_volitve_c__lanov_DS_2017.pdf",
        "2018 Slovenian National Council repeat election, culture and sport",
        "Ponovne volitve člana državnega sveta - predstavnika za področje kulture in športa 2018",
        select=("culture",),
        first="2017",
    ),
]
REPORTS += [
    Report(
        "2014",
        "by-election",
        (date(2014, 5, 29), date(2014, 5, 29)),
        f"{D}/drzavni_svet_2014/Porocilo-o-izidu-nadomestnih-volitevh---anonimizirano.pdf",
        "2014 Slovenian National Council by-election, 3rd and 6th electoral units",
        "Nadomestne volitve članov državnega sveta - predstavnikov lokalnih interesov 2014",
        contests=("unit-3", "unit-6"),
    ),
]
REPORTS += [
    Report(
        "2018-welfare",
        "by-election",
        (date(2018, 12, 17), date(2018, 12, 17)),
        f"{D}/00wetsi0.pdf",
        "2018 Slovenian National Council by-election, social welfare",
        "Nadomestne volitve člana državnega sveta - predstavnika negospodarskih dejavnosti za področje socialnega varstva 2018",
        contests=("welfare",),
    ),
    Report(
        "2020-unit-3",
        "by-election",
        (date(2020, 6, 18), date(2020, 6, 18)),
        f"{D}/drzavni_svet_2020/Koncni-izid-nadomestnih-volitev-clana-drzavnega-sveta---predstavnika-lokalnih-interesov.pdf",
        "2020 Slovenian National Council by-election, 3rd electoral unit",
        "Nadomestne volitve člana državnega sveta - predstavnika lokalnih interesov 2020",
        select=("unit-3",),
    ),
    Report(
        "2020-unit-6",
        "by-election",
        (date(2020, 10, 15), date(2020, 10, 15)),
        f"{D}/drzavni_svet_2020/KONC__NI_IZZID_VOLITEV_-_NADOMESTNE_VOLITVE_C__LANA_DS_2020_-_6._VE.pdf",
        "2020 Slovenian National Council by-election, 6th electoral unit",
        "Nadomestne volitve člana državnega sveta - predstavnika lokalnih interesov 2020 (6. volilna enota)",
        select=("unit-6",),
    ),
    Report(
        "2023-culture",
        "by-election",
        (date(2023, 5, 31), date(2023, 5, 31)),
        "Zapisnik_koncni_izid_ponovnih_volitev_v_DS_kultura_in_sport_8.6.2023.pdf",
        "2023 Slovenian National Council repeat election, culture and sport",
        "Ponovne volitve člana državnega sveta - predstavnika za področje kulture in športa 2023",
        select=("culture",),
    ),
    Report(
        "2024-unit-13",
        "by-election",
        (date(2024, 11, 14), date(2024, 11, 14)),
        "Sklep_o_izidu_nadomestnih_volitev_v_DS_26.11.2024.pdf",
        "2024 Slovenian National Council by-election, 13th electoral unit",
        "Nadomestne volitve člana državnega sveta - predstavnika lokalnih interesov 2024",
        contests=("unit-13",),
    ),
]
FORCE_OCR = {f"{BASE}/{D}/drzavni_svet_2020/KONC__NI_IZZID_VOLITEV_-_NADOMESTNE_VOLITVE_C__LANA_DS_2020_-_6._VE.pdf"}
TEXT_LAYER = {"2002", "2008-professions", "2008-unit-18"}


def report_urls() -> list[str]:
    return [r.url for r in REPORTS]


# transcriptions -----------------------------------------------------------------------------------------------------

ROW_TOLERANCE = 14


@dataclass(frozen=True)
class Row:
    page: int
    text: str
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the page


@cache
def _lines() -> dict[str, list[dict[str, object]]]:
    return json.loads(LINES.read_text())


def rows(url: str, order: tuple[int, ...] = ()) -> list[Row]:
    """The transcribed lines of a report joined into the rows a reader sees: a label and its figure share a row."""
    out: list[Row] = []
    sheets = _lines()[url]
    for number in order or range(1, len(sheets) + 1):
        sheet = sheets[number - 1]
        page: list[list[object]] = sheet["lines"]  # ty: ignore[invalid-assignment]
        width, height = int(str(sheet["w"])), int(str(sheet["h"]))
        found: list[tuple[float, list[list[object]]]] = []
        for line in sorted(page, key=lambda item: (int(str(item[0])) + int(str(item[2]))) / 2):
            center = (int(str(line[0])) + int(str(line[2]))) / 2
            if found and abs(found[-1][0] - center) < ROW_TOLERANCE:
                found[-1][1].append(line)
                found[-1] = (center, found[-1][1])
            else:
                found.append((center, [line]))
        for _, parts in found:
            parts.sort(key=lambda item: int(str(item[1])))
            text = " ".join(str(part[4]) for part in parts).strip()
            y0 = min(int(str(part[0])) for part in parts)
            x0 = min(int(str(part[1])) for part in parts)
            y1 = max(int(str(part[2])) for part in parts)
            x1 = max(int(str(part[3])) for part in parts)
            out.append(
                Row(
                    number,
                    text,
                    (round(x0 / width, 4), round(y0 / height, 4), round(x1 / width, 4), round(y1 / height, 4)),
                )
            )
    return out


# parsing ------------------------------------------------------------------------------------------------------------


@dataclass
class Candidate:
    name: str
    votes: int
    row: Row
    elected: bool = False


@dataclass
class Contest:
    """One contest of a report: a local-interest unit, or a functional-interest group."""

    unit: int | None
    group: str | None
    electors: int | None = None
    present: int | None = None
    voted: int | None = None
    cast: int | None = None
    invalid: int | None = None
    valid: int | None = None
    candidates: list[Candidate] = field(default_factory=list)
    elected: list[str] = field(default_factory=list)
    municipalities: str = ""
    rows: dict[str, Row] = field(default_factory=dict)

    @property
    def tag(self) -> str:
        return f"unit-{self.unit}" if self.unit is not None else str(self.group)


UNIT_HEAD = re.compile(
    r"^(?:\d+\.\s*)?V\s*volilni\s*enoti\s*(?:št\.?|st\.?|številka)?\s*(\d{1,2})\b(.*)$", re.IGNORECASE
)
COUNT = re.compile(r"^[ŠS]te\S*\s*(.*?)[:.]?\s+(\d[\d.]*)$", re.IGNORECASE)
VOTE = re.compile(r"^(?:\d{1,2}\s*[.,]?|[.,])?\s*(.+?)\s+(\d+)\s+glas\w*$")
FIELDS = (
    ("izvoljenih predstavnikov", "electors"),
    ("navzoc", "present"),
    ("oddanih", "cast"),
    ("neveljavnih", "invalid"),
    ("veljavnih", "valid"),
)
SKIP_ELECTED = re.compile(
    r"^(Ker\b|21\. člen|Zakona o|Na tej podlagi|skladno z|opravil žreb|volilni odbor)", re.IGNORECASE
)


def group_of(text: str) -> str | None:
    lower = fold(text)
    for code, _, _, _, word in GROUPS:
        if word in lower:
            return code
    return None


def tokens(name: str) -> frozenset[str]:
    return frozenset(w for w in fold(name).split() if w not in TITLES)


PREFIX = re.compile(r"^\d{1,2}\.\s*(?=V\s*volilni|Za člane|Predstavnik|Glede|Državna|Volilne)")
ROLL = (
    (re.compile(r"vpisanih:?\s*(\d[\d.]*)\s*elektor", re.IGNORECASE), "electors"),
    (re.compile(r"udele\w+:?\s*(\d[\d.]*)", re.IGNORECASE), "present"),
    (re.compile(r"volilnega imenika je glasovalo:?\s*(\d[\d.]*)\s*elektor", re.IGNORECASE), "voted"),
)
SINGLE = re.compile(r"ugotovila naslednji izid volitev člana državnega sveta\s*-\s*predstavnika", re.IGNORECASE)
GROUP_HEAD = re.compile(r"^Predstavnik\w*\s.*:$")
GROUP_ELECTED = re.compile(r"^(?:za predstavnik\w*|predstavnik\w*)\s.*[:.]$", re.IGNORECASE)
ENDS = re.compile(r"^(Številka|Stevilka|Datum|Državna volilna komisija je ugotovila končni|DRŽAVNA)")
TAIL = re.compile(r"^(Glede na sklep|Državna volilna komisija je ugotovila končni)")


def name_of(text: str) -> str | None:
    """A member's name from a line of the list of the elected: the name before the birth date and address."""
    parts = re.split(r",\s*(?:roj\b|rojen)", text)
    name = re.sub(r"[\s,.\-–]+$", "", parts[0])
    if (
        len(name.split()) < 2
        or (len(parts) == 1 and re.search(r"\d", name))
        or len(name.split()) > 7
        or SKIP_ELECTED.match(name)
    ):
        return None
    return name


def read(report: Report) -> list[Contest]:
    contests: list[Contest] = []
    current: Contest | None = None
    mode = "votes"
    target: Contest | None = None
    pending = False
    awaiting = False
    if len(report.contests) == 1 and not report.select:
        # A report of one contest prints it without a heading of its own.
        only = report.contests[0]
        unit = int(only[5:]) if only.startswith("unit-") else None
        current = Contest(unit=unit, group=None if unit else only)
        contests.append(current)

    def begin(contest: Contest) -> Contest:
        contests.append(contest)
        return contest

    for row in rows(report.url, report.order):
        text = PREFIX.sub("", row.text.strip())
        if not text:
            continue
        if mode == "elected_single":
            if ENDS.match(text):
                mode, target = "done", None
            elif re.match(r"^[IV]+\.$", text):
                mode, target = "votes", None
            elif re.match(r"^[A-C]\.$", text):
                mode, target = "done", None
            elif target is not None:
                found = name_of(text)
                if found:
                    target.elected.append(found)
            continue
        head = UNIT_HEAD.match(text)
        if head and "obsega" in text:
            current = begin(Contest(unit=int(head.group(1)), group=None, municipalities=head.group(2)))
            pending = not SEAT.search(current.municipalities)
            mode, target = "votes", None
            continue
        if pending and current is not None:
            current.municipalities += " " + text
            pending = not SEAT.search(current.municipalities)
            continue
        if GROUP_HEAD.match(text) and "glasov" not in text and not text.startswith("Predstavnik za člane"):
            group = group_of(text)
            if group is None:
                raise ValueError(f"{report.id}: unknown interest group {text!r}")
            current = begin(Contest(unit=None, group=group))
            mode, target = "votes", None
            continue
        if SINGLE.search(text):
            current = begin(Contest(unit=None, group=group_of(text)))
            mode, target, awaiting = "votes", None, current.group is None
            continue
        if awaiting and current is not None:
            current.group = group_of(text)
            awaiting = current.group is None
            if current.group is None and not text.endswith(":"):
                raise ValueError(f"{report.id}: unknown interest group {text!r}")
            continue
        if re.match(r"^za predstavnik", text, re.IGNORECASE) and group_of(text):
            mode = "elected_functional"
            found = [c for c in contests if c.group == group_of(text)]
            target = found[0] if found else None
            continue
        if re.search(r"predstavnike lokalnih interesov so bili izvoljeni", text, re.IGNORECASE):
            mode, target = "elected_local", None
            continue
        single = re.search(r"(?:za )?predstavnika (.+?) izvoljen:$", text)
        if single and group_of(single.group(1)):
            mode = "elected_functional"
            found = [c for c in contests if c.group == group_of(single.group(1))]
            target = found[0] if found else None
            continue
        if (report.contests or report.select) and mode == "votes" and re.search(r"izvoljen\w*:$", text):
            mode, target = "elected_single", current
            continue
        if mode.startswith("elected"):
            if TAIL.match(text) or ENDS.match(text):
                mode, target = "done", None
            elif re.match(r"^([IV]+\.|[A-C]\.|Izid volitev)", text):
                target = None
            elif head:
                found = [c for c in contests if c.unit == int(head.group(1))]
                target = found[0] if found else None
                rest = re.sub(r"^[\s:]+", "", head.group(2))
                if target is not None and (name := name_of(rest)):
                    target.elected.append(name)
            elif GROUP_ELECTED.match(text) and group_of(text):
                found = [c for c in contests if c.group == group_of(text)]
                target = found[0] if found else None
            elif target is not None and not text.endswith(":") and (name := name_of(text)):
                target.elected.append(name)
            continue
        if mode == "done" or current is None:
            continue
        roll = next(((field_name, m) for pattern, field_name in ROLL if (m := pattern.search(text))), None)
        if roll:
            setattr(current, roll[0], int(roll[1].group(1).replace(".", "")))
            current.rows[roll[0]] = row
            continue
        count = COUNT.match(text)
        if count:
            label = fold(text[: text.rfind(count.group(2))])
            field_name = next((name for word, name in FIELDS if word in label), None)
            if field_name:
                setattr(current, field_name, int(count.group(2).replace(".", "")))
                current.rows[field_name] = row
            continue
        vote = VOTE.match(text)
        if vote:
            current.candidates.append(Candidate(vote.group(1).strip(" ."), int(vote.group(2)), row))
    for c in contests:
        if c.voted is None and c.present is not None:
            c.voted, c.rows["voted"] = c.present, c.rows["present"]
    contests = [c for c in contests if (not report.select or c.tag in report.select) and c.tag not in report.skip]
    for contest in contests:
        _mark_elected(report, contest)
    return contests


def _mark_elected(report: Report, contest: Contest) -> None:
    """Match the names the report lists as elected to its candidates, by their words and, where the report spells a
    name differently in its two lists, by the closest spelling."""
    left = list(contest.candidates)
    for name in contest.elected:
        exact = [c for c in left if tokens(c.name) == tokens(name)]
        if exact:
            found = exact[0]
        else:
            ratio = {id(c): difflib.SequenceMatcher(None, fold(c.name), fold(name)).ratio() for c in left}
            ranked = sorted(left, key=lambda c: -ratio[id(c)])
            if not ranked or ratio[id(ranked[0])] < 0.8:
                raise ValueError(f"{report.id} {contest.tag}: {name!r} is elected but is no candidate")
            found = ranked[0]
        found.elected = True
        if not exact and re.search(r"[^A-Za-zÀ-ž .'-]", found.name):
            found.name = name  # a letter the scan misread, where the report's other list spells the name
        left.remove(found)


# the elections ------------------------------------------------------------------------------------------------------


@cache
def contests(report_id: str) -> list[Contest]:
    report = next(r for r in REPORTS if r.id == report_id)
    return read(report)


def fetch() -> None:
    for report in REPORTS:
        raw.fetch(SOURCE, report.url, suffix=".pdf")
    raw.fetch(SOURCE, GEOBOUNDARIES, suffix=".geojson")
    raw.fetch(SOURCE, ARCHIVE_PAGE, suffix=".html")


def packages() -> list[str]:
    return [GEO_KEY, REGISTRY_KEY, *(election_key(r) for r in REPORTS)]


def election_key(report: Report) -> str:
    return f"{SOURCE}/ds-{report.id}"


def emit() -> None:
    package.run("load", packages())


def write() -> None:
    geo().write()
    registry().write()
    for report in REPORTS:
        election(report).write()


# geography ----------------------------------------------------------------------------------------------------------

MATCH = 0.7
# Municipalities the reports' lists leave out, because they were founded after the law fixed the units: each lies in
# the unit of the municipality it was separated from (Ankaran from Koper in 2011, Straža from Novo mesto and
# Šentrupert from Trebnje in 2006). The names are as geoBoundaries prints them, with letters lost.
ALSO = {"ankaran": 18, "straca": 14, "sentrupert": 14}
# geoBoundaries names that lost letters beyond a fuzzy match.
RENAMED = {"lentjur": "sentjur pri celju", "ravne na korokkem": "ravne"}
BILINGUAL = re.compile(r" (capodistria|isola|pirano|ancarano)$")


def split_municipalities(text: str) -> list[str]:
    """The municipality names in a unit's heading ("ki obsega občine: A, B in C (sedež: D)")."""
    body = re.sub(r"^[\s,:;)]*(?:ki obsega obč\w+)?[\s:,]*", "", text)
    body = re.split(r"\(\s*sedež", body)[0]
    parts = re.split(r",|\bin\b", body)
    return [
        re.sub(r"\s+", " ", re.sub(r"^[\s:]+|[\s.]+$", "", part)).replace("- ", "-")
        for part in parts
        if part.strip(" :")
    ]


SEAT = re.compile(r"sedež\w*:?\s*([^)]+)\)")


def seat(text: str) -> str:
    found = SEAT.search(text)
    return found.group(1).strip() if found else ""


@cache
def composition() -> dict[int, tuple[str, list[str]]]:
    """Each unit's seat and municipalities, as the latest general election's report prints them."""
    latest = max((r for r in REPORTS if r.kind == "general"), key=lambda r: r.days[0])
    found: dict[int, tuple[str, list[str]]] = {}
    for contest in contests(latest.id):
        if contest.unit is not None:
            found[contest.unit] = (seat(contest.municipalities), split_municipalities(contest.municipalities))
    if set(found) != set(UNITS):
        raise ValueError(f"{latest.id}: units {sorted(set(UNITS) - set(found))} are missing")
    return found


@cache
def unit_shapes() -> dict[int, shapely.MultiPolygon]:
    """Each unit's outline: the union of its municipalities' outlines from geoBoundaries, matched by name (the file's
    names lost their letters with diacritics, so a name is matched to the closest listed one, both ways)."""
    features = json.loads(raw.cached(SOURCE, GEOBOUNDARIES).path.read_bytes())["features"]
    listed = {fold(name): unit for unit, (_, names) in composition().items() for name in names}
    theirs = {re.sub(BILINGUAL, "", fold(str(f["properties"]["shapeName"]))): f for f in features}
    theirs = {RENAMED.get(name, name): f for name, f in theirs.items() if name not in ALSO}
    ranked = sorted(((difflib.SequenceMatcher(None, a, b).ratio(), a, b) for a in theirs for b in listed), reverse=True)
    parts: dict[int, list[shapely.Geometry]] = {unit: [] for unit in UNITS}
    taken: set[str] = set()
    used: set[str] = set()
    for ratio, name, mine in ranked:
        if name in taken or mine in used or ratio < MATCH:
            continue
        taken.add(name)
        used.add(mine)
        parts[listed[mine]].append(shapely.make_valid(shapely.geometry.shape(theirs[name]["geometry"])))
    for feature in features:
        name = re.sub(BILINGUAL, "", fold(str(feature["properties"]["shapeName"])))
        if name in ALSO:
            taken.add(name)
            parts[ALSO[name]].append(shapely.make_valid(shapely.geometry.shape(feature["geometry"])))
    taken |= {n for n in ALSO if n not in theirs}
    if (set(theirs) | set(ALSO)) != taken or used != set(listed):
        raise ValueError(f"unmatched outlines {sorted(set(theirs) - taken)}, names {sorted(set(listed) - used)}")
    found: dict[int, shapely.MultiPolygon] = {}
    for unit, shapes in parts.items():
        merged = shapely.ops.unary_union([s.buffer(0.00002) for s in shapes]).buffer(-0.00002)
        polygons = [
            q
            for part in shapely.get_parts(merged)
            if shapely.get_type_id(part) in (3, 6)
            for q in shapely.get_parts(part)
        ]
        found[unit] = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    return found


def geo() -> Package:
    p = Package(
        GEO_KEY,
        SOURCE,
        "The 22 electoral units of the National Council's local-interest members, as unions of municipalities, with "
        "outlines built from geoBoundaries",
    )
    p.external.add(SLOVENIA)
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap, Wambacher)",
        title="geoBoundaries gbOpen Slovenia ADM2, 2017",
        url="https://www.geoboundaries.org/",
        license="ODbL 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), © OpenStreetMap contributors",
    )
    p.artifact(raw.cached(SOURCE, GEOBOUNDARIES), GEO_SOURCE)
    p.add(
        "scheme",
        code=UNIT,
        polity_id=SLOVENIA,
        kind="electoral_district",
        rank=1,
        noun="electoral unit",
        code_system=None,
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", UNIT, "2017"),
        scheme_code=UNIT,
        vintage="2017",
        valid_from=UNITS_FROM,
        as_of=date(2017, 1, 1),
    )
    shapes = unit_shapes()
    for unit, (home, names) in sorted(composition().items()):
        id = area(unit)
        p.add("area", id=id, scheme_code=UNIT)
        p.add("area_code", area_id=id, code=str(unit))
        p.name(id, f"Electoral unit {unit} ({home})", "en", "official")
        p.name(id, f"{unit}. volilna enota ({home})", "sl", "native", False)
        shape = shapes[unit]
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=UNITS_FROM,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=GEO_SOURCE,
        )
    return p


UNITS_FROM = date(2002, 1, 1)


# registry -----------------------------------------------------------------------------------------------------------

NOTE_LOCAL = (
    "One of the 22 members who represent local interests is elected in each of 22 electoral units, groups of "
    "municipalities. Each municipal council of the unit chooses electors in proportion to its population, and the "
    "electors meet in an electoral assembly and vote for one candidate; the candidate with the most votes is elected, "
    "and a tie is decided by lot (Zakon o državnem svetu)."
)
NOTE_FUNCTIONAL = (
    "The 18 members who represent functional interests are elected by electors chosen by the organizations of each "
    "interest group: four employers, four employees, two farmers, one craftsman, one independent professional, and "
    "six for the non-commercial activities (universities and higher education, education and training, research, "
    "culture and sport, health, and social welfare). The electors of a group vote at an assembly for as many "
    "candidates as the group has seats, and the candidates with the most votes are elected; a tie is decided by lot "
    "(Zakon o državnem svetu)."
)


def registry() -> Package:
    p = Package(
        REGISTRY_KEY,
        SOURCE,
        "The State Election Commission's National Council reports as a source, the National Council, and the "
        "electoral systems of its two kinds of member",
        depends=[GEO_KEY],
    )
    p.external.add(SLOVENIA)
    p.add(
        "source",
        id=DS_SOURCE,
        kind="official_body",
        publisher="Državna volilna komisija",
        title="National Council elections: reports on the results (volitve v državni svet, poročila o izidu)",
        url=ARCHIVE_PAGE,
        license="Public information of the State Election Commission",
        license_url="https://www.dvk-rs.si/informacije-javnega-znacaja/",
        attribution="Državna volilna komisija Republike Slovenije",
    )
    p.add("body", id=BODY, polity_id=SLOVENIA, kind="upper_house")
    p.name(BODY, "National Council", "en", "official")
    p.name(BODY, "Državni svet", "sl", "native", False)
    for id, rules, english, native, note in (
        (SYSTEM_LOCAL, {"formula": "fptp"}, "Local interests", "Predstavniki lokalnih interesov", NOTE_LOCAL),
        (
            SYSTEM_FUNCTIONAL,
            {"formula": "block"},
            "Functional interests",
            "Predstavniki funkcionalnih interesov",
            NOTE_FUNCTIONAL,
        ),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=SLOVENIA,
            valid_from=date(1992, 12, 1),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.name(id, f"National Council: {english.lower()}", "en", "official")
        p.name(id, f"Državni svet: {native.lower()}", "sl", "native", False)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    return p


# elections ----------------------------------------------------------------------------------------------------------

METHOD = (
    "Reported by the State Election Commission (Državna volilna komisija) from the records of the electoral assemblies. "
    "Electors are not voters: the units' electors are chosen by municipal councils and the groups' by their "
    "organizations. The electors on the roll are stored as registered, the electors present where the report prints "
    "them as voters, the ballots cast, the invalid ballots and the valid ballots as printed, and each candidate's "
    "votes. The report is a PDF; {how}"
)
HOW_TEXT = "its figures are read from the text of the file."
HOW_SCAN = (
    "it is a scan, and its figures are read from the page with optical character recognition; the run's checks "
    "compare every candidate list with the ballots printed above it, and the elected members the report names with "
    "the candidates who won."
)


def _frame(p: Package, facts: list[tuple[str, str, str | None, int, int]]) -> None:
    size = len(facts)
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": [f[0] for f in facts],
                "unit_id": [f[1] for f in facts],
                "option_id": [f[2] for f in facts],
                "channel": [0] * size,
                "measure": [f[3] for f in facts],
                "count_round": [0] * size,
                "flags": [0] * size,
                "value": [f[4] for f in facts],
            },
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
        ),
    )


def contest_names(report: Report, contest: Contest) -> tuple[str, str, str]:
    """English name, native name, and slug leaf of a contest."""
    if contest.unit is not None:
        home = composition()[contest.unit][0]
        return (
            f"Local interests, electoral unit {contest.unit} ({home})",
            f"Predstavnik lokalnih interesov, {contest.unit}. volilna enota ({home})",
            f"local-unit-{contest.unit}",
        )
    code, english, native, _, _ = next(g for g in GROUPS if g[0] == contest.group)
    return (
        f"Functional interests: {english.lower()}",
        f"Predstavnik funkcionalnih interesov: {native.lower()}",
        f"functional-{code}",
    )


def by_id(report_id: str) -> Report:
    return next(r for r in REPORTS if r.id == report_id)


def pages_of(report: Report) -> set[int]:
    """The pages a report's figures are read from."""
    found: set[int] = set()
    for contest in contests(report.id):
        found |= {row.page for row in contest.rows.values()} | {c.row.page for c in contest.candidates}
    return found


MEASURES = (("electors", 2), ("voted", 14), ("cast", 3), ("invalid", 5), ("valid", 4))


def election(report: Report) -> Package:
    p = Package(
        election_key(report),
        SOURCE,
        f"The {report.title}, by electoral unit and interest group: the electors' votes for each candidate",
        depends=[GEO_KEY, REGISTRY_KEY, *([election_key(by_id(report.first))] if report.first else [])],
    )
    p.external.add(SLOVENIA)
    stored = raw.cached(SOURCE, report.url)
    artifact = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{DS_SOURCE}\x1f{stored.storage_key}"))
    if report.first:
        # The file is the one the first election's package already holds, with the pages that election reads.
        p.uses(stored)
        shared = pages_of(by_id(report.first))
    else:
        p.artifact(stored, DS_SOURCE)
        shared = set()
    first, last = report.days
    election_id = key("election", report.id)
    p.add(
        "election",
        id=election_id,
        polity_id=SLOVENIA,
        kind="general" if report.kind == "general" else "by_election",
        date_from=first,
        date_to=last,
        status="held",
    )
    path = f"si/{first}-national-council"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, report.title, "en", "official")
    p.name(election_id, report.native, "sl", "native", False)
    how = HOW_TEXT if report.id in TEXT_LAYER else HOW_SCAN
    facts: list[tuple[str, str, str | None, int, int]] = []
    pages: dict[int, str] = {}
    sizes = {n: sheet for n, sheet in enumerate(_lines()[report.url], 1)}

    def locate(result_set: str, unit: str, option: str | None, measure: int, row: Row) -> None:
        if row.page not in pages:
            pages[row.page] = key("page", report.first or report.id, row.page)
            if row.page not in shared:
                p.add(
                    "source_page",
                    id=pages[row.page],
                    artifact_id=artifact,
                    page_number=row.page,
                    image_key=f"pages/{SOURCE}/{stored.sha256}/{row.page}.png",
                    width=int(str(sizes[row.page]["w"])),
                    height=int(str(sizes[row.page]["h"])),
                )
        x0, y0, x1, y1 = row.box
        p.add(
            "value_locator",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            page_id=pages[row.page],
            source_id=None,
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
        )

    for contest in contests(report.id):
        english, native, leaf = contest_names(report, contest)
        tag = f"{report.id}:{contest.tag}"
        contest_id = key("contest", tag)
        seats = 1 if contest.unit is not None else GROUP_SEATS[str(contest.group)]
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="body_seats",
            body_id=BODY,
            district_area_id=area(contest.unit) if contest.unit is not None else None,
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM_LOCAL if contest.unit is not None else SYSTEM_FUNCTIONAL,
            default_result_kind="counted",
        )
        p.name(contest_id, english, "en", "official")
        p.name(contest_id, native, "sl", "native", False)
        p.add("slug", path=f"{path}/{leaf}", entity_id=contest_id, is_current=True)
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=DS_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD.format(how=how) + report.note,
            lang="en",
        )
        unit = key("unit", tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=SLOVENIA,
            scheme_code=UNIT,
            code=f"ds:{tag}",
            area_relation="equals" if contest.unit is not None else "unlocated",
            area_id=area(contest.unit) if contest.unit is not None else None,
            kind="standard",
            valid_from=first,
        )
        p.name(unit, english, "en", "official")
        for name, measure in MEASURES:
            value = getattr(contest, name)
            if value is not None:
                facts.append((result_set, unit, None, measure, value))
                locate(result_set, unit, None, measure, contest.rows[name])
        seen: Counter[str] = Counter()
        for index, candidate in enumerate(contest.candidates, 1):
            folded = fold(candidate.name)
            seen[folded] += 1
            option = key("option", tag, folded, seen[folded])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="candidate",
                ballot_position=index,
                ballot_label=candidate.name,
            )
            person = key("person", tag, folded, seen[folded])
            p.add("person", id=person)
            p.name(person, candidate.name, "sl", "official")
            p.add(
                "candidacy",
                id=key("candidacy", tag, folded, seen[folded]),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
            )
            facts.append((result_set, unit, option, 1, candidate.votes))
            locate(result_set, unit, option, 1, candidate.row)
            p.add(
                "outcome", result_set_id=result_set, option_id=option, kind="elected" if candidate.elected else "lost"
            )
    _frame(p, facts)
    return p


def parse() -> None:
    write()
    for report in REPORTS:
        for line in problems(report):
            print(line, flush=True)


def problems(report: Report) -> list[str]:
    """What disagrees inside a report: ballots against the candidates' votes, and the report's own list of the
    elected against the candidates with the most votes."""
    found: list[str] = []
    for contest in contests(report.id):
        seats = 1 if contest.unit is not None else GROUP_SEATS[str(contest.group)]
        total = sum(c.votes for c in contest.candidates)
        where = f"{report.id} {contest.tag}"
        if None in (contest.cast, contest.invalid, contest.valid, contest.electors):
            found.append(f"{where}: a ballot count is missing")
            continue
        if contest.cast != (contest.invalid or 0) + (contest.valid or 0):
            found.append(f"{where}: cast {contest.cast} != invalid {contest.invalid} + valid {contest.valid}")
        if (contest.cast or 0) > (contest.electors or 0):
            found.append(f"{where}: cast {contest.cast} exceeds electors {contest.electors}")
        for c in contest.candidates:
            if c.votes > (contest.valid or 0):
                found.append(f"{where}: {c.name} has {c.votes} votes of {contest.valid} valid ballots")
        if seats == 1 and total != contest.valid:
            found.append(f"{where}: candidates' votes {total} != valid {contest.valid}")
        if seats > 1 and total > seats * (contest.valid or 0):
            found.append(f"{where}: candidates' votes {total} exceed {seats} per valid ballot {contest.valid}")
        if sum(c.elected for c in contest.candidates) != seats:
            found.append(f"{where}: {sum(c.elected for c in contest.candidates)} elected for {seats} seats")
        top = sorted((c.votes for c in contest.candidates), reverse=True)[:seats]
        won = sorted((c.votes for c in contest.candidates if c.elected), reverse=True)
        if top != won:
            found.append(f"{where}: the elected have votes {won}, the top {seats} have {top}")
    return found
