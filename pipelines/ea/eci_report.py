"""Reader for the Election Commission of India's statistical reports on general elections.

The reports are computer-typeset PDFs with a text layer. Volume I holds the national and state abstracts and, in its
second part, the detailed results of every constituency, with each candidate's party and votes. Volume II holds a
summary page per constituency, with the date of the poll. The state assembly reports share the layout, so the readers
here take text and know nothing of the year.
"""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


def text(path: Path) -> list[str]:
    """The lines of a PDF's text layer, in layout order."""
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True).stdout
    return out.decode("utf-8", "replace").replace("\f", "\n").splitlines()


@dataclass
class Candidate:
    position: int
    name: str
    sex: str | None
    party: str
    votes: int | None
    share: str | None
    page: int = 0
    glued: str | None = None
    uncontested: bool = False


@dataclass
class Constituency:
    state: str
    number: int
    name: str
    seats: int
    candidates: list[Candidate] = field(default_factory=list)
    electors: int | None = None
    voters: int | None = None
    valid: int | None = None
    turnout: str | None = None
    page: int = 0
    caveats: list[str] = field(default_factory=list)


CONSTITUENCY = re.compile(
    r"^\s*Constituency\s*:?\s*(\d+)\s*\.?\s+(.+?)(?:\s{2,}NUMBER OF SEATS\s+(\d+))?\s*$", re.IGNORECASE
)
NAME = r"^\s*(\d+)\s*\.\s+(.+?)\s{2,}"
CANDIDATE = re.compile(NAME + r"(?:([MF])\s{2,})?(\S.*?)\s+(\d+)\s+(\d+(?:\.\d+)?%|#Num!)\s*$")
UNCONTESTED = re.compile(NAME + r"(?:([MF])\s{2,})?(\S.*?)\s+RETURNED UNCONTESTED\s*$")
GLUED = re.compile(NAME + r"([MF])\s+(?:(\S.*?)\s{2,})?(\d+\.\d\d)%\s*$")
ZERO = re.compile(r"^\s*0\s+(?:0\.00%|#Num!)\s*$")
SHARE = re.compile(r"^\s*\d+\.\d\d%\s*$")
TOTALS = re.compile(
    r"^\s*ELECTORS\s*:\s*(\d+)\s+VOTERS\s*:\s*(\d+)\s+POLL PERCENTAGE\s*:\s*([\d.]+)%\s+VALID VOTES\s*:?\s*(\d+)\s*$"
)
TOTALS_LOOSE = re.compile(r"^\s*ELECTORS\s*:\s*(\d+)\s+(\d+)\s+.*VALID VOTES?\s*:?\s*(\d+)\s*$")
HEADERS = ("DETAILED RESULTS", "No.", "GENERAL ELECTIONS", "Election Commission", "rptDetailed", "CANDIDATE")
STATE = re.compile(r"^\s{10,}([A-Za-z][A-Za-z &.,'()/-]*?)\s*$")


def _words(value: str) -> str:
    return " ".join(value.split())


SEX_INSIDE = re.compile(r"^(.+?)\s+([MF])\s+(\S.*)$")


def _split(name: str, sex: str | None, party: str) -> tuple[str, str | None, str]:
    """A name the typesetting set in two columns ("MADAN LAL    S/O PHOOL CHAND") leaves the sex inside the party."""
    if sex is None and (m := SEX_INSIDE.match(party)) is not None:
        return _words(f"{name} {m[1]}"), m[2], _words(m[3])
    return _words(name), sex, _words(party)


