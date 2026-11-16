"""Peru's national elections by polling table (mesa) from the Oficina Nacional de Procesos Electorales.

ONPE publishes its count of each polling table on Peru's open data portal, one dataset per election and contest, from
the regional integration referendum of 2005 to the general election of 2021. A file holds one row per table (the
presidential rounds and the referendums) or one row per table and list (Congress and the Andean Parliament), with the
table's district by ONPE's own geographic code (ubigeo), its registered voters, the citizens who voted, and the votes
of each option plus blank, null, and objected votes. The preference votes in the list files carry only the candidate's
number on the list, so they are not stored.

The elections and contests themselves come from the Jurado Nacional de Elecciones' archive (`pe.jne`), whose
constituency totals are the official declared results. Each package here adds to every contest of one election day a
second, finer result set: the polling-table count, which is not the default and records how much of the official
valid vote it holds. Each file's lists are matched to the contest's options by name.
"""

import difflib
import io
import re
import unicodedata
import zipfile
from collections import Counter
from datetime import date
from functools import cache
from typing import NamedTuple
from uuid import uuid5

import fastexcel
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.pe import jne

SOURCE = "pe.onpe"
FILES = "https://www.datosabiertos.gob.pe/sites/default/files/"
PORTAL = "https://www.datosabiertos.gob.pe/dataset/"
PERU = jne.PERU_POLITY
MESA = "pe.mesa"
DEPARTMENT, PROVINCE, DISTRICT = "pe.ubigeo.departamento", "pe.ubigeo.provincia", "pe.ubigeo.distrito"
ABROAD = {"91", "92", "93", "94", "95"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ONPE = key("source", SOURCE)


class Dataset(NamedTuple):
    day: date
    contest: str
    file: str
    dictionary: str
    page: str


DATASETS = [
    Dataset(
        date(2000, 4, 9),
        "president",
        "Resultados%20por%20distrito%20de%20las%20Elecciones%20Generales%20-%20Primera%20Vuelta.csv",
        "Diccionario%20de%20los%20resultados%20por%20mesa%20%20de%20las%20Elecciones%20Generales%20-%20Primera%20vuelta.xlsx",
        "resultados-por-distrito-de-las-generales-2000-primera-vuelta-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2000, 4, 9),
        "congress",
        "Resultados%20por%20distrito%20de%20las%20Elecciones%20Congresales.csv",
        "Diccionario%20de%20los%20resultados%20por%20distrito%20de%20las%20Elecciones%20Congresales.xlsx",
        "resultados-por-distrito-de-las-elecciones-congresales-2000-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2000, 5, 28),
        "president",
        "Resultados%20por%20distrito%20de%20las%20Elecciones%20Generales%20-%20Segunda%20Vuelta.csv",
        "Diccionario%20de%20los%20resultados%20por%20distrito%20de%20las%20Elecciones%20Generales%20-%20Segunda%20Vuelta.xlsx",
        "resultados-por-distrito-de-las-generales-2000-segunda-vuelta-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2001, 6, 3),
        "president",
        "Resultados%20por%20distrito%20de%20las%20Elecciones%20Generales%20-%20Segunda%20Vuelta_0.csv",
        "Diccionario%20de%20los%20resultados%20por%20distrito%20de%20las%20Elecciones%20Generales%20-%20Segunda%20Vuelta_0.xlsx",
        "resultados-por-distrito-de-las-generales-2001-segunda-vuelta-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2005, 10, 30),
        "referendum",
        "Resultados%20por%20mesa%20del%20Refer%C3%A9ndum%202005.csv",
        "Diccionario%20de%20los%20resultados%20por%20mesa%20del%20Refer%C3%A9ndum%202005.xlsx",
        "resultados-por-mesa-del-refer%C3%A9ndum-2005-oficina-nacional-de-procesos-electorales-onpe",
    ),
    Dataset(
        date(2006, 4, 9),
        "president",
        "EG2006_Presidencial.zip",
        "Diccionario_Elecciones_Presidenciales_2006.xlsx",
        "resultados-por-mesa-de-las-generales-2006-primera-vuelta-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2006, 4, 9),
        "congress",
        "EG2006_Congresal.zip",
        "Diccionario_Elecciones_Congreso_2006.xlsx",
        "resultados-por-mesa-de-las-elecciones-congresales-2006-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2006, 4, 9),
        "andean",
        "EG2006_Parlamento.zip",
        "Diccionario_Elecciones_Parlamento_Andino_2006.xlsx",
        "resultados-por-mesa-del-parlamento-andino-2006-oficina-nacional-de-procesos-electorales-onpe",
    ),
    Dataset(
        date(2006, 6, 4),
        "president",
        "SEP2006_Presidencial.csv",
        "Diccionario_Segunda_Eleccion_Presidencial_2006.xlsx",
        "resultados-por-mesa-de-las-generales-2006-segunda-vuelta-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2010, 10, 3),
        "referendum",
        "Rerefendum_Nacional_2010.zip",
        "Diccionario_Referendum_2010.xlsx",
        "resultados-por-mesa-del-refer%C3%A9ndum-2010-oficina-nacional-de-procesos-electorales-onpe",
    ),
    Dataset(
        date(2011, 4, 10),
        "president",
        "2011_EG2011_Presidencial_0.zip",
        "Diccionario_Elecciones_Presidenciales_2011.xlsx",
        "resultados-por-mesa-de-las-elecciones-presidenciales-2011-primera-vuelta-oficina-nacional-de",
    ),
    Dataset(
        date(2011, 4, 10),
        "congress",
        "2011_EG2011_Congresal.zip",
        "Diccionario_Elecciones_Congreso_2011.xlsx",
        "resultados-por-mesa-de-las-elecciones-congresales-2011-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2011, 4, 10),
        "andean",
        "2011_EG2011_Parlamento.zip",
        "Diccionario_Elecciones_Parlamento_Andino_2011.xlsx",
        "resultados-por-mesa-de-las-elecciones-al-parlamento-andino-2011-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2011, 6, 5),
        "president",
        "2011_SEP2011_Presidencial.zip",
        "Diccionario_Segunda_Eleccion_Presidencial_2011.xlsx",
        "resultados-por-mesa-de-la-segunda-elecci%C3%B3n-presidencial-2011-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2016, 4, 10),
        "president",
        "2016_EG2016_Presidencial.csv",
        "Diccionario_Elecciones_Presidenciales_2016.xlsx",
        "resultados-por-mesa-de-las-elecciones-presidenciales-2016-primera-vuelta-oficina-nacional-de",
    ),
    Dataset(
        date(2016, 4, 10),
        "congress",
        "2016_EG2016_Congresal.zip",
        "Diccionario_Elecciones_Congreso_2016.xlsx",
        "resultados-por-mesa-de-las-elecciones-congresales-2016-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2016, 4, 10),
        "andean",
        "2016_EG2016_Parlamento.zip",
        "Diccionario_Elecciones_Parlamento_Andino_2016.xlsx",
        "resultados-por-mesa-de-las-elecciones-al-parlamento-andino-2016-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2016, 6, 5),
        "president",
        "2016_SEP2016_Presidencial.csv",
        "Diccionario_Segunda_Eleccion_Presidencial_2016.xlsx",
        "resultados-por-mesa-de-la-segunda-elecci%C3%B3n-presidencial-2016-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2018, 12, 9),
        "referendum",
        "Rerefendum_Nacional_2018.zip",
        "Diccionario_Referendum_Nacional_2018.xlsx",
        "resultados-por-mesa-del-refer%C3%A9ndum-nacional-2018-oficina-nacional-de-procesos-electorales",
    ),
    Dataset(
        date(2020, 1, 26),
        "congress",
        "ECE_Congresal_2020_total.zip",
        "Diccionario_Elecciones_Congresales_2020.xlsx",
        "resultados-por-mesa-de-las-elecciones-congresales-extraordinarias-2020-oficina-nacional-de",
    ),
    Dataset(
        date(2021, 4, 11),
        "president",
        "Resultados%20por%20mesa%20de%20las%20Elecciones%20Generales%202021.zip",
        "Diccionario%20de%20las%20Elecciones%20Generales%202021.xlsx",
        "resultados-por-mesa-de-las-elecciones-generales-2021-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2021, 4, 11),
        "congress",
        "EG2021_Congresal.zip",
        "Diccionario_Elecciones_Congresales_2021.xlsx",
        "resultados-por-mesa-de-las-elecciones-congresales-2021-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2021, 4, 11),
        "andean",
        "EG2021_Parlamento_Andino.zip",
        "Diccionario_Elecciones_Parlamento_Andino_2021.xlsx",
        "resultados-por-mesa-de-las-elecciones-al-parlamento-andino-2021-oficina-nacional-de-procesos",
    ),
    Dataset(
        date(2021, 6, 6),
        "president",
        "Resultados%20por%20mesa%20de%20la%20Segunda%20Elecci%C3%B3n%20Presidencial%202021.zip",
        "Diccionario%20de%20los%20resultados%20por%20mesa%20de%20la%20Segunda%20Elecci%C3%B3n%20Presidencial%202021.xlsx",
        "resultados-por-mesa-de-la-segunda-elecci%C3%B3n-presidencial-2021-oficina-nacional-de-procesos",
    ),
]
DAYS = sorted({d.day for d in DATASETS})
TRUNCATED = {"ECE_Congresal_2020_total.zip": 1_048_575}
SPECIAL = {"blank": 6, "null": 5, "objected": 15}
LONG_SPECIAL = {"80": "blank", "81": "null", "82": "objected"}
WIDE_SPECIAL = {"VOTOS_VB": "blank", "VOTOS_VN": "null", "VOTOS_VI": "objected"}
REFERENDUM = {"VOTOS_SI": "yes", "VOTOS_NO": "no", "VOTOS_BLANCOS": "blank", "VOTOS_NULOS": "null"}
REFERENDUM_SPECIAL = {"VOTOS_IMPUG": "objected"}
GAPS = {
    date(2011, 4, 10): (
        "The Jurado Nacional de Elecciones counted the votes of Fuerza Social, which withdrew its ticket, as null "
        "votes, while ONPE's file keeps them under the ticket."
    ),
    date(2016, 4, 10): (
        "The Jurado Nacional de Elecciones counted the votes of the lists that withdrew or were excluded as null "
        "votes (resolution 0309-2016-JNE), while ONPE's file keeps them under each list."
    ),
    date(2021, 6, 6): (
        "ONPE's file was taken before the count closed: twenty tables are still marked as in process (EN PROCESO)."
    ),
}
# ONPE's file names for a list where Infogob's differs too much for a name match.
ALIASES: dict[str, str] = {}
METHOD = (
    "Votes by polling table (mesa) as the Oficina Nacional de Procesos Electorales publishes them on Peru's open "
    "data portal. Registered voters are the table's electores hábiles and ballots cast are the citizens who voted "
    "(ciudadanos que votaron). Null votes are stored as invalid ballots and objected votes (votos impugnados) as "
    "objected ballots. A table whose record (acta) was annulled carries all of its votes as null votes, as ONPE "
    "counts it, and a table that was not installed carries only its registered voters. The constituency totals of "
    "the Jurado Nacional de Elecciones remain the default result."
)

BY_DISTRICT = (
    "Votes by district as the Oficina Nacional de Procesos Electorales publishes them on Peru's open data portal for "
    "the elections of 2000 and 2001, for which it publishes no polling-table file. Null votes are stored as invalid "
    "ballots. The constituency totals of the Jurado Nacional de Elecciones remain the default result."
)


def url(name: str) -> str:
    return FILES + name


def fetch() -> None:
    for dataset in DATASETS:
        for name in (dataset.file, dataset.dictionary):
            artifact = raw.fetch(SOURCE, url(name), suffix="." + name.rsplit(".", 1)[1])
            print(f"{dataset.day} {dataset.contest} {name}: {artifact.bytes:,} bytes", flush=True)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text).split())


