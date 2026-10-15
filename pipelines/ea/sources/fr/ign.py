import gzip
import io
import json
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "fr.ign"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
ADMIN_EXPRESS = (
    "https://data.geopf.fr/telechargement/download/ADMIN-EXPRESS-COG-CARTO/"
    "ADMIN-EXPRESS-COG-CARTO_3-2__SHP_WGS84G_FRA_2024-02-22/ADMIN-EXPRESS-COG-CARTO_3-2__SHP_WGS84G_FRA_2024-02-22.7z"
)
LAYERS = (
    "ADMIN-EXPRESS-COG-CARTO_3-2__SHP_WGS84G_FRA_2024-02-22/ADMIN-EXPRESS-COG-CARTO/"
    "1_DONNEES_LIVRAISON_2024-03-00171/ADECOGC_3-2_SHP_WGS84G_FRA-ED2024-02-22/"
)
COMMUNES = "https://etalab-datasets.geo.data.gouv.fr/contours-administratifs/2024/geojson/communes-5m.geojson.gz"
CADASTRE = "https://cadastre.data.gouv.fr/data/etalab-cadastre/2026-09-01/geojson/communes/50/50592/cadastre-50592-sections.json.gz"
SRIDS = {
    "metro": 2154,
    "971": 32620,
    "972": 32620,
    "973": 32622,
    "974": 32740,
    "975": 32621,
    "976": 32738,
    "977": 32620,
    "978": 32620,
    "986": 32701,
    "987": 32706,
    "988": 32758,
}
OSM = ("975", "977", "978", "986")
DEPARTMENTS = {"977": "ZX", "978": "ZX"}
CODES = {"97701": "ZX701", "97801": "ZX801"}
DETACHED = {"51664": "02899", "49382": "44060", "44225": "49144"}
PONT_FARCY = ("14513", "649")
CONSTITUENCIES = {"986": "98601", "977": "ZX01", "978": "ZX01"}
COUNTS = {"commune": 35021, "detached": 4, "departement": 106, "circonscription": 7, "nation": 1}
TOLERANCE = 10
ELECTION = date(2024, 6, 30)
REDISTRICTING = date(2012, 6, 10)
SOURCES = {
    ADMIN_EXPRESS: (
        "Institut national de l'information géographique et forestière",
        "ADMIN EXPRESS COG CARTO, édition 2024",
        "https://geoservices.ign.fr/adminexpress",
        "Licence Ouverte v2.0",
        "https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        "IGN, ADMIN EXPRESS COG CARTO, 2024-02-22",
    ),
    COMMUNES: (
        "Etalab",
        "Contours administratifs 2024: communes (5 m)",
        "https://www.data.gouv.fr/datasets/contours-administratifs",
        "Open Database License (ODbL) 1.0",
        "https://opendatacommons.org/licenses/odbl/1-0/",
        "Etalab, Contours administratifs 2024. Contains OpenStreetMap data © OpenStreetMap contributors.",
    ),
    CADASTRE: (
        "Direction générale des Finances publiques",
        "Plan cadastral informatisé: sections of Tessy-Bocage (50592)",
        "https://cadastre.data.gouv.fr/datasets/cadastre-etalab",
        "Licence Ouverte v2.0",
        "https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        "DGFiP, plan cadastral informatisé, 2026-09-01, via Etalab",
    ),
}


def fetch() -> None:
    raw.fetch(SOURCE, ADMIN_EXPRESS, suffix=".7z")
    raw.fetch(SOURCE, COMMUNES, suffix=".geojson.gz")
    raw.fetch(SOURCE, CADASTRE, suffix=".json.gz")


