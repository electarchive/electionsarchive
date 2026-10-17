import io
import json
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "gb.ons"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SERVICES = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
EARLY = "https://www.arcgis.com/sharing/rest/content/items/96a1a87c2ed04eef928242c0817d8e0a/data"
MIDDLE = f"{SERVICES}/WPC_Dec_2019_GCB_UK_2022/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=27700&f=geojson"
LATEST = (
    f"{SERVICES}/Westminster_Parliamentary_Constituencies_July_2024_Boundaries_UK_BGC/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&outSR=27700&f=geojson"
)
VINTAGES = {
    "1997": (EARLY, "PCON_DEC_2001_GB_BFE", "PCON01", date(1997, 5, 1), date(2005, 5, 5), 641),
    "2005": (EARLY, "PCON_DEC_2005_GB_BFE", "PCON05", date(2005, 5, 5), date(2010, 5, 6), 628),
    "2010": (MIDDLE, None, "pcon19", date(2010, 5, 6), date(2024, 7, 4), 650),
    "2024": (LATEST, None, "PCON24", date(2024, 7, 4), None, 650),
}
TITLES = {
    EARLY: "Westminster Parliamentary Constituencies (2001 and 2005) Boundaries GB BFE",
    MIDDLE: "Westminster Parliamentary Constituencies (December 2019) Boundaries UK BGC",
    LATEST: "Westminster Parliamentary Constituencies (July 2024) Boundaries UK BGC",
}
PAGES = {
    EARLY: "https://geoportal.statistics.gov.uk/datasets/96a1a87c2ed04eef928242c0817d8e0a",
    MIDDLE: "https://geoportal.statistics.gov.uk/datasets/11705b3f6efd4cc092580e269a86500f",
    LATEST: "https://geoportal.statistics.gov.uk/datasets/b49f0eeb2ce540f394831ba3a514d86e",
}
NOTES = {
    EARLY: (
        "Full-extent boundaries, which run out to the sea. They are clipped here to the coastline of the 2019 "
        "generalized boundaries. The file covers Great Britain only, so Northern Ireland's 18 seats from 1997 to 2005 "
        "have no shapes."
    ),
    MIDDLE: "Generalized boundaries clipped to the coastline. The 2010 boundaries were in force until 2024.",
    LATEST: "Generalized boundaries clipped to the coastline.",
}
COUNTRIES = {"E": "ENG", "W": "WLS", "S": "SCT", "N": "NIR"}
PARTITION = date(1922, 12, 6)
PUBLISHER = "Office for National Statistics"
LICENSE = "Open Government Licence v3.0"
LICENSE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
ATTRIBUTION = (
    "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
    "Contains OS data © Crown copyright and database right"
)
TOLERANCE = 20


def fetch() -> None:
    raw.fetch(SOURCE, EARLY, suffix=".zip")
    raw.fetch(SOURCE, MIDDLE, suffix=".geojson")
    raw.fetch(SOURCE, LATEST, suffix=".geojson")


