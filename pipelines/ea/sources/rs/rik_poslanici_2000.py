"""The municipal tables of the Republic Statistical Office's book on the parliamentary election of 23 December 2000.

The book is a scan with no text layer. Table 2.1 (results of voting) and Table 2.2 (the votes of each list) each
span two facing pages: the even page holds the left half of the table and the next odd page its right half, and the
rows of the two halves line up by their height on the page. Each table has four such spreads, 206 rows in all: the
Republic, its three regions, and then each district (okrug) followed by its municipalities. ``rik_scans.ocr`` finds
every word on those pages; ``rows`` assigns each number to its column by its place in the table and its row by its
height.

RapidOCR does not read Cyrillic letters, so the names come from the page images, in the order the book prints them
(``OKRUZI``), and each municipality is matched to the Commission's register by its name. Every figure is checked
against the redundancy the book prints: a municipality's invalid and valid ballots add up to the ballots used, its
valid ballots equal the first column of Table 2.2 and the lists' votes, and every printed percentage matches the
figure beside it; a district's rows add up to the district, the districts to the regions, and the regions to the
Republic. A cell the OCR dropped or misread is set in ``FIXES``, each read from the page and proven by those checks.
``rik_poslanici`` reads the result from ``rik_poslanici_2000.json``, so building the package needs no OCR. Run
``extract`` to read the scan again.
"""

import json
import re
import statistics
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from ea.sources.rs import rik_scans

DATA_FILE = Path(__file__).with_name("rik_poslanici_2000.json")
PAGES = range(10, 26)
SPREADS = (10, 12, 14, 16)  # the left page of each spread of Table 2.1; Table 2.2 is eight pages later
LISTS = ("srs", "spo", "sps", "dos", "ssj", "dsp", "jul", "ssdp")  # in the order of the report's list of lists
COLUMNS = {
    "2.1L": ("registered",),
    "2.1R": ("voters", "voters_pct", "received", "unused", "used", "invalid", "invalid_pct", "valid", "valid_pct"),
    "2.2L": ("valid2", "srs", "srs_pct", "spo", "spo_pct", "sps", "sps_pct"),
    "2.2R": ("dos", "dos_pct", "ssj", "ssj_pct", "dsp", "dsp_pct", "jul", "jul_pct", "ssdp", "ssdp_pct"),
}
STORED = ("registered", "voters", "used", "invalid", "valid", *LISTS)

REPUBLIC = "Република Србија"
REGIONS = ("Централна Србија", "Војводина", "Косово и Метохија")

