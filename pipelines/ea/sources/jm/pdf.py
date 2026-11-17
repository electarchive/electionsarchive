"""Reads the Electoral Commission of Jamaica's polling-station result PDFs that carry a text layer."""

import html
import re
import statistics
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import median

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
NUMBER = re.compile(r"^\d[\d,]*$")
PERCENT = re.compile(r"^-?\d[\d,]*(\.\d+)?%?$")
LABELS = frozenset(("POLLING", "STATION", "LOCATION", "DIVISION", "PD", "PS"))
STATION = re.compile(r"^\d+[A-Z]?$")
MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        1,
    )
}
DIVISION = re.compile(
    r"^ELECTORAL DIVISION(?::\s*|\s+)(?!TOTALS)(.+?)\s*\((\d+)\)(?:\s+\d+\s+#DIV/0!)?\s*$", re.IGNORECASE
)


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Station:
    division: str
    pd: str
    ps: str
    emergency: bool
    location: str
    votes: list[int | None]
    rejected: int | None
    total: int | None
    electors: int
    page: int
    box: tuple[float, float, float, float]


@dataclass
class Row:
    """One printed line of a page: a station row or a totals line."""

    line: list[Word]
    totals: bool
    division: str = ""
    head: list[Word] = field(default_factory=list)
    emergency: bool = False

    @property
    def first(self) -> int:
        """Where the cells may start: a number right after the station is the start of its location, unless it has none."""
        bare = all(NUMBER.match(w.text) or PERCENT.match(w.text) for w in self.head[2:])
        return 2 if bare else 3


@dataclass
class Totals:
    stations: int
    votes: list[int]
    rejected: int
    total: int
    electors: int


@dataclass
class Constituency:
    number: int
    name: str
    candidates: list[tuple[str, str]] = field(default_factory=list)
    stations: list[Station] = field(default_factory=list)
    declared: Totals | None = None
    divisions: list[Totals] = field(default_factory=list)
    parent: tuple[int, str] | None = None
    blank: bool = False


@dataclass
class Document:
    title: str
    day: date
    constituencies: list[Constituency]


def number(text: str) -> int:
    return int(text.replace(",", ""))


