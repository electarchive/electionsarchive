import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.in_pri_9x"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Indiana Secretary of State"
TITLE = "Election report of Indiana"
DAYS = {1996: date(1996, 5, 7), 1998: date(1998, 5, 5)}
IMAGES = {
    1996: [2892, 2893, 2894, 2895, 2896],
    1998: [3099, 3100, 3101, 3102, 3103, 3104],
}
URL = "https://indianamemory.contentdm.oclc.org/iiif/2/p16066coll37:{}/full/2000,/0/default.jpg"
PARTIES = {"D": "democratic", "R": "republican", "L": "libertarian"}


def fetch() -> None:
    for pages in IMAGES.values():
        for page in pages:
            try:
                raw.cached(SOURCE, URL.format(page))
            except LookupError:
                raw.fetch(SOURCE, URL.format(page), suffix=".jpg")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '18%%'"""
            ).fetchall()
        }
    rows = []
    for year, day in DAYS.items():
        for chunk in Path(__file__).with_name(f"in_pri_9x_{year}.txt").read_text().split("@ ")[1:]:
            header, names, *lines = chunk.strip().splitlines()
            candidates = [name.strip() for name in names.split("|")]
            total = None
            if lines[-1].startswith("TOTALS"):
                total = [int(value) for value in lines.pop().split("|")[1].split()]
            table = []
            for line in lines:
                county, values = line.split("|")
                table.append((county.strip(), [int(value) for value in values.split()]))
            if any(len(values) != len(candidates) for _, values in table):
                raise ValueError(f"{year} {header}: column count differs from the candidate count")
            if (
                total is not None
                and [sum(values[column] for _, values in table) for column in range(len(total))] != total
            ):
                print(f"{year} {header} {candidates[0]}: rows do not tie the printed totals {total}")
            for county, values in table:
                for candidate, votes in zip(candidates, values, strict=True):
                    found = re.fullmatch(r"(.*?)\s*\((\w)\)", candidate)
                    rows.append(
                        (
                            day,
                            header.strip(),
                            PARTIES[found[2]],
                            found[1],
                            counties[re.sub(r"[^a-z]", "", county.lower())],
                            votes,
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "party", "candidate", "code", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indiana's U.S. House and Senate primaries of 1996 and 1998 by county from the Secretary of State's election reports",
            polity="US-IN",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://indianamemory.contentdm.oclc.org/digital/collection/p16066coll37",
                license="Public record",
            )
        for pages in IMAGES.values():
            for page in pages:
                cs.artifact(raw.cached(SOURCE, URL.format(page)), source)
        lines = frame.select(
            year=pl.col("day").dt.year().cast(pl.Int32),
            chamber=pl.when(pl.col("district") == "senate").then(pl.lit("senate:")).otherwise(pl.lit("house:"))
            + pl.col("party"),
            day="day",
            special=pl.lit(False),
            state=pl.lit("IN"),
            district=pl.when(pl.col("district") == "senate").then(None).otherwise(pl.col("district").cast(pl.Int16)),
            code="code",
            candidate="candidate",
            measure=pl.lit(1, pl.Int16),
            writein=pl.col("candidate").str.contains("(?i)write"),
            votes="votes",
            kind=pl.lit("county"),
            source=pl.lit("IN"),
        )
        medsl.attach(cs, conn, lines, {"IN": source}, strict=True)
        return cs.write()
