"""Iran's Ministry of Interior (Election Headquarters): Majlis general elections by constituency.

The Ministry's own hosts refuse automated requests, so the reports come from the copies the Iran Data Portal at
Syracuse University republishes unchanged: the Ministry's report of candidate votes per constituency for the first
round of the 2012 election, and its list of the members elected in the first round of the 2024 election with their
votes and each constituency's valid votes, together with its table of the 2024 constituencies and their seats.
"""

import functools
import html
import json
import re
import subprocess
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "ir.moi"
GEOBOUNDARIES = "ir.geoboundaries"
IRAN = str(uuid5(package.NAMESPACE, "polity\x1fIR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fIRN"))
PORTAL = "https://irandataportal.syr.edu/wp-content/uploads/"
REPORT_2012 = PORTAL + "9th-parliamentary-election-full-results-.pdf"
ELECTED_2024 = PORTAL + "List-of-MPS-Elected-in-First-Round-March-2024.pdf"
DISTRICTS_2024 = PORTAL + "Majles-Electoral-Districts-2024.pdf"
CANDIDATES_2024: dict[str, str] = {
    "alborz": "Alborz-1.pdf",
    "ardabil": "Ardebil.pdf",
    "bushehr": "Bushehr.pdf",
    "chaharmahal-and-bakhtiari": "Chaharmahal-va-Bakhtiyari.pdf",
    "east-azerbaijan": "East-Azerbaijan-1.pdf",
    "isfahan": "Esfahan-1.pdf",
    "fars": "Fars.pdf",
    "gilan": "Gilan.pdf",
    "golestan": "Golestan.pdf",
    "hamadan": "Hamedan.pdf",
    "hormozgan": "Hormozgan.pdf",
    "ilam": "Ilam.pdf",
    "kerman": "Kerman.pdf",
    "kermanshah": "Kermanshaah.pdf",
    "razavi-khorasan": "Khorasan-Razavi.pdf",
    "khuzestan": "Khuzestan.pdf",
    "kohgiluyeh-and-boyer-ahmad": "Kohgilouyeh-va-Boyer-Ahmad.pdf",
    "kurdistan": "Kurdistan.pdf",
    "lorestan": "Lorestan.pdf",
    "markazi": "Markazi.pdf",
    "mazandaran": "Mazandaran-1.pdf",
    "north-khorasan": "North-Khorasan.pdf",
    "qazvin": "Qazvin.pdf",
    "qom": "Qom.pdf",
    "semnan": "Semnan.pdf",
    "sistan-and-baluchestan": "Sistan-va-Baluchestan.pdf",
    "south-khorasan": "South-Khorasan.pdf",
    "tehran": "Tehran-1.pdf",
    "west-azerbaijan": "West-Azerbaijan.pdf",
    "yazd": "Yazd.pdf",
    "zanjan": "Zanjan.pdf",
}
PAGE_2012 = "https://irandataportal.syr.edu/2012-parliamentary-elections/"
PAGE_2024 = "https://irandataportal.syr.edu/parliamentary-elections/2024-parliamentary-election/"
ADM1 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/IRN/ADM1/"
    "geoBoundaries-IRN-ADM1_simplified.geojson"
)
DAY_2012 = date(2012, 3, 2)
DAY_2024 = date(2024, 3, 1)
UA = "electionsarchive/0.1 (+https://electionsarchive.com)"

