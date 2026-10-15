import io
import json
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID, uuid7

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "ch.swisstopo"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
EDITION = "2026-01"
BOUNDARIES = (
    f"https://data.geo.admin.ch/ch.swisstopo.swissboundaries3d/swissboundaries3d_{EDITION}/"
    f"swissboundaries3d_{EDITION}_2056_5728.shp.zip"
)
LAYER = "swissBOUNDARIES3D_1_5_TLM_HOHEITSGEBIET"
TOLERANCE = 10
PUBLISHER = "Federal Office of Topography swisstopo"
TITLE = f"swissBOUNDARIES3D, edition {EDITION}"
CANTONS = {
    1: ("ZH", "Zürich", "de", "Zurich"),
    2: ("BE", "Bern", "de", None),
    3: ("LU", "Luzern", "de", "Lucerne"),
    4: ("UR", "Uri", "de", None),
    5: ("SZ", "Schwyz", "de", None),
    6: ("OW", "Obwalden", "de", None),
    7: ("NW", "Nidwalden", "de", None),
    8: ("GL", "Glarus", "de", None),
    9: ("ZG", "Zug", "de", None),
    10: ("FR", "Fribourg", "fr", None),
    11: ("SO", "Solothurn", "de", None),
    12: ("BS", "Basel-Stadt", "de", None),
    13: ("BL", "Basel-Landschaft", "de", None),
    14: ("SH", "Schaffhausen", "de", None),
    15: ("AR", "Appenzell Ausserrhoden", "de", None),
    16: ("AI", "Appenzell Innerrhoden", "de", None),
    17: ("SG", "St. Gallen", "de", None),
    18: ("GR", "Graubünden", "de", "Grisons"),
    19: ("AG", "Aargau", "de", None),
    20: ("TG", "Thurgau", "de", None),
    21: ("TI", "Ticino", "it", None),
    22: ("VD", "Vaud", "fr", None),
    23: ("VS", "Valais", "fr", None),
    24: ("NE", "Neuchâtel", "fr", None),
    25: ("GE", "Genève", "fr", "Geneva"),
    26: ("JU", "Jura", "fr", None),
}
MOUTIER = ("0700", "6831", date(2026, 1, 1))


