import gzip
import json
import math
import time

import numpy as np
from PIL import Image, ImageDraw
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from ea.store import DATA, ROOT, connect

SIZE = 512
BLOCK = 8
MAX_ZOOM = 8
MIN_FEATURES = 1_000
R = 20037508.342789244
LAT = 85.05112878

BLOCK_SQL = """
select a.k, ST_AsGeoJSON(ST_Simplify(ST_ClipByBox2D(
    ST_Affine(ST_Transform(g.geom, 3857), %(s)s, 0, 0, -%(s)s, %(r)s * %(s)s - %(ox)s, %(r)s * %(s)s - %(oy)s),
    ST_MakeEnvelope(-4, -4, %(w)s + 4, %(h)s + 4)), 0.35), 1)
from area a join area_geometry g on g.area_id = a.id
where a.scheme_code = %(scheme)s and g.valid @> %(asof)s::date
  and g.geom && ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326)
order by ST_Area(g.geom) desc
"""


def pixel(lon: float, lat: float, world: int) -> tuple[float, float]:
    lat = max(-LAT, min(LAT, lat))
    return (
        (lon + 180) / 360 * world,
        (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * world,
    )


def degrees(x: float, y: float, world: int) -> tuple[float, float]:
    return x / world * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / world))))


def encode(block: np.ndarray) -> bytes | None:
    ids, index = np.unique(block, return_inverse=True)
    if len(ids) == 1 and ids[0] == 0:
        return None
    return gzip.compress(
        np.array([len(ids)], dtype="<u4").tobytes()
        + np.diff(ids.astype(np.int64), prepend=0).astype("<i4").tobytes()
        + index.reshape(block.shape).astype("<u1" if len(ids) <= 256 else "<u2").tobytes(),
        9,
        mtime=0,
    )


def build(prefix: str | None) -> str:
    started = time.monotonic()
    with connect() as conn:
        sets = conn.execute(
            """select bs.scheme_code, bs.vintage, bs.as_of, count(*),
                      ST_XMin(ST_Extent(g.geom)), ST_YMin(ST_Extent(g.geom)),
                      ST_XMax(ST_Extent(g.geom)), ST_YMax(ST_Extent(g.geom))
               from boundary_set bs
               join area a on a.scheme_code = bs.scheme_code
               join area_geometry g on g.area_id = a.id and g.valid @> bs.as_of
               where (starts_with(bs.scheme_code, %s) or bs.scheme_code || '/' || bs.vintage = %s)
               group by bs.scheme_code, bs.vintage, bs.as_of
               having count(*) >= %s
               order by bs.scheme_code, bs.vintage""",
            [prefix or "", prefix, MIN_FEATURES],
        ).fetchall()
    for scheme_code, vintage, as_of, features, west, south, east, north in sets:
        built = time.monotonic()
        tiles: dict[int, bytes] = {}
        for z in range(MAX_ZOOM + 1):
            world = SIZE * 2**z
            x0, y0 = pixel(west, north, world)
            x1, y1 = pixel(east, south, world)
            first_x, first_y = int(x0 // SIZE), int(y0 // SIZE)
            last_x, last_y = min(int(x1 // SIZE), 2**z - 1), min(int(y1 // SIZE), 2**z - 1)
            for block_y in range(first_y, last_y + 1, BLOCK):
                for block_x in range(first_x, last_x + 1, BLOCK):
                    columns = min(BLOCK, last_x - block_x + 1)
                    rows_count = min(BLOCK, last_y - block_y + 1)
                    ox, oy = block_x * SIZE, block_y * SIZE
                    width, height = columns * SIZE, rows_count * SIZE
                    block_west, block_north = degrees(ox - 2, oy - 2, world)
                    block_east, block_south = degrees(ox + width + 2, oy + height + 2, world)
                    with connect() as conn:
                        rows = conn.execute(
                            BLOCK_SQL,
                            {
                                "s": world / (2 * R),
                                "r": R,
                                "ox": ox,
                                "oy": oy,
                                "w": width,
                                "h": height,
                                "scheme": scheme_code,
                                "asof": as_of,
                                "west": block_west,
                                "south": block_south,
                                "east": block_east,
                                "north": block_north,
                            },
                        ).fetchall()
                    if not rows:
                        continue
                    image = Image.new("I", (width, height), 0)
                    draw = ImageDraw.Draw(image)
                    for k, geojson in rows:
                        if geojson is None:
                            continue
                        geometry = json.loads(geojson)
                        if geometry["type"] == "Polygon":
                            polygons = [geometry["coordinates"]]
                        elif geometry["type"] == "MultiPolygon":
                            polygons = geometry["coordinates"]
                        else:
                            continue
                        for polygon in polygons:
                            for depth, ring in enumerate(polygon):
                                points = [tuple(point) for point in ring]
                                value = k if depth == 0 else 0
                                if len(points) >= 3:
                                    draw.polygon(points, fill=value, outline=value)
                                elif points and depth == 0:
                                    draw.point(points[0], fill=value)
                    pixels = np.asarray(image, dtype=np.uint32)
                    for row in range(rows_count):
                        for column in range(columns):
                            body = encode(pixels[row * SIZE : (row + 1) * SIZE, column * SIZE : (column + 1) * SIZE])
                            if body is not None:
                                tiles[zxy_to_tileid(z, block_x + column, block_y + row)] = body
        destination = DATA / "tiles" / scheme_code / f"{vintage}.ids.pmtiles"
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(".tmp")
        with partial.open("wb") as handle:
            writer = Writer(handle)
            for tile_id in sorted(tiles):
                writer.write_tile(tile_id, tiles[tile_id])
            writer.finalize(
                {
                    "tile_type": TileType.UNKNOWN,
                    "tile_compression": Compression.GZIP,
                    "min_zoom": 0,
                    "max_zoom": MAX_ZOOM,
                    "min_lon_e7": int(west * 1e7),
                    "min_lat_e7": int(south * 1e7),
                    "max_lon_e7": int(east * 1e7),
                    "max_lat_e7": int(north * 1e7),
                    "center_zoom": 3,
                    "center_lon_e7": int((west + east) / 2 * 1e7),
                    "center_lat_e7": int((south + north) / 2 * 1e7),
                },
                {"encoding": "area-ids", "tile_size": 256, "pixels": SIZE, "as_of": str(as_of)},
            )
        partial.replace(destination)
        size_mb = destination.stat().st_size / 1_000_000
        print(
            f"{scheme_code} {vintage}: {features} features, {len(tiles)} tiles, {size_mb:.2f} MB, "
            f"{time.monotonic() - built:.0f}s, {destination.relative_to(ROOT)}",
            flush=True,
        )
    return f"{len(sets)} raster archives in {time.monotonic() - started:.0f}s"
