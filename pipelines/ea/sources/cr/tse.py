"""Costa Rica's Tribunal Supremo de Elecciones: the "cómputo de votos" workbooks of every national election."""

import functools
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "cr.tse"
BASE = "https://www.tse.go.cr"
LIST_URL = f"{BASE}/estadisticas_elecciones.html"
COSTA_RICA = str(uuid5(package.NAMESPACE, "polity\x1fCR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fCRI"))
PROVINCES = ("SAN JOSE", "ALAJUELA", "CARTAGO", "HEREDIA", "GUANACASTE", "PUNTARENAS", "LIMON")
REGISTERED, CAST, VALID, INVALID, BLANK = 2, 3, 4, 5, 6
ACCOUNTING = {"ELECTORADO": REGISTERED, "TOTAL GENERAL": CAST, "TOTAL": VALID, "NULOS": INVALID, "BLANCOS": BLANK}
SKIP = {"ABSOLUTO", "NOTA", "FUENTE"}


@dataclass(frozen=True)
class Workbook:
    zip: str
    member: str  # regular expression for the member of the archive


@dataclass(frozen=True, eq=False)
class Held:
    """One election day that a workbook reports: the chapter of its tables for each contest family and round."""

    day: date
    books: tuple[Workbook, ...]
    chapters: dict[int, tuple[str, int]]  # chapter number -> (family, round)
    level: str  # "centro" for tables of voting centers, "nested" for tables nested by district
    spans: dict[int, tuple[int, int]] = field(default_factory=dict)  # chapter -> the table numbers that hold the round


ARCHIVES = (
    "computovotos_febreroabril2002",
    "computovotos_febrero2006",
    "computovotos_referendum2007",
    "computovotos_febrero2010",
    "computovotos_febrero_abril_2014",
    "computovotos_febrero_abril_2018",
    "computovotos_febrero_abril_2022",
    "computovotos_febrero2026",
)


def fetch() -> None:
    raw.fetch(SOURCE, LIST_URL, suffix=".html")
    for name in ARCHIVES:
        raw.fetch(SOURCE, zip_url(name), suffix=".zip")


CENTROS, NESTED_LEVEL = "centro", "nested"


def zip_url(name: str) -> str:
    return f"{BASE}/zip/elecciones/{name}.zip"


def fold(text: object) -> str:
    """Uppercase, accent-free, single-spaced text, so that a name matches across tables and years."""
    plain = unicodedata.normalize("NFKD", re.sub(r"\d+\s*/", " ", str(text or ""))).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9%/.]+", " ", plain).upper().split())


FIXES = {
    "ALIANZA DEMOCRATA CRISTINA": "Alianza Demócrata Cristiana",
    "ALIANZA DEMOCRATICA CRISTIANA": "Alianza Demócrata Cristiana",
    "AVANCE NACIOAL": "Avance Nacional",
    "MOVIEMIENTO LIBERTARIO": "Movimiento Libertario",
    "MOVIMIENTO LIVERTARIO": "Movimiento Libertario",
    "RENOVACION COSTRRICENSE": "Renovación Costarricense",
    "REPUBLICANO SOCILA CRISTIANO": "Republicano Social Cristiano",
    "TRASNSPARENCIA CARTAGINES": "Transparencia Cartaginés",
    "AUNTENTICO LIMONENSE": "Auténtico Limonense",
}
"""Misspelled party names in the workbooks' headers, by the folded header, with the party's name spelled right."""


def clean(text: str) -> str:
    """Text on one line, without the footnote markers the tables print after a name."""
    return " ".join(re.sub(r"(?<=\D)\d+\s*/", " ", text).split())


def title(text: str) -> str:
    small = {"de", "del", "la", "las", "los", "y", "el", "en", "por", "para", "a", "al", "e", "o"}
    words = " ".join(text.split()).lower().split(" ")
    return " ".join(w if i and w in small else w[:1].upper() + w[1:] for i, w in enumerate(words))


def number(text: str | None) -> float | None:
    if text is None or text.strip() in ("", "N.A.", "-", "--", "N.A", "N.D.", "N.D"):
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def whole(value: float) -> int:
    """A count that the source must print whole; the source's own totals sometimes carry halves."""
    if abs(value - round(value)) > 1e-6:
        raise ValueError(f"not a whole number: {value}")
    return round(value)


@functools.lru_cache(maxsize=2)
def book(archive: str, member: str) -> dict[str, pl.DataFrame]:
    """Every sheet of a workbook as a frame of strings, cells as the file holds them."""
    path = raw.cached(SOURCE, zip_url(archive)).path
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.fullmatch(member, n)]
        if len(names) != 1:
            raise LookupError(f"{archive}: {member} matches {names}")
        data = z.read(names[0])
    workbook = fastexcel.read_excel(data)
    return {
        name: workbook.load_sheet_by_name(name, header_row=None, dtypes="string").to_polars()
        for name in workbook.sheet_names
    }


def heading(frame: pl.DataFrame) -> str:
    """The caption of a table, which some sheets print in the second or third column."""
    for col in range(min(3, frame.width)):
        text = cell(frame, 0, col)
        if text:
            return " ".join(text.split())
    return ""


def cell(frame: pl.DataFrame, row: int, col: int) -> str:
    if row >= frame.height or col >= frame.width:
        return ""
    return (frame[row, col] or "").strip()


@dataclass
class Row:
    path: tuple[str, ...]  # labels of the row, outermost first, as printed
    values: dict[str, float]  # column label -> count


