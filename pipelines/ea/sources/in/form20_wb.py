"""India's Lok Sabha elections by polling station, from the Form 20 final result sheets of 2024 Assam and 2019
Karnataka, read from archived copies of the Chief Electoral Officers' sites.

After the count, the Assistant Returning Officer of each assembly segment of a parliamentary constituency signs a
Form 20 ("Final Result Sheet", rule 56C of the Conduct of Election Rules, 1961): one row per polling station with the
votes each candidate polled on the voting machines, the total of valid votes, the rejected votes, the votes for none of
the above (NOTA), the total, and the tendered votes, and the segment's electors in its heading. Postal ballots are
counted for the whole constituency and are not on the segment sheets; a constituency's Part II sheet adds them.

The Chief Electoral Officers of Assam (ceoassam.nic.in) and Karnataka (ceo.karnataka.gov.in) published the sheets as
PDFs with a text layer. Both sites refuse scripted requests now, so the files are copies saved from web.archive.org
captures of the original URLs, and each is cited under its original URL. The sheets' words are read with their
positions: the columns come from the numbers of the data rows, and each heading word goes to the nearest column.

Each constituency is a contest of a Lok Sabha general election. Its result set holds the polling stations as reporting
units, numbered as Form 20 numbers them and nested in a unit for their assembly segment, which carries the segment's
electors. The sheets name the candidates but not their parties, and the declared winner is on Form 21, not on Form 20,
so the set records no parties and no outcomes.
"""

import functools
import hashlib
import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from uuid import uuid5

import polars as pl
import pymupdf

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "in.form20_wb"
INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fIND"))
INBOX = DATA / "inbox" / SOURCE
ASSAM_DIR = "https://ceoassam.nic.in/lok_sabha/2024/Form20/"
CDX = (
    "https://web.archive.org/cdx/search/cdx?url=ceoassam.nic.in/lok_sabha/2024/Form20/&matchType=prefix"
    "&filter=statuscode:200&filter=mimetype:application/pdf&collapse=urlkey&fl=timestamp,original"
)
KARNATAKA_DIR = "http://ceo.karnataka.gov.in:80/form20_loksabhaelection2019/PDFS/"
PC_SCHEME = "in.pc"
AC_SCHEME = "in.ac"
STATION_SCHEME = "in.ps"
VOTES, REGISTERED, BALLOTS, INVALID = 1, 2, 3, 5
ALL, ELECTION_DAY, POSTAL = 0, 1, 3


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


BODY = key("body", "lok-sabha")
FPTP = key("electoral_system", "fptp")
ASSAM_SOURCE = key("source", "AS")
KARNATAKA_SOURCE = key("source", "KA")


@dataclass(frozen=True)
class Constituency:
    number: int
    name: str
    reserved: str | None
    segments: tuple[int, ...]


@dataclass(frozen=True)
class Set:
    """One state's Form 20 sheets for one Lok Sabha general election."""

    state: str
    state_name: str
    year: int
    delimitation: date
    source: str
    publisher: str
    site: str
    constituencies: tuple[Constituency, ...]
    segments: int


# Assam's 14 constituencies under the Delimitation Commission's order of 11 August 2023, with their assembly segments.
# The segments of a constituency are the ones whose sheets list the same candidates, and each group is named by the
# constituency whose candidate list it holds; Karimganj's number and segments are also printed on its Part II sheet.
# The capture of segment 71 is missing, so it belongs to no group here; it lies in Nagaon or Sonitpur.
ASSAM_PCS = (
    Constituency(1, "Kokrajhar", "ST", (1, 2, 3, 4, 5, 19, 20, 41, 42)),
    Constituency(2, "Dhubri", None, (6, 7, 8, 9, 10, 11, 12, 14, 17, 22, 23)),
    Constituency(3, "Barpeta", None, (16, 18, 21, 24, 25, 26, 30, 38, 39, 40)),
    Constituency(4, "Darrang-Udalguri", None, (31, 32, 43, 44, 45, 46, 47, 48, 49, 50, 51)),
    Constituency(5, "Guwahati", None, (13, 15, 27, 28, 29, 33, 34, 35, 36, 37)),
    Constituency(6, "Diphu", "ST", (108, 109, 110, 111, 112, 113)),
    Constituency(7, "Karimganj", None, (121, 122, 123, 124, 125, 126)),
    Constituency(8, "Silchar", "SC", (114, 115, 116, 117, 118, 119, 120)),
    Constituency(9, "Nagaon", None, (52, 53, 54, 55, 56, 58, 60, 61)),
    Constituency(10, "Kaziranga", None, (57, 59, 62, 63, 64, 103, 104, 105, 106, 107)),
    Constituency(11, "Sonitpur", None, (65, 66, 67, 68, 69, 70, 72, 73)),
    Constituency(12, "Lakhimpur", None, (74, 75, 76, 77, 78, 79, 80, 81, 82)),
    Constituency(13, "Dibrugarh", None, (83, 84, 85, 86, 87, 88, 89, 90, 91, 92)),
    Constituency(14, "Jorhat", None, (93, 94, 95, 96, 97, 98, 99, 100, 101, 102)),
)
# A candidate each group's sheets must list, to tie the group to its constituency.
ASSAM_NAMED = {
    1: "JOYANTA BASUMATARY",
    2: "RAKIBUL HUSSAIN",
    3: "PHANI BHUSAN CHOUDHURY",
    4: "DILIP SAIKIA",
    5: "BIJULI KALITA MEDHI",
    6: "AMARSING TISSO",
    7: "KRIPANATH MALLAH",
    8: "PARIMAL SUKLABAIDYA",
    9: "PRADYUT BORDOLOI",
    10: "KAMAKHYA PRASAD TASA",
    11: "RANJIT DUTTA",
    12: "PRADAN BARUAH",
    13: "SARBANANDA SONOWAL",
    14: "GAURAV GOGOI",
}
# Segment 71's sheet is missing, and it lies in one of these, so neither is complete.
ASSAM_UNPLACED = {71: (9, 11)}

