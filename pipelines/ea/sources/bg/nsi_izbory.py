"""Bulgaria's National Assembly elections of the Principality and Kingdom, from the National Statistical Institute's
scanned election volumes.

The NSI's statistical library (statlib.nsi.bg) serves scans of the Direction of Statistics' volumes "Статистика на
изборите за народни представители" for the ordinary and grand national assemblies of 1899 to 1927. The scans have no
text layer. Some files bind several volumes together: `Izbory_14_X_1900.pdf` holds the volumes of the X, XI, and XII
Ordinary National Assemblies (1899, 1901, and 1902).

Each volume prints, for every electoral district (околия) and its polling sections (секции), the registered voters, the
voters, and the votes of every elected and non-elected candidate (its Table IV). Those tables were read from the page
images and transcribed into `nsi_izbory_tables/{key}.txt`, one file per election, page by page. Parsing checks every
printed figure: each candidate's section votes against the candidate's printed total and the sections' registered
voters and voters against the district's printed totals. A mismatch raises, unless the transcription records that the
printed figures themselves disagree, so a misread number never reaches a package. Where Table IV's scan was unclear,
the same volume's Table III, which prints each section's registered voters and voters and every elected candidate's
votes again, settled the reading; the transcription notes each such case.

The elections of the X to XIII Ordinary National Assemblies (1899 to 1903) and their by-elections are transcribed so
far. The later volumes, from the XIV Assembly of 1908 on, are fetched but not yet transcribed.

Until 1916 Bulgaria kept the Julian calendar. Election days are stored in the Gregorian calendar, with the Julian date
as printed kept as the original text.
"""

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from io import BytesIO
from pathlib import Path
from uuid import uuid5

import numpy as np
from PIL import Image, ImageFilter

from ea import package, raw
from ea.package import Package

SOURCE = "bg.nsi_izbory"
BULGARIA = str(uuid5(package.NAMESPACE, "polity\x1fBG"))
BASE = "https://statlib.nsi.bg/asset/default"
TABLES = Path(__file__).with_name("nsi_izbory_tables")
VOTES, REGISTERED, VOTERS = 1, 2, 14
DERIVED, DIGITIZED = 1, 8
"""Bits of a fact's flags: worked out rather than read, and read from a scan."""
SECTION = "bg.izbory_sekciya"
OKOLIYA = "bg.izbory_okoliya"
DPI = 145


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NSI_SOURCE = key("source", "statistika-na-izborite")
ASSEMBLY = key("body", "ordinary-national-assembly")
SYSTEM_BLOCK = key("electoral_system", "block", 1897)
REGISTRY = f"{SOURCE}/registry"


@dataclass(frozen=True)
class Strips:
    """The horizontal extent of the number columns of a volume's Table IV, as fractions of the page width: registered
    voters, voters, the elected candidates' votes, and the non-elected candidates' votes."""

    registered: tuple[float, float]
    voters: tuple[float, float]
    elected: tuple[float, float]
    other: tuple[float, float]


@dataclass(frozen=True)
class Edition:
    key: str
    file: str
    julian: tuple[int, int, int] | None
    """The election day in the Julian calendar as printed, for elections before 1916."""
    printed: str
    """The election day as the volume prints it."""
    kind: str
    assembly: str
    """The ordinal of the assembly, as printed (X, XI ...)."""
    strips: Strips
    day: date | None = None
    """The Gregorian election day, for elections from 1916 on."""


def gregorian(julian: tuple[int, int, int]) -> date:
    """The Gregorian date of a Julian calendar date, given as year, month, and day (a Julian 29 February 1900 has no
    Python date)."""
    year, month, day = julian
    a = (14 - month) // 12
    y, m = year + 4800 - a, month + 12 * a - 3
    number = day + (153 * m + 2) // 5 + 365 * y + y // 4 - 32083
    return date.fromordinal(number - 1721425)


