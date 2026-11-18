"""Iran's presidential elections, from the Ministry of Interior's announced results.

The Ministry's own hosts (moi.ir, interior.gov.ir, data.moi.ir) refuse automated requests, so the results are read from
the copies other hosts republish:

- 2005 (both rounds): votes by province from a compilation the Iran Data Portal at Syracuse University republishes,
  with each province's ballots cast and registered voters from the portal's workbook of presidential turnout.
- 2009: the Ministry's tables of votes by county (shahrestan) and its table of votes cast abroad, as the portal
  republishes them.
- 2013: the Ministry's official workbook of votes by county and district (bakhsh), as the portal republishes it.
- 2017: the Ministry's announcement of votes by county, as Eghtesadonline printed it.

The portal's workbook also gives each province's registered voters and ballots cast for every election from 1980 to
2013, which this module uses only to cross-check the figures it reads.
"""

import html
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import get_close_matches
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ir import moi

SOURCE = "ir.president"
IRAN = moi.IRAN
COUNTRY = moi.COUNTRY
PORTAL = moi.PORTAL
GEOBOUNDARIES = moi.GEOBOUNDARIES
UA = moi.UA
TURNOUT = PORTAL + "Iran-Presidential-Elections-All-Provinces-1.xlsx"
PDF_2005 = PORTAL + "2005-Presidential-Election-Results-1st-2nd-rounds.pdf"
XLS_2009 = PORTAL + "Excel-data-of-10th-Presidential-elections-based-on-Ostan-Important-Source.xls"
ABROAD_2009 = PORTAL + "2009-Presidential-vote-Outside-Iran.pdf"
XLSX_2013 = PORTAL + "official-election-results.2013.xlsx"
PAGE_2017 = "https://www.eghtesadonline.com/fa/news/198378/"
FILES: dict[str, str] = {
    TURNOUT: ".xlsx",
    PDF_2005: ".pdf",
    XLS_2009: ".xls",
    ABROAD_2009: ".pdf",
    XLSX_2013: ".xlsx",
    PAGE_2017: ".html",
}
VINTAGE_2005 = "2005"
BIDI = dict.fromkeys([*range(0x200C, 0x2010), *range(0x202A, 0x202F), *range(0x2066, 0x206A)])


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


PRESIDENCY = key("office", "president")
SYSTEM = key("electoral_system", "president")
PORTAL_SOURCE = key("source", "portal")
PDF_SOURCE = key("source", "compilation-2005")
NEWS_SOURCE = key("source", "eghtesadonline-2017")
GB_SOURCE = key("source", "geoboundaries")


@dataclass(frozen=True)
class Candidate:
    slug: str
    fa: str
    en: str


CANDIDATES = {
    c.slug: c
    for c in [
        Candidate("mehralizadeh", "محسن مهرعلیزاده", "Mohsen Mehralizadeh"),
        Candidate("rafsanjani", "اکبر هاشمی رفسنجانی", "Akbar Hashemi Rafsanjani"),
        Candidate("ahmadinejad", "محمود احمدی‌نژاد", "Mahmoud Ahmadinejad"),
        Candidate("moin", "مصطفی معین", "Mostafa Moin"),
        Candidate("ghalibaf", "محمدباقر قالیباف", "Mohammad Bagher Ghalibaf"),
        Candidate("karroubi", "مهدی کروبی", "Mehdi Karroubi"),
        Candidate("larijani", "علی‌اردشیر لاریجانی", "Ali Ardeshir Larijani"),
        Candidate("rezaei", "محسن رضایی", "Mohsen Rezaei"),
        Candidate("mousavi", "میرحسین موسوی", "Mir-Hossein Mousavi"),
        Candidate("rouhani", "حسن روحانی", "Hassan Rouhani"),
        Candidate("velayati", "علی‌اکبر ولایتی", "Ali Akbar Velayati"),
        Candidate("jalili", "سعید جلیلی", "Saeed Jalili"),
        Candidate("gharazi", "محمد غرضی", "Mohammad Gharazi"),
        Candidate("raisi", "سید ابراهیم رئیسی", "Ebrahim Raisi"),
        Candidate("mirsalim", "سید مصطفی آقامیرسلیم", "Mostafa Mir-Salim"),
        Candidate("hashemitaba", "سید مصطفی هاشمی‌طبا", "Mostafa Hashemitaba"),
    ]
}
# What each election's printed candidate name contains, for matching.
MATCH = {
    "mehralizadeh": ("علیزاده",),
    "rafsanjani": ("رفسنجانی", "هاشمی"),
    "ahmadinejad": ("نژاد",),
    "moin": ("معین",),
    "ghalibaf": ("قالیباف",),
    "karroubi": ("کروبی",),
    "larijani": ("الریجانی", "لاریجانی"),
    "rezaei": ("رضایی", "رضائی"),
    "mousavi": ("موسوی",),
    "rouhani": ("روحانی", "Ruhani"),
    "velayati": ("ولایتی", "Velayati"),
    "jalili": ("جلیلی", "Jalili"),
    "gharazi": ("غرضی", "Gharazi"),
    "raisi": ("رئیسی",),
    "mirsalim": ("میر سلیم", "میرسلیم"),
    "hashemitaba": ("هاشمی طبا", "هاشمی‌طبا"),
}


