import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import numpy as np
import shapely
from PIL import Image, ImageDraw
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform, unary_union

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nyc_manhattan_ad_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MAP = "https://digitalcollections.nypl.org/items/07631740-f39c-0130-1193-58d385a7b928"
INBOX = DATA / "inbox" / "manual" / "MF-273" / "default.tif"
FIRST = date(1917, 1, 1)
LAST = date(1927, 1, 1)
SCALE = 3
CONTROL = {
    "Columbus Circle": ((4195, 3105), (-73.98197, 40.76806)),
    "Central Park northwest corner": ((5715, 3875), (-73.9583, 40.8008)),
    "Union Square": ((2637, 2853), (-73.9903, 40.7359)),
    "Battery": ((976, 2080), (-74.0141, 40.7003)),
    "Grand Army Plaza": ((4015, 3418), (-73.9738, 40.7648)),
    "Washington Square": ((2385, 2608), (-73.9973, 40.7308)),
    "125th Street and Lenox Avenue": ((6133, 4308), (-73.9453, 40.8081)),
    "Inwood Hill nose": ((9590, 5100), (-73.924, 40.876)),
}
LABELS = {
    1: (240, 215), 2: (345, 285), 3: (590, 205), 4: (270, 425), 5: (775, 280), 6: (365, 440),
    7: (920, 360), 8: (410, 335), 9: (1065, 425), 10: (610, 385), 11: (1160, 480), 12: (580, 445),
    13: (1255, 540), 14: (790, 550), 15: (910, 470), 16: (885, 600), 17: (1075, 545), 18: (1045, 690),
    19: (1210, 630), 20: (1180, 705), 21: (1355, 610), 22: (1440, 605), 23: (1670, 715),
}
CROP = (100, 450, 1.6)
to_m = Transformer.from_crs(4326, 32618, always_xy=True).transform
to_ll = Transformer.from_crs(32618, 4326, always_xy=True).transform


def fetch() -> None:
    raw.store(
        SOURCE,
        MAP,
        INBOX.read_bytes(),
        suffix=".tif",
        retrieved_at=datetime.fromtimestamp(INBOX.stat().st_mtime, UTC),
        media_type="image/tiff",
    )


