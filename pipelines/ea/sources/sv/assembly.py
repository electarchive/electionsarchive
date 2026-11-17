"""Legislative Assembly elections of El Salvador, 1994, 2012, and 2015, from the Tribunal Supremo Electoral's scrutiny reports.

Each election is one package: a contest per departmental constituency, a list option per party or coalition, and values
by municipality. The reports have a text layer and are read with `pdftotext`. The 1997 to 2009 reports are scans whose digits
do not read reliably enough to pass the accounting checks, so they are not built yet.
"""

import re
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.sv import tse
from ea.store import DATA

SOURCE = tse.SOURCE
DOCS = "https://transparencia.tse.gob.sv/laip_tse/documentos/"
LISTING = "https://transparencia.tse.gob.sv/laip_tse/index.php/component/content/article/2-uncategorised/57-resultados-de-los-escrutinios"
VOTES, VALID, INVALID, OBJECTED = tse.VOTES, tse.VALID, tse.INVALID, tse.OBJECTED
BODY = tse.key("body", "assembly")
REGISTRY = "sv.tse/assembly-registry"

PARTY_NAMES = {
    "ARENA": "Alianza Republicana Nacionalista",
    "FMLN": "Frente Farabundo Martí para la Liberación Nacional",
    "CD": "Convergencia Democrática",
    "PCN": "Partido de Conciliación Nacional",
    "PDC": "Partido Demócrata Cristiano",
    "MU": "Movimiento de Unidad",
    "MNR": "Movimiento Nacional Revolucionario",
    "MSN": "Movimiento de Solidaridad Nacional",
    "MAC": "Movimiento Auténtico Cristiano",
    "GANA": "Gran Alianza por la Unidad Nacional",
    "CN": "Cambio Nacional",
    "PSD": "Partido Social Demócrata",
    "DS": "Democracia Salvadoreña",
    "FPS": "Fraternidad Patriota Salvadoreña",
    "PLD": "Partido Liberal Democrático",
    "PRSC": "Partido de Renovación Social Cristiano",
    "PD": "Partido Demócrata",
}
INDEPENDENTS = {"HUEZO", "MORAN", "GARCIA", "GOMAR", "RUBIO", "NO PDO."}
ALIASES = {
    "SAN SEBASTIAN": "SAN SEBASTIAN SALITRILLO",
    "MERCEDES UMA": "MERCEDES UMANA",
    "NUEVA SAN SALVADOR": "SANTA TECLA",
    "SAN BUENA VENTURA": "SAN BUENAVENTURA",
    "NAHULINGO": "NAHUILINGO",
    "SAN RAFAEL OBRAJUELOS": "SAN RAFAEL OBRAJUELO",
    "SAN JOSE LAS FLORES": "LAS FLORES",
    "CANCASQUE": "SAN JOSE CANCASQUE",
    "SAN BARTOLOME PERULAPAN": "SAN BARTOLOME PERULAPIA",
    "SANTA CRUZ ANALQUIT": "SANTA CRUZ ANALQUITO",
    "TEJUTEPQUE": "TEJUTEPEQUE",
}


@dataclass
class Row:
    name: str
    votes: dict[str, float]
    valid: int
    invalid: int
    objected: int


@dataclass
class Department:
    number: int
    rows: list[Row] = field(default_factory=list)
    total: Row | None = None
    seats: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Election:
    day: date
    files: tuple[str, ...]
    read: str
    seats: str | None = None


ELECTIONS = {
    e.day: e
    for e in (
        Election(date(1994, 3, 20), (DOCS + "estadisticos/1994/votosdip1994.pdf",), "text"),
        Election(
            date(2012, 3, 11),
            (DOCS + "estadisticos/2012/consolidadomundiputados.pdf",),
            "text",
            DOCS + "estadisticos/2012/diputados_resultados_2012.pdf",
        ),
        Election(
            date(2015, 3, 1),
            (DOCS + "estadisticos/2015/Consolidado%20por%20depto-muni%20Asamblea%20Legislativa2015.pdf",),
            "text",
        ),
    )
}


