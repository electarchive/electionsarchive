import json
import zipfile
from datetime import date
from uuid import UUID

import shapefile
from psycopg import Connection, Error
from psycopg.types.range import Range

from ea import raw
from ea.store import DATA

SOURCE = "display"
LAND = "https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip"
LAKES = "https://data.hydrosheds.org/file/hydrolakes/HydroLAKES_polys_v10_shp.zip"
LAKE_KM2 = 1_000
CHUNK = 200
POINTS = 150_000
BATCH = 20_000
COARSE = 20_000
SCALE = 0.0002
DRIFT = 0.005
STRADDLE_KIDS = 100
ATTRIBUTION = "Coastlines © OpenStreetMap contributors (ODbL); lakes: HydroLAKES (CC BY 4.0, Lehner and Grill 2013)"


def version() -> str:
    return f"{raw.cached(SOURCE, LAND).sha256[:12]}-{raw.cached(SOURCE, LAKES).sha256[:12]}-{LAKE_KM2}"


def lakes(bbox: tuple[float, float, float, float]) -> list[dict]:
    raw.fetch(SOURCE, LAKES, suffix=".zip")
    return [
        item.shape.__geo_interface__
        for item in _shapes(LAKES, "HydroLAKES_polys_v10").iterShapeRecords(fields=["Lake_area"], bbox=bbox)
        if item.record["Lake_area"] >= LAKE_KM2
    ]


def fetch() -> None:
    for url in (LAND, LAKES):
        raw.fetch(SOURCE, url, suffix=".zip")


def _shapes(url: str, stem: str):
    folder = DATA / "raw" / SOURCE / raw.cached(SOURCE, url).sha256[:12]
    if not folder.exists():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            archive.extractall(folder)
    (path,) = folder.rglob(f"{stem}.shp")
    return shapefile.Reader(str(path), encoding="latin-1")


def _load(conn: Connection) -> None:
    conn.execute("drop index if exists display.display_land_idx")
    conn.execute("drop index if exists display.display_lake_idx")
    conn.execute("truncate display.land, display.lake")
    conn.execute("create temp table staged (shape text not null) on commit drop")
    for table, url, stem, wanted in (
        ("land", LAND, "land_polygons", lambda record: True),
        ("lake", LAKES, "HydroLAKES_polys_v10", lambda record: record["Lake_area"] >= LAKE_KM2),
    ):
        reader = _shapes(url, stem)
        batch: list[str] = []
        for item in reader.iterShapeRecords():
            if not wanted(item.record.as_dict()):
                continue
            batch.append(json.dumps(item.shape.__geo_interface__))
            if len(batch) == BATCH:
                _flush(conn, table, batch)
                batch = []
        _flush(conn, table, batch)
    conn.execute("create index display_land_idx on display.land using gist (geom)")
    conn.execute("create index display_lake_idx on display.lake using gist (geom)")
    conn.execute("analyze display.land")
    conn.execute("analyze display.lake")


def _flush(conn: Connection, table: str, batch: list[str]) -> None:
    with conn.cursor().copy("copy staged from stdin") as copy:
        for shape in batch:
            copy.write_row((shape,))
    if table == "land":
        conn.execute(
            "insert into display.land select (ST_Dump(ST_SetSRID(ST_GeomFromGeoJSON(shape), 4326))).geom from staged"
        )
    else:
        conn.execute(
            "insert into display.lake select ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(shape), 4326)) from staged"
        )
    conn.execute("truncate staged")


def ensure(conn: Connection) -> str:
    current = version()
    conn.execute("create schema if not exists display")
    conn.execute("create table if not exists display.meta (mask_version text not null)")
    conn.execute("create table if not exists display.land (geom geometry(Polygon, 4326) not null)")
    conn.execute("create table if not exists display.lake (geom geometry(MultiPolygon, 4326) not null)")
    conn.execute(
        """create table if not exists display.clipped (
               area_id uuid not null,
               valid daterange not null,
               geom_hash text not null,
               mask_version text not null,
               geom geometry(MultiPolygon, 4326),
               primary key (area_id, valid)
           )"""
    )
    loaded = conn.execute("select mask_version from display.meta").fetchone()
    if loaded is None or loaded[0] != current:
        _load(conn)
        conn.execute("delete from display.meta")
        conn.execute("insert into display.meta values (%s)", [current])
    return current


