import re
import time
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, tx_elchist
from ea.store import DATA, connect

SOURCE = "us.tx_house"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = tx_elchist.PUBLISHER
TITLE = "Texas election history: U.S. House primaries and runoffs by county, 2000–2014"
PAGE = re.compile(r"^(?:U\. ?S\. Representative District (\d+)|(President/Vice-President))$")
PARTIES = {"DEM": "democratic", "REP": "republican"}


def pages() -> list[tuple[int, int, int | None]]:
    return [
        (election, race, int(found[1]) if found[1] else None)
        for election in tx_elchist.PRIMARIES
        for race, title in tx_elchist.races(election)
        if (found := PAGE.match(title))
    ]


def fetch() -> None:
    for election, race, _ in pages():
        url = f"{tx_elchist.SITE}elchist{election}_race{race}.htm"
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.fetch(SOURCE, url, suffix=".htm")
            time.sleep(1)


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '48%%'"""
            ).fetchall()
        }
    rows: list[tuple[date, str, int | None, str, str, int]] = []
    for election, race, district in pages():
        text = raw.cached(SOURCE, f"{tx_elchist.SITE}elchist{election}_race{race}.htm").path.read_text(errors="replace")
        stamp = re.search(r"<H3[^>]*>(\d+)/(\d+)/(\d{4})</H3>", text, re.IGNORECASE)
        lines = re.findall(r"<TR[^>]*>(.*?)</TR>", text, re.IGNORECASE | re.DOTALL)
        headers = [tx_elchist.cells(line, "TH") for line in lines if re.search(r"<TH", line, re.IGNORECASE)]
        data = [tx_elchist.cells(line, "TD") for line in lines if re.search(r"<TD", line, re.IGNORECASE)]
        if stamp is None or not headers:
            raise ValueError(f"{election} {race}: no heading")
        day = date(int(stamp[3]), int(stamp[1]), int(stamp[2]))
        width = headers[-1].index("Votes")
        counties = [row for row in data if row[0] != "ALL COUNTIES"]
        total = next((row for row in data if row[0] == "ALL COUNTIES"), counties[0] if len(counties) == 1 else None)
        if total is None:
            continue
        for column in range(1, width):
            party = PARTIES.get(headers[-1][column])
            candidate = " ".join(
                row[column] for row in headers[:-1] if column < len(row) and row[column] not in ("...", "Total")
            )
            if party is None or not candidate.strip():
                continue
            votes = [int(row[column].replace(",", "") or 0) for row in counties]
            if sum(votes) != int(total[column].replace(",", "")):
                raise ValueError(f"{election} {race} {candidate}: counties sum to {sum(votes)}, not {total[column]}")
            rows.extend(
                (
                    day,
                    f"{'house' if district else 'president'}:{party}",
                    district,
                    candidate.upper(),
                    fips[re.sub(r"[^a-z]", "", row[0].lower())],
                    value,
                )
                for row, value in zip(counties, votes, strict=True)
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("TX"),
            measure=pl.lit(1, dtype=pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("county"),
            source=pl.lit("TX"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas's 2000–2014 U.S. House and presidential primaries and runoffs by county",
            polity="US-TX",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=tx_elchist.SITE,
            license="Public record",
        )
        for election, race, _ in pages():
            cs.artifact(raw.cached(SOURCE, f"{tx_elchist.SITE}elchist{election}_race{race}.htm"), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"TX": source}, strict=True)
        return cs.write()
