"""Tucumán provincial elections of 2007, 2011, 2015, 2019, and 2023 from the Junta Electoral's published count sheets."""

import functools
import json
import re
import subprocess
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from time import sleep
from urllib.parse import quote, unquote, urljoin
from uuid import uuid5

import httpx
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "ar.tucuman"
SITE = "https://electoraltucuman.gob.ar"
PAGE_2007 = f"{SITE}/elecciones-generales-2007/"
PAGE_2011 = f"{SITE}/elecciones-generales-2011/"
PAGE_2015 = f"{SITE}/resultadosescrutiniodefinitivo2015/"
FILES = f"{SITE}/wp-content/uploads/http://www.electoraltucuman.gob.ar/files"
URLS_2007 = {
    "gobernador": f"{FILES}/2007-GOBERNADOR-Y-VICE.pdf",
    "capital": f"{FILES}/2007-LEGISLADORES-seccion-I-CAPITAL.pdf",
    "este": f"{FILES}/2007-LEGISLADORES-seccion-II-ESTE.pdf",
    "oeste": f"{FILES}/2007-LEGISLADORES-seccion-III-OESTE.pdf",
}
ZIP_2011 = f"{FILES}/Resultados-ano-2011.zip"
ELECTED_2011 = f"{FILES}/TUCUMAN-Elecciones-2011-Electos-2014-09-02.pdf"
PAGE_2015_ACTS = f"{SITE}/elecciones-generales-2015/"
ELECTED_2015 = f"{SITE}/wp-content/uploads/Resol955-15HJEP_Anexo_2015-CANDIDATOS-Lista-Completa2015-10-14.pdf"
EXTRA_2015 = (
    f"{SITE}/wp-content/uploads/2015_ComComunal_LosRalos_CruzAlta_AcumuladoDefinitivo.pdf",
    f"{SITE}/wp-content/uploads/2015_ComComunal_SanIgnacio_LaCocha_Definitivo.pdf",
    f"{SITE}/wp-content/uploads/2015_ComComunal_SanPablo_Lules_Definitivo.pdf",
)
MESAS_2015 = f"{SITE}/wp-content/uploads/2015_escrutinio_mesa_a_mesa_capital.pdf"
PAGE_2019 = f"{SITE}/elecciones-generales-2019/"
PAGE_2023 = f"{SITE}/elecciones-generales-2023/"
ELECTED_2023 = f"{SITE}/wp-content/uploads/2023_tucuman_electos_web.pdf"
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ARG"
ARGENTINA = str(uuid5(package.NAMESPACE, "polity\x1fAR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fARG"))
PROVINCE = "AR-T"
DAYS = {
    2007: date(2007, 8, 26),
    2011: date(2011, 8, 28),
    2015: date(2015, 8, 23),
    2019: date(2019, 6, 9),
    2023: date(2023, 6, 11),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: object) -> str:
    return key("area", scheme, code)


SOURCE_ID = key("source", "junta")
GEO_SOURCE = key("source", "geoboundaries")
PROVINCE_AREA = area("ar-t.provincia", PROVINCE)


def plain(text: str) -> str:
    """Upper-case, accent-free, single-spaced text for matching names across years."""
    decomposed = unicodedata.normalize("NFKD", text.replace("�", "N"))
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).upper().replace(".", " ").split())


# ---------------------------------------------------------------------------------------------------------------------
# Fetching


def _pdf(path: Path) -> None:
    head = path.read_bytes()[:5]
    if head != b"%PDF-":
        raise ValueError(f"{path.name} is not a PDF")
    text = _text(path)
    if "ELECCIONES PROVINCIALES" not in text.upper() and "ESCRUTINIO" not in text.upper():
        raise ValueError(f"{path.name} is not a Tucumán count sheet")


