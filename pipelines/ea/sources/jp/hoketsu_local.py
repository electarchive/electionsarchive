"""Diet by-election counts by counting district (開票区), from the prefectural election commissions.

The ministry's by-election summaries (`jp.mic_hoketsu`) print each district's totals only. The prefectural election
commission that ran the by-election publishes the count by counting district, as a workbook, a PDF table, or a web
page. Each record below names the commission's page and the files that hold the candidates' votes and the turnout.

Tables are read by meaning rather than by position: the row that names every candidate of the contest fixes the
candidate columns, the columns headed 有効投票数, 無効投票数, 投票総数, 投票者数, and 当日有権者数 give the counts,
and every row whose label resolves to a municipality or ward is a counting district. A record is used only when the
candidates' votes summed over its counting districts equal the ministry's district totals; a count column is kept only
when its sum equals the ministry's figure.
"""

import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import polars as pl
import psycopg
import pymupdf

from ea import raw
from ea.sources.jp import mic
from ea.sources.jp.pref_chiji import COMMISSIONS, NO_LICENSE

SOURCE = "jp.mic_hoketsu"
MEASURES = {
    "valid": ("有効投票数",),
    "invalid": ("無効投票数",),
    "cast": ("投票総数",),
    "voters": ("投票者数", "投票者総数"),
    "registered": ("当日有権者数", "当日の有権者数", "有権者数"),
}
NOT_MEASURE = re.compile(r"率|得票総数|割合|前回|比較|増減|期日前|不在者|在外|按分")
SUBTOTAL = re.compile(r"計|^県$|^都$|^府$|^道$|^区$|選挙区|^第[\d一二三四五六七八九十]+区$|^\d+区$")
TOTAL = re.compile(r"^(合計|総計|県計|都計|府計|道計|全県計?|.*選挙区計|第[\d一二三四五六七八九十]+区計)$")
HEADER = re.compile(r"候補|得\s*票|開票区|投票数|有権者")
STATUS = re.compile(r"^(確定|開票中|未開票|開票終了|終了|速報|中間)$")
NUMBERED = re.compile(r"^(.+?)(?:市)?(?:第(\d+|[一二三四五六七八九])(?:開票区|区)?|\((?:第)?(\d+)区?\))$")
Grid = list[list[str]]


@dataclass(frozen=True)
class Record:
    """One by-election's count, keyed by the `jp.mic_hoketsu` contest key."""

    key: str
    prefecture: str
    title: str
    page: str
    files: tuple[str, ...]
    publisher: str = ""
    license: str = ""
    license_url: str | None = None
    attribution: str = ""
    sheets: tuple[str, ...] = field(default=())
    # Header names a commission prints for a candidate in place of the ballot name, by the ministry's ballot name.
    aliases: tuple[tuple[str, str], ...] = field(default=())
    # The file type of files whose addresses carry no extension.
    kind: str = ""

    def suffix(self, url: str) -> str:
        return self.kind or suffix(url)

    def commission(self) -> tuple[str, str, str | None, str]:
        known = COMMISSIONS.get(self.prefecture)
        publisher = self.publisher or (known.publisher if known else f"{self.prefecture}選挙管理委員会")
        if self.license:
            return publisher, self.license, self.license_url, self.attribution or f"出典：{publisher}"
        if known is None:
            raise ValueError(f"{self.key}: give the license of the {publisher} site")
        return publisher, known.license, known.license_url, known.attribution


IWATE = "https://www.pref.iwate.jp/_res/projects/default_project/_page_/001"
SHIZUOKA = "https://www.pref.shizuoka.jp/_res/projects/default_project/_page_/001"
YAMAGUCHI = "https://www.pref.yamaguchi.lg.jp"
OKINAWA = "https://www.pref.okinawa.lg.jp/_res/projects/default_project/_page_/001"
KAGOSHIMA = "https://www.pref.kagoshima.jp/ka01/kensei/senkyo/senkyokekka"
ISHIKAWA = "https://www.pref.ishikawa.lg.jp/senkan/kako_data/documents"
TOKYO = "https://www.senkyo.metro.tokyo.lg.jp"
TOKYO_FILES = ("kaihyo_kouhoshatokuhyo", "kaihyo_uchiwake", "touhyo")

