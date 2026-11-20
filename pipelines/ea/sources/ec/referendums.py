"""Ecuador's national referendums and popular consultations of 2018, 2023, 2024, and 2025, by parish.

The National Electoral Council publishes one parish-level results file for each vote, and this module reads the four
files that carry national questions: the 4 February 2018 referendum and popular consultation, the referendum held with
the sectional elections of 5 February 2023, the 21 April 2024 referendum and popular consultation, and the
16 November 2025 referendum and popular consultation. Each file has one row per parish, sex group, and question, and the
sex groups are added together here. Parishes keep the codes the Council gives them, so the areas have the same ids as
the areas of the Council's other parish-level files, and this source adds only the few areas those files do not hold.
"""

import functools
import json
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl
import pyreadstat

from ea import package, raw
from ea.package import Package
from ea.sources.ec import cne

SOURCE = "ec.referendums"
ECUADOR = cne.ECUADOR
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fECU"))
PAGE = cne.PAGE
DOWNLOAD = "https://www.cne.gob.ec/download/"
BOOK_2018 = f"{cne.BASE}/2022/05/RESULTADOS-REFERENDUM-Y-CONSULTA-POPULAR-2018.pdf"
BULLETIN_2024 = f"{cne.BASE}/2024/05/BOLETIN_DE_RESULTADOS_REFERENDUM_Y_CONSULTA_POPULAR_2024.pdf"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
SCHEMES = ("ec.provincia", "ec.canton", "ec.parroquia")
# The Council's own pipeline already holds most of these areas, under the same ids and with its own parent links: its
# geo package holds all but a few, and its 2025 geo package holds the parishes its geo package lacks. This source adds
# only the areas in neither, and references every other area by the id those packages loaded, as an external id.
NEW_AREAS = {
    "ec.canton": (242, 291, 421, 461, 471, 491, 927, 998, 999),
    "ec.parroquia": (5460, 6940, 7540, 9010, 9041, 9048, 9064, 9074, 9079, 9080, 9082, 9083, 9085, 9088, 9998, 9999),
}
# The days on which the Council's own pipeline already holds a parish reporting unit, by parish code, under the code
# "<day>:<parish>". Its package for such a day loads those units, so this source references them by the id that
# pipeline gives them and mints only the units it lacks. Only 2023-02-05, the day of the sectional elections, is shared.
SHARED_UNITS: dict[str, frozenset[int]] = {
    day: frozenset(codes)
    for day, codes in json.loads(Path(__file__).with_name("cne_parish_units.json").read_text()).items()
}
SYSTEM_RULES = {"formula": "referendum"}
LICENSE = "Official publication; reuse terms are not stated"
ATTRIBUTION = "Consejo Nacional Electoral del Ecuador"


class Question(NamedTuple):
    token: str  # the question's address within its vote, such as "referendum-1" or "a"
    section: str  # "referendum" or "consulta"
    label: str  # how the ballot numbers it, in Spanish
    topic: str  # what it asks, in English, or an empty string when the files in hand do not say
    wording: str  # the question as the Council published it, or an empty string


class Vote(NamedTuple):
    day: str
    url: str
    suffix: str
    english: str
    spanish: str
    questions: tuple[Question, ...]
    books: tuple[str, ...]


def question(token: str, section: str, label: str, topic: str = "", wording: str = "") -> Question:
    return Question(token, section, label, topic, wording)