def _text(path: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def _links(page: str, pattern: str) -> list[str]:
    html = raw.cached(SOURCE, page).path.read_bytes().decode("utf-8", "replace")
    found = re.findall(r'href="([^"]+)"', html)
    return sorted({quote(urljoin(page, link), safe=":/%()") for link in found if re.search(pattern, link)})


def urls_2019() -> list[str]:
    """The 2019 results page's definitive count sheets and mesa-by-mesa files."""
    return _links(PAGE_2019, r"2019 Elecciones/Resultados/.*\.pdf$")


def urls_2023() -> list[str]:
    """The 2023 results page's definitive count PDFs (one per section or department) and mesa-by-mesa files."""
    return _links(PAGE_2023, r"uploads/(?:[A-Z-]+-Resultados-Generales|2023_mesa_a_mesa_\w+)\.pdf$")


def is_mesa_file(url: str) -> bool:
    return "Mesas-" in url or "mesa_a_mesa" in url


def urls_2015() -> list[str]:
    return [*_links(PAGE_2015, r"Resultados2015/.*\.pdf$"), *EXTRA_2015]


def _fetch_pdf(url: str) -> None:
    """Fetches one PDF, waiting and trying again when the site answers with its holding page instead of the file."""
    for attempt in range(4):
        try:
            raw.fetch(SOURCE, url, suffix=".pdf", check=_pdf)
            return
        except (ValueError, httpx.HTTPError) as failure:
            print(f"{url}: {failure}", flush=True)
            sleep(5 * (attempt + 1))


def fetch() -> None:
    for page in (PAGE_2007, PAGE_2011, PAGE_2015):
        raw.fetch(SOURCE, page, suffix=".html")
    for url in URLS_2007.values():
        raw.fetch(SOURCE, url, suffix=".pdf", check=_pdf)
    raw.fetch(SOURCE, ZIP_2011, suffix=".zip")
    raw.fetch(SOURCE, PAGE_2015_ACTS, suffix=".html")
    for url in (ELECTED_2011, ELECTED_2015):
        raw.fetch(SOURCE, url, suffix=".pdf", check=_pdf)
    for url in urls_2015():
        try:
            raw.fetch(SOURCE, url, suffix=".pdf", check=_pdf)
        except httpx.HTTPStatusError as failure:
            print(f"{url}: {failure.response.status_code}", flush=True)
    raw.fetch(SOURCE, MESAS_2015, suffix=".pdf", check=_pdf)
    for page in (PAGE_2019, PAGE_2023):
        raw.fetch(SOURCE, page, suffix=".html")
    raw.fetch(SOURCE, ELECTED_2023, suffix=".pdf", check=_pdf)
    for url in (*urls_2019(), *urls_2023()):
        _fetch_pdf(url)
    for level in ("ADM1", "ADM2"):
        raw.fetch(SOURCE, f"{GEOBOUNDARIES}/{level}/geoBoundaries-ARG-{level}_simplified.geojson", suffix=".geojson")


# ---------------------------------------------------------------------------------------------------------------------
# Reading the count sheets


@dataclass
class Sheet:
    """One count sheet: one category in one jurisdiction (or one section or the whole province)."""

    file: str
    year: int
    category: str
    section: str | None
    department: str | None
    kind: str | None
    place: str | None
    positive: int
    blank: int
    null: int
    total: int
    registered: int | None
    tables_counted: int | None
    tables: int | None
    lists: list[tuple[int, str, int, str | None]] = field(default_factory=list)
    when: str = ""
    redo: str = ""


def count(text: str) -> int:
    return int(text.replace(".", "").replace(",", ""))


CATEGORIES = {
    "GOBERNADOR": "gobernador",
    "LEGISLADOR": "legislador",
    "LEGISLADORES": "legislador",
    "INTENDENTE": "intendente",
    "CONCEJAL": "concejal",
    "COMISIONADO": "comisionado",
    "COMISIONADO COMUNAL": "comisionado",
    "COMIS COMUNAL": "comisionado",
}


def sheet(file: str, year: int, text: str) -> Sheet:
    """Reads a 2011 or 2015 count sheet, the layout the Junta Electoral's system printed."""
    first = text.split("\f")[0]
    category = re.search(r"CATEGORIA[ \t]*:[ \t]*(.+?)[ \t]*$", first, re.MULTILINE)
    where = re.search(
        r"SECCION[ \t]*:[ \t]*(\S*)(?:[ \t]+DEPTO[ \t]*:[ \t]*(.*?))?[ \t]+JURISDICCION[ \t]*:[ \t]*(.*?)[ \t]*$",
        first,
        re.MULTILINE,
    )
    if not category or not where:
        raise ValueError(f"{file}: no header")
    jurisdiction = where.group(3)
    kind = place = None
    if jurisdiction:
        kind, place = jurisdiction.split(" ", 1) if " " in jurisdiction else (jurisdiction, "")
    date_line = re.search(r"ELECCIONES PROVINCIALES[^\n]*?(\d{1,2} DE \w+ DE \d{4})", first)
    redo = re.search(r"COMPLEMENTARIAS DEL (\d{1,2} DE \w+ DE \d{4})", first)
    totals = re.search(
        r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+[\d,]+\s+(\d+)\s+(\d+)\s+[\d,]+\s+(\d+)\s*$", first, re.MULTILINE
    )
    if not totals:
        raise ValueError(f"{file}: no totals row")
    s = Sheet(
        file=file,
        year=year,
        category=CATEGORIES[category.group(1).strip().upper()],
        section=where.group(1) or None,
        department=(where.group(2) or "").strip() or None,
        kind=kind,
        place=place or None,
        positive=int(totals.group(1)),
        blank=int(totals.group(2)),
        null=int(totals.group(3)),
        total=int(totals.group(4)),
        registered=int(totals.group(5)),
        tables_counted=int(totals.group(6)),
        tables=int(totals.group(7)),
        when=date_line.group(1) if date_line else "",
        redo=redo.group(1) if redo else "",
    )
    for number, page in enumerate(text.split("\f")):
        _lists(s, file, page, number == 0)
    if sum(v for _, _, v, _ in s.lists) != s.positive:
        raise ValueError(f"{file}: lists sum to {sum(v for _, _, v, _ in s.lists)}, sheet says {s.positive}")
    return s


def _lists(s: Sheet, file: str, page: str, first: bool) -> None:
    seen = not first
    for line in page.splitlines():
        if "LISTAS" in line and "VOTOS" in line:
            seen = True
            continue
        if not seen:
            continue
        entry = re.match(r"^\s*(\d+)\s{2,}(\S.*?)\s{2,}(\d+)\s+[\d,]+\s*$", line)
        if entry:
            s.lists.append((int(entry.group(1)), " ".join(entry.group(2).split()), int(entry.group(3)), None))
            continue
        if re.match(r"^\s*(POSITIVOS|EN BLANCO|NULOS|TOTAL)\s", line):
            continue
        if first and line.strip():
            raise ValueError(f"{file}: unread line {line!r}")


@functools.cache
def sheets_2015() -> list[Sheet]:
    out = []
    for url in urls_2015():
        if url not in raw.keys(SOURCE):
            continue
        out.append(sheet(unquote(url.rsplit("/", 1)[1]), 2015, _text(raw.cached(SOURCE, url).path)))
    return out


@functools.cache
def sheets_2011() -> list[Sheet]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, ZIP_2011).path)
    out = []
    with archive:
        for info in sorted(archive.infolist(), key=lambda i: i.filename):
            if not info.filename.lower().endswith(".pdf"):
                continue
            data = archive.read(info)
            if data[:5] != b"%PDF-":
                raise ValueError(f"{info.filename} is not a PDF")
            proc = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, check=True)
            s = sheet(info.filename, 2011, proc.stdout.decode("utf-8", "replace"))
            out.append(s)
    return out


def sheets_in(file: str, year: int, text: str) -> list[Sheet]:
    """Reads every count sheet in a PDF: the page numbered 1 starts a sheet and the pages after it continue it."""
    groups: list[list[str]] = []
    for page in text.split("\f"):
        if re.search(r"Paginas:[ \t]*1[ \t]*$", page, re.MULTILINE):
            groups.append([page])
        elif page.strip():
            if not groups:
                raise ValueError(f"{file}: a page with no sheet heading")
            groups[-1].append(page)
    return [sheet(file, year, "\f".join(group)) for group in groups]


@functools.cache
def sheets_2019() -> list[Sheet]:
    out = []
    for url in urls_2019():
        if not is_mesa_file(url):
            out.extend(sheets_in(unquote(url.rsplit("/", 1)[1]), 2019, _text(raw.cached(SOURCE, url).path)))
    return out


@functools.cache
def sheets_2023() -> list[Sheet]:
    out = []
    for url in urls_2023():
        if not is_mesa_file(url):
            out.extend(sheets_in(unquote(url.rsplit("/", 1)[1]), 2023, _text(raw.cached(SOURCE, url).path)))
    return out


def sheet_2007(file: str, text: str, category: str, section: str | None) -> Sheet:
    """Reads a 2007 sheet, which prints electors, ballots cast, and each list's votes beside its head candidate."""
    registered = re.search(r"TOTAL ELECTORES:\s*([\d.]+)", text)
    cast = re.search(r"TOTAL VOTOS EMITIDOS:\s*([\d.]+)", text)
    blank = re.search(r"VOTOS EN BLANCO\s+([\d.]+)\s+[\d,]+%", text)
    null = re.search(r"VOTOS NULOS\s+([\d.]+)\s+[\d,]+%", text)
    if not (registered and cast and blank and null):
        raise ValueError(f"{file}: no totals")
    lists: list[tuple[int, str, int, str | None]] = []
    for line in text.splitlines():
        entry = re.match(r"^\s*(\d+)\s+(\S.*?)\s+([\d.]+)\s+[\d,]+%\s+(\S.*?)\s*$", line)
        if entry:
            lists.append((int(entry.group(1)), " ".join(entry.group(2).split()), count(entry.group(3)), entry.group(4)))
    total = count(cast.group(1))
    s = Sheet(
        file=file,
        year=2007,
        category=category,
        section=section,
        department=None,
        kind=None,
        place=None,
        positive=total - count(blank.group(1)) - count(null.group(1)),
        blank=count(blank.group(1)),
        null=count(null.group(1)),
        total=total,
        registered=count(registered.group(1)),
        tables_counted=None,
        tables=None,
        lists=lists,
    )
    if sum(v for _, _, v, _ in lists) != s.positive:
        raise ValueError(f"{file}: lists sum to {sum(v for _, _, v, _ in lists)}, ballots say {s.positive}")
    return s


