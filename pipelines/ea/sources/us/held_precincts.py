import io
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.held_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LICENSE = (
    "Pending: no license stated by the publisher; used on the archive owner's instruction of 2026-10-17 "
    "until permission is confirmed or refused"
)
NOTES = "Loaded onto us.precinct as default shapes on the archive owner's instruction; pull them if refused."
ODP = "https://raw.githubusercontent.com/opendataphilly/odp-data-storage/master/philadelphia-wards-divisions/"
JEFFCO = "https://gisportal.jeffco.us/server/rest/services/Hosted/County_Precinct_Archive_{}_Static/FeatureServer/{}"
PHILADELPHIA = "https://services.arcgis.com/fLeGjb7u4uXqeF9q/arcgis/rest/services/Political_Divisions/FeatureServer/0"
WASHOE = "https://gisenterprise.washoecounty.gov/server/rest/services/WashoeGIS/Voter_Precincts/MapServer/0"
MGGG = "https://raw.githubusercontent.com/mggg-states/MA-shapefiles/HEAD/MA_precincts_12_16.zip"
VEST = (date(2016, 11, 8), date(2018, 11, 6), date(2020, 11, 3))
GENERALS = {year: date(year, 11, 2 + (7 - date(year, 11, 1).weekday()) % 7) for year in range(2000, 2026, 2)}
TOLERANCE = 0.00001
PHILLY = {
    "2005": "2005/2005",
    "2008-12": "2008/2008-12",
    "2009": "2009/2009",
    "2010": "2010/2010",
    "2011": "2011/2011",
    "2012-09": "2012/2012-09",
    "2013-02": "2013/2013-02",
    "2016": "2016/2016",
    "2018-01": "2018-01/2018-01",
    "2018-08": "2018-08/2018-08",
    "2019-09": "2019-09/2019-09",
}
JEFFERSON = {
    1996: (54, 1996),
    1998: (53, 1998),
    2000: (52, 2000),
    2002: (51, 2002),
    2004: (50, 2004),
    2007: (49, 2007),
    2008: (48, 2008),
    2010: (47, 2011),
    2012: (46, 2012),
    2015: (45, 2013),
    2018: (44, 2018),
    2019: (43, 2019),
    2021: (42, 2021),
    2022: (41, 2022),
}


def layers() -> dict[str, dict[str, object]]:
    found: dict[str, dict[str, object]] = {}
    vintages = list(PHILLY)
    for index, vintage in enumerate(vintages):
        start = date.fromisoformat(f"{vintage}-01" if "-" in vintage else f"{vintage}-01-01")
        end = (
            date.fromisoformat(
                f"{vintages[index + 1]}-01" if "-" in vintages[index + 1] else f"{vintages[index + 1]}-01-01"
            )
            if index + 1 < len(vintages)
            else date(2021, 1, 1)
        )
        found[f"philadelphia:{vintage}"] = {
            "url": f"{ODP}{PHILLY[vintage]}-ward-divisions.zip",
            "srid": 2272,
            "start": start,
            "end": end,
            "precision": "day" if "-" in vintage else "year",
            "county": "42101",
            "publisher": "City of Philadelphia",
            "title": f"Political Ward Divisions ({vintage})",
            "page": f"https://github.com/opendataphilly/odp-data-storage/tree/master/philadelphia-wards-divisions/{PHILLY[vintage].split('/')[0]}",
        }
    found["philadelphia:current"] = {
        "url": f"{PHILADELPHIA}/query?where=1%3D1&outFields=*&outSR=4326&f=geojson",
        "meta": f"{PHILADELPHIA}?f=json",
        "srid": 4326,
        "start": date(2021, 1, 1),
        "end": None,
        "precision": "year",
        "county": "42101",
        "publisher": "City of Philadelphia",
        "title": "Political Divisions",
        "page": PHILADELPHIA,
    }
    years = list(JEFFERSON)
    for index, year in enumerate(years):
        base = JEFFCO.format(year, JEFFERSON[year][0])
        found[f"jefferson:{year}"] = {
            "url": f"{base}/query?where=1%3D1&outFields=*&outSR=4326&f=geojson",
            "meta": f"{base}?f=json",
            "srid": 4326,
            "start": date(JEFFERSON[year][1], 1, 1),
            "end": date(JEFFERSON[years[index + 1]][1], 1, 1) if index + 1 < len(years) else None,
            "precision": "year",
            "county": "08059",
            "publisher": "Jefferson County, Colorado, Clerk and Recorder's Elections Office",
            "title": f"County Precinct Archive {year}",
            "page": base,
        }
    found["washoe:current"] = {
        "url": f"{WASHOE}/query?where=1%3D1&outFields=*&outSR=4326&f=geojson",
        "meta": f"{WASHOE}?f=json",
        "srid": 4326,
        "start": date(2024, 1, 1),
        "end": None,
        "precision": "year",
        "county": "32031",
        "publisher": "Washoe County Registrar of Voters",
        "title": "Voter Precincts",
        "page": WASHOE,
    }
    found["massachusetts:2012"] = {
        "url": MGGG,
        "srid": 6492,
        "start": date(2012, 1, 1),
        "end": date(2022, 1, 1),
        "precision": "year",
        "county": None,
        "publisher": "MGGG Redistricting Lab",
        "title": "Massachusetts precincts 2012–2016",
        "page": "https://github.com/mggg-states/MA-shapefiles",
    }
    return found


