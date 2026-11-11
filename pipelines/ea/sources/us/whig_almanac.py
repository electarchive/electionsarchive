import base64
import csv
import difflib
import functools
import io
import itertools
import json
import re
import sys
import tarfile
from collections import Counter
from datetime import date
from pathlib import Path

import numpy
from anthropic.types import ImageBlockParam, MessageParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.sources.us import house_books

SOURCE = "us.whig_almanac"
RAW = "us.whig_almanac"
VERSION = "1"
KIND = "compendium"
PUBLISHER = "Horace Greeley and Company"
TITLE = "The Whig Almanac and United States Register for 1849"
LICENSE = "Public domain"
SUMMARY = "Presidential results of 1844 and 1848 by county from the Whig Almanac for 1849"
STATE = None
FIPS = None
ITEM = "vol1849whigalman00whig"
PDF = f"https://archive.org/download/{ITEM}/{ITEM}.pdf"
LANDING = f"https://archive.org/details/{ITEM}"
PAGES = tuple(range(53, 64))
CANDIDATES = {1848: ("Taylor", "Cass", "Van Buren"), 1844: ("Clay", "Polk", "Birney")}
NAMED: dict[tuple[str, str], str] = {}
PRINTED: dict[tuple[str, int, str], int] = {}
STARTS = {(54, "coinpart")}
Row = tuple[str, str, list[int | None], list[str], str]
ITEMS = {
    1844: (ITEM, 1849, date(1844, 11, 1), PAGES),
    1848: (ITEM, 1849, date(1848, 11, 7), PAGES),
}
KEYS = {
    (1844, state, 0): f"1844:{state}:president"
    for state in ("ME", "NH", "VT", "MA", "RI", "CT", "NY", "NJ", "PA", "DE", "MD", "VA", "NC", "GA", "AL", "MS")
    + ("LA", "TN", "KY", "OH", "IN", "IL", "MO", "MI", "AR", "SC")
}
VOLUMES = {year: (PDF, LANDING, day, pages) for year, (_, _, day, pages) in ITEMS.items()}
ERRATA: set[tuple[int, tuple[str, int]]] = set()
REREAD = ("1848-11-07:VT:president", "1844:MD:president")
CHECKED = {
    1848: ("ME", "VT", "MA", "RI", "NH", "CT", "NJ", "DE", "MD", "VA", "NC", "AL", "GA", "LA", "MS", "TN", "KY")
    + ("OH", "IN", "IL", "MO", "MI", "WI", "IA"),
    1844: ("ME", "VT", "MA", "RI", "NH", "CT", "NJ", "PA", "DE", "MD", "VA", "NC", "AL", "GA", "LA", "MS", "TN")
    + ("KY", "OH", "IN", "IL"),
}
NAMES = {
    ("NH", "Hillsboro'"): "Hillsborough",
    ("CT", "N.-London"): "New London",
    ("MD", "Balt. City"): "24510",
    ("MD", "Baltimore Co."): "24005",
    ("MD", "2 Alleghany"): "Allegany",
    ("RI", "N. Shoreham t'p"): "Newport",
    ("PA", "Montgom'ry"): "Montgomery",
    ("PA", "Northam'ton"): "Northampton",
    ("PA", "Northumb'ld"): "Northumberland",
    ("PA", "Susquehana"): "Susquehanna",
    ("PA", "Westm'land"): "Westmoreland",
    ("PA", "Co. in part"): "42101",
    ("PA", "City, 4 Wds"): "42101",
    ("PA", "City,in part"): "42101",
    ("PA", "Spring G'n"): "42101",
    ("PA", "N.Liberties"): "42101",
    ("PA", "Germant'n."): "42101",
    ("PA", "Kensington"): "42101",
    ("PA", "15 Districts"): "42101",
    ("VA", "Alexandria"): "51013",
    ("VA", "Bedford"): "51019",
    ("VA", "Fairfax"): "51059",
    ("VA", "Franklin"): "51067",
    ("VA", "Richmond"): "51159",
    ("VA", "Richmond City"): "51760",
    ("VA", "Roanoke"): "51161",
    ("VA", "Norfolk Borough"): "51710",
    ("VA", "Norfolk County"): "VA:norfolkcounty",
    ("VA", "Williamsburgh"): "51830",
    ("VA", "Elizabeth City"): "VA:elizabethcity",
    ("VA", "Nansemond"): "VA:nansemond",
    ("VA", "Princess Anne"): "VA:princessanne",
    ("VA", "Warwick"): "VA:warwick",
    ("VA", "Culpepper"): "Culpeper",
    ("VA", "King & Queen"): "King and Queen",
    ("VA", "Loudon"): "Loudoun",
    ("VA", "Lunenburgh"): "Lunenburg",
    ("VA", "Highland [part.]"): "Highland",
    ("VA", "Barbour"): "VA:barbour",
    ("VA", "Berkeley"): "VA:berkeley",
    ("VA", "Boone"): "VA:boone",
    ("VA", "Braxton"): "VA:braxton",
    ("VA", "Brooke"): "VA:brooke",
    ("VA", "Cabell"): "VA:cabell",
    ("VA", "Doddridge"): "VA:doddridge",
    ("VA", "Fayette"): "VA:fayette",
    ("VA", "Gilmer"): "VA:gilmer",
    ("VA", "Greenbrier"): "VA:greenbrier",
    ("VA", "Hampshire"): "VA:hampshire",
    ("VA", "Hancock"): "VA:hancock",
    ("VA", "Hardy"): "VA:hardy",
    ("VA", "Harrison"): "VA:harrison",
    ("VA", "Jackson"): "VA:jackson",
    ("VA", "Jefferson"): "VA:jefferson",
    ("VA", "Kanawha"): "VA:kanawha",
    ("VA", "Lewis"): "VA:lewis",
    ("VA", "Logan"): "VA:logan",
    ("VA", "Marion"): "VA:marion",
    ("VA", "Marshall"): "VA:marshall",
    ("VA", "Mason"): "VA:mason",
    ("VA", "Mercer"): "VA:mercer",
    ("VA", "Monongalia"): "VA:monongalia",
    ("VA", "Monroe"): "VA:monroe",
    ("VA", "Morgan"): "VA:morgan",
    ("VA", "Nicholas"): "VA:nicholas",
    ("VA", "Ohio"): "VA:ohio",
    ("VA", "Pendleton"): "VA:pendleton",
    ("VA", "Pocahontas"): "VA:pocahontas",
    ("VA", "Preston"): "VA:preston",
    ("VA", "Putnam"): "VA:putnam",
    ("VA", "Randolph"): "VA:randolph",
    ("VA", "Ritchie"): "VA:ritchie",
    ("VA", "Tyler"): "VA:tyler",
    ("VA", "Wayne"): "VA:wayne",
    ("VA", "Wetzel"): "VA:wetzel",
    ("VA", "Wood"): "VA:wood",
    ("NC", "Cleaveland"): "Cleveland",
    ("NC", "N. Hanover"): "New Hanover",
    ("NC", "Perquimons"): "Perquimans",
    ("NC", "Stanley"): "Stanly",
    ("NC", "Yancy"): "Yancey",
    ("AL", "Benton"): "Calhoun",
    ("GA", "Campbell"): "GA:campbell",
    ("GA", "Cass"): "Bartow",
    ("GA", "Clark"): "Clarke",
    ("GA", "Green"): "Greene",
    ("GA", "Scriven"): "Screven",
    ("LA", "Carroll"): "LA:carroll",
    ("LA", "E. Baton Rouge"): "East Baton Rouge",
    ("LA", "E. Feliciana"): "East Feliciana",
    ("LA", "Lafourche Int."): "Lafourche",
    ("LA", "Point Coupee"): "Pointe Coupee",
    ("LA", "St. John"): "St. John the Baptist",
    ("LA", "Vermillion"): "Vermilion",
    ("LA", "W. Baton Rouge"): "West Baton Rouge",
    ("LA", "W. Feliciana"): "West Feliciana",
    ("MS", "La Fayettte"): "Lafayette",
    ("MS", "Tishamingo"): "Tishomingo",
    ("KY", "Breckenridge"): "Breckinridge",
    ("KY", "Greene"): "Green",
    ("KY", "Louisville City"): "Jefferson",
    ("KY", "Muhlenburg"): "Muhlenberg",
    ("IN", "Clarke"): "Clark",
    ("IN", "Richardsville"): "Howard",
    ("IL", "Iroquis"): "Iroquois",
    ("IL", "Macdonough"): "McDonough",
    ("IL", "Vermillion"): "Vermilion",
    ("MO", "Calloway"): "Callaway",
    ("MO", "Cape Girard."): "Cape Girardeau",
    ("MO", "Green"): "Greene",
    ("MO", "St. Genevieve"): "Ste. Genevieve",
    ("MO", "St. Louis"): "29189",
    ("MO", "Van Buren"): "Cass",
    ("MI", "Berry"): "Barry",
    ("WI", "Crawford&Chipa"): "WI:crawfordandchippewacounties",
    ("WI", "Iowa & Richland"): "WI:iowaandrichlandcounties",
    ("WI", "Greene"): "Green",
    ("WI", "Milwaukie"): "Milwaukee",
    ("IA", "Pottawatamie"): "Pottawattamie",
    ("IA", "Vapello"): "Wapello",
}
FORMER: dict[tuple[str, str], tuple[str, str, date]] = {
    ("VA", "barbour"): ("Barbour", "54001", date(1863, 6, 20)),
    ("VA", "boone"): ("Boone", "54005", date(1863, 6, 20)),
    ("VA", "braxton"): ("Braxton", "54007", date(1863, 6, 20)),
    ("VA", "doddridge"): ("Doddridge", "54017", date(1863, 6, 20)),
    ("VA", "fayette"): ("Fayette", "54019", date(1863, 6, 20)),
    ("VA", "gilmer"): ("Gilmer", "54021", date(1863, 6, 20)),
    ("VA", "hancock"): ("Hancock", "54029", date(1863, 6, 20)),
    ("VA", "jackson"): ("Jackson", "54035", date(1863, 6, 20)),
    ("VA", "marion"): ("Marion", "54049", date(1863, 6, 20)),
    ("VA", "marshall"): ("Marshall", "54051", date(1863, 6, 20)),
    ("VA", "mercer"): ("Mercer", "54055", date(1863, 6, 20)),
    ("VA", "putnam"): ("Putnam", "54079", date(1863, 6, 20)),
    ("VA", "ritchie"): ("Ritchie", "54085", date(1863, 6, 20)),
    ("VA", "wayne"): ("Wayne", "54099", date(1863, 6, 20)),
    ("VA", "wetzel"): ("Wetzel", "54103", date(1863, 6, 20)),
    ("WI", "crawfordandchippewacounties"): ("Crawford and Chippewa Counties", "55023", date(1849, 1, 1)),
    ("WI", "iowaandrichlandcounties"): ("Iowa and Richland Counties", "55049", date(1849, 1, 1)),
}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = False
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 190, 230)
MODEL = "claude-haiku-5-5"
ORIGINALS = f"https://archive.org/download/{ITEM}/{ITEM}_orig_jp2.tar"
READS = {"f": (2, 1.0), "g": (2, 1.0), "h": (3, 1.25)}
LEGACY = ("c", "d", "e")
JOB = "originals"
FRAMES = {
    53: (0.02, 0.47, 0.97),
    54: (0.17, 0.568, 0.97),
    55: (0.02, 0.474, 0.92),
    56: (0.17, 0.564, 0.95),
    57: (0.02, 0.435, 0.86),
    58: (0.17, 0.582, 0.97),
    59: (0.02, 0.466, 0.92),
    60: (0.17, 0.584, 0.97),
    61: (0.02, 0.432, 0.86),
    62: (0.19, 0.577, 0.96),
}
CROPS: dict[int, dict[str, tuple[tuple[float, float, float, float], bool]]] = {
    page: {"l": ((left, 0.02, middle + 0.015, 0.99), True), "r": ((middle - 0.015, 0.02, right, 0.99), True)}
    for page, (left, middle, right) in FRAMES.items()
}
CROPS[62] = {
    "l": CROPS[62]["l"],
    "m": ((0.574, 0.02, 0.78, 0.665), False),
    "n": ((0.772, 0.02, 0.98, 0.665), False),
    "o": ((0.574, 0.65, 0.98, 0.99), False),
}
CROPS[63] = {
    "a": ((0.0, 0.02, 0.49, 0.185), False),
    "b": ((0.43, 0.02, 0.88, 0.185), False),
    "x": ((0.0, 0.17, 0.37, 0.53), False),
    "y": ((0.33, 0.17, 0.59, 0.53), False),
    "z": ((0.55, 0.17, 0.88, 0.53), False),
}
PROMPT = (
    "This is a crop of a page of the Whig Almanac for 1849 that prints election returns by county. Transcribe the "
    "county rows of the presidential tables exactly as printed, digit for digit; do not correct, estimate, or fill "
    "in anything. The crop may cut into a neighboring column at its edges: ignore partial text there. Skip tables "
    "headed Congress, Aggregate Congress Vote, or Congressmen, and all prose. Output plain text only, in this form. "
    "When a state heading is visible, write a line `# XX` with the state's two-letter postal code. Whenever a "
    "column heading line is visible, including a repeated one, write a line `HEAD | label, label, label` that lists "
    "every numeric column heading as printed, left to right (for example `Taylor, Cass, Clay, Polk, Crit., Pow.`). "
    "Each county or parish is one line, `County | n n n n`, with every number in that row left to right, one per "
    "column of the last HEAD, without thousands separators, and `-` for a cell that is empty, dashed, or holds words "
    "such as `new co.` or `new par.`. The dots between numbers are leaders, not digits. Leave out the district "
    "numbers in front of county names. A table that continues from the previous column starts without a heading: "
    "write its rows with no `#` or HEAD line. When a printed Total Vote line for a state is visible, write it as "
    "`TOTAL 1848 | Taylor 67141, Cass 49720` (one line per year, surnames as printed, numbers without commas). "
    "Skip any line you cannot read rather than guessing."
)


