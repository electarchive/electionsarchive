"""Elections of El Salvador's deputies to the Central American Parliament, 1994, 2000, 2006, 2009, and 2015.

The Tribunal Supremo Electoral publishes these results as scrutiny reports. Each election is one package with a single
national contest of 20 seats. The 1994, 2000, and 2006 results are the national totals in the final scrutiny reports
(scans, transcribed here and checked against each report's own stated total). The 2009 results are the departmental
consolidation, a scan transcribed here and checked against the printed national totals. The 2015 results are the
municipal consolidation, which has a text layer and is read with `pdftotext`.
"""

import re
from dataclasses import dataclass, field
from datetime import date

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.sv import assembly, presidents, tse

SOURCE = tse.SOURCE
DOCS = assembly.DOCS
LISTING = assembly.LISTING
VOTES, VALID, INVALID, OBJECTED = tse.VOTES, tse.VALID, tse.INVALID, tse.OBJECTED
BODY = tse.key("body", "parlacen")
REGISTRY = "sv.tse/parlacen-registry"
SEATS = 20

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
    "CDU": "Centro Democrático Unido",
    "PAN": "Partido Acción Nacional",
    "USC": "Unión Social Cristiana",
    "PLD": "Partido Liberal Democrático",
    "PPL": "Partido Popular Laborista",
    "FDR": "Frente Democrático Revolucionario",
    "GANA": "Gran Alianza por la Unidad Nacional",
    "PSD": "Partido Social Demócrata",
    "FPS": "Frente Parlamentario Salvadoreño",
    "PSP": "Partido Salvadoreño Progresista",
}
EXTRA_NAMES = {"CD": ["Cambio Democrático"]}
COALITIONS = {"CDU"}

D2009 = DOCS + "estadisticos/2009/consolidadodepartamentalparlacen2009.pdf"
N2009 = DOCS + "estadisticos/2009/consolidadonacionalparlacen2009.pdf"
ACT = DOCS + "Actas_de_Escrutinio/"
M2015 = (
    DOCS + "Consolidado%20de%20votos%20PARLACEN,%20por%20municipio,%20departamento%20y%20pp,%20elecciones%202015.pdf"
)
Q2015 = DOCS + "estadisticos/2015/Votos%20cociente%20y%20residuos%20PARLACEN2015.pdf"


@dataclass
class Election:
    day: date
    files: tuple[str, ...]
    votes: dict[str, int]
    seats: dict[str, int]
    valid: int
    invalid: int | None = None
    objected: int | None = None
    departments: list[tuple[int, dict[str, int], int, int, int]] = field(default_factory=list)
    municipal: bool = False


# 2009 departmental consolidation: department number, votes by party, valid, nulos, impugnados.
DEPARTMENTS_2009 = [
    (1, (268809, 17803, 12369, 330800, 20593, 18951), 669325, 4395, 459),
    (2, (68503, 1243, 2274, 70355, 18042, 20172), 180589, 3487, 267),
    (3, (39618, 444, 1239, 68127, 7198, 34086), 150712, 3054, 272),
    (4, (104400, 4952, 2020, 102437, 19526, 13816), 247151, 2983, 330),
    (5, (45891, 1400, 1819, 58157, 13185, 4353), 124805, 2493, 253),
    (6, (56002, 9002, 1867, 62991, 19012, 8288), 157162, 3085, 307),
    (7, (33358, 441, 282, 24514, 19411, 9117), 87123, 2858, 190),
    (8, (38481, 9580, 1276, 45227, 16612, 6973), 118149, 2505, 203),
    (9, (31244, 150, 410, 31154, 9169, 8081), 80208, 1658, 152),
    (10, (38696, 493, 318, 38822, 16745, 2138), 97212, 1337, 190),
    (11, (48721, 698, 623, 40677, 13744, 13117), 117580, 2317, 278),
    (12, (28526, 220, 488, 26297, 7007, 9359), 71897, 1911, 180),
    (13, (22676, 422, 639, 26247, 8703, 4612), 63299, 1182, 141),
    (14, (29241, 123, 118, 18131, 5804, 591), 54008, 938, 152),
]
CODES_2009 = ("ARENA", "CD", "FDR", "FMLN", "PCN", "PDC")

