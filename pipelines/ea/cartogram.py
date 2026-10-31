import itertools
import json
import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import date, timedelta

import numpy as np
import shapely
from scipy.fft import dctn, idctn
from scipy.ndimage import map_coordinates
from scipy.optimize import linear_sum_assignment
from shapely import affinity, make_valid, wkt
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel

from ea.hexes import GRIDS, INSETS, OUT
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
BLOB = {"66", "69", "60", "78"}
APART = ["02", "15", "66", "69", "60", "72", "78"]
S3 = math.sqrt(3)
HEX = 3 * S3 / 2
POSTAL = {code: postal for postal, code in (STATE_FIPS | INSETS).items()}
DECADES = [1992, 2002, 2012, 2022]
LAST = 2026


def election_day(year: int) -> date:
    first = date(year, 11, 2)
    return first + timedelta((1 - first.weekday()) % 7)


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


def shapes_on(day: date) -> dict[str, BaseGeometry]:
    with connect() as db:
        rows = db.execute(
            """
            select ac.code, st_astext(st_buffer(st_simplify(st_transform(g.geom, case
              when left(ac.code, 2) = '02' then 3338
              when left(ac.code, 2) in ('15', '72', '78', '66', '60', '69') then 3857
              else 5070 end), 150), 0))
            from area a join area_code ac on ac.area_id = a.id and (ac.valid @> %(day)s or ac.valid is null)
            join area_geometry g on g.area_id = a.id and g.valid @> %(day)s
            where a.scheme_code = 'us.cd'
            """,
            {"day": day},
        ).fetchall()
    return {code: make_valid(wkt.loads(text)) for code, text in sorted(rows)}


def membership(shapes: dict[str, BaseGeometry]) -> dict[str, list[str]]:
    members: dict[str, list[str]] = defaultdict(list)
    for code in shapes:
        members[code[:2]].append(code)
    members["24"].extend(members.pop("11", []))
    return dict(sorted(members.items()))


