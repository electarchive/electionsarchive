"""Kosovo's local elections from the Central Election Commission's results archive (kqz-ks.org/rezultatet).

The archive publishes each municipal assembly and mayoral election as a folder of result files: tidy workbooks for
the 2017 and later elections and PDF reports for the earlier ones. Each election day becomes one package. The files
are read from the raw cache, where `store_fetched` puts them.
"""

import difflib
import functools
import hashlib
import json
import os
import re
import subprocess
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import fastexcel
import xlrd

from ea import raw
from ea.package import Package
from ea.sources.xk import kqz

SOURCE = kqz.SOURCE
UPLOADS = "https://kqz-ks.org/wp-content/uploads/"
FETCHED = "EA_KQZ_LOCAL_FETCHED"
LOCAL_SOURCE = kqz.key("source", "xk.kqz.local")
MAYOR_SYSTEM = kqz.key("electoral_system", "local", "mayor")
ASSEMBLY_SYSTEM = kqz.key("electoral_system", "local", "municipal_assembly")
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
}


def store_fetched(folder: Path) -> int:
    """Put the files fetched for the local elections into the raw cache; `folder` holds them and their `index.json`."""
    index = json.loads((folder / "index.json").read_text())
    stored = 0
    for link, entry in index.items():
        if entry["media_type"] == "text/html":
            continue
        content = (folder / entry["file"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"{link} does not match its checksum")
        suffix = Path(link).suffix.lower()
        raw.store(
            SOURCE,
            entry.get("source", link),
            content,
            suffix=suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            # An archived copy can come back labeled as plain text; the file's own extension names its type.
            media_type=MEDIA_TYPES.get(suffix, entry["media_type"]),
        )
        stored += 1
    return stored


def fetch() -> None:
    folder = os.environ.get(FETCHED)
    if folder:
        print(f"{store_fetched(Path(folder))} local election result files stored", flush=True)


# Reading the files.


def ascii_fold(text: object) -> str:
    text = str(text if text is not None else "").replace("\xa0", " ").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).casefold().split())


def clean(text: object) -> str:
    return kqz.clean(text)


@functools.cache
def _index() -> dict[str, str]:
    """The original address of every cached file, to the key it is cached under."""
    found: dict[str, str] = {}
    for key in raw.keys(SOURCE):
        original = re.sub(r"^https://web\.archive\.org/web/\d+id_/", "", key)
        found[unquote(original)] = key
    return found


USED: dict[str, dict[str, Any]] = defaultdict(dict)  # package key -> file name -> artifact
CURRENT: list[str] = [""]


def artifact(name: str) -> Any:
    """The cached file that was published at `uploads/{name}`."""
    original = UPLOADS + name
    key = _index().get(original) or _index().get(unquote(original))
    if key is None:
        raise LookupError(f"{original} is not in the raw cache")
    found = raw.cached(SOURCE, key)
    USED[CURRENT[0]][name] = found
    return found


@functools.cache
def sheets(name: str) -> list[tuple[str, list[list[Any]]]]:
    """Every sheet of a workbook as its name and a list of rows."""
    path = artifact(name).path
    if name.lower().endswith(".xls"):
        book = xlrd.open_workbook(path)
        return [(s.name, [s.row_values(r) for r in range(s.nrows)]) for s in book.sheets()]
    book = fastexcel.read_excel(str(path))
    out = []
    for sheet in book.sheet_names:
        frame = book.load_sheet_by_name(sheet, header_row=None, dtypes="string").to_polars()
        out.append((sheet, [list(row) for row in frame.iter_rows()]))
    return out


@functools.cache
def pdf_text(name: str) -> str:
    path = artifact(name).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def text(value: object) -> str:
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return clean(value)


def number(value: object) -> int:
    t = text(value).replace(" ", "").replace(",", "")
    if t == "":
        return 0
    return int(float(t))


# Municipalities.

MUNICIPALITY_WORDS = (
    ("deca", "1"),
    ("gjakov", "2"),
    ("dakovic", "2"),
    ("gllogov", "3"),
    ("glogova", "3"),
    ("drenas", "3"),
    ("gjilan", "4"),
    ("gnjilan", "4"),
    ("dragas", "5"),
    ("istog", "6"),
    ("istok", "6"),
    ("kacanik", "7"),
    ("klin", "8"),
    ("fushe kosov", "9"),
    ("kosovo polje", "9"),
    ("kamenic", "10"),
    ("leposav", "12"),
    ("lipjan", "13"),
    ("lipljan", "13"),
    ("novober", "14"),
    ("novo brdo", "14"),
    ("obili", "15"),
    ("rahovec", "16"),
    ("orahovac", "16"),
    ("peje", "17"),
    ("pec", "17"),
    ("podujev", "18"),
    ("prishtin", "19"),
    ("pristina", "19"),
    ("prizren", "20"),
    ("skenderaj", "21"),
    ("srbica", "21"),
    ("shtime", "22"),
    ("stimlje", "22"),
    ("shterpc", "23"),
    ("strpce", "23"),
    ("suhare", "24"),
    ("suva reka", "24"),
    ("ferizaj", "25"),
    ("urosevac", "25"),
    ("viti", "26"),
    ("vushtrri", "27"),
    ("vucitrn", "27"),
    ("zubin", "28"),
    ("zvecan", "29"),
    ("malishev", "30"),
    ("malisevo", "30"),
    ("junik", "31"),
    ("mamush", "32"),
    ("elezit", "33"),
    ("hanii", "33"),
    ("elezhan", "33"),
    ("hani i", "33"),
    ("elez han", "33"),
    ("gracanic", "34"),
    ("ranil", "35"),
    ("partes", "36"),
    ("kllokot", "37"),
    ("klokot", "37"),
)


def municipality_code(name: object) -> str:
    """The Commission's code of a municipality from its printed name, in Albanian, Serbian, or both."""
    folded = ascii_fold(name)
    if not folded:
        raise ValueError("empty municipality name")
    if "mitrovic" in folded:
        return "38" if any(w in folded for w in ("veriut", "severna", "north", "sever")) else "11"
    hits = {code for word, code in MUNICIPALITY_WORDS if word in folded}
    # 'klin' also matches nothing else, but 'viti' sits inside other names and 'pec' inside 'ranilluk'.
    if len(hits) > 1:
        first = ascii_fold(str(name).split("/")[0])
        hits = {code for word, code in MUNICIPALITY_WORDS if word in first} or hits
    if len(hits) != 1:
        raise ValueError(f"cannot place municipality {name!r}: {sorted(hits)}")
    return next(iter(hits))