# the districts in the order the book prints them, and each one's municipalities
OKRUZI: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (name, tuple(municipalities.split("|")))
    for name, municipalities in (
        (
            "Град Београд",
            (
                "Барајево|Вождовац|Врачар|Гроцка|Звездара|Земун|Лазаревац|Младеновац|Нови Београд|Обреновац|Палилула|"
                "Раковица|Савски Венац|Сопот|Стари Град|Чукарица"
            ),
        ),
        ("Севернобачки округ", "Бачка Топола|Мали Иђош|Суботица"),
        ("Средњобанатски округ", "Житиште|Зрењанин|Нова Црња|Нови Бечеј|Сечањ"),
        ("Севернобанатски округ", "Ада|Кањижа|Кикинда|Нови Кнежевац|Сента|Чока"),
        ("Јужнобанатски округ", "Алибунар|Бела Црква|Вршац|Ковачица|Ковин|Опово|Панчево|Пландиште"),
        ("Западнобачки округ", "Апатин|Кула|Оџаци|Сомбор"),
        (
            "Јужнобачки округ",
            (
                "Бач|Бачка Паланка|Бачки Петровац|Беочин|Бечеј|Жабаљ|Нови Сад|Србобран|Сремски Карловци|Темерин|Тител|"
                "Врбас"
            ),
        ),
        ("Сремски округ", "Инђија|Ириг|Пећинци|Рума|Сремска Митровица|Стара Пазова|Шид"),
        ("Мачвански округ", "Богатић|Владимирци|Коцељева|Крупањ|Лозница|Љубовија|Мали Зворник|Шабац"),
        ("Колубарски округ", "Ваљево|Лајковац|Љиг|Мионица|Осечина|Уб"),
        ("Подунавски округ", "Велика Плана|Смедерево|Смедеревска Паланка"),
        (
            "Браничевски округ",
            "Велико Градиште|Голубац|Жабари|Жагубица|Кучево|Мало Црниће|Петровац|Пожаревац",
        ),
        ("Шумадијски округ", "Аранђеловац|Баточина|Кнић|Крагујевац|Рача|Топола|Лапово"),
        ("Поморавски округ", "Деспотовац|Параћин|Рековац|Јагодина|Свилајнац|Ћуприја"),
        ("Борски округ", "Бор|Кладово|Мајданпек|Неготин"),
        ("Зајечарски округ", "Бољевац|Зајечар|Књажевац|Сокобања"),
        (
            "Златиборски округ",
            "Ариље|Бајина Башта|Косјерић|Нова Варош|Пожега|Прибој|Пријепоље|Сјеница|Ужице|Чајетина",
        ),
        ("Моравички округ", "Горњи Милановац|Ивањица|Лучани|Чачак"),
        ("Рашки округ", "Врњачка Бања|Краљево|Нови Пазар|Рашка|Тутин"),
        ("Расински округ", "Александровац|Брус|Варварин|Крушевац|Трстеник|Ћићевац"),
        ("Нишавски округ", "Алексинац|Гаџин Хан|Дољевац|Мерошина|Ниш|Ражањ|Сврљиг|Нишка Бања"),
        ("Топлички округ", "Блаце|Житорађа|Куршумлија|Прокупље"),
        ("Пиротски округ", "Бабушница|Бела Паланка|Димитровград|Пирот"),
        ("Јабланички округ", "Бојник|Власотинце|Лебане|Лесковац|Медвеђа|Црна Трава"),
        ("Пчињски округ", "Босилеград|Бујановац|Владичин Хан|Врање|Прешево|Сурдулица|Трговиште"),
        ("Косовски округ", "Косово Поље|Липљан|Обилић|Приштина|Штрпце"),
        ("Пећки округ", "Исток|Пећ"),
        ("Косовскомитровачки округ", "Вучитрн|Зубин Поток|Лепосавић|Србица|Косовска Митровица|Звечан"),
    )
)
# the districts of each region, by position in OKRUZI: Belgrade and Central Serbia, Vojvodina, Kosovo and Metohija
REGION_OKRUZI = ((0, *range(8, 25)), tuple(range(1, 8)), (25, 26, 27))
# a municipality the older books do not print, with its code in the Commission's register
EXTRA_CODES = {"Нишка Бања": "71285"}

# Cells the OCR dropped or misread, by row (0 is the Republic) and column, each read from the page and proven by the
# row's own sums and printed percentages: a missing list's votes are what is left of the valid ballots, and the
# printed percentage of voters agrees with it.
FIXES: dict[int, dict[str, int]] = {
    22: {"ssdp": 81},
    56: {"jul": 44},
    61: {"jul": 7},
    68: {"ssdp": 46},
    123: {"ssdp": 77},
    137: {"jul": 11},
    180: {"jul": 8},
    181: {"ssdp": 5},
    187: {"invalid": 77, "dsp": 4},
    191: {"jul": 2, "ssdp": 3},
    192: {"dsp": 9, "jul": 4, "ssdp": 2},
    193: {"dsp": 4, "jul": 2},
    194: {"jul": 3, "ssdp": 8},
    195: {"jul": 5},
    196: {"dsp": 0, "jul": 0, "ssdp": 1},
    197: {"invalid": 2, "srs": 8, "spo": 1, "ssj": 0, "dsp": 0, "jul": 0, "ssdp": 0},
    198: {"invalid": 9, "dsp": 0, "jul": 0, "ssdp": 1},
    200: {"jul": 4, "ssdp": 1},
    201: {"dsp": 6, "ssdp": 2},
    203: {"invalid": 2, "srs": 7, "ssj": 8, "dsp": 1, "jul": 1, "ssdp": 0},
    205: {"ssdp": 9},
}

Cell = tuple[int, int, float, float, float, float]  # value, page, x0, y0, x1, y1 in points


