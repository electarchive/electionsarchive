"""Seanad Éireann: the National University of Ireland and University of Dublin constituencies, count by count.

The results handbooks print each university count as a table of candidates in alphabetical order, with each
candidate's votes after every count. The tables come in two layouts. The 2016 handbook repeats the candidates' names
on every page of counts, and prints a candidate's transfer above the row. The 2025 handbook prints the names on the
first page only and leaves the later pages as rows of numbers in the same order. In both, a count's total is the
sum of the candidates' rows and the papers that are not effective.

A university count is in whole papers (the Act's rules weigh a paper at one vote), so no value is fractional.
Where the handbook shows a candidate's votes after exclusion as a negative figure (the papers handed on) and a dash
afterward, the candidate holds nothing from then on. The 2025 handbook's University of Dublin table instead repeats
an excluded candidate's votes in every later column, so that its totals grow by the votes handed on; the parser
keeps the figures as printed and records which candidates the table excludes.
"""

import re
import unicodedata
from dataclasses import dataclass, field

ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
}
CELL = r"[+-]?\d[\d,]*|–|(?<!\S)-(?!\S)"
CELL_RE = re.compile(CELL)
CELLS = re.compile(rf"^\s*(?:{CELL})(?:\s+(?:{CELL}))*\s*$")
NAMED = re.compile(rf"^\s*(?P<name>[*A-Za-zÀ-ÿ][^\d]*?)\s{{2,}}(?P<cells>(?:{CELL})(?:\s+(?:{CELL}))*)\s*$")
COMMENT = re.compile(r"\s+\d+(?:st|nd|rd|th)\s+Count\s*$")
PARTICLES = {"de", "van", "von", "ó", "ní", "mac", "mc", "o"}


@dataclass
class Candidate:
    surname: str
    forename: str
    values: list[int] = field(default_factory=list)
    excluded: int | None = None  # the count in which the candidate's votes were handed on

    @property
    def name(self) -> str:
        if self.forename.endswith(("O'", "O’")):
            return f"{self.forename}{self.surname}"
        return f"{self.forename} {self.surname}"


@dataclass
class Constituency:
    valid: int
    quota: int
    seats: int
    candidates: list[Candidate]
    non_effective: list[int]
    totals: list[int]
    elected: list[int]
    counts: int
    retained: bool  # an excluded candidate's votes are repeated in the later columns
    electorate: int = 0
    poll: int = 0
    rejected_at_opening: int | None = None
    spoiled: int = 0


def number(cell: str) -> int:
    return 0 if cell in {"–", "-"} else int(cell.replace(",", ""))


def cells(text: str) -> list[str]:
    return CELL_RE.findall(text)


def fold(surname: str, forename: str = "") -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", f"{surname}{forename}") if ch.isalpha()).casefold()


def proper(surname: str) -> str:
    """A surname the handbook prints in capitals, in the case the name is written in."""
    words = []
    for word in surname.replace("’", "'").split(" "):
        if re.match(r"^Ma?c[A-Z]", word):  # McDOWELL, MacNEILL
            prefix = "Mac" if word.startswith("Mac") else "Mc"
            words.append(prefix + word[len(prefix) :].capitalize())
        elif word.isupper() or word.lower() == word:
            words.append(
                "-".join("'".join(part.capitalize() for part in piece.split("'")) for piece in word.split("-"))
            )
        else:
            words.append(word)
    return " ".join(words)


def split_name(printed: str) -> tuple[str, str]:
    surname, _, forename = printed.lstrip("*").partition(",")
    return proper(surname.strip()), forename.strip().replace("’", "'")


def ordinals(line: str) -> bool:
    words = [word.lower() for word in line.split()]
    return bool(words) and all(word in ORDINALS or word.startswith("twenty-") for word in words)


def possessive(text: str) -> str:
    return re.sub(r"[’']s?$", "", text.strip())


