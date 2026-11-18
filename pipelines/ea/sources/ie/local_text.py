"""Local electoral area tables from the Department's local election results volumes, 1999 to 2024.

Every volume prints one table per local electoral area: a header naming the area and its council, the area's figures
(electorate, invalid ballot papers, valid poll, seats, and quota), the names of the candidates elected, and the count
table, one row per candidate and one column per count. The parser reads the words with their positions, finds the
first count's column from the table header, and takes each candidate's first preference votes from it, so the
running totals of later counts and the transfers printed beside them never mix in.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path

import pymupdf

GAP = 9.0
NUMBER = re.compile(r"^\d{1,3}(?:[,.]\d{3})*$|^\d+$")
DASH = re.compile(r"^[-–—]$")
LEA = re.compile(
    r"^(?:(?:Local\s+Electoral\s+Area\s+of|Former)\s+)?(.+?)(?:\s+|(?<=[a-z]))LEA,?(?:\s*\((?i:continued)\))?$"
)
COUNCIL = re.compile(r"(COUNCIL|CORPORATION)(\s*\([A-Z]+\))?\s*$", re.IGNORECASE)
FIGURES = {
    "electorate": re.compile(r"TOTAL\s+ELECTORATE\s+(\d[\d,.]*)", re.IGNORECASE),
    "invalid": re.compile(r"INVALID\s+BALLOT\s+PAPERS\s+(\d[\d,.]*)", re.IGNORECASE),
    "valid": re.compile(r"(?<!IN)VALID\s+POLL\s+(\d[\d,.]*)", re.IGNORECASE),
    "seats": re.compile(r"NUMBER\s+OF\s+SEATS\s+(\d+)", re.IGNORECASE),
    "quota": re.compile(r"QUOTA\s+(\d[\d,.]*)", re.IGNORECASE),
}
FIGURES_LABEL = re.compile(r"ELECTORATE|BALLOT|POLL|SEATS|QUOTA|ELECTED|ELECTION", re.IGNORECASE)
SIDE = re.compile(r"\((?:NR|SR|N|S|W|E|N\.R\.|S\.R\.)\)$")
NOT_NAMES = re.compile(
    r"^(NAMES?\s+OF|TOTAL|Non[\s-]*transferable|\(|\*\s*Denotes|Number|of\s+Votes|Transfer)", re.IGNORECASE
)


@dataclass
class Word:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Line:
    page: int
    y: float
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def segments(self) -> list[list[Word]]:
        out: list[list[Word]] = []
        for word in self.words:
            if out and word.x0 - out[-1][-1].x1 < GAP:
                out[-1].append(word)
            else:
                out.append([word])
        return out


@dataclass
class Candidate:
    label: str
    party: str | None
    outgoing: bool
    gender: str | None
    votes: int | None = None
    page: int = 0
    box: tuple[float, float, float, float] | None = None


@dataclass
class Table:
    name: str
    council: str
    page: int
    figures: dict[str, int] = field(default_factory=dict)
    candidates: list[Candidate] = field(default_factory=list)
    elected: list[str] = field(default_factory=list)
    total: int | None = None
    exhausted: int | None = None
    pages: set[int] = field(default_factory=set)
    nominated: int | None = None
    uncontested: bool = False


def lines(path: Path) -> list[Line]:
    found: list[Line] = []
    with pymupdf.open(path) as document:
        for page in document:
            words = joined([turn(page, w) for w in page.get_text("words") if str(w[4]).strip()])
            words.sort(key=lambda w: ((w.y0 + w.y1) / 2, w.x0))
            current: list[Word] = []
            for word in words:
                yc = (word.y0 + word.y1) / 2
                if current and yc - (current[0].y0 + current[0].y1) / 2 > 2.5:
                    found.append(Line(page.number + 1, current[0].y0, sorted(current, key=lambda w: w.x0)))
                    current = []
                current.append(word)
            if current:
                found.append(Line(page.number + 1, current[0].y0, sorted(current, key=lambda w: w.x0)))
    return found


def direction(page: pymupdf.Page) -> tuple[int, int]:
    """The direction most of a page's text runs in; the 2009 volume sets its count tables sideways."""
    counts: dict[tuple[int, int], int] = {}
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            key = (round(line["dir"][0]), round(line["dir"][1]))
            counts[key] = counts.get(key, 0) + len(line["spans"])
    return max(counts, key=lambda key: counts[key]) if counts else (1, 0)


