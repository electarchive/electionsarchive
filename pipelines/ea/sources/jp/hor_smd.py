import csv
import html
import io
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import polars as pl
import shapefile
import shapely

from ea import package, raw
from ea.package import Draft
from ea.sources.jp import mlit
from ea.sources.jp.mlit import DESIGNATED, PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.hor_smd"
VERSION = "1"
KEY = f"{SOURCE}/all"
PARSED = DATA / "parsed" / SOURCE
PARTS = Path(__file__).with_name("hor_smd_parts.tsv")
LAW_TITLE = (
    "Public Offices Election Act (Act No. 100 of 1950), annex 1: House of Representatives single-member districts"
)
CREDIT = (
    "公職選挙法別表第一、「国土数値情報（行政区域データ）」（国土交通省）、"
    "「国勢調査 小地域境界データ」（総務省統計局）、「大字・町丁目レベル位置参照情報」（国土交通省）、"
    "「街区レベル位置参照情報」（国土交通省）を加工して作成"
)
LAW_API = "https://laws.e-gov.go.jp/api/2/law_data/325AC1000000100_{}?response_format=xml"
SHUGIIN = "https://www.shugiin.go.jp/internet/itdb_housei.nsf/html/{}.htm"
ISJ = "https://nlftp.mlit.go.jp/isj/dls/data/{version}/{prefecture}000-{version}.zip"
N03 = {
    "1920": "N03-1920/N03-200101",
    "1950": "N03-1950/N03-501001",
    "1955": "N03-1955/N03-551001",
    "1960": "N03-1960/N03-601001",
    "1965": "N03-1965/N03-651001",
    "1970": "N03-1970/N03-701001",
    "1995": "N03-1995/N03-951001",
    "2000": "N03-2000/N03-001001",
}
SUFFIX = re.compile(r"(町|新|[0-9]+丁目)")
ESTAT = (
    "https://www.e-stat.go.jp/gis/statmap-search/data?dlserveyId=A00200521{year}"
    "&code={prefecture:02d}&coordSys=1&format=shape&downloadType=5&datum={datum}"
)


@dataclass(frozen=True)
class Table:
    revision: str
    effective: date
    n03: str
    census: int
    isj: str


TABLES = {
    "1994": Table("houritsu/13119941125104", date(1994, 12, 25), "1995", 2000, "03.0b"),
    "2002": Table("housei/15420020731095", date(2002, 8, 31), "2000", 2000, "03.0b"),
    "2013": Table("20170101_428AC0000000094", date(2013, 7, 28), "2013", 2010, "07.0b"),
    "2017": Table("20170716_428AC1000000049", date(2017, 7, 16), "2017", 2015, "11.0b"),
    "2022": Table("20221228_504AC0000000089", date(2022, 12, 28), "2022", 2020, "16.0b"),
}
OFFICES = {"2013": "2014"}
ERRATA = {("2013", "02", 2, "三沢市"): "むつ市", ("2013", "12", 12, "館山市"): "木更津市"}
ISLANDS = {"1336": "大島支庁", "1338": "三宅支庁", "1340": "八丈支庁", "1342": "小笠原支庁"}
NUMERALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def kanji(text: str) -> int:
    tens, _, ones = text.rpartition("十")
    if "十" not in text:
        return NUMERALS[text]
    return (NUMERALS[tens] if tens else 1) * 10 + (NUMERALS[ones] if ones else 0)


def numeral(text: str) -> int:
    """A number written in digits or in kanji up to the thousands, such as 千三百十三."""
    text = unicodedata.normalize("NFKC", text)
    if text.isdigit():
        return int(text)
    total = current = 0
    for char in text:
        if char in NUMERALS:
            current = NUMERALS[char]
        else:
            total += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            current = 0
    return total + current


def law_url(table: str) -> str:
    revision = TABLES[table].revision
    return SHUGIIN.format(revision) if "/" in revision else LAW_API.format(revision)


def sentences(element: ET.Element) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for child in element:
        if re.fullmatch(r"Item|Subitem\d+", child.tag):
            sentence = child.find(f"{child.tag}Sentence")
            depth = 0 if child.tag == "Item" else int(child.tag.removeprefix("Subitem"))
            rows.append((depth, "".join(sentence.itertext()).strip() if sentence is not None else ""))
            rows.extend(sentences(child))
        elif child.tag == "TableRow":
            cells = ["".join(column.itertext()).strip() for column in child.findall("TableColumn")]
            filled = [(index, text) for index, text in enumerate(cells) if text]
            if len(filled) != 1:
                raise ValueError(f"table row with {len(filled)} filled cells: {cells}")
            index, text = filled[0]
            rows.append((0 if len(cells) == 1 else index - 1, text))
        elif child.tag in ("TableStruct", "Table"):
            rows.extend(sentences(child))
    return rows


SPACE = " 　"
TYPOS = {
    ("01", "タ張市"): "夕張市",
    ("23", "幡豆市"): "幡豆郡",
    ("38", "字和島市"): "宇和島市",
    ("41", "神崎郡"): "神埼郡",
    ("44", "白杵市"): "臼杵市",
    ("44", "農後高田市"): "豊後高田市",
    ("46", "乗水市"): "垂水市",
}
ALIASES = {
    ("1994", "08", "勝田市"): "ひたちなか市",
    ("1994", "08", "那珂湊市"): "ひたちなか市",
    ("1994", "13", "秋川市"): "あきる野市",
    ("2002", "08", "潮来市"): "潮来町",
    ("2002", "08", "守谷市"): "守谷町",
    ("2002", "11", "大里町"): "大里村",
    ("2002", "12", "白井市"): "白井町",
    ("2002", "12", "富里市"): "富里町",
    ("2002", "25", "栗東市"): "栗東町",
    ("2002", "47", "豊見城市"): "豊見城村",
}
COUNTIES = {"1994": {"08222": "鹿島郡", "23230": "愛知郡"}}
MERGED = {
    "1994": {("01", "豊平区"): ("豊平区", "清田区")},
    "2002": {
        ("11", "さいたま市"): ("浦和市", "大宮市", "与野市"),
        ("13", "西東京市"): ("田無市", "保谷市"),
        ("37", "さぬき市"): ("津田町", "大川町", "志度町", "寒川町", "長尾町"),
        ("47", "久米島町"): ("仲里村", "具志川村"),
    },
}
OFFICIAL = re.compile(
    r"(?:別表第一|同表)(?P<pref>北海道|..?.?[都道府県])?(?P<items>(?:(?:、|及び)?第[一二三四五六七八九十]+区の項)+)"
)
Lines = list[list[int | str]]