Q2018 = (
    question(
        "referendum-1",
        "referendum",
        "Referéndum, pregunta 1",
        "Disqualify people convicted of corruption from political life and strip them of their property",
        "¿Está usted de acuerdo con que se enmiende la Constitución de la República del Ecuador, para que se sancione "
        "a toda persona condenada por actos de corrupción con su inhabilitación para participar en la vida política "
        "del país, con la pérdida de sus bienes, según lo dispuesto en el Anexo 1?",
    ),
    question(
        "referendum-2",
        "referendum",
        "Referéndum, pregunta 2",
        "Limit every elected office to one re-election",
        "¿Para garantizar el principio de alternabilidad, está usted de acuerdo con enmendar la Constitución de la "
        "República del Ecuador para que todas las autoridades de elección popular puedan ser reelectas por una sola "
        "vez para el mismo cargo, recuperando el mandato de la Constitución de Montecristi y dejando sin efecto la "
        "reelección indefinida aprobada mediante enmienda por la Asamblea Nacional el 3 de diciembre de 2015, según "
        "lo establecido en el Anexo 2?",
    ),
    question(
        "referendum-3",
        "referendum",
        "Referéndum, pregunta 3",
        "Restructure the Citizen Participation and Social Control Council and end its members' terms",
        "¿Está usted de acuerdo con enmendar la Constitución de la República del Ecuador para reestructurar al "
        "Consejo de Participación Ciudadana y Control Social, así como dar por terminado el periodo constitucional de "
        "sus actuales miembros, y que el Consejo que asuma transitoriamente sus funciones tenga la potestad de "
        "evaluar el desempeño de las autoridades cuya designación le corresponde, pudiendo, de ser el caso, "
        "anticipar la terminación de sus periodos, de acuerdo al Anexo 3?",
    ),
    question(
        "referendum-4",
        "referendum",
        "Referéndum, pregunta 4",
        "Make sexual crimes against children and adolescents imprescriptible",
        "¿Está usted de acuerdo con enmendar la Constitución de la República del Ecuador para que nunca prescriban "
        "los delitos sexuales en contra de niñas, niños y adolescentes, según el Anexo 4?",
    ),
    question(
        "referendum-5",
        "referendum",
        "Referéndum, pregunta 5",
        "Ban metallic mining in protected areas, intangible zones, and urban centers",
        "¿Está usted de acuerdo con enmendar la Constitución de la República del Ecuador para que se prohíba sin "
        "excepción la minería metálica en todas sus etapas, en áreas protegidas, zonas intangibles y centros "
        "urbanos, de conformidad con lo establecido en el Anexo 5?",
    ),
    question(
        "consulta-1",
        "consulta",
        "Consulta popular, pregunta 1",
        "Repeal the law against speculation in land values, known as the capital gains law",
        "¿Está usted de acuerdo con que se derogue la Ley Orgánica para Evitar la Especulación sobre el Valor de las "
        "Tierras y Fijación de Tributos, conocida como «Ley de Plusvalía», según el Anexo 1?",
    ),
    question(
        "consulta-2",
        "consulta",
        "Consulta popular, pregunta 2",
        "Enlarge the Yasuní intangible zone and shrink the authorized oil area",
        "¿Está usted de acuerdo en incrementar la zona intangible en al menos 50.000 hectáreas y reducir el área de "
        "explotación petrolera autorizada por la Asamblea Nacional en el Parque Nacional Yasuní de 1.030 hectáreas a "
        "300 hectáreas?",
    ),
)

Q2023 = tuple(question(f"referendum-{n}", "referendum", f"Referéndum, pregunta {n}") for n in range(1, 9))

