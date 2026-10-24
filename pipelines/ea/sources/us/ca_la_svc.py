import io
import re
import zipfile
from datetime import date
from itertools import pairwise
from pathlib import Path
from urllib.parse import urljoin

import polars as pl

from ea import raw
from ea.sources.us import ca_precincts
from ea.store import DATA

SOURCE = "us.ca_la_svc"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Los Angeles County Registrar-Recorder/County Clerk"
TITLE = "Statement of Votes Cast (Excel)"
LISTING = "https://www.lavote.gov/home/voting-elections/current-elections/election-results/past-election-results"
FIPS = "06037"
METHOD = (
    "Precinct totals from the Los Angeles County Registrar-Recorder/County Clerk's Statement of Votes Cast, matched to "
    "the contest by candidate and checked against its official totals. The county leaves out the selections of "
    "precincts with a single ballot cast, so precinct sums can fall slightly short of the contest's totals."
)


def links() -> list[str]:
    page = raw.fetch(SOURCE, LISTING, suffix=".html").path.read_text(errors="replace")
    heads = [match.start() for match in re.finditer(r">\s*[A-Z][a-z]+ \d{1,2}, \d{4}[^<]{3,160}</", page)]
    found: dict[str, None] = {}
    for start, end in pairwise([*heads, len(page)]):
        for link in re.findall(
            r'href="([^"]*)"[^>]*>\s*(?:<[^>]+>\s*)*Statement of Votes Cast Excel', page[start:end], flags=re.IGNORECASE
        ):
            found[urljoin("https://www.lavote.gov/", link).replace("http://", "https://")] = None
    return list(found)


def fetch() -> None:
    for url in links():
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    rows = []
    for url in links():
        path = raw.cached(SOURCE, url).path
        if not zipfile.is_zipfile(path):
            print(f"not a zip: {url}")
            continue
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if not name.lower().endswith(".xls") and not name.lower().endswith(".xlsx"):
                    continue
                sheet = pl.read_excel(io.BytesIO(archive.read(name)), has_header=False, infer_schema_length=0)
                lines = sheet.rows()
                head = next((i for i, line in enumerate(lines[:8]) if (line[0] or "").strip() == "LOCATION"), None)
                if head is None or sheet.width < 9:
                    continue
                day = date.fromisoformat(str(lines[0][2])[:10])
                candidates = {
                    i: (lines[head][i] or "").strip() for i in range(8, sheet.width) if (lines[head][i] or "").strip()
                }
                for line in lines[head + 1 :]:
                    if (line[6] or "").strip() != "TOTAL" or (line[0] or "").strip().startswith("BALLOT GROUP"):
                        continue
                    for i, candidate in candidates.items():
                        text = (line[i] or "").strip()
                        if not text.lstrip("-").isdigit():
                            continue
                        rows.append(
                            {
                                "day": day,
                                "url": url,
                                "contest": name,
                                "precinct": (line[1] or "").strip(),
                                "registered": int(line[5]) if (line[5] or "").strip().isdigit() else None,
                                "ballots": int(line[7]) if (line[7] or "").strip().isdigit() else None,
                                "candidate": candidate,
                                "votes": int(text),
                            }
                        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "rows.parquet")
    print(f"{len(rows)} rows, {len({(r['url'], r['contest']) for r in rows})} contests")


def emit() -> Path:
    return ca_precincts.emit(
        pipeline=SOURCE,
        version=VERSION,
        summary="Los Angeles County's local contests by precinct from the Statements of Votes Cast",
        rows=pl.read_parquet(PARSED / "rows.parquet"),
        fips=FIPS,
        place="LOS ANGELES",
        publisher=PUBLISHER,
        title=TITLE,
        listing=LISTING,
        method=METHOD,
    )
