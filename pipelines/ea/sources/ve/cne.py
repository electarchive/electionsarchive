"""Venezuela's presidential elections from the National Electoral Council (CNE, formerly the Consejo Supremo
Electoral) and its result divulgations.

The CNE's statistics office printed the 6 December 1998 and 30 July 2000 presidential results by entity as PDFs. Its
divulgation sites for the 3 December 2006, 7 October 2012, and 14 April 2013 presidential elections and for the 20 May
2018 election show a national page and a page for each state, as snapshots of the CNE's own pages. The states are the
24 first-level units of the CNE's divulgations: 23 states and the Capital District (the Federal District before 1999).
Their shapes come from geoBoundaries' open set (2020 vintage).
"""

import functools
import html
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "ve.cne"
SHAPES = "ve.geoboundaries"
SHAPE_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/VEN/ADM1/"
    "geoBoundaries-VEN-ADM1.geojson"
)
WAYBACK = "https://web.archive.org/web/"
CNE = "http://www.cne.gob.ve/web/documentos/estadisticas/"
PDF_1998 = CNE + "e98_01.pdf"
PDF_2000 = CNE + "e015.pdf"
PDF_COMPARATIVE = CNE + "e006.pdf"
BUNDLE_2006 = "http://www.cne.gob.ve/divulgacionPresidencial/resultado_nacional.php?color=2&c2=0&e=NN"
PAGE_2018 = "http://www4.cne.gob.ve/ResultadosElecciones2018/"
# The snapshot of each 2012 and 2013 page that the archive holds, by the CNE's page code.
PAGES_2012 = {
    "00": "20240516070458id_/http://www.cne.gob.ve/resultado_presidencial_2012/r/1/reg_000000.html",
    "01": "20180623165422id_/http://www4.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_010000.html",
    "02": "20180802181527id_/http://www.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_020000.html",
    "03": "20180630144659id_/http://www4.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_030000.html",
    "04": "20201001042416id_/http://www.cne.gob.ve/resultado_presidencial_2012/r/1/reg_040000.html",
    "05": "20231122235113id_/http://www.cne.gob.ve/resultado_presidencial_2012/r/1/reg_050000.html",
    "06": "20240521102723id_/http://www.cne.gob.ve/resultado_presidencial_2012/r/1/reg_060000.html",
    "07": "20180730121245id_/http://www4.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_070000.html",
    "08": "20180730121418id_/http://www4.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_080000.html",
    "09": "20180531174815id_/http://www4.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_090000.html",
    "10": "20180730120629id_/http://www4.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_100000.html",
    "12": "20240626165539id_/http://www.cne.gob.ve/resultado_presidencial_2012/r/1/reg_120000.html",
    "13": "20240528182211id_/http://www.cne.gob.ve/resultado_presidencial_2012/r/1/reg_130000.html",
    "14": "20200109022348id_/http://www.cne.gob.ve/resultado_presidencial_2012/r/1/reg_140000.html",
    "15": "20180702031019id_/http://www4.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_150000.html",
    "18": "20210311121335id_/http://www.cne.gob.ve/resultado_presidencial_2012/r/1/reg_180000.html",
    "19": "20180725182041id_/http://www4.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_190000.html",
    "20": "20180729230547id_/http://www4.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_200000.html",
    "21": "20231118003045id_/http://www.cne.gob.ve/resultado_presidencial_2012/r/1/reg_210000.html",
    "22": "20240305042714id_/http://www.cne.gob.ve/resultado_presidencial_2012/r/1/reg_220000.html",
    "23": "20180730120956id_/http://www4.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_230000.html",
    "24": "20181214101238id_/http://www.cne.gob.ve:80/resultado_presidencial_2012/r/1/reg_240000.html",
    "99": "20201128012117id_/http://www.cne.gob.ve/resultado_presidencial_2012/r/1/reg_990000.html",
    "98": "20180730121413id_/http://www4.cne.gob.ve:80/resultado_presidencial_2012/r/0/reg_980000.html",
}
PAGES_2013 = {
    "00": "20240728022606id_/http://www.cne.gob.ve/resultado_presidencial_2013/r/1/reg_000000.html",
    "01": "20240411112458id_/http://www.cne.gob.ve/resultado_presidencial_2013/r/1/reg_010000.html",
}
VENEZUELA = str(uuid5(package.NAMESPACE, "polity\x1fVE"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fVEN"))
CNE_SOURCE_PUBLISHER = "Consejo Nacional Electoral"
STATE = "ve.estado"
COUNTRY = "ve.pais"
PRESIDENCY = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1foffice\x1fpresident"))
PLURALITY = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fpresident"))
VOTES, REGISTERED, CAST, VALID, REJECTED = 1, 2, 3, 4, 5
ABROAD = "abroad"
SINCE = date(1958, 1, 23)
TOLERANCE = 0.002
SHAPE_SOURCE = ("geoboundaries-ven-adm1", "CC BY 3.0 IGO", "https://creativecommons.org/licenses/by/3.0/igo/")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFD", html.unescape(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", plain.lower())


def titled(text: str) -> str:
    return " ".join(w.capitalize() for w in re.sub(r"\s+", " ", html.unescape(text)).strip().split())


def number(text: str) -> int:
    return int(re.sub(r"\D", "", text))


# What each source calls an area, folded, and the geoBoundaries state it is.
ALIASES = {
    "distritofederal": "VE-A",
    "dttocapital": "VE-A",
    "distritocapital": "VE-A",
    "edonvaesparta": "VE-O",
    "nvaesparta": "VE-O",
    "edodeltaamac": "VE-Y",
    "deltaamac": "VE-Y",
    "vargas": "VE-X",
    "laguaira": "VE-X",
}


def fetch() -> None:
    for url in (SHAPE_URL,):
        raw.fetch(SHAPES, url, suffix=".geojson")
    for url in (PDF_1998, PDF_2000, PDF_COMPARATIVE):
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in (BUNDLE_2006, PAGE_2018):
        raw.fetch(SOURCE, url, suffix=".html")
    for pages in (PAGES_2012, PAGES_2013):
        for stamp in pages.values():
            raw.fetch(SOURCE, WAYBACK + stamp, suffix=".html")


# --- the states -----------------------------------------------------------------------------------------------


@functools.cache
def states() -> dict[str, tuple[str, shapely.Geometry]]:
    """The geoBoundaries states by ISO 3166-2 code: name and shape."""
    data = json.loads(raw.cached(SHAPES, SHAPE_URL).path.read_text())
    out: dict[str, tuple[str, shapely.Geometry]] = {}
    for feature in data["features"]:
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        out[str(feature["properties"]["shapeISO"])] = (str(feature["properties"]["shapeName"]), shape)
    return out


@functools.cache
def state_names() -> dict[str, str]:
    """Each folded name that a source may use for a state, mapped to its ISO code."""
    names = {fold(name): code for code, (name, _) in states().items()}
    names.update(ALIASES)
    return names


def state_code(label: str) -> str:
    """The ISO code of the state a source's label names, such as `EDO. NVA.ESPARTA`, `Estado Zulia`, or `DTTO. CAPITAL`."""
    text = re.sub(r"^(estado|edo\.?)\s*", "", html.unescape(label).strip(), flags=re.IGNORECASE)
    found = state_names().get(fold(text)) or state_names().get(fold(label))
    if found is None:
        raise ValueError(f"{label!r} is not a Venezuelan state")
    return found


# --- what each source prints -----------------------------------------------------------------------------------


@dataclass
class Count:
    """One reporting scope's counts as printed: candidates' votes in ballot order and the ficha's totals."""

    name: str
    votes: dict[str, int] = field(default_factory=dict)
    registered: int | None = None
    cast: int | None = None
    valid: int | None = None
    rejected: int | None = None
    winner: str | None = None


def pdf_text(url: str) -> str:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    return unicodedata.normalize("NFC", text)


def last(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return number(match.group(1)) if match else None


def pdf_pages(url: str, title: str, header: str) -> list[Count]:
    """The scopes of a CNE statistics PDF: one page each, with candidates in capitals and their TOTAL lines."""
    out: list[Count] = []
    for page in pdf_text(url).split("\f"):
        match = re.search(header, page)
        if match is None:
            continue
        count = Count(re.sub(r"\s+", " ", match.group(1)).strip())
        current: str | None = None
        for line in page.splitlines():
            name = re.match(r"^([A-ZÑÁÉÍÓÚ][A-ZÑÁÉÍÓÚ .]+?)(?:\s{2,}|$)", line)
            if name and name.group(1) == name.group(1).upper() and len(name.group(1)) > 5:
                current = name.group(1).strip()
            total = re.match(r"^\s+TOTAL\s+([\d.]+)\s+[\d.,]+%", line)
            if total and current:
                count.votes[current] = number(total.group(1))
                current = None
        valid = re.search(r"Total Votos V[áa]lidos[^\n]*?\s([\d.]+)\s+100[.,]00%", page)
        count.valid = number(valid.group(1)) if valid else None
        count.registered = last(r"Inscritos en REP\s+([\d.]+)", page)
        count.rejected = last(r"Votos Nulos\s+([\d.]+)", page)
        count.cast = last(r"Votos Escrutados\s+([\d.]+)", page)
        out.append(count)
    return out


@functools.cache
def states_1998() -> list[Count]:
    return pdf_pages(PDF_1998, "1998", r"Votos V[áa]lidos(?: por Entidad)?\s*\n\s*(\S[^\n]*?)\s*\n\s+% Votos")


@functools.cache
def states_2000() -> list[Count]:
    return pdf_pages(PDF_2000, "2000", r"(?:TOTAL NACIONAL|TOTALES POR ENTIDAD)\s*\n\s*(\S[^\n]*?)\s*\n\s*\n\s+COD")


@functools.cache
def national_1998() -> Count:
    """The 6 December 1998 national total, from the CNE's comparative table of 1958-2000."""
    block = pdf_text(PDF_COMPARATIVE)
    start = block.index("TOTAL VENEZUELA ELECCIONES 6/12/1998")
    end = block.index("Población Electoral", start)
    count = Count("Venezuela")
    current: str | None = None
    party: tuple[str, int] | None = None
    for line in block[start:end].splitlines()[4:]:
        left = line[:66].rstrip()
        match = re.match(
            r"^([A-Z][A-Za-zñÑáéíóúÁÉÍÓÚ.]+(?: [A-Za-zñÑáéíóúÁÉÍÓÚ.]+)*)\s{2,}\S+\s+([\d.]+)\s+\d+,\d+", left
        )
        if match:
            if current and party:
                count.votes.setdefault(current, party[1])
            current, party = match.group(1), (left, number(match.group(2)))
            continue
        total = re.match(r"^\s+Total\s+([\d.]+)\s+\d+,\d+", left)
        if total and current:
            count.votes[current] = number(total.group(1))
            current, party = None, None
            continue
        rest = re.match(r"^\s+\S+\s+([\d.]+)\s+\d+,\d+", left)
        if rest and current and party:
            party = (party[0], party[1] + number(rest.group(1)))
        valid = re.match(r"^\s+Total Votos V[áa]lidos\s+([\d.]+)", left)
        if valid:
            count.valid = number(valid.group(1))
    if current and party:
        count.votes.setdefault(current, party[1])
    tail = block[end - 600 : end + 400]
    count.rejected = last(r"Total Votos Nulos\s+([\d.]+)", tail)
    count.cast = last(r"Total Votos Escrutados\s+([\d.]+)", tail)
    count.registered = last(r"Población Electoral\s+([\d.]+)", block[end:])
    return count


def flat(path: str) -> str:
    text = raw.cached(SOURCE, path).path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.DOTALL)
    text = html.unescape(re.sub(r"<[^>]+>", "|", text))
    return re.sub(r"(\| ?)+", "| ", re.sub(r"\s+", " ", text))


CANDIDATE = re.compile(r"\| ([A-ZÑÁÉÍÓÚ][A-ZÑÁÉÍÓÚ .]+?)\| (Adjudicado\| )?(\d[\d.]*)\| \d+,\d+%\| Votos\| %\|")


def divulgation(path: str) -> Count:
    """A scope of the 2012 and 2013 divulgations: candidates' votes and the ficha técnica."""
    text = flat(path)
    place = re.search(r"(?:Estado|Embajada|Zonas Inhóspitas): ([^|]+)\|", text)
    count = Count(place.group(1).strip() if place else "Venezuela")
    for name, winner, votes in CANDIDATE.findall(text):
        count.votes[name.strip()] = number(votes)
        if winner:
            count.winner = name.strip()
    count.registered = last(r"ELECTORES ESPERADOS\| ([\d.]+)", text)
    count.cast = last(r"VOTOS ESCRUTADOS\| ([\d.]+)", text)
    count.valid = last(r"VOTOS VÁLIDOS\| [\d,]+ ?%\| ([\d.]+)", text)
    count.rejected = last(r"VOTOS NULOS\| [\d,]+ ?%\| ([\d.]+)", text)
    return count


@functools.cache
def pages(year: int) -> dict[str, Count]:
    table = {2012: PAGES_2012, 2013: PAGES_2013}[year]
    return {code: divulgation(WAYBACK + stamp) for code, stamp in table.items()}


BALLOT = re.compile(r"\| ([A-ZÑÁÉÍÓÚ][A-ZÑÁÉÍÓÚ .]+?)\| [\d,]*%\| ([\d.]+) votos\| Ver detalle\|")


@functools.cache
def scopes_2006() -> dict[str, Count]:
    """The 2006 divulgation's national page, 24 state pages, and embassy page, by the scope's label."""
    text = flat(BUNDLE_2006)
    out: dict[str, Count] = {}
    for part in re.split(r"\| Candidatos\| Boleta\| Cómo Votar\| --> ", text)[1:]:
        label = re.search(r"Resultados Electorales\|(.*?)\| Ficha Técnica\|", part)
        if label is None:
            continue
        head = label.group(1)
        if "MUNICIPIO:" in head or "PARROQUIA:" in head or "CENTRO:" in head:
            continue
        if "Inhóspita" in head and "ESTADO:" not in head:
            continue
        state = re.search(r"ESTADO:\| ([^|]+)\|", head)
        abroad = "EXTERIOR" in head
        scope = state_code(state.group(1)) if state else (ABROAD if abroad else "VE")
        count = Count(scope)
        for name, votes in BALLOT.findall(part):
            count.votes[name.strip()] = number(votes)
        count.registered = last(r"Total Electores Inscritos: ([\d.]+)", part)
        count.cast = last(r"Total Votos Escrutados: ([\d.]+)", part)
        count.valid = last(r"Total Votos Válidos: ([\d.]+)", part)
        count.rejected = last(r"Total Votos Nulos: ([\d.]+)", part)
        out[scope] = count
    return out


@functools.cache
def national_2018() -> Count:
    text = raw.cached(SOURCE, PAGE_2018).path.read_text(encoding="utf-8", errors="replace")
    count = Count("Venezuela")
    for name, votes, winner in re.findall(r"<h3>([A-ZÑ .]+)</h3>\s*<h4> <b>VOTOS: ([\d.]+)(?:&nbsp;)*(<strong)?", text):
        count.votes[name.strip()] = number(votes)
        if winner:
            count.winner = name.strip()
    flat_text = flat(PAGE_2018)
    count.registered = last(r"ELECTORES INSCRITOS\| ([\d.]+)", flat_text)
    count.valid = last(r"VOTOS VÁLIDOS\| ([\d.]+)", flat_text)
    count.rejected = last(r"VOTOS NULOS\| ([\d.]+)", flat_text)
    count.cast = last(r"VOTOS ESCRUTADOS\| ([\d.]+)", flat_text)
    return count


# --- the packages ---------------------------------------------------------------------------------------------

CNE_SOURCES = {
    "pdf-1998": (PDF_1998, "Elecciones 6 de Diciembre 1998, Presidente de la República, Votos Válidos por Entidad"),
    "pdf-2000": (PDF_2000, "Elecciones 30 de Julio de 2000, Presidente de la República, Totales por Entidad"),
    "comparative": (PDF_COMPARATIVE, "Elecciones presidenciales, cuadro comparativo 1958-2000 (voto grande)"),
    "bundle-2006": (BUNDLE_2006, "Divulgación Presidencial 2006, resultados nacionales, estados y embajadas"),
    "divulgation-2012": (WAYBACK + PAGES_2012["00"], "Divulgación Elecciones Presidenciales, 07 de Octubre de 2012"),
    "divulgation-2013": (WAYBACK + PAGES_2013["00"], "Divulgación Elecciones Presidenciales, 14 de Abril de 2013"),
    "divulgation-2018": (PAGE_2018, "Divulgación Electoral 2018, Presidente de la República Bolivariana de Venezuela"),
}
GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"
ELECTIONS = {
    1998: date(1998, 12, 6),
    2000: date(2000, 7, 30),
    2006: date(2006, 12, 3),
    2012: date(2012, 10, 7),
    2013: date(2013, 4, 14),
    2018: date(2018, 5, 20),
}


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"{SOURCE}/presidential-{year}" for year in ELECTIONS)]


