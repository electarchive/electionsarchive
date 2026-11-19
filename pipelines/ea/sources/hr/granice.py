"""Official boundaries of Croatia's towns and municipalities, counties, and parliamentary electoral districts.

The State Geodetic Administration publishes the 556 towns and municipalities as the statistical units of the 2021
census, in an open INSPIRE download service. Counties are the unions of their towns and municipalities. A territorial
electoral district is made of whole towns and municipalities, except that the City of Zagreb is shared by districts
I, II, and VI. The pipeline draws a district from the municipalities whose registered voters the State Electoral
Commission's 2024 polling-station table files under that district's own sheet, and leaves the three districts that
share Zagreb without a shape until the city's parts are assigned to them.

A second package draws the 21 counties of 1993, which the 1992 territorial laws defined settlement by settlement, from
the settlements of the Geodetic Administration's register of spatial units.
"""

import functools
import html
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from datetime import date
from uuid import uuid5

import numpy
import pyproj
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
    "au": "http://inspire.ec.europa.eu/schemas/au/4.0",
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
    raw.fetch(SOURCE, SETTLEMENTS, suffix=".zip")
    raw.fetch(SOURCE, LAW_1992, suffix=".html")
    raw.fetch(SOURCE, ZAGREB_1992, suffix=".html")
    raw.fetch(izbori.SOURCE, f"{izbori.DATA}/predsjednik/2024/zip/rezultati.zip", suffix=".zip")
    raw.fetch(izbori.SOURCE, f"{izbori.DATA}/parlament/2024/zip/rezultati.zip", suffix=".zip")


def parse() -> None:
    geo().write()
    geo_1993().write()


def packages() -> list[str]:
    return [GEO, GEO_1993]


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


# The counties of 1993. The Law on the Areas of Counties, Towns, and Municipalities (Narodne novine 90/1992) drew 20
# counties and named every settlement in each, and the Law on the City of Zagreb (90/1992) gave the city, which ranked
# as a county, the settlement of Zagreb and some two hundred others around it. A new law (10/1997) redrew the counties
# from 7 February 1997. The 1993 shapes are unions of today's settlements from the State Geodetic Administration's
# register of spatial units, each placed in the county whose list in the law names it.

GEO_1993 = f"{SOURCE}/geo-1993"
SETTLEMENTS = "https://geoportal.dgu.hr/services/atom/INSPIRE_Administrative_Units_(AU).zip"
SETTLEMENTS_CATALOG = "https://data.gov.hr/ckan/dataset/upravne-jedinice-inspire"
LAW_1992 = "https://narodne-novine.nn.hr/clanci/sluzbeni/1992_12_90_2333.html"
ZAGREB_1992 = "https://narodne-novine.nn.hr/clanci/sluzbeni/1992_12_90_2335.html"
FROM_1993 = date(1992, 12, 30)
TO_1993 = date(1997, 2, 7)
SETTLEMENTS_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fdgu-administrative-units"))
LAW_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fnn-90-1992"))
ROMAN_1992 = [
    "I",
    "II",
    "III",
    "IV",
    "V",
    "VI",
    "VII",
    "VIII",
    "IX",
    "X",
    "XI",
    "XII",
    "XIII",
    "XIV",
    "XV",
    "XVI",
    "XVII",
    "XVIII",
    "XIX",
    "XX",
    "XXI",
]
XLINK = "{http://www.w3.org/1999/xlink}"
# A settlement that moved county stays moved only as part of a group of neighbors that holds at least this many
# settlements the law names; a lone match is a namesake.
GROUP = 3
LAEA = pyproj.Transformer.from_crs(3035, 4326, always_xy=True)


def _key(name: str) -> str:
    """A settlement name folded for matching: no second-language name, part marker, or punctuation."""
    name = re.sub(r"-dio\b.*$", "", name.split(" - ")[0])
    folded = izbori.fold(name).replace("-", " ")
    folded = re.sub(r"\bsv\.? ?", "sveti ", folded)
    folded = re.sub(r"\bnedjelj", "nedelj", folded)
    return " ".join(re.sub(r"[^a-z ]", " ", folded).split())


def _law_lines(url: str) -> list[str]:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.DOTALL)
    text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(p|br|div|tr)[^>]*>", "\n", text)))
    return [" ".join(line.split()) for line in text.split("\n") if line.strip()]


@functools.cache
def law_1992() -> dict[str, frozenset[str]]:
    """The folded names of the settlements the 1992 laws place in each county, by county number."""
    names: dict[str, set[str]] = defaultdict(set)
    county = None
    for line in _law_lines(LAW_1992):
        if line.startswith("Članak 7"):
            break
        header = re.match(r"^([IVX]+)[.:] ŽUPANIJA", line)
        if header:
            county = f"{ROMAN_1992.index(header.group(1)) + 1:02d}"
            continue
        if county is None or line.startswith("Grad, Općine"):
            continue
        line = re.sub(r"\(.*?(\)|$)", "", line)
        line = re.sub(r"^dio naselja\s+", "", re.sub(r"^\d+\.\s*", "", line), flags=re.IGNORECASE)
        if _key(line):
            names[county].add(_key(line))
    city = " ".join(_law_lines(ZAGREB_1992))
    listed = city.split("Grad Zagreb i naselja:")[1].split("Članak 4")[0]
    names["21"] = {_key(part) for part in re.split(r"[,.;]", listed) if _key(part)} | {"zagreb"}
    if len(names) != 21:
        raise ValueError(f"the 1992 law yields {len(names)} counties")
    return {county: frozenset(found) for county, found in names.items()}


