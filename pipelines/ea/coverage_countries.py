import csv
import json
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from ea import coverage
from ea.store import ROOT, connect

DENOMINATORS = Path(__file__).parent / "denominators"
GOALS = ("national", "fine", "shapes", "subnational", "local", "referendums", "ep")
WINDOW = timedelta(days=75)
EP_SCHEMES = {
    "eu.member_state",
    "gb.euc",
    "gb.ep_constituency",
    "fr.ep_constituency",
    "it.ep_constituency",
    "ie.ep_constituency",
    "pl.ep_constituency",
}

CONTESTS_SQL = """
with recursive t(id) as (
    select id from polity where iso_code = %(iso)s
    union select pp.polity_id from polity_parent pp join t on pp.parent_id = t.id
)
select c.id::text, e.id::text, e.date_from, e.kind::text, coalesce(b.kind::text, o.kind::text, c.kind::text),
    op.iso_code, ep.iso_code, c.default_result_kind in ('tacit', 'show_of_hands'), c.district_area_id::text, a.scheme_code,
    coalesce(b.polity_id, o.polity_id, e.polity_id)::text
from contest c
join election e on e.id = c.election_id
join polity ep on ep.id = e.polity_id
left join body b on b.id = c.body_id
left join office o on o.id = c.office_id
left join polity op on op.id = coalesce(b.polity_id, o.polity_id, e.polity_id)
left join area a on a.id = c.district_area_id
where e.date_from is not null and e.date_from <= current_date and e.status <> 'scheduled' and not c.notional
    and case when %(iso)s = 'EU' then op.iso_code = 'EU' else e.polity_id in (select id from t) end
"""

UNITS_SQL = """
select r.contest_id::text, u.scheme_code, count(distinct u.k), count(distinct u.k) filter (where g.area_id is not null)
from result_set r
join coverage_cache_unit cu on cu.result_set_k = r.k
join reporting_unit u on u.k = cu.unit_k
left join (select distinct area_id from area_geometry) g on g.area_id = u.area_id and u.area_relation = 'equals'
where r.status <> 'withdrawn' and r.contest_id = any(%s::uuid[])
group by 1, 2
"""

UNIT_AREAS_SQL = """
select distinct r.contest_id::text, u.area_id::text
from result_set r
join coverage_cache_unit cu on cu.result_set_k = r.k
join reporting_unit u on u.k = cu.unit_k
where r.status <> 'withdrawn' and r.contest_id = any(%s::uuid[]) and u.scheme_code = any(%s) and u.area_id is not null
"""

PLACES_SQL = """
with recursive up(aid, cur, depth) as (
    select id, id, 0 from area where id = any(%s::uuid[])
    union all select up.aid, ap.parent_id, depth + 1 from up join area_parent ap on ap.area_id = up.cur where depth < 8
),
hit as (
    select distinct on (up.aid) up.aid, p.iso_code
    from up join polity_territory pt on pt.area_id = up.cur
    join polity p on p.id = pt.polity_id and p.kind in ('federal_unit', 'region') and p.iso_code like '%%-%%'
    order by up.aid, up.depth
),
code as (
    select distinct on (ac.area_id) ac.area_id, ac.code from area_code ac where ac.area_id = any(%s::uuid[])
    order by ac.area_id, ac.valid_from desc nulls last
)
select a.id::text, a.scheme_code, code.code, hit.iso_code
from area a left join hit on hit.aid = a.id left join code on code.area_id = a.id
where a.id = any(%s::uuid[])
"""

GEOMETRY_SQL = "select area_id::text, lower(valid), upper(valid) from area_geometry where area_id = any(%s::uuid[])"

LABELS_SQL = """
select substr(p.iso_code, length(%(iso)s) + 2), n.text
from polity p join name n on n.entity_id = p.id and n.is_preferred
where p.iso_code like %(iso)s || '-%%' and p.kind in ('federal_unit', 'region')
order by n.lang <> 'en'
"""

