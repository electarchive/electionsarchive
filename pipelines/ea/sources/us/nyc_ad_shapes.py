import json
import math
import re
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import networkx as nx
import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import nearest_points, polygonize, substring, transform, unary_union

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nyc_ad_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OVERPASS = (
    "https://overpass-api.de/api/interpreter?data="
    "%5Bout%3Ajson%5D%5Btimeout%3A250%5D%5Bmaxsize%3A1000000000%5D%3B"
    "way%5B%22highway%22%5D%5B%22name%22%5D(40.56%2C-74.06%2C40.74%2C-73.83)%3Bout%20geom%3B"
)
CLIPPING = "https://bklyn-genealogy-info.stevemorse.org/Image/1915.Assembly.Bklyn.jpg"
ALMANAC = "https://archive.org/download/brooklyndailyeag00unse_5/brooklyndailyeag00unse_5_djvu.txt"
TITLE = "Brooklyn assembly districts 1906-1916, rebuilt from the Laws of 1906 descriptions"
NOTE = (
    "Approximate outlines of Brooklyn's 23 assembly districts under the apportionment of 1906, rebuilt by tracing the "
    "streets named in the district descriptions that the Brooklyn Daily Eagle Almanac of 1915 reprinted (a clipping "
    "transcribed on Steve Morse's Brooklyn genealogy site) over OpenStreetMap street centerlines (ODbL) and today's "
    "shoreline. Approximations: streets renamed since 1906 are matched to their modern names, a few vanished streets "
    "are skipped, a corner with no modern crossing is joined by a straight chord, waterfront legs follow today's "
    "shoreline and the county line, and gaps between districts go to the neighbor sharing the longest edge. The "
    "descriptions print each district's election district count; the 1914 vote per election district under these "
    "counts runs from 245 to 317 in every district."
)
SETS = {
    "1906": {
        "valid_from": date(1906, 1, 1),
        "valid_to": date(1917, 1, 1),
        "districts": """
1|Fulton, Columbia Heights, Middagh, Fulton, Concord, Fulton, Myrtle av, Pearl, Willoughby, Lawrence, Johnson, Bridge, Tillary, Duffield, Johnson, Navy, Myrtle av, Raymond, St Edwards, Willoughby, Raymond, Lafayette, Navy, Rockwell pl, Fulton, Hudson av, Flatbush av, 4th av, Bergen, Court, Amity, Clinton, Baltic, Warren pl, Warren, Henry, Congress, Hicks, State, Furman, Atlantic av, @
2|Fulton, Columbia Heights, Middagh, Fulton, Concord, Fulton, Myrtle av, Pearl, Willoughby, Lawrence, Johnson, Bridge, Tillary, Duffield, Johnson, Navy, Myrtle av, Raymond, St Edwards, Willoughby, Raymond, Lafayette, Navy, DeKalb av, Washington Park, Myrtle av, Carlton av, Park av, Hall, Flushing av, Washington av, @
3|Atlantic av, Furman, State, Hicks, Congress, Henry, Warren, Warren pl, Baltic, Clinton, Amity, Court, 3d pl, Clinton, Huntington, Henry, Mills, Columbia^S, @
4|Washington av, Flushing av, Hall, Park av, Skillman, Willoughby av, Bedford av, Lafayette av, Marcy av, Kosciusko, Nostrand av, Flushing av, Harrison av, Hooper, B'way, Rodney, S 1st, Marcy av, S 2d, Havemeyer, B'way, S 6th, Berry, B'way, @
5|Lewis av, McDonough, Tompkins av, Fulton, Patchen av, Sumpter, Howard av, Marion, Rockaway av, B'way, Van Buren, Reid av, Lafayette av
6|Van Buren, Reid av, Lafayette av, Marcy av, Kosciusko, Nostrand av, Flushing av, B'way
7|Columbia^S, Mills, Henry, Huntington, Clinton, Nelson, Court, Huntington, Smith, 9th, 2d av, 8th, 4th av, Prospect av, 11th av, Terrace pl, Gravesend av, Fort Hamilton pkway, 37th, 7th av, 48th, 5th av, 37th, 4th av, 39th, @
8|Clinton, 3d pl, Court, Bergen, 4th av, Wyckoff, St Mark's pl, 5th av, Garfield pl, 4th av, 8th, 2d av, 9th, Smith, Huntington, Court, Nelson
9|39th, 4th av, 37th, 5th av, 48th, 7th av, 37th, 10th av, 39th, 12th av, 40th, 13th av, 58th, 12th av, 60th, 13th av, 79th, 14th av, Benson av, Bay 7th, Cropsey av, Bay 8th, @
10|5th av, St Mark's av, St Mark's pl, 4th av, Flatbush av, Fulton, Rockwell pl, DeKalb av, Washington Park, Myrtle av, Carlton av, Park av, Hall, Willoughby av, Waverly av, Atlantic av, St James pl, Lefferts pl, Grand av, Pacific, Classon av, St Mark's av, Underhill av, Prospect pl, Grand av, Washington av, Sterling pl, Underhill av, Degraw, Washington av, Flatbush av, Prospect Park plaza, Prospect Park W, President, 7th av, Lincoln pl, 6th av, Prospect pl
11|Park av, Skillman, Willoughby av, Bedford av, Carroll, Franklin av, Montgomery, Washington av, Degraw, Underhill av, Sterling pl, Washington av, Grand av, Prospect pl, Underhill av, St Mark's av, Classon av, Pacific, Grand av, Lefferts pl, St James pl, Atlantic av, Waverly av, Willoughby av, Hall
12|6th av, Lincoln pl, 7th av, President, Prospect Park W, Prospect Park plaza, Flatbush av, Ocean av, Parkside av, Prospect Park Southwest, 11th av, Prospect av, 4th av, Garfield pl, 5th av, Prospect pl
13|Meeker av, Kingsland av, Richardson, Manhattan av, Bayard, Humboldt, Driggs av, Union av, Ten Eyck, Bushwick av, Montrose av, Bushwick pl, Boerum, White, Cook, Bushwick av, Flushing av, Central av, Forrest, Morgan av, Noll, Flushing av, Knickerbocker av, Melrose, Flushing av, @
14|B'way, Berry, S 6th, B'way, Havemeyer, S 2d, Marcy av, S 1st, Rodney, S 2d, Union av, Driggs av, Leonard, Norman av, Manhattan av, Noble, Franklin, India, @
15|India, Franklin, Noble, Manhattan av, Norman av, Leonard, Driggs av, Humboldt, Bayard, Manhattan av, Richardson, Kingsland av, Meeker av, @
16|Bay 8th, Cropsey av, Bay 7th, Benson av, 14th av, 79th, 13th av, 60th, 12th av, 58th, 13th av, 40th, 12th av, 39th, 10th av, 37th, Fort Hamilton pkway, Gravesend av, Terrace pl, 11th av, 15th, Coney Island av, Vanderbilt, Prospect av, Fort Hamilton pkway, Coney Island av, Foster av, E 17th, Av I, Flatbush av, E 34th, Av J, Schenectady av, Flatbush av, Av R, Burnett, @
17|Dean, Bedford av, Lafayette av, Lewis av, McDonough, Tompkins av, Fulton, Schenectady av, Pacific, Utica av, Bergen, Albany av, Atlantic av, New York av
18|Dean, New York av, Atlantic av, Albany av, Bergen, Utica av, Church av, E 49th, Schenectady av, Av J, E 34th, Flatbush av, Av I, E 17th, Foster av, Coney Island av, Fort Hamilton pkway, Prospect av, Vanderbilt, Coney Island av, Parkside av, Ocean av, Flatbush av, Washington av, Montgomery, Franklin av, Carroll, Bedford av
19|Flushing av, Melrose, Knickerbocker av, Flushing av, Noll, Hamburg av, Forrest, Central av, Flushing av, Bushwick av, Cook, White, Boerum, Bushwick av, Moore, Flushing av, Beaver, Park, B'way, DeKalb av, Myrtle av, Cedar, Central av, DeKalb av, Hamburg av, Himrod, Knickerbocker av, Harmon, Irving av, Himrod, Wyckoff av, Harmon, St Nicholas av, Stanhope, @
20|Stanhope, St Nicholas av, Harmon, Wyckoff av, Himrod, Irving av, Harmon, Knickerbocker av, Himrod, Hamburg av, DeKalb av, Central av, Cedar, Myrtle av, DeKalb av, B'way, Moffat, Evergreen av, Hancock, Central av, Linden, Wyckoff av, Ralph, St Nicholas av, Bleecker, @
21|Ten Eyck, Union av, S 2d, Rodney, B'way, Hooper, Harrison av, Flushing av, B'way, Park, Beaver, Flushing av, Moore, Bushwick av, Boerum, Bushwick pl, Montrose av, Bushwick av
22|Bleecker, St Nicholas av, Ralph, Wyckoff av, Linden, Central av, Hancock, Evergreen av, Moffat, Bushwick av, Stewart, B'way, Fulton, Norman pl, Fulton, Vesta av, Herkimer, Sherlock pl, Atlantic av, Hinsdale, Liberty av, Williams av, @
23|Moffat, B'way, Rockaway av, Marion, Howard av, Sumpter, Patchen av, Fulton, Schenectady av, Pacific, Utica av, Church av, E 49th, Schenectady av, Flatbush av, Av R, Burnett, @, Williams av, Liberty av, Hinsdale, Atlantic av, Sherlock pl, Herkimer, Vesta av, Fulton, Norman pl, Fulton, B'way, Stewart, Bushwick av
""",
    },
}
ALIASES = {
    "kosciusko": "kosciuszko street",
    "mcdonough": "macdonough street",
    "reid av": "malcolm x boulevard",
    "gravesend av": "mcdonald avenue",
    "hamburg av": "wyckoff avenue",
    "harmon": "harman street",
    "vesta av": "van sinderen avenue",
    "prospect park plaza": "grand army plaza",
    "prospect park w": "prospect park west",
    "raymond": "ashland place",
    "mills": "mill street",
    "bway": "broadway",
    "park": "park avenue",
    "ten eyck": "ten eyck street",
}
JOINED = {"fulton street": ("old fulton street",)}
SUFFIXES = {"av": "avenue", "st": "street", "pl": "place", "pkway": "parkway"}
ANY_SUFFIX = ("street", "avenue", "place", "court", "road", "boulevard", "parkway", "terrace", "walk", "lane", "plaza")
HINTS = {"S": lambda p: p[1], "N": lambda p: -p[1], "W": lambda p: p[0], "E": lambda p: -p[0]}
to_m = Transformer.from_crs(4326, 32618, always_xy=True).transform
to_ll = Transformer.from_crs(32618, 4326, always_xy=True).transform