SETS = {
    "AS": Set(
        "AS",
        "Assam",
        2024,
        date(2023, 8, 11),
        ASSAM_SOURCE,
        "Chief Electoral Officer, Assam",
        "https://ceoassam.nic.in/",
        ASSAM_PCS,
        126,
    ),
    "KA": Set(
        "KA",
        "Karnataka",
        2019,
        date(2008, 2, 19),
        KARNATAKA_SOURCE,
        "Chief Electoral Officer, Karnataka",
        "https://ceo.karnataka.gov.in/",
        (),
        224,
    ),
}
GENERAL = {
    2019: ("2019 Indian general election", "17th", date(2019, 4, 11), date(2019, 5, 19)),
    2024: ("2024 Indian general election", "18th", date(2024, 4, 19), date(2024, 6, 1)),
}


# ---- the raw files -------------------------------------------------------------------------------------------------


def _originals(listing: bytes) -> dict[str, str]:
    """The original URL of each archived Assam sheet, by its file name, from the archive's capture listing."""
    found: dict[str, str] = {}
    for line in listing.decode().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lower().endswith(".pdf"):
            name = parts[1].rsplit("/", 1)[1].replace("%20", " ")
            found[name] = parts[1]
    return found


def fetch() -> None:
    """Store the saved copies of the sheets under their original URLs, refusing any whose hash differs."""
    index = json.loads((INBOX / "index.json").read_text())
    originals: dict[str, str] = {}
    for url in sorted(index, key=lambda u: u != CDX):
        entry = index[url]
        content = (INBOX / "files" / entry["file"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"{url}: the saved file's hash differs from {entry['sha256']}")
        if url == CDX:
            originals = _originals(content)
        target = entry.get("source", url)
        if target.startswith("browser://"):
            target = originals[target.rsplit("/", 1)[1]]
        elif url != CDX and not content.startswith(b"%PDF"):
            raise ValueError(f"{url} is not a PDF")
        raw.store(
            SOURCE,
            target,
            content,
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


def files(state: str) -> dict[int, str]:
    """The URL of each segment's sheet in the raw cache, by assembly segment number."""
    found: dict[int, str] = {}
    for url in raw.keys(SOURCE):
        if state == "AS" and url.startswith(ASSAM_DIR) and "Part_II" not in url:
            found[int(url.rsplit("/", 1)[1].split("-", 1)[0])] = url
        elif state == "KA" and url.startswith(KARNATAKA_DIR):
            found[int(url.rsplit("/", 1)[1].removesuffix(".pdf"))] = url
    return dict(sorted(found.items()))


def part_two(state: str) -> dict[int, str]:
    """The URL of each constituency's Part II sheet in the raw cache, by constituency number."""
    found: dict[int, str] = {}
    for url in raw.keys(SOURCE):
        m = re.search(r"/(\d+)-[^/]*_Form20_Part_II\.pdf$", url)
        if state == "AS" and url.startswith(ASSAM_DIR) and m:
            found[int(m.group(1))] = url
    return found


# ---- reading a sheet -----------------------------------------------------------------------------------------------

Word = tuple[float, float, float, float, str]


@dataclass
class Row:
    serial: int
    station: str
    votes: list[int]
    valid: int
    rejected: int | None
    nota: int
    total: int
    tendered: int | None


@dataclass
class Sheet:
    url: str
    pages: int
    segment: int
    segment_name: str
    electors: int
    constituency: int | None
    constituency_name: str | None
    heads: list[list[list[Word]]]  # each candidate's heading, as lines of words
    width: list[float]  # each candidate column's width
    rows: list[Row] = field(default_factory=list)
    printed: list[int | None] | None = None  # the printed total row: candidates, valid, rejected, NOTA, total


NUMBER = re.compile(r"\d+")
STATION = re.compile(r"\d+[A-Z]?")
SCIENTIFIC = re.compile(r"\d(\.\d+)?E\+\d+|#+")  # a total the sheet's spreadsheet printed too wide for its cell


def _lines(words: list[Word], tolerance: float = 2.5) -> list[list[Word]]:
    grouped: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w[1], w[0])):
        if grouped and abs(grouped[-1][0][1] - w[1]) <= tolerance:
            grouped[-1].append(w)
        else:
            grouped.append([w])
    return [sorted(line, key=lambda w: w[0]) for line in grouped]


