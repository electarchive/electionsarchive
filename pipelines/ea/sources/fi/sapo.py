"""Outlines of six Finnish municipalities that merged away before 2003, from the SAPO historical map of Finland.

Statistics Finland publishes municipal boundaries only from 2013, so the municipalities of the 1996 and 1999 elections
that merged away before then have no shape in the elections' own packages. The Semantic National Ontology of Historical
Places (SAPO, FinnONTO consortium, CC BY 3.0) draws each municipality as a polygon for each span of years in which its
boundary was unchanged. Its Turtle files are read from the archive of the `sorvi` R package, which holds them in
`data-raw/`.

The `geo` package here only adds those outlines. It attaches each outline to the municipality's area in the three
packages that name it: the municipal elections' (`fi.statfin_kunta`), the parliamentary elections' (`fi.statfin_eduskunta`),
and the European Parliament elections' (`fi.statfin_ep`).
"""

import re
import zipfile
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import shapely

from ea import package, raw
from ea.package import Package
from ea.sources.fi import statfin_eduskunta, statfin_ep, statfin_kunta, vaalit

SOURCE = "fi.sapo"
GEO = f"{SOURCE}/geo"
ARCHIVE = "https://github.com/rOpenGov/sorvi/archive/refs/heads/main.zip"
HOME = "http://www.yso.fi/onto/sapo"
BASE = "sorvi-main/data-raw/sapo-generation-polygons-{layer}.ttl"
LICENSE = "CC BY 3.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/3.0/"


@dataclass(frozen=True)
class Town:
    number: str
    name: str
    layer: str  # the SAPO polygon file, named by the year of its generation
    label: str  # the SAPO place and its years
    since: date  # when the outline begins to hold
    until: date  # when the municipality merged into another, which is when the outline stops holding
    note: str = ""


TOWNS = (
    Town("014", "Anttola", "1909", "Anttola(1875-2000)", date(1875, 1, 1), date(2001, 1, 1)),
    Town("299", "Kuorevesi", "1960", "Kuorevesi(1954-2000)", date(1954, 1, 1), date(2001, 1, 1)),
    Town(
        "492",
        "Mikkelin maalaiskunta",
        "1960",
        "Mikkelin_mlk(1955-1984)",
        date(1955, 1, 1),
        date(2001, 1, 1),
        "SAPO has an outline for 1955 to 1984 only. In 1985 the municipality gave Moisio, Tuukkala, Silvasti, Tupala, "
        "and Karjanharju to Mikkeli and SAPO has no shape for the smaller municipality, so this outline is approximate "
        "from 1985 and includes those villages.",
    ),
    Town("582", "Pattijoki", "1960", "Pattijoki(1922-2002)", date(1922, 1, 1), date(2003, 1, 1)),
    Town("841", "Temmes", "1909", "Temmes(1865-2000)", date(1865, 1, 1), date(2001, 1, 1)),
    Town("917", "Vehkalahti", "1960", "Vehkalahti(1953-2002)", date(1953, 1, 1), date(2003, 1, 1)),
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SAPO = key("source", "sapo")


def fetch() -> None:
    raw.fetch(SOURCE, ARCHIVE, suffix=".zip")


def areas(number: str) -> list[str]:
    """The area of a municipality in each package that names it."""
    return [
        vaalit.area("fi.kunta", number),  # fi.statfin_ep
        statfin_eduskunta.kunta(number),
        statfin_kunta.key("area", "fi.kunta", number),
    ]


def _polygons(layer: str, label: str) -> shapely.Geometry:
    with zipfile.ZipFile(raw.cached(SOURCE, ARCHIVE).path) as archive:
        text = archive.read(BASE.format(layer=layer)).decode("utf-8")
    start = text.index(f"<{HOME}/{label}>")
    entry = re.split(r"\n\s*\n", text[start:], maxsplit=1)[0]
    rings = re.findall(r'"([^"]+)"', entry)
    if not rings:
        raise ValueError(f"SAPO {label} has no polygon")
    polygons = []
    for ring in rings:
        points = [tuple(float(value) for value in pair.split(",")) for pair in ring.split()]
        polygons.append(shapely.Polygon(points))
    shape = shapely.make_valid(shapely.union_all(polygons) if len(polygons) > 1 else polygons[0])
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) == 3]
    return shapely.set_srid(shapely.normalize(shapely.MultiPolygon(parts)), 4326)


def packages() -> list[str]:
    return [GEO]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Outlines of the six municipalities of the 1996 and 1999 elections that merged away before 2003, from SAPO",
        depends=[f"{statfin_ep.SOURCE}/geo", f"{statfin_eduskunta.SOURCE}/geo", f"{statfin_kunta.SOURCE}/geo"],
    )
    p.external.add(vaalit.FINLAND)
    p.add(
        "source",
        id=SAPO,
        kind="academic",
        publisher="FinnONTO Consortium",
        title="SAPO, the Semantic National Ontology of Historical Places: municipality polygons by generation",
        url="http://www.yso.fi/onto/sapo",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="FinnONTO Consortium, SAPO (http://www.yso.fi/onto/sapo), CC BY 3.0",
        notes=(
            "Read from the Turtle files in the data-raw folder of the sorvi R package's repository, which holds the "
            "files as downloaded from SAPO in August 2022. The polygons are drawn for a historical atlas and are "
            "coarser than a modern survey boundary."
        ),
    )
    p.artifact(raw.cached(SOURCE, ARCHIVE), SAPO)
    for town in TOWNS:
        shape = _shape(town)
        for id in areas(town.number):
            p.add(
                "area_geometry",
                area_id=id,
                valid_from=town.since,
                valid_to=town.until,
                geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                ),
                quality="academic",
                source_id=SAPO,
            )
        if town.note:
            for id in areas(town.number):
                p.add(
                    "note",
                    id=key("note", id),
                    entity_id=id,
                    kind="caveat",
                    body_md=town.note,
                    lang="en",
                )
    return p


_cache: dict[str, shapely.Geometry] = {}


def _shape(town: Town) -> shapely.Geometry:
    if town.number not in _cache:
        _cache[town.number] = _polygons(town.layer, town.label)
    return _cache[town.number]
