"""Bulgaria's local elections of 2011, 2015, 2019, and 2023 by polling section, from the CIK's open data.

Every local election day elects, in each of the 265 municipalities, a municipal council (by list) and a mayor, and in
the villages and towns with a mayoralty (a `kmetstvo`) a mayor of the mayoralty. The three cities of Sofia, Plovdiv,
and Varna are also divided into districts (`rayoni`) that elect their own mayors. A mayor needs more than half of the
valid votes in the first round, or wins a runoff a week later between the two leaders. The CIK publishes one bundle of
semicolon-separated text files for each round and each of the four kinds of election, and this module builds one
package per election day from them, plus a `geo` package for the provinces, municipalities, districts, and
mayoralties from the National Statistical Institute's register of places (EKATTE). The 2011 bundles have their own
layout, read by the `*_2011` functions; they give each council list's seats but not the councillors who took them."""

import functools
import json
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "bg.cik_local"
BULGARIA = str(uuid5(package.NAMESPACE, "polity\x1fBG"))
BASE = "https://results.cik.bg"
NSI = "https://www.nsi.bg/nrnm/ekatte"
NSI_URLS = {name: f"{NSI}/{name}/json" for name in ("municipalities", "areas", "town-halls")}
SIK_SCHEME = "bg.local_sik"

# Measure codes of the archive's measure table.
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK, VOTERS = 1, 2, 3, 4, 5, 6, 14

COUNCIL, MAYOR, DISTRICT, VILLAGE = "OS", "KO", "KR", "KK"
CLASSES = (COUNCIL, MAYOR, DISTRICT, VILLAGE)
CLASS_PATH = {COUNCIL: "council", MAYOR: "mayor", DISTRICT: "district-mayor", VILLAGE: "village-mayor"}

# The 28 provinces, numbered as the CIK numbers them (the first two digits of every section code), with the
# National Statistical Institute's code for each.
OBLASTS: dict[int, tuple[str, str, str]] = {
    1: ("BLG", "Blagoevgrad", "Благоевград"),
    2: ("BGS", "Burgas", "Бургас"),
    3: ("VAR", "Varna", "Варна"),
    4: ("VTR", "Veliko Tarnovo", "Велико Търново"),
    5: ("VID", "Vidin", "Видин"),
    6: ("VRC", "Vratsa", "Враца"),
    7: ("GAB", "Gabrovo", "Габрово"),
    8: ("DOB", "Dobrich", "Добрич"),
    9: ("KRZ", "Kardzhali", "Кърджали"),
    10: ("KNL", "Kyustendil", "Кюстендил"),
    11: ("LOV", "Lovech", "Ловеч"),
    12: ("MON", "Montana", "Монтана"),
    13: ("PAZ", "Pazardzhik", "Пазарджик"),
    14: ("PER", "Pernik", "Перник"),
    15: ("PVN", "Pleven", "Плевен"),
    16: ("PDV", "Plovdiv", "Пловдив"),
    17: ("RAZ", "Razgrad", "Разград"),
    18: ("RSE", "Ruse", "Русе"),
    19: ("SLS", "Silistra", "Силистра"),
    20: ("SLV", "Sliven", "Сливен"),
    21: ("SML", "Smolyan", "Смолян"),
    22: ("SOF", "Sofia (capital)", "София (столица)"),
    23: ("SFO", "Sofia Province", "София"),
    24: ("SZR", "Stara Zagora", "Стара Загора"),
    25: ("TGV", "Targovishte", "Търговище"),
    26: ("HKV", "Haskovo", "Хасково"),
    27: ("SHU", "Shumen", "Шумен"),
    28: ("JAM", "Yambol", "Ямбол"),
}
OBLAST_BY_NSI = {code: number for number, (code, _, _) in OBLASTS.items()}
# The CIK calls this municipality by another name than the register does.
MUNICIPALITY_ALIASES = {"0815": "Добричка"}

METHOD = (
    "Counted by the polling section commissions and published by the Central Election Commission (CIK) as section "
    "protocols and votes in its open data. Registered voters are the voters on the roll, voters are the signatures on "
    "the roll, ballots cast are the paper ballots found in the box plus the machine ballots, and valid ballots are "
    "those marked for one list or candidate or for the no-one-supported option, which was on the ballot from 2019. "
    "Votes cast by machine are added to the paper votes of the same section. Preference votes for candidates inside "
    "a council list are in the same files and are not stored here yet. In 2011 the ballots of the municipality of "
    "Nevestino were counted by one experimental counting commission, whose protocols are stored as one unit."
)


class Day(NamedTuple):
    id: str
    day: date
    round: int
    url: str
    folder: str = ""  # the round's folder inside a bundle that holds both rounds
    classes: tuple[str, ...] = CLASSES


Z = BASE + "/"
DAYS = [
    # The 2011 bundles hold no district mayors of Sofia, Plovdiv, and Varna; the president's rounds in the same
    # bundles are read by `bg.cik`.
    Day("mi-2011-10-23", date(2011, 10, 23), 1, Z + "mipvr2011/el2011_t1.zip", classes=(COUNCIL, MAYOR, VILLAGE)),
    Day("mi-2011-10-30", date(2011, 10, 30), 2, Z + "mipvr2011/el2011_t2.zip", classes=(MAYOR, VILLAGE)),
    Day("mi-2015-10-25", date(2015, 10, 25), 1, Z + "minr2015/tur1/mi2015.zip"),
    Day("mi-2015-11-01", date(2015, 11, 1), 2, Z + "minr2015/tur2/mi2015.zip", classes=(MAYOR, DISTRICT, VILLAGE)),
    Day("mi-2019-10-27", date(2019, 10, 27), 1, Z + "mi2019/csv.zip", "TUR1"),
    Day("mi-2019-11-03", date(2019, 11, 3), 2, Z + "mi2019/csv.zip", "TUR2", (MAYOR, DISTRICT, VILLAGE)),
    Day("mi-2023-10-29", date(2023, 10, 29), 1, Z + "mi2023/tur1/opendata/export.zip"),
    Day(
        "mi-2023-11-05", date(2023, 11, 5), 2, Z + "mi2023/tur2/opendata/export.zip", classes=(MAYOR, DISTRICT, VILLAGE)
    ),
]
# Files the CIK publishes that this module does not read: the spreadsheets of the 2023 rounds repeat the text files
# split by ballot type, and the 2015 and 2019 opendata pages did not answer. results.cik.bg may answer automated
# requests with a browser challenge (403); `fetch` then prints the refusal and a rebuild reads the raw cache, whose
# index keeps each bundle's URL and hash.
BLOCKED: list[str] = []


