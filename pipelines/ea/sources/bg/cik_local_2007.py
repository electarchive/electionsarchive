"""Bulgaria's local elections of 28 October and 4 November 2007, from the CIK's results site for that election.

The 2007 local elections predate the CIK's open data: the commission for local elections published its results as a
static site, and the CIK offers that site as one zip of HTML pages per round (`results_1.zip`, `results_2.zip`). Each
province folder holds one page per municipality, with the vote for the mayor of the municipality and the lists that won
seats on the municipal council; one page per mayoralty (`kmetstvo`) and per city district (`rayon`), with the vote for
its mayor; the municipal electoral commission's decisions; and the allocation of council seats by the Hare-Niemeyer
method, which lists the votes of every list and independent council candidate. The section protocols the pages link to
are not in the zips, so the results stand at the level each contest was decided: the municipality, the mayoralty, or
the city district.

The zips also hold pages no menu links to: a test municipality and older versions of mayoralty pages, numbered by the
commission's own register instead of the EKATTE code, that name other candidates and votes. Only the pages that the
site's menus reach, starting from each province's index, are read.
"""

import functools
import html
import re
import zipfile
from collections import defaultdict
from datetime import date
from typing import NamedTuple

import httpx
import polars as pl

from ea import changeset, raw
from ea.package import Package
from ea.sources.bg import cik_local
from ea.sources.bg.cik_local import (
    BULGARIA,
    COUNCIL,
    DISTRICT,
    MAYOR,
    SOURCE,
    VILLAGE,
    VOTES,
    council_body,
    key,
    kmetstvo,
    latin,
    mayor_office,
    norm,
    obshtina,
    party_kind,
)

BASE = "https://mi2007.cik.bg"
SITE = key("source", "cik-2007")
SYSTEM_COUNCIL = key("electoral_system", "council", 2007)
SYSTEM_MAYOR = key("electoral_system", "mayor", 2007)


class Round(NamedTuple):
    id: str
    day: date
    round: int
    url: str


ROUNDS = [
    Round("mi-2007-10-28", date(2007, 10, 28), 1, f"{BASE}/results_1.zip"),
    Round("mi-2007-11-04", date(2007, 11, 4), 2, f"{BASE}/results_2.zip"),
]

METHOD = (
    "From the results site of the 2007 local elections, which the Central Election Commission (CIK) publishes as one "
    "zip of HTML pages per round. The pages give, for each municipality, mayoralty, and city district, the valid votes "
    "of every candidate for mayor, and for each municipal council the valid votes of every list and independent "
    "candidate and the seats they won by the Hare-Niemeyer method; the section protocols are not in the zips, so each "
    "contest is stored for its whole area. The pages do not give registered voters, voters, or invalid ballots. "
    "Council lists that won no seat carry no ballot number where no other ballot of the same municipality names them."
)

# The 2007 law gave council seats only to lists and independent candidates with at least the municipal quota (valid
# votes divided by seats) and divided them by the Hare-Niemeyer method; the rules schema has no quota threshold, so the
# rules below hold the formula and the note holds the threshold.
SYSTEMS: list[tuple[str, dict[str, object], str]] = [
    (
        SYSTEM_COUNCIL,
        {"formula": "hare", "lists": {"order": "flexible", "vacancy": "next_on_list"}},
        (
            "Municipal councils were elected from lists in one constituency per municipality under the Local "
            "Elections Act as it stood in 2007. Only independent candidates and lists whose valid votes reached the "
            "municipal quota, the valid votes divided by the seats, took part in the allocation. An independent "
            "candidate at or above the quota took one seat, and the remaining seats were divided among the lists by "
            "the Hare-Niemeyer method of largest remainders."
        ),
    ),
    (
        SYSTEM_MAYOR,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A mayor, of a municipality, a city district, or a mayoralty, was elected directly. A candidate with more "
            "than half of the valid votes won in the first round; otherwise the two leading candidates went to a "
            "second round a week later."
        ),
    ),
]


# --- reading the pages ---------------------------------------------------------------------------------------------


