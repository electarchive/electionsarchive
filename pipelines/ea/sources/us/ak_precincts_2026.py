"""Alaska's voting precincts under the Redistricting Board's 2023 Final Proclamation Plan, as the Division of Elections
adopted them in April 2024.

The Division publishes the plan's 402 precincts as one shapefile, each with its House and Senate district. This
pipeline builds a geo package of one precinct area per shape, a child of its House district's area, so that the 2026
precinct units can point at precinct areas instead of districts.
"""

import io
import re
import zipfile
from datetime import date

import duckdb
import pyproj
import shapefile
import shapely
import shapely.ops

from ea import raw
from ea.package import Package
from ea.store import DATA

SOURCE = "us.ak_precincts_2026"
GEO = f"{SOURCE}/geo"
SHAPES = "https://www.elections.alaska.gov/doc/maps/AK_Election_Precincts_2023Final.zip"
PAGE = "https://www.elections.alaska.gov/research/district-maps/"
PUBLISHER = "Alaska Division of Elections"
TITLE = "Voting precincts under the 2023 Final Proclamation Plan, shapefile"
ADOPTED = date(2024, 4, 25)  # the shapefile's date; the Division adopted the precincts in April 2024
REFERENCE = DATA / "reference" / "us"
PROJECT = pyproj.Transformer.from_crs(3857, 4326, always_xy=True).transform


def fetch() -> None:
    raw.fetch(SOURCE, SHAPES, suffix=".zip")


def districts() -> tuple[str, dict[int, str], str]:
    """The US-AK polity, the House districts' area ids by number, and the state area."""
    con = duckdb.connect()
    path = {name: str(REFERENCE / f"{name}.parquet") for name in ("polity", "area", "area_code", "reporting_unit")}
    (polity,) = con.execute(f"select id from '{path['polity']}' where iso_code = 'US-AK'").fetchone() or (None,)
    if polity is None:
        raise LookupError("the reference holds no US-AK polity")
    state = con.execute(
        f"select area_id from '{path['reporting_unit']}' where polity_id = ? and scheme_code = 'us.state'", [polity]
    ).fetchone()
    if state is None:
        raise LookupError("the reference holds no Alaska state unit")
    found = {
        int(code.removeprefix("AK:")): area
        for area, code in con.execute(
            f"""select c.area_id, c.code from '{path["area_code"]}' c join '{path["area"]}' a on a.id = c.area_id
                where a.scheme_code = 'us.sldl' and c.code like 'AK:%'"""
        ).fetchall()
    }
    return str(polity), found, str(state[0])


def build() -> list[Package]:
    _, house, _ = districts()
    artifact = raw.cached(SOURCE, SHAPES)
    with zipfile.ZipFile(artifact.path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist() if "." in name}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))

    p = Package(
        GEO,
        SOURCE,
        "Alaska's 402 voting precincts under the 2023 Final Proclamation Plan, as the Division of Elections adopted "
        "them in April 2024",
    )
    p.external |= {"us.precinct", *house.values()}
    src = p.id("source", "precincts")
    p.add(
        "source",
        id=src,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        license="Public record",
        attribution=PUBLISHER,
    )
    p.artifact(artifact, src)
    seen: set[str] = set()
    for item in reader.iterShapeRecords():
        if item.shape is None:
            continue
        label = re.sub(r"\s+", " ", str(item.record["Precinct_N"])).strip()  # ty: ignore[not-subscriptable]
        number = re.match(r"(\d\d)-(\d\d\d)\b", label)
        if number is None:
            raise ValueError(f"precinct {label!r} has no district-precinct number")
        code = f"{number[1]}-{number[2]}"
        if code in seen:
            raise ValueError(f"precinct {code} appears twice")
        seen.add(code)
        district = int(number[1])
        if district not in house:
            raise LookupError(f"precinct {label}: the reference holds no House district {district}")
        outline = shapely.ops.transform(PROJECT, shapely.geometry.shape(item.shape.__geo_interface__))
        outline = shapely.set_srid(
            shapely.MultiPolygon(
                [part for part in shapely.get_parts(shapely.make_valid(outline)) if shapely.get_type_id(part) == 3]
            ),
            4326,
        )
        area = p.id("area", code)
        p.add("area", id=area, scheme_code="us.precinct")
        p.add("area_code", area_id=area, code=f"2023:AK:{code}")
        p.add("area_parent", area_id=area, parent_id=house[district])
        p.name(area, label, "en", "official")
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=ADOPTED,
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=src,
        )
    if len(seen) != 402:
        raise ValueError(f"the shapefile holds {len(seen)} precincts, not 402")
    return [p]


def packages() -> list[str]:
    return [GEO]


def parse() -> None:
    fetch()
    for built in build():
        built.write()
