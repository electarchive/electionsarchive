import functools
import json
import re
import subprocess
import unicodedata
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import NamedTuple
from uuid import uuid5

import httpx
import shapely

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "ar.pba"
BASE = "https://www.juntaelectoral.gba.gov.ar"
ARGENTINA = str(uuid5(package.NAMESPACE, "polity\x1fAR"))

# Election days. The Junta Electoral's files name only the year, so each day is a curated constant, and the
# `ar.dine` packages that already hold the national elections of the same days agree with them where they overlap.
GENERAL_DAYS = {
    2003: date(2003, 9, 14),
    2005: date(2005, 10, 23),
    2007: date(2007, 10, 28),
    2009: date(2009, 6, 28),
    2011: date(2011, 10, 23),
    2013: date(2013, 10, 27),
    2015: date(2015, 10, 25),
    2017: date(2017, 10, 22),
    2019: date(2019, 10, 27),
    2021: date(2021, 11, 14),
    2023: date(2023, 10, 22),
    2025: date(2025, 9, 7),
}
PRIMARY_DAYS = {
    2011: date(2011, 8, 14),
    2013: date(2013, 8, 11),
    2015: date(2015, 8, 9),
    2017: date(2017, 8, 13),
    2019: date(2019, 8, 11),
    2021: date(2021, 9, 12),
}
# The province page lists a province-wide total only for the years with a governor's race.
TOTAL_YEARS = {"g": (2003, 2007, 2011, 2015, 2019, 2023), "e": (2011, 2015, 2019)}
KINDS = ("g", "e")
CARGOS = {"04": "governor", "06": "senate", "08": "deputies", "10": "mayor", "11": "council"}
HEADERS = (
    (re.compile(r"gobernador"), "04"),
    (re.compile(r"senador"), "06"),
    (re.compile(r"diputado"), "08"),
    (re.compile(r"intendente"), "10"),
    (re.compile(r"concejal"), "11"),
)
BLANK = 9996
NULLS = (9997, 9999)


def page(kind: str) -> str:
    return "distritoEstadisticasHistoricas" + ("EPAOS" if kind == "e" else "")


def listing_url(kind: str, did: str) -> str:
    return f"{BASE}/distritosHistorico{'EPAOS' if kind == 'e' else ''}.php?distrito={did}"


def home_url(kind: str) -> str:
    return f"{BASE}/mapa-estadisticas-historicas{'-epaos' if kind == 'e' else ''}.php"


def html_url(kind: str, year: int, did: str) -> str:
    return f"{BASE}/{page(kind)}.php?anio={year}&did={did}"


def csv_url(kind: str, year: int, did: str) -> str:
    """The CSV download is a form POST, so the cache keys it by the form's fields after a fragment."""
    return f"{BASE}/{page(kind)}Acsv.php#anio={year}&did={did}"


def province_url(kind: str, year: int) -> str:
    return f"{BASE}/provinciaEstadisticasHistoricas{'EPAOS' if kind == 'e' else ''}.php?anio={year}"


def province_csv_url(kind: str, year: int) -> str:
    return f"{BASE}/provinciaEstadisticasHistoricas{'EPAOS' if kind == 'e' else ''}Acsv.php#anio={year}"


def results_home_url() -> str:
    return f"{BASE}/mapa-provincia-bsas.php"


def results_listing_url(did: str) -> str:
    """The modal the province map opens for a partido, which links the Junta's definitive-count PDF of each year."""
    return f"{BASE}/distritos.php?distrito={did}"


def pdf_url(year: int, did: str) -> str:
    return f"{BASE}/resultados/{year}{did}.pdf"


def pdf_years(did: str) -> list[int]:
    """The general-election years whose definitive-count PDF the partido's listing links. Earlier years are not
    in the statistics the packages already hold."""
    text = raw.cached(SOURCE, results_listing_url(did)).path.read_text(encoding="utf-8", errors="replace")
    found = {int(y) for y in re.findall(rf"resultados/(\d{{4}}){did}\.pdf", text)}
    return sorted(found & GENERAL_DAYS.keys())


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=120)


def _post(client: httpx.Client, url: str) -> bytes:
    target, _, form = url.partition("#")
    fields = dict(pair.split("=", 1) for pair in form.split("&"))
    for attempt in range(4):
        try:
            response = client.post(target, data=fields)
            response.raise_for_status()
        except httpx.HTTPError:
            if attempt == 3:
                raise
            continue
        return response.content
    raise AssertionError(url)


def districts() -> list[str]:
    """The partido codes the map page lists, which are the Junta's own three-digit codes."""
    found: set[str] = set()
    for kind in KINDS:
        text = raw.cached(SOURCE, home_url(kind)).path.read_text(encoding="utf-8", errors="replace")
        found |= set(re.findall(r"distrito=(\d{3})", text))
    found.discard("000")
    return sorted(found)


def years(kind: str, did: str) -> list[int]:
    text = raw.cached(SOURCE, listing_url(kind, did)).path.read_text(encoding="utf-8", errors="replace")
    return sorted({int(y) for y in re.findall(r"anio=(\d{4})", text)})


def fetch() -> None:
    fetch_shapes()
    raw.fetch_many(SOURCE, [home_url(kind) for kind in KINDS], suffix=".html", workers=2)
    codes = districts()
    raw.fetch_many(SOURCE, [listing_url(kind, did) for kind in KINDS for did in codes], suffix=".html", workers=8)
    held = [(kind, year, did) for kind in KINDS for did in codes for year in years(kind, did)]
    raw.fetch_many(SOURCE, [html_url(*item) for item in held], suffix=".html", workers=8)
    totals = [(kind, year) for kind in KINDS for year in TOTAL_YEARS[kind]]
    raw.fetch_many(SOURCE, [province_url(*item) for item in totals], suffix=".html", workers=4)
    raw.fetch_many(SOURCE, [results_home_url()], suffix=".html", workers=1)
    raw.fetch_many(SOURCE, [results_listing_url(did) for did in codes], suffix=".html", workers=8)
    raw.fetch_many(SOURCE, [pdf_url(year, did) for did in codes for year in pdf_years(did)], suffix=".pdf", workers=8)
    wanted = [csv_url(*item) for item in held] + [province_csv_url(*item) for item in totals]
    have = raw.keys(SOURCE)
    pending = [url for url in wanted if url not in have]
    with _client() as client, ThreadPoolExecutor(8) as pool:
        for url, content in zip(pending, pool.map(lambda url: _post(client, url), pending), strict=True):
            raw.store(SOURCE, url, content, suffix=".csv", retrieved_at=datetime.now(UTC), media_type="text/csv")