def first_round(day: Day) -> Day | None:
    return next((d for d in DAYS if d.round == 1 and d.day.year == day.day.year), None) if day.round == 2 else None


def second_round(day: Day) -> Day | None:
    return next((d for d in DAYS if d.round == 2 and d.day.year == day.day.year), None) if day.round == 1 else None


def fetch() -> None:
    for url in [*dict.fromkeys(d.url for d in DAYS), *NSI_URLS.values()]:
        try:
            raw.fetch(SOURCE, url, suffix=".json" if url.startswith(NSI) else ".zip")
        except httpx.HTTPError as failure:
            print(f"{url}: {failure}", flush=True)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CIK_SOURCE = key("source", "cik")
NSI_SOURCE = key("source", "nsi")
SYSTEM_COUNCIL = key("electoral_system", "council", 2011)
SYSTEM_MAYOR = key("electoral_system", "mayor", 2011)


def oblast(number: int) -> str:
    return key("area", "bg.oblast", OBLASTS[number][0])


def obshtina(code: str) -> str:
    return key("area", "bg.obshtina", code)


def rayon(code: str) -> str:
    return key("area", "bg.rayon", code)


def kmetstvo(ekatte: str) -> str:
    return key("area", "bg.kmetstvo", ekatte.zfill(5))


def council_body(code: str) -> str:
    return key("body", "council", code)


def mayor_office(cls: str, code: str) -> str:
    return key("office", "mayor", cls, code)


# --- reading the bundles -------------------------------------------------------------------------------------------

TRANSLIT = dict(
    zip(
        "абвгдежзийклмнопрстуфхцчшщъьюя",
        ["a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "h",
         "ts", "ch", "sh", "sht", "a", "y", "yu", "ya"],
        strict=True,
    )
)  # fmt: skip


def latin(text: str) -> str:
    """The Streamlined System of romanization, for names the register does not give in English."""
    out = []
    for ch in text:
        piece = TRANSLIT.get(ch.lower(), ch)
        out.append(piece.capitalize() if ch.isupper() else piece)
    return "".join(out)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251")


