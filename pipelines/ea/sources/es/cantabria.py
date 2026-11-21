"""Parlamento de Cantabria elections, 1983 to 2023, from ICANE's open data.

The Instituto Cántabro de Estadística (ICANE) publishes two tables of the regional elections that together hold
every list and the seats: votes and seats by list ("Votos y Escaños por Partido Político") and the electorate,
participation, blank and null ballots and seats to allocate ("Datos Elecciones Autonómicas"). Cantabria is a single
constituency, so the community row is the constituency's result. ICANE's votes-by-municipality table folds minor lists
into "Otros", has no 2011 election, and carries no turnout or seats, so it is not loaded.
"""

import csv
import io
from collections import Counter
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package

SOURCE = "es.cantabria"
BASE = "https://www.icane.es/data/api/"
VOTES_TABLE = BASE + "elecciones-regional-votos-escanos-partido-politico.csv"
DATA_TABLE = BASE + "elecciones-datos-regionales.csv"
PORTAL = "https://www.icane.es/data/elecciones-regional-votos-escanos-partido-politico"
PUBLISHER = "Instituto Cántabro de Estadística (ICANE)"
TITLE = "Elecciones autonómicas: votos y escaños por partido político, y datos de participación (ICANE)"
STATUTE = "Ley Orgánica 8/1981, de 30 de diciembre, de Estatuto de Autonomía para Cantabria"
SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
REGION = str(uuid5(package.NAMESPACE, "polity\x1fES-CB"))
# The archive's province area and reporting unit for Cantabria, which are the whole constituency.
CANTABRIA_AREA = "8995ac71-cbf8-517e-b50f-e5b33c8c9c27"
CANTABRIA_UNIT = "0dcd8e2f-ac2e-5584-96ab-b5f3bb574f24"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
THRESHOLD = 5
DAYS = {
    1983: date(1983, 5, 8),
    1987: date(1987, 6, 10),
    1991: date(1991, 5, 26),
    1995: date(1995, 5, 28),
    1999: date(1999, 6, 13),
    2003: date(2003, 5, 25),
    2007: date(2007, 5, 27),
    2011: date(2011, 5, 22),
    2015: date(2015, 5, 24),
    2019: date(2019, 5, 26),
    2023: date(2023, 5, 28),
}
SYSTEM = (
    "Closed party lists in a single constituency, the whole of Cantabria. Seats go by the D'Hondt method to the lists "
    "with at least 5% of the valid votes cast in the community, blank ballots counted as valid. The Parlamento de "
    "Cantabria had 35 members in 1983, 39 from 1987 to 2011, and has had 35 since 2015 (" + STATUTE + ")."
)
METHOD = (
    "List votes and seats for the whole of Cantabria from ICANE's votes-and-seats table, with the electorate, ballots "
    "cast, blank ballots, and null ballots from ICANE's regional election data table. Valid votes here are the votes "
    "to candidacies, with blank ballots stored apart."
)
# ICANE's names for the lists, where the abbreviation alone would be opaque. Others are stored under the
# abbreviation ICANE prints.
NAMES = {
    "AP-PDP-UL": "Coalición Popular (AP-PDP-UL)",
    "CDS": "Centro Democrático y Social",
    "IU": "Izquierda Unida",
    "PCE": "Partido Comunista de España",
    "PCPE": "Partido Comunista de los Pueblos de España",
    "PP": "Partido Popular",
    "PRC": "Partido Regionalista de Cantabria",
    "PSOE": "Partido Socialista Obrero Español",
    "PTE-UC": "Partido de los Trabajadores de España-Unidad Comunista",
    "UPCA": "Unión para el Progreso de Cantabria",
    "C'S": "Ciudadanos-Partido de la Ciudadanía",
    "PODEMOS": "Podemos",
    "PACMA": "Partido Animalista Contra el Maltrato Animal",
    "VOX": "Vox",
    "EQUO": "Equo",
    "UPD": "Unión Progreso y Democracia",
    "CANTABRISTAS": "Cantabristas",
    "Ola Cantabria": "Ola Cantabria",
}


