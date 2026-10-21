import math
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import polars as pl

from ea import coverage, raw, statewide_table
from ea.store import ROOT, connect

LOCK = 7_302_019
FEDERAL = ("president", "us_senate", "us_house")
STATE_LEVELS = (
    "president",
    "us_senate",
    "us_house",
    "governor",
    "statewide_exec",
    "judicial",
    "state_upper",
    "state_lower",
    "statewide_measure",
)
GROUPS = ("county", "municipality", "township", "school", "special", "other")
OFFSETS = {"us_house": 2, "state_lower": 2, "us_senate": 6}
JUDICIAL = {
    **dict.fromkeys(
        [
            "AL",
            "AK",
            "AZ",
            "AR",
            "CA",
            "CO",
            "FL",
            "GA",
            "ID",
            "IL",
            "IN",
            "IA",
            "KS",
            "KY",
            "LA",
            "MD",
            "MI",
            "MN",
            "MS",
            "MO",
            "MT",
            "NE",
            "NV",
            "NM",
            "NC",
            "ND",
            "OH",
            "OK",
            "OR",
            "SD",
            "TN",
            "TX",
            "UT",
            "WA",
            "WV",
            "WY",
        ],
        2,
    ),
    "PA": 2,
    "WI": 1,
}
KNOWN = {("GA", 2025, "statewide_exec", "general"): 2}
ACRONYMS = {"MUD", "ISD", "CISD", "PUD", "USD", "CSD", "WCID", "GCD", "CDD", "SID", "MSD", "UD", "LID", "FWSD"}
ABBREVIATIONS = {
    "SCH": "SCHOOL",
    "SCHS": "SCHOOL",
    "SCHOOLS": "SCHOOL",
    "DIST": "DISTRICT",
    "DISTRICTS": "DISTRICT",
    "DST": "DISTRICT",
    "UNIF": "UNIFIED",
    "UNI": "UNIFIED",
    "UN": "UNION",
    "ELEM": "ELEMENTARY",
    "EL": "ELEMENTARY",
    "JT": "JOINT",
    "VAL": "VALLEY",
    "VLY": "VALLEY",
    "CO": "COUNTY",
    "CNTY": "COUNTY",
    "TWP": "TOWNSHIP",
    "TWNSHP": "TOWNSHIP",
    "HS": "HIGH",
    "COMM": "COMMUNITY",
    "CMNTY": "COMMUNITY",
    "CONS": "CONSOLIDATED",
    "CONSOL": "CONSOLIDATED",
    "MT": "MOUNT",
    "ST": "SAINT",
    "FT": "FORT",
    "CTR": "CENTER",
    "REG": "REGIONAL",
    "RGNL": "REGIONAL",
    "HOSP": "HOSPITAL",
    "PK": "PARK",
    "REC": "RECREATION",
    "WTR": "WATER",
    "IMPR": "IMPROVEMENT",
    "IMP": "IMPROVEMENT",
    "AUTH": "AUTHORITY",
    "INDEP": "INDEPENDENT",
    "IND": "INDEPENDENT",
    "N": "NORTH",
    "S": "SOUTH",
    "E": "EAST",
    "W": "WEST",
    "CENT": "CENTRAL",
    "CEN": "CENTRAL",
    "CTRL": "CENTRAL",
    "ISD": "INDEPENDENT",
    "CISD": "CONSOLIDATED INDEPENDENT",
    "USD": "UNIFIED",
    "CSD": "COMMUNITY",
    "MUD": "MUNICIPAL UTILITY",
    "PUD": "PUBLIC UTILITY",
    "FPD": "FIRE",
    "WCID": "WATER CONTROL IMPROVEMENT",
    "CDD": "COMMUNITY DEVELOPMENT",
    "GCD": "GROUNDWATER CONSERVATION",
}
STOP = {
    "SCHOOL",
    "DISTRICT",
    "PUBLIC",
    "PROTECTION",
    "OF",
    "THE",
    "NO",
    "NUMBER",
    "AND",
    "SYSTEM",
    "CORPORATION",
    "CORP",
}
PREFIX = re.compile(
    r"^((?:CHARTER )?TOWNSHIP|CITY AND COUNTY|CITY|TOWN|VILLAGE|BOROUGH|COUNTY|PLANTATION|"
    r"(?:METRO|METROPOLITAN|UNIFIED|CONSOLIDATED) GOVERNMENT) OF (.+)$"
)

