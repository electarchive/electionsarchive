import io
import re
import subprocess
import zipfile
from datetime import UTC, datetime

from ea import raw
from ea.store import DATA

SOURCE = "us.elstats_specials"
INBOX = DATA / "inbox" / "manual" / "MF-288"
WISCONSIN = "https://elections.wi.gov/elections/election-results/results-all#County%20by%20County%20Report_Representative%20in%20Congress%20District%207_Spring%20Primary_2_18_20.pdf"
OREGON = "https://sos.oregon.gov/elections/Pages/electionsstatistics.aspx#2011%20Special%20Primary%20Election%20Official%20Results.PDF"
ARKANSAS = "https://www.sos.arkansas.gov/elections/research/election-results#2001_Special_Primary_US_Congress_District_3.pdf"
ARKANSAS_RUNOFFS = {
    "Democratic": "https://www.sos.arkansas.gov/elections/research/election-results#runoff24-dem.zip",
    "Republican": "https://www.sos.arkansas.gov/elections/research/election-results#runoff24-rep.zip",
}
FILES = {
    WISCONSIN: INBOX / "2020-02-18" / "County%20by%20County%20Report_Representative%20in%20Congress%20District%207_Spring%20Primary_2_18_20.pdf",
    OREGON: INBOX / "2011-11-08" / "2011 Special Primary Election Official Results.PDF",
    ARKANSAS: INBOX / "2001" / "2001_Special_Primary_US_Congress_District_3.pdf",
    ARKANSAS_RUNOFFS["Democratic"]: INBOX / "2001" / "runoff24-dem.zip",
    ARKANSAS_RUNOFFS["Republican"]: INBOX / "2001" / "runoff24-rep.zip",
}
WISCONSIN_CANDIDATES = {
    "DEMOCRATIC": ("Democratic", ["Tricia Zunker", "Lawrence Dale", "Scattering"]),
    "REPUBLICAN": ("Republican", ["Tom Tiffany", "Jason Church", "Michael Opela", "Scattering"]),
}
OREGON_CANDIDATES = {
    "Democrat": ("Democratic", ["Dominic Hammon", "Suzanne Bonamici", "Brad Witt", "Saba Ahmed", "Brad Avakian", "Dan Strite", "Robert E Lettin", "Todd Lee Ritter", "Scattering"]),
    "Republican": ("Republican", ["Lisa Michaels", "DR Delgado-Morgan", "Pavel Goberman", "Jim Greenfield", "Rob Cornilles", "Scattering"]),
}
ARKANSAS_CANDIDATES = {
    "Boozman": "John Boozman",
    "Cates": "Brad Cates",
    "DeLay": "Gunner DeLay",
    "Hendren": "Jim Hendren",
    "Carson": "Jo Carson",
    "Hathorn": "Mike Hathorn",
    "Williams": 'Norman "Bill" Williams Jr.',
}


def fetch() -> None:
    for url, path in FILES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=path.suffix.lower(),
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/zip" if path.suffix == ".zip" else "application/pdf",
            )


def text(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True, text=True
    ).stdout


