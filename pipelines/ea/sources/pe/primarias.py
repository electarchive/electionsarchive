"""Peru's party internal elections (elecciones internas) by polling table, from the Oficina Nacional de Procesos Electorales.

Before each general, regional, and municipal election, Peruvian parties and regional movements choose their delegates
and candidates in internal elections that ONPE organizes. ONPE publishes the count of each polling table on Peru's open
data portal, one dataset per election date and kind of ballot: the delegates of a party (one row per candidate or list
and table), a party's candidate for one municipality (one row per table and municipality, with the votes of the
candidate list), and, in 2022, a party's candidates for each region (one row per table and region, with the votes of
up to two lists). Each row carries the table, its district by ONPE's geographic code (ubigeo), the party or movement,
the registered voters of the table, and its blank, null, and objected votes.

Each package holds one election day, with one election of kind `primary` for each party or movement that voted that
day. The delegates of a party are one contest, a municipal candidate is one contest per municipality, and a regional
candidate is one contest per region. The files print no totals and no winners, so the result sets hold the table
counts only and no outcomes are stored. A table that was not installed holds its registered voters and no votes.
"""

import io
import re
import unicodedata
from datetime import date
from functools import cache
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.pe import jne, onpe

SOURCE = "pe.primarias"
FILES = onpe.FILES
PORTAL = onpe.PORTAL
PERU = jne.PERU_POLITY
NATIONAL = jne.area_of("pe.circunscripcion", "nacional")
NEW_DISTRICTS = {"070918": ("0709", "Manitea")}
"""Districts in the files that ONPE's geographic codes in `pe.onpe` do not yet hold."""


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
DELEGATES, MUNICIPAL, REGIONAL = "delegates", "municipal", "regional"
ABROAD = "abroad"
OFFICES = {
    MUNICIPAL: (key("office", MUNICIPAL), "Party candidate for mayor", "Candidato municipal del partido"),
    REGIONAL: (key("office", REGIONAL), "Party candidate for regional government", "Candidato regional del partido"),
}
LOST = ("MESA NO INSTALADA", "SIN INSTALAR")


class Dataset(NamedTuple):
    day: date
    kind: str
    file: str
    dictionary: str
    page: str


_BASE = "Resultados%20por%20mesa%20de%20las%20Elecciones%20Internas%20"
_DICT = "Diccionario%20de%20los%20resultados%20por%20mesa%20"
_PAGE = "resultados-por-mesa-de-las-elecciones-internas-"
DATASETS = [
    Dataset(
        date(2020, 11, 29),
        DELEGATES,
        f"{_BASE}29Nov%20Delegado%20Regional.csv",
        f"{_DICT}Delegado%20Regional_2.xlsx",
        f"{_PAGE}2020-del-29nov-delegado-regional-oficina",
    ),
    Dataset(
        date(2022, 5, 22),
        REGIONAL,
        f"{_BASE}%2022May%20Candidato%20Regional%20M%C3%BAltiple.csv",
        f"{_DICT}Candidato%20Regional%20M%C3%BAltiple_0.xlsx",
        f"{_PAGE}2022-del-22-de-mayo-candidato-regional-2",
    ),
    Dataset(
        date(2023, 2, 26),
        MUNICIPAL,
        f"{_BASE}26Feb%20Candidato%20Municipal.csv",
        f"{_DICT}Candidato%20Municipal_6.xlsx",
        f"{_PAGE}2023-del-26feb-candidato-municipal-oficina",
    ),
    Dataset(
        date(2023, 2, 26),
        DELEGATES,
        f"{_BASE}26Feb%20Delegado%20Regional.csv",
        f"{_DICT}Delegado%20Regional_4.xlsx",
        f"{_PAGE}2023-del-26feb-delegado-regional-oficina",
    ),
    Dataset(
        date(2023, 3, 5),
        MUNICIPAL,
        f"{_BASE}05Mar%20Candidato%20Municipal.csv",
        f"{_DICT}Candidato%20Municipal_5.xlsx",
        f"{_PAGE}2023-del-05mar-candidato-municipal-oficina",
    ),
    Dataset(
        date(2024, 1, 7),
        MUNICIPAL,
        f"{_BASE}07Ene%20Candidato%20Municipal.csv",
        f"{_DICT}Candidato%20Municipal_4.xlsx",
        f"{_PAGE}2024-del-07ene-candidato-municipal-oficina",
    ),
    Dataset(
        date(2024, 1, 14),
        MUNICIPAL,
        f"{_BASE}14Ene%20Candidato%20Municipal.csv",
        f"{_DICT}Candidato%20Municipal_3.xlsx",
        f"{_PAGE}2024-del-14ene-candidato-municipal-oficina",
    ),
    Dataset(
        date(2025, 5, 10),
        MUNICIPAL,
        f"{_BASE}2025%20del%2010May%20Candidato%20Municipal.csv",
        f"{_DICT}Candidato%20Municipal_7.xlsx",
        f"{_PAGE}2025-del-10may-candidato-municipal-oficina",
    ),
    Dataset(
        date(2025, 5, 10),
        DELEGATES,
        f"{_BASE}2025%20del%2010May%20%20Delegado%20Regional.csv",
        f"{_DICT}Delegado%20Regional_5.xlsx",
        f"{_PAGE}2025-del-10may-delegado-regional-oficina",
    ),
    Dataset(
        date(2025, 5, 18),
        MUNICIPAL,
        f"{_BASE}2025%20del%2018May%20Candidato%20Municipal.csv",
        f"{_DICT.removesuffix('mesa%20')}mesa%20del%2018May%20%20Candidato%20Municipal.xlsx",
        f"{_PAGE}2025-del-18may-candidato-municipal-oficina",
    ),
]
DAYS = sorted({d.day for d in DATASETS})