def try_code(name: str) -> str | None:
    try:
        return municipality_code(name)
    except ValueError:
        return None


def station_municipality(code: str) -> str:
    """The municipality a polling station belongs to, from its code."""
    if re.match(r"\d{4}[A-Z](/|$)", code):
        return str(int(code[:2]))
    if re.match(r"(CB|BM)\d{3}/", code):
        return str(int(code[3:5]))
    raise ValueError(f"unexpected station code {code}")


def station_kind(code: str) -> str:
    if code.startswith("CB"):
        return "conditional"
    if code.startswith("BM"):
        return "postal"
    return "regular"


UNIT_KIND = {"regular": "standard", "conditional": "standard", "postal": "postal_district", "aggregate": "aggregate"}


# Parties and candidates.


def party_text(name: object) -> str:
    """A political entity's name as printed, without the ballot number and a duplicated abbreviation."""
    t = clean(name)
    t = re.sub(r"^\d{1,3}\s+", "", t)
    t = re.sub(r"^[A-ZËÇÖÜÀ-Ž]{2,6}\s+-\s+(?=\S)", "", t)
    t = re.sub(r"\s+-\s+[A-ZËÇÖÜÀ-Ž]{2,6}$", "", t)
    return t


def party_id(name: object) -> str:
    folded = ascii_fold(party_text(name)).replace("partija", "partia")
    return " ".join(folded.split())


@dataclass
class Option:
    label: str  # the entity's name as printed
    candidate: str | None = None


@dataclass
class Unit:
    code: str
    scheme: str
    kind: str
    votes: dict[str, int] = field(default_factory=dict)
    figures: dict[int, int] = field(default_factory=dict)


@dataclass
class Contest:
    municipality: str
    options: dict[str, Option] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    seats: dict[str, int] = field(default_factory=dict)
    figures: dict[int, int] = field(default_factory=dict)
    units: dict[str, Unit] = field(default_factory=dict)

    def option(self, label: object, candidate: object = None) -> str:
        key = party_id(label)
        if key not in self.options:
            self.options[key] = Option(party_text(label), clean(candidate) if candidate else None)
        return key

    def unit(self, code: str, scheme: str = "xk.vendvotimi") -> Unit:
        if code not in self.units:
            kind = "aggregate" if scheme == "xk.komuna" else station_kind(code)
            self.units[code] = Unit(code, scheme, kind)
        return self.units[code]


@dataclass
class Poll:
    day: date
    kind: str  # "mayor" or "assembly"
    contests: dict[str, Contest] = field(default_factory=dict)

    def contest(self, municipality: str) -> Contest:
        return self.contests.setdefault(municipality, Contest(municipality))


def match_header(header: str, contest: Contest, mayor: bool) -> str:
    """The option a column header names, by the candidate's name for mayors and by the entity's name otherwise."""
    wanted = ascii_fold(header)
    scores = []
    for key, option in contest.options.items():
        target = ascii_fold(option.candidate if mayor and option.candidate else option.label)
        scores.append((target in wanted, difflib.SequenceMatcher(None, target, wanted).ratio(), key))
    scores.sort(reverse=True)
    if not scores or (not scores[0][0] and scores[0][1] < 0.5) or (len(scores) > 1 and scores[0][:2] == scores[1][:2]):
        raise ValueError(f"cannot match column {header!r} to {sorted(contest.options)}")
    return scores[0][2]


# The extraordinary elections of 2019 and 2023 and the rest: each loader returns a Poll.

STATISTICS = (
    ("regjistruar", REGISTERED),
    ("pranuar i fletevotimeve te vlefshme", VALID),
    ("pavlefshme", INVALID),
    ("zbrazeta", BLANK),
)


def report_municipalities(name: str, mayor: bool, poll: Poll) -> None:
    """The 'Report' workbook of the results by municipality: a block of rows for each municipality."""
    contest: Contest | None = None
    for row in sheets(name)[0][1]:
        cells = [text(c) for c in row]
        if cells[0].startswith("Komuna"):
            contest = poll.contest(municipality_code(next(c for c in cells[1:] if c)))
            continue
        if contest is None or not cells[0].isdigit():
            continue
        if mayor:
            key = contest.option(cells[1], cells[3])
            contest.totals[key] = number(cells[4])
        else:
            key = contest.option(cells[1])
            contest.totals[key] = number(cells[3])
            contest.seats[key] = number(cells[5])


def report_statistics(name: str, poll: Poll) -> None:
    contest: Contest | None = None
    for row in sheets(name)[0][1]:
        cells = [text(c) for c in row]
        if cells[0].startswith("Komuna"):
            contest = poll.contest(municipality_code(next(c for c in cells[1:] if c)))
            continue
        if contest is None or not cells[4]:
            continue
        label = ascii_fold(cells[0])
        for word, measure in STATISTICS:
            if word in label:
                contest.figures[measure] = number(cells[4])


def station_results(name: str, poll: Poll, mayor: bool) -> None:
    """Workbooks with a sheet per group of municipalities and a column for each option."""
    for _, rows in sheets(name):
        head = [text(c) for c in rows[0]]
        for row in rows[1:]:
            cells = [text(c) for c in row]
            if not cells[1]:
                continue
            m = station_municipality(cells[1])
            contest = poll.contest(m)
            unit = contest.unit(cells[1])
            for column in range(2, len(head)):
                key = match_header(head[column], contest, mayor)
                unit.votes[key] = number(cells[column])


def station_statistics(name: str, poll: Poll) -> None:
    for _, rows in sheets(name):
        head = [ascii_fold(c) for c in rows[0]]
        for row in rows[1:]:
            cells = [text(c) for c in row]
            if not cells[1]:
                continue
            unit = poll.contest(station_municipality(cells[1])).unit(cells[1])
            for column, label in enumerate(head):
                if column < 2 or cells[column] == "":
                    continue
                if "votuesve" in label:
                    unit.figures[REGISTERED] = number(cells[column])
                elif "zbrazeta" in label:
                    unit.figures[BLANK] = number(cells[column])
                elif "pavlefshme" in label:
                    unit.figures[INVALID] = number(cells[column])
                elif "gjithsej vota" in label:
                    unit.figures[VALID] = number(cells[column])


