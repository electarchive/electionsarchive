"""Tamil Nadu's 2019 Lok Sabha general election by polling station, from the Form 20 final result sheets the Chief
Electoral Officer, Tamil Nadu posted (part of the `in.form20` source; `ea parse in.form20` builds it).

The Chief Electoral Officer's site (elections.tn.gov.in) posted, for each of the State's 39 constituencies, one Form 20
Part I sheet per assembly segment at `GELS2019_Form20/Form20/PC<pc>/AC<segment>.pdf`, 234 in all, and a Part II sheet
per constituency at `GELS2019_Form20_Part2/PC<pc>.pdf`. The site answers only visitors in India, so the files are
copies saved through an Indian exit with the archive's own User-Agent (a few from web.archive.org captures), each
stored and cited under its original URL.

Each assistant returning officer laid the sheet out in their own way: a row per polling station, or a column per
station with the candidates down the side; serial and station numbers, sometimes the station's name and electors;
the votes each candidate polled on the voting machines in ballot order; the valid votes; the rejected votes (some
sheets leave the column blank or drop it); the votes for none of the above (NOTA), which a few sheets print before
the valid votes or count in them; the total; and the tendered votes. Most sheets carry a text layer. Their words are
read with their positions and grouped into lines both ways, and a line is a station's when, for the constituency's
number of candidates, a run of figures adds up to the valid votes and the valid, rejected, and NOTA votes to the
total. The station's own number, its serial, and its electors are the figures before the run, told apart by where
they sit on the sheet. The others are scans, left out here and named in each set's note.

A sheet is kept only when every station's figures add up and every column adds up to the total row the sheet prints.
Postal ballots are counted for the whole constituency on Part II, which is mostly scanned and is not read here, so the
sets hold the machine votes.

No pipeline holds Tamil Nadu's 2019 constituency contests, so each constituency's contest is made here, under the 2019
general election and the Lok Sabha that in.form20_wb holds. Its candidates, in ballot order, and their parties are as
the sheets' headings print them; the number of candidates in each constituency is the one the Chief Electoral
Officer's table of contesting candidates gives. The declared winner is on Form 21, not on Form 20, so no outcome is
recorded.
"""

import functools
import re
import statistics
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import polars as pl
import pymupdf

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "in.form20"
INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
SITE = "https://www.elections.tn.gov.in/"
SHEETS = SITE + "GELS2019_Form20/Form20/"
CONTESTING = "https://elections.tn.gov.in/LokSabha2019/GELS2019_Contesting Can.pdf"
LGD = "33"  # the Local Government Directory's state code, which in.geo's constituency codes carry
STATION_SCHEME, SEGMENT_SCHEME, PC_SCHEME = "in.ps", "in.ac", "in.pc"
VOTES, REGISTERED, BALLOTS, INVALID = 1, 2, 3, 5
ALL, ELECTION_DAY = 0, 1
YEAR = 2019


def _ids(pipeline: str) -> Callable[..., str]:
    return Package(pipeline, pipeline, "ids").id


_GEO = _ids("in.geo")


def _wb(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join(["in.form20_wb", kind, *(str(part) for part in parts)])))


ELECTION = _wb("election", YEAR)
BODY = _wb("body", "lok-sabha")
ELECTION_PATH = f"in/{YEAR}-general"
key = _ids(SOURCE)
SOURCE_ID = key("source", "tn-2019")
FPTP = key("electoral_system", "fptp")


def pc_area(pc: int) -> str:
    return _GEO("area", PC_SCHEME, f"{LGD}{pc:02d}", "2008")


def ac_area(ac: int) -> str:
    return _GEO("area", SEGMENT_SCHEME, f"{LGD}{ac:03d}", "2008")


# ---- the constituencies and their segments -------------------------------------------------------------------------

# Each constituency: its name, its reservation, the number of candidates the table of contesting candidates gives,
# and its segments, as the site's Form 20 folders hold them (PC<pc>/AC<segment>.pdf).
PCS: dict[int, tuple[str, str | None, int, tuple[int, ...]]] = {
    1: ("Tiruvallur", "SC", 20, (1, 2, 4, 5, 6, 9)),
    2: ("Chennai North", None, 23, (10, 11, 12, 13, 15, 17)),
    3: ("Chennai South", None, 40, (22, 23, 24, 25, 26, 27)),
    4: ("Chennai Central", None, 31, (14, 16, 18, 19, 20, 21)),
    5: ("Sriperumbudur", None, 19, (7, 8, 28, 29, 30, 31)),
    6: ("Kancheepuram", "SC", 11, (32, 33, 34, 35, 36, 37)),
    7: ("Arakkonam", None, 19, (3, 38, 39, 40, 41, 42)),
    8: ("Vellore", None, 23, (43, 44, 45, 46, 47, 48)),
    9: ("Krishnagiri", None, 15, (51, 52, 53, 54, 55, 56)),
    10: ("Dharmapuri", None, 15, (57, 58, 59, 60, 61, 85)),
    11: ("Tiruvannamalai", None, 25, (49, 50, 62, 63, 64, 65)),
    12: ("Arani", None, 15, (66, 67, 68, 69, 70, 71)),
    13: ("Viluppuram", "SC", 13, (72, 73, 74, 75, 76, 77)),
    14: ("Kallakurichi", None, 24, (78, 79, 80, 81, 82, 83)),
    15: ("Salem", None, 22, (84, 86, 88, 89, 90, 91)),
    16: ("Namakkal", None, 29, (87, 92, 93, 94, 95, 96)),
    17: ("Erode", None, 20, (97, 98, 99, 100, 101, 102)),
    18: ("Tiruppur", None, 12, (103, 104, 105, 106, 113, 114)),
    19: ("Nilgiris", "SC", 10, (107, 108, 109, 110, 111, 112)),
    20: ("Coimbatore", None, 14, (115, 116, 117, 118, 120, 121)),
    21: ("Pollachi", None, 14, (119, 122, 123, 124, 125, 126)),
    22: ("Dindigul", None, 21, (127, 128, 129, 130, 131, 132)),
    23: ("Karur", None, 42, (133, 134, 135, 136, 138, 179)),
    24: ("Tiruchirappalli", None, 24, (139, 140, 141, 142, 178, 180)),
    25: ("Perambalur", None, 19, (137, 143, 144, 145, 146, 147)),
    26: ("Cuddalore", None, 21, (151, 152, 153, 154, 155, 156)),
    27: ("Chidambaram", "SC", 13, (148, 149, 150, 157, 158, 159)),
    28: ("Mayiladuthurai", None, 27, (160, 161, 162, 170, 171, 172)),
    29: ("Nagapattinam", "SC", 15, (163, 164, 165, 166, 168, 169)),
    30: ("Thanjavur", None, 12, (167, 173, 174, 175, 176, 177)),
    31: ("Sivaganga", None, 26, (181, 182, 184, 185, 186, 187)),
    32: ("Madurai", None, 27, (188, 189, 191, 192, 193, 194)),
    33: ("Theni", None, 30, (190, 197, 198, 199, 200, 201)),
    34: ("Virudhunagar", None, 28, (195, 196, 204, 205, 206, 207)),
    35: ("Ramanathapuram", None, 23, (183, 208, 209, 210, 211, 212)),
    36: ("Thoothukkudi", None, 37, (213, 214, 215, 216, 217, 218)),
    37: ("Tenkasi", "SC", 25, (202, 203, 219, 220, 221, 222)),
    38: ("Tirunelveli", None, 26, (223, 224, 225, 226, 227, 228)),
    39: ("Kanniyakumari", None, 0, (229, 230, 231, 232, 233, 234)),
}
SEGMENTS = sum(len(s) for *_, s in PCS.values())

