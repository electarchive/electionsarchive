"""Western Australian state elections from the Western Australian Electoral Commission (WAEC).

The commission publishes a Results and Statistics report after each state general election. Its district profiles
print the Legislative Assembly's first preference votes for each candidate at each polling place, the declaration
votes by type, and the two-candidate preferred count. The reports are PDFs with a text layer, read here with
`pdftotext -layout`. One package holds each general election, plus a `registry` package.
"""

import functools
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "au.waec"
HOST = "https://www.elections.wa.gov.au"
AUSTRALIA = str(uuid5(package.NAMESPACE, "polity\x1fAU"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
LICENSE = (
    "All rights reserved (Western Australian Electoral Commission): the figures are published as facts with "
    "attribution; the report files are not redistributed"
)
LICENSE_URL = f"{HOST}/copyright"
REPORTS = f"{HOST}/elections/state/reports"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


WAEC = key("source", "waec")
ASSEMBLY = key("body", "legislative-assembly")
SYSTEM = key("electoral_system", "legislative-assembly", "irv")
COUNCIL = key("body", "legislative-council")
COUNCIL_SYSTEM = key("electoral_system", "legislative-council", "stv")


def url(path: str) -> str:
    return HOST + path.replace(" ", "%20").replace("&", "%26")


@dataclass(frozen=True)
class General:
    day: str
    districts: str  # the report holding the district profiles
    candidates: str  # the report holding the list of candidates in ballot order
    enrolment: str | None = None  # the report holding the table of enrolment by district, when the profiles omit it


DOCS = "/sites/default/files/content/documents"
R2001 = url(f"{DOCS}/2001_SGE_Results_and_Stats.pdf")
R2005 = url(f"{DOCS}/2005_SGE_Results_and_Stats.pdf")
R2025 = url("/sites/default/files/SGE2025/Reports/WAEC9467 State Election Stats+Results Report WEB.pdf")
GENERALS = [
    General("2001-02-10", R2001, R2001, R2001),
    General("2005-02-26", R2005, R2005, R2005),
    General(
        "2008-09-06", url(f"{DOCS}/2008_SGE_Results_Stats_Part4.pdf"), url(f"{DOCS}/2008_SGE_Results_Stats_Part3.pdf")
    ),
    General(
        "2013-03-09", url(f"{DOCS}/2013_SGE_Results_Stats_Part4.pdf"), url(f"{DOCS}/2013_SGE_Results_Stats_Part3.pdf")
    ),
    General(
        "2017-03-11",
        url("/sites/default/files/content/documentssge2017/2017 District Profiles Election Results online.pdf"),
        url("/sites/default/files/content/documentssge2017/2017_Legislative Assembly_online.pdf"),
    ),
    General(
        "2021-03-13",
        url(
            "/sites/default/files/content/SGE 2021/2020-2021 content/Reports/4_21_Res_Stats_Dist_Prof_Elect_Results.pdf"
        ),
        url("/sites/default/files/content/SGE 2021/SGE 2021 Reports/3_21_Res_Stats_Leg_Assem_0.pdf"),
    ),
    General("2025-03-08", R2025, R2025, R2025),
]


# Reading -----------------------------------------------------------------------------------------------------------


@functools.cache
def layout(link: str) -> str:
    path = str(raw.cached(SOURCE, link).path)
    return subprocess.run(["pdftotext", "-layout", path, "-"], check=True, capture_output=True, text=True).stdout


INTEGER = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
PERCENT = re.compile(r"\d+(?:\.\d+)?%")


def integer(token: str) -> int:
    return int(token.replace(",", ""))


@dataclass
class Candidate:
    surname: str
    given: str
    party: str  # as the candidate list prints it; empty for no party
    preferred: str = ""  # the preferred given name, which the 2001 and 2005 lists print

    @property
    def ballot(self) -> str:
        return f"{self.surname}, {self.given}" if self.given else self.surname

    @property
    def display(self) -> str:
        words = []
        for word in self.surname.split():
            if re.search(r"[A-Z]{2}", word):
                word = "-".join(part.capitalize() for part in word.split("-"))
                word = re.sub(r"^(Mc|Mac|O')([a-z])", lambda m: m[1] + m[2].upper(), word)
            words.append(word)
        given = self.preferred or self.given
        return f"{given} {' '.join(words)}".strip()


@dataclass
class Place:
    name: str
    votes: list[int]
    formal: int
    informal: int
    total: int
    kind: str = "standard"
    pair: tuple[int, int] | None = None  # the two-candidate preferred count, in `District.finalists` order
    exhausted: int | None = None


@dataclass
class District:
    name: str
    candidates: list[Candidate]
    places: list[Place]
    declared: Place
    finalists: tuple[int, int] | None
    enrolled: int | None
    region: str | None = None
    winner: str | None = None  # the elected member's full name, surname first, when the profile prints it
    fixes: list[str] = field(default_factory=list)  # corrections to the report's cells, as notes


# Cells the reports misprint, each proven by the row's own figures and the district's printed totals:
# (day, district, place) to (printed total, corrected total, note).
FIXES = {
    ("2025-03-08", "Secret Harbour", "Port Kennedy Primary School"): (
        1527,
        1493,
        (
            "The report prints 1,527 total votes at Port Kennedy Primary School, but its 1,406 formal and 87 informal "
            "votes make 1,493, and the district's printed total of 28,984 votes holds only with 1,493. The archive "
            "stores 1,493."
        ),
    ),
}
STARTS = ("First Preference Votes Polled for each", "Primary Count for each Candidate", "Two Candidate Preferred Votes")
STOP = "Distribution of Preferences"
# Header words that are not part of a candidate's name or party.
FURNITURE = {
    "PP", "No.", "Polling", "Place", "Votes", "Total", "Formal", "Informal", "Exhau-", "sted", "Valid", "Exhausted",
}  # fmt: skip
SUBTOTALS = (
    "Polling Place Totals",
    "Total Polling Places Votes",
    "Declaration and Mobile Votes Total",
    "Declaration and Pre-Poll Votes Total",
    "Total Count Centre Votes",
)
TOTALS = ("Total First Preferences Votes", "Total Votes")
DECLARATION = (
    "Absent Votes",
    "Postal Votes",
    "Pre-Poll Votes",
    "Provisional Votes",
    "Early Votes (by Post)",
    "Early Votes (by post)",
    "Early Votes (In Person)",
    "Early Votes (in person)",
    "Special Institutions & Remote",
    "Special Institutions & Remotes",
    "Special Institutions, Hospitals & Remotes",
)
TITLE = re.compile(r"(?:^|\s{2,})([A-Z][A-Za-z' .-]+?) ?Electoral\s+District\b")
REGION = re.compile(r"(?:^|\s{2,})((?:[A-Z][a-z]+ |and |& )+)Region\b")


def tokens(line: str) -> list[tuple[int, int, str]]:
    """Each whitespace-separated token of a line with its start and end column."""
    return [(m.start(), m.end(), m[0]) for m in re.finditer(r"\S+", line)]


def numeric(token: str) -> bool:
    return bool(INTEGER.fullmatch(token) or PERCENT.fullmatch(token))


@dataclass
class Row:
    label: str
    numbers: list[str]
    line: int


def rows(lines: list[str], width: int) -> list[Row]:
    """The table's rows, taking the last `width` numbers of each line as its cells and the rest as its label.

    A label that wraps onto the lines around a row of numbers with no label of its own is joined back together, and
    a row whose cells a page break splits across two lines is put back together by the cells' positions."""
    out: list[Row] = []
    pending: list[str] = []
    wrapped: Row | None = None
    partial: tuple[str, list[tuple[int, str]], int] | None = None
    for index, line in enumerate(lines):
        words = line.split()
        if not words:
            continue
        trailing = 0
        while trailing < len(words) and numeric(words[-1 - trailing]):
            trailing += 1
        if partial is not None:
            label, cells, at = partial
            found = tokens(line)
            merged = sorted(cells + [(start, w) for start, _, w in found if numeric(w)])
            text = " ".join(w for _, _, w in found if not numeric(w))
            if len(merged) == width and len(text) < 40:
                out.append(Row(f"{label} {text}".strip(), [w for _, w in merged], at))
                partial = None
                continue
        if trailing >= width:
            label = " ".join(words[: len(words) - width])
            row = Row(label, words[len(words) - width :], index)
            if not label:
                row.label = " ".join(pending)
                wrapped = row
            else:
                wrapped = None
            pending = []
            partial = None
            out.append(row)
        elif trailing == 0 and not any(numeric(w) for w in words):
            text = " ".join(words)
            if wrapped is not None and len(text) < 40 and not text.startswith(("%", "Voter")):
                wrapped.label = f"{wrapped.label} {text}".strip()
                wrapped = None
            else:
                pending.append(text)
                pending = pending[-2:]
        else:
            if not numeric(words[0]) and width / 2 <= trailing < width:
                found = tokens(line)
                partial = (" ".join(words[: len(words) - trailing]), [(st, w) for st, _, w in found[-trailing:]], index)
            pending = []
            wrapped = None
    return out


def fold(name: str) -> str:
    """A district name for matching across the report's tables."""
    return re.sub(r"[^a-z]", "", name.casefold())


def upper_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z']+", text.replace("’", "'")) if re.search(r"[A-Z]{2}", w)]


def in_order(name: str, header: list[str], cands: list[Candidate]) -> None:
    """Check that the table's columns follow the candidate list's ballot order, by the surnames over them."""
    placed = sorted(((start, word) for line in header for start, _, word in tokens(line)), key=lambda t: t[0])
    printed = [w.upper() for _, text in placed for w in upper_words(text)]
    at = 0
    for c in cands:
        want = upper_words(c.surname)[-1].upper() if upper_words(c.surname) else c.surname.upper()
        hits = [
            i for i in range(at, len(printed)) if printed[i] == want or (len(want) > 4 and want.startswith(printed[i]))
        ]
        if not hits:
            raise ValueError(f"{name}: {c.surname} is not over a column in ballot order ({printed})")
        at = hits[0] + 1


LIST_HEAD = "Candidate (Full Given Names)"
ENTRY = re.compile(
    r"^\s*(?:(?P<district>[A-Z][A-Za-z'.-]*(?: [A-Za-z][A-Za-z'.-]*)*)\s+)?(?P<n>\d{1,2})\s+"
    r"(?P<surname>[^,]+),\s*(?P<rest>.*)$"
)


@functools.cache
def ballots(link: str, column: str = "District") -> dict[str, list[Candidate]]:
    """The candidates of each district (or Legislative Council region) in ballot order, from a candidate list."""
    found_lists: list[tuple[list[str], list[Candidate]]] = []
    for page in layout(link).split("\f"):
        lines = page.splitlines()
        head = next((i for i, line in enumerate(lines) if LIST_HEAD in line and line.split()[0] == column), None)
        if head is None:
            continue
        preferred = "Preferred" in " ".join(lines[max(0, head - 2) : head + 2])
        body = lines[head + 1 :]
        for index, line in enumerate(body):
            # A long name sets the candidate's name on the line above their number and party, and the rest of
            # the given names on the line below.
            bare = re.match(r"^(\s*\d{1,2})\s{2,}([^,]+)$", line)
            if bare and index > 0 and "," in body[index - 1] and not ENTRY.match(body[index - 1]):
                below = body[index + 1].strip() if index + 1 < len(body) else ""
                given = (
                    f"{body[index - 1].strip()} {below}"
                    if below and len(below.split()) <= 2
                    else body[index - 1].strip()
                )
                line = f"{bare[1]}  {given}  {bare[2].strip()}"
            found = ENTRY.match(line)
            if not found:
                continue
            number = int(found["n"])
            if number == 1:
                found_lists.append(([], []))
            if not found_lists:
                raise ValueError(f"candidate {number} before any district in {link}")
            names, entries = found_lists[-1]
            if found["district"] and found["district"] != " ".join(names):
                names.append(found["district"])  # a long district name wraps onto its later candidates' lines
            if number != len(entries) + 1:
                raise ValueError(f"{' '.join(names)}: candidate {number} follows {len(entries)} in {link}")
            rest = re.split(r"\s{2,}", found["rest"].strip())
            given = rest[0]
            fields = rest[1:]
            nick = fields.pop(0) if preferred and fields else ""
            party = " ".join(fields)
            entries.append(Candidate(found["surname"].strip(), given, party, nick))
    out: dict[str, list[Candidate]] = {}
    for names, entries in found_lists:
        name = "".join(n if n.endswith("-") else f"{n} " for n in names).strip()
        if fold(name) in out:
            raise ValueError(f"{name} is listed twice in {link}")
        out[fold(name)] = entries
    return out


def block_name(lines: list[str]) -> str:
    for line in lines:
        found = TITLE.search(line)
        if found:
            return found[1].strip()
    raise ValueError("no district title")


def nearest_name(lines: list[str], context: list[str]) -> str:
    for line in [*lines, *reversed(context)]:
        found = TITLE.search(line)
        if found:
            return found[1].strip()
    raise ValueError("no district title")


def district(
    day: str, lines: list[str], context: list[str], enrolment: dict[str, int], lists: dict[str, list[Candidate]]
) -> District:
    """One district's table of first preferences by polling place."""
    name = nearest_name(lines, context)
    cands = lists.get(fold(name))
    if cands is None:
        raise ValueError(f"{name}: not in the candidate list")
    total_index = next(
        i for i, line in enumerate(lines) if line.split()[:3] and " ".join(line.split()).startswith(TOTALS)
    )
    total_words = lines[total_index].split()
    width = sum(1 for w in total_words if numeric(w))
    percents = sum(1 for w in total_words if PERCENT.fullmatch(w))
    tcp = 4 if percents == 2 else 0
    extra = 0
    if percents not in (0, 2):
        raise ValueError(f"{name}: unexpected totals row {lines[total_index]!r}")
    count = width - 3 - extra - tcp
    head = next(i for i, line in enumerate(lines) if any(s in line for s in STARTS))
    table = rows(lines, width)
    if not table:
        raise ValueError(f"{name}: no rows")
    first = table[0].line
    header = lines[head + 1 : first]
    if "Exhau" in "\n".join(header):
        # 2025 adds exhausted and total valid columns, so the table is read again with them
        extra = 2
        count = width - 3 - extra - tcp
    in_order(name, header, cands)
    finalists: tuple[int, int] | None = None
    places: list[Place] = []
    declared: Place | None = None
    sums: dict[str, Place] = {}
    fixed: list[str] = []
    # The two-candidate preferred columns are a count and a percentage each; a percentage of nothing prints as 0.
    grid = [[integer(c) for c in (row.numbers[:-4] if tcp else row.numbers)] for row in table]
    # Some 2008 tables print a candidate's column twice under a wide surname.
    while count > len(cands):
        twin = next((j for j in range(count - 1) if all(r[j] == r[j + 1] for r in grid)), None)
        if twin is None:
            raise ValueError(f"{name}: {count} candidate columns for {len(cands)} candidates")
        grid = [r[: twin + 1] + r[twin + 2 :] for r in grid]
        count -= 1
    if count != len(cands):
        raise ValueError(f"{name}: {count} candidate columns for {len(cands)} candidates")
    for row, ints in zip(table, grid, strict=True):
        label = re.sub(r"^\d+\s+", "", row.label).strip()
        cells = row.numbers
        votes = ints[:count]
        formal, informal, total = ints[count : count + 3]
        exhausted = ints[count + 3] if extra else None
        pair_values = None
        if tcp:
            pair_values = (integer(cells[-4]), integer(cells[-2]))
        fix = FIXES.get((day, name, label))
        if fix is not None:
            printed, corrected, reason = fix
            if total != printed or corrected != formal + informal:
                raise ValueError(f"{name}, {label}: the correction no longer fits the report")
            fixed.append(reason)
            total = corrected
        place = Place(label, votes, formal, informal, total, pair=pair_values, exhausted=exhausted)
        if label.startswith(TOTALS):
            declared = place
            break
        if label in SUBTOTALS:
            sums[label] = place
            continue
        if label in DECLARATION:
            place.kind = "aggregate"
        if not label:
            raise ValueError(f"{name}: a row with no label: {cells}")
        places.append(place)
    if declared is None:
        raise ValueError(f"{name}: no district total")
    check(name, places, declared, count, sums)
    if tcp:
        finalists = tcp_names(name, header, lines[first], width, cands, [*places, declared])
    enrolled = enrolment.get(fold(name))
    if enrolled is None:
        for line in lines[head + 1 : first]:
            words = line.split()
            if len(words) == 1 and words[0].isdigit() and int(words[0]) > 1000:
                enrolled = int(words[0])
    region = None
    for line in [*context[-12:], *lines[: head + 3]]:
        found = REGION.search(line)
        if found:
            region = found[1].strip()
    winner = None
    for i, line in enumerate(context):
        if "Elected Candidate for" in line:
            for after in context[i + 1 : i + 5]:
                found = re.match(r"^\s*([A-Za-z'’ -]+, [A-Za-z'’ .-]+?)\s{2,}", after)
                if found and "Candidate Name" not in after:
                    winner = found[1].strip()
                    break
    return District(name, cands, places, declared, finalists, enrolled, region, winner, fixed)


def consistent(place: Place) -> bool:
    """Whether a row's two-candidate preferred count fits its formal votes; a few rows of 2025 are misprinted."""
    if place.pair is None:
        return False
    if place.exhausted is not None:
        return sum(place.pair) + place.exhausted == place.formal
    return sum(place.pair) <= place.formal


def tcp_names(
    name: str, header: list[str], line: str, width: int, cands: list[Candidate], places: list[Place]
) -> tuple[int, int]:
    """The candidates in the two-candidate preferred columns.

    A finalist's two-candidate count at a place is never below their first preference votes there, which settles
    the pair in almost every district; the surnames printed above the columns settle the rest."""
    count = len(cands)
    feasible = [
        (i, j)
        for i in range(count)
        for j in range(count)
        if i != j
        and all(
            not consistent(p) or (p.votes[i] <= p.pair[0] and p.votes[j] <= p.pair[1])
            for p in places
            if p.pair is not None
        )
    ]
    if len(feasible) == 1:
        return feasible[0]
    cells = [t for t in tokens(line) if numeric(t[2])]
    edge = cells[-5][1] + 1
    printed = {w.replace("’", "'").strip("'") for text in header for start, _, w in tokens(text) if start >= edge}

    def shown(k: int, strict: bool) -> bool:
        c = cands[k]
        surname = c.surname.replace("’", "'").split()[-1].strip("'")
        parts = [surname, *surname.split("-")]
        found = any(part in printed for part in parts) or any(
            w.endswith("-") and surname.startswith(w) for w in printed
        )
        if strict:
            given = [*c.preferred.split(), *c.given.split()]
            found = found and (not given or any(w[:3] == g[:3] for g in given for w in printed))
        return found

    for strict in (True, False):
        named = [(i, j) for i, j in feasible if shown(i, strict) and shown(j, strict)]
        if len(named) == 1:
            return named[0]
    raise ValueError(f"{name}: cannot tell the two-candidate preferred pair from {feasible} and {sorted(printed)}")


def check(name: str, places: list[Place], declared: Place, count: int, sums: dict[str, Place]) -> None:
    """Check each row's arithmetic and that the rows add up to the printed totals."""
    for place in [*places, declared, *sums.values()]:
        if sum(place.votes) != place.formal:
            raise ValueError(f"{name}, {place.name}: votes add up to {sum(place.votes)}, not {place.formal}")
        if place.formal + place.informal != place.total:
            raise ValueError(f"{name}, {place.name}: formal plus informal is not the total")
    for column in range(count + 3):

        def cell(p: Place, column: int = column) -> int:
            return [*p.votes, p.formal, p.informal, p.total][column]

        if sum(cell(p) for p in places) != cell(declared):
            raise ValueError(
                f"{name}: column {column + 1} adds up to {sum(cell(p) for p in places)}, not {cell(declared)}"
            )


def blocks(text: str) -> list[tuple[list[str], list[str]]]:
    """Each district's table, from its heading to its distribution of preferences, with the lines before it."""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if any(s in lines[i] for s in STARTS):
            j = i + 1
            while j < len(lines) and STOP not in lines[j] and not any(s in lines[j] for s in STARTS):
                j += 1
            out.append((lines[i - 3 : j], lines[max(0, i - 60) : i - 3]))
            i = j
        else:
            i += 1
    return out


def enrolment(link: str | None) -> dict[str, int]:
    """Enrolment by district from the report's table of returning officers, areas, and enrolments."""
    if link is None:
        return {}
    text = layout(link)
    start = text.lower().find("areas, enrolment")
    out: dict[str, int] = {}
    for line in text[start : start + 30000].splitlines():
        found = re.search(r"(?:^|\s)(\d{1,2}) ([A-Z][A-Za-z'-]+(?: [A-Z][A-Za-z'-]+)*)\s{2,}(.*)$", line)
        if not found:
            continue
        numbers = [w for w in found[3].split() if INTEGER.fullmatch(w)]
        if len(numbers) >= 3:
            # A returning officer's name sometimes follows the district after a single space.
            words = found[2].split()
            for end in range(len(words), 0, -1):
                out.setdefault(fold(" ".join(words[:end])), integer(numbers[1]))
    return out


@functools.cache
def general(day: str) -> list[District]:
    spec = next(g for g in GENERALS if g.day == day)
    rolls = enrolment(spec.enrolment)
    lists = ballots(spec.candidates)
    out = []
    for lines, context in blocks(layout(spec.districts)):
        out.append(district(day, lines, context, rolls, lists))
    return out


# Legislative Council ------------------------------------------------------------------------------------------

REGIONS = [
    "East Metropolitan",
    "North Metropolitan",
    "South Metropolitan",
    "Agricultural",
    "Mining and Pastoral",
    "South West",
]  # the order in which the region profiles print the regions' counts


@dataclass(frozen=True)
class Council:
    day: str
    candidates: str  # the report holding the Legislative Council candidate list
    counts: str  # the report holding each region's count, whose first line gives the first preference votes
    elected: str | None = None  # the report listing the elected members, when the counts do not mark them


COUNCILS = [
    Council(
        "2008-09-06",
        url(f"{DOCS}/2008_SGE_Results_Stats_Part5.pdf"),
        url(f"{DOCS}/2008_SGE_Results_Stats_Part6.pdf"),
        url(f"{DOCS}/2008_SGE_Results_Stats_Part5.pdf"),
    ),
    Council(
        "2013-03-09",
        url(f"{DOCS}/2013_SGE_Results_Stats_Part5.pdf"),
        url(f"{DOCS}/2013_SGE_Results_Stats_Part6.pdf"),
        url(f"{DOCS}/2013_SGE_Results_Stats_Part5.pdf"),
    ),
    Council(
        "2017-03-11",
        url("/sites/default/files/content/documentssge2017/2017_Legislative Council online.pdf"),
        url("/sites/default/files/content/documentssge2017/2017_Regions Profiles & Election Results online.pdf"),
    ),
    Council(
        "2021-03-13",
        url("/sites/default/files/content/SGE 2021/SGE 2021 Reports/5_21_Res_Stats_Leg Council.pdf"),
        url("/sites/default/files/content/6_21_Res_Stats_Reg_Prof_Elect_Results a.pdf"),
    ),
]


@dataclass
class Region:
    name: str
    candidates: list[Candidate]
    votes: list[int]  # first preference votes, with each group's ticket votes counted for its first candidate
    formal: int
    elected: list[int]  # the elected candidates, in the order the count elected them
    informal: int
    enrolled: int


ORDINAL = re.compile(r"\b[1-9](?:st|nd|rd|th)\.\s+(\S[^,]*?),\s*([A-Z][A-Za-z'’-]*)")


def numbers_after(lines: list[str], start: int) -> list[int]:
    """The run of numbers on a count's first preference line, continued onto the next lines when the layout puts
    them there."""
    out: list[int] = []
    for index in range(start, min(start + 6, len(lines))):
        words = lines[index].split()
        if index == start:
            words = words[words.index("Preferences") + 1 :]
            if words[:1] == ["Votes"]:
                words = words[1:]
        elif "Surplus" in words or "Exclusion" in words:
            break
        for word in words:
            if not INTEGER.fullmatch(word):
                break
            out.append(integer(word))
        if len(out) > 2:
            break
    return out


def counts(link: str) -> list[tuple[list[int], list[tuple[str, str]]]]:
    """Each region's first preference votes, exhausted and formal totals, and the members its count marks as
    elected, in the order the report prints the regions."""
    lines = layout(link).splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(r"^\s*First Preferences\b", line)]
    out = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        values = numbers_after(lines, start)
        marked = [(m[1].strip(), m[2]) for line in lines[start - 6 : end - 6] for m in ORDINAL.finditer(line)]
        out.append((values, marked))
    return out


def elected_list(link: str) -> dict[str, list[tuple[str, str]]]:
    """The members each region elected, as the report's list of elected candidates prints them."""
    text = layout(link)
    start = text.find("Candidate (Ballot Paper Name)")
    out: dict[str, list[tuple[str, str]]] = {}
    region = ""
    for line in text[start:].splitlines()[1:]:
        found = re.match(r"^\s*([A-Z][A-Za-z ]+?)?\s+[1-9]\s+\*?\s*([^,]+),\s*([A-Za-z'’-]+)", line)
        if not found:
            if out and sum(len(v) for v in out.values()) >= 36:
                break
            continue
        if found[1]:
            region = found[1].strip()
        out.setdefault(fold(region), []).append((found[2].strip(), found[3]))
    return out


def norm(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.replace("’", "'").upper())


def member(cands: list[Candidate], surname: str, given: str) -> list[int]:
    """The candidates an elected member's printed name can be: the surname, and the given name when two share it."""
    hits = [i for i, c in enumerate(cands) if norm(c.surname) == norm(surname)]
    if len(hits) > 1:
        hits = [
            i
            for i in hits
            if any(norm(n)[:3] == norm(given)[:3] for n in (cands[i].preferred, *cands[i].given.split()) if n)
        ]
    return hits


def region_totals(link: str, counts_link: str, formal: int) -> tuple[int, int | None]:
    """A region's informal votes and enrolment from the report's table of the types of vote by region.

    The table's region total reads electors, the vote types, valid, informal, and total votes. Some years' layout
    breaks the row across lines, and then the informal count is the figure set beside the valid votes on one line
    and beside the total votes on another, and the enrolment is not read."""
    rows = [[integer(w) for w in line.split() if INTEGER.fullmatch(w)] for line in layout(link).splitlines()]
    for nums in rows:
        for i in range(1, len(nums) - 2):
            if nums[i] == formal and nums[0] > formal and nums[i] + nums[i + 1] == nums[i + 2]:
                return nums[i + 1], nums[0]
    beside = {nums[i + 1] for nums in rows for i in range(len(nums) - 1) if nums[i] == formal}
    found = {
        x for x in beside for nums in rows for i in range(len(nums) - 1) if nums[i] == x and nums[i + 1] == formal + x
    }
    if len(found) == 1:
        return found.pop(), None
    # Otherwise the region's count sheet prints its formal and informal votes.
    text = layout(counts_link)
    at = text.find(f"Formal Votes: {formal:,}")
    printed = re.findall(r"Informal Votes: ([\d,]+)", text[max(0, at - 6000) : at]) if at >= 0 else []
    if not printed:
        raise ValueError(f"no informal count beside {formal:,} valid votes in {link}")
    return integer(printed[-1]), None


@functools.cache
def council(day: str) -> list[Region]:
    spec = next(c for c in COUNCILS if c.day == day)
    lists = ballots(spec.candidates, "Region")
    found = counts(spec.counts)
    if len(found) != len(REGIONS) or set(lists) != {fold(r) for r in REGIONS}:
        raise ValueError(f"{day}: {len(found)} region counts for regions {sorted(lists)}")
    listed = elected_list(spec.elected) if spec.elected else {}
    out = []
    for name, (values, marked) in zip(REGIONS, found, strict=True):
        cands = lists[fold(name)]
        if len(values) != len(cands) + 2 or values[-2] != 0 or sum(values[:-2]) != values[-1]:
            raise ValueError(f"{day} {name}: {len(values)} first preference figures for {len(cands)} candidates")
        names = listed.get(fold(name), marked)
        elected = []
        for surname, given in names:
            hits = member(cands, surname, given)
            if len(hits) != 1:
                raise ValueError(f"{day} {name}: elected {surname}, {given} matches {len(hits)} candidates")
            elected.append(hits[0])
        if len(elected) != 6 or len(set(elected)) != 6:
            raise ValueError(f"{day} {name}: {len(elected)} members elected")
        informal, enrolled = region_totals(spec.candidates, spec.counts, values[-1])
        # The region's roll is its districts' rolls; where the table's row breaks, the districts give it.
        districts = sum(d.enrolled or 0 for d in checked(day) if d.region == name)
        if enrolled is not None and enrolled != districts:
            raise ValueError(f"{day} {name}: enrolment {enrolled:,} is not its districts' {districts:,}")
        out.append(Region(name, cands, values[:-2], values[-1], elected, informal, districts))
    return out


# Totals the reports print for the whole state, which the districts must add up to: formal and informal votes.
STATE = {
    "2001-02-10": (1_026_720, 48_836),
    "2005-02-26": (1_071_953, 59_316),
    "2008-09-06": (1_089_257, 61_240),
    "2013-03-09": (1_184_432, 75_657),
    "2017-03-11": (1_321_640, 62_860),
    "2021-03-13": (1_411_990, 55_183),
    "2025-03-08": (1_527_968, 69_071),
}
# The boundary determination each general election used, by election day.
PLANS = {
    "2001-02-10": "1994",
    "2005-02-26": "2003",
    "2008-09-06": "2007",
    "2013-03-09": "2011",
    "2017-03-11": "2015",
    "2021-03-13": "2019",
    "2025-03-08": "2023",
}


@functools.cache
def checked(day: str) -> list[District]:
    """The districts of a general election, checked against the report's candidate list and state totals."""
    spec = next(g for g in GENERALS if g.day == day)
    found = general(day)
    names = {fold(d.name) for d in found}
    if len(names) != len(found) or names != set(ballots(spec.candidates)):
        raise ValueError(f"{day}: the district tables do not match the candidate list")
    formal = sum(d.declared.formal for d in found)
    informal = sum(d.declared.informal for d in found)
    if (formal, informal) != STATE[day]:
        raise ValueError(f"{day}: the districts add up to {formal:,} formal and {informal:,} informal votes")
    for d in found:
        if d.enrolled is None:
            raise ValueError(f"{day} {d.name}: no enrolment")
    return found


# Parties --------------------------------------------------------------------------------------------------------

# The candidate lists print one party under several names over the years; each name maps to the party's
# current name.
PARTY_NAMES = {
    "Australian Labor Party": "WA Labor",
    "Liberal": "Liberal Party",
    "THE NATIONALS": "The Nationals WA",
    "NATIONAL PARTY": "The Nationals WA",
    "Greens (WA)": "The Greens (WA)",
    "ONE NATION": "Pauline Hanson's One Nation",
    "One Nation": "Pauline Hanson's One Nation",
    "Pauline Hanson's ONE NATION": "Pauline Hanson's One Nation",
    "FAMILY FIRST": "Family First",
    "SFFPWA": "Shooters, Fishers and Farmers",
    "Shooters and Fishers": "Shooters, Fishers and Farmers",
    "Legalise Cannabis Western Australia Party": "Legalise Cannabis Party WA",
    "DEMOCRATS": "Australian Democrats",
    "CITIZENS ELECTORAL COUNCIL": "Citizens Electoral Council",
    "NO MANDATORY VACCINATION": "No Mandatory Vaccination",
    "WESTERN AUSTRALIA PARTY": "Western Australia Party",
    "COMMUNITY 1ST": "Community 1st",
}
NO_PARTY = {"", "Independent", "No party affiliation"}


def party_name(printed: str) -> str | None:
    printed = re.sub(r"\s+\d+$", "", printed).strip()  # a page number the layout sets beside the last entry
    if printed in NO_PARTY:
        return None
    return PARTY_NAMES.get(printed, printed)


def party_id(name: str) -> str:
    return key("party", name.casefold())


def printed_parties() -> dict[str, set[str]]:
    """Each party's current name and the names the candidate lists print for it."""
    out: dict[str, set[str]] = {}
    lists = [d.candidates for g in GENERALS for d in checked(g.day)]
    lists += [r.candidates for c in COUNCILS for r in council(c.day)]
    for cands in lists:
        for c in cands:
            name = party_name(c.party)
            if name is not None:
                out.setdefault(name, set()).add(re.sub(r"\s+\d+$", "", c.party).strip())
    return out


# Packages -------------------------------------------------------------------------------------------------------

COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUS"))
WA = key("area", "au-wa.state", "WA")


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def district_area(day: str, name: str) -> str:
    return area("au-wa.district", f"{PLANS[day]}:{changeset.slug(name)}")


def region_area(day: str, name: str) -> str:
    return area("au-wa.region", f"{PLANS[day]}:{changeset.slug(name)}")


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=WAEC,
        kind="official_body",
        publisher="Western Australian Electoral Commission",
        title="Western Australian state election Results and Statistics reports",
        url=REPORTS,
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Western Australian Electoral Commission",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Western Australia and its Legislative Assembly districts and Legislative Council regions by boundary "
        "determination, as the Western Australian Electoral Commission names them in its election reports",
    )
    p.external |= {AUSTRALIA, COUNTRY}
    source_row(p)
    for code, rank, kind, noun in (
        ("au-wa.state", 1, "administrative", "state"),
        ("au-wa.region", 2, "electoral_district", "region"),
        ("au-wa.district", 2, "electoral_district", "district"),
        ("au-wa.polling_place", 3, "precinct", "polling place"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRALIA, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=WA, scheme_code="au-wa.state")
    p.add("area_code", area_id=WA, code="WA")
    p.add("area_parent", area_id=WA, parent_id=COUNTRY)
    p.name(WA, "Western Australia", "en", "official")
    regions: set[str] = set()
    found: dict[tuple[str, str], str] = {}
    for g in GENERALS:
        for d in checked(g.day):
            found[(PLANS[g.day], changeset.slug(d.name))] = d.name
    for plan in sorted({plan for plan, _ in found}):
        p.add(
            "boundary_set",
            id=key("boundary_set", "au-wa.district", plan),
            scheme_code="au-wa.district",
            vintage=plan,
            as_of=min(date.fromisoformat(day) for day, v in PLANS.items() if v == plan),
        )
    for c in COUNCILS:
        plan = PLANS[c.day]
        for r in council(c.day):
            id = region_area(c.day, r.name)
            if id in regions:
                continue
            regions.add(id)
            p.add("area", id=id, scheme_code="au-wa.region")
            p.add("area_code", area_id=id, code=f"{plan}:{changeset.slug(r.name)}")
            p.add("area_parent", area_id=id, parent_id=WA)
            p.name(id, f"{r.name} Region", "en", "official")
    for (plan, slug), name in sorted(found.items()):
        id = area("au-wa.district", f"{plan}:{slug}")
        p.add("area", id=id, scheme_code="au-wa.district")
        p.add("area_code", area_id=id, code=f"{plan}:{slug}")
        p.add("area_parent", area_id=id, parent_id=WA)
        p.name(id, name, "en", "official")
    return p


def registry() -> Package:
    names = printed_parties()
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        f"The Western Australian Legislative Assembly and Legislative Council, their electoral systems, and the "
        f"{len(names)} parties on the "
        "Western Australian Electoral Commission's candidate lists, with the report files every election cites",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(AUSTRALIA)
    for link in files():
        p.artifact(raw.cached(SOURCE, link), WAEC)
    p.add("body", id=COUNCIL, polity_id=AUSTRALIA, kind="upper_house")
    p.name(COUNCIL, "Western Australian Legislative Council", "en", "official")
    p.add(
        "electoral_system",
        id=COUNCIL_SYSTEM,
        polity_id=AUSTRALIA,
        rules={"formula": "stv", "quota": "droop", "surplus": "gregory"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", COUNCIL_SYSTEM),
        entity_id=COUNCIL_SYSTEM,
        kind="methodology",
        body_md="From 1989 to 2021 each of six regions elected members of the Legislative Council by the single "
        "transferable vote with a Droop quota: five or seven members a region until 2005, and six from 2008. A "
        "voter marked one box above the line, adopting a group's registered voting ticket, or numbered every "
        "candidate below the line.",
        lang="en",
    )
    p.add("body", id=ASSEMBLY, polity_id=AUSTRALIA, kind="lower_house")
    p.name(ASSEMBLY, "Western Australian Legislative Assembly", "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=AUSTRALIA, rules={"formula": "irv"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each district elects one member of the Legislative Assembly by full preferential voting: voters "
        "number every box, and the candidate with the fewest votes is excluded and their ballots passed on until "
        "one candidate holds an absolute majority.",
        lang="en",
    )
    for name, printed in sorted(names.items()):
        id = party_id(name)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, name, "en", "official")
        for other in sorted(printed - {name}):
            p.name(id, other, "en", "historical", False)
    return p


METHOD = (
    "First preference votes for each candidate at each polling place, from the district profiles of the Western "
    "Australian Electoral Commission's Results and Statistics report. Absent, early (postal and in person), "
    "provisional, and special institution, hospital, and remote votes are reported once per district and stored as "
    "one unit each. Informal ballot papers are those that do not count, and valid votes are the formal ballot "
    "papers. The enrolment is stored on a separate unit of the district. The report also gives a two-candidate "
    "preferred count at each polling place; the district's final two-candidate count is given in a note, because "
    "the archive stores a count only at the first preference stage. Official outcomes are stored as declared."
)


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def election(day: str) -> Package:
    spec = next(g for g in GENERALS if g.day == day)
    when = date.fromisoformat(day)
    districts = checked(day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {when.year} Western Australian state election's Legislative Assembly results by polling place"
        + (" and Legislative Council results by region" if any(c.day == day for c in COUNCILS) else "")
        + ", from the Western Australian Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(AUSTRALIA)
    for link in dict.fromkeys(x for x in (spec.districts, spec.candidates, spec.enrolment) if x):
        p.uses(raw.cached(SOURCE, link))
    election_id = key("election", day)
    path = f"au/wa/{day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="general",
        date_from=when,
        date_to=when,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{when.year} Western Australian state election", "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    for d in districts:
        slug = changeset.slug(d.name)
        district = district_area(day, d.name)
        contest = key("contest", day, "assembly", slug)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=ASSEMBLY,
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{d.name}, Legislative Assembly", "en", "official")
        p.add("slug", path=f"{path}/assembly-{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=WAEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        for n, fix in enumerate(d.fixes):
            p.add(
                "note",
                id=key("note", result_set, "fix", n),
                entity_id=result_set,
                kind="caveat",
                body_md=fix,
                lang="en",
            )
        options: list[str] = []
        for position, c in enumerate(d.candidates, 1):
            option = key("option", contest, position)
            options.append(option)
            name = party_name(c.party)
            party = party_id(name) if name else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=c.ballot,
            )
            person = key("person", contest, position)
            p.add("person", id=person)
            p.name(person, c.display, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", contest, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.ballot,
                member_of_party_id=party,
                is_independent=party is None,
            )
        seen: Counter[str] = Counter()
        for place in d.places:
            seen[place.name] += 1
            code = f"{day}:{slug}:{changeset.slug(place.name)}"
            if seen[place.name] > 1:
                code += f"-{seen[place.name]}"
            unit = key("unit", code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=AUSTRALIA,
                scheme_code="au-wa.polling_place",
                code=code,
                area_relation="within",
                area_id=district,
                kind=place.kind,
            )
            label = place.name if place.kind == "standard" else f"{d.name}: {place.name}"
            p.name(unit, label, "en", "official")
            for option, value in zip(options, place.votes, strict=True):
                put(p, result_set, unit, option, VOTES, value)
            put(p, result_set, unit, None, VALID, place.formal)
            put(p, result_set, unit, None, INVALID, place.informal)
            put(p, result_set, unit, None, CAST, place.total)
        roll = key("unit", day, slug, "roll")
        p.add(
            "reporting_unit",
            id=roll,
            polity_id=AUSTRALIA,
            scheme_code="au-wa.polling_place",
            code=f"{day}:{slug}:roll",
            area_relation="within",
            area_id=district,
            kind="aggregate",
        )
        p.name(roll, f"{d.name}: electoral roll", "en", "official")
        assert d.enrolled is not None
        put(p, result_set, roll, None, REGISTERED, d.enrolled)
        total = d.declared
        p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=d.enrolled)
        for measure, value in ((CAST, total.total), (INVALID, total.informal), (VALID, total.formal)):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option, value in zip(options, total.votes, strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
        if d.finalists is None or total.pair is None:
            raise ValueError(f"{day} {d.name}: no two-candidate preferred count")
        first, second = d.finalists
        pair = total.pair
        win = first if pair[0] > pair[1] else second
        listing = f"{d.candidates[first].display} {pair[0]:,} and {d.candidates[second].display} {pair[1]:,}"
        body = f"After the distribution of preferences the two remaining candidates held {listing} votes."
        if total.exhausted:
            body += f" {total.exhausted:,} ballot papers exhausted before the final count."
        p.add(
            "note", id=key("note", result_set, "final"), entity_id=result_set, kind="context", body_md=body, lang="en"
        )
        for index, option in enumerate(options):
            kind = "elected" if index == win else "lost" if index in d.finalists else "eliminated"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    if any(c.day == day for c in COUNCILS):
        council_contests(p, day, election_id, path)
    return p


COUNCIL_METHOD = (
    "First preference votes for each candidate in the region, from the first line of the Western Australian "
    "Electoral Commission's count of the region in its Results and Statistics report. As in the commission's "
    "count, the votes cast above the line for a group's voting ticket are counted as first preferences for the "
    "group's first candidate. Informal ballot papers and the enrolment are the commission's figures for the region; "
    "the enrolment is the sum of its districts' enrolments. The later counts are not stored, and the members are "
    "listed as the commission declared them elected."
)


def council_contests(p: Package, day: str, election_id: str, path: str) -> None:
    spec = next(c for c in COUNCILS if c.day == day)
    for link in dict.fromkeys(x for x in (spec.candidates, spec.counts, spec.elected) if x):
        p.uses(raw.cached(SOURCE, link))
    for r in council(day):
        slug = changeset.slug(r.name)
        region = region_area(day, r.name)
        contest = key("contest", day, "council", slug)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=COUNCIL,
            district_area_id=region,
            magnitude=len(r.elected),
            round_number=1,
            electoral_system_id=COUNCIL_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{r.name} Region, Legislative Council", "en", "official")
        p.add("slug", path=f"{path}/council-{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=WAEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=COUNCIL_METHOD,
            lang="en",
        )
        unit = key("unit", day, "council", slug)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRALIA,
            scheme_code="au-wa.region",
            code=f"{day}:{slug}",
            area_relation="equals",
            area_id=region,
            kind="standard",
        )
        p.name(unit, f"{r.name} Region", "en", "official")
        for position, (c, votes) in enumerate(zip(r.candidates, r.votes, strict=True), 1):
            option = key("option", contest, position)
            name = party_name(c.party)
            party = party_id(name) if name else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=c.ballot,
            )
            person = key("person", contest, position)
            p.add("person", id=person)
            p.name(person, c.display, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", contest, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.ballot,
                member_of_party_id=party,
                is_independent=party is None,
            )
            put(p, result_set, unit, option, VOTES, votes)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
            won = position - 1 in r.elected
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if won else "lost")
        for measure, value in (
            (VALID, r.formal),
            (INVALID, r.informal),
            (CAST, r.formal + r.informal),
            (REGISTERED, r.enrolled),
        ):
            put(p, result_set, unit, None, measure, value)
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)


def files() -> list[str]:
    links = [link for g in GENERALS for link in (g.districts, g.candidates, g.enrolment) if link]
    links += [link for c in COUNCILS for link in (c.candidates, c.counts, c.elected) if link]
    return list(dict.fromkeys(links))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{g.day}" for g in GENERALS)]


def fetch() -> None:
    for link in files():
        raw.fetch(SOURCE, link, suffix=".pdf")


def parse() -> None:
    geo().write()
    registry().write()
    for g in GENERALS:
        election(g.day).write()


def emit() -> None:
    package.run("load", packages())