AREA_LABELS_SQL = """
select ac.code, n.text from area a join area_code ac on ac.area_id = a.id
join name n on n.entity_id = a.id and n.is_preferred
where a.scheme_code = %s order by n.lang <> 'en', ac.valid_from desc nulls last
"""

EU_LABELS_SQL = """
select p.iso_code, n.text from polity p join name n on n.entity_id = p.id and n.is_preferred
where p.kind = 'sovereign_state' order by n.lang <> 'en'
"""

SUMMARY_SQL = """
with recursive t(id) as (
    select id from polity where iso_code = %(iso)s
    union select pp.polity_id from polity_parent pp join t on pp.parent_id = t.id
)
select count(distinct e.id), count(c.id), min(extract(year from e.date_from))::int, max(extract(year from e.date_from))::int
from election e join contest c on c.election_id = e.id
left join body b on b.id = c.body_id
left join office o on o.id = c.office_id
left join polity op on op.id = coalesce(b.polity_id, o.polity_id, e.polity_id)
where e.date_from >= '1700-01-01' and e.date_from <= current_date and e.status <> 'scheduled' and not c.notional
    and case when %(iso)s = 'EU' then op.iso_code = 'EU' else e.polity_id in (select id from t) end
"""

FINEST_SQL = """
with recursive t(id) as (
    select id from polity where iso_code = %(iso)s
    union select pp.polity_id from polity_parent pp join t on pp.parent_id = t.id
)
select u.scheme_code, s.noun from reporting_unit u join scheme s on s.code = u.scheme_code
where u.polity_id in (select id from t) and (%(iso)s <> 'EU' or u.scheme_code = any(%(ep)s))
group by 1, 2, s.rank having count(*) >= 100 order by s.rank desc, count(*) desc limit 1
"""


IT_REGIONS = {
    "PIEMONTE": "01",
    "VALLE D'AOSTA": "02",
    "LOMBARDIA": "03",
    "TRENTINO-ALTO ADIGE": "04",
    "TRENTINO ALTO ADIGE": "04",
    "VENETO": "05",
    "FRIULI VENEZIA GIULIA": "06",
    "FRIULI-VENEZIA GIULIA": "06",
    "LIGURIA": "07",
    "EMILIA-ROMAGNA": "08",
    "EMILIA ROMAGNA": "08",
    "TOSCANA": "09",
    "UMBRIA": "10",
    "MARCHE": "11",
    "LAZIO": "12",
    "ABRUZZO": "13",
    "MOLISE": "14",
    "CAMPANIA": "15",
    "PUGLIA": "16",
    "BASILICATA": "17",
    "CALABRIA": "18",
    "SICILIA": "19",
    "SARDEGNA": "20",
}


def region(cc: str, scheme: str | None, code: str | None, parent: str | None, election_iso: str | None) -> str:
    if cc == "eu":
        return election_iso.split("-")[0]
    if parent is not None:
        return parent.split("-", 1)[1]
    if code is not None:
        if cc == "gb" and ":" in code:
            return code.split(":", 1)[1][:3].upper()
        if cc == "gb" and code[:4] in ("eng-", "sct-", "wls-", "irl-"):
            return code[:3].upper()
        if cc == "fr" and scheme == "fr.circonscription":
            return code[:3] if code.startswith("97") else code[:-2].zfill(2)
        if cc == "fr" and scheme == "fr.departement":
            return code.zfill(2)
        if cc == "tr" and scheme in ("tr.il", "tr.secim_cevresi", "tr.senato_cevresi"):
            return code.split("-")[0].zfill(2)
        if cc == "it" and scheme == "it.regione":
            return code
        if (
            cc == "it"
            and scheme is not None
            and scheme.startswith(("it.collegio", "it.uninominale", "it.plurinominale"))
        ):
            name = code.split(" - ")[0].rstrip(" 0123456789")
            return IT_REGIONS.get(name, "")
        if cc in ("br", "ch") and scheme in ("br.uf", "ch.canton"):
            return code
    if election_iso is not None and "-" in election_iso:
        return election_iso.split("-", 1)[1]
    return ""