def arrange(day: date) -> tuple[dict[str, set[Cell]], dict[str, tuple[float, ...]], dict[str, Cell]]:
    shapes = shapes_on(day)
    members = membership(shapes)
    fips = list(members)
    mainland = [s for s in fips if s not in APART]
    outline = {
        s: make_valid(solid(make_valid(shapely.union_all([shapes[c] for c in members[s]]).buffer(0))).simplify(800))
        for s in fips
    }
    masks: dict[str, set[Cell]] = {}
    frames: dict[str, tuple[float, ...]] = {}
    for s in fips:
        if s in BLOB:
            c = outline[s].centroid
            masks[s] = blob(len(members[s]) * K)
            frames[s] = (math.sqrt(len(members[s]) * K * HEX / outline[s].area), c.x, c.y, 0, 0)
        else:
            masks[s], frames[s] = raster(outline[s], len(members[s]) * K)

    polys = [outline[s].simplify(3000) for s in mainland]
    warp = diffuse(polys, [len(members[s]) / outline[s].area for s in mainland], 512)
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

    def energy(s: str, off: Cell) -> float:
        p = spot(s, off)
        total = GRAVITY * float(np.sum((p - origin[s]) ** 2))
        for n in touch[s]:
            if n in placed:
                total += min(apart(s, off, n, placed[n]) - 2, 8) ** 1.5
                total += max(0.0, bend(s, n, p, spot(n, placed[n])) - BEND) ** 2 * TWIST
        return total

    rng = random.Random(7)
    steps = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)]
    for step in range(STEPS):
        temp = 3.0 * (0.01 / 3.0) ** (step / STEPS)
        s = rng.choice(mainland)
        old = placed[s]
        dx, dz = rng.choice(steps)
        off = (old[0] + dx, old[1] + dz)
        lift(s)
        if fits(s, off):
            delta = energy(s, off) - energy(s, old)
            if delta <= 0 or rng.random() < math.exp(-delta / temp):
                old = off
        put(s, old)

    xs = [axial(*cell)[0] for cell in owner]
    ys = [axial(*cell)[1] for cell in owner]
    left, right, bottom = min(xs), max(xs), max(ys)
    corner = {
        "02": (left + 10, bottom - 2),
        "15": (left + 30, bottom + 6),
        "66": (left + 44, bottom + 9),
        "69": (left + 44, bottom + 2),
        "60": (left + 50, bottom + 9),
        "72": (right - 22, bottom + 6),
        "78": (right - 14, bottom + 6),
    }
    for s in APART:
        if s in masks:
            target[s] = np.array(corner[s])
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
    day: date, masks: dict[str, set[Cell]], frames: dict[str, tuple[float, ...]], placed: dict[str, Cell]
) -> tuple[dict[Cell, str], list[str]]:
    shapes = shapes_on(day)
    district: dict[Cell, str] = {}
    problems = []
    for s, names in membership(shapes).items():
        cells = [(x + placed[s][0], z + placed[s][1]) for x, z in masks[s]]
        if len(names) == 1:
            district.update(dict.fromkeys(cells, names[0]))
            continue
        scale, ox, oy, dx, dy = frames[s]
        off = axial(*placed[s])
        matrix = [scale, 0, 0, -scale, -ox * scale + dx + off[0], oy * scale + dy + off[1]]
        polys = [make_valid(affinity.affine_transform(shapes[c], matrix).simplify(0.05)) for c in names]
        warp = diffuse(polys, [1 / p.area for p in polys], 128)
        seeds = np.array([[p.representative_point().x, p.representative_point().y] for p in polys])
        moved = warp(np.vstack([seeds, shapely.get_coordinates(polys)]))
        warped = [make_valid(p) for p in shapely.set_coordinates(np.array(polys, dtype=object), moved[len(seeds) :])]
        union = shapely.union_all(warped)
        uc = np.array(union.centroid.coords[0])
        mc = centroid(cells)
        fit = math.sqrt(len(cells) * HEX / union.area)
        warped = [
            affinity.affine_transform(p, [fit, 0, 0, fit, mc[0] - uc[0] * fit, mc[1] - uc[1] * fit]) for p in warped
        ]
        home = {c: (np.array(moved[i]) - uc) * fit + mc for i, c in enumerate(names)}
        points = np.array([axial(*cell) for cell in cells])
        reach = np.array([shapely.distance(warped[i], shapely.points(points)) for i in range(len(names))])
        split = names
        taken: dict[Cell, str] = {}
        for weight in (0.25, 0.5, 1.0, 2.0, 4.0):
            spots = dict(home)
            for _ in range(10):
                rows = []
                for i, c in enumerate(names):
                    rows.extend([reach[i] ** 2 * REACH + ((points - spots[c]) ** 2).sum(axis=1) * weight] * K)
                picked, columns = linear_sum_assignment(np.array(rows))
                taken = {cells[j]: names[i // K] for i, j in zip(picked, columns, strict=True)}
                spread: dict[str, list[tuple[float, float]]] = defaultdict(list)
                for cell, c in taken.items():
                    spread[c].append(axial(*cell))
                spots = {c: 0.25 * home[c] + 0.75 * np.mean(spread[c], axis=0) for c in names}
            split = fix(taken, names, dict.fromkeys(names, K))
            if not split:
                break
        if split:
            problems.append(f"{day.year} {POSTAL[s]} split {', '.join(split)}")
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


def drawn(district: dict[Cell, str]) -> dict:
    by_code: dict[str, list[Cell]] = defaultdict(list)
    for cell, code in district.items():
        by_code[code].append(cell)
    by_state: dict[str, list[Cell]] = defaultdict(list)
    for code, cells in by_code.items():
        by_state[code[:2]].extend(cells)
    world = union(district)
    shapes = {code: union(cells) for code, cells in sorted(by_code.items())}
    labels = {}
    for s, cells in sorted(by_state.items()):
        whole = union(cells)
        core = max(getattr(whole, "geoms", [whole]), key=lambda g: g.area)
        point = polylabel(core, 0.05)
        x, y = point.x, point.y
        if core.boundary.distance(point) < 1.6:
            a0, b0, a1, b1 = whole.bounds
            width = len(POSTAL[s]) * 1.25
            spots = [((a0 + a1) / 2, b1 + 1.5), ((a0 + a1) / 2, b0 - 1.5), (a1 + 1.6, point.y), (a0 - 1.6, point.y)]
            for x, y in spots:
                if not world.intersects(shapely.box(x - width / 2, y - 0.9, x + width / 2, y + 0.9)):
                    break
        labels[s] = [round(x, 2), round(y, 2), POSTAL[s]]
    return {
        "districts": {code: path(shape) for code, shape in shapes.items()},
        "bounds": {code: [round(value, 2) for value in shape.bounds] for code, shape in shapes.items()},
        "points": {
            code: [round(p.x, 2), round(p.y, 2)]
            for code, shape in shapes.items()
            for p in [polylabel(max(getattr(shape, "geoms", [shape]), key=lambda g: g.area), 0.05)]
        },
        "states": {s: path(union(cells)) for s, cells in sorted(by_state.items())},
        "labels": labels,
    }


def tiles(district: dict[Cell, str]) -> tuple[dict[Cell, str], list[str]]:
    fine: dict[str, list[Cell]] = defaultdict(list)
    for cell, code in district.items():
        fine[code[:2]].append(cell)
    need = {s: len({code for code in district.values() if code[:2] == s}) for s in fine}
    inland = [s for s in sorted(fine) if s not in APART]
    closed = union(cell for s in inland for cell in fine[s]).buffer(CLOSE).buffer(-CLOSE)
    closed = shapely.union_all([Polygon(part.exterior) for part in getattr(closed, "geoms", [closed])])
    total = sum(need[s] for s in inland)
    f = math.sqrt(closed.area / (total * HEX))
    a0, b0, a1, b1 = closed.bounds
    spots = [
        (x, z)
        for z in range(math.floor(b0 / (1.5 * f)) - 1, math.ceil(b1 / (1.5 * f)) + 2)
        for x in range(math.floor(a0 / (S3 * f) - z / 2) - 2, math.ceil(a1 / (S3 * f) - z / 2) + 3)
    ]
    cover = shapely.area(
        shapely.intersection([affinity.scale(hexagon(*c), f, f, origin=(0, 0)) for c in spots], closed)
    )
    rank = dict(zip(spots, cover, strict=True))
    keep = groups(sorted(spots, key=lambda c: -rank[c])[:total])[0]
    while len(keep) < total:
        keep.add(max({o for c in keep for o in near(*c) if o not in keep}, key=lambda o: rank.get(o, 0)))
    chosen = sorted(keep)
    points = np.array([axial(*c) for c in chosen]) * f
    rows = []
    for s in inland:
        mine = np.array([axial(*cell) for cell in fine[s]])
        closest = np.sqrt(((points[:, None, :] - mine[None, :, :]) ** 2).sum(axis=2)).min(axis=1) / f
        rows.extend([closest**2 + 0.05 * ((points - mine.mean(axis=0)) ** 2).sum(axis=1) / f**2] * need[s])
    picked, columns = linear_sum_assignment(np.array(rows))
    owners = [s for s in inland for _ in range(need[s])]
    taken = {chosen[j]: owners[i] for i, j in zip(picked, columns, strict=True)}
    split = fix(taken, inland, need)
    for s in APART:
        if s not in fine:
            continue
        middle = centroid(fine[s]) / f
        z = round(middle[1] / 1.5)
        mine = [(round(middle[0] / S3 - z / 2), z)]
        while len(mine) < need[s]:
            mine.append(min({o for c in mine for o in near(*c)} - set(mine), key=lambda o: (o[1], o[0])))
        taken.update(dict.fromkeys(mine, s))
    return {cell: POSTAL[s] for cell, s in taken.items()}, [f"tiles split {POSTAL[s]}" for s in split]


def text(cells: dict[Cell, str]) -> str:
    top = min(z for _, z in cells)
    shift = top - (top & 1)
    spots = {(x + (z - (z & 1)) // 2, z - shift): token for (x, z), token in cells.items()}
    first = min(q for q, _ in spots)
    lines = []
    for r in range(min(r for _, r in spots), max(r for _, r in spots) + 1):
        row = [spots.get((q, r), "..") for q in range(first, max(q for q, _ in spots) + 1)]
        while row and row[-1] == "..":
            row.pop()
        lines.append(("  " if r & 1 else "") + "  ".join(row))
    return "\n".join(lines) + "\n"


def build() -> str:
    report = []
    years: dict[str, int] = {}
    written: dict[str, int] = {}
    for start in DECADES:
        end = min(start + 8, LAST)
        masks, frames, placed = arrange(election_day(end))
        for year in range(start, end + 1, 2):
            district, problems = districts(election_day(year), masks, frames, placed)
            report.extend(problems)
            built = json.dumps(drawn(district), separators=(",", ":"))
            first = written.setdefault(built, year)
            if first == year:
                (OUT / f"us-cd-{year}.json").write_text(built + "\n")
            years[str(year)] = first
            if year == end:
                cells, problems = tiles(district)
                report.extend(problems)
                (GRIDS / f"us-cd-{start}.txt").write_text(text(cells))
            report.append(f"us-cd {year}: {len(district) // K} districts, layout {first}")
    for stale in OUT.glob("us-cd-[0-9]*.json"):
        if int(stale.stem[-4:]) not in written.values():
            stale.unlink()
    (OUT / "us-cd.json").write_text(json.dumps(years, separators=(",", ":")) + "\n")
    return "\n".join(report)