def _words(page: pymupdf.Page) -> list[Word]:
    """A page's words, without its 'Page n' footer, which can share a line with the last row."""
    words: list[Word] = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]
    drop: set[int] = set()
    for i, w in enumerate(words):
        if w[4] == "Page":
            drop.add(i)
            after = [j for j, v in enumerate(words) if abs(v[1] - w[1]) < 2.5 and 0 <= v[0] - w[2] < 8]
            drop |= set(after[:1])
            if after and (rest := [j for j, v in enumerate(words) if abs(v[1] - w[1]) < 2.5 and v[4] == "of"]):
                drop |= {
                    rest[0],
                    *[j for j, v in enumerate(words) if abs(v[1] - w[1]) < 2.5 and 0 <= v[0] - words[rest[0]][2] < 8],
                }
    return [w for i, w in enumerate(words) if i not in drop]


def _centre(w: Word) -> float:
    return (w[0] + w[2]) / 2


def _data(line: list[Word]) -> bool:
    texts = [w[4] for w in line]
    return (
        len(texts) >= 7
        and NUMBER.fullmatch(texts[0]) is not None
        and STATION.fullmatch(texts[1]) is not None
        and all(NUMBER.fullmatch(t) for t in texts[2:])
    )


def _layout(url: str, data: list[list[Word]], columns: int) -> tuple[int, dict[str, int]]:
    """The number of candidates and the columns after them. Most sheets print valid, rejected, NOTA, total, and
    tendered votes; some leave the rejected column empty in every row, so the full rows are one shorter."""
    full = [[int(w[4]) for w in line[2:]] for line in data if len(line) == columns]
    for candidates, slots in (
        (columns - 7, {"valid": columns - 5, "rejected": columns - 4, "nota": columns - 3, "total": columns - 2}),
        (columns - 6, {"valid": columns - 4, "nota": columns - 3, "total": columns - 2}),
    ):
        slots["tendered"] = columns - 1
        good = 0
        for n in full:
            votes, valid = n[:candidates], n[slots["valid"] - 2]
            rejected = n[slots["rejected"] - 2] if "rejected" in slots else 0
            good += sum(votes) == valid and valid + rejected + n[slots["nota"] - 2] == n[slots["total"] - 2]
        if good >= 0.9 * len(full):
            return candidates, slots
    raise ValueError(f"{url}: no column layout fits the rows")


