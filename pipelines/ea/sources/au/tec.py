"""Tasmanian Electoral Commission: House of Assembly, Legislative Council, and local government elections.

The commission publishes House of Assembly first preferences by polling place as spreadsheets, and every other
count as HTML result pages. One package holds each election, plus a `geo` and a `registry` package.
"""

import functools
import re
from collections.abc import Callable
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import NamedTuple
from uuid import uuid5

import httpx
import polars as pl
import pymupdf

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
    head = re.sub(r"^(?:[a-z]+ )+(?=[A-Z])", "", head)
    head = re.sub(r"^(?i:mc|mac|de|di|van|la|le|von|del|da) ?(?=[A-Z])", "", head)
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


def _ha_2014() -> HaElection:
    names = {"bass": "Bass", "braddon": "Braddon", "clark": "Denison", "franklin": "Franklin", "lyons": "Lyons"}
    results = f"{HA}/StateElection2014/Results"
    return HaElection(
        date(2014, 3, 15),
        {
            code: (f"{results}/{name}/2014 {name} first preferences by polling place.pdf".replace(" ", "%20"), None)
            for code, name in names.items()
        },
        {
            code: f"{results}/{name}/2014 {name} scrutiny event sheet.pdf".replace(" ", "%20")
            for code, name in names.items()
        },
    )


HA_ELECTIONS = [
    _ha_2014(),
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
    if re.fullmatch(r"pre-?poll votes", name, re.IGNORECASE):
        return "aggregate"
    if re.search(r"postal|telephone", name, re.IGNORECASE):
        return "postal_district"
    if re.search(r"mobile", name, re.IGNORECASE):
        return "mobile"
    if SPECIAL.search(name) and "pre-poll" not in name.lower() or name.lower().startswith("out of division"):
        return "aggregate"
    return "standard"


@functools.cache
def sheet(url: str, name: str | None) -> Sheet:
    if url.endswith(".pdf"):
        return grid(url, name, pdf_grid(url))
    frames = pl.read_excel(
        raw.cached(SOURCE, url).path, sheet_name=name, has_header=False, infer_schema_length=0, engine="calamine"
    )
    frame = frames if name is not None else next(iter(frames.values())) if isinstance(frames, dict) else frames
    return grid(url, name, [[clean(cell or "") for cell in row] for row in frame.rows()])


def is_figure(label: str) -> bool:
    return bool(re.fullmatch(r"\d[\d ]*", label))


BALLOT = re.compile(
    r"((?:(?i:van|von|der|den|de|di|la|le|del|da|du) )*(?:(?i:mc|mac) ?)?[A-Z][A-Z'\-]+(?: [A-Z][A-Z'\-]+)*) (\S.*)"
)


def ballot_name(label: str) -> str:
    """The 2014 PDFs print `SURNAME Given`; the workbooks print `SURNAME, Given`."""
    label = re.sub(r"^\([A-Z]\) ", "", label)
    found = BALLOT.fullmatch(label)
    return f"{found.group(1)}, {found.group(2)}" if found and "," not in label else label


def pdf_grid(url: str) -> list[list[str]]:
    """The commission's 2014 PDFs print the places across, a few to a page, with the names set sideways. Lay
    them out as one grid like the later workbooks: a `Candidates` header row, then one row per candidate."""
    pages: list[list[list[str]]] = []
    for page in pymupdf.open(raw.cached(SOURCE, url).path):
        names: list[tuple[float, str]] = []
        lines: list[tuple[float, float, float, str]] = []  # x0, x1, y0, text
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                label = clean("".join(span["text"] for span in line["spans"]))
                x0, y0, x1 = line["bbox"][0], line["bbox"][1], line["bbox"][2]
                if not label:
                    continue
                if line["dir"][0] == 0:
                    names.append(((x0 + x1) / 2, label))
                else:
                    lines.append((x0, x1, y0, label))
        names.sort()
        groups: list[list[tuple[float, str]]] = []
        for center, label in names:  # a name that wraps has its lines one text height apart
            if groups and center - groups[-1][-1][0] < 15:
                groups[-1].append((center, label))
            else:
                groups.append([(center, label)])
        if not groups:
            raise ValueError(f"{url}: a page has no column names")
        centers = [group[0][0] for group in groups]
        header = ["Candidates", *(clean(" ".join(label for _, label in group)) for group in groups)]
        body = [
            (y0, [label] + [""] * len(centers))
            for x0, x1, y0, label in sorted(lines, key=lambda line: line[2])
            if 90 <= y0 <= 780 and not is_figure(label)
        ]
        body = [(y0, [ballot_name(row[0]), *row[1:]]) for y0, row in body]
        for x0, x1, y0, label in lines:
            if 90 <= y0 <= 780 and is_figure(label):
                row = min(body, key=lambda item: abs(item[0] - y0))
                if abs(row[0] - y0) > 3:
                    raise ValueError(f"{url}: figure {label} has no row label")
                index = min(range(len(centers)), key=lambda i: abs(centers[i] + 21 - x1))
                row[1][index + 1] = label
        pages.append([header, *(row for _, row in body)])
    merged = pages[0]
    for other in pages[1:]:
        if [row[0] for row in other] != [row[0] for row in merged]:
            raise ValueError(f"{url}: the pages do not list the same rows")
        for row, extra in zip(merged, other, strict=True):
            row += extra[1:]
    return merged


def grid(url: str, name: str | None, rows: list[list[str]]) -> Sheet:
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
            if pending and url.endswith(".pdf"):  # the PDFs print the ungrouped candidates without a group row
                candidates += [Candidate(ballot, None, v) for ballot, v in pending]
                pending = []
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
    r"(?<![A-Za-z])((?:(?i:mc|mac|de|di|van|la|le|von|del|da) ?)?[A-Z][A-Z'\-]+(?: [A-Z][A-Z'\-]+)*, "
    r"[A-Z][A-Za-z'\-.()]*(?: [A-Za-z'\-.()]+)*?) [\d ,]+ Elected (\d+)"
)


