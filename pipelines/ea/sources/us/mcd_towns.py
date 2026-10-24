import io
import json
import os
import re
import zipfile
from collections import Counter
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ne_towns
from ea.store import DATA, connect

SOURCE = "us.mcd_towns"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
STATES = {"26": "MI", "27": "MN", "34": "NJ", "36": "NY", "42": "PA", "55": "WI"}
TIGER = "https://www2.census.gov/geo/tiger/TIGER2024/{}/tl_2024_{}_{}.zip"
PLACES = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_{}_place_500k.zip"


def scoped() -> dict[str, str]:
    scope = os.environ.get("MCD_SCOPE")
    return {fips: abbr for fips, abbr in STATES.items() if scope is None or abbr in scope.split(",")}


def records(url: str) -> list[dict[str, str]]:
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        dbf = next(name for name in archive.namelist() if name.endswith(".dbf"))
        reader = shapefile.Reader(dbf=io.BytesIO(archive.read(dbf)))
        return [
            {key: str(value) for key, value in record.as_dict().items()}
            for record in reader.iterRecords()
            if record is not None
        ]


def fetch() -> None:
    ne_towns.fetch(
        SOURCE,
        STATES,
        tuple(
            url
            for fips in STATES
            for url in (
                TIGER.format("COUSUB", fips, "cousub"),
                TIGER.format("PLACE", fips, "place"),
                PLACES.format(fips),
            )
        ),
    )