@functools.cache
def read(url: str) -> Sheet:
    document = pymupdf.open(raw.cached(SOURCE, url).path)
    pages = [_lines(_words(page)) for page in document]
    data = [line for page in pages for line in page if _data(line)]
    if not data:
        raise ValueError(f"{url}: no polling station rows")
    columns = Counter(len(line) for line in data).most_common(1)[0][0]
    full = [line for line in data if len(line) == columns]
    anchors = [statistics.median(_centre(line[i]) for line in full) for i in range(columns)]
    pitch = anchors[2] - anchors[1]
    # A row whose serial and station numbers run together ("119118A") is split after the previous row's serial.
    data = []
    for page in pages:
        for line in page:
            glued = re.fullmatch(r"(\d+)(\d+[A-Z]?)", line[0][4]) if len(line) == columns - 1 else None
            if glued and all(NUMBER.fullmatch(w[4]) for w in line[1:]) and data:
                serial = str(int(data[-1][0][4]) + 1)
                if line[0][4].startswith(serial):
                    x0, y0, x1, y1, _ = line[0]
                    line = [(x0, y0, x0, y1, serial), (x1, y0, x1, y1, line[0][4][len(serial) :]), *line[1:]]
            stray = [w for w in line[2:] if not NUMBER.fullmatch(w[4])]
            if stray and len(line) - len(stray) == columns and all(w[4].isalpha() for w in stray):
                line = line[:2] + [w for w in line[2:] if w not in stray]  # letters of a heading that runs into the row
            if _data(line) and abs(_centre(line[0]) - anchors[0]) < pitch / 2:
                data.append(line)
    candidates, slots = _layout(url, data, columns)
    rows: list[Row] = []
    for line in data:
        cells: dict[int, str] = {}
        for w in line:
            i = min(range(columns), key=lambda i: abs(anchors[i] - _centre(w)))
            if i in cells:
                raise ValueError(f"{url}: two numbers in one column in row {line[0][4]}")
            cells[i] = w[4]
        needed = [0, 1, *range(2, 2 + candidates), slots["valid"], slots["nota"], slots["total"]]
        for i in needed:
            if i not in cells:
                raise ValueError(f"{url}: row {line[0][4]} has no value in column {i + 1}")

        def number(slot: str) -> int | None:
            i = slots.get(slot)
            return None if i is None or i not in cells else int(cells[i])

        rows.append(
            Row(
                int(cells[0]),
                cells[1],
                [int(cells[i]) for i in range(2, 2 + candidates)],
                int(cells[slots["valid"]]),
                number("rejected"),
                int(cells[slots["nota"]]),
                int(cells[slots["total"]]),
                number("tendered"),
            )
        )
    text = "\n".join(" ".join(w[4] for w in line) for line in pages[0])
    flat = " ".join(text.split())
    if m := re.search(r"Constituency/segment \.+\s*(\d+) Name of Assembly/segment \.+\s*(\d+)-(.+?) Assembly", flat):
        electors, segment, segment_name, pc, pc_name = int(m.group(1)), int(m.group(2)), m.group(3), None, None
    elif m := re.search(
        r"FROM THE (\d+)\.\s*(.+?) PARLIAMENTARY CONSTITUENCY .*?Segment\s*-\s*(\d+)\.\s*(.+?):\s*(\d+)", flat
    ):
        pc, pc_name = int(m.group(1)), m.group(2)
        segment, segment_name, electors = int(m.group(3)), m.group(4), int(m.group(5))
    else:
        raise ValueError(f"{url}: no segment heading")
    heads, width = _heads(pages[0], anchors, candidates, data[0][0][1])
    sheet = Sheet(url, len(document), segment, segment_name.strip(), electors, pc, pc_name, heads, width, rows)
    sheet.printed = _printed(pages, anchors, candidates, slots)
    return sheet


def _heads(
    page: list[list[Word]], anchors: list[float], candidates: int, first_row: float
) -> tuple[list[list[list[Word]]], list[float]]:
    """Each candidate's heading words between the 'No of Valid Votes Cast in favour of' line and the first row."""
    top = next(line[0][1] for line in page if any(w[4] == "Cast" for w in line))
    heads: list[list[list[Word]]] = [[] for _ in range(candidates)]
    for line in page:
        if not top < line[0][1] < first_row - 2:
            continue
        parts: dict[int, list[Word]] = {}
        for w in line:
            i = min(range(len(anchors)), key=lambda i: abs(anchors[i] - _centre(w)))
            if 2 <= i < 2 + candidates:
                parts.setdefault(i - 2, []).append(w)
        for i, words in parts.items():
            heads[i].append(words)
    width = [max((ws[-1][2] - ws[0][0] for ws in head), default=0.0) for head in heads]
    return heads, width


def _printed(
    pages: list[list[list[Word]]], anchors: list[float], candidates: int, slots: dict[str, int]
) -> list[int | None] | None:
    """The sheet's printed total row of the votes on the machines: 'Total EVM Votes' or 'GRAND TOTAL'. Its values are
    the candidates' votes, then the valid, rejected, NOTA, and total votes."""
    for page in pages:
        for line in page:
            texts = [w[4] for w in line]
            numbers = [w for w in line if NUMBER.fullmatch(w[4]) or SCIENTIFIC.fullmatch(w[4])]
            label = {"EVM", "TOTAL", "ALL"} & set(texts) or len(numbers) == len(line)
            first = min(range(len(anchors)), key=lambda i: abs(anchors[i] - _centre(numbers[0]))) if numbers else 0
            if len(numbers) >= candidates + 3 and label and first >= 2:
                cells: dict[int, int | None] = {}
                gap: dict[int, float] = {}
                for w in numbers:
                    i = min(range(len(anchors)), key=lambda i: abs(anchors[i] - _centre(w)))
                    if i in gap and gap[i] <= abs(anchors[i] - _centre(w)):
                        continue
                    gap[i] = abs(anchors[i] - _centre(w))
                    cells[i] = int(w[4]) if NUMBER.fullmatch(w[4]) else None
                totals = [cells.get(i, 0) for i in range(2, 2 + candidates)]
                return totals + [
                    cells.get(slots[k], 0) if k in slots else 0 for k in ("valid", "rejected", "nota", "total")
                ]
    return None


