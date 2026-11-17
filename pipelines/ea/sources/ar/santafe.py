import csv
import functools
import io
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from uuid import uuid5

import fastexcel
import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "ar.santafe"
HOST = "https://www.santafe.gob.ar/ms"
FILES = "https://www.santafe.gov.ar/tribunalelectoral/wp-content/uploads/sites/84"
UPLOADS = f"{HOST}/wp-content/uploads/sites/84/2026/11"
PAGE = f"{HOST}/tribunalelectoral/elecciones/"
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ARG"
ARGENTINA = str(uuid5(package.NAMESPACE, "polity\x1fAR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fARG"))
CIRCUITS = Path(__file__).with_name("santafe_circuits.json")

PROVINCE = "AR-S"
CITY = 9999
NULL, BLANK = 9998, 9999
PASO_DAY = date(2025, 4, 13)
GENERAL_DAY = date(2025, 6, 29)

URL_GENERAL = f"{FILES}/2025/07/resultadosGenerales2025.ods"
URL_WINNERS = f"{FILES}/2025/07/candidatosGanadoresGenerales2025.ods"
URL_PRIMARY = f"{FILES}/2025/07/resultadosPrimarias2025.ods"
URL_PRIMARY_WINNERS = f"{FILES}/2025/07/candidatosGandoresPrimarias2025.ods"
URL_CANDIDATES = f"{FILES}/2025/07/candidatosPrimarias2025.ods"
URL_MESAS = f"{UPLOADS}/renglones2025.csv"
URL_UNIQUE = f"{UPLOADS}/RDU.pdf"


def url_department(secc: int) -> str:
    return f"{UPLOADS}/{secc}RD.pdf"


DEPARTMENTS = {
    1: ("La Capital", "La Capital"),
    2: ("Las Colonias", "Las Colonias"),
    3: ("San Jerónimo", "San Jerónimo"),
    4: ("Garay", "Garay"),
    5: ("Castellanos", "Castellanos"),
    6: ("San Martín", "San Martín"),
    7: ("San Cristobal", "San Cristóbal"),
    8: ("San Justo", "San Justo"),
    9: ("General Obligado", "General Obligado"),
    10: ("Vera", "Vera"),
    11: ("San Javier", "San Javier"),
    12: ("9 de Julio", "9 de Julio"),
    13: ("Rosario", "Rosario"),
    14: ("Caseros", "Caseros"),
    15: ("Constitución", "Constitución"),
    16: ("General López", "General López"),
    17: ("Belgrano", "Belgrano"),
    18: ("Iriondo", "Iriondo"),
    19: ("San Lorenzo", "San Lorenzo"),
}
GENERAL_METHOD = (
    "Counted by mesa (polling table) by the Tribunal Electoral of Santa Fe and published as one row per mesa, "
    "category, and list in its definitive-count results file. Mesas are grouped into circuits and each circuit into "
    "the municipality or commune it votes in; the file does not name the municipality, so the grouping was "
    "reconstructed and checked against the locality totals the Tribunal prints in its results workbook, which "
    "agree exactly. Votes are votes for lists; blank and null ballots are stored per mesa and are not counted in "
    "the list votes. A mesa the file marks with type E is kept apart from the numbered mesa that shares its number."
)
PASO_METHOD = (
    "Counted by circuit by the Tribunal Electoral of Santa Fe and published in its definitive-count results "
    "workbook, one row per circuit, party, and internal list. Each municipality or commune is one circuit in this "
    "file. Blank and null ballots are stored per circuit and are not counted in the list votes."
)
CONVENTION_METHOD = (
    "Counted by circuit by the Tribunal Electoral of Santa Fe and published in its definitive-count results "
    "workbook, one row per circuit and party. Fifty convention seats are elected from the whole province and one "
    "from each of the 19 departments, each voter marking both. Blank and null ballots are stored per circuit and are "
    "not counted in the party votes."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: object) -> str:
    return key("area", scheme, code)


SOURCE_ID = key("source", "tribunal")
GEO_SOURCE = key("source", "geoboundaries")
CONVENTION = key("body", "convention")
PROVINCE_AREA = area("ar-s.provincia", PROVINCE)


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        f"{SOURCE}/2025-04-13-convencion",
        f"{SOURCE}/2025-04-13-paso",
        f"{SOURCE}/2025-06-29-general",
    ]


