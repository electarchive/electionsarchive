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

from ea import changeset, package, raw
from ea.package import Package

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


def table(content: bytes) -> list[list[str]]:
    """The rows of the first table whose header names NOMBRE and VOTOS, as cell texts."""
    page = content.decode("latin-1")
    for block in re.findall(r"<table.*?</table>", page, flags=re.IGNORECASE | re.DOTALL):
        rows = []
        for row in re.findall(r"<tr>(.*?)</tr>", block, flags=re.IGNORECASE | re.DOTALL):
            cells = [
                text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)
            ]
            if cells:
                rows.append(cells)
        if rows and {"NOMBRE", "VOTOS"} <= {fold(c) for c in rows[0]}:
            return rows
    return []


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


# ---- reading the cache -----------------------------------------------------------------------------------------


@cache
def _cached() -> frozenset[str]:
    return frozenset(raw.keys(SOURCE))


@cache
def load(url: str) -> Page | None:
    if url not in _cached():
        return None
    return read(raw.cached(SOURCE, url).path.read_bytes())


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


# ---- packages --------------------------------------------------------------------------------------------------

AREA_SCHEMES = (
    ("cl.historial_comuna", 3, "administrative", "commune"),
    ("cl.historial_distrito", 2, "electoral_district", "electoral district (1989–2013)"),
    ("cl.historial_circunscripcion", 2, "electoral_district", "senatorial constituency (1989–2013)"),
)
UNIT_SCHEME = "cl.historial_comuna_votes"


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo", SOURCE, "Chile's communes, electoral districts, and senatorial constituencies, 1989-2010"
    )
    p.external.add(CHILE)
    for code, rank, kind, noun in AREA_SCHEMES:
        p.add("scheme", code=code, polity_id=CHILE, kind=kind, rank=rank, noun=noun, code_system=None)

    def place(scheme: str, code: str, label: str) -> None:
        id = key("area", scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.name(id, label, "es", "official")

    for url in listings():
        p.uses(raw.cached(SOURCE, url))
    for code, name in communes().items():
        place("cl.historial_comuna", code, name)
    for number in range(1, 61):
        place("cl.historial_distrito", str(number), f"Distrito {number} (1989–2013)")
    for number in range(1, 20):
        place("cl.historial_circunscripcion", str(number), f"Circunscripción Senatorial {number} (1989–2013)")
    return p


def parties() -> dict[str, str]:
    found: dict[str, str] = {}
    for election in ELECTIONS:
        for part in election.parts:
            for code in communes():
                page = load(comuna_url(part, code))
                for candidate in page.candidates if page else []:
                    sigla = " ".join(candidate.party.split())
                    if sigla and not INDEPENDENT.match(fold(sigla)):
                        found.setdefault(fold(sigla), sigla)
    return dict(sorted(found.items()))


METHOD = (
    "Votes per commune as the Ministry of the Interior's historical results site, kept by the Servicio Electoral de "
    "Chile (Servel), publishes them: one page per commune lists each candidate's votes and the valid votes, and a "
    "district or constituency page gives the post each candidate won. The site publishes no figures for null or "
    "blank ballots or for the electoral roll at this level."
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Servel's historical results site as a source, with the reporting units, offices, bodies, and parties it names",
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
    for code, name in communes().items():
        unit = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CHILE,
            scheme_code=UNIT_SCHEME,
            code=code,
            area_relation="within",
            area_id=key("area", "cl.historial_comuna", code),
            kind="standard",
        )
        mayor = key("office", "mayor", code)
        p.add("office", id=mayor, polity_id=CHILE, kind="mayor")
        p.name(mayor, f"Alcalde de {name}", "es", "official")
        council = key("body", "council", code)
        p.add("body", id=council, polity_id=CHILE, kind="council")
        p.name(council, f"Concejo Municipal de {name}", "es", "official")
    p.add("office", id=PRESIDENCY, polity_id=CHILE, kind="head_of_state")
    p.name(PRESIDENCY, "Presidente de la República de Chile", "es", "official")
    p.name(PRESIDENCY, "President of Chile", "en", "official", False)
    for id, kind, es, en in (
        (CHAMBER, "lower_house", "Cámara de Diputados", "Chamber of Deputies"),
        (SENATE, "upper_house", "Senado", "Senate"),
    ):
        p.add("body", id=id, polity_id=CHILE, kind=kind)
        p.name(id, es, "es", "official")
        p.name(id, en, "en", "official", False)
    for folded, sigla in parties().items():
        id = party(folded)
        p.add("party", id=id, kind="party", polity_id=CHILE)
        p.add("external_id", entity_id=id, scheme="cl.historial.party", value=folded)
        p.name(id, sigla, "es", "official")
    return p


def build(election: Election) -> Package:
    day = election.day
    deps = [f"{SOURCE}/geo", f"{SOURCE}/registry"]
    if election.runoff_of is not None:
        deps.append(f"{SOURCE}/{election.runoff_of}")
    p = Package(
        f"{SOURCE}/{day}", SOURCE, f"{election.en} by commune from Servel's historical results site", depends=deps
    )
    p.external.add(CHILE)
    election_id = p.id("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=CHILE,
        kind=election.kind,
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, election.en, "en", "official")
    p.name(election_id, election.es, "es", "native", False)
    base = f"cl/{day}-{election.word}-by-commune"
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    slugs: set[str] = set()
    rows: list[dict[str, object]] = []
    first = election.runoff_of
    for part in election.parts:
        for contest in contests(election, part):
            _contest(p, election, part, contest, election_id, base, slugs, rows, first)
    p.frame(
        "result_value",
        pl.DataFrame(rows, schema_overrides={"unit_id": pl.String}).with_columns(
            channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
        ),
    )
    return p


def _contest(
    p: Package,
    election: Election,
    part: Part,
    contest: Contest,
    election_id: str,
    base: str,
    slugs: set[str],
    rows: list[dict[str, object]],
    first: date | None,
) -> None:
    day = election.day
    ident = (part.kind, contest.code)
    contest_id = p.id("contest", day, *ident)
    result_set = p.id("result_set", day, *ident)
    p.add(
        "contest",
        id=contest_id,
        election_id=election_id,
        kind=contest.kind,
        office_id=contest.office,
        body_id=contest.body,
        district_area_id=contest.district,
        magnitude=contest.magnitude,
        round_number=2 if first is not None else 1,
        previous_round_id=None if first is None else p.id("contest", first, *ident),
        electoral_system_id=None,
        default_result_kind="counted",
    )
    p.name(contest_id, contest.title, "es", "official")
    path = f"{base}/{changeset.slug(contest.title)}"
    if path in slugs:
        path = f"{path}-{changeset.slug(contest.code)}"
    slugs.add(path)
    p.add("slug", path=path, entity_id=contest_id, is_current=True)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest_id,
        stage="certified",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    for url in contest.urls:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    options: dict[str, str] = {}
    declared = contest.official.candidates if contest.official else []
    seen: dict[str, int] = defaultdict(int)
    order = {}
    for page in contest.units.values():
        for candidate in page.candidates:
            order.setdefault((fold(candidate.name), fold(candidate.party)), candidate)
    for candidate in declared:
        order.setdefault((fold(candidate.name), fold(candidate.party)), candidate)
    for (folded, sigla), candidate in order.items():
        seen[folded] += 1
        suffix = "" if seen[folded] == 1 else f"-{seen[folded]}"
        option = p.id("option", day, *ident, folded + suffix)
        options[f"{folded}|{sigla}"] = option
        member = None if INDEPENDENT.match(sigla) else party(sigla)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest_id,
            kind="candidate",
            party_id=member,
            ballot_label=candidate.name,
        )
        person = p.id("person", day, *ident, folded + suffix)
        p.add("person", id=person)
        p.name(person, candidate.name, "es", "official")
        p.add(
            "candidacy",
            id=p.id("candidacy", day, *ident, folded + suffix),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate.name,
            member_of_party_id=member,
            is_independent=member is None,
        )
    for candidate in declared:
        option = options[f"{fold(candidate.name)}|{fold(candidate.party)}"]
        if candidate.cargo:
            kind = "advanced" if fold(candidate.cargo).startswith("SELECCIONADO") else "elected"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    for code, page in contest.units.items():
        unit = key("unit", code)
        for candidate in page.candidates:
            rows.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": options[f"{fold(candidate.name)}|{fold(candidate.party)}"],
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