EVENT = re.compile(r"([A-Za-z'’\-]+)(?:, ([A-Z])\.)? elected (\d+)")


def ha_event_sheet(url: str, ballots: list[str]) -> Page:
    """The scrutiny event sheet's remarks name each member as `SURNAME elected n` (or `SURNAME, I. elected n`), and
    the first count's line ends with the total of formal votes."""
    body = clean(" ".join(page.get_text() for page in pymupdf.open(raw.cached(SOURCE, url).path)))
    elected: dict[str, int] = {}
    for surname, initial, order in EVENT.findall(body):
        found = [
            b
            for b in ballots
            if b.partition(",")[0].casefold() == surname.casefold()
            and (not initial or b.partition(",")[2].strip().startswith(initial))
        ]
        if len(found) != 1:
            raise ValueError(f"{url}: {surname} {initial} elected matches {found}")
        elected[found[0]] = int(order)
    formal = re.search(r"(\d{5,6}) [^\d]{0,60}? elected 1\b", body)
    return Page(elected, int(formal.group(1)) if formal else None, None, None)


def ha_page(url: str, ballots: list[str] | None = None) -> Page:
    if url.endswith(".pdf"):
        return ha_event_sheet(url, ballots or [])
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
    "First preferences per polling place from the Tasmanian Electoral Commission's spreadsheet (for 2014, PDF) of "
    "first preferences by polling place. Pre-poll voting centers are stored as places; out-of-division, postal "
    "(including telephone), mobile, and provisional votes are reported once per division and stored as one unit "
    "each. Informal ballot papers are stored on the same units. Where the commission's distribution of preferences "
    "page gives the enrolment, it is stored on its own unit. Declared outcomes are the candidates the commission's "
    "distribution of preferences (for 2014, its scrutiny event sheet) marks as elected; the later counts are not "
    "stored."
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
        page = ha_page(election.pages[code], [c.ballot for c in data.candidates])
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
    files = [lc_summary_url(span) for span in LC_SUMMARIES]
    for day, (_, divisions) in LC_VOTES.items():
        for division in divisions:
            if found := lc_found(day, division):
                files += [url for url in found if url]
    return files


