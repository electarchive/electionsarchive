"""Lithuanian presidential elections 1993-2014 by polling district from the Central Electoral Commission.

The Commission's archived results pages print each round of each election: the country, every municipality, and every
polling district (apylinkė), with the voters on the roll, the ballots cast, the invalid and valid ballots, and the votes
of each candidate cast at the polling station and by post. The pages of one round were saved together in one file per
round, each page headed by the address it came from. The 1993 results exist only as the Commission's table of the
municipalities and the countries where Lithuanians voted abroad.

Each election is one package, `lt.vrk_president/<date>`, built on `lt.vrk_president/geo` and
`lt.vrk_president/registry`. A second round is its own election, a week or two after the first, in the same package.
"""

import functools
import hashlib
import json
import re
import unicodedata
from collections.abc import Iterator
from datetime import date, datetime
from html.parser import HTMLParser
from typing import NamedTuple
from uuid import uuid5
from zoneinfo import ZoneInfo

import polars as pl
import pymupdf
import shapely
from shapely.geometry import shape

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "lt.vrk_president"
SITE = "https://www.vrk.lt"
BASE = f"{SITE}/statiniai/puslapiai"


class Page(NamedTuple):
    url: str
    tables: list[list[list[str]]]  # each table as rows of cell texts, empty cells kept
    text: str  # the page's text on one line
    lines: list[str]  # the page's text split where the page breaks a line or starts a block


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.stack: list[list[list[str]]] = []
        self.cell: list[str] | None = None
        self.words: list[str] = []
        self.lines: list[str] = [""]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.stack.append([])
        elif tag == "tr" and self.stack:
            self.stack[-1].append([])
        elif tag in ("td", "th") and self.stack:
            if not self.stack[-1]:
                self.stack[-1].append([])
            self.cell = []
            self.stack[-1][-1].append("")
        if tag in ("br", "p", "div", "tr", "table", "h1", "h2", "h3", "li"):
            self.lines.append("")
            self.words.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.cell is not None and self.stack:
            self.stack[-1][-1][-1] = clean("".join(self.cell))
            self.cell = None
        elif tag == "table" and self.stack:
            self.tables.append(self.stack.pop())

    def handle_data(self, data: str) -> None:
        self.words.append(data)
        self.lines[-1] += data
        if self.cell is not None:
            self.cell.append(data)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ").replace("﻿", " ")).strip()


def read(url: str, html: str) -> Page:
    reader = _Reader()
    reader.feed(html)
    reader.close()
    lines = [line for line in (clean(line) for line in reader.lines) if line]
    return Page(url, reader.tables, clean("".join(reader.words)), lines)


def number(text: str) -> int:
    """A count as the pages print it: digits, a dash or nothing for none, a space for thousands in some tables."""
    text = clean(text).replace(" ", "")
    if text in ("", "-", "–"):
        return 0
    if not re.fullmatch(r"\d+", text):
        raise ValueError(f"not a count: {text!r}")
    return int(text)


MARK = re.compile(r"<!-- URL: (\S+) status (\d+) -->")


def split(content: str) -> Iterator[tuple[str, str]]:
    """The pages of a saved bundle, as (address, html)."""
    parts = MARK.split(content)
    for url, status, html in zip(parts[1::3], parts[2::3], parts[3::3], strict=True):
        if status == "200":
            yield url, html


@functools.cache
def pages(key: str) -> list[Page]:
    content = raw.cached(SOURCE, key).path.read_text(encoding="utf-8", errors="replace")
    return [read(url, html) for url, html in split(content)]


NUM = r"[-–]?|\d+"


class Station(NamedTuple):
    """One polling district's protocol, or a count the Commission or a municipal commission made itself."""

    id: str  # the number the Commission's database gives the district, the same in both rounds
    round: int
    sav: str | None  # the municipality, as the page names it
    sav_number: int | None
    name: str | None
    number: int | None
    kind: str  # standard, municipal (a municipal commission's own count), commission (the Commission's), abroad
    registered: int | None
    cast: int | None
    valid: int | None
    invalid: int | None
    box_valid: int | None  # the ballots found in the box and by post, where the page splits them
    box_invalid: int | None
    post_valid: int | None
    post_invalid: int | None
    votes: dict[str, tuple[int, int, int]]  # by candidate as printed: at the station, by post, in all
    flaw: bool  # the page says the district protocol has errors


def find(text: str, expression: str) -> int | None:
    found = re.search(expression, text)
    return int(found.group(1)) if found else None


def candidates(page: Page) -> tuple[dict[str, tuple[int, int, int]], tuple[int, int, int] | None]:
    """The candidates' rows of a page's table, and its total row, as (at the station, by post, in all)."""
    votes: dict[str, tuple[int, int, int]] = {}
    total = None
    for table in page.tables:
        if not any(row and row[0] == "Kandidatas" for row in table[:2]):
            continue
        for row in table:
            if len(row) < 4 or not row[0]:
                continue
            if not all(re.fullmatch(NUM, cell.replace(" ", "")) for cell in row[1:4]):
                continue
            counts = (number(row[1]), number(row[2]), number(row[3]))
            if row[0].startswith("Iš viso"):
                total = counts
            else:
                votes[row[0]] = counts
    return votes, total


def old_header(page: Page) -> tuple[str | None, str | None, int | None, str]:
    """The municipality, district name and number, and kind of a 1997-2003 district page."""
    lines = page.lines
    start = next(i for i, line in enumerate(lines) if line.startswith("Puslapis atnaujintas"))
    rest = [line for line in lines[start + 1 :] if not line.startswith("- ")]
    found = re.fullmatch(r"(.+?) apylinkė \(Nr\. (\d+)\)", rest[1]) if len(rest) > 1 else None
    if found:
        return rest[0], found.group(1), int(found.group(2)), "standard"
    head = " ".join(rest[:2])
    if "Vyriausiosios rinkimų komisijos suskaičiuoti" in head:
        return None, None, None, "commission"
    if "rinkimų komisijos suskaičiuoti" in head:
        kind = "abroad" if "diplomatinėse atstovybėse" in head else "municipal"
        return head.split(" rinkimų komisijos")[0], None, None, kind
    raise ValueError(f"{page.url}: unrecognized header {rest[:3]}")