def _number(text: str) -> int | None:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _rows(words: list[list[Any]]) -> list[list[list[Any]]]:
    """The rows of numbers of a page, each left to right; a row has at least three numbers."""
    figures = sorted(
        (w for w in words if re.fullmatch(r"[\d.,]+", str(w[4]).strip()) and 110 < w[1] < 770 and w[0] > 60),
        key=lambda w: (w[1] + w[3]) / 2,
    )
    out: list[tuple[float, list[list[Any]]]] = []
    for w in figures:
        y = (w[1] + w[3]) / 2
        if out and y - out[-1][0] <= 3:
            out[-1][1].append(w)
        else:
            out.append((y, [w]))
    return [sorted(ws, key=lambda w: w[0]) for _, ws in out if len(ws) >= 3]


@dataclass
class Row:
    cells: dict[str, Cell] = field(default_factory=dict)  # the stored figures and valid2, with their boxes
    printed: dict[str, int] = field(default_factory=dict)  # the printed percentages, in hundredths
    top: float = 0.0
    bottom: float = 0.0


def read(pages: dict[int, list[list[Any]]]) -> tuple[list[Row], dict[str, tuple[int, float]]]:
    """Every row of the two tables, and the page and middle of each column of the spread that holds the last row.

    A cell the OCR did not find is absent; ``FIXES`` supplies it.
    """
    out: list[Row] = []
    place: dict[str, tuple[int, float]] = {}
    for left in SPREADS:
        spread = {"2.1L": left, "2.1R": left + 1, "2.2L": left + 8, "2.2R": left + 9}
        found = {k: _rows(pages[p]) for k, p in spread.items()}
        count = len(found["2.1L"])
        if any(len(v) != count for v in found.values()):
            raise ValueError(f"page {left}: the halves of the table have {[len(v) for v in found.values()]} rows")
        centers: dict[str, list[float]] = {}
        for k, names in COLUMNS.items():
            whole = [r for r in found[k] if len(r) == len(names)] if k != "2.1L" else [r[-1:] for r in found[k]]
            centers[k] = [statistics.median((r[i][0] + r[i][2]) / 2 for r in whole) for i in range(len(names))]
            for name, x in zip(names, centers[k], strict=True):
                place[f"{spread[k]}:{name}"] = (spread[k], x)
        for i in range(count):
            row = Row(
                top=min(w[1] for k in found for w in found[k][i]), bottom=max(w[3] for k in found for w in found[k][i])
            )
            for k, names in COLUMNS.items():
                for w in found[k][i][-1:] if k == "2.1L" else found[k][i]:
                    x = (w[0] + w[2]) / 2
                    j = min(range(len(names)), key=lambda j: abs(centers[k][j] - x))
                    value = _number(str(w[4]))
                    if value is None or abs(centers[k][j] - x) >= 22:
                        continue
                    if names[j].endswith("_pct"):
                        row.printed[names[j]] = value
                    else:
                        row.cells.setdefault(names[j], (value, spread[k], w[0], w[1], w[2], w[3]))
            out.append(row)
            row.cells["_spread"] = (left, left, 0, 0, 0, 0)
    return out, place


def _layout() -> list[tuple[str, str, int | None]]:
    """The kind, name and district of each of the 206 rows, in the order the book prints them."""
    rows = [("republic", REPUBLIC, None)]
    rows += [("region", name, None) for name in REGIONS]
    for n, (name, municipalities) in enumerate(OKRUZI):
        rows.append(("okrug", name, n))
        rows += [("muni", m, n) for m in municipalities]
    return rows


def _column(key: str) -> str:
    return (
        "2.1R"
        if key in ("voters", "used", "invalid", "valid")
        else "2.1L"
        if key == "registered"
        else ("2.2L" if key in ("valid2", "srs", "spo", "sps") else "2.2R")
    )