def url(name: str) -> str:
    return FILES + name


def fetch() -> None:
    for dataset in DATASETS:
        for name in (dataset.file, dataset.dictionary):
            artifact = raw.fetch(SOURCE, url(name), suffix="." + name.rsplit(".", 1)[1])
            print(f"{dataset.day} {dataset.kind} {name}: {artifact.bytes:,} bytes", flush=True)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text).split())


def clean(text: object) -> str:
    return " ".join(str(text).split())


def _text(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    return text.lstrip("﻿")


def _number(frame: pl.DataFrame, column: str, where: str) -> pl.Series:
    text = frame[column].str.strip_chars()
    numbers = text.cast(pl.Int64, strict=False)
    broken = frame.filter(text.is_not_null() & (text != "") & numbers.is_null())
    if broken.height:
        raise ValueError(f"{where}: {column} holds {broken[column].unique().to_list()[:5]}")
    return numbers


@cache
def table(dataset: Dataset) -> pl.DataFrame:
    """One row per table and option: mesa, ubigeo, names, party, scope, option, votes, and the table's accounting."""
    path = raw.cached(SOURCE, url(dataset.file)).path
    frame = pl.read_csv(
        io.StringIO(_text(path.read_bytes())),
        separator=";",
        infer_schema=False,
        quote_char=None,
        truncate_ragged_lines=True,
    ).rename(lambda name: name.strip().upper().replace(" / ", "_").replace(" ", "_"))
    where = dataset.file
    code = next(c for c in frame.columns if c.startswith("CODIGO_AGRUPACION"))
    base = [
        pl.col("UBIGEO").str.strip_chars().str.zfill(6).alias("ubigeo"),
        pl.col("DEPARTAMENTO").map_elements(clean, return_dtype=pl.String).alias("department"),
        pl.col("PROVINCIA").map_elements(clean, return_dtype=pl.String).alias("province"),
        pl.col("DISTRITO").map_elements(clean, return_dtype=pl.String).alias("district"),
        pl.col("MESA").str.strip_chars().alias("mesa"),
        pl.col("ESTADO_MESA").str.strip_chars().alias("state"),
        pl.col("TIPO_AGRUPACION").map_elements(clean, return_dtype=pl.String).alias("type"),
        pl.col(code).str.strip_chars().alias("code"),
        pl.col("AGRUPACION_POLITICA").map_elements(clean, return_dtype=pl.String).alias("party"),
        pl.Series("registered", _number(frame, "ELECTORES_HABILES", where)),
        pl.Series("blank", _number(frame, "VOTOS_BLANCOS", where)),
        pl.Series("null", _number(frame, "VOTOS_NULOS", where)),
        pl.Series("objected", _number(frame, "VOTOS_IMPUG", where)),
        pl.col("UBICACION_EN_CEDULA").cast(pl.Int64).alias("position"),
    ]
    if dataset.kind == DELEGATES:
        frame = frame.with_columns(
            pl.Series("votes", _number(frame, "VOTOS_OBTENIDOS", where)),
            pl.col("CANDIDATO_LISTA").map_elements(clean, return_dtype=pl.String).alias("option"),
        ).with_columns(
            *base,
            pl.when(pl.col("UBIGEO").str.strip_chars().str.zfill(6).str.slice(0, 2).is_in(list(onpe.ABROAD)))
            .then(pl.lit(ABROAD))
            .otherwise(pl.lit(None, pl.String))
            .alias("scope"),
        )
        return frame.select(_COLUMNS)
    scope = "MUNICIPAL" if dataset.kind == MUNICIPAL else "REGIONAL"
    lists = [c for c in frame.columns if re.fullmatch(r"LISTA_\d+", c)]
    frame = frame.with_columns(*base, pl.col(scope).map_elements(clean, return_dtype=pl.String).alias("scope"))
    parts = [
        frame.with_columns(
            pl.lit(f"Lista {c.removeprefix('LISTA_')}").alias("option"), pl.Series("votes", _number(frame, c, where))
        ).select(_COLUMNS)
        for c in lists
    ]
    return pl.concat(parts).filter(pl.col("votes").is_not_null())


_COLUMNS = [
    "ubigeo",
    "department",
    "province",
    "district",
    "mesa",
    "state",
    "position",
    "type",
    "code",
    "party",
    "scope",
    "option",
    "votes",
    "registered",
    "blank",
    "null",
    "objected",
]


def tables(day: date) -> pl.DataFrame:
    return pl.concat(
        [
            table(d).with_columns(pl.lit(d.kind).alias("kind"), pl.lit(d.file).alias("file"))
            for d in DATASETS
            if d.day == day
        ]
    )


def everything() -> pl.DataFrame:
    return pl.concat([tables(day).with_columns(pl.lit(day).alias("day")) for day in DAYS])


def party_key(name: str) -> str:
    return key("party", fold(name))


def person_key(party: str, name: str) -> str:
    return key("person", fold(party), fold(name))


@cache
def places() -> tuple[dict[tuple[str, str, str], str], dict[str, str]]:
    """The ubigeo of each district by names, and the department code of each department name."""
    found = everything().select("ubigeo", "department", "province", "district").unique()
    districts: dict[tuple[str, str, str], str] = {}
    departments: dict[str, str] = {}
    for ubigeo, department, province, district in found.iter_rows():
        districts[(fold(department), fold(province), fold(district))] = ubigeo
        if ubigeo[:2] not in onpe.ABROAD:
            departments[fold(department)] = ubigeo[:2]
    return districts, departments


def scope_area(kind: str, scope: str) -> str:
    districts, departments = places()
    if kind == MUNICIPAL:
        parts = tuple(fold(part) for part in scope.split(" - "))
        if len(parts) != 3 or parts not in districts:
            raise ValueError(f"no district for the municipality {scope!r}")
        return onpe.area(onpe.DISTRICT, districts[parts])
    return onpe.area(onpe.DEPARTMENT, departments[fold(scope)])


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{d}" for d in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for d in DAYS:
        election_day(d).write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Oficina Nacional de Procesos Electorales",
        title="Resultados por mesa de las elecciones internas (Plataforma Nacional de Datos Abiertos)",
        url="https://www.datosabiertos.gob.pe/",
        license="Open Data Commons Attribution License",
        license_url="https://opendatacommons.org/licenses/by/1-0/",
        attribution="Oficina Nacional de Procesos Electorales (ONPE), Plataforma Nacional de Datos Abiertos",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Districts in ONPE's internal election files that its geographic codes of the general elections lack",
        depends=["pe.onpe/geo"],
    )
    p.external.add(PERU)
    _source(p)
    for code, (province, name) in sorted(NEW_DISTRICTS.items()):
        id = onpe.area(onpe.DISTRICT, code)
        p.add("area", id=id, scheme_code=onpe.DISTRICT)
        p.add("area_code", area_id=id, code=code)
        p.add("area_kind", area_id=id, kind="district", source_id=SOURCE_ID)
        p.add("area_parent", area_id=id, parent_id=onpe.area(onpe.PROVINCE, province))
        p.name(id, name, "es", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties, regional movements, and candidates in ONPE's internal election files, and the party offices "
        "they chose",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(PERU)
    for id, english, spanish in OFFICES.values():
        p.add("office", id=id, polity_id=PERU, kind="other")
        p.name(id, english, "en", "official")
        p.name(id, spanish, "es", "native", False)
    frame = everything()
    names: dict[str, dict[str, int]] = {}
    for name, days in frame.group_by("party").agg(pl.col("day").n_unique()).iter_rows():
        names.setdefault(party_key(str(name)), {})[str(name)] = int(days)
    for id, spellings in sorted(names.items()):
        p.add("party", id=id, kind="party", polity_id=PERU)
        ordered = sorted(spellings, key=lambda text: (-spellings[text], text))
        for index, text in enumerate(ordered):
            p.name(id, jne.title(text), "es", "official", index == 0)
    candidates = (
        frame.filter(~pl.col("option").str.to_uppercase().str.starts_with("LISTA"))
        .select("party", "option", "day")
        .unique()
        .sort("day", descending=True)
    )
    seen: set[str] = set()
    for party, option, _ in candidates.iter_rows():
        id = person_key(str(party), str(option))
        if id in seen:
            continue
        seen.add(id)
        p.add("person", id=id)
        p.name(id, jne.title(str(option)), "es", "official")
    return p


METHOD = (
    "Counts by polling table from the open-data file ONPE publishes for the party's internal election. The file "
    "prints no totals and no winners, so no outcomes are stored. A table that was not installed holds its registered "
    "voters and no votes."
)


def election_day(when: date) -> Package:
    frame = tables(when)
    p = Package(
        f"{SOURCE}/{when}",
        SOURCE,
        f"Peru's party internal elections of {when} by polling table, from the Oficina Nacional de Procesos Electorales",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", "pe.onpe/geo", "pe.jne/geo"],
    )
    p.external.add(PERU)
    for dataset in (d for d in DATASETS if d.day == when):
        p.artifact(raw.cached(SOURCE, url(dataset.file)), SOURCE_ID)
        p.artifact(raw.cached(SOURCE, url(dataset.dictionary)), SOURCE_ID)
    # One unit per table: the ubigeo most of its rows give, a district abroad only when nothing else does.
    votes = frame.group_by("mesa", "ubigeo").len().sort(["mesa", "len", "ubigeo"], descending=[False, True, False])
    home = (
        votes.with_columns(pl.col("ubigeo").str.slice(0, 2).is_in(list(onpe.ABROAD)).alias("abroad"))
        .sort(["mesa", "abroad", "len", "ubigeo"], descending=[False, False, True, False])
        .unique("mesa", keep="first", maintain_order=True)
    )
    units = dict(home.select("mesa", "ubigeo").iter_rows())
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            [
                {
                    "id": key("unit", when, mesa),
                    "polity_id": PERU,
                    "scheme_code": onpe.MESA,
                    "code": f"{when}:{mesa}",
                    "area_relation": "within",
                    "area_id": onpe.area(onpe.DISTRICT, ubigeo),
                    "kind": "abroad_mission" if ubigeo[:2] in onpe.ABROAD else "standard",
                }
                for mesa, ubigeo in sorted(units.items())
            ]
        ),
    )
    pieces: list[pl.DataFrame] = []
    for party, rows in sorted(frame.group_by("party"), key=lambda item: str(item[0][0])):
        name = str(party[0])
        pieces += _election(p, when, name, rows)
    p.frame("result_value", pl.concat(pieces))
    return p