def _laea_ring(text: str) -> list[tuple[float, float]]:
    values = [float(part) for part in text.split()]
    return list(zip(values[0::2], values[1::2], strict=True))


@functools.cache
def settlements() -> dict[str, tuple[str, str, str, shapely.Geometry]]:
    """Today's settlements by register id: (name, municipality id, county number, shape in ETRS89-LAEA meters)."""
    found: dict[str, tuple[str, str, str | None, shapely.Geometry | None]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, SETTLEMENTS).path) as archive, archive.open("AdministrativeUnit.gml") as f:
        for _, element in ET.iterparse(f):
            if element.tag != f"{{{NS['au']}}}AdministrativeUnit":
                continue
            level = element.find("au:nationalLevel", NS)
            order = level.get(XLINK + "title") if level is not None else None
            if order in ("2ndOrder", "3rdOrder", "4thOrder"):
                upper = element.find("au:upperLevelUnit", NS)
                shape = None
                if order == "4thOrder":
                    polygons = []
                    for polygon in element.iterfind(".//gml:Polygon", NS):
                        shell = polygon.findtext("gml:exterior//gml:posList", namespaces=NS)
                        holes = [h.text for h in polygon.iterfind("gml:interior//gml:posList", NS) if h.text]
                        if shell:
                            polygons.append(shapely.Polygon(_laea_ring(shell), [_laea_ring(h) for h in holes]))
                    shape = shapely.make_valid(shapely.MultiPolygon(polygons))
                found[str(element.get(f"{{{NS['gml']}}}id"))] = (
                    order,
                    str(element.findtext(".//gn:text", namespaces=NS)),
                    upper.get(XLINK + "href", "").lstrip("#") if upper is not None else None,
                    shape,
                )
            element.clear()
    by_name = {_key(name): code for code, name in izbori.counties().items()}
    counties = {id: by_name[_key(name)] for id, (order, name, _, _) in found.items() if order == "2ndOrder"}
    out: dict[str, tuple[str, str, str, shapely.Geometry]] = {}
    for id, (order, name, municipality, shape) in found.items():
        if order != "4thOrder" or municipality is None or shape is None:
            continue
        out[id] = (name, municipality, counties[str(found[municipality][2])], shape)
    if len(out) < 6700:
        raise ValueError(f"only {len(out)} settlements in the register")
    return out


@functools.cache
def _neighbors() -> dict[str, dict[str, float]]:
    """Each settlement's neighbors, weighted by the length of the boundary they share, in meters."""
    places = settlements()
    ids = list(places)
    shapes = [places[id][3] for id in ids]
    tree = shapely.STRtree(shapes)
    edges = [shapely.boundary(shape) for shape in shapes]
    out: dict[str, dict[str, float]] = {}
    for i, id in enumerate(ids):
        near = edges[i].buffer(5)
        out[id] = {}
        for j in tree.query(near, predicate="intersects"):
            if j != i:
                shared = shapely.intersection(near, edges[j]).length
                if shared > 0:
                    out[id][ids[j]] = shared
    return out


@functools.cache
def _zagreb_1992() -> str:
    """The Law on the City of Zagreb's list of settlements, folded, between spaces."""
    city = " ".join(_law_lines(ZAGREB_1992))
    return f" {_key(city.split('Grad Zagreb i naselja:')[1].split('Članak 4')[0])} "


def _revert_small(
    out: dict[str, str], named: dict[str, str], places: dict[str, tuple[str, str, str, shapely.Geometry]]
) -> None:
    """Return to today's county every group of neighboring settlements, moved to the same county, that holds fewer
    than ``GROUP`` settlements the law names."""
    neighbors = _neighbors()
    moved = {id for id, county in out.items() if county != places[id][2]}
    seen: set[str] = set()
    for start in sorted(moved):
        if start in seen:
            continue
        group, stack = [], [start]
        seen.add(start)
        while stack:
            id = stack.pop()
            group.append(id)
            for other in neighbors[id]:
                if other in moved and other not in seen and out[other] == out[start]:
                    seen.add(other)
                    stack.append(other)
        if sum(1 for id in group if id in named) < GROUP:
            for id in group:
                out[id] = places[id][2]