def name(sheets: list[Sheet], i: int, split: bool) -> str:
    """A candidate's name from the column headings of a constituency's segment sheets. A heading wraps at its
    column's width, and where the sheets were laid out in a spreadsheet (`split`), a word longer than the column is
    broken across lines. The segments' sheets wrap at different widths, so a break is a space where any sheet prints
    a space there within a line, and inside a word where any sheet prints those letters together. Where every sheet
    wraps at the same place, the break is taken to be inside a word when the line before it is full and the next
    piece has three letters or fewer, and a space otherwise."""
    readings: list[tuple[str, set[int], dict[int, bool]]] = []
    for sheet in sheets:
        letters, spaces, wraps = "", set(), {}
        for n, line in enumerate(sheet.heads[i]):
            for m, w in enumerate(line):
                if letters:
                    if m:
                        spaces.add(len(letters))
                    else:
                        full = sheet.heads[i][n - 1][-1][2] - sheet.heads[i][n - 1][0][0] >= sheet.width[i] - 6
                        wraps[len(letters)] = full and len(sheet.heads[i][n - 1]) == 1 and len(w[4]) <= 3
                letters += w[4]
        readings.append((letters, spaces, wraps))
    letters = Counter(r[0] for r in readings).most_common(1)[0][0]
    agree = [r for r in readings if r[0] == letters]
    text = ""
    for k, letter in enumerate(letters):
        printed = any(k in r[1] for r in agree)
        wrapped = all(k in r[2] for r in agree) and not (split and all(r[2][k] for r in agree))
        if k and (printed or wrapped):
            text += " "
        text += letter
    return text


SMALL = {"OF", "AND", "ALIAS"}
KEEP = {"SC", "ST", "II", "III", "IV", "MD."}


def title(text: str) -> str:
    """Sentence-case an upper-case name; keep initials and anything already in mixed case."""
    words: list[str] = []
    for word in text.split():
        bare = word.strip("()")
        if not word.isupper() or bare in KEEP or len(bare.rstrip(".")) < 2:
            words.append(word)
        elif bare in SMALL and words:
            words.append(word.lower())
        else:
            words.append("-".join(part.capitalize() for part in word.split("-")))
    return " ".join(words)


# ---- checking a sheet ----------------------------------------------------------------------------------------------


def check(sheet: Sheet) -> list[str]:
    """Problems with a sheet's own arithmetic: rows whose candidates don't sum to the valid votes, whose valid,
    rejected, and NOTA votes don't sum to the total, and columns that don't sum to the printed total row."""
    problems: list[str] = []
    for r in sheet.rows:
        if sum(r.votes) != r.valid:
            problems.append(f"station {r.station}: candidates sum to {sum(r.votes)}, valid {r.valid}")
        if r.valid + (r.rejected or 0) + r.nota != r.total:
            problems.append(f"station {r.station}: {r.valid} + {r.rejected} + {r.nota} != {r.total}")
    stations = [r.station for r in sheet.rows]
    if len(set(stations)) != len(stations):
        problems.append(f"repeated stations {[s for s, n in Counter(stations).items() if n > 1]}")
    if sheet.printed is None:
        problems.append("no printed total row")
    else:
        n = len(sheet.heads)
        summed = [sum(r.votes[i] for r in sheet.rows) for i in range(n)]
        summed += [sum(r.valid for r in sheet.rows), sum(r.rejected or 0 for r in sheet.rows)]
        summed += [sum(r.nota for r in sheet.rows), sum(r.total for r in sheet.rows)]
        if any(p is not None and p != v for p, v in zip(sheet.printed, summed, strict=True)):
            problems.append(f"column sums {summed} differ from the printed totals {sheet.printed}")
    return problems


@dataclass
class Seat:
    """A constituency of one set, with its segments' sheets."""

    number: int
    name: str
    reserved: str | None
    sheets: list[Sheet]
    candidates: list[str]
    missing: list[int]
    postal: list[int] | None = None  # Part II's postal ballots: candidates, valid, rejected, NOTA, total
    part_two: str | None = None