def _bytes(name: str) -> tuple[str, bytes]:
    path = raw.cached(SOURCE, url(name)).path
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            inner = archive.namelist()[0]
            return inner, archive.read(inner)
    return name, path.read_bytes()


def _text(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    return text.lstrip("﻿")


def _table(name: str) -> pl.DataFrame:
    inner, data = _bytes(name)
    if inner.endswith(".xlsx"):
        return fastexcel.read_excel(data).load_sheet(0, dtypes="string").to_polars()
    return pl.read_csv(
        io.StringIO(_text(data)), separator=";", infer_schema=False, quote_char='"', truncate_ragged_lines=True
    )


def dictionary(dataset: Dataset) -> dict[str, str]:
    """The label of each wide column (VOTOS_P1 and on) from the dataset's data dictionary."""
    data = raw.cached(SOURCE, url(dataset.dictionary)).path.read_bytes()
    sheet = fastexcel.read_excel(data).load_sheet(0, header_row=None, dtypes="string").to_polars()
    found: dict[str, str] = {}
    for row in sheet.iter_rows():
        cells = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
        if len(cells) >= 2 and re.fullmatch(r"VOTOS_P\d+", cells[0]):
            found[cells[0]] = cells[1]
    return found


def _int(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars().str.replace_all(r"[^0-9-]", "").cast(pl.Int64, strict=False).fill_null(0)


@cache
def table(dataset: Dataset) -> pl.DataFrame:
    """One row per table and option: mesa, ubigeo, place names, state, registered, voted, option, label, votes.

    Options are a list code (long files), a column label (wide files), a question (referendums), or one of the
    special keys blank, null, and objected."""
    frame = _table(dataset.file)
    if "VOTOS OBTENIDOS" in frame.columns:
        return _district(frame)
    frame = frame.rename({c: c.strip().lstrip("﻿").lstrip("ï»¿") for c in frame.columns})
    if dataset.file in TRUNCATED and frame.height + 1 >= TRUNCATED[dataset.file]:
        frame = frame.filter(pl.col("MESA_DE_VOTACION") != frame["MESA_DE_VOTACION"][-1])
    mesa = "MESA_DE_VOTACION" if "MESA_DE_VOTACION" in frame.columns else "MESA"
    state = "DESCRIP_ESTADO_ACTA" if "DESCRIP_ESTADO_ACTA" in frame.columns else "ESTADO_MESA"
    registered = next(c for c in ("N_ELEC_HABIL", "NELEC_HABIL", "ELECTORES_HABILES") if c in frame.columns)
    base = [
        pl.col(mesa).str.strip_chars().str.zfill(6).alias("mesa"),
        pl.col("UBIGEO").str.strip_chars().str.zfill(6).alias("ubigeo"),
        # The 2005 referendum file names Lima's provinces outside the capital as a department of their own.
        pl.col("DEPARTAMENTO").str.strip_chars().replace("LIMA PROVINCIAS", "LIMA").alias("department"),
        pl.col("PROVINCIA").str.strip_chars().alias("province"),
        pl.col("DISTRITO").str.strip_chars().alias("district"),
        pl.col(state).str.strip_chars().str.to_uppercase().alias("state"),
        _int(registered).alias("registered"),
    ]
    if dataset.contest == "referendum":
        question = "PREGUNTA" if "PREGUNTA" in frame.columns else "PROPUESTA_DE_REGION"
        frame = frame.with_columns(
            pl.sum_horizontal(_int(c) for c in [*REFERENDUM, *REFERENDUM_SPECIAL]).alias("_voted"),
            pl.col(question).str.strip_chars().alias("_question"),
        )
        parts = [
            frame.select(
                *base,
                pl.col("_voted").alias("voted"),
                pl.col("_question").alias("question"),
                pl.lit(option).alias("option"),
                pl.lit(option).alias("label"),
                _int(column).alias("votes"),
            )
            for column, option in {**REFERENDUM, **REFERENDUM_SPECIAL}.items()
        ]
        return pl.concat(parts)
    voted = pl.col("N_CVAS").str.strip_chars().cast(pl.Int64, strict=False).fill_null(0).alias("voted")
    if "CODIGO_OP" in frame.columns:
        code = pl.col("CODIGO_OP").str.strip_chars().str.replace(r"^0+", "")
        return frame.select(
            *base,
            voted,
            pl.lit(None, pl.String).alias("question"),
            pl.when(code.is_in(list(LONG_SPECIAL)))
            .then(code.replace(LONG_SPECIAL))
            .otherwise(pl.concat_str(pl.lit("list:"), code))
            .alias("option"),
            pl.col("DESCRIPCION_OP").str.strip_chars().alias("label"),
            _int("N_TOTAL_VOTOS").alias("votes"),
        )
    labels = dictionary(dataset)
    columns = [c for c in frame.columns if re.fullmatch(r"VOTOS_P\d+", c)]
    missing = set(columns) - set(labels)
    if missing:
        raise ValueError(f"{dataset.file}: the dictionary does not name {sorted(missing)}")
    parts = [
        frame.select(
            *base,
            voted,
            pl.lit(None, pl.String).alias("question"),
            pl.lit(f"position:{column.removeprefix('VOTOS_P')}").alias("option"),
            pl.lit(labels[column].rstrip("*").strip()).alias("label"),
            _int(column).alias("votes"),
        )
        for column in columns
    ]
    parts += [
        frame.select(
            *base,
            voted,
            pl.lit(None, pl.String).alias("question"),
            pl.lit(option).alias("option"),
            pl.lit(option).alias("label"),
            _int(column).alias("votes"),
        )
        for column, option in WIDE_SPECIAL.items()
    ]
    return pl.concat(parts)


def _district(frame: pl.DataFrame) -> pl.DataFrame:
    """The district totals ONPE publishes for 2000 and 2001, in the shape of `table`, with the district as the
    table: its mesa is `distrito:` and the ubigeo."""
    columns = frame.columns
    registered = next((c for c in columns if c.startswith("ELECTORES")), None)
    frame = frame.filter(pl.col(columns[0]).str.contains(r"^\s*\d+\s*$")).with_columns(
        pl.col(columns[0]).str.strip_chars().str.zfill(6).alias("ubigeo")
    )
    base = [
        pl.concat_str(pl.lit("distrito:"), "ubigeo").alias("mesa"),
        "ubigeo",
        pl.col(columns[1]).str.strip_chars().alias("department"),
        pl.col(columns[2]).str.strip_chars().alias("province"),
        pl.col(columns[3]).str.strip_chars().alias("district"),
        pl.lit("DISTRITO").alias("state"),
        (_int(registered) if registered else pl.lit(None, pl.Int64)).alias("registered"),
        _int("VOTOS EMITIDOS").alias("voted"),
        pl.lit(None, pl.String).alias("question"),
    ]
    party = next(c for c in columns if c.startswith("AGRUPACI"))
    lists = frame.select(
        *base,
        pl.concat_str(pl.lit("list:"), pl.col(party).str.strip_chars()).alias("option"),
        pl.col(party).str.strip_chars().alias("label"),
        _int("VOTOS OBTENIDOS").alias("votes"),
    )
    districts = frame.unique("ubigeo", keep="first", maintain_order=True)
    special = [
        districts.select(*base, pl.lit(option).alias("option"), pl.lit(option).alias("label"), _int(c).alias("votes"))
        for c, option in (("VOTOS EN BLANCO", "blank"), ("VOTOS NULOS", "null"))
    ]
    return pl.concat([lists, *special])


def packages() -> list[str]:
    return ["pe.onpe/geo", *(f"pe.onpe/{d}" for d in DAYS)]


def parse() -> None:
    geo().write()
    for d in DAYS:
        election_day(d).write()


def emit() -> None:
    package.run("load", packages())


# Geography


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def places() -> pl.DataFrame:
    """Every ubigeo with its names, newest first: ubigeo, department, province, district, day."""
    frames = [
        table(d).select("ubigeo", "department", "province", "district").unique().with_columns(day=pl.lit(d.day))
        for d in DATASETS
    ]
    return pl.concat(frames).unique().sort("day", descending=True)


def geo() -> Package:
    p = Package(
        "pe.onpe/geo",
        SOURCE,
        "Peru's departments, provinces, and districts, and the countries and cities abroad where Peruvians vote, by "
        "the geographic codes of the Oficina Nacional de Procesos Electorales, with its polling-table scheme",
        depends=["pe.jne/geo"],
    )
    p.external.add(PERU)
    p.add(
        "source",
        id=ONPE,
        kind="official_body",
        publisher="Oficina Nacional de Procesos Electorales",
        title="Resultados por mesa (Plataforma Nacional de Datos Abiertos)",
        url="https://www.datosabiertos.gob.pe/",
        license="Open Data Commons Attribution License",
        license_url="https://opendatacommons.org/licenses/by/1-0/",
        attribution="Oficina Nacional de Procesos Electorales (ONPE), Plataforma Nacional de Datos Abiertos",
    )
    for code, rank, kind, noun, kinds in (
        (DEPARTMENT, 3, "administrative", "department", {"department": "department", "continent": "continent"}),
        (PROVINCE, 4, "administrative", "province", {"province": "province", "country": "country"}),
        (DISTRICT, 5, "administrative", "district", {"district": "district", "city": "city"}),
        (MESA, 6, "precinct", "polling table", None),
    ):
        p.add(
            "scheme",
            code=code,
            polity_id=PERU,
            kind=kind,
            rank=rank,
            noun=noun,
            kinds=kinds,
            code_system="onpe_ubigeo" if kinds else None,
        )
    found = places()
    levels = (
        (DEPARTMENT, 2, "department", None, ("department", "continent")),
        (PROVINCE, 4, "province", DEPARTMENT, ("province", "country")),
        (DISTRICT, 6, "district", PROVINCE, ("district", "city")),
    )
    for scheme, width, column, parent, (home, abroad) in levels:
        named: dict[str, list[str]] = {}
        for row in found.select(pl.col("ubigeo").str.slice(0, width).alias("code"), column).iter_rows():
            code, text = str(row[0]), str(row[1])
            if text not in named.setdefault(code, []):
                named[code].append(text)
        for code, texts in sorted(named.items()):
            id = area(scheme, code)
            p.add("area", id=id, scheme_code=scheme)
            p.add("area_code", area_id=id, code=code)
            p.add("area_kind", area_id=id, kind=abroad if code[:2] in ABROAD else home, source_id=ONPE)
            if parent is not None:
                p.add("area_parent", area_id=id, parent_id=area(parent, code[: width - 2]))
            folded: set[str] = set()
            for index, text in enumerate(texts):
                if _fold(text) in folded:
                    continue
                folded.add(_fold(text))
                p.name(id, jne.title(text), "es", "official", index == 0)
    return p


# Election days


class Target(NamedTuple):
    contest: str
    result_set: str
    options: dict[str, str]
    valid: int


def _contests(when: date, kind: str) -> list[jne.Contest]:
    wanted = {"president": "president", "congress": "list", "andean": "andean", "referendum": "referendum"}[kind]
    found = [
        c
        for c in jne.contests()
        if jne.day(c.process["date"]) == when and c.kind == wanted and (kind != "congress" or c.body == "congress")
    ]
    if not found:
        raise LookupError(f"Infogob has no {kind} contest on {when}")
    return found


def _options(c: jne.Contest, contest: str) -> dict[str, str]:
    return {
        _fold(str(row["TxOrgPol"])): jne.key("option", contest, str(row["IdOrgPol"]))
        for row in jne._rows(c)
        if row["IdOrgPol"] not in jne.SPECIAL
    }


def _valid(c: jne.Contest) -> int:
    return sum(jne.number(row["NuOrgVotos"]) or 0 for row in jne._rows(c) if row["IdOrgPol"] not in jne.SPECIAL)


def _target(c: jne.Contest, contest: str) -> Target:
    return Target(contest, key("result_set", contest), _options(c, contest), _valid(c))


def _constituency(when: date, contests: list[jne.Contest]) -> pl.Expr:
    """The Infogob locality of each table's congressional constituency, from its department and province."""
    labels = {c.label: c.locality for c in contests}
    # From 2011 to 2020 Infogob names Lima's constituency "LIMA + RESIDENTES EN EL EXTRANJERO".
    lima = next(labels[label] for label in labels if label.startswith("LIMA") and label != "LIMA PROVINCIAS")
    abroad = next((labels[label] for label in labels if label.startswith("RESIDENTES")), None)
    provinces = labels.get("LIMA PROVINCIAS")
    department = pl.col("department").map_elements(_fold, return_dtype=pl.String)
    expression = pl.when(pl.col("ubigeo").str.slice(0, 2).is_in(list(ABROAD))).then(
        pl.lit(abroad if abroad is not None else lima)
    )
    if provinces is not None:
        expression = expression.when((department == "LIMA") & (pl.col("ubigeo").str.slice(0, 4) != "1401")).then(
            pl.lit(provinces)
        )
    by_name = {_fold(label): locality for label, locality in labels.items()} | {"LIMA": lima}
    return expression.otherwise(department.replace_strict(by_name, default=None))


def _match(totals: dict[str, int], options: dict[str, str], where: str) -> dict[str, str]:
    """Match each ONPE list label to one of the contest's options. A list without a match and without a vote in the
    contest is one the file repeats where it was not on the ballot, and is left out."""
    found: dict[str, str] = {}
    for label, total in sorted(totals.items()):
        folded = _fold(ALIASES.get(label, label))
        if folded in options:
            found[label] = options[folded]
            continue
        close = difflib.get_close_matches(folded, list(options), n=1, cutoff=0.75)
        inside = [name for name in options if folded in name or name in folded]
        if close:
            found[label] = options[close[0]]
        elif len(inside) == 1:
            found[label] = options[inside[0]]
        elif not total:
            continue
        else:
            raise ValueError(f"{where}: no Infogob option matches {label!r} among {sorted(options)}")
    if len(set(found.values())) != len(found):
        raise ValueError(f"{where}: two lists match one option: {found}")
    return found


def unit_id(when: date, mesa: str) -> str:
    return key("unit", when, mesa)


def election_day(when: date) -> Package:
    here = [d for d in DATASETS if d.day == when]
    depends = ["pe.jne/geo", "pe.onpe/geo", f"pe.jne/{when}"]
    p = Package(
        f"pe.onpe/{when}",
        SOURCE,
        f"Peru's national elections of {when} by polling table, from the Oficina Nacional de Procesos Electorales",
        depends=depends,
    )
    p.external.add(PERU)
    for dataset in here:
        p.artifact(raw.cached(SOURCE, url(dataset.file)), ONPE)
        p.artifact(raw.cached(SOURCE, url(dataset.dictionary)), ONPE)
    units: dict[str, tuple[str, str]] = {}
    facts: list[pl.DataFrame] = []
    sets: dict[str, tuple[Target, str, Dataset]] = {}
    for dataset in here:
        frame = table(dataset)
        contests = _contests(when, dataset.contest)
        election = jne.key("election", when, jne.election_kind(contests[0]))
        for mesa, ubigeo in frame.select("mesa", "ubigeo").unique().iter_rows():
            known = units.setdefault(str(mesa), (str(ubigeo), dataset.file))
            if known[0] != ubigeo:
                raise ValueError(f"{when}: table {mesa} is in {known[0]} in {known[1]} and {ubigeo} in {dataset.file}")
        if dataset.contest == "congress" and len(contests) > 1:
            frame = frame.with_columns(_constituency(when, contests).alias("locality"))
            lost = frame.filter(pl.col("locality").is_null())
            if lost.height:
                raise ValueError(f"{dataset.file}: no constituency for {lost['department'].unique().to_list()}")
            groups = {str(locality): part for (locality,), part in frame.group_by("locality")}
            for c in contests:
                part = groups.pop(c.locality, None)
                if part is None:
                    continue
                contest = jne.key("contest", c.process["id"], c.locality)
                facts.append(_facts(p, when, dataset, part, _target(c, contest), election, sets))
            if groups:
                raise ValueError(f"{dataset.file}: tables in constituencies Infogob lacks: {sorted(groups)}")
        elif dataset.contest == "referendum":
            facts += _referendum(p, when, dataset, frame, contests, election, sets)
        else:
            (c,) = contests
            contest = jne.key("contest", c.process["id"], c.locality)
            facts.append(_facts(p, when, dataset, frame, _target(c, contest), election, sets))
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            [
                {
                    "id": unit_id(when, mesa),
                    "polity_id": PERU,
                    "scheme_code": DISTRICT if mesa.startswith("distrito:") else MESA,
                    "code": f"{when}:{mesa.removeprefix('distrito:')}",
                    "area_relation": "equals" if mesa.startswith("distrito:") else "within",
                    "area_id": area(DISTRICT, ubigeo),
                    "kind": "abroad_mission" if ubigeo[:2] in ABROAD else "standard",
                }
                for mesa, (ubigeo, _) in sorted(units.items())
            ]
        ),
    )
    values = pl.concat(facts)
    p.frame("result_value", values)
    _complete(p, when, values, sets)
    return p


