"""Peru's municipal recall consultations (consultas populares de revocatoria del mandato) from 1997 to 2025.

Any resident of a province or district may petition to recall its mayor and councilors (Law 26300). The Oficina
Nacional de Procesos Electorales holds the consultation, and the electors of the municipality answer yes or no for
each authority separately: one ballot question per authority. The Jurado Nacional de Elecciones' archive, Infogob,
lists every consultation by municipality with each authority's votes, the declared result, and the promoters' stated
grounds, for the thirteen consultation days from 1997 to 2025. ONPE publishes its count by polling table (mesa) on
the national open data portal for the consultations of 2004 to 2025, and by district for 2001.

Each package here holds one consultation day. Infogob's totals for each authority are the declared, default result
set. Where ONPE publishes the polling-table count, it is a second, finer result set of the same contest that records
how much of the declared valid vote it holds.
"""

import difflib
import functools
import html
import io
import json
import re
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import NamedTuple
from urllib.parse import quote
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "pe.revocatoria"
BASE = "https://infogob.jne.gob.pe"
TOKEN = "GAATxwlNrPU=Aw"
PERU_INFOGOB = "sPpbSaCqXnM=pa"
RECALL = "rWwQzpPNurI=wp"
NONE = "7x8igXWeaZ8=8X"
EMPTY = "HOvv9BJTsTA=vB"
ELECTIONS = f"{BASE}/Eleccion/ListarElecciones"
PROVINCES = f"{BASE}/Eleccion/ListarProvincias"
DISTRICTS = f"{BASE}/Eleccion/ListarDistritos"
RESULTS = f"{BASE}/Eleccion/RecuperarDatosCandidatosResultados"
PERU = str(uuid5(package.NAMESPACE, "polity\x1fPE"))
WORKERS = 6


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


# Infogob


def _body(params: str | None) -> dict[str, object]:
    return {"token": TOKEN} if params is None else {"istrParameters": params, "token": TOKEN}


def _post(url: str, params: str | None = None, *, suffix: str = ".json", fetch: bool = False) -> raw.Artifact:
    if fetch:
        return raw.fetch(SOURCE, url, suffix=suffix, body=_body(params))
    return raw.cached(SOURCE, url, _body(params))


def _data(artifact: raw.Artifact) -> object:
    payload = json.loads(artifact.path.read_bytes())
    if payload.get("Estado") != "success":
        raise ValueError(f"{artifact.url}: {payload.get('Mensaje')}")
    return payload["Data"]


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def ficha_url(election: str) -> str:
    return f"{BASE}/Eleccion/FichaEleccion/x_candidatos-y-resultados_{election.replace('/', '@')}"


def partial_url(election: str, flags: dict[str, str]) -> str:
    params = (
        flags["IdAmbito"]
        + flags["FgDistMultiple"]
        + flags["FgVotoPreferencial"]
        + flags["FgEleccionLista"]
        + election
        + "@"
        + flags["IdGrupoEleccion"]
    )
    return f"{BASE}/Eleccion/_CandidatosResultadosEleccion?istrParameters={quote(params, safe='')}"


class Election(NamedTuple):
    id: str
    name: str
    day: date
    scope: str
    group: str


def _day(text: str) -> date:
    d, m, y = text.split("/")
    return date(int(y), int(m), int(d))


def elections(fetch: bool = False) -> list[Election]:
    """Infogob's recall consultations, one per tier of government and round, newest first."""
    listed = _data(_post(ELECTIONS, RECALL, fetch=fetch))
    assert isinstance(listed, list)
    found = []
    for row in listed:
        if row["Value"] == NONE:
            continue
        election = str(row["Value"])
        url = ficha_url(election)
        page = (raw.fetch(SOURCE, url, suffix=".html") if fetch else raw.cached(SOURCE, url)).path.read_text(
            encoding="utf-8"
        )
        flags = {
            k: html.unescape(v)
            for k, v in re.findall(r'<input type="hidden" id="(\w+)"(?: name="\w+")? value="([^"]*)"', page)
        }
        if flags.get("IdEleccion") != election:
            raise ValueError(f"{url}: the page names election {flags.get('IdEleccion')}")
        match = re.search(r"Fecha del proceso (\d\d/\d\d/\d{4})", _text(page))
        if match is None:
            raise ValueError(f"{url}: no date")
        found.append(
            Election(
                election, flags["nombreEleccion"], _day(match.group(1)), flags["IdAmbito"], flags["IdGrupoEleccion"]
            )
        )
        FLAGS[election] = flags
    return sorted(found, key=lambda e: (e.day, e.name), reverse=True)


FLAGS: dict[str, dict[str, str]] = {}


def _options(page: str, select: str) -> list[tuple[str, str]]:
    match = re.search(rf'<select[^>]*id="{select}"[^>]*>(.*?)</select>', page, flags=re.DOTALL)
    if match is None:
        return []
    return [
        (html.unescape(value), _text(text))
        for value, text in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)', match.group(1))
        if html.unescape(value) != NONE
    ]


class Locality(NamedTuple):
    id: str
    region: str
    province: str
    district: str | None


def _list(url: str, params: str, fetch: bool, used: list[raw.Artifact] | None = None) -> list[tuple[str, str]]:
    artifact = _post(url, params, fetch=fetch)
    if used is not None:
        used.append(artifact)
    data = _data(artifact)
    assert isinstance(data, list)
    return [(str(r["Value"]), _text(str(r["Text"]))) for r in data if r["Value"] not in (NONE, EMPTY)]


