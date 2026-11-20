"""Russia's federal elections of 1993 to 1999 from the Central Election Commission's electoral archive.

The Commission's archive pages (cikrf.ru/banners/vib_arhiv/) publish, for each federal vote of the 1990s it still
lists, its summary tables and, from 1995, workbooks of the totals of every territorial election commission (TIK):

- 12 December 1993: the vote on the draft Constitution, the State Duma's federal list vote, and the Duma's and the
  Federation Council's district results, as HTML tables by federal subject or district. The district tables name only
  the elected deputies and their votes.
- 17 December 1995 and 19 December 1999: the State Duma's protocol No. 2 (federal lists) by single-member district,
  and the votes for every list and every district candidate by TIK within each district.
- 16 June and 3 July 1996: the presidential election's subject protocols, and the candidates' votes by TIK.

The site no longer answers scripted requests, so every file comes from a Wayback Machine capture of the archive, read
from the capture by the archive's own machines and added here with `raw.store`. The TIK workbooks hold only the votes
for each option; turnout and ballot accounting exist only at the subject or district level, so each contest with both
grains gets a default set at the coarser grain, which holds the protocol and the outcomes, and a second set by TIK.

Not here: the 12 June 1991 RSFSR presidential election and the 25 April 1993 referendum, which the archive pages do
not carry, and the candidates elected from the federal lists in 1995 and 1999.
"""

import functools
import html
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from uuid import uuid5