def _election(p: Package, when: date, name: str, rows: pl.DataFrame) -> list[pl.DataFrame]:
    party = party_key(name)
    election = key("election", when, party)
    p.add(
        "election",
        id=election,
        polity_id=PERU,
        kind="primary",
        date_from=when,
        date_to=when,
        status="held",
    )
    shown = jne.title(name)
    p.name(election, f"{shown} internal election, {when:%B} {when.day}, {when.year}", "en", "official")
    path = f"pe/{when}-primary-{changeset.slug(name)}"
    p.add("slug", path=path, entity_id=election, is_current=True)
    pieces: list[pl.DataFrame] = []
    sections = sorted(
        {(str(kind), "" if scope is None else str(scope)) for kind, scope in rows.select("kind", "scope").iter_rows()}
    )
    for kind, scope in sections:
        part = rows.filter((pl.col("kind") == kind) & (pl.col("scope").fill_null("") == scope))
        pieces.append(_contest(p, when, name, party, election, path, kind, scope, part))
    return pieces


def _contest(
    p: Package, when: date, name: str, party: str, election: str, path: str, kind: str, scope: str, rows: pl.DataFrame
) -> pl.DataFrame:
    contest = key("contest", when, party, kind, scope)
    if kind == DELEGATES:
        departments = {str(u)[:2] for u in rows["ubigeo"].unique()}
        district = NATIONAL if len(departments) != 1 else onpe.area(onpe.DEPARTMENT, next(iter(departments)))
        abroad = scope == ABROAD
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="delegate_allocation",
            district_area_id=district,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"{jne.title(name)} delegates" + (" abroad" if abroad else ""), "en", "official")
        slug = f"{path}/delegates" + ("-abroad" if abroad else "")
    else:
        office = OFFICES[kind][0]
        place = scope_area(kind, scope)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=office,
            district_area_id=place,
            magnitude=1,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"{jne.title(name)} candidate, {jne.title(scope)}", "en", "official")
        slug = f"{path}/{kind}-{changeset.slug(scope)}"
    p.add("slug", path=slug, entity_id=contest, is_current=True)
    result_set = key("result_set", contest)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options: dict[str, str] = {}
    first = rows.group_by("option").agg(pl.col("position").min()).sort("position", "option")
    for label, position in first.iter_rows():
        label = str(label)
        id = options[label] = key("option", contest, label)
        listed = label.upper().startswith("LISTA")
        p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind="list" if listed else "candidate",
            party_id=party,
            ballot_position=int(position) if kind == DELEGATES and position is not None else None,
            ballot_label=label,
        )
        if not listed:
            person = person_key(name, label)
            p.add(
                "candidacy",
                id=key("candidacy", contest, person),
                person_id=person,
                option_id=id,
                role="list_member",
                ballot_name=label,
                member_of_party_id=party,
            )
    duplicate = rows.group_by("mesa", "option").len().filter(pl.col("len") > 1)
    if duplicate.height:
        raise ValueError(f"{when} {name} {scope}: {duplicate.height} tables repeat an option")
    return _values(when, rows, result_set, options)


