"""Costa Rica's Tribunal Supremo de Elecciones: the "cómputo de votos" workbooks of every national election, and the
polling-table counts of its 2026 interactive report."""

import functools
import json
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import uuid5

import fastexcel
import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.cr import declared

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


MUNICIPAL_ARCHIVES = ("computovotos_febrero2016", "computovotos_febrero2020", "computovotos_febrero2024")
REPORT_PAGE = f"{BASE}/computodevotos2026.html"
REPORT_QUERY = "https://wabi-south-central-us-api.analysis.windows.net/public/reports/querydata?synchronous=true"
REPORT_KEY = "1df703ba-e3f0-4af4-aa93-e4684214e524"
REPORT_MODEL = 12883096
REPORT_TABLES = {
    "juntas": ("JUNTAS-CODIGOE", ("JUNTA", "CODIGOE", "CODIGO_CENTRO")),
    "centros": ("CENTROS DE VOTACION", ("COD_CENTRO", "NOMBRE", "TIPO")),
    "distritos": ("DISTRITO ELECTORAL", ("PCE", "PCD", "NOMBRE")),
    "territorial": ("COD_TERRITORIAL", ("PCD", "provincia", "canton", "distrito")),
    "partidos": ("PARTIDOS", ("CodPartido", "descPartido", "Orden")),
}
"""The tables of the Tribunal's 2026 vote-count report, by the name each is cached under, with the columns read."""
REPORT_VOTES = ("COMPUTO FEB 2026", ("TIPOE", "Junta", "CodPartido", "votos"))
REPORT_PAGE_ROWS = 30000


def fetch() -> None:
    raw.fetch(SOURCE, LIST_URL, suffix=".html")
    for name in (*ARCHIVES, *MUNICIPAL_ARCHIVES):
        raw.fetch(SOURCE, zip_url(name), suffix=".zip")
    declared.fetch()
    fetch_report()


def report_query(entity: str, columns: tuple[str, ...], restart: list[list[str]] | None = None) -> dict[str, object]:
    """A query of one table of the report's data model, every row at the grain of the columns, one page at a time."""
    window: dict[str, object] = {"Count": REPORT_PAGE_ROWS}
    if restart is not None:
        window["RestartTokens"] = restart
    select = [
        {"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": column}, "Name": f"t.{column}"}
        for column in columns
    ]
    command = {
        "Query": {"Version": 2, "From": [{"Name": "t", "Entity": entity, "Type": 0}], "Select": select},
        "Binding": {
            "Primary": {"Groupings": [{"Projections": list(range(len(columns)))}]},
            "DataReduction": {"DataVolume": 4, "Primary": {"Window": window}},
            "Version": 1,
        },
    }
    return {
        "version": "1.0.0",
        "queries": [{"Query": {"Commands": [{"SemanticQueryDataShapeCommand": command}]}}],
        "cancelQueries": [],
        "modelId": REPORT_MODEL,
    }


def report_url(name: str) -> str:
    return f"{REPORT_QUERY}#{name}"


def report_store(name: str, body: dict[str, object]) -> raw.Artifact:
    try:
        return raw.cached(SOURCE, report_url(name))
    except LookupError:
        response = httpx.post(
            REPORT_QUERY,
            json=body,
            headers={"X-PowerBI-ResourceKey": REPORT_KEY, "User-Agent": raw.USER_AGENT},
            timeout=300,
        )
        response.raise_for_status()
        return raw.store(
            SOURCE,
            report_url(name),
            response.content,
            suffix=".json",
            retrieved_at=datetime.now(UTC),
            media_type="application/json",
        )


def report_names() -> list[str]:
    """The cached pages of the report, in order: its tables, then the pages of the votes by polling table."""
    names = list(REPORT_TABLES)
    page = 0
    while True:
        names.append(f"computo-{page}")
        _, more = dsr(raw.cached(SOURCE, report_url(names[-1])).path.read_bytes())
        if more is None:
            return names
        page += 1


def fetch_report() -> None:
    raw.fetch(SOURCE, REPORT_PAGE, suffix=".html")
    for name, (entity, columns) in REPORT_TABLES.items():
        _, more = dsr(report_store(name, report_query(entity, columns)).path.read_bytes())
        if more is not None:
            raise ValueError(f"the report's {entity} table has more than one page")
    restart = None
    page = 0
    while True:
        content = report_store(f"computo-{page}", report_query(*REPORT_VOTES, restart)).path.read_bytes()
        _, restart = dsr(content)
        if restart is None:
            return
        page += 1


def dsr(content: bytes) -> tuple[list[list[object]], list[list[str]] | None]:
    """The rows of one page of a report query's answer, and the token that continues it, or None on the last page.
    The answer compresses each row against the one before it: a bit of R repeats a column's value, a bit of Ø
    leaves it empty, and a column with a dictionary holds the index of its value."""
    answer = json.loads(content)
    data = answer["results"][0]["result"]["data"]
    shape = data["dsr"]["DS"][0]
    dictionaries = shape.get("ValueDicts", {})
    out: list[list[object]] = []
    columns: list[dict[str, object]] = []
    previous: list[object] = []
    for group in shape["PH"]:
        for members in group.values():
            for member in members:
                if "S" in member:
                    columns = member["S"]
                    previous = [None] * len(columns)
                given = list(member.get("C", []))
                repeat, empty = member.get("R", 0), member.get("Ø", 0)
                row: list[object] = []
                for index, column in enumerate(columns):
                    if repeat >> index & 1:
                        value = previous[index]
                    elif empty >> index & 1:
                        value = None
                    else:
                        value = given.pop(0)
                        if "DN" in column and isinstance(value, int):
                            value = dictionaries[column["DN"]][value]
                    row.append(value)
                if given:
                    raise ValueError(f"a row of the report holds {len(given)} more values than its columns")
                previous = row
                out.append(row)
    return out, None if shape.get("IC", True) else shape.get("RT")


def report_table(name: str) -> list[list[object]]:
    return dsr(raw.cached(SOURCE, report_url(name)).path.read_bytes())[0]


CENTROS, NESTED_LEVEL, JUNTAS = "centro", "nested", "junta"
UNIT_NOUN = {CENTROS: "voting center", NESTED_LEVEL: "electoral district", JUNTAS: "polling table"}


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
    "TOTAL GENRAL": "Total General",
    "REPUBLICACNO SOCIAL CRISTIANO": "Republicano Social Cristiano",
    "RESTAURACION NACIONAN": "Restauración Nacional",
    "UNIDAD SOCIAL CRITIANA": "Unidad Social Cristiana",
    "HUMANISTA ECOLOGICO BARVENO": "Humanista Ecologista Barveño",
    "CAMBI0 2000": "Cambio 2000",
    "FUERZA DEMOCRTACICA": "Fuerza Democrática",
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
        (
            col
            for col in range(frame.width)
            if any(fold(cell(frame, r, col)).removeprefix("TOTAL ").startswith("JUNTAS") for r in range(2, 5))
        ),
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
    if (
        not label
        or label in SKIP
        or "%" in label
        or label.startswith(("JUNTAS", "TOTAL JUNTAS", "PARTICIPACION", "ABSTENCION"))
    ):
        return 0
    return None


PROVINCE = re.compile(r"^PROVINCIA\b")
CANTON = re.compile(r"^CANTON:?\s*(\d+)?\s*(.*)$")
DISTRICT = re.compile(r"^DISTRI+TO:?\s*(\d+)?\s*(.*)$")
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


