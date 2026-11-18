"""Peru's regional governor and regional council elections, 2002 to 2022.

Contests and official totals come from Infogob, the Jurado Nacional de Elecciones' election archive, one contest per
region. The Oficina Nacional de Procesos Electorales publishes the same elections by polling table as open data; each
table's counts are stored as a second, finer result set of the same contest.
"""

import difflib
import functools
import html
import json
import re
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.pe import jne, onpe
from ea.sources.pe.jne import (
    BASE,
    ELECTIONS,
    EMPTY,
    NONE,
    PROVINCES,
    RESULTS,
    TOKEN,
    TYPES,
    ficha_url,
    options,
    partial_url,
)

SOURCE = "pe.regional"
OPEN = "https://www.datosabiertos.gob.pe"
KINDS = ("REGIONAL", "CONSEJERO REGIONAL")
REGIONAL = next(code for code, kind in TYPES.items() if kind == "regional")


def _post(url: str, params: str | None = None, *, suffix: str = ".json") -> raw.Artifact:
    body: dict[str, object] = {"token": TOKEN} if params is None else {"istrParameters": params, "token": TOKEN}
    return raw.fetch(SOURCE, url, suffix=suffix, body=body)


def _cached(url: str, params: str | None = None) -> raw.Artifact:
    body: dict[str, object] = {"token": TOKEN} if params is None else {"istrParameters": params, "token": TOKEN}
    return raw.cached(SOURCE, url, body)


def _data(artifact: raw.Artifact) -> object:
    payload = json.loads(artifact.path.read_bytes())
    if payload.get("Estado") != "success":
        raise ValueError(f"{artifact.url}: {payload.get('Mensaje')}")
    return payload["Data"]


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def number(text: object) -> int | None:
    if text is None:
        return None
    digits = str(text).replace(",", "").strip()
    return int(digits) if digits not in ("", "-") else None


def day(text: str) -> date:
    d, m, y = text.split("/")
    return date(int(y), int(m), int(d))


def catalog(fetch: bool = False) -> list[tuple[str, str]]:
    artifact = _post(ELECTIONS, REGIONAL) if fetch else _cached(ELECTIONS, REGIONAL)
    data = _data(artifact)
    assert isinstance(data, list)
    return [(str(row["Value"]), str(row["Text"])) for row in data if row["Value"] != NONE]


def flags(election: str, fetch: bool = False) -> dict[str, str]:
    url = ficha_url(election)
    artifact = raw.fetch(SOURCE, url, suffix=".html") if fetch else raw.cached(SOURCE, url)
    page = artifact.path.read_text(encoding="utf-8")
    found = {
        key: html.unescape(value)
        for key, value in re.findall(r'<input type="hidden" id="(\w+)"(?: name="\w+")? value="([^"]*)"', page)
    }
    if found.get("IdEleccion") != election:
        raise ValueError(f"{url}: the page names election {found.get('IdEleccion')}")
    when = re.search(r"Fecha del proceso</span><span class=\"control\">(\d\d/\d\d/\d{4})<", page)
    if when is None:
        raise ValueError(f"{url}: no election date")
    found["date"] = when.group(1)
    return found


def elections() -> list[tuple[str, str]]:
    """The regional processes from 2002 on: (id, name)."""
    found = []
    for value, text in catalog():
        year = re.search(r"\d{4}", text)
        if year is not None and int(year.group()) >= 2002:
            found.append((value, text))
    return found


def districts(election: str, info: dict[str, str], fetch: bool = False) -> list[tuple[str, str, str | None]]:
    """The localities whose results make up one election: (locality, name, region name)."""
    url = partial_url(election, info)
    artifact = _post(url, suffix=".html") if fetch else _cached(url)
    page = artifact.path.read_text(encoding="utf-8")
    if info["IdAmbito"] == "2":
        return [(value, text, None) for value, text in options(page, "IdDistritoElectoral")]
    if info["IdAmbito"] == "3":
        found = []
        for region, name in options(page, "IdRegion"):
            params = f"{election}@{region}"
            data = _data(_post(PROVINCES, params) if fetch else _cached(PROVINCES, params))
            assert isinstance(data, list)
            found += [
                (str(row["Value"]), _text(str(row["Text"])), name) for row in data if row["Value"] not in (NONE, EMPTY)
            ]
        return found
    raise ValueError(f"{election}: scope {info['IdAmbito']} is not handled")


def results(election: str, locality: str, group: str, fetch: bool = False) -> dict[str, object]:
    params = f"{election}@{locality}@{group}"
    data = _data(_post(RESULTS, params) if fetch else _cached(RESULTS, params))
    assert isinstance(data, dict)
    return data


def fetch_infogob() -> None:
    for election, name in elections():
        info = flags(election, fetch=True)
        places = districts(election, info, fetch=True)
        for locality, _, _ in places:
            results(election, locality, info["IdGrupoEleccion"], fetch=True)
        print(f"{name} {info['date']}: {len(places)} localities", flush=True)


FILES = f"{OPEN}/sites/default/files/"


class Dataset(NamedTuple):
    cls: str
    tag: str  # the year, with "-runoff" for a second round
    page: str
    dictionary: str


