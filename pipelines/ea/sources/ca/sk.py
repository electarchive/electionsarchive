"""Saskatchewan's provincial general elections and by-elections, by polling division, from Elections Saskatchewan.

Elections Saskatchewan publishes a CSV file of poll-by-poll results for the general elections of 2016, 2020, and 2024
and for eleven of the twelve by-elections held since 2014, a workbook of poll results for 2011, and a Statement of
Votes for each general election since 1975. The Statements of 1999, 2016, 2020, and 2024 carry a text layer; they
give each constituency's official totals and its elected member, which are stored as declared. The constituency
shapes come from the authority's own map services: the 2002 boundaries embedded in its boundary comparison map, the
2012 boundaries, and the 2022 boundaries in force since the 2024 election.
"""

import csv
import functools
import io
import json
import re
import subprocess
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final
from uuid import uuid5

import fastexcel
import numpy
import pyproj
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.licenses import PUBLIC_RECORD, UNLICENSED

SOURCE: Final = "ca.sk"
# Canada's province polity, which the federal pipeline holds under an id it minted itself.
SASKATCHEWAN: Final = "01a13fb6-eab7-7501-a0f9-490691d098f3"
CDN: Final = "https://cdn.elections.sk.ca"
SITE: Final = "https://www.elections.sk.ca"
INDEX: Final = "https://elections.sk.ca/reports-data/election-results/"
GIS: Final = "https://services1.arcgis.com/kolHt5SBE5YFi0JC/arcgis/rest/services"
QUERY: Final = "query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
SHAPES_2022: Final = f"{GIS}/Constituency2024/FeatureServer/2/{QUERY}"
SHAPES_2012: Final = f"{GIS}/Const_2012_Boundaries/FeatureServer/0/{QUERY}"
SHAPES_2002: Final = "https://www.arcgis.com/sharing/rest/content/items/00beabcc043b403e93007c37fa7e3a62/data?f=json"

CSV_GENERALS: Final = {
    date(2016, 4, 4): f"{CDN}/reports/2016%20GE%20Poll%20by%20Poll%20Results.csv",
    date(2020, 10, 26): f"{CDN}/upload/2020-GE-POLL-BY-POLL-RESULTS-v2.0.csv",
    date(2024, 10, 28): f"{CDN}/upload/2024-GE-POLL-BY-POLL-RESULTS-v1.0.csv",
}
CSV_NOTES: Final = f"{CDN}/reports/Notes%20for%20CSV%20SOV%20files.docx"
WORKBOOK_2011: Final = f"{CDN}/upload/statementofvotes-2011-pollresults.xlsx"
DAY_2011: Final = date(2011, 11, 7)
DAY_1999: Final = date(1999, 9, 16)
STATEMENTS: Final = {
    DAY_1999: f"{CDN}/reports/1999%20Statement-of-Votes-24th%20GE.pdf",
    date(2016, 4, 4): f"{CDN}/reports/2016%20Statement%20of%20Votes%20Volume%201.pdf",
    date(2020, 10, 26): f"{CDN}/upload/2020-Statement-of-Votes-Volume-1-web-viewing.pdf",
    date(2024, 10, 28): f"{CDN}/upload/ESK-GE-Volume-1-Statement-of-Votes-singles.pdf",
}
SUMMARY_PAGES: Final = {
    DAY_1999: "https://elections.sk.ca/reports-data/election-results/1999-2/",
    DAY_2011: "https://elections.sk.ca/reports-data/election-results/2011-2/",
}
GENERALS: Final = (DAY_1999, DAY_2011, *CSV_GENERALS)
BY_ELECTIONS: Final[dict[date, list[str]]] = {
    date(2017, 3, 2): [f"{CDN}/upload/POLL-BY-POLL_MEEWASIN.csv"],
    date(2017, 9, 7): [f"{CDN}/upload/POLL-BY-POLL_FAIRVIEW.csv"],
    date(2018, 3, 1): [
        f"{CDN}/upload/POLL-BY-POLL_KINDERSLEY.csv",
        f"{CDN}/upload/POLL-BY-POLL_MELFORT.csv",
        f"{CDN}/upload/POLL-BY-POLL_SWIFT.csv",
    ],
    date(2018, 9, 12): [f"{CDN}/upload/REGINA-NORTHEAST-POLL-BY-POLL.csv"],
    date(2022, 2, 15): [f"{CDN}/upload/ATHABASCA-POLL-BY-POLL-v1.0.csv"],
    date(2022, 9, 26): [f"{CDN}/upload/MEEWASIN-POLL-BY-POLL-v1.0.csv"],
    date(2023, 8, 10): [
        f"{CDN}/upload/LUMSDEN-MORSE-POLL-BY-POLL-SEPT2023.csv",
        f"{CDN}/upload/REGINA-CORONATION-PARK-POLL-BY-POLL-SEPT2023.csv",
        f"{CDN}/upload/REGINA-WALSH-ACRES-POLL-BY-POLL-SEPT2023.csv",
    ],
}

# The boundary maps, each in force from the first general election held on it until the next map's.
MAPS: Final = {
    "1994": (date(1995, 6, 21), date(2003, 11, 5)),
    "2002": (date(2003, 11, 5), date(2016, 4, 4)),
    "2012": (date(2016, 4, 4), date(2024, 10, 28)),
    "2022": (date(2024, 10, 28), None),
}


def map_of(day: date) -> str:
    return next(name for name, (start, end) in MAPS.items() if start <= day and (end is None or day < end))


