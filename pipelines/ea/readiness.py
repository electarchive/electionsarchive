import json
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ea import coverage, statewide_table
from ea.store import ROOT, connect

DAY = "2026-11-03"
FOLDER = ROOT / "pipelines" / "ea" / "readiness"
LEVELS = (
    "us_house",
    "us_senate",
    "governor",
    "statewide_exec",
    "judicial",
    "state_upper",
    "state_lower",
    "statewide_measure",
    "county_office",
    "municipal",
    "school_board",
    "local_judicial",
)
LABELS = {
    "us_house": "U.S. House",
    "us_senate": "U.S. Senate",
    "governor": "Governor",
    "statewide_exec": "Other statewide offices",
    "state_upper": "State senate",
    "state_lower": "State house",
    "statewide_measure": "Statewide ballot measures",
    "judicial": "Statewide judicial (states)",
    "county_office": "County offices (governments)",
    "municipal": "Municipal and township offices (governments)",
    "school_board": "School boards (governments)",
    "local_judicial": "Local judicial (races held)",
}
GOVERNMENTS = {
    "county": "county_office",
    "municipality": "municipal",
    "township": "municipal",
    "school": "school_board",
}
GOVERNMENT_NOTES = {
    "county_office": "Counted by government, not by seat, because no state calendar the archive cites gives a seat count for county offices. Expected is every county government in the 2022 Census of Governments that the coverage calendar has voting in November 2026, judged from its own past elections or else from its state's election law or pattern. A county is ready when the archive holds at least one of its November 3 contests and every one of them is ready.",
    "municipal": "Counted by government, not by seat, because no state calendar the archive cites gives a seat count for city, town, village, and township offices. Expected is every municipal and township government in the 2022 Census of Governments that the coverage calendar has voting in November 2026, judged from its own past elections or else from its state's election law or pattern. A government is ready when the archive holds at least one of its November 3 contests and every one of them is ready.",
    "school_board": "Counted by government, not by seat, because no state calendar the archive cites gives a seat count for school boards. Expected is every school district in the 2022 Census of Governments that the coverage calendar has voting in November 2026, judged from its own past elections or else from its state's election law or pattern. A district is ready when the archive holds at least one of its November 3 contests and every one of them is ready.",
}
COG = {
    "title": "2022 Census of Governments, Government Units",
    "publisher": "U.S. Census Bureau",
    "url": "https://www2.census.gov/programs-surveys/gus/datasets/2022/govt_units_2022.ZIP",
}
JUDGES = """oe.value ~ '(justice|judge|court|chancellor|magistrate)' and oe.value !~ '(clerk|county-judge)'"""
APPELLATE = "(op.kind in ('federal_unit', 'region') or oe.value ~ '(supreme|appeal)')"
ZONES = {
    "US": "Pacific/Honolulu",
    "US-HI": "Pacific/Honolulu",
    "US-AK": "America/Adak",
    "US-AS": "Pacific/Pago_Pago",
    "US-GU": "Pacific/Guam",
    "US-MP": "Pacific/Saipan",
    "US-PR": "America/Puerto_Rico",
    "US-VI": "America/Puerto_Rico",
    **dict.fromkeys(("US-CA", "US-WA", "US-OR", "US-NV", "US-ID"), "America/Los_Angeles"),
    "US-AZ": "America/Phoenix",
    **dict.fromkeys(
        ("US-MT", "US-WY", "US-UT", "US-CO", "US-NM", "US-TX", "US-ND", "US-SD", "US-NE", "US-KS"), "America/Denver"
    ),
    **dict.fromkeys(
        (
            "US-OK",
            "US-MN",
            "US-IA",
            "US-MO",
            "US-AR",
            "US-LA",
            "US-WI",
            "US-IL",
            "US-MS",
            "US-AL",
            "US-TN",
            "US-KY",
            "US-IN",
            "US-MI",
            "US-FL",
        ),
        "America/Chicago",
    ),
    "BR": "America/Rio_Branco",
    "CA": "America/Vancouver",
    "CA-BC": "America/Vancouver",
    "CA-AB": "America/Edmonton",
    "CA-NT": "America/Edmonton",
    "CA-YT": "America/Whitehorse",
    "CA-SK": "America/Regina",
    "CA-MB": "America/Winnipeg",
    "CA-NU": "America/Winnipeg",
    "CA-ON": "America/Winnipeg",
    "CA-QC": "America/Toronto",
    "CA-NB": "America/Halifax",
    "CA-NS": "America/Halifax",
    "CA-PE": "America/Halifax",
    "CA-NL": "America/St_Johns",
    "CH": "Europe/Zurich",
    "DE": "Europe/Berlin",
    "EU": "Atlantic/Azores",
    "FR": "Europe/Paris",
    "GB": "Europe/London",
    "IE": "Europe/Dublin",
    "IT": "Europe/Rome",
    "TR": "Europe/Istanbul",
}
OPTIONS_SQL = f"""
select rc.id, rc.st,
    case when {JUDGES} then case when {APPELLATE} then 'judicial' else 'local_judicial' end else rc.level end,
    coalesce(c.magnitude, 1),
    count(o.k) filter (where o.kind in ('candidate', 'ticket', 'list')),
    count(o.k) filter (where o.kind = 'answer'),
    exists (select from label l where l.entity_id = rc.id and l.kind = 'ballot' and l.value = 'no_candidate_qualified'),
    op.id::text
from rc
join contest c on c.id = rc.id
join election e on e.id = c.election_id
left join office f on f.id = c.office_id
left join body b on b.id = c.body_id
left join polity op on op.id = coalesce(f.polity_id, b.polity_id, e.polity_id)
left join lateral (select x.value from external_id x where x.entity_id = c.office_id and x.scheme = 'us.office' limit 1) oe
    on true
left join contest_option o on o.contest_id = rc.id and o.parent_option_id is null
where rc.stage in ('general', 'special')
group by rc.id, rc.st, 3, 4, op.id
"""