def lg_files() -> list[str]:
    files: list[str] = []
    for year in LG_YEARS:
        for council in COUNCILS:
            files += [lg_page(year, council), *lg_found(year, council).values()]
    return files


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
    names: set[str] = set()
    for day, (_, divisions) in LC_VOTES.items():
        for division in divisions:
            if found := lc_found(day, division):
                names |= {o.party for o in lc_count(*found).options if o.party}
    return names


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


def builders() -> dict[str, Callable[[], Package]]:
    out: dict[str, Callable[[], Package]] = {}
    for election in HA_ELECTIONS:
        out[f"{SOURCE}/ha-{election.day}"] = functools.partial(ha_election, election)
    for held in lc_held():
        out[f"{SOURCE}/lc-{held.day}"] = functools.partial(lc_election, held.day)
    for year, day in LG_YEARS.items():
        out[f"{SOURCE}/lg-{day}"] = functools.partial(lg_election, year)
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


# Local government --------------------------------------------------------------------------------------------------------

LG = f"{ROOT}/local-government"
LG_YEARS = {2022: date(2022, 10, 25), 2026: date(2026, 10, 27)}
LG_ROLES = {"c": "councillor", "m": "mayor", "dm": "deputy"}


def lg_page(year: int, council: str) -> str:
    return f"{LG}/elections-{year}/results/{council}/index.html"


def lg_fragment(year: int, council: str, role: str) -> str:
    return f"{LG}/elections-{year}/results/{council}/{COUNCILS[council][1]}-{role}.html"


@functools.cache
def lg_found(year: int, council: str) -> dict[str, str]:
    """The result fragments a council's page holds, by role, among the ones the cache has."""
    have = raw.keys(SOURCE)
    return {role: url for role in LG_ROLES if (url := lg_fragment(year, council, role)) in have}


def lg_fetch() -> None:
    for year in LG_YEARS:
        for council in COUNCILS:
            raw.fetch(SOURCE, lg_page(year, council), suffix=".html")
            for role in LG_ROLES:
                url = lg_fragment(year, council, role)
                try:
                    raw.fetch(SOURCE, url, suffix=".html")
                except httpx.HTTPStatusError as failure:
                    print(f"{url}: {failure.response.status_code}", flush=True)


def fetch_files(urls: list[str]) -> None:
    for url in dict.fromkeys(urls):
        raw.fetch(SOURCE, url, suffix=".xlsx" if url.endswith(".xlsx") else ".pdf" if url.endswith(".pdf") else ".html")


def fetch() -> None:
    for election in HA_ELECTIONS:
        fetch_files(ha_files(election))
    lc_fetch()
    lg_fetch()


NAME = r"(?:(?i:mc|mac|de|di|van|la|le|von|del|da) ?)?[A-Z][A-Z'\-]+(?: [A-Z][A-Z'\-]+)*, [A-Z][A-Za-z'\-.()]*(?: [A-Za-z'\-.()]+)*?"


class Contested(NamedTuple):
    ballots: list[str]
    votes: list[int]
    elected: list[str]
    formal: int | None
    informal: int | None


def _figure(body: str, label: str) -> int | None:
    found = re.search(rf"{label}[:\s]*([\d,]+)", body)
    return number(found.group(1)) if found else None


