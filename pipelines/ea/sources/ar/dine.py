import csv
import difflib
import functools
import io
import json
import re
import time
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote
from uuid import uuid5

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import FACTS, TABLES, Package
from ea.store import DATA

SOURCE = "ar.dine"
PARSED = DATA / "parsed" / SOURCE
FILES = "https://www.argentina.gob.ar/sites/default/files"
WAYBACK = "https://web.archive.org/web/{}id_/{}"
API = "https://resultados.mininterior.gob.ar/api"
ARGENTINA = str(uuid5(package.NAMESPACE, "polity\x1fAR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fARG"))
NATIONAL = "0"
POSITIVE = "POSITIVO"
COMMAND = "COMANDO"
KINDS = ("EN BLANCO", "NULO", "RECURRIDO", "IMPUGNADO", COMMAND)
SPELLINGS = {"BLANCOS": "EN BLANCO", "NULOS": "NULO", "RECURRIDOS": "RECURRIDO", "IMPUGNADOS": "IMPUGNADO"}


class Held(NamedTuple):
    key: str
    day: date
    kind: str
    election: int
    url: str


HELD = [
    Held("2011-paso", date(2011, 8, 14), "primary", 1, f"{FILES}/2011-provisorios_paso.zip"),
    Held("2011-generales", date(2011, 10, 23), "general", 2, f"{FILES}/2011-provisorios_generales.zip"),
    Held("2013-paso", date(2013, 8, 11), "primary", 1, f"{FILES}/2013-provisorios_paso.zip"),
    Held("2013-generales", date(2013, 10, 27), "general", 2, f"{FILES}/2013-provisorios_generales.zip"),
    Held(
        "2015-paso",
        date(2015, 8, 9),
        "primary",
        1,
        WAYBACK.format("20240106041857", f"{FILES}/dine-resultados/2015-PROVISORIOS_PASO.zip"),
    ),
    Held("2015-generales", date(2015, 10, 25), "general", 2, f"{FILES}/2015-provisorios_generales.zip"),
    Held("2015-ballotage", date(2015, 11, 22), "runoff", 3, f"{FILES}/2015-provisorios_ballotage.zip"),
    Held("2017-paso", date(2017, 8, 13), "primary", 1, f"{FILES}/2017-provisorios_paso.zip"),
    Held("2017-generales", date(2017, 10, 22), "general", 2, f"{FILES}/2017-provisorios_generales.zip"),
    Held("2019-paso", date(2019, 8, 11), "primary", 1, f"{FILES}/2019-provisorios_paso.zip"),
    Held("2019-generales", date(2019, 10, 27), "general", 2, f"{FILES}/2019-provisorios_generales.zip"),
    Held("2021-paso", date(2021, 9, 12), "primary", 1, f"{FILES}/2021-provisorios_paso.zip"),
    Held("2021-generales", date(2021, 11, 14), "general", 2, f"{FILES}/2021-provisorios_generales.zip"),
    Held(
        "2023-paso",
        date(2023, 8, 13),
        "primary",
        1,
        WAYBACK.format("20240106010034", f"{FILES}/dine-resultados/2023-PROVISORIOS_PASO.zip"),
    ),
    Held("2023-generales", date(2023, 10, 22), "general", 2, f"{FILES}/2023_generales_1.zip"),
    Held("2023-segunda-vuelta", date(2023, 11, 19), "runoff", 3, f"{FILES}/2023_segundavuelta.zip"),
    Held(
        "2025-generales",
        date(2025, 10, 26),
        "general",
        2,
        "https://datos.mininterior.gob.ar/dataset/947e871a-650e-4b63-8939-ecb29acb717c/resource/"
        "a24110fb-bfcf-47a6-8aa7-2e53dab9caf5/download/elecciones_legislativas_2025.zip",
    ),
]
BY_KEY = {held.key: held for held in HELD}
SHEETS = {
    1983: (
        "resultados_1983_-_presidente_y_vicepresidente_0",
        "resultados_1983_diputados_nacionales_0",
        "resultados_1983_senadores_nacionales_0",
    ),
    1985: ("resultados_1985_diputados_nacionales",),
    1987: ("resultados_1987_diputados_nacionales",),
    1989: (
        "resultados_1989_presidente_y_vicepresidente",
        "resultados_1989_diputados_nacionales",
        "resultados_1989_senadores_nacionales",
    ),
    1991: ("resultados_1991_diputados_nacionales",),
    1992: ("resultados_1992_senadores_nacionales",),
    1993: ("resultados_1993_diputados_nacionales",),
    1995: (
        "resultados_1995_presidente_y_vicepresidente",
        "resultados_1995_diputados_nacionales",
        "resultados_1995_senadores_nacionales",
    ),
    1997: ("resultados_1997_diputados_nacionales",),
    1999: ("resultados_1999_presidente_y_vicepresidente", "resultados_1999_diputados_nacionales"),
    2001: ("resultados_2001_diputados_nacionales", "resultados_2001_senadores_nacionales"),
    2003: (
        "resultados_2003_presidente_y_vicepresidente",
        "resultados_2003_diputados_nacionales",
        "resultados_2003_senadores_nacionales",
    ),
    2005: ("resultados_2005_diputados_nacionales", "resultados_2005_senadores_nacionales"),
    2007: (
        "resultados_2007_presidente_y_vicepresidente",
        "resultados_2007_diputados_nacionales",
        "resultados_2007_senadores_nacionales",
    ),
    2009: ("resultados_2009_diputados_nacionales", "resultados_2009_senadores_nacionales_0"),
}


def sheet_url(name: str) -> str:
    return f"{FILES}/{name}.xlsx"


FIRST_ROUND = {"2015-ballotage": "2015-generales", "2023-segunda-vuelta": "2023-generales"}


class Category(NamedTuple):
    cargo: str
    national: bool
    en: str
    es: str


CATEGORIES = {
    "president": Category("1", True, "President and Vice President", "Presidente y Vicepresidente"),
    "senate": Category("2", False, "National Senate", "Senadores nacionales"),
    "deputies": Category("3", False, "Chamber of Deputies", "Diputados nacionales"),
    "mercosur": Category(
        "8", True, "Mercosur Parliament, national district", "Parlamento del Mercosur, distrito nacional"
    ),
    "mercosur-regional": Category("9", False, "Mercosur Parliament", "Parlamento del Mercosur, distrito regional"),
}

# Deputies each district elects at a half renewal of the 257-member Chamber (Law 22.847): the first figure in the
# cycle of 2013, 2017, 2021, and 2025 (127 seats), the second in that of 2011, 2015, 2019, and 2023 (130 seats).
DEPUTIES = {
    "1": (13, 12),
    "2": (35, 35),
    "3": (3, 2),
    "4": (9, 9),
    "5": (3, 4),
    "6": (4, 3),
    "7": (2, 3),
    "8": (5, 4),
    "9": (2, 3),
    "10": (3, 3),
    "11": (3, 2),
    "12": (2, 3),
    "13": (5, 5),
    "14": (3, 4),
    "15": (3, 2),
    "16": (2, 3),
    "17": (3, 4),
    "18": (3, 3),
    "19": (3, 2),
    "20": (3, 2),
    "21": (9, 10),
    "22": (3, 4),
    "23": (4, 5),
    "24": (2, 3),
}
MERCOSUR_NATIONAL_SEATS = 19
# Where the results service's registered-voter totals count a different set of tables than its own polling-table
# file: every vote and ballot total matches, only the roll differs.
ROLL_GAPS = {
    "2019-paso": "the results service also counts the rolls of six tables in Buenos Aires (2,093 voters) whose "
    "certificates the polling-table file lists with no ballots, which are left out here",
    "2023-segunda-vuelta": "the results service leaves out the rolls of the tables whose certificates report no "
    "ballot of any kind (103,772 tables scrutinized, against 104,520 in the polling-table file), while they are "
    "kept here with zero ballots",
}
DISTRICTS = {
    "1": "Ciudad Autónoma de Buenos Aires",
    "2": "Buenos Aires",
    "3": "Catamarca",
    "4": "Córdoba",
    "5": "Corrientes",
    "6": "Chaco",
    "7": "Chubut",
    "8": "Entre Ríos",
    "9": "Formosa",
    "10": "Jujuy",
    "11": "La Pampa",
    "12": "La Rioja",
    "13": "Mendoza",
    "14": "Misiones",
    "15": "Neuquén",
    "16": "Río Negro",
    "17": "Salta",
    "18": "San Juan",
    "19": "San Luis",
    "20": "Santa Cruz",
    "21": "Santa Fe",
    "22": "Santiago del Estero",
    "23": "Tucumán",
    "24": "Tierra del Fuego, Antártida e Islas del Atlántico Sur",
}