# Party labels as the sources print them, and the party each names. The Green Party of Saskatchewan printed GP until
# 2016 and SGP after it, and the Buffalo Party BP in 2020 and BPSK in 2024.
LABELS: Final = {
    "sp": "sp",
    "sask.": "sp",
    "sask": "sp",
    "ndp": "ndp",
    "n.d.p.": "ndp",
    "lib": "lib",
    "l.": "lib",
    "liberal": "lib",
    "spp": "spp",
    "pc": "pc",
    "p.c.": "pc",
    "gp": "green",
    "sgp": "green",
    "nga": "nga",
    "wip": "wip",
    "bp": "buffalo",
    "bpsk": "buffalo",
    "sup": "sup",
    "ind": "ind",
    "ind.": "ind",
    "independent": "ind",
}
PARTIES: Final = {
    "sp": ("Saskatchewan Party", "SP"),
    "ndp": ("New Democratic Party, Sask. Section", "NDP"),
    "lib": ("Saskatchewan Liberal Party", "LIB"),
    "spp": ("Saskatchewan Progress Party", "SPP"),
    "pc": ("Progressive Conservative Party of Saskatchewan", "PC"),
    "green": ("Saskatchewan Green Party", "SGP"),
    "nga": ("New Green Alliance", "NGA"),
    "wip": ("Western Independence Party of Saskatchewan", "WIP"),
    "buffalo": ("Buffalo Party of Saskatchewan", "BPSK"),
    "sup": ("Saskatchewan United Party", "SUP"),
}
# What the authority prints for the whole province in each general election: the votes each party's candidates
# received and the seats each won. The 1999 and 2011 figures are its election pages', the others its Statements'.
PUBLISHED: Final[dict[date, tuple[dict[str, int], dict[str, int]]]] = {
    DAY_1999: (
        {"lib": 81694, "ndp": 157046, "nga": 4101, "pc": 1609, "sp": 160603, "ind": 422},
        {"lib": 4, "ndp": 29, "sp": 25},
    ),
    DAY_2011: (
        {"green": 11561, "lib": 2237, "ndp": 128673, "pc": 1315, "sp": 258598, "wip": 58, "ind": 44},
        {"ndp": 9, "sp": 49},
    ),
    date(2016, 4, 4): (
        {"green": 7967, "lib": 15568, "ndp": 131137, "pc": 5571, "sp": 270776, "wip": 318, "ind": 1693},
        {"ndp": 10, "sp": 51},
    ),
    date(2020, 10, 26): (
        {"buffalo": 11298, "green": 10031, "lib": 355, "ndp": 140576, "pc": 8404, "sp": 269996, "ind": 1076},
        {"ndp": 13, "sp": 48},
    ),
    date(2024, 10, 28): (
        {
            "buffalo": 3267,
            "ndp": 188373,
            "pc": 4397,
            "green": 7957,
            "sp": 244037,
            "spp": 536,
            "sup": 18023,
            "ind": 340,
        },
        {"ndp": 27, "sp": 34},
    ),
}
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ESK: Final = key("source", "esk")
BOUNDARY_SOURCE: Final = key("source", "boundaries")
ASSEMBLY: Final = key("body", "legislative_assembly")
FPTP: Final = key("electoral_system", "fptp")
SCHEME: Final = "ca.sk"
POLL_SCHEME: Final = "ca.sk_poll"


def boundary_set(name: str) -> str:
    return key("boundary_set", SCHEME, name)


def party_code(label: str) -> str:
    code = LABELS.get(label.strip().casefold())
    if code is None:
        raise ValueError(f"unknown party label {label!r}")
    return code


