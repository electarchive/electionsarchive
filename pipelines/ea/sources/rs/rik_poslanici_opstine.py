"""The municipal tables of the Republic Statistical Office's books on the parliamentary elections of 1992, 1993 and 1997.

Each book is a scan with no text layer. For every constituency (izborna jedinica), Table P-4 of the 1992 and 1993
books and Table IJ-4 of the 1997 book print each list's votes in each municipality of the constituency, under a row
of totals for the constituency; the 1997 table also prints how many voters voted. A table runs over as many pages as
it needs for its lists, each page holding the same rows. The 1997 table adds a last row for the ballots that came by
post to the constituency's commission (НП-19ДОП), which belongs to no municipality.

``rik_scans.ocr`` finds the words on those pages. Each list's column is placed by its code in the header, and each row
by its height: the rows are evenly spaced, so a figure's row is its distance below the row of totals over the spacing.
RapidOCR misses many one- and two-digit figures on these noisy pages, so a cell it left empty is read again on its own
with the recognizer alone. RapidOCR does not read Cyrillic letters, so the municipalities come from the page images, in
the order the books print them (``ROWS``). Every column is checked against the constituency's total the table prints,
and that total against the list's constituency votes in the Commission's report of the overall results, which
``rik_poslanici`` reads; a cell the OCR misread is set in ``FIXES``, read from the page and proven by the column's sum.
``rik_poslanici`` reads the result from ``rik_poslanici_opstine.json``, so building the packages needs no OCR. Run
``extract`` to read the scans again.
"""

import importlib
import itertools
import json
import re
import statistics
import subprocess
import tempfile
import unicodedata
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

from ea.sources.rs import rik_scans

DATA_FILE = Path(__file__).with_name("rik_poslanici_opstine.json")
PRESIDENT_FILE = Path(__file__).with_name("rik_predsednik.json")
DPI = 300
# the treatments of a cell's image the recognizer reads: threshold of black, median filter, padding, and scale
TREATMENTS = ((90, False, 10, 0.6), (90, True, 10, 1), (110, True, 20, 1), (130, False, 20, 1), (130, True, 20, 1),
              (110, False, 20, 1), (150, True, 10, 1), (90, False, 10, 1))  # fmt: skip
SUPPLEMENT = "НП-19ДОП"


def _split(text: str) -> tuple[str, ...]:
    return tuple(text.split("|"))


_BELGRADE = _split(
    "Београд-Барајево|Београд-Вождовац|Београд-Врачар|Београд-Гроцка|Београд-Звездара|Београд-Земун|Београд-Лазаревац|"
    "Београд-Младеновац|Београд-Нови Београд|Београд-Обреновац|Београд-Палилула|Београд-Раковица|"
    "Београд-Савски Венац|Београд-Сопот|Београд-Стари Град|Београд-Чукарица"
)
_NINE: tuple[tuple[str, ...], ...] = (
    _BELGRADE,
    _split(
        "Ада|Алибунар|Бела Црква|Бечеј|Вршац|Жабаљ|Житиште|Зрењанин|Кањижа|Кикинда|Ковачица|Ковин|Нова Црња|"
        "Нови Бечеј|Нови Кнежевац|Опово|Панчево|Пландиште|Сента|Сечањ|Србобран|Суботица|Темерин|Тител|Чока"
    ),
    _split(
        "Александровац|Аранђеловац|Баточина|Брус|Врњачка Бања|Горњи Милановац|Кнић|Крагујевац|Краљево|Крушевац|"
        "Лапово|Лучани|Нови Пазар|Рача|Рашка|Рековац|Топола|Трстеник|Тутин|Чачак"
    ),
    _split(
        "Блаце|Бојник|Босилеград|Бујановац|Витина|Владичин Хан|Власотинце|Врање|Вучитрн|Гњилане|Житорађа|Звечан|"
        "Зубин Поток|Косовска Каменица|Косовска Митровица|Куршумлија|Лебане|Лепосавић|Лесковац|Медвеђа|Подујево|"
        "Прешево|Прокупље|Сурдулица|Трговиште|Црна Трава"
    ),
    _split(
        "Алексинац|Бабушница|Бела Паланка|Бољевац|Бор|Гаџин Хан|Димитровград|Дољевац|Зајечар|Кладово|Књажевац|"
        "Мајданпек|Мерошина|Неготин|Ниш|Пирот|Сврљиг|Сокобања"
    ),
    _split(
        "Апатин|Бач|Бачка Паланка|Бачка Топола|Бачки Петровац|Беочин|Врбас|Инђија|Ириг|Кула|Мали Иђош|Нови Сад|Оџаци|"
        "Пећинци|Рума|Сомбор|Сремска Митровица|Сремски Карловци|Стара Пазова|Шид"
    ),
    _split(
        "Гора|Дечани|Ђаковица|Исток|Качаник|Клина|Косово Поље|Липљан|Ново Брдо|Обилић|Ораховац|Пећ|Призрен|Приштина|"
        "Србица|Сува Река|Урошевац|Штимље|Штрпце"
    ),
    _split(
        "Варварин|Велика Плана|Велико Градиште|Голубац|Деспотовац|Жабари|Жагубица|Јагодина|Кучево|Мало Црниће|Параћин|"
        "Петровац|Пожаревац|Ражањ|Свилајнац|Смедерево|Смедеревска Паланка|Ћићевац|Ћуприја"
    ),
    _split(
        "Ариље|Бајина Башта|Богатић|Ваљево|Владимирци|Ивањица|Косјерић|Коцељева|Крупањ|Лајковац|Лозница|Љиг|Љубовија|"
        "Мали Зворник|Мионица|Нова Варош|Осечина|Пожега|Прибој|Пријепоље|Сјеница|Уб|Ужице|Чајетина|Шабац"
    ),
)


