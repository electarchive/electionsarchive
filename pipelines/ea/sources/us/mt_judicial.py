import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.mt_judicial"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-333"
BASE = "https://sosmt.gov/wp-content/uploads/attachments/"
PUBLISHER = "Montana Secretary of State"
TITLE = "Statewide general election canvass"
FILES = {
    2008: (date(2008, 11, 4), "2008-general/2008_State_General.pdf"),
    2010: (date(2010, 11, 2), "2010-general/2010_General_Election_Official_Statewide_Canvass.pdf"),
    2012: (date(2012, 11, 6), "2012-general/2012_General_Canvass.pdf"),
    2014: (date(2014, 11, 4), "2014-general/2014-General-Official-Statewide-Canvass.pdf"),
}
RACES = (
    (2010, "JUSTICE #4", "Justice of the Supreme Court", "Seat 4", ("Beth Baker", "Nels Swandal")),
    (2012, "JUSTICE #5", "Justice of the Supreme Court", "Seat 5", ("Laurie McKinnon", "Ed Sheehy")),
    (2014, "JUSTICE #1", "Justice of the Supreme Court", "Seat 1", ("W. David Herbert", "Jim Rice")),
    (2014, "JUSTICE #2", "Justice of the Supreme Court", "Seat 2", ("Lawrence VanDyke", "Mike Wheat")),
)
CHIEF = ("Mike McGrath", "Ron Waterman")
ROW = re.compile(r"^(?P<county>[A-Z][A-Za-z.&' ]+?)\s+(?P<a>\d[\d,]*)\s+(?P<b>\d[\d,]*)\s*$")
CELL = re.compile(r"\d[\d,]*")
METHOD = "County totals come from the Secretary of State's official statewide canvass of the general election."
MISSING = "The canvass has no row for {counties} in this race; those votes are missing from these totals."
CLOSE = (
    "The race was within 0.5% in these totals, so no winner is recorded here; the official canvass and any recount "
    "decided it."
)


def location(year: int) -> str:
    return BASE + Path(FILES[year][1]).name


def fetch() -> None:
    for year, (_, name) in FILES.items():
        raw.store(
            SOURCE,
            location(year),
            (INBOX / name).read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime(2026, 10, 31, tzinfo=UTC),
            media_type="application/pdf",
        )


def text(year: int) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, location(year)).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower().replace("&", "and"))


def pairs(lines: list[str]) -> tuple[list[tuple[str, int, int]], tuple[int, int]]:
    rows: list[tuple[str, int, int]] = []
    for line in lines:
        found = ROW.match(line.strip())
        if found is None:
            continue
        a, b = (int(found[name].replace(",", "")) for name in "ab")
        if found["county"] == "Total":
            return rows, (a, b)
        rows.append((found["county"], a, b))
    raise ValueError("no total row")


def chief(lines: list[str]) -> tuple[list[tuple[str, int, int]], tuple[int, int]]:
    start = next(i for i, line in enumerate(lines) if "Chief Justice" in line)
    head = next(i for i in range(start, len(lines)) if "319,462" in lines[i])
    ends = [match.end() for match in CELL.finditer(lines[head])]
    column = ends.index(next(end for end, match in zip(ends, CELL.finditer(lines[head])) if match[0] == "319,462"))
    rows = []
    for line in lines[head + 1 :]:
        cells = [(match.end(), int(match[0].replace(",", ""))) for match in CELL.finditer(line)]
        name = line[: line.find(next(iter(CELL.findall(line)), "")) if CELL.search(line) else 0].strip()
        if not name:
            continue
        picked = []
        for slot in (column, column + 1):
            near = [value for end, value in cells if abs(end - ends[slot]) <= 2]
            if len(near) != 1:
                raise ValueError(f"{name}: no single figure under column {slot}")
            picked.append(near[0])
        rows.append((name, picked[0], picked[1]))
        if name == "Yellowstone":
            break
    first = CELL.findall(lines[head])
    return rows, (int(first[column].replace(",", "")), int(first[column + 1].replace(",", "")))


def parse() -> None:
    with connect() as conn:
        fips = {
            key(name): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '30%%'"""
            ).fetchall()
        }
    contests = []
    for year, heading, title, post, names in RACES:
        lines = text(year).splitlines()
        start = next(i for i, line in enumerate(lines) if heading in line and "SUPREME COURT" in lines[i - 1])
        contests.append((year, title, post, names, *pairs(lines[start + 1 :])))
    contests.append((2008, "Chief Justice of the Supreme Court", None, CHIEF, *chief(text(2008).splitlines())))
    rows = []
    for year, title, post, names, counties, totals in contests:
        if len(counties) != 56:
            raise ValueError(f"{year} {title} {post}: {len(counties)} counties")
        for index, name in enumerate(names):
            if sum(county[1 + index] for county in counties) != totals[index]:
                raise ValueError(f"{year} {name}: the counties do not add up to the printed total")
            for county in counties:
                rows.append((year, FILES[year][0], title, post, fips[key(county[0])], name.upper(), county[1 + index]))
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "day": pl.Date,
            "title": pl.String,
            "post": pl.String,
            "county": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).select(
        "year",
        "day",
        state=pl.lit("MT"),
        title="title",
        post="post",
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate="candidate",
        party=pl.lit(None, pl.String),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes="votes",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(56),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("year", "candidate").agg(pl.col("votes").sum()).sort("year").rows())


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://sosmt.gov/elections/results/",
                license="Public record",
            )
        for year in FILES:
            cs.artifact(raw.cached(SOURCE, location(year)), source)
        return {"MT": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Montana's contested Supreme Court elections of 2008 through 2014 by county from the Secretary of "
        "State's official statewide canvasses",
        cite=cite,
        method=METHOD,
        absent=MISSING,
        close=CLOSE,
    )
