"""Ecuador's local popular consultations of 2015, 2016, 2021, and 2023, from the National Electoral Council's books.

The Council holds a popular consultation for one place when a decentralized government or the residents ask for it.
Four such votes have results in the Council's statistics books: the 2015 consultation that placed the undelimited
zone of La Manga del Cura in Manabí or Guayas, the 2016 consultation that placed Las Golondrinas in Esmeraldas or
Imbabura (both published by electoral zone in the Council's consultations book), the 2021 consultation on mining near
the water sources of Cuenca (five questions, published as cantonal totals in the 2021 results book), and the 2023
consultation on mining in the Chocó Andino parishes of Quito (four questions, published as totals for the Distrito
Metropolitano de Quito in the 2023 results book). Each package holds one consultation.
"""

import re
import subprocess
from datetime import date
from typing import NamedTuple

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.ec import cne

SOURCE = "ec.consultas"


def key(kind: str, *parts: object) -> str:
    return cne.key(f"consultas.{kind}", *parts)


ECUADOR = cne.ECUADOR
PAGE = cne.PAGE
BOOK_2016 = f"{cne.BASE}/2022/05/Consultas-Populares-2016.pdf"
BOOK_2021 = f"{cne.BASE}/2022/05/RESULTADOS-ELECTORALES-2021.pdf"
BOOK_2023 = f"{cne.BASE}/2024/06/ResultadosElectoralesAnticipadasyConsultas2023.pdf"
BOOKS = (BOOK_2016, BOOK_2021, BOOK_2023)
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
LICENSE = "Official publication; reuse terms are not stated"
ATTRIBUTION = "Consejo Nacional Electoral del Ecuador"
SYSTEM = key("electoral_system", "referendum")
REGISTRY = f"{SOURCE}/registry"
ZONES = "ec.zona_consulta"
CNE_SOURCE = cne.key("source", "consultas")


class Zone(NamedTuple):
    name: str
    electors: int
    first: int  # votes for the first option
    second: int  # votes for the second option
    null: int
    blank: int


class Territorial(NamedTuple):
    day: str
    slug: str
    english: str
    spanish: str
    book: str
    first_page: int  # the PDF page that carries the zone chart
    options: tuple[str, str]
    topic: str
    method: str
    zones: tuple[Zone, ...]
    cast: int  # voters who signed the register, as the book prints it
    printed: tuple[int, int, int, int]  # first option, second option, null, blank, as the book prints them


TERRITORIAL = (
    Territorial(
        "2015-09-27",
        "manga-del-cura",
        "2015 La Manga del Cura popular consultation",
        "Consulta popular La Manga del Cura 2015",
        BOOK_2016,
        11,
        ("Manabí", "Guayas"),
        "Whether the undelimited zone of La Manga del Cura belongs to Manabí or to Guayas",
        "The Council drew up a register of the zone's residents and counted the votes in 54 polling stations in five "
        "electoral zones, plus two stations for voters at home. The book prints the votes by zone.",
        (
            Zone("El Descanso", 2776, 1571, 644, 36, 16),
            Zone("La 14 / El Paraíso", 5164, 2473, 1866, 88, 31),
            Zone("La Palizada", 1340, 837, 304, 32, 10),
            Zone("Santa María", 4478, 2744, 1124, 107, 23),
            Zone("Santa Teresa", 1571, 891, 439, 27, 8),
            Zone("Voto en casa", 13, 9, 3, 1, 0),
        ),
        13285,
        (8525, 4380, 291, 88),
    ),
    Territorial(
        "2016-04-03",
        "las-golondrinas",
        "2016 Las Golondrinas popular consultation",
        "Consulta popular Las Golondrinas 2016",
        BOOK_2016,
        18,
        ("Esmeraldas", "Imbabura"),
        "Whether the undelimited zone of Las Golondrinas belongs to Esmeraldas or to Imbabura",
        "The Council drew up a register of the zone's residents and counted the votes in three electoral zones, plus "
        "voters at home. The book prints the votes by zone.",
        (
            Zone("La Independiente", 285, 57, 210, 8, 2),
            Zone("Las Golondrinas", 2864, 1213, 1463, 46, 19),
            Zone("Rumiñahui", 133, 7, 111, 3, 0),
            Zone("Voto en casa", 6, 2, 4, 0, 0),
        ),
        3145,
        (1279, 1788, 57, 21),
    ),
)