def rows(pages: dict[int, list[list[Any]]]) -> tuple[list[dict[str, Any]], list[str]]:
    """The table's rows with names, and what the book's own arithmetic finds wrong."""
    layout = _layout()
    read_rows, place = read(pages)
    if len(read_rows) != len(layout):
        raise ValueError(f"{len(read_rows)} rows read, {len(layout)} printed")
    problems: list[str] = []
    out: list[dict[str, Any]] = []
    for n, ((kind, name, okrug), row) in enumerate(zip(layout, read_rows, strict=True)):
        left = row.cells.pop("_spread")[0]
        for key, value in FIXES.get(n, {}).items():
            if key in row.cells:
                row.cells[key] = (value, *row.cells[key][1:])
            else:
                shift = {"2.1L": 0, "2.1R": 1, "2.2L": 8, "2.2R": 9}[_column(key)]
                page, x = place[f"{left + shift}:{key}"]
                row.cells[key] = (value, page, x - 18, row.top, x + 18, row.bottom)
        absent = [k for k in (*STORED, "valid2") if k not in row.cells]
        if absent:
            problems.append(f"{n} {name}: no {absent}")
            continue
        problems += _check(n, name, kind, {k: c[0] for k, c in row.cells.items()}, row.printed)
        out.append({"kind": kind, "name": name, "okrug": okrug, "cells": {k: list(row.cells[k]) for k in STORED}})
    if not problems:
        problems += _sums(out)
    return out, problems


def _close(count: int, base: int, printed: int | None) -> bool:
    """Whether a printed percentage, in hundredths, is the count's share of its base, rounded or cut off."""
    return printed is None or base == 0 or abs(10000 * count / base - printed) <= 1.01


def _check(n: int, name: str, kind: str, v: dict[str, int], printed: dict[str, int]) -> list[str]:
    found = []
    if v["used"] != v["invalid"] + v["valid"]:
        found.append("the ballots used are not the invalid and valid ballots")
    if v["valid"] != v["valid2"]:
        found.append("Table 2.1 and Table 2.2 print different valid ballots")
    if sum(v[k] for k in LISTS) != v["valid"]:
        found.append(f"the lists' votes add up to {sum(v[k] for k in LISTS)}, not {v['valid']}")
    shares = [("voters", "registered"), ("invalid", "used"), ("valid", "used"), *((k, "voters") for k in LISTS)]
    for key, base in shares:
        if not _close(v[key], v[base], printed.get(f"{key}_pct")):
            found.append(f"{key} {v[key]} of {v[base]} is not the printed {printed.get(f'{key}_pct')}")
    return [f"{n} {name}: {f}" for f in found]


def _sums(table: list[dict[str, Any]]) -> list[str]:
    """Whether each district's municipalities add up to the district, and the districts to the regions and Republic."""
    problems = []
    counts = {n: [r for r in table if r["okrug"] == n and r["kind"] == "muni"] for n in range(len(OKRUZI))}
    heads = {n: r for r in table for n in [r["okrug"]] if r["kind"] == "okrug"}
    for key in STORED:
        for n, members in counts.items():
            total = sum(r["cells"][key][0] for r in members)
            if total != heads[n]["cells"][key][0]:
                problems.append(
                    f"{OKRUZI[n][0]} {key}: municipalities add up to {total}, not {heads[n]['cells'][key][0]}"
                )
        regions = [r for r in table if r["kind"] == "region"]
        for region, members in zip(regions, REGION_OKRUZI, strict=True):
            total = sum(heads[n]["cells"][key][0] for n in members)
            if total != region["cells"][key][0]:
                problems.append(f"{region['name']} {key}: districts add up to {total}, not {region['cells'][key][0]}")
        republic = table[0]["cells"][key][0]
        if sum(r["cells"][key][0] for r in table if r["kind"] == "region") != republic:
            problems.append(f"{key}: regions do not add up to the Republic's {republic}")
    return problems


def extract(source: Path | None = None) -> None:
    """Read the scanned tables again and write ``rik_poslanici_2000.json``; give a file of ``rik_scans.ocr`` to reuse."""
    path = source or Path("rik_scans_2000.json")
    if source is None:
        rik_scans.ocr("2000", path, PAGES)
    pages = {int(k): v for k, v in json.loads(path.read_text()).items()}
    table, problems = rows(pages)
    print(len(table), "rows")
    for problem in problems:
        print("  ", problem)
    if not problems:
        DATA_FILE.write_text(json.dumps(table, ensure_ascii=False, separators=(",", ":")))


def code_of(name: str, okrug: int) -> str | None:
    """The register's code of a municipality the book prints, or None for one the register lacks."""
    from ea.sources.rs import rik_predsednik

    full = f"Београд-{name}" if okrug == 0 else name
    return rik_predsednik.CODES.get(full) or EXTRA_CODES.get(name)


@cache
def data() -> list[dict[str, Any]]:
    return json.loads(DATA_FILE.read_text())
