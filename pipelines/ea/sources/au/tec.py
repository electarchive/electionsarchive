"""Tasmanian Electoral Commission: House of Assembly, Legislative Council, and local government elections.

The commission publishes House of Assembly first preferences by polling place as spreadsheets, and every other
count as HTML result pages. One package holds each election, plus a `geo` and a `registry` package.
"""

import functools
import re
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "au.tec"
ROOT = "https://www.tec.tas.gov.au"
AUSTRALIA = str(uuid5(package.NAMESPACE, "polity\x1fAU"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUS"))
SPACE = re.compile(r"[\s ]+")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def party(name: str) -> str:
    return key("party", name.casefold())


TEC = key("source", SOURCE)
HOUSE = key("body", "house")
COUNCIL = key("body", "council")
HARE_CLARK = key("electoral_system", "hare-clark")
LC_SYSTEM = key("electoral_system", "lc")
LG_COUNCILLOR = key("electoral_system", "lg-councillor")
LG_MAYOR = key("electoral_system", "lg-mayor")
NO_PARTY = {"", "independent", "ungrouped", "no party"}


# Reading -----------------------------------------------------------------------------------------------------------


def clean(text: str) -> str:
    return SPACE.sub(" ", unescape(text).replace("•", " ")).strip()


def number(text: str) -> int | None:
    digits = re.sub(r"[\s ,]", "", text)
    return int(digits) if digits.isdigit() else None


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.skip += 1
        elif tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.skip -= 1
        elif tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append(clean("".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if any(self.row):
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None and not self.skip:
            self.cell.append(data)


def table_rows(html: str) -> list[list[str]]:
    parser = _Rows()
    parser.feed(html)
    return parser.rows


def plain(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    return clean(re.sub(r"<[^>]+>", " ", html))


@functools.cache
def text(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_bytes().decode("utf-8", errors="replace")


def person_name(ballot: str) -> str:
    """Turn the commission's `SURNAME, Given` into `Given Surname`."""
    surname, _, given = (part.strip() for part in ballot.partition(","))
    surname = " ".join(
        word.title() if word.isupper() and len(word) > 1 else word for word in re.split(r"(?<=[ -])", surname)
    ).replace("- ", "-")
    surname = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), surname)
    surname = re.sub(r"\bO'([a-z])", lambda m: "O'" + m.group(1).upper(), surname)
    return " ".join(f"{given} {surname}".split())


def surname_like(name: str) -> bool:
    """A ballot name such as `McLENNAN, Charlene` has an upper-case surname; a group such as `Liberal Party` does not."""
    head = name.partition(",")[0]
    if "," not in name or not head:
        return False
    head = re.sub(r"^(Mc|Mac|De|Di|Van|La|Le|Von|Del|Da) ?(?=[A-Z])", "", head)
    return all(not ch.islower() for ch in head)


# Result values --------------------------------------------------------------------------------------------------------

VOTES, ROLL, CAST, VALID, INFORMAL = 1, 2, 3, 4, 5


class Values:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "count_round", "value")
        }

    def add(
        self, result_set: str, unit: str, option: str | None, measure: int, value: int, count_round: int = 0
    ) -> None:
        for column, item in zip(self.columns, (result_set, unit, option, measure, count_round, value), strict=True):
            self.columns[column].append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


# House of Assembly ------------------------------------------------------------------------------------------------------

DIVISIONS = {"bass": "Bass", "braddon": "Braddon", "clark": "Clark", "franklin": "Franklin", "lyons": "Lyons"}
HA = f"{ROOT}/house-of-assembly"


class HaElection(NamedTuple):
    day: date
    workbooks: dict[str, tuple[str, str | None]]  # division -> (workbook url, sheet name)
    pages: dict[str, str]  # division -> page with the final distribution of preferences


def _ha_2018() -> HaElection:
    names = {"bass": "Bass", "braddon": "Braddon", "clark": "Denison", "franklin": "Franklin", "lyons": "Lyons"}
    return HaElection(
        date(2018, 3, 3),
        {
            code: (f"{HA}/StateElection2018/Results/PPs/2018-{name}-first-prefs-by-polling-places.xlsx", None)
            for code, name in names.items()
        },
        {code: f"{HA}/StateElection2018/Results/{name}/index.html" for code, name in names.items()},
    )


def _ha(day: date, workbook: str, folder: str) -> HaElection:
    return HaElection(
        day,
        {code: (workbook, name) for code, name in DIVISIONS.items()},
        {code: f"{HA}/{folder}/{code}/dist-{code}-m.html" for code in DIVISIONS},
    )


HA_ELECTIONS = [
    _ha_2018(),
    _ha(
        date(2021, 5, 1),
        f"{HA}/StateElection2021/results/assets/2021-State-election-first-preferences-by-polling-places.xlsx",
        "StateElection2021/results",
    ),
    _ha(
        date(2024, 3, 23),
        f"{HA}/elections-2024/results/pdf/2024-state-election-first-preferences-by-polling-places.xlsx",
        "elections-2024/results",
    ),
    _ha(
        date(2025, 7, 19),
        f"{HA}/elections-2025/results/downloads/2025-state-election-first-preferences-by-polling-places.xlsx",
        "elections-2025/results",
    ),
]


def ha_files(election: HaElection) -> list[str]:
    return list(dict.fromkeys([*(url for url, _ in election.workbooks.values()), *election.pages.values()]))


class Place(NamedTuple):
    column: int
    name: str
    kind: str


class Candidate(NamedTuple):
    ballot: str
    party: str | None
    votes: list[int | None]


class Sheet(NamedTuple):
    places: list[Place]
    candidates: list[Candidate]
    formal: list[int | None]
    informal: list[int | None]
    cast: list[int | None]
    declared: dict[str, int]  # ballot name -> total column, when the workbook carries it


SPECIAL = re.compile(r"postal|mobile|out of division|provisional|telephone|absent|early|declaration", re.IGNORECASE)
GROUP = re.compile(r"group [a-z]", re.IGNORECASE)


def place_kind(name: str) -> str:
    if re.search(r"postal|telephone", name, re.IGNORECASE):
        return "postal_district"
    if re.search(r"mobile", name, re.IGNORECASE):
        return "mobile"
    if SPECIAL.search(name) and "pre-poll" not in name.lower() or name.lower().startswith("out of division"):
        return "aggregate"
    return "standard"


@functools.cache
def sheet(url: str, name: str | None) -> Sheet:
    frames = pl.read_excel(
        raw.cached(SOURCE, url).path, sheet_name=name, has_header=False, infer_schema_length=0, engine="calamine"
    )
    frame = frames if name is not None else next(iter(frames.values())) if isinstance(frames, dict) else frames
    rows = [[clean(cell or "") for cell in row] for row in frame.rows()]
    head = next(i for i, row in enumerate(rows) if row[0] == "Candidates")
    header = rows[head]
    width = max(i for i, cell in enumerate(header) if cell) + 1
    places: list[Place] = []
    totals: dict[str, int] = {}
    for column in range(1, width):
        label = header[column]
        if re.fullmatch(r"total( ordinary votes)?", label, re.IGNORECASE):
            totals[label.lower()] = column
        else:
            places.append(Place(column, label, place_kind(label)))
    candidates: list[Candidate] = []
    pending: list[tuple[str, list[int | None]]] = []
    formal: list[int | None] = []
    informal: list[int | None] = []
    cast: list[int | None] = []
    declared: dict[str, int] = {}
    last = totals.get("total")
    for row in rows[head + 1 :]:
        label = row[0]
        if not label:
            continue
        values = [number(row[p.column]) for p in places]
        lowered = label.lower()
        if lowered.startswith("total formal"):
            formal = values
        elif lowered.startswith("informal"):
            informal = values
        elif lowered.startswith("total ballot"):
            cast = values
        elif surname_like(label):
            pending.append((label, values))
            if last is not None and (total := number(row[last])) is not None:
                declared[label] = total
        else:
            owner = None if label.lower() in NO_PARTY or GROUP.fullmatch(label) else label
            column_sums = [sum(v[i] or 0 for _, v in pending) for i in range(len(places))]
            if column_sums != [v or 0 for v in values]:
                raise ValueError(f"{url} {name}: group {label} does not add up from its candidates")
            candidates += [Candidate(ballot, owner, v) for ballot, v in pending]
            pending = []
    if pending:
        raise ValueError(f"{url} {name}: candidates {[b for b, _ in pending]} have no group row")
    return Sheet(places, candidates, formal, informal, cast, declared)


class Page(NamedTuple):
    elected: dict[str, int]  # ballot name -> order of election
    formal: int | None
    roll: int | None
    informal: int | None


ELECTED = re.compile(
    r"(?<![A-Za-z])((?:Mc|Mac|De|Di|Van|La|Le|Von|Del|Da)? ?[A-Z][A-Z'\-]+(?: [A-Z][A-Z'\-]+)*, "
    r"[A-Z][A-Za-z'\-.()]*(?: [A-Za-z'\-.()]+)*?) [\d ,]+ Elected (\d+)"
)


def ha_page(url: str) -> Page:
    html = text(url)
    body = plain(html)
    elected: dict[str, int] = {}
    for name, order in ELECTED.findall(body):
        elected.setdefault(name.strip(), int(order))

    def find(label: str) -> int | None:
        found = re.search(rf"{label}[:\s]*([\d ,]+)", body)
        return number(found.group(1)) if found else None

    return Page(elected, find("Total formal votes"), find("Electors on roll"), None)


def unit_key(day: date, *parts: str) -> str:
    return ":".join([str(day), *parts])


class Units:
    """Reporting units of one election package."""

    def __init__(self, p: Package, scheme: str, day: date) -> None:
        self.p = p
        self.scheme = scheme
        self.day = day
        self.ids: dict[str, str] = {}

    def add(self, code: str, name: str, area_id: str, kind: str, parent: str | None = None) -> str:
        if code not in self.ids:
            id = self.ids[code] = key("unit", self.scheme, code)
            self.p.add(
                "reporting_unit",
                id=id,
                polity_id=AUSTRALIA,
                scheme_code=self.scheme,
                code=code,
                parent_id=parent,
                area_relation="within",
                area_id=area_id,
                kind=kind,
            )
            self.p.name(id, name, "en", "official")
        return self.ids[code]


def result_set(p: Package, election: str, contest: str, method: str) -> str:
    id = key("result_set", contest)
    p.add(
        "result_set",
        id=id,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=TEC,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    return id


HA_METHOD = (
    "First preferences per polling place from the Tasmanian Electoral Commission's spreadsheet of first preferences "
    "by polling place. Pre-poll voting centers are stored as places; out-of-division, postal (including telephone), "
    "mobile, and provisional votes are reported once per division and stored as one unit each. Informal ballot "
    "papers are stored on the same units. The enrolment is the commission's figure on its distribution of "
    "preferences page. Declared outcomes are the candidates the commission's distribution of preferences marks as "
    "elected; the later counts are not stored."
)


def ha_election(election: HaElection) -> Package:
    day = election.day
    p = Package(
        f"{SOURCE}/ha-{day}",
        SOURCE,
        f"The {day.year} Tasmanian House of Assembly election by polling place, from the Tasmanian Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA}
    for url in ha_files(election):
        p.uses(raw.cached(SOURCE, url))
    election_id = key("election", "ha", day)
    path = f"au/tas/{day}-house-of-assembly"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{day.year} Tasmanian state election", "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    units = Units(p, "au.tas_polling_place", day)
    values = Values()
    for code, (url, name) in election.workbooks.items():
        data = sheet(url, name)
        page = ha_page(election.pages[code])
        division = area("au.tas_division", code)
        label = DIVISIONS[code]
        seats = len(page.elected)
        contest = key("contest", "ha", day, code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=HOUSE,
            district_area_id=division,
            magnitude=seats,
            round_number=1,
            electoral_system_id=HARE_CLARK,
            default_result_kind="counted",
        )
        p.name(contest, f"Division of {label}", "en", "official")
        p.add("slug", path=f"{path}/{code}", entity_id=contest, is_current=True)
        rs = result_set(p, election_id, contest, HA_METHOD)
        options: dict[str, str] = {}
        for position, candidate in enumerate(data.candidates, 1):
            option = options[candidate.ballot] = key("option", "ha", day, code, candidate.ballot)
            party_id = party(candidate.party) if candidate.party else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party_id,
                ballot_position=position,
                ballot_label=candidate.ballot,
            )
            person = key("person", "ha", day, code, candidate.ballot)
            p.add("person", id=person)
            p.name(person, person_name(candidate.ballot), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", "ha", day, code, candidate.ballot),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.ballot,
                member_of_party_id=party_id,
                is_independent=candidate.party is None,
            )
            p.add(
                "outcome",
                result_set_id=rs,
                option_id=option,
                kind="elected" if candidate.ballot in page.elected else "lost",
            )
        missing = set(page.elected) - set(options)
        if missing:
            raise ValueError(f"{day} {code}: elected {sorted(missing)} are not on the ballot")
        for index, place in enumerate(data.places):
            unit = units.add(unit_key(day, code, place.name), place.name, division, place.kind)
            for candidate in data.candidates:
                values.add(rs, unit, options[candidate.ballot], VOTES, candidate.votes[index] or 0)
            for measure, series in ((VALID, data.formal), (INFORMAL, data.informal), (CAST, data.cast)):
                if series and series[index] is not None:
                    values.add(rs, unit, None, measure, series[index] or 0)
        if page.roll is not None:
            roll = units.add(unit_key(day, code, "roll"), f"Division of {label}: electoral roll", division, "aggregate")
            values.add(rs, roll, None, ROLL, page.roll)
        for ballot, total in data.declared.items():
            p.add("declared", result_set_id=rs, option_id=options[ballot], measure=VOTES, value=total)
        if page.formal is not None:
            p.add("declared", result_set_id=rs, option_id=None, measure=VALID, value=page.formal)
    p.frame("result_value", values.frame())
    return p


# Geography and registry -------------------------------------------------------------------------------------------------

COUNCILS = {
    "break-oday": ("Break O'Day", "bod"),
    "brighton": ("Brighton", "bri"),
    "burnie-city": ("Burnie City", "bcc"),
    "central-coast": ("Central Coast", "cco"),
    "central-highlands": ("Central Highlands", "chi"),
    "circular-head": ("Circular Head", "cir"),
    "clarence-city": ("Clarence City", "ccc"),
    "derwent-valley": ("Derwent Valley", "der"),
    "devonport-city": ("Devonport City", "dcc"),
    "dorset": ("Dorset", "dor"),
    "flinders": ("Flinders", "fli"),
    "george-town": ("George Town", "gtn"),
    "glamorgan-spring-bay": ("Glamorgan Spring Bay", "gsb"),
    "glenorchy-city": ("Glenorchy City", "gcc"),
    "hobart-city": ("Hobart City", "hcc"),
    "huon-valley": ("Huon Valley", "hvy"),
    "kentish": ("Kentish", "ken"),
    "king-island": ("King Island", "kgi"),
    "kingborough": ("Kingborough", "kgb"),
    "latrobe": ("Latrobe", "lat"),
    "launceston-city": ("Launceston City", "lcc"),
    "meander-valley": ("Meander Valley", "mvy"),
    "northern-midlands": ("Northern Midlands", "nmd"),
    "sorell": ("Sorell", "sor"),
    "southern-midlands": ("Southern Midlands", "smd"),
    "tasman": ("Tasman", "tsm"),
    "waratah-wynyard": ("Waratah-Wynyard", "wwy"),
    "west-coast": ("West Coast", "wco"),
    "west-tamar": ("West Tamar", "wtm"),
}
LC_DIVISIONS = [
    "Apsley",
    "Buckingham",
    "Cambridge",
    "Cornwall",
    "Derwent",
    "Elwick",
    "Emu Bay",
    "Gordon",
    "Hobart",
    "Huon",
    "Launceston",
    "Leven",
    "Macquarie",
    "McIntyre",
    "Meander",
    "Mersey",
    "Monmouth",
    "Montgomery",
    "Murchison",
    "Nelson",
    "Newdegate",
    "Paterson",
    "Pembroke",
    "Prosser",
    "Queenborough",
    "Roland",
    "Rosevears",
    "Rowallan",
    "Rumney",
    "Russell",
    "South Esk",
    "Tamar",
    "Wellington",
    "West Devon",
    "Western Tiers",
    "Westmorland",
    "Windermere",
]


def slug(name: str) -> str:
    return changeset.slug(name)


def lc_files() -> list[str]:
    return []


def lg_files() -> list[str]:
    return []


def all_files() -> list[str]:
    files = [f for election in HA_ELECTIONS for f in ha_files(election)]
    return list(dict.fromkeys([*files, *lc_files(), *lg_files()]))


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=TEC,
        kind="official_body",
        publisher="Tasmanian Electoral Commission",
        title="Tasmanian Electoral Commission election results",
        url=f"{ROOT}/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© State of Tasmania (Tasmanian Electoral Commission)",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Tasmania's five House of Assembly divisions, its Legislative Council divisions, and its 29 local "
        "government areas, with the polling-place scheme their results use",
    )
    p.external |= {AUSTRALIA, COUNTRY}
    source_row(p)
    for code, rank, kind, noun in (
        ("au.tas_division", 1, "electoral_district", "House of Assembly division"),
        ("au.tas_lc_division", 1, "electoral_district", "Legislative Council division"),
        ("au.tas_council", 1, "administrative", "local government area"),
        ("au.tas_polling_place", 3, "precinct", "polling place"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRALIA, kind=kind, rank=rank, noun=noun, code_system=None)
    for code, name in DIVISIONS.items():
        id = area("au.tas_division", code)
        p.add("area", id=id, scheme_code="au.tas_division")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "en", "official")
        if code == "clark":
            p.name(id, "Denison", "en", "historical", False)
    for name in LC_DIVISIONS:
        id = area("au.tas_lc_division", slug(name))
        p.add("area", id=id, scheme_code="au.tas_lc_division")
        p.add("area_code", area_id=id, code=slug(name))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "en", "official")
    for code, (name, _) in COUNCILS.items():
        id = area("au.tas_council", code)
        p.add("area", id=id, scheme_code="au.tas_council")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, f"{name} Council", "en", "official")
    return p


SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        HARE_CLARK,
        date(1909, 1, 1),
        None,
        {"formula": "stv", "quota": "droop", "surplus": "last_parcel"},
        (
            "The House of Assembly elects five members from each of five divisions (seven from each division from "
            "2024) by the Hare-Clark system, a single transferable vote with a Droop quota. Voters number "
            "candidates in order of preference, and surpluses are transferred by the last-parcel method."
        ),
    ),
    (
        LC_SYSTEM,
        date(1856, 1, 1),
        None,
        {"formula": "irv"},
        (
            "Each Legislative Council division elects one member for a six-year term by optional preferential "
            "voting. Elections for two or three of the fifteen divisions are held each year in May."
        ),
    ),
    (
        LG_COUNCILLOR,
        date(1993, 1, 1),
        None,
        {"formula": "stv", "quota": "droop", "surplus": "last_parcel"},
        (
            "Each council elects its councillors, including those who go on to serve as mayor and deputy mayor, "
            "by the Hare-Clark system at a postal election held every four years."
        ),
    ),
    (
        LG_MAYOR,
        date(1993, 1, 1),
        None,
        {"formula": "irv"},
        (
            "Mayors and deputy mayors are elected by all of a council's electors by preferential voting, "
            "at the same postal election as the councillors."
        ),
    ),
]