def fetch() -> None:
    for url in (URL_GENERAL, URL_WINNERS, URL_PRIMARY, URL_PRIMARY_WINNERS, URL_CANDIDATES):
        raw.fetch(SOURCE, url, suffix=".ods")
    raw.fetch(SOURCE, URL_MESAS, suffix=".csv")
    raw.fetch(SOURCE, URL_UNIQUE, suffix=".pdf")
    for secc in DEPARTMENTS:
        raw.fetch(SOURCE, url_department(secc), suffix=".pdf")
    for level in ("ADM1", "ADM2"):
        raw.fetch(SOURCE, f"{GEOBOUNDARIES}/{level}/geoBoundaries-ARG-{level}_simplified.geojson", suffix=".geojson")


def parse() -> None:
    geo().write()
    registry().write()
    convention().write()
    primary().write()
    general().write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------------------------------------------------------------
# Reading the source files


def _sheet(url: str) -> pl.DataFrame:
    return fastexcel.read_excel(str(raw.cached(SOURCE, url).path)).load_sheet(0, header_row=0).to_polars()


@functools.cache
def _general() -> pl.DataFrame:
    return _sheet(URL_GENERAL).with_columns(pl.col("secc", "circuito", "partido", "part").cast(pl.Int64))


@functools.cache
def _primary() -> pl.DataFrame:
    return _sheet(URL_PRIMARY).rename(
        {
            "Convencional Reformador Distrito Único": "unico",
            "Convencional Reformador Departamental": "departamental",
            "miembro comunal": "comunal",
        }
    )


@functools.cache
def _winners() -> pl.DataFrame:
    return _sheet(URL_WINNERS)


@functools.cache
def _primary_winners() -> pl.DataFrame:
    return _sheet(URL_PRIMARY_WINNERS)


@functools.cache
def _candidates() -> pl.DataFrame:
    return _sheet(URL_CANDIDATES)


@functools.cache
def localities() -> dict[tuple[int, int], dict[str, object]]:
    """One entry for each municipality or commune, keyed by department and its main circuit."""
    frame = _general().select("departamento", "localidad", "secc", "circuito", "tipo").unique()
    found: dict[tuple[int, int], dict[str, object]] = {}
    for row in frame.iter_rows(named=True):
        found[(int(row["secc"]), int(row["circuito"]))] = {
            "name": str(row["localidad"]).strip(),
            "kind": "municipio" if row["tipo"] == "M" else "comuna",
        }
    return found


@functools.cache
def circuit_map() -> dict[tuple[int, int], int]:
    """Where each circuit of the mesa file votes, as the main circuit of its municipality or commune."""
    moved = {
        tuple(int(part) for part in text.split(":")): int(target)
        for text, target in json.loads(CIRCUITS.read_text()).items()
    }
    return {(secc, circuit): target for (secc, circuit), target in moved.items()}


def home(secc: int, circuit: int) -> int:
    return circuit_map().get((secc, circuit), circuit)


@functools.cache
def mesas() -> list[dict[str, str]]:
    text = raw.cached(SOURCE, URL_MESAS).path.read_bytes().decode("cp1252")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def number(text: str) -> int:
    return int(text.replace(".", ""))


def pdf_text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def declared_totals(url: str) -> tuple[dict[int, int], dict[str, int]]:
    """Party votes by draw number, and the accounting lines, from a definitive-count summary page."""
    parties: dict[int, int] = {}
    figures: dict[str, int] = {}
    for line in pdf_text(url).split("\f")[0].splitlines():
        entry = re.match(r"^\s*(\d+)\s+\S.*?\s{2,}([\d.]+)\s+[\d.]+(?:\s*\(\*\))?\s*$", line)
        if entry and "TOTAL" not in line:
            parties[int(entry.group(1))] = number(entry.group(2))
            continue
        for label, name in (
            (r"TOTAL ELECTORES:\s+([\d.]+)", "registered"),
            (r"VOTOS BLANCOS\s+([\d.]+)", "blank"),
            (r"VOTOS ANULADOS\s+([\d.]+)", "invalid"),
            (r"TOTAL DE VOTANTES\s+([\d.]+)", "voters"),
            (r"VOTOS V[ÁA]LIDOS\s+([\d.]+)", "valid"),
        ):
            match = re.search(label, line)
            if match and name not in figures:
                figures[name] = number(match.group(1))
    return parties, figures


