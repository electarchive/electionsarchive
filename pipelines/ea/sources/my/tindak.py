"""Malaysia's general and state elections before 2008, by constituency, from Tindak Malaysia's transcription of the
Election Commission's printed reports and the Gazette result notices.

The Election Commission's open data (`my.spr`) begins in 2008. Its printed reports for the earlier elections (the
"Report on the Parliamentary and State Elections" and the "Laporan Pilihan Raya Umum") are not online at any official
host this pipeline can reach. Tindak Malaysia, an electoral-reform group, transcribed them, the Gazette notices, and
the Commission's delimitation reports into one CSV file per election and chamber, and publishes them under CC BY 4.0.
This module reads those files from one archive of the repository and builds one package per election.
"""

import csv
import functools
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.my import spr

SOURCE = "my.tindak"
ARCHIVE = "https://github.com/TindakMalaysia/HISTORICAL-ELECTION-RESULTS/archive/refs/heads/main.zip"
REPOSITORY = "https://github.com/TindakMalaysia/HISTORICAL-ELECTION-RESULTS"
ROOT = "HISTORICAL-ELECTION-RESULTS-main"
MALAYSIA = spr.MALAYSIA
VOTES, REGISTERED, VALID, INVALID = 1, 2, 4, 5

# The source's spellings of each state, mapped to the codes `my.spr` gives the states it loaded.
STATES = {
    "JOHOR": "JOHOR",
    "JOHORE": "JOHOR",
    "KEDAH": "KEDAH",
    "KELANTAN": "KELANTAN",
    "MALACCA": "MELAKA",
    "MELAKA": "MELAKA",
    "NEGRI SEMBILAN": "NEGERI SEMBILAN",
    "NEGERI SEMBILAN": "NEGERI SEMBILAN",
    "PAHANG": "PAHANG",
    "PERAK": "PERAK",
    "PERLIS": "PERLIS",
    "PENANG": "PULAU PINANG",
    "PULAU PINANG": "PULAU PINANG",
    "SABAH": "SABAH",
    "SARAWAK": "SARAWAK",
    "SELANGOR": "SELANGOR",
    "TRENGGANU": "TERENGGANU",
    "TERENGGANU": "TERENGGANU",
    "WILAYAH PERSEKUTUAN KUALA LUMPUR": "W.P KUALA LUMPUR",
    "WILAYAH PERSEKUTUAN LABUAN": "W.P LABUAN",
    "WILAYAH PERSEKUTUAN PUTRAJAYA": "W.P PUTRAJAYA",
}

# Spellings of one party the source prints two ways, and the "direct" candidates Barisan Nasional fielded in its own
# name rather than through a member party.
PARTIES = {"K.I.T.A": "KITA", "DIRECT": "BN"}

SUMMARY_NOTE = (
    "Polling days are those of the federal and Sabah election summaries in Tindak Malaysia's repository, which cite "
    "the Election Commission's reports."
)


@dataclass(frozen=True)
class Event:
    id: str  # the package name and the election's address under my/
    name: str
    first: date
    last: date
    precision: str  # "day" or "year"
    files: tuple[str, ...]  # paths under the repository root
    note: str | None = None