def tree(table: Table, province: int, totals: dict[int, dict[str, float]] | None = None) -> list[Leaf]:
    """The leaves of a table whose rows are nested by markers: province, canton, district, then the finest unit. The
    printed row of each canton is added to `totals` by the canton's number."""
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
            if totals is not None:
                totals[canton[0]] = row.values
        elif not label and canton is not None:
            districts += 1
            district = (districts, "")
        elif found := DISTRICT.match(label):
            districts += 1
            district = (int(found[1]) if found[1] else districts, re.sub(r"^DISTRI+TO:?\s*\d*\s*", "", shown.upper()))
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
        {2: ("president", 1), 3: ("assembly", 1), 4: ("council", 1)},
        NESTED_LEVEL,
        {2: (1, 9), 3: (1, 9), 4: (1, 8)},
    ),
    Held(date(2002, 4, 7), (WORKBOOK_2002,), {2: ("president", 2)}, NESTED_LEVEL, {2: (10, 17)}),
    Held(
        date(2006, 2, 5),
        (
            Workbook("computovotos_febrero2006", r"CAPITULO 2 ELECCIONES PRESIDENCIALES FEBRERO 2006 MENU\.xls"),
            Workbook("computovotos_febrero2006", r"CAPITULO 3 ELECCIONES DIPUTACIONES FEBRERO 2006 MENU\.xls"),
            Workbook("computovotos_febrero2006", r"CAPITULO 4 ELECCIONES REGIDUR.AS FEBRERO 2006 MENU\.xls"),
        ),
        {2: ("president", 1), 3: ("assembly", 1), 4: ("council", 1)},
        NESTED_LEVEL,
    ),
    Held(
        date(2010, 2, 7),
        (
            Workbook("computovotos_febrero2010", r"1 ComputoVotos_ febrero2010_Presidente\.xlsm"),
            Workbook("computovotos_febrero2010", r"2 ComputoVotos_ febrero2010_Diputados\.xlsm"),
            Workbook("computovotos_febrero2010", r"3 ComputoVotos_ febrero2010_Regidores\.xlsm"),
        ),
        {1: ("president", 1), 2: ("assembly", 1), 3: ("council", 1)},
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
    Held(date(2026, 2, 1), (NESTED[2],), {1: ("president", 1), 2: ("assembly", 1)}, JUNTAS),
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
DECLARATORIAS = key("source", "declaratorias")
ASSEMBLY = key("body", "assembly")
PRESIDENCY = key("office", "president")
PRESIDENT_SYSTEM = key("electoral_system", "president")
ASSEMBLY_SYSTEM = key("electoral_system", "assembly")
COUNCIL_SYSTEM = key("electoral_system", "council")
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
JUNTA_METHOD = (
    "Counted by the Tribunal Supremo de Elecciones, as published in its interactive report of the 2026 vote count "
    "(cómputo de votos), linked from its page for the count. The report's data model holds the count of every "
    "polling table (junta receptora de votos): the registered electorate (electorado), the votes received (ballots "
    "cast), the valid votes by party, the null votes (nulos), and the blank votes (blancos), stored for each table as "
    "the report gives them, read through the report's public query service. Valid votes exclude the blank votes. Each "
    "polling table sits under its voting center (centro de votación); tables abroad sit under their consulate. The "
    "national and provincial totals are those printed in the Tribunal's workbook of the same count, and every one of "
    "them equals the sum of the tables."
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
    for code, (_, canton, district) in report().districts.items():
        if code[0] in "1234567":
            cantons.setdefault(code[:3], title(canton))
            districts.setdefault(code, title(district))
    return provinces, cantons, districts


def council_cantons() -> dict[str, str]:
    """The name of each canton that elected a municipal council in a national election, by its code."""
    found: dict[str, str] = {}
    for held in sorted(HELD, key=lambda h: h.day):
        for chapter, (family, _) in held.chapters.items():
            if family == "council":
                for province, canton, groups in scopes(chosen(held), chapter, family):
                    found[f"{province}{canton:02d}"] = canton_name(groups)
    return found


def sources(p: Package, *, with_report: bool = False) -> None:
    for name in ARCHIVES:
        p.uses(raw.cached(SOURCE, zip_url(name)))
    p.uses(raw.cached(SOURCE, LIST_URL))
    if with_report:
        for artifact in report_artifacts():
            p.uses(artifact)


def report_artifacts() -> list[raw.Artifact]:
    return [raw.cached(SOURCE, REPORT_PAGE), *(raw.cached(SOURCE, report_url(name)) for name in report_names())]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Costa Rica's 7 provinces, cantons, and administrative districts by the Tribunal Supremo de Elecciones' codes, "
        "and the schemes of its voting centers and polling tables",
    )
    p.external |= {COSTA_RICA, COUNTRY}
    sources(p, with_report=True)
    for code, rank, kind, noun in (
        ("cr.provincia", 1, "administrative", "province"),
        ("cr.canton", 2, "administrative", "canton"),
        ("cr.distrito", 3, "administrative", "district"),
        ("cr.distrito_electoral", 4, "precinct", "electoral district"),
        ("cr.centro", 4, "precinct", "voting center"),
        ("cr.pais", 4, "precinct", "country abroad"),
        ("cr.junta", 5, "precinct", "polling table"),
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
    (
        COUNCIL_SYSTEM,
        {"formula": "hare"},
        (
            "The regidores, the voting members of each canton's municipal council, are elected from closed party "
            "lists in the canton. Seats go first to each list's whole quotients, then to lists that reach a "
            "subquotient of half the quotient, and the remainder to the largest remainders. The number of regidores "
            "depends on the canton. The workbooks do not print it, so it is not stored."
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
    p.add(
        "source",
        id=DECLARATORIAS,
        kind="official_body",
        publisher="Tribunal Supremo de Elecciones",
        title="Resoluciones declaratorias de elección",
        url=declared.INDEX_URL,
        license="CC BY-SA 4.0",
        license_url="http://creativecommons.org/licenses/by-sa/4.0/",
        attribution=SOURCE_NOTE,
    )
    p.artifact(raw.cached(SOURCE, declared.INDEX_URL), DECLARATORIAS)
    for resolution in declared.available():
        p.artifact(raw.cached(SOURCE, declared.url(resolution)), DECLARATORIAS)
    for artifact in report_artifacts():
        p.artifact(artifact, TSE_SOURCE)
    p.add("body", id=ASSEMBLY, polity_id=COSTA_RICA, kind="unicameral")
    p.name(ASSEMBLY, "Legislative Assembly of Costa Rica", "en", "official")
    p.name(ASSEMBLY, "Asamblea Legislativa", "es", "native", False)
    for code, name in sorted(council_cantons().items()):
        id = council(code)
        p.add("body", id=id, polity_id=COSTA_RICA, kind="council")
        p.name(id, f"Municipal council of {name}", "en", "official")
        p.name(id, f"Concejo Municipal de {name}", "es", "native", False)
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
    cantons: dict[tuple[int, int, int], dict[str, float]] = field(default_factory=dict)
    """The printed row of each canton in a nested table, by chapter, province, and canton number."""


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
            out.declared[chapter] = printed_totals(frame)
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
            totals: dict[int, dict[str, float]] = {}
            try:
                leaves = tree(table, province, totals)
            except ValueError:
                continue
            if leaves:
                out.cantons |= {(chapter, province, number_): values for number_, values in totals.items()}
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


def printed_totals(frame: pl.DataFrame) -> dict[str, dict[str, float]]:
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
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{day}" for day in days),
        MUNICIPAL_REGISTRY,
        *(f"{SOURCE}/{m.day}" for m in MUNICIPAL),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for day in sorted({held.day for held in HELD}):
        election(day).write()
    referendum().write()
    municipal_registry().write()
    for m in MUNICIPAL:
        municipal(m).write()
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


@functools.lru_cache(maxsize=8)
def seat_table(day: date) -> declared.Seats | None:
    """The workbook's table of the deputies elected by province and party, for the days it is read."""
    sheet = declared.SEAT_SHEETS.get(day)
    if sheet is None:
        return None
    wb = next(held for held in HELD if held.day == day).books[0]
    return declared.seats(book(wb.zip, wb.member)[sheet])


def magnitude(day: date, province: int) -> int:
    table = seat_table(day)
    return table.magnitude[province] if table else SEATS[province]


def resolution_for(day: date, kind: str, round_number: int = 1) -> declared.Resolution | None:
    return next(
        (r for r in declared.available() if (r.day, r.kind, r.round) == (day, kind, round_number)),
        None,
    )


@functools.lru_cache(maxsize=8)
def elected_deputies(day: date) -> dict[int, list[declared.Elected]]:
    resolution = resolution_for(day, "assembly")
    if resolution is None:
        return {}
    found = (declared.deputies_pdf if resolution.path.endswith(".pdf") else declared.deputies_html)(resolution)
    grouped: dict[int, list[declared.Elected]] = {}
    for elected in found:
        grouped.setdefault(elected.place, []).append(elected)
    return grouped


def cite(p: Package, resolution: declared.Resolution, result_set: str) -> None:
    p.add(
        "citation",
        id=key("citation", result_set, resolution.number),
        entity_id=result_set,
        source_id=DECLARATORIAS,
        locator=json.dumps({"resolution": resolution.number}),
    )


def person_of(p: Package, elected: declared.Elected, *tag: object) -> str:
    person = key("person", *tag, fold(elected.name))
    p.add("person", id=person)
    p.name(person, title(elected.name), "es", "official")
    return person


def declare_assembly(p: Package, day: date, province: int, result_set: str, options: dict[str, str]) -> None:
    """Store the seats each party won in a province as the workbook declares them, and the deputies named elected."""
    table = seat_table(day)
    if table is None:
        return
    for name, seats in sorted(table.won[province].items()):
        if name not in options:
            raise KeyError(f"{day} {PROVINCE_NAMES[province - 1]}: no list for {name}")
        p.add("outcome", result_set_id=result_set, option_id=options[name], kind="won", seats=seats)
    note = (
        "The seats won by each party are those the Tribunal declares in its table of deputies elected by province "
        "and party."
    )
    resolution = resolution_for(day, "assembly")
    elected = elected_deputies(day).get(province, [])
    if resolution is not None:
        names = Counter(e.party for e in elected)
        if names != Counter(table.won[province]):
            raise ValueError(f"{day} {PROVINCE_NAMES[province - 1]}: {names} against {table.won[province]}")
        for deputy in elected:
            assert deputy.party is not None
            tag = (day, "assembly", province, deputy.party)
            person = person_of(p, deputy, *tag)
            candidacy = key("candidacy", *tag, fold(deputy.name))
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[deputy.party],
                role="list_member",
                member_of_party_id=party(deputy.party),
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[deputy.party],
                candidacy_id=candidacy,
                kind="elected",
            )
        cite(p, resolution, result_set)
        note += f" The deputies named are those the Tribunal declares elected in resolution n.º {resolution.number}."
    p.add(
        "note",
        id=key("note", result_set, "declared"),
        entity_id=result_set,
        kind="methodology",
        body_md=note,
        lang="en",
    )


def declare_president(p: Package, day: date, round_number: int, result_set: str, options: dict[str, str]) -> None:
    """Store which tickets the Tribunal declares elected or sends to a second round, and the elected ticket's names."""
    resolution = resolution_for(day, "president", round_number)
    if resolution is None:
        return
    names, winner = declared.ticket(resolution)
    if names:
        if winner not in options:
            raise KeyError(f"{day}: no ticket for {winner}")
        for column, option in options.items():
            kind = "elected" if column == winner else "lost"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        for member in names:
            tag = (day, "president", round_number, winner)
            person = person_of(p, member, *tag)
            p.add(
                "candidacy",
                id=key("candidacy", *tag, fold(member.name)),
                person_id=person,
                option_id=options[winner],
                role=member.role,
                list_position=member.place or None,
                member_of_party_id=party(winner),
            )
        what = f"The president and vice presidents named are those the Tribunal declares elected in resolution n.º {resolution.number}."
    else:
        runoff = declared.advancing(resolution)
        missing = [name for name in runoff if name not in options]
        if missing:
            raise KeyError(f"{day}: no ticket for {missing}")
        for column, option in options.items():
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="advanced" if column in runoff else "eliminated",
            )
        what = f"The tickets sent to a second round are those the Tribunal names in resolution n.º {resolution.number}."
    cite(p, resolution, result_set)
    p.add(
        "note",
        id=key("note", result_set, "declared"),
        entity_id=result_set,
        kind="methodology",
        body_md=what,
        lang="en",
    )


def scopes(found: Chosen, chapter: int, family: str) -> list[tuple[int, int, list[tuple[Table, list[Leaf], str]]]]:
    """The contests of a chapter: the country's president, a province's list, or a canton's council, each with the
    tables and leaves that hold it."""
    if family == "president":
        return [(0, 0, [item for (ch, _), items in sorted(found.leaves.items()) if ch == chapter for item in items])]
    out: list[tuple[int, int, list[tuple[Table, list[Leaf], str]]]] = []
    for province in sorted(pr for (ch, pr) in found.leaves if ch == chapter and pr):
        groups = found.leaves[(chapter, province)]
        if family != "council":
            out.append((province, 0, groups))
            continue
        for canton in sorted({leaf.canton[0] for _, leaves, _ in groups for leaf in leaves if leaf.canton}):
            out.append(
                (
                    province,
                    canton,
                    [
                        (table, [leaf for leaf in leaves if leaf.canton and leaf.canton[0] == canton], scheme)
                        for table, leaves, scheme in groups
                    ],
                )
            )
    return out


def canton_name(groups: list[tuple[Table, list[Leaf], str]]) -> str:
    return next(title(leaf.canton[1]) for _, leaves, _ in groups for leaf in leaves if leaf.canton and leaf.canton[1])


def council(code: str) -> str:
    return key("body", "council", code)


def printed_key(column: str) -> str | None:
    """The key of a printed column in a province's tally: the party's name, or the number of its accounting measure."""
    if column.startswith("VOTOS VALIDOS"):
        return str(VALID)
    if column in ("VOTOS RECIBIDOS", "TOTAL VOTOS RECIBIDOS"):
        return str(CAST)
    measure = measure_of(column)
    if measure is None:
        return column
    return str(measure) if measure else None


def compare_provinces(
    tally: dict[int, Counter[str]], declared_by_scope: dict[str, dict[str, float]]
) -> dict[str, dict[str, tuple[int, int]]]:
    """The gaps between the sums of the cantons' councils and the totals printed for each province and the country."""
    gaps: dict[str, dict[str, tuple[int, int]]] = {}
    country: Counter[str] = Counter()
    for counts in tally.values():
        country.update(counts)
    for scope_name, counts in [("COSTA RICA", country)] + [
        (fold(PROVINCE_NAMES[province - 1]), counts) for province, counts in tally.items()
    ]:
        found: dict[str, tuple[int, int]] = {}
        for column, value in declared_by_scope.get(scope_name, {}).items():
            key_ = printed_key(column)
            if key_ is None or "JUNTAS" in column:
                continue
            if counts[key_] != round(value):
                found[column] = (counts[key_], round(value))
        gaps[scope_name] = found
    return gaps


@dataclass(frozen=True)
class Center:
    code: int
    name: str
    kind: str
    district: str  # the TSE code of its administrative district, or "" abroad


@dataclass
class Report:
    """The 2026 vote-count report: each polling table's voting center, and the votes of each table."""

    juntas: dict[int, Center] = field(default_factory=dict)
    provinces: dict[int, int] = field(default_factory=dict)  # polling table -> province, 0 abroad
    parties: dict[int, tuple[str, int]] = field(default_factory=dict)  # code -> (name, order on the report)
    votes: dict[tuple[int, int, int], int] = field(default_factory=dict)  # (contest family, table, code) -> count
    districts: dict[str, tuple[str, str, str]] = field(default_factory=dict)  # TSE code -> province, canton, district


REPORT_FAMILY = {"president": 1, "assembly": 2}
REPORT_MEASURES = {501: REGISTERED, 502: CAST, 503: VALID, 504: INVALID, 505: BLANK}
"""The report's codes for the accounting rows, by measure; 500 counts the tables and 506 the abstentions."""
REPORT_SKIP = {500, 506}
PRISON = 9  # the report's type of center for an institutional care center (centro de atención institucional)


@functools.cache
def report() -> Report:
    out = Report()
    districts = {int(str(pce)): str(pcd) for pce, pcd, _ in report_table("distritos")}
    for pcd, province, canton, district in report_table("territorial"):
        out.districts[str(pcd)] = (str(province), str(canton), str(district))
    centers: dict[int, tuple[str, int]] = {
        int(str(code)): (" ".join(str(name).split()), int(str(kind))) for code, name, kind in report_table("centros")
    }
    for number, codigo, center in report_table("juntas"):
        number, codigo, center = int(str(number)), int(str(codigo)), int(str(center))
        name, kind = centers[center]
        province = codigo // 100000
        if province == 8:
            country = out.districts[districts[codigo]][1]
            out.juntas[number] = Center(center, f"{title(country)}: {title(name)}", "abroad_mission", "")
            out.provinces[number] = 0
        else:
            out.juntas[number] = Center(
                center, title(name), "prison" if kind == PRISON else "standard", districts[codigo]
            )
            out.provinces[number] = province
    for code, name, order in report_table("partidos"):
        out.parties[int(str(code))] = (str(name), int(str(order)))
    for name in report_names():
        if not name.startswith("computo-"):
            continue
        for family, number, code, count in dsr(raw.cached(SOURCE, report_url(name)).path.read_bytes())[0]:
            at = (int(str(family)), int(str(number)), int(str(code)))
            if out.votes.setdefault(at, int(str(count))) != int(str(count)):
                raise ValueError(f"the report gives two counts for {at}")
    return out


def tally(
    p: Package,
    contest: tuple[date, str, int, str, str],
    chapter: int,
    tables: Chosen,
    options: dict[str, str],
    units: dict[str, str],
    unit_rows: list[dict[str, object]],
) -> None:
    """The options of one contest, from the parties the workbook's totals print for it, in the report's order, and
    the voting centers and polling tables the report counts it in."""
    day, family, province, contest_id, _ = contest
    data = report()
    scope = "COSTA RICA" if family == "president" else fold(PROVINCE_NAMES[province - 1])
    running = {column for column in tables.declared[chapter][scope] if measure_of(column) is None}
    by_name = {fold(name): (code, name, order) for code, (name, order) in data.parties.items() if code < 500}
    if running - set(by_name):
        raise LookupError(f"{day} {family} {province}: {sorted(running - set(by_name))} are not on the report")
    for column in sorted(running, key=lambda column: by_name[column][2]):
        option = options[column] = key("option", day, family, province, column)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest_id,
            kind="ticket" if family == "president" else "list",
            party_id=party(column),
            ballot_position=len(options),
            ballot_label=by_name[column][1],
        )
    for number, center in sorted(data.juntas.items()):
        if family == "assembly" and data.provinces[number] != province:
            continue
        abroad_unit = not center.district
        place = {
            "polity_id": COSTA_RICA,
            "area_relation": "unlocated" if abroad_unit else "within",
            "area_id": None if abroad_unit else area("cr.distrito", center.district),
            "kind": center.kind,
        }
        parent = f"{day}:{center.code}"
        if parent not in units:
            units[parent] = key("unit", parent)
            unit_rows.append(
                {"id": units[parent], "scheme_code": "cr.centro", "code": parent, "parent_id": None}
                | place
                | {"name": center.name}
            )
        code = f"{day}:junta:{number}"
        if code not in units:
            units[code] = key("unit", code)
            unit_rows.append(
                {"id": units[code], "scheme_code": "cr.junta", "code": code, "parent_id": units[parent]}
                | place
                | {"name": f"Junta {number}"}
            )


def tallied(
    day: date,
    result_set: str,
    family: str,
    province: int,
    options: dict[str, str],
    sums: Counter[tuple[str | None, int]],
) -> list[dict[str, object]]:
    """The values of one contest from the report, one row per polling table and party or accounting measure."""
    data = report()
    wanted = REPORT_FAMILY[family]
    codes = {code: fold(name) for code, (name, _) in data.parties.items()}
    out: list[dict[str, object]] = []
    for (kind, number, code), count in sorted(data.votes.items()):
        if kind != wanted or code in REPORT_SKIP or (family == "assembly" and data.provinces[number] != province):
            continue
        if code in REPORT_MEASURES:
            option_id, measure = None, REPORT_MEASURES[code]
        elif codes[code] in options:
            option_id, measure = options[codes[code]], 1
        elif count:
            raise ValueError(f"{family} {province}: {count} votes for {codes[code]}, which the totals do not print")
        else:
            continue
        sums[(option_id, measure)] += count
        out.append(
            {
                "result_set_id": result_set,
                "unit_id": key("unit", f"{day}:junta:{number}"),
                "option_id": option_id,
                "measure": measure,
                "value": count,
            }
        )
    return out


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
        f"{UNIT_NOUN[held.level]} from the Tribunal Supremo de Elecciones' vote count",
        depends,
    )
    p.external |= {COSTA_RICA, COUNTRY}
    sources(p, with_report=held.level == JUNTAS)
    for resolution in declared.available():
        if resolution.day.year == day.year:
            p.uses(raw.cached(SOURCE, declared.url(resolution)))
    p.uses(raw.cached(SOURCE, declared.INDEX_URL))
    chosen_tables = chosen(held)
    method = (
        JUNTA_METHOD if held.level == JUNTAS else METHOD + (CENTER_METHOD if held.level == CENTROS else DISTRICT_METHOD)
    )
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
    by_province: dict[int, Counter[str]] = {province: Counter() for province in range(1, 8)}
    for chapter, (family, round_number) in sorted(held.chapters.items()):
        for province, canton, groups in scopes(chosen_tables, chapter, family):
            ident = f"{province}{canton:02d}" if family == "council" else province
            rows = f"{family}:{ident}"
            contest = key("contest", day, family, ident)
            result_set = key("result_set", day, family, ident)
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
            elif family == "council":
                name = canton_name(groups)
                p.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="list_allocation",
                    body_id=council(f"{province}{canton:02d}"),
                    district_area_id=area("cr.canton", f"{province}{canton:02d}"),
                    round_number=1,
                    electoral_system_id=COUNCIL_SYSTEM,
                    default_result_kind="counted",
                )
                p.name(contest, f"Municipal council: {name}", "en", "official")
                p.name(contest, f"Regidurías municipales: {name}", "es", "native", False)
                slug = f"{path}/municipal-council-{changeset.slug(PROVINCE_NAMES[province - 1])}-{changeset.slug(name)}"
            else:
                p.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="list_allocation",
                    body_id=ASSEMBLY,
                    district_area_id=area("cr.provincia", str(province)),
                    magnitude=magnitude(day, province),
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
            sums: Counter[tuple[str | None, int]] = Counter()
            if held.level == JUNTAS:
                tally(
                    p, (day, family, province, contest, result_set), chapter, chosen_tables, options, units, unit_rows
                )
                values += tallied(day, result_set, family, province, options, sums)
            else:
                ran = {col for _, leaves, _ in groups for leaf in leaves for col, v in leaf.values.items() if v > 0}
                for table, _, _ in groups:
                    for column, text in zip(table.columns, table.shown, strict=True):
                        if (
                            measure_of(column) is None
                            and column not in options
                            and (family != "council" or column in ran)
                        ):
                            option = options[column] = key("option", day, family, ident, column)
                            p.add(
                                "contest_option",
                                id=option,
                                contest_id=contest,
                                kind="ticket" if family == "president" else "list",
                                party_id=party(column),
                                ballot_position=len(options),
                                ballot_label=text,
                            )
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
            if family == "council":
                printed_rows = [chosen_tables.cantons.get((chapter, province, canton), {})]
            else:
                printed_rows = [
                    printed.get("COSTA RICA" if family == "president" else fold(PROVINCE_NAMES[province - 1]), {})
                ]
            gaps: dict[str, tuple[int, int]] = {}
            for printed_row in printed_rows:
                for column, value in printed_row.items():
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
            if family == "president":
                declare_president(p, day, round_number, result_set, options)
            elif family == "assembly":
                declare_assembly(p, day, province, result_set, options)
            comparison[rows] = {
                "gaps": gaps,
                "units": len({v["unit_id"] for v in values if v["result_set_id"] == result_set}),
            }
            comparison[rows + ":total"] = {measure: sums[(None, measure)] for measure in (2, 3, 4, 5, 6)}
            if family == "council":
                for column, option in options.items():
                    by_province[province][column] += sums[(option, 1)]
                for measure in (2, 3, 4, 5, 6):
                    by_province[province][str(measure)] += sums[(None, measure)]
        if family == "council":
            comparison["council:provinces"] = compare_provinces(by_province, chosen_tables.declared.get(chapter, {}))
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


