import functools
import json
import re
import subprocess
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import fastexcel
import polars as pl
import shapely

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "py.tsje"
SITE = "https://www.tsje.gov.py"
FILES = f"{SITE}/resultados"
PARAGUAY = str(uuid5(package.NAMESPACE, "polity\x1fPY"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fPRY"))
DAY = date(2026, 10, 4)
ELECTION = f"{SOURCE}/{DAY}"

MAYORS = f"{FILES}/resultado-muni-2026-intendentes.xlsx"
COUNCILS = f"{FILES}/resultado-muni-2026-junta-municipal.xlsx"
MAYORS_ELECTED = f"{FILES}/candidatos-electos-intendentes-2026.xlsx"
COUNCILS_ELECTED = f"{FILES}/candidatos-electos-junta-municipal-2026.xlsx"
ELECTED_SUMMARY = f"{FILES}/resumen-electos-por-organizacion-y-genero-2026.xlsx"
# The results page of the 2026 portal reads its district totals from these two modules.
SUMMARY = f"{SITE}/assets/resumen-DviwI-nn.js"
COUNCIL_SUMMARY = f"{SITE}/assets/juntas-o1DzBmUK.js"
# Paraguay's district boundaries (admin levels 1 and 2: 18 departments, 263 distritos) from the DGEEC/INE cartography, as redistributed in the
# humanitarian community's common operational dataset of July 2026.
BOUNDARIES = (
    "https://data.humdata.org/dataset/212cd82f-bcb8-445f-8b32-aa194387f6c3/resource/"
    "9d74ce1f-40d9-488b-8e7c-b710c1997f01/download/pry_admin_boundaries.geojson.zip"
)
BOUNDARIES_PAGE = "https://data.humdata.org/dataset/cod-ab-pry"
BOUNDARY_FILE = "pry_admin2.geojson"
DEPARTMENT_FILE = "pry_admin1.geojson"
BOUNDARY_DAY = date(2026, 7, 29)
WORKBOOKS = (MAYORS, COUNCILS, MAYORS_ELECTED, COUNCILS_ELECTED, ELECTED_SUMMARY)
MODULES = (SUMMARY, COUNCIL_SUMMARY)

TABLE = ("dep", "dis", "zona", "local", "mesa")
REGISTERED, CAST, VALID, INVALID, BLANK, PREFERENCE = 2, 3, 4, 5, 6, 7
PARTY_KINDS = {"Partido Político": "party", "Alianza Electoral": "bloc", "Movimiento Político": "independent_group"}

METHOD = (
    "Counted by the Tribunal Superior de Justicia Electoral per polling table (mesa receptora de votos), as published "
    "in its per-table results workbooks for the municipal elections of 4 October 2026. Registered voters are the "
    "table's roll; ballots cast are the table total the source prints; valid votes are the votes for lists; invalid "
    "and blank votes are the null and blank ballots. The table total also counts the ballots the source marks as not "
    "computed (no computados), which are not split by option, so in those tables ballots cast exceed valid, invalid, "
    "and blank votes together by that number. Tables are placed in their municipality (distrito)."
)
COUNCIL_METHOD = (
    METHOD + " Each list's votes are the sum of its candidates' preference votes, which the source prints per "
    "candidate and table; the preference votes are stored on each candidate under the list. The Tribunal also "
    "proclaims as many substitute councillors (suplentes) as members, from the same lists. They are not on the "
    "per-table candidate files; its file of elected candidates lists them, and each is recorded as a candidacy with "
    "the substitute role on the list that elected them, with no votes and no outcome of their own. The source "
    "numbers them in the same seat order as the members and does not say which member each one replaces."
)


def fetch() -> None:
    raw.fetch(SOURCE, BOUNDARIES, suffix=".zip")
    fetch_history()
    for url in (*WORKBOOKS, *MODULES):
        artifact = raw.fetch(SOURCE, url, suffix="." + url.rsplit(".", 1)[1])
        # The portal answers every unknown path with its HTML shell, so a missing file arrives as a page.
        if artifact.media_type is not None and artifact.media_type.startswith("text/html"):
            raise LookupError(f"{url} is not published (the portal served its HTML shell)")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def normal(text: object) -> str:
    plain = unicodedata.normalize("NFKD", clean(text)).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", plain).upper().split())


def title(text: str) -> str:
    small = {"de", "del", "la", "las", "los", "y", "el"}
    words = clean(text).lower().split()
    return " ".join(w if i and w in small else w[:1].upper() + w[1:] for i, w in enumerate(words))


def party(name: str) -> str:
    return key("party", normal(name))


def office(dep: int, dis: int) -> str:
    return key("office", "mayor", dep, dis)


def body(dep: int, dis: int) -> str:
    return key("body", "council", dep, dis)


SOURCE_ID = key("source", SOURCE)
SUMMARY_ID = key("source", SOURCE, "summary")
BOUNDARY_ID = key("source", SOURCE, "boundaries")
MAYOR_SYSTEM = key("electoral_system", "mayor", 1991)
COUNCIL_SYSTEM = key("electoral_system", "council", 2021)
SYSTEMS = (
    (MAYOR_SYSTEM, date(1991, 1, 1), {"formula": "fptp"}, "The candidate with the most votes is elected mayor."),
    (
        COUNCIL_SYSTEM,
        date(2021, 1, 1),
        {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_on_list"}},
        (
            "Council seats are divided among lists by D'Hondt; since 2021 each list's seats go to its candidates "
            "with the most preference votes (listas cerradas desbloqueadas)."
        ),
    ),
)


def _sources(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Tribunal Superior de Justicia Electoral",
        title="Resultados de las Elecciones Municipales 2026: archivos por mesa y candidatos electos",
        url=f"{SITE}/resultados-2026",
        license="Public official records of the Republic of Paraguay; reuse with the source acknowledged",
        license_url=f"{SITE}/resultados-2026",
        attribution="Tribunal Superior de Justicia Electoral (TSJE), Paraguay",
    )
    p.add(
        "source",
        id=SUMMARY_ID,
        kind="official_body",
        publisher="Tribunal Superior de Justicia Electoral",
        title="Resultados 2026: district totals shown on the results page",
        url=f"{SITE}/resultados-2026",
        license="Public official records of the Republic of Paraguay; reuse with the source acknowledged",
        license_url=f"{SITE}/resultados-2026",
        attribution="Tribunal Superior de Justicia Electoral (TSJE), Paraguay",
    )


def _code(column: str) -> pl.Expr:
    return pl.col(column).str.extract(r"^\s*(\d+)\s*-").cast(pl.Int64)


def _label(column: str) -> pl.Expr:
    return pl.col(column).str.replace(r"^\s*\d+\s*-\s*", "").str.strip_chars()


@functools.cache
def workbook(url: str, sheet: int = 0) -> pl.DataFrame:
    reader = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    return reader.load_sheet(reader.sheet_names[sheet], dtypes="string").to_polars()


@functools.cache
def votes(url: str) -> pl.DataFrame:
    """One row per table and option, with the table's accounting repeated on each row."""
    frame = workbook(url).with_columns(
        dep=_code("DEPARTAMENTO"),
        dep_name=_label("DEPARTAMENTO"),
        dis=_code("DISTRITO"),
        dis_name=_label("DISTRITO"),
        zona=_code("ZONA"),
        local=_code("LOCAL"),
        local_name=_label("LOCAL"),
        mesa=pl.col("NRO_MESA").cast(pl.Int64),
        lista=pl.col("NRO_LISTA").cast(pl.Int64),
        sigla=pl.col("SIGLA").str.strip_chars(),
        nombre=pl.col("NOMLISTA").map_elements(clean, return_dtype=pl.String),
        registered=pl.col("CANT_ELECTOR").cast(pl.Int64),
        list_votes=pl.col("VOTO_LISTA").cast(pl.Int64),
        blank=pl.col("VOT_BLANCO").cast(pl.Int64),
        invalid=pl.col("VOT_NULO").cast(pl.Int64),
        total=pl.col("TOTAL_MESA").cast(pl.Int64),
        uncounted=pl.col("NO_COMPUTADOS").cast(pl.Int64).fill_null(0),
        candidate=pl.col("NOMBRE_APELLIDO").map_elements(clean, return_dtype=pl.String),
    )
    if "ORDEN" in frame.columns:
        frame = frame.with_columns(orden=pl.col("ORDEN").cast(pl.Int64), preference=pl.col("VOTO_PREF").cast(pl.Int64))
    if frame.select(*TABLE).null_count().sum_horizontal().item():
        raise ValueError(f"{url}: rows without a table code")
    return frame


@functools.cache
def tables(url: str) -> pl.DataFrame:
    frame = votes(url)
    accounting = ["registered", "blank", "invalid", "total", "uncounted"]
    lists = frame.unique([*TABLE, "lista"])
    out = lists.group_by(*TABLE).agg(
        pl.col("dep_name").first(),
        pl.col("dis_name").first(),
        pl.col("local_name").first(),
        *(pl.col(c).n_unique().alias(f"{c}_n") for c in accounting),
        *(pl.col(c).first() for c in accounting),
        pl.col("list_votes").sum().alias("valid"),
    )
    if out.filter(pl.any_horizontal(pl.col(f"{c}_n") > 1 for c in accounting)).height:
        raise ValueError(f"{url}: a table prints two different totals")
    broken = out.filter(pl.col("valid") + pl.col("blank") + pl.col("invalid") + pl.col("uncounted") != pl.col("total"))
    if broken.height:
        raise ValueError(f"{url}: {broken.height} tables do not add up to their printed total: {broken.head()}")
    return out.drop(*(f"{c}_n" for c in accounting)).sort(*TABLE)


def _module(url: str) -> object:
    text = raw.cached(SOURCE, url).path.read_text()
    if url == SUMMARY:
        found = re.search(r"R=JSON\.parse\(`(.*?)`\)", text, re.DOTALL)
        if found is None:
            raise ValueError(f"{url}: no district table")
        return json.loads(found.group(1))
    found = re.search(r"const A=(\{.*\});export", text, re.DOTALL)
    if found is None:
        raise ValueError(f"{url}: no council table")
    return json.loads(found.group(1))


@functools.cache
def summary() -> dict[tuple[int, int], dict[str, object]]:
    rows = _module(SUMMARY)
    assert isinstance(rows, list)
    return {(int(row["dep"]), int(row["dis"])): row for row in rows}


def districts() -> pl.DataFrame:
    frame = tables(MAYORS).select("dep", "dep_name", "dis", "dis_name").unique().sort("dep", "dis")
    if frame.select("dep", "dis").n_unique() != frame.height:
        raise ValueError("a district has two names")
    return frame


def parse() -> None:
    """Rebuild every package from the raw cache, after checking the per-table files against the authority's totals."""
    compare()
    compare2018()
    compare2021()
    compare2015()
    compare2008()
    compare2013()
    compare2010()
    geo().write()
    registry().write()
    election().write()
    history().write()
    memoir_parties().write()
    for build in (vice_2000, general_2008, municipal_2010, general_2013, municipal_2015, general_2018, municipal_2021):
        build().write()


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *history_packages(), ELECTION]


def emit() -> None:
    package.run("load", packages())


def compare() -> dict[str, dict[str, int]]:
    """Sum the per-table files by district and compare them with the totals the results page prints."""
    national: dict[str, dict[str, int]] = {}
    for url, part in ((MAYORS, "intendente"), (COUNCILS, "junta")):
        lists = votes(url).unique([*TABLE, "lista"])
        by_list = {
            (dep, dis, sigla): n
            for dep, dis, sigla, n in lists.group_by("dep", "dis", "sigla").agg(pl.col("list_votes").sum()).iter_rows()
        }
        by_district = {
            (row["dep"], row["dis"]): row
            for row in tables(url)
            .group_by("dep", "dis")
            .agg(pl.len().alias("mesas"), *(pl.col(c).sum() for c in ("registered", "blank", "invalid", "valid")))
            .iter_rows(named=True)
        }
        totals = Counter[str]()
        problems: list[str] = []
        for (dep, dis), row in summary().items():
            printed = row[part]
            assert isinstance(printed, dict)
            ours = by_district[(dep, dis)]
            for field, column in (("electores", "registered"), ("blancos", "blank"), ("nulos", "invalid")):
                totals[field] += int(printed[field])
                totals[f"summed {field}"] += int(ours[column])
                if int(printed[field]) != ours[column]:
                    problems.append(f"{dep}:{dis} {field} {printed[field]} != {ours[column]}")
            for sigla, _, n in printed["listas"]:
                totals["lists"] += int(n)
                if by_list.get((dep, dis, sigla)) != int(n):
                    problems.append(f"{dep}:{dis} {sigla} {n} != {by_list.get((dep, dis, sigla))}")
            totals["summed lists"] += int(ours["valid"])
            totals["mesas"] += int(printed["mesas"])
            totals["summed mesas"] += int(ours["mesas"])
        if len(by_district) != len(summary()):
            problems.append(f"{len(by_district)} districts in the file, {len(summary())} on the results page")
        if problems:
            raise ValueError(f"{url}: {len(problems)} differences from the results page: {problems[:10]}")
        national[part] = dict(totals)
        print(f"{part}: {dict(totals)}", flush=True)
    shown = _module(COUNCIL_SUMMARY)
    assert isinstance(shown, dict)
    page = Counter(
        (district, int(rank), int(n)) for district, rows in shown.items() for tier, rank, _, _, n in rows if tier == 1
    )
    elected = workbook(COUNCILS_ELECTED).filter(pl.col("DESC_TITSUP") == "TITULARES")
    filed = Counter(
        (f"{dep}-{dis}", int(rank), int(n))
        for dep, dis, rank, n in elected.select("DEP_JUN", "DIS_JUN", "ORDEN_JUN", "TOT_VOTOS").iter_rows()
    )
    if page != filed:
        raise ValueError(
            f"the results page and the elected file disagree on {((page - filed) + (filed - page)).total()} councillors"
        )
    return national


# The cartography numbers the departments of Boquerón and Alto Paraguay the other way round from the Tribunal.
CARTOGRAPHY_DEPARTMENT = {16: 17, 17: 16}
# Department names the Tribunal abbreviates or gives another way than the cartography: the capital district is
# department 0, "Capital", and President Hayes is abbreviated.
DEPARTMENT_NAMES = {"CAPITAL": "Asunción", "PDTE. HAYES": "Presidente Hayes"}
# Districts whose Tribunal name is abbreviated or differs from the cartography's, by (department, district).
RENAMED = {
    (1, 4): "Sargento José Félix López",
    (2, 5): "Capiibary",
    (2, 9): "General Elizardo Aquino",
    (2, 11): "General Francisco Isidoro Resquín",
    (3, 1): "Primero de Marzo",
    (4, 3): "Capitán Mauricio José Troche",
    (4, 7): "Doctor Bottrell",
    (4, 11): "Gral. Eugenio A. Garay",
    (4, 19): "José Fassardi",
    (4, 21): "Mbocayaty",
    (4, 31): "Yataity",
    (5, 9): "Dr. J. Eulogio Estigarribia",
    (5, 11): "Dr. Juan Manuel Frutos",
    (5, 17): "Mariscal Francisco Solano López",
    (6, 5): "Dr. Moisés S. Bertoni",
    (6, 7): "Yegros",
    (6, 9): "Gral. Higinio Morinigo",
    (7, 35): "Mayor Julio Dionisio Otaño",
    (8, 0): "San Juan Bautista de las Misiones",
    (8, 17): "Yabebyry",
    (9, 9): "Caballero",
    (9, 21): "Roque Gonzalez de Santa Cruz",
    (10, 1): "Dr. Juan León Mallorquín",
    (11, 19): "Mariano Roque Alonso",
    (12, 7): "Gral. José Eduvigis Díaz",
    (12, 17): "Mayor José Dejesús Martínez",
    (12, 21): "San Juan Bautista de Ñeembucú",
    (13, 0): "Pedro Juan Caballero",
    (14, 9): "La Paloma del Espíritu Santo",
    (14, 13): "Villa Curuguaty",
    (14, 14): "Puerto Adela",
    (14, 18): "Ybyrarobaná",
    (15, 3): "José Falcón",
    (15, 4): "General José María Bruguez",
    (15, 9): "Tte. 1° Manuel Irala Fernández",
    (15, 11): "Teniente Esteban Martínez",
    (17, 3): "Mariscal José Félix Estigarribia",
}


def _fold(text: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", plain)


def boundaries() -> dict[tuple[int, int], shapely.Geometry]:
    """The cartography's district shapes keyed by the Tribunal's (department, district) codes."""
    with zipfile.ZipFile(raw.cached(SOURCE, BOUNDARIES).path) as archive:
        collection = json.loads(archive.read(BOUNDARY_FILE))
    shapes: dict[tuple[int, str], shapely.Geometry] = {}
    for feature in collection["features"]:
        props = feature["properties"]
        dep = int(props["adm1_pcode"][2:])
        name = _fold(props["adm2_name"])
        if (dep, name) in shapes:
            raise ValueError(f"the cartography has two districts called {props['adm2_name']} in department {dep}")
        shapes[dep, name] = shapely.from_geojson(json.dumps(feature["geometry"]))
    units = districts()
    found: dict[tuple[int, int], shapely.Geometry] = {}
    for dep, dis, name in units.select("dep", "dis", "dis_name").iter_rows():
        cartography = CARTOGRAPHY_DEPARTMENT.get(dep, dep)
        label = RENAMED.get((dep, dis), name)
        if (cartography, _fold(label)) not in shapes:
            raise ValueError(f"no cartography shape for district {dep}:{dis} {name}")
        found[dep, dis] = shapes.pop((cartography, _fold(label)))
    if shapes:
        raise ValueError(f"cartography districts without a Tribunal district: {sorted(shapes)}")
    return found


def department_boundaries() -> dict[int, shapely.Geometry]:
    """The cartography's department shapes keyed by the Tribunal's department code, matched by name.

    The cartography numbers Boquerón and Alto Paraguay the other way round, so the codes are never compared.
    """
    with zipfile.ZipFile(raw.cached(SOURCE, BOUNDARIES).path) as archive:
        collection = json.loads(archive.read(DEPARTMENT_FILE))
    shapes: dict[str, shapely.Geometry] = {}
    for feature in collection["features"]:
        name = _fold(feature["properties"]["adm1_name"])
        if name in shapes:
            raise ValueError(f"the cartography has two departments called {feature['properties']['adm1_name']}")
        shapes[name] = shapely.from_geojson(json.dumps(feature["geometry"]))
    found: dict[int, shapely.Geometry] = {}
    for dep, name in districts().select("dep", "dep_name").unique().sort("dep").iter_rows():
        label = _fold(DEPARTMENT_NAMES.get(name, name))
        if label not in shapes:
            raise ValueError(f"no cartography shape for department {dep} {name}")
        found[dep] = shapes.pop(label)
    if shapes:
        raise ValueError(f"cartography departments without a Tribunal department: {sorted(shapes)}")
    return found


def _wkb(geometry: shapely.Geometry) -> str:
    valid = shapely.make_valid(geometry)
    parts = [g for g in getattr(valid, "geoms", [valid]) if g.geom_type in {"Polygon", "MultiPolygon"}]
    polygons = shapely.union_all(parts) if len(parts) > 1 else parts[0]
    if polygons.geom_type == "Polygon":
        polygons = shapely.MultiPolygon([polygons])
    return shapely.to_wkb(shapely.set_srid(polygons, 4326), hex=True, include_srid=True)


def _point(geometry: shapely.Geometry) -> str:
    return shapely.to_wkb(
        shapely.set_srid(shapely.point_on_surface(shapely.make_valid(geometry)), 4326), hex=True, include_srid=True
    )


def geo() -> Package:
    frame = districts()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        f"Paraguay's {frame['dep'].n_unique()} departments (with the capital) and {frame.height} municipalities as "
        "the Tribunal Superior de Justicia Electoral codes them, and the Tribunal's 2026 results files",
    )
    p.external |= {PARAGUAY, COUNTRY}
    _sources(p)
    for url in WORKBOOKS:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    for url in MODULES:
        p.artifact(raw.cached(SOURCE, url), SUMMARY_ID)
    p.add(
        "source",
        id=BOUNDARY_ID,
        kind="official_body",
        publisher="Dirección General de Estadística, Encuestas y Censos (DGEEC), Instituto Nacional de Estadística",
        title="Paraguay: límites administrativos, cartografía de distritos (as distributed in the OCHA COD-AB, v01)",
        url=BOUNDARIES_PAGE,
        license="CC BY-IGO 3.0",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="Dirección General de Estadística, Encuestas y Censos (DGEEC), INE Paraguay; OCHA Field Information Services",
    )
    p.artifact(raw.cached(SOURCE, BOUNDARIES), BOUNDARY_ID)
    for code, rank, kind, noun in (
        ("py.departamento", 1, "administrative", "department"),
        ("py.distrito", 2, "administrative", "municipality"),
        ("py.mesa", 3, "precinct", "polling table"),
    ):
        p.add("scheme", code=code, polity_id=PARAGUAY, kind=kind, rank=rank, noun=noun, code_system=None)
    for dep, name in frame.select("dep", "dep_name").unique().sort("dep").iter_rows():
        id = area("py.departamento", str(dep))
        p.add("area", id=id, scheme_code="py.departamento")
        p.add("area_code", area_id=id, code=str(dep))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, title(name), "es", "official")
    for dep, dis, name in frame.select("dep", "dis", "dis_name").iter_rows():
        id = area("py.distrito", f"{dep}:{dis}")
        p.add("area", id=id, scheme_code="py.distrito")
        p.add("area_code", area_id=id, code=f"{dep}:{dis}")
        p.add("area_parent", area_id=id, parent_id=area("py.departamento", str(dep)))
        p.name(id, title(name), "es", "official")
    for dep, shape in sorted(department_boundaries().items()):
        p.add(
            "area_geometry",
            area_id=area("py.departamento", str(dep)),
            valid_from=None,
            valid_to=None,
            geom=_wkb(shape),
            label_point=_point(shape),
            quality="official",
            source_id=BOUNDARY_ID,
        )
    p.add(
        "boundary_set",
        id=p.id("boundary_set", "py.departamento", "2026"),
        scheme_code="py.departamento",
        vintage="2026",
        valid_from=None,
        valid_to=None,
        as_of=DAY,
    )
    shapes = boundaries()
    for (dep, dis), shape in sorted(shapes.items()):
        p.add(
            "area_geometry",
            area_id=area("py.distrito", f"{dep}:{dis}"),
            valid_from=None,
            valid_to=None,
            geom=_wkb(shape),
            label_point=_point(shape),
            quality="official",
            source_id=BOUNDARY_ID,
        )
    p.add(
        "boundary_set",
        id=p.id("boundary_set", "py.distrito", "2026"),
        scheme_code="py.distrito",
        vintage="2026",
        valid_from=None,
        valid_to=None,
        as_of=DAY,
    )
    return p


def _organizations() -> dict[str, str]:
    sheet = workbook(ELECTED_SUMMARY, 1)
    if sheet.columns[:3] != ["Organización Política", "Siglas", "Tipo Agrupación"]:
        raise ValueError(f"{ELECTED_SUMMARY}: unexpected header {sheet.columns[:3]}")
    return {normal(row[0]): PARTY_KINDS.get(str(row[2]), "party") for row in sheet.rows() if row[0]}


def _party_kind(name: str, kinds: dict[str, str]) -> str:
    if normal(name) in kinds:
        return kinds[normal(name)]
    if normal(name).startswith("ALIANZA"):
        return "bloc"
    if normal(name).startswith("MOVIMIENTO"):
        return "independent_group"
    return "party"


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Paraguay's municipal offices and councils, their electoral systems, and the parties, alliances, and "
        "movements on the Tribunal Superior de Justicia Electoral's 2026 municipal results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {PARAGUAY}
    for url in (*WORKBOOKS, *MODULES):
        p.uses(raw.cached(SOURCE, url))
    for dep, dis, name in districts().select("dep", "dis", "dis_name").iter_rows():
        place = title(name)
        p.add("office", id=office(dep, dis), polity_id=PARAGUAY, kind="mayor")
        p.name(office(dep, dis), f"Mayor of {place}", "en", "official")
        p.name(office(dep, dis), f"Intendente municipal de {place}", "es", "native", False)
        p.add("body", id=body(dep, dis), polity_id=PARAGUAY, kind="council")
        p.name(body(dep, dis), f"{place} Municipal Council", "en", "official")
        p.name(body(dep, dis), f"Junta municipal de {place}", "es", "native", False)
    for id, since, rules, method in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=PARAGUAY, valid_from=since, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    kinds = _organizations()
    spellings: dict[str, Counter[str]] = {}
    acronyms: dict[str, Counter[str]] = {}
    for url in (MAYORS, COUNCILS):
        listed = votes(url).unique(["dep", "dis", "lista"]).group_by("nombre", "sigla").len()
        for name, sigla, n in listed.iter_rows():
            spellings.setdefault(normal(name), Counter())[name] += n
            acronyms.setdefault(normal(name), Counter())[sigla] += n
    for normalized, names in sorted(spellings.items()):
        name = names.most_common(1)[0][0]
        id = party(name)
        p.add("party", id=id, kind=_party_kind(name, kinds), polity_id=PARAGUAY)
        p.add("external_id", entity_id=id, scheme="py.tsje.organizacion", value=normalized)
        p.name(id, name, "es", "official")
        sigla = acronyms[normalized].most_common(1)[0][0]
        if sigla:
            p.name(id, sigla, "es", "abbreviation", False)
    return p


def _slugs() -> dict[tuple[int, int], str]:
    frame = districts()
    counts = Counter(changeset.slug(title(name)) for name in frame["dis_name"])
    slugs: dict[tuple[int, int], str] = {}
    for dep, dep_name, dis, dis_name in frame.iter_rows():
        slug = changeset.slug(title(dis_name))
        slugs[(dep, dis)] = slug if counts[slug] == 1 else f"{slug}-{changeset.slug(title(dep_name))}"
    if len(set(slugs.values())) != len(slugs):
        raise ValueError("two municipalities share a slug")
    return slugs


def _unit_frame(units: pl.DataFrame) -> pl.DataFrame:
    return units.select(
        id=pl.col("code").map_elements(lambda code: key("unit", code), return_dtype=pl.String),
        polity_id=pl.lit(PARAGUAY),
        scheme_code=pl.lit("py.mesa"),
        code="code",
        area_relation=pl.lit("within"),
        area_id=pl.struct("dep", "dis").map_elements(
            lambda row: area("py.distrito", f"{row['dep']}:{row['dis']}"), return_dtype=pl.String
        ),
        kind=pl.lit("standard"),
    )


def _code_expr() -> pl.Expr:
    return pl.concat_str(pl.lit(str(DAY)), *(pl.col(c).cast(pl.String) for c in TABLE), separator=":")


def _accounting(frame: pl.DataFrame, result_set: str) -> pl.DataFrame:
    parts = [
        frame.select(unit_id="unit_id", measure=pl.lit(measure, pl.Int16), value=column)
        for measure, column in (
            (REGISTERED, "registered"),
            (CAST, "total"),
            (VALID, "valid"),
            (INVALID, "invalid"),
            (BLANK, "blank"),
        )
    ]
    return pl.concat(parts).with_columns(result_set_id=pl.lit(result_set), option_id=pl.lit(None, pl.String))


def _caveats(p: Package, result_set: str, frame: pl.DataFrame, counts: Counter[str]) -> None:
    uncounted = frame.filter(pl.col("uncounted") > 0)
    over = frame.filter(pl.col("total") > pl.col("registered"))
    if uncounted.height:
        counts["uncounted"] += uncounted.height
        p.add(
            "note",
            id=key("note", result_set, "uncounted"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"In {uncounted.height} table{'s' if uncounted.height > 1 else ''}, the source marks "
            f"{uncounted['uncounted'].sum():,} ballot{'s' if uncounted['uncounted'].sum() > 1 else ''} as not "
            "computed (no computados). They are part of ballots cast but of no option, blank, or null count.",
            lang="en",
        )
    if over.height:
        counts["over"] += over.height
        p.add(
            "note",
            id=key("note", result_set, "over"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"In {over.height} table{'s' if over.height > 1 else ''}, the source prints more ballots than "
            "voters on the table's roll. The figures are stored as printed.",
            lang="en",
        )


def election() -> Package:
    p = Package(
        ELECTION,
        SOURCE,
        "Paraguay's municipal elections of 4 October 2026 (mayors and municipal councils in all 263 "
        "municipalities), by polling table, from the Tribunal Superior de Justicia Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PARAGUAY}
    for url in (*WORKBOOKS, *MODULES):
        p.uses(raw.cached(SOURCE, url))
    election_id = key("election", DAY)
    path = f"py/{DAY}-municipal"
    p.add(
        "election",
        id=election_id,
        polity_id=PARAGUAY,
        kind="local",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, "2026 Paraguayan municipal elections", "en", "official")
    p.name(election_id, "Elecciones municipales 2026", "es", "native", False)

    mayor_tables = tables(MAYORS).with_columns(code=_code_expr())
    council_tables = tables(COUNCILS).with_columns(code=_code_expr())
    if set(mayor_tables["code"]) != set(council_tables["code"]):
        raise ValueError("the mayor and council files list different tables")
    units = _unit_frame(mayor_tables)
    p.frame("reporting_unit", units)
    unit_of = units.select("code", unit_id="id")
    mayor_tables = mayor_tables.join(unit_of, on="code")
    council_tables = council_tables.join(unit_of, on="code")
    mayor_votes = votes(MAYORS).with_columns(code=_code_expr()).join(unit_of, on="code")
    council_votes = votes(COUNCILS).with_columns(code=_code_expr()).join(unit_of, on="code")

    slugs = _slugs()
    places = {(dep, dis): title(name) for dep, _, dis, name in districts().iter_rows()}
    elected_mayors = {
        (int(row["COD_DEPARTAMENTO"]), int(row["COD_DISTRITO"])): row
        for row in workbook(MAYORS_ELECTED).iter_rows(named=True)
    }
    councillors = workbook(COUNCILS_ELECTED).filter(pl.col("DESC_TITSUP") == "TITULARES")
    substitutes = (
        workbook(COUNCILS_ELECTED)
        .filter(pl.col("DESC_TITSUP") == "SUPLENTES")
        .with_columns(dep=pl.col("DEP_JUN").cast(pl.Int64), dis=pl.col("DIS_JUN").cast(pl.Int64))
    )
    if substitutes.height != councillors.height:
        raise ValueError("the elected-candidates file lists a different number of substitutes and members")
    seats = Counter((int(row["DEP_JUN"]), int(row["DIS_JUN"])) for row in councillors.iter_rows(named=True))
    winners = {
        (int(row["DEP_JUN"]), int(row["DIS_JUN"]), int(row["LISTA_JUN"]), row["CEDULA_JUN"]): int(row["TOT_VOTOS"])
        for row in councillors.iter_rows(named=True)
    }
    counts: Counter[str] = Counter()
    values: list[pl.DataFrame] = []

    for (dep, dis), place in sorted(places.items()):
        printed = summary()[(dep, dis)]
        district = area("py.distrito", f"{dep}:{dis}")
        # Mayor.
        contest = key("contest", DAY, "mayor", dep, dis)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=office(dep, dis),
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=MAYOR_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Mayor of {place}", "en", "official")
        p.name(contest, f"Intendente municipal de {place}", "es", "native", False)
        p.add("slug", path=f"{path}/{slugs[(dep, dis)]}-mayor", entity_id=contest, is_current=True)
        result_set = key("result_set", DAY, "mayor", dep, dis)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        here = mayor_votes.filter(pl.col("dep") == dep, pl.col("dis") == dis)
        running = (
            here.group_by("lista", "sigla", "nombre", "candidate")
            .agg(pl.col("list_votes").sum())
            .sort("list_votes", "lista", descending=[True, False])
        )
        top = running["list_votes"].max()
        leaders = running.filter(pl.col("list_votes") == top)
        winner = elected_mayors[(dep, dis)]
        if int(winner["NRO_LISTA"]) not in set(leaders["lista"]) or int(winner["TOT_VOTO"]) != top:
            raise ValueError(f"{dep}:{dis}: the elected mayor is not the most voted candidate")
        by_sigla: dict[str, str] = {}
        for lista, sigla, nombre, candidate, _ in running.sort("lista").iter_rows():
            option = key("option", DAY, "mayor", dep, dis, lista)
            if sigla in by_sigla:
                raise ValueError(f"{dep}:{dis}: two mayoral candidates use {sigla}")
            by_sigla[sigla] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party(nombre),
                ballot_position=lista,
                ballot_label=candidate,
            )
            person = key("person", DAY, "mayor", dep, dis, lista)
            p.add("person", id=person)
            p.name(person, title(candidate), "es", "official")
            candidacy = key("candidacy", DAY, "mayor", dep, dis, lista)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate,
                member_of_party_id=party(nombre),
                is_independent=False,
            )
            elected = lista == int(winner["NRO_LISTA"])
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if elected else "lost")
        _declared(p, result_set, printed["intendente"], by_sigla)
        tables_here = mayor_tables.filter(pl.col("dep") == dep, pl.col("dis") == dis)
        values.append(_accounting(tables_here, result_set))
        values.append(
            here.select(
                result_set_id=pl.lit(result_set),
                unit_id="unit_id",
                option_id=pl.col("lista").map_elements(
                    lambda n, dep=dep, dis=dis: key("option", DAY, "mayor", dep, dis, n), return_dtype=pl.String
                ),
                measure=pl.lit(1, pl.Int16),
                value="list_votes",
            )
        )
        _caveats(p, result_set, tables_here, counts)

        # Council.
        contest = key("contest", DAY, "council", dep, dis)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body(dep, dis),
            district_area_id=district,
            magnitude=seats[(dep, dis)],
            round_number=1,
            electoral_system_id=COUNCIL_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{place} Municipal Council", "en", "official")
        p.name(contest, f"Junta municipal de {place}", "es", "native", False)
        p.add("slug", path=f"{path}/{slugs[(dep, dis)]}-council", entity_id=contest, is_current=True)
        result_set = key("result_set", DAY, "council", dep, dis)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=COUNCIL_METHOD,
            lang="en",
        )
        here = council_votes.filter(pl.col("dep") == dep, pl.col("dis") == dis)
        lists = here.select("lista", "sigla", "nombre").unique().sort("lista")
        won = Counter(lista for (d1, d2, lista, _) in winners if (d1, d2) == (dep, dis))
        by_sigla = {}
        by_lista: dict[int, str] = {}
        named: dict[int, str] = {}
        for lista, sigla, nombre in lists.iter_rows():
            option = key("option", DAY, "council", dep, dis, lista)
            by_lista[int(lista)] = option
            named[int(lista)] = nombre
            if sigla in by_sigla:
                raise ValueError(f"{dep}:{dis}: two council lists use {sigla}")
            by_sigla[sigla] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(nombre),
                ballot_position=lista,
                ballot_label=nombre,
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if won[lista] else "lost",
                seats=won[lista],
            )
        candidates = here.select("lista", "nombre", "orden", "candidate", "CEDULA").unique().sort("lista", "orden")
        for lista, nombre, orden, name, cedula in candidates.iter_rows():
            tag = (DAY, "council", dep, dis, lista, orden)
            option = key("option", *tag)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                parent_option_id=key("option", DAY, "council", dep, dis, lista),
                party_id=party(nombre),
                ballot_position=orden,
                ballot_label=name,
            )
            person = key("person", *tag)
            p.add("person", id=person)
            p.name(person, title(name), "es", "official")
            candidacy = key("candidacy", *tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=key("option", DAY, "council", dep, dis, lista),
                role="list_member",
                list_position=orden,
                ballot_name=name,
                member_of_party_id=party(nombre),
                is_independent=False,
            )
            total = winners.get((dep, dis, lista, cedula))
            if total is not None:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=key("option", DAY, "council", dep, dis, lista),
                    candidacy_id=candidacy,
                    kind="elected",
                )
                p.add("declared", result_set_id=result_set, option_id=option, measure=PREFERENCE, value=total)
        for row in substitutes.filter(pl.col("dep") == dep, pl.col("dis") == dis).iter_rows(named=True):
            lista = int(row["LISTA_JUN"])
            if lista not in by_lista:
                raise ValueError(f"{dep}:{dis}: substitute {row['ORDEN_JUN']} is on unknown list {lista}")
            tag = (DAY, "council", "substitute", dep, dis, int(row["ORDEN_JUN"]))
            person = key("person", *tag)
            p.add("person", id=person)
            p.name(person, title(row["APENOM_JUN"]), "es", "official")
            p.add(
                "candidacy",
                id=key("candidacy", *tag),
                person_id=person,
                option_id=by_lista[lista],
                role="substitute",
                ballot_name=row["APENOM_JUN"],
                member_of_party_id=party(named[lista]),
                is_independent=False,
            )
        _declared(p, result_set, printed["junta"], by_sigla)
        tables_here = council_tables.filter(pl.col("dep") == dep, pl.col("dis") == dis)
        values.append(_accounting(tables_here, result_set))
        list_rows = here.unique(["unit_id", "lista"])
        values.append(
            list_rows.select(
                result_set_id=pl.lit(result_set),
                unit_id="unit_id",
                option_id=pl.col("lista").map_elements(
                    lambda n, dep=dep, dis=dis: key("option", DAY, "council", dep, dis, n), return_dtype=pl.String
                ),
                measure=pl.lit(1, pl.Int16),
                value="list_votes",
            )
        )
        values.append(
            here.select(
                result_set_id=pl.lit(result_set),
                unit_id="unit_id",
                option_id=pl.struct("lista", "orden").map_elements(
                    lambda row, dep=dep, dis=dis: key("option", DAY, "council", dep, dis, row["lista"], row["orden"]),
                    return_dtype=pl.String,
                ),
                measure=pl.lit(PREFERENCE, pl.Int16),
                value="preference",
            )
        )
        _caveats(p, result_set, tables_here, counts)

    facts = pl.concat([frame.select("result_set_id", "unit_id", "option_id", "measure", "value") for frame in values])
    p.frame(
        "result_value",
        facts.with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    if counts["uncounted"]:
        p.accept(
            "accounting.ballots_sum",
            f"{counts['uncounted']} table counts (mayor and council) include ballots the TSJE marks as not computed "
            "(no computados), which are part of the table total but of no option, blank, or null count; stored as "
            "printed, with a caveat on each result set",
        )
    if counts["over"]:
        p.accept(
            "accounting.turnout",
            f"the TSJE prints more ballots than voters on the roll in {counts['over']} table counts (mayor and "
            "council); stored as printed, with a caveat on each result set",
        )
    return p


def _declared(p: Package, result_set: str, printed: object, by_sigla: dict[str, str]) -> None:
    assert isinstance(printed, dict)
    for measure, field in ((REGISTERED, "electores"), (BLANK, "blancos"), (INVALID, "nulos")):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=int(printed[field]))
    valid = 0
    for sigla, _, n in printed["listas"]:
        valid += int(n)
        p.add("declared", result_set_id=result_set, option_id=by_sigla[sigla], measure=1, value=int(n))
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=valid)