class Board(NamedTuple):
    """One partido's school board in one general election, from the Junta's definitive-count PDF."""

    seats: int
    quotient: float
    table: list[tuple[int, str, int]]
    figures: dict[str, int]
    won: list[tuple[str, int]]
    council: int | None = None
    council_won: tuple[tuple[str, int], ...] = ()


SEATS = re.compile(
    r"^\s*(?:Correspond[ae]:? a:?\s*)?(?P<name>\S.*?)(?:\s+(?P<council>\d+)\s+Concejal(?:es|e)?)?"
    r"\s+(?P<board>\d+),?\s+Consejeros?\s*$",
    re.IGNORECASE,
)
COUNCIL_SEATS = re.compile(
    r"^\s*(?:Correspond[ae]:?\s*(?:a:?\s+)?)?(?P<name>\S.*?)\s+(?P<seats>\d+)\s+Concejal(?:es?)?"
    r"(?:\s+\d+,?\s+Consejeros?)?\s*$",
    re.IGNORECASE,
)
LIST_LINE = re.compile(r"^\s*(?P<number>\d\.\d{3}|\d{1,4})(?:\s*-\s*(?=\S)|\s*(?=[^\W\d_]))(?P<rest>.+)$")
FOOTER = {
    "positive": re.compile(r"^\s*(?:TOTAL )?VOTOS? POSITIVOS?|^\s*TOTAL VOTOS VALIDOS", re.IGNORECASE),
    "blank": re.compile(r"^\s*(?:VOTOS? EN )?BLANCOS?\b", re.IGNORECASE),
    "null": re.compile(r"^\s*(?:VOTOS? )?(?:NULOS?|ANULADOS)\b", re.IGNORECASE),
    "total": re.compile(r"^\s*TOTAL DE VOTOS|^\s*TOTAL VOTOS EMITIDOS", re.IGNORECASE),
    "registered": re.compile(r"^\s*TOTAL DE (?:ELECTORES|INSCRIPTOS)", re.IGNORECASE),
}
FIGURE = re.compile(r"(?<![\w.,])\d{1,3}(?:[.,]\d{3})*(?![\w.,])|(?<![\w.,])\d+(?![\w.,])")
# The Junta's short names in a PDF's seat table that match no list name closely enough.
SEAT_ALIASES: dict[tuple[int, str, str], str] = {
    (2003, "028", ".Mov. para la Victoria de Cnel.Suarez"): "AGRUPACION MOVIMIENTO PARA LA VICTORIA DE CORONEL SUAREZ",
    (2003, "035", "Acción Federalista por Buenos Aires"): "ACCION FEDER. POR BS. AS.",
    (2003, "024", "Agrup.Intransigencia Colonense"): "AGRUPACION MUNICIPAL DE LA INTRANSIGENCIA",
    (2003, "052", "Agrup. Vecinal José Hernández"): "GENERAL SAN MARTIN, AGRUPACION VECINAL JOSE HERNANDEZ",
    (2003, "107", "Agrup. Municipal del Acuerdo"): "SAN NICOLAS, AGRUPACION MUNICIPAL DEL ACUERDO",
}


def _count(text: str) -> float:
    return float(text.replace(".", "").replace(",", ".")) if "," in text else float(text.replace(".", ""))


def _row_tail(tail: list[str]) -> bool:
    """Whether the trailing numbers of a list row are its votes and, after them, at most its percent."""
    if not re.fullmatch(r"\d{1,3}([.,]\d{3})*|\d+", tail[0]) or len(tail) > 2:
        return False
    return len(tail) == 1 or bool(re.fullmatch(r"\d{1,3}([,.]\d{1,2})?", tail[1]))


def _table(lines: list[str]) -> tuple[list[tuple[int, str, int]], dict[str, int]]:
    """The list table and the accounting lines of a PDF's text. A long name wraps onto the next line, which
    carries no list number, and a wrapped row may put its percent there."""
    rows: list[tuple[int, str, int]] = []
    figures: dict[str, int] = {}
    started = False
    for line in lines:
        if re.match(r"^\s*(LISTAS?|Lista)\s+(VOTOS|Votos)", line):
            started = True
            continue
        if not started:
            continue
        label = next((name for name, pattern in FOOTER.items() if pattern.match(line)), None)
        if label is not None:
            found = FIGURE.findall(re.sub(r"^\D*", "", line.replace("2 %", "")))
            if found and label not in figures:
                figures[label] = int(re.sub(r"[.,]", "", found[0]))
            continue
        if figures:
            continue
        match = LIST_LINE.match(re.sub(r"(\d), (\d)", r"\1,\2", line))
        if match is None:
            continue
        tokens = re.sub(r"\s+", " ", match["rest"]).strip().split(" ")
        tail: list[str] = []
        while tokens and re.fullmatch(r"[\d.,]+", tokens[-1]):
            tail.insert(0, tokens.pop())
        k = next((k for k in range(len(tail)) if _row_tail(tail[k:])), None)
        if k is None or not tokens:
            continue
        rows.append(
            (int(match["number"].replace(".", "")), " ".join([*tokens, *tail[:k]]), int(re.sub(r"[.,]", "", tail[k])))
        )
    return rows, figures


@functools.cache
def board(year: int, did: str) -> Board:
    path = raw.cached(SOURCE, pdf_url(year, did)).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    elige = re.search(r"ELIGE:[^\n]*?(\d+),?\s+Consejeros\s+Escolares", text, re.IGNORECASE)
    quotient = re.search(
        r"COCIENTE\s+CONSEJEROS(?:\s+ESCOLARES)?(?:\s*\([^)]*\)|\s*\*)?\s+([\d.,]+)", text, re.IGNORECASE
    )
    if elige is None or quotient is None:
        raise ValueError(f"{year} {did}: no school board seats or quotient in {path.name}")
    lines = text.splitlines()
    table, figures = _table(lines)
    won: list[tuple[str, int]] = []
    for line in lines:
        found = SEATS.match(line)
        if found is None or "Concejales y" in line:
            continue
        name = re.sub(r"\s+", " ", found["name"]).strip()
        if int(found["board"]):
            won.append((name, int(found["board"])))
    seats = int(elige[1])
    council = _council_seats(text, lines)
    positive = figures.get("positive")
    divisor = (positive or 0) / _count(quotient[1])
    if abs(divisor - round(divisor)) > 0.02:
        divisor = sum(votes for _, _, votes in table) / _count(quotient[1])
    problems = [
        (sum(seats for _, seats in won) != seats, f"{won} do not fill {seats} seats"),
        (
            divisor < seats - 0.02 or abs(divisor - round(divisor)) > 0.02,
            f"quotient {quotient[1]} divides {positive} {divisor:.3f} times",
        ),
    ]
    for bad, message in problems:
        if bad:
            raise ValueError(f"{year} {did}: {message}")
    return Board(seats, _count(quotient[1]), table, figures, won, *council)


