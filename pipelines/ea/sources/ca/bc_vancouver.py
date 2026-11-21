"""The City of Vancouver's municipal election results by voting division or voting place.

The city publishes each general election as open data: for 2002 to 2014 a sheet per file with a block for each race
(mayor, council, park board, school board, and the plebiscite questions) whose rows are candidates and whose columns
are voting divisions, and for 2018 and 2022 a file for each race whose rows are voting places and whose columns are
candidates. Both are read into the same shape: a race, its candidates, and each candidate's votes by unit.
"""

import csv
import io
import re
import zipfile
from datetime import date
from typing import NamedTuple

from ea import raw

SOURCE = "ca.bc_municipal.vancouver"
PAGE = "https://opendata.vancouver.ca/explore/dataset/municipal-election-results/"
WAYBACK = "https://web.archive.org/web/"
FILES = {
    1996: WAYBACK + "20260818030041id_/https://webtransfer.vancouver.ca/OpenData/csv/1996MunicipalElectionResults.csv",
    1999: WAYBACK + "20260818030038id_/https://webtransfer.vancouver.ca/OpenData/csv/1999MunicipalElectionResults.csv",
    2002: WAYBACK + "20260818030036id_/https://webtransfer.vancouver.ca/OpenData/csv/2002MunicipalElectionResults.csv",
    2005: WAYBACK + "20260818030032id_/https://webtransfer.vancouver.ca/OpenData/csv/2005MunicipalElectionResults.csv",
    2008: WAYBACK + "20260818030030id_/https://webtransfer.vancouver.ca/OpenData/csv/2008MunicipalElectionResults.csv",
    2011: WAYBACK + "20260818030029id_/https://webtransfer.vancouver.ca/OpenData/csv/2011_Election_Results.csv",
    2014: WAYBACK + "20260818030029id_/https://webtransfer.vancouver.ca/OpenData/csv/2014MunicipalElectionResults.csv",
    2018: WAYBACK + "20260818030023id_/https://webtransfer.vancouver.ca/OpenData/csv/2018MunicipalElectionResults.zip",
    2022: WAYBACK + "20260818030023id_/https://webtransfer.vancouver.ca/OpenData/csv/2022MunicipalElectionResults.zip",
    2026: WAYBACK + "20261103152121id_/https://webtransfer.vancouver.ca/OpenData/csv/2026MunicipalElectionResults.zip",
}
# The public safety plebiscite's results, published with the 2026 general election's.
PLEBISCITE = {2026: "https://webtransfer.vancouver.ca/OpenData/csv/2026PublicSafetyPlebisciteResults.zip"}
SUFFIX = {year: ".zip" if url.endswith(".zip") else ".csv" for year, url in FILES.items()}
OFFICES = {
    "mayor": "mayor",
    "councillor": "councillor",
    "councilor": "councillor",
    "school trustees": "trustee",
    "park commissioner": "commissioner",
    "park board": "commissioner",
    "school trustee": "trustee",
    "school board": "trustee",
}


class Candidate(NamedTuple):
    name: str
    party: str
    total: int | None
    votes: dict[str, int]


class Race(NamedTuple):
    office: str
    title: str
    seats: int
    candidates: list[Candidate]
    ballots: dict[str, int]
    registered: dict[str, int]


class Count(NamedTuple):
    races: list[Race]
    units: dict[str, str]


def fetch() -> None:
    for year, url in FILES.items():
        raw.fetch(SOURCE, url, suffix=SUFFIX[year])
    for url in PLEBISCITE.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def _number(text: str) -> int | None:
    text = text.replace(",", "").strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


def _code(text: str) -> str:
    """A voting division as the file's heading prints it, without the dash some years add to a bare number."""
    return re.sub(r"\s*-\s*$", "", text.strip())


def _cell(text: str) -> int | None:
    """A count in a grid of results, where a dash stands for none."""
    return 0 if text.strip() == "-" else _number(text)