@dataclass
class Table:
    sheet: str
    caption: str
    labels: int
    columns: list[str]
    shown: list[str]  # the same columns as the header prints them
    rows: list[Row] = field(default_factory=list)


def read(sheet: str, frame: pl.DataFrame) -> Table | None:
    """Read one results table: the label columns, the header, and the numeric body."""
    caption = heading(frame)
    marker = next(
        (col for col in range(frame.width) if any(fold(cell(frame, r, col)).startswith("JUNTAS") for r in range(2, 5))),
        None,
    )
    if marker is None or marker > 3:
        return None
    start = next(
        (
            r
            for r in range(3, min(frame.height, 12))
            if cell(frame, r, marker).replace(".", "", 1).isdigit() and cell(frame, r, 0)
        ),
        None,
    )
    if start is None:
        return None
    columns: list[str] = []
    shown: list[str] = []
    for col in range(marker, frame.width):
        text = next((clean(cell(frame, r, col)) for r in range(start - 1, 1, -1) if cell(frame, r, col)), "")
        fixed = FIXES.get(fold(text))
        columns.append(fold(fixed) if fixed else fold(text))
        shown.append(fixed or text)
    table = Table(sheet, caption, 1 if marker == 2 else marker, columns, shown)
    carried = [""] * max(marker, 1)
    for r in range(start, frame.height):
        first = cell(frame, r, marker)
        if not first.replace(".", "", 1).isdigit():
            continue
        labels = [clean(cell(frame, r, c)) for c in range(marker)]
        if marker == 2:
            labels = labels[1:]
        elif marker > 1:
            for c in range(marker):
                if labels[c]:
                    if labels[c] != carried[c]:
                        for later in range(c + 1, marker - 1):
                            carried[later] = ""
                    carried[c] = labels[c]
                else:
                    labels[c] = carried[c]
        values: dict[str, float] = {}
        for offset, label in enumerate(columns):
            if not label or "%" in label or label in SKIP:
                continue
            parsed = number(cell(frame, r, marker + offset))
            if parsed is not None:
                values[label] = parsed
        table.rows.append(Row(tuple(labels), values))
    return table


def measure_of(label: str) -> int | None:
    """The accounting measure a column holds, or None for a party column, or 0 for a column to leave out."""
    if label.startswith(("ELECTORADO", "ELECTORES")):
        return REGISTERED
    if label in ("TOTAL GENERAL", "VOTOS RECIBIDOS"):
        return CAST
    if label in ("TOTAL", "VOTOS VALIDOS"):
        return VALID
    if label in ("NULOS", "VOTOS NULOS"):
        return INVALID
    if label in ("BLANCOS", "VOTOS BLANCOS", "VOTOS EN BLANCO"):
        return BLANK
    if not label or label in SKIP or "%" in label or label.startswith(("JUNTAS", "PARTICIPACION", "ABSTENCION")):
        return 0
    return None


PROVINCE = re.compile(r"^PROVINCIA\b")
CANTON = re.compile(r"^CANTON:?\s*(\d+)?\s*(.*)$")
DISTRICT = re.compile(r"^DISTRITO:?\s*(\d+)?\s*(.*)$")
SCOPE = re.compile(r"^CUADRO\s*(\d+)\.\s*(\d+)\s+(.*?)(?::|\s+JUNTAS)")


@dataclass
class Leaf:
    """One finest-level row of a table."""

    province: int  # 1 to 7, or 0 abroad
    canton: tuple[int, str] | None
    district: tuple[int, str] | None
    name: str
    above: tuple[str, ...]  # abroad: the continent and country the row sits under
    values: dict[str, float]


def scope(caption: str) -> tuple[int, int, str]:
    plain = unicodedata.normalize("NFKD", caption).encode("ascii", "ignore").decode().upper()
    found = SCOPE.match(" ".join(plain.split()))
    if found is None:
        raise ValueError(f"no table scope in {caption[:60]}")
    return int(found[1]), int(found[2]), found[3]


def province_of(name: str) -> int:
    return PROVINCES.index(name) + 1 if name in PROVINCES else 0


def tree(table: Table, province: int) -> list[Leaf]:
    """The leaves of a table whose rows are nested by markers: province, canton, district, then the finest unit."""
    leaves: list[Leaf] = []
    canton: tuple[int, str] | None = None
    district: tuple[int, str] | None = None
    cantons = districts = 0
    for index, row in enumerate(table.rows):
        shown = " ".join(row.path[0].split())
        label = fold(shown)
        if index == 0 or PROVINCE.match(label):
            continue
        if found := CANTON.match(label):
            cantons += 1
            canton = (int(found[1]) if found[1] else cantons, re.sub(r"^CANT.N:?\s*\d*\s*", "", shown.upper()))
            districts = 0
            district = None
        elif not label and canton is not None:
            districts += 1
            district = (districts, "")
        elif found := DISTRICT.match(label):
            districts += 1
            district = (int(found[1]) if found[1] else districts, re.sub(r"^DISTRITO:?\s*\d*\s*", "", shown.upper()))
        else:
            if canton is None or district is None:
                raise ValueError(f"{table.sheet}: {row.path[0]} sits outside a district")
            leaves.append(Leaf(province, canton, district, shown, (), row.values))
    return leaves


def bare(name: str) -> str:
    """A folded name without its leading article, so that Fortuna and La Fortuna are one name."""
    return re.sub(r"^(LA|EL|LOS|LAS) ", "", fold(name))