@dataclass(frozen=True)
class Election:
    day: date
    round: int
    year: int
    names: tuple[str, str]
    source: str
    candidates: tuple[str, ...]  # in ballot order
    winner: str
    advanced: tuple[str, ...] = ()
    unit: str = "ir.shahrestan"


ELECTIONS = [
    Election(
        date(2005, 6, 17),
        1,
        2005,
        ("2005 Iranian presidential election, first round", "نهمین دوره انتخابات ریاست جمهوری، دور اول"),
        PDF_SOURCE,
        ("mehralizadeh", "rafsanjani", "ahmadinejad", "moin", "ghalibaf", "karroubi", "larijani"),
        "",
        ("ahmadinejad", "rafsanjani"),
        "ir.ostan",
    ),
    Election(
        date(2005, 6, 24),
        2,
        2005,
        ("2005 Iranian presidential election, second round", "نهمین دوره انتخابات ریاست جمهوری، دور دوم"),
        PDF_SOURCE,
        ("ahmadinejad", "rafsanjani"),
        "ahmadinejad",
        (),
        "ir.ostan",
    ),
    Election(
        date(2009, 6, 12),
        1,
        2009,
        ("2009 Iranian presidential election", "دهمین دوره انتخابات ریاست جمهوری"),
        PORTAL_SOURCE,
        ("ahmadinejad", "rezaei", "karroubi", "mousavi"),
        "ahmadinejad",
    ),
    Election(
        date(2013, 6, 14),
        1,
        2013,
        ("2013 Iranian presidential election", "یازدهمین دوره انتخابات ریاست جمهوری"),
        PORTAL_SOURCE,
        ("rouhani", "velayati", "jalili", "ghalibaf", "rezaei", "gharazi"),
        "rouhani",
        (),
        "ir.bakhsh",
    ),
    Election(
        date(2017, 5, 19),
        1,
        2017,
        ("2017 Iranian presidential election", "دوازدهمین دوره انتخابات ریاست جمهوری"),
        NEWS_SOURCE,
        ("rouhani", "raisi", "mirsalim", "hashemitaba"),
        "rouhani",
    ),
]
BY_DAY = {e.day: e for e in ELECTIONS}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.day}" for e in ELECTIONS)]


def fetch() -> None:
    for url, suffix in FILES.items():
        raw.fetch(SOURCE, url, suffix=suffix)
    raw.fetch(GEOBOUNDARIES, moi.ADM1, suffix=".geojson")


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        election(e).write()


def emit() -> None:
    package.run("load", packages())


# Text


def fold(text: str) -> str:
    """Persian letters in one form, no spaces, no 'province' prefix, so that spellings of a name compare equal."""
    text = unicodedata.normalize("NFKC", text).translate(BIDI)
    text = text.replace("ي", "ی").replace("ك", "ک").replace("ى", "ی").replace("ە", "ه").replace("ۀ", "ه")
    text = re.sub(r"[ً-ٟ]", "", text)
    text = re.sub(r"^\s*استان\s+", "", text)
    return re.sub(r"[\s \-–.،:]", "", text)


def clean(text: str) -> str:
    """A printed name in Persian letters with single spaces."""
    text = unicodedata.normalize("NFKC", text).translate(BIDI)
    text = text.replace("ي", "ی").replace("ك", "ک").replace("ى", "ی")
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


PROVINCE_ALIASES = {"گیالن": "gilan", "کهکیلویهوبویراحمد": "kohgiluyeh-and-boyer-ahmad"}
PROVINCE_FOLDED = {fold(persian): code for code, (_, persian) in moi.PROVINCES.items()} | PROVINCE_ALIASES
for _abbreviation, _code in (("آذرشرقی", "east-azerbaijan"), ("آذرغربی", "west-azerbaijan")):
    PROVINCE_FOLDED[_abbreviation] = _code


def province_of(text: str) -> str:
    folded = fold(text)
    if folded in PROVINCE_FOLDED:
        return PROVINCE_FOLDED[folded]
    close = get_close_matches(folded, list(PROVINCE_FOLDED), n=1, cutoff=0.8)
    if not close:
        raise ValueError(f"unknown province {text!r}")
    return PROVINCE_FOLDED[close[0]]