def load(cc: str) -> dict:
    return json.loads((DENOMINATORS / f"{cc}.json").read_text())


def countries() -> list[str]:
    return sorted(p.stem for p in DENOMINATORS.glob("*.json"))


def matches(rule: dict, row: tuple) -> bool:
    _, _, day, ekind, body, body_iso, _, _, _, _, _ = row
    return (
        ekind in rule["kinds"]
        and body in rule["bodies"]
        and (
            (body_iso or "") == rule["iso"]
            or rule["iso"].endswith("*")
            and (body_iso or "").startswith(rule["iso"][:-1])
        )
        and rule.get("from", "0000") <= day.isoformat() < rule.get("to", "9999")
    )


def measure(conn, cc: str) -> dict:
    spec = load(cc)
    iso = spec["polity"]
    rows = list(conn.execute(CONTESTS_SQL, {"iso": iso}))
    levels = spec["levels"]
    picked: dict[str, list[tuple]] = defaultdict(list)
    for row in rows:
        for level in levels:
            if any(matches(rule, row) for rule in level["match"]):
                picked[level["key"]].append(row)
                break
    ids = [r[0] for rs in picked.values() for r in rs]
    units: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    for cid, scheme, n, shaped in conn.execute(UNITS_SQL, [ids]):
        units[cid][scheme] = (n, shaped)
    schemes = sorted({s for level in levels for s in level.get("schemes", [])})
    unit_areas: dict[str, set[str]] = defaultdict(set)
    for cid, aid in conn.execute(UNIT_AREAS_SQL, [ids, schemes]):
        unit_areas[cid].add(aid)
    areas = {r[8] for rs in picked.values() for r in rs if r[8] is not None} | {
        a for s in unit_areas.values() for a in s
    }
    places = {aid: (scheme, code, parent) for aid, scheme, code, parent in conn.execute(PLACES_SQL, [list(areas)] * 3)}
    geometry: dict[str, list[tuple]] = defaultdict(list)
    for aid, lo, hi in conn.execute(GEOMETRY_SQL, [list(areas)]):
        geometry[aid].append((lo, hi))

    labels: dict[str, str] = {}
    if cc == "eu":
        for code, text in conn.execute(EU_LABELS_SQL):
            labels.setdefault(code, text)
    else:
        for key, text in conn.execute(LABELS_SQL, {"iso": iso}):
            labels.setdefault(key, text)
        for scheme in spec.get("region_schemes", []):
            for code, text in conn.execute(AREA_LABELS_SQL, [scheme]):
                labels.setdefault(code.zfill(2) if scheme in ("fr.departement", "tr.il") else code, text)
    labels |= spec.get("region_labels", {})
    labels = {k: v for k, v in labels.items() if k in set(spec.get("regions") or labels)}
    return {
        "spec": spec,
        "picked": picked,
        "units": units,
        "unit_areas": unit_areas,
        "places": places,
        "geometry": geometry,
        "labels": labels,
    }


def summary(conn, cc: str) -> tuple:
    iso = cc.upper()
    elections, contests, first, last = conn.execute(SUMMARY_SQL, {"iso": iso}).fetchone() or (0, 0, None, None)
    top = conn.execute(FINEST_SQL, {"iso": iso, "ep": sorted(EP_SCHEMES)}).fetchone()
    return first, last, elections, contests, None if top is None else top[0], None if top is None else top[1]


def shaped_on(spans: list[tuple], day: date) -> str:
    if not spans:
        return "undrawn"
    if any((lo is None or lo <= day) and (hi is None or day < hi) for lo, hi in spans):
        return "complete"
    return "unshaped"


