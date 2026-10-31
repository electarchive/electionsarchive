import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.sources.us.nv_sos import COUNTIES, PARTIES, WAYBACK, tokens
from ea.store import DATA, connect

SOURCE = "us.nv_judicial"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Nevada Secretary of State"
TITLE = "Official general election results by county: Supreme Court"
FOLDERS = {
    2000: "2000General",
    2002: "2002General",
    2004: "2004General",
    2006: "2006StateWideGeneral",
    2008: "2008StateWideGeneral",
    2010: "2010STatewideGeneral",
    2012: "2012StatewideGeneral",
    2014: "2014StatewideGeneral",
}
HEADING = re.compile(r"(?i)(?:justice of the )?supreme court(?: justice)?,? seat ([A-G])")
METHOD = (
    "County totals come from the Nevada Secretary of State's official county results pages for each general election."
)
MISSING = (
    "The Secretary of State's pages have no row for {counties} in this race; those votes are missing from these totals."
)
CLOSE = (
    "The race was within 0.5% in these totals, so no winner is recorded here; the official canvass and any recount "
    "decided it."
)


def address(year: int, county: str) -> str:
    return f"{WAYBACK}{FOLDERS[year]}/{county}.aspx"


def fetch() -> None:
    def get(url: str) -> None:
        for attempt in range(8):
            try:
                raw.fetch(SOURCE, url, suffix=".html")
                time.sleep(1)
                return
            except httpx.TransportError, httpx.HTTPStatusError:
                time.sleep(10 * (attempt + 1))
        raise RuntimeError(url)

    with ThreadPoolExecutor(1) as pool:
        list(pool.map(get, [address(year, county) for year in FOLDERS for county in COUNTIES]))


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["NV"]
    rows: list[tuple[int, str, str, str | None, int, str]] = []
    for year in FOLDERS:
        for county in COUNTIES:
            words = tokens(raw.cached(SOURCE, address(year, county)).path)
            starts = [index for index, word in enumerate(words) if "Precincts Reporting" in word]
            for position, start in enumerate(starts):
                found = HEADING.fullmatch(words[start - 1])
                if found is None:
                    continue
                post = f"Seat {found[1].upper()}"
                end = starts[position + 1] - 1 if position + 1 < len(starts) else len(words)
                held: list[str] = []
                index = start
                while index < end:
                    word = words[index]
                    if re.fullmatch(r"\d+(?:\.\d+)?%", word):
                        votes = int(words[index + 1].replace(",", ""))
                        held = [text for text in held if text != "Incumbent"]
                        party = None
                        if len(held) > 1 and re.fullmatch(r"[A-Z]{2,4}", held[-1]):
                            party = PARTIES.get(held[-1], "")
                            if party == "" and held[-1] not in {"IND", "NP"}:
                                raise ValueError(f"{year} {county} {post}: unknown party {held[-1]}")
                            held = held[:-1]
                            party = party or None
                        name = held[-1]
                        if not re.match(r"(?i)(under|over) votes", name):
                            rows.append((year, post, name, party, votes, fips[county.lower()]))
                        held = []
                        index += 2
                        continue
                    held.append(word)
                    index += 1
    year = pl.col("year").cast(pl.Int32)
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "post": pl.String,
                "candidate": pl.String,
                "party": pl.String,
                "votes": pl.Int64,
                "county": pl.String,
            },
            orient="row",
        )
        .with_columns(candidate=pl.col("candidate").str.replace(r"^([^,]+), (.+)$", "$2 $1").str.to_uppercase())
        .select(
            year=year,
            day=pl.struct(year=year).map_elements(
                lambda row: date(row["year"], 11, 2 + (-date(row["year"], 11, 1).weekday()) % 7), return_dtype=pl.Date
            ),
            state=pl.lit("NV"),
            title=pl.lit("Justice of the Supreme Court"),
            post="post",
            special=pl.lit(False),
            magnitude=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            code=pl.lit("county"),
            county="county",
            candidate="candidate",
            party="party",
            writein=pl.lit(False),
            measure=pl.lit(1, pl.Int16),
            votes="votes",
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(len(COUNTIES)),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "post", "candidate", "party")
        .agg(pl.col("votes").sum())
        .sort("year", "post", "votes", descending=[False, False, True])
        .rows()
    )


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.nvsos.gov/sos/elections/election-information/election-results-archive",
                license="Public record",
            )
        for year in FOLDERS:
            for county in COUNTIES:
                cs.artifact(raw.cached(SOURCE, address(year, county)), source)
        return {"NV": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Nevada's Supreme Court justices of 2000 through 2014 by county from the Secretary of State's official results",
        cite=cite,
        method=METHOD,
        absent=MISSING,
        close=CLOSE,
    )