def candidate_of(text: str) -> str:
    folded = fold(text)
    hits = [slug for slug, words in MATCH.items() if any(fold(word) in folded for word in words)]
    if len(hits) != 1:
        raise ValueError(f"cannot tell the candidate in {text!r}: {hits}")
    return hits[0]


def number(value: object) -> int:
    text = str(value).replace(",", "").replace("٬", "").strip()
    if not re.fullmatch(r"\d+(\.0+)?", text):
        raise ValueError(f"not a count: {value!r}")
    return int(float(text))


# Reading the files


def cached(url: str) -> raw.Artifact:
    return raw.cached(SOURCE, url)


def sheets(url: str) -> dict[str, pl.DataFrame]:
    book = pl.read_excel(cached(url).path, sheet_id=0, has_header=False, infer_schema_length=0)
    if not isinstance(book, dict):
        raise TypeError(f"{url} did not read as a workbook")
    return book


def turnout() -> dict[tuple[int, date], dict[str, tuple[int, int]]]:
    """Registered voters and ballots cast in each province, for each election of the portal's workbook."""
    frame = sheets(TURNOUT)["Sheet1"]
    rows = frame.rows()
    heads = rows[0]
    days = {
        "9th Presidential Election (first round) (2005)": date(2005, 6, 17),
        "9th Presidential Election (second round) (2005)": date(2005, 6, 24),
        "10th Presidential Election (2009)": date(2009, 6, 12),
        "11th Presidential Election (2013)": date(2013, 6, 14),
    }
    out: dict[tuple[int, date], dict[str, tuple[int, int]]] = {}
    for index, head in enumerate(heads):
        if head not in days:
            continue
        table: dict[str, tuple[int, int]] = {}
        for row in rows[2:]:
            name = str(row[0])
            if name.startswith("Iran") or row[index] in (None, "NA"):
                continue
            table[name] = (number(row[index]), number(row[index + 1]))
        out[(0, days[str(head)])] = table
    return out


TURNOUT_NAMES = {
    "East Azerbaijan": "east-azerbaijan",
    "West Azerbaijan": "west-azerbaijan",
    "Ardabil": "ardabil",
    "Esfahan": "isfahan",
    "Alborz": "alborz",
    "Ilam": "ilam",
    "Bushehr": "bushehr",
    "Tehran": "tehran",
    "Chahar Mahal and Bakhtiari": "chaharmahal-and-bakhtiari",
    "South Khorasan": "south-khorasan",
    "Razavi Khorasan": "razavi-khorasan",
    "North Khorasan": "north-khorasan",
    "Khuzestan": "khuzestan",
    "Zanjan": "zanjan",
    "Semnan": "semnan",
    "Sistan and Baluchestan": "sistan-and-baluchestan",
    "Fars": "fars",
    "Qazvin": "qazvin",
    "Qom": "qom",
    "Kurdistan": "kurdistan",
    "Kerman": "kerman",
    "Kermanshah": "kermanshah",
    "Kohgiluyeh and Buyer Ahmad": "kohgiluyeh-and-boyer-ahmad",
    "Golestan": "golestan",
    "Gilan": "gilan",
    "Lorestan": "lorestan",
    "Mazandaran": "mazandaran",
    "Markazi": "markazi",
    "Hormozgan": "hormozgan",
    "Hamadan": "hamadan",
    "Yazd": "yazd",
}


@dataclass
class Row:
    """One reporting unit's counts: candidate votes, and any of registered, cast, valid, and invalid."""

    province: str
    name: str  # the county or district, empty for a province
    votes: dict[str, int]
    registered: int | None = None
    cast: int | None = None
    valid: int | None = None
    invalid: int | None = None
    parent: str = ""  # the county, for a district


def turnout_by_province(day: date) -> dict[str, tuple[int, int]]:
    table = turnout()[(0, day)]
    return {TURNOUT_NAMES[name.strip()]: counts for name, counts in table.items()}


def layout(url: str) -> str:
    out = subprocess.run(["pdftotext", str(cached(url).path), "-"], capture_output=True, check=True, text=True)
    return unicodedata.normalize("NFKC", out.stdout).translate(BIDI)