@functools.cache
def _archive(url: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(raw.cached(SOURCE, url).path)


def _path(info: zipfile.ZipInfo) -> list[str]:
    """The folders and file name of a member; the CIK's zips hold DOS code page 866 names without the UTF-8 flag."""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("cp866")
        except UnicodeError:
            pass
    return [part for part in name.split("/") if part]


FOLDERS = {"КК": VILLAGE, "КО": MAYOR, "КР": DISTRICT, "ОС": COUNCIL, "ТУР1": "TUR1", "ТУР2": "TUR2"}


@functools.cache
def _members(url: str, year: int) -> dict[tuple[str, str, str], str]:
    """(round folder, class, role) -> member name; roles are the file stems, such as `protocols`."""
    out: dict[tuple[str, str, str], str] = {}
    for info in _archive(url).infolist():
        if info.is_dir():
            continue
        parts = _path(info)
        match = re.fullmatch(r"([a-z_]+?)_\d\d\.\d\d\.(\d{4})\.txt", parts[-1])
        if match is None or len(parts) < 2:
            continue
        cls = FOLDERS.get(parts[-2])
        if cls is None or cls not in CLASSES:
            continue
        if int(match.group(2)) != year:
            continue  # the 2023 bundle also holds a court-ordered recount of three sections, dated 2024
        folder = FOLDERS.get(parts[-3], "") if len(parts) > 2 else ""
        out.setdefault((folder if folder.startswith("TUR") else "", cls, match.group(1)), info.filename)
    return out


def rows(day: Day, cls: str, role: str) -> list[list[str]]:
    if day.day.year == 2011:
        return rows_2011(day, cls, role)
    name = _members(day.url, day.day.year).get((day.folder, cls, role))
    if name is None:
        return []
    text = _decode(_archive(day.url).read(name))
    return [line.split(";") for line in text.replace("\r", "").split("\n") if line.strip()]


def cite(p: Package, day: Day) -> None:
    artifact = raw.cached(SOURCE, day.url)
    if day.round == 2 and day.folder:
        p.uses(artifact)  # the first round's package, a dependency, already holds the artifact row
    else:
        p.artifact(artifact, CIK_SOURCE)


def number(text: str) -> int:
    text = text.strip()
    return int(text) if text.lstrip("-").isdigit() else 0


class Section(NamedTuple):
    code: str
    unit: str
    unit_name: str
    ekatte: str
    place: str
    mobile: bool


@functools.cache
def sections(day: Day, cls: str) -> dict[tuple[str, str], Section]:
    if day.day.year == 2011:
        return sections_2011(day, cls)
    out: dict[tuple[str, str], Section] = {}
    year = day.day.year
    for r in rows(day, cls, "sections"):
        tail = r[5:]
        if year == 2015:
            mobile = r[5] == "1" or r[6] == "1"
        elif year == 2019:
            mobile = r[5] == "1"
        else:  # the address may hold the field separator, so the flags are counted from the end
            mobile = tail[-2] == "1"
        out[(r[0], r[1])] = Section(r[0], r[1], r[2].strip(), r[3].strip(), r[4].strip(), mobile)
    return out


# Zero-based columns of each protocol form: registered voters, voters who signed, ballots in the box, invalid ballots,
# valid ballots, and the votes for no one. The 2023 forms add machine ballots after the paper ones.
PROTOCOLS: dict[tuple[int, str], dict[str, tuple[int, ...]]] = {
    (2015, "1"): {"reg": (6,), "voters": (10,), "box": (19,), "inv": (20,), "valid": (21,)},
    (2015, "8"): {"reg": (6,), "voters": (10,), "box": (21,), "inv": (24,), "valid": (25,)},
    (2019, "1"): {"reg": (5,), "voters": (9,), "box": (16,), "inv": (17,), "valid": (18,), "none": (20,)},
}


def cell(row: list[str], index: int) -> int:
    return number(row[index]) if index < len(row) else 0


@functools.cache
def protocols(day: Day, cls: str) -> dict[tuple[str, str], dict[str, int]]:
    """Per section and unit: reg, voters, box, inv, valid, and none; rows a section has twice are added up."""
    year = day.day.year
    if year == 2011:
        return protocols_2011(day, cls)
    out: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for r in rows(day, cls, "protocols"):
        figures = out[(r[1], r[2])]
        if year == 2023:
            machine = cell(r, 18)
            values = {
                "reg": cell(r, 7),
                "voters": cell(r, 11),
                "box": cell(r, 14) + machine,
                "inv": cell(r, 15) + max(0, machine - cell(r, 19) - cell(r, 20)),
                "valid": cell(r, 16) + cell(r, 17) + cell(r, 19) + cell(r, 20),
                "none": cell(r, 16) + cell(r, 19),
            }
        else:
            spec = PROTOCOLS[(year, r[0])]
            values = {name: cell(r, columns[0]) for name, columns in spec.items()}
        for name, value in values.items():
            if name in ("reg", "voters"):
                figures.setdefault(name, value)
            else:
                figures[name] = figures.get(name, 0) + value
    return out


@functools.cache
def votes(day: Day, cls: str) -> dict[tuple[str, str], dict[str, int]]:
    """Valid votes per section and unit, by the list's number inside its municipality's draw."""
    year = day.day.year
    if year == 2011:
        return votes_2011(day, cls)
    out: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows(day, cls, "votes"):
        if year == 2023:
            section, unit, tail, width = r[1], r[2], r[3:], 4
        else:
            section, unit, tail, width = r[0], r[1], r[2:], 3
        for i in range(0, len(tail) - width + 1, width):
            if tail[i].strip().isdigit():
                out[(section, unit)][tail[i].strip()] += number(tail[i + 1])
    return out


class Candidate(NamedTuple):
    oik: int
    unit_name: str
    party: str
    party_name: str
    position: int
    name: str
    flag: str
    unit: str = ""  # the contest's unit id, where the files give it outright


@functools.cache
def candidates(day: Day, cls: str) -> list[Candidate]:
    """One row per candidate; a list's name may hold the field separator, so the fields are counted from the end."""
    if day.day.year == 2011:
        return candidates_2011(day, cls)
    out: list[Candidate] = []
    for r in rows(day, cls, "local_candidates"):
        r = [c.strip() for c in r]
        if len(r) < 6:
            continue
        flag, name = r[-1], r[-2]
        end = len(r) - 2
        position = 0
        if len(r) >= 7 and r[-3].isdigit():
            position, end = int(r[-3]), len(r) - 3
            if day.day.year >= 2019 and cls == COUNCIL and position > 100:
                position -= 100  # the lists number their candidates from 101
        out.append(Candidate(number(r[0]), r[1], r[2], ";".join(r[3:end]), position, name, flag))
    return out


@functools.cache
def lists(day: Day, cls: str) -> dict[tuple[int, str], dict[str, str]]:
    """(oik, unit name) -> {list number: name}, from the parties registered with the municipal commissions."""
    out: dict[tuple[int, str], dict[str, str]] = defaultdict(dict)
    if day.day.year == 2011:
        return out  # the 2011 files register lists by municipality, so a contest's lists come from its candidates
    for r in rows(day, cls, "local_parties"):
        out[(number(r[0]), r[1].strip())][r[2].strip()] = ";".join(r[3:]).strip()
    return out


def oik_of(section: str) -> int:
    return int(section[:4])


@functools.cache
def units(day: Day, cls: str) -> dict[str, tuple[int, str]]:
    """Unit id -> (oik, unit name) of every contest the day held in this class."""
    out: dict[str, tuple[int, str]] = {}
    seen = {unit for _, unit in (*protocols(day, cls), *votes(day, cls))}
    for (section, unit), s in sections(day, cls).items():
        if unit in seen:
            out.setdefault(unit, (oik_of(section), s.unit_name))
    for _, unit in (*protocols(day, cls), *votes(day, cls)):
        if unit not in out:
            raise ValueError(f"{day.id} {cls}: unit {unit} has results but no section")
    return out


# --- the 2011 bundles ----------------------------------------------------------------------------------------------
# One folder per kind of election, each with semicolon-separated cp1251 files named `el2011_<kind>_<role>.txt`. A
# section's code is province (2), municipality (2), city district (2), and section (3); its first four digits are the
# municipality's CIK code. A row's first field marks the protocol: empty for a polling section, `П` for a mobile
# ballot box, which in a mayoralty election carries the EKATTE code of the mayoralty in brackets, and `Е` for the
# experimental counting commission that counted every ballot of the municipality of Nevestino in one place.

FILES_2011 = {COUNCIL: "council", MAYOR: "mayor_munic", VILLAGE: "mayor_mrlty"}
FOLDERS_2011 = {COUNCIL: "общински съветници", MAYOR: "кмет на община", VILLAGE: "кмет на кметство"}
# Zero-based columns of registered voters, voters who signed, ballots in the box, invalid, and valid ballots in each
# protocol form. Registered voters are those on the roll as handed to the commission, as `bg.cik` reads the
# president's protocols of the same day.
PROTOCOLS_2011 = {
    "": {"reg": 2, "voters": 6, "box": 13, "inv": 19, "valid": 20},
    "П": {"reg": 2, "voters": 4, "box": 10, "inv": 16, "valid": 17},
    "Е": {"reg": 5, "voters": 9, "box": 16, "inv": 22, "valid": 23},
}
RESULT_FLAGS = {"И": "1", "Б": "2"}


def rows_2011(day: Day, cls: str, role: str) -> list[list[str]]:
    if cls not in FILES_2011:
        return []
    archive = _archive(day.url)
    wanted = f"el2011_{FILES_2011[cls]}_{role}.txt"
    for info in archive.infolist():
        parts = _path(info)
        if len(parts) == 2 and parts[0] == FOLDERS_2011[cls] and parts[1] == wanted:
            text = archive.read(info.filename).decode("cp1251")
            return [line.split(";") for line in text.replace("\r", "").split("\n") if line.strip()]
    return []


def _mark(field: str) -> tuple[str, str]:
    """The protocol's form and, for a mobile box of a mayoralty election, the mayoralty's EKATTE code."""
    match = re.fullmatch(r"(\w?)(?:\((\d+)\))?", field.strip())
    if match is None:
        raise ValueError(f"unknown protocol mark {field!r}")
    return match.group(1), match.group(2) or ""


@functools.cache
def _rows_2011(day: Day, cls: str, role: str) -> list[tuple[str, str, list[str]]]:
    """(section, unit, row) for every protocols or votes row; the unit is the municipality or the mayoralty."""
    sections_ = sections_2011(day, cls)
    by_section: dict[str, list[str]] = defaultdict(list)
    for section, unit in sections_:
        by_section[section].append(unit)
    plain = [r for r in rows_2011(day, cls, role) if _mark(r[0])[0] != "Е"]
    pooled = [r for r in rows_2011(day, cls, role) if _mark(r[0])[0] == "Е"]
    out: list[tuple[str, str, list[str]]] = []
    for r in plain:
        form, ekatte = _mark(r[0])
        section = r[1].strip()
        if cls != VILLAGE:
            out.append((section, str(oik_of(section)), r))
            continue
        if ekatte:
            out.append((section, str(int(ekatte)), r))
            continue
        units_ = by_section.get(section, [])
        if len(units_) != 1:
            raise ValueError(f"{day.id}: section {section} of form {form!r} serves mayoralties {units_}")
        out.append((section, units_[0], r))
    # The counting commission files one protocol per mayoralty under one code, in the order of the sections file.
    seen: dict[str, int] = defaultdict(int)
    for r in pooled:
        section = r[1].strip()
        units_ = by_section.get(section, [])
        if cls != VILLAGE:
            out.append((section, str(oik_of(section)), r))
            continue
        out.append((section, units_[seen[section]], r))
        seen[section] += 1
    return out


@functools.cache
def sections_2011(day: Day, cls: str) -> dict[tuple[str, str], Section]:
    out: dict[tuple[str, str], Section] = {}
    for r in rows_2011(day, cls, "sections"):
        r = [c.strip() for c in r]
        section = r[1]
        if cls == VILLAGE:
            unit, name = str(int(r[7])), r[6]
        else:
            unit, name = str(oik_of(section)), r[3]
        out[(section, unit)] = Section(section, unit, name, r[5], r[4], _mark(r[0])[0] == "П")
    return out


@functools.cache
def protocols_2011(day: Day, cls: str) -> dict[tuple[str, str], dict[str, int]]:
    out: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for section, unit, r in _rows_2011(day, cls, "protocols"):
        spec = PROTOCOLS_2011[_mark(r[0])[0]]
        figures = out[(section, unit)]
        for name, column in spec.items():
            value = cell(r, column)
            if name in ("reg", "voters") and name in figures:
                continue
            figures[name] = figures.get(name, 0) + value
    return out


@functools.cache
def votes_2011(day: Day, cls: str) -> dict[tuple[str, str], dict[str, int]]:
    out: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for section, unit, r in _rows_2011(day, cls, "votes"):
        tail = r[2:]
        for i in range(0, len(tail) - 1, 2):
            if tail[i].strip().isdigit():
                out[(section, unit)][tail[i].strip()] += number(tail[i + 1])
    return out


@functools.cache
def coalitions_2011(day: Day) -> dict[tuple[int, str], str]:
    """(oik, list number) -> the name of the party, coalition, or initiative committee in that municipality."""
    out: dict[tuple[int, str], str] = {}
    for r in rows_2011(day, MAYOR, "coalitions"):
        out[(int(r[0]), r[1].strip())] = r[2].strip()
    return out


def oik_2011(text: str) -> int:
    """The municipality's CIK code from the commission's number, which drops the province's leading zero."""
    return int(text.strip())


@functools.cache
def candidates_2011(day: Day, cls: str) -> list[Candidate]:
    names = coalitions_2011(day)
    flags: dict[tuple[int, str, str], str] = {}
    for r in rows_2011(day, cls, "result"):
        r = [c.strip() for c in r]
        if cls == MAYOR:
            flags[(oik_2011(r[1]), "", r[3])] = RESULT_FLAGS[r[0]]
        elif cls == VILLAGE:
            flags[(oik_2011(r[1]), str(int(r[3])), r[5])] = RESULT_FLAGS[r[0]]
    out: list[Candidate] = []
    for r in rows_2011(day, cls, "candidates"):
        r = [c.strip() for c in r]
        oik = oik_2011(r[0])
        if cls == COUNCIL:
            party, position, name, unit, unit_name = r[1], int(r[2]), r[3], str(oik), ""
        elif cls == MAYOR:
            party, position, name, unit, unit_name = r[1], 0, r[2], str(oik), ""
        else:
            party, position, name, unit, unit_name = r[3], 0, r[4], str(int(r[1])), r[2]
        flag = flags.get((oik, "" if cls == MAYOR else unit, party), "")
        out.append(Candidate(oik, unit_name, party, names.get((oik, party), ""), position, name, flag, unit))
    return out


@functools.cache
def seats_2011(day: Day) -> dict[str, dict[str, int]]:
    """Council seats won by each list, by municipality unit, from the CIK's result file."""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for r in rows_2011(day, COUNCIL, "result"):
        out[str(oik_2011(r[1]))][r[3].strip()] = int(r[4])
    return out


# --- geography -----------------------------------------------------------------------------------------------------


def nsi(name: str, field: str) -> list[dict[str, object]]:
    data = json.loads(raw.cached(SOURCE, NSI_URLS[name]).path.read_text())
    return [row for row in data if field in row]


@dataclass
class Places:
    municipalities: dict[str, dict[str, object]] = field(default_factory=dict)  # CIK code -> NSI row
    districts: dict[str, dict[str, object]] = field(default_factory=dict)  # CIK unit id -> NSI row
    district_city: dict[str, str] = field(default_factory=dict)  # CIK unit id -> CIK municipality code
    mayoralties: dict[str, dict[str, object]] = field(default_factory=dict)  # EKATTE -> row
    mayoralty_of: dict[str, str] = field(default_factory=dict)  # EKATTE -> CIK municipality code


def cik_code(nsi_code: str) -> str:
    return f"{OBLAST_BY_NSI[nsi_code[:3]]:02d}{int(nsi_code[3:]):02d}"


@functools.cache
def places() -> Places:
    out = Places()
    by_nsi: dict[str, str] = {}
    for row in nsi("municipalities", "obshtina"):
        code = cik_code(str(row["obshtina"]))
        out.municipalities[code] = row
        by_nsi[str(row["obshtina"])] = code
    names = {
        oik_of(s.code): s.unit_name.partition(". ")[2]
        for day in DAYS
        for (_, _), s in sections(day, MAYOR).items()
        if day.round == 1 and day.day.year >= 2015
    }
    for code, row in out.municipalities.items():
        seen = names.get(int(code))
        if seen != row["name"] and MUNICIPALITY_ALIASES.get(code) != seen:
            raise ValueError(f"municipality {code}: the register says {row['name']!r}, the CIK {seen!r}")
    city = {str(r["ekatte"]): code for code, r in out.municipalities.items()}
    for row in nsi("areas", "raion"):
        ekatte, _, n = str(row["raion"]).partition("-")
        municipality = city[ekatte]
        unit = str(int(municipality[:2]) * 100 + int(n))
        out.districts[unit] = row
        out.district_city[unit] = municipality
    for row in nsi("town-halls", "kmetstvo"):
        ekatte = str(row["ekatte"])
        out.mayoralties[ekatte] = row
        out.mayoralty_of[ekatte] = by_nsi[str(row["kmetstvo"]).partition("-")[0]]
    # Mayoralties the register no longer lists, or lists under a code the CIK did not use
    for day in DAYS:
        for (section, unit), s in sections(day, VILLAGE).items():
            ekatte = unit.zfill(5)
            if ekatte not in out.mayoralties:
                out.mayoralties[ekatte] = {"kmetstvo": ekatte, "name": re.sub(r"^(с|гр)\.\s*", "", s.unit_name)}
                out.mayoralty_of[ekatte] = f"{oik_of(section):04d}"
    return out


def municipality_names(code: str) -> tuple[str, str]:
    row = places().municipalities[code]
    return str(row["name_en"]), str(row["name"])


def mayoralty_names(ekatte: str) -> tuple[str, str]:
    row = places().mayoralties[ekatte.zfill(5)]
    bg = str(row["name"])
    return str(row.get("name_en") or latin(bg)), bg


def district_names(unit: str) -> tuple[str, str]:
    row = places().districts[unit]
    return str(row["name_en"]), str(row["name"])


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bulgaria's city districts and mayoralties, from the EKATTE register; the provinces and municipalities they "
        "lie in are in the `bg.cik` geo package",
        depends=[f"{SOURCE}/registry", "bg.cik/geo"],
    )
    p.external.add(BULGARIA)
    for artifact in (raw.cached(SOURCE, url) for url in NSI_URLS.values()):
        p.artifact(artifact, NSI_SOURCE)
    for code, rank, noun in (
        ("bg.rayon", 3, "city district"),
        ("bg.kmetstvo", 3, "mayoralty"),
        (SIK_SCHEME, 4, "polling section"),
    ):
        kind = "precinct" if code == SIK_SCHEME else "administrative"
        p.add("scheme", code=code, polity_id=BULGARIA, kind=kind, rank=rank, noun=noun, code_system=None)
    where = places()
    for unit, row in sorted(where.districts.items(), key=lambda kv: int(kv[0])):
        id = rayon(str(row["raion"]))
        english, native = district_names(unit)
        p.add("area", id=id, scheme_code="bg.rayon")
        p.add("area_code", area_id=id, code=str(row["raion"]))
        p.add("area_parent", area_id=id, parent_id=obshtina(where.district_city[unit]))
        p.add("external_id", entity_id=id, scheme="bg.cik.rayon", value=unit)
        p.name(id, english, "en", "official")
        p.name(id, native, "bg", "native", False)
    for ekatte, row in sorted(where.mayoralties.items()):
        id = kmetstvo(ekatte)
        english, native = mayoralty_names(ekatte)
        p.add("area", id=id, scheme_code="bg.kmetstvo")
        p.add("area_code", area_id=id, code=str(row["kmetstvo"]))
        p.add("area_parent", area_id=id, parent_id=obshtina(where.mayoralty_of[ekatte]))
        listed = "name_en" in row
        p.add("external_id", entity_id=id, scheme="bg.ekatte" if listed else "bg.cik.kmetstvo", value=ekatte)
        p.name(id, english, "en", "official" if "name_en" in row else "transliteration")
        p.name(id, native, "bg", "native", False)
    return p