@functools.cache
def placed_1993() -> dict[str, str]:
    """The 1993 county of each settlement of today, by register id.

    A settlement takes the county whose list in the law names it. A name the law gives in several counties takes the
    one most of its municipality's uniquely named settlements fall in. Today's City of Zagreb lay wholly within the
    larger city of 1993, and a settlement of today's Zagreb County that the city's list names (where the published
    list runs two names together without a comma, anywhere in it) belonged to the city. A move to another county
    stands only for a group of neighboring settlements that holds at least ``GROUP`` named ones. A settlement the law
    does not name (renamed or created since, or in a passage the published text lacks) keeps today's county unless
    most of its boundary faces one other county.
    """
    places = settlements()
    lists = law_1992()
    city = _zagreb_1992()
    where: dict[str, set[str]] = defaultdict(set)
    for county, names in lists.items():
        for name in names:
            where[name].add(county)
    named: dict[str, str] = {}
    for id, (name, _, county, _) in places.items():
        key = _key(name)
        found = where.get(key, set())
        if county == "21" or (county == "01" and f" {key} " in city and key not in lists["01"]):
            named[id] = "21"
        elif len(found) == 1:
            named[id] = next(iter(found))
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    for id, county in named.items():
        tally[places[id][1]][county] += 1
    for id, (name, municipality, county, _) in places.items():
        found = where.get(_key(name), set())
        if id in named or not found:
            continue
        ranked = [c for c, _ in tally[municipality].most_common() if c in found]
        if ranked:
            named[id] = ranked[0]
        elif county in found:
            named[id] = county
    out = {id: named.get(id, county) for id, (_, _, county, _) in places.items()}
    _revert_small(out, named, places)
    named = {id: county for id, county in named.items() if out[id] == county}
    neighbors = _neighbors()
    for _ in range(50):
        changed = False
        for id in sorted(places):
            if id in named:
                continue
            votes: dict[str, float] = defaultdict(float)
            for other, length in neighbors[id].items():
                votes[out[other]] += length
            total = sum(votes.values())
            if not total or votes[out[id]] > total / 2:
                continue
            top, length = max(sorted(votes.items()), key=lambda vote: vote[1])
            want = top if length > total / 2 else places[id][2]
            if want != out[id]:
                out[id] = want
                changed = True
        if not changed:
            break
    _revert_small(out, named, places)
    return out


@functools.cache
def counties_1993() -> dict[str, shapely.Geometry]:
    """The 21 counties of 1993 in longitude and latitude, simplified as one coverage."""
    places = settlements()
    placed = placed_1993()
    groups: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for id, county in placed.items():
        groups[county].append(places[id][3])
    codes = sorted(groups)
    merged = [shapely.union_all(groups[code], grid_size=0.01) for code in codes]
    lonlat = [
        shapely.transform(shape, lambda xy: numpy.column_stack(LAEA.transform(xy[:, 0], xy[:, 1]))) for shape in merged
    ]
    simple = shapely.coverage_simplify(numpy.array(lonlat), TOLERANCE)
    return {code: _polygons(shape) for code, shape in zip(codes, simple, strict=True)}


def geo_1993() -> Package:
    p = Package(
        GEO_1993,
        SOURCE,
        "Boundaries of Croatia's 21 counties as the 1992 territorial laws drew them, in force from 30 December 1992 "
        "to 7 February 1997, as unions of the settlements each county's list names",
        depends=[f"{izbori.SOURCE}/geo"],
    )
    p.add(
        "source",
        id=SETTLEMENTS_ID,
        kind="official_body",
        publisher="Državna geodetska uprava",
        title="Administrative units (INSPIRE AdministrativeUnit), settlements, from the Register of Spatial Units",
        url=SETTLEMENTS_CATALOG,
        license="Open license of the Republic of Croatia (Otvorena dozvola)",
        license_url="https://data.gov.hr/otvorena-dozvola",
        attribution="Državna geodetska uprava (DGU)",
        notes="Today's settlement boundaries, in ETRS89-LAEA, from the register's pre-defined download.",
    )
    p.add(
        "source",
        id=LAW_ID,
        kind="official_body",
        publisher="Narodne novine",
        title=(
            "Zakon o područjima županija, gradova i općina u Republici Hrvatskoj and Zakon o Gradu Zagrebu "
            "(Narodne novine 90/1992)"
        ),
        url=LAW_1992,
        license="Official text, not subject to copyright (Zakon o autorskom pravu, article 8)",
        notes=(
            "The 1992 laws list the settlements of every county and of the City of Zagreb. The published text of "
            "the first law lacks the Istrian towns of Poreč, Pula, Rovinj, Umag, and Novigrad and the municipalities "
            "numbered 18 to 26 in Istria County."
        ),
    )
    p.artifact(raw.cached(SOURCE, SETTLEMENTS), SETTLEMENTS_ID)
    p.artifact(raw.cached(SOURCE, LAW_1992), LAW_ID)
    p.artifact(raw.cached(SOURCE, ZAGREB_1992), LAW_ID)
    p.uses(raw.cached(izbori.SOURCE, f"{izbori.DATA}/predsjednik/2024/zip/rezultati.zip"))
    for code, shape in sorted(counties_1993().items()):
        p.add(
            "area_geometry",
            area_id=izbori.area("hr.zupanija", code),
            valid_from=FROM_1993,
            valid_to=TO_1993,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="synthetic",
            source_id=SETTLEMENTS_ID,
        )
    return p
