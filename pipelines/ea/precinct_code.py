import re
import unicodedata
from datetime import date
from uuid import UUID

import polars as pl
from psycopg import Connection

from ea.changeset import Changeset


def normalize(precinct: str) -> str:
    plain = unicodedata.normalize("NFKD", precinct).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", " ", plain.upper()).strip() or "UNNAMED"


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
        .str.strip_chars()
        .replace("", "UNNAMED"),
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


def links(conn: Connection, moved: pl.DataFrame, sets: list[str]) -> tuple[pl.DataFrame, pl.DataFrame]:
    rows = pl.DataFrame(
        conn.execute(
            """select m.unit_id::text, m.area_id::text, m.valid_from, m.valid_from_precision::text, m.valid_to,
                      m.valid_to_precision::text, m.approximate, m.partial, m.method::text,
                      exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                              join result_set rs on rs.k = v.result_set_k
                              where u.id = m.unit_id and rs.id <> all(%s::uuid[]))
               from reporting_unit_area m where m.unit_id = any(%s::uuid[])""",
            [sets, moved["unit_id"].unique().to_list()],
        ).fetchall(),
        schema={
            "unit_id": pl.String,
            "area_id": pl.String,
            "valid_from": pl.Date,
            "valid_from_precision": pl.String,
            "valid_to": pl.Date,
            "valid_to_precision": pl.String,
            "approximate": pl.Boolean,
            "partial": pl.Boolean,
            "method": pl.String,
            "kept": pl.Boolean,
        },
        orient="row",
    )
    copied = (
        rows.join(moved.select("unit_id", "into").unique(), on="unit_id")
        .with_columns(unit_id="into")
        .drop("into", "kept")
        .unique(["unit_id", "area_id", "valid_from"], keep="first", maintain_order=True)
    )
    tombstoned = pl.DataFrame(
        conn.execute(
            "select unit_id::text, area_id::text, reason from unit_area_drop where unit_id = any(%s::uuid[])",
            [moved["unit_id"].unique().to_list()],
        ).fetchall(),
        schema={"unit_id": pl.String, "area_id": pl.String, "reason": pl.String},
        orient="row",
    )
    dropped = (
        rows.filter(~pl.col("kept"))
        .join(tombstoned, on=["unit_id", "area_id"], how="left")
        .select("unit_id", "area_id", reason=pl.col("reason").fill_null("replaced by its canonical precinct code"))
        .unique(["unit_id", "area_id"])
    )
    print(f"{copied.height} memberships copied onto canonical units, {dropped.height} dropped from old units")
    return copied, dropped


def relink(
    cs: Changeset, conn: Connection, pipeline: str, states: list[str], candidates: list[pl.Expr]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    parts = pl.col("code").str.splitn(":", 5)
    strays = (
        pl.DataFrame(
            conn.execute(
                """select u.id::text, u.code from reporting_unit u join changeset w on w.id = u.changeset_id
                   join entity e on e.id = u.id
                   where w.pipeline = %s and u.scheme_code = 'us.precinct' and e.retired_by is null
                     and u.code !~ '^[0-9]{4}:[A-Z]{2}:[0-9]{5}::[A-Z0-9]+( [A-Z0-9]+)*$'
                     and (cardinality(%s::text[]) = 0 or split_part(u.code, ':', 2) = any(%s::text[]))
                     and exists (select from reporting_unit_area m where m.unit_id = u.id)
                     and not exists (select from result_value v join result_set rs on rs.k = v.result_set_k
                                     join changeset g on g.id = rs.changeset_id
                                     where v.unit_k = u.k and g.pipeline = %s)""",
                [pipeline, states, states, pipeline],
            ).fetchall(),
            schema={"unit_id": pl.String, "code": pl.String},
            orient="row",
        )
        .with_columns(
            year=parts.struct.field("field_0"),
            state=parts.struct.field("field_1"),
            fips=parts.struct.field("field_2"),
            jurisdiction=parts.struct.field("field_3"),
            precinct=parts.struct.field("field_4"),
        )
        .with_columns(candidates=pl.concat_list(candidates).list.drop_nulls().list.unique())
    )
    found = {
        code
        for (code,) in conn.execute(
            """select distinct u.code from reporting_unit u join result_value v on v.unit_k = u.k
               join result_set rs on rs.k = v.result_set_k join changeset g on g.id = rs.changeset_id
               where g.pipeline = %s and u.scheme_code = 'us.precinct' and u.code = any(%s)""",
            [pipeline, strays["candidates"].explode().drop_nulls().unique().to_list()],
        ).fetchall()
    }
    strays = strays.with_columns(
        hits=pl.col("candidates").list.eval(pl.element().filter(pl.element().is_in(list(found))))
    )
    ambiguous = strays.filter(pl.col("hits").list.len() > 1)
    print(
        f"{strays.height} old units with memberships and no values: "
        f"{strays.filter(pl.col('hits').list.len() == 1).height} matched, {ambiguous.height} ambiguous, "
        f"{strays.filter(pl.col('hits').list.len() == 0).height} with no canonical unit"
    )
    for code, hits in ambiguous.select("code", "hits").iter_rows():
        print(f"  ambiguous {code}: {hits}")
    for (code,) in strays.filter(pl.col("hits").list.len() == 0).select("code").head(20).iter_rows():
        print(f"  unmatched {code}")
    return links(
        conn,
        strays.filter(pl.col("hits").list.len() == 1).select(
            "unit_id",
            into=pl.col("hits")
            .list.first()
            .map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String),
        ),
        [],
    )


def remap(
    cs: Changeset, conn: Connection, held: pl.DataFrame
) -> tuple[list[pl.DataFrame], list[pl.DataFrame], pl.DataFrame, pl.DataFrame]:
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
    return (
        *out,
        *links(conn, moving.select("unit_id", into="canonical").filter(pl.col("unit_id") != pl.col("into")), stale),
    )


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