def rows() -> list[dict[str, object]]:
    found = []

    def add(state: str, day: str, district: str, party: str, candidate: str, county: str, votes: int) -> None:
        found.append(
            {
                "state": state,
                "election_date": day,
                "district_name": district,
                "candidate_name": candidate,
                "candidate_party_name": party,
                "division_name": county,
                "votes": str(votes),
                "outcome": "Defeated",
                "runoff": False,
                "primary": party,
            }
        )

    party = None
    totals: dict[str, int] = {}
    tally: dict[str, list[int]] = {}
    for line in text(WISCONSIN).splitlines():
        if hit := re.match(r"^REPRESENTATIVE IN CONGRESS DISTRICT 7 - (\w+)", line):
            party = hit[1]
            tally = {}
        elif party in WISCONSIN_CANDIDATES and (hit := re.match(r"^\s*(Office Totals:|[A-Z][A-Z. ]+?)\s+([\d,]+(?:\s+[\d,]+)+)\s*$", line)):
            label, names = hit[1], WISCONSIN_CANDIDATES[party][1]
            values = [int(value.replace(",", "")) for value in hit[2].split()]
            if len(values) != len(names) + 1 or sum(values[1:]) != values[0]:
                raise ValueError(f"WI-7 2020 primary {party} {label}: {values}")
            if label == "Office Totals:":
                for index, name in enumerate(names):
                    if sum(row[index] for row in tally.values()) != values[index + 1]:
                        raise ValueError(f"WI-7 2020 primary {party} {name}: counties do not sum")
                party = None
            else:
                tally[label] = values[1:]
                for name, count in zip(names, values[1:], strict=True):
                    add("WI", "2020-02-18", "7", WISCONSIN_CANDIDATES[party][0], name, label.title(), count)

    party = None
    tally = {}
    for line in text(OREGON).splitlines():
        if line.strip() in OREGON_CANDIDATES:
            party, tally = line.strip(), {}
        elif party is not None and (hit := re.match(r"^\s*([A-Za-z]+|TOTAL)\s+((?:[\d,]+\s*)+)$", line)):
            names = OREGON_CANDIDATES[party][1]
            values = [int(value.replace(",", "")) for value in hit[2].split()]
            if len(values) != len(names):
                raise ValueError(f"OR-1 2011 primary {party} {hit[1]}: {values}")
            if hit[1] == "TOTAL":
                for index, name in enumerate(names):
                    if sum(row[index] for row in tally.values()) != values[index]:
                        raise ValueError(f"OR-1 2011 primary {party} {name}: counties do not sum")
                party = None
            else:
                tally[hit[1]] = values
                for name, count in zip(names, values, strict=True):
                    add("OR", "2011-11-08", "1", OREGON_CANDIDATES[party][0], name, hit[1], count)

    party = None
    columns: list[str] = []
    tally = {}
    seen: set[str] = set()
    for line in text(ARKANSAS).splitlines():
        if hit := re.match(r"^\s*(Republican|Democrat) Special Primary\s*$", line):
            party, columns, tally = hit[1], [], {}
        elif party is not None and (hit := re.match(r"^County\s+(.+?)\s*$", line)):
            columns = hit[1].split()
        elif party is not None and columns and (hit := re.match(r"^(?:(\w+) County|Total)\s+((?:\d+\s*)+)$", line)):
            values = [int(value) for value in hit[2].split()]
            if len(values) != len(columns):
                raise ValueError(f"AR-3 2001 special primary {party} {hit[1]}: {values}")
            if hit[1] is None:
                if party in seen:
                    continue
                for index, column in enumerate(columns):
                    if sum(row[index] for row in tally.values()) != values[index]:
                        raise ValueError(f"AR-3 2001 special primary {column}: counties do not sum")
                seen.add(party)
                party = None
            else:
                tally[hit[1]] = values
                for column, count in zip(columns, values, strict=True):
                    add("AR", "2001-09-25", "3", "Democratic" if party == "Democrat" else party, ARKANSAS_CANDIDATES[column], hit[1], count)
    for party, url in ARKANSAS_RUNOFFS.items():
        with zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, url).path.read_bytes())) as archive:
            lines = archive.read("runoff24.txt").decode("latin-1").replace("|Office|", "|\nOffice|").splitlines()
        counties: list[str] = []
        runoff: dict[str, list[int]] = {}
        for line in lines:
            cells = line.rstrip("|").split("|")
            if cells[0] == "Office":
                counties = [cell.removesuffix(" County") for cell in cells[3:]]
            elif len(cells) == len(counties) + 3:
                runoff[re.sub(r"^State (Senator|Representative) ", "", cells[1])] = [int(cell) for cell in cells[3:]]
        winner = max(runoff, key=lambda name: sum(runoff[name]))
        for name, values in runoff.items():
            for county, count in zip(counties, values, strict=True):
                add("AR", "2001-10-23", "3", party, name, county, count)
                found[-1]["runoff"] = True
                found[-1]["outcome"] = "Elected" if name == winner else "Defeated"
    for row in found:
        if row["state"] == "AR" and not row["runoff"]:
            totals = {}
            for other in found:
                if other["state"] == "AR" and other["primary"] == row["primary"]:
                    totals[other["candidate_name"]] = totals.get(other["candidate_name"], 0) + int(str(other["votes"]))
            row["outcome"] = "Runoff" if row["candidate_name"] in sorted(totals, key=totals.__getitem__)[-2:] else "Defeated"
    return found