# The assembly segments' names, for the segments whose sheets were reached.
SEGMENT_NAMES = {
    1: "Gummidipoondi", 2: "Ponneri (SC)", 3: "Tiruttani", 4: "Thiruvallur", 5: "Poonamallee (SC)", 6: "Avadi",
    7: "Maduravoyal", 8: "Ambattur", 9: "Madavaram", 10: "Thiruvottiyur", 11: "Dr. Radhakrishnan Nagar",
    12: "Perambur", 13: "Kolathur", 14: "Villivakkam", 15: "Thiru-Vi-Ka-Nagar (SC)", 16: "Egmore (SC)",
    17: "Royapuram", 18: "Harbour", 19: "Chepauk-Thiruvallikeni", 20: "Thousand Lights", 21: "Anna Nagar",
    22: "Virugampakkam", 23: "Saidapet", 24: "Thiyagarayanagar", 25: "Mylapore", 26: "Velachery",
    27: "Shozhinganallur", 28: "Alandur", 29: "Sriperumbudur (SC)", 30: "Pallavaram", 31: "Tambaram",
    32: "Chengalpattu", 33: "Thiruporur", 34: "Cheyyur (SC)", 35: "Madurantakam (SC)", 36: "Uthiramerur",
    37: "Kancheepuram", 38: "Arakkonam (SC)", 39: "Sholingur", 40: "Katpadi", 41: "Ranipet", 42: "Arcot",
    43: "Vellore", 44: "Anaikattu", 45: "Kilvaithinankuppam (SC)", 46: "Gudiyattam (SC)", 47: "Vaniyambadi",
    48: "Ambur", 49: "Jolarpet", 50: "Tiruppattur", 51: "Uthangarai (SC)", 52: "Bargur", 53: "Krishnagiri",
    54: "Veppanahalli", 55: "Hosur", 56: "Thalli", 57: "Palacodu", 58: "Pennagaram", 59: "Dharmapuri",
    60: "Pappireddippatti", 61: "Harur (SC)", 62: "Chengam (SC)", 63: "Tiruvannamalai", 64: "Kilpennathur",
    65: "Kalasapakkam", 66: "Polur", 67: "Arani", 68: "Cheyyar", 69: "Vandavasi (SC)", 70: "Gingee", 71: "Mailam",
    72: "Tindivanam (SC)", 73: "Vanur (SC)", 74: "Viluppuram", 75: "Vikravandi", 76: "Tirukkoyilur",
    77: "Ulundurpettai", 78: "Rishivandiyam", 79: "Sankarapuram", 80: "Kallakurichi (SC)", 81: "Gangavalli (SC)",
    82: "Attur (SC)", 83: "Yercaud (ST)", 85: "Mettur", 107: "Bhavanisagar (SC)", 108: "Udhagamandalam",
    109: "Gudalur (SC)", 113: "Tiruppur (North)",
}  # fmt: skip


def sheet_url(pc: int, ac: int) -> str:
    """The sheet's URL as the site links it; a few of its file names are in capitals or mixed case."""
    for u in (
        f"{SHEETS}PC{pc:02d}/AC{ac:03d}.pdf",
        f"{SHEETS}PC{pc:02d}/AC{ac:03d}.PDF",
        f"{SHEETS}PC{pc:02d}/Ac{ac:03d}.pdf",
    ):
        if _cached(u) is not None:
            return u
    return f"{SHEETS}PC{pc:02d}/AC{ac:03d}.pdf"


def _cached(url: str) -> raw.Artifact | None:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return None


# ---- reading a sheet -----------------------------------------------------------------------------------------------

type Word = tuple[float, float, float, float, str]
CELL = re.compile(r"\d+(?:[-/]?[A-Za-z]|\([A-Za-z]\))?")  # a number, or a station number with a letter: 73A, 50-A
NUMBER = re.compile(r"\d+")
# The ways of reading a page: lines by their vertical centres, or columns by their horizontal centres, each read in
# either direction.
WAYS = (("rows", False), ("columns", True), ("columns", False), ("rows", True))


