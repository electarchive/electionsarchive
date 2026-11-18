"""North Macedonia: the local elections of 2005, 2013, and 2017 from the State Election Commission.

The Commission's archive (sec.mk, kept at izbornaarhiva.mk) holds each local election as announcements and tables.
The files within reach give the following, by municipality and, for Skopje, by the City:

- 2005: one spreadsheet with the elected mayor of every municipality (the votes of the winner and the round that
  elected them), every council list with its votes, seats, and leader, and the registered voters and voters of
  each municipality. 84 municipalities and the City of Skopje.
- 2013: one table per council with the lists that won seats, their votes and seats, and the councillors elected;
  the table of the mayoral candidates that went to a second round, with their first-round votes. The council of
  Aerodrom is not among the files.
- 2017: the Commission's announcements of the seats won in each council and of the mayors elected in the first
  round or going to the second, with the votes of the candidates named.

Councils are elected by closed lists, apportioned by the D'Hondt divisors. Mayors are elected in up to two rounds.
The files print no valid-ballot totals, so the lists and candidates are stored as printed and not summed.
The announcements of 2013 and 2017 are PDF files whose font maps Cyrillic letters to other code points; the text is
decoded here, and every list's councillors are counted against its printed seats.
"""

import collections
import functools
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from uuid import uuid5
from xml.etree import ElementTree

import pymupdf
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.mk import sec