PORTAL = OPEN + "/dataset/"
GOB = "resultados-por-mesa-de-las-elecciones-de-gobernadores-y-vicegobernadores-regionales-"
CONS = "resultados-por-mesa-de-las-elecciones-de-consejeros-regionales-"
TABLES = {
    "ERM2002_Gobernador_Vicegobernador.zip": Dataset(
        "governor",
        "2002",
        "resultados-por-mesa-de-las-elecciones-regionales-2002-oficina-nacional-de-procesos",
        "Diccionario_Elecciones_Gobernador_Vicegobernador_ERM2002.xlsx",
    ),
    "ERM2006_Gobernador_Vicegobernador.zip": Dataset(
        "governor", "2006", GOB + "2006", "Diccionario_Elecciones_Gobernador_Vicegobernador_2006.xlsx"
    ),
    "ERM2010_Gobernador_Vicegobernador.zip": Dataset(
        "governor", "2010", GOB + "2010", "Diccionario_Elecciones_Gobernador_Vicegobernador_2010.xlsx"
    ),
    "SER2010_Gobernador_Vicegobernador.zip": Dataset(
        "governor",
        "2010-runoff",
        "resultados-por-mesa-de-la-segunda-elecci%C3%B3n-regional-2010-oficina-nacional-de-procesos",
        "Diccionario_Elecciones_Gobernador_Vicegobernador_SER2010.xlsx",
    ),
    "ERM2010_Consejero_Regional.zip": Dataset(
        "council",
        "2010",
        CONS + "2010-oficina-nacional-de",
        "Diccionario_Elecciones_Consejero_Regional_2010.xlsx",
    ),
    "ERM2014_Gobernador_Vicegobernador.zip": Dataset(
        "governor", "2014", GOB + "2014", "Diccionario_Elecciones_Gobernador_Vicegobernador_2014.xlsx"
    ),
    "SER2014_Gobernador_Vicegobernador.zip": Dataset(
        "governor",
        "2014-runoff",
        GOB + "2014-2",
        "Diccionario_Elecciones_Gobernador_Vicegobernador_2014%20Segunda%20Eleccion.xlsx",
    ),
    "ERM2014_Consejero_Regional.zip": Dataset(
        "council",
        "2014",
        CONS + "2014-oficina-nacional-de",
        "Diccionario_Elecciones_Consejero_Regional_2014.xlsx",
    ),
    "SER2014_Consejero_Regional.zip": Dataset(
        "council",
        "2014-runoff",
        CONS + "2014-segunda-vuelta-oficina",
        "Diccionario_Elecciones_Consejero_Regional_2014%20Segunda%20Eleccion.xlsx",
    ),
    "ERM2018_Gobernador_Regional_total.zip": Dataset(
        "governor", "2018", GOB + "2018", "Diccionario_Elecciones_Gobernador_Regional_2018.xlsx"
    ),
    "ERM2018_Consejero_Regional_total.zip": Dataset(
        "council",
        "2018",
        CONS + "2018-oficina-nacional-de",
        "Diccionario_Elecciones_Consejero_Regional_2018.xlsx",
    ),
    "ERM2022_Gobernador_Vicegobernador.zip": Dataset(
        "governor", "2022", GOB + "2022", "Diccionario_Elecciones_Gobernador_Vicegobernador_ERM2022.xlsx"
    ),
    "SER2022_Gobernador_Vicegobernador.csv": Dataset(
        "governor", "2022-runoff", GOB + "2022-2", "Diccionario_Elecciones_Gobernador_Vicegobernador_SER2022.xlsx"
    ),
    "ERM2022_Consejero_Regional.zip": Dataset(
        "council",
        "2022",
        CONS + "2022-oficina-nacional-de",
        "Diccionario_Elecciones_Consejero_Regional_ERM2022.xlsx",
    ),
}


def fetch_onpe() -> None:
    for name, dataset in TABLES.items():
        for file in (name, dataset.dictionary):
            artifact = raw.fetch(SOURCE, FILES + file, suffix=file[file.rindex(".") :])
            print(file, artifact.bytes, flush=True)


SKIPPED: dict[str, list[str]] = {}


def table(name: str) -> pl.DataFrame:
    """One ONPE polling-table file as text columns. Some files hold a line break inside an organization's name."""
    artifact = raw.cached(SOURCE, FILES + name)
    if name.endswith(".zip"):
        with zipfile.ZipFile(artifact.path) as archive:
            (member,) = archive.namelist()
            data = archive.read(member)
    else:
        data = artifact.path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    lines = text.replace("\r\n", "\n").split("\n")
    header = lines[0].strip().split(";")
    rows: list[list[str]] = []
    skipped = SKIPPED.setdefault(name, [])
    skipped.clear()
    pending = ""
    for line in lines[1:]:
        if not line.strip():
            continue
        joined = f"{pending} {line}" if pending else line
        fields = joined.split(";")
        if len(fields) > len(header):
            # a record cut short by the source swallowed this line: drop the cut record and read the line alone
            skipped.append(pending)
            joined, fields = line, line.split(";")
            if len(fields) > len(header):
                raise ValueError(f"{name}: a record has {len(fields)} fields, not {len(header)}: {line[:200]}")
        if len(fields) < len(header):
            pending = joined
            continue
        rows.append([field.strip() for field in fields])
        pending = ""
    if pending.strip():
        skipped.append(pending)
    return pl.DataFrame(rows, schema=dict.fromkeys(header, pl.String), orient="row")


# Package building

