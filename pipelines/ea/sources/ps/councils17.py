"""Reads the Central Elections Commission's per-council tables of the 2017 local elections.

The Commission prints the 13 May 2017 results as slides of 1,920 by 1,080 points, one council to a page (a long table
runs over to the next page): the registered voters, ballots cast, and seats of the council, then each list's votes and
seats. A second file names the winners with their lists, and a third lists the councils whose list was declared
elected by acclamation (tazkiya). The 29 July 2017 complementary elections have the same three files in A5 and A4
pages with their own columns. The two families embed different fonts. The first maps each two-letter ligature to its
letters reversed, which the text extractor then reverses again (the lam-alef of "الانتخابية" reads "االنتخابية");
`fix_ligatures` writes them back in order. The second is read through `ligature_pdf`.
"""

import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pymupdf

from ea.sources.ps import ligature_pdf

Word = tuple[float, float, float, float, str]


class Box(NamedTuple):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class Cell(NamedTuple):
    text: str
    box: Box | None


class Page(NamedTuple):
    number: int
    words: list[Word]


class Row(NamedTuple):
    list_name: Cell
    candidates: Cell | None
    votes: Cell | None
    seats: Cell | None


class Winner(NamedTuple):
    position: int
    name: Cell
    list_name: Cell | None


class Council(NamedTuple):
    district: str
    authority: str
    first_page: int
    voters: Cell | None
    cast: Cell | None
    seats: Cell | None
    turnout: Cell | None
    rows: list[Row]
    winners: list[Winner]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("ـ", "").replace("‌", "")).strip()


def number(text: str) -> int:
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        raise ValueError(f"no number in {text!r}")
    return int(digits)


def fold(text: str) -> str:
    """A name as the tables spell it, folded so that the same name in two tables compares equal."""
    text = re.sub(r"[ً-ٰٟ]", "", text.replace("ـ", ""))
    text = re.sub(r"[^\w]|[\d_]", "", text)
    for source, target in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        text = text.replace(source, target)
    return text


def fix_ligatures(path: Path) -> pymupdf.Document:
    """Opens a PDF whose text map stores each multi-letter ligature in visual order, so it reads reversed."""
    document = pymupdf.open(path)
    done: set[int] = set()
    for number_ in range(document.page_count):
        for font in document[number_].get_fonts():
            if font[0] in done:
                continue
            done.add(font[0])
            found = re.search(r"/ToUnicode (\d+) 0 R", document.xref_object(font[0]))
            if found is None:
                continue
            ref = int(found.group(1))
            text = document.xref_stream(ref).decode("latin1")

            def reverse(match: re.Match[str]) -> str:
                value = match.group(2)
                units = [value[i : i + 4] for i in range(0, len(value), 4)]
                return f"<{match.group(1)}> <{''.join(reversed(units))}>"

            document.update_stream(ref, re.sub(r"<(....)> <([0-9A-Fa-f]{8,})>", reverse, text).encode("latin1"))
    return document


def read_pages(path: Path, family: str) -> list[Page]:
    """Each page's words with their boxes: `slides` for the May files, `ligature` for the July files."""
    if family == "slides":
        document = fix_ligatures(path)
    else:
        document = ligature_pdf.open_fixed(path)
    out: list[Page] = []
    for index in range(document.page_count):
        words = [
            (w[0], w[1], w[2], w[3], clean(w[4]))
            for w in document[index].get_text("words")
            if clean(w[4]) and not re.fullmatch(r"[ً-ٟ]+", clean(w[4]))
        ]
        out.append(Page(index + 1, words))
    return out


def middle(word: Word) -> float:
    return (word[1] + word[3]) / 2


def cell(page: int, words: list[Word], text: str | None = None) -> Cell:
    joined = text if text is not None else clean(" ".join(w[4] for w in sorted(words, key=lambda w: -w[0])))
    return Cell(
        joined,
        Box(
            page, min(w[0] for w in words), min(w[1] for w in words), max(w[2] for w in words), max(w[3] for w in words)
        ),
    )


def after_colon(words: list[Word], low: float, high: float, left: float = 0) -> list[Word]:
    """The words printed after the colon of a label on the band between two heights."""
    band = [w for w in words if low <= middle(w) < high and w[0] >= left]
    colons = [w for w in band if w[4] == ":"]
    if not colons:
        raise ValueError(f"no label between heights {low} and {high}: {[w[4] for w in band]}")
    colon = max(colons, key=lambda w: w[0])
    return sorted((w for w in band if w[2] <= colon[0] + 1 and w is not colon), key=lambda w: -w[0])


