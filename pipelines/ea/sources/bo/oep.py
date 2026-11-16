import functools
import json
import re
import unicodedata
import zipfile
from collections import Counter
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "bo.oep"
BOLIVIA = str(uuid5(package.NAMESPACE, "polity\x1fBO"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBOL"))
HOME = "32"
"""The country code the Órgano Electoral Plurinacional gives Bolivia in its files; every other code is abroad."""
ARCHIVE = "https://archive.org"
REGISTERED, CAST, VALID, INVALID, BLANK = 2, 3, 4, 5, 6
REDISTRICTED = date(2025, 8, 17)
"""The first election on the redrawn single-member districts; the 2019 and 2020 elections share the earlier map."""


class Item(NamedTuple):
    """An Internet Archive item that holds copies of the Órgano Electoral Plurinacional's files."""

    id: str
    title: str
    kind: str
    notes: str


ITEMS = {
    "2019": Item(
        "2019_Bolivia_general_election_data",
        "Cómputo Elecciones Generales 2019: exportación por mesa (copia del 25 de octubre de 2019)",
        "official_body",
        "The Órgano Electoral Plurinacional's export of the official count by polling table, downloaded from "
        "computo.oep.org.bo on 25 October 2019 and deposited in the Internet Archive before the results sites "
        "were taken down when the election was annulled. Fetched from that copy.",
    ),
    "2020": Item(
        "bolivia_eg2020_result",
        "Cómputo Elecciones Generales 2020: exportaciones por mesa",
        "official_body",
        "The Órgano Electoral Plurinacional's exports of the official count by polling table, downloaded from its "
        "results site every 10 to 20 minutes during the count and deposited unchanged in the Internet Archive. "
        "The authority's hosts refuse connections from our fetcher, so the files were fetched from that copy; the "
        "last export, of 28 October 2020, is used.",
    ),
    "2021": Item(
        "bolivia-elections-2021",
        "Elecciones Subnacionales 2021: votos por mesa, compilados de las exportaciones del cómputo",
        "community",
        "A table of every polling table's votes in the 2021 subnational elections and the 11 April 2021 "
        "gubernatorial runoffs, compiled by an Internet Archive contributor from the Órgano Electoral "
        "Plurinacional's successive exports of its official count (each table as first seen, with the time "
        "added). It keeps the authority's columns for the place, the table, its registered voters, and each "
        "organization's votes, but not its blank, null, or valid totals.",
    ),
    "2025": Item(
        "bolivia-elections-2025",
        "Cómputo Elecciones Generales 2025: exportaciones por mesa, y resultados del SIREPRE",
        "official_body",
        "The Órgano Electoral Plurinacional's exports of the official count by polling table for both rounds, and "
        "the responses of its preliminary results service (SIREPRE) for the first round, deposited unchanged in "
        "the Internet Archive. The authority's hosts refuse connections from our fetcher, so the files were fetched "
        "from that copy.",
    ),
}
LICENSE = (
    "Public information of the Órgano Electoral Plurinacional of Bolivia, published without a stated license; "
    "reuse with the source acknowledged"
)


def download(item: str, path: str) -> str:
    return f"{ARCHIVE}/download/{ITEMS[item].id}/{path}"


R2019 = download("2019", "acta.2019.10.25.21.09.30.xlsx")
R2020 = download("2020", "2020-bolivia-spreadsheets-raw-unrepaired.zip")
R2020_MEMBER = "exportacion/EG2020_20201028_030234_3356419605290724947.csv"
R2021 = download("2021", "2021-R1/2021-R1-VOTOS.csv")
R2021_RUNOFF = download("2021", "2021-R2/2021-R2-VOTOS.csv")
R2025 = download("2025", "r1/computo/raw/EG2025_20250824_235619_5976286028509320003.csv")
R2025_RUNOFF = download("2025", "r2/computo/raw/EG2025_20251026_214642_5213834057249010478.csv")
SIREPRE = download("2025", "r1/sirepre/raw/SIREPRE_2025_R1_RAW_DATA.zip")
FILES = {
    "2019": [(R2019, ".xlsx")],
    "2020": [(R2020, ".zip")],
    "2021": [(R2021, ".csv"), (R2021_RUNOFF, ".csv")],
    "2025": [(R2025, ".csv"), (R2025_RUNOFF, ".csv"), (SIREPRE, ".zip")],
}


class Day(NamedTuple):
    day: date
    item: str
    url: str
    kind: str
    english: str
    spanish: str
    word: str
    first: date | None
    """The first round's date, for a runoff."""


DAYS = (
    Day(
        date(2019, 10, 20),
        "2019",
        R2019,
        "general",
        "2019 Bolivian general election",
        "Elecciones Generales 2019",
        "general",
        None,
    ),
    Day(
        date(2020, 10, 18),
        "2020",
        R2020,
        "general",
        "2020 Bolivian general election",
        "Elecciones Generales 2020",
        "general",
        None,
    ),
    Day(
        date(2021, 3, 7),
        "2021",
        R2021,
        "local",
        "2021 Bolivian subnational elections",
        "Elecciones Subnacionales 2021",
        "subnational",
        None,
    ),
    Day(
        date(2021, 4, 11),
        "2021",
        R2021_RUNOFF,
        "runoff",
        "2021 Bolivian gubernatorial runoffs",
        "Segunda vuelta de gobernadores 2021",
        "runoff",
        date(2021, 3, 7),
    ),
    Day(
        date(2025, 8, 17),
        "2025",
        R2025,
        "general",
        "2025 Bolivian general election",
        "Elecciones Generales 2025",
        "general",
        None,
    ),
    Day(
        date(2025, 10, 19),
        "2025",
        R2025_RUNOFF,
        "runoff",
        "2025 Bolivian presidential election, second round",
        "Segunda vuelta presidencial 2025",
        "runoff",
        date(2025, 8, 17),
    ),
)
BY_DAY = {d.day: d for d in DAYS}

OFFICES = {
    "PRESIDENTE": "president",
    "Presidente y Vicepresidente": "president",
    "DIPUTADO CIRCUNSCRIPCIÓN UNINOMINAL": "uninominal",
    "Diputados Uninominales": "uninominal",
    "DIPUTADO CIRCUNSCRIPCIÓN ESPECIAL": "special",
    "Diputados Especiales": "special",
    "GOBERNADOR(A)": "governor",
    "ASAMBLEISTA POBLACION": "population",
    "ASAMBLEISTA TERRITORIO": "territory",
    "ALCALDESA/ALCALDE": "mayor",
    "CONCEJALES": "council",
    "SUBGOBERNADOR(A)": "subgovernor",
    "CORREGIDOR(A)": "corregidor",
    "EJECUTIVO(A) REGIONAL": "regional_executive",
    "ASAMBLEISTA REGIONAL POBLACION": "regional_population",
    "ASAMBLEISTA REGIONAL TERRITORIO": "regional_territory",
    "EJECUTIVO(A) DESARROLLO": "development",
}
"""Each ballot strip (franja) the files name, by the contest family it elects."""
LEVEL = {
    "president": "country",
    "uninominal": "uninominal",
    "special": "special",
    "governor": "department",
    "population": "department",
    "territory": "province",
    "subgovernor": "province",
    "regional_executive": "province",
    "regional_population": "province",
    "mayor": "municipality",
    "council": "municipality",
    "corregidor": "municipality",
    "regional_territory": "municipality",
    "development": "municipality",
}
"""The district of each family. The Gran Chaco region of Tarija is the province of Gran Chaco."""
INDIGENOUS = {"APG", "BIA YUQUI", "CIDOB", "CONAMAQ", "CPEM B", "OICH", "ORCAWETA", "YAMINAWA"}
"""Organizations of indigenous peoples that stand in the special districts."""

PLACE = ["pais", "dep", "prov", "sec", "loc", "rec", "numero"]
NAMES = ["paisn", "depn", "provn", "mun", "locn", "recn"]
ACCOUNTING = ["registered", "cast", "valid", "invalid", "blank"]
MEASURES = {"registered": REGISTERED, "cast": CAST, "valid": VALID, "invalid": INVALID, "blank": BLANK}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def circunscripcion(scheme: str, vintage: str, number: int) -> str:
    return key("area", scheme, vintage, number)


def vintage(day: date) -> str:
    return "2025" if day >= REDISTRICTED else "2019"


def fold(text: object) -> str:
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", plain).upper().split())


def clean(text: object) -> str:
    return " ".join(str(text or "").replace('"', "").split())


def source_id(item: str) -> str:
    return key("source", item)


SIREPRE_SOURCE = key("source", "sirepre-2025")


def fetch() -> None:
    for files in FILES.values():
        for url, suffix in files:
            raw.fetch(SOURCE, url, suffix=suffix)


# Reading the files


def _int(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars().cast(pl.Int64, strict=False)


def _frame(
    data: pl.DataFrame, columns: dict[str, str], parties: list[str], accounting: dict[str, str]
) -> tuple[pl.DataFrame, list[str]]:
    """Normalize one file: place and table columns, the accounting, and one column per organization."""
    labels = [label(c) for c in parties]
    frame = data.select(
        office=pl.col(columns["office"]).replace_strict(OFFICES),
        **{name: pl.col(column).cast(pl.String) for name, column in columns.items() if name != "office"},
        **{name: _int(column) for name, column in accounting.items()},
        **{lab: _int(column) for lab, column in zip(labels, parties, strict=True)},
    )
    for name in [*PLACE, *NAMES, "cu", "ce"]:
        if name not in frame.columns:
            frame = frame.with_columns(pl.lit(None, pl.String).alias(name))
    for name in ACCOUNTING:
        if name not in frame.columns:
            frame = frame.with_columns(pl.lit(None, pl.Int64).alias(name))
    frame = frame.with_columns(pl.col(n).map_elements(clean, return_dtype=pl.String) for n in NAMES)
    return frame, labels


def label(column: str) -> str:
    """The organization's acronym as the ballot prints it."""
    return " ".join(column.replace("_", "-").split())


def sirepre() -> tuple[dict[str, dict[str, str]], dict[str, set[str]]]:
    """The first round's ballot columns (voto_1 to voto_10) with each organization's acronym, name, and color, and
    for each special district (by its code) the acronyms on its ballot that are not in those columns."""
    archive = zipfile.ZipFile(raw.cached(SOURCE, SIREPRE).path)
    columns: dict[str, dict[str, str]] = {}
    for name in sorted(archive.namelist()):
        if name.startswith("histograma/") and name.endswith(".json") and "ERROR" not in name:
            for row in json.loads(archive.read(name)).get("candidatos", []):
                columns.setdefault(
                    row["campo"], {"sigla": row["sigla"], "nombre": row["nombre"], "color": row["color"]}
                )
    known = {row["sigla"] for row in columns.values()}
    extra: dict[str, set[str]] = {}
    for name in sorted(archive.namelist()):
        found = re.search(r"resultados/especial/idDepartamento\d+_idCircunscripcion(\d+)\.json$", name)
        if found is not None:
            siglas = {row["sigla"] for row in json.loads(archive.read(name))["grafica"]}
            extra[found.group(1)] = siglas - known - {""}
    return columns, extra


def _columns_2025(data: pl.DataFrame) -> dict[str, str]:
    """Name each VotoN column: the first ten by the SIREPRE's fields, the others by the one special district whose
    ballot holds them, where their votes are."""
    columns, extra = sirepre()
    named: dict[str, str] = {}
    for column in (c for c in data.columns if re.fullmatch(r"Voto\d+", c)):
        field = f"voto_{column.removeprefix('Voto')}"
        if field in columns:
            named[column] = columns[field]["sigla"]
            continue
        cast = pl.col(column).cast(pl.Int64)
        where = set(data.filter((cast > 0) & pl.col("Descripcion").str.contains("ESPECIAL"))["CodigoCircunscripcionE"])
        if len(where) != 1 or len(extra.get(next(iter(where)), set())) != 1:
            raise ValueError(f"2025: cannot name {column}, with votes in special districts {sorted(where)}")
        named[column] = next(iter(extra[next(iter(where))]))
    return named


GENERAL = {
    "office": "Descripcion",
    "pais": "CodigoPais",
    "paisn": "NombrePais",
    "dep": "CodigoDepartamento",
    "depn": "NombreDepartamento",
    "cu": "CodigoCircunscripcionU",
    "ce": "CodigoCircunscripcionE",
    "prov": "CodigoProvincia",
    "provn": "NombreProvincia",
    "sec": "CodigoSeccion",
    "mun": "NombreMunicipio",
    "loc": "CodigoLocalidad",
    "locn": "NombreLocalidad",
    "rec": "CodigoRecinto",
    "recn": "NombreRecinto",
    "numero": "NumeroMesa",
}
GENERAL_ACCOUNTING = {
    "registered": "InscritosHabilitados",
    "valid": "VotoValido",
    "blank": "VotoBlanco",
    "invalid": "TotalVotoNulo",
    "cast": "VotoEmitido",
}


def _2025(url: str) -> tuple[pl.DataFrame, list[str]]:
    data = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema_length=0)
    if url == R2025:
        named = _columns_2025(data)
        data = data.rename(named)
        parties = list(named.values())
    else:
        start = data.columns.index("InscritosHabilitados") + 1
        parties = data.columns[start : data.columns.index("VotoValido")]
    for real, column in (("VotoValidoReal", "VotoValido"), ("VotoEmitidoReal", "VotoEmitido")):
        if not data.filter(pl.col(real) != pl.col(column)).is_empty():
            raise ValueError(f"{url}: {real} differs from {column}")
    return _frame(data, GENERAL, parties, GENERAL_ACCOUNTING)


TEXT = r"(.*?)"
NUMBER = r"(\d+)"


def _2020() -> tuple[pl.DataFrame, list[str]]:
    """The export's place names hold unquoted commas, so each line is split on its numeric fields."""
    archive = zipfile.ZipFile(raw.cached(SOURCE, R2020).path)
    lines = archive.read(R2020_MEMBER).decode("latin-1").splitlines()
    header = lines[0].split(",")
    parts = [NUMBER, r"([A-ZÁÉÍÓÚ ]+)", NUMBER, TEXT, NUMBER, TEXT, NUMBER, NUMBER, NUMBER, TEXT, NUMBER, TEXT]
    parts += [NUMBER, TEXT, NUMBER, TEXT, NUMBER] + [NUMBER] * (len(header) - 17)
    pattern = re.compile("^" + ",".join(parts) + "$")
    rows = []
    for line in lines[1:]:
        found = pattern.match(line)
        if found is None:
            raise ValueError(f"unreadable line in the 2020 export: {line[:120]}")
        rows.append(found.groups())
    data = pl.DataFrame(rows, schema=header, orient="row")
    for a, b in (("VOTO_VALIDO", "VOTO_VALIDO_SISTEMA"), ("VOTO_EMITIDO", "VOTO_EMITIDO_SISTEMA")):
        if not data.filter(pl.col(a) != pl.col(b)).is_empty():
            raise ValueError(f"2020: {a} differs from {b}")
    columns = {
        "office": "CANDIDATURA",
        "pais": "ID_PAIS",
        "paisn": "PAIS",
        "dep": "ID_DEPARTAMENTO",
        "depn": "DEPARTAMENTO",
        "cu": "CIRCUNSCRIPCION_UNINOMINAL",
        "ce": "CIRCUNSCRIPCION_ESPECIAL",
        "prov": "ID_PROVINCIA",
        "provn": "PROVINCIA",
        "sec": "SECCION",
        "mun": "MUNICIPIO",
        "loc": "ID_LOCALIDAD",
        "locn": "LOCALIDAD",
        "rec": "ID_RECINTO",
        "recn": "RECINTO",
        "numero": "NUMERO_MESA",
    }
    parties = header[header.index("INSCRITOS_HABILITADOS") + 1 : header.index("VOTO_VALIDO")]
    accounting = {
        "registered": "INSCRITOS_HABILITADOS",
        "valid": "VOTO_VALIDO",
        "blank": "VOTO_BLANCO",
        "invalid": "VOTO_NULO",
        "cast": "VOTO_EMITIDO",
    }
    return _frame(data, columns, parties, accounting)


def _2021(url: str) -> tuple[pl.DataFrame, list[str]]:
    data = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema_length=0)
    data = data.rename({c: c.lstrip("﻿") for c in data.columns})
    columns = {
        "office": "DESCRIPCION",
        "pais": "CODIGO_PAIS",
        "paisn": "NOMBRE_PAIS",
        "dep": "CODIGO_DEPARTAMENTO",
        "depn": "NOMBRE_DEPARTAMENTO",
        "prov": "CODIGO_PROVINCIA",
        "provn": "NOMBRE_PROVINCIA",
        "sec": "CODIGO_SECCION",
        "mun": "NOMBRE_MUNICIPIO",
        "loc": "CODIGO_LOCALIDAD",
        "locn": "NOMBRE_LOCALIDAD",
        "rec": "CODIGO_RECINTO",
        "recn": "NOMBRE_RECINTO",
        "numero": "NUMERO_MESA",
    }
    parties = data.columns[data.columns.index("INSCRITOS_HABILITADOS") + 1 :]
    return _frame(data, columns, parties, {"registered": "INSCRITOS_HABILITADOS"})


def _2019() -> tuple[pl.DataFrame, list[str]]:
    data = pl.read_excel(raw.cached(SOURCE, R2019).path, infer_schema_length=0)
    if set(data["Estado acta"].unique()) != {"Computada"}:
        raise ValueError("2019: the export holds tables that were not counted")
    parties = data.columns[data.columns.index("Inscritos") + 1 : data.columns.index("Votos Válidos")]
    data = data.with_columns(
        cu=pl.col("Circunscripción").str.extract(r"Uninominal (\d+)$"),
        ce=pl.col("Circunscripción").str.extract(r"Especial (\d+)$"),
        abroad=pl.col("País") != "Bolivia",
    )
    places = _municipalities_2019(data)
    data = data.join(places, on=["Número departamento", "Provincia", "Municipio"], how="left")
    data = data.with_columns(
        pais=pl.when("abroad").then(pl.col("País")).otherwise(pl.lit(HOME)),
        dep=pl.when("abroad").then(None).otherwise(pl.col("Número departamento")),
        prov=pl.when("abroad").then(None).otherwise(pl.col("prov")),
        sec=pl.when("abroad").then(None).otherwise(pl.col("sec")),
        mesa=pl.col("Código Mesa"),
    )
    columns = {
        "office": "Elección",
        "pais": "pais",
        "paisn": "País",
        "dep": "dep",
        "depn": "Departamento",
        "cu": "cu",
        "ce": "ce",
        "prov": "prov",
        "provn": "Provincia",
        "sec": "sec",
        "mun": "Municipio",
        "locn": "Localidad",
        "recn": "Recinto",
        "numero": "Número Mesa",
        "mesa": "mesa",
    }
    accounting = {"registered": "Inscritos", "valid": "Votos Válidos", "blank": "Blancos", "invalid": "Nulos"}
    return _frame(data, columns, parties, accounting)


def _municipalities_2019(data: pl.DataFrame) -> pl.DataFrame:
    """The 2019 export names municipalities without codes; match them to the codes of the 2020 and 2025 exports."""
    frames = (rows(date(2020, 10, 18))[0], rows(date(2025, 8, 17))[0])
    later = (
        pl.concat([f.filter(~pl.col("abroad")).select("dep", "prov", "sec", "mun") for f in frames])
        .unique()
        .with_columns(fold=pl.col("mun").map_elements(fold, return_dtype=pl.String))
    )
    codes: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for dep, prov, sec, folded in later.select("dep", "prov", "sec", "fold").iter_rows():
        codes.setdefault((dep, folded), set()).add((prov, sec))
    matched = []
    wanted = data.filter(~pl.col("abroad")).select("Número departamento", "Provincia", "Municipio").unique()
    for dep, province, town in wanted.iter_rows():
        folded = fold(town)
        found = codes.get((dep, folded)) or {
            code for (d, name), options in codes.items() if d == dep and folded in name for code in options
        }
        if len(found) != 1:
            raise ValueError(f"2019: {town} ({province}, department {dep}) matches {sorted(found)}")
        prov, sec = found.pop()
        matched.append((dep, province, town, prov, sec))
    return pl.DataFrame(matched, schema=["Número departamento", "Provincia", "Municipio", "prov", "sec"], orient="row")


@functools.cache
def rows(day: date) -> tuple[pl.DataFrame, list[str]]:
    """One election day's rows, one per polling table and ballot strip, with a stable table key."""
    d = BY_DAY[day]
    if d.item == "2019":
        frame, labels = _2019()
    elif d.item == "2020":
        frame, labels = _2020()
    elif d.item == "2021":
        frame, labels = _2021(d.url)
    else:
        frame, labels = _2025(d.url)
    frame = frame.with_columns(abroad=pl.col("pais") != HOME)
    if "mesa" not in frame.columns:
        frame = frame.with_columns(mesa=pl.concat_str("loc", "rec", "numero", separator=":"))
    duplicated = frame.select("mesa", "office").is_duplicated()
    if duplicated.any():
        raise ValueError(f"{day}: a polling table appears twice for one ballot strip")
    frame = frame.with_columns(
        municipio=pl.when(pl.col("abroad"))
        .then(None)
        .otherwise(pl.format("{}{}{}", *(pl.col(c).str.zfill(2) for c in ("dep", "prov", "sec")))),
        provincia=pl.when(pl.col("abroad"))
        .then(None)
        .otherwise(pl.format("{}{}", *(pl.col(c).str.zfill(2) for c in ("dep", "prov")))),
        departamento=pl.when(pl.col("abroad")).then(None).otherwise(pl.col("dep").str.zfill(2)),
    )
    return frame, labels


# Places


def _names(frames: list[pl.DataFrame], code: str, name: str) -> dict[str, str]:
    """The newest name each code carries."""
    found: dict[str, str] = {}
    for frame in frames:
        for c, text in frame.filter(~pl.col("abroad")).select(code, name).unique().sort(code).iter_rows():
            if c is not None and text:
                found[c] = text
    return found


def places() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    frames = [rows(d.day)[0] for d in DAYS]
    return (
        _names(frames, "departamento", "depn"),
        _names(frames, "provincia", "provn"),
        _names(frames, "municipio", "mun"),
    )


def districts() -> dict[tuple[str, str], tuple[str, set[str]]]:
    """Each single-member district number by map vintage and kind, with its department and its municipalities."""
    found: dict[tuple[str, str], tuple[str, set[str]]] = {}
    for d in DAYS:
        frame = rows(d.day)[0]
        for office, column in (("uninominal", "cu"), ("special", "ce")):
            here = frame.filter((pl.col("office") == office) & ~pl.col("abroad"))
            for number, dep, town in here.select(column, "departamento", "municipio").unique().iter_rows():
                k = (f"{office}:{vintage(d.day)}", str(int(number)))
                department, towns = found.setdefault(k, (dep, set()))
                if department != dep:
                    raise ValueError(f"{d.day}: {office} district {number} spans departments {department} and {dep}")
                towns.add(town)
    return found


SCHEMES = {
    "uninominal": ("bo.circunscripcion", "single-member district"),
    "special": ("bo.circunscripcion_especial", "special district"),
}


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bolivia's 9 departments, their provinces and municipalities, and the single-member and special deputy "
        "districts of the maps used from 2019 and from 2025, as the Órgano Electoral Plurinacional codes them in its count exports",
    )
    p.external |= {BOLIVIA, COUNTRY}
    for d in DAYS:
        p.uses(raw.cached(SOURCE, d.url))
    for code, rank, kind, noun in (
        ("bo.departamento", 1, "administrative", "department"),
        ("bo.provincia", 2, "administrative", "province"),
        ("bo.municipio", 3, "administrative", "municipality"),
        ("bo.circunscripcion", 2, "electoral_district", "single-member district"),
        ("bo.circunscripcion_especial", 2, "electoral_district", "special district"),
        ("bo.recinto", 4, "precinct", "polling place"),
        ("bo.mesa", 5, "precinct", "polling table"),
    ):
        p.add("scheme", code=code, polity_id=BOLIVIA, kind=kind, rank=rank, noun=noun, code_system="oep")

    def place(
        scheme: str, id: str, code: str, text: str, parent: str, since: date | None = None, until: date | None = None
    ) -> None:
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code, valid_from=since, valid_to=until)
        p.add("area_parent", area_id=id, parent_id=parent, valid_from=since, valid_to=until)
        p.name(id, text, "es", "official")

    departments, provinces, towns = places()
    for code, text in sorted(departments.items()):
        place("bo.departamento", area("bo.departamento", code), code, text, COUNTRY)
    for code, text in sorted(provinces.items()):
        place("bo.provincia", area("bo.provincia", code), code, text, area("bo.departamento", code[:2]))
    for code, text in sorted(towns.items()):
        place("bo.municipio", area("bo.municipio", code), code, text, area("bo.provincia", code[:4]))
    for (kind, number), (dep, _) in sorted(districts().items(), key=lambda item: (item[0][0], int(item[0][1]))):
        office, map_vintage = kind.split(":")
        scheme, _ = SCHEMES[office]
        since, until = (REDISTRICTED, None) if map_vintage == "2025" else (None, REDISTRICTED)
        word = "Uninominal" if office == "uninominal" else "Especial"
        place(
            scheme,
            circunscripcion(scheme, map_vintage, int(number)),
            number,
            f"Circunscripción {word} {number}",
            area("bo.departamento", dep),
            since,
            until,
        )
    for scheme, _ in SCHEMES.values():
        for map_vintage, since, until in (("2019", None, REDISTRICTED), ("2025", REDISTRICTED, None)):
            p.add(
                "boundary_set",
                id=key("boundary_set", scheme, map_vintage),
                scheme_code=scheme,
                vintage=map_vintage,
                valid_from=since,
                valid_to=until,
            )
    return p


