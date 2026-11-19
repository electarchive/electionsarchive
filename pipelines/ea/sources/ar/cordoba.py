import functools
import html
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple
from urllib.parse import quote
from uuid import uuid5

import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "ar.cordoba"
BASE = "https://www.justiciacordoba.gob.ar/Estatico/JEL/Escrutinios"
DAYS = ("20030608", "20070902", "20110807", "20150705", "20190512", "20230625")


def app(day: str) -> str:
    return f"{BASE}/ReportesEleccion{day}/"


def menu_url(day: str) -> str:
    return app(day)


def report_url(day: str, level: str, code: str, cargo: int) -> str:
    return f"{app(day)}Resultados/E{day}_{level}{quote(code)}_CA{cargo}_0.htm"


@dataclass(frozen=True)
class Place:
    code: str
    mask: int
    name: str
    section: str | None


@dataclass(frozen=True)
class Menu:
    sections: dict[str, Place]
    circuits: dict[str, Place]
    localities: dict[str, Place]
    cargos: dict[int, tuple[int, str]]


def menu(day: str) -> Menu:
    text = raw.cached(SOURCE, menu_url(day)).path.read_bytes().decode("latin-1")
    cargos: dict[int, tuple[int, str]] = {}
    for bit, label, code in re.findall(
        r'\(\((\d+) & parseFloat\(ComboConfig\)\)>0\)\s*\{\s*oOption = new Option\("([^"]*)","(\d+)"\)', text
    ):
        cargos[int(bit)] = (int(code), label.strip())
    sections: dict[str, Place] = {}
    block = text[text.index("id='cmbSecciones'") :]
    block = block[: block.index("</select>")]
    for value, label in re.findall(r'<OPTION value="([^"]*)">([^<]*)</OPTION>', block):
        if value == "0":
            continue
        code, _, mask = value.partition("|")
        sections[code] = Place(code, int(mask or 0), label.split("|", 1)[-1].strip(), None)
    members: dict[str, dict[str, str]] = {"circuit": {}, "locality": {}}
    for kind, name in (("circuit", "arrCircuitosSecc"), ("locality", "arrLocalidadesSecc")):
        for number, items in re.findall(rf"var {name}(\d+) = new Array\((.*?)\);", text, re.DOTALL):
            for item in re.findall(r"""["']([^"']*)["']""", items):
                members[kind][item.split(";", 1)[0].split("|", 1)[0]] = number
    circuits: dict[str, Place] = {}
    block = text[text.index("id='cmbCircuitosTodos'") :]
    block = block[: block.index("</select>")]
    for value, label in re.findall(r"<OPTION value='([^']*)'>([^<]*)</OPTION>", block):
        if value == "0":
            continue
        code, _, mask = value.partition("|")
        circuits[code] = Place(code, int(mask or 0), label.split("|", 1)[-1].strip(), members["circuit"].get(code))
    localities: dict[str, Place] = {}
    block = text[text.index("id='cmbLocalidadesTodos'") :]
    block = block[: block.index("</select>")]
    for value, label in re.findall(r'<OPTION value="([^"]*)">([^<]*)</OPTION>', block):
        if value == "0":
            continue
        code, _, mask = value.partition("|")
        localities[code] = Place(code, int(mask or 0), label.strip(), members["locality"].get(code))
    return Menu(sections, circuits, localities, cargos)


def provincial(label: str) -> bool:
    folded = fold(label)
    return any(
        word in folded for word in ("gobernador", "distrito unico", "tribunal de cuentas provincial", "departament")
    )


def urls(day: str) -> list[str]:
    m = menu(day)
    codes = [code for code, _ in m.cargos.values()]
    out = [report_url(day, "P", "", cargo) for cargo in codes]
    out += [report_url(day, "S", code, cargo) for code in m.sections for cargo in codes]
    for level, places in (("C", m.circuits), ("L", m.localities)):
        for place in places.values():
            out += [report_url(day, level, place.code, code) for bit, (code, _) in m.cargos.items() if place.mask & bit]
    return out


def department(name: str) -> str:
    folded = fold(name)
    return "presidente roque saenz pena" if "roque saenz" in folded else folded