def rows_2005() -> dict[int, list[Row]]:
    """Votes in each province, from the compilation of the 2005 results, with its stated totals."""
    text = layout(PDF_2005).replace("ي", "ی").replace("ك", "ک").replace("ى", "ی")
    province: str | None = None
    round = 0
    seen: dict[tuple[str, int], Row] = {}
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith(("http", "آمار")):
            continue
        if line in ("دور اول", "دوره اول"):
            round = 1
            seen[(province or "", 1)] = Row(province or "", "", {})
            continue
        if line == "دور دوم":
            round = 2
            seen[(province or "", 2)] = Row(province or "", "", {})
            continue
        if re.search(r"واجدان|مشارکت|مشرکت|هزار و\s+\d+نفر", line):
            continue
        try:
            slug = candidate_of(line)
        except ValueError:
            slug = None
        current = seen.get((province or "", round))
        if "کل آرا" in line or "شرکت کنندگان" in line:
            assert current is not None
            current.cast = number(re.findall(r"\d+", line.replace(" ", ""))[-1])
        elif slug and current is not None:
            current.votes[slug] = votes_in(line)
        elif not slug:
            province = province_of(line)
    out: dict[int, list[Row]] = {1: [], 2: []}
    for (code, round), row in seen.items():
        row.province = code
        out[round].append(row)
    return out