def localities(election: Election, fetch: bool = False, used: list[raw.Artifact] | None = None) -> list[Locality]:
    """The municipalities a consultation was held in: provinces, or districts with their province and region.
    `used` collects the cached pages the list is read from."""
    flags = FLAGS[election.id]
    page_artifact = _post(partial_url(election.id, flags), suffix=".html", fetch=fetch)
    if used is not None:
        used.append(page_artifact)
    page = page_artifact.path.read_text(encoding="utf-8")
    found = []
    for region, region_name in _options(page, "IdRegion"):
        for province, province_name in _list(PROVINCES, f"{election.id}@{region}", fetch, used):
            if election.scope == "3":
                found.append(Locality(province, region_name, province_name, None))
                continue
            for district, district_name in _list(DISTRICTS, f"{election.id}@{province}", fetch, used):
                found.append(Locality(district, region_name, province_name, district_name))
    return found


def results(election: Election, locality: Locality, fetch: bool = False) -> dict[str, object]:
    data = _data(_post(RESULTS, f"{election.id}@{locality.id}@{election.group}", fetch=fetch))
    assert isinstance(data, dict)
    return data


# ONPE's open data

FILES = "https://www.datosabiertos.gob.pe/sites/default/files/"
PORTAL = "https://www.datosabiertos.gob.pe/dataset/"
MESA = "pe.mesa"


class Dataset(NamedTuple):
    day: date
    tier: str  # the tier of the authorities consulted: district or province
    file: str
    dictionary: str | None
    page: str
    levels: str  # what the rows hold: mesa, or district for the district counts of 2001


DATASETS = [
    Dataset(
        date(2001, 11, 25),
        "province",
        "Resultados%20por%20distrito%20de%20la%20Revocatoria_Provincial.csv",
        "Diccionario%20de%20resultados%20por%20distrito%20de%20la%20Revocatoria_Provincial.xlsx",
        "resultados-por-distrito-de-la-consulta-popular-de-revocatoria-del-mandato-de-autoridades",
        "district",
    ),
    Dataset(
        date(2001, 11, 25),
        "district",
        "Resultados%20por%20distrito%20de%20la%20Revocatoria_Distrital.csv",
        "Diccionario%20de%20resultados%20por%20distrito%20de%20la%20Revocatoria_Distrital.xlsx",
        "resultados-por-distrito-de-la-consulta-popular-de-revocatoria-del-mandato-de-autoridades-2",
        "district",
    ),
    Dataset(
        date(2004, 10, 17),
        "district",
        "Resultados%20por%20mesa%20de%20la%20Consulta%20Popular%20de%20Revocatoria%202004.csv",
        "Diccionario%20de%20Resultados%20por%20mesa%20de%20la%20Consulta%20Popular%20de%20Revocatoria%202004.xlsx",
        "resultados-por-mesa-de-la-revocatoria-2004-oficina-nacional-de-procesos-electorales-onpe",
        "mesa",
    ),
    Dataset(
        date(2005, 7, 3),
        "district",
        "Resultados%20por%20mesa%20de%20la%20Consulta%20Popular%20de%20Revocatoria%202005%20-%20Primera%20vuelta.csv",
        "Diccionario%20de%20resultados%20por%20mesa%20de%20la%20Consulta%20Popular%20de%20Revocatoria%202005%20-%20Primera%20vuelta.xlsx",
        "resultados-por-mesa-de-la-revocatoria-2005-primera-vuelta-oficina-nacional-de-procesos",
        "mesa",
    ),
    Dataset(
        date(2005, 10, 30),
        "district",
        "Resultados%20por%20mesa%20de%20la%20Consulta%20Popular%20de%20Revocatoria%202005%20-%20Segunda%20vuelta.csv",
        "Diccionario%20de%20resultados%20por%20mesa%20de%20la%20Consulta%20Popular%20de%20Revocatoria%202005%20-%20Segunda%20vuelta.xlsx",
        "resultados-por-mesa-de-la-revocatoria-2005-segunda-vuelta-oficina-nacional-de-procesos",
        "mesa",
    ),
    Dataset(
        date(2008, 12, 7),
        "district",
        "Resultado%20por%20mesas%20de%20la%20Revocatoria%202008.csv",
        "Diccionario%20de%20los%20resultados%20por%20mesa%20Revocatoria%202008.xlsx",
        "resultados-por-mesa-de-la-revocatoria-2008-oficina-nacional-de-procesos-electorales-onpe",
        "mesa",
    ),
    Dataset(
        date(2009, 11, 29),
        "district",
        "Resultados%20por%20mesa%20de%20la%20Revocatoria%202009.csv",
        "Diccionario%20de%20resultados%20por%20mesa%20Revocatoria%202009.xlsx",
        "resultados-por-mesa-de-la-revocatoria-2009-oficina-nacional-de-procesos-electorales-onpe",
        "mesa",
    ),
    Dataset(
        date(2012, 9, 30),
        "district",
        "Resultados%20por%20mesa%20CPR2012%20Distrital.csv",
        "Diccionario%20Consulta%20Popular%20de%20Revocatoria_Distrital%202012.xlsx",
        "resultados-por-mesa-de-la-consulta-popular-de-revocatoria-distrital-2012-oficina-nacional-de",
        "mesa",
    ),
    Dataset(
        date(2012, 9, 30),
        "province",
        "Resultados%20por%20mesa%20CPR2012%20Provincial.csv",
        "Diccionario%20Consulta%20Popular%20de%20Revocatorias_Provincial%202012.xlsx",
        "resultados-por-mesa-de-la-consulta-popular-de-revocatoria-provincial-2012-oficina-nacional",
        "mesa",
    ),
    Dataset(
        date(2013, 3, 17),
        "district",
        "Resultados%20por%20mesa%20CPR2013%20Distrital.csv",
        "Diccionario%20de%20Resultados%20por%20mesa%20Consulta%20Popular%20de%20Revocatoria%20del%20Mandato%20de%20Autoridades%20Municipales%20Distrital%202013.xlsx",
        "resultados-por-mesa-de-la-consulta-popular-de-revocatoria-del-mandato-de-autoridades",
        "mesa",
    ),
    Dataset(
        date(2013, 3, 17),
        "province",
        "Resultados%20por%20mesa%20CPR2013%20Provincial.zip",
        "Diccionario%20de%20Resultados%20por%20mesa%20Consulta%20Popular%20de%20Revocatoria%20del%20Mandato%20de%20Autoridades%20Municipales%20Provincial%202013.xlsx",
        "resultados-por-mesa-de-la-consulta-popular-de-revocatoria-del-mandato-de-autoridades-2",
        "mesa",
    ),
    Dataset(
        date(2021, 10, 10),
        "district",
        "Resultados%20por%20mesa%20Consulta%20Popular%20de%20Revocatorias%202021.csv",
        "Diccionario%20de%20Resultados%20por%20mesa%20Consulta%20Popular%20de%20Revocatorias%202021_0.xlsx",
        "resultados-por-mesa-de-la-consulta-popular-de-revocatorias-2021-oficina-nacional-de-procesos",
        "mesa",
    ),
    Dataset(
        date(2025, 6, 8),
        "district",
        "Resultados%20por%20mesa%20de%20la%20Consulta%20Popular%20de%20Revocatoria%202025%C2%A0.csv",
        "Diccionario%20de%20Resultados%20por%20mesa%20de%20la%20Consulta%20Popular%20de%20Revocatoria.xlsx",
        "resultados-por-mesa-de-la-consulta-popular-de-revocatoria-2025-oficina-nacional-de-procesos",
        "mesa",
    ),
]