@cache
def president_1997() -> list[dict[str, Any]]:
    return json.loads(PRESIDENT_FILE.read_text())["1997-09-21"]


ALPHABET = " -абвгдђежзијклљмнњопрстћуфхцчџш"


def _collate(name: str) -> list[int]:
    return [ALPHABET.index(c) if c in ALPHABET else len(ALPHABET) for c in name.lower()]


def _twenty_nine() -> tuple[tuple[str, ...], ...]:
    """The municipalities of each of the 29 constituencies of 1997, from the same day's presidential book, in the
    Serbian alphabetical order the parliamentary book prints them in."""
    out: list[list[str]] = []
    for r in president_1997():
        if r["kind"] == "constituency":
            out.append([])
        elif r["kind"] == "municipality":
            out[-1].append(r["name"])
    return tuple((*sorted(names, key=_collate), SUPPLEMENT) for names in out)


@cache
def rows_of(year: str) -> tuple[tuple[str, ...], ...]:
    """The rows each constituency's table prints under its total, in order."""
    if year == "1997":
        return _twenty_nine()
    if year == "1993":  # in 1993 the table of Priština also prints Glogovac, which had no figures in 1992
        return tuple(("Глоговац", *names) if n == 6 else names for n, names in enumerate(_NINE))
    return _NINE


def _spans(*starts: int) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(range(a, b)) for a, b in itertools.pairwise(starts))


# the pages of each constituency's table
TABLES: dict[str, tuple[tuple[int, ...], ...]] = {
    "1992": _spans(41, 44, 46, 49, 52, 55, 57, 59, 61, 63),
    "1993": _spans(52, 55, 58, 61, 63, 66, 68, 70, 73, 76),
    "1997": (
        (25, 26), (31, 32), (37, 38), (42, 43), (48, 49, 50), (55, 56, 57), (61, 62), (66, 67), (72, 73, 74),
        (78, 79), (84, 85), (89, 90), (94, 95), (99,), (103, 104), (109, 110), (114,), (118,), (122, 123),
        (127, 128), (133, 134, 135), (139, 140), (144, 145), (150, 151), (155, 156), (160,), (164,), (168, 169),
        (173,),
    ),
}  # fmt: skip

