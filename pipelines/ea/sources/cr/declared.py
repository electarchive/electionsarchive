"""Costa Rica's declared results: seats by party and province from the workbooks, and the winners the declaratorias name."""

import html
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw

SOURCE = "cr.tse"
BASE = "https://www.tse.go.cr"
INDEX_URL = f"{BASE}/declaratorias.html"
PROVINCES = ("SAN JOSE", "ALAJUELA", "CARTAGO", "HEREDIA", "GUANACASTE", "PUNTARENAS", "LIMON")
CHALLENGE = b"Radware Bot Manager"
ATTEMPTS = 6
"""How many times to ask for a declaratoria: the site answers some requests with a bot challenge and others with the file."""


@dataclass(frozen=True)
class Resolution:
    """A declaratoria of the Tribunal: its number, where it is published, and what it declares."""

    number: str
    path: str  # under /juris/
    day: date  # the election day it belongs to
    kind: str  # "president" or "assembly"
    round: int = 1


RESOLUTIONS = (
    Resolution("0551-E11-2014", "electorales/0551-E11-2014.html", date(2014, 2, 2), "president", 1),
    Resolution("1430-E11-2014", "electorales/1430-E11-2014.html", date(2014, 4, 6), "president", 2),
    Resolution("0830-E11-2014", "electorales/0830-E11-2014.pdf", date(2014, 2, 2), "assembly"),
    Resolution("1265-E11-2018", "electorales/1265-E11-2018.html", date(2018, 2, 4), "president", 1),
    Resolution("2241-E11-2018", "relevantes/2241-E11-2018.html", date(2018, 4, 1), "president", 2),
    Resolution("1230-E11-2018", "electorales/1230-E11-2018.html", date(2018, 2, 4), "assembly"),
    Resolution("2424-E11-2022", "electorales/2424-E11-2022.html", date(2022, 4, 3), "president", 2),
    Resolution("2363-E11-2026", "relevantes/2363-E11-2026.html", date(2026, 2, 1), "assembly"),
    Resolution("1221-E11-2022", "relevantes/1221-E11-2022.html", date(2022, 2, 6), "president", 1),
    Resolution("1555-E11-2022", "electorales/1555-E11-2022.html", date(2022, 2, 6), "assembly"),
    Resolution("1876-E11-2026", "relevantes/1876-E11-2026.html", date(2026, 2, 1), "president", 1),
)
"""The declaratorias the pipeline reads. Each of the others the Tribunal lists for 2018 to 2026 answered with a bot challenge."""
SEAT_SHEETS = {
    date(2014, 2, 2): "C4.8",
    date(2018, 2, 4): "C4.8",
    date(2022, 2, 6): "C4.8",
    date(2026, 2, 1): "C3.8",
}
PARTIES = {
    "PLN": "LIBERACION NACIONAL",
    "PAC": "ACCION CIUDADANA",
    "FA": "FRENTE AMPLIO",
    "PUSC": "UNIDAD SOCIAL CRISTIANA",
    "PML": "MOVIMIENTO LIBERTARIO",
    "PREN": "RESTAURACION NACIONAL",
    "PASE": "ACCESIBILIDAD SIN EXCLUSION",
    "PRC": "RENOVACION COSTARRICENSE",
    "ADC": "ALIANZA DEMOCRATA CRISTIANA",
    "PSD": "PROGRESO SOCIAL DEMOCRATICO",
    "NR": "NUEVA REPUBLICA",
    "LP": "LIBERAL PROGRESISTA",
    "PIN": "INTEGRACION NACIONAL",
    "PRSC": "REPUBLICANO SOCIAL CRISTIANO",
    "CAC": "COALICION AGENDA CIUDADANA",
    "PPSO": "PUEBLO SOBERANO",
}
"""The initials the declaratorias print after an elected deputy, with the party's name as the workbooks print it."""


def url(resolution: Resolution) -> str:
    return f"{BASE}/juris/{resolution.path}"


def refuse(path: Path) -> None:
    if CHALLENGE in path.read_bytes()[:20000]:
        raise ConnectionError("the Tribunal's site answered with a bot challenge instead of the file")