def _y(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def _sarawak(year: int, file: str) -> Event:
    first, last = _y(year)
    return Event(
        f"{year}-sarawak-state",
        f"{year} Sarawak state election",
        first,
        last,
        "year",
        (file,),
    )


def _sabah(id: str, year: int, first: date, last: date, file: str, note: str | None = None) -> Event:
    return Event(id, f"{year} Sabah state election", first, last, "day", (file,), note)


EVENTS = [
    Event(
        "1959-05-20-state",
        "1959 Malayan state elections",
        date(1959, 5, 20),
        date(1959, 6, 27),
        "day",
        ("1959-ELECTION-RESULTS/MALAYA_1959_DUN_RESULTS.csv",),
        "The eleven state assemblies of the Federation of Malaya were elected state by state between 20 May "
        "(Perlis and Kedah) and 27 June 1959 (Pahang), before the federal election of 19 August.",
    ),
    Event(
        "1959-08-19-general",
        "1959 Malayan general election",
        date(1959, 8, 19),
        date(1959, 8, 19),
        "day",
        ("1959-ELECTION-RESULTS/MALAYA_1959_PARLIAMENT_RESULTS.csv",),
    ),
    Event(
        "1964-04-25-general",
        "1964 Malaysian general election",
        date(1964, 4, 25),
        date(1964, 4, 25),
        "day",
        (
            "1964-ELECTION-RESULTS/MALAYSIA_1964_PARLIAMENT_RESULTS.csv",
            "1964-ELECTION-RESULTS/MALAYSIA_1964_DUN_RESULTS.csv",
        ),
        "Parliamentary and concurrent state assembly seats of the States of Malaya. Sabah, Sarawak, and Singapore "
        "did not elect their members of the Dewan Rakyat directly in 1964.",
    ),
    Event(
        "1969-05-10-general",
        "1969 Malaysian general election",
        date(1969, 5, 10),
        date(1970, 7, 4),
        "day",
        (
            "1969-ELECTION-RESULTS/MALAYSIA_1969_PARLIAMENT_RESULTS.csv",
            "1969-ELECTION-RESULTS/MALAYSIA_1969_DUN_RESULTS.csv",
        ),
        "Parliamentary seats and the concurrent state assembly seats of the States of Malaya and Sarawak. Polling "
        "began on 10 May 1969 and was suspended after the riots of 13 May; it resumed in Sabah from 21 to 27 June "
        "1970 and in Sarawak from 6 June to 4 July 1970. The poll in Malacca Selatan, countermanded after a "
        "candidate's death, was held on 30 January 1971.",
    ),
    _sabah(
        "1967-04-08-state",
        1967,
        date(1967, 4, 8),
        date(1967, 4, 23),
        "1967-SABAH-STATE-ELECTIONS/SABAH_1967_DUN_RESULTS.csv",
    ),
    _sabah(
        "1971-10-05-state",
        1971,
        date(1971, 10, 5),
        date(1971, 10, 5),
        "1971-SABAH-STATE-ELECTIONS/SABAH_1971_DUN_RESULTS.csv",
        "Every seat was returned unopposed on nomination day, 5 October 1971, so no poll was held.",
    ),
    Event(
        "1974-08-24-general",
        "1974 Malaysian general election",
        date(1974, 8, 24),
        date(1974, 9, 14),
        "day",
        (
            "1974-ELECTION-RESULTS/MALAYSIA_1974_PARLIAMENT_RESULTS.csv",
            "1974-ELECTION-RESULTS/MALAYSIA_1974_DUN_RESULTS.csv",
        ),
        "Parliamentary seats and the concurrent state assembly seats of Peninsular Malaysia and Sarawak. Polling in "
        "Sabah ran to 7 September and in Sarawak to 14 September 1974.",
    ),
    _sabah(
        "1976-04-05-state",
        1976,
        date(1976, 4, 5),
        date(1976, 4, 14),
        "1976-SABAH-STATE-ELECTIONS/SABAH_1976_DUN_RESULTS.csv",
    ),
    Event(
        "1978-kelantan-state",
        "1978 Kelantan state election",
        *_y(1978),
        "year",
        ("1978-KELANTAN-STATE-ELECTIONS/KELANTAN_1978_DUN_RESULTS.csv",),
        "The snap election to the Kelantan State Legislative Assembly after the emergency rule of 1977, held months "
        "before the general election.",
    ),
    Event(
        "1978-07-08-general",
        "1978 Malaysian general election",
        date(1978, 7, 8),
        date(1978, 7, 22),
        "day",
        (
            "1978-ELECTION-RESULTS/MALAYSIA_1978_PARLIAMENT_RESULTS.csv",
            "1978-ELECTION-RESULTS/MALAYSIA_1978_DUN_RESULTS.csv",
        ),
        "Parliamentary seats and the concurrent state assembly seats of Peninsular Malaysia except Kelantan, which "
        "had elected its assembly earlier in 1978. Polling in Sabah ran to 15 July and in Sarawak to 22 July 1978.",
    ),
    _sarawak(1979, "1979-SARAWAK-STATE-ELECTIONS/SARAWAK_1979_DUN_RESULTS.csv"),
    _sabah(
        "1981-03-23-state",
        1981,
        date(1981, 3, 23),
        date(1981, 3, 28),
        "1981-SABAH-STATE-ELECTIONS/1981_SABAH_DUN_RESULTS.csv",
    ),
    Event(
        "1982-04-22-general",
        "1982 Malaysian general election",
        date(1982, 4, 22),
        date(1982, 4, 26),
        "day",
        (
            "1982-ELECTION-RESULTS/MALAYSIA_1982_PARLIAMENT_RESULTS.csv",
            "1982-ELECTION-RESULTS/MALAYSIA_1982_DUN_RESULTS.csv",
        ),
        "Parliamentary seats and the concurrent state assembly seats of Peninsular Malaysia. Polling in Sabah and "
        "Sarawak ran from 22 to 26 April 1982.",
    ),
    _sarawak(1983, "1983-SARAWAK-STATE-ELECTIONS/SARAWAK_1983_DUN_RESULTS.csv"),
    _sabah(
        "1985-04-20-state",
        1985,
        date(1985, 4, 20),
        date(1985, 4, 21),
        "1985-SABAH-STATE-ELECTIONS/1985_SABAH_DUN_RESULTS.csv",
    ),
    _sabah(
        "1986-05-05-state",
        1986,
        date(1986, 5, 5),
        date(1986, 5, 6),
        "1986-SABAH-STATE-ELECTIONS/1986_SABAH_DUN_RESULTS.csv",
    ),
    Event(
        "1986-08-02-general",
        "1986 Malaysian general election",
        date(1986, 8, 2),
        date(1986, 8, 3),
        "day",
        (
            "1986-ELECTION-RESULTS/MALAYSIA_1986_PARLIAMENT_RESULTS.csv",
            "1986-ELECTION-RESULTS/MALAYSIA_1986_DUN_RESULTS.csv",
        ),
        "Parliamentary seats and the concurrent state assembly seats of Peninsular Malaysia. Polling day was 3 "
        "August 1986; Sabah and Sarawak polled on 2 and 3 August.",
    ),
    _sarawak(1987, "1987-SARAWAK-STATE-ELECTIONS/SARAWAK_1987_DUN_RESULTS.csv"),
    _sabah(
        "1990-07-16-state",
        1990,
        date(1990, 7, 16),
        date(1990, 7, 17),
        "1990-SABAH-STATE-ELECTIONS/1990_SABAH_DUN_RESULTS.csv",
    ),
    Event(
        "1990-10-20-general",
        "1990 Malaysian general election",
        date(1990, 10, 20),
        date(1990, 10, 21),
        "day",
        (
            "1990-ELECTION-RESULTS/MALAYSIA_1990_PARLIAMENT_RESULTS.csv",
            "1990-ELECTION-RESULTS/MALAYSIA_1990_DUN_RESULTS.csv",
        ),
        "Parliamentary seats and the concurrent state assembly seats of Peninsular Malaysia. Polling day was 21 "
        "October 1990; Sabah and Sarawak polled on 20 and 21 October.",
    ),
    _sarawak(1991, "1991-SARAWAK-STATE-ELECTIONS/SARAWAK_1991_DUN_RESULTS.csv"),
    _sabah(
        "1994-02-18-state",
        1994,
        date(1994, 2, 18),
        date(1994, 2, 19),
        "1994-SABAH-STATE-ELECTIONS/1994_SABAH_DUN_RESULTS.csv",
    ),
    Event(
        "1995-04-24-general",
        "1995 Malaysian general election",
        date(1995, 4, 24),
        date(1995, 4, 25),
        "day",
        (
            "1995-ELECTION-RESULTS/MALAYSIA_1995_PARLIAMENT_RESULTS.csv",
            "1995-ELECTION-RESULTS/MALAYSIA_1995_DUN_RESULTS.csv",
        ),
        "Parliamentary seats and the concurrent state assembly seats of Peninsular Malaysia. Polling day was 25 "
        "April 1995; Sabah and Sarawak polled on 24 and 25 April.",
    ),
    _sarawak(1996, "1996-SARAWAK-STATE-ELECTIONS/SARAWAK_1996_DUN_RESULTS.csv"),
    _sabah(
        "1999-03-12-state",
        1999,
        date(1999, 3, 12),
        date(1999, 3, 13),
        "1999-SABAH-STATE-ELECTIONS/1999_SABAH_DUN_RESULTS.csv",
    ),
    Event(
        "1999-11-29-general",
        "1999 Malaysian general election",
        date(1999, 11, 29),
        date(1999, 11, 29),
        "day",
        (
            "1999-ELECTION-RESULTS/MALAYSIA_1999_PARLIAMENT_RESULTS.csv",
            "1999-ELECTION-RESULTS/MALAYSIA_1999_DUN_RESULTS.csv",
        ),
        "Parliamentary seats and the concurrent state assembly seats of Peninsular Malaysia.",
    ),
    _sarawak(2001, "2001-SARAWAK-STATE-ELECTIONS/SARAWAK_2001_DUN_RESULTS.csv"),
    Event(
        "2004-03-21-general",
        "2004 Malaysian general election",
        date(2004, 3, 21),
        date(2004, 3, 21),
        "day",
        (
            "2004-ELECTION-RESULTS/MALAYSIA_2004_PARLIAMENT_RESULTS.csv",
            "2004-ELECTION-RESULTS/2004_SABAH_DUN_RESULTS.csv",
        ),
        "Parliamentary seats and the concurrent Sabah state assembly seats. The concurrent assembly elections of "
        "the Peninsular states are not in the source yet.",
    ),
    _sarawak(2006, "2006-SARAWAK-STATE-ELECTIONS/SARAWAK_2006_DUN_Results.csv"),
]

# Parliamentary shapes in the repository, each with the span its constituencies were in force: the 1958 delimitation
# under its first numbering (used in 1959), and under the numbering of 1964 (used in 1964 and 1969).
SHAPES = {
    "1959-ELECTION-RESULTS/MALAYA_PARLIAMENT_Boundaries_1959.geojson": (
        "1959-08-19-general",
        date(1959, 8, 19),
        date(1964, 4, 25),
    ),
    "1964-ELECTION-RESULTS/MALAYSIA_PARLIAMENT_Boundaries_1964.geojson": (
        "1964-04-25-general",
        date(1964, 4, 25),
        date(1974, 8, 24),
    ),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SYSTEM = key("electoral_system", "fptp")


def fetch() -> None:
    raw.fetch(SOURCE, ARCHIVE, suffix=".zip")


@functools.cache
def archive() -> zipfile.ZipFile:
    return zipfile.ZipFile(raw.cached(SOURCE, ARCHIVE).path)


def member(path: str) -> bytes:
    return archive().read(f"{ROOT}/{path}")


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ").replace("﻿", "")).strip()


def number(text: str | None) -> int | None:
    value = clean(text).replace(",", "")
    if value in ("", "-", "N/A", "NA"):
        return None
    return int(value)


def code(text: str, chamber: str) -> str:
    found = re.fullmatch(r"(P|NO|N|S)\s*\.?\s*(\d+)", clean(text).upper())
    if found is None:
        raise ValueError(f"unreadable constituency code {text!r}")
    letter = {"NO": "N"}.get(found[1], found[1])
    if (letter == "P") != (chamber == "parliament"):
        raise ValueError(f"{text!r} is not a {chamber} code")
    return f"P.{int(found[2]):03d}" if letter == "P" else f"{letter}.{int(found[2]):02d}"


@dataclass
class Candidate:
    name: str
    party: str | None  # the party abbreviation as printed; None for an independent
    bloc: str | None  # the coalition the column falls under, where it differs from the party
    symbol: str | None  # an independent's ballot symbol, where the source gives one
    sex: str | None
    votes: int | None


@dataclass
class Contest:
    event: str
    file: str
    line: int
    state: str
    parliament: tuple[str, str]
    assembly: tuple[str, str] | None
    winning: str
    uncontested: bool
    registered: int | None
    majority: int | None
    turnout: float | None
    issued: int | None
    rejected: int | None
    valid: int | None
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def seat(self) -> tuple[str, str]:
        return self.assembly or self.parliament

    @property
    def tacit(self) -> bool:
        return len(self.candidates) == 1

    @property
    def id(self) -> str:
        return key("contest", self.event, self.state, *self.parliament, *(self.assembly or ()))


def _column(header: list[str], *tests: str) -> int | None:
    for test in tests:
        for index, name in enumerate(header):
            if re.fullmatch(test, clean(name).upper()):
                return index
    return None


def read(event: Event, path: str) -> list[Contest]:
    rows = list(csv.reader(io.StringIO(member(path).decode("utf-8-sig"))))
    header = [clean(name).upper() for name in rows[0]]
    assembly = _column(header, "STATE CONSTITUENCY CODE") is not None
    at = {
        "state": _column(header, "STATE"),
        "pcode": _column(header, "PARLIAMENTARY CONSTITUENCY CODE", "PARLIAMENTARY CODE"),
        "pname": _column(header, "PARLIAMENTARY CONSTITUENCY NAME", "PARLIAMENTARY SEAT", "PARLIAMENTARY NAME"),
        "ncode": _column(header, "STATE CONSTITUENCY CODE"),
        "nname": _column(header, "STATE CONSTITUENCY NAME"),
        "winning": _column(header, "WINNING PARTY"),
        "uncontested": _column(header, "UNCONTESTED VICTORY"),
        "registered": _column(header, "TOTAL ELECTORATE"),
        "majority": _column(header, "WINNING MAJORITY"),
        "turnout": _column(header, r"TURNOUT \(%\)"),
        "issued": _column(header, "TOTAL BALLOTS? ISSUED"),
        "rejected": _column(header, "(TOTAL )?REJECTED VOTES"),
        "valid": _column(header, "TOTAL VALID VOTES"),
    }
    for name, index in at.items():
        if index is None and (assembly or name not in ("ncode", "nname")):
            raise ValueError(f"{path}: no {name} column")
    groups = [index for index, name in enumerate(header) if re.search(r"CANDIDATE( NAME)?$", name)]
    for index in groups:
        tail = header[index + 1 : index + 5]
        if not (tail[0].endswith("SEX") and tail[1].endswith("AGE") and tail[2].endswith("VOTE")):
            raise ValueError(f"{path}: unexpected columns after {header[index]}: {tail}")
        if not header[index + 4].endswith("DEPOSIT"):
            raise ValueError(f"{path}: unexpected columns after {header[index]}: {tail}")
    out: list[Contest] = []
    for line, row in enumerate(rows[1:], start=2):
        if not any(clean(cell) for cell in row):
            continue

        def get(name: str) -> str:
            index = at[name]
            return "" if index is None else clean(row[index])

        state = STATES[get("state").upper()]
        parliament = (code(get("pcode"), "parliament"), get("pname").upper())
        seat = (code(get("ncode"), "assembly"), get("nname").upper()) if assembly else None
        turnout = get("turnout").replace("%", "")
        contest = Contest(
            event=event.id,
            file=path,
            line=line,
            state=state,
            parliament=parliament,
            assembly=seat,
            winning=get("winning").upper(),
            uncontested=get("uncontested").upper() == "Y",
            registered=number(get("registered")),
            majority=number(get("majority")),
            turnout=float(turnout) if turnout else None,
            issued=number(get("issued")),
            rejected=number(get("rejected")),
            valid=number(get("valid")),
        )
        for index in groups:
            name = clean(row[index])
            if not name:
                continue
            bloc = re.sub(r"\s*\(\d+\)$", "", clean(header[index - 1]))
            printed = clean(row[index - 1]).upper()
            symbol = None
            if bloc.startswith(("INDEPENDENT", "BEBAS")) or printed.startswith(("INDEPENDENT", "BEBAS")):
                party = None
                found = re.fullmatch(r"(?:INDEPENDENT|BEBAS)\s*-\s*(.+)", printed)
                symbol = found[1].title() if found else None
            else:
                party = printed or bloc
                found = re.fullmatch(r"(.+?)\s+-\s+(.+)", party)
                if found is not None:
                    bloc, party = found[1], found[2]
                party = PARTIES.get(party, re.sub(r"\s*\(\d+\)$", "", party))
                bloc = PARTIES.get(bloc, bloc)
            sex = clean(row[index + 1]).lower() or None
            contest.candidates.append(
                Candidate(
                    name=name,
                    party=party,
                    bloc=None if party is None or bloc in (party, "OTHER PARTY", "OTHER PARTIES") else bloc,
                    symbol=symbol,
                    sex=sex if sex in ("male", "female") else None,
                    votes=number(row[index + 3]),
                )
            )
        if not contest.candidates:
            raise ValueError(f"{path}:{line}: a contest with no candidates")
        out.append(contest)
    return out


@functools.cache
def contests(event_id: str) -> list[Contest]:
    event = next(e for e in EVENTS if e.id == event_id)
    out = [contest for path in event.files for contest in read(event, path)]
    seen: set[str] = set()
    for contest in out:
        if contest.id in seen:
            raise ValueError(f"{event.id}: two rows for {contest.state} {contest.seat}")
        seen.add(contest.id)
    return out


def all_contests() -> list[Contest]:
    return [contest for event in EVENTS for contest in contests(event.id)]


# Constituencies of the 2004 general election and the 2006 Sarawak state election that `my.spr` already loaded under
# the same code and name, from its 2008 and later elections. Each is referenced by the id `my.spr` gave it, not made
# again. The table was drawn up by matching code and name against the areas `my.spr` holds.
SPR_AREAS = Path(__file__).with_name("tindak_spr_areas.tsv")
REUSE_FROM = date(2004, 1, 1)


@functools.cache
def loaded() -> dict[tuple[str, str, str, str], str]:
    """(scheme, state, code, folded name) -> the `my.spr` area id, for the constituencies listed in `SPR_AREAS`."""
    out: dict[tuple[str, str, str, str], str] = {}
    for row in csv.DictReader(SPR_AREAS.read_text().splitlines(), delimiter="\t"):
        out[(row["scheme"], row["state"], row["code"], fold(row["name"]))] = row["area_id"]
    return out


def fold(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def parliament_area(event: Event, contest: Contest) -> tuple[str, bool]:
    """The parliamentary constituency's area id, and whether `my.spr` made it."""
    if event.first >= REUSE_FROM:
        found = loaded().get(("my.parlimen", contest.state, contest.parliament[0], fold(contest.parliament[1])))
        if found is not None:
            return found, True
    return key("area", "my.parlimen", contest.state, *contest.parliament), False


def assembly_area(event: Event, contest: Contest) -> tuple[str, bool]:
    assert contest.assembly is not None
    if event.first >= REUSE_FROM:
        found = loaded().get(("my.dun", contest.state, contest.assembly[0], fold(contest.assembly[1])))
        if found is not None:
            return found, True
    return key("area", "my.dun", contest.state, *contest.assembly, *contest.parliament), False


def by_id(event_id: str) -> Event:
    return next(e for e in EVENTS if e.id == event_id)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Malaysia's parliamentary and state constituencies of 1959 to 2006 as Tindak Malaysia transcribes them from "
        "the Election Commission's reports, with the 1958 parliamentary constituencies of Malaya drawn",
    )
    p.external.update([MALAYSIA, "my.parlimen", "my.dun"])
    p.add(
        "source",
        id=SOURCE_ID,
        **SOURCE_ROW,
    )
    p.artifact(raw.cached(SOURCE, ARCHIVE), SOURCE_ID)
    made: dict[str, tuple[str, str]] = {}
    for event in EVENTS:
        for contest in contests(event.id):
            pid, reused = parliament_area(event, contest)
            if reused:
                p.external.add(pid)
            elif pid not in made:
                made[pid] = (contest.state, contest.parliament[0])
                p.add("area", id=pid, scheme_code="my.parlimen")
                p.add("area_code", area_id=pid, code=contest.parliament[0])
                p.name(pid, spr.title(contest.parliament[1]), "ms", "official")
                parent = spr.state_area(contest.state)
                p.external.add(parent)
                p.add("area_parent", area_id=pid, parent_id=parent)
            if contest.assembly is None:
                continue
            nid, reused = assembly_area(event, contest)
            if reused:
                p.external.add(nid)
            elif nid not in made:
                made[nid] = (contest.state, contest.assembly[0])
                p.add("area", id=nid, scheme_code="my.dun")
                p.add("area_code", area_id=nid, code=contest.assembly[0])
                p.name(nid, spr.title(contest.assembly[1]), "ms", "official")
                p.add("area_parent", area_id=nid, parent_id=pid)
    for path, (event_id, first, last) in SHAPES.items():
        drawn = shapes(path)
        event = by_id(event_id)
        for contest in contests(event_id):
            if contest.assembly is not None:
                continue
            pid, reused = parliament_area(event, contest)
            shape = drawn.get((contest.state, fold(contest.parliament[1])))
            if reused or shape is None:
                continue
            p.add(
                "area_geometry",
                area_id=pid,
                valid_from=first,
                valid_to=last,
                geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                ),
                quality="digitized",
                source_id=SOURCE_ID,
            )
    return p


@functools.cache
def shapes(path: str) -> dict[tuple[str, str], shapely.Geometry]:
    """(state, folded name) -> the constituency's outline, its parts unioned."""
    found: dict[tuple[str, str], list[shapely.Geometry]] = defaultdict(list)
    for feature in json.loads(member(path))["features"]:
        props = feature["properties"]
        state = STATES.get(clean(props.get("NEGERI")).upper())
        if state is None or not props.get("PARLIMEN"):
            continue
        found[(state, fold(props["PARLIMEN"]))].append(shapely.geometry.shape(feature["geometry"]))
    out: dict[tuple[str, str], shapely.Geometry] = {}
    for place, parts in found.items():
        merged = shapely.make_valid(shapely.union_all(parts))
        polygons = [g for g in shapely.get_parts(merged) if shapely.get_type_id(g) in (3, 6)]
        flat = [part for g in polygons for part in shapely.get_parts(g)]
        out[place] = shapely.set_srid(shapely.MultiPolygon(flat), 4326)
    return out


SOURCE_ROW = {
    "kind": "community",
    "publisher": "Tindak Malaysia",
    "title": "HISTORICAL-ELECTION-RESULTS: historical election results of Malaysia at the state and federal level, "
    "transcribed from the Election Commission's reports and the Gazette",
    "url": REPOSITORY,
    "license": "CC BY 4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "attribution": "Tindak Malaysia, HISTORICAL-ELECTION-RESULTS (CC BY 4.0), after the reports of the Election "
    "Commission of Malaysia",
}


@functools.cache
def parties() -> dict[str, Counter[str]]:
    """Party abbreviation -> the coalitions the source files it under, with counts."""
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for contest in all_contests():
        for candidate in contest.candidates:
            if candidate.party is not None:
                out[candidate.party][candidate.bloc or ""] += 1
    return dict(out)


def party(abbreviation: str | None) -> str | None:
    return None if abbreviation is None else key("party", abbreviation)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties standing in Malaysia's general and state elections of 1959 to 2006, and the first-past-the-post "
        "system, from Tindak Malaysia's transcription of the Election Commission's reports",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(MALAYSIA)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=MALAYSIA,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each constituency elects one member by plurality, with no runoff.",
        lang="en",
    )
    for abbreviation in sorted(parties()):
        id = key("party", abbreviation)
        p.add("party", id=id, kind="party", polity_id=MALAYSIA)
        p.add("external_id", entity_id=id, scheme="my.tindak.party", value=abbreviation)
        p.name(id, abbreviation, "ms", "abbreviation")
    return p