def refresh(conn: Connection, scheme_code: str, as_of: date, mask: str) -> int:
    todo = conn.execute(
        """select g.area_id, g.valid, ST_NPoints(g.geom)
           from area a join area_geometry g on g.area_id = a.id
           left join display.clipped c on c.area_id = g.area_id and c.valid = g.valid
           where a.scheme_code = %s and g.valid @> %s::date
             and (c.area_id is null or c.mask_version <> %s or c.geom_hash <> md5(ST_AsBinary(g.geom)))""",
        [scheme_code, as_of, mask],
    ).fetchall()
    part: list[tuple[UUID, Range[date], int]] = []
    points = 0
    for index, row in enumerate(todo):
        part.append(row)
        points += row[2]
        if len(part) < CHUNK and points < POINTS and index + 1 < len(todo):
            continue
        conn.execute(
            """insert into display.clipped (area_id, valid, geom_hash, mask_version, geom)
               select s.area_id, s.valid, s.h, %(mask)s,
                      case when s.land then null else (
                          select ST_Multi(ST_CollectionExtract(ST_Collect(p.g), 3))
                          from ST_Dump(ST_MakeValid(s.geom)) d
                          cross join lateral (
                              select ST_Difference(
                                  ST_Intersection(
                                      d.geom,
                                      (select ST_UnaryUnion(ST_Collect(c.geom)) from display.land c where c.geom && d.geom)
                                  ),
                                  coalesce(
                                      (select ST_UnaryUnion(ST_Collect(l.geom)) from display.lake l where l.geom && d.geom),
                                      ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326)
                                  )
                              ) as g
                          ) p
                      ) end
               from (
                   select g.area_id, g.valid, g.geom, md5(ST_AsBinary(g.geom)) as h,
                          not exists (select 1 from display.lake l where l.geom && g.geom and ST_Intersects(l.geom, g.geom))
                          and exists (select 1 from display.land c where c.geom && g.geom and ST_Covers(c.geom, g.geom)) as land
                   from unnest(%(areas)s::uuid[], %(valids)s::daterange[]) u(area_id, valid)
                   join area_geometry g on g.area_id = u.area_id and g.valid = u.valid
               ) s
               on conflict (area_id, valid) do update
                   set geom_hash = excluded.geom_hash, mask_version = excluded.mask_version, geom = excluded.geom""",
            {"mask": mask, "areas": [area for area, _, _ in part], "valids": [valid for _, valid, _ in part]},
        )
        conn.execute(
            """update display.clipped set geom = null
               where area_id = any(%s) and geom is not null and ST_IsEmpty(geom)""",
            [[area for area, _, _ in part]],
        )
        conn.commit()
        part = []
        points = 0
    return len(todo)


def coarsen(conn: Connection) -> int:
    conn.execute("create schema if not exists display")
    conn.execute(
        """create table if not exists display.coarse (
               area_id uuid not null,
               valid daterange not null,
               geom_hash text not null,
               geom geometry(MultiPolygon, 4326),
               primary key (area_id, valid)
           )"""
    )
    conn.execute(
        """delete from display.coarse k where not exists (
               select from area_geometry g
               where g.area_id = k.area_id and g.valid = k.valid and md5(ST_AsBinary(g.geom)) = k.geom_hash
           )"""
    )
    todo = conn.execute(
        """select g.area_id, g.valid from area_geometry g
           where ST_NPoints(g.geom) > %s
             and not exists (select from display.coarse k where k.area_id = g.area_id and k.valid = g.valid)""",
        [COARSE],
    ).fetchall()
    for area_id, valid in todo:
        conn.execute(
            """insert into display.coarse (area_id, valid, geom_hash, geom)
               select g.area_id, g.valid, md5(ST_AsBinary(g.geom)),
                      case when ST_NPoints(s.geom) * 2 < ST_NPoints(g.geom)
                           and abs(ST_Area(s.geom) / ST_Area(g.geom) - 1) < %s then s.geom end
               from area_geometry g
               cross join lateral (
                   select ST_Multi(ST_CollectionExtract(ST_MakeValid(
                       ST_Simplify(g.geom, sqrt(ST_Area(g.geom)) * %s, false), 'method=structure keepcollapsed=false'
                   ), 3)) as geom
               ) s
               where g.area_id = %s and g.valid = %s
               on conflict (area_id, valid) do nothing""",
            [DRIFT, SCALE, area_id, valid],
        )
        conn.commit()
    return len(todo)