def outside_results(name: str, poll: Poll, mayor: bool, kind: str) -> None:
    """Conditional or postal votes by municipality and option, kept as that municipality's single such station."""
    for row in sheets(name)[0][1][1:]:
        cells = [text(c) for c in row]
        if not cells[0]:
            continue
        contest = poll.contest(municipality_code(cells[0]))
        key = match_header(cells[2] if mayor else cells[1], contest, mayor)
        contest.units.setdefault(kind, Unit(kind, "xk.vendvotimi", kind)).votes[key] = number(cells[3])


def outside_statistics(name: str, poll: Poll, kind: str) -> None:
    for row in sheets(name)[0][1][1:]:
        cells = [text(c) for c in row]
        if not cells[0] or not cells[1]:
            continue
        unit = poll.contest(municipality_code(cells[0])).units[kind]
        unit.code = cells[1]
        head = [ascii_fold(c) for c in sheets(name)[0][1][0]]
        for column, label in enumerate(head):
            if column < 2 or cells[column] == "":
                continue
            if "zbrazeta" in label:
                unit.figures[BLANK] = number(cells[column])
            elif "pavlefshme" in label:
                unit.figures[INVALID] = number(cells[column])
            elif "gjithsej vota" in label:
                unit.figures[VALID] = number(cells[column])


def renamed(poll: Poll) -> Poll:
    """Give the conditional and postal stations of each municipality their printed codes."""
    for contest in poll.contests.values():
        contest.units = {unit.code: unit for unit in contest.units.values()}
    return poll


def may_2019() -> Poll:
    poll = Poll(date(2019, 5, 19), "mayor")
    folder = "2019/05/"
    report_municipalities(folder + "1.-Rezultatet-sipas-komunave-2.xlsx", True, poll)
    report_statistics(folder + "2.-Statistikat-sipas-komunave-2.xlsx", poll)
    station_results(folder + "3.-Rezultatet-sipas-vendvotimeve-2.xlsx", poll, True)
    station_statistics(folder + "4.-Statistikat-sipas-vendvotimeve-2.xlsx", poll)
    outside_results(folder + "5.-Rezultatet-e-votave-me-kusht-2.xlsx", poll, True, "conditional")
    outside_statistics(folder + "6.-Statistikat-e-votave-me-kusht-2.xlsx", poll, "conditional")
    outside_results(folder + "7.-Rezultatet-e-votave-me-postë-2.xlsx", poll, True, "postal")
    outside_statistics(folder + "8.-Statistikat-e-votave-me-postë-2.xlsx", poll, "postal")
    return renamed(poll)


def apr_2023_mayor() -> Poll:
    poll = Poll(date(2023, 4, 23), "mayor")
    folder = "2023/04/"
    report_municipalities(folder + "1.-Rezultatet-sipas-komunave_Kryetar-Komune.xlsx", True, poll)
    station_results(folder + "3.-Rezultatet-sipas-vendvotimeve_Kryetar-Komune.xlsx", poll, True)
    return poll


def apr_2023_assembly() -> Poll:
    poll = Poll(date(2023, 4, 23), "assembly")
    folder = "2023/04/"
    report_municipalities(folder + "1.-Rezultatet-sipas-komunave_Kuvende-Komunale.xlsx", False, poll)
    station_results(folder + "3.-Rezultatet-sipas-vendvotimeve_Kuvend-Komunal.xlsx", poll, False)
    return poll


# Names of the municipalities, in Albanian and Serbian, for the titles of contests.

NAMES = {
    "1": ("Deçan", "Dečani"),
    "2": ("Gjakovë", "Đakovica"),
    "3": ("Drenas", "Glogovac"),
    "4": ("Gjilan", "Gnjilane"),
    "5": ("Dragash", "Dragaš"),
    "6": ("Istog", "Istok"),
    "7": ("Kaçanik", "Kačanik"),
    "8": ("Klinë", "Klina"),
    "9": ("Fushë Kosovë", "Kosovo Polje"),
    "10": ("Kamenicë", "Kamenica"),
    "11": ("Mitrovicë", "Mitrovica"),
    "12": ("Leposaviq", "Leposavić"),
    "13": ("Lipjan", "Lipljan"),
    "14": ("Novobërdë", "Novo Brdo"),
    "15": ("Obiliq", "Obilić"),
    "16": ("Rahovec", "Orahovac"),
    "17": ("Pejë", "Peć"),
    "18": ("Podujevë", "Podujevo"),
    "19": ("Prishtinë", "Priština"),
    "20": ("Prizren", "Prizren"),
    "21": ("Skenderaj", "Srbica"),
    "22": ("Shtime", "Štimlje"),
    "23": ("Shtërpcë", "Štrpce"),
    "24": ("Suharekë", "Suva Reka"),
    "25": ("Ferizaj", "Uroševac"),
    "26": ("Viti", "Vitina"),
    "27": ("Vushtrri", "Vučitrn"),
    "28": ("Zubin Potok", "Zubin Potok"),
    "29": ("Zveçan", "Zvečan"),
    "30": ("Malishevë", "Mališevo"),
    "31": ("Junik", "Junik"),
    "32": ("Mamushë", "Mamuša"),
    "33": ("Hani i Elezit", "Elez Han"),
    "34": ("Graçanicë", "Gračanica"),
    "35": ("Ranillug", "Ranilug"),
    "36": ("Partesh", "Parteš"),
    "37": ("Kllokot", "Klokot"),
    "38": ("Mitrovicë e Veriut", "Severna Mitrovica"),
}


@dataclass
class Spec:
    day: date
    kind: str  # "local", "runoff", or "by_election"
    en: str
    sq: str
    loaders: tuple[str, ...]
    previous: date | None = None


SPECS = (
    Spec(
        date(2017, 10, 22),
        "local",
        "2017 Kosovan local elections",
        "Zgjedhjet lokale 2017",
        ("oct_2017_mayor", "oct_2017_assembly"),
    ),
    Spec(
        date(2017, 11, 19),
        "runoff",
        "2017 Kosovan local elections, mayoral runoffs",
        "Zgjedhjet lokale 2017, balotazhi për kryetar",
        ("nov_2017_mayor",),
        date(2017, 10, 22),
    ),
    Spec(
        date(2019, 5, 19),
        "by_election",
        "2019 Kosovan extraordinary mayoral elections in four northern municipalities",
        "Zgjedhjet e jashtëzakonshme për kryetar komune 2019",
        ("may_2019",),
    ),
    Spec(
        date(2021, 10, 17),
        "local",
        "2021 Kosovan local elections",
        "Zgjedhjet lokale 2021",
        ("oct_2021_mayor", "oct_2021_assembly"),
    ),
    Spec(
        date(2021, 11, 14),
        "runoff",
        "2021 Kosovan local elections, mayoral runoffs",
        "Zgjedhjet lokale 2021, balotazhi për kryetar",
        ("nov_2021_mayor",),
        date(2021, 10, 17),
    ),
    Spec(
        date(2023, 4, 23),
        "by_election",
        "2023 Kosovan extraordinary local elections in four northern municipalities",
        "Zgjedhjet e jashtëzakonshme lokale 2023",
        ("apr_2023_mayor", "apr_2023_assembly"),
    ),
)
SPEC_BY_DAY = {str(spec.day): spec for spec in SPECS}
DAYS = tuple(str(spec.day) for spec in SPECS)