class Questions(NamedTuple):
    day: str
    slug: str
    english: str
    spanish: str
    book: str
    place_code: str  # the Council's canton code
    place: str
    questions: tuple[tuple[str, str, int, int], ...]  # topic, wording, yes votes, no votes
    method: str


ETAPA = (
    "según la delimitación técnica realizada por la Empresa Pública Municipal de Telecomunicaciones, Agua Potable, "
    "Alcantarillado y Saneamiento de Cuenca-Etapa EP?"
)
CUENCA = tuple(
    (
        f"Ban large-scale metallic mining in the water recharge zone of the {river} river",
        (
            "¿Está usted de acuerdo con la prohibición de la explotación minera metálica a gran escala en la zona de "
            f"recarga hídrica del río {river}, {ETAPA}"
        ),
        yes,
        no,
    )
    for river, yes, no in (
        ("Tarqui", 222552, 60451),
        ("Yanuncay", 218719, 54845),
        ("Tomebamba", 218591, 52914),
        ("Machángara", 217977, 52684),
        ("Norcay", 217856, 52625),
    )
)
AREA = (
    "dentro del Área de Importancia Ecológica, Cultural y de Desarrollo Productivo Sostenible conformada por los "
    "territorios de las parroquias de Nono, Calacalí, Nanegal, Nanegalito, Gualea y Pacto, que conforman la "
    "Mancomunidad del Chocó Andino?"
)
CHOCO = tuple(
    (
        f"Ban {english} metallic mining in the Chocó Andino parishes",
        f"¿Está Usted de acuerdo con que se prohíba la explotación de minería metálica {spanish} {AREA}",
        yes,
        no,
    )
    for english, spanish, yes, no in (
        ("artisanal", "de escala artesanal", 990997, 465400),
        ("small-scale", "a pequeña escala", 975274, 458463),
        ("medium-scale", "a mediana escala", 988455, 454490),
        ("large-scale", "a gran escala", 985408, 452963),
    )
)
CANTONAL = (
    Questions(
        "2021-02-07",
        "cuenca-agua",
        "2021 Cuenca popular consultation on mining near water sources",
        "Consulta popular por el agua en el cantón Cuenca 2021",
        BOOK_2021,
        "260",
        "Cuenca",
        CUENCA,
        "The book prints each question's yes and no votes for the whole canton of Cuenca.",
    ),
    Questions(
        "2023-08-20",
        "quito-choco-andino",
        "2023 Quito popular consultation on mining in the Chocó Andino",
        "Consulta popular Chocó Andino 2023",
        BOOK_2023,
        "60",
        "Quito",
        CHOCO,
        "The book prints each question's yes and no votes for the whole Distrito Metropolitano de Quito, and the "
        "percentages by parish, which are not stored here because the book gives no counts for the parishes.",
    ),
)
DAYS = [v.day for v in (*TERRITORIAL, *CANTONAL)]


def artifact(url: str) -> raw.Artifact:
    return raw.cached(SOURCE, url)


def fetch() -> None:
    for url in BOOKS:
        raw.fetch(SOURCE, url, suffix=".pdf")


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{v.day}-{v.slug}" for v in (*TERRITORIAL, *CANTONAL))]


