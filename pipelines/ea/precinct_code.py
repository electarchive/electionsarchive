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


def held(conn: Connection, pipeline: str, states: list[str]) -> pl.DataFrame:
    return pl.DataFrame(
        conn.execute(
            """select rs.id::text, u.id::text, u.code, u.area_id::text, u.area_relation::text, u.parent_id::text
               from result_set rs join changeset g on g.id = rs.changeset_id
               join result_value v on v.result_set_k = rs.k join reporting_unit u on u.k = v.unit_k
               join changeset w on w.id = u.changeset_id
               where g.pipeline = %s and w.pipeline = %s and u.scheme_code = 'us.precinct'
               and (cardinality(%s::text[]) = 0 or split_part(u.code, ':', 2) = any(%s::text[]))
               group by 1, 2, 3, 4, 5, 6""",
            [pipeline, pipeline, states, states],
        ).fetchall(),
        schema=["result_set_id", "unit_id", "code", "area_id", "area_relation", "parent_id"],
        orient="row",
    )


def remap(cs: Changeset, conn: Connection, held: pl.DataFrame) -> tuple[list[pl.DataFrame], list[pl.DataFrame]]:
    stale = held.filter(pl.col("code") != pl.col("canonical"))["result_set_id"].unique().to_list()
    moving = held.filter(pl.col("result_set_id").is_in(stale)).select(
        "result_set_id",
        "unit_id",
        canonical=pl.col("canonical").map_elements(
            lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String
        ),
    )
    grain = ["result_set_id", "unit_id", "option_id", "channel", "measure", "count_round"]
    out: tuple[list[pl.DataFrame], list[pl.DataFrame]] = ([], [])
    for table, columns, schema, merged, into in (
        (
            "result_value",
            "v.flags, v.value",
            {"flags": pl.Int16, "value": pl.Int64},
            [pl.col("flags").max(), pl.col("value").sum()],
            out[0],
        ),
        (
            "value_locator",
            "v.page_id::text, v.source_id::text, v.x0, v.y0, v.x1, v.y1",
            {
                "page_id": pl.String,
                "source_id": pl.String,
                "x0": pl.Float64,
                "y0": pl.Float64,
                "x1": pl.Float64,
                "y1": pl.Float64,
            },
            [pl.all().first()],
            out[1],
        ),
    ):
        facts = (
            pl.DataFrame(
                conn.execute(
                    f"""select rs.id::text, u.id::text, o.id::text, v.channel, v.measure, v.count_round, {columns}
                        from {table} v join result_set rs on rs.k = v.result_set_k
                        join reporting_unit u on u.k = v.unit_k join contest_option o on o.k = v.option_k
                        where rs.id = any(%s::uuid[])""",
                    [stale],
                ).fetchall(),
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    **schema,
                },
                orient="row",
            )
            .join(moving, on=["result_set_id", "unit_id"], how="left")
            .with_columns(unit_id=pl.coalesce("canonical", "unit_id"))
            .drop("canonical")
            .group_by(grain, maintain_order=True)
            .agg(*merged)
        )
        if facts.height:
            into.append(facts)
    print(f"{len(stale)} sets moved onto canonical precinct codes")
    return out


def units(cs: Changeset, conn: Connection, codes: pl.Series, carried: pl.DataFrame | None = None) -> None:
    wanted = sorted(set(codes))
    held: dict[str, tuple[str | None, str, str | None]] = {}
    links: dict[str, tuple[str | None, str, str | None]] = {}
    if carried is not None:
        for unit, area, relation, parent in (
            carried.filter(pl.col("area_relation") == "equals")
            .select("canonical", "area_id", "area_relation", "parent_id")
            .unique()
            .iter_rows()
        ):
            if links.setdefault(unit, (area, relation, parent)) != (area, relation, parent):
                raise ValueError(
                    f"us.precinct {unit} gathers units linked to different shapes; never merge across a boundary change"
                )
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
        area, relation, parent = (
            held.get(unit) or links.get(unit) or (counties[county][1], "within", counties[county][0])
        )
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
