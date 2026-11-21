import json
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from ea.store import DATA, Connection, connect

INPUTS = Path(__file__).parent / "release_inputs"
TARGETS = INPUTS / "targets.json"
ATTESTATIONS = INPUTS / "attestations.json"
STATUS = DATA / "ops" / "completeness.json"
FORMAT = 1
PRIORITY = ("in", "pk", "ng")
DONE = ("complete", "known_gap", "not_applicable")
OPEN = ("absent", "bare", "partial", "totals", "coarse", "blocked")
STATES = (*DONE, *OPEN, "upcoming", "pending")
TIERS: list[dict[str, object]] = [
    {"tier": 0, "key": "surveyed", "goals": []},
    {"tier": 1, "key": "national", "goals": ["national"]},
    {"tier": 2, "key": "history", "goals": ["national"]},
    {"tier": 3, "key": "fine", "goals": ["fine"]},
    {"tier": 4, "key": "subnational", "goals": ["subnational", "referendums", "ep"]},
    {"tier": 5, "key": "local", "goals": ["local"]},
]
GOALS: dict[int, tuple[str, ...]] = {
    1: ("national",),
    2: ("national",),
    3: ("fine",),
    4: ("subnational", "referendums", "ep"),
    5: ("local",),
}
US_FEDERAL = frozenset(("president", "us_house", "us_senate"))

COVERAGE_SQL = """
select polity, goal, level, year,
    sum(complete)::int, sum(known_gap)::int, sum(inapplicable)::int,
    sum(absent)::int, sum(bare)::int, sum(partial)::int, sum(totals)::int, sum(coarse)::int, sum(blocked)::int,
    sum(upcoming)::int, sum(pending)::int
from coverage_goal
group by 1, 2, 3, 4
"""
CLASSES_SQL = """
select lower(p.iso_code), k.id::text, k.key, k.first_held, k.earliest_record,
    k.valid_to is null or k.valid_to > current_date
from census_class k
join polity p on p.id = k.polity_id
where k.goal = 'national' and k.disposition is distinct from 'out_of_scope' and p.iso_code is not null
"""
UNCOUNTED_SQL = """
select class_id::text, count(*)::int, min(extract(year from date))::int, max(extract(year from coalesce(through, date)))::int,
    max(extract(year from date)) filter (where through is null and date <= current_date)::int
from census_date
where contests is null
group by 1
"""
CENSUS_CHANGES_SQL = """
select substr(pipeline, 8), max(published_at)
from changeset
where status = 'published' and pipeline like 'census.%' and pipeline <> 'census.world'
group by 1
"""
WORLD_SQL = "select lower(iso_code), coalesce(name_en, iso_code) from census_world where status = 'own'"
NAMES_SQL = """
select distinct on (lower(p.iso_code)) lower(p.iso_code), n.text
from polity p
join name n on n.entity_id = p.id
where p.iso_code is not null and n.lang = 'en'
order by lower(p.iso_code), n.is_preferred desc, n.kind = 'official' desc, n.text
"""
CLASS_GOALS_SQL = """
select cs.pipeline, k.goal, count(distinct cc.contest_id)::int
from contest_class cc
join contest c on c.id = cc.contest_id
join changeset cs on cs.id = c.changeset_id
join census_class k on k.id = cc.class_id
where cs.pipeline is not null
group by 1, 2
"""
FINER_SQL = """
select cs.pipeline, count(*) filter (where rs.completeness is not null)::int, count(*)::int
from result_set rs
join changeset cs on cs.id = rs.changeset_id
where rs.status = 'active' and cs.pipeline is not null
group by 1
"""
WATERMARK_SQL = (
    "select id::text, published_at from changeset where status = 'published' order by published_at desc limit 1"
)
BUILT_SQL = "select max(built_at) from coverage_cache_part"
RELEASE_SQL = "select calver, created_at, manifest -> 'polities' from release order by created_at desc limit 1"

type Counts = dict[str, int]


def _iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return value.isoformat()


def _mapped(polity: str, goal: str, level: str) -> str | None:
    if polity != "us":
        return goal
    federal = level in US_FEDERAL
    match goal:
        case "history" | "county":
            return "national" if federal else "subnational"
        case "precinct":
            return "fine" if federal else None
        case "local" | "shapes":
            return goal
    return None


def _empty() -> Counts:
    return dict.fromkeys(STATES, 0)


def _add(into: Counts, counts: Counts) -> None:
    for state in STATES:
        into[state] += counts[state]


def _summary(counts: Counts, need_done: bool = False) -> dict[str, object]:
    done = sum(counts[state] for state in DONE)
    left = sum(counts[state] for state in OPEN)
    return {
        **counts,
        "done": done,
        "open": left,
        "progress": round(done / (done + left), 4) if done + left else 1.0,
        "met": left == 0 and (done > 0 or not need_done),
    }


def _open(counts: Counts) -> int:
    return sum(counts[state] for state in OPEN)


def _breakdown(counts: Counts) -> str:
    return ", ".join(f"{counts[state]:,} {state.replace('_', ' ')}" for state in OPEN if counts[state])


