import csv
import time
from collections import defaultdict
from datetime import date

from ea.store import ROOT, Connection, connect

LEVELS = (
    "president",
    "us_senate",
    "us_house",
    "governor",
    "statewide_exec",
    "judicial",
    "state_upper",
    "state_lower",
    "county",
    "municipal",
    "school",
    "special_district",
    "statewide_measure",
    "local_measure",
)
STAGES = ("general", "primary", "runoff", "special")
LOCAL = {"county", "municipal", "school", "special_district", "local_measure"}
ERAS = ((1789, 1823), (1824, 1899), (1900, 1966), (1967, 1999), (2000, 2015), (2016, 2025))
LAST = 2025

CONTESTS_SQL = """
create temp table uspol as
with recursive t(id) as (
    select id from polity where iso_code = 'US'
    union select pp.polity_id from polity_parent pp join t on pp.parent_id = t.id
)
select distinct id from t;

create temp table pstate as
with recursive up(id, anc) as (
    select id, id from uspol
    union select up.id, pp.parent_id from up join polity_parent pp on pp.polity_id = up.anc
)
select up.id, min(substr(p.iso_code, 4)) as st
from up join polity p on p.id = up.anc
where p.kind in ('federal_unit', 'region')
group by up.id;

create temp table cc as
select c.id, extract(year from e.date_from)::int as year, coalesce(ps.st, 'US') as st,
    case
        when c.kind = 'ballot_question' then
            case when p.kind in ('sovereign_state', 'federal_unit', 'region') then 'statewide_measure'
            else 'local_measure' end
        when c.kind in ('elector_slate', 'delegate_allocation') or oe.value in ('president', 'vice_president')
            then 'president'
        when be.value = 'house' then 'us_house'
        when be.value = 'senate' then 'us_senate'
        when o.kind = 'governor' then 'governor'
        when b.kind in ('upper_house', 'unicameral') and op.kind in ('federal_unit', 'region') then 'state_upper'
        when b.kind = 'lower_house' and op.kind in ('federal_unit', 'region') then 'state_lower'
        when op.kind in ('federal_unit', 'region') and oe.value ~ '(justice|judge|court)' then 'judicial'
        when op.kind in ('federal_unit', 'region') then 'statewide_exec'
        when op.kind = 'school_district' or coalesce(oe.value, be.value) ~ '(school|education)' then 'school'
        when op.kind = 'county' then 'county'
        when op.kind = 'municipality' then 'municipal'
        else 'special_district'
    end as level,
    case
        when e.kind = 'runoff' then 'runoff'
        when c.party_scope_id is not null or e.kind in ('primary', 'caucus') then 'primary'
        when e.kind in ('special', 'by_election') or c.term = 'remainder' then 'special'
        else 'general'
    end as stage,
    c.default_result_kind = 'tacit' as tacit,
    cs.pipeline
from contest c
join election e on e.id = c.election_id
join uspol u on u.id = e.polity_id
join polity p on p.id = e.polity_id
left join pstate ps on ps.id = e.polity_id
left join office o on o.id = c.office_id
left join body b on b.id = c.body_id
left join polity op on op.id = coalesce(o.polity_id, b.polity_id, e.polity_id)
left join external_id oe on oe.entity_id = c.office_id and oe.scheme = 'us.office'
left join external_id be on be.entity_id = c.body_id and be.scheme = 'us.body'
join changeset cs on cs.id = c.changeset_id
where e.date_from is not null;

update cc set year = coalesce(
        (select 1786 + 2 * max(n) from contest c join tenure t on t.contest_id = c.id
         cross join generate_series(1, 119) n
         where c.id = cc.id and t.from_date >= case when n <= 73 then make_date(1787 + 2 * n, 3, 4)
             else make_date(1787 + 2 * n, 1, 3) end),
        (select case when year % 2 = 1 then year - 1
             when e.date_from < make_date(year, case when year < 1935 then 3 else 1 end, case when year < 1935 then 4 else 3 end)
             then year - 2 else year end
         from contest c join election e on e.id = c.election_id where c.id = cc.id))
where level = 'us_house' and stage = 'general';

create temp table rs as
select r.k, r.contest_id, cs.pipeline,
    s.publisher in ('Inter-university Consortium for Political and Social Research', 'Algara and Amlani') as derived
from result_set r join cc on cc.id = r.contest_id join changeset cs on cs.id = r.changeset_id
left join source s on s.id = r.source_id
where r.status <> 'withdrawn';

create temp table rsm as
select distinct rv.result_set_k, rv.measure from result_value rv
where rv.measure in (2, 3, 14, 24, 25) and rv.result_set_k in (select k from result_set where status <> 'withdrawn');

create temp table tn as
select cc.id from cc join contest c on c.id = cc.id
where exists (select from result_set r join rsm on rsm.result_set_k = r.k and rsm.measure in (2, 24)
        where r.election_id = c.election_id and (r.contest_id is null or r.contest_id = c.id))
    and exists (select from result_set r join rsm on rsm.result_set_k = r.k and rsm.measure in (3, 14, 25)
        where r.election_id = c.election_id and (r.contest_id is null or r.contest_id = c.id));

create temp table rsu as
select distinct rv.result_set_k, rv.unit_k from result_value rv where rv.result_set_k in (select k from rs);

create temp table ug as
select u.k,
    u.scheme_code,
    case when u.scheme_code in ('us.precinct', 'us.nyc_ed', 'us-ia.caucus_precinct') then 'precinct'
        when u.scheme_code in ('us.county', 'us.town', 'us.county_part', 'us.former_county', 'us.ak_house_district',
            'us.place') then 'county'
        else 'total' end as grain,
    u.area_relation = 'equals' and exists (select from area_geometry g where g.area_id = u.area_id) as shaped
from reporting_unit u where u.k in (select unit_k from rsu);

create temp table cd as
select cc.id as contest_id, cc.st, e.date_from as d
from cc join contest c on c.id = cc.id join election e on e.id = c.election_id
where cc.year >= 2000 and cc.level in ('president', 'us_senate', 'governor', 'statewide_exec', 'judicial', 'statewide_measure');

create temp table cx as
select s.st, s.d, count(u.k) as n
from (select distinct st, d from cd) s
left join (reporting_unit u join pstate ps on ps.id = u.polity_id) on u.scheme_code = 'us.county' and ps.st = s.st and u.valid @> s.d
group by 1, 2;

create temp table cu as
select cd.contest_id, cx.n as expected, count(distinct u.k) as present
from cd join cx on cx.st = cd.st and cx.d = cd.d
left join (rs join rsu on rsu.result_set_k = rs.k join reporting_unit u on u.k = rsu.unit_k and u.scheme_code = 'us.county')
    on rs.contest_id = cd.contest_id and u.valid @> cd.d
group by cd.contest_id, cx.n;

create temp table cg as
select rs.contest_id,
    bool_or(ug.grain = 'precinct') as precinct,
    bool_or(ug.grain = 'county' and (ug.scheme_code <> 'us.county' or cu.contest_id is null
        or cu.expected = 0 or cu.present >= 0.9 * cu.expected)) as county,
    bool_or(ug.grain = 'county' and (ug.scheme_code <> 'us.county' or cu.contest_id is null
        or cu.expected = 0 or cu.present >= 0.9 * cu.expected) and not coalesce(rs.derived, false)) as county_free
from rs join rsu on rsu.result_set_k = rs.k join ug on ug.k = rsu.unit_k
left join cu on cu.contest_id = rs.contest_id
group by rs.contest_id;
"""

