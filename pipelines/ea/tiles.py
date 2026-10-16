import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ea.store import DATA, ROOT, connect

AREAS_SQL = """
select '{"type":"Feature","id":' || a.k || ',"properties":{},"geometry":' || ST_AsGeoJSON(g.geom, 6) || '}'
from area a join area_geometry g on g.area_id = a.id
where a.scheme_code = %s and g.valid @> %s::date
"""

LABELS_SQL = """
select '{"type":"Feature","id":' || a.k || ',"properties":{},"geometry":'
    || ST_AsGeoJSON(coalesce(g.label_point, ST_PointOnSurface(g.geom)), 6) || '}'
from area a join area_geometry g on g.area_id = a.id
where a.scheme_code = %s and g.valid @> %s::date
"""

DENSE = {"us.nyc_ed": 14}


def build(prefix: str | None) -> str:
    started = time.monotonic()
    with connect() as conn:
        sets = conn.execute(
            """select bs.scheme_code, bs.vintage, bs.as_of,
                      (select count(*) from area a join area_geometry g on g.area_id = a.id
                       where a.scheme_code = bs.scheme_code and g.valid @> bs.as_of),
                      (select string_agg(distinct case when s.publisher = 'Eurostat'
                                then '© EuroGeographics for the administrative boundaries' else s.publisher end, '; ')
                       from area a join area_geometry g on g.area_id = a.id join source s on s.id = g.source_id
                       where a.scheme_code = bs.scheme_code and g.valid @> bs.as_of)
               from boundary_set bs where starts_with(bs.scheme_code, %s)
               order by bs.scheme_code, bs.vintage""",
            [prefix or ""],
        ).fetchall()
    summaries: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        for scheme_code, vintage, as_of, features, attribution in sets:
            if not features:
                continue
            areas_path = Path(scratch) / "areas.geojsonl"
            labels_path = Path(scratch) / "labels.geojsonl"
            output = Path(scratch) / "out.pmtiles"
            with connect() as conn, areas_path.open("w") as areas_file, labels_path.open("w") as labels_file:
                for query, handle in ((AREAS_SQL, areas_file), (LABELS_SQL, labels_file)):
                    with conn.cursor(name="features") as cursor:
                        cursor.itersize = 10_000
                        cursor.execute(query, [scheme_code, as_of])
                        for (line,) in cursor:
                            handle.write(f"{line}\n")
            subprocess.run(
                [
                    "tippecanoe",
                    f"--output={output}",
                    "--force",
                    "--quiet",
                    f"--attribution={attribution or ''}",
                    "--minimum-zoom=0",
                    f"--maximum-zoom={DENSE.get(scheme_code, 12 if features >= 5_000 else 10)}",
                    "--simplification=4",
                    "--detect-shared-borders",
                    "--exclude-all",
                    *(
                        ["--no-feature-limit", "--no-tile-size-limit", "--no-tiny-polygon-reduction", "--drop-rate=1"]
                        if scheme_code in DENSE
                        else ["--no-tiny-polygon-reduction-at-maximum-zoom", "--drop-smallest-as-needed"]
                    ),
                    f"--named-layer=areas:{areas_path}",
                    f"--named-layer=labels:{labels_path}",
                ],
                check=True,
            )
            destination = DATA / "tiles" / scheme_code / f"{vintage}.pmtiles"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(output, destination)
            size_mb = destination.stat().st_size / 1_000_000
            summaries.append(
                f"{scheme_code} {vintage}: {features} features, {size_mb:.2f} MB, {destination.relative_to(ROOT)}"
            )
    summaries.append(f"{len(sets)} archives in {time.monotonic() - started:.0f}s")
    return "\n".join(summaries)
