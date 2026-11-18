"""Norway's national referendums of 1905-1994: the dissolution of the union with Sweden and the choice of King Haakon
(1905), the prohibition votes of 1919 and 1926, and the advisory votes on membership of the European Community (1972) and
the European Union (1994).

The county results come from Statistics Norway's historical statistics (table 25.8). The municipality results are
digitized from the scanned volumes of Norges offisielle statistikk on Statistics Norway's site: Alkoholstatistikk
1918-1919 (the 1919 vote), Forbudsavstemningen 18 oktober 1926, Folkeavstemningen om EF (A 522, 1972) and
Folkeavstemningen 1994 om norsk medlemskap i EU (C 235). Every digitized figure keeps its page and box.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.fi import digitized
from ea.sources.fi.digitized import Box
from ea.sources.no import scan

SOURCE = "no.referendums"
BASE = "https://www.ssb.no/a/histstat/nos"
PDF = {
    1919: f"{BASE}/nos_vi_190.pdf",
    1926: f"{BASE}/nos_viii_014.pdf",
    1972: f"{BASE}/nos_a522.pdf",
    1994: f"{BASE}/nos_c235.pdf",
}
TABLE_25_8 = "https://www.ssb.no/a/kortnavn/hist_tab/25-8.html"
HERE = Path(__file__).parent
# Rows the text layer cannot settle, read from the page images: year, then table, then row key, then the row's figures.
FIXES: dict[str, dict[str, dict[str, dict[str, object]]]] = json.loads((HERE / "referendum_fixes.json").read_text())


@dataclass
class Row:
    """One printed row: a place, and its figures with the page and box of each."""

    page: int
    code: str
    name: str
    values: dict[str, int] = field(default_factory=dict)
    boxes: dict[str, tuple[int, Box]] = field(default_factory=dict)
    note: str = ""


def clean(name: str) -> str:
    name = re.sub(r"[.\s]*$", "", name)
    return re.sub(r"\s+", " ", name).strip(" .,:;")


def tidy(text: str) -> str:
    """A word without the marks the recognition attached to it: a rule of the table read as a quote, a comma, or a letter."""
    text = text.replace("&apos;", "").replace("&quot;", "").replace("&amp;", "")
    text = re.sub(r"^[^\w]+|[^\w]+$", "", text)
    return re.sub(r"^(\d+)[jIil]$", r"\1", text)


def split(words: list[Box], marks: bool = False) -> tuple[str, list[Box]]:
    """The name of a row and the digit words that follow it. Leader dots the recognition read as zeros and marks are
    dropped from the name; once the figures begin, a word of zeros is the end of a thousands group."""
    name: list[str] = []
    digits: list[Box] = []
    for word in words:
        text = tidy(word.text) if marks else word.text
        if re.fullmatch(r"\d+", text) and not (len(text) > 4 and text.startswith("000")):
            if digits or text.strip("0"):
                digits.append(Box(text, word.x0, word.y0, word.x1, word.y1))
            continue
        if digits:
            continue
        if scan.NOISE.fullmatch(word.text) or not text:
            continue
        if re.search(r"[A-Za-zÆØÅæøåÀ-ÿ]", text):
            name.append(text)
    return clean(" ".join(name)), digits


@dataclass(frozen=True)
class Spec:
    """What a printed row holds: its figures, the sums among them, and any further condition they meet. `tail` counts the
    words after the last figure that the scan cut off."""

    fields: tuple[str, ...]
    sums: tuple[tuple[str, tuple[str, ...]], ...] = ()
    test: Callable[[dict[str, int]], bool] = lambda values: True
    tail: int = 0

    def holds(self, values: dict[str, int]) -> bool:
        return all(values[t] == sum(values[p] for p in parts) for t, parts in self.sums) and self.test(values)


def distance(a: str, b: str) -> int:
    """The edit distance of two figures, as the recognition errs: a character misread, dropped, or added."""
    if a == b:
        return 0
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True))
    if abs(len(a) - len(b)) > 1:
        return 9
    short, long = sorted((a, b), key=len)
    return 1 if any(long[:i] + long[i + 1 :] == short for i in range(len(long))) else 9


def pools(words: list[Box]) -> list[tuple[list[Box], str]]:
    zeros = [d for d in words if re.fullmatch(r"0|00|090", d.text)]
    suspects = [d for d in words if re.fullmatch(r"\d?000|\d{4,}", d.text) and len(d.text) >= 4]
    out = [(words, "")]
    for drop in (zeros, suspects, zeros + suspects):
        if drop:
            out.append(([d for d in words if d not in drop], "dropped a stray mark"))
    return out


@dataclass
class Reading:
    """A row's figures as read: the box of each, or None where the sums supplied a figure the scan lost."""

    boxes: list[Box | None]
    how: str
    derived: tuple[str, ...] = ()
    values: dict[str, int] = field(default_factory=dict)


def read_values(spec: Spec, cand: list[Box | None]) -> dict[str, int] | None:
    """The figures of a candidate reading, or None when one of them is lost or not a number."""
    values: dict[str, int] = {}
    for field_, box in zip(spec.fields, cand, strict=True):
        if box is None:
            continue
        value = scan.value(box.text)
        if value is None:
            return None
        values[field_] = value
    return values


def implied(spec: Spec, values: dict[str, int], target: str) -> int | None:
    """The value a sum of the table gives the figure `target`, when the sum's other figures are known."""
    for total, parts in spec.sums:
        if target not in (total, *parts) or any(x not in values for x in (total, *parts) if x != target):
            continue
        if target == total:
            return sum(values[part] for part in parts)
        return values[total] - sum(values[part] for part in parts if part != target)
    return None


