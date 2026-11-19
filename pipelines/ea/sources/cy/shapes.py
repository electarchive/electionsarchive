"""Cyprus: outlines for the districts, municipalities, and communities of the local-election packages.

The Ministry of Interior publishes no boundaries. geoBoundaries' open set (gbOpen) holds six district outlines (ADM1)
and about 610 municipality and community outlines (ADM2) in one 2009-vintage plan. An area of the local-election
packages takes the ADM2 outline with the same English name, when exactly one such outline lies in the area's
district. The outlines are of the plan's own villages, so an area that the 2024 reform merged, or that the ADM2 layer
does not name, gets no outline here. `areas.tsv` lists each area's Ministry code, district code, and English name.
"""

import csv
import functools
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.cy import moi_local

SOURCE = "cy.shapes"
SHAPES = "cy.geoboundaries"
BASE = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/CYP/"
ADM1 = BASE + "ADM1/geoBoundaries-CYP-ADM1_simplified.geojson"
ADM2 = BASE + "ADM2/geoBoundaries-CYP-ADM2_simplified.geojson"
SHAPE_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fgeoboundaries"))
DEPENDS = [f"{moi_local.COUNCIL_PREFIX}/geo", f"{moi_local.PIPELINE}/offices/geo"]
# The Ministry's district codes and geoBoundaries' names for the districts.
DISTRICTS = {1: "Nicosia", 2: "Kyrenia", 3: "Famagusta", 4: "Larnaca", 5: "Limassol", 6: "Paphos"}
# An ADM2 outline must lie within this many degrees of its district's ADM1 outline. The British sovereign base areas
# are in neither layer's districts, and Ormideia, Xylofagou, and Akrotiri lie beside them.
TOLERANCE = 0.1
# Areas whose English name differs from the ADM2 layer's only in transliteration, by Ministry code. Each outline must
# still lie in the area's district.
ALIASES = {
    1021: "Lakatameia",
    1103: "Agia Varvara Lefkosias",
    1106: "Pera Chorio",
    1203: "Lazanias",
    1206: "Agios Epifanios Oreinis",
    1211: "Agios Ioannis Malountas",
    1222: "Analiontas",
    1321: "Agios Georgios Kafkaliou",
    1368: "Meniko",
    1407: "Sinaoros",
    1422: "Moutoulias",
    1424: "Kalapanagiotis",
    1426: "Tsakistra",
    1460: "Pigenia",
    4011: "Livadia Larnakas",
    4102: "Voroklini",
    4105: "Xylotymvou",
    4111: "Perivolia Larnakas",
    4307: "Agios Theodoros Larnakas",
    4314: "Lageia",
    4318: "Agloi Vavatsinias",
    5100: "Palodeia",
    5124: "Agios Tychon",
    5125: "Parekklisia",
    5133: "Prastio Kellakiou",
    5138: "Eptagoneia",
    5202: "Tserkezoi",
    5220: "Prastio Avdimou",
    5300: "Souni Zanakia",
    5304: "Pano Kivides",
    5305: "Agios Amvrosios Lemesou",
    5310: "Agios Georgios Lemesou",
    5313: "Silikou",
    5324: "Malia",
    5345: "Lemithou",
    5360: "Agios Theodoros Lemesou",
    6000: "Paphos",
    6020: "Chloraka",
    6125: "Letymvou",
    6221: "Ayia Marina Kelokedharon",
    6225: "Koilineia",
    6332: "Pano Akourdaleia",
    6335: "Tera",
    6336: "Kritou Tera",
    6352: "Ineia",
    6353: "Drouseia",
    6355: "Androlikou",
    6361: "Kynousa",
    6364: "Gialia",
}
# The 2024 reform's municipalities have codes from 11000 up. Each spans several of the 2009 plan's villages, so no
# single outline of the layer is theirs, even where one carries the same name.
MERGED = 11000
TSV = Path(__file__).with_name("areas.tsv")


def packages() -> list[str]:
    return [f"{SOURCE}/geo"]


def fetch() -> None:
    for url in (ADM1, ADM2):
        raw.fetch(SHAPES, url, suffix=".geojson")


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    geo().write()


def fold(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z ]", " ", ascii_text).split())


def listed() -> list[tuple[int, int, str]]:
    with TSV.open(encoding="utf-8", newline="") as file:
        return [(int(r["code"]), int(r["district"]), r["name"]) for r in csv.DictReader(file, delimiter="\t")]


def _load(url: str) -> list[tuple[str, shapely.Geometry]]:
    data = json.loads(raw.cached(SHAPES, url).path.read_text(encoding="utf-8"))
    return [(f["properties"]["shapeName"], shapely.geometry.shape(f["geometry"])) for f in data["features"]]


@functools.cache
def districts() -> dict[int, shapely.Geometry]:
    by_name = dict(_load(ADM1))
    return {code: by_name[name] for code, name in DISTRICTS.items()}


@functools.cache
def matches() -> dict[int, shapely.Geometry]:
    """Each listed area's outline: the one ADM2 outline of its name that lies in its district."""
    named: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for name, shape in _load(ADM2):
        named[fold(name)].append(shape)
    found: dict[int, shapely.Geometry] = {}
    for code, district, name in listed():
        if code >= MERGED:
            continue
        near = districts()[district].buffer(TOLERANCE)
        shapes = [s for s in named.get(fold(ALIASES.get(code, name)), []) if near.contains(shapely.point_on_surface(s))]
        if len(shapes) == 1:
            found[code] = shapes[0]
    return found


def _wkb(shape: shapely.Geometry) -> str:
    polygons = [
        polygon
        for part in shapely.get_parts(shapely.make_valid(shape))
        if shapely.get_type_id(part) in (3, 6)
        for polygon in shapely.get_parts(part)
    ]
    return shapely.to_wkb(shapely.set_srid(shapely.MultiPolygon(polygons), 4326), hex=True, include_srid=True)


def _label(shape: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Outlines of Cyprus's districts, municipalities, and communities in the local-election packages, from "
        "geoBoundaries' open ADM1 and ADM2 layers",
        depends=DEPENDS,
    )
    p.external |= {moi_local.CYPRUS, moi_local.COUNTRY}
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="Cyprus administrative boundaries, ADM1 and ADM2 (geoBoundaries gbOpen, simplified)",
        url="https://www.geoboundaries.org/countryDownloads.html",
        license="CC BY-SA 3.0",
        license_url="https://creativecommons.org/licenses/by-sa/3.0/",
        attribution="geoBoundaries gbOpen, CC BY-SA 3.0",
    )
    for url in (ADM1, ADM2):
        p.artifact(raw.cached(SHAPES, url), SHAPE_SOURCE)
    as_of = raw.cached(SHAPES, ADM2).retrieved_at.date()
    for scheme in ("cy.local_district", "cy.local_area"):
        p.add(
            "boundary_set",
            id=p.id("boundary_set", scheme, "gbopen"),
            scheme_code=scheme,
            vintage="gbOpen 2009",
            as_of=as_of,
        )
    for code, shape in sorted(districts().items()):
        p.add(
            "area_geometry",
            area_id=moi_local.district_area(code),
            geom=_wkb(shape),
            label_point=_label(shape),
            quality="academic",
            source_id=SHAPE_SOURCE,
        )
    for code, shape in sorted(matches().items()):
        p.add(
            "area_geometry",
            area_id=moi_local.area(code),
            geom=_wkb(shape),
            label_point=_label(shape),
            quality="academic",
            source_id=SHAPE_SOURCE,
        )
    return p