def _council_seats(text: str, lines: list[str]) -> tuple[int | None, tuple[tuple[str, int], ...]]:
    """The councillors a partido elects and the seats each list won, when the PDF prints both and they agree."""
    elige = re.search(r"ELIGE:[^\n]*?(\d+),?\s+Concejal(?:es)?\b", text, re.IGNORECASE)
    won: list[tuple[str, int]] = []
    for line in lines:
        found = COUNCIL_SEATS.match(line)
        if found is None or "Concejales y" in line:
            continue
        if int(found["seats"]):
            won.append((re.sub(r"\s+", " ", found["name"]).strip(), int(found["seats"])))
    if elige is None or sum(seats for _, seats in won) != int(elige[1]):
        return None, ()
    return int(elige[1]), tuple(won)


def match_list(name: str, table: list[tuple[int, str, int]], year: int, did: str) -> int:
    """The index of the table row a seat table's short name stands for."""
    name = SEAT_ALIASES.get((year, did, name), name)
    target = normal(name)
    names = [normal(row[1]) for row in table]
    exact = [i for i, other in enumerate(names) if other == target]
    if len(exact) == 1:
        return exact[0]
    close = [i for i, row in enumerate(table) if _similar(name, row[1])]
    if not close:
        close = [i for i, other in enumerate(names) if f" {target} " in f" {other} "]
    if len(close) > 1:
        close.sort(key=lambda i: -SequenceMatcher(None, target, names[i]).ratio())
        if SequenceMatcher(None, target, names[close[0]]).ratio() > 0.95:
            close = close[:1]
    if len(close) != 1:
        raise ValueError(f"{year} {did}: {name!r} matches {[table[i][1] for i in close]}")
    return close[0]


class Row(NamedTuple):
    cargo: str
    lista: int
    name: str
    line: int | None
    line_name: str | None
    votes: int


class Held(NamedTuple):
    """One partido's published results in one election: the CSV's rows plus the page's accounting."""

    section: int
    registered: int | None
    tables: int | None
    rows: list[Row]
    accounting: dict[str, dict[str, int | None]]


def number(text: str) -> int | None:
    text = text.strip().replace("\xa0", "")
    if text in ("", "-"):
        return None
    if not re.fullmatch(r"\d{1,3}(\.\d{3})*|\d+", text):
        raise ValueError(f"not a count: {text!r}")
    return int(text.replace(".", ""))


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp850" if b"\xa5" in data else "cp1252")


def normal(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.upper()).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", " ", folded).strip()


