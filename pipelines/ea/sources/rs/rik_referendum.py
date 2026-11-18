"""Serbia's republic referendums of 1992, 1998 and 2006, by municipality, from the Republic Electoral Commission's archive.

The archive holds, for each referendum, the Commission's report of the results and the Republic Statistical Office's
book of the results by municipality. The books of 1992 and 1998 are scans, read once from the page images with the
page and box of every count kept in ``rik_referendum.json`` (see ``rik_referendum_scans``); the book of 2006 has a text
layer and is read directly. The 31 May 1992 book is one volume for the federal election and the two referendums held
that day, and carries a table for each question: Amendment I to the Constitution, the coat of arms, and the anthem.
"""

import json
import re
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.rs import rik, rik_poslanici, rik_predsednik, rik_referendum_scans

DATA_FILE = Path(__file__).with_name("rik_referendum.json")
NAMES_FILE = Path(__file__).with_name("rik_referendum_names.json")
DIGITIZED = rik_predsednik.DIGITIZED
COUNTRY = rik_predsednik.COUNTRY
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = (
    rik.VOTES,
    rik.REGISTERED,
    rik.CAST,
    rik.VALID,
    rik.INVALID,
    rik.VOTERS,
)
BASE = f"{rik.ARCHIVE}/referendumi"


def _url(name: str) -> str:
    return f"{BASE}/{urllib.parse.quote(name)}"


FILES = {
    "1992-05-book": _url("1992 I referenum maj rzs  compressed.pdf"),
    "1992-05-report-amendment": _url("1992 I  izvestaj referendum Amandman I maj.pdf"),
    "1992-05-report-symbols": _url("1. izvestaj 1992 II referendum drzavni simboli.pdf"),
    "1992-10-report": _url("1. izvestaj 1992 III referendum oktobar.pdf"),
    "1992-10-book": _url("2. publikacija 1992 III referendum oktobar.pdf"),
    "1998-report": _url("1. izvestaj 1998 referendum.pdf"),
    "1998-book": _url("2. publikacija 1998 referendum.pdf"),
    "2006-report": _url("1. 2006 izvestaj o rezultatu referenduma LAT.docx"),
    "2006-book": _url("2. publikacija 2006 referendum.pdf"),
}

# the names the 1992 books print, which differ from today's: Светозарево is Јагодина and Стари Бечеј is Бечеј
ALIASES = {"Светозарево": "Јагодина", "Стари Бечеј": "Бечеј"}


@dataclass(frozen=True)
class Table:
    """A municipal table of a scanned book: where it is, and how its percentages are printed."""

    book: str
    pages: tuple[int, ...]
    names: str
    base: str  # what the answers' percentages are of
    decimals: tuple[int, int, int]
    fixes: dict[int, dict[str, int]] = field(default_factory=dict)


# Counts the OCR dropped or misread, each read by eye from the page image (fixes), and rows whose counts read right
# while the OCR misread the printed percentage (skip).
TABLES = {
    "1992-05-amendment": Table(
        "1992-05-book",
        tuple(range(37, 43)),
        "1992-05",
        "registered",
        (2, 2, 2),
        {27: {"b": 99}, 162: {"b": 1}, 165: {"b": 9}, 170: {"b": 0}, 189: {"b": 24}},
    ),
    "1992-05-arms": Table(
        "1992-05-book",
        tuple(range(43, 49)),
        "1992-05",
        "voters",
        (2, 2, 1),
        {7: {"skip": 1}, 12: {"voters": 24301}, 32: {"registered": 28717}},
    ),
    "1992-05-anthem": Table("1992-05-book", tuple(range(49, 55)), "1992-05", "voters", (2, 2, 1)),
    "1992-10": Table(
        "1992-10-book",
        (13, 15, 17, 19, 21, 23),
        "1992-10",
        "registered",
        (2, 2, 2),
        {163: {"b": 1}, 166: {"b": 9}, 171: {"b": 1}},
    ),
    "1998": Table(
        "1998-book",
        tuple(range(15, 21)),
        "1998",
        "voters",
        (2, 2, 2),
        {
            27: {"b": 92780},
            42: {"a": 196},
            46: {"a": 401},
            51: {"b": 63150},
            55: {"a": 8},
            57: {"a": 2},
            63: {"a": 4},
            72: {"a": 2222},
            73: {"a": 9},
            75: {"a": 1},
            82: {"a": 1},
            85: {"a": 0},
            92: {"a": 4},
            97: {"b": 14960},
            130: {"a": 4},
            163: {"a": 1},
            169: {"a": 6},
            174: {"a": 3},
            182: {"a": 1},
            188: {"a": 0},
            189: {"a": 3},
        },
    ),
}