def parse() -> None:
    archive = raw.cached(SOURCE, ADMIN_EXPRESS).path

    def layer(name: str) -> shapefile.Reader:
        shp, shx, dbf = (
            io.BytesIO(
                subprocess.run(
                    ["bsdtar", "-xOf", str(archive), f"{LAYERS}{name}.{ext}"], check=True, capture_output=True
                ).stdout
            )
            for ext in ("shp", "shx", "dbf")
        )
        return shapefile.Reader(shp=shp, shx=shx, dbf=dbf, encoding="utf-8")

    features: list[tuple[str, str, str, str, str]] = []
    reader = layer("COMMUNE")
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is not None and shape is not None:
            dep = str(record["INSEE_DEP"])
            geojson = json.dumps(shape.__geo_interface__)
            features.append(("commune", str(record["INSEE_COM"]), dep, dep if dep in SRIDS else "metro", geojson))
    reader = layer("COMMUNE_ASSOCIEE_OU_DELEGUEE")
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is not None and shape is not None and str(record["INSEE_CAD"]) in DETACHED:
            code = DETACHED[str(record["INSEE_CAD"])]
            features.append(("detached", code, code[:2], "metro", json.dumps(shape.__geo_interface__)))
    code, prefix = PONT_FARCY
    for feature in json.loads(gzip.decompress(raw.cached(SOURCE, CADASTRE).path.read_bytes()))["features"]:
        if feature["properties"]["prefixe"] == prefix:
            features.append(("detached", code, code[:2], "metro", json.dumps(feature["geometry"])))
    for feature in json.loads(gzip.decompress(raw.cached(SOURCE, COMMUNES).path.read_bytes()))["features"]:
        part = feature["properties"]["code"][:3]
        if part in (*OSM, "987", "988"):
            code = feature["properties"]["code"]
            features.append(
                ("commune", CODES.get(code, code), DEPARTMENTS.get(part, part), part, json.dumps(feature["geometry"]))
            )
    with connect() as conn:
        seats = {
            commune: constituency
            for commune, constituency in conn.execute(
                """select distinct cc.code, dc.code
                   from area a join area_code cc on cc.area_id = a.id
                   join result_rollup r on r.area_k = a.k
                   join result_set s on s.k = r.result_set_k
                   join contest t on t.id = s.contest_id
                   join area_code dc on dc.area_id = t.district_area_id
                   where a.scheme_code = 'fr.commune' and (cc.code like '987%' or cc.code like '988%')"""
            ).fetchall()
        }
        conn.execute(
            """create temp table feature (layer text, code text, dep text, part text, srid int,
                                          constituency text, geojson text) on commit drop"""
        )
        with (
            conn.cursor() as cursor,
            cursor.copy("copy feature (layer, code, dep, part, srid, constituency, geojson) from stdin") as copy,
        ):
            for kind, code, dep, part, geojson in features:
                copy.write_row((kind, code, dep, part, SRIDS[part], seats.get(code, CONSTITUENCIES.get(part)), geojson))
        conn.execute(
            """create temp table shape on commit drop as
               select layer, code, dep, part, srid, constituency,
                      st_setsrid(st_coveragesimplify(geom, %s) over (partition by layer, part), srid) as geom
               from (select layer, code, dep, part, srid, constituency,
                            st_setsrid(st_coverageclean(geom) over (partition by layer, part), srid) as geom
                     from (select layer, code, dep, part, srid, constituency,
                                  st_multi(st_collectionextract(st_makevalid(st_union(st_makevalid(st_force2d(
                                    st_transform(st_setsrid(st_geomfromgeojson(geojson), 4326), srid))))), 3)) as geom
                           from feature group by layer, code, dep, part, srid, constituency) features) cleaned""",
            [TOLERANCE],
        )
        rows = conn.execute(
            """with layers as (
                 select layer, code, part, geom from shape where layer = 'detached' or part <> '986'
                 union all
                 select 'commune', '98601', '986', st_setsrid(st_coverageunion(geom), 32701)
                 from shape where part = '986'
                 union all
                 select 'departement', dep, min(part), st_setsrid(st_coverageunion(geom), min(srid))
                 from shape where layer = 'commune' group by dep
                 union all
                 select 'circonscription', constituency, min(part), st_setsrid(st_coverageunion(geom), min(srid))
                 from shape where constituency is not null group by constituency
               )
               select layer, code, part,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from layers
               union all
               select 'nation', 'FR', 'metro',
                      encode(st_asewkb(st_multi(st_collectionextract(st_collect(geom), 3))), 'hex'), null
               from (select st_transform(st_setsrid(st_coverageunion(geom), min(srid)), 4326) as geom
                     from shape where layer = 'commune' and part not in ('975', '977', '978', '986', '987', '988')
                     group by part) parts"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={"layer": pl.String, "code": pl.String, "part": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
    counts = dict(frame.group_by("layer").len().iter_rows())
    if counts != COUNTS or frame["geom"].null_count():
        raise ValueError(f"expected {COUNTS}, got {counts}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "France's communes, départements, and national outline from IGN's ADMIN EXPRESS COG CARTO 2024, "
                "the overseas collectivities' communes from Etalab, Pont-Farcy from Tessy-Bocage's cadastral sections, "
                "and the seven overseas constituencies without a shape as unions of their communes"
            ),
            polity="FR",
        )
        sources: dict[str, UUID] = {}
        for url, (publisher, title, page, license, license_url, attribution) in SOURCES.items():
            sources[url] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=page,
                license=license,
                license_url=license_url,
                attribution=attribution,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        for scheme, layers in (
            ("fr.commune", ("commune", "detached")),
            ("fr.departement", ("departement",)),
            ("fr.circonscription", ("circonscription",)),
            ("fr.nation", ("nation",)),
        ):
            shapes = {
                code: (part, geom, point)
                for code, part, geom, point in frame.filter(pl.col("layer").is_in(layers))
                .select("code", "part", "geom", "label_point")
                .rows()
            }
            known = conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = %s",
                [scheme],
            ).fetchall()
            if scheme == "fr.circonscription":
                known = [(code, area) for code, area in known if code in shapes]
            missing = {code for code, _ in known if code not in shapes}
            if missing:
                raise ValueError(f"{scheme}: no shape for {sorted(missing)}")
            for code, area in known:
                part, geom, point = shapes[code]
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=REDISTRICTING if scheme == "fr.circonscription" else None,
                    valid_to=None,
                    geom=geom,
                    label_point=point,
                    quality="osm" if part in OSM else "official",
                    source_id=sources[
                        CADASTRE
                        if code == PONT_FARCY[0]
                        else COMMUNES
                        if part in (*OSM, "987", "988")
                        else ADMIN_EXPRESS
                    ],
                )
        for scheme in ("fr.commune", "fr.departement"):
            cs.add("boundary_set", scheme_code=scheme, vintage="2024", valid_from=None, valid_to=None, as_of=ELECTION)
        return cs.write()