def _values(when: date, rows: pl.DataFrame, result_set: str, options: dict[str, str]) -> pl.DataFrame:
    installed = ~pl.col("state").is_in(LOST)
    tables = rows.group_by("mesa").agg(
        pl.col("state").first(),
        pl.col("registered").first(),
        pl.col("blank").first(),
        pl.col("null").first(),
        pl.col("objected").first(),
        pl.col("registered").n_unique().alias("r"),
        pl.col("blank").n_unique().alias("b"),
        pl.col("null").n_unique().alias("n"),
        pl.col("objected").n_unique().alias("o"),
    )
    uneven = tables.filter((pl.col("r") > 1) | (pl.col("b") > 1) | (pl.col("n") > 1) | (pl.col("o") > 1))
    if uneven.height:
        raise ValueError(
            f"{when} {result_set}: tables give two counts for the same figure: {uneven['mesa'].to_list()[:5]}"
        )
    parts = [
        tables.select(
            "mesa", pl.lit(None, pl.String).alias("option_id"), pl.lit(2).alias("measure"), value=pl.col("registered")
        )
    ]
    live = tables.filter(installed.fill_null(True))
    for measure, column in ((6, "blank"), (5, "null"), (15, "objected")):
        parts.append(
            live.select(
                "mesa",
                pl.lit(None, pl.String).alias("option_id"),
                pl.lit(measure).alias("measure"),
                value=pl.col(column),
            )
        )
    counted = rows.filter(installed)
    parts.append(
        counted.select(
            "mesa",
            pl.col("option").replace_strict(options, return_dtype=pl.String).alias("option_id"),
            pl.lit(1).alias("measure"),
            pl.col("votes").alias("value"),
        )
    )
    values = pl.concat(parts, how="vertical_relaxed").filter(pl.col("value").is_not_null())
    return values.select(
        pl.lit(result_set).alias("result_set_id"),
        pl.col("mesa").map_elements(lambda mesa: key("unit", when, mesa), return_dtype=pl.String).alias("unit_id"),
        "option_id",
        pl.lit(0).alias("channel"),
        "measure",
        pl.lit(0).alias("count_round"),
        pl.lit(0).alias("flags"),
        "value",
    )