def lg_councillors(html: str) -> Contested:
    rows = table_rows(html)
    body = plain(html)
    status = next((i for i, row in enumerate(rows) if row[0].startswith("Elected candidates")), None)
    elected: list[str] = []
    if status is not None:
        for row in rows[status + 1 :]:
            if not surname_like(row[0]):
                break
            elected.append(re.sub(r"\s*\[.*", "", row[0]))
    ballots: list[str] = []
    votes: list[int] = []
    for row in rows:
        if len(row) >= 3 and surname_like(row[0]) and (n := number(row[1])) is not None and row[2].endswith("%"):
            ballots.append(row[0])
            votes.append(n)
    if not elected:
        elected = [n.strip() for n in re.findall(NAME, body)] if "unopposed" in body else []
    return Contested(
        ballots, votes, elected, _figure(body, "Total formal votes"), _figure(body, "Informal ballot papers")
    )


def lg_single(html: str) -> Contested:
    rows = table_rows(html)
    body = plain(html)
    head = next((i for i, row in enumerate(rows) if row[0] == "" and sum(surname_like(c) for c in row) >= 2), None)
    if head is None:
        names = re.findall(rf"({NAME}) elected unopposed", body)
        return Contested(names, [], names, None, None)
    ballots = [c for c in rows[head] if surname_like(c)]
    first = rows[head + 1]
    votes = [number(c) or 0 for c in first[1 : 1 + len(ballots)]]
    formal = number(first[rows[head].index("Formal votes")]) if "Formal votes" in rows[head] else None
    surnames = {b.partition(",")[0]: b for b in ballots}
    elected: list[str] = []
    for row in rows[head + 1 :]:
        if (m := re.fullmatch(r"(.+?) elected", row[-1])) and m.group(1) in surnames:
            elected.append(surnames[m.group(1)])
    return Contested(ballots, votes, elected, formal, _figure(body, "Informal ballot papers"))


def lg_unopposed(html: str) -> dict[str, list[str]]:
    """Contests the council's page settles without a ballot, by role."""
    body = plain(html)
    out: dict[str, list[str]] = {}
    for label, name in re.findall(
        rf"((?:Deputy )?(?:[Ll]ord )?[Mm]ayor) \d+ candidates? - no ballot required ({NAME}) elected unopposed", body
    ):
        out["deputy" if label.lower().startswith("deputy") else "mayor"] = [name.strip()]
    if found := re.search(
        r"Elected unopposed, no ballot required\.\s*Elected candidates \(in alphabetical order\)(.*?)(?:\d{4} State|Back to top|$)",
        body,
    ):
        out["councillor"] = [name.strip() for name in re.findall(NAME, found.group(1))]
    return out


class Council(NamedTuple):
    roll: int | None
    seats: int | None


def lg_header(url: str) -> Council:
    body = plain(text(url))
    seats = re.search(r"Number to be elected:\s*(\d+) councillors", body)
    return Council(_figure(body, "Electors enrolled"), int(seats.group(1)) if seats else None)


LG_METHOD = (
    "Councillors, mayors, and deputy mayors are elected at one postal election, so the Tasmanian Electoral "
    "Commission reports each contest once per council and the count is stored as one unit per contest. First "
    "preferences are as the commission's results page shows them; the later counts of the distribution of "
    "preferences are not stored. Declared outcomes are the candidates the commission marks as elected. "
    "Enrolment is the commission's electors enrolled for the council."
)