def old_station(page: Page, round_: int) -> Station:
    sav, name, nr, kind = old_header(page)
    found = re.search(r"/rezapll_(\d+)_\d\.htm$|rezapll\.htm-(\d+)\+\d\.htm$", page.url)
    if not found:
        raise ValueError(page.url)
    votes, _ = candidates(page)
    registered = find(page.text, r"Rinkėjų apylinkėje (\d+),")
    cast = find(page.text, r"rinkimuose dalyvavo (\d+)")
    invalid = find(page.text, r"Negaliojančių biuletenių (\d+)")
    valid = find(page.text, r"(?<![Nn]e)galiojančių biuletenių (\d+)")
    return Station(
        found.group(1) or found.group(2),
        round_,
        sav,
        None,
        name,
        nr,
        kind,
        registered,
        cast,
        valid,
        invalid,
        None,
        None,
        None,
        None,
        votes,
        "protokole yra klaidų" in page.text,
    )


def station_2004(page: Page, round_: int) -> Station:
    found = re.search(r"rez_apl_l_(\d+)_\d\.htm$", page.url)
    if not found:
        raise ValueError(page.url)
    votes, _ = candidates(page)
    kind, sav, sav_number, name, number = "standard", None, None, None, None
    title = page.lines[0]
    if "suskaičiuoti Vyriausiojoje rinkimų komisijoje" in page.text[:600]:
        kind = "commission"
    else:
        special = re.match(r"(.+?) \(Nr\.\s*(\d+)\) Balsai, suskaičiuoti savivaldybės rinkimų komisijoje", title)
        named = re.search(r"-\s*(.+?) \(Nr\.\s*(\d+)\) (.+?) \(Nr\.\s*(\d+)\)\s*$", title)
        if special:
            kind, sav, sav_number = "municipal", special.group(1), int(special.group(2))
        elif named:
            sav, sav_number, name, number = named.group(1), int(named.group(2)), named.group(3), int(named.group(4))
        else:
            raise ValueError(f"{page.url}: unrecognized title {title!r}")
    registered = find(page.text, r"turinčių piliečių - (\d+)")
    cast = find(page.text, r"dalyvavo - (\d+)")
    invalid = find(page.text, r"negaliojančių biuletenių - (\d+)") or find(
        page.text, r"Negaliojančių biuletenių - (\d+)"
    )
    valid = find(page.text, r"(?<![Nn]e)galiojančių biuletenių - (\d+)")
    return Station(
        found.group(1),
        round_,
        sav,
        sav_number,
        name,
        number,
        kind,
        registered,
        cast,
        valid,
        invalid,
        None,
        None,
        None,
        None,
        votes,
        False,
    )


def station_new(page: Page, round_: int) -> Station:
    found = re.search(r"rezultatai_apylinke(\d+)visodesc\dturas\.html$", page.url)
    if not found:
        raise ValueError(page.url)
    votes, _ = candidates(page)
    named = [line for line in page.lines if re.search(r"\(Nr\.\s*\d+\) (?:savivaldybė|apylinkė)$", line)]
    sav_line = next((line for line in named if line.endswith("savivaldybė")), None)
    ap_line = next((line for line in named if line.endswith("apylinkė")), None)
    sav = sav_number = name = number = None
    if sav_line:
        part = re.fullmatch(r"(.+?) \(Nr\.\s*(\d+)\) savivaldybė", sav_line)
        assert part
        sav, sav_number = part.group(1), int(part.group(2))
    if ap_line:
        part = re.fullmatch(r"(.+?) \(Nr\.\s*(\d+)\) apylinkė", ap_line)
        assert part
        name, number = part.group(1), int(part.group(2))
    text = page.text
    box_valid = find(text, r"Balsadėžėje rasta rinkimų biuletenių: galiojančių - (\d+)")
    box_invalid = find(text, r"Balsadėžėje rasta rinkimų biuletenių: galiojančių - \d+, negaliojančių - (\d+)")
    post_valid = find(text, r"Paštu gauta rinkimų biuletenių: galiojančių - (\d+)")
    post_invalid = find(text, r"Paštu gauta rinkimų biuletenių: galiojančių - \d+, negaliojančių - (\d+)")
    return Station(
        found.group(1),
        round_,
        sav,
        sav_number,
        name,
        number,
        "standard",
        find(text, r"Rinkėjų skaičius apylinkėje: (\d+)"),
        find(text, r"dalyvavusių rinkimuose rinkėjų skaičius: (\d+)"),
        None if box_valid is None else box_valid + (post_valid or 0),
        find(text, r"negaliojančių rinkimų apylinkėje biuletenių skaičius: (\d+)"),
        box_valid,
        box_invalid,
        post_valid,
        post_invalid,
        votes,
        False,
    )


class Declared(NamedTuple):
    """The figures a results page prints for the whole country, a municipality, or an embassy."""

    registered: int | None
    cast: int | None
    invalid: int | None
    valid: int | None
    votes: dict[str, tuple[int, int, int]]


def declared(page: Page) -> Declared:
    text = page.text
    return Declared(
        find(
            text,
            r"(?:sąrašuose yra|sąraše įrašyta rinkėjų [–-]|sąrašuose įrašyta rinkėjų [–-]|turinčių piliečių -)\s*(\d+)",
        ),
        find(text, r"dalyvavo\s*[–-]?\s*(\d+)"),
        find(text, r"[Nn]egaliojančių biuletenių\s*[–-]\s*(\d+)"),
        find(text, r"(?<![Nn]e)galiojančių biuletenių\s*[–-]\s*(\d+)"),
        candidates(page)[0],
    )


