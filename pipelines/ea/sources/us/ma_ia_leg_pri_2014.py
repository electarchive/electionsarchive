from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl, state_primaries
from ea.store import DATA, connect

SOURCE = "us.ma_ia_leg_pri_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = {"MA": date(2014, 9, 9), "IA": date(2014, 6, 3)}
SUFFIXES = {"DEM": "dp", "REP": "rp"}


def fetch() -> None:
    pass


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.read_parquet(state_primaries.PARSED / "lines.parquet").filter(
        pl.any_horizontal((pl.col("state") == state) & (pl.col("day") == day) for state, day in DAYS.items()),
        pl.col("office").is_in(["house", "senate"]),
    ).select(
        year=pl.lit(2014, pl.Int32),
        chamber=pl.concat_str("office", "code", pl.col("party").replace_strict(SUFFIXES), separator=":"),
        day="day",
        special=pl.lit(False),
        state="state",
        district=pl.lit(None, pl.Int16),
        code="unit",
        kind=pl.when(pl.col("scheme") == "us.town").then(pl.lit("town")).otherwise(pl.lit("county")),
        candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise(pl.col("choice").str.to_uppercase()),
        measure=pl.lit(1, pl.Int16),
        writein="writein",
        votes="votes",
        source="state",
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Massachusetts's 2014 State House and Senate primaries by town from the Secretary of the "
            "Commonwealth's election statistics, as the default over Klarner's district totals, and Iowa's 2014 "
            "legislative primaries by county from the Secretary of State's precinct vote totals",
            polity="US",
        )
        sources = {state: cs.source(*state_primaries.STATES[state][2:4]) for state in DAYS}
        if cs.minted & set(sources.values()):
            raise ValueError("the Massachusetts and Iowa sources must already exist")
        for office in state_primaries.ELSTATS["MA"]:
            listing = raw.cached(state_primaries.SOURCE, state_primaries.search("MA", office, 2014))
            cs.artifact(listing, sources["MA"])
            empty = set(state_primaries.MA_EMPTY.findall(listing.path.read_text()))
            for election, _, _, stage in elstats.MA_ROW.findall(listing.path.read_text()):
                if election not in empty and "Special" not in stage:
                    download = raw.cached(state_primaries.SOURCE, state_primaries.MA_DOWNLOAD.format(election))
                    cs.artifact(download, sources["MA"])
        for (year, _), url in state_primaries.iowa().items():
            if year == 2014:
                cs.artifact(raw.cached(state_primaries.SOURCE, url), sources["IA"])
        medsl.attach(
            cs,
            conn,
            frame.filter(pl.col("state") == "MA"),
            sources,
            grain="town",
            extend=True,
            authoritative="Source: the Secretary of the Commonwealth's election statistics.",
            empty=True,
        )
        medsl.attach(cs, conn, frame.filter(pl.col("state") == "IA"), sources, strict=True)
        return cs.write()
