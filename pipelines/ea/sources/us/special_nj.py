import re
import subprocess

from ea import raw

SOURCE = "us.elstats_specials"
RESULTS = "https://www.nj.gov/state/elections/assets/pdf/election-results/2026/2026-official-special-primary-results-us-house-11cd.pdf"
HEADER = re.compile(r"^([A-Z][A-Z.'()\- ]+?)(?: \(w\))?\s{2,}.*\s(Democratic|Republican)\s*$")
COUNTY = re.compile(r"^\s+(ESSEX|MORRIS|PASSAIC)\s.*?\s([\d,]+)\s*$")


def fetch() -> None:
    raw.fetch(SOURCE, RESULTS, suffix=".pdf")


def rows() -> list[dict[str, object]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, RESULTS).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    found = []
    candidate = party = None
    counties: dict[str, int] = {}
    for line in text.splitlines():
        if hit := HEADER.match(line):
            if hit[1].title() != candidate:
                candidate, party, counties = hit[1].title(), hit[2], {}
        elif hit := COUNTY.match(line):
            counties[hit[1].title()] = int(hit[2].replace(",", ""))
        elif candidate is not None and (hit := re.match(r"^\s+Total\s+([\d,]+)\s*$", line)):
            if sum(counties.values()) != int(hit[1].replace(",", "")):
                raise ValueError(f"NJ-11 2026 special primary {candidate}: counties do not sum to {hit[1]}")
            found.extend(
                {
                    "state": "NJ",
                    "election_date": "2026-02-05",
                    "district_name": "11",
                    "candidate_name": candidate,
                    "candidate_party_name": party,
                    "division_name": county,
                    "votes": str(votes),
                    "outcome": "Defeated",
                    "runoff": False,
                    "primary": party,
                }
                for county, votes in counties.items()
            )
            candidate = None
    return found
