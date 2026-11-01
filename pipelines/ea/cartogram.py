import itertools
import json
import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import shapely
from scipy.fft import dctn, idctn
from scipy.ndimage import map_coordinates
from scipy.optimize import linear_sum_assignment
from shapely import affinity, make_valid, wkt
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel

from ea.hexes import INSETS, OUT, Tiles, outline
from ea.sources.us.place_units import STATE_FIPS
from ea.store import connect

type Cell = tuple[int, int]

K = 9
CLAMP = 40.0
REACH = 1.5
HALF = 0.9
PULL = 0.05
SPREAD_END = 0.6
SLACK = 2.0
BEND = 25.0
GRAVITY = 0.01
TWIST = 0.2
STEPS = 200_000
CLOSE = 5.0
S3 = math.sqrt(3)
HEX = 3 * S3 / 2
POSTAL = {code: postal for postal, code in (STATE_FIPS | INSETS).items()}
SKIP = ("runoff", "indirect", "special", "by_election")


@dataclass(frozen=True)
class Map:
    unit: dict[str, str]
    names: dict[str, str]
    merge: dict[str, str] = field(default_factory=dict)
    wide: dict[str, tuple[float, float] | None] = field(default_factory=dict)
    blob: frozenset[str] = frozenset()
    corner: dict[str, tuple[bool, float, float]] = field(default_factory=dict)
    links: tuple[tuple[str, str], ...] = ()
    isles: frozenset[str] = frozenset()


type Run = tuple[date, date, dict[str, BaseGeometry], dict[str, int], Map, list[str]]

GB_NAMES = {
    "E12000001": "NE",
    "E12000002": "NW",
    "E12000003": "YH",
    "E12000004": "EM",
    "E12000005": "WM",
    "E12000006": "EE",
    "E12000007": "LDN",
    "E12000008": "SE",
    "E12000009": "SW",
    "SCT": "SCT",
    "WLS": "WLS",
    "NIR": "NI",
    "IRL": "IRL",
}
UNIVERSITIES = {
    "eng-cambridge-university": (0.1149, 52.2043),
    "eng-oxford-university": (-1.2544, 51.7548),
    "eng-london-university": (-0.1300, 51.5226),
    "eng-combined-english-universities": (-2.2339, 53.4668),
    "irl-dublin-university": (-6.2546, 53.3438),
    "irl-national-university-of-ireland": (-6.2210, 53.3080),
    "irl-queen-s-university-of-belfast": (-5.9348, 54.5844),
    "nir-queen-s-university-of-belfast": (-5.9348, 54.5844),
    "sct-combined-scottish-universities": (-3.1883, 55.9445),
    "sct-aberdeen-and-glasgow-universities": (-4.2886, 55.8721),
    "sct-edinburgh-and-st-andrew-s-universities": (-3.1883, 55.9445),
    "wls-university-of-wales": (-3.1791, 51.4866),
}
US_CORNER: dict[str, tuple[bool, float, float]] = {
    "02": (False, 10, -2),
    "15": (False, 30, 6),
    "66": (False, 44, 9),
    "69": (False, 44, 2),
    "60": (False, 50, 9),
    "72": (True, -22, 6),
    "78": (True, -14, 6),
}


def congress(day: date) -> date:
    year = day.year + (day.year % 2 == 0)
    return date(year, 1, 3) if year >= 1935 else date(year, 3, 4)


def axial(x: float, z: float) -> tuple[float, float]:
    return (S3 * (x + z / 2), 1.5 * z)


def near(x: int, z: int) -> list[Cell]:
    return [(x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1), (x + 1, z - 1), (x - 1, z + 1)]


def hexagon(x: int, z: int) -> Polygon:
    cx, cy = axial(x, z)
    return Polygon(
        [(cx + math.cos(math.radians(60 * i + 30)), cy + math.sin(math.radians(60 * i + 30))) for i in range(6)]
    )


def groups(cells: Iterable[Cell]) -> list[set[Cell]]:
    left = set(cells)
    found = []
    while left:
        start = left.pop()
        group, stack = {start}, [start]
        while stack:
            for other in near(*stack.pop()):
                if other in left:
                    left.discard(other)
                    group.add(other)
                    stack.append(other)
        found.append(group)
    return sorted(found, key=len, reverse=True)


def centroid(cells: Iterable[Cell]) -> np.ndarray:
    return np.array([axial(*cell) for cell in cells]).mean(axis=0)


def solid(shape: BaseGeometry, floor: float = 0.004) -> BaseGeometry:
    parts = [part for part in getattr(shape, "geoms", [shape]) if part.geom_type == "Polygon"]
    big = max(part.area for part in parts)
    return make_valid(MultiPolygon([Polygon(part.exterior) for part in parts if part.area > big * floor]))


