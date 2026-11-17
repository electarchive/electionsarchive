import csv
import difflib
import functools
import itertools
import re
import subprocess
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import fastexcel
import httpx

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.tt import report2015, report2020

SOURCE = "tt.ebc"
SITE = "https://ebctt.com"
UPLOADS = f"{SITE}/wp-content/uploads"
TRINIDAD = str(uuid5(package.NAMESPACE, "polity\x1fTT"))
PUBLISHER = "Elections and Boundaries Commission of Trinidad and Tobago"
REGISTERED, BALLOTS, VALID, INVALID, VOTES = 2, 3, 4, 5, 1

HISTORY = f"{SITE}/wp-content/plugins/charts/data/parliamentary-results.tsv"
RESULTS_2015 = f"{UPLOADS}/2015-Parliamentary-Elections-Final-Results-and-CandiatesVote-Count.pdf"
PARTY_2015 = f"{UPLOADS}/Results-of-the-Parliamentary-Election-Held-on-Monday-7th-September-2015-Political-Party.xlsx"
REPORT_2015 = f"{UPLOADS}/Report%20on%20the%202015%20Parliamentary%20Election.pdf"
# The report's station tables of Appendix I lie on these pages.
REPORT_2015_PAGES = range(9, 347)
SUMMARY_2020 = f"{UPLOADS}/The-Preliminary-Results-for-the-2020-Parliamentary-Elections.pdf"
REPORT_2020 = (
    f"{UPLOADS}/Report-of-the-Elections-Boundaries-Commission-on-the-Parliamentary-Elections-held-on-Monday-10th-"
    "August-2020.pdf"
)
# The report's station tables of Appendix I lie on these pages.
REPORT_2020_PAGES = range(19, 403)
REPORT_2025 = f"{UPLOADS}/Parliamentary_Elections_Report_2025_20260713_secured.pdf"
FILES = {
    HISTORY: ".tsv",
    RESULTS_2015: ".pdf",
    PARTY_2015: ".xlsx",
    REPORT_2015: ".pdf",
    SUMMARY_2020: ".pdf",
    REPORT_2020: ".pdf",
    REPORT_2025: ".pdf",
}
DAYS = {
    1946: date(1946, 7, 1),
    1956: date(1956, 9, 24),
    1961: date(1961, 12, 4),
    1966: date(1966, 11, 7),
    1971: date(1971, 5, 24),
    1976: date(1976, 9, 13),
    1981: date(1981, 11, 9),
    1986: date(1986, 12, 15),
    1991: date(1991, 12, 16),
    1995: date(1995, 11, 6),
    2000: date(2000, 12, 11),
    2001: date(2001, 12, 10),
    2002: date(2002, 10, 7),
    2007: date(2007, 11, 5),
    2010: date(2010, 5, 24),
    2015: date(2015, 9, 7),
    2020: date(2020, 8, 10),
    2025: date(2025, 4, 28),
}
COUNCIL = {1946, 1956}
FULL = {2015, 2020, 2025}
STATIONS = {2015, 2020, 2025}
# Party abbreviations that mean a different party in different decades.
SPLIT = {("P.D.P.", 1956): "PDP56"}
PARTIES = {
    "A.P.P.": "All People's Party",
    "C.A.R.M.": "Class Action Reform Movement",
    "C.O.P.": "Congress of the People",
    "D.P.T.": "Democratic Party of Trinidad and Tobago",
    "I.D.A.": "Innovative Democratic Alliance",
    "I.L.P.": "Independent Liberal Party",
    "M.N.D.": "Movement for National Development",
    "M.S.J.": "Movement for Social Justice",
    "N.C.T.": "National Coalition for Transformation",
    "N.N.V.": "New National Vision",
    "N.O.W.": "National Organisation of We The People",
    "N.T.A.": "National Transformation Alliance",
    "O.T.V.": "One Tobago Voice",
    "P.D.P.": "Progressive Democratic Patriots",
    "P.E.P.": "Progressive Empowerment Party",
    "P.F.": "Patriotic Front",
    "P.N.M.": "People's National Movement",
    "P.P.": "Progressive Party",
    "T.D.F.": "Trinidad and Tobago Democratic Front",
    "T.H.C.": "Trinidad Humanity Campaign",
    "T.H.M.": "The Hyarima Movement",
    "T.N.P.": "The National Party",
    "T.P.P.": "Tobago People's Party",
    "U.N.C.": "United National Congress",
    "U.P.P.": "Unrepresented Peoples Party",
    "U.T.P.": "Unity of The People",
}


def fetch() -> None:
    for url, suffix in FILES.items():
        for attempt in range(6):
            try:
                raw.fetch(SOURCE, url, suffix=suffix)
                break
            except httpx.HTTPError:
                if attempt == 5:
                    raise
                time.sleep(3 * (attempt + 1))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(code: str) -> str:
    return key("area", "tt.constituency", code)


def norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def title(text: str) -> str:
    words = re.sub(r"\s+", " ", text.strip()).title()
    for small in ("Of", "And"):
        words = words.replace(f"-{small}-", f"-{small.lower()}-")
    return re.sub(r"\bMc ([A-Z])", r"Mc\1", words.replace("'S", "'s"))


def party_code(code: str, year: int) -> str:
    return SPLIT.get((code, year), re.sub(r"[^A-Z0-9]", "", code.upper()))


def number(text: str) -> int:
    return int(text.replace(",", ""))


@dataclass
class Candidate:
    name: str | None
    party: str | None
    votes: int | None
    independent: bool = False
    label: str | None = None


@dataclass
class Station:
    code: str
    special: bool
    venue: str
    votes: list[int]
    valid: int
    rejected: int
    registered: int | None
    page: int | None = None
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)


@dataclass
class Division:
    registered: int
    page: int
    box: tuple[float, float, float, float]


@dataclass
class District:
    name: str
    registered: int | None
    valid: int | None
    rejected: int | None
    ballots: int | None
    candidates: list[Candidate]
    winner: int
    stations: list[Station] = field(default_factory=list)
    declared: dict[str, int] = field(default_factory=dict)
    # For each candidate, the index of its column in the preliminary summary the district supersedes.
    columns: list[int] = field(default_factory=list)
    # Polling divisions whose electorate is printed once for several stations: the electorate, and the page and
    # box it was read from.
    divisions: dict[str, Division] = field(default_factory=dict)