NUMBER_WORDS = {"یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "هفت": 7, "هشت": 8, "نه": 9}


def votes_in(line: str) -> int:
    """The count at the end of a candidate's line, which one province prints in words and thousands."""
    if "میلیون" in line:
        word = re.search(r"(" + "|".join(NUMBER_WORDS) + r")\s+میلیون", line)
        if word is None:
            raise ValueError(f"cannot read {line!r}")
        millions = NUMBER_WORDS[word.group(1)]
        thousands, rest = (int(n) for n in re.findall(r"\d+", line)[-2:])
        return millions * 1_000_000 + thousands * 1000 + rest
    digits = re.findall(r"\d+", line)
    return int(digits[-1])


def rows_2009() -> list[Row]:
    out: list[Row] = []
    names = ("ahmadinejad", "rezaei", "karroubi", "mousavi")
    for name, frame in sheets(XLS_2009).items():
        if name.startswith("داخل"):
            continue
        for row in frame.rows():
            if row[0] is None or row[1] is None or row[0] == "استان" or not str(row[2]).strip().isdigit():
                continue
            out.append(
                Row(
                    province_of(str(row[0])),
                    clean(str(row[1])),
                    {slug: number(row[2 + i]) for i, slug in enumerate(names)},
                    cast=number(row[6]),
                    invalid=number(row[7]),
                    valid=number(row[8]),
                )
            )
    return out


def national_2009() -> dict[str, int]:
    frame = sheets(XLS_2009)["داخل و خارج از كشور"]
    row = frame.rows()[1]
    names = ("ahmadinejad", "rezaei", "karroubi", "mousavi")
    out = {slug: number(row[1 + i]) for i, slug in enumerate(names)}
    out |= {"cast": number(row[5]), "invalid": number(row[6]), "valid": number(row[7])}
    return out


def abroad_2009() -> dict[str, int]:
    """The votes cast at the Iranian missions abroad, summed over the Ministry's rows for each mission."""
    text = unicodedata.normalize("NFKC", layout_flat(ABROAD_2009))
    totals = [0] * 7
    count = 0
    for line in text.split("\n"):
        numbers = re.findall(r"\d+", line)
        if len(numbers) == 7 and re.match(r"^\s*\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s", line):
            values = [int(n) for n in numbers]
            totals = [a + b for a, b in zip(totals, values, strict=True)]
            count += 1
    return {
        "mousavi": totals[0],
        "karroubi": totals[1],
        "rezaei": totals[2],
        "ahmadinejad": totals[3],
        "blank": totals[4],
        "invalid": totals[5],
        "cast": totals[6],
        "missions": count,
    }


def layout_flat(url: str) -> str:
    out = subprocess.run(
        ["pdftotext", "-layout", str(cached(url).path), "-"], capture_output=True, check=True, text=True
    )
    return out.stdout.translate(BIDI)


def rows_2013() -> list[Row]:
    frame = sheets(XLSX_2013)["Votes Per County"]
    names = ("rouhani", "velayati", "jalili", "ghalibaf", "rezaei", "gharazi")
    out: list[Row] = []
    for row in frame.rows()[1:]:
        out.append(
            Row(
                province_of(str(row[0])),
                clean(str(row[2])),
                {slug: number(row[3 + i]) for i, slug in enumerate(names)},
                parent=clean(str(row[1])),
            )
        )
    return out


def provinces_2013() -> dict[str, dict[str, int]]:
    frame = sheets(XLSX_2013)["Votes Per Province"]
    names = ("rouhani", "velayati", "jalili", "ghalibaf", "rezaei", "gharazi")
    return {
        province_of(str(row[0])): {slug: number(row[1 + i]) for i, slug in enumerate(names)}
        for row in frame.rows()[3:]
        if row[0] and row[1] and not str(row[0]).startswith(("(", "Votes", "Total"))
    }


# Two of the 2017 announcement's provincial tables are garbled. Chaharmahal and Bakhtiari's drops a candidate's column
# and repeats the ballots cast as the valid votes, so only that province's total is used. Razavi Khorasan's prints no
# ballots-cast column.
BROKEN_2017 = {"chaharmahal-and-bakhtiari"}
SHORT_2017 = {"razavi-khorasan"}


def rows_2017() -> tuple[list[Row], dict[str, dict[str, int]], dict[str, int], dict[str, int]]:
    """Counties, each province's total as the national table prints it, the votes cast abroad, and the nation's."""
    page = cached(PAGE_2017).path.read_text("utf-8")
    body = page[page.index("نتیجه تفضیلی") :]
    names = ("rouhani", "raisi", "mirsalim", "hashemitaba")
    heading: str | None = None
    table: str | None = None  # the province whose table is being read; None in the national table
    own: dict[str, dict[str, int]] = {}
    rows: list[Row] = []
    totals: dict[str, dict[str, int]] = {}
    abroad: dict[str, int] = {}
    national: dict[str, int] = {}
    for found in re.finditer(r"استان ([^<:]{2,40}):|(<table)|<tr>(.*?)</tr>", body, re.DOTALL):
        if found.group(1):
            heading = province_of(found.group(1).replace("&nbsp;", ""))
            continue
        if found.group(2):
            table, heading = heading, None
            continue
        cells = [
            clean(html.unescape(re.sub(r"<[^>]+>", " ", c)))
            for c in re.findall(r"<td[^>]*>(.*?)</td>", found.group(3), re.DOTALL)
        ]
        while cells and cells[-1] == "":
            cells.pop()
        if cells and cells[0] == "ردیف":
            continue
        if table in BROKEN_2017:
            continue
        if table in SHORT_2017 and len(cells) == 8 and cells[0].isdigit():
            # This table prints no ballots-cast column: invalid, valid, and the four candidates.
            counts = [number(c) for c in cells[2:]]
            votes = dict(zip(names, counts[2:], strict=True))
            rows.append(Row(table, cells[1], votes, invalid=counts[0], valid=counts[1]))
            continue
        if len(cells) == 9:
            label, figures = cells[1], cells[2:]
        elif len(cells) in (7, 8) and cells[0] in ("جمع کل", "مجموع"):
            label, figures = cells[0], cells[1:]
        else:
            continue
        if len(figures) != 7:
            continue
        counts = [number(c) for c in figures]
        votes = dict(zip(names, counts[3:], strict=True))
        figures_of = {"cast": counts[0], "invalid": counts[1], "valid": counts[2], **votes}
        if label == "خارج از کشور":
            abroad = figures_of
        elif label in ("جمع کل", "مجموع"):
            if table is None:
                national = figures_of
            else:
                own[table] = figures_of
        elif table is None:
            totals[province_of(label)] = figures_of
        else:
            rows.append(Row(table, label, votes, cast=counts[0], invalid=counts[1], valid=counts[2]))
    return rows, totals, abroad, national


def usable_2005() -> tuple[dict[int, list[Row]], list[str]]:
    """The provinces whose lines in the compilation agree with the workbook's ballots cast, and those left out.

    The compilation has transcription slips: a missing candidate, a digit dropped or doubled, a province printed
    from its capital alone. A province-round is kept only when it has every candidate and its candidates' votes fall
    between 90 and 100 percent of the ballots cast the workbook gives.
    """
    rows = rows_2005()
    kept: dict[int, list[Row]] = {1: [], 2: []}
    left: list[str] = []
    for round, day in ((1, ELECTIONS[0].day), (2, ELECTIONS[1].day)):
        table = turnout_by_province(day)
        expected = len(ELECTIONS[round - 1].candidates)
        for row in rows[round]:
            registered, cast = table[row.province]
            total = sum(row.votes.values())
            if len(row.votes) == expected and 0.9 * cast <= total <= cast:
                row.registered, row.cast = registered, cast
                kept[round].append(row)
            else:
                left.append(f"{row.province} round {round}")
    return kept, left


# Packages


def province_area(year: int, code: str) -> str:
    """A province's area: the provinces of 2005 (with Alborz inside Tehran) until 2009, then those of 2017."""
    return key("area", "ir.ostan", f"{VINTAGE_2005}:{code}") if year <= 2009 else moi.province_area(code)


def county_area(year: int, province: str, name: str, parent: str = "") -> str:
    return key("area", "ir.bakhsh" if parent else "ir.shahrestan", year, province, parent, name)


def counties(e: Election) -> list[Row]:
    if e.year == 2009:
        return rows_2009()
    if e.year == 2013:
        return rows_2013()
    return rows_2017()[0]


def vintages() -> dict[int, list[Row]]:
    return {e.year: counties(e) for e in ELECTIONS if e.unit != "ir.ostan"}


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Iran's provinces of 2005 and the counties and districts the presidential results of 2009 to 2017 report",
    )
    p.external |= {IRAN, COUNTRY}
    artifact = raw.cached(GEOBOUNDARIES, moi.ADM1)
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
    for code, rank, noun in (("ir.shahrestan", 2, "county"), ("ir.bakhsh", 3, "district")):
        p.add("scheme", code=code, polity_id=IRAN, kind="administrative", rank=rank, noun=noun)
    p.add(
        "boundary_set",
        id=key("boundary_set", "ir.ostan", VINTAGE_2005),
        scheme_code="ir.ostan",
        vintage=VINTAGE_2005,
        as_of=ELECTIONS[0].day,
    )
    shapes: dict[str, list[shapely.Geometry]] = {}
    for feature in json.loads(artifact.path.read_text("utf-8"))["features"]:
        shapes.setdefault(str(feature["properties"]["shapeName"]), []).append(
            shapely.geometry.shape(feature["geometry"])
        )
    for code, (english, persian) in moi.PROVINCES.items():
        if code == "alborz":
            continue
        id = province_area(2005, code)
        p.add("area", id=id, scheme_code="ir.ostan")
        p.add("area_code", area_id=id, code=f"{VINTAGE_2005}:{code}")
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, f"استان {persian}", "fa", "native")
        p.name(id, f"{english} Province", "en", "english", False)
        pieces = list(shapes[english]) + (list(shapes["Alborz"]) if code == "tehran" else [])
        shape = shapely.make_valid(shapely.union_all(pieces))
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
    for year, rows in vintages().items():
        day = next(e.day for e in ELECTIONS if e.year == year)
        for scheme in ("ir.shahrestan", *(("ir.bakhsh",) if year == 2013 else ())):
            p.add(
                "boundary_set",
                id=key("boundary_set", scheme, year),
                scheme_code=scheme,
                vintage=str(year),
                as_of=day,
            )
        seen: set[tuple[str, str]] = set()
        for row in rows:
            parent_name = row.parent or row.name
            if (row.province, parent_name) not in seen:
                seen.add((row.province, parent_name))
                id = county_area(year, row.province, parent_name)
                p.add("area", id=id, scheme_code="ir.shahrestan")
                p.add("area_code", area_id=id, code=f"{year}:{row.province}:{parent_name}")
                p.add("area_parent", area_id=id, parent_id=province_area(year, row.province))
                p.name(id, f"شهرستان {parent_name}", "fa", "official")
            if row.parent:
                id = county_area(year, row.province, row.name, row.parent)
                p.add("area", id=id, scheme_code="ir.bakhsh")
                p.add("area_code", area_id=id, code=f"{year}:{row.province}:{row.parent}:{row.name}")
                p.add("area_parent", area_id=id, parent_id=county_area(year, row.province, row.parent))
                p.name(id, f"بخش {row.name} شهرستان {row.parent}", "fa", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Iran's presidency, its electoral system, and the sources of the Ministry of Interior's presidential results",
    )
    p.external.add(IRAN)
    p.add(
        "source",
        id=PORTAL_SOURCE,
        kind="official_body",
        publisher="وزارت کشور، ستاد انتخابات کشور (Ministry of Interior, Election Headquarters)",
        title="Presidential election results by county, as republished by the Iran Data Portal",
        url="https://irandataportal.syr.edu/presidential-elections/",
        license="Published by the Ministry with no license stated",
        attribution="Ministry of Interior of the Islamic Republic of Iran",
        notes=(
            "The Ministry's hosts (moi.ir) refuse automated requests, so its tables are read from the copies the "
            "Iran Data Portal at Syracuse University republishes: the 2009 provincial workbooks and abroad table, "
            "the official 2013 workbook, and a workbook of provincial turnout for 1980 to 2013."
        ),
    )
    p.add(
        "source",
        id=PDF_SOURCE,
        kind="community",
        publisher="Iran Data Portal (Syracuse University), from a compilation on the iranian-it.net forum",
        title="Statistics of the votes of the ninth presidential election by province",
        url=PDF_2005,
        license="Published with no license stated",
        attribution="Iran Data Portal, Syracuse University",
        notes=(
            "A hand-typed compilation of each province's results in both rounds of 2005. It has transcription slips, "
            "so the archive keeps only the provinces whose candidate votes agree with the ballots cast in the "
            "portal's turnout workbook."
        ),
    )
    p.add(
        "source",
        id=NEWS_SOURCE,
        kind="news_agency",
        publisher="Eghtesadonline (اقتصادآنلاین)",
        title="Detailed results of the twelfth presidential election by province and county, as announced by the "
        "Ministry of Interior",
        url=PAGE_2017,
        license="Published with no license stated",
        attribution="Eghtesadonline, reproducing the Ministry of Interior's announcement",
        notes=(
            "The article reproduces the Ministry's announcement of the 2017 results by county and a national table "
            "by province and abroad."
        ),
    )
    for url, source in (
        (TURNOUT, PORTAL_SOURCE),
        (XLS_2009, PORTAL_SOURCE),
        (ABROAD_2009, PORTAL_SOURCE),
        (XLSX_2013, PORTAL_SOURCE),
        (PDF_2005, PDF_SOURCE),
        (PAGE_2017, NEWS_SOURCE),
    ):
        p.artifact(cached(url), source)
    p.add("office", id=PRESIDENCY, polity_id=IRAN, kind="head_of_government")
    p.name(PRESIDENCY, "President of Iran", "en", "official")
    p.name(PRESIDENCY, "رئیس‌جمهور ایران", "fa", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=IRAN,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "The President is elected directly for four years (Constitution, art. 114). A candidate needs more than "
            "half of the votes cast; otherwise the two leading candidates meet in a second round on the Friday of "
            "the following week (art. 117)."
        ),
        lang="en",
    )
    return p