def registry() -> Package:
    p = Package(REGISTRY, SOURCE, "The source and decision rule of Ecuador's local popular consultations")
    p.external.add(ECUADOR)
    p.add(
        "source",
        id=CNE_SOURCE,
        kind="official_body",
        publisher="Consejo Nacional Electoral del Ecuador",
        title="Statistics books published by the National Electoral Council",
        url=PAGE,
        license=LICENSE,
        attribution=ATTRIBUTION,
    )
    p.add("electoral_system", id=SYSTEM, polity_id=ECUADOR, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each question has two answers, and the answer with more of the valid votes carries it.",
        lang="en",
    )
    p.add(
        "scheme",
        code=ZONES,
        polity_id=ECUADOR,
        kind="electoral_district",
        rank=4,
        noun="electoral zone of a consultation",
    )
    return p


def parse() -> None:
    registry().write()
    for vote in TERRITORIAL:
        territorial(vote).write()
    for vote in CANTONAL:
        cantonal(vote).write()


def emit() -> None:
    from ea import package

    package.run("load", packages())


def page_text(url: str, first: int, last: int) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", "-f", str(first), "-l", str(last), str(artifact(url).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def numbers(text: str) -> set[int]:
    return {int(n.replace(".", "").replace(" ", "")) for n in re.findall(r"\d{1,3}(?:[. ]\d{3})+|\d+", text)}


def check_printed(vote: Territorial) -> None:
    """The book prints the official totals as the chart's headline; every total must appear on the page."""
    text = page_text(vote.book, vote.first_page, vote.first_page)
    found = numbers(text)
    missing = [n for n in (*vote.printed, vote.cast) if n not in found]
    if missing:
        raise ValueError(f"{vote.slug}: the book page does not print {missing}")


def start(vote: Territorial | Questions, summary: str) -> tuple[Package, str]:
    p = Package(f"{SOURCE}/{vote.day}-{vote.slug}", SOURCE, summary, depends=[REGISTRY])
    p.external.add(ECUADOR)
    p.artifact(artifact(vote.book), CNE_SOURCE)
    election = key("election", vote.day, vote.slug)
    base = f"ec/{vote.day}-{vote.slug}"
    p.add(
        "election",
        id=election,
        polity_id=ECUADOR,
        kind="referendum",
        date_from=date.fromisoformat(vote.day),
        date_to=date.fromisoformat(vote.day),
        status="held",
    )
    p.add("slug", path=base, entity_id=election, is_current=True)
    p.name(election, vote.english, "en", "official")
    p.name(election, vote.spanish, "es", "native", False)
    return p, election


def contest(
    p: Package,
    election: str,
    token: str,
    english: str,
    spanish: str,
    base: str,
    district: str | None,
    labels: tuple[tuple[str, str, str], tuple[str, str, str]],
    wording: str,
    method: str,
    type_: str = "referendum",
) -> tuple[str, str, tuple[str, str]]:
    """One ballot question with two answers; returns the contest, its result set, and the two options."""
    c = key("contest", election, token)
    p.add(
        "contest",
        id=c,
        election_id=election,
        kind="ballot_question",
        district_area_id=district,
        electoral_system_id=SYSTEM,
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=c, type=type_)
    p.name(c, english, "en", "english", False)
    p.name(c, spanish, "es", "native", True)
    p.add("slug", path=f"{base}/{token}", entity_id=c, is_current=True)
    if wording:
        p.add("note", id=key("note", c), entity_id=c, kind="context", body_md=wording, lang="es")
    options = []
    for position, (label, es, en) in enumerate(labels, 1):
        option = key("option", c, label)
        options.append(option)
        p.add("contest_option", id=option, contest_id=c, kind="answer", ballot_position=position, ballot_label=label)
        p.name(option, es, "es", "ballot")
        if en != es:
            p.name(option, en, "en", "english", False)
    result_set = key("result_set", election, token)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=c,
        stage="official",
        kind="counted",
        source_id=CNE_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    return c, result_set, (options[0], options[1])