METHOD = (
    "The provisional count (escrutinio provisorio) of the Dirección Nacional Electoral, by polling table (mesa), "
    "as published in its open data. Each table's figures are those transmitted from its count certificate "
    "(telegrama) on election night; the binding result is the definitive count of the national electoral courts, "
    "which can differ slightly. Registered voters are the voters on the table's roll. Invalid ballots are the null "
    "ballots plus the ballots objected to at the table (votos recurridos) and those cast by voters whose identity "
    "was challenged (votos de identidad impugnada), whose validity the courts settle in the definitive count; the "
    "objected measure holds those two kinds. Votes of the Comando Electoral (the security personnel deployed for the "
    "election, who vote at the table where they serve) are counted on the certificate without a list, and are "
    "stored as their own option. Valid ballots are the votes for lists plus the Comando Electoral votes, and ballots "
    "cast are valid, blank, and invalid ballots together."
)
SECTION_METHOD = (
    "The provisional count (escrutinio provisorio) of the Dirección Nacional Electoral, by section (department, "
    "partido, or comuna), as its results service (resultados.mininterior.gob.ar) publishes it. The Dirección's "
    "polling-table file for this election is no longer served. The figures are the sums of the count certificates "
    "(telegramas) transmitted on election night; the binding result is the definitive count of the national "
    "electoral courts, which can differ slightly. Registered voters are the voters on the rolls of the tables "
    "counted. Invalid ballots are the null ballots plus the ballots objected to at the table (votos recurridos) and "
    "those cast by voters whose identity was challenged (votos de identidad impugnada); the objected measure holds "
    "those two kinds. Votes of the Comando Electoral are stored as their own option. Valid ballots are the votes for "
    "lists plus the Comando Electoral votes, and ballots cast are valid, blank, and invalid ballots together."
)
PRIMARY_METHOD = (
    " In the primaries (PASO), a front that ran several internal lists in a category is one option whose lists are "
    "its child options, and each list's votes are stored on the list. A front needed 1.5% of the valid votes of the "
    "category in the district to stand in the general election."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def normal(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(plain.upper().split())


def party(name: str) -> str:
    return key("party", normal(name))


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


DINE_SOURCE = key("source", SOURCE)
API_SOURCE = key("source", "ar.dine.api")
DEPUTIES_BODY = key("body", "deputies")
SENATE_BODY = key("body", "senate")
MERCOSUR_BODY = key("body", "mercosur")
PRESIDENCY = key("office", "president")
DEPUTIES_SYSTEM = key("electoral_system", "deputies", 1983)
MERCOSUR_SYSTEM = key("electoral_system", "mercosur", 2015)
SYSTEMS = [
    (
        DEPUTIES_SYSTEM,
        date(1983, 1, 1),
        {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}},
        (
            "Each district elects half its deputies every two years from closed lists by D'Hondt. Only lists with at "
            "least 3% of the district's registered voters take part in the allocation (Law 19.945, art. 160), a "
            "threshold of registered voters that these rules do not encode."
        ),
    ),
    (
        MERCOSUR_SYSTEM,
        date(2015, 1, 1),
        {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}},
        (
            "Argentina elects 19 members of the Mercosur Parliament from closed national lists by D'Hondt, and one per "
            "district by plurality (Law 27.120)."
        ),
    ),
]


def totals_url(year: int, election: int, cargo: str, district: str) -> str:
    return (
        f"{API}/resultado/totalizado?{quote('año')}={year}&recuento=Provisorio&idEleccion={election}"
        f"&idCargo={cargo}&idDistrito={district}"
    )


def menu_url(year: int) -> str:
    return f"{API}/menu?{quote('año')}={year}"


def _get(url: str, suffix: str, attempts: int = 4) -> raw.Artifact | None:
    for attempt in range(attempts):
        try:
            return raw.fetch(SOURCE, url, suffix=suffix)
        except httpx.HTTPStatusError as failure:
            if failure.response.status_code in (400, 404, 408, 409):
                return None
            print(f"{url}: {failure}, retrying", flush=True)
        except httpx.TransportError as failure:
            print(f"{url}: {failure}", flush=True)
        if attempt + 1 < attempts:
            time.sleep(10 * 2**attempt)
    print(f"{url} could not be fetched", flush=True)
    return None


def fetch() -> None:
    for held in HELD:
        artifact = _get(held.url, ".zip", 1 if held.url.startswith(WAYBACK[:28]) else 4)
        print(f"{held.key}: {'blocked' if artifact is None else f'{artifact.bytes:,} bytes'}", flush=True)
    for names in SHEETS.values():
        for name in names:
            _get(sheet_url(name), ".xlsx")
    for year in sorted({held.day.year for held in HELD}):
        _get(menu_url(year), ".json")
    wanted = [url for _, url in _totals_wanted()]
    cached = raw.keys(SOURCE)
    wanted += [url for held in HELD if held.url not in cached for url in _section_urls(held)]
    with ThreadPoolExecutor(6) as pool:
        got = list(pool.map(lambda url: _get(url, ".json"), wanted))
    print(f"totals: {sum(a is not None for a in got):,} of {len(wanted):,}", flush=True)


def menu(year: int) -> list[dict[str, object]]:
    return json.loads(raw.cached(SOURCE, menu_url(year)).path.read_bytes())


def _categories(held: Held) -> dict[str, list[str]]:
    for entry in menu(held.day.year):
        if entry["IdEleccion"] != held.election:
            continue
        cargos = entry["Cargos"]
        assert isinstance(cargos, list)
        return {
            code: [str(d["IdDistrito"]) for d in cargo["Distritos"]]
            for cargo in cargos
            for code, category in CATEGORIES.items()
            if cargo["IdCargo"] == category.cargo
        }
    raise LookupError(f"{held.key} is not in the results menu")


def _sections(held: Held) -> dict[str, dict[str, list[tuple[str, str]]]]:
    """The sections the results menu lists for each national category and district of an election."""
    for entry in menu(held.day.year):
        if entry["IdEleccion"] != held.election:
            continue
        cargos = entry["Cargos"]
        assert isinstance(cargos, list)
        found: dict[str, dict[str, list[tuple[str, str]]]] = {}
        for cargo in cargos:
            for code, category in CATEGORIES.items():
                if cargo["IdCargo"] != category.cargo:
                    continue
                for district in cargo["Distritos"]:
                    if str(district["IdDistrito"]) == NATIONAL:
                        continue
                    found.setdefault(code, {})[str(district["IdDistrito"])] = [
                        (str(section["IdSeccion"]), clean(section["Seccion"]))
                        for group in district.get("SeccionesProvinciales") or []
                        for section in group.get("Secciones") or []
                    ]
        return found
    raise LookupError(f"{held.key} is not in the results menu")


def _section_urls(held: Held) -> list[str]:
    return [
        f"{totals_url(held.day.year, held.election, CATEGORIES[code].cargo, district)}&idSeccion={section}"
        for code, districts in _sections(held).items()
        for district, sections in districts.items()
        for section, _ in sections
    ]


