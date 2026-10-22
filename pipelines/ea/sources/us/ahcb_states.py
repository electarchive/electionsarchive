from datetime import date
from pathlib import Path
from uuid import uuid5

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.sources.us.ahcb import COUNTIES, HOME
from ea.sources.us.ahcb import SOURCE as AHCB
from ea.store import connect

SOURCE = "us.ahcb_states"
VERSION = "1"
CENSUS_FROM = date(2001, 1, 1)
CLOSE = 0.0003
SAME = 2e-6

CUTS = """
select distinct d from (
  select lower(g.valid * k.valid) d from area_parent k join area_geometry g on g.area_id = k.area_id and g.valid && k.valid
  where k.parent_id = %(state)s and not lower_inf(g.valid * k.valid)
  union
  select upper(g.valid * k.valid) from area_parent k join area_geometry g on g.area_id = k.area_id and g.valid && k.valid
  where k.parent_id = %(state)s and not upper_inf(g.valid * k.valid)
) x where d < %(from)s order by d
"""
UNION = """
select encode(st_asewkb(g), 'hex'), encode(st_asewkb(st_pointonsurface(g)), 'hex') from (
  select st_multi(st_collectionextract(st_makevalid(
           st_buffer(st_buffer(st_makevalid(st_unaryunion(st_collect(g.geom))), %(close)s), -%(close)s)), 3)) g
  from area_parent k join area_geometry g on g.area_id = k.area_id
  where k.parent_id = %(state)s and k.valid @> %(day)s::date and g.valid @> %(day)s::date
) u where g is not null and not st_isempty(g)
"""
SIMILAR = """
select st_area(st_symdifference(a, b)) <= %(same)s * greatest(st_area(a), st_area(b))
from (select st_geomfromewkb(decode(%(a)s, 'hex')) a, st_geomfromewkb(decode(%(b)s, 'hex')) b) x
"""


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="US state boundaries in force on each date before 2001, dissolved from the Newberry Atlas of Historical County Boundaries, with the Census shapes from 2001",
            polity="US",
        )
        cs.manifest["scope"] = {"polity": "US", "replaces": {"area_geometry": ["us.census"]}}
        newberry = cs.source("Newberry Library", "Atlas of Historical County Boundaries")
        cs.add(
            "source",
            id=newberry,
            kind="academic",
            publisher="Newberry Library",
            title="Atlas of Historical County Boundaries",
            url=HOME,
            license="CC BY-NC-SA 2.5 (as shipped with the 2016 files)",
            license_url="https://creativecommons.org/licenses/by-nc-sa/2.5/",
            attribution="Atlas of Historical County Boundaries, Dr. William M. Scholl Center for American History and Culture, Newberry Library",
        )
        for url in (HOME, COUNTIES):
            cs.artifact(raw.cached(AHCB, url), newberry)
        states = conn.execute(
            """select a.id, c.code, encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(g.label_point), 'hex'), g.source_id
               from area a join area_code c on c.area_id = a.id
               join area_geometry g on g.area_id = a.id and lower_inf(g.valid) and upper_inf(g.valid)
               join changeset s on s.id = g.changeset_id and s.pipeline = 'us.census'
               where a.scheme_code = 'us.state' order by c.code"""
        ).fetchall()
        starts: set[date] = set()
        for state, code, geom, point, source in states:
            cuts = [day for (day,) in conn.execute(CUTS, {"state": state, "from": CENSUS_FROM}).fetchall()]
            runs: list[list[object]] = []
            for lo, hi in zip(cuts, [*cuts[1:], CENSUS_FROM], strict=True):
                found = conn.execute(UNION, {"state": state, "day": lo, "close": CLOSE}).fetchone()
                if found is None:
                    continue
                shape, label = found
                if (
                    runs
                    and runs[-1][1] == lo
                    and conn.execute(SIMILAR, {"a": runs[-1][2], "b": shape, "same": SAME}).fetchone()[0]
                ):
                    runs[-1][1] = hi
                else:
                    runs.append([lo, hi, shape, label])
            for lo, hi, shape, label in runs:
                cs.add(
                    "area_geometry",
                    area_id=state,
                    valid_from=lo,
                    valid_to=hi,
                    geom=shape,
                    label_point=label,
                    quality="academic",
                    source_id=newberry,
                )
                starts.add(lo)
            if runs:
                cs.add(
                    "area_geometry",
                    area_id=state,
                    valid_from=CENSUS_FROM,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=source,
                )
            print(code, len(cuts), "cuts", len(runs), "shapes", flush=True)
        days = sorted(starts | {CENSUS_FROM})
        for index, day in enumerate(days):
            cs.add(
                "boundary_set",
                id=uuid5(NAMESPACE, f"boundary_set\x1fus.state@{day.isoformat()}"),
                scheme_code="us.state",
                vintage=day.isoformat(),
                valid_from=day if index else None,
                valid_to=days[index + 1] if index + 1 < len(days) else None,
                as_of=day,
            )
        print(len(days), "boundary sets")
        return cs.write()