def numbering(leaves: list[Leaf]) -> dict[tuple[int, str] | tuple[int, int, str], int]:
    """The canton and district numbers the nested tables print, found by name."""
    found: dict[tuple[int, str] | tuple[int, int, str], int] = {}
    for leaf in leaves:
        if leaf.canton is None or leaf.district is None:
            continue
        found[(leaf.province, fold(leaf.canton[1]))] = leaf.canton[0]
        found[(leaf.province, leaf.canton[0], fold(leaf.district[1]))] = leaf.district[0]
    for leaf in leaves:
        if leaf.canton is None or leaf.district is None:
            continue
        found.setdefault((leaf.province, bare(leaf.canton[1])), leaf.canton[0])
        found.setdefault((leaf.province, leaf.canton[0], bare(leaf.district[1])), leaf.district[0])
    return found


def locate(
    found: dict[tuple[int, str] | tuple[int, int, str], int], province: int, canton: str, district: str
) -> tuple[int, int]:
    """The canton and district numbers of a canton and district named in a table."""
    canton = ALIASES.get((province, fold(canton)), canton)
    number = found.get((province, fold(canton))) or found.get((province, bare(canton)))
    if number is None:
        raise LookupError(f"canton {canton} of province {province} is not in the nested tables")
    district_number = found.get((province, number, fold(district))) or found.get((province, number, bare(district)))
    if district_number is None:
        raise LookupError(f"{canton} / {district} is not in the nested tables")
    return number, district_number


def centers(table: Table, found: dict[tuple[int, str] | tuple[int, int, str], int], province: int) -> list[Leaf]:
    """The leaves of a table with one row for each voting center, under its canton and administrative district."""
    leaves: list[Leaf] = []
    for row in table.rows:
        canton, district, name = row.path
        canton = " ".join(re.sub(r"CANT.N:?|^\d+\s", " ", canton, flags=re.IGNORECASE).split())
        district = " ".join(re.sub(r"DISTRITO:?|^\d+\s", " ", district, flags=re.IGNORECASE).split())
        number, number_district = locate(found, province, canton, district)
        leaves.append(
            Leaf(
                province,
                (number, " ".join(canton.split())),
                (number_district, " ".join(district.split())),
                " ".join(name.split()),
                (),
                row.values,
            )
        )
    return leaves


def nest(rows: list[Row], keys: tuple[str, ...]) -> dict[int, int]:
    """For each row, the index of its last descendant. A row is a parent when the rows that follow it, taken as its
    children and their descendants, add up to it; a leaf maps to itself."""

    def vector(row: Row) -> tuple[float, ...]:
        return tuple(row.values.get(k, 0.0) for k in keys)

    ends: dict[int, int] = {}

    def extent(i: int) -> int:
        if i in ends:
            return ends[i]
        want = vector(rows[i])
        total = [0.0] * len(want)
        j = i + 1
        while j < len(rows) and any(t < w - 1e-6 for t, w in zip(total, want, strict=True)):
            last = extent(j)
            total = [t + v for t, v in zip(total, vector(rows[j]), strict=True)]
            if any(t > w + 1e-6 for t, w in zip(total, want, strict=True)):
                break
            j = last + 1
        else:
            if j > i + 1 and all(abs(t - w) < 1e-6 for t, w in zip(total, want, strict=True)):
                ends[i] = j - 1
                return j - 1
        ends[i] = i
        return i

    for i in reversed(range(len(rows))):
        extent(i)
    return ends


def abroad(table: Table) -> list[Leaf]:
    """The leaves of a table of the vote abroad, nested without markers."""
    rows = table.rows[1:]
    ends = nest(rows, ("JUNTAS", "ELECTORADO", "TOTAL GENERAL", "TOTAL"))
    return [
        Leaf(
            0,
            None,
            None,
            row.path[0],
            tuple(rows[i].path[0] for i in range(j) if ends[i] > i and j <= ends[i]),
            row.values,
        )
        for j, row in enumerate(rows)
        if ends[j] == j
    ]


def abroad_centers(table: Table) -> list[Leaf]:
    return [
        Leaf(0, None, None, " ".join(row.path[2].split()), (row.path[0], row.path[1]), row.values) for row in table.rows
    ]


NESTED = (
    Workbook("computovotos_febrero_abril_2014", r"Computo de Votos Elecciones Febrero y Abril 2014\.xlsm"),
    Workbook("computovotos_febrero_abril_2018", r"Computo de Votos Elecciones 2018\.xlsm"),
    Workbook("computovotos_febrero2026", r"Computo de Votos Elecciones 2026 \(vf2\)\.xlsm"),
)
"""Workbooks whose nested tables print the number of each canton and district."""
ALIASES: dict[tuple[int, str], str] = {(2, "ALFARO RUIZ"): "ZARCERO"}
"""Cantons the workbooks name differently over the years, by province and folded name."""


@functools.cache
def numbers() -> dict[tuple[int, str] | tuple[int, int, str], int]:
    """Each canton's number within its province and each district's within its canton, by name, as printed by the
    nested tables; a later workbook's number wins."""
    found: dict[tuple[int, str] | tuple[int, int, str], int] = {}
    for wb in NESTED:
        sheets = book(wb.zip, wb.member)
        for province in range(1, 8):
            sheet = f"C1.{10 + province}"
            table = read(sheet, sheets[sheet])
            if table is None:
                raise LookupError(f"{wb.zip} {sheet}")
            found |= numbering(tree(table, province))
    return found


# ---------------------------------------------------------------------------------------------------------------
# The elections

