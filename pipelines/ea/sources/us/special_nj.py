import re
import subprocess

from ea import raw

SOURCE = "us.elstats_specials"
SPECIALS = [
    (
        "https://www.nj.gov/state/elections/assets/pdf/election-results/2026/2026-official-special-primary-results-us-house-11cd.pdf",
        "2026-02-05",
        "11",
        ("ESSEX", "MORRIS", "PASSAIC"),
    ),
    (
        "https://www.nj.gov/state/elections/assets/pdf/election-results/2024/2024-official-special-primary-results-us-house-10cd.pdf",
        "2024-07-16",
        "10",
        ("ESSEX", "HUDSON", "UNION"),
    ),
]
HEADER = re.compile(r"^([A-Z][A-Z.'()\- ]+?)(?: \(w\))?\s{2,}.*\s(Democratic|Republican)\s*$")


def fetch() -> None:
    for url, *_ in SPECIALS:
        raw.fetch(SOURCE, url, suffix=".pdf")


def rows() -> list[dict[str, object]]:
    found = []
    for url, day, district, names in SPECIALS:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True, text=True
        ).stdout
        county = re.compile(rf"^\s+({'|'.join(names)})\s.*?\s([\d,]+)\s*$")
        candidate = party = None
        counties: dict[str, int] = {}
        for line in text.splitlines():
            if hit := HEADER.match(line):
                name = re.sub(r"\bMc([a-z])", lambda found: "Mc" + found[1].upper(), hit[1].title())
                if name != candidate:
                    candidate, party, counties = name, hit[2], {}
            elif hit := county.match(line):
                counties[hit[1].title()] = int(hit[2].replace(",", ""))
            elif candidate is not None and (hit := re.match(r"^\s+Total\s+([\d,]+)\s*$", line)):
                if sum(counties.values()) != int(hit[1].replace(",", "")):
                    raise ValueError(
                        f"NJ-{district} {day} special primary {candidate}: counties do not sum to {hit[1]}"
                    )
                found.extend(
                    {
                        "state": "NJ",
                        "election_date": day,
                        "district_name": district,
                        "candidate_name": candidate,
                        "candidate_party_name": party,
                        "division_name": name,
                        "votes": str(votes),
                        "outcome": "Defeated",
                        "runoff": False,
                        "primary": party,
                    }
                    for name, votes in counties.items()
                )
                candidate = None
    return found
