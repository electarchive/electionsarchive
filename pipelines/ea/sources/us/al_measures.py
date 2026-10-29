import re
import time
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import al_precincts
from ea.store import DATA, connect

SOURCE = "us.al_measures"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    date(2014, 11, 4): al_precincts.BASE + "2014General-precinctLevel.zip",
    date(2016, 3, 1): al_precincts.BASE + "2016-Primary-PrecinctLevel.zip",
    date(2020, 3, 3): "https://www.sos.alabama.gov/sites/default/files/election-data/2020-06/2020%20Primary%20Precinct%20Results.zip",
}
NUMBERS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".zip")
        time.sleep(1)


def parse() -> None:
    def statewide(title: str) -> tuple[str, int | None] | None:
        name = re.sub(r"\s+", " ", title.upper())
        found = re.match(r"(?:PROPOSED )?(?:STATEWIDE AMENDMENT|STATE AMEND\.?|AMENDMENT NUMBER) ?(?:NUMBER |NO\.? ?)?(\w+)", name)
        if found is None or "LOCAL" in name:
            return None
        return "amendment", NUMBERS.get(found[1]) or int(re.sub(r"\D", "", found[1]) or 0)

    al_precincts.contest = statewide
    al_precincts.SOURCE = SOURCE
    al_precincts.STEM = r"(?i)^20\d\d-(?:general|primary)-|[ _-]*2014 general precinct"
    al_precincts.PARSED = PARSED
    frames = []
    for day, url in FILES.items():
        al_precincts.FILES = [(day, url)]
        al_precincts.parse()
        frames.append(pl.read_parquet(PARSED / "precincts.parquet"))
    (PARSED / "precincts.parquet").unlink()
    pl.concat(frames).filter(pl.col("candidate").str.contains(r"^(?:YES|NO)\b")).with_columns(
        answer=pl.when(pl.col("candidate").str.contains(r"^YES\b")).then(pl.lit("YES")).otherwise(pl.lit("NO"))
    ).group_by(pl.col("day"), pl.col("district").alias("number"), pl.col("county_code").alias("county"), "answer").agg(
        pl.col("votes").sum()
    ).write_parquet(PARSED / "questions.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "questions.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's statewide constitutional amendments of 2014, 2016, and 2020 by county from the Secretary of State's precinct files",
            polity="US-AL",
        )
        source = cs.source(al_precincts.PUBLISHER, al_precincts.TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=al_precincts.PUBLISHER,
                title=al_precincts.TITLE,
                url=al_precincts.LANDING,
                license="Public record",
            )
        for url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        taken = 0
        for day, number in frame.select("day", "number").unique().sort("day", "number").iter_rows():
            found = conn.execute(
                """select c.id, rs.election_id from contest c
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value like %s
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   where c.kind = 'ballot_question'""",
                [f"{day}:AL:question:wikipedia:%" + (f"-amendment-{number}" if day.year == 2014 else "")],
            ).fetchall()
            if len(found) != 1:
                print(day, number, "matched", len(found), "contests, skipped")
                continue
            contest, election = found[0]
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
            for county, answer, votes in (
                frame.filter((pl.col("day") == day) & (pl.col("number") == number))
                .select("county", "answer", "votes")
                .iter_rows()
            ):
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
            taken += 1
        print(taken, "amendments matched")
        return cs.write()
