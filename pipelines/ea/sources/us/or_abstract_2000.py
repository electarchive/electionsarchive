import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.or_abstract_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://records.sos.state.or.us/ORSOSWebDrawer/Record/6920724/File/document"
DAY = date(2000, 11, 7)
PUBLISHER = "Oregon Secretary of State"
TITLE = "2000 General Election Official Results"
TABLES = {
    "SECRETARY OF STATE": (
        "Secretary of State",
        [
            ("BILL BRADBURY", "DEMOCRATIC"),
            ("LLOYD MARBET", "PACIFIC GREEN"),
            ("LYNN SNODGRASS", "REPUBLICAN"),
            ("E. J. (ED) POLE II", "LIBERTARIAN"),
        ],
    ),
    "STATE TREASURER": (
        "Treasurer",
        [
            ("LEONARD ZACK", "REFORM"),
            ("RANDALL EDWARDS", "DEMOCRATIC"),
            ("CARLOS F. LUCERO", "CONSTITUTION"),
            ("JON KVISTAD", "REPUBLICAN"),
            ("MITCHELL T. SHULTS", "LIBERTARIAN"),
        ],
    ),
    "ATTORNEY GENERAL": (
        "Attorney General",
        [
            ("HARDY MYERS", "DEMOCRATIC"),
            ("KEVIN L. MANNIX", "REPUBLICAN"),
            ("THOMAS B. COX", "LIBERTARIAN"),
        ],
    ),
}
METHOD = (
    "County totals come from the Oregon Secretary of State's official results for the November 7, 2000 general election. "
    "Each candidate's county votes add up to the TOTAL row printed in the abstract."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '41%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows = []
    table = None
    sums: list[list[int]] = []
    for line in text.splitlines():
        if line.strip() in TABLES:
            table, sums = TABLES[line.strip()], []
            continue
        if table is None or not (row := re.fullmatch(r"\s*([A-Z][A-Za-z ]+?)\s+((?:[\d,]+\s+)*[\d,]+)\s*", line)):
            continue
        title, candidates = table
        votes = [int(value.replace(",", "")) for value in row[2].split()]
        if len(votes) != len(candidates) + 1:
            raise ValueError(f"{title} {row[1]}: {votes}")
        if row[1] == "TOTAL":
            if votes != [sum(column) for column in zip(*sums, strict=True)] or len(sums) != 36:
                raise ValueError(f"{title}: counties do not sum to the printed total")
            table = None
            continue
        sums.append(votes)
        for (name, party), value in zip([*candidates, ("WRITE-INS", None)], votes, strict=True):
            rows.append((title, codes[re.sub(r"[^a-z]", "", row[1].lower())], name, party, name == "WRITE-INS", value))
    frame = pl.DataFrame(
        rows,
        schema={
            "title": pl.String,
            "county": pl.String,
            "candidate": pl.String,
            "party": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).select(
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        state=pl.lit("OR"),
        title="title",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate="candidate",
        party="party",
        writein="writein",
        measure=pl.lit(1, pl.Int16),
        votes="votes",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(36),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate").agg(pl.col("votes").sum()).sort("title", "votes").rows())


def emit() -> Path:
    def cite(cs):
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=URL,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        return {"OR": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Oregon's 2000 secretary of state, treasurer, and attorney general by county from the Secretary of State's "
        "official results",
        cite=cite,
        method=METHOD,
    )
