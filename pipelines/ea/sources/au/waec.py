"""Western Australian state elections from the Western Australian Electoral Commission (WAEC).

The commission publishes a Results and Statistics report after each state general election. Its district profiles
print the Legislative Assembly's first preference votes for each candidate at each polling place, the declaration
votes by type, and the two-candidate preferred count; its region profiles print each Legislative Council region's
count, whose first line gives every candidate's first preference votes, and from 2008 a first preference analysis
of each region: ticket and below-the-line votes for each group and candidate at each polling place (2008 to 2017)
or by district and type of vote (2021). The referendum reports print yes and no votes at each polling place. The
reports are PDFs with a text layer, read here with `pdftotext -layout`, and the analysis tables, whose candidate
names are set rotated over their columns, with `pdftotext -bbox`. One package holds each general election and each
referendum, plus a `geo` and a `registry` package.
"""

import difflib
import functools
import html
import itertools
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.au import waec_shapes

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
REFERENDUM_SYSTEM = key("electoral_system", "referendum")
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
            prefix = re.match(r"^([A-Z][a-z]+)([A-Z]{2}.*)$", word)  # McSWEENEY, MacTIERNAN, DeCAMPO
            if prefix:
                word = prefix[1] + prefix[2].capitalize()
            elif re.search(r"[A-Z]{2}", word):
                word = re.sub(r"[A-Za-z]+", lambda m: m[0].capitalize(), word)  # D'ORAZIO, BENSON-LIDHOLM
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
    # A long given name sometimes runs into the party with one space between them.
    known = sorted({c.party for _, entries in found_lists for c in entries if c.party}, key=len, reverse=True)
    for _, entries in found_lists:
        for c in entries:
            if not c.party:
                party = next((k for k in known if c.given.endswith(f" {k}")), None)
                if party:
                    c.given, c.party = c.given.removesuffix(f" {party}"), party
    out: dict[str, list[Candidate]] = {}
    for names, entries in found_lists:
        name = "".join(n if n.endswith("-") else f"{n} " for n in names).strip()
        if fold(name) in out:
            raise ValueError(f"{name} is listed twice in {link}")
        out[fold(name)] = entries
    return out


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


# Legislative Council by polling place -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def rotated(self) -> bool:
        return self.y1 - self.y0 > 1.6 * (self.x1 - self.x0) and bool(re.search(r"[A-Za-z]", self.text))

    @property
    def middle(self) -> float:
        return (self.y0 + self.y1) / 2


WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')


@functools.cache
def boxes(link: str) -> list[list[Box]]:
    """Each page's words with their boxes, from `pdftotext -bbox`."""
    path = str(raw.cached(SOURCE, link).path)
    text = subprocess.run(["pdftotext", "-bbox", path, "-"], check=True, capture_output=True, text=True).stdout
    pages = []
    for page in text.split("<page ")[1:]:
        found = WORD.findall(page)
        pages.append([Box(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in found])
    return pages


def lines_of(words: list[Box]) -> list[list[Box]]:
    """Words grouped into lines by their vertical middles, each line left to right."""
    out: list[list[Box]] = []
    for w in sorted(words, key=lambda w: w.middle):
        if out and abs(out[-1][0].middle - w.middle) < 0.45 * (w.y1 - w.y0):
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: w.x0) for line in out]


DIGITS = re.compile(r"\d{1,3}(?:[,.]\d{3})*")  # one 2008 cell prints its thousands with a full stop


def cells_of(line: list[Box], edge: float) -> list[Box]:
    """The numbers right of `edge` on a line, joining a thousands group the report sets apart with a space."""
    out: list[Box] = []
    for w in line:
        if w.x0 < edge or not DIGITS.fullmatch(w.text):
            continue
        if (
            out
            and re.fullmatch(r"\d{3}", w.text)
            and re.fullmatch(r"\d{1,3}", out[-1].text)
            and w.x0 - out[-1].x1 < 0.4 * (w.y1 - w.y0)
        ):
            last = out.pop()
            w = Box(last.x0, last.y0, w.x1, w.y1, last.text + w.text)
        out.append(w)
    return out


MISPRINTED = {"agricuturalregion": "Agricultural"}  # region headings the reports misspell
DISTRICT_MISPRINTS = {"blemont": "Belmont"}  # district names the summaries misspell
# Lines of polling place names the region profiles shorten past recognition, to the name the district profile
# prints: (day, district, line) to name.
LABEL_FIXES = {("2013-03-09", "Perth", "Northbridge"): "Central Institute of Technology - Northbridge"}


@dataclass
class Sheet:
    """One page of a region's first preference analysis: the region's summary by district and type of vote, or one
    district's polling places."""

    region: str
    district: str | None  # None for the region's summary
    columns: list[str]  # each vote column's kind ("ticket", "first", or "pref"), then "valid", "informal", "total"
    names: list[str]  # the rotated candidate names over the columns, one per candidate
    rows: list[tuple[str, str, list[int]]]  # (type of vote, label, cells)
    page: int
    labels: list[str] = field(default_factory=list)  # a district page's lines of polling place names, in order


def blocks_of(words: list[Box]) -> list[list[Box]]:
    """Rotated words grouped into the names they make, left to right."""
    out: list[list[Box]] = []
    for w in sorted(words, key=lambda w: w.x0):
        if out and w.x0 < max(v.x1 for v in out[-1]) + 0.5:
            out[-1].append(w)
        else:
            out.append([w])
    return out


