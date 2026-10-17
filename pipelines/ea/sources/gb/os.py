import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "gb.os"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HISTORIC = (
    "https://1897589978-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2FcNpJpLP8RROUaWVQo5ea"
    "%2Fuploads%2FzMpbQgTlp6SJqLcEQPGr%2Fboundary-line-historic-counties-shape.zip"
    "?alt=media&token=3fabf33b-34a6-45ff-b0d4-da5954451905"
)
LAYER = "Boundary-line-historic-counties_region"
ULSTER = (
    "https://services.arcgis.com/pMnvm7HXxTmNXxGi/arcgis/rest/services/"
    "OSNI_Open_Data_Largescale_Boundaries__County_Boundaries/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)
IRELAND = "https://data-osi.opendata.arcgis.com/api/download/v1/items/dc24df2a5ce84ee9a38d9afe8431ee9b/geojson?layers=1"
GEONAMES = tuple(f"https://download.geonames.org/export/dump/{country}.zip" for country in ("GB", "IE"))
OGL = ("Open Government Licence v3.0", "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/")
CC_BY = ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/")
SOURCES = {
    HISTORIC: (
        "Ordnance Survey",
        "Boundary-Line: historic counties",
        "https://www.ordnancesurvey.co.uk/products/boundary-line",
        *OGL,
        "Contains OS data © Crown copyright and database right",
        "official_body",
    ),
    ULSTER: (
        "Ordnance Survey of Northern Ireland",
        "OSNI Open Data: Largescale Boundaries, County Boundaries",
        "https://www.data.gov.uk/dataset/f6a923c8-06f1-47ee-a1d6-bf9dec71cb7e",
        *OGL,
        "Contains public sector information licensed under the Open Government Licence v3.0; © Crown copyright, LPS",
        "official_body",
    ),
    IRELAND: (
        "Tailte Éireann",
        "Counties: National Statutory Boundaries, Ungeneralised",
        "https://data-osi.opendata.arcgis.com/datasets/dc24df2a5ce84ee9a38d9afe8431ee9b_1",
        *CC_BY,
        "© Tailte Éireann",
        "official_body",
    ),
    GEONAMES[0]: (
        "GeoNames",
        "GeoNames gazetteer: GB and IE",
        "https://www.geonames.org/",
        *CC_BY,
        "GeoNames",
        "aggregator",
    ),
}
IRISH = {
    "Antrim": "ANTRIM",
    "Armagh": "ARMAGH",
    "Carlow": "CARLOW",
    "Cavan": "CAVAN",
    "Clare": "CLARE",
    "Cork": "CORK",
    "County Dublin": "DUBLIN",
    "County Galway": "GALWAY",
    "County Louth": "LOUTH",
    "Donegal": "DONEGAL",
    "Down": "DOWN",
    "Fermanagh": "FERMANAGH",
    "Kerry": "KERRY",
    "Kildare": "KILDARE",
    "Kilkenny": "KILKENNY",
    "King's County": "OFFALY",
    "Leitrim": "LEITRIM",
    "Limerick": "LIMERICK",
    "Londonderry": "LONDONDERRY",
    "Longford": "LONGFORD",
    "Mayo": "MAYO",
    "Meath": "MEATH",
    "Monaghan": "MONAGHAN",
    "Queen's County": "LAOIS",
    "Roscommon": "ROSCOMMON",
    "Sligo": "SLIGO",
    "Tipperary": "TIPPERARY",
    "Tyrone": "TYRONE",
    "Waterford": "WATERFORD",
    "Westmeath": "WESTMEATH",
    "Wexford": "WEXFORD",
    "Wicklow": "WICKLOW",
}
CITIES = ("Aberdeen", "Dundee", "Edinburgh", "Glasgow")
COUNTIES = {
    "Aberdeenshire": ("Aberdeenshire", "Aberdeen"),
    "Breconshire": ("Brecknockshire",),
    "Caernarvonshire": ("Carnarvonshire",),
    "Clackmannanshire and Kinross-shire": ("Clackmannanshire", "Kinross-shire"),
    "Elginshire and Nairnshire": ("Nairn",),
    "Forfarshire": ("Angus", "Dundee"),
    "Glamorganshire": ("Glamorgan",),
    "Haddingtonshire": ("East Lothian",),
    "Lanarkshire": ("Lanarkshire", "Glasgow"),
    "Linlithgowshire": ("West Lothian",),
    "Merioneth": ("Merionethshire",),
    "Midlothian": ("Midlothian", "Edinburgh"),
    "Montgomery": ("Montgomeryshire",),
    "Orkney and Shetland": ("Orkney", "Shetland"),
    "Peebles and Selkirk": ("Peebleshire", "Selkirkshire"),
}
DIRECTIONS = {
    "North East": (0.7, 0.7),
    "South East": (0.7, -0.7),
    "South West": (-0.7, -0.7),
    "North": (0.0, 1.0),
    "South": (0.0, -1.0),
    "East": (1.0, 0.0),
    "West": (-1.0, 0.0),
    "Mid": (0.0, 0.0),
}
RIDINGS = {
    "North Riding of Yorkshire": (0.1, 0.7),
    "East Riding of Yorkshire": (0.9, 0.0),
    "Northern West Riding of Yorkshire": (-0.6, 0.2),
    "Eastern West Riding of Yorkshire": (0.0, -0.3),
    "Southern West Riding of Yorkshire": (-0.3, -0.8),
}
BOROUGHS = {
    "Aberdeen": ("Aberdeen",),
    "Angus": ("Montrose Burghs",),
    "Anglesey": ("Beaumaris",),
    "Antrim": ("Belfast", "Carrickfergus", "Lisburn"),
    "Armagh": ("Armagh City",),
    "Ayrshire": ("Ayr Burghs", "Kilmarnock Burghs"),
    "Bedfordshire": ("Bedford",),
    "Berkshire": ("Abingdon", "Reading", "Wallingford", "Windsor"),
    "Brecknockshire": ("Brecon",),
    "Buckinghamshire": ("Aylesbury", "Buckingham", "Great Marlow", "Wycombe"),
    "Caithness": ("Wick Burghs",),
    "Cambridgeshire": ("Cambridge",),
    "Cardiganshire": ("Cardigan Boroughs",),
    "Carlow": ("Carlow City",),
    "Carmarthenshire": ("Carmarthen Boroughs",),
    "Carnarvonshire": ("Caernarvon Boroughs",),
    "Cheshire": ("Birkenhead", "City of Chester", "Macclesfield", "Stalybridge", "Stockport"),
    "Clare": ("Ennis",),
    "Cork": ("Bandon", "Cork City", "Kinsale", "Mallow", "Youghal"),
    "Cornwall": ("Bodmin", "Helston", "Launceston", "Liskeard", "Penryn and Falmouth", "St Ives", "Truro"),
    "County Dublin": ("Dublin",),
    "County Galway": ("Galway City",),
    "County Louth": ("Drogheda", "Dundalk"),
    "Cumberland": ("Carlisle", "Cockermouth", "Whitehaven"),
    "Denbighshire": ("Denbigh Boroughs",),
    "Derbyshire": ("Derby",),
    "Devon": ("Barnstaple", "Devonport", "Exeter", "Plymouth", "Tavistock", "Tiverton"),
    "Dorset": ("Bridport", "Dorchester", "Poole", "Shaftesbury", "Wareham", "Weymouth and Melcombe Regis"),
    "Down": ("Downpatrick", "Newry"),
    "Dumfriesshire": ("Dumfries Burghs",),
    "Dundee": ("Dundee",),
    "Durham": (
        "Darlington",
        "Durham",
        "Gateshead",
        "South Shields",
        "Stockton-on-Tees",
        "Sunderland",
        "The Hartlepools",
    ),
    "East Lothian": ("Haddington Burghs",),
    "Edinburgh": ("Edinburgh", "Leith Burghs"),
    "Essex": ("Colchester", "Harwich", "Maldon"),
    "Fermanagh": ("Enniskillen",),
    "Fife": ("Kirkcaldy Burghs", "St Andrews Burghs"),
    "Flintshire": ("Flint Boroughs",),
    "Glamorgan": ("Cardiff", "Merthyr Tydfil", "Swansea District"),
    "Glasgow": ("Glasgow",),
    "Gloucestershire": ("Bristol", "Cheltenham", "Cirencester", "Gloucester", "Stroud", "Tewkesbury"),
    "Hampshire": ("Andover", "Christchurch", "Lymington", "Petersfield", "Portsmouth", "Southampton", "Winchester"),
    "Herefordshire": ("Hereford", "Leominster"),
    "Hertfordshire": ("Hertford",),
    "Huntingdonshire": ("Huntingdon",),
    "Inverness-shire": ("Inverness Burghs",),
    "Isle of Wight": ("Newport (Isle of Wight)",),
    "Kent": ("Canterbury", "Chatham", "Dover", "Gravesend", "Greenwich", "Hythe", "Maidstone", "Rochester", "Sandwich"),
    "Kerry": ("Tralee",),
    "Kilkenny": ("Kilkenny City",),
    "Lancashire": (
        "Ashton-under-Lyne",
        "Blackburn",
        "Bolton",
        "Burnley",
        "Bury",
        "Clitheroe",
        "Liverpool",
        "Manchester",
        "Oldham",
        "Preston",
        "Rochdale",
        "Salford",
        "Warrington",
        "Wigan",
    ),
    "Leicestershire": ("Leicester",),
    "Limerick": ("Limerick City",),
    "Lincolnshire": ("Boston", "Grantham", "Great Grimsby", "Lincoln", "Stamford"),
    "Londonderry": ("Coleraine", "Londonderry City"),
    "Middlesex": ("Chelsea", "City of London", "Finsbury", "Hackney", "Marylebone", "Tower Hamlets", "Westminster"),
    "Monmouthshire": ("Monmouth Boroughs",),
    "Montgomeryshire": ("Montgomery Boroughs",),
    "Nairn": ("Elgin Burghs",),
    "Norfolk": ("King's Lynn", "Norwich"),
    "Northamptonshire": ("Northampton", "Peterborough"),
    "Northumberland": ("Berwick-upon-Tweed", "Morpeth", "Newcastle upon Tyne", "Tynemouth and North Shields"),
    "Nottinghamshire": ("East Retford", "Newark", "Nottingham"),
    "Oxfordshire": ("Banbury", "Oxford", "Woodstock"),
    "Pembrokeshire": ("Haverfordwest", "Pembroke Boroughs"),
    "Perthshire": ("Perth",),
    "Queen's County": ("Portarlington",),
    "Radnorshire": ("Radnor",),
    "Renfrewshire": ("Greenock", "Paisley"),
    "Roxburghshire": ("Hawick Burghs",),
    "Shropshire": ("Bridgnorth", "Ludlow", "Shrewsbury", "Wenlock"),
    "Sligo": ("Sligo Borough",),
    "Somerset": ("Bath", "Bridgwater", "Frome", "Taunton"),
    "Staffordshire": (
        "Dudley",
        "Lichfield",
        "Newcastle-under-Lyme",
        "Stafford",
        "Stoke-on-Trent",
        "Tamworth",
        "Walsall",
        "Wednesbury",
        "Wolverhampton",
    ),
    "Stirlingshire": ("Falkirk", "Stirling Burghs"),
    "Suffolk": ("Bury St Edmunds", "Eye", "Ipswich"),
    "Surrey": ("Guildford", "Lambeth", "Southwark"),
    "Sussex": ("Brighton", "Chichester", "Hastings", "Horsham", "Lewes", "Midhurst", "New Shoreham", "Rye"),
    "Tipperary": ("Cashel", "Clonmel"),
    "Tyrone": ("Dungannon",),
    "Warwickshire": ("Birmingham", "Coventry", "Warwick"),
    "Waterford": ("Dungarvan", "Waterford City"),
    "Westmeath": ("Athlone",),
    "Westmorland": ("Kendal",),
    "Wexford": ("New Ross", "Wexford Borough"),
    "Wigtownshire": ("Wigtown Burghs",),
    "Wiltshire": (
        "Calne",
        "Chippenham",
        "Cricklade",
        "Devizes",
        "Malmesbury",
        "Marlborough",
        "Salisbury",
        "Westbury",
        "Wilton",
    ),
    "Worcestershire": ("Bewdley", "Droitwich", "Evesham", "Kidderminster", "Worcester"),
    "Yorkshire": (
        "Beverley",
        "Bradford",
        "Dewsbury",
        "Halifax",
        "Huddersfield",
        "Kingston upon Hull",
        "Knaresborough",
        "Leeds",
        "Malton",
        "Middlesbrough",
        "Northallerton",
        "Pontefract",
        "Richmond (Yorks)",
        "Ripon",
        "Scarborough",
        "Sheffield",
        "Thirsk",
        "Wakefield",
        "Whitby",
        "York",
    ),
}
PLACES = {
    "Caernarvon Boroughs": "Caernarfon",
    "City of Chester": "Chester",
    "East Retford": "Retford",
    "Great Grimsby": "Grimsby",
    "Great Marlow": "Marlow",
    "Kingston upon Hull": "Hull",
    "Londonderry City": "Derry",
    "New Shoreham": "Shoreham-by-Sea",
    "Newport (Isle of Wight)": "Newport",
    "Penryn and Falmouth": "Penryn",
    "Radnor": "New Radnor",
    "Richmond (Yorks)": "Richmond",
    "Swansea District": "Swansea",
    "The Hartlepools": "Hartlepool",
    "Tynemouth and North Shields": "Tynemouth",
    "Wenlock": "Much Wenlock",
    "Weymouth and Melcombe Regis": "Weymouth",
    "Wycombe": "High Wycombe",
}
RADIUS = 3000
TOLERANCE = 25
ELECTION = date(1868, 11, 17)
UNION = date(1922, 12, 6)
REDISTRIBUTION = date(1885, 11, 24)


def fetch() -> None:
    raw.fetch(SOURCE, HISTORIC, suffix=".zip")
    raw.fetch(SOURCE, ULSTER, suffix=".geojson")
    raw.fetch(SOURCE, IRELAND, suffix=".geojson")
    for url in GEONAMES:
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, HISTORIC).path) as archive:
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{LAYER}.shp")),
            shx=io.BytesIO(archive.read(f"{LAYER}.shx")),
            dbf=io.BytesIO(archive.read(f"{LAYER}.dbf")),
        )
        parts = [
            (str(record["NAME"]), HISTORIC, 27700, json.dumps(shape.__geo_interface__))
            for record, shape in zip(reader.records(), reader.shapes(), strict=True)
        ]
    irish = {code: name for name, code in IRISH.items()}
    for url, field, srid in ((ULSTER, "CountyName", 4326), (IRELAND, "ENG_NAME_VALUE", 2157)):
        for feature in json.loads(raw.cached(SOURCE, url).path.read_bytes())["features"]:
            parts.append((irish[feature["properties"][field].upper()], url, srid, json.dumps(feature["geometry"])))
    located = {name: county for county, names in BOROUGHS.items() for name in names}
    searches = {name: PLACES.get(name, re.sub(r" (Boroughs|Burghs|City|Borough)$", "", name)) for name in located}
    wanted = {search.lower() for search in searches.values()}
    places = []
    for url in GEONAMES:
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            member = f"{url.rsplit('/', 1)[1].removesuffix('.zip')}.txt"
            for line in archive.read(member).decode().splitlines():
                fields = line.split("\t")
                if fields[6] in ("P", "A"):
                    for name in {name.lower() for name in (fields[1], fields[2], *fields[3].split(","))} & wanted:
                        places.append((name, float(fields[5]), float(fields[4]), int(fields[14] or 0), fields[6]))
    with connect() as conn:
        seats = conn.execute(
            """select c.code, n.text from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.is_preferred where a.scheme_code = 'gb.pcon'"""
        ).fetchall()
        known = {part for part, *_ in parts} | {"Isle of Wight"}
        divisions = []
        for code, name in seats:
            if name in located:
                continue
            if name in RIDINGS:
                divisions.append((code, "Yorkshire", *RIDINGS[name]))
                continue
            prefix = next((word for word in DIRECTIONS if name.startswith(f"{word} ")), None)
            members = COUNTIES.get(name.removeprefix(f"{prefix} ") if prefix else name)
            members = members or (name.removeprefix(f"{prefix} ") if prefix else name,)
            if any(member not in known for member in members):
                raise ValueError(f"cannot place {name}")
            divisions.append((code, "+".join(members), *DIRECTIONS[prefix or "Mid"]))
        codes = {name: code for code, name in seats}
        if missing := sorted(set(located) - set(codes)):
            raise ValueError(f"boroughs not in gb.pcon: {missing}")

        conn.execute("create temp table source_part (part text, url text, srid int, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy source_part from stdin") as copy:
            for row in parts:
                copy.write_row(row)
        conn.execute(
            """create temp table part on commit drop as
               select part, min(url) as url, st_multi(st_collectionextract(st_makevalid(st_union(
                        st_simplifypreservetopology(st_makevalid(st_transform(
                          st_setsrid(st_force2d(st_geomfromgeojson(geojson)), srid), 3035)), 20))), 3)) as geom
               from source_part group by part"""
        )
        conn.execute(
            """insert into part
               select 'Isle of Wight', min(url), st_multi(st_union(piece.geom))
               from part, lateral st_dump(geom) piece
               where part = 'Hampshire'
                 and st_y(st_transform(st_centroid(piece.geom), 4326)) < 50.77
                 and st_x(st_transform(st_centroid(piece.geom), 4326)) between -1.6 and -1.05"""
        )
        conn.execute(
            """update part set geom = st_multi(st_collectionextract(st_difference(
                 geom, (select geom from part where part = 'Isle of Wight')), 3))
               where part = 'Hampshire'"""
        )
        conn.execute("create temp table place (name text, x float, y float, population int, class text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy place from stdin") as copy:
            for row in places:
                copy.write_row(row)
        conn.execute("create temp table borough (code text, name text, part text, search text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy borough from stdin") as copy:
            for name, part in located.items():
                copy.write_row((codes[name], name, part, searches[name].lower()))
        conn.execute(
            """create temp table located on commit drop as
               select distinct on (b.code) b.code, b.part, b.name, b.name = any(%s) as city,
                      st_transform(st_setsrid(st_point(p.x, p.y), 4326), 3035) as point
               from borough b join place p on p.name = b.search
               join part c on c.part = b.part
                 and st_dwithin(c.geom, st_transform(st_setsrid(st_point(p.x, p.y), 4326), 3035), 2000)
               order by b.code, p.class = 'P' desc, p.population desc""",
            [list(CITIES)],
        )
        if unplaced := conn.execute("select name from borough where code not in (select code from located)").fetchall():
            raise ValueError(f"no GeoNames place for {sorted(name for (name,) in unplaced)}")
        conn.execute(
            """create temp table town on commit drop as
               select l.code, l.part,
                      st_multi(st_collectionextract(st_intersection(st_intersection(cell.geom,
                        st_buffer(l.point, %s)), c.geom), 3)) as geom
               from located l join part c on c.part = l.part,
               lateral (select v.geom from st_dump(st_voronoipolygons(
                   (select st_collect(point) from located where not city))) v
                 where st_contains(v.geom, l.point)) cell
               where not l.city""",
            [RADIUS],
        )
        conn.execute(
            """insert into town
               select l.code, l.part, st_multi(st_collectionextract(st_difference(c.geom,
                        coalesce((select st_union(t.geom) from town t where t.part = l.part),
                                 'SRID=3035;POLYGON EMPTY'::geometry)), 3))
               from located l join part c on c.part = l.part where l.city"""
        )
        conn.execute("create temp table division (code text, county text, dx float, dy float) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy division from stdin") as copy:
            for row in divisions:
                copy.write_row(row)
        conn.execute(
            """create temp table county on commit drop as
               select d.county, st_difference(st_union(c.geom),
                        coalesce((select st_union(t.geom) from town t
                                  where t.part = any(string_to_array(d.county, '+'))),
                                 'SRID=3035;POLYGON EMPTY'::geometry)) as geom
               from (select distinct county from division) d
               join part c on c.part = any(string_to_array(d.county, '+'))
               group by d.county"""
        )
        conn.execute(
            """create temp table seed on commit drop as
               select d.code, d.county, st_translate(st_centroid(c.geom),
                        d.dx * 0.3 * sqrt(st_area(c.geom)), d.dy * 0.3 * sqrt(st_area(c.geom))) as point
               from division d join county c using (county)"""
        )
        rows = conn.execute(
            """with shapes as (
                 select code, 'borough' as kind, geom from town
                 union all
                 select s.code, 'division', case when (select count(*) from seed o where o.county = s.county) = 1
                   then c.geom
                   else st_intersection(c.geom, (
                     select v.geom from st_dump(st_voronoipolygons(
                       (select st_collect(point) from seed o where o.county = s.county),
                       0, st_expand(c.geom, 100000))) v
                     where st_contains(v.geom, s.point))) end
                 from seed s join county c using (county)
               ),
               cleaned as (
                 select code, kind, st_setsrid(st_coveragesimplify(geom, %s) over (), 3035) as geom
                 from (select code, kind, st_setsrid(st_coverageclean(geom) over (), 3035) as geom
                       from (select code, kind, st_multi(st_collectionextract(st_makevalid(geom), 3)) as geom
                             from shapes) valid) clean
               ),
               whole as (select 'nation' as layer, 'GBI' as code, null as kind, st_union(geom) as geom from part)
               select layer, code, kind,
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select 'pcon' as layer, code, kind, geom from cleaned union all select * from whole) shapes""",
            [TOLERANCE],
        ).fetchall()
        homes = dict(conn.execute("select code, part from located").fetchall())
        homes |= {code: county.split("+")[0] for code, county, *_ in divisions}
        urls = dict(conn.execute("select part, url from part").fetchall())
    frame = pl.DataFrame(
        rows,
        schema={"layer": pl.String, "code": pl.String, "kind": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    ).with_columns(
        pl.when(pl.col("kind") == "borough")
        .then(pl.lit(GEONAMES[0]))
        .when(pl.col("layer") == "nation")
        .then(pl.lit(HISTORIC))
        .otherwise(pl.col("code").replace_strict(homes, default=None).replace_strict(urls, default=None))
        .alias("source")
    )
    pcon = frame.filter(pl.col("layer") == "pcon")
    print(pcon.group_by("kind").len().rows())
    if pcon["code"].n_unique() != len(seats) or pcon.height != len(seats) or frame["geom"].null_count():
        raise ValueError(f"{pcon.height} shapes for {len(seats)} constituencies")
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
                "Synthetic shapes for the 1868 constituencies: county divisions split from Ordnance Survey's historic "
                "counties and OSNI's and Tailte Éireann's Irish counties, and boroughs drawn around their GeoNames towns"
            ),
            polity="GB",
        )
        sources: dict[str, UUID] = {}
        for url, (publisher, title, page, license, license_url, attribution, kind) in SOURCES.items():
            sources[url] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[url],
                kind=kind,
                publisher=publisher,
                title=title,
                url=page,
                license=license,
                license_url=license_url,
                attribution=attribution,
            )
            for artifact in GEONAMES if url == GEONAMES[0] else (url,):
                cs.artifact(raw.cached(SOURCE, artifact), sources[url])
        areas = dict(
            conn.execute(
                """select c.code, a.id from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'gb.pcon'"""
            ).fetchall()
        )
        for layer, code, geom, point, source in frame.select("layer", "code", "geom", "label_point", "source").rows():
            cs.add(
                "area_geometry",
                area_id=cs.area("gb.nation", code) if layer == "nation" else areas[code],
                valid_from=None if layer == "nation" else ELECTION,
                valid_from_precision="day",
                valid_to=UNION if layer == "nation" else REDISTRIBUTION,
                valid_to_precision="day",
                geom=geom,
                label_point=point,
                quality="synthetic",
                source_id=sources[source],
            )
        existing = conn.execute(
            "select id from boundary_set where scheme_code = 'gb.pcon' and vintage = '1868'"
        ).fetchone()
        cs.add(
            "boundary_set",
            **({"id": existing[0]} if existing else {}),
            scheme_code="gb.pcon",
            vintage="1868",
            valid_from=ELECTION,
            valid_to=REDISTRIBUTION,
            as_of=ELECTION,
        )
        return cs.write()