def embassy_old(page: Page, round_: int) -> Station:
    """A 2002-2003 embassy page: the votes for each candidate in all, and nothing else."""
    found = re.search(r"rezambl\.htm-\d+\+(\d+)\+\d\.htm$", page.url)
    if not found:
        raise ValueError(page.url)
    votes: dict[str, tuple[int, int, int]] = {}
    for table in page.tables:
        for row in table:
            if len(row) >= 3 and row[0] and re.fullmatch(NUM, row[1].replace(" ", "")) and row[1]:
                if row[0].startswith("Iš viso"):
                    continue
                votes[row[0]] = (-1, -1, number(row[1]))
    start = next(i for i, line in enumerate(page.lines) if line.startswith("Puslapis atnaujintas"))
    name = page.lines[start + 1]
    return Station(
        found.group(1),
        round_,
        None,
        None,
        name,
        None,
        "abroad",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        votes,
        False,
    )


def embassy_2004(page: Page) -> tuple[int, Station]:
    found = re.search(r"rez_amb_l_(\d+)\.htm$", page.url)
    if not found:
        raise ValueError(page.url)
    votes, _ = candidates(page)
    round_ = 2 if "Pakartotinio" in page.text[:900] else 1
    name = re.sub(r"^Balsavimo rezultatai\s*-\s*", "", page.lines[0])
    text = page.text
    return round_, Station(
        found.group(1),
        round_,
        None,
        None,
        name,
        None,
        "abroad",
        find(text, r"turinčių piliečių - (\d+)"),
        find(text, r"dalyvavo - (\d+)"),
        find(text, r"(?<![Nn]e)galiojančių biuletenių - (\d+)"),
        find(text, r"negaliojančių biuletenių - (\d+)"),
        None,
        None,
        None,
        None,
        votes,
        False,
    )


class Round(NamedTuple):
    day: date
    number: int  # 1 or 2: the round of the election, as the Commission counts it
    stations: list[Station]
    national: Declared
    keys: list[str]  # the saved files the figures come from
    as_of: str | None  # the page timestamp, as printed


def bundle(fragment: str) -> str:
    found = [key for key in raw.keys(SOURCE) if fragment in key]
    if len(found) != 1:
        raise LookupError(f"{fragment!r} matches {len(found)} saved files; run `ea fetch {SOURCE}`")
    return found[0]


def station_pages(key: str, pattern: str) -> list[Page]:
    return [page for page in pages(key) if re.search(pattern, page.url)]


def stamp(page: Page) -> str | None:
    found = re.search(r"(?:atnaujintas|Atnaujinta -)\s*(\d{4})[ -](\d\d)[ -](\d\d),? (\d\d:\d\d)", page.text)
    return f"{found.group(1)}-{found.group(2)}-{found.group(3)} {found.group(4)}" if found else None


def page_named(key: str, fragment: str) -> Page:
    found = [page for page in pages(key) if fragment in page.url]
    if len(found) != 1:
        raise LookupError(f"{fragment!r} matches {len(found)} pages of {key}")
    return found[0]


def round_1997(number: int, day: date) -> Round:
    key = bundle(f"1997-president-round{number}-polling-station")
    summary = bundle("pr97/rez/rezl_6_2.htm")
    stations = [old_station(page, number) for page in pages(key)]
    national = page_named(summary, f"rezl_6_{number}.htm")
    return Round(day, number, stations, declared(national), [key, summary], stamp(national))


def round_2002(number: int, day: date) -> Round:
    key = bundle("2002-president-round1-polling-station" if number == 1 else "2003-president-round2-polling-station")
    summary = bundle("2002/Prezidentas/rezultatai/")
    stations = [old_station(page, number) for page in pages(key)]
    stations += [
        embassy_old(page, number)
        for page in pages(summary)
        if re.search(rf"rezambl\.htm-\d+\+\d+\+{number}\.htm$", page.url)
    ]
    national = page_named(summary, f"rezl.htm-14+{number}.htm")
    return Round(day, number, stations, declared(national), [key, summary], stamp(national))


def round_2004(number: int, day: date) -> Round:
    key = bundle(f"2004-president-round{number}-polling-station")
    summary = bundle("2004/prezidentas/rezultatai/")
    stations = [station_2004(page, number) for page in pages(key)]
    for page in station_pages(summary, r"rez_amb_l_\d+\.htm$"):
        which, station = embassy_2004(page)
        if which == number:
            stations.append(station)
    national = page_named(summary, f"rez_l_19_{number}.htm")
    return Round(day, number, stations, declared(national), [key, summary], stamp(national))


def matching(name: str, names: list[str]) -> str:
    """The nominative name, as the national table prints it, of a candidate whose page names them in the genitive."""

    def prefix(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a.casefold(), b.casefold(), strict=False):
            if x != y:
                break
            n += 1
        return n

    first, _, last = name.rpartition(" ")
    scores = []
    for option in names:
        f, _, ln = option.rpartition(" ")
        scores.append((prefix(first, f) + prefix(last, ln), len(option) - prefix(last, ln), option))
    scores.sort(reverse=True)
    best = scores[0]
    if prefix(last, best[2].rpartition(" ")[2]) < len(best[2].rpartition(" ")[2]) - 3 or (
        len(scores) > 1 and scores[1][0] == best[0]
    ):
        raise ValueError(f"cannot match {name!r} to {names}")
    return best[2]


def abroad_2014(summary: str, number: int, names: list[str]) -> Station:
    """The vote at diplomatic missions, which the 2014 pages print as one row of the Vilnius city candidate pages."""
    votes: dict[str, tuple[int, int, int]] = {}
    figures: list[int] = []
    for page in station_pages(summary, rf"rezultatai_prezidento_kand\d+_sav\d+_{number}_1_\.html$"):
        if not any(line.startswith("Vilniaus miesto (Nr.") for line in page.lines):
            continue
        candidate = page.lines[page.lines.index("Kandidato į Lietuvos Respublikos Prezidentus") + 1]
        row = next(r for t in page.tables for r in t if r and r[0] == "Balsavimas LR diplomatinėse atstovybėse")
        votes[matching(candidate, names)] = (number_of(row[5]), number_of(row[6]), number_of(row[7]))
        figures = [number_of(row[1]), number_of(row[2]), number_of(row[4])]
    if len(votes) != len(names):
        raise ValueError(f"the Vilnius pages hold {len(votes)} of {len(names)} candidates")
    return Station(
        "abroad",
        number,
        None,
        None,
        "Balsavimas LR diplomatinėse atstovybėse",
        None,
        "abroad",
        figures[0],
        figures[1],
        figures[2],
        None,
        None,
        None,
        None,
        None,
        votes,
        False,
    )