def anchors(link: str, number: int, words: list[Box], top: float) -> tuple[list[tuple[str, float]], list[str]]:
    """The vote columns of a page as (kind, centre), and the candidate names over them.

    Each candidate's name is printed rotated over their column, and a group's first candidate's name over both the
    group's ticket column and the candidate's own. The Ticket and Pref headings give the width of a column, and the
    spacing of the names then tells a group's first candidate (one and a half or two columns from the next name)
    from the others (one column). The headings themselves are not used to place the columns, because some pages set
    them under the wrong candidates."""
    head = [w for w in words if not w.rotated and top - 2 < w.y0 < top + 2]
    tickets = sorted((w for w in head if w.text == "Ticket"), key=lambda w: w.x0)
    prefs = sorted((w for w in head if w.text == "Pref"), key=lambda w: w.x0)
    gaps = []
    for t in tickets:
        p = next((p for p in prefs if p.x0 > t.x1), None)
        if p is not None:
            gaps.append((p.x0 + p.x1 - t.x0 - t.x1) / 2)
    width = sorted(gaps)[len(gaps) // 2]
    # A rotated word's box is as wide as its font is tall; a short one is not much taller than that.
    widths = Counter(round(w.x1 - w.x0, 1) for w in words if w.rotated and w.y1 < top)
    if not widths:
        raise ValueError(f"{link} page {number}: no candidate names")
    size = widths.most_common(1)[0][0]
    floor = min(w.y0 for w in words if w.rotated and w.y1 < top and round(w.x1 - w.x0, 1) == size) - 1
    rotated = [
        w
        for w in words
        if floor < w.y0 and w.y1 < top and abs(w.x1 - w.x0 - size) < 0.3 and re.search(r"[A-Za-z]", w.text)
    ]
    found: list[list[Box]] = []
    for block in blocks_of(rotated):
        # The lines of one name sit closer together than a column's width.
        if (
            found
            and (min(w.x0 for w in block) + max(w.x1 for w in block)) / 2
            - (min(w.x0 for w in found[-1]) + max(w.x1 for w in found[-1])) / 2
            < 0.6 * width
        ):
            found[-1] += block
        else:
            found.append(block)
    centres = [(min(w.x0 for w in b) + max(w.x1 for w in b)) / 2 for b in found]
    names = [" ".join(w.text for w in sorted(b, key=lambda w: (w.y0, w.x0))) for b in found]
    first = [1]
    for a, b in itertools.pairwise(centres):
        step = (b - a) / width
        f = round(2 * (step - 1) - first[-1])
        if f not in (0, 1) or abs(step - 1 - (first[-1] + f) / 2) > 0.3:
            raise ValueError(f"{link} page {number}: names {a:.1f} and {b:.1f} are {step:.2f} columns apart")
        first.append(f)
    if sum(first) != len(tickets):
        raise ValueError(f"{link} page {number}: {sum(first)} groups by the names, {len(tickets)} ticket columns")
    out: list[tuple[str, float]] = []
    for centre, f in zip(centres, first, strict=True):
        out += [("ticket", centre - width / 2), ("first", centre + width / 2)] if f else [("pref", centre)]
    return out, names


def sheets(link: str, districts: dict[str, list[str]]) -> list[Sheet]:
    """The first preference analysis pages of a region profiles report, with each column of votes placed by the
    page's headings. `districts` lists each region's districts."""
    out = []
    for number, words in enumerate(boxes(link), 1):
        found = sheet(link, number, words, districts)
        if found is not None:
            out.append(found)
    return out


def sheet(link: str, number: int, words: list[Box], districts: dict[str, list[str]]) -> Sheet | None:
    flat = [w for w in words if not w.rotated]
    tickets = [w for w in flat if w.text == "Ticket"]
    if not tickets or max(w.x0 for w in tickets) - min(w.x0 for w in tickets) < 5:
        return None  # no table, or the 2017 summary, which sets the candidates in rows
    top = min(w.y0 for w in tickets)
    heading = [" ".join(w.text for w in line) for line in lines_of([w for w in flat if w.y1 < top])]
    region = next(
        (r for h in heading for r in REGIONS if fold(h).startswith(fold(f"{r} Region"))),
        next((r for h in heading for m, r in MISPRINTED.items() if fold(h).startswith(m)), None),
    )
    if region is None:
        raise ValueError(f"{link} page {number}: no region in {heading}")
    known = {fold(d): d for d in districts[region]}
    report = max(i for i, h in enumerate(heading) if "Report" in h)
    district = next((known[fold(h)] for h in heading[report + 1 : report + 2] if fold(h) in known), None)
    columns, names = anchors(link, number, words, top)
    totals = []
    for label in ("Valid", "Informal", "Totals"):
        found = [w for w in flat if w.text == label and w.y1 < top + 12 and w.x0 > columns[-1][1]]
        if label == "Totals" and not found:
            found = [w for w in flat if w.text == "Total" and w.y1 < top + 12 and w.x0 > columns[-1][1]]
        if not found:
            raise ValueError(f"{link} page {number}: no {label} heading")
        w = max(found, key=lambda w: w.x0)
        totals.append((label.lower().removesuffix("s"), (w.x0 + w.x1) / 2))
    columns += totals
    centres = [x for _, x in columns]
    edge = centres[0] - 0.75 * (centres[1] - centres[0])
    lines: list[tuple[list[Box], list[int] | None]] = []
    for line in lines_of([w for w in flat if w.y0 > top + 2]):
        cells = cells_of(line, edge)
        values = [0] * len(columns)
        for c in cells:
            at = max((i for i, x in enumerate(centres) if x < c.x1), default=None)
            if at is None or (at + 1 < len(centres) and c.x1 > centres[at + 1]) or values[at]:
                raise ValueError(f"{link} page {number}: cell {c.text} at {c.x1:.1f} has no column")
            values[at] = integer(c.text.replace(".", ","))
        label = [w for w in line if w.x1 < centres[0] and not DIGITS.fullmatch(w.text)]
        lines.append((label, values if cells else None))
    width = len(columns)
    rows = place_rows(lines, width) if district else summary_rows(lines, width, districts[region])
    labels = []
    if district:
        for found, _ in lines:
            label = " ".join(w.text for w in found).strip()
            if label.startswith("Total Ordinary"):
                break
            if label and label not in ("Polling Places", "Polling Place"):
                labels.append(label)
    return Sheet(region, district, [k for k, _ in columns], names, rows, number, labels)


def vote_type(label: str) -> str | None:
    """The type of declaration vote a row label names, or None for a polling place."""
    if not re.match(r"(Absent|Early|Provisional|Special Inst|Ordinary)", label):
        return None
    text = label.casefold()
    for word, kind in (
        ("absent", "Absent Votes"),
        ("post", "Early Votes (by Post)"),
        ("person", "Early Votes (In Person)"),
        ("provisional", "Provisional Votes"),
        ("special", "Special Institutions, Hospitals & Remotes"),
        ("ordinary", "Ordinary Votes"),
    ):
        if word in text:
            return kind
    raise ValueError(f"unknown type of vote {label!r}")


TOTAL_ROW = re.compile(r"^(TOTALS?|Totals?)\b(?: \d+| Page| of| \d+ of \d+)*$")


def place_rows(lines: list[tuple[list[Box], list[int] | None]], width: int) -> list[tuple[str, str, list[int]]]:
    """A district page's rows: its polling places, Total Ordinary, the declaration votes by type, Subtotal, and
    TOTAL. A row of blank cells is a row of zeros. The layout sometimes sets a row's name on the line above or below
    its cells, wraps a polling place's name, or sets a row's total beside the first line of its name and its other
    cells beside the second; each is joined back up."""
    reserved = ("Subtotal", "Total Ordinary", "TOTAL")
    rows: list[tuple[str, str, list[int]]] = []
    orphan = False  # whether the last row is a name with no cells of its own yet
    pending: list[str] = []  # lines of a polling place's name with no cells
    for words, values in lines:
        label = " ".join(w.text for w in words).strip()
        if TOTAL_ROW.match(label):
            label = "TOTAL"
        kind = vote_type(label)
        name = kind or label
        if values is None:
            if not label or label in ("Remotes", "Remote"):
                continue
            if rows and not rows[-1][1]:
                rows[-1] = ("", name, rows[-1][2])  # a name below its cells
            elif kind or label in reserved:
                rows.append(("", name, [0] * width))
                orphan = True
            else:
                pending.append(label)
            continue
        if not label and orphan:
            rows[-1] = ("", rows[-1][1], values)  # a name above its cells
            orphan = False
            continue
        if not label and pending:
            name, pending = " ".join(pending), []
        elif pending and rows:
            rows[-1] = ("", " ".join([rows[-1][1], *pending]), rows[-1][2])  # a name that wraps
            pending = []
        orphan = False
        last = rows[-1] if rows else None
        if (
            last is not None
            and last[1]
            and not kind
            and name not in reserved
            and vote_type(last[1]) is None
            and last[1] not in reserved
            and not any(a and b for a, b in zip(last[2], values, strict=True))
            and (not label or (not any(last[2][:-1]) and not values[-1]))
        ):
            rows[-1] = ("", f"{last[1]} {name}".strip(), [a + b for a, b in zip(last[2], values, strict=True)])
            continue
        rows.append(("", name, values))
    return rows


def summary_rows(
    lines: list[tuple[list[Box], list[int] | None]], width: int, districts: list[str]
) -> list[tuple[str, str, list[int]]]:
    """A region summary's rows as (type of vote, district, cells), with Subtotal and TOTALS rows. The type of vote
    is printed down the first column beside its block of districts, ending at the block's Subtotal."""
    known = {fold(d): d for d in districts}
    known |= {m: d for m, d in DISTRICT_MISPRINTS.items() if d in districts}
    starts = [w.x0 for words, _ in lines for w in words if w.text in ("Subtotal",)]
    column = min(starts) - 1 if starts else 0.0
    rows: list[tuple[str, str, list[int]]] = []
    block: list[tuple[str, list[int]]] = []
    fragments: list[str] = []
    name: list[str] = []
    for words, values in lines:
        left = " ".join(w.text for w in words if w.x0 < column)
        right = " ".join(w.text for w in words if w.x0 >= column)
        if TOTAL_ROW.match(left) or TOTAL_ROW.match(right):
            rows.append(("", "TOTALS", values or [0] * width))
            break
        if left and left != "Districts":
            fragments.append(left)
        if not right:
            continue
        name.append(right)
        label = " ".join(name)
        if label == "Subtotal":
            kind = vote_type(" ".join(fragments))
            if kind is None:
                raise ValueError(f"no type of vote in {fragments}")
            rows += [(kind, d, v) for d, v in block] + [(kind, "Subtotal", values or [0] * width)]
            block, fragments, name = [], [], []
        elif fold(label) in known:
            block.append((known[fold(label)], values or [0] * width))
            name = []
        elif not any(k.startswith(fold(label)) for k in known):
            raise ValueError(f"unknown district {label!r}")
    return rows


@dataclass
class Tally:
    """One row of a region's first preference analysis."""

    name: str
    kind: str  # "standard" for a polling place, "aggregate" for a type of declaration vote
    tickets: list[int]  # each group's votes above the line
    prefs: list[int]  # each candidate's first preference votes below the line
    formal: int
    informal: int
    total: int

    def __add__(self, other: Tally) -> Tally:
        return Tally(
            self.name,
            self.kind,
            [a + b for a, b in zip(self.tickets, other.tickets, strict=True)],
            [a + b for a, b in zip(self.prefs, other.prefs, strict=True)],
            self.formal + other.formal,
            self.informal + other.informal,
            self.total + other.total,
        )

    def cells(self) -> tuple[int, ...]:
        return (*self.tickets, *self.prefs, self.formal, self.informal, self.total)


@dataclass
class Booths:
    """A region's first preference votes by polling place, or by district and type of vote where the report prints
    no polling places."""

    groups: list[list[int]]  # each group's candidates in ballot order; the group's ticket counts for the first
    loose: list[int]  # candidates outside any group with a ticket
    places: dict[str, list[Tally]]  # by district


# Printed subtotal and total rows that differ from the rows above them, where the district's other totals show
# the rows are right: (day and district, row) to [(cell, sum of the rows, printed)], the cells in `Tally.cells` order.
SUBTOTAL_MISPRINTS: dict[tuple[str, str], list[tuple[int, int, int]]] = {
    ("2008-09-06 Balcatta", "Subtotal"): [(26, 1, 0)],  # its absent votes and TOTAL row give the 1
    ("2008-09-06 Carine", "TOTAL"): [(1, 16, 6)],  # Total Ordinary 11 and Subtotal 5 make 16
}
ORDINARY = "Ordinary Votes"
TYPES = [
    ORDINARY,
    "Absent Votes",
    "Early Votes (by Post)",
    "Early Votes (In Person)",
    "Provisional Votes",
    "Special Institutions, Hospitals & Remotes",
]


def structure(columns: list[str], cands: list[Candidate]) -> tuple[list[list[int]], list[int]]:
    """The groups with a voting ticket, and the candidates outside them, by the columns: a group is its ticket
    column and its candidates' columns. A candidate of another party after a group's candidates, with no ticket
    column of their own, is ungrouped or in a group that lodged no ticket."""
    groups: list[list[int]] = []
    loose: list[int] = []
    at = 0
    inside = False
    for kind in columns:
        if kind == "ticket":
            groups.append([])
            inside = True
        elif kind in ("first", "pref"):
            if kind == "pref" and inside and party_name(cands[at].party) != party_name(cands[groups[-1][0]].party):
                inside = False
            if inside:
                groups[-1].append(at)
            else:
                loose.append(at)
            at += 1
    if at != len(cands):
        raise ValueError(f"{at} candidate columns for {len(cands)} candidates")
    return groups, loose


def named(names: list[str], cands: list[Candidate]) -> bool:
    """Whether the rotated names over the columns are the candidates in ballot order."""
    if len(names) != len(cands):
        return False
    for printed, c in zip(names, cands, strict=True):
        words = {norm(w) for w in printed.split() if len(norm(w)) > 1}
        parts = [norm(p) for p in re.split(r"[\s-]+", c.surname) if len(norm(p)) > 1]
        given = [norm(g) for g in (c.preferred, *c.given.split()) if g]
        if not any(p.startswith(w) or w.startswith(p) for w in words for p in parts) and not any(
            w[:3] == g[:3] for w in words for g in given
        ):
            return False
    return True


def tally(name: str, kind: str, columns: list[str], values: list[int], check: bool = True) -> Tally:
    tickets = [v for k, v in zip(columns, values, strict=True) if k == "ticket"]
    prefs = [v for k, v in zip(columns, values, strict=True) if k in ("first", "pref")]
    formal, informal, total = values[-3:]
    out = Tally(name, kind, tickets, prefs, formal, informal, total)
    if check and (sum(tickets) + sum(prefs) != formal or formal + informal != total):
        raise ValueError(f"{name}: {values} do not add up")
    return out


def summed(name: str, rows: list[Tally]) -> Tally:
    out = Tally(name, "aggregate", [0] * len(rows[0].tickets), [0] * len(rows[0].prefs), 0, 0, 0)
    for row in rows:
        out = out + row
    return out


def polling(where: str, columns: list[str], rows: list[tuple[str, str, list[int]]]) -> tuple[list[Tally], Tally]:
    """A district's polling places and declaration votes, checked against its printed subtotals and total, with
    the district's ordinary votes."""
    places: list[Tally] = []
    declared: list[Tally] = []
    printed: dict[str, Tally] = {}
    for _, label, values in rows:
        if label in ("Total Ordinary", "Subtotal", "TOTAL"):
            printed[label] = tally(f"{where}: {label}", "aggregate", columns, values, check=False)
            if label == "TOTAL":
                break
        elif label in TYPES:
            declared.append(tally(label, "aggregate", columns, values))
        elif "Total Ordinary" in printed:
            raise ValueError(f"{where}: polling place {label} after the ordinary votes")
        else:
            places.append(tally(label, "standard", columns, values))
    declared.sort(key=lambda d: TYPES.index(d.name))
    if set(printed) != {"Total Ordinary", "Subtotal", "TOTAL"} or [d.name for d in declared] != TYPES[1:]:
        raise ValueError(f"{where}: rows {[label for _, label, _ in rows]}")
    ordinary = summed(where, places)
    for label, found in (
        ("Total Ordinary", ordinary),
        ("Subtotal", summed(where, declared)),
        ("TOTAL", ordinary + summed(where, declared)),
    ):
        wrong = [
            (n, a, b) for n, (a, b) in enumerate(zip(found.cells(), printed[label].cells(), strict=True)) if a != b
        ]
        if wrong and SUBTOTAL_MISPRINTS.get((where, label)) != wrong:
            raise ValueError(
                f"{where}: the rows' sums differ from the {label} row's cells (column, sum, printed) {wrong}"
            )
    return places + declared, ordinary


def segment(lines: list[str], count: int, known: list[str]) -> list[str] | None:
    """The lines of a district's polling place names split into `count` names the district's Assembly profile
    prints, when they split one way only."""
    words = " ".join(lines).split()
    names = {}
    for name in known:
        names.setdefault(len(name.split()), {})[fold(name)] = name
    found: list[list[str]] = []

    def walk(at: int, out: list[str]) -> None:
        if len(found) > 1:
            return
        if at == len(words):
            if len(out) == count:
                found.append(list(out))
            return
        for size, table in names.items():
            text = fold(" ".join(words[at : at + size]))
            name = table.get(text)
            if name is None:  # the report cuts a long name's last letters off
                name = next((n for k, n in table.items() if k.startswith(text) and len(k) - len(text) <= 2), None)
            if name is not None:
                walk(at + size, [*out, name])

    walk(0, [])
    if len(found) == 1:
        return found[0]
    if found:
        return None
    # Otherwise the closest split, where every name is near one the Assembly profile prints: the two reports
    # spell some names a letter or a word apart.
    longest = max(len(n.split()) for n in known) + 2
    keys = [(fold(n), n) for n in known]

    @functools.cache
    def near(at: int, size: int) -> tuple[float, str] | None:
        text = fold(" ".join(words[at : at + size]))
        score, name = max((difflib.SequenceMatcher(None, text, k).ratio(), n) for k, n in keys)
        return (score, name) if score >= 0.85 else None

    @functools.cache
    def best(at: int, left: int) -> tuple[float, tuple[str, ...]] | None:
        if at == len(words):
            return (0.0, ()) if left == 0 else None
        if left == 0:
            return None
        out = None
        for size in range(1, min(longest, len(words) - at) + 1):
            hit = near(at, size)
            rest = best(at + size, left - 1) if hit else None
            if hit and rest and (out is None or hit[0] + rest[0] > out[0]):
                out = (hit[0] + rest[0], (hit[1], *rest[1]))
        return out

    result = best(0, count)
    if result is None or len(set(result[1])) != count:
        return None
    return list(result[1])


def by_type(where: str, columns: list[str], rows: list[tuple[str, str, list[int]]]) -> dict[tuple[str, str], Tally]:
    """A region summary's rows by type of vote and district, checked against its subtotals and total."""
    out: dict[tuple[str, str], Tally] = {}
    blocks: dict[str, list[Tally]] = {}
    total = None
    for kind, label, values in rows:
        row = tally(f"{where}: {kind} {label}", "aggregate", columns, values)
        if label == "TOTALS":
            total = row
        elif label == "Subtotal":
            if summed(where, blocks[kind]).cells() != row.cells():
                raise ValueError(f"{where}: {kind} does not add up to its subtotal")
        else:
            out[(kind, label)] = row
            blocks.setdefault(kind, []).append(row)
    if total is None or list(blocks) != TYPES or summed(where, list(out.values())).cells() != total.cells():
        raise ValueError(f"{where}: the types of vote {list(blocks)} do not add up to the total")
    return out


@functools.cache
def booths(day: str) -> dict[str, Booths]:
    """Each region's first preference votes by polling place, checked against the report's district totals, its
    region summary where it prints one in columns, and the region's count."""
    spec = next(c for c in COUNCILS if c.day == day)
    districts = {r: [d.name for d in checked(day) if d.region == r] for r in REGIONS}
    pages = sheets(spec.counts, districts)
    out: dict[str, Booths] = {}
    for region in council(day):
        name = region.name
        mine = [s for s in pages if s.region == name]
        if len({tuple(s.columns) for s in mine}) != 1:
            raise ValueError(f"{day} {name}: the pages set out the columns differently")
        columns = mine[0].columns
        for s in mine:
            if not named(s.names, region.candidates):
                raise ValueError(f"{day} {name} page {s.page}: names {s.names} are not the candidates in order")
        groups, loose = structure(columns, region.candidates)
        places: dict[str, list[Tally]] = {}
        ordinary: dict[str, Tally] = {}
        assembly = {d.name: [p.name for p in d.places if p.kind == "standard"] for d in checked(day)}
        for d in districts[name]:
            rows = [row for s in mine if s.district == d for row in s.rows]
            if not rows:
                continue
            places[d], ordinary[d] = polling(f"{day} {d}", columns, rows)
            # The district's polling places, named as its Assembly profile names them.
            labels = [LABEL_FIXES.get((day, d, line), line) for s in mine if s.district == d for line in s.labels]
            standard = [t for t in places[d] if t.kind == "standard"]
            found = segment(labels, len(standard), assembly[d])
            if found is None:
                raise ValueError(f"{day} {d}: cannot name {len(standard)} polling places from {labels}")
            for t, place in zip(standard, found, strict=True):
                t.name = place
        summaries = [s for s in mine if s.district is None]
        if len(summaries) > 1:
            raise ValueError(f"{day} {name}: {len(summaries)} summaries")
        if summaries:
            found = by_type(f"{day} {name}", columns, summaries[0].rows)
            if {d for _, d in found} != set(districts[name]):
                raise ValueError(f"{day} {name}: the summary's districts are {sorted({d for _, d in found})}")
            for d in districts[name]:
                rows = [found[(kind, d)] for kind in TYPES]
                if d not in places:
                    # The report prints no polling places for the district: its votes by type of vote.
                    places[d] = [
                        Tally(kind, "aggregate", r.tickets, r.prefs, r.formal, r.informal, r.total)
                        for kind, r in zip(TYPES, rows, strict=True)
                    ]
                    continue
                mine_rows = [ordinary[d], *places[d][-5:]]
                for kind, a, b in zip(TYPES, rows, mine_rows, strict=True):
                    if a.cells() != b.cells():
                        raise ValueError(f"{day} {d}: {kind} in the summary is not the district's polling places")
        if set(places) != set(districts[name]):
            raise ValueError(f"{day} {name}: first preferences for {sorted(places)}, not {districts[name]}")
        total = summed(name, [t for rows in places.values() for t in rows])
        votes = list(total.prefs)
        for g, members in enumerate(groups):
            votes[members[0]] += total.tickets[g]
        if votes != region.votes or total.formal != region.formal or total.informal != region.informal:
            raise ValueError(
                f"{day} {name}: the polling places add up to {votes}, {total.formal:,} formal and "
                f"{total.informal:,} informal, not the count's {region.votes}, {region.formal:,}, and {region.informal:,}"
            )
        out[name] = Booths(groups, loose, places)
    return out


# Referendums ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Referendum:
    day: str
    report: str
    title: str
    questions: list[str]  # each question's short name, in the report's order
    state: list[tuple[int, int, int, int]]  # the report's statewide yes, no, informal, and total votes per question


REFERENDUMS = [
    Referendum(
        "2005-02-26",
        url(f"{DOCS}/2005_Retail_Trading_Ref_Report.pdf"),
        "2005 Western Australian retail trading hours referendum",
        ["Question 1: weeknight trading", "Question 2: Sunday trading"],
        [(457_183, 648_860, 23_158, 1_129_201), (422_942, 672_478, 33_644, 1_129_064)],
    ),
    Referendum(
        "2009-05-16",
        url(f"{DOCS}/2009_DSR_ Report.pdf"),
        "2009 Western Australian daylight saving referendum",
        ["Daylight saving"],
        [(519_899, 624_304, 4_650, 1_148_853)],
    ),
]
# The boundary determination each referendum's districts follow.
REFERENDUM_PLANS = {"2005-02-26": "2003", "2009-05-16": "2007"}


@dataclass
class Question:
    text: str
    places: list[Place]  # votes are yes and no
    declared: Place
    fixes: list[str]


DISTRICT_HEAD = re.compile(r"^\s*([A-Z][A-Za-z' -]+?)(?: District)?\s*$")
# Rows the referendum reports misprint, each proven by the district's printed totals: (day, district, place) to
# (printed yes, no, valid, informal, and total votes; corrected ones; note).
REFERENDUM_FIXES = {
    ("2009-05-16", "Albany", "Albany Primary School"): (
        (198, 435, 633, 13, 646),
        (372, 484, 856, 1, 857),
        (
            "The report prints Albany Primary School with 198 yes, 435 no, 13 informal, and 646 total votes, the "
            "figures of the same polling place at the 2005 referendum's first question. With them the district's polling "
            "places fall short of its printed total of polling place votes by 174 yes, 49 no, and 211 total votes and "
            "exceed it by 12 informal votes. The archive stores the polling place as the printed total less the other "
            "polling places: 372 yes, 484 no, 1 informal, and 857 total votes."
        ),
    ),
}


@functools.cache
def referendum(day: str) -> dict[tuple[int, str], Question]:
    """Each question's table by district, keyed by the question's position and the district's name."""
    spec = next(r for r in REFERENDUMS if r.day == day)
    lines = layout(spec.report).splitlines()
    plan = REFERENDUM_PLANS[day]
    known = {fold(d.name) for g in GENERALS if PLANS[g.day] == plan for d in checked(g.day)}
    heads = [i for i, line in enumerate(lines) if (m := DISTRICT_HEAD.match(line)) and fold(m[1]) in known]
    texts: list[str] = []
    out: dict[tuple[int, str], Question] = {}
    for n, head in enumerate(heads):
        found = DISTRICT_HEAD.match(lines[head])
        assert found
        name = found[1].strip()
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        block = lines[head:end]
        question = next((i for i, line in enumerate(block) if re.match(r"^\s*Question\b", line)), None)
        if question is None:
            continue
        words = []
        for line in block[question:]:
            if not line.strip() or "%" in line or "Yes" in line:
                break
            words.append(line.strip())
        text = re.sub(r"^Question( \d)?:\s*", "", " ".join(words))
        if text not in texts:
            texts.append(text)
        places: list[Place] = []
        declared = None
        fixes: list[str] = []
        for row in rows(block, 7):
            label = row.label
            cells = tuple(integer(c) for c in row.numbers[:5])
            fix = REFERENDUM_FIXES.get((day, name, label))
            if fix is not None:
                if cells != fix[0]:
                    raise ValueError(f"{day} {name}, {label}: the correction no longer fits the report")
                cells = fix[1]
                fixes.append(fix[2])
            yes, no, valid, informal, total = cells
            place = Place(label, [yes, no], valid, informal, total)
            if label == "Total Votes":
                declared = place
                break
            if label in ("Total Polling Places Votes", "Total Count Centre Votes", "Polling Place Totals"):
                continue
            if label in DECLARATION or label.startswith("Special Institutions"):
                place.kind = "aggregate"
            places.append(place)
        if declared is None:
            raise ValueError(f"{day} {name}: no total row")
        check(f"{day} {name}", places, declared, 2, {})
        key_ = (texts.index(text), name)
        if key_ in out:
            raise ValueError(f"{day} {name}: question {key_[0] + 1} printed twice")
        out[key_] = Question(text, places, declared, fixes)
    if len(texts) != len(spec.questions):
        raise ValueError(f"{day}: {len(texts)} questions for {len(spec.questions)}")
    for index, (yes, no, informal, total) in enumerate(spec.state):
        found = [q.declared for (i, _), q in out.items() if i == index]
        sums = (
            sum(d.votes[0] for d in found),
            sum(d.votes[1] for d in found),
            sum(d.informal for d in found),
            sum(d.total for d in found),
        )
        if sums != (yes, no, informal, total):
            raise ValueError(
                f"{day} question {index + 1}: districts add up to {sums}, not {(yes, no, informal, total)}"
            )
    return out


REFERENDUM_METHOD = (
    "Yes and no votes at each polling place, from the district results of the Western Australian Electoral "
    "Commission's report on the referendum. Absent, early (postal and in person), provisional, and special "
    "institution, hospital, and remote votes are reported once per district and stored as one unit each. The "
    "districts add up to the report's statewide totals."
)


def referendum_package(day: str) -> Package:
    spec = next(r for r in REFERENDUMS if r.day == day)
    when = date.fromisoformat(day)
    found = referendum(day)
    p = Package(
        f"{SOURCE}/{day}-referendum",
        SOURCE,
        f"The {spec.title} by polling place, from the Western Australian Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(AUSTRALIA)
    p.uses(raw.cached(SOURCE, spec.report))
    election_id = key("election", day, "referendum")
    path = f"au/wa/{day}-referendum"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="referendum",
        date_from=when,
        date_to=when,
        date_precision="day",
        status="held",
    )
    p.name(election_id, spec.title[0].upper() + spec.title[1:], "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    units: dict[str, str] = {}
    for index, short in enumerate(spec.questions):
        contest = key("contest", day, "referendum", index)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="ballot_question",
            magnitude=1,
            round_number=1,
            electoral_system_id=REFERENDUM_SYSTEM,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        texts = {q.text for (i, _), q in found.items() if i == index}
        p.name(contest, short, "en", "official")
        p.add("note", id=key("note", contest), entity_id=contest, kind="context", body_md=texts.pop(), lang="en")
        p.add("slug", path=f"{path}/question-{index + 1}", entity_id=contest, is_current=True)
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
            body_md=REFERENDUM_METHOD,
            lang="en",
        )
        options = [key("option", contest, answer) for answer in ("yes", "no")]
        for position, (option, label) in enumerate(zip(options, ("Yes", "No"), strict=True), 1):
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        yes, no, informal, total = spec.state[index]
        for option, value in zip(options, (yes, no), strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
        for measure, value in ((VALID, yes + no), (INVALID, informal), (CAST, total)):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option, value in zip(options, (yes, no), strict=True):
            won = value == max(yes, no)
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if won else "lost")
        for (i, name), q in sorted(found.items()):
            if i != index:
                continue
            for n, fix in enumerate(q.fixes):
                p.add(
                    "note",
                    id=key("note", result_set, "fix", name, n),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=fix,
                    lang="en",
                )
            slug = changeset.slug(name)
            district = area("au-wa.district", f"{REFERENDUM_PLANS[day]}:{slug}")
            seen: Counter[str] = Counter()
            for place in q.places:
                seen[place.name] += 1
                code = f"{day}:referendum:{slug}:{changeset.slug(place.name)}"
                if seen[place.name] > 1:
                    code += f"-{seen[place.name]}"
                if code not in units:
                    unit = units[code] = key("unit", code)
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
                    label = place.name if place.kind == "standard" else f"{name}: {place.name}"
                    p.name(unit, label, "en", "official")
                unit = units[code]
                for option, value in zip(options, place.votes, strict=True):
                    put(p, result_set, unit, option, VOTES, value)
                put(p, result_set, unit, None, VALID, place.formal)
                put(p, result_set, unit, None, INVALID, place.informal)
                put(p, result_set, unit, None, CAST, place.total)
    return p


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
    lists += [state_council(day).candidates for day in STATE_COUNCIL]
    lists += [r.candidates for day in GROUPED for r in group_council(day)]
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
    for day in GROUPED:
        for g in group_council(day):
            id = region_area(day, g.name)
            if id in regions:
                continue
            regions.add(id)
            p.add("area", id=id, scheme_code="au-wa.region")
            p.add("area_code", area_id=id, code=f"{PLANS[day]}:{changeset.slug(g.name)}")
            p.add("area_parent", area_id=id, parent_id=WA)
            p.name(id, f"{g.name} Region", "en", "official")
    for (plan, slug), name in sorted(found.items()):
        id = area("au-wa.district", f"{plan}:{slug}")
        p.add("area", id=id, scheme_code="au-wa.district")
        p.add("area_code", area_id=id, code=f"{plan}:{slug}")
        p.add("area_parent", area_id=id, parent_id=WA)
        p.name(id, name, "en", "official")
    shapes(p, found)
    return p


def shapes(p: Package, found: dict[tuple[str, str], str]) -> None:
    """The districts' and regions' shapes, from `waec_shapes`, valid from each plan's first general election to the
    next plan's."""
    firsts = sorted({plan: min(day for day, v in PLANS.items() if v == plan) for plan in PLANS.values()}.items())
    spans = {
        plan: (date.fromisoformat(day), date.fromisoformat(firsts[i + 1][1]) if i + 1 < len(firsts) else None)
        for i, (plan, day) in enumerate(firsts)
    }
    districts: dict[str, dict[str, str]] = {}
    for plan, slug in found:
        districts.setdefault(plan, {})[slug] = area("au-wa.district", f"{plan}:{slug}")
    regions: dict[str, dict[str, str]] = {}
    for g in GENERALS:
        for d in checked(g.day):
            if d.region is not None:
                regions.setdefault(PLANS[g.day], {})[changeset.slug(d.name)] = waec_shapes.region_slug(d.region)
    waec_shapes.add(p, key, districts, regions, spans)


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
    p.add(
        "electoral_system", id=REFERENDUM_SYSTEM, polity_id=AUSTRALIA, rules={"formula": "referendum"}, rules_version=1
    )
    p.add(
        "note",
        id=key("note", REFERENDUM_SYSTEM),
        entity_id=REFERENDUM_SYSTEM,
        kind="methodology",
        body_md="A question put to the state's electors, decided by a majority of the valid votes.",
        lang="en",
    )
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
    p.add("electoral_system", id=STATE_SYSTEM, polity_id=AUSTRALIA, rules={"formula": "stv"}, rules_version=1)
    p.add(
        "note",
        id=key("note", STATE_SYSTEM),
        entity_id=STATE_SYSTEM,
        kind="methodology",
        body_md="From 2025 the state is one electorate that elects all 37 members of the Legislative Council by the "
        "single transferable vote. Candidates stand in groups, and a voter marks boxes above the line for groups "
        "or numbers candidates below it.",
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
        + (
            " and Legislative Council results"
            if any(c.day == day for c in COUNCILS) or day in STATE_COUNCIL or day in GROUPED
            else ""
        )
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
    units: dict[str, str] = {}  # each polling place unit's code to its id, which the Legislative Council shares
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
            unit = units[code] = key("unit", code)
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
        roll = units[f"{day}:{slug}:roll"] = key("unit", day, slug, "roll")
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
        council_contests(p, day, election_id, path, units)
    if day in GROUPED:
        group_contests(p, day, election_id, path, units)
    if day in STATE_COUNCIL:
        state_council_contest(p, day, election_id, path, units)
    return p


COUNCIL_METHOD = (
    "First preference votes at each polling place, from the first preference analysis in the region profiles of "
    "the Western Australian Electoral Commission's Results and Statistics report. Votes cast above the line for a "
    "group's voting ticket are stored on one option per group, and votes below the line on each candidate, so the "
    "options together hold every formal vote; the commission's count adds a group's ticket votes to its first "
    "candidate's first preferences. Absent, early (postal and in person), provisional, and special institution, "
    "hospital, and remote votes are reported once per district and stored as one unit each.{types} The rows add up "
    "to each district's printed totals, and the districts to the first preferences of the region's count. The "
    "enrolment is stored on a separate unit of each district. The later counts are not stored, and the members are "
    "listed as the commission declared them elected."
)
BY_TYPE = (
    " The {year} report prints the region's votes by district and type of vote, with no polling places, so each "
    "district's ordinary votes are stored as one unit."
)
NO_PLACES = (
    "The report leaves the polling place page of the {district} district blank, so the district's votes are stored "
    "by type of vote from the region's summary, with its ordinary votes as one unit."
)


def council_unit(p: Package, units: dict[str, str], day: str, district: str, row: Tally) -> str:
    """The unit of a row of a region's first preference analysis: the Assembly's unit for the same polling place or
    type of declaration vote in the district, or a unit of its own."""
    slug = changeset.slug(district)
    if row.kind == "standard":
        codes = [f"{day}:{slug}:{changeset.slug(row.name)}"]
    else:
        same = [
            place.name
            for d in checked(day)
            if d.name == district
            for place in d.places
            if place.kind == "aggregate" and vote_type(place.name) == row.name
        ]
        codes = [f"{day}:{slug}:{changeset.slug(name)}" for name in same]
    for code in codes:
        if code in units:
            return units[code]
    code = f"{day}:{slug}:{changeset.slug(row.name)}"
    unit = units[code] = key("unit", code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=AUSTRALIA,
        scheme_code="au-wa.polling_place",
        code=code,
        area_relation="within",
        area_id=district_area(day, district),
        kind=row.kind,
    )
    p.name(unit, row.name if row.kind == "standard" else f"{district}: {row.name}", "en", "official")
    return unit


def council_contests(p: Package, day: str, election_id: str, path: str, units: dict[str, str]) -> None:
    spec = next(c for c in COUNCILS if c.day == day)
    for link in dict.fromkeys(x for x in (spec.candidates, spec.counts, spec.elected) if x):
        p.uses(raw.cached(SOURCE, link))
    found = booths(day)
    enrolment = {d.name: d.enrolled for d in checked(day)}
    for r in council(day):
        b = found[r.name]
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
        places = any(t.kind == "standard" for rows in b.places.values() for t in rows)
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=COUNCIL_METHOD.format(types="" if places else BY_TYPE.format(year=day[:4])),
            lang="en",
        )
        for d, rows in sorted(b.places.items()):
            if places and not any(t.kind == "standard" for t in rows):
                p.add(
                    "note",
                    id=key("note", result_set, "by-type", d),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=NO_PLACES.format(district=d),
                    lang="en",
                )
        # The options in ballot order: each group's ticket above the line, then the candidates below it.
        legends: list[str] = []
        options: list[str] = []
        heads = {members[0] for members in b.groups}
        position = 0
        for index, c in enumerate(r.candidates):
            name = party_name(c.party)
            party = party_id(name) if name else None
            if index in heads:
                position += 1
                legend = key("option", contest, "group", index + 1)
                legends.append(legend)
                p.add(
                    "contest_option",
                    id=legend,
                    contest_id=contest,
                    kind="legend",
                    party_id=party,
                    ballot_position=position,
                    ballot_label=f"{name or c.display} group (above the line)",
                )
            position += 1
            option = key("option", contest, index + 1)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=c.ballot,
            )
            person = key("person", contest, index + 1)
            p.add("person", id=person)
            p.name(person, c.display, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", contest, index + 1),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.ballot,
                member_of_party_id=party,
                is_independent=party is None,
            )
            won = index in r.elected
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if won else "lost")
        rows: list[Tally] = []
        for d in sorted(b.places):
            for row in b.places[d]:
                rows.append(row)
                unit = council_unit(p, units, day, d, row)
                for option, value in zip(legends, row.tickets, strict=True):
                    put(p, result_set, unit, option, VOTES, value)
                for option, value in zip(options, row.prefs, strict=True):
                    put(p, result_set, unit, option, VOTES, value)
                put(p, result_set, unit, None, VALID, row.formal)
                put(p, result_set, unit, None, INVALID, row.informal)
                put(p, result_set, unit, None, CAST, row.total)
            enrolled = enrolment[d]
            assert enrolled is not None
            put(p, result_set, units[f"{day}:{changeset.slug(d)}:roll"], None, REGISTERED, enrolled)
        total = summed(r.name, rows)
        for option, value in [*zip(legends, total.tickets, strict=True), *zip(options, total.prefs, strict=True)]:
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
        for measure, value in (
            (VALID, r.formal),
            (INVALID, r.informal),
            (CAST, r.formal + r.informal),
            (REGISTERED, r.enrolled),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)


