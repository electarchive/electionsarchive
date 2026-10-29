import html
import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import hi_county, medsl
from ea.store import DATA, connect

SOURCE = "us.hi_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE


def parse() -> None:
    rows = []
    for year, day in hi_county.YEARS.items():
        for county, code in hi_county.COUNTIES.items():
            for header, entries in hi_county.blocks(raw.cached(hi_county.SOURCE, hi_county.BASE.format(year=year, county=county)).path):
                if not re.search(r"Governor and Lieutenant Governor", html.unescape(header), re.IGNORECASE):
                    continue
                for name, votes in entries:
                    found = re.match(r"^\([A-Z]\)\s*([^/]+?)\s*/", html.unescape(name))
                    if found is not None:
                        rows.append((year, "governor", day, False, "HI", None, code, found[1].upper(), 1, False, votes))
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(kind=pl.lit("county"), source=pl.lit("HI"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("year", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("year", "votes").rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Hawaii's 2006, 2010, and 2014 governor generals by county from the Office of Elections' county summary reports",
            polity="US-HI",
        )
        source = cs.source(hi_county.PUBLISHER, hi_county.TITLE)
        if source in cs.minted:
            raise ValueError("the county summary source is not published")
        for year in hi_county.YEARS:
            for county in hi_county.COUNTIES:
                cs.artifact(raw.cached(hi_county.SOURCE, hi_county.BASE.format(year=year, county=county)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"HI": source}, strict=True)
        return cs.write()
