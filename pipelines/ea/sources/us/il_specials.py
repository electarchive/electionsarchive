import html
import re
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.il_specials"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-031"
PUBLISHER = "Illinois State Board of Elections"
TITLE = "Election vote totals by county: {name}"
SITE = "https://www.elections.il.gov/ElectionOperations/ElectionVoteTotalsCounty.aspx"
RACES = {
    "IL-2008-03-08-special-general-CD14-by-jurisdiction.html": (
        date(2008, 3, 8),
        "house",
        14,
        "2008 special general election, 14th Congressional District",
    ),
    "IL-2009-04-07-special-general-CD05-by-jurisdiction.html": (
        date(2009, 4, 7),
        "house",
        5,
        "2009 special general election, 5th Congressional District",
    ),
    "IL-2013-04-09-special-general-CD02-by-jurisdiction.html": (
        date(2013, 4, 9),
        "house",
        2,
        "2013 special general election, 2nd Congressional District",
    ),
    "IL-2010-11-02-special-general-US-Senate-by-jurisdiction.html": (
        date(2010, 11, 2),
        "senate",
        None,
        "2010 special general election, U.S. Senate unexpired term",
    ),
}


def fetch() -> None:
    for name in RACES:
        try:
            raw.cached(SOURCE, f"{SITE}#{name}")
        except LookupError:
            raw.store(
                SOURCE,
                f"{SITE}#{name}",
                (INBOX / name).read_bytes(),
                suffix=".html",
                retrieved_at=datetime.now(UTC),
                media_type="text/html",
            )


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["IL"]
    rows = []
    for name, (day, chamber, district, _) in RACES.items():
        text = raw.cached(SOURCE, f"{SITE}#{name}").path.read_text(errors="replace")
        for part in text.split("<!-- JURISDICTION: ")[1:]:
            county = part.split(" -->", 1)[0].strip()
            flat = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "|", part)))
            flat = re.sub(r"\|(?: ?\|)+", "|", flat)
            start = flat.find("|Candidate|Party|Total Votes|Percent of Total Vote|")
            if start < 0:
                continue
            body, _, tail = flat[start + 50 :].partition("|Vote Totals|")
            total = int(re.search(r"[\d,]+", tail).group().replace(",", ""))
            lines = [
                (candidate, int(votes.replace(",", "")))
                for candidate, _, votes in re.findall(
                    r"([^|]+)\|([^|]+)\|([\d,]+)\|(?:[\d.]+%\|)?", body.strip("|") + "|"
                )
            ]
            if sum(votes for _, votes in lines) != total:
                raise ValueError(f"{name} {county}: candidates sum to {sum(v for _, v in lines)}, not {total}")
            rows.extend((day, chamber, district, county, candidate.strip(), votes) for candidate, votes in lines)
    frame = pl.DataFrame(
        rows, schema=["day", "chamber", "district", "county", "candidate", "votes"], orient="row"
    ).with_columns(
        code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips, default=None)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        state=pl.lit("IL"),
        day=pl.col("day"),
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber=pl.col("chamber"),
        district=pl.col("district").cast(pl.Int16),
        special=pl.lit(True),
        code=pl.col("code"),
        candidate=pl.col("candidate"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.to_uppercase().str.contains("WRITE"),
        votes=pl.col("votes").cast(pl.Int64),
        kind=pl.lit("county"),
        source=pl.lit("IL"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois's special general elections for U.S. House in 2008 (14th), 2009 (5th), and 2013 (2nd) and for the U.S. Senate in 2010, by county",
            polity="US-IL",
        )
        source = cs.source(PUBLISHER, TITLE.format(name="special general elections"))
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE.format(name="special general elections"),
            url=SITE,
            license="Public record",
        )
        for name in RACES:
            cs.artifact(raw.cached(SOURCE, f"{SITE}#{name}"), source)
        medsl.attach(cs, conn, frame, {"IL": source}, extend=True)
        return cs.write()
