"""The 2015, 2019, 2021, and 2025 Newfoundland and Labrador general elections, read from the text layer of Elections
NL's reports.

Each report prints, for every electoral district, the official poll-by-poll results. The 2015, 2019, and 2021 reports
also print a summary table of the candidates (name, party, votes) and each district's electors, ballots counted,
rejected ballots, and total ballots cast; the 2025 report prints neither, so its candidates come from the column
headings of the poll tables and its district figures from the district's total row. Columns are assigned by reading
the headings and then checking every column sum against the printed figures, so a district that does not reconcile
raises an error instead of loading.
"""

import functools
import re
import statistics
import subprocess
from collections import defaultdict
from datetime import date
from typing import Final

from ea import raw
from ea.sources.ca.nl_model import GENERALS, SOURCE, Candidate, District, Election, Poll

DAYS: Final = {2015: date(2015, 11, 30), 2019: date(2019, 5, 16), 2021: date(2021, 2, 13), 2025: date(2025, 10, 14)}
# The poll tables put the two optional columns (the final list of electors and the swear-ins at the polls) before
# the candidates' columns, except in 2025, which puts them after.
EXTRAS_AFTER: Final = {2015: False, 2019: False, 2021: False, 2025: True}
LEGEND_2015: Final = {
    "LIB": "Liberal Party",
    "NDP": "New Democratic Party",
    "PC": "Progressive Conservative Party",
}
LEGEND_2025: Final = {
    "LIB": "Liberal Party",
    "NDP": "New Democratic Party",
    "PC": "Progressive Conservative Party",
}
# The party totals and seats each report prints for the election, by the party code the poll tables use.
PRINTED_2025: Final = {
    "votes": {"PC": 88551, "LIB": 86668, "NDP": 16580, "IND": 7766},
    "seats": {"PC": 21, "LIB": 15, "NDP": 2, "IND": 2},
}

HEADING_WORDS: Final = frozenset(
    {
        "Total",
        "Swear",
        "Ins",
        "Final",
        "Votes",
        "and",
        "Updates",
        "to",
        "the",
        "Polls",
        "Voters",
        "List",
        "Cast",
        "Rejected",
        "Ballots",
        "Revised",
        "Number",
        "of",
        "at",
        "2015",
        "2019",
        "2021",
        "2025",
        "Eligible",
        "Candidate",
        "Counted",
    }
)
PARTY_WORDS: Final = frozenset({"LIB", "NDP", "PC", "IND", "N-A", "NL", "ALL", "GRN"})
NUMBER: Final = re.compile(r"^-?\d[\d,]*$|^\d{1,3}\.\d{3}$")
DASH: Final = re.compile(r"^(?:[-\u2013]|n/a)$", re.IGNORECASE)
PARTY: Final = re.compile(r"^[A-Z][A-Z\-]*(?: [A-Z]+)?$")
HEADING: Final = re.compile(r"ELECTORAL DISTRICT OF\s+(.+?)(?::\s*OFFICIAL RESULTS)?\s*$", re.MULTILINE)


def pages(year: int) -> list[str]:
    path = raw.cached(SOURCE, GENERALS[year]).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    return out.split("\f")


def _int(text: str) -> int:
    return int(text.replace(",", "").replace(".", ""))


def _four(cells: list[str]) -> tuple[int, int, int, int]:
    a, b, c, d = (_int(cell) for cell in cells)
    return a, b, c, d