# ---------------------------------------------------------------------------------------------------------------------
# Earlier elections, read from archived copies of the Tribunal's own files.

ARCHIVE = "https://web.archive.org/web"
GENERAL_2018_FILES = {
    "mesa": f"{ARCHIVE}/20250414201407id_/https://tsje.gov.py/static/ups/docs/archivos/2018/mayo/RESULTADOS_POR_MESA.xls",
    "departamento": f"{ARCHIVE}/20240630161212id_/https://www.tsje.gov.py/static/ups/docs/archivos/2018/mayo/RESULTADOS_POR_DEPARTAMENTO.xls",
    "nacional": f"{ARCHIVE}/20180523100718id_/http://tsje.gov.py/static/ups/docs/archivos/2018/mayo/resumen_votos_nacional.xls",
    "electos": f"{ARCHIVE}/20240630225000id_/https://www.tsje.gov.py/static/ups/docs/archivos/2018/mayo/Integracion_TODAS_LAS%20CANDIDATURAS.xls",
}
MUNICIPAL_2021_FILES = {
    "mesa": f"{ARCHIVE}/20240827153943id_/https://tsje.gov.py/descarga_rrhh.php?id=VOTOS_POR_MESA.xlsx",
    "preferentes": f"{ARCHIVE}/20260215123107id_/https://tsje.gov.py/descarga_rrhh.php?id=VOTOS_PREFERENTES_POR_MESA.xlsx",
    "distrito": f"{ARCHIVE}/20240827153802id_/https://tsje.gov.py/descarga_rrhh.php?id=VOTOS_PREFERENTES_POR_DISTRITO.xlsx",
    "intendentes": f"{ARCHIVE}/20240827153609id_/https://tsje.gov.py/descarga_rrhh.php?id=electos_intendentes_2021.xlsx",
}
ROLL = f"{ARCHIVE}/20260209145829id_/https://tsje.gov.py/descarga_rrhh.php?id=Padron-particip-abstenc-mesa-1996-a-2021-completo.csv"
MUNICIPAL_2015_FILES = {
    "resultados": f"{ARCHIVE}/20160626224654id_/http://tsje.gov.py:80/static/ups/docs/archivos/2015/noviembre/RESULTADO_PAIS_COMPLETO_2015.pdf",
    "candidatos": f"{ARCHIVE}/20151119114116id_/http://tsje.gov.py:80/static/ups/docs/archivos/2015/boletines/candidatos-distrito-09-11-2015.xlsx",
}
MEMORIA_2008 = f"{ARCHIVE}/20160808124710id_/http://tsje.gov.py/static/ups/libros/pdf/2008_resultados.pdf"
MEMORIA_2013 = f"{ARCHIVE}/2016id_/http://tsje.gov.py/static/ups/libros/pdf/2013.pdf"
MEMORIA_2010 = f"{ARCHIVE}/2016id_/http://tsje.gov.py/static/ups/libros/pdf/em-myee-2010.pdf"
# The Tribunal's district-level file for the 2000 vice-presidential election and the 2011 referendum, as the national open-data portal
# published it. Only its 2000 rows are read.
VICE_2000_FILE = (
    f"{ARCHIVE}/20250507113347/https://www.datos.gov.py/sites/default/files/"
    "resultados-2000-y-2011-vicepresidente-y-referendum-por-distrito.csv"
)
HISTORY_FILES = {
    **{url: ".xlsx" if "RESULTADOS_POR_MESA" in url else ".xls" for url in GENERAL_2018_FILES.values()},
    **{url: ".xlsx" for url in MUNICIPAL_2021_FILES.values()},
    ROLL: ".csv",
    MUNICIPAL_2015_FILES["resultados"]: ".pdf",
    MUNICIPAL_2015_FILES["candidatos"]: ".xlsx",
    MEMORIA_2008: ".pdf",
    MEMORIA_2013: ".pdf",
    MEMORIA_2010: ".pdf",
    VICE_2000_FILE: ".csv",
}
GENERAL_2008 = date(2008, 4, 20)
GENERAL_2018 = date(2018, 4, 22)
MUNICIPAL_2021 = date(2021, 10, 10)
VICE_2000 = date(2000, 8, 13)
MUNICIPAL_2015 = date(2015, 11, 15)
MUNICIPAL_2010 = date(2010, 11, 7)
GENERAL_2013 = date(2013, 4, 21)
HISTORY_DAYS = (
    VICE_2000,
    GENERAL_2008,
    MUNICIPAL_2010,
    GENERAL_2013,
    MUNICIPAL_2015,
    GENERAL_2018,
    MUNICIPAL_2021,
)
ABROAD = 18
UNIT_KEY = ("dep", "dis", "zona", "local", "mesa")
OPEN_DATA = "Public official records of the Republic of Paraguay; reuse with the source acknowledged"


def fetch_history() -> None:
    for url, suffix in HISTORY_FILES.items():
        raw.fetch(SOURCE, url, suffix=suffix)


def original(url: str) -> str:
    """The address on the Tribunal's own site that an archived copy was taken from."""
    return re.sub(r"^https://web\.archive\.org/web/\d+id_/", "", url).replace(":80/", "/")


def _int(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.Int64)


@functools.cache
def sheets(url: str, header_row: int | None = 0) -> dict[str, pl.DataFrame]:
    reader = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    return {
        name: reader.load_sheet(name, dtypes="string", header_row=header_row).to_polars() for name in reader.sheet_names
    }


@functools.cache
def roll(year: int) -> pl.DataFrame:
    """Registered voters per polling table, from the Tribunal's table-level roll and turnout file."""
    frame = pl.read_csv(
        raw.cached(SOURCE, ROLL).path, encoding="latin1", infer_schema=False, separator=";", truncate_ragged_lines=True
    )
    return (
        frame.filter(pl.col("año") == str(year), pl.col("pad_part_abs") == "padrón")
        .select(
            dep=_int("dep"),
            dis=_int("dis"),
            zona=_int("zon"),
            local=_int("loc"),
            mesa=_int("mesa"),
            registered=_int("electores"),
        )
        .unique(UNIT_KEY)
    )


def _unit_code(day: date) -> pl.Expr:
    return pl.concat_str(pl.lit(str(day)), *(pl.col(c).cast(pl.String) for c in UNIT_KEY), separator=":")


def _old_units(p: Package, day: date, units: pl.DataFrame) -> pl.DataFrame:
    """Stage one reporting unit per polling table; tables abroad have no area."""
    frame = units.with_columns(code=_unit_code(day)).with_columns(
        unit_id=pl.col("code").map_elements(lambda code: key("unit", code), return_dtype=pl.String),
        abroad=pl.col("dep") >= ABROAD,
    )
    p.frame(
        "reporting_unit",
        frame.select(
            id="unit_id",
            polity_id=pl.lit(PARAGUAY),
            scheme_code=pl.lit("py.mesa"),
            code="code",
            area_relation=pl.when("abroad").then(pl.lit("unlocated")).otherwise(pl.lit("within")),
            area_id=pl.when("abroad")
            .then(pl.lit(None, pl.String))
            .otherwise(
                pl.struct("dep", "dis").map_elements(
                    lambda row: area("py.distrito", f"{row['dep']}:{row['dis']}"), return_dtype=pl.String
                )
            ),
            kind=pl.when("abroad").then(pl.lit("abroad_mission")).otherwise(pl.lit("standard")),
        ),
    )
    return frame


