"""Prince Edward Island's general elections and by-elections, by polling division, from Elections PEI.

The Chief Electoral Officer's report on each election since 1966 prints, for every electoral district, the votes each
candidate received. The reports from 1978 on, and the by-election reports from 1975 on, give them poll by poll. The
reports of 2003 and later carry a text layer; the earlier ones are page images, which this module reads with OCR and
checks against the totals the report prints: a table is kept only when each of its columns adds up to the printed
total.
"""

import functools
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from typing import Final
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.licenses import PUBLIC_RECORD

SOURCE: Final = "ca.pei"
SITE: Final = "https://www.electionspei.ca/sites/"
TEST: Final = SITE + "test.electionspei.ca/files/"
WWW: Final = SITE + "www.electionspei.ca/files/"

# The reports with a text layer: general elections 2003 and later.
GENERAL_REPORTS: Final[dict[int, str]] = {
    2003: WWW + "ele_ceorpt2003.pdf",
    2007: TEST + "elec_CEO2007.pdf",
    2011: WWW + "general-2011-10-03.pdf",
    2015: WWW + "2015CEOProv.pdf",
    2019: TEST + "CEO%20REPORT%202019%20WEB%20VERSION.pdf",
    2023: WWW + "2023%20PGE_CEO_Report_FINAL.pdf",
}
NUMBER: Final = re.compile(r"\d[\d,]*")


def pdf_text(url: str) -> list[str]:
    result = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    )
    return result.stdout.split("\n")


def _spans(line: str, start: int = 0) -> list[tuple[int, int, int]]:
    return [(m.start(), m.end(), int(m.group().replace(",", ""))) for m in NUMBER.finditer(line, start)]


@dataclass
class Row:
    code: str
    name: str
    numbers: list[int]


@dataclass
class Table:
    """One district's poll-by-poll table as printed: its rows and the totals row."""

    number: int
    name: str
    header: int
    title_line: int
    rows: list[Row] = field(default_factory=list)
    totals: list[int] = field(default_factory=list)
    totals_line: int = 0


CODE: Final = r"\d{1,3}(?:[A-Z](?=\s)|\s[a-z](?=\s))?"
ROW: Final = re.compile(rf"^\s*(?:({CODE})\s+)?(\S.*?)\s{{2,}}((?:[\d,]+\s*)+)$")
CODED: Final = re.compile(rf"^\s*({CODE})\s+([A-Za-z][^\d]*?)\s*$")
NUMBERS_ONLY: Final = re.compile(r"^\s{6,}((?:[\d,]+\s+)+[\d,]+)\s*$")
TITLE: Final = re.compile(
    r"^\s*(?:District\s+(?:No\.?\s*)?|No\.?\s*)(\d{1,2})\s*[-–]?\s*(\S.*?)\s*(?:Returning Officer.*)?$"
)


def _header(lines: list[str], h: int) -> bool:
    if "Elections" in lines[h] or "Elected" in lines[h]:
        return False
    if re.search(r"POLL+ING\s*DIVISION|^\s*No\s+POLL\b", lines[h]):
        return True
    return bool(re.search(r"\bPOLLING\b", lines[h]) and "DIVISION" in "".join(lines[h + 1 : h + 3]))


def tables(lines: list[str], fallback: tuple[int, str] | None = None) -> list[Table]:
    found: dict[int, Table] = {}
    for h in range(len(lines)):
        if not _header(lines, h):
            continue
        title = None
        for j in range(h - 1, max(h - 12, 0), -1):
            m = TITLE.match(lines[j])
            if m and "Elected" not in lines[j]:
                title = (int(m.group(1)), re.sub(r"\s+", " ", m.group(2)).strip(), j)
                break
        if title is None and fallback is not None:
            title = (fallback[0], fallback[1], h - 3)
        if title is None:
            continue
        end = next((j for j in range(h + 1, min(h + 70, len(lines))) if re.match(r"\s*TOTALS", lines[j])), None)
        if end is None:
            continue
        table = Table(title[0], title[1], h, title[2], totals_line=end)
        table.totals = [n for _, _, n in _spans(lines[end], lines[end].index("TOTALS") + 6)]
        pending_name: tuple[str, str] | None = None
        pending_numbers: list[int] | None = None
        suffix = ""
        for j in range(h + 1, end):
            text = re.sub(r"(?<=\s)-(?=\s|$)", "0", lines[j])
            if re.search(r"%|\d\.\d|PERCENTAGE|Percentage", text):
                continue
            m = ROW.match(text)
            if m and not re.match(r"\s*[\d,\s]+$", text):
                code, name, nums = m.group(1) or "", m.group(2), [int(x.replace(",", "")) for x in m.group(3).split()]
                glued = re.fullmatch(r"(.*\(.*?)\s+(\d[\d,]*)", name)
                if glued:
                    name, nums = glued.group(1), [int(glued.group(2).replace(",", "")), *nums]
                if pending_numbers is not None and len(nums) < 3:
                    nums = pending_numbers + nums
                    pending_numbers = None
                table.rows.append(Row(code.replace(" ", ""), (name + " " + suffix).strip(), nums))
                suffix = ""
                pending_name = None
                continue
            m = NUMBERS_ONLY.match(text)
            if m:
                nums = [int(x.replace(",", "")) for x in m.group(1).split()]
                if pending_name is not None:
                    table.rows.append(Row(pending_name[0], (pending_name[1] + " " + suffix).strip(), nums))
                    pending_name = None
                    suffix = ""
                else:
                    pending_numbers = nums
                continue
            m = CODED.match(text)
            if m:
                if pending_numbers is not None:
                    table.rows.append(Row(m.group(1).replace(" ", ""), m.group(2), pending_numbers))
                    pending_numbers = None
                else:
                    pending_name = (m.group(1).replace(" ", ""), m.group(2))
                continue
            word = text.strip()
            if word and re.fullmatch(r"[A-Za-z][A-Za-z .'’-]*", word) and len(word) < 30:
                suffix = word
        old = found.get(table.number)
        if old is None:
            found[table.number] = table
        elif (old.rows, old.totals) != (table.rows, table.totals):
            raise ValueError(f"district {table.number} is printed twice with different tables")
    return [found[n] for n in sorted(found)]


@functools.cache
def general_text(year: int) -> list[str]:
    return pdf_text(GENERAL_REPORTS[year])