LOCAL_SQL = """
create temp table lc as
select coalesce(o.polity_id, b.polity_id, e.polity_id) as polity_id, cc.id, cc.year,
    extract(month from e.date_from)::int as month,
    cc.level <> 'local_measure' and cc.stage in ('general', 'primary', 'runoff') as regular, cc.stage,
    coalesce(cg.precinct, false) or cc.tacit as precinct
from cc
join contest c on c.id = cc.id
join election e on e.id = c.election_id
left join office o on o.id = c.office_id
left join body b on b.id = c.body_id
left join cg on cg.contest_id = cc.id
where cc.level in ('county', 'municipal', 'school', 'special_district', 'local_measure')
"""

HELD_SQL = """
select lc.polity_id, lc.year, count(*), count(*) filter (where lc.precinct), count(*) filter (where lc.regular),
    mode() within group (order by lc.month) filter (where lc.regular),
    count(*) filter (where lc.regular and lc.stage = 'general')
from lc group by 1, 2
"""

LOCAL_SHAPES_SQL = """
select lc.polity_id, lc.year, count(distinct ug.k), count(distinct ug.k) filter (where ug.shaped)
from lc join rs on rs.contest_id = lc.id join rsu on rsu.result_set_k = rs.k join ug on ug.k = rsu.unit_k
where ug.grain = 'precinct'
group by 1, 2
"""

POLITIES_SQL = """
select p.id, p.kind::text, e.value, n.text, ps.st
from polity p
join name n on n.entity_id = p.id and n.is_preferred
left join external_id e on e.entity_id = p.id and e.scheme = 'us.polity'
left join pstate ps on ps.id = p.id
where p.id in (select id from uspol) and p.kind not in ('sovereign_state', 'federal_unit', 'region')
"""


def tokens(text: str) -> frozenset[str]:
    text = re.sub(r"\([^)]*\)", " ", text.upper().replace("'", "").replace("’", ""))
    match = PREFIX.match(text)
    words = re.sub(r"[^A-Z0-9 ]", " ", (match[2] if match else text).replace("&", " AND ").replace("#", " ")).split()
    out = []
    for word in words:
        out += [w for w in ABBREVIATIONS.get(word, word).split() if w not in STOP]
    return frozenset(re.sub(r"^0+(?=\d)", "", w) for w in out)


