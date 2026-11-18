"""Citizens' Representatives Khural (local council) elections: results by polling section and council constituency."""

import re
from dataclasses import dataclass, field
from datetime import date

from ea.sources.mn import places, polls, sheets

CODE = re.compile(r"^\d+-\d+-\d+$")
TITLE = re.compile(
    r"^(?P<council>.+?)\s+ИТХ-ын сонгуульд\s+(?P<number>\d+)-р тойргоос.*\(Санал өгөх тоо\s+(?P<seats>\d+)\)"
)


@dataclass(frozen=True)
class Council:
    """A council as the title names it: the capital, an aimag, a soum, or a district."""

    kind: str
    aimag: str | None
    soum: str | None

    @property
    def key(self) -> str:
        if self.kind == "capital":
            return "capital"
        if self.kind == "aimag":
            return f"aimag:{places.aimag_code(self.aimag or '')}"
        place = places.place_of(self.aimag, self.soum or "")
        return f"{self.kind}:{place.code if place else self.soum}"

    @property
    def place(self) -> places.Place | None:
        return places.place_of(self.aimag, self.soum) if self.soum else None


def council(text: str) -> Council:
    text = re.sub(r"\s+", " ", text.strip())
    if text.startswith("Нийслэлийн"):
        return Council("capital", None, None)
    district = re.fullmatch(r"(.+?) дүүргийн", text)
    if district:
        return Council("district", None, district[1])
    soum = re.fullmatch(r"(.+?) аймгийн (.+?) сумын", text)
    if soum:
        return Council("soum", soum[1], soum[2])
    aimag = re.fullmatch(r"(.+?) аймгийн", text)
    if aimag:
        return Council("aimag", aimag[1], None)
    raise ValueError(f"unreadable council {text!r}")


@dataclass
class Block:
    council: Council
    number: int
    seats: int
    candidates: list[polls.Candidate]
    rows: list[polls.Row]
    declared: list[int] | None
    label: str | None = None


@dataclass
class Election:
    day: date
    blocks: list[Block] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def decode(cell: str) -> str:
    """A section code the workbook's spreadsheet turned into a date (for example 12-1-9 into 2009-12-01)."""
    found = re.fullmatch(r"(\d{4})-(\d\d)-(\d\d) 00:00:00", cell)
    if not found:
        return cell
    year = int(found[1])
    return f"{int(found[2])}-{int(found[3])}-{year - 2000 if year >= 2000 else year - 1900}"


def section_places(name: str) -> dict[str, places.Place]:
    """Section code to place, from the Commission's list of section committees."""
    out: dict[str, places.Place] = {}
    for row in sheets.sheet_rows(name, 0)[1:]:
        if CODE.match(row[2]):
            place = places.place_of(row[0], row[1])
            if place is None:
                raise ValueError(f"{name}: unreadable place {row[:3]}")
            out[row[2]] = place
    return out


def widen(locate: dict[str, places.Place]) -> dict[str, places.Place]:
    """Section code to place for sections a later list adds: a soum's sections share its first two code parts, and a
    capital district's share its first part."""
    by_prefix: dict[str, places.Place] = {}
    for code, place in locate.items():
        first, second, _ = code.split("-")
        by_prefix[first if int(first) >= 22 else f"{first}-{second}"] = place
    return by_prefix


def resolve(code: str, locate: dict[str, places.Place], by_prefix: dict[str, places.Place]) -> places.Place | None:
    if code in locate:
        return locate[code]
    first, second, _ = code.split("-")
    return by_prefix.get(first if int(first) >= 22 else f"{first}-{second}")


def blocks(name: str, locate: dict[str, places.Place], sheet: str | int = 0) -> list[Block]:
    rows = [[decode(cell) for cell in row] for row in sheets.sheet_rows(name, sheet)]
    by_prefix = widen(locate)
    out: list[Block] = []
    title: re.Match[str] | None = None
    current: Block | None = None
    columns: list[int] = []
    for index, row in enumerate(rows):
        cells = [(i, c) for i, c in enumerate(row) if c]
        if not cells:
            continue
        if len(cells) == 1 and (found := TITLE.match(cells[0][1])):
            title = found
            current = None
        elif cells[0][1] == "Тайлагнасан":
            if title is None:
                raise ValueError(f"{name}: header without a title at row {index}")
            columns = [i for i, _ in cells[1:]]
            current = Block(
                council(title["council"]),
                int(title["number"]),
                int(title["seats"]),
                [polls.candidate(c) for _, c in cells[1:]],
                [],
                None,
            )
            out.append(current)
        elif current is not None and CODE.match(cells[0][1]):
            values = [sheets.number(row[i]) for i in columns]
            place = resolve(cells[0][1], locate, by_prefix)
            current.rows.append(polls.Row(cells[0][1], place, cells[0][1], values))
        elif current is not None and cells[0][1] == "Нийт":
            current.declared = [sheets.number(row[i]) or 0 for i in columns]
    return out
