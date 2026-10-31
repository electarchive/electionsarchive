import re
import subprocess
from datetime import UTC, datetime

from ea import raw
from ea.store import DATA

SOURCE = "us.elstats_specials"
INBOX = DATA / "inbox" / "manual" / "MF-287"
ARIZONA = "https://azsos.gov/sites/default/files/docs/2025-SE-Primary-Canvass-0731.pdf"
TENNESSEE = {
    "Democratic": "https://sos-prod.tnsosgovfiles.com/s3fs-public/document/20251007DemocraticPrimarybyCounty.pdf",
    "Republican": "https://sos-prod.tnsosgovfiles.com/s3fs-public/document/20251007RepublicanPrimarybyCounty.pdf",
}
ARIZONA_COUNTIES = ("Cochise", "Maricopa", "Pima", "Pinal", "Santa Cruz", "Yuma")
ARIZONA_VOTES = {
    ("Republican", "Daniel Francis Butierez Sr."): (446, 824, 7311, 170, 936, 1434, 11121),
    ("Republican", "Jorge Rivas"): (303, 603, 2871, 78, 282, 457, 4594),
    ("Republican", "Jimmy Rodriguez"): (111, 353, 1580, 32, 154, 319, 2549),
    ("Democratic", "Deja Foxx"): (646, 1122, 10880, 31, 696, 703, 14078),
    ("Democratic", "Adelita Grijalva"): (1888, 2739, 27923, 120, 3087, 2922, 38679),
    ("Democratic", "Patrick Harris Sr."): (35, 109, 622, 7, 68, 84, 925),
    ("Democratic", "Daniel Hernandez"): (326, 1167, 5512, 45, 717, 774, 8541),
    ("Democratic", 'Jose "Malvido" Malvido Jr.'): (19, 36, 525, 5, 48, 54, 687),
}


def fetch() -> None:
    for url, name in [(ARIZONA, "2025-SE-Primary-Canvass-0731.pdf")] + [
        (url, f"20251007{party}PrimarybyCounty.pdf") for party, url in TENNESSEE.items()
    ]:
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = INBOX / name
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def rows() -> list[dict[str, object]]:
    found = []
    for (party, candidate), votes in ARIZONA_VOTES.items():
        if sum(votes[:-1]) != votes[-1]:
            raise ValueError(f"AZ-7 2025 special primary {candidate}: counties do not sum to {votes[-1]}")
        found.extend(
            {
                "state": "AZ",
                "election_date": "2025-07-15",
                "district_name": "7",
                "candidate_name": candidate,
                "candidate_party_name": party,
                "division_name": county,
                "votes": str(count),
                "outcome": "Defeated",
                "runoff": False,
                "primary": party,
            }
            for county, count in zip(ARIZONA_COUNTIES, votes, strict=False)
        )
    for party, url in TENNESSEE.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True, text=True
        ).stdout
        names: dict[int, str] = {}
        for number, name in re.findall(r"(\d+)\. (.+?)(?=\s{2,}|\s*$)", text, flags=re.MULTILINE):
            names[int(number)] = name
        order: list[int] = []
        totals: dict[int, int] = {}
        counties: dict[int, dict[str, int]] = {}
        for line in text.splitlines():
            if hit := re.match(r"^\s{10,}((?:\d+\s+)*\d+)\s*$", line):
                order = [int(number) for number in hit[1].split()]
            elif order and (hit := re.match(r"^(DISTRICT TOTALS|[A-Z][A-Za-z. ]+?)\s{2,}((?:[\d,]+\s*)+)$", line)):
                values = [int(value.replace(",", "")) for value in hit[2].split()]
                if len(values) != len(order):
                    raise ValueError(f"TN-7 2025 special primary {party}: {hit[1]} has {len(values)} values")
                if hit[1] == "DISTRICT TOTALS":
                    totals.update(zip(order, values, strict=True))
                else:
                    for number, value in zip(order, values, strict=True):
                        counties.setdefault(number, {})[hit[1]] = value
        for number, name in names.items():
            if sum(counties[number].values()) != totals[number]:
                raise ValueError(f"TN-7 2025 special primary {name}: counties do not sum to {totals[number]}")
            found.extend(
                {
                    "state": "TN",
                    "election_date": "2025-10-07",
                    "district_name": "7",
                    "candidate_name": name,
                    "candidate_party_name": party,
                    "division_name": county,
                    "votes": str(count),
                    "outcome": "Defeated",
                    "runoff": False,
                    "primary": party,
                }
                for county, count in counties[number].items()
            )
    return found