PERU = jne.PERU_POLITY
DEPARTMENT, PROVINCE, DISTRICT, MESA = onpe.DEPARTMENT, onpe.PROVINCE, onpe.DISTRICT, onpe.MESA
BLANK, NULL = jne.BLANK, jne.NULL
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/PER/"
ADM1 = GEOBOUNDARIES + "ADM1/geoBoundaries-PER-ADM1_simplified.geojson"
ADM2 = GEOBOUNDARIES + "ADM2/geoBoundaries-PER-ADM2_simplified.geojson"
FLAG_DERIVED = 1
REGIONS = {
    "01": "Amazonas",
    "02": "Áncash",
    "03": "Apurímac",
    "04": "Arequipa",
    "05": "Ayacucho",
    "06": "Cajamarca",
    "07": "Cusco",
    "08": "Huancavelica",
    "09": "Huánuco",
    "10": "Ica",
    "11": "Junín",
    "12": "La Libertad",
    "13": "Lambayeque",
    "14": "Lima Provincias",
    "15": "Loreto",
    "16": "Madre de Dios",
    "17": "Moquegua",
    "18": "Pasco",
    "19": "Piura",
    "20": "Puno",
    "21": "San Martín",
    "22": "Tacna",
    "23": "Tumbes",
    "24": "Callao",
    "25": "Ucayali",
}
# Districts that ONPE's national files, from which `pe.onpe/geo` is built, do not name.
NEW_DISTRICTS = {
    "030712",
    "050314",
    "050412",
    "050413",
    "050414",
    "050415",
    "070916",
    "070917",
    "070918",
    "070919",
    "080533",
    "080534",
    "170107",
    "210806",
    "250206",
    "250207",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


INFOGOB = key("source", "infogob")
ONPE = key("source", "onpe")
GEO_SOURCE = key("source", "geoboundaries")
METHOD = (
    "Totals as published by the Jurado Nacional de Elecciones in Infogob, its election archive, for each contest's "
    "constituency. Registered voters are the electoral roll, ballots cast are the votes cast (votos emitidos), and "
    "blank and null votes are stored as blank and invalid ballots. The Oficina Nacional de Procesos Electorales' "
    "counts by polling table are stored as a second, finer result set of the same contest."
)
FINE = (
    "The Oficina Nacional de Procesos Electorales' count of each polling table, from its open data. Registered voters "
    "are the electors enabled to vote at the table. A table that was not installed, or whose record was annulled, "
    "holds registered voters and no votes. Ballots cast are derived: the votes for the lists plus the blank and null "
    "votes. The set is not the default; its completeness is its valid votes divided by the official valid votes."
)
SYSTEMS = [
    (
        "governor-2002",
        date(2002, 1, 1),
        date(2010, 1, 1),
        {"formula": "fptp"},
        (
            "The ticket with the most votes wins the governorship (called the presidency of the regional government "
            "until 2015); there is no second round."
        ),
    ),
    (
        "governor-2010",
        date(2010, 1, 1),
        None,
        {"formula": "two_round", "majority": 0.3, "advance": 2},
        (
            "A ticket that wins at least 30% of the valid votes is elected; otherwise the two leading tickets go to a "
            "second round (Ley 27683 art. 5, as amended by Ley 29470)."
        ),
    ),
]


def system(day_: date) -> str:
    for name, since, until, _, _ in SYSTEMS:
        if since <= day_ and (until is None or day_ < until):
            return key("electoral_system", name)
    raise ValueError(f"no electoral system on {day_}")


def fold(text: str) -> str:
    return onpe._fold(text)


def title(text: str) -> str:
    return jne.title(text)


def org_of(row: dict[str, object]) -> str:
    return jne.org_of(row)


def person(path: str) -> str:
    return key("person", jne.tail(path, "_historial-partidario_"))


def party(org: str) -> str:
    return key("party", org)


class Contest(NamedTuple):
    election: str
    name: str
    cls: str  # governor or council
    round: int
    day: date
    locality: str
    label: str
    region: str | None
    data: dict[str, object]

    @property
    def rows(self) -> list[dict[str, object]]:
        found = self.data.get("Resultados") or []
        assert isinstance(found, list)
        return [row for row in found if row["IdOrgPol"] not in jne.SPECIAL]

    @property
    def special(self) -> dict[int, int | None]:
        found = self.data.get("Resultados") or []
        assert isinstance(found, list)
        return {jne.SPECIAL[str(r["IdOrgPol"])]: number(r["NuOrgVotos"]) for r in found if r["IdOrgPol"] in jne.SPECIAL}

    @property
    def electos(self) -> list[dict[str, object]]:
        found = self.data.get("Electos") or []
        assert isinstance(found, list)
        return found

    @property
    def general(self) -> dict[str, object]:
        found = self.data.get("DatosGenerales") or {}
        assert isinstance(found, dict)
        return found

    @property
    def valid(self) -> int:
        return sum(number(row["NuOrgVotos"]) or 0 for row in self.rows)


@functools.cache
def contests() -> list[Contest]:
    found = []
    for election, name in elections():
        info = flags(election)
        cls = "governor" if name.startswith("REGIONAL") else "council"
        for locality, label, region in districts(election, info):
            data = results(election, locality, info["IdGrupoEleccion"])
            found.append(
                Contest(
                    election, name, cls, 2 if "2DA" in name else 1, day(info["date"]), locality, label, region, data
                )
            )
    return found


def options_of(election: str, info: dict[str, str]) -> list[tuple[str, str]]:
    return options(_cached(partial_url(election, info)).path.read_text(encoding="utf-8"), "IdRegion")


def days() -> list[date]:
    return sorted({c.day for c in contests()})


def contest_id(c: Contest) -> str:
    return key("contest", c.election, c.locality)


# The geography: ONPE's own codes, as `pe.onpe/geo` holds them


class Geography(NamedTuple):
    department: dict[str, str]  # folded name -> code
    province: dict[tuple[str, str], str]  # (department code, folded name) -> code
    district: dict[tuple[str, str], str]  # (department code, folded name) -> code
    names: dict[str, str]  # code -> latest name, for provinces and districts
    ubigeos: dict[str, tuple[str, str, str]]  # ubigeo -> latest names (department, province, district)


@functools.cache
def geography() -> Geography:
    frames = []
    for n, name in enumerate(TABLES):
        frame = table(name).select("UBIGEO", "DEPARTAMENTO", "PROVINCIA", "DISTRITO").unique()
        frames.append(frame.with_columns(pl.lit(n).alias("n")))
    every = pl.concat(frames).sort("n", descending=True)
    department: dict[str, str] = {}
    province: dict[tuple[str, str], set[str]] = defaultdict(set)
    district: dict[tuple[str, str], set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    ubigeos: dict[str, tuple[str, str, str]] = {}
    for ubigeo, dept, prov, dist, _ in every.iter_rows():
        code = str(ubigeo)
        department.setdefault(fold(dept), code[:2])
        province[(code[:2], fold(prov))].add(code[:4])
        if code.startswith("24"):
            district[(code[:2], fold(dist))].add(code)
        names.setdefault(code[:4], prov)
        names.setdefault(code, dist)
        ubigeos.setdefault(code, (dept, prov, dist))
    for name_, code in department.items():
        if fold(REGIONS[code]) != name_ and not (code == "14" and name_ == "LIMA"):
            raise ValueError(f"department {code} is {name_} in ONPE's tables and {REGIONS[code]} here")
    many = {k: v for k, v in {**province, **district}.items() if len(v) > 1}
    if many:
        raise ValueError(f"names that two codes share: {many}")
    return Geography(
        department,
        {k: next(iter(v)) for k, v in province.items()},
        {k: next(iter(v)) for k, v in district.items()},
        names,
        ubigeos,
    )


def district_area(ubigeo: str) -> str:
    return key("area", DISTRICT, ubigeo) if ubigeo in NEW_DISTRICTS else onpe.area(DISTRICT, ubigeo)


class Place(NamedTuple):
    scheme: str
    code: str
    department: str


def place_of(c: Contest) -> Place:
    """The area a contest's constituency is: a department, a province, or, in the Callao, a district."""
    geo = geography()
    if c.cls == "governor" or (c.region is None):
        code = geo.department[fold(c.label)]
        return Place(DEPARTMENT, code, code)
    code = geo.department[fold(c.region)]
    if code == "24":
        return Place(DISTRICT, geo.district[(code, fold(c.label))], code)
    return Place(PROVINCE, geo.province[(code, fold(c.label))], code)


def area_id(place: Place) -> str:
    if place.scheme == DISTRICT:
        return district_area(place.code)
    return onpe.area(place.scheme, place.code)


# Boundaries: geoBoundaries' redistribution of the Instituto Geográfico Nacional's provinces


def _features(url: str) -> list[tuple[str, shapely.Geometry]]:
    data = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))
    return [
        (_mend(str(f["properties"]["shapeName"])), shapely.make_valid(shapely.geometry.shape(f["geometry"])))
        for f in data["features"]
    ]