@functools.cache
def sheets_2007() -> list[Sheet]:
    out = []
    for name, url in URLS_2007.items():
        out.append(
            sheet_2007(
                url.rsplit("/", 1)[1],
                _text(raw.cached(SOURCE, url).path),
                "gobernador" if name == "gobernador" else "legislador",
                None if name == "gobernador" else name.upper(),
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Mesa-by-mesa sheets for the capital


@dataclass
class MesaCount:
    lists: list[tuple[int, str, int]] = field(default_factory=list)
    positive: int = 0
    blank: int = 0
    null: int = 0
    total: int = 0


@dataclass
class Mesa:
    """One polling table's count: where it sits and, per category, each list's votes and the totals."""

    number: int
    voters: int
    envelopes: int
    section: str = ""
    department: str = ""
    jurisdiction: str = ""
    circuit: str = ""
    categories: dict[str, MesaCount] = field(default_factory=dict)


PAGE_NOISE = re.compile(
    r"^\s*(Fecha:|Junta Electoral Provincial|ELECCIONES PROVINCIALES|TUCUMAN\s*$|Paginas:|Hora:)", re.IGNORECASE
)
MESA_HEAD = re.compile(
    r"^\s*MESA\s*:\s*(\d+)\s+VOTANTES\s*:\s*(\d+)\s+SOBRES\s*:\s*(\d+)\s+DIFERENCIA\s*:\s*(-?\d+)\s*$"
)
MESA_SECTION = re.compile(r"^\s*SECCION\s*:\s*(\w*)\s+DEPARTAMENTO\s*:\s*(.*?)\s*$")
MESA_PLACE = re.compile(r"^\s*JURISDICCION\s*:\s*(.*?)\s+CIRCUITO\s*:\s*(\w+)\s*$")
MESA_ENTRY = re.compile(r"^\s*(\d+) - (\S.*?)\s+(\d+)\s*$")
MESA_TOTALS = {"VOTOS POSITIVOS": "positive", "VOTOS EN BLANCO": "blank", "VOTOS NULOS": "null", "VOTOS TOTAL": "total"}


def mesa_sheets(file: str, text: str) -> list[Mesa]:
    """Reads a mesa-by-mesa count: a block per table, each category checked against its own totals."""
    out: list[Mesa] = []
    mesa: Mesa | None = None
    count_: MesaCount | None = None
    first_word = ""
    for line in text.splitlines():
        if not line.strip() or PAGE_NOISE.match(line) or "LISTA" in line and "VOTOS" in line and "TOTAL" not in line:
            continue
        head = MESA_HEAD.match(line)
        if head:
            if mesa is not None:
                _close(file, mesa)
            mesa = Mesa(int(head.group(1)), int(head.group(2)), int(head.group(3)))
            out.append(mesa)
            count_ = None
            continue
        if mesa is None:
            raise ValueError(f"{file}: text before the first table: {line!r}")
        section = MESA_SECTION.match(line)
        if section:
            mesa.section, mesa.department = section.group(1), section.group(2)
            continue
        place = MESA_PLACE.match(line)
        if place:
            mesa.jurisdiction, mesa.circuit = place.group(1), place.group(2)
            continue
        if line.strip() == "COMISIONADO":  # the heading "COMISIONADO COMUNAL" wraps, its first word above the label
            first_word = "COMISIONADO "
            continue
        category = re.match(r"^\s*CATEGORIA\s*:\s*(\w+(?: \w+)?)\s*$", line)
        if category:
            name = CATEGORIES[first_word + category.group(1)]
            first_word = ""
            count_ = mesa.categories[name] = MesaCount()
            continue
        if count_ is None:
            raise ValueError(f"{file}: mesa {mesa.number}: unread line {line!r}")
        total = re.match(r"^\s*(VOTOS [A-Z ]+?)\s+(\d+)\s*$", line)
        if total and total.group(1) in MESA_TOTALS:
            setattr(count_, MESA_TOTALS[total.group(1)], int(total.group(2)))
            continue
        entry = MESA_ENTRY.match(line)
        if entry:
            count_.lists.append((int(entry.group(1)), " ".join(entry.group(2).split()), int(entry.group(3))))
            continue
        raise ValueError(f"{file}: mesa {mesa.number}: unread line {line!r}")
    if mesa is not None:
        _close(file, mesa)
    return out


def _close(file: str, mesa: Mesa) -> None:
    if not mesa.circuit or not mesa.section or not mesa.categories:
        raise ValueError(f"{file}: mesa {mesa.number} is incomplete")
    for name, c in mesa.categories.items():
        if sum(v for _, _, v in c.lists) != c.positive or c.positive + c.blank + c.null != c.total:
            raise ValueError(f"{file}: mesa {mesa.number} {name} does not add up")


@functools.cache
def mesas(year: int) -> list[Mesa]:
    """Every polling table's count the Junta published for one election."""
    if year == 2015:
        urls = [MESAS_2015] if MESAS_2015 in raw.keys(SOURCE) else []
    else:
        urls = [u for u in (urls_2019() if year == 2019 else urls_2023() if year == 2023 else []) if is_mesa_file(u)]
    # A section's count can run over several files (Capital-1, Capital-2), with a table split across the cut.
    files: dict[str, list[str]] = defaultdict(list)
    for url in urls:
        files[re.sub(r"-\d(?=\.pdf$)", "", url)].append(url)
    out: list[Mesa] = []
    for name, parts in files.items():
        text = "\n".join(_text(raw.cached(SOURCE, url).path) for url in sorted(parts))
        out.extend(mesa_sheets(unquote(name.rsplit("/", 1)[1]), text))
    if len({m.number for m in out}) != len(out):
        raise ValueError("a mesa number repeats")
    return out


# 2015's governor sheet for Graneros is filed under the municipality's mayoral race and prints the governor category.
MISFILED = {"sec_este-graneros-munic_de_graneros-intendente.pdf"}

DEPARTMENTS: dict[str, tuple[str, str, str]] = {
    "capital": ("Capital", "CAPITAL", "Capital"),
    "burruyacu": ("Burruyacú", "ESTE", "Burruyacú"),
    "cruz_alta": ("Cruz Alta", "ESTE", "Cruz Alta"),
    "graneros": ("Graneros", "ESTE", "Graneros"),
    "leales": ("Leales", "ESTE", "Leales"),
    "simoca": ("Simoca", "ESTE", "Simoca"),
    "trancas": ("Trancas", "ESTE", "Trancas"),
    "chicligasta": ("Chicligasta", "OESTE", "Chicligasta"),
    "famailla": ("Famaillá", "OESTE", "Famallá"),
    "juan_bautista_alberdi": ("Juan Bautista Alberdi", "OESTE", "Juan B. Alberdi"),
    "la_cocha": ("La Cocha", "OESTE", "La Cocha"),
    "lules": ("Lules", "OESTE", "Lules"),
    "monteros": ("Monteros", "OESTE", "Monteros"),
    "rio_chico": ("Río Chico", "OESTE", "Río Chico"),
    "tafi_del_valle": ("Tafí del Valle", "OESTE", "Tafí del Valle"),
    "tafi_viejo": ("Tafí Viejo", "OESTE", "Tafí Viejo"),
    "yerba_buena": ("Yerba Buena", "OESTE", "Yerba Buena"),
}
SECTIONS = {"CAPITAL": "Capital", "ESTE": "Este", "OESTE": "Oeste"}
DEPARTMENT_OF = {plain(code.replace("_", " ")): code for code in DEPARTMENTS} | {"ALBERDI": "juan_bautista_alberdi"}
DEPARTMENT_OF |= {"LOS RALOS CRUZ ALTA": "cruz_alta"}
EXTRA_DEPARTMENT = {"LosRalos_CruzAlta": "cruz_alta", "SanIgnacio_LaCocha": "la_cocha", "SanPablo_Lules": "lules"}


def department(s: Sheet) -> str | None:
    """The department a municipal or communal sheet belongs to, from its file's folder or name."""
    if s.category in ("gobernador", "legislador"):
        return None
    if s.year >= 2019:
        return DEPARTMENT_OF[plain(s.department or "")]
    if s.year == 2011:
        return DEPARTMENT_OF[plain(s.file.split("/")[1])]
    extra = re.match(r"2015_ComComunal_(.+?)_(?:Acumulado)?Definitivo", s.file)
    if extra:
        return EXTRA_DEPARTMENT[extra.group(1)]
    return DEPARTMENT_OF[plain(s.file.split("-")[1].replace("_", " "))]


def section_of(code: str) -> str:
    return DEPARTMENTS[code][1]


WORDS = {
    "RIO": "Río", "ANDRES": "Andrés", "TUCUMAN": "Tucumán", "CONCEPCION": "Concepción", "DELFIN": "Delfín",
    "GOMEZ": "Gómez", "PEREZ": "Pérez", "ARAOZ": "Aráoz", "ESTACION": "Estación", "TAFI": "Tafí", "FAMAILLA": "Famaillá",
    "BURRUYACU": "Burruyacú", "SALI": "Salí", "JOSE": "José", "GARCIA": "García", "FERNANDEZ": "Fernández",
    "CAPITAN": "Capitán", "CACERES": "Cáceres", "MOJON": "Mojón", "NOUGUES": "Nougués", "BENJAMIN": "Benjamín",
    "SARMIENTOS": "Sarmientos", "ROUGES": "Rougès", "MOLINO": "Molino", "STA": "Santa", "LAG": "Laguna",
    "BLA": "Blanca", "LUCIA": "Lucía", "BARBARA": "Bárbara", "LEON": "León", "DE": "de", "DEL": "del", "Y": "y", "LA": "La", "LAS": "Las", "LOS": "Los", "EL": "El",
}  # fmt: skip
SMALL = {"DE", "DEL", "Y"}
PLACE_FIXES = {
    "STA ROSA DE LEALES Y LAG BLANCA": "Santa Rosa de Leales y Laguna Blanca",
    "EL CHANAR": "El Chañar",
    "EL TAJAMAR Y VILLA BENJAMIN ARA": "El Tajamar y Villa Benjamín Aráoz",
}


def title(plain_name: str) -> str:
    if plain_name in PLACE_FIXES:
        return PLACE_FIXES[plain_name]
    words = []
    for number, word in enumerate(plain_name.split()):
        if number and (word in SMALL or (word in ("LA", "LAS") and words[-1] == "de")):
            words.append(word.lower())
        else:
            words.append(
                WORDS.get(word, word.capitalize()) if word not in ("LA", "LAS", "LOS", "EL") else word.capitalize()
            )
    return " ".join(words)


def label(text: str) -> str:
    return plain(text).removesuffix(" (COMUNA)")


@dataclass(frozen=True)
class Place:
    department: str
    kind: str  # "municipio" or "comuna"
    name: str  # the display name

    @property
    def code(self) -> str:
        return f"{self.department}:{self.kind}:{changeset.slug(self.name)}"

    @property
    def area(self) -> str:
        return area("ar-t.jurisdiccion", self.code)


@functools.cache
def places() -> dict[tuple[str, str, str], Place]:
    """Every municipality and commune, keyed by department, kind, and the name as printed (longest spelling wins)."""
    found: dict[tuple[str, str], set[str]] = defaultdict(set)
    for ss in (sheets_2011(), sheets_2015()):
        for s in ss:
            if s.place and s.file not in MISFILED and s.category not in ("gobernador", "legislador"):
                found[(str(department(s)), (s.kind or "").lower())].add(label(s.place))
    out: dict[tuple[str, str, str], Place] = {}
    for (dept, kind), names in found.items():
        for name in names:
            longest = max((n for n in names if n.startswith(name)), key=len)
            if len(longest) > len(name) and len(name) < 25:
                raise ValueError(f"{name!r} is a short prefix of {longest!r}")
            out[(dept, kind, name)] = Place(dept, kind, title(longest))
    for ss in (sheets_2019(), sheets_2023()):
        for s in ss:
            if s.place and s.category not in ("gobernador", "legislador"):
                dept, kind, name = str(department(s)), (s.kind or "").lower(), label(s.place)
                if (dept, kind, name) in out:
                    continue
                if (dept, kind, name) in PLACE_ALIAS:
                    out[(dept, kind, name)] = out[(dept, kind, PLACE_ALIAS[(dept, kind, name)])]
                else:
                    out[(dept, kind, name)] = Place(dept, kind, title(name))
    return out


# The Junta spells some communes differently from 2019 on than it did in 2011 and 2015: the same word order changed, a
# word was added, or a word was dropped. Each is the only commune of its department that appears in one period and not
# the other, so the later spelling folds into the earlier place and its code stays the same.
PLACE_ALIAS = {
    ("burruyacu", "comuna", "BENJAMIN ARAOZ Y EL TAJAMAR"): "EL TAJAMAR Y VILLA BENJAMIN ARA",
    ("burruyacu", "comuna", "GOBERNADOR GARMENDIA"): "GARMENDIA",
    ("chicligasta", "comuna", "LA TRINIDAD"): "TRINIDAD",
    ("chicligasta", "comuna", "MEDINAS"): "MEDINA",
    ("cruz_alta", "comuna", "EL BRACHO Y EL CEVILAR"): "EL BRACHO",
    ("monteros", "comuna", "LEON ROUGES Y SANTA ROSA"): "SANTA ROSA Y LEON ROUGES",
    ("monteros", "comuna", "SUBTENIENTE BERDINA"): "TENIENTE BERDINA",
}


def place_of(s: Sheet) -> Place:
    return places()[(str(department(s)), (s.kind or "").lower(), label(s.place or ""))]


# ---------------------------------------------------------------------------------------------------------------------
# Proclaimed authorities


@dataclass(frozen=True)
class Member:
    """One proclaimed member: the order the Junta printed, the name as printed (blank when the source leaves it
    empty), and the number and name of the list."""

    order: int
    name: str
    number: int
    party: str


ELECTED_URL = {2011: ELECTED_2011, 2015: ELECTED_2015, 2023: ELECTED_2023}
# Graneros' 2015 mayoral sheet carries a governor heading, so the count is not loaded and its proclamation has no contest.
NO_CONTEST = {(2015, ("intendente", "graneros:municipio:graneros"))}
SEATED = ("legislador", "concejal", "comisionado")
CATEGORY_OF = {"INTENDENTE": "intendente", "CONCEJAL": "concejal", "COMISIONADO COMUNAL": "comisionado"}


def proclaimed_blocks(text: str) -> list[tuple[str, list[Member]]]:
    """Each "Categoria" heading of a proclamation annex with the members printed under it."""
    blocks: list[tuple[str, list[Member]]] = []
    for line in text.splitlines():
        heading = re.match(r"^\s*Categoria\s*:\s*(\S.*?)\s*$", line)
        if heading:
            blocks.append((heading.group(1), []))
            continue
        entry = re.match(r"^\s*(\d+)\s+(.*?)\s+(?:\d{6,9}\s+){0,2}(\d+)\s+(\S.*?)\s*$", line)
        if entry and blocks:
            name = entry.group(2).strip()
            blocks[-1][1].append(
                Member(int(entry.group(1)), "" if name == "," else name, int(entry.group(3)), entry.group(4))
            )
        elif line.strip() and not re.search(
            r"APELLIDO Y NOMBRE|Junta Electoral|ELECCIONES PROVINCIALES|ANEXO AUTORIDADES", line
        ):
            raise ValueError(f"unread line in the proclamation annex: {line!r}")
    return blocks


def place_named(kind: str, name: str) -> Place:
    """The place a proclamation heading names, matching the sheets' labels, which the Junta truncates differently."""
    wanted = label(name).replace(" ", "")
    found = set()
    for (_, place_kind, printed), pl in places().items():
        for spelling in (printed, plain(pl.name)):
            squeezed = spelling.replace(" ", "")
            if place_kind == kind and (squeezed == wanted or (len(wanted) >= 20 and squeezed.startswith(wanted))):
                found.add(pl)
    if len(found) != 1:
        raise ValueError(f"{kind} {name!r} matches {len(found)} places")
    return found.pop()


@functools.cache
def proclaimed(year: int) -> dict[tuple[object, ...], list[Member]]:
    """The proclaimed members of each contest, keyed like the contests: ("gobernador",), ("legislador", section), or
    (category, place code). A heading with no members yet is left out."""
    if year not in ELECTED_URL:
        return {}
    out: dict[tuple[object, ...], list[Member]] = {}
    for heading, members in proclaimed_blocks(_text(raw.cached(SOURCE, ELECTED_URL[year]).path)):
        category, _, where = (part.strip() for part in heading.partition(" - "))
        scope: tuple[object, ...]
        if category == "GOBERNADOR":
            scope = ("gobernador",)
        elif category == "LEGISLADOR":
            scope = ("legislador", where)
        else:
            kind, _, name = where.partition(" ")
            scope = (CATEGORY_OF[category], place_named(kind.lower(), name).code)
        if scope in out:
            raise ValueError(f"two headings for {scope}")
        if members:
            out[scope] = members
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Geography


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{DAYS[year]}-general" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        general(year).write()


def emit() -> None:
    package.run("load", packages())


def _outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    shape = shapely.set_srid(shapely.make_valid(shape), 4326)
    if shapely.get_type_id(shape) == 3:
        shape = shapely.MultiPolygon([shape])
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=GEO_SOURCE,
    )


def _features(level: str) -> list[tuple[str, shapely.Geometry]]:
    """Each feature's name and shape from one geoBoundaries level."""
    path = raw.cached(SOURCE, f"{GEOBOUNDARIES}/{level}/geoBoundaries-ARG-{level}_simplified.geojson").path
    return [
        (str(f["properties"]["shapeName"]), shapely.geometry.shape(f["geometry"]))
        for f in json.loads(path.read_bytes())["features"]
    ]


@functools.cache
def shapes() -> tuple[shapely.Geometry, dict[str, shapely.Geometry]]:
    """The province outline and each department's shape, from geoBoundaries' Argentina set."""
    province = next(shape for name, shape in _features("ADM1") if name == "Tucumán")
    inside = province.buffer(0.1)
    out: dict[str, shapely.Geometry] = {}
    for code, (_, _, name) in DEPARTMENTS.items():
        found = [
            shape
            for found_name, shape in _features("ADM2")
            if found_name == name and inside.contains(shape.representative_point())
        ]
        if len(found) != 1:
            raise ValueError(f"expected one {name} department inside Tucumán, found {len(found)}")
        out[code] = found[0]
    return province, out


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Tucumán province, its 3 electoral sections, 17 departments, and the municipalities and communes the Junta "
        "Electoral reports",
    )
    p.external |= {ARGENTINA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Junta Electoral Provincial de Tucumán",
        title="Elecciones: resultados del escrutinio definitivo",
        url=f"{SITE}/elecciones/",
        license="Public information published by the provincial government",
        license_url=f"{SITE}/",
        attribution="Junta Electoral Provincial de Tucumán",
    )
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen Argentina, administrative levels 1 and 2",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="geoBoundaries, https://www.geoboundaries.org, CC BY 3.0 IGO",
    )
    levels = [
        raw.cached(SOURCE, f"{GEOBOUNDARIES}/{lv}/geoBoundaries-ARG-{lv}_simplified.geojson") for lv in ("ADM1", "ADM2")
    ]
    for artifact in levels:
        p.artifact(artifact, GEO_SOURCE)
    for code, rank, kind, noun, kinds in (
        ("ar-t.provincia", 1, "administrative", "province", None),
        ("ar-t.seccion", 2, "electoral_district", "electoral section", None),
        ("ar-t.departamento", 3, "administrative", "department", None),
        ("ar-t.jurisdiccion", 4, "administrative", "jurisdiction", {"municipio": "municipality", "comuna": "commune"}),
        ("ar-t.mesa", 5, "precinct", "polling table", None),
    ):
        p.add("scheme", code=code, polity_id=ARGENTINA, kind=kind, rank=rank, noun=noun, kinds=kinds, code_system=None)
    for code in ("ar-t.provincia", "ar-t.seccion", "ar-t.departamento"):
        p.add(
            "boundary_set",
            id=key("boundary_set", code, 2020),
            scheme_code=code,
            vintage="2020",
            as_of=levels[1].retrieved_at.date(),
        )
    province, departments = shapes()
    p.add("area", id=PROVINCE_AREA, scheme_code="ar-t.provincia")
    p.add("area_code", area_id=PROVINCE_AREA, code=PROVINCE)
    p.add("area_parent", area_id=PROVINCE_AREA, parent_id=COUNTRY)
    p.name(PROVINCE_AREA, "Tucumán", "es", "official")
    p.name(PROVINCE_AREA, "Tucumán Province", "en", "english", False)
    _outline(p, PROVINCE_AREA, province)
    for code, name in SECTIONS.items():
        id = area("ar-t.seccion", code)
        p.add("area", id=id, scheme_code="ar-t.seccion")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=PROVINCE_AREA)
        p.name(id, f"Sección {name}", "es", "official")
        p.name(id, f"{name} Section", "en", "english", False)
        _outline(p, id, shapely.union_all([departments[d] for d in DEPARTMENTS if section_of(d) == code]))
    for code, (name, section, _) in DEPARTMENTS.items():
        id = area("ar-t.departamento", code)
        p.add("area", id=id, scheme_code="ar-t.departamento")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area("ar-t.seccion", section))
        p.name(id, name, "es", "official")
        _outline(p, id, departments[code])
    for place in sorted(set(places().values()), key=lambda x: x.code):
        p.add("area", id=place.area, scheme_code="ar-t.jurisdiccion")
        p.add("area_code", area_id=place.area, code=place.code)
        p.add("area_parent", area_id=place.area, parent_id=area("ar-t.departamento", place.department))
        p.add("area_kind", area_id=place.area, kind=place.kind, source_id=SOURCE_ID)
        p.name(place.area, place.name, "es", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Registry

LEGISLATURE = key("body", "legislature")
GOVERNOR = key("office", "governor")


def council(place: Place) -> str:
    return key("body", place.kind, place.code)


def mayor(place: Place) -> str:
    return key("office", place.kind, place.code)


def _fold(seen: dict[int, set[str]]) -> dict[int, list[str]]:
    """Each number's distinct names, with a truncated spelling folded into its full one."""
    out: dict[int, list[str]] = {}
    for number, names in seen.items():
        folded: dict[str, str] = {}
        for name in names:
            same = [n for n in names if plain(n).startswith(plain(name)) or plain(name).startswith(plain(n))]
            folded[name] = max(same, key=len)
        out[number] = sorted(set(folded.values()))
    return out


def _matches(number: int, name: str, known: dict[int, list[str]]) -> bool:
    return any(
        plain(full).startswith(plain(name)) or plain(name).startswith(plain(full)) for full in known.get(number, [])
    )


@functools.cache
def party_names() -> dict[int, list[str]]:
    """Each party number's distinct names across the 2007, 2011, and 2015 sheets. Their ids stay as they were first
    published, so later elections add to this set and never rename it."""
    seen: dict[int, set[str]] = defaultdict(set)
    for ss in (sheets_2007(), sheets_2011(), sheets_2015()):
        for s in ss:
            for number, name, _, _ in s.lists:
                seen[number].add(name)
    return _fold(seen)


@functools.cache
def later_party_names() -> dict[int, list[str]]:
    """The names on the 2019 and 2023 sheets that no earlier name of the same number covers."""
    seen: dict[int, set[str]] = defaultdict(set)
    for ss in (sheets(2019), sheets(2023)):
        for s in ss:
            for number, name, _, _ in s.lists:
                if not _matches(number, name, party_names()):
                    seen[number].add(name)
    return _fold(seen)


def party(number: int, name: str) -> str:
    """The party id for a printed list name: the longest spelling of that number's name that it prefixes."""
    for known in (party_names(), later_party_names()):
        for full in known.get(number, []):
            if plain(full).startswith(plain(name)) or plain(name).startswith(plain(full)):
                return key("party", number, plain(full))
    raise KeyError((number, name))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Tucumán's legislature, governorship, municipal councils and mayoralties, communal commissions, and the "
        "parties and alliances on the 2007, 2011, 2015, 2019, and 2023 ballots",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(ARGENTINA)
    for url in (
        *URLS_2007.values(),
        ZIP_2011,
        ELECTED_2011,
        ELECTED_2015,
        ELECTED_2023,
        *urls_2019(),
        *urls_2023(),
        *(u for u in urls_2015() if u in raw.keys(SOURCE)),
    ):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add("body", id=LEGISLATURE, polity_id=ARGENTINA, kind="unicameral")
    p.name(LEGISLATURE, "Legislature of Tucumán", "en", "official")
    p.name(LEGISLATURE, "Legislatura de la Provincia de Tucumán", "es", "native", False)
    p.add("office", id=GOVERNOR, polity_id=ARGENTINA, kind="governor")
    p.name(GOVERNOR, "Governor of Tucumán", "en", "official")
    p.name(GOVERNOR, "Gobernador de la Provincia de Tucumán", "es", "native", False)
    for place in sorted(set(places().values()), key=lambda x: x.code):
        body = council(place)
        p.add("body", id=body, polity_id=ARGENTINA, kind="council")
        id = mayor(place)
        if place.kind == "municipio":
            p.name(body, f"Deliberative Council of {place.name}", "en", "official")
            p.name(body, f"Concejo Deliberante de {place.name}", "es", "native", False)
            p.add("office", id=id, polity_id=ARGENTINA, kind="mayor")
            p.name(id, f"Mayor of {place.name}", "en", "official")
            p.name(id, f"Intendente de {place.name}", "es", "native", False)
        else:
            p.name(body, f"Communal Commission of {place.name}", "en", "official")
            p.name(body, f"Comisión Comunal de {place.name}", "es", "native", False)
            p.add("office", id=id, polity_id=ARGENTINA, kind="other")
            p.name(id, f"Communal Commissioner of {place.name}", "en", "official")
            p.name(id, f"Comisionado Comunal de {place.name}", "es", "native", False)
    for number, names in sorted([*party_names().items(), *later_party_names().items()]):
        for name in names:
            id = key("party", number, plain(name))
            bloc = re.match(r"(ALIANZA|FRENTE|ACUERDO|COALICION)\b", plain(name)) is not None
            p.add("party", id=id, kind="bloc" if bloc else "party", polity_id=ARGENTINA)
            p.name(id, name.replace('"', "").strip(), "es", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Election packages

COMPLEMENTARY = {2011: date(2011, 10, 2), 2015: date(2015, 11, 8)}
METHOD = {
    2007: "Counts are from the Junta Electoral Provincial's published totals for 2007: one sheet for the governor "
    "and one for each legislative section. The sheets print electors, ballots cast, each list's votes, and blank and "
    "null ballots.",
    2011: "Counts are from the Junta Electoral Provincial's definitive count sheets (escrutinio definitivo), one per "
    "category and jurisdiction. They include the complementary voting of 2 October 2011 where the sheet says so.",
    2015: "Counts are from the Junta Electoral Provincial's definitive count sheets (escrutinio definitivo), one per "
    "category and jurisdiction. The two communes that voted again on 8 November 2015 and Los Ralos come from the "
    "cumulative sheets the Junta published separately.",
    2019: "Counts are from the Junta Electoral Provincial's definitive count sheets (escrutinio definitivo), one per "
    "category and jurisdiction.",
    2023: "Counts are from the Junta Electoral Provincial's definitive count sheets (escrutinio definitivo), one per "
    "category and jurisdiction.",
}
CATEGORY_TITLES = {
    "intendente": ("Intendente de {}", "intendente"),
    "concejal": ("Concejo Deliberante de {}", "concejo deliberante"),
    "comisionado": ("Comisionado Comunal de {}", "comisionado comunal"),
}


def mesa_only_sheets(year: int, have: list[Sheet]) -> list[Sheet]:
    """A sheet for each municipal or communal contest whose polling tables are published but whose count sheet is
    not, summed from those tables."""
    held = {(s.category, place_of(s).code) for s in have if s.place}
    sums: dict[tuple[object, ...], Sheet] = {}
    seen: dict[tuple[object, ...], dict[tuple[int, str], int]] = defaultdict(lambda: defaultdict(int))
    for m in mesas(year):
        for category, c in m.categories.items():
            if category in ("gobernador", "legislador"):
                continue
            scope = mesa_scope(m, category)
            if scope in held:
                continue
            kind, _, name = m.jurisdiction.partition(" ")
            one = sums.setdefault(
                scope,
                Sheet(
                    file=f"mesas:{scope[1]}",
                    year=year,
                    category=category,
                    section=m.section,
                    department=m.department,
                    kind=kind,
                    place=name,
                    positive=0,
                    blank=0,
                    null=0,
                    total=0,
                    registered=None,
                    tables_counted=None,
                    tables=None,
                ),
            )
            one.positive += c.positive
            one.blank += c.blank
            one.null += c.null
            one.total += c.total
            for number, list_name, votes in c.lists:
                seen[scope][(number, list_name)] += votes
    for scope, one in sums.items():
        one.lists = [(number, name, votes, None) for (number, name), votes in sorted(seen[scope].items())]
    return list(sums.values())


MESA_ONLY = (
    "The Junta Electoral Provincial published no count sheet for this contest, so the counts are the sums of its "
    "polling tables from the mesa-by-mesa definitive count."
)


def sheets(year: int) -> list[Sheet]:
    if year == 2007:
        return sheets_2007()
    found = {2011: sheets_2011, 2015: sheets_2015, 2019: sheets_2019, 2023: sheets_2023}[year]()
    found = [s for s in found if s.file not in MISFILED]
    return [*found, *mesa_only_sheets(year, found)] if year >= 2019 else found


def sources_of(year: int) -> list[str]:
    if year == 2007:
        return list(URLS_2007.values())
    if year == 2011:
        return [ZIP_2011, ELECTED_2011]
    if year == 2019:
        return urls_2019()
    if year == 2023:
        return [*urls_2023(), ELECTED_2023]
    return [
        *(u for u in urls_2015() if u in raw.keys(SOURCE)),
        *([MESAS_2015] if mesas(2015) else []),
        ELECTED_2015,
    ]


def _vote(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


# One commune's name in the 2019 mesa-by-mesa file is spelled differently from its count sheets.
MESA_SPELLING = {("rio_chico", "Comuna LOS SARMIENTOS Y LA TIPA"): "LOS SARMIENTOS Y LAS TIPAS"}


def place_in(dept: str, jurisdiction: str) -> Place:
    """The place a mesa's jurisdiction line names ("Municipio X" or "Comuna X") inside one department. The Junta cuts
    some names short, so a long name matches the start of its place's name."""
    kind, _, name = jurisdiction.partition(" ")
    wanted = label(MESA_SPELLING.get((dept, jurisdiction), name)).replace(" ", "")
    found = set()
    for (found_dept, place_kind, printed), pl in places().items():
        squeezed = printed.replace(" ", "")
        if found_dept == dept and place_kind == kind.lower() and (squeezed == wanted or squeezed.startswith(wanted)):
            found.add(pl)
    if len(found) != 1:
        raise ValueError(f"{dept} {jurisdiction!r} matches {len(found)} places")
    return found.pop()


def mesa_scope(m: Mesa, category: str) -> tuple[object, ...]:
    """The contest a polling table's category belongs to, keyed like the contests."""
    if category == "gobernador":
        return ("gobernador",)
    if category == "legislador":
        return ("legislador", m.section)
    return (category, place_in(DEPARTMENT_OF[plain(m.department)], m.jurisdiction).code)


def mesa_sets(
    p: Package,
    year: int,
    tag: str,
    contests: dict[tuple[object, ...], str],
    sets: dict[tuple[object, ...], str],
    options: dict[tuple[object, ...], str],
    official: dict[tuple[object, ...], int],
) -> None:
    """Adds a mesa-by-mesa result set beside each contest's official total, which stays the default. Each table is a
    reporting unit inside the jurisdiction it votes in, or its department when it votes in none."""
    tables = mesas(year)
    if not tables:
        return
    by_scope: dict[tuple[object, ...], list[tuple[Mesa, MesaCount]]] = defaultdict(list)
    for m in tables:
        for category, c in m.categories.items():
            # The 2015 file covers only the capital, so its governor count stays the province sheet alone.
            if year == 2015 and category == "gobernador":
                continue
            by_scope[mesa_scope(m, category)].append((m, c))
    mesa_units: dict[int, str] = {}
    for scope, counts in sorted(by_scope.items(), key=lambda item: tuple(str(part) for part in item[0])):
        if scope not in contests:
            raise ValueError(f"mesas report {scope}, which has no contest")
        number_of = {k[len(scope)]: v for k, v in options.items() if k[: len(scope)] == scope}
        set_id = key("result_set", tag, "mesa", *scope)
        p.add(
            "result_set",
            id=set_id,
            election_id=key("election", tag),
            contest_id=contests[scope],
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=False,
            completeness=None,
        )
        p.add(
            "note",
            id=key("note", set_id),
            entity_id=set_id,
            kind="methodology",
            body_md="Counts are from the Junta Electoral Provincial's mesa-by-mesa definitive count (escrutinio "
            "definitivo), one table per polling table, with the circuit each belongs to in its unit code. The "
            "contest's default set stays the official total by jurisdiction.",
            lang="en",
        )
        sheet_total = 0
        for m, c in counts:
            code = f"mesa:{m.circuit}:{m.number:04d}"
            if m.number not in mesa_units:
                mesa_units[m.number] = key("unit", tag, "ar-t.mesa", code)
                dept = DEPARTMENT_OF[plain(m.department)]
                where = place_in(dept, m.jurisdiction).area if m.jurisdiction else area("ar-t.departamento", dept)
                p.add(
                    "reporting_unit",
                    id=mesa_units[m.number],
                    polity_id=ARGENTINA,
                    scheme_code="ar-t.mesa",
                    code=f"{tag}:{code}",
                    area_relation="within",
                    area_id=where,
                    kind="standard",
                )
            unit_id = mesa_units[m.number]
            for number, name, votes in c.lists:
                if number not in number_of:
                    raise ValueError(f"list {number} {name} of mesa {m.number} is not in the {scope} sheet")
                _vote(p, set_id, unit_id, number_of[number], 1, votes)
            _vote(p, set_id, unit_id, None, 3, c.total)
            _vote(p, set_id, unit_id, None, 4, c.positive)
            _vote(p, set_id, unit_id, None, 5, c.null)
            _vote(p, set_id, unit_id, None, 6, c.blank)
            sheet_total += c.positive
        row = next(r for r in p.rows["result_set"] if r["id"] == set_id)
        row["completeness"] = round(sheet_total / official[scope], 6)


def outcomes(
    p: Package,
    tag: str,
    year: int,
    declared: dict[tuple[object, ...], list[Member]],
    contests: dict[tuple[object, ...], str],
    sets: dict[tuple[object, ...], str],
    options: dict[tuple[object, ...], str],
    party_of: dict[str, str],
) -> None:
    """Adds the Junta's proclaimed authorities to each contest's official result set: the winning list, its seats,
    and the members named under it."""
    for scope, members in declared.items():
        if scope not in contests:
            if (year, scope) in NO_CONTEST:
                continue
            raise ValueError(f"the annex proclaims {scope}, which has no contest")
        by_number: dict[int, list[str]] = defaultdict(list)
        for found, option in options.items():
            if found[: len(scope)] == scope:
                by_number[int(str(found[len(scope)]))].append(option)
        seats: dict[int, int] = defaultdict(int)
        for m in members:
            if len(by_number[m.number]) != 1:
                raise ValueError(f"{scope}: list {m.number} {m.party} matches {len(by_number[m.number])} options")
            seats[m.number] += 1
        result_set = sets[scope]
        shared = scope[0] in SEATED
        winners = {number for number in seats}
        for number, (option,) in sorted(by_number.items()):
            if number in winners:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="won" if shared else "elected",
                    seats=seats[number] if shared else None,
                )
            else:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="lost", seats=0 if shared else None)
        for m in members:
            if not m.name:
                continue
            option = by_number[m.number][0]
            person = key("person", tag, "proclaimed", *scope, m.order)
            candidacy = key("candidacy", tag, "proclaimed", *scope, m.order)
            p.add("person", id=person)
            p.name(person, m.name, "es", "official")
            role = "list_member"
            if scope[0] == "gobernador":
                role = "head" if m.order == 1 else "running_mate"
            elif scope[0] in ("intendente", "comisionado") and m.order == 1:
                role = "head"
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role=role,
                ballot_name=m.name,
                member_of_party_id=party_of[option],
            )
            if shared:
                p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
        p.add(
            "note",
            id=key("note", result_set, "proclamation"),
            entity_id=result_set,
            kind="methodology",
            body_md="Winners and seats are as the Junta Electoral Provincial proclaimed them in its annex of elected "
            f"authorities for the {year} election. The annex lists members in the order proclaimed"
            + (", and leaves the name of one member blank." if any(not m.name for m in members) else "."),
            lang="en",
        )


