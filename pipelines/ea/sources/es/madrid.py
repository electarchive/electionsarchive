"""Asamblea de Madrid elections, 1983 to 2023, from the Instituto de Estadística de la Comunidad de Madrid.

IESTADIS publishes its election series as open-data tables with a REST interface. Four of them hold what the archive
stores: the participation of the whole Community, the votes and seats of each candidacy for the whole Community
(both from 1983), and the same two by municipality and by district of the municipality of Madrid (from 1991, since
the Community has no municipal figures for 1983 and 1987). The Community is a single constituency, so the community
rows are the constituency's result and carry the seats; the municipal rows are a finer, non-default result set.

The tables name the larger lists and fold every other list into "Otros", so the minor lists are stored as one
option, and the blank ballots are a separate measure. The Community's votes cast from abroad (CERA) are the row the
tables call "not allocated", which this module stores as an aggregate unit on the abroad channel.

IESTADIS also publishes the same figures by statistical zone, which are groups of the municipalities already stored,
and the Ayuntamiento de Madrid publishes the city's results by polling station; neither is loaded here.
"""

import itertools
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "es.madrid"
API = "https://iestadis.edatos.io/statistical-resources/v1.0/datasets/IECM/"
PARTICIPATION = API + "232_000001/~latest.json"
VOTES_SEATS = API + "232_000002/~latest.json"
MUNICIPAL_PARTICIPATION = API + "232_000003/~latest.json"
MUNICIPAL_VOTES = API + "232_000004/~latest.json"
TABLES = (PARTICIPATION, VOTES_SEATS, MUNICIPAL_PARTICIPATION, MUNICIPAL_VOTES)
PORTAL = "https://web.comunidad.madrid/iestadis/fijas/estructu/general/otros/elecciones_asamblea_intedatos.htm"
PUBLISHER = "Instituto de Estadística de la Comunidad de Madrid (IESTADIS)"
TITLE = "Elecciones a la Asamblea de Madrid: participación y recuento de votos y diputados, por municipio y distrito"
STATUTE = "Ley Orgánica 3/1983, de 25 de febrero, de Estatuto de Autonomía de la Comunidad de Madrid"
LAW = "Ley 11/1986, de 16 de diciembre, Electoral de la Comunidad de Madrid"
SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
REGION = str(uuid5(package.NAMESPACE, "polity\x1fES-MD"))
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
THRESHOLD = 5
MADRID = "28079"
CERA = "28999"
DISTRICT_SCHEME = "es.madrid_distrito"
# (key, election day, IESTADIS period code). The tables date 1991 and the October 2003 election by month only;
# the days are the ones the Junta Electoral published.
ELECTIONS = [
    ("1983-05", date(1983, 5, 8), "1983-M05"),
    ("1987-06", date(1987, 6, 10), "1987-M06"),
    ("1991-05", date(1991, 5, 26), "1991-M05"),
    ("1995-05", date(1995, 5, 28), "1995-M05"),
    ("1999-06", date(1999, 6, 13), "1999-M06"),
    ("2003-05", date(2003, 5, 25), "2003-M05"),
    ("2003-10", date(2003, 10, 26), "2003-M10"),
    ("2007-05", date(2007, 5, 27), "2007-M05"),
    ("2011-05", date(2011, 5, 22), "2011-M05"),
    ("2015-05", date(2015, 5, 24), "2015-M05"),
    ("2019-05", date(2019, 5, 26), "2019-M05"),
    ("2021-05", date(2021, 5, 4), "2021-M05"),
    ("2023-05", date(2023, 5, 28), "2023-M05"),
]
FIRST_MUNICIPAL = "1991-M05"
# IESTADIS's candidacy codes and the names it gives them. Every list not named here is in "_O" (Otros).
PARTIES = {
    "PP": "Partido Popular",
    "PSOE": "Partido Socialista Obrero Español",
    "IU": "Izquierda Unida",
    "CDS": "Centro Democrático y Social",
    "UPYD": "Unión Progreso y Democracia",
    "CS": "Ciudadanos-Partido de la Ciudadanía",
    "PODEMOS": "Podemos",
    "PODEMOS_IU": "Unidas Podemos (Podemos-IU)",
    "MM": "Más Madrid",
    "VOX": "Vox",
}
OTHERS = "_O"
BLANKS = "_X"
TOTAL = "_T"
# What a code stood for in the elections where IESTADIS's note says it ran under another name.
LABELS = {
    ("PP", "1983-M05"): "Alianza Popular - Partido Demócrata Popular - Unión Liberal (AP-PDP-UL)",
    ("PP", "1987-M06"): "Federación de Partidos de Alianza Popular (FAP)",
    ("PSOE", "1999-M06"): "Coalición PSOE-Progresistas (PSOE-p)",
    ("CDS", "1999-M06"): "Unión Centrista - Centro Democrático y Social (UC-CDS)",
    ("IU", "1983-M05"): "Partido Comunista de España (PCE)",
    ("IU", "1999-M06"): "Izquierda Unida de la Comunidad de Madrid (IU-CM)",
    ("IU", "2003-M05"): "Izquierda Unida de la Comunidad de Madrid (IU-CM)",
    ("IU", "2003-M10"): "Izquierda Unida de la Comunidad de Madrid (IU-CM)",
    ("IU", "2007-M05"): "Izquierda Unida de la Comunidad de Madrid (IU-CM)",
    ("IU", "2011-M05"): "Izquierda Unida - Los Verdes (IU-LV)",
    ("IU", "2015-M05"): "Izquierda Unida de la Comunidad de Madrid - Los Verdes (IUCM-LV)",
    ("PODEMOS_IU", "2019-M05"): "Unidas Podemos - Izquierda Unida - Madrid en Pie",
    ("PODEMOS_IU", "2023-M05"): "Podemos - Izquierda Unida - Alianza Verde",
}
SYSTEM = (
    "Closed party lists in a single constituency, the whole Community of Madrid. Seats go by the D'Hondt method to "
    "the lists with at least 5% of the valid votes cast in the Community, blank ballots counted as valid ("
    + LAW
    + "; "
    + STATUTE
    + "). The number of deputies follows the population, from 94 in 1983 to 136 in 2021."
)
METHOD = (
    "Electors, ballots cast, votes to candidacies, null and blank ballots, and the votes and seats of each list for "
    "the whole Community of Madrid, from IESTADIS's tables. IESTADIS names the larger lists and folds the others "
    "into Otros. Valid votes here are the votes to candidacies, with blank ballots stored apart. The seats are the "
    "deputies IESTADIS reports for each candidacy."
)
MUNICIPAL_METHOD = (
    "Electors, ballots cast, votes to candidacies, null and blank ballots, and the votes of each list by municipality "
    "and, for the municipality of Madrid, by district, from IESTADIS's tables. The votes of residents abroad (CERA) "
    "are one aggregate row on the abroad channel. The Madrid district rows stand in for the municipality's total. "
    "The units' list votes add up to {got:,}, against {official:,} for the Community.{extra}"
)
EXTRA_1991 = (
    " IESTADIS has no complete municipal figures for 1991 and does not separate the Centro Democrático y Social from "
    "the other minor lists in them, so the municipal rows carry one Otros option that includes it."
)