# Province: code, English name as geoBoundaries prints it, Persian name as the Ministry prints it.
PROVINCES: dict[str, tuple[str, str]] = {
    "east-azerbaijan": ("East Azerbaijan", "آذربایجان شرقی"),
    "west-azerbaijan": ("West Azerbaijan", "آذربایجان غربی"),
    "ardabil": ("Ardabil", "اردبیل"),
    "isfahan": ("Isfahan", "اصفهان"),
    "alborz": ("Alborz", "البرز"),
    "ilam": ("Ilam", "ایلام"),
    "bushehr": ("Bushehr", "بوشهر"),
    "tehran": ("Tehran", "تهران"),
    "chaharmahal-and-bakhtiari": ("Chaharmahal and Bakhtiari", "چهارمحال و بختیاری"),
    "south-khorasan": ("South Khorasan", "خراسان جنوبی"),
    "razavi-khorasan": ("Razavi Khorasan", "خراسان رضوی"),
    "north-khorasan": ("North Khorasan", "خراسان شمالی"),
    "khuzestan": ("Khuzestan", "خوزستان"),
    "zanjan": ("Zanjan", "زنجان"),
    "semnan": ("Semnan", "سمنان"),
    "sistan-and-baluchestan": ("Sistan and Baluchestan", "سیستان و بلوچستان"),
    "fars": ("Fars", "فارس"),
    "qazvin": ("Qazvin", "قزوین"),
    "qom": ("Qom", "قم"),
    "kurdistan": ("Kurdistan", "کردستان"),
    "kerman": ("Kerman", "کرمان"),
    "kermanshah": ("Kermanshah", "کرمانشاه"),
    "kohgiluyeh-and-boyer-ahmad": ("Kohgiluyeh and Boyer-Ahmad", "کهگیلویه و بویراحمد"),
    "golestan": ("Golestan", "گلستان"),
    "gilan": ("Gilan", "گیلان"),
    "lorestan": ("Lorestan", "لرستان"),
    "mazandaran": ("Mazandaran", "مازندران"),
    "markazi": ("Markazi", "مرکزی"),
    "hormozgan": ("Hormozgan", "هرمزگان"),
    "hamadan": ("Hamadan", "همدان"),
    "yazd": ("Yazd", "یزد"),
}
# The five religious-minority constituencies, which vote nationwide: key, Persian name, English name.
MINORITIES: list[tuple[str, str, str]] = [
    ("zoroastrians", "زرتشتیان", "Zoroastrians"),
    ("jews", "کلیمیان", "Jews"),
    ("assyrians-chaldeans", "مسیحیان آشوری و کلدانی", "Assyrian and Chaldean Christians"),
    ("armenians-north", "مسیحیان ارمنی شمال ایران", "Armenians of the North"),
    ("armenians-south", "مسیحیان ارمنی جنوب ایران", "Armenians of the South"),
]
FIRST = "مرحله اول"
SECOND = "مرحله دوم"
MS = "خانم"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


MOI = key("source", SOURCE)
GB_SOURCE = key("source", "geoboundaries")
MAJLIS = key("body", "majlis")
SYSTEM_2012 = key("electoral_system", "2012")
SYSTEM_2024 = key("electoral_system", "2024")
SYSTEMS: dict[int, tuple[str, date, date | None, float, str]] = {
    2012: (
        SYSTEM_2012,
        DAY_2012,
        DAY_2012,
        0.25,
        (
            "Each voter writes as many names as the constituency has seats. In the first round a candidate needs a "
            "plurality that is at least a quarter of the ballots cast in the constituency; seats left over go to a "
            "second round among the leading remaining candidates, twice as many as the seats left, won by plurality. "
            "The dates bound the election this archive holds under these rules, not the period the law applied."
        ),
    ),
    2024: (
        SYSTEM_2024,
        DAY_2024,
        DAY_2024,
        0.2,
        (
            "Each voter writes as many names as the constituency has seats. In the first round a candidate needs a "
            "plurality that is at least a fifth of the ballots cast in the constituency; seats left over go to a "
            "second round among the leading remaining candidates, twice as many as the seats left, won by plurality. "
            "The dates bound the election this archive holds under these rules, not the period the law applied."
        ),
    ),
}
METHOD_2012 = (
    "Published by the Ministry of Interior's Election Headquarters as its general report of the constituencies and "
    "the candidates' votes (گزارش کلی حوزه‌های انتخابیه و آرای داوطلبان) for the first round of the ninth Majlis "
    "election, dated 8 March 2012, and read here from the copy the Iran Data Portal republishes. It lists every "
    "candidate's votes in each of the 202 territorial constituencies, and marks the candidates elected in the first "
    "round and those sent to the second round. It prints no registered voters, ballots, or valid votes. The five "
    "religious-minority seats are not in the report. Each voter writes up to as many names as the constituency has "
    "seats, so the candidates' votes add up to more than the ballots."
)
METHOD_2024 = (
    "Published by the Ministry of Interior as the official tally of the members elected in the first round of the "
    "twelfth Majlis election (اسامی منتخبان مجلس دوازدهم در مرحله اول انتخابات بر اساس آمار رسمی وزارت کشور), read "
    "here from the copy the Iran Data Portal republishes. The list gives only the candidates elected in the first "
    "round, with their votes, and each constituency's valid votes in the first round; it names no losing "
    "candidates and none sent to the second round, so the options here are the elected members alone. "
    "Constituencies that elected no one in the first round are not in the list. Seats per constituency are from the "
    "Ministry's table of the 2024 constituencies."
)


# Text


BIDI = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\ufeff"))
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def fa(text: str) -> str:
    """Persian text as printed, in logical order: compatibility forms folded, Arabic yeh and kaf as Persian."""
    text = unicodedata.normalize("NFKC", text).translate(BIDI)
    text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    text = re.sub(r"\s*[،,]\s*", "، ", text)
    return re.sub(r"\s+", " ", text).strip(" ،")