WORKBOOK_2002 = Workbook("computovotos_febreroabril2002", r"COMPUTO DE VOTOS MENU\.xls")
HELD = (
    Held(
        date(2002, 2, 3),
        (WORKBOOK_2002,),
        {2: ("president", 1), 3: ("assembly", 1)},
        NESTED_LEVEL,
        {2: (1, 9), 3: (1, 9)},
    ),
    Held(date(2002, 4, 7), (WORKBOOK_2002,), {2: ("president", 2)}, NESTED_LEVEL, {2: (10, 17)}),
    Held(
        date(2006, 2, 5),
        (
            Workbook("computovotos_febrero2006", r"CAPITULO 2 ELECCIONES PRESIDENCIALES FEBRERO 2006 MENU\.xls"),
            Workbook("computovotos_febrero2006", r"CAPITULO 3 ELECCIONES DIPUTACIONES FEBRERO 2006 MENU\.xls"),
        ),
        {2: ("president", 1), 3: ("assembly", 1)},
        NESTED_LEVEL,
    ),
    Held(
        date(2010, 2, 7),
        (
            Workbook("computovotos_febrero2010", r"1 ComputoVotos_ febrero2010_Presidente\.xlsm"),
            Workbook("computovotos_febrero2010", r"2 ComputoVotos_ febrero2010_Diputados\.xlsm"),
        ),
        {1: ("president", 1), 2: ("assembly", 1)},
        NESTED_LEVEL,
    ),
    Held(date(2014, 2, 2), (NESTED[0],), {1: ("president", 1), 2: ("assembly", 1)}, NESTED_LEVEL),
    Held(date(2014, 4, 6), (NESTED[0],), {3: ("president", 2)}, NESTED_LEVEL),
    Held(date(2018, 2, 4), (NESTED[1],), {1: ("president", 1), 2: ("assembly", 1)}, CENTROS),
    Held(date(2018, 4, 1), (NESTED[1],), {3: ("president", 2)}, CENTROS),
    Held(
        date(2022, 2, 6),
        (Workbook("computovotos_febrero_abril_2022", r"Computo de Votos Elecciones 2022\.xlsm"),),
        {1: ("president", 1), 2: ("assembly", 1)},
        CENTROS,
    ),
    Held(
        date(2022, 4, 3),
        (Workbook("computovotos_febrero_abril_2022", r"Computo de Votos Elecciones 2022\.xlsm"),),
        {3: ("president", 2)},
        CENTROS,
    ),
    Held(date(2026, 2, 1), (NESTED[2],), {1: ("president", 1), 2: ("assembly", 1)}, NESTED_LEVEL),
)
SEATS = {1: 19, 2: 11, 3: 7, 4: 6, 5: 4, 6: 5, 7: 5}
PROVINCE_NAMES = ("San José", "Alajuela", "Cartago", "Heredia", "Guanacaste", "Puntarenas", "Limón")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def party(name: str) -> str:
    return key("party", fold(name))


TSE_SOURCE = key("source", "tse")
ASSEMBLY = key("body", "assembly")
PRESIDENCY = key("office", "president")
PRESIDENT_SYSTEM = key("electoral_system", "president")
ASSEMBLY_SYSTEM = key("electoral_system", "assembly")
METHOD = (
    "Counted by the Tribunal Supremo de Elecciones, as published in its workbook of the vote count (cómputo de "
    "votos). The registered electorate (electorado), the votes received (ballots cast), the valid votes by party, "
    "the null votes (nulos), and the blank votes (blancos) are stored for each unit as printed. Valid votes exclude "
    "the blank votes. The number of polling tables (juntas) in each unit is not stored."
)
CENTER_METHOD = (
    " The units are the voting centers (centros de votación), the finest level the Tribunal publishes. Special "
    "centers, such as long-stay homes, institutional care centers, and mental health centers, are rows of the same "
    "tables."
)
DISTRICT_METHOD = (
    " The units are the electoral districts (distritos electorales) the Tribunal prints under each administrative "
    "district, the finest level it publishes for this election. Votes cast abroad are reported by country."
)
SOURCE_NOTE = "Tribunal Supremo de Elecciones, Costa Rica"


def code_of(leaf: Leaf) -> str:
    if leaf.canton is None or leaf.district is None:
        return "EXT"
    return f"{leaf.province}{leaf.canton[0]:02d}{leaf.district[0]:02d}"


def places() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """The names of the provinces, cantons, and districts by TSE code, the newest election's name winning."""
    provinces = {str(i + 1): name for i, name in enumerate(PROVINCE_NAMES)}
    cantons: dict[str, str] = {}
    districts: dict[str, str] = {}
    for held in sorted(HELD, key=lambda h: h.day):
        for found in chosen(held).leaves.values():
            for _, leaves, _ in found:
                for leaf in leaves:
                    if leaf.canton is None or leaf.district is None:
                        continue
                    canton = f"{leaf.province}{leaf.canton[0]:02d}"
                    district = f"{canton}{leaf.district[0]:02d}"
                    if leaf.canton[1]:
                        cantons[canton] = title(leaf.canton[1])
                    if leaf.district[1]:
                        districts[district] = title(leaf.district[1])
                    cantons.setdefault(canton, f"Cantón {leaf.canton[0]}")
                    districts.setdefault(district, f"Distrito {leaf.district[0]}")
    return provinces, cantons, districts


