import hashlib
import json
import math
import warnings
from collections import Counter
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from pathlib import Path

import httpx
from shapely import affinity, make_valid, unary_union
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel

from ea.sources.us.place_units import STATE_FIPS
from ea.store import DATA, ROOT, connect

COMMIT = "fd0e5c4a77057a61c69d296bd8ac5393beb60f58"
FILES = f"https://example.invalid/{COMMIT}/{{congress}}.rda"
CONGRESSES = range(2005, 2028, 2)
WORK = DATA / "work" / "hexes"
OUT = ROOT / "apps" / "web" / "src" / "lib" / "server" / "hex"
UNIT = 50
CREDIT = "third-party US House layout, since removed"
GRIDS = Path(__file__).parent / "hexgrid"
ELECTIONS = range(1992, 2027, 2)
INSETS = {"PR": "72", "VI": "78", "GU": "66", "AS": "60", "MP": "69"}
POSTAL = {fips: state for state, fips in (STATE_FIPS | INSETS).items()}
PROVINCES = {
    "10": "NL",
    "11": "PE",
    "12": "NS",
    "13": "NB",
    "24": "QC",
    "35": "ON",
    "46": "MB",
    "47": "SK",
    "48": "AB",
    "59": "BC",
    "60": "YT",
    "61": "NT",
    "62": "NU",
}
REGIONS = {
    "IDF": "75 77 78 91 92 93 94 95",
    "HDF": "02 59 60 62 80",
    "GES": "08 10 51 52 54 55 57 67 68 88",
    "NOR": "14 27 50 61 76",
    "BRE": "22 29 35 56",
    "PDL": "44 49 53 72 85",
    "CVL": "18 28 36 37 41 45",
    "BFC": "21 25 39 58 70 71 89 90",
    "ARA": "01 03 07 15 26 38 42 43 63 69 73 74",
    "NAQ": "16 17 19 23 24 33 40 47 64 79 86 87",
    "OCC": "09 11 12 30 31 32 34 46 48 65 66 81 82",
    "PAC": "04 05 06 13 83 84",
    "COR": "2A 2B",
}
DEPARTEMENTS = {code: code for codes in REGIONS.values() for code in codes.split()}
LANDER = {
    "01": "SH",
    "02": "HH",
    "03": "NI",
    "04": "HB",
    "05": "NW",
    "06": "HE",
    "07": "RP",
    "08": "BW",
    "09": "BY",
    "10": "SL",
    "11": "BE",
    "12": "BB",
    "13": "MV",
    "14": "SN",
    "15": "ST",
    "16": "TH",
}


@dataclass(frozen=True)
class Tiles:
    scheme: str
    srid: int
    cut: int
    regions: dict[str, str]
    dates: dict[int, date]
    groups: dict[str, str] | None = None
    parent: str | None = None


TILES = {
    "us-cd": Tiles("us.cd", 5070, 2, POSTAL, {year: date(year, 11, 8) for year in ELECTIONS}),
    "ca-fed": Tiles(
        "ca.fed",
        3347,
        3,
        PROVINCES,
        {
            day.year: day
            for day in [
                date(2004, 6, 28),
                date(2006, 1, 23),
                date(2008, 10, 14),
                date(2011, 5, 2),
                date(2015, 10, 19),
                date(2019, 10, 21),
                date(2021, 9, 20),
                date(2025, 4, 28),
            ]
        },
    ),
    "fr-circ": Tiles(
        "fr.circonscription",
        2154,
        2,
        DEPARTEMENTS,
        {day.year: day for day in [date(2012, 6, 10), date(2017, 6, 11), date(2022, 6, 12), date(2024, 6, 30)]},
        {code: region for region, codes in REGIONS.items() for code in codes.split()},
    ),
    "de-wk": Tiles("de.wahlkreis", 25832, 0, LANDER, {2025: date(2025, 2, 23)}, parent="de.land"),
}
STATES = {
    "us": ("us.state", STATE_FIPS | INSETS),
    "ch": ("ch.canton", {}),
    "br": ("br.uf", {}),
    "tr": ("tr.il", {}),
    "eu": ("eu.member_state", {}),
}