class Tokens(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tokens: list[tuple[str, str | list[str]]] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: str | None = None
        self.text = ""

    def flush(self) -> None:
        text = html.unescape(self.text).replace("\n", "").replace("\r", "")
        self.text = ""
        if self.row is not None:
            if self.cell is not None:
                self.cell += text
        elif text.strip(SPACE):
            self.tokens.append(("line", text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("td", "tr", "table", "dir", "p", "br"):
            self.flush()
        if tag == "td":
            self.cell = ""
        elif tag == "tr":
            self.row, self.cell = [], None
        elif tag == "table":
            self.table = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "tr", "dir", "p"):
            self.flush()
        if tag == "td" and self.row is not None and self.cell is not None:
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr":
            if self.table is not None and self.row is not None:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tokens.append(
                ("block", [cell for row in self.table for cell in row if cell.strip(SPACE) not in ("「", "」", "")])
            )
            self.table = None

    def handle_data(self, data: str) -> None:
        self.text += data


def tokens(url: str) -> list[tuple[str, str | list[str]]]:
    parser = Tokens()
    parser.feed(raw.cached(SOURCE, url).path.read_bytes().decode("cp932"))
    parser.flush()
    return parser.tokens


def bare(text: str) -> str:
    return re.sub(f"[{SPACE}]", "", text)


def indented(texts: list[str]) -> Lines:
    return [[len(text) - len(text.lstrip(SPACE)), bare(text)] for text in texts]


def districts(texts: list[str], prefecture: str = "") -> dict[tuple[str, int], Lines]:
    codes = {native: f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1)}
    table: dict[tuple[str, int], Lines] = {}
    key: tuple[str, int] | None = None
    last = 0
    for text in texts:
        depth, name = len(text) - len(text.lstrip(SPACE)), bare(text)
        name = TYPOS.get((key[0] if key else prefecture, name), name).replace("高埆", "高埇")
        if name.isascii() and name.isdigit():
            continue
        if name in codes and depth > 1:
            prefecture = codes[name]
        elif found := re.fullmatch(r"第([一二三四五六七八九十]+)区", name):
            key, last = (prefecture, kanji(found[1])), 0
            table[key] = []
        elif key is None:
            raise ValueError(f"line before any district: {text}")
        else:
            if depth == 0:
                depth = last + 1
            else:
                if (
                    "、" not in name
                    and re.search(r"(市|郡|支庁管内)$", name)
                    and not name.startswith(("第", "さいたま市"))
                ):
                    depth = 1
                last = depth
            table[key].append([depth, name])
    return table


def quoted(text: str, position: int) -> tuple[str, int]:
    depth = 0
    for index in range(position, len(text)):
        depth += {"「": 1, "」": -1}.get(text[index], 0)
        if depth == 0:
            return text[position + 1 : index], index + 1
    raise ValueError(text[position:])


class Editor:
    def __init__(self, table: dict[tuple[str, int], Lines], blocks: list[Lines]) -> None:
        self.table = table
        self.blocks = blocks

    def value(self, text: str) -> Lines:
        return [list(line) for line in self.blocks[int(text[1:])]] if text.startswith("\x00") else [[0, text]]

    def locate(self, keys: list[tuple[str, int]], text: str) -> tuple[tuple[str, int], int, int, bool]:
        target = self.value(text)
        hits: list[tuple[tuple[str, int], int, int, bool]] = []
        for key in keys:
            item = self.table[key]
            for start in range(len(item) - len(target) + 1):
                window = item[start : start + len(target)]
                if [name for _, name in window] == [name for _, name in target] and (
                    len({int(have) - int(want) for (have, _), (want, _) in zip(window, target, strict=True)}) == 1
                ):
                    hits.append((key, start, len(target), True))
        if not hits and len(target) == 1:
            hits = [
                (key, index, 1, False)
                for key in keys
                for index, (_, name) in enumerate(self.table[key])
                if str(target[0][1]) in str(name)
            ]
        if len(hits) != 1:
            raise ValueError(f"{keys} {text[:40]!r}: {len(hits)} hits")
        return hits[0]

    def replace(self, keys: list[tuple[str, int]], old: str, new: str | None) -> None:
        key, start, length, whole = self.locate(keys, old)
        item = self.table[key]
        if whole:
            base = int(item[start][0]) - int(self.value(old)[0][0])
            item[start : start + length] = (
                [] if new is None else [[base + int(depth), name] for depth, name in self.value(new)]
            )
        else:
            item[start][1] = str(item[start][1]).replace(str(self.value(old)[0][1]), new or "", 1)
            if not item[start][1]:
                del item[start]

    def after(self, keys: list[tuple[str, int]], anchor: str, extra: str) -> None:
        try:
            key, start, _, _ = self.locate(keys, anchor)
        except ValueError:
            if len(keys) != 1:
                raise
            line = max(self.table[keys[0]], key=lambda line: str(line[1]).count("、"))
            line[1] = f"{line[1]}、{anchor}{extra}"
            print(f"{keys[0]}: {anchor} missing from the amended text, appended with its additions")
            return
        self.table[key][start][1] = str(self.table[key][start][1]).replace(anchor, anchor + extra, 1)

    def edits(self, text: str, position: int, keys: list[tuple[str, int]]) -> int:
        while position < len(text):
            while text.startswith("、", position):
                position += 1
            if text.startswith(("改め", "加え", "削り"), position):
                position += 2
                if text.startswith("る。", position):
                    return position + 2
                continue
            if not text.startswith("「", position):
                return position
            value, position = quoted(text, position)
            olds = [value]
            while text.startswith("及び「", position):
                value, position = quoted(text, position + 2)
                olds.append(value)
            while text.startswith("、「", position) and re.match(
                r"、「[^「」]*」(、「[^「」]*」)*(及び「[^「」]*」)?を削", text[position:]
            ):
                value, position = quoted(text, position + 1)
                olds.append(value)
                if text.startswith("及び「", position):
                    value, position = quoted(text, position + 2)
                    olds.append(value)
            if text.startswith("を削", position):
                for old in olds:
                    self.replace(keys, old, None)
                position += 3 if text.startswith("を削り", position) else 2
            elif text.startswith("を「", position):
                new, position = quoted(text, position + 1)
                position += 1
                for old in olds:
                    self.replace(keys, old, new)
            elif text.startswith("の下に「", position):
                extra, position = quoted(text, position + 3)
                position += 1
                self.after(keys, olds[0], extra)
            else:
                raise ValueError(f"edit {text[position : position + 40]!r}")
        return position

    def run(self, text: str) -> None:
        codes = {native: f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1)}
        prefecture = ""
        pending: list[tuple[str, str, list[int]]] = []
        for segment in (segment for segment in re.split(r"(\x01.*?\x02)", text) if segment):
            if segment.startswith("\x01"):
                action, code, numbers = pending.pop(0)
                new = districts([line for group in json.loads(segment[1:-1]) for line in group], code)
                if action == "replace" and sorted(number for _, number in new) != sorted(numbers):
                    raise ValueError(f"{code} replace {numbers} with {list(new)}")
                self.table.update(new)
                continue
            position = 0
            while position < len(segment):
                match = OFFICIAL.search(segment, position)
                if not match:
                    if "別表第一の改正規定中" in segment[position:]:
                        position = segment.index("別表第一の改正規定中", position) + len("別表第一の改正規定中")
                        position = self.edits(segment, position, list(self.table))
                        continue
                    break
                if match["pref"]:
                    prefecture = codes[match["pref"]]
                numbers = [kanji(found) for found in re.findall(r"第([一二三四五六七八九十]+)区", match["items"])]
                keys = [(prefecture, number) for number in numbers]
                position = match.end()
                rest = segment[position:]
                if rest.startswith("中"):
                    position = self.edits(segment, position + 1, keys)
                elif rest.startswith("を次のように改める"):
                    pending.append(("replace", prefecture, numbers))
                    position += len("を次のように改める")
                elif rest.startswith("の次に次のように加える"):
                    pending.append(("add", prefecture, numbers))
                    position += len("の次に次のように加える")
                elif rest.startswith("を削る"):
                    for key in keys:
                        del self.table[key]
                    position += len("を削る")
                else:
                    raise ValueError(f"unknown action {rest[:40]!r}")


def amend(table: dict[tuple[str, int], Lines], stream: list[tuple[str, str | list[str]]]) -> None:
    blocks: list[Lines] = []
    text = ""
    content: list[list[str]] = []
    for kind, value in stream:
        if isinstance(value, list):
            blocks.append(indented(value))
            text += f"「\x00{len(blocks) - 1}」"
            continue
        name = bare(value)
        if text.endswith(("改める。", "加える。")) and not name.startswith(("別表", "（", "附則", "第一条", "第二条")):
            if re.fullmatch(r"第[一二三四五六七八九十]+区", name) or not content:
                content.append([])
            content[-1].append(value)
            continue
        if content:
            text += "\x01" + json.dumps(content, ensure_ascii=False) + "\x02"
            content = []
        if name.startswith(("（施行期日）", "附則")):
            break
        text += value.strip(SPACE)
    if content:
        text += "\x01" + json.dumps(content, ensure_ascii=False) + "\x02"
    Editor(table, blocks).run(text)


def statute(table: str) -> list[tuple[str, int, tuple[str, ...]]]:
    stream = tokens(SHUGIIN.format(TABLES["1994"].revision))
    start = next(index for index, (_, value) in enumerate(stream) if "別表第一（第十三条関係）" in value)
    texts: list[str] = []
    for kind, value in stream[start + 1 :]:
        name = bare(str(value))
        if isinstance(value, list) or (
            ("改め" in name or "加える" in name or name.startswith("附則")) and "、" not in name[:6]
        ):
            break
        texts.append(value)
    found = districts(texts)
    second = next(index for index, (_, value) in enumerate(stream) if str(value).startswith("第二条"))
    amend(found, stream[second + 1 :])
    if table != "1994":
        stream = tokens(SHUGIIN.format(TABLES[table].revision))
        amend(
            found, stream[next(index for index, (_, value) in enumerate(stream) if "次のように改正する" in value) + 1 :]
        )
    entries: list[tuple[str, int, tuple[str, ...]]] = []
    for prefecture, district in sorted(found):
        path: list[str] = []
        for depth, name in found[prefecture, district]:
            path = [*path[: int(depth) - 1], str(name)]
            entries.append((prefecture, district, tuple(path)))
    return entries


def annex(table: str) -> list[tuple[str, int, tuple[str, ...]]]:
    if "/" in TABLES[table].revision:
        return statute(table)
    root = ET.parse(raw.cached(SOURCE, law_url(table)).path).getroot()
    appendix = next(
        element for element in root.iter("AppdxTable") if element.findtext("AppdxTableTitle", "").strip() == "別表第一"
    )
    prefectures = {native: f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1)}
    entries: list[tuple[str, int, tuple[str, ...]]] = []
    native = prefecture = ""
    district = 0
    path: list[str] = []
    for depth, text in sentences(appendix):
        if text in prefectures:
            native, prefecture, district, path = text, prefectures[text], 0, []
        elif found := re.fullmatch(r"第([一二三四五六七八九十]+)区", text):
            district, path = kanji(found[1]), []
        elif text:
            if (
                depth == 0
                and path
                and text.endswith("管内")
                and not text.startswith(native)
                and not re.search(r"(支庁|振興局)管内$", text)
            ):
                depth = 1
            elif text.endswith("郡"):
                depth = 0
            elif depth == 0 and len(path) == 1 and path[0].endswith("郡") and text[-1] in "町村":
                depth = 1
            path = [*path[:depth], text]
            if (table, prefecture, district, text) in ERRATA and (prefecture, district, tuple(path)) in entries:
                path[-1] = ERRATA[table, prefecture, district, text]
            entries.append((prefecture, district, tuple(path)))
    return entries


def n03(vintage: str, prefecture: str) -> str:
    return f"{mlit.BASE}/{N03[vintage]}_{prefecture}_GML.zip"


def bases(vintage: str) -> list[str]:
    return [n03(vintage, f"{number:02d}") for number in range(1, 48)] if vintage in N03 else mlit.urls(vintage)


def formers() -> list[tuple[str, str]]:
    return sorted(
        {
            (row["prefecture"], name.partition("@")[2] or "2000")
            for row in rows()
            if row["kind"] == "former"
            for name, _ in items(row["names"])
        }
    )


def fetch() -> None:
    for table, spec in TABLES.items():
        raw.fetch(SOURCE, law_url(table), suffix=".htm" if "/" in spec.revision else ".xml")
    for vintage in sorted({spec.n03 for spec in TABLES.values()} & set(N03)):
        for url in bases(vintage):
            raw.fetch(SOURCE, url, suffix=".zip")
    for prefecture in [f"{number:02d}" for number in range(1, 48)]:
        for year in sorted({spec.census for spec in TABLES.values()}):
            raw.fetch(SOURCE, estat_url(year, prefecture), suffix=".zip")
        for version in sorted({spec.isj for spec in TABLES.values()}):
            raw.fetch(SOURCE, ISJ.format(version=version, prefecture=prefecture), suffix=".zip")
    for prefecture, vintage in formers():
        raw.fetch(SOURCE, n03(vintage, prefecture), suffix=".zip")
    for table in TABLES:
        for prefecture in restricted(table):
            raw.fetch(SOURCE, ISJ.format(version=block_version(table), prefecture=prefecture), suffix=".zip")


def estat_url(year: int, prefecture: str) -> str:
    return ESTAT.format(year=year, prefecture=int(prefecture), datum=2000 if year <= 2010 else 2011)


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for old, new in (
        ("ヶ", "ケ"),
        ("ヵ", "カ"),
        ("竃", "竈"),
        ("繩", "縄"),
        ("條", "条"),
        ("頚", "頸"),
        ("糟", "粕"),
        ("之", "ノ"),
        ("桧", "檜"),
        ("舘", "館"),
        ("冨", "富"),
        ("與", "与"),
    ):
        text = text.replace(old, new)
    text = re.sub(
        r"(?<![一二三四五六七八九十])([一二三四五六七八九]?十[一二三四五六七八九]?|[一二三四五六七八九])丁目",
        lambda found: f"{kanji(found[1])}丁目",
        text,
    )
    return re.sub(r"^(大字|字)", "", text.strip())


def offices(text: str) -> str:
    return re.sub(r"(総合振興局|振興局|支庁)$", "", norm(text))


def items(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    depth = 0
    current = ""
    for char in text + "、":
        depth += {"（": 1, "）": -1}.get(char, 0)
        if char == "、" and depth == 0:
            name, _, note = current.partition("（")
            for part in name.split("及び"):
                if part:
                    found.append((part, note.rstrip("）") if part == name.split("及び")[-1] else ""))
            current = ""
        else:
            current += char
    return found


FIGURES = "0-9一二三四五六七八九十百千"
ADDRESS = re.compile(rf"([{FIGURES}]+)(?:番地|番)?(?:([{FIGURES}]+)号?)?")
Address = tuple[int, int | None]


@dataclass(frozen=True)
class Blocks:
    """The blocks (番) or lots (番地) an annex note names within one chome or oaza, as inclusive ranges of
    (number, sub-number) pairs. A sub-number is the 号 of a block or the branch of a lot; None covers the whole
    block or lot. `only` is True for a note that keeps the area to those blocks (に限る) and False for one that
    takes them out of it (を除く)."""

    ranges: tuple[tuple[Address, Address], ...]
    only: bool

    def holds(self, number: int, sub: int | None) -> bool:
        """Whether the note names the block or lot. A block the note names only in part, by some of its 号, counts
        as named: the location reference points stop at the block."""
        for (low, low_sub), (high, high_sub) in self.ranges:
            if sub is None:
                if low <= number <= high:
                    return True
            elif (low, low_sub or 0) <= (number, sub) <= (high, 10**9 if high_sub is None else high_sub):
                return True
        return False


def address(text: str) -> Address | None:
    found = ADDRESS.fullmatch(text)
    if not found:
        return None
    return numeral(found[1]), None if found[2] is None else numeral(found[2])


def blocks(note: str) -> Blocks | None:
    """Read a note such as 一番から二十六番まで及び二十八番に限る or 27・29～41番に限る; None when the note is not a
    list of blocks or lots, such as 字カキヲジ原に限る."""
    text = unicodedata.normalize("NFKC", note).strip().rstrip("。")
    if text.endswith("に限る"):
        only, text = True, text.removesuffix("に限る")
    elif text.endswith("を除く"):
        only, text = False, text.removesuffix("を除く")
    else:
        return None
    ranges: list[tuple[Address, Address]] = []
    for part in re.split("、|及び|・", text.replace("~", "から")):
        low, _, high = part.removesuffix("まで").partition("から")
        first, last = address(low), address(high or low)
        if first is None or last is None:
            return None
        ranges.append((first, last))
    return Blocks(tuple(ranges), only) if ranges else None


def lot(text: str) -> Address | None:
    found = re.fullmatch(r"(\d+)(?:-(\d+))?", unicodedata.normalize("NFKC", text))
    return None if found is None else (int(found[1]), None if found[2] is None else int(found[2]))


def carve(
    polygon: shapely.Geometry, base: int, rules: list[tuple[int, Blocks]], remainder: int, sites: pl.DataFrame
) -> tuple[list[tuple[shapely.Geometry, int]], str | None]:
    """Cut one census small area along the blocks its annex notes name. Each block point of the location reference
    information takes the district its notes give it, or the area's own district, and the area is split into the
    Voronoi cells of those points. Returns the pieces with their districts, and a note when the area stays whole."""
    others = [district for district, rule in rules if rule.only]
    labels: dict[tuple[float, float], list[int]] = {}
    for x, y, block in sites.select("x", "y", "block").iter_rows():
        found = lot(block)
        if found is None:
            continue
        label = base
        for district, rule in rules:
            if rule.only and rule.holds(*found):
                label = district
            elif not rule.only and district == base and rule.holds(*found):
                label = next((other for other in others if other != district), remainder)
        labels.setdefault((x, y), []).append(label)
    chosen = {
        point: max(sorted(set(found)), key=lambda label: (found.count(label), label == base))
        for point, found in labels.items()
    }
    if not chosen:
        return [(polygon, base)], "no block points"
    if len(set(chosen.values())) == 1:
        label = next(iter(chosen.values()))
        return [(polygon, label)], None if label != base else f"{len(labels)} block points, all in district {base}"
    points = shapely.points(list(chosen))
    cells = shapely.get_parts(shapely.voronoi_polygons(shapely.multipoints(points), extend_to=polygon))
    tree = shapely.STRtree(points)
    owners = dict(zip(*tree.query(cells, predicate="contains"), strict=True))
    districts = list(chosen.values())
    pieces: list[tuple[shapely.Geometry, int]] = []
    for district in sorted(set(districts)):
        members = [cell for index, cell in enumerate(cells) if districts[owners[index]] == district]
        piece = shapely.intersection(polygon, shapely.union_all(members))
        if not piece.is_empty:
            pieces.append((piece, district))
    return pieces, None


def fields(record: shapefile._Record | None) -> dict[str, str]:
    return {key: str(value or "").strip() for key, value in (record.as_dict() if record is not None else {}).items()}


@dataclass
class Municipality:
    code: str
    parent: str
    name: str
    subprefecture: str
    county: str
    city: bool


def layers(vintage: str) -> Path:
    return (PARSED if vintage in N03 else mlit.PARSED) / f"n03_{vintage}.parquet"


def municipalities(vintage: str) -> dict[str, Municipality]:
    frame = pl.read_parquet(layers(vintage), columns=["layer", "code", "parent", "name"])
    attributes: dict[str, tuple[str, str]] = {}
    for url in [*bases(vintage), *bases(OFFICES.get(vintage, vintage))]:
        source = SOURCE if vintage in N03 else mlit.SOURCE
        with zipfile.ZipFile(raw.cached(source, url).path) as archive:
            dbf = next(name for name in archive.namelist() if name.endswith(".dbf"))
            reader = shapefile.Reader(dbf=io.BytesIO(archive.read(dbf)), encoding="cp932")
            for record in reader.iterRecords():
                row = fields(record)
                attributes[row["N03_007"]] = (row["N03_002"], row["N03_003"])
    names = dict(
        pl.read_parquet(mlit.PARSED / f"n03_{OFFICES[vintage]}.parquet", columns=["code", "name"]).iter_rows()
        if vintage in OFFICES
        else []
    )
    found: dict[str, Municipality] = {}
    for layer, code, parent, name in frame.filter(pl.col("layer").is_in(["municipality", "ward"])).iter_rows():
        name = names.get(code, name)
        subprefecture, county = attributes.get(mlit.RECODED.get(code, code), attributes.get(code, ("", "")))
        county = county or ISLANDS.get(code[:4], "")
        found[code] = Municipality(code, parent, name, subprefecture, county, layer == "ward" or name[-1] in "市区")
    return found


def shapes(vintage: str) -> dict[str, shapely.Geometry]:
    frame = pl.read_parquet(layers(vintage)).filter(pl.col("layer").is_in(["municipality", "ward", "prefecture"]))
    return {
        ("P" + code if layer == "prefecture" else code): shapely.from_wkb(geom)
        for layer, code, geom in frame.select("layer", "code", "geom").iter_rows()
    }


def small_areas(year: int, prefecture: str) -> pl.DataFrame:
    rows: list[tuple[str, str, bytes]] = []
    with zipfile.ZipFile(raw.cached(SOURCE, estat_url(year, prefecture)).path) as archive:
        stem = next(name for name in archive.namelist() if name.endswith(".shp")).removesuffix(".shp")
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{stem}.shp")),
            shx=io.BytesIO(archive.read(f"{stem}.shx")),
            dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
            encoding="cp932",
        )
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            row = fields(record)
            if shape is None or row["HCODE"] != "8101" or not row["S_NAME"]:
                continue
            geometry = shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__))
            rows.append((row["PREF"] + row["CITY"], row["S_NAME"], shapely.to_wkb(geometry)))
    return pl.DataFrame(rows, schema={"unit": pl.String, "name": pl.String, "geom": pl.Binary}, orient="row")


def rows() -> list[dict[str, str]]:
    with PARTS.open(encoding="utf-8") as file:
        return list(csv.DictReader(file, delimiter="\t"))


def parts(table: str) -> dict[tuple[str, str], list[dict[str, str]]]:
    found: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows():
        if row["table"] == table:
            found.setdefault((row["prefecture"], row["unit"]), []).append(row)
    return found


def points(version: str, prefecture: str) -> pl.DataFrame:
    with zipfile.ZipFile(raw.cached(SOURCE, ISJ.format(version=version, prefecture=prefecture)).path) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".csv"))
        frame = pl.read_csv(archive.read(name).decode("cp932").encode(), infer_schema=False)
    return frame.select(
        pl.col("市区町村コード").alias("unit"),
        pl.col("大字町丁目名").alias("name"),
        pl.col("経度").cast(pl.Float64).alias("x"),
        pl.col("緯度").cast(pl.Float64).alias("y"),
    )


def block_version(table: str) -> str:
    return TABLES[table].isj.replace("b", "a")


def block_points(version: str, prefecture: str) -> pl.DataFrame:
    """The block-level location reference points (街区レベル位置参照情報): one or more points for each block (街区) of
    the areas with residential addressing and for each lot (地番) elsewhere."""
    with zipfile.ZipFile(raw.cached(SOURCE, ISJ.format(version=version, prefecture=prefecture)).path) as archive:
        name = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
        frame = pl.read_csv(archive.read(name).decode("cp932").encode(), infer_schema=False)
    return frame.select(
        pl.col("市区町村名").alias("city"),
        # The column is 大字・丁目名, 大字・町丁目名, or 大字・町丁目, depending on the release.
        pl.col(next(column for column in frame.columns if column.startswith("大字"))).alias("name"),
        pl.col("街区符号・地番").alias("block"),
        pl.col("経度").cast(pl.Float64).alias("x"),
        pl.col("緯度").cast(pl.Float64).alias("y"),
    ).unique(maintain_order=True)


def restricted(table: str) -> list[str]:
    """The prefectures whose split units have annex notes that name blocks or lots, so their block points are
    needed."""
    found: set[str] = set()
    for prefecture, _, path in annex(table):
        if any(blocks(note) for _, note in items(path[-1]) if note):
            found.add(prefecture)
    for row in rows():
        if row["table"] == table and row["kind"] == "chome" and any(blocks(note) for _, note in items(row["names"])):
            found.add(row["prefecture"])
    return sorted(found)


def nearby(located: pl.DataFrame, code: str, shape: shapely.Geometry) -> pl.DataFrame:
    coded = located.filter(pl.col("unit") == code)
    if coded.height:
        return coded
    return located.filter(pl.Series(shapely.contains_xy(shape, located["x"].to_numpy(), located["y"].to_numpy())))


def former(prefecture: str, vintage: str) -> dict[str, shapely.Geometry]:
    found: dict[str, list[shapely.Geometry]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, n03(vintage, prefecture)).path) as archive:
        stem = next(name for name in archive.namelist() if name.endswith(".shp")).removesuffix(".shp")
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{stem}.shp")),
            shx=io.BytesIO(archive.read(f"{stem}.shx")),
            dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
            encoding="cp932",
        )
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            if shape is not None and shape.shapeType != shapefile.NULL:
                row = fields(record)
                name = row["N03_004"] or row["N03_003"]
                found.setdefault(f"{norm(name)}@{vintage}", []).append(shapely.geometry.shape(shape.__geo_interface__))
    return {name: shapely.union_all(shapely.make_valid(parts_)) for name, parts_ in found.items()}


def leaves(entries: list[tuple[str, int, tuple[str, ...]]]) -> list[tuple[str, int, tuple[str, ...]]]:
    return [
        entry
        for index, entry in enumerate(entries)
        if not entry[2][0].startswith("この表中")
        and not (index + 1 < len(entries) and entries[index + 1][2][: len(entry[2])] == entry[2])
    ]


def plan(
    table: str, towns: dict[str, Municipality]
) -> tuple[dict[tuple[str, int], list[str]], dict[str, list[tuple[int, str]]]]:
    natives = {f"{number:02d}": native for number, (native, _) in enumerate(PREFECTURES, 1)}
    wholes: dict[tuple[str, int], list[str]] = {}
    splits: dict[str, list[tuple[int, str]]] = {}
    for prefecture, district, path in leaves(annex(table)):
        local = [town for town in towns.values() if town.code[:2] == prefecture]
        root = path[0]

        def named(town: Municipality, name: str) -> bool:
            return norm(town.name) == norm(ALIASES.get((table, prefecture, name), name))

        def one(found: list[Municipality], label: str = root) -> Municipality:
            if len(found) != 1:
                raise ValueError(f"{table} {prefecture} {district} {path}: {label} matches {len(found)}")
            return found[0]

        codes: list[str] = []
        if root.endswith("管内"):
            office = root.removeprefix(natives[prefecture]).removesuffix("管内")
            members = [
                town
                for town in local
                if offices(office) in (offices(town.subprefecture), offices(town.county)) and not town.city
            ]
            if len(path) == 2:
                members = [one([town for town in members if named(town, path[1])], path[1])]
            codes = [town.code for town in members]
            if not codes or len(path) > 2:
                raise ValueError(f"{table} {prefecture} {district} {path}: subprefecture")
        elif root.endswith("郡"):
            members = [town for town in local if norm(town.county) == norm(root)]
            if len(path) > 2:
                town = one([town for town in members if named(town, path[1])], path[1])
                splits.setdefault(town.code, []).append((district, path[-1]))
            else:
                codes = (
                    [one([town for town in members if named(town, path[1])], path[1]).code]
                    if len(path) > 1
                    else [town.code for town in members]
                )
        else:
            name, _, excluded = root.partition("（")
            town = one([town for town in local if named(town, name) and town.parent == prefecture])
            if excluded:
                splits.setdefault(town.code, []).append((district, root))
            elif town.code in DESIGNATED:
                wards = [ward for ward in local if ward.parent == town.code]
                if len(path) == 1:
                    codes = [ward.code for ward in wards]
                else:
                    name, _, excluded = path[1].partition("（")
                    ward = one([ward for ward in wards if named(ward, name)], path[1])
                    if excluded:
                        splits.setdefault(ward.code, []).append((district, path[1]))
                    elif len(path) == 2:
                        codes = [ward.code]
                    else:
                        splits.setdefault(ward.code, []).append((district, path[-1]))
            elif len(path) == 1:
                codes = [town.code]
            else:
                splits.setdefault(town.code, []).append((district, path[-1]))
        known = wholes.setdefault((prefecture, district), [])
        known.extend(code for code in codes if code not in known)
    return wholes, splits


def assign(
    table: str,
    unit: Municipality,
    described: list[tuple[int, str]],
    areas: pl.DataFrame,
    located: pl.DataFrame,
    olds: dict[str, shapely.Geometry],
    sites: pl.DataFrame | None = None,
) -> tuple[list[int], int, list[str], dict[int, list[tuple[shapely.Geometry, int]]]]:
    curated = parts(table).get((unit.code[:2], unit.name), [])
    lines: list[tuple[int, str, str]] = (
        [
            (int(row["district"]), "chome" if row["kind"] == "annex" else row["kind"], text)
            for row in curated
            for text in (
                [text for district, text in described if district == int(row["district"]) and "管内" not in text]
                if row["kind"] == "annex"
                else [row["names"]]
            )
        ]
        if curated
        else [
            (district, "rest" if text.endswith("に属しない区域") else "office" if "管内" in text else "chome", text)
            for district, text in described
        ]
    )
    rest = [district for district, kind, _ in lines if kind == "rest"]
    if len(rest) != 1:
        raise ValueError(f"{table} {unit.code} {unit.name}: {len(rest)} remainders")
    remainder = rest[0]
    notes: list[str] = []
    listed: dict[str, int] = {}
    rules: dict[str, list[tuple[int, Blocks]]] = {}
    shapes_: list[tuple[shapely.Geometry, int]] = []
    for district, kind, text in lines:
        if kind == "former":
            for name, _ in items(text):
                key = f"{norm(name.partition('@')[0])}@{name.partition('@')[2] or '2000'}"
                if key not in olds:
                    raise ValueError(f"{table} {unit.code} {unit.name}: no former municipality {name}")
                shapes_.append((olds[key], district))
        elif kind == "table":
            other = pl.read_parquet(PARSED / f"districts_{text}.parquet", columns=["code", "geom"])
            shapes_.append(
                (
                    shapely.from_wkb(other.filter(pl.col("code") == f"{unit.code[:2]}{district:02d}")["geom"][0]),
                    district,
                )
            )
        elif kind == "chome":
            for name, note in items(text):
                rule = blocks(note) if note and sites is not None else None
                if rule is not None:
                    rules.setdefault(norm(name), []).append((district, rule))
                if "限る" in note:
                    if rule is None:
                        notes.append(f"{unit.name} {name}（{note}）")
                elif listed.setdefault(norm(name), district) != district:
                    raise ValueError(f"{table} {unit.code} {unit.name}: {name} in {listed[norm(name)]} and {district}")
        elif kind != "rest":
            raise ValueError(f"{table} {unit.code} {unit.name} {district}: unresolved {text[:60]}")
    polygons = shapely.from_wkb(areas["geom"].to_numpy())
    tree = shapely.STRtree(polygons)
    votes: dict[int, list[int]] = {}
    found: set[str] = set()
    for (name,), point in zip(
        located.select("name").iter_rows(),
        shapely.points(located["x"].to_numpy(), located["y"].to_numpy()),
        strict=True,
    ):
        for index in tree.query(point, predicate="within"):
            votes.setdefault(int(index), []).append(listed.get(norm(name), remainder))
            if norm(name) in listed:
                found.add(norm(name))
    chosen: list[int] = []
    for index, (name, polygon) in enumerate(zip(areas["name"], polygons, strict=True)):
        key = norm(name)
        inside = polygon.representative_point()
        prefix = [other for other in listed if key.startswith(other) and not SUFFIX.match(key[len(other) :])]
        if key in listed:
            chosen.append(listed[key])
            found.add(key)
        elif district := next((district for shape, district in shapes_ if shape.contains(inside)), None):
            chosen.append(district)
        elif prefix:
            chosen.append(listed[max(prefix, key=len)])
            found.add(max(prefix, key=len))
        elif index in votes:
            chosen.append(max(set(votes[index]), key=votes[index].count))
        else:
            chosen.append(remainder)
    carved: dict[int, list[tuple[shapely.Geometry, int]]] = {}
    if rules and sites is not None:
        city = norm(unit.name)
        named = sites.filter(
            pl.col("city").map_elements(lambda text: norm(text).endswith(city), return_dtype=pl.Boolean)
        ).with_columns(pl.col("name").map_elements(norm, return_dtype=pl.String).alias("key"))
        cut: set[str] = set()
        for index, (name, polygon) in enumerate(zip(areas["name"], polygons, strict=True)):
            key = norm(name)
            if key not in rules:
                continue
            cut.add(key)
            # An area the notes keep in part to a district lies, apart from those blocks, on the other side.
            only = {district for district, rule in rules[key] if rule.only}
            base = chosen[index]
            if base in only:
                base = next((district for district in (remainder, *sorted(set(chosen))) if district not in only), base)
            pieces, why = carve(polygon, base, rules[key], remainder, named.filter(pl.col("key") == key))
            if why is None:
                carved[index] = pieces
                notes.append(
                    f"{unit.name} {name}: cut into districts {', '.join(str(d) for _, d in pieces)}"
                    if len(pieces) > 1
                    else f"{unit.name} {name}: drawn whole in district {pieces[0][1]} by its block points"
                )
            else:
                notes.append(f"{unit.name} {name}: left whole, {why}")
        for key in sorted(set(rules) - cut):
            notes.append(f"{unit.name} {key}: blocks named, but no census small area of that name")
    missing = sorted(set(listed) - found)
    if missing:
        notes.append(f"{unit.name}: {len(missing)} listed names located nowhere: {'、'.join(missing)}")
    return chosen, remainder, notes, carved


def reconcile(table: str, towns: dict[str, Municipality], geometry: dict[str, shapely.Geometry]) -> None:
    for (prefecture, name), olds_ in MERGED.get(table, {}).items():
        members = [town for town in towns.values() if town.code[:2] == prefecture and norm(town.name) in olds_]
        if len(members) != len(olds_):
            raise ValueError(f"{table} {prefecture} {name}: {len(members)} of {len(olds_)} members")
        code = members[0].code
        counties = {town.county for town in members}
        towns[code] = Municipality(
            code,
            members[0].parent,
            name,
            members[0].subprefecture,
            counties.pop() if len(counties) == 1 else "",
            members[0].city or name[-1] == "市",
        )
        geometry[code] = mlit.merge([geometry[town.code] for town in members])
        for town in members[1:]:
            del towns[town.code], geometry[town.code]
    for code, county in COUNTIES.get(table, {}).items():
        towns[code].county, towns[code].city = county, False


def draw(table: str) -> pl.DataFrame:
    spec = TABLES[table]
    towns = municipalities(spec.n03)
    geometry = shapes(spec.n03)
    reconcile(table, towns, geometry)
    wholes, splits = plan(table, towns)
    listed = [code for codes in wholes.values() for code in codes] + list(splits)
    expected = [code for code, town in towns.items() if code not in DESIGNATED]
    if sorted(listed) != sorted(expected):
        missing = sorted(set(expected) - set(listed))
        doubled = sorted({code for code in listed if listed.count(code) > 1})
        extra = sorted(set(listed) - set(expected))
        raise ValueError(f"{table}: missing {missing}, doubled {doubled}, extra {extra}")
    members: dict[tuple[str, int], list[shapely.Geometry]] = {
        key: [geometry[code] for code in codes] for key, codes in wholes.items()
    }
    described: dict[tuple[str, int], list[str]] = {
        key: [towns[code].name for code in codes] for key, codes in wholes.items()
    }
    notes: list[str] = []
    errors: list[str] = []
    areas: dict[str, pl.DataFrame] = {}
    located: dict[str, pl.DataFrame] = {}
    olds: dict[str, dict[str, shapely.Geometry]] = {}
    sites: dict[str, pl.DataFrame | None] = {}
    vintages = formers()
    needed = restricted(table)
    for code, lines in splits.items():
        prefecture = code[:2]
        if prefecture not in areas:
            areas[prefecture] = small_areas(spec.census, prefecture)
            located[prefecture] = points(spec.isj, prefecture)
            sites[prefecture] = block_points(block_version(table), prefecture) if prefecture in needed else None
            olds[prefecture] = {
                key: shape
                for code, vintage in vintages
                if code == prefecture
                for key, shape in former(prefecture, vintage).items()
            }
        stale = areas[prefecture].filter(~pl.col("unit").is_in(list(towns)))
        shapely.prepare(geometry[code])
        inside = shapely.contains(geometry[code], shapely.point_on_surface(shapely.from_wkb(stale["geom"].to_numpy())))
        local = pl.concat([areas[prefecture].filter(pl.col("unit") == code), stale.filter(pl.Series(inside))])
        try:
            chosen, remainder, unit_notes, carved = assign(
                table,
                towns[code],
                lines,
                local,
                nearby(located[prefecture], code, geometry[code]),
                olds[prefecture],
                sites[prefecture],
            )
        except ValueError as error:
            errors.append(str(error))
            continue
        notes += unit_notes
        whole = geometry[code]
        polygons = shapely.from_wkb(local["geom"].to_numpy())
        cells = [
            cell
            for index, (polygon, district) in enumerate(zip(polygons, chosen, strict=True))
            for cell in carved.get(index, [(polygon, district)])
        ]
        taken: list[shapely.Geometry] = []
        for district in sorted({district for _, district in cells} - {remainder}):
            piece = shapely.intersection(
                whole,
                shapely.simplify(
                    shapely.union_all([cell for cell, other in cells if other == district], grid_size=1e-7),
                    mlit.TOLERANCE,
                ),
            )
            taken.append(piece)
            members.setdefault((prefecture, district), []).append(piece)
        rest = shapely.difference(whole, shapely.union_all(taken)) if taken else whole
        members.setdefault((prefecture, remainder), []).append(rest)
        for district in sorted({*(district for _, district in cells), remainder}):
            described.setdefault((prefecture, district), []).append(f"{towns[code].name} (part)")
    for note in notes:
        print(f"{table}: {note}")
    if errors:
        raise ValueError("\n".join(errors))
    records: list[tuple[str, str, int, str, bytes]] = []
    for prefecture in sorted({key[0] for key in members}):
        districts = sorted(district for key, district in members if key == prefecture)
        if districts != list(range(1, len(districts) + 1)):
            raise ValueError(f"{table} {prefecture}: districts {districts}")
        shaped = {district: mlit.polygons(mlit.merge(members[prefecture, district])) for district in districts}
        total = sum(shapely.area(shape) for shape in shaped.values())
        whole = shapely.area(geometry["P" + prefecture])
        if abs(total - whole) > 1e-3 * whole:
            raise ValueError(f"{table} {prefecture}: districts cover {total}, prefecture {whole}")
        for district, shape in shaped.items():
            records.append(
                (
                    f"{prefecture}{district:02d}",
                    prefecture,
                    district,
                    "、".join(described[prefecture, district]),
                    shapely.to_wkb(shape),
                )
            )
    return pl.DataFrame(
        records,
        schema={
            "code": pl.String,
            "prefecture": pl.String,
            "district": pl.Int64,
            "members": pl.String,
            "geom": pl.Binary,
        },
        orient="row",
    ).with_columns(pl.lit(table).alias("table"))


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for vintage in sorted({spec.n03 for spec in TABLES.values()} & set(N03)):
        if not layers(vintage).exists():
            mlit.layers(vintage, bases(vintage), SOURCE).write_parquet(layers(vintage))
    for table in sorted(TABLES, key=lambda table: TABLES[table].effective, reverse=True):
        frame = draw(table)
        print(table, frame.height, "districts")
        frame.write_parquet(PARSED / f"districts_{table}.parquet")
    print(build())


def ordinal(number: int) -> str:
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package jp.hor_smd/all from the parsed tables. It reads the database, through Draft, so the rows it
    rebuilds keep the ids the changeset path gave them."""
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House of Representatives single-member districts of Japan from the Public Offices Election Act "
            f"annex 1 ({', '.join(sorted(TABLES)[:-1])}, and {max(TABLES)} tables), drawn from N03 administrative "
            "areas and census small areas",
            polity="JP",
        )
        law = cs.source(mlit.LAW_PUBLISHER, LAW_TITLE)
        cs.add(
            "source",
            id=law,
            kind="official_body",
            publisher=mlit.LAW_PUBLISHER,
            title=LAW_TITLE,
            url=mlit.LAW,
            license="Public domain (Copyright Act art. 13)",
            license_url="https://laws.e-gov.go.jp/law/345AC0000000048#Mp-At_13",
            attribution=CREDIT,
        )
        for table in TABLES:
            cs.artifact(raw.cached(SOURCE, law_url(table)), law)
        order = sorted(TABLES, key=lambda table: TABLES[table].effective)
        districts = pl.concat([pl.read_parquet(PARSED / f"districts_{table}.parquet") for table in order])
        used = sorted(set(districts.filter(pl.col("members").str.contains("(part)", literal=True))["prefecture"]))
        for publisher, title, url, license_, license_url, attribution, links in (
            *(
                (
                    "Statistics Bureau of Japan",
                    f"Census small-area boundaries (町丁・字等), {year}",
                    "https://www.e-stat.go.jp/gis/statmap-search?page=1&type=2&aggregateUnitForBoundary=A",
                    "Government of Japan Standard Terms of Use 2.0 (compatible with CC BY 4.0)",
                    "https://www.e-stat.go.jp/terms-of-use",
                    "出典：政府統計の総合窓口(e-Stat)「国勢調査 小地域（町丁・字等）境界データ」を加工して作成",
                    [estat_url(year, prefecture) for prefecture in used],
                )
                for year in sorted({spec.census for spec in TABLES.values()})
            ),
            (
                mlit.PUBLISHER,
                "Location reference information, oaza and chome level (大字・町丁目レベル位置参照情報)",
                "https://nlftp.mlit.go.jp/isj/",
                "MLIT location reference information terms (free use with credit)",
                "https://nlftp.mlit.go.jp/isj/agreement.html",
                "「大字・町丁目レベル位置参照情報」（国土交通省）を加工して作成",
                [
                    ISJ.format(version=TABLES[table].isj, prefecture=prefecture)
                    for table in TABLES
                    for prefecture in used
                ],
            ),
            (
                mlit.PUBLISHER,
                "Location reference information, block level (街区レベル位置参照情報)",
                "https://nlftp.mlit.go.jp/isj/",
                "MLIT location reference information terms (free use with credit)",
                "https://nlftp.mlit.go.jp/isj/agreement.html",
                "「街区レベル位置参照情報」（国土交通省）を加工して作成",
                [
                    ISJ.format(version=block_version(table), prefecture=prefecture)
                    for table in TABLES
                    for prefecture in restricted(table)
                    if prefecture in used
                ],
            ),
            (
                mlit.PUBLISHER,
                "National Land Numerical Information, administrative areas (N03), 2000",
                "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2025.html",
                "CC BY 4.0",
                "https://creativecommons.org/licenses/by/4.0/",
                "「国土数値情報（行政区域データ）」（国土交通省）を加工して作成",
                [
                    *(n03(vintage, prefecture) for prefecture, vintage in formers() if vintage == "2000"),
                    *(bases("2000") if any(spec.n03 == "2000" for spec in TABLES.values()) else []),
                ],
            ),
            (
                mlit.PUBLISHER,
                "National Land Numerical Information, administrative areas (N03), 1920-1995",
                "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2025.html",
                "CC BY 4.0",
                "https://creativecommons.org/licenses/by/4.0/",
                "「国土数値情報（行政区域データ）」（国土交通省）を加工して作成",
                [
                    *(n03(vintage, prefecture) for prefecture, vintage in formers() if vintage != "2000"),
                    *(
                        url
                        for vintage in ("1995",)
                        if any(spec.n03 == vintage for spec in TABLES.values())
                        for url in bases(vintage)
                    ),
                ],
            ),
        ):
            source = cs.source(publisher, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=license_,
                license_url=license_url,
                attribution=attribution,
            )
            for link in sorted(set(links)):
                cs.artifact(raw.cached(SOURCE, link), source)
        japan = cs.polity("JP")
        cs.add(
            "scheme",
            code="jp.hor_smd",
            polity_id=japan,
            kind="electoral_district",
            rank=2,
            noun="single-member district",
        )
        for (code,), group in districts.group_by(["code"], maintain_order=True):
            rows_ = group.sort(pl.col("table").replace_strict({table: index for index, table in enumerate(order)}))
            prefecture, district = rows_["prefecture"][0], rows_["district"][0]
            first = TABLES[rows_["table"][0]].effective
            last = rows_["table"][-1]
            following = order.index(last) + 1
            end = TABLES[order[following]].effective if following < len(order) else None
            area = cs.area("jp.hor_smd", code)
            span = {"valid_from": first, "valid_to": end}
            cs.add("area", id=area, scheme_code="jp.hor_smd")
            cs.add("area_code", area_id=area, code=code, **span)
            cs.add("area_parent", area_id=area, parent_id=cs.area("jp.prefecture", prefecture), **span)
            native, english = PREFECTURES[int(prefecture) - 1]
            for text, lang, kind in (
                (f"{native}第{district}区", "ja", "official"),
                (f"{english} {ordinal(district)} district", "en", "english"),
            ):
                cs.add(
                    "name",
                    entity_id=area,
                    text=text,
                    lang=lang,
                    kind=kind,
                    is_preferred=kind == "official",
                    source_id=law,
                    valid_from_precision="day",
                    valid_to_precision="day",
                    **span,
                )
            for index, (table, wkb) in enumerate(rows_.select("table", "geom").iter_rows()):
                shape = shapely.set_srid(shapely.from_wkb(wkb), 4326)
                ends = TABLES[rows_["table"][index + 1]].effective if index + 1 < rows_.height else end
                cs.add(
                    "area_geometry",
                    area_id=area,
                    geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                    label_point=shapely.to_wkb(
                        shapely.set_srid(max(shapely.get_parts(shape), key=shapely.area).representative_point(), 4326),
                        hex=True,
                        include_srid=True,
                    ),
                    quality="official",
                    source_id=law,
                    valid_from=TABLES[table].effective,
                    valid_to=ends,
                )
        for index, table in enumerate(order):
            start = TABLES[table].effective
            cs.add(
                "boundary_set",
                scheme_code="jp.hor_smd",
                vintage=start.isoformat(),
                valid_from=start,
                valid_to=TABLES[order[index + 1]].effective if index + 1 < len(order) else None,
                as_of=start,
            )
        return cs.write()