def lg_election(year: int) -> Package:
    day = LG_YEARS[year]
    p = Package(
        f"{SOURCE}/lg-{day}",
        SOURCE,
        f"The {year} Tasmanian local government elections for 29 councils, from the Tasmanian Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA}
    election_id = key("election", "lg", day)
    path = f"au/tas/{day}-local-government"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="local",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{year} Tasmanian local government elections", "en", "official")
    p.add(
        "note",
        id=key("note", election_id),
        entity_id=election_id,
        kind="context",
        body_md=(
            f"A postal election. Votes had to reach the Tasmanian Electoral Commission by 2 pm on {day:%-d %B %Y}."
        ),
        lang="en",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    units = Units(p, "au.tas_polling_place", day)
    values = Values()
    for council, (label, _) in COUNCILS.items():
        page = lg_page(year, council)
        p.uses(raw.cached(SOURCE, page))
        header = lg_header(page)
        district = area("au.tas_council", council)
        found = lg_found(year, council)
        entries: list[tuple[str, str, Contested]] = []
        for role, url in found.items():
            p.uses(raw.cached(SOURCE, url))
            html = text(url)
            result = lg_councillors(html) if role == "c" else lg_single(html)
            kind = LG_ROLES[role]
            if role == "c" and not result.ballots:
                result = lg_single(html)
                kind = "councillor-vacancy"
            if result.ballots:
                entries.append((role, kind, result))
        for kind, names in lg_unopposed(text(page)).items():
            role = {"councillor": "c", "mayor": "m", "deputy": "dm"}[kind]
            if role == "c" and any(r == "c" and res.votes for r, _, res in entries):
                entries.append(("c", "councillor-unopposed", Contested(names, [], names, None, None)))
            elif not any(r == role for r, _, _ in entries):
                entries.append((role, kind, Contested(names, [], names, None, None)))
        elected_seats = sum(len(res.elected) for r, _, res in entries if r == "c")
        if header.seats is not None and header.seats != elected_seats:
            raise ValueError(f"{year} {council}: {elected_seats} councillors elected for {header.seats} seats")
        for role, kind, result in entries:
            contested = bool(result.votes)
            contest = key("contest", "lg", day, council, kind)
            title = {
                "councillor": "Councillors",
                "councillor-unopposed": "Councillors elected unopposed",
                "councillor-vacancy": "Councillor vacancy",
                "mayor": "Mayor",
                "deputy": "Deputy Mayor",
            }[kind]
            if role == "c":
                p.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="body_seats",
                    body_id=key("body", "council", council),
                    district_area_id=district,
                    magnitude=len(result.elected),
                    round_number=1,
                    electoral_system_id=LG_MAYOR if kind == "councillor-vacancy" else LG_COUNCILLOR,
                    default_result_kind="counted" if contested else "tacit",
                )
            else:
                p.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="office",
                    office_id=key("office", council, kind if kind == "deputy" else "mayor"),
                    district_area_id=district,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=LG_MAYOR,
                    default_result_kind="counted" if contested else "tacit",
                )
            p.name(contest, f"{label} Council: {title}", "en", "official")
            p.add("slug", path=f"{path}/{council}/{kind}", entity_id=contest, is_current=True)
            rs = key("result_set", contest)
            p.add(
                "result_set",
                id=rs,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted" if contested else "tacit",
                source_id=TEC,
                status="active",
                is_default=True,
                completeness=1.0 if contested else 0.0,
            )
            p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=LG_METHOD, lang="en")
            options: dict[str, str] = {}
            for position, ballot in enumerate(result.ballots, 1):
                option = options[ballot] = key("option", "lg", day, council, kind, ballot)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=position,
                    ballot_label=ballot,
                )
                person = key("person", "lg", day, council, kind, ballot)
                p.add("person", id=person)
                p.name(person, person_name(ballot), "en", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", "lg", day, council, kind, ballot),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    is_independent=True,
                )
                p.add(
                    "outcome",
                    result_set_id=rs,
                    option_id=option,
                    kind="elected" if ballot in result.elected else "lost",
                )
            unknown = set(result.elected) - set(options)
            if unknown:
                raise ValueError(f"{year} {council} {kind}: elected {sorted(unknown)} are not on the ballot")
            if not contested:
                continue
            unit = units.add(unit_key(day, council, kind), f"{label} Council: {title}", district, "aggregate")
            for ballot, votes in zip(result.ballots, result.votes, strict=True):
                values.add(rs, unit, options[ballot], VOTES, votes)
            if result.formal is not None:
                values.add(rs, unit, None, VALID, result.formal)
                if result.informal is not None:
                    values.add(rs, unit, None, INFORMAL, result.informal)
                    values.add(rs, unit, None, CAST, result.formal + result.informal)
                p.add("declared", result_set_id=rs, option_id=None, measure=VALID, value=result.formal)
            if header.roll is not None:
                roll = units.add(
                    unit_key(day, council, kind, "roll"), f"{label} Council: electoral roll", district, "aggregate"
                )
                values.add(rs, roll, None, ROLL, header.roll)
    p.frame("result_value", values.frame())
    return p


