import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ea import display, plane
from ea.store import DATA, ROOT, connect

FEATURES_SQL = """
select '{{"type":"Feature","id":' || k || ',"properties":{{}},"geometry":' || ST_AsGeoJSON(p, 1, 0) || '}}'
from (
    select a.k, {plane} as p
    from area a join area_geometry g on g.area_id = a.id
    left join display.clipped c on c.area_id = g.area_id and c.valid = g.valid and c.mask_version = %s
    where a.scheme_code = %s and g.valid @> %s::date
) x
where p is not null
"""

DENSE = {"us.nyc_ed": 14}


def build(prefix: str | None) -> str:
    started = time.monotonic()
    with connect() as conn:
        sets = conn.execute(
            """select bs.scheme_code, bs.vintage, bs.as_of,
                      (select count(*) from area a join area_geometry g on g.area_id = a.id
                       where a.scheme_code = bs.scheme_code and g.valid @> bs.as_of),
                      (select string_agg(distinct c.credit, E'\n')
                       from area a join area_geometry g on g.area_id = a.id join source s on s.id = g.source_id
                       cross join lateral (values (s.publisher), (nullif(s.attribution, ''))) c(credit)
                       where a.scheme_code = bs.scheme_code and g.valid @> bs.as_of and c.credit is not null)
               from boundary_set bs where starts_with(bs.scheme_code, %s)
               order by bs.scheme_code, bs.vintage""",
            [prefix or ""],
        ).fetchall()
    with connect() as conn:
        mask = display.ensure(conn)
    summaries: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        for scheme_code, vintage, as_of, features, attribution in sets:
            if not features:
                continue
            with connect() as conn:
                fresh = display.refresh(conn, scheme_code, as_of, mask)
            areas_path = Path(scratch) / "areas.geojsonl"
            labels_path = Path(scratch) / "labels.geojsonl"
            output = Path(scratch) / "out.pmtiles"
            with connect() as conn, areas_path.open("w") as areas_file, labels_path.open("w") as labels_file:
                for column, handle in (
                    ("coalesce(c.geom, g.geom)", areas_file),
                    (
                        (
                            "coalesce(case when c.geom is null or ST_Covers(c.geom, g.label_point) then g.label_point end,"
                            " ST_PointOnSurface(coalesce(c.geom, g.geom)))"
                        ),
                        labels_file,
                    ),
                ):
                    query = FEATURES_SQL.format(plane=plane.sql(column, scheme_code))
                    with conn.cursor(name="features") as cursor:
                        cursor.itersize = 10_000
                        cursor.execute(query, [mask, scheme_code, as_of])
                        for (line,) in cursor:
                            handle.write(f"{line}\n")
            subprocess.run(
                [
                    "tippecanoe",
                    f"--output={output}",
                    "--force",
                    "--quiet",
                    "--projection=EPSG:3857",
                    f"--attribution={chr(10).join(filter(None, [attribution, display.ATTRIBUTION]))}",
                    "--minimum-zoom=0",
                    f"--maximum-zoom={DENSE.get(scheme_code, 12 if features >= 5_000 else 10)}",
                    "--simplification=4",
                    "--detect-shared-borders",
                    "--exclude-all",
                    *(
                        ["--no-feature-limit", "--no-tile-size-limit", "--no-tiny-polygon-reduction", "--drop-rate=1"]
                        if scheme_code in DENSE
                        else [
                            "--no-tiny-polygon-reduction"
                            if features >= 5_000
                            else "--no-tiny-polygon-reduction-at-maximum-zoom",
                            "--coalesce-smallest-as-needed",
                        ]
                    ),
                    f"--named-layer=areas:{areas_path}",
                    f"--named-layer=labels:{labels_path}",
                ],
                check=True,
            )
            destination = DATA / "plane" / scheme_code / f"{vintage}.pmtiles"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(output, destination)
            size_mb = destination.stat().st_size / 1_000_000
            summaries.append(
                f"{scheme_code} {vintage}: {features} features, {fresh} clipped, {size_mb:.2f} MB, {destination.relative_to(ROOT)}"
            )
    summaries.append(f"{len(sets)} archives in {time.monotonic() - started:.0f}s")
    return "\n".join(summaries)
