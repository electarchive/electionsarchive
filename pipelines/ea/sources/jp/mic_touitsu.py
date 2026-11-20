"""Mayoral elections of the designated cities that vote in April with the unified local elections.

The ministry's designated-city mayoral summaries (`jp.mic_shicho`) leave out the cities whose mayors are elected on
the prefectural day of the unified local elections: Sapporo, Sagamihara, Shizuoka, Hamamatsu, and Hiroshima. The
ministry prints those counts in its 地方選挙結果調, the book it publishes after each unified round: the appendix table
指定都市の長の開票結果に関する調 gives each candidate's ballot name, legal name, age, party, status, and votes, and the
designated-city rows of the turnout table (有権者数・投票者数・棄権者数及び投票率に関する調) and the ballots table
(投票総数・有効投票数及び無効投票数等に関する調) give each city's electors, voters, ballots cast, valid ballots, and
invalid ballots. Only the 2015 and 2019 books carry a text layer that maps to Unicode; the 2003, 2007, and 2011 books
embed their Japanese fonts without one. For other years the pipeline reads the city election commissions' own count
sheets where they publish them as text or workbooks (`RECORDS`): Sagamihara 2011 and 2023, and Sapporo 2023.

Each election is one package, `jp.mic_touitsu/<city>-<year>`, which depends on `jp.mic_touitsu/registry` for the
polities, offices, reporting units, parties, and electoral system it mints. The city areas are `jp.mlit`'s, and the
Sapporo, Sagamihara, and Hiroshima reporting units and the Hiroshima polity are referenced where production holds
them. The ids of the polities, offices, and units this pipeline mints follow the changeset path's keys
(`jp.polity`, `jp.office`, and the `jp.municipality` unit code), so `jp.mic_shicho` and `jp.city_shicho` resolve to
them.
"""

import re
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import pymupdf
import xlrd

from ea import package, raw
from ea.package import Package
from ea.sources.jp import mic_release
from ea.sources.jp.mlit import PREFECTURES

SOURCE = "jp.mic_touitsu"
JAPAN = mic_release.JAPAN
INDEX = "https://www.soumu.go.jp/senkyo/senkyo_s/data/chihou/ichiran.html"
PUBLISHER = "総務省"
LICENSE = "Public Data License 1.0"
LICENSE_URL = "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0"
ATTRIBUTION = "出典：総務省ホームページ"
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
RULES = {"formula": "fptp", "minimum": {"of": "valid", "share": 0.25}}


@dataclass(frozen=True)
class City:
    native: str
    english: str
    code: str


CITIES = {
    "sapporo": City("札幌市", "Sapporo", "01100"),
    "sagamihara": City("相模原市", "Sagamihara", "14150"),
    "shizuoka": City("静岡市", "Shizuoka", "22100"),
    "hamamatsu": City("浜松市", "Hamamatsu", "22130"),
    "hiroshima": City("広島市", "Hiroshima", "34100"),
}
BY_NATIVE = {city.native: key for key, city in CITIES.items()}
# Sagamihara's municipality code changed from 14209 to 14150 when it became a designated city; jp.mlit dates the
# switch of its area to April 2012.
FORMER_AREA = {"sagamihara": ("14209", date(2012, 4, 1))}
# Rows production already holds, written by other pipelines: the Hiroshima polity (jp.local_referendum) and the
# municipality units of Sapporo and Sagamihara (jp.mic) and Hiroshima (jp.local_referendum).
HELD_POLITIES = {"hiroshima"}
HELD_UNITS = {"sapporo", "sagamihara", "hiroshima"}


@dataclass(frozen=True)
class Book:
    number: int
    held: date
    url: str


BOOKS = {
    2015: Book(18, date(2015, 4, 12), "https://www.soumu.go.jp/main_content/000478707.pdf"),
    2019: Book(19, date(2019, 4, 7), "https://www.soumu.go.jp/main_content/000786840.pdf"),
}
BOOK_TITLE = "地方選挙結果調（第{number}回統一地方選挙）"