X_STRIPS = Strips((0.225, 0.262), (0.264, 0.300), (0.565, 0.605), (0.905, 0.945))
VOLUME_X = "Izbory_14_X_1900.pdf"
VOLUME_XIII = "Izbory_14_XIII_1909.pdf"
EDITIONS = [
    Edition("1899-general", VOLUME_X, (1899, 4, 25), "25 априлий 1899 г.", "general", "X", X_STRIPS),
    Edition("1899-by-election", VOLUME_X, (1899, 9, 19), "19 септемврий 1899 г.", "by_election", "X", X_STRIPS),
    Edition("1899-12-by-election", VOLUME_X, (1899, 12, 5), "5 декемврий 1899 г.", "by_election", "X", X_STRIPS),
    Edition("1900-02-by-election", VOLUME_X, (1900, 2, 6), "6 февруарий 1900 г.", "by_election", "X", X_STRIPS),
    Edition("1901-general", VOLUME_X, (1901, 1, 28), "28 януарий 1901 г.", "general", "XI", X_STRIPS),
    Edition("1901-04-by-election", VOLUME_X, (1901, 4, 8), "8 априлий 1901 г.", "by_election", "XI", X_STRIPS),
    Edition("1901-10-by-election", VOLUME_X, (1901, 10, 7), "7 октомврий 1901 г.", "by_election", "XI", X_STRIPS),
    Edition("1902-general", VOLUME_X, (1902, 2, 17), "17 февруарий 1902 г.", "general", "XII", X_STRIPS),
    Edition("1902-by-election", VOLUME_X, (1902, 9, 1), "1 септемврий 1902 г.", "by_election", "XII", X_STRIPS),
    Edition("1903-04-by-election", VOLUME_X, (1903, 4, 20), "20 априлий 1903 г.", "by_election", "XII", X_STRIPS),
    Edition("1903-general", VOLUME_XIII, (1903, 10, 19), "19 октомврий 1903 г.", "general", "XIII", X_STRIPS),
    Edition("1904-by-election", VOLUME_XIII, (1904, 2, 29), "29 февруарий 1904 г.", "by_election", "XIII", X_STRIPS),
    Edition("1905-by-election", VOLUME_XIII, (1905, 3, 20), "20 мартъ 1905 г.", "by_election", "XIII", X_STRIPS),
    Edition("1906-by-election", VOLUME_XIII, (1906, 3, 12), "12 мартъ 1906 г.", "by_election", "XIII", X_STRIPS),
    Edition("1907-by-election", VOLUME_XIII, (1907, 4, 1), "1 априлъ 1907 г.", "by_election", "XIII", X_STRIPS),
]
BY_KEY = {e.key: e for e in EDITIONS}


def url(file: str) -> str:
    return f"{BASE}/{file}"


def election_day(e: Edition) -> date:
    if e.julian is not None:
        return gregorian(e.julian)
    if e.day is None:
        raise ValueError(f"{e.key}: no election day")
    return e.day


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{e.key}" for e in EDITIONS if (TABLES / f"{e.key}.txt").exists())]


VOLUMES = [
    "Izbory_14_X_1900.pdf",
    "Izbory_14_XI_1904.pdf",
    "Izbory_14_XII_1904.pdf",
    "Izbory_14_XIII_1909.pdf",
    "Izbory_14_XIV_1910.pdf",
    "Izbory_14_V_1914.pdf",
    "Izbory_14_XV_1914.pdf",
    "Izbory_14_XVII-XVIII-XIX_1928.pdf",
    "Izbory_14_XX_1927.pdf",
    "Izbory_14_XXI_1928.pdf",
]
"""Every election volume the statistical library serves; `Izbory_14_XI_1904.pdf` and `Izbory_14_XII_1904.pdf` are
separate scans of volumes that `Izbory_14_X_1900.pdf` also binds."""


def fetch() -> None:
    for file in VOLUMES:
        raw.fetch(SOURCE, url(file), suffix=".pdf")


def parse() -> None:
    registry().write()
    for e in EDITIONS:
        if (TABLES / f"{e.key}.txt").exists():
            election(e).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------- transcriptions

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11}
ROMAN.update({"XII": 12, "XIII": 13, "XIV": 14, "XV": 15})


@dataclass
class Cell:
    """One printed number: its value (None for a dash), its page, and its column."""

    value: int | None
    page: int
    column: str
    inferred: bool = False
    """Whether the printed figure is hidden on the scan and was worked out from the printed total."""


@dataclass
class Section:
    number: str
    registered: Cell
    voters: Cell


