"""Kyrgyzstan: the administrative boundaries of the Ministry of Emergency Situations, as distributed in the OCHA COD-AB.

The Ministry digitized the country's administrative boundaries down to the community (ayil aymak) and the town. OCHA
joined them to the State Classification of Administrative and Territorial Units (COATE) of 2018, gave each unit a
P-code, and published the result under CC BY-IGO. One GeoJSON per level sits in a single archive. The archive holds
460 named communities and the shapes of the republican and oblast cities, so a city or ayil council that the Central
Commission publishes results for can take its shape from it. Twenty communities and two towns were never digitized
(the dataset's caveats list them), so a few councils have no shape.

`council_cod.tsv` crosswalks each council code of `kg.shailoo.local` to a P-code, with the name and district of the
shape for review. A council matches by its name in the district its polling station numbers fall in: the Commission
numbers stations by oblast and then by district, which separates communities that share a name.
"""

import csv
import io
import json
import zipfile
from pathlib import Path

import shapely
from shapely.geometry import shape

from ea import raw

SOURCE = "kg.cod"
PAGE = "https://data.humdata.org/dataset/cod-ab-kgz"
URL = (
    "https://data.humdata.org/dataset/693aca77-1252-4404-9045-785e43bb6846/resource/"
    "5f6867f2-376c-47ad-8e9c-ff04465ec06c/download/kgz_admin_boundaries.geojson.zip"
)
LAYERS = (
    ("kgz_admin1.geojson", "adm1_pcode"),
    ("kgz_admin2.geojson", "adm2_pcode"),
    ("kgz_admin3.geojson", "adm3_pcode"),
)
CROSSWALK = Path(__file__).with_name("council_cod.tsv")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".zip")


def crosswalk() -> dict[str, str]:
    """The P-code of the shape of each council code."""
    with CROSSWALK.open(newline="", encoding="utf-8") as handle:
        return {row["code"]: row["pcode"] for row in csv.DictReader(handle, delimiter="\t")}


def shapes() -> dict[str, shapely.Geometry]:
    """Every polygon of the three layers by P-code, repaired and as a multipolygon."""
    archive = zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, URL).path.read_bytes()))
    out: dict[str, shapely.Geometry] = {}
    for name, field in LAYERS:
        for feature in json.loads(archive.read(name))["features"]:
            valid = shapely.make_valid(shape(feature["geometry"]))
            parts = [g for g in getattr(valid, "geoms", [valid]) if g.geom_type in {"Polygon", "MultiPolygon"}]
            if not parts:
                continue
            union = shapely.union_all(parts) if len(parts) > 1 else parts[0]
            if union.geom_type == "Polygon":
                union = shapely.MultiPolygon([union])
            out[feature["properties"][field]] = union
    return out


def wkb(geometry: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(geometry, 4326), hex=True, include_srid=True)


def label(geometry: shapely.Geometry) -> str:
    return wkb(shapely.point_on_surface(geometry))
