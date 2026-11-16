import collections
import html
import json
import re
import statistics
import subprocess
from collections.abc import Callable, Mapping
from datetime import date
from functools import cache
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "gy.gecom"
SITE = "https://www.gecom.org.gy/public"
PAGES = {
    "all": f"{SITE}/home/results",
    "2020": f"{SITE}/home/results/gre2020",
    "2025": f"{SITE}/home/results/gre2025",
}
# The years whose results are published as general and regional elections files.
YEARS = ("2011", "2015", "2020", "2025")


def listing(page: str) -> list[tuple[str, str]]:
    """The folder path and URL of every file a results page lists in its file tree."""
    html = raw.cached(SOURCE, PAGES[page]).path.read_text(errors="replace")
    found = re.search(r"var passedArray = (\[.*?\]);\s*\n", html, re.DOTALL)
    if not found:
        raise ValueError(f"{page} lists no files")
    files: list[tuple[str, str]] = []

    def walk(nodes: list[dict[str, object]], path: str) -> None:
        for node in nodes:
            children = node.get("nodes")
            if isinstance(children, list):
                walk(children, f"{path}/{node['text']}")
            elif isinstance(node.get("href"), str):
                files.append((f"{path}/{node['text']}".strip("/"), str(node["href"])))

    walk(json.loads(found.group(1)), "")
    return files


def wanted(path: str) -> bool:
    return path.split("/")[0][:4] in YEARS and "Local Government" not in path


def fetch() -> None:
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    seen: set[str] = set()
    for page in ("all", "2020", "2025"):
        for path, url in listing(page):
            if wanted(path) and url not in seen:
                seen.add(url)
                suffix = "." + url.rsplit(".", 1)[-1].lower()
                raw.fetch(SOURCE, url, suffix=suffix)
    print(f"{len(seen)} files")


HEADINGS = {
    "OF",
    "LIST",
    "FOR",
    "ALL",
    "THE",
    "NO",
    "BE",
    "CAN",
    "OR",
    "SO",
    "MORE",
    "THAN",
    "ONE",
    "WANT",
    "VOID",
    "MARKED",
    "UNMARKED",
    "GENERAL",
    "ELECTIONS",
    "REGIONAL",
    "VALID",
    "VOTES",
    "CAST",
    "OK",
    "CHK",
    "TOTAL",
    "FINAL",
    "RESULTS",
}
NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