def fold(text: str) -> str:
    """A matching key that ignores spacing, punctuation, the conjunction, and the order of a lam-alef pair."""
    text = fa(text).replace("ئ", "ی").replace("أ", "ا").replace("الله", "اله").replace("اهلل", "اله")
    text = text.replace("اسماعیل", "اسمعیل").replace("لا", "#").replace("ال", "#")
    return re.sub(r"[\s\u200c،و]", "", text).translate(HOMOPHONES)


HOMOPHONES = str.maketrans("طصثضظذحغ", "تسسزززهق")


def number(text: str) -> int:
    return int(re.sub(r"[،,٬.\s]", "", text.translate(PERSIAN_DIGITS)))


def province_code(text: str) -> str:
    folded = fold(text)
    for code, (_, persian) in PROVINCES.items():
        if fold(persian) == folded:
            return code
    raise LookupError(f"unknown province {text!r}")


# Raw files


def fetch() -> None:
    for url in (REPORT_2012, ELECTED_2024, DISTRICTS_2024):
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in candidate_urls():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in (PAGE_2012, PAGE_2024):
        raw.fetch(SOURCE, url, suffix=".html")
    raw.fetch(GEOBOUNDARIES, ADM1, suffix=".geojson")


def artifacts() -> list[raw.Artifact]:
    urls = [REPORT_2012, ELECTED_2024, DISTRICTS_2024, *candidate_urls(), PAGE_2012, PAGE_2024]
    return [raw.cached(SOURCE, url) for url in urls]


def candidate_urls() -> list[str]:
    return [PORTAL + name for name in CANDIDATES_2024.values()]


@functools.cache
def layout(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True, text=True)
    return unicodedata.normalize("NFKC", out.stdout).translate(BIDI)


@dataclass
class Word:
    page: int
    x0: float
    y0: float
    x1: float
    text: str

    @property
    def x(self) -> float:
        return (self.x0 + self.x1) / 2


@functools.cache
def words(url: str) -> list[Word]:
    """Each word with its box. Poppler gives a right-to-left word in visual order, so it is reversed back."""
    path = raw.cached(SOURCE, url).path
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, check=True, text=True).stdout
    found: list[Word] = []
    for number_, page in enumerate(out.split("<page ")[1:], 1):
        for x0, y0, x1, text in re.findall(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)</word>', page
        ):
            text = html.unescape(text)
            if not re.fullmatch(r"[\d۰-۹,٬.]+", text):
                text = text[::-1]
            found.append(Word(number_, float(x0), float(y0), float(x1), text))
    return found


# 2012: the Ministry's report of candidate votes per constituency


@dataclass
class Row:
    position: int
    name: str
    votes: int
    remark: str | None


@dataclass
class District:
    province: str
    number: int
    name: str
    seats: int
    status: str
    rows: list[Row] = field(default_factory=list)
    count: int | None = None

    @property
    def code(self) -> str:
        return f"{self.province}-{self.number:02d}"


PROVINCE_LINE = re.compile(r"^\s*استان\s*:\s*(\S.*?)\s*$")
DISTRICT_LINE = re.compile(r"^\s*(\S.*?)\s{2,}حوزه انتخابیه\s*:\s*$")
SEATS_LINE = re.compile(r"وضعیت\s*:\s*(مرحله[^\d]*?)\s+(\d+)\s+تعداد نمایندگان مورد نیاز\s*:")
ROW_LINE = re.compile(r"^\s*(?:(مرحله اول|مرحله دوم)\s+)?([\d،,]+)\s{2,}(\S.*?)\s{2,}(\d+)\s*$")
COUNT_LINE = re.compile(r"تعداد کل داوطلبین حوزه\s*(\d+)\s*:")
PROVINCE_COUNT = re.compile(r"تعداد کل داوطلبین استان\s*(\d+)\s*:")


