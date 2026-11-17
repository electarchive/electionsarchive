"""Norway's current county and municipality shapes from Kartverket, and the code changes of the 2018, 2020, and 2024
reforms (and every smaller change since 2009) from Statistics Norway's classification service."""

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

import shapely
import shapely.geometry

from ea import raw

KARTVERKET = "no.kartverket"
KLASS = "no.ssb.klass"
API = "https://api.kartverket.no/kommuneinfo/v1"
CHANGES = "https://data.ssb.no/api/klass/v1/classifications/{id}/changes?from=2009-01-01&to=2026-01-01"
CLASSIFICATIONS = {"no.fylke": 104, "no.kommune": 131}
WORKERS = 8
# The date the current counties and municipalities took effect: the last reform.
VINTAGE = date(2024, 1, 1)


def listing(level: str) -> str:
    return f"{API}/{level}"


def outline(level: str, code: str) -> str:
    return f"{API}/{level}/{code}/omrade?utkoordsys=4326"


def changes_url(scheme: str) -> str:
    return CHANGES.format(id=CLASSIFICATIONS[scheme])


def fetch() -> None:
    for level, key in (("fylker", "fylkesnummer"), ("kommuner", "kommunenummer")):
        artifact = raw.fetch(KARTVERKET, listing(level), suffix=".json")
        codes = [str(item[key]) for item in json.loads(artifact.path.read_bytes())]
        with ThreadPoolExecutor(WORKERS) as pool:
            list(pool.map(lambda code, level=level: raw.fetch(KARTVERKET, outline(level, code), suffix=".json"), codes))
        print(f"{KARTVERKET}: {len(codes)} {level}", flush=True)
    for scheme in CLASSIFICATIONS:
        raw.fetch(KLASS, changes_url(scheme), suffix=".json")


@dataclass
class Shape:
    code: str
    name: str
    geom: shapely.Geometry
    artifact: raw.Artifact


def _multipolygon(doc: dict[str, object]) -> shapely.Geometry:
    shape = shapely.make_valid(shapely.geometry.shape(doc))
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    parts = [piece for part in parts for piece in shapely.get_parts(part)]
    return shapely.set_srid(shapely.MultiPolygon(parts), 4326)


def shapes(scheme: str) -> list[Shape]:
    """Every current area of a scheme with its Kartverket outline."""
    level, code_key, name_key = (
        ("fylker", "fylkesnummer", "fylkesnavn")
        if scheme == "no.fylke"
        else ("kommuner", "kommunenummer", "kommunenavn")
    )
    found = []
    for item in json.loads(raw.cached(KARTVERKET, listing(level)).path.read_bytes()):
        code = str(item[code_key])
        artifact = raw.cached(KARTVERKET, outline(level, code))
        doc = json.loads(artifact.path.read_bytes())
        if str(doc[code_key]) != code:
            raise ValueError(f"{outline(level, code)} answered for {doc[code_key]}")
        found.append(Shape(code, str(item[name_key]), _multipolygon(doc["omrade"]), artifact))
    return sorted(found, key=lambda shape: shape.code)


@dataclass
class Change:
    scheme: str
    day: date
    old: list[tuple[str, str]]
    new: list[tuple[str, str]]

    @property
    def kind(self) -> str:
        if len(self.old) == 1 and len(self.new) == 1:
            return "recoded"
        if len(self.new) == 1:
            return "merged"
        if len(self.old) == 1:
            return "split"
        return "boundary_changed"


def changes(scheme: str) -> list[Change]:
    """The code changes of a scheme, grouped into one event for each connected set of old and new codes on a date.

    A change that only renames an area under the same code is dropped: it changes no code."""
    artifact = raw.cached(KLASS, changes_url(scheme))
    rows = json.loads(artifact.path.read_bytes())["codeChanges"]
    by_day: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_day[row["changeOccurred"]].append(row)
    found = []
    for day, items in sorted(by_day.items()):
        parent: dict[tuple[str, str], tuple[str, str]] = {}

        def root(node: tuple[str, str]) -> tuple[str, str]:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for row in items:
            parent[root(("old", row["oldCode"]))] = root(("new", row["newCode"]))
        names: dict[tuple[str, str], str] = {}
        for row in items:
            names[("old", row["oldCode"])] = row["oldName"]
            names[("new", row["newCode"])] = row["newName"]
        groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        for node in list(parent):
            groups[root(node)].append(node)
        for nodes in groups.values():
            old = sorted((c, names[("old", c)]) for side, c in nodes if side == "old")
            new = sorted((c, names[("new", c)]) for side, c in nodes if side == "new")
            if [c for c, _ in old] == [c for c, _ in new] and len(old) == 1:
                continue
            found.append(Change(scheme, date.fromisoformat(day), old, new))
    return sorted(found, key=lambda change: (change.day, change.old))
