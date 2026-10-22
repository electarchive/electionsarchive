import csv
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.md_archive"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = "https://elections.maryland.gov/elections/archive/"
FILES = {
    "2000/results/prepaa.csv": (date(2000, 3, 7), True),
    "2002/results/p_all_offices.txt": (date(2002, 9, 10), True),
    "2002/results/g_all_offices.txt": (date(2002, 11, 5), False),
}
HEADER = re.compile(
    r"(U\.S\. Senator|Representative in Congress) - \(Vote for One\)(?: - Congressional District: (\d+))? - (Democratic|Republican) Candidate\(s\)"
)
PUBLISHER = "Maryland State Board of Elections"
TITLE = "Election results archive"


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, ARCHIVE + name, suffix=Path(name).suffix)


def parse() -> None:
    with connect() as conn:
        counties = {
            name.removesuffix(", MD").removesuffix(" County").lower(): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '24%%' and u.valid @> '2002-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for name, (day, primary) in FILES.items():
        text = raw.cached(SOURCE, ARCHIVE + name).path.read_text(encoding="latin-1")
        if name.endswith(".csv"):
            contest = None
            for cells in csv.reader(text.splitlines()):
                cells = [cell.strip() for cell in cells if cell.strip()]
                if not cells:
                    contest = None
                    continue
                if len(cells) == 1 and (found := HEADER.fullmatch(cells[0])):
                    office, district, party = found.groups()
                    contest = [office, district, party, None]
                    continue
                if contest is None:
                    continue
                if contest[3] is None:
                    contest[3] = [re.sub(r"\s+Winner$", "", cell) for cell in cells]
                    continue
                county, *votes = cells
                if len(votes) != len(contest[3]):
                    raise ValueError(f"{name} {contest[:3]} {county}: {len(votes)} values for {len(contest[3])} candidates")
                for candidate, value in zip(contest[3], votes, strict=True):
                    rows.append((day, contest[0], contest[1], contest[2], counties[county.lower()], candidate, int(value)))
        else:
            for line in text.splitlines():
                cells = [cell.strip() for cell in line.split("|")]
                if cells[0] not in ("Representative in Congress", "U.S. Senator"):
                    continue
                last, middle, first = (cell for cell in cells[3:6])
                candidate = " ".join(part for part in (first, middle, last) if part and part != "\\N")
                county = counties[cells[2].removesuffix(" County").lower()]
                rows.append((day, cells[0], cells[1], cells[6] if primary else None, county, candidate, int(cells[9])))
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "office": pl.String,
            "district": pl.String,
            "party": pl.String,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    if frame.select("day", "office", "district", "party", "code", "candidate").is_duplicated().any():
        raise ValueError("a county appears twice for a candidate")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("day", "office", "party").agg(pl.col("district").n_unique(), pl.col("votes").sum()).sort("day").rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's 2000 and 2002 U.S. House and Senate primaries and 2002 House generals by county from the State Board of Elections' results archive",
            polity="US-MD",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://elections.maryland.gov/elections/archive/index.html",
                license="Public record",
            )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, ARCHIVE + name), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber=pl.when(pl.col("office") == "U.S. Senator").then(pl.lit("senate")).otherwise(pl.lit("house"))
                + pl.when(pl.col("party").is_not_null()).then(pl.lit(":") + pl.col("party").str.to_lowercase()).otherwise(pl.lit("")),
                day="day",
                special=pl.lit(False),
                state=pl.lit("MD"),
                district=pl.col("district").cast(pl.Int16),
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("candidate").str.contains("(?i)write"),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("MD"),
            ),
            {"MD": source},
            strict=True,
        )
        return cs.write()
