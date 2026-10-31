import csv
import html
import json
import os
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date

from ea import coverage, coverage_countries, readiness
from ea.store import connect

BASE = os.environ.get("EA_BASE", "http://localhost:5173")
US = {
    "federal": ["president", "us_senate", "us_house"],
    "statewide": ["governor", "statewide_exec", "judicial"],
    "legislative": ["state_upper", "state_lower"],
    "local": ["county", "municipality", "township", "school", "college", "special", "other"],
    "referendums": ["statewide_measure"],
    "precinct_shapes": ["precinct"],
    "turnout": ["general", "primary", "runoff", "referendum"],
}
US_GOALS = {
    "before_county": ("history", None),
    "before_district": ("history", None),
    "before_shapes": ("shapes_history", None),
    "since_county": ("county", None),
    "since_shapes": ("shapes", None),
    "precinct_2000": ("precinct", "mid"),
    "precinct_2016": ("precinct", "late"),
    "before": ("turnout", "before"),
    "since": ("turnout", "since"),
}
GAP_GOALS = (
    "county",
    "history",
    "turnout",
    "precinct",
    "precinct_shapes",
    "shapes",
    "shapes_history",
    "local",
    "national",
    "fine",
    "subnational",
    "referendums",
    "ep",
)
OVER = "In some years the archive holds more races than the official count lists"


def fetch(path: str) -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=180) as response:
        return response.read()