def _mend(name: str) -> str:
    """The boundary file holds some names as UTF-8 read as Latin-1."""
    try:
        return name.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return name


class Shapes(NamedTuple):
    department: dict[str, shapely.Geometry]
    province: dict[str, shapely.Geometry]


ALIASES = {"QUISPICANCHIS": "QUISPICANCHI", "NASCA": "NAZCA", "ANTONIO RAYMONDI": "ANTONIO RAIMONDI"}


@functools.cache
def shapes() -> Shapes:
    geo = geography()
    departments = _features(ADM1)
    outlines = {geo.department[fold(name)]: shape for name, shape in departments}
    wanted: dict[tuple[str, str], str] = {}
    for (code, name), province in geo.province.items():
        wanted[(code, name)] = province
        for alias, official in ALIASES.items():
            if official == name:
                wanted[(code, alias)] = province
    found: dict[str, list[shapely.Geometry]] = defaultdict(list)
    members: dict[str, list[shapely.Geometry]] = defaultdict(list)
    spare = []
    for name, shape in _features(ADM2):
        middle = shapely.point_on_surface(shape)
        code = next((c for c, outline in outlines.items() if outline.contains(middle)), None)
        if code is None:
            code = min(outlines, key=lambda c: outlines[c].distance(middle))
        members[code].append(shape)
        province = wanted.get((code, fold(name)))
        if province is None:
            spare.append((code, name))
        else:
            found[province].append(shape)
    if set(spare) - {("14", "Lima")}:
        raise ValueError(f"outlines without a province: {spare}")
    lost = set(geo.province.values()) - set(found)
    if lost:
        raise ValueError(f"provinces without an outline: {sorted(lost)}")
    return Shapes(
        {code: _multi(shapely.union_all(parts)) for code, parts in members.items()},
        {code: _multi(shapely.union_all(parts)) for code, parts in found.items()},
    )


def _multi(shape: shapely.Geometry) -> shapely.MultiPolygon:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    polygons = [q for part in parts for q in shapely.get_parts(part)]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def _outline(p: Package, id: str, shape: shapely.MultiPolygon) -> None:
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=GEO_SOURCE,
    )


def packages() -> list[str]:
    return ["pe.regional/geo", "pe.regional/registry", *(f"pe.regional/{d}" for d in days())]


def parse() -> None:
    geo().write()
    registry().write()
    for d in days():
        election_day(d).write()


def emit() -> None:
    package.run("load", packages())


def fetch() -> None:
    fetch_infogob()
    fetch_onpe()
    for url in (ADM1, ADM2):
        raw.fetch(SOURCE, url, suffix=".geojson")


def geo() -> Package:
    p = Package(
        "pe.regional/geo",
        SOURCE,
        "Outlines of Peru's departments and provinces from the Instituto Geográfico Nacional's boundaries, and the "
        "districts that ONPE's regional files name and its national files do not",
        depends=["pe.onpe/geo"],
    )
    p.external.add(PERU)
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (Instituto Geográfico Nacional del Perú, OCHA ROLAC)",
        title="geoBoundaries gbOpen Peru ADM1 and ADM2, 2020",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 IGO (geoBoundaries gbOpen, from the Instituto Geográfico Nacional)",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="geoBoundaries (William & Mary geoLab), derived from the Instituto Geográfico Nacional del Perú",
    )
    for url in (ADM1, ADM2):
        p.artifact(raw.cached(SOURCE, url), GEO_SOURCE)
    for scheme in (DEPARTMENT, PROVINCE):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, "2020"),
            scheme_code=scheme,
            vintage="2020",
            valid_from=date(2020, 1, 1),
            as_of=date(2020, 1, 1),
        )
    found = shapes()
    for code, shape in sorted(found.department.items()):
        _outline(p, onpe.area(DEPARTMENT, code), shape)
    for code, shape in sorted(found.province.items()):
        _outline(p, onpe.area(PROVINCE, code), shape)
    geo_ = geography()
    for ubigeo in sorted(NEW_DISTRICTS):
        id = district_area(ubigeo)
        p.add("area", id=id, scheme_code=DISTRICT)
        p.add("area_code", area_id=id, code=ubigeo)
        p.add("area_kind", area_id=id, kind="district", source_id=ONPE)
        p.add("area_parent", area_id=id, parent_id=onpe.area(PROVINCE, ubigeo[:4]))
        p.name(id, title(geo_.ubigeos[ubigeo][2]), "es", "official")
    return p


# Registry


def office_id(department: str) -> str:
    return key("office", "governor", department)


def body_id(department: str) -> str:
    return key("body", "council", department)


def _cited(p: Package, urls: list[tuple[str, str | None]]) -> None:
    for url, params in urls:
        p.uses(_cached(url, params))