@dataclass
class Row:
    serial: int | None
    station: str
    electors: int | None
    votes: list[int]
    valid: int
    rejected: int
    nota: int
    total: int


@dataclass
class Fit:
    """A line read as a station's figures: the words before the candidates, and the figures."""

    lead: list[tuple[float, str]]  # each word before the candidates: its position along the line, and its text
    votes: list[int]
    valid: int
    rejected: int | None  # None where the sheet has no rejected votes on the line
    nota: int
    total: int


@dataclass
class Sheet:
    url: str
    pages: int
    k: int
    way: str = ""
    rows: list[Row] = field(default_factory=list)
    printed: list[int] | None = None  # the total row: candidates, valid, NOTA, total
    electors: int | None = None
    rejected: bool = False  # whether the sheet prints rejected votes
    problems: list[str] = field(default_factory=list)


def _centre(w: Word, axis: str, edge: int = 0) -> float:
    """Where a word sits across its line: its centre, or (edge -1 or 1) the start or end of its box."""
    a, b = (w[1], w[3]) if axis == "rows" else (w[0], w[2])
    return (a + b) / 2 if edge == 0 else a if edge < 0 else b


def _along(w: Word, axis: str) -> float:
    return (w[0] + w[2]) / 2 if axis == "rows" else (w[1] + w[3]) / 2


def _groups(words: list[Word], axis: str, backward: bool, edge: int) -> list[list[Word]]:
    """The figures grouped into lines (or columns) by where they sit across them, each in reading order. Figures are
    aligned to the centre, the start, or the end of their cells, so each page is grouped all three ways."""
    tolerance = 3 if axis == "rows" else 5
    groups: list[list[Word]] = []
    for w in sorted(words, key=lambda w: _centre(w, axis, edge)):
        if groups and _centre(w, axis, edge) - _centre(groups[-1][-1], axis, edge) <= tolerance:
            groups[-1].append(w)
        else:
            groups.append([w])
    return [sorted(g, key=lambda w: _along(w, axis), reverse=backward) for g in groups]


def _fit(texts: list[str], k: int) -> tuple[int, list[int], int, int | None, int, int] | None:
    """Where a line's candidates begin, and its figures, for k candidates: the first run of k figures that adds up to
    the valid votes, with the valid, rejected, and NOTA votes adding up to the total. NOTA can come before the valid
    votes, or be counted in them."""
    n = [int(t) if NUMBER.fullmatch(t) else -1 for t in texts]  # -1 for a station number with a letter
    for i in range(len(n) - k):
        run = n[i : i + k]
        s = sum(run)
        if min(run) < 0 or s == 0:
            continue
        t = n[i + k : i + k + 5] + [-1] * 5
        if t[0] == s:  # valid, rejected, NOTA, total; or valid, NOTA, total
            if min(t[1:4]) >= 0 and s + t[1] + t[2] == t[3]:
                return i, run, s, t[1], t[2], t[3]
            if min(t[1:3]) >= 0 and s + t[1] == t[2]:
                return i, run, s, None, t[1], t[2]
        if t[0] >= 0 and t[1] == s:  # NOTA, valid, [rejected,] total
            if t[2] >= 0 and s + t[0] == t[2]:
                return i, run, s, None, t[0], t[2]
            if min(t[2:4]) >= 0 and s + t[0] + t[2] == t[3]:
                return i, run, s, t[2], t[0], t[3]
        if t[0] >= 0 and t[1] == s + t[0]:  # NOTA, valid with NOTA, [rejected,] total
            if min(t[2:4]) >= 0 and t[1] + t[2] == t[3]:
                return i, run, s, t[2], t[0], t[3]
            if t[2] == t[1]:
                return i, run, s, None, t[0], t[2]
    return None


def _clean(text: str) -> str:
    return text.strip(".,:;|")


def _fits(document: pymupdf.Document, k: int, axis: str, backward: bool) -> tuple[list[Fit], list[list[str]]]:
    """A sheet's lines read one way as stations' figures, and the number of lines with enough figures that do not
    read. Each page is grouped by the alignment that reads the most lines."""
    found: list[Fit] = []
    unread: list[list[str]] = []
    for page in document:
        # some sheets print each word twice in the same place
        words = {(round(w[0]), round(w[1]), w[4]): w for w in page.get_text("words")}
        cells = [(w[0], w[1], w[2], w[3], _clean(w[4])) for w in words.values() if CELL.fullmatch(_clean(w[4]))]
        best: tuple[list[Fit], list[list[str]]] | None = None
        for edge in (0, 1, -1):
            fits: list[Fit] = []
            missed: list[list[str]] = []
            for group in _groups(cells, axis, backward, edge):
                if len(group) < k + 3:
                    continue
                fit = _fit([w[4] for w in group], k)
                if fit is None:
                    missed.append([w[4] for w in group])
                    continue
                i, votes, valid, rejected, nota, total = fit
                lead = [(_along(w, axis), w[4]) for w in group[:i]]
                fits.append(Fit(lead, votes, valid, rejected, nota, total))
            if best is None or len(fits) > len(best[0]):
                best = (fits, missed)
        if best is not None:
            found += best[0]
            unread += best[1]
    return found, unread


def _clusters(positions: list[float], tolerance: float = 6) -> list[list[float]]:
    out: list[list[float]] = []
    for x in sorted(positions):
        if out and x - out[-1][-1] <= tolerance:
            out[-1].append(x)
        else:
            out.append([x])
    return out


def _printed(lines: list[list[str]], summed: list[int], k: int) -> bool:
    """Whether a line that does not add up as a station's is the sheet's total row: the candidates' sums in a run,
    followed by the NOTA and total sums, and the valid sum where the line prints it."""
    votes, valid, nota, total = summed[:k], summed[k], summed[k + 1], summed[k + 2]
    for texts in lines:
        n = [int(t) for t in texts if NUMBER.fullmatch(t)]
        for i in range(len(n) - k + 1):
            if n[i : i + k] == votes:
                after = n[i + k : i + k + 5]
                if nota in after and total in after and after.index(total) > after.index(nota):
                    return all(x in (valid, nota, total, 0) for x in after[: after.index(total)])
    return False