# Cells the OCR dropped or misread, by year, constituency, row (0 is the constituency's total, then the rows of
# ``rows_of`` from 1) and list code, each read from the page and proven by the column's sum.
FIXES: dict[str, dict[tuple[int, int, str], int]] = {
    "1992": {
        (1, 13, "160"): 55,
        (2, 13, "207"): 1,
        (4, 12, "017"): 1,
        (4, 21, "016"): 1,
        (4, 5, "343"): 1,
        (4, 5, "342"): 1,
        (4, 8, "342"): 1480,
        (4, 16, "011"): 11,
        (6, 4, "003"): 241,
        (6, 9, "003"): 121,
        (6, 13, "003"): 191,
        (6, 15, "363"): 111,
        (7, 5, "372"): 1,
        (7, 13, "205"): 81,
        (7, 18, "205"): 3,
        (7, 18, "372"): 0,
        (4, 26, "441"): 1,
        (8, 15, "068"): 105,
        (9, 25, "086"): 1106,
    },
    "1993": {
        (1, 11, "056"): 11694,
        (1, 3, "003"): 114,
        (1, 5, "034"): 96,
        (1, 8, "034"): 11,
        (2, 16, "201"): 1141,
        (2, 21, "201"): 1022,
        (2, 1, "020"): 113,
        (2, 22, "211"): 741,
        (2, 22, "060"): 1478,
        (2, 21, "068"): 10,
        (2, 0, "324"): 999,
        (3, 1, "006"): 1024,
        (3, 3, "006"): 571,
        (3, 15, "006"): 1133,
        (3, 16, "334"): 16,
        (3, 17, "334"): 9,
        (3, 6, "129"): 14,
        (3, 14, "335"): 1,
        (3, 5, "335"): 11,
        (3, 15, "334"): 11,
        (4, 3, "201"): 444,
        (4, 6, "201"): 1152,
        (4, 7, "201"): 1605,
        (4, 16, "201"): 1145,
        (4, 2, "216"): 1,
        (4, 2, "085"): 103,
        (4, 19, "041"): 1505,
        (4, 21, "041"): 0,
        (4, 16, "211"): 28,
        (4, 17, "211"): 74,
        (4, 24, "211"): 117,
        (4, 13, "345"): 1,
        (4, 26, "342"): 1,
        (4, 3, "341"): 1,
        (7, 19, "006"): 5,
        (7, 10, "068"): 0,
        (8, 3, "384"): 11,
        (8, 15, "384"): 14,
        (8, 1, "041"): 1137,
        (8, 13, "041"): 1136,
        (8, 16, "041"): 1057,
        (9, 21, "005"): 14,
        (9, 7, "086"): 11,
    },
    "1997": {(28, 5, "003"): 0, (27, 8, "voters"): 0},
}

# Cells the scan does not show, under a stain or a hole in the page, by year, constituency, row and list code. Where a
# column has one, it is the column's total less its other rows, flagged as derived; where it has more, they are left out.
ILLEGIBLE: dict[str, set[tuple[int, int, str]]] = {
    "1992": set(),
    "1993": {
        (2, 21, "211"),
        (2, 22, "323"),
        (2, 23, "323"),
        (2, 24, "323"),
        (4, 21, "001"),
        (4, 22, "001"),
        (4, 21, "003"),
        (4, 22, "003"),
        (4, 21, "068"),
        (4, 22, "068"),
        (4, 22, "041"),
        (9, 20, "085"),
        (9, 21, "085"),
    },
    "1997": set(),
}

Cell = list[Any]  # value, page, x0, y0, x1, y1 in points


def _number(text: str) -> int | None:
    """A count as the tables print it, with dots between thousands."""
    t = text.strip().strip(".,;:'`-_").replace(",", ".")
    if not re.fullmatch(r"\d{1,3}(\.\d{3})*", t):
        return None
    return int(t.replace(".", ""))


def _percent(text: str) -> bool:
    return re.fullmatch(r"\d{1,3}[.,]\d{2}", text.strip().strip(".,")) is not None


def _lines(words: list[list[Any]]) -> list[tuple[float, list[list[Any]]]]:
    out: list[tuple[float, list[list[Any]]]] = []
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        y = (w[1] + w[3]) / 2
        if out and y - out[-1][0] <= 3.5:
            out[-1][1].append(w)
        else:
            out.append((y, [w]))
    return [(statistics.mean((w[1] + w[3]) / 2 for w in ws), sorted(ws, key=lambda w: w[0])) for _, ws in out]


