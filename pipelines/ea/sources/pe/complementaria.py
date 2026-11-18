"""Peru's municipal by-elections from 1981 to 2025: complementary, partial, and new municipal elections, from Infogob and the ONPE polling-table files.

Infogob, the Jurado Nacional de Elecciones' election archive, publishes each complementary, partial, and new municipal
election's lists, their votes, and the elected mayor and councilors for every province and district that voted,
back to 1981. Its JSON endpoints are the ones its own pages call, with a fixed page token in the body, and every
request is a POST cached by its body. The Oficina Nacional de Procesos Electorales publishes its count by polling
table for 2003 to 2025; each file is stored as a second, finer result set of the contests it covers. Boundaries are
the Instituto Geográfico Nacional's, as distributed by OCHA (CC BY 3.0 IGO).
"""

import json
import re
import sys
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from time import sleep
from typing import NamedTuple
from uuid import uuid4

import httpx

from ea import raw
from ea.sources.pe import jne

SOURCE = "pe.complementaria"
BASE = jne.BASE
CLASSES = {"WIHkyMA8HI8=HM": "complementary", "rqThaZb/DOg=TZ": "new"}
SHAPES = "pe.ign"
HDX = "https://data.humdata.org/dataset/54fc7f4d-f4c0-4892-91f6-2fe7c1ecf363/resource/63647792-0951-40d2-a30e-4a0e60f7a176/download/per_admin_boundaries.geojson.zip"
DISTRICTS = f"{BASE}/Eleccion/ListarDistritos"


FILES = "https://www.datosabiertos.gob.pe/sites/default/files/"
PORTAL = "https://www.datosabiertos.gob.pe/dataset/"


class Dataset(NamedTuple):
    day: date
    kind: str
    file: str
    dictionary: str | None
    page: str


def _ds(day: str, kind: str, file: str, dictionary: str | None, page: str) -> Dataset:
    y, m, d = day.split("-")
    return Dataset(date(int(y), int(m), int(d)), kind, file, dictionary, page)


_COMPLEMENTARIAS = "resultados-por-mesa-de-las-elecciones-municipales-complementarias"
_NUEVAS = "resultados-por-mesa-de-las-nuevas-elecciones-municipales"
_DIC = "Diccionario%20de%20los%20resultados%20por%20mesa%20de%20las%20"

DATASETS = [
    _ds(
        "2003-07-06",
        "district",
        "Resultados%20por%20mesa%20%20de%20las%20EMC2003%20Distrital.csv",
        _DIC + "EMC2003%20Distrital.xlsx",
        "resultados-por-mesa-de-las-complementarias-2003-oficina-nacional-de-procesos-electorales",
    ),
    _ds(
        "2005-07-03",
        "district",
        "Resultados%20por%20mesa%20de%20las%20Elecciones%20Municipales%20Complementarias%202005.csv",
        _DIC + "Elecciones%20Municipales%20Complementarias%202005.xlsx",
        "resultados-por-mesa-de-las-complementarias-2005-oficina-nacional-de-procesos-electorales",
    ),
    _ds(
        "2006-08-13",
        "district",
        "Resultados%20por%20mesa%20de%20las%20Nuevas%20Elecciones%20Municipales%202006_0.csv",
        "Diccionario%20de%20resultados%20de%20las%20Nuevas%20Elecciones%20Municipales%202006.xlsx",
        "resultados-por-mesa-de-las-nuevas-municipales-2006-oficina-nacional-de-procesos-electorales",
    ),
    _ds(
        "2007-07-01",
        "district",
        "Resultados%20por%20mesa%20EMC2007.csv",
        "Diccionario%20de%20los%20resultados%20por%20mesa%20EMC2007.xlsx",
        "resultados-por-mesa-de-las-complementarias-2007-oficina-nacional-de-procesos-electorales",
    ),
    _ds(
        "2009-11-29",
        "provincial",
        "Resultados%20por%20mesa%20NEM2009%20Provincial.csv",
        "Diccionario%20de%20resultados%20de%20las%20Nuevas%20Elecciones%20Municipal%20Provincial%202009.xlsx",
        _NUEVAS + "-provincial-2009-oficina-nacional-de",
    ),
    _ds(
        "2009-11-29",
        "district",
        "Resultados%20por%20mesa%20NEM2009%20Distrital.csv",
        "Diccionario%20de%20resultados%20de%20las%20Nuevas%20Elecciones%20Municipal%20Distrital%202009.xlsx",
        _NUEVAS + "-distrital-2009-oficina-nacional-de",
    ),
    _ds(
        "2010-06-06",
        "district",
        "Resultados%20por%20mesa%20de%20las%20Nuevas%20Elecciones%20Municipales%202010.csv",
        "Diccionario%20de%20resultados%20de%20las%20Nuevas%20Elecciones%20Municipal%202010.xlsx",
        _NUEVAS + "-2010-oficina-nacional-de-procesos",
    ),
    _ds(
        "2011-07-03",
        "district",
        "Resultados%20por%20mesa%20de%20las%20EMC2011.csv",
        _DIC + "EMC2011.xlsx",
        _COMPLEMENTARIAS + "-2011-oficina-nacional-de",
    ),
    _ds(
        "2012-07-01",
        "district",
        "Resultados%20por%20mesa%20EMC2012.csv",
        "Diccionario%20de%20las%20Elecciones%20Municipales%20Complementarias%202012.xlsx",
        _COMPLEMENTARIAS + "-2012-oficina-nacional-de",
    ),
    _ds(
        "2013-07-07",
        "provincial",
        "NEM2013_JULIO_Municipal_Provincial.csv",
        None,
        _NUEVAS + "-provinciales-2013-oficina-nacional",
    ),
    _ds(
        "2013-07-07",
        "district",
        "NEM2013_JULIO_Municipal_Distrital.csv",
        "Diccionario%20de%20resultados%20de%20las%20Nuevas%20Elecciones_Municipal_%20Distrital_NEM2013.xlsx",
        _NUEVAS + "-distritales-2013-oficina-nacional",
    ),
    _ds(
        "2013-11-24",
        "provincial",
        "NEM2013_Lima_Municipal_Provincial.zip",
        "Diccionario%20de%20los%20%20resultados%20por%20mesa%20de%20las%20Nuevas%20Eleccion%20Municipal%20Lima%202013.xlsx",
        _NUEVAS + "-lima-2013-oficina-nacional-de",
    ),
    _ds(
        "2014-03-16",
        "district",
        "Resultados%20por%20mesa%20de%20las%20Nuevas%20Elecciones%20Municipales%202014_Distrital.csv",
        "Diccionario%20de%20los%20resultados%20por%20mesa%20de%20las%20Nuevas%20Elecciones%20Municipales%202014.xlsx",
        _NUEVAS + "-2014-oficina-nacional-de-procesos",
    ),
    _ds(
        "2015-07-05",
        "provincial",
        "EMC2015_Municipal_Provincial.csv",
        _DIC + "EMC2015_%20Municipal_Provincial%202015.xlsx",
        _COMPLEMENTARIAS + "-provincial-2015-oficina",
    ),
    _ds(
        "2015-07-05",
        "district",
        "EMC2015_Municipal_Distrital.csv",
        _DIC + "EMC2015_Municipal_Distrital.xlsx",
        _COMPLEMENTARIAS + "-distritales-2015-oficina",
    ),
    _ds(
        "2019-07-07",
        "district",
        "Resultados%20por%20mesa%20de%20las%20Elecciones%20Municipales%20Complementarias%202019_0.csv",
        _DIC + "Elecciones%20Municipales%20Complementarias%202019.xlsx",
        _COMPLEMENTARIAS + "-2019-oficina-nacional-de",
    ),
    _ds(
        "2021-10-10",
        "district",
        "EMC2021_Municipal_Distrital.csv",
        "Diccionario_Elecciones_Distrital_EMC2021.xlsx",
        _COMPLEMENTARIAS + "-2021-oficina-nacional-de",
    ),
    _ds(
        "2023-07-02",
        "district",
        "EMC2023_Municipal_Distrital.csv",
        "Diccionario_Elecciones_Distrital_EMC2023.xlsx",
        _COMPLEMENTARIAS + "-2023-oficina-nacional-de",
    ),
    _ds(
        "2024-05-26",
        "district",
        "Resultados%20por%20mesa%20de%20las%20Elecciones%20Municipales%20Complementarias%202024.csv",
        _DIC + "Elecciones%20Municipales%20Complementarias%202024.xlsx",
        _COMPLEMENTARIAS + "-2024-oficina-nacional-de",
    ),
    _ds(
        "2025-09-28",
        "district",
        "Resultados%20por%20mesa%20de%20las%20Elecciones%20Municipales%20Complementarias%202025.csv",
        "Diccionario%20de%20los%20resultados%20por%20mesa%20%20de%20las%20Elecciones%20Municipales%20Complementarias%202025.xlsx",
        _COMPLEMENTARIAS + "-2025-oficina-nacional-de",
    ),
]


