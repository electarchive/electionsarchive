import functools
import json
import re
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from typing import Any, cast
from uuid import uuid5

import httpx
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.dk import valgdatabasen

SOURCE = "dk.dst"
API = "https://api.statbank.dk/v1"
PAGE = "https://www.statbank.dk/"
DENMARK = str(uuid5(package.NAMESPACE, "polity\x1fDK"))
REGISTERED, CAST, VALID, INVALID, BLANK, VOTES, CANDIDATE_VOTES = 2, 3, 4, 5, 6, 1, 10


def storkreds(prefix: str, count: int = 10) -> list[str]:
    return [f"{prefix}S{n:02d}" for n in range(1, count + 1)]


# Each election: slug, date, kind, tables. Tables are Statbank table ids; a "total" table holds the printed national row.
FOLKETING = {
    date(2007, 11, 13): (storkreds("FV07"), "FV07TOT"),
    date(2011, 9, 15): (storkreds("FV11"), "FV11TOT"),
    date(2015, 6, 18): (storkreds("FV15"), "FV15TOT"),
    date(2019, 6, 5): (["FV19TOTA"], None),
    date(2022, 11, 1): (["FV22TOTA"], None),
    date(2026, 3, 24): (["FV26TOTA"], None),
}
EUROPEAN = {
    date(2009, 6, 7): (storkreds("EV09"), "EV09TOT"),
    date(2014, 5, 25): (storkreds("EV14"), "EV14TOT"),
    date(2019, 5, 26): (["EV19TOTA"], None),
    date(2024, 6, 9): (["EV24TOTA"], None),
}
REFERENDUM = {
    date(2009, 6, 7): (storkreds("FA09"), "FA09TOT"),
    date(2014, 5, 25): (["FA14TOT"], None),
    date(2015, 12, 3): (["FA15TOT"], None),
    date(2022, 6, 1): (["FA22TOTA"], None),
}
LOCAL_DAYS = {
    1989: date(1989, 11, 21),
    1993: date(1993, 11, 16),
    1997: date(1997, 11, 18),
    2001: date(2001, 11, 20),
    2005: date(2005, 11, 15),
    2009: date(2009, 11, 17),
    2013: date(2013, 11, 19),
    2017: date(2017, 11, 21),
    2021: date(2021, 11, 16),
    2025: date(2025, 11, 18),
}
DETAIL_LOCAL = ("KV25TOTA", "RV25TOTA")
SEATS = "FVKAND"
SEATS_BY_AGE = "LIGEDB1"
LOCAL_RESULTS = "KVRES"
LOCAL_PARTIES = ("VALGK3", "VALGK3X")
REGION_PARTIES = ("AKVA3", "AKVA3X")
PERSONAL = {y: f"KV{y % 100:02d}PERS" for y in (1997, 2001, 2005, 2009, 2013, 2017, 2021, 2025)}


def tables() -> list[str]:
    found: list[str] = []
    for group in (FOLKETING, EUROPEAN, REFERENDUM):
        for names, total in group.values():
            found += [*names, *([total] if total else [])]
    return [
        *found,
        SEATS,
        SEATS_BY_AGE,
        *DETAIL_LOCAL,
        LOCAL_RESULTS,
        *LOCAL_PARTIES,
        *REGION_PARTIES,
        *PERSONAL.values(),
    ]


def info_url(table: str) -> str:
    return f"{API}/tableinfo/{table}?lang=da&format=JSON"


def data_url() -> str:
    return f"{API}/data"


def body(table: str) -> dict[str, object]:
    selected = [{"code": v["id"], "values": ["*"]} for v in variables(table)]
    return {"table": table, "format": "BULK", "lang": "da", "valuePresentation": "CodeAndValue", "variables": selected}


def variables(table: str) -> list[dict[str, Any]]:
    meta = json.loads(raw.cached(SOURCE, info_url(table)).path.read_text())
    return cast(list[dict[str, Any]], meta["variables"])


def _retry(label: str, call: Callable[[], object]) -> None:
    for attempt in range(1, 7):
        try:
            call()
            return
        except httpx.TransportError as failure:
            print(f"{label}: attempt {attempt} failed: {failure!r}", flush=True)
            if attempt == 6:
                raise
            time.sleep(4 * attempt)


def fetch() -> None:
    valgdatabasen.fetch_local()
    for table in tables():
        _retry(table, lambda table=table: raw.fetch(SOURCE, info_url(table), suffix=".json"))
        _retry(table, lambda table=table: raw.fetch(SOURCE, data_url(), suffix=".csv", body=body(table)))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DST_SOURCE = key("source", "statbank")
FOLKETING_BODY = key("body", "folketing")
SYSTEM_FOLKETING = key("electoral_system", "folketing")
SYSTEM_LIST = key("electoral_system", "list")
SYSTEM_REFERENDUM = key("electoral_system", "referendum")
LETTER = re.compile(r"^([A-ZÆØÅ]{1,2})\. +(.+)$")
ALIASES = {
    "det radikale venstre": "radikale venstre",
    "det konservative folkeparti": "konservative folkeparti",
    "konservative folkepartie": "konservative folkeparti",
    "sf - socialistisk folkeparti": "socialistisk folkeparti",
    "venstre, danmarks liberale parti": "venstre",
    "dansk folkeparti (1997-)": "dansk folkeparti",
    "enhedslisten - de rød-grønne": "enhedslisten",
}
OTHERS_TEXT = "Lists without a reserved letter"


def clean(text: str) -> str:
    return " ".join(text.split())


def split(cell: str) -> tuple[str, str]:
    code, _, text = cell.strip().partition(" ")
    return code, clean(text)


def party_name(text: str) -> tuple[str, str]:
    """The party letter (or empty) and the name that identifies the party across elections."""
    found = LETTER.match(clean(text))
    letter, name = (found.group(1), found.group(2)) if found else ("", clean(text))
    key_name = name.casefold()
    return letter, ALIASES.get(key_name, key_name)


class Area:
    def __init__(self, code: str, name: str, level: str, path: tuple[str, ...], codes: tuple[str, ...] = ()) -> None:
        self.code = code
        self.name = name
        self.level = level
        self.path = path
        self.codes = codes  # the codes of the areas in path


