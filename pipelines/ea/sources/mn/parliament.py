"""The 2024 State Great Khural election, by polling section: the 13 constituencies and the nationwide party list."""

import re
from dataclasses import dataclass, field
from datetime import date

from ea.sources.mn import places, polls, sheets

DAY_2024 = date(2024, 6, 28)
CONSTITUENCIES_2024 = {
    1: "e19c6db1",
    2: "c7b48fec",
    3: "cdc1bcdf",
    4: "b816c16c",
    5: "8993f58c",
    6: "38df28f1",
    7: "ee0ea415",
    8: "f11edc09",
    9: "df31734e",
    10: "09f7a301",
    11: "8574c462",
    12: "fe1c6f04",
    13: "1fa3591f",
}
LIST_2024 = "0786a02e"
TURNOUT_2024 = "737508e4"
NAME = re.compile(r"[А-ЯӨҮЁ]{3,}")
SECTION_CODE = re.compile(r"^\d+-\d+-\d+(-\d+)?$")


@dataclass
class Contest:
    number: int | None
    seats: int
    candidates: list[polls.Candidate]
    rows: list[polls.Row]
    declared: list[int] | None
    files: list[str]
    declared_extra: dict[int, int] = field(default_factory=dict)


def seats_2024() -> dict[int, int]:
    rows = sheets.sheet_rows(TURNOUT_2024)
    return {int(row[0]): int(row[2]) for row in rows[3:] if sheets.is_index(row[0]) and sheets.is_index(row[2])}


def constituency_2024(number: int) -> Contest:
    name = CONSTITUENCIES_2024[number]
    rows = sheets.sheet_rows(name, 0)
    head = max(range(min(len(rows), 12)), key=lambda i: sum(bool(NAME.search(c)) for c in rows[i]))
    columns = [i for i, cell in enumerate(rows[head]) if NAME.search(cell)]
    candidates = [polls.candidate(rows[head][i]) for i in columns]
    out: list[polls.Row] = []
    for _, row in sheets.records(rows, head + 1):
        coded = next((c for c in row[3:9] if SECTION_CODE.match(c) and c.count("-") == 3), None)
        if not sheets.is_index(row[0]):
            if not (row[0] and coded and out):
                continue
            # one section's sequence number is mistyped in the workbook; it follows the previous row's
            row[0] = str(int(out[-1].code) + 1)
        place = places.place_of(row[1], row[2])
        if place is None:
            raise ValueError(f"{name}: unreadable place {row[:4]}")
        code = next((c for c in row[3:9] if SECTION_CODE.match(c) and c.count("-") == 3), None)
        label = ", ".join(c for c in row[1:4] if c)
        out.append(
            polls.Row(
                str(int(float(row[0]))),
                place,
                f"{label} ({code})" if code else label,
                [sheets.number(row[i]) for i in columns],
            )
        )
    sums = [sum(r.votes[k] or 0 for r in out) for k in range(len(columns))]
    declared = None
    seen = {r.code for r in out}
    for row in rows[head + 1 :]:
        if row[0] not in seen and not sheets.is_index(row[0]) and any(row):
            values = [sheets.number(row[i]) for i in columns]
            if None not in values and values == sums:
                declared = [v or 0 for v in values]
                break
    return Contest(
        number, seats_2024()[number], candidates, out, declared, [sheets.url(name), sheets.url(TURNOUT_2024)]
    )


def list_2024(sections: dict[str, polls.Row]) -> Contest:
    """The nationwide party-list ballot; its rows carry the same section numbers as the constituency workbooks."""
    rows = sheets.sheet_rows(LIST_2024, "нам эвсэл")
    head = next(i for i, row in enumerate(rows) if row and row[0] == "№")
    columns = [i for i, cell in enumerate(rows[head]) if i >= 2 and cell]
    candidates = [polls.Candidate(rows[head][i], None) for i in columns]
    out: list[polls.Row] = []
    abroad: polls.Row | None = None
    declared: list[int] | None = None
    for _, row in sheets.records(rows, head + 1):
        values = [sheets.number(row[i]) for i in columns]
        number = re.match(r"^(\d+)(?:\.0+)?(?:\+.*)?$", row[0])
        if number and int(number[1]) == len(out) + 1 and row[1] != "Гадаад":
            if number[1] not in sections:
                raise ValueError(f"list section {number[1]} has no constituency row")
            known = sections[number[1]]
            out.append(polls.Row(number[1], known.place, known.label, values))
        elif row[1] == "Гадаад":
            abroad = polls.Row("abroad", None, "Гадаад", values)
        elif row[1] == "Нийт":
            declared = [v or 0 for v in values]
    if abroad is not None:
        out.append(abroad)
    return Contest(None, 48, candidates, out, declared, [sheets.url(LIST_2024)])