def _body(params: str | None) -> dict[str, object]:
    return {"token": jne.TOKEN} if params is None else {"istrParameters": params, "token": jne.TOKEN}


def cached(url: str, params: str | None = None) -> raw.Artifact:
    return raw.cached(SOURCE, url, _body(params))


def post_many(items: Iterable[tuple[str, str | None]], *, suffix: str = ".json", workers: int = 12) -> None:
    """POST many (url, parameters) requests into the cache, writing the index once per batch."""
    folder = raw.DATA / "raw" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    index = raw._read(SOURCE)
    pending = [(url, params) for url, params in dict.fromkeys(items) if raw._key(url, _body(params)) not in index]
    client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=120)

    def one(item: tuple[str, str | None]) -> tuple[str, dict[str, str | int | None]] | None:
        url, params = item
        body = _body(params)
        for attempt in range(5):
            try:
                response = client.post(url, json=body)
                response.raise_for_status()
                if suffix == ".json" and json.loads(response.content).get("Estado") != "success":
                    raise httpx.HTTPError("not a success")
            except httpx.HTTPError, ValueError:
                sleep(2**attempt)
                continue
            content = response.content
            digest = raw.hashlib.sha256(content).hexdigest()
            name = f"{digest}{suffix}"
            if not (folder / name).exists():
                partial = folder / f".{uuid4()}.part"
                partial.write_bytes(content)
                partial.replace(folder / name)
            return raw._key(url, body), {
                "sha256": digest,
                "file": name,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "media_type": response.headers.get("content-type"),
                "bytes": len(content),
                "body": json.dumps(body, sort_keys=True),
            }
        print(f"{SOURCE}: {url} {params} could not be fetched", file=sys.stderr, flush=True)
        return None

    with client, ThreadPoolExecutor(workers) as pool:
        for start in range(0, len(pending), 1000):
            done = [hit for hit in pool.map(one, pending[start : start + 1000]) if hit is not None]
            with raw.locked(SOURCE) as current:
                for key, entry in done:
                    current[key] = entry
            for key, entry in done:
                raw._push(SOURCE, key, entry)
            print(f"{SOURCE}: {min(start + 1000, len(pending))}/{len(pending)} requests", flush=True)


def data(url: str, params: str | None = None) -> object:
    payload = json.loads(cached(url, params).path.read_bytes())
    if payload.get("Estado") != "success":
        raise ValueError(f"{url} {params}: {payload.get('Mensaje')}")
    return payload["Data"]


class Election(NamedTuple):
    id: str
    kind: str  # "provincial" or "district"
    name: str
    day: date
    flags: dict[str, str]


def _fields(text: str) -> dict[str, str]:
    return {
        key: value
        for key, value in re.findall(r'<input type="hidden" id="(\w+)"(?: name="\w+")? value="([^"]*)"', text)
    }


def _day(text: str) -> date:
    d, m, y = text.split("/")
    return date(int(y), int(m), int(d))


