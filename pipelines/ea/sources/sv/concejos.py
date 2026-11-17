"""Municipal council elections of El Salvador, 1994, 1997, 2012, and 2015, from the Tribunal Supremo Electoral's scrutiny reports.

Each election is one package: a contest per municipality, an option per party or coalition, and values by municipality.
Every report has a text layer and is read with `pdftotext`. The 2000 to 2009 reports are scans whose digits do not read
reliably enough to pass the accounting checks, and the 2018 and 2021 reports and the 2024 results are not served, so none
of them is built yet.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import cache

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.sv import assembly, presidents, tse

SOURCE = tse.SOURCE
DOCS = assembly.DOCS
VOTES, VALID, INVALID, OBJECTED = tse.VOTES, tse.VALID, tse.INVALID, tse.OBJECTED
REGISTRY = "sv.tse/council-registry"
WHOLE = "https://transparencia.tse.gob.sv/laip_tse/documentos/Actas_de_Escrutinio/"

PARTY_NAMES = {
    **assembly.PARTY_NAMES,
    "PSD": "Partido Social Demócrata",
    "PSP": "Partido Salvadoreño Progresista",
    "DS": "Democracia Salvadoreña",
    "CN": "Cambio Nacional",
}
ALIASES = {
    **assembly.ALIASES,
    "STAGO TEXACUANGOS": "SANTIAGO TEXACUANGOS",
    "ROSARIO DE LA PAZ": "EL ROSARIO",
    "SAN GERERDO": "SAN GERARDO",
    "SAN LUIS LA REINA": "SAN LUIS DE LA REINA",
    "SAN DIONICIO": "SAN DIONISIO",
    "SANTA ISABEL": "SANTA ISABEL ISHUATAN",
    "CONCEPCION DE QUEZALTEPE": "CONCEPCION QUEZALTEPEQUE",
    "SAN RAFAEL ARCANGEL O CED": "SAN RAFAEL CEDROS",
    "SAN IDELFONSO": "SAN ILDEFONSO",
    "SENSUTEPEQUE": "SENSUNTEPEQUE",
}

COLUMNS_2012 = ["ARENA", "CD", "CN", "FMLN", "FPS", "GANA", "PES", "PNL", "PP"] + [
    "ARENA/CN",
    "ARENA/PES",
    "CN/ARENA",
    "CN/ARENA/PES",
    "CN/PES",
    "CN/PNL",
    "FMLN/CD",
    "FMLN/PES",
    "PES/GANA",
]
COLUMNS_2015 = ["ARENA", "CD", "DS", "FMLN", "GANA", "FPS", "PCN", "PDC", "PSD", "PSP"] + [
    "ARENA/PCN",
    "ARENA/PDC",
    "FMLN/CD",
    "FMLN/GANA",
    "FMLN/PSP",
    "PCN/DS",
    "PCN/ARENA",
    "PCN/PDC",
    "PDC/ARENA",
    "PDC/PCN",
]


@dataclass
class Row:
    """One municipality's line: the printed column for each party or coalition, and the ballot counts."""

    name: str
    votes: dict[str, int]
    valid: int
    objected: int
    invalid: int


@dataclass
class Department:
    number: int
    rows: list[Row] = field(default_factory=list)
    total: Row | None = None


@dataclass(frozen=True)
class Election:
    day: date
    files: tuple[str, ...]
    title: str
    totals: tuple[str, ...] = ()
    winners: tuple[str, ...] = ()


ELECTIONS = {
    e.day: e
    for e in (
        Election(
            date(1994, 3, 20),
            (DOCS + "estadisticos/1994/votosalc1994.pdf",),
            "Escrutinio final para concejos municipales, elecciones del 20 de marzo de 1994",
        ),
        Election(
            date(1997, 3, 16),
            (DOCS + "estadisticos/1997/consolidadonacionalalc1997.pdf",),
            "Escrutinio final para concejos municipales, elecciones del 16 de marzo de 1997",
        ),
        Election(
            date(2012, 3, 11),
            (DOCS + "estadisticos/2012/concejos_resultados_2012.pdf",),
            "Consolidado municipal de concejos municipales de elecciones 2012",
            (),
            (DOCS + "estadisticos/2012/concejos_2012.pdf",),
        ),
        Election(
            date(2015, 3, 1),
            (DOCS + "estadisticos/2015/DetalleMuniyPPCM2015.pdf",),
            "Elecciones 2015: detalle por municipio de concejos municipales",
            (DOCS + "estadisticos/2015/DetalledeptoyPPCM2015.pdf",),
            (DOCS + "estadisticos/2015/NombreAcaldiasdeptoymuni2015.pdf",),
        ),
    )
}