def squash(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\bsaint\b", "st", name.lower().replace(".", "").replace("'", ""))).strip()


class Street:
    def __init__(self, lines: list[LineString]) -> None:
        self.geom = unary_union(lines)
        graph = nx.Graph()
        for line in lines:
            count = max(int(line.length // 5), 1)
            points = [line.interpolate(i / count, normalized=True).coords[0] for i in range(count + 1)]
            for start, end in zip(points, points[1:], strict=False):
                graph.add_edge(start, end, weight=math.dist(start, end))
        parts = [list(part) for part in nx.connected_components(graph)]
        while len(parts) > 1:
            gap, start, end, index = min(
                (math.dist(p, q), p, q, i)
                for i in range(1, len(parts))
                for p in parts[i][:: max(len(parts[i]) // 200, 1)]
                for q in parts[0][:: max(len(parts[0]) // 200, 1)]
            )
            graph.add_edge(start, end, weight=gap)
            parts[0].extend(parts.pop(index))
        self.graph = graph
        self.nodes = np.array(list(graph.nodes))

    def snap(self, point: tuple[float, float]) -> tuple[float, float]:
        nearest = self.nodes[np.argmin(((self.nodes - np.array(point)) ** 2).sum(axis=1))]
        return float(nearest[0]), float(nearest[1])

    def path(self, start: tuple[float, float], end: tuple[float, float]) -> tuple[list[tuple[float, float]], float]:
        first, last = self.snap(start), self.snap(end)
        return (
            nx.shortest_path(self.graph, first, last, weight="weight"),
            nx.shortest_path_length(self.graph, first, last, weight="weight"),
        )


class Brooklyn:
    def __init__(self, osm: bytes) -> None:
        with connect() as conn:
            county = transform(
                to_m,
                shape(
                    json.loads(
                        conn.execute(
                            """select st_asgeojson(g.geom) from area_geometry g join area a on a.id = g.area_id
                               join area_code c on c.area_id = a.id
                               where a.scheme_code = 'us.county' and c.code = '36047' order by g.valid_from limit 1"""
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
                               where geom && st_makeenvelope(-74.06, 40.55, -73.83, 40.74, 4326)"""
                        ).fetchone()[0]
                    )
                ),
            )
        islands = land.intersection(county.buffer(0)).buffer(0.5).buffer(-0.5)
        self.main = max(getattr(islands, "geoms", [islands]), key=lambda part: part.area)
        self.ring = LineString(self.main.exterior.coords)
        zone = self.main.buffer(300)
        self.ways: dict[str, list[LineString]] = {}
        for element in json.loads(osm)["elements"]:
            points = [to_m(node["lon"], node["lat"]) for node in element["geometry"]]
            if len(points) > 1 and zone.intersects(line := LineString(points)):
                self.ways.setdefault(squash(element["tags"]["name"]), []).append(line)
        self.streets: dict[str, Street | None] = {}
        self.legs: dict[tuple[str, tuple[float, float], tuple[float, float]], tuple[list[tuple[float, float]], float]] = {}

    def street(self, token: str) -> Street | None:
        if token not in self.streets:
            name = squash(token)
            name = re.sub(r"\b(\d+)d\b", lambda m: m.group(1) + ("rd" if m.group(1).endswith("3") else "nd"), name)
            name = ALIASES.get(name, name)
            words = name.split()
            if words[0] in ("e", "s", "n", "w") and len(words) > 1 and words[1][0].isdigit():
                words[0] = {"e": "east", "s": "south", "n": "north", "w": "west"}[words[0]]
            if words[0] == "av" and len(words) == 2:
                words = ["avenue", words[1]]
            if words[-1] in SUFFIXES:
                options = [" ".join([*words[:-1], SUFFIXES[words[-1]]])]
            else:
                base = " ".join(words)
                options = [base, *(f"{base} {suffix}" for suffix in ANY_SUFFIX)]
            found = next((option for option in options if option in self.ways), None)
            self.streets[token] = (
                Street([line for part in (found, *JOINED.get(found, ())) for line in self.ways.get(part, [])])
                if found is not None
                else None
            )
        return self.streets[token]

    def crossings(self, first: Street, second: Street) -> list[tuple[float, float]]:
        meet = first.geom.intersection(second.geom)
        points = [
            point
            for part in getattr(meet, "geoms", [meet])
            for point in (part.geoms if part.geom_type == "MultiPoint" else [part])
            if point.geom_type == "Point"
        ]
        if not points:
            one, two = nearest_points(first.geom, second.geom)
            return [((one.x + two.x) / 2, (one.y + two.y) / 2)]
        unique: list[tuple[float, float]] = []
        for point in points:
            if all(math.dist((point.x, point.y), other) > 25 for other in unique):
                unique.append((point.x, point.y))
        return unique

    def shore(self, street: Street) -> list[tuple[float, float]]:
        ranked = sorted((Point(node).distance(self.ring), (float(node[0]), float(node[1]))) for node in street.nodes[::2])
        found: list[tuple[float, float]] = []
        for distance, point in ranked:
            if distance > 600:
                break
            if all(math.dist(point, other) > 800 for other in found):
                found.append(point)
        return found or [ranked[0][1]]

    def arc(self, start: tuple[float, float], end: tuple[float, float]) -> tuple[list[tuple[float, float]], float]:
        length = self.ring.length
        first, last = self.ring.project(Point(start)), self.ring.project(Point(end))
        forward = (last - first) % length
        backward = length - forward
        low, high, reverse = (first, first + forward, False) if forward <= backward else (last, last + backward, True)
        if high <= length:
            points = list(substring(self.ring, low, high).coords)
        else:
            points = [*substring(self.ring, low, length).coords, *substring(self.ring, 0, high - length).coords]
        return (points[::-1] if reverse else points), min(forward, backward)

    def leg(self, token: str, start: tuple[float, float], end: tuple[float, float]) -> tuple[list[tuple[float, float]], float]:
        key = (token, start, end)
        if key not in self.legs:
            street = self.streets[token]
            try:
                points, length = street.path(start, end)
                chord = math.dist(start, end)
                if length > 3 * chord + 400:
                    points, length = [start, end], chord * 3 + 400
            except nx.NetworkXException:
                points, length = [start, end], math.dist(start, end) * 2 + 500
            self.legs[key] = (points, length)
        return self.legs[key]

    def outline(self, description: str) -> Polygon:
        tokens: list[str] = []
        hints: dict[int, str] = {}
        for token in (part.strip() for part in description.split(",")):
            name, _, hint = token.partition("^")
            if name == "@" or self.street(name) is not None:
                if hint:
                    hints[len(tokens)] = hint
                if not (name == "@" and tokens and tokens[-1] == "@"):
                    tokens.append(name)
        shift = next(i for i in range(len(tokens)) if tokens[i] != "@" and tokens[i - 1] != "@")
        tokens = tokens[shift:] + tokens[:shift]
        hints = {(i - shift) % len(tokens): hint for i, hint in hints.items()}
        count = len(tokens)
        streets = [None if token == "@" else self.street(token) for token in tokens]
        options: list[list[tuple[float, float]]] = []
        for i in range(count):
            one, two = streets[i], streets[(i + 1) % count]
            options.append(
                [(0.0, 0.0)] if one is None and two is None else self.shore(two) if one is None
                else self.shore(one) if two is None else self.crossings(one, two)
            )
        for i, hint in hints.items():
            for j in (i % count, (i - 1) % count):
                if len(options[j]) > 1 and (streets[j] is None or streets[(j + 1) % count] is None):
                    options[j] = [min(options[j], key=HINTS[hint])]

        def cost(i: int, start: tuple[float, float], end: tuple[float, float]) -> float:
            return self.arc(start, end)[1] if streets[i] is None else self.leg(tokens[i], start, end)[1]

        best: tuple[float, list[int]] | None = None
        for origin in range(len(options[-1])):
            table: list[dict[int, tuple[float, int]]] = [{origin: (0.0, -1)}]
            for i in range(count):
                row: dict[int, tuple[float, int]] = {}
                for ending, point in enumerate(options[i]):
                    if i == count - 1 and ending != origin:
                        continue
                    for previous, (spent, _) in table[-1].items():
                        start = options[i - 1][previous] if i else options[-1][origin]
                        total = spent + cost(i, start, point)
                        if ending not in row or total < row[ending][0]:
                            row[ending] = (total, previous)
                table.append(row)
            if origin in table[-1] and (best is None or table[-1][origin][0] < best[0]):
                chosen = [origin]
                for i in range(count, 1, -1):
                    chosen.append(table[i][chosen[-1]][1])
                best = (table[-1][origin][0], chosen[::-1])
        vertices = [options[i][choice] for i, choice in enumerate(best[1])]
        coords: list[tuple[float, float]] = []
        for i in range(count):
            start, end = vertices[i - 1], vertices[i]
            coords.extend(self.arc(start, end)[0] if streets[i] is None else [start, *self.leg(tokens[i], start, end)[0], end])
        outline = Polygon(coords)
        return outline if outline.is_valid else outline.buffer(0)

    def tile(self, outlines: dict[int, Polygon]) -> dict[int, MultiPolygon]:
        clipped = {number: outline.intersection(self.main) for number, outline in outlines.items()}
        faces = [
            part
            for face in polygonize(unary_union([*(outline.boundary for outline in clipped.values()), self.main.boundary]))
            for part in getattr(face.intersection(self.main), "geoms", [face.intersection(self.main)])
            if part.geom_type == "Polygon" and part.area > 20
        ]
        owner: dict[int, int] = {}
        for index, face in enumerate(faces):
            overlap = {number: face.intersection(outline).area for number, outline in clipped.items()}
            number, area = max(overlap.items(), key=lambda item: item[1])
            if area > face.area / 2:
                owner[index] = number
        while len(owner) < len(faces):
            claimed = 0
            for index in [i for i in range(len(faces)) if i not in owner]:
                shared = [
                    (faces[index].boundary.intersection(faces[other].boundary).length, number)
                    for other, number in owner.items()
                ]
                length, number = max(shared)
                if length > 1:
                    owner[index] = number
                    claimed += 1
            if not claimed:
                break
        merged = {
            number: unary_union([faces[i] for i, held in owner.items() if held == number]) for number in outlines
        }
        return {
            number: MultiPolygon(getattr(geom, "geoms", [geom])) for number, geom in merged.items()
        }


def fetch() -> None:
    raw.fetch(SOURCE, OVERPASS, suffix=".json")
    raw.fetch(SOURCE, CLIPPING, suffix=".jpg")
    raw.fetch(SOURCE, ALMANAC, suffix=".txt")


def parse() -> None:
    brooklyn = Brooklyn(raw.cached(SOURCE, OVERPASS).path.read_bytes())
    PARSED.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict[str, str]] = {}
    for vintage, spec in SETS.items():
        outlines = {
            int(number): brooklyn.outline(description)
            for number, description in (line.split("|") for line in spec["districts"].strip().splitlines())
        }
        out[vintage] = {
            str(number): json.dumps(mapping(shapely.set_precision(transform(to_ll, geom), 1e-7)))
            for number, geom in brooklyn.tile(outlines).items()
        }
    (PARSED / "shapes.json").write_text(json.dumps(out))


def emit() -> Path:
    shapes: dict[str, dict[str, str]] = json.loads((PARSED / "shapes.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Brooklyn assembly district outlines under the apportionment of 1906, rebuilt from the Laws of 1906 descriptions over OpenStreetMap streets",
            polity="US",
        )
        outline = cs.source("Brooklyn Daily Eagle Almanac; OpenStreetMap contributors", TITLE)
        cs.add(
            "source",
            id=outline,
            kind="academic",
            publisher="Brooklyn Daily Eagle Almanac; OpenStreetMap contributors",
            title=TITLE,
            url=CLIPPING,
            license="ODbL 1.0 (street lines)",
            notes=NOTE,
        )
        cs.artifact(raw.cached(SOURCE, OVERPASS), outline)
        cs.artifact(raw.cached(SOURCE, CLIPPING), outline)
        county = cs.area("us.county", "36047")
        vintages = dict(conn.execute("select vintage, id from boundary_set where scheme_code = 'us.nyc_ad'").fetchall())
        for vintage, spec in SETS.items():
            first, last = spec["valid_from"], spec["valid_to"]
            cs.add(
                "boundary_set",
                id=vintages.get(vintage, uuid4()),
                scheme_code="us.nyc_ad",
                vintage=vintage,
                valid_from=first,
                valid_to=last,
                as_of=first,
            )
            geometries = {
                number: (geom, point)
                for number, geom, point in conn.execute(
                    """select f.number, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(
                            st_setsrid(st_geomfromgeojson(f.geojson), 4326)), 3))), 'hex'),
                          encode(st_asewkb(st_pointonsurface(st_makevalid(st_setsrid(st_geomfromgeojson(f.geojson), 4326)))), 'hex')
                       from unnest(%s::int[], %s::text[]) as f(number, geojson)""",
                    [[int(number) for number in shapes[vintage]], list(shapes[vintage].values())],
                ).fetchall()
            }
            areas = {}
            for number, (geom, point) in geometries.items():
                code = f"36047:{number:02d}"
                areas[number] = cs.area("us.nyc_ad", code, first)
                cs.add("area", id=areas[number], scheme_code="us.nyc_ad")
                cs.add("area_code", area_id=areas[number], code=code, valid_from=first, valid_to=last)
                cs.add("name", entity_id=areas[number], text=f"Assembly District {number}", lang="en", kind="official", is_preferred=True)
                cs.add(
                    "area_geometry",
                    area_id=areas[number],
                    valid_from=first,
                    valid_to=last,
                    geom=geom,
                    label_point=point,
                    quality="academic",
                    source_id=outline,
                )
                cs.add("area_parent", area_id=areas[number], parent_id=county, valid_from=first, valid_to=last)
            for unit, polity, code, parent, kind, valid_from in conn.execute(
                """select id, polity_id, code, parent_id, kind, valid_from from reporting_unit
                   where scheme_code = 'us.nyc_ad' and code like '36047:__' and valid_from >= %s and valid_from < %s""",
                [first, last],
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
