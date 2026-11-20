"""The 2003, 2007 and 2011 Newfoundland and Labrador general elections, from Elections NL's reports on them.

Each report prints a summary table of official results for every district (the candidates with their party and votes,
and the district's eligible voters, ballots counted, rejected and cast) and, for each district, a poll-by-poll table of
the same figures with a total row. The layouts differ by year, so the summaries are read by year. The poll tables
share one shape (poll number, place, eligible voters, swear-ins at the polls, one column for each candidate, rejected
and total ballots) and are read together, with every column checked against the summary: the poll rows must add up to
the table's total row, and the total row must equal the district's printed figures.
"""

import difflib
import functools
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from ea import raw
from ea.sources.ca.nl_model import GENERALS, SOURCE, Candidate, District, Election, Poll

DAYS: Final = {2011: date(2011, 10, 11), 2007: date(2007, 10, 9), 2003: date(2003, 10, 21)}
PARTIES: Final = {
    "lib": "LIB",
    "liberal party": "LIB",
    "pc": "PC",
    "progressive conservative party": "PC",
    "ndp": "NDP",
    "new democratic party": "NDP",
    "n-a": "N-A",
    "non-affiliated": "N-A",
    "lab": "LAB",
    "the lab. party": "LAB",
    "the labrador party": "LAB",
}
NAMES: Final = {
    "LIB": "Liberal Party",
    "PC": "Progressive Conservative Party",
    "NDP": "New Democratic Party",
    "N-A": "Non-Affiliated",
    "LAB": "The Labrador Party",
}
# Differences the reports print themselves, keyed by year, district number and the figure that differs.
KNOWN: Final[dict[tuple[int, int | None, str], str]] = {}
FIGURE: Final = re.compile(r"^(?:\d[\d,]*(?:\.\d+)?|-)$")
SMALL: Final = {"of", "de", "au", "and"}


@dataclass
class Row:
    """A summary line split into its cells."""

    left: list[str]
    name: str
    party: str | None
    figures: list[str]
    after: list[str]


@dataclass
class Table:
    """One district's poll-by-poll table as printed, before it is matched to a district."""

    title: str
    polls: list[Poll]
    totals: Poll
    notes: list[str] = field(default_factory=list)


def _text(year: int) -> list[str]:
    path = raw.cached(SOURCE, GENERALS[year]).path
    result = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True)
    return [line.replace("\x0c", "") for line in result.stdout.splitlines()]


def _cells(line: str) -> list[str]:
    return re.split(r"\s{2,}", line.strip())


def _number(token: str) -> int | None:
    return None if token == "-" else int(token.replace(",", ""))


def _figures(cell: str) -> bool:
    return all(FIGURE.match(token) for token in cell.split())


def _party(printed: str | None) -> str | None:
    if printed is None:
        return None
    try:
        return PARTIES[printed.strip().casefold()]
    except KeyError:
        raise ValueError(f"unknown party {printed!r}") from None


def _title(name: str) -> str:
    """A district name in its usual case: `ST. JOHN’S CENTRE` is `St. John’s Centre`."""
    words = []
    for index, word in enumerate(name.split()):
        pieces = []
        for hyphenated in word.split("-"):
            parts = re.split(r"([’'])", hyphenated)
            text = parts[0].capitalize()
            for sign, rest in zip(parts[1::2], parts[2::2], strict=True):
                text += sign + (rest.capitalize() if len(parts[0]) == 1 and rest else rest.lower())
            pieces.append(text)
        text = "-".join(pieces)
        if index and text.lower() in SMALL:
            text = text.lower()
        words.append(text)
    return re.sub(r"(?<=\S)- ", "-", " ".join(words))


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("–", "-")).strip()