def sources(p: Package) -> None:
    for name in ARCHIVES:
        p.uses(raw.cached(SOURCE, zip_url(name)))
    p.uses(raw.cached(SOURCE, LIST_URL))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Costa Rica's 7 provinces, cantons, and administrative districts by the Tribunal Supremo de Elecciones' codes, "
        "and the schemes of its voting centers",
    )
    p.external |= {COSTA_RICA, COUNTRY}
    sources(p)
    for code, rank, kind, noun in (
        ("cr.provincia", 1, "administrative", "province"),
        ("cr.canton", 2, "administrative", "canton"),
        ("cr.distrito", 3, "administrative", "district"),
        ("cr.distrito_electoral", 4, "precinct", "electoral district"),
        ("cr.centro", 4, "precinct", "voting center"),
        ("cr.pais", 4, "precinct", "country abroad"),
    ):
        p.add("scheme", code=code, polity_id=COSTA_RICA, kind=kind, rank=rank, noun=noun, code_system="tse")
    provinces, cantons, districts = places()

    def place(scheme: str, code: str, text: str, parent: str) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "es", "official")

    for code, text in provinces.items():
        place("cr.provincia", code, text, COUNTRY)
    for code, text in sorted(cantons.items()):
        place("cr.canton", code, text, area("cr.provincia", code[:1]))
    for code, text in sorted(districts.items()):
        place("cr.distrito", code, text, area("cr.canton", code[:3]))
    return p


SYSTEMS = (
    (
        PRESIDENT_SYSTEM,
        {"formula": "two_round", "majority": 0.4, "advance": 2},
        (
            "The President and the two Vice Presidents are elected together on one ticket. A ticket with at least 40% "
            "of the valid votes is elected in the first round; otherwise the two leading tickets go to a second "
            "round, held in April."
        ),
    ),
    (
        ASSEMBLY_SYSTEM,
        {"formula": "hare"},
        (
            "The 57 deputies of the Legislative Assembly are elected from closed lists in the seven provinces (San "
            "José 19, Alajuela 11, Cartago 7, Heredia 6, Guanacaste 4, Puntarenas 5, Limón 5). Seats go first to each "
            "list's whole quotients, then to lists that reach a subquotient of half the quotient, and the remainder "
            "to the largest remainders."
        ),
    ),
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Costa Rica's political parties on the ballot, the Legislative Assembly, the presidency, and their electoral "
        "systems, from the Tribunal Supremo de Elecciones' workbooks",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {COSTA_RICA}
    p.add(
        "source",
        id=TSE_SOURCE,
        kind="official_body",
        publisher="Tribunal Supremo de Elecciones",
        title="Cómputo de votos: workbooks of the vote count of each election",
        url=LIST_URL,
        license="CC BY-SA 4.0",
        license_url="http://creativecommons.org/licenses/by-sa/4.0/",
        attribution=SOURCE_NOTE,
    )
    for name in ARCHIVES:
        p.artifact(raw.cached(SOURCE, zip_url(name)), TSE_SOURCE)
    p.artifact(raw.cached(SOURCE, LIST_URL), TSE_SOURCE)
    p.add("body", id=ASSEMBLY, polity_id=COSTA_RICA, kind="unicameral")
    p.name(ASSEMBLY, "Legislative Assembly of Costa Rica", "en", "official")
    p.name(ASSEMBLY, "Asamblea Legislativa", "es", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=COSTA_RICA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Costa Rica", "en", "official")
    p.name(PRESIDENCY, "Presidente de la República", "es", "native", False)
    for id, rules, method in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=COSTA_RICA, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    seen: dict[str, str] = {}
    for held in HELD:
        for table in chosen(held).tables:
            for label, text in zip(table.columns, table.shown, strict=True):
                if measure_of(label) is None:
                    seen[label] = text
    for label, text in sorted(seen.items()):
        id = party(label)
        p.add("party", id=id, kind="party", polity_id=COSTA_RICA)
        p.name(id, title(text), "es", "official")
    return p


@dataclass
class Chosen:
    """The tables of one election day that hold the finest units, with each one's leaves."""

    tables: list[Table] = field(default_factory=list)
    leaves: dict[tuple[int, int], list[tuple[Table, list[Leaf], str]]] = field(default_factory=dict)
    declared: dict[int, dict[str, dict[str, float]]] = field(default_factory=dict)


def depth(table: Table) -> int:
    return 3 if table.labels == 3 else 2 if "CONSULADO" in fold(table.caption) else 1


@functools.lru_cache(maxsize=4)
def chosen(held: Held) -> Chosen:
    out = Chosen()
    numbered = numbers()
    for name, frame in (item for wb in held.books for item in book(wb.zip, wb.member).items()):
        caption = heading(frame)
        if not caption.startswith("CUADRO"):
            continue
        try:
            chapter, number, where = scope(caption)
        except ValueError:
            continue
        low, high = held.spans.get(chapter, (1, 999))
        if chapter not in held.chapters or not low <= number <= high:
            continue
        if number == low:
            out.declared[chapter] = declared(frame)
            continue
        table = read(name, frame)
        if table is None:
            continue
        shown = fold(caption)
        province = province_of(where)
        if province and held.level == CENTROS:
            if table.labels == 3 and "TIPO DE CENTRO" not in shown:
                leaves = centers(table, numbered, province)
                out.leaves.setdefault((chapter, province), []).append((table, leaves, "cr.centro"))
        elif province and table.labels == 1:
            try:
                leaves = tree(table, province)
            except ValueError:
                continue
            if leaves:
                out.leaves.setdefault((chapter, province), []).append((table, leaves, "cr.distrito_electoral"))
        elif "EXTRANJERO" in where and "COSTA RICA" not in where:
            if table.labels == 3:
                out.leaves.setdefault((chapter, 0), []).append((table, abroad_centers(table), "cr.centro"))
            elif "CENTRO" not in shown:
                scheme = "cr.centro" if "CONSULADO" in shown else "cr.pais"
                out.leaves.setdefault((chapter, 0), []).append((table, abroad(table), scheme))
    for found in out.leaves.values():
        if len(found) > 1:
            found[:] = [max(found, key=lambda item: (depth(item[0]), len(item[1])))]
    out.tables = [table for found in out.leaves.values() for table, _, _ in found]
    return out


def declared(frame: pl.DataFrame) -> dict[str, dict[str, float]]:
    """The printed totals of a chapter's first table: for each column (Costa Rica, a province, abroad), its rows."""
    top = next(r for r in range(2, 8) if fold(cell(frame, r, 1)).startswith("COSTA RICA"))
    columns: dict[int, str] = {}
    for col in range(1, frame.width):
        label = fold(cell(frame, top + 1, col)) or fold(cell(frame, top, col))
        if label and label != "PROVINCIA":
            columns[col] = label
    found: dict[str, dict[str, float]] = {name: {} for name in columns.values()}
    for r in range(top + 2, frame.height):
        label = fold(cell(frame, r, 0))
        if not label or label.startswith(("FUENTE", "NOTA")):
            continue
        for col, name in columns.items():
            value = number(cell(frame, r, col))
            if value is not None:
                found[name][label] = value
    return found


COMPARE: dict[str, dict[str, object]] = {}


def packages() -> list[str]:
    days = sorted({held.day for held in HELD} | {REFERENDUM_DAY})
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days)]