# ---------------------------------------------------------------------------------------------------------------------
# Geography


def _outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    shape = shapely.set_srid(shapely.make_valid(shape), 4326)
    if shapely.get_type_id(shape) == 3:
        shape = shapely.MultiPolygon([shape])
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=GEO_SOURCE,
    )


def _departments() -> dict[int, shapely.Geometry]:
    boundary = raw.cached(SOURCE, f"{GEOBOUNDARIES}/ADM1/geoBoundaries-ARG-ADM1_simplified.geojson")
    province = next(
        shapely.geometry.shape(feature["geometry"])
        for feature in json.loads(boundary.path.read_bytes())["features"]
        if feature["properties"]["shapeName"] == "Santa Fe"
    )
    districts = raw.cached(SOURCE, f"{GEOBOUNDARIES}/ADM2/geoBoundaries-ARG-ADM2_simplified.geojson")
    inside = {
        feature["properties"]["shapeName"]: shapely.geometry.shape(feature["geometry"])
        for feature in json.loads(districts.path.read_bytes())["features"]
        if province.contains(shapely.geometry.shape(feature["geometry"]).representative_point())
    }
    shapes = {secc: inside[name] for secc, (name, _) in DEPARTMENTS.items()}
    if len(inside) != len(DEPARTMENTS):
        raise ValueError(f"expected {len(DEPARTMENTS)} departments inside Santa Fe, found {sorted(inside)}")
    return shapes


