import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from urllib.parse import urljoin
from uuid import UUID

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Draft
from ea.sources.jp import mic
from ea.sources.jp.mic import Places, dhondt, grid, party_key, party_label, person_name, plain, sheets
from ea.sources.jp.mlit import PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.mic_hoc"
RAW = mic.SOURCE
ROOT = mic.DATA_ROOT
ELECTIONS = {
    21: ("2007-07-29", "sangiin21/index.html", "sangiin21/sangiin21_3_14.html"),
    22: ("2010-07-11", "sangiin22/index_1.html", "sangiin22/sangiin22_7.html"),
    23: ("2013-07-21", "sangiin23/index.html", "sangiin23/sangiin23_7.html"),
    24: ("2016-07-10", "sangiin24/index.html", "sangiin24/sangiin24_8.html"),
    25: ("2019-07-21", "sangiin25/index.html", "sangiin25/sangiin25_8.html"),
    26: ("2022-07-10", "sangiin26/index.html", "sangiin26/sangiin26_8.html"),
    27: ("2025-07-20", "sangiin27/index.html", "sangiin27/sangiin27_8.html"),
}
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BOOK = "候補者別得票数（選挙区）"
SEATS = "党派別議席配分表（比例代表）"
LISTS = "党派別名簿登載者別得票数、当選人数（比例代表）"
PARTY_TOTALS = "党派別得票数（比例代表）"
PARSED_ELECTIONS = [22, 23, 24, 25, 26, 27]
PREFECTURE_NAMES = [native for native, _ in PREFECTURES]
TOTAL_ROW = re.compile(r"(合計|[都道府県]計|県計)$")
SUBTOTAL_ROW = re.compile(r"計$")
PLACE = re.compile(r"[市区町村]$")
PRIVATE = re.compile(r"[-]")
# A list candidate's votes in a counting district may carry three decimals (按分), and a district's candidate votes
# in the party workbook are their exact sum.
PLACE_SLACK = Decimal("0.0005")
# Counting-district rows of the candidate sheets that disagree with the party workbook, corrected to the values that
# make the row sum to the workbook's candidate votes there and the sheet's columns sum to its printed totals. Keyed
# by (held, prefecture, party, district), in the sheet's column order.
SHEET_FIXES: dict[tuple[str, str, str, str], list[str]] = {
    # The first candidate's cell reads "c"; the party workbook's 169 candidate votes less the other three is 53.
    ("2019-07-21", "千葉県", "社会民主党", "千葉市中央区"): ["53", "85", "9", "22"],
    # The party's one candidate has 13 and 37 here against the workbook's 5 and 11, which also make the sheet's
    # districts sum to its printed 125 and the candidate's districts to the national 15,257.
    ("2025-07-20", "青森県", "日本改革党", "十和田市"): ["5"],
    ("2025-07-20", "青森県", "日本改革党", "三沢市"): ["11"],
}
# Candidate sheets whose rows slip by one from a district on: that district repeats the row above it, each later
# district holds the next one's row, and the last district's row is missing, so it is the printed total less the
# other districts. Each shifted row then sums to the party workbook's candidate votes for its district.
SHEET_SHIFTS: dict[tuple[str, str, str], str] = {("2025-07-20", "岩手県", "日本共産党"): "平泉町"}
# Candidate sheets where one candidate's column also holds another's votes: in the 2019 Shizuoka sheet of the
# Constitutional Democratic Party, おまた一平's column adds おしどりマコ's to his own in every district (643 votes),
# so his districts sum to his national votes, and each district to the party workbook's, only once they come off.
SHEET_COLUMNS: dict[tuple[str, str, str], tuple[str, str]] = {
    ("2019-07-21", "静岡県", "立憲民主党"): ("おまた一平", "おしどりマコ"),
}
# Candidate sheets that print another prefecture's district names: the 2013 Ibaraki sheet of 生活の党 labels its 32
# city rows with Iwate's municipalities, in Ibaraki's row order, so its rows take the labels of the workbook's other
# sheets.
SHEET_LABELS: set[tuple[str, str, str]] = {("2013-07-21", "茨城県", "生活の党")}
# List candidates whose districts, as every prefecture's sheet prints them and sums them to its own totals, still
# miss the national table: in 2016 新党改革's fifth candidate by the table's order is one vote short and its ninth
# one vote over.
NATIONAL_GAPS: dict[str, set[tuple[str, int]]] = {"2016-07-10": {("新党改革", 5), ("新党改革", 9)}}
DROPPED: list[dict] = []
UNCONFIRMED: list[dict] = []
BOOK_VOTES = {
    ("2025-07-20", "兵庫県"): {
        "きし 文男": "28250",
        "うらき けんご": "13533",
        "ふじわら せいや": "273844",
        "かだ 裕之": "285451",
        "高橋 ひであき": "30638.043",
        "多田 ひとみ": "225402",
        "前田 みさ": "46703",
        "米村 明美": "85452",
        "高橋 みつお": "339822.908",
        "立花 孝志": "157005",
        "よしひら 敏孝": "275301",
        "泉 房穂": "822407",
        "金田 峰生": "87748",
    }
}
DISTRICT_UNIT = "@district"
ALIASES = {
    "緑の党グリーンズジャパン": "緑の党",
    "民進": "民進党",
    "維新": "おおさか維新の会",
    "公明": "公明党",
    "社民党": "社会民主党",
    "自民党": "自由民主党",
    "共産党": "日本共産党",
    "幸福実現等": "幸福実現党",
    "幸福": "幸福実現党",
    "日本": "日本のこころを大切にする党",
    "支持なし": "支持政党なし",
    "分のことしか考えていな": "自分のことしか考えていない党",
    "日本のこころを": "日本のこころを大切にする党",
    "生活の党と": "生活の党と山本太郎となかまたち",
    "労働の解放をめざす": "労働の解放をめざす労働者党",
    "たちあがれ": "たちあがれ日本",
    "たちあがれ日": "たちあがれ日本",
}
MERGED = ("鳥取県・島根県", "徳島県・高知県")
SIZE = re.compile(r"[（(]定数(\d+)(?:[（(](\d+)[）)])?名[）)]")


def party_name(text: str) -> str:
    name = mic.party_name(text)
    return ALIASES.get(name, name)


def amount(text: str) -> Decimal:
    text = text.replace(",", "").replace("\u3000", "").strip()
    if text in {"", "-", "－", "―", "｜", "|"}:
        return Decimal(0)
    try:
        return Decimal(text)
    except ArithmeticError:
        if re.fullmatch(r"[A-Za-z]", text):
            return Decimal(0)
        raise ValueError(f"not a number: {text!r}") from None


def close(left: list[Decimal], right: list[Decimal]) -> bool:
    return len(left) == len(right) and all(abs(a - b) < Decimal("0.01") for a, b in zip(left, right, strict=True))


def list_parts(
    values: list[tuple[UUID, UUID, UUID, Decimal]], parent: dict[UUID, UUID], legends: set[UUID], special: set[UUID]
) -> dict[tuple[UUID, UUID, UUID], Decimal]:
    sums: dict[tuple[UUID, UUID, UUID], Decimal] = {}
    for result_set, place, option, amount_ in values:
        sums[result_set, place, option] = sums.get((result_set, place, option), Decimal(0)) + amount_
    lists = set(parent.values())
    children: dict[tuple[UUID, UUID, UUID], list[tuple[UUID, UUID, UUID]]] = {}
    for key in sums:
        if key[2] in parent:
            children.setdefault((key[0], key[1], parent[key[2]]), []).append(key)
    kept = {key: amount_ for key, amount_ in sums.items() if key[2] not in lists}
    dropped = 0
    for key, total in sums.items():
        if key[2] not in lists:
            continue
        for allowed in (lambda option: True, lambda option: option not in special, lambda option: option in legends):
            parts = [part for part in children.get(key, []) if allowed(part[2])]
            rest = total - sum((sums[part] for part in parts), Decimal(0))
            if rest >= 0:
                break
        for part in children.get(key, []):
            if part not in parts:
                del kept[part]
                dropped += 1
        if rest > 0:
            kept[key] = rest
    if dropped:
        print(f"dropped {dropped} candidate values whose sums exceed their list totals")
    return kept


def url_of(base: str, href: str) -> str:
    return urljoin(base, href)


@dataclass
class Listing:
    index: str
    pages: list[tuple[str, list[str]]]
    book: str
    seats: str
    lists: str
    totals: str