def parse() -> None:
    with connect() as conn:
        conn.execute("create temp table feature (vintage text, code text, name text, geojson text) on commit drop")
        with (
            conn.cursor() as cursor,
            cursor.copy("copy feature (vintage, code, name, geojson) from stdin") as copy,
        ):
            for vintage, (url, stem, prefix, *_) in VINTAGES.items():
                if stem is None:
                    for feature in json.loads(raw.cached(SOURCE, url).path.read_bytes())["features"]:
                        properties = feature["properties"]
                        copy.write_row(
                            (
                                vintage,
                                properties[f"{prefix}cd" if prefix.islower() else f"{prefix}CD"],
                                properties[f"{prefix}nm" if prefix.islower() else f"{prefix}NM"],
                                json.dumps(feature["geometry"]),
                            )
                        )
                    continue
                with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                    reader = shapefile.Reader(
                        shp=io.BytesIO(archive.read(f"{stem}.shp")),
                        shx=io.BytesIO(archive.read(f"{stem}.shx")),
                        dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
                        encoding="latin1",
                    )
                    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
                        copy.write_row(
                            (
                                vintage,
                                f"{prefix}-{record[f'{prefix}CD']}",
                                record[f"{prefix}NM"],
                                json.dumps(shape.__geo_interface__),
                            )
                        )
        conn.execute(
            """create temp table shape on commit drop as
               select vintage, code, name,
                      st_multi(st_collectionextract(st_makevalid(st_force2d(
                        st_setsrid(st_geomfromgeojson(geojson), 27700))), 3)) as geom
               from feature"""
        )
        conn.execute(
            """create temp table coast on commit drop as
               select code, geom from shape where vintage = '2010'"""
        )
        conn.execute("create index on coast using gist (geom)")
        wholes = conn.execute(
            """select code, encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select left(code, 1) as code, st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3)) as geom
                     from shape where vintage = '2010' group by 1
                     union all
                     select 'GBR', st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3))
                     from shape where vintage = '2010') whole"""
        ).fetchall()
        rows = conn.execute(
            """with early as (
                 select vintage, code, name, st_setsrid(st_coveragesimplify(geom, %s) over (partition by vintage), 27700)
                   as geom
                 from shape where vintage in ('1997', '2005')
               ),
               clipped as (
                 select vintage, code, name,
                        st_multi(st_collectionextract(st_makevalid(st_intersection(
                          e.geom, (select st_union(c.geom) from coast c where c.geom && e.geom))), 3)) as geom
                 from early e
                 union all
                 select vintage, code, name, geom from shape where vintage in ('2010', '2024')
               )
               select vintage, code, name,
                      case when vintage in ('2010', '2024') then left(code, 1)
                        else (select left(c.code, 1) from coast c
                              order by c.geom <-> st_pointonsurface(clipped.geom) limit 1) end,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from clipped""",
            [TOLERANCE],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "vintage": pl.String,
            "code": pl.String,
            "name": pl.String,
            "country": pl.String,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    counts = dict(frame.group_by("vintage").len().iter_rows())
    expected = {vintage: spec[5] for vintage, spec in VINTAGES.items()}
    if counts != expected or frame["geom"].null_count() or frame["country"].null_count():
        raise ValueError(f"expected {expected}, got {counts}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")
    pl.DataFrame(
        wholes, schema={"code": pl.String, "geom": pl.String, "label_point": pl.String}, orient="row"
    ).write_parquet(PARSED / "wholes.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Westminster constituency boundaries from the Office for National Statistics for the 1997, 2005, "
                "2010, and 2024 maps"
            ),
            polity="GB",
        )
        sources: dict[str, UUID] = {}
        for url, title in TITLES.items():
            sources[url] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=PAGES[url],
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
                notes=NOTES[url],
            )
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        for vintage, (url, _, _, start, end, _) in VINTAGES.items():
            countries = {letter: cs.area("gb.country", code, on=start) for letter, code in COUNTRIES.items()}
            if any(area in cs.minted for area in countries.values()):
                raise ValueError("publish gb.umit before gb.ons")
            for code, label, country, geom, point in (
                frame.filter(pl.col("vintage") == vintage)
                .select("code", "name", "country", "geom", "label_point")
                .sort("code")
                .iter_rows()
            ):
                area = cs.area("gb.pcon", code, on=start)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code="gb.pcon")
                    cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
                    cs.add("area_parent", area_id=area, parent_id=countries[country], valid_from=start, valid_to=end)
                    cs.add("name", entity_id=area, text=label, lang="en", kind="official", is_preferred=True)
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=start,
                    valid_to=end,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=sources[url],
                )
            cs.add("boundary_set", scheme_code="gb.pcon", vintage=vintage, valid_from=start, valid_to=end, as_of=start)
        plans = dict(
            conn.execute(
                "select scheme_code, id from boundary_set where scheme_code in ('gb.nation', 'gb.country')"
            ).fetchall()
        )
        for scheme in ("gb.nation", "gb.country"):
            cs.add(
                "boundary_set",
                **({"id": plans[scheme]} if scheme in plans else {}),
                scheme_code=scheme,
                vintage="1922",
                valid_from=PARTITION,
                valid_to=None,
                as_of=PARTITION,
            )
        for code, geom, point in pl.read_parquet(PARSED / "wholes.parquet").sort("code").iter_rows():
            area = (
                cs.area("gb.nation", code, on=PARTITION)
                if code == "GBR"
                else cs.area("gb.country", COUNTRIES[code], on=PARTITION)
            )
            if area in cs.minted:
                raise ValueError(f"no area for {code}; publish gb.umit first")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=PARTITION,
                valid_to=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[MIDDLE],
            )
        return cs.write()
