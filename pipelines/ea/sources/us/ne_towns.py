import io
import json
import re
import zipfile
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ne_towns"
VERSION = "2"
STATES = {"09": "CT", "23": "ME", "25": "MA", "33": "NH", "44": "RI", "50": "VT"}
VINTAGES = {
    2010: "https://www2.census.gov/geo/tiger/GENZ2010/gz_2010_{}_060_00_500k.zip",
    2020: "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_{}_cousub_500k.zip",
    2024: "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_{}_cousub_500k.zip",
}
SPELLED = {"enosburg": "enosburgh", "alburg": "alburgh"}
MUNICIPAL = ("town", "city", "town city", "plantation", "reservation")
KINDS = {
    "pltn": "plantation",
    "purch": "purchase",
    "resvn": "reservation",
    "twp": "township",
    "loc": "location",
    "boro": "borough",
    "chartwp": "charter township",
    "muny": "municipality",
}


def urls(states: dict[str, str] = STATES) -> list[tuple[int, str, str]]:
    return [
        (year, fips, url.format(fips))
        for fips in states
        for year, url in VINTAGES.items()
        if not (fips == "09" and year == 2024)
    ]


def fetch(source: str = SOURCE, states: dict[str, str] = STATES, extra: tuple[str, ...] = ()) -> None:
    for url in (*(url for _, _, url in urls(states)), *extra):
        raw.fetch(source, url, suffix=".zip")