def fold(name: str) -> str:
    """A constituency name reduced for matching: `SASKATOON MEEWASIN` and `Saskatoon Meewasin` agree."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", " ", text.replace("&", " and ")).strip()


def title(name: str) -> str:
    """A name printed in capitals in mixed case: `REGINA WALSH ACRES` is Regina Walsh Acres."""
    if name != name.upper():
        return name
    words = []
    for word in re.split(r"(\s+|-)", name.lower()):
        words.append(word if word.strip() in ("", "-") else word.capitalize())
    return "".join(words).replace("Mcm", "McM")


def person_name(text: str) -> str:
    """`Laliberte, Leroy` is Leroy Laliberte."""
    text = re.sub(r"\s+", " ", text).strip()
    if "," in text:
        last, _, first = text.partition(",")
        text = f"{first.strip()} {last.strip()}"
    return text


@dataclass
class Poll:
    code: str
    name: str
    registered: int | None
    votes: list[int]
    rejected: int


@dataclass
class Race:
    number: int | None
    name: str
    candidates: list[tuple[str, str]]  # name, party code
    polls: list[Poll]
    # The authority's own totals: registered voters (when printed), each candidate's votes, and rejected ballots.
    declared: tuple[int | None, list[int], int] | None = None
    elected: str | None = None
    combined: list[str] = field(default_factory=list)
    shared_registered: int | None = None
    labels: list[str] = field(default_factory=list)
    unbalanced: list[str] = field(default_factory=list)


def fetch() -> None:
    for url in (*CSV_GENERALS.values(), *(u for urls in BY_ELECTIONS.values() for u in urls)):
        raw.fetch(SOURCE, url, suffix=".csv")
    raw.fetch(SOURCE, CSV_NOTES, suffix=".docx")
    raw.fetch(SOURCE, WORKBOOK_2011, suffix=".xlsx")
    for url in STATEMENTS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in SUMMARY_PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    raw.fetch(SOURCE, SHAPES_2022, suffix=".geojson")
    raw.fetch(SOURCE, SHAPES_2012, suffix=".geojson")
    raw.fetch(SOURCE, SHAPES_2002, suffix=".json")


def _int(text: str | None) -> int | None:
    text = (text or "").strip().replace(",", "")
    if text in ("", "-", "—", "–"):
        return None
    return int(text)


def _rows(url: str) -> list[dict[str, str]]:
    data = raw.cached(SOURCE, url).path.read_bytes()
    text = data.decode("utf-8-sig") if _utf8(data) else data.decode("cp1252")
    return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(io.StringIO(text))]


def _utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _unique(code: str, seen: set[str]) -> str:
    out, n = code, 2
    while out in seen:
        out, n = f"{code}#{n}", n + 1
    seen.add(out)
    return out


def _poll_code(row: dict[str, str]) -> str:
    code = row.get("Poll") or row.get("Poll Number") or ""
    if not code:
        code = changeset.slug(row.get("Poll Name") or row.get("PollName") or "poll").upper()
    return code.replace(" ", "")


# Candidates whose votes a poll file prints with no name in its candidate column, named as the Statement of Votes for
# the same election names them.
UNNAMED: Final = {
    ("CANORA-PELLY", "WIP"): "David Sawkiw",
    ("KELVINGTON-WADENA", "WIP"): "Walter Hrappsted",
    ("LAST MOUNTAIN-TOUCHWOOD", "WIP"): "Frank J. Serfas",
    ("MARTENSVILLE-WARMAN", "WIP"): "Pamela Spencer",
}


def _race_from_rows(rows: list[dict[str, str]]) -> list[Race]:
    """The rows of a poll-by-poll file with one vote column per party label and a `<label> Candidate` column naming
    that party's candidate on each row. A party ran a candidate in a constituency when any of its rows names one or
    gives that party a vote."""
    head = list(rows[0])
    candidate_columns = [c for c in head if re.fullmatch(r".+ ?Candidate", c, re.IGNORECASE)]
    labels = [re.sub(r" ?Candidate$", "", c, flags=re.IGNORECASE) for c in candidate_columns]
    vote_columns = {label: next(c for c in head if c.casefold() == label.casefold()) for label in labels}
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row.get("Constituency") or row.get("ConstituencyName") or "", []).append(row)
    races: dict[str, Race] = {}
    for name, group in grouped.items():
        number = _int(group[0].get("Constituency Number") or group[0].get("ConstituencyNumber"))
        candidates: list[tuple[str, str, str]] = []
        for label, column in zip(labels, candidate_columns, strict=True):
            printed = {person_name(row[column]) for row in group if row[column]}
            voted = any(_int(row[vote_columns[label]]) for row in group)
            if len(printed) > 1:
                raise ValueError(f"{name}: {label} names {sorted(printed)}")
            if printed:
                candidates.append((printed.pop(), party_code(label), label))
            elif voted:
                candidates.append((UNNAMED[(name, label)], party_code(label), label))
        race = Race(number, title(name), [(n, p) for n, p, _ in candidates], [])
        race.labels = [label for _, _, label in candidates]
        races[name] = race
        seen: set[str] = set()
        for row in group:
            votes = [_int(row[vote_columns[label]]) for label in race.labels]
            rejected = _int(row.get("Rejected") or row.get("RejectedBallots") or row.get("Rejected Ballots"))
            poll_name = row.get("Poll Name") or row.get("PollName") or ""
            if all(v is None for v in votes) and rejected is None:
                note = row.get("Notes") or row.get("Note") or ""
                race.combined.append(f"{_poll_code(row)} ({title(poll_name)}): {note or 'no figures'}")
                continue
            race.polls.append(
                Poll(
                    _unique(_poll_code(row), seen),
                    title(poll_name),
                    _int(row.get("Registered Voters") or row.get("RegisteredVoters")),
                    [v or 0 for v in votes],
                    rejected or 0,
                )
            )
    for race in races.values():
        registered = [p.registered for p in race.polls if p.registered is not None]
        if len(registered) > 2 and len(set(registered)) == 1:
            # The file repeats the constituency's registered voters on every row.
            race.shared_registered = registered[0]
            for poll in race.polls:
                poll.registered = None
    return list(races.values())


HEADER_CANDIDATE: Final = re.compile(r"^(?P<name>.+?)\s*(?:\((?P<p1>[A-Z.]+)\)|\s(?P<p2>[A-Z.]+))$")


def _race_from_headed(rows: list[dict[str, str]]) -> Race:
    """An older by-election file, whose vote columns are headed by each candidate's name and party."""
    head = list(rows[0])
    candidates: list[tuple[str, str, str]] = []
    for column in head:
        found = HEADER_CANDIDATE.match(column)
        if found and (found["p1"] or found["p2"]) and column not in ("Rejected Ballots",):
            label = found["p1"] or found["p2"]
            if label.casefold() in LABELS:
                candidates.append((column, found["name"].strip(), party_code(label)))
    name = rows[0]["Constituency Name"]
    race = Race(None, title(name), [(n, p) for _, n, p in candidates], [])
    seen: set[str] = set()
    for row in rows:
        votes = [_int(row[c]) for c, _, _ in candidates]
        rejected = _int(row.get("Rejected Ballots"))
        if all(v is None for v in votes) and rejected is None:
            race.combined.append(
                f"{_poll_code(row)} ({title(row['Poll Name'])}): {row.get('Note') or row.get('Notes')}"
            )
            continue
        race.polls.append(
            Poll(_unique(_poll_code(row), seen), title(row["Poll Name"]), None, [v or 0 for v in votes], rejected or 0)
        )
    return race


@functools.cache
def csv_general(day: date) -> list[Race]:
    races = _race_from_rows(_rows(CSV_GENERALS[day]))
    statement = statement_totals(day)
    if len(statement) != len(races):
        raise ValueError(f"{day}: {len(races)} constituencies, the Statement prints {len(statement)} totals")
    for race, (registered, votes, rejected, elected) in zip(races, statement, strict=True):
        if len(votes) != len(race.candidates):
            raise ValueError(f"{day} {race.name}: the Statement prints {len(votes)} candidates' votes")
        race.declared = (registered, votes, rejected)
        race.elected = elected
    return races


