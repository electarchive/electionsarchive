import csv
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ak_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.elections.alaska.gov/results/"
FILES = {
    date(2014, 8, 19): "14PRIM/data/results-precinct.txt",
    date(2016, 8, 16): "16PRIM/data/resultsbyprct.txt",
    date(2018, 8, 21): "18PRIM/data/resultsbyprecinct.txt",
}
PARTIES = {"DEM": "democratic", "REP": "republican", "LIB": "libertarian"}
STATS = {"Number of Precincts for Race", "Number of Precincts Reporting", "Registered Voters", "Times Counted"}
PUBLISHER = "Alaska Division of Elections"
TITLE = "Primary election results by precinct"


def fetch() -> None:
    for path in FILES.values():
        try:
            raw.cached(SOURCE, BASE + path)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfk", "--compressed", "-A", raw.USER_AGENT, BASE + path], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, BASE + path, body, suffix=".txt", retrieved_at=datetime.now(UTC), media_type="text/plain")


def parse() -> None:
    rows = []
    for day, path in FILES.items():
        totals: dict[tuple[str, str], int] = {}
        printed: dict[tuple[str, str], int] = {}
        lines = raw.cached(SOURCE, BASE + path).path.read_text(encoding="latin-1").splitlines()
        for row in csv.reader(lines):
            if len(row) < 8 or not row[1].strip().startswith(("US SENATOR", "US REPRESENTATIVE")):
                continue
            place, race, _, _, name, party, column, votes = (cell.strip() for cell in row[:8])
            if name in STATS or column != "Total" or party not in PARTIES:
                continue
            office = "senate" if race.startswith("US SENATOR") else "house"
            chamber = f"{office}:{PARTIES[party]}"
            key = (chamber, name.upper())
            if place == "Election Total":
                printed[key] = int(votes)
            elif found := re.match(r"(\d\d)-", place) or re.match(r"District (\d+) - ", place):
                totals[key] = totals.get(key, 0) + int(votes)
                rows.append(
                    (
                        day.year,
                        chamber,
                        day,
                        False,
                        "AK",
                        0 if office == "house" else None,
                        f"{day.isoformat()}:{int(found[1]):02}",
                        name.upper(),
                        "district",
                        int(votes),
                    )
                )
            elif place.startswith("HD99"):
                totals[key] = totals.get(key, 0) + int(votes)
                rows.append(
                    (
                        day.year,
                        chamber,
                        day,
                        False,
                        "AK",
                        0 if office == "house" else None,
                        "AK:99",
                        name.upper(),
                        "aggregate",
                        int(votes),
                    )
                )
        for key in set(totals) | set(printed):
            if printed and totals.get(key) != printed.get(key):
                print(day, key, "sum", totals.get(key), "printed", printed.get(key))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
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
            "kind": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "kind", "candidate").agg(
        pl.col("votes").sum()
    ).with_columns(measure=pl.lit(1, pl.Int16), writein=pl.lit(False), source=pl.lit("AK")).write_parquet(
        PARSED / "districts.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "districts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska's 2014, 2016, and 2018 U.S. Senate and House primaries by House district",
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
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        state, parent = conn.execute(
            """select p.id, u.id from reporting_unit u join polity p on p.id = u.polity_id
               where u.scheme_code = 'us.state' and p.iso_code = 'US-AK'"""
        ).fetchone()
        for code, day in frame.filter(pl.col("kind") == "district").select("code", "day").unique().iter_rows():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.ak_house_district", code),
                polity_id=state,
                scheme_code="us.ak_house_district",
                code=code,
                parent_id=parent,
                area_relation="equals",
                area_id=cs.area("us.sldl", f"AK:{int(code[-2:])}"),
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        medsl.attach(cs, conn, frame, {"AK": source}, strict=True)
        return cs.write()