# ---------------------------------------------------------------------------------------------------------------
# The municipal elections

MUNICIPAL_REGISTRY = f"{SOURCE}/municipal-registry"
MAYOR, COUNCIL, DISTRICT_COUNCIL, INTENDANT = "alcaldia", "regiduria", "sindicatura", "intendencia"
TABLES_OF = {2: MAYOR, 3: COUNCIL, 4: DISTRICT_COUNCIL}
"""The chapters of a municipal workbook that hold the tables of each canton, by the contest they report."""
AREAS = {
    MAYOR: ("Mayor", "Alcaldía", "mayor"),
    COUNCIL: ("Municipal council", "Concejo municipal", "council"),
    DISTRICT_COUNCIL: ("District council", "Concejo de distrito", "council"),
    INTENDANT: ("District intendant", "Intendencia del concejo municipal de distrito", "mayor"),
}


@dataclass(frozen=True)
class Municipal:
    day: date
    book: Workbook


MUNICIPAL = (
    Municipal(date(2016, 2, 7), Workbook("computovotos_febrero2016", r"COMPUTO DE VOTOS MUNICIPALES FEB 2016\.xlsm")),
    Municipal(date(2020, 2, 2), Workbook("computovotos_febrero2020", r"COMPUTO DE VOTOS MUNICIPALES FEB 2020\.xlsm")),
    Municipal(
        date(2024, 2, 4), Workbook("computovotos_febrero2024", r"COMPUTO DE VOTOS MUNICIPALES FEB 2024 FINAL\.xlsm")
    ),
)
BLANK_PARTY = {"VOTOS POR PARTIDO"}
ALIASES |= {(1, "LEON CORTES"): "LEON CORTES CASTRO"}
GUESSED: list[str] = []
SEATS_OF = {"COLORADO": "ABANGARES"}
"""The canton of a district intendancy whose name another canton's district shares, by the district's folded name."""