_DIRECTIONS: dict[tuple[str, int], tuple[int, int]] = {}


def turn(page: pymupdf.Page, w: tuple[float, float, float, float, str, int, int, int]) -> Word:
    """A word in reading coordinates, so a table set sideways reads like one set upright."""
    number = int(page.number or 0)
    key = (str(getattr(page.parent, "name", "")), number)
    if key not in _DIRECTIONS:
        _DIRECTIONS[key] = direction(page)
    x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], str(w[4])
    width, height = page.rect.width, page.rect.height
    match _DIRECTIONS[key]:
        case (0, -1):
            return Word(number + 1, height - y1, x0, height - y0, x1, text)
        case (0, 1):
            return Word(number + 1, y0, width - x1, y1, width - x0, text)
        case _:
            return Word(number + 1, x0, y0, x1, y1, text)


def joined(words: list[Word]) -> list[Word]:
    """Words with a thousands group the volume set apart, as in "1, 718", put back together."""
    out: list[Word] = []
    for word in sorted(words, key=lambda w: (round((w.y0 + w.y1) / 2), w.x0)):
        last = out[-1] if out else None
        if (
            last is not None
            and re.fullmatch(r"\d{1,3},", last.text)
            and re.fullmatch(r"\d{3}", word.text)
            and abs((last.y0 + last.y1) / 2 - (word.y0 + word.y1) / 2) < 2
            and 0 <= word.x0 - last.x1 < 6
        ):
            out[-1] = Word(last.page, last.x0, last.y0, word.x1, word.y1, last.text + word.text)
            continue
        out.append(word)
    return out


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower().replace("’", "'")) if c.isalpha())


def squash(text: str) -> str:
    return re.sub(r"\W", "", text.lower())


def authority(all_lines: list[Line], index: int, place: list[Word]) -> str:
    """The council printed under an area's header, joined when it runs over two lines."""
    left, right = place[0].x0 - 90, place[-1].x1 + 90
    parts: list[str] = []
    for later in all_lines[index + 1 : index + 9]:
        if later.page != all_lines[index].page:
            break
        for segment in later.segments():
            words = text_of(segment)
            if segment[0].x0 < left or segment[-1].x1 > right or FIGURES_LABEL.search(words) or re.search(r"\d", words):
                continue
            if ELECTED_NAME.match(words) and not COUNCIL.search(words) and not SIDE.search(words):
                continue
            parts.append(words)
            if COUNCIL.search(words):
                return re.sub(r"\s+", " ", " ".join(parts[-2:] if len(words.split()) <= 2 else parts[-1:]))
    return ""


def unlabelled(all_lines: list[Line], index: int) -> str:
    """The name of an area whose header leaves out "LEA": the capitals printed left of its figures."""
    for other in reversed(all_lines[max(0, index - 14) : index]):
        if other.page != all_lines[index].page:
            break
        for segment in other.segments():
            words = text_of(segment)
            if (
                segment[0].x0 < 250
                and re.fullmatch(r"[A-ZÁÉÍÓÚ'’ .-]+", words)
                and not FIGURES_LABEL.search(words)
                and not COUNCIL.search(words)
            ):
                return words.strip()
    return ""


def value(text: str) -> int:
    return int(text.replace(",", "").replace(".", ""))


def text_of(words: list[Word]) -> str:
    return " ".join(w.text for w in words)


def candidate(words: list[Word]) -> Candidate:
    text = re.sub(r"\s+", " ", text_of(words)).strip()
    outgoing = text.startswith("*")
    text = text.lstrip("*").strip()
    gender = None
    hit = re.match(r"^(.*?)\s+([MF])$", text)
    if hit is not None:
        text, gender = hit.group(1), hit.group(2)
    party = None
    hit = re.match(r"^(.*?)\s*\(([^()]*)\)$", text)
    if hit is not None:
        text, party = hit.group(1).strip(), hit.group(2).strip()
    return Candidate(text, party, outgoing, gender)