@dataclass
class Audit:
    contests: int = 0
    valid_ok: int = 0
    valid_bad: list[str] = field(default_factory=list)
    accounting_ok: int = 0
    accounting_bad: list[str] = field(default_factory=list)
    majority_ok: int = 0
    majority_bad: list[str] = field(default_factory=list)
    winner_ok: int = 0
    winner_bad: list[str] = field(default_factory=list)
    caveats: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))


def winner(contest: Contest) -> Candidate | None:
    if contest.tacit:
        return contest.candidates[0]
    ranked = sorted(contest.candidates, key=lambda c: c.votes or 0, reverse=True)
    if len(ranked) > 1 and (ranked[0].votes or 0) == (ranked[1].votes or 0):
        return None
    return ranked[0]


def audit(contest: Contest, found: Audit) -> None:
    found.contests += 1
    label = f"{contest.event} {contest.state} {contest.seat[0]} {contest.seat[1]}"
    if contest.tacit:
        return
    votes = sum(c.votes or 0 for c in contest.candidates)
    if contest.valid is not None:
        if votes == contest.valid:
            found.valid_ok += 1
        else:
            found.valid_bad.append(f"{label}: printed valid {contest.valid}, candidate votes sum to {votes}")
            found.caveats[contest.id].append(
                f"The candidates' votes add up to {votes:,}, but the total of valid votes is printed as "
                f"{contest.valid:,}. Both are stored as the source gives them."
            )
    if contest.valid is not None and contest.rejected is not None and contest.issued is not None:
        if contest.valid + contest.rejected <= contest.issued:
            found.accounting_ok += 1
        else:
            found.accounting_bad.append(
                f"{label}: valid {contest.valid} + rejected {contest.rejected} > issued {contest.issued}"
            )
    ranked = sorted((c.votes or 0 for c in contest.candidates), reverse=True)
    if contest.majority is not None and len(ranked) > 1:
        if contest.majority == ranked[0] - ranked[1]:
            found.majority_ok += 1
        else:
            found.majority_bad.append(
                f"{label}: printed majority {contest.majority}, votes give {ranked[0] - ranked[1]}"
            )
            found.caveats[contest.id].append(
                f"The printed majority of {contest.majority:,} differs from the {ranked[0] - ranked[1]:,} that the "
                "candidates' votes give. Both are as the source gives them."
            )
    top = winner(contest)
    tail = contest.winning.split(" - ")[-1].strip()
    if top is None:
        found.winner_bad.append(f"{label}: a tie for first place")
    elif tail.startswith(("INDEPENDENT", "BEBAS")):
        if top.party is None:
            found.winner_ok += 1
        else:
            found.winner_bad.append(f"{label}: printed winning party {contest.winning}, most votes {top.party}")
    elif top.party == tail or top.bloc == tail or (top.party or "") in contest.winning:
        found.winner_ok += 1
    else:
        found.winner_bad.append(f"{label}: printed winning party {contest.winning}, most votes {top.party} {top.name}")


