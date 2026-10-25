import re
import subprocess
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import polars as pl

from ea import raw
from ea.sources.us import ca_precincts
from ea.store import DATA

SOURCE = "us.ca_solano_sov"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Solano County Registrar of Voters"
TITLE = "Precinct results report"
LISTING = "https://www.solanocounty.gov/government/registrar-voters/historic-election-results-files"
PAGE = LISTING + "/{slug}"
HOST = "https://www.solanocounty.gov"
PAGES = {
    date(2016, 6, 7): "2016-june-presidential-primary-election",
    date(2016, 11, 8): "2016-november-presidential-general-election",
    date(2018, 6, 5): "2018-june-primary-election",
    date(2018, 11, 6): "2018-november-general-election",
    date(2020, 3, 3): "2020-march-presidential-primary-election",
    date(2020, 11, 3): "2020-november-presidential-general-election",
    date(2022, 6, 7): "2022-june-primary-election",
    date(2022, 11, 8): "2022-november-general-election",
    date(2024, 3, 5): "march-5-2024-presidential-primary-election",
    date(2024, 11, 5): "november-5-2024-general-election-results",
}
ANCHOR = re.compile(r'<a [^>]*(?:recinct|Pct)[^>]*href="/archival-document\?document=([^"&]+)')
HEADER = re.compile(r"^(\d+)(?: - .+?)?\s{2,}([\d,]+) of ([\d,]+) registered voters")
OLD_HEADER = re.compile(r"^\s*(\d{4}) .+? \d{5}$")
OLD_CANDIDATE = re.compile(r"^\s+(.+?)[ .]{3,}(\d[\d,]*)\s+\d*\.\d+(?:\s|$)")
OLD_REGISTERED = re.compile(r"^\s*REGISTERED VOTERS - TOTAL[ .]+([\d,]+)\s*$")
OLD_BALLOTS = re.compile(r"^\s*BALLOTS CAST - TOTAL[ .]+([\d,]+)")
PARTY = re.compile(r"\s*\([A-Z-]+\)$")
CANDIDATE = re.compile(r"^(.+?)\s{2,}(?:[A-Z]{2,4}\s+)?(\d[\d,]*\s+[\d.]+%.*)$")
AMOUNT = re.compile(r"([\d,]+)\s+[\d.]+%")
UNUSED = re.compile(r"(?i)\(W\)|write-?in|^(?:cast votes|undervotes|overvotes|unresolved)")
METHOD = (
    "Precinct totals from Solano County's precinct results report (election day, vote-by-mail, and provisional "
    "ballots, as the report's own total column), matched to the contest by candidate and checked against its "
    "official totals."
)


def pdfs() -> dict[date, str]:
    found = {}
    for day, slug in PAGES.items():
        html = raw.cached(SOURCE, PAGE.format(slug=slug)).path.read_text(encoding="utf-8")
        link = ANCHOR.search(html)
        if link is not None:
            found[day] = unquote(link[1])
    return found


def fetch() -> None:
    for slug in PAGES.values():
        raw.fetch(SOURCE, PAGE.format(slug=slug), suffix=".html")
    for url in pdfs().values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    rows = []
    for day, url in pdfs().items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=False, capture_output=True, text=True
        ).stdout
        precinct = registered = ballots = contest = None
        previous = ""
        for line in text.splitlines():
            found = HEADER.match(line)
            if found is not None:
                precinct, ballots, registered = found[1], int(found[2].replace(",", "")), int(found[3].replace(",", ""))
            elif (old := OLD_HEADER.match(line)) is not None:
                precinct, registered, ballots, contest = old[1], None, None, None
            elif (count := OLD_REGISTERED.match(line)) is not None:
                registered = int(count[1].replace(",", ""))
            elif (count := OLD_BALLOTS.match(line)) is not None:
                ballots = int(count[1].replace(",", ""))
            elif line.strip().startswith("Vote for"):
                contest = previous
            elif contest is not None and precinct is not None and (old := OLD_CANDIDATE.match(line)) is not None:
                if not UNUSED.match(old[1].strip()) and not re.match(r"(?i)^(?:over|under) votes", old[1].strip()):
                    rows.append(
                        {
                            "day": day,
                            "url": url,
                            "contest": contest,
                            "precinct": precinct,
                            "registered": registered,
                            "ballots": ballots,
                            "candidate": PARTY.sub("", old[1].strip()).strip(" ."),
                            "votes": int(old[2].replace(",", "")),
                        }
                    )
            elif line.startswith("Choice "):
                contest = previous
            else:
                entry = CANDIDATE.match(line.strip())
                if entry is not None and precinct is not None and contest is not None and not UNUSED.match(entry[1]):
                    rows.append(
                        {
                            "day": day,
                            "url": url,
                            "contest": contest,
                            "precinct": precinct,
                            "registered": registered,
                            "ballots": ballots,
                            "candidate": entry[1].strip(),
                            "votes": int(AMOUNT.findall(entry[2])[-1].replace(",", "")),
                        }
                    )
            if line.strip():
                previous = line.strip()
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema_overrides={"registered": pl.Int64, "ballots": pl.Int64}).filter(
        ~pl.col("candidate").str.contains(r"(?i)\(W\)|write-?in")
    )
    frame.write_parquet(PARSED / "rows.parquet")
    print(frame.group_by("day").agg(pl.col("contest").n_unique(), pl.col("precinct").n_unique(), pl.len()).sort("day"))


def emit() -> Path:
    return ca_precincts.emit(
        pipeline=SOURCE,
        version=VERSION,
        summary="Solano County's contests by precinct from the registrar's precinct results reports",
        rows=pl.read_parquet(PARSED / "rows.parquet"),
        fips="06095",
        place="SOLANO",
        publisher=PUBLISHER,
        title=TITLE,
        listing=LISTING,
        method=METHOD,
    )
