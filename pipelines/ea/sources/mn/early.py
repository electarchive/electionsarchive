"""The 2012 and 2016 State Great Khural and council elections, read from the Commission's workbooks of results by
polling section. A section is named by its parliamentary constituency and its number within it; the workbooks name no
soum for it."""

import re
from dataclasses import dataclass
from datetime import date

from ea.sources.mn import local, parliament, polls, sheets

DAY_2012 = date(2012, 6, 28)
DAY_2016 = date(2016, 6, 29)
RESULTS_2016 = "7fbe9ccc"
RESULTS_2012 = "1982c2f3"
SUMMARY_2012 = "45697e47"

BLOCK_TITLE = re.compile(
    r"^(?P<what>.+?)\s+сонгуульд\s+(?P<label>.+?)\s+нэр дэвшигчдэд .*\(Санал өгөх тоо\s+(?P<seats>\d+)\)"
)
CONSTITUENCY_TITLE = re.compile(r"^Улсын Их Хурлын сонгуульд (\d+)-р тойрогт ")
SECTION_NUMBER = re.compile(r"(\d+)")

# Party names as the 2012 list workbook prints them, to the abbreviation the Commission uses in 2012.
LIST_PARTIES_2012 = {
    "Монгол Ардын Нам": "МАН",
    "Ардчилсан Нам": "АН",
    "Монголын Ногоон Нам": "МНН",
    "Иргэний Зориг Ногоон Нам": "ИЗНН",
    "Монголын Уламжлалын Нэгдсэн Нам": "МУНН",
    "Эх Орон Нам": "ЭОН",
    "Гурав Дахь Хүчин эвсэл": "ГДХЭ",
    "Монголын Социал Демократ Нам": "МСДН",
    "МАХН-МҮАН-ын Шударга ёс эвсэл": "ШЁЭ",
    "Эрх Чөлөөг Хэрэгжүүлэгч Нам": "ЭЧХН",
    "Иргэний Хөдөлгөөний Нам": "ИХН",
    "Хөгжлийн Хөтөлбөр Нам": "ХХН",
    "Эх Орончдын Нэгдсэн Нам": "ЭОНН",
}
SECTION_2012 = re.compile(r"^.+? (\d+)-Р ТОЙРОГ (\d+)-р ?хэсэг(.*)$")


def parliament_2016() -> list[parliament.Contest]:
    rows = sheets.sheet_rows(RESULTS_2016, 0)
    out: list[parliament.Contest] = []
    current: parliament.Contest | None = None
    columns: list[int] = []
    number = 0
    for index, row in enumerate(rows):
        cells = [c for c in row if c]
        if len(cells) == 1 and (found := CONSTITUENCY_TITLE.match(cells[0])):
            number = int(found[1])
            seats = re.search(r"Санал өгөх тоо\s+(\d+)", cells[0])
            assert seats is not None
            current = parliament.Contest(number, int(seats[1]), [], [], None, [sheets.url(RESULTS_2016)])
            out.append(current)
        elif current is not None and row[1] == "Тайлагнасан":
            columns = [i for i, c in enumerate(row) if i >= 2 and c]
            current.candidates = [polls.candidate(row[i]) for i in columns]
        elif current is not None and row[1].endswith("хэсэг"):
            owner = SECTION_NUMBER.search(row[0])
            section = SECTION_NUMBER.match(row[1])
            if owner is None or section is None or int(owner[1]) != number:
                raise ValueError(f"{RESULTS_2016}: row {index} {row[:2]} is outside constituency {number}")
            code = f"{number}-{section[1]}"
            current.rows.append(
                polls.Row(
                    code,
                    None,
                    f"{number}-р тойрог, {section[1]}-р хэсэг",
                    [sheets.number(row[i]) for i in columns],
                    constituency=f"2016-{number}",
                )
            )
        elif current is not None and row[1] == "Нийт":
            current.declared = [sheets.number(row[i]) or 0 for i in columns]
    _verify(out)
    return out


def _verify(contests: list[parliament.Contest]) -> None:
    for contest in contests:
        sums = [sum(r.votes[k] or 0 for r in contest.rows) for k in range(len(contest.candidates))]
        if contest.declared is None or sums != contest.declared:
            raise ValueError(
                f"constituency {contest.number}: sections sum to {sums}, the file prints {contest.declared}"
            )


def local_2016() -> list[local.Block]:
    """The 2016 capital and aimag council elections by section, from the second sheet of the results workbook."""
    rows = sheets.sheet_rows(RESULTS_2016, 1)
    out: list[local.Block] = []
    current: local.Block | None = None
    columns: list[int] = []
    counts: dict[str, int] = {}
    for index, row in enumerate(rows):
        cells = [c for c in row if c]
        if len(cells) == 1 and (found := BLOCK_TITLE.match(cells[0])):
            council = local.council(found["what"].replace("ИТХ-ын", "").strip())
            label = found["label"]
            if council.kind == "capital":
                named = re.match(r"(\d+)-р тойрогт", label)
                if named is None:
                    raise ValueError(f"{RESULTS_2016}: unreadable capital council title {cells[0]!r}")
                number = int(named[1])
                label = f"{number}-р тойрог"
            else:
                counts[council.key] = number = counts.get(council.key, 0) + 1
            current = local.Block(council, number, int(found["seats"]), [], [], None, label)
            out.append(current)
        elif current is not None and row[1] == "Тайлагнасан":
            columns = [i for i, c in enumerate(row) if i >= 2 and c]
            current.candidates = [polls.candidate(row[i]) for i in columns]
        elif current is not None and row[1].endswith("хэсэг"):
            owner = SECTION_NUMBER.search(row[0])
            section = SECTION_NUMBER.match(row[1])
            if owner is None or section is None:
                raise ValueError(f"{RESULTS_2016}: row {index} {row[:2]} is not a section")
            current.rows.append(
                polls.Row(
                    f"{owner[1]}-{section[1]}",
                    None,
                    f"{owner[1]}-р тойрог, {section[1]}-р хэсэг",
                    [sheets.number(row[i]) for i in columns],
                    constituency=f"2016-{owner[1]}",
                )
            )
        elif current is not None and row[1] == "Нийт":
            current.declared = [sheets.number(row[i]) or 0 for i in columns]
    for block in out:
        sums = [sum(r.votes[k] or 0 for r in block.rows) for k in range(len(block.candidates))]
        if sums != block.declared:
            raise ValueError(
                f"{block.council.key} {block.number}: sections sum to {sums}, the file prints {block.declared}"
            )
    return out