@functools.cache
def seats(state: str) -> list[Seat]:
    s = SETS[state]
    sheets = {n: read(url) for n, url in files(state).items()}
    for n, sheet in sheets.items():
        if sheet.segment != n:
            raise ValueError(f"{sheet.url}: the sheet is for segment {sheet.segment}")
    found: list[Seat] = []
    if state == "KA":
        by_pc: dict[int, list[Sheet]] = {}
        names: dict[int, str] = {}
        for sheet in sheets.values():
            assert sheet.constituency is not None and sheet.constituency_name is not None
            by_pc.setdefault(sheet.constituency, []).append(sheet)
            names[sheet.constituency] = sheet.constituency_name
        plan = []
        for n in sorted(by_pc):
            m = re.fullmatch(r"(.+?)\s*(?:\((SC|ST)\))?", names[n].strip())
            assert m is not None
            segments = tuple(sorted(x.segment for x in by_pc[n]))
            plan.append(Constituency(n, title(m.group(1)), m.group(2), segments))
    else:
        plan = list(s.constituencies)
        placed = sorted([n for c in plan for n in c.segments] + list(ASSAM_UNPLACED))
        if placed != list(range(1, s.segments + 1)):
            raise ValueError("the Assam plan does not cover 126 segments")
    for c in plan:
        held = [sheets[n] for n in c.segments if n in sheets]
        missing = [n for n in c.segments if n not in sheets]
        missing += [n for n, among in ASSAM_UNPLACED.items() if state == "AS" and c.number in among]
        if state == "KA" and len(held) != 8:
            missing = [-1] * (8 - len(held))
        counts = {len(x.heads) for x in held}
        if len(counts) != 1:
            raise ValueError(f"{state} {c.number}: segments list {sorted(counts)} candidates")
        candidates = [name(held, i, state == "KA") for i in range(counts.pop())]
        if state == "AS" and ASSAM_NAMED[c.number] not in candidates:
            raise ValueError(f"AS {c.number}: the sheets of segments {c.segments} do not list {ASSAM_NAMED[c.number]}")
        seat = Seat(c.number, c.name, c.reserved, held, candidates, missing)
        if c.number in part_two(state):
            seat.part_two = part_two(state)[c.number]
            seat.postal = _postal(seat)
        found.append(seat)
    return found


def _postal(seat: Seat) -> list[int]:
    """The postal ballot row of a constituency's Part II sheet, after checking its segment rows against the sheets."""
    assert seat.part_two is not None
    document = pymupdf.open(raw.cached(SOURCE, seat.part_two).path)
    lines = [line for page in document for line in _lines(_words(page))]
    width = len(seat.candidates) + 5  # the candidates, then valid, rejected, NOTA, total, and tendered votes
    rows: dict[str, list[int]] = {}
    for line in lines:
        numbers = [int(w[4]) for w in line if NUMBER.fullmatch(w[4])]
        texts = " ".join(w[4] for w in line)
        if len(numbers) < width:
            continue
        if m := re.match(r"(\d+)\s*[A-Za-z]", texts):
            rows[f"segment {m.group(1)}"] = numbers[-width:-1]
        elif len(numbers) == width:
            label = "evm" if "EVM" in texts else "polled" if "Polled" in texts else "postal"
            rows[label] = numbers[:-1]
    for sheet in seat.sheets:
        assert sheet.printed is not None
        if rows.get(f"segment {sheet.segment}") != sheet.printed:  # Part II prints each segment's total row
            raise ValueError(f"{seat.part_two}: segment {sheet.segment} differs from its sheet")
    postal = rows["postal"]
    if [a + b for a, b in zip(rows["evm"], postal, strict=True)] != rows["polled"]:
        raise ValueError(f"{seat.part_two}: machine and postal votes don't sum to the votes polled")
    return postal


# ---- the geo package -----------------------------------------------------------------------------------------------


def pc_area(state: str, number: int) -> str:
    return key("area", PC_SCHEME, state, SETS[state].delimitation.isoformat(), number)


def ac_area(state: str, number: int) -> str:
    return key("area", AC_SCHEME, state, SETS[state].delimitation.isoformat(), number)