def number_of(text: str) -> int:
    return number(text)


def round_2009(number: int, day: date) -> Round:
    key = bundle("2009-president-polling-station")
    summary = bundle("2009_prezidento_rinkimai/output_lt/rinkimu_diena/")
    stations = [station_new(page, number) for page in station_pages(key, r"rezultatai_apylinke\d+visodesc\dturas")]
    national = page_named(summary, "rezultatai_vienmand_apygardose1turas.html")
    return Round(day, number, stations, declared(national), [key, summary], stamp(national))


def round_2014(number: int, day: date) -> Round:
    key = bundle(f"2014-president-round{number}-" + ("polling-station" if number == 1 else "summary-municipality"))
    summary = bundle(
        "2014_prezidento_rinkimai/output_lt/rezultatai_vienmand_apygardose/" if number == 1 else "round2-summary"
    )
    stations = [station_new(page, number) for page in station_pages(key, r"rezultatai_apylinke\d+visodesc\dturas")]
    national = declared(page_named(summary, f"rezultatai_vienmand_apygardose{number}turas.html"))
    stations.append(abroad_2014(summary, number, list(national.votes)))
    return Round(
        day,
        number,
        stations,
        national,
        [key] + ([summary] if summary != key else []),
        stamp(page_named(summary, f"rezultatai_vienmand_apygardose{number}turas.html")),
    )


class Cell(NamedTuple):
    value: int
    page: int  # the PDF page, counted from 0
    box: tuple[float, float, float, float]  # in PDF points on that page


class Row1993(NamedTuple):
    index: int | None
    name: str
    electors: Cell
    cast: Cell
    brazauskas: Cell
    lozoraitis: Cell
    unreceived: int


# The table prints some names cut off or misspelled; these are the names they stand for.
NAMES_1993 = {
    "Ukemrgės r": "Ukmergės r.",
    "Čekijos ir Sl": "Čekijos ir Slovakijos",
    "Jungtinės Valstijo": "Jungtinės Valstijos",
}


def table_1993(key: str) -> tuple[list[Row1993], dict[str, Row1993]]:
    """The 1993 table's municipality and country rows, and its total rows (LR, abroad, all) by label."""
    document = pymupdf.open(raw.cached(SOURCE, key).path)
    rows: list[Row1993] = []
    totals: dict[str, Row1993] = {}
    for number in range(document.page_count):
        page = document[number]
        lines: dict[int, list[tuple[float, float, float, float, str]]] = {}
        for x0, y0, x1, y1, word, *_ in page.get_text("words"):
            if y0 > (150 if number == 0 else 135):
                lines.setdefault(round((y0 + y1) / 4), []).append((x0, y0, x1, y1, word))
        for _, words in sorted(lines.items()):
            cells: list[list[tuple[float, float, float, float, str]]] = []
            for word in sorted(words):
                if cells and word[0] - cells[-1][-1][2] < 5:
                    cells[-1].append(word)
                else:
                    cells.append([word])
            texts = [" ".join(w[4] for w in cell) for cell in cells]
            boxes = [
                (min(w[0] for w in c), min(w[1] for w in c), max(w[2] for w in c), max(w[3] for w in c)) for c in cells
            ]
            label = None
            if texts[0].startswith("Iš viso"):
                label = name = texts[0]
                idx = None
                data = list(zip(texts[1:], boxes[1:], strict=True))
            elif re.fullmatch(r"\d+", texts[0]) and texts[1] != "2" and len(cells) >= 12:
                idx = int(texts[0])
                name = texts[1]
                data = list(zip(texts[2:], boxes[2:], strict=True))
            else:
                continue
            ints = [(t, b) for t, b in data if "," not in t]
            if len(ints) != 5:
                raise ValueError(f"{name}: {len(ints)} counts in {[t for t, _ in data]}")

            def cell(position: int) -> Cell:
                t, b = ints[position]
                return Cell(int(t.replace(" ", "")), number, b)

            row = Row1993(idx, NAMES_1993.get(name, name), cell(0), cell(1), cell(2), cell(3), int(ints[4][0]))
            if label:
                totals[label] = row
            else:
                rows.append(row)
    return rows, totals


# ---------------------------------------------------------------------------------------------------------------------
# The packages

LITHUANIA = str(uuid5(package.NAMESPACE, "polity\x1fLT"))
TERRITORY = "a7f8801f-6490-5c0f-9839-91e16d1df8f0"
UNITS = "lt.apylinke"  # the polling district scheme the Commission's other election packages already define
MUNICIPALITIES = "lt.sav_pre2016"
VILNIUS = ZoneInfo("Europe/Vilnius")
GEO = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/LTU/ADM2/geoBoundaries-LTU-ADM2.geojson"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
INBOX = DATA / "inbox" / "manual" / SOURCE
PDF_1993 = f"{SITE}/fs/prd/p/2026-02/1993prezidento.pdf"
PDF_2009 = f"{BASE}/2009_prezidento_rinkimai/output_lt/rezultatai/vrk_protokolas.pdf"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


VRK_SOURCE = key("source", "results")
GEO_SOURCE = key("source", "geoboundaries")


class Election(NamedTuple):
    first: date
    second: date | None
    load: str  # the function that reads a round


ELECTIONS = (
    Election(date(1993, 2, 14), None, "1993"),
    Election(date(1997, 12, 21), date(1998, 1, 4), "1997"),
    Election(date(2002, 12, 22), date(2003, 1, 5), "2002"),
    Election(date(2004, 6, 13), date(2004, 6, 27), "2004"),
    Election(date(2009, 5, 17), None, "2009"),
    Election(date(2014, 5, 11), date(2014, 5, 25), "2014"),
)

