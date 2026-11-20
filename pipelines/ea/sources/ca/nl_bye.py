"""Newfoundland and Labrador by-elections from 2007 to 2024, by polling division, from Elections NL's reports.

Each report prints a recapitulation of votes: one row per polling division (numbered regular polls, then advance polls
and the special ballot poll) and a total row. The columns of the 2007 to 2019 reports are the final list, the electors
sworn in at the polls, one column per candidate, rejected ballots and total ballots; the candidates' names sit in the
table header and are read from it by position. The 2020 and 2024 reports put the candidates first and add cancelled
ballots and the registered voters, and their official results block names the candidates and their parties.
"""

import functools
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from typing import Final

from ea import raw
from ea.sources.ca.nl_model import BY_ELECTION_FILES, SOURCE, Candidate, District, Election, Poll

PARTIES: Final = {
    "LIB": "LIB",
    "LIBERAL": "LIB",
    "PC": "PC",
    "NDP": "NDP",
    "NLA": "NL ALL",
    "NL ALL": "NL ALL",
    "NL ALLIANCE": "NL ALL",
    "N-A": "N-A",
    "IND": "N-A",
    "INDEPENDENT": "N-A",
    "THE LABRADOR PARTY": "LAB",
}
LEGEND: Final = {
    "LIB": "Liberal Party",
    "PC": "Progressive Conservative Party",
    "NDP": "New Democratic Party",
    "NL ALL": "NL Alliance",
    "N-A": "Non-Affiliated",
    "LAB": "The Labrador Party",
}
PARTY_NAMES: Final = {
    "Liberal Party": "LIB",
    "Progressive Conservative Party": "PC",
    "New Democratic Party": "NDP",
    "Independent": "N-A",
    "NL Alliance": "NL ALL",
}
HEADER_WORDS: Final = re.compile(
    r"^(NO\.?|POLL|POLLING|DIVISION|ELIGIBLE|VOTERS|REVISED|SWEAR[- ]?(?:INS?)?|INS?|LIST|AT|POLLS|REJECTED|TOTAL|BALLOTS|"
    r"COUNTED|NUMBER|OF|\d{4}|#)$",
    re.IGNORECASE,
)
NUMBER: Final = re.compile(r"^(?:\d[\d,]*|-|_+|—|–)$")
# The reports: url suffix -> (by-election day, district, the candidates of the official results block when the
# table does not name them as the older reports do).
FILES: Final[dict[str, list[tuple[date, str]]]] = {
    "07.ferryland-february8.pdf": [(date(2007, 2, 8), "Ferryland")],
    "07.kilbride-february8.pdf": [(date(2007, 2, 8), "Kilbride")],
    "07.portauport-february8.pdf": [(date(2007, 2, 8), "Port au Port")],
    "07.humbervalley-february12.pdf": [(date(2007, 2, 12), "Humber Valley")],
    "07.labradorwest-march13.pdf": [(date(2007, 3, 13), "Labrador West")],
    "08-cape-st.francis-and-baie-verte-springdale-aug-27.pdf": [
        (date(2008, 8, 27), "Cape St. Francis"),
        (date(2008, 8, 27), "Baie Verte - Springdale"),
    ],
    "09-swbn-by-election.pdf": [(date(2009, 10, 27), "The Straits - White Bay North")],
    "09-tn-by-election.pdf": [(date(2009, 11, 26), "Terra Nova")],
    "10.topsailbyelectionreport.pdf": [(date(2010, 3, 16), "Topsail")],
    "10-cbe-bi-by-election-report.pdf": [(date(2010, 12, 2), "Conception Bay East - Bell Island")],
    "11-hw-by-election-report.pdf": [(date(2011, 2, 15), "Humber West")],
    "13-clac-by-election-report-plain.pdf": [(date(2013, 6, 25), "Cartwright - L'Anse au Clair")],
    "13-chg-by-election-report-plain.pdf": [(date(2013, 11, 26), "Carbonear - Harbour Grace")],
    "14-vw-by-election-report-plain-final.pdf": [(date(2014, 4, 9), "Virginia Waters")],
    "14-sgse-by-election-report-plain.pdf": [(date(2014, 8, 26), "St. George's - Stephenville East")],
    "14-cbs-by-election-report-plain.pdf": [(date(2014, 11, 5), "Conception Bay South")],
    "17.mountpearlnorth.pdf": [(date(2017, 11, 21), "Mount Pearl North")],
    "18.windsorlake.pdf": [(date(2018, 9, 20), "Windsor Lake")],
    "19.topsailparadise.pdf": [(date(2019, 1, 24), "Topsail - Paradise")],
    "oceohumbergrosmornebyelectionreport2020.pdf": [(date(2020, 10, 6), "Humber - Gros Morne")],
    "conceptionbayeast-bellislandby-electionreport.pdf": [(date(2024, 1, 30), "Conception Bay East - Bell Island")],
    "fogoisland-capefreelsby-electionreport.pdf": [(date(2024, 4, 15), "Fogo Island - Cape Freels")],
    "baieverte-greenbayby-electionreport.pdf": [(date(2024, 5, 27), "Baie Verte - Green Bay")],
    "waterfordvalleyby-electionreport2024.pdf": [(date(2024, 8, 22), "Waterford Valley")],
}
# The candidates the 2008 report's mixed-case header garbles, and those of the reports whose table header wraps names
# across rotated cells: printed name, party code, votes the official results block prints.
KNOWN: Final[dict[str, list[tuple[str, str, int]]]] = {
    "08-cape-st.francis-and-baie-verte-springdale-aug-27.pdf Cape St. Francis": [
        ("CONNORS, Kathleen", "NDP", 0),
        ("PARSONS, Kevin", "PC", 0),
        ("POWER-MERCER, Tonia", "LIB", 0),
    ],
    "08-cape-st.francis-and-baie-verte-springdale-aug-27.pdf Baie Verte - Springdale": [
        ("HOWSE, Tim", "NDP", 0),
        ("LANE, Shaun", "LIB", 0),
        ("POLLARD, Kevin", "PC", 0),
    ],
    "oceohumbergrosmornebyelectionreport2020.pdf Humber - Gros Morne": [
        ("DOWNEY-SUTTON, Graham", "NDP", 121),
        ("FUREY, Andrew", "LIB", 3401),
        ("GOOSNEY, Mike", "PC", 1332),
        ("PELLEY, Graydon", "NL ALL", 464),
    ],
    "baieverte-greenbayby-electionreport.pdf Baie Verte - Green Bay": [
        ("BURT, Owen J", "LIB", 1035),
        ("HARNETT, Riley", "NDP", 96),
        ("PADDOCK, Lin", "PC", 4271),
    ],
    "fogoisland-capefreelsby-electionreport.pdf Fogo Island - Cape Freels": [
        ("BLACKMORE, Dana", "LIB", 2244),
        ("GILL, Jim", "NDP", 169),
        ("MCKENNA, Jim", "PC", 3290),
    ],
    "conceptionbayeast-bellislandby-electionreport.pdf Conception Bay East - Bell Island": [
        ("CHURCHILL, Kim", "NDP", 846),
        ("HARDING, Darryl J.", "N-A", 70),
        ("HUTTON, Fred", "LIB", 2603),
        ("NEARY, Tina", "PC", 2152),
    ],
    "waterfordvalleyby-electionreport2024.pdf Waterford Valley": [
        ("BOLAND, Nicole", "NDP", 1027),
        ("KORAB, Jamie", "LIB", 2067),
        ("WILKINS, Jesse", "PC", 1423),
    ],
}
# A table header's party that the report's own list of candidates contradicts: (name, printed party) -> party.
PARTY_FIX: Final[dict[str, dict[tuple[str, str], str]]] = {
    "19.topsailparadise.pdf Topsail - Paradise": {("HYNES-COATES, Patricia", "NDP"): "LIB"}
}
CANDIDATES_FIRST: Final = {key.split()[0] for key in KNOWN if not key.startswith("08-")}