@functools.cache
def report_2012() -> list[District]:
    text = layout(REPORT_2012).replace("ي", "ی").replace("ك", "ک")
    districts: list[District] = []
    province: str | None = None
    numbers: Counter[str] = Counter()
    province_counts: dict[str, int] = {}
    current: District | None = None
    for line in text.split("\n"):
        if found := PROVINCE_LINE.match(line):
            province = province_code(found.group(1))
            continue
        if found := DISTRICT_LINE.match(line):
            assert province is not None, line
            numbers[province] += 1
            current = District(province, numbers[province], fa(found.group(1)), 0, "")
            districts.append(current)
            continue
        if found := SEATS_LINE.search(line):
            assert current is not None and not current.seats, line
            current.status = fa(found.group(1))
            current.seats = int(found.group(2))
            continue
        if found := COUNT_LINE.search(line):
            assert current is not None, line
            current.count = int(found.group(1))
            continue
        if found := PROVINCE_COUNT.search(line):
            assert province is not None
            province_counts[province] = int(found.group(1))
            continue
        if found := ROW_LINE.match(line):
            assert current is not None and current.seats, line
            remark, votes, name, position = found.groups()
            current.rows.append(Row(int(position), fa(name), number(votes), remark))
    for district in districts:
        positions = [row.position for row in district.rows]
        if positions != list(range(1, len(positions) + 1)) or district.count != len(positions):
            raise ValueError(f"2012 {district.code}: rows {positions}, printed count {district.count}")
        votes = [row.votes for row in district.rows]
        if votes != sorted(votes, reverse=True):
            raise ValueError(f"2012 {district.code}: votes out of order")
        elected = sum(row.remark == FIRST for row in district.rows)
        advanced = sum(row.remark == SECOND for row in district.rows)
        if elected > district.seats or advanced > 2 * (district.seats - elected):
            raise ValueError(f"2012 {district.code}: {elected} elected, {advanced} advanced, {district.seats} seats")
    for code, count in province_counts.items():
        if sum(len(d.rows) for d in districts if d.province == code) != count:
            raise ValueError(f"2012 {code}: candidates do not add up to the province's printed {count}")
    if set(province_counts) != set(PROVINCES) or len(districts) != 202:
        raise ValueError(f"2012: {len(province_counts)} provinces, {len(districts)} constituencies")
    return districts


# 2024: the Ministry's list of members elected in the first round


@dataclass
class Elected:
    province: str
    district: str
    name: str
    votes: int
    valid: int
    female: bool
    text: str
    code: str = ""


@dataclass
class District2024:
    province: str
    number: int
    name: str
    seats: int
    valid: int
    elected: list[Elected] = field(default_factory=list)
    minority: str | None = None

    @property
    def code(self) -> str:
        return self.minority or f"{self.province}-{self.number:02d}"


STATUS_WORDS = {"انتخاب", "مرحله", "اول"}


HEADERS = {
    "ردیف": "row",
    "استان": "province",
    "حوزه": "district",
    "انتخابیه": "district",
    "نام": "name",
    "وضعیت": "status",
}


def _columns(page: list[Word], known: dict[str, float]) -> dict[str, float]:
    """The centers of the columns from the page's header; a page without one keeps the last page's."""
    found: dict[str, list[float]] = {}
    for w in page:
        if w.text in HEADERS and w.y0 < 200:
            found.setdefault(HEADERS[w.text], []).append(w.x0 if w.text == "ردیف" else w.x)
    return {**known, **{column: sum(xs) / len(xs) for column, xs in found.items()}}


def _cell(cell: list[Word]) -> str:
    lines: dict[float, list[Word]] = {}
    for w in sorted(cell, key=lambda w: w.y0):
        line = next((y for y in lines if abs(y - w.y0) < 3), w.y0)
        lines.setdefault(line, []).append(w)
    return " ".join(" ".join(w.text for w in sorted(lines[y], key=lambda w: -w.x)) for y in sorted(lines))


def minority_of(text: str) -> str | None:
    folded = fold(text)
    for code, persian, _ in MINORITIES:
        if fold(persian) == folded:
            return code
    return None


NUMBER = re.compile(r"[۰-۹\d][۰-۹\d,٬.]*")


@functools.cache
def elected_2024() -> list[Elected]:
    found: list[Elected] = []
    pages: dict[int, list[Word]] = {}
    for w in words(ELECTED_2024):
        pages.setdefault(w.page, []).append(w)
    columns: dict[str, float] = {}
    for number_ in sorted(pages):
        page = pages[number_]
        columns = _columns(page, columns)
        status_name = (columns["status"] + columns["name"]) / 2
        name_district = (columns["name"] + columns["district"]) / 2
        district_province = (columns["district"] + columns["province"]) / 2
        row_x = columns["row"] - 4
        headers = [w.y0 for w in page if w.text == "ردیف"]
        top = max(headers) + 12 if headers else 0
        anchors = [w for w in page if w.x0 >= row_x and w.y0 > top and re.fullmatch(r"[۰-۹\d]+", w.text)]
        rows: dict[float, list[Word]] = {a.y0: [] for a in anchors}
        for w in page:
            if w.y0 <= top or w.x0 >= row_x or not rows:
                continue
            nearest = min(rows, key=lambda y: abs(y - w.y0))
            if abs(nearest - w.y0) < 18:
                rows[nearest].append(w)
        for y in sorted(rows):
            cells = rows[y]
            numbers = sorted((w for w in cells if NUMBER.fullmatch(w.text)), key=lambda w: w.x)
            if len(numbers) != 2:
                raise ValueError(f"2024 page {number_} row at {y}: numbers {[w.text for w in numbers]}")
            text = [w for w in cells if w not in numbers and w.text not in STATUS_WORDS]
            name = _cell([w for w in text if status_name < w.x < name_district])
            district = _cell([w for w in text if name_district <= w.x < district_province])
            province = _cell([w for w in text if w.x >= district_province])
            female = name.startswith(MS + " ")
            found.append(
                Elected(
                    province=fa(province),
                    district=fa(district),
                    name=fa(name.removeprefix(MS + " ") if female else name),
                    votes=number(numbers[1].text),
                    valid=number(numbers[0].text),
                    female=female,
                    text=fa(_cell(text)),
                )
            )
    return found