def _code(w: list[Any]) -> str | None:
    t = str(w[4]).strip().strip(".").replace("S", "0").replace("$", "0")
    return t if re.fullmatch(r"\d{3}", t) else None


def _fill(codes: list[tuple[str, float]], figures: list[float], left: float | None) -> list[tuple[str, float]]:
    """The columns of a header whose codes the OCR did not all read, with a stand-in name for each one it missed.

    The columns are evenly spaced; a gap of two spacings or more between read codes, or figures right of the last one,
    holds a column whose code was missed. Each stand-in is named by its place, and its list is found by its total.
    """
    if len(codes) < 2:
        return codes
    spacing = min(b[1] - a[1] for a, b in itertools.pairwise(codes))
    out = [codes[0]]
    for code, x in codes[1:]:
        last = out[-1][1]
        for k in range(1, round((x - last) / spacing)):
            out.append((f"?{round(last + k * spacing)}", last + k * spacing))
        out.append((code, x))
    while sum(1 for x in figures if out[-1][1] + spacing * 0.6 < x < out[-1][1] + spacing * 1.4) >= 3:
        x = out[-1][1] + spacing
        out.append((f"?{round(x)}", x))
    while left is not None and sum(1 for x in figures if left < x < out[0][1] - spacing * 0.6) >= 3:
        x = out[0][1] - spacing
        out.insert(0, (f"?{round(x)}", x))
    return out


def constituency_of(words: list[list[Any]]) -> int | None:
    """The constituency number in a page's title, which reads like ``...6poj 12 - ...``."""
    for w in words:
        if w[1] < 170:
            found = re.search(r"(?:6poj|poj|6oj|bpoj|6Poj)\s*(\d{1,2})", str(w[4]))
            if found:
                return int(found.group(1))
    return None


def layout(words: list[list[Any]], count: int, wide: bool = False) -> dict[str, Any]:
    """The list codes of a page with each one's column, and the height of each of its ``count`` rows.

    The first line of figures under the header is the row of totals; the rows below it are evenly spaced.
    """
    lines = _lines(words)
    total = next(
        (y for y, ws in lines if any(w[2] < 200 and re.search(r"[YV]K[YV][TIП]", str(w[4]).upper()) for w in ws)), 280
    )
    header = max((line for line in lines if line[0] < total - 5), key=lambda line: sum(1 for w in line[1] if _code(w)))
    codes = [(c, (w[0] + w[2]) / 2) for w in header[1] if (c := _code(w))]
    if total == 280:  # the word for the total was not read: take the first line of figures under the header
        total = next(
            y
            for y, ws in lines
            if y > header[0] + 6 and sum(1 for w in ws if _number(str(w[4])) is not None and w[0] > 150) >= 2
        )
    figures = [
        (w[0] + w[2]) / 2
        for y, ws in lines
        if total - 4 < y < 500
        for w in ws
        if _number(str(w[4])) is not None and not _percent(str(w[4]))
    ]
    codes = _fill(codes, figures, None if wide else 200)
    figured = [
        (y, ws)
        for y, ws in lines
        if y > header[0] + 6
        and y > total - 4
        and (
            sum(1 for w in ws if _number(str(w[4])) is not None and w[0] > 150) >= 2
            or any(w[2] < 200 and len(re.sub(r"[^A-Za-z]", "", str(w[4]))) >= 3 for w in ws)
        )
        and y < 500
    ]
    top = figured[0][0]
    gaps = [b[0] - a[0] for a, b in itertools.pairwise(figured) if 7 < b[0] - a[0] < 14]
    pitch = statistics.median(gaps)
    # refine: fit the line heights to their nearest row index
    pairs = [(round((y - top) / pitch), y) for y, _ in figured]
    pairs = [(i, y) for i, y in pairs if 0 <= i < count]
    if len(pairs) >= 3:
        mi, my = statistics.mean(i for i, _ in pairs), statistics.mean(y for _, y in pairs)
        var = sum((i - mi) ** 2 for i, _ in pairs)
        if var:
            pitch = sum((i - mi) * (y - my) for i, y in pairs) / var
            top = my - pitch * mi
    return {"codes": codes, "header": header[0], "top": top, "pitch": pitch}