@dataclass
class Piece:
    """One table of a municipal workbook: a canton's votes for one contest family, or an intendancy's."""

    family: str
    chapter: int
    province: int
    canton: int
    canton_name: str
    table: Table
    leaves: list[tuple[int, str, Leaf]]  # district number, district name, finest-level row
    totals: dict[tuple[str, str], dict[str, float]]  # folded canton and district names -> the district's printed row
    total: dict[str, float]  # the row printed for the canton


def sheet_of(frame: pl.DataFrame) -> pl.DataFrame:
    """The sheet with the dashes that stand for zero read as zeros."""
    return frame.with_columns(pl.all().replace("-", "0"))


def home(found: dict[tuple[int, str] | tuple[int, int, str], int], name: str) -> tuple[int, int]:
    """The province and the canton number of a canton named in a workbook."""
    hits: dict[int, int] = {}
    for province in range(1, 8):
        canton = ALIASES.get((province, fold(name)), name)
        number = found.get((province, fold(canton))) or found.get((province, bare(canton)))
        if number is not None:
            hits[province] = number
    if len(hits) != 1:
        raise LookupError(f"canton {name} is in provinces {sorted(hits)}")
    province, number = next(iter(hits.items()))
    return province, number


def districts(rows: list[Row]) -> dict[tuple[str, str], dict[str, float]]:
    """The values each district row prints, by the folded name of its canton and of the district."""
    found: dict[tuple[str, str], dict[str, float]] = {}
    canton = ""
    for row in rows:
        label = fold(row.path[0])
        if match := CANTON.match(label):
            canton = fold(match[2])
        elif match := DISTRICT.match(label):
            found[(canton, fold(match[2]))] = row.values
    return found