def solve(digits: list[Box], spec: Spec) -> Reading | None:
    """The reading of a row's digit words whose figures satisfy the table's sums, and how it was reached. A row no
    reading satisfies is repaired when changing one figure by one character makes every sum hold, or when supplying one
    lost figure does, and only one such change does."""
    n = len(spec.fields)
    for cut in range(spec.tail + 1):
        words = digits[: len(digits) - cut] if cut else digits
        for pool, how in pools(words):
            good = []
            for cand in scan.segmentations(pool, n):
                values = read_values(spec, list(cand))
                if values is not None and len(values) == n and spec.holds(values):
                    good.append((cand, values))
            if good:
                cand, values = good[0]
                return Reading(list(cand), how + ("; ambiguous" if len(good) > 1 else ""), values=values)
    fixes: dict[tuple[int, ...], Reading] = {}
    fills: dict[tuple[int, ...], Reading] = {}
    for cut in range(spec.tail + 1):
        words = digits[: len(digits) - cut] if cut else digits
        for pool, _ in pools(words):
            for cand in scan.segmentations(pool, n):
                values = read_values(spec, list(cand))
                if values is None or len(values) != n:
                    continue
                broken = [t for t, parts in spec.sums if values[t] != sum(values[x] for x in parts)]
                for field_ in broken[:1]:
                    parts = dict(spec.sums)[field_]
                    for f in (field_, *parts):
                        fix = implied(spec, values, f)
                        if fix is None or fix < 0 or distance(str(values[f]), str(fix)) > 1:
                            continue
                        trial = {**values, f: fix}
                        if spec.holds(trial):
                            key = tuple(trial[x] for x in spec.fields)
                            fixes[key] = Reading(
                                list(cand), f"repaired {f}: read {values[f]}, sums give {fix}", (f,), trial
                            )
            for cand in scan.segmentations(pool, n - 1):
                for gap in range(n):
                    full: list[Box | None] = [*cand[:gap], None, *cand[gap:]]
                    known = read_values(spec, full)
                    if known is None:
                        continue
                    supplied = implied(spec, known, spec.fields[gap])
                    if supplied is None or supplied < 0:
                        continue
                    trial = {**known, spec.fields[gap]: supplied}
                    if spec.holds(trial):
                        key = tuple(trial[x] for x in spec.fields)
                        fills[key] = Reading(
                            full, f"supplied {spec.fields[gap]} from the row's sums", (spec.fields[gap],), trial
                        )
    for found in (fixes, fills):
        if len(found) == 1:
            return next(iter(found.values()))
        if found:
            return None
    return None


@dataclass
class Place:
    """A reporting place of one count: a municipality, a rural district, or a town, with its figures."""

    key: str
    name: str
    county: str
    page: int
    values: dict[str, int] = field(default_factory=dict)
    boxes: dict[str, tuple[int, Box]] = field(default_factory=dict)
    derived: set[str] = field(default_factory=set)
    how: str = ""
    urban: bool = False
    anchor: str = ""


@dataclass
class Pending:
    """A row no reading of the text layer satisfies; its figures are read from the page image."""

    year: int
    table: str
    key: str
    name: str
    page: int
    box: Box
    tokens: list[str]


@dataclass
class Count:
    """The places of one count, the printed totals of its counties, and the printed national totals."""

    places: list[Place] = field(default_factory=list)
    counties: dict[str, Place] = field(default_factory=dict)
    national: Place | None = None
    unresolved: list[Pending] = field(default_factory=list)


def place_of(key: str, name: str, county: str, page: int, spec: Spec, reading: Reading, line: list[Box]) -> Place:
    place = Place(key, name, county, page, how=reading.how)
    top, bottom = min(w.y0 for w in line), max(w.y1 for w in line)
    for f, box in zip(spec.fields, reading.boxes, strict=True):
        if box is None:
            box = Box("", line[0].x0, top, line[-1].x1, bottom)
        place.boxes[f] = (page, box)
    place.values = {f: reading.values[f] for f in spec.fields}
    place.derived = set(reading.derived)
    return place


def _label(words: list[Box]) -> str:
    return " ".join(w.text for w in words)


def row_box(line: list[Box]) -> Box:
    return Box("", min(w.x0 for w in line), min(w.y0 for w in line), max(w.x1 for w in line), max(w.y1 for w in line))


ALIAS = {
    "1972": {"T1": {"0803": "0805"}, "T1c": {"60": "05"}}
}  # a code the recognition misread in the table's text layer


def from_fix(year: int, table: str, key: str, name: str, page: int, box: Box | None, spec: Spec) -> Place | None:
    """The row as read from the page image, when the transcription holds it. A transcribed row carries its page and the
    box of its printed row."""
    read = FIXES.get(str(year), {}).get(table, {}).get(key)
    if read is None:
        return None
    if "page" in read:
        page = int(str(read["page"]))
        corners = read["box"]
        assert isinstance(corners, list)
        box = Box("", float(corners[0]), float(corners[1]), float(corners[2]), float(corners[3]))
    assert box is not None
    place = Place(key, str(read.get("name", name)), key[:2], page, how="read from the page image")
    for f in spec.fields:
        if f in read:
            place.values[f] = int(str(read[f]))
            place.boxes[f] = (page, box)
    derived = read.get("derived", [])
    assert isinstance(derived, list)
    place.derived = {str(f) for f in derived}
    return place


def manual(year: int, table: str, key: str, name: str, page: int, line: list[Box], spec: Spec) -> Place | None:
    return from_fix(year, table, key, name, page, row_box(line), spec)


def read_table(
    year: int,
    table: str,
    path: Path,
    pages: range,
    spec: Spec,
    count: Count,
    *,
    code_of: Callable[[list[Box], str | None], tuple[str | None, str | None, list[Box]]],
    merge: dict[str, Place],
    only: Callable[[int], bool] = lambda page: True,
    min_figures: int = 3,
    first_only: bool = False,
    stop: str | None = None,
) -> None:
    """Read every row of a table whose rows start with a code, the way `code_of` finds it, and file each in `merge`."""
    county: str | None = None
    seen: set[str] = set()
    for line in scan.lines(path, pages.start, pages.stop - 1):
        if not only(line.page) or not line.words:
            continue
        if stop is not None and re.search(stop, _label(line.words)):
            break
        key, county_, rest = code_of(line.words, county)
        if county_ is not None:
            county = county_
        if key is None:
            continue
        key = ALIAS.get(str(year), {}).get(table, {}).get(key, key)
        if first_only and key in seen:
            continue
        seen.add(key)
        name, digits = split(rest)
        if len(digits) < min_figures and FIXES.get(str(year), {}).get(table, {}).get(key) is None:
            continue
        fixed = manual(year, table, key, name, line.page, line.words, spec)
        if fixed is not None:
            merge[key] = fixed
            continue
        reading = solve(digits, spec) if len(digits) >= len(spec.fields) - 1 else None
        if reading is not None:
            merge[key] = place_of(key, name, key[:2], line.page, spec, reading, line.words)
        else:
            count.unresolved.append(
                Pending(year, table, key, name, line.page, row_box(line.words), [d.text for d in digits])
            )
    for key in FIXES.get(str(year), {}).get(table, {}):
        if key not in merge:
            fixed = from_fix(year, table, key, "", 0, None, spec)
            if fixed is not None:
                merge[key] = fixed


def code_4(words: list[Box], county: str | None) -> tuple[str | None, str | None, list[Box]]:
    """A row of the 1994 tables: a four-digit municipality code, a two-digit county code, or the national row."""
    first = words[0].text
    letters = re.search(r"[A-Za-zÆØÅæøå]", " ".join(w.text for w in words[1:])) is not None
    if re.fullmatch(r"\d{4}", first) and letters:
        return first, None, words[1:]
    if re.fullmatch(r"\d{2}", first) and letters:
        return first, None, words[1:]
    if first == "country":
        return "00", None, words
    return None, None, words