GOVERNMENTS_SQL = """
select g.state, g.kind, g.polity_id::text, g.name, coalesce(g.population, 0), g.county,
    coalesce(g.active and g.month = %(month)s and (%(year)s - g.phase) %% g.period = 0 and exists (
        select from coverage_government_year y where y.government_id = g.id and y.year = %(year)s - g.period and y.expected),
        false)
from coverage_government g
where g.kind in ('county', 'municipality', 'township', 'school')
"""

JUDGED_SQL = f"""
select distinct substr(p.iso_code, 4)
from contest c
join election e on e.id = c.election_id
join polity p on p.id = e.polity_id and p.iso_code like 'US-%%'
join office f on f.id = c.office_id
join polity op on op.id = f.polity_id
join external_id oe on oe.entity_id = c.office_id and oe.scheme = 'us.office'
where extract(year from e.date_from) in (%(year)s - 2, %(year)s - 4) and extract(month from e.date_from) = %(month)s
    and not c.notional and c.party_scope_id is null and e.kind in ('general', 'special') and {JUDGES} and {APPELLATE}
"""


READY_SQL = """
with x as (
    select c.id, c.kind::text as kind, coalesce(c.magnitude, 1) as seats, c.default_result_kind = 'tacit' as tacit,
        c.electoral_system_id is not null and (c.office_id is not null or c.body_id is not null or c.kind = 'ballot_question')
            and (c.district_area_id is not null or c.office_id is not null or c.kind = 'ballot_question') as framed,
        c.previous_round_id, e.kind::text = 'runoff' or coalesce(c.round_number, 1) > 1 as later,
        e.date_from, c.office_id, c.body_id, c.district_area_id,
        exists (select from label l where l.entity_id = c.id and l.kind = 'ballot' and l.value = 'no_candidate_qualified') as unqualified
    from upc join contest c on c.id = upc.id join election e on e.id = c.election_id
),
o as (
    select o.contest_id, o.id, o.kind::text as kind,
        cs.pipeline <> 'us.fec_2026' or exists (
            select from candidacy y join citation ci on ci.entity_id = y.id join source s on s.id = ci.source_id
            where y.option_id in (o.id, ch.id) and coalesce(s.url, '') !~ 'fec[.]gov'
        ) as official,
        o.party_id is not null or o.alliance_id is not null or exists (
            select from candidacy y where y.option_id in (o.id, ch.id)
                and (y.member_of_party_id is not null or y.is_independent
                    or exists (select from candidacy_nomination n where n.candidacy_id = y.id))
        ) as labeled,
        o.kind = 'list' and (exists (select from contest_option m where m.parent_option_id = o.id and m.kind = 'candidate')
            or exists (select from candidacy y where y.option_id = o.id)) as members
    from contest_option o
    join changeset cs on cs.id = o.changeset_id
    left join lateral (select m.id from contest_option m where m.parent_option_id = o.id limit 1) ch on true
    where o.contest_id in (select id from upc) and o.parent_option_id is null
        and o.kind in ('candidate', 'ticket', 'list', 'answer')
),
k as (
    select x.id,
        count(o.id) filter (where o.kind in ('candidate', 'ticket')) as people,
        count(o.id) filter (where o.kind in ('candidate', 'ticket') and o.official) as official,
        count(o.id) filter (where o.kind in ('candidate', 'ticket', 'list') and not o.labeled) as unlabeled,
        count(o.id) filter (where o.kind in ('candidate', 'ticket') and o.labeled) as labeled,
        count(o.id) filter (where o.kind = 'list') as lists,
        count(o.id) filter (where o.kind = 'list' and not o.members) as empty,
        count(o.id) filter (where o.kind = 'answer') as answers
    from x left join o on o.contest_id = x.id
    group by x.id
),
p as (
    select x.id, (
        select pc.id from contest pc join election pe on pe.id = pc.election_id
        where pc.office_id = x.office_id and pe.date_from < x.date_from and not pc.notional
            and exists (select from contest_option po where po.contest_id = pc.id and po.kind in ('candidate', 'ticket'))
        order by pe.date_from desc limit 1) as previous
    from x where x.office_id is not null
    union all
    select x.id, (
        select pc.id from contest pc join election pe on pe.id = pc.election_id
        where pc.body_id = x.body_id and pc.district_area_id = x.district_area_id and pe.date_from < x.date_from
            and not pc.notional
            and exists (select from contest_option po where po.contest_id = pc.id and po.kind in ('candidate', 'ticket'))
        order by pe.date_from desc limit 1)
    from x where x.office_id is null and x.body_id is not null
),
h as (
    select p.id, exists (
        select from contest_option o
        left join candidacy y on y.option_id = o.id
        where o.contest_id = p.previous
            and o.kind in ('candidate', 'ticket') and (o.party_id is not null or y.member_of_party_id is not null)
    ) as partisan
    from p
)
select x.id, x.framed and (x.previous_round_id is not null or not x.later) and case
    when x.kind = 'ballot_question' then k.answers >= 2 and exists (select from ballot_question_text t where t.contest_id = x.id)
    when x.later and x.previous_round_id in (select id from upc) then true
    when x.unqualified then k.people = 0
    when k.lists > 0 then k.empty = 0 and k.unlabeled = 0
    else (k.people > x.seats or k.people >= x.seats and (x.tacit or k.official >= x.seats)
            or k.people > 0 and k.official = k.people)
        and (k.unlabeled = 0 or k.labeled = 0 and not coalesce(h.partisan, false))
end
from x join k on k.id = x.id left join h on h.id = x.id
"""


