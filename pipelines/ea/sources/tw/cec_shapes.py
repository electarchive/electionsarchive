"""Outlines for Taiwan's counties and cities from geoBoundaries' open set (Taiwan's own boundary files, which the
National Land Surveying and Mapping Center publishes through TGOS, refuse automated requests)."""

import json
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "tw.cec"
ADM1 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/TWN/ADM1/"
    "geoBoundaries-TWN-ADM1_simplified.geojson"
)
SHAPES_FROM = date(2014, 12, 25)
VINTAGE = "2017"
# geoBoundaries' English names for the 22 counties and cities of 2017, and the Commission's names for them.
COUNTY = {
    "Taipei": "臺北市",
    "New Taipei": "新北市",
    "Taoyuan": "桃園市",
    "Taichung": "臺中市",
    "Tainan": "臺南市",
    "Kaohsiung": "高雄市",
    "Keelung": "基隆市",
    "Hsinchu": "新竹市",
    "Chiayi": "嘉義市",
    "Hsinchu County": "新竹縣",
    "Miaoli County": "苗栗縣",
    "Changhua County": "彰化縣",
    "Nantou County": "南投縣",
    "Yunlin County": "雲林縣",
    "Chiayi County": "嘉義縣",
    "Pingtung County": "屏東縣",
    "Yilan County": "宜蘭縣",
    "Hualien County": "花蓮縣",
    "Taitung County": "臺東縣",
    "Penghu": "澎湖縣",
    "Kinmen": "金門縣",
    "Matsu Islands": "連江縣",
}
SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fgeoboundaries-twn"))


def fetch() -> None:
    raw.fetch(SOURCE, ADM1, suffix=".geojson")


def counties() -> dict[str, shapely.MultiPolygon]:
    """Each county's outline by the Commission's name."""
    features = json.loads(raw.cached(SOURCE, ADM1).path.read_bytes())["features"]
    found: dict[str, shapely.MultiPolygon] = {}
    for feature in features:
        name = COUNTY[str(feature["properties"]["shapeName"])]
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        parts = [
            q
            for part in shapely.get_parts(shape)
            if shapely.get_type_id(part) in (3, 6)
            for q in shapely.get_parts(part)
        ]
        found[name] = shapely.set_srid(shapely.MultiPolygon(parts), 4326)
    if set(found) != set(COUNTY.values()):
        raise ValueError(f"counties without an outline: {sorted(set(COUNTY.values()) - set(found))}")
    return found


def geo(p: Package, area: dict[str, str]) -> None:
    """Add the source, the boundary set, and the county outlines; `area` maps a county's name to its area id."""
    p.add(
        "source",
        id=SOURCE_ID,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap, Wambacher)",
        title="geoBoundaries gbOpen Taiwan ADM1, 2017",
        url="https://www.geoboundaries.org/",
        license="ODbL 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), © OpenStreetMap contributors",
    )
    p.artifact(raw.cached(SOURCE, ADM1), SOURCE_ID)
    p.add(
        "boundary_set",
        id=str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fboundary_set\x1ftw.county\x1f{VINTAGE}")),
        scheme_code="tw.county",
        vintage=VINTAGE,
        valid_from=SHAPES_FROM,
        as_of=date(2017, 1, 1),
    )
    for name, shape in sorted(counties().items()):
        if name not in area:
            continue
        p.add(
            "area_geometry",
            area_id=area[name],
            valid_from=SHAPES_FROM,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=SOURCE_ID,
        )
