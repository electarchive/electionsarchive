import functools
import html
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import httpx
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "ar.caba"
TSJ = "https://www.eleccionesciudad.gob.ar"
TE = "https://resultados.electoralcaba.gob.ar"
COMUNAS_URL = "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/ministerio-de-educacion/comunas/comunas.geojson"
ARGENTINA = str(uuid5(package.NAMESPACE, "polity\x1fAR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fARG"))
COMUNAS = range(1, 16)
CITY_CODE = "caba"


DOCS = f"{TSJ}/uploads/resoluciones/"
PASO_HEAD = "Resolucion_Escrutinio_Jefe_de_Gobierno.pdf"
PASO_LEGISLATURE = "Resolucion_Escrutinio_Diputados.pdf"
PASO_JUNTAS = "Resolucion_Escrutinio_Miembros_de_Juntas_Comunales.pdf"
GENERAL_NATIONAL = "ESCRUTINIO_ELECTORES_NACIONALES..PDF"
GENERAL_FOREIGN = "Resultados_Definitivos_por_Comunas.pdf"
GENERAL_CITY = "Resultados_Generales_Jefe_de_Gobierno_y_Diputados_final.pdf"


@dataclass(frozen=True)
class Held:
    key: str
    day: date
    kind: str
    page: int | None  # the id of the election on the Tribunal Superior's results service, if it publishes one
    docs: tuple[str, ...] = ()  # the resolutions (PDF) of the Tribunal Electoral that carry its count, if it has them
    prison: bool = False  # whether the count lists the prisoners' tables apart from the comunas


# The elections of the City held under the Constitution of 1996 that this pipeline builds, each by comuna.
HELD = [
    Held("2015-paso", date(2015, 4, 26), "primary", 3),
    Held("2015-general", date(2015, 7, 5), "general", 4),
    Held("2015-runoff", date(2015, 7, 19), "runoff", 7),
    Held(
        "2023-paso",
        date(2023, 8, 13),
        "primary",
        None,
        (PASO_HEAD, PASO_LEGISLATURE, PASO_JUNTAS),
    ),
    Held(
        "2023-general",
        date(2023, 10, 22),
        "general",
        None,
        (GENERAL_NATIONAL, GENERAL_FOREIGN, GENERAL_CITY),
        True,
    ),
    Held("2025-general", date(2025, 5, 18), "general", None, (), True),
]


def source_of(held: Held) -> str:
    return (
        TEC_SOURCE if held.docs and held.key.startswith("2023") else TSJ_SOURCE if held.page is not None else TE_SOURCE
    )


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def tsj_url(held: Held, comuna: int | None = None) -> str:
    if comuna is None:
        return f"{TSJ}/resultados/eleccionReloaded/{held.page}/?tab=totales"
    return f"{TSJ}/resultados/eleccionReloaded/{held.page}/{comuna}/0/0/0"


def te_url(comuna: int | None = None) -> str:
    return f"{TE}/index.php" if comuna is None else f"{TE}/seccion/?{comuna}"


PRISONS_URL = f"{TE}/mesa/?30999"


def urls(held: Held) -> list[str]:
    if held.docs:
        return [DOCS + name for name in held.docs]
    if held.page is None:
        return [te_url(), *(te_url(c) for c in COMUNAS), PRISONS_URL]
    return [tsj_url(held), *(tsj_url(held, c) for c in COMUNAS)]


def fetch() -> None:
    raw.fetch(SOURCE, COMUNAS_URL, suffix=".geojson")
    for held in HELD:
        for url in urls(held):
            try:
                raw.fetch(SOURCE, url, suffix=".pdf" if held.docs else ".html")
            except httpx.HTTPStatusError as failure:
                print(f"{url}: {failure.response.status_code}", flush=True)


@dataclass
class Row:
    number: str
    name: str
    votes: list[int]
    lists: list[Lista]


@dataclass
class Lista:
    code: str
    name: str
    votes: list[int]


@dataclass
class Page:
    cargos: list[str]
    rows: list[Row]
    totals: dict[str, list[int]]
    registered: int | None