SYSTEM_RULES = {"formula": "two_round", "majority": 0.5, "advance": 2}
SYSTEM_NOTE = (
    "A candidate who wins more than half of the votes of the voters who took part is elected, provided at least half "
    "of the voters on the roll took part; otherwise the candidate with the most votes, at least a third of all voters "
    "on the roll, is elected. If no candidate is elected in the first round, the two leading candidates stand in a "
    "second round two weeks later, and the one with more votes is elected."
)
METHOD = (
    "Counted by the polling district commissions (apylinkių rinkimų komisijos) and published by the Central Electoral "
    "Commission per polling district. Registered voters are the electoral roll, ballots cast are the voters who took "
    "part, and valid and invalid ballots are the ballots found valid or invalid. Candidate votes are stored on the "
    "polling-station (1) and postal (3) channels as the source splits them; where it prints only the total, it is "
    "stored on channel 0."
)

CITIES = {
    "Alytus": "Alytaus miesto",
    "Birštonas": "Birštono miesto",
    "Druskininkai": "Druskininkų miesto",
    "Kaunas": "Kauno miesto",
    "Klaipėda": "Klaipėdos miesto",
    "Marijampolė": "Marijampolės miesto",
    "Neringa": "Neringos miesto",
    "Palanga": "Palangos miesto",
    "Panevėžys": "Panevėžio miesto",
    "Šiauliai": "Šiaulių miesto",
    "Vilnius": "Vilniaus miesto",
}
SHORT = {"Birštono", "Druskininkų", "Neringos", "Visagino", "Palangos"}
MISSPELLED = {"Ukemrgės r.": "Ukmergės rajono", "Vilkavišio r.": "Vilkaviškio rajono"}


def municipality(name: str) -> str:
    """One name for a municipality, whichever way a year's pages write it."""
    name = MISSPELLED.get(name, name)
    if name in CITIES:
        return CITIES[name]
    if name.endswith(" r."):
        return name.removesuffix(" r.") + " rajono"
    return name + " miesto" if name in SHORT else name


def area(name: str) -> str:
    return key("area", MUNICIPALITIES, name)


def person_name(name: str) -> str:
    return name.title() if name.upper() != name and any(w.isupper() and len(w) > 1 for w in name.split()) else name


def folded(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.lower()).split())


def inbox() -> list[tuple[str, dict[str, str]]]:
    path = INBOX / "index.json"
    if not path.exists():
        return []
    index = json.loads(path.read_text())
    return [(entry.get("source", url), entry) for url, entry in index.items()]


def fetch() -> None:
    """Take the saved results pages from the manual inbox, and the municipal outlines from geoBoundaries.

    The Commission's results server answers scripted requests with a challenge page, so the pages of each round were
    saved by hand into one file per round (every page headed by its address) and listed in `index.json`.
    """
    for source, entry in inbox():
        file = INBOX / entry["file"]
        content = file.read_bytes()
        raw.store(
            SOURCE,
            source,
            content,
            suffix=file.suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )
    raw.fetch(SOURCE, GEO, suffix=".geojson")


def moment(text: str | None) -> datetime | None:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=VILNIUS) if text else None


# the 2017 outlines geoBoundaries names in English or without the municipality's kind, by the Commission's name
SHAPES = {
    "Alytus District Municipality": "Alytaus rajono",
    "Birštonas Municipality": "Birštono miesto",
    "Druskininkų savivaldybė": "Druskininkų miesto",
    "Elektrėnai Municipality": "Elektrėnų",
    "Kalvarija Municipality": "Kalvarijos",
    "Kaunas District Municipality": "Kauno rajono",
    "Klaipėda City Municipality": "Klaipėdos miesto",
    "Klaipėda District Municipality": "Klaipėdos rajono",
    "Kretinga District Municipality": "Kretingos rajono",
    "Marijampolė Municipality": "Marijampolės",
    "Neringos savivaldybė": "Neringos miesto",
    "Prienai District Municipality": "Prienų rajono",
    "Vilkaviškis District Municipality": "Vilkaviškio rajono",
    "Vilnius city municipality": "Vilniaus miesto",
    "Šilutė District Municipality": "Šilutės rajono",
    "Visagino savivaldybė": "Visagino miesto",
    "Kazlų Rūdos savivaldybė": "Kazlų Rūdos",
    "Pagėgių savivaldybė": "Pagėgių",
    "Rietavo savivaldybė": "Rietavo",
}


@functools.cache
def outlines() -> dict[str, shapely.Geometry]:
    data = json.loads(raw.cached(SOURCE, GEO).path.read_bytes())
    found: dict[str, shapely.Geometry] = {}
    for feature in data["features"]:
        name = str(feature["properties"]["shapeName"])
        canonical = SHAPES.get(name, name.removesuffix(" savivaldybė"))
        geometry = shape(feature["geometry"])
        if not geometry.is_valid:
            geometry = shapely.make_valid(geometry)
        polygons = [g for g in shapely.get_parts(geometry) if g.geom_type == "Polygon" and not g.is_empty]
        found[canonical] = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    return found


@functools.cache
def rounds() -> dict[date, Round]:
    """Every round, by its date."""
    found: dict[date, Round] = {}
    for election in ELECTIONS:
        if election.load not in LOADERS:
            continue
        for number, day in ((1, election.first), (2, election.second)):
            if day is not None:
                found[day] = LOADERS[election.load](number, day)
    return found


