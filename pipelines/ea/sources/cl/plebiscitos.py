"""Chile's national plebiscites of 5 October 1988 and 30 July 1989, by region, from the electoral court's records."""

from datetime import date
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.cl import servel

SOURCE = "cl.plebiscitos"
CHILE = servel.CHILE
TRICEL = "https://tribunalcalificador.cl/wp-content/uploads/"
FILES = {
    date(1988, 10, 5): TRICEL + "2020/11/SENTENCIA-PLEBISCITO-1988.pdf",
    date(1989, 7, 30): TRICEL + "2024/01/RESULTADOS-PLEBISCITO-1989-v3.pdf",
}


class Vote(NamedTuple):
    yes: int
    no: int
    null: int
    blank: int
    printed: int
    electors: int | None


class Spec(NamedTuple):
    votes: dict[int, Vote]
    en: str
    es: str
    question_en: str
    question_es: str
    options: tuple[str, str]
    winner: int
    title: str
    method: str


# 1988: "Acta" of the Tribunal Calificador de Elecciones, sessions of 4 and 5 November 1988 (pages 1 to 4 of the PDF),
# with the registered voters (votantes habilitados) from its summary table. Region 13 is the Región Metropolitana.
V1988 = {
    1: Vote(75849, 93800, 3188, 2176, 175013, 181745),
    2: Vote(84259, 130052, 4159, 2592, 221062, 228808),
    3: Vote(49400, 63293, 1704, 1833, 116230, 119938),
    4: Vote(114250, 133997, 4148, 3876, 256271, 262914),
    5: Vote(324058, 434997, 11261, 7505, 777821, 799964),
    6: Vote(164430, 208574, 4684, 3876, 381564, 387337),
    7: Vote(220742, 231348, 5327, 4351, 461768, 470020),
    8: Vote(409513, 506513, 11937, 10219, 938182, 958635),
    9: Vote(220090, 187071, 5630, 4645, 417436, 431565),
    10: Vote(242457, 240984, 6486, 4704, 494631, 509991),
    11: Vote(19238, 19245, 436, 406, 39325, 41435),
    12: Vote(35549, 48372, 934, 813, 85668, 89941),
    13: Vote(1159275, 1669333, 34700, 23664, 2886972, 2947111),
}
# 1989: the "TOTAL REGION" row of each region's table in the court's scanned results (apruebo, rechazo, nulos, blancos,
# total), keyed by hand from the page images. The scans print no electoral roll.
V1989 = {
    1: Vote(147660, 11681, 8064, 3201, 170606, None),
    2: Vote(179540, 15870, 13005, 3495, 211910, None),
    3: Vote(90123, 10678, 9819, 2474, 113094, None),
    4: Vote(201925, 26269, 19201, 5781, 253176, None),
    5: Vote(666412, 53907, 37998, 11251, 769568, None),
    6: Vote(321780, 34456, 14335, 6126, 376697, None),
    7: Vote(382587, 46710, 15143, 6329, 450769, None),
    8: Vote(774111, 92564, 36228, 13729, 916632, None),
    9: Vote(322120, 55889, 14628, 6875, 399512, None),
    10: Vote(404110, 52713, 15336, 7961, 480120, None),
    11: Vote(32473, 3733, 1163, 727, 38096, None),
    12: Vote(71805, 6222, 3480, 1290, 82797, None),
    13: Vote(2474803, 170913, 135883, 37508, 2819107, None),
}
# The court's national totals as the 1988 record states them: yes, no, null, blank.
NATIONAL_1988 = (3119110, 3967579, 94594, 70660)
ELECTIONS: dict[date, Spec] = {
    date(1988, 10, 5): Spec(
        V1988,
        "1988 Chilean presidential plebiscite",
        "Plebiscito presidencial de 1988",
        "Presidential plebiscite of 5 October 1988: the candidate proposed by the Junta",
        "Plebiscito presidencial del 5 de octubre de 1988",
        ("Sí", "No"),
        1,
        "Resultados del plebiscito de 1988 (Tribunal Calificador de Elecciones)",
        (
            "Totals per region as the Tribunal Calificador de Elecciones declared them in its record of the general "
            "scrutiny of 4 and 5 November 1988, with each region's registered voters from the record's summary table. "
            "The court published no finer breakdown in this document."
        ),
    ),
    date(1989, 7, 30): Spec(
        V1989,
        "1989 Chilean constitutional reform plebiscite",
        "Plebiscito sobre reformas constitucionales de 1989",
        "Plebiscite of 30 July 1989 on the constitutional reforms",
        "Plebiscito sobre reformas constitucionales del 30 de julio de 1989",
        ("Apruebo", "Rechazo"),
        0,
        "Resultados del plebiscito de 1989 (Tribunal Calificador de Elecciones)",
        (
            "Totals per region from the 'Total region' row of each region's results table, as the Tribunal "
            "Calificador de Elecciones recorded them for the plebiscite of 30 July 1989. The source is a scanned "
            "printout, so the figures were keyed by hand from the page images, and every region's four counts "
            "were checked against the printed total of votes. The printout also lists each scrutiny college by sex, "
            "which this package does not hold."
        ),
    ),
}


