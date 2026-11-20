"""Reader for the Election Commission of India's statistical reports on the 2004 and 2009 Lok Sabha elections.

The 2004 and 2009 reports changed the layout of the earlier ones (`ea.eci_report`). Volume I's detailed results print
each candidate's votes in three columns (general, postal, and total) and, for 2009, the constituency's electors on its
header line. Volume II holds a summary page per constituency with its electors, voters, valid votes, polling date, and
winner. Volume III holds, for each parliamentary constituency, each candidate's votes in every assembly segment and
the postal ballots, which are counted for the whole constituency.

The readers take text and return the same dataclasses as `ea.eci_report` where they overlap. Nothing here knows the
year, because the two layouts differ only in a few columns.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from ea.eci_report import Candidate, Constituency, _words

STATE_LINE = re.compile(r"^([A-Z][A-Za-z &.,'()/-]*?)\s*$")
HEAD_2004 = re.compile(r"^\s*Constituency\s*:\s*(\d+)\s*\.\s+(.+?)\s*$")
HEAD_2009 = re.compile(r"^\s*CONSTITUENCY\s*:\s*(\d+)\s*\.\s+(.+?)\s*(?:\(\s*Total Electors\s+(\d+)\s*\))?\s*$")
ROW = re.compile(
    r"^\s*(\d+)\s*\.?\s+(\S.*?)\s{2,}(?:([MF])\s+)?(?:(\d+)\s+)?(GEN|SC|ST)\s+(\S.*?)\s+(\d+)\s+(\d+)\s+(\d+)"
    r"(?:\s+\d+\.\d\d\s+\d+\.\d\d)?\s*$"
)
TOTAL = re.compile(r"^\s*TOTAL\s*:\s+(\d+)\s+(\d+)\s+(\d+)")
SUFFIX = re.compile(r"\s*\((?:SC|ST)\)\s*$", re.IGNORECASE)
NOISE = ("GENERAL ELECTIONS", "No.", "SL NO", "Election Commission", "rptDetailed", "PART-", "VALID VOTES", "CANDIDATE")
HEADING = re.compile(
    r"votes secured|over total|elctors in|votes polled in|constituency in|^\s*GENERAL\s+POSTAL|^\s*(Votes Secured|POSTAL)",
    re.IGNORECASE,
)


@dataclass
class Row:
    """A candidate row of the detailed results, with the votes by channel."""

    position: int
    name: str
    sex: str | None
    age: int | None
    category: str
    party: str
    general: int
    postal: int
    total: int


def detailed(lines: list[str]) -> list[Constituency]:
    """Constituencies of the detailed results in either layout. Candidates carry their total votes; the general and
    postal votes are not kept, because volume III prints them again where they matter. `electors` is set where the
    header prints it (2009); the other figures come from volume II."""
    result: list[Constituency] = []
    state = ""
    current: Constituency | None = None
    wrapping = False
    for line in lines:
        if not line.strip():
            continue
        head = HEAD_2009.match(line) or HEAD_2004.match(line)
        if head is not None and line.lstrip().lower().startswith("constituency"):
            name = SUFFIX.sub("", _words(head[2]))
            current = Constituency(state, int(head[1]), name, 1)
            if head.re is HEAD_2009 and head[3]:
                current.electors = int(head[3])
            result.append(current)
            continue
        if current is None:
            if (m := STATE_LINE.match(line)) is not None and not line.startswith((" ", "\t")):
                state = _words(m[1])
            continue
        if (m := re.match(r"^\s*\(\s*Total Electors\s+(\d+)\s*\)\s*$", line)) is not None:
            current.electors = int(m[1])
            continue
        if HEADING.search(line) and not ROW.match(line):
            wrapping = False
            continue
        if (m := ROW.match(line)) is not None:
            wrapping = True
            current.candidates.append(
                Candidate(
                    int(m[1]),
                    _words(m[2]),
                    m[3],
                    m[6].strip(),
                    int(m[9]),
                    None,
                    page=0,
                )
            )
            if int(m[7]) + int(m[8]) != int(m[9]):
                current.caveats.append(f"Candidate {m[1]}'s general and postal votes do not add up to the total.")
            continue
        if TOTAL.match(line):
            current = None
            continue
        stripped = line.strip()
        if line.startswith(NOISE) or stripped.startswith(NOISE):
            continue
        if not line.startswith((" ", "\t")) and STATE_LINE.match(line):
            # A state heading between constituencies of a page.
            state = _words(stripped)
            current = None
            continue
        lead = len(line) - len(line.lstrip())
        if wrapping and current.candidates and lead > 5:
            # The rest of a long name wraps to the next line.
            last = current.candidates[-1]
            last.name = f"{last.name} {_words(stripped)}"
            continue
        if re.match(r"^\s*\(rpt|^\s*\d+\s*$", line):
            continue
        raise ValueError(f"unread line in {state} {current.number} {current.name}: {line.strip()!r}")
    return result


# Volume II: one summary page per constituency.

V2_STATE = re.compile(r"State/UT\s*:\s*(?:(.+?)\s{2,})?Code\s*:\s*(S\d\d|U\d\d)", re.IGNORECASE)
V2_HEAD = re.compile(r"^\s*CONSTITUENCY\s*:\s*(.+?)(?:\s{2,}NO\s*:\s*(\d+))?\s*$", re.IGNORECASE)
V2_NO = re.compile(r"^\s*No\.?\s*:\s*(\d+)\s*$", re.IGNORECASE)
V2_LAST = re.compile(r"\s(\d+)\s*$")
V2_WINNER = re.compile(r"^\s*(?:Winner|WINNER)\s*:?\s+(\S+(?: \S+)*?)\s{2,}(\S.*?)\s{2,}(\d+)\s*$")
V2_DATE = re.compile(r"(\d{1,2})-(\d{1,2}|[A-Za-z]{3})-(\d{4})")
MONTHS: dict[str, int] = {
    m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)
}


@dataclass
class Summary:
    code: str = ""
    number: int = 0
    name: str = ""
    electors: int | None = None
    voters: int | None = None
    valid: int | None = None
    rejected: int | None = None  # rejected postal votes
    poll: date | None = None
    winners: list[tuple[str, str, int]] = field(default_factory=list)


def _day(m: re.Match[str]) -> date:
    month = MONTHS[m[2][:3].lower()] if m[2].isalpha() else int(m[2])
    return date(int(m[3]), month, int(m[1]))


def summaries(lines: list[str]) -> list[Summary]:
    """What volume II prints for each constituency, in the order of the report."""
    result: list[Summary] = []
    code = ""
    number = 0
    current: Summary | None = None
    section = ""
    dates = False
    for line in lines:
        if (m := V2_STATE.search(line)) is not None:
            code = m[2].upper()
            continue
        if (m := V2_NO.match(line)) is not None:
            number = int(m[1])
            continue
        if (m := V2_HEAD.match(line)) is not None:
            n = int(m[2]) if m[2] else number
            current = Summary(code, n, SUFFIX.sub("", _words(m[1])))
            result.append(current)
            section = ""
            continue
        if current is None:
            continue
        stripped = line.strip()
        if re.match(r"^(II|III|IV|V|VI|VII)\.", stripped):
            section = stripped.split(".")[0]
            dates = section == "VI"
            continue
        total = V2_LAST.search(line)
        if section == "II" and re.match(r"^3\.\s*TOTAL\s", stripped) and total:
            current.electors = int(total[1])
        elif section == "III" and re.match(r"^4\.\s*TOTAL\s", stripped) and total:
            current.voters = int(total[1])
        elif section == "IV" and re.match(r"^1\.\s*REJECTED VOTES", stripped) and total:
            current.rejected = int(total[1])
        elif section == "IV" and re.match(r"^3\.\s*TOTAL VALID", stripped) and total:
            current.valid = int(total[1])
        elif dates and (found := V2_DATE.search(line)) is not None and current.poll is None:
            current.poll = _day(found)
        elif section == "VII" and (m := V2_WINNER.match(line)) is not None:
            current.winners.append((m[1], _words(m[2]), int(m[3])))
    return result


# Volume III: the assembly segments of each parliamentary constituency.

V3_STATE = re.compile(r"State-UT Code & Name\s*:\s*(S\d\d|U\d\d)\s*-\s*(.+?)\s*$")
V3_PC = re.compile(r"PC No\. & Name\s*:\s*(\d+)\s*-\s*(.+?)\s*$")
V3_AC = re.compile(r"AC Number and AC Name\s*:\s*(\d+)\s*-\s*(.*?)\s*$")
V3_ROW = re.compile(r"^\s*(\d+)\s+(\S.*?)\s{2,}(\S+(?: \S+)*?)\s{2,}(\d+)\s*$")
V3_AC_TOTAL = re.compile(r"Total Valid Votes for the AC\s*:\s*(\d+)")
V3_POSTAL = re.compile(r"Valid Postal Ballots for each candidate")
V3_POSTAL_TOTAL = re.compile(r"Total Valid Postal Ballots for the PC\s*:?\s*(\d+)")
V3_PC_TOTAL = re.compile(r"Total Valid Votes for the PC\s*:.*?(\d+)\s*$")


@dataclass
class Segment:
    number: int
    name: str
    votes: dict[int, int] = field(default_factory=dict)
    parties: dict[int, str] = field(default_factory=dict)
    total: int | None = None


@dataclass
class Assembly:
    """One parliamentary constituency's assembly segments and postal ballots, by candidate position."""

    code: str
    state: str
    number: int
    name: str
    segments: list[Segment] = field(default_factory=list)
    postal: dict[int, int] = field(default_factory=dict)
    parties: dict[int, str] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)
    postal_total: int | None = None
    total: int | None = None