def values(result_set: str, rows: list[tuple[str, str | None, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
        result_set_id=pl.lit(result_set),
        channel=pl.lit(0, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        flags=pl.lit(0, pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
    )


def territorial(vote: Territorial) -> Package:
    check_printed(vote)
    if tuple(sum(getattr(z, f) for z in vote.zones) for f in ("first", "second", "null", "blank")) != vote.printed:
        raise ValueError(f"{vote.slug}: the zones do not add up to the printed totals")
    p, election = start(vote, f"The {vote.english} by electoral zone, from the Council's book")
    base = f"ec/{vote.day}-{vote.slug}"
    es, en = vote.options, vote.options
    c, result_set, (first, second) = contest(
        p,
        election,
        "provincia",
        vote.english,
        vote.spanish,
        base,
        None,
        ((es[0], es[0], en[0]), (es[1], es[1], en[1])),
        "",
        "Residents of the zone vote for one of the two provinces; the province with more of the valid votes "
        f"takes the zone. {vote.method} The book prints {vote.cast:,} voters who signed the register, "
        f"against {sum(vote.printed):,} valid, null, and blank votes in its results; the votes are stored as printed.",
        "plebiscite",
    )
    p.add("note", id=key("note", c, "topic"), entity_id=c, kind="context", body_md=vote.topic + ".", lang="en")
    rows: list[tuple[str, str | None, int, int]] = []
    for number, zone in enumerate(vote.zones, 1):
        unit = key("unit", vote.day, number)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ECUADOR,
            scheme_code=ZONES,
            code=f"{vote.day}:{number}",
            area_relation="unlocated",
            kind="mobile" if zone.name == "Voto en casa" else "standard",
        )
        p.name(unit, zone.name, "es", "official")
        rows += [
            (unit, first, VOTES, zone.first),
            (unit, second, VOTES, zone.second),
            (unit, None, REGISTERED, zone.electors),
            (unit, None, VALID, zone.first + zone.second),
            (unit, None, INVALID, zone.null),
            (unit, None, BLANK, zone.blank),
        ]
    p.frame("result_value", values(result_set, rows))
    one, two, null, blank = vote.printed
    won = first if one > two else second
    p.add("outcome", result_set_id=result_set, option_id=won, kind="won")
    for option, measure, value in (
        (first, VOTES, one),
        (second, VOTES, two),
        (None, VALID, one + two),
        (None, INVALID, null),
        (None, BLANK, blank),
        (None, REGISTERED, sum(z.electors for z in vote.zones)),
    ):
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
    return p


def cantonal(vote: Questions) -> Package:
    p, election = start(vote, f"The {vote.english} by canton, from the Council's results book")
    area = cne.area("ec.canton", vote.place_code)
    p.external.update([area, "ec.canton"])
    base = f"ec/{vote.day}-{vote.slug}"
    unit = key("unit", vote.day, vote.place_code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ECUADOR,
        scheme_code="ec.canton",
        code=f"{vote.day}:{vote.place_code}",
        area_relation="equals",
        area_id=area,
        kind="aggregate",
    )
    p.name(unit, vote.place, "es", "official")
    year = vote.day[:4]
    for number, (topic, wording, yes, no) in enumerate(vote.questions, 1):
        _, result_set, (yes_option, no_option) = contest(
            p,
            election,
            f"pregunta-{number}",
            f"{year} {vote.place} popular consultation, question {number}: {topic}",
            f"Pregunta {number} de la consulta popular {vote.place} {year}",
            base,
            area,
            (("Sí", "Sí", "Yes"), ("No", "No", "No")),
            wording,
            "Each answer is counted for the whole canton. The winning answer is the one with more of the valid "
            f"votes. {vote.method}",
        )
        p.frame(
            "result_value",
            values(
                result_set,
                [(unit, yes_option, VOTES, yes), (unit, no_option, VOTES, no), (unit, None, VALID, yes + no)],
            ),
        )
        p.add("outcome", result_set_id=result_set, option_id=yes_option if yes > no else no_option, kind="won")
    return p