class Event(NamedTuple):
    kind: str
    name: str = ""
    names: tuple[tuple[str, str], ...] = ()
    code: str = ""
    special: bool = False
    venue: str = ""
    numbers: tuple[int, ...] = ()


@dataclass
class Work:
    """A district's polling station tables while the report is being read."""

    name: str
    names: list[tuple[str, str]]
    stations: list[Event]
    total: list[int]


@dataclass
class Election:
    year: int
    districts: list[District]
    totals: dict[str, int] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    stage: str = "official"
    supersedes: Election | None = None
    pages: dict[int, tuple[int, int]] = field(default_factory=dict)


def _text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def _winner(candidates: list[Candidate]) -> int:
    votes = [c.votes or 0 for c in candidates]
    top = max(votes)
    if votes.count(top) != 1:
        raise ValueError(f"no single leader in {votes}")
    return votes.index(top)


def history() -> dict[int, Election]:
    """Winners and turnout figures per constituency, 1946 to 2010, from the commission's historical district data."""
    elections: dict[int, Election] = {}
    path = raw.cached(SOURCE, HISTORY).path
    with path.open(encoding="utf-8") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            year = int(row["year"])
            if year in FULL:
                continue
            party = row["party"].strip()
            elected = Candidate(
                re.sub(r"\s+", " ", row["candidate"]).strip(),
                None if party in {"n.a.", "IND", "IND."} else party,
                None,
                independent=party in {"IND", "IND."},
            )
            elections.setdefault(year, Election(year, [], sources=[HISTORY])).districts.append(
                District(
                    title(row["district"].replace(" / ", "/")),
                    number(row["electors"]),
                    number(row["accepted"]),
                    number(row["rejected"]),
                    None,
                    [elected],
                    0,
                )
            )
    return elections


def _station_rows(lines: list[str]) -> Iterator[Event]:
    parties = re.compile(r"^(?:[A-Z]+\.)+$")
    digits = r"\d[\d,]*"
    row = re.compile(r"^\s*(\*?)(\d{3,4}(?:-\d+)?)\s{2,}(\S.*?)\s{2,}((?:" + digits + r"\s+)*" + digits + r")\s*$")
    total = re.compile(r"^TOTAL\s+((?:" + digits + r"\s+)*" + digits + r")\s*$")
    start = next(
        i
        for i, line in enumerate(lines)
        if re.search(r"Elections?\b.*House.*APPENDIX II\s*$", line)
        and "Electoral District of" in "".join(lines[i : i + 3])
    )
    current: str | None = None
    names: list[tuple[str, str]] = []
    segment: list[str] = []
    heading = False
    for index in range(start, len(lines)):
        line = lines[index]
        if re.search(r"APPENDIX III\b", line) and "Elections" in line:
            break
        if re.search(r"^\s*Elections?\b.*House.*APPENDIX II\s*$", line):
            following = next(text for text in lines[index + 1 :] if text.strip())
            found = re.search(r"Electoral District of (.+?)\s*$", following)
            if found is None:
                break
            current = found.group(1).strip()
            names, segment, heading = [], [], True
            yield Event("district", name=current)
            continue
        if current is None:
            continue
        if heading:
            text = line.strip()
            if row.match(line):
                heading = False
                yield Event("candidates", names=tuple(names))
            elif (
                not text
                or text.startswith(("NO. OF VOTES", "POLLING", "STATION", "NUMBER", "TOTAL", "Electoral District of"))
                or "POLLING STATION ADDRESS" in text
            ):
                continue
            elif parties.match(text):
                names.append((" ".join(segment), text))
                segment = []
                continue
            else:
                segment.append(text)
                continue
        found_row = row.match(line)
        if found_row:
            special, code, venue, figures = found_row.groups()
            yield Event(
                "station",
                code=code,
                special=special == "*",
                venue=venue,
                numbers=tuple(number(figure) for figure in figures.split()),
            )
            continue
        found_total = total.match(line)
        if found_total:
            yield Event("total", numbers=tuple(number(figure) for figure in found_total.group(1).split()))


def election_2025() -> Election:
    lines = _text(REPORT_2025).splitlines()
    summary: dict[str, tuple[int, int, int, int, str, str]] = {}
    head = next(i for i, line in enumerate(lines) if "APPENDIX I" in line and "Elections to the House" in line)
    for line in lines[head : head + 150]:
        found = re.match(
            r"^([A-Z][A-Z'./\- ]+?)\s{2,}([\d,]+)\s+([\d,]+)\s+(\d+)\s+([\d,]+)\s{2,}(.+?)\s{2,}((?:[A-Z]+\.)+)\s*$",
            line,
        )
        if found:
            name, registered, valid, rejected, cast, winner, party = found.groups()
            summary[norm(name)] = (number(registered), number(valid), number(rejected), number(cast), winner, party)
    totals_line = re.compile(r"^\s*TOTAL\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$")
    grand = next(found for line in lines[head : head + 200] if (found := totals_line.match(line)))
    districts: list[District] = []
    work = Work("", [], [], [])
    done = True
    for event in _station_rows(lines):
        if event.kind == "district":
            if done or work.name != event.name:
                work = Work(event.name, [], [], [])
                done = False
        elif event.kind == "candidates":
            if not work.names:
                work.names = list(event.names)
        elif event.kind == "station":
            work.stations.append(event)
        else:
            work.total = list(event.numbers)
            districts.append(_district_2025(work, summary))
            done = True
    if len(districts) != len(summary):
        raise ValueError(f"{len(districts)} districts in the polling station tables, {len(summary)} in the summary")
    return Election(
        2025,
        districts,
        {
            "registered": number(grand.group(1)),
            "valid": number(grand.group(2)),
            "rejected": number(grand.group(3)),
            "ballots": number(grand.group(4)),
        },
        [REPORT_2025],
    )


