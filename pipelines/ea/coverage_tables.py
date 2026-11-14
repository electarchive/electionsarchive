import csv
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import NamedTuple

import polars as pl
import psycopg
from psycopg.types.json import Json

from ea import census_check, coverage, coverage_countries, raw, readiness, statewide_table
from ea.census_calendar import next_date
from ea.sources.us.clerk import LEGISLATORS
from ea.store import ROOT, connect

LOCK = 7_302_019
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
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
BALANCES = {
    "3011390": "3011397",
    "4752004": "4752006",
    "1303436": "1303440",
    "2148003": "2148006",
    "2028410": "2028412",
}
OFFSETS = {"us_house": 2, "state_lower": 2, "us_senate": 6}
KNOWN = {("GA", 2025, "statewide_exec", "general"): 2}
DECIDED_EARLIER = {
    "FL": (
        "2026-08-18",
        "Florida Statutes sections 100.061 and 105.051: a universal primary contest, or a nonpartisan race in which one candidate wins a majority, is decided at the primary and is left off the general election ballot",
    ),
}
CANCELED = {
    "06037": (
        "Los Angeles County Registrar-Recorder/County Clerk, tentative list of canceled elections for November 3, 2026 (appointment in lieu of election due to insufficiency of candidates), https://content.lavote.gov/docs/rrcc/documents/canceled-elections-november-2026-tentative.pdf",
        [
            "El Rancho",
            "El Segundo",
            "Manhattan Beach",
            "San Marino",
            "Walnut Valley",
            "West Covina",
            "El Monte City",
            "Gorman Joint",
            "Hawthorne",
            "Hermosa Beach City",
            "Rosemead",
        ],
    ),
}
ACRONYMS = {"MUD", "ISD", "CISD", "PUD", "USD", "CSD", "WCID", "GCD", "CDD", "SID", "MSD", "UD", "LID", "FWSD"}
ABBREVIATIONS = {
    "SCH": "SCHOOL",
    "SCHS": "SCHOOL",
    "SCHOOLS": "SCHOOL",
    "SCHL": "SCHOOL",
    "PUB": "PUBLIC",
    "HGHTS": "HEIGHTS",
    "HGTS": "HEIGHTS",
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
    "BORO": "BOROUGH",
    "SPGS": "SPRINGS",
    "TWNSHP": "TOWNSHIP",
    "HS": "HIGH",
    "COM": "COMMUNITY",
    "COMM": "COMMUNITY",
    "CMTY": "COMMUNITY",
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
COLLEGE = re.compile(
    r"^(?!.*\b(?:SCH|SCHOOLS?|ISD|ACADEMY)\b).*(?:COLLEGE|CMTY COL|JUNIOR COL|UNIVERSITY)", re.IGNORECASE
)

LOCAL_SQL = """
create temp table lc as
select coalesce(o.polity_id, b.polity_id, e.polity_id)::text
        || case when cc.level = 'school' and hp.kind = 'county' then '#school' else '' end as polity_id, cc.id, cc.year,
    extract(month from e.date_from)::int as month,
    cc.level <> 'local_measure' and cc.stage in ('general', 'primary', 'runoff') as regular, cc.stage,
    coalesce(cg.precinct, false) or cc.tacit as precinct, cc.upcoming, coalesce(rdy.ok, false) as ready
from cc
left join rdy on rdy.id = cc.id
join contest c on c.id = cc.id
join election e on e.id = c.election_id
left join office o on o.id = c.office_id
left join body b on b.id = c.body_id
left join polity hp on hp.id = coalesce(o.polity_id, b.polity_id, e.polity_id)
left join cg on cg.contest_id = cc.id
where cc.level in ('county', 'municipal', 'school', 'special_district', 'local_measure')
"""

HELD_SQL = """
select lc.polity_id, lc.year, count(*) filter (where not lc.upcoming),
    count(*) filter (where lc.precinct and not lc.upcoming), count(*) filter (where lc.regular),
    mode() within group (order by lc.month) filter (where lc.regular),
    count(*) filter (where lc.regular and lc.stage = 'general'),
    count(*) filter (where lc.upcoming), count(*) filter (where lc.upcoming and lc.ready)
from lc group by 1, 2
"""

LOCAL_SHAPES_SQL = """
select lc.polity_id, lc.year, count(distinct ug.k), count(distinct ug.k) filter (where ug.shaped)
from lc join rs on rs.contest_id = lc.id join rsu on rsu.result_set_k = rs.k join ug on ug.k = rsu.unit_k
where ug.grain = 'precinct'
group by 1, 2
"""

SHAPES_SQL = """
create temp table sg as
select cc.id, cc.st, cc.year, cc.level, e.date_from as d,
    case when cc.level in ('us_house', 'state_upper', 'state_lower') then c.district_area_id
        else coalesce(c.district_area_id, (select pt.area_id from polity_territory pt
            where pt.polity_id = e.polity_id and pt.valid @> e.date_from limit 1)) end as area_id
from cc join contest c on c.id = cc.id join election e on e.id = c.election_id
where cc.st <> 'US' and cc.level not in ('county', 'municipal', 'school', 'special_district', 'local_measure');

analyze sg;

create temp table su as
select sg.id, bool_and(u.area_relation = 'equals'
        and exists (select from area_geometry g where g.area_id = u.area_id and g.valid @> sg.d)) as shaped
from sg
join result_set r on r.contest_id = sg.id and r.is_default
join rsu on rsu.result_set_k = r.k
join reporting_unit u on u.k = rsu.unit_k
    and u.scheme_code in ('us.county', 'us.former_county', 'us.town', 'us.ak_house_district')
group by sg.id;

analyze su;
"""

SHAPES_COUNT_SQL = """
select sg.st, sg.year, sg.level, count(*),
    count(*) filter (where t.shape = 'complete'),
    count(*) filter (where t.shape = 'unshaped'),
    count(*) filter (where t.shape = 'units'),
    count(*) filter (where t.shape = 'undrawn')
from sg left join su on su.id = sg.id
cross join lateral (select case
    when sg.area_id is null or not exists (select from area_geometry g where g.area_id = sg.area_id) then 'undrawn'
    when not exists (select from area_geometry g where g.area_id = sg.area_id and g.valid @> sg.d) then 'unshaped'
    when not coalesce(su.shaped, true) then 'units'
    else 'complete' end as shape) t
group by 1, 2, 3
"""

TOWN_SCHOOL_STATES = frozenset(("09", "25", "33", "44", "50"))
TOWN_SCHOOL_WORDS = re.compile(
    r"\b(?:TWN|TOWN|CITY|SCHS|SCH|SCHOOLS|SCHOOL|DEPT|DEPARTMENT|PUBLIC|DISTRICT|DIST|SYSTEM)\b"
)
TOWN_SCHOOL_REGIONAL = {
    "BRISTOL-WARREN REGIONAL SCHOOL DISTRICT": "BRISTOL",
    "FOSTER-GLOCESTER REGIONAL SCHOOL DISTRICT": "FOSTER",
    "FOSTER ELEMENTARY SCHOOL DISTRICT": "FOSTER",
    "EXETER-WEST GREENWICH REGIONAL SCHOOL DISTRICT": "EXETER",
}


POLITIES_SQL = """
select p.id, p.kind::text, e.value, n.text, ps.st
from polity p
join name n on n.entity_id = p.id and n.is_preferred
left join external_id e on e.entity_id = p.id and e.scheme = 'us.polity'
left join pstate ps on ps.id = p.id
where p.id in (select id from uspol) and p.kind not in ('sovereign_state', 'federal_unit', 'region')
order by p.id, e.value, n.text
"""


TURNOUT_SQL = """
with m as (
    select r.election_id, bool_or(cm.measure = 2) as registered, bool_or(cm.measure in (3, 14)) as ballots,
        bool_or(cm.measure in (24, 25)) as survey
    from result_set r join coverage_cache_measure cm on cm.result_set_k = r.k
    where r.contest_id is null and r.status = 'active'
    group by 1
), days as (
    select substr(p.iso_code, 4) as st, e.date_from, e.kind::text as kind,
        bool_or(coalesce(m.registered, false)) as registered, bool_or(coalesce(m.ballots, false)) as ballots,
        bool_or(coalesce(m.survey, false)) as survey
    from election e
    join polity p on p.id = e.polity_id and p.kind in ('federal_unit', 'region') and p.iso_code = 'US-' || %(st)s
    left join m on m.election_id = e.id
    where e.status <> 'scheduled' and e.date_from < current_date
        and e.kind in ('general', 'primary', 'runoff', 'referendum')
        and (extract(year from e.date_from) >= 2000 or e.kind = 'general' and extract(year from e.date_from) >= 1948)
        and exists (select from contest c where c.election_id = e.id and not c.notional)
    group by 1, 2, 3
)
select st, extract(year from date_from)::int, kind,
    count(*) filter (where ballots and (registered or st = 'ND' and date_from >= '1952-01-01')),
    count(*) filter (where (ballots or registered) and not (ballots and (registered or st = 'ND' and date_from >= '1952-01-01'))),
    count(*) filter (where survey and not (ballots or registered)),
    count(*) filter (where not (ballots or registered or survey))
from days group by 1, 2, 3
"""


def cycle(weights: dict[int, int]) -> tuple[int, int] | None:
    total = sum(weights.values())
    for period in (4, 2):
        shares: Counter = Counter()
        for year, weight in weights.items():
            shares[year % period] += weight
        phase, n = shares.most_common(1)[0]
        if n >= 0.9 * total:
            return period, phase
    return None


def tokens(text: str) -> frozenset[str]:
    text = re.sub(r"\([^)]*\)", " ", text.upper().replace("'", "").replace("’", ""))
    text = re.sub(r"\b(?:[A-Z]\.){2,}", lambda m: m[0].replace(".", ""), text)
    match = PREFIX.match(text)
    words = re.sub(r"[^A-Z0-9 ]", " ", (match[2] if match else text).replace("&", " AND ").replace("#", " ")).split()
    out = []
    for word in words:
        out += [w for w in ABBREVIATIONS.get(word, word).split() if w not in STOP]
    return frozenset(re.sub(r"^0+(?=\d)", "", w) for w in out)


def request() -> None:
    if (ROOT / "data" / "logs" / "coverage.paused").exists():
        return
    with connect(autocommit=True) as conn:
        recent = conn.execute(
            "update coverage_build set requested_at = now() returning built_at > now() - interval '3 hours'"
        ).fetchone()
    if recent is not None and recent[0]:
        return
    subprocess.Popen(
        [sys.executable, "-c", "from ea.cli import main; main()", "coverage", "refresh"],
        cwd=ROOT / "pipelines",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


WORKERS = 8
CADENCE = "pipelines/ea/sources/us/election_cadence.tsv"
CADENCE_SQL = """
select s.iso_code, e.government_kind, e.function, e.name_pattern, e.polity_id::text, e.selection::text,
       e.period_years, e.phase, e.month, e.day_rule, extract(year from e.valid_from)::int, e.id::text
from election_cadence e join polity s on s.id = e.polity_id
where e.class_id is null or e.class_id in (
    select k.id from census_class k join polity p on p.id = k.polity_id where p.iso_code = 'US' and k.goal = 'local'
)
"""
CADENCE_STAMP_SQL = """
select (select count(*) || max(changeset_id::text) from election_cadence),
       (select count(*) || max(changeset_id::text) from results_access),
       (select count(*) || max(changeset_id::text) from election where status = 'scheduled')
"""
ACCESS_SQL = """
select r.polity_id::text, r.id::text, p.iso_code from results_access r join polity p on p.id = r.polity_id
where r.valid_to is null or r.valid_to > current_date
order by r.serves = 'administered', r.checked_at desc
"""
SCHEDULED_SQL = """
select p.iso_code, e.date_from, e.id::text from election e join polity p on p.id = e.polity_id
where e.status = 'scheduled' and e.kind = 'local' and e.date_from > current_date and p.iso_code like 'US-%'
order by e.date_from
"""


CLASSES = {
    "county": "county",
    "municipality": "municipal",
    "township": "township",
    "school": "school",
    "college": "school",
    "special": "special_district",
}
CLASSES_SQL = "select k.key, k.id from census_class k join polity p on p.id = k.polity_id where p.iso_code = 'US'"


class Rule(NamedTuple):
    state: str
    kind: str | None
    function: str | None
    pattern: re.Pattern[str] | None
    government: str | None
    selection: str
    period: int | None
    phase: int | None
    month: int | None
    day_rule: str | None
    since: int | None
    cited: bool
    id: str | None


def cadence_rules(conn: psycopg.Connection) -> tuple[dict[str, list[Rule]], dict[object, Rule]]:
    rules = [
        Rule(
            "" if kind is None else (iso or "").removeprefix("US-"),
            kind,
            function,
            None if pattern is None else re.compile(pattern, re.IGNORECASE),
            polity if kind is None else None,
            selection,
            period,
            phase,
            month,
            day_rule,
            since,
            True,
            rule_id,
        )
        for iso, kind, function, pattern, polity, selection, period, phase, month, day_rule, since, rule_id in (
            conn.execute(CADENCE_SQL)
        )
    ]
    with (ROOT / CADENCE).open(newline="") as f:
        for row in csv.DictReader((line for line in f if not line.startswith("#")), delimiter="\t"):
            cited = bool(row["locator"] and row["url"])
            if cited and not row["government"]:
                continue
            rules.append(
                Rule(
                    row["state"],
                    row["government_kind"] or None,
                    row["function"] or None,
                    re.compile(row["name_pattern"], re.IGNORECASE) if row["name_pattern"] else None,
                    row["government"] or None,
                    row["selection"],
                    int(row["period_years"]) if row["period_years"] else None,
                    int(row["phase"]) if row["phase"] else None,
                    int(row["month"]) if row["month"] else None,
                    row["day_rule"] or None,
                    int(row["valid_from"][:4]) if row["valid_from"] else None,
                    cited,
                    None,
                )
            )
    classes: dict[str, list[Rule]] = defaultdict(list)
    own: dict[object, Rule] = {}
    for r in rules:
        if r.kind is not None:
            classes[r.state].append(r)
        elif r.state:
            own.setdefault((r.state, r.government), r)
        else:
            own.setdefault(r.government, r)
    return classes, own


def cadence(
    rules: tuple[dict[str, list[Rule]], dict[object, Rule]], g: dict, census: str
) -> tuple[Rule | None, Rule | None, Rule | None, Rule | None]:
    classes, own = rules
    kinds = (g["kind"], "school") if g["kind"] == "college" else (g["kind"],)
    function = None if g["function"] is None else g["function"].lower()
    matching = [
        r
        for r in classes.get(g["state"], [])
        if r.kind in kinds and r.function in (None, function) and (r.pattern is None or r.pattern.search(census))
    ]

    def rank(r: Rule) -> tuple[bool, bool, bool]:
        return (r.pattern is not None, r.function is not None, r.kind == g["kind"])

    timed = [r for r in matching if r.period is not None]
    best = max(timed, key=rank, default=None)
    era = sorted(
        (r for r in timed if best is not None and r[1:4] == best[1:4]),
        key=lambda r: r.since or 0,
    )
    chosen = max((r for r in matching if r.kind == "special"), key=rank, default=None)
    return (
        era[0] if era else None,
        era[1] if len(era) > 1 else None,
        own.get(g["polity_id"]) or own.get((g["state"], g["name"])),
        chosen,
    )


@functools.cache
def government_links() -> list[tuple[str, str, str]]:
    with (ROOT / "pipelines/ea/coverage_inputs/government-links.tsv").open(newline="") as f:
        lines = (line for line in f if not line.startswith("#"))
        return [(row["government"], row["county"], row["polity"]) for row in csv.DictReader(lines, delimiter="\t")]


STATE_INPUTS = (
    "pipelines/ea/coverage.py",
    "pipelines/ea/coverage_tables.py",
    "pipelines/ea/statewide_table.py",
    "pipelines/ea/readiness.py",
    f"pipelines/ea/readiness/{readiness.DAY}.json",
    "pipelines/ea/sources/us/ncsl_measures.csv",
    "pipelines/ea/coverage_inputs/finest-reported.csv",
    "pipelines/ea/coverage_inputs/coverage-gaps.tsv",
    "pipelines/ea/coverage_inputs/government-links.tsv",
)
COUNTRY_INPUTS = (
    "pipelines/ea/coverage.py",
    "pipelines/ea/coverage_countries.py",
    "pipelines/ea/census_check.py",
    "pipelines/ea/readiness.py",
    f"pipelines/ea/readiness/{readiness.DAY}.json",
    "pipelines/ea/coverage_inputs/finest-reported.csv",
    "pipelines/ea/coverage_inputs/coverage-gaps.tsv",
)

PART_SQL = """
create table if not exists coverage_cache_part (stage text not null, part text not null, inputs text not null,
    built_at timestamptz not null, seconds real not null, rows json not null, primary key (stage, part))
"""

AREA_SQL = (
    """insert into coverage_cache_area select distinct area_id from area_geometry where recorded_from > %(since)s
       on conflict do nothing""",
    """delete from coverage_cache_area a
       where a.area_id in (select area_id from area_geometry_history where upper(recorded) > %(since)s)
           and not exists (select from area_geometry g where g.area_id = a.area_id)""",
)

STAMP_SQL = """
select least(now(), min(xact_start)) from pg_stat_activity
where datname = current_database() and backend_type = 'client backend' and pid <> pg_backend_pid()
"""

AREAS_SQL = """
with recursive areas as (
    select area_id as id, recorded_from as at from area_geometry where recorded_from > %(since)s
    union all select area_id, upper(recorded) from area_geometry_history where upper(recorded) > %(since)s
    union all select area_id, recorded_from from area_code where recorded_from > %(since)s
    union all select area_id, upper(recorded) from area_code_history where upper(recorded) > %(since)s
    union all select id, recorded_from from area where recorded_from > %(since)s
    union all select id, upper(recorded) from area_history where upper(recorded) > %(since)s
)"""

CHANGED_SQL = (
    AREAS_SQL
    + """,
k as (
    select id, election_id, office_id, body_id, recorded_from as at from contest where recorded_from > %(since)s
    union all select id, election_id, office_id, body_id, upper(recorded) from contest_history
    where upper(recorded) > %(since)s
    union all select c.id, c.election_id, c.office_id, c.body_id, t.at from (
        select contest_id as id, recorded_from as at from result_set where recorded_from > %(since)s
        union all select contest_id, upper(recorded) from result_set_history where upper(recorded) > %(since)s
        union all select contest_id, recorded_from from contest_option where recorded_from > %(since)s
        union all select contest_id, upper(recorded) from contest_option_history where upper(recorded) > %(since)s
        union all select o.contest_id, y.at from (
            select option_id, recorded_from as at from candidacy where recorded_from > %(since)s
            union all select option_id, upper(recorded) from candidacy_history where upper(recorded) > %(since)s
        ) y join contest_option o on o.id = y.option_id
        union all select r.contest_id, x.at from (
            select result_set_id, recorded_from as at from outcome where recorded_from > %(since)s
            union all select result_set_id, upper(recorded) from outcome_history where upper(recorded) > %(since)s
        ) x join result_set r on r.id = x.result_set_id
        union all select contest_id, recorded_from from tenure where recorded_from > %(since)s
        union all select contest_id, upper(recorded) from tenure_history where upper(recorded) > %(since)s
        union all select contest_id, recorded_from from ballot_question_text where recorded_from > %(since)s
        union all select contest_id, upper(recorded) from ballot_question_text_history where upper(recorded) > %(since)s
        union all select entity_id, recorded_from from label where recorded_from > %(since)s
        union all select entity_id, upper(recorded) from label_history where upper(recorded) > %(since)s
        union all select entity_id, recorded_from from external_id
        where recorded_from > %(since)s and scheme = 'us.contest'
        union all select entity_id, upper(recorded) from external_id_history
        where upper(recorded) > %(since)s and scheme = 'us.contest'
        union all select r.contest_id, s.at from (
            select id, recorded_from as at from source where recorded_from > %(since)s
            union all select id, upper(recorded) from source_history where upper(recorded) > %(since)s
        ) s join result_set r on r.source_id = s.id
        union all select c.id, a.at from areas a join contest c on c.district_area_id = a.id
    ) t join contest c on c.id = t.id
),
named as (
    select entity_id, recorded_from as at from name where recorded_from > %(since)s
    union all select entity_id, upper(recorded) from name_history where upper(recorded) > %(since)s
    union all select entity_id, recorded_from from external_id where recorded_from > %(since)s
    union all select entity_id, upper(recorded) from external_id_history where upper(recorded) > %(since)s
),
touched as (
    select polity_id, max(at) as at, bool_or(wide) as wide from (
        select e.polity_id, k.at, false as wide from k join election e on e.id = k.election_id
        union all select o.polity_id, k.at, false from k join office o on o.id = k.office_id
        union all select b.polity_id, k.at, false from k join body b on b.id = k.body_id
        union all select e.polity_id, r.at, false from (
            select election_id, recorded_from as at from result_set where recorded_from > %(since)s and contest_id is null
            union all select election_id, upper(recorded) from result_set_history
            where upper(recorded) > %(since)s and contest_id is null
        ) r join election e on e.id = r.election_id
        union all select polity_id, recorded_from, false from election where recorded_from > %(since)s
        union all select polity_id, upper(recorded), false from election_history where upper(recorded) > %(since)s
        union all select polity_id, recorded_from, false from reporting_unit where recorded_from > %(since)s
        union all select polity_id, upper(recorded), false from reporting_unit_history where upper(recorded) > %(since)s
        union all select u.polity_id, a.at, false from areas a join reporting_unit u on u.area_id = a.id
        union all select t.polity_id, a.at, false from areas a join polity_territory t on t.area_id = a.id
        union all select id, recorded_from, false from polity where recorded_from > %(since)s
        union all select id, upper(recorded), false from polity_history where upper(recorded) > %(since)s
        union all select unnest(array[polity_id, parent_id]), recorded_from, false from polity_parent
        where recorded_from > %(since)s
        union all select unnest(array[polity_id, parent_id]), upper(recorded), false from polity_parent_history
        where upper(recorded) > %(since)s
        union all select polity_id, recorded_from, false from polity_territory where recorded_from > %(since)s
        union all select polity_id, upper(recorded), false from polity_territory_history where upper(recorded) > %(since)s
        union all select p.id, n.at, false from named n join polity p on p.id = n.entity_id
        union all select o.polity_id, n.at, true from named n join office o on o.id = n.entity_id
        union all select b.polity_id, n.at, true from named n join body b on b.id = n.entity_id
        union all select polity_id, recorded_from, true from office where recorded_from > %(since)s
        union all select polity_id, upper(recorded), true from office_history where upper(recorded) > %(since)s
        union all select polity_id, recorded_from, true from body where recorded_from > %(since)s
        union all select polity_id, upper(recorded), true from body_history where upper(recorded) > %(since)s
        union all select b.polity_id, s.at, true from (
            select body_id, recorded_from as at from seat where recorded_from > %(since)s
            union all select body_id, upper(recorded) from seat_history where upper(recorded) > %(since)s
        ) s join body b on b.id = s.body_id
    ) x
    where polity_id is not null
    group by 1
),
up(id, anc) as (
    select polity_id, polity_id from touched
    union select up.id, pp.parent_id from up join polity_parent pp on pp.polity_id = up.anc
)
select t.at, t.wide, array_agg(p.iso_code) filter (where p.iso_code is not null),
    min(substr(p.iso_code, 4)) filter (where p.kind in ('federal_unit', 'region'))
from touched t join up on up.id = t.polity_id join polity p on p.id = up.anc
group by t.polity_id, t.at, t.wide
"""
)

DISTRICTS_CHANGED_SQL = (
    AREAS_SQL
    + """
select substr(ac.code, 1, 2), max(c.at)
from areas c join area a on a.id = c.id and a.scheme_code = 'us.cd' join area_code ac on ac.area_id = a.id
group by 1
"""
)

OFFICE_YEARS_SQL = "select state, office_id::text, class, year, joint_ticket, basis from statewide_office_year"


def digest(paths: tuple[str, ...]) -> str:
    sha = hashlib.sha256()
    for path in paths:
        sha.update(path.encode())
        sha.update((ROOT / path).read_bytes())
    return sha.hexdigest()[:16]


def refresh(full: bool = False) -> str:
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
                stamp = coverage_countries.inputs(conn)
                commit = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
                ).stdout.strip()
                try:
                    built.append(attempt(full))
                except BaseException:
                    conn.execute("update coverage_build set started_at = built_at")
                    raise
                full = False
                conn.execute(
                    "update coverage_build set built_at = %s, commit = %s, inputs = %s",
                    [started[0], commit, json.dumps(stamp)],
                )
                coverage_countries.record(conn)
            conn.execute("select pg_advisory_unlock(%s)", [LOCK])
            pending = conn.execute("select built_at is null or requested_at > built_at from coverage_build").fetchone()
            if pending is None or not pending[0]:
                return "; ".join(built)


def attempt(full: bool) -> str:
    if "lock_timeout" not in os.environ.get("PGOPTIONS", ""):
        os.environ["PGOPTIONS"] = f"{os.environ.get('PGOPTIONS', '')} -c lock_timeout=60s".strip()
    for wait in (60, 120, 300, 600, 900, 0):
        try:
            return build(full)
        except psycopg.errors.LockNotAvailable as error:
            if not wait:
                raise
            print(f"coverage: {error.diag.message_primary}; retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError


def measure(st: str, members: list[dict], today: date) -> tuple[str, dict, float]:
    started = time.monotonic()
    with connect() as conn:
        cells = coverage.matrix(conn, [st], members, last=today.year)
        conn.execute("create temp table gap (st text, level text, stage text, y0 int, y1 int, ids uuid[])")
        conn.cursor().executemany(
            "insert into gap values (%s, %s, %s, %s, %s, %s)",
            [
                (
                    row["state"],
                    row["level"],
                    row["stage"],
                    row["from"],
                    row["to"],
                    [i for i in row["ids"] if UUID.fullmatch(i)],
                )
                for row in coverage.registry()
                if row["polity"] == "us"
                and row["grain"] in ("results", "county", "precinct")
                and (not row["ids"] or any(UUID.fullmatch(i) for i in row["ids"]))
            ],
        )
        gaps = [list(row) for row in conn.execute(coverage.GAPS_SQL)]
        units = [row for row in conn.execute(coverage.UNITS_SQL) if row[0] is not None]
        conn.execute(LOCAL_SQL)
        held: dict[str, dict[int, list]] = defaultdict(dict)
        for polity, year, n, precinct, regular, month, generals, up, ready in conn.execute(HELD_SQL):
            held[str(polity)][year] = [n, precinct, regular, month, 0, 0, generals, up, ready]
        for polity, year, n, shaped in conn.execute(LOCAL_SHAPES_SQL):
            held[str(polity)][year][4:6] = [n, shaped]
        for statement in SHAPES_SQL.split(";\n\n"):
            conn.execute(statement)
        shapes = list(conn.execute(SHAPES_COUNT_SQL))
        turnout = list(conn.execute(TURNOUT_SQL, {"st": st}))
    return (
        st,
        {"cells": cells, "units": units, "held": held, "shapes": shapes, "turnout": turnout, "gaps": gaps},
        time.monotonic() - started,
    )


def country(cc: str) -> tuple[str, dict, float]:
    started = time.monotonic()
    with connect() as conn:
        goal_rows, polity_row = coverage_countries.part(conn, cc)
    return cc, {"goals": goal_rows, "polity": polity_row}, time.monotonic() - started


def build(full: bool = False) -> str:
    started = time.monotonic()
    today = date.today()
    members = [
        person
        for kind in ("historical", "current")
        for person in json.loads(raw.cached("us.clerk", LEGISLATORS.format(kind=kind)).path.read_text())
    ]
    states_code = digest(STATE_INPUTS) + ":" + hashlib.sha256(json.dumps(members).encode()).hexdigest()[:16]
    countries_code = digest(COUNTRY_INPUTS)
    cog_files = sorted(raw.parsed("us.cog").glob("*.parquet"))
    local_code = (
        digest(("pipelines/ea/coverage_tables.py", CADENCE))
        + ":"
        + ":".join(f"{f.name}{f.stat().st_mtime_ns}" for f in cog_files)
    )
    timings: dict[str, float] = {}
    with connect(autocommit=True) as conn:
        conn.execute(PART_SQL)
        stamp: datetime = next(iter(conn.execute(STAMP_SQL)))[0]
        parts = {
            (stage, part): (inputs, built_at, seconds)
            for stage, part, inputs, built_at, seconds in conn.execute(
                "select stage, part, inputs, built_at, seconds from coverage_cache_part"
            )
        }
        clock = time.monotonic()
        conn.execute(coverage.CACHE_SQL)
        conn.execute("create table if not exists coverage_cache_area (area_id uuid primary key)")
        for statement in AREA_SQL:
            conn.execute(statement, {"since": parts.get(("cache", "area"), ("", "-infinity"))[1]})
        conn.execute("analyze coverage_cache_area")
        conn.execute(
            """insert into coverage_cache_part values ('cache', 'area', '', %s, 0, '{}')
               on conflict (stage, part) do update set built_at = excluded.built_at""",
            [stamp],
        )
        timings["cache"] = time.monotonic() - clock
        clock = time.monotonic()
        before = set(conn.execute(OFFICE_YEARS_SQL))
        with conn.transaction():
            statewide_table.rebuild(conn)
        offices = {row[0] for row in before ^ set(conn.execute(OFFICE_YEARS_SQL))}
        timings["statewide"] = time.monotonic() - clock
        for statement in coverage.CONTESTS_SQL.split(";\n\n")[:2]:
            conn.execute(statement)
        states = {fips: (st, name) for st, fips, name in conn.execute(coverage.STATES_SQL)}
        scope = sorted({st for (st,) in conn.execute("select distinct st from pstate where st is not null")} | {"US"})
        wanted = {("state", st): f"{states_code}:{today}:{readiness.local_today('US-' + st)}" for st in scope}
        clock = time.monotonic()
        with conn.transaction():
            census_check.rebuild(conn)
        timings["classes"] = time.monotonic() - clock
        census = coverage_countries.stamps(conn)
        listed = coverage_countries.countries(conn)
        for cc in ["us", *listed]:
            zones = sorted(
                {str(readiness.local_today(iso)) for iso in readiness.ZONES if cc == "eu" or iso[:2] == cc.upper()}
                | {str(readiness.local_today(cc.upper()))}
            )
            path = f"pipelines/ea/census/{cc}.json"
            extra = "" if cc == "us" else census[cc] + (":" + digest((path,)) if (ROOT / path).exists() else "")
            wanted[("country", cc)] = f"{countries_code}:{extra}:{today}:{','.join(zones)}"
        wanted[("local", "")] = f"{local_code}:{next(iter(conn.execute(CADENCE_STAMP_SQL)))}:{today}"
        clock = time.monotonic()
        moved: dict[tuple[str, str], datetime] = {}
        since = min((built for (stage, _), (_, built, _) in parts.items() if stage != "cache"), default=None)
        if since is not None and not full:
            isos = {cc.upper(): cc for cc in listed}
            for at, wide, ancestors, st in conn.execute(CHANGED_SQL, {"since": since}):
                keys = [("country", isos[iso]) for iso in set(ancestors or []) if iso in isos]
                if "US" in (ancestors or []):
                    keys += [("state", "*" if wide and st is None else st or "US"), ("country", "us"), ("local", "")]
                for key in keys:
                    moved[key] = max(moved.get(key, at), at)
            for code, at in conn.execute(DISTRICTS_CHANGED_SQL, {"since": since}):
                if code in states:
                    key = ("state", states[code][0])
                    moved[key] = max(moved.get(key, at), at)
        for st in offices:
            moved[("state", st)] = stamp
        timings["changes"] = time.monotonic() - clock

        def stale(key: tuple[str, str]) -> bool:
            if full or key not in parts or parts[key][0] != wanted[key]:
                return True
            built = parts[key][1]
            return any(at >= built for at in (moved.get(key), moved.get((key[0], "*"))) if at is not None)

        todo = [st for st in scope if stale(("state", st))]
        countries = [cc for stage, cc in wanted if stage == "country" and stale(("country", cc))]
        clock = time.monotonic()
        with ThreadPoolExecutor(WORKERS) as pool:
            jobs = [
                pool.submit(measure, part, members, today) if stage == "state" else pool.submit(country, part)
                for stage, part in sorted(
                    [("state", st) for st in todo] + [("country", cc) for cc in countries],
                    key=lambda key: -parts.get(key, ("", None, 0.0))[2],
                )
            ]
            for job in as_completed(jobs):
                part, rows, seconds = job.result()
                conn.execute(
                    """insert into coverage_cache_part values (%s, %s, %s, %s, %s, %s)
                       on conflict (stage, part) do update set inputs = excluded.inputs, built_at = excluded.built_at,
                           seconds = excluded.seconds, rows = excluded.rows""",
                    [
                        "state" if "cells" in rows else "country",
                        part,
                        wanted[("state" if "cells" in rows else "country", part)],
                        stamp,
                        seconds,
                        Json(rows),
                    ],
                )
        timings["parts"] = time.monotonic() - clock
        conn.execute("delete from coverage_cache_part where stage = 'state' and part <> all(%s)", [scope])
        conn.execute("delete from coverage_cache_part where stage = 'country' and part <> all(%s)", [["us", *listed]])
        written = parts.get(("local", ""), ("", None, 0.0))[1]
        newer = {
            stage
            for (stage, _), (_, built, _) in parts.items()
            if stage in ("state", "country") and (written is None or built > written)
        }
        if not todo and not countries and not newer and not stale(("local", "")):
            return "nothing has changed since the last build"
        clock = time.monotonic()
        country_goals: list[tuple] = []
        country_polities: list[tuple] = []
        for (rows,) in conn.execute("select rows from coverage_cache_part where stage = 'country' order by part"):
            country_goals += [tuple(row) for row in rows["goals"]]
            country_polities.append(tuple(rows["polity"]))
        if not todo and "state" not in newer and not stale(("local", "")):
            (known,) = conn.execute(
                "select coalesce(rows -> 'known', '[]') from coverage_cache_part where stage = 'local'"
            ).fetchone() or ([],)
            with conn.transaction():
                coverage_countries.write(
                    conn,
                    country_goals,
                    [
                        (*row[:9], json.dumps(json.loads(row[9]) + known, ensure_ascii=False), row[10])
                        if row[0] == "us"
                        else row
                        for row in country_polities
                    ],
                )
                conn.execute("update coverage_cache_part set built_at = %s where stage = 'local'", [stamp])
            return f"{len(countries)} countries rebuilt in {time.monotonic() - started:.0f}s"
        cells: list[dict] = []
        units: list[tuple] = []
        held: dict[str, dict[int, list]] = defaultdict(dict)
        shapes: list[tuple] = []
        turnout: list[tuple] = []
        gaps: list[list] = []
        for (rows,) in conn.execute("select rows from coverage_cache_part where stage = 'state' order by part"):
            cells += rows["cells"]
            gaps += rows["gaps"]
            units += rows["units"]
            shapes += rows["shapes"]
            turnout += rows["turnout"]
            for polity, years in rows["held"].items():
                for year, values in years.items():
                    held[polity][int(year)] = values
        polities = {str(pid): (kind, code, name, st) for pid, kind, code, name, st in conn.execute(POLITIES_SQL)}
        rules = cadence_rules(conn)
        judges = {st: rule for (key, st), rule in coverage.cycles(conn).items() if key == "judicial"}
        census_classes = dict(conn.execute(CLASSES_SQL).fetchall())
        access: dict[str, str] = {}
        for pid, aid, iso in conn.execute(ACCESS_SQL):
            access.setdefault(pid, aid)
            if iso and iso.startswith("US-"):
                access.setdefault(iso[3:], aid)
        scheduled: dict[str, list[tuple[date, str]]] = defaultdict(list)
        for iso, day, eid in conn.execute(SCHEDULED_SQL):
            scheduled[iso[3:]].append((day, eid))
        timings["load"] = time.monotonic() - clock
        clock = time.monotonic()

    by_key = {(c["state"], c["year"], c["level"], c["stage"]): c for c in cells}
    for stage in ("primary", "general"):
        for (st, year, level, cell_stage), cell in list(by_key.items()):
            if (
                cell_stage != stage
                or level not in STATE_LEVELS
                or level == "statewide_measure"
                or year != today.year - OFFSETS.get(level, 4)
            ):
                continue
            key = (st, today.year, level, stage)
            if key in by_key:
                continue
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
                    "upcoming": 0,
                    "pending": 0,
                    "target": coverage.target(st, level, today.year),
                },
            )
            current["expected"] = cell["held"]

    known = dict(KNOWN)
    for st, rule in judges.items():
        for year in (year for year in range(2000, today.year + 1) if coverage.chosen(rule, year) == "elected"):
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
    for gid, kind, name, st, county, place in cog.select(
        "id", "kind", "name", "state_fips", "county_fips", "place_fips"
    ).iter_rows():
        candidates = [f"county:{st}{county}"] if kind == "county" else []
        if kind in ("municipality", "township") and place is not None:
            candidates = [f"place:{BALANCES.get(st + place, st + place)}", f"town:{st}{county}{place}"]
            if county is not None and (county >= "500" or name.startswith("CITY AND COUNTY OF")):
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
        county = rest[2:5] if prefix == "special" and rest[2:3] != ":" else None
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
                found = [g for _, g in ranked if mine < g[2] and "COLLEGE" not in g[2] - mine]
        if len(found) == 1 and found[0][0] not in link:
            link[found[0][0]] = pid
            taken.add(pid)
    named = {
        (code[8:13], polities[pid][2].casefold()): pid
        for code, pid in codes.items()
        if code.startswith("special:") and code[10:11] != ":"
    }
    for gid, county, name in government_links():
        pid = named.get((county, name.casefold()))
        if gid not in link and pid is not None and pid not in taken:
            link[gid] = pid
            taken.add(pid)
    plain = frozenset(
        (
            "SCHOOL",
            "DISTRICT",
            "CITY",
            "PUBLIC",
            "COUNTY",
            "INDEPENDENT",
            "CONSOLIDATED",
            "COMMUNITY",
            "TOWNSHIP",
            "AREA",
            "ELEMENTARY",
            "JOINT",
            "SPECIAL",
        )
    )
    for code, pid in codes.items():
        if pid in taken or not code.startswith("school:"):
            continue
        mine = {w for w in tokens(polities[pid][2]) if not w.isdigit()} - plain
        found = [
            g[0]
            for g in pools.get((code[7:9], "school"), [])
            if g[0] not in link and mine and {w for w in g[2] if not w.isdigit()} - plain == mine
        ]
        if len(found) == 1:
            link[found[0]] = pid
            taken.add(pid)
    for code, pid in codes.items():
        if pid in taken or not code.startswith("school:"):
            continue
        mine = {w for w in tokens(polities[pid][2]) if not w.isdigit()} - plain
        ranked = []
        for g in pools.get((code[7:9], "school"), []):
            theirs = {w for w in g[2] if not w.isdigit()} - plain
            if g[0] in link or not mine or not theirs or theirs & {"COLLEGE", "COL", "UNIVERSITY"}:
                continue
            small, big = sorted((mine, theirs), key=len)
            score = (
                0.9
                if small < big and len(big - small) <= 2 and sum(map(len, small)) >= 5
                else SequenceMatcher(None, "".join(sorted(mine)), "".join(sorted(theirs))).ratio()
            )
            ranked.append((score, g[0]))
        ranked.sort(reverse=True)
        if ranked and ranked[0][0] >= 0.85 and (len(ranked) == 1 or ranked[1][0] < ranked[0][0] - 0.05):
            link[ranked[0][1]] = pid
            taken.add(pid)
    town_names: dict[tuple[str, str], list[str]] = defaultdict(list)
    for code, pid in codes.items():
        prefix, _, rest = code.partition(":")
        if prefix in ("town", "place") and rest[:2] in TOWN_SCHOOL_STATES and pid in polities:
            town_names[(rest[:2], " ".join(sorted(tokens(polities[pid][2]))))].append(pid)
    for gid, kind, name, st in cog.select("id", "kind", "name", "state_fips").iter_rows():
        if gid in link or kind not in ("school", "dependent_school") or st not in TOWN_SCHOOL_STATES:
            continue
        stripped = tokens(TOWN_SCHOOL_WORDS.sub(" ", TOWN_SCHOOL_REGIONAL.get(name.upper(), name.upper())))
        found = town_names.get((st, " ".join(sorted(stripped))), [])
        if len(found) == 1:
            link[gid] = found[0]
    link["23508201400000"] = "cf3230d1-cf93-5584-bb33-3458b297c168"
    link["05504300800000"] = "5f1810f8-9163-5a5e-aed2-ccd9fc4b1532"

    school_count = Counter(
        (st, county)
        for kind, name, st, county in cog.select("kind", "name", "state_fips", "county_fips").iter_rows()
        if kind in ("school", "dependent_school") and not COLLEGE.search(name)
    )
    governments: list[dict] = []
    for gid, kind, name, st, county, function, population, population_basis, active, elected in cog.select(
        "id",
        "kind",
        "name",
        "state_fips",
        "county_fips",
        "function",
        "population",
        "population_basis",
        "active",
        "elected",
    ).iter_rows():
        pid = link.get(gid)
        # Census of Governments leaves the Jersey City School System (31000000030100) without a county; Jersey City is in Hudson County (FIPS 34017).
        if gid == "31000000030100":
            county = "017"
        held_key = f"{codes.get(f'county:{st}{county}')}#school"
        if (
            kind not in ("school", "dependent_school")
            or COLLEGE.search(name)
            or held.get(str(pid))
            or held_key not in held
            or school_count[(st, county)] != 1
        ):
            held_key = None
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
                "kind": "college"
                if kind in ("school", "dependent_school") and COLLEGE.search(name)
                else {"dependent_school": "school"}.get(kind, kind),
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
                "census": name,
                "population": population,
                "population_basis": population_basis,
                "active": active,
                # NJ county vocational boards are appointed by the county executive or commissioners (N.J.S.A. 18A:54-16, https://pub.njleg.state.nj.us/statutes), not elected.
                "elected": elected
                and not (st == "34" and kind in ("school", "dependent_school") and re.search(r"VOCATIONAL", name)),
                "polity_id": pid,
                "held_key": held_key,
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
                else "college"
                if kind == "school_district" and COLLEGE.search(name)
                else {"county": "county", "municipality": "municipality", "school_district": "school"}.get(
                    kind, "special" if kind == "special_district" else "other"
                ),
                "name": name,
                "function": None,
                "census": "",
                "population": None,
                "population_basis": None,
                "active": True,
                "elected": True,
                "polity_id": pid,
            }
        )

    patterns: dict[tuple[str, str], Counter] = defaultdict(Counter)
    months: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for g in governments:
        years = held.get(g["polity_id"] or "", {})
        regular = sorted(y for y, v in years.items() if v[2] and 2000 <= y <= today.year)
        g["month"] = Counter(years[y][3] for y in regular).most_common(1)[0][0] if regular else None
        g["period"] = g["phase"] = g["era"] = None
        if len(regular) >= 2:
            weights = {y: years[y][2] for y in regular}
            g["period"], g["phase"] = cycle(weights) or (1, 0)
            g["basis"] = "own"
            if g["period"] == 1:
                splits = [
                    (
                        sum(weights[y] for y in regular[:i] if (y - early[1]) % early[0] == 0)
                        + sum(weights[y] for y in regular[i:] if (y - late[1]) % late[0] == 0),
                        i,
                        early,
                        late,
                    )
                    for i in range(2, len(regular) - 1)
                    if (early := cycle({y: weights[y] for y in regular[:i]})) is not None
                    and (late := cycle({y: weights[y] for y in regular[i:]})) is not None
                    and early != late
                ]
                if splits:
                    _, i, early, late = max(splits)
                    g["period"], g["phase"] = late
                    g["era"] = (regular[i - 1], regular[i], early)
                    g["basis"] = "own, two eras"
            patterns[(g["state"], g["kind"])][(g["period"], g["phase"])] += 1
        elif regular:
            g["phase"] = regular[0]
            g["basis"] = "own year, state pattern"
        else:
            g["basis"] = "state pattern"
        if g["month"] is not None:
            months[(g["state"], g["kind"])][g["month"]] += 1

    years_out: list[tuple] = []
    ahead: dict[tuple[str, int], bool] = {}
    for g in governments:
        group = (g["state"], g["kind"])
        calendar, switch, own, chosen = cadence(rules, g, g["census"])
        if (
            g["kind"] == "special"
            and g["census"]
            and chosen is not None
            and (chosen.selection in ("appointed", "landowner") or chosen.day_rule == "off_november")
        ):
            g["elected"] = False
        if g["period"] is None:
            period, phase = (None, None) if calendar is None else (calendar.period, calendar.phase)
            common = [p for p, _ in patterns[group].most_common() if period is None or p[0] == period]
            if g["phase"] is not None:
                if period is None:
                    counted = Counter(p for p, _ in patterns[group].elements()).most_common(1)
                    period = counted[0][0] if counted else 2
                g["period"], g["phase"] = period, g["phase"] % period
            elif calendar is not None and period is not None:
                g["period"], g["phase"] = period, phase
                g["basis"] = "state calendar" if calendar.cited else "estimated state calendar"
            elif common:
                g["period"], g["phase"] = common[0]
            else:
                g["period"], g["phase"], g["basis"] = 2, 0, "unknown"
        if (
            calendar is not None
            and calendar.period == 2
            and calendar.cited
            and g["period"] == 1
            and g["era"] is None
            and g["basis"].startswith("own")
        ):
            g["period"], g["phase"] = calendar.period, calendar.phase
            g["basis"] = "state calendar, own history conflicted"
        primary = calendar is not None and calendar.day_rule == "with_state_primary"
        if g["month"] is None and calendar is not None and calendar.month is not None and not primary:
            g["month"] = calendar.month
        elif g["month"] is None and months[group]:
            g["month"] = months[group].most_common(1)[0][0]
        if (
            switch is not None
            and switch.since is not None
            and switch.period is not None
            and (g["period"], g["phase"]) != (switch.period, switch.phase)
        ):
            last = switch.since - (switch.period if g["period"] == switch.period else 1)
            g["era"] = (last, switch.since, (g["period"], g["phase"]))
            g["period"], g["phase"] = switch.period, switch.phase
            g["basis"] = "state calendar, changed"
        if own is not None and own.period is not None:
            g["period"], g["phase"], g["month"] = own.period, own.phase, own.month
            g["era"] = None
            g["basis"] = "district calendar"
        if primary and calendar is not None and calendar.kind == g["kind"]:
            g["month"] = calendar.month
        rule = (
            own
            if own is not None and own.period is not None
            else switch
            if switch is not None and switch.since is not None and switch.since <= today.year
            else calendar or chosen
        )
        g["cadence_id"] = None if rule is None else rule.id
        g["access_id"] = (
            access.get(g["polity_id"] or "")
            or access.get(codes.get(f"county:{g['county']}") or "")
            or access.get(g["state"])
        )
        g["next_date"] = g["next_election_id"] = None
        if g["active"] and g["elected"] and g["period"] is not None and g["phase"] is not None:
            found = next(
                (
                    (day, eid)
                    for day, eid in scheduled.get(g["state"], [])
                    if day.month == g["month"] and (day.year - g["phase"]) % g["period"] == 0
                ),
                None,
            )
            if found is not None:
                g["next_date"], g["next_election_id"] = found
            elif rule is not None:
                g["next_date"] = next_date(rule.day_rule, g["period"], g["phase"], today)
        years = held.get(g.get("held_key") or g["polity_id"] or "", {})
        for year in sorted(set(years) | set(range(2000, today.year + 1))):
            era = g["era"]
            due = (g["period"], g["phase"]) if era is None or year >= era[1] else era[2] if year <= era[0] else None
            expected = (
                g["active"]
                and g["elected"]
                and not g["id"].startswith("polity:")
                and 2000 <= year <= today.year
                and due is not None
                and due[1] is not None
                and (year - due[1]) % due[0] == 0
                and (year < today.year or (g["month"] is not None and g["month"] < today.month))
            )
            n, precinct, _, _, precincts, shaped, generals, up, ready = years.get(year, [0, 0, 0, None, 0, 0, 0, 0, 0])
            earlier = years.get(year - 4, [0] * 9)[6] if year < today.year else 0
            if n == 0 and up and 2000 <= year <= today.year:
                ahead[(g["id"], year)] = ready > 0
            if expected or n:
                years_out.append(
                    (
                        g["id"],
                        year,
                        expected,
                        n,
                        expected and ((n == 0 and not up) or generals < earlier),
                        precinct,
                        precincts,
                        shaped,
                    )
                )
    names = dict(states.values())
    registry = coverage.registry()
    hits: dict[int, set[tuple[str, str]]] = defaultdict(set)
    reached: Counter = Counter()
    tags: dict[tuple, Counter] = defaultdict(Counter)
    for st, year, level, stage, contest, election, county_bucket, precinct_bucket in gaps:
        for goal, bucket, grains in (
            ("county", county_bucket, ("results", "county")),
            ("precinct", precinct_bucket, ("results", "county", "precinct") if county_bucket else ("precinct",)),
        ):
            found = bucket and coverage.gap(registry, grains, "us", st, level, stage, year, contest)
            if found:
                tags[(st, year, level, stage)][(goal, found["category"], bucket)] += 1
                hits[found["i"]].add((contest, election))

    def move(cell: list[int], index: int, n: int, found: dict | None, absent: bool = False) -> None:
        if found is None or n <= 0:
            return
        reached[found["i"]] += n if found["i"] >= 0 else 0
        if absent and found["category"] == "known_gap":
            return
        cell[index] -= n
        cell[22 + coverage.CATEGORIES.index(found["category"])] += n
        cell[0] -= n if found["category"] == "not_applicable" else 0

    goals: dict[tuple[str, str, str, int], list[int]] = defaultdict(lambda: [0] * 25)
    for st, year, level, total, complete, unshaped, unit_shapes, undrawn in shapes:
        if level not in STATE_LEVELS or st not in names:
            continue
        cell = goals[("shapes" if year >= 2000 else "shapes_history", st, level, year)]
        cell[0] += total
        cell[1] += complete
        cell[10] += unshaped
        cell[11] += unit_shapes
        cell[12] += undrawn
        move(cell, 12, undrawn, coverage.gap(registry, ("shapes",), "us", st, level, "", year))
    for (st, year, level, stage), c in by_key.items():
        if level not in STATE_LEVELS or st not in names:
            continue
        count = c["held"]
        base = max(0 if c["expected"] == "" else c["expected"], count)
        county = c["county_or_finer"]
        partial = c.get("county_partial", 0)
        bare = c.get("no_results", 0)
        upcoming = c.get("upcoming", 0)
        pending = c.get("pending", 0)
        totals = max(0, count - county - partial - bare - upcoming - pending)
        precinct = c["precinct"]
        major = c.get("major_only", 0)
        published = c.get("published", 0)
        whole = coverage.target(st, level, year) == "total"
        if whole:
            county = precinct = count - bare - upcoming - pending
            partial = totals = major = published = 0
        tagged = tags.get((st, year, level, stage), Counter())
        if year >= 2000:
            for goal, complete, coarse in (
                ("county", county, 0),
                ("precinct", precinct, max(0, county - precinct - major)),
            ):
                cell = goals[(goal, st, level, year)]
                for i, value in enumerate(
                    [base, complete, base - count, bare, totals, partial, coarse, c.get("with_turnout", 0)]
                ):
                    cell[i] += value
                cell[20] += upcoming
                cell[21] += pending
                if goal == "precinct":
                    cell[8] += c["precincts"]
                    cell[9] += c["shaped_precincts"]
                    cell[18] += major
                    cell[19] += published
                else:
                    cell[13] += c.get("icpsr_only", 0)
                grains = ("results", "county") if goal == "county" else ("results", "county", "precinct")
                for (tag, category, bucket), n in tagged.items():
                    if tag == goal and (bucket == "bare" or not whole):
                        index = {"bare": 3, "totals": 4, "partial": 5, "coarse": 6, "major": 18}[bucket]
                        move(cell, index, n, {"category": category, "i": -1})
                move(cell, 2, base - count, coverage.gap(registry, grains, "us", st, level, stage, year), True)
        elif coverage.target(st, level, year) == "county" or level in ("state_upper", "state_lower"):
            cell = goals[("history", st, level, year)]
            for i, value in enumerate([base, county, base - count, bare, totals, partial, 0, c.get("with_turnout", 0)]):
                cell[i] += value
            cell[13] += c.get("icpsr_only", 0)
            for (tag, category, bucket), n in tagged.items():
                if tag == "county" and (bucket == "bare" or not whole):
                    move(cell, {"bare": 3, "totals": 4, "partial": 5}[bucket], n, {"category": category, "i": -1})
            move(
                cell, 2, base - count, coverage.gap(registry, ("results", "county"), "us", st, level, stage, year), True
            )
    for st, year, n, shaped in units:
        if st in names:
            cell = goals[("precinct_shapes", st, "precinct", year)]
            cell[0] += n
            cell[1] += shaped
            cell[10] += n - shaped
    for st, year, kind, complete, partial, coarse, absent in turnout:
        if st not in names:
            continue
        cell = goals[("turnout", st, kind, year)]
        cell[0] += complete + partial + coarse + absent
        cell[1] += complete
        cell[2] += absent
        cell[5] += partial
        cell[6] += coarse
        move(cell, 2, absent, coverage.gap(registry, ("turnout",), "us", st, "", kind, year))
    kinds = {g["id"]: (g["state"], g["kind"], g["population"] or 0, g["population_basis"]) for g in governments}
    for gid, year in ahead.keys() - {(row[0], row[1]) for row in years_out}:
        st, kind, population, _ = kinds[gid]
        if st in names:
            cell = goals[("local", st, kind, year)]
            cell[0] += 1
            cell[20 if ahead[(gid, year)] else 21] += 1
            cell[14] += population
    for gid, year, expected, n, _, _, precincts, shaped in years_out:
        st, kind, population, population_basis = kinds[gid]
        if st not in names or not 2000 <= year <= today.year or not (expected or n):
            continue
        cell = goals[("local", st, kind, year)]
        cell[0] += 1
        cell[1] += n > 0
        if n == 0 and (gid, year) in ahead:
            cell[20 if ahead[(gid, year)] else 21] += 1
            cell[14] += population
            continue
        cell[2] += n == 0
        cell[8] += precincts
        cell[9] += shaped
        cell[14] += population
        cell[15] += population if n > 0 else 0
        found = None if n > 0 else coverage.gap(registry, ("results",), "us", st, kind, "", year)
        move(cell, 2, 1, found)
        cell[16] += population if n == 0 and found is None else 0
        cell[14] -= population if found is not None and found["category"] == "not_applicable" else 0
        cell[17] += population if population_basis in ("place", "county") else 0
    measures: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for (st, year, level, _), c in by_key.items():
        if level == "local_measure" and st in names:
            for i, key in enumerate(("held", "no_results", "upcoming", "pending")):
                measures[(st, year)][i] += c.get(key, 0)
    for (st, year), (count, bare, upcoming, pending) in list(measures.items()):
        if not 2000 <= year <= today.year:
            continue
        cell = goals[("local", st, "local_measure", year)]
        cell[0] += max(count, measures[(st, year - 4)][0] if (st, year - 4) in measures else 0)
        cell[1] += count - bare - upcoming - pending
        cell[2] += cell[0] - count
        cell[3] += bare
        cell[20] += upcoming
        cell[21] += pending

    with connect() as conn, conn.transaction():
        conn.execute("delete from coverage_goal where polity = 'us'")
        conn.execute("delete from coverage_government_year")
        conn.execute("delete from coverage_government")
        with conn.cursor().copy(
            "copy coverage_government (id, state, county, kind, name, function, population, population_basis, active,"
            " polity_id, period, phase, month, basis, next_date, next_election_id, cadence_id, access_id, class_id)"
            " from stdin"
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
                            "population_basis",
                            "active",
                            "polity_id",
                            "period",
                            "phase",
                            "month",
                            "basis",
                            "next_date",
                            "next_election_id",
                            "cadence_id",
                            "access_id",
                        )
                    ]
                    + [census_classes.get(CLASSES.get(g["kind"], ""))]
                )
        with conn.cursor().copy(
            "copy coverage_government_year (government_id, year, expected, held, missing, precinct, precincts, shaped)"
            " from stdin"
        ) as copy:
            for row in years_out:
                copy.write_row(row)
        timings["local"] = time.monotonic() - clock
        clock = time.monotonic()
        ready, counted = readiness.goals(conn, readiness.DAY)
        goals |= ready
        reached.update(counted)
        timings["readiness"] = time.monotonic() - clock
        with conn.cursor().copy(
            "copy coverage_goal (goal, state, level, year, total, complete, absent, bare, totals, partial, coarse,"
            " turnout, precincts, shaped, unshaped, units, undrawn, icpsr, population_total, population_complete,"
            " population_absent, population_estimated, major, published, upcoming, pending, known_gap, blocked,"
            " inapplicable) from stdin"
        ) as copy:
            for key, values in goals.items():
                copy.write_row([*key, *values])
        known = [
            coverage.note(row, hits[row["i"]], len({contest for contest, _ in hits[row["i"]]}) + reached[row["i"]])
            for row in registry
            if row["polity"] == "us"
        ]
        coverage_countries.write(
            conn,
            country_goals,
            [
                (*row[:9], json.dumps(json.loads(row[9]) + known, ensure_ascii=False), row[10])
                if row[0] == "us"
                else row
                for row in country_polities
            ],
        )
        conn.execute(
            """insert into coverage_cache_part values ('local', '', %s, %s, %s, %s)
               on conflict (stage, part) do update set inputs = excluded.inputs, built_at = excluded.built_at,
                   seconds = excluded.seconds, rows = excluded.rows""",
            [wanted[("local", "")], stamp, timings["local"], Json({"known": known})],
        )
    return (
        f"{len(todo)} of {len(scope)} states and {len(countries)} of {len(listed) + 1} "
        f"countries rebuilt; {len(governments):,} governments ({len(link):,} linked), {len(years_out):,} government "
        f"years, {len(goals):,} goal rows, {len(country_goals):,} other countries' goal rows in "
        f"{time.monotonic() - started:.0f}s ("
        + ", ".join(f"{stage} {seconds:.0f}s" for stage, seconds in timings.items())
        + ")"
    )
