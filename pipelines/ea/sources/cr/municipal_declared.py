"""The declaratorias of Costa Rica's municipal elections of 2016, 2020, and 2024: the mayors and vice mayors, the
councilors (regidores), and the district intendants and vice intendants the Tribunal declares elected, canton by canton.
"""

import html
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ea import raw
from ea.sources.cr.declared import BASE, SOURCE, decode, fold, refuse

MAYOR, COUNCIL, INTENDANT = "alcaldia", "regiduria", "intendencia"


@dataclass(frozen=True)
class Resolution:
    """A municipal declaratoria: its number, where it is published under /juris/, its day, and the contests it names."""

    number: str
    path: str
    day: date
    family: str


D16, D20, D24 = date(2016, 2, 7), date(2020, 2, 2), date(2024, 2, 4)


def _many(day: date, family: str, folder: str, suffix: str, numbers: str) -> tuple[Resolution, ...]:
    year = day.year
    return tuple(
        Resolution(f"{n}-E11-{year}", f"{folder}/{n}-E11-{year}.{suffix}", day, family) for n in numbers.split()
    )


RESOLUTIONS: tuple[Resolution, ...] = (
    *_many(D16, MAYOR, "electorales", "pdf", "1308 1314 1309 1310 1311 1312 1348 1467 1313"),
    *_many(D16, COUNCIL, "electorales", "pdf", "1376 1377 1378 1379 1380 1381 1468 1382"),
    *_many(D16, INTENDANT, "electorales", "pdf", "1825"),
    *_many(D20, MAYOR, "relevantes", "pdf", "1280 1494 1281 1282 1317 1318 1319"),
    *_many(D20, COUNCIL, "relevantes", "pdf", "1495 1496 1497 1498 1564 1565 1566"),
    *_many(D20, INTENDANT, "relevantes", "pdf", "1374"),
    *_many(D24, MAYOR, "relevantes", "html", "1658 2156 2157 2158 2159 2160 2161"),
    *_many(D24, COUNCIL, "relevantes", "html", "2218 2219 2220 2221 2222 2223 2224"),
    *_many(D24, INTENDANT, "relevantes", "html", "2715"),
)
"""The declaratorias of mayors, councilors, and district intendants the Tribunal's index lists for each election."""


def url(resolution: Resolution) -> str:
    return f"{BASE}/juris/{resolution.path}"


def fetch() -> list[Resolution]:
    got = []
    for resolution in RESOLUTIONS:
        try:
            raw.fetch(SOURCE, url(resolution), suffix=Path(resolution.path).suffix, check=refuse)
        except ConnectionError, OSError:
            continue
        got.append(resolution)
    return got


def available(day: date) -> list[Resolution]:
    found = []
    for resolution in RESOLUTIONS:
        if resolution.day != day:
            continue
        try:
            raw.cached(SOURCE, url(resolution))
        except LookupError:
            continue
        found.append(resolution)
    return found


def lines(resolution: Resolution) -> list[str]:
    """The text after 'POR TANTO', one line for each printed line (PDF) or paragraph and table row (HTML)."""
    path = raw.cached(SOURCE, url(resolution)).path
    if path.suffix == ".pdf":
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode()
    else:
        page = decode(path.read_bytes())
        page = re.sub(r"<(script|style).*?</\1>", "", page, flags=re.DOTALL | re.IGNORECASE)
        page = re.sub(r"<br[^>]*>|</(?:p|tr|div|h\d|li)>", "\x00", page, flags=re.IGNORECASE)
        page = re.sub(r"</t[dh]>", " ", page, flags=re.IGNORECASE)
        page = html.unescape(re.sub(r"<[^>]+>", " ", page))
        text = "\n".join(" ".join(part.split()) for part in page.split("\x00"))
    found = [" ".join(line.split()) for line in text.splitlines()]
    found = [line for line in found if line]
    start = max(i for i, line in enumerate(found) if fold(line) == "POR TANTO")
    return found[start + 1 :]


@dataclass(frozen=True)
class Named:
    """A person a declaratoria declares elected: the place heading it is listed under, the role, the order within the
    role, the name, and the party's initials."""

    place: str  # folded
    role: str  # "head", "running_mate", "list_member", or "substitute"
    position: int
    name: str
    initials: str


PERSON = re.compile(r"^(\d{9})\s+(.+?)\s+([A-Z][A-Z0-9.-]{0,11})$")
RENAMED = {"VALVERDE VEGA": "SARCHI"}
"""Cantons a declaratoria names by an earlier name, with the name the workbooks print."""


def role_of(heading: str) -> tuple[str, int]:
    """The role and the position of the people listed under a heading, or no role for a heading of another post."""
    if heading.startswith("VICE"):
        return "running_mate", 2 if "SEGUND" in heading else 1
    if heading in ("ALCALDE", "ALCALDESA", "INTENDENTE", "INTENDENTA"):
        return "head", 0
    if heading.startswith("REGID"):
        return ("substitute" if "SUPLENTE" in heading else "list_member"), 0
    return "", 0


def named(resolution: Resolution, places: set[str]) -> list[Named]:
    """Every person the declaratoria lists after 'POR TANTO', under the place heading (a folded name from places)
    that precedes them."""
    found: list[Named] = []
    place = ""
    role = ""
    position = 0
    for line in lines(resolution):
        folded = fold(line)
        folded = RENAMED.get(folded, folded)
        row = PERSON.match(line)
        if row and place and role:
            if role in ("list_member", "substitute"):
                position += 1
            found.append(Named(place, role, position, " ".join(row.group(2).split()), row.group(3).replace(".", "")))
        elif folded in places:
            place, role, position = folded, "", 0
        elif line == line.upper() and not re.search(r"\d", line) and not folded.startswith("C C "):
            role, position = role_of(folded)
    return found


STOP = {"DE", "DEL", "LA", "LAS", "LOS", "EL", "POR", "Y", "PARA", "EN", "A", "AL", "PARTIDO"}


def _split(letters: str, words: list[str], skip: bool) -> bool:
    """Whether the letters are prefixes of the words in order, one word each, every word used unless skip."""
    if not letters:
        return skip or not words
    if not words:
        return False
    first = words[0]
    for size in range(min(len(first), len(letters)), 0, -1):
        if first.startswith(letters[:size]) and _split(letters[size:], words[1:], skip):
            return True
    return skip and _split(letters, words[1:], skip)


def fit(initials: str, label: str) -> int:
    """How well a party's initials fit its name: 3 when they spell every significant word's start (or are a word of
    it), 2 when they spell some of them, 0 when they do not."""
    words = [w for w in label.split() if w not in STOP]
    best = 0
    for letters in {initials, *((initials[1:],) if initials.startswith("P") and len(initials) > 1 else ())}:
        if letters in words or _split(letters, words, skip=False):
            best = 3
        elif _split(letters, words, skip=True) or _split(letters, label.split(), skip=True):
            best = max(best, 2)
    return best


def resolve(initials: str, labels: list[str]) -> str | None:
    """The one party among labels whose name the initials fit best, or None when no one or several fit as well."""
    scores = {label: fit(initials, label) for label in labels}
    top = max(scores.values(), default=0)
    if not top:
        return None
    best = [label for label, score in scores.items() if score == top]
    return best[0] if len(best) == 1 else None