def locality_area(secc: int, circuit: int) -> str:
    return area("ar-s.localidad", f"{secc}-{circuit}")


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Santa Fe province, its 19 departments, and its municipalities and communes as the Tribunal Electoral lists "
        "them",
    )
    p.external |= {ARGENTINA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Tribunal Electoral de la Provincia de Santa Fe",
        title="Elecciones: resultados, archivos y documentación",
        url=PAGE,
        license="Public information published by the provincial government",
        license_url="https://www.santafe.gob.ar/ms/tribunalelectoral/",
        attribution="Tribunal Electoral de la Provincia de Santa Fe",
    )
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen Argentina, administrative levels 1 and 2",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="geoBoundaries, https://www.geoboundaries.org, CC BY 3.0 IGO",
    )
    for level in ("ADM1", "ADM2"):
        p.artifact(
            raw.cached(SOURCE, f"{GEOBOUNDARIES}/{level}/geoBoundaries-ARG-{level}_simplified.geojson"), GEO_SOURCE
        )
    p.uses(raw.cached(SOURCE, URL_GENERAL))
    for code, rank, kind, noun, kinds in (
        ("ar-s.provincia", 1, "administrative", "province", None),
        ("ar-s.departamento", 2, "administrative", "department", None),
        ("ar-s.localidad", 3, "administrative", "locality", {"municipio": "municipality", "comuna": "commune"}),
        ("ar-s.circuito", 4, "precinct", "circuit", None),
        ("ar-s.mesa", 5, "precinct", "polling table", None),
    ):
        p.add("scheme", code=code, polity_id=ARGENTINA, kind=kind, rank=rank, noun=noun, kinds=kinds, code_system=None)
    retrieved = raw.cached(SOURCE, f"{GEOBOUNDARIES}/ADM2/geoBoundaries-ARG-ADM2_simplified.geojson").retrieved_at
    for code in ("ar-s.provincia", "ar-s.departamento"):
        p.add(
            "boundary_set",
            id=key("boundary_set", code, 2020),
            scheme_code=code,
            vintage="2020",
            as_of=retrieved.date(),
        )
    shapes = _departments()
    p.add("area", id=PROVINCE_AREA, scheme_code="ar-s.provincia")
    p.add("area_code", area_id=PROVINCE_AREA, code=PROVINCE)
    p.add("area_parent", area_id=PROVINCE_AREA, parent_id=COUNTRY)
    p.name(PROVINCE_AREA, "Santa Fe", "es", "official")
    p.name(PROVINCE_AREA, "Santa Fe Province", "en", "english", False)
    _outline(p, PROVINCE_AREA, shapely.coverage_union_all(list(shapes.values())))
    for secc, (_, name) in DEPARTMENTS.items():
        id = area("ar-s.departamento", secc)
        p.add("area", id=id, scheme_code="ar-s.departamento")
        p.add("area_code", area_id=id, code=str(secc))
        p.add("area_parent", area_id=id, parent_id=PROVINCE_AREA)
        p.name(id, name, "es", "official")
        _outline(p, id, shapes[secc])
    for (secc, circuit), place in sorted(localities().items()):
        id = locality_area(secc, circuit)
        p.add("area", id=id, scheme_code="ar-s.localidad")
        p.add("area_code", area_id=id, code=f"{secc}-{circuit}")
        p.add("area_parent", area_id=id, parent_id=area("ar-s.departamento", secc))
        p.add("area_kind", area_id=id, kind=place["kind"], source_id=SOURCE_ID)
        p.name(id, str(place["name"]), "es", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Registry


def party(code: int) -> str:
    return key("party", code)


def office(secc: int, circuit: int) -> str:
    return key("office", "mayor", secc, circuit)


def council(secc: int, circuit: int) -> str:
    return key("body", "council", secc, circuit)


def _party_names() -> dict[int, str]:
    names: dict[int, str] = {}
    for frame in (
        _candidates().select(pl.col("par").cast(pl.Int64).alias("code"), pl.col("partido").alias("name")),
        _general().select(pl.col("part").alias("code"), pl.col("nombre").alias("name")),
    ):
        for row in frame.iter_rows(named=True):
            code = int(row["code"])
            if code > 0 and row["name"]:
                names[code] = " ".join(str(row["name"]).split())
    return names


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Santa Fe's constitutional convention, municipal and communal bodies and offices, and the parties and "
        "alliances on the 2025 ballots",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(ARGENTINA)
    for url in (
        URL_GENERAL,
        URL_WINNERS,
        URL_PRIMARY,
        URL_PRIMARY_WINNERS,
        URL_CANDIDATES,
        URL_MESAS,
        URL_UNIQUE,
        *(url_department(secc) for secc in DEPARTMENTS),
    ):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add("body", id=CONVENTION, polity_id=ARGENTINA, kind="constituent_assembly")
    p.name(CONVENTION, "Santa Fe Constitutional Convention", "en", "official")
    p.name(CONVENTION, "Convención Reformadora de la Constitución de Santa Fe", "es", "native", False)
    for (secc, circuit), place in sorted(localities().items()):
        name = str(place["name"])
        body = council(secc, circuit)
        p.add("body", id=body, polity_id=ARGENTINA, kind="council")
        if place["kind"] == "municipio":
            p.name(body, f"Municipal Council of {name}", "en", "official")
            p.name(body, f"Concejo Municipal de {name}", "es", "native", False)
            id = office(secc, circuit)
            p.add("office", id=id, polity_id=ARGENTINA, kind="mayor")
            p.name(id, f"Mayor of {name}", "en", "official")
            p.name(id, f"Intendente de {name}", "es", "native", False)
        else:
            p.name(body, f"Communal Commission of {name}", "en", "official")
            p.name(body, f"Comisión Comunal de {name}", "es", "native", False)
    for code, name in sorted(_party_names().items()):
        id = party(code)
        p.add("party", id=id, kind="bloc" if code >= 9000 else "party", polity_id=ARGENTINA)
        p.add("external_id", entity_id=id, scheme="ar.santafe.party", value=str(code))
        p.name(id, name, "es", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Shared pieces of an election package


class Builder:
    """Collects the contests, units, and values of one election."""

    def __init__(self, p: Package, tag: str, day: date) -> None:
        self.p = p
        self.tag = tag
        self.day = day
        self.slugs: set[str] = set()
        self.units: dict[str, str] = {}
        self.contests: dict[tuple[object, ...], str] = {}
        self.sets: dict[tuple[object, ...], str] = {}
        self.options: dict[tuple[object, ...], str] = {}
        self.options_of: dict[str, list[tuple[object, str]]] = defaultdict(list)

    def election(self, kind: str, en: str, es: str, slug: str) -> str:
        id = key("election", self.tag)
        self.p.add(
            "election", id=id, polity_id=ARGENTINA, kind=kind, date_from=self.day, date_to=self.day, status="held"
        )
        self.p.name(id, en, "en", "official")
        self.p.name(id, es, "es", "native", False)
        self.p.add("slug", path=slug, entity_id=id, is_current=True)
        self.slug = slug
        return id

    def unit(self, scheme: str, code: str, secc: int) -> str:
        if code not in self.units:
            id = self.units[code] = key("unit", self.tag, scheme, code)
            self.p.add(
                "reporting_unit",
                id=id,
                polity_id=ARGENTINA,
                scheme_code=scheme,
                code=f"{self.tag}:{code}",
                area_relation="within",
                area_id=area("ar-s.departamento", secc),
                kind="standard",
            )
        return self.units[code]

    def contest(
        self,
        scope: tuple[object, ...],
        title: str,
        kind: str,
        district: str,
        magnitude: int | None,
        *,
        election: str,
        office_id: str | None = None,
        body_id: str | None = None,
        method: str,
        label: str,
    ) -> str:
        if scope in self.contests:
            return self.contests[scope]
        p = self.p
        id = self.contests[scope] = key("contest", self.tag, *scope)
        p.add(
            "contest",
            id=id,
            election_id=election,
            kind=kind,
            office_id=office_id,
            body_id=body_id,
            district_area_id=district,
            magnitude=magnitude,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(id, title, "es", "official")
        path = f"{self.slug}/{changeset.slug(label)}"
        if path in self.slugs:
            path = f"{path}-{changeset.slug('-'.join(str(part) for part in scope))}"
        self.slugs.add(path)
        p.add("slug", path=path, entity_id=id, is_current=True)
        result_set = self.sets[scope] = key("result_set", self.tag, *scope)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        return id

    def option(self, scope: tuple[object, ...], party_code: int | None, listing: int, label: str, position: int) -> str:
        found = (*scope, party_code, listing)
        if found not in self.options:
            id = self.options[found] = key("option", self.tag, *found)
            self.p.add(
                "contest_option",
                id=id,
                contest_id=self.contests[scope],
                kind="list",
                party_id=party(party_code) if party_code else None,
                ballot_position=position,
                ballot_label=label or None,
            )
        return self.options[found]

    def vote(self, scope: tuple[object, ...], unit: str, option: str | None, measure: int, value: int) -> None:
        self.p.add(
            "result_value",
            result_set_id=self.sets[scope],
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )


def _title(category: str, place: str) -> str:
    return {
        "Intendente": f"Intendente de {place}",
        "Concejal": f"Concejo Municipal de {place}",
        "Miembro Comunal": f"Comisión Comunal de {place}",
    }[category]


def _municipal_contest(
    b: Builder, election: str, category: str, secc: int, circuit: int, method: str, magnitude: int | None
) -> str:
    place = str(localities()[(secc, circuit)]["name"])
    scope = (category, secc, circuit)
    if category == "Intendente":
        return b.contest(
            scope,
            _title(category, place),
            "office",
            locality_area(secc, circuit),
            magnitude,
            election=election,
            office_id=office(secc, circuit),
            method=method,
            label=f"{place} intendente",
        )
    return b.contest(
        scope,
        _title(category, place),
        "list_allocation",
        locality_area(secc, circuit),
        magnitude,
        election=election,
        body_id=council(secc, circuit),
        method=method,
        label=f"{place} {'concejo' if category == 'Concejal' else 'comision comunal'}",
    )


def _citations(p: Package, urls: list[str]) -> None:
    for url in urls:
        p.uses(raw.cached(SOURCE, url))


def _vote(b: Builder, scope: tuple[object, ...], unit: str, draw: int, option: str | None, value: int) -> None:
    if draw == NULL:
        b.vote(scope, unit, None, 5, value)
    elif draw == BLANK:
        b.vote(scope, unit, None, 6, value)
    else:
        b.vote(scope, unit, option, 1, value)


def _elected(b: Builder, scope: tuple[object, ...], winners: pl.DataFrame, head: bool, code_of: dict[int, int]) -> None:
    """Adds the declared titular winners of one contest, and each list's seats."""
    p = b.p
    result_set = b.sets[scope]
    seats: Counter[str] = Counter()
    for row in winners.iter_rows(named=True):
        draw = int(row["sortpar"])
        options = [id for found, id in b.options.items() if found[:-2] == scope and found[-2] == code_of[draw]]
        if len(options) != 1:
            raise ValueError(f"cannot place the winner of {scope} on list {draw}: {len(options)} candidates")
        option = options[0]
        tag = f"{b.tag}:{':'.join(str(part) for part in scope)}:{draw}:{int(row['orden'])}"
        person = key("person", tag)
        p.add("person", id=person, gender={"M": "male", "F": "female"}.get(str(row.get("sexo") or "")))
        name = " ".join(str(row["nombre"]).split())
        p.name(person, name, "es", "official")
        candidacy = key("candidacy", tag)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="head" if head else "list_member",
            list_position=int(row["orden"]),
            ballot_name=name,
            member_of_party_id=party(code_of[draw]),
        )
        if head:
            p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="won")
        else:
            p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
            seats[option] += 1
    for option, n in seats.items():
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=n)