def _facts(
    p: Package,
    when: date,
    dataset: Dataset,
    frame: pl.DataFrame,
    target: Target,
    election: str,
    sets: dict[str, tuple[Target, str, Dataset]],
) -> pl.DataFrame:
    where = f"{dataset.file} {target.contest}"
    lists = frame.filter(~pl.col("option").is_in(list(SPECIAL)))
    labels = lists.select("option", "label").unique()
    totals = dict(lists.group_by("label").agg(pl.col("votes").sum()).iter_rows())
    chosen = _match({str(k): int(v) for k, v in totals.items()}, target.options, where)
    option_of = {str(option): chosen[str(label)] for option, label in labels.iter_rows() if label in chosen}
    sets[target.result_set] = (target, election, dataset)
    return _values(when, frame, target.result_set, option_of)


def _values(when: date, frame: pl.DataFrame, result_set: str, option_of: dict[str, str]) -> pl.DataFrame:
    installed = ~pl.col("state").str.contains("NO INSTALADA|SIN INSTALAR")
    tables = frame.select("mesa", "registered", "voted", "state").unique("mesa", keep="first")
    accounting = pl.concat(
        [
            tables.filter(pl.col("registered").is_not_null()).select(
                "mesa", pl.lit(None, pl.String).alias("option_id"), pl.lit(2).alias("measure"), value="registered"
            ),
            tables.filter(installed).select(
                "mesa", pl.lit(None, pl.String).alias("option_id"), pl.lit(3).alias("measure"), value="voted"
            ),
        ],
        how="vertical_relaxed",
    )
    counted = frame.filter(installed)
    options = counted.filter(pl.col("option").is_in(list(option_of))).select(
        "mesa",
        pl.col("option").replace_strict(option_of, return_dtype=pl.String).alias("option_id"),
        pl.lit(1).alias("measure"),
        pl.col("votes").alias("value"),
    )
    special = counted.filter(pl.col("option").is_in(list(SPECIAL))).select(
        "mesa",
        pl.lit(None, pl.String).alias("option_id"),
        pl.col("option").replace_strict(SPECIAL, return_dtype=pl.Int64).alias("measure"),
        pl.col("votes").alias("value"),
    )
    mesas = frame["mesa"].unique().to_list()
    ids = pl.DataFrame({"mesa": mesas, "unit_id": [unit_id(when, mesa) for mesa in mesas]})
    return (
        pl.concat([accounting, options, special], how="vertical_relaxed")
        .join(ids, on="mesa")
        .select(
            pl.lit(result_set).alias("result_set_id"),
            "unit_id",
            "option_id",
            pl.lit(0).alias("channel"),
            "measure",
            pl.lit(0).alias("count_round"),
            pl.lit(0).alias("flags"),
            "value",
        )
    )


