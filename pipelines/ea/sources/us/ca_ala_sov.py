import re
from datetime import datetime
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.sources.us import ca_precincts
from ea.store import DATA

SOURCE = "us.ca_ala_sov"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Alameda County Registrar of Voters"
TITLE = "Statement of Votes Cast (Excel)"
HOST = "https://acvote.alamedacountyca.gov"
ARCHIVE = f"{HOST}/election-information/archived-elections"
FILES = [
    "/acvote-assets/02_election_information/PDFs/20180605/en/Results/June 5 2018 Statement of Vote Results Spreadsheet.xls",
    "/acvote-assets/02_election_information/SOV/11082022/Statement of Vote General Election.xlsx",
    "/acvote-assets/02_election_information/SOV/03052024/Statement of Vote Presidential Primary Election.xlsx",
    "/acvote-assets/02_election_information/SOV/20241105/Statement of Vote - General Election.xlsx",
    "/acvote-assets/02_election_information/SOV/20250415/Statement of Vote - Oakland Special Election.xlsx",
    "/acvote-assets/02_election_information/SOV/20251104/Statement of Vote - Statewide Special Election.xlsx",
]
METHOD = (
    "Precinct totals from the Alameda County Registrar of Voters' Statement of Votes Cast, summing its election day "
    "and vote-by-mail reporting rows for each precinct, matched to the contest by candidate and checked against its "
    "official totals."
)
SKIPPED = re.compile(r"(?i)^(over|under) ?votes?$|^total|^turnout|^registered|^ballots cast|^voters cast|^write-?ins?$")


def links() -> list[str]:
    page = raw.fetch(SOURCE, ARCHIVE, suffix=".html").path.read_text(errors="replace")
    found = re.findall(r'href="([^"]*\.xlsx?)"', page)
    return [f"{HOST}{path}".replace(" ", "%20") for path in [*found, *FILES]]


def fetch() -> None:
    for url in links():
        raw.fetch(SOURCE, url, suffix=Path(url).suffix.lower())


def parse() -> None:
    rows = []
    for url in links():
        try:
            workbook = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
        except fastexcel.CalamineError as error:
            print(f"unreadable: {url}: {error}")
            continue
        for index in range(1, len(workbook.sheet_names)):
            sheet = workbook.load_sheet(index, header_row=None, dtypes="string").to_polars()
            lines = sheet.rows()
            head = next(
                (
                    i
                    for i, line in enumerate(lines[:10])
                    if any((c or "").strip().startswith("Registered") for c in line)
                ),
                None,
            )
            if head is None:
                continue
            title = re.sub(r"^\d+ ", "", next((c for c in lines[head - 1] if c), "").strip())
            dated = next(
                (c for line in lines[:head] for c in line if c and re.search(r"[A-Z][a-z]+ \d{1,2}, \d{4}", c)), None
            )
            if not title or dated is None:
                continue
            day = datetime.strptime(re.search(r"[A-Z][a-z]+ \d{1,2}, \d{4}", dated)[0], "%B %d, %Y").date()
            names = {i: (c or "").strip() for i, c in enumerate(lines[head])}
            candidates = {i: n for i, n in names.items() if n and not SKIPPED.match(n)}
            registered = next(i for i, n in names.items() if n.startswith("Registered"))
            cast = next(i for i, n in names.items() if n in ("Ballots Cast", "Voters Cast"))
            for line in lines[head + 1 :]:
                precinct = (line[0] or "").strip()
                kind = (line[1] or "").strip()
                if not precinct or not precinct.isalnum() or "total" in kind.lower():
                    continue
                for i, candidate in candidates.items():
                    text = (line[i] or "").strip().replace(",", "")
                    if text.isdigit():
                        rows.append(
                            {
                                "day": day,
                                "url": url,
                                "contest": f"{index}:{title}",
                                "precinct": precinct,
                                "registered": int(line[registered]) if (line[registered] or "").isdigit() else None,
                                "ballots": int(line[cast]) if (line[cast] or "").isdigit() else None,
                                "candidate": re.sub(r"^[A-Z]{2,3} - ", "", candidate),
                                "votes": int(text),
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
        summary="Alameda County's contests by precinct from the Statements of Votes Cast",
        rows=pl.read_parquet(PARSED / "rows.parquet"),
        fips="06001",
        place="ALAMEDA",
        publisher=PUBLISHER,
        title=TITLE,
        listing=ARCHIVE,
        method=METHOD,
    )