# Legislative Council ----------------------------------------------------------------------------------------------------

LC = f"{ROOT}/legislative-council"
LC_SUMMARIES = ["1981-1996", "1997-1998", "1999-2008", "2009-2017", "2018-present"]
MONTHS: dict[str, int] = {
    name: number_
    for number_, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        1,
    )
}


def lc_summary_url(span: str) -> str:
    return f"{LC}/LCHistory/Summary/{span}.html"


class Seat(NamedTuple):
    division: str
    retiring: str
    elected: str
    unopposed: bool
    by_election: bool


class Held(NamedTuple):
    day: date
    seats: list[Seat]


def _day(text_: str, year: int) -> date | None:
    found = re.search(r"(\d{1,2}) ([A-Z][a-z]+)", text_)
    return date(year, MONTHS[found.group(2)], int(found.group(1))) if found and found.group(2) in MONTHS else None


@functools.cache
def lc_held() -> list[Held]:
    held: list[Held] = []
    for span in LC_SUMMARIES:
        html = text(lc_summary_url(span))
        year = 0
        for block in re.split(r'<div class="panel creamfeature"', html)[1:]:
            if found := re.search(r"<h2>(\d{4})</h2>", block):
                year = int(found.group(1))
            rows = table_rows(block)
            day = next(
                (
                    _day(row[i + 1], year)
                    for row in rows
                    for i, cell in enumerate(row[:-1])
                    if cell.lower().startswith("polling day")
                ),
                None,
            )
            seats: list[Seat] = []
            heading = next((i for i, row in enumerate(rows) if row[:1] == ["Division"]), None)
            if day is None or heading is None:
                continue
            for row in rows[heading + 1 :]:
                if len(row) != 3 or not row[0] or row[0] == "Division":
                    continue
                by = "(by-election)" in row[0]
                elected = row[2]
                unopposed = "(unopposed)" in elected
                seats.append(
                    Seat(
                        re.sub(r"\s*\(.*", "", row[0]),
                        row[1],
                        re.sub(r"\s*\(unopposed\)", "", elected).strip(),
                        unopposed,
                        by,
                    )
                )
            held.append(Held(day, seats))
    return held


LC_VOTES: dict[date, tuple[str, list[str]]] = {
    date(2021, 5, 1): (f"{LC}/LegislativeCouncilElections_2021/results", ["derwent", "mersey", "windermere"]),
    date(2022, 5, 7): (f"{LC}/legislative-council-elections-2022/results", ["elwick", "huon", "mcintyre"]),
    date(2022, 9, 10): (f"{LC}/legislative-council-byelection-2022/results", ["pembroke"]),
    date(2023, 5, 6): (f"{LC}/elections-2023/results", ["launceston", "murchison", "rumney"]),
    date(2024, 5, 4): (f"{LC}/elections-2024/results", ["elwick", "hobart", "prosser"]),
    date(2025, 5, 24): (f"{LC}/elections-2025/results", ["montgomery", "nelson", "pembroke"]),
    date(2026, 5, 2): (f"{LC}/elections-2026/results", ["huon", "rosevears"]),
}


def lc_urls(day: date, division: str) -> list[str]:
    base = LC_VOTES[day][0]
    return [f"{base}/{division}/fp-{division}-{s}.html" for s in ("m", "e")] + [
        f"{base}/{division}/dist-{division}-m.html"
    ]


def lc_found(day: date, division: str) -> tuple[str, str | None] | None:
    have = raw.keys(SOURCE)
    first, second, dist = lc_urls(day, division)
    fp = first if first in have else second if second in have else None
    return (fp, dist if dist in have else None) if fp else None


