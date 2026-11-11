from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.sources.us import mpls_evs
from ea.store import connect

SOURCE = "us.geometry_origin"
VERSION = "1"


def fetch() -> None:
    pass


def parse() -> None:
    pass


def emit() -> Path | None:
    with connect() as conn:
        rows = conn.execute(
            """select g.area_id, g.valid_from, g.valid_to, g.quality::text,
                      encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(g.label_point), 'hex'), ac.code, c.pipeline
               from area_geometry g join changeset c on c.id = g.changeset_id
               join area_code ac on ac.area_id = g.area_id
               where g.source_id is null and c.pipeline = any(%s)""",
            [["us.mpls_evs", "us.ramsey_rcv"]],
        ).fetchall()
        if not rows:
            return None
        origin = {}
        for plan, unit, area, _, _ in pl.read_parquet(mpls_evs.PARSED / "precincts.parquet").iter_rows():
            found = conn.execute(
                "select source_id from area_geometry where area_id = %s and source_id is not null", [area]
            ).fetchone()
            origin[plan, unit] = found
            origin.setdefault((plan, None), found)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Credit the source of the Minneapolis and St. Paul precinct shapes and the park board districts "
            "built from them, which were first loaded without one",
            polity="US",
            kind="correction",
        )
        cs.manifest["scope"] = {"polity": "US", "replaces": {"area_geometry": ["us.mpls_evs", "us.ramsey_rcv"]}}
        for area, valid_from, valid_to, quality, geom, point, code, pipeline in rows:
            if pipeline == "us.ramsey_rcv":
                found = conn.execute(
                    """select g.source_id from area_code c join area_geometry g on g.area_id = c.area_id
                       where c.code = %s and g.source_id is not null""",
                    [f"2022:MN:27123:RAMSEY:{code.split(':27123:', 1)[1]}"],
                ).fetchone()
            else:
                found = origin.get(
                    (str(valid_from.year), code.split(":27053:", 1)[1] if quality == "official" else None)
                )
            if found is None:
                raise ValueError(f"{code}: no sourced shape to credit")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=valid_from,
                valid_to=valid_to,
                geom=geom,
                label_point=point,
                quality=quality,
                source_id=found[0],
            )
        return cs.write()