def clean(text: str) -> str:
    """A list's name as printed, with the Junta's mojibake for Ñ (a yen sign) and a stray lowercase accent undone."""
    text = text.replace("¥", "Ñ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.upper() if any(c in text for c in "óúáíéñ") else text


def csv_rows(kind: str, year: int, did: str) -> tuple[int, list[Row]]:
    text = decode(raw.cached(SOURCE, csv_url(kind, year, did)).path.read_bytes())
    lines = text.splitlines()
    header = lines[0].split(";")
    width = len(header)
    if header[:7] != [
        "eleccion",
        "provincia_id",
        "seccion_id",
        "distrito_id",
        "distrito",
        "lista_id",
        "lista",
    ] or width not in (10, 12):
        raise ValueError(f"unexpected CSV header {lines[0]!r}")
    shift = width - 10
    out: list[Row] = []
    sections: set[int] = set()
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split(";")
        if len(cells) != width:
            raise ValueError(f"{kind} {year} {did}: {len(cells)} fields in {line!r}")
        if int(cells[0]) != year or cells[3] != did:
            raise ValueError(f"{kind} {year} {did}: row for {cells[0]} {cells[3]}")
        sections.add(int(cells[2]))
        cargo = cells[7 + shift]
        if cargo not in CARGOS:
            raise ValueError(f"{kind} {year} {did}: unknown cargo {cargo!r}")
        votes = number(cells[9 + shift])
        if votes is None:
            raise ValueError(f"{kind} {year} {did}: no votes in {line!r}")
        out.append(
            Row(
                cargo,
                int(cells[5]),
                clean(cells[6]),
                (int(cells[7]) if cells[7] else None) if shift else None,
                clean(cells[8]) if shift else None,
                votes,
            )
        )
    if len(sections) != 1:
        raise ValueError(f"{kind} {year} {did}: sections {sorted(sections)}")
    return sections.pop(), out


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th") and self.rows:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self.rows:
            self.rows[-1].append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._cell is not None:
            self._cell.append(" " if name == "nbsp" else "")


FOOTERS = {
    "positive": re.compile(r"(total )?votos positivos"),
    "blank": re.compile(r"votos? en blanco"),
    "null": re.compile(r"votos nulos"),
    "total": re.compile(r"total de votos"),
}


def accounting(path_text: str) -> tuple[int | None, int | None, dict[str, dict[str, int | None]]]:
    """The page's registered voters, tables, and per-column positive, blank, null, and total votes."""
    parser = _Rows()
    parser.feed(path_text)
    registered = tables = None
    columns: list[str] = []
    out: dict[str, dict[str, int | None]] = {}
    for cells in parser.rows:
        first = cells[0].lower() if cells else ""
        if "electores habilitados" in cells[0:1] or first == "electores habilitados":
            registered = number(cells[1])
            tables = number(cells[3]) if len(cells) > 3 else None
        elif "partido politico" in first or (len(cells) > 1 and "partido politico" in cells[1].lower()):
            heads = [c for c in cells if c and c != "%" and c.lower() not in ("partido politico", "linea interna")]
            columns = []
            for head in heads:
                for pattern, cargo in HEADERS:
                    if pattern.search(head.lower()):
                        columns.append(cargo)
                        break
                else:
                    raise ValueError(f"unknown column {head!r}")
        else:
            label = next((name for name, pattern in FOOTERS.items() if pattern.fullmatch(first)), None)
            if label is None:
                continue
            values = [c for c in cells[1:] if c != ""]
            counts = values[0::2]
            if len(counts) != len(columns):
                raise ValueError(f"{label} row has {len(counts)} counts for columns {columns}")
            for cargo, text in zip(columns, counts, strict=True):
                out.setdefault(cargo, {})[label] = number(text)
    return registered, tables, out


def held(kind: str, year: int, did: str) -> Held:
    section, rows = csv_rows(kind, year, did)
    text = decode(raw.cached(SOURCE, html_url(kind, year, did)).path.read_bytes())
    registered, tables, figures = accounting(text)
    return Held(section, registered, tables, rows, figures)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


PROVINCE = key("area", "ar-b.provincia", "B")
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fARG"))
JUNTA = key("source", "junta")
ESCRUTINIO = key("source", "junta-escrutinio")
SHAPES = key("source", "geoboundaries")
SECTION_NAMES = {
    1: "Primera",
    2: "Segunda",
    3: "Tercera",
    4: "Cuarta",
    5: "Quinta",
    6: "Sexta",
    7: "Séptima",
    8: "Octava",
}
BODIES = {
    "senate": ("upper_house", "Senate of Buenos Aires Province", "Senado de la Provincia de Buenos Aires"),
    "deputies": (
        "lower_house",
        "Chamber of Deputies of Buenos Aires Province",
        "Cámara de Diputados de la Provincia de Buenos Aires",
    ),
    "council": (
        "council",
        "Municipal councils of Buenos Aires Province",
        "Concejos Deliberantes de la Provincia de Buenos Aires",
    ),
    "school_board": (
        "council",
        "School boards (consejos escolares) of Buenos Aires Province",
        "Consejos Escolares de la Provincia de Buenos Aires",
    ),
}
OFFICES = {
    "governor": ("governor", "Governor of Buenos Aires Province", "Gobernador de la Provincia de Buenos Aires"),
    "mayor": (
        "mayor",
        "Mayor (intendente) of a Buenos Aires partido",
        "Intendente de un partido de la Provincia de Buenos Aires",
    ),
}
_PARTIDOS = [
    "Adolfo Alsina",
    "Alberti",
    "Almirante Brown",
    "Avellaneda",
    "Ayacucho",
    "Azul",
    "Bahía Blanca",
    "Balcarce",
    "Baradero",
    "Arrecifes",
    "Berazategui",
    "Berisso",
    "Bolívar",
    "Bragado",
    "Brandsen",
    "Campana",
    "Cañuelas",
    "Capitán Sarmiento",
    "Carlos Casares",
    "Carlos Tejedor",
    "Carmen de Areco",
    "Patagones",
    "Castelli",
    "Colón",
    "Coronel Dorrego",
    "Coronel Pringles",
    "Coronel Rosales",
    "Coronel Suárez",
    "Chacabuco",
    "Chascomús",
    "Chivilcoy",
    "Daireaux",
    "Dolores",
    "Ensenada",
    "Escobar",
    "Esteban Echeverría",
    "Exaltación de la Cruz",
    "Florencio Varela",
    "General Alvarado",
    "General Alvear",
    "General Arenales",
    "General Belgrano",
    "General Guido",
    "General La Madrid",
    "General Las Heras",
    "General Lavalle",
    "General Madariaga",
    "General Paz",
    "General Pinto",
    "General Pueyrredón",
    "General Rodríguez",
    "General San Martín",
    "San Miguel",
    "General Viamonte",
    "General Villegas",
    "Adolfo Gonzales Chaves",
    "Guaminí",
    "Hipólito Yrigoyen",
    "Benito Juárez",
    "Junín",
    "La Matanza",
    "Lanús",
    "La Plata",
    "Laprida",
    "Las Flores",
    "Leandro N. Alem",
    "Lincoln",
    "Lobería",
    "Lobos",
    "Lomas de Zamora",
    "Luján",
    "Magdalena",
    "Maipú",
    "Mar Chiquita",
    "Marcos Paz",
    "Mercedes",
    "Merlo",
    "Monte",
    "Moreno",
    "Morón",
    "Navarro",
    "Necochea",
    "Nueve de Julio",
    "Olavarría",
    "Pehuajó",
    "Pellegrini",
    "Pergamino",
    "Pila",
    "Pilar",
    "Pinamar",
    "Puán",
    "Quilmes",
    "Ramallo",
    "Rauch",
    "Rivadavia",
    "Rojas",
    "Roque Pérez",
    "Saavedra",
    "Saladillo",
    "Salliqueló",
    "Salto",
    "San Andrés de Giles",
    "San Antonio de Areco",
    "San Cayetano",
    "San Fernando",
    "San Isidro",
    "San Nicolás",
    "San Pedro",
    "San Vicente",
    "Suipacha",
    "Tandil",
    "Tapalqué",
    "Tigre",
    "Tornquist",
    "Trenque Lauquen",
    "Tordillo",
    "Tres Arroyos",
    "Tres de Febrero",
    "La Costa",
    "Monte Hermoso",
    "Veinticinco de Mayo",
    "Vicente López",
    "Villa Gesell",
    "Villarino",
    "Zárate",
    "Tres Lomas",
    "Florentino Ameghino",
    "Presidente Perón",
    "José C. Paz",
    "Malvinas Argentinas",
    "Punta Indio",
    "Ezeiza",
    "Ituzaingó",
    "Hurlingham",
    "Lezama",
]
NAMES: dict[str, str] = {f"{number:03d}": name for number, name in enumerate(_PARTIDOS, start=1)}
# geoBoundaries spells a few partidos differently from the Junta.
SHAPE_NAMES = {
    "027": "Coronel de Marina L. Rosales",
    "047": "General Juan Madariaga",
    "083": "9 de Julio",
    "121": "25 de Mayo",
}
# A box around the province, which holds no department of another province that shares a partido's name.
BOX = (-63.5, -41.1, -56.6, -33.2)
SHAPE_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ARG/ADM2/"
    "geoBoundaries-ARG-ADM2_simplified.geojson"
)
SHAPE_SOURCE = "ar.geoboundaries"


def fetch_shapes() -> None:
    raw.fetch(SHAPE_SOURCE, SHAPE_URL, suffix=".geojson")


def partido_shapes() -> dict[str, shapely.Geometry]:
    features = json.loads(raw.cached(SHAPE_SOURCE, SHAPE_URL).path.read_text(encoding="utf-8"))["features"]
    inside: dict[str, list[shapely.Geometry]] = {}
    for feature in features:
        shape = shapely.geometry.shape(feature["geometry"])
        point = shape.representative_point()
        if BOX[0] <= point.x <= BOX[2] and BOX[1] <= point.y <= BOX[3]:
            inside.setdefault(normal(feature["properties"]["shapeName"]), []).append(shape)
    out: dict[str, shapely.Geometry] = {}
    for did, name in NAMES.items():
        found = inside.get(normal(SHAPE_NAMES.get(did, name)), [])
        if len(found) != 1:
            raise ValueError(f"{len(found)} geoBoundaries shapes for {did} {name}")
        out[did] = found[0]
    return out


def _multi(shape: shapely.Geometry) -> shapely.Geometry:
    shape = shapely.make_valid(shape)
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def _outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    shape = _multi(shape)
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=SHAPES,
    )