COUNT_SQL = """
with c as (
    select cc.*, cg.precinct, cg.county, cg.county_free, exists (select from rs where rs.contest_id = cc.id) as results,
        exists (select from tn where tn.id = cc.id) as turnout,
        tacit and stage in ('primary', 'runoff') and st in ('AL', 'AR', 'CO', 'CT', 'DE', 'FL', 'KY', 'LA', 'MS', 'NC',
            'NY', 'OK', 'SC', 'SD', 'UT', 'VA') as cancelled
    from cc left join cg on cg.contest_id = cc.id
)
select st, year, level, stage, count(*), count(*) filter (where results),
    count(*) filter (where county or precinct or cancelled), count(*) filter (where precinct or cancelled),
    count(*) filter (where county and not county_free and not coalesce(precinct, false)),
    count(*) filter (where turnout)
from c group by 1, 2, 3, 4
"""

SHAPES_SQL = """
select cc.st, cc.year, cc.level, cc.stage, count(distinct ug.k), count(distinct ug.k) filter (where ug.shaped)
from cc join rs on rs.contest_id = cc.id join rsu on rsu.result_set_k = rs.k join ug on ug.k = rsu.unit_k
where ug.grain = 'precinct'
group by 1, 2, 3, 4
"""

PIPELINES_SQL = """
select distinct cc.st, cc.year, cc.level, cc.stage, p
from cc left join rs on rs.contest_id = cc.id, unnest(array[cc.pipeline, rs.pipeline]) p
where p is not null
"""