def parse() -> None:
    geo().write()
    registry().write()
    for day in sorted({held.day for held in HELD}):
        election(day).write()
    referendum().write()
    print(COMPARE)


def emit() -> None:
    package.run("load", packages())


def over(p: Package, values: pl.DataFrame) -> None:
    """Accept, and note on each result set, the units whose ballots cast exceed their registered electorate."""
    wide = (
        values.filter(pl.col("measure").is_in([REGISTERED, CAST]))
        .pivot(on="measure", index=["result_set_id", "unit_id"], values="value", aggregate_function="first")
        .rename({str(REGISTERED): "registered", str(CAST): "cast"})
    )
    if "cast" not in wide.columns or "registered" not in wide.columns:
        return
    excess = wide.filter(pl.col("cast") > pl.col("registered"))
    if excess.is_empty():
        return
    p.accept(
        "accounting.turnout",
        f"{excess['unit_id'].n_unique()} voting center(s) print more votes received than registered electors; the "
        "figures are the Tribunal's own and are stored as printed, and each result set notes the count",
    )
    for result_set, count in excess.group_by("result_set_id").len().iter_rows():
        p.add(
            "note",
            id=key("note", result_set, "turnout"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"In {count} unit{'s' if count > 1 else ''}, the Tribunal prints more votes received than "
            "registered electors. The figures are stored as printed.",
            lang="en",
        )


def election(day: date) -> Package:
    helds = [held for held in HELD if held.day == day]
    held = helds[0]
    first = held.day if all(rnd == 1 for _, rnd in held.chapters.values()) else None
    opening = next(h.day for h in HELD if h.day.year == day.year and all(rnd == 1 for _, rnd in h.chapters.values()))
    runoff = first is None
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"] + ([f"{SOURCE}/{opening}"] if runoff else [])
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Costa Rica's {'presidential runoff' if runoff else 'national election'} of {day:%-d %B %Y} by "
        f"{'voting center' if held.level == CENTROS else 'electoral district'} from the Tribunal Supremo de "
        "Elecciones' vote count",
        depends,
    )
    p.external |= {COSTA_RICA, COUNTRY}
    sources(p)
    chosen_tables = chosen(held)
    method = METHOD + (CENTER_METHOD if held.level == CENTROS else DISTRICT_METHOD)
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=COSTA_RICA,
        kind="runoff" if runoff else "general",
        date_from=day,
        date_to=day,
        status="held",
    )
    word = "presidential-runoff" if runoff else "general"
    path = f"cr/{day}-{word}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(
        election_id,
        f"{day.year} Costa Rican presidential runoff" if runoff else f"{day.year} Costa Rican general election",
        "en",
        "official",
    )
    p.name(
        election_id,
        f"Segunda ronda de las elecciones nacionales {day.year}" if runoff else f"Elecciones nacionales {day.year}",
        "es",
        "native",
        False,
    )
    units: dict[str, str] = {}
    unit_rows: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    comparison: dict[str, object] = {}
    for chapter, (family, round_number) in sorted(held.chapters.items()):
        provinces = (
            [0] if family == "president" else sorted(pr for (ch, pr) in chosen_tables.leaves if ch == chapter and pr)
        )
        for province in provinces:
            groups = (
                [item for (ch, pr), found in sorted(chosen_tables.leaves.items()) if ch == chapter for item in found]
                if family == "president"
                else chosen_tables.leaves[(chapter, province)]
            )
            rows = f"{family}:{province}"
            contest = key("contest", day, family, province)
            result_set = key("result_set", day, family, province)
            if family == "president":
                p.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="office",
                    office_id=PRESIDENCY,
                    district_area_id=COUNTRY,
                    magnitude=1,
                    round_number=round_number,
                    previous_round_id=key("contest", opening, family, 0) if runoff else None,
                    electoral_system_id=PRESIDENT_SYSTEM,
                    default_result_kind="counted",
                )
                label = "President of Costa Rica"
                p.name(contest, label, "en", "official")
                p.name(contest, "Presidente de la República", "es", "native", False)
                slug = f"{path}/president"
            else:
                p.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="list_allocation",
                    body_id=ASSEMBLY,
                    district_area_id=area("cr.provincia", str(province)),
                    magnitude=SEATS[province],
                    round_number=1,
                    electoral_system_id=ASSEMBLY_SYSTEM,
                    default_result_kind="counted",
                )
                label = f"Legislative Assembly: {PROVINCE_NAMES[province - 1]}"
                p.name(contest, label, "en", "official")
                p.name(contest, f"Asamblea Legislativa: {PROVINCE_NAMES[province - 1]}", "es", "native", False)
                slug = f"{path}/legislative-assembly-{changeset.slug(PROVINCE_NAMES[province - 1])}"
            p.add("slug", path=slug, entity_id=contest, is_current=True)
            p.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=TSE_SOURCE,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
            )
            options: dict[str, str] = {}
            for table, _, _ in groups:
                for column, text in zip(table.columns, table.shown, strict=True):
                    if measure_of(column) is None and column not in options:
                        option = options[column] = key("option", day, family, province, column)
                        p.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="ticket" if family == "president" else "list",
                            party_id=party(column),
                            ballot_position=len(options),
                            ballot_label=text,
                        )
            sums: Counter[tuple[str | None, int]] = Counter()
            for table, leaves, scheme in groups:
                counts: Counter[str] = Counter()
                for leaf in leaves:
                    base = f"{day}:{code_of(leaf)}:{fold(' '.join((*leaf.above, leaf.name)))}"
                    counts[base] += 1
                    code = base if counts[base] == 1 else f"{base}#{counts[base]}"
                    if code not in units:
                        unit = units[code] = key("unit", code)
                        abroad_unit = leaf.province == 0
                        unit_rows.append(
                            {
                                "id": unit,
                                "polity_id": COSTA_RICA,
                                "scheme_code": scheme,
                                "code": code,
                                "area_relation": "unlocated" if abroad_unit else "within",
                                "area_id": None if abroad_unit else area("cr.distrito", code_of(leaf)),
                                "kind": "abroad_mission" if abroad_unit else "standard",
                                "name": title(" ".join((*leaf.above[-1:], leaf.name)))
                                if abroad_unit
                                else title(leaf.name),
                            }
                        )
                    unit = units[code]
                    for column, value in leaf.values.items():
                        measure = measure_of(column)
                        if measure == 0 or (measure is None and column not in options):
                            continue
                        number_ = whole(value)
                        sums[(options[column] if measure is None else None, 1 if measure is None else measure)] += (
                            number_
                        )
                        values.append(
                            {
                                "result_set_id": result_set,
                                "unit_id": unit,
                                "option_id": options[column] if measure is None else None,
                                "measure": 1 if measure is None else measure,
                                "value": number_,
                            }
                        )
            printed = chosen_tables.declared.get(chapter, {})
            scopes = ["COSTA RICA"] if family == "president" else [fold(PROVINCE_NAMES[province - 1])]
            gaps: dict[str, tuple[int, int]] = {}
            for name in scopes:
                for column, value in printed.get(name, {}).items():
                    measure = measure_of(column)
                    option_id = None
                    if measure is None:
                        if column not in options:
                            continue
                        option_id, measure = options[column], 1
                    elif measure == 0:
                        continue
                    elif column.startswith("VOTOS VALIDOS"):
                        measure = VALID
                    elif column == "VOTOS RECIBIDOS":
                        measure = CAST
                    count = round(value)
                    p.add("declared", result_set_id=result_set, option_id=option_id, measure=measure, value=count)
                    mine = sums[(option_id, measure)]
                    if mine != count:
                        gaps[column] = (mine, count)
            comparison[rows] = {
                "gaps": gaps,
                "units": len({v["unit_id"] for v in values if v["result_set_id"] == result_set}),
            }
            comparison[rows + ":total"] = {measure: sums[(None, measure)] for measure in (2, 3, 4, 5, 6)}
    p.frame("reporting_unit", pl.DataFrame(unit_rows, infer_schema_length=None).drop("name"))
    for row in unit_rows:
        p.name(str(row["id"]), str(row["name"]), "es", "official")
    p.frame(
        "result_value",
        pl.DataFrame(values, infer_schema_length=None).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
        ),
    )
    COMPARE[str(day)] = comparison
    over(p, pl.DataFrame(values, infer_schema_length=None))
    return p