import shapely
import shapely.geometry
import xlrd

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "ru.cik"
RUSSIA = str(uuid5(package.NAMESPACE, "polity\x1fRU"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fRUS"))
INBOX = DATA / "inbox" / "manual" / SOURCE
BROWSER = "browser://20261120-004059-fb6df4/"
ARCHIVE = "http://www.cikrf.ru/banners/vib_arhiv/"
DERIVED = 1
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/RUS/ADM1/"
LAYER = f"{GEOBOUNDARIES}geoBoundaries-RUS-ADM1_simplified.geojson"
META = f"{GEOBOUNDARIES}geoBoundaries-RUS-ADM1-metaData.json"
# Subjects whose territory changed when six autonomous okrugs merged into their neighbors from 2005 to 2008: the
# layer's 2017 outlines draw the merged subjects, so these are left without an outline.
MERGED = {"AGB", "UOB", "KOP", "KOR", "TAY", "EVE", "PER", "KYA", "KAM", "IRK", "CHI"}
GRID = 1e-6

SUBJECT = "ru.subject"
OKRUG = "ru.gd_okrug"
TIK = "ru.tik"

DAY_1993 = date(1993, 12, 12)
DAY_1995 = date(1995, 12, 17)
DAY_1996 = (date(1996, 6, 16), date(1996, 7, 3))
DAY_1999 = date(1999, 12, 19)
# The single-member district plans: each was drawn for one election and held until the next plan.
PLANS = {1993: (DAY_1993, DAY_1995), 1995: (DAY_1995, DAY_1999), 1999: (DAY_1999, date(2003, 12, 7))}


def file(name: str) -> str:
    return BROWSER + name


REFERENDUM_1993 = file("ru-referendum-1993-12-12-constitution-results.html")
DUMA_1993 = file("ru-duma-1993-12-12-results.html")
COUNCIL_1993 = file("ru-sovfed-1993-12-12-results.html")
PROTOCOL = {1995: file("1995-Protokol_CIK.doc"), 1999: file("1999-Protokol_CIK.doc")}
DUMA_LISTS = {1995: file("1995-Svodnaya_CIK.xls"), 1999: file("1999-Svodnaya_CIK.xls")}
DUMA_TIK_DISTRICT = {1995: file("1995-1-TIK-usech.xls"), 1999: file("1999-1-TIK-usech.xls")}
DUMA_TIK_LIST = {1995: file("1995-2-TIK-usech.xls"), 1999: file("1999-2-TIK-usech.xls")}
PRESIDENT_SUBJECTS = (file("1996-1-Svodnaya_CIK.xls"), file("1996-2-Svodnaya_CIK.xls"))
PRESIDENT_TIK = (file("1996-1-TIK-usech.xls"), file("1996-2-TIK-usech.xls"))
PRESIDENT_PROTOCOL = (file("1996-1-Protokol_CIK.doc"), file("1996-2-Protokol_CIK.doc"))

# The seats each federal list won, as the Commission declared them: for 1995 and 1999 in its protocol of the
# federal district's results, and for 1993 in its list of the deputies elected in the federal district.
LIST_SEATS: dict[int, dict[str, int]] = {
    1993: {
        "Либерально-демократическая партия России": 59,
        "Выбор России": 40,
        "Коммунистическая партия Российской Федерации": 32,
        'Политическое движение "Женщины России"': 21,
        "Аграрная партия России": 21,
        "Блок: Явлинский - Болдырев - Лукин": 20,
        "Партия Российского Единства и Согласия": 18,
        "Демократическая партия России": 14,
    },
    1995: {"17": 45, "19": 31, "25": 99, "33": 50},
    1999: {"6": 16, "14": 64, "17": 17, "19": 37, "20": 67, "23": 24},
}
# Cells the Commission's 1993 list table misprints, by subject and list, with the figure its own totals prove: the
# Moscow cell for Russia's Choice drops a digit (114276), while the share printed beneath it (34.73%) and the national
# total (8,339,345, which the other subjects' cells fall short of by exactly 1,028,486) both give 1,142,762.
CORRECTIONS_1993 = {("MOW", "Выбор России"): (114276, 1142762)}
TULEEV = "ТУЛЕЕВ Аман-Гельды Молдагазыевич"


@dataclass(frozen=True)
class Subject:
    iso: str
    name: str
    stems: tuple[str, ...]


# The 89 federal subjects of 1993 to 1999, in the Constitution's order, with ISO 3166-2 codes (those of the six
# autonomous okrugs merged into their neighbors from 2005 to 2008 are the codes ISO withdrew). The stems pick a
# subject out of any of the spellings the Commission's tables use; the more specific ones are tried first.
SUBJECTS = [
    Subject("AD", "Республика Адыгея (Адыгея)", ("адыге",)),
    Subject("AL", "Республика Алтай", ("республикаалтай", "горноалтай")),
    Subject("BA", "Республика Башкортостан", ("башк",)),
    Subject("BU", "Республика Бурятия", ("республикабурят", "бурятск")),
    Subject("DA", "Республика Дагестан", ("дагест",)),
    Subject("IN", "Республика Ингушетия", ("ингуш",)),
    Subject("KB", "Кабардино-Балкарская Республика", ("кабардин",)),
    Subject("KL", "Республика Калмыкия", ("калмык",)),
    Subject("KC", "Карачаево-Черкесская Республика", ("карачаев",)),
    Subject("KR", "Республика Карелия", ("карел",)),
    Subject("KO", "Республика Коми", ("республикакоми", "коми")),
    Subject("ME", "Республика Марий Эл", ("марий",)),
    Subject("MO", "Республика Мордовия", ("мордов",)),
    Subject("SA", "Республика Саха (Якутия)", ("саха", "якут")),
    Subject("SE", "Республика Северная Осетия — Алания", ("осети",)),
    Subject("TA", "Республика Татарстан (Татарстан)", ("татарст",)),
    Subject("TY", "Республика Тыва", ("тыва", "тува")),
    Subject("UD", "Удмуртская Республика", ("удмурт",)),
    Subject("KK", "Республика Хакасия", ("хакас",)),
    Subject("CE", "Чеченская Республика", ("чечен",)),
    Subject("CU", "Чувашская Республика — Чăваш Республики", ("чуваш",)),
    Subject("ALT", "Алтайский край", ("алтайскийкрай", "алтайскийкр")),
    Subject("KDA", "Краснодарский край", ("краснодар",)),
    Subject("KYA", "Красноярский край", ("красноярск",)),
    Subject("PRI", "Приморский край", ("примор",)),
    Subject("STA", "Ставропольский край", ("ставропол",)),
    Subject("KHA", "Хабаровский край", ("хабаровск",)),
    Subject("AMU", "Амурская область", ("амурск",)),
    Subject("ARK", "Архангельская область", ("архангел",)),
    Subject("AST", "Астраханская область", ("астрахан",)),
    Subject("BEL", "Белгородская область", ("белгород",)),
    Subject("BRY", "Брянская область", ("брянск",)),
    Subject("VLA", "Владимирская область", ("владимир",)),
    Subject("VGG", "Волгоградская область", ("волгоград",)),
    Subject("VLG", "Вологодская область", ("вологод",)),
    Subject("VOR", "Воронежская область", ("воронеж",)),
    Subject("IVA", "Ивановская область", ("иванов",)),
    Subject("IRK", "Иркутская область", ("иркутск",)),
    Subject("KGD", "Калининградская область", ("калининград",)),
    Subject("KLU", "Калужская область", ("калуж",)),
    Subject("KAM", "Камчатская область", ("камчат",)),
    Subject("KEM", "Кемеровская область", ("кемеров",)),
    Subject("KIR", "Кировская область", ("кировск",)),
    Subject("KOS", "Костромская область", ("костром",)),
    Subject("KGN", "Курганская область", ("курган",)),
    Subject("KRS", "Курская область", ("курск",)),
    Subject("LEN", "Ленинградская область", ("ленинградск",)),
    Subject("LIP", "Липецкая область", ("липецк",)),
    Subject("MAG", "Магаданская область", ("магадан",)),
    Subject("MOS", "Московская область", ("московскаяобл", "московскаяо")),
    Subject("MUR", "Мурманская область", ("мурман",)),
    Subject("NIZ", "Нижегородская область", ("нижегород",)),
    Subject("NGR", "Новгородская область", ("новгород",)),
    Subject("NVS", "Новосибирская область", ("новосибир",)),
    Subject("OMS", "Омская область", ("омская", "омскаяо")),
    Subject("ORE", "Оренбургская область", ("оренбург",)),
    Subject("ORL", "Орловская область", ("орлов",)),
    Subject("PNZ", "Пензенская область", ("пенз",)),
    Subject("PER", "Пермская область", ("пермская", "пермск")),
    Subject("PSK", "Псковская область", ("псков",)),
    Subject("ROS", "Ростовская область", ("ростов",)),
    Subject("RYA", "Рязанская область", ("рязан",)),
    Subject("SAM", "Самарская область", ("самар",)),
    Subject("SAR", "Саратовская область", ("саратов",)),
    Subject("SAK", "Сахалинская область", ("сахалин",)),
    Subject("SVE", "Свердловская область", ("свердлов",)),
    Subject("SMO", "Смоленская область", ("смолен",)),
    Subject("TAM", "Тамбовская область", ("тамбов",)),
    Subject("TVE", "Тверская область", ("тверск",)),
    Subject("TOM", "Томская область", ("томск",)),
    Subject("TUL", "Тульская область", ("тульск",)),
    Subject("TYU", "Тюменская область", ("тюмен",)),
    Subject("ULY", "Ульяновская область", ("ульянов",)),
    Subject("CHE", "Челябинская область", ("челябинск",)),
    Subject("CHI", "Читинская область", ("читинск",)),
    Subject("YAR", "Ярославская область", ("ярослав",)),
    Subject("MOW", "Москва", ("москва",)),
    Subject("SPE", "Санкт-Петербург", ("петербург",)),
    Subject("YEV", "Еврейская автономная область", ("еврейск",)),
    Subject("AGB", "Агинский Бурятский автономный округ", ("агинск",)),
    Subject("KOP", "Коми-Пермяцкий автономный округ", ("комипермяц",)),
    Subject("KOR", "Корякский автономный округ", ("коряк",)),
    Subject("NEN", "Ненецкий автономный округ", ("ненецкийавт", "ненецкийао")),
    Subject("TAY", "Таймырский (Долгано-Ненецкий) автономный округ", ("таймыр",)),
    Subject("UOB", "Усть-Ордынский Бурятский автономный округ", ("устьордын",)),
    Subject("KHM", "Ханты-Мансийский автономный округ", ("хантыманс",)),
    Subject("CHU", "Чукотский автономный округ", ("чукот",)),
    Subject("EVE", "Эвенкийский автономный округ", ("эвенк",)),
    Subject("YAN", "Ямало-Ненецкий автономный округ", ("ямал",)),
]
ORDER = ["SAK", "AGB", "UOB", "KOP", "YAN", "TAY", "NEN", "KHM", "CHU", "EVE", "YEV", "ALT", "TOM", "MOS", "LEN"]
ABROAD = "abroad"


def _fold(text: str) -> str:
    return re.sub(r"[^а-я]", "", text.lower().replace("ё", "е"))


def subject(text: str) -> str:
    """The ISO 3166-2 suffix of the subject a table names, or ABROAD for the polling stations outside Russia."""
    folded = _fold(text)
    if "запределами" in folded:
        return ABROAD
    first = [s for s in SUBJECTS if s.iso in ORDER]
    first.sort(key=lambda s: ORDER.index(s.iso))
    for candidate in [*first, *(s for s in SUBJECTS if s.iso not in ORDER)]:
        if any(stem in folded for stem in candidate.stems):
            return candidate.iso
    raise LookupError(f"no federal subject matches {text!r}")


NAMES = {s.iso: s.name for s in SUBJECTS}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def subject_area(iso: str) -> str:
    return key("area", SUBJECT, iso)


def okrug_area(plan: int, number: int) -> str:
    return key("area", OKRUG, plan, number)


CIK = key("source", SOURCE)
SHAPES = key("source", "geoboundaries")
DUMA = key("body", "duma")
COUNCIL = key("body", "council")
PRESIDENCY = key("office", "president")
LIST_SYSTEM = key("electoral_system", "duma", "list")
DISTRICT_SYSTEM = key("electoral_system", "duma", "district")
COUNCIL_SYSTEM = key("electoral_system", "council", 1993)
PRESIDENT_SYSTEM = key("electoral_system", "president", 1996)
REFERENDUM_SYSTEM = key("electoral_system", "referendum")

# ----------------------------------------------------------------------------------------------------------------
# Raw files


def fetch() -> None:
    """Add the files the archive's machines read from the Commission's archive pages to the raw cache.

    They sit in the manual inbox with the listing that maps each address to its file; the Commission's hosts refuse
    scripted requests, so nothing is fetched directly.
    """
    raw.fetch(SOURCE, LAYER, suffix=".geojson")
    raw.fetch(SOURCE, META, suffix=".json")
    index = json.loads((INBOX / "index.json").read_text())
    for url, entry in index.items():
        content = (INBOX / entry["file"]).read_bytes()
        suffix = Path(entry["file"]).suffix
        raw.store(
            SOURCE,
            entry.get("source", url),
            content,
            suffix=suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


def path(url: str) -> Path:
    return raw.cached(SOURCE, url).path


# ----------------------------------------------------------------------------------------------------------------
# Readers


def number(value: object) -> int | None:
    if isinstance(value, float):
        if value != int(value):
            raise ValueError(f"{value} is not a whole number")
        return int(value)
    text = str(value).replace(" ", "").replace("\xa0", "").strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


def clean(text: object) -> str:
    return " ".join(str(text).replace("\xad", "").split())


Label = tuple[str, int]


def label(value: object) -> Label | None:
    """A protocol line or ballot number as a workbook prints it: a number cell, or text such as '13.' or '13'."""
    if isinstance(value, float):
        return ("N", int(value))
    text = str(value).strip().rstrip(".")
    return ("S", int(text)) if re.fullmatch(r"\d+", text) else None


@dataclass
class Block:
    subject: str
    title: str
    names: dict[Label, str] = field(default_factory=dict)
    total: dict[Label, int] = field(default_factory=dict)
    tiks: dict[str, dict[Label, int]] = field(default_factory=dict)
    winner: str | None = None
    voters: int | None = None


SKIP_TITLES = ("ВЫБОРЫ", "ИТОГИ", "ТЕРРИТОРИАЛЬН", "ТИК")


def tik_blocks(url: str) -> list[Block]:
    """The district or subject blocks of a TIK workbook, each with its total column and its TIK columns.

    A sheet holds one or more blocks side by side, each starting with a column of line numbers; a block that runs over
    several printed pages repeats its title and, in the 1999 workbooks, its line numbers, so blocks with one title are
    joined.
    """
    book = xlrd.open_workbook(path(url))
    merged: dict[tuple[str, str], Block] = {}
    for sheet in book.sheets():
        if sheet.name.strip() == "Расшифровка":
            continue
        cell = sheet.cell_value
        if label(cell(4, 0)) is None or sheet.nrows < 6:
            continue
        # A block starts at a column that repeats the sheet's first two line numbers under an empty header; blocks
        # side by side can list different numbers of candidates, so only the first two lines are compared.
        starts = [
            c
            for c in range(sheet.ncols)
            if clean(cell(3, c)) in ("", "№") and cell(4, c) == cell(4, 0) and cell(5, c) == cell(5, 0)
        ]
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else sheet.ncols
            rows = [r for r in range(4, sheet.nrows) if label(cell(r, start)) is not None]
            texts = [
                clean(cell(r, c))
                for r in range(3)
                for c in (start, start + 1)
                if c < end and clean(cell(r, c)) and not clean(cell(r, c)).upper().startswith(SKIP_TITLES)
            ]
            title = texts[1] if len(texts) > 1 else texts[0]
            block = merged.setdefault((texts[0], title), Block(subject(texts[0]), title))
            names_column = None
            first = cell(rows[0], start + 1) if start + 1 < end else ""
            if isinstance(first, str) and clean(first) and number(first) is None:
                names_column = start + 1
                for r in rows:
                    block.names[label(cell(r, start)) or ("", 0)] = clean(cell(r, names_column))
                for r in range(4, sheet.nrows):
                    text = clean(cell(r, names_column))
                    if text.startswith("Депутатом"):
                        block.winner = next(
                            clean(cell(n, names_column))
                            for n in range(r + 1, sheet.nrows)
                            if clean(cell(n, names_column))
                        )
                    elif text.startswith("Число избирателей"):
                        block.voters = number(cell(r, names_column + 1))
            for c in range(start + 1, end):
                header = clean(cell(3, c))
                if c == names_column or not header or header == "№":
                    continue
                values = {
                    found: value
                    for r in rows
                    if (found := label(cell(r, start))) is not None and (value := number(cell(r, c))) is not None
                }
                if not values:
                    continue
                if header.upper() == "ИТОГО" or header.startswith("Окружная"):
                    block.total = values
                else:
                    # A few blocks print two commissions under one name, such as a town's and its district's; the
                    # second keeps its column's place as a numbered suffix.
                    name, n = header, 1
                    while name in block.tiks:
                        n += 1
                        name = f"{header} ({n})"
                    block.tiks[name] = values
    return list(merged.values())


def legend(url: str) -> dict[Label, str]:
    """The workbook's key of its line numbers."""
    sheet = xlrd.open_workbook(path(url)).sheet_by_name("Расшифровка")
    out: dict[Label, str] = {}
    for r in range(sheet.nrows):
        found = label(sheet.cell_value(r, 0))
        text = clean(sheet.cell_value(r, 1))
        if found is not None and text:
            out[found] = text
    return out


def okrug_number(title: str) -> int:
    found = re.search(r"№\s*(\d+)", title)
    if found is None:
        raise LookupError(f"no district number in {title!r}")
    return int(found.group(1))


def okrug_name(title: str) -> str:
    return clean(re.sub(r"\s*одномандатный избирательный округ.*$", "", title, flags=re.IGNORECASE))


@dataclass
class Table:
    """A summary workbook: values by column (a district number or a subject), and the rows that hold them."""

    columns: dict[str, int]
    total: int | None
    lines: dict[int, int]
    options: list[tuple[int, str, int]]
    against: int | None
    cell: object


def okrug_table(url: str) -> Table:
    sheet = xlrd.open_workbook(path(url)).sheet_by_index(0)
    cell = sheet.cell_value
    header = next(r for r in range(sheet.nrows) if "избирательных объединений" in clean(cell(r, 1)))
    numbers = next(r for r in range(header) if sum(isinstance(cell(r, c), float) for c in range(sheet.ncols)) > 100)
    columns = {str(int(v)): c for c in range(2, sheet.ncols) if isinstance(v := cell(numbers, c), float)}
    total = next((c for c in range(sheet.ncols) for r in range(numbers + 1) if clean(cell(r, c)) == "Итого"), None)
    lines: dict[int, int] = {}
    for r in range(numbers + 1, header):
        found = label(cell(r, 0))
        if found is not None and found[1] not in lines:
            lines[found[1]] = r
    options: list[tuple[int, str, int]] = []
    against = None
    pending: tuple[int, str] | None = None
    first = next(iter(columns.values()))
    for r in range(header + 1, sheet.nrows):
        found = label(cell(r, 0))
        text = clean(cell(r, 1))
        if found is None and not text:
            continue
        filled = number(cell(r, first)) is not None
        if "против всех" in text:
            against = r
            continue
        if found is not None and text:
            pending = (found[1], text)
        elif pending is not None and text and found is None:
            pending = (pending[0], f"{pending[1]} {text}")
        if pending is not None and filled:
            ballot, name = pending
            prefix = re.match(r"(\d+)\.\s*(.*)$", name)
            if prefix:
                ballot, name = int(prefix.group(1)), prefix.group(2)
            options.append((ballot, name, r))
            pending = None
    return Table(columns, total, lines, options, against, cell)


def subject_table(url: str) -> tuple[dict[str, int], int, dict[str, int], object]:
    """The 1996 subject workbook: its subject columns, its total column, and its rows by key."""
    sheet = xlrd.open_workbook(path(url)).sheet_by_index(0)
    cell = sheet.cell_value
    columns: dict[str, int] = {}
    total = 0
    for c in range(sheet.ncols):
        text = clean(cell(5, c))
        if text.startswith("Итого"):
            total = c
        elif text and not text.startswith("Номера") and number(text) is None:
            if subject(text) in columns:
                raise ValueError(f"{url}: two columns for {subject(text)}")
            columns[subject(text)] = c
    rows: dict[str, int] = {}
    for r in range(6, sheet.nrows):
        code = str(cell(r, 0)).strip()
        text = clean(cell(r, 1))
        if re.fullmatch(r"\d+", code):
            rows[f"line {code}"] = r
        elif re.fullmatch(r"\d+\.", code):
            rows[text] = r
        elif "Тулеева" in text:
            rows[TULEEV] = r
    return columns, total, rows, cell


def pages(url: str) -> dict[str, str]:
    """The pages a saved bundle joins, by the address each was read from."""
    data = path(url).read_bytes()
    parts = re.split(rb"<!-- SOURCE: (\S+) ", data)
    out: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        body = parts[i + 1]
        encoding = "cp1251" if b"windows-1251" in body[:3000] else "utf-8"
        out[parts[i].decode()] = body.decode(encoding)
    return out


def html_rows(text: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"<tr\b.*?</tr>", text, flags=re.DOTALL | re.IGNORECASE):
        cells = re.findall(r"<t[dh]\b.*?</t[dh]>", tr, flags=re.DOTALL | re.IGNORECASE)
        rows.append([clean(html.unescape(re.sub(r"<[^>]+>", " ", c))) for c in cells])
    return rows


def page(url: str, name: str) -> list[list[str]]:
    return html_rows(next(body for address, body in pages(url).items() if address.endswith(name)))


def person_name(printed: str) -> str:
    """A name the tables print as SURNAME Given Patronymic, with the surname in sentence case."""
    parts = printed.split(" ", 1)
    surname = "-".join(part.capitalize() for part in parts[0].split("-"))
    return f"{surname} {parts[1]}" if len(parts) > 1 else surname


def party_key(name: str) -> str:
    text = re.sub(r"\s*\([А-ЯЁ]{2,6}\)\s*\"?$", "", name.strip())
    return re.sub(r"[^а-я0-9a-z]", "", text.lower().replace("ё", "е"))


def party(name: str) -> str:
    return key("party", party_key(name))


# ----------------------------------------------------------------------------------------------------------------
# Districts


def okrugs_1993() -> dict[int, tuple[str, str]]:
    """The 1993 single-member districts: number, name, and subject, from the district results page."""
    out: dict[int, tuple[str, str]] = {}
    current = ""
    for row in page(DUMA_1993, "1993_itogi_FS_GD.html")[1:]:
        if len(row) == 1 and row[0] and row[0] != ".":
            current = subject(row[0])
        elif len(row) >= 2 and number(row[0]) is not None and row[1] != "-":
            out[int(row[0])] = (row[1], current)
    return out


def okrugs(year: int) -> dict[int, tuple[str, str]]:
    if year == 1993:
        return okrugs_1993()
    out: dict[int, tuple[str, str]] = {}
    for block in tik_blocks(DUMA_TIK_DISTRICT[year]):
        out[okrug_number(block.title)] = (okrug_name(block.title), block.subject)
    return out


# ----------------------------------------------------------------------------------------------------------------
# Packages


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        f"{SOURCE}/1993-12-12",
        f"{SOURCE}/duma-1995",
        f"{SOURCE}/president-1996",
        f"{SOURCE}/duma-1999",
    ]


def parse() -> None:
    geo().write()
    registry().write()
    vote_1993().write()
    duma(1995).write()
    president().write()
    duma(1999).write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=CIK,
        kind="official_body",
        publisher="Центральная избирательная комиссия Российской Федерации",
        title="Электоральная статистика: архив выборов и референдумов",
        url=ARCHIVE,
        license="Official election results of a state body; not subject to copyright (Civil Code of the Russian "
        "Federation, art. 1259(6))",
        license_url="https://www.consultant.ru/document/cons_doc_LAW_64629/",
        attribution="Центральная избирательная комиссия Российской Федерации",
        notes="Read from Wayback Machine captures of the Commission's archive pages, which no longer answer "
        "scripted requests.",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Russia's 89 federal subjects of 1993 to 1999 and the State Duma's single-member districts of 1993, 1995, "
        "and 1999, from the Central Election Commission's results",
    )
    p.external |= {RUSSIA, TERRITORY}
    _source(p)
    for url in (DUMA_1993, DUMA_TIK_DISTRICT[1995], DUMA_TIK_DISTRICT[1999]):
        p.artifact(raw.cached(SOURCE, url), CIK)
    for code, kind, rank, noun, system in (
        (SUBJECT, "administrative", 1, "federal subject", "iso_3166_2"),
        (OKRUG, "electoral_district", 2, "single-member district", None),
        (TIK, "precinct", 3, "territorial election commission", None),
    ):
        p.add("scheme", code=code, polity_id=RUSSIA, kind=kind, rank=rank, noun=noun, code_system=system)
    p.add(
        "source",
        id=SHAPES,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen Russia ADM1, 2017 (simplified)",
        url="https://www.geoboundaries.org/",
        license="Open Data Commons Open Database License 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab); © OpenStreetMap contributors",
        notes="OpenStreetMap boundaries of 2017 as geoBoundaries releases them, used for the federal subjects whose "
        "territory has not changed since 1993.",
    )
    p.artifact(raw.cached(SOURCE, LAYER), SHAPES)
    p.artifact(raw.cached(SOURCE, META), SHAPES)
    p.add(
        "boundary_set",
        id=key("boundary_set", SUBJECT, 1993),
        scheme_code=SUBJECT,
        vintage="1993",
        valid_from=DAY_1993,
        valid_to=None,
        as_of=DAY_1999,
    )
    shapes = outlines()
    for s in SUBJECTS:
        id = subject_area(s.iso)
        p.add("area", id=id, scheme_code=SUBJECT)
        p.add("area_code", area_id=id, code=f"RU-{s.iso}")
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, s.name, "ru", "official")
        if s.iso not in MERGED:
            _outline(p, id, shapes[s.iso])
    for plan, (since, until) in PLANS.items():
        p.add(
            "boundary_set",
            id=key("boundary_set", OKRUG, plan),
            scheme_code=OKRUG,
            vintage=str(plan),
            valid_from=since,
            valid_to=until,
            as_of=since,
        )
        for n, (name, iso) in sorted(okrugs(plan).items()):
            id = okrug_area(plan, n)
            p.add("area", id=id, scheme_code=OKRUG)
            p.add("area_code", area_id=id, code=str(n), valid_from=since, valid_to=until)
            p.add("area_parent", area_id=id, parent_id=subject_area(iso), valid_from=since, valid_to=until)
            p.name(id, f"{name} № {n}", "ru", "official")
    return p