Q2024 = (
    question(
        "a",
        "referendum",
        "Referéndum, casillero A",
        "Allow the armed forces to support the National Police against organized crime",
        "¿Está usted de acuerdo con que se permita el apoyo complementario de las Fuerzas Armadas en las funciones de "
        "la Policía Nacional para combatir el crimen organizado, reformando parcialmente la Constitución de "
        "conformidad con lo previsto en el Anexo 1?",
    ),
    question(
        "b",
        "referendum",
        "Referéndum, casillero B",
        "Allow the extradition of Ecuadorians",
        "¿Está usted de acuerdo con permitir la extradición de ecuatorianos, con las condiciones, requisitos, "
        "restricciones e impedimentos establecidos en la Constitución, los instrumentos internacionales y en la Ley, "
        "enmendando la Constitución y reformando las leyes, conforme el Anexo 1?",
    ),
    question(
        "c",
        "referendum",
        "Referéndum, casillero C",
        "Create specialized constitutional courts of first and second instance",
        "¿Está usted de acuerdo con el establecimiento de judicaturas especializadas en materia constitucional, tanto "
        "en primera como en segunda instancia, para el conocimiento de las garantías jurisdiccionales que les "
        "corresponda, enmendando la Constitución y reformando la Ley Orgánica de Garantías Jurisdiccionales y Control "
        "Constitucional, de acuerdo con el Anexo 2?",
    ),
    question(
        "d",
        "referendum",
        "Referéndum, casillero D",
        "Recognize international arbitration for investment, contract, and commercial disputes",
        "¿Está usted de acuerdo que el Estado ecuatoriano reconozca el arbitraje internacional como método para "
        "solucionar controversias en materia de inversión, contractuales o comerciales?",
    ),
    question(
        "e",
        "referendum",
        "Referéndum, casillero E",
        "Allow fixed-term and hourly employment contracts",
        "¿Está usted de acuerdo con enmendar la Constitución de la República y reformar el Código de Trabajo para el "
        "contrato de trabajo a plazo fijo y por horas, cuando se celebre por primera vez entre el mismo empleador y "
        "trabajador, sin afectar los derechos adquiridos de los trabajadores, de acuerdo con el Anexo 4?",
    ),
    question(
        "f",
        "consulta",
        "Consulta popular, casillero F",
        "Have the armed forces control weapons on routes into prisons",
        "¿Está usted de acuerdo con que las Fuerzas Armadas realicen control de armas, municiones, explosivos y "
        "accesorios, permanentemente, en las rutas, caminos, vías y corredores autorizados para el ingreso a los "
        "centros de rehabilitación social?",
    ),
    question(
        "g",
        "consulta",
        "Consulta popular, casillero G",
        "Raise the penalties for ten serious crimes",
        "¿Está usted de acuerdo con que se incrementen las penas de los delitos de: (i) terrorismo y su "
        "financiación, (ii) producción y tráfico ilícito de sustancias catalogadas sujetas a fiscalización, (iii) "
        "delincuencia organizada, (iv) asesinato, (v) sicariato, (vi) trata de personas, (vii) secuestro extorsivo, "
        "(viii) tráfico de armas, (ix) lavado de activos y (x) actividad ilícita de recursos mineros, reformando el "
        "Código Orgánico Integral Penal conforme el Anexo de la pregunta?",
    ),
    question(
        "h",
        "consulta",
        "Consulta popular, casillero H",
        "Require prisoners to serve their whole sentence inside the prison for listed crimes",
        "¿Está usted de acuerdo con que las personas privadas de la libertad cumplan la totalidad de su pena dentro "
        "del centro de rehabilitación social en los delitos detallados en el Anexo de la pregunta, reformando el "
        "Código Orgánico Integral Penal conforme consta en el referido Anexo?",
    ),
    question(
        "i",
        "consulta",
        "Consulta popular, casillero I",
        "Make holding weapons reserved to the armed forces or the police a crime",
        "¿Está usted de acuerdo con que se tipifique el delito de tenencia o porte de armas, municiones o "
        "componentes que son de uso privativo de las Fuerzas Armadas o la Policía Nacional, sin afectar a las armas "
        "de fuego permitidas para uso civil, reformando el Código Orgánico Integral Penal conforme el Anexo de la "
        "pregunta?",
    ),
    question(
        "j",
        "consulta",
        "Consulta popular, casillero J",
        "Let the police and armed forces use weapons seized as evidence",
        "¿Está usted de acuerdo con que las armas, sus partes o piezas, explosivos, municiones o accesorios que "
        "fueron instrumentos u objeto material de un delito, puedan destinarse al uso inmediato de la Policía "
        "Nacional o las Fuerzas Armadas, reformando el Código Orgánico Integral Penal conforme el Anexo de la "
        "pregunta?",
    ),
    question(
        "k",
        "consulta",
        "Consulta popular, casillero K",
        "Simplify the forfeiture of property of illicit or unjustified origin",
        "¿Está usted de acuerdo con que el Estado proceda a ser el titular (propietario) de los bienes de origen "
        "ilícito o injustificado, simplificando el procedimiento de la Ley Orgánica de Extinción de Dominio, conforme "
        "el Anexo de la pregunta?",
    ),
)

Q2025 = (
    question("a", "referendum", "Referéndum, casillero A"),
    question("b", "referendum", "Referéndum, casillero B"),
    question("c", "referendum", "Referéndum, casillero C"),
    question("d", "consulta", "Consulta popular, casillero D"),
)