def local_today(iso: str | None) -> date:
    iso = iso or ""
    zone = ZONES.get(iso) or ("America/New_York" if iso.startswith("US-") else ZONES.get(iso[:2], "UTC"))
    return datetime.now(ZoneInfo(zone)).date()


def local_days(conn) -> None:
    conn.execute("create temp table if not exists local_day (st text primary key, day date not null)")
    conn.execute("delete from local_day")
    conn.cursor().executemany(
        "insert into local_day values (%s, %s)",
        [(iso[3:] or "US", local_today(iso)) for iso in ZONES if iso[:2] == "US"],
    )


def flag(conn, table: str) -> None:
    ok = ready(conn, [str(cid) for (cid,) in conn.execute(f"select id from {table} where upcoming")])
    conn.execute("create temp table if not exists rdy (id uuid primary key, ok boolean not null)")
    conn.execute("delete from rdy")
    conn.cursor().executemany("insert into rdy values (%s, %s)", list(ok.items()))
    conn.execute("analyze rdy")


def ready(conn, ids: list[str]) -> dict[str, bool]:
    conn.execute("create temp table if not exists upc (id uuid primary key)")
    conn.execute("delete from upc")
    conn.cursor().executemany("insert into upc values (%s) on conflict do nothing", [(i,) for i in ids])
    conn.execute("analyze upc")
    return {str(cid): bool(ok) for cid, ok in conn.execute(READY_SQL)}