@functools.cache
def sections() -> dict[str, int]:
    """Each partido's electoral section, which the CSVs give with every row and which never changes."""
    found: dict[str, int] = {}
    for data in everything().values():
        for did, held_ in data.items():
            if found.setdefault(did, held_.section) != held_.section:
                raise ValueError(f"{did} is in sections {found[did]} and {held_.section}")
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Buenos Aires province, its eight electoral sections, and its 135 partidos, with outlines from geoBoundaries",
    )
    p.external |= {ARGENTINA, COUNTRY}
    shapes = raw.cached(SHAPE_SOURCE, SHAPE_URL)
    p.add(
        "source",
        id=SHAPES,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Argentina ADM2 (departments and partidos, simplified, 2020), derived from the "
        "Instituto Geográfico Nacional",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="geoBoundaries (William & Mary geoLab); original data from the Instituto Geográfico Nacional "
        "and UNHCR/OCHA ROLAC",
    )
    p.artifact(shapes, SHAPES)
    for code, rank, noun in (
        ("ar-b.provincia", 1, "province"),
        ("ar-b.seccion", 2, "electoral section"),
        ("ar-b.partido", 3, "partido"),
    ):
        p.add("scheme", code=code, polity_id=ARGENTINA, kind="administrative", rank=rank, noun=noun, code_system=None)
        p.add(
            "boundary_set",
            id=key("boundary_set", code, 2020),
            scheme_code=code,
            vintage="2020",
            as_of=date(2020, 1, 1),
        )
    outlines = partido_shapes()
    section_of = sections()
    p.add("area", id=PROVINCE, scheme_code="ar-b.provincia")
    p.add("area_code", area_id=PROVINCE, code="B")
    p.add("area_parent", area_id=PROVINCE, parent_id=COUNTRY)
    p.name(PROVINCE, "Provincia de Buenos Aires", "es", "official")
    p.name(PROVINCE, "Buenos Aires Province", "en", "english", False)
    p.add(
        "note",
        id=key("note", PROVINCE, "shapes"),
        entity_id=PROVINCE,
        kind="caveat",
        body_md="Outlines are geoBoundaries' 2020 partido boundaries, used for every year. Lezama (partido 135) was "
        "created from Chascomús in 2009, so the Junta's earlier results for Chascomús cover the area both partidos "
        "now occupy.",
        lang="en",
    )
    _outline(p, PROVINCE, shapely.union_all(list(outlines.values())))
    for number, name in SECTION_NAMES.items():
        id = area("ar-b.seccion", str(number))
        p.add("area", id=id, scheme_code="ar-b.seccion")
        p.add("area_code", area_id=id, code=str(number))
        p.add("area_parent", area_id=id, parent_id=PROVINCE)
        p.name(id, f"{name} Sección Electoral", "es", "official")
        _outline(p, id, shapely.union_all([outlines[did] for did, n in section_of.items() if n == number]))
    for did, name in NAMES.items():
        id = area("ar-b.partido", did)
        p.add("area", id=id, scheme_code="ar-b.partido")
        p.add("area_code", area_id=id, code=did)
        p.add("area_parent", area_id=id, parent_id=area("ar-b.seccion", str(section_of[did])))
        p.name(id, name, "es", "official")
        _outline(p, id, outlines[did])
    return p


def _similar(a: str, b: str) -> bool:
    drop = {"ALIANZA", "AZA", "PARTIDO", "PDO"}
    a, b = (" ".join(word for word in normal(x).split() if word not in drop) for x in (a, b))
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return (len(short) >= 8 and long.startswith(short)) or SequenceMatcher(None, a, b).ratio() >= 0.8


class Party(NamedTuple):
    id: str
    name: str


@functools.cache
def parties() -> dict[tuple[int, str, int | None], Party]:
    """Each list name the Junta printed, keyed by its number, normalized name, and (for a blank name) year.

    The Junta reuses a number for different alliances from one election to the next (131 to 138 and 500 and up),
    and spells the same party's name differently from year to year, so a number's names join one party only when
    they are close to each other, and a name that is not close starts a new party under the same number. A row
    whose name is blank takes the party its number names in that year, or in the nearest year that names one."""
    seen: dict[int, Counter[tuple[int, str]]] = {}
    blank: set[tuple[int, int]] = set()
    for (_, year), data in everything().items():
        for held_ in data.values():
            for row in held_.rows:
                if row.lista >= BLANK:
                    continue
                if row.name:
                    seen.setdefault(row.lista, Counter())[(year, row.name)] += 1
                else:
                    blank.add((row.lista, year))
    out: dict[tuple[int, str, int | None], Party] = {}
    for lista, names in sorted(seen.items()):
        clusters: list[list[tuple[int, str]]] = []
        for year, name in sorted(names):
            for cluster in clusters:
                if any(_similar(name, other) for _, other in cluster):
                    cluster.append((year, name))
                    break
            else:
                clusters.append([(year, name)])
        made: list[tuple[list[tuple[int, str]], Party]] = []
        for cluster in clusters:
            counted = Counter({name: names[(year, name)] for year, name in cluster})
            best = max(counted, key=lambda name: (counted[name], len(name)))
            first_year, first = cluster[0]
            made.append((cluster, Party(key("party", lista, first_year, normal(first)), best)))
            for _, name in cluster:
                out[(lista, normal(name), None)] = made[-1][1]
        for number, year in sorted(blank):
            if number == lista:
                nearest = min((abs(y - year), index) for index, (cluster, _) in enumerate(made) for y, _ in cluster)
                out[(lista, "", year)] = made[nearest[1]][1]
    for (lista, name), party_ in sorted(_unlisted(seen, out).items()):
        out[(lista, normal(name), None)] = party_
    for lista, year in blank:
        if (lista, "", year) not in out:
            raise ValueError(f"list {lista} is unnamed in {year} and has no named year")
    return out


def _unlisted(
    seen: dict[int, Counter[tuple[int, str]]], out: dict[tuple[int, str, int | None], Party]
) -> dict[tuple[int, str], Party]:
    """The parties of lists that a definitive-count PDF's table prints with votes but the statistics file of the
    same partido and year leaves out. A list joins a party the statistics files name under its number when the
    names are close, and otherwise starts one."""
    found: dict[tuple[int, str], Party] = {}
    for did in districts():
        for year in pdf_years(did):
            have = {row.lista for row in everything()[("g", year)][did].rows}
            for lista, name, votes in board(year, did).table:
                if lista in have or not votes or (lista, name) in found:
                    continue
                known = [party_ for (number, _, _), party_ in out.items() if number == lista]
                near = next((p_ for p_ in known if _similar(name, p_.name)), None)
                found[(lista, name)] = near or Party(key("party", lista, year, normal(name)), name)
    return found