def listing(number: int, fetching: bool = False) -> Listing:
    """The ministry's files for one election, read from its index pages. When `fetching`, each page is fetched into
    the cache before it is read, so a fetch works from an empty cache."""
    _, index_path, towns_path = ELECTIONS[number]
    index = f"{ROOT}/{index_path}"
    towns = f"{ROOT}/{towns_path}.html" if not towns_path.endswith(".html") else f"{ROOT}/{towns_path}"
    if fetching:
        raw.fetch(RAW, index, suffix=".html")
        raw.fetch(RAW, towns, suffix=".html")
    named: dict[str, str] = {}
    for href, label in mic.anchors(index):
        if label not in named or not href.endswith(".pdf"):
            named[label] = url_of(index, href)
    if number == 21:
        subpages = [url_of(towns, href) for href, _ in mic.anchors(towns) if re.search(r"_14_\d+\.html$", href)]
    else:
        subpages = [
            url_of(towns, href)
            for href, _ in mic.anchors(towns)
            if re.search(r"\d+_\d+_\d\d\.html$", href) and not href.endswith("_00.html")
        ]
    pages = []
    for page in dict.fromkeys(subpages):
        if fetching:
            raw.fetch(RAW, page, suffix=".html")
        files = [url_of(page, href) for href, _ in mic.anchors(page) if "main_content" in href or "/pdf/" in href]
        pages.append((page, files))
    return Listing(index, pages, named[BOOK], named[SEATS], named[LISTS], named[PARTY_TOTALS])


def leaves_of(found: list[tuple[str, list[Decimal]]], keep: set[str] | None = None) -> list[tuple[str, list[Decimal]]]:
    rows = [row for row in found if not SUBTOTAL_ROW.search(row[0])]
    if keep is not None:
        return [row for row in rows if row[0] in keep]
    drop: set[int] = set()
    for index, (_, values) in enumerate(rows):
        if not any(values):
            continue
        for size in range(1, 40):
            below = rows[index + 1 : index + 1 + size]
            if len(below) < size:
                break
            if [sum((row[1][k] for row in below), Decimal(0)) for k in range(len(values))] == values:
                drop.add(index)
                break
    for index, (_, values) in enumerate(rows):
        if index in drop or not any(values):
            continue
        run: list[int] = []
        for before in range(index - 1, -1, -1):
            if before in drop:
                continue
            run.append(before)
            if len(run) >= 2 and [sum((rows[i][1][k] for i in run), Decimal(0)) for k in range(len(values))] == values:
                drop.add(index)
                break
            if len(run) > 40:
                break
    return [row for index, row in enumerate(rows) if index not in drop]


def smd_sheet(rows: list[list[str]], held: str, sheet: str, labels: list[str]) -> tuple[list[dict], list[dict]]:
    name_row = next(i for i, row in enumerate(rows) if "候補者名" in row[:2] or row[0].startswith("開票区名\\候補者名"))
    old = not {"候補者名"} & set(rows[name_row][:2])
    first = next(j for j, cell in enumerate(rows[name_row]) if j > 0 and cell and "得票数計" not in cell)
    party_row = name_row - 1 if old else next(i for i in range(name_row + 1, name_row + 4) if rows[i][first])
    offset = 1 if "・" in sheet else 0
    columns = [
        j
        for j, cell in enumerate(rows[name_row])
        if j > offset and cell and "得票数計" not in cell and "率" not in cell
    ]
    found: list[tuple[str, list[Decimal]]] = []
    total = None
    owner = ""
    for row in rows[max(name_row, party_row) + 1 :]:
        if offset and row[0]:
            owner = re.sub(r"\s+", "", row[0])
        label = re.sub(r"\s+", "", row[offset] if offset else next((c for c in reversed(row[:first]) if c), ""))
        if not label or not any(row[j] for j in columns):
            continue
        values = [amount(row[j]) for j in columns]
        if TOTAL_ROW.search(label):
            total = values
        else:
            found.append((owner + "|" + label if offset else label, values))
    leaves = leaves_of(found)
    sums = [sum((values[k] for _, values in leaves), Decimal(0)) for k in range(len(columns))]
    if total is not None and not close(sums, total):
        print(f"MISMATCH {held} {sheet}: leaves {sums} total {total}")
    candidates = [
        {
            "held": held,
            "district": labels[0],
            "order": position + 1,
            "name": person_name(rows[name_row][j]),
            "party": party_name(rows[party_row][j]),
            "votes": str(sums[position]),
        }
        for position, j in enumerate(columns)
    ]
    votes = [
        {"held": held, "district": labels[0], "place": name, "order": k + 1, "votes": str(v)}
        for name, values in leaves
        for k, v in enumerate(values)
    ]
    return candidates, votes


def confirmed(text: str, votes: Decimal, mark: str) -> bool:
    for form in mic.printed(votes):
        for hit in re.finditer(re.escape(form) + r"(?![\d.,])", text):
            if mark in text[max(0, hit.start() - 110) : hit.start()]:
                return True
    return False


def book_for(number: int) -> str:
    """The 結果調 book's district table as plain text. Where pdftotext lacks the CJK encoding data the 2016 book's
    fonts need, it returns almost nothing, and the text comes from PyMuPDF instead."""
    url = listing(number).book
    text = mic.book_text(url)
    if len(CJK.findall(text)) >= 100 or not url.endswith(".pdf"):
        return text
    import pymupdf

    with pymupdf.open(raw.cached(RAW, url).path) as document:
        found = "".join(str(page.get_text()) for page in document)
    return plain(unicodedata.normalize("NFKC", found))


def districts(held: str) -> list[str]:
    if held < "2016":
        return PREFECTURE_NAMES
    return [*MERGED, *(name for name in PREFECTURE_NAMES if not name.startswith(("鳥取", "島根", "徳島", "高知")))]


def sizes(text: str, held: str) -> dict[str, tuple[int, int]]:
    found = {}
    for name in districts(held):
        match = re.search(re.escape(name) + SIZE.pattern, text)
        if match:
            found[name] = (int(match[1]), int(match[2] or 0))
    return found


def fetch() -> None:
    for number in ELECTIONS:
        found = listing(number, fetching=True)
        for url in [found.index, found.book, found.seats, found.lists, found.totals]:
            raw.fetch(RAW, url, suffix=mic.suffix(url))
        for page, files in found.pages:
            raw.fetch(RAW, page, suffix=".html")
            for url in files:
                raw.fetch(RAW, url, suffix=mic.suffix(url))


def norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


LABELS = {"届出番号", "政党等名", "党派名", "政党等の名称", "開票区名"}
SUBHEAD = re.compile(r"^(得票総数|政党等の|名簿登載者|政党等|得票数|\(?[abc]\)?$)")


def is_name(cell: str) -> bool:
    return bool(cell) and cell not in LABELS and not SUBHEAD.match(norm(cell).replace(" ", "").replace("\n", ""))


def pr_party_sheet(rows: list[list[str]], held: str, prefecture: str, blank: set[str] | None = None) -> list[dict]:
    """Each party's total, party-only votes, and candidate votes by counting district. A counting district whose row
    the workbook leaves empty (some 2025 workbooks do) gets no party values; its label goes into `blank`, since the
    candidate sheets still hold its candidate votes."""
    out: list[dict] = []
    index = 0
    while index < len(rows):
        if "届出番号" not in rows[index][:2]:
            index += 1
            continue
        start = next(i for i in range(index, len(rows)) if rows[i][0].startswith("開票区名"))
        name_row = max(
            range(index + 1, start),
            key=lambda i: sum(1 for cell in rows[i][1:] if is_name(cell)),
        )
        data = start + 1
        while data < len(rows) and (not rows[data][0] or not any(rows[data][1:])):
            if blank is not None and PLACE.search(re.sub(r"\s+", "", rows[data][0])):
                blank.add(re.sub(r"\s+", "", rows[data][0]))
            data += 1
        offsets = [j for j, cell in enumerate(rows[name_row]) if j > 0 and is_name(cell)]
        stop = data
        while stop < len(rows) and "届出番号" not in rows[stop][:2]:
            stop += 1
        block = []
        base = offsets[0]
        for row in rows[data:stop]:
            label = re.sub(r"\s+", "", next((c for c in reversed(row[:base]) if c), ""))
            if label and any(row[base:]):
                block.append((label, [amount(cell) for cell in row[base:]]))
            elif blank is not None and PLACE.search(label):
                blank.add(label)
        total = next((values for label, values in block if TOTAL_ROW.search(label)), None)
        leaves = leaves_of([row for row in block if not TOTAL_ROW.search(row[0])])
        for column in offsets:
            party = party_name(rows[name_row][column])
            at = column - base
            sums = [sum((values[at + k] for _, values in leaves), Decimal(0)) for k in range(3)]
            if total is not None and not close(sums, total[at : at + 3]):
                print(f"MISMATCH {held} {prefecture} {party}: {sums} against {total[at : at + 3]}")
            for label, values in leaves:
                whole = values[at]
                # Some workbooks (2016 Kumamoto) print a district's total to two decimals while its party and
                # candidate votes keep three; their sum is the exact total.
                if abs(whole - values[at + 1] - values[at + 2]) < Decimal("0.01"):
                    whole = values[at + 1] + values[at + 2]
                out.append(
                    {
                        "held": held,
                        "prefecture": prefecture,
                        "place": label,
                        "party": party,
                        "total": str(whole),
                        "party_votes": str(values[at + 1]),
                        "candidate_votes": str(values[at + 2]),
                    }
                )
        index = stop
    return out