def catalog() -> list[tuple[str, str, str]]:
    found = []
    for code in CLASSES:
        listed = data(jne.ELECTIONS, code)
        assert isinstance(listed, list)
        for row in listed:
            if row["Value"] != jne.NONE:
                text = str(row["Text"])
                found.append((str(row["Value"]), "provincial" if "PROVINCIAL" in text else "district", text))
    return found


def elections() -> list[Election]:
    found = []
    for id, kind, _ in catalog():
        page = raw.cached(SOURCE, jne.ficha_url(id)).path.read_text(encoding="utf-8")
        flags = {k: v.replace("&amp;", "&") for k, v in _fields(page).items()}
        when = re.search(r'Fecha del proceso</span><span class="control">([^<]*)', page)
        if flags.get("IdEleccion") != id or when is None:
            raise ValueError(f"{id}: the page does not match the election")
        found.append(Election(id, kind, flags["nombreEleccion"], _day(when.group(1)), flags))
    return sorted(found, key=lambda e: (e.day, e.kind))


def regions(e: Election) -> list[tuple[str, str]]:
    page = cached(jne.partial_url(e.id, e.flags)).path.read_text(encoding="utf-8")
    return jne.options(page, "IdRegion")


def _rows(url: str, params: str) -> list[tuple[str, str]]:
    found = data(url, params)
    assert isinstance(found, list)
    return [(str(r["Value"]), jne._text(str(r["Text"]))) for r in found if r["Value"] not in (jne.NONE, jne.EMPTY)]


def provinces(e: Election, region: str) -> list[tuple[str, str]]:
    return _rows(jne.PROVINCES, f"{e.id}@{region}")


def districts(e: Election, province: str) -> list[tuple[str, str]]:
    return _rows(DISTRICTS, f"{e.id}@{province}")


def partial(e: Election) -> str:
    return jne.partial_url(e.id, e.flags)


def fetch() -> None:
    post_many([(jne.ELECTIONS, code) for code in CLASSES])
    for id, _, _ in catalog():
        raw.fetch(SOURCE, jne.ficha_url(id), suffix=".html")
    found = elections()
    post_many([(partial(e), None) for e in found], suffix=".html")
    post_many([(jne.PROVINCES, f"{e.id}@{r}") for e in found for r, _ in regions(e)])
    post_many(
        [
            (DISTRICTS, f"{e.id}@{p}")
            for e in found
            if e.kind == "district"
            for r, _ in regions(e)
            for p, _ in provinces(e, r)
        ]
    )
    post_many(
        [(jne.RESULTS, f"{e.id}@{loc}@{e.flags['IdGrupoEleccion']}") for e in found for loc, _, _ in localities(e)]
    )
    for d in DATASETS:
        raw.fetch(SOURCE, FILES + d.file, suffix=_suffix(d.file))
        if d.dictionary:
            raw.fetch(SOURCE, FILES + d.dictionary, suffix=".xlsx")
    raw.fetch(SHAPES, HDX, suffix=".zip")


def _suffix(file: str) -> str:
    return "." + file.rsplit(".", 1)[1]


class Locality(NamedTuple):
    id: str
    name: str
    region: tuple[str, str]
    province: tuple[str, str] | None


def localities(e: Election) -> list[tuple[str, str, Locality]]:
    """Every locality that holds the election: (id, name, place), the province or the district."""
    found = []
    for region in regions(e):
        for province in provinces(e, region[0]):
            if e.kind == "provincial":
                found.append((province[0], province[1], Locality(province[0], province[1], region, None)))
            else:
                for district in districts(e, province[0]):
                    found.append((district[0], district[1], Locality(district[0], district[1], region, province)))
    return found


# Geography

import difflib
import functools
import io
import unicodedata
import zipfile
from collections import Counter
from uuid import uuid5

import shapely
from shapely.geometry import shape

from ea import changeset, package
from ea.package import Package

PERU = str(uuid5(package.NAMESPACE, "polity\x1fPE"))
DEPARTMENT, PROVINCE, DISTRICT, MESA = "pe.ign_departamento", "pe.ign_provincia", "pe.ign_distrito", "pe.mesa"
BOUNDARY_DAY = date(2020, 7, 14)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


