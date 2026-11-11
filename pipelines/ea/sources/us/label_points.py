from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.label_points"
VERSION = "1"
SCHEMES = ("us.place", "us.town")


def emit() -> Path:
    with connect() as conn:
        rows = conn.execute(
            """select g.area_id, g.valid_from, g.valid_from_precision::text, g.valid_to, g.valid_to_precision::text,
                      encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(st_pointonsurface(g.geom)), 'hex'),
                      g.quality::text, g.source_id, c.pipeline
               from area_geometry g
               join area a on a.id = g.area_id
               join changeset c on c.id = g.changeset_id
               where a.scheme_code = any(%s) and g.label_point is null
               order by g.area_id, g.valid_from""",
            [list(SCHEMES)],
        ).fetchall()
        writers = sorted({row[9] for row in rows})
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Label points for the US place and town shapes that were loaded without one",
            polity="US",
        )
        cs.manifest["scope"] = {"polity": "US", "replaces": {"area_geometry": writers}}
        for area, start, start_precision, end, end_precision, geom, point, quality, source, _ in rows:
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=start,
                valid_from_precision=start_precision,
                valid_to=end,
                valid_to_precision=end_precision,
                geom=geom,
                label_point=point,
                quality=quality,
                source_id=source,
            )
        print(len(rows), "shapes from", len(writers), "pipelines")
        return cs.write()