@dataclass
class Candidate:
    elected: bool
    name: str
    votes: dict[str, Cell] = field(default_factory=dict)
    total: Cell | None = None
    unread: bool = False
    """Whether the scan cuts off the candidate's votes, so only the name is known."""


@dataclass
class District:
    number: str
    """The district's number in the table, with the letter of a split district in Latin script (the volumes print
    Plovdiv's two rural districts of 1901 as 7а and 7б)."""
    name: str
    latin: str
    page: int
    sections: list[Section] = field(default_factory=list)
    total: tuple[Cell, Cell] | None = None
    candidates: list[Candidate] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)


def _cell(text: str, page: int, column: str) -> Cell:
    text = text.strip()
    if text == "?":
        raise Unread(f"page {page}: an unreadable cell")
    inferred = text.endswith("!")
    text = text.rstrip("?!")
    if text in ("-", "—", ""):
        return Cell(None, page, column)
    if not text.isdigit():
        raise ValueError(f"page {page}: {text!r} is not a number")
    return Cell(int(text), page, column, inferred)


class Unread(ValueError):
    """A printed figure the scan hides entirely."""


def _votes(text: str, page: int, column: str) -> Iterator[tuple[str, Cell]]:
    if text.strip() in ("", "-", "—", "?"):
        return
    for word in text.split():
        section, value = word.split(":", 1)
        if "-" in section and value.rstrip("!?") in ("-", "—"):
            continue  # one dash printed across several sections: no votes in any of them
        if section not in ROMAN and not section.isdigit():
            raise ValueError(f"page {page}: section {section!r}")
        yield section, _cell(value, page, column)


@cache
def transcription(edition: str) -> list[District]:
    districts: list[District] = []
    by_number: dict[str, District] = {}
    page = 0
    current: District | None = None
    for line in (TABLES / f"{edition}.txt").read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"# page \d+", line):
            page = int(line.split()[2])
            continue
        if line.startswith("# MISMATCH"):
            if current is None:
                raise ValueError(f"{edition} page {page}: a mismatch before any district")
            current.mismatches.append(line.removeprefix("# MISMATCH").strip())
            continue
        if line.startswith("#"):
            continue
        head, _, rest = line.partition(" ")
        parts = [part.strip() for part in rest.split("|")]
        if head == "D":
            parts[0] = parts[0].replace("-", "").replace("а", "a").replace("б", "b").replace("в", "c")
            if parts[1] == "cont":
                current = by_number[parts[0]] if parts[0] != "?" else districts[-1]
                continue
            number = parts[0]
            if number in by_number:
                raise ValueError(f"{edition} page {page}: district {number} twice")
            current = by_number[number] = District(number, parts[1], parts[2], page)
            districts.append(current)
            continue
        if current is None:
            raise ValueError(f"{edition} page {page}: {line!r} before any district")
        if head == "S":
            number, registered, voters = rest.split()
            current.sections.append(
                Section(number, _cell(registered, page, "registered"), _cell(voters, page, "voters"))
            )
        elif head == "T":
            registered, voters = rest.split()
            current.total = (_cell(registered, page, "registered"), _cell(voters, page, "voters"))
        elif head in ("C", "C+"):
            elected = parts[0] == "E"
            if parts[0] not in ("E", "N"):
                raise ValueError(f"{edition} page {page}: {line!r}")
            column = "elected" if elected else "other"
            if head == "C":
                candidate = Candidate(elected, parts[1])
                current.candidates.append(candidate)
            else:
                candidate = next(c for c in reversed(current.candidates) if c.elected == elected)
            if parts[2].strip() == "?" and parts[3].strip() == "?":
                candidate.unread = True
                continue
            try:
                for section, cell in _votes(parts[2], page, column):
                    if section in candidate.votes:
                        raise ValueError(f"{edition} page {page}: {candidate.name} section {section} twice")
                    candidate.votes[section] = cell
                if parts[3].strip() != "?":
                    candidate.total = _cell(parts[3], page, column)
            except Unread:
                # the scan hides some of the candidate's figures: keep the name, store none of the votes
                candidate.unread = True
        elif head == "F":
            current.footnotes.append(rest)
        else:
            raise ValueError(f"{edition} page {page}: unknown line {line!r}")
    return districts


def _value(cell: Cell | None) -> int:
    return 0 if cell is None or cell.value is None else cell.value