def shifted(block: list[tuple[str, list[Decimal]]], first: str) -> list[tuple[str, list[Decimal]]]:
    """Undo a one-row slip in a candidate sheet (SHEET_SHIFTS): from `first` on, each district takes the row below
    it, and the last district before the total takes the total less every other district."""
    at = next(i for i, (label, _) in enumerate(block) if label == first)
    end = next(i for i, (label, _) in enumerate(block) if TOTAL_ROW.search(label))
    total = block[end][1]
    rows = [(label, values) for label, values in block[:at]]
    rows += [(block[i][0], block[i + 1][1]) for i in range(at, end - 1)]
    rest = [sum((values[k] for _, values in rows), Decimal(0)) for k in range(len(total))]
    rows.append((block[end - 1][0], [total[k] - rest[k] for k in range(len(total))]))
    return rows + block[end:]


def listed_person(named: dict[str, dict], text: str) -> dict | None:
    """The national list entry a candidate sheet's column header names: the same name after folding width and
    spaces, or else the one entry whose name starts with the header's."""
    key = plain(norm(person_name(text)))
    if key in named:
        return named[key]
    longer = [person for name, person in named.items() if len(key) >= 4 and name.startswith(key)]
    if len(longer) == 1:
        return longer[0]
    # The national tables write some rare kanji as private-use characters (such as U+E074 for a variant of 辻).
    gaiji = [
        person
        for name, person in named.items()
        if PRIVATE.search(name) and re.fullmatch("".join("." if PRIVATE.match(c) else re.escape(c) for c in name), key)
    ]
    return gaiji[0] if len(gaiji) == 1 else None


def pr_candidate_sheets(
    path, held: str, prefecture: str, keep: set[str], places: list[dict], people: list[dict]
) -> list[dict]:
    """Each list candidate's personal votes by counting district, from the prefecture's workbook of one sheet per
    party. The sheets number candidates by their registration number (整理番号), and the national table orders them
    by votes, so each column is matched to its national entry by name. Each counting district is checked against the
    party workbook's candidate votes there: the priority-quota (特定枠) candidates' votes count with the party's own
    votes in some years, and a district whose row disagrees either way is left out, so the list keeps the votes
    there as a remainder."""
    named: dict[str, dict[str, dict]] = {}
    for person in people:
        named.setdefault(person["party"], {})[plain(norm(person["name"]))] = person
    counted = {(row["place"], row["party"]): Decimal(row["candidate_votes"]) for row in places}
    # A party workbook row whose total is not its party votes plus its candidate votes (2019 南箕輪村 prints 0, 0,
    # and 19 for one party and 0, 34, and 0 for the next) settles nothing, so its candidate values stay out.
    broken = {
        (row["place"], row["party"])
        for row in places
        if Decimal(row["total"]) != Decimal(row["party_votes"]) + Decimal(row["candidate_votes"])
    }
    out: list[dict] = []
    first = grid(path, sheets(path)[0])
    labels = [row[0] for row in first[next(i for i, row in enumerate(first) if row[0] == "整理番号") + 2 :]]
    for sheet in sheets(path):
        rows = grid(path, sheet)
        named_party = party_name(rows[next(i for i, row in enumerate(rows) if row[0] == "政党等の名称") + 1][0])
        if (held, prefecture, named_party) in SHEET_LABELS:
            start = next(i for i, row in enumerate(rows) if row[0] == "整理番号") + 2
            if len(rows) - start != len(labels):
                raise ValueError(f"{held} {prefecture} {named_party}: {len(rows) - start} rows against {len(labels)}")
            rows = [*rows[:start], *([label, *row[1:]] for label, row in zip(labels, rows[start:], strict=True))]
        number_row = next(i for i, row in enumerate(rows) if row[0] == "整理番号")
        party = party_name(rows[next(i for i, row in enumerate(rows) if row[0] == "政党等の名称") + 1][0])
        columns = [j for j, cell in enumerate(rows[number_row]) if j > 0 and cell]
        persons = [listed_person(named.get(party, {}), rows[number_row + 1][j]) for j in columns]
        if None in persons:
            missing = [rows[number_row + 1][j] for j, person in zip(columns, persons, strict=True) if person is None]
            raise LookupError(f"{held} {prefecture} {party}: no national list entry for {missing}")
        regular = [k for k, person in enumerate(persons) if person is not None and not person["special"]]
        block = []
        for row in rows[number_row + 2 :]:
            label = re.sub(r"\s+", "", row[0])
            if label.endswith("区") and label not in keep:
                # A ward printed without its city: 東区 is 福岡市東区, not 北九州市八幡東区.
                label = next(iter(name for name in sorted(keep) if name.endswith("市" + label)), label)
            if label:
                block.append((label, [amount(row[j]) for j in columns]))
        if (held, prefecture, party) in SHEET_SHIFTS:
            block = shifted(block, SHEET_SHIFTS[held, prefecture, party])
        if (held, prefecture, party) in SHEET_COLUMNS:
            names = [plain(norm(person_name(rows[number_row + 1][j]))) for j in columns]
            minuend, extra = (names.index(name) for name in SHEET_COLUMNS[held, prefecture, party])
            block = [
                (label, [value - values[extra] if k == minuend else value for k, value in enumerate(values)])
                for label, values in block
            ]
        leaves = [
            (label, [Decimal(value) for value in SHEET_FIXES.get((held, prefecture, party, label), values)])
            for label, values in leaves_of([row for row in block if not TOTAL_ROW.search(row[0])], keep)
        ]
        everyone = list(range(len(columns)))
        chosen: dict[str, list[int] | None] = {}
        for label, values in leaves:
            expected = counted.get((label, party))
            if expected is None:
                continue
            if (label, party) in broken:
                chosen[label] = None
                DROPPED.append({"held": held, "prefecture": prefecture, "party": party, "place": label})
            elif abs(sum(values) - expected) <= PLACE_SLACK:
                chosen[label] = everyone
            elif abs(sum((values[k] for k in regular), Decimal(0)) - expected) <= PLACE_SLACK:
                chosen[label] = regular
            else:
                chosen[label] = None
                DROPPED.append({"held": held, "prefecture": prefecture, "party": party, "place": label})
        # One district left empty or wrong in a sheet is the printed total less the other districts, where that
        # sums to the party workbook's candidate votes there.
        total = next((values for label, values in block if TOTAL_ROW.search(label)), None)
        wrong = [label for label, choice in chosen.items() if choice is None and (label, party) not in broken]
        if total is not None and len(wrong) == 1:
            others = [values for label, values in leaves if label != wrong[0]]
            rest = [total[k] - sum((values[k] for values in others), Decimal(0)) for k in everyone]
            expected = counted[wrong[0], party]
            for choice in (everyone, regular):
                if min(rest) >= 0 and abs(sum((rest[k] for k in choice), Decimal(0)) - expected) <= PLACE_SLACK:
                    print(f"FILLED {held} {prefecture} {party} {wrong[0]} from the sheet's total")
                    leaves = [(label, rest if label == wrong[0] else values) for label, values in leaves]
                    chosen[wrong[0]] = choice
                    DROPPED.remove({"held": held, "prefecture": prefecture, "party": party, "place": wrong[0]})
                    break
        present = {label for label, _ in leaves}
        for (label, owner), expected in counted.items():
            if owner == party and expected and label not in present:
                DROPPED.append({"held": held, "prefecture": prefecture, "party": party, "place": label})
        # A district the party workbook leaves empty follows the sheet's other districts on the priority quota.
        alone = regular if any(choice is regular and choice != everyone for choice in chosen.values()) else everyone
        for label, values in leaves:
            kept = chosen.get(label, alone)
            if kept is None:
                continue
            for k in kept:
                person = persons[k]
                if person is None:
                    continue
                out.append(
                    {
                        "held": held,
                        "prefecture": prefecture,
                        "place": label,
                        "party": party,
                        "number": person["number"],
                        "name": person["name"],
                        "votes": str(values[k]),
                    }
                )
    return out


def nationally(rows: list[dict], people: list[dict], held: str) -> list[tuple[str, int]]:
    """The list candidates whose votes by counting district do not sum to their national votes. A candidate in a
    party whose sheet left a district out falls short there by design; any other gap means a column went to the
    wrong candidate."""
    sums: dict[tuple[str, int], Decimal] = {}
    for row in rows:
        if row["held"] == held:
            key = (row["party"], row["number"])
            sums[key] = sums.get(key, Decimal(0)) + Decimal(row["votes"])
    short = {(row["party"]) for row in DROPPED if row["held"] == held}
    off = []
    for person in people:
        key = (person["party"], person["number"])
        if person["party"] == "合計" or person["special"]:
            continue
        gap = Decimal(person["votes"]) - sums.get(key, Decimal(0))
        if abs(gap) > PLACE_SLACK and not (person["party"] in short and gap > 0):
            off.append(key)
            print(f"NATIONAL {held} {key}: districts sum {sums.get(key, Decimal(0))} against {person['votes']}")
    if unexplained := sorted(set(off) - NATIONAL_GAPS.get(held, set())):
        raise ValueError(f"{held}: list candidates whose districts miss their national votes: {unexplained}")
    return off