def _station(text: str) -> str:
    """A station's number as Form 20 prints it, without leading zeros or the marks around its letter: 10(A) is 10A."""
    m = re.fullmatch(r"(\d+)\W*([A-Za-z]?)\)?", text)
    if m is None:
        return text
    digits, letter = m.groups()
    return (digits.lstrip("0") or digits) + letter.upper()


ELECTORS = re.compile(
    r"Electors\s+in\s+(?:the\s+)?Assembly[^0-9]{0,60}?(\d{4,7})\b|Electors[^0-9]{0,80}?Segment[^0-9]{0,20}(\d{4,7})\b",
    re.IGNORECASE,
)


def read(url: str, k: int) -> Sheet:
    """A segment's sheet, for a constituency of k candidates."""
    document = pymupdf.open(raw.cached(SOURCE, url).path)
    sheet = Sheet(url, len(document), k)
    best: tuple[list[Fit], list[list[str]], str, bool] | None = None
    for axis, backward in WAYS:
        fits, unread = _fits(document, k, axis, backward)
        if best is None or len(fits) > len(best[0]):
            best = (fits, unread, axis, backward)
    assert best is not None
    fits, unread, axis, backward = best
    sheet.way = f"{axis}{' backward' if backward else ''}"
    if len(fits) < 3:
        sheet.problems.append("no text layer with station rows" if not fits else "few lines read as stations")
        return sheet
    _rows(sheet, fits, unread, backward)
    text = " ".join(" ".join(page.get_text().split()) for page in document[:1])
    if m := ELECTORS.search(text):
        electors = int(m.group(1) or m.group(2))
        cast = sum(r.total for r in sheet.rows)
        if cast <= electors <= 2.5 * cast:
            sheet.electors = electors
    return sheet


def _rows(sheet: Sheet, fits: list[Fit], unread: list[list[str]], backward: bool) -> None:
    """The stations from the lines read, the lead words told apart by where they sit, and the checks."""
    # The lead columns: the places where words before the candidates sit on most lines.
    led = [f for f in fits if f.lead]
    columns = [statistics.median(c) for c in _clusters([x for f in led for x, _ in f.lead]) if len(c) >= 0.3 * len(led)]
    columns.sort(reverse=backward)  # in reading order

    def cell(f: Fit, j: int) -> str | None:
        near = [t for x, t in f.lead if abs(x - columns[j]) <= 6]
        return near[0] if len(near) == 1 else None

    # The station's number sits in a column of small, mostly distinct numbers, with the serial number before it;
    # the electors, where printed, sit in a column of figures at least the station's total; other columns are figures
    # in the stations' names, such as postal codes.
    roles: dict[str, int] = {}
    numbered: list[int] = []
    for j in range(len(columns)):
        values = [v for f in led if (v := cell(f, j)) is not None and CELL.fullmatch(v)]
        small = [v for v in values if int(re.sub(r"\D", "", v)) < 5000]
        if len(small) >= 0.5 * len(led) and len(set(small)) >= 0.8 * len(small):
            numbered.append(j)
        counts = [(int(v), f.total) for f in led if (v := cell(f, j)) is not None and NUMBER.fullmatch(v)]
        if j > 0 and len(counts) >= 0.95 * len(led) and all(v >= t for v, t in counts):
            roles.setdefault("electors", j)
    numbered = [j for j in numbered if j != roles.get("electors")]
    if numbered:
        roles["station"] = numbered[-1]
        if len(numbered) >= 2:
            roles["serial"] = numbered[0]
    stations: list[tuple[Row, Fit]] = []
    loose: list[Fit] = []
    for f in fits:
        station = cell(f, roles["station"]) if "station" in roles else None
        if station is None:
            loose.append(f)
            continue
        serial = cell(f, roles["serial"]) if "serial" in roles else None
        electors = cell(f, roles["electors"]) if "electors" in roles else None
        row = Row(
            int(serial) if serial and NUMBER.fullmatch(serial) else None,
            _station(station),
            int(electors) if electors and NUMBER.fullmatch(electors) else None,
            f.votes,
            f.valid,
            f.rejected or 0,
            f.nota,
            f.total,
        )
        stations.append((row, f))
    sheet.rows = [r for r, _ in stations]
    sheet.rejected = any(f.rejected is not None for _, f in stations)
    if any((f.rejected is None) != (not sheet.rejected) for _, f in stations):
        sheet.problems.append("the rejected votes are printed on some lines and not on others")
    summed = [*(sum(r.votes[j] for r in sheet.rows) for j in range(sheet.k))]
    summed += [sum(r.valid for r in sheet.rows), sum(r.nota for r in sheet.rows), sum(r.total for r in sheet.rows)]
    totals = [[*f.votes, f.valid, f.nota, f.total] for f in loose]
    if summed in totals or _printed(unread, summed, sheet.k):
        sheet.printed = summed
    elif totals:
        sheet.problems.append(f"the columns sum to {summed}, the sheet prints {totals[-1]}")
    else:
        sheet.problems.append("no printed total row")
    if extra := [t for t in totals if t != summed and t[-1] < summed[-1]]:
        sheet.problems.append(f"{len(extra)} lines of figures without a station number")
    if repeated := [s for s, n in Counter(r.station for r in sheet.rows).items() if n > 1]:
        sheet.problems.append(f"repeated stations {repeated[:5]}")
    if "electors" in roles and any(r.electors is None or r.electors < r.total for r in sheet.rows):
        sheet.problems.append("a station's electors are missing or fewer than its votes")
    for j in range(sheet.k):
        pairs = [(r.serial, int(re.sub(r"\D", "", r.station) or -1)) for r in sheet.rows]
        same = sum(r.votes[j] in pair for r, pair in zip(sheet.rows, pairs, strict=True))
        if same > 0.5 * len(sheet.rows) and len(sheet.rows) > 10:
            sheet.problems.append(f"candidate column {j + 1} repeats the serial or station numbers")
            break