def tally(cc: str, data: dict) -> tuple[dict, list[dict], list[dict]]:
    spec = data["spec"]
    goals: dict[tuple[str, str, str, int], list[int]] = defaultdict(lambda: [0] * 14)
    elections_out: list[dict] = []
    notes: list[dict] = []
    for level in spec["levels"]:
        key, goal = level["key"], level["goal"]
        unit = level.get("unit", "district")
        schemes = set(level.get("schemes", []))
        grain = set(level.get("grain", []))
        fine = set(level.get("fine") or [])
        held = data["picked"].get(key, [])
        expected = level.get("elections", [])
        by_year = all("year" in e for e in expected) and bool(expected)
        gaps = [
            {"goal": goal, "level": key, "from": g["from"], "to": g["to"], "note": g["note"], "held": 0}
            for g in level.get("gaps", [])
        ]
        notes += gaps
        if level.get("fine_note"):
            notes.append({"goal": "fine", "level": key, "from": None, "to": None, "note": level["fine_note"]})
        groups: dict[str, list[tuple]] = defaultdict(list)
        slots: dict[str, dict] = {}
        over: list[str] = []
        deferred: dict[str, str] = {}
        for e in expected:
            slot = str(e["year"]) if by_year else e["date"]
            slots[slot] = e
            deferred |= dict.fromkeys(e.get("deferred", []), slot)
        dates = sorted((date.fromisoformat(s), s) for s in slots) if not by_year else []
        for row in held:
            day = row[2]
            if day.isoformat() in deferred:
                slot = deferred[day.isoformat()]
            elif by_year:
                slot = str(day.year)
                if level.get("cycle"):
                    slot = next((y for y in sorted(slots) if int(y) >= day.year), slot)
            else:
                near = min(dates, key=lambda d: abs(d[0] - day), default=None)
                slot = near[1] if near is not None and abs(near[0] - day) <= WINDOW else day.isoformat()
            groups[slot].append(row)
        for slot in sorted(set(slots) | set(groups)):
            e = slots.get(slot)
            rows = groups.get(slot, [])
            year = int(slot[:4])
            if e is None or e.get("contests") is None:
                span = next((g for g in gaps if g["from"] <= year <= g["to"]), None)
                if span is not None:
                    span["held"] += len(rows)
                elif rows:
                    notes.append(
                        {
                            "goal": goal,
                            "level": key,
                            "from": year,
                            "to": year,
                            "note": "" if e is None else e.get("note") or "",
                            "held": len(rows),
                            "listed": e is not None,
                        }
                    )
                continue
            n = e["contests"]
            day = date.fromisoformat(e["date"]) if "date" in e else (rows[0][2] if rows else date(year, 12, 31))
            status: dict[str, str] = {}
            fine_ok: dict[str, bool] = {}
            shape: dict[str, str] = {}
            place: dict[str, str] = {}
            coarse_only = False
            own = [r for r in rows if r[9] in schemes] if unit == "district" else []
            for r in rows:
                cid, tacit, district, scheme = r[0], r[7], r[8], r[9]
                got = data["units"].get(cid, {})
                results = bool(got)
                at_grain = sum(n_ for s, n_ in ((s, v[0]) for s, v in got.items()) if s in grain)
                finer = any(s in fine for s in got)
                if unit == "district":
                    if scheme in schemes:
                        k = cid if level.get("by_contest") else district
                        ok = results or tacit
                        if ok or status.get(k) != "complete":
                            status[k] = "complete" if ok else "bare"
                        fine_ok[k] = fine_ok.get(k, False) or finer
                        place[k] = place_of(cc, data, district, r[6])
                        shape[k] = shaped_on(data["geometry"].get(district, []), r[2])
                    elif not own:
                        found = data["unit_areas"].get(cid, set())
                        for k in found:
                            status[k] = "complete"
                            fine_ok[k] = fine_ok.get(k, False) or finer
                            place[k] = place_of(cc, data, k, r[6])
                            shape[k] = shaped_on(data["geometry"].get(k, []), r[2])
                        if results and not found:
                            coarse_only = True
                    elif results:
                        coarse_only = coarse_only or False
                else:
                    k = r[10] if unit == "polity" else cid
                    need = level.get("units") or 1
                    ok = tacit or (at_grain >= 0.9 * need or finer if grain else results)
                    current = "complete" if ok else "totals" if results else "bare"
                    rank = {"complete": 0, "totals": 1, "bare": 2}
                    if k not in status or rank[current] < rank[status[k]]:
                        status[k] = current
                    fine_ok[k] = fine_ok.get(k, False) or finer
                    place[k] = (
                        place_of(cc, data, district, r[6])
                        if district is not None
                        else region(cc, None, None, None, r[6])
                    )
            regions_expected = (e or {}).get("regions") or {}
            count_total = n or 0
            by_region: dict[str, list[int]] = defaultdict(lambda: [0] * 14)
            for k, st in status.items():
                cell = by_region[place.get(k, "")]
                cell[1] += st == "complete"
                cell[3] += st == "bare"
                cell[4] += st == "totals"
            held_n = len(status)
            if regions_expected:
                for reg, want in regions_expected.items():
                    cell = by_region[reg]
                    have = cell[1] + cell[3] + cell[4]
                    gap = max(0, want - have)
                    cell[4 if coarse_only else 2] += gap
                    cell[0] = max(want, have)
                for reg, cell in by_region.items():
                    if reg not in regions_expected:
                        cell[0] = cell[1] + cell[3] + cell[4]
                rest = count_total - sum(max(w, 0) for w in regions_expected.values())
                if rest > 0:
                    by_region[""][0] += rest
                    by_region[""][4 if coarse_only else 2] += rest
            else:
                for cell in by_region.values():
                    cell[0] = cell[1] + cell[3] + cell[4]
                gap = max(0, count_total - held_n)
                if gap:
                    cell = by_region[""]
                    cell[0] += gap
                    cell[4 if coarse_only else 2] += gap
            for reg, cell in by_region.items():
                if cell[0] == 0:
                    continue
                target = goals[(goal, reg, key, year)]
                for i, v in enumerate(cell):
                    target[i] += v
            total = max(count_total, held_n)
            shown = sum(cell[0] for cell in by_region.values())
            if shown > count_total:
                over.append(f"{year} ({shown:,} against {count_total:,})")
            complete = sum(1 for s in status.values() if s == "complete")
            if fine:
                for reg, cell in by_region.items():
                    if cell[0] == 0:
                        continue
                    f = goals[("fine", reg, key, year)]
                    done = sum(
                        1 for k, s in status.items() if place.get(k, "") == reg and s == "complete" and fine_ok.get(k)
                    )
                    f[0] += cell[0]
                    f[1] += done
                    f[2] += cell[2]
                    f[3] += cell[3]
                    f[4] += cell[4]
                    f[6] += cell[1] - done
            if unit == "district":
                for reg, cell in by_region.items():
                    if cell[0] == 0:
                        continue
                    s = goals[("shapes", reg, key, year)]
                    mine = [k for k in status if place.get(k, "") == reg]
                    s[0] += cell[0]
                    s[1] += sum(1 for k in mine if shape.get(k) == "complete")
                    s[10] += sum(1 for k in mine if shape.get(k) == "unshaped")
                    s[12] += cell[0] - sum(1 for k in mine if shape.get(k) in ("complete", "unshaped"))
            elections_out.append(
                {
                    "goal": goal,
                    "level": key,
                    "election": slot,
                    "expected": n,
                    "held": held_n,
                    "complete": complete,
                    "bare": sum(1 for s in status.values() if s == "bare"),
                    "totals": sum(1 for s in status.values() if s == "totals")
                    + (max(0, count_total - held_n) if coarse_only else 0),
                    "absent": 0 if coarse_only else max(0, count_total - held_n),
                    "fine": sum(1 for k, s in status.items() if s == "complete" and fine_ok.get(k)) if fine else "",
                    "shaped": sum(1 for v in shape.values() if v == "complete") if unit == "district" else "",
                    "total": total,
                    "source": e.get("source", ""),
                    "day": day.isoformat(),
                }
            )
        if over:
            listed = (
                over[0] if len(over) == 1 else f"{', '.join(over[:-1])}{',' if len(over) > 2 else ''} and {over[-1]}"
            )
            notes.append(
                {
                    "goal": goal,
                    "level": key,
                    "from": None,
                    "to": None,
                    "note": "In some years the archive holds more races than the official count lists, so those years' "
                    f"totals use the archive's own count: {listed}.",
                }
            )
    return goals, elections_out, notes


