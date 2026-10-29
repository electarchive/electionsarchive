import csv
import io

from ea import raw

SOURCE = "us.elstats_specials"
PRIMARIES = "https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate=1/28/2025&OfficialResults=Y"
PARTIES = {"REP": "Republican", "DEM": "Democratic"}


def fetch() -> None:
    raw.fetch(SOURCE, PRIMARIES, suffix=".txt")


def rows() -> list[dict[str, object]]:
    lines = csv.DictReader(io.StringIO(raw.cached(SOURCE, PRIMARIES).path.read_text()), delimiter="\t")
    found = []
    for row in {tuple(row.items()): dict(row) for row in lines if row["RaceCode"] == "USR"}.values():
        found.append(
            {
                "state": "FL",
                "election_date": "2025-01-28",
                "district_name": str(int(row["Juris1num"])),
                "candidate_name": " ".join(f"{row['CanNameFirst']} {row['CanNameMiddle']} {row['CanNameLast']}".split()),
                "candidate_party_name": PARTIES[row["PartyCode"]],
                "division_name": row["CountyName"],
                "votes": row["CanVotes"],
                "outcome": "Defeated",
                "runoff": False,
                "primary": PARTIES[row["PartyCode"]],
            }
        )
    return found
