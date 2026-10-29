import json
import math
from collections.abc import Sequence

import networkx as nx
import numpy as np
import shapely
from psycopg import Connection
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import nearest_points, polygonize, transform, unary_union

NAMES = (
    "Canal Street|Broadway|Park Row|Spruce Street|Gold Street|Peck Slip|Liberty Street|Maiden Lane|Reade Street"
    "|Chatham Square|Catherine Street|Division Street|Grand Street|Centre Street|Walker Street|East Houston Street"
    "|West Houston Street|6th Avenue|Rivington Street|Norfolk Street|Clinton Street|Avenue B|East 14th Street"
    "|West 14th Street|4th Avenue|Bowery|West 26th Street|East 26th Street|West 40th Street|East 40th Street"
    "|West 86th Street|East 86th Street"
)
OVERPASS = (
    "https://overpass-api.de/api/interpreter?data="
    f"%5Bout%3Ajson%5D%5Btimeout%3A120%5D%3Bway%5B%22highway%22%5D%5B%22name%22~%22%5E({NAMES.replace(' ', '%20').replace('|', '%7C')})%24%22%5D"
    "(40.69%2C-74.03%2C40.88%2C-73.90)%3Bout%20geom%3B"
)
SEEDS = {
    1: (-74.0125, 40.7040),
    2: (-74.0055, 40.7105),
    3: (-74.0120, 40.7115),
    4: (-73.9990, 40.7115),
    5: (-74.0090, 40.7185),
    6: (-74.0005, 40.7155),
    7: (-73.9890, 40.7115),
    8: (-74.0030, 40.7235),
    9: (-74.0045, 40.7320),
    10: (-73.9890, 40.7185),
    11: (-73.9760, 40.7250),
    12: (-73.9400, 40.8300),
    13: (-73.9830, 40.7170),
    14: (-73.9965, 40.7225),
    15: (-73.9960, 40.7330),
    16: (-74.0030, 40.7440),
    17: (-73.9855, 40.7280),
    18: (-73.9839, 40.7358),
    19: (-73.9600, 40.7700),
    20: (-73.9970, 40.7500),
    21: (-73.9806, 40.7453),
    22: (-73.9900, 40.7640),
}
LEGS = (
    ("liberty", "N", "E"),
    ("reade", "N", "broadway"),
    ("broadway", "liberty", "ehouston"),
    ("parkrow", "broadway", "canal"),
    ("spruce", "parkrow", "gold"),
    ("gold", "spruce", "peck"),
    ("peck", "gold", "E"),
    ("canal", "N", "broadway"),
    ("canal", "broadway", "centre"),
    ("centre", "canal", "walker"),
    ("walker", "centre", "canal"),
    ("canal", "walker", "bowery"),
    ("whouston", "N", "broadway"),
    ("ehouston", "broadway", "bowery"),
    ("bowery", "ehouston", "canal"),
    ("catherine", "parkrow", "E"),
    ("division", "catherine", "grand"),
    ("grand", "division", "E"),
    ("division", "norfolk", "bowery"),
    ("norfolk", "rivington", "division"),
    ("rivington", "bowery", "norfolk"),
    ("rivington", "norfolk", "clinton"),
    ("rivington", "E", "clinton"),
    ("clinton", "rivington", "14"),
    ("rivington", "clinton", "bowery"),
    ("bowery", "rivington", "fourth"),
    ("fourth", "bowery", "14"),
    ("14", "N", "E"),
    ("26", "N", "E"),
    ("40", "N", "E"),
    ("86", "N", "E"),
    ("sixth_s", "14", "whouston"),
    ("sixth_n", "14", "86"),
)
GRID = {"14", "26", "40", "86"}
STREETS = {
    "canal": ("Canal Street",),
    "broadway": ("Broadway",),
    "parkrow": ("Bowery", "Chatham Square", "Park Row"),
    "bowery": ("Bowery",),
    "spruce": ("Spruce Street",),
    "gold": ("Gold Street",),
    "peck": ("Peck Slip",),
    "liberty": ("Liberty Street", "Maiden Lane"),
    "reade": ("Reade Street",),
    "catherine": ("Catherine Street",),
    "division": ("Division Street",),
    "grand": ("Grand Street",),
    "centre": ("Centre Street",),
    "walker": ("Walker Street",),
    "ehouston": ("East Houston Street",),
    "whouston": ("West Houston Street",),
    "rivington": ("Rivington Street",),
    "norfolk": ("Norfolk Street",),
    "clinton": ("Clinton Street", "Avenue B"),
    "fourth": ("4th Avenue",),
    "sixth_s": ("6th Avenue",),
}
FITS = {
    "14": ("East 14th Street", "West 14th Street"),
    "26": ("East 26th Street", "West 26th Street"),
    "40": ("East 40th Street", "West 40th Street"),
    "86": ("East 86th Street", "West 86th Street"),
}
to_m = Transformer.from_crs(4326, 32618, always_xy=True).transform
to_ll = Transformer.from_crs(32618, 4326, always_xy=True).transform