# --- registry ------------------------------------------------------------------------------------------------------

SYSTEMS: list[tuple[str, date, dict[str, object], str]] = [
    (
        SYSTEM_COUNCIL,
        date(2011, 1, 1),
        {
            "formula": "hare",
            "threshold": {"level": "list", "scope": "contest", "percent": 4},
            "lists": {"order": "flexible", "vacancy": "next_on_list"},
        },
        (
            "Municipal councils are elected from lists in one constituency per municipality. Seats go to the lists "
            "that pass a 4% threshold, by the Hare quota and largest remainders. Preference votes can move a "
            "candidate up a list."
        ),
    ),
    (
        SYSTEM_MAYOR,
        date(2011, 1, 1),
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A mayor, of a municipality, a city district, or a mayoralty, is elected directly. A candidate with "
            "more than half of the valid votes wins in the first round; otherwise the two leading candidates go to a "
            "second round a week later."
        ),
    ),
]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Bulgaria's municipal councils and mayors, the electoral systems of local elections, and the CIK and EKATTE "
        "as sources",
    )
    p.external.add(BULGARIA)
    p.add(
        "source",
        id=CIK_SOURCE,
        kind="official_body",
        publisher="Централна избирателна комисия",
        title="CIK local election results open data",
        url="https://results.cik.bg/",
        license="Published for re-use as public sector information; the CIK states no further license terms",
        license_url="https://results.cik.bg/",
        attribution="Централна избирателна комисия (Central Election Commission of Bulgaria)",
    )
    p.add(
        "source",
        id=NSI_SOURCE,
        kind="official_body",
        publisher="Национален статистически институт",
        title="Единен класификатор на административно-териториалните и териториалните единици (ЕКАТТЕ)",
        url="https://www.nsi.bg/nrnm/ekatte/",
        license="Published for re-use as public sector information; the NSI states no further license terms",
        license_url="https://www.nsi.bg/nrnm/",
        attribution="Национален статистически институт (National Statistical Institute of Bulgaria)",
    )
    for id, since, rules, method in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=BULGARIA, valid_from=since, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    where = places()
    for code in sorted(where.municipalities):
        english, native = municipality_names(code)
        body = council_body(code)
        p.add("body", id=body, polity_id=BULGARIA, kind="council")
        p.name(body, f"{english} Municipal Council", "en", "official")
        p.name(body, f"Общински съвет – {native}", "bg", "native", False)
        office = mayor_office(MAYOR, code)
        p.add("office", id=office, polity_id=BULGARIA, kind="mayor")
        p.name(office, f"Mayor of {english}", "en", "official")
        p.name(office, f"Кмет на община {native}", "bg", "native", False)
    for unit in sorted(where.districts, key=int):
        english, native = district_names(unit)
        city, city_bg = municipality_names(where.district_city[unit])
        office = mayor_office(DISTRICT, unit)
        p.add("office", id=office, polity_id=BULGARIA, kind="mayor")
        p.name(office, f"Mayor of {english} district, {city}", "en", "official")
        p.name(office, f"Кмет на район {native}, {city_bg}", "bg", "native", False)
    for ekatte in sorted(where.mayoralties):
        english, native = mayoralty_names(ekatte)
        city, city_bg = municipality_names(where.mayoralty_of[ekatte])
        office = mayor_office(VILLAGE, ekatte)
        p.add("office", id=office, polity_id=BULGARIA, kind="mayor")
        p.name(office, f"Mayor of {english}, {city}", "en", "official")
        p.name(office, f"Кмет на кметство {native}, община {city_bg}", "bg", "native", False)
    return p


