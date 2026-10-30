import io
import json
import math
import sqlite3
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np
import shapefile
import shapely
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

FEATURES_LIMIT = 20_000
VERTICES_LIMIT = 5_000_000
FIELDS_LIMIT = 200
SAMPLES = 3
WGS84 = CRS.from_epsg(4326)
ENVELOPES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}

Feature = tuple[dict[str, str], shapely.Geometry]


class Unreadable(ValueError):
    pass


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip()
    return str(value).strip()


def _crs(value: object, where: str) -> CRS:
    try:
        return CRS.from_user_input(value)
    except CRSError as error:
        raise Unreadable(f"the {where} names a coordinate system the archive cannot read") from error


def _geojson(path: Path) -> tuple[list[tuple[dict[str, object], dict[str, object] | None]], CRS]:
    try:
        data = json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Unreadable("the GeoJSON file is not valid JSON") from error
    if not isinstance(data, dict):
        raise Unreadable("the GeoJSON file holds no features")
    named = (data.get("crs") or {}).get("properties", {}).get("name") if isinstance(data.get("crs"), dict) else None
    crs = WGS84 if named is None else _crs(named, "GeoJSON file")
    items = data.get("features") if data.get("type") == "FeatureCollection" else [data]
    if not isinstance(items, list):
        raise Unreadable("the GeoJSON file holds no features")
    features = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "Feature":
            raise Unreadable("the GeoJSON file holds something other than features")
        properties = item.get("properties")
        features.append((properties if isinstance(properties, dict) else {}, item.get("geometry")))
    return features, crs


def _shp(path: Path) -> tuple[list[tuple[dict[str, object], dict[str, object] | None]], CRS | None]:
    with zipfile.ZipFile(path) as archive:
        names = {name.lower(): name for name in archive.namelist() if not name.startswith("__MACOSX/")}
        stems = sorted(name[:-4] for name in names if name.endswith(".shp"))
        if len(stems) != 1:
            raise Unreadable("the zip holds more than one shapefile; upload one layer at a time")
        stem = stems[0]
        for suffix in (".shx", ".dbf"):
            if stem + suffix not in names:
                raise Unreadable(f"the shapefile is missing its {suffix} file")
        prj = names.get(f"{stem}.prj")
        crs = None if prj is None else _crs(archive.read(prj).decode("utf-8", "replace"), "shapefile's .prj file")
        cpg = names.get(f"{stem}.cpg")
        encoding = "utf-8" if cpg is None else archive.read(cpg).decode("ascii", "replace").strip() or "utf-8"
        try:
            reader = shapefile.Reader(
                shp=io.BytesIO(archive.read(names[f"{stem}.shp"])),
                shx=io.BytesIO(archive.read(names[f"{stem}.shx"])),
                dbf=io.BytesIO(archive.read(names[f"{stem}.dbf"])),
                encoding=encoding,
                encodingErrors="replace",
            )
        except (shapefile.ShapefileException, LookupError) as error:
            raise Unreadable("the shapefile could not be read") from error
        if len(reader) > FEATURES_LIMIT:
            raise Unreadable(f"the file has more than {FEATURES_LIMIT:,} features")
        features: list[tuple[dict[str, object], dict[str, object] | None]] = []
        for record, shape in zip(reader.records(), reader.shapes(), strict=True):
            geometry = None if shape is None or record is None else dict(shape.__geo_interface__)
            features.append(({} if record is None else dict(record.as_dict()), geometry))
        return features, crs


def _wkb(blob: bytes) -> bytes:
    if len(blob) < 8 or blob[:2] != b"GP":
        raise Unreadable("the GeoPackage holds a geometry it cannot read")
    flags = blob[3]
    envelope = ENVELOPES.get((flags >> 1) & 0b111)
    if envelope is None:
        raise Unreadable("the GeoPackage holds a geometry it cannot read")
    return blob[8 + envelope :]


def _gpkg(path: Path) -> tuple[list[tuple[dict[str, object], dict[str, object] | None]], CRS]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        layers = db.execute(
            """select g.table_name, g.column_name, s.organization, s.organization_coordsys_id, s.definition
               from gpkg_geometry_columns g join gpkg_spatial_ref_sys s on s.srs_id = g.srs_id
               where lower(g.geometry_type_name) in ('polygon', 'multipolygon', 'geometry', 'surface', 'multisurface')"""
        ).fetchall()
        if len(layers) != 1:
            raise Unreadable(
                "the GeoPackage has no polygon layer"
                if not layers
                else "the GeoPackage has more than one polygon layer; upload one layer at a time"
            )
        table, column, organization, code, definition = layers[0]
        crs = (
            CRS.from_epsg(code)
            if str(organization).lower() == "epsg" and code > 0
            else _crs(definition, "GeoPackage's layer")
        )
        quoted = '"' + str(table).replace('"', '""') + '"'
        columns = [row[1] for row in db.execute(f"pragma table_info({quoted})").fetchall()]
        if db.execute(f"select count(*) from {quoted}").fetchone()[0] > FEATURES_LIMIT:
            raise Unreadable(f"the file has more than {FEATURES_LIMIT:,} features")
        features = []
        for row in db.execute(f"select * from {quoted}"):
            values = dict(zip(columns, row, strict=True))
            blob = values.pop(column)
            geometry = None if blob is None else shapely.geometry.mapping(shapely.from_wkb(_wkb(blob)))
            features.append((values, geometry))
        return features, crs
    except (sqlite3.Error, shapely.errors.ShapelyError, struct.error) as error:
        raise Unreadable("the GeoPackage could not be read") from error
    finally:
        db.close()


