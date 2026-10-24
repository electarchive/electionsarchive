import re
from collections import Counter
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.town_units"
VERSION = "3"
STATES = {"MA": "25", "CT": "09", "RI": "44", "VT": "50", "NH": "33", "ME": "23"}


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
        units = pl.DataFrame(
            conn.execute(
                """select u.id::text, u.code, lower(u.valid), upper(u.valid) from reporting_unit u
                   where u.scheme_code = 'us.precinct' and split_part(u.code, ':', 2) = any(%s)
                     and split_part(u.code, ':', 4) <> ''
                     and not exists (
                       select from reporting_unit_area x join area a on a.id = x.area_id
                       join changeset c on c.id = x.changeset_id
                       where x.unit_id = u.id and a.scheme_code in ('us.town', 'us.place') and c.pipeline <> %s)""",
                [list(STATES), SOURCE],
            ).fetchall(),
            schema={"unit_id": pl.String, "code": pl.String, "valid_from": pl.Date, "valid_to": pl.Date},
            orient="row",
        )
        rows = []
        missed: Counter[str] = Counter()
        for unit, code, start, end in units.iter_rows():
            _, state, county, jurisdiction, *_ = code.split(":")
            found = set()
            for strict in (True, False):
                key = fold(jurisdiction, strict)
                found = found or towns.get((strict, county, key)) or towns.get((strict, STATES[state], key)) or set()
            if len(found) != 1:
                missed[f"{state} {jurisdiction}"] += 1
                continue
            rows.append((unit, next(iter(found)), start, end))
        print(f"{len(rows)} of {units.height} units placed")
        print("unplaced by state:", Counter(name.split()[0] for name in missed.elements()))
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                rows,
                schema={"unit_id": pl.String, "area_id": pl.String, "valid_from": pl.Date, "valid_to": pl.Date},
                orient="row",
            ),
        )
        return cs.write()
