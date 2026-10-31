import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ea.sources.us.place_units import STATE_FIPS
from ea.store import ROOT, connect

OUT = ROOT / "apps" / "web" / "src" / "lib" / "server" / "hex"
GRIDS = Path(__file__).parent / "hexgrid"
INSETS = {"PR": "72", "VI": "78", "GU": "66", "AS": "60", "MP": "69"}
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