def lc_fetch() -> None:
    for span in LC_SUMMARIES:
        raw.fetch(SOURCE, lc_summary_url(span), suffix=".html")
    for day, (_, divisions) in LC_VOTES.items():
        for division in divisions:
            for url in lc_urls(day, division):
                try:
                    raw.fetch(SOURCE, url, suffix=".html")
                except httpx.HTTPStatusError:
                    continue


class LcOption(NamedTuple):
    ballot: str
    party: str | None


class LcCount(NamedTuple):
    options: list[LcOption]
    places: list[tuple[str, list[int], int | None, int | None]]  # name, votes, formal, informal
    roll: int | None
    declared: list[int]
    elected: str | None


HEAD = re.compile(
    r'<span class="candidate-name">(.*?)</span>\s*(?:<br\s*/?>)?\s*<span class="candidate-party">(.*?)</span>',
    re.DOTALL | re.IGNORECASE,
)


def lc_count(fp: str, dist: str | None) -> LcCount:
    html = text(fp)
    options: list[LcOption] = []
    for name, party_name in HEAD.findall(html):
        parts = [clean(re.sub(r"<[^>]+>", " ", part)) for part in re.split(r"<br\s*/?>", name)]
        ballot = f"{parts[0]}, {' '.join(parts[1:])}".strip(", ")
        label = clean(party_name)
        options.append(LcOption(ballot, None if label.casefold() in NO_PARTY else label))
    rows = table_rows(html)
    width = len(options)
    places: list[tuple[str, list[int], int | None, int | None]] = []
    declared: list[int] = []
    for row in rows[1:]:
        if len(row) < width + 2:
            continue
        values = [number(c) for c in row[1 : 1 + width]]
        if row[0].upper() == "TOTALS":
            declared = [v or 0 for v in values]
        elif row[0] and not row[0].lower().startswith("total") and None not in values:
            places.append((row[0], [v or 0 for v in values], number(row[1 + width]), number(row[2 + width])))
    body = plain(html)
    final = table_rows(text(dist)) if dist else []
    remark = next((m.group(1) for row in reversed(final) if (m := re.fullmatch(r"(.+?) elected", row[-1]))), None)
    surnames = {o.ballot.partition(",")[0]: o.ballot for o in options}
    return LcCount(options, places, _figure(body, "Enrolled"), declared, surnames.get(remark or ""))


def _same(ballot: str, name: str) -> bool:
    """Whether a declared name such as `Cassy O'Connor` is the ballot name `O'CONNOR, Cassy`."""
    surname = re.sub(r"[^a-z]", "", ballot.partition(",")[0].casefold())
    return re.sub(r"[^a-z]", "", name.casefold()).endswith(surname)


LC_METHOD = (
    "First preferences per polling place from the Tasmanian Electoral Commission's results page for the "
    "division. Postal, provisional, and out-of-division votes are reported once per division and stored as one "
    "unit each. Informal ballot papers are stored on the same units. Later counts of the distribution of "
    "preferences are not stored; the declared outcome is the candidate the commission's distribution marks as "
    "elected."
)
LC_SUMMARY_METHOD = (
    "The commission's summary of Legislative Council elections lists each division's retiring member and the "
    "candidate elected. It gives no vote counts, so this contest records the declared winner only."
)


