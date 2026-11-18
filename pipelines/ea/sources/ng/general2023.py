"""Nigeria's 2023 general election from the Commission's Report of the 2023 General Election.

Annexure 2 is the presidential declaration of 1 March 2023 printed as a picture: each candidate's national total and
the national registered, accredited, cast, valid, and rejected counts. Its numbers are transcribed here with their
boxes on the page (rendered at 300 dpi), and the transcription is checked against the declaration's own sums.
Annexures 3 to 6 give the winning party in each governorship, senatorial district, and federal constituency, and the
elected member, party, and gender in each state constituency.
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ea import changeset, raw
from ea.package import Package
from ea.sources.ng import tables2023
from ea.sources.ng.tables2023 import Seat

ORIGINAL = "https://www.inecnigeria.org/wp-content/uploads/2024/02/2023-GENERAL-ELECTION-REPORT-1.pdf"
ARCHIVED = (
    "https://web.archive.org/web/20260214132338id_/"
    "https://inecnigeria.org/wp-content/uploads/2024/02/2023-GENERAL-ELECTION-REPORT-1.pdf"
)
FEDERAL_DAY = date(2023, 2, 25)
STATE_DAYS = (date(2023, 3, 18), date(2023, 4, 15))
PAGE = 457
WIDTH, HEIGHT = 2481, 3508
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
DIGITIZED = 8


@dataclass(frozen=True)
class Candidate:
    number: int
    name: str
    gender: str
    party: str
    votes: int


# Annexure 2, row by row: ballot number, name as printed, gender, party, and votes received.
CANDIDATES = [
    Candidate(1, "Imumolen Irene Christopher", "M", "A", 61_014),
    Candidate(2, "Almustapha Hamza", "M", "AA", 14_542),
    Candidate(3, "Sowore Omoyele Stephen", "M", "AAC", 14_608),
    Candidate(4, "Kachikwu Dumebi", "M", "ADC", 81_919),
    Candidate(5, "Sani Yabagi Yusuf", "M", "ADP", 43_924),
    Candidate(6, "Tinubu Bola Ahmed", "M", "APC", 8_794_726),
    Candidate(7, "Umeadi Peter Nnanna Chukwudi", "M", "APGA", 61_966),
    Candidate(8, "Ojei Princess Chichi", "F", "APM", 25_961),
    Candidate(9, "Nnadi Charles Osita", "M", "APP", 12_839),
    Candidate(10, "Adenuga Sunday Oluwafemi", "M", "BP", 16_156),
    Candidate(11, "Obi Peter Gregory", "M", "LP", 6_101_533),
    Candidate(12, "Musa Mohammed Rabiu Kwankwaso", "M", "NNPP", 1_496_687),
    Candidate(13, "Osakwe Felix Johnson", "M", "NRM", 24_869),
    Candidate(14, "Abubakar Atiku", "M", "PDP", 6_984_520),
    Candidate(15, "Abiola Latifu Kolawole", "M", "PRP", 72_144),
    Candidate(16, "Adebayo Adewole Ebenezer", "M", "SDP", 80_267),
    Candidate(17, "Ado-Ibrahim Abdulmalik", "M", "YPP", 60_600),
    Candidate(18, "Nwanyanwu Daniel Daberechukwu", "M", "ZLP", 77_665),
]
ELECTED = 6
# The panel under the table, in pixels on the 300 dpi page: measure, value, and box (left, top, right, bottom).
TOTALS = [
    (REGISTERED, 93_469_008, (478, 2858, 782, 2915)),
    (VOTERS, 25_286_616, (1355, 2858, 1630, 2915)),
    (CAST, 24_965_218, (1762, 2858, 2045, 2915)),
    (VALID, 24_025_940, (478, 2995, 772, 3050)),
    (INVALID, 939_278, (880, 2995, 1112, 3050)),
]
COLLECTED_PVCS = 87_209_007
# The votes column: the first row's top edge, the row pitch, and the column's edges, in pixels.
ROW_TOP, ROW_PITCH, ROW_HEIGHT, VOTES_LEFT, VOTES_RIGHT = 952, 97.2, 42, 1560, 1715

# Table 8.15 of the report: the parties that nominated presidential candidates.
PARTY_NAMES = {
    "A": "Accord",
    "AA": "Action Alliance",
    "AAC": "African Action Congress",
    "ADC": "African Democratic Congress",
    "ADP": "Action Democratic Party",
    "APC": "All Progressives Congress",
    "APGA": "All Progressives Grand Alliance",
    "APM": "Allied Peoples' Movement",
    "APP": "Action Peoples Party",
    "BP": "Boot Party",
    "LP": "Labour Party",
    "NNPP": "New Nigeria Peoples Party",
    "NRM": "National Rescue Movement",
    "PDP": "Peoples Democratic Party",
    "PRP": "Peoples Redemption Party",
    "SDP": "Social Democratic Party",
    "YPP": "Young Progressive Party",
    "ZLP": "Zenith Labour Party",
}
REMARKS = {"Elected": "", "Elelcted": "", "Electec": "", "": "", "Court": "Court Order"}

PRESIDENT_METHOD = (
    "The report's annexure 2 reproduces the national declaration of 1 March 2023 as a picture: each of the 18 "
    "candidates' national votes, and the registered voters, accredited voters, votes cast, valid votes, and rejected "
    "votes. The numbers are transcribed from that picture with their boxes on the page; the candidates' votes add up "
    "to the valid votes, and valid and rejected votes add up to the votes cast. The annexure gives no state "
    f"breakdown. It also prints {COLLECTED_PVCS:,} permanent voter cards collected, which the archive has no measure "
    "for."
)
METHOD = (
    "The Commission's 2023 report gives each constituency's declared winning party, with the number of local "
    "government areas, registration areas, and polling units, and no vote counts or candidate names. The outcome is "
    "the party declared the winner, including after supplementary polls."
)
MEMBER_METHOD = (
    "The Commission's 2023 report names the elected member of each state constituency, with party and gender, and "
    "gives no vote counts. The outcome is the member it lists as elected."
)


def path() -> Path:
    return raw.cached("ng.inec", ARCHIVED).path


def fetched() -> raw.Artifact:
    return raw.cached("ng.inec", ARCHIVED)


@dataclass(frozen=True)
class Report:
    governors: list[Seat]
    senate: list[Seat]
    house: list[Seat]
    members: list[Seat]


def read() -> Report:
    file = path()
    found = Report(
        tables2023.governors(file),
        tables2023.districts(file, 4),
        tables2023.districts(file, 5),
        tables2023.members(file),
    )
    checked(found)
    return found


def checked(report: Report) -> None:
    """Fail when the parse or the transcription disagrees with what the report says it holds."""
    if [s.number for s in report.governors] != list(range(1, 29)):
        raise ValueError("annexure 3 does not hold governorships 1 to 28")
    if [s.number for s in report.senate] != list(range(1, 110)):
        raise ValueError("annexure 4 does not hold senatorial districts 1 to 109")
    if sorted(s.number for s in report.house) != list(range(1, 361)):
        raise ValueError("annexure 5 does not hold federal constituencies 1 to 360")
    if sorted(s.number for s in report.members) != list(range(1, 994)):
        raise ValueError("annexure 6 does not hold state constituencies 1 to 993")
    for seat in report.senate + report.house:
        if int(seat.code.split("/")[1]) != seat.number:
            raise ValueError(f"{seat.code} is printed on row {seat.number}")
    unknown = {r for r in (s.remark for s in report.members) if r not in REMARKS}
    if unknown:
        raise ValueError(f"unexpected remarks in annexure 6: {unknown}")
    totals = {measure: value for measure, value, _ in TOTALS}
    if [c.number for c in CANDIDATES] != list(range(1, 19)):
        raise ValueError("the transcription does not hold candidates 1 to 18")
    if sum(c.votes for c in CANDIDATES) != totals[VALID]:
        raise ValueError("the candidates' votes do not add up to the valid votes")
    if totals[VALID] + totals[INVALID] != totals[CAST]:
        raise ValueError("valid and rejected votes do not add up to the votes cast")


def box(left: float, top: float, right: float, bottom: float) -> dict[str, float]:
    return {"x0": left / WIDTH, "y0": top / HEIGHT, "x1": right / WIDTH, "y1": bottom / HEIGHT}


def votes_box(number: int) -> dict[str, float]:
    top = ROW_TOP + ROW_PITCH * (number - 1)
    return box(VOTES_LEFT, top, VOTES_RIGHT, top + ROW_HEIGHT)


def parties(report: Report) -> set[str]:
    return {c.party for c in CANDIDATES} | {
        s.party for s in report.governors + report.senate + report.house + report.members if s.party
    }


def title(text: str) -> str:
    text = re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", text)).strip()
    text = re.sub(r"\b(I+)i\b", lambda m: m[0].upper(), text)
    return text if text != text.upper() else re.sub(r"[A-Za-z]+", lambda m: m[0].capitalize(), text)


def slug(text: str) -> str:
    return changeset.slug(text)


def page_id(p: Package) -> str:
    return p.id("page", "2023-report", PAGE)


def source_page(p: Package, artifact_id: str) -> str:
    id = page_id(p)
    p.add(
        "source_page",
        id=id,
        artifact_id=artifact_id,
        page_number=PAGE,
        image_key=f"pages/ng.inec/{fetched().sha256}-{PAGE}.png",
        width=WIDTH,
        height=HEIGHT,
    )
    return id


def locate(
    p: Package,
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    page: str,
    source: str,
    at: dict[str, float],
) -> None:
    p.add(
        "value_locator",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        page_id=page,
        source_id=source,
        **at,
    )


def value(p: Package, result_set: str, unit: str, option: str | None, measure: int, number: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=DIGITIZED,
        value=number,
    )