def diffuse(polys: list[BaseGeometry], density: list[float], n: int) -> Callable[[np.ndarray], np.ndarray]:
    whole = shapely.union_all(polys)
    a0, b0, a1, b1 = whole.bounds
    span = max(a1 - a0, b1 - b0)
    size = span * 2 / n
    ox, oy = (a0 + a1) / 2 - span, (b0 + b1) / 2 - span
    gx, gy = np.meshgrid(ox + (np.arange(n) + 0.5) * size, oy + (np.arange(n) + 0.5) * size)
    mean = sum(d * p.area for d, p in zip(density, polys, strict=True)) / whole.area
    rho = np.full((n, n), mean)
    for d, p in zip(density, polys, strict=True):
        rho[shapely.contains_xy(p, gx, gy)] = d
    rho = np.clip(rho, mean / CLAMP, mean * CLAMP)
    rho = rho / rho.mean()
    ky, kx = np.meshgrid(np.pi * np.arange(n) / n, np.pi * np.arange(n) / n, indexing="ij")
    k2 = kx**2 + ky**2
    spectrum = dctn(rho, type=2)

    def field(t: float) -> tuple[np.ndarray, np.ndarray]:
        now = idctn(spectrum * np.exp(-k2 * t), type=2)
        dy, dx = np.gradient(now)
        return -dx / now, -dy / now

    def move(g: np.ndarray, vx: np.ndarray, vy: np.ndarray) -> np.ndarray:
        coords = np.array([g[:, 1], g[:, 0]])
        return np.stack(
            [
                map_coordinates(vx, coords, order=1, mode="nearest"),
                map_coordinates(vy, coords, order=1, mode="nearest"),
            ],
            axis=1,
        )

    def warp(xy: np.ndarray) -> np.ndarray:
        g = np.column_stack([(xy[:, 0] - ox) / size - 0.5, (xy[:, 1] - oy) / size - 0.5])
        t = 0.01
        for _ in range(4000):
            if t >= n * n:
                break
            dt = t * 0.05
            vx, vy = field(t + dt / 2)
            step = move(g, vx, vy) * dt
            biggest = np.abs(step).max()
            if biggest > 0.3:
                dt *= 0.3 / biggest
                vx, vy = field(t + dt / 2)
                step = move(g, vx, vy) * dt
            g = g + step
            t += dt
        return np.column_stack([(g[:, 0] + 0.5) * size + ox, (g[:, 1] + 0.5) * size + oy])

    return warp


def holes(cells: set[Cell]) -> set[Cell]:
    xs = [x for x, _ in cells]
    zs = [z for _, z in cells]
    box = {
        (x, z) for x in range(min(xs) - 2 - (max(zs) - min(zs)), max(xs) + 3) for z in range(min(zs) - 1, max(zs) + 2)
    }
    empty = box - cells
    start = min(empty)
    seen, stack = {start}, [start]
    while stack:
        for other in near(*stack.pop()):
            if other in empty and other not in seen:
                seen.add(other)
                stack.append(other)
    return empty - seen


def blob(count: int) -> set[Cell]:
    ring = sorted(
        ((x, z) for x in range(-6, 7) for z in range(-6, 7)), key=lambda c: (np.hypot(*axial(*c)), c[1], c[0])
    )
    return set(ring[:count])


def raster(shape: BaseGeometry, count: int, tries: int = 5) -> tuple[set[Cell], tuple[float, ...]]:
    scale = math.sqrt(count * HEX / shape.area)
    c = shape.centroid
    base = affinity.affine_transform(shape, [scale, 0, 0, -scale, -c.x * scale, c.y * scale])
    smooth = make_valid(base.buffer(0.9).buffer(-0.9)).simplify(0.03)
    best: tuple[float, set[Cell], tuple[float, ...]] | None = None
    for i in range(tries):
        for j in range(tries):
            dx, dy = i / tries * S3, j / tries * 1.5
            moved = affinity.translate(smooth, dx, dy)
            a0, b0, a1, b1 = moved.bounds
            cells = [
                (x, z)
                for z in range(int(b0 / 1.5) - 2, int(b1 / 1.5) + 3)
                for x in range(int(a0 / S3 - z / 2) - 3, int(a1 / S3 - z / 2) + 4)
            ]
            cover = shapely.area(shapely.intersection([hexagon(*cell) for cell in cells], moved))
            rank = {cells[k]: cover[k] for k in range(len(cells))}
            order = sorted(range(len(cells)), key=lambda k: -cover[k])
            keep = set(groups(cells[k] for k in order[:count])[0])
            while len(keep) < count:
                edge = {o for cell in keep for o in near(*cell) if o not in keep}
                keep.add(max(edge, key=lambda o: rank.get(o, 0)))
            score = sum(rank.get(cell, 0) for cell in keep) - len(holes(keep)) * 2
            if best is None or score > best[0]:
                best = (score, keep, (scale, c.x, c.y, dx, dy))
    assert best is not None
    return best[1], best[2]


def roots() -> list[tuple[date, date, dict[str, int]]]:
    with connect() as db:
        db.execute("set statement_timeout = '600s'")
        rows = db.execute(
            """
            with recursive leaf as (
              select c.election_id, a.id area, coalesce(c.magnitude, 1) magnitude
              from contest c join area a on a.id = c.district_area_id and a.scheme_code = 'us.cd'
              where c.kind = 'body_seats' and c.allocation_parent_id is null and c.party_scope_id is null and not c.notional
            ), up(id, at, parent, skipped) as (
              select e.id, e.id, e.parent_id, e.kind = any(%(skip)s)
              from election e where e.id in (select election_id from leaf)
              union all
              select up.id, p.id, p.parent_id, up.skipped or (p.parent_id is not null and p.kind = any(%(skip)s))
              from up join election p on p.id = up.parent
            )
            select r.date_from, e.date_from, ac.code, max(leaf.magnitude)
            from leaf join up on up.id = leaf.election_id and up.parent is null and not up.skipped
            join election r on r.id = up.at join election e on e.id = leaf.election_id
            join area_code ac on ac.area_id = leaf.area
            group by 1, 2, 3
            """,
            {"skip": list(SKIP)},
        ).fetchall()
    found: dict[date, list[tuple[date, str, int]]] = defaultdict(list)
    for root, day, code, magnitude in rows:
        found[root].append((congress(day), code, magnitude))
    picked = []
    for root, contests in sorted(found.items()):
        if len({code for _, code, _ in contests}) < 20:
            continue
        start = Counter(day for day, _, _ in contests).most_common(1)[0][0]
        seats: dict[str, int] = {}
        for day, code, magnitude in contests:
            if day == start:
                seats[code] = max(seats.get(code, 1), magnitude)
        picked.append((root, start, seats))
    return picked


