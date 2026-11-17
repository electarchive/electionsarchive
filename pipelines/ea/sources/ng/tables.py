"""The annexures of the Report of the 2019 General Election as rows."""

import re
from dataclasses import dataclass
from pathlib import Path

from ea.sources.ng import report
from ea.sources.ng.report import Word

CONSTITUENCY = re.compile(r"(SD|FC|SC)/(\d+)/?([A-Z]{2,3})")
NUMBER = re.compile(r"[\d,]+")


@dataclass(frozen=True)
class Candidate:
    number: int
    name: str
    gender: str
    party: str
    votes: int
    elected: bool


@dataclass(frozen=True)
class Seat:
    annex: int
    number: int
    kind: str
    code: str
    state: str
    name: str
    party: str
    lgas: str
    wards: int
    units: int


def _annex(words: list[Word], current: int) -> int:
    for i, word in enumerate(words[:-1]):
        if word.text == "Annexure" and words[i + 1].text.isdigit() and word.y < 150:
            return int(words[i + 1].text)
    return current


HEADERS: dict[tuple[int, str], Word] = {}


def _header(words: list[Word], text: str, annex: int) -> Word | None:
    """A column heading from this page, or from the annexure's first page when later pages leave it out."""
    found = next((w for w in words if w.text == text and w.y < 260), None)
    if found is not None:
        HEADERS[(annex, text)] = found
        return found
    old = HEADERS.get((annex, text))
    return None if old is None else Word(old.x, 0.0, old.x1, old.text)


def read(path: Path) -> tuple[list[Candidate], list[Seat]]:
    candidates: list[Candidate] = []
    seats: list[Seat] = []
    annex = 0
    ward_group = 0
    for _, words in report.pages(path):
        annex = _annex(words, annex)
        if annex == 1:
            candidates += _candidates(words)
        elif annex == 2:
            seats += _governors(words)
        elif annex in (3, 4, 5):
            seats += _constituencies(words, annex)
        elif annex in (6, 7):
            found, ward_group = _fct(words, annex, ward_group)
            seats += found
    return candidates, seats


def _candidates(words: list[Word]) -> list[Candidate]:
    gender = _header(words, "GENDER", 1)
    name = _header(words, "NAME", 1)
    if gender is None or name is None:
        gender = gender or Word(0, 0, 0, "")
    anchors = [w for w in words if gender and w.text in ("M", "F") and abs(w.x - gender.x) <= 8 and w.y > gender.y]
    anchors.sort(key=lambda w: w.y)
    if not anchors:
        return []
    left = (name.x if name else 100) - 8
    names = report.wrapped(
        [w for w in words if w.x < gender.x + 1], anchors, left, {i: gender.x - 1 for i in range(len(anchors))}
    )
    rows: list[Candidate] = []
    for i, anchor in enumerate(anchors):
        line = report.line_of(words, anchor)
        number = next(w for w in line if w.x < left).text
        tail = [w for w in line if w.x > anchor.x + 5]
        party = tail[0].text
        votes = int(tail[1].text.replace(",", ""))
        rows.append(Candidate(int(number), names[i], anchor.text, party, votes, any(w.text == "ELECTED" for w in tail)))
    return rows


def _governors(words: list[Word]) -> list[Seat]:
    rows: list[Seat] = []
    for word in words:
        if word.text != "GOVERNOR":
            continue
        line = report.line_of(words, word)
        before = [w.text for w in line if w.x < word.x]
        after = [w.text for w in line if w.x > word.x]
        rows.append(
            Seat(
                2,
                int(before[0]),
                "GOV",
                after[0],
                "",
                " ".join(before[1:]),
                after[-1],
                after[1],
                int(after[2]),
                int(after[3]),
            )
        )
    return rows


def _constituencies(words: list[Word], annex: int) -> list[Seat]:
    header = _header(words, "CONSTITUENCY", annex)
    if header is None:
        return []
    anchors = sorted((w for w in words if CONSTITUENCY.fullmatch(w.text) and w.y > header.y), key=lambda w: w.y)
    if not anchors:
        return []
    left = header.x - 8
    names = report.wrapped(
        [w for w in words if w.y > header.y + 5], anchors, left, {i: a.x - 1 for i, a in enumerate(anchors)}
    )
    rows: list[Seat] = []
    for i, anchor in enumerate(anchors):
        line = report.line_of(words, anchor)
        number = max((w for w in line if w.text.isdigit() and w.x < left + 25), key=lambda w: w.x)
        tail = [w.text for w in line if w.x > anchor.x + 5]
        match = CONSTITUENCY.fullmatch(anchor.text)
        assert match is not None
        kind, code, state = match.groups()
        if int(code) != int(number.text):
            code = f"{int(number.text):0{len(code)}d}"
        rows.append(
            Seat(
                annex,
                int(number.text),
                kind,
                f"{kind}/{code}/{state}",
                state,
                names[i],
                tail[-1],
                tail[0],
                int(tail[1]),
                int(tail[2]),
            )
        )
    return rows


def _fct(words: list[Word], annex: int, group: int) -> tuple[list[Seat], int]:
    header = _header(words, "CODE", annex)
    constituency = _header(words, "CONSTITUENCY", annex)
    if header is None or constituency is None:
        return [], group
    anchors = sorted(
        (w for w in words if re.fullmatch(r"\d\d", w.text) and abs(w.x - header.x) <= 15 and w.y > header.y + 5),
        key=lambda w: w.y,
    )
    if not anchors:
        return [], group
    left = constituency.x - 8
    names = report.wrapped(
        [w for w in words if w.y > header.y + 5], anchors, left, {i: a.x - 1 for i, a in enumerate(anchors)}
    )
    rows: list[Seat] = []
    for i, anchor in enumerate(anchors):
        line = report.line_of(words, anchor)
        number = next(w for w in line if w.x < constituency.x - 40)
        tail = [w.text for w in line if w.x > anchor.x + 5]
        rows.append(
            Seat(
                annex,
                int(number.text),
                "AC" if annex == 6 else "WD",
                anchor.text,
                "FC",
                names[i].removeprefix("FCT").strip(),
                tail[-1],
                "",
                int(tail[-3]) if len(tail) >= 3 else 0,
                int(tail[-2]),
            )
        )
    return rows, group
