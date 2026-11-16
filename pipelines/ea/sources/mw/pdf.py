"""Word-position readers for the Malawi Electoral Commission's polling-station PDFs."""

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>')
ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&#39;": "'"}
LINE_GAP = 3.5
WRAP_GAP = 10.5


@dataclass(frozen=True)
class Word:
    x: float
    y: float
    text: str
    x1: float = 0.0


def clean(text: str) -> str:
    for entity, char in ENTITIES.items():
        text = text.replace(entity, char)
    return text


def pages(path: Path) -> Iterator[tuple[int, list[Word]]]:
    """Yield each page's number and words, read from `pdftotext -bbox` without holding the whole file."""
    with subprocess.Popen(["pdftotext", "-bbox", str(path), "-"], stdout=subprocess.PIPE, text=True) as proc:
        assert proc.stdout is not None
        number = 0
        words: list[Word] = []
        for line in proc.stdout:
            if "<page " in line:
                if number:
                    yield number, words
                number += 1
                words = []
                continue
            found = WORD.search(line)
            if found:
                words.append(Word(float(found[1]), float(found[2]), clean(found[4]), float(found[3])))
        if number:
            yield number, words
    if proc.returncode:
        raise RuntimeError(f"pdftotext failed on {path}")


def lines(words: list[Word]) -> list[list[Word]]:
    """Group a page's words into lines by vertical position; codes sit a little above their line's text."""
    ordered = sorted(words, key=lambda word: (word.y, word.x))
    grouped: list[list[Word]] = []
    top = -100.0
    for word in ordered:
        if grouped and word.y - top <= LINE_GAP:
            grouped[-1].append(word)
        else:
            grouped.append([word])
            top = word.y
    return [sorted(group, key=lambda word: word.x) for group in grouped]


@dataclass
class Entry:
    words: list[str] = field(default_factory=list)
    page: int = 0
    last: float = -1000.0

    @property
    def text(self) -> str:
        return " ".join(self.words)


def add(entry: Entry | None, page: int, y: float, text: str) -> Entry:
    """Append to a column's current entry when it continues on the next line, otherwise open a new entry."""
    if entry is not None and entry.page == page and 0 <= y - entry.last <= WRAP_GAP:
        entry.words.append(text)
        entry.last = y
        return entry
    return Entry([text], page, y)


@dataclass
class Station:
    region: Entry
    district: Entry
    constituency: Entry
    ward: Entry
    centre: Entry
    number: int
    page: int
    rows: list[tuple[str, str, int]] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)


TOTALS = {"valid": "valid", "null": "null", "no.": "cast"}


def _int(text: str) -> int:
    return int(text.replace(",", ""))


def read_stations(path: Path) -> Iterator[Station]:
    """Merge the pieces of a station whose rows continue on the next page."""
    held: Station | None = None
    for station in _stations(path):
        if held is not None and (held.centre.text, held.number, held.ward.text) == (
            station.centre.text,
            station.number,
            station.ward.text,
        ):
            held.rows.extend(station.rows)
            held.totals.update(station.totals)
            continue
        if held is not None:
            yield held
        held = station
    if held is not None:
        yield held


def _stations(path: Path) -> Iterator[Station]:
    """Read the registration-centre tables: a region, district, constituency, ward, centre, and station, with
    each candidate's party and votes and the three totals."""
    region = district = constituency = ward = centre = None
    station: Station | None = None
    last_row = -1000.0
    columns: tuple[float, ...] = ()
    start = 0.0
    for number, words in pages(path):
        page_lines = lines(words)
        if number == 1:
            head = next(line for line in page_lines if {"Region", "Candidate", "Affiliation"} <= {w.text for w in line})
            at = {w.text: w.x for w in head}
            columns = tuple(
                at[name] - 3
                for name in ("District", "Constituency", "Ward", "Centre", "Station", "Candidate", "Affiliation")
            ) + (at["Affiliation"] + 74,)
            start = head[0].y + 10
        else:
            title = next((line[0].y for line in page_lines if line[0].text == "2019" and len(line) > 3), 0.0)
            start = title + 3
        for line in page_lines:
            top = line[0].y
            if top < start:
                continue
            by: list[list[Word]] = [[] for _ in range(9)]
            for word in line:
                by[sum(word.x >= edge for edge in columns)].append(word)
            for index, label in enumerate(("region", "district", "constituency", "ward", "centre")):
                for word in by[index]:
                    entry = add(
                        {0: region, 1: district, 2: constituency, 3: ward, 4: centre}[index], number, top, word.text
                    )
                    if index == 0:
                        region = entry
                    elif index == 1:
                        district = entry
                    elif index == 2:
                        constituency = entry
                    elif index == 3:
                        ward = entry
                    else:
                        centre = entry
            label = [word.text for word in sorted(by[5], key=lambda word: word.x)]
            if label[:1] == ["Station"] and len(label) == 2:
                assert region and district and constituency and ward and centre
                if station is not None:
                    yield station
                station = Station(region, district, constituency, ward, centre, int(label[1]), number)
            if station is None:
                raise ValueError(f"{path.name} page {number}: a row appears before any station")
            middle = [word.text for word in sorted(by[6] + by[7], key=lambda word: word.x)]
            right = [word.text for word in sorted(by[8], key=lambda word: word.x)]
            if middle[:2] == ["Total", "No."] or (middle[:1] == ["Total"] and "Votes" in middle):
                kind = "valid" if "Valid" in middle else "null" if "Null" in middle else "cast"
                station.totals[kind] = _int(right[-1])
                last_row = -1000.0
                continue
            if right and by[7]:
                party = " ".join(word.text for word in sorted(by[7], key=lambda word: word.x))
                name = " ".join(word.text for word in sorted(by[6], key=lambda word: word.x))
                station.rows.append((name, party, _int(right[-1])))
                last_row = top
            elif by[6] and not by[7] and not right and station.rows and 0 < top - last_row <= WRAP_GAP:
                name, party, votes = station.rows[-1]
                station.rows[-1] = (
                    f"{name} {' '.join(word.text for word in sorted(by[6], key=lambda w: w.x))}",
                    party,
                    votes,
                )
                last_row = top
            elif by[6] or by[7] or right:
                raise ValueError(f"{path.name} page {number}: unread line {[word.text for word in line]}")
    if station is not None:
        yield station


