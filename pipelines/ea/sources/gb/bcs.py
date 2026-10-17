import io
import json
import zipfile
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.umit import fold
from ea.store import DATA, connect

SOURCE = "gb.bcs"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
UPLOADS = "https://www.bcomm-scotland.independent.gov.uk/wp-content/uploads/sites/2/2025/09"
REVIEWS = {
    "1918": (f"{UPLOADS}/UK_Parliament_1918_1950_1758732212-1.zip", "UK_Parliament_1918_1950", "1918–1950"),
    "1950": (
        f"{UPLOADS}/UK_Parliament_initial_review_1758732213-1.zip",
        "UK_parliament_initial_review",
        "Initial Review 1950–1955",
    ),
    "1955": (f"{UPLOADS}/Uk_parliament_1st_1758732212-1.zip", "Uk_parliament_1st_periodic", "1st Review 1955–1974"),
    "1974": (
        f"{UPLOADS}/2nd_Review_1974_1983.zip",
        "UK_Parliament_2nd_constituencies_orig_gen",
        "2nd Review 1974–1983",
    ),
    "1983": (
        f"{UPLOADS}/UK_Parliament_3rd_constituencies_1758732213-1.zip",
        "UK_Parliament_3rd_constituencies",
        "3rd Review 1983–1995",
    ),
}
PUBLISHER = "Boundary Commission for Scotland"
PAGE = "https://www.bcomm-scotland.independent.gov.uk/boundary-maps/boundary-maps-data-files/"
TERMS = f"{UPLOADS}/SBC_Terms_of_Use_Historical_electoral_data_1758732219-1.pdf"
ATTRIBUTION = (
    "© Boundary Commission for Scotland, Local Government Boundary Commission for Scotland. Contains Ordnance Survey "
    "data © Crown copyright and database rights 2013"
)
NOTES = (
    "Superseded UK Parliament constituency boundaries in Scotland, released as Free to Use data under the One "
    "Scotland Mapping Agreement. Simplified here to 50 m."
)
NAMES = {
    "Ayr District of Burghs": "Ayr Burghs",
    "Banff": "Banffshire",
    "Central Aberdeenshire": "Central Aberdeen and Kincardine",
    "Dumbarton District of Burghs": "Dumbarton Burghs",
    "Dunfermline District of Burghs": "Dunfermline Burghs",
    "Forfar": "Forfarshire",
    "Kincardine and West Aberdeenshire": "West Aberdeen and Kincardine",
    "Kinross and West Perthshire": "Kinross and Western Perthshire",
    "Kirkcaldy District of Burghs": "Kirkcaldy Burghs",
    "Montrose District of Burghs": "Montrose Burghs",
    "North Midlothian": "Midlothian and Peebles Northern",
    "Orkney and Zetland": "Orkney and Shetland",
    "Stirling District of Burghs": "Stirling and Falkirk",
    "Stirling and Falkirk Burghs": "Stirling and Falkirk",
    "Angus East": "East Angus",
}
NAMES_1918 = {"Argyll": "Argyllshire", "East Aberdeenshire": "East Aberdeen and Kincardine"}
TOLERANCE = 50


def fetch() -> None:
    for url, *_ in REVIEWS.values():
        raw.fetch(SOURCE, url, suffix=".zip")
    raw.fetch(SOURCE, TERMS, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        conn.execute("create temp table feature (vintage text, name text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for vintage, (url, stem, _) in REVIEWS.items():
                with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                    members = {name.rsplit("/", 1)[-1]: name for name in archive.namelist()}
                    reader = shapefile.Reader(
                        shp=io.BytesIO(archive.read(members[f"{stem}.shp"])),
                        shx=io.BytesIO(archive.read(members[f"{stem}.shx"])),
                        dbf=io.BytesIO(archive.read(members[f"{stem}.dbf"])),
                    )
                    fields = [field[0] for field in reader.fields[1:]]
                    index = fields.index("NAME" if "NAME" in fields else "Name")
                    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
                        label = str(record[index])
                        label = (NAMES_1918 if vintage == "1918" else {}).get(label, NAMES.get(label, label))
                        copy.write_row((vintage, label, json.dumps(shape.__geo_interface__)))
        rows = conn.execute(
            """select vintage, name,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select vintage, name, st_setsrid(st_coveragesimplify(geom, %s) over (partition by vintage), 27700)
                       as geom
                     from (select vintage, name, st_multi(st_collectionextract(st_makevalid(st_union(
                             st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700))))), 3)) as geom
                           from feature group by vintage, name) whole) simple""",
            [TOLERANCE],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={"vintage": pl.String, "name": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
    print(frame.group_by("vintage").len().sort("vintage").rows())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scotland's UK Parliament constituency boundaries 1918–1997 from the Boundary Commission for Scotland",
            polity="GB",
        )
        sources: dict[str, UUID] = {}
        for vintage, (url, _, title) in REVIEWS.items():
            sources[vintage] = cs.source(PUBLISHER, f"UK Parliament Constituencies {title}")
            cs.add(
                "source",
                id=sources[vintage],
                kind="official_body",
                publisher=PUBLISHER,
                title=f"UK Parliament Constituencies {title}",
                url=PAGE,
                license="Scottish Boundary Commissions Historical Electoral Data terms of use",
                license_url=TERMS,
                attribution=ATTRIBUTION,
                notes=NOTES,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[vintage])
            cs.artifact(raw.cached(SOURCE, TERMS), sources[vintage])
        areas = {
            (code.split(":", 1)[0], fold(text)): (area, start, end)
            for code, text, area, start, end in conn.execute(
                """select c.code, n.text, a.id, lower(c.valid), upper(c.valid) from area a
                   join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   where a.scheme_code = 'gb.pcon' and c.code ~ '^(1918|1950|1955|1974|1983):sct-'"""
            ).fetchall()
        }
        if missing := [
            (vintage, label)
            for vintage, label in frame.select("vintage", "name").iter_rows()
            if (vintage, fold(label)) not in areas
        ]:
            raise ValueError(f"no gb.umit area for {missing}; publish gb.umit first")
        for vintage, label, geom, point in frame.iter_rows():
            area, start, end = areas[(vintage, fold(label))]
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=start,
                valid_to=end,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[vintage],
            )
        return cs.write()
