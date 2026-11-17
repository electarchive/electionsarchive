"""Kosovo's Assembly elections from the Central Election Commission's results archive (kqz-ks.org/rezultatet).

The archive publishes each election as a folder of result files, in xls or xlsx where the Commission still holds the
workbook and otherwise in PDF. The site answers automated clients with a browser challenge, so a person saved the
files from a browser and the pipeline reads them from the raw cache, where `store_fetched` puts them.
"""

import functools
import hashlib
import json
import os
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import fastexcel
import polars as pl
import shapely
import shapely.geometry
import xlrd

from ea import raw
from ea.package import Package
from ea.sources.xk import kqz

BROWSER = "browser://20261117-210043-f62df0/"
FETCHED = "EA_KQZ_FETCHED"


def url(day: str, name: str) -> str:
    return f"{BROWSER}{day}/{name}"


def store_fetched(folder: Path) -> int:
    """Put the files a browser saved into the raw cache; `folder` holds the saved files and their `index.json`."""
    index = json.loads((folder / "index.json").read_text())
    stored = 0
    for link, entry in index.items():
        if not link.startswith(BROWSER):
            continue
        content = (folder / entry["file"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"{link} does not match its checksum")
        raw.store(
            kqz.SOURCE,
            link,
            content,
            suffix=Path(link).suffix.lower(),
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )
        stored += 1
    return stored


def fetch() -> None:
    folder = os.environ.get(FETCHED)
    if folder:
        print(f"{store_fetched(Path(folder))} Assembly result files stored", flush=True)


# Reading the files.

VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK, VOTERS = 1, 2, 3, 4, 5, 6, 14
FLAG_DERIVED, FLAG_ABOVE = 1, 32


def cell_text(value: object) -> str:
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return kqz.clean(str(value if value is not None else "")).strip()


def number(value: str | float | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.replace("\xa0", "").replace(" ", "").replace(",", "")
        if not re.fullmatch(r"-?\d+(\.0+)?", text):
            return None
        return int(float(text))
    if float(value) != int(value):
        raise ValueError(f"{value} is not a whole number")
    return int(value)


USED: dict[str, set[str]] = defaultdict(set)


@functools.cache
def sheets(day: str, name: str) -> list[list[list[Any]]]:
    """Every sheet of a workbook as a list of rows."""
    USED[day].add(name)
    artifact = raw.cached(kqz.SOURCE, url(day, name))
    if name.lower().endswith(".xls"):
        book = xlrd.open_workbook(artifact.path)
        return [[sheet.row_values(r) for r in range(sheet.nrows)] for sheet in book.sheets()]
    book = fastexcel.read_excel(str(artifact.path))
    out = []
    for sheet in book.sheet_names:
        frame = book.load_sheet_by_name(sheet, header_row=None, dtypes="string").to_polars()
        out.append([list(row) for row in frame.iter_rows()])
    return out


@functools.cache
def pdf_text(day: str, name: str, layout: bool = True) -> str:
    USED[day].add(name)
    path = raw.cached(kqz.SOURCE, url(day, name)).path
    command = ["pdftotext", *(["-layout"] if layout else []), str(path), "-"]
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout


KINDS = (
    ("diplomat", "diplomatic"),
    ("posta", "postal"),
    ("postë", "postal"),
    ("kusht", "conditional"),
    ("nevoja", "special"),
    ("rregull", "regular"),
)


def station_kind(category: str) -> str:
    """The kind of a polling station from the category the Commission prints for it."""
    folded = category.casefold()
    for word, kind in KINDS:
        if word in folded:
            return kind
    raise ValueError(f"unexpected polling station category {category}")


def municipality_of(code: str, kind: str) -> str | None:
    """The number of the municipality a station belongs to, which every code but a diplomatic mission's starts with."""
    if kind == "diplomatic":
        return None
    match = re.search(r"\d{2}", code)
    if match is None:
        raise ValueError(f"{code} names no municipality")
    return str(int(match[0]))


@dataclass
class Station:
    code: str
    kind: str
    municipality: str | None
    center: str | None = None
    votes: dict[str, int] = field(default_factory=dict)  # ballot name -> votes
    figures: dict[int, int] = field(default_factory=dict)  # measure -> count
    flags: int = 0


@dataclass
class Count:
    day: str
    stations: dict[str, Station] = field(default_factory=dict)
    printed: dict[str, int] = field(default_factory=dict)  # ballot name -> national votes as printed
    numbers: dict[str, str] = field(default_factory=dict)  # ballot name -> ballot number
    seats: dict[str, int] = field(default_factory=dict)
    figures: dict[int, int] = field(default_factory=dict)  # national accounting as printed


def party_columns(header: list[Any], first: int) -> dict[int, str]:
    return {i: cell_text(h) for i, h in enumerate(header) if i >= first and cell_text(h)}


def detailed(day: str, name: str, count: Count, category_column: int) -> None:
    """Votes of each entity at each polling station from a detailed results workbook, which has one row for each
    station and one column for each entity."""
    (rows,) = sheets(day, name)
    header = rows[0]
    first = next(i for i, h in enumerate(header) if re.match(r"(?i)vendvotimi\b", cell_text(h))) + 1
    code_column = first - 1
    center_column = first - 2
    columns = party_columns(header, first)
    for i, text in list(columns.items()):
        match = re.fullmatch(r"(\d{3})\s+(.+)", text)
        if match:
            columns[i] = match[2]
            count.numbers[match[2]] = match[1]
    for row in rows[1:]:
        code = cell_text(row[code_column])
        if not code:
            continue
        kind = station_kind(cell_text(row[category_column]))
        station = count.stations.setdefault(
            code, Station(code, kind, municipality_of(code, kind), cell_text(row[center_column]) or None)
        )
        if station.votes:
            raise ValueError(f"{day}: station {code} appears twice")
        for i, party in columns.items():
            n = number(row[i])
            if n is None:
                raise ValueError(f"{day}: {code} has no votes for {party}")
            station.votes[party] = n


def stats(day: str, name: str, count: Count, measures: dict[int, str]) -> None:
    """Ballot accounting of each station from a statistics workbook; `measures` names the column of each measure by
    the number its header starts with."""
    (rows,) = sheets(day, name)
    header = [cell_text(h) for h in rows[0]]
    code_column = next(
        i
        for i, h in enumerate(header)
        if re.match(r"(?:\d+\.\s*)?(?:Kodi i vendvotimit|Numri i vendvotimit|Vendvotimi)", h)
    )
    columns = {
        measure: next(i for i, h in enumerate(header) if h.startswith(f"{prefix}. "))
        for measure, prefix in measures.items()
    }
    for row in rows[1:]:
        code = cell_text(row[code_column])
        if not code:
            continue
        station = count.stations[code]
        for measure, column in columns.items():
            n = number(row[column])
            if n is not None:
                station.figures[measure] = n


def totals(
    day: str, name: str, count: Count, number_column: int = 0, name_column: int = 1, votes_column: int = 2
) -> None:
    """The national votes of each entity as printed in the Commission's table of overall results."""
    (rows,) = sheets(day, name)[:1]
    for row in rows:
        ballot, party, votes = cell_text(row[number_column]), cell_text(row[name_column]), number(row[votes_column])
        if re.fullmatch(r"\d+", ballot) and votes is not None:
            count.printed[party] = votes
            count.numbers[party] = ballot


def feb_2025() -> Count:
    day = "2025-02-09"
    count = Count(day)
    detailed(
        day, "4.Rezultatet-e-detajuara-per-subjekte-politike-kategori-komune-qender-votimi-vendvotim.xls", count, 0
    )
    regular = {VOTERS: "5", BALLOTS: "8", INVALID: "10", VALID: "11"}
    postal = {BALLOTS: "5", INVALID: "7", VALID: "8"}
    stats(day, "2.Statistikat-e-detajuara-nga-votimi-ne-vendvotimet-e-rregullta-1.xls", count, regular)
    stats(day, "3.Statistikat-e-detajuara-nga-votimi-ne-perfaqesi-diplomatike.xls", count, regular)
    stats(day, "4.Statistikat-e-detajuara-nga-votimi-me-poste.xls", count, postal)
    stats(day, "5.Statistikat-e-detajuara-nga-votimi-me-kusht.xls", count, postal)
    stats(day, "6.Statistikat-e-detajuara-nga-votimit-i-personave-me-nevoja-te-vecanta.xls", count, regular)
    totals(day, "1-Rezultatet-e-pergjithshme-sipas-subjekteve-politike-f.xls", count)
    (rows,) = sheets(day, "2.Ndarja-e-uleseve-ne-Kuvend-dhe-kandidatet-e-zgjedhur-2.xls")[:1]
    by_number = {n: party for party, n in count.numbers.items()}
    for row in rows:
        if re.fullmatch(r"\d+", cell_text(row[0])) and number(row[2]) is not None:
            count.seats[by_number[cell_text(row[0])]] = number(row[2]) or 0
    return count


def sums(count: Count) -> Counter[str]:
    total: Counter[str] = Counter()
    for station in count.stations.values():
        total.update(station.votes)
    return total


def jun_2026() -> Count:
    day = "2026-06-07"
    count = Count(day)
    detailed(
        day, "5.-Rezultatet-e-detajuara-per-subjekte-kategori-komune-qender-votimi-vendvotim-7June2026-2.xlsx", count, 2
    )
    totals(day, "1.-Rezultatet-e-pergjithshme-sipas-subjekteve-politike-7Jun2026-2.xlsx", count)
    by_number = {n: party for party, n in count.numbers.items()}
    (rows,) = sheets(day, "2.-Ndarja-e-uleseve-ne-Kuvend-dhe-kandidatet-e-zgjedhur-7Jun2026.xlsx")
    for row in rows[1:]:
        if re.fullmatch(r"\d+", cell_text(row[0])):
            party = by_number[cell_text(row[0])]
            count.seats[party] = count.seats.get(party, 0) + 1
    names = {kqz.fold(party): party for party in count.printed}
    count.seats = {names[kqz.fold(party)]: seats for party, seats in count.seats.items()}
    return count


def dec_2025() -> Count:
    day = "2025-12-28"
    count = Count(day)
    detailed(
        day, "5.-Rezultatet-e-detajuara-per-subjekte-politike-kategori-komune-qender-votimi-vendvotim.xlsx", count, 2
    )
    got = sums(count)
    text = pdf_text(day, "Perqindja_e_subjekteve_parlamentare-1-f-1.pdf")
    by_votes: dict[int, list[str]] = defaultdict(list)
    for party, votes in got.items():
        by_votes[votes].append(party)
    for line in text.splitlines():
        match = re.match(r"\s*(\d{3})\s+(.+?)\s{2,}([\d,]+)\s+[\d.]+%", line)
        if not match:
            continue
        votes = int(match[3].replace(",", ""))
        (party,) = by_votes[votes]
        count.printed[party] = votes
        count.numbers[party] = match[1]
    for line in pdf_text(day, "Kandidatet_e_zgjedhur_parlamentare-14-f.pdf").splitlines():
        match = re.match(r"\s*(\d{3})\s+.+?\s{2,}(\d+)\s+([\d,]+)\s+[\d,]+%", line)
        if match:
            (party,) = by_votes[int(match[3].replace(",", ""))]
            count.seats[party] = int(match[2])
    return count


CODE = re.compile(r"[A-Z0-9]{2,5}/\d{2,3}[A-Z]?")


def listed(day: str, name: str, count: Count) -> None:
    """Votes of each entity at each station from a results workbook that prints a block of rows for each station: a
    header row that names the station, then one row for each entity."""
    station: Station | None = None
    for rows in sheets(day, name):
        for row in rows:
            cells = [cell_text(cell) for cell in row]
            code = next((c for c in cells[:5] if CODE.fullmatch(c)), None)
            if code is not None and not re.match(r"\d+\.", cells[0]):
                place = re.match(r"(\d+)\.", cells[2])
                station = count.stations.setdefault(
                    code, Station(code, prefix_kind(code), municipal(place[1]) if place else None)
                )
                continue
            match = re.match(r"(\d+)\.\s+(.+)", cells[0])
            if match and station is not None and number(row[1]) is not None:
                party = match[2]
                count.numbers[party] = match[1]
                station.votes[party] = number(row[1]) or 0


def jun_listed_totals(day: str, name: str, count: Count) -> None:
    (rows,) = sheets(day, name)[:1]
    for row in rows:
        match = re.match(r"(\d+)\.\s+(.+)", cell_text(row[0]))
        if match and number(row[1]) is not None:
            count.printed[match[2]] = number(row[1]) or 0
            count.numbers[match[2]] = match[1]


def prefix_kind(code: str) -> str:
    """The kind of a station from its code: BM prefixes postal ballots and CB prefixes conditional ballots."""
    return {"BM": "postal", "CB": "conditional"}.get(code[:2], "regular")


def municipal(n: str) -> str | None:
    return str(int(n)) if 1 <= int(n) <= 38 else None


def figures_2021(count: Count, day: str, name: str) -> None:
    (rows,) = sheets(day, name)
    for row in rows[1:]:
        code = cell_text(row[4])
        if code not in count.stations:
            continue
        for measure, column in ((REGISTERED, 8), (VOTERS, 10), (VALID, 15), (INVALID, 17), (BLANK, 19)):
            n = number(row[column])
            if n is not None and not (measure == REGISTERED and count.stations[code].kind != "regular"):
                count.stations[code].figures[measure] = n


def feb_2021() -> Count:
    day = "2021-02-14"
    count = Count(day)
    listed(day, "10.Rezultatet-e-subjekteve-sipas-vendvotimeve.xls", count)
    jun_listed_totals(day, "2.Rezultatet-sipas-subjekteve-politike.xls", count)
    figures_2021(count, day, "9.Statistikat-sipas-vendvotimeve.xls")
    (rows,) = sheets(day, "3.Ndarja-e-uleseve-ne-Kuvend-Subjektet-dhe-kandidatet-e-zgjedhur.xls")[:1]
    by_number = {n: party for party, n in count.numbers.items()}
    for row in rows:
        match = re.match(r"(\d+)\.\s", cell_text(row[0]))
        if match and number(row[3]) is not None and cell_text(row[1]) != "#":
            count.seats[by_number[match[1]]] = number(row[3]) or 0
    return count


def long_results(day: str, name: str, count: Count) -> None:
    """Votes from a workbook that prints one row for each entity at each station: the municipality's number and name,
    the station, the entity's number on the ballot, its name, and its votes."""
    for rows in sheets(day, name):
        for row in rows:
            cells = [cell_text(cell) for cell in row]
            if len(cells) < 6 or not re.fullmatch(r"\d+", cells[0]) or not re.fullmatch(r"\d+", cells[3]):
                continue
            code, party, votes = cells[2], cells[4], number(row[5])
            if votes is None:
                raise ValueError(f"{day}: {code} has no votes for {party}")
            station = count.stations.setdefault(code, Station(code, prefix_kind(code), municipal(cells[0])))
            count.numbers[party] = cells[3]
            station.votes[party] = votes


def filled(row: list[Any]) -> list[str]:
    return [cell_text(cell) for cell in row if cell_text(cell)]


def name_votes_totals(day: str, name: str, count: Count) -> None:
    """The national votes of each entity from a table with the entity's name and then its votes on each row."""
    (rows,) = sheets(day, name)[:1]
    for row in rows:
        cells = filled(row)
        if len(cells) >= 3 and re.fullmatch(r"\d+", cells[1]) and re.fullmatch(r"\d+\.\d+", cells[2]):
            count.printed[cells[0]] = int(cells[1])


def feb_ordered_seats(day: str, name: str, count: Count) -> None:
    """Seats from a table that prints the votes and the seats of an entity on one row and its name on the next."""
    (rows,) = sheets(day, name)[:1]
    pending: tuple[int, int] | None = None
    for row in rows:
        cells = filled(row)
        if len(cells) == 2 and all(re.fullmatch(r"\d+", c) for c in cells):
            pending = (int(cells[0]), int(cells[1]))
        elif pending is not None and len(cells) == 2 and re.fullmatch(r"\d+\.\d+", cells[1]):
            count.seats[cells[0]] = pending[1]
            pending = None


LABELS = (
    ("TË REGJISTRUAR", REGISTERED),
    ("KANË VOTUAR NË SECILIN", VOTERS),
    ("FLETËVOTIMEVE TË VLEFSHME", VALID),
    ("FLETËVOTIMEVE TË PAVLEFSHME", INVALID),
    ("FLETËVOTIMEVE TË ZBRAZETA", BLANK),
)


def report_figures(day: str, name: str, count: Count, column: int = 15) -> None:
    """Ballot accounting from a report-style workbook that prints each figure on its own row under its label."""
    station: Station | None = None
    pending: int | None = None
    for rows in sheets(day, name):
        for row in rows:
            cells = [cell_text(cell) for cell in row]
            code = next((c for c in cells[:5] if CODE.fullmatch(c)), None)
            if code is not None:
                station = count.stations.get(code)
                pending = None
                continue
            label = next((m for text, m in LABELS if any(text in c.upper() for c in cells[:5])), None)
            if label is not None:
                pending = label
            n = number(row[column]) if len(row) > column and pending is not None else None
            if n is not None and station is not None and pending is not None:
                if not (pending == REGISTERED and station.kind != "regular"):
                    station.figures[pending] = n
                pending = None


def dotted(text: str) -> int | None:
    """A count that the workbook prints with a dot as the thousands separator, and without the zeros that end it."""
    if not re.fullmatch(r"\d+(\.\d{1,3})?", text):
        return None
    whole, _, rest = text.partition(".")
    return int(whole + rest.ljust(3, "0")) if rest else int(whole)


def numbered(text: str) -> tuple[str, str]:
    match = re.fullmatch(r"(\d+)\s+(.+)", text)
    if match is None:
        raise ValueError(f"{text!r} does not start with a ballot number")
    return match[1], match[2]


def jun_2017() -> Count:
    day = "2017-06-11"
    count = Count(day)
    for row in sheets(day, "7.-Rezultatet-e-subjekteve-sipas-vendvotimeve-1.xlsx")[0][1:]:
        cells = [cell_text(cell) for cell in row]
        if not re.fullmatch(r"\d+", cells[0]):
            continue
        code = cells[2]
        station = count.stations.setdefault(code, Station(code, "regular", str(int(cells[0]))))
        ballot, party = numbered(cells[4])
        count.numbers[party] = ballot
        station.votes[party] = number(row[5]) or 0
    for row in sheets(day, "1.-Rezultatet-e-përgjithshme-sipas-Subjekteve.xlsx")[0][1:]:
        cells = [cell_text(cell) for cell in row]
        if cells[0] and dotted(cells[1]) is not None:
            count.printed[numbered(cells[0])[1]] = dotted(cells[1]) or 0
    for row in sheets(day, "3.Statistikat-sipas-Vendvotimeve.xlsx")[0][1:]:
        code = cell_text(row[1])
        if code not in count.stations:
            continue
        for measure, column in ((VOTERS, 2), (VALID, 5), (BLANK, 6), (INVALID, 7), (BALLOTS, 8), (REGISTERED, 9)):
            n = number(row[column])
            if n is not None:
                count.stations[code].figures[measure] = n
    party = ""
    for row in sheets(day, "2.-Kandidatët-e-zgjedhur-sipas-Subjekteve.xlsx")[0]:
        cells = filled(row)
        if len(cells) == 1:
            party = numbered(cells[0])[1]
        elif cells and re.fullmatch(r"\d+", cells[0]) and party:
            count.seats[party] = count.seats.get(party, 0) + 1
    names = {kqz.fold(party): party for party in count.printed}
    count.seats = {names[kqz.fold(party)]: seats for party, seats in count.seats.items()}
    return count


HEADER = re.compile(r"^\s{20,}(\d+) (.+?)\s*$")
STATION_LINE = re.compile(r"^\s{20,}([A-Z]{0,2}\d{3,4}[A-Z]?/\d{2,3}[A-Z]?)\s*$")
VOTE_LINE = re.compile(r"^\s*(\d{1,3}) (.+?)\s{2,}(\d+)\s+([\d,]+)%\s*$")
TOTAL_LINE = re.compile(r"^\s*(\d{1,3}) (.+?)\s{2,}([\d.]+)\s+([\d,]+)%\s*$")


def pdf_names(day: str, name: str) -> dict[str, tuple[str, int]]:
    """Ballot number -> (name, votes) from a PDF table of national results, where a long name wraps to a second line."""
    lines = pdf_text(day, name).splitlines()
    found: dict[str, tuple[str, int]] = {}
    for i, line in enumerate(lines):
        match = TOTAL_LINE.match(line)
        if match is None:
            continue
        label = match[2]
        follow = lines[i + 1] if i + 1 < len(lines) else ""
        if follow.strip() and not TOTAL_LINE.match(follow) and "ZGJEDHJET" not in follow and "Page" not in follow:
            label += " " + follow.strip()
        found[match[1]] = (kqz.clean(label), dotted(match[3]) or 0)
    return found


def pdf_stations(day: str, name: str, names: dict[str, tuple[str, int]], count: Count) -> None:
    """Votes of each entity at each station from a PDF that prints a page for each: the municipality and the station,
    then each entity with its votes."""
    code: str | None = None
    municipality: str | None = None
    seen: set[tuple[str, str]] = set()
    for line in pdf_text(day, name).splitlines():
        head = HEADER.match(line)
        if head is not None and not STATION_LINE.match(line):
            municipality = municipality_id(head[2])
            continue
        found = STATION_LINE.match(line)
        if found is not None:
            code = found[1]
            if code not in count.stations:
                count.stations[code] = Station(
                    code, prefix_kind(code), municipal(code[3:5]) if code[:2] == "CB" else municipality
                )
            continue
        vote = VOTE_LINE.match(line)
        if vote is not None and code is not None:
            party = names[vote[1]][0]
            if (code, party) in seen:
                continue
            seen.add((code, party))
            count.stations[code].votes[party] = int(vote[3])
            count.numbers[party] = vote[1]


def abbreviation_totals(day: str, name: str, count: Count) -> None:
    """National votes and seats from a table that prints an entity's abbreviation, votes, percentage, and seats."""
    for line in pdf_text(day, name).splitlines():
        match = re.match(r"^(\S.*?)\s{2,}([\d,]+)\s+[\d.]+ %\s+(\d+)\s*$", line)
        if match:
            count.printed[match[1]] = int(match[2].replace(",", ""))
            count.seats[match[1]] = int(match[3])


def jun_2010_stations(day: str, name: str, count: Count) -> None:
    """Votes at each station from a PDF with a page for each: the municipality number and name, the station, then each
    entity's ballot number, abbreviation, and votes."""
    code: str | None = None
    municipality: str | None = None
    for line in pdf_text(day, name).splitlines():
        head = re.match(r"^\s+(\d{1,2}) ([A-Za-zÀ-ž].+?/.+?)\s*$", line)
        if head is not None:
            municipality = municipality_id(head[2])
            continue
        found = re.match(r"^([A-Z]{0,2}\d{3,4}[A-Z]?/\d{2,3}[A-Z]?)\s*$", line)
        if found is not None:
            code = found[1]
            if code not in count.stations:
                count.stations[code] = Station(code, "regular", municipality)
            continue
        vote = re.match(r"^\s+(\d{1,2})\s+(\S.*?)\s{2,}(\d+)\s*$", line)
        if vote is not None and code is not None:
            count.stations[code].votes[vote[2]] = int(vote[3])
            count.numbers[vote[2]] = vote[1]


def fold_name(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in plain if not unicodedata.combining(c)))


@functools.cache
def municipality_names() -> dict[str, set[str]]:
    """Each municipality's number with the folded forms of its Albanian and Serbian names."""
    found: dict[str, set[str]] = {}
    for m, (sq, sr) in kqz.municipalities().items():
        found[m] = {fold_name(sq), fold_name(sr)}
    return found


def municipality_id(printed: str) -> str:
    """The number of a municipality from its name as the Commission printed it, in either language or both."""
    parts = [fold_name(part) for part in re.split(r"\s*/\s*", printed) if fold_name(part)]
    if any(part.startswith(("hanielezit", "deneraljankovic")) for part in parts):
        return "33"
    if any(part in ("mitrovice", "mitrovica") for part in parts) and not any(
        "sever" in part or "veri" in part for part in parts
    ):
        return "11"
    for part in parts:
        hits = [m for m, names in municipality_names().items() if part in names]
        if len(hits) == 1:
            return hits[0]
    for part in parts:
        hits = [m for m, names in municipality_names().items() if any(n[:5] == part[:5] for n in names)]
        if len(hits) == 1:
            return hits[0]
    raise ValueError(f"unknown municipality {printed!r}")


def municipal_pdf(day: str, name: str) -> dict[str, dict[str, int]]:
    """Votes of each entity in each municipality from a PDF that prints a page for each municipality."""
    found: dict[str, dict[str, int]] = {}
    current: dict[str, int] | None = None
    for line in pdf_text(day, name).splitlines():
        head = re.match(r"^\s{1,6}(\S[^\d]*?/[^\d]*?)\s*$", line)
        if head is not None and "NAZIV" not in line and "ZGJEDHJET" not in line:
            current = found.setdefault(municipality_id(head[1]), {})
            continue
        vote = re.match(r"^\s*(\d{1,2})\s+(\S.*?)\s{2,}([\d,]+)\s*$", line)
        if vote is not None and current is not None:
            current[vote[2]] = int(vote[3].replace(",", ""))
    return found


def oct_2019() -> Count:
    day = "2019-10-06"
    count = Count(day)
    long_results(day, "10.Rezultatet-e-subjekteve-sipas-vendvotimeve.xlsx", count)
    name_votes_totals(day, "2.Rezultatet-sipas-subjekteve-politike (1).xls", count)
    feb_ordered_seats(day, "3.Ndarja-e-ulëseve-në-Kuvend-Subjektet-dhe-kandidatët-e-zgjedhur.xls", count)
    report_figures(day, "9.Statistikat-sipas-vendvotimeve (1).xls", count)
    return count


def jun_2014() -> Count:
    day = "2014-06-08"
    count = Count(day)
    names = pdf_names(day, "1.-Rezultatet-e-përgjithshme-sipas-Subjekteve-2014.pdf")
    count.printed = {name: votes for name, votes in names.values()}
    pdf_stations(day, "7.-Rezultatet-e-subjekteve-sipas-vendvotimeve-2014.pdf", names, count)
    postal = Station("VP", "postal", None, None, flags=FLAG_ABOVE)
    for ballot, (name, votes) in pdf_names(day, "11.-Rezultatet-e-votave-me-postë-sipas-subjekteve-2014.pdf").items():
        if names[ballot][0] != name:
            raise ValueError(f"{day}: postal table names {ballot} differently")
        postal.votes[name] = votes
    for name in count.printed:
        postal.votes.setdefault(name, 0)
    count.stations[postal.code] = postal
    for line in pdf_text(day, "2.-Kandidatët-e-zgjedhur-sipas-Subjekteve-2014.pdf").splitlines():
        match = re.match(r"^\s*(\d{2}) .+?\s{2,}([\d,]+)\s+[\d.]+%\s+(\d+)\s*$", line)
        if match:
            count.seats[names[match[1]][0]] = int(match[3])
    return count


def dec_2010() -> Count:
    day = "2010-12-12"
    count = Count(day)
    abbreviation_totals(day, "1.-Rezultatet-e-përgjithshme-sipas-Subjekteve-2010.pdf", count)
    jun_2010_stations(day, "7.-Rezultatet-e-subjekteve-sipas-vendvotimeve-2010.pdf", count)
    inside: dict[str, Counter[str]] = defaultdict(Counter)
    for station in count.stations.values():
        inside[station.municipality or ""].update(station.votes)
    for m, printed in municipal_pdf(day, "6.-Rezultatet-e-subjekteve-sipas-komunave-2010.pdf").items():
        left = {party: votes - inside[m].get(party, 0) for party, votes in printed.items()}
        if any(votes < 0 for votes in left.values()):
            continue
        station = Station(f"OUT{int(m):02d}", "aggregate", m, None, left, flags=FLAG_DERIVED | FLAG_ABOVE)
        count.stations[station.code] = station
    return count


# Parties.

INDEPENDENTS = {"esmirkasi", "hasangashi", "nedzmidinsejdilar", "fatmirbytyqi"}
MERGED = {
    "pdk": ("pdk", "partiademokratikeekosovespdk", "partiademokratikeekosoves"),
    "ldk": ("ldk", "lidhjademokratikeekosovesldk", "lidhjademokratikeekosoves"),
    "vv": ("vetevendosje", "levizjavetevendosje"),
    "aak": (
        "aak",
        "aleancaperardhmerineekosovesaak",
        "aleancaperardhmerineekosoves",
        "aakaleancaperardhmerineekosoves",
    ),
    "nisma": ("nismaperkosoven", "nismasocialdemokratenisma"),
    "srpska_lista": ("srpskalista", "gradanskainicijativasrpskalista"),
    "samostalna_liberalna_stranka": ("sls", "samostalnaliberalnastranka"),
    "vakat": ("vakat", "koalicijavakat"),
    "kdtp": ("kdtp", "kosovademokratikturkpartisi", "kosovademokratikturkpartisikdtp"),
    "irdk": ("irdk", "iniciativaeredemokratikeekosoves", "iniciativaeredemokratikeekosovesirdk"),
    "nds": ("nds", "novademokratskastranka", "novademokratskastrankands"),
    "ple": ("partialiberaleegjiptiane", "partialiberaleegjiptianeple"),
    "pai": ("pai", "partiaeashkalinjeveperintegrim", "partiaeashkalinjeveperintegrimpai"),
    "pdak": ("pdak", "partiademokratikeeashkanlivetekosoves", "partiademokratikeeashkanlivetekosovespdak"),
    "prebk": ("prebk", "partiaromeebashkuarekosoves", "partiaromeebashkuarekosovesprebk"),
    "lprk": ("levizjaperparimtareeromevetekosoves", "levizjaperparimtareeromevetekosoveslprk"),
    "ythp": ("yenilikciturkhareketpartisi", "yenilikciturkhareketpartisiythp"),
    "gi_spo": ("zaslobodupravduiopstanak", "gizaslobodupravduiopstanakgispo"),
    "knrp": ("knrp", "kosovakineviromanipartia", "kosovakineviromanipartiaknrp"),
    "jgp": ("jedinstvenagoranskapartija", "jedinstvenagoranskapartijajgp"),
    "sdu": ("socijaldemokratskaunijasdu", "socialdemokratskaunijasdu"),
    "bsdak": ("bsdak", "bosnjackastrankademokratskeakcijekosova"),
    "sda": ("sda", "strankademokratskeakcije"),
    "ktap": ("kosovaturkadaletpartisi", "kosovaadaletturkpartisi"),
    "pokret_za_gora": ("pokretzagora",),
}
BLOCS = (
    ("pdk_pd_lb_pshdk_pk", "partiademokratikeekosovespartiaedrejtesise"),
    ("aak_nisma_pd_lb_pshdk_pk", "partiademokratikeekosovesaleanca"),
    ("ldk_akr", "lidhjademokratikeekosovesaleancakosovaere"),
    ("aak_psd", "aakpsdkoalicioni"),
    ("nisma_akr_pd", "nismasocialdemokrateaaleancakosovaere"),
    ("aak_nisma", "aakaleancaperardhmerineekosovesnisma"),
)


def party_key(name: str) -> tuple[str | None, str]:
    """The key of the party or alliance behind a ballot name, or None for a candidate who stands alone, with its kind.
    Names the Commission prints differently from one election to the next are merged by hand."""
    folded = fold_name(name.replace("ð", "d").replace("Ð", "D"))
    if folded in INDEPENDENTS:
        return None, "independent"
    for key, start in BLOCS:
        if folded.startswith(start) or (
            key == "nisma_akr_pd" and folded.startswith("nismasocialdemokrate") and "aleancakosova" in folded
        ):
            return key, "bloc"
    for key, forms in MERGED.items():
        if folded in forms:
            return key, "party"
    return folded, "bloc" if "koalic" in folded or "pdaklpb" in folded else "party"


# The packages.

ASSEMBLY_SOURCE = kqz.key("source", "xk.kqz.assembly")
GEO_SOURCE = kqz.key("source", "geoboundaries.xkx.adm2")
GEOBOUNDARIES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/XKX/ADM2/"
    "geoBoundaries-XKX-ADM2.geojson"
)
ASSEMBLY = kqz.key("body", "assembly")
SYSTEM_UNMIK = kqz.key("electoral_system", "assembly", "unmik")
SYSTEM_LAW = kqz.key("electoral_system", "assembly", "law-2008")
DAYS = (
    "2010-12-12",
    "2014-06-08",
    "2017-06-11",
    "2019-10-06",
    "2021-02-14",
    "2025-02-09",
    "2025-12-28",
    "2026-06-07",
)
LOADERS = {
    "2010-12-12": dec_2010,
    "2014-06-08": jun_2014,
    "2017-06-11": jun_2017,
    "2019-10-06": oct_2019,
    "2021-02-14": feb_2021,
    "2025-02-09": feb_2025,
    "2025-12-28": dec_2025,
    "2026-06-07": jun_2026,
}
SHAPE_NAMES = {
    "Municipality of Partesh": "36",
    "Municipality of Dragash": "5",
    "Municipality of Rahovec": "16",
    "Municipality of Shtërpcë": "23",
    "Municipality of Kamenica": "10",
    "Municipality of Fushë Kosovë": "9",
    "Municipality of Novobërdë": "14",
    "Municipality of Istog": "6",
    "Municipality of Prizren": "20",
    "Municipality of Pristina": "19",
    "Municipality of Han i Elezit": "33",
    "Municipality of North Mitrovica": "38",
    "Municipality of Kllokot": "37",
    "Municipality of Ferizaj": "25",
    "Municipality of Drenas": "3",
    "Municipality of Deçan": "1",
    "Municipality of Mitrovica": "11",
    "Municipality of Podujeva": "18",
    "Municipality of Suhareka": "24",
    "Municipality of Kaçanik": "7",
    "Municipality of Ranillug": "35",
    "Municipality of Malisheva": "30",
    "Municipality of Peja": "17",
    "Municipality of Zubin Potok": "28",
    "Municipality of Junik": "31",
    "Municipality of Viti": "26",
    "Municipality of Gjakova": "2",
    "Municipality of Zveçan": "29",
    "Municipality of Klina": "8",
    "Municipality of Gjilan": "4",
    "Municipality of Leposaviq": "12",
    "Municipality of Gracanica": "34",
    "Municipality of Mamusha": "32",
    "Municipality of Vushtrri": "27",
    "Municipality of Lipjan": "13",
    "Municipality of Skenderaj": "21",
    "Municipality of Obiliq": "15",
    "Municipality of Shtime": "22",
}