def parse() -> None:
    from scipy import ndimage
    from skimage.measure import find_contours
    from skimage.segmentation import watershed

    Image.MAX_IMAGE_PIXELS = None
    scan = Image.open(raw.cached(SOURCE, MAP).path).convert("RGB")
    scan = scan.resize((scan.width // SCALE, scan.height // SCALE))
    pixels = np.asarray(scan).astype(int)
    r, g, b = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    red = (r > 70) & (r > 1.5 * g) & (r > 1.35 * b) & (r - g > 28)
    dark = (r < 110) & (g < 105) & (b < 125) & (np.abs(r - b) < 45)
    lines = ndimage.binary_closing(red | (dark & ndimage.binary_dilation(red, iterations=7)), iterations=5)

    pairs = list(CONTROL.values())
    full = np.array([[x, y, 1] for (x, y), _ in pairs], float)
    meters = np.array([to_m(*ll) for _, ll in pairs])
    affine, *_ = np.linalg.lstsq(full, meters, rcond=None)
    inverse, *_ = np.linalg.lstsq(np.hstack([meters, np.ones((len(meters), 1))]), full[:, :2], rcond=None)

    with connect() as conn:
        land = shape(
            json.loads(
                conn.execute(
                    """select st_asgeojson(st_intersection(st_union(l.geom), c.geom))
                       from display.land l,
                         (select g.geom from area_geometry g join area a on a.id = g.area_id
                          join area_code c on c.area_id = a.id
                          where a.scheme_code = 'us.county' and c.code = '36061' order by g.valid_from limit 1) c
                       where l.geom && c.geom group by c.geom"""
                ).fetchone()[0]
            )
        )
    land_m = transform(to_m, land)
    reach = Image.new("L", scan.size, 0)
    drawer = ImageDraw.Draw(reach)
    for part in getattr(land_m.buffer(120), "geoms", [land_m.buffer(120)]):
        drawer.polygon(
            [tuple(np.array([x, y, 1]) @ inverse / SCALE) for x, y in part.exterior.coords], fill=255
        )
    inside = np.asarray(reach) > 0

    seeds = np.zeros(lines.shape, np.int32)
    rows, cols = np.mgrid[: lines.shape[0], : lines.shape[1]]
    for number, (x, y) in LABELS.items():
        cx, cy = CROP[0] + x * CROP[2], CROP[1] + y * CROP[2]
        seeds[(cols - cx) ** 2 + (rows - cy) ** 2 < 144] = number
    districts = watershed(ndimage.gaussian_filter(lines.astype(float), 1.5), seeds, compactness=0.01)
    districts[~inside] = 0

    out: dict[str, str] = {}
    for number in LABELS:
        mask = ndimage.binary_fill_holes(ndimage.binary_opening(districts == number, iterations=2))
        padded = np.pad(mask, 1).astype(float)
        rings = [
            Polygon([tuple(np.array([(x - 1) * SCALE, (y - 1) * SCALE, 1]) @ affine) for y, x in ring])
            for ring in find_contours(padded, 0.5)
            if len(ring) > 8
        ]
        face = unary_union([ring.buffer(0) for ring in rings if ring.is_valid or ring.buffer(0).area > 0])
        clipped = face.intersection(land_m.buffer(0)).buffer(0)
        out[str(number)] = json.dumps(
            mapping(shapely.set_precision(transform(to_ll, MultiPolygon(getattr(clipped, "geoms", [clipped]))), 1e-7))
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "shapes.json").write_text(json.dumps(out))


def emit() -> Path:
    shapes: dict[str, str] = json.loads((PARSED / "shapes.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Manhattan assembly district outlines under the apportionment of 1917, traced from the 1918 map of the borough's assembly, aldermanic, and municipal court districts",
            polity="US",
        )
        county = cs.area("us.county", "36061")
        vintage = conn.execute(
            "select id from boundary_set where scheme_code = 'us.nyc_ad' and vintage = '1917'"
        ).fetchone()
        outline = cs.source("New York Public Library", "Map of the Borough of Manhattan, showing the Assembly, Aldermanic, and Municipal Court Districts (1918)")
        cs.add(
            "source",
            id=outline,
            kind="academic",
            publisher="New York Public Library",
            title="Map of the Borough of Manhattan, showing the Assembly, Aldermanic, and Municipal Court Districts (1918)",
            url=MAP,
            license="Public domain",
            notes=(
                "Approximate outlines of Manhattan's 23 assembly districts under the apportionment of 1917, traced from the "
                "red district lines of the 1918 NYPL map: the scan is georeferenced to modern coordinates by an affine fit "
                "through eight landmarks (residuals at the landmarks: median 113 m, maximum 240 m), each district is a watershed of the line network seeded at its printed label, and "
                "the result is clipped to the present shoreline of New York County. Every outline is approximate and can sit a block or two from the printed line. Boundaries follow the printed lines "
                "where they are continuous and fall between neighboring labels where the lines break."
            ),
        )
        cs.artifact(raw.cached(SOURCE, MAP), outline)
        cs.add(
            "boundary_set",
            id=vintage[0] if vintage else uuid4(),
            scheme_code="us.nyc_ad",
            vintage="1917",
            valid_from=FIRST,
            valid_to=LAST,
            as_of=FIRST,
        )
        geometries = {
            number: (geom, point)
            for number, geom, point in conn.execute(
                """select f.number, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(st_makevalid(st_setsrid(st_geomfromgeojson(f.geojson), 4326)))), 'hex')
                   from unnest(%s::int[], %s::text[]) as f(number, geojson)""",
                [[int(number) for number in shapes], list(shapes.values())],
            ).fetchall()
        }
        areas = {}
        for number, (geom, point) in geometries.items():
            code = f"36061:{number:02d}"
            areas[number] = cs.area("us.nyc_ad", code, FIRST)
            cs.add("area", id=areas[number], scheme_code="us.nyc_ad")
            cs.add("area_code", area_id=areas[number], code=code, valid_from=FIRST, valid_to=LAST)
            cs.add("name", entity_id=areas[number], text=f"Assembly District {number}", lang="en", kind="official", is_preferred=True)
            cs.add(
                "area_geometry",
                area_id=areas[number],
                valid_from=FIRST,
                valid_to=LAST,
                geom=geom,
                label_point=point,
                quality="academic",
                source_id=outline,
            )
            cs.add("area_parent", area_id=areas[number], parent_id=county, valid_from=FIRST, valid_to=LAST)
        for unit, polity, code, parent, kind, valid_from in conn.execute(
            """select id, polity_id, code, parent_id, kind, valid_from from reporting_unit
               where scheme_code = 'us.nyc_ad' and code like '36061:__' and valid_from >= %s and valid_from < %s""",
            [FIRST, LAST],
        ).fetchall():
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=polity,
                scheme_code="us.nyc_ad",
                code=code,
                parent_id=parent,
                area_relation="equals",
                area_id=areas[int(code[-2:])],
                kind=kind,
                valid_from=valid_from,
                valid_to=valid_from + timedelta(days=1),
            )
        return cs.write()
