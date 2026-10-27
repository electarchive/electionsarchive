import re
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import me_sos
from ea.store import connect

SOURCE = "us.me_cd2_2018"
VERSION = "1"
KEY = "2018-11-06:ME:house:2"
DAY = date(2018, 11, 6)


def fetch() -> None:
    pass


def emit() -> Path:
    names = dict(pl.read_parquet(raw.parsed(me_sos.SOURCE) / "towns.parquet").select("code", "display").iter_rows())
    tallies = pl.read_parquet(raw.parsed(me_sos.SOURCE) / "tallies.parquet").filter(pl.col("key") == KEY)

    def code(precinct: str, town: str | None) -> str:
        if town is None:
            return "other"
        name = names[town].upper()
        ward = re.search(r"\s+(?:W|Ward\s*)(\d+)$", precinct)
        part = f"Ward {ward[1]}" if ward else "All" if re.search(r"\sAll(?:/|$)", precinct) else name
        return f"2018:ME:{town[:5]}:{name}:{part}"

    rows = (
        tallies.with_columns(
            code=pl.struct("precinct", "town").map_elements(
                lambda row: code(row["precinct"], row["town"]), return_dtype=pl.String
            )
        )
        .group_by("code", "round", "label")
        .agg(pl.col("votes").sum())
    )
    first = rows.filter(pl.col("round") == 1)
    frame = pl.concat(
        [
            rows,
            first.group_by("code")
            .agg(pl.col("votes").sum())
            .select("code", round=pl.lit(0, pl.Int16), label=pl.lit("ballots"), votes="votes"),
            first.filter(pl.col("label").is_not_null())
            .group_by("code")
            .agg(pl.col("votes").sum())
            .select("code", round=pl.lit(1, pl.Int16), label=pl.lit("valid"), votes="votes"),
        ]
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's 2018 U.S. House election in the 2nd District by ward and municipality, each ranked-choice round from the Secretary of State's cast vote records",
            polity="US-ME",
        )
        contest, election = conn.execute(
            "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
            [KEY],
        ).fetchone()
        (source,) = conn.execute(
            """select r.source_id from result_set r join changeset c on c.id = r.changeset_id
               where r.contest_id = %s and r.status = 'active' and r.is_default and c.pipeline = %s""",
            [contest, me_sos.SOURCE],
        ).fetchone()
        options = {
            surname: option
            for option, label in conn.execute(
                "select id, ballot_label from contest_option where contest_id = %s", [contest]
            ).fetchall()
            for surname in set(rows["label"].drop_nulls())
            if label.endswith(surname)
        }
        if len(options) != rows["label"].drop_nulls().n_unique():
            raise ValueError(f"options {options}")
        counties = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '23%'"
            ).fetchall()
        )
        areas = dict(
            conn.execute(
                "select c.code, c.area_id from area_code c join area a on a.id = c.area_id where a.scheme_code = 'us.county' and c.code like '23%'"
            ).fetchall()
        )
        state = cs.polity("US-ME")
        units = {"other": cs.unit("us.aggregate", me_sos.OTHER)}
        if units["other"] in cs.minted:
            raise ValueError(f"{me_sos.OTHER} must already exist")
        for precinct in sorted(set(frame["code"]) - {"other"}):
            units[precinct] = cs.unit("us.precinct", precinct)
            if units[precinct] in cs.minted:
                county = precinct.split(":")[2]
                cs.add(
                    "reporting_unit",
                    id=units[precinct],
                    polity_id=state,
                    scheme_code="us.precinct",
                    code=precinct,
                    parent_id=counties[county],
                    area_relation="within",
                    area_id=areas[county],
                    kind="standard",
                    valid_from=DAY,
                    valid_to=DAY + timedelta(days=1),
                )
        result_set = cs.key(f"{SOURCE}.result_set", KEY)
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=False,
            completeness=1,
        )
        cs.add(
            "note",
            entity_id=result_set,
            kind="methodology",
            body_md="Counted from the Secretary of State's cast vote records, which name each ballot's precinct: wards in the cities that vote by ward, a city-wide pool where the records say All, and the municipality elsewhere. Ballots whose precinct names no municipality are grouped as the rest of the district. Each round's totals equal the Secretary of State's ranked-choice tabulation.",
        )
        cs.frame(
            "result_value",
            frame.select(
                result_set_id=pl.lit(str(result_set)),
                unit_id=pl.col("code").replace_strict({code: str(unit) for code, unit in units.items()}),
                option_id=pl.col("label").replace_strict(
                    {label: str(option) for label, option in options.items()}, default=None
                ),
                channel=pl.lit(0, pl.Int16),
                measure=pl.when(pl.col("label").is_null())
                .then(21)
                .otherwise(pl.col("label").replace_strict({"ballots": 3, "valid": 4}, default=1))
                .cast(pl.Int16),
                count_round=pl.col("round").cast(pl.Int16),
                flags=pl.lit(1, pl.Int16),
                value="votes",
            ),
        )
        return cs.write()