def fold(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", text.lower()) if ch.isalnum() or ch == " ").strip()


@dataclass(frozen=True)
class Report:
    place: str
    cargo: str
    lines: list[tuple[str, str, int]]
    totals: dict[str, int]
    tables: int | None


TOTALS = (
    ("valid", re.compile(r"total de votos v[aá]lidos", re.IGNORECASE)),
    ("null", re.compile(r"total de votos nulos", re.IGNORECASE)),
    ("blank", re.compile(r"total de votos blancos", re.IGNORECASE)),
    ("voters", re.compile(r"total de votantes", re.IGNORECASE)),
    ("registered", re.compile(r"total de electores en padr[oó]n", re.IGNORECASE)),
)


def number(text: str) -> int:
    return int(text.replace(".", "").replace(",", ""))


def read(text: str) -> Report:
    heads: list[str] = []
    lines: list[tuple[str, str, int]] = []
    totals: dict[str, int] = {}
    tables: int | None = None
    started = False
    for tr in re.findall(r"<TR>(.*?)</TR>", text, re.DOTALL | re.IGNORECASE):
        values = [
            html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", match))).strip()
            for match in re.findall(r"<TD[^>]*>(.*?)</TD>", tr, re.DOTALL | re.IGNORECASE)
        ]
        values = [value for value in values if value]
        if not values:
            continue
        if len(values) == 3 and values[1].startswith("Agrupaciones"):
            started = True
        elif not started:
            if len(values) == 1:
                heads.append(values[0])
        elif len(values) == 2 and any(pattern.fullmatch(values[0]) for _, pattern in TOTALS):
            for name, pattern in TOTALS:
                if pattern.fullmatch(values[0]):
                    totals[name] = number(values[1])
        elif len(values) == 1 and (found := re.fullmatch(r"(\d+) Mesas", values[0])):
            tables = int(found[1])
        elif len(values) == 3 and re.fullmatch(r"[\d.,]+", values[2]) and not values[0].startswith("Total"):
            lines.append((values[0], values[1], number(values[2])))
    return Report(heads[2] if len(heads) > 2 else "", heads[3] if len(heads) > 3 else "", lines, totals, tables)


# --- fetching ------------------------------------------------------------------------------------------------------
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/ARG/"
ADM1 = GEOBOUNDARIES + "ADM1/geoBoundaries-ARG-ADM1_simplified.geojson"
ADM2 = GEOBOUNDARIES + "ADM2/geoBoundaries-ARG-ADM2_simplified.geojson"
SHAPES = "ar.geoboundaries"
ACTAS = "https://www.justiciacordoba.gob.ar/Jel/pdf/procesos/"
CONSULTA_2001 = ACTAS + "2001.07.22%20-%20Escrutinio%20Definitivo.pdf"
CONVENTION_2001 = ACTAS + "2001.09.02%20-%20Escrutinio%20Definitivo.pdf"
CONSULTA_DAY = date(2001, 7, 22)
CONVENTION_DAY = date(2001, 9, 2)


def fetch() -> None:
    for url in (ADM1, ADM2):
        raw.fetch(SHAPES, url, suffix=".geojson")
    for url in (CONSULTA_2001, CONVENTION_2001):
        raw.fetch(SOURCE, url, suffix=".pdf")
    for day in DAYS:
        raw.fetch(SOURCE, menu_url(day), suffix=".htm")
    for day in DAYS:
        found = raw.fetch_many(SOURCE, urls(day), suffix=".htm", workers=12)
        print(f"{day}: {len(found)} reports", flush=True)


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        f"{SOURCE}/consulta-2001",
        f"{SOURCE}/convention-2001",
        *(f"{SOURCE}/{day[:4]}" for day in DAYS),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    consulta().write()
    convention().write()
    for day in DAYS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


# --- identifiers ---------------------------------------------------------------------------------------------------
ARGENTINA = str(uuid5(package.NAMESPACE, "polity\x1fAR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fARG"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


JEL_SOURCE = key("source", "jel")
SHAPE_SOURCE = key("source", "geoboundaries")
PROVINCE = key("area", "ar-x.provincia", "X")
LEGISLATURE = key("body", "legislature")
AUDIT_PROVINCIAL = key("body", "audit-provincial")
AUDIT_MUNICIPAL = key("body", "audit-municipal")
AUDIT_COMMUNAL = key("body", "audit-communal")
AUDIT_LOCAL = key("body", "audit-local")
COMMUNAL = key("body", "communal-commission")
CONVENTION = key("body", "local-convention")
CONSTITUENT = key("body", "constituent-convention")
GOVERNOR = key("office", "governor")
MAYOR = key("office", "mayor")
MAYOR_OR_COMMISSION = key("office", "mayor-or-commission")
OMBUDSMAN = key("office", "ombudsman")


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def party_key(name: str) -> str:
    """The party's key is its printed name without case, accents, or punctuation. The court reuses a code for
    different lists from one locality or year to the next, so the code cannot identify a party."""
    return re.sub(r"\s+", " ", fold(re.sub(r"[^\w\s]", " ", name))).strip()


def party(name: str) -> str:
    return key("party", party_key(name))


# --- reports -------------------------------------------------------------------------------------------------------
@functools.cache
def _menu(day: str) -> Menu:
    return menu(day)


@functools.cache
def report(day: str, level: str, code: str, cargo: int) -> Report | None:
    try:
        artifact = raw.cached(SOURCE, report_url(day, level, code, cargo))
    except LookupError:
        return None
    return read(artifact.path.read_bytes().decode("latin-1"))


def day_of(day: str) -> date:
    return date(int(day[:4]), int(day[4:6]), int(day[6:]))


def kind_of(label: str) -> str:
    folded = fold(label)
    for needle, kind in (
        ("gobernador", "governor"),
        ("distrito unico", "legislators"),
        ("departament", "departmental"),
        ("tribunal de cuentas provincial", "audit_provincial"),
        ("tribunal de cuentas comunal", "audit_communal"),
        ("tribunal de cuentas municipal", "audit_municipal"),
        ("tribunal de ctas municipal", "audit_local"),
        ("intendendente y concejal comision", "mayor_or_commission"),
        ("comision comunal", "communal"),
        ("intendente y concejal", "mayor"),
        ("defensor del pueblo", "ombudsman"),
        ("consulta popular", "consulta"),
        ("referendum", "referendum"),
        ("convencional", "convention"),
    ):
        if needle in folded.replace("/", "").replace("  ", " "):
            return kind
    raise ValueError(f"unknown office {label!r}")


TITLES = {
    "governor": ("Governor and Vice Governor", "Gobernador y Vicegobernador"),
    "legislators": ("Provincial legislators, province-wide district", "Legisladores por distrito único"),
    "departmental": ("Departmental legislator", "Legislador departamental"),
    "audit_provincial": ("Provincial Audit Court", "Tribunal de Cuentas Provincial"),
    "audit_communal": ("Communal audit tribunal", "Tribunal de Cuentas Comunal"),
    "audit_municipal": ("Municipal audit tribunal", "Tribunal de Cuentas Municipal"),
    "audit_local": ("Municipal or communal audit tribunal", "Tribunal de Cuentas Municipal / Comunal"),
    "communal": ("Communal commission", "Comisión comunal"),
    "mayor_or_commission": ("Mayor and council, or communal commission", "Intendente y Concejal / Comisión Comunal"),
    "mayor": ("Mayor and council", "Intendente y Concejal"),
    "ombudsman": ("Ombudsman", "Defensor del Pueblo"),
    "consulta": ("Popular consultation", "Consulta popular"),
    "referendum": ("Referendum", "Referéndum"),
    "convention": ("Municipal charter convention", "Convencional constituyente"),
}
BODIES = {
    "legislators": LEGISLATURE,
    "departmental": LEGISLATURE,
    "audit_provincial": AUDIT_PROVINCIAL,
    "audit_communal": AUDIT_COMMUNAL,
    "audit_municipal": AUDIT_MUNICIPAL,
    "audit_local": AUDIT_LOCAL,
    "communal": COMMUNAL,
    "convention": CONVENTION,
}
OFFICES = {"governor": GOVERNOR, "mayor": MAYOR, "mayor_or_commission": MAYOR_OR_COMMISSION, "ombudsman": OMBUDSMAN}
QUESTIONS = {"consulta", "referendum"}
SMALL = {"de", "del", "la", "las", "los", "y", "e", "el", "por", "en", "para", "con", "a"}


def title_case(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if text != text.upper():
        return text
    words = text.lower().split(" ")
    return " ".join(w if i and w in SMALL else w[:1].upper() + w[1:] for i, w in enumerate(words))


def contests(day: str) -> list[tuple[int, str, str, bool]]:
    """The offices of one election as (cargo code, kind, printed label, whether the office is provincial)."""
    out = []
    for code, label in _menu(day).cargos.values():
        if label:
            out.append((code, kind_of(label), label, provincial(label)))
    return out


def circuits(day: str, cargo: int) -> list[tuple[Place, Report]]:
    found = []
    for place in _menu(day).circuits.values():
        item = report(day, "C", place.code, cargo)
        if item is not None and item.lines:
            found.append((place, item))
    return found


def localities(day: str, cargo: int) -> list[tuple[Place, Report]]:
    found = []
    for place in _menu(day).localities.values():
        item = report(day, "L", place.code, cargo)
        if item is not None and item.lines:
            found.append((place, item))
    return found


def used(day: str) -> list[tuple[str, Place | None, int, Report]]:
    """Every report a day's package reads: circuits for provincial offices, localities for local ones, and the
    province and section totals that the circuits must add up to."""
    found: list[tuple[str, Place | None, int, Report]] = []
    for cargo, _, _, wide in contests(day):
        if wide:
            top = report(day, "P", "", cargo)
            if top is not None:
                found.append(("P", None, cargo, top))
            for place in _menu(day).sections.values():
                item = report(day, "S", place.code, cargo)
                if item is not None:
                    found.append(("S", place, cargo, item))
            found += [("C", place, cargo, item) for place, item in circuits(day, cargo)]
        else:
            found += [("L", place, cargo, item) for place, item in localities(day, cargo)]
    return found


# --- assigning circuits to localities -----------------------------------------------------------------------------
def vector(item: Report) -> dict[str, int]:
    """The vote counts of a report: its totals except the electors on the roll, and each list's votes."""
    out = {f"t:{name}": votes for name, votes in item.totals.items() if name != "registered" and votes}
    for code, _, votes in item.lines:
        if votes:
            out[f"o:{code}"] = out.get(f"o:{code}", 0) + votes
    return out


NODES = 300_000


def solve(circuits: list[tuple[str, dict[str, int]]], places: list[tuple[str, dict[str, int]]]) -> list[dict[str, str]]:
    """Every way, up to two, to give each place a set of circuits whose vote counts add up to the place's exactly.
    A circuit that no place printed (a locality without a report of its own) stays unassigned."""
    keys = sorted({k for _, v in circuits + places for k in v})
    width = len(keys)

    def row(v: dict[str, int]) -> list[int]:
        return [v.get(k, 0) for k in keys]

    ordered = sorted(((i, row(v)) for i, v in circuits), key=lambda a: (-sum(a[1]), a[1]))
    left = [row(v) for _, v in places]
    count = len(left)
    cand = [[j for j in range(count) if all(a <= b for a, b in zip(cv, left[j]))] for _, cv in ordered]
    avail = [[0] * width for _ in range(count)]
    for i, (_, cv) in enumerate(ordered):
        for j in cand[i]:
            for k in range(width):
                avail[j][k] += cv[k]
    if any(a > b for j in range(count) for a, b in zip(left[j], avail[j])):
        return []
    solutions: list[dict[str, str]] = []
    nodes = 0
    chosen: dict[str, str] = {}
    pick = [0] * len(ordered)

    def search(i: int) -> None:
        nonlocal nodes
        if len(solutions) >= 2:
            return
        nodes += 1
        if nodes > NODES:
            raise ValueError("circuit assignment search exceeded its budget")
        if i == len(ordered):
            if not any(any(r) for r in left):
                solutions.append(dict(chosen))
            return
        name, cv = ordered[i]
        for j in cand[i]:
            for k in range(width):
                avail[j][k] -= cv[k]
        floor = pick[i - 1] if i and ordered[i - 1][1] == cv else 0
        for j in [j for j in cand[i] if j >= floor] + [count]:
            if j < count:
                for k in range(width):
                    left[j][k] -= cv[k]
                if any(x < 0 for x in left[j]):
                    for k in range(width):
                        left[j][k] += cv[k]
                    continue
            if all(a <= b for x in cand[i] for a, b in zip(left[x], avail[x])):
                pick[i] = j
                if j < count:
                    chosen[name] = places[j][0]
                search(i + 1)
                chosen.pop(name, None)
            if j < count:
                for k in range(width):
                    left[j][k] += cv[k]
        for j in cand[i]:
            for k in range(width):
                avail[j][k] += cv[k]

    search(0)
    return solutions


class Split(NamedTuple):
    locality: Place
    report: Report
    circuits: list[tuple[Place, Report]]


def splits(day: str, cargo: int) -> tuple[list[Split], list[str]]:
    """The localities of a local office whose circuits are known, and why the others are not. A circuit joins a
    locality when, section by section, the circuits' vote counts add up exactly to the localities' printed ones. The
    menu lists no section for some localities; those are matched against the circuits left over. Where two
    assignments fit, a locality counts only if both give it the same circuits."""
    found: list[Split] = []
    skipped: list[str] = []
    everyone = circuits(day, cargo)
    places = localities(day, cargo)

    def resolve(group: list[tuple[Place, Report]], pool: dict[str, tuple[Place, Report]]) -> None:
        solutions = solve([(code, vector(r)) for code, (_, r) in pool.items()], [(p.code, vector(r)) for p, r in group])
        for place, item in group:
            members = [{code for code, owner in solution.items() if owner == place.code} for solution in solutions]
            if solutions and members[0] and all(m == members[0] for m in members):
                found.append(Split(place, item, [pool[code] for code in sorted(members[0], key=_natural)]))
            else:
                skipped.append(place.name)

    for section in sorted({place.section for place, _ in places if place.section}, key=int):
        resolve(
            [(p, r) for p, r in places if p.section == section],
            {p.code: (p, r) for p, r in everyone if p.section == section},
        )
    loose = [(p, r) for p, r in places if not p.section]
    if loose:
        taken = {circuit.code for split in found for circuit, _ in split.circuits}
        resolve(loose, {p.code: (p, r) for p, r in everyone if p.code not in taken})
    return found, skipped


def _natural(code: str) -> tuple[int, str]:
    digits = re.match(r"\d+", code.strip())
    return (int(digits[0]) if digits else 0, code)


# --- geography -----------------------------------------------------------------------------------------------------
def _outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    polygons: list[shapely.Geometry] = []
    for part in parts:
        polygons += list(shapely.get_parts(part))
    shape = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=SHAPE_SOURCE,
    )


def _features(url: str) -> list[tuple[str, shapely.Geometry]]:
    import json

    data = json.loads(raw.cached(SHAPES, url).path.read_text(encoding="utf-8"))
    return [(f["properties"]["shapeName"], shapely.geometry.shape(f["geometry"])) for f in data["features"]]


@functools.cache
def departments() -> tuple[shapely.Geometry, dict[str, tuple[str, shapely.Geometry]]]:
    """Córdoba's outline and its departments, keyed by the electoral section number."""
    (province,) = [shape for name, shape in _features(ADM1) if fold(name) == "cordoba"]
    inside = {
        department(name): (name, shape)
        for name, shape in _features(ADM2)
        if shapely.contains(province, shapely.point_on_surface(shape))
    }
    sections = _menu(DAYS[-1]).sections
    out: dict[str, tuple[str, shapely.Geometry]] = {}
    for number, place in sections.items():
        out[number] = inside[department(place.name)]
    if len(out) != 26 or len({id(v[1]) for v in out.values()}) != 26:
        raise ValueError("Córdoba's 26 departments did not all match their sections")
    return province, out


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Córdoba Province and its 26 departments (the electoral sections), with the reporting-unit schemes that "
        "Justicia Electoral's results use",
    )
    p.external |= {ARGENTINA, COUNTRY}
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="Argentina administrative boundaries, ADM1 and ADM2 (geoBoundaries gbOpen, simplified)",
        url="https://www.geoboundaries.org/countryDownloads.html",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries gbOpen, CC BY 4.0",
    )
    for url in (ADM1, ADM2):
        p.artifact(raw.cached(SHAPES, url), SHAPE_SOURCE)
    artifact = raw.cached(SHAPES, ADM2)
    for code, rank, kind, noun in (
        ("ar-x.provincia", 1, "administrative", "province"),
        ("ar-x.departamento", 2, "administrative", "department"),
        ("ar-x.localidad", 3, "precinct", "locality"),
        ("ar-x.circuito", 3, "precinct", "electoral circuit"),
    ):
        p.add("scheme", code=code, polity_id=ARGENTINA, kind=kind, rank=rank, noun=noun, code_system=None)
    for code in ("ar-x.provincia", "ar-x.departamento"):
        p.add(
            "boundary_set",
            id=key("boundary_set", code, "gbopen"),
            scheme_code=code,
            vintage="gbOpen",
            as_of=artifact.retrieved_at.date(),
        )
    province, parts = departments()
    p.add("area", id=PROVINCE, scheme_code="ar-x.provincia")
    p.add("area_code", area_id=PROVINCE, code="X")
    p.add("area_parent", area_id=PROVINCE, parent_id=COUNTRY)
    p.name(PROVINCE, "Córdoba", "es", "official")
    _outline(p, PROVINCE, province)
    for number, (name, shape) in sorted(parts.items(), key=lambda item: int(item[0])):
        id = area("ar-x.departamento", number)
        p.add("area", id=id, scheme_code="ar-x.departamento")
        p.add("area_code", area_id=id, code=number)
        p.add("area_parent", area_id=id, parent_id=PROVINCE)
        p.name(id, name, "es", "official")
        _outline(p, id, shape)
    return p


# --- registry ------------------------------------------------------------------------------------------------------
@functools.cache
def convention_lines() -> list[tuple[str, str, int]]:
    return [(code, name, votes) for code, name, votes, _ in _convention_table()[0]]


def names_of_parties() -> dict[str, tuple[dict[str, tuple[str, str]], bool]]:
    """Every printed spelling of every party, by party key, with the first year it was printed and whether the
    court ever numbered the party as an alliance."""
    out: dict[str, tuple[dict[str, tuple[str, str]], bool]] = {}
    for day in DAYS:
        for _, _, _, item in used(day):
            for code, name, _ in item.lines:
                if not code.startswith("v"):
                    names, alliance = out.get(party_key(name), ({}, False))
                    names.setdefault(name, (day[:4], name))
                    out[party_key(name)] = (names, alliance or bool(re.fullmatch(r"A\d+", code.strip())))
    for _, name, _ in convention_lines():
        names, alliance = out.get(party_key(name), ({}, False))
        names.setdefault(name, ("2001", name))
        out[party_key(name)] = (names, alliance or "Alianza" in name)
    return out


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties that ran in Córdoba's provincial and local elections, and the bodies and offices they filled",
    )
    p.external.add(ARGENTINA)
    p.add(
        "source",
        id=JEL_SOURCE,
        kind="official_body",
        publisher="Justicia Electoral de la Provincia de Córdoba",
        title="Escrutinios: report applications and certified counts, 2001 to 2023",
        url="https://www.justiciacordoba.gob.ar/Jel/Contenido/escrutinios.aspx",
        license="Published by the electoral court with no license stated",
        license_url="https://www.justiciacordoba.gob.ar/Jel/Contenido/escrutinios.aspx",
        attribution="Justicia Electoral de la Provincia de Córdoba",
    )
    for id, kind, english, spanish in (
        (LEGISLATURE, "unicameral", "Legislature of Córdoba Province", "Legislatura de la Provincia de Córdoba"),
        (AUDIT_PROVINCIAL, "council", "Audit Court of Córdoba Province", "Tribunal de Cuentas de la Provincia"),
        (AUDIT_MUNICIPAL, "council", "Municipal audit tribunals", "Tribunales de Cuentas municipales"),
        (AUDIT_COMMUNAL, "council", "Communal audit tribunals", "Tribunales de Cuentas comunales"),
        (
            AUDIT_LOCAL,
            "council",
            "Municipal and communal audit tribunals",
            "Tribunales de Cuentas municipales y comunales",
        ),
        (COMMUNAL, "council", "Communal commissions", "Comisiones comunales"),
        (CONVENTION, "constituent_assembly", "Municipal charter conventions", "Convenciones municipales"),
        (
            CONSTITUENT,
            "constituent_assembly",
            "Constitutional Convention of Córdoba",
            "Convención Constituyente de Córdoba",
        ),
    ):
        p.add("body", id=id, polity_id=ARGENTINA, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, spanish, "es", "native", False)
    for id, kind, english, spanish in (
        (GOVERNOR, "governor", "Governor of Córdoba", "Gobernador de Córdoba"),
        (MAYOR, "mayor", "Mayor (intendente)", "Intendente"),
        (MAYOR_OR_COMMISSION, "other", "Mayor or communal commission", "Intendente o comisión comunal"),
        (OMBUDSMAN, "other", "Municipal ombudsman", "Defensor del Pueblo municipal"),
    ):
        p.add("office", id=id, polity_id=ARGENTINA, body_id=None, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, spanish, "es", "native", False)
    for code, (names, alliance) in sorted(names_of_parties().items()):
        id = key("party", code)
        p.add("party", id=id, kind="bloc" if alliance else "party", polity_id=ARGENTINA)
        p.add("external_id", entity_id=id, scheme="ar.cordoba.party", value=code)
        ordered = sorted(names.values(), reverse=True)
        seen: set[str] = set()
        for _, name in ordered:
            text = title_case(name)
            if text not in seen:
                p.name(id, text, "es", "official", not seen)
                seen.add(text)
    return p


