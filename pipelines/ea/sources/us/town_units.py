import re
from collections import Counter
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.town_units"
VERSION = "4"
STATES = {"MA": "25", "CT": "09", "RI": "44", "VT": "50", "NH": "33", "ME": "23"}
DROPPED = "The precinct's code names its county, not its town; the town is read from the precinct's own name."


def fold(text: str, strict: bool) -> str:
    text = re.sub(r"^CITY OF\s+", "", re.sub(r",\s*[A-Z]{2}$", "", text.strip().upper()))
    text = re.sub(
        r"^([NSEW])\.\s*", lambda match: {"N": "NORTH ", "S": "SOUTH ", "E": "EAST ", "W": "WEST "}[match[1]], text
    )
    text = re.sub(r"\s+PLT$", " PLANTATION", text)
    if not strict:
        text = re.sub(r"\s+(TOWN|CITY|PLANTATION)$", "", text)
    return re.sub(r"[^A-Z0-9]", "", text.replace("SAINT", "ST"))


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New England precincts placed in their town or city: each precinct unit whose code names its jurisdiction "
            "becomes a member of the town (county subdivision) or city area of that name, so town pages roll up state and national results",
            polity="US",
        )
        towns: dict[tuple[bool, str, str], set[str]] = {}
        for code, area, text in conn.execute(
            """select c.code, a.id::text, n.text from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.kind = 'official' and n.is_preferred
               where (a.scheme_code = 'us.town' or a.scheme_code = 'us.place') and left(c.code, 2) = any(%s)""",
            [list(STATES.values())],
        ).fetchall():
            scope = code[:5] if len(code) == 10 else code[:2]
            for strict in (True, False):
                towns.setdefault((strict, scope, fold(text, strict)), set()).add(area)
        counties = {
            code: fold(re.sub(r"\s+County$", "", text), True)
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and left(u.code, 2) = any(%s)""",
                [list(STATES.values())],
            ).fetchall()
        }
        units = conn.execute(
            """select u.id::text, u.code, lower(u.valid), upper(u.valid), p.code, n.text from reporting_unit u
               left join reporting_unit p on p.id = u.parent_id and p.scheme_code = 'us.county'
               left join name n on n.entity_id = u.area_id and n.kind = 'official' and n.is_preferred
                 and u.code ~ '^[0-9]{4}:[A-Z]{2}:[0-9]+$'
               where u.scheme_code = 'us.precinct' and split_part(u.code, ':', 2) = any(%s)
                 and not exists (
                   select from reporting_unit_area x join area a on a.id = x.area_id
                   join changeset c on c.id = x.changeset_id
                   where x.unit_id = u.id and a.scheme_code in ('us.town', 'us.place') and c.pipeline <> %s)""",
            [list(STATES), SOURCE],
        ).fetchall()

        def lead(text: str, county: str) -> set[str]:
            words = [word for word in re.split(r"[\s_/-]+", text) if word]
            for scope in (county, county[:2]):
                for size in range(len(words), 0, -1):
                    for strict in (True, False):
                        if found := towns.get((strict, scope, fold(" ".join(words[:size]), strict))):
                            return found
            return set()

        coextensive = dict(
            conn.execute(
                """with named as (
                     select a.id, a.scheme_code, n.text,
                            (select g.geom from area_geometry g where g.area_id = a.id
                             order by g.valid_from desc nulls last limit 1) as geom
                     from area a join area_code c on c.area_id = a.id
                     join name n on n.entity_id = a.id and n.kind = 'official' and n.is_preferred
                     where a.scheme_code in ('us.town', 'us.place') and left(c.code, 2) = any(%s))
                   select t.id::text, p.id::text from named t join named p on p.text = t.text and p.scheme_code = 'us.place'
                   where t.scheme_code = 'us.town'
                     and st_area(st_intersection(t.geom, p.geom)::geography) >= 0.99 * st_area(t.geom::geography)
                     and st_area(st_intersection(t.geom, p.geom)::geography) >= 0.99 * st_area(p.geom::geography)""",
                [list(STATES.values())],
            ).fetchall()
        )
        print(f"{len(coextensive)} towns coextensive with a city")
        coextensive |= {city: town for town, city in coextensive.items()}
        named: Counter[tuple[str, str]] = Counter()
        sized: Counter[tuple[str, str]] = Counter()
        for _, code, *_ in units:
            parts = code.split(":")
            if len(parts) > 4:
                sized[(parts[0], parts[1])] += 1
                named[(parts[0], parts[1])] += fold(parts[3], True) == counties.get(parts[2])
        coarse = {file for file, count in sized.items() if named[file] > count / 4}
        print("files naming the county, not the town:", sorted(coarse))
        rows = []
        missed: Counter[str] = Counter()
        for unit, code, start, end, parent, label in units:
            state, *parts = code.split(":")[1:]
            found: set[str] = set()
            if len(parts) <= 1:
                if parent is not None and label is not None:
                    found = lead(
                        re.sub(r"^(Town|City) Of\s+", "", re.sub(r",[^,]*County$", "", label), flags=re.IGNORECASE),
                        parent,
                    )
            else:
                county, jurisdiction, *rest = parts
                precinct = ":".join(rest)
                if (
                    jurisdiction == ""
                    or (code.split(":")[0], state) in coarse
                    and fold(jurisdiction, True) == counties.get(county)
                ):
                    found = lead(precinct, county)
                    if not found and re.match(r"(?i)\s*(DISTRICT|WARD|PRECINCT)?\s*\d", precinct) is None:
                        jurisdiction = ""
                if not found and jurisdiction:
                    for strict in (True, False):
                        key = fold(jurisdiction, strict)
                        found = (
                            found
                            or towns.get((strict, county, key))
                            or towns.get((strict, STATES[state], key))
                            or set()
                        )
            if len(found) != 1:
                missed[f"{state} {code}"] += 1
                continue
            rows.append((unit, next(iter(found)), start, end))
            if next(iter(found)) in coextensive:
                rows.append((unit, coextensive[next(iter(found))], start, end))
        placed = {(unit, area) for unit, area, _, _ in rows}
        stale = [
            (unit, area)
            for unit, area in conn.execute(
                """select x.unit_id::text, x.area_id::text from reporting_unit_area x
                   join changeset c on c.id = x.changeset_id where c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
            if (unit, area) not in placed
        ]
        print(f"{len(stale)} earlier placements dropped")
        for unit, area in stale:
            cs.add("unit_area_drop", unit_id=unit, area_id=area, reason=DROPPED)
        print(f"{len(rows)} of {len(units)} units placed")
        print("unplaced by state:", Counter(name.split()[0] for name in missed.elements()))
        print("unplaced sample:", list(missed)[:: max(1, len(missed) // 40)])
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                rows,
                schema={"unit_id": pl.String, "area_id": pl.String, "valid_from": pl.Date, "valid_to": pl.Date},
                orient="row",
            ),
        )
        return cs.write()