# ---------------------------------------------------------------------------------------------------------------------
# 13 April 2025: constitutional convention


def _sorteo_to_party() -> dict[int, int]:
    """April's draw numbers mapped to the party codes the candidate file pairs them with."""
    found = _candidates().select(pl.col("par").cast(pl.Int64), pl.col("sortpar").cast(pl.Int64)).unique()
    return {int(row["sortpar"]): int(row["par"]) for row in found.iter_rows(named=True)}


def convention() -> Package:
    p = Package(
        f"{SOURCE}/2025-04-13-convencion",
        SOURCE,
        "The 13 April 2025 election of Santa Fe's constitutional convention by circuit from the Tribunal "
        "Electoral's results workbook",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ARGENTINA)
    _citations(p, [URL_PRIMARY, URL_PRIMARY_WINNERS, URL_CANDIDATES, URL_UNIQUE])
    _citations(p, [url_department(secc) for secc in DEPARTMENTS])
    b = Builder(p, "2025-04-13-convencion", PASO_DAY)
    election = b.election(
        "general",
        "2025 Santa Fe constitutional convention election",
        "Elección de convencionales reformadores de Santa Fe 2025",
        "ar/santa-fe/2025-04-13-convention",
    )
    code_of = _sorteo_to_party()
    frame = _primary()
    winners = (
        _primary_winners()
        .filter(pl.col("situacion") == "Titular")
        .with_columns(pl.col("sec").cast(pl.Int64), pl.col("sortpar").cast(pl.Int64), pl.col("orden").cast(pl.Int64))
        .rename({"apellido y nombre": "nombre"})
    )
    unique: tuple[object, ...] = ("unico",)
    b.contest(
        unique,
        "Convencionales reformadores de distrito único",
        "list_allocation",
        PROVINCE_AREA,
        50,
        election=election,
        body_id=CONVENTION,
        method=CONVENTION_METHOD,
        label="distrito unico",
    )
    scopes: dict[int, tuple[object, ...]] = {}
    for secc, (_, name) in DEPARTMENTS.items():
        scopes[secc] = ("departamental", secc)
        b.contest(
            scopes[secc],
            f"Convencional reformador departamental de {name}",
            "list_allocation",
            area("ar-s.departamento", secc),
            1,
            election=election,
            body_id=CONVENTION,
            method=CONVENTION_METHOD,
            label=f"departamento {name}",
        )
    frame = frame.filter(pl.col("lista") == 0)
    for row in frame.iter_rows(named=True):
        draw = int(row["partido"])
        if draw in (NULL, BLANK):
            continue
        label = str(row["nombre_partido"]).strip()
        if row["unico"]:
            b.option(unique, code_of[draw], 0, label, draw)
        if row["departamental"]:
            b.option(scopes[int(row["secc"])], code_of[draw], 0, label, draw)
    for row in frame.iter_rows(named=True):
        secc, circuit, draw = int(row["secc"]), int(row["circuito"]), int(row["partido"])
        unit = b.unit("ar-s.circuito", f"{secc}:{circuit}", secc)
        for column, scope in (("unico", unique), ("departamental", scopes[secc])):
            option = b.options.get((*scope, code_of.get(draw), 0))
            if draw in (NULL, BLANK) or option is not None:
                _vote(b, scope, unit, draw, option, int(row[column]))
    _elected(
        b,
        unique,
        winners.filter(pl.col("cargo").str.starts_with("Convencional Reformador Distrito")),
        False,
        code_of,
    )
    for secc, scope in scopes.items():
        here = winners.filter(pl.col("cargo").str.ends_with("Departamental") & (pl.col("sec") == secc))
        _elected(b, scope, here, False, code_of)
    _declare(b, unique, URL_UNIQUE, code_of)
    for secc, scope in scopes.items():
        _declare(b, scope, url_department(secc), code_of)
    return p