PARTY_CODES: Final = {
    "PC": "pc",
    "LIB": "lib",
    "LIBERAL": "lib",
    "NDP": "ndp",
    "NPD": "ndp",
    "GR": "green",
    "GP": "green",
    "GREEN": "green",
    "ISL": "island",
    "IP": "island",
    "ISLAND": "island",
    "IND": "ind",
    "INDEPENDENT": "ind",
}


def party_code(label: str) -> str:
    code = PARTY_CODES.get(label.strip("() ").upper())
    if code is None:
        raise ValueError(f"unknown party label {label!r}")
    return code


@dataclass
class Candidate:
    surname: str
    given: str
    party: str


@dataclass
class Poll:
    code: str
    name: str
    electors: int | None
    cast: int | None
    votes: list[int]
    rejected: int


@dataclass
class District:
    number: int
    name: str
    candidates: list[Candidate]
    polls: list[Poll]
    totals: Poll
    notes: list[str] = field(default_factory=list)


def _poll(code: str, name: str, nums: list[int], layout: str, k: int, advance: bool) -> Poll:
    """Read one printed row. `layout` names the columns: e electors, c ballots cast, then k candidates and the
    rejected ballots. The advance row has no electors, and a blank rejected cell is a zero."""
    columns = [*layout, *("v" * k), "r"]
    if advance:
        columns = [c for c in columns if c != "e"]
    if len(nums) == len(columns) - 1:
        nums = [*nums, 0]
    if len(nums) != len(columns):
        raise ValueError(f"{code} {name}: {len(nums)} numbers for columns {''.join(columns)}")
    got = dict.fromkeys("ecr")
    votes: list[int] = []
    for column, value in zip(columns, nums, strict=True):
        if column == "v":
            votes.append(value)
        else:
            got[column] = value
    return Poll(code, name, got["e"], got["c"], votes, got["r"] or 0)


def build(table: Table, candidates: list[Candidate], layout: str) -> District:
    k = len(candidates)
    expected = len(layout) + k + 1
    totals = table.totals
    if len(totals) == expected + 1 and totals[-2] == 0:
        totals = totals[:-2] + totals[-1:]
    polls = [_poll(row.code, row.name, row.numbers, layout, k, "dvance" in row.name) for row in table.rows]
    return District(table.number, table.name, candidates, polls, _poll("", "Total", totals, layout, k, False))


GIVEN_STOP: Final = {
    "Number",
    "of",
    "Ballots",
    "Cast",
    "Electors",
    "Rejected",
    "Percentage",
    "No.",
    "Returning",
    "BALLOTS",
    "CAST",
    "FOR",
    "CANDIDATES",
    "ELECTORS",
    "CONFIRMED",
}


def _titlecase(word: str) -> str:
    out = []
    for part in re.split(r"([-'’ ])", word.lower()):
        if part.startswith("mc") and len(part) > 2:
            part = "Mc" + part[2:].capitalize()
        elif part.startswith("mac") and len(part) > 4:
            part = "Mac" + part[3:].capitalize()
        else:
            part = part.capitalize()
        out.append(part)
    return "".join(out)


def header_candidates(lines: list[str], table: Table, layout: str, stacked: bool = False) -> list[Candidate]:
    """Read the candidates from the header of a table: a party, a given name, and a surname over each column of
    votes, placed by the columns of the totals row."""
    k = len(table.totals) - len(layout) - 1
    spans = _spans(lines[table.totals_line], lines[table.totals_line].index("TOTALS") + 6)
    centers = [(a + b) / 2 for a, b, _ in spans]
    voting = centers[len(layout) : len(layout) + k]
    parties: list[list[str]] = [[] for _ in voting]
    surnames: list[list[tuple[int, str]]] = [[] for _ in voting]
    givens: list[list[str]] = [[] for _ in voting]
    for j in range(table.title_line + 1, table.header + 5):
        text = re.sub(r"\(\s*([A-Za-z]+)\s*\)", r"(\1)", lines[j])
        if re.search(r"\d", text) or "Returning" in text:
            continue
        for m in re.finditer(r"\S+", text):
            word = m.group().rstrip(",")
            if word in "()":
                continue
            c = (m.start() + m.end()) / 2
            distance = [abs(c - x) for x in centers]
            nearest = min(range(len(centers)), key=lambda i: distance[i])
            if not (len(layout) <= nearest < len(layout) + k) or distance[nearest] > 13 or word in GIVEN_STOP:
                continue
            i = nearest - len(layout)
            bare = word.strip("()")
            if bare.upper() in PARTY_CODES and (word.startswith("(") or bare.isupper() and len(bare) <= 3):
                parties[i].append(bare)
            elif stacked or re.fullmatch(r"[A-ZÀ-Þ'’-]{2,}", bare):
                surnames[i].append((j, word))
            else:
                givens[i].append(word)
    if stacked:
        # The given names stand above the surnames, in capitals: the last printed line of each column is the surname.
        for i in range(k):
            if surnames[i]:
                last = max(j for j, _ in surnames[i])
                givens[i] = [w for j, w in surnames[i] if j != last]
                surnames[i] = [(j, w) for j, w in surnames[i] if j == last]
    out = []
    for i in range(k):
        if len(parties[i]) != 1 or not surnames[i] or not givens[i]:
            raise ValueError(
                f"district {table.number}: cannot read candidate {i + 1}: {parties[i]} {surnames[i]} {givens[i]}"
            )
        surname = " ".join(_titlecase(w) for _, w in surnames[i])
        given = " ".join(_titlecase(w) if stacked else w for w in givens[i]).replace("- ", "-")
        out.append(Candidate(surname, given, party_code(parties[i][0])))
    return out