# Figures a book prints that its own other tables contradict. The 2019 book repeats the 2015 book's turnout rows for
# Shizuoka and Hamamatsu digit for digit, and prints their valid votes again as ballots cast, so those three figures
# are left out; the candidates' votes, valid votes, and invalid votes stay.
DROPPED = {
    (2019, "shizuoka"): ("registered", "voters", "cast"),
    (2019, "hamamatsu"): ("registered", "voters", "cast"),
}
DROPPED_NOTE = (
    "The ministry's 2019 book repeats its 2015 turnout row for this city digit for digit (electors and voters) and "
    "prints the valid votes again as ballots cast, so the archive leaves out electors, voters, and ballots cast for "
    "this election and keeps the candidates' votes, valid votes, and invalid votes the book prints."
)
METHOD = (
    "Candidates, their votes, and the winner (○) come from the table 指定都市の長の開票結果に関する調 in the appendix "
    "of the ministry's {title} (page {page} of the PDF). Electors and voters on election day come from the city's row "
    "of 有権者数・投票者数・棄権者数及び投票率に関する調 (page {turnout}), and ballots cast, valid ballots, and invalid "
    "ballots from its row of 投票総数・有効投票数及び無効投票数等に関する調 (page {ballots}). Figures are read from the "
    "PDF's text layer. The book gives city totals only."
)


@dataclass
class Candidate:
    ballot: str
    legal: str | None
    age: int | None
    party: str
    status: str | None
    votes: int
    won: bool


@dataclass
class Count:
    city: str
    held: date
    candidates: list[Candidate] = field(default_factory=list)
    figures: dict[str, int] = field(default_factory=dict)
    source: str = ""
    urls: list[str] = field(default_factory=list)
    method: str = ""


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{city}-{year}" for year, city in elections())]


def elections() -> list[tuple[int, str]]:
    return sorted(counts(), key=lambda item: (item[0], list(CITIES).index(item[1])))


def fetch() -> None:
    raw.fetch(SOURCE, INDEX, suffix=".html")
    for book in BOOKS.values():
        raw.fetch(SOURCE, book.url, suffix=".pdf", check=_pdf)
    for record in RECORDS.values():
        for url in record.urls:
            raw.fetch(SOURCE, url, suffix=Path(url).suffix, check=_pdf if url.endswith(".pdf") else None)


def _pdf(path: Path) -> None:
    with open(path, "rb") as file:
        if file.read(5) != b"%PDF-":
            raise ValueError(f"{path}: not a PDF")


def parse() -> None:
    registry().write()
    for year, city in elections():
        election(year, city).write()


def emit() -> None:
    package.run("load", packages())


# Reading the books