def _text(raw_bytes: bytes) -> str:
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raw_bytes = raw_bytes[3:]
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes.decode("cp1252", errors="replace")


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def person(text: str) -> str:
    """A name the files print as SURNAME, Given names, in the order a ballot prints it."""
    text = " ".join(text.split())
    if "," not in text:
        return text
    surname, given = (part.strip() for part in text.split(",", 1))
    if surname.isupper():
        surname = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), surname.title())
        surname = re.sub(r"(?<=['-])([a-z])", lambda m: m.group(1).upper(), surname)
    return f"{given} {surname}".strip()


def _party(text: str) -> tuple[str, str]:
    """A candidate heading's name and the elector organization in its closing parentheses."""
    text = " ".join(text.split())
    found = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", text)
    return (found.group(1), found.group(2)) if found else (text, "")


DIVISION = re.compile(r"\d{1,3}[A-Z]?")


def _title(rows: list[list[str]], at: int) -> str:
    """The race a header row belongs to: the nearest line above it that names one in its first cell."""
    for back in range(at - 1, max(at - 14, -1), -1):
        first = rows[back][0].strip() if rows[back] else ""
        if first and not first.isdigit() and not first.lower().startswith(("candidate", "registered", "ballots")):
            return first
    return ""


def _wide(rows: list[list[str]]) -> Count:
    """Races laid out with a row for each candidate and a column for each voting division or place."""
    races: list[Race] = []
    units: dict[str, str] = {}
    i = 0
    while i < len(rows):
        row = rows[i]
        head = next((j for j, cell in enumerate(row) if cell.strip() == "Candidate Name"), None)
        if head is None:
            i += 1
            continue
        title = _title(rows, i)
        total = next((j for j, cell in enumerate(row) if cell.strip().lower().startswith("election")), None)
        columns = {
            j: _code(cell)
            for j, cell in enumerate(row)
            if j > head + 1 and j != total and cell.strip() and cell.strip() not in ("Party", "Elector Organization")
        }
        seats = None
        for back in range(i - 1, max(i - 14, -1), -1):
            if len(rows[back]) > 2 and rows[back][1].strip() == "Vote For":
                seats = _number(rows[back][2])
        candidates: list[Candidate] = []
        ballots: dict[str, int] = {}
        registered: dict[str, int] = {}
        i += 1
        while i < len(rows) and any(cell.strip() for cell in rows[i]):
            data = rows[i]
            label = data[head].strip() if head < len(data) else ""
            lowered = label.lower()
            numbers = {code: _cell(data[j]) for j, code in columns.items() if j < len(data)}
            if "candidate name" in lowered:
                break
            if lowered.startswith(("registered", "ballots cast", "times counted")):
                if lowered.startswith("registered"):
                    registered.update({c: n for c, n in numbers.items() if n is not None and DIVISION.fullmatch(c)})
                else:
                    ballots.update({c: n for c, n in numbers.items() if n is not None})
            elif (
                lowered
                and any(n is not None for n in numbers.values())
                and not lowered.startswith(("number of", "times", "under", "over", "blank", "write"))
            ):
                declared = _number(data[total]) if total is not None and total < len(data) else None
                party = data[head + 1].strip() if head + 1 < len(data) else ""
                candidates.append(
                    Candidate(person(label), party, declared, {c: n for c, n in numbers.items() if n is not None})
                )
            i += 1
        units.update({code: code for code in columns.values()})
        office = OFFICES.get(re.sub(r"\s+", " ", title.lower()), "question")
        if candidates:
            races.append(
                Race(
                    office,
                    title,
                    seats or {"councillor": 10, "commissioner": 7, "trustee": 9}.get(office, 1),
                    candidates,
                    ballots,
                    registered,
                )
            )
    return Count(races, units)


def _plain(text: str) -> str:
    """A name the early files print with the surname in capitals, in the order a ballot prints it."""
    words = []
    for word in " ".join(text.split()).split(" "):
        if len(word) > 1 and word.isupper() and word.replace(".", "").isalpha():
            word = re.sub(r"(?<=['-])([a-z])", lambda m: m.group(1).upper(), word.title())
            word = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), word)
        words.append(word)
    return " ".join(words)