T1_1994 = Spec(
    ("reg", "reg_m", "reg_f", "cast", "cast_m", "cast_f", "poll", "poll_m", "poll_f"),
    (("reg", ("reg_m", "reg_f")), ("cast", ("cast_m", "cast_f")), ("poll", ("poll_m", "poll_f"))),
    lambda v: v["poll"] <= v["cast"] <= v["reg"],
)
T2_1994 = Spec(("valid", "yes", "no"), (("valid", ("yes", "no")),), tail=1)


def read_1994() -> Count:
    path = raw.cached(SOURCE, PDF[1994]).path
    count = Count()
    first: dict[str, Place] = {}
    second: dict[str, Place] = {}
    read_table(1994, "T1", path, range(21, 39), T1_1994, count, code_of=code_4, merge=first)
    read_table(1994, "T2", path, range(39, 48), T2_1994, count, code_of=code_4, merge=second)
    return finish(count, first, second, 4)


def finish(count: Count, first: dict[str, Place], second: dict[str, Place], width: int) -> Count:
    for key in sorted({*first, *second}):
        a, b = first.get(key), second.get(key)
        if a is None or b is None:
            continue
        place = Place(key, a.name or b.name, key[:2], a.page, {**a.values, **b.values}, {**a.boxes, **b.boxes})
        place.derived = a.derived | b.derived
        place.how = "; ".join(x for x in (a.how, b.how) if x)
        if key == "00":
            count.national = place
        elif len(key) == 2:
            count.counties[key] = place
        else:
            count.places.append(place)
    oslo = count.counties.get("03")
    if oslo is not None and not any(p.key == "0301" for p in count.places):
        # Oslo is one municipality and one county, and the tables list it once, as a county
        count.places.append(
            Place("0301", "Oslo", "03", oslo.page, dict(oslo.values), dict(oslo.boxes), set(oslo.derived), oslo.how)
        )
    return count


def fetch() -> None:
    for url in PDF.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, TABLE_25_8, suffix=".html")


def code_72(words: list[Box], county: str | None) -> tuple[str | None, str | None, list[Box]]:
    """A row of the 1972 tables: a municipality's number within the county the last heading named, or Oslo's four-digit
    number, or a county's own row, or the national row."""
    first = words[0].text
    rest = words[1:]
    text = " ".join(w.text for w in rest)
    letters = re.search(r"[A-Za-zÆØÅæøåÀ-ÿ]", text) is not None
    if first == "THE" and "COUNTRY" in text:
        return "00", None, words
    if not (re.fullmatch(r"\d{2}", first) or first == "0301") or not letters:
        return None, None, words
    if first == "0301":
        return "0301", "03", rest
    name = split(rest)[0]
    if not any(w.isdigit() for w in text.split()) and name.upper() == name:
        return None, first, words  # a county heading: "01 ØSTFOLD"
    return (county or "") + first, None, rest


T1_1972 = Spec(
    ("days", "reg", "reg_m", "reg_f", "cast", "cast_m", "cast_f", "rejected"),
    (("reg", ("reg_m", "reg_f")), ("cast", ("cast_m", "cast_f"))),
    lambda v: v["days"] in (1, 2, 3) and v["cast"] <= v["reg"],
)
T2_1972 = Spec(("valid", "yes", "no"), (("valid", ("yes", "no")),), tail=3)


def code_county(words: list[Box], county: str | None) -> tuple[str | None, str | None, list[Box]]:
    first = words[0].text
    text = " ".join(w.text for w in words[1:])
    if first == "THE" and "COUNTRY" in text:
        return "00", None, words
    if re.fullmatch(r"\d{2}", first) and re.search(r"[A-Za-zÆØÅæøåÀ-ÿ]", text) and len(first) == 2:
        return first, None, words[1:]
    return None, None, words


T1_COUNTY_1972 = Spec(
    ("reg", "reg_m", "reg_f", "cast", "cast_m", "cast_f"),
    (("reg", ("reg_m", "reg_f")), ("cast", ("cast_m", "cast_f"))),
    lambda v: v["cast"] <= v["reg"],
    tail=3,
)


def read_1972() -> Count:
    path = raw.cached(SOURCE, PDF[1972]).path
    count = Count()
    first: dict[str, Place] = {}
    second: dict[str, Place] = {}
    read_table(
        1972,
        "T1c",
        path,
        range(14, 16),
        T1_COUNTY_1972,
        count,
        code_of=code_county,
        merge=first,
        min_figures=5,
        first_only=True,
        stop=r"BYKOMMUNER\s+I\b",
    )
    read_table(1972, "T2c", path, range(30, 31), T2_1972, count, code_of=code_county, merge=second, first_only=True)
    read_table(1972, "T1", path, range(16, 29), T1_1972, count, code_of=code_72, merge=first, only=lambda p: p % 2 == 0)
    read_table(1972, "T2", path, range(31, 38), T2_1972, count, code_of=code_72, merge=second)
    return finish(count, first, second, 4)


COUNTIES = (
    ("ostfold", "Østfold"),
    ("akershus", "Akershus"),
    ("hedmark", "Hedmark"),
    ("opland", "Opland"),
    ("buskerud", "Buskerud"),
    ("vestfold", "Vestfold"),
    ("telemark", "Telemark"),
    ("aust-agder", "Aust-Agder"),
    ("vest-agder", "Vest-Agder"),
    ("rogaland", "Rogaland"),
    ("hordaland", "Hordaland"),
    ("sogn-og-fjordane", "Sogn og Fjordane"),
    ("more", "Møre"),
    ("sor-trondelag", "Sør-Trøndelag"),
    ("nord-trondelag", "Nord-Trøndelag"),
    ("nordland", "Nordland"),
    ("troms", "Troms"),
    ("finnmark", "Finnmark"),
)


def slug(text: str) -> str:
    text = text.lower().translate(str.maketrans({"ø": "o", "æ": "ae", "å": "a", "ö": "o", "ü": "u", "é": "e"}))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def county_heading(text: str) -> str | None:
    """The county a heading such as "Sør-Trøndelag fylke (forts.)" or "Vest-Agder." names, or None for any other line."""
    if len(re.findall(r"\d", text)) > 2:
        return None
    head = slug(re.split(r"(?i)\bfylke|\bf3r|\(forts|\.\s*$", text)[0])
    if not head or len(head) < 4:
        return None
    head = {"finmark": "finnmark", "more-og-romsdal": "more"}.get(head, head)
    for k, _ in COUNTIES:
        if head == k or (len(head) >= 5 and k.endswith(head)) or (len(head) >= 5 and k.startswith(head)):
            return k
    return None


