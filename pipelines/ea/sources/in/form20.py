"""India's 2024 Lok Sabha general election by polling station, from the Form 20 final result sheets the Chief
Electoral Officers of Bihar and Rajasthan posted, read from scans.

After the count, the Assistant Returning Officer of each assembly segment of a parliamentary constituency signs Form 20
Part I (rule 56C of the Conduct of Election Rules, 1961): one row per polling station with the votes each candidate
polled on the voting machines, the total of valid votes, the rejected votes, the votes for none of the above (NOTA),
the total, and the tendered votes, and a row at the foot with the segment's totals. The Returning Officer's Part II
sheet gives each segment's totals again, the postal ballots, and the constituency's total.

The Chief Electoral Officer of Bihar (ceobihar.nic.in) lists the sheets on one page, Part I by constituency and segment
(`pdf/GELS2024/Form_20/Part_1/<pc>/<segment>.pdf`) and Part II by constituency; the Chief Electoral Officer of
Rajasthan posts Part I as `Form20-<segment>.pdf`. Both sites refuse requests from outside India, so the files are
copies saved from web.archive.org captures of the original URLs, and each is cited under its original URL.

Every sheet is a scan. Some carry a text layer from a character recognition pass, but its figures do not add up, so the
pages are read again here: the grid of each page from its rules and each cell twice with Tesseract
(`ea.scan_grid`). A station's figures are kept only when one choice among the reads satisfies the sheet's
arithmetic; a figure no read gives is taken from the row's arithmetic only when every other figure in the row agrees,
and the sheet's printed totals then confirm it. A segment is kept only when its stations add up to the totals its sheet
prints, and, where Part II was read, to the segment's row there. Each figure keeps its page and the box of its cell.

The election, the Lok Sabha, and every constituency contest of both States, with their candidates and declared
totals, are already held by in.eci_results. Each Form 20 result set attaches to that contest and its options, matched
by the names the sheets print, and stays beside its default declared count.

Maharashtra's 2019 sheets, which the Chief Electoral Officer still posts, are read by `form20_mh`, built from here,
and Tamil Nadu's 2019 sheets, saved from the Chief Electoral Officer's site, by `form20_tn`.
"""

import functools
import hashlib
import importlib
import json
import os
import re
import tempfile
import urllib.parse
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid5

import polars as pl
import pymupdf
from PIL import Image

from ea import package, raw, scan
from ea import scan_grid as ocr
from ea.package import Package
from ea.store import DATA

form20_mh = importlib.import_module("ea.sources.in.form20_mh")
form20_tn = importlib.import_module("ea.sources.in.form20_tn")

SOURCE = "in.form20"
INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
INBOX = DATA / "inbox" / SOURCE
WORK = DATA / "work" / SOURCE
PAGES = DATA / "pages" / SOURCE
REFERENCE = DATA / "reference"
BIHAR_INDEX = "https://ceobihar.nic.in/GELS2024Form20_Part1_Part2.html"
BIHAR_DIR = "https://ceobihar.nic.in/pdf/GELS2024/Form_20/"
RAJASTHAN_DIR = "https://ceorajasthan.nic.in/Lok_Sabha_Election_2024/ElectionResults/Form20/"
STATION_SCHEME = "in.ps"
SEGMENT_SCHEME = "in.ac"
PC_SCHEME = "in.pc"
VOTES, BALLOTS, INVALID = 1, 3, 5
ELECTION_DAY, POSTAL = 1, 3
DIGITIZED = 8


def _ids(pipeline: str) -> Callable[..., str]:
    return Package(pipeline, pipeline, "ids").id


_GEO = _ids("in.geo")
_RESULTS = _ids("in.eci_results")
key = _ids(SOURCE)
ELECTION = _RESULTS("election", "lok-sabha-2024")


@dataclass(frozen=True)
class State:
    code: str  # the package suffix
    name: str
    eci: str  # the Commission's state code
    lgd: str  # the Local Government Directory's state code, which in.geo's constituency codes carry
    publisher: str
    site: str
    lead: int  # cells before the candidates in a station row: the serial number, and the station's own where printed


STATES = {
    "br": State("br", "Bihar", "S04", "10", "Chief Electoral Officer, Bihar", "https://ceobihar.nic.in/", 2),
    "rj": State(
        "rj", "Rajasthan", "S20", "08", "Chief Electoral Officer, Rajasthan", "https://ceorajasthan.nic.in/", 1
    ),
}


def source_id(s: State) -> str:
    return key("source", s.code)


def contest_id(s: State, pc: int) -> str:
    return _RESULTS("contest", f"lok-sabha-2024:{s.eci}:{pc}")