@dataclass
class Row:
    name: str | None
    cells: list[str]


def is_delta(tokens: list[str]) -> bool:
    if any(token.startswith("+") for token in tokens):
        return True
    return all(token[0] in "+-" and len(token) > 1 for token in tokens)


def rows_of_block(lines: list[str], known: int | None) -> tuple[list[Row], int, list[str]]:
    """The rows of one page of counts, from its first line, and the index of the line after its last row.

    A page ends at its TOTAL row when the rows carry names, and after `known` rows of numbers when they do not."""
    named: list[Row] = []
    bare: list[Row] = []
    header: list[str] = []
    for index, raw_line in enumerate(lines):
        line = COMMENT.sub("", raw_line.rstrip())
        stripped = line.strip()
        if not stripped:
            continue
        match = NAMED.match(line)
        label = match["name"].strip() if match else ""
        if match and ("," in label or re.match(r"(?i)^(non|effective|total)", label)):
            found = cells(match["cells"])
            if any(token.startswith("+") for token in found):
                continue  # the transfers printed beside a label
            named.append(Row(label, found))
            if label.lower().startswith("total"):
                return named, index + 1, header
            continue
        if CELLS.match(line) and named == [] and known is not None:
            tokens = cells(line)
            if not is_delta(tokens):
                bare.append(Row(None, tokens))
                if len(bare) == known:
                    return bare, index + 1, header
            continue
        if not named and not bare and not CELLS.match(line):
            header.append(line)
    raise ValueError("a page of counts has no total")


def blocks(lines: list[str]) -> list[tuple[list[Row], list[str]]]:
    out: list[tuple[list[Row], list[str]]] = []
    known: int | None = None
    cursor = 0
    while True:
        start = next(
            (
                i
                for i in range(cursor, len(lines))
                if ordinals(lines[i]) or lines[i].strip().startswith("Names of candidates")
            ),
            None,
        )
        if start is None:
            break
        rows, used, header = rows_of_block(lines[start:], known)
        out.append((rows, header))
        if known is None:
            known = len(rows)
        cursor = start + used
    return out


def header_transfers(header: list[str], width: int) -> list[tuple[str, str]]:
    """The (kind, name) of each count in a page: `votes` or `surplus`, and the possessive name the header gives."""
    kinds = next(
        (
            [t for t in re.split(r"\s{2,}", line.strip()) if t.startswith(("Votes", "Surplus"))]
            for line in header
            if "Votes and" in line or "Surplus and" in line
        ),
        [],
    )
    if not kinds:
        return []
    position = next(i for i, line in enumerate(header) if "Votes and" in line or "Surplus and" in line)
    names = [t for t in re.split(r"\s{2,}", header[position - 1].strip()) if t]
    if len(names) != len(kinds):
        return []
    found = [
        ("surplus" if kind.startswith("Surplus") else "votes", possessive(name))
        for kind, name in zip(kinds, names, strict=True)
    ]
    return [("none", "")] * (width - len(found)) + found


