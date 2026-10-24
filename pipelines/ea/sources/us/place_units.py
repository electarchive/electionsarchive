import csv
import io
import json
import os
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
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
POPULATION = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/cities/totals/sub-est2024.csv"
CITIES = 100
VINTAGES = range(2011, 2026)
INSIDE = 0.99
FLOOR = 0.01
TIGER_TITLE = "TIGER/Line Shapefiles: Places"
TOWN_TITLE = "TIGER/Line Shapefiles: County Subdivisions"
TOWN_STATES = {
    "09": "CT",
    "23": "ME",
    "25": "MA",
    "33": "NH",
    "44": "RI",
    "50": "VT",
    "26": "MI",
    "27": "MN",
    "34": "NJ",
    "36": "NY",
    "42": "PA",
    "55": "WI",
}
TOWNSHIP = {"township", "charter township"}
KINDS = {
    "CITY": {"city"},
    "C": {"city"},
    "CTY": {"city"},
    "TOWNSHIP": TOWNSHIP,
    "TWP": TOWNSHIP,
    "TWSP": TOWNSHIP,
    "TP": TOWNSHIP,
    "CHARTER": {"charter township"},
    "TOWN": {"town"},
    "T": {"town"} | TOWNSHIP,
    "VILLAGE": {"village"},
    "VIL": {"village"},
    "VLG": {"village"},
    "V": {"village"},
    "BOROUGH": {"borough"},
    "BORO": {"borough"},
    "BOR": {"borough"},
    "B": {"borough"},
}
FOLLOWERS = {
    *("W", "WD", "WARD", "WARDS", "P", "PCT", "PREC", "PRECINCT", "D", "DIST", "DISTRICT", "ED"),
    *("N", "S", "E", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST", "UPPER", "LOWER", "MIDDLE", "CENTRAL"),
}
MARKER = re.compile(r"^(CITY OF )?(?P<name>.+?)( CITY| C| WD| WARD| W| PCT| PRECINCT| P| DIST| DISTRICT| \d)( |$)")


def tiger(year: int, state: str, layer: str = "place") -> str:
    return f"https://www2.census.gov/geo/tiger/TIGER{year}/{layer.upper()}/tl_{year}_{state}_{layer}.zip"


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
    found = vintages(set(TOWN_STATES))
    for state in TOWN_STATES:
        for year in sorted(found.get(state, set()) | {2020}):
            raw.fetch(SOURCE, tiger(year, state, "cousub"), suffix=".zip")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    cities = dict(largest())
    rows = []
    for state, years in sorted(vintages({geoid[:2] for geoid in cities}).items()):
        for year in sorted(years):
            for geoid, _, _, geojson in shapes(tiger(year, state), cities):
                rows.append((geoid, year, geojson))
    pl.DataFrame(rows, schema={"geoid": pl.String, "year": pl.Int32, "geojson": pl.String}, orient="row").write_parquet(
        PARSED / "cities.parquet"
    )
    print(f"{len(rows)} city shapes for {len(cities)} cities")


def shapes(url: str, wanted: set[str] | dict[str, str] | None = None) -> list[tuple[str, str, str, str]]:
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as z:
        parts = {name.rsplit(".", 1)[1].lower(): z.read(name) for name in z.namelist()}
    reader = shapefile.Reader(
        shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]), encoding="latin-1"
    )
    found = []
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None:
            continue
        fields = record.as_dict()
        geoid = str(fields.get("GEOID") or next(value for key, value in fields.items() if key.startswith("GEOID")))
        if wanted is None or geoid in wanted:
            found.append((geoid, str(fields["NAME"]), str(fields["NAMELSAD"]), json.dumps(shape.__geo_interface__)))
    return found


