"""Reads OpenHistoricalMap's daily planet for the historical basemap (`ea basemap --historical`).

License handling, decided by Firat on 2026-11-11. Each feature carries its own `license` tag and is kept or dropped on
that tag alone:

- `CC0`, `CC0-1.0`, and similar are kept (class `cc0`).
- `public domain`, `PDM`, `copyright expired`, and `dl-de/zero` are kept (class `public_domain`).
- `CC BY` of any version, spelled out or abbreviated and with trailing credit text, and attribution-only equivalents
  such as `dl-de/by-2-0` are kept (class `cc_by`), unless the value also names SA, NC, ND, or IGO; the
  `license`, `source`, and `attribution` tags of each such feature become one credit, which the output lists.
- A feature with no `license` tag is kept as the planet's default CC0 (class `default_cc0`).
- ODbL, CC BY-SA, GPL, CC BY-NC, CC BY-ND, and every other value are dropped. A value that lists several licenses
  separated by semicolons is kept only when all of them are kept.
- A relation also needs every member way to pass, and it carries the credits of its member ways.

Dates follow `start_date` and `end_date`. A year-only or year-month start reads as the first day of that period. A
year-only or year-month end covers its whole period, so `end_date` is stored as the first day after it (an end of
`1972` becomes `1973-01-01`) and the half-open rule below still holds. Unpadded months and days (`2010-6-8`) and
negative years (read as BCE) are accepted. A feature with no `start_date`, with free text or any other unparseable
date, or with an end that is not after its start is dropped, and the counts report these as `dropped_undated` and
`dropped_date`. A feature is valid on date D when `start <= D < end`, and a missing end means open. The
tile properties `s` and `e` are days since 1970-01-01, so the comparison needs no date parsing in the app. Syria and
Hatay State overlap from 1938-09-02 to 1938-09-07, and the half-open rule keeps both on those days.
"""

import hashlib
import json
import re
import sqlite3
import struct
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from shapely import LineString, make_valid, unary_union
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize

RAIL_KINDS = frozenset({"rail", "narrow_gauge", "light_rail", "subway", "tram", "monorail", "funicular", "preserved"})
DATE = re.compile(r"(-?\d{1,6})(?:-(\d{1,2})(?:-(\d{1,2}))?)?")
CC0 = re.compile(r"(cc0|cc-0|cc-zero)(-\d(\.\d)?)?(-universal)?")
PUBLIC = re.compile(
    r"(public-domain|pd|pdm|pdm-\d(\.\d)?|cc-pdm|dl-de/zero(-\d-\d)?|copyright-expired|cc0-/-public-domain)"
)
ATTRIBUTION = re.compile(r"((cc-by|creative-commons-(by|attribution))\b.*|dl-de[/-]by([-/]\d[-.]\d)?)")
RESTRICTED = frozenset({"sa", "nc", "nd", "sharealike", "noncommercial", "noderivatives", "igo"})
MONTHS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


@dataclass(frozen=True)
class Credit:
    license: str
    source: str
    attribution: str

    @property
    def id(self) -> str:
        return hashlib.sha1(
            json.dumps([self.license, self.source, self.attribution]).encode(), usedforsecurity=False
        ).hexdigest()[:8]


@dataclass
class Tally:
    counts: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    credits: Counter[tuple[str, str, Credit]] = field(default_factory=Counter)
    rejected: Counter[str] = field(default_factory=Counter)


def days(year: int, month: int, day: int) -> int:
    year -= month <= 2
    era = year // 400
    of_year = year - era * 400
    of_day = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    return era * 146097 + of_year * 365 + of_year // 4 - of_year // 100 + of_day - 719468


def normalize(value: str | None, end: bool = False) -> tuple[str, int] | None:
    match = DATE.fullmatch(value.strip()) if value else None
    if match is None:
        return None
    year, month, day = int(match[1]), int(match[2] or 1), int(match[3] or 1)
    astronomical = year + 1 if year < 0 else year
    leap = astronomical % 4 == 0 and (astronomical % 100 != 0 or astronomical % 400 == 0)
    if not 1 <= month <= 12 or not 1 <= day <= MONTHS[month - 1] + (month == 2 and leap):
        return None
    if end and match[3] is None:
        month, year = (month + 1, year) if match[2] is not None and month < 12 else (1, year + 1 if year != -1 else 1)
        astronomical = year + 1 if year < 0 else year
    sign = "-" if year < 0 else ""
    return f"{sign}{abs(year):04d}-{month:02d}-{day:02d}", days(astronomical, month, day)


def stamp(tags: dict[str, str]) -> dict[str, str | int] | None:
    start = normalize(tags.get("start_date"))
    if start is None:
        return None
    props: dict[str, str | int] = {"start_date": start[0], "s": start[1]}
    if "end_date" in tags:
        end = normalize(tags["end_date"], end=True)
        if end is None or end[1] <= start[1]:
            return None
        props |= {"end_date": end[0], "e": end[1]}
    return props