@dataclass(frozen=True)
class Question:
    leaf: str  # the contest's path segment
    title: str
    native: str
    table: str | None  # the municipal table, when it is a scan; the 2006 book is read from its text
    options: tuple[tuple[str, str], ...]  # English and Serbian labels, in ballot order
    declared: dict[int, int]  # the Republic's figures as the report or book prints them
    votes: tuple[int, int]  # the Republic's votes for each option
    outcomes: dict[int, str]  # the Commission's declared outcome of each option, by position from 0
    type: str
    note: str


YES_NO = (("Yes", "ЗА"), ("No", "ПРОТИВ"))
PROPOSALS = (("Proposal 1", "Предлог под бројем 1"), ("Proposal 2", "Предлог под бројем 2"))


@dataclass(frozen=True)
class Referendum:
    slug: str
    day: date
    last: date
    title: str
    native: str
    path: str
    questions: tuple[Question, ...]
    files: tuple[str, ...]  # the files the package cites; the first is the municipal book
    prefix: str  # keeps the reporting units of two referendums held the same day apart
    note: str


METHOD_SCAN = (
    "Digitized from the Republic Statistical Office's book of the results by municipality, published in the "
    "Commission's archive as a scan. Every count was read from the page image and keeps its page and bounding box, "
    "and each row's counts were checked against the percentages the book prints beside them. The book gives, for each "
    "municipality, the registered voters, the voters, and the votes for each answer; it gives no invalid ballots by "
    "municipality, which are stored for the Republic only. Municipalities are those of the time: Belgrade's are "
    'printed as "Београд-...", Niš, Novi Sad and Kragujevac as one municipality each, and Kosovo and Metohija\'s '
    "municipalities are included. A municipality that the archive's register no longer lists is placed within the "
    "country. The printed national totals are stored as declared."
)
METHOD_TEXT = (
    "Taken from the text layer of the Republic Statistical Office's book of the results by municipality, published in "
    "the Commission's archive, which prints for each municipality the registered voters, the voters, the valid and "
    "invalid ballots, and the votes for each answer. Voters abroad and in penal institutions are reported in units of "
    "their own. The book notes that the registered voters of the municipalities add up to more than the Republic's "
    "total because voters in special extracts, such as soldiers, are counted where they voted as well as where they "
    "are registered. The printed national totals are stored as declared."
)