def by_election(url: str) -> Race:
    rows = _rows(url)
    if any(c.endswith("Candidate") for c in rows[0]):
        (race,) = _race_from_rows(rows)
    else:
        race = _race_from_headed(rows)
    return race


def pdf_text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


TOTALS: Final = re.compile(r"^\s*Totals\s+(?P<nums>[\d,\-\s]+?)\s*$")


@functools.cache
def statement_totals(day: date) -> list[tuple[int | None, list[int], int, str]]:
    """Each constituency's Totals row in a Statement of Votes, in print order: registered voters, the candidates'
    votes, rejected ballots (the last figure, votes for candidates, is dropped), and the member it declares elected."""
    found: list[tuple[int | None, list[int], int, str]] = []
    pending: tuple[int | None, list[int], int] | None = None
    waiting = False
    for line in pdf_text(STATEMENTS[day]).splitlines():
        hit = TOTALS.match(line)
        if hit:
            if pending is not None:
                raise ValueError(f"{day}: two Totals rows without an elected member")
            numbers = [_int(x) for x in hit["nums"].split()]
            registered, *votes, rejected, counted = numbers
            if sum(v or 0 for v in votes) != counted:
                raise ValueError(f"{day}: a Totals row whose votes do not add to {counted}: {line}")
            pending = (registered, [v or 0 for v in votes], rejected or 0)
            continue
        if waiting and line.strip() and pending is not None:
            found.append((*pending, line.strip()))
            pending, waiting = None, False
            continue
        elected = re.match(r"^\s*Candidate Elected\s*-\s*(.*?)\s*$", line)
        if elected and pending is not None:
            if not elected.group(1):
                waiting = True  # the name is printed on the next line
                continue
            found.append((*pending, elected.group(1)))
            pending = None
    return found


# The 2011 workbook's sheet names that misspell their constituency.
SHEET_NAMES: Final = {"Sasktoon Nutana": "Saskatoon Nutana", "Saskatoon Silver Spring": "Saskatoon Silver Springs"}


@functools.cache
def workbook_2011() -> list[Race]:
    path = str(raw.cached(SOURCE, WORKBOOK_2011).path)
    book = fastexcel.read_excel(path)
    races = []
    for sheet in book.sheet_names:
        frame = book.load_sheet_by_name(sheet, header_row=None, dtypes="string").to_polars()
        rows = [[(c or "").strip() for c in row] for row in frame.iter_rows()]
        head = next(i for i, r in enumerate(rows) if r[0] == "Poll")
        width = len(rows[head])
        counted = next(i for i in range(width) if rows[head][i].lower().startswith(("totel", "total")))
        names = [rows[head][i] for i in range(4, counted)]
        parties = [rows[head + 1][i] for i in range(4, counted)]
        name = SHEET_NAMES.get(sheet.strip(), sheet.strip())
        race = Race(None, name, [(n, party_code(p)) for n, p in zip(names, parties, strict=True)], [])
        seen: set[str] = set()
        for row in rows[head + 2 :]:
            if row[1] == "Totals" or row[2] == "Totals":
                votes = [_int(x) or 0 for x in row[4:counted]]
                race.declared = (_int(row[3]), votes, _int(row[counted + 1]) or 0)
                continue
            elected = re.match(r"Candidate Elected\s*-\s*(.+)", row[2])
            if elected:
                race.elected = elected.group(1).strip()
            if not (row[0] or row[1]) or not any(row[4 : counted + 2]):
                continue
            votes_or_none = [_int(x) for x in row[4:counted]]
            code = row[0].replace(" ", "") or changeset.slug(row[1]).upper()
            race.polls.append(
                Poll(
                    _unique(code, seen),
                    row[2] or title(row[1]),
                    _int(row[3]) or None,
                    [v or 0 for v in votes_or_none],
                    _int(row[counted + 1]) or 0,
                )
            )
        if race.declared is None or race.elected is None:
            raise ValueError(f"2011 {sheet}: no totals or no elected member")
        races.append(race)
    return races


SUMMARY_1999: Final = re.compile(
    r"(?P<name>[A-Z][^\d]*?)\s{2,}(?P<party>L\.|N\.D\.P\.|Sask\.|P\.C\.|NGA|Ind\.)\s+(?P<votes>[\d,]+)\s+[\d.]+"
)
SUMMARY_HEAD_1999: Final = re.compile(
    r"^(?P<name>[A-Z][A-Za-z.’'\- ]+?)\s+(?P<electors>[\d,]+)\s+[\d.]+\s+(?P<counted>[\d,]+)\s*$"
)


@functools.cache
def summary_1999() -> list[Race]:
    """Appendix V of the 1999 Statement: each constituency's electors, candidates, votes, and rejected ballots, printed
    in two columns per page."""
    text = pdf_text(STATEMENTS[DAY_1999])
    start = text.index("Vote Summary by Constituency      ")
    end = text.index("Vote Summary by Constituency Polling Division:")
    left: list[str] = []
    right: list[str] = []
    split = None
    for line in text[start:end].splitlines():
        if "Constituency" in line and "Electors" in line:
            split = line.index("Constituency", 10) - 2
            continue
        if "Eligible" in line and split is not None:
            continue
        if split is None:
            continue
        left.append(line[:split])
        right.append(line[split:])
    races: list[Race] = []
    for column in (left, right):
        race: Race | None = None
        for line in column:
            stripped = line.strip()
            head = SUMMARY_HEAD_1999.match(stripped)
            if head:
                race = Race(None, head["name"].strip(), [], [], (_int(head["electors"]), [], 0))
                races.append(race)
                continue
            if race is None or race.declared is None:
                continue
            candidate = SUMMARY_1999.match(stripped)
            if candidate:
                race.candidates.append((candidate["name"].strip(), party_code(candidate["party"])))
                race.declared[1].append(_int(candidate["votes"]) or 0)
                continue
            rejected = re.match(r"^Rejected\s+([\d,]+)$", stripped)
            if rejected:
                race.declared = (race.declared[0], race.declared[1], _int(rejected.group(1)) or 0)
    return sorted(races, key=lambda r: fold(r.name))


