import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "gb.parish"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SERVICES = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
LAYERS = {
    2023: ("Parishes_May_2023_Boundaries_EW_BGC", "PAR23", "NMW", 11344, date(2023, 5, 4)),
    2024: ("Parishes_May_2024_Boundaries_EW_BGC", "PAR24", "NMW", 11337, date(2024, 5, 2)),
    2025: ("PARNCP_MAY_2025_EW_BGC", "PARNCP25", "NW", 11539, date(2025, 5, 1)),
    2026: ("PARNCP_MAY_2026_EW_BGC", "PARNCP26", "NW", 11542, date(2026, 5, 7)),
}
PAGE = 1000
TOLERANCE = 0.005
PUBLISHER = "Office for National Statistics"
LICENSE = "Open Government Licence v3.0"
LICENSE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
ATTRIBUTION = (
    "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
    "Contains OS data © Crown copyright and database right"
)
NOTE = (
    "Every civil parish in England and community in Wales, from the ONS parish boundary layers for the May 2023, "
    "2024, 2025, and 2026 elections (generalized to 20 m and clipped to the coastline). A shape runs from the first "
    "layer that shows it until a layer redraws it by more than half a percent of its area. Each parish is a polity "
    "because every English parish has a parish meeting (Local Government Act 1972, s. 9) and every Welsh community "
    "can hold a community meeting (s. 27). ONS does not say which parishes have a council rather than only a "
    "meeting, and it publishes no parish wards, so neither councils nor parish wards are recorded here."
)


def pages(year: int) -> list[str]:
    service, prefix, _, count, _ = LAYERS[year]
    return [
        f"{SERVICES}/{service}/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=27700&orderByFields={prefix}CD"
        f"&resultOffset={offset}&resultRecordCount={PAGE}&f=geojson"
        for offset in range(0, count, PAGE)
    ]


def fetch() -> None:
    for year in LAYERS:
        for url in pages(year):
            raw.fetch(SOURCE, url, suffix=".geojson")