def party(name: str) -> str | None:
    key, _ = party_key(name)
    return None if key is None else kqz.key("party", "assembly", key)


@functools.cache
def counts() -> dict[str, Count]:
    return {day: loader() for day, loader in LOADERS.items()}


def fetch_all() -> None:
    raw.fetch(kqz.SOURCE, GEOBOUNDARIES, suffix=".geojson")
    fetch()


def extend_geo(p: Package) -> None:
    """Add the outlines of the 38 municipalities."""
    artifact = raw.cached(kqz.SOURCE, GEOBOUNDARIES)
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab), from OpenStreetMap",
        title="geoBoundaries gbOpen: Kosovo ADM2 (municipalities)",
        url="https://www.geoboundaries.org/",
        license="Creative Commons Attribution-ShareAlike 2.0 (OpenStreetMap contributors, through geoBoundaries)",
        license_url="https://www.openstreetmap.org/copyright",
        attribution="geoBoundaries; © OpenStreetMap contributors",
        notes="Boundary year 2017, built December 2023. The 38 municipalities of Law 03/L-041.",
    )
    p.artifact(artifact, GEO_SOURCE)
    p.add(
        "boundary_set",
        id=kqz.key("boundary_set", "xk.komuna", 2017),
        scheme_code="xk.komuna",
        vintage="2017",
        valid_from=date(2008, 6, 2),
        as_of=date(2017, 1, 1),
    )
    found = json.loads(artifact.path.read_bytes())["features"]
    placed = {SHAPE_NAMES[f["properties"]["shapeName"]]: shapely.geometry.shape(f["geometry"]) for f in found}
    if sorted(placed, key=int) != [str(n) for n in range(1, 39)]:
        raise ValueError("the outlines do not cover the 38 municipalities")
    for m, shape in sorted(placed.items(), key=lambda item: int(item[0])):
        shape = shapely.set_srid(shapely.make_valid(shape), 4326)
        if shapely.get_type_id(shape) == 3:
            shape = shapely.MultiPolygon([shape])
        p.add(
            "area_geometry",
            area_id=kqz.municipality(m),
            valid_from=date(2008, 6, 2),
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=GEO_SOURCE,
        )