def _sources(p: Package) -> None:
    for s in SETS.values():
        p.add(
            "source",
            id=s.source,
            kind="official_body",
            publisher=s.publisher,
            title=f"Form 20 final result sheets, {s.year} Lok Sabha general election, {s.state_name}",
            url=s.site,
            license="Official publication of the Election Commission of India",
            attribution=f"{s.publisher}, Form 20 final result sheets",
            notes="The site refuses scripted requests; each sheet is a copy saved from a web.archive.org capture of "
            "the URL it is cited under.",
        )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The parliamentary constituencies and assembly segments of Assam (2023 delimitation) and Karnataka (2008 "
        "delimitation), as the Form 20 sheets number and name them",
    )
    p.external |= {INDIA, COUNTRY}
    _sources(p)
    p.add(
        "scheme", code=PC_SCHEME, polity_id=INDIA, kind="electoral_district", rank=1, noun="parliamentary constituency"
    )
    p.add("scheme", code=AC_SCHEME, polity_id=INDIA, kind="electoral_district", rank=2, noun="assembly constituency")
    p.add("scheme", code=STATION_SCHEME, polity_id=INDIA, kind="precinct", rank=3, noun="polling station")
    for state, s in SETS.items():
        for seat in complete(state):
            pc = pc_area(state, seat.number)
            p.add("area", id=pc, scheme_code=PC_SCHEME)
            p.add("area_code", area_id=pc, code=f"{state}-{seat.number}", valid_from=s.delimitation)
            p.add("area_parent", area_id=pc, parent_id=COUNTRY, valid_from=s.delimitation)
            p.name(pc, seat.name, "en", "official")
            for sheet in seat.sheets:
                p.uses(raw.cached(SOURCE, sheet.url))
                ac = ac_area(state, sheet.segment)
                p.add("area", id=ac, scheme_code=AC_SCHEME)
                p.add("area_code", area_id=ac, code=f"{state}-{sheet.segment}", valid_from=s.delimitation)
                p.add("area_parent", area_id=ac, parent_id=pc, valid_from=s.delimitation)
                p.name(ac, title(sheet.segment_name), "en", "official")
    return p


# ---- the registry package ------------------------------------------------------------------------------------------


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Lok Sabha and its electoral system, first past the post in single-member constituencies",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(INDIA)
    p.add("body", id=BODY, polity_id=INDIA, kind="lower_house")
    p.name(BODY, "Lok Sabha", "en", "official")
    p.name(BODY, "लोक सभा", "hi", "native", False)
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=INDIA,
        valid_from=date(1962, 1, 1),
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="Each constituency elects one member: the candidate with the most votes wins.",
        lang="en",
    )
    return p


# ---- the election packages -----------------------------------------------------------------------------------------


METHOD = (
    "Counted at each polling station on the voting machines, as the Form 20 final result sheet of each assembly "
    "segment prints it ({publisher}), read from the sheets' text layer. Polling stations are numbered as the sheets "
    "number them and nested in their assembly segment, which carries the segment's electors. Ballots cast are the "
    "sheet's total votes (valid, rejected, and NOTA); tendered votes are not counted and are not stored. The sheets "
    "name the candidates in ballot order but not their parties, so no party is recorded, and the declared winner is "
    "not on Form 20, so no outcome is recorded. Names are read from the column headings, which wrap at the column's "
    "width; a few may be split or joined where a heading breaks inside a word."
)
POSTAL_NOTE = (
    " Postal ballots are counted for the whole constituency; they are stored on a constituency-wide unit from the "
    "Part II sheet, so the set holds the whole count."
)
NO_POSTAL_NOTE = (
    " Postal ballots are counted for the whole constituency and are not on the segment sheets, so the set holds the "
    "machine votes only."
)


def package_key(state: str) -> str:
    return f"{SOURCE}/ls-{SETS[state].year}-{state.lower()}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(state) for state in SETS)]


def complete(state: str) -> list[Seat]:
    return [seat for seat in seats(state) if not seat.missing]