def spec(day: str) -> dict:
    return json.loads((FOLDER / f"{day}.json").read_text())


def measure(conn, day: str) -> dict:
    data = spec(day)
    year = int(day[:4])
    contests = coverage.CONTESTS_SQL[: coverage.CONTESTS_SQL.index("analyze cc;")]
    contests = contests.replace("create temp table uspol as", "create temp table if not exists uspol as", 1)
    contests = contests.replace("create temp table pstate as", "create temp table if not exists pstate as", 1)
    contests = contests.replace("create temp table cc as", "create temp table rc as", 1)
    dated = contests.replace(
        "where e.date_from is not null and not c.notional",
        f"where e.date_from = '{day}' and not c.notional",
        1,
    )
    if dated == contests:
        raise RuntimeError("readiness: coverage.CONTESTS_SQL no longer has the date filter readiness narrows")
    contests = dated
    local_days(conn)
    for statement in contests.split(";\n\n"):
        if statement.strip():
            conn.execute(statement)
    ok = ready(conn, [str(cid) for (cid,) in conn.execute("select id from rc")])
    expected: dict[tuple[str, str], int] = defaultdict(int)
    listed: dict[str, set[str]] = defaultdict(set)
    params = {"year": year, "month": int(day[5:7])}
    places: list[tuple[str, str, str, int, bool]] = []
    by_polity: dict[str, list[int]] = defaultdict(list)
    counties: dict[str, tuple[str, str]] = {}
    schools: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for st, kind, polity, name, population, county, due in conn.execute(GOVERNMENTS_SQL, params):
        if polity is not None:
            by_polity[polity].append(len(places))
            if kind == "county" and county is not None:
                counties[polity] = (county, name.removesuffix(" County").removesuffix(" Parish"))
        if kind == "school" and county is not None:
            schools[county].append((len(places), name))
        places.append((GOVERNMENTS[kind], st, name, population, due))
    from ea.coverage_tables import JUDICIAL

    judged = {st for (st,) in conn.execute(JUDGED_SQL, params)}
    judges: dict[str, int] = {}
    for st, period in JUDICIAL.items():
        if st in judged and (year - (2001 if st == "PA" else 2000)) % period == 0:
            judges[st] = len(places)
            places.append(("judicial", st, st, 0, True))
    held: dict[int, list[tuple[bool, bool]]] = defaultdict(list)
    for level in data["levels"]:
        if level.get("schedule"):
            due = {
                (st, office, seat)
                for st, office, _, when, joint, _, seat in statewide_table.schedule(conn, year)
                if when == year and not joint
            }
            for st, *_ in due:
                expected[(level["key"], st)] += 1
                listed[level["key"]].add(st)
            continue
        for part in (level, *(level.get("specials") or [])):
            for st, n in part.get("states", {}).items():
                expected[(level["key"], st)] += n
                listed[level["key"]].add(st)
    units = {level["key"]: level["unit"] for level in data["levels"]} | {"local_judicial": "races"}
    cells: dict[tuple[str, str], dict] = defaultdict(
        lambda: dict.fromkeys(
            ("exist", "candidates", "ready", "places", "population_total", "population_ready", "population_absent"), 0
        )
    )
    for cid, st, level, magnitude, candidates, answers, unqualified, polity in conn.execute(OPTIONS_SQL):
        has = unqualified or (answers if level == "statewide_measure" else candidates) >= 1
        if level == "judicial" and st not in judges:
            judges[st] = len(places)
            places.append(("judicial", st, st, 0, False))
        targets = (
            [judges[st]]
            if level == "judicial"
            else by_polity.get(polity or "", [])
            if level in ("county", "municipal", "school", "special_district")
            else []
        )
        if level == "school" and (polity or "") in counties:
            county, short = counties[polity or ""]
            system = [i for i, name in schools[county] if short in name]
            targets = [system[0]] if len(system) == 1 else targets
        if targets:
            for place in targets:
                held[place].append((has, ok.get(str(cid), False)))
            continue
        if level not in units or level not in ("statewide_exec", "local_judicial") and st not in listed[level]:
            continue
        weight = magnitude if units[level] == "seats" else 1
        cell = cells[(level, st)]
        cell["exist"] += weight
        cell["candidates"] += weight if has else 0
        cell["ready"] += weight if ok.get(str(cid)) else 0
    short = []
    for i, (level, st, name, population, due) in enumerate(places):
        contests = held.get(i, [])
        if not (due or contests):
            continue
        cell = cells[(level, st)]
        expected[(level, st)] += due
        candidates = bool(contests) and all(has for has, _ in contests)
        done = bool(contests) and all(ok for _, ok in contests)
        cell["places"] += 1
        cell["exist"] += bool(contests)
        cell["candidates"] += candidates
        cell["ready"] += done
        cell["population_total"] += population
        cell["population_ready"] += population if done else 0
        cell["population_absent"] += 0 if contests else population
        if not done:
            short.append(
                {
                    "level": level,
                    "state": st,
                    "name": name,
                    "population": population,
                    "status": "with candidates" if candidates else "in the archive" if contests else "missing",
                }
            )
    rows = []
    for level, st in sorted(set(expected) | set(cells)):
        cell = dict(cells[(level, st)])
        want = expected.get((level, st), 0)
        rows.append(
            {
                "level": level,
                "state": st,
                "expected": want,
                "total": cell.pop("places") or max(want, cell["exist"]),
                **cell,
            }
        )
    short.sort(key=lambda place: -int(place["population"]))
    return {"spec": data, "rows": rows, "short": short}