@dataclass
class Table:
    """One IESTADIS table: its observations keyed by the codes of its dimensions, in the order the API gives them."""

    names: list[str]
    cells: dict[tuple[str, ...], int | float | None]


def fetch() -> None:
    for url in TABLES:
        raw.fetch(SOURCE, url, suffix=".json")


def number(cell: str) -> int | float | None:
    cell = cell.strip()
    if not cell:
        return None
    value = float(cell)
    return int(value) if value == int(value) else value


def table(url: str) -> Table:
    data = json.loads(raw.cached(SOURCE, url).path.read_bytes())["data"]
    names = [d["dimensionId"] for d in data["dimensions"]["dimension"]]
    codes = [[r["code"] for r in d["representations"]["representation"]] for d in data["dimensions"]["dimension"]]
    cells = data["observations"].split(" | ")
    if len(cells) != len(list(itertools.product(*[range(len(c)) for c in codes]))):
        raise ValueError(f"{url}: {len(cells)} observations for dimensions {names}")
    return Table(names, {k: number(c) for k, c in zip(itertools.product(*codes), cells, strict=True)})


@dataclass
class Place:
    """The figures of one reporting unit in one election: the ballot counts and the votes of each list."""

    code: str
    counts: dict[str, int]
    votes: dict[str, int]


@dataclass
class Election:
    key: str
    day: date
    period: str
    seats: dict[str, int]
    community: Place
    places: list[Place]


def int_of(value: float | None) -> int:
    if value is None or value != int(value):
        raise ValueError(f"not a count: {value!r}")
    return int(value)