# Legislative Council by group, 2001 and 2005 ----------------------------------------------------------------------

# These reports print each region's first preference votes only for each group (its voting ticket and its first
# candidate together) and each ungrouped candidate, by district and type of vote, with the seats each group won.
# Their count sheets give the later counts, which are not stored.
GROUPED = {"2001-02-10": R2001, "2005-02-26": R2005}
# The formal votes of every region together, from the reports' comparative summaries.
GROUPED_FORMAL = {"2001-02-10": 1_048_752, "2005-02-26": 1_097_344}
# The columns of each region's table of group first preferences, left to right: a party's abbreviation, or the
# surname of an ungrouped candidate or of the head of a group of independents.
COLUMNS = {
    "2001-02-10": {
        "East Metropolitan": "ALP Kirwan Bateson DEM GRN PHO CDP LIB Torr Hoyer Tucak",
        "North Metropolitan": "GRN CDP PHO LIB Fredericks ALP DEM Whiteside Anderson Tabaczynski Berry",
        "South Metropolitan": "DEM ALP LIB Bennett GRN PHO Finney Hwang Zuidwind Shave",
        "Agricultural": "LIB ALP NP DEM GRN PHO Harvey Kennedy",
        "Mining and Pastoral": "PHO GRN ALP Nevill Helm DEM NP LIB Cooper Kennedy",
        "South West": "ALP LIB GRN DEM NP PHO Wood Gunson CDP Iturbide",
    },
    "2005-02-26": {
        "East Metropolitan": "ALP Truman GRN DEM FFP NCO PHS ONP L4F CDP LIB Button CEC Mnyirrinna Tucak",
        "North Metropolitan": "LIB CDP PHS Ulasowski FFP DEM ALP L4F GRN ONP Mummery Cloghan",
        "South Metropolitan": "DEM FHS LIB ALP FFP PHS CDP ONP GRN L4F Jamieson Milianku Zaknic Thorp Choi",
        "Agricultural": "ALP LIB CEC NCO FFP L4F PHS DEM ONP GRN CDP NAT Duncanson",
        "Mining and Pastoral": "PHS LIB ONP GRN L4F CDP DEM ALP Fischer CEC",
        "South West": "GRN PHS NCO FFP NAT ALP ONP DEM CDP LIB L4F Gunson",
    },
}
# The start of the party name the candidate list prints for each abbreviation.
ABBREVIATIONS = {
    "ALP": "Australian Labor Party",
    "LIB": "Liberal",
    "DEM": "DEMOCRATS",
    "GRN": "Greens",
    "PHO": "Pauline Hanson",
    "ONP": "ONE NATION",
    "CDP": "Christian Democratic",
    "NP": "NATIONAL PARTY",
    "NAT": "THE NATIONAL",
    "FFP": "FAMILY FIRST",
    "NCO": "New Country",
    "PHS": "Public Hospital",
    "FHS": "Fremantle Hospital",
    "L4F": "FOREST LIBERAL",
    "CEC": "CITIZENS ELECTORAL",
}
ENTRY_LINE = re.compile(
    r"^\s*(?:(?P<region>[A-Z][A-Za-z &]+?)\s{2,})?(?P<n>\d{1,2})\s+(?:\*\s+)?(?P<surname>[^,]+?),\s+(?P<rest>.*)$"
)
ROW = re.compile(r"^(?P<label>[A-Za-z][A-Za-z'()&. -]*?)\s+(?P<nums>\d[\d,]*(?:\s+\d[\d,]*)*)\s*$")


