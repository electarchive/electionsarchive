"""Candidate-vote tables of Mongolia's presidential elections, read from the Commission's workbooks."""

import re
from dataclasses import dataclass, field
from datetime import date

from ea.sources.mn import places, sheets

BLANK = 6
INVALID = 5


@dataclass
class Candidate:
    label: str
    party: str | None


@dataclass
class Row:
    code: str
    place: places.Place | None
    label: str
    votes: list[int | None]
    extra: dict[int, int] = field(default_factory=dict)
    constituency: str | None = None


@dataclass
class Poll:
    key: str
    day: date
    first_round: bool
    files: list[str]
    candidates: list[Candidate]
    rows: list[Row]
    declared: list[int]
    declared_extra: dict[int, int]


CANONICAL = {"ҮНДЭСНИЙ ЭВСЭЛ": "ҮЭ"}


def candidate(header: str) -> Candidate:
    """A candidate column heading: the name, then the party after a dash or in parentheses."""
    header = re.sub(r"\s+", " ", header).strip()
    named = re.match(r"^(.*?) - (.+)$", header)
    if named:
        name, party = named[1], named[2].strip()
        inner = re.search(r"\(([^)]*)\)\s*$", party)
        party = inner[1].strip() if inner else party
    else:
        bracket = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", header)
        name, party = (bracket[1], bracket[2].strip()) if bracket else (header, None)
    return Candidate(name.strip(), CANONICAL.get(party, party) if party else None)


SECTION = re.compile(r"^(?:(.*?) аймаг )?(?:Улаанбаатар хот )?(.+?) (?:сум|дүүрэг) (\d+)-р хэс")


def _sectioned(
    name: str,
    sheet: str | None,
    key: str,
    day: date,
    first_round: bool,
    ncand: int,
    extras: dict[int, int],
) -> Poll:
    """A workbook with one row per polling section: number, label, candidate votes, then invalid and blank ballots."""
    rows = sheets.sheet_rows(name, sheet)
    header = next(row for row in rows if row and row[0] == "Дүгаар")
    candidates = [candidate(h) for h in header[2 : 2 + ncand]]
    out: list[Row] = []
    declared: list[int] = []
    declared_extra: dict[int, int] = {}
    for _, row in sheets.records(rows, rows.index(header) + 1):
        values = [sheets.number(v) for v in row[2 : 2 + ncand]]
        if sheets.is_index(row[0]):
            found = SECTION.match(row[1])
            place = places.place_of(found[1], found[2]) if found else None
            if place is None and row[1] != "Гадаад":
                raise ValueError(f"{name}: unreadable section {row[:3]}")
            extra = {m: v for m, c in extras.items() if (v := sheets.number(row[2 + ncand + c])) is not None}
            out.append(Row(row[0], place, row[1], values, extra))
        elif row[0] == "Нийт" and not declared:
            declared = [v or 0 for v in values]
            declared_extra = {m: sheets.number(row[2 + ncand + c]) or 0 for m, c in extras.items()}
    return Poll(key, day, first_round, [sheets.url(name)], candidates, out, declared, declared_extra)


def president_2013() -> Poll:
    return _sectioned("73d8c87c", None, "2013-06-26", date(2013, 6, 26), True, 3, {INVALID: 0, BLANK: 1})


def president_2017() -> Poll:
    return _sectioned(
        "ab68dfc3", "Эхний сонгууль 2017", "2017-06-26", date(2017, 6, 26), True, 3, {INVALID: 0, BLANK: 1}
    )


def president_2017_runoff() -> Poll:
    return _sectioned("ab68dfc3", "07-07", "2017-07-07", date(2017, 7, 7), False, 2, {INVALID: 0, BLANK: 1})


def president_2021() -> Poll:
    name = "fc728bc1"
    rows = sheets.sheet_rows(name, "2021 Ерөнхийлөгч тойргоор")
    header = rows[2]
    candidates = [candidate(h) for h in header[7:10]]
    out: list[Row] = []
    aimag = soum = ""
    seen: set[str] = set()
    for _, row in sheets.records(rows, 3):
        aimag, soum = row[2] or aimag, row[3] or soum
        if not sheets.is_index(row[0]):
            continue
        place = places.place_of(aimag, soum)
        if place is None:
            raise ValueError(f"{name}: unreadable place {row[:6]}")
        code = row[1] if row[1] not in seen else f"{row[1]}#{row[0]}"
        seen.add(row[1])
        out.append(Row(code, place, f"{aimag}, {soum}, {row[4]}", [sheets.number(v) for v in row[7:10]]))
    totals = sheets.sheet_rows("15295fd8")
    national = next(row for row in totals if row[0] == "НИЙТ")
    abroad = next(row for row in totals if row[0] == "Гадаад")
    out.append(
        Row(
            "abroad",
            None,
            "Гадаад",
            [sheets.number(abroad[i]) for i in (8, 10, 12)],
        )
    )
    declared = [sheets.number(national[i]) or 0 for i in (8, 10, 12)]
    return Poll(
        "2021-06-09",
        date(2021, 6, 9),
        True,
        [sheets.url(name), sheets.url("15295fd8")],
        candidates,
        out,
        declared,
        {},
    )
