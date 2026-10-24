import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ne_towns"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
STATES = {"09": "CT", "23": "ME", "25": "MA", "33": "NH", "44": "RI", "50": "VT"}
VINTAGES = {
    2010: "https://www2.census.gov/geo/tiger/GENZ2010/gz_2010_{}_060_00_500k.zip",
    2020: "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_{}_cousub_500k.zip",
    2024: "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_{}_cousub_500k.zip",
}
SPELLED = {"enosburg": "enosburgh", "alburg": "alburgh"}
MUNICIPAL = ("town", "city", "town city", "plantation", "reservation")
KINDS = {"pltn": "plantation", "purch": "purchase", "resvn": "reservation", "twp": "township", "loc": "location"}


def urls() -> list[tuple[int, str, str]]:
    return [
        (year, fips, url.format(fips))
        for fips in STATES
        for year, url in VINTAGES.items()
        if not (fips == "09" and year == 2024)
    ]


def fetch() -> None:
    for _, _, url in urls():
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    records = []
    for year, fips, url in urls():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            row = record.as_dict()
            code = str(row.get("GEOID") or "")[-10:] or f"{row['STATE']}{row['COUNTY']}{row['COUSUB']}"
            if code.endswith("00000"):
                continue
            proper = str(row["NAME"])
            kind = (str(row["NAMELSAD"])[len(proper) :] if "NAMELSAD" in row else str(row["LSAD"])).strip().lower()
            kind = KINDS.get(kind, kind)
            if kind == "city" and proper.endswith(" Town"):
                proper, kind = proper.removesuffix(" Town"), "town city"
            folded = re.sub(r"[^a-z]", "", proper.lower())
            records.append(
                {
                    "year": year,
                    "state": fips,
                    "code": code,
                    "town": f"{code[:5]}:{SPELLED.get(folded, folded)}",
                    "name": proper,
                    "kind": kind,
                    "geojson": json.dumps(shape.__geo_interface__),
                }
            )
    frame = pl.DataFrame(records)
    paired = frame.filter(pl.len().over("year", "town") > 1)["town"].unique().to_list()
    frame = frame.with_columns(
        town=pl.when(pl.col("town").is_in(paired)).then(pl.col("town") + ":" + pl.col("kind")).otherwise("town")
    )
    latest = frame.sort("year").group_by("town").last()
    codes = frame.group_by("town", "code").agg(first=pl.col("year").min(), last=pl.col("year").max())
    ends = frame.group_by("state").agg(pl.col("year").max().alias("final"))
    codes = codes.join(latest.select("town", "state"), on="town").join(ends, on="state")
    with connect() as conn:
        shapes = pl.DataFrame(
            conn.execute(
                """select f.town, encode(st_asewkb(s.geom), 'hex'), encode(st_asewkb(st_pointonsurface(s.geom)), 'hex')
                   from unnest(%s::text[], %s::text[]) as f(town, geojson)
                   cross join lateral (select st_multi(st_collectionextract(st_makevalid(st_transform(
                     st_setsrid(st_geomfromgeojson(f.geojson), 4269), 4326)), 3)) as geom) s""",
                [latest["town"].to_list(), latest["geojson"].to_list()],
            ).fetchall(),
            schema=["town", "geom", "point"],
            orient="row",
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    latest.drop("geojson").join(shapes, on="town").write_parquet(PARSED / "towns.parquet")
    codes.write_parquet(PARSED / "codes.parquet")
    print(latest.group_by("state").len().sort("state"))
    print(codes.filter((pl.col("first") > 2010) | (pl.col("last") < pl.col("final"))).sort("town"))


def emit() -> Path:
    towns = pl.read_parquet(PARSED / "towns.parquet")
    codes = pl.read_parquet(PARSED / "codes.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New England's towns, cities, plantations, and other county subdivisions from the Census Bureau's cartographic "
            "boundary files (2010, 2020, and 2024 vintages), as us.town areas with municipal polities, each code dated by the vintages that carry it",
            polity="US",
        )
        existing = {
            code: str(area)
            for code, area in conn.execute(
                """select c.code, c.area_id from area_code c join area a on a.id = c.area_id
                   where a.scheme_code = 'us.town' and left(c.code, 2) = any(%s)""",
                [list(STATES)],
            ).fetchall()
        }
        places = {
            (state, re.sub(r"[^a-z]", "", text.lower())): str(polity)
            for state, text, polity in conn.execute(
                """select left(c.code, 2), n.text, p.id from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.kind = 'official'
                   join polity_territory t on t.polity_id = p.id join area a on a.id = t.area_id
                   join area_code c on c.area_id = a.id
                   where p.kind = 'municipality' and a.scheme_code = 'us.place' and left(c.code, 2) = any(%s)""",
                [list(STATES)],
            ).fetchall()
        }
        slugs = {path for (path,) in conn.execute("select path from slug").fetchall()}
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        if census in cs.minted:
            raise ValueError("the Census Bureau's cartographic boundary source must already exist")
        for _, _, url in urls():
            cs.artifact(raw.cached(SOURCE, url), census)
        dated = {town: rows for (town,), rows in codes.group_by("town")}
        created = reused = skipped = 0
        for row in towns.iter_rows(named=True):
            state, abbreviation = row["state"], STATES[row["state"]]
            rows = dated[row["town"]]
            known = [existing[code] for code in rows["code"] if code in existing]
            place = places.get((state, re.sub(r"[^a-z]", "", row["name"].lower())))
            if not known and place is not None and row["kind"] in ("city", "town city"):
                skipped += 1
                continue
            if known:
                reused += 1
                continue
            area = str(cs.area("us.town", row["code"]))
            for code, first, last, final in rows.select("code", "first", "last", "final").iter_rows():
                cs.add(
                    "area_code",
                    area_id=area,
                    code=code,
                    valid_from=None if first == 2010 else date(first, 1, 1),
                    valid_to=None if last == final else date(next(y for y in VINTAGES if y > last), 1, 1),
                )
            created += 1
            proper, kind = row["name"], row["kind"]
            municipal = kind in MUNICIPAL
            display = proper if kind in ("town", "city", "town city") else f"{proper} {kind.title() if municipal else kind}"
            cs.add("area", id=area, scheme_code="us.town")
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.county", row["code"][:5]))
            cs.add("name", entity_id=area, text=f"{display}, {abbreviation}", lang="en", kind="official", is_preferred=True)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=row["geom"],
                label_point=row["point"],
                quality="official",
                source_id=census,
            )
            polity = None
            if municipal:
                polity = str(cs.key("us.polity", f"town:{row['code']}"))
                path = f"us/{abbreviation.lower()}/{slug(display)}"
                for fallback in (f"{path}-{kind.replace(' ', '-')}", f"{path}-{row['code']}"):
                    if path in slugs:
                        path = fallback
                slugs.add(path)
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                cs.add("name", entity_id=polity, text=display, lang="en", kind="official", is_preferred=True)
                cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"US-{abbreviation}"))
                cs.add("slug", path=path, entity_id=polity)
                cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "reporting_unit",
                id=cs.unit("us.town", row["code"]),
                polity_id=polity or str(cs.polity(f"US-{abbreviation}")),
                scheme_code="us.town",
                code=row["code"],
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        print(f"{created} towns created, {reused} already held, {skipped} cities already held as places")
        return cs.write()