def fetch() -> None:
    raw.fetch(RAW, PDF, suffix=".pdf")
    raw.fetch(RAW, ORIGINALS, suffix=".tar")


def _leaf(page: int) -> Image.Image:
    with tarfile.open(raw.cached(RAW, ORIGINALS).path) as tar:
        member = tar.extractfile(f"{ITEM}_orig_jp2/{ITEM}_orig_{page:04d}.jp2")
        if member is None:
            raise ValueError(f"no original image for page {page}")
        image = Image.open(io.BytesIO(member.read())).convert("L").rotate(-90 if page % 2 else 90, expand=True)
    light = numpy.asarray(image) > 150
    rows = numpy.flatnonzero(light.mean(axis=1) > 0.35)
    columns = numpy.flatnonzero(light.mean(axis=0) > 0.35)
    return image.crop((int(columns.min()), int(rows.min()), int(columns.max()), int(rows.max())))


def _labels(name: str, page: int) -> list[str]:
    if name in LEGACY:
        return ["l", "r", "x", "y", "z"] if page == 63 else ["l", "r"]
    pieces = READS[name][0]
    return [f"{side}{index}" for side, (_, split) in CROPS[page].items() for index in range(pieces if split else 1)]


def crops(name: str) -> dict[str, tuple[bytes, tuple[int, int]]]:
    pieces, scale = READS[name]
    out: dict[str, tuple[bytes, tuple[int, int]]] = {}
    for page in PAGES:
        image = _leaf(page)
        width, height = image.size
        for side, ((left, top, right, bottom), split) in CROPS[page].items():
            part = image.crop((round(left * width), round(top * height), round(right * width), round(bottom * height)))
            ink = 255 - numpy.asarray(part, dtype=float).mean(axis=1)
            edges = [0]
            for k in range(1, pieces if split else 1):
                low, high = round(part.height * (k / pieces - 0.05)), round(part.height * (k / pieces + 0.05))
                edges.append(low + int(ink[low:high].argmin()))
            edges.append(part.height)
            for index, (upper, lower) in enumerate(itertools.pairwise(edges)):
                piece = part.crop((0, upper, part.width, lower))
                piece = piece.resize(
                    (round(piece.width * scale), round(piece.height * scale)), Image.Resampling.LANCZOS
                )
                buffer = io.BytesIO()
                piece.save(buffer, "PNG")
                out[f"p{page}{side}{index}"] = (buffer.getvalue(), piece.size)
    return out


