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
CANDIDATE_LEFT = 280.0


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


def _tail(tokens: list[Word]) -> tuple[int, float, int, int, int] | None:
    """The last four cells of a row: rejected ballots, total votes, percent, and electors."""
    if len(tokens) < 4:
        return None
    electors, percent, total, rejected = tokens[-1], tokens[-2], tokens[-3], tokens[-4]
    if not (
        NUMBER.match(electors.text)
        and PERCENT.match(percent.text)
        and NUMBER.match(total.text)
        and NUMBER.match(rejected.text)
    ):
        return None
    return (
        number(rejected.text),
        float(percent.text.rstrip("%").replace(",", "")),
        number(total.text),
        number(electors.text),
        0,
    )


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
    head = [w for w in page if w.y0 >= top and w.y1 <= end + 1 and w.x1 <= cut + 2 and w.xc >= CANDIDATE_LEFT - 10]
    rotated = sum(1 for w in head if w.y1 - w.y0 > 1.5 * (w.x1 - w.x0)) > len(head) / 2
    if rotated:
        groups: list[list[Word]] = [[] for _ in columns]
        edges = [e for _, e in columns]
        for w in head:
            groups[next((i for i, e in enumerate(edges) if w.xc <= e + 4), len(edges) - 1)].append(w)
        out: list[tuple[str, str]] = []
        for group in groups:
            ordered = lines_vertical(group)
            party = ""
            if ordered and _party_line(ordered[0]):
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


def read(path: Path) -> Document:
    pages = words_by_page(path)
    constituencies: dict[int, Constituency] = {}
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
        if label is None:
            continue
        row = [w for w in page if abs(w.yc - label.yc) < 4 and w.x0 > label.x1]
        row.sort(key=lambda w: w.x0)
        found = re.fullmatch(r"(.+?)\s*\((\d+)\)", " ".join(w.text for w in row))
        if not found:
            raise ValueError(f"page {page_number}: constituency line {row!r} in {text!r}")
        no = int(found.group(2))
        current = constituencies.setdefault(no, Constituency(no, found.group(1)))
        anchor = next((w for w in page if w.text == "LOCATION"), None)
        if anchor is None:
            raise ValueError(f"page {page_number}: no LOCATION header")
        end = anchor.y1
        body = lines([w for w in page if w.y0 > end - 1 and not (w.y1 - w.y0 > 20)])
        division = ""
        pending: list[Station] = []
        shapes: list[list[Word]] = []
        for line in body:
            joined = " ".join(w.text for w in line)
            if "Report Printed" in joined or re.search(r"\bPage \d+ of \d+", joined):
                continue
            if joined.startswith("ELECTORAL DIVISION:"):
                division = joined.split(":", 1)[1].strip()
                continue
            if "TOTALS" in joined:
                tail = _tail(line)
                if tail is None:
                    raise ValueError(f"page {page_number}: totals {joined!r}")
                rejected, _, total, electors, _ = tail
                cells = [w for w in line[:-4] if NUMBER.match(w.text) and w.x0 >= CANDIDATE_LEFT - 10]
                found_ps = re.search(r"(\d+) PSs", joined)
                totals = Totals(
                    int(found_ps.group(1)) if found_ps else 0,
                    [number(w.text) for w in cells],
                    rejected,
                    total,
                    electors,
                )
                if "CONSTITUENCY" in joined:
                    current.declared = totals
                else:
                    current.divisions.append(totals)
                continue
            tail = _tail(line)
            rejected: int | None
            total: int | None
            if tail is None:
                only = line[-1]
                if (
                    re.match(r"^\d", joined)
                    and len(line) > 3
                    and NUMBER.match(only.text)
                    and only.x0 > 560
                    and STATION.match(line[1].text)
                ):
                    rejected = total = None
                    electors = number(only.text)
                    head = line[:-1]
                else:
                    if re.match(r"^\d", joined) and len(line) > 5:
                        raise ValueError(f"page {page_number}: unreadable row {joined!r}")
                    continue
            else:
                rejected, _, total, electors, _ = tail
                head = line[:-4]
            emergency = any(w.text == "EPM" for w in head)
            head = [w for w in head if w.text != "EPM"]
            if head and re.fullmatch(r"\d+\.", head[0].text):
                head = head[1:]  # an older report numbers every row before its polling division
            if len(head) < 2 or not NUMBER.match(head[0].text) or not STATION.match(head[1].text):
                raise ValueError(f"page {page_number}: row {joined!r}")
            cells = [w for w in head[2:] if NUMBER.match(w.text) and w.x0 >= CANDIDATE_LEFT]
            location = [w for w in head[2:] if w not in cells]
            pending.append(
                Station(
                    division,
                    head[0].text,
                    head[1].text,
                    emergency,
                    " ".join(w.text for w in location),
                    [number(w.text) for w in cells],
                    rejected,
                    total,
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
            shapes.append(cells)
        if pending:
            count = int(median(len(s.votes) for s in pending if s.total is not None))
            if any(len(s.votes) != count for s in pending if s.total is not None):
                bad = next(s for s in pending if s.total is not None and len(s.votes) != count)
                raise ValueError(
                    f"page {page_number}: row {bad.pd}/{bad.ps} has {len(bad.votes)} candidate cells against {count}"
                )
            shapes = [shape for shape, s in zip(shapes, pending, strict=True) if s.total is not None]
            columns = [
                (
                    median(shape[i].xc for shape in shapes),
                    median(shape[i].x1 for shape in shapes),
                )
                for i in range(count)
            ]
            below = min(
                [s.box[1] for s in pending]
                + [line[0].y0 for line in body if " ".join(w.text for w in line).startswith("ELECTORAL DIVISION")]
            )
            header, mapping = _header(page, columns, label.y1 - 1, below - 1)
            if mapping != list(range(len(header))):
                for station in pending:
                    if station.votes:
                        cells: list[int | None] = [None] * len(header)
                        for i, value in zip(mapping, station.votes, strict=True):
                            cells[i] = value
                        station.votes = cells
            while header and header[-1] == ("", ""):
                if any(s.votes[len(header) - 1] for s in pending if s.votes):
                    raise ValueError(f"page {page_number}: votes in an unnamed column")
                header.pop()
                for station in pending:
                    if station.votes:
                        station.votes.pop()
            if not current.candidates:
                current.candidates = header
            elif current.candidates != header:
                raise ValueError(f"page {page_number}: header {header} against {current.candidates}")
            current.stations.extend(pending)
    if day is None:
        raise ValueError("empty document")
    return Document(title, day, [constituencies[k] for k in sorted(constituencies)])