def key(abbreviation: str) -> str:
    return slug(abbreviation.replace("'", ""))


def fetch() -> None:
    raw.fetch(SOURCE, VOTES_TABLE, suffix=".csv")
    raw.fetch(SOURCE, DATA_TABLE, suffix=".csv")


def table(url: str) -> list[list[str]]:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("utf-8")
    return list(csv.reader(io.StringIO(text), delimiter=";"))


class Election:
    def __init__(self, year: int, votes: dict[str, int], seats: dict[str, int], data: dict[str, int]) -> None:
        self.year, self.votes, self.seats, self.data = year, votes, seats, data


def load() -> list[Election]:
    rows = table(VOTES_TABLE)
    head = rows[0]
    out: dict[int, tuple[dict[str, int], dict[str, int]]] = {}
    for row in rows[1:]:
        votes: dict[str, int] = {}
        seats: dict[str, int] = {}
        for column, cell in zip(head[1:], row[1:], strict=True):
            if not cell:
                continue
            name, kind = column.rsplit(" - ", 1)
            (votes if kind == "Votos" else seats)[name] = int(float(cell))
        out[int(row[0])] = (votes, seats)
    data_rows = table(DATA_TABLE)
    fields = data_rows[0][1:]
    data = {
        int(r[0]): {f: int(float(c)) for f, c in zip(fields, r[1:], strict=True) if f != "% Participación"}
        for r in data_rows[1:]
    }
    if sorted(out) != sorted(DAYS) or sorted(data) != sorted(DAYS):
        raise ValueError(f"years {sorted(out)} and {sorted(data)}")
    return [Election(y, *out[y], data[y]) for y in sorted(DAYS)]


def dhondt(votes: dict[str, int], valid: int, seats: int) -> Counter[str]:
    eligible = {k: v for k, v in votes.items() if v * 100 >= THRESHOLD * valid}
    quotients = sorted(
        ((v / d, v, k) for k, v in eligible.items() for d in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(k for *_, k in quotients[:seats])


def source_id() -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1ficane"))


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PORTAL,
        attribution="Fuente: Instituto Cántabro de Estadística (ICANE), a partir de los datos de la Junta Electoral.",
        notes="ICANE's open-data portal publishes these tables for reuse with attribution.",
    )


def registry(elections: list[Election]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Parlamento de Cantabria, its electoral system and size, ICANE's tables as a source, and the lists that "
        "stood in the elections of 1983 to 2023",
    )
    p.external.add(REGION)
    add_source(p)
    p.uses(raw.cached(SOURCE, VOTES_TABLE))
    p.uses(raw.cached(SOURCE, DATA_TABLE))
    body = p.id("body", "parlamento")
    p.add("body", id=body, polity_id=REGION, kind="unicameral")
    p.name(body, "Parliament of Cantabria", "en", "official")
    p.name(body, "Parlamento de Cantabria", "es", "native")
    p.add("body_size", body_id=body, seats=35, valid_from=DAYS[1983], valid_to=DAYS[1987])
    p.add("body_size", body_id=body, seats=39, valid_from=DAYS[1987], valid_to=DAYS[2015])
    p.add("body_size", body_id=body, seats=35, valid_from=DAYS[2015], valid_to=None)
    system = p.id("electoral_system", "parlamento")
    p.add(
        "electoral_system",
        id=system,
        polity_id=REGION,
        rules={
            "formula": "dhondt",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD},
        },
        rules_version=1,
    )
    p.name(system, "Parliament of Cantabria closed-list D'Hondt system with a 5% threshold", "en", "official")
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM, lang="en")
    names = {a for e in elections for a in e.votes}
    for abbreviation in sorted(names):
        party = p.id("party", key(abbreviation))
        p.add("party", id=party, kind="party", polity_id=REGION)
        p.name(party, NAMES.get(abbreviation, abbreviation), "es", "official")
        p.add("external_id", entity_id=party, scheme=f"{SOURCE}.party", value=key(abbreviation))
    return p


