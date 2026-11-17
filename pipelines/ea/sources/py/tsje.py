import functools
import json
import re
import subprocess
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Iterable
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
    geo().write()
    registry().write()
    election().write()
    history().write()
    for build in (general_2008, municipal_2015, general_2018, municipal_2021):
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
HISTORY_FILES = {
    **{url: ".xlsx" if "RESULTADOS_POR_MESA" in url else ".xls" for url in GENERAL_2018_FILES.values()},
    **{url: ".xlsx" for url in MUNICIPAL_2021_FILES.values()},
    ROLL: ".csv",
    MUNICIPAL_2015_FILES["resultados"]: ".pdf",
    MUNICIPAL_2015_FILES["candidatos"]: ".xlsx",
    MEMORIA_2008: ".pdf",
}
GENERAL_2008 = date(2008, 4, 20)
GENERAL_2018 = date(2018, 4, 22)
MUNICIPAL_2021 = date(2021, 10, 10)
MUNICIPAL_2015 = date(2015, 11, 15)
HISTORY_DAYS = (GENERAL_2008, MUNICIPAL_2015, GENERAL_2018, MUNICIPAL_2021)
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
    return [f"{SOURCE}/history", *(f"{SOURCE}/{day}" for day in HISTORY_DAYS)]


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


def _assign(ends: list[int], columns: list[int]) -> list[int] | None:
    """Place each figure under the header whose right edge is nearest, keeping their order."""
    k, n, inf = len(ends), len(columns), 10**9
    cost = [[inf] * (n + 1) for _ in range(k + 1)]
    take = [[False] * (n + 1) for _ in range(k + 1)]
    cost[0] = [0] * (n + 1)
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