def level_of(code: str, local: bool) -> str:
    if code in ("0", "TOT", "000", "DK"):
        return "nation"
    if code == "RIG":
        return "realm"
    number = int(code)
    if number >= 1000:
        return "area"
    if local:
        return "region" if number < 100 else "kommune"
    return "region" if number < 10 else "storkreds" if number < 20 else "opstillingskreds"


class Table:
    """One Statbank table: its ordered areas, ordered result rows, and the non-empty cells."""

    def __init__(self, name: str, local: bool = False) -> None:
        self.name = name
        found = {str(v["id"]): v for v in variables(name)}
        self.area_variable = "OMRÅDE"
        self.row_variable = next(v for v in found if v not in ("OMRÅDE", "Tid"))
        self.areas: list[Area] = []
        trail: dict[str, str] = {}
        ids: dict[str, str] = {}
        for value in found["OMRÅDE"]["values"]:
            code, text = str(value["id"]), clean(str(value["text"]))
            level = level_of(code, local)
            if level in ("nation", "realm"):
                self.areas.append(Area(code, text, level, ()))
                continue
            order = ["region", "storkreds", "opstillingskreds", "kommune", "area"]
            trail[level] = text
            ids[level] = code
            for deeper in order[order.index(level) + 1 :]:
                trail.pop(deeper, None)
                ids.pop(deeper, None)
            above = [step for step in order[: order.index(level)] if step in trail]
            self.areas.append(Area(code, text, level, tuple(trail[s] for s in above), tuple(ids[s] for s in above)))
        self.rows = [(str(v["id"]), clean(str(v["text"]))) for v in found[self.row_variable]["values"]]
        frame = pl.read_csv(raw.cached(SOURCE, data_url(), body(name)).path, separator=";", infer_schema_length=0)
        self.cells: dict[tuple[str, str], int] = {}
        for row, area, value in zip(
            frame[frame.columns[0]], frame[frame.columns[1]], frame[frame.columns[-1]], strict=True
        ):
            if value is not None and value.strip() not in ("", ".."):
                self.cells[(split(row)[0], split(area)[0])] = int(value)

    def finest(self) -> str:
        levels = {a.level for a in self.areas}
        for level in ("area", "opstillingskreds", "kommune"):
            if level in levels:
                return level
        raise LookupError(self.name)


def roles(rows: list[tuple[str, str]]) -> dict[str, str]:
    """Classify a table's result rows by their place in the table's own order."""
    found: dict[str, str] = {}
    stage = "start"
    fixed = {
        "GYLD_IALT": "valid",
        "UDENFOR_IALT": "others",
        "ANDRE": "others",
        "PERS_IALT": "personal",
        "BREV_IALT": "postal",
        "UGYLD_IALT": "invalid",
        "VAELG": "registered",
    }
    for code, _ in rows:
        if code in fixed:
            found[code] = fixed[code]
            stage = {
                "GYLD_IALT": "options",
                "UDENFOR_IALT": "after",
                "ANDRE": "after",
                "PERS_IALT": "candidates",
                "BREV_IALT": "rest",
                "UGYLD_IALT": "rest",
            }.get(code, stage)
        elif stage == "options":
            found[code] = "option"
        elif stage == "candidates":
            found[code] = "candidate"
        else:
            found[code] = "detail"
    return found


class Option:
    def __init__(
        self, code: str, label: str, kind: str, party: str | None = None, parent: str | None = None, ballot: str = ""
    ) -> None:
        self.code = code
        self.label = label
        self.kind = kind  # list, others, answer, or candidate
        self.party = party  # the party's cross-election key
        self.parent = parent  # a candidate's list option code
        self.ballot = ballot


class Unit:
    def __init__(self, code: str, name: str, scheme: str) -> None:
        self.code = code
        self.name = name
        self.scheme = scheme
        self.accounting: dict[int, int] = {}
        self.values: dict[tuple[str, int], int] = {}


class Contest:
    def __init__(self, code: str, english: str, danish: str, kind: str, system: str) -> None:
        self.code = code
        self.english = english
        self.danish = danish
        self.kind = kind  # list_allocation or ballot_question
        self.system = system
        self.body: str | None = None
        self.options: dict[str, Option] = {}
        self.units: list[Unit] = []
        self.seats: dict[str, int] = {}
        self.declared: dict[tuple[str | None, int], int] = {}
        self.candidate_votes: dict[str, int] = {}  # a candidate option to its personal votes in the council
        self.tables: list[str] = []
        self.notes: list[str] = []
        self.body_names: tuple[str, str] = ("", "")
        self.place = ""  # the council's area name
        self.districts: list[Unit] = []  # the polling districts from the election database, when it holds them
        self.extract: int | None = None  # the election database's id of the election the districts come from
        self.district_notes: list[str] = []


class Election:
    def __init__(self, slug: str, day: date, kind: str, english: str, danish: str, path: str) -> None:
        self.slug = slug
        self.day = day
        self.kind = kind
        self.english = english
        self.danish = danish
        self.path = path
        self.contests: list[Contest] = []


def scheme_of(level: str) -> str:
    return {
        "area": "dk.afstemningsomraade",
        "opstillingskreds": "dk.opstillingskreds",
        "kommune": "dk.kommune",
        "region": "dk.region",
        "amt": "dk.amt",
    }[level]


def declare(contest: Contest, table: Table, candidates: bool) -> None:
    """Record the printed national row of a table as the contest's declared totals."""
    kinds = roles(table.rows)
    nation = next(a for a in table.areas if a.level == "nation")
    for code, role in kinds.items():
        value = table.cells.get((code, nation.code))
        if value is None:
            continue
        if role == "valid":
            contest.declared[(None, VALID)] = value
        elif role == "registered":
            contest.declared[(None, REGISTERED)] = value
        elif role == "invalid":
            contest.declared[(None, INVALID)] = value
        elif role in ("option", "others") and code in contest.options:
            contest.declared[(code, VOTES)] = value
        elif role == "candidate" and candidates and code in contest.options:
            contest.declared[(code, CANDIDATE_VOTES)] = value


