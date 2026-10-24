import io
import itertools
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb import os
from ea.store import DATA, connect

SOURCE = "gb.os_1832"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
STATUTES = (
    "Parliamentary Boundaries Act 1832 (2 & 3 Will. 4 c. 64, https://www.legislation.gov.uk/ukpga/Will4/2-3/64/enacted), "
    "Representation of the People Act 1832 (2 & 3 Will. 4 c. 45, schedules A–F), Representation of the People "
    "(Scotland) Act 1832 (2 & 3 Will. 4 c. 65), Representation of the People (Ireland) Act 1832 (2 & 3 Will. 4 c. 88), "
    "Sudbury Disfranchisement Act 1844 (7 & 8 Vict. c. 53), St Albans Disfranchisement Act 1852 (15 & 16 Vict. c. 9), "
    "and Birkenhead Enfranchisement Act 1861 (24 & 25 Vict. c. 112)"
)
NOTES = (
    "Synthetic 1832–1868 constituencies. Boroughs that kept their seats in 1868 reuse gb.os's 1868 shapes; the "
    "boroughs disfranchised before or in 1868 are drawn within 3 km of their GeoNames town, and each county division is "
    "its historic county less its boroughs, split between its divisions by compass direction rather than by the "
    f"hundreds the statutes list. Statutes: {STATUTES}."
)
LOST = {
    "Arundel": "Sussex",
    "Ashburton": "Devon",
    "Dartmouth": "Devon",
    "Great Yarmouth": "Norfolk",
    "Honiton": "Devon",
    "Lancaster": "Lancashire",
    "Lyme Regis": "Dorset",
    "Reigate": "Surrey",
    "St Albans": "Hertfordshire",
    "Sudbury": "Suffolk",
    "Thetford": "Norfolk",
    "Totnes": "Devon",
    "Wells": "Somerset",
}
COUNTIES = os.COUNTIES | {"Peeblesshire": ("Peebleshire",)}
RIDINGS = {
    "North Riding of Yorkshire": "North Riding of Yorkshire",
    "East Riding of Yorkshire": "East Riding of Yorkshire",
    "Northern West Riding of Yorkshire": "West Riding of Yorkshire",
    "Eastern West Riding of Yorkshire": "West Riding of Yorkshire",
    "Southern West Riding of Yorkshire": "West Riding of Yorkshire",
}
WEST = {"Northern West Riding of Yorkshire": "North", "Southern West Riding of Yorkshire": "South"}
END = date(1868, 11, 17)


def fetch() -> None:
    os.fetch()


