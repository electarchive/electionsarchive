"""Outlines and lineage for comuni abolished from 1991 to 2021.

ISTAT publishes the boundaries of every comune at the 1991 census and as of 1 January of most years since 2002. An
abolished comune takes its outline from the latest of those files dated before its abolition, and the outline holds
until that day. The outlines of one file are simplified together, at the 10 m tolerance it.istat uses for the
current ones, so neighbors keep a shared border. ISTAT's file of administrative changes since 1991 gives each
abolition's date and its successor: a new comune formed by merger (`CS`), or an existing comune that absorbed it
(`AQES`). The 2022 and later changes are it.istat's.
"""

import csv
import io
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime

import numpy
import polars as pl
import pyproj
import shapefile
import shapely
from shapely.geometry import shape

from ea import raw
from ea.sources.it import istat

BASE = "https://www.istat.it/storage/cartografia/confini_amministrativi/non_generalizzati"
# The census of 20 October 1991, then 1 January of each year the abolitions need.
VINTAGES = {
    date(1991, 10, 20): f"{BASE}/Limiti1991.zip",
    date(2002, 1, 1): f"{BASE}/Limiti01012002.zip",
    date(2010, 1, 1): f"{BASE}/Limiti01012010.zip",
    **{date(year, 1, 1): f"{BASE}/Limiti0101{year}.zip" for year in range(2013, 2021)},
}
TITLES = {
    day: "Confini delle unità amministrative a fini statistici al censimento 1991"
    if day.year == 1991
    else f"Confini delle unità amministrative a fini statistici al 1° gennaio {day.year}"
    for day in VINTAGES
}
PAGE = istat.PAGES[istat.LIMITS[2025]]
TOLERANCE = 10
UNTIL = istat.FIRST
MONTHS = {
    month: number
    for number, month in enumerate(
        (
            "gennaio",
            "febbraio",
            "marzo",
            "aprile",
            "maggio",
            "giugno",
            "luglio",
            "agosto",
            "settembre",
            "ottobre",
            "novembre",
            "dicembre",
        ),
        start=1,
    )
}
# Some rows of the change file carry no date, but their law gives the day it took effect.
IN_FORCE = re.compile(r"(?i)in vigore dal (\d{1,2})°? (\w+) (\d{4})")
TO_WGS84 = pyproj.Transformer.from_crs(32632, 4326, always_xy=True)


def fetch(source: str) -> None:
    for url in VINTAGES.values():
        raw.fetch(source, url, suffix=".zip")


def changes() -> list[list[str]]:
    with zipfile.ZipFile(raw.cached(istat.SOURCE, istat.VARIATIONS).path) as archive:
        text = archive.read(istat.VARIATIONS_CSV).decode("cp1252")
    return [[cell.strip() for cell in row] for row in list(csv.reader(io.StringIO(text), delimiter=";"))[1:]]


def effective(row: list[str]) -> tuple[date, str]:
    """The day a change took effect and its precision: the row's date, the day its law came into force, or the
    year alone."""
    if row[12]:
        return datetime.strptime(row[12], "%d/%m/%Y").date(), "day"
    found = IN_FORCE.search(row[10])
    if found and found[2].lower() in MONTHS:
        return date(int(found[3]), MONTHS[found[2].lower()], int(found[1])), "day"
    return date(int(row[0]), 1, 1), "year"


def endings(rows: list[list[str]], codes: set[str]) -> pl.DataFrame:
    """When each of the codes was abolished, and the day its outline holds until: the day itself, or the end of the
    year when the change file gives only the year."""
    found = []
    for row in rows:
        if row[1] == "ES" and row[4] in codes:
            day, precision = effective(row)
            found.append((row[4], row[5], day, precision, day if precision == "day" else date(day.year + 1, 1, 1)))
    frame = pl.DataFrame(
        found,
        schema={"code": pl.String, "name": pl.String, "until": pl.Date, "precision": pl.String, "outline_to": pl.Date},
        orient="row",
    )
    if frame["code"].is_duplicated().any():
        raise ValueError(f"comuni abolished twice: {frame.filter(pl.col('code').is_duplicated())['code'].to_list()}")
    return frame


