import re
import unicodedata
from datetime import date
from uuid import UUID

import polars as pl
from psycopg import Connection

from ea.changeset import Changeset


def normalize(precinct: str) -> str:
    plain = unicodedata.normalize("NFKD", precinct).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", " ", plain.upper()).strip()


def code(year: int, state: str, county: str, precinct: str) -> str:
    return f"{year}:{state}:{county}::{normalize(precinct)}"


def expr(year: pl.Expr, state: pl.Expr, county: pl.Expr, precinct: pl.Expr) -> pl.Expr:
    return pl.concat_str(
        year.cast(pl.String),
        pl.lit(":"),
        state,
        pl.lit(":"),
        county,
        pl.lit("::"),
        precinct.str.normalize("NFKD")
        .str.replace_all(r"[^\x00-\x7F]", "")
        .str.to_uppercase()
        .str.replace_all(r"[^A-Z0-9]+", " ")
        .str.strip_chars(),
    )


def units(cs: Changeset, conn: Connection, codes: pl.Series) -> None:
    wanted = sorted(set(codes))
    held: dict[str, tuple[str | None, str, str | None]] = {}
    survivors: dict[str, UUID] = {}
    merged: set[str] = set()
    for id, unit, area, relation, parent in conn.execute(
        """select id, code, area_id::text, area_relation::text, parent_id::text from reporting_unit
           where scheme_code = 'us.precinct' and code = any(%s) order by code, valid_from nulls first, k""",
        [wanted],
    ).fetchall():
        if unit not in survivors:
            survivors[unit] = id
            held[unit] = (area, relation, parent)
            continue
        if (area, relation, parent) != held[unit]:
            raise ValueError(f"us.precinct {unit} has rows with different areas; never merge across a boundary change")
        cs.merge(id, survivors[unit])
        merged.add(unit)
    if merged:
        print(f"{len(merged)} us.precinct codes merged into one unit each; emit again to stage them for the year")
    counties = {
        county: (str(unit), str(area))
        for county, unit, area in conn.execute(
            "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
        ).fetchall()
    }
    rows = []
    for unit in wanted:
        if unit in merged:
            continue
        year, state, county = unit.split(":")[:3]
        area, relation, parent = held.get(unit, (counties[county][1], "within", counties[county][0]))
        rows.append(
            {
                "id": str(cs.unit("us.precinct", unit)),
                "polity_id": str(cs.polity(f"US-{state}")),
                "scheme_code": "us.precinct",
                "code": unit,
                "parent_id": parent,
                "area_relation": relation,
                "area_id": area,
                "valid_from": date(int(year), 1, 1),
                "valid_to": date(int(year) + 1, 1, 1),
            }
        )
    cs.frame("reporting_unit", pl.DataFrame(rows))