@functools.cache
def general_1999() -> list[Race]:
    """The 1999 Statement's poll tables (Appendix VI), with each constituency's candidates and totals from Appendix V.

    A poll row ends in the poll's eligible voters (none for advance, absentee, and hospital polls), the candidates'
    votes in the Appendix V order, the total counted, and the rejected ballots, with a dash for none. A polling
    division split between two ballot boxes prints its eligible voters once, on a line between the two boxes; the two
    boxes are stored together as the division.
    """
    races = {fold(r.name): r for r in summary_1999()}
    text = pdf_text(STATEMENTS[DAY_1999])
    blocks = text.split("Vote Summary by Constituency Polling Division:")[1:]
    if len(blocks) != len(races):
        raise ValueError(f"1999: {len(blocks)} poll tables for {len(races)} constituencies")
    for block in blocks:
        name = re.split(r"\s{2,}", block.strip(), maxsplit=1)[0]
        race = races[fold(name)]
        width = len(race.candidates)
        lines = block.splitlines()
        head = next(i for i, line in enumerate(lines) if re.match(r"^\s*No\.\s+Name", line))
        cut = lines[head].index("Rejected") + len("Rejected") + 2
        elected = next(
            (
                re.split(r"\s{2,}", m.group(1))[0]
                for line in lines
                if (m := re.match(r"^\s*Candidate Elected\s*-\s*(.+?)\s*$", line))
            ),
            None,
        )
        race.elected = elected
        seen: set[str] = set()
        held: tuple[str, str, list[int | None]] | None = None
        loose: int | None = None
        for line in lines[head + 1 :]:
            if re.match(r"^\s*Candidate Elected", line):
                break
            body = line[:cut].replace("`", " ").rstrip()
            if not body.strip():
                continue
            tokens = body.split()
            figures: list[int | None] = []
            while tokens and re.fullmatch(r"[\d,]+|-", tokens[-1]):
                figures.insert(0, _int(tokens.pop()))
            if not tokens and len(figures) == 1:
                loose = figures[0]
                continue
            if tokens and tokens[0] in ("Total", "Totals"):
                if len(figures) == width + 3:
                    figures = figures[1:]
                if [f or 0 for f in figures[:width]] != (race.declared or (None, [], 0))[1]:
                    raise ValueError(f"1999 {race.name}: Totals row {figures} differs from Appendix V")
                continue
            if len(figures) not in (width + 2, width + 3):
                if figures:
                    raise ValueError(f"1999 {race.name}: a poll row with {len(figures)} figures: {line!r}")
                continue
            registered = figures[0] if len(figures) == width + 3 else None
            votes = figures[-width - 2 : -2]
            counted, rejected = figures[-2], figures[-1]
            if sum(v or 0 for v in votes) != (counted or 0):
                race.unbalanced.append(
                    f"{' '.join(tokens)}: the votes add to {sum(v or 0 for v in votes)}, the "
                    f"row's total counted is {counted or 0}"
                )
            match = re.match(r"^(\d+[A-Z]?)\s+(.*)$", " ".join(tokens))
            code, label = (match.group(1), match.group(2)) if match else ("", " ".join(tokens))
            if not code:
                code = changeset.slug(label).upper()
            row = [registered, *votes, rejected]
            if loose is not None and held is not None and registered is None:
                # The second box of a split division: merge it with the first under the eligible voters between them.
                first_code, first_label, first = held
                merged = [loose, *[(a or 0) + (b or 0) for a, b in zip(first[1:], row[1:], strict=True)]]
                race.polls.append(_poll_1999(f"{first_code}/{code}", first_label, merged, seen))
                held, loose = None, None
                continue
            if held is not None:
                race.polls.append(_poll_1999(*held, seen))
                held = None
            if registered is None and re.fullmatch(r"\d+A", code):
                held = (code, label, row)
                continue
            race.polls.append(_poll_1999(code, label, row, seen))
        if held is not None:
            race.polls.append(_poll_1999(*held, seen))
        sums = [sum(p.votes[i] for p in race.polls) for i in range(width)]
        if race.declared is None or sums != race.declared[1]:
            raise ValueError(f"1999 {race.name}: polls add to {sums}, Appendix V prints {race.declared}")
    return sorted(races.values(), key=lambda r: fold(r.name))


def _poll_1999(code: str, label: str, row: Sequence[int | None], seen: set[str]) -> Poll:
    return Poll(_unique(code, seen), label, row[0], [v or 0 for v in row[1:-1]], row[-1] or 0)


@functools.cache
def general(day: date) -> list[Race]:
    if day == DAY_1999:
        races = general_1999()
    elif day == DAY_2011:
        races = workbook_2011()
    else:
        races = csv_general(day)
    votes: dict[str, int] = {}
    seats: dict[str, int] = {}
    for race in races:
        if race.declared is None:
            raise ValueError(f"{day} {race.name}: no declared totals")
        for (_, party), count in zip(race.candidates, race.declared[1], strict=True):
            votes[party] = votes.get(party, 0) + count
        winner = winner_of(race)
        party = race.candidates[winner][1]
        seats[party] = seats.get(party, 0) + 1
    expected_votes, expected_seats = PUBLISHED[day]
    if votes != expected_votes or seats != expected_seats:
        raise ValueError(f"{day}: built {votes} {seats}, the authority prints {expected_votes} {expected_seats}")
    return races


def _plain(name: str) -> list[str]:
    return [w for w in re.sub(r"\([^)]*\)|[^\w\s]", " ", fold(name)).split() if len(w) > 1]


