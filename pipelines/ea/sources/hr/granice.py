"""Official boundaries of Croatia's towns and municipalities, counties, and parliamentary electoral districts.

The State Geodetic Administration publishes the 556 towns and municipalities as the statistical units of the 2021
census, in an open INSPIRE download service. Counties are the unions of their towns and municipalities. A territorial
electoral district is made of whole towns and municipalities, except that the City of Zagreb is shared by districts
I, II, and VI. The pipeline draws a district from the municipalities whose registered voters the State Electoral
Commission's 2024 polling-station table files under that district's own sheet, and leaves the three districts that
share Zagreb without a shape until the city's parts are assigned to them.
"""

import functools
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date
from uuid import uuid5

import numpy
import shapely

from ea import package, raw
from ea.package import Package
from ea.sources.hr import izbori

SOURCE = "hr.granice"
GEO = f"{SOURCE}/geo"
UNITS = (
    "https://transformiraj.nipp.hr/ows/services/org.14.f834670c-e1ce-4714-ab8d-9d06317a0fb2_wfs"
    "?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES=AreaStatisticalUnit&COUNT=556"
)
CATALOG = "https://data.gov.hr/hr/dataset/raspodjela-stanovnistva-prema-dobnim-skupinama-i-spolu-na-razni-gradova-opcina-iz-popisa-stanovnistva-2021-inspire"
FROM = date(2021, 1, 1)
TOLERANCE = 0.0002  # degrees, about 20 meters
NS = {
    "gml": "http://www.opengis.net/gml/3.2",
    "su": "http://inspire.ec.europa.eu/schemas/su-vector/4.0",
    "gn": "http://inspire.ec.europa.eu/schemas/gn/4.0",
    "base": "http://inspire.ec.europa.eu/schemas/base/3.3",
}
# Four names are shared by towns or municipalities in two counties. A (latitude, longitude) inside each tells them apart.
TWINS: dict[tuple[str, str], tuple[float, float]] = {
    ("13", "novigrad"): (44.19, 15.52),
    ("18", "novigrad"): (45.32, 13.56),
    ("16", "otok"): (45.15, 18.90),
    ("17", "otok"): (43.70, 16.70),
    ("01", "sveta nedelja"): (45.80, 15.80),
    ("18", "sveta nedelja"): (45.00, 14.10),
    ("13", "privlaka"): (44.25, 15.13),
    ("16", "privlaka"): (45.20, 18.70),
}
# The City of Zagreb's registered voters are split among districts I, II, and VI, so the city belongs to none alone.
SHARED = {"zagreb"}
SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fdgu-statistical-units"))


def fetch() -> None:
    raw.fetch(SOURCE, UNITS, suffix=".gml")
    raw.fetch(izbori.SOURCE, f"{izbori.DATA}/predsjednik/2024/zip/rezultati.zip", suffix=".zip")
    raw.fetch(izbori.SOURCE, f"{izbori.DATA}/parlament/2024/zip/rezultati.zip", suffix=".zip")


def parse() -> None:
    geo().write()


def packages() -> list[str]:
    return [GEO]


def emit() -> None:
    package.run("load", packages())


def _ring(text: str) -> list[tuple[float, float]]:
    """A GML position list in latitude, longitude order as (longitude, latitude) pairs."""
    values = [float(part) for part in text.split()]
    return [(values[i + 1], values[i]) for i in range(0, len(values), 2)]


def _surface(feature: ET.Element) -> shapely.Geometry:
    polygons = []
    for patch in feature.iterfind(".//gml:PolygonPatch", NS):
        shell = patch.find("gml:exterior/gml:LinearRing/gml:posList", NS)
        if shell is None or shell.text is None:
            continue
        holes = [
            _ring(hole.text)
            for hole in patch.iterfind("gml:interior/gml:LinearRing/gml:posList", NS)
            if hole.text is not None
        ]
        polygons.append(shapely.make_valid(shapely.Polygon(_ring(shell.text), holes)))
    return shapely.union_all(polygons)


@functools.cache
def _units() -> list[tuple[str, shapely.Geometry]]:
    """The statistical units as (name, shape)."""
    tree = ET.parse(raw.cached(SOURCE, UNITS).path)
    return [
        (str(feature.findtext(".//gn:text", namespaces=NS)), _surface(feature))
        for feature in tree.iterfind(".//su:AreaStatisticalUnit", NS)
    ]


def _polygons(shape: shapely.Geometry) -> shapely.Geometry:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    polygons = [polygon for part in parts for polygon in shapely.get_parts(part)]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


