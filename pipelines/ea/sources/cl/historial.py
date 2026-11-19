"""Chile's elections of 1989-2010 by commune, from the Interior Ministry's historical results site (Servel)."""

import html
import re
import unicodedata
from collections import defaultdict
from datetime import date
from functools import cache
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.cl import servel

SOURCE = "cl.historial"
SITE = "https://historial.servel.cl/SitioHistorico"
PAGES = f"{SITE}/paginas"
CHILE = "01016eed-79a5-5393-a8a1-e2f5adb6103c"

# The site lists the communes of each senatorial constituency in a table; the union over these listings is every
# commune that existed at any election from 1989 to 2010.
LISTINGS = (
    (1989, "diputados"),
    (1992, "municipales"),
    (2000, "municipales"),
    (2005, "diputados"),
    (2008, "concejales"),
    (2009, "diputados"),
)
CIRCS = range(201, 220)
DISTRICTS = range(401, 461)


LISTED = ("deputies", "senators", "council")


class Part(NamedTuple):
    kind: str  # president, deputies, senators, council, mayor
    year: int  # the year folder, that of the first round
    folder: str


class Election(NamedTuple):
    day: date
    kind: str
    word: str
    en: str
    es: str
    parts: tuple[Part, ...]
    runoff_of: date | None = None


def _general(day: date, year: int, president: bool, deputies: bool = True, senators: bool = True) -> Election:
    parts = []
    if president:
        parts.append(Part("president", year, "presidencial1v"))
    if deputies:
        parts.append(Part("deputies", year, "diputados"))
    if senators:
        parts.append(Part("senators", year, "senadores"))
    if president:
        en, es = f"{year} Chilean general election", f"Elecciones presidencial y parlamentarias {year}"
    else:
        en, es = f"{year} Chilean parliamentary election", f"Elecciones parlamentarias {year}"
    return Election(day, "general", "general", en, es, tuple(parts))


def _runoff(day: date, first: date) -> Election:
    year = first.year
    return Election(
        day,
        "runoff",
        "presidential-runoff",
        f"{year} Chilean presidential election, second round",
        f"Segunda votación presidencial {year}",
        (Part("president", year, "presidencial2v"),),
        first,
    )


def _municipal(day: date, folder: str, mayor: bool = False) -> Election:
    parts = [Part("council", day.year, folder)]
    if mayor:
        parts.append(Part("mayor", day.year, "alcaldes"))
    return Election(
        day,
        "local",
        "municipal",
        f"{day.year} Chilean municipal elections",
        f"Elecciones municipales {day.year}",
        tuple(parts),
    )


