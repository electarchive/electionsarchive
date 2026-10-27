import json
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ea import coverage, statewide_table
from ea.store import ROOT, connect

DAY = "2026-11-03"
FOLDER = ROOT / "pipelines" / "ea" / "readiness"
LEVELS = ("us_house", "us_senate", "governor", "statewide_exec", "state_upper", "state_lower", "statewide_measure")
LABELS = {
    "us_house": "U.S. House",
    "us_senate": "U.S. Senate",
    "governor": "Governor",
    "statewide_exec": "Other statewide offices",
    "state_upper": "State senate",
    "state_lower": "State house",
    "statewide_measure": "Statewide ballot measures",
}
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
OPTIONS_SQL = """
select rc.id, rc.st, rc.level, coalesce(c.magnitude, 1),
    count(o.k) filter (where o.kind in ('candidate', 'ticket', 'list')),
    count(o.k) filter (where o.kind = 'answer')
from rc
join contest c on c.id = rc.id
left join contest_option o on o.contest_id = rc.id and o.parent_option_id is null
where rc.stage in ('general', 'special')
group by 1, 2, 3, 4
"""


READY_SQL = """
with x as (
    select c.id, c.kind::text as kind, coalesce(c.magnitude, 1) as seats, c.default_result_kind = 'tacit' as tacit,
        c.electoral_system_id is not null and (c.office_id is not null or c.body_id is not null or c.kind = 'ballot_question')
            and (c.district_area_id is not null or c.office_id is not null or c.kind = 'ballot_question') as framed,
        c.previous_round_id, e.kind::text = 'runoff' or coalesce(c.round_number, 1) > 1 as later,
        e.date_from, c.office_id, c.body_id, c.district_area_id
    from upc join contest c on c.id = upc.id join election e on e.id = c.election_id
),
o as (
    select o.contest_id, o.id, o.kind::text as kind, cs.pipeline,
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
        count(o.id) filter (where o.kind in ('candidate', 'ticket') and o.pipeline <> 'us.fec_2026') as official,
        count(o.id) filter (where o.kind in ('candidate', 'ticket', 'list') and not o.labeled) as unlabeled,
        count(o.id) filter (where o.kind in ('candidate', 'ticket') and o.labeled) as labeled,
        count(o.id) filter (where o.kind = 'list') as lists,
        count(o.id) filter (where o.kind = 'list' and not o.members) as empty,
        count(o.id) filter (where o.kind = 'answer') as answers
    from x left join o on o.contest_id = x.id
    group by x.id
),
h as (
    select x.id, exists (
        select from contest_option o
        left join candidacy y on y.option_id = o.id
        where o.contest_id = (
            select pc.id from contest pc join election pe on pe.id = pc.election_id
            where pe.date_from < x.date_from and not pc.notional
                and (pc.office_id = x.office_id or pc.body_id = x.body_id and pc.district_area_id = x.district_area_id)
                and exists (select from contest_option po where po.contest_id = pc.id and po.kind in ('candidate', 'ticket'))
            order by pe.date_from desc limit 1)
            and o.kind in ('candidate', 'ticket') and (o.party_id is not null or y.member_of_party_id is not null)
    ) as partisan
    from x where x.office_id is not null or x.body_id is not null
)
select x.id, x.framed and (x.previous_round_id is not null or not x.later) and case
    when x.kind = 'ballot_question' then k.answers >= 2 and exists (select from ballot_question_text t where t.contest_id = x.id)
    when x.later and x.previous_round_id in (select id from upc) then true
    when k.lists > 0 then k.empty = 0 and k.unlabeled = 0
    else (k.people > x.seats or k.people >= x.seats and (x.tacit or k.official >= x.seats))
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
    contests = contests.replace(
        "where e.date_from is not null and not c.notional;",
        f"where e.date_from = '{day}' and not c.notional;",
        1,
    )
    local_days(conn)
    for statement in contests.split(";\n\n"):
        if statement.strip():
            conn.execute(statement)
    ok = ready(conn, [str(cid) for (cid,) in conn.execute("select id from rc")])
    expected: dict[tuple[str, str], int] = defaultdict(int)
    listed: dict[str, set[str]] = defaultdict(set)
    for level in data["levels"]:
        if level.get("schedule"):
            due = {
                (st, office)
                for st, office, _, when, joint, _ in statewide_table.schedule(conn, year)
                if when == year and not joint
            }
            for st, _ in due:
                expected[(level["key"], st)] += 1
                listed[level["key"]].add(st)
            continue
        for part in (level, level.get("specials") or {}):
            for st, n in part.get("states", {}).items():
                expected[(level["key"], st)] += n
                listed[level["key"]].add(st)
    units = {level["key"]: level["unit"] for level in data["levels"]}
    cells: dict[tuple[str, str], dict] = defaultdict(lambda: dict.fromkeys(("exist", "candidates", "ready"), 0))
    for cid, st, level, magnitude, candidates, answers in conn.execute(OPTIONS_SQL):
        if level not in units or level != "statewide_exec" and st not in listed[level]:
            continue
        weight = magnitude if units[level] == "seats" else 1
        cell = cells[(level, st)]
        cell["exist"] += weight
        cell["candidates"] += weight if (answers if level == "statewide_measure" else candidates) >= 1 else 0
        cell["ready"] += weight if ok.get(str(cid)) else 0
    rows = []
    for level, st in sorted(set(expected) | set(cells)):
        cell = cells[(level, st)]
        want = expected.get((level, st), 0)
        rows.append(
            {
                "level": level,
                "state": st,
                "expected": want,
                "total": max(want, cell["exist"]),
                **cell,
            }
        )
    return {"spec": data, "rows": rows}


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
        out[("readiness", row["state"], row["level"], year)] = values
    return out


def notes(day: str) -> tuple[list[dict], dict]:
    data = spec(day)
    year = int(day[:4])
    found = []
    for level in data["levels"]:
        for part in (level, level.get("specials") or {}):
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
    return found, data["sources"]


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
            " tacit result kind, or candidates written by a pipeline other than `us.fec_2026`, an official state list rather"
            " than the FEC's filers); and a party, nomination, or independent mark on every candidate unless the race is"
            " nonpartisan. A measure is ready with two answers and its official wording. Places outside a level's cited list (the territories' delegates, governors, and legislatures) are left out. Other statewide offices are expected from the due-office schedule in"
            " `statewide_table.py` (each office's term and last elections); the other counts cite the sources in the file."
        ),
        "",
        "### Short by state",
        "",
        coverage.table(
            ["Office", "State", "Expected", "In the archive", "With candidates", "Ready"],
            [
                [LABELS[r["level"]], r["state"], r["expected"], r["exist"], r["candidates"], r["ready"]]
                for r in rows
                if r["ready"] < r["total"]
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