@dataclass
class Candidate:
    province: str
    district: str
    first: str
    last: str
    code: str

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"


@functools.cache
def candidates_2024(province: str) -> list[Candidate]:
    """The Ministry's list of the qualified candidates of one province, with each one's ballot code."""
    url = PORTAL + CANDIDATES_2024[province]
    found: list[Candidate] = []
    pages: dict[int, list[Word]] = {}
    for w in words(url):
        pages.setdefault(w.page, []).append(w)
    for page in pages.values():
        anchors = [w for w in page if w.x1 < 300 and re.fullmatch(r"\d+", w.text)]
        rows: dict[float, list[Word]] = {a.y0: [] for a in anchors}
        codes = {a.y0: a.text for a in anchors}
        for w in page:
            if w.x1 < 300 or not rows:
                continue
            nearest = min(rows, key=lambda y: abs(y - w.y0))
            if abs(nearest - w.y0) < 45:
                rows[nearest].append(w)
        for y, cells in sorted(rows.items()):
            region = _cell([w for w in cells if w.x0 >= 685])
            found.append(
                Candidate(
                    province=fa(region).removeprefix("استان "),
                    district=fa(_cell([w for w in cells if 575 <= w.x < 685])),
                    first=fa(_cell([w for w in cells if 470 <= w.x < 575])),
                    last=fa(_cell([w for w in cells if 300 <= w.x < 470])),
                    code=codes[y],
                )
            )
    if not found or len({(c.district, c.code) for c in found}) != len(found):
        raise ValueError(f"2024 candidates of {province}: {len(found)} rows, codes not unique")
    return found


def _likeness(a: str, b: str) -> float:
    plain = SequenceMatcher(None, fold(a), fold(b)).ratio()
    tokens = SequenceMatcher(None, "".join(sorted(_tokens(a))), "".join(sorted(_tokens(b)))).ratio()
    return max(plain, tokens)


@functools.cache
def seats_2024() -> dict[str, int]:
    """Seats of each constituency the candidate lists name, from the Ministry's table. The two spell some names
    differently, so each list name takes the table row most like it, and no table row is taken twice."""
    rows = seat_rows_2024()
    names = sorted({c.district for code in CANDIDATES_2024 for c in candidates_2024(code)})
    if len(names) != len(rows):
        raise ValueError(f"2024: {len(names)} constituencies in the candidate lists, {len(rows)} in the seat table")
    pairs = sorted(((_likeness(name, row[0]), name, i) for name in names for i, row in enumerate(rows)), reverse=True)
    found: dict[str, int] = {}
    taken: set[int] = set()
    for score, name, i in pairs:
        if name in found or i in taken:
            continue
        if score < 0.6:
            raise LookupError(f"2024: no seat-table row for {name!r}")
        found[name] = rows[i][1]
        taken.add(i)
    return found


@functools.cache
def seat_rows_2024() -> list[tuple[str, int]]:
    """The Ministry's table of the 2024 constituencies, each name with its seats."""
    found: list[tuple[str, int]] = []
    pages: dict[int, list[Word]] = {}
    for w in words(DISTRICTS_2024):
        pages.setdefault(w.page, []).append(w)
    total = 0
    for page in pages.values():
        lines: dict[float, list[Word]] = {}
        for w in page:
            line = next((y for y in lines if abs(y - w.y0) < 4), w.y0)
            lines.setdefault(line, []).append(w)
        for y in sorted(lines):
            line = sorted(lines[y], key=lambda w: -w.x)
            if len(line) < 3 or not re.fullmatch(r"\d+", line[0].text) or not re.fullmatch(r"\d+", line[-1].text):
                continue
            name = fa(" ".join(w.text for w in line[1:-1]))
            seats = int(line[-1].text)
            total += seats
            found.append((name, seats))
    if total != 290:
        raise ValueError(f"2024 constituency table: {total} seats, not 290")
    return found