@functools.cache
def everything() -> dict[tuple[str, int], dict[str, Held]]:
    out: dict[tuple[str, int], dict[str, Held]] = {}
    for kind in KINDS:
        for did in districts():
            for year in years(kind, did):
                out.setdefault((kind, year), {})[did] = held(kind, year, did)
    return out


def party(row: Row, year: int) -> Party:
    return parties()[(row.lista, "", year) if not row.name else (row.lista, normal(row.name), None)]


def election_key(kind: str, year: int) -> str:
    return f"{year}-{'general' if kind == 'g' else 'paso'}"


def packages() -> list[str]:
    ordered = sorted(everything(), key=lambda item: (item[1], item[0]))
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{election_key(*item)}" for item in ordered)]


def parse() -> None:
    geo().write()
    registry().write()
    for kind, year in sorted(everything(), key=lambda item: (item[1], item[0])):
        election(kind, year).write()


def emit() -> None:
    package.run("load", packages())


def body(name: str) -> str:
    return key("body", name)


def office(name: str) -> str:
    return key("office", name)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties, legislative bodies, and offices in the Junta Electoral of Buenos Aires province's historical "
        "results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ARGENTINA}
    p.add(
        "source",
        id=JUNTA,
        kind="official_body",
        publisher="Junta Electoral de la Provincia de Buenos Aires",
        title="Estadísticas históricas: elecciones generales y EPAOS por partido",
        url=f"{BASE}/mapa-estadisticas-historicas.php",
        license="Official public record, no license stated",
        license_url=f"{BASE}/mapa-estadisticas-historicas.php",
        attribution="Junta Electoral de la Provincia de Buenos Aires",
    )
    for kind in KINDS:
        p.artifact(raw.cached(SOURCE, home_url(kind)), JUNTA)
    p.add(
        "source",
        id=ESCRUTINIO,
        kind="official_body",
        publisher="Junta Electoral de la Provincia de Buenos Aires",
        title="Escrutinio definitivo de las elecciones generales por partido",
        url=results_home_url(),
        license="Official public record, no license stated",
        license_url=results_home_url(),
        attribution="Junta Electoral de la Provincia de Buenos Aires",
    )
    p.artifact(raw.cached(SOURCE, results_home_url()), ESCRUTINIO)
    for name, (kind, english, spanish) in BODIES.items():
        p.add("body", id=body(name), polity_id=ARGENTINA, kind=kind)
        p.name(body(name), english, "en", "english", False)
        p.name(body(name), spanish, "es", "official")
    for name, (kind, english, spanish) in OFFICES.items():
        p.add("office", id=office(name), polity_id=ARGENTINA, kind=kind)
        p.name(office(name), english, "en", "english", False)
        p.name(office(name), spanish, "es", "official")
    for found in sorted({value for value in parties().values()}):
        p.add("party", id=found.id, kind="party", polity_id=ARGENTINA)
        p.name(found.id, found.name, "es", "official")
    return p


def _day(kind: str, year: int) -> date:
    return (GENERAL_DAYS if kind == "g" else PRIMARY_DAYS)[year]


def _most(counter: Counter[str]) -> str:
    return max(counter, key=lambda name: (counter[name], len(name)))


class Declared(NamedTuple):
    lists: list[Row]
    registered: int | None
    accounting: dict[str, int | None]


def province_rows(kind: str, year: int) -> Declared:
    """The province page's governor totals: its CSV rows and its page's registered voters, valid, blank, null, total."""
    text = decode(raw.cached(SOURCE, province_csv_url(kind, year)).path.read_bytes())
    lines = text.splitlines()
    shift = 1 if kind == "e" else 0
    rows: list[Row] = []
    for line in lines[1:]:
        cells = line.split(";")
        lista = int(cells[3 - shift])
        rows.append(
            Row(
                cells[5 + shift] if kind == "g" else cells[6],
                lista,
                clean(cells[4 - shift]),
                (int(cells[4]) if cells[4] else None) if kind == "e" else None,
                clean(cells[5]) if kind == "e" else None,
                number(cells[-1]) or 0,
            )
        )
    registered, _, figures = accounting(decode(raw.cached(SOURCE, province_url(kind, year)).path.read_bytes()))
    return Declared(rows, registered, figures["04"])


def election(kind: str, year: int) -> Package:
    day = _day(kind, year)
    data = everything()[(kind, year)]
    primary = kind == "e"
    label = "primary" if primary else "general"
    p = Package(
        f"{SOURCE}/{election_key(kind, year)}",
        SOURCE,
        f"The {year} Buenos Aires province {'open primaries (EPAOS)' if primary else 'general election'} by partido "
        "from the Junta Electoral's historical statistics",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ARGENTINA, COUNTRY}
    for did in data:
        p.artifact(raw.cached(SOURCE, csv_url(kind, year, did)), JUNTA)
        p.artifact(raw.cached(SOURCE, html_url(kind, year, did)), JUNTA)
    total = year in TOTAL_YEARS[kind]
    if total:
        p.artifact(raw.cached(SOURCE, province_url(kind, year)), JUNTA)
        p.artifact(raw.cached(SOURCE, province_csv_url(kind, year)), JUNTA)
    election_id = key("election", kind, year)
    path = f"ar-b/{day}-{label}"
    p.add(
        "election",
        id=election_id,
        polity_id=ARGENTINA,
        kind="primary" if primary else "general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(
        election_id,
        f"{year} Buenos Aires province {'open primaries' if primary else 'general election'} of {day:%-d %B}",
        "en",
        "english",
        False,
    )
    p.name(
        election_id,
        f"{'Elecciones primarias, abiertas, simultáneas y obligatorias' if primary else 'Elecciones generales'} "
        f"de la Provincia de Buenos Aires de {year}",
        "es",
        "official",
    )
    p.add(
        "note",
        id=key("note", election_id),
        entity_id=election_id,
        kind="methodology",
        body_md="Votes are each partido's totals for each office as the Junta Electoral publishes them, with the "
        "positive votes, blank votes, null votes, and registered voters its partido pages print. The Junta "
        "publishes no seats or winners with these tables. "
        + (
            ""
            if primary
            else "Each partido's school board (consejo escolar) is added from the Junta's definitive-count PDF "
            "for the partido, which prints one list table for the mayor, the councillors, and the school board "
            "together and declares the seats each list won. In a year the statistics hold only the mayor, the "
            "partido's councillors come from the same PDF, with that table's votes and the councillor seats. "
        )
        + (
            "In the primaries each list's votes are those of its internal lines, which sit under the list."
            if primary
            else ""
        ),
        lang="en",
    )
    section_of = {did: held_.section for did, held_ in data.items()}
    units: dict[str, str] = {}
    for did in sorted(data):
        unit = units[did] = key("unit", kind, year, did)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ARGENTINA,
            scheme_code="ar-b.partido",
            code=f"{election_key(kind, year)}:{did}",
            area_relation="equals",
            area_id=area("ar-b.partido", did),
            kind="standard",
        )
    groups: dict[tuple[str, str], list[str]] = {}
    for did, held_ in sorted(data.items()):
        for cargo in sorted({row.cargo for row in held_.rows}):
            scope = {"04": "B", "06": str(held_.section), "08": str(held_.section)}.get(cargo, did)
            groups.setdefault((cargo, scope), []).append(did)
    for (cargo, scope), dids in groups.items():
        if cargo in ("06", "08"):
            everyone = [did for did, section in section_of.items() if str(section) == scope]
            if sorted(everyone) != sorted(dids):
                raise ValueError(
                    f"{kind} {year} cargo {cargo} section {scope} lacks {sorted(set(everyone) - set(dids))}"
                )
    declared = province_rows(kind, year) if total else None
    if declared is not None:
        _reconcile(p, declared, data)
    for (cargo, scope), dids in sorted(groups.items()):
        _contest(p, kind, year, election_id, path, cargo, scope, dids, data, units, declared if cargo == "04" else None)
    if not primary:
        for did in sorted(data):
            if year in pdf_years(did):
                p.artifact(raw.cached(SOURCE, pdf_url(year, did)), ESCRUTINIO)
                p.artifact(raw.cached(SOURCE, results_listing_url(did)), ESCRUTINIO)
                _school_board(p, year, election_id, path, did, data[did], units[did])
                if "11" not in data[did].accounting:
                    _council(p, year, election_id, path, did, data[did], units[did])
        _accept_gaps(p, year, data)
    return p


