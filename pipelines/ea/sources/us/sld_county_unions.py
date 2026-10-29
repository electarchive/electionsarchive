import re
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.sld_county_unions"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE


def parse() -> None:
    with connect() as conn:
        counties = conn.execute(
            """select ac.code, n.text from area a join area_code ac on ac.area_id = a.id
               join name n on n.entity_id = a.id and n.text like '%%, __'
               where a.scheme_code = 'us.county' and exists (select 1 from area_geometry g where g.area_id = a.id)"""
        ).fetchall()
        wanted = conn.execute(
            """select a.id::text, ac.code from area a join area_code ac on ac.area_id = a.id
               where a.scheme_code in ('us.sldl', 'us.sldu') and ac.code ~ '^[A-Z]{2}:[a-z]+$'
                 and not exists (select 1 from area_geometry g where g.area_id = a.id)"""
        ).fetchall()
    names: dict[str, dict[str, set[str]]] = {}
    for fips, text in counties:
        place, state = text.rsplit(", ", 1)
        plain = re.sub(r"[^a-z]", "", place.lower())
        for key in {plain, re.sub(r"(county|parish|borough|censusarea)$", "", plain)}:
            names.setdefault(state, {}).setdefault(key, set()).add(fips)
    rows: list[dict[str, object]] = []
    for area, code in wanted:
        state, token = code.split(":")
        pool = names.get(state, {})
        token = re.sub(r"county$", "", token) if token not in pool else token
        paths: list[list[str]] = []
        stack: list[tuple[int, list[str]]] = [(0, [])]
        while stack:
            at, chosen = stack.pop()
            if at == len(token):
                paths.append(chosen)
                continue
            for key, fips in pool.items():
                if token.startswith(key, at) and len(fips) == 1:
                    stack.append((at + len(key), [*chosen, next(iter(fips))]))
        unique = {tuple(sorted(set(path))) for path in paths}
        if len(unique) == 1:
            rows.append({"area": area, "code": code, "counties": list(unique.pop())})
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "matches.parquet")
    print(f"{len(rows)} of {len(wanted)} whole-county districts matched")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "matches.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative districts made of whole counties, shaped as the union of their counties",
            polity="US",
        )
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for area, counties in zip(frame["area"], frame["counties"], strict=True):
            geom, point = conn.execute(
                """with latest as (
                       select distinct on (ac.code) ac.code, g.geom from area_geometry g
                       join area_code ac on ac.area_id = g.area_id
                       where ac.code = any(%s) order by ac.code, g.valid_from desc nulls last)
                   select encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3))), 'hex'),
                          encode(st_asewkb(st_pointonsurface(st_makevalid(st_union(geom)))), 'hex') from latest""",
                [list(counties)],
            ).fetchone()
            cs.add("area_geometry", area_id=area, valid_from=None, geom=geom, label_point=point, quality="official", source_id=census)
        return cs.write()