def _totals_wanted() -> Iterator[tuple[Held, str]]:
    for held in HELD:
        try:
            categories = _categories(held)
        except LookupError:
            continue
        for code, districts in categories.items():
            for district in districts:
                yield held, totals_url(held.day.year, held.election, CATEGORIES[code].cargo, district)


# The provisional-count files are one long CSV per election with every field quoted, and some names hold unescaped
# quotes (`"LISTA OPCION AL CAMBIO "A""`). A line the csv module splits into the wrong number of fields is split
# again by a pattern that ends a quoted field only at a quote followed by a comma or the end of the line.

FIELD = re.compile(r'"((?:[^"]|"(?!,|$))*)"(?=,|$)|([^,"]*)(?=,|$)')
COLUMNS = (
    "distrito_id",
    "distrito_nombre",
    "seccion_id",
    "seccion_nombre",
    "circuito_id",
    "mesa_id",
    "mesa_tipo",
    "mesa_electores",
    "cargo_id",
    "cargo_nombre",
    "agrupacion_id",
    "agrupacion_nombre",
    "lista_numero",
    "lista_nombre",
    "votos_tipo",
    "votos_cantidad",
)
OPTIONAL = {"lista_numero", "lista_nombre", "mesa_tipo"}


def _split(line: str) -> list[str]:
    fields: list[str] = []
    position = 0
    while True:
        match = FIELD.match(line, position)
        if match is None:
            raise ValueError(f"unreadable line: {line!r}")
        fields.append(match.group(1) if match.group(1) is not None else match.group(2))
        position = match.end()
        if position >= len(line):
            return fields
        position += 1


def _lines(held: Held) -> Iterator[str]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, held.url).path)
    (member,) = [
        name
        for name in archive.namelist()
        if not name.startswith("__MACOSX") and re.match(r"result.*\.csv$", name.rsplit("/", 1)[-1], re.IGNORECASE)
    ]
    with archive.open(member) as handle:
        try:
            handle.read(64 << 20).decode("utf-8")
            encoding = "utf-8-sig"
        except UnicodeDecodeError:
            encoding = "cp1252"
    with archive.open(member) as handle:
        yield from io.TextIOWrapper(handle, encoding=encoding, newline="")


def _category(name: str) -> str | None:
    text = normal(name)
    if "PRESIDENTE" in text:
        return "president"
    if "MERCOSUR" in text:
        return "mercosur" if "NACIONAL" in text else "mercosur-regional"
    if "NACIONAL" in text and "SENADOR" in text:
        return "senate"
    if "NACIONAL" in text and "DIPUTADO" in text:
        return "deputies"
    return None


CHUNK = 2_000_000


def _chunk(kept: dict[str, list[str]]) -> pl.DataFrame:
    """One slice of staged rows, typed, so a multi-gigabyte file never sits in memory as Python strings."""
    return (
        pl.DataFrame(kept)
        .with_columns(pl.col(column).str.strip_chars() for column in COLUMNS)
        .with_columns(
            pl.col("distrito_id", "seccion_id").str.strip_chars_start("0").replace("", "0"),
            pl.col("agrupacion_id", "lista_numero").str.strip_chars_start("0"),
            pl.col("mesa_electores", "votos_cantidad").replace("", None).cast(pl.Int64),
        )
    )


def stage(name: str) -> Path:
    held = BY_KEY[name]
    lines = _lines(held)
    header = [column.strip().lower() for column in _split(next(lines).rstrip("\r\n").lstrip("﻿"))]
    index = {column: header.index(column) for column in COLUMNS if column in header}
    missing = set(COLUMNS) - set(index) - OPTIONAL
    if missing:
        raise ValueError(f"{held.key} lacks columns {sorted(missing)}: {header}")
    kept: dict[str, list[str]] = {column: [] for column in (*COLUMNS, "category")}
    parts: list[pl.DataFrame] = []
    seen: Counter[tuple[str, str, str | None]] = Counter()
    for number, line in enumerate(lines, 2):
        line = line.rstrip("\r\n")
        if not line:
            continue
        row = next(csv.reader([line]))
        if len(row) != len(header):
            row = _split(line)
        if len(row) != len(header):
            raise ValueError(f"{held.key} line {number} has {len(row)} fields, not {len(header)}: {line!r}")
        cargo_name = row[index["cargo_nombre"]]
        category = _category(cargo_name)
        seen[(row[index["cargo_id"]].strip(), clean(cargo_name), category)] += 1
        if category is None:
            continue
        for column in COLUMNS:
            kept[column].append(row[index[column]] if column in index else "")
        kept["category"].append(category)
        if len(kept["category"]) >= CHUNK:
            parts.append(_chunk(kept))
            kept = {column: [] for column in kept}
    parts.append(_chunk(kept))
    for (cargo, cargo_name, category), n in sorted(seen.items()):
        print(f"{held.key}: cargo {cargo} {cargo_name}: {n:,} rows, {category or 'left out'}", flush=True)
    frame = pl.concat(parts)
    PARSED.mkdir(parents=True, exist_ok=True)
    out = PARSED / f"{held.key}.parquet"
    frame.write_parquet(out)
    print(f"{held.key}: {frame.height:,} national rows staged", flush=True)
    return out


def stage_sections(name: str) -> Path:
    """Stage an election whose polling-table file is no longer served from the results API's section totals."""
    held = BY_KEY[name]
    kept: dict[str, list[object]] = {column: [] for column in (*COLUMNS, "category")}

    def add(**row: object) -> None:
        for column, values in kept.items():
            values.append(row.get(column, ""))

    blocked: list[str] = []
    cached = raw.keys(SOURCE)
    for code, districts in _sections(held).items():
        category = CATEGORIES[code]
        for district, sections in districts.items():
            urls = [
                (
                    section,
                    name,
                    f"{totals_url(held.day.year, held.election, category.cargo, district)}&idSeccion={section}",
                )
                for section, name in sections
            ]
            loaded = [
                (section, name, json.loads(raw.cached(SOURCE, url).path.read_bytes()) if url in cached else None)
                for section, name, url in urls
            ]
            if not loaded or any(not isinstance(data, dict) or "agrupaciones" not in data for _, _, data in loaded):
                blocked += [url for _, _, url in urls if url not in cached]
                print(f"{held.key}: {code} in district {district} is not served by section, left out", flush=True)
                continue
            for section, section_name, data in loaded:
                assert isinstance(data, dict)
                base = {
                    "distrito_id": district,
                    "distrito_nombre": clean(str(data.get("Distrito") or "")),
                    "seccion_id": section,
                    "seccion_nombre": section_name,
                    "mesa_electores": _int(data.get("Electores")),
                    "cargo_id": category.cargo,
                    "cargo_nombre": clean(str(data.get("Cargo") or "")),
                    "category": code,
                }
                for front in data["agrupaciones"]:
                    for entry in front["listas"]:
                        add(
                            **base,
                            agrupacion_id=_number(entry.get("idAgrupacion")),
                            agrupacion_nombre=clean(front["nombre"]),
                            lista_numero=_number(entry.get("idlista")),
                            lista_nombre=clean(entry["lista"]),
                            votos_tipo=POSITIVE,
                            votos_cantidad=_int(entry["Votos"]),
                        )
                for kind in KINDS:
                    add(**base, votos_tipo=kind, votos_cantidad=_int(data.get(kind)))
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / f"{held.key}.blocked.json").write_text(json.dumps(blocked, indent=1))
    frame = pl.DataFrame(kept).with_columns(pl.col("mesa_electores", "votos_cantidad").cast(pl.Int64))
    out = PARSED / f"{held.key}.parquet"
    frame.write_parquet(out)
    print(f"{held.key}: {frame.height:,} section rows staged from the results API", flush=True)
    return out


def staged(held: Held) -> pl.DataFrame:
    frame = pl.read_parquet(PARSED / f"{held.key}.parquet")
    return frame.with_columns(pl.col("votos_tipo").str.to_uppercase().replace(SPELLINGS))


