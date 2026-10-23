import csv
import io
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections, wa_local, wa_results
from ea.store import DATA, connect

SOURCE = "us.wa_counties"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://results.vote.wa.gov/results/{day:%Y%m%d}/export/{day:%Y%m%d}_{name}.csv"
EACH = [date(2008, 11, 4), date(2010, 11, 2)]
ALL = {date(2012, 11, 6): "AllCounties_20121205_1451", date(2014, 11, 4): "AllCounties"}
PRIMARY = date(2014, 8, 5)
RACE = re.compile(
    r"(?:U\.S\. )?(Congressional|Legislative) District (\d+)\s*-?\s*(U\.S\. Representative|Representative|State "
    r"Senator|State Representative Pos\. (\d))(.*)"
)
UNEXPIRED = {(date(2008, 11, 4), "34")}


def files() -> list[tuple[date, str | None, str, str]]:
    return [
        *(
            (day, county, wa_results.SOURCE, wa_results.SITE.format(county))
            if day == wa_results.DAY
            else (day, county, SOURCE, BASE.format(day=day, name=county))
            for day in EACH
            for county in wa_results.COUNTIES
        ),
        *((day, None, SOURCE, BASE.format(day=day, name=name)) for day, name in ALL.items()),
        (PRIMARY, None, wa_local.SOURCE, wa_local.FILES[PRIMARY]),
    ]


def fetch() -> None:
    for _, _, source, url in files():
        if source == SOURCE:
            raw.fetch(SOURCE, url, suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["WA"]
    rows = []
    for day, county, source, url in files():
        path = raw.cached(source, url).path
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig", errors="replace"))):
            found = RACE.fullmatch(re.sub(r"\s+", " ", row["Race"]).strip())
            if found is None:
                continue
            federal = found[1] == "Congressional"
            stage = "ttp" if day == PRIMARY else "g"
            if federal and day == PRIMARY:
                continue
            special = bool(re.search(r"(?i)short|unexpired", found[5])) or (day, found[2]) in UNEXPIRED
            if found[5].strip() and not re.search(r"(?i)short|unexpired", found[5]):
                raise ValueError(f"{day}: {row['Race']}")
            chamber = (
                "house"
                if federal
                else f"senate:{found[2]}:{stage}{'s' if special else ''}"
                if found[3] == "State Senator"
                else f"house:{found[2]}:{found[4]}:{stage}"
            )
            rows.append(
                (
                    day,
                    chamber,
                    int(found[2]) if federal else None,
                    special and federal,
                    fips[re.sub(r"[^a-z]", "", (county or row["County"]).lower())],
                    re.sub(r"\s+", " ", row["Candidate"]).strip().upper(),
                    bool(re.fullmatch(r"(?i)write-?ins?", row["Candidate"].strip())),
                    int(row["Votes"]),
                )
            )
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "special": pl.Boolean,
                "code": pl.String,
                "candidate": pl.String,
                "writein": pl.Boolean,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .group_by("day", "chamber", "district", "special", "code", "candidate", "writein")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            state=pl.lit("WA"),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("WA"),
        )
    )
    print(frame.group_by("day").agg(pl.struct("chamber", "district").n_unique(), pl.col("votes").sum()).sort("day"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's U.S. House and legislative generals 2008–2014 and 2014 legislative primaries by county from the Secretary of State's "
            "county export files",
            polity="US-WA",
        )
        source = cs.source(wa_local.PUBLISHER, wa_local.TITLE)
        if source in cs.minted:
            raise ValueError("the Secretary of State's election results source must already exist")
        for _, _, cache, url in files():
            cs.artifact(raw.cached(cache, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WA": source}, strict=True)
        return cs.write()