# Headers the column placement cannot read: names set so close that two share a column, or a surname printed in
# lower case or a given name in capitals.
HEADERS: Final[dict[tuple[int, int], list[Candidate]]] = {
    (2015, 19): [
        Candidate("Fox", "Jamie", "pc"),
        Candidate("Macfarlane", "Ranald", "green"),
        Candidate("Roberts", "Ramona", "lib"),
        Candidate("Tweten", "Aleida", "ndp"),
    ],
    (2019, 4): [
        Candidate("Compton", "Darlene", "pc"),
        Candidate("Macpherson", "Ian", "lib"),
        Candidate("Sanders", "James", "green"),
    ],
    (2019, 12): [
        Candidate("Bernard", "Karla", "green"),
        Candidate("Brown", "Richard", "lib"),
        Candidate("Byrne", "Joe", "ndp"),
        Candidate("Keizer", "Tim", "pc"),
    ],
    (2019, 23): [
        Candidate("Altass", "Trish", "green"),
        Candidate("Biggar", "Paula", "lib"),
        Candidate("Enman", "“Robin” John Robert", "ndp"),
        Candidate("MacLennan", "Hilton A.", "pc"),
    ],
    (2023, 21): [
        Candidate("Desroches", "Tyler", "pc"),
        Candidate("Lund", "Lynne", "green"),
        Candidate("MacKay", "Cassie", "ndp"),
        Candidate("O'Reilly", "Eriena", "island"),
        Candidate("Reid", "Don", "lib"),
    ],
}


CANDIDATE: Final = re.compile(r"^(.+?),\s*(.+?)\s*\(+(\w+)\)$")
DISTRICT: Final = re.compile(r"^No\.\s*(\d+)$")


@dataclass
class Summary:
    number: int
    name: str
    electors: int
    cast: int
    rejected: int
    candidates: list[Candidate] = field(default_factory=list)
    votes: list[int] = field(default_factory=list)


def _int(text: str) -> int:
    return int(text.replace(",", ""))


def summaries(lines: list[str]) -> dict[int, Summary]:
    """The declaration-day results of the reports of 2003 to 2011, which list each district's candidates in ballot
    order with their party and votes."""
    start = next(i for i, line in enumerate(lines) if "DECLARATION DAY RESULTS" in line)
    out: dict[int, Summary] = {}
    current: Summary | None = None
    named = True
    stop = max(i for i, line in enumerate(lines) if "DECLARATION DAY RESULTS" in line) + 70
    for i in range(start, min(stop, len(lines))):
        parts = re.split(r"\s{2,}", lines[i].strip())
        if not parts or not parts[0]:
            continue
        number = None
        header = DISTRICT.match(parts[0])
        if header:
            number = int(header.group(1))
            parts = parts[1:]
        elif current is not None and not named and not CANDIDATE.match(parts[0]) and len(parts) > 1:
            current.name = parts[0]
            named = True
            parts = parts[1:]
        elif current is not None and not named and not CANDIDATE.match(parts[0]) and len(parts) == 1:
            current.name = parts[0]
            named = True
            continue
        elif (
            current is not None
            and named
            and len(parts) > 1
            and not CANDIDATE.match(parts[0])
            and CANDIDATE.match(parts[1])
            and current.candidates
        ):
            parts = parts[1:]
        if not parts:
            continue
        m = CANDIDATE.match(parts[0])
        if m is None:
            if number is not None:
                current = None
            continue
        numbers = [x for part in parts[1:] for x in part.split() if re.fullmatch(r"[\d,]+(?:\.\d+)?", x)]
        if number is not None:
            if len(numbers) < 7:
                continue
            current = Summary(number, "", _int(numbers[0]), _int(numbers[1]), _int(numbers[3]))
            out[number] = current
            named = False
            votes = _int(numbers[5])
        elif current is not None and numbers:
            votes = _int(numbers[0])
        else:
            continue
        assert current is not None
        current.candidates.append(Candidate(m.group(1).strip(), m.group(2).strip(), party_code(m.group(3))))
        current.votes.append(votes)
    return out


# Layouts of the poll tables: e electors, c ballots cast, then the candidates and the rejected ballots.
LAYOUTS: Final = {2003: "e", 2007: "e", 2011: "e", 2015: "ec", 2019: "ec", 2023: "c"}
# Misprints of the declaration-day lists, as the poll tables spell them.
SPELLINGS: Final = {(2003, 16, "Bulter"): "Butler"}


def general(year: int) -> list[tuple[District, date | None]]:
    """The districts of one general election, each with the date of its deferred poll if its candidate died."""
    lines = general_text(year)
    layout = LAYOUTS[year]
    found = summaries(lines) if year <= 2011 else {}
    out: list[tuple[District, date | None]] = []
    for table in tables(lines):
        key = (year, table.number)
        if key in HEADERS:
            candidates = HEADERS[key]
        elif table.number in found:
            candidates = [
                Candidate(SPELLINGS.get((year, table.number, c.surname), c.surname), c.given, c.party)
                for c in found[table.number].candidates
            ]
        else:
            candidates = header_candidates(lines, table, layout, stacked=year <= 2011)
        deferred = None
        if year == 2019 and table.number == 9:
            deferred = date(2019, 7, 15)
        out.append((build(table, candidates, layout), deferred))
    return out


HISTORY: Final[dict[int, str]] = {}
HISTORY.update(
    {
        year: TEST + f"{year}Report.pdf"
        for year in (
            1900,
            1904,
            1908,
            1912,
            1915,
            1919,
            1923,
            1927,
            1931,
            1935,
            1939,
            1943,
            1947,
            1951,
            1955,
            1959,
            1962,
            1966,
            1970,
            1974,
            1978,
            1979,
            1982,
            1986,
            1989,
            1993,
            1996,
            2000,
            2003,
            2007,
            2011,
        )
    }
)
MONTHS: Final = {
    m: i + 1
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
    )
}
LEGEND_PARTIES: Final = {
    "LIB": "lib",
    "CON": "pc",
    "PC": "pc",
    "NDP": "ndp",
    "IND": "ind",
    "PRO": "pro",
    "CCF": "ccf",
    "IL": "ind",
    "DBP": "dbp",
}


@dataclass
class Entry:
    party: str
    label: str
    name: str
    votes: int | None
    elected: bool


@dataclass
class Seat:
    """One seat's candidates in one district, as the historical results print them."""

    district: str
    seat: str
    entries: list[Entry] = field(default_factory=list)


@dataclass
class Results:
    year: int
    day: date
    seats: list[Seat]


ENTRY: Final = re.compile(r"\((?P<party>[A-Z]+)\)\s+(?P<name>\S.*?)\s{2,}(?P<votes>[\d,]+|\(acc\))(?P<e>\s+\(e\))?")


def history_text(year: int) -> list[str]:
    return pdf_text(HISTORY[year])


