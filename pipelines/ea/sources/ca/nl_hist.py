"""The province-wide statistics of the general elections of 1949 to 1971, from the historical statistics at the end of
Elections NL's report on the 2019 general election.

The report prints, for each general election since 1949, the number of districts and seats, each party's candidates
nominated and elected, each party's votes, the rejected ballots, the votes cast, and the registered voters. The
authority's reports hold no poll-by-poll or candidate-level figures for these elections. The figures below are
transcribed from the printed pages 323 to 326, and `check` confirms that every figure occurs in the report's text
layer, in the column of its election.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import date
from typing import Final

from ea import raw
from ea.sources.ca.nl_model import GENERALS, SOURCE

REPORT: Final = GENERALS[2019]
DAYS: Final = {
    1949: date(1949, 5, 27),
    1951: date(1951, 11, 26),
    1956: date(1956, 10, 2),
    1959: date(1959, 8, 20),
    1962: date(1962, 11, 19),
    1966: date(1966, 9, 8),
    1971: date(1971, 10, 28),
}
YEARS: Final = tuple(DAYS)


@dataclass
class Statistics:
    districts: int
    seats: int
    nominated: dict[str, int]  # party code -> candidates nominated
    acclaimed: int
    elected: dict[str, int]  # party code -> candidates elected
    votes: dict[str, int]  # party code -> votes received
    rejected: int
    cast: int  # the printed `Votes Cast (Total)`
    registered: int
    notes: list[str]


def _row(*values: int) -> dict[int, int]:
    return dict(zip(YEARS, values, strict=True))


DISTRICTS: Final = _row(25, 25, 35, 35, 41, 41, 41)
SEATS: Final = _row(28, 28, 36, 36, 42, 42, 42)
NOMINATED: Final = {
    "LIB": _row(28, 28, 35, 32, 42, 42, 42),
    "NDP": _row(0, 0, 0, 18, 6, 3, 19),
    "PC": _row(28, 23, 31, 32, 35, 38, 42),
    "N-A": _row(2, 3, 11, 12, 3, 7, 5),
}
ACCLAIMED: Final = _row(0, 5, 4, 1, 3, 3, 0)
ELECTED: Final = {
    "LIB": _row(22, 24, 32, 31, 34, 39, 20),
    "NDP": _row(0, 0, 0, 0, 0, 0, 0),
    "PC": _row(5, 4, 4, 3, 7, 3, 21),
    "N-A": _row(1, 0, 0, 2, 1, 0, 1),
}
VOTES: Final = {
    "LIB": _row(108802, 83628, 75883, 75560, 72319, 91613, 102775),
    "NDP": _row(0, 0, 0, 9352, 4479, 2725, 4075),
    "PC": _row(55111, 46782, 36591, 33002, 45055, 50316, 118899),
    "N-A": _row(2642, 1156, 1964, 12411, 1348, 3548, 5804),
}
REJECTED: Final = _row(880, 837, 1027, 823, 759, 1169, 2020)
CAST: Final = _row(168435, 132403, 115465, 131148, 123990, 149371, 233573)
REGISTERED: Final = _row(176281, 176281, 189240, 189240, 211921, 239616, 265653)
LABELS: Final = (
    ("Number of Electoral Districts", DISTRICTS),
    ("Number of Seats", SEATS),
    ("LIB Candidates Nominated", NOMINATED["LIB"]),
    ("NDP Candidates Nominated", NOMINATED["NDP"]),
    ("PC Candidates Nominated", NOMINATED["PC"]),
    ("LIB Candidates Elected", ELECTED["LIB"]),
    ("PC Candidates Elected", ELECTED["PC"]),
    ("Liberal Votes Received", VOTES["LIB"]),
    ("NDP Votes Received", VOTES["NDP"]),
    ("PC Votes Received", VOTES["PC"]),
    ("Rejected Ballots", REJECTED),
    ("Votes Cast (Total)", CAST),
    ("Registered Voters (Total)", REGISTERED),
)


def check() -> None:
    """Confirm each transcribed row against the report's text layer, in the column of its election."""
    path = str(raw.cached(SOURCE, REPORT).path)
    text = subprocess.run(["pdftotext", "-layout", path, "-"], check=True, capture_output=True, text=True).stdout
    for label, row in LABELS:
        for line in text.splitlines():
            if line.startswith(label):
                tokens = line[len(label) :].split()
                printed = [0 if t == "-" else int(t.replace(",", "")) for t in tokens]
                if printed[:7] == list(row.values()):
                    break
        else:
            raise ValueError(f"the report does not print {label!r} as {list(row.values())}")


def statistics(year: int) -> Statistics:
    notes = []
    votes = {party: row[year] for party, row in VOTES.items()}
    if sum(votes.values()) + REJECTED[year] != CAST[year]:
        notes.append(
            f"The report prints {CAST[year]:,} votes cast, but the party votes and the rejected ballots it prints "
            f"add to {sum(votes.values()) + REJECTED[year]:,}."
        )
    return Statistics(
        districts=DISTRICTS[year],
        seats=SEATS[year],
        nominated={party: row[year] for party, row in NOMINATED.items()},
        acclaimed=ACCLAIMED[year],
        elected={party: row[year] for party, row in ELECTED.items()},
        votes=votes,
        rejected=REJECTED[year],
        cast=CAST[year],
        registered=REGISTERED[year],
        notes=notes,
    )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