def fetch() -> list[Resolution]:
    """Fetch the index and every declaratoria; return those that answered with the file."""
    raw.fetch(SOURCE, INDEX_URL, suffix=".html")
    got = []
    for resolution in RESOLUTIONS:
        for attempt in range(ATTEMPTS):
            try:
                raw.fetch(SOURCE, url(resolution), suffix=Path(resolution.path).suffix, check=refuse)
            except ConnectionError, OSError:
                if attempt + 1 < ATTEMPTS:
                    time.sleep(5)
                continue
            got.append(resolution)
            break
    return got


def available() -> list[Resolution]:
    found = []
    for resolution in RESOLUTIONS:
        try:
            raw.cached(SOURCE, url(resolution))
        except LookupError:
            continue
        found.append(resolution)
    return found


def fold(text: str) -> str:
    import unicodedata

    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", plain).upper().split())


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def plain(resolution: Resolution) -> str:
    """The text of a declaratoria on one line, whitespace collapsed."""
    path = raw.cached(SOURCE, url(resolution)).path
    if path.suffix == ".pdf":
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode()
        return text
    text = re.sub(r"<(script|style).*?</\1>", "", decode(path.read_bytes()), flags=re.DOTALL | re.IGNORECASE)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def squash(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class Seats:
    """The seats the workbook declares, by province then party, and each province's magnitude."""

    magnitude: dict[int, int]
    won: dict[int, dict[str, int]]


def seats(frame: pl.DataFrame) -> Seats:
    """Read the table of deputies elected by province and party."""
    cells = [[(c or "").strip() for c in row] for row in frame.iter_rows()]
    header = next(i for i, row in enumerate(cells) if fold(row[0]).startswith("PARTIDO"))
    columns = {
        col: PROVINCES.index(fold(text)) + 1 for col, text in enumerate(cells[header]) if fold(text) in PROVINCES
    }
    magnitude: dict[int, int] = {}
    won: dict[int, dict[str, int]] = {province: {} for province in columns.values()}
    for row in cells[header + 1 :]:
        label = fold(row[0])
        if not label or label.startswith("FUENTE"):
            continue
        for col, province in columns.items():
            count = int(float(row[col].replace(",", "")) if row[col] not in ("", "-", "--") else 0)
            if label == "TOTAL":
                magnitude[province] = count
            elif count:
                won[province][label] = count
    for province, parties in won.items():
        if sum(parties.values()) != magnitude[province]:
            raise ValueError(f"province {province}: {parties} does not add up to {magnitude[province]}")
    return Seats(magnitude, won)


@dataclass(frozen=True)
class Elected:
    """A person a declaratoria names as elected."""

    name: str
    role: str  # "head", "running_mate", or "list_member"
    place: int  # the list position for a vice president (1 or 2), or the province (1 to 7) for a deputy
    party: str | None = None  # the party's name as the workbooks print it, for a deputy


ROLES = {
    "PRESIDENTE": ("head", 0),
    "PRESIDENTA": ("head", 0),
    "PRIMER VICEPRESIDENTE": ("running_mate", 1),
    "PRIMERA VICEPRESIDENTA": ("running_mate", 1),
    "SEGUNDO VICEPRESIDENTE": ("running_mate", 2),
    "SEGUNDA VICEPRESIDENTA": ("running_mate", 2),
}
TICKET = re.compile(
    r"\b(?:a\s+la\s+señora|al\s+señor|a\s+la|al|a)\s+([A-ZÁÉÍÓÚÑ][^,;]+?)(?:,\s*conocid[oa]\s+como\s+[^,;]+?)?,\s*portador(?:a)?\s+de\s+la\s+cédula\s+de\s+identidad\s+número\s+\d[\d\s-]*?\s*,?\s+"
    r"(PRESIDENT[EA]|PRIM(?:ER|ERA)\s+VICEPRESIDENT[EA]|SEGUND[OA]\s+VICEPRESIDENT[EA])"
)


def ticket(resolution: Resolution) -> tuple[list[Elected], str | None]:
    """The president and vice presidents a declaratoria declares elected, and the party it says won."""
    text = squash(plain(resolution))
    after = text[text.rindex("POR TANTO") :]
    found = []
    for match in TICKET.finditer(after):
        role, place = ROLES[" ".join(match.group(2).split())]
        found.append(Elected(" ".join(match.group(1).split()), role, place))
    if not found:
        if resolution.round == 2:
            raise ValueError(f"{resolution.number}: a second-round declaratoria names no elected ticket")
        return [], None
    if sorted(e.place for e in found) != [0, 1, 2]:
        raise ValueError(f"{resolution.number}: found {found}")
    winner = re.search(r"el\s+partido\s+(?:político\s+)?que\s+obtuvo\s+la\s+mayoría.*?fue\s+([^.,]+?)\s*[.,]", text)
    if winner is None:
        raise ValueError(f"{resolution.number}: names a ticket but not the party that won")
    return found, fold(winner.group(1))


def advancing(resolution: Resolution) -> list[str]:
    """The parties whose tickets the declaratoria sends to a second round."""
    text = squash(plain(resolution))
    match = re.search(r"entre\s+las\s+nóminas\s+de\s+los\s+partidos\s+políticos\s+(.+?)\s*,?\s+por\s+ser", text)
    if match is None:
        match = re.search(
            r"participar[aá]n\s+únicamente\s+las\s+nóminas\s+correspondientes\s+a\s+los\s+partidos\s+(.+?)\s*\.", text
        )
    if match is None:
        raise ValueError(f"{resolution.number}: no second round")
    return [fold(part) for part in re.split(r"\s+y\s+|,\s*", match.group(1))]


def deputies_pdf(resolution: Resolution) -> list[Elected]:
    """The deputies a declaratoria in a layout-preserved PDF lists under 'POR TANTO', province by province."""
    text = plain(resolution)
    after = text[text.rindex("POR TANTO") :]
    found: list[Elected] = []
    province = 0
    for line in after.splitlines():
        heading = re.match(r"\s*PROVINCIA DE\s+(.+?)\s*$", line)
        if heading:
            province = PROVINCES.index(fold(heading.group(1))) + 1
            continue
        row = re.match(r"\s*(\d+)\s+(\d{9})\s+(.+?)\s{2,}([A-Z]+)\s*$", line)
        if row and province:
            found.append(Elected(squash(row.group(3)), "list_member", province, PARTIES[row.group(4)]))
    return found


def deputies_html(resolution: Resolution) -> list[Elected]:
    """The deputies a declaratoria in a Word-exported page lists in one table per province."""
    path = raw.cached(SOURCE, url(resolution)).path
    page = decode(path.read_bytes())
    found: list[Elected] = []
    province, pending = 0, False
    for match in re.finditer(r"<tr.*?</tr>|Provincia:\s*[^<]+", page, flags=re.DOTALL | re.IGNORECASE):
        row = match.group(0)
        if not row.lower().startswith("<tr"):
            row = f"<td>{row}</td>"  # a heading printed as a paragraph above the province's table
        cells = [
            squash(html.unescape(re.sub(r"<[^>]+>", " ", cell)))
            for cell in re.findall(r"<td.*?</td>", row, flags=re.DOTALL | re.IGNORECASE)
        ]
        cells = [cell for cell in cells if cell]
        if cells and cells[0].startswith("Provincia:"):
            named = " ".join([cells[0].removeprefix("Provincia:"), *cells[1:2]])
            pending = fold(named) not in PROVINCES
            province = 0 if pending else PROVINCES.index(fold(named)) + 1
        elif pending and len(cells) == 1 and fold(cells[0]) in PROVINCES:
            province, pending = PROVINCES.index(fold(cells[0])) + 1, False
        elif len(cells) >= 4 and cells[0].isdigit() and re.fullmatch(r"\d{9}", cells[1]) and province:
            name = re.split(r"\s+C\.C\.\s+", cells[2])[0]
            found.append(Elected(squash(name), "list_member", province, PARTIES[cells[-1]]))
    return found