VOTES_BY_DAY = (
    Vote(
        "2018-02-04",
        f"{DOWNLOAD}consulta-popular-2018/?wpdmdl=21143",
        ".sav",
        "2018 Ecuadorian referendum and popular consultation",
        "Referéndum y consulta popular del Ecuador 2018",
        Q2018,
        (BOOK_2018,),
    ),
    Vote(
        "2023-02-05",
        f"{DOWNLOAD}resultados-electorales-2023/?wpdmdl=39834",
        ".sav",
        "2023 Ecuadorian referendum",
        "Referéndum del Ecuador 2023",
        Q2023,
        (),
    ),
    Vote(
        "2024-04-21",
        f"{DOWNLOAD}consulta-popular-2024/?wpdmdl=50125",
        ".sav",
        "2024 Ecuadorian referendum and popular consultation",
        "Referéndum y consulta popular del Ecuador 2024",
        Q2024,
        (BULLETIN_2024,),
    ),
    Vote(
        "2025-11-16",
        f"{DOWNLOAD}referendum-y-consulta-popular-2025/?wpdmdl=62026",
        ".sav",
        "2025 Ecuadorian referendum and popular consultation",
        "Referéndum y consulta popular del Ecuador 2025",
        Q2025,
        (),
    ),
)
VOTE = {vote.day: vote for vote in VOTES_BY_DAY}
DAYS = {vote.day: date.fromisoformat(vote.day) for vote in VOTES_BY_DAY}

# The question each 2023 to 2025 file row group belongs to: the file's dignity code, then the question's token.
DIGNITY = {
    "2023-02-05": {19 + n: f"referendum-{n + 1}" for n in range(8)},
    "2024-04-21": {11 + n: letter for n, letter in enumerate("abcdefghijk")},
    "2025-11-16": {11 + n: letter for n, letter in enumerate("abcd")},
}
# The code of a parish that the 2018 file gives to Fajardo, which every later file lists under another code; the 2018
# code belongs to Punzara in the later files.
RECODED = {"2018-02-04": {7530: 7535}}

# The national totals each source prints: blank ballots, null ballots, yes, no, and valid votes (None where unprinted).
PRINTED_2018 = {
    "referendum-1": (386817, 585167, None, None, 9546377),
    "referendum-2": (378973, 610738, None, None, 9525888),
    "referendum-3": (412276, 619393, None, None, 9484858),
    "referendum-4": (433070, 618414, None, None, 9465280),
    "referendum-5": (479625, 585081, None, None, 9452764),
    "consulta-1": (450745, 609729, None, None, 9456436),
    "consulta-2": (538273, 563948, None, None, 9415553),
}
PRINTED_2024 = {
    "a": (378207, 616550, 7079407, 2720584, 9799991),
    "b": (438643, 620216, 6263941, 3472059, 9736000),
    "c": (535340, 627892, 5771174, 3860518, 9631692),
    "d": (503656, 777339, 3314564, 6199181, 9513745),
    "e": (520873, 722402, 2912979, 6638627, 9551606),
    "f": (482862, 629997, 6744840, 2937293, 9682133),
    "g": (521088, 615235, 6504098, 3154672, 9658770),
    "h": (536053, 613922, 6431992, 3213131, 9645123),
    "i": (564967, 613511, 6144794, 3471562, 9616356),
    "j": (582032, 605000, 6155324, 3452749, 9608073),
    "k": (612427, 598447, 5846059, 3738144, 9584203),
}
PRINTED = {"2018-02-04": PRINTED_2018, "2024-04-21": PRINTED_2024}
# Parish-question rows whose own figures disagree: rows, of how many, the largest gap in ballots, and the largest
# share of a row's ballots.
DISAGREE = {
    "2018-02-04": (3602, 9156, 47, "0.8%"),
    "2023-02-05": (5597, 10400, 101, "0.9%"),
    "2024-04-21": (5387, 14333, 97, "0.8%"),
    "2025-11-16": (1982, 5208, 88, "0.5%"),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CNE_SOURCE = key("source", "cne")
SYSTEM = key("electoral_system", "referendum")


def artifact(url: str) -> raw.Artifact:
    return raw.cached(SOURCE, url)


def held(url: str) -> bool:
    """Whether the raw cache holds a file; the Council's host refuses some downloads, so a build uses what it has."""
    try:
        artifact(url)
    except LookupError:
        return False
    return True


def days() -> list[str]:
    """The votes whose results file is in the raw cache, in date order."""
    return [vote.day for vote in VOTES_BY_DAY if held(vote.url)]


def fetch() -> None:
    for vote in VOTES_BY_DAY:
        raw.fetch(SOURCE, vote.url, suffix=vote.suffix)
    for url in (BOOK_2018, BULLETIN_2024):
        raw.fetch(SOURCE, url, suffix=".pdf")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days())]


def parse() -> None:
    geo().write()
    registry().write()
    for day in days():
        build(day).write()