def group_rows(words: list[Word], anchors: list[Word]) -> list[list[Word]]:
    """Groups words under the anchor nearest in height."""
    groups: list[list[Word]] = [[] for _ in anchors]
    for word in words:
        nearest = min(range(len(anchors)), key=lambda i: abs(middle(anchors[i]) - middle(word)))
        groups[nearest].append(word)
    return groups


def is_number(text: str) -> bool:
    return bool(re.fullmatch(r"[\d,]+", text))


# --- the May slides --------------------------------------------------------------------------------------------------


def slide_header(page: Page) -> tuple[str, str]:
    district = clean(" ".join(w[4] for w in after_colon(page.words, 40, 136, 1000)))
    authority = clean(" ".join(w[4] for w in after_colon(page.words, 136, 215, 1000)))
    return district, authority


def slide_figures(page: Page) -> tuple[Cell, Cell, Cell, Cell]:
    """The council's registered voters, ballots cast, turnout, and seats, in the slide's top-left block."""
    block = [w for w in page.words if middle(w) < 215 and w[2] < 1000 and re.fullmatch(r"%?[\d,.]+%?|%", w[4])]
    top = sorted((w for w in block if middle(w) < 135), key=lambda w: -w[0])
    low = sorted((w for w in block if middle(w) >= 135 and w[4] != "%"), key=lambda w: -w[0])
    if len(top) != 2 or len(low) != 2:
        raise ValueError(f"page {page.number}: the header figures are {[w[4] for w in top + low]}")
    voters, seats = (cell(page.number, [w]) for w in top)
    cast, turnout = (cell(page.number, [w]) for w in low)
    return voters, cast, turnout, seats


def slide_results(pages: list[Page]) -> list[Council]:
    """The councils of the May results slides: figures, and each list's votes and seats."""
    out: list[Council] = []
    for page in pages:
        district, authority = slide_header(page)
        voters, cast, turnout, seats = slide_figures(page)
        body = [w for w in page.words if middle(w) > 300 and middle(w) < 960]
        numbers = [w for w in body if is_number(w[4]) and w[2] <= 900]
        votes = sorted((w for w in numbers if w[2] > 500), key=middle)
        names = [w for w in body if w[0] > 900]
        seat_cells = [w for w in numbers if w[2] <= 500]
        if len(votes) != len(seat_cells):
            raise ValueError(f"page {page.number}: {len(votes)} votes against {len(seat_cells)} seat counts")
        rows: list[Row] = []
        for anchor, group in zip(votes, group_rows(names, votes), strict=True):
            if not group:
                raise ValueError(f"page {page.number}: a vote count without a list")
            seat = min(seat_cells, key=lambda w: abs(middle(w) - middle(anchor)))
            rows.append(Row(cell(page.number, group), None, cell(page.number, [anchor]), cell(page.number, [seat])))
        council = Council(district, authority, page.number, voters, cast, seats, turnout, rows, [])
        if out and (out[-1].district, out[-1].authority) == (district, authority):
            out[-1].rows.extend(rows)
        else:
            out.append(council)
    return out


def slide_winners(pages: list[Page]) -> list[Council]:
    """The winners of the May slides, in the order printed, each with the list the Commission names."""
    out: list[Council] = []
    for page in pages:
        district, authority = slide_header(page)
        top = max(middle(w) for w in page.words if w[4] in {"الفائز", "القائمة"} and middle(w) < 300)
        body = [w for w in page.words if middle(w) > top + 20 and middle(w) < 960]
        winner_words = [w for w in body if w[2] <= 1100]
        list_words = [w for w in body if w[2] > 1100]
        rows: dict[int, list[Word]] = defaultdict(list)
        for word in sorted(winner_words + list_words, key=middle):
            for key, parts in rows.items():
                if abs(key - middle(word)) < 20:
                    parts.append(word)
                    break
            else:
                rows[round(middle(word))].append(word)
        winners: list[Winner] = []
        for parts in rows.values():
            names = [w for w in parts if w[2] <= 1100]
            lists = [w for w in parts if w[2] > 1100]
            if not names:
                raise ValueError(f"page {page.number}: a list {[w[4] for w in lists]} without a winner")
            winners.append(Winner(0, cell(page.number, names), cell(page.number, lists) if lists else None))
        council = Council(district, authority, page.number, None, None, None, None, [], winners)
        if out and (out[-1].district, out[-1].authority) == (district, authority):
            out[-1].winners.extend(winners)
        else:
            out.append(council)
    return [c._replace(winners=[w._replace(position=i) for i, w in enumerate(c.winners, 1)]) for c in out]