CARGOS = (("JEFE", "head"), ("DIPUTAD", "legislature"), ("JUNTA", "junta"))
TOTAL_LABELS = {
    "Votos Positivos": "valid",
    "Votos en Blanco": "blank",
    "Votos Nulos": "null",
    "Votos Recurridos": "challenged",
    "Votos Impugnados": "contested",
    "Total de votantes": "cast",
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def number(text: str) -> int:
    digits = text.replace(".", "").strip()
    return int(digits) if digits.isdigit() else 0


def _table(text: str, marker: str) -> list[tuple[str, list[str]]]:
    start = text.find(marker)
    if start < 0:
        raise LookupError(f"no {marker} table")
    end = text.find("</table>", start)
    return [
        (attrs, [clean(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", body, re.DOTALL)])
        for attrs, body in re.findall(r"<tr([^>]*)>(.*?)</tr>", text[start:end], re.DOTALL)
    ]


def tsj_page(url: str) -> Page:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    rows = _table(text, '<table class="rg-table"')
    cargos = [next(code for word, code in CARGOS if word in cell.upper()) for cell in rows[0][1][2:] if cell.strip()]
    registered = re.search(r"Total inscriptos al Padr[^<]*<[^>]*>\s*(?:<[^>]*>\s*)*([\d.]+)", text)
    page = Page(cargos, [], {}, number(registered.group(1)) if registered else None)
    for attrs, cells in rows[1:]:
        if len(cells) < 2 + 3 * len(cargos) - 2 or "Lista" in cells[0]:
            continue
        votes = [number(cells[2 + 3 * k]) if 2 + 3 * k < len(cells) else 0 for k in range(len(cargos))]
        if "partidopolitico" in attrs:
            page.rows.append(Row(cells[0], cells[1], votes, []))
        elif "internapolitica" in attrs:
            code = re.search(r"\bin(\d+)", attrs)
            assert code is not None
            page.rows[-1].lists.append(Lista(code.group(1), cells[1], votes))
        elif "totalrow" in attrs and cells[1] in TOTAL_LABELS:
            page.totals[TOTAL_LABELS[cells[1]]] = votes
    return page


TE_LABELS = {
    "VOTOS TOTALES": "cast",
    "Votos válidos": "valid",
    "Votos en blanco": "blank",
    "Votos impugnados": "contested",
    "Votos nulos": "null",
    "Votos recurridos": "challenged",
}


def te_page(url: str) -> Page:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    page = Page(["legislature"], [], {}, None)
    for _, cells in _table(text, "<table"):
        if len(cells) == 2 and cells[0] in TE_LABELS:
            page.totals[TE_LABELS[cells[0]]] = [number(cells[1])]
    tables = text.split("<table")
    body = tables[2] if len(tables) > 2 else ""
    for _, cells in _table("<table" + body, "<table"):
        if len(cells) >= 4 and cells[0].isdigit():
            page.rows.append(Row(cells[0], cells[1], [number(cells[3])], []))
    if not page.rows or len(page.totals) != 6:
        raise ValueError(f"{url}: unexpected page shape")
    return page


PARTY_NAMES = {
    "40": "Movimiento Libres del Sur",
    "72": "Instrumento Electoral por la Unidad Popular",
    "92": "Política Obrera",
    "95": "Frente Patriota Federal",
    "347": "Aptitud Renovadora",
    "350": "Izquierda en la Ciudad",
    "351": "Principios y Valores",
    "801": "La Libertad Avanza",
    "802": "Unión por la Patria",
    "803": "Juntos por el Cambio",
    "804": "Frente de Izquierda y de Trabajadores - Unidad",
}
LIST_NAMES = {
    ("40", 1): "Alternativa Progresista",
    ("72", 1): "El Movimiento - La Ciudad Somos Quienes la Habitamos",
    ("92", 1): "Unidad Obrera",
    ("95", 1): "Primero la Patria",
    ("347", 1): "Primero los Porteños",
    ("350", 1): "Izquierda Anticapitalista",
    ("351", 1): "Celeste y Blanca",
    ("801", 1): "La Ciudad Avanza",
    ("802", 1): "Alternativa para Ganar Buenos Aires",
    ("803", 1): "A - Evolución",
    ("803", 2): "B - Vayamos por Más",
    ("804", 1): "A - Unir y Fortalecer la Izquierda",
    ("804", 2): "B - Unidad de Luchadores y la Izquierda",
}
ALLIANCES = ("801", "802", "803", "804")
JUNTA_ONLY = ("351",)  # lists that ran for the Community Board of one comuna only, in the last column
GENERAL_CARGOS = ["head", "legislature", "junta"]


@functools.cache
def pdf_text(name: str) -> str:
    path = raw.cached(SOURCE, DOCS + name).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def whole_numbers(line: str) -> list[int]:
    return [
        int(token) for token in re.findall(r"(?<![\w,.])\d+(?![\w,.])", re.sub(r"%\s*\d+(?:,\d+)?|\d+,\d+%?", "", line))
    ]


def pdf_totals(blank: list[int], null: list[int], cast: list[int]) -> dict[str, list[int]]:
    return {"blank": blank, "null": null, "cast": cast, "challenged": [0], "contested": [0]}


def _positive(rows: list[Row], totals: dict[str, list[int]]) -> None:
    totals["valid"] = [
        cast - null - blank for cast, null, blank in zip(totals["cast"], totals["null"], totals["blank"], strict=True)
    ]
    for k, valid in enumerate(totals["valid"]):
        listed = sum(row.votes[k] for row in rows)
        if listed != valid:
            raise ValueError(f"the lists add up to {listed}, the totals leave {valid} positive votes")


def electors_pages(name: str) -> dict[int, Page]:
    """One block per comuna of a definitive count of electors: the alliances 801 to 804 in the last three columns
    (Head of Government, Legislature, Community Board), then null and blank votes and the total."""
    text = pdf_text(name)
    parts = re.split(r"CONSULTA DEFINITIVA - SECCION:\s*\d+ COMUNA (\d+)", text)
    pages: dict[int, Page] = {}
    for comuna, body in zip(parts[1::2], parts[2::2], strict=True):
        registered = re.search(r"Total de inscriptos:\s*(\d+)", body)
        assert registered is not None
        rows: list[Row] = []
        found: dict[str, list[int]] = {}
        for line in body.splitlines():
            figures = whole_numbers(line)
            label = line.strip()
            if label.startswith("VOTOS NULOS"):
                found["null"] = figures[-3:]
            elif label.startswith("VOTOS EN BLANCO"):
                found["blank"] = figures[-3:]
            elif label.startswith("TOTALES"):
                found["cast"] = figures[-3:]
            elif figures and str(figures[0]) in ALLIANCES:
                rows.append(Row(str(figures[0]), PARTY_NAMES[str(figures[0])], figures[-3:], []))
            elif len(figures) == 2 and str(figures[0]) in JUNTA_ONLY:
                rows.append(Row(str(figures[0]), PARTY_NAMES[str(figures[0])], [0, 0, figures[1]], []))
        if [row.number for row in rows if row.number in ALLIANCES] != list(ALLIANCES) or set(found) != {
            "null",
            "blank",
            "cast",
        }:
            raise ValueError(f"{name}: comuna {comuna} has an unexpected shape")
        totals = pdf_totals(found["blank"], found["null"], found["cast"])
        _positive(rows, totals)
        pages[int(comuna)] = Page(list(GENERAL_CARGOS), rows, totals, int(registered.group(1)))
    if sorted(pages) != list(COMUNAS):
        raise ValueError(f"{name}: expected the 15 comunas, found {sorted(pages)}")
    return pages


def _sum_pages(first: Page, second: Page) -> Page:
    numbers = sorted({row.number for row in [*first.rows, *second.rows]}, key=int)
    rows = []
    for number in numbers:
        found = [row for page in (first, second) for row in page.rows if row.number == number]
        rows.append(
            Row(number, found[0].name, [sum(votes) for votes in zip(*(r.votes for r in found), strict=True)], [])
        )
    totals = {label: [x + y for x, y in zip(v, second.totals[label], strict=True)] for label, v in first.totals.items()}
    assert first.registered is not None and second.registered is not None
    return Page(first.cargos, rows, totals, first.registered + second.registered)


def city_general_pages() -> tuple[Page, Page]:
    """The city totals by alliance and office, with each column of electors: Argentines, prisoners among them,
    foreign residents, and prisoners among them. Returns the city's count and the prisoners' count."""
    text = pdf_text(GENERAL_CITY)
    offices = re.split(r"ELECCIONES GENERALES DEL 22 DE OCTUBRE DE 2023", text)[1:]
    if len(offices) != 2:
        raise ValueError("expected the Head of Government and the Deputies")
    city_rows: list[list[Row]] = []
    prison_rows: list[list[Row]] = []
    city_totals: list[dict[str, list[int]]] = []
    prison_totals: list[dict[str, list[int]]] = []
    for office in offices:
        rows, prisons = [], []
        found: dict[str, tuple[int, int]] = {}
        for line in office.splitlines():
            figures = whole_numbers(line)
            if len(figures) != 5:
                continue
            native, native_prison, foreign, foreign_prison, total = figures
            if native + native_prison + foreign + foreign_prison != total:
                raise ValueError(f"{line.strip()}: the columns do not add up")
            label = line.strip()
            if label.startswith("Votos Nulos"):
                found["null"] = (total, native_prison + foreign_prison)
            elif label.startswith("Votos Blancos"):
                found["blank"] = (total, native_prison + foreign_prison)
            elif label.startswith("Votos Totales"):
                found["cast"] = (total, native_prison + foreign_prison)
            else:
                number = ALLIANCES[len(rows)]
                rows.append(Row(number, PARTY_NAMES[number], [total], []))
                prisons.append(Row(number, PARTY_NAMES[number], [native_prison + foreign_prison], []))
        if len(rows) != 4 or set(found) != {"null", "blank", "cast"}:
            raise ValueError("unexpected shape of the city totals")
        city_rows.append(rows)
        prison_rows.append(prisons)
        city_totals.append(pdf_totals([found["blank"][0]], [found["null"][0]], [found["cast"][0]]))
        prison_totals.append(pdf_totals([found["blank"][1]], [found["null"][1]], [found["cast"][1]]))
    cargos = ["head", "legislature"]

    def merge(rows: list[list[Row]], totals: list[dict[str, list[int]]]) -> Page:
        merged = [Row(a.number, a.name, [a.votes[0], b.votes[0]], []) for a, b in zip(rows[0], rows[1], strict=True)]
        combined = {label: [totals[0][label][0], totals[1][label][0]] for label in totals[0]}
        _positive(merged, combined)
        return Page(cargos, merged, combined, None)

    return merge(city_rows, city_totals), merge(prison_rows, prison_totals)


def _paso_rows(text: str, columns: int) -> tuple[list[Row], dict[str, list[int]], int]:
    """The table of one office in a primary resolution: the lists by number, with their votes by kind of elector
    (columns of them), and below them blank, null, and the totals. Returns the rows, the totals, and the registered."""
    registered = re.search(r"Total de inscriptos:\s*(\d+)", text)
    assert registered is not None
    body = text[text.index("Lista n") :]
    rows: list[Row] = []
    lists: list[tuple[int, str]] = []
    found: dict[str, int] = {}
    for line in body.splitlines()[1:]:
        label = line.strip()
        figures = whole_numbers(line)
        if not figures:
            continue
        if label.startswith("VOTOS EN BLANCO"):
            found["blank"] = figures[-1]
        elif label.startswith("VOTOS NULOS"):
            found["null"] = figures[-1]
        elif label.startswith("TOTAL VOTOS EMITIDOS"):
            found["cast"] = figures[-1]
        elif label.startswith("TOTAL VOTOS VALIDOS"):
            found["valid"] = figures[-1]
        elif len(figures) == columns + 3:
            number = str(figures[0])
            rows.append(
                Row(number, PARTY_NAMES[number], [figures[-1]], [Lista("1", LIST_NAMES[(number, 1)], [figures[-1]])])
            )
            if sum(figures[1 : columns + 1]) != figures[columns + 1]:
                raise ValueError(f"{line.strip()}: the columns do not add up")
        elif len(figures) == 2:
            number = str(figures[0])
            rows.append(Row(number, PARTY_NAMES[number], [figures[1]], []))
        elif len(figures) == columns + 1:
            if sum(figures[:columns]) != figures[columns]:
                raise ValueError(f"{line.strip()}: the columns do not add up")
            lists.append((figures[columns], label))
    for row in rows:
        if row.lists:
            continue
        for ordinal in (1, 2):
            if not lists:
                raise ValueError(f"no lists left for {row.number}")
            votes, _ = lists.pop(0)
            row.lists.append(Lista(str(ordinal), LIST_NAMES[(row.number, ordinal)], [votes]))
        if sum(lista.votes[0] for lista in row.lists) != row.votes[0]:
            raise ValueError(f"the lists of {row.number} do not add up to the alliance")
    if lists or set(found) != {"blank", "null", "cast", "valid"}:
        raise ValueError("unexpected shape of the primary table")
    totals = pdf_totals([found["blank"]], [found["null"]], [found["cast"]])
    if found["cast"] - found["null"] != found["valid"]:
        raise ValueError("the printed valid votes do not match the cast and null votes")
    _positive(rows, totals)
    return rows, totals, int(registered.group(1))


@functools.cache
def paso_city_page() -> Page:
    cargos, rows, totals, registered = [], [], {}, []
    for name, cargo in ((PASO_HEAD, "head"), (PASO_LEGISLATURE, "legislature")):
        office_rows, office_totals, count = _paso_rows(pdf_text(name), 3)
        cargos.append(cargo)
        registered.append(count)
        rows.append(office_rows)
        for label, values in office_totals.items():
            totals.setdefault(label, []).extend(values)
    if len(set(registered)) != 1:
        raise ValueError("the two offices disagree on the registered voters")
    merged: list[Row] = []
    for number in sorted({row.number for office in rows for row in office}, key=int):
        found = [next((row for row in office if row.number == number), None) for office in rows]
        present = [row for row in found if row is not None]
        merged.append(
            Row(
                number,
                present[0].name,
                [row.votes[0] if row else 0 for row in found],
                [
                    Lista(lista.code, lista.name, [_vote(row, lista.code) for row in found])
                    for lista in present[0].lists
                ],
            )
        )
    return Page(cargos, merged, totals, registered[0])


def _vote(row: Row | None, code: str) -> int:
    return next((lista.votes[0] for lista in row.lists if lista.code == code), 0) if row else 0


@functools.cache
def paso_junta_pages() -> dict[int, Page]:
    parts = re.split(r"MIEMBROS DE LA JUNTA COMUNAL N° (\d+)", pdf_text(PASO_JUNTAS))
    pages: dict[int, Page] = {}
    for comuna, body in zip(parts[1::2], parts[2::2], strict=True):
        rows, totals, registered = _paso_rows(body.split("TRIBUNAL ELECTORAL DE LA CIUDAD")[0], 2)
        pages[int(comuna)] = Page(["junta"], rows, totals, registered)
    if sorted(pages) != list(COMUNAS):
        raise ValueError(f"expected the 15 comunas, found {sorted(pages)}")
    return pages


@functools.cache
def general_pages() -> dict[int | None, Page]:
    national = electors_pages(GENERAL_NATIONAL)
    foreign = electors_pages(GENERAL_FOREIGN)
    pages: dict[int | None, Page] = {c: _sum_pages(national[c], foreign[c]) for c in COMUNAS}
    city, prison = city_general_pages()
    pages[None] = city
    pages[0] = prison
    return pages


def pdf_page(held: Held, comuna: int | None, prison: bool) -> Page:
    if held.kind == "primary":
        return paso_junta_pages()[comuna] if comuna else paso_city_page()
    return general_pages()[0 if prison else comuna]


def page_of(held: Held, comuna: int | None, prison: bool = False) -> Page:
    if held.docs:
        return pdf_page(held, comuna, prison)
    if held.page is None:
        return te_page(PRISONS_URL if prison else te_url(comuna))
    return tsj_page(tsj_url(held, comuna))


CITY = area("ar.caba_ciudad", CITY_CODE)
TSJ_SOURCE = key("source", "tsj")
TE_SOURCE = key("source", "te")
TEC_SOURCE = key("source", "te.resoluciones")
GCBA_SOURCE = key("source", "gcba.comunas")
HEAD = key("office", "jefe-de-gobierno")
LEGISLATURE = key("body", "legislatura")
HEAD_SYSTEM = key("electoral_system", "jefe-de-gobierno")
LEGISLATURE_SYSTEM = key("electoral_system", "legislatura")
JUNTA_SYSTEM = key("electoral_system", "junta-comunal")
COMUNAS_FROM = date(2005, 9, 1)
SYSTEMS = [
    (
        HEAD_SYSTEM,
        date(1996, 1, 1),
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "The Head of Government and the Deputy Head are elected together on one ticket, with the City as a single "
            "district, by an absolute majority of the affirmative votes. If no ticket reaches it, the two leading tickets "
            "go to a second round."
        ),
    ),
    (
        LEGISLATURE_SYSTEM,
        date(1996, 1, 1),
        {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}},
        (
            "The 60 members of the Legislature are elected from closed lists by proportional representation (D'Hondt), "
            "half of them every two years, with the City as a single district. Only lists with at least 3% of the "
            "registered voters take part in the allocation, a threshold of registered voters that these rules do not "
            "encode."
        ),
    ),
    (
        JUNTA_SYSTEM,
        COMUNAS_FROM,
        {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}},
        (
            "Each of the 15 comunas elects a Junta Comunal of seven members by proportional representation, with the "
            "comuna as a single district, on the day of the election of the Head of Government."
        ),
    ),
]
METHOD = (
    "Counted by the electoral authority of the City of Buenos Aires and published by comuna on its results service. "
    "Each comuna is one reporting unit, and its figures include the polling tables of Argentine voters, of foreign "
    "residents, and of prisoners that the authority assigns to it. Valid votes are the votes for lists (the "
    "authority's positive votes), and ballots cast are valid, blank, null, and challenged or contested ballots "
    "together. Challenged and contested ballots are stored as null ballots and as the objected measure. The "
    "service does not label the count's stage. A list that received no votes in a unit is not shown by the service "
    "there, and is stored as zero only where it received votes elsewhere in the same contest."
)
PRIMARY_METHOD = (
    " In the open primaries (PASO), the options are the lists that each party or alliance ran for the office, and "
    "the votes are stored on the lists, whose party or alliance is their parent option."
)
PDF_METHOD = (
    "Counted by the electoral authority of the City of Buenos Aires and published in the resolutions of its Tribunal "
    "Electoral, with the definitive count of electors by comuna and the totals of the City. Each comuna is one "
    "reporting unit. Valid votes are the votes for lists (the authority's positive votes), and ballots cast are "
    "valid, blank, and null ballots together. Where the authority prints one count of Argentine electors and another "
    "of foreign residents by comuna, the unit is their sum. A list that received no votes in a unit is not shown "
    "there, and is stored as zero only where it received votes elsewhere in the same contest."
)
PRIMARY_DOCS_METHOD = (
    " The resolutions for the primaries give the Head of Government and the Legislature only as totals of the City, "
    "and the Community Boards by comuna, so the first two are stored as declared totals and on one unit that is the "
    "whole City."
)
PRISON_METHOD = (
    " The prisoners' tables, which the service lists apart from the comunas, are one more unit with no area."
)


def party(code: str, year: int) -> str:
    return key("party", code) if int(code) < 700 else key("party", year, code)


def _names() -> dict[tuple[str, int], str]:
    names: dict[tuple[str, int], str] = {}
    for held in HELD:
        for comuna in COMUNAS:
            for row in page_of(held, comuna).rows:
                names.setdefault((row.number, held.day.year), row.name)
    return names


def _shapes() -> dict[int, shapely.Geometry]:
    import json

    data = json.loads(raw.cached(SOURCE, COMUNAS_URL).path.read_text())
    shapes = {
        int(feature["properties"]["comuna"]): shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        for feature in data["features"]
    }
    if sorted(shapes) != list(COMUNAS):
        raise ValueError(f"expected the 15 comunas, found {sorted(shapes)}")
    return shapes


def _outline(p: Package, id: str, shape: shapely.Geometry, since: date | None) -> None:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    shape = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=since,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=GCBA_SOURCE,
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The City of Buenos Aires and its 15 comunas, with the comuna boundaries of Law 1777 from the City's open data",
    )
    p.external |= {ARGENTINA, COUNTRY}
    artifact = raw.cached(SOURCE, COMUNAS_URL)
    p.add(
        "source",
        id=GCBA_SOURCE,
        kind="official_body",
        publisher="Gobierno de la Ciudad de Buenos Aires",
        title="Comunas (BA Data)",
        url="https://data.buenosaires.gob.ar/dataset/comunas",
        license="CC BY 2.5 AR",
        license_url="https://creativecommons.org/licenses/by/2.5/ar/",
        attribution="Gobierno de la Ciudad de Buenos Aires, BA Data",
    )
    p.artifact(artifact, GCBA_SOURCE)
    p.add("scheme", code="ar.caba_ciudad", polity_id=ARGENTINA, kind="administrative", rank=1, noun="city")
    p.add("scheme", code="ar.caba_comuna", polity_id=ARGENTINA, kind="administrative", rank=2, noun="comuna")
    p.add(
        "boundary_set",
        id=key("boundary_set", "ar.caba_comuna", "ley1777"),
        scheme_code="ar.caba_comuna",
        vintage="Ley 1777",
        valid_from=COMUNAS_FROM,
        as_of=artifact.retrieved_at.date(),
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", "ar.caba_ciudad", "ley1777"),
        scheme_code="ar.caba_ciudad",
        vintage="Ley 1777",
        valid_from=COMUNAS_FROM,
        as_of=artifact.retrieved_at.date(),
    )
    shapes = _shapes()
    p.add("area", id=CITY, scheme_code="ar.caba_ciudad")
    p.add("area_code", area_id=CITY, code=CITY_CODE)
    p.add("area_parent", area_id=CITY, parent_id=COUNTRY)
    p.name(CITY, "Ciudad Autónoma de Buenos Aires", "es", "official")
    p.name(CITY, "City of Buenos Aires", "en", "official", False)
    _outline(p, CITY, shapely.union_all(list(shapes.values()), grid_size=1e-7), COMUNAS_FROM)
    for comuna, shape in sorted(shapes.items()):
        id = area("ar.caba_comuna", str(comuna))
        p.add("area", id=id, scheme_code="ar.caba_comuna")
        p.add("area_code", area_id=id, code=str(comuna), valid_from=COMUNAS_FROM)
        p.add("area_parent", area_id=id, parent_id=CITY, valid_from=COMUNAS_FROM)
        p.name(id, f"Comuna {comuna}", "es", "official")
        p.name(id, f"Comuna {comuna}", "en", "official", False)
        _outline(p, id, shape, COMUNAS_FROM)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The City of Buenos Aires's Head of Government, Legislature, and 15 comunal boards, their electoral systems, "
        "and the parties and alliances on the ballot",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ARGENTINA}
    for id, publisher, title, url in (
        (
            TSJ_SOURCE,
            "Tribunal Superior de Justicia de la Ciudad Autónoma de Buenos Aires",
            "Resultados electorales de la Ciudad",
            f"{TSJ}/resultados",
        ),
        (
            TE_SOURCE,
            "Tribunal Electoral de la Ciudad Autónoma de Buenos Aires",
            "Resultados Elecciones Legislativas 2025",
            f"{TE}/index.php",
        ),
        (
            TEC_SOURCE,
            "Tribunal Electoral de la Ciudad Autónoma de Buenos Aires",
            "Resoluciones de escrutinio de las elecciones de 2023",
            f"{TSJ}/resultados",
        ),
    ):
        p.add(
            "source",
            id=id,
            kind="official_body",
            publisher=publisher,
            title=title,
            url=url,
            license="Public information of the City of Buenos Aires, free reuse with the source cited (Ley 104)",
            license_url="https://www.buenosaires.gob.ar/",
            attribution=publisher,
        )
    for held in HELD:
        source = source_of(held)
        for url in urls(held):
            p.artifact(raw.cached(SOURCE, url), source)
    p.add("office", id=HEAD, polity_id=ARGENTINA, kind="head_of_government")
    p.name(HEAD, "Head of Government of the City of Buenos Aires", "en", "official")
    p.name(HEAD, "Jefe de Gobierno de la Ciudad Autónoma de Buenos Aires", "es", "native", False)
    p.add("body", id=LEGISLATURE, polity_id=ARGENTINA, kind="unicameral")
    p.name(LEGISLATURE, "Legislature of the City of Buenos Aires", "en", "official")
    p.name(LEGISLATURE, "Legislatura de la Ciudad Autónoma de Buenos Aires", "es", "native", False)
    for comuna in COMUNAS:
        id = junta(comuna)
        p.add("body", id=id, polity_id=ARGENTINA, kind="council")
        p.name(id, f"Comuna {comuna} Community Board", "en", "official")
        p.name(id, f"Junta Comunal de la Comuna {comuna}", "es", "native", False)
    for id, since, rules, method in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=ARGENTINA, valid_from=since, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    names = _names()
    for (code, year), text in sorted(names.items(), key=lambda item: (int(item[0][0]), item[0][1])):
        if int(code) < 700 and (code, year) != max((k for k in names if k[0] == code), key=lambda k: k[1]):
            continue
        id = party(code, year)
        p.add("party", id=id, kind="party" if int(code) < 700 else "bloc", polity_id=ARGENTINA)
        p.add(
            "external_id",
            entity_id=id,
            scheme="ar.caba.agrupacion",
            value=code if int(code) < 700 else f"{year}:{code}",
        )
        p.name(id, text, "es", "official")
    return p