def list_contest(
    election: Election, code: str, english: str, danish: str, names: list[str], total: str | None, local: bool = False
) -> Contest:
    """A national contest read from the per-constituency tables of one election, finest unit each."""
    answers = election.kind == "referendum"
    contest = Contest(
        code,
        english,
        danish,
        "ballot_question" if answers else "list_allocation",
        SYSTEM_REFERENDUM if answers else SYSTEM_LIST,
    )
    contest.tables = [*names, *([total] if total else [])]
    positions = 0
    candidate_parent: dict[str, str] = {}
    for name in names:
        table = Table(name, local)
        kinds = roles(table.rows)
        for code_, text in table.rows:
            role = kinds[code_]
            if role == "option" and code_ not in contest.options:
                positions += 1
                if answers:
                    contest.options[code_] = Option(code_, text, "answer", ballot=str(positions))
                else:
                    letter, party = party_name(text)
                    contest.options[code_] = Option(code_, text, "list", party=party, ballot=letter)
            elif role == "others" and code_ not in contest.options:
                contest.options[code_] = Option(code_, text, "others")
            elif role == "candidate" and code_ not in contest.options:
                found = LETTER.match(text)
                parent = None
                if found:
                    letter = found.group(1)
                    parent = next(
                        (c for c, o in contest.options.items() if o.kind == "list" and o.ballot == letter), None
                    )
                    text = found.group(2)
                if parent is None:
                    parent = next(c for c, o in contest.options.items() if o.kind == "others")
                contest.options[code_] = Option(code_, text, "candidate", parent=parent)
                candidate_parent[code_] = parent
        level = table.finest()
        for area in table.areas:
            if area.level != level:
                continue
            unit = Unit(
                area.code,
                f"{area.name} ({', '.join(reversed(area.path[-2:]))})" if area.path else area.name,
                scheme_of(level),
            )
            valid = table.cells.get(("GYLD_IALT", area.code))
            for code_, role in kinds.items():
                value = table.cells.get((code_, area.code))
                if role == "registered" and value is not None:
                    unit.accounting[REGISTERED] = value
                elif role == "invalid" and value is not None:
                    unit.accounting[INVALID] = value
                elif role in ("option", "others") and code_ in contest.options:
                    unit.values[(code_, VOTES)] = value or 0
                elif role == "candidate" and value:
                    unit.values[(code_, CANDIDATE_VOTES)] = value
            if valid is not None:
                unit.accounting[VALID] = valid
                unit.accounting[CAST] = valid + unit.accounting.get(INVALID, 0)
            contest.units.append(unit)
    declare(contest, Table(total or names[0], local), candidates=total is None)
    election.contests.append(contest)
    return contest


def attach_seats(contest: Contest, year: int) -> None:
    """The seats of each party: the candidates elected for it, men and women, in Statistics Denmark's candidate table.

    The table counts the elected per party and sex (and, in a second table, per sex and age) but names nobody, so
    the seats are stored per party and no candidate is marked elected. A party is the same in the ballot tables and
    this one when its Statbank code agrees (Ny Alliance of 2007 is the table's Liberal Alliance); a code the ballot
    tables lack is matched by letter and name. Both tables must add up to 175."""
    names = labels(SEATS, "PARTI")
    by_party: dict[str, int] = defaultdict(int)
    for cell in sheet(SEATS):
        if cell["Tid"] == str(year) and cell["KANDIDAT"] in ("MVALGT", "KVALGT"):
            by_party[cell["PARTI"]] += int(cell["value"])
    lists = {(o.ballot, o.party): code for code, o in contest.options.items() if o.kind == "list"}
    others = next(code for code, o in contest.options.items() if o.kind == "others")
    for code, count in by_party.items():
        if not count:
            continue
        found = (
            code
            if contest.options.get(code) and contest.options[code].kind == "list"
            else lists.get(party_name(names[code]))
        )
        if found is None and code == "9999":
            found = others
        if found is None:
            raise LookupError(f"{year}: {names[code]} holds {count} seats but is not on the ballot tables")
        contest.seats[found] = contest.seats.get(found, 0) + count
    elected = sum(
        int(cell["value"])
        for cell in sheet(SEATS_BY_AGE)
        if cell["Tid"] == str(year) and cell["KANDIDAT"] == "VK" and cell["KØN"] == "TOT" and cell["ALDER"] == "TOT"
    )
    total = sum(contest.seats.values())
    if not total == elected == 175:
        raise ValueError(f"{year}: {total} seats by party, {elected} elected by age, expected 175")


def sheet(name: str) -> list[dict[str, str]]:
    """Every non-empty cell of a table, with each variable's code and a `value` entry."""
    ids = [str(v["id"]) for v in variables(name)]
    frame = pl.read_csv(raw.cached(SOURCE, data_url(), body(name)).path, separator=";", infer_schema_length=0)
    found: list[dict[str, str]] = []
    for row in frame.iter_rows():
        if row[-1] is None or row[-1].strip() in ("", ".."):
            continue
        found.append({**{i: split(cell)[0] for i, cell in zip(ids, row[:-1], strict=True)}, "value": row[-1].strip()})
    return found


def labels(name: str, variable: str) -> dict[str, str]:
    for v in variables(name):
        if v["id"] == variable:
            return {str(x["id"]): clean(str(x["text"])) for x in v["values"]}
    raise LookupError(variable)


def aggregate(code: str, name: str) -> bool:
    return code in ("000", "100", "400") or int(code) < 100 or "amt" in name.casefold().split()


def council(election: Election, kind: str, code: str, name: str, year: int) -> Contest:
    english = {"kommune": "municipal council", "region": "regional council", "amt": "county council"}[kind]
    danish = {"kommune": "Kommunalbestyrelsen", "region": "Regionsrådet", "amt": "Amtsrådet"}[kind]
    contest = Contest(
        f"{kind}-{code}",
        f"{year} {name} {english} election",
        f"Valg til {danish.lower() if kind != 'kommune' else 'kommunalbestyrelsen'} i {name} {year}",
        "list_allocation",
        SYSTEM_LIST,
    )
    contest.body = key("body", kind, code)
    contest.place = name
    contest.body_names = (f"{name} {english.title()}", f"{name} {danish}")
    election.contests.append(contest)
    return contest


def party_option(contest: Contest, text: str) -> str:
    letter, party = party_name(text)
    if party not in contest.options:
        contest.options[party] = Option(party, text, "list", party=party, ballot=letter)
    return party


