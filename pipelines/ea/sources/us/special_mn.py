from datetime import UTC, datetime

from ea import raw
from ea.store import DATA

SOURCE = "us.elstats_specials"
PRIMARY = "https://electionresultsfiles.sos.mn.gov/20220524/USHouseCty.txt"
FILE = DATA / "inbox" / "manual" / "MF-016" / "20220524" / "USHouseCty.txt"
PARTIES = {"R": "Republican", "DFL": "DFL", "GLC": "GLC", "LMN": "LMN"}


def fetch() -> None:
    try:
        raw.cached(SOURCE, PRIMARY)
    except LookupError:
        raw.store(
            SOURCE,
            PRIMARY,
            FILE.read_bytes(),
            suffix=".txt",
            retrieved_at=datetime(2025, 4, 21, 14, 6, 8, tzinfo=UTC),
            media_type="text/plain",
        )


def rows() -> list[dict[str, object]]:
    found = []
    for line in raw.cached(SOURCE, PRIMARY).path.read_text().splitlines():
        cells = line.split(";")
        found.append(
            {
                "state": "MN",
                "election_date": "2022-05-24",
                "district_name": cells[5],
                "candidate_name": cells[7],
                "candidate_party_name": PARTIES[cells[10]],
                "division_name": f"code:27{2 * int(cells[1]) - 1:03d}",
                "votes": cells[13],
                "outcome": "Defeated",
                "runoff": False,
                "primary": PARTIES[cells[10]],
            }
        )
    return found