def url(name: str) -> str:
    return FILES + name


def fetch_onpe() -> None:
    for dataset in DATASETS:
        for name in (dataset.file, dataset.dictionary):
            if name is not None:
                artifact = raw.fetch(SOURCE, url(name), suffix="." + name.rsplit(".", 1)[1])
                print(f"{dataset.day} {name}: {artifact.bytes:,} bytes", flush=True)


HDX = (
    "https://data.humdata.org/dataset/54fc7f4d-f4c0-4892-91f6-2fe7c1ecf363/resource/"
    "63647792-0951-40d2-a30e-4a0e60f7a176/download/per_admin_boundaries.geojson.zip"
)
HDX_PAGE = "https://data.humdata.org/dataset/cod-ab-per"


def fetch() -> None:
    raw.fetch(SOURCE, HDX, suffix=".zip")
    fetch_onpe()
    for election in elections(fetch=True):
        places = localities(election, fetch=True)
        with ThreadPoolExecutor(WORKERS) as pool:
            list(pool.map(lambda place, e=election: results(e, place, fetch=True), places))
        print(f"{election.day} {election.name}: {len(places)} municipalities", flush=True)


# The tables


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text).split())


def number(text: object) -> int | None:
    if text is None:
        return None
    digits = str(text).replace(",", "").strip()
    return int(digits) if digits not in ("", "-") else None


@functools.cache
def infogob() -> pl.DataFrame:
    """One row per authority consulted, from Infogob: the election, municipality, authority, and each count."""
    rows: list[dict[str, object]] = []
    for election in elections():
        for locality in localities(election):
            data = results(election, locality)
            general = data["DatosGenerales"]
            assert isinstance(general, dict)
            listed = data["Resultados"]
            assert isinstance(listed, list)
            for index, row in enumerate(listed, start=1):
                rows.append(
                    {
                        "election": election.id,
                        "name": election.name,
                        "day": election.day,
                        "tier": "province" if election.scope == "3" else "district",
                        "locality": locality.id,
                        "region": locality.region,
                        "province": locality.province,
                        "district": locality.district,
                        "index": index,
                        "electors": number(general.get("NuElectores")),
                        "emitted": number(general.get("NuVotosEmi")),
                        "office": str(row["TxCargoAut"]),
                        "authority": str(row["TxAutoridad"]),
                        "party": str(row["TxOrgPol"]),
                        "yes": number(row["NuVotosSi"]),
                        "no": number(row["NuVotosNo"]),
                        "blank": number(row["NuVotBlanco"]),
                        "null": number(row["NuVotNulos"]),
                        "total": number(row["NuTotalVotos"]),
                        "result": str(row["TxResultado"]),
                    }
                )
    return pl.DataFrame(rows, infer_schema_length=None)


def _csv(name: str) -> str:
    path = raw.cached(SOURCE, url(name)).path
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            data = archive.read(archive.namelist()[0])
    else:
        data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    return text.lstrip("﻿")