def _row(line: str) -> Row | None:
    """A summary line that names a candidate: the cells before the name, the party, the figures and what follows."""
    cells = _cells(line)
    for index, cell in enumerate(cells):
        if "," not in cell or not re.match(r"^\*?[^\W\d_]", cell):
            continue
        merged = re.match(r"^(.*?,.*?[^\d\s])\s+(\d[\d,]*)$", cell)
        name = cell
        tail = cells[index + 1 :]
        if merged:
            name, votes = merged.groups()
            tail = [votes, *tail]
        if re.search(r"\d", name):
            continue
        party = None
        if tail and not _figures(tail[0]):
            party, tail = tail[0], tail[1:]
        figures: list[str] = []
        while tail and _figures(tail[0]):
            figures += tail[0].split()
            tail = tail[1:]
        if figures:
            return Row(cells[:index], name.strip(), party, figures, tail)
    return None


def _candidate(row: Row, votes: int, elected: bool = False) -> Candidate:
    return Candidate(row.name, _party(row.party), votes, elected)


def _discrepancy(year: int, number: int | None, what: str, notes: list[str], got: object, want: object) -> None:
    if got == want:
        return
    text = KNOWN.get((year, number, what))
    if text is None:
        raise ValueError(f"{year} district {number}: {what} is {got}, expected {want}")
    notes.append(text)


# --- poll tables


def _tail(rest: str) -> tuple[str, list[str]]:
    """Split a line's remainder into its place and the figures that end it."""
    cells = _cells(rest)
    tokens: list[str] = []
    while cells and _figures(cells[-1]):
        tokens = cells.pop().split() + tokens
    return " ".join(cells), tokens


def _row_polls(lines: list[str], width: int) -> list[Poll]:
    polls: list[Poll] = []
    pending: tuple[str, str] | None = None
    votes = width - 4
    for line in lines:
        text = line.strip()
        found = re.match(r"^(\d+)\s+(\S.*)$", text)
        if found:
            code, place, figures = found.group(1), *_tail(found.group(2))
        else:
            code = None
            place, figures = _tail(text)
            if not place or not figures:
                continue
        if code is not None and not figures:
            pending = (code, place)
            continue
        if code is None:
            if len(figures) < width - 1 or not place:
                continue
            code, place = pending if pending else (place, place)
            if pending:
                place = f"{pending[1]} {place}"
            pending = None
        else:
            pending = None
        if len(figures) not in (width, width - 1):
            if len(figures) == 1 and "cancel" in place.lower():
                polls.append(Poll(code, place, _number(figures[0]), None, [0] * votes, 0, 0))
                continue
            continue
        values = [_number(token) for token in figures]
        if len(values) == width - 1:
            values.insert(0, None)
        polls.append(
            Poll(
                code,
                place or None,
                values[0],
                values[1],
                [v or 0 for v in values[2 : 2 + votes]],
                values[2 + votes] or 0,
                values[3 + votes] or 0,
            )
        )
    return polls


def _tables(lines: list[str]) -> list[Table]:
    tables: list[Table] = []
    index = 0
    while index < len(lines):
        found = re.search(r"ELECTORAL DISTRICT OF\s+(\S.*?)\s*$", lines[index])
        if not found or not any("olling" in line.lower() for line in lines[index : index + 14]):
            index += 1
            continue
        title = found.group(1)
        end = index + 1
        body: list[str] = []
        totals: list[str] | None = None
        last = 0
        while end < len(lines):
            line = lines[end]
            next_header = re.search(r"ELECTORAL DISTRICT OF\s+(\S.*?)\s*$", line)
            if next_header and next_header.group(1) != title:
                break
            text = line.strip()
            first = re.match(r"^(\d+)\s", text)
            numbered = bool(first) and int(first.group(1)) == last + 1
            if numbered:
                last += 1
            if not numbered and text and not re.search(r"[A-Za-z]", text) and len(text.split()) >= 5 and _figures(text):
                totals = text.split()
                end += 1
                break
            body.append(line)
            end += 1
        if totals is None:
            index = end
            continue
        width = len(totals)
        values = [_number(token) for token in totals]
        count = width - 4
        total = Poll(
            "Total",
            None,
            values[0],
            values[1],
            [v or 0 for v in values[2 : 2 + count]],
            values[2 + count] or 0,
            values[3 + count] or 0,
        )
        if all(t.title != title for t in tables):
            tables.append(Table(title, _row_polls(body, width), total))
        index = end
    return tables