@dataclass
class List2012:
    parties: list[str]
    rows: list[polls.Row]
    declared: list[int]
    extra_declared: dict[int, int]
    seats: int


INVALID = 5
BLANK = 6


def _list_block(rows: list[list[str]], start: int, seats: int) -> List2012:
    header = rows[start + 2]
    columns = [i for i, c in enumerate(header) if i >= 2 and c in LIST_PARTIES_2012]
    extra = {
        INVALID: next(i for i, c in enumerate(header) if c.startswith("Хүчингүй")),
        BLANK: next(i for i, c in enumerate(header) if c.startswith("Санал огт")),
    }
    out: list[polls.Row] = []
    declared: list[int] = []
    extra_declared: dict[int, int] = {}
    for row in rows[start + 3 :]:
        if row[0] == "Нийт":
            declared = [sheets.number(row[i]) or 0 for i in columns]
            extra_declared = {m: sheets.number(row[i]) or 0 for m, i in extra.items()}
            break
        if not sheets.is_index(row[0]):
            continue
        found = SECTION_2012.match(row[1])
        values = [sheets.number(row[i]) for i in columns]
        extras = {m: v for m, i in extra.items() if (v := sheets.number(row[i])) is not None}
        if found:
            tail = found[3].strip()
            code = f"{found[1]}-{found[2]}" + (f" {tail}" if tail else "")
            label = f"{found[1]}-р тойрог, {found[2]}-р хэсэг" + (f" {tail}" if tail else "")
            out.append(polls.Row(code, None, label, values, extras, f"2012-{found[1]}"))
        elif row[1] == "Central Scanning Location":
            out.append(polls.Row("central", None, row[1], values, extras))
        else:
            raise ValueError(f"{RESULTS_2012}: unreadable section {row[:2]}")
    return List2012([header[i] for i in columns], out, declared, extra_declared, seats)


def list_2012() -> List2012:
    result = _list_block(sheets.sheet_rows(RESULTS_2012, 0), 0, 28)
    _verify_list(result)
    return result


def capital_list_2012() -> List2012:
    rows = sheets.sheet_rows(RESULTS_2012, 0)
    start = next(i for i, r in enumerate(rows) if r[0].startswith("Нийслэлийн ИТХ-ын сонгуульд"))
    result = _list_block(rows, start, 0)
    _verify_list(result)
    return result


def _verify_list(block: List2012) -> None:
    sums = [sum(r.votes[k] or 0 for r in block.rows) for k in range(len(block.parties))]
    if sums != block.declared:
        raise ValueError(f"2012 list: sections sum to {sums}, the file prints {block.declared}")
    for measure, total in block.extra_declared.items():
        if sum(r.extra.get(measure, 0) for r in block.rows) != total:
            raise ValueError(f"2012 list: measure {measure} does not sum to the printed {total}")


def constituencies_2012() -> list[parliament.Contest]:
    """The constituencies of the 2012 workbook, which prints only the first five."""
    rows = sheets.sheet_rows(RESULTS_2012, 0)
    out: list[parliament.Contest] = []
    current: parliament.Contest | None = None
    columns: list[int] = []
    for row in rows:
        cells = [c for c in row if c]
        found = (
            re.match(r"^УИХ-ын сонгуулийн (\d+)-р тойрогт .*\(Мандатын тоо\s+(\d+)\)", cells[0])
            if len(cells) == 1
            else None
        )
        if found:
            current = parliament.Contest(int(found[1]), int(found[2]), [], [], None, [sheets.url(RESULTS_2012)])
            out.append(current)
            columns = []
        elif current is not None and row[0] == "Дүгаар":
            columns = [
                i for i, c in enumerate(row) if i >= 2 and c and not c.startswith(("Хүчингүй", "Санал огт", "Нийт"))
            ]
            current.candidates = [polls.Candidate(row[i], None) for i in columns]
        elif current is not None and sheets.is_index(row[0]):
            if row[1] == "Central Scanning Location":
                current.rows.append(polls.Row("central", None, row[1], [sheets.number(row[i]) for i in columns]))
                continue
            found_row = SECTION_2012.match(row[1])
            if found_row is None or int(found_row[1]) != current.number:
                raise ValueError(f"{RESULTS_2012}: unreadable section {row[:2]}")
            tail = found_row[3].strip()
            code = f"{found_row[1]}-{found_row[2]}" + (f" {tail}" if tail else "")
            current.rows.append(
                polls.Row(
                    code,
                    None,
                    f"{found_row[1]}-р тойрог, {found_row[2]}-р хэсэг" + (f" {tail}" if tail else ""),
                    [sheets.number(row[i]) for i in columns],
                    constituency=f"2012-{found_row[1]}",
                )
            )
        elif current is not None and row[0] == "Нийт":
            current.declared = [sheets.number(row[i]) or 0 for i in columns]
    # the workbook stops inside the last block, which is dropped when its total row is missing
    complete = [c for c in out if c.declared is not None]
    _verify(complete)
    return complete