def disagreements(d: District) -> list[str]:
    """The printed checks a district's figures fail, in words."""
    found: list[str] = []
    sections = {s.number for s in d.sections}
    if d.total is not None:
        for index, column in enumerate(("registered voters", "voters")):
            summed = sum(_value((s.registered, s.voters)[index]) for s in d.sections)
            if summed != _value(d.total[index]):
                found.append(
                    f"the sections' {column} add up to {summed:,}, but the district total prints {_value(d.total[index]):,}"
                )
    if not any(c.elected for c in d.candidates):
        found.append("no candidate is marked elected")
    for c in d.candidates:
        if c.unread:
            continue
        if set(c.votes) - sections:
            found.append(
                f"{clean(c.name)} has votes in sections {sorted(set(c.votes) - sections)} that the district lacks"
            )
        summed = sum(_value(cell) for cell in c.votes.values())
        if c.total is None or summed != _value(c.total):
            printed = "no total" if c.total is None else f"a total of {_value(c.total):,}"
            found.append(f"{clean(c.name)}'s section votes add up to {summed:,}, but the volume prints {printed}")
    return found


def problems(edition: str) -> list[str]:
    """Every printed check the transcription fails, except in districts whose transcription records that the
    volume's own figures disagree."""
    found: list[str] = []
    for d in transcription(edition):
        own = disagreements(d)
        if own and not d.mismatches:
            found.extend(f"district {d.number}: {text}" for text in own)
        if d.mismatches and not own:
            found.append(f"district {d.number}: a recorded mismatch, but the figures add up")
    return found


def tag_of(number: str) -> str:
    """A district's number as a code: two digits, with a letter suffix in Latin script."""
    digits = number.rstrip("abc")
    return f"{int(digits):02d}" + number[len(digits) :]


def clean(name: str) -> str:
    """A candidate's name without the volume's marks (К., ОТ., *, †) and the transcription's notes."""
    name = re.sub(r"\(+[А-ЯA-Z]+\.?\)|\[[^\]]*\]|†", "", name)
    return " ".join(name.replace("?", "").split())


# ---------------------------------------------------------------- page boxes


@cache
def _image(pdf: Path, page: int) -> np.ndarray:
    out = subprocess.run(
        ["pdftoppm", "-r", str(DPI), "-gray", "-f", str(page), "-l", str(page), str(pdf)],
        capture_output=True,
        check=True,
    ).stdout
    page_image = Image.open(BytesIO(out)).convert("L")
    # divide out the uneven paper tone of the scan, so a fixed threshold finds the ink everywhere on the page
    paper = page_image.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(12))
    flat = np.asarray(page_image, dtype=np.float32) / np.maximum(np.asarray(paper, dtype=np.float32), 1)
    return np.clip(flat * 255, 0, 255).astype(np.uint8)


def _bands(image: np.ndarray, x0: int, x1: int) -> list[tuple[int, int]]:
    """The printed lines between two x positions: runs of rows with ink, after dropping the table's vertical rules,
    too tall to be a horizontal rule and too short to be two lines."""
    height = image.shape[0]
    top, bottom = round(height * 0.17), round(height * 0.985)
    dark = image[top:bottom, x0:x1] < 170
    rule = dark.mean(axis=0) > 0.45
    rule = rule | np.roll(rule, 1) | np.roll(rule, -1) | np.roll(rule, 2) | np.roll(rule, -2)
    dark = dark[:, ~rule]
    if not dark.shape[1]:
        return []
    ink = dark.sum(axis=1)
    rows = (ink >= 3) & (ink < 0.6 * dark.shape[1])
    bands: list[tuple[int, int]] = []
    start = None
    for y, on in enumerate(rows):
        if on and start is None:
            start = y
        elif not on and start is not None:
            bands.append((start + top, y + top))
            start = None
    if start is not None:
        bands.append((start + top, len(rows) + top))
    return [(a, b) for a, b in bands if 6 <= b - a <= 26]