# --- elections -----------------------------------------------------------------------------------------------------
METHOD = (
    "Counted by the Justicia Electoral de la Provincia de Córdoba and published in its escrutinio report application. "
    "Registered voters are the electors on the roll, ballots cast are the voters, and valid votes are the votes for "
    "party lists; null and blank ballots are counted apart, so valid, null, and blank ballots add up to the voters. "
    "The report names only the party or alliance of each list, not its candidates."
)
TOTALS_MEASURES = (("registered", 2), ("voters", 3), ("valid", 4), ("null", 5), ("blank", 6))
OPTION_KINDS = {"governor": "ticket", "consulta": "answer", "referendum": "answer"}


def _values(p: Package, result_set: str, unit: str, item: Report, options: dict[str, str]) -> None:
    for name, measure in TOTALS_MEASURES:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=None,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=item.totals[name],
        )
    for code, _, votes in item.lines:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=options[code],
            channel=0,
            measure=1,
            count_round=0,
            flags=0,
            value=votes,
        )


def _declared(p: Package, result_set: str, item: Report, options: dict[str, str]) -> None:
    for name, measure in TOTALS_MEASURES:
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=item.totals[name])
    for code, _, votes in item.lines:
        p.add("declared", result_set_id=result_set, option_id=options[code], measure=1, value=votes)