def _district_2025(work: Work, summary: dict[str, tuple[int, int, int, int, str, str]]) -> District:
    name, names, total = work.name, work.names, work.total
    stations: list[Station] = []
    width = len(names) + 3
    for data in work.stations:
        figures = list(data.numbers)
        if len(figures) != width:
            raise ValueError(f"{name} station {data.code}: {len(figures)} figures for {len(names)} candidates")
        stations.append(
            Station(
                data.code,
                data.special,
                data.venue,
                figures[: len(names)],
                figures[-3],
                figures[-2],
                figures[-1],
            )
        )
    for column in range(width):
        if sum(s_figures(s, len(names))[column] for s in stations) != total[column]:
            raise ValueError(f"{name}: column {column} of the polling stations disagrees with the printed total")
    registered, valid, rejected, ballots, winner, party = summary[norm(name)]
    if [total[-1], total[-3], total[-2], total[-3] + total[-2]] != [registered, valid, rejected, ballots]:
        raise ValueError(f"{name}: printed district total disagrees with the summary")
    candidates = []
    for label, code in names:
        surname, _, given = label.partition(",")
        candidates.append(
            Candidate(
                f"{given.strip()} {surname.strip()}".strip(),
                None if code == "IND." else code,
                None,
                independent=code == "IND.",
                label=label,
            )
        )
    for index, candidate in enumerate(candidates):
        candidate.votes = total[index]
    leader = _winner(candidates)
    if norm(candidates[leader].name or "") != norm(winner) or (candidates[leader].party or "IND.") != party:
        raise ValueError(f"{name}: declared winner {winner} is not the leader {candidates[leader].name}")
    return District(
        title(name), registered, valid, rejected, ballots, candidates, leader, stations, {"ballots": ballots}
    )


def s_figures(station: Station, width: int) -> list[int]:
    return [*station.votes, station.valid, station.rejected, station.registered or 0]


def preliminary_2020() -> Election:
    lines = _text(SUMMARY_2020).splitlines()
    header: list[tuple[str, float]] | None = None
    districts: list[District] = []
    columns: Counter[str] = Counter()
    grand: tuple[dict[str, int], int, int] | None = None
    for line in lines:
        if "DISTRICT WITH ELECTORATE" in line:
            header = None
            continue
        if header is None and re.search(r"\bCOP\b.*\bGrand Total\b", line):
            squeezed = line.replace("Grand Total", "Grand_Total")
            header = [(found.group(), (found.start() + found.end()) / 2) for found in re.finditer(r"\S+", squeezed)]
            continue
        if header is None:
            continue
        found = re.match(r"^(\S.*?) - (\d+)\s{2,}(.*)$", line) or re.match(r"^(Grand Total)()\s{2,}(.*)$", line)
        if not found:
            continue
        name, registered, rest = found.groups()
        offset = len(line) - len(rest)
        figures = [(t.group(), offset + (t.start() + t.end()) / 2) for t in re.finditer(r"[\d,]+", rest)]
        cells: dict[str, int] = {}
        for text, center in figures[:-2]:
            column = min(header[:-2], key=lambda item: abs(item[1] - center))[0]
            if column in cells:
                raise ValueError(f"{name}: two figures under {column}")
            cells[column] = number(text)
        rejected, ballots = number(figures[-2][0]), number(figures[-1][0])
        if sum(cells.values()) + rejected != ballots:
            raise ValueError(f"{name}: parties and rejected ballots do not add up to the grand total")
        if name == "Grand Total":
            grand = (cells, rejected, ballots)
            continue
        candidates = [
            Candidate(
                None,
                None if column.startswith("IN") else column,
                votes,
                independent=column.startswith("IN"),
                label=f"Independent {column[2:]}" if column.startswith("IN") else None,
            )
            for column, votes in cells.items()
        ]
        columns.update(cells)
        districts.append(
            District(
                title(name),
                int(registered),
                sum(cells.values()),
                rejected,
                ballots,
                candidates,
                _winner(candidates),
            )
        )
    if grand is None or dict(columns) != grand[0] or sum(d.rejected or 0 for d in districts) != grand[1]:
        raise ValueError("2020 districts disagree with the printed grand total")
    return Election(
        2020,
        districts,
        {
            "valid": grand[2] - grand[1],
            "rejected": grand[1],
            "ballots": grand[2],
            "registered": sum(d.registered or 0 for d in districts),
        },
        [SUMMARY_2020],
        "preliminary",
    )


def _header(text: str) -> tuple[str, str]:
    """A candidate's column heading in the report: the name, surname first, and the party abbreviation below it."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError(f"unreadable candidate heading {text!r}")
    return " ".join(lines[:-1]), lines[-1]


def election_2020() -> Election:
    """The 2020 election by polling station, from Appendix I of the commission's report, superseding the summary."""
    preliminary = preliminary_2020()
    earlier = {norm(district.name): district for district in preliminary.districts}
    pdf = raw.cached(SOURCE, REPORT_2020).path
    fixes = report2020.overrides()
    headings = report2020.headings()
    groups: dict[str, list[report2020.Draft]] = {}
    sizes: dict[int, tuple[int, int]] = {}
    for reading in report2020.readings(pdf, REPORT_2020_PAGES).values():
        page = report2020.draft(reading, fixes)
        code = _district_code(page.district, list(earlier))
        if code is None:
            raise ValueError(f"report page {page.number}: no district {page.district!r} in the summary")
        groups.setdefault(code, []).append(page)
        sizes[page.number] = (page.width, page.height)
    if set(groups) != set(earlier):
        raise ValueError(f"districts missing from the report: {sorted(set(earlier) - set(groups))}")
    districts = [_district_2020(earlier[code], pages, headings.get(code)) for code, pages in groups.items()]
    order = {norm(district.name): index for index, district in enumerate(preliminary.districts)}
    districts.sort(key=lambda district: order[norm(district.name)])
    parties: Counter[str] = Counter()
    for district in districts:
        for candidate in district.candidates:
            parties[candidate.party if candidate.party in NATIONAL_2020 else ""] += candidate.votes or 0
    if dict(parties) != NATIONAL_2020:
        raise ValueError(f"2020 party totals {dict(parties)} disagree with the report's {NATIONAL_2020}")
    return Election(
        2020,
        districts,
        {
            "registered": sum(d.registered or 0 for d in districts),
            "valid": sum(d.valid or 0 for d in districts),
            "rejected": sum(d.rejected or 0 for d in districts),
            "ballots": sum(d.ballots or 0 for d in districts),
        },
        [REPORT_2020],
        supersedes=preliminary,
        pages=sizes,
    )


def _venue(text: str) -> str:
    """A polling station's venue as read from the scan, with the commas it misreads for abbreviation stops."""
    text = re.sub(r"\b(ST|NO|[A-Z]),(?=\s|$)", r"\1.", re.sub(r"[|_~]", " ", text.upper()))
    return title(re.sub(r"\s+", " ", text).strip(" ,"))