def key(name: str, label: str) -> str:
    return f"{SOURCE}/{name}/{label}"


def ensure() -> bool:
    wanted: dict[str, batch.Want] = {}
    for name in READS:
        for label, (data, (width, height)) in crops(name).items():
            content: list[ImageBlockParam | TextBlockParam] = [
                ImageBlockParam(
                    type="image",
                    source={"type": "base64", "media_type": "image/png", "data": base64.b64encode(data).decode()},
                ),
                TextBlockParam(type="text", text=PROMPT),
            ]
            params = MessageCreateParamsNonStreaming(
                model=MODEL, max_tokens=16000, messages=[MessageParam(role="user", content=content)]
            )
            wanted[key(name, label)] = (params, (width * height / 750 * 0.05 + 6000 * 0.25) / 1e6)
    ready, note = batch.ensure(SOURCE, JOB, wanted)
    print(note)
    return ready


def _plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _candidate(year: int, label: str) -> str | None:
    plain = _plain(label)
    for name in CANDIDATES[year]:
        if plain[:2] == _plain(name)[:2] or (name == "Van Buren" and plain.startswith("v")):
            return name
    return None


def _columns(head: list[str]) -> dict[int, dict[str, int]]:
    plain = [_plain(label) for label in head]
    taylor = next((i for i, p in enumerate(plain) if p.startswith("ta")), None)
    clay = next((i for i, p in enumerate(plain) if p.startswith("cl")), None)
    out: dict[int, dict[str, int]] = {1848: {}, 1844: {}}
    if taylor is not None:
        out[1848]["Taylor"] = taylor
        if taylor + 1 < len(plain) and plain[taylor + 1].startswith("cas"):
            out[1848]["Cass"] = taylor + 1
        if taylor + 2 < len(plain) and plain[taylor + 2].startswith("v"):
            out[1848]["Van Buren"] = taylor + 2
    if clay is not None:
        out[1844]["Clay"] = clay
        if clay + 1 < len(plain) and plain[clay + 1].startswith("po"):
            out[1844]["Polk"] = clay + 1
            if clay + 2 < len(plain) and plain[clay + 2][:1] in ("b", "d") and not plain[clay + 2].startswith("br"):
                out[1844]["Birney"] = clay + 2
    return out


