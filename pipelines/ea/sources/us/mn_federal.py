import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mn_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://electionresultsfiles.sos.mn.gov/"
FILES = [
    (date(2020, 8, 11), "20200811/USHouseCty.txt"),
    (date(2020, 8, 11), "20200811/USSenateCty.txt"),
    (date(2022, 8, 9), "20220809/USHouseCty.txt"),
]
PARTIES = {
    "DFL": "minnesota-democratic-farmer-labor",
    "R": "republican",
    "IP": "independence",
    "I": "independence",
    "GP": "green",
    "GLC": "grassroots-legalize-cannabis",
    "LMN": "legal-marijuana-now",
    "LIB": "libertarian",
    "CP": "constitution",
}
OFFICE = re.compile(r"(?i)u\.?s\.? (senator|representative)(?: district (\d+))?")
PUBLISHER = "Minnesota Secretary of State"
TITLE = "Primary election results"


def fetch() -> None:
    for _, path in FILES:
        raw.fetch(SOURCE, BASE + path, suffix=".txt")


def parse() -> None:
    rows = []
    for day, path in FILES:
        for line in raw.cached(SOURCE, BASE + path).path.read_text(encoding="latin-1").splitlines():
            cells = [cell.strip().strip('"').strip() for cell in line.split(";")]
            if len(cells) < 14 or (found := OFFICE.fullmatch(cells[4])) is None:
                continue
            party = PARTIES.get(cells[10])
            if party is None:
                print(day, "unmapped party", cells[10], cells[7])
                continue
            rows.append(
                (
                    day.year,
                    ("house:" if found[2] else "senate:") + party,
                    day,
                    False,
                    "MN",
                    int(found[2]) if found[2] else None,
                    f"27{2 * int(cells[1]) - 1:03}",
                    re.sub(r"\s+", " ", cells[7]).upper(),
                    1,
                    "WRITE" in cells[7].upper(),
                    int(cells[13] or 0),
                )
            )
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
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein").agg(
        pl.col("votes").sum()
    ).with_columns(kind=pl.lit("county"), source=pl.lit("MN")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's 2020 U.S. Senate and 2020 and 2022 U.S. House primaries by county",
            polity="US-MN",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://electionresults.sos.mn.gov/",
            license="Public record",
        )
        for _, path in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").filter(~pl.col("writein")),
            {"MN": source},
            strict=True,
        )
        return cs.write()