def _count(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars().cast(pl.Int64, strict=False)


@functools.cache
def onpe(dataset: Dataset) -> pl.DataFrame:
    """One row per table and authority (per district and authority for the district counts of 2001): the place names,
    the table, its state, the authority, and the counts."""
    text = _csv(dataset.file)
    if dataset.levels == "district":
        # ONPE's 2001 files print a summary by municipality and, for provinces, a detail by district.
        parts = re.split(r"\n(?=UBIGEO;)", text.replace("\r", ""))
        sections = [part for part in parts if part.startswith("UBIGEO;")]
        body = sections[-1]
        lines = [line for line in body.split("\n") if re.match(r"^\s*\d{6};", line) or line.startswith("UBIGEO;")]
        frame = pl.read_csv(io.StringIO("\n".join(lines)), separator=";", infer_schema=False)
        frame = frame.rename({c: c.strip() for c in frame.columns})
        return frame.select(
            pl.col("UBIGEO").str.strip_chars().alias("ubigeo"),
            pl.col("DEPARTAMENTO").str.strip_chars().alias("department"),
            pl.col("PROVINCIA").str.strip_chars().alias("province"),
            pl.col("DISTRITO").str.strip_chars().alias("district"),
            pl.col("UBIGEO").str.strip_chars().alias("mesa"),
            pl.lit("DISTRITO").alias("state"),
            pl.lit(None, pl.Int64).alias("position"),
            pl.col("AUTORIDAD EN CONSULTA").str.strip_chars().alias("authority"),
            _count("ELECTORES").alias("registered"),
            _count("VOTOS SI").alias("yes"),
            _count("VOTOS NO").alias("no"),
            _count("VOTOS BLANCOS").alias("blank"),
            _count("VOTOS NULOS").alias("null"),
            _count("VOTOS IMPUGNADOS").alias("objected"),
            _count("VOTOS TOTAL").alias("total"),
        )
    frame = pl.read_csv(io.StringIO(text), separator=";", infer_schema=False, truncate_ragged_lines=True)
    frame = frame.select([c for c in frame.columns if c.strip()]).rename(lambda c: c.strip())
    pick = lambda *names: next(c for c in names if c in frame.columns)
    return frame.select(
        pl.col("UBIGEO").str.strip_chars().str.zfill(6).alias("ubigeo"),
        pl.col("DEPARTAMENTO").str.strip_chars().alias("department"),
        pl.col("PROVINCIA").str.strip_chars().alias("province"),
        pl.col("DISTRITO").str.strip_chars().alias("district"),
        pl.col("MESA").str.strip_chars().str.zfill(6).alias("mesa"),
        pl.col("ESTADO_MESA").str.strip_chars().str.to_uppercase().alias("state"),
        _count("UBICACION_EN_CEDULA").alias("position"),
        pl.col("AUTORIDAD_EN_CONSULTA").str.strip_chars().alias("authority"),
        _count("ELECTORES_HABILES").alias("registered"),
        _count(pick("VOTOS_SI", "NVOTOS_SI")).alias("yes"),
        _count(pick("VOTOS_NO", "NVOTOS_NO")).alias("no"),
        _count(pick("VOTOS_BLANCOS", "VOTOS_BLANCO")).alias("blank"),
        _count("VOTOS_NULOS").alias("null"),
        _count("VOTOS_IMPUGNADOS").alias("objected"),
        pl.lit(None, pl.Int64).alias("total"),
    )


# Geography: OCHA's COD-AB boundaries of Peru, drawn from the Instituto Nacional de Estadística e Informática's
# 2020 district map, name the departments, provinces, and districts by INEI's ubigeo code.

DEPARTMENT, PROVINCE, DISTRICT = "pe.inei_departamento", "pe.inei_provincia", "pe.inei_distrito"
ALIASES = {
    "LIMA PROVINCIAS": "LIMA",
    "PROV CONST DEL CALLAO": "CALLAO",
    "PROVINCIA CONSTITUCIONAL DEL CALLAO": "CALLAO",
}


class Shape(NamedTuple):
    code: str
    name: str
    parent: str | None
    geometry: dict[str, object]


@functools.cache
def gazetteer() -> dict[int, list[Shape]]:
    found: dict[int, list[Shape]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, HDX).path) as archive:
        for level in (1, 2, 3):
            data = json.loads(archive.read(f"per_admin{level}.geojson"))
            found[level] = [
                Shape(
                    f["properties"][f"adm{level}_pcode"].removeprefix("PE"),
                    f["properties"][f"adm{level}_name"],
                    f["properties"][f"adm{level - 1}_pcode"].removeprefix("PE") if level > 1 else None,
                    f["geometry"],
                )
                for f in data["features"]
            ]
    return found


@functools.cache
def _index() -> tuple[dict[str, str], dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    shapes = gazetteer()
    departments = {fold(s.name): s.code for s in shapes[1]}
    provinces = {(str(s.parent), fold(s.name)): s.code for s in shapes[2]}
    districts = {(str(s.parent), fold(s.name)): s.code for s in shapes[3]}
    return departments, provinces, districts


def _near(name: str, options: list[str], cutoff: float = 0.8) -> str | None:
    close = difflib.get_close_matches(name, options, n=1, cutoff=cutoff)
    return close[0] if close else None


def locate(department: str, province: str, district: str | None) -> tuple[str, str | None, str | None]:
    """The INEI codes of a municipality by the names a source prints: its department, province, and district, each
    None where the name matches no unit of the 2020 map."""
    departments, provinces, districts = _index()
    d = ALIASES.get(fold(department), fold(department))
    dept = departments.get(d) or departments.get(_near(d, list(departments)) or "")
    if dept is None:
        return "", None, None
    names = [name for parent, name in provinces if parent == dept]
    p = ALIASES.get(fold(province), fold(province))
    prov = provinces.get((dept, p)) or provinces.get((dept, _near(p, names, 0.6) or ""))
    if prov is None:
        return dept, None, None
    if district is None:
        return dept, prov, None
    names = [name for parent, name in districts if parent == prov]
    n = fold(district)
    found = districts.get((prov, n)) or districts.get((prov, _near(n, names, 0.75) or ""))
    if found is None:
        # A district the 2020 map names in full, such as Pampas Grande for Infogob's Pampas.
        longer = [name for name in names if name.startswith(n + " ")]
        found = districts[(prov, longer[0])] if len(longer) == 1 else None
    return dept, prov, found


class Place(NamedTuple):
    scheme: str
    code: str
    name: str
    parent: str | None
    shaped: bool


MINOR = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "para", "por", "a", "al"}