def load() -> list[Election]:
    participation, regional, municipal_counts, municipal_votes = (table(url) for url in TABLES)
    if participation.names != ["MEDIDAS", "TIME_PERIOD"] or municipal_counts.names != [
        "TERRITORIO",
        "MEDIDAS",
        "TIME_PERIOD",
    ]:
        raise ValueError(f"dimensions {participation.names} {municipal_counts.names}")
    if regional.names != ["TIME_PERIOD", "CANDIDATURAS_ELECTORALES", "MEDIDAS"] or municipal_votes.names != [
        "TIME_PERIOD",
        "TERRITORIO",
        "CANDIDATURAS_ELECTORALES",
        "MEDIDAS",
    ]:
        raise ValueError(f"dimensions {regional.names} {municipal_votes.names}")
    lists = sorted({k[1] for k in regional.cells} - {TOTAL, BLANKS})
    if sorted(lists) != sorted([*PARTIES, OTHERS]):
        raise ValueError(f"candidacies {lists}")
    out = []
    for key, day, period in ELECTIONS:
        seats: dict[str, int] = {}
        votes: dict[str, int] = {}
        for code in lists:
            vote = regional.cells[(period, code, "VOTOS_VALIDOS")]
            if vote is not None:
                votes[code] = int_of(vote)
            seat = regional.cells[(period, code, "DIPUTADOS")]
            if seat is not None and int_of(seat):
                seats[code] = int_of(seat)
        total = regional.cells[(period, TOTAL, "DIPUTADOS")]
        if total is None or int_of(total) != sum(seats.values()):
            raise ValueError(f"{key}: seats {sum(seats.values())} against {total}")
        community = counts_of(participation, (period,), regional=True)
        if sum(votes.values()) != community["valid"]:
            raise ValueError(f"{key}: list votes {sum(votes.values())} against {community['valid']}")
        if regional.cells[(period, BLANKS, "VOTOS_VALIDOS")] != community["blank"]:
            raise ValueError(f"{key}: blank ballots")
        places: list[Place] = []
        if period >= FIRST_MUNICIPAL:
            codes = sorted({k[0] for k in municipal_counts.cells} - {"ES30", MADRID})
            for territory in codes:
                places.append(
                    Place(
                        CERA if territory == "ES300_X" else territory.replace("28079_D", MADRID),
                        counts_of(municipal_counts, (territory,), period=period),
                        {
                            code: int_of(v)
                            for code in lists
                            if (v := municipal_votes.cells[(period, territory, code, "VOTOS_VALIDOS")]) is not None
                        },
                    )
                )
        out.append(Election(key, day, period, seats, Place("ES30", community, votes), places))
    return out