ELECTIONS = (
    _general(date(1989, 12, 14), 1989, True),
    _municipal(date(1992, 6, 28), "municipales"),
    _general(date(1993, 12, 11), 1993, True),
    _municipal(date(1996, 10, 27), "municipales"),
    _general(date(1997, 12, 11), 1997, False),
    Election(
        date(1999, 12, 12),
        "general",
        "presidential",
        "1999 Chilean presidential election",
        "Elección presidencial 1999",
        (Part("president", 1999, "presidencial1v"),),
    ),
    _runoff(date(2000, 1, 16), date(1999, 12, 12)),
    _municipal(date(2000, 10, 29), "municipales"),
    _general(date(2001, 12, 16), 2001, False),
    _municipal(date(2004, 10, 31), "concejales", True),
    _general(date(2005, 12, 11), 2005, True),
    _runoff(date(2006, 1, 15), date(2005, 12, 11)),
    _municipal(date(2008, 10, 26), "concejales", True),
    _general(date(2009, 12, 13), 2009, True),
    _runoff(date(2010, 1, 17), date(2009, 12, 13)),
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
PRESIDENCY = key("office", "president")
CHAMBER = key("body", "chamber")
SENATE = key("body", "senate")


def fold(text: object) -> str:
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", plain).upper().split())


# ---- the pages -------------------------------------------------------------------------------------------------


def listing_url(year: int, folder: str, circ: int) -> str:
    return f"{PAGES}/{year}/{folder}/circunscripciones/pactos/total/dcomuna{circ}.htm"


def comuna_url(part: Part, code: str) -> str:
    return f"{PAGES}/{part.year}/{part.folder}/comunas/candidatos/total/{code}.htm"


def pact_url(part: Part, code: str) -> str:
    return f"{PAGES}/{part.year}/{part.folder}/comunas/pactos/total/{code}.htm"


def district_url(part: Part, code: int) -> str:
    place = "distritos" if part.kind == "deputies" else "circunscripciones"
    return f"{PAGES}/{part.year}/{part.folder}/{place}/candidatos/total/{code}.htm"


def national_url(part: Part) -> str:
    agrupacion = "candidatos" if part.kind == "president" else "pactos"
    return f"{PAGES}/{part.year}/{part.folder}/pais/{agrupacion}/total/1.htm"


def text(cell: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", cell)).replace("\xa0", " ").split())


def number(cell: str) -> int:
    digits = re.sub(r"\D", "", cell)
    return int(digits) if digits else 0


def table(content: bytes, first: str = "NOMBRE") -> list[list[str]]:
    """The rows of the first table whose header names `first` and VOTOS, as cell texts."""
    page = content.decode("latin-1")
    for block in re.findall(r"<table.*?</table>", page, flags=re.IGNORECASE | re.DOTALL):
        rows = []
        for row in re.findall(r"<tr>(.*?)</tr>", block, flags=re.IGNORECASE | re.DOTALL):
            cells = [
                text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)
            ]
            if cells:
                rows.append(cells)
        if rows and {first, "VOTOS"} <= {fold(c) for c in rows[0]}:
            return rows
    return []


class Pact(NamedTuple):
    letter: str | None  # None for the independents outside any pact
    label: str
    votes: int
    candidates: int
    elected: int


class PactPage(NamedTuple):
    pacts: list[Pact]
    valid: int | None
    candidates: int | None
    elected: int | None


PACT_LABEL = re.compile(r"^([A-Z]{1,2})\s*\.\s*-\s*(.+)$")


def read_pacts(content: bytes) -> PactPage | None:
    """The lists or pacts of a commune with their votes, candidates, and elected, and the totals row."""
    rows = table(content, "LISTA PACTO")
    if not rows:
        return None
    pacts: list[Pact] = []
    valid = candidates = elected = None
    for cells in rows[1:]:
        if len(cells) < 5:
            continue
        label = " ".join(cells[0].split())
        counts = (number(cells[1]), number(cells[3]), number(cells[4]))
        if fold(label).startswith("VALIDAMENTE"):
            valid, candidates, elected = counts
            continue
        found = PACT_LABEL.match(label)
        if found:
            pacts.append(Pact(found.group(1), found.group(2).strip(), *counts))
        elif fold(label).startswith("INDEPENDIENTES"):
            pacts.append(Pact(None, "Independientes (Fuera de Pacto)", *counts))
        elif label:
            raise ValueError(f"unrecognized list row {label!r}")
    return PactPage(pacts, valid, candidates, elected)


class Candidate(NamedTuple):
    name: str
    party: str
    votes: int
    cargo: str


class Page(NamedTuple):
    candidates: list[Candidate]
    valid: int | None
    blank: int | None
    invalid: int | None


def read(content: bytes) -> Page | None:
    """Candidates, parties, votes, and the post won, from a page of candidate votes; None for a page without any."""
    rows = table(content)
    if not rows:
        return None
    header = [fold(c) for c in rows[0]]
    if "NOMBRE" not in header or "VOTOS" not in header:
        return None
    at = {name: header.index(name) for name in ("NOMBRE", "PARTIDO", "VOTOS", "CARGO") if name in header}
    candidates: list[Candidate] = []
    valid = blank = invalid = None
    for cells in rows[1:]:
        label = fold(cells[at["NOMBRE"]])
        votes = number(cells[at["VOTOS"]]) if at["VOTOS"] < len(cells) else 0
        if label.startswith("VALIDAMENTE"):
            valid = votes
        elif label in ("NULOS", "VOTOS NULOS", "VOTO NULO"):
            invalid = votes
        elif label in ("BLANCOS", "VOTOS EN BLANCO", "VOTO EN BLANCO"):
            blank = votes
        elif label:
            party = cells[at["PARTIDO"]] if "PARTIDO" in at and at["PARTIDO"] < len(cells) else ""
            cargo = cells[at["CARGO"]] if "CARGO" in at and at["CARGO"] < len(cells) else ""
            candidates.append(Candidate(" ".join(cells[at["NOMBRE"]].split()), party, votes, cargo))
    return Page(candidates, valid, blank, invalid)


# ---- fetching --------------------------------------------------------------------------------------------------


def listings() -> list[str]:
    return [listing_url(year, folder, circ) for year, folder in LISTINGS for circ in CIRCS]


@cache
def communes() -> dict[str, str]:
    """Page code to commune name, from the senatorial constituency listings."""
    found: dict[str, str] = {}
    for artifact in raw.fetch_many(SOURCE, listings(), suffix=".htm").values():
        page = artifact.path.read_bytes().decode("latin-1")
        for code, name in re.findall(r"bar(\d{4})\.gif','Fila: ([^']+)'", page):
            found.setdefault(code, text(name))
    return dict(sorted(found.items()))


def urls() -> list[str]:
    found = list(listings())
    for election in ELECTIONS:
        for part in election.parts:
            found.append(national_url(part))
            if part.kind in ("deputies", "senators"):
                found.extend(district_url(part, code) for code in (DISTRICTS if part.kind == "deputies" else CIRCS))
    return found


def fetch() -> None:
    raw.fetch_many(SOURCE, listings(), suffix=".htm")
    raw.fetch_many(SOURCE, urls(), suffix=".htm")
    for election in ELECTIONS:
        for part in election.parts:
            raw.fetch_many(SOURCE, [comuna_url(part, code) for code in communes()], suffix=".htm")
            if part.kind in LISTED:
                raw.fetch_many(SOURCE, [pact_url(part, code) for code in communes()], suffix=".htm")


# ---- reading the cache -----------------------------------------------------------------------------------------


@cache
def _cached() -> frozenset[str]:
    return frozenset(raw.keys(SOURCE))


@cache
def load(url: str) -> Page | None:
    if url not in _cached():
        return None
    return read(raw.cached(SOURCE, url).path.read_bytes())


@cache
def load_pacts(url: str) -> PactPage | None:
    if url not in _cached():
        return None
    return read_pacts(raw.cached(SOURCE, url).path.read_bytes())


class Contest(NamedTuple):
    code: str
    title: str
    kind: str  # the contest kind of the archive
    district: str | None
    office: str | None
    body: str | None
    magnitude: int | None
    units: dict[str, Page]
    official: Page | None
    urls: list[str]


def comuna_name(code: str) -> str:
    return communes()[code]


def party(sigla: str) -> str:
    return key("party", fold(sigla))


INDEPENDENT = re.compile(r"^(IND|IL[A-Z]|S ?P|)$")


def contests(election: Election, part: Part) -> list[Contest]:
    pages = {code: load(comuna_url(part, code)) for code in communes()}
    # A page that lists 50 candidates and no valid-votes row has been cut off by the site (Viña del Mar and Maipú in
    # 2008), so its commune is left out rather than published short.
    pages = {
        code: page for code, page in pages.items() if page is not None and page.candidates and page.valid is not None
    }
    used = [comuna_url(part, code) for code in pages]
    if part.kind == "president":
        official = load(national_url(part))
        return [
            Contest(
                "",
                "Presidente de la República",
                "office",
                None,
                PRESIDENCY,
                None,
                1,
                pages,
                official,
                [*used, national_url(part)],
            )
        ]
    if part.kind in ("council", "mayor"):
        out = []
        for code, page in pages.items():
            name = comuna_name(code)
            if part.kind == "council":
                out.append(
                    Contest(
                        code,
                        f"Concejo Municipal de {name}",
                        "body_seats",
                        None,
                        None,
                        key("body", "council", code),
                        None,
                        {code: page},
                        page,
                        [comuna_url(part, code)],
                    )
                )
            else:
                out.append(
                    Contest(
                        code,
                        f"Alcalde de {name}",
                        "office",
                        None,
                        key("office", "mayor", code),
                        None,
                        1,
                        {code: page},
                        page,
                        [comuna_url(part, code)],
                    )
                )
        return out
    # Deputies and senators: the commune pages carry no district, so each goes to the district whose candidates it lists.
    deputies = part.kind == "deputies"
    seats: dict[int, Page] = {}
    for number in DISTRICTS if deputies else CIRCS:
        page = load(district_url(part, number))
        if page is not None and page.candidates:
            seats[number] = page
    sets = {number: {fold(c.name) for c in page.candidates} for number, page in seats.items()}
    grouped: dict[int, dict[str, Page]] = defaultdict(dict)
    for code, page in pages.items():
        names = {fold(c.name) for c in page.candidates}
        fits = [number for number, found in sets.items() if names <= found]
        if not fits:
            raise ValueError(f"{election.day} {part.kind}: commune {code} fits no district")
        best = max(fits, key=lambda number: len(names & sets[number]))
        if len(fits) > 1 and len(names & sets[best]) == max(len(names & sets[n]) for n in fits if n != best):
            raise ValueError(f"{election.day} {part.kind}: commune {code} fits districts {fits}")
        grouped[best][code] = page
    out = []
    for number, members in sorted(grouped.items()):
        out.append(
            Contest(
                str(number - (400 if deputies else 200)),
                f"Distrito {number - 400}" if deputies else f"Circunscripción Senatorial {number - 200}",
                "body_seats",
                key(
                    "area",
                    "cl.historial_distrito" if deputies else "cl.historial_circunscripcion",
                    number - (400 if deputies else 200),
                ),
                None,
                CHAMBER if deputies else SENATE,
                2,
                members,
                seats[number],
                [*(comuna_url(part, code) for code in members), district_url(part, number)],
            )
        )
    return out


# ---- lists and pacts -------------------------------------------------------------------------------------------
#
# A commune's pact page prints each list's votes, its number of candidates, and its number of elected, for the whole
# district or council, and the candidate pages name each candidate's party but not the list. A candidate belongs to the
# list whose figures the parties add up to: each list's candidate count, elected count, and votes in every commune
# must equal the sum over its candidates. A party's candidates all stand on one list, an independent's party column
# reads `IL` and the list's letter, and the independents outside any pact read `IND`. The assignment is kept only when
# exactly one satisfies every one of those sums.

BUDGET = 2_000_000
IN_LIST = re.compile(r"^IL([A-Z])")


class Slate(NamedTuple):
    key: str | None  # the list's letter; None for the independents outside any pact
    label: str
    candidates: int
    elected: int
    votes: dict[str, int]  # by commune


def slates(units: dict[str, PactPage]) -> list[Slate] | None:
    """The lists of a contest, in the order of the first commune that prints each; None where the communes disagree."""
    order: dict[str | None, Slate] = {}
    for code, page in units.items():
        for pact in page.pacts:
            known = order.get(pact.letter)
            if known is None:
                order[pact.letter] = Slate(pact.letter, pact.label, pact.candidates, pact.elected, {code: pact.votes})
            elif (known.label, known.candidates, known.elected) != (pact.label, pact.candidates, pact.elected):
                return None
            else:
                known.votes[code] = pact.votes
    return sorted(order.values(), key=lambda slate: (slate.key is None, len(slate.key or ""), slate.key or ""))


def tie(contest: Contest, found: list[Slate]) -> dict[str, str | None] | None:
    """Each candidate's list (its key, None for the independents), by folded name; None where no single assignment
    satisfies the figures."""
    communes_ = sorted(contest.units)
    elected = {fold(c.name) for c in (contest.official.candidates if contest.official else []) if c.cargo}
    votes: dict[str, list[int]] = defaultdict(lambda: [0] * len(communes_))
    party_of: dict[str, str] = {}
    for at, code in enumerate(communes_):
        for candidate in contest.units[code].candidates:
            name = fold(candidate.name)
            votes[name][at] += candidate.votes
            party_of[name] = fold(candidate.party)
    keys = [slate.key for slate in found]
    left = {
        slate.key: [slate.candidates, slate.elected, *(slate.votes.get(code, 0) for code in communes_)]
        for slate in found
    }
    groups: dict[str, list[str]] = defaultdict(list)
    fixed: dict[str, str | None] = {}
    for name, party_ in party_of.items():
        inside = IN_LIST.match(party_)
        if party_ in ("IND", ""):
            fixed[name] = None
        elif inside and inside.group(1) in keys:
            fixed[name] = inside.group(1)
        else:
            groups[party_].append(name)
    if any(key not in left for key in fixed.values()):
        return None

    def take(slate: str | None, names: list[str]) -> list[int] | None:
        need = left[slate]
        need[0] -= len(names)
        need[1] -= sum(name in elected for name in names)
        for at in range(len(communes_)):
            need[2 + at] -= sum(votes[name][at] for name in names)
        return need

    def fits(need: list[int]) -> bool:
        return all(value >= 0 for value in need)

    for name, slate in fixed.items():
        take(slate, [name])
    if not all(fits(need) for need in left.values()):
        return None
    items = sorted(groups.values(), key=lambda names: (-len(names), -sum(sum(votes[n]) for n in names)))
    solutions: list[dict[str, str | None]] = []
    nodes = 0

    def search(at: int, chosen: dict[str, str | None]) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > BUDGET or len(solutions) > 1:
            return
        if at == len(items):
            if all(not any(need) for need in left.values()):
                solutions.append(dict(chosen))
            return
        names = items[at]
        for slate in keys:
            need = take(slate, names)
            if need is not None and fits(need):
                for name in names:
                    chosen[name] = slate
                search(at + 1, chosen)
                for name in names:
                    del chosen[name]
            take_back(slate, names)

    def take_back(slate: str | None, names: list[str]) -> None:
        need = left[slate]
        need[0] += len(names)
        need[1] += sum(name in elected for name in names)
        for at in range(len(communes_)):
            need[2 + at] += sum(votes[name][at] for name in names)

    search(0, {})
    if nodes > BUDGET or len(solutions) != 1:
        return None
    return {**fixed, **solutions[0]}


# ---- packages --------------------------------------------------------------------------------------------------
#
# Servel's workbooks (`cl.servel`) hold every election of 1989-2010 by circunscripcion electoral, and they are finer
# than this site's commune pages, so they carry the elections, contests, options, offices, bodies, and parties. The
# two sources agree except in a few contests, where this site's commune pages add up to the candidate totals its own
# district and national pages print and the workbooks do not. For each of those contests (`servel.SUPERSEDED`) this
# pipeline publishes its commune counts as the default result set, superseding the workbook's, on Servel's contest.
#
# The workbooks name each list by its letter and give it no votes. For every deputies, senators, and council contest
# the commune pact pages add the lists' names and votes as one more result set, which is not the default, on Servel's
# contest. A candidate that a superseding set adds takes its list from `tie`.

AREA_SCHEMES = (("cl.historial_comuna", 3, "administrative", "commune"),)
UNIT_SCHEME = "cl.historial_comuna_votes"


def geo() -> Package:
    p = Package(f"{SOURCE}/geo", SOURCE, "Chile's communes as the historical results site lists them, 1989-2010")
    p.external.add(CHILE)
    for code, rank, kind, noun in AREA_SCHEMES:
        p.add("scheme", code=code, polity_id=CHILE, kind=kind, rank=rank, noun=noun, code_system=None)
    for url in listings():
        p.uses(raw.cached(SOURCE, url))
    for code, name in communes().items():
        id = key("area", "cl.historial_comuna", code)
        p.add("area", id=id, scheme_code="cl.historial_comuna")
        p.add("area_code", area_id=id, code=code)
        p.name(id, name, "es", "official")
    return p


METHOD = (
    "Votes per commune as the Ministry of the Interior's historical results site, kept by the Servicio Electoral de "
    "Chile (Servel), publishes them: one page per commune lists each candidate's votes and the valid votes, and a "
    "district or constituency page gives the post each candidate won. The site publishes no figures for null or "
    "blank ballots or for the electoral roll at this level."
)
SUPERSEDES = (
    "The commune counts add up to the candidate totals that the site's own page for the {where} prints. Some "
    "candidates' totals in Servel's workbooks by circunscripcion electoral differ from those, so this set supersedes "
    "the workbooks' set for this contest."
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Servel's historical results site as a source, with its commune reporting units",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(CHILE)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Servicio Electoral de Chile (Servel)",
        title="Sitio histórico electoral, 1989-2010",
        url=f"{SITE}/",
        license="No license stated: official results published by the Ministerio del Interior and Servel; facts "
        "published with attribution",
        license_url=None,
        attribution="Servicio Electoral de Chile (Servel)",
    )
    p.add("scheme", code=UNIT_SCHEME, polity_id=CHILE, kind="precinct", rank=4, noun="commune count", code_system=None)
    for code in communes():
        p.add(
            "reporting_unit",
            id=key("unit", code),
            polity_id=CHILE,
            scheme_code=UNIT_SCHEME,
            code=code,
            area_relation="within",
            area_id=key("area", "cl.historial_comuna", code),
            kind="standard",
        )
    return p


def _workbook(day: date, table: str) -> pl.DataFrame:
    return pl.read_parquet(package.folder(f"{servel.SOURCE}/{day}") / "build" / f"{table}.parquet")


def _words(name: str) -> set[str]:
    return set(fold(name).split())


def _match(servel_options: list[tuple[str, str]], name: str) -> str | None:
    """The workbook's option for a candidate the site names, by the words the two names share (the workbooks print a
    name shorter or in another form)."""
    found = [
        option for option, label in servel_options if _words(label) <= _words(name) or _words(name) <= _words(label)
    ]
    return found[0] if len(found) == 1 else None


def totals(contest: Contest) -> dict[str, int]:
    found: dict[str, int] = defaultdict(int)
    for page in contest.units.values():
        for candidate in page.candidates:
            found[fold(candidate.name)] += candidate.votes
    return dict(found)


def differing(election: Election) -> set[tuple[date, str, str]]:
    """The contests whose commune counts add up to the site's own printed totals and differ from the workbooks'."""
    options = _workbook(election.day, "contest_option").select("id", "contest_id", "ballot_label")
    sets = _workbook(election.day, "result_set").select(pl.col("id").alias("result_set_id"), "contest_id")
    votes = (
        _workbook(election.day, "result_value")
        .filter(pl.col("measure") == 1)
        .group_by("result_set_id", "option_id")
        .agg(pl.col("value").sum())
        .join(sets, on="result_set_id")
    )
    sums = {(r[0], r[1]): r[2] for r in votes.select("contest_id", "option_id", "value").rows()}
    listed: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for option, contest_id, label in options.rows():
        listed[contest_id].append((option, label))
    found = set()
    for part in election.parts:
        if part.kind in ("council", "mayor"):
            continue
        for contest in contests(election, part):
            wanted = servel.key("contest", election.day, part.kind, contest.code)
            counted = totals(contest)
            printed = {fold(c.name): c.votes for c in contest.official.candidates} if contest.official else None
            if printed is None or counted != printed:
                continue
            for name, value in counted.items():
                option = _match(listed[wanted], name)
                if option is not None and sums.get((wanted, option), 0) != value:
                    found.add((election.day, part.kind, contest.code))
    return found


def _workbook_contests(day: date) -> set[str]:
    return set(_workbook(day, "contest")["id"].to_list())


def _pacts(part: Part, contest: Contest) -> dict[str, PactPage]:
    found = {code: load_pacts(pact_url(part, code)) for code in contest.units}
    return {code: page for code, page in found.items() if page is not None and page.pacts}


def _target(day: date, part: Part, contest: Contest) -> str:
    """The workbook's contest for this one: a district or constituency by number, a council by its commune's name."""
    if part.kind != "council":
        return servel.key("contest", day, part.kind, contest.code)
    name = comuna_name(contest.code)
    renamed = re.search(r"\(actual ([^)]+)\)", name)
    return servel.key("contest", day, "council", servel.comuna(renamed.group(1) if renamed else name))


LISTS_METHOD = (
    "Votes of each list or pact per commune as the Ministry of the Interior's historical results site, kept by the "
    "Servicio Electoral de Chile (Servel), publishes them: one page per commune prints each list's votes, its "
    "candidates, and how many were elected. The lists carry the letter and name the site prints. The site prints "
    "the independents outside any pact as one row."
)


def _lists(
    p: Package,
    part: Part,
    contest: Contest,
    target: str,
    found: list[Slate],
    rows: list[dict[str, object]],
) -> dict[str | None, str]:
    """The list options and their votes per commune, as a result set that sits beside the workbook's own."""
    day = date.fromisoformat(p.key.rpartition("/")[2])
    ident = (day, part.kind, contest.code)
    result_set = p.id("result_set", "lists", *ident)
    p.add(
        "result_set",
        id=result_set,
        election_id=servel.key("election", day),
        contest_id=target,
        stage="certified",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=False,
        completeness=1.0,
    )
    p.add(
        "note",
        id=p.id("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=LISTS_METHOD,
        lang="en",
    )
    options: dict[str | None, str] = {}
    for position, slate in enumerate(found, 1):
        option = options[slate.key] = p.id("option", "list", *ident, slate.key or "")
        p.add(
            "contest_option",
            id=option,
            contest_id=target,
            kind="independents" if slate.key is None else "list",
            ballot_position=position,
            ballot_label=slate.label if slate.key is None else f"{slate.key}. {slate.label}",
        )
        if slate.elected:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=slate.elected)
    pages = _pacts(part, contest)
    for code in pages:
        p.artifact(raw.cached(SOURCE, pact_url(part, code)), SOURCE_ID)
    for code, page in pages.items():
        unit = key("unit", code)
        for pact in page.pacts:
            rows.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": options[pact.letter],
                    "measure": 1,
                    "value": pact.votes,
                }
            )
        if page.valid is not None:
            rows.append(
                {"result_set_id": result_set, "unit_id": unit, "option_id": None, "measure": 4, "value": page.valid}
            )
    return options