def title(text: str) -> str:
    words = " ".join(text.split()).lower().split(" ")
    return " ".join(
        word if index and word in MINOR else word[:1].upper() + word[1:] for index, word in enumerate(words)
    )


def where(tier: str, department: str, province: str, district: str | None) -> tuple[str, str]:
    """The scheme and code of a municipality by the names a source prints."""
    _, prov, dist = locate(department, province, district if tier == "district" else None)
    if prov is None:
        raise ValueError(f"no province {department} / {province} on the 2020 map")
    if tier == "province":
        return PROVINCE, prov
    if dist is not None:
        return DISTRICT, dist
    assert district is not None
    return DISTRICT, f"{prov}-{changeset.slug(district)}"


def _units() -> list[tuple[str, str, str, str | None]]:
    """Every (tier, department, province, district) the sources name: Infogob's municipalities and the districts
    whose polling tables ONPE counts."""
    found: set[tuple[str, str, str, str | None]] = set()
    for tier, region, province, district in (
        infogob().select("tier", "region", "province", "district").unique().iter_rows()
    ):
        found.add((tier, region, province, district))
    for dataset in DATASETS:
        frame = onpe(dataset)
        for department, province, district in frame.select("department", "province", "district").unique().iter_rows():
            found.add(("district", department, province, district))
            if dataset.tier == "province":
                found.add(("province", department, province, None))
    return sorted(found, key=lambda t: (t[0], t[1], t[2], t[3] or ""))


@functools.cache
def places() -> dict[tuple[str, str], Place]:
    """Every department, province, and district the packages use, by scheme and INEI code. A district the 2020 map
    does not hold, because it was renamed or split since, takes a code of its province and its name."""
    shapes = gazetteer()
    names = {
        (scheme, s.code): Place(scheme, s.code, s.name, s.parent, True)
        for scheme, level in ((DEPARTMENT, 1), (PROVINCE, 2), (DISTRICT, 3))
        for s in shapes[level]
    }
    found: dict[tuple[str, str], Place] = {}

    def take(place: Place) -> None:
        found[(place.scheme, place.code)] = place
        if place.parent is not None:
            parent = DEPARTMENT if place.scheme == PROVINCE else PROVINCE
            take(names[(parent, place.parent)])

    for tier, department, province, district in _units():
        scheme, code = where(tier, department, province, district)
        if (scheme, code) in names:
            take(names[(scheme, code)])
        else:
            assert district is not None
            parent = locate(department, province, None)[1]
            assert parent is not None
            take(Place(scheme, code, title(district), parent, False))
    return found


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


INFOGOB_SOURCE = key("source", "infogob")
ONPE_SOURCE = key("source", "onpe")
SHAPE_SOURCE = key("source", "inei-cod-ab")
SYSTEM = key("electoral_system", "recall")
VINTAGE = date(2020, 7, 14)


def packages() -> list[str]:
    days = sorted({d for d in infogob()["day"].unique().to_list()})
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{d}" for d in days)]


def parse() -> None:
    geo().write()
    registry().write()
    for d in sorted(infogob()["day"].unique().to_list()):
        election_day(d).write()


def emit() -> None:
    package.run("load", packages())


def _outline(geometry: dict[str, object]) -> tuple[str, str]:
    shape = shapely.geometry.shape(geometry)
    shape = shapely.make_valid(shape.simplify(0.0008, preserve_topology=True))
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    polygons = [q for part in parts for q in shapely.get_parts(part)]
    wgs = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    label = shapely.set_srid(shapely.point_on_surface(wgs), 4326)
    return shapely.to_wkb(wgs, hex=True, include_srid=True), shapely.to_wkb(label, hex=True, include_srid=True)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The departments, provinces, and districts of Peru where municipal recall consultations were held, with the "
        "boundaries of the Instituto Nacional de Estadística e Informática's 2020 map",
    )
    p.external.add(PERU)
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="official_body",
        publisher="Instituto Nacional de Estadística e Informática (INEI), distributed by the United Nations Office "
        "for the Coordination of Humanitarian Affairs",
        title="Peru - Subnational Administrative Boundaries (COD-AB)",
        url=HDX_PAGE,
        license="Creative Commons Attribution for Intergovernmental Organisations (CC BY-IGO)",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="Instituto Nacional de Estadística e Informática (INEI); OCHA, Common Operational Datasets",
    )
    p.artifact(raw.cached(SOURCE, HDX), SHAPE_SOURCE)
    for code, rank, noun in ((DEPARTMENT, 3, "department"), (PROVINCE, 4, "province"), (DISTRICT, 5, "district")):
        p.add(
            "scheme", code=code, polity_id=PERU, kind="administrative", rank=rank, noun=noun, code_system="inei_ubigeo"
        )
        p.add("boundary_set", id=key("boundary_set", code, 2020), scheme_code=code, vintage="2020", as_of=VINTAGE)
    shapes = {
        (scheme, s.code): s
        for scheme, level in ((DEPARTMENT, 1), (PROVINCE, 2), (DISTRICT, 3))
        for s in gazetteer()[level]
    }
    for (scheme, code), place in sorted(places().items()):
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add(
            "area_kind",
            area_id=id,
            kind=scheme.removeprefix("pe.inei_"),
            source_id=SHAPE_SOURCE if place.shaped else None,
        )
        if place.parent is not None:
            parent = DEPARTMENT if scheme == PROVINCE else PROVINCE
            p.add("area_parent", area_id=id, parent_id=area(parent, place.parent))
        p.name(id, place.name, "es", "official")
        if place.shaped:
            geom, label = _outline(shapes[(scheme, code)].geometry)
            p.add(
                "area_geometry",
                area_id=id,
                geom=geom,
                label_point=label,
                quality="official",
                source_id=SHAPE_SOURCE,
            )
    return p