def counts_of(t: Table, prefix: tuple[str, ...], regional: bool = False, period: str | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for measure, name in (
        ("ELECTORES", "registered"),
        ("VOTOS_EMITIDOS", "ballots"),
        ("VOTOS_A_CANDIDATURAS", "valid"),
        ("VOTOS_NULOS", "invalid"),
        ("VOTOS_EN_BLANCO", "blank"),
    ):
        key = (measure, prefix[0]) if regional else (prefix[0], measure, str(period))
        cell = t.cells[key]
        if cell is not None:
            out[name] = int_of(cell)
    return out


def dhondt(votes: dict[str, int], valid: int, seats: int) -> Counter[str]:
    eligible = {k: v for k, v in votes.items() if k != OTHERS and v * 100 >= THRESHOLD * valid}
    quotients = sorted(
        ((v / d, v, k) for k, v in eligible.items() for d in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(k for *_, k in quotients[:seats])


def source_id() -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fiestadis"))


def unit_id(scheme: str, code: str) -> str:
    """The id the archive gives a unit by its scheme and code, so a unit another source holds resolves to it."""
    return str(uuid5(package.NAMESPACE, f"unit:{scheme}@None\x1f{code}"))


def area_id(scheme: str, code: str) -> str:
    return str(uuid5(package.NAMESPACE, f"area:{scheme}@None\x1f{code}"))


PROVINCE_AREA = area_id("es.provincia", "28")
PROVINCE_UNIT = unit_id("es.provincia", "28")


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PORTAL,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Fuente: Instituto de Estadística de la Comunidad de Madrid, a partir de los datos de la Junta Electoral.",
        notes="IESTADIS's open-data portal publishes these tables for reuse with attribution.",
    )


def registry(elections: list[Election]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Asamblea de Madrid, its electoral system and size, IESTADIS's tables as a source, and the lists that "
        "stood in the elections of 1983 to 2023",
    )
    p.external.add(REGION)
    add_source(p)
    for url in TABLES:
        p.uses(raw.cached(SOURCE, url))
    body = p.id("body", "asamblea")
    p.add("body", id=body, polity_id=REGION, kind="unicameral")
    p.name(body, "Assembly of Madrid", "en", "official")
    p.name(body, "Asamblea de Madrid", "es", "native")
    sizes = [(e.day, sum(e.seats.values())) for e in elections]
    for (day, seats), following in itertools.zip_longest(sizes, [d for d, _ in sizes[1:]]):
        p.add("body_size", body_id=body, seats=seats, valid_from=day, valid_to=following)
    system = p.id("electoral_system", "asamblea")
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
    p.name(system, "Assembly of Madrid closed-list D'Hondt system with a 5% threshold", "en", "official")
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM, lang="en")
    for code, name in PARTIES.items():
        party = p.id("party", code.lower())
        p.add("party", id=party, kind="party", polity_id=REGION)
        p.name(party, name, "es", "official")
        p.add("external_id", entity_id=party, scheme=f"{SOURCE}.party", value=code.lower())
    return p


def geo() -> Package:
    """The units the archive lacks: the districts of the municipality of Madrid and the votes from abroad."""
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Reporting units for the 21 districts of the municipality of Madrid and for the votes of residents abroad "
        "in the Community of Madrid, which IESTADIS's municipal tables report",
    )
    p.external |= {SPAIN, "es.municipio", PROVINCE_UNIT, PROVINCE_AREA, unit_id("es.municipio", MADRID)}
    p.external.add(area_id("es.municipio", MADRID))
    p.add(
        "scheme",
        code=DISTRICT_SCHEME,
        polity_id=SPAIN,
        kind="administrative",
        rank=4,
        noun="district of the municipality of Madrid",
    )
    for number_ in range(1, 22):
        code = f"{MADRID}{number_:02d}"
        p.add(
            "reporting_unit",
            id=unit_id(DISTRICT_SCHEME, code),
            polity_id=SPAIN,
            scheme_code=DISTRICT_SCHEME,
            code=code,
            parent_id=unit_id("es.municipio", MADRID),
            area_relation="within",
            area_id=area_id("es.municipio", MADRID),
            kind="standard",
        )
    p.add(
        "reporting_unit",
        id=unit_id("es.municipio", CERA),
        polity_id=SPAIN,
        scheme_code="es.municipio",
        code=CERA,
        parent_id=PROVINCE_UNIT,
        area_relation="within",
        area_id=PROVINCE_AREA,
        kind="aggregate",
    )
    return p


def label(code: str, period: str) -> str:
    return LABELS.get((code, period), PARTIES.get(code, "Otros"))


