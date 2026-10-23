import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.sources.us.tx_elchist import PUBLISHER, SITE, cells
from ea.store import DATA, connect

SOURCE = "us.tx_pres_2012"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2012, 4, 3)
PARTIES = {160: "republican", 161: "democratic"}
TITLE = "Texas election history: 2012 presidential primaries by county"


def url(election: int) -> str:
    return f"{SITE}elchist{election}_race62.htm"


def fetch() -> None:
    for election in PARTIES:
        raw.fetch(SOURCE, url(election), suffix=".htm")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["TX"]
    rows = []
    for election, party in PARTIES.items():
        text = raw.cached(SOURCE, url(election)).path.read_text(errors="replace")
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
                raise ValueError(f"{party} {name}: counties sum to {sum(votes)}, not {total[column]}")
            rows.extend(
                (party, name, county[0], count)
                for county, count in zip(counties, votes, strict=True)
                if int(county[width].replace(",", "") or 0) > 0
            )
    frame = pl.DataFrame(rows, schema=["party", "candidate", "county", "votes"], orient="row").with_columns(
        code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips, default=None)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        year=pl.lit(2012, pl.Int32),
        chamber=pl.concat_str(pl.lit("president:"), pl.col("party")),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("TX"),
        district=pl.lit(None, pl.Int16),
        code=pl.col("code"),
        candidate=pl.col("candidate").str.strip_chars().str.to_uppercase(),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
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
            summary="Texas's 2012 Democratic and Republican presidential primaries (held May 29, 2012) by county from the Secretary of State",
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
        for election in PARTIES:
            cs.artifact(raw.cached(SOURCE, url(election)), source)
        medsl.attach(cs, conn, frame, {"TX": source}, strict=True)
        return cs.write()
