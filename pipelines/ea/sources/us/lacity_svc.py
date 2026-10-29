import re
from datetime import datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import ca_precincts
from ea.store import DATA

SOURCE = "us.lacity_svc"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Office of the City Clerk, City of Los Angeles"
TITLE = "Statement of Votes Cast by Precinct, Vote by Mail and Precinct"
LISTING = "https://clerk.lacity.gov/clerk-services/elections/election-archives"
ARCHIVE = "https://cityclerk.lacity.org//election/archives/archives{year}/{round}/svcptotl.txt"
ELECTIONS = [
    (2007, "primary"),
    (2007, "general"),
    (2009, "primary"),
    (2009, "special"),
    (2011, "primary"),
    (2011, "general"),
    (2013, "primary"),
    (2013, "general"),
    (2015, "primary"),
    (2015, "general"),
    (2017, "general"),
]
FIPS = "06037"
METHOD = (
    "Precinct totals, vote by mail included, from the Los Angeles City Clerk's Statement of Votes Cast by Precinct, "
    "matched to the contest by candidate surname and checked against its official totals."
)
PRECINCT = re.compile(r"^(\d{7}[A-Z])\s+(.*?)\s{2,}([\d,]+(?:\s+[\d,.\-]+)*)\s*$")
SPACED = re.compile(r"^\s*(?:\S {1,2}){3,}\S\s*$")


def fetch() -> None:
    for year, round_ in ELECTIONS:
        raw.fetch(SOURCE, ARCHIVE.format(year=year, round=round_), suffix=".txt")


def parse() -> None:
    rows = []
    for year, round_ in ELECTIONS:
        url = ARCHIVE.format(year=year, round=round_)
        text = raw.cached(SOURCE, url).path.read_text(errors="replace")
        day = None
        for page in re.split(r"\*\*  OFFICIAL [A-Z ]+\*\*", text):
            lines = page.split("\n")
            first = next((i for i, line in enumerate(lines) if PRECINCT.match(line)), None)
            if first is None:
                continue
            head = lines[:first]
            slashes = [i for i, line in enumerate(head) if "\\" in line]
            if not slashes:
                continue
            for line in head[: slashes[0]]:
                spaced = re.fullmatch(r"([A-Z]+)(\d{1,2}),(\d{4})", re.sub(r"\s", "", line))
                if spaced:
                    day = datetime.strptime(" ".join(spaced.groups()), "%B %d %Y").date()
            if day is None or day.year != year:
                raise ValueError(f"{url}: no election date")
            titles = []
            for line in reversed(head[: slashes[0]]):
                if not line.strip():
                    if titles:
                        break
                    continue
                if SPACED.match(line):
                    break
                titles.insert(0, line)
            block = head[slashes[0] : slashes[-1] + 1]
            top, bottom = block[0], block[-1]
            starts = [x for x, char in enumerate(top) if char == "\\"]
            ends = [x for x, char in enumerate(bottom) if char == "\\"]
            contests = []
            for j, start in enumerate(starts):
                stop = starts[j + 1] if j + 1 < len(starts) else None
                text_ = " ".join(line[start:stop].strip() for line in titles if line[start:stop].strip())
                contests.append(re.sub(r"\s*-\s*\(group \d+ of \d+\)$", "", text_))
            names = []
            for x in range(ends[0] + 1, len(bottom)):
                if bottom[x] in " \\":
                    continue
                letters = [
                    block[-1 - k][x - k] if x - k < len(block[-1 - k]) else " " for k in range(len(block)) if x - k >= 0
                ]
                name = "".join(reversed(letters)).split("\\")[-1].strip()
                if not any(char.isalnum() for char in name):
                    continue
                contest = max(j for j, end in enumerate(ends) if end < x)
                names.append((contests[contest], re.sub(r"\s+", " ", name)))
            registered = "%" in bottom[: ends[0]]
            for line in lines[first:]:
                match = PRECINCT.match(line)
                if not match:
                    continue
                tokens = [int(float(token.replace(",", ""))) for token in match[3].split()]
                values = tokens[3:] if registered else tokens
                if len(values) != len(names):
                    raise ValueError(f"{url}: {match[1]} has {len(values)} values for {len(names)} columns")
                for (contest, name), votes in zip(names, values, strict=True):
                    rows.append(
                        {
                            "day": day,
                            "url": url,
                            "contest": contest,
                            "precinct": match[1],
                            "registered": tokens[0] if registered else None,
                            "ballots": tokens[1] if registered else None,
                            "candidate": name,
                            "votes": votes,
                        }
                    )
    frame = pl.DataFrame(rows)
    frame = frame.with_columns(
        pl.col("registered", "ballots").fill_null(strategy="max").over("url", "precinct")
    ).unique(["url", "contest", "precinct", "candidate"], keep="first", maintain_order=True)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "rows.parquet")
    print(f"{frame.height} rows, {frame.select('url', 'contest').n_unique()} contests")


def emit() -> Path:
    return ca_precincts.emit(
        pipeline=SOURCE,
        version=VERSION,
        summary="Los Angeles city elections 2007-2017 by precinct from the City Clerk's Statements of Votes Cast",
        rows=pl.read_parquet(PARSED / "rows.parquet"),
        fips=FIPS,
        place="LOS ANGELES",
        publisher=PUBLISHER,
        title=TITLE,
        listing=LISTING,
        method=METHOD,
    )
