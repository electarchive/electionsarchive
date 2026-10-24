import json
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "gb.ireland_1918"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SERVICES = "https://services-eu1.arcgis.com/FH5XCsx8rYXqnjF5/arcgis/rest/services"
DIVISIONS = (
    f"{SERVICES}/Electoral_Divisions___OSi_National_Statutory_Boundaries___Generalised_20m/FeatureServer/0/query"
    "?where=COUNTY%3D%27DUBLIN%27&outFields=ENGLISH&outSR=2157&orderByFields=ED_ID&f=geojson"
)
STATUTE = "https://archive.org/details/representationof00grearich"
ELECTION = date(1918, 12, 14)
TOLERANCE = 50
MERGES = {
    "county-louth": ("north-louth", "south-louth"),
    "county-waterford": ("east-waterford", "west-waterford"),
    "king-s-county": ("birr", "tullamore"),
    "leitrim": ("north-leitrim", "south-leitrim"),
    "longford": ("north-longford", "south-longford"),
    "queen-s-county": ("ossory", "leix"),
    "westmeath": ("north-westmeath", "south-westmeath"),
    "galway-connemara": ("galway-connemara", "galway-city"),
    "north-kilkenny": ("north-kilkenny", "kilkenny-city"),
}
DUBLIN = {
    "dublin-clontarf": ("CLONTARF", "DRUMCONDRA", "MOUNTJOY", "BALLYBOUGH"),
    "dublin-college-green": ("INNS QUAY", "NORTH CITY", "ROTUNDA"),
    "dublin-harbour": ("NORTH DOCK", "SOUTH DOCK"),
    "dublin-st-james-s": ("USHERS", "KILMAINHAM", "INCHICORE"),
    "dublin-st-michan-s": ("ARRAN QUAY", "BOTANIC", "CABRA EAST"),
    "dublin-st-patrick-s": ("MERCHANTS QUAY", "WOOD QUAY"),
    "dublin-st-stephen-s-green": ("MANSION HOUSE", "ROYAL EXCHANGE", "SAINT KEVIN"),
    "dublin-pembroke": (
        "PEMBROKE",
        "CLONSKEAGH",
        "DUNDRUM",
        "CHURCHTOWN",
        "CRUMLIN",
        "KIMMAGE",
        "RATHFARNHAM",
        "TEMPLEOGUE",
        "WALKINSTOWN",
        "BALLINTEER",
        "FIRHOUSE",
        "BALLYBODEN",
        "EDMONDSTOWN",
        "BOHERNABREENA",
        "TIBRADDEN",
    ),
    "dublin-rathmines": ("RATHMINES", "TERENURE"),
    "north-county-dublin": (
        "TALLAGHT",
        "CLONDALKIN",
        "PALMERSTON",
        "LUCAN",
        "NEWCASTLE",
        "RATHCOOLE",
        "SAGGART",
        "BALLINASCORNEY",
        "CHERRY ORCHARD",
        "DRUMFINN",
        "KYLEMORE",
        "DECIES",
        "CARNA",
    ),
    "south-county-dublin": (
        "BLACKROCK",
        "DALKEY",
        "KILLINEY",
        "DUN LAOGHAIRE",
        "STILLORGAN",
        "FOXROCK",
        "CABINTEELY",
        "SHANKILL",
        "GLENCULLEN",
        "BALLYBRACK",
    ),
}
LIFFEY = (-6.2592, 53.3472)
NOTES = (
    "Synthetic shapes for Ireland's 1918–1922 constituencies outside the six counties. Divisions the "
    "Redistribution of Seats (Ireland) Act 1918 (7 & 8 Geo. 5 c. 65) left alone keep this archive's synthetic 1885 "
    "shapes; counties reduced to one member are the union of their 1885 divisions, Galway's urban district joins "
    "Connemara, and Kilkenny's municipal borough joins North Kilkenny, as the Act's Fourth Schedule places them. The "
    "seven Dublin borough divisions and the four county divisions are built from Tailte Éireann's electoral divisions "
    "by the 1918 ward, township, and district names they still carry; electoral divisions with no such name go to "
    "North Dublin north of the Liffey and otherwise to the nearest assigned division."
)


def fetch() -> None:
    raw.fetch(SOURCE, DIVISIONS, suffix=".geojson")