def request() -> None:
    subprocess.Popen(
        [sys.executable, "-c", "from ea.cli import main; main()", "coverage", "refresh"],
        cwd=ROOT / "pipelines",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


FINGERPRINT_SQL = """
select concat_ws(':', current_date, count(*) filter (where status = 'published'), max(published_at),
    count(*) filter (where status = 'rejected'))
from changeset
"""


def fingerprint(conn) -> str:
    row = conn.execute(FINGERPRINT_SQL).fetchone()
    files = [Path(__file__).parent / name for name in ("coverage.py", "coverage_tables.py", "statewide_table.py")]
    files += sorted(raw.parsed("us.cog").glob("*.parquet"))
    return ":".join([row[0] if row else "", *(f"{f.name}{f.stat().st_mtime_ns}" for f in files)])


def refresh() -> str:
    built = []
    with connect(autocommit=True) as conn:
        conn.execute("update coverage_build set requested_at = now()")
        while True:
            locked = conn.execute("select pg_try_advisory_lock(%s)", [LOCK]).fetchone()
            if locked is None or not locked[0]:
                return "; ".join(built) or "a refresh is already running and will pick up this request"
            while (
                started := conn.execute(
                    """update coverage_build set started_at = now()
                       where built_at is null or requested_at > built_at returning started_at"""
                ).fetchone()
            ) is not None:
                conn.execute("create table if not exists coverage_cache_state (fingerprint text not null)")
                current = fingerprint(conn)
                previous = conn.execute("select fingerprint from coverage_cache_state").fetchone()
                if previous is not None and previous[0] == current:
                    built.append("nothing has changed since the last build")
                else:
                    built.append(build())
                    conn.execute("delete from coverage_cache_state")
                    conn.execute("insert into coverage_cache_state values (%s)", [current])
                conn.execute("update coverage_build set built_at = %s", [started[0]])
            conn.execute("select pg_advisory_unlock(%s)", [LOCK])
            pending = conn.execute("select built_at is null or requested_at > built_at from coverage_build").fetchone()
            if pending is None or not pending[0]:
                return "; ".join(built)


def build() -> str:
    started = time.monotonic()
    today = date.today()
    with connect() as conn:
        statewide_table.rebuild(conn)
        cells = coverage.matrix(conn, last=today.year)
        states = {fips: (st, name) for st, fips, name in conn.execute(coverage.STATES_SQL)}
        conn.execute(LOCAL_SQL)
        held: dict[str, dict[int, list]] = defaultdict(dict)
        for polity, year, n, precinct, regular, month, generals in conn.execute(HELD_SQL):
            held[str(polity)][year] = [n, precinct, regular, month, 0, 0, generals]
        for polity, year, n, shaped in conn.execute(LOCAL_SHAPES_SQL):
            held[str(polity)][year][4:6] = [n, shaped]
        polities = {str(pid): (kind, code, name, st) for pid, kind, code, name, st in conn.execute(POLITIES_SQL)}

        by_key = {(c["state"], c["year"], c["level"], c["stage"]): c for c in cells}
        season = {
            "primary": date(today.year, 9, 20),
            "general": date(today.year, 11, 2 + -date(today.year, 11, 1).weekday() % 7),
        }
        for stage, day in season.items():
            if today <= day:
                continue
            for (st, year, level, cell_stage), cell in list(by_key.items()):
                if cell_stage != stage or level not in STATE_LEVELS or year != today.year - OFFSETS.get(level, 4):
                    continue
                key = (st, today.year, level, stage)
                current = by_key.setdefault(
                    key,
                    {
                        "state": st,
                        "year": today.year,
                        "level": level,
                        "stage": stage,
                        "expected": "",
                        "held": 0,
                        "county_or_finer": 0,
                        "precinct": 0,
                        "precincts": 0,
                        "shaped_precincts": 0,
                        "target": coverage.target(st, level, today.year),
                    },
                )
                current["expected"] = max(current["expected"] or 0, cell["held"])

        known = dict(KNOWN)
        for st, period in JUDICIAL.items():
            for year in range(2000 + (st == "PA"), today.year + 1, period):
                if year == today.year and today <= (date(year, 4, 15) if st == "WI" else season["general"]):
                    continue
                retained = any(
                    "us.retention" in c.get("pipelines", "")
                    for (s, y, level, _), c in by_key.items()
                    if s == st and y == year and level == "statewide_measure"
                )
                known.setdefault((st, year, "judicial", "general"), 0 if retained else 1)
        for (st, year, level, stage), due in known.items():
            if due == 0:
                continue
            current = by_key.setdefault(
                (st, year, level, stage),
                {
                    "state": st,
                    "year": year,
                    "level": level,
                    "stage": stage,
                    "expected": "",
                    "held": 0,
                    "county_or_finer": 0,
                    "precinct": 0,
                    "precincts": 0,
                    "shaped_precincts": 0,
                    "target": coverage.target(st, level, year),
                },
            )
            current["expected"] = max(current["expected"] or 0, due, current["held"])

    cog = pl.read_parquet(raw.parsed("us.cog") / "governments.parquet")
    codes = {code: pid for pid, (_, code, _, _) in polities.items() if code is not None}
    link: dict[str, str] = {}
    for gid, kind, st, county, place in cog.select("id", "kind", "state_fips", "county_fips", "place_fips").iter_rows():
        candidates = [f"county:{st}{county}"] if kind == "county" else []
        if kind in ("municipality", "township") and place is not None:
            candidates = [f"place:{st}{place}", f"town:{st}{county}{place}"]
            if county is not None and county >= "500":
                candidates.append(f"county:{st}{county}")
            if st + place == "3651000":
                candidates.append("new-york-city")
        found = next((codes[c] for c in candidates if c in codes), None)
        if found is not None:
            link[gid] = found
    towns: dict[tuple[str, str], list[str]] = defaultdict(list)
    for code, pid in codes.items():
        if code.startswith("town:"):
            towns[(code[5:7], code[10:])].append(pid)
    for gid, kind, st, place in cog.select("id", "kind", "state_fips", "place_fips").iter_rows():
        if gid not in link and kind in ("municipality", "township") and len(towns.get((st, place or ""), [])) == 1:
            link[gid] = towns[(st, place)][0]
    pools: dict[tuple[str, str], list[tuple[str, str | None, frozenset[str]]]] = defaultdict(list)
    for gid, kind, name, st, county in cog.select("id", "kind", "name", "state_fips", "county_fips").iter_rows():
        if gid not in link:
            pools[(st, {"dependent_school": "school", "township": "municipality"}.get(kind, kind))].append(
                (gid, county, tokens(name))
            )
    taken = set(link.values())
    for code, pid in codes.items():
        prefix, _, rest = code.partition(":")
        if pid in taken or prefix not in ("school", "special", "place", "town", "county"):
            continue
        county = rest[2:5] if prefix == "special" else None
        mine = tokens(polities[pid][2])
        pool = pools.get((rest[:2], prefix if prefix in ("school", "special") else "municipality"), [])
        if not any(w.isdigit() for w in mine):
            pool = [(g, c, frozenset(w for w in t if not (w.isdigit() and len(w) >= 3))) for g, c, t in pool]
        digits = {w for w in mine if w.isdigit()}
        pool = [g for g in pool if county is None or g[1] == county]
        found = [g for g in pool if g[2] == mine]
        if len(found) != 1:
            ranked = sorted(
                ((len(mine & g[2]) / len(mine | g[2]), g) for g in pool if {w for w in g[2] if w.isdigit()} == digits),
                key=lambda pair: -pair[0],
            )
            if ranked and ranked[0][0] >= 0.75 and (len(ranked) == 1 or ranked[1][0] < ranked[0][0]):
                found = [ranked[0][1]]
            else:
                found = [g for _, g in ranked if mine < g[2]]
        if len(found) == 1 and found[0][0] not in link:
            link[found[0][0]] = pid
            taken.add(pid)

    governments: list[dict] = []
    for gid, kind, name, st, county, function, population, active in cog.select(
        "id", "kind", "name", "state_fips", "county_fips", "function", "population", "active"
    ).iter_rows():
        pid = link.get(gid)
        match = PREFIX.match(name)
        words = (
            name
            if match is None or "GOVERNMENT" in match[1] or match[1] == "CITY AND COUNTY"
            else f"{match[2]} {match[1]}"
            if match[1] in ("COUNTY", "TOWNSHIP", "CHARTER TOWNSHIP")
            else match[2]
        ).split()
        governments.append(
            {
                "id": gid,
                "state": states[st][0],
                "county": None if county is None else st + county,
                "kind": {"dependent_school": "school"}.get(kind, kind),
                "name": polities[pid][2]
                if pid is not None
                else " ".join(
                    w
                    if w in ACRONYMS
                    else w.lower()
                    if i > 0 and w in ("OF", "AND", "THE", "AT", "ON", "IN")
                    else "-".join(part.capitalize() for part in w.split("-"))
                    for i, w in enumerate(words)
                ),
                "function": None if function is None else function.split(" - ", 1)[-1].capitalize(),
                "population": population,
                "active": active,
                "polity_id": pid,
            }
        )
    for pid, years in held.items():
        if pid in taken or pid not in polities:
            continue
        kind, code, name, st = polities[pid]
        prefix = (code or "").partition(":")[0]
        governments.append(
            {
                "id": f"polity:{pid}",
                "state": st or "US",
                "county": None,
                "kind": "township"
                if prefix == "town" and kind == "municipality"
                else {"county": "county", "municipality": "municipality", "school_district": "school"}.get(
                    kind, "special" if kind == "special_district" else "other"
                ),
                "name": name,
                "function": None,
                "population": None,
                "active": True,
                "polity_id": pid,
            }
        )

    patterns: dict[tuple[str, str], Counter] = defaultdict(Counter)
    months: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for g in governments:
        years = held.get(g["polity_id"] or "", {})
        regular = sorted(y for y, v in years.items() if v[2] and 2000 <= y <= today.year)
        g["month"] = Counter(years[y][3] for y in regular).most_common(1)[0][0] if regular else None
        g["period"] = g["phase"] = None
        if len(regular) >= 2:
            step = 0
            for y in regular[1:]:
                step = math.gcd(step, y - regular[0])
            g["period"] = 4 if step % 4 == 0 else 2 if step % 2 == 0 else 1
            g["phase"] = regular[0] % g["period"]
            g["basis"] = "own"
            patterns[(g["state"], g["kind"])][(g["period"], g["phase"])] += 1
        elif regular:
            g["phase"] = regular[0]
            g["basis"] = "own year, state pattern"
        else:
            g["basis"] = "state pattern"
        if g["month"] is not None:
            months[(g["state"], g["kind"])][g["month"]] += 1

    years_out: list[tuple] = []
    for g in governments:
        group = (g["state"], g["kind"])
        if g["period"] is None:
            common = patterns[group].most_common(1)
            if g["phase"] is not None:
                period = Counter(p for p, _ in patterns[group].elements()).most_common(1)
                g["period"] = period[0][0] if period else 2
                g["phase"] = g["phase"] % g["period"]
            elif common:
                g["period"], g["phase"] = common[0][0]
            else:
                g["period"], g["phase"], g["basis"] = 1, 0, "unknown"
        if g["month"] is None and months[group]:
            g["month"] = months[group].most_common(1)[0][0]
        years = held.get(g["polity_id"] or "", {})
        for year in sorted(set(years) | set(range(2000, today.year + 1))):
            expected = (
                g["active"]
                and 2000 <= year <= today.year
                and (year - g["phase"]) % g["period"] == 0
                and (year < today.year or (g["month"] is not None and g["month"] < today.month))
            )
            n, precinct, _, _, precincts, shaped, generals = years.get(year, [0, 0, 0, None, 0, 0, 0])
            earlier = years.get(year - 4, [0] * 7)[6] if year < today.year else 0
            if expected or n:
                years_out.append(
                    (
                        g["id"],
                        year,
                        expected,
                        n,
                        expected and (n == 0 or generals < earlier),
                        precinct,
                        precincts,
                        shaped,
                    )
                )
    leaves: dict[tuple[int, str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0, 0, 0])
    kinds = {g["id"]: (g["state"], g["kind"]) for g in governments}
    for gid, year, expected, n, missing, precinct, precincts, shaped in years_out:
        st, kind = kinds[gid]
        cell = leaves[(year, st, kind)]
        cell[0] += 1
        cell[1] += expected
        cell[2] += n
        cell[3] += missing
        cell[4] += n - precinct + (expected and n == 0)
        cell[5] += precincts - shaped
        cell[6] += precincts
        cell[7] += shaped
        cell[8] += n + (expected and n == 0)

    def node(path: str, parent: str | None, year: int, sort: int, label: str, kind: str) -> dict:
        return {
            "path": path,
            "parent": parent,
            "year": year,
            "sort": sort,
            "label": label,
            "kind": kind,
            "expected": None,
            "held": 0,
            "governments": None,
            "missing": None,
            "county": None,
            "precinct": None,
            "shapes": None,
            "precincts": 0,
            "shaped": 0,
            "county_total": None,
            "precinct_total": None,
            "shapes_total": None,
            "turnout": None,
            "turnout_total": None,
        }

    nodes: dict[str, dict] = {}

    def add(parent: str | None, chain: list[tuple[str, int, str, str]], year: int, values: dict) -> None:
        for path, sort, label, kind in chain:
            if path not in nodes:
                nodes[path] = node(path, parent, year, sort, label, kind)
            row = nodes[path]
            for field, value in values.items():
                if value is not None:
                    row[field] = (row[field] or 0) + value
            parent = path

    names = dict(states.values())
    order = {st: i for i, st in enumerate(sorted(names, key=lambda s: names[s]))}
    for (st, year, level, stage), c in by_key.items():
        if level not in STATE_LEVELS or (st != "US" and st not in names):
            continue
        expected = None if c["expected"] == "" else c["expected"]
        base = max(expected or 0, c["held"])
        county = (
            base - c["county_or_finer"]
            if st != "US" and c["target"] in ("county", "precinct") and level not in ("state_upper", "state_lower")
            else None
        )
        precinct = base - c["precinct"] if c["target"] == "precinct" else None
        shapes = c["target"] == "precinct" and c["precincts"] > 0
        values = {
            "expected": expected,
            "held": c["held"],
            "missing": max(0, (expected or 0) - c["held"]),
            "county": county,
            "precinct": precinct,
            "shapes": c["precincts"] - c["shaped_precincts"] if shapes else None,
            "precincts": c["precincts"],
            "shaped": c["shaped_precincts"],
            "county_total": None if county is None else base,
            "precinct_total": None if precinct is None else base,
            "shapes_total": c["precincts"] if shapes else None,
            "turnout": c["held"] - c.get("with_turnout", 0),
            "turnout_total": c["held"],
        }
        if str(year) not in nodes:
            nodes[str(year)] = node(str(year), None, year, -year, str(year), "year")
        level_sort = STATE_LEVELS.index(level)
        if level in FEDERAL:
            add(
                None if st == "US" else str(year),
                [
                    *([(str(year), -year, str(year), "year")] if st == "US" else []),
                    (f"{year}/federal", -1, "federal", "federal"),
                    (f"{year}/federal/{level}", level_sort, level, "level"),
                    (
                        f"{year}/federal/{level}/{st}",
                        order.get(st, -1),
                        "national" if st == "US" else names[st],
                        "state",
                    ),
                ],
                year,
                values,
            )
        if st == "US":
            continue
        add(
            None,
            [
                (str(year), -year, str(year), "year"),
                (f"{year}/{st}", order[st], names[st], "state"),
                (f"{year}/{st}/{level}", level_sort, level, "level"),
                (f"{year}/{st}/{level}/{stage}", coverage.STAGES.index(stage), stage, "stage"),
            ],
            year,
            values,
        )
    for (year, st, kind), (count, expected, n, missing, precinct, shapes, precincts, shaped, total) in leaves.items():
        if st not in names:
            continue
        add(
            None,
            [
                (str(year), -year, str(year), "year"),
                (f"{year}/{st}", order[st], names[st], "state"),
                (f"{year}/{st}/{kind}", len(STATE_LEVELS) + GROUPS.index(kind), kind, "governments"),
            ],
            year,
            {
                "expected": expected,
                "held": n,
                "governments": count,
                "missing": missing,
                "county": None,
                "precinct": precinct if year >= 2000 else None,
                "shapes": shapes if year >= 2000 and precincts else None,
                "precincts": precincts,
                "shaped": shaped,
                "precinct_total": total if year >= 2000 else None,
                "shapes_total": precincts if year >= 2000 and precincts else None,
            },
        )
    rows = list(nodes.values())

    with connect() as conn, conn.transaction():
        conn.execute("truncate coverage_row, coverage_government_year, coverage_government")
        with conn.cursor().copy(
            "copy coverage_government (id, state, county, kind, name, function, population, active, polity_id, period,"
            " phase, month, basis) from stdin"
        ) as copy:
            for g in governments:
                copy.write_row(
                    [
                        g[k]
                        for k in (
                            "id",
                            "state",
                            "county",
                            "kind",
                            "name",
                            "function",
                            "population",
                            "active",
                            "polity_id",
                            "period",
                            "phase",
                            "month",
                            "basis",
                        )
                    ]
                )
        with conn.cursor().copy(
            "copy coverage_government_year (government_id, year, expected, held, missing, precinct, precincts, shaped)"
            " from stdin"
        ) as copy:
            for row in years_out:
                copy.write_row(row)
        with conn.cursor().copy(
            "copy coverage_row (path, parent, year, sort, label, kind, expected, held, governments, missing, county,"
            " precinct, shapes, precincts, shaped, county_total, precinct_total, shapes_total, turnout,"
            " turnout_total) from stdin"
        ) as copy:
            for row in rows:
                copy.write_row(list(row.values()))
    return (
        f"{len(governments):,} governments ({len(link):,} linked), {len(years_out):,} government years, "
        f"{len(rows):,} rows in {time.monotonic() - started:.0f}s"
    )