def plain(value: str) -> str:
    value = unicodedata.normalize("NFD", value.upper())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    value = re.sub(r"\bSTA\b\.?", "SANTA", value)
    value = re.sub(r"\bSTO\b\.?", "SANTO", value)
    value = re.sub(r"\bSTAGO\b\.?", "SANTIAGO", value)
    value = re.sub(r"\bSN\b\.?", "SAN", value)
    return re.sub(r"[^A-Z0-9]+", " ", value).strip()


def departments() -> dict[str, int]:
    names, _, _ = tse.tree(1, with_centers=False)
    return {plain(label): int(code.removeprefix("dep")) for code, label in names.items() if int(code[3:]) <= 14}


def municipalities() -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {}
    for number in range(1, 15):
        for label, link, _ in tse.rows(tse.text(1, f"dep{number:02d}")):
            if link:
                out.setdefault(number, {})[plain(label)] = link.removesuffix(".html")
    return out


def pdf_text(artifact: raw.Artifact) -> str:
    out = DATA / "parsed" / SOURCE / f"{artifact.sha256}.txt"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        text = subprocess.run(
            ["pdftotext", "-layout", str(artifact.path), "-"], check=True, capture_output=True, text=True
        ).stdout
        out.write_text(text, "utf-8")
    return out.read_text("utf-8")


def amount(token: str) -> float:
    return float(token.replace(",", ""))


def is_number(token: str) -> bool:
    return re.fullmatch(r"\d[\d,]*(?:\.\d+)?|\d", token) is not None


def split(line: str) -> tuple[str, list[str]]:
    """A report line as its leading label and the run of numbers that ends it."""
    tokens = line.split()
    end = len(tokens)
    while end and is_number(tokens[end - 1]):
        end -= 1
    return " ".join(tokens[:end]), tokens[end:]


def read_1994(text: str) -> list[Department]:
    codes = ["ARENA", "FMLN", "CD", "MNR", "MAC", "MU", "MSN", "PCN", "PDC"]
    out: list[Department] = []
    for line in text.splitlines():
        if re.match(r"\s*DEPARTAMENTO [A-ZÑ ]+$", line):
            out.append(Department(len(out) + 1))
            continue
        label, numbers = split(line)
        if not out or len(numbers) != 14:
            continue
        values = [amount(n) for n in numbers]
        row = Row(
            re.sub(r"^\d+\s+", "", label),
            dict(zip(codes, values[:9], strict=True)),
            int(values[9]),
            int(values[11]),
            int(values[10]),
        )
        if label.startswith("TOTAL"):
            out[-1].total = row
        else:
            out[-1].rows.append(row)
    return out


def read_2012(text: str) -> list[Department]:
    codes = ["ARENA", "CD", "CN", "FMLN", "FPS", "GANA", "PES", "PNL", "PP", "CN/PES", "PES/CN"]
    codes += ["HUEZO", "MORAN", "GARCIA", "GOMAR", "RUBIO"]
    numbers_of = departments()
    out: dict[int, Department] = {}
    current: Department | None = None
    for line in text.splitlines():
        header = re.match(r"\s*DEPARTAMENTO:\s+([A-ZÑ ]+?)\s{2,}", line)
        if header:
            number = numbers_of[plain(header.group(1))]
            current = out.setdefault(number, Department(number))
            continue
        label, numbers = split(line)
        if current is None or len(numbers) != 25:
            continue
        values = [amount(n) for n in numbers]
        row = Row(label, dict(zip(codes, values[:16], strict=True)), int(values[16]), int(values[18]), int(values[17]))
        if label.startswith("Totales"):
            current.total = row
        else:
            current.rows.append(row)
    for department in out.values():
        assert department.total is not None
        # Where a coalition of CN and PES ran, its column holds the list's votes and the CN and PES columns only
        # split them between the two parties; elsewhere the coalition columns are empty and CN and PES ran alone.
        coalition = any(department.total.votes[c] for c in ("CN/PES", "PES/CN"))
        drop = ("CN", "PES") if coalition else ("CN/PES", "PES/CN")
        for row in [*department.rows, department.total]:
            for code in drop:
                del row.votes[code]
    return [out[number] for number in sorted(out)]


