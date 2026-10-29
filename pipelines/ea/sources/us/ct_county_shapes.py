from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ct_county_shapes"
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
            summary="Connecticut's eight counties keep their 2001 shapes after 2022, because sources still report results by county",
            polity="US",
        )
        rows = conn.execute(
            """select g.area_id, encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(g.label_point), 'hex'), g.source_id
               from area_geometry g join area_code c on c.area_id = g.area_id
               where c.code in ('09001', '09003', '09005', '09007', '09009', '09011', '09013', '09015')
                   and g.valid_to = date '2022-01-01' and g.quality = 'official'"""
        ).fetchall()
        for area, geom, point, source in rows:
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from="2022-01-01",
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )
        return cs.write()