def _read(name: str) -> tuple[dict[int, list[Row]], list[tuple[Row | None, int, str, int]]]:
    rows: dict[int, list[Row]] = {page: [] for page in PAGES}
    totals: list[tuple[Row | None, int, str, int]] = []
    last: Row | None = None
    state = ""
    mark = ""
    head: list[str] = []
    for page in PAGES:
        for label in _labels(name, page):
            message = json.loads(raw.cached(RAW, key(name, f"p{page}{label}")).path.read_text())
            text = "".join(block.get("text", "") for block in message["content"] if block.get("type") == "text")
            for line in text.splitlines():
                line = line.strip().strip("`")
                if match := re.fullmatch(r"#\s*([A-Z]{2})", line):
                    state = mark = match[1]
                elif line.startswith("HEAD") and "|" in line:
                    head = [label.strip() for label in line.split("|", 1)[1].replace("|", ",").split(",")]
                elif match := re.fullmatch(r"TOTAL\s+(\d{4})\s*\|(.*)", line):
                    for part in match[2].split(",") if int(match[1]) in CANDIDATES else ():
                        if found := re.fullmatch(r"\s*(.*?)\s+([\d,]+)\s*", part):
                            for year in CANDIDATES:
                                candidate = _candidate(year, found[1])
                                if candidate is not None:
                                    totals.append((last, year, candidate, int(found[2].replace(",", ""))))
                elif "|" in line and state and head:
                    county, rest = (part.strip() for part in line.split("|", 1))
                    county = re.sub(r"\s*[*†‡]+$", "", county)
                    cells = rest.replace("|", " ").split()
                    if (
                        not county
                        or re.search(r"vote|total", county, re.IGNORECASE)
                        or len(cells) != len(head)
                        or not all(re.fullmatch(r"\d+|-", c) for c in cells)
                    ):
                        continue
                    last = (state, county, [None if c == "-" else int(c) for c in cells], head, mark)
                    rows[page].append(last)
                    mark = ""
    return rows, totals