def build(e: Election) -> Package:
    day = e.day
    p = Package(
        f"{SOURCE}/{e.key}",
        SOURCE,
        f"The {e.day:%d %B %Y} election to the Asamblea de Madrid, with the votes and seats of every list and the "
        "results by municipality" + ("" if e.places else " (not published for this election)"),
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external |= {SPAIN, REGION, PROVINCE_AREA, PROVINCE_UNIT}
    src = source_id()
    for url in TABLES:
        if e.key == "2003-10":
            # The May 2003 package this one depends on holds the same artifact rows.
            p.uses(raw.cached(SOURCE, url))
        else:
            p.artifact(raw.cached(SOURCE, url), src)
    body, system = p.id("body", "asamblea"), p.id("electoral_system", "asamblea")
    eid = p.id("election", day)
    rerun = None
    if e.key == "2003-10":
        rerun = p.id("election", date(2003, 5, 25))
        p.depends.append(f"{SOURCE}/2003-05")
    p.add(
        "election",
        id=eid,
        polity_id=REGION,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
        rerun_of_id=rerun,
    )
    p.name(
        eid, f"{e.day.year} Madrid regional election" + (" (October)" if e.key == "2003-10" else ""), "en", "official"
    )
    p.name(
        eid,
        f"Elecciones a la Asamblea de Madrid de {day:%Y}" + (" (octubre)" if e.key == "2003-10" else ""),
        "es",
        "native",
    )
    if e.key in ("2003-05", "2003-10"):
        text = (
            "The Asamblea elected on 25 May 2003 did not invest a president, and the election was repeated on 26 October 2003."
            if e.key == "2003-05"
            else "The repeat of the 25 May 2003 election, called after that Asamblea did not invest a president."
        )
        p.add("note", id=p.id("note", eid), entity_id=eid, kind="context", body_md=text, lang="en")
    path = f"es/md/{day}-general"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    magnitude = sum(e.seats.values())
    contest = p.id("contest", str(day))
    p.add(
        "contest",
        id=contest,
        election_id=eid,
        kind="list_allocation",
        body_id=body,
        district_area_id=PROVINCE_AREA,
        magnitude=magnitude,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(contest, "Comunidad de Madrid", "es", "official")
    p.add("slug", path=f"{path}/comunidad-de-madrid", entity_id=contest, is_current=True)
    votes = e.community.votes
    options: dict[str, str] = {}
    for code in sorted(votes, key=lambda c: (c == OTHERS, -votes[c])):
        options[code] = p.id("option", f"{day}:{code.lower()}")
        p.add(
            "contest_option",
            id=options[code],
            contest_id=contest,
            kind="others" if code == OTHERS else "list",
            party_id=None if code == OTHERS else p.id("party", code.lower()),
            ballot_label=label(code, e.period),
        )
    c = e.community.counts
    won = dhondt(votes, c["valid"] + c["blank"], magnitude)
    note = METHOD
    if any(won[k] != e.seats.get(k, 0) for k in options if k != OTHERS):
        note += " D'Hondt with a 5% threshold applied to these votes gives a different split from the declared seats, which are stored as published."
    result_set = p.id("result_set", str(day), "comunidad")
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
    values: list[tuple[str, str, str | None, int, int, int]] = []

    def account(rs: str, unit: str, got: dict[str, int], channel: int) -> None:
        for name, measure in (
            ("registered", REGISTERED),
            ("ballots", BALLOTS),
            ("valid", VALID),
            ("invalid", INVALID),
            ("blank", BLANK),
        ):
            if name in got:
                values.append((rs, unit, None, measure, got[name], channel))

    account(result_set, PROVINCE_UNIT, c, 0)
    for code, option in options.items():
        values.append((result_set, PROVINCE_UNIT, option, VOTES, votes[code], 0))
        if e.seats.get(code):
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=e.seats[code])
    print(
        f"{e.key} seats={magnitude} lists={sum(votes.values())} dhondt_ok={all(won[k] == e.seats.get(k, 0) for k in options if k != OTHERS)}",
        flush=True,
    )
    if e.places:
        merged = e.period == FIRST_MUNICIPAL
        municipal = p.id("result_set", str(day), "municipio")
        sums = Counter[str]()
        for place in e.places:
            sums.update(place.votes)
        got = sum(sums.values())
        extra = EXTRA_1991 if merged else ""
        completeness = min(got / sum(votes.values()), 1.0)
        p.add(
            "result_set",
            id=municipal,
            election_id=eid,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=src,
            is_default=False,
            completeness=completeness,
        )
        p.add(
            "note",
            id=p.id("note", municipal),
            entity_id=municipal,
            kind="methodology",
            body_md=MUNICIPAL_METHOD.format(got=got, official=sum(votes.values()), extra=extra),
            lang="en",
        )
        option_of = dict(options)
        if merged:
            cds_option = p.id("option", f"{day}:otros-con-cds")
            p.add(
                "contest_option",
                id=cds_option,
                contest_id=contest,
                kind="others",
                party_id=None,
                ballot_label="Otros (including the Centro Democrático y Social)",
            )
            option_of[OTHERS] = cds_option
        for place in e.places:
            if place.code == CERA:
                unit, channel = unit_id("es.municipio", CERA), ABROAD
            elif place.code.startswith(MADRID) and len(place.code) == 7:
                unit, channel = unit_id(DISTRICT_SCHEME, place.code), 0
            else:
                unit, channel = unit_id("es.municipio", place.code), 0
                p.external.add(unit)
            account(municipal, unit, place.counts, channel)
            for code, vote in place.votes.items():
                values.append((municipal, unit, option_of[code], VOTES, vote, channel))
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
    return [f"{SOURCE}/registry", f"{SOURCE}/geo", *(f"{SOURCE}/{key}" for key, *_ in ELECTIONS)]


def parse() -> None:
    elections = load()
    registry(elections).write()
    geo().write()
    for e in elections:
        build(e).write()


def emit() -> None:
    package.run("load", packages())