def under_cantons(rows: list[Row], found: dict[tuple[int, str] | tuple[int, int, str], int]) -> list[Row]:
    """Rows nested by district only, with a row for the canton put before the districts of each canton."""
    names = {(key[0], number): key[1] for key, number in found.items() if len(key) == 2}
    out: list[Row] = []
    last: tuple[int, int] | None = None
    for row in rows:
        match = DISTRICT.match(fold(row.path[0]))
        if match:
            district = fold(match[2])
            where = {key[:2] for key in found if len(key) == 3 and key[2] in (district, bare(match[2]))}
            if len(where) > 1 and district in SEATS_OF:
                where = {home(found, SEATS_OF[district])}
            if len(where) != 1:
                raise LookupError(f"district {match[2]} is in cantons {sorted(where)}")
            here = next(iter(where))
            if here != last:
                out.append(Row((f"CANTÓN {names[here]}",), {}))
                last = here
        out.append(row)
    return out


def pieces(m: Municipal) -> list[Piece]:
    """The canton tables of a municipal workbook for the mayor, the council, and the districts' councils, and the
    tables of the district intendancies, each read into the finest units it prints."""
    found = numbers()
    out: list[Piece] = []
    for name, raw_frame in book(m.book.zip, m.book.member).items():
        caption = heading(raw_frame)
        if not caption.startswith("CUADRO"):
            continue
        try:
            chapter, number, where = scope(caption)
        except ValueError:
            continue
        frame = sheet_of(raw_frame)
        if chapter in TABLES_OF and where.startswith("CANTON "):
            family = TABLES_OF[chapter]
            cantons = [where.removeprefix("CANTON ").strip()]
        elif chapter == 5 and number == 2:
            family, cantons = INTENDANT, []
        else:
            continue
        table = read(name, frame)
        if table is None:
            raise ValueError(f"{m.day} {name}: no table")
        for label in table.columns:
            if label in BLANK_PARTY:
                raise ValueError(f"{m.day} {name}: column {label}")
        rows = [row for row in table.rows if not row.path[0].strip().isdigit()]  # a stray row repeating a total
        if fold(rows[0].path[0]).startswith("TOTAL"):
            rows = [Row((f"CANTÓN {cantons[0]}",), rows[0].values), *rows[1:]] if cantons else rows[1:]
        if family == INTENDANT and not any(CANTON.match(fold(row.path[0])) for row in rows):
            rows = under_cantons(rows, found)
        shell = Table(
            table.sheet, table.caption, table.labels, table.columns, table.shown, [Row(("PROVINCIA",), {}), *rows]
        )
        leaves = tree(shell, 0)
        by_canton: dict[str, list[Leaf]] = {}
        for leaf in leaves:
            assert leaf.canton is not None
            by_canton.setdefault(leaf.canton[1], []).append(leaf)
        for canton_name, group in by_canton.items():
            if cantons and fold(canton_name) != fold(cantons[0]):
                raise ValueError(f"{m.day} {name}: canton {canton_name} is not {cantons}")
            province, canton = home(found, canton_name)
            located: list[tuple[int, str, Leaf]] = []
            for leaf in group:
                assert leaf.district is not None
                try:
                    _, district = locate(found, province, canton_name, leaf.district[1])
                except LookupError:
                    squashed = {
                        found[k]
                        for k in found
                        if len(k) == 3
                        and k[:2] == (province, canton)
                        and str(k[2]).replace(" ", "") == fold(leaf.district[1]).replace(" ", "")
                    }
                    if len(squashed) == 1:
                        located.append((next(iter(squashed)), leaf.district[1], leaf))
                        continue
                    district = leaf.district[0]
                    GUESSED.append(f"{m.day} {name}: {canton_name} / {leaf.district[1]} taken as {district}")
                located.append((district, leaf.district[1], leaf))
            mine = [row for row in rows if fold(row.path[0]) == fold(f"CANTON {canton_name}")]
            total = mine[0].values if mine else {}
            out.append(
                Piece(
                    family,
                    chapter,
                    province,
                    canton,
                    canton_name,
                    table,
                    located,
                    districts(rows),
                    total,
                )
            )
    return out