def _declare(b: Builder, scope: tuple[object, ...], url: str, code_of: dict[int, int]) -> None:
    parties, figures = declared_totals(url)
    result_set = b.sets[scope]
    for draw, votes in parties.items():
        option = b.options.get((*scope, code_of[draw], 0))
        if option is None:
            raise ValueError(f"{url} prints a list the workbook lacks: {draw}")
        b.p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=votes)
    for measure, name in ((5, "invalid"), (6, "blank")):
        if name in figures:
            b.p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=figures[name])


# ---------------------------------------------------------------------------------------------------------------------
# 13 April 2025: municipal and communal primaries

CATEGORY_COLUMNS = {"Intendente": "intendente", "Concejal": "concejal", "Miembro Comunal": "comunal"}


def primary() -> Package:
    p = Package(
        f"{SOURCE}/2025-04-13-paso",
        SOURCE,
        "The 13 April 2025 municipal and communal open primaries in Santa Fe by circuit from the Tribunal "
        "Electoral's results workbook",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ARGENTINA)
    _citations(p, [URL_PRIMARY, URL_CANDIDATES, URL_GENERAL])
    b = Builder(p, "2025-04-13-paso", PASO_DAY)
    election = b.election(
        "primary",
        "2025 Santa Fe municipal and communal primaries",
        "Elecciones primarias municipales y comunales de Santa Fe 2025",
        "ar/santa-fe/2025-04-13-primary",
    )
    code_of = _sorteo_to_party()
    frame = _primary()
    known = localities()
    contests: set[tuple[str, int, int]] = set()
    for row in frame.iter_rows(named=True):
        place = (int(row["secc"]), int(row["circuito"]))
        if int(row["partido"]) in (NULL, BLANK):
            continue
        if place not in known:
            raise ValueError(f"primary circuit {place} is not a locality in the general results")
        contests |= {(category, *place) for category, column in CATEGORY_COLUMNS.items() if row[column]}
    for category, secc, circuit in sorted(contests):
        _municipal_contest(b, election, category, secc, circuit, PASO_METHOD, None)
    for row in frame.iter_rows(named=True):
        draw = int(row["partido"])
        if draw in (NULL, BLANK):
            continue
        for category, column in CATEGORY_COLUMNS.items():
            scope = (category, int(row["secc"]), int(row["circuito"]))
            if row[column] and scope in b.contests:
                b.option(
                    scope,
                    code_of[draw],
                    int(row["lista"]),
                    str(row["nombre_lista"]).strip(),
                    draw * 100 + int(row["lista"]),
                )
    for row in frame.iter_rows(named=True):
        secc, circuit, draw = int(row["secc"]), int(row["circuito"]), int(row["partido"])
        unit = b.unit("ar-s.circuito", f"{secc}:{circuit}", secc)
        for category, column in CATEGORY_COLUMNS.items():
            scope = (category, secc, circuit)
            if scope not in b.contests:
                continue
            option = b.options.get((*scope, code_of.get(draw), int(row["lista"])))
            if draw in (NULL, BLANK) or option is not None:
                _vote(b, scope, unit, draw, option, int(row[column]))
    return p