REFERENDUMS = (
    Referendum(
        "1992-05-31-referendum",
        date(1992, 5, 31),
        date(1992, 5, 31),
        "1992 Serbian constitutional referendum (Amendment I)",
        "Републички референдум ради потврђивања Амандмана I на Устав Републике Србије 1992.",
        "rs/1992-05-31-referendum",
        (
            Question(
                "question",
                "1992 Serbian constitutional referendum (Amendment I)",
                "Да ли потврђујете Амандман I на Устав Републике Србије",
                "1992-05-amendment",
                YES_NO,
                {REGISTERED: 6930574, VOTERS: 3821564, INVALID: 335159, CAST: 3806539},
                (3326346, 144510),
                {0: "lost"},
                "mandatory_referendum",
                "The Commission found, under article 133 of the Constitution, that 3,326,346 voters, 48.00% of the "
                "voters registered, had voted for the amendment, and so that the amendment was not confirmed. "
                "Voting took place in 189 of 190 municipalities; it was not held in one. The Commission's report "
                "prints the registered voters, the answers and the invalid ballots; the book also prints the voters "
                "and the ballots used.",
            ),
        ),
        ("1992-05-book", "1992-05-report-amendment"),
        "1992-05-31",
        "",
    ),
    Referendum(
        "1992-05-31-referendum-symbols",
        date(1992, 5, 31),
        date(1992, 5, 31),
        "1992 Serbian referendum on the state symbols",
        "Претходно изјашњавање грађана на републичком референдуму о државним симболима Републике Србије 1992.",
        "rs/1992-05-31-referendum-symbols",
        (
            Question(
                "coat-of-arms",
                "1992 Serbian referendum on the state symbols: coat of arms",
                "Изглед и садржина грба Републике Србије",
                "1992-05-arms",
                PROPOSALS,
                {REGISTERED: 6930928, VOTERS: 3686788, INVALID: 617739, CAST: 3756162},
                (1586384, 1464011),
                {0: "won", 1: "lost"},
                "referendum",
                "Voters chose between two designs of the coat of arms of the Republic of Serbia. The Commission found "
                "that the majority, 1,586,383 voters or 43.03% of those who voted, had chosen design 1 (the report "
                "prints 1,586,383 in this sentence and 1,586,384 in its count). Voting took place in 189 of 190 "
                "municipalities. The ballots used, 3,756,162, are those of both questions.",
            ),
            Question(
                "anthem",
                "1992 Serbian referendum on the state symbols: anthem",
                "Предлог химне Републике Србије",
                "1992-05-anthem",
                PROPOSALS,
                {REGISTERED: 6930928, VOTERS: 3707338, INVALID: 482242, CAST: 3756162},
                (1730070, 1490484),
                {0: "won", 1: "lost"},
                "referendum",
                "Voters chose between two proposals for the anthem of the Republic of Serbia. The Commission found "
                "that the majority, 1,730,070 voters or 46.67% of those who voted, had chosen proposal 1. Voting "
                "took place in 189 of 190 municipalities. The ballots used, 3,756,162, are those of both questions.",
            ),
        ),
        ("1992-05-book", "1992-05-report-symbols"),
        "1992-05-31-symbols",
        "",
    ),
    Referendum(
        "1992-10-11-referendum",
        date(1992, 10, 11),
        date(1992, 10, 11),
        "October 1992 Serbian constitutional referendum (Amendment I)",
        "Републички референдум ради потврђивања Амандмана I на Устав Републике Србије 1992.",
        "rs/1992-10-11-referendum",
        (
            Question(
                "question",
                "October 1992 Serbian constitutional referendum (Amendment I)",
                "Да ли потврђујете Амандман I на Устав Републике Србије",
                "1992-10",
                YES_NO,
                {REGISTERED: 6977081, VOTERS: 3178086, INVALID: 21790, CAST: 3176764},
                (3040911, 114048),
                {0: "lost"},
                "mandatory_referendum",
                "The National Assembly called the vote again for 11 October 1992 after the 31 May vote failed. The "
                "Commission found that 3,040,911 voters, 43.58% of the voters registered, had voted for the "
                "amendment, and so that the amendment was not confirmed. Voting took place in 189 of 190 "
                "municipalities; it was not held in one.",
            ),
        ),
        ("1992-10-book", "1992-10-report"),
        "1992-10-11",
        "",
    ),
    Referendum(
        "1998-04-23-referendum",
        date(1998, 4, 23),
        date(1998, 4, 23),
        "1998 Serbian referendum on foreign representatives in resolving Kosovo",
        "Републички референдум ради изјашњавања грађана Србије да ли прихватају учешће страних представника у "
        "решавању проблема на Косову и Метохији 1998.",
        "rs/1998-04-23-referendum",
        (
            Question(
                "question",
                "1998 Serbian referendum on foreign representatives in resolving Kosovo",
                "Да ли прихватате учешће страних представника у решавању проблема на Косову и Метохији",
                "1998",
                (("Yes", "ДА"), ("No", "НЕ")),
                {REGISTERED: 7252304, VOTERS: 5297776, INVALID: 98498, CAST: 5296664},
                (180692, 5017383),
                {0: "lost", 1: "won"},
                "referendum",
                "The Commission found the referendum valid, because a majority of the registered voters took part, and "
                'that the majority had circled "НЕ" (no). Voting took place at 9,836 polling stations in all 189 '
                "municipalities. The printed percentages of the answers are shares of the voters. The votes and the "
                "invalid ballots the report prints add up to 5,296,573, 91 fewer than the 5,296,664 ballots used it "
                "prints.",
            ),
        ),
        ("1998-book", "1998-report"),
        "1998-04-23",
        "",
    ),
    Referendum(
        "2006-10-28-referendum",
        date(2006, 10, 28),
        date(2006, 10, 29),
        "2006 Serbian constitutional referendum",
        "Републички референдум ради потврђивања новог Устава Републике Србије 2006.",
        "rs/2006-10-28-referendum",
        (
            Question(
                "question",
                "2006 Serbian constitutional referendum",
                "Да ли сте за потврђивање новог Устава Републике Србије",
                None,
                (("Yes", "ДА"), ("No", "НЕ")),
                {REGISTERED: 6639385, VOTERS: 3645517, INVALID: 25866, VALID: 3619221},
                (3521724, 97497),
                {0: "won", 1: "lost"},
                "mandatory_referendum",
                "Voting took place on 28 and 29 October 2006 at 8,401 polling stations in the Republic and 40 abroad. "
                "The Commission found the referendum valid, because a majority of the registered voters took part, and "
                'that the 3,521,724 voters who circled "ДА" were more than half of the registered voters, so that '
                "the new Constitution was finally adopted. The book counts 430 ballots that were not put in the box "
                "among the voters but not among the valid or invalid ballots.",
            ),
        ),
        ("2006-book", "2006-report"),
        "2006-10-28",
        "",
    ),
)


