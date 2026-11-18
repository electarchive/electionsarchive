"""The 2012 and 2016 State Great Khural and council elections, read from the Commission's workbooks of results by
polling section. A section is named by its parliamentary constituency and its number within it; the workbooks name no
soum for it."""

import re
from dataclasses import dataclass
from datetime import date

from ea.sources.mn import book, local, parliament, places, polls, sheets

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


# 2012 re-vote of constituency 26, the vote in the workbook of the Commission's results of that repeat vote.
REVOTE_26 = "50c8b733"
DAY_REVOTE_26 = date(2013, 4, 14)
# The aimag code of each province constituency, to name its soums; the soums of Govisumber are told apart.
AIMAG_OF_CONSTITUENCY = {
    1: "073",
    2: "071",
    3: "069",
    4: "067",
    5: "065",
    6: "063",
    7: "061",
    8: "059",
    9: "057",
    10: "055",
    11: "053",
    12: "051",
    13: "049",
    14: "047",
    15: "046",
    16: "043",
    17: "041",
    18: "039",
    19: "037",
    20: "035",
}
PARTY_KEYS: dict[str, str | None] = {}


def _party_key(text: str) -> str:
    return re.sub(r"[\s\-]+", "", text).casefold()


for _name, _abbreviation in LIST_PARTIES_2012.items():
    PARTY_KEYS[_party_key(_name)] = _abbreviation
    PARTY_KEYS[_party_key(_abbreviation)] = _abbreviation
PARTY_KEYS[_party_key("Бие даан нэрээ дэвшүүлэгч")] = None
PARTY_KEYS[_party_key("Бие даан нэр дэвшигч")] = None


def party_2012(text: str) -> str | None:
    """The abbreviation of the party or coalition the book names for a candidate, or None for an independent."""
    try:
        return PARTY_KEYS[_party_key(text)]
    except KeyError:
        raise KeyError(f"2012 candidate party {text!r} is not known") from None


def seats_2012() -> dict[int, int]:
    """The seats of each constituency, from the workbook of constituencies, seats, and voters."""
    out: dict[int, int] = {}
    for row in sheets.sheet_rows(SUMMARY_2012, 0):
        if row[0].isdigit() and len(row) > 3 and row[2].isdigit() and row[3].isdigit():
            out[int(row[2])] = int(row[3])
    return out


def with_parties(contests: list[parliament.Contest]) -> None:
    """Give the candidates of the workbook's constituencies the party the book prints beside the same name."""
    for contest in contests:
        assert contest.number is not None
        named = {_party_key(c.label): c for c in book.read(contest.number).candidates}
        for candidate in contest.candidates:
            found = named.get(_party_key(candidate.label))
            if found is None:
                raise ValueError(f"constituency {contest.number}: {candidate.label} is not in the book")
            candidate.party = party_2012(found.party)


def book_contests() -> list[parliament.Contest]:
    """The constituencies the workbook leaves out (5 to 26), at the finest level the book prints: the soums of a
    province, and the polling sections where it prints them."""
    seats = seats_2012()
    out: list[parliament.Contest] = []
    for number in range(5, 27):
        detail = book.read(number)
        rows: list[polls.Row] = []
        for unit in detail.units:
            extra = {2: unit.registered, 3: unit.voters, 4: unit.valid, 5: unit.invalid}
            if unit.section is not None and number >= 19 and not (number == 20 and unit.label == "Жаргалант"):
                rows.append(
                    polls.Row(
                        f"{number}-{unit.section}",
                        None,
                        f"{number}-р тойрог, {unit.section}-р хэсэг",
                        list(unit.votes),
                        extra,
                        f"2012-{number}",
                    )
                )
            else:
                rows.append(
                    polls.Row(
                        f"soum:{number}:{places.norm(unit.label)}",
                        None,
                        unit.label,
                        list(unit.votes),
                        extra,
                        f"2012-{number}",
                    )
                )
        candidates = [polls.Candidate(c.label, party_2012(c.party)) for c in detail.candidates]
        declared = {
            2: detail.total.registered if detail.total else 0,
            3: detail.total.voters if detail.total else 0,
            4: detail.total.valid if detail.total else 0,
            5: detail.total.invalid if detail.total else 0,
        }
        contest = parliament.Contest(
            number, seats[number], candidates, rows, [c.total for c in detail.candidates], [sheets.url(book.BOOK)]
        )
        contest.declared_extra = declared
        out.append(contest)
    return out


def revote_26() -> parliament.Contest:
    """The repeat vote in constituency 26, which the Commission published by polling section."""
    rows = sheets.sheet_rows(REVOTE_26, 0)
    header = next(r for r in rows if r[0] == "Дүгаар")
    candidates = [polls.candidate(c) for c in header[2:4]]
    out: list[polls.Row] = []
    declared: list[int] = []
    declared_extra: dict[int, int] = {}
    for row in rows:
        if row[0] == "Нийт":
            declared = [sheets.number(c) or 0 for c in row[2:4]]
            declared_extra = {
                5: sheets.number(row[4]) or 0,
                6: sheets.number(row[5]) or 0,
                4: sheets.number(row[6]) or 0,
            }
        elif sheets.is_index(row[0]):
            found = re.search(r"(\d+)-р хороо (\d+)-р хэсэг$", row[1])
            if found is None:
                raise ValueError(f"{REVOTE_26}: unreadable section {row[:2]}")
            out.append(
                polls.Row(
                    f"26-{found[2]}",
                    None,
                    f"26-р тойрог, {found[1]}-р хороо, {found[2]}-р хэсэг",
                    [sheets.number(row[2]), sheets.number(row[3])],
                    {
                        5: sheets.number(row[4]) or 0,
                        6: sheets.number(row[5]) or 0,
                        4: sheets.number(row[6]) or 0,
                    },
                    "2012-26",
                )
            )
    contest = parliament.Contest(26, 1, candidates, out, declared, [sheets.url(REVOTE_26)])
    contest.declared_extra = declared_extra
    for k, total in enumerate(declared):
        if sum(r.votes[k] or 0 for r in out) != total:
            raise ValueError(f"{REVOTE_26}: candidate {k} sections do not sum to the printed total")
    for measure, total in declared_extra.items():
        if sum(r.extra[measure] for r in out) != total:
            raise ValueError(f"{REVOTE_26}: measure {measure} sections do not sum to the printed total")
    return contest