def artifacts() -> list[tuple[raw.Artifact, str]]:
    """Every Infogob and ONPE file the packages read."""
    found: list[tuple[raw.Artifact, str]] = [(_cached(ELECTIONS, REGIONAL), INFOGOB)]
    for election, _ in elections():
        info = flags(election)
        found.append((raw.cached(SOURCE, ficha_url(election)), INFOGOB))
        found.append((_cached(partial_url(election, info)), INFOGOB))
        if info["IdAmbito"] == "3":
            found += [(_cached(PROVINCES, f"{election}@{region}"), INFOGOB) for region, _ in options_of(election, info)]
    found += [
        (_cached(RESULTS, f"{c.election}@{c.locality}@{flags(c.election)['IdGrupoEleccion']}"), INFOGOB)
        for c in contests()
    ]
    for name, dataset in TABLES.items():
        found.append((raw.cached(SOURCE, FILES + name), ONPE))
        found.append((raw.cached(SOURCE, FILES + dataset.dictionary), ONPE))
    return found


def registry() -> Package:
    p = Package(
        "pe.regional/registry",
        SOURCE,
        "Peru's regional governors' offices and regional councils, their electoral systems, and the parties and "
        "elected officials of the regional elections from 2002 to 2022",
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
        title="Resultados por mesa (Plataforma Nacional de Datos Abiertos)",
        url="https://www.datosabiertos.gob.pe/",
        license="Open Data Commons Attribution License",
        license_url="https://opendatacommons.org/licenses/by/1-0/",
        attribution="Oficina Nacional de Procesos Electorales (ONPE), Plataforma Nacional de Datos Abiertos",
    )
    described: set[str] = set()
    for artifact, source in artifacts():
        if artifact.storage_key not in described:
            described.add(artifact.storage_key)
            p.artifact(artifact, source)
    for code, name in REGIONS.items():
        office = office_id(code)
        p.add("office", id=office, polity_id=PERU, kind="governor")
        p.name(office, f"Governor of {name}", "en", "official")
        p.name(office, f"Gobernador Regional de {name}", "es", "native", False)
        body = body_id(code)
        p.add("body", id=body, polity_id=PERU, kind="council")
        p.name(body, f"Regional Council of {name}", "en", "official")
        p.name(body, f"Consejo Regional de {name}", "es", "native", False)
    for name, since, until, rules, method in SYSTEMS:
        id = key("electoral_system", name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=PERU,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    names: dict[str, list[tuple[date, str]]] = {}
    people: dict[str, list[tuple[date, str]]] = {}
    for c in contests():
        for row in c.rows:
            names.setdefault(str(row["IdOrgPol"]), []).append((c.day, " ".join(str(row["TxOrgPol"]).split())))
        for row in c.electos:
            names.setdefault(org_of(row), []).append((c.day, " ".join(str(row["TxOrgPol"]).split())))
            people.setdefault(person(str(row["TxRutaPolitico"])), []).append(
                (c.day, " ".join(str(row["TxCandidato"]).split()))
            )
    for org, seen in sorted(names.items()):
        id = party(org)
        p.add("party", id=id, kind="party", polity_id=PERU)
        latest = max(seen)[1]
        p.name(id, latest, "es", "official")
        for text in sorted({text for _, text in seen} - {latest}):
            p.name(id, text, "es", "official", False)
    for id, seen in sorted(people.items()):
        p.add("person", id=id)
        p.name(id, title(max(seen)[1]), "es", "official")
    return p


# Election days

ANNULLED = re.compile(r"NO INSTALADA|ANULAD")
SOURCES_LOG: list[str] = []


def dataset_of(c: Contest) -> str | None:
    """The ONPE file that holds a contest's polling tables, when ONPE publishes one."""
    tag = str(c.day.year) + ("-runoff" if c.round == 2 else "")
    found = [name for name, d in TABLES.items() if d.cls == c.cls and d.tag == tag]
    return found[0] if found else None


@functools.cache
def fine_table(name: str) -> pl.DataFrame:
    frame = table(name)
    zero = pl.lit("0")
    return frame.select(
        pl.col("UBIGEO").alias("ubigeo"),
        pl.col("MESA").alias("mesa"),
        (pl.col("ESTADO_MESA") if "ESTADO_MESA" in frame.columns else pl.lit("")).alias("state"),
        pl.col("AGRUPACION_POLITICA").alias("label"),
        pl.col("VOTOS_OBTENIDOS").cast(pl.Int64).alias("votes"),
        pl.col("ELECTORES_HABILES").cast(pl.Int64).alias("registered"),
        pl.col("VOTOS_BLANCOS").cast(pl.Int64).alias("blank"),
        pl.col("VOTOS_NULOS").cast(pl.Int64).alias("null"),
        (pl.col("VOTOS_IMPUG") if "VOTOS_IMPUG" in frame.columns else zero).cast(pl.Int64).alias("objected"),
    )


def _slice(frame: pl.DataFrame, place: Place) -> pl.DataFrame:
    width = {DEPARTMENT: 2, PROVINCE: 4, DISTRICT: 6}[place.scheme]
    return frame.filter(pl.col("ubigeo").str.slice(0, width) == place.code)


def mesa_unit(when: date, mesa: str) -> str:
    return key("unit", when, MESA, mesa)


def _fine_values(part: pl.DataFrame, result_set: str, when: date, option_of: dict[str, str]) -> pl.DataFrame:
    mesas = part.unique("mesa", keep="first").with_columns(
        (~pl.col("state").str.contains(ANNULLED.pattern)).alias("counted")
    )
    counted = mesas.filter(pl.col("counted"))
    lists = part.filter(pl.col("label").is_in(list(option_of))).join(counted.select("mesa"), on="mesa", how="semi")
    pieces = [
        mesas.select(
            "mesa", pl.lit(None, pl.String).alias("option_id"), pl.lit(2).alias("measure"), value="registered"
        ),
        lists.select(
            "mesa",
            pl.col("label").replace_strict(option_of, return_dtype=pl.String).alias("option_id"),
            pl.lit(1).alias("measure"),
            pl.col("votes").alias("value"),
        ),
    ]
    for measure, column in ((6, "blank"), (5, "null")):
        pieces.append(
            counted.select(
                "mesa", pl.lit(None, pl.String).alias("option_id"), pl.lit(measure).alias("measure"), value=column
            )
        )
    if part["objected"].sum():
        pieces.append(
            counted.select(
                "mesa", pl.lit(None, pl.String).alias("option_id"), pl.lit(15).alias("measure"), value="objected"
            )
        )
    sums = lists.group_by("mesa").agg(pl.col("votes").sum().alias("valid"))
    cast = counted.join(sums, on="mesa", how="left").select(
        "mesa",
        pl.lit(None, pl.String).alias("option_id"),
        pl.lit(3).alias("measure"),
        (pl.col("valid").fill_null(0) + pl.col("blank") + pl.col("null")).alias("value"),
    )
    values = pl.concat([*pieces, cast], how="vertical_relaxed")
    ids = pl.DataFrame({"mesa": mesas["mesa"].to_list(), "unit_id": [mesa_unit(when, m) for m in mesas["mesa"]]})
    return values.join(ids, on="mesa").select(
        pl.lit(result_set).alias("result_set_id"),
        "unit_id",
        "option_id",
        pl.lit(0).alias("channel"),
        "measure",
        pl.lit(0).alias("count_round"),
        pl.when(pl.col("measure") == 3).then(pl.lit(FLAG_DERIVED)).otherwise(pl.lit(0)).alias("flags"),
        "value",
    )


def election_kind(c: Contest) -> str:
    return "runoff" if c.round == 2 else "general"


def first_round(c: Contest) -> Contest | None:
    """The first round of a second-round contest: the same place, earlier in the same year."""
    if c.round == 1:
        return None
    here = place_of(c)
    found = [
        o for o in contests() if o.cls == c.cls and o.round == 1 and o.day.year == c.day.year and place_of(o) == here
    ]
    if len(found) != 1:
        raise ValueError(f"{c.name} {c.label}: {len(found)} first rounds")
    return found[0]


def later_round(c: Contest) -> Contest | None:
    here = place_of(c)
    for o in contests():
        if o.cls == c.cls and o.round == 2 and o.day.year == c.day.year and place_of(o) == here:
            return o
    return None


def election_day(when: date) -> Package:
    here = [c for c in contests() if c.day == when]
    depends = ["pe.onpe/geo", "pe.regional/geo", "pe.regional/registry"]
    for c in here:
        first = first_round(c)
        if first is not None and f"pe.regional/{first.day}" not in depends:
            depends.append(f"pe.regional/{first.day}")
    p = Package(
        f"pe.regional/{when}",
        SOURCE,
        f"Peru's regional governor and regional council elections of {when} by constituency, from the Jurado "
        "Nacional de Elecciones' Infogob archive, with the polling-table counts of the Oficina Nacional de Procesos "
        "Electorales",
        depends=depends,
    )
    p.external.add(PERU)
    elections_: dict[int, str] = {}
    paths: dict[int, str] = {}
    for number_ in sorted({c.round for c in here}):
        kind = "runoff" if number_ == 2 else "general"
        id = elections_[number_] = key("election", when, kind)
        p.add(
            "election",
            id=id,
            polity_id=PERU,
            kind=kind,
            date_from=when,
            date_to=when,
            status="held",
        )
        p.name(
            id,
            f"{when.year} Peruvian regional elections" + (", second round" if number_ == 2 else ""),
            "en",
            "official",
        )
        p.name(
            id,
            f"Elecciones regionales {when.year}" + (" - segunda vuelta" if number_ == 2 else ""),
            "es",
            "native",
            False,
        )
        path = paths[number_] = f"pe/{when}-regional" + ("-runoff" if number_ == 2 else "")
        p.add("slug", path=path, entity_id=id, is_current=True)
    for election_id in sorted({c.election for c in here}):
        info = flags(election_id)
        _cite(p, raw.cached(SOURCE, ficha_url(election_id)), INFOGOB)
        _cite(p, _cached(partial_url(election_id, info)), INFOGOB)
        if info["IdAmbito"] == "3":
            for region, _ in options_of(election_id, info):
                _cite(p, _cached(PROVINCES, f"{election_id}@{region}"), INFOGOB)
    units: set[str] = set()
    mesas: dict[str, str] = {}
    facts: list[pl.DataFrame] = []
    sets: list[tuple[str, str, str, int, str]] = []  # result set, contest, election, valid, file
    mismatched: Counter[str] = Counter()
    over: Counter[str] = Counter()
    for c in here:
        _contest(p, c, when, elections_[c.round], paths[c.round], units, mismatched, over, mesas, facts, sets)
    for dataset in sorted({name for _, _, _, _, name in sets}):
        _cite(p, raw.cached(SOURCE, FILES + dataset), ONPE)
        _cite(p, raw.cached(SOURCE, FILES + TABLES[dataset].dictionary), ONPE)
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            [
                {
                    "id": mesa_unit(when, mesa),
                    "polity_id": PERU,
                    "scheme_code": MESA,
                    "code": f"{when}:regional:{mesa}",
                    "area_relation": "within",
                    "area_id": district_area(ubigeo),
                    "kind": "standard",
                }
                for mesa, ubigeo in sorted(mesas.items())
            ],
            schema={
                "id": pl.String,
                "polity_id": pl.String,
                "scheme_code": pl.String,
                "code": pl.String,
                "area_relation": pl.String,
                "area_id": pl.String,
                "kind": pl.String,
            },
        ),
    )
    if facts:
        p.frame("result_value", pl.concat(facts))
    for result_set in mismatched:
        p.add(
            "note",
            id=key("note", result_set, "sum"),
            entity_id=result_set,
            kind="caveat",
            body_md="Infogob's votes for the options plus blank and null votes do not add up to the votes cast it "
            "prints for this contest. The figures are stored as printed.",
            lang="en",
        )
    if over:
        p.accept(
            "accounting.turnout",
            f"Infogob prints more votes cast than registered voters in {over.total()} constituencies; "
            "stored as printed",
        )
    return p


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