def region_name(text: str) -> str | None:
    return next((r for r in REGIONS if fold(r) == fold(text.replace("&", "and"))), None)


@dataclass
class GroupRegion:
    name: str
    candidates: list[Candidate]
    groups: list[list[int]]  # each group's candidates, as indexes into the list, in ballot order
    seats: list[int]  # the seats each group won
    elected: list[int]  # the elected candidates, as indexes into the list
    districts: dict[str, list[int]]  # each district's ordinary votes for each group
    types: dict[str, list[int]]  # the region's votes of each type of vote for each group
    informal: dict[str, int]  # the informal votes of each district and type of vote
    formal: int
    total_informal: int
    enrolled: int
    magnitude: int


def listed(text: str, start: int, end: int) -> dict[str, list[tuple[str, str, str]]]:
    """Each region's lines of a candidate table: surname, given names, and party."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    region = ""
    for line in text[start:end].splitlines():
        found = ENTRY_LINE.match(line)
        if not found:
            continue
        if found["region"]:
            region = region_name(found["region"]) or region_name(found["region"] + " Pastoral") or region
        parts = re.split(r"\s{2,}", found["rest"].strip())
        entries = out.setdefault(region, [])
        if int(found["n"]) != len(entries) + 1:
            raise ValueError(f"{region}: entry {found['n']} follows {len(entries)}")
        entries.append((found["surname"].strip(), parts[0], re.sub(r"\s+\d+$", "", " ".join(parts[1:])).strip()))
    return out


def head_of(label: str, cands: list[Candidate], after: int) -> int:
    """The first candidate of the group a column belongs to."""
    if label in ABBREVIATIONS:
        prefix = ABBREVIATIONS[label]
        for i in range(after + 1, len(cands)):
            if cands[i].party.startswith(prefix) and (i == 0 or cands[i - 1].party != cands[i].party):
                return i
        raise ValueError(f"no group of {label} after candidate {after + 1}")
    hits = [i for i, c in enumerate(cands) if norm(c.surname).endswith(norm(label)) and i > after]
    if not hits:
        raise ValueError(f"{label} heads no group")
    return hits[0]


@functools.cache
def group_council(day: str) -> list[GroupRegion]:
    text = layout(GROUPED[day])
    head = re.search(r"Region\s+Candidate \(Full Given Name", text)
    assert head is not None
    first = head.start()
    middle = text.index("Candidate (Ballot Paper Name)", first)
    lists = listed(text, first, middle)
    won_list = listed(text, middle, text.index("Types of Votes by District", middle))
    begin = text.index("Number of Group Quotas Achieved on First Preferences")
    lines = text[begin : text.index("The following section contains a region profile", begin)].splitlines()
    heads = {
        i: region_name(line.strip().removesuffix("Region").strip())
        for i, line in enumerate(lines)
        if line.strip().endswith(" Region") and len(line.split()) < 6
    }
    out = []
    for name in REGIONS:
        at = next(i for i, r in heads.items() if r == name)
        block = lines[at : min((i for i in heads if i > at), default=len(lines))]
        cands = [Candidate(s, g, p) for s, g, p in lists[name]]
        labels = COLUMNS[day][name].split()
        starts: list[int] = []
        for label in labels:
            starts.append(head_of(label, cands, starts[-1] if starts else -1))
        groups = [list(range(a, b)) for a, b in zip(starts, [*starts[1:], len(cands)], strict=True)]
        width = len(labels) + 3  # the groups, then valid, informal, and total votes
        rows: dict[str, list[int]] = {}
        for line in block:
            found = ROW.match(line)
            if not found or found["label"].startswith("No. "):
                continue
            nums = [integer(w) for w in found["nums"].split()]
            if len(nums) == width and found["label"] not in rows:
                rows[found["label"]] = nums
        total = rows.pop("Total")
        body = "\n".join(block)
        figure = re.search(r"Enrolment Figure:\s*([\d,]+)", body)
        assert figure is not None
        enrolled = integer(figure[1])
        quota = re.search(r"\((\d)\+1\)", body)
        assert quota is not None
        won = [int(float(w)) for line in block if line.strip().startswith("No. Elected") for w in line.split()[2:]]
        if len(won) != len(labels):
            raise ValueError(f"{day} {name}: {len(won)} seat figures for {len(labels)} groups")
        names = {fold(d.name) for d in checked(day) if d.region and region_name(d.region) == name}
        districts = {k: r for k, r in rows.items() if fold(k) in names}
        types = {k: r for k, r in rows.items() if k not in districts}
        if {fold(k) for k in districts} != names or len(types) != 4:
            raise ValueError(f"{day} {name}: rows {list(rows)} for districts {sorted(names)}")
        for k, r in rows.items():
            if sum(r[: len(labels)]) != r[-3] or r[-3] + r[-2] != r[-1]:
                raise ValueError(f"{day} {name} {k}: the figures do not add up")
        for column in range(width):
            summed = sum(r[column] for r in rows.values())
            if summed != total[column]:
                raise ValueError(f"{day} {name}: column {column} adds up to {summed:,}, not {total[column]:,}")
        members: list[int] = []
        for surname, given, party in won_list[name]:
            hits = [
                i
                for g, seats in zip(groups, won, strict=True)
                if seats
                for i in g
                if norm(cands[i].surname) == norm(surname) and cands[i].party.startswith(party[:12])
            ]
            if len(hits) > 1:
                hits = [i for i in hits if norm(cands[i].given)[:2] == norm(given)[:2]]
            if len(hits) != 1:
                raise ValueError(f"{day} {name}: elected {surname}, {given} matches {len(hits)} candidates")
            members.append(hits[0])
        for g, seats in zip(groups, won, strict=True):
            if sum(1 for i in members if i in g) != seats:
                raise ValueError(f"{day} {name}: a group's members are not its {seats} seats")
        magnitude = int(quota[1])
        if len(members) != magnitude or sum(won) != magnitude:
            raise ValueError(f"{day} {name}: {len(members)} members of {magnitude}")
        roll = sum(d.enrolled or 0 for d in checked(day) if d.region and region_name(d.region) == name)
        if roll != enrolled:
            raise ValueError(f"{day} {name}: enrolment {enrolled:,} is not its districts' {roll:,}")
        n = len(labels)
        out.append(
            GroupRegion(
                name,
                cands,
                groups,
                won,
                members,
                {k: r[:n] for k, r in districts.items()},
                {k: r[:n] for k, r in types.items()},
                {k: r[-2] for k, r in rows.items()},
                total[-3],
                total[-2],
                enrolled,
                magnitude,
            )
        )
    formal = sum(r.formal for r in out)
    if formal != GROUPED_FORMAL[day]:
        raise ValueError(f"{day}: the regions add up to {formal:,} formal votes, not {GROUPED_FORMAL[day]:,}")
    return out


GROUP_METHOD = (
    "First preference votes for each group, from the table of group quotas in the region profiles of the Western "
    "Australian Electoral Commission's Results and Statistics report. The report prints no votes for individual "
    "candidates at these counts: each option holds a group's votes, which are the votes for its voting ticket and "
    "its first candidate's own first preferences, or the first preferences of an ungrouped candidate. The "
    "votes of each district's polling places are reported together and stored as one unit of ordinary votes. "
    "Absent, postal or early, and provisional votes are reported once for the region and stored as one unit each. "
    "The districts and the four other units add up to the region's printed total, and the regions to the "
    "state's formal votes in the report's comparative summary. The later counts are not stored. Each group's seats "
    "and the members elected are as the commission declared them."
)


def group_contests(p: Package, day: str, election_id: str, path: str, units: dict[str, str]) -> None:
    p.uses(raw.cached(SOURCE, GROUPED[day]))
    for r in group_council(day):
        slug = changeset.slug(r.name)
        contest = key("contest", day, "council", slug)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=COUNCIL,
            district_area_id=region_area(day, r.name),
            magnitude=r.magnitude,
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
            body_md=GROUP_METHOD,
            lang="en",
        )
        options: list[str] = []
        for number, (members, seats) in enumerate(zip(r.groups, r.seats, strict=True), 1):
            head = r.candidates[members[0]]
            name = party_name(head.party)
            party = party_id(name) if name else None
            if name:
                label = f"{name} group" if len(members) > 1 else f"{name}: {head.display}"
            else:
                label = head.display if len(members) == 1 else f"Independent group headed by {head.display}"
            option = key("option", contest, "group", number)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list" if name else "independents",
                party_id=party,
                ballot_position=number,
                ballot_label=label,
            )
            if seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
            for position, index in enumerate(members, 1):
                c = r.candidates[index]
                person = key("person", contest, index + 1)
                p.add("person", id=person)
                p.name(person, c.display, "en", "official")
                candidacy = key("candidacy", contest, index + 1)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    list_position=position,
                    ballot_name=c.ballot,
                    member_of_party_id=party,
                    is_independent=party is None,
                )
                if index in r.elected:
                    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
        rows: list[tuple[str, str, str, list[int], int]] = []
        for district, votes in r.districts.items():
            code = f"{day}:{changeset.slug(district)}:council"
            unit = units[code] = key("unit", code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=AUSTRALIA,
                scheme_code="au-wa.polling_place",
                code=code,
                area_relation="within",
                area_id=district_area(day, district),
                kind="aggregate",
            )
            p.name(unit, f"{district}: ordinary votes", "en", "official")
            rows.append((unit, district, district, votes, r.informal[district]))
        for kind, votes in r.types.items():
            code = f"{day}:{slug}:council-{changeset.slug(kind)}"
            unit = units[code] = key("unit", code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=AUSTRALIA,
                scheme_code="au-wa.polling_place",
                code=code,
                area_relation="within",
                area_id=region_area(day, r.name),
                kind="aggregate",
            )
            p.name(unit, f"{r.name} Region: {kind.lower()} votes", "en", "official")
            rows.append((unit, kind, kind, votes, r.informal[kind]))
        for unit, _, _, votes, informal in rows:
            formal = sum(votes)
            for option, value in zip(options, votes, strict=True):
                put(p, result_set, unit, option, VOTES, value)
            put(p, result_set, unit, None, VALID, formal)
            put(p, result_set, unit, None, INVALID, informal)
            put(p, result_set, unit, None, CAST, formal + informal)
        for district in r.districts:
            enrolled = next(d.enrolled for d in checked(day) if d.name == district)
            assert enrolled is not None
            put(p, result_set, units[f"{day}:{changeset.slug(district)}:roll"], None, REGISTERED, enrolled)
        for column, option in enumerate(options):
            total = sum(votes[column] for _, _, _, votes, _ in rows)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=total)
        for measure, value in (
            (VALID, r.formal),
            (INVALID, r.total_informal),
            (CAST, r.formal + r.total_informal),
            (REGISTERED, r.enrolled),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)


# Legislative Council, whole of state -----------------------------------------------------------------------------

# From 2025 the Legislative Council's members are elected from one electorate, the whole state. The report's section
# 9 prints the first preference votes of each group (above and below the line together) at each district's ordinary
# votes, and the absent, postal, and provisional votes once for the state.
STATE_COUNCIL = {"2025-03-08": R2025}
STATE_SYSTEM = key("electoral_system", "legislative-council", "stv-statewide")
GROUPS = [  # the column labels of the primary count, and the party the candidate list prints for the group
    ("SAP", "Sustainable Australia Party - Anti-corruption"),
    ("SFF", "SFFPWA"),
    ("LIB", "Liberal Party"),
    ("NATS", "The Nationals WA"),
    ("AC", "Australian Christians"),
    ("GRN", "The Greens (WA)"),
    ("ALP", "WA Labor"),
    ("AJP", "Animal Justice Party"),
    ("Libertarian", "Libertarian"),
    ("SPPk", "Stop Pedophiles! Protect kiddies!"),
    ("LCWA", "Legalise Cannabis Party WA"),
    ("PHON", "One Nation"),
    ("IND - MOERMOND", "Independent"),
    ("IND - SUDHIR", "Independent"),
]
CANDIDATE_ENTRY = re.compile(r"^\s*(\d{1,3})\s{2,}([^,]+?),\s*(\S.*?)(?:\s{2,}(\S.*?))?\s*$")
COUNT_ROW = re.compile(r"^\s+([A-Za-z][A-Za-z' -]*?)\s+(\d[\d,]*(?:\s+\d[\d,]*){16})\s*$")
TYPES_ROW = re.compile(
    r"^(\S.*?)?\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+[\d.]+%\s*$"
)


@dataclass
class Group:
    label: str
    party: str
    members: list[int]  # indexes into the candidate list, in ballot order
    votes: int = 0
    seats: int = 0


@dataclass
class StateCouncil:
    candidates: list[Candidate]
    groups: list[Group]
    districts: dict[str, list[int]]  # each district's ordinary votes for each group
    district_informal: dict[str, int]
    types: dict[str, list[int]]  # the state's absent, postal, and provisional votes for each group
    type_informal: dict[str, int]
    totals: list[int]
    formal: int
    informal: int
    enrolled: int
    elected: list[int]
    above: int
    below: int


def section(text: str, start: str, end: str) -> list[str]:
    begin = text.index(start)
    return text[begin : text.index(end, begin)].splitlines()


def council_candidates(text: str) -> list[Candidate]:
    lines = section(text, "2. Legislative Council candidates\nThe following", "The following is a summary")
    out: list[Candidate] = []
    for line in lines:
        found = CANDIDATE_ENTRY.match(line)
        if not found or "Candidate (" in line:
            continue
        if int(found[1]) != len(out) + 1:
            raise ValueError(f"Legislative Council candidate {found[1]} follows {len(out)}")
        given = "" if found[3] == "." else found[3]
        out.append(Candidate(found[2].strip(), given, (found[4] or "").strip()))
    return out


def council_groups(cands: list[Candidate], labels: list[str]) -> list[Group]:
    """The groups in ballot order: each run of one party's candidates, with the independents split where the
    primary count's columns name the head of an independent group."""
    heads = [i for i, c in enumerate(cands) if i == 0 or party_name(c.party) != party_name(cands[i - 1].party)]
    for label in labels:
        if label.startswith("IND - "):
            hit = [i for i, c in enumerate(cands) if norm(c.surname) == norm(label.removeprefix("IND - "))]
            if len(hit) != 1:
                raise ValueError(f"{label} heads {len(hit)} candidates")
            heads.append(hit[0])
    heads = sorted(set(heads))
    out = []
    for n, head in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(cands)
        out.append(Group("", cands[head].party, list(range(head, end))))

    return out