def place_of(cc: str, data: dict, area: str | None, election_iso: str | None) -> str:
    if area is None:
        return region(cc, None, None, None, election_iso)
    scheme, code, parent = data["places"].get(area, (None, None, None))
    return region(cc, scheme, code, parent, election_iso)


def build(conn) -> tuple[list[tuple], list[tuple]]:
    goal_rows: list[tuple] = []
    polity_rows: list[tuple] = [("us", *summary(conn, "us"), "{}", "[]", "[]", "{}")]
    for cc in countries():
        data = measure(conn, cc)
        goals, _, notes = tally(cc, data)
        for (goal, reg, level, year), values in goals.items():
            goal_rows.append((cc, goal, reg, level, year, *values))
        spec = data["spec"]
        polity_rows.append(
            (
                cc,
                *summary(conn, cc),
                json.dumps(data["labels"], ensure_ascii=False),
                json.dumps(
                    [
                        {
                            "key": lv["key"],
                            "label": lv["label"],
                            "goal": lv["goal"],
                            "fine": bool(lv.get("fine")),
                            "unit": lv.get("unit", "district"),
                            "note": lv.get("note"),
                        }
                        for lv in spec["levels"]
                    ]
                ),
                json.dumps(notes, ensure_ascii=False),
                json.dumps(spec["sources"], ensure_ascii=False),
            )
        )
    return goal_rows, polity_rows


