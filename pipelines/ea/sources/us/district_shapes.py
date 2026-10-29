import json
import re
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.district_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

LAYERS: list[dict[str, str]] = [
    {
        "publisher": "Cook County GIS",
        "title": "Cook County Commissioner District (effective 12/5/22)",
        "pattern": r"^17:17031:board-of-commissioners:district-",
        "layer": "https://gis12.cookcountyil.gov/traditional/rest/services/politicalBoundary/MapServer/9",
        "field": "DISTRICT_INT",
    },
    {
        "publisher": "Cook County GIS",
        "title": "Board of Review (effective 12/5/22)",
        "pattern": r"^17:17031:board-of-review:district-",
        "layer": "https://gis12.cookcountyil.gov/traditional/rest/services/politicalBoundary/MapServer/10",
        "field": "DISTRICT_INT",
    },
    {
        "publisher": "Westchester County GIS",
        "title": "Westchester County Legislative Districts",
        "pattern": r"^36:36119:(county-)?legislature:district-",
        "layer": "https://giswww.westchestergov.com/arcgis/rest/services/MappingWestchesterCounty/MapServer/80",
        "field": "DISTRICTID",
    },
    {
        "publisher": "Onondaga County",
        "title": "Onondaga County Legislative Districts",
        "pattern": r"^36:36067:(county-)?legislature:district-",
        "layer": "https://spatialags.vhb.com/arcgis/rest/services/29821_Syr_Onondaga/Onondaga_Service/MapServer/10",
        "field": "COUNTY_LEG",
    },
]


def url(layer: dict[str, str]) -> str:
    return f"{layer['layer']}/query?where=1%3D1&outFields={layer['field']}&outSR=4326&f=geojson"


def fetch() -> None:
    for layer in LAYERS:
        raw.fetch(SOURCE, url(layer), suffix=".json")


def parse() -> None:
    rows = []
    with connect() as conn:
        for layer in LAYERS:
            features = json.loads(raw.cached(SOURCE, url(layer)).path.read_bytes())["features"]
            shapes = {
                re.sub(r"[^a-z0-9]+", "-", str(f["properties"][layer["field"]]).lower()).strip("-"): f["geometry"]
                for f in features
                if f["geometry"] is not None
            }
            wanted = conn.execute(
                """select a.id::text, ac.code from area a join area_code ac on ac.area_id = a.id
                   where a.scheme_code = 'us.local_district' and ac.code ~ %s
                     and not exists (select 1 from area_geometry g where g.area_id = a.id)""",
                [layer["pattern"]],
            ).fetchall()
            matched = 0
            for area, code in wanted:
                geometry = shapes.get(code.rsplit("district-", 1)[1])
                if geometry is None:
                    print("no shape:", code)
                    continue
                matched += 1
                rows.append({"area": area, "geojson": json.dumps(geometry), "layer": layer["title"]})
            print(layer["title"], len(wanted), "wanted;", matched, "matched")
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "matches.json").write_text(json.dumps(rows))


def emit() -> Path:
    rows = json.loads((PARSED / "matches.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="County district boundaries from open GIS layers for commissioner, legislature, and board districts",
            polity="US",
        )
        source_ids = {}
        for layer in LAYERS:
            source = cs.source(layer["publisher"], layer["title"])
            source_ids[layer["title"]] = source
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=layer["publisher"],
                    title=layer["title"],
                    url=layer["layer"],
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, url(layer)), source)
        shapes = conn.execute(
            """select f.area, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(st_makevalid(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4326)))), 'hex'), f.layer
               from unnest(%s::text[], %s::text[], %s::text[]) as f(area, geojson, layer)""",
            [[r["area"] for r in rows], [r["geojson"] for r in rows], [r["layer"] for r in rows]],
        ).fetchall()
        for area, geom, point, layer in shapes:
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source_ids[layer],
            )
        return cs.write()