def _tokens(text: str) -> list[str]:
    return [token for token in (fold(part) for part in text.split()) if token]


def _share(tokens: list[str], folded: str) -> float:
    return sum(len(t) for t in tokens if t in folded) / max(1, sum(len(t) for t in tokens))


def _without(folded: str, province: str) -> str:
    """The row's text with its province name taken out once, so a constituency named after it must name it again."""
    return folded.replace(fold(PROVINCES[province][1]), "", 1) if province in PROVINCES else folded


def match(row: Elected) -> tuple[str, Candidate]:
    """The candidate-list row of an elected member: the constituency whose name the row's text holds best, then
    the candidate of that constituency whose name is closest to the row's name."""
    folded = fold(row.text)
    minority = any(_share(_tokens(persian), folded) == 1 for _, persian, _ in MINORITIES)
    pool = [
        (code, c)
        for code in CANDIDATES_2024
        for c in candidates_2024(code)
        if bool(minority_of(c.district)) == minority
    ]
    if not minority:
        right = fold(row.province)
        provinces = {
            code: (_share(_tokens(persian), folded), _share(_tokens(persian)[:1], right))
            for code, (_, persian) in PROVINCES.items()
        }
        pool = [(code, c) for code, c in pool if provinces[code][0] == 1]
    districts = {(code, c.district) for code, c in pool}
    scores = {d: (_share(_tokens(d[1]), _without(folded, d[0])), len(fold(d[1]))) for d in districts}
    best = max(scores.values())
    chosen = [d for d, score in scores.items() if score == best]
    if best[0] < 0.4 or len(chosen) != 1:
        raise LookupError(f"2024: constituency of {row.text!r}: {chosen[:4]} at {best}")
    code, district = chosen[0]
    places = {*_tokens(district), *(t for _, persian in PROVINCES.values() for t in _tokens(persian)), fold(MS)}
    status = fold("انتخاب مرحله اول")
    rest = [t for t in _tokens(row.text) if t not in places and not (len(t) <= 3 and t in status)]
    ranked = sorted(
        (
            (SequenceMatcher(None, "".join(rest), fold(c.name)).ratio() + _share(rest, fold(c.name)), c)
            for here, c in pool
            if (here, c.district) == (code, district)
        ),
        key=lambda pair: -pair[0],
    )
    if ranked[0][0] < 1.2 or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.1):
        raise LookupError(f"2024: candidate of {row.text!r}: {[(round(s, 2), c.name) for s, c in ranked[:3]]}")
    return code, ranked[0][1]


@functools.cache
def districts_2024() -> list[District2024]:
    table = seats_2024()
    districts: dict[tuple[str, str], District2024] = {}
    numbers: Counter[str] = Counter()
    for row in elected_2024():
        province, candidate = match(row)
        minority = minority_of(candidate.district)
        if minority:
            province = "minorities"
        here = (province, fold(candidate.district))
        if here not in districts:
            numbers[province] += 1
            districts[here] = District2024(
                province, numbers[province], candidate.district, table[candidate.district], row.valid, minority=minority
            )
        district = districts[here]
        if district.valid != row.valid:
            raise ValueError(f"2024 {district.code}: valid votes {district.valid} and {row.valid}")
        row.name = candidate.name
        row.code = candidate.code
        district.elected.append(row)
    for district in districts.values():
        if len(district.elected) > district.seats:
            raise ValueError(f"2024 {district.code}: {len(district.elected)} elected for {district.seats} seats")
        if len({row.code for row in district.elected}) != len(district.elected):
            raise ValueError(f"2024 {district.code}: one candidate matched twice")
        for row in district.elected:
            if row.votes > row.valid or row.votes * 5 < row.valid:
                raise ValueError(f"2024 {district.code}: {row.name} has {row.votes} of {row.valid} valid votes")
    return list(districts.values())


# Packages


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/2012-03-02", f"{SOURCE}/2024-03-01"]


def parse() -> None:
    geo().write()
    registry().write()
    election_2012().write()
    election_2024().write()


def emit() -> None:
    package.run("load", packages())


def province_area(code: str) -> str:
    return key("area", "ir.ostan", code)