def parse_table(lines: list[str], seats: int, members: set[str]) -> Constituency:
    pages = blocks(lines)
    if not pages:
        raise ValueError("no counts")
    first = pages[0][0]
    names = [row.name for row in first if row.name]
    if len(names) != len(first):
        raise ValueError("the first page of counts has no names")
    candidates = [Candidate(*split_name(row.name or "")) for row in first if "," in (row.name or "")]
    labels = [row.name or "" for row in first]
    expected = len(candidates) + 2
    if len(first) != expected or not labels[-1].lower().startswith("total"):
        raise ValueError(f"{len(first)} rows on the first page for {len(candidates)} candidates")
    non_effective: list[int] = []
    totals: list[int] = []
    transfers: list[tuple[str, str]] = []
    for page, (rows, header) in enumerate(pages):
        if len(rows) != expected:
            raise ValueError(f"page {page + 1} has {len(rows)} rows, expected {expected}")
        if page and rows[0].name and rows[0].name != first[0].name:
            raise ValueError(f"page {page + 1} starts at {rows[0].name!r}")
        width = len(rows[-1].cells)
        for row, candidate in zip(rows[:-2], candidates, strict=True):
            if len(row.cells) != width:
                raise ValueError(f"{candidate.name}: {len(row.cells)} cells for {width} counts")
            for cell in row.cells:
                before = candidate.values[-1] if candidate.values else 0
                if cell.startswith("-") and len(cell) > 1:
                    if number(cell[1:]) != before:
                        raise ValueError(f"{candidate.name} hands on {cell}, holding {before}")
                    candidate.excluded = len(candidate.values) + 1
                    candidate.values.append(0)
                else:
                    candidate.values.append(number(cell))
        effective = [number(cell) for cell in rows[-2].cells]
        if len(effective) == width - 1 and page == 0:
            effective = [0, *effective]
        if len(effective) != width:
            raise ValueError(f"{len(effective)} papers not effective for {width} counts")
        non_effective += effective
        totals += [number(cell) for cell in rows[-1].cells]
        transfers += header_transfers(header, width)
    counts = len(totals)
    for candidate in candidates:
        if len(candidate.values) != counts:
            raise ValueError(f"{candidate.name} has {len(candidate.values)} of {counts} counts")
    for count in range(counts):
        held = sum(candidate.values[count] for candidate in candidates) + non_effective[count]
        if held != totals[count]:
            raise ValueError(f"count {count + 1} holds {held}, printed total {totals[count]}")
    retained = not any(candidate.excluded for candidate in candidates)
    if retained:
        # the table repeats excluded candidates' votes, so the headers say who was excluded
        if len(transfers) != counts:
            raise ValueError("the table's headers do not say who was excluded")
        for count, (kind, name) in enumerate(transfers, 1):
            if kind != "votes":
                continue
            wanted = fold(name)
            hits = [
                c
                for c in candidates
                if wanted in {fold(c.surname), fold(c.surname, c.forename), fold(c.forename, c.surname)}
                or fold(c.surname).startswith(wanted)
            ]
            hits = [c for c in hits if c.excluded is None]
            if len(hits) != 1:
                raise ValueError(f"count {count}: {name!r} names {len(hits)} candidates")
            hits[0].excluded = count
    elected = [
        i for i, c in enumerate(candidates) if {fold(c.surname, c.forename), fold(c.forename, c.surname)} & members
    ]
    if len(elected) != seats or len(members) != seats:
        raise ValueError(f"{len(elected)} of {len(members)} members are among the candidates, for {seats} seats")
    for i in elected:
        if candidates[i].values[-1] == 0:
            raise ValueError(f"{candidates[i].name} is a member who holds nothing at the last count")
    return Constituency(
        valid=totals[0],
        quota=0,
        seats=seats,
        candidates=candidates,
        non_effective=non_effective,
        totals=totals,
        elected=elected,
        counts=counts,
        retained=retained,
    )


UNIVERSITIES = {
    "nui": ("National University of Ireland", "Ollscoil na hÉireann", "NATIONAL UNIVERSITY OF IRELAND"),
    "dublin": ("University of Dublin", "Ollscoil Bhaile Átha Cliath", "UNIVERSITY OF DUBLIN"),
}
HEADINGS = {heading for _, _, heading in UNIVERSITIES.values()}
LABELS = {
    "Electorate": "electorate",
    "Rejected at opening": "rejected",
    "Total Poll": "poll",
    "Spoiled Votes": "spoiled",
    "Valid Poll": "valid",
    "Quota": "quota",
}