def _source(p: Package, id: str, title_: str, page: str, urls: Iterable[str]) -> None:
    p.add(
        "source",
        id=id,
        kind="official_body",
        publisher="Tribunal Superior de Justicia Electoral",
        title=title_,
        url=page,
        license=OPEN_DATA,
        license_url=SITE,
        attribution="Tribunal Superior de Justicia Electoral (TSJE), Paraguay",
    )
    for url in urls:
        p.artifact(raw.cached(SOURCE, url), id)


# Parties that every cycle numbers the same way. A list on a table-level file that carries only a number and an
# acronym is tied to a party only when both agree with this table.
NATIONAL_PARTIES = {
    (1, "ANR"): "PARTIDO COLORADO",
    (2, "PLRA"): "PARTIDO LIBERAL RADICAL AUTENTICO",
    (3, "PRF"): "PARTIDO REVOLUCIONARIO FEBRERISTA",
    (6, "PDC"): "PARTIDO DEMOCRATA CRISTIANO",
    (7, "UNACE"): "PARTIDO UNACE",
    (8, "PPQ"): "PARTIDO PATRIA QUERIDA",
    (9, "PEN"): "PARTIDO ENCUENTRO NACIONAL",
    (11, "PMAS"): "PARTIDO DEL MOVIMIENTO AL SOCIALISMO",
    (12, "PFA"): "PARTIDO FRENTE AMPLIO",
    (23, "PVP"): "PARTIDO VERDE PARAGUAY",
    (40, "CNFG"): "CONCERTACION NACIONAL FRENTE GUASU",
    (45, "PSDH"): "PARTIDO SOCIALISTA DEMOCRATICO HEREDEROS",
}


@functools.cache
def registry_parties() -> frozenset[str]:
    """Normalized names of the parties the 2026 registry package already holds."""
    names = set()
    for url in (MAYORS, COUNCILS):
        names |= {normal(name) for name in votes(url)["nombre"].unique()}
    return frozenset(names)


class Spec(NamedTuple):
    key: str
    kind: str
    en: str
    es: str
    slug: str
    district: str
    office: str | None
    body: str | None
    system: str | None
    magnitude: int | None
    option: str


class Tally(NamedTuple):
    """One contest's table-level figures and what the Tribunal declared for it."""

    rows: pl.DataFrame  # unit_id, lista, votes
    totals: pl.DataFrame  # unit_id, registered (nullable), total, blank, invalid
    labels: dict[int, tuple[str, str | None]]  # list number -> (ballot label, party id)
    seats: dict[int, int] | None = None  # list contests: seats by list, when declared
    winner: int | None = None  # office contests: the declared winner's list
    members: tuple[tuple[int, int, str], ...] = ()  # (list, position, name) of declared winners and running mates
    declared: tuple[tuple[int | None, int, int], ...] = ()  # (list or None, measure, value) the Tribunal prints
    preferences: pl.DataFrame | None = None  # unit_id, lista, orden, votes
    declared_preferences: tuple[tuple[int, int, int], ...] = ()  # (list, position, votes) the Tribunal prints
    candidates: tuple[tuple[int, int, str], ...] = ()  # (list, position, name) of registered candidates, no outcome
    positions: dict[int, int | None] | None = None  # list -> number on the ballot, when it differs from the list key


def _add_contest(
    p: Package, election_id: str, day: date, path: str, spec: Spec, tally: Tally, source_id: str, note: str
) -> pl.DataFrame:
    contest = key("contest", day, spec.key)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind=spec.kind,
        office_id=spec.office,
        body_id=spec.body,
        district_area_id=spec.district,
        magnitude=spec.magnitude,
        round_number=1,
        electoral_system_id=spec.system,
        default_result_kind="counted",
    )
    p.name(contest, spec.en, "en", "official")
    p.name(contest, spec.es, "es", "native", False)
    p.add("slug", path=f"{path}/{spec.slug}", entity_id=contest, is_current=True)
    result_set = key("result_set", day, spec.key)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source_id,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
    options = {lista: key("option", day, spec.key, lista) for lista in sorted(tally.labels)}
    for lista, option in options.items():
        label, party_id = tally.labels[lista]
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind=spec.option,
            party_id=party_id,
            ballot_position=lista if tally.positions is None else tally.positions.get(lista),
            ballot_label=label,
        )
        if spec.kind == "office":
            if tally.winner is not None:
                kind = "elected" if lista == tally.winner else "lost"
                p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        elif tally.seats is not None:
            won = tally.seats.get(lista, 0)
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if won else "lost", seats=won)
    for lista, position, name in tally.members:
        tag = (day, spec.key, lista, position)
        person = key("person", *tag)
        p.add("person", id=person)
        p.name(person, title(name), "es", "official")
        candidacy = key("candidacy", *tag)
        is_list = spec.kind == "list_allocation"
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=options[lista],
            role=("list_member" if is_list else ("head" if position == 1 else "running_mate")),
            list_position=position if is_list else None,
            ballot_name=name,
            member_of_party_id=tally.labels[lista][1],
            is_independent=False,
        )
        if is_list:
            p.add("outcome", result_set_id=result_set, option_id=options[lista], candidacy_id=candidacy, kind="elected")
    for lista, position, name in tally.candidates:
        tag = (day, spec.key, "candidate", lista, position)
        person = key("person", *tag)
        p.add("person", id=person)
        p.name(person, title(name), "es", "official")
        p.add(
            "candidacy",
            id=key("candidacy", *tag),
            person_id=person,
            option_id=options[lista],
            role="list_member" if spec.kind == "list_allocation" else "head",
            list_position=position if spec.kind == "list_allocation" else None,
            ballot_name=name,
            member_of_party_id=tally.labels[lista][1],
            is_independent=False,
        )
    for lista, measure, value in tally.declared:
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=None if lista is None else options[lista],
            measure=measure,
            value=value,
        )
    option_ids = pl.DataFrame(
        {"lista": list(options), "option_id": list(options.values())},
        schema={"lista": pl.Int64, "option_id": pl.String},
    )
    parts = [
        tally.rows.join(option_ids, on="lista").select(
            result_set_id=pl.lit(result_set),
            unit_id="unit_id",
            option_id="option_id",
            measure=pl.lit(1, pl.Int16),
            value="votes",
        )
    ]
    accounting = tally.totals.with_columns(
        valid=pl.col("unit_id").replace_strict(
            dict(tally.rows.group_by("unit_id").agg(pl.col("votes").sum()).iter_rows()),
            default=0,
            return_dtype=pl.Int64,
        )
    )
    for measure, column in (
        (REGISTERED, "registered"),
        (CAST, "total"),
        (VALID, "valid"),
        (INVALID, "invalid"),
        (BLANK, "blank"),
    ):
        parts.append(
            accounting.filter(pl.col(column).is_not_null()).select(
                result_set_id=pl.lit(result_set),
                unit_id="unit_id",
                option_id=pl.lit(None, pl.String),
                measure=pl.lit(measure, pl.Int16),
                value=column,
            )
        )
    if tally.preferences is not None:
        parts.append(_candidates(p, day, spec, contest, result_set, tally, options))
        for lista, orden, value in tally.declared_preferences:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=key("option", day, spec.key, lista, orden),
                measure=PREFERENCE,
                value=value,
            )
    return pl.concat(parts)


def _candidates(
    p: Package, day: date, spec: Spec, contest: str, result_set: str, tally: Tally, options: dict[int, str]
) -> pl.DataFrame:
    assert tally.preferences is not None
    positions = tally.preferences.select("lista", "orden").unique().sort("lista", "orden")
    ids = []
    for lista, orden in positions.iter_rows():
        option = key("option", day, spec.key, lista, orden)
        ids.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            parent_option_id=options[lista],
            party_id=tally.labels[lista][1],
            ballot_position=orden,
            ballot_label=f"{tally.labels[lista][0]} #{orden}",
        )
    lookup = positions.with_columns(option_id=pl.Series(ids, dtype=pl.String))
    return tally.preferences.join(lookup, on=["lista", "orden"]).select(
        result_set_id=pl.lit(result_set),
        unit_id="unit_id",
        option_id="option_id",
        measure=pl.lit(PREFERENCE, pl.Int16),
        value="votes",
    )


# ---------------------------------------------------------------------------------------------------------------------
# The general election of 22 April 2018.

TITLES_2018 = {
    "Presidente y Vicepresidente": "PRESIDENTE",
    "Senadores": "SENADO",
    "Parlamentarios del MERCOSUR": "PARLASUR",
    "Parlamentarios del Mercosur": "PARLASUR",
    "Diputados": "DIPUTADOS",
    "Gobernador": "GOBERNADOR",
    "Junta Departamental": "JUNTA",
}
PRINTED_ROWS = {
    "VOTOS NULOS": INVALID,
    "VOTOS EN BLANCO": BLANK,
    "TOTAL DE VOTOS": CAST,
    "ELECTORES HABILITADOS": REGISTERED,
}
# Acronyms on the Tribunal's list of elected candidates, with the list each one ran under in 2018.
ELECTED_LISTS_2018 = {
    "ANR": (1, "PARTIDO COLORADO"),
    "PLRA": (2, "PARTIDO LIBERAL RADICAL AUTENTICO"),
    "GANAR": (5, "ALIANZA GANAR"),
    "UNACE": (7, "PARTIDO UNACE"),
    "PPQ": (8, "PARTIDO PATRIA QUERIDA"),
    "PEN": (9, "PARTIDO ENCUENTRO NACIONAL"),
    "PPH": (20, "PARTIDO HAGAMOS"),
    "FG": (40, "CONCERTACION NACIONAL FRENTE GUASU"),
    "PDP": (100, "PARTIDO DEMOCRATICO PROGRESISTA"),
    "CN": (999, "MOVIMIENTO CRUZADA NACIONAL"),
}


@functools.cache
def mesa2018() -> pl.DataFrame:
    """One row per table, contest, and list, with the table's ballots, blanks, and nulls repeated on each row."""
    frames = [
        frame.select(
            contest=pl.lit(name),
            dep=_int("COD_DEPARTAMENTO"),
            dis=_int("COD_DISTRITO"),
            zona=_int("COD_ZONA"),
            local=_int("COD_LOCAL"),
            mesa=_int("NRO_MESA"),
            total=_int("TOT_PROGRAMA"),
            blank=_int("VOT_BLANCO"),
            invalid=_int("VOT_NULO"),
            lista=_int("NRO_LISTA"),
            votes=_int("TOT_VOTO"),
        )
        for name, frame in sheets(GENERAL_2018_FILES["mesa"], 1).items()
    ]
    frame = pl.concat(frames)
    if frame.select(UNIT_KEY).null_count().sum_horizontal().item():
        raise ValueError("2018 table file: rows without a table code")
    return frame


@functools.cache
def tables2018() -> pl.DataFrame:
    """One row per table and contest, with the table's accounting."""
    frame = mesa2018()
    out = frame.group_by("contest", *UNIT_KEY).agg(
        pl.col("total").n_unique().alias("n"),
        pl.col("total").first(),
        pl.col("blank").first(),
        pl.col("invalid").first(),
        pl.col("votes").sum().alias("valid"),
    )
    if out.filter(pl.col("n") > 1).height:
        raise ValueError("2018 table file: a table prints two different totals")
    broken = out.filter(pl.col("valid") + pl.col("blank") + pl.col("invalid") != pl.col("total"))
    if broken.height:
        raise ValueError(f"2018 table file: {broken.height} tables do not add up to their printed total")
    if frame.select("contest", *UNIT_KEY, "lista").is_duplicated().any():
        raise ValueError("2018 table file: a list appears twice on a table")
    return out.drop("n")


@functools.cache
def printed2018() -> tuple[pl.DataFrame, pl.DataFrame]:
    """The Tribunal's department and national tables: (contest, dep, lista, label, value) and (contest, lista, label, value)."""
    department: list[tuple[str, int, int | None, str, int]] = []
    contest = ""
    for first, _, number, label, value in sheets(GENERAL_2018_FILES["departamento"], None)[
        "por departamento"
    ].iter_rows():
        if first in TITLES_2018:
            contest = TITLES_2018[first]
        elif first is not None and first.strip().isdigit() and label and not label.startswith("PORCENTAJE"):
            department.append(
                (contest, int(first), int(number) if number and number.strip() else None, label, int(value))
            )
    national: list[tuple[str, int | None, str, int]] = []
    for first, label, value in sheets(GENERAL_2018_FILES["nacional"], None)["RESUMEN-PAIS"].iter_rows():
        if first is not None and first.strip() in TITLES_2018:
            contest = TITLES_2018[first.strip()]
        elif label and value and value.strip().isdigit():
            national.append((contest, int(first) if first and first.strip().isdigit() else None, label, int(value)))
    return (
        pl.DataFrame(department, schema=["contest", "dep", "lista", "label", "value"], orient="row"),
        pl.DataFrame(national, schema=["contest", "lista", "label", "value"], orient="row"),
    )


def compare2018() -> dict[str, int]:
    """Sum the table file by department and nation, and compare it with the Tribunal's printed tables."""
    frame, tables = mesa2018(), tables2018()
    by_dep, by_nation = printed2018()
    problems: list[str] = []
    ours = {
        (c, d, lista): n
        for c, d, lista, n in frame.group_by("contest", "dep", "lista").agg(pl.col("votes").sum()).iter_rows()
    }
    for contest, dep, lista, label, value in by_dep.iter_rows():
        if lista is not None and ours.get((contest, dep, lista)) != value:
            problems.append(f"{contest} {dep} {lista} {label}: {value} != {ours.get((contest, dep, lista))}")
    by_table = tables.group_by("contest", "dep").agg(
        pl.col("blank").sum(), pl.col("invalid").sum(), pl.col("total").sum()
    )
    accounting = {(row["contest"], row["dep"]): row for row in by_table.iter_rows(named=True)}
    for contest, dep, lista, label, value in by_dep.iter_rows():
        measure = PRINTED_ROWS.get(label)
        column = {BLANK: "blank", INVALID: "invalid", CAST: "total"}.get(measure) if measure else None
        if column and accounting[(contest, dep)][column] != value:
            problems.append(f"{contest} {dep} {label}: {value} != {accounting[(contest, dep)][column]}")
    labels = {(c, d, lista): label.strip() for c, d, lista, label, _ in by_dep.iter_rows() if lista is not None}
    nation = Counter[tuple[str, str]]()
    for (contest, dep, lista), n in ours.items():
        nation[(contest, labels.get((contest, dep, lista), f"list {lista}"))] += n
    summed = {
        row["contest"]: row
        for row in tables.group_by("contest").agg(pl.col("blank").sum(), pl.col("invalid").sum()).iter_rows(named=True)
    }
    totals: Counter[str] = Counter()
    printed = Counter[tuple[str, str]]()
    for contest, lista, label, value in by_nation.iter_rows():
        if lista is not None:
            totals["lists"] += 1
            printed[(contest, label.strip())] += value
        elif label == "VOTOS NULOS" and summed[contest]["invalid"] != value:
            problems.append(f"nation {contest} nulls {value} != {summed[contest]['invalid']}")
        elif label == "VOTOS EN BLANCO" and summed[contest]["blank"] != value:
            problems.append(f"nation {contest} blanks {value} != {summed[contest]['blank']}")
    if printed != nation:
        problems.append(f"nation lists differ: {list((printed - nation) + (nation - printed))[:5]}")
    if problems:
        raise ValueError(f"2018: {len(problems)} differences from the printed tables: {problems[:10]}")
    totals["department rows"] = by_dep.height
    totals["tables"] = tables.select(UNIT_KEY).unique().height
    print(f"2018: {dict(totals)}", flush=True)
    return dict(totals)


def _dep_codes() -> dict[str, int]:
    return {normal(name): dep for dep, name in districts().select("dep", "dep_name").unique().iter_rows()}


@functools.cache
def elected2018() -> dict[str, list[tuple[int | None, str, str, str | None]]]:
    """The Tribunal's list of elected candidates: contest -> (department code, name, acronym, position)."""
    codes = _dep_codes()
    out: dict[str, list[tuple[int | None, str, str, str | None]]] = {}
    for sheet, rows in sheets(GENERAL_2018_FILES["electos"], None).items():
        found: list[tuple[int | None, str, str, str | None]] = []
        full = True
        for row in rows.iter_rows():
            first, second = row[0], row[1]
            if first is not None and second is None:
                full = "Suplente" not in first
            elif not full or first is None:
                continue
            elif sheet == "PTE_VICEPTE" and first in ("Presidente", "Vicepresidente"):
                found.append((None, second or "", row[2] or "", first))
            elif sheet == "GOBERNADORES" and normal(first) in codes:
                found.append((codes[normal(first)], row[1] or "", row[2] or "", None))
            elif sheet == "MIEMBROS_JUNTA_DPTAL" and normal(first) in codes:
                found.append((codes[normal(first)], row[2] or "", row[3] or "", row[1]))
            elif sheet == "DIPUTADOS" and first.strip().isdigit():
                found.append((codes[normal(row[3] or "")], row[1] or "", row[2] or "", first))
            elif sheet in ("SENADORES", "PARLAMENTARIOS_MERCOSUR") and first.strip().isdigit():
                found.append((None, row[1] or "", row[2] or "", first))
        out[sheet] = found
    return out


PRESIDENCY = key("office", "president")
SENATE = key("body", "senate")
CHAMBER = key("body", "chamber")
PARLASUR = key("body", "parlasur")
PRESIDENT_SYSTEM = key("electoral_system", "president", 1992)
SENATE_SYSTEM = key("electoral_system", "senate", 1992)
CHAMBER_SYSTEM = key("electoral_system", "chamber", 1992)
BOARD_SYSTEM = key("electoral_system", "board", 1992)
GOVERNOR_SYSTEM = key("electoral_system", "governor", 1992)
PARLASUR_SYSTEM = key("electoral_system", "parlasur", 2008)
COUNCIL_SYSTEM_BLOCKED = key("electoral_system", "council", 1991)
CLOSED = {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}}
GENERAL_SYSTEMS = (
    (
        PRESIDENT_SYSTEM,
        date(1992, 6, 20),
        None,
        {"formula": "fptp"},
        "The ticket with the most votes in a single round is elected President and Vice President.",
    ),
    (
        SENATE_SYSTEM,
        date(1992, 6, 20),
        None,
        CLOSED,
        "The 45 senators are elected from closed national lists by D'Hondt.",
    ),
    (
        CHAMBER_SYSTEM,
        date(1992, 6, 20),
        None,
        CLOSED,
        "Deputies are elected from closed lists in each department by D'Hondt.",
    ),
    (
        BOARD_SYSTEM,
        date(1992, 6, 20),
        None,
        CLOSED,
        "Departmental board members are elected from closed lists in each department by D'Hondt.",
    ),
    (
        GOVERNOR_SYSTEM,
        date(1992, 6, 20),
        None,
        {"formula": "fptp"},
        "The candidate with the most votes in a department is elected governor.",
    ),
    (
        PARLASUR_SYSTEM,
        date(2008, 1, 1),
        None,
        CLOSED,
        "Paraguay's members of the Mercosur Parliament are elected from closed national lists by D'Hondt.",
    ),
    (
        COUNCIL_SYSTEM_BLOCKED,
        date(1991, 1, 1),
        date(2021, 1, 1),
        CLOSED,
        "Council seats were divided among closed lists by D'Hondt, in the order the lists printed.",
    ),
)
GENERAL_NOTE = (
    "Counted by the Tribunal Superior de Justicia Electoral per polling table (mesa receptora de votos), as published "
    "in its per-table results workbook for the general elections of 22 April 2018. Valid votes are the votes for "
    "lists; blank and null ballots are the table's own counts, and ballots cast are the table total the source "
    "prints. Tables abroad have no place in the archive's geography. The department figures the Tribunal prints "
    "agree with the sum of the tables for every list, and the national figures agree with the sum of the "
    "departments."
)


def _dep_names() -> dict[int, str]:
    return {dep: title(name) for dep, name in districts().select("dep", "dep_name").unique().iter_rows()}


def _label_2018(label: str) -> str:
    return clean(label)


@functools.cache
def labels2018() -> dict[tuple[str, int | None], dict[int, str]]:
    """Ballot labels by contest and department; national contests use None."""
    by_dep, by_nation = printed2018()
    out: dict[tuple[str, int | None], dict[int, str]] = {}
    for contest, dep, lista, label, _ in by_dep.iter_rows():
        if lista is not None:
            out.setdefault((contest, dep), {})[lista] = _label_2018(label)
    for contest, lista, label, _ in by_nation.iter_rows():
        if lista is not None and contest in ("PRESIDENTE", "SENADO", "PARLASUR"):
            out.setdefault((contest, None), {})[lista] = _label_2018(label)
    return out


def _party_labels(labels: dict[int, str]) -> dict[int, tuple[str, str | None]]:
    return {lista: (label, party(label)) for lista, label in labels.items()}


def _printed(contest: str, dep: int | None, labels: dict[int, str]) -> tuple[tuple[int | None, int, int], ...]:
    by_dep, by_nation = printed2018()
    out: list[tuple[int | None, int, int]] = []
    if dep is None:
        for c, lista, label, value in by_nation.iter_rows():
            if c == contest and lista is not None:
                out.append((lista, 1, value))
    else:
        for c, d, lista, label, value in by_dep.iter_rows():
            if c == contest and d == dep:
                measure = PRINTED_ROWS.get(label)
                if lista is not None:
                    out.append((lista, 1, value))
                elif measure is not None and measure != CAST:
                    out.append((None, measure, value))
    return tuple(out)


def _elected(
    contest: str, dep: int | None, labels: dict[int, str]
) -> tuple[dict[int, int], tuple[tuple[int, int, str], ...]] | None:
    """Seats and members the Tribunal declared for a contest, when every acronym is tied to a list."""
    sheet = {
        "PRESIDENTE": "PTE_VICEPTE",
        "SENADO": "SENADORES",
        "PARLASUR": "PARLAMENTARIOS_MERCOSUR",
        "DIPUTADOS": "DIPUTADOS",
        "GOBERNADOR": "GOBERNADORES",
        "JUNTA": "MIEMBROS_JUNTA_DPTAL",
    }[contest]
    seats: Counter[int] = Counter()
    members: list[tuple[int, int, str]] = []
    for place, name, sigla, position in elected2018()[sheet]:
        if place != dep:
            continue
        if sigla not in ELECTED_LISTS_2018:
            return None
        lista, expected = ELECTED_LISTS_2018[sigla]
        if normal(labels.get(lista, "")) != normal(expected):
            return None
        seats[lista] += 1
        rank = {"Presidente": 1, "Vicepresidente": 2}.get(position or "") or int(position or seats[lista])
        members.append((lista, rank, name))
    return dict(seats), tuple(members)