def winner_of(race: Race) -> int:
    """The index of the elected candidate: the one the authority names, which must also have the most votes."""
    totals = (
        race.declared[1]
        if race.declared
        else [sum(p.votes[i] for p in race.polls) for i in range(len(race.candidates))]
    )
    top = max(range(len(totals)), key=lambda i: totals[i])
    if sorted(totals)[-2:].count(totals[top]) > 1:
        raise ValueError(f"{race.name}: a tie for the most votes")
    if race.elected is not None:
        declared = _plain(race.elected)
        printed = _plain(race.candidates[top][0])
        if not declared or (declared[-1] != printed[-1] and declared[0] != printed[0]):
            raise ValueError(
                f"{race.name}: the authority declares {race.elected!r}, the most votes are {race.candidates[top]}"
            )
    return top


def shapes(url: str) -> list[tuple[dict[str, object], shapely.Geometry]]:
    data = json.loads(raw.cached(SOURCE, url).path.read_bytes())
    found: list[tuple[dict[str, object], shapely.Geometry]] = []
    if "features" in data:
        for feature in data["features"]:
            found.append((feature["properties"], shapely.geometry.shape(feature["geometry"])))
        return found
    project = pyproj.Transformer.from_crs(3857, 4326, always_xy=True)
    layer = data["operationalLayers"][0]["featureCollection"]["layers"][0]
    for feature in layer["featureSet"]["features"]:
        rings = feature["geometry"]["rings"]
        polygon = shapely.make_valid(shapely.geometry.shape({"type": "Polygon", "coordinates": rings}))
        moved = shapely.transform(polygon, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
        found.append((feature["attributes"], moved))
    return found


def _clean(shape: shapely.Geometry) -> shapely.MultiPolygon:
    shape = shapely.force_2d(shapely.make_valid(shape))
    parts = [part for part in shapely.get_parts(shape) if part.geom_type == "Polygon" and part.area > 0]
    polygons: list[shapely.Polygon] = []
    for part in parts:
        polygons.extend(shapely.get_parts(part))
    return shapely.MultiPolygon(polygons)


def _wkb(shape: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True)


def area(map_name: str, code: str) -> str:
    return key("area", map_name, code)


@functools.cache
def districts() -> dict[str, dict[str, tuple[str, str, shapely.Geometry | None]]]:
    """Each map's constituencies: code to (code, name, shape). The 1994 map's constituencies are known only by name, so
    their code is the name's slug."""
    out: dict[str, dict[str, tuple[str, str, shapely.Geometry | None]]] = {}
    out["2022"] = {
        str(p["ConstituencyNumber"]): (str(p["ConstituencyNumber"]), str(p["Constituency"]).strip(), _clean(s))
        for p, s in shapes(SHAPES_2022)
    }
    found: dict[str, tuple[str, str, shapely.Geometry | None]] = {}
    for p, s in shapes(SHAPES_2012):
        found[str(p["Con_Num"])] = (str(p["Con_Num"]), str(p["Con_Name"]).strip(), _clean(s))
    out["2012"] = found
    names_2011 = {fold(r.name): r.name for r in workbook_2011()}
    merged: dict[str, tuple[str, list[shapely.Geometry]]] = {}
    for p, s in shapes(SHAPES_2002):
        number = str(p["CON_NUM"])
        name = names_2011[fold(str(p["CON_NAME"]))]
        merged.setdefault(number, (name, []))[1].append(s)
    out["2002"] = {n: (n, name, _clean(shapely.union_all(parts))) for n, (name, parts) in merged.items()}
    out["1994"] = {changeset.slug(r.name): (changeset.slug(r.name), r.name, None) for r in summary_1999()}
    for name, expected in (("2022", 61), ("2012", 61), ("2002", 58), ("1994", 58)):
        if len(out[name]) != expected:
            raise ValueError(f"the {name} map has {len(out[name])} constituencies, expected {expected}")
    return out


def district_of(map_name: str, name: str, number: int | None = None) -> str:
    """The area of a constituency on a map, by its number where the source gives one (checked against its name)."""
    found = districts()[map_name]
    by_name = {fold(n): code for code, n, _ in found.values()}
    code = by_name.get(fold(name))
    if number is not None and map_name != "1994":
        if str(number) not in found:
            raise ValueError(f"{map_name}: no constituency {number}")
        if code is not None and code != str(number):
            raise ValueError(f"{map_name}: {name} is {code}, the source says {number}")
        code = str(number)
    if code is None:
        raise ValueError(f"{map_name}: no constituency named {name!r}")
    return code


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/ge-{day}" for day in GENERALS),
        *(f"{SOURCE}/be-{day}" for day in BY_ELECTIONS),
    ]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Saskatchewan's provincial constituencies on the boundary maps of 1994, 2002, 2012, and 2022, with the shapes "
        "Elections Saskatchewan publishes for the last three",
    )
    p.external.add(SASKATCHEWAN)
    p.add(
        "source",
        id=BOUNDARY_SOURCE,
        kind="official_body",
        publisher="Elections Saskatchewan",
        title="Provincial constituency boundaries of 2002, 2012, and 2022 (Elections Saskatchewan map services)",
        url=f"{GIS}/Constituency2024/FeatureServer",
        license=UNLICENSED,
        license_url=None,
        attribution="Elections Saskatchewan",
        notes=(
            "The 2022 and 2012 boundaries are the authority's Constituency2024 and Const_2012_Boundaries feature "
            "services. The 2002 boundaries are the features embedded in its Constituency Boundary Comparison 2002 - "
            "2013 web map, which stores them in Web Mercator rounded to the metre."
        ),
    )
    for url in (SHAPES_2022, SHAPES_2012, SHAPES_2002):
        p.artifact(raw.cached(SOURCE, url), BOUNDARY_SOURCE)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=SASKATCHEWAN,
        kind="electoral_district",
        rank=1,
        noun="constituency",
        code_system=None,
    )
    p.add(
        "scheme",
        code=POLL_SCHEME,
        polity_id=SASKATCHEWAN,
        kind="precinct",
        rank=2,
        noun="polling division",
        code_system=None,
    )
    for map_name, (start, end) in MAPS.items():
        p.add(
            "boundary_set",
            id=boundary_set(map_name),
            scheme_code=SCHEME,
            vintage=map_name,
            valid_from=start,
            valid_to=end,
            as_of=start,
        )
        for code, name, shape in districts()[map_name].values():
            id = area(map_name, code)
            p.add("area", id=id, scheme_code=SCHEME)
            p.add("area_code", area_id=id, code=code, valid_from=start, valid_to=end)
            p.name(id, name, "en", "official")
            if shape is not None:
                p.add(
                    "area_geometry",
                    area_id=id,
                    valid_from=start,
                    valid_to=end,
                    geom=_wkb(shape),
                    label_point=_wkb(shapely.point_on_surface(shape)),
                    quality="official",
                    source_id=BOUNDARY_SOURCE,
                )
    return p


