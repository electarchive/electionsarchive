from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl, state_primaries
from ea.store import connect

SOURCE = "us.ma_house_pri_2012"
VERSION = "1"
ELECTION = "113027"
KEY = "2012-09-06:MA:house:9:republican"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Massachusetts's 2012 9th District Republican primary by county, the Secretary of the "
            "Commonwealth's certified town results as the default over the FEC's compilation",
            polity="US",
        )
        source = cs.source(elstats.MA_PUBLISHER, elstats.TITLE)
        cs.artifact(raw.cached(state_primaries.SOURCE, state_primaries.MA_DOWNLOAD.format(ELECTION)), source)
        congress = pl.read_parquet(raw.parsed(state_primaries.SOURCE) / "congress.parquet").filter(
            pl.col("source") == "MA",
            pl.col("year") == 2012,
            pl.col("district") == 9,
            pl.col("chamber") == "house:republican",
        )
        medsl.attach(
            cs,
            conn,
            congress,
            {"MA": source},
            authoritative="Source: the Secretary of the Commonwealth's certified town results.",
            declared={
                KEY: (
                    "CHRISTOPHER SHELDON",
                    (
                        "The certified town results add up to 22,013 votes for the two candidates (Sheldon 11,046, Chaprales "
                        "10,967). The Federal Election Commission's compilation counts 21,837 and lists Chaprales at 10,697, "
                        "apparently transposed digits; the state certifies the result, so this set is the default."
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
