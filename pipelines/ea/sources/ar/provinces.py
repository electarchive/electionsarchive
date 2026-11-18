"""Argentina's 23 provinces and the Autonomous City of Buenos Aires as polities under the national polity.

Each is a `federal_unit` polity keyed by its ISO 3166-2 code, so the id is the one a later pipeline derives from
`polity\\x1fAR-B`, and carries the INDEC province code as an external id. Its territory is the province's electoral
district area (`ar.distrito`, minted by `ar.dine`), which this package also draws, from geoBoundaries' open ADM1 layer.
"""

import functools
import json
import re
import unicodedata
from typing import cast
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ar import dine

SOURCE = "ar.provinces"
GEOREF = "https://apis.datos.gob.ar/georef/api/"
CAMPOS = "id,nombre,iso_id,iso_nombre,categoria,centroide"
PROVINCES = f"{GEOREF}provincias?max=30&campos={CAMPOS}"
ENTRE_RIOS_CODE = "30"
DEPARTMENTS = f"{GEOREF}departamentos?provincia={ENTRE_RIOS_CODE}&max=50&campos=id,nombre,centroide"
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/ARG/"
ADM1 = GEOBOUNDARIES + "ADM1/geoBoundaries-ARG-ADM1_simplified.geojson"
ADM2 = GEOBOUNDARIES + "ADM2/geoBoundaries-ARG-ADM2_simplified.geojson"
SHAPES = "ar.geoboundaries"
ARGENTINA = str(uuid5(package.NAMESPACE, "polity\x1fAR"))
INDEC = "ar.indec.provincia"

# The Dirección Nacional Electoral's district codes (the `ar.distrito` areas) and each district's ISO 3166-2 code.
ISO = {
    "1": "C",
    "2": "B",
    "3": "K",
    "4": "X",
    "5": "W",
    "6": "H",
    "7": "U",
    "8": "E",
    "9": "P",
    "10": "Y",
    "11": "L",
    "12": "F",
    "13": "M",
    "14": "N",
    "15": "Q",
    "16": "R",
    "17": "A",
    "18": "J",
    "19": "D",
    "20": "Z",
    "21": "S",
    "22": "G",
    "23": "T",
    "24": "V",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


GEOREF_SOURCE = key("source", "georef")
SHAPE_SOURCE = key("source", "geoboundaries")


def polity(iso: str) -> str:
    return str(uuid5(package.NAMESPACE, f"polity\x1fAR-{iso}"))


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


def fetch() -> None:
    raw.fetch(SOURCE, PROVINCES, suffix=".json")
    raw.fetch(SOURCE, DEPARTMENTS, suffix=".json")
    for url in (ADM1, ADM2):
        raw.fetch(SHAPES, url, suffix=".geojson")


def packages() -> list[str]:
    return [f"{SOURCE}/geo"]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def _json(source: str, url: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(raw.cached(source, url).path.read_text(encoding="utf-8")))


def _centroid(row: dict[str, object]) -> shapely.Point:
    found = cast(dict[str, float], row["centroide"])
    return shapely.Point(found["lon"], found["lat"])


@functools.cache
def listed() -> dict[str, dict[str, object]]:
    """The 24 provinces as Georef lists them, keyed by ISO 3166-2 letter."""
    rows = cast(list[dict[str, object]], _json(SOURCE, PROVINCES)["provincias"])
    found = {str(row["iso_id"]).removeprefix("AR-"): row for row in rows}
    if len(rows) != 24 or len(found) != 24 or set(found) != set(ISO.values()):
        raise ValueError(f"Georef lists {len(rows)} provinces; expected the 24 ISO 3166-2 codes of Argentina")
    for code, letter in ISO.items():
        district = fold(dine.DISTRICTS[code])
        name = fold(str(found[letter]["nombre"]))
        if not (district == name or district.startswith(name)):
            raise ValueError(
                f"DINE district {code} is {dine.DISTRICTS[code]}, but AR-{letter} is {found[letter]['nombre']}"
            )
    return found


@functools.cache
def _adm1() -> dict[str, shapely.Geometry]:
    data = json.loads(raw.cached(SHAPES, ADM1).path.read_text(encoding="utf-8"))
    return {
        f["properties"]["shapeISO"].removeprefix("AR-"): shapely.geometry.shape(f["geometry"]) for f in data["features"]
    }


def _entre_rios() -> shapely.Geometry:
    """geoBoundaries' ADM1 layer has no Entre Ríos, so draw it as the union of its 17 departments, each picked from the
    ADM2 layer by Georef's name and the department whose outline lies nearest Georef's centroid."""
    rows = cast(list[dict[str, object]], _json(SOURCE, DEPARTMENTS)["departamentos"])
    if len(rows) != 17:
        raise ValueError(f"Georef lists {len(rows)} departments in Entre Ríos; expected 17")
    data = json.loads(raw.cached(SHAPES, ADM2).path.read_text(encoding="utf-8"))
    named: dict[str, list[shapely.Geometry]] = {}
    for feature in data["features"]:
        named.setdefault(fold(feature["properties"]["shapeName"]), []).append(
            shapely.geometry.shape(feature["geometry"])
        )
    chosen: list[shapely.Geometry] = []
    for row in rows:
        point = _centroid(row)
        near = sorted(named.get(fold(str(row["nombre"])), []), key=lambda shape: shape.distance(point))
        if not near or near[0].distance(point) > 0.05 or (len(near) > 1 and near[1].distance(point) < 0.5):
            raise ValueError(f"department {row['nombre']} of Entre Ríos has no unambiguous ADM2 outline")
        chosen.append(near[0])
    return shapely.union_all(chosen)


def _outline(p: Package, id: str, shape: shapely.Geometry, quality: str) -> None:
    polygons = [
        polygon
        for part in shapely.get_parts(shapely.make_valid(shape))
        if shapely.get_type_id(part) in (3, 6)
        for polygon in shapely.get_parts(part)
    ]
    shape = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality=quality,
        source_id=SHAPE_SOURCE,
    )