def others_option(contest: Contest, code: str) -> str:
    contest.options.setdefault(code, Option(code, OTHERS_TEXT, "others"))
    return code


def party_sheet(name: str, year: int, area_names: dict[str, str]) -> tuple[dict, dict, dict]:
    """Votes, seats, and party texts by area from a party-by-area table such as VALGK3."""
    texts = labels(name, "PARTI")
    votes: dict[str, dict[str, int]] = defaultdict(dict)
    seats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in sheet(name):
        if row["Tid"] != str(year):
            continue
        area, party = row["OMRÅDE"], row["PARTI"]
        if row["STEMMER"] == "1":
            votes[area][party] = int(row["value"])
        elif row["STEMMER"] in ("6", "7"):
            seats[area][party] += int(row["value"])
    return votes, seats, texts


def attach_party_figures(
    contest: Contest, unit: Unit, votes: dict[str, int], seats: dict[str, int], texts: dict[str, str]
) -> None:
    for party, count in votes.items():
        if not count:
            continue
        code = others_option(contest, "others") if party == "EJR" else party_option(contest, texts[party])
        unit.values[(code, VOTES)] = count
    for party, count in seats.items():
        if count:
            code = others_option(contest, "others") if party == "EJR" else party_option(contest, texts[party])
            contest.seats[code] = count


def aggregate_councils(election: Election, year: int, kind: str, table: str) -> None:
    names = labels(table, "OMRÅDE")
    votes, seats, texts = party_sheet(table, year, names)
    for area in sorted(votes):
        name = names[area].strip()
        if kind == "kommune" and aggregate(area, name):
            continue
        if kind == "region" and area == "000":
            continue
        if not any(votes[area].values()):
            continue
        contest = council(election, kind, area, name, year)
        contest.tables = [table]
        unit = Unit(area, name, scheme_of(kind))
        attach_party_figures(contest, unit, votes[area], seats.get(area, {}), texts)
        for code in contest.options:
            unit.values.setdefault((code, VOTES), 0)
        contest.units.append(unit)


def kommune_results(election: Election, year: int, names: dict[str, str]) -> None:
    """Municipal results 2005-2025 at kommune level: accounting from KVRES, seats from VALGK3."""
    votes, seats, texts = party_sheet("VALGK3", year, names)
    rows = labels(LOCAL_RESULTS, "VALRES")
    cells: dict[str, dict[str, int]] = defaultdict(dict)
    for row in sheet(LOCAL_RESULTS):
        if row["Tid"] == str(year):
            cells[row["OMRÅDE"]][row["VALRES"]] = int(row["value"])
    for area in sorted(cells):
        # 400 is Bornholm's own municipality from 2003 on, not the sum of the island's municipalities
        if area != "400" and aggregate(area, names.get(area, "")):
            continue
        name = names[area].strip()
        contest = council(election, "kommune", area, name, year)
        contest.tables = [LOCAL_RESULTS, "VALGK3"]
        unit = Unit(area, name, "dk.kommune")
        figures = cells[area]
        for code, measure in (("V", REGISTERED), ("AS", CAST), ("GS", VALID), ("BS", BLANK), ("AGS", INVALID)):
            if code in figures:
                unit.accounting[measure] = figures[code]
        for code, count in figures.items():
            if code.startswith("GS") and code != "GS" and count:
                letter = code[2:]
                option = others_option(contest, "others") if letter == "EJR" else party_option(contest, rows[code])
                unit.values[(option, VOTES)] = count
        for party, count in seats.get(area, {}).items():
            if count:
                option = others_option(contest, "others") if party == "EJR" else party_option(contest, texts[party])
                contest.seats[option] = count
        for code in contest.options:
            unit.values.setdefault((code, VOTES), 0)
        contest.units.append(unit)
        contest.notes.append(
            "Counted figures per municipality; the per-polling-district counts of this election are not in the "
            "published tables this source reads."
        )
        # the party-by-area table repeats the party votes; they must agree with the results table
        for party, count in votes.get(area, {}).items():
            mine = unit.values.get((party if party != "EJR" else "others", VOTES))
            if mine is not None and mine != count:
                raise ValueError(f"{year} {area} {party}: {mine} against {count}")


def detail_councils(
    election: Election, year: int, table_name: str, kind: str, names: dict[str, str], seats_table: str
) -> None:
    """The 2025 municipal or regional results per polling district, grouped by council."""
    table = Table(table_name, local=True)
    kinds = roles(table.rows)
    _, seats, texts = party_sheet(seats_table, year, names)
    by_name = {clean(n).casefold(): c for c, n in names.items()}
    councils: dict[str, Contest] = {}
    for area in table.areas:
        if area.level != "area":
            continue
        if kind == "kommune":
            code, label = area.codes[-1], area.path[-1]
        else:
            label = area.path[0]
            code = by_name[label.casefold()]
        if code not in councils:
            councils[code] = council(election, kind, code, names[code].strip(), year)
            councils[code].tables = [table_name, seats_table]
        contest = councils[code]
        unit = Unit(area.code, f"{area.name} ({label})", "dk.afstemningsomraade")
        for row_code, role in kinds.items():
            value = table.cells.get((row_code, area.code))
            text = dict(table.rows)[row_code]
            if role == "registered" and value is not None:
                unit.accounting[REGISTERED] = value
            elif role == "invalid" and value is not None:
                unit.accounting[INVALID] = value
            elif role == "option" and value:
                unit.values[(party_option(contest, text), VOTES)] = value
            elif role == "others" and value:
                unit.values[(others_option(contest, row_code), VOTES)] = value
        valid = table.cells.get(("GYLD_IALT", area.code))
        if valid is not None:
            unit.accounting[VALID] = valid
            unit.accounting[CAST] = valid + unit.accounting.get(INVALID, 0)
        contest.units.append(unit)
    for code, contest in councils.items():
        for party, count in seats.get(code, {}).items():
            if count:
                option = others_option(contest, "others") if party == "EJR" else party_option(contest, texts[party])
                contest.seats[option] = count
        for unit in contest.units:
            for option in contest.options:
                unit.values.setdefault((option, VOTES), 0)