@dataclass
class Row:
    code: str
    label: str
    numbers: list[int | None]


def _value(token: str) -> int | None:
    return int(token.replace(",", "")) if token[0].isdigit() else None


def _split(line: str) -> tuple[str, list[str]]:
    """A line's text before its trailing run of numbers, and that run."""
    tokens = line.split()
    count = 0
    while count < len(tokens) and NUMBER.match(tokens[-1 - count]):
        count += 1
    return " ".join(tokens[: len(tokens) - count]), tokens[len(tokens) - count :]


def _lines(url: str) -> list[str]:
    done = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    )
    return done.stdout.splitlines()


def _url(suffix: str) -> str:
    return next(url for url in BY_ELECTION_FILES if url.endswith(suffix))


def _segment(lines: list[str], suffix: str, district: str) -> list[str]:
    """The part of a report that holds one district's table: the whole report, or, in a report of two by-elections, the
    stretch from that district's recapitulation heading to the next."""
    if suffix != "08-cape-st.francis-and-baie-verte-springdale-aug-27.pdf":
        return lines
    key = "cape st" if district.startswith("Cape") else "baie verte"
    starts = [
        i
        for i, line in enumerate(lines)
        if "electoral district of" in line.casefold() and "ballots counted" in " ".join(lines[i : i + 8]).casefold()
    ]
    begin = next((i for i in starts if key in lines[i].casefold()), None)
    if begin is None:
        raise ValueError(f"{district}: no recapitulation heading")
    end = next((i for i in starts if i > begin and key not in lines[i].casefold()), len(lines))
    return lines[begin:end]