def emit() -> None:
    package.run("load", packages())


class Read(NamedTuple):
    results: pl.DataFrame  # one row per question token and parish: yes, no, blank, null, cast
    places: pl.DataFrame  # one row per area: level, code, name, parent


@functools.cache
def sav(url: str) -> tuple[pl.DataFrame, dict[str, dict[float, str]]]:
    frame, meta = pyreadstat.read_sav(str(artifact(url).path), apply_value_formats=False, output_format="polars")
    if not isinstance(frame, pl.DataFrame):
        raise TypeError(f"{url} did not read as a polars frame")
    return frame, {name: dict(labels) for name, labels in meta.variable_value_labels.items()}


def named(frame: pl.DataFrame, labels: dict[str, dict[float, str]], column: str) -> pl.Series:
    """A name column as text, whether the file stores the text or a coded value with labels."""
    series = frame[column]
    if series.dtype == pl.String:
        return series.map_elements(cne.clean, return_dtype=pl.String)
    return series.map_elements(lambda value: cne.clean(labels[column][float(value)]), return_dtype=pl.String)


def parishes(vote: Vote, frame: pl.DataFrame, labels: dict[str, dict[float, str]]) -> pl.DataFrame:
    recoded = RECODED.get(vote.day, {})
    frame = frame.with_columns(
        pl.col("PROVINCIA_CODIGO").cast(pl.Int64).alias("province"),
        pl.col("CANTON_CODIGO").cast(pl.Int64).alias("canton"),
        pl.col("PARROQUIA_CODIGO").cast(pl.Int64).replace(recoded).alias("parish"),
        named(frame, labels, "PROVINCIA_NOMBRE").alias("province_name"),
        named(frame, labels, "CANTON_NOMBRE").alias("canton_name"),
        named(frame, labels, "PARROQUIA_NOMBRE").alias("parish_name"),
    )
    return frame