T_1919 = Spec(
    ("em", "ef", "et", "cm", "cf", "ct", "ja", "nei"),
    (("et", ("em", "ef")), ("ct", ("cm", "cf"))),
    lambda v: v["ja"] + v["nei"] <= v["ct"] <= v["et"] and v["ct"] - v["ja"] - v["nei"] <= max(5, v["ct"] // 10),
    tail=4,
)


def clipped_total_agrees(place: Place, reading: Reading, digits: list[Box]) -> bool:
    """The last column of the 1919 table is cut off by the scan, but its leading digits are often read: the votes for
    and against must add up to them."""
    last = reading.boxes[-1]
    if last is None:
        return True
    tail = "".join(d.text for d in digits if d.x0 > last.x1 - 0.5)
    total = str(place.values["ja"] + place.values["nei"])
    if len(tail) < 2:
        return True
    width = 1 if len(tail) == 2 else min(len(tail), 3)
    return total[:width] == tail[:width]


def read_1919() -> Count:
    path = raw.cached(SOURCE, PDF[1919]).path
    count = Count()
    county: str | None = None
    urban = False
    seen: set[str] = set()
    for line in scan.lines(path, 104, 119, tolerance=3.0):
        words = line.words
        text = _label(words)
        name, digits = split(words)
        if len(digits) < 5:
            if re.search(r"B\.?\s*Byer", text):
                urban, county = True, "byer"
            elif (found := county_heading(text)) is not None:
                county = found
            continue
        if county is None:
            continue
        low = slug(name)
        if not name or len(low) < 2:
            name, low = "", ""
        if low.startswith(("fylket", "byene", "tilsammen")):
            reading = solve(digits, T_1919)
            if reading is not None and low.startswith("fylket"):
                total = place_of(f"c:{county}", name, county, line.page, T_1919, reading, words)
                count.counties[county] = total
            continue
        anchor = f"{line.page}@{min(w.y0 for w in words):.1f}"
        place = from_fix(1919, "T15", anchor, name, line.page, row_box(words), T_1919)
        if place is None:
            reading = solve(digits, T_1919)
            if reading is None:
                count.unresolved.append(
                    Pending(1919, "T15", anchor, name, line.page, row_box(words), [d.text for d in digits])
                )
                continue
            place = place_of(anchor, name, county, line.page, T_1919, reading, words)
            if not clipped_total_agrees(place, reading, digits):
                count.unresolved.append(
                    Pending(1919, "T15", anchor, name, line.page, row_box(words), [d.text for d in digits])
                )
                continue
        key = f"{county}:{slug(place.name)}" if place.name else f"{county}:{anchor}"
        if key in seen:
            key = f"{key}-{anchor}"
        place.key = key
        place.anchor = anchor
        place.urban = urban
        place.county = county
        seen.add(key)
        count.places.append(place)
    return count


T_1926 = Spec(("adg", "cast", "for", "mot"), (), lambda v: v["for"] + v["mot"] <= v["cast"] <= v["adg"])
MID_1926 = 206.0


def read_1926() -> Count:
    path = raw.cached(SOURCE, PDF[1926]).path
    count = Count()
    county: str | None = None
    urban = False
    seen: set[str] = set()
    for number in range(14, 23):
        page = digitized_page(path, number)
        for half in (0, 1):
            words = [w for w in page if (w.x0 < MID_1926) == (half == 0)]
            for line in scan_rows(words):
                text = _label(line)
                name, digits = split(line, marks=True)
                if len(digits) < 3:
                    if re.search(r"B\.?\s*Byer", text):
                        urban, county = True, "byer"
                    elif (found := county_heading(text)) is not None:
                        county = found
                    continue
                if county is None:
                    continue
                low = slug(name)
                if low.startswith("tilsammen"):
                    reading = solve(digits, T_1926)
                    if reading is not None:
                        count.counties[county] = place_of(f"c:{county}", name, county, number, T_1926, reading, line)
                    continue
                anchor = f"{number}{'LR'[half]}@{min(w.y0 for w in line):.1f}"
                place = from_fix(1926, "T2", anchor, name, number, row_box(line), T_1926)
                if place is None:
                    reading = solve(digits, T_1926) if len(digits) >= 4 and name else None
                    if reading is None:
                        count.unresolved.append(
                            Pending(1926, "T2", anchor, name, number, row_box(line), [d.text for d in digits])
                        )
                        continue
                    place = place_of(anchor, name, county, number, T_1926, reading, line)
                key = f"{county}:{slug(place.name)}" if place.name else f"{county}:{anchor}"
                if key in seen:
                    key = f"{key}-{anchor}"
                place.key = key
                place.anchor = anchor
                place.urban = urban
                place.county = county
                seen.add(key)
                count.places.append(place)
    return count


def digitized_page(path: Path, number: int) -> list[Box]:
    return digitized.pages(Path(str(path)), number, number)[number].words


def scan_rows(words: list[Box]) -> list[list[Box]]:
    return digitized.rows(words, 3.0)


# ---------------------------------------------------------------------------------------------------------------------
# Statistics Norway's table 25.8, the county results of all six votes


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.tables[-1].append([])
        elif tag in ("td", "th") and self.tables:
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.cell is not None and self.tables and self.tables[-1]:
            self.tables[-1][-1].append("".join(self.cell).replace("\xa0", " ").strip())
            self.cell = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)


COUNTY_NAMES_25_8 = {
    "ostfold": "Østfold",
    "akershus": "Akershus",
    "oslo": "Oslo",
    "hedmark": "Hedmark",
    "oppland": "Oppland",
    "buskerud": "Buskerud",
    "vestfold": "Vestfold",
    "telemark": "Telemark",
    "aust-agder": "Aust-Agder",
    "vest-agder": "Vest-Agder",
    "rogaland": "Rogaland",
    "hordaland": "Hordaland",
    "bergen": "Bergen",
    "sogn-og-fjordane": "Sogn og Fjordane",
    "more-og-romsdal": "Møre og Romsdal",
    "sor-trondelag": "Sør-Trøndelag",
    "nord-trondelag": "Nord-Trøndelag",
    "nordland": "Nordland",
    "troms": "Troms",
    "finnmark": "Finnmark",
}


def county_of(label: str) -> str | None:
    text = slug(re.sub(r"[�­]", " ", label.split(" Romsa")[0].split(" Finnm")[0]))
    text = re.sub(r"-?\d+$", "", text)
    if text.startswith("hordaland"):
        return "hordaland"
    if text.startswith("sogn"):
        return "sogn-og-fjordane"
    if text.startswith("more"):
        return "more-og-romsdal"
    if text.startswith("troms"):
        return "troms"
    if text.startswith("finnmark"):
        return "finnmark"
    return text if text in COUNTY_NAMES_25_8 else None


