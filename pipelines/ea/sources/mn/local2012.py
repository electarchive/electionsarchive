"""The 2012 aimag, soum, and district council elections, read from the two workbooks of results by polling section.

One workbook holds the sections the Commission grouped by council constituency, the other those it grouped by
district. Each is a stack of blocks: a title naming the council and either its party-list ballot or one constituency
(a soum, a bag, or a khoroo), a header, one row per polling section, and a Нийт row. The workbooks print only the
sections they list, so a council's blocks cover part of its electorate, and a district's constituency may continue in
the other workbook with different sections."""

import re
from dataclasses import dataclass, field

from ea.sources.mn import local, parliament, places, polls, sheets

FILES = ("f70dc429", "1a27c1a1")

PARLIAMENT_TITLE = re.compile(r"^УИХ-ын сонгуулийн (?P<number>\d+)-р тойрогт .*\(Мандатын тоо\s+(?P<seats>\d+)\)$")
LIST_TITLE = re.compile(
    r"^(?P<head>.+?) ИТХ-ын сонгуульд нам, эвсэлд өгсөн хүчинтэй саналын тоо\s*\(Мандатын тоо\s+(?P<seats>\d+)\)$"
)
SEAT_TITLE = re.compile(
    r"^(?P<head>.+?) ИТХ-ын сонгуульд (?P<what>.+?) нэр дэвшигчдэд өгсөн хүчинтэй саналын тоо"
    r"\s*\(Мандатын тоо\s+(?P<seats>\d+)\)$"
)
PART = re.compile(r"^(?:(?P<name>.+?) )?(?P<n>\d+)(?:-р| дугаар| дүгээр) (?P<kind>баг|хороо)(?:аас|ноос)$")
SOUM = re.compile(r"^(?P<soum>.+?) сумаас$")
SOUM_PART = re.compile(r"^(?P<soum>.+?) сумын (?P<n>\d+)-р багаас$")
SECTION = re.compile(
    r"^(?:(?P<aimag>.+?) аймаг |Улаанбаатар хот )?(?P<place>.+?) (?P<part>\d+)-р (?:баг|хороо) (?P<number>\d+)-р хэсэг$"
)

# The party-list columns print each party's full name; the candidate columns print an abbreviation.
LIST_PARTIES = {
    "Монгол Ардын Нам": "МАН",
    "Ардчилсан Нам": "АН",
    "Монгол Ардын Хувьсгалт Нам": "МАХН",
    "Иргэний Зориг Ногоон Нам": "ИЗНН",
    "Монгол Үндэсний Ардчилсан Нам": "МҮАН",
    "Эрх Чөлөөг Хэрэгжүүлэгч Нам": "ЭЧХН",
}
TAILS = (("Хүчингүй", polls.INVALID), ("Санал огт", polls.BLANK), ("Нийт уншигдсан", 0))


@dataclass
class ListBlock(local.Block):
    """A council's party-list ballot, whose options are parties."""


@dataclass
class Raw:
    council: local.Council | None
    number: int | None
    seats: int
    label: str | None
    is_list: bool
    candidates: list[polls.Candidate] = field(default_factory=list)
    rows: list[polls.Row] = field(default_factory=list)
    declared: list[int] = field(default_factory=list)
    declared_extra: dict[int, int] = field(default_factory=dict)
    where: str = ""