@functools.cache
def _alike(one: str, other: str) -> float:
    return 1.0 if one == other else difflib.SequenceMatcher(None, one, other).ratio()


def _align(spine: list[dict[str, Row]], name: str, rows: list[Row]) -> list[dict[str, Row]]:
    def score(slot: dict[str, Row], row: Row) -> float:
        if sum(v is not None for v in row[2]) >= 3 and any(other[2] == row[2] for other in slot.values()):
            return 2.0
        best = max(_alike(_plain(other[1]), _plain(row[1])) for other in slot.values())
        return best if best >= 0.6 else 0.0

    table = [[0.0] * (len(rows) + 1) for _ in range(len(spine) + 1)]
    for i, slot in enumerate(spine):
        for j, row in enumerate(rows):
            gain = score(slot, row)
            table[i + 1][j + 1] = max(table[i][j + 1], table[i + 1][j], table[i][j] + gain if gain else 0.0)
    out: list[dict[str, Row]] = []
    i, j = len(spine), len(rows)
    while i or j:
        gain = score(spine[i - 1], rows[j - 1]) if i and j else 0.0
        if gain and table[i][j] == table[i - 1][j - 1] + gain:
            out.append({**spine[i - 1], name: rows[j - 1]})
            i, j = i - 1, j - 1
        elif j and table[i][j] == table[i][j - 1]:
            out.append({name: rows[j - 1]})
            j -= 1
        else:
            out.append(spine[i - 1])
            i -= 1
    return out[::-1]