def _accept_roll(p: Package, tables: pl.DataFrame, mismatched: int) -> None:
    over = tables.filter(pl.col("registered").is_not_null(), pl.col("total") > pl.col("registered")).height
    if over:
        p.accept(
            "accounting.turnout",
            f"{over} table counts print more ballots than voters on the table's roll in the Tribunal's roll file; "
            "both are stored as printed",
        )
    if mismatched:
        p.accept(
            "accounting.declared",
            f"in {mismatched} result sets, the registered voters the Tribunal prints for a department differ from the "
            "sum of the table rolls in its roll file; both are stored as printed",
        )


def general_2018() -> Package:
    day = GENERAL_2018
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "Paraguay's general elections of 22 April 2018 (President, Senate, Mercosur Parliament, Chamber of Deputies, "
        "governors, and departmental boards), by polling table, from the Tribunal Superior de Justicia Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/history"],
    )
    p.external |= {PARAGUAY, COUNTRY}
    source_id = key("source", SOURCE, "general-2018")
    _source(
        p,
        source_id,
        "Resultados de las Elecciones Generales 2018: archivos por mesa, por departamento y candidatos electos",
        f"{SITE}/elecciones-generales-2018",
        GENERAL_2018_FILES.values(),
    )
    p.uses(raw.cached(SOURCE, ROLL))
    election_id = key("election", day)
    path = f"py/{day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=PARAGUAY,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, "2018 Paraguayan general election", "en", "official")
    p.name(election_id, "Elecciones generales 2018", "es", "native", False)

    registered = roll(2018)
    tables = tables2018().join(registered, on=UNIT_KEY, how="left")
    units = _old_units(p, day, tables.select(UNIT_KEY).unique().sort(UNIT_KEY))
    unit_ids = units.select(*UNIT_KEY, "unit_id")
    mesa = mesa2018().join(unit_ids, on=UNIT_KEY)
    tables = tables.join(unit_ids, on=UNIT_KEY)
    names = _dep_names()
    slugs = {dep: changeset.slug(name) for dep, name in names.items()}
    values: list[pl.DataFrame] = []
    left: Counter[str] = Counter()
    mismatched = 0

    def contest(code: str, dep: int | None, spec: Spec) -> None:
        labels = labels2018()[(code, dep)]
        part = mesa.filter(pl.col("contest") == code, *([] if dep is None else [pl.col("dep") == dep]))
        found = set(part["lista"].unique())
        labels = {lista: labels.get(lista, f"Lista {lista}") for lista in sorted(found | set(labels))}
        declared = _elected(code, dep, labels)
        if declared is None:
            left[code] += 1
        spec = spec._replace(
            magnitude=spec.magnitude
            if spec.magnitude is not None
            else (sum(declared[0].values()) if declared else None)
        )
        tally = Tally(
            rows=part.select("unit_id", "lista", "votes"),
            totals=tables.filter(pl.col("contest") == code, *([] if dep is None else [pl.col("dep") == dep])).select(
                "unit_id", "registered", "total", "blank", "invalid"
            ),
            labels=_party_labels(labels),
            seats=None if declared is None or code in ("PRESIDENTE", "GOBERNADOR") else declared[0],
            winner=None if declared is None or code not in ("PRESIDENTE", "GOBERNADOR") else next(iter(declared[0])),
            members=() if declared is None else declared[1],
            declared=_printed(code, dep, labels),
        )
        nonlocal mismatched
        printed_roll = next((v for lista, m, v in tally.declared if lista is None and m == REGISTERED), None)
        if printed_roll is not None and printed_roll != tally.totals["registered"].sum():
            mismatched += 1
        values.append(_add_contest(p, election_id, day, path, spec, tally, source_id, GENERAL_NOTE))

    contest(
        "PRESIDENTE",
        None,
        Spec(
            "president",
            "office",
            "2018 Paraguayan presidential election",
            "Elección presidencial 2018",
            "president",
            COUNTRY,
            PRESIDENCY,
            None,
            PRESIDENT_SYSTEM,
            1,
            "ticket",
        ),
    )
    contest(
        "SENADO",
        None,
        Spec(
            "senate",
            "list_allocation",
            "2018 Paraguayan Senate election",
            "Elección de senadores 2018",
            "senate",
            COUNTRY,
            None,
            SENATE,
            SENATE_SYSTEM,
            45,
            "list",
        ),
    )
    contest(
        "PARLASUR",
        None,
        Spec(
            "parlasur",
            "list_allocation",
            "2018 election of Paraguay's members of the Mercosur Parliament",
            "Elección de parlamentarios del Mercosur 2018",
            "mercosur-parliament",
            COUNTRY,
            None,
            PARLASUR,
            PARLASUR_SYSTEM,
            18,
            "list",
        ),
    )
    for dep in sorted({d for c, d in labels2018() if c == "DIPUTADOS" and d is not None}):
        place, district = names[dep], area("py.departamento", str(dep))
        contest(
            "DIPUTADOS",
            dep,
            Spec(
                f"deputies:{dep}",
                "list_allocation",
                f"2018 Chamber of Deputies election, {place}",
                f"Elección de diputados 2018, {place}",
                f"deputies-{slugs[dep]}",
                district,
                None,
                CHAMBER,
                CHAMBER_SYSTEM,
                None,
                "list",
            ),
        )
    for dep in sorted({d for c, d in labels2018() if c == "GOBERNADOR" and d is not None}):
        place, district = names[dep], area("py.departamento", str(dep))
        contest(
            "GOBERNADOR",
            dep,
            Spec(
                f"governor:{dep}",
                "office",
                f"2018 governor election, {place}",
                f"Elección de gobernador 2018, {place}",
                f"governor-{slugs[dep]}",
                district,
                key("office", "governor", dep),
                None,
                GOVERNOR_SYSTEM,
                1,
                "candidate",
            ),
        )
    for dep in sorted({d for c, d in labels2018() if c == "JUNTA" and d is not None}):
        place, district = names[dep], area("py.departamento", str(dep))
        contest(
            "JUNTA",
            dep,
            Spec(
                f"board:{dep}",
                "list_allocation",
                f"2018 {place} Departmental Board election",
                f"Elección de la Junta Departamental de {place} 2018",
                f"departmental-board-{slugs[dep]}",
                district,
                None,
                key("body", "board", dep),
                BOARD_SYSTEM,
                None,
                "list",
            ),
        )
    facts = pl.concat(values)
    p.frame(
        "result_value",
        facts.with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    _accept_roll(p, tables, mismatched)
    if left:
        p.add(
            "note",
            id=key("note", election_id, "seats"),
            entity_id=election_id,
            kind="caveat",
            body_md="Seats and elected members are stored only for contests where every acronym on the Tribunal's "
            "list of elected candidates is tied to a list on the ballot: "
            + ", ".join(f"{n} {c.lower()} contests are left without them" for c, n in sorted(left.items()))
            + ".",
            lang="en",
        )
    return p


# ---------------------------------------------------------------------------------------------------------------------
# The municipal election of 10 October 2021.

SPECIAL = {9990: "blank", 9991: "invalid", 9992: "total"}
MUNICIPAL_NOTE = (
    "Counted by the Tribunal Superior de Justicia Electoral per polling table (mesa receptora de votos), as published "
    "in its per-table results workbook for the municipal elections of 10 October 2021. Valid votes are the votes for "
    "lists; blank and null ballots and the ballots cast are the table's own counts. Registered voters are the "
    "table's roll in the Tribunal's roll and turnout file. In the council contest, each list's votes are the sum of "
    "its candidates' preference votes, which the source prints per table and position on the list; the preference "
    "votes are stored on a candidate option under the list. The Tribunal prints no candidate names for the council "
    "lists, so the candidate options are identified by their position."
)


@functools.cache
def mesa2021() -> pl.DataFrame:
    frame = next(iter(sheets(MUNICIPAL_2021_FILES["mesa"]).values()))
    out = frame.select(
        contest=pl.when(pl.col("CANDIDATURA") == "INTENDENTE").then(pl.lit("mayor")).otherwise(pl.lit("council")),
        dep=_int("DEPTO"),
        dis=_int("COD DISTRITO"),
        zona=_int("COD ZONA"),
        local=_int("COD LOCAL"),
        mesa=_int("NRO MESA"),
        lista=_int("LISTA"),
        sigla=pl.col("SIGLA").str.strip_chars(),
        votes=_int("VOTOS"),
    )
    if out.filter(pl.col("contest").is_null()).height:
        raise ValueError("2021 table file: an unknown candidacy")
    return out


@functools.cache
def split2021() -> tuple[pl.DataFrame, pl.DataFrame]:
    """The table file's list rows, and its per-table accounting (blank, null, and total rows)."""
    frame = mesa2021()
    lists = frame.filter(pl.col("lista") < 9000)
    special = frame.filter(pl.col("lista") >= 9000).with_columns(
        kind=pl.col("lista").replace_strict(SPECIAL, return_dtype=pl.String)
    )
    wide = special.pivot(on="kind", index=["contest", *UNIT_KEY], values="votes")
    valid = lists.group_by("contest", *UNIT_KEY).agg(pl.col("votes").sum().alias("valid"))
    tables = wide.join(valid, on=["contest", *UNIT_KEY], how="left").with_columns(pl.col("valid").fill_null(0))
    broken = tables.filter(pl.col("valid") + pl.col("blank") + pl.col("invalid") != pl.col("total"))
    if broken.height:
        raise ValueError(f"2021 table file: {broken.height} tables do not add up to their printed total")
    if lists.select("contest", *UNIT_KEY, "lista").is_duplicated().any():
        raise ValueError("2021 table file: a list appears twice on a table")
    return lists, tables


@functools.cache
def preferences2021() -> pl.DataFrame:
    frames = [
        frame.select(
            dep=_int("DEPARTAMENTO"),
            dis=_int("DISTRITO"),
            zona=_int("ZONA"),
            local=_int("LOCAL"),
            mesa=_int("NRO_MESA"),
            lista=_int("NRO_LISTA"),
            orden=_int("ORDEN_LISTA"),
            votes=_int("VOTOS"),
        )
        for frame in sheets(MUNICIPAL_2021_FILES["preferentes"]).values()
        if "NRO_LISTA" in frame.columns
    ]
    return pl.concat(frames)


@functools.cache
def printed2021() -> pl.DataFrame:
    frame = next(iter(sheets(MUNICIPAL_2021_FILES["distrito"], 1).values()))
    return frame.select(
        dep=_int("DEPTO"), dis=_int("DISTRITO"), lista=_int("LISTA"), orden=_int("ORDEN_LISTA"), votes=_int("VOTOS")
    )


@functools.cache
def mayors2021() -> dict[tuple[int, int], dict[str, str]]:
    frame = next(iter(sheets(MUNICIPAL_2021_FILES["intendentes"], 1).values()))
    return {(int(row["DEPTO"]), int(row["DISTRITO"])): row for row in frame.iter_rows(named=True)}


def compare2021() -> dict[str, int]:
    """Check the table file against the district preference file and the list of elected mayors."""
    lists, tables = split2021()
    prefs, printed = preferences2021(), printed2021()
    council = lists.filter(pl.col("contest") == "council")
    by_mesa = prefs.group_by(*UNIT_KEY, "lista").agg(pl.col("votes").sum().alias("preferences"))
    joined = council.join(by_mesa, on=[*UNIT_KEY, "lista"], how="full", coalesce=True)
    wrong = joined.filter(pl.col("votes").fill_null(0) != pl.col("preferences").fill_null(0))
    problems = (
        [f"{wrong.height} table lists whose preference votes do not sum to the list's votes"] if wrong.height else []
    )
    by_district = prefs.group_by("dep", "dis", "lista", "orden").agg(pl.col("votes").sum())
    gap = by_district.join(printed, on=["dep", "dis", "lista", "orden"], how="full", coalesce=True, suffix="_printed")
    gap = gap.filter(pl.col("votes").fill_null(0) != pl.col("votes_printed").fill_null(0))
    if gap.height:
        problems.append(f"{gap.height} candidate positions differ from the district preference file")
    mayor_totals = lists.filter(pl.col("contest") == "mayor").group_by("dep", "dis", "lista").agg(pl.col("votes").sum())
    for (dep, dis), row in mayors2021().items():
        here = mayor_totals.filter(pl.col("dep") == dep, pl.col("dis") == dis)
        top = here["votes"].max()
        if int(row["LISTA"]) not in set(here.filter(pl.col("votes") == top)["lista"]):
            problems.append(f"{dep}:{dis}: the elected mayor is not the most voted candidate")
    if len(mayors2021()) != mayor_totals.select("dep", "dis").unique().height:
        problems.append("the list of elected mayors and the table file cover different districts")
    if problems:
        raise ValueError(f"2021: {problems}")
    out = {
        "tables": tables.select(UNIT_KEY).unique().height,
        "mayor votes": int(lists.filter(pl.col("contest") == "mayor")["votes"].sum()),
        "council votes": int(council["votes"].sum()),
        "preference votes": int(prefs["votes"].sum()),
    }
    print(f"2021: {out}", flush=True)
    return out


def _party_2021(lista: int, sigla: str) -> str | None:
    name = NATIONAL_PARTIES.get((lista, sigla))
    return None if name is None else party(name)


def municipal_2021() -> Package:
    day = MUNICIPAL_2021
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "Paraguay's municipal elections of 10 October 2021 (mayors and municipal councils in 261 municipalities), by "
        "polling table, from the Tribunal Superior de Justicia Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/history"],
    )
    p.external |= {PARAGUAY}
    source_id = key("source", SOURCE, "municipal-2021")
    _source(
        p,
        source_id,
        "Resultados de las Elecciones Municipales 2021: votos por mesa, votos preferentes y intendentes electos",
        f"{SITE}/elecciones-municipales-2021",
        MUNICIPAL_2021_FILES.values(),
    )
    p.uses(raw.cached(SOURCE, ROLL))
    election_id = key("election", day)
    path = f"py/{day}-municipal"
    p.add(
        "election",
        id=election_id,
        polity_id=PARAGUAY,
        kind="local",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, "2021 Paraguayan municipal elections", "en", "official")
    p.name(election_id, "Elecciones municipales 2021", "es", "native", False)

    lists, tables = split2021()
    tables = tables.join(roll(2021), on=UNIT_KEY, how="left")
    units = _old_units(p, day, tables.select(UNIT_KEY).unique().sort(UNIT_KEY))
    unit_ids = units.select(*UNIT_KEY, "unit_id")
    lists = lists.join(unit_ids, on=UNIT_KEY)
    tables = tables.join(unit_ids, on=UNIT_KEY)
    prefs = preferences2021().join(unit_ids, on=UNIT_KEY)
    printed = printed2021()
    slugs = _slugs()
    places = {(dep, dis): title(name) for dep, _, dis, name in districts().iter_rows()}
    mayors = mayors2021()
    values: list[pl.DataFrame] = []
    for dep, dis in sorted(mayors):
        place = places[(dep, dis)]
        district = area("py.distrito", f"{dep}:{dis}")
        for code in ("mayor", "council"):
            part = lists.filter(pl.col("contest") == code, pl.col("dep") == dep, pl.col("dis") == dis)
            sigla = {
                lista: label
                for lista, label in part.group_by("lista", "sigla")
                .len()
                .sort("len", descending=True)
                .unique("lista", keep="first", maintain_order=True)
                .select("lista", "sigla")
                .iter_rows()
            }
            labels = {lista: (label, _party_2021(lista, label)) for lista, label in sigla.items()}
            totals = tables.filter(pl.col("contest") == code, pl.col("dep") == dep, pl.col("dis") == dis).select(
                "unit_id", "registered", "total", "blank", "invalid"
            )
            if code == "mayor":
                winner = mayors[(dep, dis)]
                won = int(winner["LISTA"])
                named = party(winner["DESCRIPCIÓN"]) if winner["DESCRIPCIÓN"] else None
                labels[won] = (labels[won][0], labels[won][1] or named)
                spec = Spec(
                    f"mayor:{dep}:{dis}",
                    "office",
                    f"Mayor of {place}",
                    f"Intendente municipal de {place}",
                    f"{slugs[(dep, dis)]}-mayor",
                    district,
                    office(dep, dis),
                    None,
                    MAYOR_SYSTEM,
                    1,
                    "candidate",
                )
                tally = Tally(
                    rows=part.select("unit_id", "lista", "votes"),
                    totals=totals,
                    labels=labels,
                    winner=won,
                    members=((won, 1, f"{winner['NOMBRE']} {winner['APELLIDO']}"),),
                )
            else:
                here = prefs.filter(pl.col("dep") == dep, pl.col("dis") == dis)
                said = printed.filter(pl.col("dep") == dep, pl.col("dis") == dis)
                spec = Spec(
                    f"council:{dep}:{dis}",
                    "list_allocation",
                    f"{place} Municipal Council",
                    f"Junta municipal de {place}",
                    f"{slugs[(dep, dis)]}-council",
                    district,
                    None,
                    body(dep, dis),
                    COUNCIL_SYSTEM,
                    None,
                    "list",
                )
                tally = Tally(
                    rows=part.select("unit_id", "lista", "votes"),
                    totals=totals,
                    labels=labels,
                    declared=tuple(
                        (lista, 1, votes)
                        for lista, votes in said.group_by("lista").agg(pl.col("votes").sum()).sort("lista").iter_rows()
                    ),
                    preferences=here.select("unit_id", "lista", "orden", "votes"),
                    declared_preferences=tuple(
                        said.select("lista", "orden", "votes").sort("lista", "orden").iter_rows()
                    ),
                )
            values.append(_add_contest(p, election_id, day, path, spec, tally, source_id, MUNICIPAL_NOTE))
    facts = pl.concat(values)
    p.frame(
        "result_value",
        facts.with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    _accept_roll(p, tables, 0)
    return p


# ---------------------------------------------------------------------------------------------------------------------
# The municipal election of 15 November 2015, from the Tribunal's printed summary by district.

DISTRICT_LINE = re.compile(
    r"Distrito :\s+(\d+)\s+(.+?)\s{2,}Total de Mesas Procesadas del Distrito :\s+([\d.]+)\s+de\s+([\d.]+)"
)
NUMBERS_LINE = re.compile(r"^\s*[\d.]+(\s+[\d.]+){2,}\s*$")
MUNICIPAL_2015_NOTE = (
    "Counted by the Tribunal Superior de Justicia Electoral and printed in its summary of results by district for the "
    "municipal elections of 15 November 2015. The summary gives each district's votes for each list and its blank "
    "and null ballots and total; it is the finest level the Tribunal published for this election in the files the "
    "archive holds, so each district is one reporting unit. Registered voters are not printed. Candidate names come "
    "from the Tribunal's list of candidates published on 9 November 2015; the lists are matched to the summary by "
    "district and list number."
)


@functools.cache
def pdf_text(url: str) -> str:
    """The text of an archived PDF, in the reading order of its layout (needs poppler's pdftotext)."""
    result = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    )
    return result.stdout


def _count(token: str) -> int:
    return int(token.replace(".", ""))


@functools.cache
def results2015() -> tuple[pl.DataFrame, pl.DataFrame]:
    """(dep, dis, contest, lista, sigla, votes) per list, and (dep, dis, contest, blank, invalid, total) per district."""
    lines = pdf_text(MUNICIPAL_2015_FILES["resultados"]).splitlines()
    dep, contest = -1, ""
    names: dict[int, str] = {}
    lists: list[tuple[int, int, str, int, str, int]] = []
    totals: list[tuple[int, int, str, int, int, int, int, int]] = []
    place: tuple[int, str, int, int] | None = None
    pending: tuple[tuple[int, int, str], list[int], list[str], list[int]] | None = None
    header: list[int] = []
    siglas: list[str] = []
    district_sums: Counter[tuple[int, str]] = Counter()
    department: dict[tuple[int, str], int] = {}
    for line in lines:
        found = re.match(r"\s*Departamento :\s+(\d+)\s+(.+?)\s*$", line)
        if found:
            dep, names[dep] = int(found.group(1)), found.group(2)
            continue
        found = re.match(r"\s*Candidatura :\s+(\d)\s+(INTENDENTE|JUNTA MUNICIPAL)", line)
        if found:
            contest = "mayor" if found.group(2) == "INTENDENTE" else "council"
            place = None
            if pending is None or pending[0][2] != contest:
                header, siglas, pending = [], [], None
            continue
        found = DISTRICT_LINE.search(line)
        if found:
            place = (int(found.group(1)), found.group(2).strip(), _count(found.group(3)), _count(found.group(4)))
            if pending is None or pending[0] != (dep, place[0], contest):
                header, siglas, pending = [], [], None
            continue
        if line.lstrip().startswith("L."):
            header += [int(n) for n in re.findall(r"L\.\s*(\d+)", line)]
        elif NUMBERS_LINE.match(line):
            figures = [_count(token) for token in line.split()]
            if place is None:
                department[(dep, contest)] = figures[-1]
                continue
            dis, _, done, planned = place
            if pending is not None and pending[0] == (dep, dis, contest) and len(figures) == 3:
                header, siglas, figures = pending[1], pending[2], [*pending[3], *figures]
                pending = None
            elif len(figures) == len(header):
                # A wide table prints its lists on one page and the blank, null, and total counts on the next.
                pending = ((dep, dis, contest), header, siglas, figures)
                continue
            if len(figures) != len(header) + 3:
                raise ValueError(
                    f"2015 summary: {dep}:{dis} {contest} prints {len(figures)} figures for {len(header)} lists"
                )
            if sum(figures[:-1]) != figures[-1]:
                raise ValueError(f"2015 summary: {dep}:{dis} {contest} does not add up to its printed total")
            aligned = siglas if len(siglas) == len(header) else [""] * len(header)
            for lista, sigla, votes in zip(header, aligned, figures, strict=False):
                lists.append((dep, dis, contest, lista, sigla, votes))
            totals.append((dep, dis, contest, figures[-3], figures[-2], figures[-1], done, planned))
            district_sums[(dep, contest)] += figures[-1]
            place = None
        elif header and place is not None and line.strip() and "Blancos" not in line and line.strip() != "Total":
            siglas = line.split()
    wrong = {k: (n, department.get(k)) for k, n in district_sums.items() if department.get(k) not in (None, n)}
    if wrong:
        raise ValueError(f"2015 summary: department totals differ from the districts: {wrong}")
    return (
        pl.DataFrame(lists, schema=["dep", "dis", "contest", "lista", "sigla", "votes"], orient="row"),
        pl.DataFrame(
            totals, schema=["dep", "dis", "contest", "blank", "invalid", "total", "done", "planned"], orient="row"
        ),
    )