def _column(image: np.ndarray, strip: tuple[float, float], count: int) -> tuple[int, int, list[tuple[int, int]]]:
    """Find a number column near its usual place: slide the strip until its printed lines number exactly the
    transcribed numbers, preferring the place with the most ink."""
    width = image.shape[1]
    span = round((strip[1] - strip[0]) * width)
    best: tuple[float, int, list[tuple[int, int]]] | None = None
    for shift in range(-round(0.05 * width), round(0.05 * width) + 1, 3):
        x0 = round(strip[0] * width) + shift
        if x0 < 0 or x0 + span > width:
            continue
        bands = _bands(image, x0, x0 + span)
        if len(bands) != count:
            continue
        ink = float((image[:, x0 : x0 + span] < 170).sum())
        if best is None or ink > best[0]:
            best = (ink, x0, bands)
    if best is None:
        return round(strip[0] * width), round(strip[0] * width) + span, []
    return best[1], best[1] + span, best[2]


type Box = tuple[float, float, float, float]


@cache
def boxes(edition: str, pdf: Path) -> dict[int, Box]:
    """The box of each printed number, by the cell's id, where the column's printed lines on its page match the
    transcribed numbers one to one; other cells get the box of their column on the page."""
    e = BY_KEY[edition]
    runs: dict[tuple[int, str], list[Cell]] = {}
    for d in transcription(edition):
        for s in d.sections:
            for cell in (s.registered, s.voters):
                runs.setdefault((cell.page, cell.column), []).append(cell)
        if d.total is not None:
            for cell in d.total:
                runs.setdefault((cell.page, cell.column), []).append(cell)
        for column in (True, False):
            for c in d.candidates:
                if c.elected != column:
                    continue
                for cell in [*c.votes.values(), *([c.total] if c.total else [])]:
                    runs.setdefault((cell.page, cell.column), []).append(cell)
    found: dict[int, Box] = {}
    for (page, column), cells in runs.items():
        image = _image(pdf, page)
        height, width = image.shape
        strip: tuple[float, float] = getattr(e.strips, column)
        printed = [cell for cell in cells if cell.value is not None]
        x0, x1, bands = _column(image, strip, len(printed))
        whole = (x0 / width, 0.17, x1 / width, 0.985)
        for cell, (y0, y1) in zip(printed, bands, strict=False):
            found[id(cell)] = (x0 / width, (y0 - 2) / height, x1 / width, (y1 + 2) / height)
        for cell in cells:
            found.setdefault(id(cell), whole)
    return found


# ---------------------------------------------------------------- registry


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The National Statistical Institute's scanned election volumes of the Principality and Kingdom of Bulgaria, "
        "with the Ordinary National Assembly and its electoral system",
    )
    p.external.add(BULGARIA)
    p.add(
        "source",
        id=NSI_SOURCE,
        kind="official_body",
        publisher="Дирекция на статистиката (Национален статистически институт)",
        title="Статистика на изборите за народни представители",
        url=url(VOLUME_X),
        attribution="Национален статистически институт, статистическа библиотека",
    )
    for file in sorted({e.file for e in EDITIONS}):
        p.artifact(raw.cached(SOURCE, url(file)), NSI_SOURCE)
    p.add("scheme", code=OKOLIYA, polity_id=BULGARIA, kind="electoral_district", rank=3, noun="electoral district")
    p.add("scheme", code=SECTION, polity_id=BULGARIA, kind="precinct", rank=4, noun="polling section")
    p.add("body", id=ASSEMBLY, polity_id=BULGARIA, kind="unicameral")
    p.name(ASSEMBLY, "Ordinary National Assembly of Bulgaria (1879-1946)", "en", "official")
    p.name(ASSEMBLY, "Обикновено народно събрание", "bg", "native", False)
    p.add(
        "note",
        id=key("note", ASSEMBLY),
        entity_id=ASSEMBLY,
        kind="methodology",
        lang="en",
        body_md=(
            "The single chamber of the Principality (from 1908 the Kingdom) of Bulgaria under the Tarnovo "
            "Constitution of 1879. Grand National Assemblies, elected to amend the constitution or choose a monarch, "
            "were separate bodies."
        ),
    )
    p.add("electoral_system", id=SYSTEM_BLOCK, polity_id=BULGARIA, rules={"formula": "block"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM_BLOCK),
        entity_id=SYSTEM_BLOCK,
        kind="methodology",
        lang="en",
        body_md=(
            "Plurality block voting under the election law of 23 March 1897, amended on 19 December 1898 and 15 "
            "June 1901, as the volumes describe it: each electoral district (околия) elects a number of deputies set "
            "by its population, each voter may vote for as many candidates as there are seats, and the candidates "
            "with the most votes are proclaimed elected."
        ),
    )
    return p