CIRCUIT_METHOD = (
    "A locality's report does not list its circuits, so the circuits of its section were assigned to it where their "
    "vote counts add up exactly to the locality's printed ones: every list's votes, and the valid, null, and blank "
    "ballots and voters. Circuits that no locality report accounts for are left out. This set supersedes the set of "
    "locality totals."
)


def election(day: str) -> Package:
    year = day[:4]
    when = day_of(day)
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Córdoba provincial election and the municipal and communal elections held with it, by "
        "electoral circuit and locality, from the Justicia Electoral report application",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ARGENTINA)
    path = f"ar/{when}-cordoba"
    provincial_election = key("election", day)
    local_election = key("election", day, "local")
    p.add(
        "election",
        id=provincial_election,
        polity_id=ARGENTINA,
        kind="general",
        date_from=when,
        date_to=when,
        status="held",
    )
    p.name(provincial_election, f"{year} Córdoba provincial election", "en", "official")
    p.name(provincial_election, f"Elecciones provinciales de Córdoba {year}", "es", "native", False)
    p.add("slug", path=f"{path}-provincial", entity_id=provincial_election, is_current=True)
    p.add(
        "election",
        id=local_election,
        polity_id=ARGENTINA,
        parent_id=provincial_election,
        kind="local",
        date_from=when,
        date_to=when,
        status="held",
    )
    p.name(local_election, f"{year} Córdoba municipal and communal elections", "en", "official")
    p.name(local_election, f"Elecciones municipales y comunales de Córdoba {year}", "es", "native", False)
    p.add("slug", path=f"{path}-local", entity_id=local_election, is_current=True)
    units: dict[str, str] = {}
    slugs: set[str] = set()
    sections = _menu(day).sections
    made = 0

    def unit(level: str, place: Place) -> str:
        code = f"{day}:{level}{place.code.strip()}"
        id = units.get(code)
        if id is not None:
            return id
        id = units[code] = key("unit", code)
        section = place.section if place.section in sections else None
        p.add(
            "reporting_unit",
            id=id,
            polity_id=ARGENTINA,
            scheme_code="ar-x.circuito" if level == "C" else "ar-x.localidad",
            code=code,
            area_relation="within",
            area_id=area("ar-x.departamento", section) if section else PROVINCE,
            kind="standard",
        )
        return id

    def contest(
        cargo: int,
        kind: str,
        label: str,
        district: str,
        name: str | None,
        wide: bool,
        area_id: str | None,
        finer: bool = False,
    ):
        nonlocal made
        id = key("contest", day, cargo, district)
        english, spanish = TITLES[kind]
        title = f"{english}: {name}" if name else english
        native = f"{spanish}: {name}" if name else spanish
        contest_kind = (
            "ballot_question"
            if kind in QUESTIONS
            else "office"
            if kind in OFFICES
            else "body_seats"
            if kind == "departmental"
            else "list_allocation"
        )
        p.add(
            "contest",
            id=id,
            election_id=provincial_election if wide else local_election,
            kind=contest_kind,
            office_id=OFFICES.get(kind),
            body_id=BODIES.get(kind),
            district_area_id=area_id,
            magnitude={"governor": 1, "departmental": 1, "legislators": 44}.get(kind),
            round_number=1,
            default_result_kind="counted",
        )
        if kind in QUESTIONS:
            p.add("ballot_question", contest_id=id, type="referendum")
        p.name(id, title, "en", "official")
        p.name(id, native, "es", "native", False)
        slug = f"{path}-{'provincial' if wide else 'local'}/{changeset.slug(title)}"
        if slug in slugs:
            slug += f"-{district.lower()}"
        slugs.add(slug)
        p.add("slug", path=slug, entity_id=id, is_current=True)
        result_set = key("result_set", id)
        p.add(
            "result_set",
            id=result_set,
            election_id=provincial_election if wide else local_election,
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=JEL_SOURCE,
            status="superseded" if finer else "active",
            is_default=not finer,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        made += 1
        return id, result_set

    def options_of(id: str, kind: str, rows: list[Report]) -> dict[str, str]:
        out: dict[str, str] = {}
        for item in rows:
            for code, name, _ in item.lines:
                if code in out:
                    continue
                out[code] = key("option", id, code.strip())
                answer = code.startswith("v")
                digits = re.match(r"\d+", code.lstrip("AP"))
                p.add(
                    "contest_option",
                    id=out[code],
                    contest_id=id,
                    kind="answer" if answer else OPTION_KINDS.get(kind, "list"),
                    party_id=None if answer else party(name),
                    ballot_position=int(digits[0]) if digits and not answer else None,
                    ballot_label=title_case(name),
                )
        return out

    cited: list[str] = []
    over: list[str] = []
    short: list[str] = []
    unlisted: list[str] = []

    def cite(level: str, place: Place | None, cargo: int) -> None:
        cited.append(report_url(day, level, place.code if place else "", cargo))

    for cargo, kind, label, wide in contests(day):
        if wide and kind == "departmental":
            for number, place in sections.items():
                rows = [(c, r) for c, r in circuits(day, cargo) if c.section == number]
                top = report(day, "S", number, cargo)
                if not rows or top is None:
                    continue
                id, result_set = contest(
                    cargo, kind, label, number, place.name, True, area("ar-x.departamento", number)
                )
                options = options_of(id, kind, [r for _, r in rows])
                for circuit, item in rows:
                    _values(p, result_set, unit("C", circuit), item, options)
                    cite("C", circuit, cargo)
                    if item.totals["voters"] > item.totals["registered"]:
                        over.append(circuit.name)
                _declared(p, result_set, top, options)
                if sum(r.totals["registered"] for _, r in rows) != top.totals["registered"]:
                    short.append(place.name)
                cite("S", place, cargo)
        elif wide:
            rows = circuits(day, cargo)
            top = report(day, "P", "", cargo)
            if not rows:
                continue
            id, result_set = contest(cargo, kind, label, "P", None, True, PROVINCE)
            options = options_of(id, kind, [r for _, r in rows])
            for circuit, item in rows:
                _values(p, result_set, unit("C", circuit), item, options)
                cite("C", circuit, cargo)
                if item.totals["voters"] > item.totals["registered"]:
                    over.append(circuit.name)
            if top is not None:
                _declared(p, result_set, top, options)
                cite("P", None, cargo)
                if sum(r.totals["registered"] for _, r in rows) != top.totals["registered"]:
                    short.append("province")
        else:
            by_locality = {split.locality.code: split for split in splits(day, cargo)[0]}
            for place, item in localities(day, cargo):
                split = by_locality.get(place.code)
                id, result_set = contest(
                    cargo, kind, label, f"L{place.code}", title_case(place.name), False, None, split is not None
                )
                options = options_of(id, kind, [item])
                _values(p, result_set, unit("L", place), item, options)
                cite("L", place, cargo)
                if item.totals["voters"] > item.totals["registered"]:
                    over.append(place.name)
                if split is None:
                    continue
                finer = key("result_set", id, "circuits")
                p.add(
                    "result_set",
                    id=finer,
                    election_id=local_election,
                    contest_id=id,
                    stage="official",
                    kind="counted",
                    source_id=JEL_SOURCE,
                    status="active",
                    is_default=True,
                    completeness=1.0,
                    supersedes_id=result_set,
                )
                p.add(
                    "note",
                    id=key("note", finer),
                    entity_id=finer,
                    kind="methodology",
                    body_md=f"{METHOD} {CIRCUIT_METHOD}",
                    lang="en",
                )
                for circuit, report_of in split.circuits:
                    _values(p, finer, unit("C", circuit), report_of, options)
                    cite("C", circuit, cargo)
                    if report_of.totals["voters"] > report_of.totals["registered"]:
                        over.append(circuit.name)
                _declared(p, finer, item, options)
                if sum(r.totals["registered"] for _, r in split.circuits) != item.totals["registered"]:
                    unlisted.append(title_case(place.name))
    if over:
        p.accept(
            "accounting.turnout",
            "The report prints more voters than registered electors for "
            + ", ".join(sorted(set(over)))
            + "; the figures are the electoral court's own.",
        )
    if short:
        absent = sorted(
            place.name
            for place in _menu(day).circuits.values()
            if all(report(day, "C", place.code, cargo) is None for cargo, *_ in contests(day))
        )
        p.accept(
            "accounting.declared",
            "The registered electors printed for "
            + ", ".join(sorted(set(short)))
            + " exceed the sum of the circuit reports, which do not exist for the circuits "
            + ", ".join(absent)
            + "; every vote count agrees with the printed totals.",
        )
    if unlisted:
        p.accept(
            "accounting.declared",
            (p.accepted.get("accounting.declared", "") + " " if short else "")
            + "The registered electors printed for "
            + ", ".join(sorted(set(unlisted)))
            + " differ from the sum of the circuit reports assigned to them, because circuits without a report of "
            "their own are part of the locality; every vote count agrees with the printed totals.",
        )
    seen_artifacts: set[str] = set()
    for url in cited:
        artifact = raw.cached(SOURCE, url)
        if artifact.sha256 not in seen_artifacts:
            seen_artifacts.add(artifact.sha256)
            p.artifact(artifact, JEL_SOURCE)
    return p


# --- the 2001 acts -------------------------------------------------------------------------------------------------
class Word(NamedTuple):
    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    width: float
    height: float


def _words(url: str) -> list[Word]:
    path = raw.cached(SOURCE, url).path
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    words: list[Word] = []
    page = 0
    size = (0.0, 0.0)
    for line in out.splitlines():
        if found := re.search(r'<page width="([\d.]+)" height="([\d.]+)"', line):
            page += 1
            size = (float(found[1]), float(found[2]))
        elif found := re.search(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', line
        ):
            x0, y0, x1, y1 = (float(found[i]) for i in range(1, 5))
            words.append(Word(html.unescape(found[5]), page, x0, y0, x1, y1, *size))
    return words


def _rows(url: str) -> list[list[Word]]:
    """The words of the act's table page, grouped into printed lines from top to bottom."""
    words = [w for w in _words(url) if w.page == 2]
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (round(w.y0), w.x0)):
        if lines and abs(lines[-1][0].y0 - word.y0) < 6:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


