import csv
import json
import os
import urllib.request
from collections import defaultdict
from datetime import date

from ea import coverage_countries, readiness
from ea.store import ROOT, connect

BASE = os.environ.get("EA_BASE", "http://localhost:5173")
US = {
    "federal": ["president", "us_senate", "us_house"],
    "statewide": ["governor", "statewide_exec", "judicial"],
    "legislative": ["state_upper", "state_lower"],
    "local": ["county", "municipality", "township", "school", "college", "special", "other"],
    "referendums": ["statewide_measure"],
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
    for polity, goal, level, state, year, complete, icpsr, published in conn.execute(
        "select polity, goal, level, state, year, complete, icpsr, published from coverage_goal"
        " where icpsr < 0 or published < 0 or icpsr + published > complete"
        " or published > 0 and (polity <> 'us' or goal <> 'precinct')"
    ):
        problems.append(
            f"{polity} {goal} {level} {state} {year}: icpsr {icpsr} and published {published} against complete {complete}"
        )
    with (ROOT / ".agents" / "spec" / "finest-reported.csv").open() as f:
        for row in csv.DictReader(f):
            county = row.get("county") or ""
            if row["finest"] not in ("none", "county", "town", "precinct") or (
                row["finest"] == "precinct" and not (row["source"].strip() and row["url"].strip())
            ):
                problems.append(f"finest-reported {row['state']} {row['year']} {row['level']}: finest {row['finest']}")
            if (
                county
                and not conn.execute(
                    "select 1 from reporting_unit u join polity p on p.id = u.polity_id"
                    " where u.scheme_code = 'us.county' and u.code = %s and p.iso_code = %s",
                    [county, f"US-{row['state']}"],
                ).fetchone()
            ):
                problems.append(f"finest-reported {row['state']} {row['year']} {row['level']}: no county {county}")
    for cc in coverage_countries.countries():
        spec = coverage_countries.load(cc)
        for level in spec["levels"]:
            for span in level.get("lost", []):
                if not (span.get("quote") and span.get("note") and span.get("source") in spec["sources"]):
                    problems.append(f"{cc} {level['key']} lost {span.get('from')}: needs a source, a quote, and a note")
    for polity, goal, level, state, year, lost, bare in conn.execute(
        "select polity, goal, level, state, year, lost, bare from coverage_goal where lost < 0"
        " or lost > 0 and (polity = 'us' or goal not in ('national', 'subnational', 'local', 'referendums', 'ep', 'fine'))"
    ):
        problems.append(f"{polity} {goal} {level} {state} {year}: lost {lost} outside a country vote goal")
    for polity, goal, level, state, year, upcoming, pending in conn.execute(
        "select polity, goal, level, state, year, upcoming, pending from coverage_goal where upcoming < 0 or pending < 0"
        " or (upcoming > 0 or pending > 0) and year < %s"
        " or complete + upcoming + absent + bare + totals + partial + lost + pending > total and goal <> 'readiness'",
        [date.today().year],
    ):
        problems.append(f"{polity} {goal} {level} {state} {year}: upcoming {upcoming} pending {pending} out of bounds")
    ready = readiness.spec(readiness.DAY)
    for level in ready["levels"]:
        for part in (level, level.get("specials") or {}):
            if (
                part
                and not level.get("schedule")
                and not (part.get("quote") and part.get("source") in ready["sources"])
            ):
                problems.append(f"readiness {level['key']}: needs a source and a quote")
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
    listing = json.loads(fetch("/api/v1/coverage"))
    overview = {row["polity"]: row for row in listing["polities"]}
    stale = conn.execute("select latest is not null and inputs is distinct from latest from coverage_build").fetchone()[
        0
    ]
    if listing["stale"] != stale or (("Rebuilt before the latest" in fetch("/coverage.md").decode()) != stale):
        problems.append(
            f"overview: stale is {listing['stale']} in REST, {stale} in the database, or the twin disagrees"
        )
    for cc, summary in overview.items():
        rest = json.loads(fetch(f"/api/v1/coverage/{cc}"))
        twin = json.loads(fetch(f"/coverage/{cc}.json"))
        if rest["stale"] != stale:
            problems.append(f"{cc}: stale is {rest['stale']} in REST and {stale} in the database")
        if rest["tiers"] != twin["tiers"]:
            problems.append(f"{cc}: REST and JSON twin tiers differ")
        markdown = fetch(f"/coverage/{cc}.md").decode()
        if ("Rebuilt before the latest" in markdown) != stale:
            problems.append(f"{cc}: the twin's stale line does not match the database")
        levels = {lv["key"]: lv for lv in rest["summary"]["levels"]}
        for row in rest.get("readiness", []):
            total, complete = conn.execute(
                "select coalesce(sum(total), 0), coalesce(sum(complete), 0) from coverage_goal"
                " where polity = %s and goal = 'readiness' and level = %s",
                [cc, row["level"]],
            ).fetchone()
            if (total, complete) != (row["total"], row["complete"]) or row["complete"] > row["total"]:
                problems.append(
                    f"{cc} readiness {row['level']}: page {row['complete']}/{row['total']} db {complete}/{total}"
                )
            if f"{row['complete']:,} of {row['total']:,} ready" not in markdown:
                problems.append(f"{cc} readiness {row['level']}: not in the twin")
        for note in rest["summary"]["notes"]:
            if note.get("canvass") is not None and note["note"] not in markdown:
                problems.append(f"{cc} {note['canvass']} {note['from']} {note['level']}: canvass note not in the twin")
        for key in levels if cc != "us" else []:
            missing = ", ".join(
                str(n["from"]) if n["from"] == n["to"] else f"{n['from']}–{n['to']}"
                for n in rest["summary"]["notes"]
                if n["level"] == key
                and n["goal"] != "fine"
                and n.get("lost") is None
                and n["from"] is not None
                and n.get("listed") is not False
            )
            if missing and f"No official count for {missing}" not in markdown:
                problems.append(f"{cc} {key}: uncounted years {missing} not in the twin")
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
                    total, complete, icpsr, published, lost, upcoming, pending = conn.execute(
                        "select coalesce(sum(total), 0), coalesce(sum(complete + upcoming), 0), coalesce(sum(icpsr), 0),"
                        " coalesce(sum(published), 0), coalesce(sum(lost), 0), coalesce(sum(upcoming), 0),"
                        " coalesce(sum(pending), 0) from coverage_goal where polity = %s"
                        " and goal = %s and level = any(%s) and year between %s and %s",
                        [cc, goal, keys, low, high],
                    ).fetchone()
                    if (total, complete) != (cell["total"], cell["complete"]) and not (cc == "us" and total == 0):
                        problems.append(
                            f"{cc} {key} {level} {column}: page {cell['complete']}/{cell['total']} db {complete}/{total}"
                        )
                    if total and (icpsr, published) != (cell["icpsr"], cell["published"]):
                        problems.append(
                            f"{cc} {key} {level} {column}: page icpsr {cell['icpsr']} published {cell['published']}"
                            f" db {icpsr} {published}"
                        )
                    if lost != cell["lost"]:
                        problems.append(f"{cc} {key} {level} {column}: page lost {cell['lost']} db {lost}")
                    if (upcoming, pending) != (cell["upcoming"], cell["pending"]):
                        problems.append(
                            f"{cc} {key} {level} {column}: page upcoming {cell['upcoming']} pending {cell['pending']}"
                            f" db {upcoming} {pending}"
                        )
                    for count, phrase in (
                        (cell["upcoming"], "upcoming and ready"),
                        (cell["lost"], "with no surviving votes"),
                        (cell["icpsr"], "rely on ICPSR alone"),
                        (cell["published"], "at the finest unit published"),
                    ):
                        if level is None and count > 0 and f"{count:,} {phrase}" not in markdown:
                            problems.append(f"{cc} {key} {column}: {count:,} {phrase} not in the twin")
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
            if found is None:
                expected = None
            elif cc != "us" or tier["key"] == "local":
                expected = found["cells"].get("constituency" if cc != "us" else "since_any")
            else:
                parts = [found["cells"][c] for c in ("before_any", "since_any") if found["cells"].get(c) is not None]
                expected = {k: sum(p[k] for p in parts) for k in parts[0]} if parts else None
            if found is None or expected != tier["cell"]:
                problems.append(f"{cc} overview {tier['key']} differs from the country page")
    return problems


def check() -> str:
    with connect() as conn:
        behind = coverage_countries.record(conn)
        conn.commit()
        problems = database(conn) + pages(conn)
        if behind:
            commit = conn.execute("select commit from coverage_build").fetchone()[0]
            built = "an unstamped build" if commit is None else f"commit {commit[:12]}"
            problems.append(
                f"STALE: the coverage tables come from {built}, which predates main's {', '.join(behind)};"
                " the next ea-coverage refresh rebuilds them, and until then the pages say so"
            )
    if problems:
        raise SystemExit("\n".join(problems))
    return "coverage checks pass"