def build(e: Election) -> Package:
    year, day = e.year, DAYS[e.year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} election to the Parlamento de Cantabria, with the votes and seats of every list",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {SPAIN, REGION, CANTABRIA_AREA, CANTABRIA_UNIT}
    src = source_id()
    p.artifact(raw.cached(SOURCE, VOTES_TABLE), src)
    p.artifact(raw.cached(SOURCE, DATA_TABLE), src)
    body, system = p.id("body", "parlamento"), p.id("electoral_system", "parlamento")
    eid = p.id("election", day)
    p.add("election", id=eid, polity_id=REGION, kind="general", date_from=day, date_to=day, status="held")
    p.name(eid, f"{year} Cantabrian regional election", "en", "official")
    p.name(eid, f"Elecciones al Parlamento de Cantabria de {year}", "es", "native")
    path = f"es/cb/{day}-general"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    magnitude = sum(e.seats.values())
    if magnitude != e.data["Escaños a repartir"]:
        raise ValueError(f"{year}: {magnitude} seats against {e.data['Escaños a repartir']}")
    contest = p.id("contest", str(day))
    p.add(
        "contest",
        id=contest,
        election_id=eid,
        kind="list_allocation",
        body_id=body,
        district_area_id=CANTABRIA_AREA,
        magnitude=magnitude,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(contest, "Cantabria", "es", "official")
    p.add("slug", path=f"{path}/cantabria", entity_id=contest, is_current=True)
    options: dict[str, str] = {}
    for abbreviation in sorted(e.votes, key=lambda a: -e.votes[a]):
        options[abbreviation] = p.id("option", f"{day}:{key(abbreviation)}")
        p.add(
            "contest_option",
            id=options[abbreviation],
            contest_id=contest,
            kind="list",
            party_id=p.id("party", key(abbreviation)),
            ballot_label=abbreviation,
        )
    note = METHOD
    listed = sum(e.votes.values())
    cand, blank = e.data["Votos Candidaturas"], e.data["Votos en Blanco"]
    won = dhondt(e.votes, listed + blank, magnitude)
    if any(won[a] != e.seats.get(a, 0) for a in options):
        note += " D'Hondt with a 5% threshold applied to these votes gives a different split from the declared seats, which are stored as published."
    result_set = p.id("result_set", str(day), "cantabria")
    p.add(
        "result_set",
        id=result_set,
        election_id=eid,
        contest_id=contest,
        stage="official",
        kind="counted",
        status="active",
        source_id=src,
        is_default=True,
        completeness=1,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
    values: list[tuple[str, str, str | None, int, int, int]] = [
        (result_set, CANTABRIA_UNIT, None, REGISTERED, e.data["Censo escrutinio"], 0),
        (result_set, CANTABRIA_UNIT, None, BALLOTS, e.data["Votos TOTALES"], 0),
        (result_set, CANTABRIA_UNIT, None, VALID, cand, 0),
        (result_set, CANTABRIA_UNIT, None, INVALID, e.data["Votos Nulos"], 0),
        (result_set, CANTABRIA_UNIT, None, BLANK, blank, 0),
    ]
    for abbreviation, option in options.items():
        values.append((result_set, CANTABRIA_UNIT, option, VOTES, e.votes[abbreviation], 0))
        if e.seats.get(abbreviation):
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=e.seats[abbreviation])
    print(
        f"{year} seats={magnitude} lists={listed} candidacies={cand} dhondt_ok={all(won[a] == e.seats.get(a, 0) for a in options)}"
    )
    if listed != cand:
        reason = (
            f"In ICANE's tables the {year} lists' votes add up to {listed:,}, against {cand:,} votes to candidacies "
            "in its regional data table; both are stored as published."
        )
        p.accept("accounting.votes_valid", reason)
        if e.data["Votos TOTALES"] != cand + blank + e.data["Votos Nulos"]:
            p.accept(
                "accounting.ballots_sum",
                reason + " The ballots cast there equal the lists' votes plus blank and null ballots.",
            )
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "channel": pl.Int16,
            },
            orient="row",
        ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def parse() -> None:
    elections = load()
    registry(elections).write()
    for e in elections:
        build(e).write()


def emit() -> None:
    package.run("load", packages())