@functools.cache
def municipalities() -> list[str]:
    names = {municipality(s.sav) for r in rounds().values() for s in r.stations if s.sav}
    names |= {municipality(r.name) for r in table_1993(PDF_1993)[0] if r.index is not None and r.index <= 55}
    return sorted(names)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Lithuanian municipalities as the Central Electoral Commission's presidential results pages name them, "
        "1993-2014, with the 2017 outlines of the municipalities that still exist",
    )
    p.external |= {LITHUANIA, TERRITORY}
    p.add("scheme", code=MUNICIPALITIES, polity_id=LITHUANIA, kind="administrative", rank=1, noun="municipality")
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap boundaries)",
        title="geoBoundaries gbOpen Lithuania ADM2, 2017",
        url="https://www.geoboundaries.org/",
        license="Open Database License 1.0 (OpenStreetMap contributors), through geoBoundaries (CC BY 4.0)",
        license_url="https://www.openstreetmap.org/copyright",
        attribution="OpenStreetMap contributors (Wambacher), through geoBoundaries (William & Mary geoLab)",
    )
    p.artifact(raw.cached(SOURCE, GEO), GEO_SOURCE)
    p.add(
        "boundary_set",
        id=key("boundary_set", MUNICIPALITIES, 2017),
        scheme_code=MUNICIPALITIES,
        vintage="2017",
        valid_from=None,
        valid_to=None,
        as_of=date(2017, 1, 1),
    )
    shapes = outlines()
    for name in municipalities():
        id = area(name)
        p.add("area", id=id, scheme_code=MUNICIPALITIES)
        p.add("area_code", area_id=id, code=name)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, f"{name} savivaldybė", "lt", "official")
        if name in shapes:
            whole = shapes[name]
            p.add(
                "area_geometry",
                area_id=id,
                valid_from=None,
                valid_to=None,
                geom=shapely.to_wkb(whole, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(whole), 4326), hex=True, include_srid=True
                ),
                quality="osm",
                source_id=GEO_SOURCE,
            )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The presidency of Lithuania and its two-round electoral system, as the presidential elections of 1993-2014 used",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {LITHUANIA, TERRITORY}
    p.add(
        "source",
        id=VRK_SOURCE,
        kind="official_body",
        publisher="Vyriausioji rinkimų komisija",
        title="Lietuvos Respublikos vyriausioji rinkimų komisija: Respublikos Prezidento rinkimų rezultatai",
        url=f"{SITE}/lt/rinkimai-ir-referendumai",
        license="Public record",
        attribution="Vyriausioji rinkimų komisija",
    )
    presidency = key("office", "president")
    p.add("office", id=presidency, polity_id=LITHUANIA, kind="head_of_state")
    p.name(presidency, "President of the Republic of Lithuania", "en", "official")
    p.name(presidency, "Lietuvos Respublikos Prezidentas", "lt", "native", False)
    system = key("electoral_system", "two_round")
    p.add(
        "electoral_system",
        id=system,
        polity_id=LITHUANIA,
        valid_from=date(1992, 11, 2),
        valid_to=date(2014, 12, 31),
        rules=SYSTEM_RULES,
        rules_version=1,
    )
    p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


LOADERS = {
    "1997": round_1997,
    "2002": round_2002,
    "2004": round_2004,
    "2009": round_2009,
    "2014": round_2014,
}
ENGLISH = {
    1993: "1993 Lithuanian presidential election",
    1997: "1997-98 Lithuanian presidential election",
    2002: "2002-03 Lithuanian presidential election",
    2004: "2004 Lithuanian presidential election",
    2009: "2009 Lithuanian presidential election",
    2014: "2014 Lithuanian presidential election",
}


REASONS = {
    "1997": (
        "The Commission's national figures include the counts made by municipal commissions and by the Commission "
        "itself, and the protocols its pages mark as having errors. Those pages print candidate votes, which add up "
        "to the national figures to the vote, but for some of them no ballot figures."
    ),
    "2002": (
        "The 49 embassy and consulate pages of each round print only the candidates' votes, without the roll, the "
        "ballots cast, or the invalid and valid ballots the national figures include. A few municipalities' totals "
        "also differ from the candidates' vote totals by a few votes (net 22 in round 1 and 12 in round 2, and in "
        "both directions for some candidates), which the pages do not explain."
    ),
    "2004": (
        "The national roll includes the voters of counts the pages print without a roll: the Commission's own count "
        "and the municipal commission counts."
    ),
    "2009": (
        "The Commission's pages print the vote at diplomatic missions abroad only inside the national and Vilnius city "
        "totals, not as a count of its own, so the polling-district pages leave it out."
    ),
    "2014": (
        "The vote at diplomatic missions is one row of the Vilnius city pages, with the voters on the roll, the "
        "ballots cast, and the valid ballots but not the invalid ones; the first round's national figures also "
        "include a few votes and ballots that no page prints as a count of its own."
    ),
}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.first}" for e in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for election in ELECTIONS:
        build(election).write()


def emit() -> None:
    package.run("load", packages())


class State:
    def __init__(self) -> None:
        self.people: set[str] = set()
        self.previous: str | None = None
        self.contests: dict[int, str] = {}
        self.gaps: list[str] = []
        self.flaws: list[str] = []
        self.over: list[str] = []
        self.cited: set[str] = set()


VALUES = {
    "result_set_id": pl.String,
    "unit_id": pl.String,
    "option_id": pl.String,
    "channel": pl.Int16,
    "measure": pl.Int16,
    "count_round": pl.Int16,
    "flags": pl.Int16,
    "value": pl.Int64,
}