def outlines() -> dict[str, shapely.Geometry]:
    """The layer's outlines by the ISO 3166-2 suffix it tags them with."""
    features = json.loads(raw.cached(SOURCE, LAYER).path.read_text(encoding="utf-8"))["features"]
    found: dict[str, shapely.Geometry] = {}
    for feature in features:
        iso = str(feature["properties"]["shapeISO"]).removeprefix("RU-")
        found[iso] = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
    missing = {s.iso for s in SUBJECTS} - MERGED - set(found)
    if missing:
        raise LookupError(f"the geoBoundaries layer has no outline for {sorted(missing)}")
    return found


def _outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    gridded = shapely.set_precision(shapely.make_valid(shape), GRID)
    polygons = [part for part in shapely.get_parts(gridded) if shapely.get_type_id(part) == 3 and not part.is_empty]
    outline = shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(outline, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
        ),
        quality="osm",
        source_id=SHAPES,
    )


SYSTEMS = [
    (
        LIST_SYSTEM,
        date(1993, 10, 1),
        date(2007, 1, 1),
        {
            "formula": "hare",
            "threshold": {"level": "list", "scope": "national", "percent": 5},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "Half of the State Duma's 450 deputies, 225, are elected from federal lists of parties and blocs in one "
            "nationwide district, by largest remainders with the Hare quota among the lists that win at least 5% of "
            "the votes. Votes against all lists count toward the total."
        ),
    ),
    (
        DISTRICT_SYSTEM,
        date(1993, 10, 1),
        date(2007, 1, 1),
        {"formula": "fptp"},
        (
            "The other 225 deputies are elected in single-member districts by a plurality of the votes, provided at "
            "least a quarter of the registered voters take part and the winner has more votes than the line against "
            "all candidates."
        ),
    ),
    (
        COUNCIL_SYSTEM,
        date(1993, 10, 1),
        date(1995, 12, 5),
        {"formula": "block"},
        (
            "In 1993 each federal subject formed one two-member district for the Federation Council; each voter could "
            "vote for two candidates, and the two with the most votes were elected."
        ),
    ),
    (
        PRESIDENT_SYSTEM,
        date(1991, 4, 24),
        None,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A candidate with more than half of the votes of the voters who took part is elected; otherwise the two "
            "leading candidates go to a second round. Votes against all candidates count among the voters who took part."
        ),
    ),
    (
        REFERENDUM_SYSTEM,
        date(1993, 10, 15),
        None,
        {"formula": "referendum"},
        (
            "The draft Constitution was adopted if more than half of the voters who took part voted for it and more "
            "than half of the registered voters took part."
        ),
    ),
]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The State Duma, the Federation Council, the presidency, their electoral systems, and the parties and blocs "
        "on the federal lists of 1993, 1995, and 1999, from the Central Election Commission",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(RUSSIA)
    p.add("body", id=DUMA, polity_id=RUSSIA, kind="lower_house")
    p.name(DUMA, "State Duma", "en", "official")
    p.name(DUMA, "Государственная Дума", "ru", "native", False)
    p.add("body", id=COUNCIL, polity_id=RUSSIA, kind="upper_house")
    p.name(COUNCIL, "Federation Council", "en", "official")
    p.name(COUNCIL, "Совет Федерации", "ru", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=RUSSIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Russian Federation", "en", "official")
    p.name(PRESIDENCY, "Президент Российской Федерации", "ru", "native", False)
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=RUSSIA, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    seen: set[str] = set()
    for name in list_names():
        id = party(name)
        if id in seen:
            continue
        seen.add(id)
        p.add("party", id=id, kind="bloc" if "блок" in name.lower() else "party", polity_id=RUSSIA)
        p.name(id, display(name), "ru", "official")
    return p


def display(name: str) -> str:
    """A list's name without the quotation marks the 1995 and 1999 tables wrap it in."""
    text = clean(name)
    if text.startswith('"') and text.endswith('"') and text.count('"') % 2 == 0:
        text = text[1:-1]
    return text


def list_names() -> Iterable[str]:
    for row in national_1993():
        yield row[0]
    for year in (1995, 1999):
        for _, name, _ in okrug_table(DUMA_LISTS[year]).options:
            yield name


def national_1993() -> list[tuple[str, int]]:
    rows = page(DUMA_1993, "1993_itogi_FS_obshefed_okrug.html")
    start = next(i for i, row in enumerate(rows) if row and row[0] == "Названия избирательных объединений")
    return [(row[0], int(row[1])) for row in rows[start + 1 :] if len(row) == 4]


# ----------------------------------------------------------------------------------------------------------------
# Shared builders


@dataclass
class Builder:
    p: Package
    units: set[str] = field(default_factory=set)

    def unit(self, scheme: str, code: str, relation: str, area: str | None, kind: str = "standard") -> str:
        id = key("unit", scheme, code)
        if id not in self.units:
            self.units.add(id)
            self.p.add(
                "reporting_unit",
                id=id,
                polity_id=RUSSIA,
                scheme_code=scheme,
                code=code,
                area_relation=relation,
                area_id=area,
                kind=kind,
            )
        return id

    def subject_unit(self, day: date, iso: str) -> str:
        if iso == ABROAD:
            return self.unit(SUBJECT, f"{day}:abroad", "unlocated", None, "abroad_mission")
        return self.unit(SUBJECT, f"{day}:RU-{iso}", "equals", subject_area(iso))

    def okrug_unit(self, day: date, plan: int, n: int) -> str:
        return self.unit(OKRUG, f"{day}:{n}", "equals", okrug_area(plan, n))

    def tik_unit(self, day: date, place: str, area: str | None, name: str) -> str:
        if area is None:
            return self.unit(TIK, f"{day}:{place}:{name}", "unlocated", None, "abroad_mission")
        return self.unit(TIK, f"{day}:{place}:{name}", "within", area)

    def value(
        self, result_set: str, unit: str, option: str | None, measure: int, value: int | None, flags: int = 0
    ) -> None:
        if value is None:
            return
        self.p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=flags,
            value=value,
        )

    def election(self, id: str, kind: str, day: date, slug: str, english: str, russian: str) -> str:
        self.p.add("election", id=id, polity_id=RUSSIA, kind=kind, date_from=day, date_to=day, status="held")
        self.p.add("slug", path=slug, entity_id=id, is_current=True)
        self.p.name(id, english, "en", "official")
        self.p.name(id, russian, "ru", "native", False)
        return id

    def contest(
        self,
        id: str,
        election: str,
        kind: str,
        slug: str,
        name: str,
        *,
        district: str,
        magnitude: int | None,
        system: str,
        body: str | None = None,
        office: str | None = None,
        round_number: int | None = None,
        previous: str | None = None,
        boundary: str | None = None,
    ) -> str:
        self.p.add(
            "contest",
            id=id,
            election_id=election,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district,
            magnitude=magnitude,
            round_number=round_number,
            previous_round_id=previous,
            electoral_system_id=system,
            default_result_kind="counted",
            boundary_set_id=boundary,
        )
        self.p.name(id, name, "ru", "official")
        self.p.add("slug", path=slug, entity_id=id, is_current=True)
        return id

    def result_set(
        self, id: str, election: str, contest: str, default: bool, method: str, completeness: float | None = 1.0
    ) -> str:
        self.p.add(
            "result_set",
            id=id,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CIK,
            status="active",
            is_default=default,
            completeness=completeness,
        )
        self.p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
        return id

    def option(
        self, id: str, contest: str, kind: str, position: int | None, ballot: str, party_id: str | None = None
    ) -> str:
        self.p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind=kind,
            party_id=party_id,
            ballot_position=position,
            ballot_label=ballot,
        )
        return id

    def candidate(self, tag: str, option: str, printed: str, role: str = "head") -> str:
        person = key("person", tag)
        self.p.add("person", id=person)
        self.p.name(person, person_name(printed), "ru", "official")
        candidacy = key("candidacy", tag)
        self.p.add("candidacy", id=candidacy, person_id=person, option_id=option, role=role, ballot_name=printed)
        return candidacy

    def declared(self, result_set: str, option: str | None, measure: int, value: int | None) -> None:
        if value is not None:
            self.p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)