SOURCE = "mk.sec_local"
MACEDONIA = str(uuid5(package.NAMESPACE, "polity\x1fMK"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fMKD"))
HOME = "https://www.sec.mk/"
FILES = "browser://20261118-083102-6a65e6/"
INBOX = "fetched"
S_MUNI, S_CITY = "mk.opstina", "mk.grad"
VOTES, REGISTERED, BALLOTS = 1, 2, 3
PLAN_2004 = date(2004, 8, 11)
PLAN_2013 = sec.PLAN
DAYS = {
    2005: (date(2005, 3, 13), date(2005, 3, 27)),
    2013: (date(2013, 3, 24), date(2013, 4, 7)),
    2017: (date(2017, 10, 15), date(2017, 10, 29)),
}
SKOPJE_MUNICIPALITIES = {
    "Аеродром",
    "Бутел",
    "Гази Баба",
    "Ѓорче Петров",
    "Карпош",
    "Кисела Вода",
    "Сарај",
    "Центар",
    "Чаир",
    "Шуто Оризари",
}
OLD = {"Другово": "Drugovo", "Осломеј": "Oslomej", "Вранештица": "Vraneshtitsa", "Зајас": "Zajas"}
OLD_KICHEVO = "Кичево@2004"
CITY = "Град Скопје"
CITY_ENGLISH = "City of Skopje"
RULES_COUNCIL = {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}}
RULES_MAYOR = {"formula": "two_round", "majority": 0.5, "advance": 2}

FILE_2005 = "Rezultati_Osvoeni-mandati_gradonacalnici-i-sovetnici_Lokalni-izbori_2005.xlsx"
COUNCILS_2013 = "Osvoeni_mandati_rezultati_sovetnici_lokalni_izbori_2013-"
RUNOFF_2013 = "Kandidati-za-gradonacalnici-za-vtor-krug_lokalni-izbori-2013.pdf"
COUNCILS_2017 = "Соопштение-за-распределба-на-мандатите-за-совети-на-општина.pdf"
CITY_COUNCIL_2017 = "Соопштение-за-распределба-на-мандатите-на-совет-на-град-Скопје.pdf"
MAYORS_2017 = "Соопштение-за-избор-на-градоначалници-и-за-кандидати-за-втор-круг.pdf"
CITY_MAYOR_2017 = "Соопштение-за-избор-на-градоначалник-на-град-Скопје.pdf"
TOTALS_2017 = "Вкупни-резултати-од-прв-круг-од-локални-избори-2017-Интегриран-изборен-информатички-систем-на-ДИК.pdf"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SEC = key("source", "sec")
SHAPE_SOURCE = key("source", "geoboundaries")
COUNCILS = key("body", "municipal-councils")
CITY_COUNCIL = key("body", "skopje-council")
MAYOR = key("office", "mayor")
CITY_MAYOR = key("office", "skopje-mayor")
SYSTEM_COUNCIL = key("electoral_system", "council")
SYSTEM_MAYOR = key("electoral_system", "mayor")


def slug(text: str) -> str:
    return sec.slug(text)


# The raw files.


def fetch() -> None:
    """Take the files the archive's fetchers saved into the raw cache, and the municipal outlines."""
    folder = raw.DATA / "inbox" / INBOX
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    for entry in index.values():
        if not str(entry["source"]).startswith(FILES):
            continue
        name = str(entry["source"]).removeprefix(FILES)
        if name not in wanted():
            continue
        content = (folder / str(entry["file"])).read_bytes()
        raw.store(
            SOURCE,
            str(entry["source"]),
            content,
            suffix=Path(str(entry["file"])).suffix,
            retrieved_at=datetime.fromisoformat(str(entry["retrieved_at"])),
            media_type=str(entry["media_type"]),
        )
    raw.fetch(SOURCE, sec.SHAPES, suffix=".geojson")


def wanted() -> set[str]:
    names = {FILE_2005, RUNOFF_2013, COUNCILS_2017, CITY_COUNCIL_2017, MAYORS_2017, CITY_MAYOR_2017, TOTALS_2017}
    return names | {name for name in _council_files_2013_all()}


def _council_files_2013_all() -> list[str]:
    folder = raw.DATA / "inbox" / INBOX
    if not (folder / "index.json").exists():
        return sorted(name for name in _cached_names() if name.startswith(COUNCILS_2013))
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    return [
        str(entry["source"]).removeprefix(FILES)
        for entry in index.values()
        if str(entry["source"]).removeprefix(FILES).startswith(COUNCILS_2013)
    ]


def _cached_names() -> list[str]:
    return [url.removeprefix(FILES) for url in raw.keys(SOURCE) if url.startswith(FILES)]


def artifact(name: str) -> raw.Artifact:
    return raw.cached(SOURCE, FILES + name)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for day in ELECTIONS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


# Names of places.


LATIN = str.maketrans("ABCEHJKMOPTXY", "АВСЕНЈКМОРТХУ")
ALIASES = {
    "ЧЕШИНОВО": "Чешиново - Облешево",
    "ДЕБРЦА": "Дебарца",
    "МАВРОВОИРОСТУШЕ": "Маврово и Ростуша",
    "ГРАДСКОПЈЕ": CITY,
    "СКОПЈЕ": CITY,
}


def canon(text: str) -> str:
    return "".join(c for c in text.translate(LATIN).upper() if c.isalpha())


MUNICIPALITIES = {**sec.MUNICIPALITIES, **{name: english for name, english in OLD.items()}}
BY_CANON = {canon(name): name for name in MUNICIPALITIES} | {canon(CITY): CITY} | ALIASES
ENGLISH = {**MUNICIPALITIES, CITY: CITY_ENGLISH, OLD_KICHEVO: "Kichevo"}


def municipality(text: str) -> str:
    found = BY_CANON.get(canon(text))
    if found is None:
        raise ValueError(f"unknown municipality {text!r}")
    return found


def title(text: str) -> str:
    return " ".join(part.capitalize() for part in " ".join(text.split()).split(" "))


def area_of(name: str, year: int) -> str:
    if name == CITY:
        return key("area", S_CITY, "skopje")
    if name == "Кичево" and year < 2013:
        return key("area", S_MUNI, OLD_KICHEVO)
    return key("area", S_MUNI, name)


# What the files print.


@dataclass
class Row:
    """A list or a candidate as printed."""

    ballot: int | None
    label: str
    votes: int | None
    seats: int | None = None
    status: str = ""
    leader: str = ""
    names: list[str] = field(default_factory=list)


@dataclass
class Place:
    registered: int | None = None
    voted: int | None = None
    council_seats: int | None = None
    council: list[Row] = field(default_factory=list)
    mayors: list[Row] = field(default_factory=list)


NBSP = "\xa0"


def number(text: object) -> int | None:
    if text is None:
        return None
    cleaned = str(text).replace(NBSP, "").replace(".", "").replace(" ", "")
    return int(cleaned) if cleaned.isdigit() else None


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def sheets(path: Path) -> list[list[dict[str, object]]]:
    """The rows of every sheet of a spreadsheet, as dicts from column letter to value."""
    archive = zipfile.ZipFile(path)
    strings: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", NS):
            strings.append("".join(t.text or "" for t in item.iter(f"{{{NS['m']}}}t")))
    book = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    targets = {
        r.get("Id"): str(r.get("Target")) for r in ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    }
    out: list[list[dict[str, object]]] = []
    for sheet in book.find("m:sheets", NS) or []:
        target = targets[str(sheet.get(REL))].lstrip("/")
        rows: list[dict[str, object]] = []
        for row in ElementTree.fromstring(archive.read(target if target.startswith("xl/") else f"xl/{target}")).iter(
            f"{{{NS['m']}}}row"
        ):
            cells: dict[str, object] = {}
            for cell in row.findall("m:c", NS):
                column = re.match("[A-Z]+", str(cell.get("r")))
                value = cell.find("m:v", NS)
                if column is None or value is None:
                    continue
                cells[column.group()] = strings[int(str(value.text))] if cell.get("t") == "s" else value.text
            rows.append(cells)
        out.append(rows)
    return out


@functools.cache
def results_2005() -> dict[str, Place]:
    places: dict[str, Place] = {}
    place: Place | None = None
    mode = ""
    for rows in sheets(artifact(FILE_2005).path):
        for row in rows:
            first = row.get("B")
            if isinstance(first, str) and re.match(r"^\d+[\s\xa0]+\D", first) and not row.get("F"):
                name = municipality(re.sub(r"^\d+[\s\xa0]+", "", first))
                place = places[name] = Place()
                mode = ""
                continue
            if place is None or first is None:
                continue
            text = str(first).strip()
            if text == "Запишани избирачи во ИС":
                place.registered = number(row.get("G"))
            elif text == "Излегле на гласање":
                place.voted = number(row.get("G"))
            elif text == "Градоначалници":
                mode = "mayor"
            elif text == "Советници":
                mode = "council"
            elif text == "Советнички места":
                place.council_seats = number(row.get("G"))
            elif mode == "mayor" and re.match(r"^\d+\.", text) and row.get("F") is not None:
                place.mayors.append(
                    Row(
                        ballot=int(text.split(".")[0]),
                        label=str(row.get("E", "")).strip(),
                        votes=number(row.get("F")),
                        status=str(row.get("G", "")).strip(),
                        leader=re.sub(r"^\d+\.\s*", "", text),
                    )
                )
            elif mode == "council" and number(first) is not None and row.get("F") is not None:
                place.council.append(
                    Row(
                        ballot=number(first),
                        label=str(row.get("D", "")).strip(),
                        votes=number(row.get("F")),
                        seats=number(row.get("G")),
                        leader=str(row.get("E", "")).strip(),
                    )
                )
    return places


# The PDF font of the Commission's announcements puts Cyrillic letters at other code points.

SPECIAL = {
    "Ȯ": "Ѓ",
    "ȳ": "Ј",
    "ȴ": "Љ",
    "ȵ": "Њ",
    "ȷ": "Ќ",
    "ȹ": "Џ",
    "ʁ": "ј",
}
NUMBER = re.compile(r"^\d{1,3}(\.\d{3})*$")


def decode(text: str) -> str:
    out: list[str] = []
    for char in text:
        if char in SPECIAL:
            out.append(SPECIAL[char])
        elif 0x23A <= ord(char) <= 0x279:
            out.append(chr(ord(char) + 0x1D6))
        else:
            out.append(char)
    return "".join(out)


def pages(path: Path) -> list[pymupdf.Page]:
    document = pymupdf.open(path)
    return [document[index] for index in range(document.page_count)]


def page_lines(path: Path) -> list[tuple[int, int, list[tuple[float, str]]]]:
    """Each line of each page as (page, y, words with x), the text decoded."""
    out: list[tuple[int, int, list[tuple[float, str]]]] = []
    for index, page in enumerate(pages(path)):
        lines: dict[int, list[tuple[float, str]]] = collections.defaultdict(list)
        for word in page.get_text("words"):
            lines[round(word[1] / 3)].append((word[0], decode(word[4])))
        out.extend((index, line * 3, sorted(lines[line])) for line in sorted(lines))
    return out


def clean_label(parts: list[str]) -> str:
    return " ".join(" ".join(parts).replace("±", "–").split())


def announcement(path: Path, kind: str) -> list[tuple[str, Row]]:
    """The rows of a 2017 announcement, each with its municipality as the file prints it.

    A row starts with the list's ballot number at the left, its votes and then its seats (or status) at the
    right. Its coalition's names follow, and then, for councils, the councillors elected, one a line.
    """
    rows: list[tuple[str, Row]] = []
    place = ""
    current: Row | None = None
    last: tuple[int, int, str] | None = None
    for page, y, words in page_lines(path):
        text = " ".join(word for _, word in words)
        if y > 760 and "Страна" in text:
            continue
        if page == 0 and y < 170:
            continue
        if text.startswith(("Број на Назив", "листа Добиени", "листа гласови")):
            if text.startswith("Број на") and last is not None and last[0] == page and last[1] <= 200:
                place = last[2]
                if current is not None and current.names and current.names[-1] == place:
                    current.names.pop()
            last = None
            continue
        x = words[0][0]
        found = re.match(r"^(\d+)\.$", words[0][1])
        if found and 55 < x < 80 and len(words) >= 2:
            current = Row(ballot=int(found.group(1)), label="", votes=None)
            right = [(left, word) for left, word in words if left > 380]
            for _, word in right:
                if NUMBER.match(word) and current.votes is None:
                    current.votes = int(word.replace(".", ""))
                elif current.status == "":
                    current.status = word
            rows.append((place, current))
            inline = [word for left, word in words if 80 <= left <= 380]
            parts[id(current)] = [" ".join(inline)] if inline else []
            last = None
            continue
        if current is not None:
            left = [(left, word) for left, word in words if left <= 380]
            right = [(left, word) for left, word in words if left > 380]
            if right and current.status == "":
                current.status = " ".join(word for _, word in right)
            if left:
                line = " ".join(word for _, word in left)
                if left[0][0] >= 80:
                    parts[id(current)].append(line)
                else:
                    current.names.append(line)
        last = (page, y, text) if 36 <= x <= 37 else None
    for _, row in rows:
        row.label = clean_label(parts.pop(id(row), []))
    return rows


parts: dict[int, list[str]] = {}


def read_announcement(name: str, kind: str) -> dict[str, list[Row]]:
    found: dict[str, list[Row]] = {}
    for place, row in announcement(artifact(name).path, kind):
        name_ = CITY if canon(place) == canon("СКОПЈЕ") else municipality(place)
        if row.status.startswith("За понови") or row.status.isdigit() is False and kind == "council":
            row.status = ""
        found.setdefault(name_, []).append(row)
    return found


@functools.cache
def councils_2017() -> dict[str, list[Row]]:
    found = read_announcement(COUNCILS_2017, "council")
    for name, rows in read_announcement(CITY_COUNCIL_2017, "council").items():
        found[name] = rows
    for name, rows in found.items():
        for row in rows:
            row.seats = number(row.status)
            row.names = [line for line in row.names if not line.startswith(("За понови", "rezultati"))]
            if row.seats is None or row.seats != len(row.names):
                raise ValueError(f"{name} list {row.ballot}: {row.seats} seats and {len(row.names)} names")
    return found


@functools.cache
def mayors_2017() -> dict[str, list[Row]]:
    found = read_announcement(MAYORS_2017, "mayor")
    for name, rows in read_announcement(CITY_MAYOR_2017, "mayor").items():
        found[name] = rows
    for rows in found.values():
        for row in rows:
            row.names = [line for line in row.names if not line.startswith(("За понови", "rezultati"))]
            if len(row.names) != 1:
                raise ValueError(f"mayor list {row.ballot}: {row.names}")
            row.leader = row.names[0]
            row.names = []
    return found


def council_2013(file: str) -> tuple[str, list[Row]]:
    """A 2013 council table: the lists that won seats, each with its votes, seats, and the councillors elected."""
    path = artifact(file).path
    rows: list[Row] = []
    current: Row | None = None
    place = ""
    for page, y, words in page_lines(path):
        text = " ".join(word for _, word in words)
        if page == 0 and 150 < y < 185 and "Совет на" in text:
            place = text.split("општина")[-1] if "општина" in text else text.split("град")[-1]
        if (page == 0 and y < 215) or y > 745:
            continue
        right = [(x, word) for x, word in words if x > 395]
        if len(right) >= 2 and NUMBER.match(right[0][1]) and right[1][1].isdigit() and right[1][0] > 480:
            current = Row(ballot=None, label="", votes=int(right[0][1].replace(".", "")), seats=int(right[1][1]))
            rows.append(current)
            parts[id(current)] = []
            left = [word for x, word in words if x <= 395]
            if left and left != ["–"]:
                parts[id(current)].append(" ".join(left))
            continue
        if current is None:
            continue
        left = [(x, word) for x, word in words if x <= 395 and word != "–"]
        if not left:
            continue
        line = " ".join(word for _, word in left)
        if 88.5 <= left[0][0] <= 91.5:
            current.names.append(line)
        else:
            parts[id(current)].append(line)
    for row in rows:
        row.label = clean_label(parts.pop(id(row), []))
        if row.seats != len(row.names):
            raise ValueError(f"{file}: {row.seats} seats and {len(row.names)} names for {row.label[:40]}")
    return place, rows


@functools.cache
def councils_2013() -> dict[str, list[Row]]:
    found: dict[str, list[Row]] = {}
    for file in sorted(_cached_names()):
        if not file.startswith(COUNCILS_2013):
            continue
        place, rows = council_2013(file)
        name = CITY if file.endswith("Skopje.pdf") else municipality(place)
        found[name] = rows
    return found


@functools.cache
def runoff_2013() -> dict[str, list[Row]]:
    """The two candidates of each municipality that went to a second round, with their first-round votes."""
    lines: list[tuple[int, int, list[tuple[float, str]]]] = []
    for index, page in enumerate(pages(artifact(RUNOFF_2013).path)):
        grouped: list[tuple[float, list[tuple[float, str]]]] = []
        for word in sorted(page.get_text("words"), key=lambda w: w[1]):
            if grouped and abs(word[1] - grouped[-1][0]) < 4:
                grouped[-1][1].append((word[0], word[4]))
            else:
                grouped.append((word[1], [(word[0], word[4])]))
        lines.extend((index, round(top), sorted(words)) for top, words in grouped)
    names: list[str] = []
    candidates: list[tuple[str, int]] = []
    for index, page in enumerate(pages(artifact(RUNOFF_2013).path)):
        found = [w for w in page.get_text("words") if index > 0 or w[1] > 270]
        votes = sorted((w for w in found if 465 < w[0] < 495 and w[4].isdigit()), key=lambda w: w[1])
        text: list[tuple[float, list[str]]] = []
        for word in sorted((w for w in found if 150 < w[0] < 262), key=lambda w: (w[1], w[0])):
            if text and abs(word[1] - text[-1][0]) < 4:
                text[-1][1].append(word[4])
            else:
                text.append((word[1], [word[4]]))
        owned: dict[int, list[str]] = collections.defaultdict(list)
        for top, line in text:
            owned[min(range(len(votes)), key=lambda i, y=top: abs(votes[i][1] - y))].extend(line)
        for number_, vote in enumerate(votes):
            candidates.append((" ".join(owned[number_]), int(vote[4])))
    for page in pages(artifact(RUNOFF_2013).path):
        found = page.get_text("words")
        for index in sorted((w for w in found if w[0] < 60 and w[4].isdigit()), key=lambda w: w[1]):
            near = sorted(
                (w for w in found if 60 < w[0] < 150 and abs(w[1] - index[1]) < 14), key=lambda w: (round(w[1]), w[0])
            )
            names.append(" ".join(w[4] for w in near))
    if len(candidates) != 2 * len(names):
        raise ValueError(f"runoff 2013: {len(candidates)} candidates for {len(names)} municipalities")
    found: dict[str, list[Row]] = {}
    for index, name in enumerate(names):
        place = CITY if canon(name) == canon("ГРАДСКОПЈЕ") else municipality(name)
        found[place] = [
            Row(ballot=None, label="", votes=votes, status="runoff", leader=leader)
            for leader, votes in candidates[2 * index : 2 * index + 2]
        ]
    return found


# Parties.


LEADS = (
    (r"^(СОЦИЈАЛДЕМОКРАТСКИ СОЈУЗ НА МАКЕДОНИЈА|СДСМ)", "СДСМ", "Социјалдемократски сојуз на Македонија"),
    (r"^ВМРО.{0,3}ДПМНЕ", "ВМРО-ДПМНЕ", "ВМРО - Демократска партија за македонско национално единство"),
    (r"^(ДЕМОКРАТСКА УНИЈА ЗА ИНТЕГРАЦИЈА|ДУИ)", "ДУИ", "Демократска унија за интеграција"),
    (r"^(ДЕМОКРАТСКА ПАРТИЈА НА АЛБАНЦИТЕ|ДПА)", "ДПА", "Демократска партија на Албанците"),
    (r"^(ДВИЖЕЊЕ БЕСА|БЕСА)", "БЕСА", "Движење Беса"),
    (r"^(АЛИЈАНСА НА АЛБАНЦИТЕ|ААА)", "ААА", "Алијанса на Албанците"),
    (r"^(НОВА СОЦИЈАЛДЕМОКРАТСКА ПАРТИЈА|НСДП)", "НСДП", "Нова социјалдемократска партија"),
    (r"^ВМРО.{0,3}НАРОДНА ПАРТИЈА|^ВМРО.{0,3}НП", "ВМРО-НП", "ВМРО - Народна партија"),
    (r"^ЛЕВИЦА", "Левица", "Левица"),
)


@dataclass(frozen=True)
class Party:
    key: str
    name: str
    short: str | None
    kind: str


def party_of(label: str) -> Party:
    """The party or bloc a ballot line stands for, named after its lead party."""
    text = " ".join(label.replace("±", "–").replace("+", " ").split()).upper()
    bloc = len(text) > 110 or "И ДРУГИ" in text or label.rstrip().endswith("+")
    for pattern, short, name in LEADS:
        if re.search(pattern, text):
            return Party(
                f"{short}{'+' if bloc else ''}", f"{name} и други" if bloc else name, short, "bloc" if bloc else "party"
            )
    lead = re.split(r"\s[–-]\s|,|\s\(", " ".join(label.replace("±", "–").replace("+", " ").split()))[0].strip()
    return Party(f"{canon(lead)}{'+' if bloc else ''}", lead, None, "bloc" if bloc else "party")


def party_id(party: Party) -> str:
    return key("party", party.key)


# The elections.


@dataclass(frozen=True)
class Election:
    day: date
    year: int
    round: int


ELECTIONS = {day: Election(day, year, number_) for year, days in DAYS.items() for number_, day in enumerate(days, 1)}


def has_data(election: Election) -> bool:
    if election.year == 2013:
        return election.round == 1
    if election.year == 2017:
        return election.round == 1
    return True


ELECTIONS = {day: item for day, item in ELECTIONS.items() if has_data(item)}


def all_rows() -> list[Row]:
    rows: list[Row] = []
    for place in results_2005().values():
        rows.extend(place.council)
        rows.extend(place.mayors)
    for lists in councils_2013().values():
        rows.extend(lists)
    for lists in councils_2017().values():
        rows.extend(lists)
    for lists in mayors_2017().values():
        rows.extend(lists)
    return rows


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The 84 municipalities of North Macedonia's 2004 plan and the 80 of the 2013 plan, with the City of Skopje, "
        "outlined from geoBoundaries",
    )
    p.external |= {MACEDONIA, COUNTRY}
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen North Macedonia ADM2 (municipalities, 2016)",
        url="https://www.geoboundaries.org/",
        license="Creative Commons Attribution 4.0 International",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), from EuroGeographics and NTES data",
        notes="Boundary year 2016, built December 2023. The outlines of Drugovo, Oslomej, Vraneshtitsa, and Zajas stand "
        "alone for the 2004 plan and are merged into Kichevo for the 2013 plan.",
    )
    shapes = raw.cached(SOURCE, sec.SHAPES)
    p.artifact(shapes, SHAPE_SOURCE)
    for code, rank, kind, noun in (
        (S_CITY, 1, "administrative", "city"),
        (S_MUNI, 2, "administrative", "municipality"),
    ):
        p.add("scheme", code=code, polity_id=MACEDONIA, kind=kind, rank=rank, noun=noun, code_system=None)
    for vintage, since, until in (("2004", PLAN_2004, PLAN_2013), ("2013", PLAN_2013, None)):
        p.add(
            "boundary_set",
            id=key("boundary_set", S_MUNI, vintage),
            scheme_code=S_MUNI,
            vintage=vintage,
            valid_from=since,
            valid_to=until,
            as_of=shapes.retrieved_at.date(),
        )
    alone: dict[str, shapely.Geometry] = {}
    for feature in json.loads(shapes.path.read_text(encoding="utf-8"))["features"]:
        alone[feature["properties"]["shapeName"]] = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))

    def outline(id: str, shape: shapely.Geometry, since: date, until: date | None) -> None:
        polygons = [
            part
            for geometry in shapely.get_parts(shape)
            if shapely.get_type_id(geometry) in (3, 6)
            for part in shapely.get_parts(geometry)
        ]
        multi = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=since,
            valid_to=until,
            geom=shapely.to_wkb(multi, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(multi), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPE_SOURCE,
        )

    city = key("area", S_CITY, "skopje")
    p.add("area", id=city, scheme_code=S_CITY)
    p.add("area_code", area_id=city, code="skopje", valid_from=PLAN_2004)
    p.add("area_parent", area_id=city, parent_id=COUNTRY, valid_from=PLAN_2004)
    p.name(city, CITY, "mk", "official")
    p.name(city, CITY_ENGLISH, "en", "english", False)
    members = [alone[MUNICIPALITIES[name]] for name in sorted(SKOPJE_MUNICIPALITIES)]
    outline(city, shapely.union_all(members), PLAN_2004, None)

    def place(name: str, english: str, id: str, shape: shapely.Geometry, since: date, until: date | None) -> None:
        p.add("area", id=id, scheme_code=S_MUNI)
        p.add("area_code", area_id=id, code=slug(english), valid_from=since, valid_to=until)
        parent = city if name in SKOPJE_MUNICIPALITIES else COUNTRY
        p.add("area_parent", area_id=id, parent_id=parent, valid_from=since, valid_to=until)
        p.name(id, name.removesuffix("@2004"), "mk", "official")
        p.name(id, english, "en", "english", False)
        outline(id, shape, since, until)

    for name, english in sorted(sec.MUNICIPALITIES.items()):
        if name == "Кичево":
            merged = [alone[english], *(alone[old] for old in OLD.values())]
            place(name, english, key("area", S_MUNI, name), shapely.union_all(merged), PLAN_2013, None)
            place(name, english, key("area", S_MUNI, OLD_KICHEVO), alone[english], PLAN_2004, PLAN_2013)
        else:
            place(name, english, key("area", S_MUNI, name), alone[english], PLAN_2004, None)
    for name, english in sorted(OLD.items()):
        place(name, english, key("area", S_MUNI, name), alone[english], PLAN_2004, PLAN_2013)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The councils and mayors of North Macedonia's municipalities and the City of Skopje, their electoral systems, "
        "and the parties on the ballots of the 2005, 2013, and 2017 local elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {MACEDONIA}
    p.add(
        "source",
        id=SEC,
        kind="official_body",
        publisher="Државна изборна комисија",
        title="Local elections: announcements and results",
        url=HOME,
        license="No license stated; published by the State Election Commission for public use",
        attribution="Државна изборна комисија на Република Македонија",
        notes="The Commission's tables and announcements of the results of the local elections, as kept in its "
        "electoral archive.",
    )
    for id, kind, english, native in (
        (COUNCILS, "council", "Municipal council", "Совет на општина"),
        (CITY_COUNCIL, "council", "Council of the City of Skopje", "Совет на град Скопје"),
    ):
        p.add("body", id=id, polity_id=MACEDONIA, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, native, "mk", "official", False)
    for id, english, native in (
        (MAYOR, "Mayor of a municipality", "Градоначалник на општина"),
        (CITY_MAYOR, "Mayor of the City of Skopje", "Градоначалник на град Скопје"),
    ):
        p.add("office", id=id, polity_id=MACEDONIA, kind="mayor")
        p.name(id, english, "en", "official")
        p.name(id, native, "mk", "official", False)
    for id, rules, english, text in (
        (
            SYSTEM_COUNCIL,
            RULES_COUNCIL,
            "Closed lists and D'Hondt in each municipality",
            (
                "Each municipal council, and the Council of the City of Skopje, is elected for four years by closed "
                "lists in the whole municipality, with seats apportioned by the D'Hondt divisors. The number of "
                "seats depends on the municipality's population, from 9 to 33 in a municipality and 45 in the City."
            ),
        ),
        (
            SYSTEM_MAYOR,
            RULES_MAYOR,
            "Mayors elected in up to two rounds",
            (
                "A candidate with a majority of the votes cast is elected in the first round. Otherwise the two "
                "candidates with the most votes stand in a second round two weeks later."
            ),
        ),
    ):
        p.add("electoral_system", id=id, polity_id=MACEDONIA, valid_from=date(2002, 1, 1), rules=rules, rules_version=1)
        p.name(id, english, "en", "official")
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    seen: dict[str, Party] = {}
    for row in all_rows():
        if row.label:
            party = party_of(row.label)
            seen.setdefault(party.key, party)
    for item in seen.values():
        id = party_id(item)
        p.add("party", id=id, kind=item.kind, polity_id=MACEDONIA)
        p.name(id, item.name, "mk", "official")
        if item.short:
            p.name(id, item.short, "mk", "abbreviation", False)
    return p


