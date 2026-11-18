import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import fastexcel

from ea import package, raw
from ea.package import Package

SOURCE = "me.dik"
SITE = "https://cik.me"
PAGE = f"{SITE}/dik-izbori"
MEDIA = f"{SITE}/images/DIK-media/izbori"
MONTENEGRO = str(uuid5(package.NAMESPACE, "polity\x1fME"))
REGISTERED, VOTES, CAST, VALID, INVALID, VOTERS = 2, 1, 3, 4, 5, 14
MEASURES = {"registered": REGISTERED, "voters": VOTERS, "cast": CAST, "invalid": INVALID, "valid": VALID}
ACCOUNTING = tuple(MEASURES)
CODE = r"\d+(?:\s*-?\s*[A-Za-z])?"

FILES = {
    "ref2006": f"{SITE}/images/DIK/Izbori_arhiva/Referendum_2006/Konacni-rezultati-po-glasackim-mjestima.pdf",
    "par2006": f"{MEDIA}/parlamentarni/2006/KONACNI-REZULTATI-2006.xlsx",
    "pres2008": f"{MEDIA}/predsjednicki/2008/20080410%20ukupni-rezultati-2008.pdf",
    "par2009": f"{MEDIA}/parlamentarni/2009/ukupni-rezultati-izbora-2009.xls",
    "par2012": f"{MEDIA}/parlamentarni/2012/konacni_za_objavu_medijima.xls",
    "pres2013": f"{MEDIA}/predsjednicki/2013/20130421_Konacni-rezultati-predsjednicki-izbori-2013.xlsx",
    "par2016": f"{MEDIA}/parlamentarni/2016/ukupni-za-objavu.xlsx",
    "pres2018-prov": f"{MEDIA}/predsjednicki/2018/20180417%20privremeni_rezultati.xls",
    "pres2018-final": f"{MEDIA}/predsjednicki/2018/20180428%20Konacni%20za%20objavu.pdf",
    "par2020": f"{MEDIA}/parlamentarni/2020/RezParlamentarni_2020_konacni-1.xlsx",
    "pres2023-final": f"{MEDIA}/predsjednicki/2023/konacni-rezultati-2023.pdf",
    "pres2023-r2-mun": f"{MEDIA}/predsjednicki/2023/preliminarni_rezultati_po_opstinama-1.pdf",
    "par2023": f"{MEDIA}/parlamentarni/2023/Konacni-rezultati-po-birackim-mjestima.pdf",
    "par2023-final": f"{MEDIA}/parlamentarni/2023/KONACNI-REZULTATI-2023.pdf",
    "par2023-prelim": f"{MEDIA}/parlamentarni/2023/Preliminarni_rezultati_po_svim_birackim_mjestima.pdf",
    "ref2006-mun": f"{SITE}/images/DIK/Izbori_arhiva/Referendum_2006/Rezultati-konacni-po-opstinama.pdf",
    "par2006-final": f"{MEDIA}/parlamentarni/2006/20.09.06.-KONA.pdf",
    "par2009-final": f"{MEDIA}/parlamentarni/2009/Konacni-rezultati-izbora-za-poslanike-u-Skupstinu-CG-2009.pdf",
    "par2016-final": f"{MEDIA}/parlamentarni/2016/Konacni-rezultati.pdf",
    "par2020-final": f"{MEDIA}/parlamentarni/2020/konacni_2020.pdf",
    "pres2008-final": f"{MEDIA}/predsjednicki/2008/20080410%20konacni-2008.pdf",
    "pres2013-final": f"{MEDIA}/predsjednicki/2013/20130421_KONACNI-rezultati.pdf",
    "lists2006": f"{MEDIA}/parlamentarni/2006/zbirna_izborna_lista_2006.pdf",
    "lists2009": f"{MEDIA}/parlamentarni/2009/ZBIRNA-IZBORNA-LISTA-2009.pdf",
    "lists2012": f"{MEDIA}/parlamentarni/2012/Zbirna-izborna-lista-2012.pdf",
    "lists2016": f"{MEDIA}/parlamentarni/2016/ZBIRNA-IZBORNA-LISTA.pdf",
    "lists2020": f"{MEDIA}/parlamentarni/2020/zbirna-izborna-lista-2020-2.pdf",
    "lists2023": f"{MEDIA}/parlamentarni/2023/Zbirna-izborna-lista.pdf",
}

CYRILLIC = dict(
    zip(
        "абвгдђежзијклљмнњопрстћуфхцчџш",
        ["a", "b", "v", "g", "d", "dj", "e", "z", "z", "i", "j", "k", "l", "lj", "m", "n", "nj", "o", "p", "r"]
        + ["s", "t", "c", "u", "f", "h", "c", "c", "dz", "s"],
        strict=True,
    )
)


def fold(text: str) -> str:
    text = "".join(CYRILLIC.get(c, c) for c in text.lower())
    plain = unicodedata.normalize("NFKD", text.replace("Đ", "Dj").replace("đ", "dj").replace("đ", "dj"))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.lower()).split())


NAMES = ["Andrijevica", "Bar", "Berane", "Bijelo Polje", "Budva", "Cetinje", "Danilovgrad", "Gusinje", "Herceg Novi"]
NAMES += ["Kolašin", "Kotor", "Mojkovac", "Nikšić", "Petnjica", "Plav", "Plužine", "Pljevlja", "Podgorica", "Rožaje"]
NAMES += ["Šavnik", "Tivat", "Tuzi", "Ulcinj", "Žabljak", "Zeta"]
CANON = {fold(name): name for name in NAMES}
SPACELESS = {key.replace(" ", ""): name for key, name in CANON.items()}  # some PDFs print `Bij elo Polj e`
PRISON = "Prisons"
PRISON_KEYS = {"ziks", "uiks", "zatvori", "izdvojena bm", "izdvojena biracka mjesta"}


def place_of(text: str) -> str | None:
    """The canonical municipality for a printed name, `Prisons` for the prison units, or None."""
    key = fold(text)
    if key not in CANON and key.replace(" ", "") in SPACELESS:
        return SPACELESS[key.replace(" ", "")]
    if key in PRISON_KEYS or key.startswith("ziks"):
        return PRISON
    return CANON.get(key)


def _not_a_page(path: Path) -> None:
    """The site sometimes answers a data request with a self-reloading challenge page; refuse to keep one."""
    if path.open("rb").read(200).lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise ValueError(f"{path.name} is a web page, not the data file")


def fetch() -> None:
    for url in FILES.values():
        for attempt in range(5):
            try:
                raw.fetch(SOURCE, url, suffix=url.rsplit(".", 1)[1], check=_not_a_page)
                break
            except ValueError:
                time.sleep(10 * (attempt + 1))
        else:
            raise ValueError(f"{url} keeps answering with a web page")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DIK_SOURCE = key("source", "dik")
PARLIAMENT = key("body", "parliament")
PRESIDENCY = key("office", "president")
SYSTEM_LIST = key("electoral_system", "parliament", "list")
SYSTEM_LIST_2011 = key("electoral_system", "parliament", "list", "2011")
# Law on the Election of Councilors and Members of Parliament, amended in Sl. list CG 46/11 (in force on 2011-09-24).
LIST_RULES_2011 = date(2011, 9, 24)
SYSTEM_PRESIDENT = key("electoral_system", "president", "two_round")
SYSTEM_REFERENDUM = key("electoral_system", "referendum")


@dataclass
class Row:
    place: str
    code: str
    name: str
    acc: dict[str, int | None]
    votes: list[int | None]
    kind: str = "standard"


@dataclass
class Table:
    labels: list[str]
    rows: list[Row]
    total: Row | None = None
    places: dict[str, Row] = field(default_factory=dict)
    level: str = "station"  # station, municipality, or national


def counts(values: list[int]) -> list[int | None]:
    return list(values)


def clean(text: object) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split())


def num(cell: object) -> int | None:
    """A workbook cell as a count; None when the cell is empty."""
    text = clean(cell)
    if text in ("", "None", "-"):
        return None
    return int(float(text))


def pnum(text: str) -> int:
    """A count printed with a thousands separator, as in `4,369`, `4.369`, or `341 581`."""
    return int(re.sub(r"[ ,.]", "", text))


def grid(name: str, sheet: str | None = None) -> list[list[str | None]]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, FILES[name]).path))
    frame = book.load_sheet_by_name(sheet or book.sheet_names[0], header_row=None, dtypes="string").to_polars()
    return [[None if cell is None else str(cell) for cell in row] for row in frame.rows()]


def sheet_names(name: str) -> list[str]:
    return fastexcel.read_excel(str(raw.cached(SOURCE, FILES[name]).path)).sheet_names


def pdf(name: str, layout: bool = False) -> list[str]:
    path = raw.cached(SOURCE, FILES[name]).path
    mode = "-layout" if layout else "-raw"
    out = subprocess.run(["pdftotext", mode, str(path), "-"], capture_output=True, text=True, check=True).stdout
    return [clean(line) for line in out.splitlines() if line.strip()]