def valid_on(props: dict[str, str | int], day: int) -> bool:
    return int(props["s"]) <= day < int(props.get("e", day + 1))


def classify_one(value: str) -> str | None:
    key = re.sub(r"[\s_]+", "-", value.strip().lower())
    if CC0.fullmatch(key):
        return "cc0"
    if PUBLIC.fullmatch(key):
        return "public_domain"
    if ATTRIBUTION.fullmatch(key) and RESTRICTED.isdisjoint(re.split(r"[^a-z0-9]+", key)):
        return "cc_by"
    return None


def classify(value: str | None) -> str | None:
    if value is None or not value.strip():
        return "default_cc0"
    kinds = {classify_one(part) for part in value.split(";")}
    if None in kinds:
        return None
    return next(kind for kind in ("cc_by", "public_domain", "cc0") if kind in kinds)


def vet(tags: dict[str, str], tally: Tally) -> tuple[str, Credit | None] | None:
    value = tags.get("license")
    kind = classify(value)
    if kind is None:
        tally.rejected[value or ""] += 1
        return None
    if kind == "cc_by":
        return kind, Credit(value or "", tags.get("source", ""), tags.get("attribution", ""))
    return kind, None


def relations(
    path: Path, pick: Callable[[int, dict[str, str]], bool]
) -> dict[int, tuple[dict[str, str], list[tuple[int, str]]]]:
    import osmium
    import osmium.filter
    import osmium.osm

    found = {}
    for relation in osmium.FileProcessor(str(path), osmium.osm.RELATION):
        if not isinstance(relation, osmium.osm.Relation):
            continue
        tags = {tag.k: tag.v for tag in relation.tags}
        if pick(relation.id, tags):
            found[relation.id] = (tags, [(m.ref, m.role) for m in relation.members if m.type == "w"])
    return found


def ways(
    path: Path,
    work: Path,
    keep: set[int],
    rail: Callable[[list[tuple[float, float]], dict[str, str]], None],
    store: sqlite3.Connection,
) -> None:
    """Streams the planet once with node locations in an on-disk index, calls `rail` for each railway line, and
    saves the member ways of the wanted relations in `store`."""
    import osmium
    import osmium.filter
    import osmium.osm

    store.execute("create table if not exists ways (id integer primary key, points blob, tags text)")
    index = work / "nodes.idx"
    index.unlink(missing_ok=True)
    processor = (
        osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations(f"sparse_file_array,{index}")
        .with_filter(osmium.filter.EmptyTagFilter().enable_for(osmium.osm.NODE))
    )
    pending = 0
    try:
        for item in processor:
            if not isinstance(item, osmium.osm.Way):
                continue
            wanted = item.id in keep
            if item.tags.get("railway") not in RAIL_KINDS and not wanted:
                continue
            try:
                points = [(node.lon, node.lat) for node in item.nodes]
            except osmium.InvalidLocationError:
                continue
            tags = {tag.k: tag.v for tag in item.tags}
            if tags.get("railway") in RAIL_KINDS and len(points) > 1:
                rail(points, tags)
            if wanted:
                flat = [value for point in points for value in point]
                keys = ("license", "source", "attribution")
                store.execute(
                    "insert or replace into ways values (?, ?, ?)",
                    (
                        item.id,
                        struct.pack(f"<{len(flat)}d", *flat),
                        json.dumps({k: tags[k] for k in keys if k in tags}),
                    ),
                )
                pending += 1
                if pending % 20_000 == 0:
                    store.commit()
    finally:
        index.unlink(missing_ok=True)
    store.commit()


def assemble(
    store: sqlite3.Connection, members: list[tuple[int, str]], tally: Tally
) -> tuple[BaseGeometry | None, list[Credit] | None]:
    """Joins a boundary relation's member ways into a polygon. The geometry is None when a way is missing or no
    ring closes, and the credits are None when a member fails the license rule."""
    outer, inner, credits = [], [], []
    for ref, role in members:
        row = store.execute("select points, tags from ways where id = ?", (ref,)).fetchone()
        if row is None:
            return None, []
        vetted = vet(json.loads(row[1]), tally)
        if vetted is None:
            return None, None
        if vetted[1] is not None:
            credits.append(vetted[1])
        flat = struct.unpack(f"<{len(row[0]) // 8}d", row[0])
        if len(flat) >= 4:
            (inner if role == "inner" else outer).append(LineString(list(zip(flat[::2], flat[1::2], strict=True))))
    shell = unary_union(list(polygonize(unary_union(outer)))) if outer else None
    if shell is None or shell.is_empty:
        return None, []
    if inner:
        shell = shell.difference(unary_union(list(polygonize(unary_union(inner)))))
    return make_valid(shell), credits