# ---------------------------------------------------------------------------------------------------------------------
# 29 June 2025: municipal and communal general elections


def _check_localities() -> None:
    """The reconstructed grouping of circuits must reproduce each locality's printed totals."""
    printed: Counter[tuple[int, int, str, int]] = Counter()
    for row in _general().iter_rows(named=True):
        secc, circuit, draw = int(row["secc"]), int(row["circuito"]), int(row["partido"])
        if draw >= NULL:
            continue
        category = "Miembro Comunal" if row["tipo"] == "C" else "Concejal"
        printed[(secc, circuit, category, draw)] += int(row["concejal/comunal"])
        if row["intendente"]:
            printed[(secc, circuit, "Intendente", draw)] += int(row["intendente"])
    counted: Counter[tuple[int, int, str, int]] = Counter()
    for row in mesas():
        if int(row["renglon"]) >= NULL:
            continue
        secc = int(row["seccion"])
        counted[(secc, home(secc, int(row["circuito"])), row["categoria"], int(row["sorteo Partido"]))] += int(
            row["votos"]
        )
    wrong = {k: (printed[k], counted[k]) for k in set(printed) | set(counted) if printed[k] != counted[k]}
    if wrong:
        raise ValueError(f"circuit grouping disagrees with the printed locality totals in {len(wrong)} places")