def detailed(lines: list[str]) -> list[Constituency]:
    """Every constituency of the report's detailed results, with its candidates and printed totals."""
    start = next(i for i, line in enumerate(lines) if line.strip() == "DETAILED RESULTS" and i > 200)
    result: list[Constituency] = []
    state = ""
    current: Constituency | None = None
    page = 0
    for line in lines[start:]:
        if not line.strip():
            continue
        if line.lstrip().startswith(("rptDetailed", "(rptDetailed")):
            page += 1
            continue
        if any(line.lstrip().startswith(h) for h in HEADERS) and not CONSTITUENCY.match(line):
            continue
        if ZERO.match(line) or SHARE.match(line):
            continue
        if (m := TOTALS.match(line)) is not None:
            if current is None:
                raise ValueError(f"totals without a constituency: {line!r}")
            current.electors, current.voters, current.turnout, current.valid = (
                int(m[1]),
                int(m[2]),
                m[3],
                int(m[4]),
            )
            continue
        if (m := TOTALS_LOOSE.match(line)) is not None:
            if current is None:
                raise ValueError(f"totals without a constituency: {line!r}")
            current.electors, current.voters, current.valid = int(m[1]), int(m[2]), int(m[3])
            continue
        if (m := CONSTITUENCY.match(line)) is not None:
            current = Constituency(state, int(m[1]), _words(m[2]), int(m[3] or 1), page=page)
            result.append(current)
            continue
        if (m := UNCONTESTED.match(line)) is not None:
            if current is None:
                raise ValueError(f"candidate without a constituency: {line!r}")
            name, sex, party = _split(m[2], m[3], m[4])
            current.candidates.append(Candidate(int(m[1]), name, sex, party, None, None, page, uncontested=True))
            continue
        if (m := CANDIDATE.match(line)) is not None:
            if current is None:
                raise ValueError(f"candidate without a constituency: {line!r}")
            share = None if m[6] == "#Num!" else m[6]
            name, sex, party = _split(m[2], m[3], m[4])
            current.candidates.append(Candidate(int(m[1]), name, sex, party, int(m[5]), share, page))
            continue
        if (m := GLUED.match(line)) is not None:
            if current is None:
                raise ValueError(f"candidate without a constituency: {line!r}")
            current.candidates.append(
                Candidate(int(m[1]), _words(m[2]), m[3], _words(m[4] or ""), None, None, page, glued=m[5])
            )
            continue
        indent = len(line) - len(line.lstrip())
        if current is not None and not re.search(r"\d", line) and (line.strip().startswith("(") or indent < 24):
            if current.candidates:
                current.candidates[-1].name += " " + line.strip()
            else:
                current.name += " " + line.strip()
            continue
        if (m := STATE.match(line)) is not None and not re.search(r"\d", line):
            joined = _words(m[1])
            state = f"{state} {joined}" if state.endswith((" OF", " of")) else joined
            continue
        raise ValueError(f"unread line in the detailed results: {line!r}")
    for c in result:
        _unglue(c)
    return result


def _unglue(c: Constituency) -> None:
    """Split a candidate's votes from its share where the typesetting ran them together. A figure the page cuts off
    stays empty, and the constituency records it as a caveat."""
    for candidate in c.candidates:
        if candidate.glued is None:
            continue
        whole, cents = candidate.glued.split(".")
        for k in (1, 2, 3):
            if len(whole) <= k:
                continue
            votes, share = int(whole[:-k]), int(whole[-k:]) + int(cents) / 100
            if c.valid and share <= 100 and abs(votes / c.valid * 100 - share) < 0.011:
                candidate.votes, candidate.share, candidate.glued = votes, f"{share:.2f}%", None
                break
        else:
            candidate.share, candidate.glued = f"{candidate.glued}%", None
            c.caveats.append(
                f"The page cuts off the votes of {candidate.name} (printed share {candidate.share}); they are not stored."
            )


WINNER = re.compile(r"^\s*(\d+)\s*\.\s+(.+?)\s{2,}(\S.*?)\s{2,}(\S+(?: \S+)*)\s*$")
CONTINUED = re.compile(r"^\s{30,}(\S.*?)\s{2,}(\S+(?: \S+)*)\s*$")
LIST_END = re.compile(r"rptListSuccessfulCandidates\s*-\s*(\d+) of (\d+)")


@dataclass(frozen=True)
class Winner:
    state: str
    number: int
    constituency: str
    name: str
    party: str


