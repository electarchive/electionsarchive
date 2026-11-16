"""Reading the polling-result appendices of the Independent Electoral Commission's general election reports."""

import re
from dataclasses import dataclass, field

HEADER = re.compile(
    r"^\s*(\d{1,3})\s+(\S.*?)\s{2,}(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d.]*)%?\s*$"
)
TIED = re.compile(r"^\s*(\d{1,3})\s+(\S.*?)\s{2,}(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(-|\d[\d.]*)%?\s*$")
CANDIDATE = re.compile(r"^\s+([A-Za-z][^\d]*?)\s+([A-Z][A-Za-z]{1,5})\s+(\d[\d,]*)\s*$")
WALKOVER = re.compile(r"^\s+([A-Za-z][^\d]*?)\s+([A-Z][A-Za-z]{1,5})\s*$")
BARE = re.compile(r"^\s*(\d{1,3})\s+(\S.*?)\s{2,}(\d[\d,]*)\s+(\d[\d,]*)\s*$")
TOTAL = re.compile(r"^\s+(\d[\d,]*)\s*$")
COUNCIL = re.compile(r"^\s*DISTRICT\s*(?:COUNCIL)?\s*:\s*(.+?)\s*$")
CONTINUATION = re.compile(r"^\s{4,}([A-Za-z][A-Za-z.'’\- ]*)$")
BY_HEADER = re.compile(r"^\s+(\d{1,3})\s{2,}([A-Za-z][A-Za-z\-' ./]*?)\s{2,}(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")
BY_CANDIDATE = re.compile(r"^\s{6,}(\S.*?)\s{2,}(\S.*?)\s{2,}([MF])\s+(?:\d{2}\s+)?([A-Z]{2,5})\s+(\d+)\s*$")
SKIP = (
    "page ",
    "Voters",
    "Polling District",
    "Candidate",
    "Constituency",
    "Costituency",
    "Reg.",
    "APPENDIX",
    "BOTSWANA",
    "POLLING RESULTS",
    "RESULTS",
    "and Candidate",
    "t\n",
)


@dataclass
class Unit:
    code: str
    name: str
    council: str
    registered: int
    rejected: int
    cast: int
    margin: int | None
    candidates: list[tuple[str, str, int | None]] = field(default_factory=list)
    valid: int | None = None
    line: int = 0


def number(text: str) -> int:
    return int(text.replace(",", ""))


def units(lines: list[str], first: int, last: int) -> list[Unit]:
    """The units between two line numbers, each with its candidates in printed order."""
    found: list[Unit] = []
    council = ""
    for at in range(first, last):
        line = lines[at].rstrip()
        if not line.strip():
            continue
        match = COUNCIL.match(line)
        if match:
            council = match[1].strip()
            continue
        match = HEADER.match(line)
        if match:
            code, name = match[1], match[2].strip()
            found.append(
                Unit(
                    code,
                    name,
                    council,
                    number(match[3]),
                    number(match[4]),
                    number(match[5]),
                    number(match[6]),
                    line=at + 1,
                )
            )
            continue
        match = BARE.match(line)
        if match:
            found.append(
                Unit(match[1], match[2].strip(), council, number(match[3]), number(match[4]), 0, None, line=at + 1)
            )
            found[-1].cast = -1
            continue
        if not found:
            continue
        match = CANDIDATE.match(line)
        if match and not line.lstrip().startswith(SKIP):
            found[-1].candidates.append((match[1].strip(), match[2], number(match[3])))
            continue
        match = WALKOVER.match(line)
        if match and found[-1].cast == -1 and not found[-1].candidates:
            found[-1].candidates.append((match[1].strip(), match[2], None))
            continue
        match = TOTAL.match(line)
        if match and found[-1].valid is None:
            found[-1].valid = number(match[1])
            continue
        match = CONTINUATION.match(line)
        if match and found[-1].candidates and found[-1].valid is None and not line.strip().startswith(SKIP):
            name, party, votes = found[-1].candidates[-1]
            found[-1].candidates[-1] = (f"{name} {match[1].strip()}", party, votes)
    return found