class Reader:
    """Reads one cell of a page image with RapidOCR's recognizer alone."""

    def __init__(self, year: str) -> None:
        self.engine: Any = importlib.import_module("rapidocr_onnxruntime").RapidOCR()
        self.pdf = rik_scans.fetch(year)
        self.images: dict[int, Any] = {}
        self.tmp = tempfile.TemporaryDirectory()

    def image(self, page: int) -> Any:
        if page not in self.images:
            image_module: Any = importlib.import_module("PIL.Image")
            out = f"{self.tmp.name}/p{page}"
            subprocess.run(
                ["pdftoppm", "-r", str(DPI), "-png", "-singlefile", "-f", str(page), "-l", str(page), self.pdf, out],
                check=True,
            )
            self.images[page] = image_module.open(f"{out}.png").convert("L")
        return self.images[page]

    def votes(self, page: int, x0: float, y0: float, x1: float, y1: float) -> Counter[int]:
        """The counts the recognizer reads in a cell under several treatments of its image, with how often each."""
        numpy: Any = importlib.import_module("numpy")
        image_module: Any = importlib.import_module("PIL.Image")
        image_filter: Any = importlib.import_module("PIL.ImageFilter")
        s = DPI / 72
        base = self.image(page).crop((int(x0 * s), int(y0 * s), int(x1 * s), int(y1 * s)))
        found: Counter[int] = Counter()
        for threshold, median, pad, scale in TREATMENTS:
            crop = base.filter(image_filter.MedianFilter(3)) if median else base
            a = numpy.array(crop).copy()
            dark = a < threshold
            a[:, dark.sum(0) > 0.6 * a.shape[0]] = 255  # the table's rules
            a[dark.sum(1) > 0.6 * a.shape[1], :] = 255
            a = numpy.where(a < threshold, 0, 255).astype("uint8")
            dark = a < 128
            cols = numpy.where(dark.sum(0) >= 2)[0]
            rows = numpy.where(dark.sum(1) >= 2)[0]
            if len(cols) == 0 or len(rows) == 0:
                continue
            a = a[max(int(rows[0]) - 4, 0) : int(rows[-1]) + 5, max(int(cols[0]) - 4, 0) : int(cols[-1]) + 5]
            image = image_module.fromarray(a)
            if scale != 1:
                image = image.resize((max(int(image.width * scale), 1), max(int(image.height * scale), 1)))
            padded = image_module.new("L", (image.width + 2 * pad, image.height + 2 * pad), 255)
            padded.paste(image, (pad, pad))
            text, _ = self.engine.text_recognizer([numpy.array(padded.convert("RGB"))])
            value = _number(_digits(str(text[0][0]))) if text else None
            if value is not None:
                found[value] += 1
        return found


