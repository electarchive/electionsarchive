from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl
from ea.store import connect

SOURCE = "us.vt_early"
VERSION = "2"
TENANT, PUBLISHER, SITE = elstats.TENANTS["VT"]


def emit() -> Path:
    frame = pl.read_parquet(elstats.PARSED / "counties.parquet").filter(
        (pl.col("state") == "VT") & pl.col("chamber").str.starts_with("house") & (pl.col("year") < elstats.FIRST)
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Vermont's House results by county, 1824 to 1978, summed from the Secretary of State's town returns",
            polity="US",
        )
        sources = {"VT": cs.source(PUBLISHER, elstats.TITLE)}
        cs.add(
            "source",
            id=sources["VT"],
            kind="official_body",
            publisher=PUBLISHER,
            title=elstats.TITLE,
            url=SITE,
            license="Public record",
        )
        for year in frame["year"].unique().sort():
            cs.artifact(raw.cached(elstats.SOURCE, elstats.url(TENANT, year)), sources["VT"])
        held = {
            (value[:7], int(value.split(":")[3]))
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.elstats.result_set' and value ~ ':VT:house:[0-9]+:county$'"
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.struct(pl.col("day").dt.strftime("%Y-%m").alias("m"), "district").map_elements(
                lambda row: (row["m"], row["district"]) in held, return_dtype=pl.Boolean
            )
        )
        named = pl.struct(pl.col("day").dt.strftime("%Y-%m").alias("m"), "district").map_elements(
            lambda row: (
                (row["m"], row["district"])
                in {("1852-09", 3), ("1856-09", 1), ("1860-09", 2), ("1870-09", 1), ("1880-11", 3)}
            ),
            return_dtype=pl.Boolean,
        )
        authoritative = "Vermont's Secretary of State publishes the town returns these sums come from."
        medsl.attach(cs, conn, frame.filter(~named), sources, authoritative=authoritative)
        medsl.attach(cs, conn, frame.filter(named), sources, authoritative=authoritative, extend=True)
        return cs.write()