def lineage(rows: list[list[str]], codes: set[str]) -> pl.DataFrame:
    """The mergers and absorptions before 2022 that abolished any of the codes: one row per event and member."""
    abolished = {row[4] for row in rows if row[1] == "ES"}
    events: dict[tuple[str, str, date], dict[str, object]] = {}
    for row in rows:
        # A comune formed from part of another (`CECS` on the other's side) is not a merger.
        if row[1] not in ("CS", "AQES") or row[8] not in abolished:
            continue
        day, precision = effective(row)
        if day >= UNTIL:
            continue
        kind = "merged" if row[1] == "CS" else "absorbed"
        event = events.setdefault(
            (kind, row[4], day),
            {"precision": precision, "name": row[5], "from": {}},
        )
        if event["precision"] != precision:
            raise ValueError(f"{row[4]} {day}: the change file dates one event at two precisions")
        sources = event["from"]
        assert isinstance(sources, dict)
        sources[row[8]] = row[9]
    found = []
    for (kind, code, day), event in sorted(events.items(), key=lambda item: (item[0][2], item[0][1], item[0][0])):
        predecessors = event["from"]
        assert isinstance(predecessors, dict)
        if not set(predecessors) & codes:
            continue
        name = str(event["name"])
        if kind == "merged" and name in predecessors.values():
            # A merger whose new comune keeps one predecessor's name: the others merged into it, under a new code.
            kind = "absorbed"
            others = [label for label in predecessors.values() if label != name]
            notes = f"{joined(others)} merged into {name}, which took the new code {code}."
        elif kind == "merged":
            notes = f"{joined(list(predecessors.values()))} merged to form {name}."
        else:
            notes = f"{name} absorbed {joined(list(predecessors.values()))}."
        for member, role in [*((other, "from") for other in sorted(predecessors)), (code, "to")]:
            found.append((kind, day, event["precision"], code, notes, member, role))
    return pl.DataFrame(
        found,
        schema={
            "kind": pl.String,
            "date": pl.Date,
            "precision": pl.String,
            "successor": pl.String,
            "notes": pl.String,
            "code": pl.String,
            "role": pl.String,
        },
        orient="row",
    )


def joined(names: list[str]) -> str:
    return " and ".join(names) if len(names) < 3 else f"{', '.join(names[:-1])}, and {names[-1]}"


def _layer(archive: zipfile.ZipFile) -> shapefile.Reader:
    stems = [
        name.removesuffix(".shp")
        for name in archive.namelist()
        if re.search(r"(^|/)Com[^/]*_WGS84\.shp$", name, flags=re.IGNORECASE)
    ]
    if len(stems) != 1:
        raise ValueError(f"{archive.filename}: expected one comuni layer, found {stems}")
    stem = stems[0]
    if b"UTM_Zone_32N" not in archive.read(f"{stem}.prj") or b"WGS_1984" not in archive.read(f"{stem}.prj"):
        raise ValueError(f"{archive.filename}: the comuni layer is not in WGS 84 / UTM zone 32N")
    return shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{stem}.shp")),
        shx=io.BytesIO(archive.read(f"{stem}.shx")),
        dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
        encoding="utf-8",
    )


def _wgs84(geometry: shapely.Geometry) -> shapely.Geometry:
    return shapely.transform(geometry, lambda xy: numpy.column_stack(TO_WGS84.transform(xy[:, 0], xy[:, 1])))


def _polygonal(geometry: shapely.Geometry) -> shapely.Geometry:
    parts = shapely.get_parts(shapely.make_valid(geometry))
    return shapely.multipolygons([part for part in parts if shapely.get_type_id(part) == 3])


def _hex(geometry: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(geometry, 4326), hex=True, include_srid=True)


def outlines(source: str, rows: list[list[str]], until: dict[str, date]) -> pl.DataFrame:
    """Each code's outline from the latest boundary file dated before `until`, under the code it held then (a move
    to another province, `AP`, gives a comune a new code)."""
    earlier: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row[1] == "AP" and row[8]:
            earlier[row[8]].add(row[4])

    def names(code: str) -> set[str]:
        found, queue = {code}, [code]
        while queue:
            for old in earlier.get(queue.pop(), set()) - found:
                found.add(old)
                queue.append(old)
        return found

    held: dict[date, set[str]] = {}
    for vintage, url in VINTAGES.items():
        with zipfile.ZipFile(raw.cached(source, url).path) as archive:
            reader = _layer(archive)
            held[vintage] = {
                str(record["PRO_COM_T"]) for record in reader.iterRecords(fields=["PRO_COM_T"]) if record is not None
            }
    chosen: dict[date, dict[str, str]] = defaultdict(dict)
    for code, end in until.items():
        options = [
            (vintage, old)
            for vintage in VINTAGES
            if vintage < end
            for old in sorted(names(code))
            if old in held[vintage]
        ]
        if options:
            vintage, old = max(options)
            chosen[vintage][old] = code
    found = []
    for vintage, wanted in sorted(chosen.items()):
        with zipfile.ZipFile(raw.cached(source, VINTAGES[vintage]).path) as archive:
            reader = _layer(archive)
            codes, shapes = [], []
            for record, item in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                if record is None or item is None:
                    raise ValueError(f"{VINTAGES[vintage]}: a comune without a record or a shape")
                if str(record["PRO_COM_T"]) in wanted:
                    codes.append(wanted[str(record["PRO_COM_T"])])
                    shapes.append(_polygonal(shapely.force_2d(shape(item.__geo_interface__))))
        if sorted(codes) != sorted(wanted.values()):
            raise ValueError(f"{VINTAGES[vintage]}: comuni listed more than once or missing among {sorted(wanted)}")
        simplified = shapely.coverage_simplify(numpy.array(shapes), TOLERANCE)
        for code, geometry in zip(codes, (_polygonal(item) for item in simplified), strict=True):
            if geometry.is_empty:
                raise ValueError(f"{code}: empty outline in {VINTAGES[vintage]}")
            found.append(
                (
                    code,
                    vintage,
                    _hex(_wgs84(geometry)),
                    _hex(_wgs84(shapely.point_on_surface(geometry))),
                )
            )
    return pl.DataFrame(
        found,
        schema={"code": pl.String, "vintage": pl.Date, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