def number_row(words: str) -> list[int]:
    return [integer(w) for w in words.split()]


def types_table(text: str) -> dict[str, tuple[int, int]]:
    """Each district's ordinary and valid votes and its electors from the table of types of votes by district."""
    lines = section(text, "4. L\n", "5. L\n")
    out: dict[str, tuple[int, int]] = {}
    for index, line in enumerate(lines):
        found = TYPES_ROW.match(line)
        if not found:
            continue
        name = (found[1] or "").strip()
        nums = [integer(found[k]) for k in range(2, 10)]
        if not name or name.startswith(("Total", "Percentage")):
            if found[1] is None:
                before = next(x.strip() for x in reversed(lines[:index]) if x.strip())
                after = next((x.strip() for x in lines[index + 1 :] if x.strip()), "")
                name = f"{before}{after}" if before.endswith("-") else f"{before} {after}"
            else:
                continue
        elif found[1] is not None and found[1].strip().startswith("Total"):
            continue
        out[fold(name)] = (nums[1], nums[0])
    return out


def state_council(day: str) -> StateCouncil:
    link = STATE_COUNCIL[day]
    text = layout(link)
    cands = council_candidates(text)
    # The primary count, from the heading to the percentages beneath the totals.
    lines = section(text, "9. Legislative Council Primary Count\n", "% of Total")
    printed = re.search(r"Enrolment Figure: ([\d,]+)", "\n".join(lines))
    if printed is None:
        raise ValueError("the Legislative Council primary count prints no enrolment")
    enrolled = integer(printed[1])
    header = " ".join(x.strip() for x in lines[:12] if "SAP" in x or "Liber" in x or "IND" in x or "MOER" in x)
    rows: dict[str, list[int]] = {}
    for line in lines:
        found = COUNT_ROW.match(line)
        if found:
            name = found[1].strip()
            if name in rows:
                raise ValueError(f"{name} is printed twice in the Legislative Council primary count")
            rows[name] = number_row(found[2])
    labels = [label for label, _ in GROUPS]
    if "SAP" not in header or "MOND" not in " ".join(lines[:12]):
        raise ValueError("the primary count's columns are not those expected")
    groups = council_groups(cands, labels)
    if [g.party for g in groups] != [party for _, party in GROUPS]:
        raise ValueError(f"the candidate list's groups {[g.party for g in groups]} do not match the count's columns")
    for g, (label, _) in zip(groups, GROUPS, strict=True):
        g.label = label
    total = rows.pop("Total")
    kinds = {k: rows.pop(k) for k in ("Absent Votes", "Postal Votes", "Provisional Votes")}
    for name, nums in [*rows.items(), *kinds.items(), ("Total", total)]:
        if sum(nums[:14]) != nums[14] or nums[14] + nums[15] != nums[16]:
            raise ValueError(f"{name}: the Legislative Council primary count's row does not add up")
    columns = [sum(nums[k] for nums in [*rows.values(), *kinds.values()]) for k in range(17)]
    if columns != total:
        raise ValueError(f"the Legislative Council primary count's rows add up to {columns}, not its total {total}")
    # The districts against the table of types of votes, and the totals against the comparative summary.
    types = types_table(text)
    if set(types) != {fold(name) for name in rows}:
        raise ValueError(f"the types of votes by district name {sorted(set(types) ^ {fold(n) for n in rows})}")
    for name, nums in rows.items():
        if types[fold(name)][0] != nums[14]:
            raise ValueError(
                f"{name}: {nums[14]:,} ordinary votes, but the table of types of votes has {types[fold(name)][0]:,}"
            )
    if sum(electors for _, electors in types.values()) != enrolled:
        raise ValueError("the districts' electors do not add up to the printed enrolment")
    summary = section(text, "Legislative Council - Types of Votes - Comparative Summary", "Types of Votes and Turnout")
    formal = integer(next(x.split()[-1] for x in summary if x.startswith("Total Formal Votes")))
    informal = integer(next(x.split()[-1] for x in summary if x.startswith("Informal Votes")))
    if (formal, informal) != (total[14], total[15]) or total[16] != integer(
        next(x.split()[-1] for x in summary if x.startswith("Total Votes"))
    ):
        raise ValueError("the primary count's totals differ from the comparative summary")
    split = re.search(r"Whole of State\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", text[text.index("5. L\n") :])
    if split is None or integer(split[1]) + integer(split[2]) != formal:
        raise ValueError("the above and below the line votes do not add up to the formal votes")
    # The elected members.
    elected = council_elected(text, cands)
    for k, g in enumerate(groups):
        g.votes = total[k]
        g.seats = sum(1 for i in elected if i in g.members)
    return StateCouncil(
        cands,
        groups,
        {n: v[:14] for n, v in rows.items()},
        {n: v[15] for n, v in rows.items()},
        {n.removesuffix(" Votes"): v[:14] for n, v in kinds.items()},
        {n.removesuffix(" Votes"): v[15] for n, v in kinds.items()},
        total[:14],
        formal,
        informal,
        enrolled,
        elected,
        integer(split[1]),
        integer(split[2]),
    )