class Word(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def y(self) -> float:
        return (self.y0 + self.y1) / 2


class Box(NamedTuple):
    """One polling station's row of a district tabulation form."""

    number: str
    code: str
    division: str
    station: str
    cells: list[float | None]
    words: list[Word]


class Sheet(NamedTuple):
    parties: list[str]
    boxes: list[Box]
    total: list[float]


def words(path: Path) -> list[list[Word]]:
    """Each page's words with their boxes, read from `pdftotext -bbox`."""
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    pages: list[list[Word]] = []
    for page in out.split("<page ")[1:]:
        pages.append(
            [
                Word(float(a), float(b), float(c), float(d), html.unescape(t))
                for a, b, c, d, t in re.findall(
                    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', page
                )
            ]
        )
    return pages


def join(found: list[Word]) -> str:
    lines: dict[int, list[Word]] = {}
    for word in sorted(found, key=lambda w: (w.y, w.x0)):
        for key, line in lines.items():
            if abs(key - word.y) < 3:
                line.append(word)
                break
        else:
            lines[round(word.y)] = [word]
    return " ".join(" ".join(w.text for w in sorted(line, key=lambda w: w.x0)) for _, line in sorted(lines.items()))


def value(text: str) -> float:
    return float(text.replace(",", "").rstrip("%"))


def fit(found: list[Word], centres: list[float]) -> list[float | None]:
    """A row's numbers laid on the page's columns, so that a blank cell becomes None instead of shifting its neighbors."""
    cells: list[float | None] = [None] * len(centres)
    at = 0
    for w in found:
        mid = (w.x0 + w.x1) / 2
        best = min(range(at, len(centres)), key=lambda i: abs(centres[i] - mid))
        cells[best] = value(w.text)
        at = best + 1
    return cells


def centres_of(rows: list[list[Word]], size: int) -> list[float]:
    full = [r for r in rows if len(r) == size]
    if not full:
        raise ValueError("no row fills every column")
    return [statistics.median((r[i].x0 + r[i].x1) / 2 for r in full) for i in range(size)]


def sheet(path: Path) -> Sheet:
    """The polling stations of a 2025 district tabulation form, with its party columns and its printed total row."""
    boxes: list[Box] = []
    parties: list[str] = []
    total: list[float] = []
    for page in words(path):
        marker = sorted((w for w in page if w.text == "BOX" and w.x0 > 100), key=lambda w: -w.x0)
        if not marker:
            continue
        head = marker[0]
        pound = [
            w for w in page if w.text == "#" and abs(w.y0 - head.y0) < 15 and w.x0 > head.x0 and w.x0 < head.x1 + 20
        ]
        left = head.x0 - 30
        right = (pound[0].x1 if pound else head.x1) + 20
        rows = sorted(
            (
                w
                for w in page
                if left <= w.x0 and w.x1 <= right and re.fullmatch(r"\d{4,6}", w.text) and w.y0 > head.y0 + 20
            ),
            key=lambda w: w.y,
        )
        if not rows:
            continue

        def line(centre: float, x: float) -> list[Word]:
            return sorted(
                (w for w in page if w.x0 >= x - 3 and NUMBER.fullmatch(w.text) and abs(w.y - centre) < 9),
                key=lambda w: w.x0,
            )

        electors = next(w for w in page if w.text == "ELECTORS")
        lines = [line(row.y, row.x0) for row in rows]
        size = collections.Counter(len(cells) for cells in lines).most_common(1)[0][0]
        first = next(cells for cells in lines if len(cells) == size)
        count = size - 13
        if not parties:
            body = [
                w
                for w in page
                if w.y1 < rows[0].y0 - 10
                and w.y0 > electors.y0 - 8
                and w.x0 > electors.x1
                and w.x1 < first[count + 2].x0
                and not NUMBER.fullmatch(w.text)
                and w.text not in HEADINGS
            ]
            centres = [(w.x0 + w.x1) / 2 for w in first[2 : 2 + count]]
            labels: list[list[Word]] = [[] for _ in centres]
            for w in body:
                nearest = min(range(count), key=lambda i: abs(centres[i] - (w.x0 + w.x1) / 2))
                labels[nearest].append(w)
            parties = [join(label) for label in labels]
            if not all(parties):
                raise ValueError(f"{path.name}: party headings {parties}")
        top = [w for w in page if w.x0 >= left and NUMBER.fullmatch(w.text) and head.y0 + 20 < w.y0 < rows[0].y0 - 5]
        if top and not total:
            total = [value(w.text) for w in sorted(top, key=lambda w: w.x0)]
        centres = centres_of(lines, count + 13)
        for row, cells in zip(rows, lines, strict=True):
            if len(cells) > count + 13:
                raise ValueError(f"{path.name}: box {row.text} has {len(cells)} cells, not {count + 13}")
            around = [w for w in page if w.x1 <= left + 15 and w.x0 > 30 and abs(w.y - row.y) < 10]
            owner = [w for w in around if min(rows, key=lambda r: abs(r.y - w.y)) is row]
            boxes.append(
                Box(
                    row.text,
                    join([w for w in owner if 104 <= w.x0 < 135]),
                    join([w for w in owner if w.x0 < 104]),
                    join([w for w in owner if w.x0 >= 135]),
                    fit(cells, centres)[1:],
                    cells,
                )
            )
    return Sheet(parties, boxes, total)


def sheet15(path: Path) -> Sheet:
    """The polling stations of a 2015 district form: electors, one column per party, then the ballot accounting."""
    boxes: list[Box] = []
    parties: list[str] = []
    for page in words(path):
        marker = [w for w in page if w.text == "BOX"]
        if not marker:
            continue
        head = marker[0]
        left, right = head.x0 - 10, head.x1 + 30
        rows = sorted(
            (
                w
                for w in page
                if left <= w.x0 and w.x1 <= right and re.fullmatch(r"\d{4}", w.text) and w.y0 > head.y1 + 12
            ),
            key=lambda w: w.y,
        )
        if not rows:
            continue

        def line(centre: float) -> list[Word]:
            found = sorted((w for w in page if w.x0 >= left and abs(w.y - centre) < 8), key=lambda w: w.x0)
            cells: list[Word] = []
            for w in found:
                if not NUMBER.fullmatch(w.text) and w.text != "OK":
                    continue
                if w.text == "OK":
                    break
                cells.append(w)
            return cells

        lines = [line(row.y) for row in rows]
        size = collections.Counter(min(len(cells), 99) for cells in lines).most_common(1)[0][0]
        first = next(cells for cells in lines if len(cells) == size)
        count = size - 12
        if not parties:
            electors = next(w for w in page if w.text.lower() == "electors" and w.y0 < rows[0].y0 and 300 < w.x0 < 420)
            end = min(w.x0 for w in page if w.text == "Valid" and w.x0 > first[count].x1 - 10 and w.y0 < rows[0].y0) - 2
            centres = [(w.x0 + w.x1) / 2 for w in first[2 : 2 + count]]
            labels: list[list[Word]] = [[] for _ in centres]
            for w in page:
                if (
                    electors.y0 - 12 < w.y0 < rows[0].y0 - 5
                    and electors.x1 + 4 < w.x0
                    and w.x1 < end
                    and re.fullmatch(r"[A-Z][A-Z/]{1,9}", w.text)
                    and w.text not in HEADINGS
                ):
                    nearest = min(range(count), key=lambda i: abs(centres[i] - (w.x0 + w.x1) / 2))
                    labels[nearest].append(w)
            parties = [join(label).replace(" ", "") for label in labels]
            if not all(parties):
                raise ValueError(f"{path.name}: party headings {parties}")
        lines = [cells[: count + 12] for cells in lines]
        centres = centres_of(lines, count + 12)
        number = next(w for w in page if w.text == "NUMBER" and w.y0 < rows[0].y0)
        division = max(
            (w for w in page if w.text == "DIVISION" and abs(w.y - number.y) < 3 and w.x1 < number.x0),
            key=lambda w: w.x0,
        )
        polling = next(w for w in page if w.text == "POLLING" and w.y0 < rows[0].y0)
        officer = next(w for w in page if w.text == "PRESIDING" and w.y0 < rows[0].y0)
        cuts = (0.0, division.x0 - 4, polling.x0 - 6, officer.x0 - 4)
        for row, cells in zip(rows, lines, strict=True):
            around = [w for w in page if w.x1 <= left + 5 and w.x0 > 60 and abs(w.y - row.y) < 12]
            owner = [w for w in around if min(rows, key=lambda r: abs(r.y - w.y)) is row]
            boxes.append(
                Box(
                    row.text,
                    join([w for w in owner if cuts[1] <= w.x0 < cuts[2]]),
                    join([w for w in owner if w.x0 < cuts[1]]),
                    join([w for w in owner if cuts[2] <= w.x0 < cuts[3]]),
                    fit(cells, centres)[1:],
                    cells,
                )
            )
    return Sheet(parties, boxes, [])


class Summary(NamedTuple):
    """A 2015 final summary: the party columns, each district's printed row, and the grand total row."""

    parties: list[str]
    districts: dict[int, list[float]]
    total: list[float]


def summary15(path: Path) -> Summary:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    parties: list[str] = []
    districts: dict[int, list[float]] = {}
    total: list[float] = []
    for line in text.splitlines():
        tokens = line.split()
        if not parties and tokens[:1] == ["Stations"] and "0" in tokens:
            names = tokens[1 : tokens.index("0")]
            parties = ["APNU/" + names[0] if names[0] == "AFC" else names[0], *names[1:]]
        found = re.match(r"\s*(\d+)\s+DISTRICT NO\.[^()]*\([^)]*\)\s+(.*)", line)
        if found:
            districts[int(found.group(1))] = [
                value(t) for t in found.group(2).split() if NUMBER.fullmatch(t.rstrip("%"))
            ]
        elif "Grand Total" in line:
            total = [value(t) for t in line.split("Grand Total")[1].split() if NUMBER.fullmatch(t.rstrip("%"))]
    return Summary(parties, districts, total)


class Tabulation(NamedTuple):
    """A 2020 tabulation summary: one row per district, then the printed total."""

    parties: list[str]
    rows: dict[int, dict[str, float | None]]
    total: dict[str, float | None]


TOTALS = ("valid", "rejected", "appeared", "spoilt", "tendered")


def tabulation20(path: Path) -> Tabulation:
    page = words(path)[0]
    body = [w for w in page if 85 < w.y0 < 135 and w.x0 > 90 and w.x1 < 650 and re.fullmatch(r"[A-Z][A-Z/]+", w.text)]
    groups: list[list[Word]] = []
    for w in sorted(body, key=lambda w: (w.x0 + w.x1) / 2):
        if groups and abs((w.x0 + w.x1) / 2 - statistics.mean((g.x0 + g.x1) / 2 for g in groups[-1])) < 25:
            groups[-1].append(w)
        else:
            groups.append([w])
    parties = ["+".join(w.text for w in sorted(g, key=lambda w: w.y0)) for g in groups]
    centres = [statistics.mean((w.x0 + w.x1) / 2 for w in g) for g in groups]
    ends = {
        name: statistics.mean((w.x0 + w.x1) / 2 for w in page if w.y0 < 125 and w.text == mark and w.x0 > 650)
        for name, mark in (
            ("valid", "Valid"),
            ("rejected", "Rejected"),
            ("appeared", "Appeared"),
            ("spoilt", "Spoilt"),
            ("tendered", "Tendered"),
        )
    }
    names = [*parties, *ends]
    marks = [*centres, *ends.values()]
    rows: dict[int, dict[str, float | None]] = {}
    total: dict[str, float | None] = {}
    limit = min(centres) - 25
    for label in (w for w in page if w.x1 < limit and w.y0 > 120 and (w.text.isdigit() or w.text == "Total")):
        cells: dict[str, float | None] = dict.fromkeys(names)
        for w in page:
            if w.x0 > label.x1 + 5 and NUMBER.fullmatch(w.text) and abs(w.y - label.y) < 8:
                cells[names[min(range(len(marks)), key=lambda i: abs(marks[i] - (w.x0 + w.x1) / 2))]] = value(w.text)
        if label.text == "Total":
            total = cells
        else:
            rows[int(label.text)] = cells
    return Tabulation(parties, rows, total)


class Declaration(NamedTuple):
    """One declaration of results in the 2011 gazette: a region's list votes, rejected ballots, and seats."""

    votes: dict[str, int]
    valid: int
    rejected: int
    seats: dict[str, int]


PARTIES11 = {
    "A Partnership for National Unity": "APNU",
    "A Partnership For National Unity": "APNU",
    "Alliance For Change": "AFC",
    "People's Progressive Party/Civic": "PPP/C",
    "The United Force": "TUF",
}


def party11(text: str) -> str | None:
    for name, short in PARTIES11.items():
        if name in text:
            return short
    return None


def gazette11(path: Path) -> dict[tuple[str, int], Declaration]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    out: dict[tuple[str, int], Declaration] = {}
    section: tuple[str, int] | None = None
    votes: dict[str, int] = {}
    seats: dict[str, int] = {}
    valid = rejected = 0
    part = ""

    def close() -> None:
        if section is not None and section not in out:
            out[section] = Declaration(votes, valid, rejected, seats)

    for line in text.splitlines():
        head = re.match(r"\s*(Geographical Constituency|Regional Democratic Council), Region No\. (\d+)", line)
        if head or re.match(r"\s*National Top-Up\s*$", line):
            close()
            section = ("GC" if head.group(1).startswith("Geo") else "RDC", int(head.group(2))) if head else ("TOP", 0)
            votes, seats, valid, rejected, part = {}, {}, 0, 0, "a"
            continue
        if section is None:
            continue
        if re.match(r"\s*\(c\)", line):
            part = "c"
        elif re.match(r"\s*\(d\)", line):
            part = "d"
        elif re.match(r"\s*(NOTICE|Dated this)", line):
            close()
            section = None
        found = re.match(r"\s*(.+?)[\s-]+([\d,]+|NIL)\s*$", line)
        total = re.match(r"\s*Total [Nn]umber of [Vv]alid [Vv]otes\s+([\d,]+)", line)
        refused = re.search(r"rejected ballot papers( is)?\s+([\d,]+)", line)
        if total:
            valid = int(total.group(1).replace(",", ""))
        elif refused and part == "a":
            rejected = int(refused.group(2).replace(",", ""))
        elif found and (short := party11(found.group(1))):
            number = 0 if found.group(2) == "NIL" else int(found.group(2).replace(",", ""))
            if part == "a":
                votes[short] = number
            elif part == "c":
                seats[short] = number
    close()
    return out


def recount25(path: Path) -> tuple[list[str], dict[int, dict[str, float | None]], dict[str, float | None]]:
    """The 2025 general election's final geographical constituency votes by party, from the recount comparison."""
    page = words(path)[0]
    heads = sorted(
        (w for w in page if re.fullmatch(r"[A-Z][A-Z/]+", w.text) and w.x1 < 340 and 130 < w.y0 < 160),
        key=lambda w: w.x0,
    )
    parties = [w.text for w in heads]
    marks = [(w.x0 + w.x1) / 2 for w in heads]
    rows: dict[int, dict[str, float | None]] = {}
    total: dict[str, float | None] = {}
    for label in (w for w in page if w.x1 < 50 and w.y0 > 160 and (w.text.isdigit() or w.text == "TOTAL")):
        cells: dict[str, float | None] = dict.fromkeys([*parties, "valid"])
        for w in page:
            if label.x1 + 5 < w.x0 and w.x1 < 345 and NUMBER.fullmatch(w.text) and abs(w.y - label.y) < 8:
                mid = (w.x0 + w.x1) / 2
                if mid > marks[-1] + 22:
                    cells["valid"] = value(w.text)
                else:
                    cells[parties[min(range(len(marks)), key=lambda i: abs(marks[i] - mid))]] = value(w.text)
        if label.text == "TOTAL":
            total = cells
        else:
            rows[int(label.text)] = cells
    return parties, rows, total


def seats_numbers(path: Path) -> list[list[str]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return [re.split(r"\s+", line.strip()) for line in text.splitlines() if line.strip()]


def constituency_seats25(path: Path) -> dict[int, dict[str, int]]:
    """The seats each party won in each geographical constituency: the last three columns are APNU, PPP/C, and WIN."""
    out: dict[int, dict[str, int]] = {}
    for tokens in seats_numbers(path):
        if tokens[0].isdigit() and 1 <= int(tokens[0]) <= 10 and len(tokens) > 12 and tokens[-1].isdigit():
            out[int(tokens[0])] = dict(zip(("APNU", "PPP/C", "WIN"), (int(t) for t in tokens[-3:]), strict=True))
    return out


def national25(path: Path) -> tuple[dict[str, tuple[int, int]], int]:
    """Each list's national votes and its top-up seats, and the total of top-up seats."""
    out: dict[str, tuple[int, int]] = {}
    for tokens in seats_numbers(path):
        if len(tokens) > 6 and tokens[0].isdigit() and re.fullmatch(r"[A-Z/]+", tokens[1]):
            out[tokens[1]] = (int(tokens[2].replace(",", "")), int(tokens[-1]))
    return out, sum(top for _, top in out.values())


class Council(NamedTuple):
    seats: int
    votes: dict[str, int]
    won: dict[str, int]


def councils25(path: Path) -> dict[int, Council]:
    """Each regional council's votes by list and the seats each list won, from the council allocation tables."""
    out: dict[int, Council] = {}
    votes: dict[str, int] = {}
    won: dict[str, int] = {}
    for tokens in seats_numbers(path):
        if (
            len(tokens) > 3
            and tokens[0].isdigit()
            and re.fullmatch(r"[A-Z][A-Z/]+", tokens[1])
            and tokens[2][0].isdigit()
        ):
            votes[tokens[1]] = int(tokens[2].replace(",", ""))
            won[tokens[1]] = int(tokens[-1])
        elif tokens[:3] == ["TOTAL", "SEATS", "ON"]:
            out[len(out) + 1] = Council(int(tokens[4]), votes, won)
            votes, won = {}, {}
    return out


# ---- the archive's packages -------------------------------------------------------------------------------------

COUNTRY = str(uuid5(package.NAMESPACE, "polity\x1fGY"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
PAGE = f"{SITE}/home/results"
DAYS = {
    "2011-11-28": date(2011, 11, 28),
    "2015-05-11": date(2015, 5, 11),
    "2020-03-02": date(2020, 3, 2),
    "2025-09-01": date(2025, 9, 1),
}
REGIONS = {
    1: "Barima-Waini",
    2: "Pomeroon-Supenaam",
    3: "Essequibo Islands-West Demerara",
    4: "Demerara-Mahaica",
    5: "Mahaica-Berbice",
    6: "East Berbice-Corentyne",
    7: "Cuyuni-Mazaruni",
    8: "Potaro-Siparuni",
    9: "Upper Takutu-Upper Essequibo",
    10: "Upper Demerara-Berbice",
}
FULL = {
    "APNU": "A Partnership for National Unity",
    "AFC": "Alliance For Change",
    "PPP/C": "People's Progressive Party/Civic",
    "TUF": "The United Force",
}
BLOCS = {"APNU", "APNU/AFC", "APNU+AFC"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


GECOM = key("source", "gecom")
ASSEMBLY = key("body", "national-assembly")
SYSTEM = key("electoral_system", "list", "hare")
METHOD = (
    "Counted at each polling station and published by the Guyana Elections Commission in its tabulation forms. "
    "Registered voters are the electors on the list for the station. Ballots cast are the valid votes plus the "
    "rejected ballot papers. Parties that did not stand in a region are not options of its contests."
)


def council(region: int) -> str:
    return key("body", "council", region)


class Station(NamedTuple):
    number: str
    name: str
    registered: float | None
    votes: Mapping[str, float | None]
    valid: float | None
    invalid: float | None
    cast: float | None
    aggregate: bool = False


class Declared(NamedTuple):
    """What the Commission prints for a whole contest."""

    votes: dict[str, int]
    valid: int | None = None
    invalid: int | None = None
    registered: int | None = None
    cast: int | None = None


class Contest(NamedTuple):
    kind: str  # constituency, council, or top-up
    region: int
    stations: list[Station]
    parties: list[str]
    declared: Declared | None
    seats: dict[str, int] | None
    magnitude: int | None
    unit: str  # station, region, or national


def urls() -> dict[str, str]:
    """The URL of every file the results pages list, by its file name."""
    found: dict[str, str] = {}
    for page in PAGES:
        for path, url in listing(page):
            if wanted(path):
                name = unquote(url.rsplit("/", 1)[1])
                if found.setdefault(name, url) != url:
                    raise ValueError(f"two files named {name}")
    return found


def path(name: str) -> Path:
    return raw.cached(SOURCE, urls()[name]).path


def station(box: Box, parties: list[str]) -> Station:
    cells = box.cells
    name = ", ".join(part for part in (box.station, box.division) if part)
    if len(cells) <= 1 or all(cell is None for cell in cells[1:]):
        return Station(box.number, name, cells[0] if cells else None, {}, None, None, None)
    k = len(parties)
    return Station(
        box.number,
        name,
        cells[0],
        dict(zip(parties, cells[1 : 1 + k], strict=True)),
        cells[1 + k],
        cells[1 + k + 5],
        cells[1 + k + 6],
    )


def subdistrict(path: Path, region: int, number: int, parties: list[str]) -> Station:
    """A sub-district's printed total, from a region's summary sheet, for a sub-district whose boxes are not published."""
    page = words(path)[0]
    label = next(w for w in page if w.text == str(number) and w.x1 < 50 and w.y0 > 150)
    cells = sorted(
        (w for w in page if w.x0 >= 150 and NUMBER.fullmatch(w.text) and abs(w.y - label.y) < 10), key=lambda w: w.x0
    )
    k = len(parties)
    if len(cells) != k + 13:
        raise ValueError(f"{path.name}: sub-district {number} has {len(cells)} cells, not {k + 13}")
    v = [value(w.text) for w in cells]
    name = join([w for w in page if w.x1 < 150 and w.x0 > 50 and abs(w.y - label.y) < 14])
    return Station(
        f"region-{region}-sub-district-{number}",
        name.title().replace("Sub- District", "sub-district").replace("Sub-District", "sub-district") + " total",
        v[1],
        dict(zip(parties, v[2 : 2 + k], strict=True)),
        v[2 + k],
        v[7 + k],
        v[8 + k],
        True,
    )


def ints(values: Mapping[str, float | None]) -> dict[str, int]:
    return {name: int(v) for name, v in values.items() if v is not None}


@cache
def contests_2015() -> list[Contest]:
    out: list[Contest] = []
    for kind, label in (("GE", "constituency"), ("RE", "council")):
        summary = summary15(path(f"{kind}_Summary.pdf"))
        width = len(summary.parties) + 2
        for region in range(1, 11):
            sh = sheet15(path(f"{kind}_District_{region}.pdf"))
            row = summary.districts[region]
            if len(sh.boxes) != row[0]:
                raise ValueError(
                    f"2015 {kind} region {region}: {len(sh.boxes)} stations, the summary prints {row[0]:.0f}"
                )
            out.append(
                Contest(
                    label,
                    region,
                    [station(box, sh.parties) for box in sh.boxes],
                    sh.parties,
                    Declared(
                        ints(dict(zip(summary.parties, row[1 : 1 + len(summary.parties)], strict=True))),
                        registered=int(row[2 + width]),
                        cast=int(row[4 + width]),
                    ),
                    None,
                    None,
                    "station",
                )
            )
    return out


PARTS_2025 = {
    1: [""],
    2: [""],
    3: ["-SD1", "-SD2", "-SD3"],
    4: ["-SD1", "-SD2", "-SD3", "-SD4"],
    5: [""],
    6: ["-SD1", "-SD2", "-SD3"],
}


def files_2025(kind: str, region: int) -> list[str]:
    names = urls()
    out = []
    for part in PARTS_2025.get(region, [""]):
        name = f"{kind}_2025_Region{region:02d}{part}.pdf"
        if kind == "GE" and region == 4 and part == "-SD4":
            name = f"{kind}_2025_Region{region:02d}{part}-Recount.pdf"
        if name in names:
            out.append(name)
    return out


@cache
def contests_2025() -> list[Contest]:
    out: list[Contest] = []
    parties, final, national = recount25(path("GE-Results-Comparative-Recount.pdf"))
    gc = constituency_seats25(path("GE-Seat-Allocations-Geographical-Constituencies.pdf"))
    top, top_seats = national25(path("GE-Seat-Allocations-National-Assembly.pdf"))
    councils = councils25(path("RE-Seat-Allocations-Regional-Democratic-Councils.pdf"))
    for region in range(1, 11):
        boxes: list[Box] = []
        labels: list[str] = []
        for name in files_2025("GE", region):
            sh = sheet(path(name))
            boxes += sh.boxes
            labels = sh.parties
        row = final[region]
        stations = [station(box, labels) for box in boxes]
        if region == 6:  # the commission published no box-by-box file for this sub-district of the general election
            stations.append(subdistrict(path("GE_2025_Region06.pdf"), 6, 3, labels))
        out.append(
            Contest(
                "constituency",
                region,
                stations,
                labels,
                Declared(ints({p: row[p] for p in parties if row[p] is not None}), valid=int(row["valid"] or 0)),
                {p: n for p, n in gc[region].items() if n},
                sum(gc[region].values()),
                "station",
            )
        )
    for region in range(1, 11):
        boxes = []
        for name in files_2025("RE", region):
            sh = sheet(path(name))
            boxes += sh.boxes
            labels = sh.parties
        c = councils[region]
        out.append(
            Contest(
                "council",
                region,
                [station(box, labels) for box in boxes],
                labels,
                Declared(c.votes, valid=sum(c.votes.values())),
                {p: n for p, n in c.won.items() if n},
                c.seats,
                "station",
            )
        )
    out.append(
        Contest(
            "top-up",
            0,
            [
                Station(
                    "national",
                    "Guyana",
                    None,
                    {p: float(v) for p, (v, _) in top.items()},
                    national["valid"],
                    None,
                    None,
                )
            ],
            list(top),
            Declared({p: v for p, (v, _) in top.items()}, valid=int(national["valid"] or 0)),
            {p: seats for p, (_, seats) in top.items() if seats},
            top_seats,
            "national",
        )
    )
    return out


@cache
def contests_2020() -> list[Contest]:
    out: list[Contest] = []
    for kind, label, name in (
        ("GE", "constituency", "Tabulation-General-2020.pdf"),
        ("RE", "council", "Tabulation-Regional-2020.pdf"),
    ):
        t = tabulation20(path(name))
        for region in range(1, 11):
            row = t.rows[region]
            votes = {p: row[p] for p in t.parties if row[p] is not None}
            out.append(
                Contest(
                    label,
                    region,
                    [
                        Station(
                            f"region-{region}",
                            REGIONS[region],
                            None,
                            votes,
                            row["valid"],
                            row["rejected"],
                            row["appeared"],
                        )
                    ],
                    list(votes),
                    Declared(
                        ints(votes),
                        valid=int(row["valid"] or 0),
                        invalid=int(row["rejected"] or 0),
                        cast=int(row["appeared"] or 0),
                    ),
                    None,
                    None,
                    "region",
                )
            )
    return out


@cache
def contests_2011() -> list[Contest]:
    out: list[Contest] = []
    found = gazette11(path("GAZETTED_RESULTS_OF_THE_2011_GENERAL_&_REGIONAL_ELECTIONS.pdf"))
    for (kind, region), d in sorted(found.items(), key=lambda item: (item[0][0] != "GC", item[0][1])):
        votes = {p: float(v) for p, v in d.votes.items()}
        unit = "national" if kind == "TOP" else f"region-{region}"
        out.append(
            Contest(
                {"GC": "constituency", "RDC": "council", "TOP": "top-up"}[kind],
                region,
                [
                    Station(
                        unit,
                        "Guyana" if kind == "TOP" else REGIONS[region],
                        None,
                        votes,
                        float(d.valid),
                        float(d.rejected),
                        float(d.valid + d.rejected),
                    )
                ],
                list(d.votes),
                Declared(dict(d.votes), valid=d.valid, invalid=d.rejected),
                {p: n for p, n in d.seats.items() if n},
                sum(d.seats.values()),
                "national" if kind == "TOP" else "region",
            )
        )
    return out


@cache
def loaders() -> dict[str, tuple[Callable[[], list[Contest]], list[str]]]:
    """The contest loader and the files each election reads, by election day."""
    return {
        "2011-11-28": (contests_2011, ["GAZETTED_RESULTS_OF_THE_2011_GENERAL_&_REGIONAL_ELECTIONS.pdf"]),
        "2015-05-11": (
            contests_2015,
            [f"{k}_{n}.pdf" for k in ("GE", "RE") for n in ("Summary", *(f"District_{i}" for i in range(1, 11)))],
        ),
        "2020-03-02": (contests_2020, ["Tabulation-General-2020.pdf", "Tabulation-Regional-2020.pdf"]),
        "2025-09-01": (
            contests_2025,
            [
                "GE-Results-Comparative-Recount.pdf",
                "GE-Seat-Allocations-Geographical-Constituencies.pdf",
                "GE-Seat-Allocations-National-Assembly.pdf",
                "RE-Seat-Allocations-Regional-Democratic-Councils.pdf",
                "GE_2025_Region06.pdf",
                *(n for k in ("GE", "RE") for r in range(1, 11) for n in files_2025(k, r)),
            ],
        ),
    }


def abbreviations() -> list[str]:
    return sorted({p for loader, _ in loaders().values() for c in loader() for p in c.parties})


def registry() -> Package:
    p = Package(
        "gy.gecom/registry",
        SOURCE,
        "Guyanese parties, the National Assembly, regional councils, and their electoral system",
    )
    p.external.add(COUNTRY)
    p.add(
        "source",
        id=GECOM,
        kind="official_body",
        publisher="Guyana Elections Commission",
        title="Election results published by the Guyana Elections Commission",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Guyana Elections Commission",
    )
    p.add("scheme", code="gy.region", polity_id=COUNTRY, kind="electoral_district", rank=1, noun="region")
    p.add("scheme", code="gy.station", polity_id=COUNTRY, kind="precinct", rank=2, noun="polling station")
    p.add("body", id=ASSEMBLY, polity_id=COUNTRY, kind="unicameral")
    p.name(ASSEMBLY, "National Assembly of Guyana", "en", "official")
    for region, name in REGIONS.items():
        p.add("body", id=council(region), polity_id=COUNTRY, kind="council")
        p.name(council(region), f"Regional Democratic Council of Region {region} ({name})", "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=COUNTRY, rules={"formula": "hare"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "Voters choose a party list. Seats are allocated by the largest remainder method with a Hare quota. The "
            "National Assembly has 65 seats: 25 are allocated among the ten geographical constituencies and 40 from "
            "a national top-up list. Each Regional Democratic Council is allocated in the same way."
        ),
        lang="en",
    )
    for abbreviation in abbreviations():
        party = key("party", abbreviation)
        p.add("party", id=party, kind="bloc" if abbreviation in BLOCS else "party", polity_id=COUNTRY)
        p.name(party, abbreviation, "en", "abbreviation")
        if abbreviation in FULL:
            p.name(party, FULL[abbreviation], "en", "official", False)
    return p


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: float | None) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=int(value),
    )


def _unit(p: Package, day: str, c: Contest, s: Station, made: dict[str, str]) -> str:
    code = f"{day}:{s.number}"
    if code in made:
        return made[code]
    unit = made[code] = key("unit", code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=COUNTRY,
        scheme_code="gy.station" if c.unit == "station" and not s.aggregate else "gy.region",
        code=code,
        area_relation="unlocated",
        area_id=None,
        kind="standard" if c.unit == "station" and not s.aggregate else "aggregate",
    )
    p.name(
        unit,
        f"{s.name} (box {s.number})" if c.unit == "station" and s.name and not s.aggregate else s.name or s.number,
        "en",
        "official",
    )
    return unit


CAUSES = {
    "2011-11-28": "The gazette's declared figures disagree with its own entries:",
    "2015-05-11": "The summary sheet's printed electors differ from the sum of the district sheet's stations:",
    "2020-03-02": "The tabulation's printed figures differ from its own rows:",
    "2025-09-01": "Declared totals follow the recount and the amended gazette, while the station sheets carry the "
    "counts printed on the tabulation forms, which the Commission did not republish after the recount. In Region 4 "
    "the general election sheet for sub-district 2 prints no votes for station 40409, although the sub-district's "
    "printed total exceeds its other stations by 199 valid votes:",
}


def differences(c: Contest) -> list[str]:
    """Where a contest's declared totals differ from the sum of its published rows."""
    d = c.declared
    if d is None:
        return []
    out: list[str] = []
    for party, declared in d.votes.items():
        total = sum(s.votes.get(party) or 0 for s in c.stations)
        if total != declared:
            out.append(f"{party} declared {declared:,} against {total:,.0f} in the rows")
    for label, declared, pick in (
        ("registered", d.registered, lambda s: s.registered),
        ("valid", d.valid, lambda s: s.valid),
        ("rejected", d.invalid, lambda s: s.invalid),
        ("cast", d.cast, lambda s: s.cast),
    ):
        if declared is not None:
            total = sum(pick(s) or 0 for s in c.stations)
            if total != declared:
                out.append(f"{label} declared {declared:,} against {total:,.0f} in the rows")
    return out


def build(day: str) -> Package:
    loader, files = loaders()[day]
    first = DAYS[day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Guyanese general and regional elections of {first} by polling station where published, "
        "from the Guyana Elections Commission's tabulation forms",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(COUNTRY)
    for name in files:
        p.artifact(raw.cached(SOURCE, urls()[name]), GECOM)
    election = key("election", day)
    p.add("election", id=election, polity_id=COUNTRY, kind="general", date_from=first, date_to=first, status="held")
    p.add("slug", path=f"gy/{day}-general", entity_id=election, is_current=True)
    p.name(election, f"{first.year} Guyanese general and regional elections", "en", "official")
    made: dict[str, str] = {}
    differing: dict[str, list[str]] = {}
    over = 0
    for c in loader():
        name = f"{c.kind}-{c.region}" if c.region else c.kind
        contest = key("contest", day, name)
        english = {
            "constituency": f"{first.year} National Assembly election, geographical constituency of Region {c.region} ({REGIONS.get(c.region, '')})",
            "council": f"{first.year} Regional Democratic Council election, Region {c.region} ({REGIONS.get(c.region, '')})",
            "top-up": f"{first.year} National Assembly election, national top-up",
        }[c.kind]
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=council(c.region) if c.kind == "council" else ASSEMBLY,
            magnitude=c.magnitude,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, english, "en", "official")
        slug = {"constituency": f"constituency-{c.region}", "council": f"council-{c.region}", "top-up": "top-up"}[
            c.kind
        ]
        p.add("slug", path=f"gy/{day}-general/{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, name)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=GECOM,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        options = {}
        for position, party in enumerate(c.parties, 1):
            option = options[party] = key("option", day, name, party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=key("party", party),
                ballot_position=position,
                ballot_label=party,
            )
        for s in c.stations:
            unit = _unit(p, day, c, s, made)
            _put(p, result_set, unit, None, REGISTERED, s.registered)
            _put(p, result_set, unit, None, VALID, s.valid)
            _put(p, result_set, unit, None, INVALID, s.invalid)
            _put(p, result_set, unit, None, CAST, s.cast)
            for party, option in options.items():
                _put(p, result_set, unit, option, VOTES, s.votes.get(party))
        d = c.declared
        if d is not None:
            for measure, value in ((REGISTERED, d.registered), (VALID, d.valid), (INVALID, d.invalid), (CAST, d.cast)):
                if value is not None:
                    p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            for party, value in d.votes.items():
                if party in options:
                    p.add("declared", result_set_id=result_set, option_id=options[party], measure=VOTES, value=value)
        for party, seats in (c.seats or {}).items():
            p.add("outcome", result_set_id=result_set, option_id=options[party], kind="won", seats=seats)
        gap = differences(c)
        if gap:
            differing[english] = gap
            p.add(
                "note",
                id=key("note", result_set, "declared"),
                entity_id=result_set,
                kind="caveat",
                body_md="The totals the Commission declares for this contest differ from the sum of its published "
                f"rows: {'; '.join(gap)}. Both are stored as printed.",
                lang="en",
            )
        over += sum(1 for s in c.stations if s.registered is not None and s.cast is not None and s.cast > s.registered)
    if differing:
        p.accept(
            "accounting.declared", CAUSES[day] + " " + " | ".join(f"{k}: {'; '.join(v)}" for k, v in differing.items())
        )
    if over:
        p.accept(
            "accounting.turnout",
            f"{over} polling stations print more ballots cast than electors on their list; stored as printed",
        )
    if day == "2011-11-28":
        p.accept(
            "accounting.votes_valid",
            "the gazette prints 54,942 valid votes for the Region 6 council while its four lists sum to 54,790; "
            "stored as printed",
        )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())