def election_day(lines: list[str]) -> date:
    m = re.search(r"Election Date:\s+(?:\w+day\s+)?(\d{1,2}) (\w+) (\d{4})", "\n".join(lines[:4]))
    if m is None:
        raise ValueError("no election date")
    return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))


def district_label(text: str) -> str:
    """Charlottetown's seats are the 5th Queens from 1939 on, and printed as Charlottetown before."""
    text = re.sub(r"\s+", " ", text).strip()
    return "5th Queens" if text.startswith("Charlottetown") else text


def dual(year: int, lines: list[str]) -> list[Seat]:
    """The councillor and assemblyman of each district, in the two columns of the results of 1900 to 1993."""
    header = next(line for line in lines if "Councillor" in line)
    split = header.index("Votes") + 4
    out: list[Seat] = []
    district = ""
    for line in lines[lines.index(header) + 1 :]:
        if line.lstrip().startswith(("File:", "Prepared")):
            break
        entries = list(ENTRY.finditer(line))
        left = (line[: entries[0].start()] if entries else line[:30]).strip()
        if left and re.match(r"\d(?:st|nd|rd|th) |Charlottetown", left):
            district = district_label(left)
        for m in entries:
            seat = "councillor" if m.start() < split else "assemblyman"
            current = next((s for s in out if s.district == district and s.seat == seat), None)
            if current is None:
                current = Seat(district, seat)
                out.append(current)
            acclaimed = m.group("votes") == "(acc)"
            current.entries.append(
                Entry(
                    LEGEND_PARTIES[m.group("party")],
                    m.group("party"),
                    re.sub(r"\s+", " ", m.group("name")).strip(),
                    None if acclaimed else _int(m.group("votes")),
                    bool(m.group("e")),
                )
            )
    return out


def fetch() -> None:
    for url in (*GENERAL_REPORTS.values(), *HISTORY.values(), *BY_REPORTS.values(), *BY_RESULTS.values()):
        raw.fetch(SOURCE, url, suffix=".pdf")


PARTY_COLUMNS: Final = {
    "Island New Democrats": "ndp",
    "Progressive Conservative": "pc",
    "Liberal": "lib",
    "Independent": "ind",
}


def columns(year: int, lines: list[str]) -> list[Seat]:
    """The member of each district in the party columns of the results of 1996 and 2000: a line of names and a
    line of votes for each district."""
    index = next(i for i, line in enumerate(lines) if line.startswith("District Number"))
    header = lines[index]
    anchors = sorted((header.index(name), code, name) for name, code in PARTY_COLUMNS.items() if name in header)
    out: list[Seat] = []
    i = index + 1
    while i < len(lines) - 1:
        m = re.match(r"No\.\s*(\d+)\s+(.*)$", lines[i])
        if m is None:
            i += 1
            continue
        names: list[tuple[int, str, bool]] = []
        for found in re.finditer(r"\(e\)|(?:(?!\(e\))\S)(?:(?!\s{2,}|\(e\)).)*", lines[i][m.start(2) :]):
            if found.group() == "(e)":
                position, name, _ = names[-1]
                names[-1] = (position, name, True)
            else:
                names.append((m.start(2) + found.start(), found.group().strip(), False))
        district_line = lines[i + 1]
        label = re.sub(r"\s+", " ", re.split(r"\s{2,}|\s\d", district_line.strip())[0])
        numbers = [(f.start(), _int(f.group())) for f in re.finditer(r"[\d,]+", district_line[30:])]
        if len(numbers) != len(names):
            raise ValueError(f"{year} district {m.group(1)}: {len(names)} names but {len(numbers)} counts")
        seat = Seat(f"{int(m.group(1)):02d} {label}", "member")
        for (position, name, elected), (_, votes) in zip(names, numbers, strict=True):
            column = min(anchors, key=lambda a: abs(a[0] - position))
            seat.entries.append(Entry(column[1], column[2], name, votes, elected))
        out.append(seat)
        i += 2
    return out


# ----------------------------------------------------------------------------------------------------------------
# Packages

GENERAL_DAYS_FROM_REPORT: Final = {2015: date(2015, 5, 4), 2019: date(2019, 4, 23), 2023: date(2023, 4, 3)}

PEI: Final = "01a13fb6-eab7-7501-a0f9-4900c883e563"
PROVINCE_AREA: Final = "01a13fb6-eab7-7501-a0f9-48f30c015af1"
RESULTS_PAGE: Final = "https://www.electionspei.ca/historical-election-data-pdf"
REPORTS_PAGE: Final = "https://www.electionspei.ca/resources/ceo-election-reports"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


EP: Final = key("source", "elections-pei")
ASSEMBLY: Final = key("body", "legislative-assembly")
FPTP: Final = key("electoral_system", "fptp")
OFFICES: Final = {"councillor": key("office", "councillor"), "assemblyman": key("office", "assemblyman")}
OFFICE_NAMES: Final = {"councillor": "Councillor", "assemblyman": "Assemblyman"}

# The district plans: the sixteen two-member districts of the counties, then three plans of twenty-seven districts.
PLANS: Final = {
    "counties": (None, date(1996, 11, 18)),
    "1996": (date(1996, 11, 18), date(2007, 5, 28)),
    "2007": (date(2007, 5, 28), date(2019, 4, 23)),
    "2019": (date(2019, 4, 23), None),
}
COUNTY_CODES: Final = {
    f"{n}{suffix} {county}": f"{county[0]}{n}"
    for county in ("Kings", "Queens", "Prince")
    for n, suffix in ((1, "st"), (2, "nd"), (3, "rd"), (4, "th"), (5, "th"), (6, "th"))
}
SIXTH_QUEENS: Final = date(1966, 5, 30)
PARTIES: Final = {
    "lib": ("Liberal Party of Prince Edward Island", "Liberal", "LIB"),
    "pc": ("Progressive Conservative Party of Prince Edward Island", "Progressive Conservative", "PC"),
    "ndp": ("New Democratic Party of Prince Edward Island", "NDP", "NDP"),
    "green": ("Green Party of Prince Edward Island", "Green", "GP"),
    "island": ("Island Party of Prince Edward Island", "Island Party", "ISL"),
    "ccf": ("Co-operative Commonwealth Federation", "CCF", "CCF"),
    "pro": ("Progressive Party", "Progressive", "PRO"),
    "dbp": ("Draft Beer Party", "Draft Beer Party", "DBP"),
    "garden": ("Garden Party", "Garden Party", "GARDEN"),
}
DEFERRED: Final = {(1966, "1st Kings"): date(1966, 7, 11), (2019, "09"): date(2019, 7, 15)}