def unquote(text: str) -> str:
    """Undo the CSV quoting a few workbooks carry into cell text."""
    text = clean(text)
    if len(text) > 1 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].replace('""', '"')
    return clean(text)


YUSCII = str.maketrans("[\\]^@{|}~`", "ŠĐĆČŽšđćčž")


def yuscii(text: str) -> str:
    """The 2006 workbook is typed in the old YUSCII code page, where [ is Š and ^ is Č."""
    return clean(text.translate(YUSCII))


def row_of(
    place: str, code: str, name: str, cells: list[str | None], cols: dict[str, int], first: int, end: int
) -> Row:
    acc = {k: num(cells[i]) for k, i in cols.items() if i < len(cells)}
    return Row(
        place,
        code,
        name,
        acc,
        [num(c) for c in cells[first:end]],
        "prison" if place == PRISON else "standard",
    )


def _finish(table: Table) -> Table:
    seen: set[tuple[str, str]] = set()
    for row in table.rows:
        if (row.place, row.code) in seen:
            raise ValueError(f"{row.place} {row.code} is listed twice")
        seen.add((row.place, row.code))
    return table


# Workbook layouts: index of each accounting figure, then the first and end index of the option columns.


def _place(text: str) -> str:
    place = place_of(text)
    if place is None:
        raise ValueError(f"{text!r} is not a known municipality")
    return place


def _sum_voters(row: Row, a: int, b: int, cells: list[str | None]) -> None:
    left, right = num(cells[a]), num(cells[b])
    row.acc["voters"] = None if left is None or right is None else left + right


def read_2006() -> Table:
    rows = grid("par2006")
    labels = [yuscii(cell) for cell in rows[0][10:] if cell]
    cols = {"registered": 2, "cast": 5, "invalid": 6, "valid": 7}
    end = 10 + len(labels)
    table = Table(labels, [])
    place = ""
    for cells in rows[1:]:
        if all(cell is None for cell in cells):
            continue
        head = yuscii(cells[0] or "")
        if cells[1] is None:
            place = _place(head)
            total = row_of(place, "", head, cells, cols, 10, end)
            _sum_voters(total, 8, 9, cells)
            table.places[place] = total
        else:
            row = row_of(place, head, yuscii(cells[1]), cells, cols, 10, end)
            _sum_voters(row, 8, 9, cells)
            table.rows.append(row)
    return _finish(table)


def read_2009() -> Table:
    name = sheet_names("par2009")[1]
    rows = grid("par2009", name)
    labels = [clean(r[1]) for r in rows[:16]]
    cols = {"registered": 2, "cast": 5, "invalid": 7, "valid": 8, "voters": 12}
    table = Table(labels, [])
    place = ""
    for cells in rows[16:]:
        head = clean(cells[0])
        if not head and all(cell is None for cell in cells):
            continue
        if clean(cells[2]) == "UPISANO":
            place = _place(head)
        elif head == "UKUPNO":
            table.places[place] = row_of(place, "", head, cells, cols, 13, 29)
        elif head == "ZIKS":
            table.rows.append(row_of(PRISON, "ZIKS", "ZIKS", cells, cols, 13, 29))
        elif cells[1] is not None:
            table.rows.append(row_of(place, head, clean(cells[1]), cells, cols, 13, 29))
    return _finish(table)


def _dotted_labels(rows: list[list[str | None]], column: int, vote_column: int) -> list[str]:
    labels: list[str] = []
    for cells in rows:
        found = re.match(r"\s*(\d+)\.\s*(.+?)\s*[.…]{3,}", cells[column] or "")
        if found and cells[vote_column] is not None:
            labels.append(clean(found.group(2)))
    return labels


def read_2012() -> Table:
    names = sheet_names("par2012")
    detail = grid("par2012", names[1])
    labels = _dotted_labels(detail, 0, 9)
    cols = {"registered": 2, "voters": 5, "cast": 9, "invalid": 10, "valid": 11}
    table = Table(labels, [])
    place = ""
    for cells in detail:
        head = clean(cells[0])
        if head == "Broj":
            place = _place(cells[1] or "")
        elif head.startswith("UKUPNO"):
            table.places[place] = row_of(place, "", head, cells, cols, 12, 25)
        elif re.fullmatch(CODE, head) and cells[1] is not None and cells[2] is not None:
            table.rows.append(row_of(place, head, clean(cells[1]), cells, cols, 12, 25))
    cols = {"registered": 1, "voters": 4, "cast": 8, "invalid": 9, "valid": 10}
    for cells in grid("par2012", names[0])[1:]:
        if cells[0] is None or cells[1] is None:
            continue
        if clean(cells[0]) == "Crna Gora":
            table.total = row_of("", "", "Crna Gora", cells, cols, 11, 24)
    return _finish(table)


def read_2013() -> Table:
    names = sheet_names("pres2013")
    detail = grid("pres2013", names[1])
    labels = [clean(c) for c in detail[0][13:15]]
    cols = {"registered": 2, "cast": 5, "invalid": 7, "valid": 8, "voters": 12}
    table = Table(labels, [])
    place = ""
    for cells in detail:
        head = clean(cells[0])
        if head == "Broj":
            place = _place(cells[1] or "")
        elif head.startswith("UKUPNO"):
            table.places[place] = row_of(place, "", head, cells, cols, 13, 15)
        elif re.fullmatch(CODE, head) and cells[1] is not None:
            table.rows.append(row_of(place, head, clean(cells[1]), cells, cols, 13, 15))
    cols = {"registered": 1, "cast": 4, "invalid": 6, "valid": 7, "voters": 11}
    for cells in grid("pres2013", names[0]):
        if clean(cells[0]).startswith("Crna Gora"):
            table.total = row_of("", "", "Crna Gora", cells, cols, 12, 14)
    return _finish(table)


def read_2016() -> Table:
    names = sheet_names("par2016")
    detail = grid("par2016", names[0])
    labels: list[str] = []
    for cells in detail:
        found = re.match(r"\s*(\d+)\.\s*(.+?)\s*-\s*[\d.]+\s+glas", cells[0] or "")
        if found:
            labels.append(clean(found.group(2)))
    cols = {"registered": 1, "voters": 4, "cast": 7, "invalid": 8, "valid": 9}
    end = 10 + len(labels)
    table = Table(labels, [])
    place = ""
    for cells in detail[14:]:
        head = clean(cells[0])
        if not head:
            continue
        if re.match(r"IV\b|[-–]|a da su|Pojedine", head):
            break
        if cells[1] is None and place_of(head) is not None:
            place = _place(head)
        elif head.endswith(" ukupno"):
            continue
        else:
            found = re.match(r"'?\s*(\d+(?:\s*-\s*[A-Za-z])?)\s+(.*?)\s*'?$", head)
            if found and cells[1] is not None:
                name = found.group(2).replace("\u2500\u00ee", "Č")
                table.rows.append(row_of(place, found.group(1).replace(" ", ""), clean(name), cells, cols, 10, end))
    for cells in grid("par2016", names[1]):
        head = clean(cells[0])
        if head.endswith(" ukupno"):
            place = head.removesuffix(" ukupno")
            if place == "Crna Gora":
                table.total = row_of("", "", head, cells, cols, 10, end)
            else:
                table.places[_place(place)] = row_of(_place(place), "", head, cells, cols, 10, end)
    return _finish(table)


def read_2020() -> Table:
    names = sheet_names("par2020")
    detail = grid("par2020", names[1])
    heads = next(cells for cells in detail if clean(cells[1]) == "Primljeno")
    labels = [re.sub(r"^\d+\.\s*", "", clean(cell)) for cell in heads[12:] if cell and re.match(r"\d+\.", clean(cell))]
    cols = {"registered": 8, "voters": 11, "cast": 3, "invalid": 7, "valid": 6}
    end = 12 + len(labels)
    table = Table(labels, [])
    place = ""
    for cells in detail:
        head = unquote(cells[0] or "")
        if clean(cells[1]) == "Primljeno":
            place = _place(head)
        elif head.startswith("Sum:"):
            table.places[place] = row_of(place, "", head, cells, cols, 12, end)
        elif head.startswith("(") and cells[1] is not None:
            found = re.match(r"\(\s*([^)]+?)\s*\)\s*(.*)$", head)
            if found is None:
                raise ValueError(head)
            table.rows.append(row_of(place, found.group(1), clean(found.group(2)), cells, cols, 12, end))
    cols = {"registered": 11, "voters": 14, "cast": 6, "invalid": 10, "valid": 9}
    for cells in grid("par2020", names[0]):
        if clean(cells[0]) == "Crna Gora":
            table.total = row_of("", "", "Crna Gora", cells, cols, 15, 15 + len(labels))
    return _finish(table)