def _unit(p: Package, c: Contest, when: date, place: Place, units: set[str]) -> str:
    id = key("unit", when, place.scheme, place.code)
    if id not in units:
        units.add(id)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=PERU,
            scheme_code=place.scheme,
            code=f"{when}:{place.code}",
            area_relation="equals",
            area_id=area_id(place),
            kind="standard",
        )
    return id


def _title(c: Contest, place: Place, when: date) -> tuple[str, str]:
    """The English name and the path tail of a contest."""
    region = REGIONS[place.department]
    second = ", second round" if c.round == 2 else ""
    if c.cls == "governor":
        return f"{when.year} {region} governor election{second}", f"governor-{changeset.slug(region)}"
    name = title(c.label)
    return (
        f"{when.year} {region} regional council election, {name}{second}",
        f"council-{changeset.slug(region)}-{changeset.slug(c.label)}",
    )


def _contest(
    p: Package,
    c: Contest,
    when: date,
    election: str,
    path: str,
    units: set[str],
    mismatched: Counter[str],
    over: Counter[str],
    mesas: dict[str, str],
    facts: list[pl.DataFrame],
    sets: list[tuple[str, str, str, int, str]],
) -> None:
    place = place_of(c)
    contest = contest_id(c)
    governor = c.cls == "governor"
    previous = None
    first = first_round(c)
    if first is not None:
        previous = contest_id(first)
        p.external.add(previous)
    magnitude = None if governor else len([r for r in c.electos if r["TxCargoElecto"] == "CONSEJERO REGIONAL"]) or None
    english, tail = _title(c, place, when)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office" if governor else "list_allocation",
        office_id=office_id(place.department) if governor else None,
        body_id=None if governor else body_id(place.department),
        district_area_id=area_id(place),
        magnitude=1 if governor else magnitude,
        round_number=c.round,
        previous_round_id=previous,
        electoral_system_id=system(when) if governor else None,
        default_result_kind="counted",
    )
    p.name(contest, english, "en", "official")
    p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
    if governor and place.code == "14":
        p.add(
            "note",
            id=key("note", contest, "lima"),
            entity_id=contest,
            kind="context",
            body_md="The Lima region covers the provinces of Lima outside the province of Lima, which the Municipality "
            "of Lima Metropolitana governs; the department's area includes that province, whose voters do not take "
            "part in this election.",
            lang="en",
        )
    if place.scheme == DISTRICT:
        p.add(
            "note",
            id=key("note", contest, "district"),
            entity_id=contest,
            kind="context",
            body_md="The Callao elects its regional councillors by district, not by province.",
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
        source_id=INFOGOB,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=INFOGOB,
        locator={"url": ficha_url(c.election), "locality": c.label},
    )
    _cite(p, _cached(RESULTS, f"{c.election}@{c.locality}@{flags(c.election)['IdGrupoEleccion']}"), INFOGOB)
    unit = _unit(p, c, when, place, units)
    options: dict[str, str] = {}

    def option(org: str, label: str) -> str:
        if org not in options:
            id = options[org] = key("option", contest, org)
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="ticket" if governor else "list",
                party_id=party(org),
                ballot_label=" ".join(label.split()),
            )
        return options[org]

    total = 0
    for row in c.rows:
        votes = number(row["NuOrgVotos"])
        _value(p, result_set, unit, option(str(row["IdOrgPol"]), str(row["TxOrgPol"])), 1, votes)
        total += votes or 0
    registered = number(c.general.get("NuElectores")) or None
    cast = number(c.general.get("NuVotosEmi")) or None
    _value(p, result_set, unit, None, 2, registered)
    _value(p, result_set, unit, None, 3, cast)
    for measure, value in c.special.items():
        _value(p, result_set, unit, None, measure, value)
    if cast is not None and total + sum(v or 0 for v in c.special.values()) != cast:
        mismatched[result_set] += 1
    if cast is not None and registered is not None and cast > registered:
        over[result_set] += 1
    _outcomes(p, c, result_set, contest, options, option)
    if governor and any(r["TxCargoElecto"] == "CONSEJERO REGIONAL" for r in c.electos):
        _ticket_council(p, c, when, election, path, place)
    dataset = dataset_of(c)
    if dataset is not None and c.rows:
        _fine(p, c, when, election, contest, place, options, dataset, total, mesas, facts, sets)


