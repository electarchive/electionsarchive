import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import fp_canvass, medsl, sc_report
from ea.store import DATA, connect

SOURCE = "us.sc_fp"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://scvotes.gov/wp-content/uploads/2022/08/Election_Report_{}.pdf"
BOOKS = {2002: "2002", 2004: "2004"}
DAYS = {2002: date(2002, 11, 5), 2004: date(2004, 11, 2)}
TABLES = {
    (2002, "governor"): (41, ["JAMES H. HODGES", "MARK SANFORD", "WRITE-INS"]),
    (2002, "senate"): (38, ["ALEX SANDERS", "TED ADAMS", "LINDSEY GRAHAM", "VICTOR KOCHER", "WRITE-INS"]),
    (2004, "senate"): (
        46,
        [
            "PATRICK TYNDALL",
            "TEE FERGUSON",
            "EFIA NWANGAZA",
            "JIM DEMINT",
            "REBEKAH E. SUTHERLAND",
            "INEZ TENENBAUM",
            "WRITE-INS",
        ],
    ),
}
CHARLESTON = {
    (2002, "governor"): {"JAMES H. HODGES": 41352, "MARK SANFORD": 51917},
    (2002, "senate"): {"ALEX SANDERS": 41955, "TED ADAMS": 659, "LINDSEY GRAHAM": 48773, "VICTOR KOCHER": 553},
}
ABBEVILLE_2004 = dict(enumerate([88, 43, 20, 4954, 40, 4592, 6]))
FIXES = str.maketrans({"&": "6", "l": "1", "I": "1", "O": "0", "o": "0", "S": "5", "B": "8", "Z": "2", "|": "1"})
NUMBER = re.compile(r"[\dOolIZSB&|]+(?:\s?[,.]\s?[\dOolIZSB&|]+)*")


def fetch() -> None:
    for book in BOOKS.values():
        raw.fetch(SOURCE, URL.format(book), suffix=".pdf")


def parse() -> None:
    rows = []
    for (year, chamber), (page, names) in TABLES.items():
        path = raw.cached(SOURCE, URL.format(BOOKS[year])).path
        text = subprocess.run(
            ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.replace("\xad", "").replace("'", " ").splitlines()
        total = next(line for line in text if line.strip().startswith("STATE TOTAL"))
        edges = [m.end() for m in NUMBER.finditer(total[total.index("TOTAL") + 5 :])]
        offset = total.index("TOTAL") + 5
        edges = [edge + offset for edge in edges][: len(names)]
        printed = [int(re.sub(r"[^0-9]", "", m[0].translate(FIXES))) for m in NUMBER.finditer(total[offset:])][: len(names)]
        body = []
        for line in text[: text.index(total)]:
            match = re.match(r"^\s*[A-Za-z.~]{3,}[A-Za-z.~ ]*?\s{2,}(?=[\dOolIZSB&|])", line)
            if match and len(body) < 46 and re.search(r"\d", line):
                body.append(line)
        grid: list[dict[int, int]] = [ABBEVILLE_2004] if (year, chamber) == (2004, "senate") else []
        if len(body) + len(grid) != 46:
            raise ValueError(f"{year} {chamber}: {len(body) + len(grid)} county rows")
        for line in body:
            row: dict[int, int] = {}
            for m in NUMBER.finditer(line[20:]):
                end = m.end() + 20
                column = min(range(len(edges)), key=lambda i: abs(edges[i] - end))
                if abs(edges[column] - end) <= 5:
                    row[column] = int(re.sub(r"[^0-9]", "", m[0].translate(FIXES)))
            grid.append(row)
        override = CHARLESTON.get((year, chamber), {})
        for column, name in enumerate(names):
            summed = sum(row.get(column, 0) for row in grid)
            if summed != printed[column]:
                raise ValueError(f"{year} {chamber} {name}: counties sum to {summed}, printed {printed[column]}")
            if name in override:
                grid[sc_report.COUNTIES.index("Charleston")][column] = override[name]
        for index, county in enumerate(sc_report.COUNTIES):
            for column, name in enumerate(names):
                if column in grid[index]:
                    rows.append((year, chamber, county, name, grid[index][column], page))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["year", "chamber", "county", "candidate", "votes", "page"], orient="row")
    print(frame.group_by("year", "chamber", "candidate").agg(pl.col("votes").sum()).sort("year", "chamber", "votes"))
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        codes = fp_canvass.counties(conn, "45")
        frame = frame.with_columns(
            code=pl.col("county").map_elements(lambda name: codes[re.sub(r"[^a-z]", "", name.lower())], return_dtype=pl.String),
            day=pl.col("year").replace_strict({year: day for year, day in DAYS.items()}, return_dtype=pl.Date),
            state=pl.lit("SC"),
            district=pl.lit(None, pl.Int16),
            special=pl.lit(False),
            writein=pl.col("candidate") == "WRITE-INS",
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("SC"),
        ).drop("county", "page")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Carolina's 2002 governor and U.S. Senate and 2004 U.S. Senate generals by county from the "
            "State Election Commission's election reports, for contests whose only county results were Algara and Amlani's",
            polity="US",
        )
        sources: dict[str, UUID] = {}
        title = "South Carolina Election Report, 2002 and 2004"
        sources["SC"] = cs.source(sc_report.PUBLISHER, title)
        if sources["SC"] in cs.minted:
            cs.add(
                "source",
                id=sources["SC"],
                kind="official_body",
                publisher=sc_report.PUBLISHER,
                title=title,
                url=sc_report.LANDING,
                license="Public record",
            )
        for book in BOOKS.values():
            cs.artifact(raw.cached(SOURCE, URL.format(book)), sources["SC"])
        columns = ["year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein", "votes", "kind", "source"]
        medsl.attach(
            cs,
            conn,
            frame.filter(pl.col("year") == 2002).select(columns),
            sources,
            strict=True,
            extend=True,
        )
        medsl.attach(
            cs,
            conn,
            frame.filter(pl.col("year") == 2004).select(columns),
            sources,
            strict=False,
            extend=True,
            authoritative="The report prints Efia Nwangaza's Green and Independence lines as one column.",
        )
        notes = {
            "2002-11-05:SC:governor": "Charleston County's figures for Jim Hodges and Mark Sanford are the county totals printed on the Commission's county precinct pages (41,352 and 51,917); the report's statewide table prints Charleston 2,852 and 4,963 votes short, and its statewide totals are short by the same amounts.",
            "2002-11-05:SC:senate": "Charleston County's figures for Alex Sanders, Ted Adams, Lindsey Graham, and Victor Kocher are the county totals printed on the Commission's county precinct pages (41,955, 659, 48,773, and 553); the report's statewide table prints Charleston short, and with the county pages' totals every candidate's counties sum to the FEC's total.",
            "2004-11-02:SC:senate": "The report prints Efia Nwangaza's Green Party and Independence Party lines as one column (4,383 statewide), which is credited to her Green line; the FEC lists 4,245 and 138 separately.",
        }
        for key, body in notes.items():
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            if any(row["id"] == result_set for row in cs.rows.get("result_set", [])):
                cs.add("note", entity_id=result_set, kind="caveat", body_md=body)
        return cs.write()