def region_unit(region: int) -> str:
    """The archive's reporting unit for a region (scheme cl.region), which another Chile load already holds.

    The id is the one `Changeset` derives from the unit's natural key, so these packages reference the unit rather
    than mint one: a unit code is unique within its scheme.
    """
    return str(uuid5(package.NAMESPACE, f"unit:cl.region@None\x1f{region}"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [f"{SOURCE}/{day}" for day in ELECTIONS]


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    for day, spec in ELECTIONS.items():
        for region, row in spec.votes.items():
            if row.yes + row.no + row.null + row.blank != row.printed:
                raise ValueError(f"{day} region {region}: counts do not add up to the printed total")
    totals = tuple(sum(row[i] or 0 for row in V1988.values()) for i in range(4))
    if totals != NATIONAL_1988:
        raise ValueError(f"1988 regions add up to {totals}, not the court's {NATIONAL_1988}")
    for day in ELECTIONS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def build(day: date) -> Package:
    spec = ELECTIONS[day]
    votes = spec.votes
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"{spec.en} by region, from the Tribunal Calificador de Elecciones",
        depends=["cl.servel/registry"],
    )
    p.external.add(CHILE)
    source = p.id("source", day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Tribunal Calificador de Elecciones de Chile",
        title=spec.title,
        url=FILES[day],
        license="No license stated: official record of the electoral court; facts published with attribution",
        attribution="Tribunal Calificador de Elecciones de Chile",
    )
    p.artifact(raw.cached(SOURCE, FILES[day]), source)

    election = p.id("election", day)
    p.add(
        "election",
        id=election,
        polity_id=CHILE,
        kind="referendum",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election, spec.en, "en", "official")
    p.name(election, spec.es, "es", "native", False)
    base = f"cl/{day}-plebiscite"
    p.add("slug", path=base, entity_id=election, is_current=True)

    contest = p.id("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        electoral_system_id=servel.key("electoral_system", "plebiscite"),
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="plebiscite")
    p.name(contest, spec.question_es, "es", "official")
    p.name(contest, spec.question_en, "en", "official", False)
    p.add("slug", path=f"{base}/question", entity_id=contest, is_current=True)

    options = []
    for position, es in enumerate(spec.options, 1):
        option = p.id("option", day, position)
        options.append(option)
        p.add("contest_option", id=option, contest_id=contest, kind="answer", ballot_position=position, ballot_label=es)

    result_set = p.id("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="certified",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=p.id("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=spec.method,
        lang="en",
    )
    p.add("outcome", result_set_id=result_set, option_id=options[spec.winner], kind="won")

    rows: list[tuple[str, str | None, int, int]] = []
    for region, row in votes.items():
        unit = region_unit(region)
        p.external.add(unit)
        rows += [
            (unit, options[0], 1, row.yes),
            (unit, options[1], 1, row.no),
            (unit, None, 5, row.null),
            (unit, None, 6, row.blank),
        ]
        if row.electors is not None:
            rows.append((unit, None, 2, row.electors))
    p.frame(
        "result_value",
        pl.DataFrame(rows, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p