def _cite(p: Package, artifact: raw.Artifact, _source: str) -> None:
    """A day's package lists the files it reads; the registry holds the source artifact rows, so that a file that two
    days share is described once."""
    p.uses(artifact)


def _outcomes(
    p: Package,
    c: Contest,
    result_set: str,
    contest: str,
    options: dict[str, str],
    option: Callable[[str, str], str],
) -> None:
    electos = c.electos
    if c.cls == "governor":
        heads = [
            r for r in electos if "VICE" not in str(r["TxCargoElecto"]) and "CONSEJERO" not in str(r["TxCargoElecto"])
        ]
        for row in [r for r in electos if "CONSEJERO" not in str(r["TxCargoElecto"])]:
            org = org_of(row)
            chosen = option(org, str(row["TxOrgPol"]))
            candidate = person(str(row["TxRutaPolitico"]))
            candidacy = key("candidacy", contest, candidate)
            vice = "VICE" in str(row["TxCargoElecto"])
            p.add(
                "candidacy",
                id=candidacy,
                person_id=candidate,
                option_id=chosen,
                role="running_mate" if vice else "head",
                ballot_name=" ".join(str(row["TxCandidato"]).split()),
                member_of_party_id=party(org),
            )
            if not vice:
                p.add("outcome", result_set_id=result_set, option_id=chosen, kind="elected")
        if heads:
            elected = {options[org_of(r)] for r in heads}
            for id in options.values():
                if id not in elected:
                    p.add("outcome", result_set_id=result_set, option_id=id, kind="lost")
        else:
            _advanced(p, c, result_set, options)
        return
    seats: Counter[str] = Counter()
    for row in electos:
        org = org_of(row)
        chosen = option(org, str(row["TxOrgPol"]))
        candidate = person(str(row["TxRutaPolitico"]))
        candidacy = key("candidacy", contest, candidate)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=candidate,
            option_id=chosen,
            role="list_member",
            ballot_name=" ".join(str(row["TxCandidato"]).split()),
            member_of_party_id=party(org),
        )
        seats[chosen] += 1
        p.add("outcome", result_set_id=result_set, option_id=chosen, candidacy_id=candidacy, kind="elected")
    for chosen, n in seats.items():
        p.add("outcome", result_set_id=result_set, option_id=chosen, kind="won", seats=n)
    if not electos:
        _advanced(p, c, result_set, options)