def tables(path: Path) -> list[Table]:
    found: list[Table] = []
    table: Table | None = None
    column: tuple[float, float] | None = None
    state = "idle"
    current: Candidate | None = None
    all_lines = lines(path)
    for index, line in enumerate(all_lines):
        segments = line.segments()
        joined = line.text
        header = None
        place: list[Word] = []
        for at, segment in enumerate(segments):
            if at and re.fullmatch(r"LEA,?", text_of(segment)):
                segment = segments[at - 1] + segment
            hit = LEA.match(text_of(segment))
            if hit is not None and ":" not in text_of(segment):
                header = hit.group(1).strip()
                place = segment
        if header is not None:
            name = re.sub(r"\s+", " ", header).strip(" ,")
            if table is None or squash(table.name) != squash(name):
                council = authority(all_lines, index, place)
                table = Table(name, council, line.page)
                found.append(table)
                state = "idle"
                column = None
                current = None
        if table is None:
            continue
        table.pages.add(line.page)
        firsts = [w for w in line.words if w.text.lower() == "first"]
        if firsts and state != "rows":
            following = " ".join(other.text for other in all_lines[index : index + 3])
            if re.search(r"\bCount\b", following, re.IGNORECASE):
                if state == "done" and table.total is not None:
                    # the volume left "LEA" out of this area's header, so the previous table ends here
                    table = Table(unlabelled(all_lines, index), table.council, line.page)
                    found.append(table)
                first = firsts[0]
                seconds = [w for w in line.words if w.text.lower() == "second"]
                right = (first.xc + seconds[0].xc) / 2 if seconds else first.xc + 40
                column = (first.xc - (right - first.xc) - 8, right)
                state = "head"
                current = None
                continue
        hit = re.search(r"NAMES\s+OF\s+CANDIDATES\s*\(?\s*(\d+)\s*No", joined, re.IGNORECASE)
        if hit is not None and table.nominated is None:
            table.nominated = int(hit.group(1))
        if re.search(r"declared\s+elected\s+without", joined, re.IGNORECASE):
            table.uncontested = True
        if column is None or state in ("idle", "done"):
            continue
        numbers = [
            w for w in line.words if column[0] <= w.xc <= column[1] and (NUMBER.match(w.text) or DASH.match(w.text))
        ]
        left = [w for w in line.words if w.x1 < column[0]]
        lead = re.sub(r"^\d+\s+(?=TOTAL)", "", text_of(left).strip())
        if re.match(r"^TOTALS?\b", lead, re.IGNORECASE) or (state == "total" and numbers):
            if numbers:
                table.total = 0 if DASH.match(numbers[0].text) else value(numbers[0].text)
                state = "done"
            else:
                state = "total"
            continue
        if state == "total":
            continue
        if re.match(r"^Non[\s-]*transferable", lead, re.IGNORECASE):
            state = "exhausted"
            current = None
            if numbers:
                table.exhausted = 0 if DASH.match(numbers[0].text) else value(numbers[0].text)
            continue
        if state == "exhausted":
            if numbers and table.exhausted is None:
                table.exhausted = 0 if DASH.match(numbers[0].text) else value(numbers[0].text)
            continue
        wrapped = (
            current is not None
            and lead
            and "," not in lead
            and not NOT_NAMES.match(lead)
            and (current.votes is None or current.label.endswith(","))
            and (current.party is None)
        )
        if wrapped and current is not None:
            # a name the volume wraps onto the next line
            joined_label = f"{'* ' if current.outgoing else ''}{current.label} {lead}"
            merged = candidate([Word(line.page, 0, 0, 0, 0, joined_label)])
            current.label, current.party = merged.label, merged.party
            current.gender = current.gender or merged.gender
        elif lead and ("," in lead or lead.endswith(")")) and not NOT_NAMES.match(lead.lstrip("* ")):
            current = candidate(left)
            sex = [w.text for w in line.words if w.text in ("M", "F") and w.x0 > left[-1].x1]
            current.gender = current.gender or (sex[0] if sex else None)
            current.page = line.page
            table.candidates.append(current)
            state = "rows"
        if numbers and current is not None and current.votes is None:
            word = numbers[0]
            if DASH.match(word.text):
                continue
            current.votes = value(word.text)
            current.box = (word.x0, word.y0, word.x1, word.y1)
            current.page = word.page
    for before, table in pairwise(found):
        table.council = table.council or before.council
    blocks = figures(all_lines)
    used: set[int] = set()
    for table in found:
        if not table.candidates:
            continue
        votes = sum(c.votes or 0 for c in table.candidates)
        near = sorted(
            (abs(block.page - table.page), n)
            for n, block in enumerate(blocks)
            if n not in used and block.figures.get("valid") == votes and abs(block.page - table.page) <= 3
        )
        if near:
            used.add(near[0][1])
            table.figures = blocks[near[0][1]].figures
    lists = elected(all_lines)
    taken: set[int] = set()
    for table in found:
        best: tuple[int, int, int] | None = None
        for n, (page, names) in enumerate(lists):
            if n in taken or not 0 <= page - table.page <= 3:
                continue
            hits = sum(1 for name in names if match(name, table.candidates) is not None)
            if hits * 2 >= len(names) and (best is None or (hits, -abs(page - table.page)) > (best[0], -best[1])):
                best = (hits, abs(page - table.page), n)
        if best is not None:
            taken.add(best[2])
            table.elected = lists[best[2]][1]
    for table in found:
        if table.uncontested and not table.candidates:
            # declared elected without a poll: the candidates are the names elected
            block = next((b for b in blocks if b.page == table.page), None)
            table.figures = block.figures if block is not None else {}
            names = next((names for page, names in lists if page == table.page), [])
            for name in names:
                hit = ELECTED_NAME.match(name)
                if hit is None:
                    continue
                words = hit.group(1).lstrip("* ").split()
                label = f"{' '.join(words[1:])}, {words[0]}" if len(words) > 1 else words[0]
                table.candidates.append(Candidate(label, hit.group(2), name.startswith("*"), None))
            table.elected = names
    return [t for t in found if t.candidates]