# ---- the candidates ------------------------------------------------------------------------------------------------

BSP, DMK, AIADMK = "Bahujan Samaj Party", "Dravida Munnetra Kazhagam", "All India Anna Dravida Munnetra Kazhagam"
INC, PMK, MNM, NTK = "Indian National Congress", "Pattali Makkal Katchi", "Makkal Needhi Maiam", "Naam Tamilar Katchi"
DMDK, TIK = "Desiya Murpokku Dravida Kazhagam", "Tamilnadu Ilangyar Katchi"
ACDP, RPIA = "Anti Corruption Dynamic Party", "Republican Party of India (A)"
DMSK, PPI = "Desiya Makkal Sakthi Katchi", "Pyramid Party of India"
IND = None  # an independent
UNKNOWN = ""  # a sheet that prints no parties

# Each constituency's candidates in ballot order, with their parties, as the headings of its sheets print them (names
# as one sheet spells them, a name a sheet breaks over two lines joined; a party a sheet abbreviates spelled out where
# another sheet of the State spells it out). The sheet each list is read from is named.
CANDIDATES: dict[int, tuple[str, list[tuple[str, str | None]]]] = {
    1: ("AC005", [
        ("R. Anbuchezhian", BSP), ("Dr. P. Venugopal", AIADMK), ("Dr. K. Jayakumar", INC), ("C. Kalanithi", ACDP),
        ("D. Ravi Pariyanar", RPIA), ("Logarangan. M", MNM), ("M. Vetriselvi", NTK), ("G. Arulraj", IND),
        ("D. Anandaraj", IND), ("K. Kamalanathan", IND), ("V. Gunasekaran", IND), ("K. Chandrasekar", IND),
        ("S. Sargunam", IND), ("C. Bharath", IND), ("A. Manikandan", IND), ("Murugesan", IND), ("Pon. Raja", IND),
        ("N. Vikraman", IND), ("Vijaya. R.", IND), ("G. George", IND),
    ]),
    2: ("AC012", [
        ("Alagapuram R. Mohanraj", DMDK), ("Dr. Kalanidhi Veeraswamy", DMK), ("S. Robert Gnana Sekar", BSP),
        ("M.L. Ravi", DMSK), ("Kamesh", TIK), ("Kaliyammal. P", NTK),
        ("J. Sebastin", "Socialist Unity Centre of India (Communist)"), ("K. Prabhakaran", "Makkalatchi Katchi"),
        ("L. Praveen Kumar", "Peoples Party of India (Secular)"), ("A. G. Mourya", MNM), ("R. Arulmurugan", IND),
        ("S. Ganesh", IND), ("N. Satheesh Kannan", IND), ("P. Santhana Krishnan", IND), ("V. Saravanan", IND),
        ("G. Srinivasan", IND), ("S. Selvaraj", IND), ("C. Dhanraj", IND), ("J. Dharanidharan", IND),
        ("A.G. Damodharan", IND), ("S. Prithiviraj", IND), ("P. Marimuthu", IND), ("L. Raj", IND),
    ]),
    3: ("AC024", [
        ("Kottur R. Kumar", BSP), ("T. Sumathy (a) Thamizhachi Thangapandian", DMK), ("Dr. J. Jayavardhan", AIADMK),
        ("Saikumar S", "Socialist Unity Centre of India (Communist)"), ("S. Srinivasan (a) Power Star", RPIA),
        ("V. Thirunavukarasu", "Makkalatchi Katchi"), ("Dr. A. Naresh", TIK), ("K. Murali Krishnan", PPI),
        ("R. Rangarajan", MNM), ("R. Johnson", "Indian Christian Front"), ("M.A. Jayakumar", DMSK),
        ("A.J. Sherine", NTK), ("Agni Sriramachandran", IND), ("S. Ashok", IND), ("P. Azhagiri", IND),
        ("S. Elankumaran", IND), ("R. Ganesan", IND), ("K. Kannan", IND), ("B. Karthikeyan", IND),
        ("D. Karthick", IND), ("K. Saravana Perumal", IND), ("I. Chidambara Anantha Raja (a) Icarr Weeoui", IND),
        ("Sibi Chakkaravarthy", IND), ("K. Sudhagar", IND), ("N. Subramani", IND),
        ("Dr. E. Subaya (a) Esakki Subaya", IND), ("E. Dhanasekaran", IND), ("G. Dhanasekaran", IND),
        ("G. Devasahayam", IND), ("Kupal G. Devadoss", IND), ("S. Manova", IND), ("Murthy. M", IND),
        ("A. Ravichandran", IND), ("M. Radha", IND), ("B. Raji", IND), ("M. Rajeswari Priya", IND), ("Rosi", IND),
        ("J. Janci Rani", IND), ("Farmer K. Jayaraman", IND), ("D. Hari Haran", IND),
    ]),
    4: ("AC021", [
        ("Dayanidhi Maran", DMK), ("Parthasarathy M", BSP), ("Kameela Nasser", MNM), ("Karnan. C.S", ACDP),
        ("Dr. Karthikeyan R", NTK), ("Geethalakshmi. V.R.", PPI), ("Sasikumar. S.", TIK), ("Sam Paul. S.R.", PMK),
        ("Suresh Babu. D.", DMSK), ("Najimunnissa", "Anaithu Makkal Katchi"),
        ("Valarmathi. K.", "Akila India Vallalar Peravai"), ("Jitendra Kumar Jain.", RPIA),
        ("Sheik Mohamed @ Dhehlan Baqavi", "Social Democratic Party of India"), ("Raghavan. M.", IND),
        ("Radhakrishnan. V.", IND), ("Dr. Gunasekar. N.", IND), ("Kuppusamy. K.", IND), ("Govindaraj. L", IND),
        ("Chandranathan. S.", IND), ("Samuel Paul", IND), ("Sam Paul", IND), ("Tamilarasan. V.V.", IND),
        ("Dinakaran G", IND), ("Nasar. K.", IND), ("Prabakaran. K.M.", IND), ("Prabhakaran. N.", IND),
        ("Pushparaj. J.L.", IND), ("Madhanagopal. T", IND), ("Ravichandran. M", IND), ("Raj Ramchand", IND),
        ("Vaithiyanathan. R", IND),
    ]),
    5: ("AC030", [
        ("Antony", BSP), ("Baalu, T.R.", DMK), ("Godwin Shadrach, Sr.", "Tamizhaga Murpokku Makkal Katchi"),
        ("Palanivel, K.", "Communist Party of India (Marxist-Leninist) (Liberation)"), ("Mahendran, H.", NTK),
        ("Rajasekaran, S.", ACDP), ("Vaithilingam, A.", PMK), ("Sridhar, M.", MNM), ("Anantha Raman, R.K.", IND),
        ("Ayodhi, L. Dr.", IND), ("Indian, P.N.K.", IND), ("Sasikumar, A.", IND), ("Singa Rajan, S.P.", IND),
        ("Tambaram Narayanan, G.", IND), ("Muthumaran, K.", IND), ("Raja Marimuthu", IND), ("Vasanthi, S.", IND),
        ("Viruthagiri, A.", IND), ("Vaithiyalingam, R.", IND),
    ]),
    6: ("AC034", [
        ("Selvam, G", DMK), ("Sekar, D", BSP), ("Maragatham, K", AIADMK), ("Sivaranjini, D", NTK),
        ("Elangovan, M", IND), ("Devarajan, C", IND), ("Pon Jeyaraman, S.P", IND), ("Maragadam, M", IND),
        ("Munusamy, A", IND), ("Ramesh, S", IND), ("Vinoth Raj, R", IND),
    ]),
    7: ("AC038", [
        ("D. Doss", BSP), ("S. Jagathrakshakan", DMK), ("M. Savitha", "Ambedkarite Party of India"),
        ("Y.R. Pavendhan", NTK), ("A.K. Moorthy", PMK), ("Rajendran, N.", MNM), ("R. Elam Vazhuthi", IND),
        ("Panchu. Udayakumar", IND), ("B. Ganesan", IND), ("M.S. Krishnan", IND),
        ("Dr. T.M.S. Sadhu Muthu Krishnan Erajendran", IND), ("P.S. Suresh", IND), ("S. Shettu", IND),
        ("M. Natarajan", IND), ("N.G. Parthiban", IND), ("M. Parthiban", IND), ("G. Moorthy", IND),
        ("C. Moorthy", IND), ("R. Ramesh", IND),
    ]),
    9: ("AC052", [
        ("Dr. A. Chellakumar", INC), ("K.P. Munusamy", AIADMK), ("Madhusoodhanan. N", NTK),
        ("Sri Karunya Subrahmanyam", MNM), ("N. Aslam Rahman Sheriff", IND), ("Ajaz. S", IND),
        ("S. Ganesa Kumar", IND), ("T.V.S. Gandhi", IND), ("K. Kuppan", IND), ("M. Kumaresan", IND),
        ("B. Govindan", IND), ("Devappa. Y", IND), ("Nagesh. L", IND), ("M. Meena", IND), ("N. Srinivasa", IND),
    ]),
    10: ("AC059", [
        ("Sivanandham. C.", BSP), ("DNV Senthilkumar. S.", DMK), ("Annadhurai. K.", "GPI"),
        ("Anbumani Ramadoss", PMK), ("Rajasekar. D.", MNM), ("Rukmanidevi. R.", NTK), ("Arivazhagan. P.", IND),
        ("Dr. Elango. S.", IND), ("Sakthivel. V.", IND), ("Saravanan. S.", IND), ("Durai. S.", IND),
        ("Dr. Padmarajan. K.", IND), ("Palaniappan. P.", IND), ("Manivasagam. C.", IND), ("Venkatachalam. C.", IND),
    ]),
    11: ("AC049", [
        ("Annadurai. C.N.", DMK), ("Agri Krishnamurthy. S.S.", AIADMK), ("Babu. B.", BSP), ("Arul. R.", MNM),
        ("Kalasthri. S.", "All India Uzhavargal Uzhaippalargal Katchi"), ("Rameshbabu. R.", NTK),
        ("Annadurai. T.S.", IND), ("Annadurai. S", IND), ("Annadurai. B.", IND), ("Anbalagan. M.", IND),
        ("Indiramohan. P.", IND), ("Udhayakumar. P.S.", IND), ("Ayyappan. M.", IND), ("Karuna. S.", IND),
        ("Kalaimani. S.", IND), ("Krishnamoorthy. A.", IND), ("Krishnamurthi. R.", IND), ("Sivagururaj. T.", IND),
        ("Gnanasekar. A.", IND), ("Padavettan. M.", IND), ("Raghunathan. K.", IND), ("Rajendiran. T.", IND),
        ("Vigneshwaran. A.", IND), ("Vijayan. A.", IND), ("Velu. R.", IND),
    ]),
    12: ("AC066", [
        ("Elumalai. V", AIADMK), ("Sivaprakash. K", BSP), ("Vishnu Prasad. M.K.", INC), ("Sakthivel. K", TIK),
        ("Sundar. K", ACDP), ("Tamizharasi. A", NTK), ("Shaji. V", MNM), ("Elumalai. K", IND), ("Elumalai. S", IND),
        ("Kothandapani. B", IND), ("Senjeraja. K.N.K.", IND), ("Senthamizhan. G", IND), ("Perumal. M", IND),
        ("Ramamurthy. S", IND), ("Ramamurthy. C", IND),
    ]),
    13: ("AC073", [
        ("Kaliyamoorthy G", BSP), ("Ravikumar D", DMK), ("Abirami P", TIK), ("Anbin Poyyamozhi S", MNM),
        ("Prakalatha D", NTK), ("Raja S", "AIMK"), ("Vadivel Ravanan S", PMK), ("Arasan K", IND),
        ("Anbalagan T", IND), ("Ganapathy N", IND), ("Kathirvel M", IND), ("Desingu A", IND),
        ("Rajasekaran M", IND),
    ]),
    14: ("AC079", [
        ("Gautham Sigamani Pon", DMK), ("Sakthivel S", BSP), ("Sudhish L K", DMDK), ("Ganesh H", MNM),
        ("Chandramohan M", "VMMK"), ("Sharfudeen S", NTK), ("Kannan S", IND), ("Kumar K R", IND),
        ("Komugi Maniyan M", IND), ("Govindasamy A", IND), ("Sathishkumar A", IND), ("Sathish Kumar G", IND),
        ("Sathees Kumar R", IND), ("Chandrasekaran V", IND), ("Siva Kumar P", IND), ("Sumathi B", IND),
        ("Selvam D", IND), ("Nagarajan S", IND), ("Prabhu A", IND), ("Manikandan C", IND), ("Mannan M P", IND),
        ("Mayilamparai Mari A", IND), ("Ramachandran K", IND), ("Ramadoss R", IND),
    ]),
    18: ("AC113", [
        ("Ayyanar, C.", UNKNOWN), ("Anandan, M.S.M.", UNKNOWN), ("Subbarayan, K.", UNKNOWN),
        ("Chandirakumar, V.S.", UNKNOWN), ("Jaganathan, P.", UNKNOWN), ("Kathiresan, L.", UNKNOWN),
        ("Kanagaraj, P.", UNKNOWN), ("Kumar, D.", UNKNOWN), ("Senthilvel, A.", UNKNOWN), ("Selvam, S.R.", UNKNOWN),
        ("Rajkumar, S.", UNKNOWN), ("Jaganathan, P.", UNKNOWN),
    ]),
    19: ("AC107", [
        ("Ashok Kumar, R.", BSP), ("Raja, A.", DMK), ("Thiyagarajan, M.", AIADMK), ("Rajenthiran, N.", MNM),
        ("Arumukam, P.", IND), ("Ramaswamy, M.", IND), ("Subramani, M.", IND), ("Nagarajan, A.", IND),
        ("Rajarathinam, M.", IND), ("Raja, K.", IND),
    ]),
}  # fmt: skip