@functools.cache
def polls(day: str) -> tuple[Poll, ...]:
    CURRENT[0] = day
    return tuple(globals()[loader]() for loader in SPEC_BY_DAY[day].loaders)


# Offices, bodies, parties, and electoral systems.

MAYOR_RULES = {"formula": "two_round", "majority": 0.5, "advance": 2}
ASSEMBLY_RULES = {"formula": "sainte_lague", "lists": {"order": "open", "vacancy": "next_by_votes"}}
MAYOR_NOTE = (
    "A municipality's mayor is elected directly. A candidate with more than half of the valid votes is elected in the "
    "first round; otherwise the two leading candidates meet in a runoff, and the one with more votes is elected."
)
ASSEMBLY_NOTE = (
    "A municipal assembly is elected by open-list proportional representation in a single municipality-wide district. "
    "Seats are divided among the political entities by the Sainte-Laguë method, and each entity's seats go to its "
    "candidates in order of their preference votes, subject to the gender quota, and some seats are reserved for "
    "non-majority communities."
)


def party_kind(label: str) -> str:
    folded = ascii_fold(label)
    if "koalic" in folded:
        return "bloc"
    if "iniciativ" in folded or "inicijativ" in folded:
        return "independent_group"
    return "party"


def party(key: str) -> str:
    return kqz.key("party", "local", key)


def independent(label: str, candidates: set[str]) -> bool:
    """An entity that is a single candidate standing on their own, named for themselves."""
    return ascii_fold(label) in candidates