AMOUNT = re.compile(r"\d{1,3}(?:\.\d{3})*")


def _table(url: str) -> list[tuple[list[Word], Word]]:
    """Each printed line of the table with its figure: the last amount on the line."""
    out = []
    for line in _rows(url):
        if line[-1].x0 > 300 and AMOUNT.fullmatch(line[-1].text):
            out.append((line, line[-1]))
    return out


def _convention_table() -> tuple[list[tuple[str, str, int, Word]], dict[str, tuple[int, Word]]]:
    lists: list[tuple[str, str, int, Word]] = []
    totals: dict[str, tuple[int, Word]] = {}
    for line, figure in _table(CONVENTION_2001):
        label = " ".join(w.text for w in line[:-1])
        value = number(figure.text)
        if re.fullmatch(r"\d+ .+", label):
            code, name = label.split(" ", 1)
            lists.append((code, name, value, figure))
        else:
            for name, pattern in (
                ("blank", "en blanco"),
                ("valid", "v[aá]lidos"),
                ("null", "nulos"),
                ("voters", "votantes"),
                ("registered", "inscriptos"),
            ):
                if re.search(pattern, label, re.IGNORECASE):
                    totals[name] = (value, figure)
    return lists, totals


def _consulta_table() -> dict[str, tuple[int, Word]]:
    figures = [figure for _, figure in _table(CONSULTA_2001)]
    names = ("yes", "no", "blank", "valid", "null", "voters", "registered")
    if len(figures) < len(names):
        raise ValueError("the 2001 consulta act has too few figures")
    return {name: (number(figure.text), figure) for name, figure in zip(names, figures, strict=False)}