@functools.cache
def towns() -> dict[tuple[str, str], shapely.Geometry]:
    """Each town or municipality's shape by (county number, folded name), simplified as one coverage."""
    units = _units()
    named = izbori.municipalities()
    counties_of: dict[str, list[str]] = defaultdict(list)
    for county, name in named:
        counties_of[name].append(county)
    simple = shapely.coverage_simplify(numpy.array([shape for _, shape in units]), TOLERANCE)
    out: dict[tuple[str, str], shapely.Geometry] = {}
    for (name, _), shape in zip(units, simple, strict=True):
        folded = izbori.place(name)
        counties = counties_of.get(folded, [])
        if len(counties) > 1:
            where = shapely.point_on_surface(shape)
            counties = [
                min(
                    counties,
                    key=lambda c, f=folded: shapely.distance(where, shapely.Point(TWINS[(c, f)][1], TWINS[(c, f)][0])),
                )
            ]
        if len(counties) != 1 or (counties[0], folded) in out:
            raise ValueError(f"cannot place {name!r}")
        out[(counties[0], folded)] = _polygons(shape)
    missing = set(named) - set(out)
    if missing:
        raise ValueError(f"no shape for {sorted(missing)}")
    return out


@functools.cache
def _registered() -> dict[tuple[str, str], Counter[str]]:
    registered: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for sheet in izbori.sheets("parlament", 2024):
        if not sheet.contest.isdigit() or int(sheet.contest) > 10:
            continue
        found, _ = izbori.roles(sheet.header)
        for row in sheet.rows:
            if not izbori._keep(row, found) or izbori.fold(izbori._cell(row, found, "kind")) == "drzava":
                continue
            county = izbori._cell(row, found, "county_no").zfill(2)
            name = izbori.place(izbori._cell(row, found, "muni"))
            registered[(county, name)][sheet.contest] += izbori.number(izbori._cell(row, found, "registered"))
    return registered


@functools.cache
def shared() -> set[str]:
    """The districts that hold a real part of a shared city: more than a tenth of its registered voters."""
    return {
        contest
        for key, counts in _registered().items()
        if key[1] in SHARED
        for contest, n in counts.items()
        if n > 0.1 * sum(counts.values())
    }


@functools.cache
def districts() -> dict[tuple[str, str], str]:
    """The territorial district (1 to 10) each whole town or municipality's polling stations are filed under."""
    out: dict[tuple[str, str], str] = {}
    for key, counts in _registered().items():
        if key[1] in SHARED:
            continue
        (contest, most), total = counts.most_common(1)[0], sum(counts.values())
        if most < 0.9 * total:
            raise ValueError(f"{key} is split among districts: {dict(counts)}")
        out[key] = contest
    return out


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Boundaries of Croatia's 556 towns and municipalities from the State Geodetic Administration's 2021 statistical "
        "units, the 21 counties as their unions, and the seven electoral districts made of whole municipalities",
        depends=[f"{izbori.SOURCE}/geo"],
    )
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Državna geodetska uprava",
        title="Statistical units of the 2021 census (INSPIRE AreaStatisticalUnit, towns and municipalities)",
        url=CATALOG,
        license="Open license of the Republic of Croatia (Otvorena dozvola)",
        license_url="https://data.gov.hr/otvorena-dozvola",
        attribution="Državna geodetska uprava (DGU), Državni zavod za statistiku (DZS)",
        notes=(
            "Boundaries valid for 2021, in ETRS89 and simplified to about 20 meters as one coverage, so that "
            "neighbors share their edges. Counties are the unions of their towns and municipalities, and electoral "
            "districts are the unions of the municipalities filed under them in the 2024 parliamentary results."
        ),
    )
    p.artifact(raw.cached(SOURCE, UNITS), SOURCE_ID)
    p.uses(raw.cached(izbori.SOURCE, f"{izbori.DATA}/predsjednik/2024/zip/rezultati.zip"))
    p.uses(raw.cached(izbori.SOURCE, f"{izbori.DATA}/parlament/2024/zip/rezultati.zip"))
    shapes = towns()
    for (county, folded), shape in sorted(shapes.items()):
        _geometry(p, izbori.area("hr.opcina", f"{county}:{folded}"), shape)
    for county in sorted({county for county, _ in shapes}):
        members = [shape for (c, _), shape in shapes.items() if c == county]
        _geometry(p, izbori.area("hr.zupanija", county), shapely.union_all(members))
    belongs = districts()
    for number in sorted({*belongs.values()} - shared(), key=int):
        members = [shapes[key] for key, contest in belongs.items() if contest == number]
        _geometry(p, izbori.area("hr.izborna_jedinica", number), shapely.union_all(members))
    return p


def _geometry(p: Package, area_id: str, shape: shapely.Geometry) -> None:
    shape = _polygons(shape)
    p.add(
        "area_geometry",
        area_id=area_id,
        valid_from=FROM,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=SOURCE_ID,
    )