def places(frame: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for level, code, name, parent in (
        ("ec.provincia", "province", "province_name", None),
        ("ec.canton", "canton", "canton_name", "province"),
        ("ec.parroquia", "parish", "parish_name", "canton"),
    ):
        part = frame.select(code, name, *([parent] if parent else [])).unique()
        for row in part.iter_rows():
            rows.append((level, row[0], row[1], row[2] if parent else None))
    return pl.DataFrame(rows, schema=["level", "code", "name", "parent"], orient="row")


def read_wide(vote: Vote) -> Read:
    """The 2018 file: one row per parish, sex group, and question, with yes and no in two columns."""
    frame, labels = sav(vote.url)
    frame = parishes(vote, frame, labels)
    tokens = {n + 1: q.token for n, q in enumerate(vote.questions)}
    results = (
        frame.with_columns(pl.col("NUMERO_PREGUNTA").cast(pl.Int64).replace_strict(tokens).alias("q"))
        .group_by("q", "parish")
        .agg(
            pl.col("VOTOS_SI").sum().cast(pl.Int64).alias("yes"),
            pl.col("VOTOS_NO").sum().cast(pl.Int64).alias("no"),
            pl.col("BLANCOS").sum().cast(pl.Int64).alias("blank"),
            pl.col("NULOS").sum().cast(pl.Int64).alias("null"),
            pl.col("SUFRAGANTES").sum().cast(pl.Int64).alias("cast"),
        )
    )
    return Read(results, places(frame))


def read_long(vote: Vote) -> Read:
    """The 2023 to 2025 files: one row per parish, sex group, question, and answer, each repeating the accounting."""
    frame, labels = sav(vote.url)
    frame = parishes(vote, frame, labels)
    answer = "CANDIDATO_NOMBRE" if vote.day == "2023-02-05" else "OPCION_NOMBRE"
    tokens = DIGNITY[vote.day]
    kept = frame.filter(pl.col("DIGNIDAD_CODIGO").cast(pl.Int64).is_in(list(tokens)))
    group = ["DIGNIDAD_CODIGO", "parish", "JUNTA_SEXO"]
    if "CIRCUNSCRIPCION_CODIGO" in kept.columns:
        group.append("CIRCUNSCRIPCION_CODIGO")
    groups = kept.group_by(group).agg(
        pl.len().alias("rows"),
        pl.col(answer).n_unique().alias("answers"),
        pl.col("SUFRAGANTES").n_unique().alias("a"),
        pl.col("BLANCOS").n_unique().alias("b"),
        pl.col("NULOS").n_unique().alias("c"),
        pl.col("SUFRAGANTES").first().alias("cast"),
        pl.col("BLANCOS").first().alias("blank"),
        pl.col("NULOS").first().alias("null"),
        pl.col("VOTOS").filter(pl.col(answer) == "SI").sum().alias("yes"),
        pl.col("VOTOS").filter(pl.col(answer) == "NO").sum().alias("no"),
    )
    if groups.filter(
        (pl.col("rows") != 2) | (pl.col("answers") != 2) | (pl.col("a") + pl.col("b") + pl.col("c") != 3)
    ).height:
        raise ValueError(
            f"{vote.day}: a parish group does not hold one yes row and one no row with the same accounting"
        )
    results = (
        groups.with_columns(pl.col("DIGNIDAD_CODIGO").cast(pl.Int64).replace_strict(tokens).alias("q"))
        .group_by("q", "parish")
        .agg(pl.col(c).sum().cast(pl.Int64) for c in ("yes", "no", "blank", "null", "cast"))
    )
    return Read(results, places(kept))


@functools.cache
def read(day: str) -> Read:
    return read_wide(VOTE[day]) if day == "2018-02-04" else read_long(VOTE[day])


def places_all() -> dict[str, dict[int, tuple[str, int | None]]]:
    """Every area the files in hand list; a later file's spelling replaces an earlier one, and a parent may not change."""
    found: dict[str, dict[int, tuple[str, int | None]]] = {scheme: {} for scheme in SCHEMES}
    for day in days():
        for level, code, name, parent in read(day).places.iter_rows():
            if code in found[level] and found[level][code][1] != parent:
                raise ValueError(f"{level} {code} sits under {found[level][code][1]} and {parent}")
            found[level][code] = (name, parent)
    return found


def area(scheme: str, code: int) -> str:
    return cne.area(scheme, str(code))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The cantons, parishes, and overseas units that Ecuador's referendum results files list and the Council's "
        "other files do not",
    )
    p.external.update([ECUADOR, *SCHEMES])
    p.add(
        "source",
        id=CNE_SOURCE,
        kind="official_body",
        publisher="Consejo Nacional Electoral del Ecuador",
        title="Referendum and popular consultation results files and books published by the National Electoral Council",
        url=PAGE,
        license=LICENSE,
        attribution=ATTRIBUTION,
    )
    for vote in VOTES_BY_DAY:
        for url in (vote.url, *vote.books):
            if held(url):
                p.artifact(artifact(url), CNE_SOURCE)
    found = places_all()
    parent_scheme = {"ec.canton": "ec.provincia", "ec.parroquia": "ec.canton"}
    for scheme, codes in NEW_AREAS.items():
        for code in codes:
            if code not in found[scheme]:
                continue  # listed only by a file that is not in hand
            name, parent = found[scheme][code]
            id = area(scheme, code)
            p.add("area", id=id, scheme_code=scheme)
            p.add("area_code", area_id=id, code=str(code))
            if parent is not None:
                parent_id = area(parent_scheme[scheme], parent)
                p.add("area_parent", area_id=id, parent_id=parent_id)
                if parent not in NEW_AREAS.get(parent_scheme[scheme], ()):
                    p.external.add(parent_id)
            p.name(id, cne.title(name), "es", "official")
    return p


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "The decision rule of Ecuador's referendums and popular consultations")
    p.external.add(ECUADOR)
    p.add("electoral_system", id=SYSTEM, polity_id=ECUADOR, rules=SYSTEM_RULES, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each question has a yes and a no answer, and the answer with more of the valid votes carries it.",
        lang="en",
    )
    return p


def method(vote: Vote) -> str:
    text = (
        "Counted per parish and published by the National Electoral Council in its parish-level results file. Each "
        "parish's polling stations are grouped into men's and women's stations, and the parish figures here add the "
        "groups. Ballots cast are the voters who took part, and valid votes are the yes and no votes together. Blank "
        "and null ballots are counted for each question separately."
    )
    if vote.day == "2023-02-05":
        text += (
            " A parish that holds several urban circumscriptions is published once for each circumscription, and the "
            "parish figures add them."
        )
    return text