def _plain(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", text).upper()


def _cells(line: str) -> list[str]:
    return [cell for cell in re.split(r"\s{2,}", re.sub(r"(\d,) (\d)", r"\1\2", line.strip())) if cell]


def _is_name(cell: str) -> bool:
    return re.match(r"^[A-Z][A-Za-z'\u2019.\- ]*,\s*\S", cell) is not None and "Provincial" not in cell


# ---------------------------------------------------------------------------------------------------------------
# The summary table (2015, 2019, 2021)


class Row:
    def __init__(self, index: int, name: str, party: str | None, votes: int) -> None:
        self.index = index
        self.name = name
        self.party = party
        self.votes = votes


def _summary(
    year: int, all_pages: list[str], counts: list[int]
) -> tuple[list[tuple[list[Row], tuple[int, int, int, int], str]], dict]:
    """Each district's candidates, its four printed figures, and its printed name, in the report's order."""
    header = re.compile(r"No\.\s+(?:Electoral )?District\s+Candidate\s+Party\s+Votes")
    chosen = [page for page in all_pages if header.search(page)]
    if not chosen:
        raise ValueError(f"{year}: no summary table")
    legend: dict[str, str] = {}
    first = all_pages.index(chosen[0])
    for page in all_pages[max(first - 1, 0) : first + 1]:
        for found in re.finditer(
            r"(?<![A-Za-z])([A-Z][A-Z\-]*(?: ALL)?):\s+([A-Z][A-Za-z\- ]+?)\s*$", page, re.MULTILINE
        ):
            legend[found.group(1)] = found.group(2).strip()
    rows: list[Row] = []
    numbers: list[tuple[int, int]] = []
    figures_at: list[tuple[int, tuple[int, int, int, int]]] = []
    left: list[tuple[int, str]] = []
    pending: tuple[int, str, list[str]] | None = None
    index = 0
    for page in chosen:
        body = page.split("\n")
        start = next(i for i, line in enumerate(body) if header.search(line))
        for line in body[start + 1 :]:
            index += 1
            if not line.strip() or "Provincial General Election" in line:
                continue
            cells = _cells(line)
            pct = next(
                (
                    i
                    for i, c in enumerate(cells)
                    if i >= 1
                    and re.fullmatch(r"\d+(?:\.+\d+)?%?", c)
                    and ("." in c or "%" in c)
                    and NUMBER.match(cells[i - 1])
                ),
                None,
            )
            name_at = next((i for i, c in enumerate(cells) if _is_name(c)), None)
            if pct is None:
                run = 0
                while run < len(cells) and NUMBER.match(cells[len(cells) - 1 - run]):
                    run += 1
                lead = cells[: len(cells) - run]
                nums = cells[len(cells) - run :]
                if name_at is not None:
                    pending = (index, cells[name_at], cells[:name_at])
                    before = cells[:name_at]
                    if before and before[0].isdigit() and len(before[0]) <= 2:
                        numbers.append((index, int(before[0])))
                        before = before[1:]
                    if before:
                        left.append((index, " ".join(before)))
                    continue
                if lead and lead[0].isdigit() and len(lead[0]) <= 2 and (len(lead) > 1 or not nums):
                    numbers.append((index, int(lead[0])))
                    lead = lead[1:]
                elif not lead and run in (1, 5) and len(nums[0]) <= 2:
                    numbers.append((index, int(nums[0])))
                    nums = nums[1:]
                if lead and not all(re.fullmatch(r"[A-Z\-]+(?: ALL)?", c) for c in lead):
                    left.append((index, " ".join(lead)))
                if len(nums) >= 4:
                    figures_at.append((index, _four(nums[-4:])))
                continue
            votes = _int(cells[pct - 1])
            party = None
            if pct >= 2 and PARTY.match(cells[pct - 2]) and not _is_name(cells[pct - 2]):
                party = cells[pct - 2]
            if name_at is not None:
                name = cells[name_at]
                lead = cells[:name_at]
            elif pending is not None:
                name = pending[1]
                lead = cells[: max(pct - (2 if party else 1), 0)]
            else:
                raise ValueError(f"{year}: candidate line without a name: {line!r}")
            pending = None
            rows.append(Row(index, name, party, votes))
            if lead and lead[0].isdigit() and len(lead[0]) <= 2:
                numbers.append((index, int(lead[0])))
                lead = lead[1:]
            if lead:
                left.append((index, " ".join(lead)))
            after = [c for c in cells[pct + 1 :] if NUMBER.match(c)]
            if len(after) == 4:
                figures_at.append((index, _four(after)))
    footer = None
    if len(figures_at) == len(numbers) + 1:
        footer = figures_at.pop()[1]
    if len(figures_at) != len(numbers) or [n for _, n in numbers] != list(range(1, len(numbers) + 1)):
        raise ValueError(f"{year}: {len(numbers)} district numbers but {len(figures_at)} figure lines")
    merged = [(min(a, b), n, f) for (a, n), (b, f) in zip(numbers, figures_at, strict=True)]
    districts: list[tuple[list[Row], tuple[int, int, int, int], str]] = []
    cursor = 0
    if len(counts) != len(merged):
        raise ValueError(f"{year}: {len(merged)} districts in the summary, {len(counts)} poll tables")
    for (_, number, figures), count in zip(merged, counts, strict=True):
        districts.append((rows[cursor : cursor + count], figures, str(number)))
        cursor += count
    if cursor != len(rows):
        raise ValueError(f"{year}: {len(rows) - cursor} candidates left over")
    return [(d[0], d[1], d[2]) for d in districts], {"legend": legend, "footer": footer, "left": left, "at": merged}


def _name_of(heading: str, at: int, left: list[tuple[int, str]]) -> str:
    near = [(i, t) for i, t in left if abs(i - at) <= 3]
    target = _plain(heading)
    for a in range(len(near)):
        for b in range(a, len(near)):
            run = near[a : b + 1]
            joined = " ".join(t for _, t in run)
            if _plain(joined) == target:
                return re.sub(r"\s+", " ", joined).replace(" - ", " - ")
    return " ".join(w.capitalize() for w in re.sub(r"\xad", "-", heading).split())


# ---------------------------------------------------------------------------------------------------------------
# The poll-by-poll tables


class Tail:
    def __init__(self, line: str) -> None:
        tokens = [(m.group(0), m.end()) for m in re.finditer(r"\S+", line)]
        k = len(tokens)
        while k > 0 and (NUMBER.match(tokens[k - 1][0]) or DASH.match(tokens[k - 1][0])):
            k -= 1
        self.head = " ".join(t for t, _ in tokens[:k])
        self.tail = tokens[k:]


def _poll_pages(all_pages: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for page in all_pages:
        match = HEADING.search(page)
        if match is None or "Rejected" not in page or "TURNOUT" in page or "SUMMARY" in page:
            continue
        found.setdefault(_plain(match.group(1)), []).append(page)
    return found


def _count(year: int, pages_of: list[str]) -> int:
    """The number of candidates of a district's poll table."""
    if year != 2021:
        return len(_header_candidates(pages_of[0]))
    # the 2021 total row prints only the candidates, the rejected ballots, and the ballots cast
    for line in reversed(pages_of[-1].split("\n")):
        tail = Tail(line).tail
        if len(tail) >= 4 and not Tail(line).head:
            return len(tail) - 2
    raise ValueError("no total row")


def _align_codes(centres: list[float], anchors: list[float]) -> list[int]:
    """The candidates that carry the party codes: the increasing assignment nearest the printed positions."""
    found = _align([int(c * 10) for c in centres], [a * 10 for a in anchors])
    if found is None:
        raise ValueError("more party codes than candidates in a poll table heading")
    return found


def _header_candidates(page: str) -> list[tuple[str, str | None]]:
    """The candidates of a poll table, from the surname, given-name, and party words above the first row."""
    lines = page.split("\n")
    start = next(i for i, line in enumerate(lines) if "Eligible Voters" in line)
    marks = [i for i, line in enumerate(lines[: start + 14]) if re.search(r"\bPolls\b", line)]
    if marks:
        end = max(marks)
    else:
        end = next(i for i, line in enumerate(lines) if i > start and len(Tail(line).tail) >= 3) - 1
        while not lines[end].strip():
            end -= 1
    while end + 1 < len(lines) and lines[end + 1].split() and all(w in PARTY_WORDS for w in lines[end + 1].split()):
        end += 1
    block = lines[start + 1 : end + 1]
    skip = HEADING_WORDS | {"Poll", "#", "Polling", "Division", "Description"}
    rows: list[list[tuple[int, int, str]]] = []
    for line in block:
        rows.append([(m.start(), m.end(), m.group(0)) for m in re.finditer(r"\S+", line)])
    role_x = [(a + b) / 2 for row in rows for a, b, w in row if w in ("Rejected", "Final")]
    limit = min(role_x) - 4 if role_x else 1e9
    surname = [(a + b) / 2 for row in rows for a, b, w in row if w.endswith(",") and (a + b) / 2 < limit]
    surname.sort()
    ncand = len(surname)
    if not ncand:
        raise ValueError("no candidate columns in a poll table heading")
    parts: list[list[tuple[int, str]]] = [[] for _ in range(ncand)]
    codes: list[tuple[float, str]] = []
    for n, row in enumerate(rows):
        groups: list[list[tuple[int, int, str]]] = []
        for a, b, word in row:
            if (a + b) / 2 >= limit or word in skip:
                continue
            if word in PARTY_WORDS:
                codes.append(((a + b) / 2, word))
                continue
            new = not groups or a - groups[-1][-1][1] >= 2
            if groups and groups[-1][-1][2].endswith(",") and word.isupper() and sum(c.isalpha() for c in word) >= 3:
                new = True
            if new:
                groups.append([])
            groups[-1].append((a, b, word))
        if not groups:
            continue
        centres = [(g[0][0] + g[-1][1]) / 2 for g in groups]
        if len(groups) == ncand:
            slots = list(range(ncand))
        else:
            found = _align([int(c * 10) for c in centres], [c * 10 for c in surname])
            if found is None:
                raise ValueError("more name groups than candidates in a poll table heading")
            slots = found
        for slot, group in zip(slots, groups, strict=True):
            parts[slot].append((n, " ".join(w for _, _, w in group)))
    joined: list[tuple[float, str]] = []
    for centre, word in sorted(codes):
        if joined and word == "ALL" and joined[-1][1] == "NL":
            joined[-1] = ((joined[-1][0] + centre) / 2, "NL ALL")
        else:
            joined.append((centre, word))
    placed = _align_codes([c for c, _ in joined], surname)
    party = {slot: joined[i][1] for i, slot in enumerate(placed)}
    found_names = []
    for slot in range(ncand):
        name = " ".join(t for _, t in sorted(parts[slot], key=lambda x: x[0]))
        found_names.append((re.sub(r"(\w-) (\w)", r"\1\2", name), party.get(slot)))
    return found_names


def _align(ends: list[int], columns: list[float]) -> list[int] | None:
    """The increasing assignment of figures to columns that puts every figure's right edge nearest its column."""
    n, m = len(ends), len(columns)
    if n > m:
        return None
    best: dict[tuple[int, int], tuple[float, list[int]]] = {(0, -1): (0.0, [])}
    for i in range(n):
        step: dict[tuple[int, int], tuple[float, list[int]]] = {}
        for (_, last), (cost, path) in best.items():
            for j in range(last + 1, m - (n - i - 1)):
                total = cost + abs(ends[i] - columns[j])
                if (i + 1, j) not in step or total < step[(i + 1, j)][0]:
                    step[(i + 1, j)] = (total, [*path, j])
        best = step
    return min(best.values(), key=lambda t: t[0])[1] if best else None


def _rows(year: int, pages_of: list[str], ncand: int) -> tuple[list[Poll], Poll, list[float] | None]:
    after = EXTRAS_AFTER[year]
    raw_rows: list[dict] = []
    for page in pages_of:
        lines = page.split("\n")
        starts = [m.end() for line in lines if (m := re.match(r"\s*\d+\s+(?=[A-Za-z])", line))]
        desc_start = statistics.median(starts) if starts else 12
        marks = [i for i, line in enumerate(lines[:16]) if re.search(r"\bPolls\b", line)]
        first = (max(marks) + 1) if marks else 0
        pre: list[str] = []
        seen_row = False
        orphan: int | None = None
        skip = 0
        footer = re.compile(r"Provincial General Election|Electoral District Maps")
        body = [line for line in lines[first:] if line.strip()]
        for at, line in enumerate(body):
            if skip:
                skip -= 1
                continue
            tail = Tail(line)
            core = ncand + 2
            if len(tail.tail) < core:
                if tail.head and not footer.search(tail.head):
                    pre.append(tail.head)
                if len(tail.tail) == 1 and not DASH.match(tail.tail[0][0]):
                    orphan = _int(tail.tail[0][0])
                continue
            tokens = tail.tail
            head = tail.head
            number = None
            if not head and tokens[0][1] <= desc_start and "," not in tokens[0][0]:
                number = tokens[0][0]
                tokens = tokens[1:]
            if len(tokens) > ncand + 4:
                extra = len(tokens) - (ncand + 4)
                head = (head + " " + " ".join(t for t, _ in tokens[:extra])).strip()
                tokens = tokens[extra:]
            if head and re.match(r"^\d+\s", head):
                number, head = head.split(None, 1)
            elif head and head.isdigit():
                number, head = head, ""
            elif not head and number is None and at + 1 < len(body):
                following = body[at + 1]
                found = re.match(r"^\s*(\d+)\s+(\S.*)$", following)
                if found and len(Tail(following).tail) < core and not footer.search(following):
                    number, head = found.group(1), found.group(2).strip()
                    skip = 1
            raw_rows.append(
                {
                    "number": number,
                    "head": head,
                    "pre": pre if seen_row else [],
                    "tokens": tokens,
                    "line": line,
                    "page": id(page),
                    "orphan": orphan,
                }
            )
            seen_row = True
            pre = []
            orphan = None
    if not raw_rows:
        raise ValueError(f"{year}: poll rows missing")
    width = ncand + 4

    def medians(rows_: list[dict]) -> list[float] | None:
        full = [r for r in rows_ if len(r["tokens"]) == width]
        if len(full) < 2:
            return None
        return [statistics.median(r["tokens"][k][1] for r in full) for k in range(width)]

    overall = medians(raw_rows)
    by_page: dict[int, list[float] | None] = {}
    for page_no in {r["page"] for r in raw_rows}:
        by_page[page_no] = medians([r for r in raw_rows if r["page"] == page_no])
    polls: list[Poll] = []
    for row in raw_rows:
        tokens = row["tokens"]
        values = [None if DASH.match(t) else _int(t) for t, _ in tokens]
        columns: list[int | None] = [None] * width
        if len(tokens) == width:
            columns = list(values)
        else:
            ends = by_page[row["page"]] or overall
            if ends is None:
                raise ValueError(f"{year}: a poll row with {len(tokens)} figures and no full row to align it with")
            assigned = _align([end for _, end in tokens], ends)
            if assigned is None:
                raise ValueError(f"{year}: figures of {row['line'].strip()!r} do not align with the columns")
            for j, value in zip(assigned, values, strict=True):
                columns[j] = value
        if after:
            core, elec, swear = columns[: ncand + 2], columns[ncand + 2], columns[ncand + 3]
        else:
            elec, swear, core = columns[0], columns[1], columns[2:]
        votes = [v or 0 for v in core[:ncand]]
        rejected = core[ncand] or 0
        if core[ncand + 1] is None and row["orphan"] == sum(votes) + rejected:
            core[ncand + 1] = row["orphan"]
        text = row["head"]
        wrapped = " ".join([*row["pre"], text]).strip()
        number = row["number"]
        label = number
        description = text or None
        if number is None:
            if re.search(r"(?:^|\s)TOTAL$", wrapped, re.IGNORECASE) or not text:
                label = "TOTAL"
                description = None
            elif re.search(r"special", wrapped, re.IGNORECASE):
                label = "Special Ballots"
                description = None
            else:
                label = text
                description = None
        elif re.match(r"^(advance|special)", text, re.IGNORECASE) and ":" not in text:
            label = text
            description = None
        polls.append(Poll(str(label), description, elec, swear, votes, rejected, core[ncand + 1]))
    total_rows = [p for p in polls if p.code == "TOTAL"]
    if len(total_rows) != 1 or polls[-1].code != "TOTAL":
        raise ValueError(f"{year}: expected one closing total row, found {len(total_rows)}")
    return polls[:-1], polls[-1], overall


# ---------------------------------------------------------------------------------------------------------------


def _plausible(polls: list[Poll]) -> bool:
    """A district whose rows differ from its total row is a source discrepancy, not a misread, only when at most
    three rows fail to add up to their own printed totals."""
    off = [p for p in polls if p.total is not None and p.total != sum(p.votes) + (p.rejected or 0)]
    return len(off) <= 3


def _reconcile(
    year: int,
    label: str,
    polls: list[Poll],
    total: Poll,
    declared: list[int],
    figures: tuple[int, int, int, int] | None,
) -> tuple[list[Poll], Poll, list[str]]:
    """Put the candidates' columns in the declared order, and check every sum."""
    notes: list[str] = []
    ncand = len(declared)
    sums = [sum(p.votes[i] for p in polls) for i in range(ncand)]
    if sums != declared and figures is not None and sum(sums) == figures[1] and total.votes == sums:
        for i, (got, want) in enumerate(zip(sums, declared, strict=True)):
            if got != want:
                notes.append(
                    f"the summary prints {want} votes for candidate {i + 1}; the poll table, its total row, and the "
                    f"printed ballots counted give {got}"
                )
        declared = list(sums)
    if sums != declared and sorted(sums) != sorted(declared):
        if not _plausible(polls):
            raise ValueError(f"{year} {label}: poll columns sum to {sums}, the authority declares {declared}")
        for i, (got, want) in enumerate(zip(sums, declared, strict=True)):
            if got != want:
                notes.append(
                    f"the poll rows add up to {got} votes for candidate {i + 1}, the district's printed total row "
                    f"gives {want}; the total row is the declared figure"
                )
        if total.votes != declared and figures is not None:
            notes.append(
                f"the district's printed total row gives {total.votes} votes; the summary prints {declared}, which are "
                "stored"
            )
            total = Poll(total.code, None, total.electors, total.swear_ins, list(declared), total.rejected, total.total)
        return polls, total, notes
    if sums != declared:
        order = []
        free = list(range(ncand))
        for want in declared:
            pick = next((i for i in free if sums[i] == want), None)
            if pick is None:
                raise ValueError(f"{year} {label}: poll columns sum to {sums}, the authority declares {declared}")
            free.remove(pick)
            order.append(pick)
        polls = [
            Poll(p.code, p.description, p.electors, p.swear_ins, [p.votes[i] for i in order], p.rejected, p.total)
            for p in polls
        ]
        total = Poll(
            total.code,
            None,
            total.electors,
            total.swear_ins,
            [total.votes[i] for i in order],
            total.rejected,
            total.total,
        )
        notes.append("the poll table prints the candidates in a different order than the summary")
    if total.votes != declared:
        if figures is None or not _plausible(polls):
            raise ValueError(f"{year} {label}: total row {total.votes}, the authority declares {declared}")
        notes.append(
            f"the district's printed total row gives {total.votes} votes; the poll rows and the summary agree on "
            f"{declared}, which are stored"
        )
        total = Poll(total.code, None, total.electors, total.swear_ins, list(declared), total.rejected, total.total)
    rejected = sum(p.rejected or 0 for p in polls)
    cast = sum(p.total or 0 for p in polls)
    if rejected != total.rejected or cast != total.total:
        if not _plausible(polls):
            raise ValueError(
                f"{year} {label}: rejected {rejected}/{total.rejected} or cast {cast}/{total.total} disagree with "
                "the total row"
            )
        notes.append(
            f"the poll rows add up to {rejected} rejected ballots and {cast} ballots cast, the district's printed "
            f"total row gives {total.rejected} and {total.total}; the total row is the declared figure"
        )
    if figures is not None:
        _, counted, rej, tot = figures
        got = (sum(total.votes), total.rejected, total.total)
        if got != (counted, rej, tot):
            adds = sum(total.votes) + (total.rejected or 0) == total.total
            if not adds and counted + rej != tot:
                raise ValueError(f"{year} {label}: the summary prints {figures}, the poll table {got}")
            notes.append(
                f"the summary prints ballots counted, rejected, and cast of {counted}, {rej}, {tot}; the poll "
                f"table's total row, which {'adds up' if adds else 'does not add up'}, gives "
                f"{got[0]}, {got[1]}, {got[2]}"
            )
    for p in polls:
        if p.total is not None and p.total != sum(p.votes) + (p.rejected or 0):
            notes.append(
                f"in poll {p.code} the printed total ballots ({p.total}) differ from its votes plus rejected ballots "
                f"({sum(p.votes) + (p.rejected or 0)}); stored as printed"
            )
    return polls, total, notes


@functools.cache
def election(year: int) -> Election:
    all_pages = pages(year)
    poll_pages = _poll_pages(all_pages)
    headings = []
    for page in all_pages:
        match = HEADING.search(page)
        if match is not None and _plain(match.group(1)) in poll_pages and match.group(1) not in headings:
            headings.append(match.group(1))
    keys: list[str] = []
    for heading in headings:
        if _plain(heading) not in keys:
            keys.append(_plain(heading))
    districts: list[District] = []
    notes: list[str] = []
    legend: dict[str, str] = {}
    if year == 2025:
        legend = dict(LEGEND_2025)
        known = {_plain(d.name): d.name for d in election(2021).districts}
        regs = {}
        for page in all_pages:
            m = re.search(r"ELECTORAL DISTRICT OF (.+?): SUMMARY OF POLLING DIVISIONS", page)
            r = re.search(r"Registered Electors in District:\s*([\d,]+)", page)
            if m and r:
                regs[_plain(m.group(1))] = _int(r.group(1))
        for number, key in enumerate(keys, 1):
            first = poll_pages[key][0]
            heading = next(h for h in headings if _plain(h) == key)
            cands = _header_candidates(first)
            try:
                polls, total, _ = _rows(year, poll_pages[key], len(cands))
            except ValueError as problem:
                raise ValueError(f"{year} {heading}: {problem}") from problem
            declared = list(total.votes)
            polls, total, extra = _reconcile(year, heading, polls, total, declared, None)
            candidates = [Candidate(n, c, v) for (n, c), v in zip(cands, declared, strict=True)]
            winner = max(range(len(candidates)), key=lambda i: candidates[i].votes)
            candidates[winner].elected = True
            districts.append(
                District(
                    number,
                    known.get(key) or re.sub(r"\s+", " ", heading).title().replace("\u2019S", "\u2019s"),
                    candidates,
                    regs.get(key),
                    sum(declared),
                    total.rejected,
                    total.total,
                    polls,
                    total,
                    notes=extra,
                )
            )
        sums: dict[str, int] = defaultdict(int)
        seats: dict[str, int] = defaultdict(int)
        for d in districts:
            for c in d.candidates:
                sums[c.party or "IND"] += c.votes
                if c.elected:
                    seats[c.party or "IND"] += 1
        if dict(seats) != PRINTED_2025["seats"] or set(sums) != set(PRINTED_2025["votes"]):
            raise ValueError(f"2025: party seats {dict(seats)} / parties {sorted(sums)} differ from the report")
        for code, printed in PRINTED_2025["votes"].items():
            if abs(sums[code] - printed) > 50:
                raise ValueError(f"2025: {code} votes sum to {sums[code]}, the report prints {printed}")
            if sums[code] != printed:
                notes.append(
                    f"the report's totals print {printed} votes for {code}; the districts' total rows add up to "
                    f"{sums[code]}"
                )
    else:
        counts = [_count(year, poll_pages[key]) for key in keys]
        summary, info = _summary(year, all_pages, counts)
        legend = info["legend"] or dict(LEGEND_2015)
        for (number, (rows, figures, _)), key, (at, _, _) in zip(enumerate(summary, 1), keys, info["at"], strict=True):
            heading = next(h for h in headings if _plain(h) == key)
            try:
                polls, total, _ = _rows(year, poll_pages[key], len(rows))
            except ValueError as problem:
                raise ValueError(f"{year} {heading}: {problem}") from problem
            declared = [r.votes for r in rows]
            polls, total, extra = _reconcile(year, heading, polls, total, declared, figures)
            candidates = [Candidate(r.name, r.party, v) for r, v in zip(rows, total.votes, strict=True)]
            adds = sum(total.votes) + (total.rejected or 0) == total.total
            winner = max(range(len(candidates)), key=lambda i: candidates[i].votes)
            candidates[winner].elected = True
            districts.append(
                District(
                    number,
                    _name_of(heading, at, info["left"]),
                    candidates,
                    figures[0],
                    sum(total.votes),
                    total.rejected if adds else figures[2],
                    total.total if adds else figures[3],
                    polls,
                    total,
                    notes=extra,
                )
            )
        footer = info["footer"]
        if footer is not None:
            got = (
                sum(d.electors or 0 for d in districts),
                sum(d.counted or 0 for d in districts),
                sum(d.rejected or 0 for d in districts),
                sum(d.cast or 0 for d in districts),
            )
            if got[1:] != footer[1:]:
                raise ValueError(f"{year}: the provincial totals print {footer}, the districts sum to {got}")
            if got[0] != footer[0]:
                notes.append(
                    f"the report prints {footer[0]} eligible voters on the revised list for the province; the "
                    f"districts' printed lists add up to {got[0]}"
                )
        else:
            notes.append("the summary prints no provincial total line")
    return Election(DAYS[year], "general", GENERALS[year], districts, legend, notes)


def generals() -> dict[int, Election]:
    return {year: election(year) for year in (2015, 2019, 2021, 2025)}