STATES_SQL = """
select substr(p.iso_code, 4), ac.code, n.text
from polity p
join polity_territory pt on pt.polity_id = p.id
join area a on a.id = pt.area_id and a.scheme_code = 'us.state'
join area_code ac on ac.area_id = a.id
join name n on n.entity_id = p.id and n.is_preferred
where p.iso_code like 'US-%'
"""

ELECTORS_SQL = """
select distinct extract(year from e.date_from)::int / 4 * 4, substr(p.iso_code, 4)
from contest c
join election e on e.id = c.election_id
join result_set r on r.contest_id = c.id
join result_value v on v.result_set_k = r.k
join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.state'
join polity p on p.id = u.polity_id
where e.kind = 'indirect' and c.kind = 'office' and v.measure = 11
"""

DISTRICTS_SQL = """
select n, substr(ac.code, 1, 2), count(distinct a.id) filter (where substr(ac.code, 3) <> '00'),
    bool_or(substr(ac.code, 3) = '00')
from generate_series(1, 119) n
cross join lateral (select case when n <= 73 then make_date(1787 + 2 * n, 3, 4) else make_date(1787 + 2 * n, 1, 3) end
    as day) d
join area_geometry g on g.valid @> d.day
join area a on a.id = g.area_id and a.scheme_code = 'us.cd'
join area_code ac on ac.area_id = a.id
group by 1, 2
"""

AT_LARGE_SQL = """
select cc.st, cc.year, count(*) from cc
join contest c on c.id = cc.id
join area_code ac on ac.area_id = c.district_area_id and substr(ac.code, 3) = '00'
join area a on a.id = ac.area_id and a.scheme_code = 'us.cd'
where cc.level = 'us_house' and cc.stage = 'general'
group by 1, 2
"""

SENATE_SQL = """
select s.label from seat s join external_id e on e.entity_id = s.body_id and e.scheme = 'us.body' and e.value = 'senate'
"""

LEGISLATIVE_POPULAR_GAPS = {1824: {"DE", "GA", "LA", "NY", "SC", "VT"}, 1828: {"DE", "SC"}, 1864: {"LA"}, 1868: {"FL"}}
OFF_CYCLE_STATEWIDE = {("NH", 2022), ("WA", 2022), ("IL", 2016), ("AR", 2024), ("NM", 2016), ("UT", 2022)}
CLASS_OFFSET = {"I": 2, "II": 4, "III": 0}
GOVERNOR_CYCLES = {
    "midterm": "AL AK AZ CA CO CT FL GA HI ID IL IA KS ME MD MA MI MN NE NV NM NY OH OK OR PA SC SD TN TX WI WY GU VI",
    "presidential": "DE IN MO MT NC ND UT WA WV PR",
    "biennial": "NH VT",
    "virginia": "VA NJ",
    "kentucky": "KY MS LA",
}
GOVERNOR_YEARS = {
    "AR": [*range(1974, 1986, 2), *range(1986, LAST + 1, 4)],
    "RI": [*range(1974, 1994, 2), *range(1994, LAST + 1, 4)],
    "IL": [1976, *range(1978, LAST + 1, 4)],
    "AS": [*range(1980, LAST + 1, 4)],
}
ODD_LEGISLATURES = {
    2023: {"LA": 2019, "MS": 2019, "VA": 2019, ("NJ", "state_lower"): 2019, ("NJ", "state_upper"): 2021},
    2025: {("NJ", "state_lower"): 2021, ("VA", "state_lower"): 2021},
}


def era(year: int) -> str:
    for first, last in ERAS:
        if first <= year <= last:
            return f"{first}–{last}"
    return "other"


def target(st: str, level: str, year: int) -> str:
    if st == "US":
        return "total"
    if year >= 2000:
        return "precinct"
    if year >= 1824 and level not in LOCAL and level not in ("state_upper", "state_lower"):
        return "county"
    return "total"


def owner(level: str, pipelines: set[str]) -> str:
    if any(p.startswith("us.nyc_") for p in pipelines):
        return "nyc lane"
    return "local lane" if level in LOCAL else "coverage lane"