def registry() -> Package:
    p = Package(
        f"{SOURCE}/local-registry",
        SOURCE,
        "The political entities on the ballots of Kosovo's local elections and the electoral systems of its mayors and "
        "municipal assemblies",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {kqz.KOSOVO}
    p.add(
        "source",
        id=LOCAL_SOURCE,
        kind="official_body",
        publisher="Komisioni Qendror i Zgjedhjeve",
        title="Results of the local elections, KQZ results archive",
        url="https://kqz-ks.org/rezultatet/zgjedhjet-per-kryetare-te-komunave/",
        license="Official election results of the Republic of Kosovo, published for public use",
        license_url="https://kqz-ks.org/rezultatet/",
        attribution="Central Election Commission of the Republic of Kosovo (KQZ), kqz-ks.org",
    )
    for id, rules, note in ((MAYOR_SYSTEM, MAYOR_RULES, MAYOR_NOTE), (ASSEMBLY_SYSTEM, ASSEMBLY_RULES, ASSEMBLY_NOTE)):
        p.add(
            "electoral_system",
            id=id,
            polity_id=kqz.KOSOVO,
            valid_from=date(2008, 6, 15),
            valid_to=date(2025, 10, 11),
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=kqz.key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    names: dict[str, dict[str, str]] = defaultdict(dict)  # party key -> day -> printed name
    people: set[str] = set()
    for day in DAYS:
        for poll in polls(day):
            for contest in poll.contests.values():
                for option in contest.options.values():
                    if option.candidate:
                        people.add(ascii_fold(option.candidate))
    for day in DAYS:
        for poll in polls(day):
            for contest in poll.contests.values():
                for key, option in contest.options.items():
                    if not independent(option.label, people):
                        names[key][day] = option.label
    for key, by_day in sorted(names.items()):
        id = party(key)
        p.add("party", id=id, kind=party_kind(by_day[max(by_day)]), polity_id=kqz.KOSOVO)
        latest = by_day[max(by_day)]
        p.name(id, latest, "sq", "official")
        for label in sorted(set(by_day.values()) - {latest}):
            p.name(id, label, "sq", "ballot", False)
    return p


# The elections.


def person(day: date, m: str, name: str) -> str:
    return kqz.key("person", "local", day, m, ascii_fold(name))


def election_package(day: str) -> Package:
    spec = SPEC_BY_DAY[day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {spec.en} by polling station from the Central Election Commission",
        depends=[
            f"{SOURCE}/geo",
            f"{SOURCE}/registry",
            f"{SOURCE}/local-registry",
            *([f"{SOURCE}/{spec.previous}"] if spec.previous else []),
        ],
    )
    all_polls = polls(day)
    p.external |= {kqz.KOSOVO}
    election_id = kqz.key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=kqz.KOSOVO,
        kind=spec.kind,
        date_from=spec.day,
        date_to=spec.day,
        status="held",
    )
    p.name(election_id, spec.en[0].upper() + spec.en[1:], "en", "official")
    p.name(election_id, spec.sq, "sq", "native", False)
    path = f"xk/{day}-{'local-runoff' if spec.kind == 'runoff' else 'local' if spec.kind == 'local' else 'local-extraordinary'}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    people = {
        ascii_fold(o.candidate)
        for poll in all_polls
        for c in poll.contests.values()
        for o in c.options.values()
        if o.candidate
    }
    facts = kqz.Facts()
    unit_issues: dict[str, list[str]] = {}
    declared_issues: dict[str, list[str]] = {}
    made_units: set[str] = set()
    made_people: set[str] = set()
    for poll in all_polls:
        mayor = poll.kind == "mayor"
        for m, contest in sorted(poll.contests.items(), key=lambda item: int(item[0])):
            sq, sr = NAMES[m]
            place = path_slug(sq)
            runoff = spec.kind == "runoff"
            id = kqz.key("contest", day, poll.kind, m)
            if mayor:
                p.add(
                    "contest",
                    id=id,
                    election_id=election_id,
                    kind="office",
                    office_id=kqz.office(m),
                    district_area_id=kqz.municipality(m),
                    magnitude=1,
                    round_number=2 if runoff else 1,
                    previous_round_id=kqz.key("contest", spec.previous, "mayor", m) if runoff else None,
                    electoral_system_id=MAYOR_SYSTEM,
                    default_result_kind="counted",
                )
                suffix = ", runoff" if runoff else ""
                p.name(id, f"Mayor of {sq}{suffix}", "en", "official")
                p.name(id, f"Kryetari i Komunës së {sq}{', balotazhi' if runoff else ''}", "sq", "native", False)
                p.name(id, f"Gradonačelnik opštine {sr}{', drugi krug' if runoff else ''}", "sr", "native", False)
            else:
                p.add(
                    "contest",
                    id=id,
                    election_id=election_id,
                    kind="list_allocation",
                    body_id=kqz.body(m),
                    district_area_id=kqz.municipality(m),
                    round_number=1,
                    electoral_system_id=ASSEMBLY_SYSTEM,
                    default_result_kind="counted",
                )
                p.name(id, f"Municipal Assembly of {sq}", "en", "official")
                p.name(id, f"Kuvendi Komunal i {sq}", "sq", "native", False)
                p.name(id, f"Skupština opštine {sr}", "sr", "native", False)
            p.add("slug", path=f"{path}/{place}/{poll.kind}", entity_id=id, is_current=True)
            rs = kqz.key("result_set", day, poll.kind, m)
            declared_votes = sum(contest.totals.values())
            station_votes = sum(sum(u.votes.values()) for u in contest.units.values())
            p.add(
                "result_set",
                id=rs,
                election_id=election_id,
                contest_id=id,
                stage="official",
                kind="counted",
                source_id=LOCAL_SOURCE,
                status="active",
                is_default=True,
                completeness=min(1.0, station_votes / declared_votes) if declared_votes else 1.0,
            )
            p.add(
                "note",
                id=kqz.key("note", rs),
                entity_id=rs,
                kind="methodology",
                body_md=method(contest, poll.kind),
                lang="en",
            )
            options: dict[str, str] = {}
            for key, option in sorted(contest.options.items()):
                oid = options[key] = kqz.key("option", day, poll.kind, m, key)
                alone = independent(option.label, people)
                p.add(
                    "contest_option",
                    id=oid,
                    contest_id=id,
                    kind="candidate" if mayor or alone else "list",
                    party_id=None if alone else party(key),
                    ballot_label=option.candidate if mayor and option.candidate else option.label,
                )
                if mayor and option.candidate:
                    who = person(spec.previous or spec.day, m, option.candidate)
                    if who not in made_people and not runoff:
                        made_people.add(who)
                        p.add("person", id=who)
                        p.name(who, option.candidate, "sq", "official")
                    p.add(
                        "candidacy",
                        id=kqz.key("candidacy", day, poll.kind, m, key),
                        person_id=who,
                        option_id=oid,
                        role="head",
                        ballot_name=option.candidate,
                        member_of_party_id=None if alone else party(key),
                        is_independent=alone,
                    )
            for key, votes in sorted(contest.totals.items()):
                p.add("declared", result_set_id=rs, option_id=options[key], measure=VOTES, value=votes)
                if not mayor and contest.seats.get(key):
                    p.add("outcome", result_set_id=rs, option_id=options[key], kind="won", seats=contest.seats[key])
            if VALID in contest.figures:
                p.add("declared", result_set_id=rs, option_id=None, measure=VALID, value=contest.figures[VALID])
            if mayor:
                outcome(p, rs, options, contest, spec, m, day)
            for code, unit in contest.units.items():
                if VALID in unit.figures and sum(unit.votes.values()) != unit.figures[VALID]:
                    unit_issues.setdefault(sq, []).append(code)
            if contest.units and (
                any(sum(u.votes.get(k, 0) for u in contest.units.values()) != v for k, v in contest.totals.items())
                or (
                    VALID in contest.figures
                    and sum(u.figures.get(VALID, 0) for u in contest.units.values()) != contest.figures[VALID]
                )
            ):
                declared_issues.setdefault(sq, []).append(poll.kind)
            # The values: each station, or the municipality as one aggregate unit when no station is published.
            if contest.units:
                for code, unit in sorted(contest.units.items()):
                    uid = kqz.unit_id(spec.day, code)
                    if uid not in made_units:
                        made_units.add(uid)
                        p.add(
                            "reporting_unit",
                            id=uid,
                            polity_id=kqz.KOSOVO,
                            scheme_code="xk.vendvotimi",
                            code=f"{day}:{code}",
                            area_relation="within",
                            area_id=kqz.municipality(m),
                            kind=UNIT_KIND[unit.kind],
                        )
                        p.name(uid, code, "sq", "official")
                    if unit.kind == "regular" and unit.figures.get(REGISTERED):
                        facts.add(rs, uid, None, REGISTERED, unit.figures[REGISTERED])
                    for measure, value in unit.figures.items():
                        if measure != REGISTERED:
                            facts.add(rs, uid, None, measure, value)
                    for key, votes in unit.votes.items():
                        facts.add(rs, uid, options[key], VOTES, votes)
            else:
                uid = kqz.key("unit", f"{day}:municipality:{m}")
                if uid not in made_units:
                    made_units.add(uid)
                    p.add(
                        "reporting_unit",
                        id=uid,
                        polity_id=kqz.KOSOVO,
                        scheme_code="xk.komuna",
                        code=f"{day}:{m}",
                        area_relation="equals",
                        area_id=kqz.municipality(m),
                        kind="aggregate",
                    )
                    p.name(uid, f"{sq}", "sq", "official")
                for measure, value in contest.figures.items():
                    facts.add(rs, uid, None, measure, value)
                for key, votes in contest.totals.items():
                    facts.add(rs, uid, options[key], VOTES, votes)
    if unit_issues:
        count = sum(len(v) for v in unit_issues.values())
        p.accept(
            "accounting.votes_valid",
            f"In {count} polling units, in {len(unit_issues)} municipalities ({', '.join(sorted(unit_issues))}), the "
            "votes of the Commission's results file do not sum to the valid ballots its statistics file prints for the "
            "same unit; both are stored as printed.",
        )
    if declared_issues:
        p.accept(
            "accounting.declared",
            f"In {len(declared_issues)} municipalities ({', '.join(sorted(declared_issues))}) the Commission's "
            "municipal totals do not equal the sum of its unit files; the totals are stored as declared and the units "
            "as printed, and each result set's completeness records the share the units cover.",
        )
    for name, found in sorted(USED[day].items()):
        p.artifact(found, LOCAL_SOURCE)
    p.frame("result_value", facts.frame())
    return p


def path_slug(text: str) -> str:
    from ea.changeset import slug

    return slug(text)