@functools.cache
def district_names2015() -> dict[tuple[int, int], tuple[str, str]]:
    out: dict[tuple[int, int], tuple[str, str]] = {}
    dep, dep_name = -1, ""
    for line in pdf_text(MUNICIPAL_2015_FILES["resultados"]).splitlines():
        found = re.match(r"\s*Departamento :\s+(\d+)\s+(.+?)\s*$", line)
        if found:
            dep, dep_name = int(found.group(1)), found.group(2)
        found = DISTRICT_LINE.search(line)
        if found:
            out[(dep, int(found.group(1)))] = (dep_name, found.group(2).strip())
    return out


@functools.cache
def candidates2015() -> pl.DataFrame:
    """The Tribunal's candidate list of 9 November 2015, with the district codes of the results summary."""
    codes = {(normal(a), normal(b)): k for k, (a, b) in district_names2015().items()}
    frame = sheets(MUNICIPAL_2015_FILES["candidatos"])["Hoja1"]
    keys = [codes[(normal(a), normal(b))] for a, b in frame.select("DEPARTAMENTO", "DISTRITO").iter_rows()]
    return frame.select(
        lista=_int("LISTA"),
        party=pl.col("PARTIDO/MOV./ALIANZA/CONC.").map_elements(clean, return_dtype=pl.String),
        contest=pl.when(pl.col("CANDIDATURA") == "INTENDENTE").then(pl.lit("mayor")).otherwise(pl.lit("council")),
        tier=pl.col("TITSUP").str.strip_chars(),
        orden=_int("ORDEN"),
        name=pl.col("NOMAPE").map_elements(clean, return_dtype=pl.String),
    ).with_columns(
        dep=pl.Series([k[0] for k in keys], dtype=pl.Int64), dis=pl.Series([k[1] for k in keys], dtype=pl.Int64)
    )


def compare2015() -> dict[str, int]:
    _, totals = results2015()
    out = {
        "districts": totals.select("dep", "dis").unique().height,
        "mayor votes": int(totals.filter(pl.col("contest") == "mayor")["total"].sum()),
        "council votes": int(totals.filter(pl.col("contest") == "council")["total"].sum()),
    }
    unknown = set(totals.select("dep", "dis").iter_rows()) - set(districts().select("dep", "dis").iter_rows())
    if unknown:
        raise ValueError(f"2015 summary: districts without a place in the geography: {sorted(unknown)[:5]}")
    print(f"2015: {out}", flush=True)
    return out


def municipal_2015() -> Package:
    day = MUNICIPAL_2015
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "Paraguay's municipal elections of 15 November 2015 (mayors and municipal councils), by district, from the "
        "Tribunal Superior de Justicia Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/history"],
    )
    p.external |= {PARAGUAY}
    source_id = key("source", SOURCE, "municipal-2015")
    _source(
        p,
        source_id,
        "Elecciones Municipales 2015: resumen de resultados por distrito y candidatos por distrito",
        f"{SITE}/elecciones-municipales-2015",
        MUNICIPAL_2015_FILES.values(),
    )
    election_id = key("election", day)
    path = f"py/{day}-municipal"
    p.add(
        "election",
        id=election_id,
        polity_id=PARAGUAY,
        kind="local",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, "2015 Paraguayan municipal elections", "en", "official")
    p.name(election_id, "Elecciones municipales 2015", "es", "native", False)

    lists, totals = results2015()
    candidates = candidates2015()
    frame = totals.select("dep", "dis").unique().sort("dep", "dis")
    codes = frame.with_columns(code=pl.concat_str(pl.lit(str(day)), "dep", "dis", separator=":")).with_columns(
        unit_id=pl.col("code").map_elements(lambda code: key("unit", code), return_dtype=pl.String)
    )
    p.frame(
        "reporting_unit",
        codes.select(
            id="unit_id",
            polity_id=pl.lit(PARAGUAY),
            scheme_code=pl.lit("py.distrito"),
            code="code",
            area_relation=pl.lit("equals"),
            area_id=pl.struct("dep", "dis").map_elements(
                lambda row: area("py.distrito", f"{row['dep']}:{row['dis']}"), return_dtype=pl.String
            ),
            kind=pl.lit("aggregate"),
        ),
    )
    unit_of = codes.select("dep", "dis", "unit_id")
    slugs = _slugs()
    places = {(dep, dis): title(name) for dep, _, dis, name in districts().iter_rows()}
    values: list[pl.DataFrame] = []
    for dep, dis in sorted(places):
        if (dep, dis) not in set(frame.iter_rows()):
            continue
        place = places[(dep, dis)]
        district = area("py.distrito", f"{dep}:{dis}")
        for code in ("mayor", "council"):
            part = lists.filter(pl.col("contest") == code, pl.col("dep") == dep, pl.col("dis") == dis)
            named = candidates.filter(pl.col("contest") == code, pl.col("dep") == dep, pl.col("dis") == dis)
            parties = dict(named.select("lista", "party").unique().iter_rows())
            sigla = dict(part.select("lista", "sigla").iter_rows())
            labels = {
                lista: (
                    parties.get(lista) or sigla.get(lista) or f"Lista {lista}",
                    party(parties[lista]) if lista in parties else None,
                )
                for lista in sorted(set(part["lista"]))
            }
            mine = totals.filter(pl.col("contest") == code, pl.col("dep") == dep, pl.col("dis") == dis).join(
                unit_of, on=["dep", "dis"]
            )
            rows = part.join(unit_of, on=["dep", "dis"]).select("unit_id", "lista", "votes")
            account = mine.select(
                "unit_id", registered=pl.lit(None, pl.Int64), total="total", blank="blank", invalid="invalid"
            )
            if code == "mayor":
                spec = Spec(
                    f"mayor:{dep}:{dis}",
                    "office",
                    f"Mayor of {place}",
                    f"Intendente municipal de {place}",
                    f"{slugs[(dep, dis)]}-mayor",
                    district,
                    office(dep, dis),
                    None,
                    MAYOR_SYSTEM,
                    1,
                    "candidate",
                )
                heads = tuple(
                    (lista, 1, name)
                    for lista, name in named.filter(pl.col("tier") == "")
                    .select("lista", "name")
                    .unique("lista")
                    .iter_rows()
                    if lista in labels
                )
            else:
                spec = Spec(
                    f"council:{dep}:{dis}",
                    "list_allocation",
                    f"{place} Municipal Council",
                    f"Junta municipal de {place}",
                    f"{slugs[(dep, dis)]}-council",
                    district,
                    None,
                    body(dep, dis),
                    COUNCIL_SYSTEM_BLOCKED,
                    None,
                    "list",
                )
                heads = tuple(
                    (lista, orden, name)
                    for lista, orden, name in named.filter(pl.col("tier") == "TITULARES")
                    .select("lista", "orden", "name")
                    .unique(["lista", "orden"])
                    .sort("lista", "orden")
                    .iter_rows()
                    if lista in labels
                )
            tally = Tally(rows=rows, totals=account, labels=labels, candidates=heads)
            values.append(_add_contest(p, election_id, day, path, spec, tally, source_id, MUNICIPAL_2015_NOTE))
            short = mine.filter(pl.col("done") != pl.col("planned"))
            if short.height:
                result_set = key("result_set", day, spec.key)
                p.add(
                    "note",
                    id=key("note", result_set, "tables"),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"The summary counts {short['done'].item()} of the district's {short['planned'].item()} "
                    "tables; the figures are stored as printed.",
                    lang="en",
                )
    facts = pl.concat(values)
    p.frame(
        "result_value",
        facts.with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    return p


# ---------------------------------------------------------------------------------------------------------------------
# What the earlier elections share.


def party_names() -> dict[str, str]:
    """Spellings of the parties the earlier elections name, by normalized name, that the 2026 registry lacks."""
    found: dict[str, Counter[str]] = {}

    def add(name: str) -> None:
        if clean(name):
            found.setdefault(normal(name), Counter())[clean(name)] += 1

    for labels in labels2018().values():
        for label in labels.values():
            add(label)
    for row in mayors2021().values():
        add(row["DESCRIPCIÓN"] or "")
    for name in NATIONAL_PARTIES.values():
        add(name)
    for name in candidates2015()["party"]:
        add(name)
    for name in PARTIES_2008.values():
        add(name)
    return {n: c.most_common(1)[0][0] for n, c in sorted(found.items()) if n not in registry_parties()}


def history() -> Package:
    p = Package(
        f"{SOURCE}/history",
        SOURCE,
        "Paraguay's national and departmental offices and bodies, the electoral systems of its earlier elections, and "
        "the parties those elections name that the 2026 registry lacks",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PARAGUAY}
    p.add("office", id=PRESIDENCY, polity_id=PARAGUAY, kind="head_of_state")
    p.name(PRESIDENCY, "President of Paraguay", "en", "official")
    p.name(PRESIDENCY, "Presidente de la República del Paraguay", "es", "native", False)
    for id, kind, en, es in (
        (SENATE, "upper_house", "Senate of Paraguay", "Cámara de Senadores"),
        (CHAMBER, "lower_house", "Chamber of Deputies of Paraguay", "Cámara de Diputados"),
        (
            PARLASUR,
            "unicameral",
            "Paraguay's delegation to the Mercosur Parliament",
            "Parlamentarios del Mercosur por Paraguay",
        ),
    ):
        p.add("body", id=id, polity_id=PARAGUAY, kind=kind)
        p.name(id, en, "en", "official")
        p.name(id, es, "es", "native", False)
    for dep, place in sorted(_dep_names().items()):
        if dep == 0:
            continue
        governor, board = key("office", "governor", dep), key("body", "board", dep)
        p.add("office", id=governor, polity_id=PARAGUAY, kind="governor")
        p.name(governor, f"Governor of {place}", "en", "official")
        p.name(governor, f"Gobernador de {place}", "es", "native", False)
        p.add("body", id=board, polity_id=PARAGUAY, kind="council")
        p.name(board, f"{place} Departmental Board", "en", "official")
        p.name(board, f"Junta Departamental de {place}", "es", "native", False)
    for id, since, until, rules, method in GENERAL_SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=PARAGUAY,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for normalized, name in party_names().items():
        id = party(name)
        p.add("party", id=id, kind=_party_kind(name, {}), polity_id=PARAGUAY)
        p.add("external_id", entity_id=id, scheme="py.tsje.organizacion", value=normalized)
        p.name(id, name, "es", "official")
    return p


def history_packages() -> list[str]:
    return [f"{SOURCE}/history", f"{SOURCE}/memoir-parties", *(f"{SOURCE}/{day}" for day in HISTORY_DAYS)]


# ---------------------------------------------------------------------------------------------------------------------
# The general election of 20 April 2008, from the Tribunal's memoir of that election.

# The parties the memoir spells out, by the acronym its tables use. A party the 2018 table also numbers keeps the
# name that table gives it, so both elections tie to the same record.
PARTIES_2008 = {
    "ANR": NATIONAL_PARTIES[(1, "ANR")],
    "PLRA": NATIONAL_PARTIES[(2, "PLRA")],
    "PPS": "PARTIDO PAIS SOLIDARIO",
    "PHP": "PARTIDO HUMANISTA PARAGUAYO",
    "UNACE": NATIONAL_PARTIES[(7, "UNACE")],
    "PPQ": NATIONAL_PARTIES[(8, "PPQ")],
    "PEN": NATIONAL_PARTIES[(9, "PEN")],
    "P_MAS": NATIONAL_PARTIES[(11, "PMAS")],
    "P-MAS": NATIONAL_PARTIES[(11, "PMAS")],
    "PFA": NATIONAL_PARTIES[(12, "PFA")],
    "PUP": "PARTIDO DE LA UNIDAD POPULAR",
    "MRCN": "MOVIMIENTO RESISTENCIA CIUDADANA NACIONAL",
    "MTP": "MOVIMIENTO TETA PYAHU",
    "MPT": "MOVIMIENTO POPULAR TEKOJOJA",
    "PDP": "PARTIDO DEMOCRATICO PROGRESISTA",
    "PT": "PARTIDO DE LOS TRABAJADORES",
    "ADT": "ALIANZA DEMOCRATICA TRICOLOR",
    "APC": "ALIANZA PATRIOTICA PARA EL CAMBIO",
    "PRF": NATIONAL_PARTIES[(3, "PRF")],
}
CONTESTS_2008 = {
    "Presidente y Vicepresidente": "president",
    "Senadores": "senate",
    "Parlamento Mercosur": "parlasur",
    "Diputados": "deputies",
    "Gobernador": "governor",
    "Gobernadores": "governor",
}
ORDER_2008 = ("president", "senate", "parlasur", "deputies", "governor")
HEADING_2008 = re.compile(
    r"Resultados para (Presidente y Vicepresidente|Senadores|Parlamento Mercosur|Diputados|Gobernador(?:es)?|"
    r"Junta Departamental)\b(.*)$"
)
HEADER_2008 = re.compile(r"L\s?\d+\s?\(\*\)|\S+(?:\s\(\*\))?")
FIGURE_2008 = re.compile(r"(?<![\d,.])(?:\d+(?:\.\d{3})*|-)(?![\d,])")
ROW_2008 = re.compile(r"^\s*(\d+)\s+([A-ZÑÁÉÍÓÚ0-9][A-ZÑÁÉÍÓÚ0-9 .\-/'()]*?)\s{2,}([\d-].*)$")
TOTALS_2008 = re.compile(r"^\s*TOTALES\s{2,}([\d-].*)$")
NOTE_2008 = re.compile(
    r"^\s*(?:(L\s?\d+\s?\(\*\))\s+)?(\S+(?:\s?-\s?\S+)?)\s{2,}(\S.*?),?\s+EN DPTOS?\.\s*([\d, ]+)\.?\s*$"
)
META_2008 = ("ELECTORES", "MESAS", "BLANCOS", "NULOS", "TOTAL", "PARTICIPACION")


class Parsed2008(NamedTuple):
    districts: dict[tuple[str, int, int], dict[str, int]]  # (contest, dep, dis) -> column -> figure
    names: dict[tuple[int, int], str]  # (dep, dis) -> the memoir's spelling
    departments: dict[tuple[str, int], dict[str, int]]  # (contest, dep) -> the department row of a national table
    national: dict[str, dict[str, int]]  # contest -> the printed national totals
    notes: dict[tuple[str, str], list[tuple[str, str, frozenset[int]]]]  # (contest, column) -> (acronym, name, deps)


def _column(text: str) -> str:
    """A header's name without its spaces; an asterisk after an acronym only points at the footnote."""
    name = re.sub(r"\s", "", text)
    return name if re.fullmatch(r"L\d+\(\*\)", name) else name.replace("(*)", "")


def _assign(ends: Sequence[float], columns: Sequence[float]) -> list[int] | None:
    """Place each figure under the header whose right edge is nearest, keeping their order."""
    k, n, inf = len(ends), len(columns), 10.0**9
    cost = [[inf] * (n + 1) for _ in range(k + 1)]
    take = [[False] * (n + 1) for _ in range(k + 1)]
    cost[0] = [0.0] * (n + 1)
    for i in range(1, k + 1):
        for j in range(1, n + 1):
            use = cost[i - 1][j - 1] + abs(ends[i - 1] - columns[j - 1])
            if use <= cost[i][j - 1]:
                cost[i][j], take[i][j] = use, True
            else:
                cost[i][j] = cost[i][j - 1]
    if cost[k][n] >= inf:
        return None
    out: list[int] = []
    i, j = k, n
    while i > 0:
        if take[i][j]:
            out.append(j - 1)
            i, j = i - 1, j - 1
        else:
            j -= 1
    return out[::-1]


def _figures(text: str, offset: int, columns: list[tuple[str, int]], line: str) -> dict[str, int]:
    found = [
        (0 if m.group(0) == "-" else int(m.group(0).replace(".", "")), m.end() + offset)
        for m in FIGURE_2008.finditer(text)
    ]
    placed = _assign([end for _, end in found], [end for _, end in columns])
    if placed is None:
        raise ValueError(
            f"2008 memoir: {len(found)} figures do not fit {len(columns)} headers in {line.strip()[:80]!r}"
        )
    return {columns[j][0]: value for (value, _), j in zip(found, placed, strict=True)}


@functools.cache
def memoir2008() -> Parsed2008:
    """The district tables of the memoir's results chapter, placed under their column headers by position."""
    deps = {**_dep_codes(), "PRESIDENTE HAYES": 15}
    districts: dict[tuple[str, int, int], dict[str, int]] = {}
    names: dict[tuple[int, int], str] = {}
    departments: dict[tuple[str, int], dict[str, int]] = {}
    national: dict[str, dict[str, int]] = {}
    notes: dict[tuple[str, str], list[tuple[str, str, frozenset[int]]]] = {}
    contest: str | None = None
    section: str | None = None
    columns: list[tuple[str, int]] | None = None
    rank, late, last = 0, False, ""
    for line in pdf_text(MEMORIA_2008).splitlines():
        found = HEADING_2008.search(line)
        if found:
            contest, section, columns = CONTESTS_2008.get(found.group(1)), None, None
            late = late or found.group(1) == "Junta Departamental"
            if contest is not None:
                # The memoir prints its departmental boards under the headings of the other contests too.
                if ORDER_2008.index(contest) < rank or late:
                    contest = None
                rank = max(rank, ORDER_2008.index(contest)) if contest else rank
            if "Zonas" in found.group(2):
                contest = None
            continue
        if contest is None:
            continue
        if re.match(r"\s*COD\.", line):
            heads = [(_column(m.group(0)), m.end()) for m in HEADER_2008.finditer(line)]
            columns = None if "ZONAS" in line else [(n, e) for n, e in heads if n not in ("COD.", "DISTRITO")]
            continue
        if columns is not None and re.match(r"\s*PORCENTAJE", line):
            columns = None
            continue
        text = line.strip()
        if columns is None:
            note = NOTE_2008.match(line)
            if note and section is not None and section.startswith("Resumen"):
                if note.group(1):
                    last = re.sub(r"\s", "", note.group(1))
                place = frozenset(int(n) for n in re.findall(r"\d+", note.group(4)))
                entry = (clean(note.group(2)).replace("- ", "-"), clean(note.group(3)), place)
                if entry not in notes.setdefault((contest, last), []):
                    notes[(contest, last)].append(entry)
            elif text and section is None:
                section = re.sub(r"\s*\(\d\)$", "", text)
            continue
        row = ROW_2008.match(line)
        if row:
            assert section is not None
            code = int(row.group(1))
            values = _figures(row.group(3), row.start(3), columns, line)
            if section.startswith("Resumen"):
                departments.setdefault((contest, code), {}).update(values)
                continue
            dep = deps[normal(section)]
            districts.setdefault((contest, dep, code), {}).update(values)
            names[(dep, code)] = clean(row.group(2))
            continue
        total = TOTALS_2008.match(line)
        if total and section is not None and section.startswith("Resumen"):
            national.setdefault(contest, {}).update(_figures(total.group(1), total.start(1), columns, line))
    # The footnotes to column L 66 disagree with the department rows. For the deputies, the department rows put the
    # Caazapeña alliance in Caazapá (6) and the alliance for change in 10, 12, and 16; for the governors, the
    # footnote omits Alto Paraná (10), where the column holds the alliance for change.
    caazapena, change = "ALIANZA PATRIOTICA CAAZAPEÑA", "ALIANZA PATRIOTICA PARA EL CAMBIO"
    notes[("deputies", "L66(*)")] = [entry for entry in notes.get(("deputies", "L66(*)"), []) if entry[0] != "APC"] + [
        ("APC", caazapena, frozenset({6})),
        ("APC", change, frozenset({10, 12, 16})),
    ]
    notes[("governor", "L66(*)")] = [
        entry for entry in notes.get(("governor", "L66(*)"), []) if entry[1] == caazapena
    ] + [("APC", change, frozenset({10, 12, 16}))]
    for (contest, dep, dis), values in districts.items():
        parties = sum(v for k, v in values.items() if k not in META_2008)
        if parties + values.get("BLANCOS", 0) + values.get("NULOS", 0) != values.get("TOTAL"):
            raise ValueError(f"2008 memoir: {contest} {dep}:{dis} does not add up to its printed total")
    return Parsed2008(districts, names, departments, national, notes)


@functools.cache
def governors2008() -> dict[int, tuple[str, str]]:
    """The governors the memoir declares elected, by department: the name and the acronym of the winning list."""
    deps = {**_dep_codes(), "PRESIDENTE HAYES": 15}
    out: dict[int, tuple[str, str]] = {}
    started, dep = False, None
    for line in pdf_text(MEMORIA_2008).splitlines():
        if "Gobernadores Electos por Departamento" in line:
            started = True
        elif started and "Junta" in line:
            break
        elif started:
            text = clean(line)
            if normal(text) in deps:
                dep = deps[normal(text)]
                continue
            found = re.match(r"^(?:8\.16\s+)?(\D+?)\s+(\d+)\s+(\S+)\s+\S.*$", text)
            if found and dep is not None and dep not in out and "Nombre" not in text:
                out[dep] = (clean(found.group(1)), found.group(3))
    return out


def acronym2008(contest: str, column: str, dep: int) -> str:
    """The acronym behind a numbered column of the memoir's tables, in the department where it ran."""
    mine = [a for a, _, deps in memoir2008().notes.get((contest, column), []) if dep in deps]
    return mine[0] if len(mine) == 1 else column


def compare2008() -> dict[str, int]:
    """Check the district tables against the department rows and the national totals the memoir prints."""
    memoir = memoir2008()
    known = set(districts().select("dep", "dis").iter_rows())
    unknown = {(dep, dis) for _, dep, dis in memoir.districts} - known
    if unknown:
        raise ValueError(f"2008 memoir: districts without a place in the geography: {sorted(unknown)[:5]}")
    sums: dict[tuple[str, int], Counter[str]] = {}
    for (contest, dep, _), values in memoir.districts.items():
        sums.setdefault((contest, dep), Counter()).update(
            {acronym2008(contest, k, dep): v for k, v in values.items() if k != "PARTICIPACION"}
        )
    out = {"districts": len({(dep, dis) for _, dep, dis in memoir.districts})}
    for contest in ORDER_2008:
        total: Counter[str] = Counter()
        for (c, dep), counts in sums.items():
            if c != contest:
                continue
            total.update(counts)
            printed = memoir.departments.get((contest, dep))
            if printed is not None:
                wrong = {
                    k: (v, counts.get(acronym2008(contest, k, dep), 0))
                    for k, v in printed.items()
                    if k != "PARTICIPACION" and counts.get(acronym2008(contest, k, dep), 0) != v
                }
                if wrong:
                    raise ValueError(f"2008 memoir: {contest} department {dep} differs from its districts: {wrong}")
        printed_total = memoir.national.get(contest)
        if printed_total is not None:
            wrong = {
                k: (v, total.get(k, 0))
                for k, v in printed_total.items()
                if k != "PARTICIPACION"
                and not k.startswith("L")
                and not (k == "APC" and contest in ("deputies", "governor"))  # three different alliances share it
                and total.get(k, 0) != v
            }
            if wrong:
                raise ValueError(f"2008 memoir: {contest} national totals differ from its districts: {wrong}")
        out[f"{contest} votes"] = total["TOTAL"]
    print(f"2008: {out}", flush=True)
    return out


GENERAL_2008_NOTE = (
    "Counted by the Tribunal Superior de Justicia Electoral and printed, by municipality (distrito), in the results "
    "chapter of its memoir of the general and departmental elections of 20 April 2008. Valid votes are the votes for "
    "lists; blank and null ballots are the district's own counts, and ballots cast are the district total the memoir "
    "prints. Registered voters are the district's roll as printed. The sums of the districts agree with the "
    "department rows and with the national totals the memoir prints, for every list. The memoir also breaks the "
    "Capital's results down by zone; the zones are not stored. A numbered column of the memoir's tables is read as "
    "the list its footnote names in that department."
)


def _ticket2008() -> tuple[str, str]:
    """The names of the president and vice president the memoir declares elected."""
    lines = [clean(line) for line in pdf_text(MEMORIA_2008).splitlines()]
    at = next(i for i, line in enumerate(lines) if line.startswith("PRESIDENTE DE LA REPUBLICA"))
    names = [
        re.sub(r"\s+[\d.]+\s+[\d,]+%$", "", line) for line in lines[at + 1 : at + 12] if line and "FOTO" not in line
    ]
    return names[0], names[1]


def _labels2008(contest: str, dep: int, order: list[str]) -> dict[int, tuple[str, str | None]]:
    """Ballot label and party of each column of a contest, in the department where it is read."""
    memoir = memoir2008()
    named = {a: n for (c, _), entries in memoir.notes.items() if c == contest for a, n, deps in entries if dep in deps}
    known = {normal(name) for name in PARTIES_2008.values()}
    out: dict[int, tuple[str, str | None]] = {}
    for lista, column in enumerate(order, 1):
        acronym = acronym2008(contest, column, dep)
        name = named.get(acronym) or PARTIES_2008.get(acronym) or acronym
        out[lista] = (name, party(name) if normal(name) in known else None)
    return out


def general_2008() -> Package:
    day = GENERAL_2008
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "Paraguay's general elections of 20 April 2008 (President, Senate, Mercosur Parliament, Chamber of Deputies, "
        "and governors), by municipality, from the Tribunal Superior de Justicia Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/history"],
    )
    p.external |= {PARAGUAY, COUNTRY}
    source_id = key("source", SOURCE, "general-2008")
    _source(
        p,
        source_id,
        "Memoria y Estadísticas Electorales: resultados de las elecciones generales y departamentales de 2008",
        original(MEMORIA_2008),
        [MEMORIA_2008],
    )
    election_id = key("election", day)
    path = f"py/{day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=PARAGUAY,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, "2008 Paraguayan general election", "en", "official")
    p.name(election_id, "Elecciones generales 2008", "es", "native", False)

    memoir = memoir2008()
    places = sorted({(dep, dis) for _, dep, dis in memoir.districts})
    frame = pl.DataFrame(places, schema=["dep", "dis"], orient="row").with_columns(
        code=pl.concat_str(pl.lit(str(day)), "dep", "dis", separator=":")
    )
    frame = frame.with_columns(unit_id=pl.col("code").map_elements(lambda c: key("unit", c), return_dtype=pl.String))
    p.frame(
        "reporting_unit",
        frame.select(
            id="unit_id",
            polity_id=pl.lit(PARAGUAY),
            scheme_code=pl.lit("py.distrito"),
            code="code",
            area_relation=pl.lit("equals"),
            area_id=pl.struct("dep", "dis").map_elements(
                lambda row: area("py.distrito", f"{row['dep']}:{row['dis']}"), return_dtype=pl.String
            ),
            kind=pl.lit("aggregate"),
        ),
    )
    unit_of = {(dep, dis): unit for dep, dis, unit in frame.select("dep", "dis", "unit_id").iter_rows()}
    names = _dep_names()
    slugs = {dep: changeset.slug(name) for dep, name in names.items()}
    values: list[pl.DataFrame] = []
    elected = governors2008()

    def contest(code: str, dep: int | None, spec: Spec) -> None:
        mine = {
            (d, dis): row for (c, d, dis), row in memoir.districts.items() if c == code and (dep is None or d == dep)
        }
        order: list[str] = []
        for _, row in sorted(mine.items()):
            order += [column for column in row if column not in META_2008 and column not in order]
        # A national contest prints one acronym per list, whatever the department.
        labels = _labels2008(code, 0 if dep is None else dep, order)
        slot = {column: lista for lista, column in enumerate(order, 1)}
        rows = [
            (unit_of[place], slot[column], value)
            for place, row in mine.items()
            for column, value in row.items()
            if column not in META_2008
        ]
        accounting = [
            (
                unit_of[place],
                row.get("ELECTORES") or None,
                row["TOTAL"],
                row.get("BLANCOS"),
                row.get("NULOS"),
            )
            for place, row in mine.items()
        ]
        printed = memoir.national.get(code) if dep is None else memoir.departments.get((code, dep))
        declared: list[tuple[int | None, int, int]] = []
        for column, value in (printed or {}).items():
            if column in slot:
                declared.append((slot[column], 1, value))
        if printed:
            for column, measure in (("BLANCOS", BLANK), ("NULOS", INVALID)):
                if column in printed:
                    declared.append((None, measure, printed[column]))
            if all(row.get("ELECTORES") for row in mine.values()) and "ELECTORES" in printed:
                declared.append((None, REGISTERED, printed["ELECTORES"]))
        winner: int | None = None
        members: tuple[tuple[int, int, str], ...] = ()
        if code == "president":
            winner = slot["APC"]
            members = tuple((winner, rank, name) for rank, name in enumerate(_ticket2008(), 1))
        elif code == "governor" and dep is not None and dep in elected:
            name, sigla = elected[dep]
            matched = [slot[c] for c in order if acronym2008(code, c, dep) == sigla]
            if len(matched) == 1:
                winner, members = matched[0], ((matched[0], 1, name),)
        tally = Tally(
            rows=pl.DataFrame(rows, schema=["unit_id", "lista", "votes"], orient="row"),
            totals=pl.DataFrame(
                accounting,
                schema={
                    "unit_id": pl.String,
                    "registered": pl.Int64,
                    "total": pl.Int64,
                    "blank": pl.Int64,
                    "invalid": pl.Int64,
                },
                orient="row",
            ),
            labels=labels,
            winner=winner,
            members=members,
            declared=tuple(declared),
            positions=dict.fromkeys(labels),
        )
        values.append(_add_contest(p, election_id, day, path, spec, tally, source_id, GENERAL_2008_NOTE))

    contest(
        "president",
        None,
        Spec(
            "president",
            "office",
            "2008 Paraguayan presidential election",
            "Elección presidencial 2008",
            "president",
            COUNTRY,
            PRESIDENCY,
            None,
            PRESIDENT_SYSTEM,
            1,
            "ticket",
        ),
    )
    contest(
        "senate",
        None,
        Spec(
            "senate",
            "list_allocation",
            "2008 Paraguayan Senate election",
            "Elección de senadores 2008",
            "senate",
            COUNTRY,
            None,
            SENATE,
            SENATE_SYSTEM,
            45,
            "list",
        ),
    )
    contest(
        "parlasur",
        None,
        Spec(
            "parlasur",
            "list_allocation",
            "2008 election of Paraguay's members of the Mercosur Parliament",
            "Elección de parlamentarios del Mercosur 2008",
            "mercosur-parliament",
            COUNTRY,
            None,
            PARLASUR,
            PARLASUR_SYSTEM,
            18,
            "list",
        ),
    )
    for dep in sorted({d for c, d, _ in memoir.districts if c == "deputies"}):
        contest(
            "deputies",
            dep,
            Spec(
                f"deputies:{dep}",
                "list_allocation",
                f"2008 Chamber of Deputies election, {names[dep]}",
                f"Elección de diputados 2008, {names[dep]}",
                f"deputies-{slugs[dep]}",
                area("py.departamento", str(dep)),
                None,
                CHAMBER,
                CHAMBER_SYSTEM,
                None,
                "list",
            ),
        )
    for dep in sorted({d for c, d, _ in memoir.districts if c == "governor"}):
        contest(
            "governor",
            dep,
            Spec(
                f"governor:{dep}",
                "office",
                f"2008 governor election, {names[dep]}",
                f"Elección de gobernador 2008, {names[dep]}",
                f"governor-{slugs[dep]}",
                area("py.departamento", str(dep)),
                key("office", "governor", dep),
                None,
                GOVERNOR_SYSTEM,
                1,
                "candidate",
            ),
        )
    p.frame(
        "result_value",
        pl.concat(values).with_columns(
            channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
        ),
    )
    return p