@dataclass
class Contest:
    year: int
    day: date
    plan: str
    code: str
    district: str
    seat: str
    entries: list[Entry]
    polls: list[Poll] | None = None
    totals: Poll | None = None
    sources: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)


def plan_of(day: date) -> str:
    return next(
        plan for plan, (start, end) in PLANS.items() if (start is None or day >= start) and (end is None or day < end)
    )


@functools.cache
def district_names() -> dict[tuple[str, str], str]:
    """The name of each district of the plans of 27 districts, as the plan's first election prints it."""
    names: dict[tuple[str, str], str] = {}
    for seat in columns(1996, history_text(1996)):
        code, _, label = seat.district.partition(" ")
        names["1996", code] = label
    for plan, year in (("2007", 2007), ("2019", 2019)):
        for d, _ in general(year):
            names[plan, f"{d.number:02d}"] = d.name
    return names


HISTORY_NOTES: Final = {
    (1904, "2nd Kings", "assemblyman"): (
        "Arthur Peters and Harvey D. McEwen each received 515 votes, so no candidate is marked as elected. The "
        "authority's note says a writ was issued on 25 February 1905 for a by-election, in which Arthur Peters was "
        "elected by acclamation; that by-election is not stored."
    ),
    (1966, "1st Kings", "councillor"): "The poll was deferred to 11 July 1966 because a candidate died.",
    (1966, "1st Kings", "assemblyman"): "The poll was deferred to 11 July 1966 because a candidate died.",
}


def settle(c: Contest) -> Contest:
    """Note a seat where the page marks as elected a candidate with fewer votes than another. The mark is stored as
    declared."""
    note = HISTORY_NOTES.get((c.year, c.district, c.seat))
    if note:
        c.notes.append(note)
    voted = [e for e in c.entries if e.votes is not None]
    elected = [e for e in c.entries if e.elected]
    if voted and len(elected) == 1:
        top = max(voted, key=lambda e: e.votes or 0)
        if elected[0] is not top and sum(e.votes == top.votes for e in voted) == 1:
            c.notes.append(
                f"The authority's page marks {elected[0].name} as elected, but {top.name} received more votes "
                f"({top.votes} to {elected[0].votes}), and the party standings it prints count one more seat for "
                f"{top.name}'s party than the candidates it marks as elected. The mark is stored as printed."
            )
    return c


def historical(year: int) -> list[Contest]:
    lines = history_text(year)
    day = election_day(lines)
    out: list[Contest] = []
    if year >= 1996:
        seats = columns(year, lines)
        for seat in seats:
            code, _, label = seat.district.partition(" ")
            out.append(
                settle(Contest(year, day, "1996", code, label, "member", seat.entries, sources=(HISTORY[year],)))
            )
    else:
        for seat in dual(year, lines):
            when = DEFERRED.get((year, seat.district), day)
            out.append(
                settle(
                    Contest(
                        year,
                        when,
                        "counties",
                        COUNTY_CODES[seat.district],
                        seat.district,
                        seat.seat,
                        seat.entries,
                        sources=(HISTORY[year],),
                    )
                )
            )
    return out


def modern(year: int) -> list[Contest]:
    out: list[Contest] = []
    plan = {2003: "1996", 2007: "2007", 2011: "2007", 2015: "2007", 2019: "2019", 2023: "2019"}[year]
    day = election_day(history_text(year)) if year in HISTORY else GENERAL_DAYS_FROM_REPORT[year]
    for d, deferred in general(year):
        entries = [
            Entry(c.party, c.party.upper(), f"{c.given} {c.surname}", votes, False)
            for c, votes in zip(d.candidates, d.totals.votes, strict=True)
        ]
        top = max(e.votes or 0 for e in entries)
        if sum(e.votes == top for e in entries) == 1:
            for e in entries:
                e.elected = e.votes == top
        when = deferred or day
        assert when is not None
        out.append(
            Contest(
                year,
                when,
                plan,
                f"{d.number:02d}",
                d.name,
                "member",
                entries,
                d.polls,
                d.totals,
                (GENERAL_REPORTS[year],),
            )
        )
    return out


def years() -> list[int]:
    return sorted({*(y for y in HISTORY if y >= 1900), *GENERAL_REPORTS})


def contests(year: int) -> list[Contest]:
    return modern(year) if year in GENERAL_REPORTS else historical(year)


def area(plan: str, code: str) -> str:
    return key("area", plan, code)


def district_unit(plan: str, code: str) -> str:
    return key("unit", "district", plan, code)


def by_days() -> list[date]:
    return sorted({day for day, *_ in BY_ELECTIONS})


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/ge-{year}" for year in years()),
        *(f"{SOURCE}/be-{day}" for day in by_days()),
    ]