def _district_code(title: str, codes: list[str]) -> str | None:
    """The summary's district a page heading names, allowing for a misread letter or two."""
    found = difflib.get_close_matches(norm(title), codes, n=1, cutoff=0.8)
    return found[0] if found else None


def _district_2020(earlier: District, pages: list[report2020.Draft], heading: list[list[str]] | None) -> District:
    name = earlier.name
    labels = heading or _headings(pages, earlier)
    width = len(labels) + 3
    for page in pages:
        if any(len(row) != width for row in page.grid):
            raise ValueError(f"{name}: page {page.number} does not have {width} columns")
    settled = report2020.settle(pages)
    if settled.problems:
        raise ValueError(f"{name}: {'; '.join(settled.problems)}")
    stations = [
        Station(
            row.code,
            row.special,
            _venue(row.venue),
            row.values[: len(labels)],
            row.values[-3],
            row.values[-2],
            row.values[-1],
            number,
            row.boxes,
        )
        for number, row in settled.rows
    ]
    total = settled.total
    codes = [station.code for station in stations]
    if len(codes) != len(set(codes)):
        raise ValueError(f"{name}: a polling station appears twice")
    candidates = []
    for (label, code), votes in zip(labels, total, strict=False):
        surname, _, given = label.partition(",")
        candidates.append(
            Candidate(
                f"{given.strip()} {surname.strip()}".strip(),
                None if code == "IND." else code,
                votes,
                independent=code == "IND.",
                label=label,
            )
        )
    valid, rejected, registered = total[-3:]
    if sum(total[: len(labels)]) != valid:
        raise ValueError(f"{name}: candidate totals do not add up to the valid votes")
    return District(
        name,
        registered,
        valid,
        rejected,
        valid + rejected,
        candidates,
        _winner(candidates),
        stations,
        {"ballots": valid + rejected},
        _columns(earlier, candidates),
    )


# The national votes the report's account of the results gives the two main parties and all others together.
NATIONAL_2020 = {"P.N.M.": 322_180, "U.N.C.": 309_654, "": 25_058}
# Party labels the report spells out instead of abbreviating.
SPELLED = {"ONETOBAGOVOICE": "OTV", "TTDEMFRONT": "TDF", "PROGRESSIVEPARTY": "PP", "UNITYOFTHEPEOPLE": "UTP"}


def _headings(pages: list[report2020.Draft], earlier: District) -> list[list[str]]:
    """Each candidate's name and party as most of a district's pages read them.

    The party is matched to the closest party abbreviation in the preliminary summary for the same district,
    because the scan often reads a dot as a letter (U.L.N.C. for U.N.C.).
    """
    codes = {re.sub(r"[^A-Z0-9]", "", c.party or "IND") for c in earlier.candidates}
    found: list[list[str]] = []
    for column in range(len(pages[0].names)):
        votes: Counter[str] = Counter()
        parties: Counter[str] = Counter()
        for page in pages:
            if column >= len(page.names):
                continue
            name, party = _header(page.names[column])
            name = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z,.'\- ]", " ", name)).strip(" ,-").upper()
            # Join a name the heading breaks after a hyphen, and read a trailing initial's comma as its stop.
            name = re.sub(r"- ", "-", re.sub(r"\s+,", ",", re.sub(r",(?=\S)", ", ", name)))
            name = re.sub(r"(?<=\b[A-Z])[,]?$", ".", name) if re.search(r"\b[A-Z][.,]?$", name) else name
            votes[name] += 1
            letters = re.sub(r"[^A-Z0-9]", "", party.upper()).replace("0", "O")
            letters = SPELLED.get(letters, letters)
            match = difflib.get_close_matches(letters, sorted(codes), n=1, cutoff=0.5)
            if match:
                parties[match[0]] += 1
        if not parties:
            raise ValueError(f"{earlier.name}: no party read for candidate {column + 1}")
        code = parties.most_common(1)[0][0]
        found.append([votes.most_common(1)[0][0], "IND." if code == "IND" else "".join(f"{c}." for c in code)])
    return found


def _columns(earlier: District, candidates: list[Candidate]) -> list[int]:
    """Matches each candidate in the report to the column for the same option in the preliminary summary."""
    found: list[int] = []
    for candidate in candidates:
        party = re.sub(r"[^A-Z0-9]", "", candidate.party or "")
        matches = [
            index
            for index, option in enumerate(earlier.candidates)
            if index not in found
            and (
                (candidate.independent and option.independent)
                or (party and re.sub(r"[^A-Z0-9]", "", option.party or "") == party)
            )
        ]
        if not matches:
            raise ValueError(f"{earlier.name}: {candidate.label} ({candidate.party}) is not in the summary")
        found.append(
            min(matches, key=lambda index: abs((earlier.candidates[index].votes or 0) - (candidate.votes or 0)))
        )
    if len(found) != len(earlier.candidates):
        raise ValueError(
            f"{earlier.name}: {len(found)} candidates in the report, {len(earlier.candidates)} in the summary"
        )
    return found


def sheet_2015() -> Election:
    """The 2015 election by district, from the commission's final results sheet and candidate table."""
    reader = fastexcel.read_excel(str(raw.cached(SOURCE, PARTY_2015).path))
    rows = reader.load_sheet(0, header_row=None).to_polars().rows()
    heading = rows[3]
    columns = {c: str(heading[c]).strip() for c in range(6, len(heading)) if heading[c] and str(heading[c]).strip()}
    declared: dict[int, tuple[str, int, int, int, int, dict[str, int]]] = {}
    totals: dict[str, int] = {}
    for row in rows[6:]:
        if not row[0]:
            continue
        found = re.match(r"^\s*(\d+)\s*\.\s*(.+)$", str(row[0]))
        if found:
            declared[int(found.group(1))] = (
                found.group(2).strip(),
                int(row[1]),
                int(row[2]),
                int(row[4]),
                int(row[5]),
                {columns[c]: int(row[c]) for c in columns if row[c] not in (None, "")},
            )
        elif str(row[0]).strip() == "TOTAL":
            totals = {"registered": int(row[1]), "ballots": int(row[2]), "rejected": int(row[4]), "valid": int(row[5])}
    named: dict[int, list[Candidate]] = {}
    current = 0
    line_pattern = re.compile(r"^(.*?)\s{2,}([A-Z][A-Z.]*\.)\s+([\d,]+)\s+([\d.]+)%")
    for line in _text(RESULTS_2015).splitlines():
        start = re.match(r"^\s{0,12}(\d{1,2})\s*\.\s*[A-Z]", line)
        if start:
            current = int(start.group(1))
            named.setdefault(current, [])
        found = line_pattern.match(line)
        if found and current:
            who = re.split(r"\s{2,}", found.group(1).strip())[-1].strip()
            named[current].append(Candidate(who, found.group(2), number(found.group(3))))
    districts: list[District] = []
    for index, (name, registered, ballots, rejected, valid, votes) in sorted(declared.items()):
        candidates = named.get(index, [])
        have = Counter()
        for candidate in candidates:
            have[candidate.party or ""] += candidate.votes or 0
        for party, count in votes.items():
            if have[party] == 0 and count:
                # Y.O.U.R. Party's label wraps onto two lines in the commission's PDF, so the line is missed.
                missing = Candidate(MISSING.get((index, party)), party, count)
                candidates.append(missing)
                have[party] += count
        if dict(have) != votes or sum(votes.values()) != valid:
            raise ValueError(f"2015 district {index} {name}: candidate lines disagree with the party sheet")
        for candidate in candidates:
            if candidate.party == "IND.":
                candidate.independent, candidate.party = True, None
        districts.append(District(title(name), registered, valid, rejected, ballots, candidates, _winner(candidates)))
    return Election(2015, districts, totals, [RESULTS_2015, PARTY_2015])