def _table(lines: list[str], front: bool, count: int | None) -> tuple[list[Row], list[Row], int, int]:
    """The table's numbered and unnumbered rows, its total rows, the modal column count and the first row's line."""
    parsed = [(i, *_split(line)) for i, line in enumerate(lines)]
    lengths: dict[int, int] = {}
    for _, text, run in parsed:
        if re.search(r"[A-Za-z]{3}", text) and len(run) >= 4:
            lengths[len(run)] = lengths.get(len(run), 0) + 1
    width = max(lengths, key=lambda k: (lengths[k], k)) if count is None else count + 5
    low = width - 2 if count is None else count + 1
    rows: list[Row] = []
    totals: list[Row] = []
    first = -1
    last = 0
    full: dict[int, list[float]] = {}
    for i, text, run in parsed:
        if front and len(run) == width and text:
            full[i] = _centres(lines[i], width)
    pending: tuple[int, str, str] | None = None
    for i, text, run in parsed:
        if not run:
            held = re.match(r"\s*(\d{1,3}[A-Z]?)\s+(\S.*)", text)
            pending = (i, held.group(1), held.group(2)) if held else pending
            continue
        if (
            run
            and text.strip().upper().rstrip(":") in ("TOTAL", "TOTALS")
            and len(run) >= width - (0 if count is None else 1)
        ):
            totals.append(Row("", "", [_value(t) for t in run]))
            continue
        if not run or len(run) < low or len(run) > width + 1:
            continue
        label = text
        number = ""
        if pending and 0 < i - pending[0] <= 2 and not re.match(r"\d", text):
            # The number and the first words of a label that wraps onto the line holding the figures.
            text = f"{pending[1]} {pending[2]} {text}".strip()
            label = text
        found = re.fullmatch(r"(\d{1,3}[A-Z]?)(?:\s+(.*))?", text)
        if found:
            number, label = found.group(1), found.group(2) or ""
        elif not text and len(run) == width + 1:
            number, run = run[0], run[1:]
        if len(run) == width + 1 and not number:
            continue
        if front and not text and len(run) == width and run[0].isdigit() and int(run[0]) == last + 1:
            # A numbered row whose label sits on the lines around it.
            number, run = run[0], run[1:]
        if not label and not number:
            if len(run) == width:
                totals.append(Row("", "", [_value(t) for t in run]))
            continue
        if not re.search(r"[A-Za-z]", label) and not number:
            continue
        if re.search(r"[A-Za-z]{3}", text) is None and not number:
            continue
        values = [_value(t) for t in run]
        if len(values) > width:
            continue
        # A row that leaves its list and sworn-in cells blank (the old layout), or its trailing cells (the new one).
        if front and len(values) < width:
            values = _place(lines[i], run, full, i, width) or [*[None] * (width - len(values)), *values]
        elif not front:
            values = [*values, *[None] * (width - len(values))]
        if number.isdigit():
            last = int(number)
        pending = None
        if first < 0:
            first = i
        rows.append(Row(number or label, label, values))
    return rows, totals, width, first


