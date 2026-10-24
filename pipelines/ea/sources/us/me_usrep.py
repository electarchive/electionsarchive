import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA

SOURCE = "us.me_usrep"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Maine Secretary of State"
TITLE = "General election tabulation, U.S. Representative to Congress"
LANDING = "https://lldc.mainelegislature.org/Open/Elections/Returns/"
URL = "https://lldc.mainelegislature.org/Open/Elections/Returns/{year}/{day}/elec_{day}{letter}.xlsx"
FILES = {
    date(1992, 11, 3): "h",
    date(1994, 11, 8): "h",
    date(1996, 11, 5): "l",
}
COUNTIES = {
    "AND": "23001",
    "ARO": "23003",
    "CUM": "23005",
    "FRA": "23007",
    "HAN": "23009",
    "KEN": "23011",
    "KNO": "23013",
    "LIN": "23015",
    "OXF": "23017",
    "PEN": "23019",
    "PIS": "23021",
    "SAG": "23023",
    "SOM": "23025",
    "WAL": "23027",
    "WAS": "23029",
    "YOR": "23031",
}
METHOD = (
    "County totals are sums of the Secretary of State's official town and ward tabulation for the U.S. Representative "
    "to Congress (the Law and Legislative Digital Library's spreadsheets of the Bureau of Corporations, Elections and "
    "Commissions' election database)."
)


def url(day: date) -> str:
    return URL.format(year=day.year, day=day.strftime("%Y%m%d"), letter=FILES[day])


def fetch() -> None:
    for day in FILES:
        try:
            raw.cached(SOURCE, url(day))
        except LookupError:
            body = subprocess.run(
                ["curl", "-sL", "-m", "300", "-A", "Mozilla/5.0", url(day)], capture_output=True, check=True
            ).stdout
            raw.store(
                SOURCE,
                url(day),
                body,
                suffix=".xlsx",
                retrieved_at=datetime.now(UTC),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def parse() -> None:
    rows = []
    for day in FILES:
        for row in pl.read_excel(raw.cached(SOURCE, url(day)).path, has_header=False).iter_rows():
            if day.year == 1992:
                if not isinstance(row[0], int) or row[2] not in COUNTIES:
                    continue
                for vote, name in zip(row[4:8], list(row[8:11]) + ["OTHER"], strict=True):
                    rows.append((str(day), int(row[3]), COUNTIES[row[2]], f"{row[3]}:{name}", int(vote)))
            elif day.year == 1994:
                if row[0] not in COUNTIES:
                    continue
                for index in range(3, 15, 3):
                    if row[index] is not None:
                        rows.append(
                            (str(day), int(row[2]), COUNTIES[row[0]], f"{row[2]}:{row[index]}", int(row[index + 2]))
                        )
                rows.append((str(day), int(row[2]), COUNTIES[row[0]], f"{row[2]}:OTHER", int(row[15])))
            else:
                if row[0] != "CG" or row[3] is None or row[2] not in COUNTIES:
                    continue
                for index in range(5, len(row) - 3, 4):
                    if row[index] is None:
                        continue
                    vote = row[index + 3]
                    if isinstance(vote, int | float):
                        rows.append((str(day), int(row[1]), COUNTIES[row[2]], f"{row[1]}:{row[index]}", int(vote)))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = (
        pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
        .group_by("day", "district", "county", "column")
        .agg(pl.col("votes").sum())
        .filter(pl.col("votes") > 0)
    )
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "district", "column").agg(pl.col("votes").sum()).sort("day", "district", "column"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Maine's U.S. House general elections of 1992 through 1996 by county from the Secretary of State's tabulations",
        PUBLISHER,
        TITLE,
        LANDING,
        "ME",
        "23",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, url(day)) for day in FILES],
        lambda key: METHOD,
    )
