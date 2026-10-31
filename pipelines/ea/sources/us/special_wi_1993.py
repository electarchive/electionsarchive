from datetime import UTC, datetime

from ea import raw
from ea.store import DATA

SOURCE = "us.elstats_specials"
PAGE = "https://search.library.wisc.edu/digital/A6GO4S5JO5BQTO8M/pages/AKBRCM6TGFISCM9B"
FILE = DATA / "inbox" / "manual" / "MF-018" / "WI-Blue-Book-1993-94_p918_WI-1-special-1993-05-04.pdf"
CANDIDATES = [
    ("Peter W. Barca", "Democratic", (590, 700)),
    ("Edward J. Kozak", "Libertarian", (715, 815)),
    ("Mark Neumann", "Republican", (835, 955)),
    ("Karl Huebner", None, (970, 1045)),
    ("Gary W. Thompson", None, (1065, 1150)),
]
COUNTIES = [
    ("Green", 1340, (535, 5, 855, 4, 4)),
    ("Jefferson", 1357, (100, 0, 136, 1, 2)),
    ("Kenosha", 1375, (16488, 75, 10568, 32, 94)),
    ("Racine", 1393, (18200, 99, 19421, 71, 103)),
    ("Rock", 1411, (15432, 88, 14055, 56, 86)),
    ("Walworth", 1430, (4464, 105, 8965, 37, 36)),
    ("Waukesha", 1448, (386, 3, 930, 2, 2)),
]
TOTALS = (55605, 375, 54930, 203, 327)


def fetch() -> None:
    raw.store(
        SOURCE,
        PAGE,
        FILE.read_bytes(),
        suffix=".pdf",
        retrieved_at=datetime(2026, 10, 21, tzinfo=UTC),
        media_type="application/pdf",
    )


def rows() -> list[dict[str, object]]:
    if [sum(votes[index] for _, _, votes in COUNTIES) for index in range(5)] != list(TOTALS):
        raise ValueError("the county rows do not sum to the printed totals")
    return [
        {
            "state": "WI",
            "election_date": "1993-05-04",
            "district_name": "1",
            "candidate_name": name,
            "candidate_party_name": party,
            "division_name": county,
            "votes": str(votes[index]),
            "page": 1,
            "box": [span[0] / 1245, (top - 9) / 2000, span[1] / 1245, (top + 9) / 2000],
        }
        for county, top, votes in COUNTIES
        for index, (name, party, span) in enumerate(CANDIDATES)
    ]