def district_area(year: int, code: str) -> str:
    return key("area", "ir.majlis_district", year, code)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Iran's 31 provinces from geoBoundaries, with the Majlis constituencies of 2012 and 2024 placed within them",
    )
    p.external |= {IRAN, COUNTRY}
    artifact = raw.cached(GEOBOUNDARIES, ADM1)
    p.add(
        "source",
        id=GB_SOURCE,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap, Wambacher)",
        title="geoBoundaries gbOpen Iran ADM1, 2017",
        url="https://www.geoboundaries.org/",
        license="ODbL 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), © OpenStreetMap contributors",
    )
    p.artifact(artifact, GB_SOURCE)
    for code, rank, kind, noun in (
        ("ir.ostan", 1, "administrative", "province"),
        ("ir.majlis_district", 2, "electoral_district", "Majlis constituency"),
    ):
        p.add("scheme", code=code, polity_id=IRAN, kind=kind, rank=rank, noun=noun)
    p.add(
        "boundary_set",
        id=key("boundary_set", "ir.ostan", "2017"),
        scheme_code="ir.ostan",
        vintage="2017",
        as_of=artifact.retrieved_at.date(),
    )
    shapes: dict[str, list[shapely.Geometry]] = {}
    for feature in json.loads(artifact.path.read_text("utf-8"))["features"]:
        shapes.setdefault(str(feature["properties"]["shapeName"]), []).append(
            shapely.geometry.shape(feature["geometry"])
        )
    for code, (english, persian) in PROVINCES.items():
        id = province_area(code)
        p.add("area", id=id, scheme_code="ir.ostan")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, f"استان {persian}", "fa", "official")
        p.name(id, f"{english} Province", "en", "english", False)
        shape = shapely.make_valid(shapely.union_all(shapes[english]))
        shape = shapely.MultiPolygon([g for g in shapely.get_parts(shape) if shapely.get_type_id(g) == 3])
        shape = shapely.set_srid(shape, 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=GB_SOURCE,
        )
    if set(shapes) != {english for english, _ in PROVINCES.values()}:
        raise ValueError(f"geoBoundaries provinces differ: {sorted(shapes)}")
    plans: list[tuple[int, date, list[tuple[str, str, str]]]] = [
        (2012, DAY_2012, [(d.code, d.province, d.name) for d in report_2012()]),
        (2024, DAY_2024, [(d.code, d.province, d.name) for d in districts_2024() if not d.minority]),
    ]
    for year, day, rows in plans:
        p.add(
            "boundary_set",
            id=key("boundary_set", "ir.majlis_district", year),
            scheme_code="ir.majlis_district",
            vintage=str(year),
            as_of=day,
        )
        for code, province, name in rows:
            id = district_area(year, code)
            p.add("area", id=id, scheme_code="ir.majlis_district")
            p.add("area_code", area_id=id, code=f"{year}:{code}")
            p.add("area_parent", area_id=id, parent_id=province_area(province))
            p.name(id, name, "fa", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Iran's Islamic Consultative Assembly (Majlis), its electoral systems, and the Ministry of Interior's reports",
    )
    p.external.add(IRAN)
    p.add(
        "source",
        id=MOI,
        kind="official_body",
        publisher="وزارت کشور، ستاد انتخابات کشور (Ministry of Interior, Election Headquarters)",
        title="Majlis election results by constituency",
        url=PAGE_2012,
        license="Published by the Ministry with no license stated",
        attribution="Ministry of Interior of the Islamic Republic of Iran",
        notes=(
            "The Ministry's hosts (moi.ir) refuse automated requests, so its reports are read from the unchanged "
            "copies the Iran Data Portal at Syracuse University republishes."
        ),
    )
    for artifact in artifacts():
        p.artifact(artifact, MOI)
    p.add("body", id=MAJLIS, polity_id=IRAN, kind="unicameral")
    p.name(MAJLIS, "مجلس شورای اسلامی", "fa", "official")
    p.name(MAJLIS, "Islamic Consultative Assembly", "en", "english", False)
    for id, since, until, majority, text in SYSTEMS.values():
        p.add(
            "electoral_system",
            id=id,
            polity_id=IRAN,
            valid_from=since,
            valid_to=until,
            rules={"formula": "two_round", "majority": majority, "advance": 2},
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    return p


class Builder:
    def __init__(self, p: Package, year: int, day: date) -> None:
        self.p = p
        self.year = year
        self.day = day
        self.election = key("election", day.isoformat())
        self.path = f"ir/{day.isoformat()}-majlis"

    def election_row(self, name_fa: str, name_en: str) -> None:
        p = self.p
        p.add(
            "election",
            id=self.election,
            polity_id=IRAN,
            kind="general",
            date_from=self.day,
            date_to=self.day,
            status="held",
        )
        p.add("slug", path=self.path, entity_id=self.election, is_current=True)
        p.name(self.election, name_fa, "fa", "official")
        p.name(self.election, name_en, "en", "english", False)

    def contest(
        self, code: str, area: str, seats: int, name: str, english: str | None, slug: str, method: str
    ) -> tuple[str, str, str]:
        p = self.p
        contest = key("contest", self.year, code)
        p.add(
            "contest",
            id=contest,
            election_id=self.election,
            kind="body_seats",
            body_id=MAJLIS,
            district_area_id=area,
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEMS[self.year][0],
            default_result_kind="counted",
        )
        p.name(contest, name, "fa", "official")
        if english:
            p.name(contest, english, "en", "english", False)
        p.add("slug", path=f"{self.path}/{slug}", entity_id=contest, is_current=True)
        unit = key("unit", self.year, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=IRAN,
            scheme_code="ir.majlis_district",
            code=f"{self.year}:{code}",
            area_relation="within" if area == COUNTRY else "equals",
            area_id=area,
            kind="standard",
        )
        p.name(unit, name, "fa", "official")
        result_set = key("result_set", self.year, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=MOI,
            status="active",
            is_default=True,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        return contest, unit, result_set

    def candidate(
        self,
        contest: str,
        unit: str,
        result_set: str,
        tag: str,
        position: int,
        name: str,
        votes: int,
        outcome: str,
        gender: str | None = None,
    ) -> None:
        p = self.p
        option = key("option", self.year, tag)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=name,
        )
        person = key("person", self.year, tag)
        p.add("person", id=person, gender=gender)
        p.name(person, name, "fa", "official")
        p.add("candidacy", id=key("candidacy", self.year, tag), person_id=person, option_id=option, role="head")
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=1,
            count_round=0,
            flags=0,
            value=votes,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)