def _kind(title: str) -> str:
    return OFFICES.get(re.sub(r"\s+", " ", title.strip().lower()), "question")


def _seats(office: str) -> int:
    return {"councillor": 10, "commissioner": 7, "trustee": 9}.get(office, 1)


def _all_columns(rows: list[list[str]]) -> Count:
    """The 1999 file: one heading row of voting divisions, then a block for each race with a row for each candidate."""
    columns = {j: _code(cell) for j, cell in enumerate(rows[0]) if j > 1 and cell.strip()}
    races: list[Race] = []
    title = ""
    candidates: list[Candidate] = []
    ballots: dict[str, int] = {}
    registered: dict[str, int] = {}

    def close() -> None:
        if candidates:
            office = _kind(title)
            races.append(Race(office, title, _seats(office), list(candidates), dict(ballots), dict(registered)))

    for row in rows[1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        label = row[0].strip()
        numbers = {code: _cell(row[j]) for j, code in columns.items() if j < len(row)}
        if not any(n is not None for n in numbers.values()):
            close()
            title, candidates = label, []
            ballots, registered = {}, {}
        elif label.lower().startswith("registered"):
            registered.update({c: n for c, n in numbers.items() if n is not None and DIVISION.fullmatch(c)})
        elif label.lower().startswith("ballots cast"):
            ballots.update({c: n for c, n in numbers.items() if n is not None})
        else:
            name = _plain(label)
            if _kind(title) == "question":
                name = name.title()
            candidates.append(
                Candidate(name, row[1].strip(), None, {c: n for c, n in numbers.items() if n is not None})
            )
    close()
    return Count(races, {code: code for code in columns.values()})


def _lettered(rows: list[list[str]]) -> Count:
    """The 1996 file: every line starts with a letter that says what it holds."""
    heading = next(row for row in rows if row and row[0] == "P")
    columns = {j: _code(cell) for j, cell in enumerate(heading) if j >= 5 and cell.strip()}
    total = next(j for j, cell in columns.items() if cell.upper().startswith("ELECTION SUM"))
    del columns[total]
    races: list[Race] = []
    title, code = "", 0
    candidates: list[Candidate] = []
    ballots: dict[str, int] = {}

    def close() -> None:
        if candidates:
            office = _kind(title)
            races.append(Race(office, title, _seats(office), list(candidates), dict(ballots), {}))

    for row in rows:
        if not row or not row[0]:
            continue
        kind = row[0]
        if kind == "R":
            close()
            code, title, candidates, ballots = int(row[1]), " ".join(row[3].split()), [], {}
            if code >= 100:
                title = f"Method of electing: {title}"
        elif kind == "T":
            ballots = {c: n for j, c in columns.items() if j < len(row) and (n := _cell(row[j])) is not None}
        elif kind == "C":
            numbers = {c: _cell(row[j]) for j, c in columns.items() if j < len(row)}
            name = _plain(row[3])
            if _kind(title) == "question" or title.startswith("Method"):
                name = name.title()
            candidates.append(
                Candidate(
                    name,
                    row[4].strip(),
                    _number(row[total]) if total < len(row) else None,
                    {c: n for c, n in numbers.items() if n is not None},
                )
            )
    close()
    return Count(races, {code: code for code in columns.values()})


def _places(rows: list[list[str]], title: str, office: str, seats: int) -> tuple[Race, dict[str, str]]:
    """One race laid out with a row for each voting place and a column for each candidate or answer."""
    head = next(i for i, row in enumerate(rows) if row and row[0].strip().startswith(("Voting Place ID", "Precinct")))
    header = [cell.strip() for cell in rows[head]]
    named = header[1] == "Voting Place"
    cast = header.index("Times Cast")
    registered_at = next((j for j, cell in enumerate(header) if cell.lower().startswith("registered")), None)
    over = header.index("Overvotes")
    names = [(j, cell) for j, cell in enumerate(header) if j > over and cell and cell != "Total Votes"]
    votes: dict[int, dict[str, int]] = {j: {} for j, _ in names}
    totals: dict[int, int | None] = {}
    ballots: dict[str, int] = {}
    registered: dict[str, int] = {}
    places: dict[str, str] = {}
    for row in rows[head + 1 :]:
        if not row or not any(cell.strip() for cell in row):
            continue
        first = row[0].strip()
        if named and not first or not named and first.lower().startswith(("total", "plebiscite - total")):
            totals = {j: _number(row[j]) for j, _ in names if j < len(row)}
            break
        if not named and not _number(row[cast]) and not row[cast].strip():
            continue
        if named:
            code, place = first, " ".join(row[1].split())
        else:
            code, _, place = first.partition(" - ")
            code, place = code.strip(), " ".join(place.split())
        places[code] = place
        count = _number(row[cast])
        if count is not None:
            ballots[code] = count
        if registered_at is not None and (n := _number(row[registered_at])):
            registered[code] = n
        for j, _ in names:
            n = _number(row[j]) if j < len(row) else None
            if n is not None:
                votes[j][code] = n
    candidates: list[Candidate] = []
    for j, text in names:
        if office in ("question", "plebiscite"):
            answer = re.match(r"(yes|no)\b", text.strip(), re.IGNORECASE)
            candidates.append(Candidate(answer.group(1).title() if answer else text, "", totals.get(j), votes[j]))
            continue
        label, party = _party(re.sub(r"^\d+\s+", "", text))
        candidates.append(Candidate(person(label), party, totals.get(j), votes[j]))
    return Race(office, title, seats, candidates, ballots, registered), places


def _zip(path: str, plebiscite: bool = False) -> Count:
    archive = zipfile.ZipFile(path)
    races: list[Race] = []
    units: dict[str, str] = {}
    for member in sorted(archive.namelist()):
        base = member.rsplit("/", 1)[-1].removesuffix(".csv")
        kind = base.split(" - ")[-1].strip().lower()
        if kind.split("_")[-1] in ("overview", "totals") or kind.endswith("totals"):
            continue
        rows = _rows(_text(archive.read(member)))
        title = " ".join(rows[0][0].split())
        seats = re.search(r"Vote for:?\s*(\d+)", title, re.IGNORECASE)
        kind = re.sub(r"^(results|election\d+)", "", kind.split("_")[-1])
        office = {
            "mayor": "mayor",
            "councillor": "councillor",
            "parkboard": "commissioner",
            "parkcommissioner": "commissioner",
            "schooltrustee": "trustee",
        }.get(kind, "plebiscite" if plebiscite else "question")
        race, places = _places(rows, title, office, int(seats.group(1)) if seats else 1)
        if office != "question" and not seats:
            race = race._replace(seats={"councillor": 10, "commissioner": 7, "trustee": 9}.get(office, 1))
        races.append(race)
        units.update(places)
    return Count(races, units)


def count(year: int) -> Count:
    """The city's results for one general election."""
    path = raw.cached(SOURCE, FILES[year]).path
    if SUFFIX[year] == ".zip":
        found = _zip(path.as_posix())
        if year in PLEBISCITE:
            more = _zip(raw.cached(SOURCE, PLEBISCITE[year]).path.as_posix(), plebiscite=True)
            found = Count(found.races + more.races, {**found.units, **more.units})
        return found
    rows = _rows(_text(path.read_bytes()))
    if year == 1996:
        return _lettered(rows)
    return _all_columns(rows) if year == 1999 else _wide(rows)


def day(year: int) -> date:
    return {
        1996: date(1996, 11, 16),
        1999: date(1999, 11, 20),
        2002: date(2002, 11, 16),
        2005: date(2005, 11, 19),
        2008: date(2008, 11, 15),
        2011: date(2011, 11, 19),
        2014: date(2014, 11, 15),
        2018: date(2018, 10, 20),
        2022: date(2022, 10, 15),
        2026: date(2026, 10, 17),
    }[year]