def load_targets() -> dict[str, object]:
    return json.loads(TARGETS.read_text())


def load_attestations() -> dict[str, dict[str, dict[str, str]]]:
    return json.loads(ATTESTATIONS.read_text())


def _pipelines(conn: Connection) -> dict[str, str]:
    goals: dict[str, Counter[str]] = defaultdict(Counter)
    for pipeline, goal, contests in conn.execute(CLASS_GOALS_SQL).fetchall():
        goals[pipeline][goal] += contests
    finer = {pipeline: (fine, total) for pipeline, fine, total in conn.execute(FINER_SQL).fetchall()}
    out: dict[str, str] = {}
    for pipeline in sorted(set(goals) | set(finer)):
        fine, total = finer.get(pipeline, (0, 0))
        if total and fine * 2 > total:
            out[pipeline] = "fine"
        elif goals[pipeline]:
            out[pipeline] = goals[pipeline].most_common(1)[0][0]
    return out


def compute(conn: Connection) -> dict[str, object]:
    cells: dict[str, dict[tuple[str, str, int], Counts]] = defaultdict(dict)
    for polity, goal, level, year, *values in conn.execute(COVERAGE_SQL).fetchall():
        mapped = _mapped(polity, goal, level)
        if mapped is None:
            continue
        counts = cells[polity].setdefault((mapped, level, year), _empty())
        _add(counts, dict(zip(STATES, values, strict=True)))
    classes: dict[str, list[tuple[str, str, date | None]]] = defaultdict(list)
    defunct: dict[str, set[str]] = defaultdict(set)
    for polity, class_id, key, first_held, earliest, current in conn.execute(CLASSES_SQL).fetchall():
        classes[polity].append((class_id, key, first_held or earliest))
        if not current:
            defunct[polity].add(key)
    uncounted = {row[0]: row[1:] for row in conn.execute(UNCOUNTED_SQL).fetchall()}
    census_changed = {code: published for code, published in conn.execute(CENSUS_CHANGES_SQL).fetchall()}
    world = dict(conn.execute(WORLD_SQL).fetchall())
    names = {**dict(conn.execute(NAMES_SQL).fetchall()), **world}
    watermark = conn.execute(WATERMARK_SQL).fetchone()
    built = conn.execute(BUILT_SQL).fetchone()
    release = conn.execute(RELEASE_SQL).fetchone()
    released: dict[str, int] = {}
    if release is not None and isinstance(release[2], list):
        for entry in release[2]:
            if isinstance(entry, dict) and isinstance(entry.get("code"), str) and isinstance(entry.get("tier"), int):
                released[entry["code"]] = entry["tier"]
    targets = load_targets()
    raw_items = targets.get("items")
    wanted = {
        str(item["polity"]): int(item["tier"])
        for item in (raw_items if isinstance(raw_items, list) else [])
        if isinstance(item, dict)
    }
    reviews = load_attestations().get("census_review", {})

    polities: list[dict[str, object]] = []
    for code in sorted(set(cells) | set(world) | set(classes)):
        own = cells.get(code, {})
        goals: dict[str, Counts] = defaultdict(_empty)
        latest_year: dict[str, int] = {}
        years_of: dict[str, list[int]] = defaultdict(list)
        for (goal, level, year), counts in own.items():
            _add(goals[goal], counts)
            if goal == "national" and sum(counts[state] for state in (*DONE, *OPEN)):
                years_of[level].append(year)
                if level not in defunct[code]:
                    latest_year[level] = max(latest_year.get(level, year), year)
        unlisted = [
            {"key": key, "year": uncounted[class_id][3]}
            for class_id, key, _ in classes.get(code, [])
            if key not in defunct[code]
            and class_id in uncounted
            and uncounted[class_id][3] is not None
            and uncounted[class_id][3] > latest_year.get(key, 0)
        ]
        latest_classes = []
        latest = _empty()
        for level, year in sorted(latest_year.items()):
            counts = own[("national", level, year)]
            _add(latest, counts)
            latest_classes.append(
                {"key": level, "year": year, "done": sum(counts[s] for s in DONE), "open": _open(counts)}
            )
        problems: list[dict[str, str]] = []
        for class_id, key, first in classes.get(code, []):
            if class_id in uncounted:
                count, low, high, _ = uncounted[class_id]
                problems.append(
                    {
                        "class": key,
                        "kind": "uncounted",
                        "detail": f"{count} census dates without a contest count, {low} to {high}",
                    }
                )
            if first is None:
                problems.append({"class": key, "kind": "no_first_held", "detail": "the census gives no first election"})
            elif years_of.get(key) and min(years_of[key]) > first.year + 1:
                problems.append(
                    {
                        "class": key,
                        "kind": "starts_late",
                        "detail": f"expected contests start in {min(years_of[key])}, first held {first.year}",
                    }
                )
        review = reviews.get(code)
        gate = None
        if review is not None:
            changed = census_changed.get(code)
            gate = {**review, "current": changed is None or date.fromisoformat(review["on"]) >= changed.date()}
        surveyed = bool(classes.get(code)) or "national" in goals
        national = goals.get("national", _empty())
        rules = {
            1: _open(latest) == 0 and sum(latest[s] for s in DONE) > 0 and not unlisted,
            2: _open(national) == 0
            and sum(national[s] for s in DONE) > 0
            and not problems
            and gate is not None
            and bool(gate["current"]),
            3: _open(goals["fine"]) == 0 if "fine" in goals else True,
            4: all(_open(goals[goal]) == 0 for goal in GOALS[4] if goal in goals),
            5: _open(goals["local"]) == 0 if "local" in goals else True,
        }
        tier = -1
        if surveyed:
            tier = 0
            for step in range(1, 6):
                if not rules[step]:
                    break
                tier = step

        def need(step: int) -> list[str]:
            if step == 1:
                return ["national"] if _open(latest) or unlisted or not sum(latest[s] for s in DONE) else []
            return [goal for goal in GOALS[step] if goal in goals and _open(goals[goal])]

        target_tier = wanted.get(code)
        needs = need(tier + 1) if 0 <= tier < 5 else []
        target_needs = sorted(
            {goal for step in range(tier + 1, (target_tier or 0) + 1) for goal in need(step)} if target_tier else set()
        )
        blockers: list[dict[str, object]] = []
        if tier >= 0:
            for step in range(tier + 1, max(tier + 1, target_tier or 0) + 1):
                if step == 1 and _open(latest):
                    blockers.append(
                        {"tier": 1, "goal": "national", "reason": f"latest elections: {_breakdown(latest)}"}
                    )
                elif step == 1 and not sum(latest[s] for s in DONE):
                    blockers.append(
                        {"tier": 1, "goal": "national", "reason": "no national contest is held in the archive"}
                    )
                for item in unlisted if step == 1 else []:
                    blockers.append(
                        {
                            "tier": 1,
                            "goal": "census",
                            "reason": f"{item['key']}: the census lists {item['year']} without a contest count",
                        }
                    )
                for goal in need(step) if step > 1 else []:
                    blockers.append({"tier": step, "goal": goal, "reason": _breakdown(goals[goal])})
                if step == 2:
                    for problem in problems:
                        blockers.append(
                            {"tier": 2, "goal": "census", "reason": f"{problem['class']}: {problem['detail']}"}
                        )
                    if gate is None:
                        blockers.append({"tier": 2, "goal": "census", "reason": "census review not recorded"})
                    elif not gate["current"]:
                        blockers.append({"tier": 2, "goal": "census", "reason": "census changed after its review"})
        shapes = goals.get("shapes")
        polities.append(
            {
                "code": code,
                "name": names.get(code, code.upper()),
                "priority": code in PRIORITY,
                "tier": tier,
                "released_tier": released.get(code),
                "target_tier": target_tier,
                "needs": needs,
                "target_needs": target_needs,
                "goals": {goal: _summary(counts, goal == "national") for goal, counts in sorted(goals.items())},
                "latest": {**_summary(latest, True), "classes": latest_classes, "unlisted": unlisted},
                "span": {"ok": not problems, "problems": problems},
                "gates": {"census_review": gate},
                "blockers": blockers,
                "metrics": {"shapes": _summary(shapes)["progress"] if shapes is not None else None},
            }
        )
    polities.sort(key=lambda entry: (not entry["priority"], -int(str(entry["tier"])), str(entry["code"])))
    tiers_of = {str(entry["code"]): int(str(entry["tier"])) for entry in polities}
    items = [
        {"polity": polity, "tier": tier, "reached": tiers_of.get(polity, -1) >= tier} for polity, tier in wanted.items()
    ]
    return {
        "format": FORMAT,
        "computed_at": _iso(datetime.now(UTC)),
        "coverage_built_at": _iso(built[0]) if built is not None else None,
        "watermark": {
            "changeset_id": watermark[0] if watermark is not None else None,
            "published_at": _iso(watermark[1]) if watermark is not None else None,
        },
        "tiers": TIERS,
        "target": {
            "version": targets.get("version"),
            "date": targets.get("date"),
            "items": items,
            "met": all(item["reached"] for item in items),
        },
        "release": {"version": release[0], "published_at": _iso(release[1])} if release is not None else None,
        "polities": polities,
        "pipelines": _pipelines(conn),
    }


def snapshot() -> dict[str, object]:
    with connect() as conn:
        conn.execute("set transaction isolation level repeatable read, read only")
        return compute(conn)


def status() -> str:
    found = snapshot()
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(found, indent=1, ensure_ascii=False) + "\n")
    temporary.replace(STATUS)
    polities = found["polities"]
    assert isinstance(polities, list)
    by_tier: Counter[int] = Counter(int(entry["tier"]) for entry in polities)
    ladder = ", ".join(f"T{tier}: {by_tier[tier]}" for tier in range(6))
    return f"{STATUS}: {len(polities)} polities ({ladder}; not surveyed: {by_tier[-1]})"