def parse() -> None:
    features = [
        (feature["properties"]["ENGLISH"], json.dumps(feature["geometry"]))
        for feature in json.loads(raw.cached(SOURCE, DIVISIONS).path.read_text())["features"]
    ]
    assigned = [
        (name, next((division for division, prefixes in DUBLIN.items() if name.upper().startswith(prefixes)), None))
        for name, _ in features
    ]
    with connect() as conn:
        seats = [
            code.removeprefix("1918:irl-")
            for (code,) in conn.execute(
                """select c.code from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'gb.pcon' and c.code like '1918:irl-%%'
                     and not exists (select 1 from area_geometry g where g.area_id = a.id
                                     and g.source_id not in (select id from source where publisher = 'Tailte Éireann'
                                                             and title like 'Redistribution of Seats (Ireland)%%'))"""
            ).fetchall()
        ]
        conn.execute("create temp table ed (name text, division text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy ed from stdin") as copy:
            for (name, geojson), (_, division) in zip(features, assigned, strict=True):
                copy.write_row((name, division, geojson))
        conn.execute(
            """alter table ed add column geom geometry;
               update ed set geom = st_multi(st_collectionextract(st_makevalid(
                 st_setsrid(st_force2d(st_geomfromgeojson(geojson)), 2157)), 3))"""
        )
        conn.execute(
            """update ed set division = 'north-county-dublin'
               where division is null and st_y(st_transform(st_pointonsurface(geom), 4326)) > %s""",
            [LIFFEY[1]],
        )
        conn.execute(
            """update ed set division = (select o.division from ed o where o.division is not null
                                         order by o.geom <-> ed.geom limit 1)
               where division is null"""
        )
        conn.execute(
            """create temp table old on commit drop as
               select replace(c.code, '1885:irl-', '') as slug, st_transform(g.geom, 2157) as geom
               from area_code c join area_geometry g on g.area_id = c.area_id
               where c.code like '1885:irl-%%' and g.valid @> date '1910-12-03'"""
        )
        conn.execute("create temp table seat (slug text, geom geometry) on commit drop")
        for slug in seats:
            parts = MERGES.get(slug, (slug,))
            if slug in DUBLIN:
                conn.execute("insert into seat select %s, st_union(geom) from ed where division = %s", [slug, slug])
            else:
                conn.execute(
                    "insert into seat select %s, st_union(geom) from old where slug = any(%s)", [slug, list(parts)]
                )
        missing = conn.execute("select slug from seat where geom is null").fetchall()
        if missing:
            raise ValueError(f"no shape for {missing}")
        shapes = conn.execute(
            """select slug, encode(st_asewkb(st_transform(geom, 4326)), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select slug, st_setsrid(st_coveragesimplify(geom, %s) over (), 2157) as geom
                     from (select slug, st_setsrid(st_coverageclean(st_multi(st_collectionextract(
                             st_makevalid(geom), 3))) over (), 2157) as geom from seat) clean) simple""",
            [TOLERANCE],
        ).fetchall()
    frame = pl.DataFrame(shapes, schema={"slug": pl.String, "geom": pl.String, "label_point": pl.String}, orient="row")
    print(frame.height, "seats")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        areas = {
            code.removeprefix("1918:irl-"): (area, start, end)
            for code, area, start, end in conn.execute(
                """select c.code, a.id, lower(c.valid), upper(c.valid) from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'gb.pcon' and c.code like '1918:irl-%'"""
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Synthetic shapes for Ireland's 1918–1922 constituencies outside the six counties, from the "
            "1885 shapes and Tailte Éireann's Dublin electoral divisions as the Redistribution of Seats (Ireland) "
            "Act 1918 defines them",
            polity="GB",
        )
        publisher = "Tailte Éireann"
        title = "Redistribution of Seats (Ireland) Act 1918 divisions: Electoral Divisions, Generalised 20m"
        source = cs.source(publisher, title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=publisher,
            title=title,
            url="https://data-osi.opendata.arcgis.com/",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution="© Tailte Éireann",
            notes=f"{NOTES} Statute: https://www.legislation.gov.uk/ukpga/Geo5/7-8/65 ({STATUTE}).",
        )
        cs.artifact(raw.cached(SOURCE, DIVISIONS), source)
        for slug, geom, point in frame.rows():
            area, start, end = areas[slug]
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=start,
                valid_to=end,
                geom=geom,
                label_point=point,
                quality="synthetic",
                source_id=source,
            )
        return cs.write()