def completeness(part: int, whole: int) -> float:
    return round(part / whole, 6) if whole else 0.0


# ----------------------------------------------------------------------------------------------------------------
# 12 December 1993

REFERENDUM_METHOD = (
    "The Commission's table of the vote on the draft Constitution by federal subject. Voters are the ballots issued "
    "(including those issued early), ballots cast are the ballots found in the boxes, and valid and invalid are the "
    "ballots so counted. No vote was held in the Chechen Republic, whose row is all zeros and is left out."
)
LIST_METHOD_1993 = (
    "The Commission's table of the federal list vote by federal subject, which prints each list's votes and the "
    "subject's registered voters; it prints no ballot accounting by subject. The national totals are the "
    "Commission's own. Seats are those of the deputies the Commission lists as elected in the federal district."
)
DISTRICT_METHOD_1993 = (
    "The Commission's table of the single-member district results, which prints each district's ballot accounting "
    "and only the elected deputy with their votes; the other candidates' votes are not published there. Voters are "
    "the ballots issued on election day, and ballots cast are those found in the boxes."
)
COUNCIL_METHOD_1993 = (
    "The Commission's table of the Federation Council results, which prints each two-member district's ballot "
    "accounting and only the two elected deputies with their votes. Each voter could vote for two candidates, so "
    "the votes add up to more than the valid ballots."
)


