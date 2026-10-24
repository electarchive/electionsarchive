import csv
import re
import zipfile
from datetime import datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import ca_precincts
from ea.store import DATA

SOURCE = "us.ca_oc_sov"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Orange County Registrar of Voters"
TITLE = "Statement of Votes, plain-text extract"
HOST = "https://ocvote.gov"
LISTING = f"{HOST}/data/election-results-archives/archived-statement-of-votes"
METHOD = (
    "Precinct totals from the Orange County Registrar of Voters' plain-text extract of the Statement of Votes, "
    "matched to the contest by candidate and checked against its official totals."
)
DATE = re.compile(r"[A-Z][a-z]+ \d{1,2}, ?\d{4}")
UNUSED = re.compile(r"(?i)write-?ins?|^(?:over|under)votes?$|^total")


def links() -> dict[str, str]:
    page = raw.fetch(SOURCE, LISTING, suffix=".html").path.read_text(errors="replace").replace("&nbsp;", " ")
    dates = [(m.start(), m[0]) for m in DATE.finditer(page)]
    found: dict[str, str] = {}
    for match in re.finditer(r'href="([^"]*media\.zip)"', page):
        before = [text for start, text in dates if start < match.start()]
        if before:
            url = match[1] if match[1].startswith("http") else f"{HOST}{match[1]}"
            found[url] = datetime.strptime(before[-1].replace(",", ", ").replace(",  ", ", "), "%B %d, %Y").date()
    return found


def fetch() -> None:
    for url in links():
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    rows = []
    for url, day in links().items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8-sig", errors="replace")
        lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#FormatVersion")]
        delimiter = "\t" if "\t" in lines[0] else ","
        header = [
            name.strip().lstrip(".#").strip().lower() for name in next(csv.reader([lines[0]], delimiter=delimiter))
        ]
        index = {name: i for i, name in enumerate(header)}
        precinct = index.get("precinct", index.get("precinct_name"))
        title = index.get("contest title", index.get("contest_title"))
        contest_id = index.get("contest id", index.get("contest_id"))
        name = index.get("choice name", index.get("choice name1", index.get("candidate_name")))
        registered = index.get("registered voters", index.get("reg_voters"))
        ballots = index.get("ballots cast", index.get("ballots"))
        kind = index.get("candidate_type")
        for line in csv.reader(lines[1:], delimiter=delimiter):
            if len(line) <= max(precinct, title, name):
                continue
            candidate = line[name].strip()
            if UNUSED.search(candidate) or (kind is not None and line[kind].strip() not in ("C", "")):
                continue
            if "total votes" in index:
                votes = line[index["total votes"]].strip()
            else:
                parts = [line[index[c]].strip() for c in ("absentee_votes", "early_votes", "election_votes")]
                votes = str(sum(int(p) for p in parts if p.isdigit()))
            if not votes.isdigit():
                continue
            rows.append(
                {
                    "day": day,
                    "url": url,
                    "contest": f"{line[contest_id].strip()}:{line[title].strip()}",
                    "precinct": line[precinct].strip(),
                    "registered": int(line[registered])
                    if registered is not None and line[registered].strip().isdigit()
                    else None,
                    "ballots": int(line[ballots]) if ballots is not None and line[ballots].strip().isdigit() else None,
                    "candidate": candidate,
                    "votes": int(votes),
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema_overrides={"registered": pl.Int64, "ballots": pl.Int64}).write_parquet(
        PARSED / "rows.parquet"
    )


def emit() -> Path:
    return ca_precincts.emit(
        pipeline=SOURCE,
        version=VERSION,
        summary="Orange County's contests by precinct from the Statements of Votes",
        rows=pl.read_parquet(PARSED / "rows.parquet"),
        fips="06059",
        place="ORANGE",
        publisher=PUBLISHER,
        title=TITLE,
        listing=LISTING,
        method=METHOD,
    )