ELECTED_NAME = re.compile(r"^([^\d]+?)\s*\(([^()]+)\)$")


def elected(all_lines: list[Line]) -> list[tuple[int, list[str]]]:
    """The names under each heading of candidates elected, with the page they are printed on.

    A name usually ends in its party; one printed without a party counts when it starts where the others do.
    """
    found: list[tuple[int, list[str]]] = []
    for index, line in enumerate(all_lines):
        if re.search(r"CANDIDATES\s+ELECTED", line.text, re.IGNORECASE):
            begin = index
        elif LABELS["electorate"].search(line.text) and not any(
            re.search(r"CANDIDATES\s+ELECTED", other.text, re.IGNORECASE) and other.page == line.page
            for other in all_lines[max(0, index - 4) : index + 2]
        ):
            # some areas print the names elected without their heading
            begin = max(0, index - 3)
            while all_lines[begin].page != line.page:
                begin += 1
        else:
            continue
        seen: list[tuple[float, str]] = []
        for other in all_lines[begin : begin + 22]:
            if other.page != line.page or (other is not line and re.search(r"\b(First|Count)\b", other.text)):
                break
            pieces = [
                piece
                for segment in other.segments()
                for piece in re.split(r"(?:(?<=\.\))|(?<=-P\)))\s+(?=[^\W\d_])", text_of(segment))
            ]
            for piece in pieces:
                words = piece
                for pattern in FIGURES.values():
                    words = pattern.sub("", words)
                words = re.sub(r"^.*CANDIDATES\s+ELECTED", "", words, flags=re.IGNORECASE).strip()
                bare = re.sub(r"\([^()]*\)$", "", words)
                if (
                    not words
                    or LEA.match(words)
                    or SIDE.search(words)
                    or re.search(r"COUNCIL|CORPORATION|ELECTION|\d", bare, re.IGNORECASE)
                ):
                    continue
                if not NOT_NAMES.match(words) and all(words != w for _, w in seen):
                    first = words.split()[0]
                    x = next((w.x0 for w in other.words if w.text == first), other.words[0].x0)
                    seen.append((x, words))
        starts = [x for x, words in seen if ELECTED_NAME.match(words)]
        names = [
            words
            for x, words in seen
            if ELECTED_NAME.match(words)
            or (any(abs(x - start) < 6 for start in starts) and re.fullmatch(r"[^\W\d_][\w.'’-]* [\w.'’ -]+", words))
        ]
        found.append((line.page, names))
    return found