RULES_UNMIK = {"formula": "sainte_lague", "lists": {"order": "closed", "vacancy": "next_on_list"}}
RULES_LAW = {
    "formula": "sainte_lague",
    "threshold": {"level": "list", "scope": "national", "percent": 5},
    "lists": {"order": "open", "vacancy": "next_by_votes"},
}
NOTE_UNMIK = (
    "Under the UNMIK Constitutional Framework the Assembly had 120 members elected in one territory-wide district: 100 "
    "by proportional representation among all lists and 20 seats reserved for non-majority communities, 10 for Serbs "
    "and 10 for the others. Lists were closed in 2001 and 2004 and open in 2007."
)
NOTE_LAW = (
    "Under the Constitution and the Law on General Elections the Assembly has 120 members elected in one nationwide "
    "district. Seats are divided among the lists by the Sainte-Laguë method; a list needs 5% of the valid votes for "
    "the 100 open seats, and the 20 seats reserved for non-majority communities (10 for Serbs and 10 for the Roma, "
    "Ashkali, Egyptian, Bosniak, Turkish, and Gorani communities) go to those communities' lists whatever their "
    "share. A seat goes to the list's candidates in the order of their preference votes, subject to the gender quota."
)


def extend_registry(p: Package) -> None:
    p.add(
        "source",
        id=ASSEMBLY_SOURCE,
        kind="official_body",
        publisher="Komisioni Qendror i Zgjedhjeve",
        title="Results of the Assembly elections, KQZ results archive",
        url="https://kqz-ks.org/rezultatet/zgjedhjet-per-kuvend-te-kosoves/",
        license="Official election results of the Republic of Kosovo, published for public use",
        license_url="https://kqz-ks.org/rezultatet/",
        attribution="Central Election Commission of the Republic of Kosovo (KQZ), kqz-ks.org",
    )
    p.add("body", id=ASSEMBLY, polity_id=kqz.KOSOVO, kind="unicameral")
    p.name(ASSEMBLY, "Assembly of Kosovo", "en", "official")
    p.name(ASSEMBLY, "Kuvendi i Kosovës", "sq", "native", False)
    p.name(ASSEMBLY, "Skupština Kosova", "sr", "native", False)
    for id, since, until, rules, note in (
        (SYSTEM_UNMIK, date(2001, 5, 15), date(2008, 6, 15), RULES_UNMIK, NOTE_UNMIK),
        (SYSTEM_LAW, date(2008, 6, 15), None, RULES_LAW, NOTE_LAW),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=kqz.KOSOVO,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=kqz.key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    names: dict[str, dict[str, str]] = defaultdict(dict)  # key -> day -> name
    kinds: dict[str, str] = {}
    for day, count in counts().items():
        for name in count.printed:
            key, kind = party_key(name)
            if key is not None:
                names[key][day] = name
                kinds[key] = kind
    for key, by_day in sorted(names.items()):
        id = kqz.key("party", "assembly", key)
        p.add("party", id=id, kind=kinds[key], polity_id=kqz.KOSOVO)
        latest = by_day[max(by_day)]
        p.name(id, latest, "sq", "official")
        for name in sorted(set(by_day.values()) - {latest}):
            p.name(id, name, "sq", "ballot", False)


UNIT_KIND = {
    "regular": "standard",
    "conditional": "standard",
    "special": "mobile",
    "postal": "postal_district",
    "diplomatic": "abroad_mission",
    "aggregate": "aggregate",
}
KIND_NOTE = {
    "regular": "a polling station",
    "conditional": "a conditional-ballot station",
    "special": "a station for voters with special needs",
    "postal": "a postal-ballot station",
    "diplomatic": "a station at a diplomatic mission",
}
MEASURES = {REGISTERED, BALLOTS, VALID, INVALID, BLANK, VOTERS}
METHOD = (
    "Votes of each political entity at each polling station, from the results the Commission publishes in its results "
    "archive on kqz-ks.org. The national totals, the seats of each entity, and the station files come from "
    "separate files, and the station votes add up to the national total of every entity. Entities stand on a single "
    "nationwide list; the elected members and the preference votes of candidates are not stored here."
)


def station_unit(day: str, code: str) -> str:
    return kqz.unit_id(date.fromisoformat(day), code)


def english_name(day: str) -> str:
    when = date.fromisoformat(day)
    month = when.strftime("%B %Y") if when.year >= 2025 else str(when.year)
    return f"{month} Kosovan parliamentary election"


def caveat(p: Package, result_set: str, code: str, reason: str, text: str) -> None:
    p.accept(code, reason)
    p.add(
        "note",
        id=kqz.key("note", result_set, code),
        entity_id=result_set,
        kind="caveat",
        body_md=text,
        lang="en",
    )


def caveats(p: Package, count: Count, result_set: str) -> None:
    """Accept the checks that the Commission's own figures fail, and say so on the result set."""
    wrong = [
        s.code for s in count.stations.values() if VALID in s.figures and s.figures[VALID] != sum(s.votes.values())
    ]
    if wrong:
        caveat(
            p,
            result_set,
            "accounting.votes_valid",
            f"{len(wrong)} station{'s' if len(wrong) > 1 else ''} print{'' if len(wrong) > 1 else 's'} a number of "
            "valid ballots that differs from the sum of their entities' votes",
            f"At {len(wrong)} station{'s' if len(wrong) > 1 else ''} ({', '.join(wrong[:5])}) the printed valid "
            "ballots differ from the sum of the entities' votes. Both are stored as printed.",
        )
    unbalanced = []
    for s in count.stations.values():
        cast, valid = s.figures.get(BALLOTS), s.figures.get(VALID)
        invalid, blank = s.figures.get(INVALID), s.figures.get(BLANK)
        if cast is None or valid is None:
            continue
        if (invalid is None and blank is None and valid > cast) or (
            (invalid is not None or blank is not None) and valid + (invalid or 0) + (blank or 0) != cast
        ):
            unbalanced.append(s.code)
    if unbalanced:
        caveat(
            p,
            result_set,
            "accounting.ballots_sum",
            f"{len(unbalanced)} stations print a number of ballots in the box that differs from their valid, "
            "invalid, and blank ballots",
            f"At {len(unbalanced)} stations the printed ballots in the box differ from the valid, invalid, and "
            "blank ballots added together. The figures are stored as printed.",
        )
    over = [
        s.code
        for s in count.stations.values()
        if REGISTERED in s.figures and s.figures.get(BALLOTS, s.figures.get(VOTERS, 0)) > s.figures[REGISTERED]
    ]
    if over:
        caveat(
            p,
            result_set,
            "accounting.turnout",
            f"{len(over)} stations print more ballots than voters on their roll",
            f"At {len(over)} stations more ballots were counted than voters are on the roll. The figures are stored "
            "as printed.",
        )
    summed = sums(count)
    differ = [name for name, votes in count.printed.items() if summed[name] != votes]
    valid = sum(s.figures.get(VALID, 0) for s in count.stations.values())
    total = sum(count.printed.values())
    if differ:
        gap = sum(abs(count.printed[name] - summed[name]) for name in differ)
        caveat(
            p,
            result_set,
            "accounting.declared",
            "the national totals and the station figures differ in places, and each is stored as the Commission "
            "printed it",
            f"The national totals that the Commission prints differ from the sum of its station figures by {gap:,} "
            "votes in all, across "
            f"{len(differ)} entities. Each is stored as printed.",
        )
    elif valid and valid != total:
        caveat(
            p,
            result_set,
            "accounting.declared",
            "the printed valid ballots of the stations add up to fewer than the national valid votes",
            f"The valid ballots that the stations print add up to {valid:,}, and the national valid votes are "
            f"{total:,}. Each is stored as printed.",
        )


def election_package(day: str) -> Package:
    count = counts()[day]
    when = date.fromisoformat(day)
    p = Package(
        f"{kqz.SOURCE}/{day}",
        kqz.SOURCE,
        f"The {english_name(day)} by polling station from the Central Election Commission",
        depends=[f"{kqz.SOURCE}/geo", f"{kqz.SOURCE}/registry"],
    )
    p.external.add(kqz.KOSOVO)
    for name in sorted(USED[day]):
        p.artifact(raw.cached(kqz.SOURCE, url(day, name)), ASSEMBLY_SOURCE)
    election_id = kqz.key("election", "assembly", day)
    p.add("election", id=election_id, polity_id=kqz.KOSOVO, kind="general", date_from=when, date_to=when, status="held")
    title = english_name(day)
    p.name(election_id, title[0].upper() + title[1:], "en", "official")
    p.name(election_id, f"Zgjedhjet për Kuvendin e Kosovës {when.year}", "sq", "native", False)
    path = f"xk/{day}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = kqz.key("contest", "assembly", day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=ASSEMBLY,
        magnitude=120,
        round_number=1,
        electoral_system_id=SYSTEM_LAW,
        default_result_kind="counted",
    )
    p.name(contest, "Assembly of Kosovo", "en", "official")
    p.name(contest, "Kuvendi i Kosovës", "sq", "native", False)
    p.add("slug", path=f"{path}/assembly", entity_id=contest, is_current=True)
    result_set = kqz.key("result_set", "assembly", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=ASSEMBLY_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=kqz.key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options: dict[str, str] = {}
    for name in count.printed:
        option = options[name] = kqz.key("option", "assembly", day, fold_name(name))
        key, _ = party_key(name)
        ballot = count.numbers.get(name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate" if key is None else "list",
            party_id=party(name),
            ballot_position=int(ballot) if ballot else None,
            ballot_label=name,
        )
        seats = count.seats.get(name, 0)
        if seats:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=count.printed[name])
    if any(VALID in station.figures for station in count.stations.values()):
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=sum(count.printed.values()))
    caveats(p, count, result_set)
    facts = kqz.Facts()
    flagged: dict[int, list[tuple[str, str, str | None, int, int]]] = defaultdict(list)
    for station in sorted(count.stations.values(), key=lambda s: s.code):
        unit = station_unit(day, station.code)
        m = station.municipality
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=kqz.KOSOVO,
            scheme_code="xk.vendvotimi",
            code=f"{day}:{station.code}",
            area_relation="within" if m else "unlocated",
            area_id=kqz.municipality(m) if m else None,
            kind=UNIT_KIND[station.kind],
        )
        p.name(
            unit,
            station.code + (f" ({station.center})" if station.center and station.center != station.code else ""),
            "sq",
            "official",
        )
        rows = flagged[station.flags]
        for name, votes in station.votes.items():
            rows.append((result_set, unit, options[name], VOTES, votes))
        for measure, value in station.figures.items():
            rows.append((result_set, unit, None, measure, value))
    for flags, rows in sorted(flagged.items()):
        facts.rows = rows
        p.frame("result_value", facts.frame().with_columns(flags=pl.lit(flags, pl.Int16)))
    return p