def emit() -> Path:
    scope = os.environ.get("TOWN_SCOPE")
    towns = {STATE_FIPS[abbr]: abbr for abbr in scope.split(",")} if scope is not None else {}
    cities = {} if towns else dict(largest())
    title = TOWN_TITLE if towns else TIGER_TITLE
    log_path = DATA / "logs" / (f"place_units-towns-{scope}.tsv" if towns else "place_units-disagreements.tsv")
    with connect() as conn, open(log_path, "w") as log:
        log.write("area\tdate\tkind\tunit\tdetail\n")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                f"Precincts placed in the towns, townships, cities, villages, and boroughs of {', '.join(towns.values())}"
                if towns
                else f"Precincts placed in the {CITIES} largest cities"
            )
            + " by exact methods only: the government's own contest that day, the source naming it, or a precinct shape "
            "at least 99% inside its full-resolution TIGER/Line boundary for the election year; each one's precinct "
            "universe on each election day is recorded",
            polity="US",
        )
        tiger_source = cs.source("U.S. Census Bureau", title)
        cs.add(
            "source",
            id=tiger_source,
            kind="official_body",
            publisher="U.S. Census Bureau",
            title=title,
            url="https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html",
            license="Public domain",
        )
        found = vintages(set(towns) if towns else {geoid[:2] for geoid in cities})
        years = {state: found.get(state, set()) | ({2020} if towns else set()) for state in towns or found}
        for state, held in years.items():
            for year in held:
                cs.artifact(raw.cached(SOURCE, tiger(year, state, "cousub" if towns else "place")), tiger_source)
        areas = dict(
            conn.execute(
                """select c.code, a.id::text from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = %s and (c.code = any(%s) or left(c.code, 2) = any(%s))""",
                ["us.town" if towns else "us.place", list(cities), list(towns) if towns else []],
            ).fetchall()
        )
        if not towns:
            print(
                f"{len(areas)} of {len(cities)} cities have a place area; missing: {sorted(set(cities) - set(areas))}"
            )
        conn.execute("create temporary table city_shape (geoid text, year int, geom geometry(MultiPolygon, 4326))")
        if not towns:
            for geoid, year, geojson in pl.read_parquet(raw.parsed(SOURCE) / "cities.parquet").iter_rows():
                if geoid in areas:
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
                [list(areas.values()), SOURCE],
            ).fetchall()
        }
        memberships: list[tuple[str, str, date, date, str]] = []
        universes = 0
        chosen: Counter[str] = Counter()
        for state in sorted(towns or {geoid[:2] for geoid in areas}):
            abbr = FIPS_STATE[state]
            index: dict[int, dict[tuple[str, str], list[tuple[str, str]]]] = {}
            labels: dict[str, str] = {}
            if towns:
                connecticut = {code[5:]: code for code in areas if code.startswith("09")}
                conn.execute("truncate city_shape")
                for year in sorted(years[state]):
                    rows = [
                        (connecticut.get(geoid[5:], geoid) if state == "09" else geoid, *rest)
                        for geoid, *rest in shapes(tiger(year, state, "cousub"))
                    ]
                    for geoid, name, legal, _ in rows:
                        if geoid in areas:
                            labels[geoid] = legal
                            index.setdefault(year, {}).setdefault(
                                (geoid[:2] if state == "09" else geoid[:5], fold(name)), []
                            ).append((geoid, legal[len(name) :].strip().lower()))
                    with conn.cursor() as cursor:
                        cursor.executemany(
                            """insert into city_shape values
                               (%s, %s, st_multi(st_collectionextract(st_makevalid(st_setsrid(st_geomfromgeojson(%s), 4326)), 3)))""",
                            [(geoid, year, geojson) for geoid, _, _, geojson in rows if geoid in areas],
                        )
                conn.execute("create index if not exists city_shape_geom on city_shape using gist (geom)")
                conn.execute("analyze city_shape")
                here = dict(labels)
                longest = max((len(name.split()) for names in index.values() for _, name in names), default=0)
            else:
                here = {geoid: name for geoid, name in cities.items() if geoid in areas and geoid[:2] == state}
            counties = {
                fold(re.sub(r"\s+(County|Parish)(, [A-Z]{2})?$", "", text)): code
                for code, text in conn.execute(
                    """select c.code, n.text from area a join area_code c on c.area_id = a.id
                       join name n on n.entity_id = a.id and n.is_preferred
                       where a.scheme_code = 'us.county' and left(c.code, 2) = %s""",
                    [state],
                ).fetchall()
            }
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
                   from area_code ac join area d on d.id = ac.area_id and d.scheme_code = %s
                   join contest c on c.district_area_id = ac.area_id
                   join result_set r on r.contest_id = c.id and r.status = 'active'
                   join election e on e.id = r.election_id
                   join lateral (select distinct unit_k from result_value v where v.result_set_k = r.k offset 0) v on true
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
                   left join area own on own.id = u.area_id
                   where ac.code = any(%s)
                   group by 1, 2, 3, 4""",
                ["us.town" if towns else "us.place", list(here)],
            ).fetchall():
                if len(keys) > len(own_sets.get((geoid, day), ("", "", set()))[2]):
                    own_sets[(geoid, day)] = (set_id, source_id, set(keys))
            for day, rows in sorted(by_day.items()):
                named: dict[str, dict[str, set[str]]] = {}
                day_keys: dict[str, set[str]] = {}
                for row in rows:
                    day_keys.setdefault(row[7], set()).add(row[4])
                if towns:
                    names = index[min(years[state], key=lambda year: (abs(year - day.year), -year))]
                    for unit, code, start, end, key, _, label, pipeline in rows:
                        parts = code.split(":")
                        segments = (label or "").split(", ")
                        county = (
                            parts[2]
                            if len(parts) > 2 and len(parts[2]) == 5 and parts[2].startswith(state)
                            else next(
                                (
                                    counties.get(fold(re.sub(r"\s+(County|Parish)$", "", segment)))
                                    for segment in segments
                                    if re.search(r"\s(County|Parish)$", segment)
                                ),
                                None,
                            )
                        )
                        if state == "09":
                            county = state
                        elif county is None:
                            continue
                        matched = set()
                        for text in (*parts[3:5], ", ".join(segments[:-1]) if len(segments) > 1 else segments[0]):
                            words = fold(text).split()
                            while words and words[0].isdigit():
                                words.pop(0)
                            kinds: set[str] | None = None
                            for prefix in (["CHARTER", "TOWNSHIP", "OF"], [words[0] if words else "", "OF"]):
                                if words[: len(prefix)] == prefix and prefix[0] in KINDS:
                                    kinds, words = KINDS[prefix[0]], words[len(prefix) :]
                                    break
                            for size in range(min(longest, len(words)), 0, -1):
                                found_towns = names.get((county, " ".join(words[:size])))
                                if found_towns is None:
                                    continue
                                follower = words[size] if size < len(words) else None
                                if follower in KINDS:
                                    kinds = KINDS[follower] if kinds is None else kinds & KINDS[follower]
                                elif (
                                    follower is None
                                    and kinds is None
                                    and state not in ("09", "23", "25", "33", "44", "50")
                                ) or (follower is not None and not (follower[0].isdigit() or follower in FOLLOWERS)):
                                    break
                                if (
                                    follower is not None
                                    and follower[0].isdigit()
                                    and counties.get(" ".join(words[:size])) == county
                                ):
                                    break
                                picked = [geoid for geoid, kind in found_towns if kinds is None or kind in kinds]
                                if len(picked) == 1:
                                    matched.add(picked[0])
                                break
                        if len(matched) == 1:
                            named.setdefault(matched.pop(), {}).setdefault(pipeline, set()).add(key)
                else:
                    for geoid, city in here.items():
                        name = fold(
                            re.sub(
                                r"\s+(city|town|village|municipality|metropolitan government|"
                                r"urban county|consolidated government|unified government)\b.*$",
                                "",
                                re.sub(r"-[A-Za-z]+ (metropolitan|consolidated|unified).*$", "", city),
                            )
                        )
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
                                named.setdefault(geoid, {}).setdefault(pipeline, set()).add(key)
                claimed: dict[str, set[str]] = {}
                for geoid, found in named.items():
                    for keys in found.values():
                        for key in keys:
                            claimed.setdefault(key, set()).add(geoid)
                for geoid, city in here.items():
                    found_named = named.get(geoid, {})
                    source_keys = set().union(*found_named.values()) if found_named else set()
                    share = shares.get((geoid, day), {})
                    own = own_sets.get((geoid, day))
                    if not source_keys and not share and own is None:
                        continue
                    inside = {
                        key for key, value in share.items() if value >= INSIDE and claimed.get(key, {geoid}) >= {geoid}
                    }
                    touched = {
                        key for key, value in share.items() if value >= FLOOR and claimed.get(key, {geoid}) >= {geoid}
                    }
                    for key, value in share.items():
                        if 0.005 <= value <= 0.02:
                            log.write(f"{city}\t{day}\tfloor\t{key}\t{value:.4f}\n")
                    methods = {
                        "city_universe": own[2] if own else set(),
                        "source_code": source_keys,
                        "shape_contained": inside,
                    }
                    candidates = []
                    for system in ("a", "u"):
                        city_keys = {key for key in methods["city_universe"] if key[0] == system}
                        if city_keys:
                            candidates.append((len(city_keys), "city_universe", city_keys, f"{system} city"))
                            continue
                        for pipeline, held in day_keys.items():
                            within = {
                                "city_universe": set(),
                                "source_code": {key for key in found_named.get(pipeline, set()) if key[0] == system},
                                "shape_contained": {key for key in inside if key[0] == system and key in held},
                            }
                            present = [method for method, keys in within.items() if keys]
                            if not present:
                                continue
                            sizes = {
                                "city_universe": len(within["city_universe"]),
                                "source_code": len(within["source_code"]),
                                "shape_contained": len({key for key in touched if key[0] == system and key in held}),
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
                            candidates.append((sizes[method], method, keys, f"{system} {pipeline}"))
                    if not candidates:
                        continue
                    expected, method, keys, system = max(
                        candidates, key=lambda candidate: (len(candidate[2]), candidate[0])
                    )
                    for other in candidates:
                        if other[3] != system:
                            log.write(
                                f"{city}\t{day}\tskipped\t{other[3]}\t{other[1]} {other[0]} against {system} {method} {expected}\n"
                            )
                    placed = {key: other[1] for other in candidates for key in other[2] if other[3][0] == system[0]}
                    for unit, code, start, end, key, _, label, pipeline in rows:
                        if key in placed and (unit, areas[geoid]) not in owned:
                            memberships.append((unit, areas[geoid], start, end, placed[key]))
                    if expected == 0:
                        continue
                    chosen[method] += 1
                    universes += 1
                    cs.add(
                        "area_universe",
                        area_id=areas[geoid],
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
        kept = {(unit, area) for unit, area, *_ in memberships}
        stale = [
            pair
            for pair in conn.execute(
                """select distinct x.unit_id::text, x.area_id::text from reporting_unit_area x
                   join changeset c on c.id = x.changeset_id where c.pipeline = %s and x.area_id = any(%s::uuid[])""",
                [SOURCE, list(areas.values())],
            ).fetchall()
            if pair not in kept
        ]
        for unit, area in stale:
            cs.add("unit_area_drop", unit_id=unit, area_id=area, reason="no longer placed by an exact method")
        print(f"{universes} universes by {dict(chosen)}; {len(memberships)} memberships; {len(stale)} dropped")
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