def add_election(p: Package, state: State, election: Election, day: date, number: int) -> str:
    id = key("election", day)
    p.add(
        "election",
        id=id,
        polity_id=LITHUANIA,
        kind="runoff" if number == 2 else "general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    english = ENGLISH[election.first.year]
    p.name(id, english + (", second round" if number == 2 else ""), "en", "official")
    span = (
        f"{election.first.year}-{str(election.second.year)[2:]}"
        if election.second and election.second.year != election.first.year
        else str(election.first.year)
    )
    p.name(
        id,
        f"{span} m. Respublikos Prezidento rinkimai" + (", pakartotinis balsavimas" if number == 2 else ""),
        "lt",
        "native",
        False,
    )
    path = f"lt/{day}-presidential" + ("-runoff" if number == 2 else "")
    p.add("slug", path=path, entity_id=id, is_current=True)
    state.contests[number] = path
    return id


def add_contest(
    p: Package, state: State, election: Election, id: str, day: date, number: int, names: list[str]
) -> tuple[str, str, dict[str, str]]:
    path = state.contests[number]
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="office",
        office_id=key("office", "president"),
        district_area_id=TERRITORY,
        magnitude=1,
        round_number=number,
        previous_round_id=state.previous if number == 2 else None,
        electoral_system_id=key("electoral_system", "two_round"),
        default_result_kind="counted",
    )
    state.previous = contest
    p.name(
        contest,
        f"President of the Republic of Lithuania, {day.year}" + (", second round" if number == 2 else ""),
        "en",
        "official",
    )
    p.name(
        contest,
        f"Respublikos Prezidentas, {day.year}" + (", pakartotinis balsavimas" if number == 2 else ""),
        "lt",
        "native",
        False,
    )
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", contest)
    options: dict[str, str] = {}
    for name in names:
        label = person_name(name)
        option = options[folded(name)] = key("option", contest, folded(name))
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=label)
        person = key("person", election.first, folded(name))
        if person not in state.people:
            state.people.add(person)
            p.add("person", id=person)
            p.name(person, label, "lt", "official")
        p.add(
            "candidacy",
            id=key("candidacy", contest, folded(name)),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=label,
        )
    return contest, result_set, options


def add_result_set(p: Package, contest: str, election_id: str, as_of: datetime | None, caveat: str | None) -> str:
    result_set = key("result_set", contest)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=VRK_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
        as_of=as_of,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    if caveat:
        p.add(
            "note", id=key("note", "caveat", result_set), entity_id=result_set, kind="caveat", body_md=caveat, lang="en"
        )
    return result_set


def unit_code(day: date, s: Station) -> str:
    if s.kind == "standard":
        return f"{day}:{municipality(s.sav or '')}:{s.number}:{s.name}"
    if s.kind == "municipal":
        return f"{day}:{municipality(s.sav or '')}:municipal-commission-count"
    if s.kind == "commission":
        return f"{day}:central-commission-count"
    return f"{day}:abroad" + (f":{s.name}" if s.name and s.id != "abroad" else "")


def add_units(
    p: Package, state: State, day: date, result_set: str, options: dict[str, str], stations: list[Station]
) -> dict[str, str]:
    """The polling districts and counts of one round, with their ballots and votes."""
    codes: dict[str, int] = {}
    for s in stations:
        codes[unit_code(day, s)] = codes.get(unit_code(day, s), 0) + 1
    rows: list[dict[str, object]] = []
    ids: dict[str, str] = {}

    def value(unit: str, option: str | None, channel: int, measure: int, count: int | None) -> None:
        if count is not None:
            rows.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": channel,
                    "measure": measure,
                    "count_round": 0,
                    "flags": 0,
                    "value": count,
                }
            )

    for s in stations:
        code = unit_code(day, s)
        if codes[code] > 1:
            code += f":{s.id}"
        unit = ids[s.id] = key("unit", code)
        outside = s.kind == "abroad"
        place = None if outside else area(municipality(s.sav)) if s.sav else TERRITORY
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=LITHUANIA,
            scheme_code=UNITS,
            code=code,
            area_relation="unlocated" if outside else "within",
            area_id=place,
            kind="abroad_mission" if outside else "standard" if s.kind == "standard" else "aggregate",
        )
        for measure, count in ((REGISTERED, s.registered), (CAST, s.cast), (VALID, s.valid), (INVALID, s.invalid)):
            value(unit, None, 0, measure, count)
        for name, (box, post, total) in s.votes.items():
            option = options[folded(name)]
            if box < 0:
                value(unit, option, 0, VOTES, total)
            else:
                if box + post != total:
                    raise ValueError(f"{code}: {name} has {box} + {post} votes, not {total}")
                value(unit, option, 1, VOTES, box)
                value(unit, option, 3, VOTES, post)
        if s.flaw:
            state.flaws.append(code)
        if s.registered is not None and s.cast is not None and s.cast > s.registered:
            state.over.append(f"{code} ({s.cast:,} voters on a roll of {s.registered:,})")
    p.frame("result_value", pl.DataFrame(rows, schema=VALUES, orient="row"))
    return ids


def gaps(r: Round, options: dict[str, str]) -> list[str]:
    """How the figures the pages print for the whole country differ from what the districts add up to."""
    sums = {
        "registered": sum(s.registered or 0 for s in r.stations),
        "ballots cast": sum(s.cast or 0 for s in r.stations),
        "invalid ballots": sum(s.invalid or 0 for s in r.stations),
        "valid ballots": sum(s.valid or 0 for s in r.stations),
    }
    printed = {
        "registered": r.national.registered,
        "ballots cast": r.national.cast,
        "invalid ballots": r.national.invalid,
        "valid ballots": r.national.valid,
    }
    found = [
        f"{label} {(printed[label] or 0) - sums[label]:+,}"
        for label in sums
        if printed[label] is not None and printed[label] != sums[label]
    ]
    for name, (_, _, total) in r.national.votes.items():
        mine = sum(v[2] for s in r.stations for n, v in s.votes.items() if folded(n) == folded(name))
        if mine != total:
            found.append(f"{person_name(name)} {total - mine:+,}")
    return found


def add_declared(p: Package, result_set: str, options: dict[str, str], national: Declared) -> None:
    for measure, count in (
        (REGISTERED, national.registered),
        (CAST, national.cast),
        (INVALID, national.invalid),
        (VALID, national.valid),
    ):
        if count is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=count)
    for name, (_, _, total) in national.votes.items():
        p.add("declared", result_set_id=result_set, option_id=options[folded(name)], measure=VOTES, value=total)


def render_pages(p: Package, artifact: str, key_: str) -> tuple[list[str], list[tuple[float, float]]]:
    """Add a `source_page` row for each page of a PDF, rendered to a JPEG the page images are served from."""
    document = pymupdf.open(raw.cached(SOURCE, key_).path)
    ids: list[str] = []
    sizes: list[tuple[float, float]] = []
    for number in range(document.page_count):
        page = document[number]
        pixmap = page.get_pixmap(dpi=150)
        content = pixmap.tobytes("jpeg")
        digest = hashlib.sha256(content).hexdigest()
        image = DATA / "pages" / SOURCE / f"{digest}.jpg"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(content)
        id = key("page", number)
        ids.append(id)
        sizes.append((page.rect.width, page.rect.height))
        p.add(
            "source_page",
            id=id,
            artifact_id=artifact,
            page_number=number + 1,
            image_key=f"pages/{SOURCE}/{digest}.jpg",
            width=pixmap.width,
            height=pixmap.height,
        )
    return ids, sizes