def shapes_on(day: date, used: dict[str, int]) -> tuple[dict[str, BaseGeometry], dict[str, int]]:
    with connect() as db:
        rows = db.execute(
            """
            select ac.code, st_astext(st_buffer(st_simplify(st_transform(g.geom, case
              when left(ac.code, 2) = '02' then 3338
              when left(ac.code, 2) in ('15', '72', '78', '66', '60', '69') then 3857
              else 5070 end), 150), 0))
            from area a join area_code ac on ac.area_id = a.id
            join area_geometry g on g.area_id = a.id and g.valid @> %(day)s
            where a.scheme_code = 'us.cd'
            """,
            {"day": day},
        ).fetchall()
        apportioned = dict(
            db.execute(
                """
                select ac.code, ba.seats from body_apportionment ba
                join body b on b.id = ba.body_id and b.kind = 'lower_house'
                join area a on a.id = ba.area_id and a.scheme_code = 'us.state'
                join area_code ac on ac.area_id = a.id
                where ba.valid @> %(day)s
                """,
                {"day": day},
            ).fetchall()
        )
    shapes = {code: make_valid(wkt.loads(text)) for code, text in sorted(rows)}
    seats = {code: used.get(code, 1) for code in shapes if not code.endswith("00")}
    for wide in [code for code in shapes if code.endswith("00")]:
        state = wide[:2]
        taken = sum(count for code, count in seats.items() if code[:2] == state)
        left = apportioned.get(state, taken) - taken
        if wide in used or taken == 0 or left > 0:
            seats[wide] = used.get(wide, max(left, 1))
    return {code: shapes[code] for code in sorted(seats)}, seats


def us_map(shapes: dict[str, BaseGeometry]) -> Map:
    return Map(
        unit={code: code[:2] for code in shapes},
        names=POSTAL,
        merge={"11": "24"},
        wide=dict.fromkeys([code for code in shapes if code.endswith("00")]),
        blob=frozenset({"66", "69", "60", "78"}),
        corner=US_CORNER,
    )


def membership(m: Map) -> dict[str, list[str]]:
    members: dict[str, list[str]] = defaultdict(list)
    for code, unit in sorted(m.unit.items(), key=lambda item: (item[1] in m.merge, item[0])):
        members[m.merge.get(unit, unit)].append(code)
    return dict(sorted(members.items()))