METHOD = (
    "Totals as published by the Jurado Nacional de Elecciones in Infogob, its election archive, for each authority "
    "consulted: the votes for yes (to recall) and no, blank and null votes, the electors of the municipality, and the "
    "declared result. The consultation is held by the Oficina Nacional de Procesos Electorales."
)
FINER = (
    "The Oficina Nacional de Procesos Electorales' count by polling table, published on the national open data "
    "portal. It is a finer result set of the same question and not the default: it records how much of the declared "
    "valid vote it holds. Each table's registered electors are those of the table, and the ballots cast are the sum of "
    "its yes, no, blank, null, and objected votes."
)
BY_DISTRICT = (
    "The Oficina Nacional de Procesos Electorales' count by district for the provincial consultation of 2001, "
    "published on the national open data portal. It is a finer result set of the same question and not the default: "
    "it records how much of the declared valid vote it holds."
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The sources and the electoral system of Peru's municipal recall consultations",
    )
    p.external.add(PERU)
    p.add(
        "source",
        id=INFOGOB_SOURCE,
        kind="official_body",
        publisher="Jurado Nacional de Elecciones",
        title="Infogob – Observatorio para la Gobernabilidad",
        url=BASE,
        license="Official public record, no license stated",
        license_url=BASE,
        attribution="Jurado Nacional de Elecciones, Infogob",
    )
    p.add(
        "source",
        id=ONPE_SOURCE,
        kind="official_body",
        publisher="Oficina Nacional de Procesos Electorales",
        title="Resultados por mesa de la Consulta Popular de Revocatoria (Plataforma Nacional de Datos Abiertos)",
        url="https://www.datosabiertos.gob.pe/",
        license="Open Data Commons Attribution License",
        license_url="https://opendatacommons.org/licenses/by/1-0/",
        attribution="Oficina Nacional de Procesos Electorales (ONPE), Plataforma Nacional de Datos Abiertos",
    )
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=PERU,
        valid_from=date(1997, 1, 1),
        valid_to=None,
        rules={"formula": "referendum", "majority": {"share": 0.5}},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=RULE, lang="en")
    return p


RULE = (
    "Each authority of a municipality is put to a separate yes-or-no question: whether to recall them. The electors "
    "of the province or district answer all the questions of their ballot. The result of each question is stored as "
    "the Jurado Nacional de Elecciones declared it, revoked or not revoked. A declared result does not always follow "
    "a simple majority of the yes and no votes: the law also required the yes votes to reach a share of the "
    "municipality's electors, so the engine's majority rule is only a comparison and never replaces the declared "
    "result."
)
OFFICES = {
    "ALCALDE DISTRITAL": ("district mayor", "alcalde distrital"),
    "REGIDOR DISTRITAL": ("district councilor", "regidor distrital"),
    "ALCALDE PROVINCIAL": ("provincial mayor", "alcalde provincial"),
    "REGIDOR PROVINCIAL": ("provincial councilor", "regidor provincial"),
}
MONTHS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]
REVOKED = {"REVOCADO": True, "NO REVOCADO": False, "ANULADO": None}


def _tokens(text: str) -> str:
    return " ".join(sorted(fold(text).split()))


def pair(declared: list[str], counted: list[str], where_: str) -> dict[str, int]:
    """Match the authorities ONPE names to Infogob's, by their names in any order: ONPE's index into `declared`."""
    folded = [_tokens(name) for name in declared]
    found: dict[str, int] = {}
    for name in counted:
        token = _tokens(name)
        if token in folded:
            found[name] = folded.index(token)
            continue
        close = difflib.get_close_matches(token, folded, n=1, cutoff=0.85)
        if not close:
            mine = set(token.split())
            inside = [f for f in folded if mine <= set(f.split()) or set(f.split()) <= mine]
            close = inside if len(inside) == 1 else []
        if not close:
            raise ValueError(f"{where_}: no authority of Infogob matches {name!r} among {declared}")
        found[name] = folded.index(close[0])
    if len(set(found.values())) != len(found):
        raise ValueError(f"{where_}: two names match one authority: {found}")
    return found


def _unit_row(when: date, scheme: str, code: str) -> dict[str, object]:
    return {
        "id": key("unit", when, scheme, code),
        "polity_id": PERU,
        "scheme_code": scheme,
        "code": f"{when}:{code}",
        "area_relation": "equals",
        "area_id": area(scheme, code),
        "kind": "standard",
    }


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
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


def _cite(p: Package, artifacts: list[raw.Artifact], source: str, seen: set[str] | None = None) -> None:
    seen = set() if seen is None else seen
    for artifact in artifacts:
        if artifact.storage_key not in seen:
            seen.add(artifact.storage_key)
            p.artifact(artifact, source)


