import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mn_specials"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://electionresultsfiles.sos.mn.gov/{day:%Y%m%d}/{name}.txt"
FILES = {
    date(2023, 12, 5): ("sthousepct", "s"),
    date(2024, 3, 19): ("sthousepct", "s"),
    date(2025, 3, 11): ("sthousepct", "s"),
    date(2025, 4, 29): ("StateSenateByPct", "s"),
    date(2025, 9, 16): ("sthousepct", "s"),
    date(2025, 11, 4): ("StateSenateByPct", "gs"),
    date(2026, 1, 27): ("sthousepct", "s"),
}
PUBLISHER = "Minnesota Secretary of State"
TITLE = "Special election results by precinct"


def fetch() -> None:
    for day, (name, _) in FILES.items():
        raw.fetch(SOURCE, BASE.format(day=day, name=name), suffix=".txt")


def parse() -> None:
    rows = []
    for day, (name, suffix) in FILES.items():
        for line in raw.cached(SOURCE, BASE.format(day=day, name=name)).path.read_text(errors="replace").splitlines():
            fields = line.split(";")
            if len(fields) < 14 or not fields[1].strip():
                continue
            found = re.fullmatch(r"State (Senator|Representative) District (\d+)([AB]?)", fields[4].strip())
            if not found:
                raise ValueError(f"{day}: {fields[4]}")
            district = f"{int(found[2])}-{found[3].lower()}" if found[3] else str(int(found[2]))
            rows.append(
                (
                    day,
                    f"{'senate' if found[1] == 'Senator' else 'house'}:{district}:{suffix}",
                    "27" + str(int(fields[1]) * 2 - 1).zfill(3),
                    "WRITE-INS" if fields[10] == "WI" else fields[7].strip().upper(),
                    fields[10] == "WI",
                    int(fields[13]),
                )
            )
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "code": pl.String,
                "candidate": pl.String,
                "writein": pl.Boolean,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .group_by("day", "chamber", "code", "candidate", "writein")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            district=pl.lit(None, pl.Int16),
            special=pl.lit(True),
            state=pl.lit("MN"),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("MN"),
        )
    )
    print(frame.group_by("day", "chamber").agg(pl.col("code").n_unique(), pl.col("votes").sum()).sort("day"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's 2023–2026 state legislative specials by county from the Secretary of State's precinct files",
            polity="US-MN",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://electionresultsfiles.sos.mn.gov/",
                license="Public record",
            )
        for day, (name, _) in FILES.items():
            cs.artifact(raw.cached(SOURCE, BASE.format(day=day, name=name)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MN": source}, strict=True)
        return cs.write()
