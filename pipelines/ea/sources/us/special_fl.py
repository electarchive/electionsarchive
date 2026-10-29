import csv
import io
from datetime import datetime

from ea import raw

SOURCE = "us.elstats_specials"
PRIMARIES = [
    f"https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate={day}&OfficialResults=Y"
    for day in ("2/2/2010", "1/14/2014", "4/22/2014", "11/2/2021", "1/28/2025")
]
PARTIES = {"REP": "Republican", "DEM": "Democratic"}


def fetch() -> None:
    for url in PRIMARIES:
        raw.fetch(SOURCE, url, suffix=".txt")


def rows() -> list[dict[str, object]]:
    found = []
    for url in PRIMARIES:
        lines = csv.DictReader(io.StringIO(raw.cached(SOURCE, url).path.read_text()), delimiter="\t")
        for row in {tuple(row.items()): dict(row) for row in lines if row["RaceCode"] == "USR"}.values():
            found.append(
                {
                    "state": "FL",
                    "election_date": datetime.strptime(row["ElectionDate"], "%m/%d/%Y").strftime("%Y-%m-%d"),
                    "district_name": str(int(row["Juris1num"])),
                    "candidate_name": " ".join(
                        f"{row['CanNameFirst']} {row['CanNameMiddle']} {row['CanNameLast']}".split()
                    ),
                    "candidate_party_name": PARTIES[row["PartyCode"]],
                    "division_name": row["CountyName"],
                    "votes": row["CanVotes"],
                    "outcome": "Defeated",
                    "runoff": False,
                    "primary": PARTIES[row["PartyCode"]],
                }
            )
    return found