def governor_years(st: str) -> set[int]:
    if st in GOVERNOR_YEARS:
        return set(GOVERNOR_YEARS[st])
    for cycle, states in GOVERNOR_CYCLES.items():
        if st in states.split():
            start = {"midterm": 1974, "presidential": 1976, "biennial": 1974, "virginia": 1977, "kentucky": 1975}[cycle]
            return set(range(start, LAST + 1, 2 if cycle == "biennial" else 4))
    return set()


def matrix(conn: Connection, last: int = LAST) -> list[dict]:
    conn.execute("set statement_timeout = 0")
    for statement in CONTESTS_SQL.split(";\n\n"):
        conn.execute(statement)
    held = {
        (st, year, level, stage): {
            "held": n,
            "results": results,
            "county": county,
            "precinct": precinct,
            "icpsr_only": icpsr,
            "turnout": turnout,
        }
        for st, year, level, stage, n, results, county, precinct, icpsr, turnout in conn.execute(COUNT_SQL)
    }
    shapes = {
        (st, year, level, stage): (n, shaped) for st, year, level, stage, n, shaped in conn.execute(SHAPES_SQL)
    }
    pipelines: dict[tuple, set[str]] = defaultdict(set)
    for st, year, level, stage, pipeline in conn.execute(PIPELINES_SQL):
        pipelines[(st, year, level, stage)].add(pipeline)
    states = {fips: (st, name) for st, fips, name in conn.execute(STATES_SQL)}
    electors = list(conn.execute(ELECTORS_SQL))
    districts = list(conn.execute(DISTRICTS_SQL))
    at_large = {(st, year): n for st, year, n in conn.execute(AT_LARGE_SQL)}
    seats = [label for (label,) in conn.execute(SENATE_SQL)]

    expected: dict[tuple, int] = defaultdict(int)
    for year, st in electors:
        if year >= 1824 and st not in LEGISLATIVE_POPULAR_GAPS.get(year, set()) and not (st == "SC" and year <= 1860):
            expected[(st, year, "president", "general")] += (
                1 + (2 if st == "ME" and year >= 1972 else 0) + (3 if st == "NE" and year >= 1992 else 0)
            )
    for n, fips, count, statewide in districts:
        if fips in states:
            st, year = states[fips][0], 1786 + 2 * n
            expected[(st, year, "us_house", "general")] += count + (
                (1 if count == 0 else at_large.get((st, year), 0)) if statewide else 0
            )
    by_name = {name: st for st, name in states.values()}
    for label in seats:
        name, _, numeral = label.rpartition(" Class ")
        st = by_name[name]
        first = 1960 if st in ("AK", "HI") else 1914
        for year in range(first, LAST + 1):
            if year % 6 == CLASS_OFFSET[numeral]:
                expected[(st, year, "us_senate", "general")] += 1

    for (st, year, level, stage), cell in held.items():
        if level == "governor" and stage == "general" and year < 1974:
            expected[(st, year, level, stage)] = max(expected[(st, year, level, stage)], 1)
    for st, _ in states.values():
        for year in governor_years(st):
            expected[(st, year, "governor", "general")] = 1

    for (st, year, level, stage), cell in list(held.items()):
        if level in ("state_upper", "state_lower") and stage == "general" and 1967 <= year <= 2022:
            expected[(st, year, level, stage)] = max(expected[(st, year, level, stage)], cell["held"])

    def chamber(st: str, year: int, level: str) -> int:
        return held.get((st, year, level, "general"), {}).get("held", 0)

    for st, _ in states.values():
        for level in ("state_upper", "state_lower"):
            for year, sources in ODD_LEGISLATURES.items():
                source = sources.get((st, level), sources.get(st))
                if source:
                    expected[(st, year, level, "general")] = chamber(st, source, level)
            counts = {year: chamber(st, year, level) for year in (2016, 2018, 2020, 2022)}
            if level == "state_lower" and all(counts[y] for y in (2018, 2020, 2022)):
                expected[(st, 2024, level, "general")] = max(counts[2022], chamber(st, 2024, level))
            staggered = counts[2018] >= counts[2016] / 2 and abs(counts[2020] - counts[2016]) <= counts[2016] / 10
            if level == "state_upper" and all(counts.values()) and staggered:
                expected[(st, 2024, level, "general")] = max(counts[2020], chamber(st, 2024, level))
            if st == "VT":
                for year in range(1968, 1986, 2):
                    expected[(st, year, level, "general")] = chamber(st, 1986, level)
            if st == "NC":
                expected[(st, 1968, level, "general")] = chamber(st, 1970, level)

    statewide: dict[str, set[int]] = defaultdict(set)
    for st, year, level, stage in held:
        if level == "statewide_exec" and stage == "general" and year >= 1990 and (st, year) not in OFF_CYCLE_STATEWIDE:
            statewide[st].add(year)
    for st, recent in statewide.items():
        period = 2 if st in ("NH", "VT") else 4
        for year in range(2000, LAST + 1):
            if any((year - y) % period == 0 for y in recent):
                key = (st, year, "statewide_exec", "general")
                expected[key] = max(expected[key], held.get(key, {}).get("held", 0), 1)

    for (st, year, level, stage), cell in list(held.items()):
        if level in ("president", "us_house", "us_senate") and stage == "primary" and year in (2020, 2022):
            key = (st, year + 4 if level == "president" else year + 2, level, stage)
            expected[key] = max(expected[key], cell["held"])

    rows: list[dict] = []
    for key in sorted(set(held) | set(expected)):
        st, year, level, stage = key
        if year > last:
            continue
        cell = held.get(key, {"held": 0, "results": 0, "county": 0, "precinct": 0, "icpsr_only": 0, "turnout": 0})
        exp = expected.get(key)
        base = max(exp or 0, cell["held"])
        goal = target(st, level, year)
        attained = {"precinct": cell["precinct"], "county": cell["county"], "total": cell["results"]}[goal]
        precincts, shaped = shapes.get(key, (0, 0))
        rows.append(
            {
                "state": st,
                "year": year,
                "level": level,
                "stage": stage,
                "expected": "" if exp is None else exp,
                "held": cell["held"],
                "missing": max(0, (exp or 0) - cell["held"]),
                "with_results": cell["results"],
                "county_or_finer": cell["county"],
                "precinct": cell["precinct"],
                "with_turnout": cell["turnout"],
                "precinct_share": round(cell["precinct"] / cell["held"], 3) if cell["held"] else 0,
                "grain": "precinct"
                if cell["precinct"]
                else "county"
                if cell["county"] and level not in LOCAL
                else "total"
                if cell["results"]
                else "none",
                "precincts": precincts,
                "shaped_precincts": shaped,
                "shape_share": round(shaped / precincts, 3) if precincts else "",
                "target": goal,
                "gap": base - attained,
                "icpsr_only": cell["icpsr_only"],
                "owner": owner(level, pipelines.get(key, set())),
                "pipelines": " ".join(sorted(pipelines.get(key, set()))),
            }
        )

    return rows