class Street:
    def __init__(self, lines: Sequence[LineString]) -> None:
        self.geom = unary_union(lines)
        graph = nx.Graph()
        for line in lines:
            count = max(int(line.length // 4), 1)
            points = [line.interpolate(i / count, normalized=True).coords[0] for i in range(count + 1)]
            for start, end in zip(points, points[1:], strict=False):
                graph.add_edge(start, end, weight=math.dist(start, end))
        parts = [list(part) for part in nx.connected_components(graph)]
        while len(parts) > 1:
            gap, start, end, index = min(
                (math.dist(p, q), p, q, i)
                for i in range(1, len(parts))
                for p in parts[i][:: max(len(parts[i]) // 400, 1)]
                for q in parts[0][:: max(len(parts[0]) // 400, 1)]
            )
            graph.add_edge(start, end, weight=gap)
            parts[0].extend(parts.pop(index))
        self.graph = graph
        self.nodes = np.array(list(graph.nodes))

    def snap(self, point: Point) -> tuple[float, float]:
        nearest = self.nodes[np.argmin(((self.nodes - np.array([point.x, point.y])) ** 2).sum(axis=1))]
        return float(nearest[0]), float(nearest[1])

    def path(self, start: Point, end: Point) -> LineString:
        return LineString(nx.shortest_path(self.graph, self.snap(start), self.snap(end), weight="weight"))


def build(osm: bytes, conn: Connection) -> dict[int, str]:
    county = transform(
        to_m,
        shape(
            json.loads(
                conn.execute(
                    """select st_asgeojson(g.geom) from area_geometry g join area a on a.id = g.area_id
                       join area_code c on c.area_id = a.id
                       where a.scheme_code = 'us.county' and c.code = '36061' and g.valid_from = '1691-10-01'"""
                ).fetchone()[0]
            )
        ),
    )
    land = transform(
        to_m,
        shape(
            json.loads(
                conn.execute(
                    """select st_asgeojson(st_union(geom)) from display.land
                       where geom && st_makeenvelope(-74.06, 40.67, -73.89, 40.90, 4326)"""
                ).fetchone()[0]
            )
        ),
    )
    islands = land.intersection(county.buffer(0)).buffer(0.5).buffer(-0.5)
    main = max(islands.geoms, key=lambda part: part.area)
    shore = main.boundary
    zone = main.buffer(250)
    north = to_m(-73.99, 40.745)[1]
    south_of_14 = to_m(-73.99, 40.7360)[1]
    ways: dict[str, list[LineString]] = {}
    near: dict[str, list[LineString]] = {}
    for element in json.loads(osm)["elements"]:
        points = [to_m(node["lon"], node["lat"]) for node in element["geometry"]]
        if len(points) > 1:
            line = LineString(points)
            if zone.intersects(line) and line.length < 3000:
                ways.setdefault(element["tags"]["name"], []).append(line)
                if line.bounds[3] < north:
                    near.setdefault(element["tags"]["name"], []).append(line)
    streets = {key: Street([line for name in names for line in near[name]]) for key, names in STREETS.items()}
    for key, names in FITS.items():
        points = np.array([point for name in names for line in ways[name] for point in line.coords])
        center = points.mean(axis=0)
        direction = np.linalg.svd(points - center)[2][0]
        direction = direction if direction[0] >= 0 else -direction
        streets[key] = Street([LineString([center - direction * 6000, center + direction * 6000])])
    avenue = np.array([point for line in ways["6th Avenue"] for point in line.coords if point[1] > south_of_14])
    center = avenue.mean(axis=0)
    direction = np.linalg.svd(avenue - center)[2][0]
    direction = direction if direction[0] >= 0 else -direction
    streets["sixth_n"] = Street([LineString([center - direction * 6000, center + direction * 6000])])

    def crossing(first: str, second: str) -> Point:
        meet = streets[first].geom.intersection(streets[second].geom)
        points = [part for part in getattr(meet, "geoms", [meet]) if part.geom_type == "Point"]
        if points:
            return points[0]
        one, two = nearest_points(streets[first].geom, streets[second].geom)
        return Point((one.x + two.x) / 2, (one.y + two.y) / 2)

    def river(key: str, side: str) -> Point:
        nodes = streets[key].nodes
        end = nodes[np.argmin(nodes[:, 0])] if side == "N" else nodes[np.argmax(nodes[:, 0])]
        distance = np.sqrt(((nodes - end) ** 2).sum(axis=1))
        heading = end - nodes[(distance > 40) & (distance < 120)].mean(axis=0)
        heading = heading / np.linalg.norm(heading)
        meet = unary_union([streets[key].geom, LineString([end - heading * 100, end + heading * 1500])]).intersection(shore)
        points = [part for part in getattr(meet, "geoms", [meet]) if part.geom_type == "Point"]
        if key in GRID:
            return min(points, key=lambda point: point.x if side == "N" else -point.x)
        return min(points, key=lambda point: point.distance(Point(end)))

    segments = []
    for key, start, end in LEGS:
        first = river(key, start) if start in ("N", "E") else crossing(key, start)
        last = river(key, end) if end in ("N", "E") else crossing(key, end)
        path = streets[key].path(first, last)
        chord = first.distance(last)
        segments.append(
            LineString([first, last]) if path.length > 1.6 * chord + 60 else LineString([first, *path.coords, last])
        )

    joins = []
    for index, segment in enumerate(segments):
        others = unary_union([other for other_index, other in enumerate(segments) if other_index != index] + [shore])
        for end in (segment.coords[0], segment.coords[-1]):
            gap = Point(end).distance(others)
            if 1e-6 < gap < 90:
                joins.append(LineString(nearest_points(Point(end), others)))

    def extend(line: LineString, reach: float) -> LineString:
        coords = list(line.coords)

        def push(point: tuple[float, float], toward: tuple[float, float]) -> tuple[float, float]:
            step = np.array(point) - np.array(toward)
            length = np.linalg.norm(step)
            return (float(point[0] + step[0] / length * reach), float(point[1] + step[1] / length * reach)) if length else point

        return LineString([push(coords[0], coords[1]), *coords, push(coords[-1], coords[-2])])

    extended = [
        extend(
            segment,
            40 if shore.distance(Point(segment.coords[0])) < 1 or shore.distance(Point(segment.coords[-1])) < 1 else 8,
        )
        for segment in segments
    ]
    faces = []
    for face in polygonize(unary_union([*extended, *joins, shore])):
        for part in getattr(face.intersection(main), "geoms", [face.intersection(main)]):
            if part.geom_type == "Polygon" and part.area > 150:
                faces.append(part)
    owner: dict[int, int] = {}
    for ward, (lon, lat) in SEEDS.items():
        (hit,) = [i for i, face in enumerate(faces) if face.contains(Point(*to_m(lon, lat)))]
        owner[hit] = ward
    while len(owner) < len(faces):
        for index in [i for i in range(len(faces)) if i not in owner]:
            shared = [
                (faces[index].boundary.intersection(faces[other].boundary).length, ward) for other, ward in owner.items()
            ]
            length, ward = max(shared)
            if length > 1:
                owner[index] = ward
    parts: dict[int, list] = {ward: [faces[i] for i, held in owner.items() if held == ward] for ward in SEEDS}
    for island in islands.geoms:
        if island.equals(main) or island.area < 500:
            continue
        lon, lat = to_ll(*island.representative_point().coords[0])
        parts[1 if lat < 40.705 and lon < -73.99 else 19 if lon > -73.965 and lat < 40.79 else 12].append(island)
    return {
        ward: json.dumps(mapping(shapely.set_precision(transform(to_ll, unary_union(shapes)), 1e-7)))
        for ward, shapes in parts.items()
    }
