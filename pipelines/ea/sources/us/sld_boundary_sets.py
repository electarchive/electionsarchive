from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.sld_boundary_sets"
VERSION = "1"


def fetch() -> None:
    pass


def parse() -> None:
    pass


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative district boundary sets, one per span in which no district shape changes",
            polity="US",
        )
        for scheme in ("us.sldl", "us.sldu"):
            breaks = [
                day
                for (day,) in conn.execute(
                    """select distinct d from area a join area_geometry g on g.area_id = a.id
                       cross join lateral (values (g.valid_from), (g.valid_to)) v(d)
                       where a.scheme_code = %s and d is not null order by d""",
                    [scheme],
                ).fetchall()
            ]
            for start, end in zip(breaks, [*breaks[1:], None], strict=True):
                cs.add(
                    "boundary_set",
                    scheme_code=scheme,
                    vintage=str(start.year),
                    valid_from=start,
                    valid_to=end,
                    as_of=start,
                )
        return cs.write()