def us() -> str:
    started = time.monotonic()
    with connect() as conn:
        rows = matrix(conn)
    spec = ROOT / ".agents" / "spec"
    with (spec / "us-coverage.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (spec / "us-coverage.md").write_text(report(rows))
    return f"{len(rows)} cells, {sum(r['held'] for r in rows)} contests in {time.monotonic() - started:.0f}s"


def table(header: list[str], body: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in body]
    return "\n".join(lines)


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "–"


def top_states(rows: list[dict], field: str, n: int = 6) -> str:
    by_state: dict[str, int] = defaultdict(int)
    for row in rows:
        by_state[row["state"]] += row[field]
    ranked = sorted((v, s) for s, v in by_state.items() if v > 0)[::-1][:n]
    return ", ".join(f"{s} {v:,}" for v, s in ranked)


def report(rows: list[dict]) -> str:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["level"], row["stage"], era(row["year"]), row["target"], row["owner"])].append(row)
    ranked = []
    for (level, stage, span, goal, who), members in groups.items():
        gap = sum(r["gap"] for r in members)
        if gap <= 0:
            continue
        held = sum(r["held"] for r in members)
        missing = sum(r["missing"] for r in members)
        ranked.append(
            (
                gap,
                [level, stage, span, goal, f"{gap:,}", f"{missing:,}", f"{held:,}", top_states(members, "gap"), who],
            )
        )
    ranked.sort(key=lambda r: -r[0])

    levels = []
    for level in LEVELS:
        members = [r for r in rows if r["level"] == level]
        held = sum(r["held"] for r in members)
        if not held and not any(r["expected"] for r in members):
            levels.append([level, 0, "–", "–", "–", "–", "–"])
            continue
        modern = [r for r in members if r["year"] >= 2000]
        older = [r for r in members if 1824 <= r["year"] < 2000]
        modern_held = sum(r["held"] for r in modern)
        older_held = sum(r["held"] for r in older)
        precincts = sum(r["precincts"] for r in modern)
        years = [r["year"] for r in members if r["held"]]
        levels.append(
            [
                level,
                f"{held:,}",
                f"{min(years)}–{max(years)}" if years else "–",
                f"{sum(r['missing'] for r in members):,}",
                pct(sum(r["precinct"] for r in modern), modern_held),
                pct(sum(r["shaped_precincts"] for r in modern), precincts),
                pct(sum(r["county_or_finer"] for r in older), older_held),
            ]
        )

    precinct_years = []
    for year in range(2000, LAST + 1):
        members = [r for r in rows if r["year"] == year and r["level"] not in LOCAL]
        held = sum(r["held"] for r in members)
        precincts = sum(r["precincts"] for r in members)
        cells = [year, f"{held:,}"]
        for level in ("president", "us_senate", "us_house", "governor", "statewide_exec", "state_upper", "state_lower"):
            subset = [r for r in members if r["level"] == level and r["stage"] == "general"]
            cells.append(pct(sum(r["precinct"] for r in subset), sum(r["held"] for r in subset)))
        cells.append(pct(sum(r["shaped_precincts"] for r in members), precincts))
        precinct_years.append(cells)

    missing_rows = []
    for level in ("president", "us_house", "us_senate", "governor", "statewide_exec", "state_upper", "state_lower"):
        for stage in STAGES:
            for first, last in ERAS:
                members = [
                    r for r in rows if r["level"] == level and r["stage"] == stage and first <= r["year"] <= last
                ]
                expected = sum(r["expected"] or 0 for r in members)
                missing = sum(r["missing"] for r in members)
                if missing:
                    missing_rows.append(
                        [
                            level,
                            stage,
                            f"{first}–{last}",
                            f"{expected:,}",
                            f"{missing:,}",
                            top_states(members, "missing"),
                        ]
                    )

    shape_rows = []
    for year in range(2000, LAST + 1):
        members = [r for r in rows if r["year"] == year and r["precincts"]]
        precincts = sum(r["precincts"] for r in members)
        if precincts:
            shaped = sum(r["shaped_precincts"] for r in members)
            unshaped = defaultdict(int)
            for r in members:
                unshaped[r["state"]] += r["precincts"] - r["shaped_precincts"]
            worst = ", ".join(
                f"{s} {v:,}" for v, s in sorted(((v, s) for s, v in unshaped.items() if v), reverse=True)[:8]
            )
            shape_rows.append([year, f"{precincts:,}", pct(shaped, precincts), worst])

    icpsr = defaultdict(int)
    for r in rows:
        if r["icpsr_only"]:
            icpsr[(r["level"], era(r["year"]))] += r["icpsr_only"]

    local = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        if r["owner"] == "local lane" and r["year"] >= 2000:
            local[r["level"]][0] += r["held"]
            local[r["level"]][1] += r["precinct"]
            local[r["level"]][2] += r["gap"]

    today = date.today().isoformat()
    total = sum(r["held"] for r in rows)
    return f"""# US coverage

Generated by `ea coverage us` on {today} from the database, measured against the US v1 goal in `us-v1-plan.md`. Do not edit by hand; rerun the command after each fill. `us-coverage.csv` holds the full matrix: one row per state (or `US` for national contests), year, office level, and stage, with contests held and expected, the finest grain held, the share with precinct results, the share of those precincts with shapes, the target grain, the gap, the owning lane, and the pipelines.

{total:,} US contests held.

## How it is measured

- Level comes from the contest's office or body and its polity: the president (state slates, national contests, Electoral College votes, and primary delegate allocations), the US Senate and House, governors, other statewide offices (`judicial` when the office names a justice, judge, or court), state upper and lower chambers (Nebraska's unicameral counts as upper), county, municipal, and school district offices and bodies (county and city offices naming a school or education board count as school), and statewide and local ballot questions. No special district polities exist yet, so that level is empty.
- Stage: `runoff` for runoff elections (general and primary), `primary` for party-scoped contests and primary, top-two, and caucus elections, `special` for special elections and remainder terms on a regular day, and `general` otherwise.
- Grain: a contest is `precinct` when any of its result sets has a precinct unit (`us.precinct`, `us.nyc_ed`, Iowa caucus precincts), `county` when any has a county, town, county part, Alaska house district, or place unit, `total` when it has any result set, and `none` otherwise. A precinct has a shape when its unit equals an area with geometry. `icpsr_only` counts contests whose only county-level results come from ICPSR or from Algara and Amlani's returns, which are compiled from ICPSR and CQ (`us.icpsr`, `us.algara`, `us.algara_federal`; the ICPSR replacement lane's work). A result set is matched by its source's publisher, not by the changeset that last wrote it, since another pipeline that makes a set non-default restages it.
- Target: precinct from 2000, county from 1824 to 1999 (total for local offices, local measures, and state legislative districts, whose county breakdowns before 2000 are not measured), and the contest total before 1824. `gap` is the larger of contests expected and held, less those reaching the target. A primary or runoff listed as unopposed with no vote (the FEC's `Unopposed`, held as a tacit result) needs no breakdown in the states whose law cancels an uncontested primary and declares the lone candidate nominated: Alabama, Arkansas, Colorado (when the whole primary is canceled), Connecticut, Delaware, Florida, Kentucky, Louisiana, Mississippi, New York, North Carolina, Oklahoma, South Carolina, South Dakota, Utah, and Virginia.
- Expected contests: presidential slates for each state casting electoral votes from 1824 (less the states whose legislatures chose electors, and Maine's and Nebraska's districts), House seats from `us.cd` districts in force at each Congress's start, with a state's at-large seat counted only when it has no numbered districts or a source holds an at-large race that cycle (each race filed under the even year before the Congress its winner was seated in, or, with no seated winner, the Congress its date falls in), regular Senate elections from each state's seat classes since 1914 (1960 in Alaska and Hawaii), governors from 1974 by each state's cycle and before 1974 wherever any source holds one, state legislative generals as Klarner holds them 1967–2022 (Vermont 1968–1984 and North Carolina 1968, which Klarner lacks, at their next counts) and 2023–2025 at the count four years earlier, other statewide offices from 2000 on every cycle in which a state elected any since 1990 (at least one contest per year; two-year cycles in New Hampshire and Vermont; one-off contests outside a state's cycle, such as Illinois's 2016 comptroller special, set no cycle), and 2024 congressional and presidential primaries at their 2022 and 2020 counts. Specials, runoffs, judicial races, ballot measures, local races, and anything before these windows have no calendar, so they count only what is held.

## Coverage by level

Contests held, years spanned, contests missing against the calendar, the share of contests from 2000 with precinct results, the share of those precincts with shapes, and the share of 1824–1999 contests with county or finer results.

{table(["Level", "Contests", "Years", "Missing", "Precinct, 2000+", "Shaped", "County, 1824–1999"], levels)}

## Gaps ranked by size

Each row groups the matrix by level, stage, era, target, and owner. Gap is contests short of the target grain (missing contests included); missing is contests the calendar expects that no source holds.

{table(["Level", "Stage", "Era", "Target", "Gap", "Missing", "Held", "Largest states", "Owner"], [r for _, r in ranked[:60]])}

## Missing contests against the calendar

{table(["Level", "Stage", "Era", "Expected", "Missing", "Largest states"], missing_rows)}

## Precinct results, 2000 on

Contests held at state and federal levels per year, the share of general-election contests with precinct results per level, and the share of those years' precincts with shapes.

{table(["Year", "Contests", "President", "Senate", "House", "Governor", "Statewide", "Upper", "Lower", "Shaped"], precinct_years)}

## For other lanes

Precinct shapes (the precinct shapes lane): precincts with results and the share with shapes, with the states holding the most precincts without one.

{table(["Year", "Precincts", "Shaped", "Most unshaped"], shape_rows)}

Local offices and measures from 2000 (the local lane): contests held, with precinct results, and gap to precinct grain.

{table(["Level", "Held", "Precinct", "Gap"], [[k, f"{v[0]:,}", f"{v[1]:,}", f"{v[2]:,}"] for k, v in sorted(local.items())])}

ICPSR-only county results (the ICPSR replacement lane): contests whose only county breakdown comes from `us.icpsr`, `us.algara`, or `us.algara_federal`.

{table(["Level", "Era", "Contests"], [[lv, span, f"{v:,}"] for (lv, span), v in sorted(icpsr.items())])}
"""
