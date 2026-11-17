"""Chile's national consultation of 4 January 1978 and constitutional plebiscite of 11 September 1980, by region.

Both votes were run by the Interior Ministry under decrees of the military government, with no electoral register.
The results are the official tables the ministry and the national scrutiny college published afterward.
"""

from datetime import date
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.cl import plebiscitos, servel

SOURCE = "cl.plebiscitos_historicos"
CHILE = servel.CHILE
ARCHIVE = "https://archivo.servel.cl/uploads/r/null/"
LEY = "https://www.bcn.cl/leychile/consulta/obtxml?opt=7&idNorma="
RESULTS = {
    date(1978, 1, 4): ARCHIVE + "1/9/8/1987715a1969d0b298a6cdd615a353fbd004c0c3d1f1c6e1033342b3faa45d48/SFMI-10.pdf",
    date(1980, 9, 11): ARCHIVE + "d/b/8/db8e1c5bec4514bd8ed6fc1ff9dc6fb13c3d8760983d0807188675ad18e9e955/SFMI-16.pdf",
}
# The decrees that called each vote and, for 1980, the one that declared the constitution approved.
DECREES = {
    date(1978, 1, 4): ((LEY + "264163", "Decreto 1308 (27 December 1977): convoca y reglamenta la Consulta Nacional"),),
    date(1980, 9, 11): (
        (LEY + "7130", "Decreto Ley 3465 (8 August 1980): convoca al plebiscito y señala las normas a que se sujetará"),
        (LEY + "17039", "Decreto 1150 (21 October 1980): texto de la Constitución Política de la República de Chile"),
    ),
}


class Row(NamedTuple):
    yes: int
    no: int
    blank: int | None
    null: int | None
    other: int | None  # blank and null votes the table reports together, for 1978
    total: int


class Spec(NamedTuple):
    rows: dict[int, Row]
    national: Row
    en: str
    es: str
    question_en: str
    question_es: str
    options: tuple[str, str]
    title: str
    method: str


# 1978: the "Sub total" row of each region in the Interior Ministry's "Resultados oficiales y finales de la Consulta
# Nacional" table (si, no, nulos y blancos, totales). The table prints null and blank ballots as one column, and the
# decree counted only the "SI" and "NO" votes. Region 13 is the Región Metropolitana.
V1978 = {
    1: Row(102156, 23177, None, None, 9001, 134334),
    2: Row(123886, 33018, None, None, 14233, 171137),
    3: Row(69581, 17499, None, None, 6243, 93323),
    4: Row(154122, 38356, None, None, 9873, 202351),
    5: Row(507608, 142836, None, None, 35577, 686021),
    6: Row(232935, 50624, None, None, 11651, 295210),
    7: Row(315275, 52898, None, None, 7921, 376094),
    8: Row(578754, 126443, None, None, 17841, 723038),
    9: Row(284302, 34490, None, None, 7098, 325890),
    10: Row(322483, 52814, None, None, 15950, 391247),
    11: Row(24234, 4968, None, None, 1354, 30556),
    12: Row(47930, 11929, None, None, 6593, 66452),
    13: Row(1413798, 542063, None, None, 114774, 2070635),
}
N1978 = Row(4177064, 1131115, None, None, 258109, 5566288)
# 1980: the national scrutiny college's "Resultado nacional del plebiscito" by region (13 October 1980): the "Sí"
# votes proper, the blank votes (which the rules counted for "Sí"), the "No" votes, null votes, and the general total.
V1980 = {
    1: Row(99708, 52509, 3640, 3299, None, 159156),
    2: Row(120061, 66031, 3713, 4418, None, 194223),
    3: Row(67900, 30732, 2064, 2897, None, 103593),
    4: Row(162420, 47305, 3213, 9234, None, 222172),
    5: Row(463031, 224049, 9587, 21821, None, 718488),
    6: Row(224846, 74293, 3558, 12865, None, 315562),
    7: Row(307048, 77462, 5449, 13448, None, 403407),
    8: Row(525226, 244790, 9242, 25749, None, 805007),
    9: Row(276772, 68416, 2837, 11864, None, 359889),
    10: Row(324318, 87895, 6100, 12251, None, 430564),
    11: Row(22863, 10882, 313, 1254, None, 35312),
    12: Row(46082, 29074, 1267, 1445, None, 77868),
    13: Row(1480792, 879982, 32829, 53024, None, 2446627),
}
N1980 = Row(4121067, 1893420, 83812, 173569, None, 6271868)