def successful(lines: list[str], known: dict[tuple[str, int], str] | None = None) -> list[Winner]:
    """The list of successful candidates: one row per seat, a second member on a continued line. `known` names each
    constituency by (upper-case state, number), for rows whose columns the typesetting ran together."""
    heads = [i for i, line in enumerate(lines) if line.strip() == "LIST OF SUCCESSFUL CANDIDATES"]
    start = next(i for i in heads if i > 120)
    result: list[Winner] = []
    state = ""
    number, constituency = 0, ""
    for line in lines[start:]:
        if not line.strip():
            continue
        if (m := LIST_END.search(line)) is not None:
            if m[1] == m[2]:
                break
            continue
        if "LIST OF SUCCESSFUL" in line or line.lstrip().startswith(("CONSTITUENCY", "(rpt")):
            continue
        if re.match(r"^\s*(Election Commission|\d+\s*$)", line):
            continue
        if (m := WINNER.match(line)) is not None:
            number, constituency = int(m[1]), _words(m[2])
            result.append(Winner(state, number, constituency, _words(m[3]), m[4]))
            continue
        if known and (m := re.match(r"^\s*(\d+)\s*\.\s+(\S.*)$", line)) is not None:
            name = known.get((state.upper(), int(m[1])))
            if name is not None:
                words, rest = name.split(), m[2].split()
                used = 0
                while used < len(words) and used < len(rest) and words[used] == rest[used]:
                    used += 1
                rest = rest[used:]
                if used and len(rest) >= 2:
                    number, constituency = int(m[1]), name
                    result.append(Winner(state, number, constituency, " ".join(rest[:-1]), rest[-1]))
                    continue
        if (m := CONTINUED.match(line)) is not None and result and number:
            result.append(Winner(state, number, constituency, _words(m[1]), m[2]))
            continue
        if not re.search(r"\d", line):
            lead = len(line) - len(line.lstrip())
            if result and number and 8 <= lead < 30:
                continue
            if result and number and lead >= 30:
                last = result[-1]
                result[-1] = Winner(
                    last.state, last.number, last.constituency, f"{last.name} {line.strip()}", last.party
                )
                continue
            joined = _words(line)
            wrapped = state.endswith((" OF", " of")) or joined.startswith(("OF ", "of "))
            state = f"{state} {joined}" if wrapped and number == 0 and state else joined
            number = 0
    return result


DATE = re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})")
VOL2_STATE = re.compile(r"STATE/UT\s*:\s*(.+?)\s{2,}CODE", re.IGNORECASE)
VOL2_CONSTITUENCY = re.compile(
    r"^\s*CONSTITUENCY\s*:\s*(.+?)(?:\s{2,}(?:NO|NUMBER OF SEATS)\s*:\s*(\d+))?\s*$", re.IGNORECASE
)
VOL2_WINNER = re.compile(r"^\s*Winner\s*\d*\s+(\S+(?: \S+)*?)\s{2,}(\S.*?)\s{2,}(\d+)\s*$", re.IGNORECASE)


@dataclass
class Summary:
    poll: date | None = None
    printed: tuple[int, int, int] | None = None
    winners: list[tuple[str, str, int]] = field(default_factory=list)


def summaries(lines: list[str]) -> dict[tuple[str, int], Summary]:
    """What volume II's summary page of each constituency prints: the date of the poll and the winners, by
    (upper-case state, constituency number)."""
    state = ""
    key: tuple[str, int] | None = None
    waiting = False
    result: dict[tuple[str, int], Summary] = {}
    for line in lines:
        if (m := VOL2_STATE.search(line)) is not None:
            state = _words(m[1]).upper()
            continue
        if (m := VOL2_CONSTITUENCY.match(line)) is not None:
            number: int | None = int(m[2]) if m[2] is not None and "SEATS" not in line.upper() else None
            if number is None and (lead := re.match(r"\s*(\d+)\s*-", m[1])) is not None:
                number = int(lead[1])
            key = (state, number) if number is not None else None
            if key is not None:
                result[key] = Summary()
            waiting = False
            continue
        if key is None:
            continue
        if "POLLING" in line and "COUNTING" in line:
            waiting = True
            continue
        if waiting and (m := DATE.search(line)) is not None:
            result[key].printed = (int(m[1]), int(m[2]), int(m[3]))
            waiting = False
            continue
        if (m := VOL2_WINNER.match(line)) is not None:
            result[key].winners.append((m[1], _words(m[2]), int(m[3])))
    printed = [v.printed for v in result.values() if v.printed]
    firsts = {a for a, _, _ in printed}
    month_first = (any(b > 12 for _, b, _ in printed) and not any(a > 12 for a in firsts)) or (
        len(firsts) == 1 and len({b for _, b, _ in printed}) > 2
    )
    for v in result.values():
        if v.printed is None:
            continue
        a, b, y = v.printed
        try:
            v.poll = date(y, a, b) if month_first else date(y, b, a)
        except ValueError:
            v.poll = None
    return result