RECORDS = [
    Record(
        "2024-10-27:hoc:03",
        "岩手県",
        "参議院岩手県選出議員補欠選挙 開票結果・投票結果",
        "https://www.pref.iwate.jp/iinkai/senkyo/kekka/1078566.html",
        (f"{IWATE}/078/566/sanho002.xlsx", f"{IWATE}/078/566/sanho01.xlsx"),
    ),
    Record(
        "2021-10-24:hoc:22",
        "静岡県",
        "参議院静岡県選出議員補欠選挙 開票結果・投票結果",
        "https://www.pref.shizuoka.jp/kensei/senkyo/kakosenkyo/1040574/index.html",
        (f"{SHIZUOKA}/032/196/sanho_kai_kaku_2355.xlsx", f"{SHIZUOKA}/032/195/sanho_tou_kaku_2040.xlsx"),
    ),
    Record(
        "2020-04-26:hor:22:4",
        "静岡県",
        "衆議院小選挙区選出議員補欠選挙（静岡県第4区） 開票結果・投票結果",
        "https://www.pref.shizuoka.jp/kensei/senkyo/kakosenkyo/1040575/index.html",
        (f"{SHIZUOKA}/032/316/kai-kakuc.xlsx", f"{SHIZUOKA}/032/315/tou-kakuc.xlsx"),
    ),
    Record(
        "2023-04-23:hor:35:2",
        "山口県",
        "衆議院山口県第2区及び第4区選出議員補欠選挙 確定開票状況・確定投票状況",
        f"{YAMAGUCHI}/site/senkyo/206692.html",
        (f"{YAMAGUCHI}/uploaded/attachment/147955.xls", f"{YAMAGUCHI}/uploaded/attachment/147953.xls"),
    ),
    Record(
        "2023-04-23:hor:35:4",
        "山口県",
        "衆議院山口県第2区及び第4区選出議員補欠選挙 確定開票状況・確定投票状況",
        f"{YAMAGUCHI}/site/senkyo/206692.html",
        (f"{YAMAGUCHI}/uploaded/attachment/147955.xls", f"{YAMAGUCHI}/uploaded/attachment/147953.xls"),
    ),
    Record(
        "2021-10-24:hoc:35",
        "山口県",
        "参議院山口県選挙区選出議員補欠選挙 開票結果・投票結果",
        f"{YAMAGUCHI}/site/senkyo/102601.html",
        (
            f"{YAMAGUCHI}/uploaded/attachment/106514.xlsx",
            f"{YAMAGUCHI}/uploaded/attachment/106515.xls",
            f"{YAMAGUCHI}/uploaded/attachment/106513.xls",
        ),
        aliases=(("はらだ しょうた", "へずまりゅう"),),
    ),
    Record(
        "2013-04-28:hoc:35",
        "山口県",
        "参議院山口県選挙区選出議員補欠選挙 開票状況・確定投票状況",
        f"{YAMAGUCHI}/site/senkyo/25853.html",
        (f"{YAMAGUCHI}/site/senkyo/34132.html", f"{YAMAGUCHI}/site/senkyo/34133.html"),
    ),
    Record(
        "2024-04-28:hor:13:15",
        "東京都",
        "衆議院議員補欠選挙（令和6年4月28日執行）候補者別得票数・開票結果内訳・投票結果",
        f"{TOKYO}/election/shuugiin-all/shuugiin-sokuhou2024",
        tuple(f"{TOKYO}/documents/d/senkyo/r6shuugiinhosen_{name}_2" for name in TOKYO_FILES),
        kind=".xls",
    ),
    Record(
        "2016-10-23:hor:13:10",
        "東京都",
        "衆議院議員補欠選挙（平成28年10月23日執行）候補者別得票数・開票結果内訳・投票結果",
        f"{TOKYO}/election/shuugiin-all/shuugiin-sokuhou2016",
        tuple(f"{TOKYO}/documents/d/senkyo/h28shuugiinhosen_{name}_2" for name in TOKYO_FILES),
        kind=".xls",
    ),
    Record(
        "2022-04-24:hoc:17",
        "石川県",
        "参議院石川県選挙区選出議員補欠選挙 開票結果・投票結果",
        "https://www.pref.ishikawa.lg.jp/senkan/kako_data/sangi.html",
        (f"{ISHIKAWA}/kaihyou_1.pdf", f"{ISHIKAWA}/touhyou_1.pdf"),
    ),
    Record(
        "2016-04-24:hor:26:3",
        "京都府",
        "衆議院京都府第3区選出議員補欠選挙 開票結果・投票結果",
        "https://www.pref.kyoto.jp/senkyo/kekka.html",
        (
            "https://www.pref.kyoto.jp/senkyo/documents/kaihyouketsuryousokuhou.pdf",
            "https://www.pref.kyoto.jp/senkyo/documents/touhyouketsuryousokuhou.pdf",
        ),
    ),
    Record(
        "2006-04-23:hor:12:7",
        "千葉県",
        "衆議院小選挙区（千葉県第7区）選出議員補欠選挙 開票調・投票調",
        "https://www.pref.chiba.lg.jp/senkan/kakokekka/kako/h18-0423.html",
        (
            "https://www.pref.chiba.lg.jp/senkan/kakokekka/kako/documents/sk180423.xls",
            "https://www.pref.chiba.lg.jp/senkan/kakokekka/kako/documents/st180423.xls",
        ),
    ),
    Record(
        "2023-04-23:hoc:44",
        "大分県",
        "参議院大分県選出議員補欠選挙 開票結果（候補者別・開票区別投票総数）・投票結果",
        "https://www.pref.oita.jp/site/senkyo/sangihoketsukekka.html",
        tuple(f"https://www.pref.oita.jp/uploaded/attachment/{n}.pdf" for n in (2185012, 2185011, 2185009)),
        license=NO_LICENSE,
    ),
    Record(
        "2021-04-25:hoc:34",
        "広島県",
        "参議院広島県選出議員再選挙 開票結果・投票結果",
        "https://www.pref.hiroshima.lg.jp/soshiki/117/03sangisai-kekka.html",
        tuple(f"https://www.pref.hiroshima.lg.jp/uploaded/attachment/{n}.pdf" for n in (434397, 434253)),
        aliases=(("もりい しゅういち", "さとう しゅういち"),),
    ),
    Record(
        "2019-04-21:hor:47:3",
        "沖縄県",
        "衆議院沖縄県第3区選出議員補欠選挙 開票最終結果・投票最終結果",
        "https://www.pref.okinawa.lg.jp/kensei/senkyo/1005009/1023800/1005021.html",
        (f"{OKINAWA}/005/021/senkaisaishuu0423.xls", f"{OKINAWA}/005/021/sentousaishu0423.xls"),
    ),
    Record(
        "2014-04-27:hor:46:2",
        "鹿児島県",
        "衆議院鹿児島県第2区選出議員補欠選挙 投開票速報",
        f"{KAGOSHIMA}/20140427/20140427.html",
        (
            f"{KAGOSHIMA}/20140427/documents/38831_20140427225657-1.xls",
            f"{KAGOSHIMA}/20140427/documents/38831_20140427213032-1.xls",
        ),
    ),
    Record(
        "2012-10-28:hor:46:3",
        "鹿児島県",
        "衆議院鹿児島県第3区選出議員補欠選挙 投開票速報",
        f"{KAGOSHIMA}/121028/121028.html",
        (
            f"{KAGOSHIMA}/121028/documents/28409_20121029085900-1.xls",
            f"{KAGOSHIMA}/121028/documents/28409_20121029085835-1.xls",
        ),
    ),
]