def merge() -> tuple[
    list[tuple[str, str]],
    dict[tuple[str, str], int],
    dict[tuple[str, str], dict[tuple[int, str], dict[str, int | None]]],
    dict[tuple[str, int, str], int],
]:
    reads = {name: _read(name) for name in (*READS, *LEGACY)}
    slots: list[tuple[int, str, dict[str, Row]]] = []
    state = ""
    for page in PAGES:
        spine: list[dict[str, Row]] = []
        for name in sorted(reads, key=lambda n: -len(reads[n][0][page])):
            spine = _align(spine, name, reads[name][0][page])
        for slot in spine:
            marks = Counter(row[4] for row in slot.values() if row[4]).most_common()
            if marks and (marks[0][1] >= 2 or (page, _plain(next(iter(slot.values()))[1])) in STARTS):
                state = marks[0][0]
            elif marks and marks[0][0] != state:
                print(f"one read starts {marks[0][0]} at {next(iter(slot.values()))[1]} on page {page}, in {state}")
            last = slots[-1][2] if slots and slots[-1][0] == page else None
            if (
                last is not None
                and not set(last) & set(slot)
                and any(
                    a[2] == b[2] and sum(v is not None for v in a[2]) >= 2 for a in last.values() for b in slot.values()
                )
            ):
                last.update(slot)
                continue
            slots.append((page, state, slot))
    mapping: dict[tuple[str, int], dict[int, dict[str, int]]] = {}
    for state, width in {(state, len(row[2])) for _, state, slot in slots for row in slot.values()}:
        heads = Counter(
            tuple(row[3])
            for _, at, slot in slots
            for row in slot.values()
            if at == state == row[0] and len(row[2]) == width
        )
        if heads:
            mapping[(state, width)] = _columns(list(heads.most_common(1)[0][0]))
    placed = {id(row): state for _, state, slot in slots for row in slot.values()}
    given: dict[tuple[str, int, str], Counter[int]] = {}
    for _, totals in reads.values():
        for last, year, candidate, value in totals:
            if last is not None:
                given.setdefault((placed[id(last)], year, candidate), Counter())[value] += 1
    printed = {key_: votes.most_common(1)[0][0] for key_, votes in given.items() if votes.most_common(1)[0][1] >= 2}
    order: list[tuple[str, str]] = []
    pages: dict[tuple[str, str], int] = {}
    seen: dict[tuple[str, str], dict[tuple[int, str], dict[str, int | None]]] = {}
    first: dict[tuple[str, str], dict[str, Row]] = {}
    for page, state, slot in slots:
        names = Counter(row[1] for name, row in slot.items() if name in READS) or Counter(
            row[1] for row in slot.values()
        )
        row = (state, _plain(names.most_common(1)[0][0]))
        if row in seen and not any(a[2] == b[2] for a in first[row].values() for b in slot.values()):
            row = (state, f"{row[1]}again")
        if row not in seen:
            order.append(row)
            pages[row] = page
            NAMED[row] = names.most_common(1)[0][0]
            seen[row] = {}
            first[row] = slot
        for name, (_, _, values, _, _) in slot.items():
            for year, picked in mapping.get((state, len(values)), {}).items():
                for candidate, index in picked.items():
                    seen[row].setdefault((year, candidate), {}).setdefault(name, values[index])
    return order, pages, seen, printed | PRINTED


