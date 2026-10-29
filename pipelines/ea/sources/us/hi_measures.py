from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import hi_county, hi_statewide
from ea.store import DATA, connect

SOURCE = "us.hi_measures"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE


def parse() -> None:
    rows = []
    for year in hi_statewide.DAYS:
        for county, code in hi_county.COUNTIES.items():
            found = 0
            for _, entries in hi_county.blocks(raw.cached(hi_county.SOURCE, hi_county.BASE.format(year=year, county=county)).path):
                votes = {name.upper(): count for name, count in entries if name.upper() in ("YES", "NO")}
                if len(votes) == 2:
                    rows.extend((year, found, code, answer, count) for answer, count in votes.items())
                    found += 1
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["year", "position", "county", "answer", "votes"], orient="row").write_parquet(
        PARSED / "questions.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "questions.parquet")
    totals = frame.group_by("year", "position", "answer").agg(pl.col("votes").sum())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Hawaii's statewide constitutional questions of 2000 and 2004–2022 by county from the Office of Elections' county summary reports",
            polity="US-HI",
        )
        source = cs.source(hi_county.PUBLISHER, hi_county.TITLE)
        if source in cs.minted:
            raise ValueError("the county summary source is not published")
        for year in hi_statewide.DAYS:
            for county in hi_county.COUNTIES:
                cs.artifact(raw.cached(hi_county.SOURCE, hi_county.BASE.format(year=year, county=county)), source)
        held = {
            (row[0], row[1], row[2]): row[3:]
            for row in conn.execute(
                """select extract(year from e.date_from)::int, t.yes, t.no, c.id, rs.election_id, t.k from contest c
                   join election e on e.id = c.election_id
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value like %s
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join lateral (select sum(v.value) filter (where upper(o.ballot_label) = 'YES') as yes,
                                        sum(v.value) filter (where upper(o.ballot_label) = 'NO') as no, 1 as k
                                 from result_value v join contest_option o on o.k = v.option_k
                                 where v.result_set_k = rs.k and v.measure = 1) t on true
                   where c.kind = 'ballot_question' and t.yes is not null""",
                ["%:HI:question:%"],
            ).fetchall()
        }
        taken: set[object] = set()
        for year, position in frame.select("year", "position").unique().sort("year", "position").iter_rows():
            sums = {a: v for a, v in totals.filter((pl.col("year") == year) & (pl.col("position") == position)).select("answer", "votes").iter_rows()}
            match = held.get((year, sums["YES"], sums["NO"]))
            if match is None or match[0] in taken:
                continue
            contest, election, _ = match
            taken.add(contest)
            options = dict(
                conn.execute("select upper(ballot_label), id from contest_option where contest_id = %s", [contest]).fetchall()
            )
            result_set = cs.key(f"{SOURCE}.result_set", f"{contest}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                source_id=source,
                status="active",
                is_default=False,
                completeness=1.0,
            )
            for county, answer, votes in frame.filter((pl.col("year") == year) & (pl.col("position") == position)).select("county", "answer", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.county", county),
                    option_id=options[answer],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        print(len(taken), "questions matched")
        return cs.write()
