"""Read the constituency tables of the 2074 (2017) House of Representatives result book."""

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from ea.sources.np import glyphs
from ea.sources.np.glyphs import Word

DEVA = str.maketrans("०१२३४५६७८९", "0123456789")
GENDERS = {"पुरुष": "male", "महिला": "female", "अन्य": "other"}
INT = re.compile(r"^\d[\d,]*$")
PCT = re.compile(r"^\d+\.\d+$")


def digits(text: str) -> str:
    return text.translate(DEVA)


@dataclass
class Row:
    serial: int
    name: str
    age: int | None
    gender: str
    party: str
    votes: int | None
    elected: bool
    page: int
    gender_x: float = 0.0


@dataclass
class Block:
    district: str
    number: int
    registered: int
    cast: int
    valid: int
    invalid: int
    page: int
    rows: list[Row] = field(default_factory=list)


def _join(words: list[Word]) -> str:
    words = sorted(words, key=lambda w: (round(w.y / 4), w.x0))
    text = " ".join(w.text for w in words)
    return re.sub(r"\s*\(\s*", " (", re.sub(r"\s*\)", ")", text)).strip().replace("( ", "(")


def _band_row(words: list[Word], page: int) -> Row | None:
    ws = sorted(words, key=lambda w: (round(w.y / 4), w.x0))
    genders = [w for w in ws if w.text in GENDERS]
    if not genders:
        return None
    gender = max(genders, key=lambda w: w.x0)  # a given name can read as a gender word
    left = min(w.x0 for w in ws)
    serial = next((w for w in ws if abs(w.x0 - left) < 6 and digits(w.text).isdigit() and w.x1 < gender.x0 - 60), None)
    numbers = sorted(
        (w for w in ws if w.x0 > gender.x1 and (INT.match(digits(w.text)) or PCT.match(digits(w.text)))),
        key=lambda w: w.x0,
    )
    if not numbers:
        return None
    votes = numbers[0]
    if PCT.match(digits(votes.text)) and len(numbers) > 1 and INT.match(digits(numbers[1].text)):
        votes = numbers[1]  # the book prints the percentage where the votes belong
    ages = [
        w for w in ws if w.x1 <= gender.x0 + 1 and digits(w.text).isdigit() and w is not serial and w.x0 > left + 20
    ]
    age = max(ages, key=lambda w: w.x0) if ages else None
    name_end = (age.x0 if age else gender.x0) - 1
    name_start = serial.x1 + 3 if serial else left - 1
    name = _join([w for w in ws if w.x0 > name_start and w.x0 < name_end])
    party = _join([w for w in ws if w.x0 > gender.x1 + 2 and w.x0 < numbers[0].x0 - 3])
    elected = any("निर्वाचित" in w.text for w in ws)
    return Row(
        int(digits(serial.text)) if serial else 0,
        name,
        int(digits(age.text)) if age else None,
        GENDERS[gender.text],
        party,
        int(digits(votes.text).replace(",", "")) if INT.match(digits(votes.text)) else 0,
        elected,
        page,
        gender.x0,
    )


def _continue(row: Row, band: list[Word]) -> None:
    """Add the lines of a cell that ran over onto the next page to the row they belong to."""
    ws = sorted(band, key=lambda w: (round(w.y / 4), w.x0))
    if any(re.search(r"राजनीतिक|उम्मेदवा|संख्या|निर्वाचन|प्रतिनिधि|सदस्य|क्षेत्र|कैफियत|जम्मा", w.text) for w in ws):
        return  # a caption or a header, not the rest of a cell
    name = [w for w in ws if w.x0 < row.gender_x - 10 and not digits(w.text).isdigit()]
    party = [w for w in ws if w.x0 > row.gender_x + 20 and not INT.match(digits(w.text))]
    if name:
        row.name = _join([Word(row.name, 0, 0, 0, 0), *name]) if row.name else _join(name)
    if party:
        row.party = _join([Word(row.party, 0, 0, 0, 0), *party]) if row.party else _join(party)


HEADER = re.compile(r"(?:जि)?ल्ला\s*:?\s*(?P<d>.+?)\s*निर्वाचन\s*क्षेत्र\s*नं\s*\.?\s*(?P<n>[\d०-९]+)")
TOTAL = re.compile(r"(?:जम्मा|कुल)\s*मतदाता\s*:?\s*(?P<reg>[\d०-९,]+)")