PARTY = re.compile(r"^\s*\d+\s*\.?\s+(\S+(?: \S+)*?)\s{2,}(\S.*?)\s*$")


def parties(lines: list[str]) -> dict[str, str]:
    """Party abbreviation -> name, from the list of participating political parties."""
    start = next(i for i, line in enumerate(lines) if "ABBREVIATION" in line and "PARTY" in line)
    result: dict[str, str] = {}
    for line in lines[start + 1 : start + 400]:
        if "CONSTITUENC" in line.upper() and "TYPES" in line.upper():
            break
        if (m := PARTY.match(line)) is not None and not re.search(r"\s\d+\s*-?\s*\d*$", line):
            result.setdefault(m[1], _words(m[2]))
    return result


NATIONAL = re.compile(r"^\s*\(Contested in[^)]*\)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+[\d.]+%\s+[\d.]+%\s*$")


@dataclass(frozen=True)
class PartyTotal:
    contested: int
    won: int
    forfeited: int
    votes: int


def national(lines: list[str]) -> dict[str, PartyTotal]:
    """The party-wise national summary: seats contested and won, forfeited deposits, and votes, by abbreviation."""
    heads = [i for i, line in enumerate(lines) if line.strip().endswith("VOTES POLLED BY PARTIES - NATIONAL SUMMARY")]
    start = next(i for i in heads if i > 120)
    result: dict[str, PartyTotal] = {}
    previous = ""
    for line in lines[start:]:
        if (m := re.search(r"rptVotesPolledByParties-National Summary - Page (\d+) of (\d+)", line)) is not None:
            if m[1] == m[2]:
                break
            continue
        if not line.strip():
            continue
        if (m := NATIONAL.match(line)) is not None:
            if previous in result:
                raise ValueError(f"party {previous} listed twice in the national summary")
            result[previous] = PartyTotal(int(m[1]), int(m[2]), int(m[3]), int(m[4]))
            previous = ""
            continue
        previous = _words(line)
    return result


STATE_ROW = re.compile(r"^\s+[SU]\d\d\s+(\S.*?)\s{2,}(\d+)\s+(\d+)\s+(\d+)(?:\s+(\d+))?(?:\s+[\d.]+%?)?\s*$")
STATE_HEAD = re.compile(r"^\s{4,}(?:[A-Z]\s{2,})?(\S+(?: \S+)*)\s*$")


def by_state(lines: list[str]) -> dict[tuple[str, str], PartyTotal]:
    """The party-wise state summary: seats contested and won, forfeited deposits, and votes, by (party, upper-case
    state)."""
    heads = [i for i, line in enumerate(lines) if line.strip().endswith("VOTES POLLED BY PARTIES IN STATES/UTs")]
    start = next(i for i in heads if i > 120)
    rows = [line for line in lines[start:] if line.strip()]
    result: dict[tuple[str, str], PartyTotal] = {}
    party = ""
    for i, line in enumerate(rows):
        if re.search(r"rptVotesPolledByParties.*Page (\d+) of \1\b", line) or "VOTES POLLED IN" in line:
            break
        if (m := STATE_ROW.match(line)) is not None and party:
            state = _words(m[1]).upper()
            result[(party, state)] = PartyTotal(int(m[2]), int(m[3]), int(m[4]), int(m[5] or 0))
            continue
        nxt = rows[i + 1] if i + 1 < len(rows) else ""
        before = rows[i - 1] if i else ""
        head = STATE_HEAD.match(line)
        if head and STATE_ROW.match(nxt) and not STATE_ROW.match(before) and not re.search(r"\d{2,}", line):
            party = _words(head[1])
    return result
