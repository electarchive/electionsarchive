import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ky_statewide, medsl
from ea.store import DATA, connect

SOURCE = "us.ky_gov_2003"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.read_parquet(ky_statewide.PARSED / "governor.parquet").filter(pl.col("year") == 2003).select(
        year="year",
        chamber=pl.lit("governor"),
        day=pl.col("day").str.to_date(),
        special=pl.lit(False),
        state=pl.lit("KY"),
        district=pl.lit(None, pl.Int16),
        code="county",
        kind=pl.lit("county"),
        candidate="candidate",
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        votes="votes",
        source=pl.lit("KY"),
    ).write_parquet(PARSED / "counties.parquet")


def alias() -> str:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kentucky's 2003 governor contest under its election-day key",
            polity="US",
        )
        contest = cs.key("us.contest", "2003:KY:governor")
        cs.add("external_id", entity_id=contest, scheme="us.contest", value="2003-11-04:KY:governor")
        return str(cs.write())


def emit() -> str:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kentucky's 2003 governor by county from the State Board of Elections' official results",
            polity="US-KY",
        )
        source = cs.source(ky_statewide.PUBLISHER, ky_statewide.TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=ky_statewide.PUBLISHER,
            title=ky_statewide.TITLE,
            url="https://elect.ky.gov/results/",
            license="Public record",
        )
        for kind in ("county", "office"):
            cs.artifact(raw.cached(ky_statewide.SOURCE, ky_statewide.address(2003, kind)), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet"),
            {"KY": source},
            strict=True,
            authoritative=ky_statewide.METHOD,
        )
        return str(cs.write())