def _county_names() -> dict[str, str]:
    return {code: label for label, code in COUNTY_CODES.items()}


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Prince Edward Island's electoral districts: the sixteen two-member districts of the counties, and the three "
        "plans of twenty-seven districts from 1996, 2007, and 2019, with a reporting unit for each district's total",
    )
    p.external |= {PEI, PROVINCE_AREA}
    p.add(
        "scheme",
        code="ca.pei",
        polity_id=PEI,
        kind="electoral_district",
        rank=1,
        noun="electoral district",
        code_system=None,
    )
    p.add(
        "scheme", code="ca.pei_poll", polity_id=PEI, kind="precinct", rank=2, noun="polling division", code_system=None
    )
    for plan, (start, end) in PLANS.items():
        p.add(
            "boundary_set",
            id=key("boundary_set", plan),
            scheme_code="ca.pei",
            vintage=plan,
            valid_from=start,
            valid_to=end,
            as_of=start or date(1900, 12, 12),
        )
        if plan == "counties":
            items = [(code, label) for code, label in _county_names().items() if code != "P6" and code != "K6"]
        else:
            items = sorted((code, name) for (pl, code), name in district_names().items() if pl == plan)
        for code, name in items:
            if plan == "counties" and code == "Q6":
                since: date | None = SIXTH_QUEENS
            else:
                since = start
            id = area(plan, code)
            p.add("area", id=id, scheme_code="ca.pei")
            p.add("area_code", area_id=id, code=code, valid_from=since, valid_to=end)
            p.add("area_parent", area_id=id, parent_id=PROVINCE_AREA, valid_from=since, valid_to=end)
            p.name(id, name, "en", "official")
            unit = district_unit(plan, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=PEI,
                scheme_code="ca.pei",
                code=f"{plan}:{code}",
                area_relation="equals",
                area_id=id,
                kind="standard",
                valid_from=since,
                valid_to=end,
            )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Prince Edward Island's Legislative Assembly, its offices, parties, and single-member plurality system, from "
        "Elections PEI",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {PEI}
    p.add(
        "source",
        id=EP,
        kind="official_body",
        publisher="Elections PEI",
        title="Historical election results and reports of the Chief Electoral Officer",
        url=RESULTS_PAGE,
        license=PUBLIC_RECORD,
        license_url=None,
        attribution="Elections PEI",
    )
    for url in (*GENERAL_REPORTS.values(), *HISTORY.values(), *BY_REPORTS.values(), *BY_RESULTS.values()):
        p.artifact(raw.cached(SOURCE, url), EP)
    p.add("body", id=ASSEMBLY, polity_id=PEI, kind="unicameral")
    p.name(ASSEMBLY, "Legislative Assembly of Prince Edward Island", "en", "official")
    for seat, id in OFFICES.items():
        p.add("office", id=id, polity_id=PEI, body_id=ASSEMBLY, kind="other")
        p.name(id, OFFICE_NAMES[seat], "en", "official")
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=PEI,
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
        body_md="Each seat is won by the candidate with the most votes. Until 1996 each district elected two members, a "
        "councillor and an assemblyman, each in a separate vote for one seat.",
        lang="en",
    )
    used = {e.party for c in all_contests() for e in c.entries if e.party not in ("ind", "")}
    for code in sorted(used):
        official, short, abbreviation = PARTIES[code]
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=PEI)
        p.add("external_id", entity_id=id, scheme="ca.pei.party", value=code)
        p.name(id, official, "en", "official")
        p.name(id, short, "en", "short", False)
        p.name(id, abbreviation, "en", "abbreviation", False)
    return p


GRAIN_NOTE: Final = (
    "Elections PEI prints each polling division's electors on the final list (except for the advance poll), the votes "
    "each candidate received, and the rejected ballots, and a total for the district. Advance polls, mobile and "
    "hospital polls, and mail-in ballots are reported for the district as a whole and stored as an aggregate unit. "
    "Valid votes are the sum of the candidates' votes."
)
DISTRICT_NOTE: Final = (
    "Elections PEI prints each candidate's votes for the district as a whole; no count by polling division is "
    "printed for this election."
)


def poll_unit(day: date, plan: str, code: str, poll: str) -> str:
    return key("unit", "poll", day, plan, code, poll)


def _poll_code(poll: Poll) -> str:
    return "advance" if "dvance" in poll.name else poll.code


def _contest(p: Package, election: str, path: str, c: Contest) -> tuple[str, bool]:
    plan_area = area(c.plan, c.code)
    contest = key("contest", c.day, c.plan, c.code, c.seat)
    office = OFFICES.get(c.seat)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        office_id=office,
        body_id=ASSEMBLY,
        district_area_id=plan_area,
        magnitude=1,
        round_number=1,
        electoral_system_id=FPTP,
        default_result_kind="counted",
        boundary_set_id=key("boundary_set", c.plan),
    )
    label = c.district if c.seat == "member" else f"{c.district}, {OFFICE_NAMES[c.seat]}"
    p.name(contest, label, "en", "official")
    p.add("slug", path=f"{path}/{changeset.slug(label)}", entity_id=contest, is_current=True)
    result_set = key("result_set", c.day, c.plan, c.code, c.seat)
    acclaimed = all(e.votes is None for e in c.entries)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if acclaimed else "counted",
        source_id=EP,
        status="active",
        is_default=True,
        completeness=None if acclaimed or any(e.votes is None for e in c.entries) else 1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=DISTRICT_NOTE if c.polls is None else GRAIN_NOTE,
        lang="en",
    )
    for text in c.notes:
        p.add("note", id=key("note", result_set, text), entity_id=result_set, kind="caveat", body_md=text, lang="en")
    options = []
    winners = [i for i, e in enumerate(c.entries) if e.elected]
    for index, entry in enumerate(c.entries):
        tag = f"{c.day}:{c.plan}:{c.code}:{c.seat}:{index + 1}"
        option = key("option", tag)
        person = key("person", tag)
        options.append(option)
        party = None if entry.party in ("ind", "") else key("party", entry.party)
        p.add("person", id=person)
        p.name(person, entry.name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_position=index + 1,
            ballot_label=entry.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=entry.name,
            member_of_party_id=party,
            is_independent=entry.party == "ind",
        )
        if winners:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if index in winners else "lost")
    if c.polls is None:
        unit = district_unit(c.plan, c.code)
        for option, entry in zip(options, c.entries, strict=True):
            if entry.votes is not None:
                _value(p, result_set, unit, option, 1, entry.votes)
    else:
        assert c.totals is not None
        for option, votes in zip(options, c.totals.votes, strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=votes)
        for poll in c.polls:
            code = _poll_code(poll)
            unit = poll_unit(c.day, c.plan, c.code, code)
            aggregate = code == "advance"
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=PEI,
                scheme_code="ca.pei_poll",
                code=f"{c.day}:{c.code}:{code}",
                area_relation="equals" if aggregate else "within",
                area_id=plan_area,
                kind="aggregate" if aggregate else "standard",
            )
            for measure, value in ((2, poll.electors), (3, poll.cast), (4, sum(poll.votes)), (5, poll.rejected)):
                if value is not None:
                    _value(p, result_set, unit, None, measure, value)
            for option, votes in zip(options, poll.votes, strict=True):
                _value(p, result_set, unit, option, 1, votes)
    return result_set, c.polls is not None


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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