# Organizations and the registry


def organization(sigla: str, dep: str | None) -> str:
    """Parties are keyed by acronym; an organization that stood in one department only is keyed with it."""
    return key("party", fold(sigla)) if dep is None else key("party", dep, fold(sigla))


def _contest_totals(day: date) -> pl.DataFrame:
    """Each organization's votes by contest, with its department, for the organizations a contest's ballot holds."""
    frame, labels = rows(day)
    contests = _contest_keys(frame)
    long = contests.select("contest", "departamento", *labels).unpivot(
        index=["contest", "departamento"], variable_name="sigla", value_name="votes"
    )
    return (
        long.filter(pl.col("votes").is_not_null())
        .group_by("contest", "sigla")
        .agg(pl.col("votes").sum(), departments=pl.col("departamento").unique())
    )


@functools.cache
def organizations() -> dict[tuple[date, str, str], tuple[str, str | None]]:
    """For each day, contest, and ballot acronym, the organization's id and the department it is scoped to."""
    national: set[str] = set()
    totals: dict[date, pl.DataFrame] = {}
    for d in DAYS:
        totals[d.day] = _contest_totals(d.day).filter(pl.col("votes") > 0)
        if d.item != "2021":
            national |= {fold(s) for s in totals[d.day]["sigla"]}
    local = pl.concat([totals[d.day] for d in DAYS if d.item == "2021"]).explode("departments")
    spread = local.group_by(pl.col("sigla").map_elements(fold, return_dtype=pl.String)).agg(
        pl.col("departments").drop_nulls().n_unique()
    )
    national |= {s for s, n in spread.iter_rows() if n > 1}
    found: dict[tuple[date, str, str], tuple[str, str | None]] = {}
    for day, frame in totals.items():
        for contest, sigla, _, departments in frame.iter_rows():
            scope = None if fold(sigla) in national else next(d for d in departments if d is not None)
            found[(day, contest, sigla)] = (organization(sigla, scope), scope)
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The sources, organizations, bodies, offices, and electoral systems of Bolivia's general elections of 2019, "
        "2020, and 2025 and subnational elections of 2021, from the Órgano Electoral Plurinacional's count exports",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {BOLIVIA}
    for item, info in ITEMS.items():
        p.add(
            "source",
            id=source_id(item),
            kind=info.kind,
            publisher="Órgano Electoral Plurinacional" if info.kind == "official_body" else "Internet Archive",
            title=info.title,
            url=f"{ARCHIVE}/details/{info.id}",
            license=LICENSE,
            license_url="https://www.oep.org.bo",
            attribution="Órgano Electoral Plurinacional (OEP), Bolivia",
            notes=info.notes,
        )
        for url, _ in FILES[item]:
            if url != SIREPRE:
                p.artifact(raw.cached(SOURCE, url), source_id(item))
    p.add(
        "source",
        id=SIREPRE_SOURCE,
        kind="official_body",
        publisher="Órgano Electoral Plurinacional",
        title="Sistema de Resultados Preliminares (SIREPRE), Elecciones Generales 2025: respuestas de su servicio",
        url=f"{ARCHIVE}/details/{ITEMS['2025'].id}",
        license=LICENSE,
        license_url="https://www.oep.org.bo",
        attribution="Órgano Electoral Plurinacional (OEP), Bolivia",
        notes="The responses of the preliminary results service for the first round of 2025, deposited in the "
        "Internet Archive. Used only for the names, acronyms, and colors of the organizations on the ballot.",
    )
    p.artifact(raw.cached(SOURCE, SIREPRE), SIREPRE_SOURCE)
    _bodies(p)
    names = {fold(row["sigla"]): row for row in sirepre()[0].values()}
    seen: set[str] = set()
    for (_, _, sigla), (id, scope) in sorted(organizations().items(), key=lambda item: (item[1][0], item[0][2])):
        if id in seen:
            continue
        seen.add(id)
        folded = fold(sigla)
        kind = "independent_group" if scope is not None or folded in INDIGENOUS else "party"
        p.add("party", id=id, kind=kind, polity_id=BOLIVIA)
        p.add(
            "external_id", entity_id=id, scheme="bo.oep.sigla", value=folded if scope is None else f"{scope}:{folded}"
        )
        full = names.get(folded)
        if full is not None and fold(full["nombre"]) != folded:
            p.name(id, " ".join(full["nombre"].split()), "es", "official")
            p.name(id, sigla, "es", "abbreviation", False)
        else:
            p.name(id, sigla, "es", "official")
        if full is not None and re.fullmatch(r"#[0-9A-Fa-f]{6}", full["color"]):
            p.add("party_color", party_id=id, color=full["color"].upper(), source_id=SIREPRE_SOURCE)
    return p