# reading ---------------------------------------------------------------------------------------------


@cache
def names_of(key: str) -> list[str]:
    """The municipalities of a scanned table in the order the book prints them, which the page images give."""
    return json.loads(NAMES_FILE.read_text())[key]


@cache
def data() -> dict[str, list[dict[str, Any]]]:
    return json.loads(DATA_FILE.read_text())


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(rik.SOURCE, url, suffix=Path(urllib.parse.urlparse(url).path).suffix.lower())


def extract(*only: str) -> None:
    """Read the scanned tables again with OCR and write ``rik_referendum.json``; name tables to read just those."""
    kept = data() if only and DATA_FILE.exists() else {}
    out: dict[str, list[dict[str, Any]]] = {k: v for k, v in kept.items() if k not in only}
    for key, t in TABLES.items():
        if only and key not in only:
            continue
        path = raw.cached(rik.SOURCE, FILES[t.book]).path
        words = rik_referendum_scans.read(path, t.pages)
        out[key], problems = rik_referendum_scans.rows(words, len(names_of(t.names)), t.base, t.decimals, t.fixes)
        print(key, len(out[key]), "rows")
        for problem in problems:
            print("  ", problem)
    DATA_FILE.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))


def text_2006() -> list[dict[str, Any]]:
    """The municipal rows of the 2006 book: registered, voters, invalid, valid and the two answers."""
    path = raw.cached(rik.SOURCE, FILES["2006-book"]).path
    lines = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    number = r"-?\d+(?:,\d+)?"
    row = re.compile(rf"^(\s*)(\S.*?)\s{{2,}}((?:{number}\s+)*{number})\s*$")
    one: dict[str, list[int]] = {}
    two: dict[str, list[int]] = {}
    table = 0
    pending = ""
    order: list[str] = []
    for line in lines.splitlines():
        if re.match(r"^\s*2\. Гласачка места и гласачки листићи\s*$", line):
            table = 2
        elif re.match(r"^\s*3\. Гласачи и резултати гласања\s*$", line):
            table = 3
        elif re.match(r"^\s*4\. ", line):
            table = 0
        if not table:
            continue
        if line.strip() == "санкција" or re.match(r"^санкција\s", line):
            line = "Заводи за извршење заводских санкција" + line.replace("санкција", "", 1)
        m = row.match(line)
        if not m:
            pending = line.strip()
            continue
        indent, name, figures = m[1], m[2], m[3].split()
        if pending.startswith("Заводи за извршење") and not name.startswith("Заводи"):
            name = "Заводи за извршење заводских санкција"
        pending = ""
        want = 8 if table == 2 else 7
        if len(figures) != want or (len(indent) == 0 and not name.startswith(("Иностранство", "Заводи"))):
            continue
        if name in ("Република Србија", "Централна Србија", "АП Војводина", "АП Косово и Метохија", "Град Београд"):
            continue
        values = [int(f.replace(",", "")) if "," not in f else 0 for f in figures]
        if table == 2:
            two[name] = [values[4], values[6]]  # invalid and valid ballots
        else:
            one[name] = [values[0], values[1], values[3], values[5]]
            order.append(name)
    rows = []
    for name in order:
        reg, voters, yes, no = one[name]
        invalid, valid = two[name]
        rows.append(
            {"name": name, "registered": reg, "voters": voters, "invalid": invalid, "valid": valid, "a": yes, "b": no}
        )
    return rows


# packages --------------------------------------------------------------------------------------------


def packages() -> list[str]:
    return [f"{rik.SOURCE}/{r.slug}" for r in REFERENDUMS if r.slug != "1992-05-31-referendum-symbols"]