def person_name(text: str) -> str:
    """A name as a sheet prints it, with an all-capitals word in title case."""
    return " ".join(w.capitalize() if w.isupper() and len(w) > 2 else w for w in text.split())


# ---- putting a constituency together -------------------------------------------------------------------------------


@dataclass
class Seat:
    pc: int
    name: str
    reserved: str | None
    segments: tuple[int, ...]
    candidates: list[tuple[str, str | None]]
    sheets: dict[int, Sheet]  # the segments whose sheets check out
    left_out: dict[int, str]  # the others, and why


def _reason(sheet: Sheet | None) -> str:
    if sheet is None:
        return "no copy of its sheet was reached"
    if any("no text layer" in p for p in sheet.problems):
        return "a scan without a text layer of its figures"
    if any("few lines" in p for p in sheet.problems):
        return "its text layer does not read as the sheet's lines"
    if any(p.startswith("the columns sum to") for p in sheet.problems):
        return "its stations' figures that add up do not reach the total row it prints"
    return "; ".join(sheet.problems[:2])


@functools.cache
def seats() -> list[Seat]:
    out: list[Seat] = []
    for pc, (name, reserved, k, segments) in PCS.items():
        good: dict[int, Sheet] = {}
        left: dict[int, str] = {}
        for ac in segments:
            url = sheet_url(pc, ac)
            sheet = read(url, k) if k and _cached(url) else None
            if sheet is None or sheet.problems:
                left[ac] = _reason(sheet)
            else:
                good[ac] = sheet
        listed = CANDIDATES[pc][1] if pc in CANDIDATES else []
        if good and len(listed) != k:
            raise ValueError(f"PC {pc}: {len(listed)} candidates listed, the table gives {k}")
        out.append(Seat(pc, name, reserved, segments, listed, good, left))
    return out