def key(kind: str, *parts: object) -> str:
    return tse.key(kind, "council", *parts)


@cache
def held() -> dict[str, str]:
    """The party codes that loaded sv.tse packages already declare, each with the package declaring it.

    Every sv.tse package gives a printed code the same source-wide id, so a council option of a held code takes that
    id and the council registry leaves the code to the package that holds it.
    """
    out = dict.fromkeys(tse.PARTIES, "sv.tse/registry")
    for code in sorted({code for e in presidents.ELECTIONS.values() for code in e.parties}):
        out.setdefault(code, presidents.REGISTRY)
    return out


def party(code: str) -> str:
    """A party's id: the source-wide id that every sv.tse package uses for the same printed code."""
    return tse.key("party", code)


def holders() -> list[str]:
    """The earlier packages that declare parties on the council ballots."""
    found = {held()[code] for code in council_codes() if code in held()}
    return sorted(found)


@cache
def council_codes() -> tuple[str, ...]:
    codes: dict[str, None] = {}
    for election in ELECTIONS.values():
        for department in tables(election)[0]:
            for row in department.rows:
                for code in ballot(row):
                    if "/" not in code:
                        codes.setdefault(code)
    return tuple(codes)


def count(token: str) -> int:
    return int(token.replace(",", ""))


def read_headed(text: str, department: str) -> list[Department]:
    """A report whose pages name their parties in a header line: `MUNICIPIOS ARENA ... VALIDOS IMPUG NULOS ABST VOTOS`."""
    numbers_of = assembly.departments()
    out: dict[int, Department] = {}
    current: Department | None = None
    parties: list[str] = []
    for line in text.splitlines():
        header = re.match(department, line)
        if header:
            current = out.setdefault(
                numbers_of[assembly.plain(header.group(1))], Department(numbers_of[assembly.plain(header.group(1))])
            )
            continue
        columns = re.match(r"\s*MUNICIPIOS?\s+(.*?)\s+VALIDOS", line)
        if columns:
            parties = columns.group(1).split()
            continue
        label, numbers = assembly.split(line)
        if current is None or not parties or len(numbers) != len(parties) + 5:
            continue
        values = [count(n) for n in numbers]
        n = len(parties)
        row = Row(
            re.sub(r"^\d+\s+", "", label),
            dict(zip(parties, values[:n], strict=True)),
            values[n],
            values[n + 1],
            values[n + 2],
        )
        if label.startswith("TOTAL"):
            current.total = row
        else:
            current.rows.append(row)
    return [out[number] for number in sorted(out)]


def read_1994(text: str) -> list[Department]:
    return read_headed(text, r"\s*DEPARTAMENTO ([A-ZÑ ]+?)\s*$")


def read_1997(text: str) -> list[Department]:
    return read_headed(text, r"\s*DEPARTAMENTO:\s+([A-ZÑ ]+?)\s*$")


def read_2012(text: str) -> list[Department]:
    numbers_of = assembly.departments()
    out: dict[int, Department] = {}
    current: Department | None = None
    for line in text.splitlines():
        header = re.match(r"\s*DEPARTAMENTO:\s+([A-ZÑ ]+?)\s{2,}", line)
        if header:
            number = numbers_of[assembly.plain(header.group(1))]
            current = out.setdefault(number, Department(number))
            continue
        label, numbers = assembly.split(line)
        if current is None or len(numbers) != 27 or not label:
            continue
        values = [count(n) for n in numbers]
        row = Row(label, dict(zip(COLUMNS_2012, values[:18], strict=True)), values[18], values[19], values[20])
        if label.startswith("Totales"):
            current.total = row
        elif row.name not in {r.name for r in current.rows}:
            current.rows.append(row)
    return [out[number] for number in sorted(out)]


def read_2015(text: str) -> list[Department]:
    out: dict[int, Department] = {}
    current: Department | None = None
    pending: str | None = None
    after_title = False
    for line in text.splitlines():
        if "DETALLE POR MUNICIPIO" in line or "DETALLE POR DEPARTAMENTO" in line:
            after_title = True
            continue
        head = re.match(r"^\s*(\d+)\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .]*)$", line)
        if after_title and head:
            current = out.setdefault(int(head.group(1)), Department(int(head.group(1))))
            after_title = False
            continue
        if current is None:
            continue
        if head:
            pending = head.group(2).strip()
            continue
        _, numbers = assembly.split(line)
        if len(numbers) == 29 and pending:
            values = [count(n) for n in numbers]
            current.rows.append(
                Row(pending, dict(zip(COLUMNS_2015, values[:20], strict=True)), values[20], values[21], values[22])
            )
            pending = None
    return [out[number] for number in sorted(out)]


