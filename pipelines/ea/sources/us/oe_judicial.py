import re
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, oe_statewide, openelections
from ea.store import DATA

SOURCE = "us.oe_judicial"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SINGLE_SEAT = {
    "MN": [2012, 2014],
    "WA": [2014],
    "ND": [2000, 2002, 2004, 2006, 2010, 2012, 2014],
    "NM": [2012],
}
METHOD = (
    "County totals are the sums of the rows in OpenElections' results file for the state and year, which is a "
    "volunteer transcription of the state's official returns. The winner shown is the candidate with the most votes "
    "in those sums."
)


def fetch() -> None:
    openelections.fetch()


def parse() -> None:
    frame, fips = oe_statewide.load()
    frame = frame.filter(
        pl.any_horizontal((pl.col("state") == state) & pl.col("year").is_in(years) for state, years in SINGLE_SEAT.items())
    )
    named = []
    for (office,) in frame.select("office").unique().iter_rows():
        for pattern, title in medsl_statewide.OFFICES:
            if (match := re.fullmatch(pattern, office)) and ("Justice" in title or "Judge" in title):
                post = match.groupdict().get("seat")
                if post and (number := re.fullmatch(r"(?:PLACE|POSITION|SEAT|COURT|- JUSTICE POSITION)? ?(?:NO\. )?#?0*(\d+)", post)):
                    post = number[1]
                named.append((office, title, post))
                break
    oe_statewide.shape(frame, fips, named, PARSED)


def emit() -> Path:
    states = pl.read_parquet(PARSED / "races.parquet")["state"].unique().sort().to_list()

    def cite(cs: Changeset) -> dict[str, UUID]:
        sources = {}
        for state in states:
            title = f"{state} county-level results"
            sources[state] = cs.source("OpenElections", title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="community",
                    publisher="OpenElections",
                    title=title,
                    url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                    license=None,
                )
        for state, _, path in openelections.picks():
            if state in sources:
                cs.artifact(
                    raw.cached(
                        openelections.SOURCE,
                        openelections.REPOSITORY.format(
                            state=state.lower(), commit=openelections.COMMITS[state], path=path
                        ),
                    ),
                    sources[state],
                )
        return sources

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="State supreme court and court of appeals elections 2000–2014 by county from OpenElections' results files",
        cite=cite,
        method=METHOD,
        absent=oe_statewide.MISSING,
        close=oe_statewide.CLOSE,
    )
