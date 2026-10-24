from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, sd_sos_statewide
from ea.store import connect

SOURCE = "us.sd_sos_governor"
VERSION = "1"


def emit() -> Path:
    frame = (
        pl.read_parquet(sd_sos_statewide.PARSED / "returns.parquet")
        .filter(pl.col("title") == "Governor")
        .select(
            "year",
            chamber=pl.lit("governor"),
            day="day",
            district=pl.lit(None, pl.Int16),
            code="county",
            candidate="candidate",
            special=pl.lit(False),
            writein=pl.lit(False),
            votes="votes",
            state=pl.lit("SD"),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("SD"),
        )
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Dakota's 1990, 1994, and 1998 governor general elections by county from the Secretary of State",
            polity="US-SD",
        )
        source = cs.source(sd_sos_statewide.PUBLISHER, sd_sos_statewide.TITLE)
        for url in sd_sos_statewide.PAGES.values():
            cs.artifact(raw.cached(sd_sos_statewide.SOURCE, url), source)
        medsl.attach(
            cs,
            conn,
            frame,
            {"SD": source},
            authoritative="The Secretary of State's returns page for the general election.",
            extend=True,
        )
        return cs.write()