def department_totals_2015(text: str) -> dict[int, Row]:
    """The printed totals by department in 2015, from the report of the departments."""
    out: dict[int, Row] = {}
    pending: int | None = None
    for line in text.splitlines():
        head = re.match(r"^\s*(\d+)\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .]*)$", line)
        if head:
            pending = int(head.group(1))
            continue
        _, numbers = assembly.split(line)
        if pending and len(numbers) >= 23:
            values = [count(n) for n in numbers]
            out[pending] = Row(
                "", dict(zip(COLUMNS_2015, values[:20], strict=True)), values[20], values[21], values[22]
            )
            pending = None
    return out


def winners_2012(text: str, deps: list[Department], known: dict[int, dict[str, str]]) -> dict[str, str]:
    """The party or coalition of each elected mayor in 2012, by municipality code, from the list of elected mayors."""
    numbers_of = assembly.departments()
    out: dict[str, str] = {}
    ordinal = 0
    for line in text.splitlines():
        match = re.match(r"^\s*(?:\d+-)?(\d+)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]+?)\s{2,}.*\s{2,}([A-Z][A-Z-]*)\s*$", line)
        if match and numbers_of.get(assembly.plain(match.group(2))) is not None:
            number, ordinal_, label = numbers_of[assembly.plain(match.group(2))], int(match.group(1)), match.group(3)
        else:
            # A municipality whose name wraps puts its number on one line and its department and party on the next.
            first = re.match(r"^\s*(\d+)\s{2,}[A-Za-zÁÉÍÓÚÑáéíóúñ ]+$", line)
            if first:
                ordinal = int(first.group(1))
                continue
            second = re.match(r"^\s+([A-Za-zÁÉÍÓÚÑ ]+?)\s{2,}.*\s{2,}([A-Z][A-Z-]*)\s*$", line)
            if not (ordinal and second and numbers_of.get(assembly.plain(second.group(1))) is not None):
                continue
            number, ordinal_, label = numbers_of[assembly.plain(second.group(1))], ordinal, second.group(2)
        ordinal = 0
        rows = next(d.rows for d in deps if d.number == number)
        out[locate(number, rows[ordinal_ - 1].name, known)] = label
    return out


def winners_2015(text: str, known: dict[int, dict[str, str]]) -> dict[str, str]:
    """The party or coalition that won each municipality in 2015, from the list of mayoralties by party."""
    numbers_of = assembly.departments()
    out: dict[str, str] = {}
    party_label = ""
    department = 0
    header = ""
    for line in text.splitlines():
        heading = re.match(r"^\s{5,}([A-Z]+(?:/[A-Z]+)*)\s*$", line)
        if heading:
            party_label, department, header = heading.group(1), 0, ""
            continue
        for number, name in re.findall(r"(\d+)\s+([A-ZÑ][A-ZÑ ]*?)(?=\s{2,}\d+\s|\s*$)", line):
            # A department's header line has the department's own number and name; a municipality that is alone on
            # its line and shares both, such as San Salvador, follows its department's header directly.
            if numbers_of.get(assembly.plain(name)) == int(number) and line.strip() == f"{number} {name}" != header:
                department, header = int(number), line.strip()
            else:
                out[locate(department, name, known)] = party_label
                header = ""
    return out


def winners(election: Election, deps: list[Department], known: dict[int, dict[str, str]]) -> dict[str, str]:
    if not election.winners:
        return {}
    text = assembly.pdf_text(raw.cached(SOURCE, election.winners[0]))
    return winners_2012(text, deps, known) if election.day.year == 2012 else winners_2015(text, known)


def verify(election: Election, deps: list[Department]) -> list[str]:
    """Differences between each department's printed totals and the sum of its municipalities."""
    if election.totals:
        printed = department_totals_2015(assembly.pdf_text(raw.cached(SOURCE, election.totals[0])))
        for department in deps:
            department.total = printed.get(department.number)
    out: list[str] = []
    for department in deps:
        if department.total is None:
            continue
        total = department.total
        columns = {k: sum(r.votes.get(k, 0) for r in department.rows) for k in total.votes}
        columns |= {"valid": sum(r.valid for r in department.rows)}
        want = {**total.votes, "valid": total.valid}
        out += [f"{department.number} {k}: {columns[k]} != {want[k]}" for k in want if columns[k] != want[k]]
        if sum(r.objected for r in department.rows) != total.objected:
            out.append(f"{department.number} objected")
        if sum(r.invalid for r in department.rows) != total.invalid:
            out.append(f"{department.number} invalid")
    return out