def council_elected(text: str, cands: list[Candidate]) -> list[int]:
    """The candidates the report lists as elected, checked against its table of members by party."""
    lines = section(
        text,
        "The following table shows the candidates elected to the Legislative Council",
        "Western Australian Electoral Commission 2025",
    )
    elected: list[int] = []
    for line in lines[2:]:
        found = re.match(r"^(\S.*?)\*?\s{2,}(\S.*?)\s*$", line)
        if not found or found[1].startswith("Member"):
            continue
        printed = found[1].rstrip("*").strip()
        surname, _, given = printed.partition(",")
        party = party_name(found[2])
        hits = [i for i, c in enumerate(cands) if norm(c.surname) == norm(surname) and party_name(c.party) == party]
        if len(hits) > 1:
            hits = [i for i in hits if norm(given.strip())[:3] in {norm(n)[:3] for n in cands[i].given.split()}]
        if len(hits) != 1:
            raise ValueError(f"elected {printed} matches {len(hits)} candidates")
        elected.append(hits[0])
    summary = section(text, "Elected Members\n", "Total ")
    seats = {
        party_name(m[1]): int(m[4]) for x in summary if (m := re.match(r"^(\S.*?)\s+(\d+)\s+(\d+)\s+(\d+)\s*$", x))
    }
    held = Counter(party_name(cands[i].party) for i in elected)
    if len(elected) != 37 or len(set(elected)) != 37 or dict(held) != seats:
        raise ValueError(f"{len(elected)} elected members, {dict(held)}, against the report's {seats}")
    return elected