NUM = r"\d{1,3}(?:[. ]\d{3})+|\d+"


HEADER_WORDS = {"NEUPOTRIJEBLJE", "NO", "UPOTRIJEBLJENO", "KONTROLNIH", "KUPONA", "GLASAČKOM", "MJESTU", "PISMOM"}


def _tail(line: str, count: int) -> tuple[list[str], list[int]] | None:
    """Split a table line into its leading text tokens and its last `count` counts, or None."""
    tokens = line.replace("%", "% ").split()
    tokens = [t for t in tokens if not t.endswith("%") and t not in HEADER_WORDS]
    if len(tokens) <= count or not all(re.fullmatch(r"[\d,]+", t) for t in tokens[-count:]):
        return None
    return tokens[:-count], [pnum(t) for t in tokens[-count:]]


def _pdf_row(place: str, tokens: list[str], values: list[int], cols: dict[str, int], first: int) -> Row:
    acc: dict[str, int | None] = {k: values[i] for k, i in cols.items()}
    return Row(
        place,
        tokens[0],
        " ".join(tokens[1:]),
        acc,
        counts(values[first:]),
        "prison" if place == PRISON else "standard",
    )


ORDER = [
    *("Andrijevica", "Bar", "Berane", "Bijelo Polje", "Budva", "Danilovgrad", "Žabljak", "Kolašin", "Kotor"),
    *("Mojkovac", "Nikšić", "Plav", "Plužine", "Pljevlja", "Podgorica", "Rožaje", "Tivat", "Ulcinj"),
    *("Herceg Novi", "Cetinje", "Šavnik", PRISON),
]  # the order the referendum tables list them in, which the station rows rely on to find each header


def read_ref2006() -> Table:
    cols = {"registered": 0, "cast": 3, "invalid": 5, "valid": 6}
    table = Table(["DA", "NE"], [], level="station")
    order = ORDER
    headers = 0
    place = ""
    pending: Row | None = None
    for line in pdf("ref2006", layout=True):
        found = _tail(line, 11)
        if found is None:
            # A station's letter suffix and the rest of a long name wrap onto the next line.
            if pending is not None and "-" in pending.code and not any(c.isdigit() for c in line.replace("(", "")):
                letter, _, rest = line.partition(" ")
                if pending.code.endswith("-") and len(letter) == 1 and letter.isalpha():
                    pending.code += letter
                    line = rest
                pending.name = clean(f"{pending.name} {line}")
            continue
        tokens, values = found
        glued = re.match(r"(\d+)(-?)(\D.*)?$", tokens[0]) if tokens else None
        if glued is None:
            continue
        tokens = [glued.group(1) + glued.group(2), *([glued.group(3)] if glued.group(3) else []), *tokens[1:]]
        name = " ".join(tokens[1:])
        if headers < len(order) and name == name.upper() and place_of(name) == order[headers]:
            place = _place(name)
            headers += 1
            pending = None
            continue
        row = _pdf_row(place, tokens, values, cols, 7)
        row.votes = counts(values[7:9])
        row.acc["voters"] = values[9] + values[10]
        table.rows.append(row)
        pending = row
    for line in pdf("ref2006-mun", layout=True):
        found = _tail(line, 11)
        if found is None:
            continue
        tokens, values = found
        name = re.sub(r"^\d+\s+", "", " ".join(tokens))
        total = _pdf_row("", ["", ""], values, cols, 7)
        total.votes = counts(values[7:9])
        total.acc["voters"] = values[9] + values[10]
        if name == "CRNA GORA":
            table.total = total
        elif place_of(name) is not None:
            total.place = _place(name)
            table.places[total.place] = total
    return _finish(table)


def read_2008() -> Table:
    cols = {"registered": 0, "cast": 3, "invalid": 7, "valid": 8}
    labels = ["Nebojša Medojević", "Andrija Mandić", "Srđan Milić", "Filip Vujanović"]
    table = Table(labels, [])
    place = ""
    for line in pdf("pres2008", layout=True):
        wide = _tail(line, 13)
        if wide is not None:
            tokens, values = wide
            name = " ".join(tokens)
            if tokens and not re.fullmatch(CODE, tokens[0]) and place_of(name) is not None:
                place = _place(name)
                continue
            if tokens and re.fullmatch(CODE, tokens[0]):
                if any(r.place == place and r.code == tokens[0] for r in table.rows) and place in ORDER:
                    place = ORDER[ORDER.index(place) + 1]  # the printed table lacks Podgorica's header line
                row = _pdf_row(place, tokens, values, cols, 9)
                row.acc["voters"] = values[5] + values[6]
                table.rows.append(row)
            continue
        narrow = _tail(line, 12)
        if narrow is not None and line.startswith("ZATVORI"):
            # The prisons' combined row prints no registered figure, and its columns run invalid, valid, outside, inside.
            _, v = narrow
            row = Row(PRISON, "ZATVORI", "ZATVORI (zbirno 3 biračka mjesta)", {}, list(v[8:]), "prison")
            row.acc = {"registered": None, "cast": v[2], "invalid": v[4], "valid": v[5], "voters": v[6] + v[7]}
            table.rows.append(row)
    return _finish(table)


def read_2018() -> Table:
    names = sheet_names("pres2018-prov")
    head = grid("pres2018-prov", names[0])[0]
    labels = [re.sub(r"^\d+\.\s*", "", clean(cell)) for cell in head[16:] if cell]
    cols = {"registered": 11, "voters": 15, "cast": 6, "invalid": 10, "valid": 9}
    end = 16 + len(labels)
    table = Table(labels, [])
    for name in names[1:]:
        place = _place(name.replace("_", " "))
        for cells in grid("pres2018-prov", name)[1:]:
            text = clean(cells[0])
            found = re.match(r"^(\d+(?:-[A-Za-z])?|[IVX]+)\.\s*(.*)$", text)
            if found:
                table.rows.append(row_of(place, found.group(1), clean(found.group(2)), cells, cols, 16, end))
            elif text:
                table.places[place] = row_of(place, "", text, cells, cols, 16, end)
    for cells in grid("pres2018-prov", names[0])[1:]:
        text = clean(cells[0])
        if text == "Crna Gora":
            table.total = row_of("", "", text, cells, cols, 16, end)
    return _finish(table)


@dataclass
class Decision:
    """The national figures a commission's decision prints: accounting, each option's votes, and seats."""

    acc: dict[str, int]
    labels: list[str]
    votes: list[int]
    seats: list[tuple[str, int]]


ACC_PATTERNS = {
    "registered": [
        rf"(?:upisano(?: je)?|biračko pravo imalo je|spisak ukupno upisano)\s+({NUM})\s+(?:birača|građan)",
        rf"upisano\s+({NUM})",
    ],
    "voters": [rf"[Gg]lasalo ukupno\s+({NUM})", rf"[Uu]kupno (?:je )?glasalo\s+({NUM})"],
    "cast": [rf"(?<!ne)upotrijebljeno(?: je)?\s+({NUM})", rf"({NUM})\s+(?<!ne)upotrijebljenih"],
    "invalid": [rf"({NUM})\s+nevažeć", rf"nevažećih\s+({NUM})"],
    "valid": [rf"({NUM})\s*(?<!ne)važeć", rf"(?<!ne)važećih\s+({NUM})"],
}
WORDS = {
    "jedan": 1,
    "jedna": 1,
    "dva": 2,
    "tri": 3,
    "četiri": 4,
    "pet": 5,
    "šest": 6,
    "sedam": 7,
    "osam": 8,
    "devet": 9,
    "deset": 10,
}


def decision(name: str, start: str | None = None, stop: str | None = None) -> Decision:
    text = " ".join(pdf(name))
    if start is not None:
        text = text[text.index(start) :]
    if stop is not None:
        text = text[: text.index(stop)]
    acc: dict[str, int] = {}
    for measure, patterns in ACC_PATTERNS.items():
        for pattern in patterns:
            found = re.search(pattern, text)
            if found:
                acc[measure] = pnum(found.group(1))
                break
    labels: list[str] = []
    votes: list[int] = []
    wanted = 1
    for found in re.finditer(rf"(?<![\d.])(\d{{1,2}})\.\s+(.+?)[\s.…]*?[\s.]({NUM})\s+glas", text):
        if int(found.group(1)) == wanted and not re.search(r"\d\s*\(", found.group(2)):
            labels.append(clean(re.sub(r"\.{2,}.*$", "", found.group(2))))
            votes.append(pnum(found.group(3)))
            wanted += 1
    seats = [
        (clean(found.group(1)), int(found.group(2)))
        for found in re.finditer(r"(?<!\d)\d{1,2}\.\s+(.+?)[\s.…]*?(\d+)\s*(?:\([^)]*\)\s*)?mandat", text)
    ]
    return Decision(acc, labels, votes, seats)