def build(election: Election) -> Package:
    day = election.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Commune counts of the {election.en}: list votes, and counts that supersede Servel's workbooks where they differ",
        depends=[f"{SOURCE}/registry", f"{servel.SOURCE}/{day}"],
    )
    listed: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for option, contest_id, label in _workbook(day, "contest_option").select("id", "contest_id", "ballot_label").rows():
        listed[contest_id].append((option, label))
    parties = {
        row[0]
        for row in pl.read_parquet(package.folder(f"{servel.SOURCE}/registry") / "build" / "party.parquet")
        .select("id")
        .rows()
    }
    rows: list[dict[str, object]] = []
    known = _workbook_contests(day)
    for part in election.parts:
        for contest in contests(election, part):
            ident = (day, part.kind, contest.code)
            lists: dict[str | None, str] = {}
            tied: dict[str, str | None] | None = None
            if part.kind in LISTED:
                found = slates(_pacts(part, contest))
                target = _target(day, part, contest)
                if found and target in known:
                    lists = _lists(p, part, contest, target, found, rows)
                    tied = tie(contest, found)
            if ident in servel.SUPERSEDED:
                _supersede(p, part, contest, listed[servel.key("contest", *ident)], parties, rows, lists, tied)
    schema = {"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "measure": pl.Int64}
    p.frame(
        "result_value",
        pl.DataFrame(rows, schema_overrides={**schema, "value": pl.Int64}).with_columns(
            channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
        ),
    )
    return p


def _supersede(
    p: Package,
    part: Part,
    contest: Contest,
    options: list[tuple[str, str]],
    parties: set[str],
    rows: list[dict[str, object]],
    lists: dict[str | None, str],
    tied: dict[str, str | None] | None,
) -> None:
    day = date.fromisoformat(p.key.rpartition("/")[2])
    ident = (day, part.kind, contest.code)
    contest_id = servel.key("contest", *ident)
    result_set = p.id("result_set", *ident)
    p.add(
        "result_set",
        id=result_set,
        election_id=servel.key("election", day),
        contest_id=contest_id,
        stage="certified",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        supersedes_id=servel.key("result_set", *ident),
        completeness=1.0,
    )
    where = "country" if part.kind == "president" else contest.title.lower()
    p.add(
        "note",
        id=p.id("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=f"{METHOD}\n\n{SUPERSEDES.format(where=where)}",
        lang="en",
    )
    for url in contest.urls:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    chosen: dict[str, str] = {}
    declared = contest.official.candidates if contest.official else []
    pages = [candidate for page in contest.units.values() for candidate in page.candidates]
    for candidate in [*pages, *declared]:
        folded = fold(candidate.name)
        if folded in chosen:
            continue
        option = _match(options, candidate.name)
        if option is None or option in chosen.values():
            option = p.id("option", *ident, folded)
            sigla = fold(candidate.party)
            member = servel.party(sigla) if servel.party(sigla) in parties else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="candidate",
                party_id=member,
                ballot_label=candidate.name,
                parent_option_id=lists.get(tied[folded]) if tied is not None and folded in tied else None,
            )
            person = p.id("person", *ident, folded)
            p.add("person", id=person)
            p.name(person, candidate.name, "es", "official")
            p.add(
                "candidacy",
                id=p.id("candidacy", *ident, folded),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
                member_of_party_id=member,
                is_independent=member is None,
            )
        chosen[folded] = option
    for candidate in declared:
        if candidate.cargo:
            kind = "advanced" if fold(candidate.cargo).startswith("SELECCIONADO") else "elected"
            p.add("outcome", result_set_id=result_set, option_id=chosen[fold(candidate.name)], kind=kind)
    for code, page in contest.units.items():
        unit = key("unit", code)
        for candidate in page.candidates:
            rows.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": chosen[fold(candidate.name)],
                    "measure": 1,
                    "value": candidate.votes,
                }
            )
        for measure, value in ((4, page.valid), (5, page.invalid), (6, page.blank)):
            if value is not None:
                rows.append(
                    {
                        "result_set_id": result_set,
                        "unit_id": unit,
                        "option_id": None,
                        "measure": measure,
                        "value": value,
                    }
                )


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{election.day}" for election in ELECTIONS)]