METHOD = {
    2005: "From the Commission's spreadsheet of the 2005 local elections. Each list is printed with its votes, "
    "seats, and leader.",
    2013: "From the Commission's table of the seats won in the council. Only the lists that won seats are printed, "
    "each with its votes, its seats, and the councillors elected.",
    2017: "From the Commission's announcement of the allocation of the seats in the councils. Only the lists that "
    "won seats are printed, each with its votes, its seats, and the councillors elected.",
}
MAYOR_METHOD = {
    2005: "From the Commission's spreadsheet of the 2005 local elections, which prints the elected mayor of each "
    "municipality with the votes the mayor received and the round in which the mayor was elected, and the "
    "registered voters and the voters of the municipality, which stand under the mayor's result and are stored with "
    "it. The other candidates are not printed.",
    2013: "From the Commission's table of the candidates for mayor that went to a second round, with the votes each "
    "received in the first round. The mayors elected in the first round and the other candidates are not printed.",
    2017: "From the Commission's announcement of the mayors elected in the first round and of the two candidates "
    "going to the second round in each of the other municipalities, with the votes each received in the first "
    "round. The other candidates are not printed.",
}


def build(day: date) -> Package:
    election = ELECTIONS[day]
    year, rnd = election.year, election.round
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} local elections of North Macedonia"
        + (", second round," if rnd == 2 else ", first round,")
        + " by municipality, from the State Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {MACEDONIA, COUNTRY}
    eid = key("election", day)
    p.add(
        "election",
        id=eid,
        polity_id=MACEDONIA,
        kind="runoff" if rnd == 2 else "local",
        date_from=day,
        date_to=day,
        status="held",
    )
    base = f"mk/{day}-local" + ("-runoff" if rnd == 2 else "")
    p.add("slug", path=base, entity_id=eid, is_current=True)
    p.name(eid, f"{year} North Macedonian local elections" + (", second round" if rnd == 2 else ""), "en", "official")
    p.name(eid, f"Локални избори {year}" + (", втор круг" if rnd == 2 else ""), "mk", "native", False)
    if year == 2005:
        names = [artifact(FILE_2005)]
    elif year == 2013:
        names = [artifact(name) for name in sorted(_cached_names()) if name.startswith(COUNCILS_2013)]
        names.append(artifact(RUNOFF_2013))
    else:
        names = [artifact(name) for name in (COUNCILS_2017, CITY_COUNCIL_2017, MAYORS_2017, CITY_MAYOR_2017)]
    for item in names:
        p.artifact(item, SEC)
    if year == 2017:
        p.uses(artifact(TOTALS_2017))
    context = Context(p, election, eid, base)
    if year == 2005:
        build_2005(context)
    elif year == 2013:
        build_2013(context)
    else:
        build_2017(context)
    accept(p, year)
    return p