def pc_area(s: State, pc: int) -> str:
    return _GEO("area", PC_SCHEME, f"{s.lgd}{pc:02d}", "2008")


def ac_area(s: State, ac: int) -> str:
    return _GEO("area", SEGMENT_SCHEME, f"{s.lgd}{ac:03d}", "2008")


# ---- the raw files -------------------------------------------------------------------------------------------------


def fetch() -> None:
    """Fetch Maharashtra's 2019 sheets, then store the saved copies of Bihar's and Rajasthan's."""
    form20_mh.fetch()
    if (INBOX / "index.json").exists():
        _store_saved()


def _store_saved() -> None:
    """Store the saved copies under their original URLs, refusing any whose hash differs. A copy saved from a browser
    session is named by its file name only; it is stored under the URL of the same file name that the Bihar index or
    the Rajasthan folder gives, unless the archive's capture of that URL is already stored."""
    index = json.loads((INBOX / "index.json").read_text())
    entries: list[tuple[str, dict[str, str]]] = sorted(index.items(), key=lambda item: item[0].startswith("browser://"))
    stored: set[str] = set()
    for url, entry in entries:
        content = (INBOX / "files" / entry["file"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"{url}: the saved file's hash differs from {entry['sha256']}")
        target = url
        if url.startswith("browser://"):
            name = url.rsplit("/", 1)[1]
            target = RAJASTHAN_DIR + name if name.startswith("Form20-") else _bihar_by_name().get(name, "")
            if not target or target in stored:
                continue
        elif url.lower().endswith(".pdf") and not content.startswith(b"%PDF"):
            raise ValueError(f"{url} is not a PDF")
        raw.store(
            SOURCE,
            target,
            content,
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )
        stored.add(target)


def _quoted(url: str) -> str:
    return urllib.parse.quote(url, safe=":/")


@functools.cache
def bihar_links() -> list[str]:
    """The sheet URLs the Bihar index page links, quoted as the archive captured them."""
    text = raw.cached(SOURCE, BIHAR_INDEX).path.read_text(encoding="utf-8", errors="replace")
    return [
        _quoted(u) for u in dict.fromkeys(re.findall(r'href="(https://ceobihar\.nic\.in/pdf/GELS2024/[^"]+)"', text))
    ]


@functools.cache
def _bihar_by_name() -> dict[str, str]:
    index = json.loads((INBOX / "index.json").read_text())
    entry = index[BIHAR_INDEX]
    text = (INBOX / "files" / entry["file"]).read_text(encoding="utf-8", errors="replace")
    links = [_quoted(u) for u in re.findall(r'href="(https://ceobihar\.nic\.in/pdf/GELS2024/[^"]+)"', text)]
    return {urllib.parse.unquote(u.rsplit("/", 1)[1]): u for u in links if "/Part_1/" in u}


def _cached(url: str) -> raw.Artifact | None:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return None


@dataclass(frozen=True)
class Planned:
    pc: int
    pc_name: str
    segments: dict[int, str | None]  # each segment's Part I URL, or None where no copy was reached
    part_two: str | None


@functools.cache
def bihar_plan() -> dict[int, Planned]:
    """Bihar's constituencies and their segments, as the index page lists them."""
    pcs: dict[int, tuple[str, dict[int, str | None]]] = {}
    twos: dict[int, str] = {}
    for url in bihar_links():
        path = urllib.parse.unquote(url)
        if m := re.search(r"/Part_1/(\d+)-([^/]+)/(\d+)-[^/]+\.pdf$", path):
            pc = int(m.group(1))
            pcs.setdefault(pc, (m.group(2), {}))[1][int(m.group(3))] = url if _cached(url) else None
        elif m := re.search(r"/Part_2/(\d+)-[^/]+\.pdf$", path):
            if _cached(url):
                twos[int(m.group(1))] = url
    return {pc: Planned(pc, name, dict(sorted(segs.items())), twos.get(pc)) for pc, (name, segs) in sorted(pcs.items())}


def rajasthan_files() -> dict[int, str]:
    found: dict[int, str] = {}
    for url in raw.keys(SOURCE):
        if m := re.fullmatch(re.escape(RAJASTHAN_DIR) + r"Form20-(\d+)\.pdf", url):
            found[int(m.group(1))] = url
    return dict(sorted(found.items()))


# ---- the constituencies in.eci_results holds -----------------------------------------------------------------------


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    nota: bool


@functools.cache
def held_options() -> dict[str, list[Option]]:
    """Each contest's options as in.eci_results holds them, from the reference partition."""
    frame = pl.read_parquet(REFERENCE / "in" / "contest_option.parquet").filter(pl.col("pipeline") == "in.eci_results")
    out: dict[str, list[Option]] = {}
    for row in frame.sort("ballot_position", nulls_last=True).iter_rows(named=True):
        out.setdefault(row["contest_id"], []).append(
            Option(row["id"], row["ballot_label"] or "", row["kind"] == "none_of_the_above")
        )
    return out


def _fold(name: str) -> str:
    return " ".join(re.sub(r"[^A-Z]+", " ", name.upper()).split())


def similar(a: str, b: str) -> float:
    a, b = _fold(a), _fold(b)
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    overlap = len(ta & tb) / max(len(ta | tb), 1)
    return max(overlap, SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", "")).ratio())


def match(names: list[str], options: list[Option]) -> tuple[list[Option], float] | None:
    """Pair each column's printed name with one of the contest's candidates, best pairs first; None unless every
    column finds a distinct candidate."""
    pool = [o for o in options if not o.nota]
    if len(pool) != len(names):
        return None
    pairs = sorted(((similar(n, o.label), j, i) for j, n in enumerate(names) for i, o in enumerate(pool)), reverse=True)
    chosen: dict[int, int] = {}
    used: set[int] = set()
    for _, j, i in pairs:
        if j not in chosen and i not in used:
            chosen[j] = i
            used.add(i)
    worst = min(similar(names[j], pool[i].label) for j, i in chosen.items())
    return [pool[chosen[j]] for j in range(len(names))], worst


# ---- reading a sheet -----------------------------------------------------------------------------------------------


def render(artifact: raw.Artifact, page: int) -> Path:
    """The page at 300 dpi in grey, from the scan, turned level."""
    WORK.mkdir(parents=True, exist_ok=True)
    png = WORK / f"{artifact.sha256}-{page}.png"
    if not png.exists():
        with tempfile.TemporaryDirectory(dir=WORK) as folder:
            image = Image.open(scan.rendered(artifact.path, page, Path(folder))).convert("L")
            ocr.deskew(image).save(png)
    return png


def page_image(artifact: raw.Artifact, page: int) -> tuple[Path, int, int]:
    """The page image the site shows beside a figure: 150 dpi, named by the file's hash and the page number."""
    PAGES.mkdir(parents=True, exist_ok=True)
    image = PAGES / f"{artifact.sha256}-{page}.png"
    if not image.exists():
        picture = Image.open(render(artifact, page)).convert("L")
        picture = picture.resize((picture.width // 2, picture.height // 2), Image.Resampling.LANCZOS)
        picture.save(image, optimize=True)
    width, height = Image.open(image).size
    return image, width, height


@dataclass
class PageRead:
    """One page's grid and its rows' reads, cached beside the rendered page."""

    rows: list[int]
    cols: list[list[int]]
    size: tuple[int, int]
    a: list[list[str]]
    b: list[list[str]]
    names: list[str] = field(default_factory=list)  # the text of each cell of the row above the first number row
    header: str = ""  # the text above the table
    extra: dict[str, list[str]] = field(default_factory=dict)  # "row,col": the cell read on its own


def _first_number_row(a: list[list[str]], b: list[list[str]]) -> int | None:
    for r, (x, y) in enumerate(zip(a, b, strict=True)):
        if (
            sum(ocr.number(c) is not None for c in x) >= len(x) - 3
            or sum(ocr.number(c) is not None for c in y) >= len(y) - 3
        ):
            return r
    return None


def read_page(artifact: raw.Artifact, page: int) -> PageRead | None:
    cache = WORK / f"{artifact.sha256}-{page}.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        return None if data is None else PageRead(**data)
    g = ocr.grid(render(artifact, page))
    if g is None:
        cache.write_text("null")
        return None
    reads = ocr.reads(g)
    out = PageRead(g.rows, g.cols, g.size, reads.a, reads.b)
    first = _first_number_row(reads.a, reads.b)
    if g.rows[0] > 60:
        out.header = ocr.text(g.image.crop((0, 0, g.size[0], g.rows[0])))
    if first:
        out.names = ocr.strip(g.image, [g.box(first - 1, c) for c in range(len(g.cols[0]) - 1)], 1.0, False, None, "6")
    cache.write_text(json.dumps(out.__dict__))
    return out


@functools.lru_cache(maxsize=8)
def _image(path: Path) -> Image.Image:
    return Image.open(path).convert("L")


def extra(artifact: raw.Artifact, page: int, read: PageRead, r: int, c: int) -> list[str]:
    """A cell read on its own, for a row whose strip reads don't settle it; cached with the page's reads."""
    slot = f"{r},{c}"
    if slot not in read.extra:
        g = ocr.Grid(_image(render(artifact, page)), read.rows, read.cols, read.size)
        read.extra[slot] = ocr.cell(g.image, g.box(r, c))
        (WORK / f"{artifact.sha256}-{page}.json").write_text(json.dumps(read.__dict__))
    return read.extra[slot]


type Box = tuple[float, float, float, float]


@dataclass
class Row:
    serial: int | None
    station: str
    votes: list[int]
    valid: int
    rejected: int
    nota: int
    total: int
    page: int
    boxes: list[Box]  # each figure's box: the candidates, then valid, rejected, NOTA, total
    derived: int = 0  # figures taken from the row's arithmetic


@dataclass
class Sheet:
    url: str
    segment: int | None  # the segment the sheet's heading names
    pages: int
    k: int
    names: list[str]
    rows: list[Row]
    printed: list[int] | None  # the totals row: candidates, valid, rejected, NOTA, total
    problems: list[str]


def _norm(read: PageRead, r: int, c: int) -> Box:
    w, h = read.size
    return (
        round(read.cols[r][c] / w, 4),
        round(read.rows[r] / h, 4),
        round(read.cols[r][c + 1] / w, 4),
        round(read.rows[r + 1] / h, 4),
    )


def _resolve(
    artifact: raw.Artifact, page: int, read: PageRead, r: int, first: int, k: int
) -> tuple[list[int] | None, int]:
    """The row's figures (candidates, valid, rejected, NOTA, total) from cells first..first+k+4, and how many were
    taken from the arithmetic."""
    cells = range(first, first + k + 4)
    options = [ocr.options([read.a[r][c], read.b[r][c]]) for c in cells]
    solved, _ = ocr.solve(options, k)
    if solved is not None and all(len(o) == 1 for o in options):
        return solved, 0
    if solved is None:
        unsure = [i for i, o in enumerate(options) if len(o) != 1] or list(range(len(options)))
        for i, c in enumerate(cells):
            if i in unsure:
                options[i] = ocr.options([read.a[r][c], read.b[r][c], *extra(artifact, page, read, r, c)])
        solved, count = ocr.solve(options, k)
        if solved is None and count == 0:
            # A figure no read gives: when every other figure has one agreed value, and the valid, rejected, NOTA,
            # and total votes agree among themselves, the arithmetic gives it.
            empty = [i for i, o in enumerate(options) if not o]
            fixed = all(len(o) == 1 for i, o in enumerate(options) if i not in empty)
            if len(empty) == 1 and fixed and empty[0] < k:
                values = [o[0] if o else 0 for o in options]
                valid, rejected, nota, total = values[k : k + 4]
                missing = valid - sum(values[:k])
                if valid + rejected + nota == total and 0 <= missing < 10_000:
                    values[empty[0]] = missing
                    return values, 1
        return solved, 0
    return solved, 0


def offset(read: PageRead, k: int) -> int:
    """The cell where the candidates' columns begin on a page: the place at which the most rows add up. A page whose
    outer rule the scan lost has a cell fewer at its right end, so the place is not fixed by the count of cells."""
    n = len(read.cols[0]) - 1
    best, score = n - (k + 5), -1
    for first in range(max(n - (k + 5), 0), max(n - (k + 4), 0) + 1):
        if first < 1:
            continue
        hits = 0
        for a, b in zip(read.a, read.b, strict=True):
            options = [ocr.options([a[c], b[c]]) for c in range(first, first + k + 4)]
            hits += ocr.solve(options, k)[0] is not None
        if hits > score:
            best, score = first, hits
    return best


def _code(text: str) -> str | None:
    m = re.fullmatch(r"(\d{1,4})", text)
    return str(int(m.group(1))) if m else None


def _station(artifact: raw.Artifact, page: int, read: PageRead, r: int, first: int, lead: int) -> str | None:
    """The station's number: its own cell where the sheet prints one, else the serial number. The rule between the
    two cells is faint, so a read can put both numbers in one cell; the two are then the same number twice."""
    a, b = read.a[r], read.b[r]
    if lead == 1 or first < 2:
        codes = {_code(a[0]), _code(b[0])} - {None}
        return codes.pop() if len(codes) == 1 else None
    found: set[str | None] = set()
    for x in (a, b):
        joined = x[0] + x[1]
        half = len(joined) // 2
        if x[1] and (not x[0] or x[0] == x[1]):
            found.add(_code(x[1]))
        elif joined and len(joined) % 2 == 0 and joined[:half] == joined[half:]:
            found.add(_code(joined[:half]))
    found -= {None}
    if len(found) != 1:
        texts = extra(artifact, page, read, r, 1)
        found = {_code(t) for t in texts} - {None} if len({_code(t) for t in texts} - {None}) == 1 else found
    return found.pop() if len(found) == 1 else None


SEGMENT = re.compile(r"[Ss]egment\W{0,12}(\d{1,3})\s*[-–.]\s*[A-Za-z]")


def read_sheet(url: str, k: int, lead: int) -> Sheet:
    """A segment's Part I sheet: its station rows, each settled by the reads and the row's arithmetic, and its
    printed totals row."""
    artifact = raw.cached(SOURCE, url)
    document = pymupdf.open(artifact.path)
    rows: list[Row] = []
    printed: list[int] | None = None
    problems: list[str] = []
    names: list[str] = []
    segment: int | None = None
    for page in range(1, len(document) + 1):
        read = read_page(artifact, page)
        if read is None:
            problems.append(f"page {page}: no grid")
            continue
        n = len(read.cols[0]) - 1
        first = offset(read, k)
        if first < 1:
            problems.append(f"page {page}: {n} cells in a row, too few for {k} candidates")
            continue
        if not names and read.names:
            names = read.names[first : first + k]
        if segment is None and (m := SEGMENT.search(" ".join(read.header.split()))):
            segment = int(m.group(1))
        for r in range(len(read.a)):
            a, b = read.a[r], read.b[r]
            leading = [ocr.number(a[c]) for c in range(first)] + [ocr.number(b[c]) for c in range(first)]
            figures = sum(ocr.number(x) is not None or ocr.number(y) is not None for x, y in zip(a, b, strict=True))
            if figures < k + 2:
                continue
            values, derived = _resolve(artifact, page, read, r, first, k)
            boxes = [_norm(read, r, c) for c in range(first, first + k + 4)]
            if all(v is None for v in leading):
                # the totals row: its label spans the serial and station cells
                if values is not None:
                    printed = values
                continue
            if values is None:
                problems.append(f"page {page} row {r}: the reads don't settle the row ({a[first:]} / {b[first:]})")
                continue
            serial = next((x for x in (ocr.number(a[0]), ocr.number(b[0])) if x is not None), None)
            station = _station(artifact, page, read, r, first, lead)
            rows.append(
                Row(
                    serial,
                    station or "",
                    values[:k],
                    values[k],
                    values[k + 1],
                    values[k + 2],
                    values[k + 3],
                    page,
                    boxes,
                    derived,
                )
            )
    _number_stations(rows, problems, lead)
    sheet = Sheet(url, segment, len(document), k, names, rows, printed, problems)
    sheet.problems += check(sheet)
    return sheet


def _number_stations(rows: list[Row], problems: list[str], lead: int) -> None:
    """Fill a station number the reads left unsettled from its neighbours: stations run in serial order, and where
    the sheet prints no separate station number, the serial number is the station's."""
    for i, row in enumerate(rows):
        if row.station:
            continue
        before = rows[i - 1].station if i else "0"
        after = rows[i + 1].station if i + 1 < len(rows) else None
        if before.isdigit() and (after is None or (after.isdigit() and int(after) == int(before) + 2)):
            row.station = str(int(before) + 1)
        else:
            problems.append(f"row {i + 1}: the station number is not settled")
    codes = [r.station for r in rows]
    repeated = [c for c, n in Counter(codes).items() if n > 1 and c]
    if repeated:
        problems.append(f"repeated stations {repeated[:5]}")


def check(sheet: Sheet) -> list[str]:
    """The sheet's columns against its printed totals row."""
    if not sheet.rows:
        return ["no station rows"]
    if sheet.printed is None:
        return ["no printed totals row was read"]
    k = sheet.k
    summed = [sum(r.votes[j] for r in sheet.rows) for j in range(k)]
    summed += [sum(r.valid for r in sheet.rows), sum(r.rejected for r in sheet.rows)]
    summed += [sum(r.nota for r in sheet.rows), sum(r.total for r in sheet.rows)]
    if summed != sheet.printed:
        return [f"the columns sum to {summed}, the sheet prints {sheet.printed}"]
    return []


# ---- Part II -------------------------------------------------------------------------------------------------------


@dataclass
class PartTwo:
    url: str
    segments: list[list[int]]  # each segment's row: candidates, valid, rejected, NOTA, total
    postal: list[int]
    postal_boxes: list[Box]
    evm: list[int]
    polled: list[int]
    page: int


def read_part_two(url: str, k: int) -> PartTwo | str:
    artifact = raw.cached(SOURCE, url)
    read = read_page(artifact, 1)
    if read is None:
        return "no grid"
    n = len(read.cols[0]) - 1
    first = offset(read, k)
    if first < 1:
        return f"{n} cells in a row, too few for {k} candidates"
    found: list[tuple[int, list[int]]] = []
    for r in range(len(read.a)):
        a, b = read.a[r], read.b[r]
        figures = sum(ocr.number(x) is not None or ocr.number(y) is not None for x, y in zip(a, b, strict=True))
        if figures < k + 2:
            continue
        values, _ = _resolve(artifact, 1, read, r, first, k)
        if values is None:
            return f"row {r}: the reads don't settle the row"
        found.append((r, values))
    # the segments' rows, then the machine votes, the postal ballots, and the votes polled
    totals = found[-3:]
    segments = [v for _, v in found[:-3]]
    if len(totals) != 3:
        return f"{len(totals)} total rows, not three"
    evm, postal, polled = (t[1] for t in totals)
    if [x + y for x, y in zip(evm, postal, strict=True)] != polled:
        return "the machine and postal votes don't add to the votes polled"
    summed = [sum(v[j] for v in segments) for j in range(k + 4)]
    if summed != evm:
        return "the segments' rows don't add to the machine total"
    boxes = [_norm(read, totals[1][0], c) for c in range(first, first + k + 4)]
    return PartTwo(url, segments, postal, boxes, evm, polled, 1)


# ---- putting a constituency together -------------------------------------------------------------------------------


@dataclass
class Constituency:
    state: State
    pc: int
    name: str
    segments: list[int]  # every segment of the constituency
    sheets: list[Sheet]
    columns: list[Option]  # the option of each Form 20 column
    nota: Option
    part_two: PartTwo | None
    left_out: list[str]


def read_all(jobs: list[tuple[str, int, int]]) -> dict[str, Sheet]:
    """Read many sheets, a few at a time; each page's rows are read in parallel within it."""
    workers = (os.cpu_count() or 2) + 2
    with ThreadPoolExecutor(workers) as pool:
        return {job[0]: sheet for job, sheet in zip(jobs, pool.map(lambda job: read_sheet(*job), jobs), strict=True)}


def _columns(sheets: list[Sheet], options: list[Option]) -> tuple[list[Option], float] | None:
    """The option of each column, from the names the sheets print: the matching whose weakest pair is strongest."""
    best: tuple[list[Option], float] | None = None
    for sheet in sheets:
        if len(sheet.names) == sheet.k and all(sheet.names):
            found = match(sheet.names, options)
            if found and (best is None or found[1] > best[1]):
                best = found
    return best


def assemble(
    s: State, pc: int, name: str, segments: dict[int, str | None], part_two: str | None, sheets: dict[str, Sheet]
) -> Constituency | None:
    """A constituency's sheets that check out, keyed by the segment each sheet's heading names; a sheet whose heading
    names another segment of the constituency than its file name stands for that segment."""
    options = held_options().get(contest_id(s, pc), [])
    if not options:
        return None
    nota = next((o for o in options if o.nota), None)
    if nota is None:
        return None
    k = len(options) - 1
    left: list[str] = []
    two: PartTwo | None = None
    if part_two:
        found = read_part_two(part_two, k)
        if isinstance(found, str):
            left.append(f"Part II: {found}")
        else:
            two = found
    read = [sheets[u] for u in segments.values() if u and u in sheets]
    good: dict[int, Sheet] = {}
    for ac, url in segments.items():
        sheet = sheets.get(url) if url else None
        if sheet is None:
            continue
        printed = sheet.segment if sheet.segment in segments else ac
        if printed in good:
            continue
        if sheet.problems:
            left.append(f"segment {printed}: {'; '.join(sheet.problems[:3])}")
        elif two and sheet.printed not in two.segments:
            left.append(f"segment {printed}: its totals differ from its row on Part II")
        else:
            sheet.segment = printed
            good[printed] = sheet
    for ac in segments:
        if ac not in good and not any(x.startswith(f"segment {ac}:") for x in left):
            left.append(f"segment {ac}: no copy of its sheet was reached")
    columns = _columns(read, options)
    if good and (columns is None or columns[1] < 0.6):
        left.append("the columns could not be matched to the candidates by name")
        good = {}
    return Constituency(
        s,
        pc,
        name,
        list(segments),
        [good[ac] for ac in sorted(good)],
        columns[0] if columns and good else [],
        nota,
        two if good else None,
        left,
    )


@functools.cache
def bihar() -> list[Constituency]:
    s = STATES["br"]
    plan = bihar_plan()
    jobs: list[tuple[str, int, int]] = []
    for p in plan.values():
        k = len(held_options().get(contest_id(s, p.pc), [])) - 1
        jobs += [(url, k, s.lead) for url in p.segments.values() if url and k > 0]
    sheets = read_all(jobs)
    out = [assemble(s, p.pc, p.pc_name, p.segments, p.part_two, sheets) for p in plan.values()]
    return [c for c in out if c is not None]


# ---- the packages --------------------------------------------------------------------------------------------------


def package_key(s: State) -> str:
    return f"{SOURCE}/ls-2024-{s.code}"


def packages() -> list[str]:
    """The packages with at least one constituency whose sheets check out."""
    return [
        *(package_key(s) for s in STATES.values() if constituencies(s)),
        *form20_mh.packages(),
        *form20_tn.packages(),
    ]


def constituencies(s: State) -> list[Constituency]:
    if s.code != "br" or _cached(BIHAR_INDEX) is None:
        return []
    return [c for c in bihar() if c.sheets]


@functools.cache
def area_names() -> dict[str, str]:
    frame = pl.read_parquet(REFERENCE / "in" / "area.parquet")
    return dict(zip(frame["id"], frame["name"], strict=True))


METHOD = (
    "Polling-station results from Form 20 Part I, the final result sheet the Assistant Returning Officer signed for "
    "each assembly segment, as the {publisher} posted it, read from archived copies of the scans. Each page's grid was "
    "found from its rules and each cell read twice with Tesseract, and once more on its own where the two reads "
    "disagreed; a station's figures were kept only where exactly one choice among the reads satisfies the row's "
    "arithmetic (the candidates' votes add to the valid votes, and the valid, rejected, and NOTA votes to the total). "
    "{derived}A segment is kept only when its stations add up to every total its sheet prints{part_two}. Each figure "
    "keeps its page and the box of its cell. For each station: the machine votes for each candidate and for NOTA, the "
    "rejected votes (stored as invalid), and the total (stored as ballots cast); tendered votes are not counted and not "
    "stored. Stations are numbered as the sheets number them and nested in their segment. Columns are matched to the "
    "candidates by the names the sheets print. The declared totals of the constituency are the default count of this "
    "contest."
)


def election(s: State) -> Package:
    p = Package(
        package_key(s),
        SOURCE,
        f"The 2024 Lok Sabha election in {s.name} by polling station, from the Form 20 sheets, attached to the "
        "constituency contests the Election Commission's result pages give",
        depends=["in.geo/geo", "in.eci_results/registry", "in.eci_results/lok-sabha-2024"],
    )
    p.external |= {INDIA, ELECTION}
    sid = source_id(s)
    p.add(
        "source",
        id=sid,
        kind="official_body",
        publisher=s.publisher,
        title=f"Form 20 final result sheets, 2024 Lok Sabha general election, {s.name}",
        url=s.site,
        license="Official publication of the Election Commission of India",
        attribution=f"{s.publisher}, Form 20 final result sheets",
        notes="The site refuses requests from outside India; each sheet is a copy saved from a web.archive.org "
        "capture of the URL it is cited under.",
    )
    if s.code == "br":
        p.uses(raw.cached(SOURCE, BIHAR_INDEX))
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    for c in constituencies(s):
        _constituency(p, c, values, locators)
    p.frame("result_value", pl.DataFrame(values, schema_overrides={"option_id": pl.String}))
    p.frame("value_locator", pl.DataFrame(locators, schema_overrides={"option_id": pl.String}))
    return p


def _constituency(p: Package, c: Constituency, values: list[dict[str, object]], locators: list[dict]) -> None:
    s = c.state
    sid = source_id(s)
    contest = contest_id(s, c.pc)
    p.external |= {contest, c.nota.id, *(o.id for o in c.columns), pc_area(s, c.pc)}
    rs = key("result_set", 2024, s.eci, c.pc)
    p.add(
        "result_set",
        id=rs,
        election_id=ELECTION,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=sid,
        status="active",
        is_default=False,
        completeness=round(len(c.sheets) / len(c.segments), 4),
    )
    derived = sum(r.derived for sh in c.sheets for r in sh.rows)
    body = METHOD.format(
        publisher=s.publisher,
        derived=f"{derived} figures no read gave were taken from their row's arithmetic, where every other figure "
        "of the row agreed and the sheet's printed totals then held. "
        if derived
        else "",
        part_two=", and to its row on the Returning Officer's Part II sheet" if c.part_two else "",
    )
    read = ", ".join(str(sh.segment) for sh in c.sheets)
    missing = [ac for ac in c.segments if ac not in {sh.segment for sh in c.sheets}]
    body += f" Segments read: {read}."
    if missing:
        body += f" Segments without a sheet that checks out here: {', '.join(map(str, missing))}."
    if c.part_two:
        body += " The postal ballots are stored on a constituency-wide unit from Part II."
    else:
        body += " Postal ballots are counted for the whole constituency and are not on the segment sheets."
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=body, lang="en")
    options = [o.id for o in c.columns]

    def put(unit: str, option: str | None, channel: int, measure: int, value: int, page: str, box: Box) -> None:
        fact = {
            "result_set_id": rs,
            "unit_id": unit,
            "option_id": option,
            "channel": channel,
            "measure": measure,
            "count_round": 0,
        }
        values.append({**fact, "flags": DIGITIZED, "value": value})
        locators.append(
            {**fact, "page_id": page, "source_id": None, "x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3]}
        )

    def pages(url: str, numbers: set[int]) -> dict[int, str]:
        artifact = raw.cached(SOURCE, url)
        stored = p.artifact(artifact, sid)
        out: dict[int, str] = {}
        for n in sorted(numbers):
            image, width, height = page_image(artifact, n)
            out[n] = key("page", artifact.sha256, n)
            p.add(
                "source_page",
                id=out[n],
                artifact_id=stored,
                page_number=n,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        return out

    for sh in c.sheets:
        assert sh.segment is not None
        area = ac_area(s, sh.segment)
        p.external.add(area)
        segment = key("unit", 2024, s.eci, c.pc, sh.segment)
        p.add(
            "reporting_unit",
            id=segment,
            polity_id=INDIA,
            scheme_code=SEGMENT_SCHEME,
            code=f"2024:{s.eci}:{c.pc}:{sh.segment}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        p.name(segment, f"{sh.segment} {area_names().get(area, '')}".strip(), "en", "official")
        ids = pages(sh.url, {r.page for r in sh.rows})
        seen: Counter[str] = Counter()
        for r in sh.rows:
            seen[r.station] += 1
            code = r.station if seen[r.station] == 1 else f"{r.station}#{seen[r.station]}"
            unit = key("unit", 2024, s.lgd, sh.segment, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=INDIA,
                scheme_code=STATION_SCHEME,
                code=f"2024:{s.lgd}{sh.segment:03d}:{code}",
                parent_id=segment,
                area_relation="within",
                area_id=area,
                kind="standard",
            )
            page = ids[r.page]
            for j, option in enumerate(options):
                put(unit, option, ELECTION_DAY, VOTES, r.votes[j], page, r.boxes[j])
            k = len(options)
            put(unit, c.nota.id, ELECTION_DAY, VOTES, r.nota, page, r.boxes[k + 2])
            put(unit, None, ELECTION_DAY, INVALID, r.rejected, page, r.boxes[k + 1])
            put(unit, None, ELECTION_DAY, BALLOTS, r.total, page, r.boxes[k + 3])
    if c.part_two:
        two = c.part_two
        page = pages(two.url, {two.page})[two.page]
        postal = key("unit", 2024, s.eci, c.pc, "postal")
        p.add(
            "reporting_unit",
            id=postal,
            polity_id=INDIA,
            scheme_code=PC_SCHEME,
            code=f"2024:{s.eci}:{c.pc}:postal",
            area_relation="equals",
            area_id=pc_area(s, c.pc),
            kind="postal_district",
        )
        p.name(postal, f"{c.name}: postal ballots", "en", "official")
        k = len(options)
        for j, option in enumerate(options):
            put(postal, option, POSTAL, VOTES, two.postal[j], page, two.postal_boxes[j])
        put(postal, c.nota.id, POSTAL, VOTES, two.postal[k + 2], page, two.postal_boxes[k + 2])
        put(postal, None, POSTAL, INVALID, two.postal[k + 1], page, two.postal_boxes[k + 1])
        put(postal, None, POSTAL, BALLOTS, two.postal[k + 3], page, two.postal_boxes[k + 3])


def parse() -> None:
    for s in STATES.values():
        if constituencies(s):
            election(s).write()
    if form20_mh.packages():
        form20_mh.election().write()
    if form20_tn.packages():
        form20_tn.election().write()
    package.folder(SOURCE).mkdir(parents=True, exist_ok=True)
    (package.folder(SOURCE) / "coverage.json").write_text(json.dumps(coverage(), indent=1))
    (package.folder(SOURCE) / "coverage-mh-2019.json").write_text(json.dumps(form20_mh.coverage(), indent=1))
    (package.folder(SOURCE) / "coverage-tn-2019.json").write_text(json.dumps(form20_tn.coverage(), indent=1))


def emit() -> None:
    package.run("load", packages())


def coverage() -> dict[str, object]:
    """Each constituency: the segments read, and why the others are not."""
    return {
        f"{c.state.code}-{c.pc}": {
            "name": c.name,
            "read": [sh.segment for sh in c.sheets],
            "segments": c.segments,
            "postal": bool(c.part_two),
            "left_out": c.left_out,
        }
        for c in (bihar() if _cached(BIHAR_INDEX) else [])
    }