def squash(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def number(text: str) -> int:
    text = squash(text)
    if not re.fullmatch(r"\d{1,3}(,\d{3})*", text):
        raise ValueError(f"{text!r} is not a count")
    return int(text.replace(",", ""))


def find(doc: pymupdf.Document, heading: str, after: int = 0) -> int:
    """The 0-based index of the first page from `after` that carries the heading and is not a contents page."""
    for index in range(after, doc.page_count):
        text = squash(doc[index].get_text())
        if heading in text and "....." not in text:
            return index
    raise ValueError(f"no page carries {heading}")


Word = tuple[float, float, str]  # the row position (down the page), the column position, the text


def words(page: pymupdf.Page) -> list[Word]:
    """The page's words with their row and column. The books print their tables turned a quarter turn, so a table
    row runs along the page's y axis: x grows down the table and y falls from the table's left to its right."""
    return [
        ((w[0] + w[2]) / 2, -(w[1] + w[3]) / 2, unicodedata.normalize("NFKC", w[4])) for w in page.get_text("words")
    ]


HEADS = {
    "候補者氏名": "name",
    "年齢": "age",
    "党派": "party",
    "新現元別": "status",
    "得票数": "votes",
    "得票数合計": "total",
}


def candidates(page: pymupdf.Page, label: str) -> dict[str, list[Candidate]]:
    """Read 指定都市の長の開票結果に関する調: one row per candidate, the ballot name above the legal name in
    parentheses, the age, party, status, and votes between them, the city and its total on its first and last row."""
    found = words(page)
    heads: dict[str, float] = {}
    for row, column, text in found:
        name = HEADS.get(squash(text))
        if name is not None and name not in heads:
            heads[name] = column
    if len(heads) != len(HEADS):
        raise ValueError(f"{label}: column heads {sorted(heads)}")
    top = min(row for row, column, text in found if squash(text) in HEADS) + 4
    order = sorted(heads.items(), key=lambda item: item[1])
    bounds = [(order[at][1] + order[at + 1][1]) / 2 for at in range(len(order) - 1)]
    if order[0][0] != "name":
        raise ValueError(f"{label}: the names are not the first column")
    name_left = heads["name"] - (bounds[0] - heads["name"])

    def column_of(column: float) -> str:
        if column < name_left:
            return "city"
        for at, bound in enumerate(bounds):
            if column < bound:
                return order[at][0]
        return order[-1][0]

    cells = [(row, column, text, column_of(column)) for row, column, text in found if row > top and text.strip()]
    anchors = sorted(row for row, _, text, kind in cells if kind == "votes" and re.fullmatch(r"[\d,]+", squash(text)))
    if not anchors:
        raise ValueError(f"{label}: no candidate votes")
    groups: list[list[tuple[float, float, str, str]]] = [[] for _ in anchors]
    for cell in cells:
        if cell[0] > anchors[-1] + 14:
            continue
        nearest = min(range(len(anchors)), key=lambda at: abs(anchors[at] - cell[0]))
        groups[nearest].append(cell)
    out: dict[str, list[Candidate]] = {}
    city: str | None = None
    for group in groups:
        by = {
            kind: sorted([c for c in group if c[3] == kind], key=lambda c: (round(c[0]), c[1]))
            for kind in HEADS.values()
        }
        places = squash("".join(c[2] for c in sorted([c for c in group if c[3] == "city"], key=lambda c: c[1])))
        won = "○" in places
        place = places.replace("○", "")
        if place:
            if place not in BY_NATIVE and not place.endswith("市"):
                raise ValueError(f"{label}: {place!r} is not a city")
            city = place
            if city in out:
                raise ValueError(f"{label}: {city} appears twice")
            out[city] = []
        if city is None:
            raise ValueError(f"{label}: a candidate precedes the first city")
        name_lines: list[list[tuple[float, float, str, str]]] = []
        for c in sorted(by["name"]):
            if name_lines and c[0] - name_lines[-1][0][0] < 3:
                name_lines[-1].append(c)
            else:
                name_lines.append([c])
        texts = ["".join(c[2] for c in sorted(line, key=lambda c: c[1])).strip() for line in name_lines]
        legals = [text for text in texts if text.startswith(("(", "（"))]
        ballots = [text for text in texts if not text.startswith(("(", "（"))]
        if len(legals) > 1 or len(ballots) != 1:
            raise ValueError(f"{label}: {city} candidate names {texts}")
        ballot = ballots[0]
        legal = legals[0] if legals else ""
        votes = [c for c in by["votes"] if re.fullmatch(r"[\d,]+", squash(c[2]))]
        ages, statuses = by["age"], by["status"]
        if len(votes) != 1 or len(ages) != 1 or len(statuses) != 1:
            raise ValueError(f"{label}: {city} candidate {ballot!r} has {len(votes)} votes, {len(ages)} ages")
        party = squash("".join(c[2] for c in sorted(by["party"], key=lambda c: (c[0], c[1]))))
        out[city].append(
            Candidate(
                ballot=spaced(ballot),
                legal=spaced(legal.strip().strip("()（）")) if legal else spaced(ballot),
                age=int(squash(ages[0][2])),
                party=party,
                status=squash(statuses[0][2]),
                votes=number(votes[0][2]),
                won=won,
            )
        )
        if by["total"]:
            total = number(by["total"][0][2])
            if sum(c.votes for c in out[city]) != total:
                raise ValueError(f"{label}: {city}'s candidates do not add up to {total}")
            city = None
    if city is not None:
        raise ValueError(f"{label}: {city} has no total")
    return out


# Names whose print leaves no wider gap between surname and given name than between their other characters.
SPACING = {
    "松 井 かずみ": "松井 かずみ",
    "中野渡旬": "中野渡 旬",
    "野澤正司": "野澤 正司",
    "山本りょうたろう": "山本 りょうたろう",
}


def spaced(text: str) -> str:
    """A name with one space between surname and given name where the print leaves a wide gap."""
    text = unicodedata.normalize("NFKC", text).strip()
    parts = [part for part in re.split(r"\s{2,}", text) if part]
    name = " ".join(part.replace(" ", "") for part in parts)
    return SPACING.get(name, name)


def table_rows(page: pymupdf.Page) -> dict[str, list[str]]:
    """The designated-city rows of a table page, read from word positions: the city's figures left to right."""
    merged: list[list[Word]] = []
    for word in sorted(words(page)):
        if merged and word[0] - merged[-1][0][0] < 2.5:
            merged[-1].append(word)
        else:
            merged.append([word])
    out: dict[str, list[str]] = {}
    for line in merged:
        ordered = sorted(line, key=lambda w: w[1])
        text = [squash(w[2]) for w in ordered]
        head = ""
        for at, token in enumerate(text):
            head += token
            if head in BY_NATIVE:
                out[head] = [t for t in text[at + 1 :] if t]
                break
            if len(head) > 5:
                break
    return out


def read(year: int) -> dict[str, Count]:
    book = BOOKS[year]
    label = f"{SOURCE} {year}"
    doc = pymupdf.open(raw.cached(SOURCE, book.url).path)
    appendix = find(doc, "開票結果に関する調", find(doc, "指定都市の", doc.page_count - 80))
    turnout_page = find(doc, "有権者数・投票者数・棄権者数及び投票率に関する調(市区長)")
    ballots_page = find(doc, "投票総数・有効投票数及び無効投票数等に関する調(市区町村長)")
    people = candidates(doc[appendix], label)
    turnout = table_rows(doc[turnout_page])
    ballots = table_rows(doc[ballots_page])
    out: dict[str, Count] = {}
    for native, running in people.items():
        if native not in BY_NATIVE:
            continue
        key = BY_NATIVE[native]
        count = Count(key, book.held, running, source=BOOK_SOURCE, urls=[book.url])
        count.method = METHOD.format(
            title=BOOK_TITLE.format(number=book.number),
            page=appendix + 1,
            turnout=turnout_page + 1,
            ballots=ballots_page + 1,
        )
        t = turnout.get(native)
        b = ballots.get(native)
        if t is None or len(t) != 12 or b is None or len(b) != 8:
            raise ValueError(f"{label}: {native} turnout {t} ballots {b}")
        men, women, registered, voted_men, voted_women, voters = (number(x) for x in t[:6])
        if men + women != registered or voted_men + voted_women != voters:
            raise ValueError(f"{label}: {native}'s men and women do not add up")
        if number(t[8]) != registered - voters:
            raise ValueError(f"{label}: {native}'s abstainers are not electors less voters")
        cast, valid, invalid = number(b[0]), number(b[2]), number(b[4])
        if valid != sum(c.votes for c in running):
            raise ValueError(f"{label}: {native}'s valid votes {valid} differ from its candidates' votes")
        count.figures = {"registered": registered, "voters": voters, "cast": cast, "valid": valid, "invalid": invalid}
        for dropped in DROPPED.get((year, key), ()):
            del count.figures[dropped]
        figures = count.figures
        if "cast" in figures and figures["cast"] != valid + invalid:
            raise ValueError(f"{label}: {native}'s ballots cast are not valid plus invalid ballots")
        if "voters" in figures and "cast" in figures and figures["cast"] > figures["voters"]:
            raise ValueError(f"{label}: {native}'s ballots cast exceed its voters")
        if sum(c.won for c in running) != 1:
            raise ValueError(f"{label}: {native} marks {sum(c.won for c in running)} winners")
        out[key] = count
    return out


_COUNTS: dict[tuple[int, str], Count] = {}


def counts() -> dict[tuple[int, str], Count]:
    """Every election this pipeline loads: the books' counts, then the commissions' files for other years."""
    if not _COUNTS:
        for year in BOOKS:
            for city, count in read(year).items():
                _COUNTS[year, city] = count
        ballots = {(n.city, c.ballot): c.legal for n in _COUNTS.values() for c in n.candidates}
        for (year, city), record in RECORDS.items():
            count = record.reader(record, city)
            for candidate in count.candidates:
                candidate.legal = candidate.legal or ballots.get((city, candidate.ballot))
            check_count(count, f"{SOURCE} {city} {year}")
            _COUNTS[year, city] = count
    return _COUNTS


def check_count(count: Count, label: str) -> None:
    figures = count.figures
    if figures["valid"] != sum(c.votes for c in count.candidates):
        raise ValueError(f"{label}: valid votes differ from the candidates' votes")
    if figures["cast"] != figures["valid"] + figures["invalid"]:
        raise ValueError(f"{label}: ballots cast are not valid plus invalid ballots")
    if not figures["cast"] <= figures["voters"] <= figures["registered"]:
        raise ValueError(f"{label}: ballots cast, voters, and electors are out of order")
    if sum(c.won for c in count.candidates) != 1:
        raise ValueError(f"{label}: {sum(c.won for c in count.candidates)} winners")
    winner = next(c for c in count.candidates if c.won)
    if any(c.votes >= winner.votes for c in count.candidates if c is not winner):
        raise ValueError(f"{label}: the winner does not lead")


# Reading the city election commissions' files

NUMBER = re.compile(r"^\d{1,3}(?:,\d{3})*(?:\.0+)?$")


def pdf_lines(url: str) -> list[str]:
    path = raw.cached(SOURCE, url).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    return [unicodedata.normalize("NFKC", line) for line in text.splitlines()]


def figures_of(line: str) -> list[int]:
    return [
        int(float(t.replace(",", "").removesuffix("人"))) for t in line.split() if NUMBER.match(t.removesuffix("人"))
    ]


def labelled(lines: list[str], label: str) -> int:
    """The first figure on the line that starts with the label: the city total in these sheets."""
    for line in lines:
        if squash(line).lstrip("アイウエオカキクケコ").startswith(label):
            found = figures_of(line)
            if found:
                return found[0]
    raise ValueError(f"no line {label}")


def sagamihara_sheet(record: Record, city: str) -> Count:
    """相模原市長選挙 開票結果集計表 (or 開票速報): a row per candidate with the city total first, then the
    ballot lines ア to コ; and the turnout sheet's city total for the mayoral election."""
    votes, turnout = (pdf_lines(url) for url in record.urls)
    count = Count(city, record.held, source=record.source, urls=list(record.urls), method=record.method)
    for line in votes:
        if squash(line).startswith(("ア得票総数", "ア")) and "得票総数" in line:
            break
        tokens = line.split()
        if tokens and re.fullmatch(r"\d{1,2}", tokens[0]):
            tokens = tokens[1:]
        won = "当" in tokens
        tokens = [t for t in tokens if t not in ("当", "落")]
        figures = [t for t in tokens if NUMBER.match(t)]
        head = [t for t in tokens if not NUMBER.match(t)]
        if not figures or len(head) < 2 or tokens[: len(head)] != head:
            continue
        party, name = head[0], " ".join(head[1:])
        count.candidates.append(Candidate(name, None, None, party, None, figures_of(line)[-len(figures)], won))
    count.figures = {
        "valid": labelled(votes, "有効投票数"),
        "invalid": labelled(votes, "無効投票数"),
        "cast": labelled(votes, "投票総数"),
        "voters": labelled(votes, "投票者総数"),
    }
    count.figures["registered"], voters = record.turnout(turnout)
    if voters != count.figures["voters"]:
        raise ValueError(f"{city} {record.held}: the turnout sheet's voters differ from the count's")
    return count


def sagamihara_turnout_2023(lines: list[str]) -> tuple[int, int]:
    at = next(i for i, line in enumerate(lines) if "〔相模原市長選挙〕" in line)
    total = next(line for line in lines[at:] if squash(line).startswith("計"))
    found = figures_of(total)
    return found[2], found[5]


def sagamihara_turnout_2011(lines: list[str]) -> tuple[int, int]:
    at = next(i for i, line in enumerate(lines) if squash(line).startswith("市長当日"))
    electors = next(line for line in reversed(lines[:at]) if squash(line).startswith("計"))
    voters = next(line for line in lines[at:] if "投票者数" in line)
    return figures_of(electors)[3], figures_of(voters)[3]


def sapporo_workbook(record: Record, city: str) -> Count:
    """札幌市長選挙 開票結果集計表 and 投票者数調全市集計表: the city column (計) of each workbook."""
    count = Count(city, record.held, source=record.source, urls=list(record.urls), method=record.method)
    votes, turnout = (xlrd.open_workbook(str(raw.cached(SOURCE, url).path)).sheet_by_index(0) for url in record.urls)
    head = [squash(str(v)) for v in votes.row_values(2)]
    total = head.index("計")
    lines: dict[str, int] = {}
    for at in range(4, votes.nrows):
        row = votes.row_values(at)
        label = squash(str(row[1]))
        match = re.fullmatch(r"(.+)\((.+)\)", unicodedata.normalize("NFKC", str(row[1])).strip())
        if isinstance(row[0], float) and row[0] > 0 and match:
            name = re.sub(r"\s+", " ", match[1]).strip()
            count.candidates.append(Candidate(name, None, None, match[2], None, int(row[total]), False))
        elif label:
            lines[label] = int(row[total])
    winner = max(count.candidates, key=lambda c: c.votes)
    winner.won = True
    city_row = next(
        turnout.row_values(at) for at in range(turnout.nrows) if squash(str(turnout.cell_value(at, 0))) == "全市計"
    )
    count.figures = {
        "valid": lines["有効投票(A+B+C)"],
        "invalid": lines["無効投票"],
        "cast": lines["投票総数(D+E)"],
        "voters": lines["投票者数"],
        "registered": int(city_row[6]),
    }
    if int(city_row[9]) != count.figures["voters"]:
        raise ValueError(f"{city} {record.held}: the turnout workbook's voters differ from the count's")
    return count


# Ids


def changeset_key(cache: str, value: str) -> str:
    """The id the changeset path gives an entity it keys this way, so both paths agree."""
    return str(uuid5(package.NAMESPACE, f"{cache}\x1f{value}"))


def polity(city: str) -> str:
    return changeset_key("external_id:jp.polity", CITIES[city].native)


def office(city: str) -> str:
    return changeset_key("external_id:jp.office", f"mayor-{CITIES[city].code}")


def area(city: str, held: date) -> str:
    former = FORMER_AREA.get(city)
    code = former[0] if former and held < former[1] else CITIES[city].code
    return changeset_key("area:jp.municipality@None", code)


def unit(city: str) -> str:
    return changeset_key("unit:jp.municipality@None", CITIES[city].code)


def prefecture(city: str) -> int:
    return int(CITIES[city].code[:2])


def prefix(city: str) -> str:
    return f"jp/{PREFECTURES[prefecture(city) - 1][1].lower()}/{CITIES[city].english.lower()}-city"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SYSTEM = key("electoral_system", "mayor")
BOOK_SOURCE = key("source", PUBLISHER, "地方選挙結果調")
INDEPENDENT = "無所属"


def party(name: str) -> str:
    return key("party", name)


def parties() -> list[str]:
    return sorted({c.party for count in counts().values() for c in count.candidates if c.party != INDEPENDENT})


NO_LICENSE = "No license stated: facts published with attribution"


@dataclass(frozen=True)
class Commission:
    publisher: str
    title: str
    url: str


COMMISSIONS = {
    "sapporo": Commission("札幌市選挙管理委員会", "札幌市の選挙結果", "https://www.city.sapporo.jp/senkan/"),
    "sagamihara": Commission(
        "相模原市選挙管理委員会",
        "過去の選挙結果",
        "https://www.city.sagamihara.kanagawa.jp/shisei/1026766/senkyo/1026800/kekka_kako/index.html",
    ),
}


def commission_source(city: str) -> str:
    commission = COMMISSIONS[city]
    return key("source", commission.publisher, commission.title)


@dataclass(frozen=True)
class Record:
    held: date
    urls: tuple[str, ...]
    reader: Callable[[Record, str], Count]
    turnout: Callable[[list[str]], tuple[int, int]] = lambda lines: (0, 0)
    source: str = ""
    method: str = ""


SAGAMIHARA = "https://www.city.sagamihara.kanagawa.jp/_res/projects/default_project/_page_/001"
SAGAMIHARA_METHOD = (
    "The Sagamihara Election Commission's count sheet (開票結果集計表) of the mayoral election: each candidate's votes "
    "and the winner (当), and the city's valid, invalid, and cast ballots and voters (投票者総数), the city total "
    "column. Electors on election day come from the commission's turnout sheet for the same day. The sheets also give "
    "the count by ward, which the archive does not load yet."
)
SAPPORO_METHOD = (
    "The Sapporo Election Commission's count workbook (開票結果集計表) of the mayoral election, its city column (計): "
    "each candidate's votes and the city's valid, invalid, and cast ballots and voters. Electors on election day "
    "(当日有権者数) come from the commission's turnout workbook (投票者数調全市集計表). The workbook does not mark "
    "the winner; the archive records the candidate with the most votes, who passed the legal minimum of a quarter of "
    "the valid votes. The workbooks also give the count by ward, which the archive does not load yet."
)
RECORDS: dict[tuple[int, str], Record] = {
    (2011, "sagamihara"): Record(
        date(2011, 4, 10),
        (f"{SAGAMIHARA}/005/908/001_sityou.pdf", f"{SAGAMIHARA}/005/908/110410_kekka2141.pdf"),
        sagamihara_sheet,
        sagamihara_turnout_2011,
        commission_source("sagamihara"),
        SAGAMIHARA_METHOD,
    ),
    (2023, "sagamihara"): Record(
        date(2023, 4, 9),
        (f"{SAGAMIHARA}/028/387/05.pdf", f"{SAGAMIHARA}/028/387/01.pdf"),
        sagamihara_sheet,
        sagamihara_turnout_2023,
        commission_source("sagamihara"),
        SAGAMIHARA_METHOD,
    ),
    (2023, "sapporo"): Record(
        date(2023, 4, 9),
        (
            "https://www.city.sapporo.jp/senkan/kekka20230409/documents/dlc2050.xls",
            "https://www.city.sapporo.jp/senkan/kekka20230409/documents/dlc102.xls",
        ),
        sapporo_workbook,
        source=commission_source("sapporo"),
        method=SAPPORO_METHOD,
    ),
}


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The mayors of Sapporo, Sagamihara, Shizuoka, Hamamatsu, and Hiroshima, their cities and city reporting units, "
        "the parties their candidates ran for, and the mayoral electoral system, for the ministry's unified local "
        "election books",
    )
    p.external.add(JAPAN)
    p.add(
        "source",
        id=BOOK_SOURCE,
        kind="official_body",
        publisher=PUBLISHER,
        title="地方選挙結果調",
        url=INDEX,
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution=ATTRIBUTION,
    )
    p.artifact(raw.cached(SOURCE, INDEX), BOOK_SOURCE)
    for city, commission in COMMISSIONS.items():
        p.add(
            "source",
            id=commission_source(city),
            kind="official_body",
            publisher=commission.publisher,
            title=commission.title,
            url=commission.url,
            license=NO_LICENSE,
            attribution=f"出典：{commission.publisher}",
        )
    p.add("electoral_system", id=SYSTEM, polity_id=JAPAN, rules=RULES, rules_version=1)
    p.name(SYSTEM, "First past the post with a legal minimum of a quarter of the valid votes", "en", "official")
    p.name(SYSTEM, "相対多数制（法定得票数は有効投票総数の4分の1）", "ja", "native", False)
    for city, info in CITIES.items():
        parent = mic_release.prefecture_polity(prefecture(city))
        p.external.add(parent)
        city_polity = polity(city)
        if city in HELD_POLITIES:
            p.external.add(city_polity)
        else:
            p.add("polity", id=city_polity, kind="municipality", iso_code=None)
            p.add("external_id", entity_id=city_polity, scheme="jp.polity", value=info.native)
            p.name(city_polity, info.english, "en", "official")
            p.name(city_polity, info.native, "ja", "native", False)
            p.add("polity_parent", polity_id=city_polity, parent_id=parent)
            territory = changeset_key("area:jp.municipality@None", info.code)
            p.external.add(territory)
            p.add("polity_territory", polity_id=city_polity, area_id=territory)
            p.add("slug", path=prefix(city), entity_id=city_polity, is_current=True)
        mayor = office(city)
        p.add("office", id=mayor, polity_id=city_polity, kind="mayor")
        p.add("external_id", entity_id=mayor, scheme="jp.office", value=f"mayor-{info.code}")
        p.name(mayor, f"Mayor of {info.english}", "en", "official")
        p.name(mayor, f"{info.native}長", "ja", "native", False)
        city_unit = unit(city)
        if city in HELD_UNITS:
            p.external.add(city_unit)
        else:
            parent_unit = changeset_key("unit:jp.prefecture@None", info.code[:2])
            territory = changeset_key("area:jp.municipality@None", info.code)
            p.external |= {parent_unit, territory, "jp.municipality"}
            p.add(
                "reporting_unit",
                id=city_unit,
                polity_id=JAPAN,
                scheme_code="jp.municipality",
                code=info.code,
                parent_id=parent_unit,
                area_relation="equals",
                area_id=territory,
                kind="standard",
            )
    for name in parties():
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=JAPAN)
        p.name(id, name, "ja", "official")
    people: dict[str, tuple[str, set[str]]] = {}
    for year, city in elections():
        for candidate in counts()[year, city].candidates:
            legal, ballots = people.setdefault(person_of(city, candidate), (legal_name(candidate), set()))
            if legal != legal_name(candidate):
                raise ValueError(f"{SOURCE}: {legal} and {candidate.legal} share a person key")
            if candidate.ballot != legal:
                ballots.add(candidate.ballot)
    known = {person_of(n.city, c) for n in counts().values() for c in n.candidates if c.legal}
    for person, (legal, ballots) in sorted(people.items()):
        p.add("person", id=person)
        if person in known:
            p.name(person, legal, "ja", "official")
        else:
            # The source prints only the name on the ballot.
            p.name(person, legal, "ja", "ballot")
        for ballot in sorted(ballots):
            p.name(person, ballot, "ja", "ballot", False)
    return p