def parse() -> None:
    with zipfile.ZipFile(raw.cached(os.SOURCE, os.HISTORIC).path) as archive:
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{os.LAYER}.shp")),
            shx=io.BytesIO(archive.read(f"{os.LAYER}.shx")),
            dbf=io.BytesIO(archive.read(f"{os.LAYER}.dbf")),
        )
        parts = [
            (str(record["NAME"]), os.HISTORIC, 27700, json.dumps(shape.__geo_interface__))
            for record, shape in zip(reader.records(), reader.shapes(), strict=True)
        ]
    irish = {code: name for name, code in os.IRISH.items()}
    for url, field, srid in ((os.ULSTER, "CountyName", 4326), (os.IRELAND, "ENG_NAME_VALUE", 2157)):
        for feature in json.loads(raw.cached(os.SOURCE, url).path.read_bytes())["features"]:
            parts.append((irish[feature["properties"][field].upper()], url, srid, json.dumps(feature["geometry"])))
    wanted = {re.sub(r" (Boroughs|Burghs|City|Borough)$", "", name).lower() for name in LOST}
    places = []
    for url in os.GEONAMES:
        with zipfile.ZipFile(raw.cached(os.SOURCE, url).path) as archive:
            member = f"{url.rsplit('/', 1)[1].removesuffix('.zip')}.txt"
            for line in archive.read(member).decode().splitlines():
                fields = line.split("\t")
                if fields[6] in ("P", "A"):
                    for name in {name.lower() for name in (fields[1], fields[2], *fields[3].split(","))} & wanted:
                        places.append((name, float(fields[5]), float(fields[4]), int(fields[14] or 0), fields[6]))
    homes = {name: county for county, names in os.BOROUGHS.items() for name in names}
    reused = pl.read_parquet(raw.parsed(os.SOURCE) / "areas.parquet").filter(pl.col("layer") == "pcon")
    with connect() as conn:
        seats = conn.execute(
            """select c.code, n.text, lower(c.valid), least(upper(c.valid), %s)
               from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.is_preferred
               where a.scheme_code = 'gb.pcon' and c.code like '1832:%%'""",
            [END],
        ).fetchall()
        names1868 = dict(
            conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   where a.scheme_code = 'gb.pcon' and c.code ~ '^[0-9]+$'"""
            ).fetchall()
        )
        cuts = sorted({start for _, _, start, _ in seats} | {end for *_, end in seats})
        periods = list(itertools.pairwise(cuts))
        boroughs = {name: (homes[name], old) for old, name in names1868.items() if name in homes}
        live = {
            code: [index for index, (low, high) in enumerate(periods) if start <= low and high <= end]
            for code, _, start, end in seats
        }
        towns, lost, divisions = [], [], []
        for code, name, _, _ in seats:
            if name in LOST:
                lost.append((code, name, LOST[name], name.lower()))
            elif name in boroughs:
                towns.append((code, boroughs[name][1], boroughs[name][0]))
            for index in live[code]:
                if name in LOST or name in boroughs:
                    continue
                if name in RIDINGS or name == "West Riding of Yorkshire":
                    divisions.append((index, code, RIDINGS.get(name, name), *os.DIRECTIONS[WEST.get(name, "Mid")]))
                    continue
                prefix = next((word for word in os.DIRECTIONS if name.startswith(f"{word} ")), None)
                base = name.removeprefix(f"{prefix} ") if prefix else name
                divisions.append((index, code, "+".join(COUNTIES.get(base, (base,))), *os.DIRECTIONS[prefix or "Mid"]))

        conn.execute("create temp table source_part (part text, url text, srid int, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy source_part from stdin") as copy:
            for row in parts:
                copy.write_row(row)
        conn.execute(
            """create temp table part on commit drop as
               select part, min(url) as url, st_multi(st_collectionextract(st_makevalid(st_union(
                        st_simplifypreservetopology(st_makevalid(st_transform(
                          st_setsrid(st_force2d(st_geomfromgeojson(geojson)), srid), 3035)), 20))), 3)) as geom
               from source_part group by part"""
        )
        conn.execute(
            """insert into part
               select 'Isle of Wight', min(url), st_multi(st_union(piece.geom))
               from part, lateral st_dump(geom) piece
               where part = 'Hampshire'
                 and st_y(st_transform(st_centroid(piece.geom), 4326)) < 50.77
                 and st_x(st_transform(st_centroid(piece.geom), 4326)) between -1.6 and -1.05"""
        )
        conn.execute(
            """update part set geom = st_multi(st_collectionextract(st_difference(
                 geom, (select geom from part where part = 'Isle of Wight')), 3))
               where part = 'Hampshire'"""
        )
        known = {row[0] for row in conn.execute("select part from part").fetchall()}
        if (
            missing := {member for _, _, county, *_ in divisions for member in county.split("+")}
            - known
            - set(RIDINGS.values())
            - {"Yorkshire"}
        ):
            raise ValueError(f"cannot place {sorted(missing)}")
        conn.execute("create temp table old (code text, geom geometry, source text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy old from stdin") as copy:
            for code, geom, source in reused.select("code", "geom", "source").rows():
                copy.write_row((code, geom, source))
        conn.execute("create temp table town (code text, part text, geom geometry, source text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy town (code, part, source) from stdin") as copy:
            for code, old, part in towns:
                copy.write_row((code, part, old))
        conn.execute(
            """update town set geom = st_transform(st_geomfromewkb(decode(o.geom, 'hex')), 3035), source = o.source
               from old o where o.code = town.source"""
        )
        if unmatched := conn.execute("select code from town where geom is null").fetchall():
            raise ValueError(f"no 1868 shape for {unmatched}")
        conn.execute("create temp table place (name text, x float, y float, population int, class text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy place from stdin") as copy:
            for row in places:
                copy.write_row(row)
        conn.execute("create temp table lost (code text, name text, part text, search text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy lost from stdin") as copy:
            for row in lost:
                copy.write_row(row)
        conn.execute(
            """create temp table located on commit drop as
               select distinct on (b.code) b.code, b.part, st_transform(st_setsrid(st_point(p.x, p.y), 4326), 3035) as point
               from lost b join place p on p.name = b.search
               join part c on c.part = b.part
                 and st_dwithin(c.geom, st_transform(st_setsrid(st_point(p.x, p.y), 4326), 3035), 2000)
               order by b.code, p.class = 'P' desc, p.population desc"""
        )
        if unplaced := conn.execute("select name from lost where code not in (select code from located)").fetchall():
            raise ValueError(f"no GeoNames place for {sorted(name for (name,) in unplaced)}")
        conn.execute(
            """insert into town
               select l.code, l.part,
                      st_multi(st_collectionextract(st_difference(st_intersection(st_intersection(cell.geom,
                        st_buffer(l.point, %s)), c.geom),
                        coalesce((select st_union(t.geom) from town t where t.part = l.part),
                                 'SRID=3035;POLYGON EMPTY'::geometry)), 3)), %s
               from located l join part c on c.part = l.part,
               lateral (select v.geom from st_dump(st_voronoipolygons((select st_collect(point) from located))) v
                 where st_contains(v.geom, l.point)) cell""",
            [os.RADIUS, os.GEONAMES[0]],
        )
        conn.execute("create temp table span (code text, period int) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy span from stdin") as copy:
            for code, indexes in live.items():
                for index in indexes:
                    copy.write_row((code, index))
        conn.execute(
            "create temp table division (period int, code text, county text, dx float, dy float) on commit drop"
        )
        with conn.cursor() as cursor, cursor.copy("copy division from stdin") as copy:
            for row in divisions:
                copy.write_row(row)
        conn.execute(
            """create temp table county on commit drop as
               select s.period, d.county, st_difference(st_union(c.geom),
                        coalesce((select st_union(t.geom) from town t join span u on u.code = t.code
                                  where u.period = s.period and t.part = any(string_to_array(d.county, '+'))),
                                 'SRID=3035;POLYGON EMPTY'::geometry)) as geom
               from (select distinct period from division) s
               cross join (select distinct county from division where county not like '%%Riding%%'
                           union select 'Yorkshire') d
               join part c on c.part = any(string_to_array(d.county, '+'))
               group by s.period, d.county"""
        )
        conn.execute("create temp table riding (name text, zone text, dx float, dy float) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy riding from stdin") as copy:
            for name, zone in RIDINGS.items():
                copy.write_row((name, zone, *os.RIDINGS[name]))
        conn.execute(
            """insert into county
               select c.period, r.zone, st_union(st_intersection(c.geom, (
                        select v.geom from st_dump(st_voronoipolygons(
                          (select st_collect(st_translate(st_centroid(c.geom),
                                   o.dx * 0.3 * sqrt(st_area(c.geom)), o.dy * 0.3 * sqrt(st_area(c.geom))))
                           from riding o), 0, st_expand(c.geom, 100000))) v
                        where st_contains(v.geom, st_translate(st_centroid(c.geom),
                          r.dx * 0.3 * sqrt(st_area(c.geom)), r.dy * 0.3 * sqrt(st_area(c.geom)))))))
               from county c cross join riding r
               where c.county = 'Yorkshire'
               group by c.period, r.zone"""
        )
        conn.execute(
            """create temp table seed on commit drop as
               select d.period, d.code, d.county, st_translate(st_centroid(c.geom),
                        d.dx * 0.3 * sqrt(st_area(c.geom)), d.dy * 0.3 * sqrt(st_area(c.geom))) as point
               from division d join county c using (period, county)"""
        )
        rows = conn.execute(
            """with shapes as (
                 select u.period, t.code, t.source, t.geom from town t join span u using (code)
                 union all
                 select s.period, s.code, null, case
                   when (select count(*) from seed o where o.period = s.period and o.county = s.county) = 1 then c.geom
                   else st_intersection(c.geom, (
                     select v.geom from st_dump(st_voronoipolygons(
                       (select st_collect(point) from seed o where o.period = s.period and o.county = s.county),
                       0, st_expand(c.geom, 100000))) v
                     where st_contains(v.geom, s.point))) end
                 from seed s join county c using (period, county)
               ),
               cleaned as (
                 select period, code, source, key,
                        st_setsrid(st_coveragesimplify(geom, %s) over (partition by period), 3035) as geom
                 from (select period, code, source, key,
                              st_setsrid(st_coverageclean(geom) over (partition by period), 3035) as geom
                       from (select period, code, source, md5(st_asewkb(geom)) as key, st_multi(st_collectionextract(st_makevalid(geom), 3)) as geom
                             from shapes) valid) clean
               )
               select period, code, source,
                      coalesce(key <> lag(key) over (partition by code order by period), true),
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from cleaned order by code, period""",
            [os.TOLERANCE],
        ).fetchall()
        urls = dict(conn.execute("select part, url from part").fetchall())
    counties = {code: county.split("+")[0] for _, code, county, *_ in divisions}
    merged: list[tuple[str, date, date, str, str, str]] = []
    for period, code, source, changed, geom, point in rows:
        low, high = periods[period]
        if not changed and merged[-1][0] == code and merged[-1][2] == low:
            merged[-1] = (code, merged[-1][1], high, geom, merged[-1][4], merged[-1][5])
            continue
        home = urls.get(counties.get(code, ""), os.HISTORIC)
        merged.append((code, low, high, geom, point, source or ("statute" if home == os.HISTORIC else home)))
    frame = pl.DataFrame(
        merged,
        schema={
            "code": pl.String,
            "valid_from": pl.Date,
            "valid_to": pl.Date,
            "geom": pl.String,
            "label_point": pl.String,
            "source": pl.String,
        },
        orient="row",
    )
    print(len(periods), "periods", frame.height, "shapes", frame.group_by("source").len().rows())
    if set(frame["code"]) != {code for code, *_ in seats} or frame["geom"].null_count():
        raise ValueError(f"{frame['code'].n_unique()} shaped of {len(seats)} constituencies")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Synthetic shapes for the 1832–1868 constituencies: boroughs kept in 1868 reuse their 1868 shapes, the "
                "boroughs disfranchised by 1868 are drawn around their GeoNames towns, and county divisions are split "
                "from Ordnance Survey's historic counties and OSNI's and Tailte Éireann's Irish counties as the "
                "Parliamentary Boundaries Act 1832 and the Reform Acts of 1832 assign them"
            ),
            polity="GB",
        )
        sources: dict[str, UUID] = {}
        for url, (publisher, title, page, license, license_url, attribution, kind) in os.SOURCES.items():
            sources[url] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[url],
                kind=kind,
                publisher=publisher,
                title=title,
                url=page,
                license=license,
                license_url=license_url,
                attribution=attribution,
            )
            for artifact in os.GEONAMES if url == os.GEONAMES[0] else (url,):
                cs.artifact(raw.cached(os.SOURCE, artifact), sources[url])
        publisher, title = "Ordnance Survey", "Parliamentary Boundaries Act 1832 county divisions: historic counties"
        sources["statute"] = cs.source(publisher, title)
        cs.add(
            "source",
            id=sources["statute"],
            kind="official_body",
            publisher=publisher,
            title=title,
            url="https://www.legislation.gov.uk/ukpga/Will4/2-3/64/enacted",
            license=os.OGL[0],
            license_url=os.OGL[1],
            attribution="Contains OS data © Crown copyright and database right",
            notes=NOTES,
        )
        cs.artifact(raw.cached(os.SOURCE, os.HISTORIC), sources["statute"])
        areas = dict(
            conn.execute(
                """select c.code, a.id from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'gb.pcon' and c.code like '1832:%'"""
            ).fetchall()
        )
        for code, start, end, geom, point, source in frame.rows():
            cs.add(
                "area_geometry",
                area_id=areas[code],
                valid_from=start,
                valid_from_precision="day",
                valid_to=end,
                valid_to_precision="day",
                geom=geom,
                label_point=point,
                quality="digitized" if source == os.RCAHMW else "synthetic",
                source_id=sources[source],
            )
        existing = conn.execute(
            "select id from boundary_set where scheme_code = 'gb.pcon' and vintage = '1832'"
        ).fetchone()
        cs.add(
            "boundary_set",
            **({"id": existing[0]} if existing else {}),
            scheme_code="gb.pcon",
            vintage="1832",
            valid_from=date(1832, 12, 8),
            valid_to=END,
            as_of=date(1832, 12, 8),
        )
        return cs.write()