def _same(a: str, b: str) -> bool:
    def clean(text: str) -> str:
        text = text.upper().replace("&", "AND").replace("THE ISLES OF ", "").replace("-", " ")
        return re.sub(r"[^A-Z]", "", text)

    a, b = clean(a), clean(b)
    return a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def _match(names: list[str], tables: list[Table]) -> list[Table | None]:
    """The poll table of each district, in order; a district with none gets None."""
    matched: list[Table | None] = []
    pointer = 0
    for name in names:
        for offset in range(3):
            if pointer + offset < len(tables) and _same(name, tables[pointer + offset].title):
                matched.append(tables[pointer + offset])
                pointer += offset + 1
                break
        else:
            matched.append(None)
    if pointer != len(tables):
        raise ValueError(f"{len(tables) - pointer} poll tables match no district")
    return matched


def _reconcile(year: int, d: District, table: Table | None) -> None:
    if table is None:
        return
    votes = [c.votes for c in d.candidates]
    got = table.totals.votes
    if len(got) != len(votes):
        raise ValueError(f"{year} {d.name}: {len(got)} poll columns for {len(votes)} candidates")
    if got != votes:
        if sorted(got) == sorted(votes) and len(set(votes)) == len(votes):
            order = [got.index(v) for v in votes]
            for poll in [*table.polls, table.totals]:
                poll.votes = [poll.votes[i] for i in order]
        else:
            _discrepancy(year, d.number, "candidate votes in the poll table", d.notes, got, votes)
    _discrepancy(year, d.number, "rejected ballots", d.notes, table.totals.rejected, d.rejected)
    _discrepancy(year, d.number, "ballots cast", d.notes, table.totals.total, d.cast)
    _discrepancy(year, d.number, "eligible voters", d.notes, table.totals.electors, d.electors)
    for position in range(len(votes)):
        _discrepancy(
            year,
            d.number,
            f"poll rows of candidate {position + 1}",
            d.notes,
            sum(p.votes[position] for p in table.polls),
            table.totals.votes[position],
        )
    _discrepancy(
        year, d.number, "poll rows of rejected", d.notes, sum(p.rejected or 0 for p in table.polls), d.rejected
    )
    _discrepancy(year, d.number, "poll rows of total", d.notes, sum(p.total or 0 for p in table.polls), d.cast)
    d.polls = table.polls
    d.totals = table.totals
    d.notes += table.notes
    for poll in table.polls:
        if "cancel" in (poll.description or "").lower():
            d.notes.append(f"Poll {poll.code} ({poll.description}) was cancelled; no ballots were counted.")


# --- 2011


def _officials_2011(lines: list[str]) -> dict[int, str]:
    start = max(i for i, line in enumerate(lines) if line.strip() == "ELECTION OFFICIALS")
    stop = max(i for i, line in enumerate(lines) if line.strip() == "NOMINATED CANDIDATES")
    names: dict[int, str] = {}
    for line in lines[start:stop]:
        cells = _cells(line)
        if len(cells) >= 3 and cells[0].isdigit():
            names[int(cells[0])] = _clean(cells[1])
    return names