def junta(comuna: int) -> str:
    return key("body", "junta", comuna)


PLACE_NAMES = {
    "head": (
        "Head of Government of the City of Buenos Aires",
        "Jefe de Gobierno de la Ciudad Autónoma de Buenos Aires",
    ),
    "legislature": ("Legislature of the City of Buenos Aires", "Legislatura de la Ciudad Autónoma de Buenos Aires"),
}
ELECTION_NAMES = {
    "primary": (
        "{year} City of Buenos Aires open primaries",
        "Elecciones primarias de la Ciudad de Buenos Aires {year}",
    ),
    "general": ("{year} City of Buenos Aires election", "Elecciones de la Ciudad de Buenos Aires {year}"),
    "runoff": (
        "{year} City of Buenos Aires runoff election",
        "Segunda vuelta de la Ciudad de Buenos Aires {year}",
    ),
}
FIRST_ROUND = {"2015-runoff": "2015-general"}
MEASURES = {"valid": 4, "blank": 6, "cast": 3}


def _value(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int, table: str = "result_value"
) -> None:
    p.add(
        table,
        result_set_id=result_set,
        **({"unit_id": unit} if table == "result_value" else {}),
        option_id=option,
        **({"channel": 0, "count_round": 0, "flags": 0} if table == "result_value" else {}),
        measure=measure,
        value=value,
    )