def all_races() -> list[tuple[date, Race]]:
    found = [(day, race) for day in GENERALS for race in general(day)]
    for day, urls in BY_ELECTIONS.items():
        found += [(day, by_election(url)) for url in urls]
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Saskatchewan's parties, the Legislative Assembly, and its single-member plurality system, from Elections "
        "Saskatchewan",
    )
    p.external.add(SASKATCHEWAN)
    p.add(
        "source",
        id=ESK,
        kind="official_body",
        publisher="Elections Saskatchewan",
        title="Election results: poll-by-poll results and Statements of Votes",
        url=INDEX,
        license=PUBLIC_RECORD,
        license_url=None,
        attribution="Elections Saskatchewan",
    )
    cited = [
        *CSV_GENERALS.values(),
        CSV_NOTES,
        WORKBOOK_2011,
        *STATEMENTS.values(),
        *SUMMARY_PAGES.values(),
        *(u for urls in BY_ELECTIONS.values() for u in urls),
    ]
    for url in cited:
        p.artifact(raw.cached(SOURCE, url), ESK)
    p.add("body", id=ASSEMBLY, polity_id=SASKATCHEWAN, kind="unicameral")
    p.name(ASSEMBLY, "Legislative Assembly of Saskatchewan", "en", "official")
    p.add("body_size", body_id=ASSEMBLY, seats=58, valid_from=MAPS["1994"][0], valid_to=MAPS["2012"][0])
    p.add("body_size", body_id=ASSEMBLY, seats=61, valid_from=MAPS["2012"][0], valid_to=None)
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=SASKATCHEWAN,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="One member is elected for each constituency, by the most votes.",
        lang="en",
    )
    used = {party for _, race in all_races() for _, party in race.candidates} - {"ind"}
    for code in sorted(used):
        official, abbreviation = PARTIES[code]
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=SASKATCHEWAN)
        p.name(id, official, "en", "official")
        p.name(id, abbreviation, "en", "abbreviation", False)
    return p


METHOD: Final = (
    "Counted per polling division and published by Elections Saskatchewan{file}. Each row gives the poll's "
    "registered voters where printed, each candidate's votes, and the rejected ballots; valid votes here are the sum "
    "of the candidates' votes and ballots cast are valid votes plus rejected ballots. Advance, mobile, personal care "
    "facility, hospital, homebound, and mail-in polls are stored as their own units within the constituency, and "
    "carry no registered voters where the source prints none.{declared}"
)
DECLARED_NOTE: Final = (
    " The constituency totals and the elected member are the ones the authority's Statement of Votes prints, stored as "
    "declared."
)