def fetch(congress: int) -> Path:
    target = WORK / COMMIT[:12] / f"hex_{congress}.rda"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        response = httpx.get(FILES.format(congress=congress), follow_redirects=True, timeout=120)
        response.raise_for_status()
        target.write_bytes(response.content)
    return target


def read(path: Path) -> dict[str, BaseGeometry]:
    import rdata

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        frame = next(iter(rdata.conversion.convert(rdata.parser.parse_file(path), default_encoding="utf8").values()))
    shapes = {}
    for cd, geometry in zip(frame["cd"], frame["geometry"]):
        polygons = [geometry] if getattr(geometry[0], "ndim", 0) == 2 else geometry
        parts = [Polygon(polygon[0], polygon[1:]) for polygon in polygons]
        shapes[str(cd)] = make_valid(MultiPolygon(parts) if len(parts) > 1 else parts[0])
    counts = Counter(cd[:2] for cd in shapes)
    return {code(cd, counts[cd[:2]] == 1): shape for cd, shape in shapes.items()}


def code(cd: str, alone: bool) -> str:
    state, number = cd.split("-")
    return f"{STATE_FIPS[state]}{'00' if alone else number.zfill(2)}"


def anchor(geometry: BaseGeometry) -> list[int]:
    main = max(geometry.geoms, key=lambda part: part.area) if isinstance(geometry, MultiPolygon) else geometry
    point = polylabel(main, 0.5)
    return [round(point.x), round(point.y)]


def path(geometry: BaseGeometry) -> str:
    parts = geometry.geoms if isinstance(geometry, MultiPolygon) else [geometry]
    out = []
    for part in parts:
        for ring in [part.exterior, *part.interiors]:
            points = [(round(x), round(y)) for x, y in ring.coords[:-1]]
            kept = [point for index, point in enumerate(points) if point != points[index - 1]]
            if len(kept) < 3:
                continue
            x0, y0 = kept[0]
            steps = [f"{x - px} {y - py}" for (px, py), (x, y) in pairwise(kept)]
            out.append(f"M{x0} {y0}l{' '.join(steps)}z")
    return "".join(out)


def districts(year: int) -> set[str]:
    with connect() as db:
        rows = db.execute(
            """
            select distinct ac.code
            from contest c
            join election e on e.id = c.election_id
            join area a on a.id = c.district_area_id and a.scheme_code = 'us.cd'
            join area_code ac on ac.area_id = a.id and ac.valid @> e.date_from
            where c.kind = 'body_seats' and e.date_from between make_date(%s, 11, 1) and make_date(%s, 11, 9)
              and coalesce(c.magnitude, 1) = 1 and substr(ac.code, 3, 2) < '98'
            """,
            (year, year),
        ).fetchall()
    return {row[0] for row in rows}


def layout(shapes: dict[str, BaseGeometry]) -> dict:
    total = sum(shape.area for shape in shapes.values())
    scale = UNIT / math.sqrt(total / len(shapes))
    west = min(shape.bounds[0] for shape in shapes.values())
    north = max(shape.bounds[3] for shape in shapes.values())
    plane = {
        cd: affinity.affine_transform(shape, [scale, 0, 0, -scale, -west * scale, north * scale]).simplify(0.4)
        for cd, shape in shapes.items()
    }
    states: dict[str, list[BaseGeometry]] = {}
    for cd, shape in plane.items():
        states.setdefault(cd[:2], []).append(shape.buffer(0.05))
    outlines = {fips: unary_union(parts) for fips, parts in sorted(states.items())}
    labels = {}
    for fips, outline in outlines.items():
        main = max(outline.geoms, key=lambda part: part.area) if isinstance(outline, MultiPolygon) else outline
        point = polylabel(main, 0.5)
        labels[fips] = [round(point.x), round(point.y), POSTAL[fips]]
    return {
        "credit": CREDIT,
        "districts": {cd: path(shape) for cd, shape in sorted(plane.items())},
        "bounds": {
            cd: [
                math.floor(shape.bounds[0]),
                math.floor(shape.bounds[1]),
                math.ceil(shape.bounds[2]),
                math.ceil(shape.bounds[3]),
            ]
            for cd, shape in sorted(plane.items())
        },
        "points": {cd: anchor(shape) for cd, shape in sorted(plane.items())},
        "states": {fips: path(outline) for fips, outline in outlines.items()},
        "labels": labels,
    }