def fetch() -> None:
    raw.fetch(SOURCE, BOUNDARIES, suffix=".zip")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, BOUNDARIES).path) as archive:
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{LAYER}.shp")),
            shx=io.BytesIO(archive.read(f"{LAYER}.shx")),
            dbf=io.BytesIO(archive.read(f"{LAYER}.dbf")),
            encoding="utf-8",
        )
        features = [
            (
                record["OBJEKTART"] == "Gemeindegebiet",
                f"{record['BFS_NUMMER']:04d}",
                record["KANTONSNUM"],
                record["NAME"],
                json.dumps(shape.__geo_interface__),
            )
            for record, shape in zip(reader.records(), reader.shapes(), strict=True)
            if record is not None and shape is not None and record["ICC"] == "CH"
        ]
    with connect() as conn:
        conn.execute(
            """create temp table shape on commit drop as
               select commune, code, canton, name, st_setsrid(st_coveragesimplify(geom, %s) over (), 2056) as geom
               from (select commune, code, canton, name,
                            st_multi(st_collectionextract(st_makevalid(st_force2d(
                              st_setsrid(st_geomfromgeojson(geojson), 2056))), 3)) as geom
                     from unnest(%s::bool[], %s::text[], %s::int[], %s::text[], %s::text[])
                       as f(commune, code, canton, name, geojson)) features""",
            [TOLERANCE, *(list(column) for column in zip(*features, strict=True))],
        )
        rows = conn.execute(
            """with layers as (
                 select 'commune' as layer, code, canton, name, geom from shape where commune
                 union all
                 select 'canton', canton::text, canton, null, st_setsrid(st_coverageunion(geom), 2056)
                 from shape group by canton
                 union all
                 select 'nation', 'CH', null, null, st_setsrid(st_coverageunion(geom), 2056) from shape
               )
               select layer, code, canton, name,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex'),
                      st_npoints(geom)
               from layers"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "layer": pl.String,
            "code": pl.String,
            "canton": pl.Int64,
            "name": pl.String,
            "geom": pl.String,
            "label_point": pl.String,
            "points": pl.Int64,
        },
        orient="row",
    )
    counts = dict(frame.group_by("layer").len().iter_rows())
    if counts != {"commune": 2110, "canton": 26, "nation": 1} or frame["geom"].null_count():
        raise ValueError(f"expected 2,110 communes, 26 cantons, and one nation, got {counts}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.drop("points").write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Switzerland, its cantons, and its communes from swissBOUNDARIES3D {EDITION}",
            polity="CH",
        )

        def name(entity: UUID, text: str, lang: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d",
            license="Open Government Data, free use with attribution",
            license_url="https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices",
            attribution="© swisstopo",
        )
        cs.artifact(raw.cached(SOURCE, BOUNDARIES), source)

        def geometry(area: UUID, geom: str, point: str | None) -> None:
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )

        switzerland = cs.polity("CH")
        cs.add("polity", id=switzerland, kind="sovereign_state", iso_code="CH")
        name(switzerland, "Switzerland", "en")
        for text, lang in (("Schweiz", "de"), ("Suisse", "fr"), ("Svizzera", "it"), ("Svizra", "rm")):
            name(switzerland, text, lang, "native")
        cs.add("slug", path="ch", entity_id=switzerland)
        cs.add("external_id", entity_id=switzerland, scheme="wikidata", value="Q39")
        for code, rank, noun in (("ch.nation", 0, "country"), ("ch.canton", 1, "canton"), ("ch.commune", 2, "commune")):
            cs.add("scheme", code=code, polity_id=switzerland, kind="administrative", rank=rank, noun=noun)

        layers = {layer: lines for (layer,), lines in frame.group_by("layer")}
        nation = cs.area("ch.nation", "CH")
        cs.add("area", id=nation, scheme_code="ch.nation")
        cs.add("area_code", area_id=nation, code="CH", valid_from=None)
        name(nation, "Switzerland", "en")
        cs.add("polity_territory", polity_id=switzerland, area_id=nation)
        geometry(nation, layers["nation"]["geom"][0], None)

        cantons: dict[int, UUID] = {}
        for number, geom, point in layers["canton"].select(pl.col("code").cast(pl.Int64), "geom", "label_point").rows():
            abbreviation, native, lang, english = CANTONS[number]
            polity = cs.polity(f"CH-{abbreviation}")
            cs.add("polity", id=polity, kind="federal_unit", iso_code=f"CH-{abbreviation}")
            cs.add("polity_parent", polity_id=polity, parent_id=switzerland)
            cantons[number] = cs.area("ch.canton", abbreviation)
            cs.add("area", id=cantons[number], scheme_code="ch.canton")
            cs.add("area_code", area_id=cantons[number], code=abbreviation, valid_from=None)
            cs.add("area_parent", area_id=cantons[number], parent_id=nation, valid_from=None)
            cs.add("polity_territory", polity_id=polity, area_id=cantons[number])
            for entity in (polity, cantons[number]):
                name(entity, native, lang)
                if english is not None:
                    name(entity, english, "en", "english")
            geometry(cantons[number], geom, point)

        old, new, moved = MOUTIER
        bern = cantons[next(number for number, canton in CANTONS.items() if canton[0] == "BE")]
        for code, canton, text, geom, point in (
            layers["commune"].select("code", "canton", "name", "geom", "label_point").rows()
        ):
            commune = cs.area("ch.commune", code)
            cs.add("area", id=commune, scheme_code="ch.commune")
            name(commune, text, CANTONS[canton][2])
            geometry(commune, geom, point)
            if code != new:
                cs.add("area_code", area_id=commune, code=code, valid_from=None)
                cs.add("area_parent", area_id=commune, parent_id=cantons[canton], valid_from=None)
                continue
            cs.add("area_code", area_id=commune, code=old, valid_from=None, valid_to=moved)
            cs.add("area_code", area_id=commune, code=new, valid_from=moved)
            cs.add("area_parent", area_id=commune, parent_id=bern, valid_from=None, valid_to=moved)
            cs.add("area_parent", area_id=commune, parent_id=cantons[canton], valid_from=moved)
            existing = conn.execute(
                """select e.id from area_event e join area_event_member m on m.event_id = e.id
                   where e.kind = 'transferred' and e.date = %s and m.area_id = %s""",
                [moved, commune],
            ).fetchone()
            event = uuid7() if existing is None else existing[0]
            cs.add(
                "area_event",
                id=event,
                kind="transferred",
                date=moved,
                source_id=source,
                notes="Moutier transferred from the canton of Bern to the canton of Jura",
            )
            cs.add("area_event_member", event_id=event, area_id=commune, role="from")
        return cs.write()
