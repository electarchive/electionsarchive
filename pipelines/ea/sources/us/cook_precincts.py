import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.cook_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CHICAGO = "https://data.cityofchicago.org/"
COOK = "https://datacatalog.cookcountyil.gov/"
LICENSE = "Unconfirmed: permission to redistribute requested on 2026-10-17, pending"
NOTES = (
    "Held out of public releases and default views until permission arrives: the shapes sit on the "
    "us.restricted_precinct scheme and link to units only as extra memberships."
)
LAYERS = {
    "chicago:2011": (
        CHICAGO,
        "6evd-yiph",
        "Chicago Board of Election Commissioners",
        "Boundaries - Ward Precincts (2011)",
        date(2011, 1, 1),
        date(2014, 3, 18),
    ),
    "chicago:2013": (
        CHICAGO,
        "nvke-umup",
        "Chicago Board of Election Commissioners",
        "Boundaries - Ward Precincts (2013-2022)",
        date(2014, 3, 18),
        date(2022, 6, 29),
    ),
    "chicago:2023": (
        CHICAGO,
        "6piy-vbxa",
        "Chicago Board of Election Commissioners",
        "Boundaries - Ward Precincts (2023-2024)",
        date(2022, 11, 8),
        date(2024, 11, 6),
    ),
    "cook:2009": (
        COOK,
        "2jm7-izpg",
        "Cook County Clerk",
        "Election Precinct - 2009",
        date(2010, 1, 1),
        date(2012, 1, 1),
    ),
    "cook:2011": (
        COOK,
        "8xd4-7sya",
        "Cook County Clerk",
        "Election Precincts - 2011",
        date(2012, 1, 1),
        date(2016, 1, 1),
    ),
    "cook:current": (
        COOK,
        "k7sw-w3b8",
        "Cook County Clerk",
        "Suburban Cook Election Precincts - Current",
        date(2022, 1, 1),
        None,
    ),
}
GENERALS = {
    2014: date(2014, 11, 4),
    2016: date(2016, 11, 8),
    2018: date(2018, 11, 6),
    2020: date(2020, 11, 3),
    2022: date(2022, 11, 8),
    2024: date(2024, 11, 5),
}
TOLERANCE = 0.00001


def urls(layer: str) -> tuple[str, str]:
    portal, view, *_ = LAYERS[layer]
    data = (
        f"{portal}download/{view}/application%2Fzip"
        if view == "6evd-yiph"
        else f"{portal}resource/{view}.geojson?$limit=100000"
    )
    return f"{portal}api/views/{view}.json", data


