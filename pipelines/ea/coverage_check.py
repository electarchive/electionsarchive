import json
import os
import urllib.request
from collections import defaultdict
from datetime import date

from ea import coverage_countries
from ea.store import connect

BASE = os.environ.get("EA_BASE", "http://localhost:5173")
US = {
    "federal": ["president", "us_senate", "us_house"],
    "statewide": ["governor", "statewide_exec", "judicial", "statewide_measure"],
    "legislative": ["state_upper", "state_lower"],
    "local": ["county", "municipality", "township", "school", "college", "special", "other"],
    "turnout": ["general", "primary", "runoff", "referendum"],
}
US_GOALS = {
    "before_county": ("history", None),
    "before_district": ("history", None),
    "before_shapes": ("shapes_history", None),
    "since_county": ("county", None),
    "since_precinct": ("precinct", None),
    "since_shapes": ("shapes", None),
    "before": ("turnout", "before"),
    "since": ("turnout", "since"),
}
OVER = "In some years the archive holds more races than the official count lists"


def fetch(path: str) -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=180) as response:
        return response.read()


def database(conn) -> list[str]:
    problems = []
    for polity, goal, level, state, year, total, complete, low in conn.execute(
        "select polity, goal, level, state, year, total, complete, least(total, complete, absent, bare, totals, partial,"
        " coarse, shaped, unshaped) from coverage_goal where complete > total or least(total, complete, absent, bare,"
        " totals, partial, coarse, shaped, unshaped) < 0"
    ):
        problems.append(f"{polity} {goal} {level} {state} {year}: complete {complete} of {total}, lowest value {low}")
    sums: dict[tuple[str, str, int], int] = defaultdict(int)
    for polity, level, goal, year, total in conn.execute(
        "select polity, level, goal, year, sum(total) from coverage_goal where polity <> 'us' group by 1, 2, 3, 4"
    ):
        sums[(polity, level, goal, year)] = total
    notes = {
        polity: [n for n in found if n["from"] is None and n["note"].startswith(OVER)]
        for polity, found in conn.execute("select polity, notes from coverage_polity")
    }
    for cc in coverage_countries.countries():
        spec = coverage_countries.load(cc)
        for level in spec["levels"]:
            cited: dict[int, int] = defaultdict(int)
            for e in level.get("elections", []):
                if e.get("contests") is not None:
                    cited[int(str(e.get("year") or e["date"])[:4])] += e["contests"]
            over = any(n["level"] == level["key"] for n in notes.get(cc, []))
            for year, want in cited.items():
                have = sums.get((cc, level["key"], level["goal"], year), 0)
                if have < want:
                    problems.append(f"{cc} {level['key']} {year}: total {have} below the cited {want}")
                if have > want and not over:
                    problems.append(f"{cc} {level['key']} {year}: total {have} above the cited {want} with no note")
    return problems


def pages(conn) -> list[str]:
    problems = []
    current = date.today().year
    overview = {row["polity"]: row for row in json.loads(fetch("/api/v1/coverage"))["polities"]}
    for cc, summary in overview.items():
        rest = json.loads(fetch(f"/api/v1/coverage/{cc}"))
        twin = json.loads(fetch(f"/coverage/{cc}.json"))
        if rest["tiers"] != twin["tiers"]:
            problems.append(f"{cc}: REST and JSON twin tiers differ")
        markdown = fetch(f"/coverage/{cc}.md").decode()
        levels = {lv["key"]: lv for lv in rest["summary"]["levels"]}
        for tier in rest["tiers"]:
            scopes = [(tier["key"], None, tier["cells"])] + [
                (tier["key"], lv["key"], lv["cells"]) for lv in tier["levels"]
            ]
            for key, level, cells in scopes:
                if level is not None:
                    keys = [level]
                elif cc == "us":
                    keys = US[key]
                else:
                    keys = [k for k, v in levels.items() if v["goal"] == key]
                for column, cell in cells.items():
                    if cell is None:
                        continue
                    if cell["complete"] > cell["total"] or min(cell.values()) < 0:
                        problems.append(f"{cc} {key} {level} {column}: complete {cell['complete']} of {cell['total']}")
                    if column in ("any", "before_any", "since_any"):
                        continue
                    goal, era = (
                        US_GOALS.get(column, ("local", None))
                        if cc == "us"
                        else (
                            key if column == "constituency" else column,
                            None,
                        )
                    )
                    low, high = {"before": (0, 1999), "since": (2000, 9999)}.get(era, (0, 9999))
                    total, complete = conn.execute(
                        "select coalesce(sum(total), 0), coalesce(sum(complete), 0) from coverage_goal where polity = %s"
                        " and goal = %s and level = any(%s) and year <> %s and year between %s and %s",
                        [cc, goal, keys, current, low, high],
                    ).fetchone()
                    if (total, complete) != (cell["total"], cell["complete"]) and not (cc == "us" and total == 0):
                        problems.append(
                            f"{cc} {key} {level} {column}: page {cell['complete']}/{cell['total']} db {complete}/{total}"
                        )
                    if (
                        level is None
                        and cell["total"] > 0
                        and f"{cell['complete']:,} of {cell['total']:,}" not in markdown
                    ):
                        problems.append(
                            f"{cc} {key} {column}: {cell['complete']:,} of {cell['total']:,} not in the twin"
                        )
        for tier in summary["tiers"]:
            found = next((t for t in rest["tiers"] if t["key"] == tier["key"]), None)
            column = "constituency" if cc != "us" else ("since_any" if tier["key"] == "local" else "since_county")
            if found is None or found["cells"].get(column) != tier["cell"]:
                problems.append(f"{cc} overview {tier['key']} differs from the country page")
    return problems


def check() -> str:
    with connect() as conn:
        problems = database(conn) + pages(conn)
    if problems:
        raise SystemExit("\n".join(problems))
    return "coverage checks pass"