DAY_2020 = date(2020, 6, 24)
RESULTS_2020 = "3c7b2c91"
TURNOUT_2020 = "4194d7ca"


@dataclass
class Constituency:
    """A constituency whose results the Commission publishes as one line per candidate, with no finer unit."""

    number: int
    name: str
    seats: int
    registered: int
    voters: int
    blank: int
    candidates: list[tuple[str, str, int]]
    total: int


def constituencies_2020() -> list[Constituency]:
    seats = {
        int(row[0]): int(row[3])
        for row in sheets.sheet_rows(TURNOUT_2020, "22 цаг")
        if sheets.is_index(row[0]) and sheets.is_index(row[3])
    }
    out: list[Constituency] = []
    rows = sheets.sheet_rows(RESULTS_2020, 0)
    for row in rows[4:]:
        if row[2] and sheets.is_index(row[2]):
            number = int(float(row[2]))
            out.append(
                Constituency(
                    number,
                    row[1],
                    seats[number],
                    sheets.number(row[3]) or 0,
                    sheets.number(row[4]) or 0,
                    sheets.number(row[6]) or 0,
                    [],
                    0,
                )
            )
        if row[8] and row[10] and out and row[0] != "ДҮН":
            out[-1].candidates.append((row[8], row[9], sheets.number(row[10]) or 0))
        if row[0] == "ДҮН" and out:
            out[-1].total = sheets.number(row[10]) or 0
    for entry in out:
        if sum(votes for _, _, votes in entry.candidates) != entry.total:
            raise ValueError(f"2020 constituency {entry.number}: candidate votes do not sum to the printed total")
    return out


BY_ELECTION_2021 = date(2021, 10, 16)
BY_ELECTIONS_2021 = {28: "53649ec2", 18: "e78f8a03"}
SECTION_NUMBER = re.compile(r"^(\d+-\d+)(?:-р хэсэг)?$")
LISTED = re.compile(r"^(.+?)\s*/([^/]+)/\s*нэр дэвшигчийн")


@dataclass
class ByElection:
    number: int
    candidates: list[polls.Candidate]
    rows: list[polls.Row]
    declared: list[int]
    declared_extra: dict[int, int]
    file: str


def by_election_2021(number: int) -> ByElection:
    name = BY_ELECTIONS_2021[number]
    rows = sheets.sheet_rows(name, 0)
    candidates = []
    for cell in next(row for row in rows if any(LISTED.match(c) for c in row)):
        found = LISTED.match(cell)
        if found:
            party = found[2].strip()
            independent = party.startswith("Бие даан")
            candidates.append(polls.Candidate(found[1].strip(), None if independent else party))
    out: list[polls.Row] = []
    declared: list[int] = []
    declared_extra: dict[int, int] = {}
    count = len(candidates)
    for row in rows:
        cells = [c for c in row if c]
        if cells and cells[0] == "Дүн":
            values = [sheets.number(c) or 0 for c in cells[1:]]
            declared = [values[6 + 2 * k] for k in range(count)]
            declared_extra = {2: values[0], 3: values[5], 6: values[6 + 2 * count]}
            continue
        at = next((i for i, c in enumerate(cells) if SECTION_NUMBER.match(c)), None)
        if at is None or at < 1:
            continue
        values = [sheets.number(c) for c in cells[at + 1 :]]
        code = SECTION_NUMBER.match(cells[at])
        assert code is not None
        extra = {2: values[0], 3: values[5], 6: values[6 + 2 * count]}
        out.append(
            polls.Row(
                code[1],
                None,
                f"{cells[at - 1]}, {code[1]}",
                [values[6 + 2 * k] for k in range(count)],
                {m: v for m, v in extra.items() if v is not None},
            )
        )
    sums = [sum(r.votes[k] or 0 for r in out) for k in range(count)]
    if sums != declared:
        raise ValueError(f"by-election {number}: sections sum to {sums}, the total row prints {declared}")
    return ByElection(number, candidates, out, declared, declared_extra, sheets.url(name))