def arrange(
    shapes: dict[str, BaseGeometry], seats: dict[str, int], m: Map
) -> tuple[dict[str, set[Cell]], dict[str, tuple[float, ...]], dict[str, Cell]]:
    members = membership(m)
    fips = list(members)
    count = {s: sum(seats.get(c, 1) for c in members[s]) for s in fips}
    mainland = [s for s in fips if s not in m.corner]
    outline = {
        s: make_valid(
            solid(make_valid(shapely.union_all([shapes[c] for c in members[s] if c in shapes]).buffer(0))).simplify(800)
        )
        for s in fips
    }
    masks: dict[str, set[Cell]] = {}
    frames: dict[str, tuple[float, ...]] = {}
    for s in fips:
        if s in m.blob:
            c = outline[s].centroid
            masks[s] = blob(count[s] * K)
            frames[s] = (math.sqrt(count[s] * K * HEX / outline[s].area), c.x, c.y, 0, 0)
        else:
            masks[s], frames[s] = raster(outline[s], count[s] * K)

    polys = [outline[s].simplify(3000) for s in mainland]
    warp = diffuse(polys, [count[s] / outline[s].area for s in mainland], 512)
    seeds = np.array([[p.centroid.x, p.centroid.y] for p in polys])
    moved = warp(np.vstack([seeds, shapely.get_coordinates(polys)]))
    flat = shapely.set_coordinates(np.array(polys, dtype=object), moved[len(seeds) :])
    unit = math.sqrt(sum(len(masks[s]) for s in mainland) * HEX / sum(make_valid(p).area for p in flat))
    cx, cy = moved[: len(seeds)].mean(axis=0)
    origin = {s: np.array([(moved[i][0] - cx) * unit, -(moved[i][1] - cy) * unit]) for i, s in enumerate(mainland)}
    touch: dict[str, set[str]] = defaultdict(set)
    for i, a in enumerate(mainland):
        for b in mainland[i + 1 :]:
            shared = outline[a].buffer(500).intersection(outline[b].buffer(500))
            if not shared.is_empty and shared.area > 500 * 2 * 20000:
                touch[a].add(b)
                touch[b].add(a)
    for a, b in m.links:
        if a in mainland and b in mainland:
            touch[a].add(b)
            touch[b].add(a)

    local = {s: centroid(masks[s]) for s in fips}
    placed: dict[str, Cell] = {}
    owner: dict[Cell, str] = {}
    blocked: Counter[Cell] = Counter()

    def put(s: str, off: Cell) -> None:
        placed[s] = off
        for x, z in masks[s]:
            cell = (x + off[0], z + off[1])
            owner[cell] = s
            blocked[cell] += 1
            for o in near(*cell):
                blocked[o] += 1

    def lift(s: str) -> None:
        off = placed.pop(s)
        for x, z in masks[s]:
            cell = (x + off[0], z + off[1])
            del owner[cell]
            blocked[cell] -= 1
            for o in near(*cell):
                blocked[o] -= 1

    def fits(s: str, off: Cell) -> bool:
        return all(blocked[(x + off[0], z + off[1])] == 0 for x, z in masks[s])

    def offset_for(s: str, point: np.ndarray) -> Cell:
        want = point - local[s]
        z = round(want[1] / 1.5)
        return (round(want[0] / S3 - z / 2), z)

    band = {}
    for s in fips:
        first = {o for c in masks[s] for o in near(*c)} - masks[s]
        band[s] = list({o for c in first for o in near(*c)} - masks[s] - first)

    def contact(s: str, off: Cell) -> tuple[int, int]:
        hit: Counter[str] = Counter()
        for x, z in band[s]:
            w = owner.get((x + off[0], z + off[1]))
            if w is not None and w != s:
                hit[w] += 1
        return sum(min(hit[f], 25) for f in touch[s] if f in hit), sum(v for k, v in hit.items() if k not in touch[s])

    body = {
        s: affinity.translate(shapely.union_all([hexagon(*c) for c in masks[s]]).buffer(HALF), *(-local[s]))
        for s in mainland
    }
    pos = {s: origin[s] * 1.8 for s in mainland}
    mass = {s: len(masks[s]) for s in mainland}

    def separate(rounds: int) -> float:
        worst = 0.0
        for _ in range(rounds):
            now = {s: affinity.translate(body[s], *pos[s]) for s in mainland}
            boxes = {s: now[s].bounds for s in mainland}
            push = {s: np.zeros(2) for s in mainland}
            worst = 0.0
            for i, a in enumerate(mainland):
                for b in mainland[i + 1 :]:
                    ax0, ay0, ax1, ay1 = boxes[a]
                    bx0, by0, bx1, by1 = boxes[b]
                    if ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0:
                        continue
                    inter = now[a].intersection(now[b])
                    if inter.area <= 0:
                        continue
                    worst = max(worst, inter.area)
                    u = pos[b] - pos[a]
                    u = u / max(np.hypot(*u), 1e-6)
                    amount = min(math.sqrt(inter.area) * 0.3, 0.6)
                    push[a] -= u * amount * mass[b] / (mass[a] + mass[b])
                    push[b] += u * amount * mass[a] / (mass[a] + mass[b])
            for s in mainland:
                pos[s] = pos[s] + push[s]
            if worst == 0:
                break
        return worst

    f = 1.8
    for _ in range(500):
        for s in mainland:
            pos[s] = pos[s] + (origin[s] * f - pos[s]) * PULL
        f = max(SPREAD_END, f - 0.006) if separate(10) < SLACK else f + 0.004
    separate(200)
    target = dict(pos)

    def cost(s: str, off: Cell, alpha: float) -> float:
        good, bad = contact(s, off)
        return float(np.sum((local[s] + np.array(axial(*off)) - target[s]) ** 2)) - alpha * good + alpha * 0.5 * bad

    def search(s: str, center: np.ndarray, radius: int, alpha: float) -> tuple[float, Cell] | None:
        best = None
        base = offset_for(s, center)
        for dz in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                off = (base[0] + dx, base[1] + dz)
                if abs(dx + dz) > radius or not fits(s, off):
                    continue
                c = cost(s, off, alpha)
                if best is None or c < best[0]:
                    best = (c, off)
        return best

    def settle(s: str, point: np.ndarray) -> None:
        radius = 2
        while (found := search(s, point, radius, 0.0)) is None:
            radius += 2
        put(s, found[1])

    for s in sorted(mainland, key=lambda s: -len(masks[s])):
        settle(s, target[s])
    rng = random.Random(1)
    for _ in range(30):
        moves = 0
        order = list(mainland)
        rng.shuffle(order)
        for s in order:
            old = placed[s]
            lift(s)
            options = [search(s, local[s] + np.array(axial(*old)), 3, 1.0), search(s, target[s], 3, 1.0)]
            best = min([o for o in options if o is not None] + [(cost(s, old, 1.0), old)], key=lambda o: o[0])
            put(s, best[1])
            moves += best[1] != old
        if moves == 0:
            break

    def spot(s: str, off: Cell) -> np.ndarray:
        return local[s] + np.array(axial(*off))

    def bend(a: str, b: str, pa: np.ndarray, pb: np.ndarray) -> float:
        u, v = pb - pa, origin[b] - origin[a]
        c = np.dot(u, v) / max(np.hypot(*u) * np.hypot(*v), 1e-9)
        return math.degrees(math.acos(max(-1.0, min(1.0, c))))

    cube = {s: np.array(sorted(masks[s])) for s in mainland}

    def apart(a: str, oa: Cell, b: str, ob: Cell) -> int:
        p, q = cube[a] + np.array(oa), cube[b] + np.array(ob)
        dx = p[:, None, 0] - q[None, :, 0]
        dz = p[:, None, 1] - q[None, :, 1]
        return int(((np.abs(dx) + np.abs(dz) + np.abs(dx + dz)) // 2).min())

    def energy(s: str, off: Cell, reach: float) -> float:
        p = spot(s, off)
        total = GRAVITY * float(np.sum((p - origin[s]) ** 2))
        for n in touch[s]:
            if n in placed:
                gap = apart(s, off, n, placed[n]) - 2
                total += min(gap, 8) ** 1.5 + max(0, gap - 8) * reach
                total += max(0.0, bend(s, n, p, spot(n, placed[n])) - BEND) ** 2 * TWIST
        return total

    def pieces(close: Callable[[str, str], bool]) -> int:
        seen: set[str] = set()
        count = 0
        for first in mainland:
            if first in seen:
                continue
            count += 1
            seen.add(first)
            stack = [first]
            while stack:
                a = stack.pop()
                for b in touch[a]:
                    if b not in seen and close(a, b):
                        seen.add(b)
                        stack.append(b)
        return count

    whole = pieces(lambda a, b: True)
    start = dict(placed)
    best: tuple[int, dict[str, Cell]] | None = None
    steps = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)]
    for attempt, reach in enumerate((0.0, 4.0, 4.0, 4.0)):
        for s in list(placed):
            lift(s)
        for s, off in start.items():
            put(s, off)
        rng = random.Random(7 + attempt)
        for step in range(STEPS):
            temp = 3.0 * (0.01 / 3.0) ** (step / STEPS)
            s = rng.choice(mainland)
            old = placed[s]
            dx, dz = rng.choice(steps)
            off = (old[0] + dx, old[1] + dz)
            lift(s)
            if fits(s, off):
                delta = energy(s, off, reach) - energy(s, old, reach)
                if delta <= 0 or rng.random() < math.exp(-delta / temp):
                    old = off
            put(s, old)
        split = pieces(lambda a, b: apart(a, placed[a], b, placed[b]) <= 3)
        if best is None or split < best[0]:
            best = (split, dict(placed))
        if split == whole:
            break
    assert best is not None
    for s in list(placed):
        lift(s)
    for s, off in best[1].items():
        put(s, off)

    def clusters() -> list[set[str]]:
        found: list[set[str]] = []
        seen: set[str] = set()
        for first in mainland:
            if first in seen:
                continue
            group = {first}
            stack = [first]
            while stack:
                a = stack.pop()
                for b in touch[a]:
                    if b not in group and apart(a, placed[a], b, placed[b]) <= 3:
                        group.add(b)
                        stack.append(b)
            seen |= group
            found.append(group)
        return found

    for _ in range(len(mainland)):
        loose = sorted(
            (g for g in clusters() if any(b not in g for a in g for b in touch[a])),
            key=lambda g: sum(len(masks[s]) for s in g),
        )
        main = loose[-1] if loose else set()
        joining = [g for g in loose[:-1] if any(b in main for a in g for b in touch[a])]
        if not joining:
            break
        group = joining[0]
        pairs = [(a, b) for a in group for b in touch[a] if b in main]
        ideal = np.mean([spot(b, placed[b]) + origin[a] - origin[b] - spot(a, placed[a]) for a, b in pairs], axis=0)
        z0 = round(ideal[1] / 1.5)
        x0 = round(ideal[0] / S3 - z0 / 2)
        old = {s: placed[s] for s in group}
        for s in group:
            lift(s)
        choice: tuple[float, Cell] | None = None
        for radius in range(4, 40, 4):
            for dz in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if abs(dx + dz) > radius:
                        continue
                    shift = (x0 + dx, z0 + dz)
                    moved = {s: (old[s][0] + shift[0], old[s][1] + shift[1]) for s in group}
                    if not all(fits(s, moved[s]) for s in group):
                        continue
                    gaps = sum(max(0, apart(a, moved[a], b, placed[b]) - 2) for a, b in pairs)
                    score = gaps * 100 + abs(dx) + abs(dz) + abs(dx + dz)
                    if choice is None or score < choice[0]:
                        choice = (score, shift)
            if choice is not None and choice[0] < 100:
                break
        shift = choice[1] if choice is not None else (0, 0)
        for s in group:
            put(s, (old[s][0] + shift[0], old[s][1] + shift[1]))

    xs = [axial(*cell)[0] for cell in owner]
    ys = [axial(*cell)[1] for cell in owner]
    left, right, bottom = min(xs), max(xs), max(ys)
    for s, (east, dx, dy) in m.corner.items():
        if s in masks:
            target[s] = np.array(((right if east else left) + dx, bottom + dy))
            settle(s, target[s])
    return masks, frames, placed