def _locator(p: Package, artifact: str, stored: str, pages: dict[int, str], word: Word, **fact: object) -> None:
    if word.page not in pages:
        pages[word.page] = key("page", artifact, word.page)
        p.add(
            "source_page",
            id=pages[word.page],
            artifact_id=artifact,
            page_number=word.page,
            image_key=f"pages/{SOURCE}/{stored}/{word.page}.png",
            width=round(word.width),
            height=round(word.height),
        )
    p.add(
        "value_locator",
        **fact,
        channel=0,
        count_round=0,
        page_id=pages[word.page],
        x0=word.x0 / word.width,
        y0=word.y0 / word.height,
        x1=word.x1 / word.width,
        y1=word.y1 / word.height,
    )


def _act(p: Package, url: str, english: str, spanish: str, when: date, kind: str) -> tuple[str, str, str]:
    """The election, the province-wide unit, and the source artifact of one of the 2001 acts."""
    election = key("election", when)
    p.add("election", id=election, polity_id=ARGENTINA, kind=kind, date_from=when, date_to=when, status="held")
    p.name(election, english, "en", "official")
    p.name(election, spanish, "es", "native", False)
    p.add("slug", path=f"ar/{when}-cordoba", entity_id=election, is_current=True)
    unit = key("unit", when)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ARGENTINA,
        scheme_code="ar-x.provincia",
        code=str(when),
        area_relation="equals",
        area_id=PROVINCE,
        kind="aggregate",
    )
    artifact = p.artifact(raw.cached(SOURCE, url), JEL_SOURCE)
    return election, unit, artifact