def general(year: int) -> Package:
    day = DAYS[year]
    tag = str(day)
    p = Package(
        f"{SOURCE}/{day}-general",
        SOURCE,
        f"The {year} Tucumán provincial election: governor, legislators, mayors, councils, and communal commissioners "
        "from the Junta Electoral's count sheets"
        if year != 2007
        else "The 2007 Tucumán provincial election: governor and legislators by section from the Junta Electoral's "
        "published totals",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ARGENTINA)
    for url in sources_of(year):
        p.uses(raw.cached(SOURCE, url))
    election = key("election", tag)
    p.add(
        "election",
        id=election,
        polity_id=ARGENTINA,
        kind="general",
        date_from=day,
        date_to=COMPLEMENTARY.get(year, day),
        status="held",
    )
    p.name(election, f"{year} Tucumán provincial election", "en", "official")
    p.name(election, f"Elecciones provinciales de Tucumán {year}", "es", "native", False)
    slug = f"ar/tucuman/{tag}-general"
    p.add("slug", path=slug, entity_id=election, is_current=True)
    slugs: set[str] = set()
    units: dict[str, str] = {}
    contests: dict[tuple[object, ...], str] = {}
    sets: dict[tuple[object, ...], str] = {}
    options: dict[tuple[object, ...], str] = {}
    progress: dict[tuple[object, ...], list[int]] = defaultdict(lambda: [0, 0])
    official: dict[tuple[object, ...], int] = defaultdict(int)
    sheetless: set[tuple[object, ...]] = set()
    party_of: dict[str, str] = {}
    declared = proclaimed(year)

    def unit(scheme: str, code: str, area_id: str) -> str:
        if code not in units:
            id = units[code] = key("unit", tag, scheme, code)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=ARGENTINA,
                scheme_code=scheme,
                code=f"{tag}:{code}",
                area_relation="equals",
                area_id=area_id,
                kind="aggregate",
            )
        return units[code]

    def contest(
        scope: tuple[object, ...],
        name: str,
        label: str,
        kind: str,
        district: str,
        magnitude: int | None,
        *,
        office_id: str | None = None,
        body_id: str | None = None,
    ) -> str:
        if scope in declared and scope[0] in SEATED:
            magnitude = len(declared[scope])
        id = contests[scope] = key("contest", tag, *scope)
        p.add(
            "contest",
            id=id,
            election_id=election,
            kind=kind,
            office_id=office_id,
            body_id=body_id,
            district_area_id=district,
            magnitude=magnitude,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(id, name, "es", "official")
        path = f"{slug}/{changeset.slug(label)}"
        if path in slugs:
            raise ValueError(f"duplicate slug {path}")
        slugs.add(path)
        p.add("slug", path=path, entity_id=id, is_current=True)
        result_set = sets[scope] = key("result_set", tag, *scope)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=MESA_ONLY if scope in sheetless else METHOD[year],
            lang="en",
        )
        return id

    def record(scope: tuple[object, ...], s: Sheet, unit_id: str, head: bool) -> None:
        result_set = sets[scope]
        for number, name, votes, candidate in s.lists:
            found = (*scope, number, plain(name))
            if found not in options:
                option = options[found] = key("option", tag, *found)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contests[scope],
                    kind="list",
                    party_id=party(number, name),
                    ballot_position=number,
                    ballot_label=name,
                )
                party_of[option] = party(number, name)
                if candidate:
                    person = key("person", tag, *found)
                    p.add("person", id=person)
                    p.name(person, candidate, "es", "official")
                    p.add(
                        "candidacy",
                        id=key("candidacy", tag, *found),
                        person_id=person,
                        option_id=option,
                        role="head" if head else "list_member",
                        list_position=None if head else 1,
                        ballot_name=candidate,
                        member_of_party_id=party(number, name),
                    )
            _vote(p, result_set, unit_id, options[found], 1, votes)
        if s.registered is not None:
            _vote(p, result_set, unit_id, None, 2, s.registered)
        _vote(p, result_set, unit_id, None, 3, s.total)
        _vote(p, result_set, unit_id, None, 4, s.positive)
        _vote(p, result_set, unit_id, None, 5, s.null)
        _vote(p, result_set, unit_id, None, 6, s.blank)
        official[scope] += s.positive
        if s.tables:
            progress[scope][0] += s.tables_counted or 0
            progress[scope][1] += s.tables

    everything = sheets(year)
    sheetless |= {(s.category, place_of(s).code) for s in everything if s.file.startswith("mesas:")}
    governor_sheets = [s for s in everything if s.category == "gobernador"]
    province_sheet = next((s for s in governor_sheets if s.section is None), None)
    section_sheets = [s for s in governor_sheets if s.section is not None]
    scope = ("gobernador",)
    contest(
        scope,
        "Gobernador y Vicegobernador de Tucumán",
        "gobernador",
        "office",
        PROVINCE_AREA,
        1,
        office_id=GOVERNOR,
    )
    if section_sheets:
        for s in section_sheets:
            code = str(s.section)
            record(scope, s, unit("ar-t.seccion", code, area("ar-t.seccion", code)), True)
        assert province_sheet is not None
        for number, name, votes, _ in province_sheet.lists:
            option = options[(*scope, number, plain(name))]
            p.add("declared", result_set_id=sets[scope], option_id=option, measure=1, value=votes)
        for measure, value in (
            (2, province_sheet.registered),
            (3, province_sheet.total),
            (4, province_sheet.positive),
            (5, province_sheet.null),
            (6, province_sheet.blank),
        ):
            if value is not None:
                p.add("declared", result_set_id=sets[scope], option_id=None, measure=measure, value=value)
    else:
        assert province_sheet is not None
        record(scope, province_sheet, unit("ar-t.provincia", PROVINCE, PROVINCE_AREA), True)
    for s in sorted((s for s in everything if s.category == "legislador"), key=lambda s: str(s.section)):
        code = str(s.section)
        scope = ("legislador", code)
        contest(
            scope,
            f"Legisladores provinciales, Sección {SECTIONS[code]}",
            f"legisladores {SECTIONS[code]}",
            "list_allocation",
            area("ar-t.seccion", code),
            None,
            body_id=LEGISLATURE,
        )
        record(scope, s, unit("ar-t.seccion", code, area("ar-t.seccion", code)), False)
    for s in sorted(
        (s for s in everything if s.category in CATEGORY_TITLES), key=lambda s: (s.category, place_of(s).code)
    ):
        place = place_of(s)
        scope = (s.category, place.code)
        name, label = CATEGORY_TITLES[s.category]
        if scope in contests:
            raise ValueError(f"two sheets for {scope}: {s.file}")
        office = s.category in ("intendente", "comisionado")
        contest(
            scope,
            name.format(place.name),
            f"{label} {place.name}",
            "office" if office else "list_allocation",
            place.area,
            1 if office else None,
            office_id=mayor(place) if office else None,
            body_id=None if office else council(place),
        )
        record(scope, s, unit("ar-t.jurisdiccion", place.code, place.area), True)
    mesa_sets(p, year, tag, contests, sets, options, official)
    outcomes(p, tag, year, declared, contests, sets, options, party_of)
    blank = sum(not m.name for members in declared.values() for m in members)
    if blank:
        p.accept(
            "outcomes.list_members",
            f"The Junta's annex of proclaimed authorities prints {blank} members with no name, so those seats are "
            "declared for their lists without a named member.",
        )
    for scope, (counted, tables) in progress.items():
        rows = p.rows["result_set"]
        row = next(r for r in rows if r["id"] == sets[scope])
        row["completeness"] = round(counted / tables, 6) if tables else 1.0
    return p