def suffix(url: str) -> str:
    match = re.search(r"\.(xlsx?|pdf|csv|html?)$", url.lower())
    return f".{match[1]}" if match else ".html"


def fetch() -> None:
    for record in RECORDS:
        raw.fetch(SOURCE, record.page, suffix=".html")
        for url in record.files:
            raw.fetch(SOURCE, url, suffix=record.suffix(url))


def clean(text: object) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or ""))).strip()


def html_tables(text: str) -> list[Grid]:
    """Each table as a grid, with cells spanning rows and columns repeated into every slot they cover."""
    found = []
    for table in re.findall(r"<table[^>]*>(.*?)</table>", text, re.DOTALL | re.IGNORECASE):
        grid: dict[tuple[int, int], str] = {}
        for r, row in enumerate(re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL | re.IGNORECASE)):
            c = 0
            for attrs, body in re.findall(r"<t[dh]([^>]*)>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE):
                while (r, c) in grid:
                    c += 1
                cell = clean(html.unescape(re.sub(r"<br\s*/?>", " ", body, flags=re.IGNORECASE)).replace("\xa0", " "))
                cell = clean(re.sub(r"<[^>]+>", "", cell))
                rows = int(m[1]) if (m := re.search(r'rowspan="?(\d+)', attrs, re.IGNORECASE)) else 1
                cols = int(m[1]) if (m := re.search(r'colspan="?(\d+)', attrs, re.IGNORECASE)) else 1
                for dr in range(rows):
                    for dc in range(cols):
                        grid[r + dr, c + dc] = cell
                c += cols
        if grid:
            height = max(r for r, _ in grid) + 1
            width = max(c for _, c in grid) + 1
            found.append([[grid.get((r, c), "") for c in range(width)] for r in range(height)])
    return found


def grids(path: Path, sheets: tuple[str, ...] = ()) -> list[Grid]:
    kind = path.suffix.lower()
    if kind in {".xls", ".xlsx"}:
        return [
            [[clean(c) for c in row] for row in mic.grid(path, s)]
            for s in mic.sheets(path)
            if not sheets or s in sheets
        ]
    if kind == ".pdf":
        found = []
        with pymupdf.open(path) as document:
            for page in document:
                for table in page.find_tables().tables:
                    found.append([[clean(c) for c in row] for row in table.extract()])
        return found
    data = path.read_bytes()
    charset = re.search(rb'charset=["\']?([\w-]+)', data[:3000])
    return html_tables(data.decode(charset[1].decode() if charset else "utf-8", errors="replace"))


def squeeze(text: str) -> str:
    return re.sub(r"[\s・()（）*＊]", "", text)


def number(text: str) -> Decimal | None:
    text = re.sub(r"[,\s票人]", "", text)
    if text in {"", "-", "−", "―", "‐"}:
        return Decimal(0) if text else None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def is_number(text: str) -> bool:
    return bool(text.strip()) and number(text) is not None


@dataclass
class Layout:
    """Where a table keeps its counts: the first column of figures and the column of each candidate or count."""

    first: int
    candidates: dict[int, int]
    measures: dict[str, int]
    width: int


def name_keys(people: pl.DataFrame, aliases: tuple[tuple[str, str], ...] = ()) -> dict[int, list[str]]:
    """Each candidate's ballot and legal names, which a header cell may contain, and the surname, which a header cell
    may equal (some commissions head the columns with surnames alone) when no other candidate shares it."""
    full: dict[int, list[str]] = {}
    surnames: dict[int, set[str]] = {}
    for row in people.iter_rows(named=True):
        names = [row["name"], row["legal_name"] or "", *(alias for name, alias in aliases if name == row["name"])]
        full[row["order"]] = [mic.variant(squeeze(name)) for name in names if name]
        surnames[row["order"]] = {mic.variant(name.split()[0]) for name in names if " " in name}
    shared = {
        s for order, own in surnames.items() for s in own if any(s in o for k, o in surnames.items() if k != order)
    }
    return {order: keys + [f"={s}" for s in sorted(surnames[order] - shared)] for order, keys in full.items()}


def candidate_columns(row: list[str], names: dict[int, list[str]]) -> dict[int, int]:
    found: dict[int, int] = {}
    for column, cell in enumerate(row):
        cell = mic.variant(squeeze(cell))
        if not cell:
            continue
        for order, keys in names.items():
            if order in found:
                continue
            if any(cell == key[1:] if key.startswith("=") else key in cell for key in keys):
                found[order] = column
                break
    return found


def measure_columns(grid: Grid, top: int, bottom: int) -> dict[str, int]:
    found: dict[str, int] = {}
    for r in range(max(0, top), min(len(grid), bottom)):
        for c, cell in enumerate(grid[r]):
            text = squeeze(cell)
            if not text or NOT_MEASURE.search(text):
                continue
            for measure, labels in MEASURES.items():
                if measure in found or not any(label in text for label in labels):
                    continue
                if measure == "registered" and "voters" not in found and "投票者" in text:
                    continue
                column = c
                # A count split into men, women, and total keeps the total in the column headed 計.
                for below in range(r + 1, min(len(grid), r + 3)):
                    total = [
                        k for k in range(c, min(len(grid[below]), c + 4)) if squeeze(grid[below][k]) in {"計", "合計"}
                    ]
                    if total:
                        column = total[0]
                        break
                found[measure] = column
    return found


def layout_of(grid: Grid, names: dict[int, list[str]]) -> tuple[int, Layout] | None:
    for r, row in enumerate(grid):
        if names:
            joined = [a + b for a, b in zip(row, grid[r + 1], strict=False)] if r + 1 < len(grid) else row
            for end, cells in ((r, row), (r + 1, joined)):
                candidates = candidate_columns(cells, names)
                if len(candidates) == len(names):
                    measures = measure_columns(grid, r - 3, end + 3)
                    return end, Layout(min(candidates.values()), candidates, measures, len(row))
        else:
            measures = measure_columns(grid, r, r + 1)
            if "registered" in measures or "voters" in measures:
                measures = measure_columns(grid, r - 2, r + 3)
                return r, Layout(min(measures.values()), {}, measures, len(row))
    return None


def label_of(row: list[str], first: int) -> str:
    parts = [cell for cell in row[:first] if cell and not is_number(cell) and not STATUS.match(cell)]
    # A header written down the side of the table (得 / 票 / 数) leaves one character in each row's first cell.
    if any(len(squeeze(part)) > 1 for part in parts):
        parts = [part for part in parts if len(squeeze(part)) > 1]
    unique: list[str] = []
    for part in parts:
        if part not in unique:
            unique.append(part)
    return squeeze("".join(unique))


SHIFTS = (0, 1, -1, 2, -2, 3, -3, 4, -4)
FRACTION = re.compile(r"^0?\.\d+$")


def settled(rows: Grid, columns: dict[str, int]) -> dict[str, list[int]]:
    """The columns that hold each header's figures. A header merged across several cells can sit a few columns off
    its figures, so the nearest column holding figures in the rows below is taken; a candidate's votes can continue in
    a column of fractions (按分) to the right, which is added."""
    sample = rows[:12]

    def filled(c: int) -> bool:
        return any(c < len(row) and is_number(row[c]) for row in sample)

    out: dict[str, list[int]] = {}
    taken = set(columns.values())
    for name, column in columns.items():
        found = next(
            (
                column + s
                for s in SHIFTS
                if column + s >= 0 and (not s or column + s not in taken) and filled(column + s)
            ),
            column,
        )
        taken.add(found)
        out[name] = [found]
    taken = {c for cs in out.values() for c in cs}
    for name, (column,) in list(out.items()):
        if not name.startswith("votes:"):
            continue
        for c in range(column + 1, column + 20):
            if c in taken:
                break
            cells = [row[c] for row in sample if c < len(row) and row[c].strip()]
            if cells and all(FRACTION.match(cell.replace(",", "")) for cell in cells) and any("." in x for x in cells):
                out[name].append(c)
                taken.add(c)
                break
    return out


def rows_of(grids: list[Grid], names: dict[int, list[str]]) -> list[tuple[str, dict[str, Decimal]]]:
    """Label and figures of every row below a header that names the candidates (or, with no names, the turnout
    counts). A table with no header of its own and the width of the last one continues it, as a PDF table split across
    pages does."""
    found: list[tuple[str, dict[str, Decimal]]] = []
    last: Layout | None = None
    for grid in grids:
        located = layout_of(grid, names)
        if located is not None:
            start, last = located[0] + 1, located[1]
        elif (
            last is not None
            and grid
            and len(grid[0]) == last.width
            and not any(HEADER.search(c) for row in grid for c in row)
        ):
            start = 0
        else:
            continue
        columns = settled(
            grid[start:], {f"votes:{order}": column for order, column in last.candidates.items()} | last.measures
        )
        edge = min((c for cs in columns.values() for c in cs), default=last.first)
        here = 0
        for row in grid[start:]:
            label = label_of(row, min(edge, last.first))
            if label and TOTAL.match(label) and here:
                break
            if not label or SUBTOTAL.search(label):
                continue
            figures: dict[str, Decimal | None] = {}
            for name, cs in columns.items():
                parts = [number(row[c]) if c < len(row) else None for c in cs]
                figures[name] = None if parts[0] is None else sum((p or Decimal(0) for p in parts), Decimal(0))
            if any(value is None for value in figures.values()):
                continue
            here += 1
            found.append((label, {name: value for name, value in figures.items() if value is not None}))
    return found


@dataclass
class Place:
    label: str
    scheme: str
    code: str
    figures: dict[str, Decimal]
    # The commission labels the row as a numbered part of the municipality (松戸市7区, 周南第二).
    part: bool = False


def resolve(
    rows: list[tuple[str, dict[str, Decimal]]], prefectures: list[str], places: mic.Places
) -> tuple[list[Place], list[str]]:
    resolved: list[Place] = []
    skipped: list[str] = []
    previous: str | None = None
    for label, figures in rows:
        # A city counted in numbered parts (周南第二 for the second counting district of 周南市) is held as the part
        # of the city in the district, so only the city is resolved here.
        numbered = NUMBERED.match(label)
        tries = [label] + ([numbered[1], numbered[1] + "市"] if numbered else [])
        if label.endswith("開票区"):
            tries += [label[:-3] + "区", label[:-3]]
        for prefecture, attempt in ((p, a) for p in prefectures for a in tries):
            try:
                scheme, code, piece = places.resolve(prefecture, attempt, previous)
            except LookupError:
                continue
            previous = code
            resolved.append(Place(label, scheme, code, figures, piece is not None or attempt != tries[0]))
            break
        else:
            skipped.append(label)
    wards = {places.city_of(place.code) for place in resolved if place.scheme == "jp.ward"}
    return [place for place in resolved if place.code not in wards], skipped


def first(places: list[Place]) -> list[Place]:
    """One place per municipality: a row repeated in a later table is dropped, and the numbered counting districts of
    one city are added together."""
    seen: dict[str, Place] = {}
    labels: dict[str, set[str]] = {}
    for place in places:
        if place.code not in seen:
            seen[place.code] = Place(place.label, place.scheme, place.code, dict(place.figures), place.part)
            labels[place.code] = {place.label}
        elif place.label not in labels[place.code] and NUMBERED.match(place.label):
            labels[place.code].add(place.label)
            held = seen[place.code]
            held.figures = {k: v + place.figures.get(k, Decimal(0)) for k, v in held.figures.items()}
    return list(seen.values())


def gathered(measure: str, pools: list[list[Place]], inside: list[str], total: Decimal) -> dict[str, Decimal] | None:
    """The count of each counting district from the first table that gives it for every one of them and sums to
    the ministry's figure."""
    for pool in pools:
        values: dict[str, Decimal] = {}
        for place in pool:
            if measure in place.figures and place.code in inside and place.code not in values:
                values[place.code] = place.figures[measure]
        if len(values) == len(inside) and sum(values.values(), Decimal(0)) == total:
            return values
    return None


def split_cities(conn: psycopg.Connection, held: str, prefecture: str, district: int) -> set[str]:
    """Municipalities and wards that the ministry counted in parts for this district at the last general election
    before the by-election, which the by-election shares."""
    before = max((day for day in mic.ELECTIONS.values() if day < held), default=None)
    if before is None:
        return set()
    rows = conn.execute(
        """select distinct p.code from external_id x
           join result_set s on s.id = x.entity_id
           join result_value v on v.result_set_k = s.k
           join reporting_unit u on u.k = v.unit_k
           join reporting_unit p on p.id = u.parent_id
           where x.scheme = 'jp.mic.result_set' and x.value = %s and u.scheme_code = 'jp.counting_district'""",
        [f"{before}:{prefecture}:{district}"],
    ).fetchall()
    return {str(code) for (code,) in rows}


def parse(conn: psycopg.Connection, contests: pl.DataFrame, candidates: pl.DataFrame) -> pl.DataFrame:
    out: list[dict] = []
    for record in RECORDS:
        meta = contests.filter(pl.col("key") == record.key)
        if meta.height != 1:
            raise LookupError(f"{record.key}: no such contest")
        contest = meta.row(0, named=True)
        people = candidates.filter(pl.col("label") == contest["label"])
        names = name_keys(people, record.aliases)
        tables = [grid for url in record.files for grid in grids(raw.cached(SOURCE, url).path, record.sheets)]
        held = contest["held"]
        codes = contest["codes"].split(",")
        places = mic.Places(conn, date.fromisoformat(held))
        voted, skipped = resolve(rows_of(tables, names), codes, places)
        voting = first(voted)
        inside = [place.code for place in voting]
        turnout = resolve(rows_of(tables, {}), codes, places)[0]
        split = split_cities(conn, held, codes[0], int(contest["district"])) if contest["district"] else set()
        problems = []
        for row in people.iter_rows(named=True):
            total = sum((place.figures[f"votes:{row['order']}"] for place in voting), Decimal(0))
            if abs(total - Decimal(row["votes"])) > Decimal("0.001") * max(len(voting), 1):
                problems.append(f"{row['name']} {total} against {row['votes']}")
        if problems or not voting:
            raise ValueError(
                f"{record.key}: counting districts do not sum to the ministry's totals: {problems}; "
                f"{len(voting)} places {[p.label for p in voting]}, skipped {skipped}"
            )
        kept: dict[str, dict[str, Decimal]] = {}
        for measure in MEASURES:
            if contest.get(measure) is None:
                continue
            values = gathered(measure, [turnout, voted], inside, Decimal(contest[measure]))
            values = values or gathered(measure, [voted, turnout], inside, Decimal(contest[measure]))
            if values is not None:
                kept[measure] = values

        def row_of(place: Place, measure: str, order: int | None, value: Decimal) -> dict:
            district = contest["district"]
            piece = str(district) if district and (place.code in split or place.part) else None
            return {
                "key": record.key,
                "scheme": place.scheme,
                "code": place.code,
                "piece": piece,
                "label": place.label,
                "measure": measure,
                "order": order,
                "value": str(value),
            }

        for place in voting:
            for row in people.iter_rows(named=True):
                out.append(row_of(place, "votes", row["order"], place.figures[f"votes:{row['order']}"]))
            for measure, values in kept.items():
                out.append(row_of(place, measure, None, values[place.code]))
        print(f"{record.key}: {len(voting)} counting districts, counts {sorted(kept)}, skipped {skipped}")
    return pl.DataFrame(out, schema=LOCAL)


LOCAL = {
    "key": pl.String,
    "scheme": pl.String,
    "code": pl.String,
    "piece": pl.String,
    "label": pl.String,
    "measure": pl.String,
    "order": pl.Int64,
    "value": pl.String,
}