def _read(name: str) -> list[Raw]:
    rows = sheets.sheet_rows(name, 0)
    out: list[Raw] = []
    current: Raw | None = None
    columns: list[int] = []
    extras: dict[int, int] = {}
    for index, row in enumerate(rows):
        cells = [c for c in row if c]
        if not cells:
            continue
        where = f"{name}: row {index}"
        if len(cells) == 1 and cells[0].startswith("УИХ-ын"):
            found = PARLIAMENT_TITLE.match(cells[0])
            if found is None:
                raise ValueError(f"{where}: unreadable title {cells[0]!r}")
            current = Raw(None, int(found["number"]), int(found["seats"]), None, False, where=where)
            out.append(current)
        elif len(cells) == 1 and (found := LIST_TITLE.match(cells[0])):
            current = Raw(local.council(found["head"]), None, int(found["seats"]), None, True, where=where)
            out.append(current)
        elif len(cells) == 1 and (found := SEAT_TITLE.match(cells[0])):
            current = Raw(local.council(found["head"]), None, int(found["seats"]), None, False, where=where)
            what = re.sub(r"\s*\(.*?\)\s*", " ", found["what"]).strip()
            if current.council and current.council.kind != "aimag" and (part := PART.match(what)):
                current.number = int(part["n"])
                label = f"{part['n']}-р {part['kind']}"
                current.label = f"{part['name']} {label}" if part["name"] else label
            elif (named := SOUM.match(what)) or (named := SOUM_PART.match(what)):
                current.label = f"{named['soum']} сум" + (f", {named['n']}-р баг" if "n" in named.groupdict() else "")
            else:
                raise ValueError(f"{where}: unreadable constituency {what!r}")
            out.append(current)
        elif row[0] == "Дүгаар":
            if current is None:
                raise ValueError(f"{where}: header without a title")
            tails = {measure: i for i, c in enumerate(row) for prefix, measure in TAILS if c.startswith(prefix)}
            if len(tails) != 3:
                raise ValueError(f"{where}: unreadable header {row!r}")
            first_tail = min(tails.values())
            columns = [i for i, c in enumerate(row) if 2 <= i < first_tail and c]
            extras = {m: i for m, i in tails.items() if m}
            if current.is_list:
                unknown = [row[i] for i in columns if row[i] not in LIST_PARTIES]
                if unknown:
                    raise ValueError(f"{where}: unknown parties {unknown}")
                current.candidates = [polls.Candidate(row[i], LIST_PARTIES[row[i]]) for i in columns]
            else:
                current.candidates = [polls.candidate(row[i]) for i in columns]
        elif current is not None and sheets.is_index(row[0]):
            label = SECTION.match(row[1])
            if label is None:
                raise ValueError(f"{where}: {row[1]!r} is not a section")
            place = places.place_of(label["aimag"], label["place"])
            if place is None:
                raise ValueError(f"{where}: unreadable place in {row[1]!r}")
            current.rows.append(
                polls.Row(
                    str(int(row[0])),
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
            raise ValueError(f"{block.where}: sections sum to {sums}, the file prints {block.declared}")
        for measure, total in block.declared_extra.items():
            if sum(r.extra.get(measure, 0) for r in block.rows) != total:
                raise ValueError(f"{block.where}: measure {measure} does not sum to the printed {total}")
    return out


def read(name: str) -> tuple[list[local.Block], parliament.Contest | None]:
    """The council blocks of one workbook, and its parliamentary block if it has one. A council's constituencies
    that the workbook titles by soum or bag are numbered in file order."""
    contest: Raw | None = None
    seen: set[tuple[str, bool, str]] = set()
    blocks: list[local.Block] = []
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for raw_block in _read(name):
        if raw_block.council is None:
            if contest is not None:
                raise ValueError(f"{raw_block.where}: a second parliamentary block")
            contest = raw_block
            continue
        ident = (raw_block.council.key, raw_block.is_list, raw_block.label or "")
        if ident in seen:
            raise ValueError(f"{raw_block.where}: {ident} appears twice")
        seen.add(ident)
        number = raw_block.number
        if number is None and not raw_block.is_list:
            counts[raw_block.council.key] = number = counts.get(raw_block.council.key, 0) + 1
        cls = ListBlock if raw_block.is_list else local.Block
        blocks.append(
            cls(
                raw_block.council,
                number or 0,
                raw_block.seats,
                raw_block.candidates,
                raw_block.rows,
                raw_block.declared,
                raw_block.label,
                raw_block.declared_extra,
            )
        )
    for block in [*blocks, *([contest] if contest else [])]:
        for row in block.rows:
            if labels.setdefault(row.code, row.label) != row.label:
                raise ValueError(f"{name}: section {row.code} is named {row.label!r} and {labels[row.code]!r}")
    if contest is None:
        return blocks, None
    return blocks, parliament.Contest(
        contest.number,
        contest.seats,
        contest.candidates,
        contest.rows,
        contest.declared,
        [sheets.url(name)],
        contest.declared_extra,
    )