def _kind(code: str, name: str) -> str:
    text = f"{code} {name}".casefold()
    if any(w in text for w in ("mail", "vbm", "absentee")):
        return "postal_district"
    if re.match(r"^(m|mp|pcf|hrt|home)", code.casefold()) or any(
        w in text for w in ("mobile", "homebound", "hospital", "personal care", "remand")
    ):
        return "mobile"
    return "standard"


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def _contests(p: Package, day: date, election: str, path: str, races: list[Race], file: str) -> None:
    map_name = map_of(day)
    over = 0
    differs: list[str] = []
    for race in races:
        code = district_of(map_name, race.name, race.number)
        district = area(map_name, code)
        name = districts()[map_name][code][1]
        tag = f"{day}:{code}"
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=ASSEMBLY,
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="counted",
            boundary_set_id=boundary_set(map_name),
        )
        p.name(contest, name, "en", "official")
        p.add("slug", path=f"{path}/{changeset.slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ESK,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD.format(file=file, declared=DECLARED_NOTE if race.elected else ""),
            lang="en",
        )
        winner = winner_of(race)
        options = []
        for index, (person_label, party) in enumerate(race.candidates):
            option = key("option", tag, index + 1)
            person = key("person", tag, index + 1)
            options.append(option)
            party_id = None if party == "ind" else key("party", party)
            p.add("person", id=person)
            p.name(person, person_label, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party_id,
                ballot_position=index + 1,
                ballot_label=person_label,
            )
            p.add(
                "candidacy",
                id=key("candidacy", tag, index + 1),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=person_label,
                member_of_party_id=party_id,
                is_independent=party == "ind",
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if index == winner else "lost")
        sums = [sum(poll.votes[i] for poll in race.polls) for i in range(len(options))]
        rejected = sum(poll.rejected for poll in race.polls)
        if race.declared is not None:
            registered, votes, declared_rejected = race.declared
            for option, value in zip(options, votes, strict=True):
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
            if registered is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=registered)
            p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=declared_rejected)
            p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=sum(votes))
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=None,
                measure=CAST,
                value=sum(votes) + declared_rejected,
            )
            if votes != sums or declared_rejected != rejected:
                differs.append(name)
                p.add(
                    "note",
                    id=key("note", result_set, "differs"),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=(
                        f"The poll rows add to {', '.join(f'{v:,}' for v in sums)} votes for the candidates and "
                        f"{rejected:,} rejected ballots; the Statement of Votes prints "
                        f"{', '.join(f'{v:,}' for v in votes)} and {declared_rejected:,}. Both are stored as printed."
                    ),
                    lang="en",
                )
        if race.shared_registered is not None:
            p.add(
                "note",
                id=key("note", result_set, "registered"),
                entity_id=result_set,
                kind="caveat",
                body_md=(
                    f"The poll file prints the constituency's {race.shared_registered:,} registered voters on every "
                    "row, so they are not stored for each poll."
                ),
                lang="en",
            )
        if race.unbalanced:
            p.add(
                "note",
                id=key("note", result_set, "unbalanced"),
                entity_id=result_set,
                kind="caveat",
                body_md="The Statement prints a total counted that differs from the sum of the candidates' votes on "
                f"these poll rows: {'; '.join(race.unbalanced)}. The candidates' votes are stored as printed, and valid "
                "votes are their sum.",
                lang="en",
            )
        if race.combined:
            listed = "; ".join(race.combined)
            p.add(
                "note",
                id=key("note", result_set, "combined"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"These polls print no figures of their own, because the authority reported them with another "
                f"poll: {listed}.",
                lang="en",
            )
        overs: list[str] = []
        for poll in race.polls:
            unit = key("unit", tag, poll.code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=SASKATCHEWAN,
                scheme_code=POLL_SCHEME,
                code=f"{day}:{code}:{poll.code}",
                area_relation="within",
                area_id=district,
                kind=_kind(poll.code, poll.name),
            )
            label = f"{name}, poll {poll.code}" + (f" ({poll.name})" if poll.name else "")
            p.name(unit, label, "en", "official")
            valid = sum(poll.votes)
            _put(p, result_set, unit, None, REGISTERED, poll.registered)
            _put(p, result_set, unit, None, CAST, valid + poll.rejected)
            _put(p, result_set, unit, None, VALID, valid)
            _put(p, result_set, unit, None, INVALID, poll.rejected)
            for option, value in zip(options, poll.votes, strict=True):
                _put(p, result_set, unit, option, VOTES, value)
            if poll.registered is not None and valid + poll.rejected > poll.registered:
                overs.append(poll.code)
        if overs:
            over += len(overs)
            p.add(
                "note",
                id=key("note", result_set, "overcount"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"The authority prints more ballots than registered voters at poll(s) {', '.join(overs)}. The "
                "figures are stored as printed.",
                lang="en",
            )
    if over:
        p.accept(
            "accounting.turnout",
            f"Elections Saskatchewan prints more ballots than registered voters at {over} polls; each is listed in a "
            "caveat on its result set and stored as printed",
        )
    if differs:
        p.accept(
            "accounting.declared",
            "The Statement of Votes prints constituency totals that differ from the sum of the poll rows in "
            f"{len(differs)} constituencies ({', '.join(differs)}); both are stored as printed",
        )


def election_package(day: date) -> Package:
    p = Package(
        f"{SOURCE}/ge-{day}",
        SOURCE,
        f"The {day.year} Saskatchewan general election by polling division, from Elections Saskatchewan",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(SASKATCHEWAN)
    if day == DAY_1999:
        files = [STATEMENTS[day], SUMMARY_PAGES[day]]
        file = (
            " in Appendix VI of its Statement of Votes, with each constituency's candidates from Appendix V. A "
            "polling division split between two ballot boxes, for which the Statement prints one count of eligible "
            "voters, is stored as one poll coded with both box numbers"
        )
    elif day == DAY_2011:
        files = [WORKBOOK_2011, SUMMARY_PAGES[day]]
        file = " in its workbook of 2011 poll results, which also prints each constituency's totals"
    else:
        files = [CSV_GENERALS[day], CSV_NOTES, STATEMENTS[day]]
        file = " in its CSV file of poll results"
    for url in files:
        p.uses(raw.cached(SOURCE, url))
    election = key("election", day)
    path = f"ca/sk/{day}-general"
    p.add("election", id=election, polity_id=SASKATCHEWAN, kind="general", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{day.year} Saskatchewan general election", "en", "official")
    _contests(p, day, election, path, general(day), file)
    return p


def by_election_package(day: date) -> Package:
    races = [by_election(url) for url in BY_ELECTIONS[day]]
    names = [districts()[map_of(day)][district_of(map_of(day), r.name, r.number)][1] for r in races]
    p = Package(
        f"{SOURCE}/be-{day}",
        SOURCE,
        f"The Saskatchewan by-election{'s' if len(races) > 1 else ''} of {day} in {', '.join(names)}, by polling "
        "division, from Elections Saskatchewan",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(SASKATCHEWAN)
    for url in BY_ELECTIONS[day]:
        p.uses(raw.cached(SOURCE, url))
    election = key("election", day)
    path = f"ca/sk/{day}-by-election"
    p.add(
        "election", id=election, polity_id=SASKATCHEWAN, kind="by_election", date_from=day, date_to=day, status="held"
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    label = " and ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1 else names)
    p.name(election, f"{label} by-election{'s' if len(names) > 1 else ''}, {day:%-d %B %Y}", "en", "official")
    _contests(p, day, election, path, races, " in its CSV file of poll results for the by-election")
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for day in GENERALS:
        election_package(day).write()
    for day in BY_ELECTIONS:
        by_election_package(day).write()


def emit() -> None:
    package.run("load", packages())
