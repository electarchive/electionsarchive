import io
import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.oc_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://ocvote.gov/bin/maps/shapefiles/zip/Precincts_KMZ/OC_Precinct_2026_GE.zip"
LISTING = "https://ocvote.gov/bin/maps/shapefiles/"
PUBLISHER = "Orange County Registrar of Voters"
TITLE = "Precinct shapefile, 2026 general election"
DAY = date(2026, 6, 2)
FIPS = "06059"
CAVEAT = (
    "The Registrar publishes one precinct layer for the 2026 general election. It is used here for the June 2, 2026 "
    "primary, so a precinct consolidated or split between the two elections is drawn as the general election has it."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".zip")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, URL).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist() if "." in name}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), dbf=io.BytesIO(parts["dbf"]), shx=io.BytesIO(parts["shx"]))
    rows = [
        (str(record[0]).strip(), json.dumps(reader.shape(index).__geo_interface__))
        for index, record in enumerate(reader.iterRecords())
        if reader.shape(index).points
    ]
    with connect() as conn:
        frame = pl.DataFrame(
            conn.execute(
                """select precinct, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (
                     select precinct, st_multi(st_transform(st_makevalid(st_setsrid(st_union(st_geomfromgeojson(geojson)), 3857)), 4326)) as geom
                     from unnest(%s::text[], %s::text[]) as t(precinct, geojson) group by 1
                   ) s where not st_isempty(geom)""",
                [[precinct for precinct, _ in rows], [geojson for _, geojson in rows]],
            ).fetchall(),
            schema=["precinct", "geom", "point"],
            orient="row",
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    print(frame.height, "precincts")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Orange County's precinct shapes for 2026 from the Registrar of Voters",
            polity="US",
        )
        county, name = conn.execute(
            """select a.id, n.text from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.kind = 'official' and n.is_preferred
               where a.scheme_code = 'us.county' and c.code = %s""",
            [FIPS],
        ).fetchone()
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LISTING,
            license="Public record",
            attribution=PUBLISHER,
            notes=CAVEAT,
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        cs.add(
            "boundary_set",
            scheme_code="us.precinct",
            vintage=f"{DAY}:{FIPS}",
            valid_from=DAY,
            valid_to=DAY + timedelta(days=1),
            as_of=DAY,
        )
        ids = [str(cs.area("us.precinct", f"{DAY}:CA:{FIPS}:{precinct}")) for precinct in frame["precinct"]]
        cs.frame("area", pl.DataFrame({"id": ids, "scheme_code": ["us.precinct"] * len(ids)}))
        cs.frame(
            "area_code",
            pl.DataFrame({"area_id": ids, "code": [f"{DAY}:CA:{FIPS}:{p}" for p in frame["precinct"]]}).with_columns(
                valid_from=pl.lit(None, pl.Date)
            ),
        )
        cs.frame(
            "area_parent",
            pl.DataFrame({"area_id": ids, "parent_id": [str(county)] * len(ids)}).with_columns(
                valid_from=pl.lit(None, pl.Date)
            ),
        )
        cs.frame(
            "area_geometry",
            pl.DataFrame({"area_id": ids, "geom": frame["geom"], "label_point": frame["point"]}).with_columns(
                valid_from=pl.lit(DAY),
                valid_to=pl.lit(DAY + timedelta(days=1)),
                quality=pl.lit("official"),
                source_id=pl.lit(str(source)),
            ),
        )
        cs.frame(
            "name",
            pl.DataFrame(
                {
                    "entity_id": ids,
                    "text": [f"Precinct {p}, {name.rsplit(',', 1)[0]}" for p in frame["precinct"]],
                }
            ).with_columns(is_preferred=pl.lit(True), lang=pl.lit("en"), kind=pl.lit("official")),
        )
        return cs.write()