def goals(conn, day: str) -> dict[tuple[str, str, str, int], list[int]]:
    year = int(day[:4])
    out = {}
    for row in measure(conn, day)["rows"]:
        values = [0] * 22
        values[0] = row["total"]
        values[1] = row["ready"]
        values[2] = row["total"] - row["exist"]
        values[3] = row["exist"] - row["candidates"]
        values[5] = row["candidates"] - row["ready"]
        values[14] = row["population_total"]
        values[15] = row["population_ready"]
        values[16] = row["population_absent"]
        out[("readiness", row["state"], row["level"], year)] = values
    return out


def notes(day: str) -> tuple[list[dict], dict]:
    data = spec(day)
    year = int(day[:4])
    found = []
    for level in data["levels"]:
        for part in (level, *(level.get("specials") or [])):
            if part.get("source"):
                found.append(
                    {
                        "goal": "readiness",
                        "level": level["key"],
                        "from": year,
                        "to": year,
                        "note": part["quote"],
                        "readiness": part["source"],
                    }
                )
    found += [
        {"goal": "readiness", "level": level, "from": year, "to": year, "note": note, "readiness": "cog-2022"}
        for level, note in GOVERNMENT_NOTES.items()
    ]
    return found, {**data["sources"], "cog-2022": COG}