def election_day(when: date) -> Package:
    here = infogob().filter(pl.col("day") == when).sort("election", "region", "province", "district", "index")
    p = Package(
        f"{SOURCE}/{when}",
        SOURCE,
        f"Peru's municipal recall consultation of {when}, one question for each authority consulted, from the "
        "Jurado Nacional de Elecciones' Infogob archive and the Oficina Nacional de Procesos Electorales' counts",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(PERU)
    election = key("election", when)
    path = f"pe/{when}-recall"
    p.add("election", id=election, polity_id=PERU, kind="recall", date_from=when, date_to=when, status="held")
    p.name(election, f"{when.year} Peruvian municipal recall consultation ({when:%B} {when.day})", "en", "official")
    p.name(
        election,
        f"Consulta Popular de Revocatoria del Mandato de Autoridades Municipales del {when.day} de "
        f"{MONTHS[when.month - 1]} de {when.year}",
        "es",
        "native",
        False,
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    names = sorted({str(n) for n in here["name"].unique().to_list()})
    p.add(
        "note",
        id=key("note", election, "processes"),
        entity_id=election,
        kind="context",
        body_md="Infogob lists this day as: " + "; ".join(title(n) for n in names) + ".",
        lang="es",
    )
    cited: list[raw.Artifact] = [raw.cached(SOURCE, ELECTIONS, _body(RECALL))]
    for e in elections():
        if e.day == when:
            cited.append(raw.cached(SOURCE, ficha_url(e.id)))
            for locality in localities(e, used=cited):
                cited.append(_post(RESULTS, f"{e.id}@{locality.id}@{e.group}"))
    _cite(p, cited, INFOGOB_SOURCE)
    units: set[str] = set()
    contests: dict[tuple[str, str, str], list[tuple[int, str, str, str, str]]] = {}
    valid: dict[str, int] = {}
    used_paths: set[str] = set()
    for row in here.iter_rows(named=True):
        tier = str(row["tier"])
        scheme, code = where(tier, str(row["region"]), str(row["province"]), row["district"])
        place = places()[(scheme, code)]
        unit = key("unit", when, scheme, code)
        if unit not in units:
            units.add(unit)
            p.add("reporting_unit", **_unit_row(when, scheme, code))
        office_en, office_es = OFFICES[str(row["office"])]
        person = title(str(row["authority"]))
        contest = key("contest", row["election"], row["locality"], row["index"])
        yes, no = key("option", contest, "yes"), key("option", contest, "no")
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=area(scheme, code),
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="recall_question")
        p.name(contest, f"Recall of {person}, {office_en} of {place.name}", "en", "official")
        p.name(contest, f"Revocatoria de {person}, {office_es} de {place.name}", "es", "native", False)
        parents = []
        node: Place | None = place
        while node is not None and node.scheme != DEPARTMENT:
            parents.append(node)
            node = places()[(PROVINCE if node.scheme == DISTRICT else DEPARTMENT, node.parent)] if node.parent else None
        if node is not None:
            parents.append(node)
        trail = "/".join(changeset.slug(q.name) for q in reversed(parents))
        slug = f"{path}/{trail}/{changeset.slug(person)}"
        while slug in used_paths:
            slug += "-2"
        used_paths.add(slug)
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        p.add("contest_option", id=yes, contest_id=contest, kind="answer", ballot_position=1, ballot_label="Sí")
        p.add("contest_option", id=no, contest_id=contest, kind="answer", ballot_position=2, ballot_label="No")
        p.add(
            "note",
            id=key("note", contest, "authority"),
            entity_id=contest,
            kind="context",
            body_md=f"Authority consulted: {person}, {office_en}, elected for {title(str(row['party']))}.",
            lang="en",
        )
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=INFOGOB_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        _value(p, result_set, unit, None, 2, row["electors"])
        _value(p, result_set, unit, None, 3, row["total"])
        _value(p, result_set, unit, yes, 1, row["yes"])
        _value(p, result_set, unit, no, 1, row["no"])
        _value(p, result_set, unit, None, 6, row["blank"])
        _value(p, result_set, unit, None, 5, row["null"])
        revoked = REVOKED[str(row["result"])]
        if revoked is None:
            p.add(
                "note",
                id=key("note", result_set, "annulled"),
                entity_id=result_set,
                kind="caveat",
                body_md="Infogob lists the result of this consultation as annulled (anulado); no outcome is stored.",
                lang="en",
            )
        else:
            p.add("outcome", result_set_id=result_set, option_id=yes, kind="won" if revoked else "lost")
            p.add("outcome", result_set_id=result_set, option_id=no, kind="lost" if revoked else "won")
        contests.setdefault((tier, scheme, code), []).append(
            (int(row["index"]), str(row["authority"]), contest, yes, no)
        )
        valid[contest] = (row["yes"] or 0) + (row["no"] or 0)
    _finer(p, when, contests, valid, units, set())
    return p


SPECIAL = {"blank": 6, "null": 5, "objected": 15}


def _finer(
    p: Package,
    when: date,
    contests: dict[tuple[str, str, str], list[tuple[int, str, str, str, str]]],
    valid: dict[str, int],
    units: set[str],
    cited: set[str],
) -> None:
    """ONPE's count by polling table (or by district, for 2001) as a second result set of each contest it covers."""
    for dataset in DATASETS:
        if dataset.day != when or (dataset.levels == "district" and dataset.tier == "district"):
            continue
        _cite(
            p,
            [raw.cached(SOURCE, url(name)) for name in (dataset.file, dataset.dictionary) if name],
            ONPE_SOURCE,
            cited,
        )
        frame = onpe(dataset)
        scheme = PROVINCE if dataset.tier == "province" else DISTRICT
        rows = []
        for department, province, district in frame.select("department", "province", "district").unique().iter_rows():
            unit_code = where("district", department, province, district)[1]
            muni = where(dataset.tier, department, province, district)[1]
            rows.append((department, province, district, unit_code, muni))
        frame = frame.join(
            pl.DataFrame(rows, schema=["department", "province", "district", "unit_code", "muni"], orient="row"),
            on=["department", "province", "district"],
        )
        links = []
        for muni in frame["muni"].unique().to_list():
            members = sorted(contests.get((dataset.tier, scheme, str(muni)), []))
            if not members:
                raise ValueError(f"{dataset.file}: Infogob has no consultation in {muni}")
            counted = frame.filter(pl.col("muni") == muni)["authority"].unique().to_list()
            matched = pair([m[1] for m in members], [str(c) for c in counted], f"{dataset.file} {muni}")
            for name, index in matched.items():
                _, _, contest, yes, no = members[index]
                links.append((muni, name, contest, yes, no, key("result_set", contest, "onpe")))
        frame = frame.join(
            pl.DataFrame(links, schema=["muni", "authority", "contest", "yes_id", "no_id", "set"], orient="row"),
            on=["muni", "authority"],
        )
        if dataset.levels == "mesa":
            p.external.add(MESA)
            unit_of = frame.select("mesa", "unit_code").unique()
            if unit_of["mesa"].n_unique() != unit_of.height:
                raise ValueError(f"{dataset.file}: a table is in two districts")
            frame = frame.with_columns(
                pl.col("mesa")
                .map_elements(lambda m: key("unit", when, "mesa", m), return_dtype=pl.String)
                .alias("unit")
            )
            fresh = unit_of.filter(
                ~pl.col("mesa")
                .map_elements(lambda m: key("unit", when, "mesa", m), return_dtype=pl.String)
                .is_in(list(units))
            )
            p.frame(
                "reporting_unit",
                pl.DataFrame(
                    [
                        {
                            "id": key("unit", when, "mesa", mesa),
                            "polity_id": PERU,
                            "scheme_code": MESA,
                            "code": f"{when}:revocatoria:{mesa}",
                            "area_relation": "within",
                            "area_id": area(DISTRICT, code),
                            "kind": "standard",
                        }
                        for mesa, code in fresh.iter_rows()
                    ],
                    schema_overrides={"id": pl.String},
                )
                if fresh.height
                else pl.DataFrame(),
            )
            units |= {key("unit", when, "mesa", m) for m in fresh["mesa"].to_list()}
        else:
            frame = frame.with_columns(
                pl.col("unit_code")
                .map_elements(lambda c: key("unit", when, DISTRICT, c), return_dtype=pl.String)
                .alias("unit")
            )
            for code in frame["unit_code"].unique().to_list():
                if key("unit", when, DISTRICT, code) not in units:
                    units.add(key("unit", when, DISTRICT, code))
                    p.add("reporting_unit", **_unit_row(when, DISTRICT, str(code)))
        installed = ~pl.col("state").str.contains("NO INSTALADA")
        counted = frame.filter(installed)
        parts = [
            frame.filter(pl.col("registered").is_not_null()).select(
                pl.col("set"),
                "unit",
                pl.lit(None, pl.String).alias("option_id"),
                pl.lit(2).alias("measure"),
                pl.col("registered").alias("value"),
            ),
            counted.select(
                "set",
                "unit",
                pl.col("yes_id").alias("option_id"),
                pl.lit(1).alias("measure"),
                pl.col("yes").alias("value"),
            ),
            counted.select(
                "set",
                "unit",
                pl.col("no_id").alias("option_id"),
                pl.lit(1).alias("measure"),
                pl.col("no").alias("value"),
            ),
            *(
                counted.select(
                    "set",
                    "unit",
                    pl.lit(None, pl.String).alias("option_id"),
                    pl.lit(code).alias("measure"),
                    pl.col(column).alias("value"),
                )
                for column, code in SPECIAL.items()
            ),
            counted.select(
                "set",
                "unit",
                pl.lit(None, pl.String).alias("option_id"),
                pl.lit(3).alias("measure"),
                pl.sum_horizontal("yes", "no", "blank", "null", "objected").alias("value"),
            ),
        ]
        values = pl.concat(parts, how="vertical_relaxed").filter(pl.col("value").is_not_null())
        p.frame(
            "result_value",
            values.select(
                pl.col("set").alias("result_set_id"),
                pl.col("unit").alias("unit_id"),
                "option_id",
                pl.lit(0).alias("channel"),
                "measure",
                pl.lit(0).alias("count_round"),
                pl.lit(0).alias("flags"),
                "value",
            ),
        )
        tally = dict(values.filter(pl.col("measure") == 1).group_by("set").agg(pl.col("value").sum()).iter_rows())
        for contest, result_set in frame.select("contest", "set").unique().iter_rows():
            got = int(tally.get(result_set, 0))
            declared = valid[contest]
            p.add(
                "result_set",
                id=result_set,
                election_id=key("election", when),
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=ONPE_SOURCE,
                status="active",
                is_default=False,
                completeness=round(got / declared, 6) if declared else None,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=BY_DISTRICT if dataset.levels == "district" else FINER,
                lang="en",
            )
            p.add(
                "citation",
                id=key("citation", result_set),
                entity_id=result_set,
                source_id=ONPE_SOURCE,
                locator={"dataset": PORTAL + dataset.page, "file": url(dataset.file)},
            )
            if got != declared:
                p.add(
                    "note",
                    id=key("note", result_set, "total"),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=(
                        f"The count adds up to {got:,} yes and no votes, {abs(got - declared):,} "
                        f"{'more' if got > declared else 'fewer'} than the {declared:,} of the declared result."
                    ),
                    lang="en",
                )