def election(year: int) -> Package:
    items = contests(year)
    p = Package(
        f"{SOURCE}/ge-{year}",
        SOURCE,
        f"The {year} Prince Edward Island general election, from Elections PEI",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PEI, PROVINCE_AREA}
    for url in sorted({url for c in items for url in c.sources}):
        p.uses(raw.cached(SOURCE, url))
    days = sorted({c.day for c in items})
    main = days[0] if len(days) == 1 else max(days, key=lambda d: sum(c.day == d for c in items))
    for day in days:
        election_id = key("election", day)
        path = f"ca/pei/{day}-general"
        rows = [c for c in items if c.day == day]
        p.add("election", id=election_id, polity_id=PEI, kind="general", date_from=day, date_to=day, status="held")
        p.add("slug", path=path, entity_id=election_id, is_current=True)
        if day == main:
            p.name(election_id, f"{year} Prince Edward Island general election", "en", "official")
        else:
            places = ", ".join(sorted({c.district for c in rows}))
            p.name(
                election_id,
                f"{places} deferred election of the {year} general election, {day:%-d %B %Y}",
                "en",
                "official",
            )
        for c in rows:
            _contest(p, election_id, path, c)
    _accept(p, items)
    return p


def _accept(p: Package, items: list[Contest]) -> None:
    """Accept the findings that come from figures the authority prints inconsistently."""
    rows = [
        (c, poll)
        for c in items
        if c.polls is not None
        for poll in c.polls
        if poll.cast is not None and poll.cast != sum(poll.votes) + poll.rejected
    ]
    if rows:
        p.accept(
            "accounting.ballots_sum",
            f"Elections PEI prints ballots cast that differ from the candidates' votes plus the rejected ballots in "
            f"{len(rows)} polling divisions; stored as printed",
        )
    off = [
        c
        for c in items
        if c.polls is not None
        and c.totals is not None
        and [sum(poll.votes[i] for poll in c.polls) for i in range(len(c.entries))] != c.totals.votes
    ]
    if off:
        p.accept(
            "accounting.declared",
            f"In {len(off)} district(s) the district total Elections PEI prints for a candidate differs from the sum "
            "of the polling divisions it prints; both stored as printed",
        )