# --- elections -----------------------------------------------------------------------------------------------------


def norm(text: str) -> str:
    return re.sub(r"[\s\"„“”'`]+", " ", text).strip().casefold()


LOCAL_LIST = re.compile(r"^(ик|инициативен комитет|мк|местна коалиция)\b", re.IGNORECASE)


def party_kind(name: str) -> str:
    low = name.lower()
    if low.startswith(("инициативен комитет", "ик ")):
        return "independent_group"
    if low.startswith(("коалиция", "кп ", "мк ", "местна коалиция")):
        return "bloc"
    return "party"


class Contest(NamedTuple):
    cls: str
    unit: str
    oik: int
    unit_name: str


def contest_names(cls: str, unit: str, oik: int, second: bool) -> tuple[str, str]:
    code = f"{oik:04d}"
    if cls == COUNCIL:
        en, bg = f"{municipality_names(code)[0]} municipal council", f"Общински съвет – {municipality_names(code)[1]}"
    elif cls == MAYOR:
        en, bg = f"Mayor of {municipality_names(code)[0]}", f"Кмет на община {municipality_names(code)[1]}"
    elif cls == DISTRICT:
        english, native = district_names(unit)
        city = municipality_names(code)
        en, bg = f"Mayor of {english} district, {city[0]}", f"Кмет на район {native}, {city[1]}"
    else:
        english, native = mayoralty_names(unit)
        city = municipality_names(code)
        en, bg = f"Mayor of {english}, {city[0]}", f"Кмет на кметство {native}, община {city[1]}"
    if second:
        return f"{en} (second round)", f"{bg} (втори тур)"
    return en, bg