def blocks(path: Path, first: int, last: int) -> list[Block]:
    doc = pymupdf.open(path)
    out: list[Block] = []
    cur: Block | None = None
    for pno in range(first, last + 1):
        page = doc[pno - 1]
        ws = glyphs.words(page)
        rules, _ = glyphs.grid(page)
        heads = sorted((w for w in ws if re.match(r"(जि)?ल्ला\s*:?$", w.text)), key=lambda w: w.y)
        events: list[tuple[float, str, list[Word]]] = []
        for h in heads:
            near = [w for w in ws if h.y - 3 <= w.y <= h.y + 30]
            events.append((h.y, "head", near))
        edges = [0.0, *rules, 1e9] if rules else []
        for a, b in itertools.pairwise(edges):
            events.append((a, "band", [w for w in ws if a - 1 <= w.y < b - 1]))
        events.sort(key=lambda e: (e[0], 0 if e[1] == "head" else 1))
        started = False
        top = min((e[0] for e in events if e[1] == "head"), default=1e9) - 4  # a header's own lines are not a cell
        for _, kind, payload in events:
            if kind == "head":
                near = payload
                text = digits(_join(sorted(near, key=lambda w: (round(w.y / 4), w.x0))))
                text = re.sub(r"\s+", " ", text)
                m = HEADER.search(text)
                nums = re.findall(
                    r"(?:जम्मा|कुल) मतदाता\s*:\s*([\d ]+?)\s*खसेको मत\s*:\s*([\d ]+?)\s*सदर मत\s*:\s*([\d ]+?)\s*बदर मत\s*:\s*([\d ]+)",
                    text,
                )
                if not m or not nums:
                    raise ValueError(f"page {pno}: header not read: {text!r}")
                reg, cast, valid, invalid = (int(x.replace(" ", "")) for x in nums[0])
                cur = Block(m["d"].strip(), int(m["n"]), reg, cast, valid, invalid, pno)
                out.append(cur)
            else:
                row = _band_row(payload, pno)
                if row and cur is not None:
                    cur.rows.append(row)
                elif payload and cur is not None and cur.rows and not started:
                    _continue(cur.rows[-1], [w for w in payload if w.y < top])
            started = started or bool(payload)
    return out


@dataclass
class PABlock:
    province: int
    district: str
    hor: int
    number: int
    registered: int
    cast: int
    valid: int
    invalid: int
    page: int
    rows: list[Row] = field(default_factory=list)


def _pa_row(words: list[Word], page: int) -> Row | None:
    ws = sorted(words, key=lambda w: (round(w.y / 4), w.x0))
    genders = [w for w in ws if w.text in GENDERS]
    if not genders:
        return None
    # the gender column is the left-most gender word right of the name column
    left = min(w.x0 for w in ws)
    gender = max(genders, key=lambda w: w.x0)
    serial = next((w for w in ws if abs(w.x0 - left) < 6 and digits(w.text).isdigit() and w.x1 < gender.x0 - 60), None)
    numbers = sorted(
        (w for w in ws if w.x0 > gender.x1 and INT.match(digits(w.text))),
        key=lambda w: w.x0,
    )
    ages = [w for w in numbers if w.x0 < gender.x0 + 100]
    votes_ = [w for w in numbers if w.x0 >= gender.x0 + 100]
    if not votes_ and not ages:
        return None
    age = ages[0] if ages else None
    votes = votes_[0] if votes_ else None  # the book leaves a cell empty
    name_start = serial.x1 + 3 if serial else left - 1
    name = _join([w for w in ws if w.x0 > name_start and w.x0 < gender.x0 - 1])
    party = _join(
        [
            w
            for w in ws
            if w.x0 > gender.x1 + 20 and (votes is None or w.x0 < votes.x0 - 3) and not INT.match(digits(w.text))
        ]
    )
    elected = any("निर्वाचित" in w.text for w in ws)
    return Row(
        int(digits(serial.text)) if serial else 0,
        name,
        int(digits(age.text)) if age else None,
        GENDERS[gender.text],
        party,
        int(digits(votes.text).replace(",", "")) if votes else None,
        elected,
        page,
        gender.x0,
    )


PA_PROVINCE = re.compile(r"प्रदेश\s*[:ःM]?\s*(\d+)")
PA_DISTRICT = re.compile(r"(?:जिल्ला|ल्ला)\s*[:ःM]?\s*(.+?)(?=\s*(?:प्रतिनिधि|प्रदेश|क्षेत्र|नं\b|कूल)|$)")
PA_NUMBERS = re.compile(r"नं\s*\.?\s*(\d+)")


def _figure(text: str, label: str) -> int | None:
    m = re.search(label + r"\s*[:M]?\s*(\d[\d ]*?)(?=\s*(?:[^\d\s]|$))", text)
    return int(m[1].replace(" ", "")) if m else None


