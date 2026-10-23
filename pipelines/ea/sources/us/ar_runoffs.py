import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ar_runoffs"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "http://www.sos.arkansas.gov/uploads/elections/reportpr2012.pdf"
DAY = date(2012, 6, 12)
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Official primary runoff results by county"
HEADER = re.compile(r"^(\S.*?) - (DEM|REP)\s*$")
CONGRESS = re.compile(r"^U\.S\. Congress District (\d+)$")
CANDIDATE = re.compile(r"^#(\d+)-(.+?)\s*$")
ROW = re.compile(r"^([A-Za-z. ]+?)\s{2,}(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$")
TITLES = re.compile(r"^(?:Prosecuting Attorney|Senator|Representative|Congressman|Judge)\s+")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", name.removesuffix(", AR").removesuffix(" County").lower().replace("st.", "st")): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '05%%' and u.valid @> %s::date""",
                [DAY],
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows = []
    race: tuple[str, int] | None = None
    names: dict[str, str] = {}
    counted: dict[str, list[tuple[str, int, int]]] = {}
    for line in text.splitlines():
        header = HEADER.match(line)
        if header:
            found = CONGRESS.match(header[1])
            race = ("house:" + {"DEM": "democratic", "REP": "republican"}[header[2]], int(found[1])) if found else None
            names, counted = {}, {}
            continue
        if race is None:
            continue
        candidate = CANDIDATE.match(line)
        if candidate:
            names[candidate[1]] = TITLES.sub("", candidate[2])
            continue
        if line.startswith("Totals"):
            totals = [int(v.replace(",", "")) for v in line.split()[1:]]
            for position, name in names.items():
                summed = sum(votes[int(position) - 1] for votes in counted.values())
                if summed != totals[int(position) - 1]:
                    raise ValueError(f"{race} {name}: {summed} by county, {totals[int(position) - 1]} reported")
                rows.extend(
                    (race[0], race[1], codes[re.sub(r"[^a-z]", "", county.lower())], name, votes[int(position) - 1])
                    for county, votes in counted.items()
                )
            continue
        row = ROW.match(line)
        if row and row[1] != "County":
            counted[row[1]] = [int(row[2].replace(",", "")), int(row[3].replace(",", ""))]
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows,
        schema={
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("chamber", "district", "candidate").agg(pl.col("votes").sum(), pl.len()).rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas's June 12, 2012 Democratic primary runoffs for U.S. House by county",
            polity="US-AR",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.sos.arkansas.gov/elections/research/election-results",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.lit(DAY.year, pl.Int32),
                chamber="chamber",
                day=pl.lit(DAY),
                special=pl.lit(False),
                state=pl.lit("AR"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("AR"),
            ),
            {"AR": source},
            strict=True,
        )
        return cs.write()