def number_or_none(text: str) -> int | None:
    text = re.sub(r"^\d(?=\d{3}\s)", "", text) if re.match(r"^\d\d{3} \d{3}$", text) else text
    text = text.replace(" ", "")
    return int(text) if text.isdigit() else None


def percent(text: str) -> Decimal | None:
    text = text.replace(" ", "").replace(",", ".")
    return Decimal(text) if re.fullmatch(r"\d+(\.\d+)?", text) else None


def read_25_8() -> dict[str, dict[str, list[str]]]:
    """The rows of table 25.8 by vote: county key to the vote's cells. The national row is under 'country'."""
    parser = _Tables()
    parser.feed(raw.cached(SOURCE, TABLE_25_8).path.read_text(encoding="utf-8", errors="replace"))
    out: dict[str, dict[str, list[str]]] = {y: {} for y in ("1905a", "1905b", "1919", "1926", "1972", "1994")}
    table = parser.tables
    for rows, layout in (
        (table[2], {"1905a": (1, 4), "1905b": (4, 7), "1919": (7, 10)}),
        (table[4], {"1926": (1, 4), "1972": (4, 10)}),
        (table[6], {"1994": (1, 7)}),
    ):
        for row in rows:
            if len(row) < 5:
                continue
            name = "country" if row[0].startswith("Hele landet") else county_of(row[0])
            if name is None:
                continue
            for year, (a, b) in layout.items():
                out[year][name] = row[a:b]
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Packages

VOTES, REGISTERED, CAST, VALID, SHARE = 1, 2, 3, 4, 13
DIGITIZED, DERIVED = 8, 1
NORWAY = str(uuid5(package.NAMESPACE, "polity\x1fNO"))
REGISTRY = f"{SOURCE}/registry"
COUNTY_SCHEME = "no.referendum_county"
MUNICIPALITY_SCHEME = "no.referendum_municipality"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_NOS = key("source", "nos")
SOURCE_TABLE = key("source", "table-25-8")
SYSTEM = key("electoral_system", "referendum")


@dataclass(frozen=True)
class Vote:
    ident: str
    start: date
    end: date
    en: str
    nb: str
    question_en: str
    question_nb: str
    yes: tuple[str, str, str]
    no: tuple[str, str, str]
    winner: str
    context: str
    volume: int | None = None

    @property
    def year(self) -> int:
        return int(self.ident[:4])


VOTES_LIST = (
    Vote(
        "1905a",
        date(1905, 8, 13),
        date(1905, 8, 13),
        "1905 Norwegian referendum on the dissolution of the union with Sweden",
        "Folkeavstemningen om oppløsning av unionen med Sverige 1905",
        "1905 referendum: should the union with Sweden be dissolved?",
        "Oppløsning av unionen med Sverige",
        ("Yes", "Ja", "Ja"),
        ("No", "Nei", "Nei"),
        "yes",
        "The vote of 13 August 1905 on whether the union with Sweden should be dissolved.",
    ),
    Vote(
        "1905b",
        date(1905, 11, 12),
        date(1905, 11, 13),
        "1905 Norwegian referendum on Prince Carl of Denmark as King of Norway",
        "Folkeavstemningen om prins Carl av Danmark som norsk konge 1905",
        "1905 referendum: should Prince Carl of Denmark become King of Norway?",
        "Prins Carl av Danmark til norsk konge",
        ("Yes (Prince Carl as king)", "Ja", "Ja"),
        ("No", "Nei", "Nei"),
        "yes",
        "The vote of 12 and 13 November 1905 on whether Prince Carl of Denmark should become King of Norway.",
    ),
    Vote(
        "1919",
        date(1919, 10, 5),
        date(1919, 10, 6),
        "1919 Norwegian prohibition referendum",
        "Forbudsavstemningen 5 og 6 oktober 1919",
        "1919 referendum: should prohibition of the production, import and sale of spirits and strong wine be made law?",
        "Lovfestet varig forbud mot tilvirkning, innførsel og omsetning av brennevin og sterk (het) vin",
        ("Yes (for prohibition)", "Ja (for forbud)", "Ja"),
        ("No (against prohibition)", "Nei (mot forbud)", "Nei"),
        "yes",
        "The vote of 5 and 6 October 1919, under the law of 23 July 1918, on whether prohibition of spirits and "
        "strong wine should be lasting law. It followed the electoral roll of the 1918 Storting election.",
        1919,
    ),
    Vote(
        "1926",
        date(1926, 10, 18),
        date(1926, 10, 18),
        "1926 Norwegian prohibition referendum",
        "Forbudsavstemningen 18 oktober 1926",
        "1926 referendum: should the prohibition of spirits be kept?",
        "Opprettholdelse av brennevinsforbudet",
        ("For prohibition", "For brennevinsforbud", "For"),
        ("Against prohibition", "Mot brennevinsforbud", "Mot"),
        "no",
        "The vote of 18 October 1926, under the law of 21 May 1926, on whether the prohibition of spirits should be "
        "kept. It followed the electoral roll of the 1924 Storting election.",
        1926,
    ),
    Vote(
        "1972",
        date(1972, 9, 24),
        date(1972, 9, 25),
        "1972 Norwegian advisory referendum on membership of the European Communities",
        "Den rådgivende folkeavstemningen om Norges tilslutning til EF 1972",
        "1972 advisory referendum: should Norway join the European Communities?",
        "Norges tilslutning til EF",
        ("Yes", "Ja", "Ja"),
        ("No", "Nei", "Nei"),
        "no",
        "An advisory referendum on 24 and 25 September 1972 on Norway's accession to the European Communities.",
        1972,
    ),
    Vote(
        "1994",
        date(1994, 11, 27),
        date(1994, 11, 28),
        "1994 Norwegian advisory referendum on membership of the European Union",
        "Den rådgivende folkeavstemningen om Norges tilslutning til EU 1994",
        "1994 advisory referendum: should Norway join the European Union?",
        "Norges tilslutning til EU",
        ("Yes", "Ja", "Ja"),
        ("No", "Nei", "Nei"),
        "no",
        "An advisory referendum on 27 and 28 November 1994 on Norway's membership of the European Union.",
        1994,
    ),
)
BY_IDENT = {v.ident: v for v in VOTES_LIST}


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{v.start}" for v in VOTES_LIST)]


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Norway's national referendums of 1905-1994: Statistics Norway's publications they come from, the electoral "
        "system, and the schemes of their reporting units",
    )
    p.external.add(NORWAY)
    p.add(
        "source",
        id=SOURCE_NOS,
        kind="official_body",
        publisher="Statistisk sentralbyrå (Statistics Norway)",
        title="Norges offisielle statistikk: Folkeavstemningene 1919, 1926, 1972 og 1994",
        url=f"{BASE}/",
        license="Public official statistics; scans published by Statistics Norway",
        license_url="https://www.ssb.no/en/diverse/lisens",
        attribution="Statistisk sentralbyrå, Norges offisielle statistikk",
    )
    p.add(
        "source",
        id=SOURCE_TABLE,
        kind="official_body",
        publisher="Statistisk sentralbyrå (Statistics Norway)",
        title="Historisk statistikk, tabell 25.8 Folkeavstemninger",
        url=TABLE_25_8,
        license="Public official statistics published by Statistics Norway",
        license_url="https://www.ssb.no/en/diverse/lisens",
        attribution="Statistisk sentralbyrå, Historisk statistikk, tabell 25.8",
    )
    p.add("electoral_system", id=SYSTEM, polity_id=NORWAY, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="A national vote on one question under a statute that sets the terms of the vote: the answer with the "
        "most approved votes carries.",
        lang="en",
    )
    for code, noun, rank in ((COUNTY_SCHEME, "county", 1), (MUNICIPALITY_SCHEME, "municipality", 2)):
        p.add("scheme", code=code, polity_id=NORWAY, kind="administrative", rank=rank, noun=noun)
    return p


