from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ca_council_dedupe"
VERSION = "1"


def parse() -> None:
    return None


def emit() -> Path:
    with connect() as conn:
        rows = conn.execute(
            """with x as (
                 select c.area_id, c.code, regexp_replace(c.code, ':(area|district)-[^:]+$', '') as place,
                        regexp_replace(c.code, '^.*:(area|district)-', '') as number, c.code ~ ':area-[^:]+$' as is_area
                 from area_code c join area a on a.id = c.area_id
                 where a.scheme_code = 'us.local_district' and c.code ~ '^06:[^:]+:council:(area|district)-[^:]+$'
                   and not exists (select from entity_merge m where m.entity_id = c.area_id)
               )
               select d.area_id, k.area_id, d.code, k.code,
                      (select u.id from reporting_unit u where u.scheme_code = 'us.local_district' and u.code = d.code
                         and not exists (select from entity_merge m where m.entity_id = u.id)),
                      (select u.id from reporting_unit u where u.scheme_code = 'us.local_district' and u.code = k.code
                         and not exists (select from entity_merge m where m.entity_id = u.id))
               from x d join x k on k.place = d.place and k.number = d.number and not k.is_area
               where d.is_area
                 and not exists (
                   select from area_geometry g where g.area_id = d.area_id
                     and not exists (select from area_geometry h where h.area_id = k.area_id and h.valid = g.valid
                                       and st_equals(h.geom, g.geom)))
               order by d.code"""
        ).fetchall()
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Merge the California council districts that us.ceda minted as Area N into the same districts already held as District N",
            polity="US-CA",
        )
        for area, survivor, code, kept, unit, kept_unit in rows:
            print(code, "->", kept)
            cs.merge(area, survivor)
            if unit is not None and kept_unit is not None:
                cs.merge(unit, kept_unit)
        print(f"{len(rows)} districts")
        return cs.write()