CHAMBER = key("body", "chamber")
PRESIDENCY = key("office", "president")
REGION = "0603"
"""The Gran Chaco region of Tarija, which is the province of Gran Chaco."""
SYSTEMS = {
    "two_round": (
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A candidate is elected with more than half of the valid votes, or with at least 40 percent of them and a "
            "lead of 10 percentage points over the runner-up; otherwise the two leading candidates go to a second "
            "round. The 40 percent alternative is not expressed in these rules."
        ),
    ),
    "fptp": ({"formula": "fptp"}, "The candidate with the most valid votes is elected."),
    "dhondt": (
        {"formula": "dhondt"},
        "Seats are allocated among the closed lists by the D'Hondt method.",
    ),
}


def system(name: str) -> str:
    return key("electoral_system", name)


def assembly(dep: str) -> str:
    return key("body", "assembly", dep)


def council(town: str) -> str:
    return key("body", "council", town)


def office(kind: str, code: str) -> str:
    return key("office", kind, code)


OFFICE_KINDS = {
    "governor": ("governor", "Gobernador(a) de {}", "Governor of {}"),
    "mayor": ("mayor", "Alcalde(sa) de {}", "Mayor of {}"),
    "subgovernor": ("other", "Subgobernador(a) de la provincia {}", "Sub-governor of {} province"),
    "corregidor": ("other", "Corregidor(a) de {}", "Corregidor of {}"),
    "regional_executive": ("other", "Ejecutivo(a) Regional del Gran Chaco", "Regional Executive of the Gran Chaco"),
    "development": ("other", "Ejecutivo(a) Seccional de Desarrollo de {}", "Sectional Development Executive of {}"),
}