def parse() -> None:
    registry().write()
    for v in VOTES_LIST:
        election(v).write()


def emit() -> None:
    package.run("load", packages())


SHARE_ONLY = 4
FIELDS = {
    "1919": {"et": REGISTERED, "ct": CAST, "ja": "yes", "nei": "no"},
    "1926": {"adg": REGISTERED, "cast": CAST, "for": "yes", "mot": "no"},
    "1972": {"reg": REGISTERED, "cast": CAST, "valid": VALID, "yes": "yes", "no": "no"},
    "1994": {"reg": REGISTERED, "cast": CAST, "valid": VALID, "yes": "yes", "no": "no"},
}
# The national totals of the two 1905 votes: the footnotes of table 25.8 print them
NATIONAL = {
    "1905a": {(VALID, None): (368_392, 0), (VOTES, "no"): (184, 0), (VOTES, "yes"): (368_208, DERIVED)},
    "1905b": {(VOTES, "yes"): (259_563, 0), (VOTES, "no"): (69_264, 0)},
}
# The national figures of table 25.8 itself, for the county sets drawn from it
DECLARED_TABLE = {
    "1905a": {(REGISTERED, None): 435_376, (VOTES, "no"): 184},
    "1905b": {(REGISTERED, None): 439_718},
    "1919": {(VALID, None): 792_206},
    "1926": {(REGISTERED, None): 1_482_724},
    "1972": {},
    "1994": {},
}
DECLARED = {
    "1905a": {(REGISTERED, None): 435_376, (VALID, None): 368_392, (VOTES, "no"): 184},
    "1905b": {(REGISTERED, None): 439_718, (VOTES, "yes"): 259_563, (VOTES, "no"): 69_264},
    "1919": {
        (REGISTERED, None): 1_198_522,
        (CAST, None): 797_474,
        (VALID, None): 793_690,
        (VOTES, "yes"): 489_017,
        (VOTES, "no"): 304_673,
    },
    "1926": {
        (REGISTERED, None): 1_482_724,
        (CAST, None): 960_582,
        (VALID, None): 954_115,
        (VOTES, "yes"): 423_031,
        (VOTES, "no"): 531_084,
    },
    "1972": {
        (REGISTERED, None): 2_645_349,
        (CAST, None): 2_095_675,
        (VALID, None): 2_089_968,
        (VOTES, "yes"): 971_687,
        (VOTES, "no"): 1_118_281,
    },
    "1994": {
        (REGISTERED, None): 3_266_064,
        (CAST, None): 2_908_210,
        (VALID, None): 2_906_800,
        (VOTES, "yes"): 1_389_997,
        (VOTES, "no"): 1_516_803,
    },
}
COUNTY_BY_CODE = {
    "01": "Østfold",
    "02": "Akershus",
    "03": "Oslo",
    "04": "Hedmark",
    "05": "Oppland",
    "06": "Buskerud",
    "07": "Vestfold",
    "08": "Telemark",
    "09": "Aust-Agder",
    "10": "Vest-Agder",
    "11": "Rogaland",
    "12": "Hordaland",
    "14": "Sogn og Fjordane",
    "15": "Møre og Romsdal",
    "16": "Sør-Trøndelag",
    "17": "Nord-Trøndelag",
    "18": "Nordland",
    "19": "Troms",
    "20": "Finnmark",
}
READERS = {"1919": read_1919, "1926": read_1926, "1972": read_1972, "1994": read_1994}


class Builder:
    """The rows one vote's package shares: values, their locators, and the pages they cite."""

    def __init__(self, p: Package, vote: Vote) -> None:
        self.p, self.vote = p, vote
        self.options: dict[str, str] = {}
        self.values: list[dict[str, object]] = []
        self.locators: list[dict[str, object]] = []
        self.pages: dict[int, str] = {}
        self.sizes: dict[int, tuple[float, float]] = {}
        self.artifact: str | None = None
        self.sha = ""
        self.municipal = ""
        self.table_sets: set[str] = set()

    def page_id(self, number: int) -> str:
        if number not in self.pages:
            self.pages[number] = key("page", self.vote.volume, number)
            width, height = self.sizes[number]
            self.p.add(
                "source_page",
                id=self.pages[number],
                artifact_id=self.artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{self.sha}-{number}.png",
                width=round(width * 100 / 72),
                height=round(height * 100 / 72),
            )
        return self.pages[number]

    def put(
        self,
        result_set: str,
        unit: str,
        option: str | None,
        measure: int,
        value: int | Decimal,
        flags: int,
        where: tuple[int, Box] | None = None,
    ) -> None:
        option_id = self.options[option] if option else None
        self.values.append(
            {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option_id,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "flags": flags,
                "value": value,
            }
        )
        if where is not None:
            page, box = where
            width, height = self.sizes[page]
            self.locators.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option_id,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "page_id": self.page_id(page),
                    "source_id": None,
                    "x0": round(box.x0 / width, 5),
                    "y0": round(box.y0 / height, 5),
                    "x1": round(box.x1 / width, 5),
                    "y1": round(box.y1 / height, 5),
                }
            )

    def flush(self) -> None:
        import polars as pl

        for table, rows in (("result_value", self.values), ("value_locator", self.locators)):
            if rows:
                self.p.frame(table, pl.DataFrame(rows, infer_schema_length=None))


def result_set(b: Builder, name: str, default: bool, completeness: float, method: str, source: str) -> str:
    id = key("result_set", b.vote.ident, name)
    b.p.add(
        "result_set",
        id=id,
        election_id=key("election", b.vote.start),
        contest_id=key("contest", b.vote.start),
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=default,
        completeness=completeness,
    )
    b.p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    return id