INFOGOB = key("source", "infogob")
ONPE = key("source", "onpe")
IGN = key("source", "ign")


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("Ñ", "N").replace("ñ", "n"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


class Unit(NamedTuple):
    code: str
    name: str
    parent: str | None
    shape: shapely.Geometry


@functools.cache
def shapes() -> dict[str, dict[str, Unit]]:
    """The IGN boundaries of departments, provinces, and districts by ubigeo."""
    path = raw.cached(SHAPES, HDX).path
    out: dict[str, dict[str, Unit]] = {}
    with zipfile.ZipFile(path) as archive:
        for level, scheme, tolerance in ((1, DEPARTMENT, 0.002), (2, PROVINCE, 0.001), (3, DISTRICT, 0.0005)):
            collection = json.loads(archive.read(f"per_admin{level}.geojson"))
            units = {}
            for feature in collection["features"]:
                props = feature["properties"]
                code = str(props[f"adm{level}_pcode"]).removeprefix("PE")
                parent = code[:-2] if level > 1 else None
                geometry = shapely.make_valid(shape(feature["geometry"])).simplify(tolerance, preserve_topology=True)
                units[code] = Unit(code, str(props[f"adm{level}_name"]), parent, geometry)
            out[scheme] = units
    return out


def outline(geometry: shapely.Geometry) -> tuple[str, str]:
    parts = [p for p in shapely.get_parts(shapely.make_valid(geometry)) if shapely.get_type_id(p) in (3, 6)]
    wgs = shapely.set_srid(shapely.MultiPolygon([q for p in parts for q in shapely.get_parts(p)]), 4326)
    point = shapely.set_srid(shapely.point_on_surface(wgs), 4326)
    return shapely.to_wkb(wgs, hex=True, include_srid=True), shapely.to_wkb(point, hex=True, include_srid=True)


class Contest(NamedTuple):
    election: Election
    place: Locality
    data: dict[str, object]


@functools.cache
def elections_cached() -> list[Election]:
    return elections()


@functools.cache
def contests() -> list[Contest]:
    found = []
    for e in elections_cached():
        seen: set[str] = set()
        for loc, _, place in localities(e):
            if loc in seen:
                continue
            seen.add(loc)
            data = jne_results(e, loc)
            general = data.get("DatosGenerales") or {}
            assert isinstance(general, dict)
            if not data.get("Resultados") and not data.get("Electos"):
                continue
            found.append(Contest(e, place, data))
    return found


def jne_results(e: Election, loc: str) -> dict[str, object]:
    found = data(jne.RESULTS, f"{e.id}@{loc}@{e.flags['IdGrupoEleccion']}")
    assert isinstance(found, dict)
    return found


# Infogob spells some places differently from the IGN.
ALIASES: dict[tuple[str, ...], str] = {
    ("ica", "nazca"): "Nasca",
    ("ancash", "antonio raimondi"): "Antonio Raymondi",
    ("ancash", "san luis"): "Carlos Fermin Fitzcarrald",
    ("la libertad", "huamachuco"): "Sanchez Carrion",
}

DISTRICT_ALIASES = {
    "san francisco de yeso": "san francisco del yeso",
    "milpucc": "milpuc",
    "antonio raimondi": "antonio raymondi",
    "huayllo": "ihuayllo",
    "huaillati": "huayllati",
    "mariscal gamarra": "gamarra",
    "santa rita de sihuas": "santa rita de siguas",
    "san jose de uchua": "san jose de ushua",
    "huallay grande": "huayllay grande",
    "santiago de chocorvoz": "santiago de chocorvos",
    "quisqui": "quisqui kichki",
    "san juan de yscos": "san juan de iscos",
    "tres de diciiembre": "tres de diciembre",
    "mazamari": "mazamari pangoa",
    "tnte manuel clavero": "teniente manuel clavero",
    "capaso": "capazo",
    "caspizapa": "caspisapa",
    "elias soplin": "elias soplin vargas",
    "lacroja": "locroja",
}


class Resolved(NamedTuple):
    scheme: str
    code: str
    known: bool


@functools.cache
def _index() -> tuple[dict[str, str], dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    found = shapes()
    departments = {fold(u.name): c for c, u in found[DEPARTMENT].items()}
    provinces = {(u.parent or "", fold(u.name)): c for c, u in found[PROVINCE].items()}
    districts = {(u.parent or "", fold(u.name)): c for c, u in found[DISTRICT].items()}
    return departments, provinces, districts


@functools.cache
def _by_department() -> dict[tuple[str, str], list[str]]:
    found: dict[tuple[str, str], list[str]] = {}
    for code, unit in shapes()[DISTRICT].items():
        found.setdefault((code[:2], fold(unit.name)), []).append(code)
    return found


def resolve(place: Locality) -> dict[str, Resolved]:
    """The department, province, and district (when the locality is one) an Infogob locality names."""
    departments, provinces, districts = _index()
    region = fold(place.region[1])
    out: dict[str, Resolved] = {}
    d = departments.get(region)
    out[DEPARTMENT] = Resolved(DEPARTMENT, d or f"x:{region}", d is not None)
    province_name = place.province[1] if place.province else place.name
    p = provinces.get((d or "", fold(ALIASES.get((region, fold(province_name)), province_name))))
    out[PROVINCE] = Resolved(PROVINCE, p or f"x:{region}:{fold(province_name)}", p is not None)
    if place.province:
        k = districts.get((p or "", fold(place.name))) if p else None
        name = DISTRICT_ALIASES.get(fold(place.name), fold(place.name))
        if k is None and p:
            k = districts.get((p, name))
        if k is None and d is not None:
            # A district that moved between provinces since the election: take the department's only district of
            # that name.
            same = _by_department().get((d, name), [])
            if len(same) == 1:
                k = same[0]
        out[DISTRICT] = Resolved(DISTRICT, k or f"x:{region}:{fold(province_name)}:{fold(place.name)}", k is not None)
    return out


def report() -> None:
    missing: Counter[tuple[str, ...]] = Counter()
    for c in contests():
        for scheme, r in resolve(c.place).items():
            if not r.known:
                missing[(scheme, c.place.region[1], c.place.province[1] if c.place.province else "", c.place.name)] += 1
    for k, n in sorted(missing.items()):
        print(n, k)
    print(len(missing), "unmatched")


def title(text: str) -> str:
    return jne.title(text)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Peru's departments, provinces, and districts with the Instituto Geográfico Nacional's 2020 boundaries, and "
        "the places Infogob names in municipal elections that the boundaries lack",
    )
    p.external.add(PERU)
    p.add(
        "source",
        id=IGN,
        kind="official_body",
        publisher="Instituto Geográfico Nacional (Peru), distributed by OCHA ROLAC",
        title="Perú: límites administrativos (departamentos, provincias y distritos), versión 2020",
        url="https://data.humdata.org/dataset/cod-ab-per",
        license="Creative Commons Attribution 3.0 IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="Instituto Geográfico Nacional (IGN), OCHA ROLAC",
    )
    p.artifact(raw.cached(SHAPES, HDX), IGN)
    for code, rank, noun in ((DEPARTMENT, 3, "department"), (PROVINCE, 4, "province"), (DISTRICT, 5, "district")):
        p.add("scheme", code=code, polity_id=PERU, kind="administrative", rank=rank, noun=noun)
        p.add("boundary_set", id=key("boundary_set", code, 2020), scheme_code=code, vintage="2020", as_of=BOUNDARY_DAY)
    for scheme, units in shapes().items():
        for code, unit in sorted(units.items()):
            id = area(scheme, code)
            p.add("area", id=id, scheme_code=scheme)
            p.add("area_code", area_id=id, code=code)
            if unit.parent is not None:
                p.add("area_parent", area_id=id, parent_id=area(PARENT[scheme], unit.parent))
            p.name(id, unit.name, "es", "official")
            geom, label = outline(unit.shape)
            p.add("area_geometry", area_id=id, geom=geom, label_point=label, quality="official", source_id=IGN)
    unknown: dict[tuple[str, str], tuple[str, str, str | None]] = {}
    for c in contests():
        found = resolve(c.place)
        names = {
            DEPARTMENT: c.place.region[1],
            PROVINCE: (c.place.province or (c.place.id, c.place.name))[1],
            DISTRICT: c.place.name,
        }
        for scheme, r in found.items():
            if not r.known:
                parent = found[PARENT[scheme]].code if scheme in PARENT else None
                unknown.setdefault((scheme, r.code), (names[scheme], PARENT.get(scheme, ""), parent))
    for (scheme, code), (name, parent_scheme, parent) in sorted(unknown.items()):
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        if parent is not None:
            p.add("area_parent", area_id=id, parent_id=area(parent_scheme, parent))
        p.name(id, title(name), "es", "official")
    return p


PARENT = {PROVINCE: DEPARTMENT, DISTRICT: PROVINCE}


# Registry

BODIES = {
    "provincial": ("council-provincial", "Provincial municipal councils", "Concejos municipales provinciales"),
    "district": ("council-district", "District municipal councils", "Concejos municipales distritales"),
}
LAW = (
    "Municipal elections held between the regular rounds. Complementary elections (elecciones municipales "
    "complementarias, and partial ones in 1996) fill a municipality where the regular vote did not take place or was "
    "annulled; new elections (nuevas elecciones municipales) repeat a vote where null and blank votes exceeded "
    "two-thirds of the votes cast or the Jurado Nacional de Elecciones annulled the result. Each elects a mayor and "
    "council for the rest of the term. From 1998 the rules of Ley 26864, the Ley de Elecciones Municipales of 1997, "
    "apply: a list is headed by its candidate for mayor and carries the candidates for councilor (regidor). The list "
    "with the most votes wins the mayoralty, and at least half plus one of the councilor seats go to it, so that it "
    "holds the majority of the council. The remaining seats are shared among the other lists by D'Hondt's highest "
    "averages."
)
SYSTEM = key("electoral_system", "municipal-1998")
RULES = {"formula": "dhondt", "bonus": {"level": "list", "size": {"kind": "target", "fraction": 0.5, "rounding": "up"}}}
SPECIAL = jne.SPECIAL
METHOD = (
    "Totals as published by the Jurado Nacional de Elecciones in Infogob, its election archive, for each province or "
    "district. Registered voters are the electoral roll (Infogob prints zero for the early elections, which is stored "
    "as missing), ballots cast are the votes cast (votos emitidos), and blank and null votes are stored as blank and "
    "invalid ballots. The council's seats are the mayor and the councilors Infogob lists as elected. Where the "
    "Oficina Nacional de Procesos Electorales publishes its counts by polling table or district, they are stored as a "
    "second, finer result set of the same contest."
)


def party(org: str) -> str:
    return key("party", org)


def org_of(row: dict[str, object]) -> str:
    return jne.org_of(row)


def person(path: str) -> str:
    return key("person", jne.tail(path, "_historial-partidario_"))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Peru's municipal councils, the municipal electoral system of Ley 26864, and the lists and elected "
        "authorities of the complementary, partial, and new municipal elections of 1981 to 2025 from Infogob",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(PERU)
    p.add(
        "source",
        id=INFOGOB,
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
        id=ONPE,
        kind="official_body",
        publisher="Oficina Nacional de Procesos Electorales",
        title="Resultados por mesa y por distrito de las elecciones municipales (Plataforma Nacional de Datos Abiertos)",
        url="https://www.datosabiertos.gob.pe/",
        license="Open Data Commons Attribution License",
        license_url="https://opendatacommons.org/licenses/by/1-0/",
        attribution="Oficina Nacional de Procesos Electorales (ONPE), Plataforma Nacional de Datos Abiertos",
    )
    for code, english, spanish in BODIES.values():
        id = key("body", code)
        p.add("body", id=id, polity_id=PERU, kind="council")
        p.name(id, english, "en", "official")
        p.name(id, spanish, "es", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=PERU,
        valid_from=date(1997, 10, 14),
        valid_to=None,
        rules=RULES,
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=LAW, lang="en")
    names: dict[str, list[tuple[date, str]]] = {}
    people: dict[str, list[tuple[date, str]]] = {}
    for c in contests():
        when = c.election.day
        for row in rows_of(c):
            org = row.get("IdOrgPol")
            if org and org not in SPECIAL:
                names.setdefault(str(org), []).append((when, " ".join(str(row["TxOrgPol"]).split())))
        for row in electos_of(c):
            names.setdefault(org_of(row), []).append((when, " ".join(str(row["TxOrgPol"]).split())))
            people.setdefault(person(str(row["TxRutaPolitico"])), []).append(
                (when, " ".join(str(row["TxCandidato"]).split()))
            )
    for org, seen in sorted(names.items()):
        id = party(org)
        p.add("party", id=id, kind="party", polity_id=PERU)
        p.add("external_id", entity_id=id, scheme="pe.municipal.orgpol", value=org)
        latest = max(seen)[1]
        p.name(id, latest, "es", "official")
        for text in sorted({text for _, text in seen} - {latest}):
            p.name(id, text, "es", "official", False)
    for id, seen in sorted(people.items()):
        p.add("person", id=id)
        p.name(id, title(max(seen)[1]), "es", "official")
    return p


def rows_of(c: Contest) -> list[dict[str, object]]:
    found = c.data.get("Resultados") or []
    assert isinstance(found, list)
    return found


def electos_of(c: Contest) -> list[dict[str, object]]:
    found = c.data.get("Electos") or []
    assert isinstance(found, list)
    return found


# Election days

ANNULLED = re.compile(r"ANULAD")


def days() -> list[date]:
    return sorted({e.day for e in elections_cached()})


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{d}" for d in days())]


def parse() -> None:
    geo().write()
    registry().write()
    for d in days():
        election_day(d).write()


def emit() -> None:
    package.run("load", packages())


def election_kind(when: date) -> str:
    return "by_election"


def process(e: Election) -> str:
    name = e.name.upper()
    return "new" if "NUEVAS" in name else "partial" if "PARCIALES" in name else "complementary"


def english_name(when: date) -> str:
    kinds = sorted({process(c.election) for c in contests() if c.election.day == when})
    noun = {
        "complementary": "complementary municipal elections",
        "new": "new municipal elections",
        "partial": "partial municipal elections",
    }
    text = " and ".join(noun[k] for k in kinds)
    same_year = [d for d in days() if d.year == when.year]
    if len(same_year) > 1:
        return f"{when:%B %Y} Peruvian {text}"
    return f"{when.year} Peruvian {text}"


def number(text: object) -> int | None:
    return jne.number(text)


def _value(p: Package, rs: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=rs,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


class Day(Package):
    """A package that cites each raw file once."""

    def artifact(self, artifact: raw.Artifact, source_id: str) -> str:
        id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{source_id}\x1f{artifact.storage_key}"))
        if id not in self.cited:
            self.cited.add(id)
            return super().artifact(artifact, source_id)
        return id

    cited: set[str]


def election_day(when: date) -> Package:
    here = [c for c in contests() if c.election.day == when]
    p = Day(
        f"{SOURCE}/{when}",
        SOURCE,
        f"Peru's complementary, partial, and new municipal elections of {when} by province and district, from the "
        "Jurado Nacional de Elecciones' Infogob archive",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.cited = set()
    p.external |= {PERU, MESA}
    for e in {c.election.id: c.election for c in here}.values():
        p.artifact(raw.cached(SOURCE, jne.ficha_url(e.id)), INFOGOB)
        p.artifact(cached(partial(e)), INFOGOB)
    election = key("election", when)
    path = f"pe/{when}-municipal"
    p.add(
        "election",
        id=election,
        polity_id=PERU,
        kind=election_kind(when),
        date_from=when,
        date_to=when,
        date_precision="day",
        status="held",
    )
    p.name(election, english_name(when), "en", "official")
    spanish = sorted(
        {title(e.flags["nombreEleccion"].split(" - ")[0]) for e in {c.election.id: c.election for c in here}.values()}
    )
    for index, text in enumerate(spanish):
        p.name(election, text, "es", "native", index == 0)
    p.add("slug", path=path, entity_id=election, is_current=True)
    units: set[str] = set()
    mismatched: Counter[str] = Counter()
    over: Counter[str] = Counter()
    slugs: Counter[str] = Counter()
    for c in sorted(
        here, key=lambda c: (c.election.kind, c.place.region[1], c.place.province or ("", ""), c.place.name)
    ):
        p.artifact(cached(jne.RESULTS, f"{c.election.id}@{c.place.id}@{c.election.flags['IdGrupoEleccion']}"), INFOGOB)
        _contest(p, c, when, election, path, units, mismatched, over, slugs)
    onpe_day(p, when, here, election)
    for result_set in mismatched:
        p.add(
            "note",
            id=key("note", result_set, "sum"),
            entity_id=result_set,
            kind="caveat",
            body_md="Infogob's votes for the lists plus blank and null votes do not add up to the votes cast it "
            "prints for this contest. The figures are stored as printed.",
            lang="en",
        )
    if over:
        p.accept(
            "accounting.turnout",
            f"Infogob prints more votes cast than registered voters in {over.total()} municipalities; stored as printed",
        )
    return p


def _unit(p: Package, c: Contest, when: date, units: set[str]) -> str:
    r = resolve(c.place)[PROVINCE if c.election.kind == "provincial" else DISTRICT]
    id = key("unit", when, r.scheme, r.code)
    if id not in units:
        units.add(id)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=PERU,
            scheme_code=r.scheme,
            code=f"{when}:{r.code}",
            area_relation="equals",
            area_id=area(r.scheme, r.code),
            kind="standard",
        )
    return id


def _contest(
    p: Package,
    c: Contest,
    when: date,
    election: str,
    path: str,
    units: set[str],
    mismatched: Counter[str],
    over: Counter[str],
    slugs: Counter[str],
) -> None:
    provincial = c.election.kind == "provincial"
    resolved = resolve(c.place)[PROVINCE if provincial else DISTRICT]
    contest = key("contest", c.election.id, c.place.id)
    rows = [row for row in rows_of(c) if row["IdOrgPol"] not in SPECIAL]
    electos = list({person(str(row["TxRutaPolitico"])): row for row in reversed(electos_of(c))}.values())[::-1]
    elected = [row for row in electos if "SUPLENTE" not in str(row["TxCargoElecto"])]
    listed = bool(rows)
    place = title(c.place.name)
    province = title(c.place.province[1]) if c.place.province else None
    region = title(c.place.region[1])
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation" if listed else "body_seats",
        body_id=key("body", BODIES[c.election.kind][0]),
        district_area_id=area(resolved.scheme, resolved.code),
        magnitude=len(elected) or None,
        round_number=1,
        electoral_system_id=SYSTEM if listed and when >= date(1998, 1, 1) else None,
        default_result_kind="counted",
    )
    if provincial:
        p.name(contest, f"Mayor and council of {place} province, {region}", "en", "official")
        p.name(contest, f"Alcalde y regidores de la provincia de {place}, {region}", "es", "native", False)
        tail = f"provincial-{changeset.slug(region)}-{changeset.slug(place)}"
    else:
        p.name(contest, f"Mayor and council of {place} district, {province}, {region}", "en", "official")
        p.name(contest, f"Alcalde y regidores del distrito de {place}, {province}, {region}", "es", "native", False)
        tail = f"district-{changeset.slug(region)}-{changeset.slug(province or '')}-{changeset.slug(place)}"
    slugs[tail] += 1
    if slugs[tail] > 1:
        tail = f"{tail}-{slugs[tail]}"
    p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
    result_set = key("result_set", contest)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=INFOGOB,
        status="active",
        is_default=True,
        completeness=1.0 if listed else None,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    if not listed:
        p.add(
            "note",
            id=key("note", result_set, "elected"),
            entity_id=result_set,
            kind="caveat",
            body_md="Infogob publishes only the authorities elected here, and not the votes of the lists; no vote "
            "totals are stored for this contest.",
            lang="en",
        )
    unit = _unit(p, c, when, units)
    options: dict[str, str] = {}

    def option(org: str, label: str) -> str:
        if org not in options:
            id = options[org] = key("option", contest, org)
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="list",
                party_id=party(org),
                ballot_label=" ".join(label.split()),
            )
        return options[org]

    total = 0
    for row in rows:
        votes = number(row["NuOrgVotos"])
        _value(p, result_set, unit, option(str(row["IdOrgPol"]), str(row["TxOrgPol"])), 1, votes)
        total += votes or 0
    general = c.data.get("DatosGenerales") or {}
    assert isinstance(general, dict)
    registered = number(general.get("NuElectores")) or None
    cast = number(general.get("NuVotosEmi")) or None
    _value(p, result_set, unit, None, 2, registered)
    _value(p, result_set, unit, None, 3, cast)
    special = {
        SPECIAL[str(row["IdOrgPol"])]: number(row["NuOrgVotos"]) for row in rows_of(c) if row["IdOrgPol"] in SPECIAL
    }
    for measure, value in special.items():
        _value(p, result_set, unit, None, measure, value)
    if listed and cast is not None and total + sum(v or 0 for v in special.values()) != cast:
        mismatched[result_set] += 1
    if cast is not None and registered is not None and cast > registered:
        over[result_set] += 1
    seats: Counter[str] = Counter()
    made: set[str] = set()
    for row in electos:
        chosen = option(org_of(row), str(row["TxOrgPol"]))
        office = str(row["TxCargoElecto"])
        role = "substitute" if "SUPLENTE" in office else "head" if "ALCALDE" in office else "list_member"
        candidate = person(str(row["TxRutaPolitico"]))
        candidacy = key("candidacy", contest, candidate)
        if candidacy in made:
            continue
        made.add(candidacy)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=candidate,
            option_id=chosen,
            role=role,
            ballot_name=" ".join(str(row["TxCandidato"]).split()),
            member_of_party_id=party(org_of(row)),
        )
        if role != "substitute":
            seats[chosen] += 1
            p.add("outcome", result_set_id=result_set, option_id=chosen, candidacy_id=candidacy, kind="elected")
    for chosen, n in seats.items():
        p.add("outcome", result_set_id=result_set, option_id=chosen, kind="won", seats=n)