def parse() -> None:
    names: list[dict[str, object]] = []
    with connect() as conn:
        conn.execute("create temp table feature (year int, code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for year, (_, prefix, welsh, count, _) in LAYERS.items():
                rows = [
                    feature
                    for url in pages(year)
                    for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
                ]
                if len(rows) != count:
                    raise ValueError(f"{year}: {len(rows)} features, expected {count}")
                for feature in rows:
                    properties = feature["properties"]
                    code = properties[f"{prefix}CD"]
                    if code[:3] not in ("E04", "W04"):
                        continue
                    names.append(
                        {
                            "year": year,
                            "code": code,
                            "name": properties[f"{prefix}NM"],
                            "name_cy": properties[f"{prefix}{welsh}"] or None,
                            "lad": properties.get("LAD26CD"),
                        }
                    )
                    copy.write_row((year, code, json.dumps(feature["geometry"])))
        conn.execute(
            """create temp table shape on commit drop as
               select year, code, st_multi(st_collectionextract(st_makevalid(st_union(
                        st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700))))), 3)) as geom
               from feature group by year, code"""
        )
        rows = conn.execute(
            """select year, code, st_area(geom), encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex'),
                      (select st_area(st_symdifference(geom, before.geom)) / nullif(st_area(geom), 0)
                       from shape before where before.code = shape.code and before.year < shape.year
                       order by before.year desc limit 1)
               from shape order by code, year"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "code": pl.String,
            "area": pl.Float64,
            "geom": pl.String,
            "label_point": pl.String,
            "change": pl.Float64,
        },
        orient="row",
    ).join(pl.DataFrame(names, infer_schema_length=None), on=["year", "code"])
    print(frame.group_by("year").len().sort("year").rows(), file=sys.stderr)
    print(
        frame.filter(pl.col("change").is_not_null())
        .select(pl.col("change").quantile(q).alias(str(q)) for q in (0.5, 0.9, 0.99, 0.999))
        .rows(),
        file=sys.stderr,
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "parishes.parquet")


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", folded.replace("&", " and ")).strip("-")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "parishes.parquet").sort("code", "year")
    years = sorted(LAYERS)
    if frame["change"].fill_null(0).max() != 0:
        raise ValueError("a layer redraws a parish under its old code")
    spans: dict[str, tuple[int, int]] = {}
    for code, first, last in (
        frame.group_by("code").agg(pl.col("year").min().alias("first"), pl.col("year").max().alias("last")).iter_rows()
    ):
        if (last - first + 1) != frame.filter(pl.col("code") == code).height:
            raise ValueError(f"{code} skips a layer")
        spans[code] = (first, last)
    latest = {
        row["code"]: row
        for row in frame.filter(pl.col("year") == pl.col("year").max().over("code")).iter_rows(named=True)
    }
    with connect() as conn:
        orphans = [(code, row["year"], row["label_point"]) for code, row in latest.items() if row["lad"] is None]
        lads = dict(
            conn.execute(
                """select distinct on (f.code) f.code, c.code
                   from unnest(%s::text[], %s::int[], %s::text[]) f(code, year, point)
                   join area a on a.scheme_code = 'gb.lad' join area_code c on c.area_id = a.id
                   join area_geometry g on g.area_id = a.id and g.valid @> make_date(f.year, 5, 1)
                   where st_intersects(g.geom, st_geomfromewkb(decode(f.point, 'hex')))
                   order by f.code, st_distance(st_centroid(g.geom), st_geomfromewkb(decode(f.point, 'hex')))""",
                [[orphan[index] for orphan in orphans] for index in range(3)],
            ).fetchall()
        )
        lads |= {code: row["lad"] for code, row in latest.items() if row["lad"] is not None}
        if missing := sorted(latest.keys() - lads.keys()):
            raise ValueError(f"no council area for {', '.join(missing)}")
        councils = dict(
            conn.execute(
                """select e.value, s.path from external_id e join slug s on s.entity_id = e.entity_id and s.is_current
                   where e.scheme = 'gb.polity'"""
            ).fetchall()
        )
        taken = {path for (path,) in conn.execute("select path from slug where path like 'gb/%%'").fetchall()}
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Every civil parish in England and community in Wales, May 2023 to May 2026, from ONS",
            polity="GB",
        )

        def name(entity: object, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = 'gb.parish'").fetchone() is None:
            cs.add("scheme", code="gb.parish", polity_id=gb, kind="administrative", rank=4, noun="parish or community")
        sources = {}
        for year, (service, _, _, _, _) in LAYERS.items():
            title = f"Parishes{' and Non Civil Parished Areas' if year >= 2025 else ''} (May {year}) Boundaries EW BGC"
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"{SERVICES}/{service}/FeatureServer",
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
                notes=NOTE,
            )
            for url in pages(year):
                cs.artifact(raw.cached(SOURCE, url), sources[year])

        def ends(last: int) -> date | None:
            return LAYERS[last + 1][4] if last < years[-1] else None

        successor: dict[str, str] = {}
        starts: dict[tuple[str, str, int], list[str]] = {}
        for code, (first, _) in spans.items():
            starts.setdefault((lads[code], slug(latest[code]["name"]), first), []).append(code)
        for code, (_, last) in spans.items():
            if last < years[-1]:
                heirs = starts.get((lads[code], slug(latest[code]["name"]), last + 1), [])
                if len(heirs) == 1:
                    successor[code] = heirs[0]
        root: dict[str, str] = {}
        for code in sorted(spans, key=lambda code: spans[code][0]):
            for prior, heir in successor.items():
                if heir == code:
                    root[code] = root.get(prior, prior)
            root.setdefault(code, code)

        polities: dict[str, object] = {}
        for code in sorted(spans, key=lambda code: (-spans[code][1], code)):
            first, last = spans[code]
            row = latest[code]
            area = cs.area("gb.parish", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.parish")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add(
                    "area_parent", area_id=area, parent_id=cs.area("gb.lad", lads[code]), valid_from=None, valid_to=None
                )
            name(area, row["name"])
            if code.startswith("W04") and row["name_cy"]:
                name(area, row["name_cy"], lang="cy", kind="native")
            for former in (
                frame.filter((pl.col("code") == code) & (pl.col("name") != row["name"]))["name"].unique().sort()
            ):
                name(area, former, kind="historical")
            for year, geom, point in (
                frame.filter(pl.col("code") == code).select("year", "geom", "label_point").rows()[:1]
            ):
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=LAYERS[year][4],
                    valid_to=ends(last),
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=sources[year],
                )
            key = root[code]
            polity = cs.key("gb.polity", key)
            if key not in polities:
                polities[key] = polity
                if polity in cs.minted:
                    cs.add("polity", id=polity, kind="submunicipal", iso_code=None)
                    cs.add("polity_parent", polity_id=polity, parent_id=cs.key("gb.polity", lads[code]))
                name(polity, row["name"])
                if code.startswith("W04") and row["name_cy"]:
                    name(polity, row["name_cy"], lang="cy", kind="native")
                path = f"{councils[lads[code]]}/{slug(row['name'])}"
                if path in taken:
                    path = f"{path}-{key.lower()}"
                taken.add(path)
                cs.add("slug", path=path, entity_id=polity)
            cs.add("polity_territory", polity_id=polity, area_id=area, valid_from=LAYERS[first][4], valid_to=ends(last))
        print(
            f"{len(spans)} areas, {len(polities)} polities, {len(successor)} codes succeeded by a namesake",
            file=sys.stderr,
        )
        plans = {
            vintage: id
            for id, vintage in conn.execute(
                "select id, vintage from boundary_set where scheme_code = 'gb.parish'"
            ).fetchall()
        }
        for year in years:
            cs.add(
                "boundary_set",
                **({"id": plans[str(year)]} if str(year) in plans else {}),
                scheme_code="gb.parish",
                vintage=str(year),
                valid_from=LAYERS[year][4],
                valid_to=ends(year),
                as_of=LAYERS[year][4],
            )
        return cs.write()