def build_1993(p: Package, state: State, election: Election) -> Package:
    day = election.first
    rows, totals = table_1993(PDF_1993)
    artifact = p.artifact(raw.cached(SOURCE, PDF_1993), VRK_SOURCE)
    pages_, sizes = render_pages(p, artifact, PDF_1993)
    names = ["A.M. Brazauskas", "S. Lozoraitis"]
    id = add_election(p, state, election, day, 1)
    contest, result_set, options = add_contest(p, state, election, id, day, 1, names)
    note = (
        "The Commission's table prints, for each municipality and each country where Lithuanians voted, the voters on "
        "the roll, the voters who took part, and the votes of each candidate; it prints no valid or invalid ballots "
        "and no split between the polling station and post. Municipalities are stored as aggregate units and countries "
        "as units abroad. The figures were read from the PDF's text layer, and each carries its page and box. Names "
        "the PDF prints cut off or misspelled are completed (Ukmergės, Vilkaviškio, Čekijos ir Slovakijos, Jungtinės "
        "Valstijos)."
    )
    add_result_set(p, contest, id, None, note)
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    for row in rows:
        abroad = (row.index or 0) > 55
        name = municipality(row.name) if not abroad else row.name
        code = f"{day}:abroad:{name}" if abroad else f"{day}:{name}"
        unit = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=LITHUANIA,
            scheme_code=UNITS,
            code=code,
            area_relation="unlocated" if abroad else "equals",
            area_id=None if abroad else area(name),
            kind="abroad_mission" if abroad else "aggregate",
        )
        for measure, option, cell in (
            (REGISTERED, None, row.electors),
            (CAST, None, row.cast),
            (VOTES, options[folded(names[0])], row.brazauskas),
            (VOTES, options[folded(names[1])], row.lozoraitis),
        ):
            values.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "flags": DIGITIZED,
                    "value": cell.value,
                }
            )
            width, height = sizes[cell.page]
            x0, y0, x1, y1 = cell.box
            locators.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "page_id": pages_[cell.page],
                    "source_id": None,
                    "x0": round(x0 / width, 4),
                    "y0": round(y0 / height, 4),
                    "x1": round(x1 / width, 4),
                    "y1": round(y1 / height, 4),
                }
            )
    p.frame("result_value", pl.DataFrame(values, schema=VALUES, orient="row"))
    p.frame("value_locator", pl.DataFrame(locators, orient="row"))
    total = totals["Iš viso"]
    for measure, option, cell in (
        (REGISTERED, None, total.electors),
        (CAST, None, total.cast),
        (VOTES, options[folded(names[0])], total.brazauskas),
        (VOTES, options[folded(names[1])], total.lozoraitis),
    ):
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=cell.value)
    leader = names[0] if total.brazauskas.value > total.lozoraitis.value else names[1]
    p.add("outcome", result_set_id=result_set, option_id=options[folded(leader)], kind="elected")
    return p


def build(election: Election) -> Package:
    first = election.first
    p = Package(
        f"{SOURCE}/{first}",
        SOURCE,
        f"The {ENGLISH[first.year]}, "
        + ("by municipality" if first.year == 1993 else "by polling district")
        + ", from the Central Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {LITHUANIA, TERRITORY, UNITS}
    state = State()
    if election.load == "1993":
        return build_1993(p, state, election)
    played = [(1, first), *([(2, election.second)] if election.second else [])]
    final = max(number for number, _ in played)
    leaders: dict[int, str] = {}
    contests: dict[int, tuple[str, dict[str, str]]] = {}
    for number, day in played:
        r = rounds()[day]
        for key_ in r.keys:
            if key_ not in state.cited:
                state.cited.add(key_)
                p.artifact(raw.cached(SOURCE, key_), VRK_SOURCE)
        if election.load == "2009":
            p.artifact(raw.cached(SOURCE, PDF_2009), VRK_SOURCE)
        names = list(r.national.votes)
        known = {folded(n) for n in names}
        stray = {n for s in r.stations for n in s.votes if folded(n) not in known}
        if stray:
            raise ValueError(f"{day}: candidates {stray} are not in the national table")
        id = add_election(p, state, election, day, number)
        contest, result_set, options = add_contest(p, state, election, id, day, number, names)
        found = gaps(r, options)
        caveat = None
        if found:
            caveat = (
                "The figures the Commission's pages print for the whole country differ from what the polling-district "
                "pages add up to: " + ", ".join(found) + ". The pages give no polling district for the difference."
            )
            state.gaps.append(f"{day}: " + ", ".join(found))
        add_result_set(p, contest, id, moment(r.as_of), caveat)
        add_units(p, state, day, result_set, options, r.stations)
        add_declared(p, result_set, options, r.national)
        contests[number] = (result_set, options)
        leaders[number] = max(r.national.votes, key=lambda n: r.national.votes[n][2])
    result_set, options = contests[final]
    p.add("outcome", result_set_id=result_set, option_id=options[folded(leaders[final])], kind="elected")
    if final == 2:
        first_set, first_options = contests[1]
        assert election.second is not None
        for name in rounds()[election.second].national.votes:
            p.add("outcome", result_set_id=first_set, option_id=first_options[folded(name)], kind="advanced")
    if state.gaps:
        p.accept("accounting.declared", REASONS[election.load] + " The differences: " + "; ".join(state.gaps) + ".")
    if state.over:
        p.accept(
            "accounting.turnout",
            "The Commission prints more voters than the roll holds in " + "; ".join(state.over) + ". These are the "
            "protocol's own figures, kept as published.",
        )
    return p