def election(vote: Vote) -> Package:
    day = vote.start
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {vote.en[:4]} Norwegian referendum" + (" by municipality and county" if vote.volume else " by county"),
        depends=[REGISTRY],
    )
    p.external.add(NORWAY)
    b = Builder(p, vote)
    election_id, contest = key("election", day), key("contest", day)
    p.add(
        "election",
        id=election_id,
        polity_id=NORWAY,
        kind="referendum",
        date_from=vote.start,
        date_to=vote.end,
        date_precision="day",
        status="held",
    )
    p.name(election_id, vote.en, "en", "official")
    p.name(election_id, vote.nb, "nb", "native", False)
    base = f"no/{day}-referendum"
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="ballot_question",
        electoral_system_id=SYSTEM,
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, vote.question_en, "en", "official")
    p.name(contest, vote.question_nb, "nb", "native", False)
    p.add("slug", path=f"{base}/question", entity_id=contest, is_current=True)
    p.add(
        "note",
        id=key("note", contest, "context"),
        entity_id=contest,
        kind="context",
        body_md=vote.context,
        lang="en",
    )
    for position, (name, labels) in enumerate((("yes", vote.yes), ("no", vote.no)), 1):
        option = b.options[name] = key("option", day, name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=labels[1],
        )
        p.name(option, labels[0], "en", "official")
        p.name(option, labels[1], "nb", "native", False)
    table = raw.cached(SOURCE, TABLE_25_8)
    p.artifact(table, SOURCE_TABLE)
    p.add(
        "citation",
        id=key("citation", election_id, "table"),
        entity_id=election_id,
        source_id=SOURCE_TABLE,
        locator=json.dumps({"url": TABLE_25_8}),
    )
    sets: list[str] = []
    count: Count | None = None
    if vote.volume:
        pdf = raw.cached(SOURCE, PDF[vote.volume])
        b.artifact = p.artifact(pdf, SOURCE_NOS)
        p.add(
            "citation",
            id=key("citation", election_id, "volume"),
            entity_id=election_id,
            source_id=SOURCE_NOS,
            locator=json.dumps({"url": PDF[vote.volume]}),
        )
        b.sha = pdf.sha256
        count = READERS[vote.ident]()
        municipal(b, count)
        sets.append(b.municipal)
    county_set = counties(b, count)
    sets.append(county_set)
    if vote.ident in NATIONAL:
        sets.append(national(b))
    held = {(row["result_set_id"], row["option_id"], row["measure"]) for row in b.values}
    for rs in sets:
        printed = DECLARED_TABLE[vote.ident] if rs in b.table_sets else DECLARED[vote.ident]
        for (measure, option), value in printed.items():
            if (rs, b.options[option] if option else None, measure) not in held:
                continue
            p.add(
                "declared",
                result_set_id=rs,
                option_id=b.options[option] if option else None,
                measure=measure,
                value=value,
            )
        won, lost = (vote.winner, "no" if vote.winner == "yes" else "yes")
        p.add("outcome", result_set_id=rs, option_id=b.options[won], kind="won")
        p.add("outcome", result_set_id=rs, option_id=b.options[lost], kind="lost")
    b.flush()
    accept(p, vote)
    return p


ACCEPTED = {
    "1919": {
        "accounting.declared": "The county rows are Statistics Norway's table 25.8, whose own footnote says the national "
        "approved-votes figure it prints (792 206) is 500 lower than the sum of its county rows."
    },
    "1926": {
        "accounting.declared": "The district rows of the volume's detailed table add up to 20 more ballots cast in "
        "Møre than the 50 842 that the same volume prints as the county total, in its detailed table and in its "
        "summary table. Every figure of every Møre row agrees between the text layer and a second reading of the "
        "image, so the rows are stored as printed."
    },
    "1972": {
        "accounting.ballots_sum": "Table 2 of the volume prints approved votes for nine municipalities that exceed the "
        "ballots cast that its Table 1 prints for them, by up to four; both tables are stored as printed."
    },
    "1994": {
        "accounting.ballots_sum": "Table 2 of the volume prints approved votes for some municipalities and counties "
        "that exceed the ballots cast that its Table 1 prints for them, by up to 166. Table 2's national approved "
        "votes (2 906 800) also differ from Table 1's (2 904 649), which Table 1's own ballots cast and rejected "
        "votes do not reconcile. Both tables are stored as printed."
    },
}


def accept(p: Package, vote: Vote) -> None:
    for code, reason in ACCEPTED.get(vote.ident, {}).items():
        p.accept(code, reason)


def unit(b: Builder, scheme: str, code: str, name: str, kind: str = "standard") -> str:
    id = key("unit", b.vote.start, scheme, code)
    b.p.add(
        "reporting_unit",
        id=id,
        polity_id=NORWAY,
        scheme_code=scheme,
        code=f"{b.vote.start}:{code}",
        area_relation="unlocated",
        area_id=None,
        kind=kind,
    )
    b.p.name(id, name, "nb", "official")
    return id


TABLE_NOTE = {
    "1905a": (
        "From Statistics Norway's table 25.8 of the historical statistics, which prints for each county the persons "
        "entitled to vote, the turnout in percent, and the number of no votes. The turnout rates are not stored, "
        "because the archive's measures hold counts and shares of votes, not rates of turnout. Oslo and Bergen "
        "count as counties of their own."
    ),
    "1905b": (
        "From Statistics Norway's table 25.8 of the historical statistics, which prints for each county the persons "
        "entitled to vote, the turnout in percent, and the yes votes in percent of the approved votes. The turnout "
        "rates are not stored, because the archive's measures hold counts and shares of votes, not rates of "
        "turnout. The yes shares are stored as shares, because the table prints no counts. Oslo and Bergen count as "
        "counties of their own."
    ),
    "1919": (
        "From Statistics Norway's table 25.8 of the historical statistics, which prints for each county, with its "
        "towns, the approved votes and the yes votes in percent of them. The yes shares are stored as shares, "
        "because the table prints no counts. Oslo and Bergen count as counties of their own. The table's footnote "
        "says that the national figures it prints are 500 lower than the sum of its county rows."
    ),
    "1926": (
        "From Statistics Norway's table 25.8 of the historical statistics, which prints for each county, with its "
        "towns, the persons entitled to vote, the turnout in percent, and the votes for prohibition in percent of the "
        "approved votes. The turnout rates are not stored, because the archive's measures hold counts and shares of "
        "votes, not rates of turnout. The shares for prohibition are stored as shares, because the table prints no "
        "counts. Oslo and Bergen count as counties of their own."
    ),
}

