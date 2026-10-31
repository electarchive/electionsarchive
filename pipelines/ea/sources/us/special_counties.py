import re
import subprocess

from ea import raw

SOURCE = "us.elstats_specials"
MISSISSIPPI = "https://www.sos.ms.gov/elections/electionresults/{}/certified%20results/US%20House,%20Congressional%20District%201%20Certification.pdf"
MISSISSIPPI_ROUNDS = [("2015Special", "2015-05-12", False), ("2015SpecialRunoff", "2015-06-02", True)]
RHODE_ISLAND = "https://elections.ri.gov/sites/g/files/xkgbur756/files/2023-12/Congress%201%20Sumarry.pdf"
RHODE_ISLAND_COUNTIES = {
    "Barrington": "Bristol",
    "Bristol": "Bristol",
    "Warren": "Bristol",
    "Jamestown": "Newport",
    "Little Compton": "Newport",
    "Middletown": "Newport",
    "Newport": "Newport",
    "Portsmouth": "Newport",
    "Tiverton": "Newport",
}
OKLAHOMA = "https://oklahoma.gov/content/dam/ok/en/elections/election-results/2002-election-results/2002-general-election-results/02cd01.pdf"
OKLAHOMA_SECTIONS = [
    ("2001-12-11", "Democratic", [("James E. Lamkin", "Democratic"), ("Doug Dodd", "Democratic")]),
    (
        "2001-12-11",
        "Republican",
        [
            ("Scott Pruitt", "Republican"),
            ("Evelyn L. Rogers", "Republican"),
            ("Cathy Keating", "Republican"),
            ("John Sullivan", "Republican"),
            ("George E. Banasky", "Republican"),
        ],
    ),
    (
        "2002-01-08",
        None,
        [("John Sullivan", "Republican"), ("Doug Dodd", "Democratic"), ("David Fares", None), ("Neil Mavis", None)],
    ),
]


def text(link: str, *flags: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", *flags, str(raw.cached(SOURCE, link).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def fetch() -> None:
    for folder, _, _ in MISSISSIPPI_ROUNDS:
        raw.fetch(SOURCE, MISSISSIPPI.format(folder), suffix=".pdf")
    raw.fetch(SOURCE, RHODE_ISLAND, suffix=".pdf")
    raw.fetch(SOURCE, OKLAHOMA, suffix=".pdf")


def links() -> dict[str, list[str]]:
    return {
        "MS": [MISSISSIPPI.format(folder) for folder, _, _ in MISSISSIPPI_ROUNDS],
        "RI": [RHODE_ISLAND],
        "OK": [OKLAHOMA],
    }


def rows() -> list[dict[str, object]]:
    found = []
    for folder, day, runoff in MISSISSIPPI_ROUNDS:
        county = ""
        for line in text(MISSISSIPPI.format(folder)).splitlines():
            hit = re.match(r"^(\S+)?\s+(\S.*?)\s+([\d,]+)\s+(?:[\d.]+%|#DIV/0!)\s*$", line)
            if hit is None:
                continue
            if hit[1] is not None:
                county = hit[1]
            if county == "Totals":
                break
            found.append(
                {
                    "state": "MS",
                    "election_date": day,
                    "district_name": "1",
                    "candidate_name": hit[2].strip(),
                    "candidate_party_name": None,
                    "division_name": county,
                    "votes": hit[3].replace(",", ""),
                    "runoff": runoff,
                }
            )
    for day in {row["election_date"] for row in found}:
        totals: dict[str, int] = {}
        for row in found:
            if row["election_date"] == day:
                totals[row["candidate_name"]] = totals.get(row["candidate_name"], 0) + int(row["votes"])
        leaders = sorted(totals, key=lambda name: -totals[name])[: 1 if day == "2015-06-02" else 2]
        for row in found:
            if row["election_date"] == day:
                row["outcome"] = (
                    ("Elected" if day == "2015-06-02" else "Runoff") if row["candidate_name"] in leaders else "Defeated"
                )
    town = ""
    for line in text(RHODE_ISLAND).splitlines():
        header = re.match(r"^Special Election 2023\s{2,}(\S.*?)\s*$", line)
        if header is not None:
            town = header[1]
            continue
        hit = re.match(r"^(Gabriel Amo|Gerry W\. Leonard, Jr\.|Write-In Totals)\s+([\d,]+)\s+[\d.]+%", line)
        if hit is None:
            continue
        found.append(
            {
                "state": "RI",
                "election_date": "2023-11-07",
                "district_name": "1",
                "candidate_name": "Write-ins" if hit[1].startswith("Write") else hit[1],
                "candidate_party_name": {"Gabriel Amo": "Democratic", "Gerry W. Leonard, Jr.": "Republican"}.get(
                    hit[1]
                ),
                "division_name": RHODE_ISLAND_COUNTIES.get(town, "Providence"),
                "votes": hit[2].replace(",", ""),
            }
        )
    sheet = text(OKLAHOMA)
    for (day, primary, candidates), block in zip(
        OKLAHOMA_SECTIONS, re.split(r"UNITED STATES REPRESENTATIVE, DISTRICT 1", sheet)[1:], strict=True
    ):
        for line in block.splitlines():
            hit = re.match(r"^(TULSA|WAGONER)\s+([\d,\s]+)$", line)
            if hit is None:
                continue
            counts = hit[2].replace(",", "").split()
            for (name, party), count in zip(candidates, counts, strict=False):
                found.append(
                    {
                        "state": "OK",
                        "election_date": day,
                        "district_name": "1",
                        "candidate_name": name,
                        "candidate_party_name": party,
                        "division_name": hit[1].title(),
                        "votes": count,
                        "primary": primary,
                    }
                )
    return found