def method(contest: Contest, kind: str) -> str:
    text = (
        "Votes by polling station from the results files of the Commission's archive. Each polling station, the "
        "conditional-ballot station, and the postal station of a municipality is stored as its own reporting unit."
        if contest.units
        else "Votes by municipality from the results files of the Commission's archive."
    )
    if kind == "mayor":
        text += " The official outcome is the one the Commission declared: a candidate with more than half of the valid votes is elected."
    else:
        text += " Seats are those the Commission allocated to each political entity; candidates' preference votes are not stored."
    return text


def outcome(
    p: Package,
    rs: str,
    options: dict[str, str],
    contest: Contest,
    spec: Spec,
    m: str,
    day: str,
) -> None:
    ranked = sorted(contest.totals, key=lambda k: (-contest.totals[k], k))
    valid = sum(contest.totals.values())
    top = contest.totals[ranked[0]]
    if len(ranked) > 1 and top == contest.totals[ranked[1]]:
        return
    if 2 * top > valid or spec.kind == "runoff":
        p.add("outcome", result_set_id=rs, option_id=options[ranked[0]], kind="elected")
        return
    later = [s for s in SPECS if s.previous == spec.day]
    runoff = [
        {party_id(o.label) for o in poll.contests[m].options.values()}
        for s in later
        for poll in polls(str(s.day))
        if m in poll.contests
    ]
    if runoff:
        if runoff[0] != set(ranked[:2]):
            raise ValueError(f"{day} {m}: the runoff pair is not the two leading candidates")
        for key in ranked[:2]:
            p.add("outcome", result_set_id=rs, option_id=options[key], kind="advanced")


# The 2017 and 2021 elections: workbooks and reports that print the results as labeled rows.

STATION_CODE = re.compile(r"(?<![A-Z0-9])(\d{4}[A-Z]/\d{2}[A-Z])(?![A-Z0-9])")
MUNICIPALITY_LINE = re.compile(r"^[A-ZËÇŠČĆŽĐ' .-]{3,}/[A-ZËÇŠČĆŽĐ' ./-]{3,}$")
ENTITY = re.compile(r"^(\d{1,3})\s+[^\d\s]")
LABELS = (
    ("pavlefshme", INVALID),
    ("zbrazeta", BLANK),
    ("pranuar i fletevotimeve te vlefshme", VALID),
    ("votuesve te regjistruar", REGISTERED),
)
DERIVED = -1


def is_municipality(cell: str) -> bool:
    if "/" not in cell or len(cell) > 60 or any(ch.isdigit() for ch in cell) or ENTITY.match(cell):
        return False
    if not MUNICIPALITY_LINE.match(cell) and not re.fullmatch(
        r"[A-Za-zËÇëçŠČĆŽĐšđčćž' .-]+/[A-Za-zËÇëçŠČĆŽĐšđčćž' ./-]+", cell
    ):
        return False
    try:
        municipality_code(cell)
    except ValueError:
        return False
    return True


def label_measure(cells: list[str]) -> int | None:
    folded = ascii_fold(" ".join(c for c in cells if not re.fullmatch(r"[\d., -]+", c)))
    for word, measure in LABELS:
        if word in folded:
            return measure
    return None


def scan(name: str, mayor: bool) -> list[tuple[Any, ...]]:
    """The facts a report prints, in order: ("municipality", name), ("station", code), ("entity", label, candidate,
    votes), and ("figure", measure, value)."""
    out: list[tuple[Any, ...]] = []
    pending: tuple[str, str | None] | None = None  # an entity waiting for its votes
    measure: int | None = None
    for _, rows in sheets(name):
        for row in rows:
            cells = [text(c) for c in row if text(c) != ""]
            if not cells:
                continue
            numeric = [c for c in cells if re.fullmatch(r"\d[\d ]*(\.\d+)?", c)]
            if pending and len(cells) == 1 and numeric:
                out.append(("entity", pending[0], pending[1], number(cells[0])))
                pending = None
                continue
            entity = next(
                (i for i, c in enumerate(cells) if ENTITY.match(c) and (not mayor or i >= 1 or len(cells) > 1)), None
            )
            if entity is not None and (len(cells) > 1 or mayor is False):
                label = cells[entity]
                candidate = " ".join(cells[:entity]) if mayor else None
                if mayor and (not candidate or re.fullmatch(r"[\d., ]+", candidate)):
                    candidate = None
                after = [c for c in cells[entity + 1 :] if re.fullmatch(r"\d[\d ]*", c)]
                if after:
                    out.append(("entity", label, candidate, number(after[0])))
                    pending = None
                else:
                    pending = (label, candidate)
                continue
            if len(cells) <= 2 and any(is_municipality(c) for c in cells):
                out.append(("municipality", next(c for c in cells if is_municipality(c))))
                pending = None
                measure = None
                continue
            found = next((m for c in cells if (m := STATION_CODE.search(c))), None)
            if found:
                out.append(("station", found.group(1)))
                pending = None
                measure = None
                continue
            found_measure = label_measure(cells)
            figures = [c for c in cells if re.fullmatch(r"\d[\d ]*", c)]
            if found_measure is not None:
                measure = found_measure
            elif not figures:
                measure = None
            if measure is not None and figures:
                out.append(("figure", measure, number(figures[-1])))
                measure = None
    return out


def results_by_municipality(name: str, mayor: bool, poll: Poll) -> None:
    contest: Contest | None = None
    for item in scan(name, mayor):
        if item[0] == "municipality":
            contest = poll.contest(municipality_code(item[1]))
        elif item[0] == "entity" and contest is not None:
            contest.totals[contest.option(item[1], item[2])] = item[3]


def results_by_station(name: str, mayor: bool, poll: Poll) -> None:
    unit: Unit | None = None
    contest: Contest | None = None
    for item in scan(name, mayor):
        if item[0] == "station":
            contest = poll.contest(station_municipality(item[1]))
            unit = contest.unit(item[1])
        elif item[0] == "entity" and unit is not None and contest is not None:
            unit.votes[contest.option(item[1], item[2])] = item[3]


def stats_by_municipality(name: str, poll: Poll) -> None:
    contest: Contest | None = None
    for item in scan(name, False):
        if item[0] == "municipality":
            contest = poll.contest(municipality_code(item[1]))
        elif item[0] == "figure" and contest is not None:
            contest.figures.setdefault(item[1], item[2])