METHOD = (
    "Counted per constituency and published by the Election Commission of Malaysia in its report on the election "
    "and in the Gazette. The figures are Tindak Malaysia's transcription of those reports, from its "
    "HISTORICAL-ELECTION-RESULTS repository (CC BY 4.0): each candidate's votes, the electorate, the rejected "
    "ballots, and the total of valid votes, as transcribed. The source also gives ballots issued, the turnout "
    "percentage, and the winner's majority; those are checked here and not stored. The winner is the candidate with "
    "the most votes, checked against the winning party the source names."
)


def election(event: Event, checked: Audit) -> Package:
    p = Package(
        f"{SOURCE}/{event.id}",
        SOURCE,
        f"The {event.name} by constituency, from Tindak Malaysia's transcription of the Election Commission's report",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.update([MALAYSIA, "my.keputusan"])
    p.uses(raw.cached(SOURCE, ARCHIVE))
    id = key("election", event.id)
    path = f"my/{event.id}"
    p.add(
        "election",
        id=id,
        polity_id=MALAYSIA,
        kind="general",
        date_from=event.first,
        date_to=event.last,
        date_precision=event.precision,
        status="held",
    )
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, event.name, "en", "official")
    if event.note:
        p.add("note", id=p.id("note", id), entity_id=id, kind="context", body_md=event.note, lang="en")
    if event.precision == "year":
        p.add(
            "note",
            id=p.id("note", id, "date"),
            entity_id=id,
            kind="caveat",
            body_md="The source gives this election's year but not its polling days, so it is dated to the year.",
            lang="en",
        )
    else:
        p.add("note", id=p.id("note", id, "date"), entity_id=id, kind="context", body_md=SUMMARY_NOTE, lang="en")
    if checked.valid_bad:
        p.accept(
            "accounting.votes_valid",
            "The source prints a total of valid votes that differs from the sum of the candidates' printed votes in "
            + "; ".join(line.split(": ")[0].removeprefix(event.id + " ") for line in checked.valid_bad)
            + ". Both figures are stored as printed, with a caveat on each result set.",
        )
    blocs: dict[str, set[str]] = defaultdict(set)
    for contest in contests(event.id):
        for candidate in contest.candidates:
            if candidate.bloc is not None and candidate.party is not None:
                blocs[candidate.bloc].add(candidate.party)
    for bloc, members in sorted(blocs.items()):
        alliance = key("alliance", event.id, bloc)
        p.add("alliance", id=alliance, kind="electoral_alliance", election_id=id)
        p.name(alliance, bloc.title() if " " in bloc else bloc, "ms", "official")
        for member in sorted(members):
            p.add("alliance_member", alliance_id=alliance, party_id=party(member))
    seen: set[str] = set()
    for contest in contests(event.id):
        seat = contest.seat
        state_name = spr.STATES[contest.state]
        body = spr.key("body", "dun", contest.state) if contest.assembly is not None else spr.DEWAN_RAKYAT
        p.external.add(body)
        area, reused = (assembly_area if contest.assembly else parliament_area)(event, contest)
        if reused:
            p.external.add(area)
        p.add(
            "contest",
            id=contest.id,
            election_id=id,
            kind="body_seats",
            body_id=body,
            district_area_id=area,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="tacit" if contest.tacit else "counted",
        )
        p.name(contest.id, f"{event.name}: {seat[0]} {spr.title(seat[1])}, {state_name}", "en", "english")
        slug = f"{path}/{changeset.slug(state_name)}-{changeset.slug(seat[0])}-{changeset.slug(seat[1])}"
        if slug in seen:
            raise ValueError(f"duplicate contest slug {slug}")
        seen.add(slug)
        p.add("slug", path=slug, entity_id=contest.id, is_current=True)
        result_set = key("result_set", contest.id)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest.id,
            stage="official",
            kind="tacit" if contest.tacit else "counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=None if contest.tacit else 1.0,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        for index, caveat in enumerate(checked.caveats.get(contest.id, [])):
            p.add(
                "note",
                id=p.id("note", result_set, "caveat", index),
                entity_id=result_set,
                kind="caveat",
                body_md=caveat,
                lang="en",
            )
        unit = key("unit", event.id, contest.state, seat[0])
        if not contest.tacit:
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=MALAYSIA,
                scheme_code="my.keputusan",
                code=f"{event.id}:{changeset.slug(contest.state)}:{seat[0]}",
                area_relation="equals",
                area_id=area,
                kind="standard",
            )
        top = winner(contest)
        for index, candidate in enumerate(contest.candidates, start=1):
            option = key("option", contest.id, index)
            person = key("person", contest.id, index)
            party_id = party(candidate.party)
            p.add("person", id=person, gender=candidate.sex)
            p.name(person, candidate.name, "ms", "ballot")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest.id,
                kind="candidate",
                party_id=party_id,
                ballot_label=candidate.name,
                alliance_id=None if candidate.bloc is None else key("alliance", event.id, candidate.bloc),
            )
            p.add(
                "candidacy",
                id=key("candidacy", contest.id, index),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
                member_of_party_id=party_id,
                is_independent=party_id is None,
            )
            if top is not None:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="elected" if candidate is top else "lost",
                )
            if not contest.tacit and candidate.votes is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=VOTES,
                    count_round=0,
                    flags=0,
                    value=candidate.votes,
                )
        if contest.tacit:
            continue
        for measure, value in ((REGISTERED, contest.registered), (VALID, contest.valid), (INVALID, contest.rejected)):
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{event.id}" for event in EVENTS)]


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    geo().write()
    registry().write()
    totals = Audit()
    for event in EVENTS:
        found = Audit()
        for contest in contests(event.id):
            audit(contest, found)
        election(event, found).write()
        totals.contests += found.contests
        for name in ("valid_ok", "accounting_ok", "majority_ok", "winner_ok"):
            setattr(totals, name, getattr(totals, name) + getattr(found, name))
        for name in ("valid_bad", "accounting_bad", "majority_bad", "winner_bad"):
            getattr(totals, name).extend(getattr(found, name))
    print(
        f"audit: {totals.contests} contests; valid ok {totals.valid_ok}, off {len(totals.valid_bad)}; "
        f"issued ok {totals.accounting_ok}, off {len(totals.accounting_bad)}; majority ok {totals.majority_ok}, "
        f"off {len(totals.majority_bad)}; winner ok {totals.winner_ok}, off {len(totals.winner_bad)}",
        flush=True,
    )
    for line in [*totals.valid_bad, *totals.accounting_bad, *totals.majority_bad, *totals.winner_bad]:
        print(f"  {line}", flush=True)
