import csv
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ak_federal import STATS
from ea.sources.us.ak_leg import KINDS
from ea.store import connect

SOURCE = "us.ak_governor"
VERSION = "1"
DAY = date(2014, 11, 4)
URL = "https://www.elections.alaska.gov/results/14GENR/data/results-precinct.txt"
PUBLISHER = "Alaska Division of Elections"
TITLE = "Statements of votes cast by House district and precinct results"


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        body = subprocess.run(
            ["curl", "-sfkL", "--compressed", "-A", raw.USER_AGENT, URL], capture_output=True, check=True
        ).stdout
        raw.store(SOURCE, URL, body, suffix=".txt", retrieved_at=datetime.now(UTC), media_type="text/plain")


def emit() -> Path:
    rows = []
    for row in csv.reader(raw.cached(SOURCE, URL).path.read_text(encoding="latin-1").splitlines(), escapechar="\\"):
        if len(row) < 6:
            continue
        place, race, name, _, column, votes = (cell.strip() for cell in row[:6])
        if race != "GOVERNOR/LT GOVERNOR" or column != "Total" or name in STATS or not votes:
            continue
        writein = name.lower().startswith("write")
        if precinct := re.match(r"(\d\d-\d\d\d) ", place):
            code, kind = f"{DAY.year}:AK:{precinct[1]}", "precinct"
        elif aggregate := re.fullmatch(r"District (\d+)\s*- (.*)", place):
            code, kind = f"AK:{DAY.year}:{int(aggregate[1]):02}:{KINDS[aggregate[2]]}", "aggregate"
        else:
            raise ValueError(f"unknown place {place}")
        rows.append((code, kind, "WRITE-INS" if writein else name.split("/")[0].upper(), writein, int(votes)))
    frame = pl.DataFrame(
        rows,
        schema={"code": pl.String, "kind": pl.String, "candidate": pl.String, "writein": pl.Boolean, "votes": pl.Int64},
        orient="row",
    ).select(
        year=pl.lit(DAY.year, pl.Int32),
        chamber=pl.lit("governor"),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("AK"),
        district=pl.lit(None, pl.Int16),
        code="code",
        kind="kind",
        candidate="candidate",
        measure=pl.lit(1, pl.Int16),
        writein="writein",
        votes="votes",
        source=pl.lit("AK"),
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska's 2014 governor election by precinct, with absentee, question, and early votes by House district",
            polity="US-AK",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.elections.alaska.gov/election-results/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(
            cs,
            conn,
            frame,
            {"AK": source},
            grain="precinct",
            strict=True,
            authoritative="Source: the Division of Elections' official results by precinct.",
        )
        return cs.write()