def _settle(by: dict[str, int | None]) -> tuple[bool, int | None]:
    sharp = Counter(v for name, v in by.items() if name in READS).most_common()
    every = Counter(by.values()).most_common()
    if sharp and sharp[0][1] >= 2:
        return True, sharp[0][0]
    if every[0][1] >= 2 and (len(every) == 1 or every[1][1] < every[0][1]):
        return True, every[0][0]
    return False, None


def build() -> None:
    order, pages, seen, printed = merge()
    checked: dict[tuple[str, str, int, str], int | None] = {
        (line["state"], line["county"], int(line["year"]), line["candidate"]): int(line["votes"])
        if line["votes"]
        else None
        for line in csv.DictReader(Path(__file__).with_name("whig_almanac.tsv").open(), delimiter="\t")
    }
    for row in {key_[:2] for key_ in checked} - set(order):
        print(f"checked by eye but not in the reads: {row}")
    settled: dict[tuple[str, str, int, str], int | None] = {}
    doubts: dict[tuple[str, int, str], list[str]] = {}
    for row in order:
        for cell, by in seen[row].items():
            sure, value = _settle(by)
            if (*row, *cell) in checked:
                settled[(*row, *cell)] = checked[(*row, *cell)]
                continue
            if sure:
                settled[(*row, *cell)] = value
            if not sure or len(set(by.values())) > 1:
                doubts.setdefault((row[0], *cell), []).append(
                    f"{NAMED[row]} p{pages[row]}: {value if sure else '?'} of {dict(Counter(by.values()))}"
                )
    folder = raw.parsed(SOURCE) / "model"
    folder.mkdir(parents=True, exist_ok=True)
    for year, names in CANDIDATES.items():
        text: list[str] = []
        for state in dict.fromkeys(state for state, _ in order):
            rows = [row for row in order if row[0] == state]
            used = [c for c in names if any(settled.get((*row, year, c)) is not None for row in rows)]
            if not used:
                continue
            kept = [row for row in rows if any(settled.get((*row, year, c)) is not None for c in used)]
            whole = all((*row, year, c) in settled for row in rows for c in used if (year, c) in seen[row])
            sums = [sum(settled.get((*row, year, c)) or 0 for row in kept) for c in used]
            given = [printed.get((state, year, c)) for c in used]
            if not whole or (sums != given and state not in CHECKED[year]):
                print(f"{year} {state}: left out, {len(rows)} rows, county sums {sums}, printed totals {given}")
                for c in used:
                    for doubt in doubts.get((state, year, c), []):
                        print(f"    {c} {doubt}")
                continue
            page = None
            for row in kept:
                if pages[row] != page:
                    page = pages[row]
                    text += [f"@{page}", f"# {state}", f"0 | {', '.join(used)}"]
                text.append(f"{NAMED[row]} | {' '.join(str(settled.get((*row, year, c)) or 0) for c in used)}")
            if sums == given:
                text.append(f"Totals | {' '.join(str(g) for g in given)}")
            print(f"{year} {state}: {len(kept)} rows, county sums {sums}, printed totals {given}")
        for suffix in ("txt", "v2.txt"):
            (folder / f"{year}.{suffix}").write_text("\n".join(text) + "\n")


def parse() -> None:
    if not ensure():
        return
    build()
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