ELECTIONS = {
    e.day: e
    for e in (
        Election(
            date(1994, 3, 20),
            (ACT + "Acta%20de%20Escrutinio%20Final%20de%20Eleccion%20de%20Diputados%20al%20PARLACEN%20de%201994.pdf",),
            {
                "ARENA": 605775,
                "FMLN": 287811,
                "CD": 59843,
                "MNR": 9431,
                "MAC": 12109,
                "MU": 33510,
                "MSN": 12827,
                "PCN": 83520,
                "PDC": 240451,
            },
            {"ARENA": 9, "FMLN": 4, "CD": 1, "MU": 1, "PCN": 1, "PDC": 4},
            1345277,
        ),
        Election(
            date(2000, 3, 12),
            (
                ACT + "Actas%20de%20Escrutinio%20Final%20de%20Diputados%20a%20la%20A.L,%20PARLACEN%20y%20C.M."
                "%20del%20a%C3%B1o%202000.pdf",
            ),
            {
                "ARENA": 436190,
                "FMLN": 426298,
                "PCN": 106804,
                "PDC": 87078,
                "CDU": 65072,
                "PAN": 44901,
                "USC": 23333,
                "PLD": 15639,
                "PPL": 4998,
            },
            {"ARENA": 7, "FMLN": 7, "PCN": 2, "PDC": 2, "CDU": 1, "PAN": 1},
            1210313,
        ),
        Election(
            date(2006, 3, 12),
            (
                ACT + "Actas%20de%20Escrutinio%20Final%20de%20Elecciones%20de%20Diputados%20a%20la%20A.L.%20y%20de"
                "%20C.M.%20de%202006.pdf",
            ),
            {"FMLN": 785072, "ARENA": 783230, "PCN": 228196, "PDC": 138538, "CD": 61022},
            {"ARENA": 8, "FMLN": 8, "PCN": 2, "PDC": 1, "CD": 1},
            1996058,
        ),
        Election(
            date(2009, 1, 18),
            (D2009, N2009),
            dict(zip(CODES_2009, (854166, 46971, 25742, 943936, 194751, 153654), strict=True)),
            {"FMLN": 9, "ARENA": 8, "PCN": 2, "PDC": 1},
            2219220,
            34203,
            3374,
            [(n, dict(zip(CODES_2009, v, strict=True)), a, b, c) for n, v, a, b, c in DEPARTMENTS_2009],
        ),
        Election(
            date(2015, 3, 1),
            (M2015, Q2015),
            {},
            {"ARENA": 8, "FMLN": 8, "GANA": 2, "PCN": 1, "PDC": 1},
            2146329,
            municipal=True,
        ),
    )
}
CODES_2015 = ("ARENA", "CD", "FMLN", "GANA", "PCN", "PDC", "PSD", "FPS", "PSP")


@dataclass
class Municipality:
    department: int
    name: str
    votes: dict[str, float]
    valid: int
    invalid: int
    objected: int


def key(kind: str, *parts: object) -> str:
    return tse.key(kind, "parlacen", *parts)


def party(code: str) -> str:
    """A party's id: the source-wide id that every sv.tse package uses for the same printed code."""
    return tse.key("party", code)


def held() -> dict[str, str]:
    """The party codes that loaded sv.tse packages already declare, each with the package declaring it."""
    out = dict.fromkeys(tse.PARTIES, "sv.tse/registry")
    for code in sorted({code for e in presidents.ELECTIONS.values() for code in e.parties}):
        out.setdefault(code, presidents.REGISTRY)
    return out


def codes() -> list[str]:
    """Every party code on the PARLACEN ballots, in first-seen order."""
    seen: dict[str, None] = {}
    for election in ELECTIONS.values():
        seen.update(dict.fromkeys(election.votes))
    seen.update(dict.fromkeys(CODES_2015))
    return list(seen)


def holders() -> list[str]:
    """The earlier packages that declare parties on the PARLACEN ballots."""
    return sorted({held()[code] for code in codes() if code in held()})


def read_2015(text: str) -> tuple[list[Municipality], dict[int, tuple[dict[str, float], int, int, int]]]:
    """Municipal rows and department totals of the 2015 consolidation."""
    municipalities: list[Municipality] = []
    totals: dict[int, tuple[dict[str, float], int, int, int]] = {}
    department = 0
    pending: str | None = None
    for line in text.splitlines():
        head = re.fullmatch(r"\s{8,}(\d+) ([A-ZÁÉÍÓÚÑ ]+)", line.rstrip())
        if head:
            department = int(head.group(1))
            continue
        _, numbers = assembly.split(line)
        if len(numbers) >= 19:
            values = [assembly.amount(n) for n in numbers]
            votes = dict(zip(CODES_2015, values[:9], strict=True))
            valid, objected, invalid = round(values[10]), int(values[11]), int(values[12])
            if pending is None:
                totals[department] = (votes, valid, invalid, objected)
            else:
                municipalities.append(Municipality(department, pending, votes, valid, invalid, objected))
            pending = None
        elif department and re.match(r"\d+ [A-ZÁÉÍÓÚÑ]", line.strip()) and not line.strip().startswith("Página"):
            pending = re.sub(r"^\d+ ", "", line.strip())
    return municipalities, totals