def fix(taken: dict[Cell, str], names: list[str], need: dict[str, int]) -> list[str]:
    def owned(code: str) -> list[Cell]:
        return [cell for cell, o in taken.items() if o == code]

    def parts(code: str) -> list[set[Cell]]:
        return groups(owned(code))

    def shift(src: str, dst: str) -> bool:
        mine = owned(src)
        if len(mine) <= 1:
            return False
        goal = centroid(owned(dst))
        edge = [cell for cell in mine if any(taken.get(o) == dst for o in near(*cell))]
        for cell in sorted(edge, key=lambda c: np.hypot(*np.subtract(axial(*c), goal))):
            if len(groups(set(mine) - {cell})) == 1:
                taken[cell] = dst
                return True
        return False

    def links(code: str) -> set[str]:
        return {taken[o] for cell in owned(code) for o in near(*cell) if o in taken and taken[o] != code}

    def route(src: str, dst: str, banned: set[tuple[str, str]]) -> list[str] | None:
        prev: dict[str, str | None] = {src: None}
        queue = [src]
        while queue:
            at = queue.pop(0)
            if at == dst:
                break
            for nxt in links(at):
                if nxt not in prev and (at, nxt) not in banned:
                    prev[nxt] = at
                    queue.append(nxt)
        if dst not in prev:
            return None
        path = [dst]
        while (back := prev[path[-1]]) is not None:
            path.append(back)
        return path[::-1]

    def balance(src: str, dst: str) -> None:
        banned: set[tuple[str, str]] = set()
        for _ in range(60):
            path = route(src, dst, banned)
            if path is None:
                return
            for a, b in itertools.pairwise(path):
                if not shift(a, b):
                    banned.add((a, b))
                    src = a
                    break
            else:
                return

    for _ in range(60):
        broken = [(c, p) for c in names for p in parts(c)[1:]]
        if not broken:
            break
        c, piece = broken[0]
        given = []
        left = set(piece)
        while left:
            ready = [(Counter(taken[o] for o in near(*cell) if o in taken and taken[o] != c), cell) for cell in left]
            ready = [(v, cell) for v, cell in ready if v]
            if not ready:
                break
            votes, cell = max(ready, key=lambda r: max(r[0].values()))
            taken[cell] = votes.most_common(1)[0][0]
            given.append(taken[cell])
            left.discard(cell)
        for who in given:
            balance(who, c)
    return [c for c in names if len(parts(c)) > 1 or len(owned(c)) != need[c]]