def _advanced(p: Package, c: Contest, result_set: str, options: dict[str, str]) -> None:
    """With no one elected, the options that went on to the second round advanced and the rest were eliminated."""
    later = later_round(c)
    if later is None:
        return
    advanced = {str(row["IdOrgPol"]) for row in later.rows}
    if not advanced <= set(options):
        raise ValueError(f"{c.name} {c.label}: second-round options {advanced - set(options)} did not run")
    for org, id in options.items():
        p.add("outcome", result_set_id=result_set, option_id=id, kind="advanced" if org in advanced else "eliminated")


def _ticket_council(p: Package, c: Contest, when: date, election: str, path: str, place: Place) -> None:
    """In 2002 and 2006 the councillors were elected on the governor's list, so Infogob holds only who was elected."""
    contest = key("contest", c.election, c.locality, "council")
    members = [r for r in c.electos if r["TxCargoElecto"] == "CONSEJERO REGIONAL"]
    region = REGIONS[place.department]
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=body_id(place.department),
        district_area_id=area_id(place),
        magnitude=len(members),
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, f"{when.year} {region} regional council election", "en", "official")
    p.add("slug", path=f"{path}/council-{changeset.slug(region)}", entity_id=contest, is_current=True)
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
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="caveat",
        body_md="Councillors were elected on the same list as the governor, so the votes are those of the governor's "
        "contest. Infogob publishes only the councillors elected, and no vote totals are stored for this contest.",
        lang="en",
    )
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=INFOGOB,
        locator={"url": ficha_url(c.election), "locality": c.label},
    )
    options: dict[str, str] = {}
    seats: Counter[str] = Counter()
    for row in members:
        org = org_of(row)
        if org not in options:
            options[org] = key("option", contest, org)
            p.add(
                "contest_option",
                id=options[org],
                contest_id=contest,
                kind="list",
                party_id=party(org),
                ballot_label=" ".join(str(row["TxOrgPol"]).split()),
            )
        candidate = person(str(row["TxRutaPolitico"]))
        candidacy = key("candidacy", contest, candidate)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=candidate,
            option_id=options[org],
            role="list_member",
            ballot_name=" ".join(str(row["TxCandidato"]).split()),
            member_of_party_id=party(org),
        )
        seats[options[org]] += 1
        p.add("outcome", result_set_id=result_set, option_id=options[org], candidacy_id=candidacy, kind="elected")
    for chosen, n in seats.items():
        p.add("outcome", result_set_id=result_set, option_id=chosen, kind="won", seats=n)


LABELS = {"PARTIDO INKA PACHAKUTEQ": "MOVIMIENTO REGIONAL INKA PACHAKUTEQ"}
UNMATCHED: list[tuple[str, str, int, list[str]]] = []


def _match(totals: dict[str, int], options: dict[str, str], where: str) -> dict[str, str]:
    """Match each ONPE list label to one of the contest's options: by name, then by the shorter name inside the longer
    one (ONPE writes "MOVIMIENTO REGIONAL" or "PARTIDO" where Infogob leaves it out), then by close spelling. A list
    without a match and without a vote in the contest is one the file repeats where it was not on the ballot."""
    found: dict[str, str] = {}
    folded = {label: fold(LABELS.get(fold(label), label)) for label in totals}
    for label, name in folded.items():
        if name in options:
            found[label] = options[name]
    free = {name: id for name, id in options.items() if id not in found.values()}
    for label, total in sorted(totals.items(), key=lambda item: -item[1]):
        if label in found:
            continue
        name = folded[label]
        inside = sorted((o for o in free if o in name or name in o), key=len, reverse=True)
        if inside and (len(inside) == 1 or len(inside[0]) > len(inside[1])):
            match = inside[0]
        else:
            close = difflib.get_close_matches(name, list(free), n=1, cutoff=0.8)
            match = close[0] if close else None
        if match is not None:
            found[label] = free.pop(match)
        elif total:
            UNMATCHED.append((where, label, total, sorted(options)))
    return found


def _fine(
    p: Package,
    c: Contest,
    when: date,
    election: str,
    contest: str,
    place: Place,
    options: dict[str, str],
    dataset: str,
    valid: int,
    mesas: dict[str, str],
    facts: list[pl.DataFrame],
    sets: list[tuple[str, str, str, int, str]],
) -> None:
    part = _slice(fine_table(dataset), place)
    if not part.height:
        print(f"{when}: {c.label}: no polling tables in {dataset}", flush=True)
        return
    by_name = {fold(str(r["TxOrgPol"])): options[str(r["IdOrgPol"])] for r in c.rows}
    totals = {str(k): int(v) for k, v in part.group_by("label").agg(pl.col("votes").sum()).iter_rows()}
    chosen = _match(totals, by_name, f"{dataset} {c.label}")
    for mesa, ubigeo in part.select("mesa", "ubigeo").unique().iter_rows():
        known = mesas.setdefault(str(mesa), str(ubigeo))
        if known != ubigeo:
            raise ValueError(f"{when}: table {mesa} is in {known} and {ubigeo}")
    result_set = key("result_set", contest, "onpe")
    values = _fine_values(part, result_set, when, chosen)
    facts.append(values)
    counted = int(values.filter(pl.col("measure") == 1)["value"].sum())
    info = TABLES[dataset]
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
        completeness=round(counted / valid, 6) if valid else None,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=FINE, lang="en")
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=ONPE,
        locator={"dataset": PORTAL + info.page, "file": FILES + dataset},
    )
    for lost in SKIPPED.get(dataset, []):
        if lost.split(";")[0] in set(part["ubigeo"].to_list()):
            p.add(
                "note",
                id=key("note", result_set, "cut", lost),
                entity_id=result_set,
                kind="caveat",
                body_md="ONPE's file holds a record cut short, which could not be read and is left out: "
                f"`{lost}`. The tables it belongs to may be missing a list's votes.",
                lang="en",
            )
    if counted != valid:
        p.add(
            "note",
            id=key("note", result_set, "total"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The polling tables add up to {counted:,} valid votes, {abs(counted - valid):,} "
            f"{'more' if counted > valid else 'fewer'} than the {valid:,} of the declared result.",
            lang="en",
        )
    sets.append((result_set, contest, election, valid, dataset))
    SOURCES_LOG.append(f"{when}\t{c.cls}\t{c.label}\t{valid}\t{counted}")