def fetch() -> None:
    for election in ELECTIONS.values():
        for url in election.files:
            raw.fetch(SOURCE, url, suffix=".pdf")


def packages() -> list[str]:
    return [REGISTRY, *(f"sv.tse/parlacen-{day}" for day in ELECTIONS)]


def build(name: str) -> Package:
    if name == REGISTRY:
        return registry()
    return parlacen(date.fromisoformat(name.removeprefix("sv.tse/parlacen-")))


SYSTEMS = [
    (
        "closed",
        date(1994, 1, 1),
        date(2012, 1, 1),
        "closed",
        (
            "El Salvador's deputies to the Central American Parliament are elected from one national constituency of "
            "20 seats on closed party lists. Seats are allocated among the lists by the electoral quotient (valid "
            "votes divided by 20) and largest remainders."
        ),
    ),
    (
        "preferential",
        date(2012, 1, 1),
        None,
        "open",
        (
            "One national constituency of 20 seats. Voters may mark candidates across lists, which orders the winners "
            "within each list, and a vote for a candidate of another list is shared among the lists. Seats are "
            "allocated by the electoral quotient and largest remainders."
        ),
    ),
]


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "El Salvador's delegation to the Central American Parliament, its electoral systems, and the parties on its "
        "national lists, 1994 to 2015",
        depends=["sv.tse/geo", *holders()],
    )
    p.external |= {tse.EL_SALVADOR, tse.TERRITORY}
    p.add("scheme", code="sv.pais", polity_id=tse.EL_SALVADOR, kind="administrative", rank=0, noun="country")
    p.add("body", id=BODY, polity_id=tse.EL_SALVADOR, kind="unicameral")
    p.name(BODY, "Salvadoran deputies to the Central American Parliament", "en", "official")
    p.name(BODY, "Diputados de El Salvador al Parlamento Centroamericano", "es", "native", False)
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
    for code in codes():
        if code in held():
            continue  # an earlier sv.tse package already declares this party and its code
        id = party(code)
        p.add("party", id=id, kind="list_vehicle" if code in COALITIONS else "party", polity_id=tse.EL_SALVADOR)
        p.add("external_id", entity_id=id, scheme="sv.tse.party", value=code)
        p.name(id, PARTY_NAMES[code], "es", "official")
        for other in EXTRA_NAMES.get(code, []):
            p.name(id, other, "es", "official", False)
        p.name(id, code, "es", "abbreviation", False)
    return p


def system(day: date) -> str:
    return key("electoral_system", "preferential" if day.year >= 2012 else "closed")


def method(election: Election) -> str:
    year = election.day.year
    text = (
        f"Final scrutiny (escrutinio final) of the {year} election of El Salvador's 20 deputies to the Central "
        "American Parliament as published by the Tribunal Supremo Electoral. Votes are valid votes for each party's "
        "national list."
    )
    if year in (1994, 2000, 2006):
        text += (
            " The report is a scan; the national totals by party were transcribed from it and each total equals the "
            "sum of the parties' votes. The report states the valid votes but the other votes for this contest only "
            "as part of the assembly totals, so they are not stored."
        )
    if year == 2009:
        text += (
            " The consolidation is a scan whose department rows were transcribed from it; they sum to the printed "
            "national totals. Nulos are stored as invalid ballots and impugnados as objected ballots. The source "
            "also lists abstentions and ballot papers, which are not stored. The consolidation by municipality is a "
            "separate scan that is not read here."
        )
    if year == 2015:
        text += (
            " The report is the municipal consolidation, which gives each list's votes as fractions because votes "
            "cast across lists are shared among them. Each municipality's fractions are rounded to whole votes "
            "that sum to its valid votes, so a list's vote can differ from the printed fraction by less than one. "
            "National votes by party are not declared as whole numbers, for the same reason; the valid total and "
            "the seats are as the Tribunal states them."
        )
    return text