def assemblies(lines: list[str]) -> list[Assembly]:
    result: list[Assembly] = []
    code, state = "", ""
    current: Assembly | None = None
    segment: Segment | None = None
    postal = False
    for line in lines:
        if (m := V3_STATE.search(line)) is not None:
            code, state = m[1], _words(m[2])
            continue
        if (m := V3_PC.search(line)) is not None:
            current = Assembly(code, state, int(m[1]), SUFFIX.sub("", _words(m[2])))
            result.append(current)
            segment, postal = None, False
            continue
        if current is None:
            continue
        if (m := V3_AC.search(line)) is not None:
            segment = Segment(int(m[1]), _words(m[2]))
            if segment.number:  # a union territory with no assembly is numbered 0 and has no segment rows
                current.segments.append(segment)
            postal = False
            continue
        if V3_POSTAL.search(line):
            segment, postal = None, True
            continue
        if (m := V3_AC_TOTAL.search(line)) is not None and segment is not None:
            segment.total = int(m[1])
            continue
        if (m := V3_POSTAL_TOTAL.search(line)) is not None:
            current.postal_total = int(m[1])
            continue
        if (m := V3_PC_TOTAL.search(line)) is not None:
            current.total = int(m[1])
            continue
        if (m := V3_ROW.match(line)) is not None:
            position, party, votes = int(m[1]), m[3], int(m[4])
            if postal:
                current.postal[position] = votes
            elif segment is not None:
                segment.votes[position] = votes
                segment.parties[position] = party
            current.parties[position] = party
            current.names[position] = _words(m[2])
    return result