def by_election(day: date) -> Package:
    items = [c for c in by_elections() if c.day == day]
    p = Package(
        f"{SOURCE}/be-{day}",
        SOURCE,
        f"The Prince Edward Island by-election of {day}, from Elections PEI",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PEI, PROVINCE_AREA}
    for url in sorted({url for c in items for url in c.sources}):
        p.uses(raw.cached(SOURCE, url))
    election_id = key("election", day, "by")
    path = f"ca/pei/{day}-by-election"
    p.add("election", id=election_id, polity_id=PEI, kind="by_election", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    places = ", ".join(dict.fromkeys(c.district for c in items))
    p.name(election_id, f"{places} by-election, {day:%-d %B %Y}", "en", "official")
    for c in items:
        _contest(p, election_id, path, c)
    _accept(p, items)
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for year in years():
        election(year).write()
    for day in by_days():
        by_election(day).write()


def emit() -> None:
    package.run("load", packages())


BY_REPORTS: Final[dict[str, str]] = {
    "2016": WWW + "EPEI_CEO_2016.pdf",
    "2017": TEST + "2017%20Nov%2027%20By-Election%20District%2011.pdf",
    "2020": WWW + "WebVersion_2020_D10BY-ELECTION.pdf",
    "2021": WWW + "2021_D16BYELECTIONSummaryFINAL.pdf",
    "2024": TEST + "CEOReportDistrict19ByElectionFeb7_2024.pdf",
    "2025-d2": TEST + "2025%20By-Election%20District%202.pdf",
    "2025-d9": TEST + "2025%20By-Election%20District%209%20and%20District%2015.pdf",
    "1975": TEST + "1975%20Feb%2017%20ByElection%205th%20Queens.pdf",
    "1976": TEST + "1976%20Dec%2031%20ByElections_4%20Districts.pdf",
    "1981-02": TEST + "1981%20Feb%202%20ByElection%202nd%20Prince.pdf",
    "1981-07": TEST + "1981%20July%2013%20ByElection%203rd%20Kings.pdf",
}
BY_RESULTS: Final[dict[str, str]] = {
    "2016": TEST + "2016%20ByElection%20Results.pdf",
    "2017": TEST + "2017%20ByElection%20Results.pdf",
    "2020": TEST + "D10ByElectionNov22020.pdf",
    "2021": TEST + "District16ByElection.pdf",
    "2024": TEST + "District19ByElectionFeb72024.pdf",
    "2025-d2": TEST + "D2ByElectionDec82025.pdf",
    "2025-d9": TEST + "D9D15ByElectionAug122025.pdf",
    "2026": TEST + "District16ByElectionJuly132026.pdf",
}


def _e(party: str, name: str, votes: int | None, elected: bool = False) -> Entry:
    return Entry(party, party.upper(), name, votes, elected)


# By-elections, as the authority's results pages and reports print them: the day, the district's code in the plan
# of the day, the seat, the report with the poll table if it has one, and the candidates with their votes. Those of
# 1975 to 1981 are read from the page images of the reports; they print no party for the 1981 candidates.
BY_ELECTIONS: Final[list[tuple[date, str, str, str, str | None, list[Entry]]]] = [
    (
        date(1975, 2, 17),
        "counties",
        "Q5",
        "assemblyman",
        "1975",
        [
            _e("ndp", "Madrien Ferris", 735),
            _e("pc", "James Lee", 2343, True),
            _e("lib", "David McLane", 2250),
            _e("garden", "Susan Partridge", 53),
        ],
    ),
    (
        date(1976, 11, 8),
        "counties",
        "P2",
        "councillor",
        "1976",
        [_e("pc", "Dr. George L. Dewar", 1165, True), _e("lib", "Robert Allison Ellis", 1150)],
    ),
    (
        date(1976, 11, 8),
        "counties",
        "P5",
        "assemblyman",
        "1976",
        [_e("pc", "George McMahon", 2131, True), _e("lib", "George Olscamp", 1311)],
    ),
    (
        date(1976, 11, 8),
        "counties",
        "Q4",
        "assemblyman",
        "1976",
        [_e("lib", "John Brehaut", 1005), _e("pc", "J. Angus MacLean", 1257, True)],
    ),
    (
        date(1976, 11, 8),
        "counties",
        "K1",
        "councillor",
        "1976",
        [_e("lib", "James Fay", 1472, True), _e("pc", "Reg Peters", 1045)],
    ),
    (
        date(1981, 2, 2),
        "counties",
        "P2",
        "assemblyman",
        "1981-02",
        [_e("", "James Matheson", 1055), _e("", "Keith Milligan", 1405, True)],
    ),
    (
        date(1981, 7, 13),
        "counties",
        "K3",
        "assemblyman",
        "1981-07",
        [_e("", "Paul DesRoches", 980), _e("", 'A.A. "Joey" Fraser', 1293, True)],
    ),
    (
        date(2016, 10, 17),
        "2007",
        "21",
        "member",
        "2016",
        [
            _e("green", "Lynn Lund", 506),
            _e("ndp", "Scott Gaudet", 107),
            _e("lib", "Chris Palmer", 978, True),
            _e("pc", "Brian Ramsay", 720),
        ],
    ),
    (
        date(2017, 11, 27),
        "2007",
        "11",
        "member",
        "2017",
        [
            _e("green", "Hannah Bell", 768, True),
            _e("ndp", "Mike Redmond", 202),
            _e("lib", "Bob Doiron", 621),
            _e("pc", "Melissa Hilton", 586),
        ],
    ),
    (
        date(2020, 11, 2),
        "2019",
        "10",
        "member",
        "2020",
        [
            _e("green", "Chris van Ouwerkerk", 783),
            _e("ndp", "Lynne Thiele", 37),
            _e("lib", "Zac Murphy", 636),
            _e("pc", "Zack Bell", 1402, True),
        ],
    ),
    (
        date(2021, 11, 15),
        "2019",
        "16",
        "member",
        "2021",
        [
            _e("ndp", "Larry Hale", 83),
            _e("lib", "Jane MacIsaac", 815),
            _e("green", "Todd McLean", 574),
            _e("pc", "Mark McLane", 982, True),
        ],
    ),
    (
        date(2024, 2, 7),
        "2019",
        "19",
        "member",
        "2024",
        [
            _e("green", "Matt MacFarlane", 1226, True),
            _e("lib", "Gordon Sobey", 272),
            _e("ndp", "Karen Morton", 40),
            _e("pc", "Carmen Reeves", 964),
        ],
    ),
    (
        date(2025, 8, 12),
        "2019",
        "09",
        "member",
        "2025-d9",
        [
            _e("green", "Janine Karpakis", 231),
            _e("lib", "Carolyn Simpson", 979, True),
            _e("ndp", "Simone Webster", 55),
            _e("pc", "Dennis Jameson", 784),
        ],
    ),
    (
        date(2025, 8, 12),
        "2019",
        "15",
        "member",
        "2025-d9",
        [
            _e("green", "Philip Hamming", 538),
            _e("lib", "Nicole Ford", 527),
            _e("ndp", "Michelle Neill", 63),
            _e("pc", "Kent Dollar", 1140, True),
        ],
    ),
    (
        date(2025, 12, 8),
        "2019",
        "02",
        "member",
        "2025-d2",
        [
            _e("green", "Eddie Childs", 200),
            _e("lib", "Robert Mitchell", 881),
            _e("ndp", "Kevin Trainor", 40),
            _e("pc", "Brendan J. Curran", 986, True),
        ],
    ),
    (
        date(2026, 7, 13),
        "2019",
        "16",
        "member",
        None,
        [
            _e("green", "Tayte Willows", 1184, True),
            _e("lib", "Robert Mitchell", 955),
            _e("ndp", "Craig Nash", 24),
            _e("pc", "Caley McDonald", None),
        ],
    ),
]
FALLBACK_TITLES: Final = {"2017": (11, "Charlottetown-Parkdale"), "2020": (10, "Charlottetown-Winsloe")}
LAYOUT_BY: Final = {"2016": "ec", "2017": "ec", "2020": "ec", "2024": "c", "2025-d9": "ec"}


def _by_table(key: str, number: int) -> Table | None:
    lines = pdf_text(BY_REPORTS[key])
    return next((t for t in tables(lines, FALLBACK_TITLES.get(key)) if t.number == number), None)


def _polls_of(key: str, number: int, entries: list[Entry]) -> tuple[list[Poll], Poll] | None:
    """The polling divisions of a by-election, with each column of votes put in the order of the candidates by
    matching its total to theirs."""
    table = _by_table(key, number)
    if table is None:
        return None
    if key == "2025-d2":
        polls = []
        for row in table.rows:
            nums = row.numbers
            if len(nums) == 6:
                nums = [*nums, nums[-1]]
            cast, votes, valid = nums[0], nums[1:5], nums[5]
            polls.append(Poll(row.code, row.name, None, cast, votes, max(cast - valid, 0)))
        totals = Poll("", "Total", None, table.totals[0], table.totals[1:5], table.totals[6])
        for poll in polls:
            if "dvance" in poll.name:
                poll.code = ""
    else:
        k = len(table.totals) - len(LAYOUT_BY[key]) - 1
        district = build(table, [Candidate("", "", "ind")] * k, LAYOUT_BY[key])
        polls, totals = district.polls, district.totals
    order = []
    for entry in entries:
        found = [i for i, v in enumerate(totals.votes) if v == entry.votes]
        if len(found) != 1:
            raise ValueError(f"{key}: cannot place {entry.name} among {totals.votes}")
        order.append(found[0])
    for poll in [*polls, totals]:
        poll.votes = [poll.votes[i] for i in order]
    return polls, totals


def by_elections() -> list[Contest]:
    out: list[Contest] = []
    for day, plan, code, seat, key_, entries in BY_ELECTIONS:
        if plan == "counties":
            label = _county_names()[code]
        else:
            label = district_names()[plan, code]
        polls = totals = None
        sources = [BY_REPORTS[key_]] if key_ else []
        number = int(code) if code.isdigit() else 0
        if plan != "counties" and key_ and key_ not in ("2021",):
            found = _polls_of(key_, number, entries)
            if found:
                polls, totals = found
        if key_ in BY_RESULTS:
            sources.append(BY_RESULTS[key_])
        elif key_ is None:
            sources.append(BY_RESULTS["2026"])
        c = Contest(day.year, day, plan, code, label, seat, entries, polls, totals, tuple(sources))
        if any(e.votes is None for e in entries):
            c.notes.append(
                "The authority's results page prints no vote count for one candidate: the cell is cut off in the "
                "published table. That candidate's votes are not stored."
            )
        out.append(c)
    return out


def all_contests() -> list[Contest]:
    return [c for year in years() for c in contests(year)] + by_elections()