def _centres(line: str, count: int) -> list[float]:
    return [(m.start() + m.end()) / 2 for m in list(re.finditer(r"\S+", line))[-count:]]


def _place(line: str, run: list[str], full: dict[int, list[float]], at: int, width: int) -> list[int | None] | None:
    """A short row's numbers placed in the columns of the nearest full row, by position, or None when two numbers land
    in one column."""
    if not full:
        return None
    near = full[min(full, key=lambda j: abs(j - at))]
    placed: list[int | None] = [None] * width
    for centre, token in zip(_centres(line, len(run)), run, strict=True):
        column = min(range(width), key=lambda c: abs(near[c] - centre))
        if placed[column] is not None:
            return None
        placed[column] = _value(token)
    return placed


def _phrases(line: str) -> list[tuple[float, str]]:
    return [(m.start() + len(m.group()) / 2, m.group()) for m in re.finditer(r"\S+(?: \S+)*", line)]


def _header(lines: list[str], first: int, columns: list[float]) -> list[tuple[str, str | None]]:
    """The candidates the table header names, left to right, as (name, party code or None)."""
    start = next(i for i in range(first - 1, -1, -1) if re.search(r"eligible\s+voters", lines[i], re.IGNORECASE))
    cells: list[list[str]] = [[] for _ in columns]
    for line in lines[start:first]:
        for centre, phrase in _phrases(line):
            if all(HEADER_WORDS.match(w) for w in phrase.split()):
                continue
            words = [w for w in phrase.split() if not HEADER_WORDS.match(w)]
            nearest = min(range(len(columns)), key=lambda c: abs(columns[c] - centre))
            cells[nearest].append(" ".join(words))
    found = []
    for cell in cells:
        party = None
        if len(cell) > 1 and cell[-1].upper() in PARTIES:
            party = PARTIES[cell.pop().upper()]
        surname = [cell[0].rstrip(",")]
        given = []
        for part in cell[1:]:
            if re.fullmatch(r"[-A-Z' ,.]+", part) and not given:
                surname.append(part.rstrip(",") if not part.startswith("-") else "\0" + part.lstrip("- ").rstrip(","))
            else:
                given.append(part.replace(",", ""))
        last = " ".join(surname).replace(" \0", "-").replace("- ", "-")
        found.append((f"{last}, {' '.join(given)}".strip(", "), party))
    return found


def _numbered(row: Row, candidates: int) -> Poll:
    n = row.numbers
    return Poll(
        row.code,
        row.label or None,
        n[0],
        n[1],
        [v or 0 for v in n[2 : 2 + candidates]],
        n[2 + candidates],
        n[3 + candidates],
    )


def _first_layout(rows: list[Row], width: int, columns: list[float] | None) -> list[float]:
    return columns or []


