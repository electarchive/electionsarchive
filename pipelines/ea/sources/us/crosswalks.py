import re
import time
import zipfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid5

import httpx
import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.crosswalks"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "U.S. Census Bureau"
TITLE = "2020 Census Redistricting Data (Public Law 94-171) Summary File"
LISTING = "https://www2.census.gov/programs-surveys/decennial/2020/data/01-Redistricting_File--PL_94-171/"


def files() -> list[str]:
    states = sorted(
        set(re.findall(r'href="([A-Z][A-Za-z_]+)/"', raw.cached(SOURCE, LISTING).path.read_text()))
        - {"National", "Puerto_Rico"}
    )
    urls = []
    for state in states:
        folder = f"{LISTING}{state}/"
        polite(folder, ".html")
        urls += [
            folder + name
            for name in re.findall(r'href="([a-z]{2}2020\.pl\.zip)"', raw.cached(SOURCE, folder).path.read_text())
        ]
    return urls


def polite(url: str, suffix: str) -> None:
    for attempt in range(8):
        try:
            raw.fetch(SOURCE, url, suffix=suffix)
            return
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 429 or attempt == 7:
                raise
            time.sleep(30 * 2**attempt)


def fetch() -> None:
    polite(LISTING, ".html")
    for url in files():
        polite(url, ".zip")
        print(url, flush=True)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames = []
    for url in files():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            member = next(name for name in archive.namelist() if name.endswith("geo2020.pl"))
            rows = [
                (parts[9], int(parts[90]), float(parts[92]), float(parts[93]))
                for parts in (line.split("|") for line in archive.read(member).decode("latin-1").splitlines())
                if parts[2] == "750" and parts[90] not in ("", "0")
            ]
        frames.append(pl.DataFrame(rows, schema=["block", "pop", "lat", "lon"], orient="row"))
        print(f"{url.rsplit('/', 1)[1]}: {len(rows)} populated blocks, {frames[-1]['pop'].sum()} people", flush=True)
    pl.concat(frames).sort("block").write_parquet(PARSED / "blocks.parquet")


def emit() -> Path:
    blocks = pl.read_parquet(PARSED / "blocks.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Estimated crosswalks between precinct and election district plans, weighting each earlier "
            "unit's votes by the 2020 census population of the blocks it shares with each later unit",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LISTING,
            license="Public domain",
        )
        cs.artifact(raw.cached(SOURCE, LISTING), source)
        for url in files():
            cs.artifact(raw.cached(SOURCE, url), source)
        sets = conn.execute(
            """select id, scheme_code, vintage, as_of from boundary_set
               where scheme_code in ('us.precinct', 'us.nyc_ed') order by scheme_code, as_of"""
        ).fetchall()
        pairs = []
        for to_id, scheme, vintage, as_of in sets:
            for from_id, other, earlier, then in sets:
                if other != scheme or then >= as_of:
                    continue
                general = vintage.isdigit() and earlier.isdigit()
                months = abs((as_of.month - then.month + 6) % 12 - 6)
                if (general and int(vintage) - int(earlier) in (2, 4)) or (
                    not vintage.isdigit()
                    and not earlier.isdigit()
                    and months <= 1
                    and timedelta(days=300) <= as_of - then <= timedelta(days=4 * 366 + 45)
                ):
                    pairs.append((from_id, to_id, scheme, earlier, vintage))
        used = {}
        for from_id, to_id, scheme, _, _ in pairs:
            for id in (from_id, to_id):
                used[id] = next((row[1], row[3]) for row in sets if row[0] == id)
        conn.execute("set work_mem = '512MB'")
        conn.execute("create temp table blocks (id serial primary key, pop int, geom geometry(Point, 4326))")
        with conn.cursor().copy("copy blocks (pop, geom) from stdin") as copy:
            for pop, lat, lon in blocks.select("pop", "lat", "lon").iter_rows():
                copy.write_row((pop, f"SRID=4326;POINT({lon} {lat})"))
        conn.execute("create index on blocks using gist (geom)")
        conn.execute("analyze blocks")
        print(f"{blocks.height} populated blocks loaded", flush=True)
        conn.execute("create temp table assign (plan uuid, block int, k int)")
        conn.execute("create temp table plan (plan uuid, k int, geom geometry)")
        for id, (scheme, as_of) in used.items():
            conn.execute(
                """insert into plan
                   select %s, a.k, g.geom from area a join area_geometry g on g.area_id = a.id
                   where a.scheme_code = %s and g.valid @> %s::date""",
                [id, scheme, as_of],
            )
            count = conn.execute(
                """insert into assign
                   select p.plan, b.id, p.k from plan p join blocks b on st_intersects(p.geom, b.geom)
                   where p.plan = %s""",
                [id],
            ).rowcount
            print(f"{scheme} {as_of}: {count} block assignments", flush=True)
        conn.execute("create index on assign (plan, block)")
        conn.execute("create index on plan using gist (geom)")
        conn.execute("create index on plan (plan, k)")
        conn.execute("analyze assign")
        conn.execute("analyze plan")
        for from_id, to_id, scheme, earlier, vintage in pairs:
            crosswalk = uuid5(NAMESPACE, f"{SOURCE}\x1f{from_id}\x1f{to_id}\x1f{VERSION}")
            weights = pl.DataFrame(
                conn.execute(
                    """with f as (select block, k from assign where plan = %(from)s),
                            t as (select block, k from assign where plan = %(to)s),
                            total as (select f.k, sum(b.pop) as pop from f join blocks b on b.id = f.block group by f.k),
                            shared as (
                              select f.k as from_k, t.k as to_k, sum(b.pop) as pop
                              from f join t on t.block = f.block join blocks b on b.id = f.block group by 1, 2
                            ),
                            bare as (
                              select p.k, p.geom from plan p
                              where p.plan = %(from)s and not exists (select from total where total.k = p.k)
                            ),
                            areal as (
                              select bare.k as from_k, q.k as to_k,
                                st_area(st_intersection(st_makevalid(bare.geom), st_makevalid(q.geom))) / nullif(st_area(bare.geom), 0) as weight
                              from bare join plan q on q.plan = %(to)s and st_intersects(bare.geom, q.geom)
                            )
                       select s.from_k, s.to_k, s.pop::float8 / total.pop as weight, false as areal
                       from shared s join total on total.k = s.from_k
                       union all
                       select from_k, to_k, weight, true from areal where weight >= 0.001""",
                    {"from": from_id, "to": to_id},
                ).fetchall(),
                schema={"from_k": pl.Int32, "to_k": pl.Int32, "weight": pl.Float64, "areal": pl.Boolean},
                orient="row",
            ).filter(pl.col("weight") >= 0.0005)
            if weights.is_empty():
                continue
            cs.add(
                "crosswalk",
                id=crosswalk,
                from_kind="boundary_set",
                from_boundary_set_id=from_id,
                to_boundary_set_id=to_id,
                method="population",
            )
            cs.frame(
                "crosswalk_weight",
                weights.select(
                    crosswalk_id=pl.lit(str(crosswalk)),
                    from_k="from_k",
                    to_k="to_k",
                    weight=pl.col("weight").round(6).cast(pl.Decimal(10, 6)),
                    exact=pl.col("from_k") == pl.col("to_k"),
                ),
            )
            print(
                f"{scheme} {earlier} -> {vintage}: {weights.height} weights, "
                f"{weights['from_k'].n_unique()} units, {weights.filter('areal')['from_k'].n_unique()} by area",
                flush=True,
            )
        return cs.write()
