import csv
import io
import json
import re
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.place_units"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
POPULATION = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/cities/totals/sub-est2024.csv"
CITIES = 100
VINTAGES = range(2011, 2026)
INSIDE = 0.99
FLOOR = 0.01
TIGER_TITLE = "TIGER/Line Shapefiles: Places"
MARKER = re.compile(r"^(CITY OF )?(?P<name>.+?)( CITY| C| WD| WARD| W| PCT| PRECINCT| P| DIST| DISTRICT| \d)( |$)")


def tiger(year: int, state: str) -> str:
    return f"https://www2.census.gov/geo/tiger/TIGER{year}/PLACE/tl_{year}_{state}_place.zip"


def fold(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", text.upper().replace("SAINT", "ST"))).strip()


def largest() -> list[tuple[str, str]]:
    with open(raw.cached(SOURCE, POPULATION).path, encoding="latin-1") as file:
        rows = [row for row in csv.DictReader(file) if row["SUMLEV"] == "162"]
    rows.sort(key=lambda row: -int(row["ESTIMATESBASE2020"]))
    return [(row["STATE"] + row["PLACE"], row["NAME"]) for row in rows[:CITIES]]


def vintages(states: set[str]) -> dict[str, set[int]]:
    with connect() as conn:
        found: dict[str, set[int]] = {}
        for state, year in conn.execute(
            """select distinct split_part(u.code, ':', 2), extract(year from lower(u.valid))::int
               from reporting_unit u join area a on a.id = u.area_id and a.scheme_code = 'us.precinct'
               where u.scheme_code = 'us.precinct'"""
        ).fetchall():
            fips = STATE_FIPS.get(state)
            if fips in states and year >= VINTAGES.start:
                found.setdefault(fips, set()).add(min(year, VINTAGES.stop - 1))
        return found


def fetch() -> None:
    raw.fetch(SOURCE, POPULATION, suffix=".csv")
    for state, years in sorted(vintages({geoid[:2] for geoid, _ in largest()}).items()):
        for year in sorted(years):
            raw.fetch(SOURCE, tiger(year, state), suffix=".zip")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    cities = dict(largest())
    rows = []
    for state, years in sorted(vintages({geoid[:2] for geoid in cities}).items()):
        for year in sorted(years):
            with zipfile.ZipFile(raw.cached(SOURCE, tiger(year, state)).path) as z:
                parts = {name.rsplit(".", 1)[1].lower(): z.read(name) for name in z.namelist()}
            reader = shapefile.Reader(
                shp=io.BytesIO(parts["shp"]),
                shx=io.BytesIO(parts["shx"]),
                dbf=io.BytesIO(parts["dbf"]),
                encoding="latin-1",
            )
            for record, shape in zip(reader.records(), reader.shapes(), strict=True):
                if record is None or shape is None:
                    continue
                fields = record.as_dict()
                geoid = fields.get("GEOID") or fields.get(f"GEOID{str(year)[2:]}")
                if geoid in cities:
                    rows.append((geoid, year, json.dumps(shape.__geo_interface__)))
    pl.DataFrame(rows, schema={"geoid": pl.String, "year": pl.Int32, "geojson": pl.String}, orient="row").write_parquet(
        PARSED / "cities.parquet"
    )
    print(f"{len(rows)} city shapes for {len(cities)} cities")


def emit() -> Path:
    cities = dict(largest())
    shapes = pl.read_parquet(raw.parsed(SOURCE) / "cities.parquet")
    with connect() as conn, open(DATA / "logs" / "place_units-disagreements.tsv", "w") as log:
        log.write("city\tdate\tkind\tunit\tdetail\n")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Precincts placed in the {CITIES} largest cities by exact methods only: the city's own contest that day, "
            "the source naming the city, or a precinct shape at least 99% inside the city's full-resolution TIGER/Line "
            "boundary for the election year; each city's precinct universe on each election day is recorded",
            polity="US",
        )
        tiger_source = cs.source("U.S. Census Bureau", TIGER_TITLE)
        cs.add(
            "source",
            id=tiger_source,
            kind="official_body",
            publisher="U.S. Census Bureau",
            title=TIGER_TITLE,
            url="https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html",
            license="Public domain",
        )
        for state, years in vintages({geoid[:2] for geoid, _ in cities.items()}).items():
            for year in years:
                cs.artifact(raw.cached(SOURCE, tiger(year, state)), tiger_source)
        places = dict(
            conn.execute(
                """select c.code, a.id::text from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'us.place' and c.code = any(%s)""",
                [list(cities)],
            ).fetchall()
        )
        print(f"{len(places)} of {len(cities)} cities have a place area; missing: {sorted(set(cities) - set(places))}")
        conn.execute("create temporary table city_shape (geoid text, year int, geom geometry(MultiPolygon, 4326))")
        for geoid, year, geojson in shapes.iter_rows():
            if geoid in places:
                conn.execute(
                    """insert into city_shape values
                       (%s, %s, st_multi(st_collectionextract(st_makevalid(st_setsrid(st_geomfromgeojson(%s), 4326)), 3)))""",
                    [geoid, year, geojson],
                )
        conn.execute("create index on city_shape using gist (geom)")
        conn.execute("analyze city_shape")
        owned = {
            (unit, area)
            for unit, area in conn.execute(
                """select x.unit_id::text, x.area_id::text from reporting_unit_area x join changeset c on c.id = x.changeset_id
                   where x.area_id = any(%s::uuid[]) and c.pipeline <> %s""",
                [list(places.values()), SOURCE],
            ).fetchall()
        }
        memberships: list[tuple[str, str, date, date, str]] = []
        universes = 0
        chosen: Counter[str] = Counter()
        for state in sorted({geoid[:2] for geoid in places}):
            abbr = FIPS_STATE[state]
            here = {geoid: name for geoid, name in cities.items() if geoid in places and geoid[:2] == state}
            units = conn.execute(
                """select u.id::text, u.code, lower(u.valid), upper(u.valid),
                          case when own.scheme_code = u.scheme_code then 'a' || own.id::text else 'u' || u.k::text end,
                          own.scheme_code = 'us.precinct', n.text, c.pipeline
                   from reporting_unit u join changeset c on c.id = u.changeset_id
                   left join area own on own.id = u.area_id
                   left join name n on n.entity_id = u.area_id and n.kind = 'official' and n.is_preferred
                   where u.scheme_code = 'us.precinct' and split_part(u.code, ':', 2) = %s and lower(u.valid) is not null""",
                [abbr],
            ).fetchall()
            by_day: dict[date, list[tuple[str, str, date, date, str, bool, str | None, str]]] = {}
            for row in units:
                by_day.setdefault(row[2], []).append(row)
            shares: dict[tuple[str, date], dict[str, float]] = {}
            for geoid, day, key, share in conn.execute(
                """with p as (
                     select distinct u.area_id, lower(u.valid) as d from reporting_unit u
                     join area a on a.id = u.area_id and a.scheme_code = 'us.precinct'
                     where u.scheme_code = 'us.precinct' and split_part(u.code, ':', 2) = %s
                       and extract(year from lower(u.valid)) >= %s
                   )
                   select t.geoid, p.d, 'a' || p.area_id::text,
                          st_area(st_transform(st_intersection(st_makevalid(g.geom), t.geom), 5070))
                            / nullif(st_area(st_transform(g.geom, 5070)), 0)
                   from p join area_geometry g on g.area_id = p.area_id and g.valid @> p.d
                   join city_shape t on t.geoid = any(%s) and t.year = least(extract(year from p.d)::int, %s)
                     and t.geom && g.geom and st_intersects(t.geom, g.geom)""",
                [abbr, VINTAGES.start, list(here), VINTAGES.stop - 1],
            ).fetchall():
                if share is not None:
                    shares.setdefault((geoid, day), {})[key] = share
            own_sets: dict[tuple[str, date], tuple[str, str, set[str]]] = {}
            for geoid, day, set_id, source_id, keys in conn.execute(
                """select ac.code, e.date_from, r.id::text, r.source_id::text,
                          array_agg(distinct case when own.scheme_code = u.scheme_code then 'a' || own.id::text
                                                  else 'u' || u.k::text end)
                   from area_code ac join contest c on c.district_area_id = ac.area_id
                   join result_set r on r.contest_id = c.id and r.status = 'active'
                   join election e on e.id = r.election_id
                   join lateral (select distinct unit_k from result_value v where v.result_set_k = r.k offset 0) v on true
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
                   left join area own on own.id = u.area_id
                   where ac.code = any(%s)
                   group by 1, 2, 3, 4""",
                [list(here)],
            ).fetchall():
                if len(keys) > len(own_sets.get((geoid, day), ("", "", set()))[2]):
                    own_sets[(geoid, day)] = (set_id, source_id, set(keys))
            for day, rows in sorted(by_day.items()):
                for geoid, city in here.items():
                    name = fold(
                        re.sub(
                            r"\s+(city|town|village|municipality|metropolitan government|"
                            r"urban county|consolidated government|unified government)\b.*$",
                            "",
                            re.sub(r"-[A-Za-z]+ (metropolitan|consolidated|unified).*$", "", city),
                        )
                    )
                    named: dict[str, set[str]] = {}
                    for unit, code, start, end, key, _, label, pipeline in rows:
                        county = (
                            fold(re.sub(r"\s+(County|Parish)$", "", label.rsplit(", ", 1)[1]))
                            if label and ", " in label
                            else ""
                        )
                        parts = code.split(":")
                        texts = [*parts[3:5], label.rsplit(", ", 1)[0] if label else ""]
                        if any(
                            (m := MARKER.match(fold(text) + " "))
                            and m["name"] == name
                            and not (county == name and m[3].strip().isdigit())
                            for text in texts
                            if text
                        ):
                            named.setdefault(pipeline, set()).add(key)
                    source_keys = set().union(*named.values()) if named else set()
                    share = shares.get((geoid, day), {})
                    inside = {key for key, value in share.items() if value >= INSIDE}
                    touched = {key for key, value in share.items() if value >= FLOOR}
                    for key, value in share.items():
                        if 0.005 <= value <= 0.02:
                            log.write(f"{city}\t{day}\tfloor\t{key}\t{value:.4f}\n")
                    own = own_sets.get((geoid, day))
                    methods = {
                        "city_universe": own[2] if own else set(),
                        "source_code": source_keys,
                        "shape_contained": inside,
                    }
                    candidates = []
                    for system in ("a", "u"):
                        within = {method: {key for key in keys if key[0] == system} for method, keys in methods.items()}
                        present = [method for method, keys in within.items() if keys]
                        if not present:
                            continue
                        sizes = {
                            "city_universe": len(within["city_universe"]),
                            "source_code": max(
                                (len({key for key in found if key[0] == system}) for found in named.values()), default=0
                            ),
                            "shape_contained": len(touched),
                        }
                        method = (
                            "city_universe"
                            if within["city_universe"]
                            else "source_code"
                            if "source_code" in present and sizes["source_code"] >= len(within["shape_contained"])
                            else "shape_contained"
                        )
                        keys = within[method]
                        for other in present:
                            if other == method:
                                continue
                            for key in sorted(keys ^ within[other]):
                                log.write(
                                    f"{city}\t{day}\t{method} vs {other}\t{key}\t"
                                    f"only {method if key in keys else other}\n"
                                )
                        candidates.append((sizes[method], method, keys, system))
                    if not candidates:
                        continue
                    expected, method, keys, system = max(candidates, key=lambda candidate: candidate[0])
                    for other in candidates:
                        if other[3] != system:
                            log.write(
                                f"{city}\t{day}\tsystem skipped\t{other[3]}\t{other[1]} {other[0]} against {method} {expected}\n"
                            )
                    if expected == 0:
                        continue
                    chosen[method] += 1
                    universes += 1
                    cs.add(
                        "area_universe",
                        area_id=places[geoid],
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                        expected=expected,
                        method=method,
                        source_id=own[1]
                        if method == "city_universe" and own
                        else tiger_source
                        if method == "shape_contained"
                        else None,
                        result_set_id=own[0] if method == "city_universe" and own else None,
                    )
                    for unit, code, start, end, key, _, label, pipeline in rows:
                        if key in keys and (unit, places[geoid]) not in owned:
                            memberships.append((unit, places[geoid], start, end, method))
        print(f"{universes} city universes by {dict(chosen)}; {len(memberships)} memberships")
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                memberships,
                schema={
                    "unit_id": pl.String,
                    "area_id": pl.String,
                    "valid_from": pl.Date,
                    "valid_to": pl.Date,
                    "method": pl.String,
                },
                orient="row",
            ),
        )
        return cs.write()


STATE_FIPS = {
    "AL": "01",
    "AK": "02",
    "AZ": "04",
    "AR": "05",
    "CA": "06",
    "CO": "08",
    "CT": "09",
    "DE": "10",
    "DC": "11",
    "FL": "12",
    "GA": "13",
    "HI": "15",
    "ID": "16",
    "IL": "17",
    "IN": "18",
    "IA": "19",
    "KS": "20",
    "KY": "21",
    "LA": "22",
    "ME": "23",
    "MD": "24",
    "MA": "25",
    "MI": "26",
    "MN": "27",
    "MS": "28",
    "MO": "29",
    "MT": "30",
    "NE": "31",
    "NV": "32",
    "NH": "33",
    "NJ": "34",
    "NM": "35",
    "NY": "36",
    "NC": "37",
    "ND": "38",
    "OH": "39",
    "OK": "40",
    "OR": "41",
    "PA": "42",
    "RI": "44",
    "SC": "45",
    "SD": "46",
    "TN": "47",
    "TX": "48",
    "UT": "49",
    "VT": "50",
    "VA": "51",
    "WA": "53",
    "WV": "54",
    "WI": "55",
    "WY": "56",
}
FIPS_STATE = {fips: abbr for abbr, fips in STATE_FIPS.items()}