def figures(lines: list[str], heading: str) -> dict[str, int]:
    """The statistical summary of one constituency: electorate, poll, spoiled, valid, and quota."""
    for i, line in enumerate(lines):
        if line.strip() != heading:
            continue
        following = [lines[j].strip() for j in range(i + 1, min(i + 4, len(lines))) if lines[j].strip()]
        if not following or not following[0].startswith("Electorate"):
            continue
        out: dict[str, int] = {}
        for text in lines[i + 1 : i + 14]:
            if text.strip() in HEADINGS:
                break
            for label, name in LABELS.items():
                if text.strip().startswith(label) and (found := cells(text)):
                    out[name] = number(found[-1])
        return out
    raise ValueError(f"no figures for {heading}")


def section(lines: list[str], heading: str, others: list[str]) -> list[str]:
    """The lines of a constituency's results, from its first heading to the next section."""

    def opens(i: int, name: str) -> bool:
        following = next((lines[j].strip() for j in range(i + 1, len(lines)) if lines[j].strip()), "")
        return lines[i].strip().startswith(name) and following.startswith("Number of Valid Votes")

    start = next(i for i in range(len(lines)) if opens(i, heading))
    stop = len(lines)
    for i in range(start + 1, len(lines)):
        text = lines[i].strip()
        if text.startswith("Red print denotes"):
            later = [line.strip() for line in lines[i + 1 : i + 14] if line.strip()]
            if not any(line.startswith(heading) and line.endswith("continued") for line in later):
                stop = i + 1  # the last page of the constituency
                break
        if (any(opens(i, other) for other in others) and not text.endswith("continued")) or text.startswith(
            ("MEMBERS NOMINATED", "EXPLANATORY NOTES")
        ):
            stop = i
            break
    return lines[start:stop]


def elected_names(lines: list[str]) -> set[str]:
    """The members the handbook names as elected, folded in both orders."""
    start = next((i for i, line in enumerate(lines) if line.strip().startswith("Names of Candidates elected")), None)
    if start is None:
        return set()
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    found = " ".join(line.strip() for line in lines[start:end])
    parts = re.split(r",\s*|\s+and\s+", found.partition(":")[2].strip().rstrip("."))
    return {fold(part) for part in parts if part}


def listed_members(document: str) -> dict[str, set[str]]:
    """The university members of the new Seanad, from the handbook's two-column list of its members."""
    lines = document.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip().startswith("MEMBERS OF THE")), None)
    found: dict[str, set[str]] = {key: set() for key in UNIVERSITIES}
    if start is None:
        return found
    titles = {title: key for key, (title, _, _) in UNIVERSITIES.items()}
    current: str | None = None
    for line in lines[start + 1 : start + 80]:
        part = line[40:].strip()
        if part in titles:
            current = titles[part]
        elif part.endswith("Nominees"):
            current = None
        elif current and "," in part:
            surname, _, forename = part.partition(",")
            found[current].add(fold(surname, forename))
    return found


def parse(document: str) -> dict[str, Constituency]:
    """Each university constituency's count, tied to its printed totals and to the members it elected."""
    lines = document.splitlines()
    listed = listed_members(document)
    out: dict[str, Constituency] = {}
    for key, (_, _, heading) in UNIVERSITIES.items():
        body = section(lines, heading, [other for other in HEADINGS if other != heading])
        members = elected_names(body) or listed[key]
        stats = figures(lines, heading)
        seats = next(number(cells(line)[-1]) for line in body[:12] if line.strip().startswith("Number of members"))
        quota = next(
            number(cells(line)[-1])
            for line in body[:12]
            if ("Quota" in line or "the election of a candidate" in line) and cells(line)
        )
        table = parse_table(body, seats, members)
        if table.valid != stats["valid"] or quota != stats["quota"]:
            raise ValueError(f"{key}: the summary disagrees with the table")
        table.quota = quota
        table.electorate = stats["electorate"]
        table.poll = stats["poll"]
        table.spoiled = stats["spoiled"]
        table.rejected_at_opening = stats.get("rejected")
        out[key] = table
    return out
