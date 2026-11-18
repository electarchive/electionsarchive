"""The 2016 State Great Khural results by soum, read from the Commission's detailed workbooks. One sheet holds the
constituencies of one province: its columns are soums (or, in Darkhan-Uul and Orkhon, the polling sections of each
soum), and its rows are the registered voters, the ballots, and the votes for each candidate. The sheets for the
capital's constituencies list polling sections only, which the polling-section package already holds."""

import re
from dataclasses import dataclass, field

from ea.sources.mn import places, sheets

BOOKS = ("a9e37406", "8c2ca0d0")
TURNOUT = "5125f0aa"
TITLE = re.compile(r"тойрог\.\s*(?P<aimags>[^.]+?)\s+аймаг(?:уудын)?\.\s*Сум")
CONSTITUENCY = re.compile(r"^(\d+)\s+д[уү]г(?:аар|ээр)\s+тойрог$")
SECTION = re.compile(r"^\d+-р\s+хэсэг$")
LABELS = {
    "Нэрийн жагсаалтад": "registered",
    "Санал хураалтад оролцсон": "voters",
    "Хүчинтэй саналын": "valid",
    "Санал огт тэмдэглээгүй": "blank",
}
# The sheet for constituencies 12 and 13 holds two provinces. Constituency 12 takes in the three soums of Govisumber
# and the first eight soums of Dornogovi, and the sheet names no province for a soum, so it is told here. Each
# province's registered voters match the turnout workbook's lines for them.
GOVISUMBER_AIMAG = "Говьсүмбэр"
GOVISUMBER = {"Сүмбэр", "Баянтал", "Шивээговь"}
UNMARKED = re.compile(r"\s+(?:сумын|сум)\b.*$")


@dataclass
class Candidate:
    label: str
    party: str
    votes: dict[int, int] = field(default_factory=dict)
    total: int = 0


@dataclass
class Column:
    index: int
    soum: str
    section: str | None


@dataclass
class Sheet:
    name: str
    aimags: list[str]
    columns: list[Column]
    figures: dict[str, dict[int, int]]
    totals: dict[str, int]
    contests: dict[int, list[Candidate]]


@dataclass
class Unit:
    """The votes of one soum within one constituency."""

    constituency: int
    aimag: str
    soum: str
    columns: list[int]
    registered: int
    ballots: int
    valid: int
    blank: int
    votes: list[int]


def soum_name(text: str) -> str:
    text = re.sub(r"\s*\*+$", "", re.sub(r"\s+", " ", text).strip())
    return UNMARKED.sub("", text).strip()


def _int(text: str) -> int:
    value = sheets.number(text)
    if value is None:
        raise ValueError(f"not a number: {text!r}")
    return value


def read_sheet(rows: list[list[str]], name: str) -> Sheet:
    title = " ".join(rows[0][0].split())
    found = TITLE.search(title)
    if found is None:
        raise ValueError(f"{name}: no province in {title!r}")
    aimags = [part.strip() for part in re.split(r",", found["aimags"])]
    registered = next(i for i, row in enumerate(rows) if row[0].startswith("Нэрийн жагсаалтад"))
    total = next(
        (j for i in range(registered) for j, cell in enumerate(rows[i]) if cell.strip().upper() == "ДҮН"),
        None,
    )
    if total is None:
        raise ValueError(f"{name}: no total column")
    # The soum names sit in the row above the registered voters, or the row above that where sections are listed.
    names = [rows[i] for i in range(registered - 3, registered) if any(SECTION.match(c) for c in rows[i][3:total])]
    if names:
        sections_row = rows[registered - 1]
        soum_row = rows[registered - 2]
    else:
        sections_row = None
        soum_row = rows[registered - 1]
    columns: list[Column] = []
    base = current = ""
    for index in range(3, total):
        label = sections_row[index] if sections_row else ""
        if soum_row[index]:
            base = current = soum_name(soum_row[index])
        if not (soum_row[index] or label):
            continue
        if label and not SECTION.match(label):
            # A soum named among the sections is a column of its own; the sections after it return to the first soum.
            columns.append(Column(index, soum_name(label), None))
            current = base
            continue
        columns.append(Column(index, current, label or None))
    figures: dict[str, dict[int, int]] = {}
    totals: dict[str, int] = {}
    contests: dict[int, list[Candidate]] = {}
    number = 0
    for index, row in enumerate(rows[registered:], start=registered):
        head = row[0]
        if (metric := next((v for k, v in LABELS.items() if head.startswith(k)), None)) is not None:
            figures[metric] = {c.index: _int(row[c.index]) for c in columns if row[c.index]}
            totals[metric] = _int(row[total])
            continue
        if (title_match := CONSTITUENCY.match(head)) is not None:
            number = int(title_match[1])
            contests[number] = []
            continue
        if number and sheets.is_index(head) and row[2] == "тоо":
            candidate = Candidate(" ".join(row[1].split()), row[total + 1])
            candidate.votes = {c.index: _int(row[c.index]) for c in columns if row[c.index]}
            candidate.total = _int(row[total])
            contests[number].append(candidate)
    return Sheet(name, aimags, columns, figures, totals, contests)