def grid(path: Path) -> dict[tuple[int, int], str]:
    lines = path.read_text().rstrip("\n").split("\n")
    offset = 1 if lines[0].startswith("  ") else 0
    cells = {}
    for index, line in enumerate(lines):
        for q, token in enumerate(line.split()):
            if token != "..":
                cells[(q, index + offset)] = token
    return cells


def center(q: int, r: int) -> tuple[float, float]:
    return (math.sqrt(3) * (q + (r & 1) / 2), 1.5 * r)


def ahead(q: int, r: int) -> list[tuple[int, int]]:
    odd = r & 1
    return [(q + odd, r - 1), (q + 1, r), (q + odd, r + 1)]


def match(cost: list[list[float]]) -> list[int]:
    n = len(cost)
    u, v, owner, way = [0.0] * (n + 1), [0.0] * (n + 1), [0] * (n + 1), [0] * (n + 1)
    for row in range(1, n + 1):
        owner[0] = row
        column = 0
        least = [math.inf] * (n + 1)
        used = [False] * (n + 1)
        while owner[column] != 0:
            used[column] = True
            current, delta, step = owner[column], math.inf, 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                value = cost[current - 1][j - 1] - u[current] - v[j]
                if value < least[j]:
                    least[j], way[j] = value, column
                if least[j] < delta:
                    delta, step = least[j], j
            for j in range(n + 1):
                if used[j]:
                    u[owner[j]] += delta
                    v[j] -= delta
                else:
                    least[j] -= delta
            column = step
        while column != 0:
            previous = way[column]
            owner[column] = owner[previous]
            column = previous
    picked = [0] * n
    for j in range(1, n + 1):
        picked[owner[j] - 1] = j - 1
    return picked