# ---------------------------------------------------------------------------------------------------------------------
# The election of 13 August 2000 to fill the vacant vice presidency.

VICE_PRESIDENCY = key("office", "vice-president")
VICE_SYSTEM = key("electoral_system", "vice-president", 1992)
VICE_2000_SOURCE = (
    "https://www.datos.gov.py/sites/default/files/resultados-2000-y-2011-vicepresidente-y-referendum-por-distrito.csv"
)
VICE_2000_LISTS = {
    "lista1anr": (1, "ANR", NATIONAL_PARTIES[(1, "ANR")]),
    "lista2plra": (2, "PLRA", NATIONAL_PARTIES[(2, "PLRA")]),
    "lista5ph": (5, "PH", None),
}
VICE_2000_NOTE = (
    "Counted by the Tribunal Superior de Justicia Electoral and published, by municipality (distrito), in the open-data "
    "file of the vice-presidential election of 13 August 2000, called under the Electoral Code to fill the vacant "
    "vice presidency. Each district row gives the ballots cast, the blank and null ballots, and the votes of the three "
    "lists, numbered as the file numbers them (1 ANR, 2 PLRA, 5 PH); ballots cast equal the three lists plus blank and "
    "null ballots in every district. The file prints no registered voters, no candidate names, no results by polling "
    "table, and no national or department totals, and it does not say who was elected, so no outcome is stored. "
    "Districts are keyed by the Tribunal's current district codes, which every district of the file carries; "
    "municipalities created after 2000 do not appear, and their territory then belonged to the districts of the file. "
    "The labels of the lists are the file's own abbreviations; the file does not name the lists' parties, so the "
    "PH list is not tied to a party record."
)


@functools.cache
def vice2000() -> pl.DataFrame:
    """(dep, dis, name, total, blank, invalid, and one column per list) per district for 2000."""
    text = raw.cached(SOURCE, VICE_2000_FILE).path.read_bytes().decode("latin-1")
    frame = pl.read_csv(text.encode("utf-8"), separator=";", infer_schema=False).filter(pl.col("2000") == "2000")
    frame = frame.rename({"departamento": "dep", "distrito": "dis", "descripcion-distrito": "name"})
    return frame.select(
        _int("dep"),
        _int("dis"),
        pl.col("name"),
        total=_int("total"),
        blank=_int("votos-blancos"),
        invalid=_int("votos-nulos"),
        **{column: _int(column) for column in VICE_2000_LISTS},
    )


def compare2000() -> dict[str, int]:
    """Check that every district adds up, and return the national sums of the file."""
    frame = vice2000()
    if frame.select("dep", "dis").n_unique() != frame.height:
        raise ValueError("a district appears twice in the 2000 file")
    summed = frame.select(pl.sum_horizontal("blank", "invalid", *VICE_2000_LISTS)).to_series()
    if (summed != frame["total"]).any():
        raise ValueError("a district of the 2000 file does not add up to its total")
    known = set(districts().select("dep", "dis").iter_rows())
    unknown = [row for row in frame.select("dep", "dis").iter_rows() if row not in known]
    if unknown:
        raise ValueError(f"districts of the 2000 file the geography lacks: {unknown}")
    return {column: int(frame[column].sum()) for column in ("total", "blank", "invalid", *VICE_2000_LISTS)}