STATE_METHOD = (
    "First preference votes for each group of candidates, above and below the line together, from section 9 of the "
    "Western Australian Electoral Commission's Results and Statistics report. The report prints each district's "
    "ordinary votes, which are stored on one unit per district, and the absent, postal, and provisional votes once "
    "for the whole state, which are stored on one unit each. It prints no votes for individual candidates, so the "
    "options are the groups; a group's seats are its candidates the commission declared elected. The rows add up "
    "to the state's formal and informal votes in the report's comparative summary. The later counts are not stored."
)


def state_council_contest(p: Package, day: str, election_id: str, path: str, units: dict[str, str]) -> None:
    link = STATE_COUNCIL[day]
    p.uses(raw.cached(SOURCE, link))
    state = state_council(day)
    contest = key("contest", day, "council", "whole-of-state")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="body_seats",
        body_id=COUNCIL,
        district_area_id=WA,
        magnitude=len(state.elected),
        round_number=1,
        electoral_system_id=STATE_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, "Whole of State, Legislative Council", "en", "official")
    p.add("slug", path=f"{path}/council", entity_id=contest, is_current=True)
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
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=STATE_METHOD, lang="en")
    p.add(
        "note",
        id=key("note", result_set, "atl-btl"),
        entity_id=result_set,
        kind="context",
        body_md=f"Of the {state.formal:,} formal votes, {state.above:,} were cast above the line and {state.below:,} "
        "below the line.",
        lang="en",
    )
    options: list[str] = []
    for number, group in enumerate(state.groups, 1):
        name = party_name(group.party)
        head = state.candidates[group.members[0]]
        label = name or f"Independent group of {head.display}"
        option = key("option", contest, "group", number)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party_id(name) if name else None,
            ballot_position=number,
            ballot_label=label,
        )
        if group.seats:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=group.seats)
        for position, index in enumerate(group.members, 1):
            c = state.candidates[index]
            person = key("person", contest, index + 1)
            p.add("person", id=person)
            p.name(person, c.display, "en", "official")
            candidacy = key("candidacy", contest, index + 1)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=position,
                ballot_name=c.ballot,
                member_of_party_id=party_id(name) if name else None,
                is_independent=name is None,
            )
            if index in state.elected:
                p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")

    def unit(code: str, label: str, area: str, kind: str) -> str:
        id = key("unit", code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=AUSTRALIA,
            scheme_code="au-wa.polling_place",
            code=code,
            area_relation="within",
            area_id=area,
            kind=kind,
        )
        p.name(id, label, "en", "official")
        return id

    def row(id: str, votes: list[int], informal: int) -> None:
        for option, value in zip(options, votes, strict=True):
            put(p, result_set, id, option, VOTES, value)
        put(p, result_set, id, None, VALID, sum(votes))
        put(p, result_set, id, None, INVALID, informal)
        put(p, result_set, id, None, CAST, sum(votes) + informal)

    for name, votes in sorted(state.districts.items()):
        slug = changeset.slug(name)
        district = next(d for d in checked(day) if fold(d.name) == fold(name))
        row(
            unit(
                f"{day}:council:{slug}",
                f"{district.name}: ordinary votes",
                district_area(day, district.name),
                "aggregate",
            ),
            votes,
            state.district_informal[name],
        )
        assert district.enrolled is not None
        put(p, result_set, units[f"{day}:{slug}:roll"], None, REGISTERED, district.enrolled)
    for name, votes in state.types.items():
        label = f"{name} votes"
        row(unit(f"{day}:council:{changeset.slug(name)}", label, WA, "aggregate"), votes, state.type_informal[name])
    for option, value in zip(options, state.totals, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
    for measure, value in (
        (VALID, state.formal),
        (INVALID, state.informal),
        (CAST, state.formal + state.informal),
        (REGISTERED, state.enrolled),
    ):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)


def files() -> list[str]:
    links = [link for g in GENERALS for link in (g.districts, g.candidates, g.enrolment) if link]
    links += [link for c in COUNCILS for link in (c.candidates, c.counts, c.elected) if link]
    links += list(GROUPED.values())
    links += [r.report for r in REFERENDUMS]
    return list(dict.fromkeys(links))


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{g.day}" for g in GENERALS),
        *(f"{SOURCE}/{r.day}-referendum" for r in REFERENDUMS),
    ]


def fetch() -> None:
    for link in files():
        raw.fetch(SOURCE, link, suffix=".pdf")
    waec_shapes.fetch()


def parse() -> None:
    geo().write()
    registry().write()
    for g in GENERALS:
        election(g.day).write()
    for r in REFERENDUMS:
        referendum_package(r.day).write()


def emit() -> None:
    package.run("load", packages())