def polygonal(geometry: shapely.Geometry) -> shapely.Geometry | None:
    if not geometry.is_valid:
        geometry = shapely.make_valid(geometry)
    parts = [
        polygon
        for part in shapely.get_parts(geometry)
        for polygon in shapely.get_parts(part)
        if isinstance(polygon, shapely.Polygon) and not polygon.is_empty
    ]
    if not parts:
        return None
    return shapely.MultiPolygon(parts)


def read(path: Path) -> list[Feature]:
    with path.open("rb") as file:
        head = file.read(16)
    try:
        if head.startswith(b"SQLite format 3\x00"):
            raw, crs = _gpkg(path)
        elif head.startswith(b"PK"):
            raw, crs = _shp(path)
        else:
            raw, crs = _geojson(path)
    except zipfile.BadZipFile as error:
        raise Unreadable("the zip file is damaged") from error
    if len(raw) > FEATURES_LIMIT:
        raise Unreadable(f"the file has more than {FEATURES_LIMIT:,} features")
    shapes: list[shapely.Geometry | None] = []
    for _, geometry in raw:
        try:
            shapes.append(None if geometry is None else shapely.geometry.shape(geometry))
        except (shapely.errors.ShapelyError, KeyError, TypeError, ValueError) as error:
            raise Unreadable("a feature's geometry could not be read") from error
    vertices = sum(shapely.get_num_coordinates(shape) for shape in shapes if shape is not None)
    if vertices > VERTICES_LIMIT:
        raise Unreadable(f"the file has more than {VERTICES_LIMIT:,} vertices")
    present = [shape for shape in shapes if shape is not None and not shape.is_empty]
    if crs is None:
        bounds = shapely.total_bounds(present) if present else np.zeros(4)
        if not (-180 <= bounds[0] and bounds[2] <= 180 and -90 <= bounds[1] and bounds[3] <= 90):
            raise Unreadable("the shapefile has no .prj file, so its coordinate system is unknown")
        crs = WGS84
    if not crs.equals(WGS84, ignore_axis_order=True):
        transformer = Transformer.from_crs(crs, WGS84, always_xy=True)

        def project(coordinates: np.ndarray) -> np.ndarray:
            x, y = transformer.transform(coordinates[:, 0], coordinates[:, 1])
            return np.column_stack([x, y])

        shapes = [None if shape is None else shapely.transform(shapely.force_2d(shape), project) for shape in shapes]
    features: list[Feature] = []
    for (properties, _), shape in zip(raw, shapes, strict=True):
        polygons = None if shape is None or shape.is_empty else polygonal(shapely.force_2d(shape))
        if polygons is None:
            continue
        bounds = polygons.bounds
        if not all(math.isfinite(value) for value in bounds) or bounds[0] < -180 or bounds[2] > 180:
            raise Unreadable("a feature lies outside longitude and latitude after reprojection")
        features.append(({str(key): _text(value) for key, value in properties.items()}, polygons))
    return features


def describe(path: Path, folder: Path) -> None:
    features = read(path)
    fields: dict[str, list[str]] = {}
    for properties, _ in features:
        for key, value in properties.items():
            if key not in fields and len(fields) >= FIELDS_LIMIT:
                continue
            fields.setdefault(key, []).append(value)
    bounds = shapely.total_bounds([geometry for _, geometry in features]) if features else None
    tolerance = 0.0 if bounds is None else math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1]) / 3_000
    preview = shapely.set_precision(
        shapely.simplify([geometry for _, geometry in features], tolerance, preserve_topology=True), 1e-6
    )
    (folder / "shapes.json").write_text(
        json.dumps(
            {
                "count": len(features),
                "bbox": None if bounds is None else [round(float(value), 6) for value in bounds],
                "fields": [
                    {
                        "name": name,
                        "samples": list(dict.fromkeys(value for value in values if value))[:SAMPLES],
                        "unique": len(values) == len(features) and all(values) and len(set(values)) == len(values),
                    }
                    for name, values in fields.items()
                ],
            },
            ensure_ascii=False,
        )
    )
    with (folder / "preview.geojson").open("w") as file:
        file.write('{"type":"FeatureCollection","features":[')
        for n, ((properties, _), geometry) in enumerate(zip(features, preview, strict=True)):
            if n:
                file.write(",")
            file.write(
                f'{{"type":"Feature","id":{n},"properties":{json.dumps(properties, ensure_ascii=False)},'
                f'"geometry":{shapely.to_geojson(geometry)}}}'
            )
        file.write("]}")


if __name__ == "__main__":
    try:
        describe(Path(sys.argv[1]), Path(sys.argv[2]))
    except Unreadable as error:
        print(error, file=sys.stderr)
        sys.exit(3)
    except MemoryError:
        print("the file is too large to read as boundaries", file=sys.stderr)
        sys.exit(3)