def pa_blocks(path: Path, first: int, last: int) -> list[PABlock]:
    doc = pymupdf.open(path)
    out: list[PABlock] = []
    cur: PABlock | None = None
    last_head = -10
    for pno in range(first, last + 1):
        page = doc[pno - 1]
        ws = glyphs.words(page)
        rules, _ = glyphs.grid(page)
        events: list[tuple[float, str, list[Word]]] = []
        for w in ws:
            if w.x0 < 200 and (
                re.match(r"^(?:कूल|कुल)मतदाता", w.text)
                or (
                    w.text in ("कूल", "कुल")
                    and any(v.text.startswith("मतदाता") and abs(v.y - w.y) < 2 and 0 < v.x0 - w.x0 < 30 for v in ws)
                )
            ):
                near = [v for v in ws if w.y - 36 <= v.y <= w.y + 3 or (w.y + 3 < v.y <= w.y + 14 and v.x0 > 400)]
                events.append((w.y - 36, "head", near))
        edges = [0.0, *rules, 1e9] if rules else []
        for a, b in itertools.pairwise(edges):
            events.append((a, "band", [w for w in ws if a - 1 <= w.y < b - 1]))
        events.sort(key=lambda e: (e[0], 0 if e[1] == "head" else 1))
        if any(e[1] == "head" for e in events):
            last_head = pno
        elif pno > last_head + 1:
            cur = None  # a page of lists or summaries, not a continuation of the last table
        started = False
        top = min((e[0] for e in events if e[1] == "head"), default=1e9) - 4  # a header's own lines are not a cell
        for _, kind, payload in events:
            if kind == "head":
                near = payload
                text = re.sub(r"\s+", " ", digits(_join(sorted(near, key=lambda w: (round(w.y / 4), w.x0)))))
                text = re.sub(r"(?:प्रतिनिधि|प्रदेश) सभा सदस्य निर्वाचन", " ", text)
                pm, dm = PA_PROVINCE.search(text), PA_DISTRICT.search(text)
                if pm and dm and dm[1].strip():
                    m, d = pm[1], dm[1].strip()
                else:
                    seg = re.sub(r"प्रदेश|जिल्ला|ल्ला|[:ःM]", " ", text.split("क्षेत्र")[0]).split()
                    lone = [t for t in seg if re.fullmatch(r"\d", t)]
                    m = lone[0] if lone else None
                    d = " ".join(t for t in seg if t != m) or None
                n = PA_NUMBERS.findall(text)
                if not m or not d or len(n) != 2:
                    raise ValueError(f"page {pno}: header not read: {text!r}")
                reg = _figure(text, r"मतदाता")
                cast = _figure(text, r"खसेको\s*मत")
                valid = _figure(text, r"सदर\s*मत")
                invalid = _figure(text, r"बदर\s*मत")
                if reg is None or cast is None or (valid is None and invalid is None):
                    raise ValueError(f"page {pno}: totals not read: {text!r}")
                if valid is None:
                    valid = cast - (invalid or 0)  # the book leaves the line out
                if invalid is None:
                    invalid = cast - valid
                cur = PABlock(int(m), d, int(n[0]), int(n[1]), reg, cast, valid, invalid, pno)
                out.append(cur)
            else:
                row = _pa_row(payload, pno)
                if row and cur is not None and row.serial in (0, len(cur.rows) + 1):
                    cur.rows.append(row)
                elif payload and cur is not None and cur.rows and not started:
                    _continue(cur.rows[-1], [w for w in payload if w.y < top])
            started = started or bool(payload)
    return out


@dataclass
class ListRow:
    serial: int
    name: str
    numbers: list[int]
    page: int


def list_rows(doc: pymupdf.Document, pno: int) -> list[ListRow]:
    """Rows of a party-list table: a serial, the party name (which may wrap), and the integers that follow."""
    page = doc[pno - 1]
    ws = glyphs.words(page)
    rules, _ = glyphs.grid(page)
    edges = [0.0, *rules, 1e9] if rules else []
    out: list[ListRow] = []
    for a, b in itertools.pairwise(edges):
        band = sorted((w for w in ws if a - 1 <= w.y < b - 1), key=lambda w: (round(w.y / 4), w.x0))
        if not band:
            continue
        left = min(w.x0 for w in band)
        first = next((w for w in band if w.x0 < left + 12 and re.fullmatch(r"[\d०-९]+\.?", w.text)), None)
        m = re.fullmatch(r"([\d०-९]+)\.?", first.text) if first else None
        if not m:
            continue
        rest = [w for w in band if w is not first]
        numbers = [w for w in rest if INT.match(digits(w.text))]
        text = _join([w for w in rest if not INT.match(digits(w.text)) and not PCT.match(digits(w.text))])
        out.append(ListRow(int(digits(m[1])), text, [int(digits(w.text).replace(",", "")) for w in numbers], pno))
    return out