GEOMETRY_MARK = """select greatest(
    (select changeset_id from area_geometry order by changeset_id desc limit 1),
    (select superseded_by from area_geometry_history where superseded_by > '00000000-0000-0000-0000-000000000000' order by superseded_by desc limit 1)
)"""


def straddle(conn: Connection) -> int:
    conn.execute("create schema if not exists display")
    conn.execute(
        """create table if not exists display.straddle (
               area_id uuid not null,
               day date not null,
               kid_id uuid not null,
               straddles boolean not null,
               primary key (area_id, day, kid_id)
           )"""
    )
    conn.execute("create table if not exists display.straddle_mark (mark uuid not null)")
    row = conn.execute(GEOMETRY_MARK).fetchone()
    mark = row[0] if row else None
    held = conn.execute("select mark from display.straddle_mark").fetchone()
    if held is None:
        conn.execute("truncate display.straddle")
    elif held[0] != mark:
        conn.execute(
            """with changed as (
                   select area_id from area_geometry where changeset_id > %(held)s
                   union
                   select area_id from area_geometry_history where superseded_by > %(held)s
               )
               delete from display.straddle s
               where s.area_id in (select area_id from changed) or s.kid_id in (select area_id from changed)""",
            {"held": held[0]},
        )
    conn.execute("delete from display.straddle_mark")
    if mark is not None:
        conn.execute("insert into display.straddle_mark values (%s)", [mark])
    conn.commit()
    schemes = [
        code
        for (code,) in conn.execute(
            "select code from scheme where kind = 'electoral_district' order by code"
        ).fetchall()
    ]
    done = 0
    for scheme in schemes:
        todo = conn.execute(
            """with grp as (
                   select c.district_area_id as area_id, coalesce(e.date_from, 'infinity'::date) as day, rs.k
                   from contest c
                   join area a on a.id = c.district_area_id and a.scheme_code = %(scheme)s
                   join election e on e.id = c.election_id
                   join result_set rs on rs.contest_id = c.id and rs.status <> 'withdrawn'
               ),
               kids as (
                   select g.area_id, g.day, u.area_k as k from grp g join result_rollup_unit u on u.result_set_k = g.k
                   union
                   select g.area_id, g.day, r.area_k from grp g join result_rollup_sum r on r.result_set_k = g.k
               ),
               big as (select area_id, day from kids group by area_id, day having count(*) > %(least)s)
               select k.area_id, k.day, array_agg(a.id)
               from kids k
               join big using (area_id, day)
               join area a on a.k = k.k
               where a.id <> k.area_id
                 and not exists (
                     select from display.straddle s where s.area_id = k.area_id and s.day = k.day and s.kid_id = a.id
                 )
               group by k.area_id, k.day""",
            {"scheme": scheme, "least": STRADDLE_KIDS},
        ).fetchall()
        for area_id, day, kids in todo:
            try:
                with conn.transaction():
                    conn.execute(
                        """with me as materialized (
                               select geom from area_geometry where area_id = %(area)s
                               order by valid @> %(day)s::date desc, upper(valid) desc nulls first limit 1
                           )
                           insert into display.straddle (area_id, day, kid_id, straddles)
                           select %(area)s, %(day)s, k.id, coalesce((
                               select not ST_Covers(me.geom, g.geom)
                                   and ST_Area(ST_Intersection(g.geom, me.geom)) < 0.95 * ST_Area(g.geom)
                               from me cross join lateral (
                                   select g.geom from area_geometry g where g.area_id = k.id
                                   order by g.valid @> %(day)s::date desc, upper(g.valid) desc nulls first limit 1
                               ) g
                           ), false)
                           from unnest(%(kids)s::uuid[]) k(id)
                           on conflict do nothing""",
                        {"area": area_id, "day": day, "kids": kids},
                    )
                done += len(kids)
            except Error as error:
                print(f"straddle {scheme} {area_id} {day}: {error}".splitlines()[0], flush=True)
            conn.commit()
        print(f"straddle {scheme}: {len(todo)} districts and days", flush=True)
    return done