def letters(name: str) -> str:
    return re.sub(r"[^A-Z]+", "", name.upper())


def same_state(a: str, b: str) -> bool:
    """Whether two reports' spellings name one state (one of them may be cut at a column)."""
    x, y = letters(a), letters(b)
    return x.startswith(y) or y.startswith(x)


def match(candidates: list[Candidate], parties: dict[int, str], names: dict[int, str]) -> dict[int, Candidate] | None:
    """Pair volume III's candidate numbers with the detailed results' candidates by name, then by party where two names
    are alike (volume III cuts long names, numbers the 2004 ballot differently, and spells some party codes in
    another way). Exact names go first, so a name that is the start of another's keeps its own row. Returns None
    unless every number pairs exactly once."""

    def alike(a: str, b: str) -> bool:
        return a.startswith(b) or b.startswith(a)

    paired: dict[int, Candidate] = {}
    for exact in (True, False):
        for number, party in parties.items():
            if number in paired:
                continue
            word = letters(names[number])
            hits = [
                x
                for x in candidates
                if x not in paired.values() and (letters(x.name) == word if exact else alike(letters(x.name), word))
            ]
            if len(hits) > 1:
                hits = [x for x in hits if alike(x.party, party)] or hits
            if len(hits) == 1:
                paired[number] = hits[0]
            elif not exact:
                break
    if len(paired) == len(parties):
        return paired
    # Repeated names (a namesake on the same ballot): where both reports number the ballot alike, go by number.
    by_position = {x.position: x for x in candidates}
    if all(n in by_position and alike(letters(by_position[n].name), letters(names[n])) for n in parties):
        return {n: by_position[n] for n in parties}
    return None


# Party-wise seats and votes by state, for checking 2009 (volume II prints it as one table with party names only).

P09_ROW = re.compile(r"^\s*(?:(\d+)\s+(\S.*?)\s+)?[NSUZ]\s{2,}(\S.*?)\s{2,}(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+[\d.]+\s*$")


@dataclass(frozen=True)
class StateParty:
    party: str
    state: str
    state_valid: int
    seats: int
    votes: int


def state_parties(lines: list[str]) -> list[StateParty]:
    """Rows of the 2009 party-wise table of seats won and votes polled: the party's name (carried down from the row
    that numbers it), its state, the state's valid votes, and the party's seats and votes there."""
    result: list[StateParty] = []
    party = ""
    for line in lines:
        if (m := P09_ROW.match(line)) is None:
            continue
        if m[2]:
            party = _words(m[2])
        result.append(StateParty(party, _words(m[3]), int(m[4]), int(m[6]), int(m[7])))
    return result