def vote_1993() -> Package:
    p = Package(
        f"{SOURCE}/1993-12-12",
        SOURCE,
        "The 12 December 1993 vote on the Constitution, State Duma election, and Federation Council election, by "
        "federal subject and district, from the Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {RUSSIA, TERRITORY}
    for url in (REFERENDUM_1993, DUMA_1993, COUNCIL_1993):
        p.uses(raw.cached(SOURCE, url))
    p.accept(
        "accounting.votes_valid",
        "the Commission's 1993 single-member district table prints only the elected deputy's votes, and no "
        "candidate's votes in the four Tatarstan districts it declared void, so the stored votes fall short of the "
        "valid ballots in every district",
    )
    p.accept(
        "accounting.ballots_sum",
        "in four 1993 district rows the Commission prints valid and invalid ballots that differ by 1 to 6 from the "
        "ballots it found in the boxes; stored as printed",
    )
    b = Builder(p)
    day = DAY_1993
    _referendum_1993(b, day)
    _duma_1993(b, day)
    _council_1993(b, day)
    return p


def _referendum_1993(b: Builder, day: date) -> None:
    path_ = "ru/1993-12-12-referendum"
    election = b.election(
        key("election", "referendum", 1993),
        "referendum",
        day,
        path_,
        "1993 Russian constitutional referendum",
        "Всенародное голосование по проекту Конституции Российской Федерации 12 декабря 1993 года",
    )
    contest = b.contest(
        key("contest", "referendum", 1993),
        election,
        "ballot_question",
        f"{path_}/constitution",
        "Принятие Конституции Российской Федерации",
        district=TERRITORY,
        magnitude=None,
        system=REFERENDUM_SYSTEM,
    )
    b.p.add("ballot_question", contest_id=contest, type="referendum")
    b.p.add(
        "ballot_question_text",
        id=key("question", 1993),
        contest_id=contest,
        kind="summary",
        lang="en",
        body="Do you approve the draft Constitution of the Russian Federation presented by the President?",
        source_id=CIK,
    )
    result_set = b.result_set(key("result_set", "referendum", 1993), election, contest, True, REFERENDUM_METHOD)
    yes = b.option(key("option", "referendum", 1993, "yes"), contest, "answer", 1, "За")
    no = b.option(key("option", "referendum", 1993, "no"), contest, "answer", 2, "Против")
    b.p.add("outcome", result_set_id=result_set, option_id=yes, kind="won")
    rows = page(REFERENDUM_1993, "1993_ref_itogi.html")
    totals = {}
    for row in rows[1:]:
        if len(row) != 10:
            continue
        figures = [number(cell) for cell in row[2:9]]
        if row[1] == "ИТОГО":
            totals = dict(zip(("registered", "voters", "cast", "invalid", "valid", "yes", "no"), figures, strict=True))
            continue
        if not any(figures):
            continue
        unit = b.subject_unit(day, subject(row[1]))
        registered, voters, cast, invalid, valid, for_, against = figures
        for measure, value in ((2, registered), (14, voters), (3, cast), (5, invalid), (4, valid)):
            b.value(result_set, unit, None, measure, value)
        b.value(result_set, unit, yes, 1, for_)
        b.value(result_set, unit, no, 1, against)
    for measure, name in ((2, "registered"), (14, "voters"), (3, "cast"), (5, "invalid"), (4, "valid")):
        b.declared(result_set, None, measure, totals[name])
    b.declared(result_set, yes, 1, totals["yes"])
    b.declared(result_set, no, 1, totals["no"])


def _duma_1993(b: Builder, day: date) -> None:
    path_ = "ru/1993-12-12-general"
    election = b.election(
        key("election", "duma", 1993),
        "general",
        day,
        path_,
        "1993 Russian State Duma election",
        "Выборы депутатов Государственной Думы Федерального Собрания Российской Федерации 1993 года",
    )
    contest = b.contest(
        key("contest", "duma", 1993, "list"),
        election,
        "list_allocation",
        f"{path_}/federal-list",
        "Общефедеральный избирательный округ",
        district=TERRITORY,
        magnitude=225,
        system=LIST_SYSTEM,
        body=DUMA,
    )
    result_set = b.result_set(key("result_set", "duma", 1993, "list"), election, contest, True, LIST_METHOD_1993)
    national = national_1993()
    options: list[str] = []
    for position, (name, votes) in enumerate(national, start=1):
        option = b.option(key("option", "duma", 1993, "list", position), contest, "list", position, name, party(name))
        options.append(option)
        b.declared(result_set, option, 1, votes)
        seats = LIST_SEATS[1993].get(name)
        if seats:
            b.p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
    rows = page(DUMA_1993, "1993_itogi_FS_obshefed_okrug.html")
    current: str | None = None
    for row in rows[1:]:
        if row and row[0] == "Названия избирательных объединений":
            break
        if len(row) == 1 and row[0].startswith("Всего по региону"):
            registered = number(re.sub(r"\D", "", row[0]))
            if current is not None:
                b.value(result_set, b.subject_unit(day, current), None, 2, registered)
            continue
        if len(row) == 1 and row[0] not in ("", "."):
            current = subject(row[0])
        elif len(row) == len(options) and current is not None and all(number(c) is not None for c in row):
            unit = b.subject_unit(day, current)
            for (name, _), option, cell in zip(national, options, row, strict=True):
                value = number(cell)
                printed, corrected = CORRECTIONS_1993.get((current, name), (value, value))
                if value != printed:
                    raise ValueError(f"the 1993 list table no longer prints {printed} for {name} in {current}")
                b.value(result_set, unit, option, 1, corrected)
    b.p.add(
        "note",
        id=key("note", result_set, "corrected"),
        entity_id=result_set,
        kind="caveat",
        body_md="The Commission's table prints 114,276 votes for Russia's Choice in Moscow; the share printed beneath "
        "it (34.73%) and the national total both give 1,142,762, which is stored here.",
        lang="en",
    )
    plan = 1993
    for n, (name, _) in sorted(okrugs_1993().items()):
        _district_1993(b, day, election, path_, plan, n, name)


def _district_1993(b: Builder, day: date, election: str, path_: str, plan: int, n: int, name: str) -> None:
    row = districts_1993()[n]
    figures = [number(cell) for cell in row[2:7]]
    if not any(figures):
        return
    contest = b.contest(
        key("contest", "duma", 1993, "district", n),
        election,
        "body_seats",
        f"{path_}/{changeset.slug(f'{n} {name}')}",
        f"{name} одномандатный избирательный округ № {n}",
        district=okrug_area(plan, n),
        magnitude=1,
        system=DISTRICT_SYSTEM,
        body=DUMA,
        boundary=key("boundary_set", OKRUG, plan),
    )
    result_set = b.result_set(
        key("result_set", "duma", 1993, "district", n), election, contest, True, DISTRICT_METHOD_1993, None
    )
    unit = b.okrug_unit(day, plan, n)
    registered, voters, cast, invalid, valid = figures
    for measure, value in ((2, registered), (14, voters), (3, cast), (5, invalid), (4, valid)):
        b.value(result_set, unit, None, measure, value)
    winner = row[7] if len(row) > 8 and row[7] != "-" else ""
    if winner:
        option = b.option(key("option", "duma", 1993, "district", n, 1), contest, "candidate", None, winner)
        b.candidate(f"duma:1993:{n}:{winner}", option, winner)
        b.value(result_set, unit, option, 1, number(row[8]))
        b.p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
    else:
        b.p.add(
            "note",
            id=key("note", result_set, "void"),
            entity_id=result_set,
            kind="caveat",
            body_md="No deputy was elected: the Commission's resolution of 25 December 1993 declared the election "
            "void here because valid ballots fell short of a quarter of the registered voters.",
            lang="en",
        )


@functools.cache
def districts_1993() -> dict[int, list[str]]:
    return {
        int(row[0]): row
        for row in page(DUMA_1993, "1993_itogi_FS_GD.html")[1:]
        if len(row) >= 7 and number(row[0]) is not None
    }


def _council_1993(b: Builder, day: date) -> None:
    path_ = "ru/1993-12-12-federation-council"
    election = b.election(
        key("election", "council", 1993),
        "general",
        day,
        path_,
        "1993 Russian Federation Council election",
        "Выборы депутатов Совета Федерации Федерального Собрания Российской Федерации 1993 года",
    )
    rows = page(COUNCIL_1993, "1993_itogi_FS.html")
    heading = ""
    current: dict[str, object] | None = None
    districts: list[dict[str, object]] = []
    for row in rows[1:]:
        if len(row) == 1 and (found := re.match(r"N\s*(\d+)\s+(.*)$", row[0])):
            current = {"n": int(found.group(1)), "name": found.group(2), "iso": subject(heading), "winners": []}
            districts.append(current)
        elif len(row) == 1 and row[0]:
            heading = row[0]
        elif current is not None and len(row) == 8 and row[6]:
            winners = current["winners"]
            assert isinstance(winners, list)
            winners.append((row[6], number(row[7])))
        elif current is not None and len(row) == 8 and any(number(c) for c in row[1:6]):
            current["figures"] = [number(c) for c in row[1:6]]
    for entry in districts:
        n = int(str(entry["n"]))
        iso = str(entry["iso"])
        contest = b.contest(
            key("contest", "council", 1993, n),
            election,
            "body_seats",
            f"{path_}/{changeset.slug(f'{n} {NAMES[iso]}')}",
            f"{entry['name']} двухмандатный избирательный округ № {n}",
            district=subject_area(iso),
            magnitude=2,
            system=COUNCIL_SYSTEM,
            body=COUNCIL,
        )
        result_set = b.result_set(
            key("result_set", "council", 1993, n), election, contest, True, COUNCIL_METHOD_1993, None
        )
        unit = b.subject_unit(day, iso)
        figures = entry.get("figures")
        if isinstance(figures, list):
            for measure, value in zip((2, 14, 3, 5, 4), figures, strict=True):
                b.value(result_set, unit, None, measure, value)
        winners = entry["winners"]
        assert isinstance(winners, list)
        for i, (name, votes) in enumerate(winners, start=1):
            option = b.option(key("option", "council", 1993, n, i), contest, "candidate", None, name)
            b.candidate(f"council:1993:{n}:{name}", option, name)
            b.value(result_set, unit, option, 1, votes)
            b.p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")


# ----------------------------------------------------------------------------------------------------------------
# State Duma 1995 and 1999

LIST_METHOD = (
    "The Commission's summary of the district commissions' protocols No. 2 (federal lists), by single-member "
    "district. Registered voters, valid and invalid ballots are as printed; ballots cast are the ballots found in "
    "the stationary and portable boxes, added here. The national totals are the Commission's own."
)
LIST_TIK_METHOD = (
    "The votes for each federal list and against all lists by territorial election commission (TIK) within each "
    "single-member district, from the Commission's TIK workbook. It prints no turnout or ballot accounting; those "
    "are in the default set by district. In 1999 the workbook's sums differ by a few dozen votes in all from the "
    "district protocols."
)
DISTRICT_METHOD = (
    "The votes for each candidate and against all candidates by territorial election commission (TIK), from the "
    "Commission's TIK workbook of the district commissions' protocols No. 1. It prints no ballot accounting by TIK. "
    "The district totals the workbook prints are recorded as the declared totals."
)


def duma(year: int) -> Package:
    day = DAY_1995 if year == 1995 else DAY_1999
    p = Package(
        f"{SOURCE}/duma-{year}",
        SOURCE,
        f"The {day:%-d %B %Y} State Duma election: federal lists by district and by TIK, and single-member districts "
        "by TIK, from the Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {RUSSIA, TERRITORY}
    for url in (DUMA_LISTS[year], DUMA_TIK_DISTRICT[year], DUMA_TIK_LIST[year], PROTOCOL[year]):
        p.uses(raw.cached(SOURCE, url))
    b = Builder(p)
    path_ = f"ru/{day}-general"
    election = b.election(
        key("election", "duma", year),
        "general",
        day,
        path_,
        f"{year} Russian State Duma election",
        f"Выборы депутатов Государственной Думы Федерального Собрания Российской Федерации {year} года",
    )
    contest = b.contest(
        key("contest", "duma", year, "list"),
        election,
        "list_allocation",
        f"{path_}/federal-list",
        "Федеральный избирательный округ",
        district=TERRITORY,
        magnitude=225,
        system=LIST_SYSTEM,
        body=DUMA,
    )
    table = okrug_table(DUMA_LISTS[year])
    cell = table.cell
    assert callable(cell)
    result_set = b.result_set(key("result_set", "duma", year, "list"), election, contest, True, LIST_METHOD)
    options: dict[int, str] = {}
    for ballot, name, _ in table.options:
        options[ballot] = b.option(
            key("option", "duma", year, "list", ballot), contest, "list", ballot, display(name), party(name)
        )
        seats = LIST_SEATS[year].get(str(ballot))
        if seats:
            b.p.add("outcome", result_set_id=result_set, option_id=options[ballot], kind="won", seats=seats)
    against = b.option(
        key("option", "duma", year, "list", "against"), contest, "none_of_the_above", None, "Против всех"
    )
    lines = table.lines
    for n_text, column in table.columns.items():
        unit = b.okrug_unit(day, year, int(n_text))
        b.value(result_set, unit, None, 2, number(cell(lines[1], column)))
        b.value(result_set, unit, None, 4, number(cell(lines[9], column)))
        b.value(result_set, unit, None, 5, number(cell(lines[10], column)))
        boxes = [number(cell(lines[7], column)), number(cell(lines[8], column))]
        if None not in boxes:
            b.value(result_set, unit, None, 3, sum(v for v in boxes if v is not None), DERIVED)
        for ballot, _, row in table.options:
            b.value(result_set, unit, options[ballot], 1, number(cell(row, column)))
        if table.against is not None:
            b.value(result_set, unit, against, 1, number(cell(table.against, column)))
    if table.total is not None:
        for measure, line in ((2, 1), (4, 9), (5, 10)):
            b.declared(result_set, None, measure, number(cell(lines[line], table.total)))
        for ballot, _, row in table.options:
            b.declared(result_set, options[ballot], 1, number(cell(row, table.total)))
        if table.against is not None:
            b.declared(result_set, against, 1, number(cell(table.against, table.total)))
    list_votes = sum(number(cell(row, column)) or 0 for _, _, row in table.options for column in table.columns.values())
    _list_tik(b, year, day, election, contest, options, against, list_votes)
    for block in tik_blocks(DUMA_TIK_DISTRICT[year]):
        _district(b, year, day, election, path_, block)
    return p


def _list_tik(
    b: Builder,
    year: int,
    day: date,
    election: str,
    contest: str,
    options: dict[int, str],
    against: str,
    list_votes: int,
) -> None:
    url = DUMA_TIK_LIST[year]
    meaning: dict[Label, str] = {}
    if year == 1995:
        # The 1995 key numbers the lists 1 to 43 and the line against all 13, as the protocol does; the sheets print
        # the lists' numbers as number cells and the line's as text.
        meaning = {("N", ballot): str(ballot) for ballot in options}
        meaning[("S", 13)] = "against"
    else:
        for found, text in legend(url).items():
            if "против всех" in text:
                meaning[found] = "against"
            elif prefix := re.match(r"(\d+)\.", text):
                meaning[found] = prefix.group(1)
    result_set = key("result_set", "duma", year, "list", "tik")
    counted = 0
    rows: list[tuple[str, str, int]] = []
    for block in tik_blocks(url):
        n = okrug_number(block.title)
        for name, values in block.tiks.items():
            unit = b.tik_unit(day, str(n), okrug_area(year, n), name)
            for found, value in values.items():
                if found not in meaning:
                    raise LookupError(f"{url}: line {found} of {block.title} has no meaning")
                option = against if meaning[found] == "against" else options[int(meaning[found])]
                if option != against:
                    counted += value
                rows.append((unit, option, value))
    b.result_set(
        result_set, election, contest, False, LIST_TIK_METHOD, completeness(counted, list_votes) if list_votes else None
    )
    for unit, option, value in rows:
        b.value(result_set, unit, option, 1, value)


def _district(b: Builder, year: int, day: date, election: str, path_: str, block: Block) -> None:
    n = okrug_number(block.title)
    name = okrug_name(block.title)
    contest = b.contest(
        key("contest", "duma", year, "district", n),
        election,
        "body_seats",
        f"{path_}/{changeset.slug(f'{n} {name}')}",
        block.title,
        district=okrug_area(year, n),
        magnitude=1,
        system=DISTRICT_SYSTEM,
        body=DUMA,
        boundary=key("boundary_set", OKRUG, year),
    )
    result_set = b.result_set(
        key("result_set", "duma", year, "district", n), election, contest, True, DISTRICT_METHOD, None
    )
    options: dict[Label, str] = {}
    winner = None
    for found, printed in block.names.items():
        if not printed:
            options[found] = b.option(
                key("option", "duma", year, "district", n, "against"),
                contest,
                "none_of_the_above",
                None,
                "Против всех",
            )
            continue
        option = b.option(key("option", "duma", year, "district", n, found[1]), contest, "candidate", found[1], printed)
        options[found] = option
        b.candidate(f"duma:{year}:{n}:{found[1]}", option, printed)
        if block.winner and clean(block.winner) == printed:
            winner = option
    if block.winner and winner is None:
        raise LookupError(f"{block.title}: the elected deputy {block.winner} is not among the candidates")
    if winner is not None:
        b.p.add("outcome", result_set_id=result_set, option_id=winner, kind="elected")
    for tik, values in block.tiks.items():
        unit = b.tik_unit(day, str(n), okrug_area(year, n), tik)
        for found, value in values.items():
            b.value(result_set, unit, options[found], 1, value)
    counted = {found for values in block.tiks.values() for found in values}
    for found, value in block.total.items():
        if value or found in counted:
            b.declared(result_set, options[found], 1, value)


# ----------------------------------------------------------------------------------------------------------------
# President 1996

PRESIDENT_METHOD = (
    "The Commission's summary of the subject election commissions' protocols. Registered voters, valid and invalid "
    "ballots are as printed; ballots cast are the ballots found in the stationary and portable boxes, added here. "
    "Polling stations abroad form their own column. The national totals are the Commission's own."
)
PRESIDENT_TIK_METHOD = (
    "The votes for each candidate and against all candidates by territorial election commission (TIK), from the "
    "Commission's TIK workbook. It prints no turnout or ballot accounting by TIK; those are in the default set by "
    "federal subject."
)


def president() -> Package:
    p = Package(
        f"{SOURCE}/president-1996",
        SOURCE,
        "The 1996 presidential election, both rounds, by federal subject and by TIK, from the Central Election "
        "Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {RUSSIA, TERRITORY}
    for url in (*PRESIDENT_SUBJECTS, *PRESIDENT_TIK, *PRESIDENT_PROTOCOL):
        p.uses(raw.cached(SOURCE, url))
    b = Builder(p)
    previous: str | None = None
    people: dict[str, str] = {}
    for index, day in enumerate(DAY_1996):
        round_number = index + 1
        path_ = f"ru/{day}-presidential" + ("" if round_number == 1 else "-runoff")
        election = b.election(
            key("election", "president", 1996, round_number),
            "general" if round_number == 1 else "runoff",
            day,
            path_,
            f"1996 Russian presidential election{'' if round_number == 1 else ', second round'}",
            "Выборы Президента Российской Федерации 1996 года"
            + ("" if round_number == 1 else ", повторное голосование"),
        )
        contest = b.contest(
            key("contest", "president", 1996, round_number),
            election,
            "office",
            f"{path_}/president",
            "Президент Российской Федерации",
            district=TERRITORY,
            magnitude=1,
            system=PRESIDENT_SYSTEM,
            office=PRESIDENCY,
            round_number=round_number,
            previous=previous,
        )
        previous = contest
        result_set = b.result_set(
            key("result_set", "president", 1996, round_number), election, contest, True, PRESIDENT_METHOD
        )
        columns, total, rows, cell = subject_table(PRESIDENT_SUBJECTS[index])
        assert callable(cell)
        candidates = [name for name in rows if not name.startswith("line ")]
        options: dict[str, str] = {}
        for position, printed in enumerate(candidates, start=1):
            option = options[printed] = b.option(
                key("option", "president", 1996, round_number, printed),
                contest,
                "candidate",
                None if printed == TULEEV else position,
                printed,
            )
            if printed not in people:
                person = people[printed] = key("person", "president", 1996, printed)
                p.add("person", id=person)
                p.name(person, person_name(printed), "ru", "official")
            person = people[printed]
            candidacy = key("candidacy", "president", 1996, round_number, printed)
            p.add("candidacy", id=candidacy, person_id=person, option_id=option, role="head", ballot_name=printed)
            outcome = OUTCOMES_1996[round_number].get(printed.split()[0])
            if outcome:
                p.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
        against = b.option(
            key("option", "president", 1996, round_number, "against"),
            contest,
            "none_of_the_above",
            None,
            "Против всех",
        )
        for iso, column in columns.items():
            unit = b.subject_unit(day, iso)
            _president_protocol(b, result_set, unit, rows, cell, column, options, against)
        for measure, line in ((2, "1"), (4, "8"), (5, "9")):
            b.declared(result_set, None, measure, number(cell(rows[f"line {line}"], total)))
        for printed, option in options.items():
            b.declared(result_set, option, 1, number(cell(rows[printed], total)))
        b.declared(result_set, against, 1, number(cell(rows["line 13"], total)))
        candidate_votes = sum(number(cell(rows[printed], total)) or 0 for printed in options)
        _president_tik(b, round_number, day, election, contest, options, against, candidate_votes)
    return p


OUTCOMES_1996 = {
    1: {"ЕЛЬЦИН": "advanced", "ЗЮГАНОВ": "advanced", "ТУЛЕЕВ": "withdrew"},
    2: {"ЕЛЬЦИН": "elected", "ЗЮГАНОВ": "lost"},
}


def _president_protocol(
    b: Builder,
    result_set: str,
    unit: str,
    rows: dict[str, int],
    cell: object,
    column: int,
    options: dict[str, str],
    against: str,
) -> None:
    assert callable(cell)

    def line(n: str) -> int | None:
        return number(cell(rows[f"line {n}"], column))

    b.value(result_set, unit, None, 2, line("1"))
    b.value(result_set, unit, None, 4, line("8"))
    b.value(result_set, unit, None, 5, line("9"))
    portable, stationary = line("6"), line("7")
    if portable is not None and stationary is not None:
        b.value(result_set, unit, None, 3, portable + stationary, DERIVED)
    for printed, option in options.items():
        b.value(result_set, unit, option, 1, number(cell(rows[printed], column)))
    b.value(result_set, unit, against, 1, line("13"))


def _president_tik(
    b: Builder,
    round_number: int,
    day: date,
    election: str,
    contest: str,
    options: dict[str, str],
    against: str,
    candidate_votes: int,
) -> None:
    url = PRESIDENT_TIK[round_number - 1]
    meaning: dict[Label, str] = {}
    for found, text in legend(url).items():
        if "против всех" in text:
            meaning[found] = "against"
        elif "Тулеева" in text:
            meaning[found] = TULEEV
        elif text in options:
            meaning[found] = text
    result_set = key("result_set", "president", 1996, round_number, "tik")
    counted = 0
    values_out: list[tuple[str, str, int]] = []
    for block in tik_blocks(url):
        area = None if block.subject == ABROAD else subject_area(block.subject)
        for name, values in block.tiks.items():
            unit = b.tik_unit(day, f"RU-{block.subject}" if area else ABROAD, area, name)
            for found, value in values.items():
                if found not in meaning:
                    raise LookupError(f"{url}: line {found} of {block.title} has no meaning")
                option = against if meaning[found] == "against" else options[meaning[found]]
                if option != against:
                    counted += value
                values_out.append((unit, option, value))
    b.result_set(result_set, election, contest, False, PRESIDENT_TIK_METHOD, completeness(counted, candidate_votes))
    for unit, option, value in values_out:
        b.value(result_set, unit, option, 1, value)
