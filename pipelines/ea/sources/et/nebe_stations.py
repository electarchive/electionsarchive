"""Ethiopia's 2026 polling stations and constituencies from the National Election Board of Ethiopia (NEBE). The
regions the board's older results define come from `ea.sources.et.nebe`; this module adds the regions formed since.

The board publishes twenty polling-station lists as PDF tables (one per region for manually registered voters, and one
per region for technology-registered voters) and, for the seventh general election, a summary plus one constituency
list per region. Tigray is not listed. Each station code splits into the region, the House of Peoples'
Representatives (HoPR) constituency, the woreda, the zone, the kebele, and the station.
"""

import difflib
import functools
import re
import statistics
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from uuid import uuid5

import shapely

from ea import package, raw
from ea.package import Package

SOURCE = "et.nebe_stations"
BASE = "https://nebe.org.et"
STATIONS_PAGE = f"{BASE}/en/List_of_polling_stations"
ELECTION_PAGE = f"{BASE}/en/7th-General-Election"
ETHIOPIA = str(uuid5(package.NAMESPACE, "polity\x1fET"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fETH"))
SKIP = ("Proc", "Proclamtion")
CODE = re.compile(r"\d{12,13}")
DECIMAL = re.compile(r"-?\d{1,3}\.\d{3,}")  # a coordinate; shorter decimals are parts of names
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
GEEZ = re.compile(r"[ሀ-᎟ⶀ-⷟꬀-꬯]")
LATIN = re.compile(r"[A-Za-z]")
ETHIOPIA_BOX = (3.0, 15.5, 32.5, 48.5)  # south, north, west, east


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    glued: bool = False  # a code that the source ran into the word before it

    @property
    def mid(self) -> float:
        return (self.y0 + self.y1) / 2


def _links(page: str) -> list[str]:
    html = raw.cached(SOURCE, page).path.read_text(encoding="utf-8", errors="replace")
    found = dict.fromkeys(re.findall(r'href="([^"]*\.pdf)"', html))
    return [BASE + link if link.startswith("/") else link for link in found if not any(s in link for s in SKIP)]


def fetch() -> None:
    for page in (STATIONS_PAGE, ELECTION_PAGE):
        raw.fetch(SOURCE, page, suffix=".html")
        for url in _links(page):
            raw.fetch(SOURCE, url, suffix=".pdf")


def station_files() -> list[str]:
    return sorted(_links(STATIONS_PAGE))


def constituency_files() -> list[str]:
    return sorted(_links(ELECTION_PAGE))


def pages(url: str) -> Iterator[list[Word]]:
    path = raw.cached(SOURCE, url).path
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    for page in out.split("<page ")[1:]:
        yield [
            Word(float(a), float(b), float(c), float(d), _unescape(t))
            for a, b, c, d, t in WORD.findall(page)
            if t.strip()
        ]


def _unescape(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&amp;", "&")
    )


@dataclass
class Row:
    url: str
    code: str
    left: list[Word]
    right: list[Word]
    page: int = 0

    @property
    def parts(self) -> tuple[int, int, int, int, int, int]:
        digits = self.code
        region = len(digits) - 11
        return (
            int(digits[:region]),
            int(digits[region : region + 3]),
            int(digits[region + 3 : region + 5]),
            int(digits[region + 5 : region + 7]),
            int(digits[region + 7 : region + 9]),
            int(digits[region + 9 :]),
        )


def table_rows(url: str) -> tuple[list[Row], int]:
    """Every station row of a list, and the number of words in the table that no row claimed."""
    found: list[Row] = []
    loose = 0
    for number, page in enumerate(pages(url)):
        anchors = sorted((w for w in page if CODE.fullmatch(w.text)), key=lambda w: w.mid)
        if not anchors:
            continue
        top, bottom = anchors[0].y0 - 6, anchors[-1].y1 + 6
        claimed: dict[int, list[Word]] = defaultdict(list)
        for word in page:
            if not top <= word.mid <= bottom or CODE.fullmatch(word.text):
                continue
            nearest = min(range(len(anchors)), key=lambda i: abs(anchors[i].mid - word.mid))
            if abs(anchors[nearest].mid - word.mid) > 4.0:
                loose += 1
                continue
            claimed[nearest].append(word)
        for i, anchor in enumerate(anchors):
            words = sorted(claimed[i], key=lambda w: w.x0)
            found.append(
                Row(
                    url,
                    anchor.text,
                    [w for w in words if w.x0 < anchor.x0],
                    [w for w in words if w.x0 > anchor.x0],
                    number,
                )
            )
    return found, loose


FIELDS = ("region", "zone", "woreda", "kebele", "hopr")
LONG = {"zone", "woreda", "kebele"}


def _matches(word: Word, value: int, long: bool) -> bool:
    text = word.text
    return text.isdigit() and (int(text) == value or (long and len(text) >= 3 and int(text[-2:]) == value))


@dataclass
class Layout:
    code: dict[str, float] = field(default_factory=dict)  # x of each code column
    rc: float = 0.0  # x of the RC constituency code
    extras: list[float] = field(default_factory=list)  # x of numeric columns between the RC name and the station code
    tolerance: float = 2.5


def _clusters(xs: list[float], tolerance: float = 1.5) -> list[tuple[float, int]]:
    found: list[list[float]] = []
    for x in sorted(xs):
        if found and x - found[-1][-1] <= tolerance:
            found[-1].append(x)
        else:
            found.append([x])
    return [(statistics.fmean(c), len(c)) for c in found]


DIGIT = 2.9  # the width of a digit in these tables, in points


def _unglue(left: list[Word], x: float, after: int, value: int | None) -> list[Word]:
    """Split a cell that runs a name into its code, as `ጎንደር4` does, putting the code at its column."""
    for i, word in enumerate(left):
        if i <= after or word.x0 > x - 1 or word.x1 < x + 0.5:
            continue
        trailing = re.search(r"\d+$", word.text)
        if trailing is None or len(trailing.group()) == len(word.text) and word.text.isdigit() and value is None:
            continue
        if value is not None:
            digits = str(value)
            if word.text.endswith(f"{value:02d}") and len(word.text) > 2:
                digits = f"{value:02d}"
            if not word.text.endswith(digits) or len(word.text) == len(digits):
                continue
        else:
            digits = trailing.group()[-max(1, round((word.x1 - x) / DIGIT)) :]
            if len(digits) == len(word.text):
                continue
        share = (word.x1 - word.x0) * len(digits) / len(word.text)
        name = Word(word.x0, word.y0, word.x1 - share, word.y1, word.text[: -len(digits)], word.glued)
        return [*left[:i], name, Word(x, word.y0, word.x1, word.y1, digits, True), *left[i + 1 :]]
    return left


def calibrate(rows: list[Row]) -> Layout:
    """Find the x of each numeric column from where the codes the station code states appear, across the whole list."""
    layout = Layout()
    body = [(r, r.parts) for r in rows]
    n = len(body)
    candidates: list[list[tuple[float, int]]] = []
    for step, name in enumerate(FIELDS):
        xs: list[float] = []
        for row, parts in body:
            value = (parts[0], parts[3], parts[2], parts[4], parts[1])[step]
            xs += [w.x0 for w in row.left[1:] if _matches(w, value, name in LONG)]
            xs += [
                w.x1 - DIGIT * len(str(value))
                for w in row.left[1:]
                if not w.text.isdigit() and w.text.endswith(str(value)) and re.search(r"\D", w.text)
            ]
        candidates.append([(x, c) for x, c in _clusters(xs) if c >= 0.3 * n])
    # the code columns run left to right in this order; pick the increasing chain that explains the most rows,
    # leaving a column out when the codes it holds are not the ones the station code states
    best: tuple[int, list[float | None]] = (0, [])
    chains: list[tuple[int, list[float | None]]] = [(0, [])]
    for options in candidates:
        grown: list[tuple[int, list[float | None]]] = []
        for score, path in chains:
            grown.append((score, [*path, None]))
            floor = max((x for x in path if x is not None), default=-1e9)
            grown += [(score + count, [*path, x]) for x, count in options if x > floor + 6]
        chains = sorted(grown, key=lambda item: -item[0])[:50]
    best = chains[0]
    path = best[1]
    pool: list[tuple[float, int]] = []
    for row, _ in body:
        pool += [(w.x0, len(w.text)) for w in row.left[1:] if w.text.isdigit()]
    for i, x in enumerate(path):
        if x is not None:
            continue
        low = max((v for v in path[:i] if v is not None), default=-1e9)
        high = min((v for v in path[i + 1 :] if v is not None), default=1e9)
        between = [(x2, c) for x2, c in _clusters([x2 for x2, _ in pool]) if low + 6 < x2 < high - 6 and c >= 0.6 * n]
        if not between:
            raise ValueError(f"{rows[0].url}: no column for the {FIELDS[i]} code")
        path[i] = max(between, key=lambda item: item[1])[0]
    layout.code = dict(zip(FIELDS, [x for x in path if x is not None], strict=True))
    last: list[float] = []
    second: list[float] = []
    numeric: list[tuple[float, int]] = []
    for row, _ in body:
        numbers = [w for w in row.left if w.text.isdigit() and w.x0 > layout.code["hopr"] + 8]
        if numbers:
            last.append(numbers[-1].x0)
        if len(numbers) > 1:
            second.append(numbers[-2].x0)
        numeric += [(w.x0, len(w.text)) for w in numbers]
    for xs in (second, last):
        found = [x for x, c in _clusters(xs) if c >= 0.9 * n]
        if found:
            layout.extras.append(found[0])
    layout.extras.sort()
    floor = layout.extras[0] if layout.extras else float("inf")
    pool = [(x, size) for x, size in numeric if x < floor - 3]
    wide = [x for x, c in _clusters([x for x, size in pool if size >= 3]) if c >= 0.5 * n]
    plain = [x for x, c in _clusters([x for x, _ in pool]) if c >= 0.9 * n]
    pick = wide or plain
    if not pick:
        raise ValueError(f"{rows[0].url}: no column for the RC constituency code")
    layout.rc = pick[-1] if wide else plain[-1]
    return layout


@dataclass
class Station:
    code: str
    region: str
    zone: str
    woreda: str
    kebele: str
    hopr: tuple[str, str]
    rc: tuple[str, str]
    name: str
    latitude: float | None
    longitude: float | None
    url: str


def _join(words: list[Word]) -> str:
    text = ""
    for word in words:
        text += word.text if word.glued or not text else " " + word.text
    return text


def _at(row: Row, x: float, tolerance: float, after: int = 0) -> int | None:
    best: tuple[float, int] | None = None
    for i, word in enumerate(row.left):
        if i <= after or not word.text.isdigit() or abs(word.x0 - x) > tolerance:
            continue
        if best is None or abs(word.x0 - x) < best[0]:
            best = (abs(word.x0 - x), i)
    return None if best is None else best[1]


def read_row(row: Row, layout: Layout, status_at: float) -> Station:
    left = row.left
    region, hopr, woreda, zone, kebele, _ = row.parts
    where: dict[str, int] = {}
    previous = 0
    for name, value in zip(FIELDS, (region, zone, woreda, kebele, hopr), strict=True):
        found = _at(Row(row.url, row.code, left, []), layout.code[name], layout.tolerance, previous)
        if found is None:
            left = _unglue(left, layout.code[name], previous, value)
            found = _at(Row(row.url, row.code, left, []), layout.code[name], layout.tolerance, previous)
        if found is None:
            near = [
                (abs(w.x0 - layout.code[name]), i)
                for i, w in enumerate(left)
                if i > previous and _matches(w, value, name in LONG) and abs(w.x0 - layout.code[name]) < 40
            ]
            found = min(near)[1] if near else None
        if found is None:
            raise ValueError(f"{row.code}: no {name} code in {_join(left)!r}")
        where[name] = previous = found
    shift = left[where["hopr"]].x0 - layout.code["hopr"]  # a few rows sit a little off the column
    shift = shift if abs(shift) > layout.tolerance else 0.0
    rc = _at(Row(row.url, row.code, left, []), layout.rc + shift, layout.tolerance, where["hopr"])
    if rc is None:
        left = _unglue(left, layout.rc + shift, where["hopr"], None)
        rc = _at(Row(row.url, row.code, left, []), layout.rc + shift, layout.tolerance, where["hopr"])
    if rc is None:
        wide = [
            (abs(w.x0 - layout.rc - shift), i)
            for i, w in enumerate(left)
            if i > where["hopr"] and w.text.isdigit() and len(w.text) >= 3 and abs(w.x0 - layout.rc - shift) < 25
        ]
        rc = min(wide)[1] if wide else None
    if rc is None:
        raise ValueError(f"{row.code}: no RC constituency code in {_join(left)!r}")
    stop = len(left)
    for x in layout.extras:
        extra = _at(Row(row.url, row.code, left, []), x + shift, layout.tolerance, rc)
        if extra is not None:
            stop = min(stop, extra)
    decimals = [w for w in row.right if DECIMAL.fullmatch(w.text)]
    cut = min([w.x0 for w in decimals] + [status_at - 0.5])
    latitude = float(decimals[0].text) if len(decimals) >= 2 else None
    longitude = float(decimals[1].text) if len(decimals) >= 2 else None
    return Station(
        row.code,
        _join(left[1 : where["region"]]),
        _join(left[where["region"] + 1 : where["zone"]]),
        _join(left[where["zone"] + 1 : where["woreda"]]),
        _join(left[where["woreda"] + 1 : where["kebele"]]),
        (left[where["hopr"]].text, _join(left[where["hopr"] + 1 : rc])),
        (left[rc].text, _join(left[rc + 1 : stop])),
        _join([w for w in row.right if w.x0 < cut and not DECIMAL.fullmatch(w.text)]),
        latitude,
        longitude,
        row.url,
    )


STATUS_WORDS = {"የማኑዋል", "ማኑዋል", "የቴክኖሎጂ", "ሙሉ"}  # how the registration-method column begins


def _status_column(rows: list[Row]) -> float:
    """The x where the registration-method column starts, found from the words that open it in most rows."""
    xs = [w.x0 for row in rows for w in row.right if w.text in STATUS_WORDS]
    found = [x for x, c in _clusters(xs) if c >= 0.3 * len(rows)]
    if not found:
        raise ValueError(f"{rows[0].url}: no registration column")
    return found[-1]


@functools.cache
def stations(url: str) -> list[Station]:
    rows, _ = table_rows(url)
    status_at = _status_column(rows)
    whole = calibrate(rows)
    by_page: dict[int, list[Row]] = defaultdict(list)
    for row in rows:
        by_page[row.page].append(row)
    layouts: dict[int, Layout] = {}
    for number, group in by_page.items():
        try:
            page = calibrate(group) if len(group) >= 8 else whole
        except ValueError:
            page = whole
        if len(page.extras) < len(whole.extras):
            moved = page.code["hopr"] - whole.code["hopr"]
            page.extras = [x + moved for x in whole.extras]
        layouts[number] = page
    return [read_row(row, layouts[row.page], status_at) for row in rows]


REGIONS = {
    "Addis%20Ababa_0.pdf": 14,
    "Afar_4.pdf": 2,
    "Amhara_0.pdf": 3,
    "Benshangul%20Gumuz.pdf": 6,
    "Central%20Ethiopia.pdf": 7,
    "DireDawa.pdf": 15,
    "Gambella_0.pdf": 12,
    "Harari.pdf": 13,
    "Oromiya_0.pdf": 4,
    "Sidama_1.pdf": 8,
    "Somali_1.pdf": 5,
    "South%20Ethiopia.pdf": 9,
    "South%20West%20Ethiopia.pdf": 11,
}


@dataclass
class Seat:
    region: int
    english: str
    amharic: str
    local: str
    seats: int
    page: int


def _text(words: list[Word]) -> str:
    return " ".join(w.text for w in sorted(words, key=lambda w: w.x0))


def _split_names(words: list[Word]) -> tuple[str, str, str]:
    """A name cell's English text, its Amharic text, and the other-language text after a slash."""
    ordered = sorted(words, key=lambda w: w.x0)
    first = next((i for i, w in enumerate(ordered) if GEEZ.search(w.text)), len(ordered))
    rest = ordered[first:]
    slash = next((i for i, w in enumerate(rest) if w.text == "/"), None)
    amharic = rest if slash is None else rest[:slash]
    local = [] if slash is None else rest[slash + 1 :]
    return _text(ordered[:first]), _text(amharic), _text(local)


def _bands(anchors: list[Word]) -> list[tuple[float, float]]:
    mids = [a.mid for a in anchors]
    out = []
    for i, mid in enumerate(mids):
        low = (mids[i - 1] + mid) / 2 if i else mid - 6
        high = (mid + mids[i + 1]) / 2 if i + 1 < len(mids) else mid + 6
        out.append((low, high))
    return out


def _seat_column(numbers: list[Word], right: float) -> float | None:
    """The x of the House seats column, whose cells all read 1."""
    best: tuple[int, float] | None = None
    for x, count in _clusters([w.x0 for w in numbers if w.text == "1" and 200 < w.x0 < right - 60], 3.0):
        around = [w for w in numbers if abs(w.x0 - x) < 3.5]
        if count >= 2 and sum(w.text == "1" for w in around) >= 0.6 * len(around) and (best is None or count > best[0]):
            best = (count, x)
    return None if best is None else best[1]


def constituencies(url: str) -> tuple[list[Seat], list[Seat]]:
    """The House and regional council constituencies a regional list names, with their seats."""
    region = REGIONS[url.rsplit("/", 1)[1]]
    house: list[Seat] = []
    council: list[Seat] = []
    english_left: list[float] = []
    cells: list[tuple[int, list[Word]]] = []
    for number, page in enumerate(pages(url)):
        numbers = [w for w in page if w.text.isdigit()]
        if not numbers:
            continue
        right = max(w.x0 for w in numbers)
        seats_at = _seat_column(numbers, right + 1)
        only_house = False
        if seats_at is None:
            # a list of House constituencies alone has its seats in the rightmost column
            seats_at = _seat_column(numbers, right + 61)
            only_house = seats_at is not None
        if seats_at is not None:
            ones = [w for w in numbers if abs(w.x0 - seats_at) < 3.5 and w.text == "1"]
            for anchor in sorted(ones, key=lambda w: w.mid):
                words = [w for w in page if abs(w.mid - anchor.mid) < 7 and 150 < w.x0 < seats_at - 2]
                latin = [w.x0 for w in words if LATIN.search(w.text)]
                if latin:
                    english_left.append(min(latin))
                cells.append((number, words))
        if only_house:
            continue
        council_at = sorted((w for w in numbers if w.x0 >= right - 12), key=lambda w: w.mid)
        limit = (seats_at + 3) if seats_at is not None else 330
        for anchor, (low, high) in zip(council_at, _bands(council_at), strict=True):
            words = [w for w in page if low <= w.mid < high and limit < w.x0 < right - 3 and w is not anchor]
            en, am, local = _split_names(words)
            # a row with no seats repeats a name that another row seats; a total row has a count where the name would be
            named = bool(LATIN.search(en) or GEEZ.search(am) or local) and fold(am) != "ድምር"
            if named and int(anchor.text) > 0:
                council.append(Seat(region, en, am, local, int(anchor.text), number))
    left = statistics.median(english_left) if english_left else 0.0
    for number, words in cells:
        en, am, local = _split_names([w for w in words if w.x0 >= left - 2])
        house.append(Seat(region, en, am, local, 1, number))
    return house, council


HOMOPHONES = str.maketrans(
    {
        "ሐ": "ሀ", "ኀ": "ሀ", "ሑ": "ሁ", "ኁ": "ሁ", "ሒ": "ሂ", "ኂ": "ሂ", "ሓ": "ሃ", "ኃ": "ሃ", "ሔ": "ሄ", "ኄ": "ሄ",
        "ሕ": "ህ", "ኅ": "ህ", "ሖ": "ሆ", "ኆ": "ሆ", "ሠ": "ሰ", "ሡ": "ሱ", "ሢ": "ሲ", "ሣ": "ሳ", "ሤ": "ሴ", "ሥ": "ስ",
        "ሦ": "ሶ", "ዐ": "አ", "ዑ": "ኡ", "ዒ": "ኢ", "ዓ": "ኣ", "ዔ": "ኤ", "ዕ": "እ", "ዖ": "ኦ", "ፀ": "ጸ", "ፁ": "ጹ",
        "ፂ": "ጺ", "ፃ": "ጻ", "ፄ": "ጼ", "ፅ": "ጽ", "ፆ": "ጾ", "ኣ": "አ",
    }
)  # fmt: skip


def fold(text: str) -> str:
    """An Amharic name with spacing, punctuation, word order, and spelling variants removed, for matching two lists."""
    tokens = ["".join(c for c in t.translate(HOMOPHONES) if GEEZ.match(c) or c.isdigit()) for t in text.split()]
    return "".join(sorted(t for t in tokens if t))


def match_names(wanted: dict[int, str], listed: list[str]) -> dict[int, int]:
    """Pair each station-list constituency with an entry of the regional list by its Amharic name.

    Exact matches after folding go first; then the closest remaining names pair up when each is the other's best
    and the match is close. Returns the station-list code to the regional list's index.
    """
    pairs: dict[int, int] = {}
    free = set(range(len(listed)))
    folded = [fold(t) for t in listed]
    for code, text in sorted(wanted.items()):
        hits = [i for i in free if folded[i] == fold(text)]
        if len(hits) == 1:
            pairs[code] = hits[0]
            free.discard(hits[0])
    left = {code: fold(wanted[code]) for code in wanted if code not in pairs}
    scores = {
        (code, i): difflib.SequenceMatcher(None, name, folded[i]).ratio() for code, name in left.items() for i in free
    }
    for code in left:
        best = max(free, key=lambda i: scores[code, i], default=None)
        if best is None or scores[code, best] < 0.75:
            continue
        if max((c for c in left if c not in pairs), key=lambda c: scores[c, best]) == code:
            pairs[code] = best
            free.discard(best)
    return pairs


# The Summary's table for the seventh general election: House constituencies, regional council constituencies, and
# regional council seats for each region, keyed by the stations' region code. Tigray (38, 38, 152) is not listed.
SUMMARY = {
    15: (2, 47, 189),
    7: (35, 35, 175),
    8: (19, 19, 190),
    14: (23, 10, 158),
    9: (49, 56, 229),
    11: (20, 22, 171),
    12: (3, 14, 205),
    6: (9, 20, 165),
    4: (178, 179, 537),
    2: (8, 32, 142),
    5: (23, 72, 273),
    13: (2, 3, 36),
    3: (138, 137, 294),
}

STATION_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fstations"))
ELECTION_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fconstituencies"))
SQUARE = 0.00005  # half the side of the square drawn around a station's point, in degrees (about 5 meters)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    if scheme == "et.region" and int(code) in EXISTING:
        return EXISTING[int(code)]
    return key("area", scheme, code)


# The board's region numbers for the regions the board's older results already define, mapped to those areas (minted
# by `ea.sources.et.nebe` from their ISO codes), and the regions formed since 2020 with their ISO 3166-2 codes.
EXISTING = {
    number: str(uuid5(package.NAMESPACE, f"et.nebe\x1farea\x1fet.region\x1f{code}"))
    for number, code in {2: "AF", 3: "AM", 4: "OR", 5: "SO", 6: "BG", 12: "GA", 13: "HA", 14: "AA", 15: "DD"}.items()
}
NEW_REGIONS = {7: "ET-CE", 8: "ET-SI", 9: "ET-SE", 11: "ET-SW"}


def parts(code: str) -> tuple[int, int, int, int, int, int]:
    """A station code's region, House constituency, woreda, zone, kebele, and station."""
    region = len(code) - 11
    return (
        int(code[:region]),
        int(code[region : region + 3]),
        int(code[region + 3 : region + 5]),
        int(code[region + 5 : region + 7]),
        int(code[region + 7 : region + 9]),
        int(code[region + 9 :]),
    )


# The board's link texts misspell or shorten a few region names; these are the names the regions go by.
RESPELLED = {
    "Oromiya": "Oromia",
    "Somalli": "Somali",
    "Benshangul Gumz": "Benishangul-Gumuz",
    "Diredawa": "Dire Dawa",
    "South West": "South West Ethiopia",
}


def region_labels() -> dict[int, str]:
    """Each region's English name as the board's list of regional files words it."""
    html = raw.cached(SOURCE, ELECTION_PAGE).path.read_text(encoding="utf-8", errors="replace")
    labels: dict[int, str] = {}
    for href, text in re.findall(r'<a href="([^"]*\.pdf)">([^<]*)', html):
        file = href.rsplit("/", 1)[-1]
        if file in REGIONS:
            label = re.sub(r"\s+(Region|City Administration|City)$", "", " ".join(text.split()))
            labels[REGIONS[file]] = RESPELLED.get(label, label)
    return labels


@functools.cache
def all_stations() -> list[Station]:
    found = [s for url in station_files() for s in stations(url)]
    if len({s.code for s in found}) != len(found):
        raise ValueError("a polling station code appears twice")
    return found


def kind(url: str) -> str:
    return "manual" if "manu" in url.rsplit("/", 1)[1] else "technology"


def _vote(votes: dict[object, Counter[str]], key: object) -> str:
    counts = votes.get(key)
    if not counts:
        return ""
    return max(sorted(counts), key=lambda text: (counts[text], len(text)))


@dataclass
class Gazetteer:
    """The areas the lists name, keyed by their codes, with the most common spelling of each name."""

    region: dict[int, str] = field(default_factory=dict)
    zone: dict[tuple[int, int], str] = field(default_factory=dict)
    woreda: dict[tuple[int, int, int], str] = field(default_factory=dict)
    kebele: dict[tuple[int, int, int, int], str] = field(default_factory=dict)
    hopr: dict[int, tuple[int, str]] = field(default_factory=dict)  # code to region and Amharic name
    rc: dict[int, tuple[int, str]] = field(default_factory=dict)


@functools.cache
def gazetteer() -> Gazetteer:
    names: dict[str, dict[object, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    homes: dict[str, dict[int, Counter[int]]] = defaultdict(lambda: defaultdict(Counter))
    for s in all_stations():
        region, _, woreda, zone, kebele, _ = parts(s.code)
        for level, at, text in (
            ("region", region, s.region),
            ("zone", (region, zone), s.zone),
            ("woreda", (region, zone, woreda), s.woreda),
            ("kebele", (region, zone, woreda, kebele), s.kebele),
            ("hopr", int(s.hopr[0]), s.hopr[1]),
            ("rc", int(s.rc[0]), s.rc[1]),
        ):
            if text:
                names[level][at][text] += 1
            else:
                names[level].setdefault(at, Counter())
        homes["hopr"][int(s.hopr[0])][region] += 1
        homes["rc"][int(s.rc[0])][region] += 1
    out = Gazetteer()
    for level in ("region", "zone", "woreda", "kebele"):
        setattr(out, level, {at: _vote(names[level], at) for at in names[level]})
    for level in ("hopr", "rc"):
        setattr(
            out,
            level,
            {at: (homes[level][at].most_common(1)[0][0], _vote(names[level], at)) for at in homes[level]},
        )
    return out


@functools.cache
def lists() -> dict[int, tuple[list[Seat], list[Seat]]]:
    return {
        REGIONS[url.rsplit("/", 1)[1]]: constituencies(url)
        for url in constituency_files()
        if url.rsplit("/", 1)[1] in REGIONS
    }


def pairing() -> tuple[dict[int, Seat], dict[int, Seat]]:
    """The regional list's entry for each House and regional council constituency the station lists name."""
    gaz = gazetteer()
    house: dict[int, Seat] = {}
    council: dict[int, Seat] = {}
    for region, (h, c) in lists().items():
        for wanted, listed, into in (
            ({code: name for code, (r, name) in gaz.hopr.items() if r == region}, h, house),
            ({code: name for code, (r, name) in gaz.rc.items() if r == region}, c, council),
        ):
            for code, index in match_names(wanted, [s.amharic for s in listed]).items():
                into[code] = listed[index]
    return house, council


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry"]


def totals() -> list[str]:
    """Compare the lists with each other and with the Summary's counts, one line per region."""
    gaz = gazetteer()
    house, council = pairing()
    out: list[str] = []
    for region, (houses, councils, seats) in sorted(SUMMARY.items()):
        have_house = sum(1 for r, _ in gaz.hopr.values() if r == region)
        have_council = sum(1 for r, _ in gaz.rc.values() if r == region)
        listed_house, listed_council = lists()[region]
        out.append(
            f"region {region}: House constituencies {have_house} in the station lists, {len(listed_house)} in the "
            f"regional list, {houses} in the Summary; regional council constituencies {have_council}, "
            f"{len(listed_council)}, {councils}; council seats {sum(s.seats for s in listed_council)} in the "
            f"regional list, {seats} in the Summary; {sum(1 for c, (r, _) in gaz.hopr.items() if r == region and c in house)} "
            f"and {sum(1 for c, (r, _) in gaz.rc.items() if r == region and c in council)} paired by name"
        )
    out.append(
        f"{len(all_stations()):,} polling stations in {len(station_files())} lists; "
        f"{len(gaz.hopr)} House constituencies against {sum(h for h, _, _ in SUMMARY.values())} in the Summary"
    )
    return out


def parse() -> None:
    for line in totals():
        print(line, flush=True)
    geo().write()
    registry().write()


def emit() -> None:
    package.run("load", packages())


def _sources(p: Package) -> None:
    p.add(
        "source",
        id=STATION_SOURCE,
        kind="official_body",
        publisher="National Election Board of Ethiopia",
        title="List of polling stations for the seventh general election",
        url=STATIONS_PAGE,
        license="Official publication of the National Election Board of Ethiopia, released for public use",
        license_url=STATIONS_PAGE,
        attribution="National Election Board of Ethiopia (NEBE), nebe.org.et",
    )
    p.add(
        "source",
        id=ELECTION_SOURCE,
        kind="official_body",
        publisher="National Election Board of Ethiopia",
        title="Constituency lists and seats for the seventh general election",
        url=ELECTION_PAGE,
        license="Official publication of the National Election Board of Ethiopia, released for public use",
        license_url=ELECTION_PAGE,
        attribution="National Election Board of Ethiopia (NEBE), nebe.org.et",
    )
    for page, source in ((STATIONS_PAGE, STATION_SOURCE), (ELECTION_PAGE, ELECTION_SOURCE)):
        p.artifact(raw.cached(SOURCE, page), source)
        for url in _links(page):
            p.artifact(raw.cached(SOURCE, url), source)


def _name(p: Package, id: str, text: str, lang: str, kind: str) -> None:
    if text:
        p.name(id, text, lang, kind)


def _square(longitude: float, latitude: float) -> tuple[str, str]:
    shape = shapely.box(longitude - SQUARE, latitude - SQUARE, longitude + SQUARE, latitude + SQUARE)
    outline = shapely.set_srid(shapely.MultiPolygon([shape]), 4326)
    point = shapely.set_srid(shapely.Point(longitude, latitude), 4326)
    return shapely.to_wkb(outline, hex=True, include_srid=True), shapely.to_wkb(point, hex=True, include_srid=True)


def geo() -> Package:
    gaz = gazetteer()
    stations = all_stations()
    labels = region_labels()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Ethiopia's four regions formed since 2020, and its zones, woredas, kebeles, House and regional council constituencies, and polling stations "
        "from the National Election Board's 2026 lists. Tigray is not listed. A station's point is drawn as a "
        "10-meter square and flagged synthetic; its label point is the published coordinate.",
        depends=["et.nebe/geo"],
    )
    p.external |= {ETHIOPIA, COUNTRY, "et.region", *EXISTING.values()}
    _sources(p)
    for code, rank, kind_, noun in (
        ("et.zone", 2, "administrative", "zone"),
        ("et.woreda", 3, "administrative", "woreda"),
        ("et.kebele", 4, "administrative", "kebele"),
        ("et.polling_station", 5, "precinct", "polling station"),
        ("et.hopr_constituency", 1, "electoral_district", "House of Peoples' Representatives constituency"),
        ("et.rc_constituency", 1, "electoral_district", "regional council constituency"),
    ):
        p.add("scheme", code=code, polity_id=ETHIOPIA, kind=kind_, rank=rank, noun=noun, code_system=None)
    kinds = {
        "manual": "polling station (manual registration)",
        "technology": "polling station (technology registration)",
    }
    for row in p.rows["scheme"]:
        if row["code"] == "et.polling_station":
            row["kinds"] = kinds
    p.add(
        "boundary_set",
        id=key("boundary_set", "et.polling_station", "2026"),
        scheme_code="et.polling_station",
        vintage="2026",
        as_of=raw.cached(SOURCE, STATIONS_PAGE).retrieved_at.date(),
    )

    def place(scheme: str, code: str, parent: str, am: str, en: str = "") -> str:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        _name(p, id, am, "am", "official")
        _name(p, id, en, "en", "english")
        return id

    unknown = set(gaz.region) - set(EXISTING) - set(NEW_REGIONS)
    if unknown:
        raise ValueError(f"region numbers with no area: {sorted(unknown)}")
    for region, am in sorted(gaz.region.items()):
        if region in NEW_REGIONS:
            id = area("et.region", str(region))
            p.add("area", id=id, scheme_code="et.region")
            p.add("area_code", area_id=id, code=NEW_REGIONS[region])
            p.add("area_parent", area_id=id, parent_id=COUNTRY)
            _name(p, id, labels.get(region, ""), "en", "official")
            if am:
                p.name(id, am, "am", "native", False)
    for (region, zone), am in sorted(gaz.zone.items()):
        place("et.zone", f"{region}-{zone:02d}", area("et.region", str(region)), am)
    for (region, zone, woreda), am in sorted(gaz.woreda.items()):
        place("et.woreda", f"{region}-{zone:02d}-{woreda:02d}", area("et.zone", f"{region}-{zone:02d}"), am)
    for (region, zone, woreda, kebele), am in sorted(gaz.kebele.items()):
        place(
            "et.kebele",
            f"{region}-{zone:02d}-{woreda:02d}-{kebele:02d}",
            area("et.woreda", f"{region}-{zone:02d}-{woreda:02d}"),
            am,
        )
    house, council = pairing()
    for scheme, found, listed in (("et.hopr_constituency", gaz.hopr, house), ("et.rc_constituency", gaz.rc, council)):
        for code, (region, am) in sorted(found.items()):
            id = place(
                scheme,
                f"{code:03d}",
                area("et.region", str(region)),
                am,
                listed[code].english if code in listed else "",
            )
            seat = listed.get(code)
            if seat is None:
                continue
            if scheme == "et.rc_constituency":
                p.add(
                    "note",
                    id=key("note", id, "seats"),
                    entity_id=id,
                    kind="context",
                    body_md=f"The seventh general election fills {seat.seats} seats on the regional council from this constituency.",
                    lang="en",
                )
            p.add(
                "citation",
                id=key("citation", id),
                entity_id=id,
                source_id=ELECTION_SOURCE,
                locator={"page": seat.page + 1, "region": region},
            )
    for s in stations:
        region, _, woreda, zone, kebele, _ = parts(s.code)
        id = area("et.polling_station", s.code)
        p.add("area", id=id, scheme_code="et.polling_station")
        p.add("area_code", area_id=id, code=s.code)
        p.add("area_parent", area_id=id, parent_id=area("et.kebele", f"{region}-{zone:02d}-{woreda:02d}-{kebele:02d}"))
        p.add("area_kind", area_id=id, kind=kind(s.url), source_id=STATION_SOURCE)
        _name(p, id, s.name, "am", "official")
        if s.latitude is not None and s.longitude is not None and _inside(s.latitude, s.longitude):
            outline, point = _square(s.longitude, s.latitude)
            p.add(
                "area_geometry",
                area_id=id,
                geom=outline,
                label_point=point,
                quality="synthetic",
                source_id=STATION_SOURCE,
            )
    return p


def _inside(latitude: float, longitude: float) -> bool:
    south, north, west, east = ETHIOPIA_BOX
    return south <= latitude <= north and west <= longitude <= east


def registry() -> Package:
    labels = region_labels()
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The councils of Ethiopia's four regions formed since 2020, which the 2026 constituency lists fill",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ETHIOPIA}
    source = key("source", "registry")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="National Election Board of Ethiopia",
        title="Constituency lists and seats for the seventh general election",
        url=ELECTION_PAGE,
        license="Official publication of the National Election Board of Ethiopia, released for public use",
        license_url=ELECTION_PAGE,
        attribution="National Election Board of Ethiopia (NEBE), nebe.org.et",
    )
    p.artifact(raw.cached(SOURCE, ELECTION_PAGE), source)
    for url in constituency_files():
        p.artifact(raw.cached(SOURCE, url), source)
    for region in sorted(set(gazetteer().region) & set(NEW_REGIONS)):
        id = key("body", "council", region)
        p.add("body", id=id, polity_id=ETHIOPIA, kind="council")
        label = labels.get(region, "")
        if label:
            p.name(id, f"{label} Regional Council", "en", "official")
    return p
