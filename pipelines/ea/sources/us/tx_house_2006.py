import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.sources.us.tx_elchist import PUBLISHER, SITE, cells
from ea.store import DATA, connect

SOURCE = "us.tx_house_2006"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2006, 11, 7)
ELECTION = 128
RACES = {77: 15, 83: 21, 84: 22, 85: 23, 87: 25, 90: 28}
TITLE = "Texas election history: 2006 special November elections by county"


def url(race: int) -> str:
    return f"{SITE}elchist{ELECTION}_race{race}.htm"


def fetch() -> None:
    for race in RACES:
        raw.fetch(SOURCE, url(race), suffix=".htm")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["TX"]
    rows = []
    for race, district in RACES.items():
        text = raw.cached(SOURCE, url(race)).path.read_text(errors="replace")
        lines = re.findall(r"<TR[^>]*>(.*?)</TR>", text, re.IGNORECASE | re.DOTALL)
        headers = [cells(line, "TH") for line in lines if re.search(r"<TH", line, re.IGNORECASE)]
        data = [cells(line, "TD") for line in lines if re.search(r"<TD", line, re.IGNORECASE)]
        width = headers[-1].index("Votes")
        names = [
            " ".join(
                part for row in headers[:-1] if column < len(row) and (part := row[column]) not in ("...", "Total")
            )
            for column in range(1, width)
        ]
        total = next(row for row in data if row[0] == "ALL COUNTIES")
        counties = [row for row in data if row[0] != "ALL COUNTIES"]
        for column, name in enumerate(names, start=1):
            votes = [int(row[column].replace(",", "") or 0) for row in counties]
            if sum(votes) != int(total[column].replace(",", "")):
                raise ValueError(f"{district} {name}: counties sum to {sum(votes)}, not {total[column]}")
            rows.extend(
                (district, name, county[0], count)
                for county, count in zip(counties, votes, strict=True)
                if int(county[width].replace(",", "") or 0) > 0
            )
    frame = pl.DataFrame(rows, schema=["district", "candidate", "county", "votes"], orient="row").with_columns(
        code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips, default=None)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        state=pl.lit("TX"),
        day=pl.lit(DAY),
        year=pl.lit(2006, pl.Int32),
        chamber=pl.lit("house"),
        district=pl.col("district").cast(pl.Int16),
        special=pl.col("district") == 22,
        code=pl.col("code"),
        candidate=pl.col("candidate"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.to_uppercase().str.contains("WRITE"),
        votes=pl.col("votes").cast(pl.Int64),
        kind=pl.lit("county"),
        source=pl.lit("TX"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas's U.S. House elections of November 7, 2006 in the five districts redrawn by the court and the District 22 unexpired term, by county from the Secretary of State",
            polity="US-TX",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=f"{SITE}index.htm",
            license="Public record",
        )
        for race in RACES:
            cs.artifact(raw.cached(SOURCE, url(race)), source)
        medsl.attach(cs, conn, frame, {"TX": source}, extend=True)
        return cs.write()