def general() -> Package:
    p = Package(
        f"{SOURCE}/2025-06-29-general",
        SOURCE,
        "The 29 June 2025 municipal and communal general elections in Santa Fe by mesa from the Tribunal "
        "Electoral's results file",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ARGENTINA)
    _citations(p, [URL_MESAS, URL_GENERAL, URL_WINNERS])
    _check_localities()
    b = Builder(p, "2025-06-29-general", GENERAL_DAY)
    election = b.election(
        "local",
        "2025 Santa Fe municipal and communal elections",
        "Elecciones generales municipales y comunales de Santa Fe 2025",
        "ar/santa-fe/2025-06-29-local",
    )
    code_of = {
        int(row["partido"]): int(row["part"])
        for row in _general().filter(pl.col("partido") < NULL).select("partido", "part").unique().iter_rows(named=True)
    }
    winners = (
        _winners()
        .filter(pl.col("descripcionSitua") == "Titular")
        .with_columns(pl.col("sec").cast(pl.Int64), pl.col("cirnro").cast(pl.Int64), pl.col("sortpar").cast(pl.Int64))
        .with_columns(pl.col("orden").cast(pl.Int64))
        .rename({"apellido y nombre ": "nombre", "descripcionCargo": "cargo"})
    )
    cargos = {"Intendente": "Intendente/a", "Concejal": "Concejal/a", "Miembro Comunal": "M. Comunal"}
    rows = mesas()
    found = sorted(
        {(row["categoria"], int(row["seccion"]), home(int(row["seccion"]), int(row["circuito"]))) for row in rows}
    )
    for category, secc, circuit in found:
        here = winners.filter(
            (pl.col("cargo") == cargos[category]) & (pl.col("sec") == secc) & (pl.col("cirnro") == circuit)
        )
        seats = here.height or None
        _municipal_contest(
            b, election, category, secc, circuit, GENERAL_METHOD, 1 if category == "Intendente" else seats
        )
    for row in rows:
        if int(row["renglon"]) >= NULL:
            continue
        secc = int(row["seccion"])
        scope = (row["categoria"], secc, home(secc, int(row["circuito"])))
        draw, listing = int(row["sorteo Partido"]), int(row["sorteo Lista"])
        b.option(scope, code_of[draw], listing, row["Nombre Lista"].strip(), int(row["renglon"]))
    for row in rows:
        secc, circuit = int(row["seccion"]), int(row["circuito"])
        scope = (row["categoria"], secc, home(secc, circuit))
        unit = b.unit("ar-s.mesa", f"{secc}:{circuit}:{row['mesa']}{row['tipo']}", secc)
        draw = int(row["sorteo Partido"])
        option = b.options.get((*scope, code_of.get(draw), int(row["sorteo Lista"])))
        _vote(b, scope, unit, int(row["renglon"]), option, int(row["votos"]))
    for category, secc, circuit in found:
        scope = (category, secc, circuit)
        here = winners.filter(
            (pl.col("cargo") == cargos[category]) & (pl.col("sec") == secc) & (pl.col("cirnro") == circuit)
        )
        if here.height:
            _elected(b, scope, here, category == "Intendente", code_of)
    return p
