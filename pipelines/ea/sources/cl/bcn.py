"""Chile's commune, region, electoral district, and senatorial constituency boundaries from the BCN's vector maps.

The Biblioteca del Congreso Nacional publishes the commune and region outlines. Servel's list of territories says which
district each commune belongs to, so a district is the union of its communes, and a senatorial constituency is the union
of the region it covers.
"""

import io
import zipfile
from datetime import date
from uuid import uuid5

import numpy
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.cl import servel

SOURCE = "cl.bcn"
BCN = "https://www.bcn.cl/obtienearchivo?id=repositorio/10221/"
COMUNAS = BCN + "10396/5/comunas_final.zip"
REGIONES = BCN + "10398/2/Regiones.zip"
PAGE = "https://www.bcn.cl/siit/mapas_vectoriales"
BCN_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fmapas-vectoriales"))
TOLERANCE = 60.0  # meters in Web Mercator, which stretches distances by about 1.2 at Chile's latitudes
FROM = date(2017, 1, 1)  # the districts and constituencies of the 2017 reform


def packages() -> list[str]:
    return [f"{SOURCE}/geo"]


def fetch() -> None:
    raw.fetch(SOURCE, COMUNAS, suffix=".zip")
    raw.fetch(SOURCE, REGIONES, suffix=".zip")
    raw.fetch("cl.servel", servel.url(servel.TERRITORIES), suffix=".xlsx")


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def _read(url: str) -> tuple[list[dict[str, object]], list[shapely.Geometry]]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, url).path)
    base = next(name[:-4] for name in archive.namelist() if name.endswith(".shp"))
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{base}.shp")),
        shx=io.BytesIO(archive.read(f"{base}.shx")),
        dbf=io.BytesIO(archive.read(f"{base}.dbf")),
        encoding="utf-8",
    )
    records: list[dict[str, object]] = [dict(record.as_dict()) for record in reader.records() if record is not None]
    shapes = [shapely.geometry.shape(shape.__geo_interface__) for shape in reader.shapes() if shape is not None]
    simple = shapely.make_valid(shapely.coverage_simplify(numpy.array(shapes), TOLERANCE))
    return records, list(simple)


def _wgs(shape: shapely.Geometry) -> shapely.Geometry:
    project = pyproj.Transformer.from_crs(3857, 4326, always_xy=True)
    moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Boundaries of Chile's 16 regions, 345 communes, 28 electoral districts, and 16 senatorial constituencies from "
        "the Biblioteca del Congreso Nacional's vector maps",
        depends=[f"{servel.SOURCE}/registry"],
    )
    communes = raw.cached(SOURCE, COMUNAS)
    regions = raw.cached(SOURCE, REGIONES)
    p.add(
        "source",
        id=BCN_SOURCE,
        kind="official_body",
        publisher="Biblioteca del Congreso Nacional de Chile",
        title="Mapas vectoriales: comunas y regiones",
        url=PAGE,
        license="Creative Commons Attribution 3.0 Chile (CC BY 3.0 CL)",
        license_url="https://creativecommons.org/licenses/by/3.0/cl/",
        attribution="Biblioteca del Congreso Nacional de Chile / BCN",
    )
    p.artifact(communes, BCN_SOURCE)
    p.artifact(regions, BCN_SOURCE)
    p.uses(raw.cached(servel.SOURCE, servel.url(servel.TERRITORIES)))
    for code in ("cl.region", "cl.comuna", "cl.distrito", "cl.circunscripcion"):
        p.add(
            "boundary_set",
            id=p.id("boundary_set", code, communes.retrieved_at.year),
            scheme_code=code,
            vintage=str(communes.retrieved_at.year),
            as_of=communes.retrieved_at.date(),
        )

    def place(scheme: str, code: str, shape: shapely.Geometry, since: date | None) -> None:
        shape = _wgs(shape)
        p.add(
            "area_geometry",
            area_id=servel.area(scheme, code),
            valid_from=since,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=BCN_SOURCE,
        )

    territories = servel.territories()
    by_district: dict[int, list[shapely.Geometry]] = {}
    by_region: dict[int, list[shapely.Geometry]] = {}
    rows, outlines = _read(COMUNAS)
    seen: set[str] = set()
    for row, shape in zip(rows, outlines, strict=True):
        name = servel.comuna(str(row["Comuna"]))
        if name is None:
            print(f"left out: {row['Comuna']} is not one of Servel's communes", flush=True)
            continue
        _, region, district = territories[name]
        seen.add(name)
        place("cl.comuna", name, shape, None)
        by_district.setdefault(district, []).append(shape)
        by_region.setdefault(region, []).append(shape)
    for name in sorted(set(territories) - seen):
        print(f"no shape: {name} has no outline in the BCN's commune map", flush=True)
    for district, members in sorted(by_district.items()):
        place("cl.distrito", str(district), shapely.union_all(members), FROM)
    rows, outlines = _read(REGIONES)
    for row, shape in zip(rows, outlines, strict=True):
        code = int(str(row["codregion"]))
        if code not in servel.REGIONS:
            print(f"left out: {row['Region']}", flush=True)
            continue
        place("cl.region", str(code), shape, None)
    for number, region in servel.CS_REGION.items():
        place("cl.circunscripcion", str(number), shapely.union_all(by_region[region]), FROM)
    return p