def national_lists(path, held: str) -> tuple[list[dict], list[dict]]:
    parties: list[dict] = []
    people: list[dict] = []
    rows = grid(path, sheets(path)[0])
    for sheet in sheets(path)[1:]:
        rows += grid(path, sheet)
    index = 0
    while index < len(rows):
        starts = [j for j, cell in enumerate(rows[index]) if cell == "政党等の名称"]
        if not starts:
            index += 1
            continue
        end = next((i for i in range(index + 1, len(rows)) if "政党等の名称" in rows[i]), len(rows))
        band = rows[index:end]
        for c in starts:
            width = next((s - c for s in starts if s > c), len(rows[index]) - c)
            party = party_name(next(cell for row in band[1:5] for cell in row[c : c + width] if cell))
            if party.startswith("得票総数"):
                continue
            record = {"held": held, "party": party}
            for row in band:
                label = row[c].replace(" ", "")
                cells = [cell for cell in row[c + 1 : c + width] if re.fullmatch(r"[\d,.]+", cell)]
                if not cells and label != "当選人数":
                    continue
                if label.startswith("得票総数(a)+(b)"):
                    record["total"] = str(amount(cells[0]))
                elif label.startswith("政党等の得票総数(a)"):
                    record["party_votes"] = str(amount(cells[0]))
                elif label.startswith("名簿登載者数"):
                    record["listed"] = int(cells[0])
                elif label.startswith("名簿登載者"):
                    record["candidate_votes"] = str(amount(cells[0]))
                elif label.startswith("当選人数"):
                    record["seats"] = int(cells[0])
            heading = next(i for i, row in enumerate(band) if row[c] in ("順位", "得票順位"))
            for row in band[heading + 1 :]:
                if not row[c].isdigit():
                    continue
                people.append(
                    {
                        "held": held,
                        "party": party,
                        "number": int(row[c]),
                        "result": row[c + 1],
                        "name": person_name(row[c + 2]),
                        "votes": str(amount(next((cell for cell in row[c + 3 : c + width] if cell), ""))),
                        "special": next((cell for cell in row[c + 3 : c + width] if cell), "") == "-",
                    }
                )
            parties.append(record)
        index = end
    return parties, people


def seat_table(path) -> dict[str, tuple[Decimal, int]]:
    seats: dict[str, list] = {}
    names: dict[int, str] = {}
    waiting = False
    for row in grid(path, sheets(path)[0]):
        if row[0] == "除数":
            waiting = True
        if waiting and any(row[1:]) and not (row[0] == "除数" and not any(row[1:])):
            names = {j: party_name(cell) for j, cell in enumerate(row) if j > 0 and cell}
            for name in names.values():
                seats.setdefault(name, [Decimal(0), 0])
            waiting = False
        elif row[0].isdigit() and names:
            for j, name in names.items():
                if row[0] == "1":
                    seats[name][0] = amount(row[j + 1])
                if row[j]:
                    seats[name][1] += 1
    return {name: (votes, won) for name, (votes, won) in seats.items()}


def prefecture_of_page(page: str) -> str:
    match = re.search(r"_(\d+)\.html$", page)
    if match is None:
        raise ValueError(page)
    return PREFECTURE_NAMES[int(match[1]) - 1]


BOOK_PARTIES = [
    "民主党",
    "自由民主党",
    "日本共産党",
    "みんなの党",
    "無所属",
    "幸福実現党",
    "社会民主党",
    "新党改革",
    "たちあがれ日本",
    "公明党",
    "日本創新党",
    "国民新党",
    "あきつ新党",
    "スマイル党",
    "新党フリーウェイクラブ",
    "平和党核兵器廃絶平和運動",
    "平和党核兵器廃絶平和",
    "世界経済共同体党",
    "新党本質",
    "女性党",
    "維新政党・新風",
]
ENTRY = re.compile(
    r"(当|落)([^\d男女()（）当落]{1,14}?)(?:男|女)\d{2,3}("
    + "|".join(sorted(map(re.escape, BOOK_PARTIES), key=len, reverse=True))
    + r")(?:新|現|元).*?(\d{1,3}(?:,\d{3})+(?:\.\d+)?)"
)
NOT_LABEL = re.compile(r"^(確定?|残票|開票率|.*順位|.*番号|[*＊]|\d.*|.*率|.+名)$")
CJK = re.compile(r"[぀-ヿ一-鿿]")


def book_entries(text: str) -> list[dict]:
    party = {"平和党核兵器廃絶平和": "平和党核兵器廃絶平和運動"}
    return [
        {"result": mark, "name": name, "party": party.get(member, member), "votes": amount(votes)}
        for mark, name, member, votes in ENTRY.findall(text)
    ]


def number_of(cell: str) -> Decimal | None:
    text = cell.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    return Decimal(text)


def label_of(row: list[str], before: int) -> str:
    for cell in reversed(row[:before]):
        text = re.sub(r"[\s☆★※◎○●*]+", "", cell).replace("ケ", "ヶ")
        if text and CJK.search(text) and not NOT_LABEL.match(text):
            return text
    return ""


def pdf_rows(path: Path) -> list[list[str]]:
    tsv = subprocess.run(["pdftotext", "-tsv", str(path), "-"], check=True, capture_output=True, text=True).stdout
    pages: dict[str, list[tuple[float, float, float, str]]] = {}
    for line in tsv.splitlines()[1:]:
        cells = line.split("\t")
        if cells[0] == "5" and cells[11].strip():
            left, width = float(cells[6]), float(cells[8])
            pages.setdefault(cells[1], []).append((float(cells[7]), left, left + width, norm(cells[11]).strip()))
    rows: list[list[str]] = []
    for words in pages.values():
        lines: list[list[tuple[float, float, str]]] = []
        last = -10.0
        for y, left, right, word in sorted(words):
            if y - last > 3:
                lines.append([])
                last = y
            lines[-1].append((left, right, word))
        joined: list[list[tuple[float, float, str]]] = []
        for line in lines:
            line.sort()
            out: list[tuple[float, float, str]] = []
            for left, right, word in line:
                if out and re.fullmatch(r"\.\d+", word) and number_of(out[-1][2]) is not None:
                    out[-1] = (out[-1][0], out[-1][1], out[-1][2] + word)
                else:
                    out.append((left, right, word))
            joined.append(out)

        def numeric(word: str) -> bool:
            return number_of(word) is not None and not re.fullmatch(r"0\d+", word)

        edges = sorted(
            (left, right)
            for line in joined
            if line
            and not numeric(line[0][2])
            and (count := sum(1 for *_, word in line if numeric(word))) >= 3
            and count >= 2 * (len(line) - count)
            for left, right, word in line
            if numeric(word)
        )
        clusters: list[list[tuple[float, float]]] = []
        for left, right in sorted(edges, key=lambda edge: edge[1]):
            if clusters and right - clusters[-1][-1][1] <= 6:
                clusters[-1].append((left, right))
            else:
                clusters.append([(left, right)])
        spans = [(min(left for left, _ in cluster), max(right for _, right in cluster)) for cluster in clusters]
        if not spans:
            continue
        for line in joined:
            row = [""] * (len(spans) + 1)
            for left, right, word in line:
                if numeric(word):
                    k = min(range(len(spans)), key=lambda k: abs(spans[k][1] - right))
                elif right <= spans[0][0] + 2:
                    row[0] += word
                    continue
                else:
                    middle = (left + right) / 2
                    k = min(range(len(spans)), key=lambda k: abs((spans[k][0] + spans[k][1]) / 2 - middle))
                row[k + 1] += word
            rows.append(row)
    return rows


def workbook_rows(path: Path) -> list[list[str]]:
    if path.suffix == ".pdf":
        return wards(pdf_rows(path))
    rows: list[list[str]] = []
    for sheet in sheets(path):
        rows += grid(path, sheet)
    return wards(rows)