def parse() -> None:
    for referendum in REFERENDUMS:
        if referendum.slug == "1992-05-31-referendum":
            build(referendum, REFERENDUMS[1]).write()
        elif referendum.slug != "1992-05-31-referendum-symbols":
            build(referendum).write()


def code_of(name: str) -> str | None:
    n = re.sub(r"\s*-\s*град$", "", name.strip())
    n = re.sub(r"^Град\s+", "", n)
    n = re.sub(r"\s*-\s*", "-", n) if n.startswith("Београд") else n
    return rik_predsednik.CODES.get(ALIASES.get(n, n))


def _unit(p: Package, code: str, kind: str, place: str | None, name: str) -> str:
    unit = rik.key("unit", code)
    if any(row["id"] == unit for row in p.rows.get("reporting_unit", [])):
        return unit
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=rik.SERBIA,
        scheme_code="rs.opstina",
        code=code,
        area_relation="within" if place else "unlocated",
        area_id=place,
        kind=kind,
    )
    p.name(unit, name, "sr", "official")
    return unit


Figure = tuple[int | None, int, int, list[float] | None]  # option position, measure, value, page and box


def _municipalities(r: Referendum, q: Question) -> list[tuple[str, str, list[Figure]]]:
    """Each municipality's name, unit kind, and figures."""
    found: list[tuple[str, str, list[Figure]]] = []
    figures: list[Figure]
    if q.table is None:
        for row in text_2006():
            name = row["name"]
            kind = (
                "abroad_mission"
                if name.startswith("Иностранство")
                else "prison"
                if name.startswith("Заводи")
                else "standard"
            )
            figures = [
                (None, REGISTERED, row["registered"], None),
                (None, VOTERS, row["voters"], None),
                (None, INVALID, row["invalid"], None),
                (None, VALID, row["valid"], None),
                (0, VOTES, row["a"], None),
                (1, VOTES, row["b"], None),
            ]
            found.append((name, kind, figures))
        return found
    table = TABLES[q.table]
    names = names_of(table.names)
    for row in data()[q.table]:
        figures = [
            (None, REGISTERED, row["registered"][0], row["registered"][1]),
            (None, VOTERS, row["voters"][0], row["voters"][1]),
            (0, VOTES, row["a"][0], row["a"][1]),
            (1, VOTES, row["b"][0], row["b"][1]),
        ]
        found.append((names[row["n"] - 1], "standard", figures))
    return found


def build(r: Referendum, *more: Referendum) -> Package:
    """One package for a referendum; two held the same day share it, so that they cite the one book once."""
    all_referendums = (r, *more)
    p = Package(
        f"{rik.SOURCE}/{r.slug}",
        rik.SOURCE,
        f"The {r.title[:4]} {r.title[5:]}, by municipality, from the Republic Electoral Commission's archive",
        depends=[f"{rik.SOURCE}/geo", f"{rik.SOURCE}/registry"],
    )
    p.external |= {rik.SERBIA, COUNTRY}
    cited: set[str] = set()
    for x in all_referendums:
        for name in x.files:
            if name not in cited:
                cited.add(name)
                p.artifact(raw.cached(rik.SOURCE, FILES[name]), rik.SOURCE_ID)
    reasons: list[str] = []
    for x in all_referendums:
        _referendum(p, x, reasons)
    if reasons:
        p.accept(
            "accounting.declared",
            "The municipal rows do not add up to the Republic's printed totals (rows against printed): "
            + " | ".join(reasons)
            + ". Every figure is stored as printed.",
        )
    return p