def _referendum(
    p: Package,
    when: date,
    dataset: Dataset,
    frame: pl.DataFrame,
    contests: list[jne.Contest],
    election: str,
    sets: dict[str, tuple[Target, str, Dataset]],
) -> list[pl.DataFrame]:
    found: list[pl.DataFrame] = []
    regional = any(c.locality != jne.PERU for c in contests)
    if regional:
        member: dict[str, jne.Contest] = {}
        for c in contests:
            for row in jne._rows(c):
                member[_fold(str(row["TxRegionRef"]))] = c
        frame = frame.with_columns(
            pl.col("department").map_elements(_fold, return_dtype=pl.String).alias("_department")
        )
        for c in contests:
            departments = [name for name, owner in member.items() if owner is c]
            part = frame.filter(pl.col("_department").is_in(departments))
            if not part.height:
                continue
            contest = jne.key("contest", c.process["id"], c.locality, 1)
            found.append(_answer(p, when, dataset, part, c, contest, 0, election, sets))
        unplaced = frame.filter(~pl.col("_department").is_in(list(member)))
        if unplaced.height:
            raise ValueError(f"{dataset.file}: departments outside every proposed region: {unplaced['department']}")
        return found
    (c,) = contests
    questions = [str(q) for q in frame["question"].unique(maintain_order=True).to_list()]
    rows = jne._rows(c)
    if len(questions) != len(rows):
        raise ValueError(f"{dataset.file}: {len(questions)} questions, Infogob has {len(rows)}")
    for index, question in enumerate(questions, start=1):
        contest = jne.key("contest", c.process["id"], c.locality, index)
        part = frame.filter(pl.col("question") == question)
        found.append(_answer(p, when, dataset, part, c, contest, index - 1, election, sets))
    return found