def _digits(text: str) -> str:
    """A recognizer's reading of a cell with look-alike characters taken for the digits they stand for."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(str.maketrans({"i": "1", "l": "1", "I": "1", "!": "1", "|": "1", "O": "0", "o": "0"}))
    return re.sub(r"[^0-9.,]", "", text)


def read_page(
    words: list[list[Any]], page: int, count: int, year: str, reader: Reader | None
) -> tuple[dict[str, list[Cell | None]], list[Cell | None]]:
    """Each list's cells on a page, by its code, from the row of totals down; and, for 1997, the voters' column."""
    lay = layout(words, count, year == "1997")
    codes = lay["codes"]
    spacing = statistics.median(b[1] - a[1] for a, b in itertools.pairwise(codes)) if len(codes) > 1 else 50
    if year == "1997":  # each list has three columns, votes and two percentages; the code sits over the middle one
        centers = {c: x - spacing / 3 for c, x in codes}
        width = spacing / 3
    else:
        centers = {c: x for c, x in codes}
        width = spacing
    first = min(centers.values())
    voters_x = first - width
    out: dict[str, list[Cell | None]] = {c: [None] * count for c in centers}
    voters: list[Cell | None] = [None] * count
    for w in words:
        y = (w[1] + w[3]) / 2
        if y < lay["header"] + 6:
            continue
        i = round((y - lay["top"]) / lay["pitch"])
        if not 0 <= i < count or abs(y - (lay["top"] + i * lay["pitch"])) > lay["pitch"] * 0.42:
            continue
        text = str(w[4])
        if _percent(text):
            continue
        value = _number(text)
        if value is None:
            continue
        x = (w[0] + w[2]) / 2
        code = min(centers, key=lambda c: abs(centers[c] - x))
        cell = [value, page, w[0], w[1], w[2], w[3]]
        if abs(centers[code] - x) < width * 0.45:
            if out[code][i] is None:
                out[code][i] = cell
        elif year == "1997" and abs(x - voters_x) < width * 0.6 and voters[i] is None:
            voters[i] = cell
    if reader is not None:
        for code, cells in out.items():
            # the 1997 figures are set flush right in a narrow column: read from the column's right edge
            rights = [c[4] for c in cells if c is not None]
            right = statistics.median(rights) if year == "1997" and len(rights) >= 2 else None
            for i, cell in enumerate(cells):
                y = lay["top"] + i * lay["pitch"]
                if right is not None:
                    box = (right - width * 1.02, y - lay["pitch"] * 0.48, right + 2.5)
                else:
                    box = (centers[code] - width * 0.48, y - lay["pitch"] * 0.48, centers[code] + width * 0.48)
                votes = reader.votes(page, box[0], box[1], box[2], y + lay["pitch"] * 0.48)
                if cell is not None:
                    votes[cell[0]] += 3
                ranked = [v for v, _ in votes.most_common()]
                if not ranked:
                    continue
                if cell is None:
                    cell = [ranked[0], page, round(box[0], 1), round(y - 5, 1), round(box[2], 1), round(y + 5, 1)]
                    cells[i] = cell
                cell[0] = ranked[0]
                cell.append(ranked[1:])
    return out, voters


@cache
def report() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name("rik_poslanici.json").read_text())


def tables(year: str, pages: dict[int, list[list[Any]]], reader: Reader | None) -> tuple[dict[str, Any], list[str]]:
    """Every constituency's table: its rows, each list's cells by code, and what the checks find wrong."""
    problems: list[str] = []
    out: dict[str, Any] = {}
    groups = rows_of(year)
    for n, span in enumerate(TABLES[year], 1):
        names = groups[n - 1]
        count = len(names) + 1
        columns: dict[str, list[Cell | None]] = {}
        voters: list[Cell | None] = [None] * count
        for page in span:
            words = pages[page]
            if constituency_of(words) not in (n, None):
                problems.append(f"{year} page {page}: titled constituency {constituency_of(words)}, not {n}")
            cells, seen = read_page(words, page, count, year, reader)
            for code, column in cells.items():
                if code in columns:
                    problems.append(f"{year} {n}: code {code} on two pages")
                columns[code] = column
            voters = [a or b for a, b in zip(voters, seen, strict=True)]
        for (c, i, code), value in FIXES[year].items():
            if c == n and code == "voters":
                voters[i] = [value, *_place(voters, i)]
            elif c == n:
                cell = columns[code][i]
                columns[code][i] = [value, *cell[1:]] if cell else [value, *_place(columns[code], i)]
        fixed = {(i, code) for (c, i, code) in FIXES[year] if c == n}
        printed = {row["cells"][str(n)][0] for row in report()[year]["lists"] if str(n) in row["cells"]}
        for code, column in columns.items():
            hidden = sorted(i for c, i, k in ILLEGIBLE[year] if c == n and k == code)
            for i in hidden:
                column[i] = None
            if hidden:
                if any(cell is None for i, cell in enumerate(column) if i not in hidden):
                    problems.append(f"{year} {n} {code}: no figure in rows besides the illegible {hidden}")
                    continue
                rest = sum(cell[0] for cell in column[1:] if cell)
                total = column[0]
                if total is None:
                    problems.append(f"{year} {n} {code}: no total")
                    continue
                if len(hidden) == 1:
                    value = total[0] - rest
                    if value < 0:
                        problems.append(f"{year} {n} {code}: the other rows add up to more than the total")
                    column[hidden[0]] = [value, *_place(column, hidden[0]), "derived"]
                elif rest > total[0]:
                    problems.append(f"{year} {n} {code}: the legible rows add up to more than the total")
                continue
            absent = [i for i, cell in enumerate(column) if cell is None]
            if absent:
                problems.append(f"{year} {n} {code}: no figure in rows {absent}")
                continue
            problem = _resolve(column, printed, {i for i, c in fixed if c == code})
            if problem:
                problems.append(f"{year} {n} {code}: {problem}")
        for column in columns.values():
            for cell in column:
                if cell is not None and cell[-1] != "derived":
                    del cell[6:]
        if year == "1997":
            if any(cell is None for cell in voters):
                problems.append(
                    f"{year} {n} voters: no figure in rows {[i for i, c in enumerate(voters) if c is None]}"
                )
            elif sum(c[0] for c in voters[1:] if c) != (voters[0] or [0])[0]:
                problems.append(f"{year} {n} voters: rows do not add up to the total")
        out[str(n)] = {"rows": list(names), "lists": columns, "voters": voters if year == "1997" else None}
    return out, problems


def _resolve(column: list[Any], printed: set[int], fixed: set[int]) -> str | None:
    """Settle a column on readings that add up to its total, which the report prints for the list.

    Each cell holds the reading most treatments agree on and then the others; a reading other than the first is taken
    only when it is the one change, of one cell or of two, that makes the rows add up to the total.
    """

    def alternatives(i: int) -> list[int]:
        cell = column[i]
        return [] if i in fixed or len(cell) < 7 else list(cell[6])

    total = column[0]
    if total[0] not in printed:
        better = [v for v in alternatives(0) if v in printed]
        if len(better) == 1:
            total[0] = better[0]
    gap = total[0] - sum(c[0] for c in column[1:])
    if gap == 0:
        return None
    singles = [(i, v) for i in range(1, len(column)) for v in alternatives(i) if v - column[i][0] == gap]
    if len(singles) == 1:
        i, v = singles[0]
        column[i][0] = v
        return None
    if not singles:
        pairs = [
            ((i, v), (j, w))
            for i in range(1, len(column))
            for v in alternatives(i)
            for j in range(i + 1, len(column))
            for w in alternatives(j)
            if v - column[i][0] + w - column[j][0] == gap
        ]
        if len(pairs) == 1:
            for i, v in pairs[0]:
                column[i][0] = v
            return None
    values = [c[0] for c in column]
    return f"rows add up to {sum(values[1:])}, not {values[0]}: {values}; options {[alternatives(i) for i in range(len(column))]}"


def _place(column: list[Cell | None], i: int) -> list[Any]:
    """A box for a cell the OCR did not find, between the cells above and below it in its column."""
    known = [(j, c) for j, c in enumerate(column) if c]
    above = max((jc for jc in known if jc[0] < i), key=lambda jc: jc[0])
    below = min((jc for jc in known if jc[0] > i), key=lambda jc: jc[0], default=None)
    j, c = above
    pitch = (below[1][3] - c[3]) / (below[0] - j) if below else 10.5
    shift = pitch * (i - j)
    return [c[1], c[2], round(c[3] + shift, 1), c[4], round(c[5] + shift, 1)]


def extract(year: str, source: Path | None = None, cells: bool = True) -> None:
    """Read a book's tables again and update ``rik_poslanici_opstine.json``; give a file of ``rik_scans.ocr`` to reuse."""
    path = source or Path(f"rik_scans_{year}.json")
    if source is None:
        rik_scans.ocr(year, path)
    pages = {int(k): v for k, v in json.loads(path.read_text()).items()}
    found, problems = tables(year, pages, Reader(year) if cells else None)
    for problem in problems:
        print("  ", problem)
    stored = json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}
    stored[year] = found
    DATA_FILE.write_text(json.dumps(stored, ensure_ascii=False, separators=(",", ":")))


@cache
def data() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text())