def _summary_2011(lines: list[str], tables: list[Table]) -> tuple[list[District], list[int]]:
    start = lines.index("OFFICIAL ELECTION RESULTS")
    stop = next(i for i in range(start, len(lines)) if lines[i].startswith("POLLING DIVISION"))
    names = _officials_2011(lines)
    candidates: list[Row] = []
    tails: list[list[int | None]] = []
    printed: list[int | None] = []
    for line in lines[start:stop]:
        row = _row(line)
        if row is not None:
            numbers = [t for t in row.figures if "." not in t]
            candidates.append(Row(row.left, row.name, row.party, numbers[:1], []))
            if len(numbers) >= 5:
                tails.append([_number(t) for t in numbers[1:5]])
            continue
        tokens = [t for c in _cells(line) for t in (c.split() if _figures(c) else ["x"])]
        figures = [t for t in tokens if t != "x"]
        trailing = []
        for token in reversed(tokens):
            if token == "x":
                break
            trailing.insert(0, token)
        if len(trailing) == 4 and len(figures) == 4:
            printed = [_number(t) for t in trailing]
        elif len(trailing) >= 4 and "." not in trailing[-1]:
            tails.append([_number(t) for t in trailing[-4:]])
    if len(tables) != len(names) or len(tails) != len(tables):
        raise ValueError(f"2011: {len(tables)} tables, {len(names)} districts, {len(tails)} totals")
    districts: list[District] = []
    position = 0
    for number, table in enumerate(tables, start=1):
        size = len(table.totals.votes)
        rows = candidates[position : position + size]
        position += size
        eligible, counted, rejected, cast = tails[number - 1]
        votes = [int(r.figures[0].replace(",", "")) for r in rows]
        if sum(votes) != counted:
            raise ValueError(f"2011 district {number}: the candidates add to {sum(votes)}, not {counted}")
        top = max(votes)
        d = District(number, names[number], [], eligible, counted, rejected, cast, [], None)
        d.candidates = [_candidate(r, v, v == top) for r, v in zip(rows, votes, strict=True)]
        districts.append(d)
    if position != len(candidates):
        raise ValueError(f"2011: {len(candidates) - position} candidate lines are left over")
    return districts, [v or 0 for v in printed]


# --- 2007


def _summary_2007(lines: list[str]) -> tuple[list[District], list[int], list[int]]:
    start = next(i for i, line in enumerate(lines) if "GENERAL ELECTION RESULTS FOR ALL ELECTORAL DISTRICTS" in line)
    delayed = next(i for i, line in enumerate(lines) if "Because of the death of Liberal Candidate" in line)
    stop = next(i for i in range(delayed, len(lines)) if "OVERALL TOTAL" in lines[i])
    blocks: list[tuple[int, list[str], list[Row]]] = []
    printed: dict[str, list[int]] = {}
    for line in lines[start : stop + 2]:
        text = line.strip()
        for label in ("TOTALS:", "OVERALL TOTAL:"):
            if text.startswith(label):
                printed[label] = [int(t.replace(",", "")) for t in text[len(label) :].split()]
        row = _row(line)
        head = re.match(r"^\s*(\d+)\s+([A-Z][^a-z]*?)(?:\s{2,}|$)", line)
        if head and (row is None or row.left):
            blocks.append((int(head.group(1)), [_clean(head.group(2))], []))
            if row is not None:
                blocks[-1][2].append(row)
            continue
        if not blocks:
            continue
        if row is not None:
            blocks[-1][2].append(row)
            fragment = [c for c in row.left if not c.startswith("(")]
        else:
            fragment = [
                c
                for c in _cells(line)
                if c and not c.startswith("(") and c != "SEE BELOW*" and c.isupper() and "TOTAL" not in c
            ]
        blocks[-1][1].extend(_clean(c) for c in fragment)
    districts: dict[int, District] = {}
    for number, parts, rows in blocks:
        if not rows:
            continue
        # The delayed election of district 18 is the one the poll table prints; its first listing has no figures.
        tails = [r for r in rows if len([t for t in r.figures if t != "-"]) >= 5 or len(r.figures) >= 5]
        if len(tails) != 1:
            raise ValueError(f"2007 district {number}: {len(tails)} lines carry the district's figures")
        tail = [_number(t) for t in tails[0].figures[-4:]]
        votes = [_number(r.figures[0]) or 0 for r in rows]
        counted = tail[1]
        notes: list[str] = []
        acclaimed = counted is None
        if acclaimed:
            counted = rejected = cast = None
            notes.append(
                "Liberal candidate Clayton Hobbs withdrew on October 5, 2007, which left Roger Fitzgerald as the "
                "only candidate, acclaimed and declared elected that day; no poll was held, and the advance and "
                "special ballots cast were declared null and void."
            )
        else:
            rejected, cast = tail[2], tail[3]
        if not acclaimed and sum(votes) != counted:
            raise ValueError(f"2007 district {number}: the candidates add to {sum(votes)}, not {counted}")
        top = max(votes)
        d = District(
            number,
            _title(" ".join(parts)),
            [],
            tail[0],
            counted,
            rejected,
            cast,
            [],
            None,
            acclaimed,
            notes,
        )
        if acclaimed:
            d.candidates = [_candidate(r, v, r.name.startswith("FITZGERALD")) for r, v in zip(rows, votes, strict=True)]
        else:
            d.candidates = [_candidate(r, v, v == top) for r, v in zip(rows, votes, strict=True)]
        if number in districts:
            raise ValueError(f"2007 district {number} is listed twice")
        districts[number] = d
    districts[18].notes.append(
        "The October 9, 2007 election in this district was postponed to November 6, 2007 after the death of Liberal "
        "candidate Dr. Gerry Tobin; the figures are those of the November 6 election."
    )
    return [districts[n] for n in sorted(districts)], printed["TOTALS:"], printed["OVERALL TOTAL:"]