def slide_acclaimed(pages: list[Page]) -> list[Council]:
    """The May councils declared by acclamation: the authority, its list, and the seats the list won."""
    out: list[Council] = []
    for page in pages:
        district = clean(" ".join(w[4] for w in after_colon(page.words, 100, 215, 1000)))
        top = max(middle(w) for w in page.words if w[4] in {"المقاعد", "القائمة"} and middle(w) < 330)
        body = [w for w in page.words if top + 20 < middle(w) < 940]
        seat_words = [w for w in body if is_number(w[4]) and w[2] < 400]
        for anchor, group in zip(
            sorted(seat_words, key=middle), group_rows(body, sorted(seat_words, key=middle)), strict=True
        ):
            authority_words = [w for w in group if w[2] > 1000]
            list_words = [w for w in group if 400 <= w[0] and w[2] <= 1000 and not is_number(w[4])]
            if not authority_words or not list_words:
                raise ValueError(f"page {page.number}: an acclaimed row without an authority or list")
            out.append(
                Council(
                    district,
                    clean(" ".join(w[4] for w in sorted(authority_words, key=lambda w: -w[0]))),
                    page.number,
                    None,
                    None,
                    cell(page.number, [anchor]),
                    None,
                    [Row(cell(page.number, list_words), None, None, cell(page.number, [anchor]))],
                    [],
                )
            )
    return out


# --- the July pages --------------------------------------------------------------------------------------------------


def july_header(page: Page) -> tuple[str, str]:
    district = clean(" ".join(w[4] for w in after_colon(page.words, 60, 100, 300)))
    authority = clean(" ".join(w[4] for w in after_colon(page.words, 100, 133, 300)))
    return district, authority


def july_results(pages: list[Page]) -> list[Council]:
    """The councils of the complementary elections that voted: figures, then each list's candidates, votes, seats."""
    out: list[Council] = []
    for page in pages:
        district, authority = july_header(page)
        band = [w for w in page.words if 130 <= middle(w) < 170]
        figures = sorted((w for w in band if re.fullmatch(r"%?[\d,.]+%?", w[4]) and w[4] != "%"), key=lambda w: -w[0])
        if len(figures) != 4:
            raise ValueError(f"page {page.number}: the header figures are {[w[4] for w in figures]}")
        voters, cast, turnout, seats = (cell(page.number, [w]) for w in figures)
        top = max(middle(w) for w in page.words if w[4] in {"الأصوات", "المرشحين"} and middle(w) < 230)
        body = [w for w in page.words if middle(w) > top + 12]
        columns = [w for w in body if is_number(w[4]) and w[2] < 260]
        anchors = sorted((w for w in columns if 100 <= w[0] < 190), key=middle)  # the votes column
        rows: list[Row] = []
        for anchor, group in zip(anchors, group_rows(body, anchors), strict=True):
            names = [w for w in group if w[0] >= 260]
            cells = {k: [w for w in group if lo <= w[0] < hi and is_number(w[4])] for k, (lo, hi) in COLUMNS.items()}
            if not names or any(len(v) != 1 for v in cells.values()):
                raise ValueError(f"page {page.number}: a list row reads {[w[4] for w in group]}")
            rows.append(
                Row(
                    cell(page.number, names),
                    cell(page.number, cells["candidates"]),
                    cell(page.number, cells["votes"]),
                    cell(page.number, cells["seats"]),
                )
            )
        council = Council(district, authority, page.number, voters, cast, seats, turnout, rows, [])
        if out and (out[-1].district, out[-1].authority) == (district, authority):
            out[-1].rows.extend(rows)
        else:
            out.append(council)
    return out


COLUMNS = {"candidates": (190, 260), "votes": (100, 190), "seats": (0, 100)}


def july_winners(pages: list[Page]) -> list[Council]:
    """The winners of the complementary elections that voted, with their list and the position the table prints."""
    out: list[Council] = []
    for page in pages:
        district, authority = july_header(page)
        top = max(middle(w) for w in page.words if w[4] in {"الفائز"} and middle(w) < 230)
        body = [w for w in page.words if middle(w) > top + 12 and middle(w) < 800]
        anchors = sorted((w for w in body if re.fullmatch(r"\d+", w[4]) and w[0] >= 530), key=middle)
        winners: list[Winner] = []
        for anchor, group in zip(anchors, group_rows(body, anchors), strict=True):
            names = [w for w in group if 280 <= w[0] < 530]
            lists = [w for w in group if w[2] <= 280]
            if not names or not lists:
                raise ValueError(f"page {page.number}: a winner row reads {[w[4] for w in group]}")
            winners.append(Winner(int(anchor[4]), cell(page.number, names), cell(page.number, lists)))
        council = Council(district, authority, page.number, None, None, None, None, [], winners)
        if out and (out[-1].district, out[-1].authority) == (district, authority):
            out[-1].winners.extend(winners)
        else:
            out.append(council)
    return out


