"""The National Land Survey of Finland's municipal division of 2005 to 2012, as shapes for `fi.statfin_kunta` areas.

The Survey published its municipal division once a year, in a different scale and layout each year, and the Statistics
Finland boundaries start only in 2013. Each vintage holds from 1 January of its year to 1 January of the next. The
1:1,000,000 files of 2006 and 2010 to 2012 are used as published; the 1:100,000 files of 2005 and 2007 and the
1:10,000 files of 2008 and 2009 are simplified to a 250 m tolerance so that all the shapes have the same detail.
"""

import functools
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

import pyproj
import shapefile
import shapely
from shapely import ops

from ea import raw
from ea.package import Package

SOURCE = "fi.mml"
BASE = "https://mirrors.nic.funet.fi/index/geodata/mml"
PUBLISHER = "Maanmittauslaitos"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
PARTS = ("shp", "shx", "dbf", "prj")
TOLERANCE = 250.0
SLIVER = 1e-6  # square degrees, about 0.06 km²; the 2008 file holds a stray 300 m² sliver coded 940
KKJ, ETRS = "EPSG:2393", "EPSG:3067"


@dataclass(frozen=True)
class Vintage:
    year: int
    folder: str
    stem: str
    field: str
    crs: str
    simplify: bool

    def url(self, part: str) -> str:
        return f"{BASE}/{self.folder}/{self.stem}.{part}"


VINTAGES = (
    Vintage(2005, "hallintorajat_100k/2005", "Kunnat_2005_100k", "CODE", KKJ, True),
    Vintage(2006, "hallintorajat_milj_tk/2006_100k", "Kunnat_2006_milj_tk", "CODE", KKJ, False),
    Vintage(2007, "hallintorajat_100k/2007", "Kunnat_2007_100k", "CODE", KKJ, True),
    Vintage(2008, "hallintorajat_10k/2008", "Kunnat_2008_10k", "TEKSTI", ETRS, True),
    Vintage(2009, "hallintorajat_10k/2009", "Kunnat_2009_10k", "kunta", ETRS, True),
    Vintage(2010, "hallintorajat_milj_tk/2010", "Kunnat_2010_milj_tk", "Kunta", ETRS, False),
    Vintage(2011, "hallintorajat_milj_tk/2011", "Kunnat_2011_milj_tk", "Kunta", ETRS, False),
    Vintage(2012, "hallintorajat_milj_tk/2012", "Kunnat_2012_milj_tk", "kuntakoodi", ETRS, False),
)


def urls() -> list[str]:
    return [vintage.url(part) for vintage in VINTAGES for part in PARTS]


def fetch() -> None:
    for address in urls():
        raw.fetch(SOURCE, address, suffix="." + address.rsplit(".", 1)[1])


def _code(value: object) -> str:
    return str(int(str(value).strip())).zfill(3)


def _polygons(shape: shapely.Geometry) -> Iterator[shapely.Geometry]:
    for part in shapely.get_parts(shapely.make_valid(shape)):
        if shapely.get_type_id(part) in (3, 6):
            yield from shapely.get_parts(part)


@functools.cache
def layer(year: int) -> dict[str, shapely.Geometry]:
    """The shapes of one vintage by three-digit municipality code, a municipality's parts and enclaves united."""
    (vintage,) = [item for item in VINTAGES if item.year == year]
    files = {part: raw.cached(SOURCE, vintage.url(part)).path.open("rb") for part in PARTS}
    parts: dict[str, list[shapely.Geometry]] = {}
    try:
        reader = shapefile.Reader(shp=files["shp"], shx=files["shx"], dbf=files["dbf"], encoding="latin-1")
        names = [field[0] for field in reader.fields[1:]]
        index = names.index(vintage.field)
        for record in reader.iterShapeRecords():
            row = record.record
            value = row[index] if row is not None else None
            if value in (None, "") or not str(value).strip():
                continue
            if record.shape is None:
                continue
            geometry = shapely.from_geojson(json.dumps(dict(record.shape.__geo_interface__)))
            parts.setdefault(_code(value), []).append(geometry)
    finally:
        for file in files.values():
            file.close()
    move = pyproj.Transformer.from_crs(vintage.crs, "EPSG:4326", always_xy=True).transform
    out: dict[str, shapely.Geometry] = {}
    for code, shapes in parts.items():
        united = shapely.union_all([shapely.make_valid(shape) for shape in shapes])
        if vintage.simplify:
            united = shapely.simplify(united, TOLERANCE)
        flat = [polygon for polygon in _polygons(united) if not polygon.is_empty]
        if flat:
            projected = ops.transform(move, shapely.MultiPolygon(flat))
            if projected.area >= SLIVER:
                out[code] = shapely.set_srid(shapely.normalize(shapely.make_valid(projected)), 4326)
    return out


def spans() -> dict[str, list[tuple[date, date, shapely.Geometry]]]:
    """Each code's shapes over valid time, one span per run of consecutive vintages with the same outline."""
    out: dict[str, list[tuple[date, date, shapely.Geometry]]] = {}
    for vintage in VINTAGES:
        since, until = date(vintage.year, 1, 1), date(vintage.year + 1, 1, 1)
        for code, shape in layer(vintage.year).items():
            runs = out.setdefault(code, [])
            if runs and runs[-1][1] == since and shapely.equals_exact(runs[-1][2], shape, 1e-9):
                runs[-1] = (runs[-1][0], until, runs[-1][2])
            else:
                runs.append((since, until, shape))
    return out


def add_source(p: Package, source_id: str) -> None:
    p.add(
        "source",
        id=source_id,
        kind="official_body",
        publisher=PUBLISHER,
        title="Kuntajako (National Land Survey of Finland municipal division, annual vintages 2005 to 2012)",
        url="https://www.maanmittauslaitos.fi/en/maps-and-spatial-data/datasets-and-interfaces/product-descriptions/administrative-division",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Maanmittauslaitos (National Land Survey of Finland), Kuntajako, CC BY 4.0",
    )
    found = {artifact.storage_key: artifact for artifact in (raw.cached(SOURCE, address) for address in urls())}
    for artifact in found.values():
        p.artifact(artifact, source_id)