def districts(
    shapes: dict[str, BaseGeometry],
    seats: dict[str, int],
    m: Map,
    masks: dict[str, set[Cell]],
    frames: dict[str, tuple[float, ...]],
    placed: dict[str, Cell],
) -> tuple[dict[Cell, str], list[str]]:
    district: dict[Cell, str] = {}
    problems = []
    for s, members in membership(m).items():
        cells = [(x + placed[s][0], z + placed[s][1]) for x, z in masks[s]]
        if len(members) == 1:
            district.update(dict.fromkeys(cells, members[0]))
            continue
        names = [c for c in members if c not in m.wide]
        wide = [c for c in members if c in m.wide]
        anchors = {c: point for c in wide if (point := m.wide[c]) is not None}
        pinned = list(anchors)
        need = {c: K * seats.get(c, 1) for c in members}
        scale, ox, oy, dx, dy = frames[s]
        off = axial(*placed[s])
        matrix = [scale, 0, 0, -scale, -ox * scale + dx + off[0], oy * scale + dy + off[1]]
        polys = [make_valid(affinity.affine_transform(shapes[c], matrix).simplify(0.05)) for c in names]
        warp = diffuse(polys, [1 / p.area for p in polys], 128)
        seeds = np.array(
            [[p.representative_point().x, p.representative_point().y] for p in polys]
            + [[matrix[0] * x + matrix[4], matrix[3] * y + matrix[5]] for x, y in anchors.values()]
        )
        moved = warp(np.vstack([seeds, shapely.get_coordinates(polys)]))
        warped = [make_valid(p) for p in shapely.set_coordinates(np.array(polys, dtype=object), moved[len(seeds) :])]
        union = shapely.union_all(warped)
        uc = np.array(union.centroid.coords[0])
        mc = centroid(cells)
        fit = math.sqrt(len(cells) * HEX / union.area)
        warped = [
            affinity.affine_transform(p, [fit, 0, 0, fit, mc[0] - uc[0] * fit, mc[1] - uc[1] * fit]) for p in warped
        ]
        home = {c: (np.array(moved[i]) - uc) * fit + mc for i, c in enumerate([*names, *pinned])} | dict.fromkeys(
            [c for c in wide if c not in pinned], mc
        )
        points = np.array([axial(*cell) for cell in cells])
        reach = {c: shapely.distance(warped[i], shapely.points(points)) for i, c in enumerate(names)}
        reach |= {c: np.zeros(len(points)) for c in wide}
        owners = [c for c in members for _ in range(need[c])]
        split = members
        taken: dict[Cell, str] = {}
        for weight in (0.25, 0.5, 1.0, 2.0, 4.0):
            spots = dict(home)
            for _ in range(10):
                rows = [reach[c] ** 2 * REACH + ((points - spots[c]) ** 2).sum(axis=1) * weight for c in owners]
                picked, columns = linear_sum_assignment(np.array(rows))
                taken = {cells[j]: owners[i] for i, j in zip(picked, columns, strict=True)}
                spread: dict[str, list[tuple[float, float]]] = defaultdict(list)
                for cell, c in taken.items():
                    spread[c].append(axial(*cell))
                spots = {c: 0.25 * home[c] + 0.75 * np.mean(spread[c], axis=0) for c in members}
            split = fix(taken, members, need)
            if not split:
                break
        if split:
            problems.append(f"{m.names[s]} split {', '.join(split)}")
        district.update(taken)
    return district, problems


def path(shape: BaseGeometry) -> str:
    rings = []
    for poly in getattr(shape, "geoms", [shape]):
        for ring in [poly.exterior, *poly.interiors]:
            rings.append("M" + "L".join(f"{x:.2f} {y:.2f}".replace(".00", "") for x, y in ring.coords[:-1]) + "Z")
    return "".join(rings)


def union(cells: Iterable[Cell]) -> BaseGeometry:
    return shapely.set_precision(shapely.union_all([hexagon(*c) for c in cells]), 0.001)


def drawn(district: dict[Cell, str], seats: dict[str, int], m: Map) -> dict:
    by_code: dict[str, list[Cell]] = defaultdict(list)
    for cell, code in district.items():
        by_code[code].append(cell)
    by_state: dict[str, list[str]] = defaultdict(list)
    for code in by_code:
        by_state[m.unit[code]].append(code)
    world = union(district)
    shapes = {code: union(cells) for code, cells in sorted(by_code.items())}
    points = {
        code: polylabel(max(getattr(shape, "geoms", [shape]), key=lambda g: g.area), 0.05)
        for code, shape in shapes.items()
    }
    usual = Counter(seats.get(code, 1) for code in by_code).most_common(1)[0][0]
    counts = {
        code: [round(points[code].x, 2), round(points[code].y, 2), seats[code]]
        for codes in by_state.values()
        if len(codes) > 1
        for code in codes
        if seats.get(code, 1) > max(usual, 1)
    }
    labels: dict[str, list] = {}
    for s, codes in sorted(by_state.items()):
        whole = union(cell for code in codes for cell in by_code[code])
        core = max(getattr(whole, "geoms", [whole]), key=lambda g: g.area)
        stacked = seats.get(codes[0], 1) if len(codes) == 1 else 1
        width, height = len(m.names[s]) * 0.625, 1.9 if stacked > 1 else 0.9
        boxes = [
            shapely.box(
                x - len(str(n)) * 0.45 - width, y - 0.75 - height, x + len(str(n)) * 0.45 + width, y + 0.75 + height
            )
            for code in codes
            if code in counts
            for x, y, n in [counts[code]]
        ]
        room = core.difference(shapely.union_all(boxes)) if boxes else core
        room = max(getattr(room, "geoms", [room]), key=lambda g: g.area) if not room.is_empty else core
        point = polylabel(room, 0.05)
        x, y = point.x, point.y
        if core.boundary.distance(point) < (2.0 if stacked > 1 else 1.6):
            a0, b0, a1, b1 = whole.bounds
            spots = [
                ((a0 + a1) / 2, b1 + height + 0.6),
                ((a0 + a1) / 2, b0 - height - 0.6),
                (a1 + width + 0.35, point.y),
                (a0 - width - 0.35, point.y),
            ]
            for x, y in spots:
                if not world.intersects(shapely.box(x - width, y - height, x + width, y + height)):
                    break
        labels[s] = [round(x, 2), round(y, 2), m.names[s], *([stacked] if stacked > 1 else [])]
    return {
        "districts": {code: path(shape) for code, shape in shapes.items()},
        "bounds": {code: [round(value, 2) for value in shape.bounds] for code, shape in shapes.items()},
        "points": {code: [round(p.x, 2), round(p.y, 2)] for code, p in points.items()},
        "states": {
            s: path(union(cell for code in codes for cell in by_code[code])) for s, codes in sorted(by_state.items())
        },
        "labels": labels,
        "counts": counts,
    }


