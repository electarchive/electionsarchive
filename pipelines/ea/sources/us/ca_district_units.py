from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ca_district_units"
VERSION = "2"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California school district contests: the precincts a county's results list for the contest are members of the district",
            polity="US",
        )
        rows = conn.execute(
            """select distinct u.id::text, c.district_area_id::text, lower(u.valid), upper(u.valid)
               from contest c
               join area a on a.id = c.district_area_id and a.scheme_code = 'us.school_district'
               join result_set r on r.contest_id = c.id and r.status = 'active'
               join lateral (select distinct unit_k from result_value v where v.result_set_k = r.k offset 0) v on true
               join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
               where u.code like '%%:CA:%%' and u.code !~ ':99999$' and lower(u.valid) is not null
                 and not exists (
                   select 1 from reporting_unit_area x
                   where x.unit_id = u.id and x.area_id = c.district_area_id and x.valid_from = lower(u.valid))"""
        ).fetchall()
        print(f"{len(rows)} memberships")
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                [(unit, area, start, end, "official_list") for unit, area, start, end in rows],
                schema={
                    "unit_id": pl.String,
                    "area_id": pl.String,
                    "valid_from": pl.Date,
                    "valid_to": pl.Date,
                    "method": pl.String,
                },
                orient="row",
            ),
        )
        return cs.write()