def election_2015() -> Election:
    """The 2015 election by polling station, from Appendix I of the commission's report, superseding the sheet."""
    sheet = sheet_2015()
    earlier = {norm(district.name): district for district in sheet.districts}
    pdf = raw.cached(SOURCE, REPORT_2015).path
    overrides = report2015.OVERRIDES
    fixes = report2020.overrides(overrides)
    headings = report2020.headings(overrides)
    groups: dict[str, list[report2020.Draft]] = {}
    sizes: dict[int, tuple[int, int]] = {}
    for reading in report2015.readings(pdf, REPORT_2015_PAGES, 3).values():
        page = report2020.draft(reading, fixes)
        code = _district_code(page.district, list(earlier))
        if code is None:
            raise ValueError(f"report page {page.number}: no district {page.district!r} in the results sheet")
        groups.setdefault(code, []).append(page)
        sizes[page.number] = (page.width, page.height)
    if set(groups) != set(earlier):
        raise ValueError(f"districts missing from the report: {sorted(set(earlier) - set(groups))}")
    districts = [_district_2015(earlier[code], pages, headings.get(code)) for code, pages in groups.items()]
    order = {norm(district.name): index for index, district in enumerate(sheet.districts)}
    districts.sort(key=lambda district: order[norm(district.name)])
    return Election(
        2015,
        districts,
        dict(sheet.totals),
        [REPORT_2015],
        supersedes=sheet,
        pages=sizes,
    )


def _sequence(codes: list[str]) -> list[str]:
    """Station numbers with a misread suffix restored: a division's stations are numbered 01, 02, and so on."""
    fixed = codes[:]
    for index, code in enumerate(codes):
        division, _, suffix = code.partition("-")
        if not suffix.isdigit():
            continue
        before = fixed[index - 1].partition("-") if index else ("", "", "")
        after = codes[index + 1].partition("-") if index + 1 < len(codes) else ("", "", "")
        if before[0] == division and before[2].isdigit():
            expected = int(before[2]) + 1
        elif after[0] == division and after[2].isdigit():
            expected = int(after[2]) - 1
        else:
            continue
        if expected >= 1 and int(suffix) != expected:
            fixed[index] = f"{division}-{expected:02d}"
    return fixed


def _scrub(settled: report2020.Settled) -> None:
    """Clears specks read as electorates in the empty cells of a division whose electorate one station carries."""
    rows = [row for _, row in settled.rows]
    for _, group in itertools.groupby(rows, key=lambda row: row.code.lstrip("*").partition("-")[0]):
        members = list(group)
        held = [row for row in members if row.values[-1] >= 20]
        if len(members) > 1 and len(held) == 1:
            for row in members:
                if row is not held[0] and row.values[-1] < 20:
                    row.values[-1] = 0
    settled.total = [sum(row.values[c] for row in rows) for c in range(len(settled.total))]


def _carried(pages: list[report2020.Draft], values: list[list[int]]) -> int:
    """How many of the running totals printed at the foot and head of the district's pages the rows reach."""
    matched, start = 0, 0
    width = len(values[0])
    running = [0] * width
    for k, page in enumerate(pages):
        for row in values[start : start + len(page.grid)]:
            running = [a + b for a, b in zip(running, row, strict=True)]
        start += len(page.grid)
        if k + 1 == len(pages):
            break
        for c in range(width):
            reads = [*page.totals.get("carried", [[]] * width)[c], *pages[k + 1].totals.get("brought", [[]] * width)[c]]
            matched += running[c] in reads
    return matched