def read_2015(text: str) -> list[Department]:
    codes = [
        "ARENA",
        "CD",
        "DS",
        "FMLN",
        "GANA",
        "PCN",
        "PDC",
        "PSD",
        "ARENA / PCN",
        "PCN / DS",
        "PCN / PDC",
        "NO PDO.",
    ]
    out: dict[int, Department] = {}
    current: Department | None = None
    pending: str | None = None
    expect_header = False
    for line in text.splitlines():
        if "RECUENTO DEL DEPARTAMENTO" in line:
            expect_header = True
            continue
        if expect_header and line.strip():
            number, _, _ = line.strip().partition(" ")
            current = out.setdefault(int(number), Department(int(number)))
            expect_header = False
            continue
        _, numbers = split(line)
        if current is None:
            continue
        if len(numbers) >= 21:
            values = [amount(n) for n in numbers]
            row = Row(pending or "", dict(zip(codes, values[:12], strict=True)), int(values[13]), int(values[15]), 0)
            row.objected = int(values[14])
            if pending is None:
                current.total = row
            else:
                current.rows.append(row)
            pending = None
        elif re.match(r"\d+\s+[A-ZÁÉÍÓÚÑ]", line.strip()) and not line.strip().startswith("Página"):
            pending = re.sub(r"^\d+\s+", "", line.strip())
    return [out[number] for number in sorted(out)]


READERS: dict[int, Callable[[str], list[Department]]] = {1994: read_1994, 2012: read_2012, 2015: read_2015}


def tables(election: Election) -> tuple[list[Department], list[raw.Artifact]]:
    artifacts = [raw.cached(SOURCE, url) for url in election.files]
    return READERS[election.day.year]("\n".join(pdf_text(a) for a in artifacts)), artifacts


def key(kind: str, *parts: object) -> str:
    return tse.key(kind, "assembly", *parts)


def party(code: str) -> str:
    """A party's id: the source-wide id that every sv.tse package uses for the same printed code."""
    return tse.key("party", code)


def codes_in(election: list[Department]) -> list[str]:
    seen: dict[str, None] = {}
    for department in election:
        for row in [*department.rows, *([department.total] if department.total else [])]:
            for code, value in row.votes.items():
                if value:
                    seen.setdefault(code)
    return list(seen)


def locate(number: int, name: str, known: dict[int, dict[str, str]]) -> str:
    """The code of the municipality a report names, from the 2014 list of 262."""
    label = plain(name)
    if label not in known[number]:
        label = ALIASES.get(label, label)
    return known[number][label]


