import json
import re
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.maricopa_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LAYER = "https://gis.maricopa.gov/arcgis/rest/services/GIO/JusticePrecinct/MapServer/0"
URL = f"{LAYER}/query?where=1%3D1&outFields=PrecinctName&outSR=4326&f=geojson"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".json")


def parse() -> None:
    features = json.loads(raw.cached(SOURCE, URL).path.read_bytes())["features"]
    shapes = {slug(f["properties"]["PrecinctName"]): f["geometry"] for f in features if f["geometry"] is not None}
    with connect() as conn:
        wanted = conn.execute(
            """select a.id::text, ac.code from area a join area_code ac on ac.area_id = a.id
               where a.scheme_code = 'us.local_district'
                 and ac.code ~ '^04:04013:(justice-of-the-peace|constable):district-'
                 and not exists (select 1 from area_geometry g where g.area_id = a.id)"""
        ).fetchall()
    rows = []
    for area, code in wanted:
        name = code.rsplit("district-", 1)[1]
        geometry = shapes.get(name) or shapes.get(f"{name}-justice-court")
        if geometry is None:
            print("no shape:", code)
            continue
        rows.append({"area": area, "geojson": json.dumps(geometry)})
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "matches.json").write_text(json.dumps(rows))
    print(len(wanted), "wanted;", len(rows), "matched")


def emit() -> Path:
    rows = json.loads((PARSED / "matches.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maricopa County justice precinct boundaries for justice of the peace and constable districts",
            polity="US",
        )
        source = cs.source("Maricopa County Geographic Information Office", "Justice Precinct map service")
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher="Maricopa County Geographic Information Office",
                title="Justice Precinct map service",
                url=LAYER,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        shapes = conn.execute(
            """select f.area, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(st_makevalid(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4326)))), 'hex')
               from unnest(%s::text[], %s::text[]) as f(area, geojson)""",
            [[r["area"] for r in rows], [r["geojson"] for r in rows]],
        ).fetchall()
        for area, geom, point in shapes:
            cs.add("area_geometry", area_id=area, valid_from=None, geom=geom, label_point=point, quality="official", source_id=source)
        return cs.write()