def area(code: str) -> str:
    return key("area", STATE, code)


def outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    shape = shapely.make_valid(shapely.simplify(shape, TOLERANCE))
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=key("source", SHAPE_SOURCE[0]),
    )


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Venezuela's 23 states, the Capital District, and the Federal Dependencies, with geoBoundaries shapes",
    )
    p.external |= {VENEZUELA, TERRITORY}
    sid = key("source", SHAPE_SOURCE[0])
    p.add(
        "source",
        id=sid,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen Venezuela ADM1 (2020)",
        url="https://www.geoboundaries.org/api/current/gbOpen/VEN/ADM1/",
        license=SHAPE_SOURCE[1],
        license_url=SHAPE_SOURCE[2],
        attribution="geoBoundaries, William & Mary geoLab; OCHA Venezuela, Instituto Nacional de Estadística",
    )
    p.artifact(raw.cached(SHAPES, SHAPE_URL), sid)
    p.add(
        "scheme", code=STATE, polity_id=VENEZUELA, kind="administrative", rank=1, noun="state", code_system="iso3166-2"
    )
    p.add(
        "scheme",
        code=COUNTRY,
        polity_id=VENEZUELA,
        kind="administrative",
        rank=0,
        noun="country",
        code_system="iso3166-1",
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", STATE, 2020),
        scheme_code=STATE,
        vintage="2020",
        valid_from=date(1998, 1, 1),
        as_of=date(2020, 1, 1),
    )
    for code, (name, shape) in sorted(states().items()):
        id = area(code)
        p.add("area", id=id, scheme_code=STATE)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, name, "en", "official")
        outline(p, id, shape)
    return p


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Venezuela's presidency and the plurality rule that elects it",
        depends=[GEO],
    )
    p.external |= {VENEZUELA, TERRITORY}
    p.add("office", id=PRESIDENCY, polity_id=VENEZUELA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Venezuela", "en", "official")
    p.name(PRESIDENCY, "Presidente de la República", "es", "native", False)
    p.add(
        "electoral_system",
        id=PLURALITY,
        polity_id=VENEZUELA,
        valid_from=SINCE,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", PLURALITY),
        entity_id=PLURALITY,
        kind="methodology",
        body_md="The president is elected by direct vote in a single round, and the candidate with the most votes "
        "wins (Constitution of 1961, art. 183, and Constitution of 1999, art. 228).",
        lang="en",
    )
    return p


def person_label(name: str) -> str:
    return titled(name)


def same(name: str, names: list[str]) -> str | None:
    """The one of `names` that is the printed name's spelling, or None."""
    import difflib

    folded = fold(name)
    best = max(names, key=lambda n: difflib.SequenceMatcher(None, folded, fold(n)).ratio())
    return best if difflib.SequenceMatcher(None, folded, fold(best)).ratio() >= 0.8 else None


class Builder:
    """Stages one election's contest, units, and values."""

    def __init__(self, p: Package, year: int, source: str, method: str, title: str) -> None:
        self.p = p
        self.year = year
        self.day = ELECTIONS[year]
        self.source = source
        self.path = f"ve/{self.day}-presidential"
        self.units: dict[str, str] = {}
        self.election = key("election", self.day)
        p.external |= {VENEZUELA, TERRITORY}
        p.add(
            "election",
            id=self.election,
            polity_id=VENEZUELA,
            kind="general",
            date_from=self.day,
            date_to=self.day,
            status="held",
        )
        p.name(self.election, title, "en", "official")
        p.add("slug", path=self.path, entity_id=self.election, is_current=True)
        self.contest = key("contest", self.day, "president")
        p.add(
            "contest",
            id=self.contest,
            election_id=self.election,
            kind="office",
            office_id=PRESIDENCY,
            district_area_id=TERRITORY,
            magnitude=1,
            round_number=1,
            electoral_system_id=PLURALITY,
            default_result_kind="counted",
        )
        p.name(self.contest, title, "en", "official")
        p.add("slug", path=f"{self.path}/president", entity_id=self.contest, is_current=True)
        self.result_set = key("result_set", self.day, "president")
        self.method = method
        self.options: dict[str, str] = {}

    def unit(self, code: str) -> str:
        known = self.units.get(code)
        if known is not None:
            return known
        id = key("unit", self.day, code)
        self.units[code] = id
        if code == "VE":
            self.p.add(
                "reporting_unit",
                id=id,
                polity_id=VENEZUELA,
                scheme_code=COUNTRY,
                code=f"{self.day}:national",
                area_relation="equals",
                area_id=TERRITORY,
                kind="aggregate",
            )
            return id
        outside = code == ABROAD or not code.startswith("VE-")
        self.p.add(
            "reporting_unit",
            id=id,
            polity_id=VENEZUELA,
            scheme_code=STATE,
            code=f"{self.day}:{code}",
            area_relation="unlocated" if outside else "equals",
            area_id=None if outside else area(code),
            kind="abroad_mission" if code == ABROAD else ("mobile" if outside else "standard"),
        )
        return id

    def candidates(self, names: list[str]) -> None:
        """Stage the candidates in the order given; each one's option is found by the spelling of its name."""
        p = self.p
        for position, name in enumerate(names, 1):
            option = key("option", self.day, fold(name))
            person = key("person", self.day, fold(name))
            label = person_label(name)
            p.add("person", id=person)
            p.name(person, label, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=self.contest,
                kind="candidate",
                party_id=None,
                ballot_position=position,
                ballot_label=label,
            )
            p.add(
                "candidacy",
                id=key("candidacy", self.day, fold(name), "head"),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
            )
            self.options[name] = option

    def close(self, national: Count, parts: list[Count]) -> None:
        """Stage the result set, whose completeness is the units' valid votes over the national valid votes."""
        p = self.p
        summed = sum(c.valid or 0 for c in parts)
        completeness = min(1.0, summed / national.valid) if national.valid else 1.0
        p.add(
            "result_set",
            id=self.result_set,
            election_id=self.election,
            contest_id=self.contest,
            stage="official",
            kind="counted",
            source_id=self.source,
            status="active",
            is_default=True,
            completeness=round(completeness, 6),
        )
        p.add(
            "note",
            id=key("note", self.result_set),
            entity_id=self.result_set,
            kind="methodology",
            body_md=self.method,
            lang="en",
        )

    def option(self, name: str) -> str:
        found = self.options.get(name) or self.options.get(same(name, list(self.options)) or "")
        if found is None:
            raise ValueError(f"{name!r} is not one of the candidates {list(self.options)}")
        return found

    def put(self, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        self.p.add(
            "result_value",
            result_set_id=self.result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )

    def scope(self, code: str, count: Count) -> None:
        unit = self.unit(code)
        for name, votes in count.votes.items():
            self.put(unit, self.option(name), VOTES, votes)
        self.put(unit, None, REGISTERED, count.registered)
        self.put(unit, None, CAST, count.cast)
        self.put(unit, None, VALID, count.valid)
        self.put(unit, None, REJECTED, count.rejected)

    def declare(self, count: Count) -> None:
        p = self.p
        for name, votes in count.votes.items():
            p.add("declared", result_set_id=self.result_set, option_id=self.option(name), measure=VOTES, value=votes)
        for measure, value in (
            (REGISTERED, count.registered),
            (CAST, count.cast),
            (VALID, count.valid),
            (REJECTED, count.rejected),
        ):
            if value is not None:
                p.add("declared", result_set_id=self.result_set, option_id=None, measure=measure, value=value)

    def outcomes(self, count: Count) -> None:
        top = max(count.votes.values())
        leaders = [name for name, votes in count.votes.items() if votes == top]
        if len(leaders) != 1:
            return
        if count.winner is not None and count.winner != leaders[0]:
            raise ValueError(f"the source awards {count.winner}, but {leaders[0]} has the most votes")
        for name in count.votes:
            self.p.add(
                "outcome",
                result_set_id=self.result_set,
                option_id=self.option(name),
                kind="elected" if name == leaders[0] else "lost",
                seats=1 if name == leaders[0] else 0,
            )


def source_for(p: Package, name: str, url: str, title: str, paths: list[str]) -> str:
    sid = key("source", name)
    p.add("source", id=sid, kind="official_body", publisher=CNE_SOURCE_PUBLISHER, title=title, url=url)
    for path in paths:
        p.artifact(raw.cached(SOURCE, path), sid)
    return sid


def check_sums(b: Builder, label: str, national: Count, parts: list[Count], extra: str) -> None:
    """Accept the accounting findings that the printed figures cause, naming each difference."""
    differ = []
    for what, printed, summed in (
        ("registered voters", national.registered, sum(c.registered or 0 for c in parts)),
        ("ballots cast", national.cast, sum(c.cast or 0 for c in parts)),
        ("valid votes", national.valid, sum(c.valid or 0 for c in parts)),
        ("rejected ballots", national.rejected, sum(c.rejected or 0 for c in parts)),
    ):
        if printed is not None and printed != summed:
            differ.append(f"{what} {printed:,} against {summed:,}")
    for name, votes in national.votes.items():
        summed = sum(v for c in parts for n, v in c.votes.items() if b.option(n) == b.option(name))
        if summed != votes:
            differ.append(f"{titled(name)} {votes:,} against {summed:,}")
    if differ:
        b.p.accept(
            "accounting.declared",
            f"{label}: the printed national figures are not the sum of the units' figures: "
            + "; ".join(differ)
            + ". "
            + extra,
        )


def canonical(counts: list[Count]) -> list[str]:
    """The candidates' names as the first scope that lists all of them spells them, in descending national order."""
    best = max(counts, key=lambda c: len(c.votes))
    return [name for name, _ in sorted(best.votes.items(), key=lambda item: -item[1])]


def elections(year: int) -> Package:
    p = Package(
        f"{SOURCE}/presidential-{year}",
        SOURCE,
        SUMMARIES[year],
        depends=[GEO, REGISTRY],
    )
    build = {1998: e1998, 2000: e2000, 2006: e2006, 2012: e2012, 2013: e2013, 2018: e2018}[year]
    build(p)
    return p


def pdf_election(p: Package, year: int, name: str, pdf: str, scopes: list[Count], national: Count, note: str) -> None:
    sid = source_for(p, name, CNE_SOURCES[name][0], CNE_SOURCES[name][1], [pdf, PDF_COMPARATIVE])
    b = Builder(p, year, sid, METHODS[year], f"{year} Venezuelan presidential election")
    b.candidates(canonical(scopes))
    parts = []
    for count in scopes:
        label = count.name
        if re.search(r"exterior|embajada", label, flags=re.IGNORECASE):
            code = ABROAD
        else:
            code = state_code(label)
        b.scope(code, count)
        parts.append(count)
    b.declare(national)
    b.outcomes(national)
    b.close(national, parts)
    check_sums(b, "National figures", national, parts, note)


def e1998(p: Package) -> None:
    scopes = states_1998()
    pdf_election(
        p,
        1998,
        "pdf-1998",
        PDF_1998,
        scopes,
        national_1998(),
        "The national table counts the 23 states and the Federal District; the page on votes abroad adds 1,060 valid "
        "votes that the table does not include.",
    )


def e2000(p: Package) -> None:
    scopes = states_2000()
    national, parts = scopes[0], scopes[1:]
    pdf_election(
        p,
        2000,
        "pdf-2000",
        PDF_2000,
        parts,
        national,
        "The national page prints the whole country.",
    )


def e2006(p: Package) -> None:
    sid = source_for(p, "bundle-2006", *CNE_SOURCES["bundle-2006"], [BUNDLE_2006])
    scopes = scopes_2006()
    national = scopes["VE"]
    b = Builder(p, 2006, sid, METHODS[2006], "2006 Venezuelan presidential election")
    b.candidates(canonical([national]))
    parts = []
    for code, count in sorted(scopes.items()):
        if code == "VE":
            continue
        b.scope(code, count)
        parts.append(count)
    b.declare(national)
    b.outcomes(national)
    b.close(national, parts)
    check_sums(
        b,
        "2006",
        national,
        parts,
        "The national page counts 99.08% of the tally sheets and each state page about 99%, because the pages "
        "were refreshed at different moments.",
    )


def divulgation_election(p: Package, year: int, pages_: dict[str, Count], note: str) -> None:
    name = f"divulgation-{year}"
    paths = [WAYBACK + stamp for stamp in {2012: PAGES_2012, 2013: PAGES_2013}[year].values()]
    sid = source_for(p, name, CNE_SOURCES[name][0], CNE_SOURCES[name][1], paths)
    national = pages_["00"]
    b = Builder(p, year, sid, METHODS[year], f"{year} Venezuelan presidential election")
    b.candidates(canonical([national]))
    parts = []
    for page, count in sorted(pages_.items()):
        if page == "00":
            continue
        if page == "99":
            code = ABROAD
        elif page == "98":
            code = "inhospitable"
        else:
            code = state_code(count.name)
        b.scope(code, count)
        parts.append(count)
    b.declare(national)
    b.outcomes(national)
    b.close(national, parts)
    check_sums(b, str(year), national, parts, note)


def e2012(p: Package) -> None:
    divulgation_election(
        p,
        2012,
        pages(2012),
        "The archive holds the pages of 21 of the 24 first-level units; those of Lara, Portuguesa, and Sucre were "
        "not reachable.",
    )


def e2013(p: Package) -> None:
    divulgation_election(
        p,
        2013,
        pages(2013),
        "The archive holds only the page of the Capital District; the pages of the 23 states and the votes abroad "
        "were not reachable.",
    )


def e2018(p: Package) -> None:
    sid = source_for(p, "divulgation-2018", *CNE_SOURCES["divulgation-2018"], [PAGE_2018])
    national = national_2018()
    b = Builder(p, 2018, sid, METHODS[2018], "2018 Venezuelan presidential election")
    b.candidates(canonical([national]))
    b.scope("VE", national)
    b.declare(national)
    b.outcomes(national)
    b.close(national, [national])
    p.accept(
        "accounting.votes_valid",
        "The divulgation page prints the votes of two candidates, Nicolás Maduro and Henri Falcón, and prints valid "
        f"votes of {national.valid or 0:,}, which is more than the {sum(national.votes.values()):,} votes of those "
        "two; the page does not list the other candidates.",
    )


SUMMARIES = {
    1998: "The 6 December 1998 presidential election by state and abroad",
    2000: "The 30 July 2000 presidential election by state and abroad",
    2006: "The 3 December 2006 presidential election by state and abroad",
    2012: "The 7 October 2012 presidential election by state, with the national total",
    2013: "The 14 April 2013 presidential election: the national total and the Capital District",
    2018: "The 20 May 2018 presidential election: the national total",
}
COMMON = (
    "Counted by the National Electoral Council (the Consejo Supremo Electoral until 1999) and printed on its "
    "result pages. Votes are each candidate's valid votes summed over the parties and alliances that nominated "
    "them; the votes by party are not stored. The candidate with the most votes is stored as elected. "
)
METHODS = {
    1998: COMMON
    + "The source is the statistics office's PDF of valid votes by entity, with a page for votes cast in the "
    "exterior, and its comparative table of 1958-2000 for the national total, which excludes the exterior. "
    "Registered voters are 'Inscritos en REP', ballots cast are 'Votos Escrutados' (valid and null votes), "
    "and rejected ballots are null votes. The Federal District is stored as the Capital District. "
    "Votes by municipality, parish, voting center, and table are not stored.",
    2000: COMMON
    + "The source is the statistics office's PDF of totals by entity, whose first page is the national total. "
    "Registered voters are 'Inscritos en REP', ballots cast are 'Votos Escrutados', and rejected ballots are null "
    "votes. Votes by municipality, parish, voting center, and table are not stored.",
    2006: COMMON
    + "The source is the divulgation site's national page, its 24 state pages, and its page on votes in embassies "
    "abroad, which counted 50.24% of its tally sheets. The pages were saved at "
    "different moments, so the state pages count slightly fewer tally sheets than the national page. Registered "
    "voters are the electors enrolled, and ballots cast are the votes counted (valid and null). Votes by "
    "municipality, parish, and voting center on the same site are not stored.",
    2012: COMMON
    + "The source is the divulgation site's national page and the pages of 21 of the 24 first-level units, "
    "as snapshots captured on various dates, with the embassies and the remote zones (zonas inhóspitas) as "
    "units of their own. The pages of Lara, Portuguesa, and Sucre were not reachable. Registered voters are the "
    "electors expected, and ballots cast are the votes counted (valid and null). Votes by municipality, parish, "
    "voting center, and table are not stored. Hugo Chávez was proclaimed the winner.",
    2013: COMMON
    + "The source is the divulgation site's national page and its page of the Capital District, as snapshots; "
    "the other states' pages were not reachable. Registered voters are the electors expected, and ballots "
    "cast are the votes counted (valid and null). Nicolás Maduro was proclaimed the winner.",
    2018: COMMON
    + "The source is the divulgation page updated on 28 May 2018, which prints the national total of the two "
    "candidates whom it lists, Nicolás Maduro, who is marked as awarded the office, and Henri Falcón, "
    "and the election's ficha técnica. The page lists no other candidates and no results below the nation; the "
    "votes shown for the two do not add up to its valid votes.",
}


def parse() -> None:
    geo().write()
    registry().write()
    for year in ELECTIONS:
        elections(year).write()


def emit() -> None:
    package.run("load", packages())