READERS: dict[int, Callable[[str], list[Department]]] = {
    1994: read_1994,
    1997: read_1997,
    2012: read_2012,
    2015: read_2015,
}


def tables(election: Election) -> tuple[list[Department], list[raw.Artifact]]:
    artifacts = [raw.cached(SOURCE, url) for url in election.files]
    return READERS[election.day.year]("\n".join(assembly.pdf_text(a) for a in artifacts)), artifacts


def ballot(row: Row) -> dict[str, int]:
    """The votes for each list on a municipality's ballot.

    A coalition's column holds its total, which includes the columns of its member parties, so a coalition that
    received votes replaces its members; elsewhere each party's own column is its list.
    """
    coalitions = {code for code, value in row.votes.items() if "/" in code and value}
    members = {member for code in coalitions for member in code.split("/")}
    return {
        code: value
        for code, value in row.votes.items()
        if value and (code in coalitions or ("/" not in code and code not in members))
    }


def locate(number: int, name: str, known: dict[int, dict[str, str]]) -> str:
    label = assembly.plain(name)
    if label not in known[number]:
        label = ALIASES.get(label, label)
    return known[number][label]


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The municipal councils of El Salvador, their electoral systems, and the parties on their ballots, 1994 to 2015",
        depends=["sv.tse/geo", *holders()],
    )
    p.external |= {tse.EL_SALVADOR, tse.TERRITORY}
    for era, since, until, rules, text in SYSTEMS:
        id = key("electoral_system", era)
        p.add(
            "electoral_system",
            id=id,
            polity_id=tse.EL_SALVADOR,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    names = assembly_names()
    for units in assembly.municipalities().values():
        for code in units.values():
            id = key("body", code)
            p.add("body", id=id, polity_id=tse.EL_SALVADOR, kind="council")
            title = names[code]
            p.name(id, f"Municipal council of {title}", "en", "official")
            p.name(id, f"Concejo Municipal de {title}", "es", "native", False)
    for code in council_codes():
        if code in held():
            continue  # an earlier sv.tse package already declares this party and its code
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=tse.EL_SALVADOR)
        p.add("external_id", entity_id=id, scheme="sv.tse.party", value=code)
        p.name(id, PARTY_NAMES.get(code, code), "es", "official")
        if code in PARTY_NAMES:
            p.name(id, code, "es", "abbreviation", False)
    return p


def assembly_names() -> dict[str, str]:
    """The 2014 name of each municipality, by code."""
    _, municipalities, _ = tse.tree(1, with_centers=False)
    return {code: label.title() for code, (label, _) in municipalities.items()}


SYSTEMS = [
    (
        "slate",
        date(1985, 1, 1),
        date(2015, 1, 1),
        {"formula": "winner_take_all"},
        (
            "Each municipality elects its whole council, a mayor, a síndico, and regidores, on one party slate. The "
            "slate with the most valid votes wins every seat (Código Electoral, art. 262)."
        ),
    ),
    (
        "plural",
        date(2015, 1, 1),
        None,
        {"formula": "hare"},
        (
            "Each municipality elects its council on party slates. The slate with the most votes takes the mayor, the "
            "síndico, and enough regidores for a majority of the council, and the other regidores go to the remaining "
            "slates by quotient and largest remainder."
        ),
    ),
]


def slug(text: str) -> str:
    return assembly.slug(text)


def method(day: date) -> str:
    text = (
        f"Final scrutiny (escrutinio final) of the {day.year} election of municipal councils as published by the "
        "Tribunal Supremo Electoral, one row per municipality. Votes are valid votes for each party's slate; nulos "
        "are stored as invalid ballots and impugnados as objected ballots. The source also lists abstentions and "
        "ballot papers that are missing, in surplus, or unused, which are not stored."
    )
    if day.year == 1997:
        text += (
            " The report prints each party's own column; a coalition's votes are the votes of its member parties, so "
            "the options here are the parties."
        )
    if day.year in (2012, 2015):
        text += (
            " Where a coalition ran, the report prints a column for the coalition holding its votes and also the "
            "columns of its member parties; a coalition that received votes replaces its members here, so the options "
            "of a municipality sum to its valid votes."
        )
    return text