ELECTIONS: dict[date, Spec] = {
    date(1978, 1, 4): Spec(
        V1978,
        N1978,
        "1978 Chilean national consultation",
        "Consulta Nacional de 1978",
        "National consultation of 4 January 1978: support for President Pinochet against the United Nations resolution",
        (
            "Frente a la agresión internacional desatada en contra de nuestra Patria, respaldo al Presidente Pinochet "
            "en su defensa de la dignidad de Chile, y reafirmo la legitimidad del Gobierno de la República para "
            "encabezar soberanamente el proceso de institucionalidad del país"
        ),
        ("SÍ", "NO"),
        "Resultados oficiales y finales de la Consulta Nacional (Ministerio del Interior)",
        (
            "Totals per region from the 'Sub total' rows of the Interior Ministry's official and final results table "
            "for the consultation of 4 January 1978, as the newspaper El Cronista reproduced it on 19 January 1978. "
            "The table also lists each province and, for the Santiago region, each commune; this package holds only "
            "the regional totals. It prints null and blank ballots as one column, which this package does not "
            "store. The decree counted only 'SI' and 'NO' votes. The regions add up to the national totals the "
            "table prints: 4,177,064 'SI', 1,131,115 'NO', 258,109 null and blank, 5,566,288 in all."
        ),
    ),
    date(1980, 9, 11): Spec(
        V1980,
        N1980,
        "1980 Chilean constitutional plebiscite",
        "Plebiscito constitucional de 1980",
        "Plebiscite of 11 September 1980 on the Political Constitution of the Republic",
        "Plebiscito para la ratificación de la Constitución Política de la República de Chile",
        ("SÍ", "NO"),
        "Resultado nacional del plebiscito (Colegio Escrutador Nacional)",
        (
            "Totals per region from the national scrutiny college's 'Resultado nacional del plebiscito', read out by "
            "its president on 15 October 1980. The 'SI' votes are those the table calls 'Votación SI parcial'. The "
            "Decreto Ley 3465 (article 20) counted blank ballots for the 'SI' option, so the college's 'Total SI' is the "
            "'SI' votes plus the blank votes: 4,204,879 nationally, against 1,893,420 'NO', 173,569 null, and "
            "6,271,868 ballots in all. This package stores the blank votes apart and leaves the declared outcome as "
            "the college stated it. The table is dated 13 October 1980."
        ),
    ),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [f"{SOURCE}/{day}" for day in ELECTIONS]


def fetch() -> None:
    for url in RESULTS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for decrees in DECREES.values():
        for url, _ in decrees:
            raw.fetch(SOURCE, url, suffix=".xml")


def parse() -> None:
    for day, spec in ELECTIONS.items():
        for region, row in spec.rows.items():
            if row.yes + row.no + (row.blank or 0) + (row.null or 0) + (row.other or 0) != row.total:
                raise ValueError(f"{day} region {region}: counts do not add up to the printed total")
        for i, name in enumerate(Row._fields):
            summed = sum(row[i] or 0 for row in spec.rows.values())
            if summed != (spec.national[i] or 0):
                raise ValueError(f"{day} {name}: regions add up to {summed}, not the printed {spec.national[i]}")
    for day in ELECTIONS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def build(day: date) -> Package:
    spec = ELECTIONS[day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"{spec.en} by region, from the official results tables",
        depends=["cl.servel/registry"],
    )
    p.external.add(CHILE)
    source = p.id("source", day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Ministerio del Interior de Chile" if day.year == 1978 else "Colegio Escrutador Nacional de Chile",
        title=spec.title,
        url=RESULTS[day],
        license="No license stated: official government record; facts published with attribution",
        attribution="Servicio Electoral de Chile, Archivo Histórico",
    )
    p.artifact(raw.cached(SOURCE, RESULTS[day]), source)
    for number, (url, title) in enumerate(DECREES[day]):
        decree = p.id("source", day, "decree", number)
        p.add(
            "source",
            id=decree,
            kind="official_body",
            publisher="Biblioteca del Congreso Nacional de Chile (LeyChile)",
            title=title,
            url=url,
            license="No license stated: official legal text; facts published with attribution",
            attribution="Biblioteca del Congreso Nacional de Chile",
        )
        p.artifact(raw.cached(SOURCE, url), decree)

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
    for position, label in enumerate(spec.options, 1):
        option = p.id("option", day, position)
        options.append(option)
        p.add(
            "contest_option", id=option, contest_id=contest, kind="answer", ballot_position=position, ballot_label=label
        )

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
    p.add("outcome", result_set_id=result_set, option_id=options[0], kind="won")

    rows: list[tuple[str, str | None, int, int]] = []
    for region, row in spec.rows.items():
        unit = plebiscitos.region_unit(region)
        p.external.add(unit)
        rows += [(unit, options[0], 1, row.yes), (unit, options[1], 1, row.no), (unit, None, 3, row.total)]
        if row.null is not None:
            rows.append((unit, None, 5, row.null))
        if row.blank is not None:
            rows.append((unit, None, 6, row.blank))
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
