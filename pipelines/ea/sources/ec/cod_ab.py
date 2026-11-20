"""Ecuador: outlines of the parishes the National Electoral Council's results files list, from INEC's boundaries.

The Instituto Nacional de Estadística y Censos publishes Ecuador's political-administrative division (DPA) down to the
parish, and the United Nations' common operational dataset for administrative boundaries (COD-AB, CC BY-IGO) carries it
as a shapefile, reviewed in December 2024. Its third level holds 1,042 parishes, each under a six-digit INEC code.

The Council numbers its parishes differently: its codes (5, 10, 15, and so on) follow the alphabetical order of the
names in its own files, and it also lists the urban parishes of the larger cities, which INEC draws as one outline per
cantonal seat. The table beside this module pairs each of the Council's parishes that has an outline with the INEC
code. Parishes were paired by name within the canton, after pairing the cantons by name and province code, and the
pairs were read through. Four pairs were made by hand: El Carmen de Pijilí, which INEC places in Santa Isabel and the
Council in Camilo Ponce Enríquez; and Santa María, El Paraíso la 14, and Sansahuari, which the Council's later files
list and the 2019 file does not. Two cantonal seats (Oña and Yaguachi) hold exactly one of the Council's urban
parishes, so the seat's outline is that parish's. The urban parishes of Quito, Guayaquil, Cuenca, and the other cities
that split their seat, and the overseas units, have no outline here.
"""

import csv
import io
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

import shapefile
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ec import cne

SOURCE = "ec.cod_ab"
GEO = f"{SOURCE}/geo"
URL = (
    "https://data.humdata.org/dataset/ab3c7592-3b0c-41cd-999a-2919a6b243f2/resource/"
    "d00145f6-141c-4bf2-a881-c32341ddec75/download/ecu_adm_2024.zip"
)
PAGE = "https://data.humdata.org/dataset/cod-ab-ecu"
MEMBER = "ecu_adm_adm3_2024"
TABLE = Path(__file__).with_name("cod_ab_parishes.tsv")
SCHEME = "ec.parroquia"
# The layer says the boundaries were established in 2017 and names no day.
SINCE = date(2017, 1, 1)
# The layer holds 1,042 parishes; the table pairs all but the ones the Council lists only as urban parishes.
LAYER_PARISHES = 1042
# Outlines are kept to a grid of about a meter.
GRID = 1e-5


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".zip")


def packages() -> list[str]:
    return [GEO]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def table() -> list[dict[str, str]]:
    with TABLE.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def layer() -> dict[str, tuple[str, shapely.Geometry]]:
    """Each parish's name and outline by its INEC code."""
    with zipfile.ZipFile(raw.cached(SOURCE, URL).path) as archive:
        parts = {ext: io.BytesIO(archive.read(f"{MEMBER}.{ext}")) for ext in ("shp", "shx", "dbf")}
    reader = shapefile.Reader(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], encoding="utf-8")
    found: dict[str, tuple[str, shapely.Geometry]] = {}
    for shape, record in zip(reader.shapes(), reader.records(), strict=True):
        if shape is None or record is None:
            raise ValueError("the layer holds a feature with no shape or no record")
        pcode = str(record["ADM3_PCODE"])
        if pcode in found:
            raise ValueError(f"the layer holds {pcode} twice")
        outline = shapely.geometry.shape(shape.__geo_interface__)
        found[pcode] = (str(record["ADM3_ES"]), shapely.make_valid(outline))
    return found


def multipolygon(shape: shapely.Geometry) -> shapely.MultiPolygon:
    gridded = shapely.set_precision(shapely.make_valid(shape), GRID)
    polygons = [part for part in shapely.get_parts(gridded) if shapely.get_type_id(part) == 3 and not part.is_empty]
    if not polygons:
        raise ValueError("an outline holds no polygon")
    return shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Outlines of Ecuador's parishes from INEC's boundaries (UN COD-AB, reviewed 2024), under the National "
        "Electoral Council's parish codes",
    )
    p.external.add(SCHEME)
    source = p.id("source", "cod-ab-ecu-adm3")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Instituto Nacional de Estadística y Censos del Ecuador (INEC), distributed by UN OCHA",
        title="Ecuador subnational administrative boundaries (COD-AB), level 3 (parishes)",
        url=PAGE,
        license="CC BY-IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="INEC Ecuador; UN OCHA Common Operational Datasets",
        notes=(
            "The boundaries were established in 2017 and reviewed in December 2024. The layer names no day, so the "
            "outlines are dated from 1 January 2017."
        ),
    )
    p.artifact(raw.cached(SOURCE, URL), source)
    p.add(
        "boundary_set",
        id=p.id("boundary_set", SCHEME, "2017"),
        scheme_code=SCHEME,
        vintage="2017",
        valid_from=SINCE,
        valid_to=None,
        as_of=SINCE,
    )
    shapes = layer()
    if len(shapes) != LAYER_PARISHES:
        raise ValueError(f"the layer holds {len(shapes)} parishes, not {LAYER_PARISHES}")
    rows = table()
    codes = [row["code"] for row in rows]
    pcodes = [row["inec"] for row in rows]
    for what, values in (("Council codes", codes), ("INEC codes", pcodes)):
        repeated = [value for value, n in Counter(values).items() if n > 1]
        if repeated:
            raise ValueError(f"the table repeats {what}: {repeated}")
    for row in rows:
        name, shape = shapes[row["inec"]]
        if name != row["name"]:
            raise ValueError(f"{row['inec']} is {name} in the layer, not {row['name']}")
        id = cne.area(SCHEME, row["code"])
        p.external.add(id)
        outline = multipolygon(shape)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=SINCE,
            valid_to=None,
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=source,
        )
    return p