def _district_2015(earlier: District, pages: list[report2020.Draft], heading: list[list[str]] | None) -> District:
    name = earlier.name
    labels = heading or _headings(pages, earlier)
    width = len(labels) + 3
    for page in pages:
        if any(len(row) != width for row in page.grid):
            raise ValueError(f"{name}: page {page.number} does not have {width} columns")
    order = _columns(earlier, [Candidate(None, None if c == "IND." else c, None, c == "IND.") for _, c in labels])
    sheet = [*(earlier.candidates[i].votes or 0 for i in order), earlier.valid, earlier.rejected, earlier.registered]
    # The results sheet's district totals count as one more reading of the totals row on the district's last page.
    last = pages[-1].totals.setdefault("total", [[] for _ in range(width)])
    for column, value in enumerate(sheet):
        last[column] = [*last[column], value] if value is not None else last[column]
    settled = report2020.settle(pages)
    _scrub(settled)
    # A page's printed running totals sometimes disagree with its own rows, so a district stands on its rows and
    # on its final totals: every station adds up, and every column adds up to the printed or the sheet's total.
    problems = [problem for problem in settled.problems if "do not add up" in problem]
    printed = pages[-1].totals["total"]
    checked = norm(name) in report2015.discrepancies()
    problems += (
        []
        if checked
        else [
            f"column {column} adds up to {settled.total[column]}, printed {printed[column]}"
            for column in range(width)
            if settled.total[column] not in printed[column]
        ]
    )
    if problems and not checked:
        grid = [row for page in pages for row in page.grid]
        values = [row.values for _, row in settled.rows]
        solved = report2015.resolve(grid, values, printed, len(labels), functools.partial(_carried, pages))
        if solved is None:
            raise ValueError(f"{name}: {'; '.join(problems)}")
        for (_, row), fixed in zip(settled.rows, solved, strict=True):
            row.values = fixed
        settled.total = [sum(row[c] for row in solved) for c in range(width)]
    elif problems:
        raise ValueError(f"{name}: {'; '.join(problems)}")
    # A number read by hand is keyed by page and the scan's reading, with the reading's order on the page after a
    # hash when the scan reads two stations alike.
    read, seen = report2015.codes(), Counter[tuple[int, str]]()
    raw_codes = []
    for number, row in settled.rows:
        nth = seen[number, row.code]
        seen[number, row.code] += 1
        raw_codes.append(read.get(f"{number}:{row.code}#{nth}", read.get(f"{number}:{row.code}", row.code)))
    codes = _sequence(raw_codes)
    if len(codes) != len(set(codes)):
        raise ValueError(f"{name}: a polling station appears twice in {codes}")
    stations = [
        Station(
            code,
            row.special,
            _venue(row.venue),
            row.values[: len(labels)],
            row.values[-3],
            row.values[-2],
            row.values[-1],
            number,
            row.boxes,
        )
        for code, (number, row) in zip(codes, settled.rows, strict=True)
    ]
    # The electorate of a division with several stations is printed once, beside its first station or its last.
    divisions: dict[str, Division] = {}
    for division, group in itertools.groupby(stations, key=lambda station: station.code.partition("-")[0]):
        members = list(group)
        if len(members) == 1:
            continue
        carriers = [station for station in members if station.registered]
        if len(carriers) == 1:
            held = carriers[0]
            assert held.registered is not None and held.page is not None
            divisions[division] = Division(held.registered, held.page, held.boxes[-1])
            for station in members:
                station.registered = None
        elif len(carriers) != len(members):
            raise ValueError(f"{name}: division {division} prints its electorate unevenly")
    columns = order
    candidates = []
    # The district's figures are the commission's declared ones, from the results sheet; the stations hold the
    # report's rows, which add up to them except where the report's own rows and totals disagree.
    for index, (label, code) in enumerate(labels):
        votes = earlier.candidates[columns[index]].votes
        # The person keeps the name the results sheet gives, which the earlier set already holds.
        candidates.append(
            Candidate(
                earlier.candidates[columns[index]].name,
                None if code == "IND." else code,
                votes,
                independent=code == "IND.",
                label=label,
            )
        )
    reached = [sum(candidate.votes or 0 for candidate in candidates), earlier.valid]
    if reached[0] != reached[1]:
        raise ValueError(f"{name}: the sheet's candidate votes do not add up to its valid votes")
    return District(
        name,
        earlier.registered,
        earlier.valid,
        earlier.rejected,
        earlier.ballots,
        candidates,
        _winner(candidates),
        stations,
        {"ballots": earlier.ballots or 0},
        columns,
        divisions,
    )


MISSING = {(29, "Y.O.U.R."): "Judith De Verteuil"}


@functools.cache
def elections() -> dict[int, Election]:
    found = history()
    found[2015] = election_2015()
    found[2020] = election_2020()
    found[2025] = election_2025()
    for election in found.values():
        keys = [norm(d.name) for d in election.districts]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{election.year} repeats a district name")
    return dict(sorted(found.items()))