# ---- the package ---------------------------------------------------------------------------------------------------


PACKAGE = f"{SOURCE}/ls-2019-tn"

METHOD = (
    "Counted at each polling station on the voting machines, as the Form 20 Part I sheet of each assembly segment "
    "prints it (Chief Electoral Officer, Tamil Nadu), read from the sheets' text layer with each word's position. A "
    "sheet is kept only when every station's candidates add up to its valid votes, its valid, rejected, and NOTA "
    "votes to its total, and every column to the total row the sheet prints. Polling stations are numbered as the "
    "sheets number them (an auxiliary station keeps its letter) and nested in their assembly segment, which carries "
    "the segment's electors where the sheet prints them; a station carries its electors where its sheet prints them. "
    "Ballots cast are the station's total votes (valid, rejected, and NOTA); rejected votes are stored where the sheet "
    "prints them, and tendered votes are not stored. Postal ballots are counted for the whole constituency on Part II "
    "and are not on the segment sheets, so the set holds the machine votes only. The candidates, their ballot order, "
    "and their parties are as the sheets' headings print them, and their number is the one the Chief Electoral "
    "Officer's table of contesting candidates gives; the declared winner is not on Form 20, so no outcome is recorded."
)


def packages() -> list[str]:
    return [PACKAGE] if any(s.sheets for s in seats()) else []


def _value(rows: list[dict[str, object]], rs: str, unit: str, option: str | None, channel: int, measure: int, v: int):
    rows.append(
        {
            "result_set_id": rs,
            "unit_id": unit,
            "option_id": option,
            "channel": channel,
            "measure": measure,
            "count_round": 0,
            "flags": 0,
            "value": v,
        }
    )