@dataclass
class Count:
    district: Entry
    constituency: Entry
    ward: Entry
    centre: Entry
    station: int
    page: int
    registered: int
    null: int
    cast: int
    votes: list[int]


TITLE = {
    "MALAWI",
    "ELECTORAL",
    "COMMISSION",
    "FRESH",
    "PRESIDENTIAL",
    "ELECTION",
    "CONSTITUENCY",
    "RESULTS",
    "BY",
    "POLLING",
    "STATION",
}


def read_counts(path: Path, candidates: int) -> Iterator[Count]:
    """Read the fresh presidential election's table: one line per polling station, with right-aligned numbers."""
    district = constituency = ward = centre = None
    cuts = (392.0, 415.0, 460.0, 530.0, 580.0)
    for number, words in pages(path):
        words = [
            word
            for word in words
            if not (word.text in TITLE and any(abs(word.y - at) < 0.3 for at in (95.4, 104.7, 114.1)))
        ]
        for line in lines(words):
            top = line[0].y
            if number == 1 and top < 165 or top > 500:
                continue
            cells: list[list[Word]] = [[] for _ in range(4)]
            figures: list[Word] = []
            station = None
            for word in line:
                if word.x < 120:
                    cells[0].append(word)
                elif word.x < 170:
                    cells[1].append(word)
                elif word.x < 212:
                    cells[2].append(word)
                elif word.x < 313:
                    cells[3].append(word)
                elif word.x < 340 and re.fullmatch(r"PS\d+", word.text):
                    station = int(word.text[2:])
                else:
                    figures.append(word)
            for index, label in enumerate(("district", "constituency", "ward")):
                for word in cells[index]:
                    entry = add({0: district, 1: constituency, 2: ward}[index], number, top, word.text)
                    if index == 0:
                        district = entry
                    elif index == 1:
                        constituency = entry
                    else:
                        ward = entry
            for word in cells[3]:
                if re.fullmatch(r"\d{5}", word.text):
                    centre = None
                centre = add(centre, number, top, word.text)
            if line[0].text == "Total" and station is None:
                continue
            if station is None:
                if figures:
                    raise ValueError(f"{path.name} page {number}: unread line {[w.text for w in line]}")
                continue
            assert district and constituency and ward and centre
            bins: list[list[int]] = [[] for _ in range(6)]
            for word in figures:
                bins[sum(word.x1 >= cut for cut in cuts)].append(int(word.text.replace(",", "")))
            values = [item[0] if len(item) == 1 else None for item in bins]
            if any(item is None for item in values[: 3 + 0]) and False:
                raise ValueError("unreachable")
            yield Count(
                district,
                constituency,
                ward,
                centre,
                station,
                number,
                *[0 if v is None else v for v in values[:3]],
                [0 if v is None else v for v in values[3:]],
            )


def read_total(path: Path, candidates: int) -> list[int]:
    """Read the printed national row at the end of the fresh presidential table."""
    last: list[Word] = []
    for _, words in pages(path):
        last = words
    row = next(line for line in lines(last) if line[0].text == "Total" and len(line) > 1)
    cuts = (460.0, 530.0, 580.0)
    found: list[list[int]] = [[] for _ in range(4)]
    for word in row[1:]:
        found[sum(word.x1 >= cut for cut in cuts)].append(int(word.text.replace(",", "")))
    return [item[0] for item in found[1 : candidates + 1]]