def vice_2000() -> Package:
    day = VICE_2000
    compare2000()
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "Paraguay's election of 13 August 2000 to fill the vacant vice presidency, by municipality, from the "
        "Tribunal Superior de Justicia Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PARAGUAY, COUNTRY}
    source_id = key("source", SOURCE, "vice-2000")
    _source(
        p,
        source_id,
        "Resultados 2000 y 2011: vicepresidente y referéndum, por distrito",
        VICE_2000_SOURCE,
        [VICE_2000_FILE],
    )
    p.add("office", id=VICE_PRESIDENCY, polity_id=PARAGUAY, kind="other")
    p.name(VICE_PRESIDENCY, "Vice President of Paraguay", "en", "official")
    p.name(VICE_PRESIDENCY, "Vicepresidente de la República del Paraguay", "es", "native", False)
    p.add(
        "electoral_system",
        id=VICE_SYSTEM,
        polity_id=PARAGUAY,
        valid_from=date(1992, 6, 20),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", VICE_SYSTEM),
        entity_id=VICE_SYSTEM,
        kind="methodology",
        body_md="A vacant vice presidency is filled by direct election in a single round; the candidate with the most "
        "votes is elected.",
        lang="en",
    )
    election_id = key("election", day)
    path = f"py/{day}-vice-presidential"
    p.add(
        "election",
        id=election_id,
        polity_id=PARAGUAY,
        kind="by_election",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, "2000 Paraguayan vice-presidential election", "en", "official")
    p.name(election_id, "Elección de vicepresidente 2000", "es", "native", False)

    frame = vice2000().with_columns(code=pl.concat_str(pl.lit(str(day)), "dep", "dis", separator=":"))
    frame = frame.with_columns(unit_id=pl.col("code").map_elements(lambda c: key("unit", c), return_dtype=pl.String))
    p.frame(
        "reporting_unit",
        frame.select(
            id="unit_id",
            polity_id=pl.lit(PARAGUAY),
            scheme_code=pl.lit("py.distrito"),
            code="code",
            area_relation=pl.lit("equals"),
            area_id=pl.struct("dep", "dis").map_elements(
                lambda row: area("py.distrito", f"{row['dep']}:{row['dis']}"), return_dtype=pl.String
            ),
            kind=pl.lit("aggregate"),
        ),
    )
    rows = frame.select("unit_id", *VICE_2000_LISTS).unpivot(
        index="unit_id", variable_name="column", value_name="votes"
    )
    rows = rows.with_columns(
        lista=pl.col("column").replace_strict({c: n for c, (n, _, _) in VICE_2000_LISTS.items()}, return_dtype=pl.Int64)
    ).select("unit_id", "lista", "votes")
    labels = {number: (acronym, party(name) if name else None) for number, acronym, name in VICE_2000_LISTS.values()}
    tally = Tally(
        rows=rows,
        totals=frame.select(
            "unit_id", registered=pl.lit(None, pl.Int64), total="total", blank="blank", invalid="invalid"
        ),
        labels=labels,
    )
    spec = Spec(
        "vice",
        "office",
        "2000 Paraguayan vice-presidential election",
        "Elección de vicepresidente 2000",
        "vice-president",
        COUNTRY,
        VICE_PRESIDENCY,
        None,
        VICE_SYSTEM,
        1,
        "candidate",
    )
    values = _add_contest(p, election_id, day, path, spec, tally, source_id, VICE_2000_NOTE)
    p.frame(
        "result_value",
        values.with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    return p


# ---------------------------------------------------------------------------------------------------------------------
# The general election of 21 April 2013, from the Tribunal's memoir of that election.

CONTESTS_2013 = {
    "PRESIDENTE Y VICEPRESIDENTE": "president",
    "SENADORES": "senate",
    "PARLAMENTO MERCOSUR": "parlasur",
    "DIPUTADOS": "deputies",
    "GOBERNADORES": "governor",
    "JUNTA DEPARTAMENTAL": "board",
}
ORDER_2013 = ("president", "senate", "parlasur", "deputies", "governor")
HEADING_2013 = re.compile(
    r"RESULTADOS PARA (PRESIDENTE Y VICEPRESIDENTE|SENADORES|PARLAMENTO MERCOSUR|DIPUTADOS|GOBERNADORES|"
    r"JUNTA DEPARTAMENTAL)\b",
    re.IGNORECASE,
)
ABROAD_2013 = re.compile(r"^4\.2\.([123]) Resultados")
HEADER_LINE_2013 = re.compile(r"\s*C?OD\b\.?\s")
TOTALS_2013 = re.compile(r"^\s*TOTALE?S?\s{2,}([\d-].*)$")
NOT_COLUMNS_2013 = ("COD.", "COD", "OD.", "DISTRITO", "ZONAS", "ZONA", "PAÍS", "DEPARTAMENTO")
SKIPPED_2013 = ("PARTICIPACION", "MESAS")
DEPARTMENT_UNIT = -1  # the district code of a unit that is a whole department
COUNTRIES_2013 = {"ARGENTINA", "ESPAÑA", "ESTADOS UNIDOS"}


class Parsed2013(NamedTuple):
    districts: dict[tuple[str, int, int], dict[str, int]]  # (contest, dep, dis) -> column -> figure
    names: dict[tuple[int, int], str]
    departments: dict[tuple[str, int], dict[str, int]]  # the department rows of a national summary
    totals: dict[tuple[str, int], dict[str, int]]  # the TOTALES row printed under a department's table
    national: dict[str, dict[str, int]]


def _cells(text: str, offset: int, columns: list[tuple[str, int]], line: str) -> dict[str, int]:
    """The figures of a 2013 row. Its cells are never blank (a zero is a dash), so a full row is read in order."""
    found = [0 if m.group(0) == "-" else int(m.group(0).replace(".", "")) for m in FIGURE_2008.finditer(text)]
    names = [name for name, _ in columns if name != "PARTICIPACION"]
    if len(found) == len(names):
        return dict(zip(names, found, strict=True))
    return _figures(text, offset, columns, line)


def _merge(into: dict[str, int], values: dict[str, int], where: str) -> None:
    for column, value in values.items():
        if into.setdefault(column, value) != value:
            raise ValueError(f"{where}: {column} is printed twice with different figures")


@functools.cache
def memoir2013() -> Parsed2013:
    """The district tables of the 2013 memoir's results chapter, placed under their column headers by position."""
    deps = {**_dep_codes(), "PRESIDENTE HAYES": 15}
    districts: dict[tuple[str, int, int], dict[str, int]] = {}
    names: dict[tuple[int, int], str] = {}
    departments: dict[tuple[str, int], dict[str, int]] = {}
    totals: dict[tuple[str, int], dict[str, int]] = {}
    national: dict[str, dict[str, int]] = {}
    contest: str | None = None
    section: str | None = None
    columns: list[tuple[str, int]] | None = None
    abroad = zones = False
    for line in pdf_text(MEMORIA_2013).splitlines():
        found = HEADING_2013.search(line)
        if found:
            contest = CONTESTS_2013[found.group(1).upper()]
            if contest == "board":
                contest = None
            section, columns, zones = None, None, False
            continue
        found = ABROAD_2013.match(line)
        if found:
            contest = ("president", "senate", "parlasur")[int(found.group(1)) - 1]
            section, columns, zones, abroad = "EXTERIOR", None, False, True
            continue
        if contest is None:
            continue
        if HEADER_LINE_2013.match(line):
            if abroad and "ZONA" in line:
                columns = None
                continue
            line = line.replace("MICABLANCOS", "MICA BLANCOS")
            heads = [(_column(m.group(0)), m.end()) for m in HEADER_2008.finditer(line)]
            columns = [(n, e) for n, e in heads if n not in NOT_COLUMNS_2013]
            zones = "ZONAS" in line
            continue
        if columns is not None and re.match(r"\s*(PORCENTAJE|%)", line):
            columns = None
            continue
        text = line.strip()
        if columns is None:
            if section is None or not abroad:
                if normal(text) in deps:
                    section, abroad = text, False
                elif normal(text).startswith("RESUMEN A NIVEL"):
                    section, abroad = "RESUMEN", False
                elif text and section is None and not text.isdigit():
                    section = text
            continue
        where = f"2013 memoir: {contest} {section}"
        total = TOTALS_2013.match(line)
        if total:
            values = _cells(total.group(1), total.start(1), columns, line)
            if section == "RESUMEN":
                _merge(national.setdefault(contest, {}), values, where)
            elif abroad:
                _merge(totals.setdefault((contest, 18), {}), values, where)
            else:
                dep = deps[normal(section or "")] if not zones or section else 0
                _merge(totals.setdefault((contest, dep), {}), values, where)
                if zones:
                    _merge(districts.setdefault((contest, dep, 0), {}), values, where)
                    names[(dep, 0)] = "ASUNCION"
            continue
        row = ROW_2008.match(line)
        if row is None or zones:
            continue
        code = int(row.group(1))
        values = _cells(row.group(3), row.start(3), columns, line)
        if section == "RESUMEN":
            _merge(departments.setdefault((contest, code), {}), values, where)
        elif abroad:
            if row.group(2).strip() not in COUNTRIES_2013:
                raise ValueError(f"{where}: unexpected abroad row {line.strip()[:60]!r}")
            _merge(districts.setdefault((contest, 18, code), {}), values, where)
            names[(18, code)] = clean(row.group(2))
        else:
            dep = deps[normal(section or "")]
            _merge(districts.setdefault((contest, dep, code), {}), values, where)
            names[(dep, code)] = clean(row.group(2))
    for (c, dep, dis), values in districts.items():
        parties = sum(v for k, v in values.items() if k not in META_2008)
        if parties + values.get("BLANCOS", 0) + values.get("NULOS", 0) != values.get("TOTAL"):
            raise ValueError(f"2013 memoir: {c} {dep}:{dis} does not add up to its printed total: {values}")
    # The memoir's table of Concepción's districts for the Senate reports about two thirds of the department's votes
    # (participation above 100% in several districts) and disagrees with the department row that the national
    # summary prints, so the department is kept as one unit with the department row's figures.
    for (contest, dep), printed in sorted(departments.items()):
        counts: Counter[str] = Counter()
        for (c, d, _), values in districts.items():
            if (c, d) == (contest, dep):
                counts.update({k: v for k, v in values.items() if k not in SKIPPED_2013})
        if any(counts[k] != v for k, v in printed.items() if k not in SKIPPED_2013):
            if (contest, dep) != ("senate", 1):
                raise ValueError(f"2013 memoir: {contest} department {dep} differs from its districts")
            for key_ in [k for k in districts if k[:2] == (contest, dep)]:
                del districts[key_]
            totals.pop((contest, dep), None)
            districts[(contest, dep, DEPARTMENT_UNIT)] = dict(printed)
            names[(dep, DEPARTMENT_UNIT)] = "CONCEPCION (DEPARTMENT)"
    return Parsed2013(districts, names, departments, totals, national)


def compare2013() -> dict[str, int]:
    """Check the district tables against the department rows, the department totals, and the national totals."""
    memoir = memoir2013()
    known = set(districts().select("dep", "dis").iter_rows())
    unknown = {(dep, dis) for _, dep, dis in memoir.districts if dep != ABROAD and dis != DEPARTMENT_UNIT} - known
    if unknown:
        raise ValueError(f"2013 memoir: districts without a place in the geography: {sorted(unknown)[:5]}")
    sums: dict[tuple[str, int], Counter[str]] = {}
    for (contest, dep, _), values in memoir.districts.items():
        sums.setdefault((contest, dep), Counter()).update({k: v for k, v in values.items() if k != "PARTICIPACION"})
    for (contest, dep), counts in sums.items():
        for printed, what in (
            (memoir.departments.get((contest, dep)), "department row"),
            (memoir.totals.get((contest, dep)), "total"),
        ):
            if printed is None:
                continue
            wrong = {k: (v, counts[k]) for k, v in printed.items() if k not in SKIPPED_2013 and counts[k] != v}
            if wrong:
                raise ValueError(f"2013 memoir: {contest} {dep} {what} differs from its districts: {wrong}")
    out = {"districts": len({(dep, dis) for _, dep, dis in memoir.districts})}
    for contest in ORDER_2013:
        total: Counter[str] = Counter()
        for (c, _), counts in sums.items():
            if c == contest:
                total.update(counts)
        printed_total = memoir.national.get(contest)
        if printed_total is not None:
            wrong = {k: (v, total[k]) for k, v in printed_total.items() if k not in SKIPPED_2013 and total[k] != v}
            if wrong:
                raise ValueError(f"2013 memoir: {contest} national totals differ from its districts: {wrong}")
        out[f"{contest} votes"] = total["TOTAL"]
    print(f"2013: {out}", flush=True)
    return out


# The ballot number and name of each acronym the 2013 tables use, from the Tribunal's list of the organizations that
# registered candidacies. An alliance that ran in one department under its own acronym still ran as list 4.
KNOWN_2013 = {
    "ANR": (1, NATIONAL_PARTIES[(1, "ANR")]),
    "PLRA": (2, NATIONAL_PARTIES[(2, "PLRA")]),
    "PHP": (5, PARTIES_2008["PHP"]),
    "UNACE": (7, NATIONAL_PARTIES[(7, "UNACE")]),
    "PPQ": (8, NATIONAL_PARTIES[(8, "PPQ")]),
    "PEN": (9, NATIONAL_PARTIES[(9, "PEN")]),
    "PT": (14, PARTIES_2008["PT"]),
    "PVP": (23, NATIONAL_PARTIES[(23, "PVP")]),
    "FG": (40, NATIONAL_PARTIES[(40, "CNFG")]),
    "PDP": (100, PARTIES_2008["PDP"]),
}
ACRONYMS_2013 = {
    **KNOWN_2013,
    "AP": (3, "CONCERTACION AVANZA PAIS"),
    "PA": (4, "ALIANZA PARAGUAY ALEGRE"),
    "PC": (4, "ALIANZA PATRIOTICA CAAZAPEÑA"),
    "TIA": (4, "ALIANZA ITAPUA ALEGRE"),
    "APA": (4, "ALIANZA ALTO PARAGUAY ALEGRE"),
    "PCH": (4, "ALIANZA PASION CHAQUEÑA"),
    "ATPA": (4, "ALIANZA TODOS POR UN PARAGUARI ALEGRE"),
    "PB": (10, "PARTIDO BLANCO"),
    "PEA": (13, "MOVIMIENTO PUEBLO EN ACCION"),
    "MDIP": (16, "MOVIMIENTO DEMOCRATICO INDEPENDIENTE PARTICIPATIVO"),
    "UDV": (18, "MOVIMIENTO UNIDAD DEMOCRATICA PARA LA VICTORIA"),
    "PJ": (21, "PARTIDO DE LA JUVENTUD - FUERZA JOVEN"),
    "PSD": (22, "PARTIDO SOCIAL DEMOCRATA"),
    "MDC": (25, "MOVIMIENTO DESPERTAR CIUDADANO"),
    "PSN": (26, "MOVIMIENTO PROPUESTA POR LA SOBERANIA NACIONAL"),
    "PL": (28, "PARTIDO LIBERAL"),
    "30-A": (30, "MOVIMIENTO NACIONAL PLURALISTA PARTICIPATIVO 30 DE AGOSTO"),
    "PCA": (44, "MOVIMIENTO PODER CIUDADANO EN ACCION"),
    "MII": (50, "MOVIMIENTO INDEPENDIENTE INSTITUCIONAL"),
    "PPL": (55, "PARTIDO PATRIA LIBRE"),
    "MKP": (69, "MOVIMIENTO KUÑA PYRENDA"),
    "MT": (75, "MOVIMIENTO REGIONAL DE LOS TRABAJADORES DE AMAMBAY"),
    "TC": (100, "CONCERTACION TODOS POR CONCEPCION"),
    "PEP": (110, "MOVIMIENTO PARA EL PUEBLO ENCARNACION"),
    "+PY": (500, "MOVIMIENTO POLITICO MAS PARAGUAY"),
    "MICA": (900, "MOVIMIENTO INDEPENDIENTE CONSTITUCIONALISTA EN ALIANZA"),
    "MOT": (911, "MOVIMIENTO REGIONAL OPORTUNIDAD PARA TODOS"),
}
SUMMARY_2013 = re.compile(r"^\s*(\d+)\s+(\S+)\s+(\S.*?)\s{2,}([\d.]+)\s+[\d,]+%\s*(\d+)?\s*$")
ELECTED_2013 = re.compile(r"^\s*([A-ZÑÁÉÍÓÚ. ]+?)\s{2,}([A-ZÑÁÉÍÓÚ ]+?)\s{2,}(\d+)\s+(\S+)\s+\S.*$")


def pcn_name(acronym: str, dep: int) -> str:
    """The name of the list an acronym stands for in the department where it ran."""
    name = ACRONYMS_2013[acronym][1]
    if acronym == "PCH":
        return f"{name} - {'PDTE. HAYES' if dep == 15 else 'BOQUERON'}"
    return name


class Summary2013(NamedTuple):
    lists: dict[str, dict[int, tuple[str, int, int | None]]]  # contest -> list -> (acronym, votes, seats)
    ticket: tuple[str, str]
    governors: dict[int, tuple[str, int]]  # department -> (name, list) of the governor declared elected


@functools.cache
def summary2013() -> Summary2013:
    """The national summaries of the memoir's results chapter, and the governors it declares elected."""
    lines = pdf_text(MEMORIA_2013).splitlines()
    lists: dict[str, dict[int, tuple[str, int, int | None]]] = {}
    contest: str | None = None
    ticket: list[str] = []
    start = next(i for i, line in enumerate(lines) if line.startswith("4.1.1 Resultados"))
    stop = next(i for i, line in enumerate(lines) if line.startswith("4.2.1 Resultados"))
    for line in lines[start:stop]:
        found = re.match(r"^4\.1\.([123]) Resultados", line)
        if found:
            contest = ("president", "senate", "parlasur")[int(found.group(1)) - 1]
            continue
        found = re.match(r"^\s*(Presidente|Vicepresidente)\s{2,}(\S.*?)\s{2,}ANR\s*$", line)
        if found:
            ticket.append(clean(found.group(2)))
        found = SUMMARY_2013.match(line)
        if found and contest is not None:
            number, acronym = int(found.group(1)), found.group(2)
            lists.setdefault(contest, {})[number] = (
                acronym,
                _count(found.group(4)),
                int(found.group(5)) if found.group(5) else None,
            )
    deps = {**_dep_codes(), "PRESIDENTE HAYES": 15}
    elected: dict[int, tuple[str, int]] = {}
    at = next(i for i, line in enumerate(lines) if "GOBERNADORES ELECTOS POR DEPARTAMENTO" in line)
    for line in lines[at : at + 140]:
        found = ELECTED_2013.match(line)
        if found and normal(found.group(1)) in deps and "NOMBRE" not in line:
            elected.setdefault(deps[normal(found.group(1))], (clean(found.group(2)), int(found.group(3))))
    if len(ticket) != 2 or len(elected) != 17:
        raise ValueError(f"2013 memoir: read {len(ticket)} names of the ticket and {len(elected)} governors")
    return Summary2013(lists, (ticket[0], ticket[1]), elected)


# ---------------------------------------------------------------------------------------------------------------------
# The organizations the two memoirs name that no other table of this pipeline does.

MEMOIR_SCHEME = "py.tsje.memoria"
CONCERTACION = "CONCERTACION"


def _known_names() -> frozenset[str]:
    return frozenset(normal(name) for name in (*PARTIES_2008.values(), *NATIONAL_PARTIES.values()))


LISTS_2010: dict[str, tuple[int, str]] = {}


def memoir_names() -> dict[str, str]:
    """Normalized name -> spelling of each organization the memoirs name, that the other packages do not hold."""
    found: dict[str, str] = {}
    for acronym in ACRONYMS_2013:
        for dep in (15, 17) if acronym == "PCH" else (0,):
            found.setdefault(normal(pcn_name(acronym, dep)), pcn_name(acronym, dep))
    for _, name in LISTS_2010.values():
        found.setdefault(normal(name), name)
    return {n: name for n, name in sorted(found.items()) if n not in _known_names()}


def memoir_party(name: str) -> str | None:
    """The party record of a list's organization: the shared one when the pipeline already holds it, else the memoirs'."""
    if normal(name) in _known_names():
        return party(name)
    return key("party", "memoir", normal(name))


def memoir_kind(name: str) -> str:
    return "bloc" if normal(name).startswith(CONCERTACION) else _party_kind(name, {})


def memoir_parties() -> Package:
    p = Package(
        f"{SOURCE}/memoir-parties",
        SOURCE,
        "The parties, alliances, and movements named in the Tribunal's memoirs of the 2010 municipal and 2013 general "
        "elections that its other tables do not name",
    )
    p.external |= {PARAGUAY}
    for normalized, name in memoir_names().items():
        id = key("party", "memoir", normalized)
        p.add("party", id=id, kind=memoir_kind(name), polity_id=PARAGUAY)
        p.add("external_id", entity_id=id, scheme=MEMOIR_SCHEME, value=normalized)
        p.name(id, name, "es", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------

GENERAL_2013_NOTE = (
    "Counted by the Tribunal Superior de Justicia Electoral and printed, by municipality (distrito), in the results "
    "chapter of its memoir of the general and departmental elections of 21 April 2013. Valid votes are the votes for "
    "lists; blank and null ballots are the district's own counts, and ballots cast are the district total the memoir "
    "prints. Registered voters are the district's roll as printed. The sums of the districts agree with the "
    "department rows and with the national totals the memoir prints, for every list. The memoir also breaks the "
    "Capital's results down by zone; the zones are not stored, and the Capital's own total is one unit. Voters abroad "
    "are reported by country (Argentina, Spain, and the United States) and have no place in the archive's geography. "
    "A numbered column of the memoir's tables is read as the list its acronym names in that department; an alliance "
    "that ran under its own acronym in one department ran as list 4."
)
CONCEPCION_NOTE = (
    "The memoir's table of Concepción's districts for the Senate reports 50,715 valid and invalid ballots, about two "
    "thirds of the 76,304 that its department row and national summary print, and gives a participation above 100% "
    "in several districts, so those district figures are not stored. The department is one unit here, with the "
    "department row's figures."
)


def _units2013(p: Package, day: date, places: list[tuple[int, int]]) -> dict[tuple[int, int], str]:
    rows = []
    unit_of: dict[tuple[int, int], str] = {}
    for dep, dis in places:
        code = f"{day}:{dep}" if dis == DEPARTMENT_UNIT else f"{day}:{dep}:{dis}"
        unit_of[(dep, dis)] = key("unit", code)
        if dep == ABROAD:
            scheme, area_id, relation = "py.distrito", None, "unlocated"
        elif dis == DEPARTMENT_UNIT:
            scheme, area_id, relation = "py.departamento", area("py.departamento", str(dep)), "equals"
        else:
            scheme, area_id, relation = "py.distrito", area("py.distrito", f"{dep}:{dis}"), "equals"
        rows.append((unit_of[(dep, dis)], scheme, code, relation, area_id))
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            rows,
            schema={
                "id": pl.String,
                "scheme_code": pl.String,
                "code": pl.String,
                "area_relation": pl.String,
                "area_id": pl.String,
            },
            orient="row",
        ).with_columns(polity_id=pl.lit(PARAGUAY), kind=pl.lit("aggregate")),
    )
    return unit_of


def general_2013() -> Package:
    day = GENERAL_2013
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "Paraguay's general elections of 21 April 2013 (President, Senate, Mercosur Parliament, Chamber of Deputies, "
        "and governors), by municipality, from the Tribunal Superior de Justicia Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/history", f"{SOURCE}/memoir-parties"],
    )
    p.external |= {PARAGUAY, COUNTRY}
    source_id = key("source", SOURCE, "general-2013")
    _source(
        p,
        source_id,
        "Memoria y Estadísticas Electorales: elecciones generales y departamentales de 2013",
        original(MEMORIA_2013),
        [MEMORIA_2013],
    )
    election_id = key("election", day)
    path = f"py/{day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=PARAGUAY,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, "2013 Paraguayan general election", "en", "official")
    p.name(election_id, "Elecciones generales 2013", "es", "native", False)

    memoir, summary = memoir2013(), summary2013()
    unit_of = _units2013(p, day, sorted({(dep, dis) for _, dep, dis in memoir.districts}))
    names = _dep_names()
    slugs = {dep: changeset.slug(name) for dep, name in names.items()}
    values: list[pl.DataFrame] = []

    def contest(code: str, dep: int | None, spec: Spec) -> None:
        mine = {
            (d, dis): row for (c, d, dis), row in memoir.districts.items() if c == code and (dep is None or d == dep)
        }
        order: list[str] = []
        for _, row in sorted(mine.items()):
            order += [column for column in row if column not in META_2008 and column not in order]
        labels: dict[int, tuple[str, str | None]] = {}
        slot: dict[str, int] = {}
        for column in order:
            number = ACRONYMS_2013[column][0]
            name = pcn_name(column, 0 if dep is None else dep)
            if number in labels:
                raise ValueError(f"2013 memoir: {code} {dep}: two columns on list {number}")
            labels[number], slot[column] = (name, memoir_party(name)), number
        rows = [
            (unit_of[place], slot[column], value)
            for place, row in mine.items()
            for column, value in row.items()
            if column not in META_2008
        ]
        accounting = [
            (unit_of[place], row.get("ELECTORES") or None, row["TOTAL"], row.get("BLANCOS"), row.get("NULOS"))
            for place, row in mine.items()
        ]
        printed = memoir.national.get(code) if dep is None else memoir.totals.get((code, dep))
        declared: list[tuple[int | None, int, int]] = []
        for column, value in (printed or {}).items():
            if column in slot:
                declared.append((slot[column], 1, value))
        if printed:
            for column, measure in (("BLANCOS", BLANK), ("NULOS", INVALID)):
                if column in printed:
                    declared.append((None, measure, printed[column]))
            if all(row.get("ELECTORES") for row in mine.values()) and "ELECTORES" in printed:
                declared.append((None, REGISTERED, printed["ELECTORES"]))
        winner: int | None = None
        members: tuple[tuple[int, int, str], ...] = ()
        seats: dict[int, int] | None = None
        if code == "president":
            winner = 1
            members = tuple((winner, rank, name) for rank, name in enumerate(summary.ticket, 1))
        elif code == "governor" and dep is not None:
            name, winner = summary.governors[dep]
            members = ((winner, 1, name),)
        elif code in ("senate", "parlasur"):
            seats = {lista: n for lista, (_, _, n) in summary.lists[code].items() if n}
            if lista_set := set(summary.lists[code]) - set(labels):
                raise ValueError(f"2013 memoir: {code} summary lists {sorted(lista_set)} are not in its tables")
        tally = Tally(
            rows=pl.DataFrame(rows, schema=["unit_id", "lista", "votes"], orient="row"),
            totals=pl.DataFrame(
                accounting,
                schema={
                    "unit_id": pl.String,
                    "registered": pl.Int64,
                    "total": pl.Int64,
                    "blank": pl.Int64,
                    "invalid": pl.Int64,
                },
                orient="row",
            ),
            labels=labels,
            seats=seats,
            winner=winner,
            members=members,
            declared=tuple(declared),
        )
        values.append(_add_contest(p, election_id, day, path, spec, tally, source_id, GENERAL_2013_NOTE))
        if code == "senate" and (1, DEPARTMENT_UNIT) in unit_of and (1, DEPARTMENT_UNIT) in mine:
            result_set = key("result_set", day, spec.key)
            p.add(
                "note",
                id=key("note", result_set, "department"),
                entity_id=result_set,
                kind="caveat",
                body_md=CONCEPCION_NOTE,
                lang="en",
            )

    contest(
        "president",
        None,
        Spec(
            "president",
            "office",
            "2013 Paraguayan presidential election",
            "Elección presidencial 2013",
            "president",
            COUNTRY,
            PRESIDENCY,
            None,
            PRESIDENT_SYSTEM,
            1,
            "ticket",
        ),
    )
    contest(
        "senate",
        None,
        Spec(
            "senate",
            "list_allocation",
            "2013 Paraguayan Senate election",
            "Elección de senadores 2013",
            "senate",
            COUNTRY,
            None,
            SENATE,
            SENATE_SYSTEM,
            45,
            "list",
        ),
    )
    contest(
        "parlasur",
        None,
        Spec(
            "parlasur",
            "list_allocation",
            "2013 election of Paraguay's members of the Mercosur Parliament",
            "Elección de parlamentarios del Mercosur 2013",
            "mercosur-parliament",
            COUNTRY,
            None,
            PARLASUR,
            PARLASUR_SYSTEM,
            18,
            "list",
        ),
    )
    for dep in sorted({d for c, d, _ in memoir.districts if c == "deputies"}):
        contest(
            "deputies",
            dep,
            Spec(
                f"deputies:{dep}",
                "list_allocation",
                f"2013 Chamber of Deputies election, {names[dep]}",
                f"Elección de diputados 2013, {names[dep]}",
                f"deputies-{slugs[dep]}",
                area("py.departamento", str(dep)),
                None,
                CHAMBER,
                CHAMBER_SYSTEM,
                None,
                "list",
            ),
        )
    for dep in sorted({d for c, d, _ in memoir.districts if c == "governor"}):
        contest(
            "governor",
            dep,
            Spec(
                f"governor:{dep}",
                "office",
                f"2013 governor election, {names[dep]}",
                f"Elección de gobernador 2013, {names[dep]}",
                f"governor-{slugs[dep]}",
                area("py.departamento", str(dep)),
                key("office", "governor", dep),
                None,
                GOVERNOR_SYSTEM,
                1,
                "candidate",
            ),
        )
    p.frame(
        "result_value",
        pl.concat(values).with_columns(
            channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
        ),
    )
    return p


# ---------------------------------------------------------------------------------------------------------------------
# The municipal elections of 7 November 2010, from the Tribunal's memoir of that election.

HEADING_2010 = re.compile(r"Resultados para (Intendentes|Junta Municipal)\b(?: - (Primera|Segunda) Parte)?")
ROW_2010 = re.compile(r"^\s*(\d+)\s+(\S.*?)\s{2,}(\d.*)$")
TOTALS_2010 = re.compile(r"^\s*TOTALES\s+(\d.*)$")
FIXED_2010 = ("ELECTORES", "MESAS", "BLANCOS", "NULOS", "TOTAL")
CONTESTS_2010 = {"Intendentes": "mayor", "Junta Municipal": "council"}
ALLIANCE_2010 = re.compile(r"lista(\d+)|ALI/MOV\d+")
LEGEND_2010 = re.compile(
    r"(\d+|RESTO DE MUNICIPIOS)\s+(?:(\S.*?)\s{2,})?(\d+)\s+(\S+)\s{2,}(\S.*?)(?=\s{3,}(?:\d+\s+\S|RESTO)|\s*$)"
)
# The organizations that run under the same list number everywhere, by the acronym the tables print without dots.
LISTS_2010 = {
    "ANR": (1, NATIONAL_PARTIES[(1, "ANR")]),
    "PLRA": (2, NATIONAL_PARTIES[(2, "PLRA")]),
    "PRF": (3, NATIONAL_PARTIES[(3, "PRF")]),
    "PPS": (4, PARTIES_2008["PPS"]),
    "PHP": (5, PARTIES_2008["PHP"]),
    "PDC": (6, NATIONAL_PARTIES[(6, "PDC")]),
    "UNACE": (7, NATIONAL_PARTIES[(7, "UNACE")]),
    "PPQ": (8, NATIONAL_PARTIES[(8, "PPQ")]),
    "PEN": (9, NATIONAL_PARTIES[(9, "PEN")]),
    "FG": (40, "FRENTE GUAZU"),
    "PPT": (99, "PARTIDO POPULAR TEKOJOJA"),
    "PDP": (100, PARTIES_2008["PDP"]),
    "M20A": (20, "MOVIMIENTO 20 DE ABRIL"),
}
EDGE_2010 = 2.5  # how far a column's figures end before its header does
EXTRA_2010 = 10_000  # list numbers a header does not print are numbered from here and have no ballot position


def _acronym(text: str) -> str:
    return re.sub(r"[^A-Z0-9/]", "", text.upper())


class Table2010(NamedTuple):
    rows: dict[int, dict[int | str, int]]  # district -> list number or column name -> figure
    totals: dict[int | str, int]
    acronyms: dict[int, str]


def _placements(ends: list[float], anchors: list[float], slack: float = 14.0, limit: int = 12) -> list[tuple[int, ...]]:
    """The ways to place figures under columns in order, best first, within a little of the best fit."""
    best = _assign(ends, anchors)
    if best is None:
        return []
    base = sum(abs(ends[i] - anchors[j]) for i, j in enumerate(best))
    found: list[tuple[float, tuple[int, ...]]] = []

    def step(i: int, start: int, cost: float, chosen: list[int]) -> None:
        if len(found) > 400:
            return
        if i == len(ends):
            found.append((cost, tuple(chosen)))
            return
        for j in range(start, len(anchors) - (len(ends) - i) + 1):
            extra = abs(ends[i] - anchors[j])
            if cost + extra <= base + slack:
                chosen.append(j)
                step(i + 1, j + 1, cost + extra, chosen)
                chosen.pop()

    step(0, 0, 0.0, [])
    return [picks for _, picks in sorted(found)[:limit]]


def _settle2010(
    options: list[list[tuple[int, ...]]],
    figures: list[list[int]],
    names: list[int | str],
    printed: dict[int | str, int],
) -> list[tuple[int, ...]]:
    """Choose each row's placement so that the columns add up to the totals the table prints, when it prints them."""
    limits = [printed.get(name) for name in names]
    sums = [0] * len(names)
    chosen: list[tuple[int, ...]] = []

    def walk(i: int) -> bool:
        if i == len(options):
            return all(limit is None or sums[j] == limit for j, limit in enumerate(limits))
        for picks in options[i]:
            for j, value in zip(picks, figures[i], strict=True):
                sums[j] += value
            fits = all((limit := limits[j]) is None or sums[j] <= limit for j in picks)
            chosen.append(picks)
            if fits and walk(i + 1):
                return True
            chosen.pop()
            for j, value in zip(picks, figures[i], strict=True):
                sums[j] -= value
        return False

    if not printed or not walk(0):
        return [row[0] if row else () for row in options]
    return list(chosen)