PRELIM_TOKEN = re.compile(r"^(?:\d+|\d+(?:\.\d+)?%)$")
PRELIM_HEADER = {"Ukupno", "upisano", "glasalo", "Glasali", "na", "biračkom", "mjestu", "putem", "pisma", "Izborna"}
PRELIM_HEADER |= {"Upotrijebljeno", "Neupotrijebljeno", "Broj", "odrezaka/potvrda", "odvojenih", "kupona", "vizuelnih"}
PRELIM_HEADER |= {"identifikacija", "Važeći", "Nevažeći", "lista"}  # the rotated column headings, printed once
PRELIM_FIGURES = 26  # eleven accounting figures and fifteen lists, each followed by its share
DISAGREE: list[tuple[str, str, int, int]] = []  # place, station, preliminary valid, final list votes


@dataclass
class Prelim:
    place: str
    name: str
    acc: dict[str, int]
    votes: list[int]


def read_2023_prelim() -> list[Prelim]:
    """The preliminary per-station accounting: each station prints its name, then a figure and a share per column."""
    lines = pdf("par2023-prelim")
    start = lines.index("BIRAČKO MJESTO")
    records: list[Prelim] = []
    place: str | None = None
    name: list[str] = []
    tokens: list[str] = []
    carry: list[str] = []  # the figures of the next station, printed before its name

    def flush() -> None:
        nonlocal name, tokens, carry
        if not tokens:
            return
        if len(tokens) == 3 * PRELIM_FIGURES and all(t.isdigit() for t in tokens[2 * PRELIM_FIGURES :]):
            tokens, carry = tokens[: 2 * PRELIM_FIGURES], tokens[2 * PRELIM_FIGURES :]
        if place is None or not name or len(tokens) != 2 * PRELIM_FIGURES:
            raise ValueError(f"unreadable preliminary station {name!r} in {place!r}: {len(tokens)} tokens")
        half = PRELIM_FIGURES
        if all(t.isdigit() for t in tokens[:half]) and all(t.endswith("%") for t in tokens[half:]):
            figures = [int(token) for token in tokens[:half]]  # figures first, then every share
        elif all(t.isdigit() for t in tokens[::2]) and all(t.endswith("%") for t in tokens[1::2]):
            figures = [int(token) for token in tokens[::2]]
        else:
            raise ValueError(f"misaligned preliminary station {name!r} in {place!r}: {tokens}")
        acc = {"registered": figures[0], "voters": figures[1], "valid": figures[9], "invalid": figures[10]}
        records.append(Prelim(place, "".join(name) if len(name) == 1 else " ".join(name), acc, figures[11:]))
        name, tokens = [], []

    skip_number = False
    body = lines[start + 1 :]
    for at, line in enumerate(body):
        if line.startswith(("Ukupno:", "Izborna lista")):
            break
        if skip_number:  # the list number printed after `lista` in the column heading
            skip_number = False
            if PRELIM_TOKEN.match(line):
                continue
        if line in PRELIM_HEADER:
            skip_number = line == "lista"
            continue
        parts = line.split()
        if all(PRELIM_TOKEN.match(part) for part in parts):  # a line may hold several figures
            tokens.extend(parts)
            continue
        flush()
        found = place_of(line)
        if found is not None and (line != line.upper() or found == PRISON):
            place = found
            name = []
            continue
        following = body[at + 1].split() if at + 1 < len(body) else []
        if "100%" in parts[1:] and carry and not parts[parts.index("100%") - 1].isdigit():
            at100 = parts.index("100%")  # `NAME 100% 57.33% ...`, the figures having come first
            name.append(" ".join(parts[:at100]))
            tokens, carry = carry + parts[at100:], []
            continue
        for index, part in enumerate(parts):  # the registered figure can share a line with the name
            after = parts[index + 1] if index + 1 < len(parts) else (following[0] if following else "")
            if index and part.isdigit() and after == "100%":
                name.append(" ".join(parts[:index]))
                tokens.append(part)
                tokens.extend(parts[index + 1 :])
                break
        else:
            name.append(line)
    flush()
    return records


def _glued(text: str) -> str:
    """A station name with spacing ignored, because the PDF splits some names across lines mid-word."""
    return fold(text).replace(" ", "")


def attach_prelim(table: Table) -> None:
    """Give each final station its preliminary registered, voters, valid, and invalid, matched by name within the
    municipality, in listed order when a name repeats. A station whose final list votes do not add up to its preliminary
    valid ballots is noted in `DISAGREE`."""
    pool: dict[tuple[str, str], list[Prelim]] = {}
    for record in read_2023_prelim():
        pool.setdefault((record.place, _glued(record.name)), []).append(record)
    DISAGREE.clear()
    for row in table.rows:
        queue = pool.get((row.place, _glued(row.name)))
        if not queue:
            raise ValueError(f"no preliminary figures for {row.place}: {row.name}")
        record = queue.pop(0)
        if not any(record.acc.values()) and any(row.votes):
            continue  # the preliminary table prints zeros for a station it had not yet received
        row.acc = dict(record.acc)
        total = sum(v or 0 for v in row.votes)
        if record.acc["valid"] != total:
            DISAGREE.append((row.place, row.name, record.acc["valid"], total))
    left = [record for queue in pool.values() for record in queue]
    if left:
        raise ValueError(f"{len(left)} preliminary stations match no final station, such as {left[0]}")


def read_2023() -> Table:
    """The final per-station list votes. The table gives no station numbers, so each station is numbered in table order."""
    final = decision("par2023-final")
    names = sorted([*NAMES, "ZIKS", "UIKS"], key=len, reverse=True)
    skip = ("KONAČNI REZULTATI PO", "PARLAMENTARNI IZBORI -")
    lines = [line for line in pdf("par2023") if not line.startswith(skip)]
    start = next(i for i, line in enumerate(lines) if line.startswith("Andrijevica ANDRIJEVICA - PEOVAC"))
    records: list[list[str]] = []
    for line in lines[start:]:
        if line.startswith(("Ukupno:", "UKUPNO CG:")):
            records.append(["", line])
            continue
        place = next((n for n in names if line.upper().startswith(n.upper() + " ") or line.upper() == n.upper()), None)
        if place is not None:
            records.append([place, line])
        elif records:
            records[-1][1] += " " + line
    table = Table(final.labels, [])
    for place, line in records:
        if not place:
            continue
        found = re.match(r"^(.+?)\s+((?:\d+\s+){14}\d+)$", line)
        if found is None:
            raise ValueError(f"unreadable station line: {line}")
        name = found.group(1)[len(place) :].strip()
        votes = [int(v) for v in found.group(2).split()]
        where = _place(place)
        code = str(1 + sum(1 for r in table.rows if r.place == where))
        table.rows.append(Row(where, code, name, {}, list(votes), "prison" if where == PRISON else "standard"))
    attach_prelim(table)
    return _finish(table)


def read_2023_runoff() -> Table:
    """The preliminary second-round count by municipality; each place prints ten figures, each followed by a share."""
    lines = pdf("pres2023-r2-mun")
    labels = [label for label in decision("pres2023-final", "VII Državna").labels]
    table = Table(labels, [], level="municipality")
    cols = {"registered": 0, "voters": 1, "cast": 4, "valid": 6, "invalid": 7}
    i = 0
    while i < len(lines) and lines[i] != "BIRAČKO MJESTO":
        i += 1
    i += 1
    while i < len(lines):
        parts: list[str] = []
        while i < len(lines) and not re.fullmatch(r"[\d.]+%?", lines[i]):
            parts.append(lines[i])
            i += 1
        name = next(
            (
                text
                for k in range(len(parts) - 1, -1, -1)
                if (text := "".join(parts[k:])) == "Ukupno:" or place_of(text) is not None
            ),
            "",
        )
        figures = []
        while i < len(lines) and re.fullmatch(r"[\d.]+%?", lines[i]):
            figures.append(lines[i])
            i += 1
        values = [pnum(f) for f in figures[0::2]]
        if len(values) < 10:
            continue
        row = Row(
            "",
            "",
            name,
            {k: values[c] for k, c in cols.items()},
            counts(values[8:10]),
            "standard",
        )
        if name == "Ukupno:":
            table.total = row
        else:
            row.place = _place(name)
            row.name = row.place
            table.rows.append(row)
    return table


def _tokens(text: str) -> set[str]:
    return {word for word in fold(text).split() if len(word) > 2}


def match_labels(labels: list[str], entries: list[tuple[str, int]]) -> dict[int, int]:
    """Assign each (printed label, figure) entry to the ballot line whose label it best matches, one each."""
    found: dict[int, int] = {}
    for text, figure in entries:
        scored = sorted(
            (
                (len(_tokens(text) & _tokens(label)) / max(1, len(_tokens(text) | _tokens(label))), index)
                for index, label in enumerate(labels)
                if index not in found
            ),
            reverse=True,
        )
        if not scored or scored[0][0] < 0.25:
            raise ValueError(f"no ballot line matches {text!r}")
        found[scored[0][1]] = figure
    return found