REFERENDUM = Workbook("computovotos_referendum2007", r"RESULTADOS REFERENDUM 2007-MENU\.xls")
REFERENDUM_DAY = date(2007, 10, 7)
REFERENDUM_SYSTEM = key("electoral_system", "referendum")
REFERENDUM_COLUMNS = {1: "JUNTAS", 3: "ELECTORADO", 5: "SI", 7: "NO", 9: "NULOS", 11: "BLANCOS"}
REFERENDUM_QUESTION = (
    "Do you approve the Dominican Republic–Central America–United States Free Trade Agreement (DR-CAFTA)?",
    "¿Aprueba el Tratado de Libre Comercio entre República Dominicana, Centroamérica y los Estados Unidos?",
)


def referendum_rows() -> tuple[list[Row], dict[str, float]]:
    """The rows of the referendum's finest table, and the national totals its first table prints."""
    sheets = book(REFERENDUM.zip, REFERENDUM.member)
    frame = sheets["C5"]
    rows: list[Row] = []
    for r in range(7, frame.height):
        label = clean(cell(frame, r, 0))
        if label and number(cell(frame, r, 1)) is not None:
            rows.append(
                Row((label,), {name: float(number(cell(frame, r, c)) or 0) for c, name in REFERENDUM_COLUMNS.items()})
            )
    totals = {
        name: float(number(cell(sheets["C1"], 7, column)) or 0)
        for column, name in enumerate(REFERENDUM_COLUMNS.values(), 1)
    }
    return rows, totals