def words_by_page(path: Path) -> list[list[Word]]:
    out = subprocess.run(
        ["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    pages: list[list[Word]] = []
    for chunk in out.split("<page ")[1:]:
        pages.append(
            [Word(*(float(v) for v in m.groups()[:4]), html.unescape(m.group(5))) for m in WORD.finditer(chunk)]
        )
    return pages


def lines(words: list[Word], tolerance: float = 5.0) -> list[list[Word]]:
    out: list[list[Word]] = []
    for word in sorted(words, key=lambda w: w.yc):
        if out and abs(word.yc - statistics.fmean(w.yc for w in out[-1])) <= tolerance:
            out[-1].append(word)
        else:
            out.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in out]


def parse_day(text: str) -> date:
    found = re.search(r"ELECTION DAY:\s*(\d{1,2})-([A-Za-z]{3})[a-z]*-(\d{4})", text)
    if found:
        month = next(n for name, n in MONTHS.items() if name.startswith(found.group(2).lower()))
        return date(int(found.group(3)), month, int(found.group(1)))
    found = re.search(r"ELECTION DAY:\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if not found:
        raise ValueError(f"no election day in {text!r}")
    return date(int(found.group(3)), MONTHS[found.group(1).lower()], int(found.group(2)))


def _edges(tokens: list[tuple[Word, Word]], rows: int) -> list[float]:
    """The right edges of the candidate columns: right-aligned numbers share one, and location text does not.

    A cluster of numbers that mostly follow a word of the location closely is the end of a location, such as
    "Phase 2", and not a column.
    """
    clusters: list[list[tuple[Word, Word]]] = []
    for pair in sorted(tokens, key=lambda t: t[0].x1):
        if clusters and pair[0].x1 - clusters[-1][-1][0].x1 <= 1.6:
            clusters[-1].append(pair)
        else:
            clusters.append([pair])
    need = 2 if rows >= 10 else 1
    return [
        statistics.median(w.x1 for w, _ in c)
        for c in clusters
        if len(c) >= need
        and sum(1 for w, before in c if not NUMBER.match(before.text) and w.x0 - before.x1 < 6) * 2 < len(c)
    ]


def _party_line(line: list[Word]) -> bool:
    return any(w.x1 - w.x0 > 7.9 for w in line) if line else False


def _header(
    page: list[Word], columns: list[tuple[float, float]], top: float, end: float
) -> tuple[list[tuple[str, str]], list[int]]:
    """The printed party and name of each candidate column, and the header column each data column belongs to.

    Newer reports print the header rotated, one column of words per candidate, directly above its numbers. Older ones
    print it flat and leave a candidate's cells blank when nothing was recorded for them, so the data columns are
    matched to the printed parties by position.
    """
    cut = min((w.x0 for w in page if w.text in ("BALLOTS", "REJECTED") and w.x0 > 400 and w.y1 < end), default=10_000)
    head = [
        w
        for w in page
        if w.y0 >= top
        and w.y1 <= end + 1
        and w.x1 <= cut + 2
        and w.xc >= columns[0][1] - 60
        and w.text not in LABELS
        and not re.fullmatch(r"\(\d+\)", w.text)
    ]
    rotated = sum(1 for w in head if w.y1 - w.y0 > 1.5 * (w.x1 - w.x0)) > len(head) / 2
    if rotated:
        groups: list[list[Word]] = [[] for _ in columns]
        edges = [e for _, e in columns]
        for w in head:
            groups[next((i for i, e in enumerate(edges) if w.xc <= e + 4), len(edges) - 1)].append(w)
        out: list[tuple[str, str]] = []
        # Rotated words are one font height wide, which the longer ones show. A horizontal party tag sits above the rotated names in a newer report,
        # and an older report rotates the tag too, as the first line of each column.
        font = statistics.median([w.x1 - w.x0 for w in head if w.y1 - w.y0 > 18] or [7.5])

        def vertical(w: Word) -> bool:
            return abs(w.x1 - w.x0 - font) <= 1.2

        flat_tags = any(not vertical(w) for w in head)
        for group in groups:
            tags = sorted((w for w in group if not vertical(w)), key=lambda w: w.x0) if flat_tags else []
            rest = [w for w in group if not any(w is t for t in tags)]
            ordered = lines_vertical(rest)
            party = " ".join(w.text for w in tags)
            if not flat_tags and ordered:
                party = " ".join(w.text for w in ordered[0])
                ordered = ordered[1:]
            out.append((party, " ".join(w.text for line in ordered for w in line)))
        return out, list(range(len(columns)))
    flat = lines(head, 3.0)
    if not flat:
        return [("", "") for _ in columns], list(range(len(columns)))
    parties = flat[0]
    centers = [w.xc for w in parties]
    named: list[list[Word]] = [[] for _ in parties]
    for line in flat[1:]:
        previous: Word | None = None
        owner = 0
        for w in line:
            if (
                previous is not None
                and previous.text.endswith(",")
                and w.x0 - previous.x1 < 8
                and not w.text.endswith(",")
            ):
                named[owner].append(w)  # a given name runs on from the word before it
            else:
                owner = min(range(len(centers)), key=lambda i: abs(centers[i] - w.xc))
                named[owner].append(w)
            previous = w
    mapping = [min(range(len(centers)), key=lambda i: abs(centers[i] - xc)) for xc, _ in columns]
    if len(set(mapping)) != len(mapping):
        raise ValueError("two data columns fall under one printed party")
    out = []
    for party, group in zip(parties, named, strict=True):
        text = " ".join(w.text for line in lines(group, 3.0) for w in line)
        out.append((party.text, text.replace("- ", "-")))
    return out, mapping


def lines_vertical(words: list[Word]) -> list[list[Word]]:
    """Rotated header text: each printed line is a column of words, read from the bottom up."""
    out: list[list[Word]] = []
    for word in sorted(words, key=lambda w: w.x0):
        if out and abs(word.x0 - out[-1][0].x0) <= 3.0:
            out[-1].append(word)
        else:
            out.append([word])
    return [sorted(line, key=lambda w: -w.y1) for line in out]


def read(path: Path, by: str = "constituency", name: str = "") -> Document:
    """The stations of a report, grouped by constituency, by electoral division, or as one municipality named `name`.

    A local government report prints a separate set of candidates for each electoral division, so `by="division"`
    keys the contests by division and keeps the constituency each one lies in. A mayoral report counts one contest for
    the whole municipality and breaks it down by division, so `by="municipality"` holds all its stations in one.
    """
    pages = words_by_page(path)
    constituencies: dict[tuple[int, int], Constituency] = {}
    current: Constituency | None = None
    title = ""
    day: date | None = None
    for page_number, page in enumerate(pages, 1):
        if not page:
            continue
        top = lines([w for w in page if w.y0 < 60])
        text = " ".join(" ".join(w.text for w in line) for line in top)
        if day is None:
            title = " ".join(w.text for w in top[0]) if top else ""
            day = parse_day(" ".join(w.text for w in page if w.y0 < 80))
        label = min(
            (w for w in page if w.text in ("CONSTITUENCY:", "CONSTITUENCY") and w.y0 < 120),
            key=lambda w: w.y0,
            default=None,
        )
        anchor = min((w for w in page if w.text == "LOCATION"), key=lambda w: w.y1, default=None)
        if label is None and by != "municipality":
            continue
        if anchor is None:
            if by == "municipality":
                continue
            raise ValueError(f"page {page_number}: no LOCATION header")
        end = anchor.y1
        body = lines([w for w in page if w.y0 > end - 1 and not (w.y1 - w.y0 > 20)])
        parent: tuple[int, str] | None = None
        if label is not None:
            row = [w for w in page if abs(w.yc - label.yc) < 4 and w.x0 > label.x1]
            row.sort(key=lambda w: w.x0)
            found = re.fullmatch(r"(.+?)\s*\((\d+)\)", " ".join(w.text for w in row))
            if not found and by != "municipality":
                raise ValueError(f"page {page_number}: constituency line {row!r} in {text!r}")
            if found:
                parent = (int(found.group(2)), found.group(1))
        if label is not None:
            top = label.y1 - 1
        else:
            top = max(
                (w.y1 for w in page if w.y0 < 110 and ("MUNICIPALITY" in w.text or w.text in ("ELECTION", "DAY:"))),
                default=0.0,
            )
        if by == "municipality":
            current = constituencies.setdefault((0, 1), Constituency(1, name))
        elif by == "division":
            printed = {
                (m.group(1), int(m.group(2)))
                for m in (DIVISION.match(" ".join(w.text for w in line)) for line in body)
                if m
            }
            if len(printed) != 1:
                raise ValueError(f"page {page_number}: divisions {sorted(printed)}")
            division_name, division_no = next(iter(printed))
            current = constituencies.setdefault(
                (parent[0] if parent else 0, division_no), Constituency(division_no, division_name)
            )
            current.parent = parent
        else:
            assert parent is not None
            current = constituencies.setdefault((parent[0], 0), Constituency(parent[0], parent[1]))
        division = ""
        items: list[Row] = []
        for line in body:
            joined = " ".join(w.text for w in line)
            if "Report Printed" in joined or re.search(r"\bPage \d+ of \d+", joined):
                continue
            if shown := DIVISION.match(joined):
                division = f"{shown.group(1)} ({shown.group(2)})"
                continue
            if re.search(r"\bTOTALS?\b", joined) and not re.match(r"^\d", joined):
                items.append(Row(line, True))
                continue
            head = [w for w in line if w.text != "EPM"]
            if head and re.fullmatch(r"\d+\.", head[0].text):
                head = head[1:]  # an older report numbers every row before its polling division
            if len(head) < 3 or not NUMBER.match(head[0].text) or not STATION.match(head[1].text):
                if re.match(r"^\d", joined) and len(line) > 5:
                    raise ValueError(f"page {page_number}: unreadable row {joined!r}")
                continue
            items.append(Row(line, False, division, head, any(w.text == "EPM" for w in line)))
        stations = [item for item in items if not item.totals]
        numbers = [
            (w, item.head[i - 1])
            for item in stations
            for i, w in enumerate(item.head[2:], 2)
            if i >= item.first and NUMBER.match(w.text)
        ]
        rows = len(stations)
        if rows and not numbers:
            current.blank = True  # the report lists the stations and prints no figure for any of them
            continue
        edges = _edges(numbers, rows)
        if rows and len(edges) < 4:
            raise ValueError(f"page {page_number}: only {len(edges)} columns")
        count = len(edges) - 3

        def aligned(words: list[Word], tolerance: float = 1.6) -> list[Word | None]:
            """The word under each column edge, matching each word to the edge nearest to it."""
            best: dict[int, tuple[float, Word]] = {}
            for w in words:
                index = min(range(len(edges)), key=lambda i: abs(w.x1 - edges[i]), default=None)
                if index is None:
                    continue
                gap = abs(w.x1 - edges[index])
                if gap <= tolerance and (index not in best or gap < best[index][0]):
                    best[index] = (gap, w)
            return [best[i][1] if i in best else None for i in range(len(edges))]

        def value(word: Word | None) -> int | None:
            return None if word is None else number(word.text)

        pending: list[Station] = []
        page_totals: list[Totals] = []
        shapes: list[list[Word | None]] = []
        for item in items:
            line = item.line
            if item.totals:
                joined = " ".join(w.text for w in line)
                cells = aligned([w for w in line if NUMBER.match(w.text)], 6.0)
                found_ps = re.search(r"(\d+) P[SD]s", joined)
                rejected_cell, total_cell, electors_cell = cells[-3:]
                if total_cell is None or electors_cell is None:
                    raise ValueError(f"page {page_number}: totals {joined!r}")
                totals = Totals(
                    int(found_ps.group(1)) if found_ps else 0,
                    [number(w.text) if w else 0 for w in cells[:-3]],
                    value(rejected_cell) or 0,
                    number(total_cell.text),
                    number(electors_cell.text),
                )
                page_totals.append(totals)
                if by == "municipality":
                    if "DIVISION" in joined:
                        current.divisions.append(totals)
                elif by == "division":
                    if "DIVISION" in joined:
                        current.declared = totals
                elif "CONSTITUENCY" in joined:
                    current.declared = totals
                else:
                    current.divisions.append(totals)
                continue
            station_division, head, emergency = item.division, item.head, item.emergency
            cells = aligned([w for w in head[item.first :] if NUMBER.match(w.text)])
            electors = value(cells[-1])
            if electors is None and re.search(r"\bNO ELECTORS?\b", " ".join(w.text for w in line)):
                electors = 0
            if electors is None:
                raise ValueError(f"page {page_number}: no electors in {' '.join(w.text for w in line)!r}")
            location = [
                w
                for w in head[2:]
                if not any(w is c for c in cells) and not (PERCENT.match(w.text) and re.search(r"[.%]", w.text))
            ]
            pending.append(
                Station(
                    station_division,
                    head[0].text,
                    head[1].text,
                    emergency,
                    " ".join(w.text for w in location),
                    [value(w) for w in cells[:-3]] if any(cells[:-1]) else [],
                    value(cells[-3]),
                    value(cells[-2]),
                    electors,
                    page_number,
                    (
                        min(w.x0 for w in line),
                        min(w.y0 for w in line),
                        max(w.x1 for w in line),
                        max(w.y1 for w in line),
                    ),
                )
            )
            shapes.append(cells[:-3])
        if pending:
            columns = [
                (
                    median(w.xc for shape in shapes if (w := shape[i]) is not None),
                    median(w.x1 for shape in shapes if (w := shape[i]) is not None),
                )
                for i in range(count)
            ]
            below = min(
                [s.box[1] for s in pending]
                + [
                    line[0].y0
                    for line in body
                    if " ".join(w.text for w in line).upper().startswith("ELECTORAL DIVISION")
                ]
            )
            header, mapping = _header(page, columns, top, below - 1)
            if mapping != list(range(len(header))):
                for station in pending:
                    if station.votes:
                        expanded: list[int | None] = [None] * len(header)
                        for i, cell in zip(mapping, station.votes, strict=True):
                            expanded[i] = cell
                        station.votes = expanded
                for totals in page_totals:
                    wide = [0] * len(header)
                    for i, total in zip(mapping, totals.votes, strict=True):
                        wide[i] = total
                    totals.votes = wide
            while header and header[-1] == ("", ""):
                if any(s.votes[len(header) - 1] for s in pending if s.votes):
                    raise ValueError(f"page {page_number}: votes in an unnamed column")
                header.pop()
                for station in pending:
                    if station.votes:
                        station.votes.pop()
                for totals in page_totals:
                    totals.votes = totals.votes[: len(header)]
            if not current.candidates:
                current.candidates = header
            elif current.candidates != header:
                raise ValueError(f"page {page_number}: header {header} against {current.candidates}")
            current.stations.extend(pending)
    if day is None:
        raise ValueError("empty document")
    return Document(title, day, [constituencies[k] for k in sorted(constituencies)])