def parlacen(day: date) -> Package:
    election = ELECTIONS[day]
    p = Package(
        f"sv.tse/parlacen-{day}",
        SOURCE,
        f"The {day.year} election of El Salvador's deputies to the Central American Parliament, from the Tribunal "
        "Supremo Electoral",
        depends=["sv.tse/geo", *holders(), REGISTRY],
    )
    p.external |= {tse.EL_SALVADOR, tse.TERRITORY}
    source = key("source", day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Tribunal Supremo Electoral de El Salvador",
        title=f"Escrutinio final de las elecciones de diputados al Parlamento Centroamericano {day.year}",
        url=LISTING,
        license="Official public results of the Tribunal Supremo Electoral",
        license_url="https://transparencia.tse.gob.sv/",
        attribution="Tribunal Supremo Electoral de El Salvador",
    )
    for url in election.files:
        p.artifact(raw.cached(SOURCE, url), source)
    id = key("election", day)
    p.add("election", id=id, polity_id=tse.EL_SALVADOR, kind="general", date_from=day, date_to=day, status="held")
    path = f"sv/{day}-parlacen"
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{day.year} Salvadoran election of deputies to the Central American Parliament", "en", "official")
    p.name(id, f"Elección de diputados al Parlamento Centroamericano {day.year}", "es", "native", False)
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        body_id=BODY,
        district_area_id=tse.TERRITORY,
        magnitude=SEATS,
        round_number=1,
        electoral_system_id=system(day),
        default_result_kind="counted",
    )
    p.name(contest, f"{day.year} Central American Parliament, El Salvador", "en", "official")
    p.name(contest, f"Diputados al PARLACEN {day.year}", "es", "native", False)
    p.add("slug", path=f"{path}/national", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
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
        body_md=method(election),
        lang="en",
    )

    municipalities: list[Municipality] = []
    if election.municipal:
        municipalities, _ = read_2015(assembly.pdf_text(raw.cached(SOURCE, M2015)))
        codes = list(CODES_2015)
    else:
        codes = list(election.votes)
    options: dict[str, str] = {}
    for position, code in enumerate(codes, 1):
        option = options[code] = key("option", day, code)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(code),
            ballot_position=position,
            ballot_label=code,
        )
        won = election.seats.get(code, 0)
        if won:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
        if election.votes:
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=election.votes[code])
    declared: list[tuple[int, int | None]] = [
        (VALID, election.valid),
        (INVALID, election.invalid),
        (OBJECTED, election.objected),
    ]
    for measure, amount_ in declared:
        if amount_ is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=amount_)

    facts: list[dict[str, object]] = []

    def value(unit: str, option: str | None, measure: int, amount_: int) -> None:
        facts.append(
            {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "flags": 0,
                "value": amount_,
            }
        )

    def unit_for(scheme: str, code: str, area_code: str, label: str) -> str:
        unit = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=tse.EL_SALVADOR,
            scheme_code=scheme,
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=tse.TERRITORY if scheme == "sv.pais" else tse.area(scheme, area_code),
            kind="aggregate",
        )
        p.name(unit, label, "es", "official")
        return unit

    if not election.departments and not municipalities:
        unit = unit_for("sv.pais", "pais", "SLV", "El Salvador")
        for option_code, option in options.items():
            value(unit, option, VOTES, election.votes[option_code])
        value(unit, None, VALID, election.valid)
    if election.departments:
        names = tse.tree(1, with_centers=False)[0]
        for number, votes, valid, invalid, objected in election.departments:
            code = f"dep{number:02d}"
            unit = unit_for("sv.departamento", code, code, names[code].title())
            for option_code, option in options.items():
                value(unit, option, VOTES, votes[option_code])
            value(unit, None, VALID, valid)
            value(unit, None, INVALID, invalid)
            value(unit, None, OBJECTED, objected)
    if municipalities:
        known = assembly.municipalities()
        for row in municipalities:
            code = assembly.locate(row.department, row.name, known)
            unit = unit_for("sv.municipio", code.removeprefix("mun"), code, row.name.title())
            counts = assembly.apportion(
                assembly.Row(row.name, row.votes, row.valid, row.invalid, row.objected), list(options)
            )
            for option_code, option in options.items():
                value(unit, option, VOTES, counts[option_code])
            value(unit, None, VALID, row.valid)
            value(unit, None, INVALID, row.invalid)
            value(unit, None, OBJECTED, row.objected)
    if facts:
        p.frame("result_value", pl.DataFrame(facts))
    return p