def wards(rows: list[list[str]]) -> list[list[str]]:
    city = None
    for row in rows:
        label, at = labeled(row)
        if not label:
            continue
        if label.endswith("市"):
            city = label
        elif label.endswith("区") and city and "市" not in label:
            row[at] = city + label
        elif any(re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+", cell) for cell in row[at + 1 :]):
            city = None
    return rows


def table_rows(rows: list[list[str]], columns: list[int]) -> list[tuple[str, list[Decimal]]]:
    found = []
    for row in rows:
        if max(columns) >= len(row):
            continue
        cells = [number_of(row[j]) if row[j] not in {"-", "－", "―"} else Decimal(0) for j in columns]
        if all(cell is None for cell in cells) or any(cell is None and row[j] for cell, j in zip(cells, columns)):
            continue
        label = label_of(row, min(columns))
        steps = [cell or Decimal(0) for cell in cells]
        if len(steps) >= 3 and all(b - a == 1 for a, b in pairwise(steps)):
            continue
        if label:
            found.append((label, [cell or Decimal(0) for cell in cells]))
    return found


def smd_2010(
    path: Path, entries: list[dict], held: str, district: str
) -> tuple[list[dict], list[dict], dict[str, str]] | None:
    rows = workbook_rows(path)
    totals = {entry["votes"]: index for index, entry in enumerate(entries)}
    best: dict[int, int] = {}
    at = 0
    for index, row in enumerate(rows):
        hits: dict[int, int] = {}
        for j, cell in enumerate(row):
            value = number_of(cell)
            if value is not None and value in totals and totals[value] not in hits.values():
                hits[j] = totals[value]
        if len(hits) > len(best):
            best, at = hits, index
    columns = sorted(best)
    wanted = [entries[best[j]]["votes"] for j in columns]
    if not best:
        return None
    for part in (rows[:at], rows[at + 1 :], rows):
        found = [row for row in table_rows(part, columns) if not TOTAL_ROW.search(row[0])]
        leaves = leaves_of(found)
        sums = [sum((values[k] for _, values in leaves), Decimal(0)) for k in range(len(columns))]
        if sums == wanted:
            break
    else:
        print(f"MISMATCH {held} {district}: leaves {sums} against the book {wanted}")
        return None
    aliases = {
        label: found[index - 1][0]
        for index, (label, values) in enumerate(found)
        if label.endswith("郡") and index and found[index - 1][1] == values and (label, values) in leaves
    }
    leaves = [(aliases.get(label, label), values) for label, values in leaves]
    candidates = []
    for position, j in enumerate(columns):
        entry = entries[best[j]]
        header = next((row[j] for row in rows if j < len(row) and plain(row[j]) == plain(entry["name"])), "")
        candidates.append(
            {
                "held": held,
                "district": district,
                "order": position + 1,
                "name": person_name(header) if header else entry["name"],
                "party": party_name(entry["party"]),
                "votes": str(entry["votes"]),
                "result": entry["result"],
            }
        )
    votes = [
        {"held": held, "district": district, "place": name, "order": k + 1, "votes": str(v)}
        for name, values in leaves
        for k, v in enumerate(values)
    ]
    return candidates, votes, aliases


def bands(rows: list[list[str]], match) -> list[tuple[dict[int, object], list[list[str]]]]:
    out: list[tuple[dict[int, object], list[list[str]]]] = []
    for row in rows:
        hits = {j: key for j, cell in enumerate(row) if cell and (key := match(cell)) is not None}
        if hits:
            out.append((hits, []))
        elif out:
            out[-1][1].append(row)
    return out


FRACTION = re.compile(r"0?\.\d{1,3}|0")


def labeled(row: list[str]) -> tuple[str, int]:
    found = "", -1
    for j, cell in enumerate(row):
        if number_of(cell) is not None and found[1] >= 0:
            break
        text = re.sub(r"[\s☆★※◎○●*]+", "", cell).replace("ケ", "ヶ")
        if text and CJK.search(text) and not NOT_LABEL.match(text):
            found = text, j
    return found


def fused(
    rows: list[list[str]], keep: set[str], aliases: dict[str, str] | None = None
) -> list[tuple[str, dict[int, Decimal]]]:
    present = {labeled(row)[0] for row in rows}
    names = {county: town for county, town in (aliases or {}).items() if town not in present}
    rows = [
        row for row in rows if names.get(labeled(row)[0], labeled(row)[0]) in keep or TOTAL_ROW.search(labeled(row)[0])
    ]
    width = max((len(row) for row in rows), default=0)
    fraction = set()
    for j in range(1, width):
        cells = [row[j] for row in rows if j < len(row) and row[j]]
        before = [row[j - 1] for row in rows if j < len(row) and row[j - 1]]
        if (
            cells
            and all(FRACTION.fullmatch(cell) for cell in cells)
            and all(re.fullmatch(r"[\d,]+", cell) for cell in before)
        ):
            fraction.add(j)
    out = []
    for row in rows:
        label, at = labeled(row)
        label = names.get(label, label)
        if label not in keep and not TOTAL_ROW.search(label):
            continue
        values: dict[int, Decimal] = {}
        last = None
        for j in range(at + 1, len(row)):
            if j in fraction and last is not None:
                if row[j]:
                    values[last] += Decimal(row[j])
                continue
            value = number_of(row[j])
            if value is not None:
                values[j] = value
                last = j
        out.append((label, values))
    return out


def split(group: list[int], found: list[tuple[str, dict[int, Decimal]]]) -> tuple[int, int | None, int | None] | None:
    if len(group) == 1:
        return group[0], None, None
    for total in group:
        for a in group:
            for b in group:
                if len({total, a, b}) < 3 or a > b:
                    continue
                if all(
                    abs(values.get(total, Decimal(0)) - values.get(a, Decimal(0)) - values.get(b, Decimal(0)))
                    < Decimal("0.002")
                    for _, values in found
                ) and any(values.get(a) for _, values in found):
                    return total, a, b
    return None


def pr_party_2010(
    path: Path, held: str, prefecture: str, known: set[str], keep: list[str], aliases: dict[str, str]
) -> list[dict]:
    def match(cell: str) -> str | None:
        name = party_name(re.sub(r"^\d+[:：]?", "", norm(cell)))
        return name if name in known else None

    first: dict[tuple[str, str], tuple[Decimal, Decimal | None, Decimal | None]] = {}
    for hits, rows in bands(workbook_rows(path), match):
        found = fused(rows, set(keep), aliases)
        if not found:
            continue
        counts: dict[int, int] = {}
        for _, values in found:
            for j in values:
                counts[j] = counts.get(j, 0) + 1
        columns = sorted(j for j, count in counts.items() if count * 2 >= len(found))
        heads = sorted({str(party): j for j, party in sorted(hits.items(), reverse=True)}.values())
        groups: list[tuple[int, int | None, int | None]] = []
        index = 0
        while index < len(columns):
            for size in (3, 4):
                picked = split(columns[index : index + size], found) if index + size <= len(columns) else None
                if picked is not None and columns[index] in picked:
                    groups.append(picked)
                    index += size
                    break
            else:
                index += 1
        if not groups:
            groups = [(j, None, None) for j in heads if j in columns]
        if len(groups) == len(heads) + 1:
            for extra in (groups[0], groups[-1]):
                rest = [group for group in groups if group != extra]
                if all(
                    values.get(extra[0], Decimal(0))
                    == sum((values.get(group[0], Decimal(0)) for group in rest), Decimal(0))
                    for _, values in found
                ):
                    groups = rest
                    break
        parties = [str(hits[j]) for j in heads]
        if len(groups) == len(heads):
            for party, (total, a, b) in zip(parties, groups, strict=True):
                for label, values in found:
                    first.setdefault(
                        (party, label),
                        (
                            values.get(total, Decimal(0)),
                            None if a is None else values.get(a, Decimal(0)),
                            None if b is None else values.get(b, Decimal(0)),
                        ),
                    )
            continue
        for label, values in found:
            tokens = list(values.values())
            triples = [tokens[k : k + 3] for k in range(0, len(tokens), 3)]
            if len(tokens) != 3 * len(parties) or any(abs(t - a - b) >= Decimal("0.002") for t, a, b in triples):
                print(f"COLUMNS {held} {prefecture} {label}: {len(tokens)} values for {len(parties)} parties")
                continue
            for party, (total, a, b) in zip(parties, triples, strict=True):
                first.setdefault((party, label), (total, a, b))
    out = []
    for party in dict.fromkeys(party for party, _ in first):
        missing = [label for label in keep if (party, label) not in first]
        if missing:
            print(f"MISSING {held} {prefecture} {party}: {missing[:5]}")
        totals = [first[party, label] for p, label in first if p == party and TOTAL_ROW.search(label)]
        whole = sum((first[party, label][0] for label in keep if (party, label) in first), Decimal(0))
        if totals and all(abs(total[0] - whole) > Decimal("0.01") for total in totals):
            print(f"MISMATCH {held} {prefecture} {party}: {whole} against {[str(total[0]) for total in totals]}")
        for label in keep:
            if (party, label) not in first:
                continue
            total, a, b = first[party, label]
            out.append(
                {
                    "held": held,
                    "prefecture": prefecture,
                    "place": label,
                    "party": party,
                    "total": str(total),
                    "party_votes": None if a is None else str(a),
                    "candidate_votes": None if b is None else str(b),
                }
            )
    return out


def rising(values: list[int]) -> bool:
    return bool(values) and values[0] == 1 and all(a < b for a, b in pairwise(values))


def pr_candidates_2010(
    path: Path, held: str, prefecture: str, keep: list[str], known: set[str], aliases: dict[str, str]
) -> list[dict]:
    rows = workbook_rows(path)
    labels = set(keep)
    out: list[dict] = []
    parties: list[str] = []
    for row in rows:
        found = [name for cell in row if (name := party_name(re.sub(r"^\d+[:：\s]?", "", norm(cell)))) in known]
        parties.append(found[0] if found else parties[-1] if parties else "")
    index = 0
    while index < len(rows):
        row = rows[index]
        party = parties[index]
        if labeled(row)[0] in labels:
            index += 1
            continue
        numbers = {j: int(cell) for j, cell in enumerate(row) if j > 0 and re.fullmatch(r"\d{1,3}", cell)}
        if not rising(list(numbers.values())):
            index += 1
            continue
        stop = index + 1
        while stop < len(rows):
            later = rows[stop]
            values = [int(cell) for j, cell in enumerate(later) if j > 0 and re.fullmatch(r"\d{1,3}", cell)]
            if labeled(later)[0] not in labels and rising(values):
                break
            stop += 1
        names = {
            j: next(
                (
                    later[j]
                    for later in rows[index + 1 : stop]
                    if j < len(later) and later[j] and number_of(later[j]) is None
                ),
                "",
            )
            for j in numbers
        }
        for label, values in fused(rows[index + 1 : stop], labels, aliases):
            if label not in labels:
                continue
            for j, number in numbers.items():
                out.append(
                    {
                        "held": held,
                        "prefecture": prefecture,
                        "place": label,
                        "party": party,
                        "number": number,
                        "name": plain(names[j]),
                        "votes": str(values.get(j, Decimal(0))),
                    }
                )
        index = stop
    return out


def ranked_numbers(rows: list[dict], people: list[dict]) -> dict[tuple[str, int], dict]:
    named = {(row["party"], plain(norm(row["name"]))): row for row in people}
    votes: dict[tuple[str, int], dict[tuple[str, int], int]] = {}
    for row in rows:
        key = (row["party"], plain(norm(row["name"])))
        person = named.get(key)
        if person is None:
            longer = [
                found
                for (party, name), found in named.items()
                if party == row["party"] and len(key[1]) >= 4 and name.startswith(key[1])
            ]
            person = longer[0] if len(longer) == 1 else None
        if person is not None:
            tally = votes.setdefault((row["party"], row["number"]), {})
            tally[person["party"], person["number"]] = tally.get((person["party"], person["number"]), 0) + 1
    by = {(row["party"], row["number"]): row for row in people}
    return {key: by[max(tally, key=lambda k: tally[k])] for key, tally in votes.items()}


def parse_2010(
    found: Listing, text: str, held: str, size: dict[str, tuple[int, int]], listed: list[dict], ranked: list[dict]
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    entries = book_entries(text)
    known = {row["party"] for row in listed if row["party"] != "合計"}
    people = [row for row in ranked if row["party"] != "合計"]
    candidates: list[dict] = []
    smd_votes: list[dict] = []
    places: list[dict] = []
    loose: list[dict] = []
    for page, files in found.pages:
        prefecture = prefecture_of_page(page)
        parsed = smd_2010(raw.cached(RAW, files[0]).path, entries, held, prefecture)
        if parsed is None or not parsed[0]:
            print(f"SKIPPED {held} {prefecture}: no readable municipal table")
            UNCONFIRMED.append({"held": held, "district": prefecture})
            continue
        won = sum(1 for row in parsed[0] if row["result"] == "当")
        if won != sum(size[prefecture]):
            raise ValueError(f"{held} {prefecture}: {won} winners against {size[prefecture]} seats")
        candidates += parsed[0]
        smd_votes += parsed[1]
        keep = list(dict.fromkeys(row["place"] for row in parsed[1]))
        party_file = files[2] if files[1].endswith(".pdf") else files[1]
        places += pr_party_2010(raw.cached(RAW, party_file).path, held, prefecture, known, keep, parsed[2])
        loose += pr_candidates_2010(raw.cached(RAW, files[2]).path, held, prefecture, keep, known, parsed[2])
    print(f"{held}: {len(candidates)} district candidates")
    mapping = ranked_numbers(loose, people)
    split_votes = {
        (row["prefecture"], row["place"], row["party"]): row["candidate_votes"]
        for row in places
        if row["candidate_votes"] is not None
    }
    counts = {row["party"]: row["listed"] for row in listed}
    by_prefecture: dict[str, list[dict]] = {}
    for row in loose:
        by_prefecture.setdefault(row["prefecture"], []).append(row)
    pr_candidates: list[dict] = []
    for prefecture, rows in by_prefecture.items():
        sums: dict[tuple[str, str], Decimal] = {}
        mapped: list[dict] = []
        persons: dict[str, set[int]] = {}
        for row in rows:
            person = mapping.get((row["party"], row["number"]))
            if person is None:
                continue
            persons.setdefault(row["party"], set()).add(person["number"])
            sums[row["place"], row["party"]] = sums.get((row["place"], row["party"]), Decimal(0)) + Decimal(
                row["votes"]
            )
            mapped.append({**row, "number": person["number"], "name": person["name"]})
        short = sorted(party for party in known if len(persons.get(party, ())) != counts[party])
        off = sorted(
            {
                party
                for (place, party), total in sums.items()
                if (prefecture, place, party) in split_votes
                and abs(Decimal(split_votes[prefecture, place, party]) - total) > Decimal("0.01")
            }
        )
        if short or off:
            print(f"DROPPED {held} {prefecture} candidates: unmapped {short}, sums off {off}")
            DROPPED.append({"held": held, "prefecture": prefecture, "party": ", ".join(short + off)})
            continue
        pr_candidates += mapped
    return candidates, smd_votes, places, pr_candidates


def parse(numbers: list[int] | None = None) -> None:
    candidates: list[dict] = []
    smd_votes: list[dict] = []
    parties: list[dict] = []
    people: list[dict] = []
    seats: list[dict] = []
    pr_places: list[dict] = []
    pr_candidates: list[dict] = []
    for number in numbers or PARSED_ELECTIONS:
        held = ELECTIONS[number][0]
        found = listing(number)
        text = book_for(number)
        size = sizes(text, held)
        done: set[str] = set()
        parts: dict[str, tuple[list[dict], list[dict]]] = {}
        listed, ranked = national_lists(raw.cached(RAW, found.lists).path, held)
        if number == 22:
            more = parse_2010(found, text, held, size, listed, ranked)
            candidates += more[0]
            smd_votes += more[1]
            pr_places += more[2]
            pr_candidates += more[3]
        for page, files in found.pages if number != 22 else []:
            prefecture = prefecture_of_page(page)
            path = raw.cached(RAW, files[0]).path
            if files[0] not in done:
                done.add(files[0])
                names = sheets(path)
                sheet = next((x for x in names if "・" in x), names[0])
                pair = next((m for m in MERGED if prefecture in m and held >= "2016"), prefecture)
                district = sheet if "・" in sheet else pair
                more, votes = smd_sheet(grid(path, sheet), held, sheet, [district])
                if district in parts and "・" not in sheet:
                    for earlier, candidate in zip(parts[district][0], more, strict=True):
                        earlier["votes"] = str(Decimal(earlier["votes"]) + Decimal(candidate["votes"]))
                    parts[district][1].extend(votes)
                else:
                    parts[district] = (more, votes)
            party_path = raw.cached(RAW, files[1]).path
            parties_sheet = sheets(party_path)[0]
            blank: set[str] = set()
            places = pr_party_sheet(grid(party_path, parties_sheet), held, prefecture, blank)
            pr_places += places
            keep = {row["place"] for row in places} | blank
            pr_candidates += pr_candidate_sheets(raw.cached(RAW, files[2]).path, held, prefecture, keep, places, ranked)
        if number != 22:
            nationally(pr_candidates, ranked, held)
        for district, (more_, votes) in parts.items():
            more = more_
            won = sum(size[district])
            top = sorted(more, key=lambda c: Decimal(c["votes"]), reverse=True)
            if won < len(top) and Decimal(top[won - 1]["votes"]) == Decimal(top[won]["votes"]):
                raise ValueError(f"{held} {district}: tie at the last seat")
            for candidate in more:
                candidate["result"] = "当" if candidate in top[:won] else "落"
            if all(confirmed(text, Decimal(c["votes"]), c["result"]) for c in more):
                candidates += more
                smd_votes += votes
            elif (held, district) in BOOK_VOTES:
                book = BOOK_VOTES[held, district]
                if {c["name"] for c in more} != set(book):
                    raise ValueError(
                        f"{held} {district}: book names {sorted(book)} against {[c['name'] for c in more]}"
                    )
                for candidate in more:
                    candidate["votes"] = book[candidate["name"]]
                top = sorted(more, key=lambda c: Decimal(c["votes"]), reverse=True)
                for candidate in more:
                    candidate["result"] = "当" if candidate in top[:won] else "落"
                if not all(confirmed(text, Decimal(c["votes"]), c["result"]) for c in more):
                    raise ValueError(f"{held} {district}: book votes not found in the book text")
                candidates += more
                smd_votes += [
                    {
                        "held": held,
                        "district": district,
                        "place": DISTRICT_UNIT,
                        "order": c["order"],
                        "votes": c["votes"],
                    }
                    for c in more
                ]
            else:
                print(f"UNCONFIRMED {held} {district}: the municipal file disagrees with the book")
                UNCONFIRMED.append({"held": held, "district": district})
        allocation = seat_table(raw.cached(RAW, found.seats).path)
        for record in listed:
            if record["party"] == "合計":
                continue
            votes, won = allocation[record["party"]]
            if won != record["seats"] or votes != Decimal(record["total"]):
                raise ValueError(f"{held} {record['party']}: list table {record} against allocation {votes} {won}")
            parties.append(record)
            seats.append({"held": held, "party": record["party"], "seats": won})
        people += [row for row in ranked if row["party"] != "合計"]
    known = {*(row["party"] for row in parties), "無所属"}
    for candidate in candidates:
        if candidate["name"] in known and candidate["party"] not in known:
            candidate["name"], candidate["party"] = candidate["party"], candidate["name"]
    PARSED.mkdir(parents=True, exist_ok=True)
    for name, rows in (
        ("candidates", candidates),
        ("smd", smd_votes),
        ("parties", parties),
        ("people", people),
        ("pr", pr_places),
        ("pr_candidates", pr_candidates),
    ):
        pl.DataFrame(rows).write_parquet(PARSED / f"{name}-v{VERSION}.parquet")
    print(f"{sum(1 for row in DROPPED if 'place' in row)} counting districts left without list candidate votes")
    print(build())


TITLE = "参議院議員通常選挙結果調"
TYPOS = {
    "泰野市": "秦野市",
    "川口1": "川口市第1",
    "川口2": "川口市第2",
    **{f"揖斐川町{part}": f"揖斐川町({part})" for part in ("揖斐川", "谷汲", "春日", "久瀬", "藤橋", "坂内")},
}
SMD_RULES = {"formula": "sntv"}
PR_RULES = {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}}
SMD_SYSTEM = (
    "Each prefectural district elects its seats by single non-transferable vote: every voter names one candidate, "
    "and the candidates with the most votes win the seats. Half the House is up every three years, so a district "
    "elects half of its seats at each election. Tottori and Shimane, and Tokushima and Kochi, have formed one "
    "district each since 2016."
)
PR_SYSTEM = (
    "The national list tier fills its seats by D'Hondt on each party's total, which is the votes written for the "
    "party plus the votes written for its candidates. Within a party, seats go to the candidates with the most "
    "personal votes. Since 2019, a party may place candidates in a priority quota (特定枠) who fill its seats "
    "first, in the order the party fixed, whatever their personal votes; those candidates receive no personal "
    "votes."
)
SMD_METHOD = (
    "Candidate votes by counting district (開票区), from the ministry's tables by municipality, which the "
    "prefectural election commissions compile. Counting districts are municipalities or designated-city wards. "
    "Votes for candidates whose names other candidates share are apportioned (按分) and carry three decimals. "
    "The winners are the candidates with the most votes for the district's seats, and each candidate's total and "
    "result mark were checked against the ministry's 結果調 book."
)
PR_NATIONAL_METHOD = (
    "Party totals, party-only votes, personal votes, and seats from the ministry's national tables (党派別議席配分表 "
    "and 党派別名簿登載者別得票数、当選人数) in the 結果調 book. Personal votes for candidates whose names other "
    "candidates share are apportioned (按分). Candidates in a party's priority quota are listed with no votes."
)
PR_PLACE_METHOD = (
    "Votes by counting district from the ministry's tables by municipality: each party's total, its party-only "
    "votes, and each candidate's personal votes, matched to the national list by name. A candidate with no personal "
    "votes in a district has no value there. Where the party table counts the priority-quota (特定枠) candidates' "
    "votes with the party's own votes, those candidates have no values by district. Where a party's candidate sheet "
    "disagrees with its party table in a district and the totals cannot settle which is right, the district holds "
    "that party's candidate votes on the list as a whole, without a split by candidate. Where the party table "
    "leaves a district blank, the district holds the candidates' votes only."
)
PR_ENGINE = (
    "The declared seats differ from a plain D'Hondt count on the party totals. Declared, then D'Hondt alone: {moved}."
)
BOOK_CAVEAT = (
    "The ministry's workbook by counting district for this district stops at 99.4 percent of the count and its "
    "candidate totals disagree with the 結果調 book, so the candidate totals here are the book's, loaded at the "
    "district level only, with no counting-district values."
)
BY_ELECTION = (
    "{count} of this district's seats fill a vacancy for the remainder of a term, and the ministry's tables count "
    "them with the district's regular seats."
)


def codes_of(district: str) -> list[str]:
    return [f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1) if native in district]