def build_election(day: date) -> Package:
    election = ELECTIONS[day]
    deps, artifacts = tables(election)
    if differences := verify(election, deps):
        raise ValueError(f"{day}: department totals differ from municipal sums: {differences}")
    known = assembly.municipalities()
    won = winners(election, deps, known)
    if election.winners and len(won) != 262:
        raise ValueError(f"{day}: {len(won)} winners")
    names = assembly_names()
    departments = {number: name.title() for name, number in assembly.departments().items()}
    p = Package(
        f"sv.tse/council-{day}",
        SOURCE,
        f"The {day.year} election of municipal councils of El Salvador, by municipality, from the Tribunal Supremo "
        "Electoral",
        depends=["sv.tse/geo", *holders(), REGISTRY],
    )
    p.external |= {tse.EL_SALVADOR, tse.TERRITORY}
    source = key("source", day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Tribunal Supremo Electoral de El Salvador",
        title=election.title,
        url=assembly.LISTING,
        license="Official public results of the Tribunal Supremo Electoral",
        license_url="https://transparencia.tse.gob.sv/",
        attribution="Tribunal Supremo Electoral de El Salvador",
    )
    for artifact in artifacts:
        p.artifact(artifact, source)
    for url in (*election.totals, *election.winners):
        p.artifact(raw.cached(SOURCE, url), source)
    id = key("election", day)
    p.add("election", id=id, polity_id=tse.EL_SALVADOR, kind="general", date_from=day, date_to=day, status="held")
    path = f"sv/{day}-municipal"
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{day.year} Salvadoran municipal elections", "en", "official")
    p.name(id, f"Elecciones de concejos municipales {day.year}", "es", "native", False)
    system = key("electoral_system", "plural" if day.year >= 2015 else "slate")
    facts: list[dict[str, object]] = []

    def value(unit: str, set_: str, option: str | None, measure: int, amount: int) -> None:
        facts.append(
            {
                "result_set_id": set_,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "flags": 0,
                "value": amount,
            }
        )

    seen: set[str] = set()
    placed: set[str] = set()
    for department in deps:
        for row in department.rows:
            code = locate(department.number, row.name, known)
            if code in seen:
                raise ValueError(f"{day}: {row.name} appears twice")
            seen.add(code)
            title = names[code]
            contest = key("contest", day, code)
            p.add(
                "contest",
                id=contest,
                election_id=id,
                kind="list_allocation",
                body_id=key("body", code),
                district_area_id=tse.area("sv.municipio", code),
                magnitude=None,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            p.name(contest, f"{day.year} municipal council, {title}", "en", "official")
            p.name(contest, f"Concejo Municipal de {title} {day.year}", "es", "native", False)
            p.add(
                "slug",
                path=f"{path}/{slug(departments[department.number])}/{slug(title)}",
                entity_id=contest,
                is_current=True,
            )
            result_set = key("result_set", day, code)
            p.add(
                "result_set",
                id=result_set,
                election_id=id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=source,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=method(day),
                lang="en",
            )
            unit = key("unit", day, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=tse.EL_SALVADOR,
                scheme_code="sv.municipio",
                code=f"{day}:council:{code.removeprefix('mun')}",
                area_relation="equals",
                area_id=tse.area("sv.municipio", code),
                kind="aggregate",
            )
            p.name(unit, title, "es", "official")
            lists = ballot(row)
            for position, (label, votes) in enumerate(lists.items(), 1):
                option = key("option", day, code, label)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=None if "/" in label else party(label),
                    ballot_position=position,
                    ballot_label=label,
                )
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
                if code in won and set(re.split(r"[-/]", won[code])) == set(label.split("/")):
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="won")
                    placed.add(code)
                value(unit, result_set, option, VOTES, votes)
            for measure, amount_ in ((VALID, row.valid), (INVALID, row.invalid), (OBJECTED, row.objected)):
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=amount_)
                value(unit, result_set, None, measure, amount_)
    if missing := set(won) - placed:
        raise ValueError(f"{day}: winners without an option: {sorted(missing)[:5]}")
    p.frame("result_value", pl.DataFrame(facts))
    return p


def fetch() -> None:
    for name in ("pais", *("dep%02d" % n for n in range(1, 16))):
        tse._crawl(1, name)
    for election in ELECTIONS.values():
        for url in (*election.files, *election.totals, *election.winners):
            raw.fetch(SOURCE, url, suffix=".pdf")


def packages() -> list[str]:
    return [REGISTRY, *(f"sv.tse/council-{day}" for day in ELECTIONS)]


def build(name: str) -> Package:
    return registry() if name == REGISTRY else build_election(date.fromisoformat(name.removeprefix("sv.tse/council-")))