# --- 2003


def _summary_2003(lines: list[str]) -> tuple[list[District], list[int]]:
    start = next(i for i, line in enumerate(lines) if "FOR ALL ELECTORAL DISTRICTS" in line)
    stop = next(i for i in range(start, len(lines)) if "RECAPITULATION OF VOTES" in lines[i])
    blocks: list[tuple[int, list[str], list[int | None], list[tuple[Row, str | None]]]] = []
    printed: list[int] = []
    fixed: list[str] = []
    for line in lines[start:stop]:
        cells = _cells(line)
        if cells and cells[0] == "Totals":
            printed = [int(c.replace(",", "")) for c in cells[1:]]
            continue
        row = _row(line)
        if row is None:
            continue
        left = row.left
        head = re.match(r"^(\d+)\.?\s*(.*)$", left[0]) if left else None
        if head:
            fragments = [head.group(2), *[c for c in left[1:] if not _figures(c)]]
            numbers = [_number(t) for c in left[1:] if _figures(c) for t in c.split()]
            blocks.append((int(head.group(1)), [_clean(f) for f in fragments if f], numbers, []))
        elif blocks:
            blocks[-1][1].extend(_clean(c) for c in left if not _figures(c))
        elected = next((c for c in row.after if "," in c), None)
        row.party = next((c for c in row.after if "," not in c), None)
        if row.name == "SACREY, Doris" and row.party is None:
            # The summary leaves her party blank; the poll table's header and the report's party totals give Liberal.
            row.party = "Lib"
            fixed.append(row.name)
        blocks[-1][3].append((row, elected))
    districts: list[District] = []
    for number, parts, numbers, rows in blocks:
        notes = [
            f"The summary leaves the party of {r.name.title()} blank; the poll table's header and the report's party "
            "totals give Liberal."
            for r, _ in rows
            if r.name in fixed
        ]
        eligible, _, cast, rejected = numbers
        votes = [int(r.figures[0].replace(",", "")) for r, _ in rows]
        if rejected is None or cast is None or sum(votes) != cast - rejected:
            raise ValueError(f"2003 district {number}: the candidates add to {sum(votes)} of {cast} cast")
        winners = [r.name for r, elected in rows if elected]
        if len(winners) != 1:
            raise ValueError(f"2003 district {number}: {len(winners)} elected candidates")
        districts.append(
            District(
                number,
                _title(" ".join(parts)),
                [_candidate(r, v, bool(e)) for (r, e), v in zip(rows, votes, strict=True)],
                eligible,
                cast - rejected,
                rejected,
                cast,
                [],
                None,
                notes=notes,
            )
        )
    return districts, printed


