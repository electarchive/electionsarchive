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
    declared_extra: dict[int, int] | None = None


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


SECTION_TITLE = re.compile(
    r"^(?P<head>.+?) ИТХ-ын сонгуульд (?P<what>.+?) нэр дэвшигчдэд өгсөн хүчинтэй саналын тоо"
    r"\s*\(Мандатын тоо\s+(?P<seats>\d+)\)$"
)
SECTION_LABEL = re.compile(
    r"^(?:(?P<aimag>.+?) аймаг |Улаанбаатар хот )?(?P<place>.+?) (?P<part>\d+)-р (?:баг|хороо) (?P<number>\d+)-р хэсэг$"
)
PART_TITLE = re.compile(r"^(?P<n>\d+)-р (?P<kind>баг|хороо)(?:аас|ноос)$")
SOUM_TITLE = re.compile(r"^(?P<soum>.+?) сумаас$")
SOUM_PART_TITLE = re.compile(r"^(?P<soum>.+?) сумын(?: \(.*?\))? (?P<n>\d+)-р багаас$")


def section_blocks(name: str, sheet: str | int = 0) -> list[Block]:
    """Blocks of a workbook that titles each council constituency and lists its polling sections.

    Each block has a title naming the council and the constituency, a header row ("Дүгаар", "Хэсгийн дүгаар", the
    candidates, then the invalid ballots, the ballots with no mark, and the valid ballots read), one row per section,
    and a Нийт row. A soum or district council's constituencies are its baghs or khoroos and keep their own numbers.
    An aimag council's constituencies are soums or a soum's baghs, and are numbered in file order."""
    rows = sheets.sheet_rows(name, sheet)
    out: list[Block] = []
    current: Block | None = None
    columns: list[int] = []
    extras: dict[int, int] = {}
    counts: dict[str, int] = {}
    seen: set[tuple[str, int]] = set()
    for index, row in enumerate(rows):
        cells = [c for c in row if c]
        if not cells:
            continue
        where = f"{name}: row {index}"
        if len(cells) == 1 and (found := SECTION_TITLE.match(cells[0])):
            owner = council(found["head"])
            what = found["what"].strip()
            if part := PART_TITLE.match(what):
                number, label = int(part["n"]), f"{part['n']}-р {part['kind']}"
            elif (named := SOUM_TITLE.match(what)) or (named := SOUM_PART_TITLE.match(what)):
                counts[owner.key] = number = counts.get(owner.key, 0) + 1
                label = f"{named['soum']} сум" + (f", {named['n']}-р баг" if "n" in named.groupdict() else "")
            else:
                raise ValueError(f"{where}: unreadable constituency {what!r}")
            if (owner.key, number) in seen:
                raise ValueError(f"{where}: {owner.key} constituency {number} appears twice")
            seen.add((owner.key, number))
            current = Block(owner, number, int(found["seats"]), [], [], None, label)
            out.append(current)
        elif current is not None and row[0] == "Дүгаар":
            tail = [i for i, c in enumerate(row) if c.startswith(("Хүчингүй", "Санал огт", "Нийт уншигдсан"))]
            if len(tail) != 3:
                raise ValueError(f"{where}: unreadable header {row!r}")
            columns = [i for i, c in enumerate(row) if 2 <= i < tail[0] and c]
            extras = {polls.INVALID: tail[0], polls.BLANK: tail[1]}
            current.candidates = [polls.candidate(row[i]) for i in columns]
        elif current is not None and sheets.is_index(row[0]):
            label = SECTION_LABEL.match(row[1])
            if label is None:
                raise ValueError(f"{where}: {row[1]!r} is not a section")
            place = places.place_of(label["aimag"], label["place"])
            if place is None:
                raise ValueError(f"{where}: unreadable place in {row[1]!r}")
            current.rows.append(
                polls.Row(
                    label["number"],
                    place,
                    row[1],
                    [sheets.number(row[i]) for i in columns],
                    {m: v for m, i in extras.items() if (v := sheets.number(row[i])) is not None},
                )
            )
        elif current is not None and row[0] == "Нийт":
            current.declared = [sheets.number(row[i]) or 0 for i in columns]
            current.declared_extra = {m: sheets.number(row[i]) or 0 for m, i in extras.items()}
    for block in out:
        sums = [sum(r.votes[k] or 0 for r in block.rows) for k in range(len(block.candidates))]
        if sums != block.declared:
            raise ValueError(
                f"{name}: {block.council.key} {block.number}: sections sum to {sums}, file prints {block.declared}"
            )
    return out