def seats_in(text: str) -> list[tuple[str, int]]:
    """Each list's seats as a decision prints them, `<label> .... 41 (četrdeset jedan) mandata`."""
    start = re.search(r"broj\s+mandata|sljedeći broj mandata|sledeći broj mandata", text)
    text = text[start.end() :] if start else text
    text = text.replace("i to:", "i to: - ")
    entries = []
    for found in re.finditer(r"(?:(?<=\s)|^)(?:\d{1,2}\.|[-–])\s+(.+?)[\s.…]*?(\d+)\s*(?:\([^)]*\)\s*)?mandat", text):
        label = clean(found.group(1))
        if not label.startswith("Zbirna izborna lista"):
            entries.append((label, int(found.group(2))))
    return entries


def votes_in_2009() -> list[tuple[str, int]]:
    """The 2009 decision prints each list's votes without the word `glasova`, ordered by votes."""
    text = " ".join(pdf("par2009-final"))
    text = text[text.index("glasova, i to:") : text.index("V Primjenom")]
    return [
        (clean(found.group(2)), pnum(found.group(3)))
        for found in re.finditer(rf"(?<![\d.])(\d{{1,2}})\.\s+(.+?)\s+({NUM})(?=\s+\d{{1,2}}\.\s|\s*$)", text)
    ]


READERS = {
    "ref2006": read_ref2006,
    "par2006": read_2006,
    "pres2008": read_2008,
    "par2009": read_2009,
    "par2012": read_2012,
    "pres2013": read_2013,
    "par2016": read_2016,
    "pres2018": read_2018,
    "par2020": read_2020,
    "par2023": read_2023,
    "pres2023-r2": read_2023_runoff,
}
_TABLES: dict[str, Table] = {}


def table(name: str) -> Table:
    if name not in _TABLES:
        _TABLES[name] = READERS[name]()
    return _TABLES[name]


@dataclass
class Official:
    """The national figures a commission declared for a result set."""

    acc: dict[str, int | None]
    votes: list[int] | None


def national(name: str) -> Official:
    """The declared national figures, from the workbook's printed total or the commission's decision."""
    t = table(name) if name in READERS else None
    if name in ("par2006", "pres2008", "par2023"):
        d = decision({"par2006": "par2006-final", "pres2008": "pres2008-final", "par2023": "par2023-final"}[name])
        return Official(dict(d.acc), d.votes)
    if name == "par2009":
        d = decision("par2009-final")
        assert t is not None
        found = match_labels(t.labels, votes_in_2009())
        return Official(dict(d.acc), [found[i] for i in range(len(t.labels))])
    if name == "pres2013":
        d = decision("pres2013-final")
        assert t is not None and t.total is not None
        if any(t.total.acc[k] != v for k, v in d.acc.items()) or d.votes != [v or 0 for v in t.total.votes]:
            raise ValueError("the 2013 workbook's national row disagrees with the decision")
    if t is None or t.total is None:
        raise ValueError(f"{name} prints no national total")
    return Official(dict(t.total.acc), [v or 0 for v in t.total.votes])


def workbook_text(name: str, sheet: int) -> str:
    rows = grid(name, sheet_names(name)[sheet])
    return " ".join(clean(cell) for row in rows for cell in row if cell is not None)


def seats_for(name: str) -> dict[int, int]:
    """Each list's declared seats by zero-based ballot position."""
    t = table(name)
    source = {
        "par2006": lambda: " ".join(pdf("par2006-final")),
        "par2009": lambda: " ".join(pdf("par2009-final")),
        "par2012": lambda: workbook_text("par2012", 1),
        "par2016": lambda: workbook_text("par2016", 0),
        "par2020": lambda: " ".join(pdf("par2020-final")),
        "par2023": lambda: " ".join(pdf("par2023-final")),
    }[name]()
    found = match_labels(t.labels, seats_in(source))
    if sum(found.values()) != 81:
        raise ValueError(f"{name}: declared seats add up to {sum(found.values())}")
    return found


ITEM = re.compile(r"^(\d+)\.\s+(\S.*)$")
HEADING = re.compile(r"^(\d+)(?:\.\s*|\s+)(\S.*)$")
CELL = re.compile(r"(?:(?<=\s{2})|^)\d+\.(?=\s*\S)")
TITLE_LINES = 9  # the longest coalition names wrap onto this many lines


@dataclass
class Slate:
    """One list on the consolidated list: its printed title and its candidates in list order."""

    title: str
    names: dict[int, str] = field(default_factory=dict)


def _cells(text: str) -> list[str]:
    """Split a layout line into its numbered cells, so `1. A   17. B` gives two; leading text is kept as a cell."""
    cuts = [m.start() for m in CELL.finditer(text)]
    if not cuts:
        return [text]
    cells = [text[a:b].strip() for a, b in zip(cuts, [*cuts[1:], len(text)], strict=True)]
    if cuts[0] > 0:
        cells.insert(0, text[: cuts[0]].strip())
    return [re.sub(r"^(\d+)\.\s*", r"\1. ", cell) for cell in cells]


def pdf_lines(name: str) -> list[str]:
    path = raw.cached(SOURCE, FILES[name]).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return out.replace("\f", "\n").splitlines()


def read_slates(name: str) -> list[Slate]:
    """The candidates of every list on the commission's consolidated list, in the order the lists are printed.

    A list starts at a line holding only its number and title, where the next numbered line is candidate 1. The
    candidates sit in two columns, and a name that wraps leaves its tail in front of the next row's right column."""
    rows = [
        _cells(line)
        for line in (raw_line.strip() for raw_line in pdf_lines(name))
        if line and not re.fullmatch(r"\d+", line)
    ]
    slates: list[Slate] = []
    last = 0
    index = 0
    while index < len(rows):
        row = rows[index]
        found = HEADING.match(row[0]) if len(row) == 1 else None
        if found and int(found.group(1)) == len(slates) + 1:
            title = [found.group(2)]
            after = index + 1
            while after < len(rows) and not ITEM.match(rows[after][0]) and after < index + TITLE_LINES:
                title.append(rows[after][0])
                after += 1
            first = ITEM.match(rows[after][0]) if after < len(rows) else None
            if first is not None and first.group(1) == "1":
                slates.append(Slate(clean(" ".join(title))))
                index, last = after, 0
                continue
        if slates:
            for position, cell in enumerate(row):
                entry = ITEM.match(cell)
                if entry is None:
                    if position == 0 and len(row) > 1 and last:
                        slates[-1].names[last] = clean(f"{slates[-1].names[last]} {cell}")
                    continue
                number = int(entry.group(1))
                if number in slates[-1].names:
                    raise ValueError(f"{name}: candidate {number} appears twice on list {len(slates)}")
                slates[-1].names[number] = entry.group(2)
                if position == 0:
                    last = number
        index += 1
    for number, slate in enumerate(slates, 1):
        if sorted(slate.names) != list(range(1, len(slate.names) + 1)):
            raise ValueError(f"{name}: list {number} has gaps in its numbering")
    return slates


def _score(text: str, label: str) -> float:
    left, right = _tokens(text), _tokens(label)
    return len(left & right) / max(1, len(left | right))


def slates_for(name: str, labels: list[str]) -> list[Slate]:
    """The consolidated list's lists, checked against the ballot lines of the results in the same order."""
    slates = read_slates(name.replace("par", "lists"))
    if len(slates) != len(labels):
        raise ValueError(f"{name}: {len(slates)} lists on the consolidated list against {len(labels)} ballot lines")
    for position, slate in enumerate(slates):
        best = max(range(len(labels)), key=lambda i: _score(slate.title, labels[i]))
        if best != position:
            raise ValueError(f"{name}: list {position + 1} {slate.title!r} is closest to {labels[best]!r}")
    return slates


DAYS = {
    "2006-05-21": date(2006, 5, 21),
    "2006-09-10": date(2006, 9, 10),
    "2008-04-06": date(2008, 4, 6),
    "2009-03-29": date(2009, 3, 29),
    "2012-10-14": date(2012, 10, 14),
    "2013-04-07": date(2013, 4, 7),
    "2016-10-16": date(2016, 10, 16),
    "2018-04-15": date(2018, 4, 15),
    "2020-08-30": date(2020, 8, 30),
    "2023-03-19": date(2023, 3, 19),
    "2023-04-02": date(2023, 4, 2),
    "2023-06-11": date(2023, 6, 11),
}
PARLIAMENTARY = {
    "2006-09-10": "par2006",
    "2009-03-29": "par2009",
    "2012-10-14": "par2012",
    "2016-10-16": "par2016",
    "2020-08-30": "par2020",
    "2023-06-11": "par2023",
}
PRESIDENTIAL = {"2008-04-06": "pres2008", "2013-04-07": "pres2013", "2018-04-15": "pres2018"}
COUNTRY = "ME"
TITLES = re.compile(r"^(?:mr|dr|docent|prof)\b\.?\s*")

