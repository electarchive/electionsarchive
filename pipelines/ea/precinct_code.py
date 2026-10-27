import re
import unicodedata
from datetime import date

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
    for unit, area, relation, parent in conn.execute(
        """select code, area_id::text, area_relation::text, parent_id::text from reporting_unit
           where scheme_code = 'us.precinct' and code = any(%s)""",
        [wanted],
    ).fetchall():
        if unit in held:
            raise ValueError(f"us.precinct {unit} has more than one row; merge them before staging it for the year")
        held[unit] = (area, relation, parent)
    counties = {
        county: (str(unit), str(area))
        for county, unit, area in conn.execute(
            "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
        ).fetchall()
    }
    rows = []
    for unit in wanted:
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