# --- checks


EXPECTED: Final = {
    2011: ({"LIB": 42417, "NDP": 54713, "PC": 124523, "N-A": 430}, {"LIB": 6, "NDP": 5, "PC": 37}),
    2007: ({"LIB": 48598, "NDP": 19028, "PC": 155972, "LAB": 68, "N-A": 446}, {"LIB": 3, "NDP": 1, "PC": 44}),
    2003: ({"LIB": 92152, "NDP": 19048, "PC": 162949, "N-A": 998}, {"LIB": 12, "NDP": 2, "PC": 34}),
}


def _check(year: int, districts: list[District], printed: list[int], sums: tuple[int, int, int, int]) -> None:
    got = (
        sum(d.electors or 0 for d in districts),
        sum(d.counted or 0 for d in districts),
        sum(d.rejected or 0 for d in districts),
        sum(d.cast or 0 for d in districts),
    )
    if got != sums:
        raise ValueError(f"{year}: the districts add to {got}, the report prints {sums} ({printed})")
    votes: dict[str, int] = {}
    seats: dict[str, int] = {}
    for d in districts:
        for c in d.candidates:
            code = c.party or "N-A"
            votes[code] = votes.get(code, 0) + c.votes
            if c.elected:
                seats[code] = seats.get(code, 0) + 1
    want_votes, want_seats = EXPECTED[year]
    if year == 2003:
        want_votes = {**want_votes, "LAB": votes.get("LAB", 0)}
    if votes != {k: v for k, v in want_votes.items() if v} and votes != want_votes:
        raise ValueError(f"{year}: party votes {votes}, the report prints {want_votes}")
    if seats != want_seats:
        raise ValueError(f"{year}: seats {seats}, the report prints {want_seats}")


@functools.cache
def _election(year: int) -> Election:
    lines = _text(year)
    tables = _tables(lines)
    if year == 2011:
        districts, printed = _summary_2011(lines, tables)
        sums = (printed[0], printed[1], printed[2], printed[3])
        matched: list[Table | None] = list(tables)
    elif year == 2007:
        districts, main, overall = _summary_2007(lines)
        printed = overall
        sums = (overall[0], overall[1], overall[2], overall[3])
        matched = _match([d.name for d in districts], tables)
        without = [d for d in districts if d.number != 18]
        eligible, counted, rejected, cast = main
        if (
            sum(d.electors or 0 for d in without),
            sum(d.counted or 0 for d in without),
            sum(d.rejected or 0 for d in without),
            sum(d.cast or 0 for d in without),
        ) != (eligible, counted, rejected, cast):
            raise ValueError("2007: the districts of October 9 do not add to the printed totals")
    else:
        districts, printed = _summary_2003(lines)
        sums = (printed[0], printed[4], printed[3], printed[2])
        matched = _match([d.name for d in districts], tables)
        if printed[2] != printed[3] + printed[4]:
            raise ValueError("2003: the printed totals do not add up")
    for d, table in zip(districts, matched, strict=True):
        if table is None and not d.acclaimed:
            raise ValueError(f"{year} {d.name}: no poll table")
        _reconcile(year, d, table)
    _check(year, districts, printed, sums)
    used = {c.party for d in districts for c in d.candidates if c.party}
    legend = {code: NAMES[code] for code in sorted(used)}
    notes = []
    if year == 2007:
        notes.append(
            "The election in Grand Falls-Windsor-Buchans was postponed to November 6, 2007; the report's totals "
            "include it."
        )
    return Election(DAYS[year], "general", GENERALS[year], districts, legend, notes)


def generals() -> dict[int, Election]:
    return {year: _election(year) for year in (2003, 2007, 2011)}