def july_acclaimed(pages: list[Page]) -> list[Council]:
    """The complementary councils declared by acclamation: the list, its seats, and the winners with positions."""
    out: list[Council] = []
    for page in pages:
        district = clean(" ".join(w[4] for w in after_colon(page.words, 60, 100, 300)))
        authority = clean(" ".join(w[4] for w in after_colon(page.words, 100, 130, 250)))
        list_name = after_colon(page.words, 130, 165, 250)
        seat_label = [w for w in page.words if 130 <= middle(w) < 165 and w[2] <= 200]
        seats_words = [w for w in seat_label if re.fullmatch(r"\d+", w[4])]
        if not seats_words:
            raise ValueError(f"page {page.number}: no seat count")
        name_words = [w for w in list_name if w[0] >= 200]
        top = max(middle(w) for w in page.words if w[4] == "الفائز" and middle(w) < 230)
        body = [w for w in page.words if middle(w) > top + 12 and middle(w) < 800]
        anchors = sorted((w for w in body if re.fullmatch(r"\d+", w[4]) and w[0] >= 500), key=middle)
        winners = [
            Winner(int(a[4]), cell(page.number, [w for w in g if w[0] < 500]), None)
            for a, g in zip(anchors, group_rows(body, anchors), strict=True)
        ]
        row = Row(cell(page.number, name_words), None, None, cell(page.number, seats_words))
        council = Council(
            district, authority, page.number, None, None, cell(page.number, seats_words), None, [row], winners
        )
        if out and (out[-1].district, out[-1].authority) == (district, authority):
            out[-1].winners.extend(winners)
        else:
            out.append(council)
    return out


READERS: dict[str, Callable[[list[Page]], list[Council]]] = {
    "may-results": slide_results,
    "may-winners": slide_winners,
    "may-acclaimed": slide_acclaimed,
    "july-results": july_results,
    "july-winners": july_winners,
    "july-acclaimed": july_acclaimed,
}


# --- the final report's table of lists ---------------------------------------------------------------------------------


REPORT_TABLE_PAGE = 77
TAZKIYA = "تزكية"


class Nomination(NamedTuple):
    """One list of the final report's table: the council, the list's name, and the nomination type printed beside it."""

    council: Cell
    list_name: Cell
    nomination: Cell
    votes: str
    seats: int
    page: int


def report_nominations(document: pymupdf.Document) -> list[Nomination]:
    """The lists of the report's table of results by council (from its page 77), each with its nomination type.

    A page prints a row for each list: the council and its seats at the right (once for a council, centered on its
    lists), then the list's name, its nomination type, its votes or "tazkiya", and its seats.
    """
    out: list[Nomination] = []
    started = False
    for index in range(REPORT_TABLE_PAGE - 1, document.page_count):
        text = document[index].get_text()
        if "صفة" not in text or "الترشح" not in text:
            if started:
                break
            continue
        started = True
        words = [
            (w[0], w[1], w[2], w[3], clean(w[4]))
            for w in document[index].get_text("words")
            if clean(w[4]) and 95 < (w[1] + w[3]) / 2 < 735
        ]
        anchors = sorted((w for w in words if re.fullmatch(r"\d+", w[4]) and w[0] >= 70 and w[2] <= 95), key=middle)
        names = sorted((w for w in words if w[0] >= 385 and w[2] < 480), key=middle)
        clusters: list[list[Word]] = []
        for word in names:
            if clusters and middle(word) - middle(clusters[-1][-1]) < 12:
                clusters[-1].append(word)
            else:
                clusters.append([word])
        centers = [sum(middle(w) for w in c) / len(c) for c in clusters]
        for anchor, group in zip(anchors, group_rows(words, anchors), strict=True):
            council = min(range(len(clusters)), key=lambda i: abs(centers[i] - middle(anchor)))
            list_words = [w for w in group if 225 <= w[0] < 350]
            type_words = [w for w in group if 150 <= w[0] < 215]
            if not list_words or not type_words:
                raise ValueError(f"page {index + 1}: a list row reads {[w[4] for w in group]}")
            vote_words = [w for w in group if 100 <= w[0] < 150]
            out.append(
                Nomination(
                    cell(index + 1, clusters[council]),
                    cell(index + 1, list_words, line_text(list_words)),
                    cell(index + 1, type_words, line_text(type_words)),
                    line_text(vote_words),
                    int(anchor[4]),
                    index + 1,
                )
            )
    return out


def line_text(words: list[Word]) -> str:
    """The words of a cell in reading order: top line first, each line from the right."""
    return clean(" ".join(w[4] for w in sorted(words, key=lambda w: (round(middle(w) / 6), -w[0]))))