def _read_table(lines: list[str], head: int) -> Table2010:
    """One table of the 2010 chapter, with its figures placed under the column headers by position."""
    header = lines[head]
    above = [
        line for line in lines[max(0, head - 4) : head] if "lista" in line and not line.strip().startswith("LISTA")
    ]
    if not above:
        raise ValueError("2010 memoir: a table has no list numbers above its header")
    # Each figure ends a little before the right edge of its column's header.
    lists: list[tuple[int, float]] = []
    extra = EXTRA_2010
    for found in ALLIANCE_2010.finditer(above[-1]):
        if found.group(1):
            number = int(found.group(1))
        else:
            number, extra = extra, extra + 1
        lists.append((number, found.end() - EDGE_2010))
    if len({n for n, _ in lists}) != len(lists):
        raise ValueError("2010 memoir: a table repeats a list number in its header")
    columns: list[tuple[int | str, float]] = list(lists)
    other = header.find("OTRAS LISTAS *")
    if other >= 0:
        columns.append(("OTHER", other + len("OTRAS LISTAS *") - EDGE_2010))
    elif "Otras listas" in header:
        columns.append(("AGGREGATE", header.rfind("Otras listas") + len("Otras listas") - EDGE_2010))
    # A table that breaks the other lists down closes with the totals of the lists it leaves out.
    last = max(found.end() for found in ALLIANCE_2010.finditer(above[-1]))
    for n, found in enumerate(re.finditer(r"\S(?:.*?\S)?(?=\s{3,}|$)", above[-1][last:])):
        text = clean(found.group(0))
        if other < 0 and "Otras listas" not in header and text.lower().startswith(("otras", "listas")):
            columns.append((f"AGGREGATE{n}", last + found.end() - EDGE_2010))
    for name in ("BLANCOS", "NULOS", "TOTAL"):
        ends = [m.end() for m in re.finditer(name, header)]
        if ends:
            columns.append((name, ends[-1] - 0.5))
    columns.sort(key=lambda c: c[1])
    start = header.find("MESAS") + 5
    stop = min(
        [i for i in (header.find("BLANCOS"), header.find("OTRAS LISTAS *"), header.find("Otras listas")) if i > 0]
        or [len(header)]
    )
    chunks = [clean(m.group(0)) for m in re.finditer(r"\S(?:.*?\S)?(?=\s{2,}|$)", header[start:stop])]
    words = header[start:stop].split()
    pieces = words if len(words) == len(lists) else chunks
    acronyms = {n: c for (n, _), c in zip(lists, pieces, strict=False)} if len(pieces) == len(lists) else {}
    rows: dict[int, dict[int | str, int]] = {}
    totals: dict[int | str, int] = {}
    # A legend of alliances sometimes prints to the right of the table; its numbers are not the table's.
    legend = [line.find("DETALLE") for line in lines[max(0, head - 4) : head + 2] if "DETALLE" in line]
    limit = min(legend) - 2 if legend else None
    entries: list[tuple[int | None, list[tuple[int, int]]]] = []  # (district, figures); None for the totals row
    for line in lines[head + 1 :]:
        line = line[:limit] if limit else line
        total = TOTALS_2010.match(line)
        row = None if total else ROW_2010.match(line)
        match = total or row
        if match is None:
            continue
        at = match.start(1 if total else 3)
        found = [
            (0 if m.group(0) == "-" else int(m.group(0).replace(".", "")), m.end() + at)
            for m in FIGURE_2008.finditer(line[at:])
        ]
        if len(found) < 2 + sum(1 for c, _ in columns if c in ("BLANCOS", "NULOS", "TOTAL")):
            raise ValueError(f"2010 memoir: too few figures in {line.strip()[:80]!r}")
        entries.append((None if row is None else int(row.group(1)), found))
        if row is None:
            break
    names = [c for c, _ in columns]
    anchors = [a for _, a in columns]
    printed: dict[int | str, int] = {}
    if entries and entries[-1][0] is None:
        figs = entries[-1][1][2:]
        placed = (
            list(range(len(columns))) if len(figs) == len(columns) else _assign([float(e) for _, e in figs], anchors)
        )
        if placed is None:
            raise ValueError("2010 memoir: the totals row does not fit the headers")
        printed = {names[j]: v for (v, _), j in zip(figs, placed, strict=True)}
        totals = {"ELECTORES": entries[-1][1][0][0], "MESAS": entries[-1][1][1][0], **printed}
    body = [(d, f) for d, f in entries if d is not None]
    options = []
    for _, found in body:
        ends = [float(e) for _, e in found[2:]]
        if len(ends) > len(columns):
            raise ValueError(f"2010 memoir: {len(ends)} figures do not fit {len(columns)} headers")
        options.append(_placements(ends, anchors))
    chosen = _settle2010(options, [[v for v, _ in f[2:]] for _, f in body], names, printed)
    for (district, found), picks in zip(body, chosen, strict=True):
        row_values: dict[int | str, int] = {"ELECTORES": found[0][0], "MESAS": found[1][0]}
        row_values.update({names[j]: v for (v, _), j in zip(found[2:], picks, strict=True)})
        rows[district] = row_values  # type: ignore[index]
    return Table2010(rows, totals, acronyms)


class Block2010(NamedTuple):
    contest: str
    dep: int
    line: int
    lines: list[str]


def blocks2010() -> list[Block2010]:
    lines = pdf_text(MEMORIA_2010).splitlines()
    deps = {**_dep_codes(), "PRESIDENTE HAYES": 15}
    starts = [i for i, line in enumerate(lines) if HEADING_2010.search(line)]
    out: list[Block2010] = []
    for n, at in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        body = lines[at + 1 : end]
        found = HEADING_2010.search(lines[at])
        assert found is not None
        place = next((normal(line) for line in body[:12] if normal(line) in deps), None)
        if place is None:
            # A department whose councillors share the mayors' page prints its name once, above the mayors' table.
            if not out or out[-1].contest != "mayor" or CONTESTS_2010[found.group(1)] != "council":
                continue
            dep = out[-1].dep
        else:
            dep = deps[place]
        out.append(Block2010(CONTESTS_2010[found.group(1)], dep, at, body))
    return out


def _page_table2010(contest: str, dep: int) -> Table2010:
    """A table whose text layer prints twice, read from the positions of its words.

    The page is set sideways and the memoir draws the table a second time, shifted, over the first. The words of the
    shifted copy are dropped, and the rest are placed by their coordinates: a district runs along the page's width
    and a column down its height.
    """
    import pymupdf  # imported here because only these pages need it

    name = next(n for n, d in {**_dep_codes(), "PRESIDENTE HAYES": 15}.items() if d == dep)
    doc = pymupdf.open(str(raw.cached(SOURCE, MEMORIA_2010).path))
    heading = {v: k for k, v in CONTESTS_2010.items()}[contest]
    page = next(
        pg
        for pg in doc
        if f"Resultados para {heading}" in pg.get_text()
        and name in normal(pg.get_text().replace("Ó", "O")).replace("  ", " ")
        and pg.get_text().count("COD.") >= 2
    )
    words = [((w[0] + w[2]) / 2, (w[1] + w[3]) / 2, str(w[4])) for w in page.get_text("words")]
    anchors = sorted((x, y) for x, y, t in words if t == "ELECTORES")
    if len(anchors) < 2 or not 0 < anchors[1][0] - anchors[0][0] < 10:
        raise ValueError(f"2010 memoir: {contest} {dep} does not print its table twice")
    dx, dy = anchors[1][0] - anchors[0][0], anchors[1][1] - anchors[0][1]
    spots = {(t, round(x), round(y)) for x, y, t in words}
    first = [
        (x, y, t)
        for x, y, t in words
        if not any((t, round(x - dx) + a, round(y - dy) + b) in spots for a in (-1, 0, 1) for b in (-1, 0, 1))
    ]
    code = next((y, x) for x, y, t in first if t == "COD." and abs(x - anchors[0][0]) < 4)
    heads = {t: y for x, y, t in first if t in FIXED_2010 and abs(x - anchors[0][0]) < 4}
    listed = sorted(
        ((y, int(t[5:])) for x, y, t in first if re.fullmatch(r"lista\d+", t) and -14 < x - anchors[0][0] < -4),
        reverse=True,
    )
    # The list numbers print once above each column, 'lista' lines at the lower position of the two copies.
    numbers = sorted({(round(y), n) for y, n in listed})
    columns = [(n, float(y)) for y, n in numbers] + [(name_, y) for name_, y in heads.items()]
    columns.sort(key=lambda c: -c[1])
    code_y = code[0]
    bands: list[list[tuple[float, float, str]]] = []
    for x, y, t in sorted(first):
        if x <= anchors[0][0] + 4:
            continue
        if bands and abs(bands[-1][0][0] - x) < 5:
            bands[-1].append((x, y, t))
        else:
            bands.append([(x, y, t)])
    rows: dict[int, dict[int | str, int]] = {}
    totals: dict[int | str, int] = {}
    for band in bands:
        if totals:
            break
        label = [t for x, y, t in band if abs(y - code_y) < 6 and t.isdigit()]
        is_total = any(t == "TOTALES" for _, _, t in band)
        if not label and not is_total:
            continue
        found = [
            (0 if t == "-" else int(t.replace(".", "")), y)
            for _, y, t in band
            if FIGURE_2008.fullmatch(t) and abs(y - code_y) >= 6
        ]
        order = sorted(found, key=lambda f: -f[1])
        placed = _assign([-y for _, y in order], [-y for _, y in columns])
        if placed is None:
            raise ValueError(f"2010 memoir: {contest} {dep}: figures do not fit the headers")
        values = {columns[j][0]: v for (v, _), j in zip(order, placed, strict=True)}
        if is_total:
            totals = values
        else:
            rows[int(label[0])] = values
    return Table2010(rows, totals, {})


OTHERS_2010 = 9999  # the lists the memoir leaves unnamed, in one option
SKIPPED_2010 = ("ELECTORES", "MESAS", "BLANCOS", "NULOS", "TOTAL", "OTHER")
# Two tables print twice over each other in the memoir's text layer and are read from the page's coordinates instead.
GARBLED_2010 = frozenset({("mayor", 1), ("mayor", 13)})


class Parsed2010(NamedTuple):
    districts: dict[tuple[str, int, int], dict[int, int]]  # (contest, dep, dis) -> list number -> votes
    accounting: dict[tuple[str, int, int], dict[str, int]]  # ELECTORES, BLANCOS, NULOS, TOTAL of each district
    acronyms: dict[tuple[str, int], dict[int, str]]  # (contest, dep) -> list number -> the table's acronym
    national: dict[str, dict[str, int]]  # contest -> printed totals


@functools.cache
def memoir2010() -> Parsed2010:
    """The district tables of the 2010 memoir's results chapter."""
    districts: dict[tuple[str, int, int], dict[int, int]] = {}
    accounting: dict[tuple[str, int, int], dict[str, int]] = {}
    acronyms: dict[tuple[str, int], dict[int, str]] = {}
    printed: dict[tuple[str, int], Counter[str | int]] = {}
    for block in blocks2010():
        found = [i for i, line in enumerate(block.lines) if "COD." in line and "DISTRITO" in line]
        tables = (
            [_page_table2010(block.contest, block.dep)]
            if (block.contest, block.dep) in GARBLED_2010
            else [_read_table(block.lines, head) for head in found]
        )
        for table in tables:
            where = (block.contest, block.dep)
            acronyms.setdefault(where, {}).update(table.acronyms)
            counts = printed.setdefault(where, Counter())
            for column, value in table.totals.items():
                if isinstance(column, int):
                    counts[column] += value
                elif column in ("ELECTORES", "BLANCOS", "NULOS", "TOTAL"):
                    counts.setdefault(column, value)
            for dis, values in table.rows.items():
                key_ = (block.contest, block.dep, dis)
                votes = districts.setdefault(key_, {})
                account = accounting.setdefault(key_, {})
                for column, value in values.items():
                    if column in SKIPPED_2010:
                        if column != "OTHER" and account.setdefault(str(column), value) != value:
                            raise ValueError(f"2010 memoir: {key_} prints {column} twice with different figures")
                    elif isinstance(column, int) and votes.setdefault(column, value) != value:
                        raise ValueError(f"2010 memoir: {key_} prints list {column} twice with different figures")
    # The Capital prints the lists behind its 'other lists' in a small table with no district column.
    capital = next(b for b in blocks2010() if (b.contest, b.dep) == ("council", 0))
    at = next(
        i for i, line in enumerate(capital.lines) if "OTRAS LISTAS *" in line and not line.strip().startswith("COD")
    )
    heads = ALLIANCE_2010.finditer(capital.lines[at + 1])
    anchors = [m.end() - EDGE_2010 for m in heads]
    figs_line = next(line for line in capital.lines[at + 2 :] if NUMBERS_LINE.match(line))
    found = [(_count(m.group(0)), m.end()) for m in re.finditer(r"[\d.]+", figs_line)]
    anchors.append(capital.lines[at + 1].rfind("Otras listas") + len("Otras listas") - EDGE_2010)
    numbers = [int(m.group(1)) for m in ALLIANCE_2010.finditer(capital.lines[at + 1])]
    placed = _assign([float(e) for _, e in found], anchors)
    if placed is None or len(found) != len(numbers) + 1 or sum(v for v, _ in found[:-1]) != found[-1][0]:
        raise ValueError("2010 memoir: the Capital's other lists do not add up")
    for (value, _), j in zip(found[:-1], placed, strict=False):
        districts[("council", 0, 0)][numbers[j]] = value
    for key_, votes in districts.items():
        account = accounting[key_]
        rest = account["TOTAL"] - account.get("BLANCOS", 0) - account.get("NULOS", 0) - sum(votes.values())
        if rest < 0:
            raise ValueError(f"2010 memoir: {key_} lists add up to more than the printed total")
        if rest:
            votes[OTHERS_2010] = rest
    # The department rows the memoir prints under each table must agree with the districts, for every list it names.
    for (contest, dep), counts in printed.items():
        sums: Counter[str | int] = Counter()
        for (c, d, _), votes in districts.items():
            if (c, d) == (contest, dep):
                sums.update(votes)
                sums.update(
                    {k: v for k, v in accounting[(c, d, _)].items() if k in ("ELECTORES", "BLANCOS", "NULOS", "TOTAL")}
                )
        wrong = {k: (v, sums[k]) for k, v in counts.items() if k in sums or k in (OTHERS_2010,) and sums[k] != v}
        wrong = {k: x for k, x in wrong.items() if x[0] != x[1] and k != "PARTICIPACION"}
        if wrong:
            raise ValueError(f"2010 memoir: {contest} department {dep} differs from its districts: {wrong}")
    return Parsed2010(districts, accounting, acronyms, _national2010())


def _national2010() -> dict[str, dict[str, int]]:
    """The comparison of the parties' votes for mayors and councillors that the memoir prints before its tables."""
    text = pdf_text(MEMORIA_2010)
    out: dict[str, dict[str, int]] = {"mayor": {}, "council": {}}
    patterns = (
        ("BLANCOS", r"Votos en Blanco\s+([\d.]+)\s+[\d,]+%\s+([\d.]+)"),
        ("NULOS", r"Votos Nulos\s+([\d.]+)\s+[\d,]+%\s+([\d.]+)"),
        ("TOTAL", r"TOTAL DE VOTOS PARA INTENDENTES Y CONCEJALES\s+([\d.]+)\s+([\d.]+)"),
        ("ANR", r"\n\s+ANR\s+ASOCIACION NACIONAL REPUBLICANA\s+([\d.]+)\s+[\d,]+%\s+([\d.]+)"),
        ("PLRA", r"\n\s+PLRA\s+PARTIDO LIBERAL RADICAL AUTENTICO\s+([\d.]+)\s+[\d,]+%\s+([\d.]+)"),
    )
    for label, pattern in patterns:
        found = re.search(pattern, text)
        if found is None:
            raise ValueError(f"2010 memoir: no national figure for {label}")
        out["mayor"][label], out["council"][label] = _count(found.group(1)), _count(found.group(2))
    return out


def compare2010() -> dict[str, int]:
    """Check the districts against the geography and the national figures the memoir prints."""
    memoir = memoir2010()
    known = set(districts().select("dep", "dis").iter_rows())
    unknown = {(dep, dis) for _, dep, dis in memoir.districts} - known
    if unknown:
        raise ValueError(f"2010 memoir: districts without a place in the geography: {sorted(unknown)[:5]}")
    out = {"districts": len({(dep, dis) for _, dep, dis in memoir.districts})}
    for contest, printed in memoir.national.items():
        total: Counter[str] = Counter()
        for (c, _, _), account in memoir.accounting.items():
            if c == contest:
                total.update({k: v for k, v in account.items() if k in ("BLANCOS", "NULOS", "TOTAL")})
        for (c, _, _), votes in memoir.districts.items():
            if c == contest:
                total["ANR"] += votes.get(1, 0)
        wrong = {k: (v, total[k]) for k, v in printed.items() if k in total and total[k] != v}
        if wrong:
            raise ValueError(f"2010 memoir: {contest} national totals differ from its districts: {wrong}")
        out[f"{contest} votes"] = total["TOTAL"]
    print(f"2010: {out}", flush=True)
    return out


MUNICIPAL_2010_NOTE = (
    "Counted by the Tribunal Superior de Justicia Electoral and printed, by municipality (distrito), in the results "
    "chapter of its memoir of the municipal elections of 7 November 2010. Valid votes are the votes for lists; blank "
    "and null ballots are the district's own counts, and ballots cast are the district total the memoir prints. "
    "Registered voters are the district's roll as printed. The memoir prints each list's votes in the table's "
    "column for it, and lists behind its 'other lists' column in a second table. The sums of the districts agree "
    "with the department totals the memoir prints for every list, and with its national totals of votes, blank "
    "ballots, and null ballots. A list is named by the acronym its table prints; the memoir's tables leave the "
    "names of the alliances and movements to a legend that is not read here, and a column it labels only as an "
    "alliance or movement is shown by its list number. The memoir prints the municipal councils' seats by "
    "department, not by district, and does not declare the winning mayors in these tables, so no seats or "
    "winners are stored."
)


def _label2010(contest: str, dep: int, number: int) -> tuple[str, str | None]:
    if number == OTHERS_2010:
        return "Other lists", None
    acronym = memoir2010().acronyms.get((contest, dep), {}).get(number)
    named = LISTS_2010.get(_acronym(acronym)) if acronym else None
    if named is not None and named[0] == number:
        return named[1], memoir_party(named[1])
    if acronym is None:
        fixed = {1: "ANR", 2: "PLRA"}.get(number)
        if fixed is not None:
            return LISTS_2010[fixed][1], memoir_party(LISTS_2010[fixed][1])
    if acronym and not acronym.startswith("ALI/MOV"):
        return acronym, None
    return (f"Lista {number}" if number < EXTRA_2010 else "Alianza o movimiento"), None


def municipal_2010() -> Package:
    day = MUNICIPAL_2010
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "Paraguay's municipal elections of 7 November 2010 (mayors and municipal councils), by district, from the "
        "Tribunal Superior de Justicia Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/history", f"{SOURCE}/memoir-parties"],
    )
    p.external |= {PARAGUAY}
    source_id = key("source", SOURCE, "municipal-2010")
    _source(
        p,
        source_id,
        "Elecciones Municipales 2010: Memoria y Estadísticas Electorales",
        original(MEMORIA_2010),
        [MEMORIA_2010],
    )
    election_id = key("election", day)
    path = f"py/{day}-municipal"
    p.add(
        "election",
        id=election_id,
        polity_id=PARAGUAY,
        kind="local",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, "2010 Paraguayan municipal elections", "en", "official")
    p.name(election_id, "Elecciones municipales 2010", "es", "native", False)

    memoir = memoir2010()
    frame = pl.DataFrame(sorted({(dep, dis) for _, dep, dis in memoir.districts}), schema=["dep", "dis"], orient="row")
    frame = frame.with_columns(code=pl.concat_str(pl.lit(str(day)), "dep", "dis", separator=":")).with_columns(
        unit_id=pl.col("code").map_elements(lambda code: key("unit", code), return_dtype=pl.String)
    )
    p.frame(
        "reporting_unit",
        frame.select(
            id="unit_id",
            polity_id=pl.lit(PARAGUAY),
            scheme_code=pl.lit("py.distrito"),
            code="code",
            area_relation=pl.lit("equals"),
            area_id=pl.struct("dep", "dis").map_elements(
                lambda row: area("py.distrito", f"{row['dep']}:{row['dis']}"), return_dtype=pl.String
            ),
            kind=pl.lit("aggregate"),
        ),
    )
    unit_of = {(dep, dis): unit for dep, dis, unit in frame.select("dep", "dis", "unit_id").iter_rows()}
    slugs = _slugs()
    places = {(dep, dis): title(name) for dep, _, dis, name in districts().iter_rows()}
    values: list[pl.DataFrame] = []
    for dep, dis in sorted(unit_of):
        place = places[(dep, dis)]
        district = area("py.distrito", f"{dep}:{dis}")
        for code in ("mayor", "council"):
            votes = memoir.districts[(code, dep, dis)]
            account = memoir.accounting[(code, dep, dis)]
            labels = {number: _label2010(code, dep, number) for number in sorted(votes)}
            rows = pl.DataFrame(
                [(unit_of[(dep, dis)], number, value) for number, value in votes.items()],
                schema=["unit_id", "lista", "votes"],
                orient="row",
            )
            totals = pl.DataFrame(
                [
                    (
                        unit_of[(dep, dis)],
                        account.get("ELECTORES") or None,
                        account["TOTAL"],
                        account.get("BLANCOS"),
                        account.get("NULOS"),
                    )
                ],
                schema={
                    "unit_id": pl.String,
                    "registered": pl.Int64,
                    "total": pl.Int64,
                    "blank": pl.Int64,
                    "invalid": pl.Int64,
                },
                orient="row",
            )
            positions = {n: (None if n >= EXTRA_2010 or n == OTHERS_2010 else n) for n in labels}
            if code == "mayor":
                spec = Spec(
                    f"mayor:{dep}:{dis}",
                    "office",
                    f"Mayor of {place}",
                    f"Intendente municipal de {place}",
                    f"{slugs[(dep, dis)]}-mayor",
                    district,
                    office(dep, dis),
                    None,
                    MAYOR_SYSTEM,
                    1,
                    "candidate",
                )
            else:
                spec = Spec(
                    f"council:{dep}:{dis}",
                    "list_allocation",
                    f"{place} Municipal Council",
                    f"Junta municipal de {place}",
                    f"{slugs[(dep, dis)]}-council",
                    district,
                    None,
                    body(dep, dis),
                    COUNCIL_SYSTEM_BLOCKED,
                    None,
                    "list",
                )
            tally = Tally(rows=rows, totals=totals, labels=labels, positions=positions)
            values.append(_add_contest(p, election_id, day, path, spec, tally, source_id, MUNICIPAL_2010_NOTE))
    p.frame(
        "result_value",
        pl.concat(values).with_columns(
            channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
        ),
    )
    return p