def accept(p: Package, year: int) -> None:
    del p, year


@dataclass
class Context:
    p: Package
    election: Election
    eid: str
    base: str
    units: dict[str, str] = field(default_factory=dict)

    @property
    def year(self) -> int:
        return self.election.year

    def unit(self, name: str) -> str:
        if name in self.units:
            return self.units[name]
        scheme = S_CITY if name == CITY else S_MUNI
        id = key("unit", self.election.day, name)
        self.p.add(
            "reporting_unit",
            id=id,
            polity_id=MACEDONIA,
            scheme_code=scheme,
            code=f"{self.election.day}:{slug(ENGLISH[name])}",
            area_relation="equals",
            area_id=area_of(name, self.year),
            kind="aggregate",
        )
        self.p.name(id, ENGLISH[name], "en", "official")
        self.units[name] = id
        return id

    def contest(self, name: str, mayor: bool, magnitude: int) -> tuple[str, str]:
        p = self.p
        tag = ("mayor" if mayor else "council", self.election.day, name)
        cid = key("contest", *tag)
        city = name == CITY
        what = "mayor" if mayor else "council"
        p.add(
            "contest",
            id=cid,
            election_id=self.eid,
            kind="office" if mayor else "body_seats",
            office_id=(CITY_MAYOR if city else MAYOR) if mayor else None,
            body_id=None if mayor else (CITY_COUNCIL if city else COUNCILS),
            district_area_id=area_of(name, self.year),
            magnitude=magnitude,
            round_number=self.election.round if mayor else 1,
            electoral_system_id=SYSTEM_MAYOR if mayor else SYSTEM_COUNCIL,
            default_result_kind="counted",
        )
        p.name(cid, f"{ENGLISH[name]} {what}", "en", "official")
        p.add("slug", path=f"{self.base}/{slug(ENGLISH[name])}-{what}", entity_id=cid, is_current=True)
        rs = key("result_set", *tag)
        p.add(
            "result_set",
            id=rs,
            election_id=self.eid,
            contest_id=cid,
            stage="official",
            kind="counted",
            source_id=SEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        text = (MAYOR_METHOD if mayor else METHOD)[self.year]
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=text, lang="en")
        return cid, rs

    def value(self, rs: str, unit: str, option: str | None, measure: int, value: int) -> None:
        self.p.add(
            "result_value",
            result_set_id=rs,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )

    def council(self, name: str, rows: list[Row], seats: int | None = None) -> tuple[str, str]:
        total = sum(row.seats or 0 for row in rows)
        if seats is not None and seats != total:
            raise ValueError(f"{name}: council of {seats} seats but lists with {total}")
        cid, rs = self.contest(name, False, seats or total)
        unit = self.unit(name)
        for position, row in enumerate(rows, 1):
            tag = ("list", self.election.day, name, row.ballot or position)
            option = key("option", *tag)
            party = party_of(row.label)
            self.p.add(
                "contest_option",
                id=option,
                contest_id=cid,
                kind="list",
                party_id=party_id(party),
                ballot_position=row.ballot or position,
                ballot_label=row.label,
            )
            if row.leader:
                person = key("person", *tag, "leader")
                self.p.add("person", id=person)
                self.p.name(person, title(row.leader), "mk", "official")
                self.p.add(
                    "candidacy",
                    id=key("candidacy", *tag, "leader"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    list_position=1,
                    ballot_name=row.leader,
                    member_of_party_id=party_id(party),
                    is_independent=False,
                )
            if row.votes is not None:
                self.value(rs, unit, option, VOTES, row.votes)
            seats_won = row.seats or 0
            self.p.add(
                "outcome", result_set_id=rs, option_id=option, kind="won" if seats_won else "lost", seats=seats_won
            )
            for index, member in enumerate(row.names, 1):
                member_tag = (*tag, index)
                person = key("person", *member_tag)
                self.p.add("person", id=person)
                self.p.name(person, title(member), "mk", "official")
                candidacy = key("candidacy", *member_tag)
                self.p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    list_position=index,
                    ballot_name=member,
                    member_of_party_id=party_id(party),
                    is_independent=False,
                )
                self.p.add("outcome", result_set_id=rs, option_id=option, candidacy_id=candidacy, kind="elected")
        return cid, rs

    def mayor(self, name: str, rows: list[Row], elected: set[int] | None = None) -> tuple[str, str]:
        cid, rs = self.contest(name, True, 1)
        unit = self.unit(name)
        for position, row in enumerate(rows, 1):
            tag = ("candidate", self.election.day, name, row.ballot or position)
            option = key("option", *tag)
            party = party_of(row.label) if row.label else None
            self.p.add(
                "contest_option",
                id=option,
                contest_id=cid,
                kind="candidate",
                party_id=party_id(party) if party else None,
                ballot_position=row.ballot or position,
                ballot_label=title(row.leader),
            )
            person = key("person", *tag)
            self.p.add("person", id=person)
            self.p.name(person, title(row.leader), "mk", "official")
            self.p.add(
                "candidacy",
                id=key("candidacy", *tag),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=row.leader,
                member_of_party_id=party_id(party) if party else None,
                is_independent=False,
            )
            if row.votes is not None:
                self.value(rs, unit, option, VOTES, row.votes)
            self.p.add("outcome", result_set_id=rs, option_id=option, kind=outcome_kind(row))
        return cid, rs


def outcome_kind(row: Row) -> str:
    if row.status.startswith("Избран"):
        return "elected"
    return "advanced"


def build_2005(c: Context) -> None:
    for name, place in sorted(results_2005().items()):
        won = "Избран во прв" if c.election.round == 1 else "Избран во втор"
        if c.election.round == 1:
            c.council(name, place.council, place.council_seats)
        elected = [row for row in place.mayors if row.status.startswith(won)]
        if not elected:
            continue
        _, rs = c.mayor(name, elected)
        if place.registered is not None:
            c.value(rs, c.unit(name), None, REGISTERED, place.registered)
        if place.voted is not None:
            c.value(rs, c.unit(name), None, BALLOTS, place.voted)


def build_2013(c: Context) -> None:
    for name, rows in sorted(councils_2013().items()):
        c.council(name, rows)
    for name, rows in sorted(runoff_2013().items()):
        c.mayor(name, rows)


def build_2017(c: Context) -> None:
    for name, rows in sorted(councils_2017().items()):
        c.council(name, rows)
    for name, rows in sorted(mayors_2017().items()):
        c.mayor(name, rows)