def visible(path: str) -> str:
    page = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", fetch(path).decode())
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def shares(path: str, text: str) -> list[str]:
    return [
        f"{path}: shows {found}%"
        for found in re.findall(r"(?<![\w%.,/])(\d[\d,]*(?:\.\d+)?)%", re.sub(r"https?://\S+", " ", text))
        if float(found.replace(",", "")) > 100
    ]


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
    with coverage.FINEST.open() as f:
        for row in csv.DictReader(f):
            county = row.get("county") or ""
            if row["finest"] not in ("none", "county", "town", "district", "precinct") or (
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
    polities = {"us", *coverage_countries.countries()}
    for row in coverage.registry():
        where = f"coverage-gaps.tsv {row['polity']} {row['state']} {row['level']} {row['years']} {row['grain']}"
        if (
            row["polity"] not in polities
            or row["category"] not in coverage.CATEGORIES
            or row["grain"] not in coverage.GRAINS
        ):
            problems.append(f"{where}: unknown polity, category, or grain")
        if not (row["reason"] and row["sources"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["checked"])):
            problems.append(f"{where}: needs a reason, the sources tried, and the date checked")
        if row["from"] > row["to"]:
            problems.append(f"{where}: years run backward")
        for contest in row["ids"]:
            if not conn.execute("select 1 from contest where id::text = %s", [contest]).fetchone():
                problems.append(f"{where}: no contest {contest}")
    for polity, goal, level, state, year in conn.execute(
        "select polity, goal, level, state, year from coverage_goal where known_gap < 0 or blocked < 0 or inapplicable < 0"
    ):
        problems.append(f"{polity} {goal} {level} {state} {year}: negative category count")
    for polity, goal, level, state, year, upcoming, pending in conn.execute(
        "select polity, goal, level, state, year, upcoming, pending from coverage_goal where upcoming < 0 or pending < 0"
        " or (upcoming > 0 or pending > 0) and year < %s"
        " or complete + upcoming + absent + bare + totals + partial + known_gap + blocked + pending > total and goal <> 'readiness'",
        [date.today().year],
    ):
        problems.append(f"{polity} {goal} {level} {state} {year}: upcoming {upcoming} pending {pending} out of bounds")
    for path, options, kind, publisher, siblings, results in conn.execute("""
        with m as (
            select l.entity_id as id, l.source_id from label l
            where l.kind = 'ballot' and l.value = 'no_candidate_qualified'
        ),
        same as (
            select m.id, p.id as peer, p.election_id = c.election_id as now from m join contest c on c.id = m.id
            join contest p on p.id <> c.id and (p.body_id = c.body_id or c.body_id is null and p.office_id = c.office_id)
        )
        select coalesce((select s.path from slug s where s.entity_id = m.id and s.is_current), m.id::text),
            (select count(*) from contest_option o where o.contest_id = m.id and o.kind in ('candidate', 'ticket', 'list')),
            s.kind::text, s.publisher,
            array(
                select distinct cs.publisher from same
                join contest_option o on o.contest_id = same.peer
                join candidacy y on y.option_id = o.id
                join citation ci on ci.entity_id = y.id
                join source cs on cs.id = ci.source_id and cs.kind = 'official_body'
                where same.id = m.id and same.now
            ),
            array(
                select distinct rs.publisher from same
                join result_set r on r.contest_id = same.peer
                join source rs on rs.id = r.source_id and rs.kind = 'official_body'
                where same.id = m.id
            )
        from m left join source s on s.id = m.source_id
    """):
        if options:
            problems.append(f"{path}: no candidate qualified, but {options} candidate options")
        certifiers = siblings or results
        if kind != "official_body" or publisher not in certifiers:
            problems.append(
                f"{path}: no-candidate label cites {publisher or 'nothing'} ({kind}), not the ballot's certifier"
                f" ({', '.join(certifiers) or 'none on record'})"
            )
    ready = readiness.spec(readiness.DAY)
    for level in ready["levels"]:
        for part in (level, *(level.get("specials") or [])):
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
    landing = visible("/coverage")
    problems += shares("/coverage", landing) + shares("/coverage.md", fetch("/coverage.md").decode())
    for cc, summary in overview.items():
        for tier in summary["tiers"]:
            cell = tier["cell"]
            if cell is not None and cell["total"] > 0 and f"{cell['complete']:,} of {cell['total']:,}" not in landing:
                problems.append(f"/coverage {cc} {tier['key']}: {cell['complete']:,} of {cell['total']:,} not shown")
    for cc, summary in overview.items():
        page = visible(f"/coverage/{cc}")
        rest = json.loads(fetch(f"/api/v1/coverage/{cc}"))
        twin = json.loads(fetch(f"/coverage/{cc}.json"))
        if rest["stale"] != stale:
            problems.append(f"{cc}: stale is {rest['stale']} in REST and {stale} in the database")
        if rest["tiers"] != twin["tiers"]:
            problems.append(f"{cc}: REST and JSON twin tiers differ")
        markdown = fetch(f"/coverage/{cc}.md").decode()
        problems += shares(f"/coverage/{cc}", page) + shares(f"/coverage/{cc}.md", markdown)
        for goal, total, complete in conn.execute(
            "select goal, sum(total), sum(complete + upcoming) from coverage_goal where polity = %s"
            " and goal = any(%s) and (goal <> 'precinct_shapes' or year >= 2016) group by 1",
            [cc, list(GAP_GOALS)],
        ):
            query = urllib.parse.urlencode({"goal": goal})
            rows = json.loads(fetch(f"/api/v1/coverage/{cc}/gaps?{query}"))
            found = (sum(r["total"] for r in rows), sum(r["complete"] for r in rows))
            if found != (total, complete):
                problems.append(f"{cc} gaps {goal}: list sums {found[1]}/{found[0]}, db {complete}/{total}")
        for span in rest.get("unshaped", []):
            total, inside = conn.execute(
                "select coalesce(sum(unshaped), 0), coalesce(sum(unshaped) filter (where year between %s and %s), 0)"
                " from coverage_goal where polity = %s and goal = %s and level = any(%s)",
                [span["from"], span["to"], cc, span["goal"], US.get(span["tier"], [])],
            ).fetchone()
            if inside * 2 <= total:
                problems.append(
                    f"{cc} {span['tier']} {span['goal']}: {span['from']}–{span['to']} holds {inside} of {total}"
                )
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
                and n.get("category") is None
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
                        ("precinct_shapes", US_GOALS[column][1])
                        if key == "precinct_shapes"
                        else US_GOALS.get(column, ("local", None))
                        if cc == "us"
                        else (
                            key if column == "constituency" else column,
                            None,
                        )
                    )
                    low, high = {
                        "before": (0, 1999),
                        "since": (2000, 9999),
                        "mid": (2000, 2015),
                        "late": (2016, 9999),
                    }.get(era, (0, 9999))
                    total, complete, icpsr, published, known_gap, blocked, inapplicable, upcoming, pending = (
                        conn.execute(
                            "select coalesce(sum(total), 0), coalesce(sum(complete + upcoming), 0), coalesce(sum(icpsr), 0),"
                            " coalesce(sum(published), 0), coalesce(sum(known_gap), 0), coalesce(sum(blocked), 0),"
                            " coalesce(sum(inapplicable), 0), coalesce(sum(upcoming), 0),"
                            " coalesce(sum(pending), 0) from coverage_goal where polity = %s"
                            " and goal = %s and level = any(%s) and year between %s and %s",
                            [cc, goal, keys, low, high],
                        ).fetchone()
                    )
                    if (total, complete) != (cell["total"], cell["complete"]) and not (cc == "us" and total == 0):
                        problems.append(
                            f"{cc} {key} {level} {column}: page {cell['complete']}/{cell['total']} db {complete}/{total}"
                        )
                    if total and (icpsr, published) != (cell["icpsr"], cell["published"]):
                        problems.append(
                            f"{cc} {key} {level} {column}: page icpsr {cell['icpsr']} published {cell['published']}"
                            f" db {icpsr} {published}"
                        )
                    if (known_gap, blocked, inapplicable) != (cell["knownGap"], cell["blocked"], cell["inapplicable"]):
                        problems.append(
                            f"{cc} {key} {level} {column}: page known gap {cell['knownGap']} blocked {cell['blocked']}"
                            f" not applicable {cell['inapplicable']} db {known_gap} {blocked} {inapplicable}"
                        )
                    if (upcoming, pending) != (cell["upcoming"], cell["pending"]):
                        problems.append(
                            f"{cc} {key} {level} {column}: page upcoming {cell['upcoming']} pending {cell['pending']}"
                            f" db {upcoming} {pending}"
                        )
                    for count, phrase in (
                        (cell["upcoming"], "upcoming and ready"),
                        (cell["knownGap"], "with a known gap"),
                        (cell["blocked"], "blocked"),
                        (cell["inapplicable"], "not applicable"),
                        (cell["icpsr"], "rely on ICPSR alone"),
                        (cell["published"], "at the finest unit published"),
                    ):
                        if level is None and count > 0 and f"{count:,} {phrase}" not in markdown:
                            problems.append(f"{cc} {key} {column}: {count:,} {phrase} not in the twin")
                    if level is None and cell["total"] > 0 and f"{cell['complete']:,} of {cell['total']:,}" not in page:
                        problems.append(
                            f"/coverage/{cc} {key} {column}: {cell['complete']:,} of {cell['total']:,} not shown"
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
