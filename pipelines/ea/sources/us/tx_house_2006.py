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
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
RACES = {
    (128, 77): (date(2006, 11, 7), 15),
    (128, 83): (date(2006, 11, 7), 21),
    (128, 84): (date(2006, 11, 7), 22),
    (128, 85): (date(2006, 11, 7), 23),
    (128, 87): (date(2006, 11, 7), 25),
    (128, 90): (date(2006, 11, 7), 28),
    (129, 85): (date(2006, 12, 12), 23),
}
TITLE = "Texas election history: 2006 special November elections by county"


def url(election: int, race: int) -> str:
    return f"{SITE}elchist{election}_race{race}.htm"


def fetch() -> None:
    for election, race in RACES:
        raw.fetch(SOURCE, url(election, race), suffix=".htm")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["TX"]
    rows = []
    for (election, race), (day, district) in RACES.items():
        text = raw.cached(SOURCE, url(election, race)).path.read_text(errors="replace")
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
                (day, district, name, county[0], count)
                for county, count in zip(counties, votes, strict=True)
                if int(county[width].replace(",", "") or 0) > 0
            )
    frame = pl.DataFrame(rows, schema=["day", "district", "candidate", "county", "votes"], orient="row").with_columns(
        code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips, default=None)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        state=pl.lit("TX"),
        day=pl.col("day"),
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
            summary="Texas's U.S. House elections of November 7, 2006 in the five districts redrawn by the court and the District 22 unexpired term, and the December 12 District 23 runoff, by county from the Secretary of State",
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
        for election, race in RACES:
            cs.artifact(raw.cached(SOURCE, url(election, race)), source)
        medsl.attach(cs, conn, frame, {"TX": source}, extend=True)
        return cs.write()