def election(state: str) -> Package:
    s = SETS[state]
    title_text, ordinal, first, last = GENERAL[s.year]
    p = Package(
        package_key(state),
        SOURCE,
        f"The {s.year} Lok Sabha general election in {s.state_name} by polling station, from the Form 20 final "
        "result sheets of each assembly segment",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(INDIA)
    general = key("election", s.year)
    p.add(
        "election",
        id=general,
        polity_id=INDIA,
        kind="general",
        date_from=first,
        date_to=last,
        date_precision="day",
        status="held",
    )
    p.name(general, title_text, "en", "official")
    p.add("slug", path=f"in/{s.year}-general", entity_id=general, is_current=True)
    p.add(
        "note",
        id=key("note", general),
        entity_id=general,
        kind="context",
        body_md=f"The general election to the {ordinal} Lok Sabha, polled in phases from {first:%-d %B} to "
        f"{last:%-d %B %Y}.",
        lang="en",
    )
    for seat in complete(state):
        _contest(p, s, general, seat)
    return p


def _value(
    rows: list[dict[str, object]],
    result_set: str,
    unit: str,
    option: str | None,
    channel: int,
    measure: int,
    value: int | None,
) -> None:
    if value is None:
        return
    rows.append(
        {
            "result_set_id": result_set,
            "unit_id": unit,
            "option_id": option,
            "channel": channel,
            "measure": measure,
            "count_round": 0,
            "flags": 0,
            "value": value,
        }
    )


def _contest(p: Package, s: Set, general: str, seat: Seat) -> None:
    tag = f"{s.year}:{s.state}:{seat.number}"
    contest = key("contest", tag)
    district = pc_area(s.state, seat.number)
    p.add(
        "contest",
        id=contest,
        election_id=general,
        kind="body_seats",
        body_id=BODY,
        district_area_id=district,
        magnitude=1,
        round_number=1,
        electoral_system_id=FPTP,
        default_result_kind="counted",
    )
    label = seat.name + (f" ({seat.reserved})" if seat.reserved else "")
    p.name(contest, f"{s.year} Lok Sabha election in {label}, {s.state_name}", "en", "official")
    path = f"in/{s.year}-general/{s.state.lower()}/{seat.number}-{changeset.slug(seat.name)}"
    p.add("slug", path=path, entity_id=contest, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=general,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=s.source,
        status="active",
        is_default=True,
    )
    method = METHOD.format(publisher=s.publisher) + (POSTAL_NOTE if seat.postal else NO_POSTAL_NOTE)
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    options: list[str] = []
    for i, text in enumerate(seat.candidates, start=1):
        option, person = key("option", tag, i), key("person", tag, i)
        p.add("person", id=person)
        p.name(person, title(text), "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=i,
            ballot_label=title(text),
        )
        p.add(
            "candidacy", id=key("candidacy", tag, i), person_id=person, option_id=option, role="head", ballot_name=text
        )
        options.append(option)
    nota = key("option", tag, "nota")
    p.add("contest_option", id=nota, contest_id=contest, kind="none_of_the_above", ballot_label="NOTA")
    units: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    for sheet in seat.sheets:
        p.artifact(raw.cached(SOURCE, sheet.url), s.source)
        segment = key("unit", tag, sheet.segment)
        units.append(
            {
                "id": segment,
                "polity_id": INDIA,
                "scheme_code": AC_SCHEME,
                "code": f"{tag}:{sheet.segment}",
                "area_relation": "equals",
                "area_id": ac_area(s.state, sheet.segment),
                "kind": "standard",
            }
        )
        p.name(segment, f"{sheet.segment} {title(sheet.segment_name)}", "en", "official")
        _value(values, result_set, segment, None, ALL, REGISTERED, sheet.electors)
        for r in sheet.rows:
            unit = key("unit", tag, sheet.segment, r.station)
            units.append(
                {
                    "id": unit,
                    "polity_id": INDIA,
                    "scheme_code": STATION_SCHEME,
                    "code": f"{tag}:{sheet.segment}:{r.station}",
                    "parent_id": segment,
                    "area_relation": "within",
                    "area_id": ac_area(s.state, sheet.segment),
                    "kind": "standard",
                }
            )
            for option, votes in zip(options, r.votes, strict=True):
                _value(values, result_set, unit, option, ELECTION_DAY, VOTES, votes)
            _value(values, result_set, unit, nota, ELECTION_DAY, VOTES, r.nota)
            _value(values, result_set, unit, None, ELECTION_DAY, INVALID, r.rejected)
            _value(values, result_set, unit, None, ELECTION_DAY, BALLOTS, r.total)
    if seat.postal and seat.part_two:
        p.artifact(raw.cached(SOURCE, seat.part_two), s.source)
        postal = key("unit", tag, "postal")
        units.append(
            {
                "id": postal,
                "polity_id": INDIA,
                "scheme_code": PC_SCHEME,
                "code": f"{tag}:postal",
                "area_relation": "equals",
                "area_id": district,
                "kind": "postal_district",
            }
        )
        p.name(postal, f"{seat.name}: postal ballots", "en", "official")
        n = len(options)
        for option, votes in zip(options, seat.postal[:n], strict=True):
            _value(values, result_set, postal, option, POSTAL, VOTES, votes)
        _value(values, result_set, postal, nota, POSTAL, VOTES, seat.postal[n + 2])
        _value(values, result_set, postal, None, POSTAL, INVALID, seat.postal[n + 1])
        _value(values, result_set, postal, None, POSTAL, BALLOTS, seat.postal[n + 3])
    p.frame("reporting_unit", pl.DataFrame(units, infer_schema_length=None))
    p.frame("result_value", pl.DataFrame(values, infer_schema_length=None))


# ---- the steps -----------------------------------------------------------------------------------------------------


def parse() -> None:
    for state in SETS:
        for seat in seats(state):
            for sheet in seat.sheets:
                problems = check(sheet)
                if problems:
                    raise ValueError(f"{sheet.url}: " + "; ".join(problems[:5]))
    geo().write()
    registry().write()
    for state in SETS:
        election(state).write()


def emit() -> None:
    package.run("load", packages())