def election_2012() -> Package:
    p = Package(
        f"{SOURCE}/2012-03-02",
        SOURCE,
        "The first round of the 2 March 2012 Majlis election: candidate votes in the 202 territorial constituencies",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {IRAN}
    p.uses(raw.cached(SOURCE, REPORT_2012))
    b = Builder(p, 2012, DAY_2012)
    b.election_row("انتخابات نهمین دوره مجلس شورای اسلامی", "2012 Iranian legislative election")
    for district in report_2012():
        english = PROVINCES[district.province][0]
        contest, unit, result_set = b.contest(
            district.code,
            district_area(2012, district.code),
            district.seats,
            district.name,
            f"{english} Province, constituency {district.number}",
            f"{district.province}-{district.number}",
            METHOD_2012,
        )
        runoff = any(row.remark == SECOND for row in district.rows)
        for row in district.rows:
            outcome = {FIRST: "elected", SECOND: "advanced"}.get(row.remark or "", "eliminated" if runoff else "lost")
            tag = f"{district.code}:{row.position}"
            b.candidate(contest, unit, result_set, tag, row.position, row.name, row.votes, outcome)
    return p


def election_2024() -> Package:
    p = Package(
        f"{SOURCE}/2024-03-01",
        SOURCE,
        "The first round of the 1 March 2024 Majlis election: the members elected in the first round, with their votes "
        "and each constituency's valid votes",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {IRAN, COUNTRY}
    p.uses(raw.cached(SOURCE, ELECTED_2024))
    p.uses(raw.cached(SOURCE, DISTRICTS_2024))
    for url in candidate_urls():
        p.uses(raw.cached(SOURCE, url))
    b = Builder(p, 2024, DAY_2024)
    b.election_row("انتخابات دوازدهمین دوره مجلس شورای اسلامی", "2024 Iranian legislative election")
    english_minority = {code: english for code, _, english in MINORITIES}
    for district in districts_2024():
        if district.minority:
            area = COUNTRY
            english = f"Religious minorities: {english_minority[district.minority]}"
            slug = district.minority
        else:
            area = district_area(2024, district.code)
            english = f"{PROVINCES[district.province][0]} Province, constituency {district.number}"
            slug = f"{district.province}-{district.number}"
        contest, unit, result_set = b.contest(
            district.code, area, district.seats, district.name, english, slug, METHOD_2024
        )
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=None,
            channel=0,
            measure=4,
            count_round=0,
            flags=0,
            value=district.valid,
        )
        ranked = sorted(district.elected, key=lambda row: -row.votes)
        for position, row in enumerate(ranked, 1):
            tag = f"{district.code}:{row.code}"
            gender = "female" if row.female else None
            b.candidate(contest, unit, result_set, tag, position, row.name, row.votes, "elected", gender)
    p.accept(
        "accounting.votes_valid",
        "The Ministry's list gives each constituency's valid votes but only the votes of the members elected in the "
        "first round, so in a single-seat constituency the one listed candidate's votes fall short of the valid votes.",
    )
    return p