METHOD = (
    "Counted at each polling station by its electoral board and reported through the municipal electoral commissions "
    "to the State Election Commission, which published the figures in a workbook or table of its own. Registered "
    "voters are the voters on the roll. Voters are the voters who took part, at the polling station or by letter, "
    "ballots cast are the ballots used, and valid and invalid are the valid and invalid ballots. Polling stations "
    "inside the prisons are listed as units of their own."
)
METHOD_2023 = (
    "The list votes are the final count at each polling station, from the State Election Commission's final table "
    "by polling station. Registered voters, voters, and valid and invalid ballots are the commission's preliminary "
    "table by polling station, matched to the final stations by name within each municipality, so they are the "
    "preliminary counts and can differ slightly from the final ones. Registered voters are the voters on the roll, "
    "voters are the voters who took part, at the polling station or by letter, and valid and invalid are the valid "
    'and invalid ballots. One station, JU OŠ "Jedinstvo" Skorać in Tuzi, prints zeros in the preliminary table '
    "because it had not been received yet, so it carries list votes only. Polling stations inside the prisons are "
    "listed as units of their own."
)
NATIONAL_METHOD = (
    "The national figures the State Election Commission declared in its decision on the results. Registered voters "
    "are the voters on the roll, voters are the voters who took part, ballots cast are the ballots used, and valid "
    "and invalid are the valid and invalid ballots."
)


def person_key(label: str) -> str:
    text = fold(label)
    while (shorter := TITLES.sub("", text)) != text:
        text = shorter.strip()
    return text


HONORIFICS = r"^(?:mr|dr|doc|docent|prof|мр|др|доц|проф)(?=[\s.])\.?\s*"


def person_name(label: str) -> str:
    text = clean(label)
    while (shorter := re.sub(HONORIFICS, "", text, flags=re.IGNORECASE)) != text:
        text = shorter.strip()
    return text.title() if text.isupper() else text


def party_id(label: str) -> str:
    return key("party", fold(label))


def list_labels() -> dict[str, str]:
    found: dict[str, str] = {}
    for name in PARLIAMENTARY.values():
        for label in table(name).labels:
            found.setdefault(party_id(label), clean(label))
    return found


def candidate_labels() -> dict[str, str]:
    found: dict[str, str] = {}
    for name in (*PRESIDENTIAL.values(), "pres2023-r2"):
        for label in table(name).labels:
            found.setdefault(key("person", person_key(label)), person_name(label))
    for label in decision("pres2023-final", None, "IV Na izborima koji").labels:
        found.setdefault(key("person", person_key(label)), person_name(label))
    return found


def packages() -> list[str]:
    return ["me.dik/registry", *(f"me.dik/{day}" for day in DAYS)]


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        "me.dik/registry",
        SOURCE,
        "Montenegrin parties, candidates, the Parliament, the presidency, electoral systems, and unit schemes, from the "
        "State Election Commission's result publications",
    )
    p.external.add(MONTENEGRO)
    p.add(
        "source",
        id=DIK_SOURCE,
        kind="official_body",
        publisher="Državna izborna komisija Crne Gore",
        title="Election results published by the State Election Commission of Montenegro",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Državna izborna komisija Crne Gore",
    )
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), DIK_SOURCE)
    p.add("scheme", code="me.country", polity_id=MONTENEGRO, kind="administrative", rank=0, noun="country")
    p.add("scheme", code="me.opstina", polity_id=MONTENEGRO, kind="administrative", rank=1, noun="municipality")
    p.add("scheme", code="me.bm", polity_id=MONTENEGRO, kind="precinct", rank=2, noun="polling station")
    p.add("body", id=PARLIAMENT, polity_id=MONTENEGRO, kind="unicameral")
    p.name(PARLIAMENT, "Parliament of Montenegro", "en", "official")
    p.name(PARLIAMENT, "Skupština Crne Gore", "sr", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=MONTENEGRO, kind="head_of_state")
    p.name(PRESIDENCY, "President of Montenegro", "en", "official")
    p.name(PRESIDENCY, "Predsjednik Crne Gore", "sr", "native", False)
    list_rules = {"formula": "dhondt", "threshold": {"level": "list", "scope": "contest", "percent": 3}}
    systems = (
        (
            SYSTEM_LIST,
            list_rules,
            (
                "One national constituency elects 81 members by party-list proportional representation. Only lists "
                "with at least 3 percent of the valid votes in the constituency share in the allocation, as the "
                "commission's decisions on the 2020 and 2023 results cite under article 94 of the Law on the Election "
                "of Councilors and Members of Parliament. This system covers the 2006 and 2009 elections, held before "
                "the amendment of 2011 (Sl. list CG 46/11) deleted article 94a and rewrote the minority provisions of "
                "article 94. The minority-list rules in force at those two elections are not stored here, because the "
                "consolidated text consulted does not carry the earlier wording."
            ),
            None,
            LIST_RULES_2011,
        ),
        (
            SYSTEM_LIST_2011,
            list_rules,
            (
                "One national constituency elects 81 members by party-list proportional representation, with seats "
                "divided by the D'Hondt highest-quotient method (article 95). Only lists with at least 3 percent of "
                "the valid votes share in the allocation (article 94, paragraph 1). The structured rules above carry "
                "only that general threshold. Article 94, paragraph 2 sets exceptions for lists of a minority people "
                "or national minority community named in the list's submission or name. (1) If no such list reaches "
                "3 percent, the minority lists that each win at least 0.7 percent of the valid votes take part in the "
                "allocation as one combined list with their summed votes, and the sum counts only up to the level "
                "that yields at most three seats. The seats the combined list wins are divided among its member "
                "lists by dividing each list's votes by 1, 2, 3 and so on, and taking the largest quotients (article "
                "95). (2) If no list of the Croatian people in Montenegro qualifies under paragraph 1 or point 1, "
                "the most successful of them with at least 0.35 percent of the valid votes wins one seat. Point 1 is "
                "open only to lists of a people or community making up up to 15 percent of the population of the "
                "constituency in the last census (paragraph 3). A minority list's membership in a pre-election "
                "coalition with other lists does not deny the right to other submitters of that people's lists "
                "(paragraph 5). Article 94a, the earlier minority provision, was deleted by Sl. list CG 46/11, in "
                "force on 2011-09-24, so this system covers the 2012, 2016, 2020 and 2023 elections. The text is "
                "the consolidated law in the State Election Commission's catalogue, which reads the same in its "
                "versions amended through 2020 and through the 2025 and 2026 amendments."
            ),
            LIST_RULES_2011,
            None,
        ),
        (
            SYSTEM_PRESIDENT,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A candidate with a majority of the valid votes in the first round is elected; otherwise the two leading "
                "candidates go to a second round."
            ),
            None,
            None,
        ),
        (
            SYSTEM_REFERENDUM,
            {"formula": "referendum", "majority": {"share": 0.55}},
            "A yes or no question put to all voters. The 2006 referendum on statehood required 55 percent in favor.",
            None,
            None,
        ),
    )
    for id, rules, text, valid_from, valid_to in systems:
        p.add(
            "electoral_system",
            id=id,
            polity_id=MONTENEGRO,
            valid_from=valid_from,
            valid_to=valid_to,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for id, text in sorted(list_labels().items(), key=lambda item: item[1]):
        p.add(
            "party",
            id=id,
            kind="bloc" if re.search(r"koalicij|coalition|front", text, re.IGNORECASE) else "party",
            polity_id=MONTENEGRO,
        )
        p.name(id, text, "sr", "official")
    for id, text in sorted(candidate_labels().items(), key=lambda item: item[1]):
        p.add("person", id=id)
        p.name(id, text, "sr", "official")
    return p


class Units:
    """The reporting units of one package, made on first use."""

    def __init__(self, p: Package, day: str) -> None:
        self.p = p
        self.day = day
        self.ids: dict[str, str] = {}

    def _make(self, code: str, scheme: str, name: str, parent: str | None = None, kind: str = "standard") -> str:
        unit = self.ids.get(code)
        if unit is None:
            unit = self.ids[code] = key("unit", self.day, code)
            self.p.add(
                "reporting_unit",
                id=unit,
                polity_id=MONTENEGRO,
                scheme_code=scheme,
                code=f"{self.day}:{code}",
                parent_id=parent,
                area_relation="unlocated",
                area_id=None,
                kind=kind,
            )
            self.p.name(unit, name, "sr", "official")
        return unit

    def country(self) -> str:
        return self._make(COUNTRY, "me.country", "Montenegro")

    def municipality(self, place: str) -> str:
        return self._make(f"m:{place}", "me.opstina", place)

    def unit(self, row: Row, level: str) -> str:
        if level == "national":
            return self.country()
        if level == "municipality":
            return self.municipality(row.place)
        parent = None if row.place == PRISON else self.municipality(row.place)
        text = row.name if row.place == PRISON else f"{row.name}, {row.place}"
        return self._make(f"s:{row.place}/{row.code}", "me.bm", text, parent, row.kind)


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
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


def _result_set(
    p: Package, election: str, contest: str, name: str, stage: str, default: bool, method: str, completeness: float
) -> str:
    result_set = key("result_set", name)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage=stage,
        kind="counted",
        source_id=DIK_SOURCE,
        status="active",
        is_default=default,
        completeness=completeness,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    return result_set


def _fill(
    p: Package, units: Units, result_set: str, t: Table, options: list[str], official: Official | None
) -> dict[str, int]:
    """Store a table's figures per unit and the declared national figures; returns the national sums."""
    stored: set[str] = set()
    sums = {measure: 0 for measure in ACCOUNTING}
    for row in t.rows:
        unit = units.unit(row, t.level)
        for measure in ACCOUNTING:
            value = row.acc.get(measure)
            if measure == "registered" and row.kind == "prison" and not value:
                continue  # prisoners vote without being on a roll, and some tables print a zero for them
            if value is not None:
                stored.add(measure)
                sums[measure] += value
                _put(p, result_set, unit, None, MEASURES[measure], value)
        for option, value in zip(options, row.votes, strict=True):
            _put(p, result_set, unit, option, VOTES, value)
    if official is not None:
        for measure in ACCOUNTING:
            value = official.acc.get(measure)
            if value is not None and measure in stored:
                p.add("declared", result_set_id=result_set, option_id=None, measure=MEASURES[measure], value=value)
        if official.votes is not None:
            for option, value in zip(options, official.votes, strict=True):
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
    return sums


def _election(p: Package, name: str, day: date, kind: str, english: str, native: str, path: str) -> str:
    election = key("election", name)
    p.add("election", id=election, polity_id=MONTENEGRO, kind=kind, date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    p.name(election, native, "sr", "native", False)
    return election


def _contest(
    p: Package, name: str, election: str, kind: str, english: str, native: str, path: str, **fields: object
) -> str:
    contest = key("contest", name)
    row = {"round_number": 1, "default_result_kind": "counted", **fields}
    p.add("contest", id=contest, election_id=election, kind=kind, **row)
    p.name(contest, english, "en", "official")
    p.name(contest, native, "sr", "native", False)
    p.add("slug", path=path, entity_id=contest, is_current=True)
    return contest


def _uses(p: Package, *names: str) -> None:
    for name in names:
        p.uses(raw.cached(SOURCE, FILES[name]))


def _list_options(p: Package, tag: str, contest: str, labels: list[str]) -> list[str]:
    options = []
    for position, label in enumerate(labels, 1):
        option = key("option", tag, position)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party_id(label),
            ballot_position=position,
            ballot_label=label,
        )
    return options


def _candidate_options(p: Package, tag: str, contest: str, labels: list[str]) -> list[str]:
    options = []
    for position, label in enumerate(labels, 1):
        option = key("option", tag, position)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=clean(label),
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, position),
            person_id=key("person", person_key(label)),
            option_id=option,
            role="head",
            ballot_name=clean(label),
        )
    return options