ACT_METHOD = (
    "The province-wide count approved by the provincial electoral judge in the definitive scrutiny act. The act "
    "gives only the province total, not counts by circuit. Its valid votes include the blank ballots, which are "
    "therefore recorded here as an option, so that the options add up to the valid votes."
)


def _act_results(
    p: Package,
    election: str,
    unit: str,
    artifact: str,
    stored: str,
    when: date,
    contest_kind: str,
    names: tuple[str, str],
    body: str | None,
    options: list[tuple[str, str, str, int, Word]],
    totals: dict[str, tuple[int, Word]],
) -> None:
    contest = key("contest", when)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind=contest_kind,
        body_id=body,
        district_area_id=PROVINCE,
        round_number=1,
        default_result_kind="counted",
    )
    if contest_kind == "ballot_question":
        p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, names[0], "en", "official")
    p.name(contest, names[1], "es", "native", False)
    p.add("slug", path=f"ar/{when}-cordoba/{changeset.slug(names[0])}", entity_id=contest, is_current=True)
    result_set = key("result_set", contest)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=JEL_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=ACT_METHOD, lang="en")
    pages: dict[int, str] = {}
    for measure, name in ((2, "registered"), (3, "voters"), (4, "valid"), (5, "null")):
        value, word = totals[name]
        fact = {"result_set_id": result_set, "unit_id": unit, "option_id": None, "measure": measure}
        p.add("result_value", **fact, channel=0, count_round=0, flags=0, value=value)
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        _locator(p, artifact, stored, pages, word, **fact, source_id=None)
    for position, (code, kind, label, votes, word) in enumerate(options, 1):
        option = key("option", contest, code)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind=kind,
            party_id=party(label) if kind == "list" else None,
            ballot_position=int(code) if code.isdigit() else position,
            ballot_label=label,
        )
        fact = {"result_set_id": result_set, "unit_id": unit, "option_id": option, "measure": 1}
        p.add("result_value", **fact, channel=0, count_round=0, flags=0, value=votes)
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=votes)
        _locator(p, artifact, stored, pages, word, **fact, source_id=None)