def fetch() -> None:
    for r in ROUNDS:
        try:
            raw.fetch(SOURCE, r.url, suffix=".zip")
        except httpx.HTTPError as failure:
            print(f"{r.url}: {failure}", flush=True)


@functools.cache
def _archive(url: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(raw.cached(SOURCE, url).path)


def _page(url: str, name: str) -> str:
    return _archive(url).read(name).decode("utf-8")


def text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def integer(fragment: str) -> int:
    value = text(fragment).replace(" ", "")
    if not value.isdigit():
        raise ValueError(f"not a count: {fragment!r}")
    return int(value)


class Runner(NamedTuple):
    number: int
    name: str
    party: str
    votes: int
    percent: str
    marked: bool  # the row is marked as elected, or in a first round as going on to the second


class Seat(NamedTuple):
    number: int
    name: str
    members: tuple[str, ...]
    votes: int
    seats: int


class Share(NamedTuple):
    name: str
    votes: int
    independent: bool
    seats: int


class Page(NamedTuple):
    cls: str  # MAYOR for a municipality's page, DISTRICT, or VILLAGE
    name: str  # the file name inside the zip
    municipality: str  # the CIK code of the municipality, province and municipality numbers
    unit: str  # the municipality code, the district's CIK unit, or the mayoralty's EKATTE code
    place: str  # the municipality, district, or mayoralty as the breadcrumbs name it
    status: str
    mayor: tuple[Runner, ...]
    council: tuple[Seat, ...]


def _rows(table: str) -> list[tuple[str, list[str]]]:
    out = []
    for attrs, body in re.findall(r"<tr([^>]*)>(.*?)</tr>", table, re.DOTALL):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", body, re.DOTALL)
        if cells:
            out.append((attrs, cells))
    return out


def _tables(page: str) -> dict[str, str]:
    content = page.partition('<div id="content">')[2]
    return {
        text(caption): body
        for caption, body in re.findall(r"<caption>(.*?)</caption>(.*?)</table>", content, re.DOTALL)
    }


def _runners(table: str) -> tuple[Runner, ...]:
    out = []
    for attrs, cells in _rows(table):
        name, _, party = cells[1].partition('<span class="party">')
        out.append(
            Runner(int(text(cells[0])), text(name), text(party), integer(cells[2]), text(cells[3]), "elected" in attrs)
        )
    return tuple(out)


def _seats(table: str) -> tuple[Seat, ...]:
    out = []
    for _, cells in _rows(table):
        head = re.search(r"<p[^>]*>\s*<span[^>]*>\s*(\d+)\.(?:&nbsp;|\s)*</span>(.*?)</p>", cells[0], re.DOTALL)
        if head is None:
            raise ValueError(f"council row without a list number: {cells[0][:200]!r}")
        members = tuple(text(m) for m in re.findall(r"<li>(.*?)</li>", cells[0], re.DOTALL))
        out.append(Seat(int(head.group(1)), text(head.group(2)), members, integer(cells[1]), integer(cells[3])))
    return tuple(out)


def _links(page: str) -> list[str]:
    return re.findall(r"<li><a href='\./([^']+\.html)'>", page)


def _identify(name: str) -> tuple[str, str, str]:
    """(class, municipality code, unit) from a page's file name."""
    stem = name.rpartition("/")[2].removesuffix(".html")
    if re.fullmatch(r"\d{4}", stem):
        return MAYOR, stem, stem
    if match := re.fullmatch(r"r(\d\d)(\d\d)(\d\d)", stem):
        return DISTRICT, match.group(1) + match.group(2), str(int(match.group(1)) * 100 + int(match.group(3)))
    if match := re.fullmatch(r"(\d{1,2})(\d\d)(\d{5})", stem):
        return VILLAGE, f"{int(match.group(1)):02d}{match.group(2)}", match.group(3)
    raise ValueError(f"unknown page {name}")


@functools.cache
def pages(r: Round) -> tuple[Page, ...]:
    """Every results page the site's menus reach from the province indexes, in province and file order."""
    archive = _archive(r.url)
    names = {i.filename for i in archive.infolist()}
    out: list[Page] = []
    for province in range(1, 29):
        folder = f"{province:02d}/"
        index = folder + "index.html"
        # Sofia's index lists no municipality, and two provinces have no index in the second round, so every
        # municipality page but the test one (numbered 99) seeds the walk.
        seeds = [
            n for n in names if re.fullmatch(rf"{folder}{province:02d}\d\d\.html", n) and not n.endswith("99.html")
        ]
        seen: set[str] = set(seeds)
        queue = [*([index] if index in names else []), *seeds]
        while queue:
            current = queue.pop()
            for link in _links(_page(r.url, current)):
                target = folder + link
                if target not in seen and target in names and link != "index.html":
                    seen.add(target)
                    queue.append(target)
        for name in sorted(seen):
            page = _page(r.url, name)
            cls, municipality, unit = _identify(name)
            crumbs = re.search(r'<ol id="breadcrumbs">(.*?)</ol>', page, re.DOTALL)
            place = text(re.findall(r"<li>(.*?)</li>", crumbs.group(1), re.DOTALL)[-1]) if crumbs else ""
            heads = re.findall(r"<h3>(.*?)</h3>", page.partition('<div id="content">')[2], re.DOTALL)
            tables = _tables(page)
            caption = {MAYOR: "Кмет на община", DISTRICT: "Кмет на район", VILLAGE: "Кмет на кметство"}[cls]
            mayor = _runners(tables[caption]) if caption in tables else ()
            council = _seats(tables["Общински съвет"]) if "Общински съвет" in tables else ()
            out.append(Page(cls, name, municipality, unit, place, text(heads[0]) if heads else "", mayor, council))
    return tuple(out)


@functools.cache
def shares(r: Round, municipality: str) -> tuple[int, tuple[Share, ...]]:
    """The council's seats and the votes and seats of every list and independent candidate, from the Hare-Niemeyer
    page."""
    name = f"{municipality[:2]}/{municipality}hnm.html"
    page = _page(r.url, name)
    size = re.search(r"Мандати за разпределяне:\s*(\d+)", page)
    if size is None:
        raise ValueError(f"{name}: no council size")
    total = int(size.group(1))
    body = page.partition("<tbody>")[2].partition("</tbody>")[0]
    out = []
    rows = _rows(body)
    for (_, cells), (_, kind) in zip(rows[::2], rows[1::2], strict=True):
        independent = text(kind[0]) == "независим кандидат"
        won = text(cells[9]) if len(cells) > 9 else ""
        if independent:
            seats = 1 if text(cells[3]) not in ("", "-", "0") else 0
        else:
            seats = int(won.partition("=")[0]) if won and won != "-" else 0
        out.append(Share(text(cells[1]), integer(cells[2]), independent, seats))
    stated = re.search(r"Сумата на действителните гласове е (\d+)", page)
    if stated is not None and int(stated.group(1)) != sum(s.votes for s in out):
        raise ValueError(f"{name}: the votes add up to {sum(s.votes for s in out)}, the page says {stated.group(1)}")
    return total, tuple(out)


# --- building the packages -----------------------------------------------------------------------------------------

# Mayoralties of 2007 that the EKATTE register, from which `bg.cik_local/geo` builds its mayoralties, no longer lists:
# they were merged or abolished after 2007. The first round's package adds them, with their mayors' offices.
GONE = frozenset(
    [
        "00456",
        "02573",
        "02782",
        "03424",
        "03900",
        "05240",
        "05668",
        "06241",
        "07185",
        "10536",
        "10985",
        "11960",
        "15059",
        "16081",
        "16242",
        "16382",
        "16835",
        "20420",
        "20630",
        "23185",
        "27574",
        "29074",
        "29088",
        "29252",
        "29427",
        "32929",
        "39462",
        "52060",
        "58949",
        "65588",
        "72080",
        "72299",
        "72882",
        "86116",
    ]
)
SCHEME = {MAYOR: "bg.obshtina", COUNCIL: "bg.obshtina", DISTRICT: "bg.rayon", VILLAGE: "bg.kmetstvo"}
PLACE = re.compile(r"^(община|район|кметство)\s+", re.IGNORECASE)
INITIATIVE = re.compile(r"^инициативен\s+комитет\b", re.IGNORECASE)


# The EKATTE codes of the three cities divided into districts: Varna, Plovdiv, and Sofia.
CITIES = {"0306": "10135", "1622": "56784", "2246": "68134"}


def rayon_code(unit: str, municipality: str) -> str:
    """The register's code of a city district, such as `10135-01`, from its CIK unit and municipality."""
    return f"{CITIES[municipality]}-{int(unit) % 100:02d}"


def area_of(page: Page) -> str:
    if page.cls == MAYOR:
        return obshtina(page.municipality)
    if page.cls == DISTRICT:
        return cik_local.rayon(rayon_code(page.unit, page.municipality))
    return kmetstvo(page.unit)


def office_of(page: Page) -> str:
    return mayor_office(page.cls, page.unit)


def native(page: Page) -> str:
    name = PLACE.sub("", page.place).strip()
    return "Столична" if page.unit == "2246" and page.cls == MAYOR else name


def contest_names(cls: str, page: Page, municipality: str, second: bool) -> tuple[str, str]:
    place, city = native(page), municipality
    if cls == COUNCIL:
        en, bg = f"{latin(place)} municipal council", f"Общински съвет – {place}"
    elif cls == MAYOR:
        en, bg = f"Mayor of {latin(place)}", f"Кмет на община {place}"
    elif cls == DISTRICT:
        en, bg = f"Mayor of {latin(place)} district, {latin(city)}", f"Кмет на район {place}, {city}"
    else:
        en, bg = f"Mayor of {latin(place)}, {latin(city)}", f"Кмет на кметство {place}, община {city}"
    return (f"{en} (second round)", f"{bg} (втори тур)") if second else (en, bg)


def contest_slug(cls: str, page: Page) -> str:
    code = page.unit.zfill(5) if cls == VILLAGE else page.unit
    return f"{cik_local.CLASS_PATH[cls]}/{code}-{changeset.slug(latin(native(page)))}"


def runoff_of(r: Round) -> Round | None:
    return next((x for x in ROUNDS if x.round == 2), None) if r.round == 1 else None


def first_of(r: Round) -> Round | None:
    return next((x for x in ROUNDS if x.round == 1), None) if r.round == 2 else None


def _registry(p: Package) -> None:
    """The source, the electoral systems, and the mayoralties the register no longer lists, in the first round."""
    p.add(
        "source",
        id=SITE,
        kind="official_body",
        publisher="Централна избирателна комисия",
        title="Results of the 2007 local elections (Местни избори 2007)",
        url=f"{BASE}/",
        license="Published for re-use as public sector information; the CIK states no further license terms",
        license_url="https://results.cik.bg/",
        attribution="Централна избирателна комисия (Central Election Commission of Bulgaria)",
    )
    for id, rules, method in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=BULGARIA, valid_from=date(2007, 1, 1), rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    gone: dict[str, Page] = {}
    for page in pages(ROUNDS[0]):
        if page.cls == VILLAGE and page.unit in GONE:
            gone.setdefault(page.unit, page)
    if set(gone) != GONE:
        raise ValueError(f"mayoralties without a first-round page: {sorted(GONE - set(gone))}")
    municipalities = {page.municipality: native(page) for page in pages(ROUNDS[0]) if page.cls == MAYOR}
    for ekatte, page in sorted(gone.items()):
        id, place, city = kmetstvo(ekatte), native(page), municipalities[page.municipality]
        p.add("area", id=id, scheme_code="bg.kmetstvo")
        p.add("area_code", area_id=id, code=ekatte)
        p.add("area_parent", area_id=id, parent_id=obshtina(page.municipality))
        p.external.add(obshtina(page.municipality))
        p.name(id, latin(place), "en", "transliteration")
        p.name(id, place, "bg", "native", False)
        office = mayor_office(VILLAGE, ekatte)
        p.add("office", id=office, polity_id=BULGARIA, kind="mayor")
        p.name(office, f"Mayor of {latin(place)}, {latin(city)}", "en", "official")
        p.name(office, f"Кмет на кметство {place}, община {city}", "bg", "native", False)


def build(r: Round) -> Package:
    first, later = first_of(r), runoff_of(r)
    p = Package(
        f"{SOURCE}/{r.id}",
        SOURCE,
        f"The {r.day:%d %B %Y} Bulgarian local elections{' (second round)' if r.round == 2 else ''} by municipality, "
        "city district, and mayoralty, from the CIK's results site for the 2007 local elections",
        depends=[f"{SOURCE}/{first.id}"] if first is not None else [],
    )
    p.external.update([BULGARIA, *sorted(set(SCHEME.values()))])
    p.artifact(raw.cached(SOURCE, r.url), SITE)
    if first is None:
        _registry(p)
    election = key("election", r.id)
    spoken = f"{r.day.day} {r.day:%B} {r.day.year}"
    p.add(
        "election",
        id=election,
        polity_id=BULGARIA,
        kind="runoff" if r.round == 2 else "local",
        date_from=r.day,
        date_to=r.day,
        status="held",
    )
    if r.round == 2:
        p.name(election, f"Bulgarian local elections, {spoken}, second round", "en", "official")
        p.name(election, f"Местни избори, {r.day:%d.%m.%Y}, втори тур", "bg", "native", False)
    else:
        p.name(election, f"Bulgarian local elections, {spoken}", "en", "official")
        p.name(election, f"Местни избори, {r.day:%d.%m.%Y}", "bg", "native", False)
    path = f"bg/{r.day}-local" + ("-runoff" if r.round == 2 else "")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add("note", id=key("note", election), entity_id=election, kind="methodology", body_md=METHOD, lang="en")

    here = pages(r)
    municipalities = {page.municipality: native(page) for page in pages(ROUNDS[0]) if page.cls == MAYOR}
    earlier = {(page.cls, page.unit) for page in pages(first)} if first is not None else set()
    runoffs = {(page.cls, page.unit) for page in pages(later) if page.mayor} if later is not None else set()
    parties: dict[str, str] = {}
    units: dict[str, str] = {}
    values: dict[str, list[object]] = {c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "value")}
    ties: list[str] = []
    differ: set[str] = set()  # municipalities whose declared seats differ from the allocation page
    short: set[str] = set()
    unnamed: set[str] = set()  # municipalities whose declared seats add up to fewer or more than the council's size

    def party_id(name: str, municipality: str) -> str:
        local = bool(INITIATIVE.match(name)) or cik_local.LOCAL_LIST.match(name) is not None
        tag = f"{municipality}:{norm(name)}" if local else norm(name)
        if tag not in parties:
            parties[tag] = p.id("party", r.id, tag)
            p.add("party", id=parties[tag], kind=party_kind(name), polity_id=BULGARIA)
            p.name(parties[tag], name, "bg", "official")
        return parties[tag]

    def unit_of(page: Page) -> str:
        own = rayon_code(page.unit, page.municipality) if page.cls == DISTRICT else page.unit
        code = f"{r.id}:{own}"
        if code not in units:
            units[code] = key("unit", r.id, SCHEME[page.cls], page.unit)
            p.add(
                "reporting_unit",
                id=units[code],
                polity_id=BULGARIA,
                scheme_code=SCHEME[page.cls],
                code=code,
                area_relation="equals",
                area_id=area_of(page),
                kind="standard",
            )
            if page.cls != VILLAGE or page.unit not in GONE:
                p.external.add(area_of(page))
        return units[code]

    def put(rs: str, unit: str, option: str, votes: int) -> None:
        values["result_set_id"].append(rs)
        values["unit_id"].append(unit)
        values["option_id"].append(option)
        values["measure"].append(VOTES)
        values["value"].append(votes)

    def contest(cls: str, page: Page, seats: int | None = None) -> tuple[str, str]:
        id = key("contest", r.id, cls, page.unit)
        rs = key("result_set", r.id, cls, page.unit)
        council = cls == COUNCIL
        city = municipalities[page.municipality]
        previous = (
            key("contest", first.id, cls, page.unit) if first is not None and (cls, page.unit) in earlier else None
        )
        body = council_body(page.municipality) if council else None
        office = None if council else office_of(page)
        p.add(
            "contest",
            id=id,
            election_id=election,
            kind="list_allocation" if council else "office",
            office_id=office,
            body_id=body,
            district_area_id=area_of(page),
            magnitude=seats if council else 1,
            round_number=r.round,
            previous_round_id=previous,
            electoral_system_id=SYSTEM_COUNCIL if council else SYSTEM_MAYOR,
            default_result_kind="counted",
        )
        for ref in (body, office):
            if ref is not None and not (cls == VILLAGE and page.unit in GONE):
                p.external.add(ref)
        en, bg = contest_names(cls, page, city, r.round == 2)
        p.name(id, en, "en", "official")
        p.name(id, bg, "bg", "native", False)
        p.add("slug", path=f"{path}/{contest_slug(cls, page)}", entity_id=id, is_current=True)
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=SITE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        return id, rs

    numbers: dict[str, dict[str, int]] = defaultdict(dict)  # municipality -> party name -> ballot number
    for page in here:
        for runner in page.mayor:
            if runner.party and not INITIATIVE.match(runner.party):
                numbers[page.municipality].setdefault(norm(runner.party), runner.number)
        for seat in page.council:
            if not INITIATIVE.match(seat.name):
                numbers[page.municipality][norm(seat.name)] = seat.number

    for page in here:
        if page.mayor:
            id, rs = contest(page.cls, page)
            unit = unit_of(page)
            total = sum(x.votes for x in page.mayor)
            marked = [x for x in page.mayor if x.marked]
            if r.round == 1:
                outright = len(marked) == 1 and 2 * marked[0].votes > total
                if not outright and len(marked) > 2:
                    ties.append(page.name)
            for x in page.mayor:
                party = x.party
                if not party or INITIATIVE.match(party) and norm(party) == norm("Инициативен комитет"):
                    party = f"Инициативен комитет {x.name}"
                independent = bool(INITIATIVE.match(party))
                option = key("option", r.id, page.cls, page.unit, x.number)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=id,
                    kind="candidate",
                    party_id=party_id(party, page.municipality),
                    ballot_position=x.number,
                    ballot_label=x.name,
                )
                person = p.id("person", r.id, page.cls, page.unit, x.number)
                candidacy = p.id("candidacy", r.id, page.cls, page.unit, x.number)
                p.add("person", id=person)
                p.name(person, x.name, "bg", "official")
                run: dict[str, object] = {
                    "id": candidacy,
                    "person_id": person,
                    "option_id": option,
                    "role": "head",
                    "ballot_name": x.name,
                }
                if independent:
                    run["is_independent"] = True
                p.add("candidacy", **run)
                put(rs, unit, option, x.votes)
                if r.round == 1 and marked:
                    if outright:
                        kind = "elected" if x.marked else "lost"
                    else:
                        kind = "advanced" if x.marked else "eliminated"
                    p.add("outcome", result_set_id=rs, option_id=option, kind=kind)
                elif r.round == 2 and len(marked) == 1:
                    p.add("outcome", result_set_id=rs, option_id=option, kind="elected" if x.marked else "lost")
            undecided = r.round == 1 and marked and not (len(marked) == 1 and 2 * marked[0].votes > total)
            if undecided and (page.cls, page.unit) not in runoffs:
                raise ValueError(f"{page.name}: no one elected and no second round")
        if page.cls == MAYOR and r.round == 1:
            seats, shares_ = shares(r, page.municipality)
            id, rs = contest(COUNCIL, page, seats)
            unit = unit_of(page)
            by_name = {norm(s.name): s for s in page.council}
            declared = 0
            for order, share in enumerate(shares_, 1):
                name = norm(share.name)
                seat = (
                    next(
                        (
                            s
                            for s in page.council
                            if len(s.members) == 1
                            and s.votes == share.votes
                            and norm(" ".join(s.members).rstrip(".")) in norm(share.name)
                        ),
                        None,
                    )
                    if share.independent
                    else by_name.get(name)
                    or next(
                        (
                            s
                            for s in page.council
                            if re.match(re.escape(name) + r"\s*:", norm(s.name)) and s.votes == share.votes
                        ),
                        None,
                    )
                )
                if seat is not None and seat.votes != share.votes:
                    raise ValueError(f"{page.name}: {share.name} has {seat.votes} votes on the results page, "
                                     f"{share.votes} in the allocation")  # fmt: skip
                # The results page carries the commission's decision; where it differs from the allocation page, the
                # decision's seats are declared.
                won = seat.seats if seat is not None else 0
                if won != share.seats:
                    differ.add(native(page))
                number = seat.number if seat is not None else numbers[page.municipality].get(name)
                label = seat.name if seat is not None and not share.independent else share.name
                option = key("option", r.id, COUNCIL, page.unit, f"order-{order}")
                row: dict[str, object] = {
                    "id": option,
                    "contest_id": id,
                    "kind": "list",
                    "party_id": party_id(label, page.municipality),
                    "ballot_label": label,
                }
                if number is not None:
                    row["ballot_position"] = number
                p.add("contest_option", **row)
                put(rs, unit, option, share.votes)
                members: tuple[str, ...] = seat.members if seat is not None else ()
                if share.independent:
                    # an independent who won no seat is named only inside the committee's name, so has no candidacy
                    members = (seat.members[0].rstrip("."),) if seat is not None else ()
                if won:
                    p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=won)
                for position, member in enumerate(members, 1):
                    person = p.id("person", r.id, COUNCIL, page.unit, order, position)
                    candidacy = p.id("candidacy", r.id, COUNCIL, page.unit, order, position)
                    p.add("person", id=person)
                    p.name(person, member, "bg", "official")
                    run = {"id": candidacy, "person_id": person, "option_id": option, "role": "list_member",
                           "ballot_name": member}  # fmt: skip
                    if share.independent:
                        run["is_independent"] = True
                    p.add("candidacy", **run)
                    if won and (not share.independent or position == 1):
                        p.add("outcome", result_set_id=rs, option_id=option, candidacy_id=candidacy, kind="elected")
                if won and len(members) != won:
                    unnamed.add(native(page))  # a list that won more seats than the page names members for
                declared += won
            if sum(s.seats for s in page.council) != declared:
                raise ValueError(f"{page.name}: a list on the results page has no line in the allocation")
            if declared != seats:
                short.add(native(page))

    frame = cik_local.values_frame(values).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
    )
    p.frame("result_value", frame)
    if unnamed:
        p.accept(
            "outcomes.list_members",
            f"the results pages of the councils of {', '.join(sorted(unnamed))} name fewer "
            "elected councillors for a list than the seats the page gives it; the seats and the named councillors "
            "are stored as printed",
        )
    if short:
        p.accept(
            "outcomes.seats_magnitude",
            f"in the councils of {', '.join(sorted(short))} the seats the results page declares add "
            "up to another number than the seats the allocation page divides; stored as printed",
        )
    if differ:
        p.add(
            "note",
            id=key("note", election, "seats"),
            entity_id=election,
            kind="caveat",
            body_md=(
                f"In {', '.join(sorted(differ))} the seats on the results page, which "
                "follow the municipal electoral commission's decision, differ from the Hare-Niemeyer allocation page "
                "of the same site. The declared seats are those of the results page; the votes of every list are "
                "those of the allocation page, which lists the lists that won no seat as well."
            ),
            lang="en",
        )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/{r.id}" for r in ROUNDS]


def parse() -> None:
    for r in ROUNDS:
        build(r).write()