def contest_slug(cls: str, unit: str, oik: int) -> str:
    code = f"{oik:04d}"
    english = municipality_names(code)[0]
    if cls in (COUNCIL, MAYOR):
        return f"{CLASS_PATH[cls]}/{code}-{changeset.slug(english)}"
    if cls == DISTRICT:
        return f"{CLASS_PATH[cls]}/{unit}-{changeset.slug(district_names(unit)[0])}"
    return f"{CLASS_PATH[cls]}/{unit.zfill(5)}-{changeset.slug(mayoralty_names(unit)[0])}"


def area_of(cls: str, unit: str, oik: int) -> str:
    if cls in (COUNCIL, MAYOR):
        return obshtina(f"{oik:04d}")
    if cls == DISTRICT:
        return rayon(str(places().districts[unit]["raion"]))
    return kmetstvo(unit)


def office_of(cls: str, unit: str, oik: int) -> str:
    code = f"{oik:04d}" if cls == MAYOR else unit.zfill(5) if cls == VILLAGE else unit
    return mayor_office(cls, code)


def values_frame(columns: dict[str, list[object]]) -> pl.DataFrame:
    return pl.DataFrame(columns, schema_overrides={"channel": pl.Int16, "measure": pl.Int16}, strict=False)


def build(day: Day) -> Package:
    first = first_round(day)
    second = second_round(day)
    p = Package(
        f"{SOURCE}/{day.id}",
        SOURCE,
        f"The {day.day:%d %B %Y} Bulgarian local elections{' (second round)' if day.round == 2 else ''} by polling "
        "section from the CIK's open data",
        depends=[
            f"{SOURCE}/geo",
            f"{SOURCE}/registry",
            *([f"{SOURCE}/{first.id}"] if first is not None else []),
        ],
    )
    p.external.add(BULGARIA)
    cite(p, day)
    election = key("election", day.id)
    text = f"{day.day.day} {day.day.strftime('%B')} {day.day.year}"
    p.add(
        "election",
        id=election,
        polity_id=BULGARIA,
        kind="runoff" if day.round == 2 else "local",
        date_from=day.day,
        date_to=day.day,
        status="held",
    )
    if day.round == 2:
        p.name(election, f"Bulgarian local elections, {text}, second round", "en", "official")
        p.name(election, f"Местни избори, {day.day:%d.%m.%Y}, втори тур", "bg", "native", False)
    else:
        p.name(election, f"Bulgarian local elections, {text}", "en", "official")
        p.name(election, f"Местни избори, {day.day:%d.%m.%Y}", "bg", "native", False)
    path = f"bg/{day.day}-local" + ("-runoff" if day.round == 2 else "")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add("note", id=key("note", election), entity_id=election, kind="methodology", body_md=METHOD, lang="en")

    parties: dict[str, str] = {}
    party_rows: list[dict[str, object]] = []
    party_names: list[dict[str, object]] = []

    def party_id(name: str, oik: int, scope_local: bool) -> str:
        tag = norm(name) if not scope_local else f"{oik}:{norm(name)}"
        found = parties.get(tag)
        if found is None:
            found = parties[tag] = p.id("party", day.id, tag)
            party_rows.append({"id": found, "kind": party_kind(name), "polity_id": BULGARIA})
            party_names.append(
                {
                    "id": p.id("name", found),
                    "entity_id": found,
                    "text": name,
                    "lang": "bg",
                    "kind": "official",
                    "is_preferred": True,
                }
            )
        return found

    contests_: list[dict[str, object]] = []
    contest_names_: list[dict[str, object]] = []
    options: list[dict[str, object]] = []
    result_sets: list[dict[str, object]] = []
    slugs: list[dict[str, object]] = []
    people: list[dict[str, object]] = []
    named: list[dict[str, object]] = []
    runs: list[dict[str, object]] = []
    outcomes: list[dict[str, object]] = []
    unit_rows: dict[str, dict[str, object]] = {}
    unit_names: dict[str, dict[str, object]] = {}
    cols: dict[str, list[object]] = {c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "value")}
    stats = {"sections": 0, "contests": 0}
    figures_all: dict[tuple[str, str, str], dict[str, int]] = {}
    votes_all: dict[tuple[str, str, str], dict[str, int]] = {}

    def put(rs: str, unit: str, option: str | None, measure: int, value: int) -> None:
        cols["result_set_id"].append(rs)
        cols["unit_id"].append(unit)
        cols["option_id"].append(option)
        cols["measure"].append(measure)
        cols["value"].append(value)

    for cls in day.classes:
        secs = sections(day, cls)
        figures = protocols(day, cls)
        tallies = votes(day, cls)
        contest_units = units(day, cls)
        fig_by_unit: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
        for (section, u), f in figures.items():
            fig_by_unit[u][section] = f
        tal_by_unit: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
        for (section, u), per in tallies.items():
            tal_by_unit[u][section] = per
        listed = lists(day, cls)
        cands = candidates(day, cls)
        by_unit: dict[str, list[Candidate]] = defaultdict(list)
        unit_of_name = {(oik, name): unit for unit, (oik, name) in contest_units.items()}
        for c in cands:
            unit = c.unit or unit_of_name.get((c.oik, c.unit_name))
            if unit is not None:
                by_unit[unit].append(c)
        later = {}
        if second is not None and cls != COUNCIL:
            later_units = units(second, cls)
            later_of = {(oik, name): unit for unit, (oik, name) in later_units.items()}
            for c in candidates(second, cls):
                unit = c.unit or later_of.get((c.oik, c.unit_name))
                if unit is not None:
                    later.setdefault(unit, set()).add((norm(c.name), norm(c.party_name)))
        for unit, (oik, unit_name) in sorted(contest_units.items(), key=lambda kv: (kv[1][0], int(kv[0]))):
            ckey = f"{cls}:{unit}"
            contest = key("contest", day.id, cls, unit)
            rs = key("result_set", day.id, cls, unit)
            en, bg = contest_names(cls, unit, oik, day.round == 2)
            previous = key("contest", first.id, cls, unit) if first is not None else None
            council = cls == COUNCIL
            contest_row: dict[str, object] = {
                "id": contest,
                "election_id": election,
                "kind": "list_allocation" if council else "office",
                "office_id": None if council else office_of(cls, unit, oik),
                "body_id": council_body(f"{oik:04d}") if council else None,
                "district_area_id": area_of(cls, unit, oik),
                "magnitude": None if council else 1,
                "round_number": day.round,
                "previous_round_id": previous,
                "electoral_system_id": SYSTEM_COUNCIL if council else SYSTEM_MAYOR,
                "default_result_kind": "counted",
            }
            contests_.append(contest_row)
            contest_names_.extend(
                [
                    {"id": p.id("name", contest, "en"), "entity_id": contest, "text": en, "lang": "en",
                     "kind": "official", "is_preferred": True},
                    {"id": p.id("name", contest, "bg"), "entity_id": contest, "text": bg, "lang": "bg",
                     "kind": "native", "is_preferred": False},
                ]
            )  # fmt: skip
            slugs.append({"path": f"{path}/{contest_slug(cls, unit, oik)}", "entity_id": contest, "is_current": True})
            result_sets.append(
                {
                    "id": rs,
                    "election_id": election,
                    "contest_id": contest,
                    "stage": "official",
                    "kind": "counted",
                    "source_id": CIK_SOURCE,
                    "status": "active",
                    "is_default": True,
                    "completeness": 1.0,
                }
            )
            stats["contests"] += 1

            # The lists on the ballot: those registered, those with candidates, and any the votes name.
            names = dict(listed.get((oik, unit_name), {}))
            mine = by_unit.get(unit, [])
            for c in mine:
                names.setdefault(c.party, c.party_name)
            seen = {n for per in tal_by_unit[unit].values() for n in per}
            order = sorted({*names, *seen}, key=int)
            option_of: dict[str, str] = {}
            by_party: dict[str, list[Candidate]] = defaultdict(list)
            for c in mine:
                by_party[c.party].append(c)
            for n in order:
                name = names.get(n) or f"List {n}"
                runner = by_party.get(n, [])
                own = not council and any(norm(c.name) == norm(name) for c in runner)
                local = own or bool(LOCAL_LIST.match(name))
                option = option_of[n] = key("option", day.id, cls, unit, n)
                options.append(
                    {
                        "id": option,
                        "contest_id": contest,
                        "kind": "list" if council else "candidate",
                        "party_id": party_id(name, oik, local),
                        "ballot_position": int(n),
                        "ballot_label": runner[0].name if runner and not council else name,
                    }
                )
            none_option = None
            if any("none" in f for f in fig_by_unit[unit].values()):
                none_option = key("option", day.id, cls, unit, "none")
                options.append(
                    {
                        "id": none_option,
                        "contest_id": contest,
                        "kind": "none_of_the_above",
                        "ballot_label": "Не подкрепям никого",
                    }
                )

            # candidates and declared outcomes
            elected: dict[str, list[str]] = defaultdict(list)
            flags: dict[str, str] = {}
            for c in mine:
                tag = f"{ckey}:{c.party}:{c.position}"
                person = p.id("person", day.id, tag)
                candidacy = p.id("candidacy", day.id, tag)
                people.append({"id": person})
                named.append(
                    {
                        "id": p.id("name", person),
                        "entity_id": person,
                        "text": c.name,
                        "lang": "bg",
                        "kind": "official",
                        "is_preferred": True,
                    }
                )
                independent = not council and norm(c.name) == norm(c.party_name)
                row: dict[str, object] = {
                    "id": candidacy,
                    "person_id": person,
                    "option_id": option_of[c.party],
                    "role": "list_member" if council else "head",
                    "ballot_name": c.name,
                }
                if council:
                    row["list_position"] = c.position
                if independent:
                    row["is_independent"] = True
                runs.append(row)
                if c.flag == "1":
                    elected[c.party].append(candidacy)
                if c.flag:
                    flags[c.party] = c.flag
            if council:
                for n, members in elected.items():
                    outcomes.append(
                        {"result_set_id": rs, "option_id": option_of[n], "kind": "won", "seats": len(members)}
                    )
                    outcomes.extend(
                        {"result_set_id": rs, "option_id": option_of[n], "candidacy_id": m, "kind": "elected"}
                        for m in members
                    )
                if elected:
                    contests_[-1]["magnitude"] = sum(len(m) for m in elected.values())
                elif day.day.year == 2011:  # the 2011 files give each list's seats but not who took them
                    won = seats_2011(day).get(unit, {})
                    outcomes.extend(
                        {"result_set_id": rs, "option_id": option_of[n], "kind": "won", "seats": count}
                        for n, count in sorted(won.items(), key=lambda kv: int(kv[0]))
                        if count
                    )
                    if won:
                        contests_[-1]["magnitude"] = sum(won.values())
            elif flags:
                winner = any(f == "1" for f in flags.values())
                for c in mine:
                    kind = {"1": "elected", "2": "advanced"}.get(
                        c.flag, "lost" if winner or day.round == 2 else "eliminated"
                    )
                    outcomes.append({"result_set_id": rs, "option_id": option_of[c.party], "kind": kind})
            elif unit in later:
                for c in mine:
                    kind = "advanced" if (norm(c.name), norm(c.party_name)) in later[unit] else "eliminated"
                    outcomes.append({"result_set_id": rs, "option_id": option_of[c.party], "kind": kind})

            # values
            for section, f in fig_by_unit[unit].items():
                figures_all[(day.id, ckey, section)] = f
            for section, per in tal_by_unit[unit].items():
                votes_all[(day.id, ckey, section)] = per
            sections_here = sorted({*fig_by_unit[unit], *tal_by_unit[unit]})
            for section in sections_here:
                sec = secs.get((section, unit))
                uid = key("unit", day.id, section)
                if section not in unit_rows:
                    mobile = sec is not None and sec.mobile
                    unit_rows[section] = {
                        "id": uid,
                        "polity_id": BULGARIA,
                        "scheme_code": SIK_SCHEME,
                        "code": f"{day.id}:{section}",
                        "area_relation": "within",
                        "area_id": obshtina(f"{oik_of(section):04d}"),
                        "kind": "mobile" if mobile else "standard",
                    }
                    if sec is not None and sec.place:
                        unit_names[section] = {
                            "id": p.id("name", uid),
                            "entity_id": uid,
                            "text": f"{sec.place}, {section}",
                            "lang": "bg",
                            "kind": "official",
                            "is_preferred": True,
                        }
                f = fig_by_unit[unit].get(section, {})
                for name, measure in (
                    ("reg", REGISTERED),
                    ("voters", VOTERS),
                    ("box", BALLOTS),
                    ("valid", VALID),
                    ("inv", INVALID),
                ):
                    if name in f:
                        put(rs, uid, None, measure, f[name])
                for n, count in tal_by_unit[unit].get(section, {}).items():
                    put(rs, uid, option_of[n], VOTES, count)
                if none_option is not None and "none" in f:
                    put(rs, uid, none_option, VOTES, f["none"])

    stats["sections"] = len(unit_rows)
    p.frame("party", pl.DataFrame(party_rows))
    p.frame("name", pl.DataFrame(party_names))
    p.frame("contest", pl.DataFrame(contests_, infer_schema_length=None))
    p.frame("name", pl.DataFrame(contest_names_))
    p.frame("slug", pl.DataFrame(slugs))
    p.frame("result_set", pl.DataFrame(result_sets))
    p.frame("contest_option", pl.DataFrame(options, infer_schema_length=None))
    p.frame("person", pl.DataFrame(people))
    p.frame("name", pl.DataFrame(named))
    p.frame("candidacy", pl.DataFrame(runs, infer_schema_length=None))
    if outcomes:
        p.frame("outcome", pl.DataFrame(outcomes, infer_schema_length=None))
    p.frame("reporting_unit", pl.DataFrame(list(unit_rows.values())))
    p.frame("name", pl.DataFrame(list(unit_names.values())))
    frame = values_frame(cols).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    accept(p, day, figures_all, votes_all)
    return p