def _contest(
    p: Package,
    kind: str,
    year: int,
    election_id: str,
    path: str,
    cargo: str,
    scope: str,
    dids: list[str],
    data: dict[str, Held],
    units: dict[str, str],
    declared: Declared | None,
) -> None:
    primary = kind == "e"
    contest = key("contest", kind, year, cargo, scope)
    place = {"04": None, "06": SECTION_NAMES.get(int(scope)) if scope.isdigit() and len(scope) == 1 else None}.get(
        cargo
    )
    if cargo in ("06", "08"):
        title_es = f"{'Senadores' if cargo == '06' else 'Diputados'} provinciales, {SECTION_NAMES[int(scope)]} Sección Electoral"
        title_en = (
            f"Provincial {'senators' if cargo == '06' else 'deputies'}, {SECTION_NAMES[int(scope)]} electoral section"
        )
        tail = f"{'senate' if cargo == '06' else 'deputies'}-section-{scope}"
        district = area("ar-b.seccion", scope)
    elif cargo == "04":
        title_es, title_en, tail = (
            "Gobernador de la Provincia de Buenos Aires",
            "Governor of Buenos Aires Province",
            "governor",
        )
        district = PROVINCE
    else:
        name = NAMES[scope]
        mayor = cargo == "10"
        title_es = f"{'Intendente' if mayor else 'Concejales'} de {name}"
        title_en = f"{'Mayor' if mayor else 'Municipal councillors'} of {name}"
        tail = f"{'mayor' if mayor else 'council'}-{changeset.slug(name)}"
        district = area("ar-b.partido", scope)
    del place
    seat = {
        "04": {"kind": "office", "office_id": office("governor"), "magnitude": 1},
        "10": {"kind": "office", "office_id": office("mayor"), "magnitude": 1},
        "06": {"kind": "list_allocation", "body_id": body("senate")},
        "08": {"kind": "list_allocation", "body_id": body("deputies")},
        "11": {"kind": "list_allocation", "body_id": body("council")},
    }[cargo]
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        district_area_id=district,
        round_number=1,
        default_result_kind="counted",
        **seat,
    )
    p.name(contest, title_es, "es", "official")
    p.name(contest, title_en, "en", "english", False)
    p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
    result_set = key("result_set", kind, year, cargo, scope)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=JUNTA,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    labels: dict[str, Counter[str]] = {}
    lines: dict[tuple[str, str], Counter[str]] = {}
    chosen: dict[str, Party] = {}
    for did in dids:
        for row in data[did].rows:
            if row.cargo != cargo or row.lista >= BLANK:
                continue
            found = party(row, year)
            chosen[found.id] = found
            labels.setdefault(found.id, Counter())[row.name or found.name] += 1
            if primary:
                lines.setdefault((found.id, line_key(row)), Counter())[row.line_name or ""] += 1
    tops: dict[str, str] = {}
    for id in sorted(labels):
        tops[id] = key("option", kind, year, cargo, scope, id)
        p.add(
            "contest_option",
            id=tops[id],
            contest_id=contest,
            kind="list",
            party_id=id,
            ballot_label=_most(labels[id]),
        )
    leaves: dict[tuple[str, str], str] = {}
    for (id, line), names in sorted(lines.items()):
        leaves[(id, line)] = key("option", kind, year, cargo, scope, id, line)
        p.add(
            "contest_option",
            id=leaves[(id, line)],
            contest_id=contest,
            kind="list",
            parent_option_id=tops[id],
            party_id=id,
            ballot_label=_most(names) or None,
        )

    def option(row: Row) -> str:
        found = party(row, year).id
        return leaves[(found, line_key(row))] if primary else tops[found]

    for did in dids:
        held_ = data[did]
        figures = held_.accounting[cargo]
        for measure, value in (
            (2, held_.registered),
            (3, figures.get("total")),
            (4, figures.get("positive")),
            (5, figures.get("null")),
            (6, figures.get("blank")),
        ):
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=units[did],
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        for row in held_.rows:
            if row.cargo != cargo or row.lista >= BLANK:
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=units[did],
                option_id=option(row),
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=row.votes,
            )
    if declared is not None:
        _declare(p, result_set, declared, option)


def _accept_gaps(p: Package, year: int, data: dict[str, Held]) -> None:
    """Accept the accounting findings that the PDFs' own printed figures cause: a list table that does not add
    up to the printed positive votes, or positive, blank, and null votes that do not add up to the votes cast."""
    sums: list[str] = []
    totals: list[str] = []
    for did in sorted(data):
        if year not in pdf_years(did):
            continue
        here = board(year, did)
        listed = sum(votes for _, _, votes in here.table)
        figures = here.figures
        positive = figures.get("positive")
        if positive is not None and listed != positive and not _recovered(here, data[did], positive):
            sums.append(f"{NAMES[did]} (lists {listed:,}, positive votes {positive:,})")
        pos, blank, null, total = (figures.get(name) for name in ("positive", "blank", "null", "total"))
        if (
            pos is not None
            and blank is not None
            and null is not None
            and total is not None
            and pos + blank + null != total
        ):
            totals.append(
                f"{NAMES[did]} (positive, blank, and null votes {pos + blank + null:,}, votes cast {total:,})"
            )
    if sums:
        p.accept(
            "accounting.votes_valid",
            "In the Junta's definitive-count PDF the printed list votes do not add up to the printed positive votes "
            f"for {'; '.join(sums)}. The package keeps each figure as printed.",
        )
    if totals:
        p.accept(
            "accounting.ballots_sum",
            "In the Junta's definitive-count PDF the printed positive, blank, and null votes do not add up to the "
            f"printed votes cast for {'; '.join(totals)}. The package keeps each figure as printed.",
        )