def geo() -> Package:
    found = listed()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Argentina's 23 provinces and the Autonomous City of Buenos Aires as polities under the national polity, "
        "with INDEC province codes and the shapes of their electoral districts",
        depends=[f"{dine.SOURCE}/geo"],
    )
    p.external.add(ARGENTINA)
    p.add(
        "source",
        id=GEOREF_SOURCE,
        kind="official_body",
        publisher="Argentina.gob.ar, Georef API",
        title="Georef: provinces and departments of Argentina, with INDEC codes and ISO 3166-2 codes",
        url="https://apis.datos.gob.ar/georef/",
    )
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
    for url in (PROVINCES, DEPARTMENTS):
        p.artifact(raw.cached(SOURCE, url), GEOREF_SOURCE)
    for url in (ADM1, ADM2):
        p.artifact(raw.cached(SHAPES, url), SHAPE_SOURCE)
    p.add(
        "boundary_set",
        id=key("boundary_set", "ar.distrito", "gbopen"),
        scheme_code="ar.distrito",
        vintage="gbOpen",
        as_of=raw.cached(SHAPES, ADM1).retrieved_at.date(),
    )
    shapes = _adm1()
    for code, letter in sorted(ISO.items(), key=lambda item: int(item[0])):
        row = found[letter]
        id, area = polity(letter), dine.area("ar.distrito", code)
        full, indec = str(row["iso_nombre"]), str(row["id"])
        short = str(row["nombre"])
        official = short if letter == "C" else f"Provincia de {full}"
        if letter == "V":
            official = "Provincia de Tierra del Fuego, Antártida e Islas del Atlántico Sur"
        p.add("polity", id=id, kind="federal_unit", iso_code=f"AR-{letter}")
        p.add("polity_parent", polity_id=id, parent_id=ARGENTINA)
        p.add("polity_territory", polity_id=id, area_id=area)
        p.name(id, official, "es", "official")
        if letter not in ("C", "V"):
            p.name(id, short, "es", "short", False)
        elif letter == "V":
            p.name(id, "Tierra del Fuego", "es", "short", False)
        p.add("slug", path=f"ar/{slug('Tierra del Fuego' if letter == 'V' else short)}", entity_id=id, is_current=True)
        p.add("external_id", entity_id=id, scheme=INDEC, value=indec)
        p.add(
            "citation",
            id=p.id("citation", id, indec),
            entity_id=id,
            source_id=GEOREF_SOURCE,
            locator={"url": PROVINCES, "id": indec, "iso_id": f"AR-{letter}"},
        )
        if letter in shapes:
            # Tierra del Fuego's Georef centroid sits in the Antarctic sector it claims, far from the islands drawn here.
            if letter != "V" and shapes[letter].distance(_centroid(row)) > 0.5:
                raise ValueError(f"the ADM1 shape for AR-{letter} does not lie near Georef's centroid")
            _outline(p, area, shapes[letter], "official")
        else:
            if letter != "E":
                raise ValueError(f"no ADM1 shape for AR-{letter}")
            _outline(p, area, _entre_rios(), "academic")
    return p