def referendum_leaves(rows: list[Row]) -> list[Leaf]:
    """The finest rows: the electoral districts under each administrative district."""
    found = numbers()
    ends = nest(rows, ("JUNTAS", "ELECTORADO", "SI", "NO"))
    leaves: list[Leaf] = []
    for j, row in enumerate(rows):
        if ends[j] != j:
            continue
        above = [rows[i].path[0] for i in range(j) if ends[i] > i and j <= ends[i]]
        province = canton = None
        for label in above:
            if label.upper().startswith("PROVINCIA"):
                province = province_of(fold(re.sub(r"^PROVINCIA:?\s*", "", label, flags=re.IGNORECASE)))
            elif label.upper().startswith("CANT"):
                canton = re.sub(r"^CANT.N:?\s*", "", label, flags=re.IGNORECASE)
        after = [label for label in above if not label.upper().startswith(("TOTAL", "PROVINCIA", "CANT"))]
        district = after[0] if after else row.path[0]
        if not province or canton is None:
            raise ValueError(f"referendum: {row.path[0]} sits outside a canton")
        number_canton, number_district = locate(found, province, canton, district)
        leaves.append(Leaf(province, (number_canton, canton), (number_district, district), row.path[0], (), row.values))
    return leaves


def referendum() -> Package:
    day = REFERENDUM_DAY
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "Costa Rica's referendum of 7 October 2007 on the Dominican Republic–Central America–United States Free Trade "
        "Agreement by electoral district from the Tribunal Supremo de Elecciones' vote count",
        [f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {COSTA_RICA, COUNTRY}
    sources(p)
    rows, printed = referendum_rows()
    leaves = referendum_leaves(rows)
    election_id = key("election", day)
    path = f"cr/{day}-referendum"
    p.add(
        "election", id=election_id, polity_id=COSTA_RICA, kind="referendum", date_from=day, date_to=day, status="held"
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(
        election_id, "2007 Costa Rican referendum on the free trade agreement with the United States", "en", "official"
    )
    p.name(
        election_id,
        "Referéndum sobre el Tratado de Libre Comercio con los Estados Unidos de 2007",
        "es",
        "native",
        False,
    )
    p.add(
        "electoral_system",
        id=REFERENDUM_SYSTEM,
        polity_id=COSTA_RICA,
        rules={"formula": "referendum", "majority": {"share": 0.5}},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", REFERENDUM_SYSTEM),
        entity_id=REFERENDUM_SYSTEM,
        kind="methodology",
        body_md="A referendum is approved when more than half of the valid votes are in favor, if at least 30% of the "
        "electorate votes.",
        lang="en",
    )
    contest = key("contest", day, "referendum")
    result_set = key("result_set", day, "referendum")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="ballot_question",
        district_area_id=COUNTRY,
        round_number=1,
        electoral_system_id=REFERENDUM_SYSTEM,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, REFERENDUM_QUESTION[0], "en", "official")
    p.name(contest, REFERENDUM_QUESTION[1], "es", "native", False)
    p.add("slug", path=f"{path}/question", entity_id=contest, is_current=True)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=TSE_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = (
        "Counted by the Tribunal Supremo de Elecciones, as published in its workbook of the referendum count. The "
        "registered electorate, the votes for yes (sí) and no, the null votes, and the blank votes are stored for "
        "each electoral district (distrito electoral), the finest level the Tribunal publishes, as printed. The "
        "workbook does not print ballots cast or valid votes for each district, so those are not stored. The number "
        "of polling tables (juntas) is not stored."
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    options = {}
    for position, (column, english, native) in enumerate((("SI", "Yes", "Sí"), ("NO", "No", "No")), 1):
        option = options[column] = key("option", day, column)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=native.upper(),
        )
        p.name(option, english, "en", "official")
        p.name(option, native, "es", "native", False)
    measures = {"ELECTORADO": REGISTERED, "NULOS": INVALID, "BLANCOS": BLANK}
    for column, value in printed.items():
        if column == "JUNTAS":
            continue
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=options.get(column),
            measure=1 if column in options else measures[column],
            value=round(value),
        )
    counts: Counter[str] = Counter()
    unit_rows: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    sums: Counter[str] = Counter()
    for leaf in leaves:
        base = f"{day}:{code_of(leaf)}:{fold(leaf.name)}"
        counts[base] += 1
        code = base if counts[base] == 1 else f"{base}#{counts[base]}"
        unit = key("unit", code)
        unit_rows.append(
            {
                "id": unit,
                "polity_id": COSTA_RICA,
                "scheme_code": "cr.distrito_electoral",
                "code": code,
                "area_relation": "within",
                "area_id": area("cr.distrito", code_of(leaf)),
                "kind": "standard",
            }
        )
        p.name(unit, title(leaf.name), "es", "official")
        for column, value in leaf.values.items():
            if column == "JUNTAS":
                continue
            sums[column] += whole(value)
            values.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": options.get(column),
                    "measure": 1 if column in options else measures[column],
                    "value": whole(value),
                }
            )
    gaps = {k: (sums[k], round(v)) for k, v in printed.items() if k != "JUNTAS" and sums[k] != round(v)}
    COMPARE[str(day)] = {"referendum:0": {"gaps": gaps, "units": len(unit_rows)}, "referendum:0:total": dict(sums)}
    p.frame("reporting_unit", pl.DataFrame(unit_rows, infer_schema_length=None))
    p.frame(
        "result_value",
        pl.DataFrame(values, infer_schema_length=None).with_columns(
            channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
        ),
    )
    return p