def packages() -> list[str]:
    return [
        "tt.ebc/geo",
        "tt.ebc/registry",
        *(f"tt.ebc/{day}" for day in DAYS.values()),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for year in elections():
        general(year).write()


def emit() -> None:
    package.run("load", packages())


METHODS = {
    "history": (
        "From the commission's historical district profile data, which gives each constituency's electors, accepted "
        "(valid) votes, rejected ballots, and the elected candidate with their party. Candidates who lost and the "
        "votes of the winner are not in that file, so the result set holds the registered, valid, and invalid "
        "counts and the winner only. The file's turnout column mixes two formulas between years, so it is not stored."
    ),
    2015: (
        "From the commission's report on the 7 September 2015 election, Appendix I, which gives each candidate's votes "
        "at each polling station with its valid votes and rejected ballots, and the electorate of each polling "
        "division. Where a division has several stations, its electorate is printed once, beside one of them, and is "
        "stored on the division. The report is a scan, so its figures were read by optical character recognition, "
        "each figure's ink enlarged and read on its own. Every station's candidate votes add up to its valid votes, "
        "and the stations add up to the district totals the report prints; figures that did not settle against those "
        "sums and the totals carried from page to page were read from the page by hand. The district totals are the "
        "commission's declared figures from its results sheet. In a few districts the report's printed totals differ "
        "from its own stations or from the sheet; the stations are kept as printed, and the declared totals as "
        "declared. Candidates keep the names the commission's results sheet gives. Each figure records the page and "
        "the box it was read from."
    ),
    "2015-summary": (
        "From the commission's final results of the 7 September 2015 election: a sheet of votes by party for each "
        "electoral district, and a table of each candidate's votes. The two agree in every district. Registered "
        "voters are the electorate, ballots cast are valid plus rejected ballots, and the winner is the candidate "
        "with most votes. The polling station figures of the commission's report on the election supersede this set."
    ),
    "2020-summary": (
        "From the commission's published preliminary results summary of the 10 August 2020 election, which gives "
        "each electoral district's votes for each party, with independent candidates as IN1 to IN4, and rejected "
        "ballots. Its district electorate and rejected ballots are stored; ballots cast is the summary's grand "
        "total. The polling station figures of the commission's report on the election supersede this set."
    ),
    2020: (
        "From the commission's report on the 10 August 2020 election, Appendix I, which gives each candidate's votes "
        "at each polling station with its valid votes, rejected ballots, and electorate, and names the candidates. "
        "The report is a scan, so its figures were read by optical character recognition in three passes: each "
        "column as a strip, each cell on its own, and a tight crop around short figures. Every station's candidate "
        "votes add up to its valid votes, every page's stations carry the totals brought forward to the totals "
        "carried forward, and each district's stations add up to the totals the returning officer certified. Four "
        "electorate figures the passes could not settle were read from the page by hand. The parties' national "
        "totals match those in the report's own account of the results. Each figure records the page and the box "
        "it was read from. Ballots cast are valid votes plus rejected ballots."
    ),
    2025: (
        "From the commission's report on the 28 April 2025 election, Appendix II, which gives each candidate's votes "
        "at each polling station (division) with its valid votes, rejected ballots, and electorate. The station "
        "figures sum to the district totals the report prints and to the district summary in Appendix I, which "
        "also names the declared winner. Stations marked with an asterisk held the ballots of special electors."
    ),
}


def _label(year: int) -> str:
    return f"{year} Trinidad and Tobago {'Legislative Council' if year in COUNCIL else 'parliamentary'} election"


def geo() -> Package:
    p = Package(
        "tt.ebc/geo",
        SOURCE,
        "Trinidad and Tobago's electoral districts (constituencies) by name, with the polling stations of each "
        "election as reporting units, from the Elections and Boundaries Commission",
    )
    p.external.add(TRINIDAD)
    p.add("scheme", code="tt.constituency", polity_id=TRINIDAD, kind="electoral_district", rank=1, noun="constituency")
    p.add("scheme", code="tt.polling_division", polity_id=TRINIDAD, kind="precinct", rank=2, noun="polling division")
    p.add("scheme", code="tt.polling_station", polity_id=TRINIDAD, kind="precinct", rank=3, noun="polling station")
    seen: dict[str, str] = {}
    for election in elections().values():
        for district in election.districts:
            seen[norm(district.name)] = district.name
    for code, name in sorted(seen.items()):
        id = area(code)
        p.add("area", id=id, scheme_code="tt.constituency")
        p.add("area_code", area_id=id, code=code)
        p.name(id, name, "en", "official")
    return p


BODY = key("body", "house")
COUNCIL_BODY = key("body", "council")
SOURCE_ID = key("source", SOURCE)
SYSTEM = key("electoral_system", "fptp")


def registry() -> Package:
    p = Package(
        "tt.ebc/registry",
        SOURCE,
        "Trinidad and Tobago's House of Representatives and Legislative Council, the plurality system, the "
        "commission as a source, and the parties that contested parliamentary elections",
        depends=["tt.ebc/geo"],
    )
    p.external.add(TRINIDAD)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher=PUBLISHER,
        title="Parliamentary election results",
        url=SITE,
        license="Official public record, no license stated",
        attribution=PUBLISHER,
    )
    for file in FILES:
        p.artifact(raw.cached(SOURCE, file), SOURCE_ID)
    p.add("body", id=BODY, polity_id=TRINIDAD, kind="lower_house")
    p.name(BODY, "House of Representatives", "en", "official")
    p.add("body", id=COUNCIL_BODY, polity_id=TRINIDAD, kind="unicameral")
    p.name(COUNCIL_BODY, "Legislative Council", "en", "official")
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=TRINIDAD,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.name(SYSTEM, "Single-member plurality", "en", "official")
    codes: dict[str, str] = {}
    for year, election in elections().items():
        for district in election.districts:
            for candidate in district.candidates:
                if candidate.party:
                    codes[party_code(candidate.party, year)] = candidate.party
    for code, text in sorted(codes.items()):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=TRINIDAD)
        full = PARTIES.get(text)
        p.name(id, full or text, "en", "official")
        if full:
            p.name(id, text, "en", "abbreviation", False)
    return p


def party(code: str) -> str:
    return key("party", code)


def general(year: int) -> Package:
    election = elections()[year]
    day = DAYS[year]
    p = Package(
        f"tt.ebc/{day}",
        SOURCE,
        f"The {year} Trinidad and Tobago "
        f"{'Legislative Council' if year in COUNCIL else 'parliamentary'} election by "
        f"{'polling station' if year in STATIONS else 'constituency'}, from the Elections and Boundaries Commission",
        depends=["tt.ebc/geo", "tt.ebc/registry"],
    )
    p.external.add(TRINIDAD)
    for url in election.sources + (election.supersedes.sources if election.supersedes else []):
        p.uses(raw.cached(SOURCE, url))
    pages = _pages(p, election)
    earlier = {norm(d.name): d for d in election.supersedes.districts} if election.supersedes else {}
    body = COUNCIL_BODY if year in COUNCIL else BODY
    method = METHODS[year] if year in METHODS else METHODS["history"]
    if year not in FULL:
        p.accept(
            "accounting.votes_valid",
            "The commission's historical district data gives each constituency's valid votes and only the elected "
            "candidate, without the votes of any candidate, so the options' votes cannot sum to the valid votes.",
        )
    if year == 2015:
        p.accept(
            "accounting.declared",
            "In some electoral districts the polling station rows of Appendix I of the commission's report, which add "
            "up to the district totals the report itself prints, differ from the declared district figures of the "
            "commission's results sheet, which the result sets keep as declared; the stations are stored as printed.",
        )
    first = key("election", day)
    p.add("election", id=first, polity_id=TRINIDAD, kind="general", date_from=day, date_to=day, status="held")
    p.name(first, _label(year), "en", "official")
    path = f"tt/{day}-general"
    p.add("slug", path=path, entity_id=first, is_current=True)
    p.add("citation", id=key("citation", first), entity_id=first, source_id=SOURCE_ID, locator=None)
    for district in election.districts:
        code = norm(district.name)
        contest = key("contest", day, code)
        p.add(
            "contest",
            id=contest,
            election_id=first,
            kind="body_seats",
            body_id=body,
            district_area_id=area(code),
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, district.name, "en", "official")
        p.add("slug", path=f"{path}/{slug(district.name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, code)
        previous = earlier.get(code)
        if previous is not None and election.supersedes is not None:
            # The set built from the preliminary summary stays, superseded by the report's polling station set.
            p.add(
                "result_set",
                id=result_set,
                election_id=first,
                contest_id=contest,
                stage=election.supersedes.stage,
                kind="counted",
                source_id=SOURCE_ID,
                status="superseded",
                is_default=False,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=METHODS[f"{year}-summary"],
                lang="en",
            )
            superseded, result_set = result_set, key("result_set", day, code, "report")
        else:
            superseded = None
        p.add(
            "result_set",
            id=result_set,
            election_id=first,
            contest_id=contest,
            stage=election.stage,
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
            supersedes_id=superseded,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        unit = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=TRINIDAD,
            scheme_code="tt.constituency",
            code=f"{day}:{code}",
            parent_id=None,
            area_relation="equals",
            area_id=area(code),
            kind="standard",
        )
        p.name(unit, district.name, "en", "official")
        options: list[str] = []
        named = previous.candidates if previous is not None else district.candidates
        tags = [f"{index}:{norm(c.name or c.label or c.party or '')}" for index, c in enumerate(named)]
        for index, candidate in enumerate(district.candidates):
            # A candidate keeps the option the preliminary summary's column for it already had.
            tag = tags[district.columns[index]] if previous is not None else tags[index]
            option = key("option", day, code, tag)
            options.append(option)
            label = candidate.label if candidate.name is None else (candidate.name)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party(party_code(candidate.party, year)) if candidate.party else None,
                ballot_position=None,
                ballot_label=label or candidate.party,
            )
            if candidate.name:
                person = key("person", day, code, norm(candidate.name))
                p.add("person", id=person)
                p.name(person, title(candidate.name) if candidate.name.isupper() else candidate.name, "en", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", day, code, tag),
                    person_id=person,
                    option_id=option,
                    role="head",
                    list_position=None,
                    ballot_name=candidate.label or candidate.name,
                    member_of_party_id=party(party_code(candidate.party, year)) if candidate.party else None,
                    is_independent=candidate.independent,
                )
        winner = district.winner
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=options[winner],
            kind="elected",
        )
        if superseded is not None and previous is not None:
            old = [key("option", day, code, tag) for tag in tags]
            p.add("outcome", result_set_id=superseded, option_id=old[previous.winner], kind="elected")
            _values(p, superseded, [unit], old, previous)
        stations = district.stations
        units = [unit]
        parents: dict[str, str] = {}
        for division in district.divisions:
            parents[division] = key("unit", day, code, "division", division)
            p.add(
                "reporting_unit",
                id=parents[division],
                polity_id=TRINIDAD,
                scheme_code="tt.polling_division",
                code=f"{day}:{code}:{division}",
                parent_id=unit,
                area_relation="within",
                area_id=area(code),
                kind="standard",
            )
            p.name(parents[division], f"Polling division {division}", "en", "official")
        if stations:
            units = []
            for station in stations:
                child = key("unit", day, code, station.code)
                p.add(
                    "reporting_unit",
                    id=child,
                    polity_id=TRINIDAD,
                    scheme_code="tt.polling_station",
                    code=f"{day}:{code}:{station.code}",
                    parent_id=parents.get(station.code.partition("-")[0], unit),
                    area_relation="within",
                    area_id=area(code),
                    kind="standard",
                )
                p.name(child, f"{station.code} {station.venue}", "en", "official")
                units.append(child)
        _values(p, result_set, units, options, district)
        for division, held in district.divisions.items():
            _value(p, result_set, parents[division], None, REGISTERED, held.registered)
            p.add(
                "value_locator",
                result_set_id=result_set,
                unit_id=parents[division],
                option_id=None,
                channel=0,
                measure=REGISTERED,
                count_round=0,
                page_id=pages[held.page],
                source_id=SOURCE_ID,
                x0=round(held.box[0], 1),
                y0=round(held.box[1], 1),
                x1=round(held.box[2], 1),
                y1=round(held.box[3], 1),
            )
        if stations and stations[0].page is not None:
            _locate(p, result_set, units, options, district, pages)
    return p