ACCEPTED = {
    2009: (
        "The Ministry's national table gives 39,371,214 ballots and 38,951,043 valid votes. Its 366 county rows and "
        "its table of the missions abroad add up to 39,343,628 and 38,728,799, 27,586 and 222,244 short, and 526 "
        "more Ahmadinejad votes than the national table. Neither table says where the difference lies."
    ),
    2017: (
        "The announcement's national table gives 41,366,085 ballots, but Razavi Khorasan's county table prints no "
        "ballots-cast column, so the units' ballots cast leave out that province's 3,456,382. The "
        "county rows of West Azerbaijan and Isfahan differ from those provinces' totals by 3 to 15 votes, and "
        "the national table's invalid and candidate counts differ from the provinces' sums by up to 30 votes."
    ),
}


def election(e: Election) -> Package:
    first = e.round == 1
    path = f"ir/{e.day}-presidential" + ("" if first else "-runoff")
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"]
    if not first:
        depends.append(f"{SOURCE}/{ELECTIONS[0].day}")
    p = Package(
        f"{SOURCE}/{e.day}", SOURCE, e.names[0] + ", by " + ("province" if e.unit == "ir.ostan" else "county"), depends
    )
    p.external |= {IRAN, COUNTRY}
    for url in {2005: [PDF_2005, TURNOUT], 2009: [XLS_2009, ABROAD_2009], 2013: [XLSX_2013], 2017: [PAGE_2017]}[e.year]:
        p.uses(cached(url))
    election_id = key("election", e.day)
    p.add(
        "election",
        id=election_id,
        polity_id=IRAN,
        kind="general" if first else "runoff",
        date_from=e.day,
        date_to=e.day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, e.names[0], "en", "english")
    p.name(election_id, e.names[1], "fa", "native", False)
    contest = key("contest", e.day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=COUNTRY,
        magnitude=1,
        round_number=e.round,
        previous_round_id=None if first else key("contest", ELECTIONS[0].day),
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, e.names[0], "en", "english")
    p.name(contest, e.names[1], "fa", "native", False)
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", e.day)
    official = e.source == PORTAL_SOURCE or e.source == NEWS_SOURCE
    options: dict[str, str] = {}
    for position, slug in enumerate(e.candidates, 1):
        c = CANDIDATES[slug]
        option = options[slug] = key("option", e.day, slug)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=c.fa,
        )
        person = key("person", e.day, slug)
        p.add("person", id=person)
        p.name(person, c.fa, "fa", "official")
        p.name(person, c.en, "en", "english", False)
        p.add(
            "candidacy",
            id=key("candidacy", e.day, slug),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.fa,
        )
        if slug == e.winner:
            kind = "elected"
        elif slug in e.advanced:
            kind = "advanced"
        else:
            kind = "eliminated" if e.advanced else "lost"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)

    values: list[tuple[str, str | None, int, int]] = []

    def add_unit(code: str, scheme: str, name: str, area: str | None, kind: str = "standard") -> str:
        id = key("unit", e.day, code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=IRAN,
            scheme_code=scheme,
            code=f"{e.day}:{code}",
            area_relation="equals" if area else "unlocated",
            area_id=area,
            kind=kind,
        )
        p.name(id, name, "fa", "official")
        return id

    def put(id: str, row: Row | dict[str, int]) -> None:
        votes = row.votes if isinstance(row, Row) else {k: v for k, v in row.items() if k in options}
        figures = (
            {2: row.registered, 3: row.cast, 4: row.valid, 5: row.invalid}
            if isinstance(row, Row)
            else {3: row.get("cast"), 4: row.get("valid"), 5: row.get("invalid")}
        )
        for slug, count in votes.items():
            values.append((id, options[slug], 1, count))
        for measure, count in figures.items():
            if count is not None:
                values.append((id, None, measure, count))

    method: str
    completeness = 1.0
    declared: dict[str, int] = {}
    if e.unit == "ir.ostan":
        kept, left = usable_2005()
        rows = kept[e.round]
        for row in rows:
            persian = moi.PROVINCES[row.province][1]
            put(add_unit(row.province, "ir.ostan", f"استان {persian}", province_area(2005, row.province)), row)
        every = turnout_by_province(e.day)
        completeness = round(sum(r.cast or 0 for r in rows) / sum(c for _, c in every.values()), 4)
        missing = [name for name in left if name.endswith(f"round {e.round}")]
        method = (
            f"Votes for each candidate in {len(rows)} of the 30 provinces of 2005, from a hand-typed compilation of "
            "the Ministry's announcements that has transcription slips; each province's ballots cast and registered "
            "voters are from the Iran Data Portal's turnout workbook, which the Ministry's own national total "
            "matches. A province is kept only when its candidates' votes fall between 90 and 100 percent of its "
            "ballots cast. Left out: " + (", ".join(n.rsplit(" round", 1)[0] for n in missing) or "none") + ". "
            "Votes cast abroad are not in the source. Alborz did not yet exist and is inside Tehran. The set is "
            "an alternative tally because it is partial."
        )
    elif e.year == 2009:
        for row in counties(e):
            persian = moi.PROVINCES[row.province][1]
            put(
                add_unit(
                    f"{row.province}:{row.name}",
                    "ir.shahrestan",
                    f"شهرستان {row.name}، استان {persian}",
                    county_area(2009, row.province, row.name),
                ),
                row,
            )
        declared = national_2009()
        abroad = abroad_2009()
        id = add_unit("abroad", "ir.nation", "ایرانیان خارج از کشور", None, "aggregate")
        put(id, {k: v for k, v in abroad.items() if k in options})
        values.append((id, None, 3, abroad["cast"]))
        values.append((id, None, 5, abroad["invalid"]))
        values.append((id, None, 6, abroad["blank"]))
        method = (
            "Votes for each candidate in each of the 366 counties, from the Ministry's provincial tables, with its "
            "table of the votes cast at Iran's missions abroad summed into one unit. The county tables and the "
            "abroad table together fall short of the Ministry's national totals."
        )
    elif e.year == 2013:
        for row in counties(e):
            persian = moi.PROVINCES[row.province][1]
            put(
                add_unit(
                    f"{row.province}:{row.parent}:{row.name}",
                    "ir.bakhsh",
                    f"بخش {row.name} شهرستان {row.parent}، استان {persian}",
                    county_area(2013, row.province, row.name, row.parent),
                ),
                row,
            )
        method = (
            "Votes for each candidate in each of the 1,039 districts (bakhsh) of the 2013 counties, from the "
            "Ministry's official workbook, whose provincial sheet matches the districts. The workbook prints no "
            "ballots cast, valid, or invalid figures by district, and no votes cast abroad."
        )
    else:
        rows, totals, abroad, declared = rows_2017()
        for row in rows:
            persian = moi.PROVINCES[row.province][1]
            put(
                add_unit(
                    f"{row.province}:{row.name}",
                    "ir.shahrestan",
                    f"شهرستان {row.name}، استان {persian}",
                    county_area(2017, row.province, row.name),
                ),
                row,
            )
        for province in BROKEN_2017:
            persian = moi.PROVINCES[province][1]
            put(add_unit(province, "ir.ostan", f"استان {persian}", moi.province_area(province)), totals[province])
        put(add_unit("abroad", "ir.nation", "ایرانیان خارج از کشور", None, "aggregate"), abroad)
        method = (
            "Votes for each candidate in each of the 421 counties and in Chaharmahal and Bakhtiari as a whole, "
            "whose county table in the announcement is garbled (it drops a candidate's column), from the Ministry's "
            "announcement as Eghtesadonline printed it, with the votes cast abroad. In Razavi Khorasan the "
            "announcement prints no ballots-cast column."
        )
    for slug, count in declared.items():
        if slug in options:
            p.add("declared", result_set_id=result_set, option_id=options[slug], measure=1, value=count)
    for name, measure in (("cast", 3), ("valid", 4), ("invalid", 5)):
        if name in declared:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=declared[name])
    if e.year in ACCEPTED:
        p.accept("accounting.declared", ACCEPTED[e.year])
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official" if official else "alternative",
        kind="counted",
        source_id=e.source,
        status="active",
        is_default=official,
        completeness=completeness,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    p.frame(
        "result_value",
        pl.DataFrame(values, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p