def build(day: str) -> Package:
    vote = VOTE[day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Ecuadorian referendum questions of {DAYS[day]} by parish, from the National Electoral Council's results file",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.update([ECUADOR, COUNTRY, *SCHEMES])
    data = read(day)
    p.uses(artifact(vote.url))
    books = [url for url in vote.books if held(url)]
    for url in books:
        p.uses(artifact(url))
    names = {code: name for code, (name, _) in places_all()["ec.parroquia"].items()}
    election = key("election", day)
    base = f"ec/{day}-referendum"
    p.add(
        "election",
        id=election,
        polity_id=ECUADOR,
        kind="referendum",
        date_from=DAYS[day],
        date_to=DAYS[day],
        status="held",
    )
    p.add("slug", path=base, entity_id=election, is_current=True)
    p.name(election, vote.english, "en", "official")
    p.name(election, vote.spanish, "es", "native", False)
    units: dict[int, str] = {}
    shared = SHARED_UNITS.get(day, frozenset())
    for code in sorted(data.results["parish"].unique().to_list()):
        if code in shared:
            units[code] = cne.key("unit", day, "parish", code)
            p.external.add(units[code])
            continue
        unit = units[code] = key("unit", day, code)
        if code not in NEW_AREAS["ec.parroquia"]:
            p.external.add(area("ec.parroquia", code))
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ECUADOR,
            scheme_code="ec.parroquia",
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=area("ec.parroquia", code),
            kind="standard",
        )
        p.name(unit, cne.title(names[code]), "es", "official")
    # The printed totals are declared only when the book that prints them is in hand, so each traces to a file.
    printed = PRINTED.get(day, {}) if books else {}
    for asked in vote.questions:
        token = asked.token
        contest = key("contest", day, token)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=COUNTRY,
            electoral_system_id=SYSTEM,
            round_number=1,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        year = day[:4]
        english = f"{year} Ecuadorian {'referendum' if asked.section == 'referendum' else 'popular consultation'}"
        english += f", {asked.label.split(', ', 1)[1]}"
        if asked.topic:
            english += f": {asked.topic}"
        p.name(contest, english, "en", "official" if not asked.topic else "english", False)
        p.name(contest, f"{asked.label} {year}", "es", "native", True)
        p.add("slug", path=f"{base}/{token}", entity_id=contest, is_current=True)
        if asked.wording:
            p.add(
                "note",
                id=key("note", contest, "question"),
                entity_id=contest,
                kind="context",
                body_md=asked.wording,
                lang="es",
            )
        yes, no = (key("option", contest, name) for name in ("yes", "no"))
        for position, (option, es, en) in enumerate(((yes, "Sí", "Yes"), (no, "No", "No")), 1):
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=es,
            )
            p.name(option, es, "es", "ballot")
            p.name(option, en, "en", "english", False)
        result_set = key("result_set", day, token)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CNE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=method(vote),
            lang="en",
        )
        rows = data.results.filter(pl.col("q") == token).sort("parish")
        if rows.height != len(units):
            raise ValueError(f"{day} {token}: {rows.height} parishes, not {len(units)}")
        total_yes, total_no = int(rows["yes"].sum()), int(rows["no"].sum())
        p.add("outcome", result_set_id=result_set, option_id=yes if total_yes > total_no else no, kind="won")
        values = []
        for parish, y, n, blank, null, cast in rows.select("parish", "yes", "no", "blank", "null", "cast").iter_rows():
            unit = units[parish]
            values += [
                (unit, yes, VOTES, y),
                (unit, no, VOTES, n),
                (unit, None, CAST, cast),
                (unit, None, VALID, y + n),
                (unit, None, INVALID, null),
                (unit, None, BLANK, blank),
            ]
        p.frame(
            "result_value",
            pl.DataFrame(values, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
                result_set_id=pl.lit(result_set),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )
        if token in printed:
            blank, null, printed_yes, printed_no, valid = printed[token]
            for option, measure, value in (
                (None, BLANK, blank),
                (None, INVALID, null),
                (None, VALID, valid),
                (yes, VOTES, printed_yes),
                (no, VOTES, printed_no),
            ):
                if value is not None:
                    p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
    rows, of, most, share = DISAGREE[day]
    p.accept(
        "accounting.ballots_sum",
        f"the file's own parish figures disagree: valid votes plus blank and null votes differ from the ballots cast in "
        f"{rows:,} of {of:,} parish-question rows, by {most} ballots at most and by under {share} of a row's ballots, "
        "in both directions; stored as published",
    )
    return p