# ONPE counts

import polars as pl

from ea.sources.pe import onpe

METHOD_MESA = (
    "Votes by polling table (mesa) as the Oficina Nacional de Procesos Electorales publishes them on Peru's open "
    "data portal. Registered voters are the table's electores hábiles; null votes are stored as invalid ballots and "
    "objected votes (votos impugnados) as objected ballots. A table that was not installed carries only its "
    "registered voters. The totals of the Jurado Nacional de Elecciones remain the default result."
)
OBJECTED = 15


def _int(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars().str.replace_all(r"[^0-9]", "").cast(pl.Int64, strict=False).fill_null(0)


@functools.cache
def onpe_frame(file: str) -> pl.DataFrame:
    """One row per table and list: ubigeo, mesa, state, registered, blank, null, objected,
    label, votes."""
    path = raw.cached(SOURCE, FILES + file).path
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            data = archive.read(archive.namelist()[0])
    else:
        data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
    text = text.lstrip("\ufeff")
    separator = ";" if text.split("\n", 1)[0].count(";") > 3 else ","
    frame = pl.read_csv(
        io.StringIO(text), separator=separator, infer_schema=False, truncate_ragged_lines=True, quote_char=None
    )
    frame = frame.rename({c: c.strip().lstrip("ï»¿") for c in frame.columns})
    objected_column = next((c for c in frame.columns if c.startswith("VOTOS_IMPU")), "VOTOS_IMPUGNADOS")
    state = "ESTADO_MESA" if "ESTADO_MESA" in frame.columns else None
    frame = frame.filter(
        pl.col("UBIGEO").str.contains(r"^\s*\d{6}\s*$") & pl.col("MESA").str.contains(r"^\s*\d{6}\s*$")
    )
    return frame.select(
        pl.col("UBIGEO").str.strip_chars().alias("ubigeo"),
        pl.col("MESA").str.strip_chars().alias("mesa"),
        pl.col("DEPARTAMENTO").str.strip_chars().alias("dep"),
        pl.col("PROVINCIA").str.strip_chars().alias("prov"),
        pl.col("DISTRITO").str.strip_chars().alias("dist"),
        (
            pl.col(state).str.strip_chars().str.to_uppercase().fill_null("").replace("", "NORMAL")
            if state
            else pl.lit("NORMAL")
        ).alias("state"),
        _int("ELECTORES_HABILES").alias("registered"),
        _int("VOTOS_BLANCOS").alias("blank"),
        _int("VOTOS_NULOS").alias("null"),
        _int(objected_column).alias("objected"),
        pl.col("AGRUPACION_POLITICA").str.strip_chars().alias("label"),
        _int("VOTOS_OBTENIDOS").alias("votes"),
    )


def clean(label: str) -> str:
    return label.strip().strip('"').replace('""', '"').strip()


def match_lists(totals: dict[str, int], ours: list[tuple[str, str, int]]) -> dict[str, str] | None:
    """Match each ONPE list label to one of Infogob's options: by name, then by equal votes, then by close names. A
    label that has votes and no match gives None; one without votes, which the file repeats where the list did not
    run, is left out."""
    found: dict[str, str] = {}
    free = {name: (id, votes) for name, id, votes in ours}
    pending = dict(totals)
    for label in list(pending):
        name = onpe._fold(clean(label))
        if name in free and free[name][0] not in found.values():
            found[label] = free[name][0]
            del pending[label]
    taken = set(found.values())
    left = [(name, id, votes) for name, (id, votes) in free.items() if id not in taken]
    for label, total in list(pending.items()):
        same = [id for _, id, votes in left if votes == total and total]
        rivals = [other for other, value in pending.items() if value == total]
        if len(same) == 1 and len(rivals) == 1:
            found[label] = same[0]
            taken.add(same[0])
            del pending[label]
    left = [(name, id, votes) for name, id, votes in left if id not in taken]
    for label in list(pending):
        name = onpe._fold(clean(label))
        close = difflib.get_close_matches(name, [n for n, _, _ in left], n=1, cutoff=0.8)
        if close:
            id = next(i for n, i, _ in left if n == close[0])
            found[label] = id
            left = [x for x in left if x[1] != id]
            del pending[label]
    if any(total for total in pending.values()):
        return None
    return found


@functools.cache
def onpe_code(kind: str, dep: str, prov: str, dist: str = "") -> str:
    """The IGN code of the province or district ONPE names, or the stand-in code the geo package gives a place the
    boundaries lack; ONPE's own ubigeo codes differ from the IGN's."""
    region = ("", dep.replace("LIMA PROVINCIAS", "LIMA"))
    if kind == "provincial":
        r = resolve(Locality("", prov, region, None))[PROVINCE]
    else:
        r = resolve(Locality("", dist, region, ("", prov)))[DISTRICT]
    return r.code


def onpe_day(p: Package, when: date, here: list[Contest], election: str) -> None:
    sets = [d for d in DATASETS if d.day == when]
    if not sets:
        return
    by_area: dict[tuple[str, str], Contest] = {}
    for c in here:
        r = resolve(c.place)[PROVINCE if c.election.kind == "provincial" else DISTRICT]
        by_area[(c.election.kind, r.code)] = c
    unit_rows: dict[str, tuple[str, str]] = {}
    facts: list[pl.DataFrame] = []
    totals: Counter[str] = Counter()
    for d in sets:
        p.artifact(raw.cached(SOURCE, FILES + d.file), ONPE)
        if d.dictionary:
            p.artifact(raw.cached(SOURCE, FILES + d.dictionary), ONPE)
        frame = onpe_frame(d.file)
        names = ["dep", "prov"] if d.kind == "provincial" else ["dep", "prov", "dist"]
        for group, part in frame.group_by(names, maintain_order=True):
            c = by_area.get((d.kind, onpe_code(d.kind, *map(str, group))))
            code = group
            if c is None:
                totals["no contest"] += 1
                continue
            contest = key("contest", c.election.id, c.place.id)
            ours = [
                (
                    onpe._fold(str(row["TxOrgPol"])),
                    key("option", contest, str(row["IdOrgPol"])),
                    number(row["NuOrgVotos"]) or 0,
                )
                for row in rows_of(c)
                if row["IdOrgPol"] not in SPECIAL
            ]
            lists = part.group_by("label").agg(pl.col("votes").sum())
            chosen = match_lists({str(a): int(b) for a, b in lists.iter_rows()}, ours)
            if chosen is None:
                totals["unmatched"] += 1
                print(f"{d.file} {code}: the lists do not match Infogob's", flush=True)
                continue
            result_set = key("result_set", contest, "onpe")
            valid = int(part.filter(pl.col("label").is_in(list(chosen)))["votes"].sum())
            target = sum(number(row["NuOrgVotos"]) or 0 for row in rows_of(c) if row["IdOrgPol"] not in SPECIAL)
            p.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=ONPE,
                status="active",
                is_default=False,
                completeness=round(valid / target, 6) if target else None,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD_MESA,
                lang="en",
            )
            p.add(
                "citation",
                id=key("citation", result_set),
                entity_id=result_set,
                source_id=ONPE,
                locator={"dataset": PORTAL + d.page, "file": FILES + d.file},
            )
            if valid != target:
                totals["off"] += 1
                p.add(
                    "note",
                    id=key("note", result_set, "total"),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"The count adds up to {valid:,} valid votes, {abs(valid - target):,} "
                    f"{'more' if valid > target else 'fewer'} than the {target:,} of the declared result.",
                    lang="en",
                )
            part = part.with_columns(
                option_id=pl.col("label").replace_strict(chosen, default=None, return_dtype=pl.String)
            )
            for row in part.select("dep", "prov", "dist", "mesa").unique().iter_rows():
                unit_rows.setdefault(str(row[3]), (onpe_code("district", *map(str, row[:3])), "mesa"))
            facts.append(_onpe_values(when, part, result_set))
            totals["sets"] += 1
    units = []
    known = set(shapes()[DISTRICT])
    for mesa, (ubigeo, _) in sorted(unit_rows.items()):
        inside = ubigeo in known
        units.append(
            {
                "id": key("unit", when, "mesa", mesa),
                "polity_id": PERU,
                "scheme_code": MESA,
                "code": f"{when}:{mesa}",
                "area_relation": "within" if inside else "unlocated",
                "area_id": area(DISTRICT, ubigeo) if inside else None,
                "kind": "standard",
            }
        )
    if units:
        p.frame("reporting_unit", pl.DataFrame(units))
    if facts:
        p.frame("result_value", pl.concat(facts))
    print(f"{when}: ONPE {dict(totals)}", flush=True)