def read_seats(text: str) -> dict[int, tuple[int, dict[str, int]]]:
    """Seats by department in 2012: the department's magnitude and each list's total of seats."""
    out: dict[int, tuple[int, dict[str, int]]] = {}
    number = 0
    magnitude = 0
    for line in text.splitlines():
        head = re.match(r"(\d+)\s+[A-ZÑ ]+\s{3,}\d\d/\d\d/\d{4}", line)
        if head:
            number = int(head.group(1))
            continue
        size = re.search(r"Cantidad de Diputados para el Departamento:\s+(\d+)", line)
        if size:
            magnitude = int(size.group(1))
            out[number] = (magnitude, {})
            continue
        label, numbers = split(line)
        if number and len(numbers) == 6 and not label.startswith("Totales"):
            out[number][1][label.upper()] = int(numbers[5])
    return out


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Legislative Assembly of El Salvador, its electoral systems, and the parties and independent candidates on "
        "its departmental lists, 1994 to 2015",
        depends=["sv.tse/geo", "sv.tse/registry"],
    )
    p.external |= {tse.EL_SALVADOR, tse.TERRITORY}
    p.add("body", id=BODY, polity_id=tse.EL_SALVADOR, kind="unicameral")
    p.name(BODY, "Legislative Assembly of El Salvador", "en", "official")
    p.name(BODY, "Asamblea Legislativa de la República de El Salvador", "es", "native", False)
    for era, since, until, order, text in SYSTEMS:
        id = key("electoral_system", era)
        p.add(
            "electoral_system",
            id=id,
            polity_id=tse.EL_SALVADOR,
            valid_from=since,
            valid_to=until,
            rules={"formula": "hare", "lists": {"order": order, "vacancy": "next_on_list"}},
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    codes: dict[str, None] = {}
    for election in ELECTIONS.values():
        departments_, _ = tables(election)
        codes.update(dict.fromkeys(codes_in(departments_)))
    for code in codes:
        if code in INDEPENDENTS or "/" in code:
            continue
        id = party(code)
        if code in tse.PARTIES:
            continue  # sv.tse/registry already declares this party and its code
        p.add("party", id=id, kind="party", polity_id=tse.EL_SALVADOR)
        p.add("external_id", entity_id=id, scheme="sv.tse.party", value=code)
        p.name(id, PARTY_NAMES.get(code, code), "es", "official")
        if code in PARTY_NAMES:
            p.name(id, code, "es", "abbreviation", False)
    return p


SYSTEMS = [
    (
        "closed",
        date(1991, 1, 1),
        date(2012, 1, 1),
        "closed",
        (
            "Closed party lists in departmental constituencies. Seats are allocated among the lists of a department "
            "by the electoral quotient and largest remainders (Código Electoral, art. 217)."
        ),
    ),
    (
        "preferential",
        date(2012, 1, 1),
        None,
        "open",
        (
            "Departmental lists on which voters mark the candidates they prefer, which orders the winners within "
            "each list. Seats are allocated among the lists of a department by the electoral quotient and largest "
            "remainders."
        ),
    ),
]


def system(day: date) -> str:
    return key("electoral_system", "preferential" if day.year >= 2012 else "closed")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", plain(text).lower()).strip("-")


def label(code: str) -> str:
    return code.title() if code in INDEPENDENTS else code


# The packages that already declare a reporting unit for every municipality on an election day, with the key
# namespace that declares them (None for the plain source-wide key). An assembly package reuses their units, and
# depends on the package, instead of declaring the same "<day>:<municipality>" codes again.
UNIT_HOLDERS: dict[date, tuple[str, str | None]] = {
    date(1994, 3, 20): ("sv.tse/pres-1994-03-20", None),
    date(2012, 3, 11): ("sv.tse/council-2012-03-11", "council"),
    date(2015, 3, 1): ("sv.tse/council-2015-03-01", "council"),
}


def unit_key(day: date, code: str) -> str:
    holder = UNIT_HOLDERS.get(day)
    if holder is None:
        return key("unit", day, code)
    return tse.key("unit", day, code) if holder[1] is None else tse.key("unit", holder[1], day, code)


def assembly(day: date) -> Package:
    election = ELECTIONS[day]
    deps, artifacts = tables(election)
    known = municipalities()
    names = {number: name for name, number in departments().items()}
    seats = read_seats(pdf_text(raw.cached(SOURCE, election.seats))) if election.seats else {}
    p = Package(
        f"sv.tse/assembly-{day}",
        SOURCE,
        f"The {day.year} election of deputies to the Legislative Assembly of El Salvador, by municipality, from the "
        "Tribunal Supremo Electoral",
        depends=["sv.tse/geo", "sv.tse/registry", REGISTRY, *([UNIT_HOLDERS[day][0]] if day in UNIT_HOLDERS else [])],
    )
    p.external |= {tse.EL_SALVADOR, tse.TERRITORY}
    source = key("source", day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Tribunal Supremo Electoral de El Salvador",
        title=f"Escrutinio final de las elecciones de diputados a la Asamblea Legislativa {day.year}",
        url=LISTING,
        license="Official public results of the Tribunal Supremo Electoral",
        license_url="https://transparencia.tse.gob.sv/",
        attribution="Tribunal Supremo Electoral de El Salvador",
    )
    for artifact in artifacts:
        p.artifact(artifact, source)
    if election.seats:
        p.artifact(raw.cached(SOURCE, election.seats), source)
    id = key("election", day)
    p.add(
        "election",
        id=id,
        polity_id=tse.EL_SALVADOR,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
    )
    path = f"sv/{day}-legislative"
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{day.year} Salvadoran legislative election", "en", "official")
    p.name(id, f"Elección de diputados a la Asamblea Legislativa {day.year}", "es", "native", False)
    units: dict[str, str] = {}
    facts: list[dict[str, object]] = []
    for department in deps:
        name = names[department.number].title()
        contest = key("contest", day, department.number)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="list_allocation",
            body_id=BODY,
            district_area_id=tse.area("sv.departamento", f"dep{department.number:02d}"),
            magnitude=seats[department.number][0] if seats else None,
            round_number=1,
            electoral_system_id=system(day),
            default_result_kind="counted",
        )
        p.name(contest, f"{day.year} Legislative Assembly, {name}", "en", "official")
        p.name(contest, f"Diputados por {name} {day.year}", "es", "native", False)
        p.add("slug", path=f"{path}/{slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, department.number)
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
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method(day), lang="en"
        )
        assert department.total is not None
        running = [c for c in codes_in([department])]
        whole = all(v == int(v) for r in [*department.rows, department.total] for v in r.votes.values())
        options: dict[str, str] = {}
        for position, code in enumerate(running, 1):
            option = options[code] = key("option", day, department.number, code)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=None if code in INDEPENDENTS or "/" in code else party(code),
                ballot_position=position,
                ballot_label=label(code),
            )
            won = seats.get(department.number, (0, {}))[1].get(code, 0)
            if won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
            if whole:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=option,
                    measure=VOTES,
                    value=int(department.total.votes[code]),
                )
        for measure, amount_ in (
            (VALID, department.total.valid),
            (INVALID, department.total.invalid),
            (OBJECTED, department.total.objected),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=amount_)

        def value(unit: str, option: str | None, measure: int, amount_: int, set_: str = result_set) -> None:
            facts.append(
                {
                    "result_set_id": set_,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "flags": 0,
                    "value": amount_,
                }
            )

        for row in department.rows:
            code = locate(department.number, row.name, known)
            if code not in units:
                unit = units[code] = unit_key(day, code)
                if day not in UNIT_HOLDERS:
                    p.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=tse.EL_SALVADOR,
                        scheme_code="sv.municipio",
                        code=f"{day}:{code.removeprefix('mun')}",
                        area_relation="equals",
                        area_id=tse.area("sv.municipio", code),
                        kind="aggregate",
                    )
                    p.name(unit, row.name.title(), "es", "official")
            unit = units[code]
            counts = apportion(row, running)
            for option_code, option in options.items():
                value(unit, option, VOTES, counts[option_code])
            value(unit, None, VALID, row.valid)
            value(unit, None, INVALID, row.invalid)
            value(unit, None, OBJECTED, row.objected)
    p.frame("result_value", pl.DataFrame(facts))
    return p