def lc_election(day: date) -> Package:
    held = next(h for h in lc_held() if h.day == day)
    by = all(seat.by_election for seat in held.seats)
    title = (
        f"{day.year} Tasmanian Legislative Council {held.seats[0].division} by-election"
        if by and len(held.seats) == 1
        else f"{day.year} Tasmanian Legislative Council {'by-elections' if by else 'elections'}"
    )
    p = Package(
        f"{SOURCE}/lc-{day}",
        SOURCE,
        f"The {title[:1].lower() + title[1:]}, from the Tasmanian Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA}
    p.uses(raw.cached(SOURCE, lc_summary_url(next(s for s in LC_SUMMARIES if _span_has(s, day)))))
    election_id = key("election", "lc", day)
    path = f"au/tas/{day}-legislative-council"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="by_election" if by else "general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, title, "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    units = Units(p, "au.tas_polling_place", day)
    values = Values()
    for seat in held.seats:
        code = slug(seat.division)
        if seat.division not in LC_DIVISIONS:
            raise ValueError(f"{day}: unknown division {seat.division}")
        district = area("au.tas_lc_division", code)
        contest = key("contest", "lc", day, code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=key("body", "lc"),
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=LC_SYSTEM,
            default_result_kind="tacit" if seat.unopposed else "counted",
        )
        p.name(contest, f"Division of {seat.division}", "en", "official")
        p.add("slug", path=f"{path}/{code}", entity_id=contest, is_current=True)
        found = lc_found(day, code) if day in LC_VOTES else None
        rs = key("result_set", contest)
        p.add(
            "result_set",
            id=rs,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="tacit" if seat.unopposed else "counted",
            source_id=TEC,
            status="active",
            is_default=True,
            completeness=1.0 if found else 0.0,
        )
        p.add(
            "note",
            id=key("note", rs),
            entity_id=rs,
            kind="methodology",
            body_md=LC_METHOD if found else LC_SUMMARY_METHOD,
            lang="en",
        )
        options: dict[str, str] = {}
        if found:
            for url in found:
                if url:
                    p.uses(raw.cached(SOURCE, url))
            count = lc_count(*found)
            candidates = count.options
            winner = count.elected
            if winner is None:
                winner = next((o.ballot for o in candidates if _same(o.ballot, seat.elected)), None)
            if winner is None or not _same(winner, seat.elected):
                raise ValueError(f"{day} {code}: the count elects {winner}, the summary {seat.elected}")
        else:
            candidates = [LcOption(seat.elected, None)]
            winner = seat.elected
        for position, candidate in enumerate(candidates, 1):
            option = options[candidate.ballot] = key("option", "lc", day, code, candidate.ballot)
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
            person = key("person", "lc", day, code, candidate.ballot)
            p.add("person", id=person)
            p.name(
                person, person_name(candidate.ballot) if "," in candidate.ballot else candidate.ballot, "en", "official"
            )
            p.add(
                "candidacy",
                id=key("candidacy", "lc", day, code, candidate.ballot),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.ballot,
                member_of_party_id=party_id,
                is_independent=candidate.party is None and bool(found),
            )
            p.add(
                "outcome",
                result_set_id=rs,
                option_id=option,
                kind="elected" if candidate.ballot == winner else "lost",
            )
        if not found:
            continue
        label = seat.division
        for name, votes, formal, informal in count.places:
            unit = units.add(unit_key(day, code, name), name, district, place_kind(name))
            for candidate, n in zip(candidates, votes, strict=True):
                values.add(rs, unit, options[candidate.ballot], VOTES, n)
            if formal is not None:
                values.add(rs, unit, None, VALID, formal)
                if informal is not None:
                    values.add(rs, unit, None, INFORMAL, informal)
                    values.add(rs, unit, None, CAST, formal + informal)
        if count.roll is not None:
            roll = units.add(unit_key(day, code, "roll"), f"Division of {label}: electoral roll", district, "aggregate")
            values.add(rs, roll, None, ROLL, count.roll)
        for candidate, total in zip(candidates, count.declared, strict=False):
            p.add("declared", result_set_id=rs, option_id=options[candidate.ballot], measure=VOTES, value=total)
    p.frame("result_value", values.frame())
    return p


def _span_has(span: str, day: date) -> bool:
    return any(h.day == day for h in lc_held_for(span))


@functools.cache
def lc_held_for(span: str) -> list[Held]:
    first, _, last = span.partition("-")
    low = int(first)
    high = 2100 if last == "present" else int(last)
    return [h for h in lc_held() if low <= h.day.year <= high]