def stats_by_station(name: str, poll: Poll) -> None:
    unit: Unit | None = None
    for item in scan(name, False):
        if item[0] == "station":
            unit = poll.contest(station_municipality(item[1])).unit(item[1])
        elif item[0] == "figure" and unit is not None:
            unit.figures.setdefault(item[1], item[2])


def outside_unit(contest: Contest, kind: str) -> Unit:
    code = f"{'CB' if kind == 'conditional' else 'BM'}-{int(contest.municipality):02d}"
    if code not in contest.units:
        contest.units[code] = Unit(code, "xk.vendvotimi", kind)
    return contest.units[code]


def outside_tidy(name: str, poll: Poll, kind: str, mayor: bool) -> None:
    """A tidy workbook of the conditional or postal ballots: votes by municipality and entity, or the ballots of
    each such station, which are added up to one unit for the municipality."""
    rows = sheets(name)[0][1]
    head = [text(c) for c in rows[0]]
    for row in rows[1:]:
        cells = [text(c) for c in row]
        if not cells[1]:
            continue
        contest = poll.contest(municipality_code(cells[1]))
        unit = outside_unit(contest, kind)
        if head[3] == "NameParty":
            label = cells[3]
            candidate = f"{cells[4]} {cells[5]}" if mayor else None
            unit.votes[contest.option(label, candidate)] = number(cells[-1])
        else:
            for measure, column in ((BLANK, 3), (INVALID, 4), (VALID, 5)):
                unit.figures[measure] = unit.figures.get(measure, 0) + number(cells[column])


ASSEMBLY_LINE = re.compile(r"^\s+(\d{1,3}) (.+?)\s{2,}([\d ]+?)\s+([\d,]+)%\s+(\d+)\s*$")
MUNICIPALITY_HEADER = re.compile(r"^\s+(\d{1,2}) ([A-Z][A-Z .'-]*/[A-Z /.'-]*)$")


def assembly_pdf(name: str, poll: Poll) -> None:
    """The report of the municipal results with the seats of each entity."""
    contest: Contest | None = None
    for line in pdf_text(name).splitlines():
        header = MUNICIPALITY_HEADER.match(line)
        if header:
            contest = poll.contest(str(int(header.group(1))))
            continue
        found = ASSEMBLY_LINE.match(line)
        if found and contest is not None:
            key = contest.option(f"{found.group(1)} {found.group(2)}")
            contest.totals[key] = number(found.group(3))
            contest.seats[key] = number(found.group(5))


def oct_2021_mayor() -> Poll:
    poll = Poll(date(2021, 10, 17), "mayor")
    folder = "2021/10/"
    results_by_municipality(folder + "1.-ResultstByMinicipality_Mayor-1.xls", True, poll)
    stats_by_municipality(folder + "2.-StatsByMunicipality_Mayor-2.xlsx", poll)
    results_by_station(folder + "3.-ResultsByPS_Mayor-1.xls", True, poll)
    stats_by_station(folder + "4.-StatsByPS_Mayor-2.xls", poll)
    outside_tidy(folder + "15.-CB_PartyCandidatesByMunicipality_Report_Mayor.xlsx", poll, "conditional", True)
    outside_tidy(folder + "14.-CB_StatisticByMunicpality_Report_Mayor.xlsx", poll, "conditional", True)
    outside_tidy(folder + "19.-BM_PartyCandidatesByMunicipality_Report_Mayor.xlsx", poll, "postal", True)
    outside_tidy(folder + "18.-BM_StatisticByMunicpality_Rreport_Mayor.xlsx", poll, "postal", True)
    return poll


def nov_2021_mayor() -> Poll:
    poll = Poll(date(2021, 11, 14), "mayor")
    folder = "2021/11/"
    results_by_municipality(folder + "3.-Rezultatet-per-Kryetar-te-Komunave.xls", True, poll)
    stats_by_municipality(folder + "2.-Statistikat-sipas-komunave-1.xls", poll)
    results_by_station(folder + "5.-Rezultatet-sipas-vendvotimeve.xls", True, poll)
    stats_by_station(folder + "4.-Statistikat-sipas-vendvotimeve.xls", poll)
    outside_tidy(folder + "7.-Rezultatet-per-Kryetar-te-Komunave-sipas-votave-me-kusht.xlsx", poll, "conditional", True)
    outside_tidy(folder + "6.-Statistikat-e-votave-me-kusht-sipas-komunave.xlsx", poll, "conditional", True)
    outside_tidy(folder + "9.-Rezultatet-per-Kryetar-te-Komunave-sipas-votave-me-poste.xlsx", poll, "postal", True)
    outside_tidy(folder + "8.-Statistikat-e-votave-me-poste-sipas-komunave.xlsx", poll, "postal", True)
    return poll


def oct_2021_assembly() -> Poll:
    poll = Poll(date(2021, 10, 17), "assembly")
    folder = "2021/11/"
    assembly_pdf(folder + "03.-Rezultatet-e-subjekteve-dhe-ndarja-e-mandateve.pdf", poll)
    stats_by_municipality(folder + "02.-Statistikat-sipas-komunave.xls", poll)
    results_by_station(folder + "05.-Rezultatet-e-subjekteve-sipas-vendvotimeve.xls", False, poll)
    stats_by_station(folder + "04.-Statistikat-sipas-vendvotimeve.xls", poll)
    outside_tidy(folder + "11.-Rezultatet-e-votave-me-kusht-2.xlsx", poll, "conditional", False)
    outside_tidy(folder + "12.-Statistikat-e-votave-me-kusht-sipas-komunave-1.xlsx", poll, "conditional", False)
    outside_tidy(folder + "15.-Rezultatet-e-votave-me-poste-1.xlsx", poll, "postal", False)
    outside_tidy(folder + "16.-Statistikat-e-votave-me-poste-sipas-komunave-1.xlsx", poll, "postal", False)
    return poll


# The tidy workbooks of 2017: one row per municipality, polling center, or station and entity.


def norm(cell: object) -> str:
    return re.sub(r"[^a-z0-9]", "", ascii_fold(cell))


FIGURES = {
    "totalvalidvotes": VALID,
    "totalvalidballots": VALID,
    "numbervalidballotsreceived": VALID,
    "blankballots": BLANK,
    "blanballots": BLANK,
    "invalidballots": INVALID,
    "asinvalidballots": INVALID,
    "numberelectors": REGISTERED,
}