def available() -> list[Held]:
    return [held for held in HELD if (PARSED / f"{held.key}.parquet").exists()]


class Frames(Package):
    """A package whose largest tables arrive as polars frames, keeping only the columns of the package format."""

    def frame(self, table: str, frame: pl.DataFrame) -> None:
        schema = TABLES[table][1] if table in TABLES else FACTS[table]
        super().frame(table, frame.select(column for column in frame.columns if column in schema))


def _source_rows(p: Package) -> None:
    for id, title, url in (
        (
            DINE_SOURCE,
            "Resultados electorales provisorios por mesa",
            "https://www.argentina.gob.ar/dine/resultados-electorales",
        ),
        (API_SOURCE, "Sistema de Publicación de Resultados Electorales", "https://resultados.mininterior.gob.ar/"),
    ):
        p.add(
            "source",
            id=id,
            kind="official_body",
            publisher="Dirección Nacional Electoral",
            title=title,
            url=url,
            license="Argentine public information, free reuse with the source cited (Law 27.275)",
            license_url="https://www.argentina.gob.ar/aaip/accesoalainformacion/ley-27275",
            attribution="Dirección Nacional Electoral, Ministerio del Interior",
        )


@functools.cache
def _places() -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    districts: dict[str, Counter[str]] = {}
    sections: dict[tuple[str, str], Counter[str]] = {}
    for held in available():
        frame = staged(held).select("distrito_id", "distrito_nombre", "seccion_id", "seccion_nombre").unique()
        for district, district_name, section, section_name in frame.iter_rows():
            districts.setdefault(district, Counter())[clean(district_name)] += held.day.year
            sections.setdefault((district, section), Counter())[clean(section_name)] += held.day.year

    def best(names: Counter[str]) -> str:
        return max(names.items(), key=lambda item: (item[0] != item[0].upper(), item[1]))[0]

    unknown = set(districts) - set(DISTRICTS)
    if unknown:
        raise ValueError(f"unknown district codes {sorted(unknown)}")
    return (
        {code: DISTRICTS[code] for code in districts},
        {code: best(names) for code, names in sections.items()},
    )


def geo() -> Package:
    districts, sections = _places()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        f"Argentina's {len(districts)} electoral districts and {len(sections):,} sections (departments, partidos, and "
        "comunas) as the Dirección Nacional Electoral codes them",
    )
    p.external |= {ARGENTINA, COUNTRY}
    _source_rows(p)
    for code, rank, kind, noun in (
        ("ar.distrito", 1, "administrative", "province"),
        ("ar.seccion", 2, "administrative", "department"),
        ("ar.mesa", 3, "precinct", "polling table"),
    ):
        p.add("scheme", code=code, polity_id=ARGENTINA, kind=kind, rank=rank, noun=noun, code_system=None)
    for code, text in sorted(districts.items(), key=lambda item: int(item[0])):
        id = area("ar.distrito", code)
        p.add("area", id=id, scheme_code="ar.distrito")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, text, "es", "official")
    for (district, section), text in sorted(sections.items(), key=lambda item: (int(item[0][0]), int(item[0][1]))):
        id = area("ar.seccion", f"{district}:{section}")
        p.add("area", id=id, scheme_code="ar.seccion")
        p.add("area_code", area_id=id, code=f"{district}:{section}")
        p.add("area_parent", area_id=id, parent_id=area("ar.distrito", district))
        p.name(id, text, "es", "official")
    return p