def person_of(city: str, candidate: Candidate) -> str:
    """A candidate's person: one per legal name within a city, so a mayor who runs again keeps one record."""
    return key("person", CITIES[city].code, legal_name(candidate).replace(" ", ""))


def legal_name(candidate: Candidate) -> str:
    return candidate.legal or candidate.ballot


def election(year: int, city: str) -> Package:
    count = counts()[year, city]
    info = CITIES[city]
    held = count.held
    label = f"{SOURCE}/{city}-{year}"
    p = Package(
        label,
        SOURCE,
        f"The {year} {info.english} mayoral election of {held:%-d %B %Y}, city totals from "
        + (
            "the ministry's unified local election book"
            if count.source == BOOK_SOURCE
            else "the city election commission"
        ),
        depends=[f"{SOURCE}/registry"],
    )
    district = area(city, held)
    city_polity, city_unit = polity(city), unit(city)
    p.external |= {district}
    if city in HELD_POLITIES:
        p.external.add(city_polity)
    if city in HELD_UNITS:
        p.external.add(city_unit)
    for url in count.urls:
        p.artifact(raw.cached(SOURCE, url), count.source)
    election_id = key("election", city, held)
    p.add(
        "election", id=election_id, polity_id=city_polity, kind="general", date_from=held, date_to=held, status="held"
    )
    p.name(election_id, f"{held.year} {info.english} mayoral election", "en", "official")
    p.name(election_id, f"{info.native}長選挙", "ja", "native", False)
    path = f"{prefix(city)}/{held}-mayor"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = key("contest", city, held)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=office(city),
        district_area_id=district,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{held.year} election for Mayor of {info.english}", "en", "official")
    p.add("slug", path=f"{path}/mayor", entity_id=contest, is_current=True)
    result_set = key("result_set", city, held)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=count.source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=count.method, lang="en")
    if (year, city) in DROPPED:
        p.add(
            "note",
            id=key("note", result_set, "dropped"),
            entity_id=result_set,
            kind="caveat",
            body_md=DROPPED_NOTE,
            lang="en",
        )

    def value(option: str | None, measure: int, amount: int) -> None:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=city_unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=amount,
        )

    valid = count.figures["valid"]
    for position, candidate in enumerate(count.candidates, 1):
        option = key("option", city, held, position)
        member = None if candidate.party == INDEPENDENT else party(candidate.party)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=member,
            ballot_position=position,
            ballot_label=candidate.ballot,
        )
        person = person_of(city, candidate)
        p.external.add(person)
        p.add(
            "candidacy",
            id=key("candidacy", city, held, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate.ballot,
            member_of_party_id=member,
            is_independent=member is None,
            is_incumbent=None if candidate.status is None else candidate.status == "現",
        )
        value(option, VOTES, candidate.votes)
        if candidate.won:
            if candidate.votes * 4 < valid:
                raise ValueError(f"{label}: the winner falls short of the legal minimum")
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
    for name, measure in (
        ("registered", REGISTERED),
        ("voters", VOTERS),
        ("cast", CAST),
        ("valid", VALID),
        ("invalid", INVALID),
    ):
        if name in count.figures:
            value(None, measure, count.figures[name])
    return p