def apportion(row: Row, codes: list[str]) -> dict[str, int]:
    """Whole votes for each list: the printed counts, or fractional shares rounded so that they sum to the valid votes."""
    shares = {code: row.votes.get(code, 0.0) for code in codes}
    if all(value == int(value) for value in shares.values()):
        return {code: int(value) for code, value in shares.items()}
    floors = {code: int(value) for code, value in shares.items()}
    short = row.valid - sum(floors.values())
    for code in sorted(codes, key=lambda c: floors[c] - shares[c])[: max(short, 0)]:
        floors[code] += 1
    return floors


def method(day: date) -> str:
    text = (
        f"Final scrutiny (escrutinio final) of the {day.year} election of deputies to the Legislative Assembly as "
        "published by the Tribunal Supremo Electoral, one row per municipality in each department's consolidated "
        "report. Votes are valid votes for each party's list; nulos are stored as invalid ballots and impugnados as "
        "objected ballots. The source also lists abstentions and unused ballot papers, which are not stored."
    )
    if day.year == 2012:
        text += (
            " Votes for the CN and PES coalition marks are already counted in the CN and PES columns and are not "
            "stored again. Independent candidates appear as lists of one."
        )
    if day.year == 2015:
        text += (
            " The report is the recount of the departments after the Sala de lo Constitucional's amparo 117-2015 "
            "and gives each list's votes as fractions, because votes cast across lists are shared among them. Each "
            "municipality's fractions are rounded to whole votes that sum to its valid votes, so a list's vote can "
            "differ from the printed fraction by less than one."
        )
    return text


def fetch() -> None:
    for election in ELECTIONS.values():
        for url in (*election.files, *([election.seats] if election.seats else [])):
            raw.fetch(SOURCE, url, suffix=".pdf")


def packages() -> list[str]:
    return [REGISTRY, *(f"sv.tse/assembly-{day}" for day in ELECTIONS)]


def build(name: str) -> Package:
    return registry() if name == REGISTRY else assembly(date.fromisoformat(name.removeprefix("sv.tse/assembly-")))