@functools.cache
def all_pieces() -> dict[date, list[Piece]]:
    return {m.day: pieces(m) for m in MUNICIPAL}


@dataclass
class Centro:
    """One province table of a municipal workbook: the votes of every voting center for one contest family."""

    family: str
    province: int
    table: Table
    leaves: list[Leaf]  # one for each voting center, under its canton and administrative district


CENTRO_FROM = date(2020, 2, 2)
"""The first municipal workbook whose chapters print a table of voting centers for each province."""


CENTRO_DISTRICTS = {(2, "LOS CHILES", "ROSARIO"): "LOS CHILES"}
"""Districts that a province table prints for a canton that the canton's own table counts under another district, by
province, canton, and folded name: the 2020 tables of Alajuela print the voting centers of Los Chiles that sit in
Rosario under their own district, and the canton tables count them in Los Chiles."""


def centro_leaves(day: date) -> dict[tuple[str, str], list[tuple[str, Leaf]]]:
    """The voting centers of each contest of a municipal election, by family and the code of the canton (for the
    mayor and the council) or of the administrative district (for the district council). Each center has the code
    that the national tables of voting centers give it: the day, its district, and its folded name, with a number
    after it when a district holds several centers of one name."""
    found: dict[tuple[str, str], list[tuple[str, Leaf]]] = {}
    for item in all_centros()[day]:
        counts: Counter[str] = Counter()
        for leaf in item.leaves:
            assert leaf.canton is not None and leaf.district is not None
            base = f"{day}:{code_of(leaf)}:{fold(leaf.name)}"
            counts[base] += 1
            code = base if counts[base] == 1 else f"{base}#{counts[base]}"
            here = f"{leaf.province}{leaf.canton[0]:02d}"
            seat_code = here if item.family in (MAYOR, COUNCIL) else f"{here}{leaf.district[0]:02d}"
            found.setdefault((item.family, seat_code), []).append((code, leaf))
    return found


def centro_pieces(m: Municipal) -> list[Centro]:
    """The province tables of a municipal workbook that print a row for each voting center, for the mayors, the
    councils, and the districts' councils."""
    if m.day < CENTRO_FROM:
        return []
    found = numbers()
    out: list[Centro] = []
    for name, raw_frame in book(m.book.zip, m.book.member).items():
        caption = heading(raw_frame)
        if not caption.startswith("CUADRO"):
            continue
        try:
            chapter, _, where = scope(caption)
        except ValueError:
            continue
        if chapter not in TABLES_OF or not where.startswith("PROVINCIA "):
            continue
        province = province_of(where.removeprefix("PROVINCIA ").strip())
        frame = sheet_of(raw_frame)
        for r in range(3, min(frame.height, 12)):  # a total row that prints its label in the second column
            if not cell(frame, r, 0) and fold(cell(frame, r, 1)).startswith("TOTAL "):
                frame[r, 0] = cell(frame, r, 1)
                frame[r, 1] = ""
        table = read(name, frame)
        if not province or table is None or table.labels != 3:
            continue  # a province's summary table, which prints no voting centers
        table.rows = [
            Row(
                (
                    row.path[0],
                    CENTRO_DISTRICTS.get((province, fold(row.path[0]), fold(row.path[1])), row.path[1]),
                    row.path[2],
                ),
                row.values,
            )
            for row in table.rows
            if not fold(row.path[0]).startswith("TOTAL")
        ]
        out.append(Centro(TABLES_OF[chapter], province, table, centers(table, found, province)))
    if Counter((c.family, c.province) for c in out) != Counter((f, p) for f in TABLES_OF.values() for p in range(1, 8)):
        raise ValueError(
            f"{m.day}: the workbook does not hold one table of voting centers for each province and contest"
        )
    return out


@functools.cache
def all_centros() -> dict[date, list[Centro]]:
    return {m.day: centro_pieces(m) for m in MUNICIPAL}


def printed(frame: pl.DataFrame) -> dict[str, float]:
    """The national column of a table of totals: for each row, its label and the figure printed for Costa Rica."""
    top = next(r for r in range(2, 8) if fold(cell(frame, r, 1)) == "TOTAL")
    found: dict[str, float] = {}
    for r in range(top + 1, frame.height):
        label = fold(cell(frame, r, 0))
        value = number(cell(frame, r, 1))
        if label and value is not None and not label.startswith(("FUENTE", "NOTA")):
            found[label] = value
    return found


def national(m: Municipal, chapter: int) -> dict[str, float]:
    sheets = book(m.book.zip, m.book.member)
    return printed(sheet_of(sheets[f"{chapter}.1"]))


PRINTED = {
    "ELECTORADO": REGISTERED,
    "VOTOS RECIBIDOS": CAST,
    "VOTOS POR PARTIDO": VALID,
    "VOTOS VALIDOS": VALID,
    "VOTOS NULOS": INVALID,
    "VOTOS BLANCOS": BLANK,
    "VOTOS EN BLANCO": BLANK,
}


def compare(m: Municipal, chapter: int, mine: Counter[str | int]) -> dict[str, object]:
    """How the sums of a family's contests compare with the national table of the chapter: the rows that differ."""
    differ: dict[str, tuple[int, int]] = {}
    checked = 0
    for label, value in national(m, chapter).items():
        if label in ("JUNTAS", "ABSTENCIONISMO") or "PORCENTAJE" in label or "%" in label:
            continue
        key_ = PRINTED.get(label, fold(FIXES[label]) if label in FIXES else label)
        checked += 1
        if mine[key_] != round(value):
            differ[label] = (mine[key_], round(value))
    return {"checked": checked, "differ": differ}


def national_labels() -> set[str]:
    """The party labels the national package of the registry holds."""
    found: set[str] = set()
    for held in HELD:
        for table in chosen(held).tables:
            for label in table.columns:
                if measure_of(label) is None:
                    found.add(label)
    return found