def fetch() -> None:
    for layer in LAYERS:
        meta, data = urls(layer)
        raw.fetch(SOURCE, meta, suffix=".json")
        raw.fetch(SOURCE, data, suffix=".kmz" if data.endswith("zip") else ".geojson")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("create temp table prec (key text, label text, kind text, text text)")
        for layer in LAYERS:
            path = raw.cached(SOURCE, urls(layer)[1]).path
            rows = []
            if layer == "chicago:2011":
                with zipfile.ZipFile(path) as archive:
                    root = ET.fromstring(archive.read("doc.kml"))
                ns = {"k": "http://www.opengis.net/kml/2.2"}
                for mark in root.iter("{http://www.opengis.net/kml/2.2}Placemark"):
                    number = int(mark.findtext("k:name", namespaces=ns) or "0")
                    geometry = mark.find("k:MultiGeometry", ns)
                    if geometry is None:
                        geometry = mark.find("k:Polygon", ns)
                    if geometry is None:
                        continue
                    rows.append(
                        (
                            f"{number // 1000}-{number % 1000}",
                            f"Ward {number // 1000} Precinct {number % 1000}",
                            "kml",
                            ET.tostring(geometry, encoding="unicode").replace("ns0:", "").replace(":ns0", ""),
                        )
                    )
            else:
                for feature in json.loads(path.read_text())["features"]:
                    properties, geometry = feature["properties"], feature["geometry"]
                    if geometry is None:
                        continue
                    if layer.startswith("chicago"):
                        ward, precinct = int(float(properties["ward"])), int(float(properties["precinct"]))
                        key, label = f"{ward}-{precinct}", f"Ward {ward} Precinct {precinct}"
                    else:
                        number = int(float(properties.get("precinctid") or properties["idpct"]))
                        key = str(number)
                        label = (
                            properties.get("name")
                            if layer == "cook:current"
                            else f"{properties['name']} {number % 100000}"
                        )
                    rows.append((key, label, "geojson", json.dumps(geometry)))
            with conn.cursor().copy("copy prec (key, label, kind, text) from stdin") as copy:
                for row in rows:
                    copy.write_row(row)
            frame = pl.DataFrame(
                conn.execute(
                    """with source as (
                         select key, min(label) as label, st_union(st_makevalid(st_setsrid(st_force2d(
                                  case when kind = 'kml' then st_geomfromkml(text) else st_geomfromgeojson(text) end),
                                  4326))) as geom
                         from prec group by key),
                       simplified as (
                         select key, label, geom as original, st_coveragesimplify(geom, %s) over () as geom
                         from source)
                       select key, label, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                       from (select key, label, st_setsrid(st_multi(st_collectionextract(st_makevalid(
                                      case when geom is null or st_isempty(geom) then original else geom end), 3)), 4326) as geom
                             from simplified) shapes
                       where not st_isempty(geom)""",
                    [TOLERANCE],
                ).fetchall(),
                schema=["key", "label", "geom", "point"],
                orient="row",
            )
            conn.execute("truncate prec")
            frame.sort("key").write_parquet(PARSED / f"{layer.replace(':', '_')}.parquet")
            print(f"{layer}: {len(rows)} features, {frame.height} precincts", flush=True)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Chicago's ward precincts of 2011, 2013, and 2023 and suburban Cook County's precincts of 2009, "
            "2011, and today, held out of releases until permission arrives, linked to MEDSL's and "
            "OpenElections' precincts",
            polity="US",
        )
        if conn.execute("select from scheme where code = 'us.restricted_precinct'").fetchone() is None:
            cs.add(
                "scheme",
                code="us.restricted_precinct",
                polity_id=cs.polity("US"),
                kind="precinct",
                rank=3,
                noun="precinct",
            )
        (cook,) = conn.execute(
            """select a.id::text from area a join area_code c on c.area_id = a.id
               where a.scheme_code = 'us.county' and c.code = '17031'"""
        ).fetchone() or (None,)
        if cook is None:
            raise ValueError("Cook County must already be an area")
        keys: dict[str, dict[str, str]] = {}
        for layer, (portal, view, publisher, title, start, end) in LAYERS.items():
            meta, data = urls(layer)
            retrieved = raw.cached(SOURCE, data).retrieved_at.date()
            source = cs.source(publisher, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=f"{portal}d/{view}",
                license=LICENSE,
                attribution=publisher,
                notes=NOTES,
            )
            cs.artifact(raw.cached(SOURCE, meta), source)
            cs.artifact(raw.cached(SOURCE, data), source)
            frame = pl.read_parquet(PARSED / f"{layer.replace(':', '_')}.parquet").with_columns(
                code=pl.format("{}:{}", pl.lit(layer), "key")
            )
            ids = [str(cs.area("us.restricted_precinct", code)) for code in frame["code"]]
            keys[layer] = dict(zip(frame["key"], ids, strict=True))
            cs.frame("area", pl.DataFrame({"id": ids, "scheme_code": "us.restricted_precinct"}))
            cs.frame(
                "area_code",
                pl.DataFrame({"area_id": ids, "code": frame["code"]}).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            cs.frame(
                "area_parent",
                pl.DataFrame({"area_id": ids, "parent_id": cook}).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            cs.frame(
                "area_geometry",
                pl.DataFrame(
                    {
                        "area_id": ids,
                        "geom": frame["geom"],
                        "label_point": frame["point"],
                        "valid_from": [
                            date(2023, 1, 10) if layer == "chicago:2023" and key in ("24-16", "24-20") else start
                            for key in frame["key"]
                        ],
                    }
                ).with_columns(
                    valid_from_precision=pl.lit("year" if start.month == start.day == 1 else "day"),
                    valid_to=pl.lit(end or retrieved + timedelta(days=1)),
                    valid_to_precision=pl.lit("year" if end is not None and end.month == end.day == 1 else "day"),
                    quality=pl.lit("official"),
                    source_id=pl.lit(str(source)),
                ),
            )
            cs.frame(
                "name",
                pl.DataFrame(
                    {
                        "entity_id": ids,
                        "text": [
                            f"{label.title() if label.isupper() else label}, "
                            f"{'Chicago' if layer.startswith('chicago') else 'Cook County'} ({start.year}"
                            f"{'' if end is None else f'–{end.year if end.month > 1 or end.day > 1 else end.year - 1}'})"
                            for label in frame["label"]
                        ],
                    }
                ).with_columns(is_preferred=pl.lit(True), lang=pl.lit("en"), kind=pl.lit("official")),
            )
            print(f"{layer}: {frame.height} precincts, {start} to {end or retrieved}")
        units = conn.execute(
            """select u.id::text, u.code from reporting_unit u join changeset c on c.id = u.changeset_id
               where c.pipeline in ('us.medsl_precincts', 'us.oe_precincts')
                 and u.code ~ '^(2014|2016|2018|2020|2022|2024):IL:17031:'"""
        ).fetchall()
        links = []
        tally: dict[tuple[int, str], list[int]] = {}
        for unit, code in units:
            year, name = int(code.split(":")[0]), code.split(":")[-1].strip()
            day = GENERALS[year]
            ward = re.fullmatch(r"(?i)ward\s*0*(\d+)\s*precinct\s*0*(\d+)", name)
            if ward is not None:
                part, key = "Chicago", f"{int(ward[1])}-{int(ward[2])}"
                layer = "chicago:2013" if day < date(2022, 6, 29) else "chicago:2023"
            elif re.fullmatch(r"\d{7}(\.0)?", name):
                part, key = "suburban Cook", str(int(float(name)))
                layer = "cook:2011" if year < 2016 else "cook:current" if year >= 2022 else None
            else:
                part, key, layer = "other", "", None
            area = keys.get(layer or "", {}).get(key)
            if layer == "chicago:2023" and day < date(2023, 1, 10) and key in ("24-16", "24-20"):
                area = None
            tally.setdefault((year, part), [0, 0])[1] += 1
            if area is not None:
                tally[(year, part)][0] += 1
                links.append((unit, area, day))
        for (year, part), (matched, total) in sorted(tally.items()):
            print(f"{year} {part}: {matched} of {total} units linked ({matched / total:.1%})")
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(links, schema=["unit_id", "area_id", "valid_from"], orient="row").with_columns(
                valid_to=pl.col("valid_from") + timedelta(days=1)
            ),
        )
        return cs.write()