def election() -> Package:
    p = Package(
        PACKAGE,
        SOURCE,
        "The 2019 Lok Sabha general election in Tamil Nadu by polling station, from the Form 20 sheets the Chief "
        "Electoral Officer posted, with each constituency's candidates and parties as the sheets print them",
    )
    p.external |= {INDIA, ELECTION, BODY, SEGMENT_SCHEME, STATION_SCHEME}
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=INDIA,
        valid_from=date(1952, 1, 1),
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
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Chief Electoral Officer, Tamil Nadu",
        title="Form 20 final result sheets and table of contesting candidates, 2019 Lok Sabha general election, "
        "Tamil Nadu",
        url=SITE,
        license="Official publication of the Election Commission of India",
        attribution="Chief Electoral Officer, Tamil Nadu, Form 20 final result sheets",
        notes=f"The sheets are posted per constituency and segment under {SHEETS}; the site answers only visitors "
        "in India, so the copies were saved through an Indian exit and a few from web.archive.org captures of the "
        "same URLs.",
    )
    p.artifact(raw.cached(SOURCE, CONTESTING), SOURCE_ID)
    held = [s for s in seats() if s.sheets]
    parties: dict[str, str] = {}
    for seat in held:
        for _, party in seat.candidates:
            if party and party not in parties:
                parties[party] = key("party", "tn", party.lower())
                p.add("party", id=parties[party], kind="party", polity_id=INDIA)
                p.name(parties[party], party, "en", "official")
    units: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    for seat in held:
        _contest(p, seat, parties, units, values)
    p.frame("reporting_unit", pl.DataFrame(units, infer_schema_length=None))
    p.frame("result_value", pl.DataFrame(values, infer_schema_length=None))
    return p


def _contest(
    p: Package, seat: Seat, parties: dict[str, str], units: list[dict[str, object]], values: list[dict[str, object]]
) -> None:
    tag = f"{YEAR}:TN:{seat.pc}"
    contest = key("contest", tag)
    district = pc_area(seat.pc)
    p.external.add(district)
    p.add(
        "contest",
        id=contest,
        election_id=ELECTION,
        kind="body_seats",
        body_id=BODY,
        district_area_id=district,
        magnitude=1,
        round_number=1,
        electoral_system_id=FPTP,
        default_result_kind="counted",
    )
    label = seat.name + (f" ({seat.reserved})" if seat.reserved else "")
    p.name(contest, f"{YEAR} Lok Sabha election in {label}, Tamil Nadu", "en", "official")
    p.add("slug", path=f"{ELECTION_PATH}/tn/{seat.pc}-{changeset.slug(seat.name)}", entity_id=contest, is_current=True)
    options: list[str] = []
    for position, (printed, party) in enumerate(seat.candidates, start=1):
        option, person = key("option", tag, position), key("person", tag, position)
        name = person_name(printed)
        p.add("person", id=person)
        p.name(person, name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=parties[party] if party else None,
            ballot_position=position,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=printed,
            member_of_party_id=parties[party] if party else None,
            is_independent=True if party is IND else None,
        )
        options.append(option)
    nota = key("option", tag, "nota")
    p.add(
        "contest_option",
        id=nota,
        contest_id=contest,
        kind="none_of_the_above",
        ballot_position=len(options) + 1,
        ballot_label="NOTA",
    )
    rs = key("result_set", tag)
    expected = len(seat.segments)
    p.add(
        "result_set",
        id=rs,
        election_id=ELECTION,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=round(len(seat.sheets) / expected, 4),
    )
    body = METHOD + f" Candidates as the heading of the sheet for segment {CANDIDATES[seat.pc][0][2:].lstrip('0')}."
    body += f" Segments read: {', '.join(str(ac) for ac in seat.sheets)} of {expected}."
    if seat.left_out:
        body += " Left out: " + "; ".join(f"segment {ac}, {why}" for ac, why in seat.left_out.items()) + "."
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=body, lang="en")
    for ac, sheet in seat.sheets.items():
        p.artifact(raw.cached(SOURCE, sheet.url), SOURCE_ID)
        area = ac_area(ac)
        p.external.add(area)
        segment = key("unit", tag, ac)
        units.append(
            {
                "id": segment,
                "polity_id": INDIA,
                "scheme_code": SEGMENT_SCHEME,
                "code": f"{tag}:{ac}",
                "area_relation": "equals",
                "area_id": area,
                "kind": "standard",
            }
        )
        p.name(segment, f"{ac} {SEGMENT_NAMES[ac]}", "en", "official")
        if sheet.electors:
            _value(values, rs, segment, None, ALL, REGISTERED, sheet.electors)
        for r in sheet.rows:
            unit = key("unit", tag, ac, r.station)
            units.append(
                {
                    "id": unit,
                    "polity_id": INDIA,
                    "scheme_code": STATION_SCHEME,
                    "code": f"{tag}:{ac}:{r.station}",
                    "parent_id": segment,
                    "area_relation": "within",
                    "area_id": area,
                    "kind": "standard",
                }
            )
            for option, votes in zip(options, r.votes, strict=True):
                _value(values, rs, unit, option, ELECTION_DAY, VOTES, votes)
            _value(values, rs, unit, nota, ELECTION_DAY, VOTES, r.nota)
            if sheet.rejected:
                _value(values, rs, unit, None, ELECTION_DAY, INVALID, r.rejected)
            _value(values, rs, unit, None, ELECTION_DAY, BALLOTS, r.total)
            if r.electors is not None:
                _value(values, rs, unit, None, ALL, REGISTERED, r.electors)


def coverage() -> dict[str, object]:
    """Each constituency: the segments read, and why the others are not."""
    return {
        str(s.pc): {
            "name": s.name,
            "read": list(s.sheets),
            "segments": list(s.segments),
            "left_out": {str(ac): why for ac, why in s.left_out.items()},
        }
        for s in seats()
    }