def _colors() -> dict[str, str]:
    colors: dict[str, str] = {}
    for held in [held for held in available() if held.url in raw.keys(SOURCE)]:
        archive = zipfile.ZipFile(raw.cached(SOURCE, held.url).path)
        for member in archive.namelist():
            if not re.search(r"colores[^/]*\.csv$", member, re.IGNORECASE):
                continue
            text = archive.read(member).decode("utf-8-sig", errors="replace")
            for row in csv.DictReader(io.StringIO(text)):
                fields = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                name, color = fields.get("agrupacion_nombre", ""), fields.get("agrupacion_color", "")
                if name and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                    colors[normal(name)] = color.lower()
    return colors


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Argentina's national offices, chambers, electoral systems, and the electoral fronts and parties on the "
        "Dirección Nacional Electoral's provisional-count files",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ARGENTINA}
    for held in [held for held in available() if held.url in raw.keys(SOURCE)]:
        p.artifact(raw.cached(SOURCE, held.url), DINE_SOURCE)
    for year in sorted({held.day.year for held in HELD}):
        p.artifact(raw.cached(SOURCE, menu_url(year)), API_SOURCE)
    for _, url in _totals_wanted():
        if url in raw.keys(SOURCE):
            p.artifact(raw.cached(SOURCE, url), API_SOURCE)
    for workbooks in SHEETS.values():
        for workbook in workbooks:
            p.artifact(raw.cached(SOURCE, sheet_url(workbook)), DINE_SOURCE)
    for id, kind, english, spanish in (
        (DEPUTIES_BODY, "lower_house", "Chamber of Deputies", "Cámara de Diputados de la Nación"),
        (SENATE_BODY, "upper_house", "Senate", "Senado de la Nación"),
        (MERCOSUR_BODY, "unicameral", "Mercosur Parliament (Argentine members)", "Parlamento del Mercosur"),
    ):
        p.add("body", id=id, polity_id=ARGENTINA, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, spanish, "es", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=ARGENTINA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Argentina", "en", "official")
    p.name(PRESIDENCY, "Presidente de la Nación Argentina", "es", "native", False)
    for id, since, rules, method in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=ARGENTINA, valid_from=since, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    spellings: dict[str, Counter[str]] = {}
    for held in available():
        names = (
            staged(held)
            .filter(pl.col("votos_tipo") == POSITIVE, pl.col("agrupacion_nombre") != "")
            .group_by("agrupacion_nombre")
            .len()
        )
        for name, n in names.iter_rows():
            spellings.setdefault(normal(name), Counter())[clean(name)] += n
    for sheets in history()[0].values():
        for sheet in sheets:
            for line in sheet.lines:
                if line.party:
                    spellings.setdefault(normal(line.party), Counter())[line.party] += line.votes
    colors = _colors()
    for normalized, names in sorted(spellings.items()):
        id = key("party", normalized)
        p.add("party", id=id, kind="party", polity_id=ARGENTINA)
        p.add("external_id", entity_id=id, scheme="ar.dine.agrupacion", value=normalized)
        p.name(id, names.most_common(1)[0][0], "es", "official")
        if normalized in colors:
            p.add("party_color", party_id=id, color=colors[normalized], source_id=DINE_SOURCE)
    return p


def _declared(held: Held, code: str, district: str) -> dict[str, object] | None:
    url = totals_url(held.day.year, held.election, CATEGORIES[code].cargo, district)
    if url not in raw.keys(SOURCE):
        return None
    data = json.loads(raw.cached(SOURCE, url).path.read_bytes())
    return data if isinstance(data, dict) and "agrupaciones" in data else None


def _number(text: object) -> str:
    return str(text or "").strip().lstrip("0")


def _int(value: object) -> int:
    return int(str(value or 0))


class Contest(NamedTuple):
    kind: str
    magnitude: int | None
    office: str | None
    body: str | None
    system: str | None


def _contest(held: Held, code: str, district: str) -> Contest:
    general = held.kind != "primary"
    if code == "president":
        return Contest("office", 1 if general else None, PRESIDENCY, None, None)
    if code == "deputies":
        seats = DEPUTIES[district][held.day.year % 4 != 1] if general else None
        return Contest("list_allocation", seats, None, DEPUTIES_BODY, DEPUTIES_SYSTEM)
    if code == "senate":
        return Contest("body_seats", 3 if general else None, None, SENATE_BODY, None)
    if code == "mercosur":
        return Contest(
            "list_allocation", MERCOSUR_NATIONAL_SEATS if general else None, None, MERCOSUR_BODY, MERCOSUR_SYSTEM
        )
    return Contest("body_seats", 1 if general else None, None, MERCOSUR_BODY, None)


def election(name: str) -> Package:
    held = BY_KEY[name]
    year = held.day.year
    first = FIRST_ROUND.get(held.key)
    p = Frames(
        f"{SOURCE}/{held.key}",
        SOURCE,
        {
            "primary": f"Argentina's {year} primaries (PASO) for national offices",
            "general": f"Argentina's {year} general election for national offices",
            "runoff": f"Argentina's {year} presidential runoff",
        }[held.kind]
        + ", by polling table, from the Dirección Nacional Electoral's provisional count",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", *([f"{SOURCE}/{first}"] if first else [])],
    )
    p.external |= {ARGENTINA, COUNTRY}
    by_section = held.url not in raw.keys(SOURCE)
    if by_section:
        cached = raw.keys(SOURCE)
        for url in _section_urls(held):
            if url in cached:
                p.uses(raw.cached(SOURCE, url))
        p.summary = p.summary.replace("by polling table", "by section").replace(
            "provisional count", "provisional count as its results service publishes it"
        )
    else:
        p.uses(raw.cached(SOURCE, held.url))
    districts, sections = _places()
    frame = staged(held).with_columns(
        contest=pl.when(pl.col("category").is_in([c for c, v in CATEGORIES.items() if v.national]))
        .then(pl.concat_str(pl.col("category"), pl.lit(":" + NATIONAL)))
        .otherwise(pl.concat_str("category", pl.lit(":"), "distrito_id"))
    )
    election_id = key("election", held.key)
    path = {
        "primary": f"ar/{held.day}-primary",
        "general": f"ar/{held.day}-general",
        "runoff": f"ar/{held.day}-presidential-runoff",
    }[held.kind]
    p.add(
        "election",
        id=election_id,
        polity_id=ARGENTINA,
        kind=held.kind,
        date_from=held.day,
        date_to=held.day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    english, spanish = {
        "primary": (
            f"{year} Argentine primary elections",
            f"Elecciones primarias abiertas, simultáneas y obligatorias (PASO) {year}",
        ),
        "general": (f"{year} Argentine general election", f"Elecciones generales {year}"),
        "runoff": (f"{year} Argentine presidential runoff", f"Segunda vuelta presidencial {year}"),
    }[held.kind]
    p.name(election_id, english, "en", "official")
    p.name(election_id, spanish, "es", "native", False)

    table = ["distrito_id", "seccion_id"] if by_section else ["distrito_id", "seccion_id", "circuito_id", "mesa_id"]
    units = frame.select(*table, "mesa_tipo").unique().sort(table)
    doubled = units.group_by(table).len().filter(pl.col("len") > 1)
    if doubled.height:
        raise ValueError(f"{held.key}: polling tables with two types: {doubled.head()}")
    unit_frame = units.with_columns(
        code=pl.concat_str(pl.lit(str(held.day)), *table, separator=":"),
        area_id=pl.struct("distrito_id", "seccion_id").map_elements(
            lambda row: (
                area("ar.seccion", f"{row['distrito_id']}:{row['seccion_id']}")
                if (row["distrito_id"], row["seccion_id"]) in sections
                else area("ar.distrito", row["distrito_id"])
            ),
            return_dtype=pl.String,
        ),
        kind=pl.when(pl.col("mesa_tipo").str.to_uppercase().str.contains("PRIV"))
        .then(pl.lit("prison"))
        .otherwise(pl.lit("standard")),
    ).with_columns(
        id=pl.col("code").map_elements(lambda code: key("unit", code), return_dtype=pl.String),
        polity_id=pl.lit(ARGENTINA),
        scheme_code=pl.lit("ar.mesa"),
        area_relation=pl.lit("within"),
    )
    if by_section:
        unit_frame = unit_frame.with_columns(
            scheme_code=pl.lit("ar.seccion"), area_relation=pl.lit("equals"), kind=pl.lit("aggregate")
        )
    p.frame("reporting_unit", unit_frame)
    unit_ids = unit_frame.select(*table, pl.col("id").alias("unit_id"))

    sets: dict[str, str] = {}
    option_rows: list[tuple[str, str, str | None, str]] = []
    for contest_key in sorted(frame["contest"].unique()):
        code, district = contest_key.split(":")
        category = CATEGORIES[code]
        here = frame.filter(pl.col("contest") == contest_key)
        contest = key("contest", held.key, contest_key)
        place = districts.get(district, district)
        shape = _contest(held, code, district)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind=shape.kind,
            office_id=shape.office,
            body_id=shape.body,
            district_area_id=COUNTRY if category.national else area("ar.distrito", district),
            magnitude=shape.magnitude,
            round_number=2 if held.kind == "runoff" else 1,
            previous_round_id=key("contest", first, contest_key) if first else None,
            electoral_system_id=shape.system,
            default_result_kind="counted",
        )
        p.name(contest, category.en if category.national else f"{category.en}, {place}", "en", "official")
        p.name(contest, category.es if category.national else f"{category.es}, {place}", "es", "native", False)
        tail = {"mercosur": "mercosur-national", "mercosur-regional": "mercosur"}.get(code, code)
        if not category.national:
            tail = f"{tail}-{changeset.slug(place) or district}"
        p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
        result_set = sets[contest_key] = key("result_set", held.key, contest_key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="preliminary",
            kind="counted",
            source_id=DINE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=(SECTION_METHOD if by_section else METHOD) + (PRIMARY_METHOD if held.kind == "primary" else ""),
            lang="en",
        )
        labels = (
            here.filter(pl.col("votos_tipo") == POSITIVE)
            .group_by("agrupacion_id", "agrupacion_nombre", "lista_numero", "lista_nombre")
            .agg(pl.col("votos_cantidad").sum())
            .sort("votos_cantidad", "agrupacion_nombre", "lista_nombre", descending=True)
        )
        fronts: dict[str, str] = {}
        lists: dict[str, set[str]] = {}
        list_names: dict[tuple[str, str], str] = {}
        for front, front_name, number, list_name, _ in labels.iter_rows():
            fronts.setdefault(front, clean(front_name))
            lists.setdefault(front, set()).add(number)
            list_names.setdefault((front, number), clean(list_name))
        option_kind = {"president": "ticket", "mercosur-regional": "candidate"}.get(code, "list")
        for front in sorted(fronts, key=lambda f: (len(f), f)):
            numbers = lists[front]
            parent = key("option", held.key, contest_key, front)
            party_id = party(fronts[front]) if fronts[front] else None
            split = held.kind == "primary" and numbers != {""}
            p.add(
                "contest_option",
                id=parent,
                contest_id=contest,
                kind=option_kind,
                party_id=party_id,
                ballot_label=fronts[front] or f"Agrupación {front}",
            )
            if not split:
                option_rows.extend((contest_key, front, number, parent) for number in numbers)
                continue
            for number in sorted(numbers, key=lambda n: (len(n), n)):
                child = key("option", held.key, contest_key, front, number)
                option_rows.append((contest_key, front, number, child))
                p.add(
                    "contest_option",
                    id=child,
                    contest_id=contest,
                    kind=option_kind,
                    parent_option_id=parent,
                    party_id=party_id,
                    ballot_label=list_names[(front, number)] or (f"Lista {number}" if number else fronts[front]),
                )
        if here.filter(pl.col("votos_tipo") == COMMAND).height:
            command = key("option", held.key, contest_key, "comando")
            option_rows.append((contest_key, COMMAND, "", command))
            p.add(
                "contest_option",
                id=command,
                contest_id=contest,
                kind="others",
                ballot_label="Votos del Comando Electoral",
            )
        declared = _declared(held, code, district)
        if declared is None and category.national:
            parts = [d for d in (_declared(held, code, x) for x in sorted(districts, key=int)) if d is not None]
            declared = _summed(parts) if parts else None
        if declared is not None:
            _add_declared(p, held, result_set, contest_key, declared, option_rows)

    options = pl.DataFrame(option_rows, schema=["contest", "agrupacion_id", "lista_numero", "option_id"], orient="row")
    frame = frame.select(
        *table, "contest", "mesa_electores", "agrupacion_id", "lista_numero", "votos_tipo", "votos_cantidad"
    )
    rows = frame.join(pl.DataFrame({"contest": list(sets), "result_set_id": list(sets.values())}), on="contest").join(
        unit_ids, on=table
    )
    votes = rows.filter(pl.col("votos_tipo") == POSITIVE).join(
        options, on=["contest", "agrupacion_id", "lista_numero"], how="left"
    )
    if votes["option_id"].null_count():
        raise ValueError(f"{held.key}: votes without an option: {votes.filter(pl.col('option_id').is_null()).head()}")
    votes = votes.group_by("result_set_id", "unit_id", "option_id").agg(pl.col("votos_cantidad").sum(), pl.len())
    doubled = votes.filter(pl.col("len") > 1)
    if doubled.height:
        raise ValueError(f"{held.key}: {doubled.height} options with two rows on one table: {doubled.head()}")
    unknown = rows.filter(~pl.col("votos_tipo").is_in([POSITIVE, *KINDS]))["votos_tipo"].unique().to_list()
    if unknown:
        raise ValueError(f"{held.key}: unknown ballot kinds {unknown}")
    pair = ["result_set_id", "unit_id"]
    others = (
        rows.filter(pl.col("votos_tipo") != POSITIVE)
        .group_by(*pair, "contest")
        .agg(pl.col("votos_cantidad").filter(pl.col("votos_tipo") == kind).sum().alias(kind) for kind in KINDS)
    )
    rolls = rows.group_by(pair).agg(
        pl.col("mesa_electores").max().alias("registered"), pl.col("mesa_electores").n_unique().alias("rolls")
    )
    if rolls.filter(pl.col("rolls") > 1).height:
        raise ValueError(f"{held.key}: polling tables with two roll sizes")
    positive = votes.group_by(pair).agg(pl.col("votos_cantidad").sum().alias("positive"))
    totals = (
        others.join(positive, on=pair, how="left")
        .join(rolls, on=pair, how="left")
        .with_columns(pl.col("positive").fill_null(0))
        .with_columns(
            valid=pl.col("positive") + pl.col(COMMAND),
            invalid=pl.col("NULO") + pl.col("RECURRIDO") + pl.col("IMPUGNADO"),
            objected=pl.col("RECURRIDO") + pl.col("IMPUGNADO"),
        )
        .with_columns(cast=pl.col("valid") + pl.col("invalid") + pl.col("EN BLANCO"))
    )
    unpaired = positive.join(others, on=pair, how="anti")
    if unpaired["positive"].sum():
        raise ValueError(f"{held.key}: tables with list votes but no blank or null count: {unpaired.head()}")
    votes = votes.join(unpaired, on=pair, how="anti")
    rolls = rolls.join(others, on=pair, how="semi")
    if unpaired.height:
        p.add(
            "note",
            id=key("note", held.key, "uncounted"),
            entity_id=election_id,
            kind="caveat",
            body_md=f"The provisional-count file lists {unpaired.height:,} category-table pairs with every list at "
            "zero and no blank, null, or objected row, so no certificate was counted for them; they are left out.",
            lang="en",
        )
    if held.key in ROLL_GAPS:
        p.accept("accounting.declared", ROLL_GAPS[held.key])
        for result_set in sets.values():
            p.add(
                "note",
                id=key("note", result_set, "rolls"),
                entity_id=result_set,
                kind="caveat",
                body_md="The registered voters summed here differ slightly from the total the results service "
                f"prints: {ROLL_GAPS[held.key]}",
                lang="en",
            )

    def measure(source: pl.DataFrame, number: int, value: str, option: pl.Expr | None = None) -> pl.DataFrame:
        return source.select(
            *pair,
            (pl.lit(None, pl.String) if option is None else option).alias("option_id"),
            pl.lit(number).alias("measure"),
            pl.col(value).alias("value"),
        )

    facts = [
        measure(votes, 1, "votos_cantidad", pl.col("option_id")),
        measure(rolls.filter(pl.col("registered").is_not_null()), 2, "registered"),
        *(
            measure(totals, number, column)
            for number, column in ((3, "cast"), (4, "valid"), (5, "invalid"), (6, "EN BLANCO"), (15, "objected"))
        ),
        measure(
            totals.join(options.filter(pl.col("agrupacion_id") == COMMAND), on="contest"),
            1,
            COMMAND,
            pl.col("option_id"),
        ),
    ]
    p.frame(
        "result_value",
        pl.concat(facts).with_columns(channel=pl.lit(0), count_round=pl.lit(0), flags=pl.lit(0)),
    )
    over = totals.filter(pl.col("cast") > pl.col("registered"))
    if over.height:
        p.accept(
            "accounting.turnout",
            f"{over.height:,} polling tables report more ballots than voters on their roll in the provisional count "
            "as transmitted on election night; stored as published",
        )
        for result_set, n in over.group_by("result_set_id").len().iter_rows():
            p.add(
                "note",
                id=key("note", result_set, "turnout"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"In {n} polling table{'s' if n > 1 else ''}, the provisional count reports more ballots than "
                "voters on the roll. The figures are stored as transmitted.",
                lang="en",
            )
    return p


def _summed(parts: list[dict[str, object]]) -> dict[str, object]:
    fronts: list[object] = []
    total: dict[str, object] = {"agrupaciones": fronts}
    for part in parts:
        for field in (COMMAND, "EN BLANCO", "IMPUGNADO", "NULO", "RECURRIDO", "Electores"):
            total[field] = _int(total.get(field)) + _int(part.get(field))
        listed = part["agrupaciones"]
        assert isinstance(listed, list)
        fronts.extend(listed)
    return total


def _add_declared(
    p: Package,
    held: Held,
    result_set: str,
    contest_key: str,
    declared: dict[str, object],
    option_rows: list[tuple[str, str, str | None, str]],
) -> None:
    options = {
        (front, number or ""): option for contest, front, number, option in option_rows if contest == contest_key
    }
    sums: Counter[str] = Counter()
    fronts = declared["agrupaciones"]
    assert isinstance(fronts, list)
    for front in fronts:
        for entry in front["listas"]:
            option = options.get((_number(entry.get("idAgrupacion")), _number(entry.get("idlista"))))
            if option is None:
                raise ValueError(f"{held.key} {contest_key}: the published totals list {entry}, which has no option")
            sums[option] += _int(entry["Votos"])
    if (COMMAND, "") in options:
        sums[options[(COMMAND, "")]] += _int(declared.get(COMMAND))
    for option, value in sums.items():
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=value)
    valid = sum(sums.values())
    invalid = _int(declared.get("NULO")) + _int(declared.get("RECURRIDO")) + _int(declared.get("IMPUGNADO"))
    blank = _int(declared.get("EN BLANCO"))
    for number, value in (
        (2, _int(declared.get("Electores"))),
        (3, valid + invalid + blank),
        (4, valid),
        (5, invalid),
        (6, blank),
        (15, _int(declared.get("RECURRIDO")) + _int(declared.get("IMPUGNADO"))),
    ):
        p.add("declared", result_set_id=result_set, option_id=None, measure=number, value=value)


# 1983 to 2009: the Dirección's definitive results by district, one workbook per category and year, one sheet per
# district. Each sheet carries its own date, because legislative elections were not always held on one day.

MONTHS = {
    "ENERO": 1,
    "FEBRERO": 2,
    "MARZO": 3,
    "ABRIL": 4,
    "MAYO": 5,
    "JUNIO": 6,
    "JULIO": 7,
    "AGOSTO": 8,
    "SEPTIEMBRE": 9,
    "SETIEMBRE": 9,
    "OCTUBRE": 10,
    "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}
SUMMARY = {
    "VOTOSPOSITIVOS": "positive",
    "VOTOSENBLANCO": "blank",
    "VOTOSNULOS": "null",
    "VOTOSANULADOS": "null",
    "VOTOSIMPUGNADOS": "objected",
    "VOTOSRECURRIDOS": "objected",
    "DIFERENCIADEACTAS": "difference",
    "TOTALDEVOTANTES": "total",
}
ALIASES = {"CAPITAL FEDERAL": "1", "CABA": "1", "TIERRA DEL FUEGO": "24"}
HISTORY_METHOD = (
    "The definitive count by district, as the Dirección Nacional Electoral publishes it in its historical results. "
    "Registered voters are the district's electoral roll, ballots cast are the voters the count recorded, and "
    "invalid ballots are the null ballots."
)
COLLEGE_METHOD = (
    " Until the 1994 constitutional reform, the president and the Capital's senators were chosen by electoral "
    "colleges: voters elected each party's electors in each district, and the electoral votes measure holds the "
    "electors each list won."
)


class Line(NamedTuple):
    formula: str
    party: str
    votes: int
    seats: int


class Sheet(NamedTuple):
    category: str
    district: str
    day: date
    registered: int | None
    lines: list[Line]
    totals: dict[str, int]
    college: bool
    elected: list[tuple[str, str]]
    workbook: str
    issues: list[tuple[str, str]]


def _cell(value: object) -> str:
    return clean(None if value is None else str(value))


def _count(value: object) -> int | None:
    text = _cell(value).replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", text):
        return int(re.sub(r"[.,]", "", text))
    if re.fullmatch(r"\d+(\.0+)?", text):
        return int(float(text))
    return None


def _day(title: str, year: int) -> date | None:
    match = re.search(r"(\d{1,2}) DE ([A-Z]+)(?: DE (\d{4}))?", normal(title))
    if match is None or match.group(2) not in MONTHS:
        return None
    return date(int(match.group(3) or year), MONTHS[match.group(2)], int(match.group(1)))


def _district(name: str) -> str | None:
    text = normal(name)
    for alias, code in ALIASES.items():
        if text.startswith(alias):
            return code
    names = {normal(value): code for code, value in DISTRICTS.items()}
    return names.get(text) or next((code for value, code in names.items() if text and value.startswith(text)), None)


def _sheets(year: int, workbook: str) -> tuple[list[Sheet], list[str]]:
    category = "president" if "presidente" in workbook else "senate" if "senadores" in workbook else "deputies"
    book = pl.read_excel(
        raw.cached(SOURCE, sheet_url(workbook)).path, sheet_id=0, has_header=False, raise_if_empty=False
    )
    found: list[Sheet] = []
    problems: list[str] = []
    for title, frame in book.items():
        if normal(title).startswith(("NACIONAL", "LEGISLACION")) or frame.height < 5:
            continue
        rows = [[_cell(value) for value in row] for row in frame.iter_rows()]
        district = _district(title) or _district(rows[0][0])
        day = next((d for row in rows[:4] for d in [_day(row[0], year)] if d is not None), None)
        if district is None or day is None:
            problems.append(f"{workbook} {title}: no district or date")
            continue
        registered = next((_count(row[1]) for row in rows if "INSCRIPTOS" in normal(row[0]) and len(row) > 1), None)
        header = next((i for i, row in enumerate(rows) if "VOTOS" in [normal(cell) for cell in row]), None)
        if header is None:
            problems.append(f"{workbook} {title}: no votes column")
            continue
        heads = [normal(cell) for cell in rows[header]]
        votes = heads.index("VOTOS")
        seats = next((i for i, h in enumerate(heads) if h == "CARGOS" or h.startswith("ELECTORES")), None)
        college = seats is not None and heads[seats].startswith("ELECTORES")
        formula = next((i for i, h in enumerate(heads) if h.startswith("FORMULA")), None)
        party_column = next((i for i, h in enumerate(heads) if h.startswith("AGRUPACION")), 0)
        lines: list[Line] = []
        totals: dict[str, int] = {}
        end = len(rows)
        for index in range(header + 1, len(rows)):
            row = rows[index]
            labels = [normal(cell) for cell in row[:votes]]
            if any(label.startswith("LISTADO") for label in labels):
                end = index
                break
            squeezed = [label.replace(" ", "") for label in labels]
            summary = next((SUMMARY[label] for label in squeezed if label in SUMMARY), None)
            value = _count(row[votes])
            if summary is not None:
                if value is not None:
                    totals[summary] = totals.get(summary, 0) + value
                continue
            if any(label.startswith(("VOTOS", "TOTAL", "DIFERENCIA")) for label in squeezed):
                problems.append(f"{workbook} {title}: unread summary row {row}")
                continue
            if value is None:
                continue
            lines.append(
                Line(
                    row[formula] if formula is not None else "",
                    row[party_column],
                    value,
                    (_count(row[seats]) or 0) if seats is not None else 0,
                )
            )
        listed = sum(line.votes for line in lines)
        issues: list[tuple[str, str]] = []
        if "positive" in totals and listed != totals["positive"]:
            issues.append(
                (
                    "accounting.votes_valid",
                    f"the lists sum to {listed:,} votes, while the sheet prints {totals['positive']:,} valid votes",
                )
            )
        parts = sum(totals.get(k, 0) for k in ("positive", "blank", "null", "objected", "difference"))
        if "total" in totals and "positive" in totals and parts != totals["total"]:
            issues.append(
                (
                    "accounting.ballots_sum",
                    (
                        f"valid, blank, and null ballots sum to {parts:,}, while the sheet prints {totals['total']:,} "
                        "voters"
                    ),
                )
            )
        if registered is not None and totals.get("total", 0) > registered:
            issues.append(
                (
                    "accounting.turnout",
                    f"the sheet prints {registered:,} registered voters and {totals['total']:,} voters",
                )
            )
        for _, issue in issues:
            problems.append(f"{workbook} {title}: {issue}, stored as printed")
        elected: list[tuple[str, str]] = []
        labels = {normal(line.party) for line in lines}
        for row in rows[end + 1 :]:
            name = row[0]
            text = normal(name)
            if not name or text in labels or text.startswith("ALIANZA") or name != name.upper():
                break
            elected.append((name, next((cell for cell in row[1:] if cell), "")))
        found.append(
            Sheet(
                category,
                district,
                day,
                registered,
                lines,
                totals,
                college,
                elected if not college else [],
                workbook,
                issues,
            )
        )
    return found, problems


@functools.cache
def history() -> tuple[dict[date, list[Sheet]], tuple[str, ...]]:
    by_day: dict[date, list[Sheet]] = {}
    problems: list[str] = []
    for year, workbooks in SHEETS.items():
        for workbook in workbooks:
            sheets, found = _sheets(year, workbook)
            problems += found
            for sheet in sheets:
                by_day.setdefault(sheet.day, []).append(sheet)
    return dict(sorted(by_day.items())), tuple(problems)


def _person(name: str) -> str:
    surname, _, given = name.partition(",")
    words = f"{given} {surname}" if given else name
    small = {"DE", "DEL", "LA", "LAS", "LOS", "Y", "E"}
    return " ".join(w.lower() if w in small else w.capitalize() for w in clean(words).split()).strip()


def _formula_key(text: str) -> str:
    head = normal(re.sub(r"[‐-―−]", "-", text)).split("-")[0]
    return re.sub(r"[^A-Z]", "", head)


def past(day: date) -> Package:
    sheets = history()[0][day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Argentina's national election of {day:%-d %B %Y} ("
        + ", ".join(sorted({sheet.category for sheet in sheets}))
        + "), by district, from the Dirección Nacional Electoral's definitive results",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ARGENTINA, COUNTRY}
    for workbook in sorted({sheet.workbook for sheet in sheets}):
        p.uses(raw.cached(SOURCE, sheet_url(workbook)))
    districts, _ = _places()
    election_id = key("election", day)
    path = f"ar/{day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=ARGENTINA,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{day.year} Argentine national election of {day:%-d %B}", "en", "official")
    p.name(election_id, f"Elecciones nacionales del {day.day} de {_month(day)} de {day.year}", "es", "native", False)
    units: dict[str, str] = {}
    for district in sorted({sheet.district for sheet in sheets}, key=int):
        unit = units[district] = key("unit", day, district)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ARGENTINA,
            scheme_code="ar.distrito",
            code=f"{day}:{district}",
            area_relation="equals",
            area_id=area("ar.distrito", district),
            kind="aggregate",
        )
    groups: dict[tuple[str, str], list[Sheet]] = {}
    for sheet in sheets:
        groups.setdefault((sheet.category, NATIONAL if sheet.category == "president" else sheet.district), []).append(
            sheet
        )
    ballots_off: Counter[str] = Counter()
    accepted: dict[str, list[str]] = {}
    for (code, district), members in sorted(groups.items()):
        category = CATEGORIES[code]
        place = districts.get(district, district)
        contest_key = f"{code}:{district}"
        contest = key("contest", day, contest_key)
        college = any(sheet.college for sheet in members)
        seats = sum(line.seats for sheet in members for line in sheet.lines) if not college else None
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind={"president": "office", "deputies": "list_allocation"}.get(code, "body_seats"),
            office_id=PRESIDENCY if code == "president" else None,
            body_id={"deputies": DEPUTIES_BODY, "senate": SENATE_BODY}.get(code),
            district_area_id=COUNTRY if code == "president" else area("ar.distrito", district),
            magnitude=1 if code == "president" else seats or None,
            round_number=1,
            electoral_system_id=DEPUTIES_SYSTEM if code == "deputies" else None,
            default_result_kind="counted",
        )
        p.name(contest, category.en if code == "president" else f"{category.en}, {place}", "en", "official")
        p.name(contest, category.es if code == "president" else f"{category.es}, {place}", "es", "native", False)
        tail = "president" if code == "president" else f"{code}-{changeset.slug(place) or district}"
        p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, contest_key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=DINE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=HISTORY_METHOD + (COLLEGE_METHOD if college else ""),
            lang="en",
        )
        labels: dict[str, Counter[str]] = {}
        options: dict[str, str] = {}

        def option_key(line: Line) -> str:
            if code == "president" and line.formula:
                return "F:" + _formula_key(line.formula)
            return "P:" + normal(line.party)

        for sheet in members:
            for line in sheet.lines:
                labels.setdefault(option_key(line), Counter())[line.formula or line.party] += line.votes
        for name, spelled in sorted(labels.items(), key=lambda item: (-sum(item[1].values()), item[0])):
            option = options[name] = key("option", day, contest_key, name)
            label = spelled.most_common(1)[0][0]
            parties = {normal(line.party) for sheet in members for line in sheet.lines if option_key(line) == name} - {
                ""
            }
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="ticket" if name.startswith("F:") else "list",
                party_id=key("party", next(iter(parties))) if len(parties) == 1 else None,
                ballot_position=None,
                ballot_label=label,
            )
        for sheet in members:
            unit = units[sheet.district]
            summed: Counter[str] = Counter()
            electors: Counter[str] = Counter()
            for line in sheet.lines:
                summed[options[option_key(line)]] += line.votes
                electors[options[option_key(line)]] += line.seats
            for option, value in summed.items():
                _value(p, result_set, unit, option, 1, value)
                if sheet.college:
                    _value(p, result_set, unit, option, 11, electors[option])
            totals = sheet.totals
            if sheet.registered:
                _value(p, result_set, unit, None, 2, sheet.registered)
            if "total" in totals:
                _value(p, result_set, unit, None, 3, totals["total"])
            _value(p, result_set, unit, None, 4, totals.get("positive", sum(summed.values())))
            if "null" in totals:
                _value(p, result_set, unit, None, 5, totals["null"] + totals.get("objected", 0))
            if "objected" in totals:
                _value(p, result_set, unit, None, 15, totals["objected"])
            if "blank" in totals:
                _value(p, result_set, unit, None, 6, totals["blank"])
            if totals.get("difference"):
                ballots_off[result_set] += totals["difference"]
            for code_, issue in sheet.issues:
                where = districts.get(sheet.district, sheet.district)
                accepted.setdefault(code_, []).append(f"{where} {sheet.category} ({issue})")
                p.add(
                    "note",
                    id=key("note", result_set, code_),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"In the Dirección's results for this district, {issue}. The figures are stored as "
                    "printed.",
                    lang="en",
                )
            if not sheet.college and code != "president":
                for option, n in electors.items():
                    if n:
                        p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=n)
                _elected(p, day, contest_key, result_set, sheet, options, option_key)
    for code_, where in accepted.items():
        p.accept(code_, "the Dirección's own sheets are inconsistent, stored as printed: " + "; ".join(where))
    if ballots_off:
        reason = (
            "the Dirección prints a difference between certificates (diferencia de actas) that is neither a valid, "
            "a blank, nor a null ballot; stored as printed"
        )
        p.accept("accounting.ballots_sum", "; ".join(filter(None, [reason, p.accepted.get("accounting.ballots_sum")])))
        for result_set, n in ballots_off.items():
            p.add(
                "note",
                id=key("note", result_set, "difference"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"The ballots cast include {n:,} that the Dirección prints as a difference between count "
                "certificates (diferencia de actas), counted neither as valid, blank, nor null ballots.",
                lang="en",
            )
    return p


def _month(day: date) -> str:
    return next(name.lower() for name, number in MONTHS.items() if number == day.month)


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def _elected(
    p: Package,
    day: date,
    contest_key: str,
    result_set: str,
    sheet: Sheet,
    options: dict[str, str],
    option_key: Callable[[Line], str],
) -> None:
    winners = [line for line in sheet.lines if line.seats]
    if not sheet.elected or len(sheet.elected) != sum(line.seats for line in winners):
        return
    groups: list[tuple[str, list[str]]] = []
    for name, party in sheet.elected:
        if party or not groups:
            groups.append((party, []))
        groups[-1][1].append(name)
    if len(groups) != len(winners):
        return
    paired: list[tuple[Line, list[str]]] = []
    left = list(winners)
    for party, names in groups:
        line = max(left, key=lambda line: difflib.SequenceMatcher(None, normal(party), normal(line.party)).ratio())
        if line.seats != len(names):
            return
        left.remove(line)
        paired.append((line, names))
    for line, names in paired:
        option = options[option_key(line)]
        for position, name in enumerate(names, 1):
            tag = f"{day}:{contest_key}:{normal(line.party)}:{position}"
            person = key("person", tag)
            p.add("person", id=person)
            p.name(person, _person(name), "es", "official")
            candidacy = key("candidacy", tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=position,
                ballot_name=name,
            )
            p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{day}" for day in history()[0]),
        *(f"{SOURCE}/{held.key}" for held in available()),
    ]


def _build(name: str) -> str:
    election(name).write()
    return name


def parse() -> None:
    cached = raw.keys(SOURCE)
    wanted = [held.key for held in HELD if held.url in cached and not (PARSED / f"{held.key}.parquet").exists()]
    with ProcessPoolExecutor(3) as pool:
        list(pool.map(stage, wanted))
    for held in HELD:
        if held.url not in cached and not (PARSED / f"{held.key}.parquet").exists():
            stage_sections(held.key)
    geo().write()
    registry().write()
    for day in history()[0]:
        past(day).write()
    for held in sorted(available(), key=lambda held: held.key in FIRST_ROUND):
        _build(held.key)


def emit() -> None:
    package.run("load", packages())