def _district_codes() -> dict[str, set[str]]:
    """The district codes each 2021 family elects in, from the 2021 files."""
    found: dict[str, set[str]] = {}
    for d in DAYS:
        if d.item != "2021":
            continue
        contests = _contest_keys(rows(d.day)[0])
        for family, code in contests.select("family", "code").unique().iter_rows():
            found.setdefault(family, set()).add(code)
    return found


def _bodies(p: Package) -> None:
    departments, provinces, towns = places()
    p.add("body", id=CHAMBER, polity_id=BOLIVIA, kind="lower_house")
    p.name(CHAMBER, "Chamber of Deputies of Bolivia", "en", "official")
    p.name(CHAMBER, "Cámara de Diputados", "es", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=BOLIVIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Plurinational State of Bolivia", "en", "official")
    p.name(PRESIDENCY, "Presidente del Estado Plurinacional de Bolivia", "es", "native", False)
    for name, (rules, method) in SYSTEMS.items():
        p.add("electoral_system", id=system(name), polity_id=BOLIVIA, rules=rules, rules_version=1)
        p.add(
            "note", id=key("note", system(name)), entity_id=system(name), kind="methodology", body_md=method, lang="en"
        )
    codes = _district_codes()
    for dep in sorted({code[:2] for code in codes.get("population", set()) | codes.get("territory", set())}):
        p.add("body", id=assembly(dep), polity_id=BOLIVIA, kind="unicameral")
        p.name(assembly(dep), f"Asamblea Legislativa Departamental de {departments[dep]}", "es", "official")
    regional = key("body", "regional", REGION)
    p.add("body", id=regional, polity_id=BOLIVIA, kind="unicameral")
    p.name(regional, "Asamblea Regional del Gran Chaco", "es", "official")
    for town in sorted(codes.get("council", set())):
        p.add("body", id=council(town), polity_id=BOLIVIA, kind="council")
        p.name(council(town), f"Concejo Municipal de {towns[town]}", "es", "official")
    names = {"department": departments, "province": provinces, "municipality": towns}
    for family, (kind, spanish, english) in OFFICE_KINDS.items():
        for code in sorted(codes.get(family, set())):
            id = office(family, code)
            text = names[LEVEL[family]][code]
            p.add("office", id=id, polity_id=BOLIVIA, kind=kind)
            p.name(id, english.format(text), "en", "official")
            p.name(id, spanish.format(text), "es", "native", False)


# Contests


def _contest_keys(frame: pl.DataFrame) -> pl.DataFrame:
    """Each row's contest key, family, and district code."""
    code = (
        pl.when(pl.col("office") == "president")
        .then(pl.lit("BO"))
        .when(pl.col("office") == "uninominal")
        .then(pl.col("cu").cast(pl.Int64).cast(pl.String))
        .when(pl.col("office") == "special")
        .then(pl.col("ce").cast(pl.Int64).cast(pl.String))
        .when(pl.col("office").replace_strict(LEVEL, default=None) == "department")
        .then(pl.col("departamento"))
        .when(pl.col("office").replace_strict(LEVEL, default=None) == "province")
        .then(pl.col("provincia"))
        .otherwise(pl.col("municipio"))
    )
    return frame.with_columns(family=pl.col("office"), code=code).with_columns(
        contest=pl.concat_str("family", "code", separator=":")
    )


def _slug(level: str, code: str, name: str, departments: dict[str, str]) -> str:
    """A province's or municipality's slug carries its department's, since names repeat across departments."""
    if level in ("province", "municipality"):
        return f"{changeset.slug(departments[code[:2]])}-{changeset.slug(name)}"
    return changeset.slug(name)


def _district(family: str, code: str, day: date) -> str:
    level = LEVEL[family]
    if level == "country":
        return COUNTRY
    if level in SCHEMES:
        scheme, _ = SCHEMES[level]
        return circunscripcion(scheme, vintage(day), int(code))
    return area(AREA_SCHEMES[level], code)


AREA_SCHEMES = {"department": "bo.departamento", "province": "bo.provincia", "municipality": "bo.municipio"}


TITLES = {
    "president": ("Presidente y Vicepresidente", "presidente"),
    "uninominal": ("Diputado(a) por la Circunscripción Uninominal {code}", "diputados-uninominal-{code}"),
    "special": ("Diputado(a) por la Circunscripción Especial {code}", "diputados-especial-{code}"),
    "governor": ("Gobernador(a) de {name}", "gobernador-{slug}"),
    "population": ("Asambleístas por población de {name}", "asamblea-poblacion-{slug}"),
    "territory": ("Asambleísta por territorio de la provincia {name}", "asamblea-territorio-{slug}"),
    "mayor": ("Alcalde(sa) de {name}", "alcalde-{slug}"),
    "council": ("Concejales de {name}", "concejo-{slug}"),
    "subgovernor": ("Subgobernador(a) de la provincia {name}", "subgobernador-{slug}"),
    "corregidor": ("Corregidor(a) de {name}", "corregidor-{slug}"),
    "regional_executive": ("Ejecutivo(a) Regional del Gran Chaco", "ejecutivo-regional-gran-chaco"),
    "regional_population": ("Asambleístas regionales por población del Gran Chaco", "asamblea-regional-poblacion"),
    "regional_territory": ("Asambleísta regional por territorio de {name}", "asamblea-regional-territorio-{slug}"),
    "development": ("Ejecutivo(a) Seccional de Desarrollo de {name}", "ejecutivo-desarrollo-{slug}"),
}
SHAPES: dict[str, tuple[str, int | None, str, str | None]] = {
    "president": ("office", 1, "ticket", "two_round"),
    "uninominal": ("body_seats", 1, "candidate", "fptp"),
    "special": ("body_seats", 1, "candidate", "fptp"),
    "governor": ("office", 1, "candidate", "two_round"),
    "population": ("list_allocation", None, "list", "dhondt"),
    "territory": ("body_seats", None, "candidate", None),
    "mayor": ("office", 1, "candidate", "fptp"),
    "council": ("list_allocation", None, "list", "dhondt"),
    "subgovernor": ("office", 1, "candidate", "fptp"),
    "corregidor": ("office", 1, "candidate", "fptp"),
    "regional_executive": ("office", 1, "candidate", "fptp"),
    "regional_population": ("list_allocation", None, "list", "dhondt"),
    "regional_territory": ("body_seats", None, "candidate", None),
    "development": ("office", 1, "candidate", "fptp"),
}
"""Each family's contest kind, magnitude, option kind, and electoral system. Seat counts the files do not give are
left unknown."""


def _body(family: str, code: str) -> str | None:
    if family in ("uninominal", "special"):
        return CHAMBER
    if family in ("population", "territory"):
        return assembly(code[:2])
    if family in ("regional_population", "regional_territory"):
        return key("body", "regional", REGION)
    if family == "council":
        return council(code)
    return None


def _office(family: str, code: str) -> str | None:
    if family == "president":
        return PRESIDENCY
    if family in OFFICE_KINDS:
        return office(family, code)
    return None


METHOD = {
    "2019": (
        "Counted by the Órgano Electoral Plurinacional per polling table (mesa), as published in the export of its "
        "official count (cómputo) of 25 October 2019, when every table had been counted. Registered voters are the "
        "table's enabled voters (inscritos habilitados); valid votes are the votes for the organizations; null and "
        "blank votes are stored as invalid and blank ballots. The export prints no total of ballots cast. The "
        "election was annulled by Law 1266 of 24 November 2019, and these results never took effect."
    ),
    "2020": (
        "Counted by the Órgano Electoral Plurinacional per polling table (mesa), as published in the last export of "
        "its official count (cómputo), of 28 October 2020. Registered voters are the table's enabled voters "
        "(inscritos habilitados); valid votes are the votes for the organizations, ballots cast are the votes "
        "emitted, and null and blank votes are stored as invalid and blank ballots. Votes for organizations that "
        "withdrew are counted as null, so those organizations are not listed."
    ),
    "2021": (
        "Counted by the Órgano Electoral Plurinacional per polling table (mesa), from a compilation of the exports of "
        "its official count (cómputo) deposited in the Internet Archive. Registered voters are the table's enabled "
        "voters (inscritos habilitados). The compilation keeps each organization's votes but not the blank, null, or "
        "valid totals, so those are not stored. An organization is listed in a contest when the count gives it at "
        "least one vote there; an organization's empty cell is left out."
    ),
    "2025": (
        "Counted by the Órgano Electoral Plurinacional per polling table (mesa), as published in the export of its "
        "official count (cómputo). Registered voters are the table's enabled voters (inscritos habilitados); valid "
        "votes are the votes for the organizations, ballots cast are the votes emitted, and invalid ballots are all "
        "null votes, including votes for organizations that withdrew (nulo por declinación). Blank votes are stored "
        "as blank ballots. Organizations that withdrew are not listed."
    ),
}
DEPUTY_NOTE = (
    " The export names each single-member district's candidates only by the organization that nominated them, so the "
    "options carry the organization's acronym."
)
TERRITORY_NOTE = " The export does not say how many members the district elects."


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{d.day}" for d in DAYS)]


def parse() -> None:
    for name, report in compare().items():
        print(f"{name}: {report}", flush=True)
    geo().write()
    registry().write()
    for d in DAYS:
        election(d.day).write()


def emit() -> None:
    package.run("load", packages())


def compare() -> dict[str, dict[str, int]]:
    """National totals of each presidential count, the figures the authority published."""
    report: dict[str, dict[str, int]] = {}
    for d in DAYS:
        frame, labels = rows(d.day)
        president = frame.filter(pl.col("office") == "president")
        if president.is_empty():
            continue
        sums = president.select(*(pl.col(c).sum() for c in [*ACCOUNTING, *labels])).row(0, named=True)
        report[str(d.day)] = {k: int(v) for k, v in sums.items() if v}
    return report


def _units(p: Package, d: Day, frame: pl.DataFrame) -> pl.DataFrame:
    """Polling places and the polling tables in them, keyed by the day."""
    tables = frame.select(
        "mesa", "loc", "rec", "locn", "recn", "numero", "abroad", "municipio", "paisn", "depn", "mun"
    ).unique(subset="mesa", keep="first")
    if d.item == "2019":
        tables = tables.with_columns(
            place=pl.concat_str(
                pl.coalesce("municipio", "paisn"),
                "depn",
                pl.col("locn").map_elements(fold, return_dtype=pl.String),
                pl.col("recn").map_elements(fold, return_dtype=pl.String),
                separator=":",
                ignore_nulls=True,
            )
        )
    else:
        tables = tables.with_columns(place=pl.concat_str("loc", "rec", separator=":"))
    prefix = f"{d.day}:"
    tables = tables.with_columns(
        unit_id=pl.col("mesa").map_elements(lambda m: key("unit", "bo.mesa", d.day, m), return_dtype=pl.String),
        parent_id=pl.col("place").map_elements(lambda c: key("unit", "bo.recinto", d.day, c), return_dtype=pl.String),
        area_id=pl.col("municipio").map_elements(lambda c: area("bo.municipio", c), return_dtype=pl.String),
    )
    places = tables.unique(subset="place", keep="first").sort("place")
    relation = pl.when("abroad").then(pl.lit("unlocated")).otherwise(pl.lit("within"))
    kind = pl.when("abroad").then(pl.lit("abroad_mission")).otherwise(pl.lit("standard"))
    p.frame(
        "reporting_unit",
        places.select(
            id="parent_id",
            polity_id=pl.lit(BOLIVIA),
            scheme_code=pl.lit("bo.recinto"),
            code=pl.lit(prefix) + pl.col("place"),
            area_relation=relation,
            area_id="area_id",
            kind=kind,
        ),
    )
    p.frame(
        "reporting_unit",
        tables.select(
            id="unit_id",
            polity_id=pl.lit(BOLIVIA),
            scheme_code=pl.lit("bo.mesa"),
            code=pl.lit(prefix) + pl.col("mesa"),
            parent_id="parent_id",
            area_relation=relation,
            area_id="area_id",
            kind=kind,
        ),
    )
    for id, recinto, town, country in places.filter("abroad").select("parent_id", "recn", "mun", "paisn").iter_rows():
        p.name(id, f"{recinto}, {town}, {country}", "es", "official")
    return tables.select("mesa", "unit_id")


def _outcomes(day: date) -> dict[str, dict[str, str]]:
    """The declared outcome of each presidential and gubernatorial contest, by contest key and acronym.

    A contest that went to a second round advanced its two leaders; any other was won by its leader."""
    found: dict[str, dict[str, str]] = {}
    if BY_DAY[day].item == "2019":
        return found
    runoff = next((r for r in DAYS if r.first == day), None)
    later = set() if runoff is None else set(_contest_keys(rows(runoff.day)[0])["contest"])
    totals = _contest_totals(day).filter(pl.col("votes") > 0).sort("votes", descending=True)
    for contest, sigla, _, _ in totals.iter_rows():
        if not contest.startswith(("president:", "governor:")):
            continue
        ranked = found.setdefault(contest, {})
        if contest in later:
            ranked[sigla] = "advanced" if len(ranked) < 2 else "eliminated"
        else:
            ranked[sigla] = "lost" if ranked else "elected"
    return found


def election(day: date) -> Package:
    d = BY_DAY[day]
    frame, labels = rows(day)
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"] + ([f"{SOURCE}/{d.first}"] if d.first else [])
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Bolivia's {d.english.removeprefix(str(day.year) + ' Bolivian ')} of {day:%-d %B %Y} by polling table from the "
        "Órgano Electoral Plurinacional's official count",
        depends,
    )
    p.external |= {BOLIVIA, COUNTRY}
    p.uses(raw.cached(SOURCE, d.url))
    if d.url == R2025:
        p.uses(raw.cached(SOURCE, SIREPRE))
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=BOLIVIA,
        kind=d.kind,
        date_from=day,
        date_to=day,
        status="annulled" if d.item == "2019" else "held",
    )
    p.name(election_id, d.english, "en", "official")
    p.name(election_id, d.spanish, "es", "native", False)
    path = f"bo/{day}-{d.word}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    if d.item == "2019":
        p.add(
            "note",
            id=key("note", election_id, "annulled"),
            entity_id=election_id,
            kind="caveat",
            body_md="The Legislative Assembly annulled this election by Law 1266 of 24 November 2019, and new "
            "elections were held on 18 October 2020.",
            lang="en",
        )
    units = _units(p, d, frame)
    contests = _contest_keys(frame).join(units, on="mesa", how="left")
    if contests["unit_id"].null_count():
        raise ValueError(f"{day}: tables without a unit")
    departments, provinces, towns = places()
    names = {"department": departments, "province": provinces, "municipality": towns}
    owners = organizations()
    outcomes = _outcomes(day)
    method = METHOD[d.item]
    values: list[pl.DataFrame] = []
    tally: Counter[str] = Counter()
    for (contest_key, family, code), here in sorted(
        contests.group_by("contest", "family", "code"), key=lambda item: str(item[0])
    ):
        contest_key, family, code = str(contest_key), str(family), str(code)
        kind, magnitude, option_kind, rules = SHAPES[family]
        contest = key("contest", day, contest_key)
        level = LEVEL[family]
        name = names[level].get(code, "") if level in names else ""
        title, slug = TITLES[family]
        previous = key("contest", d.first, contest_key) if d.first else None
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind=kind,
            office_id=_office(family, code),
            body_id=_body(family, code),
            district_area_id=_district(family, code, day),
            magnitude=magnitude,
            round_number=2 if d.first else 1,
            previous_round_id=previous,
            electoral_system_id=system(rules) if rules else None,
            default_result_kind="counted",
        )
        p.name(contest, title.format(code=code, name=name), "es", "official")
        p.add(
            "slug",
            path=f"{path}/{slug.format(code=code, slug=_slug(level, code, name, departments))}",
            entity_id=contest,
            is_current=True,
        )
        result_set = key("result_set", day, contest_key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source_id(d.item),
            status="annulled" if d.item == "2019" else "active",
            is_default=True,
            completeness=1.0,
        )
        note = method + (DEPUTY_NOTE if family in ("uninominal", "special") else "")
        note += TERRITORY_NOTE if family in ("territory", "regional_territory") else ""
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        options: dict[str, str] = {}
        for position, sigla in enumerate(labels, start=1):
            owner = owners.get((day, contest_key, sigla))
            if owner is None:
                continue
            option = options[sigla] = key("option", day, contest_key, fold(sigla))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind=option_kind,
                party_id=owner[0],
                ballot_position=None if d.item == "2021" else position,
                ballot_label=sigla,
            )
            outcome = outcomes.get(contest_key, {}).get(sigla)
            if outcome is not None:
                p.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
        votes = (
            here.select("unit_id", *options)
            .rename(options)
            .unpivot(index="unit_id", variable_name="option_id", value_name="value")
            .filter(pl.col("value").is_not_null())
            .with_columns(measure=pl.lit(1, pl.Int16))
        )
        tally["empty cells"] += here.select(pl.sum_horizontal(pl.col(c).is_null() for c in options)).sum().item()
        accounting = pl.concat(
            [
                here.select("unit_id", value=pl.col(column), measure=pl.lit(MEASURES[column], pl.Int16))
                for column in ACCOUNTING
            ]
        ).filter(pl.col("value").is_not_null())
        frame_values = pl.concat(
            [votes, accounting.with_columns(option_id=pl.lit(None, pl.String)).select(votes.columns)]
        )
        values.append(frame_values.with_columns(result_set_id=pl.lit(result_set)))
        summed = here.filter(pl.col("valid").is_not_null() & (pl.sum_horizontal(*options) != pl.col("valid")))
        if summed.height:
            tally["valid differs"] += summed.height
            net = int(summed.select((pl.sum_horizontal(*options) - pl.col("valid")).sum()).item())
            p.add(
                "note",
                id=key("note", result_set, "valid"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"In {summed.height} polling table{'s' if summed.height > 1 else ''}, the export prints a "
                f"valid-vote total that differs from the sum of the organizations' votes (by {net:+,} votes in all). "
                "The figures are stored as printed.",
                lang="en",
            )
        over = here.filter(pl.coalesce("cast", "valid") > pl.col("registered")).height
        if over:
            tally["over roll"] += over
            p.add(
                "note",
                id=key("note", result_set, "over"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"In {over} polling table{'s' if over > 1 else ''}, more votes were counted than the table's "
                "enabled voters. The figures are stored as the count published them.",
                lang="en",
            )
    facts = pl.concat(values)
    p.frame(
        "result_value",
        facts.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0),
            measure="measure",
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value="value",
        ),
    )
    if tally["valid differs"]:
        p.accept(
            "accounting.votes_valid",
            f"the 2019 count export prints, in {tally['valid differs']} table counts, a valid-vote total that differs "
            "from the sum of the organizations' votes; stored as printed and noted on each result set",
        )
    if tally["over roll"]:
        p.accept(
            "accounting.turnout",
            f"{tally['over roll']} polling tables count more votes than their enabled voters; stored as the count "
            "published them and noted on each result set",
        )
    print(f"{day}: {facts.height:,} values, {dict(tally)}", flush=True)
    return p