# The most votes by which a council's polling districts may differ from its own count, in any figure, before the
# districts are taken as incomplete and the council keeps its per-council set; councils above 20,000 valid votes
# allow one in a thousand.
DISTRICT_TOLERANCE = 20
SHORT_COUNCILS: list[str] = []


def gaps_of(contest: Contest, units: list[Unit]) -> dict[tuple[str | None, int], int]:
    """How far the districts' sums fall from the council's own count, for each figure the council holds."""
    (held,) = contest.units
    sums: dict[tuple[str | None, int], int] = defaultdict(int)
    for unit in units:
        for (option, measure), value in unit.values.items():
            sums[(option, measure)] += value
        for measure, value in unit.accounting.items():
            sums[(None, measure)] += value
    figures = [*held.values.items(), *(((None, m), v) for m, v in held.accounting.items())]
    return {k: sums.get(k, 0) - v for k, v in figures if sums.get(k, 0) != v}


def letters(contest: Contest) -> dict[str, str]:
    """The ballot letter of each national party's list in a municipal contest, to the list's option code."""
    return {o.ballot: code for code, o in contest.options.items() if o.kind == "list" and o.ballot}


def attach_districts(municipal: Election, regional: Election, day: date) -> None:
    """Give each municipal and regional council the election database's polling districts, when they add up to
    the council's own count."""
    national: dict[str, str] = {}
    for contest in municipal.contests:
        for letter, code in letters(contest).items():
            if national.setdefault(letter, code) != code:
                raise ValueError(f"{municipal.slug}: letter {letter} stands for {national[letter]} and {code}")
    kv, rv = valgdatabasen.LOCAL[day]
    for election, valg, kind in ((municipal, kv, "KV"), (regional, rv, "RV")):
        groups: dict[str, list[valgdatabasen.LocalDistrict]] = defaultdict(list)
        for district in valgdatabasen.local_districts(valg, day, kind):
            council = district.municipality if kind == "KV" else valgdatabasen.REGION_OF[district.municipality]
            groups[f"{'kommune' if kind == 'KV' else 'region'}-{int(council):03d}"].append(district)
        for contest in election.contests:
            found = groups.pop(contest.code, [])
            if not found:
                raise ValueError(f"{election.slug} {contest.code}: the election database holds no polling districts")
            own = letters(contest) if kind == "KV" else {k: v for k, v in national.items() if v in contest.options}
            units: list[Unit] = []
            for district in found:
                unit = Unit(district.code, f"{district.name} ({district.municipality_name})", "dk.afstemningsomraade")
                unit.accounting = dict(district.accounting)
                for letter, count in district.votes.items():
                    option = own.get(letter) or others_option(contest, "others")
                    unit.values[(option, VOTES)] = unit.values.get((option, VOTES), 0) + count
                for option in contest.options:
                    unit.values.setdefault((option, VOTES), 0)
                units.append(unit)
            gaps = gaps_of(contest, units)
            (held,) = contest.units
            tolerance = max(DISTRICT_TOLERANCE, held.accounting.get(VALID, 0) // 1000)
            if any(abs(gap) > tolerance for gap in gaps.values()):
                SHORT_COUNCILS.append(f"{election.slug} {contest.code}")
                continue
            contest.districts = units
            contest.extract = valg
            if gaps:
                valid = sum(u.accounting.get(VALID, 0) for u in units)
                contest.district_notes.append(
                    f"The polling districts of the election database add up to {valid:,} valid votes, and differ "
                    f"from the per-council count this set supersedes by at most {max(map(abs, gaps.values())):,} "
                    "votes in any figure; the per-council figures are kept as this set's declared totals."
                )
        if groups:
            raise ValueError(f"{election.slug}: polling districts outside every council: {sorted(groups)}")


REFERENDA = {
    date(2009, 6, 7): ("Referendum on the Act of Succession to the Throne", "Folkeafstemning om Tronfølgeloven"),
    date(2014, 5, 25): ("Referendum on the Unified Patent Court", "Folkeafstemning om fælles patentdomstol"),
    date(2015, 12, 3): (
        "Referendum on converting the justice opt-out into an opt-in arrangement",
        "Folkeafstemning om omdannelse af retsforbeholdet til en tilvalgsordning",
    ),
    date(2022, 6, 1): (
        "Referendum on abolishing the EU defence opt-out",
        "Folkeafstemning om afskaffelse af EU-forsvarsforbeholdet",
    ),
}


@functools.cache
def folketing_elections() -> list[Election]:
    found: list[Election] = []
    for day, (names, total) in FOLKETING.items():
        election = Election(
            f"fv{day.year}",
            day,
            "general",
            f"{day.year} Danish general election",
            f"Folketingsvalget {day.year}",
            f"dk/{day}-general",
        )
        contest = list_contest(
            election, "folketing", f"{day.year} Folketing election", f"Folketingsvalget {day.year}", names, total
        )
        contest.body = FOLKETING_BODY
        contest.system = SYSTEM_FOLKETING
        contest.notes.append(
            "The 175 seats of the Folketing in Denmark proper are allocated by constituency and levelling seats. "
            "The seats per party are the elected candidates Statistics Denmark counts per party; the four seats of "
            "Greenland and the Faroe Islands are not part of these tables."
        )
        attach_seats(contest, day.year)
        found.append(election)
    return found


CANDIDATE = re.compile(r"^(.+) \(([^()]+)\) \(([^()]+)\)$")


# The ballot letters of the national lists before 2009, when the party tables do not give them, to the list's code.
OLD_LETTERS = {
    "A": "socialdemokratiet",
    "B": "radikale venstre",
    "C": "konservative folkeparti",
    "D": "centrum-demokraterne",
    "F": "socialistisk folkeparti",
    "O": "dansk folkeparti",
    "Q": "kristeligt folkeparti",
    "S": "slesvigsk parti",
    "V": "venstre",
    "Z": "fremskridtspartiet",
    "Ø": "enhedslisten",
}


def resolve_lists(contest: Contest, tokens: list[str], year: int) -> dict[str, str]:
    """The list each ballot token of a council's candidates stands for.

    A national list is known by its letter. The source sometimes writes a list's letter with a number (V1), so a
    numbered token joins the letter's list when together they hold exactly that list's seats; any other token is a
    list without a reserved letter, which the source counts among the others."""
    mapping = dict(letters(contest))
    if year < 2009:
        mapping |= {k: v for k, v in OLD_LETTERS.items() if v in contest.options}
    unreserved = [c for c, o in contest.options.items() if o.kind == "others"]
    other = next((c for c in unreserved if c in contest.seats), None) or next(iter(unreserved), None)
    other = other or others_option(contest, "others")
    sizes: dict[str, int] = defaultdict(int)
    for token in tokens:
        sizes[token] += 1
    found = {token: mapping.get(token, other) for token in sizes}
    for letter, code in mapping.items():
        group = [t for t in sizes if t != letter and t.rstrip("0123456789") == letter]
        if group and sum(sizes[t] for t in [letter, *group] if t in sizes) == contest.seats.get(code, 0):
            for token in group:
                found[token] = code
    return found


# The candidate tables' spellings of municipalities the area tables name differently.
SPELLINGS = {
    "københavns": "københavn",
    "neksø": "nexø",
    "nykøbing f.": "nykøbing-falster",
    "thyborøn-harboør": "thyborøn-harboøre",
}


def council_key(text: str) -> str:
    """A municipality's name without its suffix, for matching the names two tables spell differently."""
    name = re.sub(r"\s*\([^()]*\)$", "", clean(text).casefold())
    name = re.sub(r"\s+(kommune|amt)$", "", name)
    name = SPELLINGS.get(name, name)
    return name.replace("aa", "å").replace("å", "aa")


def attach_candidates(election: Election, year: int) -> None:
    """Personal votes of the elected municipal candidates, per municipality, as candidates under their lists."""
    table = PERSONAL[year]
    codes: dict[str, str] = {}
    for c in election.contests:
        name = c.place
        if codes.setdefault(council_key(name), c.code.removeprefix("kommune-")) != c.code.removeprefix("kommune-"):
            raise ValueError(f"{election.slug}: two councils named {name}")
    texts = {str(x["id"]): str(x["text"]) for x in next(v for v in variables(table) if v["id"] == "KANDIDAT")["values"]}
    by_council = {c.code: c for c in election.contests}
    found = 0
    missing: set[str] = set()
    held: dict[str, list[tuple[str, str, str, int]]] = defaultdict(list)
    for row in sheet(table):
        if row["STEMMETYPE"] != "PERS" or row["Tid"] != str(year):
            continue
        match = CANDIDATE.match(clean(texts[row["KANDIDAT"]]))
        if match is None:
            raise ValueError(f"{table}: candidate {texts[row['KANDIDAT']]!r}")
        name, letter, council_name = (clean(part) for part in match.groups())
        code = codes.get(council_key(council_name))
        contest = by_council.get(f"kommune-{code}") if code else None
        if contest is None:
            missing.add(council_name)
            continue
        held[contest.code].append((f"candidate-{row['KANDIDAT']}", name, letter, int(row["value"])))
        found += 1
    if missing:
        raise ValueError(f"{table}: no council for {sorted(missing)}")
    for code, rows in held.items():
        contest = by_council[code]
        lists = resolve_lists(contest, [letter for _, _, letter, _ in rows], year)
        for option, name, letter, votes in rows:
            contest.options[option] = Option(option, name, "candidate", parent=lists[letter])
            contest.candidate_votes[option] = votes
    for contest in election.contests:
        counted: dict[str, int] = defaultdict(int)
        for option in contest.candidate_votes:
            counted[contest.options[option].parent or ""] += 1
        if counted != {k: v for k, v in contest.seats.items() if v}:
            contest.notes.append(
                "The party letters the source prints with the elected candidates do not add up to the council's "
                "seats per list; the candidates are stored under the lists their letters name."
            )
            print(f"{table} {contest.code}: letters differ from the seats", flush=True)
    for contest in election.contests:
        if contest.candidate_votes:
            contest.tables.append(table)
            contest.notes.append(
                "The candidates are the elected members and deputies only: the source publishes the personal votes "
                "of the elected candidates, per council, and not those of candidates who were not elected. The "
                "candidate votes are given for the whole council."
            )
    print(f"{election.slug}: {found} candidates", flush=True)


@functools.cache
def elections() -> list[Election]:
    found: list[Election] = list(folketing_elections())
    for day, (names, total) in EUROPEAN.items():
        election = Election(
            f"ep{day.year}",
            day,
            "general",
            f"{day.year} European Parliament election in Denmark",
            f"Europa-Parlamentsvalget {day.year}",
            f"dk/{day}-european",
        )
        list_contest(
            election,
            "parliament",
            f"{day.year} European Parliament election in Denmark",
            f"Europa-Parlamentsvalget {day.year}",
            names,
            total,
        )
        found.append(election)
    for day, (names, total) in REFERENDUM.items():
        english, danish = REFERENDA[day]
        election = Election(f"fa{day.year}", day, "referendum", english, danish, f"dk/{day}-referendum")
        list_contest(election, "question", english, danish, names, total)
        found.append(election)
    kommuner = labels("VALGK3", "OMRÅDE")
    regions = labels("AKVA3", "OMRÅDE")
    for year, day in LOCAL_DAYS.items():
        municipal = Election(
            f"kv{year}",
            day,
            "local",
            f"{year} Danish municipal elections",
            f"Kommunalvalget {year}",
            f"dk/{day}-municipal",
        )
        if year == 2025:
            detail_councils(municipal, year, "KV25TOTA", "kommune", kommuner, "VALGK3")
        elif year >= 2005:
            kommune_results(municipal, year, kommuner)
        else:
            aggregate_councils(municipal, year, "kommune", "VALGK3X")
        if year in PERSONAL:
            attach_candidates(municipal, year)
        found.append(municipal)
        if year >= 2005:
            regional = Election(
                f"rv{year}",
                day,
                "local",
                f"{year} Danish regional council elections",
                f"Regionsrådsvalget {year}",
                f"dk/{day}-regional",
            )
            if year == 2025:
                detail_councils(regional, year, "RV25TOTA", "region", regions, "AKVA3")
            else:
                aggregate_councils(regional, year, "region", "AKVA3")
            found.append(regional)
            if day in valgdatabasen.LOCAL:
                attach_districts(municipal, regional, day)
        else:
            county = Election(
                f"av{year}",
                day,
                "local",
                f"{year} Danish county council elections",
                f"Amtsrådsvalget {year}",
                f"dk/{day}-county",
            )
            aggregate_councils(county, year, "amt", "AKVA3X")
            found.append(county)
    return found


METHOD = (
    "Counted per polling district (afstemningsområde) and published by Statistics Denmark in its Statbank election "
    "tables, which hold the results the election authority declares. Registered voters are the voters entitled to "
    "vote, valid votes are the valid votes, invalid votes are the invalid votes (blank and otherwise), and ballots "
    "cast are valid plus invalid votes. A party's votes include the personal votes for its candidates; personal votes "
    "are stored per candidate. The source omits zero cells, so a figure it does not print is stored as zero for a "
    "party and left out for a candidate."
)
AGGREGATE_METHOD = (
    "Published by Statistics Denmark in its Statbank election tables, which hold the results the election authority "
    "declares, as one figure per party for the whole council area. The election authority's finer per-polling-district "
    "counts of this election are not in these tables."
)
SCHEMES = (
    ("dk.region", "administrative", 1, "region"),
    ("dk.amt", "administrative", 1, "county"),
    ("dk.kommune", "administrative", 2, "municipality"),
    ("dk.opstillingskreds", "electoral_district", 3, "nomination district"),
    ("dk.afstemningsomraade", "precinct", 4, "polling district"),
)


def packages() -> list[str]:
    days = sorted({e.day for e in elections()})
    return ["dk.dst/registry", *(f"dk.dst/{day}" for day in days)]


def parse() -> None:
    registry().write()
    for day in sorted({e.day for e in elections()}):
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def display(label: str) -> str:
    found = LETTER.match(clean(label))
    return found.group(2) if found else clean(label)


def registry() -> Package:
    p = Package(
        "dk.dst/registry",
        SOURCE,
        "Danish parties, the Folketing, council bodies, and electoral systems, from Statistics Denmark's election tables",
    )
    p.external.add(DENMARK)
    p.add(
        "source",
        id=DST_SOURCE,
        kind="official_body",
        publisher="Danmarks Statistik",
        title="Election results in the Statbank",
        url=PAGE,
        license="Statistics Denmark data may be reused with attribution to Statistics Denmark as the source",
        attribution="Danmarks Statistik",
    )
    for table in tables():
        p.artifact(raw.cached(SOURCE, info_url(table)), DST_SOURCE)
        p.artifact(raw.cached(SOURCE, data_url(), body(table)), DST_SOURCE)
    for code, kind, rank, noun in SCHEMES:
        p.add("scheme", code=code, polity_id=DENMARK, kind=kind, rank=rank, noun=noun)
    p.add("body", id=FOLKETING_BODY, polity_id=DENMARK, kind="unicameral")
    p.name(FOLKETING_BODY, "Folketing", "en", "official")
    p.name(FOLKETING_BODY, "Folketinget", "da", "native", False)
    systems = (
        (
            SYSTEM_FOLKETING,
            {"formula": "dhondt", "threshold": {"percent": 2, "level": "party", "scope": "national"}},
            (
                "Party-list proportional representation in ten multi-member constituencies with national levelling seats. "
                "A party takes part in the levelling seats if it wins a constituency seat, reaches 2 percent of the valid "
                "votes nationally, or meets a ratio test in two of the three regions; only the 2 percent test is "
                "recorded here."
            ),
        ),
        (SYSTEM_LIST, {"formula": "dhondt"}, "Party-list proportional representation."),
        (SYSTEM_REFERENDUM, {"formula": "referendum"}, "A yes or no question put to all voters."),
    )
    for id, rules, text in systems:
        p.add("electoral_system", id=id, polity_id=DENMARK, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    bodies: dict[str, tuple[str, str]] = {}
    names: dict[str, str] = {}
    for election in elections():
        for contest in election.contests:
            if contest.body and contest.body != FOLKETING_BODY:
                bodies[contest.body] = contest.body_names
            for option in contest.options.values():
                if option.kind == "list" and option.party:
                    names[option.party] = display(option.label)
    for id, (english, danish) in sorted(bodies.items()):
        p.add("body", id=id, polity_id=DENMARK, kind="council")
        p.name(id, english, "en", "official")
        p.name(id, danish, "da", "native", False)
    for party, text in sorted(names.items()):
        id = key("party", party)
        p.add("party", id=id, kind="party", polity_id=DENMARK)
        p.name(id, text, "da", "official")
    return p


def build(day: date) -> Package:
    found = [e for e in (folketing_elections() if day in FOLKETING else elections()) if e.day == day]
    finer = day in valgdatabasen.LOCAL
    p = Package(
        f"dk.dst/{day}",
        SOURCE,
        f"The Danish elections of {day} at the finest unit Statistics Denmark publishes",
        depends=["dk.dst/registry", *([valgdatabasen.REGISTRY] if finer else [])],
    )
    p.external.add(DENMARK)
    if finer:
        p.external.add(valgdatabasen.SCHEME)
        for valg in valgdatabasen.LOCAL[day]:
            p.artifact(valgdatabasen.local_artifact(valg), valgdatabasen.SOURCE_ID)
    for election in found:
        write_election(p, election)
    gapped = [f"{e.slug} {c.code}" for e in found for c in e.contests if c.districts and c.district_notes]
    reasons: list[str] = []
    if gapped:
        reasons.append(
            "The election database's polling districts differ from Statistics Denmark's per-council count by a few "
            f"votes (at most one in a thousand) in {', '.join(gapped)}; both are the authority's own figures, and "
            "the per-council figures are kept as the declared totals."
        )
    if any(c.candidate_votes and (c.districts or c.units[0].scheme != "dk.kommune") for e in found for c in e.contests):
        reasons.append(
            "Statistics Denmark publishes the personal votes of the elected candidates per council only, not per "
            "polling district, so the candidates' votes are declared for the council and have no polling-district "
            "counts to add up to."
        )
    if reasons:
        p.accept("accounting.declared", " ".join(reasons))
    return p


def write_election(p: Package, election: Election) -> None:
    id = key("election", election.slug)
    p.add(
        "election",
        id=id,
        polity_id=DENMARK,
        kind=election.kind,
        date_from=election.day,
        date_to=election.day,
        status="held",
    )
    p.add("slug", path=election.path, entity_id=id, is_current=True)
    p.name(id, election.english, "en", "official")
    p.name(id, election.danish, "da", "native", False)
    for contest in election.contests:
        write_contest(p, election, id, contest)


def write_contest(p: Package, election: Election, election_id: str, c: Contest) -> None:
    contest = key("contest", election.slug, c.code)
    seats = sum(c.seats.values()) or None
    fields: dict[str, object] = {"round_number": 1, "default_result_kind": "counted", "magnitude": seats}
    if c.kind == "list_allocation":
        fields["body_id"] = c.body
        if c.body == FOLKETING_BODY:
            fields["magnitude"] = 175
    p.add("contest", id=contest, election_id=election_id, kind=c.kind, electoral_system_id=c.system, **fields)
    p.name(contest, c.english, "en", "official")
    p.name(contest, c.danish, "da", "native", False)
    p.add("slug", path=f"{election.path}/{c.code}", entity_id=contest, is_current=True)
    if c.kind == "ballot_question":
        p.add("ballot_question", contest_id=contest, type="referendum")
    for table in [*c.tables, *([SEATS, SEATS_BY_AGE] if c.body == FOLKETING_BODY else [])]:
        p.uses(raw.cached(SOURCE, info_url(table)))
        p.uses(raw.cached(SOURCE, data_url(), body(table)))
    result_set = key("result_set", election.slug, c.code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=DST_SOURCE,
        status="superseded" if c.districts else "active",
        is_default=not c.districts,
        completeness=1.0,
    )
    detailed = any(u.scheme == "dk.afstemningsomraade" for u in c.units)
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        lang="en",
        body_md=METHOD if detailed else AGGREGATE_METHOD,
    )
    for text in c.notes:
        p.add("note", id=key("note", result_set, text), entity_id=result_set, kind="caveat", body_md=text, lang="en")
    ids: dict[str, str] = {}
    for option in c.options.values():
        ids[option.code] = key("option", election.slug, c.code, option.code)
    for option in c.options.values():
        id = ids[option.code]
        kind = option.kind
        p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind=kind,
            parent_option_id=ids[option.parent] if option.parent else None,
            party_id=key("party", option.party) if option.party else None,
            ballot_position=None,
            ballot_label=display(option.label) if kind == "list" else option.label,
        )
        if kind == "candidate":
            person = key("person", election.slug, c.code, option.code)
            p.add("person", id=person)
            p.name(person, option.label, "da", "official")
            parent = c.options[option.parent] if option.parent else None
            p.add(
                "candidacy",
                id=key("candidacy", election.slug, c.code, option.code),
                person_id=person,
                option_id=id,
                role="list_member",
                ballot_name=option.label,
                member_of_party_id=key("party", parent.party) if parent and parent.party else None,
                is_independent=parent is None or parent.kind == "others",
            )
    for unit in c.units:
        code = f"{election.slug}:{unit.code}"
        unit_id = key("unit", election.slug, c.code, unit.code)
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=DENMARK,
            scheme_code=unit.scheme,
            code=code,
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit_id, unit.name, "da", "official")
        for measure, value in unit.accounting.items():
            put(p, result_set, unit_id, None, measure, value)
        for (option, measure), value in unit.values.items():
            put(p, result_set, unit_id, ids[option], measure, value)
        if unit.scheme == "dk.kommune":
            for option, value in c.candidate_votes.items():
                put(p, result_set, unit_id, ids[option], CANDIDATE_VOTES, value)
    if not any(u.scheme == "dk.kommune" for u in c.units):
        # the polling districts do not carry the candidates' votes, which the source gives for the whole council
        for option, value in c.candidate_votes.items():
            p.add("declared", result_set_id=result_set, option_id=ids[option], measure=CANDIDATE_VOTES, value=value)
    for (option, measure), value in c.declared.items():
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=ids[option] if option else None,
            measure=measure,
            value=value,
        )
    for option, count in c.seats.items():
        p.add("outcome", result_set_id=result_set, option_id=ids[option], kind="won", seats=count)
    if c.districts:
        write_districts(p, election, election_id, contest, c, result_set, ids)