def parse() -> None:
    print("\n".join([*reconcile(), *national()]), flush=True)
    found = {ident for election in ELECTIONS for ident in differing(election)}
    if found != servel.SUPERSEDED:
        raise ValueError(f"contests to supersede changed: {sorted(found ^ servel.SUPERSEDED)}")
    geo().write()
    registry().write()
    for election in ELECTIONS:
        build(election).write()


def emit() -> None:
    package.run("load", packages())


def reconcile() -> list[str]:
    """Compare the commune pages with the site's own totals: each candidate's votes over a district's or the country's
    communes against the district or national page, and each commune's candidate votes against its valid votes."""
    report = []
    for election in ELECTIONS:
        for part in election.parts:
            exact = mismatched = pages = off = 0
            for contest in contests(election, part):
                for page in contest.units.values():
                    pages += 1
                    if page.valid is not None and sum(c.votes for c in page.candidates) != page.valid:
                        off += 1
                if part.kind in ("council", "mayor") or contest.official is None:
                    continue
                total: dict[str, int] = defaultdict(int)
                for page in contest.units.values():
                    for candidate in page.candidates:
                        total[fold(candidate.name)] += candidate.votes
                for candidate in contest.official.candidates:
                    if total[fold(candidate.name)] == candidate.votes:
                        exact += 1
                    else:
                        mismatched += 1
            report.append(
                f"{election.day} {part.kind}: {pages} commune pages, {off} whose candidates do not sum to the valid "
                f"votes; {exact} candidates match the official page, {mismatched} do not"
            )
    return report


def national_valid(part: Part) -> int | None:
    artifact = load_raw(national_url(part))
    if artifact is None:
        return None
    for row in re.findall(r"<tr>(.*?)</tr>", artifact.decode("latin-1"), flags=re.IGNORECASE | re.DOTALL):
        cells = [text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)]
        if cells and fold(cells[0]).startswith("VALIDAMENTE"):
            return number(cells[1])
    return None


def load_raw(url: str) -> bytes | None:
    return raw.cached(SOURCE, url).path.read_bytes() if url in _cached() else None


def national() -> list[str]:
    report = []
    for election in ELECTIONS:
        for part in election.parts:
            expected = national_valid(part)
            got = sum(page.valid or 0 for contest in contests(election, part) for page in contest.units.values())
            if expected is None:
                report.append(f"{election.day} {part.kind}: no national page; communes sum to {got:,} valid votes")
            else:
                report.append(
                    f"{election.day} {part.kind}: national page {expected:,} valid votes, communes {got:,}, "
                    f"difference {got - expected:,}"
                )
    return report