def party_list(doc: pymupdf.Document, start: int, pages: int = 3) -> list[ListRow]:
    """The first run of consecutively numbered rows from page `start`: the table of party votes."""
    out: list[ListRow] = []
    for pno in range(start, start + pages):
        for row in list_rows(doc, pno):
            if row.serial == len(out) + 1 and row.numbers:
                out.append(row)
            elif out and row.serial == 1:
                return out
    return out


def phases(doc: pymupdf.Document, first: int = 55, last: int = 79) -> dict[tuple[str, int], int]:
    """The voting phase of each House constituency, from the book's table of voters by constituency (set on its side)."""
    out: dict[tuple[str, int], int] = {}
    for pno in range(first, last + 1):
        page = doc[pno - 1]
        if page.rotation != 90:
            continue
        strips: list[tuple[float, list[Word]]] = []
        for w in sorted(glyphs.words(page), key=lambda w: w.x0):
            if strips and abs(w.x0 - strips[-1][0]) < 3.5:
                strips[-1][1].append(w)
            else:
                strips.append((w.x0, [w]))
        for at, (_, sw) in enumerate(strips):
            number = [w for w in sw if 530 < w.y < 545]
            phase = [w for w in sw if 664 < w.y < 676]
            district = [w for w in sw if 545 < w.y < 660 and not digits(w.text).isdigit() and "म्मा" not in w.text]
            nxt = strips[at + 1][1] if at + 1 < len(strips) else []
            if number and phase and not any(530 < w.y < 545 for w in nxt):
                district += [w for w in nxt if 545 < w.y < 660 and "म्मा" not in w.text]  # a wrapped name
            if district and number and phase:
                out[(" ".join(w.text for w in sorted(district, key=lambda w: w.y)), int(digits(number[0].text)))] = int(
                    digits(phase[0].text)
                )
    return out


@dataclass
class Winner:
    district: str
    number: int
    name: str
    votes: int


def winners(doc: pymupdf.Document, first: int = 54, last: int = 58) -> list[Winner]:
    """The elected members the book lists by party: province, district, constituency, name, age, gender, votes."""
    out: list[Winner] = []
    for pno in range(first, last + 1):
        for _, ws in glyphs.lines(doc[pno - 1]):
            t = [w.text for w in ws]
            if len(t) < 7 or not t[0].isdigit() or not t[1].isdigit() or t[-2] not in GENDERS:
                continue
            at = next((i for i in range(2, len(t)) if t[i].isdigit()), None)
            if at is None or at < 3 or not INT.match(t[-1]):
                continue
            out.append(Winner(" ".join(t[2:at]), int(t[at]), " ".join(t[at + 1 : -3]), int(t[-1].replace(",", ""))))
    return out


@dataclass
class Listing:
    rows: list[ListRow]
    total: int
    seats: dict[int, int]


def national_list(doc: pymupdf.Document, first: int = 282, last: int = 283) -> Listing:
    """The national party-list votes, the printed total, and the seats of the parties above the threshold."""
    rows: list[ListRow] = []
    seats: dict[int, int] = {}
    total = 0
    for pno in range(first, last + 1):
        page = doc[pno - 1]
        for row in list_rows(doc, pno):
            if row.serial == len(rows) + 1 and len(row.numbers) == 1 and not seats:
                rows.append(row)
        for _, ws in glyphs.lines(page):
            t = [w.text for w in ws]
            if t and t[0] == "जम्मा" and not total and len(t) > 1 and INT.match(t[1]):
                total = int(t[1])
    # the seat table repeats the leading parties with their seats
    threshold = False
    for row in list_rows(doc, last):
        if row.serial == 1 and len(row.numbers) >= 2 and not threshold:
            threshold = True
        if threshold and len(row.numbers) >= 2:
            seats[row.numbers[0]] = row.numbers[1]
    return Listing(rows, total, seats)


def list_total(doc: pymupdf.Document, pno: int) -> int:
    """The valid votes a party-list table prints above its rows."""
    for _, ws in glyphs.lines(doc[pno - 1]):
        text = " ".join(w.text for w in ws)
        if "सदर" in text and "मत" in text:
            after = [w for w in ws if w.x0 > next(v.x0 for v in ws if v.text.startswith("मत"))]
            nums = [digits(w.text) for w in after if INT.match(digits(w.text))]
            if nums:
                return int("".join(nums).replace(",", ""))  # the figure may be set in pieces
    raise ValueError(f"page {pno}: no printed total of valid votes")