def _recovered(here: Board, held_: Held, positive: int) -> bool:
    """Whether a list the table leaves out, taken from the statistics file, brings the table to the positive votes."""
    cargo = "11" if "11" in held_.accounting else "10"
    numbers = {row[0] for row in here.table}
    missing = [row for row in held_.rows if row.cargo == cargo and row.lista < BLANK and row.lista not in numbers]
    return sum(votes for _, _, votes in here.table) + sum(row.votes for row in missing) == positive


def _list_party(lista: int, name: str, held_: Held, year: int) -> tuple[Party, str]:
    """The party a PDF's list row stands for, found by number among the partido's rows in the statistics file
    of the same year (and its name there), or else by the name the PDF prints for a list the file leaves out."""
    for row in held_.rows:
        if row.lista == lista:
            return party(row, year), row.name or party(row, year).name
    return parties()[(lista, normal(name), None)], name


def _school_board(p: Package, year: int, election_id: str, path: str, did: str, held_: Held, unit: str) -> None:
    """The partido's consejo escolar: the list votes of the Junta's definitive-count PDF, which prints one list
    table for the mayor, the councillors, and the school board together, and the school board seats it declares to
    each list. A list the PDF's table leaves out but the statistics file prints, with votes that bring the table to
    the PDF's positive votes, is taken from the statistics file."""
    _board_contest(p, year, election_id, path, did, held_, unit, council=False)


def _council(p: Package, year: int, election_id: str, path: str, did: str, held_: Held, unit: str) -> None:
    """The partido's council in a year the statistics file holds only the mayor: the same list table, with the
    councillor seats the PDF declares to each list."""
    _board_contest(p, year, election_id, path, did, held_, unit, council=True)


def _board_contest(
    p: Package, year: int, election_id: str, path: str, did: str, held_: Held, unit: str, council: bool
) -> None:
    here = board(year, did)
    listed = {row.lista for row in held_.rows}
    table = [row for row in here.table if row[2] or row[0] in listed]
    cargo = "11" if "11" in held_.accounting else "10"
    positive = here.figures.get("positive")
    missing = [
        row for row in held_.rows if row.cargo == cargo and row.lista < BLANK and row.lista not in {t[0] for t in table}
    ]
    if (
        missing
        and positive is not None
        and sum(votes for _, _, votes in table) + sum(r.votes for r in missing) == positive
    ):
        table += [(row.lista, row.name, row.votes) for row in missing]
    name = NAMES[did]
    tag = "11" if council else "school"
    seats = here.council if council else here.seats
    if seats is None:
        raise ValueError(f"{year} {did}: no councillor seats in the PDF")
    contest = key("contest", "g", year, tag, did)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        district_area_id=area("ar-b.partido", did),
        round_number=1,
        default_result_kind="counted",
        kind="list_allocation",
        body_id=body("council" if council else "school_board"),
        magnitude=seats,
    )
    if council:
        p.name(contest, f"Concejales de {name}", "es", "official")
        p.name(contest, f"Municipal councillors of {name}", "en", "english", False)
        tail = f"council-{changeset.slug(name)}"
    else:
        p.name(contest, f"Consejeros escolares de {name}", "es", "official")
        p.name(contest, f"School board members (consejeros escolares) of {name}", "en", "english", False)
        tail = f"school-board-{changeset.slug(name)}"
    p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
    result_set = key("result_set", "g", year, tag, did)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=ESCRUTINIO,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    options: dict[int, str] = {}
    for lista, list_name, _ in table:
        found, label = _list_party(lista, list_name, held_, year)
        options[lista] = key("option", "g", year, tag, did, found.id)
        p.add(
            "contest_option",
            id=options[lista],
            contest_id=contest,
            kind="list",
            party_id=found.id,
            ballot_label=label,
        )
    for measure, label in ((2, "registered"), (3, "total"), (4, "positive"), (5, "null"), (6, "blank")):
        if label in here.figures:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=here.figures[label],
            )
    for lista, _, votes in table:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=options[lista],
            channel=0,
            measure=1,
            count_round=0,
            flags=0,
            value=votes,
        )
    for list_name, won in here.council_won if council else here.won:
        lista = table[match_list(list_name, table, year, did)][0]
        p.add("outcome", result_set_id=result_set, option_id=options[lista], kind="won", seats=won)


def _reconcile(p: Package, declared: Declared, data: dict[str, Held]) -> None:
    """Accept the declared-total finding where the province page's governor totals exceed its partido pages' sums."""
    pages = [held_.accounting["04"] for held_ in data.values() if "04" in held_.accounting]
    labels = {"positive": "positive votes", "blank": "blank votes", "null": "null votes", "total": "votes cast"}
    off = {
        name: (declared.accounting.get(name) or 0) - sum(figures.get(name) or 0 for figures in pages) for name in labels
    }
    off = {name: gap for name, gap in off.items() if gap}
    if off:
        gaps = ", ".join(f"{labels[name]} by {gap:,}" for name, gap in off.items())
        p.accept(
            "accounting.declared",
            f"The Junta's province page for the governor's race exceeds the sum of its {len(pages)} partido pages: "
            f"{gaps}. Each partido page adds up on its own and the registered voters agree to the voter, so the gap "
            "lies in the source's own figures. The package keeps both: the partido values, and the province page's "
            "totals as declared.",
        )


def line_key(row: Row) -> str:
    """A primary's internal line: the Junta's number for it where printed, otherwise its name."""
    return str(row.line) if row.line is not None else normal(row.line_name or "")


def _declare(
    p: Package,
    result_set: str,
    declared: Declared,
    option: Callable[[Row], str],
) -> None:
    for measure, value in (
        (2, declared.registered),
        (3, declared.accounting.get("total")),
        (4, declared.accounting.get("positive")),
        (5, declared.accounting.get("null")),
        (6, declared.accounting.get("blank")),
    ):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for row in declared.lists:
        if row.cargo == "04" and row.lista < BLANK:
            p.add("declared", result_set_id=result_set, option_id=option(row), measure=1, value=row.votes)