def fetch() -> None:
    for layer in layers().values():
        raw.fetch(SOURCE, str(layer["url"]), suffix=".zip" if str(layer["url"]).endswith(".zip") else ".geojson")
        if "meta" in layer:
            raw.fetch(SOURCE, str(layer["meta"]), suffix=".json")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("create temp table prec (key text, label text, srid int, text text)")
        for name, layer in layers().items():
            path = raw.cached(SOURCE, str(layer["url"])).path
            rows = []
            if str(layer["url"]).endswith(".zip"):
                with zipfile.ZipFile(path) as archive:
                    members = [member for member in archive.namelist() if not member.startswith("__MACOSX")]
                    shp = next(member for member in members if member.lower().endswith(".shp"))
                    dbf = next(member for member in members if member.lower() == shp[:-4].lower() + ".dbf")
                    reader = shapefile.Reader(shp=io.BytesIO(archive.read(shp)), dbf=io.BytesIO(archive.read(dbf)))
                    fields = [field[0] for field in reader.fields[1:]]
                    for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                        row = dict(zip(fields, list(record), strict=True))
                        if name.startswith("philadelphia"):
                            key = str(row.get("WARD_DIVSN") or row["DIVISION_N"]).zfill(4)
                            label = f"Ward {int(key[:2])} Division {int(key[2:])}"
                        else:
                            town, ward, precinct = (str(row[field]).strip() for field in ("TOWN", "WARD", "PRECINCT"))
                            ward = "" if ward in ("-", "") else ward.lstrip("0")
                            key = f"{town.upper()}|{ward}|{precinct.lstrip('0')}"
                            label = f"{town} {'Ward ' + ward + ' ' if ward else ''}Precinct {precinct}"
                        rows.append((key, label, layer["srid"], json.dumps(shape.__geo_interface__)))
            else:
                for feature in json.loads(path.read_text())["features"]:
                    properties, geometry = feature["properties"], feature["geometry"]
                    if geometry is None:
                        continue
                    if name.startswith("philadelphia"):
                        number = str(properties["division_num"]).zfill(4)
                        key, label = number, f"Ward {int(number[:2])} Division {int(number[2:])}"
                    elif name.startswith("jefferson"):
                        code = next(
                            str(properties[field])
                            for field in ("cpcnt", "cpcnt2010rs", "cpcnt2011", "countyprecinct")
                            if properties.get(field) not in (None, "")
                        )
                        key = str(int(float(code)))
                        label = f"Precinct {key}"
                    else:
                        key = str(int(float(properties["PRECINCT"])))
                        label = f"Precinct {key}"
                    rows.append((key, label, 4326, json.dumps(geometry)))
            with conn.cursor().copy("copy prec (key, label, srid, text) from stdin") as copy:
                for row in rows:
                    copy.write_row(row)
            frame = pl.DataFrame(
                conn.execute(
                    """with source as (
                         select key, min(label) as label, st_union(st_makevalid(st_transform(st_setsrid(st_force2d(
                                  st_geomfromgeojson(text)), srid), 4326))) as geom
                         from prec group by key),
                       simplified as (
                         select key, label, geom as original, st_coveragesimplify(geom, %s) over () as geom
                         from source),
                       shapes as (
                         select key, label, st_setsrid(st_multi(st_collectionextract(st_makevalid(
                                  case when geom is null or st_isempty(geom) then original else geom end), 3)), 4326) as geom
                         from simplified)
                       select s.key, s.label, encode(st_asewkb(s.geom), 'hex'), encode(st_asewkb(st_pointonsurface(s.geom)), 'hex'),
                              (select c.code from area a join area_code c on c.area_id = a.id
                               join area_geometry g on g.area_id = a.id
                               where a.scheme_code = 'us.county' and c.code ~ '^25[0-9]{3}$' and st_intersects(g.geom, st_pointonsurface(s.geom))
                               limit 1)
                       from shapes s where not st_isempty(s.geom)"""
                    if name.startswith("massachusetts")
                    else """with source as (
                         select key, min(label) as label, st_union(st_makevalid(st_transform(st_setsrid(st_force2d(
                                  st_geomfromgeojson(text)), srid), 4326))) as geom
                         from prec group by key),
                       simplified as (
                         select key, label, geom as original, st_coveragesimplify(geom, %s) over () as geom
                         from source)
                       select key, label, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex'), null
                       from (select key, label, st_setsrid(st_multi(st_collectionextract(st_makevalid(
                                      case when geom is null or st_isempty(geom) then original else geom end), 3)), 4326) as geom
                             from simplified) shapes
                       where not st_isempty(geom)""",
                    [TOLERANCE],
                ).fetchall(),
                schema=["key", "label", "geom", "point", "county"],
                orient="row",
            ).with_columns(county=pl.col("county").fill_null(pl.lit(layer["county"])))
            conn.execute("truncate prec")
            frame.sort("key").write_parquet(PARSED / f"{name.replace(':', '_')}.parquet")
            print(f"{name}: {len(rows)} features, {frame.height} precincts", flush=True)
        units = pl.DataFrame(
            conn.execute(
                """select u.code, c.pipeline from reporting_unit u join changeset c on c.id = u.changeset_id
                   where c.pipeline in ('us.medsl_precincts', 'us.oe_precincts') and u.scheme_code = 'us.precinct'
                     and u.code ~ '^20[0-9]{2}:(PA:42101|CO:08059|NV:32031|MA:25[0-9]{3}):'"""
            ).fetchall(),
            schema=["code", "pipeline"],
            orient="row",
        )
        numbers = {
            (year, number): division
            for year, number, division in units.filter(pl.col("code").str.contains(r"^20(18|20):PA:42101:"))
            .select(
                year=pl.col("code").str.slice(0, 4).cast(pl.Int32),
                number=pl.col("code").str.extract(r":(\d+)_\d+_(\d{4})$", 1),
                division=pl.col("code").str.extract(r":(\d+)_\d+_(\d{4})$", 2),
            )
            .drop_nulls()
            .iter_rows()
        }
        keys = {name: set(pl.read_parquet(PARSED / f"{name.replace(':', '_')}.parquet")["key"]) for name in layers()}
        counties = {
            name: dict(
                pl.read_parquet(PARSED / f"{name.replace(':', '_')}.parquet").select("key", "county").iter_rows()
            )
            for name in layers()
        }
        towns = {key.split("|")[0] for key in keys["massachusetts:2012"]}
        links = []
        tally: dict[tuple[int, str], list[int]] = {}
        for code, _ in units.iter_rows():
            parts = code.split(":")
            year, state, county, name = int(parts[0]), parts[1], parts[2], parts[-1].strip()
            day = GENERALS[year]
            if day in VEST:
                continue
            prefix = {"PA": "philadelphia", "CO": "jefferson", "NV": "washoe", "MA": "massachusetts"}[state]
            layer = next(
                (
                    key
                    for key, value in layers().items()
                    if key.startswith(prefix) and value["start"] <= day and (value["end"] is None or day < value["end"])
                ),
                None,
            )
            key = None
            if state == "PA":
                if match := re.fullmatch(r"\d+ PHILADELPHIA 0*(\d+) 0*(\d+)", name):
                    key = f"{int(match[1]):02d}{int(match[2]):02d}"
                elif match := re.fullmatch(r"\d+_\d+_(\d{4})", name):
                    key = match[1]
                elif re.fullmatch(r"\d+", name):
                    key = numbers.get((2020, name)) if year >= 2022 else numbers.get((2018, name))
            elif state == "CO" and re.fullmatch(r"\d{10}", name):
                key = str(int(name))
            elif state == "NV" and (match := re.search(r"(\d{4})(?: \(MP\))?$", name)):
                key = str(int(match[1]))
            elif state == "MA" and layer is not None:
                town = max((town for town in towns if name.upper().startswith(f"{town} ")), key=len, default=None)
                rest = name.upper()[len(town or "") :].strip()
                ward = re.fullmatch(r"W?0*(\d+)\s*-?\s*P?\s*0*([A-Z0-9]+)", rest)
                key = next(
                    (
                        candidate
                        for candidate in (
                            f"{town}||{rest.lstrip('0')}",
                            f"{town}|{ward[1]}|{ward[2]}" if ward is not None else None,
                        )
                        if candidate in keys[layer]
                    ),
                    None,
                )
            tally.setdefault((year, prefix), [0, 0])[1] += 1
            if (
                layer is not None
                and key is not None
                and key in keys[layer]
                and (state != "MA" or counties[layer].get(key) == county)
            ):
                tally[(year, prefix)][0] += 1
                start = layers()[layer]["start"]
                links.append((code, f"{start}:{state}:{counties[layer][key]}:{key}"))
        frame = pl.DataFrame(links, schema=["code", "area"], orient="row")
        frame = frame.filter(pl.len().over(["area", pl.col("code").str.slice(0, 4)]) == 1)
        frame.write_parquet(PARSED / "links.parquet")
        for (year, prefix), (linked, total) in sorted(tally.items()):
            print(f"{year} {prefix}: {linked} of {total} units", flush=True)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Precinct shapes held for unclear licenses, loaded on the archive owner's instruction: "
            "Philadelphia's divisions 2005–today, Jefferson County, Colorado's precincts 1996–2022, Washoe County's "
            "current precincts, and MGGG's Massachusetts precincts 2012–2021",
            polity="US",
        )
        counties = {
            code: area
            for code, area in conn.execute(
                """select c.code, a.id::text from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'us.county' and c.code ~ '^(42101|08059|32031|25[0-9]{3})$'"""
            ).fetchall()
        }
        names = {"42101": "Philadelphia", "08059": "Jefferson County", "32031": "Washoe County"}
        for name, layer in layers().items():
            path = raw.cached(SOURCE, str(layer["url"]))
            retrieved = path.retrieved_at.date()
            start, end = layer["start"], layer["end"]
            assert isinstance(start, date)
            stop = end if isinstance(end, date) else retrieved + timedelta(days=1)
            publisher, title = str(layer["publisher"]), str(layer["title"])
            source = cs.source(publisher, title)
            mggg = name.startswith("massachusetts")
            cs.add(
                "source",
                id=source,
                kind="academic" if mggg else "official_body",
                publisher=publisher,
                title=title,
                url=str(layer["page"]),
                license="Open Database License (ODbL) 1.0" if mggg else LICENSE,
                attribution=(
                    "Contains information from MGGG Redistricting Lab, which is made available here under the "
                    "Open Database License (ODbL)."
                    if mggg
                    else "Credit Jefferson County IT Services (GIS Services) and the Clerk and Recorder's Elections Office"
                    if name.startswith("jefferson")
                    else publisher
                ),
                notes=(
                    "Share-alike; loaded on the archive owner's instruction of 2026-10-17. The 2012–2016 file is the "
                    "Secretary of the Commonwealth's precinct shapefile as MGGG processed it."
                    if mggg
                    else NOTES
                ),
            )
            cs.artifact(path, source)
            if "meta" in layer:
                cs.artifact(raw.cached(SOURCE, str(layer["meta"])), source)
            frame = pl.read_parquet(PARSED / f"{name.replace(':', '_')}.parquet").filter(
                pl.col("county").is_in(list(counties))
            )
            state = {"philadelphia": "PA", "jefferson": "CO", "washoe": "NV", "massachusetts": "MA"}[name.split(":")[0]]
            frame = frame.with_columns(
                code=pl.format("{}:{}:{}:{}", pl.lit(str(start)), pl.lit(state), "county", "key")
            )
            ids = [str(cs.area("us.precinct", code)) for code in frame["code"]]
            cs.frame("area", pl.DataFrame({"id": ids, "scheme_code": "us.precinct"}))
            cs.frame(
                "area_code",
                pl.DataFrame({"area_id": ids, "code": frame["code"]}).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            cs.frame(
                "area_parent",
                pl.DataFrame(
                    {"area_id": ids, "parent_id": [counties[county] for county in frame["county"]]}
                ).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            days = [day for day in VEST if start < day < stop]
            for since, until in zip([start, *(day + timedelta(days=1) for day in days)], [*days, stop], strict=True):
                if since >= until:
                    continue
                cs.frame(
                    "area_geometry",
                    pl.DataFrame({"area_id": ids, "geom": frame["geom"], "label_point": frame["point"]}).with_columns(
                        valid_from=pl.lit(since),
                        valid_from_precision=pl.lit(layer["precision"] if since == start else "day"),
                        valid_to=pl.lit(until),
                        valid_to_precision=pl.lit("day"),
                        quality=pl.lit("academic" if mggg else "official"),
                        source_id=pl.lit(str(source)),
                    ),
                )
            period = f"{start.year}–{stop.year if end is not None else 'today'}"
            cs.frame(
                "name",
                pl.DataFrame(
                    {
                        "entity_id": ids,
                        "text": [
                            f"{label}, {names.get(county, 'Massachusetts')} ({period})"
                            for label, county in frame.select("label", "county").iter_rows()
                        ],
                    }
                ).with_columns(is_preferred=pl.lit(True), lang=pl.lit("en"), kind=pl.lit("official")),
            )
            print(f"{name}: {frame.height} precincts, {start} to {stop}")
        return cs.write()
