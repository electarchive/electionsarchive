import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.hi_leg"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://files.hawaii.gov/elections/files/results/"
DAYS = {
    date(2002, 11, 5): (7, 13, 16),
    date(2004, 11, 2): (7, 13, 16),
    date(2006, 11, 7): (7, 13, 16),
    date(2008, 11, 4): (7, 13, 16),
    date(2010, 11, 2): (7, 13, 16),
    date(2012, 11, 6): (7, 13, 16),
    date(2014, 11, 4): (7, 13, 16),
    date(2016, 11, 8): (7, 13, 16),
    date(2018, 11, 6): (7, 13, 16),
    date(2020, 11, 3): (7, 13, 16),
    date(2022, 11, 8): (8, 14, 17),
}
COUNTIES = ("15001", "15009", "15007", "15003")
PRECINCT = re.compile(r"^\s*(?:PRECINCT|MAIL|IN-PERSON)\s+(\d\d-\d\d)\b\s*-?\s*(.*?)\s*(?:Printed on.*)?$", re.MULTILINE)
HEADER = re.compile(r"State (Representative|Senator)\b[^│\d]*?(\d+)(.*?)(?= {3}|$)", re.IGNORECASE)
CANDIDATE = re.compile(r"(?:\([A-Z]{1,2}\)\s+)?([A-Z][A-Za-z'’.\- ]*?,[^\d│]*?)\s*(?:\([A-Z]{1,2}\))?\s+([\d,]+)\s+[\d.]+%?")
OVER = re.compile(r"over votes", re.IGNORECASE)
PUBLISHER = "Hawaii Office of Elections"
TITLE = "General election precinct detail reports"


def url(day: date) -> str:
    return f"{BASE}{day.year}/general/precinct.pdf"


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, url(day), suffix=".pdf")


def parse() -> None:
    rows = []
    names = {}
    for day, bounds in DAYS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(day)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.replace("│", " ")
        overs = sorted({match.start() for line in text.splitlines() for match in OVER.finditer(line)})
        starts = [0]
        for previous, position in zip(overs, overs[1:], strict=False):
            if position > previous + 20:
                starts.append(position - 8)
        precinct = None
        contests: dict[int, str | None] = {}
        for page in text.split("\f"):
            if found := PRECINCT.search(page):
                precinct = found[1]
                names.setdefault((day.year, precinct), found[2].strip(" -"))
                contests = {}
            if precinct is None:
                continue
            for line in page.splitlines():
                for match in HEADER.finditer(line):
                    column = max(index for index, start in enumerate(starts) if start <= match.start() + 2)
                    special = re.search(r"vacan|unexpired|\d\s*-?\s*y(?:ea)?r", match[3], re.IGNORECASE)
                    office = "house" if match[1].lower() == "representative" else "senate"
                    contests[column] = None if special else f"{office}:{int(match[2])}:g"
                headed = [match.span() for match in HEADER.finditer(line)]
                for match in CANDIDATE.finditer(line):
                    if any(start <= match.start() <= end for start, end in headed):
                        continue
                    column = max(index for index, start in enumerate(starts) if start <= match.start() + 2)
                    if contests.get(column) is not None:
                        rows.append((day, contests[column], precinct, match[1].strip(), int(match[2].replace(",", ""))))
                for match in OVER.finditer(line):
                    contests[max(index for index, start in enumerate(starts) if start <= match.start())] = None
        print(day, "columns at", starts, len({row[1] for row in rows if row[0] == day}), "contests")
    frame = (
        pl.DataFrame(
            rows,
            schema={"day": pl.Date, "chamber": pl.String, "precinct": pl.String, "candidate": pl.String, "votes": pl.Int64},
            orient="row",
        )
        .group_by("day", "chamber", "precinct", "candidate")
        .agg(pl.col("votes").sum())
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        district=pl.col("precinct").str.slice(0, 2).cast(pl.Int32),
        cuts=pl.col("day").replace_strict({day: list(bounds) for day, bounds in DAYS.items()}, return_dtype=pl.List(pl.Int32)),
    ).select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber="chamber",
        day="day",
        special=pl.lit(False),
        state=pl.lit("HI"),
        district=pl.lit(None, pl.Int16),
        code=pl.concat_str(
            pl.col("day").dt.year().cast(pl.String),
            pl.lit("HI"),
            pl.when(pl.col("district") <= pl.col("cuts").list.get(0))
            .then(pl.lit(COUNTIES[0]))
            .when(pl.col("district") <= pl.col("cuts").list.get(1))
            .then(pl.lit(COUNTIES[1]))
            .when(pl.col("district") <= pl.col("cuts").list.get(2))
            .then(pl.lit(COUNTIES[2]))
            .otherwise(pl.lit(COUNTIES[3])),
            "precinct",
            separator=":",
        ),
        kind=pl.lit("precinct"),
        candidate="candidate",
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        votes="votes",
        source=pl.lit("HI"),
    ).write_parquet(PARSED / "precincts.parquet")
    pl.DataFrame(
        [(f"{year}:{precinct}", name) for (year, precinct), name in names.items()],
        schema={"key": pl.String, "name": pl.String},
        orient="row",
    ).write_parquet(PARSED / "names.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    names = dict(pl.read_parquet(PARSED / "names.parquet").iter_rows())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Hawaii's 2002 to 2022 State House and Senate generals by precinct from the Office of Elections' "
            "precinct detail reports",
            polity="US-HI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.hawaii.gov/election-results/",
            license="Public record",
        )
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, url(day)), source)
        medsl.attach(
            cs,
            conn,
            frame,
            {"HI": source},
            grain="precinct",
            strict=True,
            authoritative="Source: the Office of Elections' precinct detail reports.",
        )
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '15%%'"
            ).fetchall()
        }
        for code, day in frame.group_by("code").agg(pl.col("day").first()).iter_rows():
            unit = cs.unit("us.precinct", code)
            if str(unit) not in used or unit not in cs.minted:
                continue
            year, _, fips, precinct = code.split(":")
            name = names.get(f"{year}:{precinct}")
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=cs.polity("US-HI"),
                scheme_code="us.precinct",
                code=code,
                parent_id=counties[fips][0],
                area_relation="within",
                area_id=counties[fips][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
            cs.add(
                "name",
                entity_id=unit,
                text=f"Precinct {precinct}" + (f", {name}" if name else ""),
                lang="en",
                kind="official",
                is_preferred=True,
            )
        return cs.write()