METHOD = {
    "1919": (
        "Digitized from Table 15, II, of Alkoholstatistikk 1918-1919 (NOS VI 190), which prints for each rural district "
        "(herred) and town the effective electorate and the ballots cast, each for men and for women, and the votes "
        "for (ja) and against (nei). The scan cuts off the printed total of approved votes, so the votes for and "
        "against are stored without it. The volume returns Aurskog, Blaker and Holand, Buksnes and Hol, and Øksnes "
        "and Langenes each as one row, and so they are stored. The rows add up to the printed totals of the rural "
        "districts, of the towns, and of the country in every column, and to the printed county rows."
    ),
    "1926": (
        "Digitized from the table of the polling parishes (valgsogn) in NOS VIII 14, which prints for each rural "
        "district and town the persons with access to vote, the ballots cast, and the approved votes for and against "
        "prohibition. The volume prints Horten with 220 persons with access to vote and Åsgårdstrand with 6 150; the "
        "names look transposed, and the rows are stored as printed. The rows add up to the printed totals of every "
        "county, of the towns, and of the country, but for the ballots cast in Møre."
    ),
    "1972": (
        "Digitized from Tables 1 and 2 of NOS A 522: Table 1 prints for each municipality the persons entitled to vote "
        "and the ballots cast, and Table 2 the approved votes and the yes and no votes. Oslo is one municipality and "
        "one county, and the tables list it once, as a county. Table 1's rejected ballots are not stored, because "
        "they do not match the difference between Table 1's ballots cast and Table 2's approved votes. The "
        "municipalities add up to the national row and to the county rows in every column."
    ),
    "1994": (
        "Digitized from Tables 1 and 2 of NOS C 235: Table 1 prints for each municipality the persons entitled to vote "
        "and the ballots cast, and Table 2 the approved votes and the yes and no votes. Oslo is one municipality and "
        "one county, and the tables list it once, as a county. Table 1's own approved and rejected votes are not "
        "stored, because they do not match Table 2's, which carry the yes and no votes. The municipalities add up "
        "to the national row and to the county rows in every column."
    ),
}


def stats(count: Count) -> list[str]:
    """What the sums had to settle in a digitized table, for the note that tells a reader how far to trust it."""
    seen = [p.how for p in count.places if p.how]
    images = sum(1 for how in seen if how.startswith("read from"))
    repaired = sum(1 for how in seen if how.startswith("repaired"))
    supplied = sum(1 for how in seen if how.startswith("supplied"))
    out = [f"Every value keeps the page and the box of its row: {len(count.places)} rows."]
    if images:
        out.append(
            f"{images} rows, where the text layer of the scan could not be settled by the volume's sums, were read "
            "from the page image and keep the box of the whole row."
        )
    if repaired or supplied:
        out.append(
            f"{repaired + supplied} figures that the text layer garbled or lost were settled by the sums of their "
            "row and are flagged as derived."
        )
    return out


def municipal(b: Builder, count: Count) -> None:
    vote = b.vote
    assert vote.volume is not None
    b.sizes = scan.sizes(raw.cached(SOURCE, PDF[vote.volume]).path, 1, 200)
    method = " ".join([METHOD[vote.ident], *stats(count)])
    rs = result_set(b, "municipality", True, 1.0, method, SOURCE_NOS)
    b.municipal = rs
    mapping = FIELDS[vote.ident]
    for place in count.places:
        u = unit(b, MUNICIPALITY_SCHEME, place.key, place.name)
        for field_, target in mapping.items():
            if field_ not in place.values:
                continue
            flags = DIGITIZED | (DERIVED if field_ in place.derived else 0)
            where = place.boxes.get(field_)
            if isinstance(target, str):
                b.put(rs, u, target, VOTES, place.values[field_], flags, where)
            else:
                b.put(rs, u, None, target, place.values[field_], flags, where)


def counties(b: Builder, count: Count | None) -> str:
    vote = b.vote
    default = not vote.volume
    if vote.volume in (1972, 1994) and count is not None:
        rs = result_set(
            b,
            "county",
            False,
            1.0,
            "The county rows of the volume's Tables 1 and 2, which the municipalities add up to in every column.",
            SOURCE_NOS,
        )
        mapping = FIELDS[vote.ident]
        for code, place in sorted(count.counties.items()):
            u = unit(b, COUNTY_SCHEME, code, COUNTY_BY_CODE[code])
            for field_, target in mapping.items():
                if field_ not in place.values:
                    continue
                where = place.boxes.get(field_)
                flags = DIGITIZED | (DERIVED if field_ in place.derived else 0)
                if isinstance(target, str):
                    b.put(rs, u, target, VOTES, place.values[field_], flags, where)
                else:
                    b.put(rs, u, None, target, place.values[field_], flags, where)
        return rs
    method = TABLE_NOTE[vote.ident]
    rs = result_set(b, "county", default, 1.0, method, SOURCE_TABLE)
    b.table_sets.add(rs)
    for ckey, cells in sorted(read_25_8()[vote.ident].items()):
        if ckey == "country":
            continue
        u = unit(b, COUNTY_SCHEME, ckey, COUNTY_NAMES_25_8[ckey])
        if vote.ident == "1905a":
            for measure, option, cell in ((REGISTERED, None, cells[0]), (VOTES, "no", cells[2])):
                if (n := number_or_none(cell)) is not None:
                    b.put(rs, u, option, measure, n, 0)
        elif vote.ident == "1905b":
            if (n := number_or_none(cells[0])) is not None:
                b.put(rs, u, None, REGISTERED, n, 0)
            if (x := percent(cells[2])) is not None:
                b.put(rs, u, "yes", SHARE, x, SHARE_ONLY)
        elif vote.ident == "1919":
            if (n := number_or_none(cells[1])) is not None:
                b.put(rs, u, None, VALID, n, 0)
            if (x := percent(cells[2])) is not None:
                b.put(rs, u, "yes", SHARE, x, SHARE_ONLY)
        elif vote.ident == "1926":
            if (n := number_or_none(cells[0])) is not None:
                b.put(rs, u, None, REGISTERED, n, 0)
            if (x := percent(cells[2])) is not None:
                b.put(rs, u, "yes", SHARE, x, SHARE_ONLY)
    return rs


def national(b: Builder) -> str:
    method = "The national totals that the footnotes of Statistics Norway's table 25.8 print for the vote. " + (
        "The footnote gives the approved votes (368 392) and the no votes (184); the yes votes are the difference."
        if b.vote.ident == "1905a"
        else "The footnote gives the yes votes and the no votes."
    )
    rs = result_set(b, "national", False, 1.0, method, SOURCE_TABLE)
    u = unit(b, COUNTY_SCHEME, "norway", "Norway", "aggregate")
    for (measure, option), (value, flags) in NATIONAL[b.vote.ident].items():
        b.put(rs, u, option, measure, value, flags)
    return rs