KEY = f"{SOURCE}/all"
SUMMARY = (
    "Japan's House of Councillors regular elections 2010-2025 from the Ministry of Internal Affairs and "
    "Communications: prefectural district candidates and national list votes by counting district, with declared "
    "winners and seats"
)


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package jp.mic_hoc/all from the parsed tables. It reads the database, through Draft, so the rows it
    rebuilds keep the ids the changeset path gave them."""
    candidates = pl.read_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    smd = pl.read_parquet(PARSED / f"smd-v{VERSION}.parquet")
    parties = pl.read_parquet(PARSED / f"parties-v{VERSION}.parquet")
    people = pl.read_parquet(PARSED / f"people-v{VERSION}.parquet")
    pr = pl.read_parquet(PARSED / f"pr-v{VERSION}.parquet")
    pr_people = pl.read_parquet(PARSED / f"pr_candidates-v{VERSION}.parquet")
    english = dict(PREFECTURES)
    with connect() as conn:
        cs = Draft(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="JP")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        japan = cs.polity("JP")
        house = cs.key("jp.body", "house-of-councillors")
        if {japan, house} & cs.minted:
            raise ValueError("run jp.mlit and jp.diet first")
        source = cs.source(mic.PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=mic.PUBLISHER,
            title=TITLE,
            url=f"{ROOT}/sangiin/index.html",
            license=mic.LICENSE,
            license_url=mic.LICENSE_URL,
            attribution=mic.ATTRIBUTION,
        )
        smd_system = cs.key(f"{SOURCE}.electoral_system", "hoc-sntv")
        cs.add("electoral_system", id=smd_system, polity_id=japan, rules=SMD_RULES, rules_version=1)
        name(smd_system, "House of Councillors prefectural districts, single non-transferable vote")
        name(smd_system, "選挙区選出（単記非移譲式）", "ja", "native", False)
        cs.add("note", entity_id=smd_system, kind="methodology", body_md=SMD_SYSTEM)
        pr_system = cs.key(f"{SOURCE}.electoral_system", "hoc-pr")
        cs.add("electoral_system", id=pr_system, polity_id=japan, rules=PR_RULES, rules_version=1)
        name(pr_system, "House of Councillors national open lists by D'Hondt")
        name(pr_system, "比例代表選出（非拘束名簿式、ドント式）", "ja", "native", False)
        cs.add("note", entity_id=pr_system, kind="methodology", body_md=PR_SYSTEM)

        keyed: dict[tuple[str, str], UUID] = {}

        def party(text: str, held: str) -> UUID | None:
            found = party_key(text, held)
            if found is None:
                return None
            # A party this pipeline minted under its own key keeps it after jp.mic gives the label a Wikidata item;
            # joining it to the item's party is jp.mic_parties' merge, not a rewrite of these rows.
            local = (f"{mic.SOURCE}.party", party_label(text))
            if found[0] == "wikidata" and local in cs.own_keys:
                found = local
            if found not in keyed:
                entity = keyed[found] = cs.key(*found)
                if entity in cs.minted:
                    if found[0] == "wikidata":
                        raise ValueError(f"{found[1]} should already exist from jp.diet")
                    cs.add("party", id=entity, kind="party", polity_id=japan)
                    name(entity, found[1], "ja")
            return keyed[found]

        added: set[UUID] = set()

        def unit(scheme: str, code: str, parent: UUID | None, area: UUID, relation: str, polity: UUID) -> UUID:
            entity = cs.unit(scheme, code)
            if entity in cs.minted and entity not in added:
                added.add(entity)
                cs.add(
                    "reporting_unit",
                    id=entity,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=code,
                    parent_id=parent,
                    area_relation=relation,
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
            return entity

        def prefecture_unit(code: str) -> UUID:
            return unit("jp.prefecture", code, None, cs.area("jp.prefecture", code), "equals", cs.polity(f"JP-{code}"))

        # A district candidate's name comes from the earliest election from 2013 on: the 2010 tables, read from
        # their PDF layout, space some names differently.
        printed: dict[str, str] = {}
        for row in sorted(candidates.iter_rows(named=True), key=lambda row: (row["held"] < "2013", row["held"])):
            printed.setdefault(f"smd:{row['district']}:{plain(row['name'])}", row["name"])

        def person(key: str, text: str) -> UUID:
            entity = cs.key(f"{SOURCE}.person", key)
            if entity in cs.minted and entity not in added:
                added.add(entity)
                cs.add("person", id=entity)
                name(entity, printed.get(key, text), "ja")
            return entity

        nation = unit("jp.nation", "JP", None, cs.area("jp.nation", "JP"), "equals", japan)
        values: list[tuple[UUID, UUID, UUID, Decimal]] = []
        parent: dict[UUID, UUID] = {}
        legends: set[UUID] = set()
        special: set[UUID] = set()
        for number, (held, index_path, _) in ELECTIONS.items():
            if held not in set(candidates["held"]):
                continue
            day = date.fromisoformat(held)
            found = listing(number)
            for url in [found.index, found.book, found.seats, found.lists, found.totals]:
                cs.artifact(raw.cached(RAW, url), source)
            for page_url, files in found.pages:
                cs.artifact(raw.cached(RAW, page_url), source)
                for url in files:
                    cs.artifact(raw.cached(RAW, url), source)
            by_election = {district: extra for district, (_, extra) in sizes(book_for(number), held).items() if extra}
            places = Places(conn, day)

            def place_unit(prefectures: list[str], place: str, previous: str | None) -> tuple[UUID, str]:
                owner, _, label = place.rpartition("|")
                label = TYPOS.get(label, label)
                hit = [code for code in prefectures if owner and english.get(owner) and codes_of(owner) == [code]]
                resolved = []
                for code in hit or prefectures:
                    try:
                        resolved.append((code, *places.resolve(code, label, previous)))
                    except LookupError:
                        continue
                if len(resolved) != 1:
                    raise LookupError(f"{held} {prefectures} {place}: {len(resolved)} matches")
                code, scheme, area_code, piece = resolved[0]
                parent = prefecture_unit(code)
                polity = cs.polity(f"JP-{code}")
                area = cs.area(scheme, area_code)
                whole = unit(scheme, area_code, parent, area, "equals", polity)
                if piece is None:
                    return whole, area_code
                return unit(mic.PIECES, f"{held}:{area_code}:{piece}", whole, area, "within", polity), area_code

            election = cs.key(f"{SOURCE}.election", held)
            cs.add("election", id=election, polity_id=japan, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} Japanese House of Councillors election")
            name(election, f"第{number}回参議院議員通常選挙", "ja", "native", False)
            path = f"jp/{held}-sangiin"
            cs.add("slug", path=path, entity_id=election)

            for district, rows in candidates.filter(pl.col("held") == held).group_by("district", maintain_order=True):
                district = str(district[0])
                key = f"{held}:{district}"
                prefectures = codes_of(district)
                seats_won = int((rows["result"] == "当").sum())
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=house,
                    magnitude=seats_won,
                    electoral_system_id=smd_system,
                    default_result_kind="counted",
                )
                label = (
                    " and ".join(english[native] for native in district.split("・"))
                    if "・" in district
                    else english[district]
                )
                name(contest, f"{label} district")
                name(contest, f"{district}選挙区", "ja", "native", False)
                cs.add("slug", path=f"{path}/{slug(label)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=source,
                    status="active",
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=SMD_METHOD)
                if (held, district) in BOOK_VOTES:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=BOOK_CAVEAT)
                if district in by_election:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=BY_ELECTION.format(count=by_election[district]),
                    )
                options: dict[int, UUID] = {}
                for row in rows.iter_rows(named=True):
                    option = options[row["order"]] = cs.key(f"{SOURCE}.option", f"{key}:{row['order']}")
                    member = party(row["party"], held)
                    who = person(f"smd:{district}:{plain(row['name'])}", row["name"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=member,
                        ballot_position=row["order"],
                        ballot_label=row["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{key}:{row['order']}"),
                        person_id=who,
                        option_id=option,
                        role="head",
                        ballot_name=row["name"],
                        member_of_party_id=member,
                        is_independent=member is None,
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if row["result"] == "当" else "lost",
                        seats=1 if row["result"] == "当" else None,
                    )
                previous = None
                for row in smd.filter((pl.col("held") == held) & (pl.col("district") == district)).iter_rows(
                    named=True
                ):
                    if row["place"] == DISTRICT_UNIT:
                        values.append(
                            (result_set, prefecture_unit(prefectures[0]), options[row["order"]], Decimal(row["votes"]))
                        )
                        continue
                    place, previous = place_unit(prefectures, row["place"], previous)
                    values.append((result_set, place, options[row["order"]], Decimal(row["votes"])))

            national = parties.filter(pl.col("held") == held)
            size = int(national["seats"].sum())
            contest = cs.key(f"{SOURCE}.contest", f"{held}:national")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=house,
                magnitude=size,
                electoral_system_id=pr_system,
                default_result_kind="counted",
            )
            name(contest, "National proportional representation")
            name(contest, "比例代表選出", "ja", "native", False)
            cs.add("slug", path=f"{path}/national", entity_id=contest)
            national_set = cs.key(f"{SOURCE}.result_set", f"{held}:national")
            place_set = cs.key(f"{SOURCE}.result_set", f"{held}:national:places")
            local = pr.filter(pl.col("held") == held)
            local_total = sum((Decimal(v) for v in local["total"]), Decimal(0))
            declared_total = sum((Decimal(v) for v in national["total"]), Decimal(0))
            for result_set, default, method, share in (
                (national_set, True, PR_NATIONAL_METHOD, 1.0),
                (place_set, False, PR_PLACE_METHOD, float(local_total / declared_total)),
            ):
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=source,
                    status="active",
                    is_default=default,
                    completeness=share,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=method)
            totals = {row["party"]: Decimal(row["total"]) for row in national.iter_rows(named=True)}
            declared = {row["party"]: row["seats"] for row in national.iter_rows(named=True)}
            engine = dhondt(totals, size)
            if engine != declared:
                moved = ", ".join(f"{p} {declared[p]} and {engine[p]}" for p in totals if engine[p] != declared[p])
                cs.add("note", entity_id=national_set, kind="caveat", body_md=PR_ENGINE.format(moved=moved))
            options_by: dict[tuple[str, int | None], UUID] = {}
            for position, row in enumerate(national.iter_rows(named=True), 1):
                label = row["party"]
                member = party(label, held)
                listed = cs.key(f"{SOURCE}.option", f"{held}:national:{label}")
                cs.add(
                    "contest_option",
                    id=listed,
                    contest_id=contest,
                    kind="list",
                    party_id=member,
                    ballot_position=position,
                    ballot_label=party_label(label),
                )
                cs.add(
                    "outcome",
                    result_set_id=national_set,
                    option_id=listed,
                    kind="won" if row["seats"] else "lost",
                    seats=row["seats"],
                )
                options_by[label, -1] = listed
                legend = options_by[label, None] = cs.key(f"{SOURCE}.option", f"{held}:national:{label}:party")
                cs.add(
                    "contest_option",
                    id=legend,
                    contest_id=contest,
                    kind="legend",
                    parent_option_id=listed,
                    party_id=member,
                    ballot_position=None,
                    ballot_label=None,
                )
                parent[legend] = listed
                legends.add(legend)
                values.append((national_set, nation, listed, Decimal(row["total"])))
                values.append((national_set, nation, legend, Decimal(row["party_votes"])))
                for candidate in people.filter((pl.col("held") == held) & (pl.col("party") == label)).iter_rows(
                    named=True
                ):
                    option = options_by[label, candidate["number"]] = cs.key(
                        f"{SOURCE}.option", f"{held}:national:{label}:{candidate['number']}"
                    )
                    who = person(f"pr:{party_label(label)}:{plain(candidate['name'])}", candidate["name"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=listed,
                        party_id=member,
                        ballot_position=candidate["number"],
                        ballot_label=candidate["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{held}:national:{label}:{candidate['number']}"),
                        person_id=who,
                        option_id=option,
                        role="list_member",
                        ballot_name=candidate["name"],
                        member_of_party_id=member,
                        is_independent=False,
                    )
                    cs.add(
                        "outcome",
                        result_set_id=national_set,
                        option_id=option,
                        kind="elected" if candidate["result"] == "当" else "lost",
                        seats=None,
                    )
                    parent[option] = listed
                    if candidate["special"]:
                        special.add(option)
                    else:
                        values.append((national_set, nation, option, Decimal(candidate["votes"])))
            for row in local.iter_rows(named=True):
                code = codes_of(row["prefecture"])
                place, _ = place_unit(code, row["place"], None)
                values.append((place_set, place, options_by[row["party"], -1], Decimal(row["total"])))
                if row["party_votes"] is not None:
                    values.append((place_set, place, options_by[row["party"], None], Decimal(row["party_votes"])))
            for row in pr_people.filter((pl.col("held") == held) & (pl.col("votes").cast(pl.Float64) > 0)).iter_rows(
                named=True
            ):
                place, _ = place_unit(codes_of(row["prefecture"]), row["place"], None)
                values.append((place_set, place, options_by[row["party"], row["number"]], Decimal(row["votes"])))

        for (result_set, place, option), amount_ in list_parts(values, parent, legends, special).items():
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=place,
                option_id=option,
                channel=0,
                measure=mic.VOTES,
                count_round=0,
                flags=0,
                value=amount_,
            )
        return cs.write()
