from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.az_house_1992"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = DATA / "inbox" / "manual" / "MF-278" / "35971.pdf"
URL = "https://azmemory.azlibrary.gov/assets/downloadwiz/1815181"
PUBLISHER = "Arizona Secretary of State"
TITLE = "State of Arizona Official Canvass, General Election, November 3, 1992"


def fetch() -> None:
    raw.store(
        SOURCE,
        URL,
        FILE.read_bytes(),
        suffix=".pdf",
        retrieved_at=datetime(2026, 10, 29, tzinfo=UTC),
        media_type="application/pdf",
    )


def parse() -> None:
    frame = pl.read_csv(Path(__file__).with_name("az_house_1992.tsv"), separator="\t", schema_overrides={"fips": pl.String})
    print(frame.group_by("district", "surname").agg(pl.col("votes").sum(), pl.col("fips").n_unique().alias("counties")).sort("district", "surname"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arizona's 1992 U.S. House general elections by county from page 3 of the Secretary of State's official canvass",
            polity="US-AZ",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://azmemory.azlibrary.gov/nodes/view/102689",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        for district in frame["district"].unique().sort():
            found = conn.execute(
                """select c.id, rs.election_id, rs.k from contest c
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s
                   join result_set rs on rs.contest_id = c.id and rs.is_default""",
                [f"1992-11-03:AZ:house:{district}"],
            ).fetchall()
            if len(found) != 1:
                raise ValueError(f"district {district}: {len(found)} contests matched")
            contest, election, held = found[0]
            options = conn.execute("select id, lower(ballot_label) from contest_option where contest_id = %s", [contest]).fetchall()
            held_totals = dict(
                conn.execute(
                    "select o.id, sum(v.value) from result_value v join contest_option o on o.k = v.option_k where v.result_set_k = %s and v.measure = 1 group by 1",
                    [held],
                ).fetchall()
            )
            part = frame.filter(pl.col("district") == district)
            picked = {}
            for surname in part["surname"].unique():
                matches = [option for option, label in options if surname.lower() in label]
                if len(matches) != 1:
                    raise ValueError(f"district {district}: {len(matches)} options for {surname}")
                picked[surname] = matches[0]
            counted = {picked[name]: votes for name, votes in part.group_by("surname").agg(pl.col("votes").sum()).iter_rows()}
            if counted != {option: int(total) for option, total in held_totals.items()}:
                raise ValueError(f"district {district}: {counted} against {held_totals}")
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
            for surname, fips, votes in part.select("surname", "fips", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.county", fips),
                    option_id=picked[surname],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