def tidy(name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for _, sheet in sheets(name):
        head = [norm(c) for c in sheet[0]]
        for row in sheet[1:]:
            cells = [text(c) for c in row]
            if any(cells):
                rows.append(dict(zip(head, cells, strict=False)))
    return rows


def tidy_municipality(row: dict[str, str]) -> str:
    for column in ("namemunicipality", "municipality", "municipalityname", "komunaopstina"):
        if row.get(column):
            return municipality_code(row[column])
    for column in ("idmunicipality", "shortnamemunicipality"):
        if row.get(column):
            return str(int(row[column]))
    raise ValueError(f"no municipality in {row}")


def tidy_unit(contest: Contest, kind: str, row: dict[str, str]) -> Unit | None:
    """The unit a tidy row belongs to: a polling center or station's center, or the municipality's conditional or
    postal ballots as one unit."""
    if kind in ("conditional", "postal"):
        return outside_unit(contest, kind)
    code = row.get("psnumber") or row.get("pc") or row.get("namepoolingcentar") or ""
    if not code:
        return None
    return contest.unit(code[:5] if re.match(r"\d{4}[A-Z]", code) and "/" in code and kind == "center" else code)


def tidy_votes(name: str, poll: Poll, mayor: bool, kind: str) -> None:
    """Votes by entity: `kind` is "municipal" for the municipal totals, "center" for polling centers or stations, and
    "conditional" or "postal" for those ballots. Rows of the same entity are added, so candidates' rows add up."""
    for row in tidy(name):
        contest = poll.contest(tidy_municipality(row))
        label = row.get("nameparty") or row.get("partyname") or ""
        if not label:
            continue
        candidate = None
        if mayor:
            candidate = f"{row.get('namemayor', '')} {row.get('surnamemayor', '')}".strip() or None
        votes = number(next(v for k, v in row.items() if k.startswith("votes")))
        key = contest.option(label, candidate)
        if kind == "municipal":
            contest.totals[key] = contest.totals.get(key, 0) + votes
        else:
            unit = tidy_unit(contest, kind, row)
            if unit is not None:
                unit.votes[key] = unit.votes.get(key, 0) + votes


def tidy_figures(name: str, poll: Poll, kind: str) -> None:
    """Ballot counts: by municipality, or by station added up to the center or to the conditional or postal unit."""
    for row in tidy(name):
        contest = poll.contest(tidy_municipality(row))
        target: Unit | None = None
        if kind != "municipal":
            target = tidy_unit(contest, kind, row)
            if target is None:
                continue
        for column, measure in FIGURES.items():
            if column in row and row[column] != "":
                if target is None:
                    contest.figures[measure] = number(row[column])
                else:
                    target.figures[measure] = target.figures.get(measure, 0) + number(row[column])


def assembly_xls(name: str, poll: Poll) -> None:
    """The workbook of the municipal results and seats: an entity's name, and on the next row its votes, its share,
    and its seats."""
    contest: Contest | None = None
    pending: str | None = None
    for _, rows in sheets(name):
        for row in rows:
            cells = [text(c) for c in row if text(c) != ""]
            if not cells:
                continue
            if pending and len(cells) == 3 and all(re.fullmatch(r"\d+(\.\d+)?", c) for c in cells):
                key = contest.option(pending) if contest else ""
                if contest:
                    contest.totals[key] = number(cells[0])
                    contest.seats[key] = number(cells[2])
                pending = None
            elif len(cells) == 1 and ENTITY.match(cells[0]):
                pending = cells[0]
            else:
                names = [c for c in cells if "/" in c and not any(ch.isdigit() for ch in c) and len(c) < 60]
                codes = [code for c in names if (code := try_code(c))]
                if codes:
                    contest = poll.contest(codes[0])
                    pending = None


def oct_2017_assembly() -> Poll:
    poll = Poll(date(2017, 10, 22), "assembly")
    assembly_xls("2018/04/2017-Komunal-1.xls", poll)
    tidy_figures("2018/03/1.Statistikat-sipas-komunave.xlsx", poll, "municipal")
    tidy_votes("2018/01/4-1.xlsx", poll, False, "center")
    tidy_figures("2018/01/2.Statistikat-sipas-vendvotimeve-1.xlsx", poll, "center")
    tidy_votes("2018/01/7.xlsx", poll, False, "conditional")
    tidy_figures("2018/01/5.-Statistikat-e-votave-me-kusht-sips-komunave.xlsx", poll, "conditional")
    tidy_votes("2018/01/8-1.xlsx", poll, False, "postal")
    return poll


def oct_2017_mayor() -> Poll:
    poll = Poll(date(2017, 10, 22), "mayor")
    tidy_votes("2018/01/1.xlsx", poll, True, "municipal")
    tidy_figures("2018/01/2.xlsx", poll, "municipal")
    tidy_votes("2018/01/2-1.xlsx", poll, True, "center")
    tidy_figures("2018/04/3.xlsx", poll, "center")
    tidy_votes("2018/01/4-4.xlsx", poll, True, "conditional")
    tidy_figures("2018/01/5.xlsx", poll, "conditional")
    tidy_votes("2018/01/5-1.xlsx", poll, True, "postal")
    tidy_figures("2018/01/7-1.xlsx", poll, "postal")
    # The municipal file prints the votes of the regular stations; the official total adds the other ballots.
    for contest in poll.contests.values():
        for code, unit in contest.units.items():
            if code.startswith(("CB", "BM")):
                for key, votes in unit.votes.items():
                    contest.totals[key] = contest.totals.get(key, 0) + votes
    return poll


def nov_2017_mayor() -> Poll:
    poll = Poll(date(2017, 11, 19), "mayor")
    folder = "2018/04/"
    results_by_municipality(folder + "1.-Results-Report-for-Mayor-By-Municipality.xls", True, poll)
    stats_by_municipality(folder + "1.-Statistic-Report-for-Mayor.xls", poll)
    tidy_votes(folder + "3.-Results-By-Polling-Station-for-Mayor.xlsx", poll, True, "station")
    stats_by_station(folder + "2.-Statistic-Report-for-Mayor-By-Polling-Station.xls", poll)
    tidy_votes(folder + "4.-CB-Party-Candidates-By-Municipality-Report-for-Mayor.xlsx", poll, True, "conditional")
    tidy_figures(folder + "4.-CB-Statistic-By-Municpality-Report-for-Mayor.xlsx", poll, "conditional")
    tidy_votes(folder + "5.-BM-Party-Candidates-By-Municipality-for-Report-Mayor.xlsx", poll, True, "postal")
    tidy_figures(folder + "6.-BM-Statistic-By-Municpality-Rreport-for-Mayor.xlsx", poll, "postal")
    return poll