def parse(source: str = SOURCE, states: dict[str, str] = STATES, funcstat: dict[str, str] | None = None) -> None:
    parsed = DATA / "parsed" / source
    records = []
    for year, fips, url in urls(states):
        with zipfile.ZipFile(raw.cached(source, url).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
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
            folded = re.sub(r"[^a-z0-9]", "", proper.lower())
            records.append(
                {
                    "year": year,
                    "state": fips,
                    "code": code,
                    "town": f"{code[:5]}:{SPELLED.get(folded, folded)}",
                    "name": proper,
                    "kind": kind,
                    "geojson": json.dumps(shape.__geo_interface__),
                    **(
                        {"legal": str(row.get("NAMELSAD") or ""), "funcstat": funcstat.get(code)}
                        if funcstat is not None
                        else {}
                    ),
                }
            )
    frame = pl.DataFrame(records)
    paired = frame.filter(pl.len().over("year", "town") > 1)["town"].unique().to_list()
    frame = frame.with_columns(
        town=pl.when(pl.col("town").is_in(paired)).then(pl.col("town") + ":" + pl.col("kind")).otherwise("town")
    )
    frame = frame.with_columns(town=pl.col("town").sort_by("year").last().over("code"))
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
    parsed.mkdir(parents=True, exist_ok=True)
    latest.drop("geojson").join(shapes, on="town").write_parquet(parsed / "towns.parquet")
    codes.write_parquet(parsed / "codes.parquet")
    print(latest.group_by("state").len().sort("state"))
    print(codes.filter((pl.col("first") > 2010) | (pl.col("last") < pl.col("final"))).sort("town"))


def emit(
    source: str = SOURCE,
    version: str = VERSION,
    states: dict[str, str] = STATES,
    summary: str = "New England's towns, cities, plantations, and other county subdivisions from the Census Bureau's cartographic "
    "boundary files (2010, 2020, and 2024 vintages), as us.town areas with municipal polities, each code dated by the vintages that carry it",
    governed: Callable[[Changeset, set[str]], dict[str, str]] | None = None,
) -> Path:
    parsed = DATA / "parsed" / source
    towns = pl.read_parquet(parsed / "towns.parquet").filter(pl.col("state").is_in(list(states)))
    codes = pl.read_parquet(parsed / "codes.parquet").filter(pl.col("state").is_in(list(states)))
    distinct = "funcstat" in towns.columns
    with connect() as conn:
        cs = Changeset(conn, pipeline=source, version=version, summary=summary, polity="US")
        for fips in states:
            cs.scope_units("us.town", f"{fips}%")
        existing = {
            code: str(area)
            for code, area in conn.execute(
                """select c.code, c.area_id from area_code c join area a on a.id = c.area_id
                   join changeset x on x.id = a.changeset_id
                   where a.scheme_code = 'us.town' and left(c.code, 2) = any(%s) and x.pipeline <> %s""",
                [list(states), source],
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
                [list(states)],
            ).fetchall()
        }
        slugs = {
            path
            for (path,) in conn.execute(
                "select s.path from slug s join changeset c on c.id = s.changeset_id where c.pipeline <> %s", [source]
            ).fetchall()
        }
        governments = governed(cs, slugs) if governed is not None else {}
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        if census in cs.minted:
            raise ValueError("the Census Bureau's cartographic boundary source must already exist")
        for _, _, url in urls(states):
            cs.artifact(raw.cached(source, url), census)
        dated = {town: rows for (town,), rows in codes.group_by("town")}
        keys = set(towns["town"])
        counties = dict(
            conn.execute(
                """select c.code, regexp_replace(n.text, ',[^,]*$', '') from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.kind = 'official' and n.is_preferred
                   where a.scheme_code = 'us.county' and left(c.code, 2) = any(%s)""",
                [list(states)],
            ).fetchall()
        )
        labels: dict[str, str] = {}
        if distinct:
            rows = list(towns.iter_rows(named=True))
            legal = {
                row["town"]: re.sub(r"\b([a-z])", lambda match: match[1].upper(), row["legal"] or row["name"])
                for row in rows
            }
            names = Counter((row["state"], row["name"]) for row in rows if row["code"] not in existing)
            legals = Counter((row["state"], legal[row["town"]]) for row in rows if row["code"] not in existing)
            for state, text in conn.execute(
                """select left(c.code, 2), regexp_replace(n.text, ',[^,]*$', '') from area a
                   join area_code c on c.area_id = a.id join changeset x on x.id = a.changeset_id
                   join name n on n.entity_id = a.id and n.kind = 'official' and n.is_preferred
                   where a.scheme_code in ('us.town', 'us.place') and left(c.code, 2) = any(%s) and x.pipeline <> %s""",
                [list(states), source],
            ).fetchall():
                names[(state, text)] += 1
                legals[(state, text)] += 1
            for row in rows:
                labels[row["town"]] = (
                    row["name"]
                    if names[(row["state"], row["name"])] == 1 and row["code"] not in governments
                    else legal[row["town"]]
                    if legals[(row["state"], legal[row["town"]])] == 1 and row["code"] not in governments
                    else f"{legal[row['town']]} ({counties.get(row['code'][:5], row['code'][:5])})"
                )
        created = reused = skipped = polities = 0
        for row in towns.iter_rows(named=True):
            state, abbreviation = row["state"], states[row["state"]]
            rows = dated[row["town"]]
            known = [existing[code] for code in rows["code"] if code in existing]
            place = places.get((state, re.sub(r"[^a-z]", "", row["name"].lower())))
            if not distinct and not known and place is not None and row["kind"] in ("city", "town city"):
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
            if distinct:
                municipal = row["funcstat"] == "A"
            display = (
                labels[row["town"]]
                if distinct
                else f"{proper} {kind.title()}"
                if kind in ("town", "city")
                and f"{row['town'].rsplit(':', 1)[0]}:{'city' if kind == 'town' else 'town'}" in keys
                else proper
                if kind in ("town", "city", "town city")
                else f"{proper} {kind.title() if municipal else kind}"
            )
            cs.add("area", id=area, scheme_code="us.town")
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.county", row["code"][:5]))
            cs.add(
                "name", entity_id=area, text=f"{display}, {abbreviation}", lang="en", kind="official", is_preferred=True
            )
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
                polities += 1
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
                polity_id=polity or governments.get(row["code"]) or str(cs.polity(f"US-{abbreviation}")),
                scheme_code="us.town",
                code=row["code"],
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        print(
            f"{created} towns created, {polities} with polities, {reused} already held, {skipped} cities already held as places"
        )
        return cs.write()