def _pages(p: Package, election: Election) -> dict[int, str]:
    """A source page for each scanned page the election's figures are read from."""
    if not election.pages:
        return {}
    artifact = raw.cached(SOURCE, election.sources[0])
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{SOURCE_ID}\x1f{artifact.storage_key}"))
    found: dict[int, str] = {}
    for number, (width, height) in sorted(election.pages.items()):
        found[number] = key("page", election.year, number)
        p.add(
            "source_page",
            id=found[number],
            artifact_id=artifact_id,
            page_number=number,
            image_key=f"pages/{SOURCE}/{artifact.sha256}-{number}.png",
            width=width,
            height=height,
        )
    return found


def _locate(
    p: Package, result_set: str, units: list[str], options: list[str], district: District, pages: dict[int, str]
) -> None:
    """Where on the scanned page each polling station figure was read, in pixels of the page at 200 dpi."""
    for unit, station in zip(units, district.stations, strict=True):
        if station.page is None:
            continue
        cells = [*((option, VOTES) for option in options), (None, VALID), (None, INVALID), (None, REGISTERED)]
        for (option, measure), box in zip(cells, station.boxes, strict=True):
            if measure == REGISTERED and station.registered is None:
                continue
            p.add(
                "value_locator",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                page_id=pages[station.page],
                source_id=SOURCE_ID,
                x0=round(box[0], 1),
                y0=round(box[1], 1),
                x1=round(box[2], 1),
                y1=round(box[3], 1),
            )


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
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


def _values(p: Package, result_set: str, units: list[str], options: list[str], district: District) -> None:
    if district.stations:
        for unit, station in zip(units, district.stations, strict=True):
            for option, votes in zip(options, station.votes, strict=True):
                _value(p, result_set, unit, option, VOTES, votes)
            _value(p, result_set, unit, None, REGISTERED, station.registered)
            _value(p, result_set, unit, None, VALID, station.valid)
            _value(p, result_set, unit, None, INVALID, station.rejected)
        for measure, value in (
            (REGISTERED, district.registered),
            (VALID, district.valid),
            (INVALID, district.rejected),
        ):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option, candidate in zip(options, district.candidates, strict=True):
            if candidate.votes is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=candidate.votes)
        return
    unit = units[0]
    for option, candidate in zip(options, district.candidates, strict=True):
        _value(p, result_set, unit, option, VOTES, candidate.votes)
    _value(p, result_set, unit, None, REGISTERED, district.registered)
    _value(p, result_set, unit, None, BALLOTS, district.ballots)
    _value(p, result_set, unit, None, VALID, district.valid)
    _value(p, result_set, unit, None, INVALID, district.rejected)


def totals() -> dict[int, dict[str, tuple[int, int | None]]]:
    """Each election's national figures: what the units add up to, and what the commission prints."""
    found: dict[int, dict[str, tuple[int, int | None]]] = {}
    for year, election in elections().items():
        mine = {
            "registered": sum(d.registered or 0 for d in election.districts),
            "valid": sum(d.valid or 0 for d in election.districts),
            "rejected": sum(d.rejected or 0 for d in election.districts),
            "ballots": sum(d.ballots or 0 for d in election.districts),
        }
        found[year] = {measure: (value, election.totals.get(measure)) for measure, value in mine.items()}
    return found
