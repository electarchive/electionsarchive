import hashlib
import json
import math
import warnings
from collections import Counter
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
        labels[fips] = [round(point.x), round(point.y)]
    east = max(shape.bounds[2] for shape in plane.values())
    south = max(shape.bounds[3] for shape in plane.values())
    return {
        "credit": CREDIT,
        "box": [0, 0, math.ceil(east), math.ceil(south)],
        "districts": {cd: path(shape) for cd, shape in sorted(plane.items())},
        "states": {fips: path(outline) for fips, outline in outlines.items()},
        "labels": labels,
    }


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
    return "\n".join(report)
