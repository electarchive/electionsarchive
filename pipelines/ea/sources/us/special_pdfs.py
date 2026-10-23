import re
import subprocess

from ea import raw

SOURCE = "us.elstats_specials"
NEBRASKA = "https://sos.nebraska.gov/sites/default/files/doc/elections/2022/Special-Election/2022%20Special%20CG1%20Canvass%20Book.pdf"
KANSAS = "https://www.sos.ks.gov/elections/17elec/2017_Special_Election_Official_Results.pdf"
WISCONSIN = "https://elections.wi.gov/sites/default/files/documents/County%20by%20County%20Report_US%20Congress_Special%20CD%208%20Election.pdf"
SOUTH_DAKOTA = "https://sdsos.gov/elections-voting/election-resources/election-history/2004/2004_special_congressional_election_data_official_returns_special_congressional_election.aspx"
PARTIES = {"D": "Democratic", "R": "Republican", "L": "Libertarian"}


def fetch() -> None:
    for link in (NEBRASKA, KANSAS, WISCONSIN):
        raw.fetch(SOURCE, link, suffix=".pdf")
    raw.fetch(SOURCE, SOUTH_DAKOTA, suffix=".html")


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
        for name, party, votes in (
            ("Mike Flood", "Republican", hit[2]),
            ("Patty Pansing Brooks", "Democratic", hit[3]),
        ):
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
    report = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, WISCONSIN).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    sums = [0, 0, 0]
    for hit in re.finditer(
        r"^([A-Z][A-Z .]+?|\s+Office Totals:)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$", report, re.MULTILINE
    ):
        total, *votes = (int(value.replace(",", "")) for value in hit.groups()[1:])
        if total != sum(votes):
            raise ValueError(f"WI-8 2024 {hit[1].strip()}: the candidates do not add up to the total")
        if "Office Totals" in hit[1]:
            if votes != sums:
                raise ValueError("WI-8 2024: the counties do not add up to the office totals")
            break
        for index, (name, party) in enumerate(
            (("Kristin Lyerly", "Democratic"), ("Tony Wied", "Republican"), ("Scattering", None))
        ):
            sums[index] += votes[index]
            found.append(
                {
                    "state": "WI",
                    "election_date": "2024-11-05",
                    "district_name": "8",
                    "candidate_name": name,
                    "candidate_party_name": party,
                    "division_name": hit[1].title(),
                    "votes": str(votes[index]),
                }
            )
    else:
        raise ValueError("WI-8 2024: no office totals row")
    cells = [
        re.sub(r"\s+", " ", cell).strip()
        for cell in re.findall(r"<td[^>]*>(.*?)</td>", raw.cached(SOURCE, SOUTH_DAKOTA).path.read_text(), re.DOTALL)
    ]
    sums = [0, 0]
    for county, *votes in zip(cells[0::3], cells[1::3], cells[2::3], strict=False):
        if county.startswith("TOTAL:"):
            if [int(re.sub(r"\D", "", value)) for value in votes] != sums:
                raise ValueError("SD 2004 special: the counties do not add up to the totals")
            break
        if not re.fullmatch(r"[A-Z ]+", county):
            continue
        for index, (name, party) in enumerate((("Stephanie Herseth", "Democratic"), ("Larry Diedrich", "Republican"))):
            sums[index] += int(votes[index])
            found.append(
                {
                    "state": "SD",
                    "election_date": "2004-06-01",
                    "district_name": "0",
                    "candidate_name": name,
                    "candidate_party_name": party,
                    "division_name": county.title(),
                    "votes": votes[index],
                }
            )
    else:
        raise ValueError("SD 2004 special: no totals row")
    return found