def _onpe_values(when: date, part: pl.DataFrame, result_set: str) -> pl.DataFrame:
    ids = {str(m): key("unit", when, "mesa", m) for m in part["mesa"].unique()}
    installed = ~pl.col("state").str.contains("NO INSTALADA|SIN INSTALAR")
    tables = part.unique("mesa", keep="first", maintain_order=True)
    none = pl.lit(None, pl.String)
    accounting = pl.concat(
        [
            tables.select(
                "mesa", none.alias("option_id"), pl.lit(2).alias("measure"), pl.col("registered").alias("value")
            ),
            *(
                tables.filter(installed).select(
                    "mesa", none.alias("option_id"), pl.lit(measure).alias("measure"), pl.col(column).alias("value")
                )
                for column, measure in (("blank", 6), ("null", 5), ("objected", OBJECTED))
            ),
        ],
        how="vertical_relaxed",
    )
    options = part.filter(installed & pl.col("option_id").is_not_null()).select(
        "mesa", "option_id", pl.lit(1).alias("measure"), pl.col("votes").alias("value")
    )
    mapping = pl.DataFrame({"mesa": list(ids), "unit_id": list(ids.values())})
    return (
        pl.concat([accounting, options], how="vertical_relaxed")
        .join(mapping, on="mesa")
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