def parse() -> None:
    funcstat: dict[str, str] = {}
    parts: list[tuple[str, str]] = []
    governments: dict[str, tuple[str, str, str]] = {}
    for fips in STATES:
        active = {
            str(row["NAMELSAD"]).lower(): str(row["GEOID"])
            for row in records(TIGER.format("PLACE", fips, "place"))
            if row["FUNCSTAT"] == "A"
        }
        counted = Counter(
            str(row["NAMELSAD"]).lower()
            for row in records(TIGER.format("PLACE", fips, "place"))
            if row["FUNCSTAT"] == "A"
        )
        for row in records(TIGER.format("COUSUB", fips, "cousub")):
            code, legal = str(row["GEOID"]), str(row["NAMELSAD"]).lower()
            funcstat[code] = str(row["FUNCSTAT"])
            if row["FUNCSTAT"] == "F" and counted[legal] == 1:
                parts.append((code, active[legal]))
        for row in records(TIGER.format("PLACE", fips, "place")):
            governments[str(row["GEOID"])] = (fips, str(row["NAME"]), str(row["NAMELSAD"]))
    used = {geoid for _, geoid in parts}
    shapes = []
    for fips in STATES:
        with zipfile.ZipFile(raw.cached(SOURCE, PLACES.format(fips)).path) as archive:
            files = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(files["shp"]), shx=io.BytesIO(files["shx"]), dbf=io.BytesIO(files["dbf"])
        )
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            if record is not None and shape is not None and str(record["GEOID"]) in used:
                shapes.append((str(record["GEOID"]), json.dumps(shape.__geo_interface__)))
    with connect() as conn:
        geometry = conn.execute(
            """select f.geoid, encode(st_asewkb(s.geom), 'hex'), encode(st_asewkb(st_pointonsurface(s.geom)), 'hex')
               from unnest(%s::text[], %s::text[]) as f(geoid, geojson)
               cross join lateral (select st_multi(st_collectionextract(st_makevalid(st_transform(
                 st_setsrid(st_geomfromgeojson(f.geojson), 4269), 4326)), 3)) as geom) s""",
            [[geoid for geoid, _ in shapes], [geojson for _, geojson in shapes]],
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(parts, schema={"code": pl.String, "geoid": pl.String}, orient="row").write_parquet(
        PARSED / "parts.parquet"
    )
    pl.DataFrame(
        [(geoid, *governments[geoid], geom, point) for geoid, geom, point in geometry],
        schema={
            "geoid": pl.String,
            "state": pl.String,
            "name": pl.String,
            "legal": pl.String,
            "geom": pl.String,
            "point": pl.String,
        },
        orient="row",
    ).write_parquet(PARSED / "places.parquet")
    print(f"{len(parts)} F parts matched to {len(used)} active places, {len(geometry)} with shapes")
    ne_towns.parse(SOURCE, STATES, funcstat)


def emit() -> Path:
    states = scoped()

    def governed(cs: Changeset, slugs: set[str]) -> dict[str, str]:
        parts = pl.read_parquet(PARSED / "parts.parquet").filter(pl.col("code").str.slice(0, 2).is_in(list(states)))
        places = pl.read_parquet(PARSED / "places.parquet").filter(pl.col("state").is_in(list(states)))
        towns = pl.read_parquet(PARSED / "towns.parquet").filter(pl.col("state").is_in(list(states)))
        taken = Counter(
            (state, name)
            for state, name, _ in {
                *(
                    (row["state"], row["name"], row["code"])
                    for row in towns.iter_rows(named=True)
                    if row["funcstat"] != "F"
                ),
                *((row["state"], row["name"], row["geoid"]) for row in places.iter_rows(named=True)),
            }
        )
        held = dict(
            cs.conn.execute(
                """select c.code, t.polity_id::text from polity_territory t join area a on a.id = t.area_id
                   join area_code c on c.area_id = a.id join polity p on p.id = t.polity_id
                   where a.scheme_code = 'us.place' and p.kind = 'municipality' and left(c.code, 2) = any(%s)""",
                [list(states)],
            ).fetchall()
        )
        towns_held = dict(
            cs.conn.execute(
                """select c.code, min(t.polity_id::text) from polity_territory t join area a on a.id = t.area_id
                   join area_code c on c.area_id = a.id join polity p on p.id = t.polity_id
                   join changeset x on x.id = a.changeset_id
                   where a.scheme_code = 'us.town' and p.kind = 'municipality' and x.pipeline <> %s
                     and left(c.code, 2) = any(%s)
                   group by c.code having count(distinct t.polity_id) = 1""",
                [SOURCE, list(states)],
            ).fetchall()
        )
        for code, geoid in parts.iter_rows():
            if code in towns_held and geoid not in held:
                held[geoid] = towns_held[code]
        legals = {row["code"]: row["legal"] for row in towns.iter_rows(named=True) if row["legal"]}
        for fips in states:
            legals.update(
                {str(row["GEOID"]): str(row["NAMELSAD"]) for row in records(TIGER.format("PLACE", fips, "place"))}
            )
        shared: dict[tuple[str, str, bool], dict[str, tuple[list[object], set[str]]]] = {}
        for entity, state, codes, *name, is_area in cs.conn.execute(
            """with e as (
                 select a.id as area, a.id as entity, left(c.code, 2) as st, c.code from area a
                 join area_code c on c.area_id = a.id join changeset x on x.id = a.changeset_id
                 where a.scheme_code in ('us.town', 'us.place') and x.pipeline <> %s and left(c.code, 2) = any(%s)
                 union all
                 select a.id, t.polity_id, left(c.code, 2), c.code from polity_territory t
                 join polity p on p.id = t.polity_id and p.kind = 'municipality' join area a on a.id = t.area_id
                 join area_code c on c.area_id = a.id join changeset x on x.id = p.changeset_id
                 where a.scheme_code in ('us.town', 'us.place') and x.pipeline <> %s and left(c.code, 2) = any(%s))
               select e.entity::text, e.st, array_agg(distinct e.code), n.id, n.entity_id, n.text, n.lang, n.script,
                      n.kind, n.source_id, bool_or(e.area = e.entity)
               from e join name n on n.entity_id = e.entity and n.is_preferred and n.valid_from is null and n.valid_to is null
               group by e.entity, e.st, n.id""",
            [SOURCE, list(states), SOURCE, list(states)],
        ).fetchall():
            shared.setdefault((state, name[2], is_area), {})[entity] = (
                name,
                {legals[code] for code in codes if code in legals},
            )
        renamed = []
        for (state, text, is_area), group in shared.items():
            titles = {
                entity: re.sub(r"\b([a-z])", lambda match: match[1].upper(), next(iter(found)))
                for entity, (_, found) in group.items()
                if len(found) == 1
            }
            if len(group) < 2 or len(titles) < len(group) or len(set(titles.values())) < len(group):
                continue
            for entity, (name, _) in group.items():
                fresh = f"{titles[entity]}, {states[state]}" if is_area else titles[entity]
                cs.add(
                    "name",
                    **dict(zip(("id", "entity_id", "text", "lang", "script", "kind", "source_id"), name, strict=True)),
                    is_preferred=False,
                )
                cs.add("name", entity_id=entity, text=fresh, lang="en", kind="official", is_preferred=True)
                renamed.append(f"{text} -> {fresh}")
        print("renamed:", "; ".join(sorted(renamed)))
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for fips in states:
            cs.artifact(raw.cached(SOURCE, PLACES.format(fips)), census)
        polities: dict[str, str] = {}
        created = attached = 0
        for geoid, state, name, legal, geom, point in places.iter_rows():
            if geoid in held:
                polities[geoid] = held[geoid]
                attached += 1
                continue
            abbreviation = states[state]
            display = name if taken[(state, name)] == 1 else re.sub(r"\b([a-z])", lambda match: match[1].upper(), legal)
            minted = cs.area("us.place", geoid)
            area = str(minted)
            if minted in cs.minted:
                cs.add("area", id=area, scheme_code="us.place")
                cs.add("area_code", area_id=area, code=geoid, valid_from=None, valid_to=None)
                cs.add(
                    "name",
                    entity_id=area,
                    text=f"{display}, {abbreviation}",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=None,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=census,
                )
            polity = str(cs.key("us.polity", f"place:{geoid}"))
            path = f"us/{abbreviation.lower()}/{slug(display)}"
            for fallback in (f"{path}-{slug(legal.rsplit(' ', 1)[-1])}", f"{path}-{geoid}"):
                if path in slugs:
                    path = fallback
            slugs.add(path)
            cs.add("polity", id=polity, kind="municipality", iso_code=None)
            cs.add("name", entity_id=polity, text=display, lang="en", kind="official", is_preferred=True)
            cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"US-{abbreviation}"))
            cs.add("slug", path=path, entity_id=polity)
            cs.add("polity_territory", polity_id=polity, area_id=area)
            polities[geoid] = polity
            created += 1
        assigned = {code: polities[geoid] for code, geoid in parts.iter_rows() if geoid in polities}
        print(
            f"{created} polities from F parts via their place, {attached} places already held as polities, "
            f"{len(assigned)} parts governed ({sum(1 for _, geoid in parts.iter_rows() if held.get(geoid))} under a held polity)"
        )
        return assigned

    return ne_towns.emit(
        SOURCE,
        VERSION,
        states,
        f"County subdivisions of {', '.join(states.values())} (townships, towns, villages, boroughs, and cities) from the "
        "Census Bureau's cartographic boundary files (2010, 2020, and 2024 vintages), as us.town areas; those the 2024 "
        "TIGER/Line file lists as active governments (FUNCSTAT A) get municipal polities, and a city, village, or borough "
        "part (FUNCSTAT F) belongs to its incorporated place's polity, whose territory is the place",
        governed,
    )
