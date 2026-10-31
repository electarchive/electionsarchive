from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import connect

SOURCE = "us.oh_house_2020_14"
VERSION = "1"
KEY = "2020-11-03:OH:house:14"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's 2020 14th District general by county from the MIT Election Data and Science Lab, as the "
            "default over the Federal Election Commission's figure",
            polity="US",
        )
        source = cs.source(
            "MIT Election Data and Science Lab", "U.S. House of Representatives Precinct-Level Returns 2020"
        )
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(raw.parsed("us.medsl") / "congress.parquet")
            .filter(pl.col("state") == "OH", pl.col("year") == 2020, pl.col("district") == 14)
            .with_columns(source=pl.lit("OH")),
            {"OH": source},
            authoritative="Source: the Ohio Secretary of State's certified county totals, which these returns match.",
            declared={
                KEY: (
                    "DAVID P JOYCE",
                    (
                        "The Federal Election Commission prints Joyce's total as 233,864, one digit off the 238,864 that "
                        "the Ohio Secretary of State certified and these county returns add up to."
                    ),
                )
            },
        )
        columns = (
            "id",
            "election_id",
            "contest_id",
            "stage",
            "kind",
            "status",
            "source_id",
            "is_default",
            "completeness",
        )
        for row in conn.execute(
            f"""select {", ".join(f"r.{column}" for column in columns)} from external_id x
                join result_set r on r.contest_id = x.entity_id and r.is_default and r.status = 'active'
                where x.scheme = 'us.contest' and x.value = %s""",
            [KEY],
        ).fetchall():
            cs.add("result_set", **(dict(zip(columns, row, strict=True)) | {"is_default": False}))
        return cs.write()