def consulta() -> Package:
    p = Package(
        f"{SOURCE}/consulta-2001",
        SOURCE,
        "The provincial popular consultation of 22 July 2001, from the electoral judge's definitive scrutiny act",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ARGENTINA)
    figures = _consulta_table()
    election, unit, artifact = _act(
        p,
        CONSULTA_2001,
        "2001 Córdoba provincial popular consultation",
        "Consulta popular provincial de Córdoba 2001",
        CONSULTA_DAY,
        "referendum",
    )
    options = [
        ("SI", "answer", "Sí", *figures["yes"]),
        ("NO", "answer", "No", *figures["no"]),
        ("BLANCO", "none_of_the_above", "Votos en blanco", *figures["blank"]),
    ]
    if sum(v for *_, v, _ in options) != figures["valid"][0]:
        raise ValueError("the 2001 consulta options do not add up to the valid votes")
    _act_results(
        p,
        election,
        unit,
        artifact,
        raw.cached(SOURCE, CONSULTA_2001).sha256,
        CONSULTA_DAY,
        "ballot_question",
        ("Popular consultation on constitutional reform", "Consulta popular sobre la reforma constitucional"),
        None,
        options,
        figures,
    )
    return p


def convention() -> Package:
    p = Package(
        f"{SOURCE}/convention-2001",
        SOURCE,
        "The election of constitutional convention members on 2 September 2001, from the electoral judge's "
        "definitive scrutiny act",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ARGENTINA)
    lists, totals = _convention_table()
    election, unit, artifact = _act(
        p,
        CONVENTION_2001,
        "2001 Córdoba constitutional convention election",
        "Elección de convencionales constituyentes de Córdoba 2001",
        CONVENTION_DAY,
        "general",
    )
    options = [(code, "list", title_case(name), votes, word) for code, name, votes, word in lists]
    options.append(("BLANCO", "none_of_the_above", "Votos en blanco", *totals["blank"]))
    if sum(v for *_, v, _ in options) != totals["valid"][0]:
        raise ValueError("the 2001 convention options do not add up to the valid votes")
    _act_results(
        p,
        election,
        unit,
        artifact,
        raw.cached(SOURCE, CONVENTION_2001).sha256,
        CONVENTION_DAY,
        "list_allocation",
        ("Constitutional convention members", "Convencionales constituyentes"),
        CONSTITUENT,
        options,
        totals,
    )
    return p