def national_table(labels: list[str], official: Official) -> Table:
    row = Row("", COUNTRY, "Montenegro", dict(official.acc), [v or 0 for v in official.votes or []])
    return Table(labels, [row], level="national")


def _completeness(sums: dict[str, int], official: Official) -> float:
    valid = official.acc.get("valid")
    if not valid or not sums.get("valid"):
        return 1.0
    return round(min(1.0, sums["valid"] / valid), 6)


def _caveat(p: Package, code: str, result_set: str, text: str) -> None:
    """Accept a finding the source's own figures cause, and say so on the result set."""
    p.accept(code, text)
    p.add(
        "note",
        id=key("note", result_set, code),
        entity_id=result_set,
        kind="caveat",
        body_md=text[0].upper() + text[1:] + ". The figures are stored as printed.",
        lang="en",
    )


def _president_outcomes(p: Package, result_set: str, tag: str, options: list[str], kinds: dict[int, str]) -> None:
    for index, kind in kinds.items():
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=options[index],
            kind=kind,
        )


def _leader(official: Official) -> int:
    votes = official.votes or []
    return max(range(len(votes)), key=lambda i: votes[i])


def _candidate_set(
    p: Package,
    units: Units,
    *,
    tag: str,
    election: str,
    contest: str,
    labels: list[str],
    detail: Table | None,
    official: Official,
    detail_stage: str,
    note: str,
) -> tuple[list[str], str]:
    """A national official set, plus the finer table when there is one; returns the options and the default set."""
    options = _candidate_options(p, tag, contest, labels)
    if detail is None:
        result_set = _result_set(p, election, contest, tag, "official", True, NATIONAL_METHOD, 1.0)
        _fill(p, units, result_set, national_table(labels, official), options, official)
        return options, result_set
    coarse = _result_set(p, election, contest, f"{tag}-national", "official", True, NATIONAL_METHOD, 1.0)
    _fill(p, units, coarse, national_table(labels, official), options, official)
    own = Official(dict(detail.total.acc), [v or 0 for v in detail.total.votes]) if detail.total is not None else None
    fine = key("result_set", tag)
    result_set = _result_set(p, election, contest, tag, detail_stage, True, note, 1.0)
    assert fine == result_set
    sums = _fill(p, units, result_set, detail, options, own)
    for row in p.rows["result_set"]:
        if row["id"] == result_set:
            row["completeness"] = _completeness(sums, official)
    return options, coarse


def build_referendum(day: str) -> Package:
    first = DAYS[day]
    p = Package(
        f"me.dik/{day}",
        SOURCE,
        f"The Montenegrin referendum on state status of {first} by polling station, from the State Election Commission's "
        "published results",
        depends=["me.dik/registry"],
    )
    p.external.add(MONTENEGRO)
    _uses(p, "ref2006", "ref2006-mun")
    election = _election(
        p,
        "ref2006",
        first,
        "referendum",
        "2006 Montenegrin independence referendum",
        "Referendum o državno-pravnom statusu Republike Crne Gore 2006",
        f"me/{day}-referendum",
    )
    contest = _contest(
        p,
        "ref2006",
        election,
        "ballot_question",
        "2006 Montenegrin referendum on state status",
        "Referendum o državno-pravnom statusu 2006",
        f"me/{day}-referendum/question",
        electoral_system_id=SYSTEM_REFERENDUM,
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    t = table("ref2006")
    options = []
    for position, label in enumerate(t.labels, 1):
        option = key("option", "ref2006", position)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=label,
        )
    assert t.total is not None
    official = Official(dict(t.total.acc), [v or 0 for v in t.total.votes])
    result_set = _result_set(p, election, contest, "ref2006", "official", True, METHOD, 1.0)
    _fill(p, Units(p, day), result_set, t, options, official)
    p.add("outcome", result_set_id=result_set, option_id=options[0], kind="won")
    p.add("outcome", result_set_id=result_set, option_id=options[1], kind="lost")
    return p


LIST_NOTE = (
    "The candidates of each list are from the State Election Commission's consolidated list of lists, in the order "
    "the list printed them; the number is the candidate's place on the list. Names are as printed, with academic "
    "titles kept in the ballot name. The commission published the lists before the vote and did not publish who "
    "took the seats of each list, so no candidate is marked as elected."
)


def _candidates(p: Package, name: str, contest: str, options: list[str], labels: list[str]) -> None:
    """Every list's candidates as list members, each a person of this election's own."""
    for number, (slate, option) in enumerate(zip(slates_for(name, labels), options, strict=True), 1):
        for position, text in sorted(slate.names.items()):
            person = key("person", name, number, position)
            p.add("person", id=person)
            p.name(person, person_name(text), "sr", "official")
            p.add(
                "candidacy",
                id=key("candidacy", name, number, position),
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=position,
                ballot_name=text,
            )
    p.add("note", id=key("note", contest, "list"), entity_id=contest, kind="methodology", body_md=LIST_NOTE, lang="en")


