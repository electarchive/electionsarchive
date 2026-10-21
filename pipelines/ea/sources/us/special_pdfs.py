import re
import subprocess

from ea import raw

SOURCE = "us.elstats_specials"
NEBRASKA = "https://sos.nebraska.gov/sites/default/files/doc/elections/2022/Special-Election/2022%20Special%20CG1%20Canvass%20Book.pdf"
KANSAS = "https://www.sos.ks.gov/elections/17elec/2017_Special_Election_Official_Results.pdf"
PARTIES = {"D": "Democratic", "R": "Republican", "L": "Libertarian"}


def fetch() -> None:
    for link in (NEBRASKA, KANSAS):
        raw.fetch(SOURCE, link, suffix=".pdf")


def rows() -> list[dict[str, object]]:
    found = []
    canvass = subprocess.run(
        ["pdftotext", "-layout", "-f", "6", "-l", "6", str(raw.cached(SOURCE, NEBRASKA).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for line in canvass.splitlines():
        hit = re.match(r"^\s+([A-Z][A-Za-z ]+?)(?: \(\d+ precincts\))?\s+([\d,]+)\s+([\d,]+)\s*$", line)
        if hit is None or hit[1] == "Total":
            continue
        for name, party, votes in (("Mike Flood", "Republican", hit[2]), ("Patty Pansing Brooks", "Democratic", hit[3])):
            found.append(
                {
                    "state": "NE",
                    "election_date": "2022-06-28",
                    "district_name": "1",
                    "candidate_name": name,
                    "candidate_party_name": party,
                    "division_name": hit[1],
                    "votes": votes.replace(",", ""),
                }
            )
    sheet = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, KANSAS).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    for hit in re.finditer(r"([DRL])-([A-Za-z. ]+?)\s+([\d,]+)\s+[\d.]+ %", sheet):
        found.append(
            {
                "state": "KS",
                "election_date": "2017-04-11",
                "district_name": "4",
                "candidate_name": hit[2].strip(),
                "candidate_party_name": PARTIES[hit[1]],
                "division_name": "cd:4",
                "votes": hit[3].replace(",", ""),
            }
        )
    return found