def markdown(day: str) -> str:
    with connect() as conn:
        found = measure(conn, day)
    rows = found["rows"]
    by_level: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for row in rows:
        cell = by_level[row["level"]]
        for i, key in enumerate(("total", "exist", "candidates", "ready")):
            cell[i] += row[key]
    lines = [
        f"## {day} readiness",
        "",
        coverage.table(
            ["Office", "Expected", "In the archive", "With candidates", "Ready", "Share ready"],
            [
                [
                    LABELS[level],
                    f"{v[0]:,}",
                    f"{v[1]:,}",
                    f"{v[2]:,}",
                    f"{v[3]:,}",
                    coverage.pct(v[3], v[0]),
                ]
                for level in LEVELS
                if (v := by_level.get(level)) is not None
            ],
        ),
        "",
        (
            f"Measured live on {date.today().isoformat()} against `pipelines/ea/readiness/{day}.json`. Seats for the House and"
            " the state legislatures (a contest counts its magnitude), races for the Senate, governors, and other statewide"
            " offices, and measures for ballot measures. A race is in the archive when a general or special contest for it is"
            " dated that day, and has candidates with at least one candidate option (a measure with at least one answer)."
            " Ready is the same rule as the upcoming track everywhere on /coverage (`READY_SQL`): an office or body, a"
            " district, and an electoral system; more candidates than seats, or exactly as many when it is uncontested (a"
            " tacit result, or candidates taken from an official state list rather than from the FEC's filers), or any number"
            " when every candidate comes from an official list, since an official list with fewer candidates than seats is"
            " still complete; and a party, nomination, or independent mark on every candidate unless the race is"
            " nonpartisan. A race the certifying body's list reports with no candidate qualified (write-in only, a `ballot` label of"
            " `no_candidate_qualified` citing that list) is ready, and counts as having candidates, when it has no candidate options."
            " A measure is ready with two answers and its official wording. Places outside a level's cited list (the territories' delegates, governors, and legislatures) are left out. Other statewide offices are expected from the due-office schedule in"
            " `statewide_table.py` (each office's term and last elections); the other counts cite the sources in the file."
            " Statewide judicial races (supreme and appellate courts, including those elected by district) count by state: a"
            " state is expected when `coverage_tables.JUDICIAL` has it electing judges that year and the archive holds a"
            " November statewide judicial race there two or four years earlier, and it is ready when the archive holds at"
            " least one of its Nov 3 judicial races and every one is ready. Local judicial races (trial courts, justices of"
            " the peace) have no cited count, so they are measured against the races the archive holds. County, municipal"
            " and township, and school board offices count by government, not seat: expected is every 2022 Census of"
            " Governments unit that the coverage build's calendar (`coverage_government`: its own cycle, else the state"
            " calendar or pattern) has electing in November of that year, and a government is ready when the archive holds"
            " at least one of its Nov 3 contests and every one is ready. Run `ea coverage us` first, since these read the"
            " last build's `coverage_government`."
        ),
        "",
        "### Short by state",
        "",
        coverage.table(
            ["Office", "State", "Expected", "In the archive", "With candidates", "Ready"],
            [
                [LABELS[r["level"]], r["state"], r["expected"], r["exist"], r["candidates"], r["ready"]]
                for r in rows
                if r["ready"] < r["total"] and r["level"] not in GOVERNMENTS.values()
            ],
        ),
        "",
        "### Local governments short by state, largest population first",
        "",
        coverage.table(
            ["Office", "State", "Expected", "In the archive", "With candidates", "Ready", "Population not ready"],
            [
                [
                    LABELS[r["level"]],
                    r["state"],
                    r["expected"],
                    r["exist"],
                    r["candidates"],
                    r["ready"],
                    f"{r['population_total'] - r['population_ready']:,}",
                ]
                for r in sorted(rows, key=lambda r: r["population_ready"] - r["population_total"])
                if r["ready"] < r["total"] and r["level"] in GOVERNMENTS.values()
            ],
        ),
        "",
        "### Counties and municipalities over 100,000 not ready",
        "",
        coverage.table(
            ["Government", "State", "Office", "Population", "Stands at"],
            [
                [p["name"], p["state"], LABELS[p["level"]], f"{p['population']:,}", p["status"]]
                for p in found["short"]
                if p["population"] >= 100_000 and p["level"] in ("county_office", "municipal")
            ],
        ),
    ]
    split = [r for r in rows if r["level"] == "statewide_exec" and r["exist"] > 0 and r["exist"] != r["expected"]]
    lines += [
        "",
        "### Other statewide offices: schedule against the archive",
        "",
        (
            "States where the races the archive holds for that day differ from the due-office schedule, to settle against"
            " each Secretary of State's candidate list."
        ),
        "",
        coverage.table(
            ["State", "Schedule", "In the archive"],
            [[r["state"], r["expected"], r["exist"]] for r in split],
        )
        if split
        else "None yet.",
        "",
    ]
    return "\n".join(lines)
