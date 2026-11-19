"""Read the filled outlines of a schematic SVG map into polygons in the drawing's own coordinates."""

import re
from xml.etree import ElementTree

import numpy as np
import shapely
import shapely.geometry

SVG = "{http://www.w3.org/2000/svg}"
_TOKEN = re.compile(r"[MmLlHhVvCcSsZz]|-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?", re.IGNORECASE)
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Z": 0}
_STEPS = 12


def _bezier(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> list[np.ndarray]:
    t = np.linspace(0, 1, _STEPS + 1)[1:, None]
    return list((1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t**2 * p2 + t**3 * p3)


def path_rings(d: str) -> list[list[tuple[float, float]]]:
    """Flatten an SVG path (absolute and relative M, L, H, V, C, S, Z) into rings of points."""
    tokens = _TOKEN.findall(d)
    rings: list[list[np.ndarray]] = []
    ring: list[np.ndarray] = []
    pos = np.zeros(2)
    start = np.zeros(2)
    last_ctrl: np.ndarray | None = None
    command = ""
    i = 0
    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]
            i += 1
            if command in "Zz":
                if ring:
                    rings.append(ring)
                ring = []
                pos = start
                continue
        rel = command.islower()
        n = _ARGS[command.upper()]
        a = [float(x) for x in tokens[i : i + n]]
        i += n
        c = command.upper()
        base = pos if rel else np.zeros(2)
        ctrl: np.ndarray | None = None
        if c == "M":
            if ring:
                rings.append(ring)
            pos = base + np.array(a)
            start = pos
            ring = [pos]
            command = "l" if rel else "L"
        elif c == "L":
            pos = base + np.array(a)
            ring.append(pos)
        elif c == "H":
            pos = np.array([(pos[0] if rel else 0) + a[0], pos[1]])
            ring.append(pos)
        elif c == "V":
            pos = np.array([pos[0], (pos[1] if rel else 0) + a[0]])
            ring.append(pos)
        elif c == "C":
            p1, p2, p3 = (base + np.array(a[k : k + 2]) for k in (0, 2, 4))
            ring += _bezier(pos, p1, p2, p3)
            pos, ctrl = p3, p2
        elif c == "S":
            p1 = 2 * pos - last_ctrl if last_ctrl is not None else pos
            p2, p3 = (base + np.array(a[k : k + 2]) for k in (0, 2))
            ring += _bezier(pos, p1, p2, p3)
            pos, ctrl = p3, p2
        last_ctrl = ctrl
    if ring:
        rings.append(ring)
    return [[(float(x), float(y)) for x, y in r] for r in rings if len(r) > 2]


def filled_polygons(svg: str) -> list[tuple[str, shapely.Polygon]]:
    """The filled paths of the drawing, each with its fill color, in document order."""
    out = []
    for el in ElementTree.fromstring(svg).iter(f"{SVG}path"):
        m = re.search(r"fill:(#[0-9A-Fa-f]{6})", el.get("style", ""))
        if not m:
            continue
        rings = path_rings(el.get("d", ""))
        shape = shapely.make_valid(shapely.union_all([shapely.Polygon(r) for r in rings]))
        out.append((m.group(1).upper(), shape))
    return out
