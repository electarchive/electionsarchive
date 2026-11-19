"""The scanned reports of the Director of Elections for Jamaica's parliamentary elections before 2002.

The reports have no text layer. Their constituency tables were read from the page images and are kept as one JSON file
per election in `scans/`: each constituency's electors, ballots cast, rejected ballots, and every candidate with party
and votes, as printed, with the PDF page they were read from.
"""

import functools
import json
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent / "scans"
BASE = "https://www.ecj.com.jm/wp-content/uploads/2017/12"
BROWSER = "browser://20261117-100011-851ef3"

# The report each election's constituency tables were read from, by election day. A browser:// file is one saved from
# the authority's site by hand, whose page the saved copy does not name.
FILES: dict[str, tuple[str, ...]] = {
    "1944-12-14": (f"{BROWSER}/19441214generalsummary.pdf",),
    "1949-12-20": (f"{BROWSER}/19491220generalsummary.pdf",),
    "1955-01-12": (f"{BROWSER}/19550112generalsummary.pdf",),
    "1959-07-28": (f"{BROWSER}/19590728generalsummary.pdf",),
    "1962-04-10": (f"{BROWSER}/19620410generalsummary.pdf",),
    "1967-02-21": (f"{BROWSER}/19670221generalsummary.pdf",),
    "1972-02-29": (f"{BROWSER}/19720229generalsummary.pdf",),
    "1976-12-15": (f"{BASE}/1976GeneralElectionSummary.pdf",),
    "1980-10-30": (f"{BASE}/1980GeneralElectionSummary.pdf",),
    "1983-12-15": (f"{BASE}/1983GeneralElectionSummary.pdf",),
    "1989-02-09": (f"{BASE}/1989GeneralElectionBoxbyBox.pdf",),
    "1993-03-30": (f"{BASE}/1993GeneralElectionSummary.pdf", f"{BASE}/1993GeneralElectionBoxbyBox.pdf"),
    "1997-12-18": (f"{BASE}/1997GeneralElectionBoxbyBox.pdf",),
}
DAYS = tuple(sorted(FILES))
# Parties whose full names the reports print; the others are known by the abbreviation alone.
PARTY_NAMES = {
    "JDP": "Jamaica Democratic Party",
    "AIP": "Agricultural Industrial Party",
    "UPJ": "United Party of Jamaica",
    "NLP": "National Labour Party",
    "FP": "Farmers' Party",
    "RIGHT": "Right Party",
    "PFM": "People's Freedom Movement",
    "PPP": "People's Political Party",
    "JUP": "Jamaica United Party",
    "JAWEP": "Jamaica We Party",
    "REPUBLICAN": "Republican Party",
    "CDP": "Christian Democratic Party",
    "CCM": "Christian Conscience Movement",
    "JUF": "Jamaica United Front",
    "CIP": "Convention Independent Party",
    "JIM": "Jamaica Independent Movement",
    "ILP": "Independent Labour Party",
    "IL": "Independent Labour",
}
# Candidates of "other parties" that the 1944 report does not name; they belong to no single party.
UNAFFILIATED = frozenset(("OTH",))


@dataclass
class Candidate:
    name: str
    party_printed: str
    party: str
    votes: int | None
    pct: float | None = None


@dataclass
class Constituency:
    number: int
    name: str
    page: int
    stats_page: int
    electors: int | None
    cast: int | None
    rejected: int | None
    accepted: int | None
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Report:
    day: str
    file: str
    title: str
    constituencies: list[Constituency]
    notes: list[str]


@functools.cache
def read(day: str) -> Report:
    raw = json.loads((HERE / f"{day}.json").read_text(encoding="utf-8"))
    return Report(
        raw["day"],
        raw["file"],
        raw["title"],
        [
            Constituency(
                c["number"],
                c["name"],
                c["page"],
                c["stats_page"],
                c["electors"],
                c["cast"],
                c["rejected"],
                c["accepted"],
                [
                    Candidate(x["name"], x["party_printed"], x["party"], x["votes"], x.get("pct"))
                    for x in c["candidates"]
                ],
            )
            for c in raw["constituencies"]
        ],
        raw["notes"],
    )


# The box-by-box reports, whose polling-station tables `boxes/` holds for the constituencies whose reading passed every
# check, by election day.
BOXFILES: dict[str, str] = {
    "1976-12-15": f"{BASE}/1976GeneralElectionBoxbyBox.pdf",
    "1980-10-30": f"{BASE}/1980GeneralElectionBoxbyBox.pdf",
    "1983-12-15": f"{BASE}/1983GeneralElectionBoxbyBox.pdf",
    "1989-02-09": f"{BASE}/1989GeneralElectionBoxbyBox.pdf",
    "1993-03-30": f"{BASE}/1993GeneralElectionBoxbyBox.pdf",
    "1997-12-18": f"{BASE}/1997GeneralElectionBoxbyBox.pdf",
}
BOXES = Path(__file__).parent / "boxes"


@dataclass
class Station:
    code: str
    page: int
    votes: list[int]
    rejected: int
    total: int
    electors: int | None
    boxes: list[list[float] | None]
    inferred: bool


@functools.cache
def stations(day: str) -> dict[int, list[Station]]:
    """The polling stations of each constituency whose box-by-box table was read and checked, by its number."""
    path = BOXES / f"{day}.json"
    if day not in BOXFILES or not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        c["number"]: [
            Station(
                s["code"],
                s["page"],
                s["votes"],
                s["rejected"],
                s["total"],
                s["electors"],
                s["boxes"],
                bool(s.get("inferred")),
            )
            for s in c["stations"]
        ]
        for c in raw["constituencies"]
    }


@functools.cache
def own_totals(day: str) -> frozenset[int]:
    """The constituencies whose stations add up to the box-by-box report's own totals line, not to the summary's."""
    path = BOXES / f"{day}.json"
    if day not in BOXFILES or not path.exists():
        return frozenset()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(c["number"] for c in raw["constituencies"] if c.get("own_totals"))


LOCATORS = Path(__file__).parent / "locators"


@functools.cache
def locators(day: str) -> dict[int, dict[str, list[float]]]:
    """Where each figure of a constituency is printed, by its number: the figure's key, then page and box.

    The keys are `electors`, `cast`, `accepted`, `rejected`, and `votes:<position>`; the box is normalized to the page.
    Figures that could not be found on the page have no entry.
    """
    path = LOCATORS / f"{day}.json"
    if not path.exists():
        return {}
    return {int(number): figures for number, figures in json.loads(path.read_text(encoding="utf-8")).items()}