def write_districts(
    p: Package, election: Election, election_id: str, contest: str, c: Contest, superseded: str, ids: dict[str, str]
) -> None:
    """The election database's polling-district count, superseding the per-council set as the contest's default.

    The per-council figures the superseded set holds are declared on the new set, so the districts are checked
    against them; the seats are carried over from the per-council count."""
    result_set = key("result_set", election.slug, c.code, "districts")
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=valgdatabasen.SOURCE_ID,
        status="active",
        is_default=True,
        supersedes_id=superseded,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        lang="en",
        body_md=valgdatabasen.LOCAL_METHOD,
    )
    for text in c.district_notes:
        p.add("note", id=key("note", result_set, text), entity_id=result_set, kind="caveat", body_md=text, lang="en")
    url = valgdatabasen.local_artifact(c.extract).url if c.extract is not None else ""
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=valgdatabasen.SOURCE_ID,
        locator=json.dumps({"url": url, "site": valgdatabasen.SITE}),
    )
    for unit in c.districts:
        unit_id = key("unit", election.slug, c.code, "district", unit.code)
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=DENMARK,
            scheme_code=unit.scheme,
            code=f"{election.slug}:{unit.code}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit_id, unit.name, "da", "official")
        for measure, value in sorted(unit.accounting.items()):
            put(p, result_set, unit_id, None, measure, value)
        for (option, measure), value in unit.values.items():
            put(p, result_set, unit_id, ids[option], measure, value)
    (held,) = c.units
    for measure, value in sorted(held.accounting.items()):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for (option, measure), value in held.values.items():
        p.add("declared", result_set_id=result_set, option_id=ids[option], measure=measure, value=value)
    for option, value in c.candidate_votes.items():
        p.add("declared", result_set_id=result_set, option_id=ids[option], measure=CANDIDATE_VOTES, value=value)
    for option, count in c.seats.items():
        p.add("outcome", result_set_id=result_set, option_id=ids[option], kind="won", seats=count)


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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