def build_parliament(day: str) -> Package:
    first = DAYS[day]
    name = PARLIAMENTARY[day]
    p = Package(
        f"me.dik/{day}",
        SOURCE,
        f"The Montenegrin parliamentary election of {first} by polling station, from the State Election Commission's "
        "published results",
        depends=["me.dik/registry"],
    )
    p.external.add(MONTENEGRO)
    _uses(p, name, name.replace("par", "lists"))
    _uses(p, *([f"{name}-final"] if name in ("par2006", "par2009", "par2016", "par2020", "par2023") else []))
    if name == "par2023":
        _uses(p, "par2023-prelim")
    election = _election(
        p,
        name,
        first,
        "general",
        f"{first.year} Montenegrin parliamentary election",
        f"Izbori za poslanike u Skupštinu Crne Gore {first.year}",
        f"me/{day}-general",
    )
    contest = _contest(
        p,
        name,
        election,
        "list_allocation",
        f"{first.year} Montenegrin parliamentary election",
        f"Izbori za poslanike u Skupštinu Crne Gore {first.year}",
        f"me/{day}-general/parliament",
        body_id=PARLIAMENT,
        magnitude=81,
        electoral_system_id=SYSTEM_LIST if first < LIST_RULES_2011 else SYSTEM_LIST_2011,
    )
    t = table(name)
    options = _list_options(p, name, contest, t.labels)
    _candidates(p, name, contest, options, t.labels)
    official = national(name)
    units = Units(p, day)
    seats = seats_for(name)
    if name == "par2023":
        coarse = _result_set(p, election, contest, f"{name}-national", "official", True, NATIONAL_METHOD, 1.0)
        _fill(p, units, coarse, national_table(t.labels, official), options, official)
        result_set = _result_set(p, election, contest, name, "official", False, METHOD_2023, 1.0)
        _fill(p, units, result_set, t, options, Official({}, official.votes))
        outcomes = coarse
    else:
        result_set = _result_set(p, election, contest, name, "official", True, METHOD, 1.0)
        _fill(p, units, result_set, t, options, official)
        outcomes = result_set
    for index, count in sorted(seats.items()):
        p.add("outcome", result_set_id=outcomes, option_id=options[index], kind="won", seats=count)
    if name == "par2023" and DISAGREE:
        listed = "; ".join(
            f"{where}, {station} ({valid} valid ballots against {votes} list votes)"
            for where, station, valid, votes in DISAGREE
        )
        _caveat(
            p,
            "accounting.votes_valid",
            result_set,
            "at these polling stations the preliminary table's valid ballots differ from the list votes in the final "
            f"table: {listed}",
        )
    if name == "par2006":
        _caveat(
            p,
            "accounting.declared",
            result_set,
            "the decision on the results prints 1,284 votes for list 5, Demokratska stranka Crne Gore, while its "
            "polling-station rows add up to 1,286",
        )
    if name == "par2016":
        _caveat(
            p,
            "accounting.ballots_sum",
            result_set,
            "the Tehnička škola II polling station in Cetinje prints 497 used ballots against 484 valid and 12 invalid "
            "ballots",
        )
    if name == "par2020":
        _caveat(
            p,
            "accounting.ballots_sum",
            result_set,
            "polling station 1 of JUMSŠ Braća Selić in Kolašin prints 757 used ballots against 742 valid and 14 invalid "
            "ballots, because one voter left with the ballot, as the decision on the results records",
        )
    return p


def build_presidential(day: str) -> Package:
    first = DAYS[day]
    name = PRESIDENTIAL[day]
    year = first.year
    p = Package(
        f"me.dik/{day}",
        SOURCE,
        f"The Montenegrin presidential election of {first} by polling station, from the State Election Commission's "
        "published results",
        depends=["me.dik/registry"],
    )
    p.external.add(MONTENEGRO)
    _uses(p, *{"pres2008": ["pres2008", "pres2008-final"], "pres2013": ["pres2013", "pres2013-final"]}.get(name, []))
    if name == "pres2018":
        _uses(p, "pres2018-prov", "pres2018-final")
    election = _election(
        p,
        name,
        first,
        "general",
        f"{year} Montenegrin presidential election",
        f"Izbori za predsjednika Crne Gore {year}",
        f"me/{day}-presidential",
    )
    contest = _contest(
        p,
        name,
        election,
        "office",
        f"{year} Montenegrin presidential election",
        f"Izbori za predsjednika Crne Gore {year}",
        f"me/{day}-presidential/president",
        office_id=PRESIDENCY,
        magnitude=1,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    t = table(name)
    units = Units(p, day)
    if name == "pres2018":
        decided = decision("pres2018-final")
        official = Official(dict(decided.acc), decided.votes)
        options, result_set = _candidate_set(
            p,
            units,
            tag=name,
            election=election,
            contest=contest,
            labels=t.labels,
            detail=t,
            official=official,
            detail_stage="preliminary",
            note=METHOD.replace("published the figures", "published the preliminary figures"),
        )
    else:
        official = national(name)
        options = _candidate_options(p, name, contest, t.labels)
        result_set = _result_set(p, election, contest, name, "official", True, METHOD, 1.0)
        _fill(p, units, result_set, t, options, official)
    leader = _leader(official)
    _president_outcomes(
        p, result_set, name, options, {i: "elected" if i == leader else "lost" for i in range(len(options))}
    )
    if name == "pres2018":
        _caveat(
            p,
            "accounting.ballots_sum",
            result_set,
            "the decision on the results prints 340,462 used ballots against 334,462 valid and 5,997 invalid ones, "
            "which it explains by an extra ballot at two Cetinje polling stations and by three voters who took their "
            "ballots away, and the preliminary count shows the same difference at those stations",
        )
    return p


def build_2023_first() -> Package:
    day = "2023-03-19"
    first = DAYS[day]
    p = Package(
        f"me.dik/{day}",
        SOURCE,
        f"The Montenegrin presidential election of {first}, first round, from the State Election Commission's decision "
        "on the final results",
        depends=["me.dik/registry"],
    )
    p.external.add(MONTENEGRO)
    _uses(p, "pres2023-final")
    election = _election(
        p,
        "pres2023-1",
        first,
        "general",
        "2023 Montenegrin presidential election",
        "Izbori za predsjednika Crne Gore 2023",
        f"me/{day}-presidential",
    )
    contest = _contest(
        p,
        "pres2023-1",
        election,
        "office",
        "2023 Montenegrin presidential election, first round",
        "Izbori za predsjednika Crne Gore 2023, prvi krug",
        f"me/{day}-presidential/president",
        office_id=PRESIDENCY,
        magnitude=1,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    decided = decision("pres2023-final", None, "IV Na izborima koji")
    official = Official(dict(decided.acc), decided.votes)
    options, result_set = _candidate_set(
        p,
        Units(p, day),
        tag="pres2023-1",
        election=election,
        contest=contest,
        labels=decided.labels,
        detail=None,
        official=official,
        detail_stage="official",
        note=NATIONAL_METHOD,
    )
    top = sorted(range(len(options)), key=lambda i: -(official.votes or [])[i])[:2]
    kinds = {i: "advanced" if i in top else "eliminated" for i in range(len(options))}
    _president_outcomes(p, result_set, "pres2023-1", options, kinds)
    _caveat(
        p,
        "accounting.ballots_sum",
        result_set,
        "the decision on the results prints 341,582 used ballots against 338,411 valid and 3,169 invalid ones, "
        "because two used ballots are missing from the Herceg Novi commission's report",
    )
    return p


def build_2023_runoff() -> Package:
    day = "2023-04-02"
    first = DAYS[day]
    p = Package(
        f"me.dik/{day}",
        SOURCE,
        f"The Montenegrin presidential election of {first}, second round, by municipality, from the State Election "
        "Commission's published results",
        depends=["me.dik/registry", "me.dik/2023-03-19"],
    )
    p.external.add(MONTENEGRO)
    _uses(p, "pres2023-final", "pres2023-r2-mun")
    election = _election(
        p,
        "pres2023-2",
        first,
        "runoff",
        "2023 Montenegrin presidential election, second round",
        "Izbori za predsjednika Crne Gore 2023, drugi krug",
        f"me/{day}-presidential-runoff",
    )
    contest = _contest(
        p,
        "pres2023-2",
        election,
        "office",
        "2023 Montenegrin presidential election, second round",
        "Izbori za predsjednika Crne Gore 2023, drugi krug",
        f"me/{day}-presidential-runoff/president",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=2,
        previous_round_id=key("contest", "pres2023-1"),
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    t = table("pres2023-r2")
    decided = decision("pres2023-final", "VII Državna")
    official = Official(dict(decided.acc), decided.votes)
    options, result_set = _candidate_set(
        p,
        Units(p, day),
        tag="pres2023-2",
        election=election,
        contest=contest,
        labels=t.labels,
        detail=t,
        official=official,
        detail_stage="preliminary",
        note=METHOD.replace("published the figures", "published the preliminary figures").replace(
            "Counted at each polling station by its electoral board and reported", "Counted and reported"
        ),
    )
    leader = _leader(official)
    _president_outcomes(
        p, result_set, "pres2023-2", options, {i: "elected" if i == leader else "lost" for i in range(2)}
    )
    return p


def build(day: str) -> Package:
    if day == "2006-05-21":
        return build_referendum(day)
    if day in PARLIAMENTARY:
        return build_parliament(day)
    if day == "2023-03-19":
        return build_2023_first()
    if day == "2023-04-02":
        return build_2023_runoff()
    return build_presidential(day)