def accept(
    p: Package,
    day: Day,
    figures: dict[tuple[str, str, str], dict[str, int]],
    votes: dict[tuple[str, str, str], dict[str, int]],
) -> None:
    over = sum(1 for f in figures.values() if "box" in f and "valid" in f and f["box"] != f.get("inv", 0) + f["valid"])
    short = 0
    for k, f in figures.items():
        if "valid" in f and sum(votes.get(k, {}).values()) + f.get("none", 0) != f["valid"]:
            short += 1
    if over:
        p.accept(
            "accounting.ballots_sum",
            f"the protocols of {over:,} section counts print ballots in the box that differ from invalid plus valid "
            "ballots; stored as printed",
        )
    if short:
        p.accept(
            "accounting.votes_valid",
            f"in {short:,} section counts the votes printed for the lists or candidates do not add up to the valid "
            "ballots printed in the protocol; stored as printed",
        )
    if any("reg" in f and "voters" in f and f["voters"] > f["reg"] for f in figures.values()):
        p.accept(
            "accounting.turnout",
            "some sections report more voters than the roll holds, from voters added on the day under the line of "
            "the roll or with voting certificates; stored as printed",
        )


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/geo", *(f"{SOURCE}/{d.id}" for d in DAYS)]


def parse() -> None:
    registry().write()
    geo().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())