def municipal_labels() -> dict[str, str]:
    """Each party label of the municipal workbooks, with the name as printed."""
    found: dict[str, str] = {}
    for items in all_pieces().values():
        for piece in items:
            for label, text in zip(piece.table.columns, piece.table.shown, strict=True):
                if measure_of(label) is None:
                    found.setdefault(label, text)
    return found


def seat(family: str, code: str) -> str:
    return key("seat", family, code)


def municipal_places() -> dict[str, dict[str, str]]:
    """The places that have a council or an office, by family: the code of each and its name."""
    _, cantons, districts_ = places()
    found: dict[str, dict[str, str]] = {MAYOR: {}, COUNCIL: {}, DISTRICT_COUNCIL: {}, INTENDANT: {}}
    for items in all_pieces().values():
        for piece in items:
            code = f"{piece.province}{piece.canton:02d}"
            if piece.family in (MAYOR, COUNCIL):
                found[piece.family][code] = cantons[code]
            else:
                for district, _, _ in piece.leaves:
                    here = f"{code}{district:02d}"
                    found[piece.family][here] = f"{districts_[here]} ({cantons[code]})"
    return found


def municipal_registry() -> Package:
    p = Package(
        MUNICIPAL_REGISTRY,
        SOURCE,
        "The parties, municipal councils, and local offices on the ballots of Costa Rica's municipal elections, "
        "from the Tribunal Supremo de Elecciones' workbooks",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {COSTA_RICA}
    for m in MUNICIPAL:
        p.artifact(raw.cached(SOURCE, zip_url(m.book.zip)), TSE_SOURCE)
    known = national_labels()
    for label, text in sorted(municipal_labels().items()):
        if label in known:
            continue
        id = party(label)
        p.add("party", id=id, kind="party", polity_id=COSTA_RICA)
        p.name(id, title(text), "es", "official")
    for family, here in municipal_places().items():
        english, native, kind = AREAS[family]
        for code, text in sorted(here.items()):
            id = seat(family, code)
            if kind == "council":
                p.add("body", id=id, polity_id=COSTA_RICA, kind="council")
            else:
                p.add("office", id=id, polity_id=COSTA_RICA, kind=kind)
            p.name(id, f"{english} of {text}", "en", "official")
            p.name(id, f"{native} de {text}", "es", "native", False)
    return p


MUNICIPAL_METHOD = (
    "Counted by the Tribunal Supremo de Elecciones, as published in its workbook of the municipal vote count (cómputo "
    "de votos). The registered electorate (electorado), the votes received (ballots cast), the valid votes by party, "
    "the null votes (nulos), and the blank votes (blancos) are stored for each electoral district (distrito "
    "electoral) as printed. The workbook's tables of each canton print the electoral districts under each "
    "administrative district. Valid votes exclude the blank votes. The number of polling tables (juntas) is not "
    "stored. Voters mark a party for each office, so the options are the parties' candidacies and lists."
)
CENTRO_METHOD = (
    " The tables of each province, which print the voting centers (centros de votación) under each canton and "
    "administrative district, give a finer count of the same contest. Each voting center is a unit of that "
    "result set, which supersedes the one by electoral district, and every center's figures are stored as printed."
)
DISTRICT_NOTE = (
    " The tables print each party that ran in the canton for the district councils; a party with no votes in a "
    "district is left out of that district's contest."
)


def inconsistent(p: Package, values: pl.DataFrame) -> None:
    """Accept the units whose printed figures do not add up, and say how many there are: the figures are the
    Tribunal's own and are stored as printed."""
    wide = (
        values.filter(pl.col("option_id").is_null())
        .pivot(on="measure", index=["result_set_id", "unit_id"], values="value", aggregate_function="first")
        .rename({str(m): f"m{m}" for m in (CAST, VALID, INVALID, BLANK)})
    )
    parties = (
        values.filter(pl.col("option_id").is_not_null())
        .group_by("result_set_id", "unit_id")
        .agg(pl.col("value").sum().alias("parties"))
    )
    wide = wide.join(parties, on=["result_set_id", "unit_id"], how="left")
    valid = wide.filter(pl.col("parties") != pl.col(f"m{VALID}"))
    ballots = wide.filter(pl.col(f"m{CAST}") != pl.col(f"m{VALID}") + pl.col(f"m{INVALID}") + pl.col(f"m{BLANK}"))
    for code, found, what in (
        ("accounting.votes_valid", valid, "valid votes that differ from the votes for the parties"),
        ("accounting.ballots_sum", ballots, "votes received that differ from the valid, null, and blank votes"),
    ):
        if not found.is_empty():
            p.accept(
                code,
                f"{found.height} unit(s) print {what}; the figures are the Tribunal's own and are stored as printed",
            )


def municipal(m: Municipal) -> Package:
    day = m.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Costa Rica's municipal elections of {day:%-d %B %Y} (mayors, municipal councils, district councils, and "
        "district intendants) by electoral district from the Tribunal Supremo de Elecciones' vote count",
        [f"{SOURCE}/geo", f"{SOURCE}/registry", MUNICIPAL_REGISTRY],
    )
    p.external |= {COSTA_RICA, COUNTRY}
    p.uses(raw.cached(SOURCE, zip_url(m.book.zip)))
    p.uses(raw.cached(SOURCE, LIST_URL))
    election_id = key("election", day, "municipal")
    path = f"cr/{day}-municipal"
    p.add("election", id=election_id, polity_id=COSTA_RICA, kind="local", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{day.year} Costa Rican municipal elections", "en", "official")
    p.name(election_id, f"Elecciones municipales {day.year}", "es", "native", False)
    _, cantons, districts_ = places()
    units: dict[str, str] = {}
    unit_rows: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    slugs: set[str] = set()
    comparison: dict[str, object] = {}
    national_sums: dict[str, Counter[str | int]] = {family: Counter() for family in AREAS}
    gaps: dict[str, object] = {}
    finer = centro_leaves(day)
    centro_sums: dict[str, Counter[str | int]] = {family: Counter() for family in AREAS}
    centro_units: dict[str, str] = {}
    centro_rows: list[dict[str, object]] = []
    differ: dict[str, object] = {}
    for piece in all_pieces()[day]:
        code = f"{piece.province}{piece.canton:02d}"
        if piece.family in (MAYOR, COUNCIL):
            groups = [("", piece.leaves, piece.total, code, cantons[code], cantons[code])]
        else:
            by: dict[int, list[tuple[int, str, Leaf]]] = {}
            for item in piece.leaves:
                by.setdefault(item[0], []).append(item)
            groups = []
            for district, found in sorted(by.items()):
                here = f"{code}{district:02d}"
                total = piece.totals.get((fold(piece.canton_name), fold(found[0][1])), {})
                groups.append((here, found, total, here, districts_[here], f"{districts_[here]} ({cantons[code]})"))
        for suffix, leaves, total, seat_code, area_name, label in groups:
            family = piece.family
            contest = key("contest", day, family, seat_code)
            result_set = key("result_set", day, family, seat_code)
            is_office = family in (MAYOR, INTENDANT)
            area_id = area("cr.canton", code) if family in (MAYOR, COUNCIL) else area("cr.distrito", seat_code)
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="office" if is_office else "list_allocation",
                office_id=seat(family, seat_code or code) if is_office else None,
                body_id=None if is_office else seat(family, seat_code or code),
                district_area_id=area_id,
                round_number=1,
                default_result_kind="counted",
            )
            english, native, _ = AREAS[family]
            p.name(contest, f"{english} of {label}", "en", "official")
            p.name(contest, f"{native} de {label}", "es", "native", False)
            slug = f"{path}/{family}-{changeset.slug(label)}"
            if slug in slugs:
                slug += f"-{seat_code or code}"
            slugs.add(slug)
            p.add("slug", path=slug, entity_id=contest, is_current=True)
            p.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=TSE_SOURCE,
                status="superseded" if (family, seat_code) in finer else "active",
                is_default=(family, seat_code) not in finer,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=MUNICIPAL_METHOD + (DISTRICT_NOTE if family == DISTRICT_COUNCIL else ""),
                lang="en",
            )
            options: dict[str, str] = {}
            for column, text in zip(piece.table.columns, piece.table.shown, strict=True):
                if measure_of(column) is not None:
                    continue
                if family == DISTRICT_COUNCIL and not any(leaf.values.get(column, 0) for _, _, leaf in leaves):
                    continue
                option = options[column] = key("option", day, family, seat_code or code, column)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="ticket" if is_office else "list",
                    party_id=party(column),
                    ballot_position=len(options),
                    ballot_label=text,
                )
            mine: Counter[tuple[str | None, int]] = Counter()
            repeats: Counter[str] = Counter()
            for district, _, leaf in leaves:
                here_code = f"{piece.province}{piece.canton:02d}{district:02d}"
                base = f"{day}:{here_code}:{fold(leaf.name)}"
                repeats[base] += 1
                if repeats[base] > 1:
                    base = f"{base}#{repeats[base]}"
                if base not in units:
                    unit = units[base] = key("unit", base)
                    unit_rows.append(
                        {
                            "id": unit,
                            "polity_id": COSTA_RICA,
                            "scheme_code": "cr.distrito_electoral",
                            "code": base,
                            "area_relation": "within",
                            "area_id": area("cr.distrito", here_code),
                            "kind": "standard",
                            "name": title(leaf.name),
                        }
                    )
                unit = units[base]
                for column, value in leaf.values.items():
                    measure = measure_of(column)
                    if measure == 0 or (measure is None and column not in options):
                        continue
                    count = whole(value)
                    mine[(options[column] if measure is None else None, 1 if measure is None else measure)] += count
                    values.append(
                        {
                            "result_set_id": result_set,
                            "unit_id": unit,
                            "option_id": options[column] if measure is None else None,
                            "measure": 1 if measure is None else measure,
                            "value": count,
                        }
                    )
            miss: dict[str, tuple[int, int]] = {}
            declared_rows: list[dict[str, object]] = []
            for column, value in total.items():
                measure = measure_of(column)
                if measure == 0:
                    continue
                option_id = None
                if measure is None:
                    if column not in options:
                        continue
                    option_id, measure = options[column], 1
                count = round(value)
                declared_rows.append(
                    {"result_set_id": result_set, "option_id": option_id, "measure": measure, "value": count}
                )
                if mine[(option_id, measure)] != count:
                    miss[column] = (mine[(option_id, measure)], count)
            if miss:
                gaps[f"{family}:{seat_code or code}"] = miss
                p.add(
                    "note",
                    id=key("note", result_set, "printed"),
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The figures the Tribunal prints for the electoral districts of this table do not add up "
                    "to the totals it prints for the "
                    + ("canton" if family in (MAYOR, COUNCIL) else "administrative district")
                    + ". The figures of the electoral districts are stored as printed, and the printed totals are "
                    "not stored as declared.",
                    lang="en",
                )
            else:
                for row in declared_rows:
                    p.add("declared", **row)
            for (option_id, measure), count in mine.items():
                label = next((c for c, o in options.items() if o == option_id), "") if option_id else measure
                national_sums[family][label] += count
            if (family, seat_code) not in finer:
                continue
            finer_set = key("result_set", day, family, seat_code, "centro")
            p.add(
                "result_set",
                id=finer_set,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=TSE_SOURCE,
                status="active",
                is_default=True,
                supersedes_id=result_set,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", finer_set),
                entity_id=finer_set,
                kind="methodology",
                body_md=MUNICIPAL_METHOD + CENTRO_METHOD + (DISTRICT_NOTE if family == DISTRICT_COUNCIL else ""),
                lang="en",
            )
            by_column = {option: column for column, option in options.items()}
            tally_here: Counter[tuple[str | None, int]] = Counter()
            for center, leaf in finer.pop((family, seat_code)):
                assert leaf.canton is not None and leaf.district is not None
                if center not in centro_units:
                    unit = centro_units[center] = key("unit", center)
                    centro_rows.append(
                        {
                            "id": unit,
                            "polity_id": COSTA_RICA,
                            "scheme_code": "cr.centro",
                            "code": center,
                            "area_relation": "within",
                            "area_id": area("cr.distrito", code_of(leaf)),
                            "kind": "standard",
                            "name": title(leaf.name),
                        }
                    )
                for column, value in leaf.values.items():
                    measure = measure_of(column)
                    if measure == 0:
                        continue
                    if measure is None and column not in options:
                        if value:
                            raise ValueError(f"{day} {center}: {column} has {value} votes but is not on the ballot")
                        continue
                    count = whole(value)
                    option_id = options[column] if measure is None else None
                    tally_here[(option_id, 1 if measure is None else measure)] += count
                    values.append(
                        {
                            "result_set_id": finer_set,
                            "unit_id": centro_units[center],
                            "option_id": option_id,
                            "measure": 1 if measure is None else measure,
                            "value": count,
                        }
                    )
            for (option_id, measure), count in tally_here.items():
                label = by_column[option_id] if option_id else measure
                centro_sums[family][label] += count
            off = {
                (by_column[spot[0]] if spot[0] else str(spot[1])): (tally_here[spot], mine[spot])
                for spot in {*mine, *tally_here}
                if mine[spot] != tally_here[spot]
            }
            if off:
                differ[f"{family}:{seat_code}"] = off
            if any(
                tally_here[(str(row["option_id"]) if row["option_id"] else None, int(str(row["measure"])))]
                != row["value"]
                for row in declared_rows
            ):
                p.add(
                    "note",
                    id=key("note", finer_set, "printed"),
                    entity_id=finer_set,
                    kind="caveat",
                    body_md="The figures the Tribunal prints for the voting centers of this contest do not add up to "
                    "the totals its canton table prints for the "
                    + ("canton" if family in (MAYOR, COUNCIL) else "administrative district")
                    + ". The figures of the voting centers are stored as printed, and the printed totals are not "
                    "stored as declared.",
                    lang="en",
                )
            else:
                for row in declared_rows:
                    p.add("declared", **{**row, "result_set_id": finer_set})
    if finer:
        raise LookupError(f"{day}: voting centers of contests that have no table by canton: {sorted(finer)[:5]}")
    comparison["gaps"] = gaps
    comparison["national"] = {
        family: compare(m, chapter, national_sums[family]) for chapter, family in {**TABLES_OF, 5: INTENDANT}.items()
    }
    comparison["units"] = len(unit_rows)
    if centro_rows:
        if overlap := {str(row["id"]) for row in centro_rows} & {str(row["id"]) for row in unit_rows}:
            raise ValueError(f"{day}: voting centers and electoral districts share {len(overlap)} unit ids")
        comparison["centro"] = {
            "units": len(centro_rows),
            "differ_from_districts": differ,
            "national": {family: compare(m, chapter, centro_sums[family]) for chapter, family in TABLES_OF.items()},
        }
        unit_rows += centro_rows
    COMPARE[str(day)] = comparison
    p.frame("reporting_unit", pl.DataFrame(unit_rows, infer_schema_length=None).drop("name"))
    for row in unit_rows:
        p.name(str(row["id"]), str(row["name"]), "es", "official")
    frame = pl.DataFrame(values, infer_schema_length=None)
    p.frame(
        "result_value",
        frame.with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    over(p, frame)
    inconsistent(p, frame)
    return p