def _accounting(p: Package, result_set: str, unit: str | None, totals: dict[str, int], registered: int | None) -> None:
    invalid = totals["null"] + totals["challenged"] + totals["contested"]
    objected = totals["challenged"] + totals["contested"]
    figures = [(3, totals["cast"]), (4, totals["valid"]), (5, invalid), (6, totals["blank"]), (15, objected)]
    if registered is not None:
        figures.append((2, registered))
    for measure, value in figures:
        if unit is None:
            _value(p, result_set, "", None, measure, value, "declared")
        else:
            _value(p, result_set, unit, None, measure, value)


def _at(totals: dict[str, list[int]], k: int) -> dict[str, int]:
    return {label: values[min(k, len(values) - 1)] for label, values in totals.items()}


def election(name: str) -> Package:
    held = next(h for h in HELD if h.key == name)
    year = held.day.year
    first = FIRST_ROUND.get(held.key)
    english, spanish = ELECTION_NAMES[held.kind]
    p = Package(
        f"{SOURCE}/{held.key}",
        SOURCE,
        f"The City of Buenos Aires's {held.day:%-d %B %Y} {held.kind.replace('primary', 'open primaries')} by comuna",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", *([f"{SOURCE}/{first}"] if first else [])],
    )
    p.external.add(ARGENTINA)
    source = source_of(held)
    for url in urls(held):
        p.uses(raw.cached(SOURCE, url))
    primary = held.kind == "primary"
    election_id = key("election", held.key)
    path = f"ar/caba/{held.day}-{held.kind}"
    p.add(
        "election",
        id=election_id,
        polity_id=ARGENTINA,
        kind=held.kind,
        date_from=held.day,
        date_to=held.day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, english.format(year=year), "en", "official")
    p.name(election_id, spanish.format(year=year), "es", "native", False)
    city = page_of(held, None)
    pages: dict[int, Page] = {comuna: page_of(held, comuna) for comuna in COMUNAS}
    prison = page_of(held, None, True) if held.prison else None
    units: dict[int, str] = {}
    for comuna in COMUNAS:
        unit = units[comuna] = key("unit", held.day, comuna)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ARGENTINA,
            scheme_code="ar.caba_comuna",
            code=f"{held.day}:{comuna}",
            area_relation="equals",
            area_id=area("ar.caba_comuna", str(comuna)),
            kind="aggregate",
        )
    city_unit = key("unit", held.day, "city")
    if any(cargo not in pages[1].cargos for cargo in city.cargos):
        p.add(
            "reporting_unit",
            id=city_unit,
            polity_id=ARGENTINA,
            scheme_code="ar.caba_ciudad",
            code=f"{held.day}:city",
            area_relation="equals",
            area_id=CITY,
            kind="aggregate",
        )
    prison_unit = key("unit", held.day, "prison")
    if prison is not None:
        p.add(
            "reporting_unit",
            id=prison_unit,
            polity_id=ARGENTINA,
            scheme_code="ar.caba_comuna",
            code=f"{held.day}:prison",
            area_relation="unlocated",
            kind="prison",
        )
    cargos = [*city.cargos, *(["junta"] if "junta" in pages[1].cargos else [])]
    for cargo in cargos:
        scopes = list(COMUNAS) if cargo == "junta" else [0]
        for scope in scopes:
            tag = cargo if scope == 0 else f"junta:{scope}"
            contest = key("contest", held.key, tag)
            if cargo == "head":
                en, es = PLACE_NAMES["head"]
                slug, kind, body, office, magnitude, system = (
                    "chief-of-government",
                    "office",
                    None,
                    HEAD,
                    1,
                    HEAD_SYSTEM,
                )
            elif cargo == "legislature":
                en, es = PLACE_NAMES["legislature"]
                slug, kind, body, office, magnitude, system = (
                    "legislature",
                    "list_allocation",
                    LEGISLATURE,
                    None,
                    30,
                    LEGISLATURE_SYSTEM,
                )
            else:
                en, es = f"Comuna {scope} Community Board", f"Junta Comunal de la Comuna {scope}"
                slug, kind, body, office, magnitude, system = (
                    f"comuna-{scope}-community-board",
                    "list_allocation",
                    junta(scope),
                    None,
                    7,
                    JUNTA_SYSTEM,
                )
            if primary:
                magnitude, system = None, None  # type: ignore[assignment]
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind=kind,
                office_id=office,
                body_id=body,
                district_area_id=CITY if scope == 0 else area("ar.caba_comuna", str(scope)),
                magnitude=magnitude,
                round_number=2 if held.kind == "runoff" else 1,
                previous_round_id=key("contest", first, tag) if first else None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            p.name(contest, en, "en", "official")
            p.name(contest, es, "es", "native", False)
            p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
            result_set = key("result_set", held.key, tag)
            p.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=source,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=(PDF_METHOD if held.docs and year == 2023 else METHOD)
                + (PRIMARY_METHOD if primary else "")
                + (PRISON_METHOD if prison is not None else "")
                + (PRIMARY_DOCS_METHOD if primary and year == 2023 else ""),
                lang="en",
            )
            here: list[tuple[str, Page, int]] = [
                (units[c], pages[c], pages[c].cargos.index(cargo)) for c in COMUNAS if cargo in pages[c].cargos
            ]
            if scope:
                here = [(units[scope], pages[scope], pages[scope].cargos.index("junta"))]
            elif prison is not None and cargo in prison.cargos:
                here.append((prison_unit, prison, prison.cargos.index(cargo)))
            if scope == 0 and not here:
                here = [(city_unit, city, city.cargos.index(cargo))]
            looked = [*here, ("", city, city.cargos.index(cargo))] if scope == 0 else here
            options: dict[tuple[str, str], str] = {}
            numbers = sorted(
                {row.number for _, page, k in looked for row in page.rows if row.votes[k]}, key=lambda n: int(n)
            )
            for number in numbers:
                label = next(row.name for _, page, _ in looked for row in page.rows if row.number == number)
                parent = key("option", held.key, tag, number)
                p.add(
                    "contest_option",
                    id=parent,
                    contest_id=contest,
                    kind="ticket" if cargo == "head" else "list",
                    party_id=party(number, year),
                    ballot_position=int(number),
                    ballot_label=label,
                )
                options[(number, "")] = parent
                if not primary:
                    continue
                codes = sorted(
                    {
                        lista.code
                        for _, page, k in looked
                        for row in page.rows
                        if row.number == number
                        for lista in row.lists
                        if lista.votes[k]
                    },
                    key=int,
                )
                for code in codes:
                    child = options[(number, code)] = key("option", held.key, tag, number, code)
                    name_of = next(
                        lista.name
                        for _, page, _ in looked
                        for row in page.rows
                        if row.number == number
                        for lista in row.lists
                        if lista.code == code
                    )
                    p.add(
                        "contest_option",
                        id=child,
                        contest_id=contest,
                        kind="list",
                        parent_option_id=parent,
                        party_id=party(number, year),
                        ballot_label=name_of,
                    )
            for unit, page, k in here:
                _accounting(p, result_set, unit, _at(page.totals, k), page.registered)
                for row in page.rows:
                    if (row.number, "") not in options:
                        continue
                    if primary:
                        for lista in row.lists:
                            if (row.number, lista.code) in options:
                                _value(p, result_set, unit, options[(row.number, lista.code)], 1, lista.votes[k])
                    else:
                        _value(p, result_set, unit, options[(row.number, "")], 1, row.votes[k])
            if scope == 0:
                k = city.cargos.index(cargo)
                _accounting(p, result_set, None, _at(city.totals, k), city.registered)
                for row in city.rows:
                    if (row.number, "") not in options:
                        continue
                    if primary:
                        for lista in row.lists:
                            if (row.number, lista.code) in options:
                                _value(
                                    p, result_set, "", options[(row.number, lista.code)], 1, lista.votes[k], "declared"
                                )
                    else:
                        _value(p, result_set, "", options[(row.number, "")], 1, row.votes[k], "declared")
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{held.key}" for held in HELD)]


def parse() -> None:
    geo().write()
    registry().write()
    for held in HELD:
        election(held.key).write()


def emit() -> None:
    package.run("load", packages())
