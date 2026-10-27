import json
from collections import defaultdict
from datetime import date

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
OPTIONS_SQL = """
select rc.id, rc.st, rc.level, rc.tacit, coalesce(c.magnitude, 1),
    count(o.k) filter (where o.kind in ('candidate', 'ticket', 'list')),
    count(o.k) filter (where o.kind = 'answer'),
    count(o.k) filter (where o.kind in ('candidate', 'ticket', 'list') and cs.pipeline <> 'us.fec_2026')
from rc
join contest c on c.id = rc.id
left join contest_option o on o.contest_id = rc.id and o.parent_option_id is null
left join changeset cs on cs.id = o.changeset_id
where rc.stage in ('general', 'special')
group by 1, 2, 3, 4, 5
"""


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
    for statement in contests.split(";\n\n"):
        if statement.strip():
            conn.execute(statement)
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
    for _, st, level, tacit, magnitude, candidates, answers, official in conn.execute(OPTIONS_SQL):
        if level not in units or level != "statewide_exec" and st not in listed[level]:
            continue
        weight = magnitude if units[level] == "seats" else 1
        cell = cells[(level, st)]
        cell["exist"] += weight
        if level == "statewide_measure":
            cell["candidates"] += weight if answers >= 1 else 0
            cell["ready"] += weight if answers >= 2 else 0
            continue
        cell["candidates"] += weight if candidates >= 1 else 0
        if candidates > magnitude or candidates >= magnitude and (tacit or official >= magnitude):
            cell["ready"] += weight
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
        values = [0] * 20
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
            " dated that day, has candidates with at least one candidate option (a measure with at least one answer), and is"
            " ready with more candidates than seats, or exactly as many when it is uncontested: a tacit result kind, or"
            " candidates written by a pipeline other than `us.fec_2026` (an official state list, not the FEC's filers)."
            " A measure is ready with both its answers. Places outside a level's cited list (the territories' delegates, governors, and legislatures) are left out. Other statewide offices are expected from the due-office schedule in"
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