def _referendum(p: Package, r: Referendum, reasons: list[str]) -> None:
    election = rik.key("election", r.slug)
    p.add(
        "election", id=election, polity_id=rik.SERBIA, kind="referendum", date_from=r.day, date_to=r.last, status="held"
    )
    p.name(election, r.title, "en", "official")
    p.name(election, r.native, "sr", "native", False)
    p.add("slug", path=r.path, entity_id=election, is_current=True)
    multiple = len(r.questions) > 1
    for q in r.questions:
        contest = rik.key("contest", r.slug, q.leaf)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            office_id=None,
            body_id=None,
            magnitude=None,
            round_number=1,
            previous_round_id=None,
            electoral_system_id=rik.SYSTEM_OF["referendum"],
            default_result_kind="counted",
        )
        p.name(contest, q.title, "en", "official")
        p.name(contest, q.native, "sr", "native", False)
        p.add("slug", path=f"{r.path}/{q.leaf}", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type=q.type)
        result_set = rik.key("result_set", r.slug, q.leaf)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=rik.SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        method = (METHOD_TEXT if q.table is None else METHOD_SCAN) + " " + q.note
        p.add(
            "note", id=rik.key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
        )
        options: list[str] = []
        for position, (en, sr) in enumerate(q.options):
            option = rik.key("option", r.slug, q.leaf, position + 1)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position + 1,
                ballot_label=sr,
                party_id=None,
            )
            p.name(option, en, "en", "official")
            if position in q.outcomes:
                p.add("outcome", result_set_id=result_set, option_id=option, kind=q.outcomes[position])
        for measure, value in q.declared.items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option, value in zip(options, q.votes, strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)

        rows: list[tuple[str, str | None, int, int]] = []
        locators: list[tuple[str, str | None, int, str, float, float, float, float]] = []
        municipalities = _municipalities(r, q)
        used = {int(f[3][0]) for _, _, figures in municipalities for f in figures if f[3]}
        pages = _pages(p, FILES[r.files[0]], used)
        scale = 100 / 72
        seen: set[str] = set()
        summed: dict[tuple[int | None, int], int] = {}
        for name, kind, figures in municipalities:
            code = code_of(name) if kind == "standard" else None
            slug = code or ("~" + rik._norm(name))
            if slug in seen:
                raise ValueError(f"{r.slug}: {name} appears twice as {slug}")
            seen.add(slug)
            place = rik.area("rs.opstina", code) if code else (COUNTRY if kind == "standard" else None)
            unit = _unit(p, f"{r.prefix}:{slug}", kind, place, name)
            for position, measure, value, box in figures:
                option = None if position is None else options[position]
                rows.append((unit, option, measure, value))
                summed[(position, measure)] = summed.get((position, measure), 0) + value
                if box:
                    page, x0, y0, x1, y1 = box
                    locators.append(
                        (unit, option, measure, pages[int(page)], x0 * scale, y0 * scale, x1 * scale, y1 * scale)
                    )
        if q.table is not None:
            # the Republic's invalid and used ballots are printed for the Republic only, so they sit in a unit of their own
            national = _unit(p, f"{r.prefix}:total:{q.leaf}", "aggregate", COUNTRY, "Република Србија")
            for measure in (INVALID, CAST):
                rows.append((national, None, measure, q.declared[measure]))
                summed[(None, measure)] = q.declared[measure]
        _values(p, result_set, rows, locators, DIGITIZED if q.table else 0)
        differences = []
        label = {
            REGISTERED: "registered voters",
            VOTERS: "voters",
            INVALID: "invalid ballots",
            VALID: "valid ballots",
            CAST: "ballots used",
        }
        for measure, value in q.declared.items():
            got = summed.get((None, measure))
            if got != value:
                differences.append(
                    f"{label[measure]} {got:,} in the municipalities against {value:,}"
                    if got is not None
                    else f"{label[measure]} have no municipal figure"
                )
        for position, value in enumerate(q.votes):
            got = summed.get((position, VOTES), 0)
            if got != value:
                differences.append(f"votes for {q.options[position][0]} {got:,} against {value:,}")
        if differences:
            reasons.append((f"{q.title}: " if multiple else "") + "; ".join(differences))


_PAGES: dict[tuple[str, str], dict[int, str]] = {}


def _pages(p: Package, url: str, used: set[int]) -> dict[int, str]:
    """The ids of the cited pages of a scan, registering each page of it once per package."""
    known = _PAGES.setdefault((p.key, url), {})
    new = used - known.keys()
    if new:
        known.update(rik_poslanici._pages(p, url, new))
    return known


def _values(
    p: Package,
    result_set: str,
    rows: list[tuple[str, str | None, int, int]],
    locators: list[tuple[str, str | None, int, str, float, float, float, float]],
    flags: int,
) -> None:
    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
            schema=["unit_id", "option_id", "measure", "value"],
            orient="row",
            schema_overrides={"option_id": pl.String},
        ).with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(flags, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    if locators:
        p.frame(
            "value_locator",
            pl.DataFrame(
                locators,
                schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
                orient="row",
                schema_overrides={"option_id": pl.String},
            ).with_columns(
                result_set_id=pl.lit(result_set),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                source_id=pl.lit(None, pl.String),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )
