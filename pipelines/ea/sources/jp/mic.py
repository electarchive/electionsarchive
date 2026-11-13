import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl
import psycopg

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.jp.mlit import BLOCKS, PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.mic"
ROOT = "https://www.soumu.go.jp"
DATA_ROOT = f"{ROOT}/senkyo/senkyo_s/data"
ELECTIONS = {
    45: "2009-08-30",
    46: "2012-12-16",
    47: "2014-12-14",
    48: "2017-10-22",
    49: "2021-10-31",
    50: "2024-10-27",
    51: "2026-02-08",
}
ANCHOR = re.compile(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
SEATS_LABEL = "党派別議席配分表（比例代表）"
WINNERS_LABEL = "候補者別得票数（小選挙区）"


def page(url: str) -> str:
    content = raw.cached(SOURCE, url).path.read_bytes()
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp932")


def anchors(url: str) -> list[tuple[str, str]]:
    found = []
    for href, text in ANCHOR.findall(page(url)):
        label = re.sub(r"\s+|（別ウィンドウ）", "", unicodedata.normalize("NFKC", re.sub(r"<[^>]+>", "", text)))
        label = label.replace("(", "（").replace(")", "）")
        if label:
            found.append((ROOT + href if href.startswith("/") else href, label))
    return found


def suffix(url: str) -> str:
    return url[url.rindex(".") :]


@dataclass
class Listing:
    index: str
    towns: str
    subpages: list[str]
    smd: list[tuple[str, str]]
    pr: list[tuple[str, str]]
    seats: str
    winners: str


def listing(number: int) -> Listing:
    index = f"{DATA_ROOT}/shugiin{number}/index.html"
    towns = f"{DATA_ROOT}/shugiin{number}/shikuchouson.html"
    links = anchors(index)
    smd: list[tuple[str, str]] = []
    pr: list[tuple[str, str]] = []
    subpages = [(url, label) for url, label in anchors(towns) if re.search(r"shikuchouson_\d+\.html$", url)]
    if subpages:
        for url, prefecture in subpages:
            files = {label: href for href, label in anchors(url) if "/main_content/" in href}
            smd.append((prefecture, files["小選挙区"]))
            pr.append((prefecture, files["比例代表"]))
    else:
        for url, label in anchors(towns):
            if "/main_content/" not in url:
                continue
            (pr if label.endswith("選挙区") else smd).append((label, url))
    seats = next(url for url, label in links if SEATS_LABEL in label)
    winners = next(url for url, label in links if WINNERS_LABEL in label)
    return Listing(index, towns, [url for url, _ in subpages], smd, pr, seats, winners)


def fetch() -> None:
    for number in ELECTIONS:
        raw.fetch(SOURCE, f"{DATA_ROOT}/shugiin{number}/index.html", suffix=".html")
        raw.fetch(SOURCE, f"{DATA_ROOT}/shugiin{number}/shikuchouson.html", suffix=".html")
        for url, _ in anchors(f"{DATA_ROOT}/shugiin{number}/shikuchouson.html"):
            if re.search(r"shikuchouson_\d+\.html$", url):
                raw.fetch(SOURCE, url, suffix=".html")
        found = listing(number)
        for url in [*(url for _, url in found.smd + found.pr), found.seats, found.winners]:
            raw.fetch(SOURCE, url, suffix=suffix(url))


VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INDEPENDENT = {"", "無所属"}
STATE_TOTAL = re.compile(r"^(?:[道都府県]計|.+[道都府県] 合計)$")
PARTY_LIKE = re.compile(r"(党|の会|組|連合|クラブ|みらい)$")
ERRATA = {("2017-10-22", "福井県", 1, "永平寺町"): "the town's row repeats the sum of the rows above it"}
STALE = {
    ("2021-10-31", "宮城県", 3): {1},
    ("2021-10-31", "埼玉県", 14): {1},
    ("2021-10-31", "静岡県", 7): {1, 2},
}
PARTIAL_PR = {
    ("2009-08-30", "栃木県"): "The ministry's Tochigi sheet has no columns for 民主党, 社会民主党, and 幸福実現党, "
    "so their Tochigi counting districts are missing and their Kita-Kanto sums fall short of the block totals."
}
RENAMED = {("2021-10-31", "NHK党"): "NHKと裁判してる党弁護士法72条違反で"}
SWAPPED = {("2017-10-22", "滋賀県", 3): (0, 2, 1)}
ALIASES = {
    ("2017-10-22", "群馬県", 4, "福田 たつお"): "福田 達夫",
    ("2017-10-22", "岐阜県", 1, "浅井 里江"): "吉田 りえ",
}


def grid(path: Path, sheet: str) -> list[list[str]]:
    frame = fastexcel.read_excel(str(path)).load_sheet(sheet, header_row=None, dtypes="string").to_polars()
    return [[unicodedata.normalize("NFKC", v).strip() if v else "" for v in row] for row in frame.iter_rows()]


def sheets(path: Path) -> list[str]:
    return [sheet for sheet in fastexcel.read_excel(str(path)).sheet_names if sheet != "リスト"]


def amount(text: str) -> Decimal:
    text = text.replace(",", "").strip()
    return Decimal(text) if text not in {"", "-", "－"} else Decimal(0)


def person_name(text: str) -> str:
    text = re.sub(r"[ \u3000]+$", "", unicodedata.normalize("NFKC", text).replace("\n", ""))
    text = re.sub(r"^\((.*)\)$", r"\1", re.sub(r"\s*\((無所属|諸派)\)$", "", text))
    if re.search(r"\S {2,}\S", text):
        return " ".join(part.replace(" ", "") for part in re.split(r" {2,}", text.strip()))
    return re.sub(r" +", " ", text.strip())


def party_name(text: str) -> str:
    return re.sub(r"^\((.*)\)$", r"\1", re.sub(r"\s+", "", text))


def places(rows: list[list[str]], columns: list[int], start: int) -> tuple[list[tuple[str, list[Decimal]]], int]:
    found: list[tuple[int, str, list[Decimal]]] = []
    index = start
    while index < len(rows):
        row = rows[index]
        level = 0 if row[0] else 1 if len(row) > 1 and row[1] else None
        if level is None:
            index += 1
            continue
        name = re.sub(r"\s+", "", row[level])
        if name.endswith("計"):
            break
        if not any(row[column] for column in columns):
            index += 1
            continue
        found.append((level, name, [amount(row[column]) for column in columns]))
        index += 1
    leaves = []
    for position, (level, name, values) in enumerate(found):
        following = found[position + 1] if position + 1 < len(found) else None
        if following is not None and (
            (level == 0 and following[0] == 1) or (following[1].startswith(name) and following[1] != name)
        ):
            continue
        leaves.append((name, values))
    return leaves, index


def total_of(rows: list[list[str]], index: int, columns: list[int]) -> list[Decimal]:
    return [amount(rows[index][column]) for column in columns]


def smd_sheet(rows: list[list[str]], held: str, prefecture: str, sheet: str) -> tuple[list[dict], list[dict]]:
    district = int(re.findall(r"(\d+)区", sheet)[0])
    head = next(i for i, row in enumerate(rows) if row and row[0] == "候補者名")
    columns = [j for j, cell in enumerate(rows[head]) if j > 0 and cell and cell != "得票数計" and "率" not in cell]
    party_row = next(i for i in range(head + 1, len(rows)) if rows[i][0].startswith(("市区町村名", "開票区名")))
    people, labels = rows[head], rows[party_row] + [""] * len(rows[head])
    if all(PARTY_LIKE.search(people[column]) for column in columns):
        people, labels = labels, people
    leaves, end = places(rows, columns, party_row + 1)
    leaves = [(name, values) for name, values in leaves if (held, prefecture, district, name) not in ERRATA]
    order = SWAPPED.get((held, prefecture, district), tuple(range(len(columns))))
    leaves = [(name, [values[order[k]] for k in range(len(columns))]) for name, values in leaves]
    printed_row = [amount(rows[end][columns[order[k]]]) for k in range(len(columns))]
    kept = [k for k in range(len(columns)) if k + 1 not in STALE.get((held, prefecture, district), set())]
    leaves = [(name, [values[k] for k in kept]) for name, values in leaves]
    sums = [sum((values[k] for _, values in leaves), Decimal(0)) for k in range(len(kept))]
    candidates = []
    for position, k in enumerate(kept):
        name = person_name(people[columns[k]])
        candidates.append(
            {
                "held": held,
                "prefecture": prefecture,
                "district": district,
                "order": position + 1,
                "name": ALIASES.get((held, prefecture, district, name), name),
                "party": party_name(labels[columns[k]]),
                "votes": str(sums[position]),
                "printed": str(printed_row[k]),
            }
        )
    votes = [
        {"held": held, "prefecture": prefecture, "district": district, "place": name, "order": k + 1, "votes": str(v)}
        for name, values in leaves
        for k, v in enumerate(values)
    ]
    return candidates, votes


def pr_sheet(rows: list[list[str]], held: str, prefecture: str) -> list[dict]:
    if any(row[0] == "党派名" for row in rows):
        start = next(i for i, row in enumerate(rows) if row[0] == "党派名")
        names_row = next(i for i in range(start, len(rows)) if any(rows[i][1:]))
        data = next(i for i, row in enumerate(rows) if row[0] == "開票区名") + 1
    else:
        names_row = next(i for i, row in enumerate(rows) if row[0].startswith(("市区町村名", "開票区名")))
        data = names_row + 1
    columns = [
        j for j, cell in enumerate(rows[names_row]) if j > 0 and cell and cell != "得票数計" and "率" not in cell
    ]
    parties = [party_name(rows[names_row][j]) for j in columns]
    leaves: list[tuple[str, list[Decimal]]] = []
    index = data
    while True:
        found, index = places(rows, columns, index)
        leaves += found
        if index >= len(rows) or STATE_TOTAL.match(rows[index][0].replace("　", " ")):
            break
        index += 1
    if index >= len(rows):
        raise ValueError(f"{held} {prefecture}: no prefecture total row")
    total = total_of(rows, index, columns)
    sums = [sum((values[k] for _, values in leaves), Decimal(0)) for k in range(len(columns))]
    if sums != total:
        print(f"MISMATCH {held} {prefecture} PR: places sum to {sums}, total row {total}")
    return [
        {"held": held, "prefecture": prefecture, "party": party, "place": name, "votes": str(values[k])}
        for name, values in leaves
        for k, party in enumerate(parties)
    ]


def prefecture_of(label: str) -> str:
    match = re.match(r"(.+?[都道府県])", label)
    return match[1] if match else label


def parse() -> None:
    candidates: list[dict] = []
    smd_votes: list[dict] = []
    pr_votes: list[dict] = []
    seats: list[dict] = []
    for number, held in ELECTIONS.items():
        found = listing(number)
        text = book_text(found.winners)
        for prefecture, url in found.smd:
            path = raw.cached(SOURCE, url).path
            for sheet in sheets(path):
                more, votes = smd_sheet(grid(path, sheet), held, prefecture, sheet)
                erratum = any(key[:3] == (held, prefecture, more[0]["district"]) for key in ERRATA)
                for candidate in more:
                    checked = Decimal(candidate["printed" if erratum else "votes"])
                    candidate["result"] = declared(text, candidate["name"], checked)
                marks = [candidate["result"] for candidate in more]
                if None in marks or marks.count("当") != 1:
                    raise ValueError(f"{held} {prefecture} {sheet}: the book does not confirm {more}")
                candidates += more
                smd_votes += votes
        block_votes: dict[tuple[str, str], Decimal] = {}
        for label, url in found.pr:
            path = raw.cached(SOURCE, url).path
            for sheet in sheets(path):
                rows = grid(path, sheet)
                if not any(row[0].startswith(("市区町村名", "開票区名", "党派名")) for row in rows):
                    continue
                prefecture = sheet if re.search(r"[都道府県]$", sheet) else prefecture_of(label)
                block = BLOCK_OF[prefecture]
                for row in pr_sheet(rows, held, prefecture):
                    row["party"] = RENAMED.get((held, row["party"]), row["party"])
                    pr_votes.append(row | {"block": block})
                    key = (block, row["party"])
                    block_votes[key] = block_votes.get(key, Decimal(0)) + Decimal(row["votes"])
        for block, parties in allocation(found.seats).items():
            for party, (votes, won) in parties.items():
                short = any(held == day and BLOCK_OF[prefecture] == block for day, prefecture in PARTIAL_PR)
                if votes and block_votes.get((block, party)) != votes and not short:
                    raise ValueError(
                        f"{held} {block} {party}: places sum to {block_votes.get((block, party))}, {votes}"
                    )
                votes = votes or block_votes.get((block, party), Decimal(0))
                seats.append({"held": held, "block": block, "party": party, "votes": str(votes), "seats": won})
        listed = {(block, party) for block, party in block_votes}
        if missing := {(row["block"], row["party"]) for row in seats if row["held"] == held} - listed:
            raise ValueError(f"{held}: allocation parties not in the vote tables: {missing}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(candidates).write_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    pl.DataFrame(smd_votes).write_parquet(PARSED / f"smd-v{VERSION}.parquet")
    pl.DataFrame(pr_votes).write_parquet(PARSED / f"pr-v{VERSION}.parquet")
    pl.DataFrame(seats).write_parquet(PARSED / f"seats-v{VERSION}.parquet")


def book_text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    if path.suffix == ".pdf":
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True
        ).stdout
    else:
        text = "\n".join(" ".join(cell for cell in row if cell) for sheet in sheets(path) for row in grid(path, sheet))
    return plain(unicodedata.normalize("NFKC", text))


def printed(votes: Decimal) -> list[str]:
    whole = votes == votes.to_integral()
    forms = [f"{votes:,.0f}", f"{votes:.0f}"] if whole else [f"{votes:,.3f}", f"{votes:.3f}"]
    short = votes.normalize()
    return [*forms, f"{short:,f}", f"{short:f}"]


def plain(text: str) -> str:
    return re.sub(r"[\s\ufe00-\ufe0f\U000e0100-\U000e01ef]+", "", text)


def declared(text: str, name: str, votes: Decimal) -> str | None:
    key = plain(name)
    forms = printed(votes)
    for match in re.finditer(r"(当|落)" + re.escape(key), text):
        if any(form in text[match.end() : match.end() + 90] for form in forms):
            return match[1]
    for form in forms:
        for found in re.finditer(re.escape(form) + r"(?![\d.,])", text):
            before = text[max(0, found.start() - 90) : found.start()]
            mark = max(before.rfind("当"), before.rfind("落"))
            between = before[mark + 1 :]
            if mark >= 0 and (len(set(between) & set(key)) * 2 >= len(set(key)) or re.match(r"\d\d\D", between)):
                return before[mark]
    return None


BLOCK_OF = {PREFECTURES[number - 1][0]: block for block, _, numbers in BLOCKS for number in numbers}
BLOCK_HEADER = re.compile(r"^[<＜]\s*(.+?)選挙区\s*[>＞]$")


def allocation(url: str) -> dict[str, dict[str, tuple[Decimal, int]]]:
    path = raw.cached(SOURCE, url).path
    blocks: dict[str, dict[str, tuple[Decimal, int]]] = {}
    for sheet in sheets(path):
        block = None
        parties: dict[int, str] = {}
        pairs: dict[str, tuple[int, int]] = {}
        for row in grid(path, sheet):
            cells = [cell for cell in row if cell]
            header = next((found for cell in cells if (found := BLOCK_HEADER.match(cell))), None)
            if header:
                block = header[1]
                blocks.setdefault(block, {})
                parties, pairs = {}, {}
            elif block is None or not cells:
                continue
            elif "順位" in cells:
                pairs = {party: (j, j + 1) if row[j] == "順位" else (j - 1, j) for j, party in parties.items()}
            elif not row[0].isdigit() and any(not re.match(r"^[\d,.]+$", cell) for cell in cells if cell != "除数"):
                if pairs or row[0] == "除数":
                    parties, pairs = {}, {}
                for j, cell in enumerate(row):
                    if j > 0 and cell:
                        parties[j] = party_name(parties.get(j, "") + cell)
            elif row[0].isdigit() and pairs:
                for party, (rank, quotient) in pairs.items():
                    votes, seats = blocks[block].get(party, (Decimal(0), 0))
                    if row[0] == "1":
                        votes = amount(row[quotient])
                    blocks[block][party] = (votes, seats + (1 if row[rank] else 0))
    return blocks


FIXES = {
    "栗国村": "粟国村",
    "佐伯氏": "佐伯市",
    "始良市": "姶良市",
    "南薩摩市": "南さつま市",
    "清水町": "湧水町",
    "大崎市": "大崎町",
    "横浜市都築区": "横浜市都筑区",
    "稲敷市": "稲城市",
    "静岡市葵開票区": "静岡市葵区",
    "静岡市駿河開票区": "静岡市駿河区",
}
FIXED_IN = {"大崎市": "46", "清水町": "46", "稲敷市": "13"}
VARIANTS = str.maketrans({"ケ": "ヶ", "ヵ": "ヶ", "鯵": "鰺", "桧": "檜", "檮": "梼", "栗": "栗"})
NUMERALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
SPLIT = re.compile(r"^(.+?[市区町村])(.*)$")
PIECE = re.compile(r"^(?:第|\()*(?:第)?(\d+|[一二三四五六七八九])(?:区|開票区)?\)?(?:\((.+)\))?$")


def variant(text: str) -> str:
    return text.translate(VARIANTS)


class Places:
    def __init__(self, conn: psycopg.Connection, held: date) -> None:
        rows = conn.execute(
            """select a.scheme_code, c.code, n.text, upper(c.valid) is null or upper(c.valid) > %s::date
               from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.lang = 'ja' and n.kind = 'official' and n.valid && c.valid
               where a.scheme_code in ('jp.municipality', 'jp.ward')
               and c.valid && daterange(%s::date - 1500, %s::date + 1500)""",
            [held, held, held],
        ).fetchall()
        rows.sort(key=lambda row: not row[3])
        self.towns: dict[tuple[str, str], str] = {}
        for scheme, code, text, _ in rows:
            if scheme == "jp.municipality":
                self.towns.setdefault((code[:2], variant(text)), code)
        cities = {code: text for (_, text), code in self.towns.items() if text.endswith("市")}
        self.wards: dict[tuple[str, str], str] = {}
        self.short: dict[tuple[str, str], list[str]] = {}
        for scheme, code, text, _ in rows:
            if scheme != "jp.ward":
                continue
            city = max(c for c in cities if c[:2] == code[:2] and c < code)
            self.wards.setdefault((code[:2], cities[city] + variant(text)), code)
            if code not in self.short.setdefault((code[:2], variant(text)), []):
                self.short[code[:2], variant(text)].append(code)

    def find(self, prefecture: str, base: str, previous: str | None) -> tuple[str, str] | None:
        base = re.sub(r"^[^市区町村]+?郡(?=.+[町村]$)", "", variant(base))
        if (prefecture, base) in self.wards:
            return "jp.ward", self.wards[prefecture, base]
        if (prefecture, base) in self.towns:
            return "jp.municipality", self.towns[prefecture, base]
        if prefecture == "13" and (prefecture, base + "区") in self.towns:
            return "jp.municipality", self.towns[prefecture, base + "区"]
        found = self.short.get((prefecture, base), [])
        if len(found) > 1 and previous is not None:
            found = [code for code in found if code[:4] == previous[:4]]
        return ("jp.ward", found[0]) if len(found) == 1 else None

    def resolve(self, prefecture: str, place: str, previous: str | None) -> tuple[str, str, str | None]:
        place = re.sub(r"\s+|" + "|".join(name for name, _ in PREFECTURES), "", place) or place
        for wrong, right in FIXES.items():
            if FIXED_IN.get(wrong, prefecture) == prefecture and place.startswith(wrong):
                place = right + place[len(wrong) :]
        if (whole := self.find(prefecture, place, previous)) is not None:
            return *whole, None
        if (
            prefecture == "13"
            and (match := re.match(r"^(\D+?)(\d+)区$", place))
            and (found := self.find(prefecture, match[1], previous)) is not None
        ):
            return *found, match[2]
        ends = [match.end() for match in re.finditer(r"[市区町村]", place)]
        for end in reversed(ends):
            head, rest = place[:end], place[end:]
            found = self.find(prefecture, head, previous)
            if found is None or not rest:
                continue
            if piece := PIECE.match(rest):
                number = piece[1]
                return *found, str(NUMERALS.get(number, number)) + (f"({piece[2]})" if piece[2] else "")
            if re.match(r"^(?:\(\d+区\))?[・、]", rest):
                city = found[1] if found[0] == "jp.municipality" else self.city_of(found[1])
                return "jp.municipality", city, place
            if rest.startswith("("):
                return *found, rest.strip("()")
        raise LookupError(f"{prefecture} {place}")

    def city_of(self, ward: str) -> str:
        return max(code for (prefecture, _), code in self.towns.items() if prefecture == ward[:2] and code < ward)


PUBLISHER = "総務省"
TITLE = "衆議院議員総選挙・最高裁判所裁判官国民審査結果調"
INDEX = f"{DATA_ROOT}/shugiin/ichiran.html"
LICENSE = "Public Data License 1.0"
LICENSE_URL = "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0"
ATTRIBUTION = "出典：総務省ホームページ"
VOTES = 1
PIECES = "jp.counting_district"
PARTY_ALIASES = {
    "社民党": "社会民主党",
    "公明": "公明党",
    "日本維新": "日本維新の会",
    "幸福実現党鹿児島県本部": "幸福実現党",
}
WIKIDATA = {
    "自由民主党": "Q232595",
    "日本共産党": "Q641600",
    "民主党": "Q200314",
    "幸福実現党": "Q1571486",
    "参政党": "Q113009668",
    "中道改革連合": "Q137787102",
    "希望の党": "Q7141081",
    "社会民主党": "Q835109",
    "日本未来の党": "Q194683",
    "公明党": "Q1061354",
    "みんなの党": "Q1355093",
    "維新の党": "Q18116909",
    "れいわ新選組": "Q64405573",
    "次世代の党": "Q17488856",
    "日本のこころ": "Q17488856",
    "NHKと裁判してる党弁護士法72条違反で": "Q39142381",
    "みんなでつくる党": "Q39142381",
    "日本保守党": "Q122639399",
    "生活の党": "Q2164308",
    "国民新党": "Q241325",
    "チームみらい": "Q134414127",
    "新党大地": "Q1247614",
    "新党日本": "Q60658",
    "新党改革": "Q2094225",
    "改革クラブ": "Q2094225",
}
DATED = {
    "日本維新の会": (("2014-01-01", "Q1027055"), ("9999-12-31", "Q21499745")),
    "立憲民主党": (("2020-01-01", "Q41532587"), ("9999-12-31", "Q99233271")),
    "国民民主党": (("2020-01-01", "Q52729635"), ("9999-12-31", "Q99527550")),
}
SMD_RULES = {"formula": "fptp"}
PR_RULES = {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}}
PR_SYSTEM = (
    "Each block's seats go to the party lists by D'Hondt. A list may rank several candidates who also stand in "
    "single-member districts at the same place; among them, seats go in order of each one's district vote as a "
    "share of the district winner's (惜敗率). District winners are struck from the list, and so are dual candidates "
    "who fell below a tenth of their district's valid votes. A list that runs out of names gives up its remaining "
    "seats, which go to the next highest quotients of the other lists (Public Offices Election Act art. 95-2)."
)
SMD_METHOD = (
    "Candidate votes by counting district (開票区), from the ministry's tables by municipality, which the "
    "prefectural election commissions compile. Counting districts are municipalities or designated-city wards; a "
    "municipality or ward split between districts is counted in parts, each held here as a unit within it. Votes "
    "for candidates whose names other candidates share are apportioned (按分) and carry three decimals. Each "
    "candidate's total and result mark were checked against the ministry's 結果調 book."
)
PR_BLOCK_METHOD = (
    "Party list votes and seats for the block from the ministry's seat allocation table (党派別議席配分表) in the "
    "結果調 book."
)
PR_PLACE_METHOD = (
    "Party list votes by counting district from the ministry's tables by municipality. The counting districts add "
    "up to the block totals in the seat allocation table."
)


def party_label(text: str) -> str:
    text = re.sub(r"^\[(.*)\]$", r"\1", text)
    text = re.sub(r"\((前|新|元)\)$", "", text)
    return PARTY_ALIASES.get(text, text)


def party_key(text: str, held: str) -> tuple[str, str] | None:
    text = party_label(text)
    if text in INDEPENDENT:
        return None
    if text in DATED:
        return "wikidata", next(qid for until, qid in DATED[text] if held < until)
    if text in WIKIDATA:
        return "wikidata", WIKIDATA[text]
    return f"{SOURCE}.party", text


def dhondt(votes: dict[str, Decimal], seats: int) -> dict[str, int]:
    won = dict.fromkeys(votes, 0)
    for _ in range(seats):
        best = max(votes, key=lambda party: (votes[party] / (won[party] + 1), votes[party]))
        won[best] += 1
    return won


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    smd = pl.read_parquet(PARSED / f"smd-v{VERSION}.parquet")
    pr = pl.read_parquet(PARSED / f"pr-v{VERSION}.parquet")
    seats = pl.read_parquet(PARSED / f"seats-v{VERSION}.parquet")
    codes = {native: f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1)}
    english = {native: name for native, name in PREFECTURES}
    blocks = {native: (f"{number:02d}", name) for number, (native, name, _) in enumerate(BLOCKS, 1)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's House of Representatives general elections 2009-2026 from the Ministry of Internal "
            "Affairs and Communications: district candidates and party lists by counting district, with declared "
            "winners and block seats",
            polity="JP",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        japan = cs.polity("JP")
        house = cs.key("jp.body", "house-of-representatives")
        if {japan, house} & cs.minted:
            raise ValueError("run jp.mlit and jp.diet first")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=INDEX,
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution=ATTRIBUTION,
        )
        cs.add("scheme", code=PIECES, polity_id=japan, kind="precinct", rank=4, noun="counting district")
        smd_system = cs.key(f"{SOURCE}.electoral_system", "hor-smd")
        cs.add("electoral_system", id=smd_system, polity_id=japan, rules=SMD_RULES, rules_version=1)
        name(smd_system, "House of Representatives single-member districts, first past the post")
        name(smd_system, "小選挙区制", "ja", "native", False)
        pr_system = cs.key(f"{SOURCE}.electoral_system", "hor-pr")
        cs.add("electoral_system", id=pr_system, polity_id=japan, rules=PR_RULES, rules_version=1)
        name(pr_system, "House of Representatives proportional blocks, closed lists by D'Hondt")
        name(pr_system, "比例代表制（拘束名簿式、ドント式）", "ja", "native", False)
        cs.add("note", entity_id=pr_system, kind="methodology", body_md=PR_SYSTEM)

        parties: dict[tuple[str, str], UUID] = {}

        def party(text: str, held: str) -> UUID | None:
            found = party_key(text, held)
            if found is None:
                return None
            if found not in parties:
                entity = parties[found] = cs.key(*found)
                if entity in cs.minted:
                    if found[0] == "wikidata":
                        raise ValueError(f"{found[1]} should already exist from jp.diet")
                    cs.add("party", id=entity, kind="party", polity_id=japan)
                    name(entity, found[1], "ja")
            return parties[found]

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

        values: list[tuple[UUID, UUID, UUID, Decimal]] = []
        for number, held in ELECTIONS.items():
            day = date.fromisoformat(held)
            found = listing(number)
            for url in [found.index, found.towns, *found.subpages, found.seats, found.winners]:
                cs.artifact(raw.cached(SOURCE, url), source)
            for _, url in found.smd + found.pr:
                cs.artifact(raw.cached(SOURCE, url), source)
            places = Places(conn, day)

            def place_unit(prefecture: str, place: str, previous: str | None) -> tuple[UUID, str]:
                code = codes[prefecture]
                scheme, area_code, piece = places.resolve(code, place, previous)
                parent = prefecture_unit(code)
                polity = cs.polity(f"JP-{code}")
                area = cs.area(scheme, area_code)
                whole = unit(scheme, area_code, parent, area, "equals", polity)
                if piece is None:
                    return whole, area_code
                return unit(PIECES, f"{held}:{area_code}:{piece}", whole, area, "within", polity), area_code

            election = cs.key(f"{SOURCE}.election", held)
            cs.add("election", id=election, polity_id=japan, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} Japanese general election")
            name(election, f"第{number}回衆議院議員総選挙", "ja", "native", False)
            path = f"jp/{held}-general"
            cs.add("slug", path=path, entity_id=election)

            for (prefecture, district), rows in candidates.filter(pl.col("held") == held).group_by(
                "prefecture", "district", maintain_order=True
            ):
                key = f"{held}:{codes[str(prefecture)]}:{district}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=house,
                    magnitude=1,
                    electoral_system_id=smd_system,
                    default_result_kind="counted",
                )
                name(contest, f"{english[str(prefecture)]} {district}")
                name(contest, f"{prefecture}第{district}区", "ja", "native", False)
                cs.add("slug", path=f"{path}/{slug(english[str(prefecture)])}-{district}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", key)
                counted = sum(Decimal(v) for v in rows["votes"])
                printed = sum(Decimal(v) for v in rows["printed"])
                erratum = [text for (h, p, d, _), text in ERRATA.items() if (h, p, d) == (held, prefecture, district)]
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
                    completeness=float(counted / printed) if erratum else 1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=SMD_METHOD)
                for (h, p, d, place), text in ERRATA.items():
                    if (h, p, d) == (held, prefecture, district):
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md=f"{place} is left out: in the ministry's table {text}. The district totals in "
                            "the 結果調 book include it.",
                        )
                options: dict[int, UUID] = {}
                for row in rows.iter_rows(named=True):
                    option = options[row["order"]] = cs.key(f"{SOURCE}.option", f"{key}:{row['order']}")
                    member = party(row["party"], held)
                    person = cs.key(f"{SOURCE}.person", f"{codes[str(prefecture)]}:{plain(row['name'])}")
                    if person in cs.minted and person not in added:
                        added.add(person)
                        cs.add("person", id=person)
                        name(person, row["name"], "ja")
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
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=row["name"],
                        member_of_party_id=member,
                        is_independent=member is None,
                    )
                    if row["result"] == "当":
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                previous = None
                for row in smd.filter(
                    (pl.col("held") == held) & (pl.col("prefecture") == prefecture) & (pl.col("district") == district)
                ).iter_rows(named=True):
                    place, previous = place_unit(row["prefecture"], row["place"], previous)
                    values.append((result_set, place, options[row["order"]], Decimal(row["votes"])))

            for block, (block_code, block_english) in blocks.items():
                key = f"{held}:pr:{block_code}"
                contest = cs.key(f"{SOURCE}.contest", key)
                allocated = seats.filter((pl.col("held") == held) & (pl.col("block") == block))
                size = int(allocated["seats"].sum())
                area = cs.area("jp.hor_block", block_code)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=house,
                    district_area_id=area,
                    magnitude=size,
                    electoral_system_id=pr_system,
                    default_result_kind="counted",
                )
                name(contest, f"{block_english} proportional block")
                name(contest, f"比例代表{block}選挙区", "ja", "native", False)
                cs.add("slug", path=f"{path}/{slug(block_english)}-block", entity_id=contest)
                block_set = cs.key(f"{SOURCE}.result_set", key)
                place_set = cs.key(f"{SOURCE}.result_set", f"{key}:places")
                block_votes = {row["party"]: Decimal(row["votes"]) for row in allocated.iter_rows(named=True)}
                local = pr.filter((pl.col("held") == held) & (pl.col("block") == block))
                local_total = sum((Decimal(v) for v in local["votes"]), Decimal(0))
                for result_set, default, method, share in (
                    (block_set, True, PR_BLOCK_METHOD, 1.0),
                    (place_set, False, PR_PLACE_METHOD, float(local_total / sum(block_votes.values()))),
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
                for (day_key, prefecture), text in PARTIAL_PR.items():
                    if day_key == held and BLOCK_OF[prefecture] == block:
                        cs.add("note", entity_id=place_set, kind="caveat", body_md=text)
                engine = dhondt(block_votes, size)
                declared_seats = {row["party"]: row["seats"] for row in allocated.iter_rows(named=True)}
                if engine != declared_seats:
                    moved = ", ".join(
                        f"{label} {declared_seats[label]} and {engine[label]}"
                        for label in block_votes
                        if engine[label] != declared_seats[label]
                    )
                    cs.add(
                        "note",
                        entity_id=block_set,
                        kind="caveat",
                        body_md="The declared seats differ from a plain D'Hondt count because a list ran out of "
                        "eligible names: its candidates won their districts or fell below a tenth of their district's "
                        "valid votes, so its seats passed to the next highest quotients, and the ministry's table "
                        f"leaves its quotients blank. Declared, then D'Hondt alone: {moved}.",
                    )
                block_unit = unit("jp.hor_block", block_code, None, area, "equals", japan)
                options = {}
                for position, row in enumerate(allocated.iter_rows(named=True), 1):
                    option = options[row["party"]] = cs.key(f"{SOURCE}.option", f"{key}:{row['party']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party(row["party"], held),
                        ballot_position=position,
                        ballot_label=party_label(row["party"]),
                    )
                    values.append((block_set, block_unit, option, Decimal(row["votes"])))
                    if row["seats"]:
                        cs.add("outcome", result_set_id=block_set, option_id=option, kind="won", seats=row["seats"])
                previous = None
                for row in local.iter_rows(named=True):
                    place, previous = place_unit(row["prefecture"], row["place"], previous)
                    values.append((place_set, place, options[row["party"]], Decimal(row["votes"])))

        totals: dict[tuple[UUID, UUID, UUID], Decimal] = {}
        for result_set, place, option, amount_ in values:
            totals[result_set, place, option] = totals.get((result_set, place, option), Decimal(0)) + amount_
        for (result_set, place, option), amount_ in totals.items():
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=place,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=amount_,
            )
        return cs.write()