def _candidate_first(row: Row, count: int, fixed: bool) -> Poll:
    """A row of the 2020 and 2024 layout: candidates, rejected, total cast, then cancelled ballots, the registered voters
    and the electors sworn in. The 2020 table prints every cell. The 2024 tables leave a zero cell blank, so the
    ballots cast are found as the first number that is the candidates' votes, or those and the rejected ballots before
    it, and the registered voters are the trailing number that exceeds the ballots cast."""
    n = row.numbers
    votes = [v or 0 for v in n[:count]]
    if fixed:
        return Poll(row.code, row.label or None, n[count + 3], n[count + 4], votes, n[count], n[count + 1])
    tail = [v for v in n[count:] if v is not None]
    seen = sum(votes)
    if len(tail) > 1 and tail[0] + seen == tail[1]:
        rejected, total, rest = tail[0], tail[1], tail[2:]
    elif tail and tail[0] == seen:
        rejected, total, rest = 0, tail[0], tail[1:]
    else:
        raise ValueError(f"{row.code}: the ballots cast do not follow the candidates' votes: {n}")
    electors = max(rest) if rest and max(rest) > total else None
    sworn = None
    if len(rest) == 3:
        sworn = rest[2]
    elif len(rest) == 2 and rest[0] > total:
        sworn = rest[1]
    if re.search(r"advance|special|mobile", row.label, re.IGNORECASE):
        electors = None  # an advance or special ballot row repeats the district's registered voters, or leaves them out
    return Poll(row.code, row.label or None, electors, sworn, votes, rejected, total)


def _unique(rows: list[Row]) -> list[Row]:
    seen: dict[str, int] = {}
    for row in rows:
        seen[row.code] = seen.get(row.code, 0) + 1
        if seen[row.code] > 1:
            row.code = f"{row.code} ({seen[row.code]})"
    return rows