def write(conn, goal_rows: list[tuple], polity_rows: list[tuple]) -> None:
    conn.execute("delete from coverage_goal where polity <> 'us'")
    conn.execute("delete from coverage_polity")
    with conn.cursor().copy(
        "copy coverage_goal (polity, goal, state, level, year, total, complete, absent, bare, totals, partial, coarse,"
        " turnout, precincts, shaped, unshaped, units, undrawn, icpsr) from stdin"
    ) as copy:
        for row in goal_rows:
            copy.write_row(row)
    with conn.cursor().copy(
        "copy coverage_polity (polity, first, last, elections, contests, finest, finest_noun, regions, levels, notes,"
        " sources) from stdin"
    ) as copy:
        for row in polity_rows:
            copy.write_row(row)


def report(cc: str) -> str:
    started = time.monotonic()
    with connect() as conn:
        conn.execute(coverage.CACHE_SQL)
        data = measure(conn, cc)
        data["first"], data["last"], data["elections"], data["contests"], _, _ = summary(conn, cc)
    goals, elections, notes = tally(cc, data)
    folder = ROOT / ".agents" / "spec" / "coverage"
    folder.mkdir(exist_ok=True)
    with (folder / f"{cc}-coverage.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(elections[0]) if elections else ["goal"])
        writer.writeheader()
        writer.writerows(elections)
    (folder / f"{cc}-coverage.md").write_text(markdown(cc, data, goals, elections, notes))
    return f"{cc}: {len(elections)} elections, {len(goals)} goal rows in {time.monotonic() - started:.0f}s"


def markdown(cc: str, data: dict, goals: dict, elections: list[dict], notes: list[dict]) -> str:
    spec = data["spec"]
    sums: dict[str, list[int]] = defaultdict(lambda: [0] * 14)
    for (goal, _, _, _), values in goals.items():
        for i, v in enumerate(values):
            sums[goal][i] += v
    lines = [
        f"# {spec['name']} coverage",
        "",
        f"Generated by `ea coverage {cc}` on {date.today().isoformat()} from the database and the cited denominators in "
        f"`pipelines/ea/denominators/{cc}.json`. The archive holds {data['elections']:,} elections and "
        f"{data['contests']:,} contests for {spec['name']}"
        + (f", {data['first']}–{data['last']}" if data["first"] is not None else "")
        + ".",
        "",
        "## Goals",
        "",
        coverage.table(
            ["Goal", "Expected", "Meets it", "Share", "Totals only", "No vote counts", "Not held"],
            [
                [g, f"{v[0]:,}", f"{v[1]:,}", coverage.pct(v[1], v[0]), f"{v[4]:,}", f"{v[3]:,}", f"{v[2]:,}"]
                for g in GOALS
                if (v := sums.get(g)) is not None
            ],
        ),
    ]
    for level in spec["levels"]:
        mine = [e for e in elections if e["level"] == level["key"]]
        if not mine:
            continue
        lines += [
            "",
            f"## {level['label']} ({level['goal']})",
            "",
            coverage.table(
                [
                    "Election",
                    "Expected",
                    "Held",
                    "Meets it",
                    "Totals only",
                    "No vote counts",
                    "Not held",
                    "Finer",
                    "Shaped",
                ],
                [
                    [
                        e["election"],
                        e["expected"],
                        e["held"],
                        e["complete"],
                        e["totals"],
                        e["bare"],
                        e["absent"],
                        e["fine"],
                        e["shaped"],
                    ]
                    for e in mine
                ],
            ),
        ]
    if notes:
        lines += ["", "## Not counted", ""]
        for n in notes:
            span = "" if n["from"] is None else f"{n['from']}" if n["from"] == n["to"] else f"{n['from']}–{n['to']}"
            held = f" (archive holds {n['held']})" if n.get("held") else ""
            lines.append(f"- {n['level']} {span}{held}: {n['note']}".replace("  ", " "))
    lines += ["", "## Sources", ""]
    for key, s in spec["sources"].items():
        lines.append(f"- `{key}`: {s.get('publisher', '')}, {s['title']}, <{s['url']}>")
    return "\n".join(lines) + "\n"


WORLD_SQL = """
select polity, level, year, sum(total)::int, sum(complete)::int, sum(absent)::int, sum(bare)::int
from coverage_goal
where polity <> 'us' and goal in ('national', 'subnational', 'local', 'referendums', 'ep') and year < %s
group by 1, 2, 3 having sum(total) > sum(complete)
order by 1, 2, 3
"""

EFFORT = {
    "series": "one unit for the whole series",
    "election": "one unit per election",
    "scan": "three units per election",
}


def world() -> str:
    leads = json.loads((Path(__file__).parent / "world_leads.json").read_text())
    with connect() as conn:
        rows = list(conn.execute(WORLD_SQL, [date.today().year]))
    specs = {cc: load(cc) for cc in countries()}
    spans: dict[tuple, dict] = {}
    for cc, level, year, total, complete, absent, bare in rows:
        found = next(
            (
                (i, lead)
                for i, lead in enumerate(leads.get(cc, {}).get(level, []))
                if lead["from"] <= year <= lead["to"]
            ),
            (None, None),
        )
        index, lead = found
        span = spans.setdefault(
            (cc, level, index if lead is not None else -year),
            {"cc": cc, "level": level, "lead": lead, "years": [], "missing": 0, "absent": 0, "bare": 0},
        )
        span["years"].append(year)
        span["missing"] += total - complete
        span["absent"] += absent
        span["bare"] += bare
    labels = {(cc, lv["key"]): lv.get("label", lv["key"]) for cc, spec in specs.items() for lv in spec["levels"]}
    sourced, unsourced = [], []
    for span in spans.values():
        lead = span["lead"]
        if lead is None:
            unsourced.append(span)
            continue
        kind = lead["effort"]
        span["effort"] = 1 if kind == "series" else len(span["years"]) * (3 if kind == "scan" else 1)
        span["per"] = span["missing"] / span["effort"]
        sourced.append(span)
    sourced.sort(key=lambda s: (-s["per"], -s["missing"]))

    def years(span: dict) -> str:
        first, last = min(span["years"]), max(span["years"])
        return str(first) if first == last else f"{first}–{last}"

    def entry(rank: int, span: dict) -> list[str]:
        lead = span["lead"]
        name = specs[span["cc"]]["name"]
        return [
            f"### {rank}. {name}, {labels.get((span['cc'], span['level']), span['level'])}, {years(span)}",
            "",
            (
                f"- Missing: {span['missing']:,} contests in {len(span['years'])} election years"
                f" ({span['absent']:,} not in the archive at all, {span['bare']:,} held without results)."
            ),
            f"- Source: [{lead['title']}]({lead['url']}), {lead['publisher']}.",
            f"- Format: {lead['format']}. Grain: {lead['grain']}.",
            f"- Effort: {span['effort']} ({EFFORT[lead['effort']]}); {span['per']:,.1f} contests per unit.",
            *([f"- Note: {lead['note']}"] if lead.get("note") else []),
            "",
        ]

    lines = [
        "# World coverage gaps, ranked by source",
        "",
        (
            f"Generated by `ea coverage world` on {date.today().isoformat()} from `coverage_goal` (the last coverage build)"
            " and the leads in `pipelines/ea/world_leads.json`. Do not edit by hand; edit the leads and rerun."
        ),
        "",
        "## How it is counted",
        "",
        (
            "- A gap is a country, level, and year outside the US whose cited denominator (`pipelines/ea/denominators/`)"
            " counts more contests than the archive holds with results at the level's grain; the current year is left out."
            " Missing is total less complete, split into contests the archive lacks entirely and contests it holds with no"
            " results."
        ),
        (
            "- Each year goes to the first lead in `world_leads.json` whose years cover it, and consecutive years under one"
            " lead form a span. Effort units: a machine-readable series (CSV, spreadsheet, API, or a fix to an existing"
            " pipeline) is 1 for the whole span; an HTML page or text-layer PDF is 1 per election year; a scanned table read"
            " with the two-reader vision pass (`pipelines.md`, Digitization) is 3 per election year."
        ),
        "- Spans are ranked by missing contests per effort unit. Spans with no known free source are listed last.",
        "",
        "## Start here",
        "",
    ]
    first = [s for s in sourced if s["lead"].get("first")]
    for rank, span in enumerate(first, 1):
        lines += entry(rank, span)
    lines += ["## All spans, ranked", ""]
    for rank, span in enumerate(sourced, 1):
        lines += entry(rank, span)
    lines += ["## No free source known", "", "| Country | Level | Years | Missing |", "| --- | --- | --- | --- |"]
    for span in sorted(unsourced, key=lambda s: -s["missing"]):
        lines.append(
            f"| {specs[span['cc']]['name']} | {labels.get((span['cc'], span['level']), span['level'])}"
            f" | {', '.join(str(y) for y in sorted(span['years']))} | {span['missing']:,} |"
        )
    (ROOT / ".agents" / "spec" / "world-gaps.md").write_text("\n".join(lines) + "\n")
    return f"world-gaps.md: {len(sourced)} sourced spans, {len(unsourced)} unsourced years"