def tiles(district: dict[Cell, str], seats: dict[str, int], m: Map, scheme: str) -> tuple[dict, list[str]]:
    fine: dict[str, list[Cell]] = defaultdict(list)
    spread: dict[str, list[Cell]] = defaultdict(list)
    for cell, code in district.items():
        fine[m.unit[code]].append(cell)
        spread[code].append(cell)
    need = {code: seats.get(code, 1) for code in spread}
    total_of: Counter[str] = Counter()
    for code, count in need.items():
        total_of[m.unit[code]] += count
    inland = [s for s in sorted(fine) if s not in m.corner]
    lands: list[Polygon] = []
    for part in [[s for s in inland if s not in m.isles]] + [[s] for s in inland if s in m.isles]:
        closed = union(cell for s in part for cell in fine[s]).buffer(CLOSE).buffer(-CLOSE)
        lands.extend(Polygon(piece.exterior) for piece in getattr(closed, "geoms", [closed]))
    lands.sort(key=lambda land: -land.area)
    total = sum(total_of[s] for s in inland)
    f = math.sqrt(sum(land.area for land in lands) / (total * HEX))
    home = {
        s: max(
            range(len(lands)),
            key=lambda i: int(shapely.contains_xy(lands[i], *np.array([axial(*cell) for cell in fine[s]]).T).sum()),
        )
        for s in inland
    }
    used: set[Cell] = set()
    sea: set[Cell] = set()
    for i, land in enumerate(lands):
        count = sum(total_of[s] for s in inland if home[s] == i)
        if count == 0:
            continue
        a0, b0, a1, b1 = land.bounds
        spots = [
            (x, z)
            for z in range(math.floor(b0 / (1.5 * f)) - 1, math.ceil(b1 / (1.5 * f)) + 2)
            for x in range(math.floor(a0 / (S3 * f) - z / 2) - 2, math.ceil(a1 / (S3 * f) - z / 2) + 3)
            if (x, z) not in sea
        ]
        cover = shapely.area(
            shapely.intersection([affinity.scale(hexagon(*c), f, f, origin=(0, 0)) for c in spots], land)
        )
        rank = dict(zip(spots, cover, strict=True))
        keep = groups(sorted(spots, key=lambda c: -rank[c])[:count])[0]
        while len(keep) < count:
            keep.add(max({o for c in keep for o in near(*c) if o not in keep | sea}, key=lambda o: rank.get(o, 0)))
        used |= keep
        sea |= keep | {o for c in keep for o in near(*c)}
    chosen = sorted(used)
    points = np.array([axial(*c) for c in chosen]) * f
    rows = []
    for s in inland:
        mine = np.array([axial(*cell) for cell in fine[s]])
        closest = np.sqrt(((points[:, None, :] - mine[None, :, :]) ** 2).sum(axis=2)).min(axis=1) / f
        rows.extend([closest**2 + 0.05 * ((points - mine.mean(axis=0)) ** 2).sum(axis=1) / f**2] * total_of[s])
    picked, columns = linear_sum_assignment(np.array(rows))
    owners = [s for s in inland for _ in range(total_of[s])]
    taken = {chosen[j]: owners[i] for i, j in zip(picked, columns, strict=True)}
    problems = [f"tiles split {m.names[s]}" for s in fix(taken, inland, dict(total_of))]
    for s in m.corner:
        if s not in fine:
            continue
        middle = centroid(fine[s]) / f
        z = round(middle[1] / 1.5)
        mine = [(round(middle[0] / S3 - z / 2), z)]
        while len(mine) < total_of[s]:
            mine.append(min({o for c in mine for o in near(*c)} - set(mine), key=lambda o: (o[1], o[0])))
        taken.update(dict.fromkeys(mine, s))
    placed: dict[Cell, str] = {}
    for s in sorted(fine):
        cells = [cell for cell, owner in taken.items() if owner == s]
        codes = sorted(code for code in spread if m.unit[code] == s)
        if len(codes) == 1:
            placed.update(dict.fromkeys(cells, codes[0]))
            continue
        at = np.array([axial(*cell) for cell in cells]) * f
        copies = [code for code in codes for _ in range(need[code])]
        middle = {code: centroid(spread[code]) for code in codes}
        picked, columns = linear_sum_assignment(np.array([((at - middle[code]) ** 2).sum(axis=1) for code in copies]))
        mine = {cells[j]: copies[i] for i, j in zip(picked, columns, strict=True)}
        problems.extend(f"tiles split {code}" for code in fix(mine, codes, need))
        placed.update(mine)
    top = min(z for _, z in placed)
    shift = top - (top & 1)
    grid = {(x + (z - (z & 1)) // 2, z - shift): code for (x, z), code in placed.items()}
    hexes: dict[str, list[int]] = defaultdict(list)
    for (q, r), code in sorted(grid.items()):
        hexes[code].extend([q, -r])
    shape = outline(Tiles(scheme, 0, 2, m.names, {}), {cell: m.names[m.unit[code]] for cell, code in grid.items()})
    regions = {code: m.unit[code] for code in sorted(hexes) if m.unit[code] != code[:-2]}
    return {"hexes": dict(sorted(hexes.items())), **({"regions": regions} if regions else {}), **shape}, problems


def us_runs() -> Iterator[Run]:
    for root, day, used in roots():
        shapes, seats = shapes_on(day, used)
        yield root, day, shapes, seats, us_map(shapes), sorted(set(used) - set(shapes))


def gb_runs() -> Iterator[Run]:
    with connect() as db:
        db.execute("set statement_timeout = '600s'")
        rows = db.execute(
            """
            select e.date_from, a.id, ac.code, max(coalesce(c.magnitude, 1)),
              (select pc.code from area_parent ap join area p on p.id = ap.parent_id and p.scheme_code = 'gb.country'
               join area_code pc on pc.area_id = p.id where ap.area_id = a.id and ap.valid @> e.date_from limit 1),
              (select pc.code from area_parent ap join area p on p.id = ap.parent_id and p.scheme_code = 'gb.region'
               join area_code pc on pc.area_id = p.id where ap.area_id = a.id and ap.valid @> e.date_from limit 1)
            from contest c join election e on e.id = c.election_id and e.parent_id is null and e.kind = 'general'
            join body b on b.id = c.body_id and b.kind = 'lower_house'
            join area a on a.id = c.district_area_id and a.scheme_code = 'gb.pcon'
            join area_code ac on ac.area_id = a.id and (ac.valid @> e.date_from or ac.valid is null)
            where c.kind = 'body_seats' and c.allocation_parent_id is null and c.party_scope_id is null and not c.notional
            group by 1, 2, 3
            """
        ).fetchall()
        regions = [
            (code, make_valid(wkt.loads(text)))
            for code, text in db.execute(
                """
                select distinct on (ac.code) ac.code, st_astext(st_simplify(st_transform(g.geom, 27700), 200))
                from area a join area_code ac on ac.area_id = a.id join area_geometry g on g.area_id = a.id
                where a.scheme_code = 'gb.region' order by ac.code, g.valid_from desc nulls last
                """
            ).fetchall()
        ]
        anchors = {
            code: (x, y)
            for code, x, y in db.execute(
                """
                select code, st_x(p), st_y(p) from (
                  select code, st_transform(st_setsrid(st_point(lon, lat), 4326), 27700) p
                  from unnest(%(codes)s::text[], %(lon)s::float8[], %(lat)s::float8[]) u(code, lon, lat)) t
                """,
                {
                    "codes": list(UNIVERSITIES),
                    "lon": [lon for lon, _ in UNIVERSITIES.values()],
                    "lat": [lat for _, lat in UNIVERSITIES.values()],
                },
            ).fetchall()
        }
    found: dict[date, list[tuple[str, str, int, str, str | None]]] = defaultdict(list)
    for day, area, code, magnitude, country, region in rows:
        found[day].append((area, code, magnitude, "IRL" if country == "NIR" and day.year < 1922 else country, region))
    for day, contests in sorted(found.items()):
        with connect() as db:
            texts = dict(
                db.execute(
                    """
                    select g.area_id, st_astext(st_buffer(st_simplify(st_transform(g.geom, 27700), 100), 0))
                    from area_geometry g where g.area_id = any(%(areas)s) and g.valid @> %(day)s
                    """,
                    {"areas": [area for area, *_ in contests], "day": day},
                ).fetchall()
            )
        shapes: dict[str, BaseGeometry] = {}
        seats: dict[str, int] = {}
        unit: dict[str, str] = {}
        wide: dict[str, tuple[float, float] | None] = {}
        missing = []
        for area, code, magnitude, country, region in sorted(contests, key=lambda c: c[1]):
            point = anchors.get(code)
            if area in texts:
                shapes[code] = make_valid(wkt.loads(texts[area]))
            elif point is not None:
                wide[code] = point
            else:
                missing.append(code)
                continue
            seats[code] = magnitude
            if region is not None:
                unit[code] = region
            elif country != "ENG":
                unit[code] = country
            else:
                spot = shapes[code] if code in shapes else shapely.Point(point)
                unit[code] = max(regions, key=lambda r: (r[1].intersection(spot).area, -r[1].distance(spot)))[0]
        yield (
            day,
            day,
            shapes,
            seats,
            Map(unit, GB_NAMES, wide=wide, links=(("NIR", "SCT"),), isles=frozenset({"NIR", "IRL"})),
            missing,
        )


def layouts(prefix: str, scheme: str, runs: Iterable[Run], out: Path = OUT) -> list[str]:
    report = []
    index: dict[str, str] = {}
    written: dict[str, str] = {}
    cache: dict[tuple[tuple[str, int], ...], tuple] = {}
    for root, day, shapes, seats, m, missing in runs:
        sizes: Counter[str] = Counter()
        for code, unit in m.unit.items():
            sizes[m.merge.get(unit, unit)] += seats.get(code, 1)
        key = tuple(sorted(sizes.items()))
        if key not in cache:
            cache[key] = arrange(shapes, seats, m)
        district, problems = districts(shapes, seats, m, *cache[key])
        tiled, more = tiles(district, seats, m, scheme)
        name = root.isoformat()
        report.extend(f"{name} {problem}" for problem in [*problems, *more])
        report.extend(f"{name} {code} has no shape" for code in missing)
        built = json.dumps(drawn(district, seats, m) | {"tiles": tiled}, separators=(",", ":"))
        first = written.setdefault(built, name)
        if first == name:
            (out / f"{prefix}-{name}.json").write_text(built + "\n")
        index[name] = first
        report.append(
            f"{prefix} {name}: seats of {day}, {len(m.unit)} districts, {sum(sizes.values())} seats, layout {first}"
        )
    for stale in out.glob(f"{prefix}-[0-9]*.json"):
        if stale.stem.removeprefix(f"{prefix}-") not in written.values():
            stale.unlink()
    (out / f"{prefix}.json").write_text(json.dumps(index, separators=(",", ":")) + "\n")
    return report


def build() -> str:
    return "\n".join([*layouts("us-cd", "us.cd", us_runs()), *layouts("gb-pcon", "gb.pcon", gb_runs())])