# ---------------------------------------------------------------- elections


def artifact_id(storage_key: str) -> str:
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{NSI_SOURCE}\x1f{storage_key}"))


def _page_sizes(pdf: Path) -> dict[int, tuple[int, int]]:
    out = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "9999", str(pdf)], capture_output=True, text=True, check=True
    ).stdout
    return {
        int(m.group(1)): (round(float(m.group(2))), round(float(m.group(3))))
        for m in re.finditer(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", out)
    }


def _latin(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("é", "e").replace("è", "e")).strip("-")


NOUNS = {"general": "general election", "by_election": "by-elections"}
NATIVE = {"general": "Избори за {} Обикновено народно събрание", "by_election": "Допълнителни избори за {} ОНС"}
METHOD = (
    "Transcribed from Table IV of the Direction of Statistics' volume (scanned by the National Statistical "
    "Institute; pages {pages} of {file}): the elected and non-elected candidates and the votes each received in each "
    "polling section, with each section's registered voters and voters. Every number was checked against the totals "
    "the table prints: each candidate's sections against the candidate's total, and the sections' registered voters "
    "and voters against the district's totals. Each voter could vote for as many candidates as the district elects, "
    "so the candidates' votes add up to more than the voters. Non-elected candidates are those the table prints: "
    "every declared candidate, and anyone else who received more than 30 votes. Handwritten party marks that a "
    "reader added to the scanned copy are not part of the printed table and are not stored."
)


def election(e: Edition) -> Package:
    districts = transcription(e.key)
    failed = problems(e.key)
    if failed:
        raise ValueError(f"{e.key}: " + "; ".join(failed))
    day = election_day(e)
    noun = NOUNS[e.kind]
    p = Package(
        f"{SOURCE}/{e.key}",
        SOURCE,
        f"The Bulgarian National Assembly {noun} of {day} by polling section, from the Direction of Statistics' volume",
        depends=[REGISTRY],
    )
    p.external.add(BULGARIA)
    stored = raw.cached(SOURCE, url(e.file))
    p.uses(stored)
    election_id = key("election", e.key)
    p.add(
        "election",
        id=election_id,
        polity_id=BULGARIA,
        kind=e.kind,
        date_from=day,
        date_to=day,
        date_precision="day",
        original_calendar="julian" if e.julian else None,
        original_text=e.printed,
        status="held",
    )
    p.name(election_id, f"{day.year} Bulgarian National Assembly {noun}", "en", "official")
    p.name(election_id, NATIVE[e.kind].format(e.assembly), "bg", "native", False)
    path = f"bg/{day}-{'general' if e.kind == 'general' else 'by-election'}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)

    pdf = stored.path
    sizes = _page_sizes(pdf)
    located = boxes(e.key, pdf)
    pages = sorted({d.page for d in districts} | {c.page for d in districts for c in _cells(d)})
    page_ids: dict[int, str] = {}
    for number in pages:
        width, height = sizes[number]
        page_ids[number] = key("page", e.file, number)
        p.add(
            "source_page",
            id=page_ids[number],
            artifact_id=artifact_id(stored.storage_key),
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}/{number}.png",
            width=width,
            height=height,
        )
    method = METHOD.format(pages=f"{pages[0]}-{pages[-1]}", file=e.file)

    def fact(result_set: str, unit: str, option: str | None, measure: int, cell: Cell) -> None:
        if cell.value is None and option is None:
            return
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=DIGITIZED | (DERIVED if cell.inferred else 0),
            value=cell.value or 0,
        )
        x0, y0, x1, y1 = located[id(cell)]
        p.add(
            "value_locator",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            page_id=page_ids[cell.page],
            source_id=None,
            x0=round(x0, 4),
            y0=round(y0, 4),
            x1=round(x1, 4),
            y1=round(y1, 4),
        )

    disagreeing = [f"district {d.number}: {text}" for d in districts for text in disagreements(d)]
    if disagreeing:
        p.accept(
            "accounting.declared",
            "The volume's own printed totals disagree with its section figures, which are stored as printed: "
            + "; ".join(disagreeing),
        )
    for d in districts:
        tag = tag_of(d.number)
        seats = sum(c.elected for c in d.candidates)
        contest = key("contest", e.key, tag)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=ASSEMBLY,
            magnitude=seats,
            electoral_system_id=SYSTEM_BLOCK,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"{d.number}. {d.latin}", "en", "official")
        p.name(contest, f"{d.number}. {d.name}", "bg", "native", False)
        p.add("slug", path=f"{path}/{tag}-{_latin(d.latin)}", entity_id=contest, is_current=True)
        result_set = key("result_set", e.key, tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=NSI_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", lang="en", body_md=method)
        caveats: list[str] = []
        own = disagreements(d)
        if own:
            caveats.append(
                "The volume's own printed figures for this district do not add up: "
                + "; ".join(own)
                + ". Every figure is stored as printed."
            )
        for c in d.candidates:
            if re.search(r"\(ОТ\.?\)", c.name):
                caveats.append(
                    f"{clean(c.name)} was also elected in another district and took the seat there, so he declined "
                    "this one (the volume marks him ОТ.)."
                )
            if "(К.)" in c.name:
                caveats.append(
                    f"The National Assembly annulled the election of {clean(c.name)} (the volume marks him К.)."
                )
            if c.unread:
                caveats.append(
                    f"A stamp or the edge of the scan hides some of the votes of {clean(c.name)}, so none of that "
                    "candidate's votes are stored."
                )
        inferred = [c for c in d.candidates for cell in c.votes.values() if cell.inferred]
        inferred += [c for c in d.candidates if c.total is not None and c.total.inferred]
        if inferred:
            names = ", ".join(dict.fromkeys(clean(c.name) for c in inferred))
            caveats.append(
                f"A stamp or the edge of the scan hides a figure of {names}; each hidden figure was worked "
                "out from the candidate's other printed figures (the section votes and the total) and is flagged as "
                "derived."
            )
        for index, text in enumerate(caveats):
            p.add(
                "note", id=key("note", result_set, index), entity_id=result_set, kind="caveat", lang="en", body_md=text
            )
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=NSI_SOURCE,
            locator={"url": url(e.file), "pdf_page": d.page, "table": "IV"},
        )
        district_unit = key("unit", e.key, tag)
        p.add(
            "reporting_unit",
            id=district_unit,
            polity_id=BULGARIA,
            scheme_code=OKOLIYA,
            code=f"{day}:{tag}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(district_unit, d.name, "bg", "official")
        units: dict[str, str] = {}
        for s in d.sections:
            unit = units[s.number] = key("unit", e.key, tag, s.number)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=BULGARIA,
                scheme_code=SECTION,
                code=f"{day}:{tag}:{s.number}",
                parent_id=district_unit,
                area_relation="unlocated",
                area_id=None,
                kind="standard",
            )
            p.name(unit, f"{d.name}, {s.number} секция", "bg", "official")
            fact(result_set, unit, None, REGISTERED, s.registered)
            fact(result_set, unit, None, VOTERS, s.voters)
        if d.total is not None:
            for measure, cell in zip((REGISTERED, VOTERS), d.total, strict=True):
                if cell.value is not None:
                    p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=cell.value)
        ranked = sorted(d.candidates, key=lambda c: (not c.elected, -_value(c.total)))
        for position, c in enumerate(ranked, start=1):
            option = key("option", e.key, tag, c.name)
            person = key("person", e.key, tag, c.name)
            name = clean(c.name)
            p.add("person", id=person)
            p.name(person, name, "bg", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=name,
            )
            p.add("candidacy", id=key("candidacy", e.key, tag, c.name), person_id=person, option_id=option, role="head")
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if c.elected else "lost",
                seats=1 if c.elected else None,
            )
            for section, cell in c.votes.items():
                fact(result_set, units[section], option, VOTES, cell)
            if c.total is not None and c.total.value is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=c.total.value)
    return p


def _cells(d: District) -> Iterator[Cell]:
    for s in d.sections:
        yield s.registered
        yield s.voters
    if d.total is not None:
        yield from d.total
    for c in d.candidates:
        yield from c.votes.values()
        if c.total is not None:
            yield c.total