@functools.cache
def _district(suffix: str, district: str) -> District:
    url = _url(suffix)
    lines = _segment(_lines(url), suffix, district)
    known = KNOWN.get(f"{suffix} {district}")
    rows, totals, width, first = _table(
        lines, suffix not in CANDIDATES_FIRST, len(known) if suffix in CANDIDATES_FIRST and known else None
    )
    rows = _unique(rows)
    notes: list[str] = []
    names: list[tuple[str, str | None]]
    if suffix in CANDIDATES_FIRST:
        assert known is not None
        count = len(known)
        polls = [_candidate_first(r, count, suffix.startswith("oceo")) for r in rows]
        fixed = suffix.startswith("oceo")
        if totals:
            n = totals[-1].numbers
            votes_here = sum(v or 0 for v in n[:count])
            if not fixed:
                # The total row of the 2024 tables carries zeros between the candidates and the rejected ballots.
                for gap in (0, 1, 2):
                    at = count + gap
                    rejected_here, cast_here = n[at], n[at + 1]
                    if rejected_here is not None and rejected_here + votes_here == cast_here:
                        del n[count:at]
                        break
        total = _candidate_first(totals[-1], count, fixed) if totals else None
        names = [(name, party) for name, party, _ in known]
    else:
        count = width - 4
        text_columns = _row_columns(lines, first, width)
        names = (
            [(name, party) for name, party, _ in known] if known else _header(lines, first, text_columns[2 : 2 + count])
        )
        if len(names) != count:
            raise ValueError(f"{district}: {len(names)} candidates for {count} vote columns")
        polls = [_numbered(r, count) for r in rows]
        total = _numbered(totals[-1], count) if totals else None
    votes = [sum(p.votes[i] for p in polls) for i in range(count)]
    for (name, party), fixed_party in PARTY_FIX.get(f"{suffix} {district}", {}).items():
        notes.append(f"the table header prints {name}'s party as {party}; the report's list of candidates gives it")
        names = [(n, fixed_party if (n, q) == (name, party) else q) for n, q in names]
    official = [v for _, _, v in known] if known else []
    if known and all(official) and official == votes and (total is None or total.votes != votes):
        notes.append(
            "the last total row of the report's text layer is the subtotal of the final page, so the district's "
            "figures are the sums of its rows, which match the results block"
        )
        return _finish(district, names, votes, None, polls, notes, None)
    if total is None:
        raise ValueError(f"{suffix} {district}: no total row (width {width}, {len(rows)} rows)")
    numbered = sorted(int(m.group()) for p in polls if (m := re.match(r"\d+", p.code)))
    if len({p.code for p in polls}) != len(polls):
        raise ValueError(f"{district}: a poll is printed twice")
    missing = sorted(set(range(1, numbered[-1] + 1)) - set(numbered))
    if missing:
        notes.append(f"the report prints no row for poll number(s) {', '.join(map(str, missing))}")
    if sum(total.votes) + (total.rejected or 0) != total.total:
        raise ValueError(f"{district}: the total row's votes and rejected ballots do not sum to its total ballots")
    columns = [
        *(zip(names, votes, total.votes, strict=True)),
        (("rejected ballots", None), sum(p.rejected or 0 for p in polls), total.rejected),
        (("total ballots", None), sum(p.total or 0 for p in polls), total.total),
    ]
    for (label, _), got, printed in columns:
        if got != printed:
            notes.append(
                f"the polling divisions' {label} sum to {got}, the total row prints {printed}; the total row is "
                "stored as the declared figure"
            )
    declared = total.votes
    if known:
        for (name, _, official), got in zip(known, votes, strict=True):
            if official and official != got:
                raise ValueError(f"{district}: {name} has {got} votes in the table, {official} in the results block")
    for poll in polls:
        if sum(poll.votes) + (poll.rejected or 0) != poll.total:
            notes.append(
                f"poll {poll.code}: the candidates' votes and rejected ballots sum to "
                f"{sum(poll.votes) + (poll.rejected or 0)}, the row prints {poll.total} total ballots; stored as printed"
            )
    for label, got, printed in (
        ("final list", sum(p.electors or 0 for p in polls), total.electors),
        ("sworn in", sum(p.swear_ins or 0 for p in polls), total.swear_ins),
    ):
        if printed is not None and got != printed:
            notes.append(f"the polls' {label} sum to {got}, the total row prints {printed}; stored as printed")
    return _finish(district, names, declared, total, polls, notes, total)


def _finish(
    district: str,
    names: list[tuple[str, str | None]],
    declared: list[int],
    total: Poll | None,
    polls: list[Poll],
    notes: list[str],
    totals: Poll | None,
) -> District:
    top = max(declared)
    candidates = [Candidate(name, party, v, elected=v == top) for (name, party), v in zip(names, declared, strict=True)]
    return District(
        None,
        district,
        candidates,
        total.electors if total else None,
        sum(declared),
        total.rejected if total else sum(p.rejected or 0 for p in polls),
        total.total if total else sum(p.total or 0 for p in polls),
        polls,
        totals,
        notes=notes,
    )


def _row_columns(lines: list[str], first: int, width: int) -> list[float]:
    """The centres of a full data row's numeric columns, to place the header's words."""
    for line in lines[first:]:
        text, run = _split(line)
        if len(run) == width and text:
            spans = [m for m in re.finditer(r"\S+", line)][-width:]
            return [(m.start() + m.end()) / 2 for m in spans]
    raise ValueError("no full data row")


@functools.cache
def by_elections() -> dict[date, Election]:
    found: dict[date, Election] = {}
    for suffix, entries in FILES.items():
        url = _url(suffix)
        for day, district in entries:
            d = _district(suffix, district)
            election = found.get(day)
            if election is None:
                election = found[day] = Election(day, "by_election", url, [], dict(LEGEND))
            elif url != election.url:
                election.notes.append(f"{district} is reported in {url}")
            election.districts.append(d)
    return found