def _answer(
    p: Package,
    when: date,
    dataset: Dataset,
    frame: pl.DataFrame,
    c: jne.Contest,
    contest: str,
    row: int,
    election: str,
    sets: dict[str, tuple[Target, str, Dataset]],
) -> pl.DataFrame:
    rows = jne._rows(c)
    valid = (
        sum((jne.number(r["NuVotosSi"]) or 0) + (jne.number(r["NuVotosNo"]) or 0) for r in rows)
        if c.locality != jne.PERU
        else (jne.number(rows[row]["NuVotosSi"]) or 0) + (jne.number(rows[row]["NuVotosNo"]) or 0)
    )
    options = {"yes": jne.key("option", contest, "yes"), "no": jne.key("option", contest, "no")}
    target = Target(contest, key("result_set", contest), options, valid)
    sets[target.result_set] = (target, election, dataset)
    return _values(when, frame, target.result_set, options)


def _complete(p: Package, when: date, values: pl.DataFrame, sets: dict[str, tuple[Target, str, Dataset]]) -> None:
    valid = dict(values.filter(pl.col("measure") == 1).group_by("result_set_id").agg(pl.col("value").sum()).iter_rows())
    off: Counter[str] = Counter()
    for result_set, (target, election, dataset) in sorted(sets.items()):
        counted = int(valid.get(result_set, 0))
        completeness = round(counted / target.valid, 6) if target.valid else None
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=target.contest,
            stage="official",
            kind="counted",
            source_id=ONPE,
            status="active",
            is_default=False,
            completeness=completeness,
        )
        method = BY_DISTRICT if "por%20distrito" in dataset.file else METHOD
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=ONPE,
            locator={"dataset": PORTAL + dataset.page, "file": url(dataset.file)},
        )
        if dataset.file in TRUNCATED:
            p.add(
                "note",
                id=key("note", result_set, "truncated"),
                entity_id=result_set,
                kind="caveat",
                body_md=(
                    "ONPE's file for this election stops at 1,048,575 rows, the row limit of a spreadsheet, so it "
                    "holds only the polling tables listed before that row; the last table, cut off part way, is "
                    "left out. The other tables are missing from the published file."
                ),
                lang="en",
            )
        if counted != target.valid:
            off[result_set] = counted - target.valid
            p.add(
                "note",
                id=key("note", result_set, "total"),
                entity_id=result_set,
                kind="caveat",
                body_md=(
                    f"The polling tables add up to {counted:,} valid votes, {abs(counted - target.valid):,} "
                    f"{'more' if counted > target.valid else 'fewer'} than the {target.valid:,} of the declared "
                    f"result. {GAPS.get(when, '')}"
                ).strip(),
                lang="en",
            )
    for result_set, gap in off.items():
        target = sets[result_set][0]
        print(f"{when}: {target.contest} {gap:+,} valid votes against Infogob", flush=True)