def normal(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    mx = sum(x for x, _ in points) / len(points)
    my = sum(y for _, y in points) / len(points)
    spread = math.sqrt(sum((x - mx) ** 2 + (y - my) ** 2 for x, y in points) / len(points)) or 1
    return [((x - mx) / spread, (y - my) / spread) for x, y in points]


def centers(tiles: Tiles, day: date) -> dict[str, tuple[str, float, float]]:
    with connect() as db:
        rows = db.execute(
            """
            select ac.code, pc.code, st_x(p), st_y(p)
            from area a
            join area_code ac on ac.area_id = a.id and ac.valid @> %(day)s
            left join (area_parent ap join area pa on pa.id = ap.parent_id and pa.scheme_code = %(parent)s
              join area_code pc on pc.area_id = pa.id) on ap.area_id = a.id and ap.valid @> %(day)s
            join area_geometry g on g.area_id = a.id and (g.valid @> %(day)s or g.valid is null),
            lateral st_pointonsurface(st_transform(g.geom, %(srid)s)) p
            where a.scheme_code = %(scheme)s
            """,
            {"day": day, "srid": tiles.srid, "scheme": tiles.scheme, "parent": tiles.parent},
        ).fetchall()
    return {code: (parent or code[: -tiles.cut], x, y) for code, parent, x, y in rows}


def outline(tiles: Tiles, cells: dict[tuple[int, int], str]) -> dict:
    prefix = {token: code for code, token in tiles.regions.items()}
    groups = tiles.groups or {}
    edges: dict[str, list[list[int]]] = {"states": [], "inner": []}
    for (q, r), token in sorted(cells.items()):
        for side, other in enumerate(ahead(q, r)):
            if other in cells and cells[other] != token:
                group = groups.get(prefix[token])
                inner = group is not None and group == groups.get(prefix[cells[other]])
                edges["inner" if inner else "states"].append([q, -r, side + 1])
    spread: dict[str, list[tuple[float, float, int, int]]] = {}
    for (q, r), token in cells.items():
        spread.setdefault(token, []).append((*center(q, r), q, r))
    labels = {}
    for token, spots in spread.items():
        mx = sum(spot[0] for spot in spots) / len(spots)
        my = sum(spot[1] for spot in spots) / len(spots)
        _, _, q, r = min(spots, key=lambda spot: (spot[0] - mx) ** 2 + (spot[1] - my) ** 2)
        labels[prefix[token]] = [q, -r, token]
    return {"boundaries": {name: kept for name, kept in edges.items() if kept}, "labels": dict(sorted(labels.items()))}


def place(
    tiles: Tiles, day: date, cells: dict[tuple[int, int], str]
) -> tuple[dict[str, list[int]], dict[str, str], list[str]]:
    prefix = {token: code for code, token in tiles.regions.items()}
    points = centers(tiles, day)
    groups: dict[str, list[tuple[int, int]]] = {}
    for cell, token in cells.items():
        groups.setdefault(token, []).append(cell)
    hexes: dict[str, list[int]] = {}
    problems = []
    for token, spots in sorted(groups.items()):
        codes = sorted(code for code, point in points.items() if point[0] == prefix[token])
        if not codes:
            continue
        if len(codes) != len(spots):
            problems.append(f"{token} {len(codes)} districts for {len(spots)} cells")
            continue
        real = normal([(points[code][1], -points[code][2]) for code in codes])
        plane = normal([center(*spot) for spot in spots])
        cost = [[(x - a) ** 2 + (y - b) ** 2 for a, b in plane] for x, y in real]
        for code, column in zip(codes, match(cost)):
            q, r = spots[column]
            hexes[code] = [q, -r]
    return dict(sorted(hexes.items())), {code: points[code][0] for code in sorted(hexes)}, problems


def build_tiles() -> str:
    report = []
    for name, tiles in TILES.items():
        grids = {path.stem.rsplit("-", 1)[1]: grid(path) for path in sorted(GRIDS.glob(f"{name}-*.txt"))}
        years = {}
        for year, day in tiles.dates.items():
            start = max(first for first in grids if int(first) <= year)
            hexes, regions, problems = place(tiles, day, grids[start])
            years[str(year)] = {"grid": start, "hexes": hexes} | ({"regions": regions} if tiles.parent else {})
            report.append(f"{name} {year} (grid {start}): {len(hexes)} placed; {'; '.join(problems) or 'ok'}")
        built = {
            "layout": "odd-r",
            "grids": {start: outline(tiles, cells) for start, cells in grids.items()},
            "years": years,
        }
        (OUT / f"{name}-tiles.json").write_text(json.dumps(built, separators=(",", ":")) + "\n")
    return "\n".join([*report, build_states()])


def build_states() -> str:
    built = {}
    report = []
    with connect() as db:
        for polity, (scheme, codes) in STATES.items():
            known = {
                row[0]
                for row in db.execute(
                    "select ac.code from area a join area_code ac on ac.area_id = a.id where a.scheme_code = %s",
                    [scheme],
                )
            }
            hexes = {
                codes.get(token, token): [q, -r, token]
                for (q, r), token in grid(GRIDS / f"states-{polity}.txt").items()
            }
            report.append(
                f"states {polity}: {len(hexes)} placed; unknown {sorted(set(hexes) - known)}; off the grid {sorted(known - set(hexes))}"
            )
            built[polity] = {"scheme": scheme, "hexes": dict(sorted(hexes.items()))}
    (OUT / "states.json").write_text(json.dumps(built, separators=(",", ":")) + "\n")
    return "\n".join(report)


def build() -> str:
    seen: dict[str, int] = {}
    report = []
    index = {}
    for congress in CONGRESSES:
        shapes = read(fetch(congress))
        built = layout(shapes)
        digest = hashlib.sha256(json.dumps(built, sort_keys=True).encode()).hexdigest()
        first = seen.setdefault(digest, congress)
        if first == congress:
            (OUT / f"us-cd-{congress}.json").write_text(json.dumps(built, separators=(",", ":")) + "\n")
        index[str(congress - 1)] = first
        ours = districts(congress - 1)
        theirs = set(built["districts"])
        report.append(
            f"{congress - 1} (Congress from {congress}, layout {first}): {len(theirs)} in layout, {len(ours)} ours;"
            f" layout only {sorted(theirs - ours)}; ours only {sorted(ours - theirs)}"
        )
    (OUT / "us-cd.json").write_text(json.dumps(index, separators=(",", ":")) + "\n")
    return "\n".join([*report, build_tiles()])
