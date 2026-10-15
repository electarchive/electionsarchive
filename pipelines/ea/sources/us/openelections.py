from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.openelections"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
GEORGIA = "https://raw.githubusercontent.com/openelections/openelections-data-ga/"
FILES = {
    date(2020, 11, 3): "330d0aa36a4aec2c93920bd93bd4b3a5482fc5aa/2020/20201103__ga__general.csv",
    date(2021, 1, 5): "96ed3beaa91ee59b93d4dd384a1a06d5004552bf/2021/20210105__ga__runoff.csv",
}
OFFICES = {
    "President": "president",
    "U.S. Senate": "senate",
    "U.S. Senate (Special)": "senate:special",
    "U.S. House": "house",
}
CHANNELS = {"election_day_votes": 1, "advanced_votes": 2, "absentee_by_mail_votes": 3, "provisional_votes": 5}
TYPOS = {"Matt Lierberman": "Matt Lieberman"}


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, GEORGIA + path, suffix=".csv")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(
        pl.read_csv(raw.cached(SOURCE, GEORGIA + path).path, infer_schema_length=0)
        .filter(pl.col("office").is_in(list(OFFICES)))
        .select(
            pl.lit(day).alias("day"),
            pl.concat_str(
                pl.lit(f"{day}:GA:"),
                pl.col("office").replace_strict(OFFICES),
                pl.when(pl.col("office") == "U.S. House").then(pl.lit(":") + pl.col("district")).otherwise(pl.lit("")),
            ).alias("contest"),
            "county",
            "precinct",
            pl.col("candidate").replace(TYPOS),
            *(pl.col(column).cast(pl.Int64) for column in CHANNELS),
        )
        for day, path in FILES.items()
    ).write_parquet(PARSED / "georgia.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "georgia.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia's 2020 federal general election and 2021 Senate runoffs by precinct",
            polity="US",
        )
        source = cs.source("OpenElections", "Georgia precinct-level results")
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="OpenElections",
            title="Georgia precinct-level results",
            url="https://github.com/openelections/openelections-data-ga",
            license=None,
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, GEORGIA + path), source)
        cs.add("scheme", code="us.precinct", polity_id=cs.polity("US"), kind="precinct", rank=3, noun="precinct")

        counties: dict[str, tuple[str, UUID, UUID]] = {
            text: (code, area, unit)
            for text, code, area, unit in conn.execute(
                """select n.text, u.code, u.area_id, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '13%'"""
            ).fetchall()
        }
        units: dict[str, UUID] = {}
        georgia = cs.polity("US-GA")
        for county, precinct in frame.select("county", "precinct").unique().sort("county", "precinct").iter_rows():
            fips, area, parent = counties[county]
            code = f"{fips}:{precinct}"
            units[code] = cs.unit("us.precinct", code)
            cs.add(
                "reporting_unit",
                id=units[code],
                polity_id=georgia,
                scheme_code="us.precinct",
                code=code,
                parent_id=parent,
                area_relation="within",
                area_id=area,
                kind="standard",
                valid_from=min(FILES),
                valid_to=date(2021, 1, 6),
            )

        result_sets: dict[str, UUID] = {}
        options: dict[tuple[str, str], UUID] = {}
        for key in frame["contest"].unique().sort():
            contest = cs.key("us.contest", key)
            if contest in cs.minted:
                raise ValueError(f"{key}: the contest must already exist")
            election, official = conn.execute(
                """select c.election_id, sum(v.value) from contest c
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure = 4
                   where c.id = %s group by c.election_id""",
                [contest],
            ).fetchone() or (None, None)
            known = conn.execute(
                """select o.id, o.ballot_label, n.text from contest_option o
                   join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where o.contest_id = %s and o.parent_option_id is null""",
                [contest],
            ).fetchall()
            lines = frame.filter(pl.col("contest") == key)
            for candidate in lines["candidate"].unique():
                words = {word for word in slug(candidate).split("-") if len(word) > 1}
                scores = sorted(
                    (
                        (len(words & {word for text in texts if text for word in slug(text).split("-")}), option)
                        for option, *texts in known
                    ),
                    key=lambda score: score[0],
                    reverse=True,
                )
                if not scores or scores[0][0] == 0 or (len(scores) > 1 and scores[0][0] == scores[1][0]):
                    raise ValueError(f"{key}: {candidate} matches no single option: {scores[:2]}")
                options[(key, candidate)] = scores[0][1]
            counted = lines.select(pl.sum_horizontal(*CHANNELS)).sum().item()
            result_sets[key] = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_sets[key],
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=round(counted / official, 4),
            )

        minted = [key for key, result_set in result_sets.items() if result_set in cs.minted]
        cs.frame(
            "result_value",
            frame.filter(pl.col("contest").is_in(minted))
            .unpivot(list(CHANNELS), index=["contest", "county", "precinct", "candidate"], variable_name="column")
            .select(
                result_set_id=pl.col("contest").replace_strict({key: str(id) for key, id in result_sets.items()}),
                unit_id=pl.concat_str(
                    pl.col("county").replace_strict({county: fips for county, (fips, _, _) in counties.items()}),
                    pl.lit(":"),
                    "precinct",
                ).replace_strict({code: str(id) for code, id in units.items()}),
                option_id=pl.concat_str("contest", pl.lit("\x1f"), "candidate").replace_strict(
                    {f"{key}\x1f{candidate}": str(id) for (key, candidate), id in options.items()}
                ),
                channel=pl.col("column").replace_strict(CHANNELS, return_dtype=pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value="value",
            ),
        )
        return cs.write()