def given(label: str) -> tuple[str, str]:
    """A candidate's surname and given names, from the printed `SURNAME, Given` form."""
    surname, _, first = re.sub(r"\([^()]*\)", "", label).partition(",")
    return surname.strip(), first.strip()


def match(name: str, candidates: list[Candidate]) -> Candidate | None:
    """The candidate an elected name refers to: the same name in the other order, else the same surname and initial.

    Two candidates with one name are told apart by the party printed after it.
    """
    hit = ELECTED_NAME.match(name)
    text, party = (hit.group(1), hit.group(2)) if hit is not None else (name, "")
    text = re.sub(r"\([^()]*\)|[“”\"][^“”\"]*[“”\"]", "", text)
    if "," in text:
        surname, _, first = text.partition(",")
        text = f"{first} {surname}"
    target = fold(text)

    def one(found: list[Candidate]) -> Candidate | None:
        if len(found) > 1:
            found = [c for c in found if fold(c.party or "") == fold(party)]
        return found[0] if len(found) == 1 else None

    exact = one([c for c in candidates if fold("".join(reversed(given(c.label)))) == target])
    if exact is not None:
        return exact
    initial = one(
        [
            c
            for c in candidates
            if target.endswith(fold(given(c.label)[0])) and target[:1] == fold(given(c.label)[1])[:1]
        ]
    )
    if initial is not None:
        return initial
    same = [c for c in candidates if target.endswith(fold(given(c.label)[0])) and fold(c.party or "") == fold(party)]
    if len(same) == 1:
        return same[0]
    # a misprint in either list: the one candidate whose name is nearly the same
    scored = sorted(
        (
            (SequenceMatcher(None, target, fold("".join(reversed(given(c.label))))).ratio(), n)
            for n, c in enumerate(candidates)
        ),
        reverse=True,
    )
    if scored and scored[0][0] >= 0.8 and (len(scored) == 1 or scored[1][0] < scored[0][0] - 0.1):
        return candidates[scored[0][1]]
    return None


@dataclass
class Block:
    page: int
    figures: dict[str, int]


LABELS = {
    "electorate": re.compile(r"TOTAL\s+ELECTORATE", re.IGNORECASE),
    "invalid": re.compile(r"INVALID\s+BALLOT", re.IGNORECASE),
    "valid": re.compile(r"(?<!IN)VALID\s+POLL", re.IGNORECASE),
    "seats": re.compile(r"NUMBER\s+OF\s+SEATS", re.IGNORECASE),
    "quota": re.compile(r"QUOTA", re.IGNORECASE),
}


def figures(all_lines: list[Line]) -> list[Block]:
    """Each area's printed figures, read from the lines around its electorate.

    A figure is the number printed after its label, on the label's line or, where the volume sets it a little
    higher or lower, the nearest number to its right.
    """
    found: list[Block] = []
    for index, line in enumerate(all_lines):
        if not LABELS["electorate"].search(line.text):
            continue
        window = [other for other in all_lines[max(0, index - 4) : index + 12] if other.page == line.page]
        words = [w for other in window for w in other.words]
        block = Block(line.page, {})
        for key, label in LABELS.items():
            for other in window:
                hit = label.search(other.text)
                if hit is None:
                    continue
                end = len(other.text[: hit.end()].split())
                last = other.words[end - 1]
                y = (last.y0 + last.y1) / 2
                near = [
                    w
                    for w in words
                    if NUMBER.match(w.text) and abs((w.y0 + w.y1) / 2 - y) <= 7 and last.x1 < w.x0 < last.x1 + 260
                ]
                if near:
                    block.figures[key] = value(min(near, key=lambda w: w.x0).text)
                break
        found.append(block)
    return found