def read_books() -> list[Sheet]:
    out: list[Sheet] = []
    for book in BOOKS:
        for name in sheets.sheet_names(book):
            rows = sheets.sheet_rows(book, name)
            title = " ".join(rows[0][0].split())
            if "Хэсэг, нэр дэвшигч" in title:
                continue
            out.append(read_sheet(rows, name))
    return out


def units(sheet: Sheet) -> list[Unit]:
    """Group a sheet's columns into soums within constituencies, summing the sections of a soum."""
    owner: dict[int, int] = {}
    for number, candidates in sheet.contests.items():
        for candidate in candidates:
            for index in candidate.votes:
                if owner.setdefault(index, number) != number:
                    raise ValueError(
                        f"{sheet.name}: column {index} holds votes in constituencies {owner[index]}, {number}"
                    )
    out: list[Unit] = []
    groups: dict[tuple[int, str], list[int]] = {}
    for column in sheet.columns:
        if column.index not in owner:
            raise ValueError(f"{sheet.name}: column {column.index} ({column.soum}) has no votes")
        group = groups.setdefault((owner[column.index], column.soum), [])
        if group and column.section is None:
            raise ValueError(f"{sheet.name}: {column.soum} has two columns in constituency {owner[column.index]}")
        group.append(column.index)
    for (number, soum), indexes in groups.items():
        aimag = GOVISUMBER_AIMAG if len(sheet.aimags) > 1 and soum in GOVISUMBER else sheet.aimags[0]
        if len(sheet.aimags) > 2:
            raise ValueError(f"{sheet.name}: more than two provinces")
        votes = [sum(c.votes.get(i, 0) for i in indexes) for c in sheet.contests[number]]
        out.append(
            Unit(
                number,
                aimag,
                soum,
                indexes,
                sum(sheet.figures["registered"].get(i, 0) for i in indexes),
                sum(sheet.figures["voters"].get(i, 0) for i in indexes),
                sum(sheet.figures["valid"].get(i, 0) for i in indexes),
                sum(sheet.figures["blank"].get(i, 0) for i in indexes),
                votes,
            )
        )
    return out


# Soums the sheets spell differently from the Commission's other workbooks, keyed by province code.
SPELLINGS = {
    ("039", "Баян-Адрага"): "Баян-Адарга",
    ("039", "Дэлгэрхээн"): "Дэлгэрхаан",
    ("043", "Мөнхайрхан"): "Мөнххайрхан",
    ("047", "Сүмбар"): "Сүмбэр",
    ("047", "Эрдэнэсэнт"): "Эрдэнэсант",
    ("057", "Сантмаргаз"): "Сантмаргац",
    ("065", "Дарви"): "Дарив",
}


def place(aimag: str, soum: str) -> places.Place:
    code = places.aimag_code(aimag)
    found = places.place_of(aimag, SPELLINGS.get((code or "", soum), soum))
    if found is None:
        raise ValueError(f"{aimag} / {soum}: no place")
    return found


@dataclass(frozen=True)
class Flash:
    """A constituency's line in the turnout workbook of election day: its province and its basic registered voters."""

    aimag: str
    sections: int
    registered: int
    voters_22h: int


def flash() -> dict[int, Flash]:
    out: dict[int, Flash] = {}
    aimag = ""
    for row in sheets.sheet_rows(TURNOUT)[7:]:
        match = re.fullmatch(r"(\d+)-р тойрог", row[0])
        if match:
            out[int(match[1])] = Flash(aimag, _int(row[2]), _int(row[5]), _int(row[6]))
        elif row[0] and row[2]:
            aimag = row[0]
    return out