def registry_parties() -> dict[str, str]:
    names: dict[str, str] = {}
    for election in HA_ELECTIONS:
        for url, name in dict.fromkeys(election.workbooks.values()):
            for candidate in sheet(url, name).candidates:
                if candidate.party:
                    names.setdefault(candidate.party.casefold(), candidate.party)
    for name in lc_parties():
        names.setdefault(name.casefold(), name)
    return names


def lc_parties() -> set[str]:
    return set()


def registry() -> Package:
    names = registry_parties()
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Tasmania's House of Assembly, Legislative Council, and 29 councils, their electoral systems and mayoral "
        f"offices, and the {len(names)} parties on the Tasmanian Electoral Commission's results, with the source "
        "files every election package cites",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRALIA}
    for url in all_files():
        p.artifact(raw.cached(SOURCE, url), TEC)
    p.add("body", id=HOUSE, polity_id=AUSTRALIA, kind="lower_house")
    p.name(HOUSE, "Tasmanian House of Assembly", "en", "official")
    lc = key("body", "lc")
    p.add("body", id=lc, polity_id=AUSTRALIA, kind="upper_house")
    p.name(lc, "Tasmanian Legislative Council", "en", "official")
    for code, (name, _) in COUNCILS.items():
        body = key("body", "council", code)
        p.add("body", id=body, polity_id=AUSTRALIA, kind="council")
        p.name(body, f"{name} Council", "en", "official")
        for office, label, kind in (("mayor", "Mayor", "mayor"), ("deputy", "Deputy Mayor", "deputy_mayor")):
            id = key("office", code, office)
            p.add("office", id=id, polity_id=AUSTRALIA, body_id=body, kind=kind)
            p.name(id, f"{label} of {name}", "en", "official")
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=AUSTRALIA,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for casefolded, name in sorted(names.items()):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.add("external_id", entity_id=id, scheme="au.tec.party", value=casefolded)
        p.name(id, name, "en", "official")
    return p


# Build ---------------------------------------------------------------------------------------------------------------


def builders() -> dict[str, callable]:  # type: ignore[valid-type]
    out: dict[str, callable] = {}  # type: ignore[valid-type]
    for election in HA_ELECTIONS:
        out[f"{SOURCE}/ha-{election.day}"] = functools.partial(ha_election, election)
    return out


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *builders()]


def parse() -> None:
    geo().write()
    registry().write()
    for build in builders().values():
        build().write()
        text.cache_clear()


def emit() -> None:
    package.run("load", packages())
