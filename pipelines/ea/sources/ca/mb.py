"""Manitoba's provincial general elections and by-elections, by voting area, from Elections Manitoba.

Elections Manitoba publishes, for each general election since 1999, the votes each candidate received in each voting
area, advance poll, institutional poll, and special ballot count of every electoral division. The 1999 and 2003
files are one long table for the province, 2007's is one long table for each division, and 2011 to 2023 give one
wide sheet for each division. It also publishes a summary of each general election (the member elected, the
registered voters, ballots cast, and each party's votes in each division), which every division built here is checked
against, and, for each by-election since 2012, a workbook of the same results by voting area. The electoral division
boundaries of 2018 come from Elections Manitoba's shapefile.
"""

import functools
import io
import re
import subprocess
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import Final
from urllib.parse import quote
from uuid import uuid5

import fastexcel
import numpy
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.licenses import PUBLIC_RECORD, UNLICENSED

SOURCE: Final = "ca.mb"
SITE: Final = "https://electionsmanitoba.ca/downloads/"
OFFICIAL: Final = "https://electionsmanitoba.ca/En/Results"
# Manitoba's polity and province area, which the federal pipeline holds under ids it minted itself.
MANITOBA: Final = "01a13fb6-eab7-7501-a0f9-490529e3c248"
PROVINCE_AREA: Final = "01a13fb6-eab7-7501-a0f9-48f8d6d38062"


def _url(path: str) -> str:
    return SITE + quote(path)


GENERAL_NUMBERS: Final = {1999: 37, 2003: 38, 2007: 39, 2011: 40, 2016: 41, 2019: 42, 2023: 43}
GENERAL_DAYS: Final = {
    1999: date(1999, 9, 21),
    2003: date(2003, 6, 3),
    2007: date(2007, 5, 22),
    2011: date(2011, 10, 4),
    2016: date(2016, 4, 19),
    2019: date(2019, 9, 10),
    2023: date(2023, 10, 3),
}


def by_poll_url(year: int) -> str:
    return _url(f"{GENERAL_NUMBERS[year]}GE.zip")


def summary_url(year: int) -> str:
    return _url(f"Summary_of_Results_GE{year}.{'xlsx' if year >= 2023 else 'xls'}")


def votes_url(year: int) -> str:
    return _url(f"{GENERAL_NUMBERS[year]}GE Summary of Votes Received.{'xlsx' if year >= 2023 else 'xls'}")


# Each by-election from 2012: its day, its electoral division, and the workbook of its results by voting area.
BY_ELECTIONS: Final = (
    (date(2012, 9, 4), "Fort Whyte", "By2012/Results/Results_by_Voting_Area_By2012_FW.xlsx"),
    (date(2014, 1, 28), "Arthur-Virden", "By2014/VAbyVA_results_Arthur-Virden_French.xlsx"),
    (date(2014, 1, 28), "Morris", "By2014/VAbyVA_results_Morris_French.xlsx"),
    (date(2015, 4, 21), "The Pas", "By2015/Results_By_Voting_Area_By2015_ThePas.xlsx"),
    (date(2017, 6, 13), "Point Douglas", "By2017/Results_By_Voting_Area_By2017_PointDouglas.xlsx"),
    (date(2018, 7, 17), "St. Boniface", "By2018/Results_By_Voting_Area_By2018_StBoniface_EngFr.xlsx"),
    (date(2022, 3, 22), "Fort Whyte", "BE2022FW/Results_By_Poll_By2022_FortWhyte_EngFr.xlsx"),
    (date(2022, 6, 7), "Thompson", "BE2022Thompson/Results_By_Poll_By2022_Thompson_EngFr.xlsx"),
    (date(2022, 12, 13), "Kirkfield Park", "BE2022KirkfieldPark/Results_By_Poll_By2022_KirkFieldPark_EngFr.xlsx"),
    (date(2024, 6, 18), "Tuxedo", "2024Tuxedo/Statement of Official Results-web.xlsx"),
    (date(2025, 3, 18), "Transcona", "2025Transcona/605 Statement of Votes_web.xlsx"),
    (date(2025, 8, 26), "Spruce Woods", "2025SpruceWoods/605 Statement of Votes_web.xlsx"),
    (date(2026, 7, 21), "The Pas-Kameesak", "2026ThePasKameesak/605_Statement_of_Votes_web.xlsx"),
)

# Divisions whose results-by-poll workbook disagrees with the statement of votes Elections Manitoba printed for the
# election (and with its summary of votes received), read from that statement instead.
STATEMENTS: Final = {
    "2003a": _url("2003_statvotes_a-l.pdf"),
    "2016a": _url("2016_statvotes_A-I.pdf"),
    "2016s": _url("2016_statvotes_S-W.pdf"),
}
DIFFERS: Final = (
    "The results-by-poll workbook's candidates' totals differ from the statement of votes and the summary of votes "
    "received"
)
FROM_STATEMENT: Final = {
    (2003, "Arthur-Virden"): (
        STATEMENTS["2003a"],
        (
            "The results-by-poll workbook misprints most of the New Democratic candidate's votes and the rejected "
            "and declined ballots of several polls"
        ),
    ),
    (2016, "Dawson Trail"): (STATEMENTS["2016a"], DIFFERS),
    (2016, "Tuxedo"): (STATEMENTS["2016s"], DIFFERS),
}

# The 2018 division boundaries: the rural divisions in one file and Winnipeg's in another.
BOUNDARY_FILES: Final = (
    _url("2018_Final_ED_Manitoba_Public_Urban.zip"),
    _url("2018_Final_ED_Winnipeg_Public_Urban.zip"),
)
# The electoral division maps that took effect with each redistribution, the general election that first used each,
# and the general election that first used the next.
VINTAGES: Final = {
    "1998": (date(1999, 9, 21), date(2011, 10, 4)),
    "2008": (date(2011, 10, 4), date(2019, 9, 10)),
    "2018": (date(2019, 9, 10), None),
}


def vintage(day: date) -> str:
    return next(v for v, (since, until) in VINTAGES.items() if since <= day and (until is None or day < until))


def fetch() -> None:
    for year in GENERAL_NUMBERS:
        raw.fetch(SOURCE, by_poll_url(year), suffix=".zip")
        for url in (summary_url(year), votes_url(year)):
            raw.fetch(SOURCE, url, suffix="." + url.rsplit(".", 1)[1])
    for _, _, path in BY_ELECTIONS:
        raw.fetch(SOURCE, _url(path), suffix=".xlsx")
    for url in STATEMENTS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in BOUNDARY_FILES:
        raw.fetch(SOURCE, url, suffix=".zip")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def _clean(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\xa0", " ")).strip()


def norm(name: str) -> str:
    """A division's name reduced to its English letters, so `BrandonWest`, `BRANDON WEST`, and `Brandon
    West/Brandon-Ouest` are one name."""
    english = re.split(r"[/\n]", name)[0]
    plain = unicodedata.normalize("NFKD", english).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", plain.casefold())


def _number(text: object) -> int | None:
    value = _clean(text).replace(",", "")
    if value in ("", "-", "~"):
        return None
    number = float(value)
    if number != int(number):
        raise ValueError(f"a count with a fraction: {text!r}")
    return int(number)


@dataclass
class Unit:
    label: str
    votes: list[int]
    rejected: int
    declined: int
    total: int | None
    electors: int | None


@dataclass
class District:
    name: str
    candidates: list[tuple[str, str]]
    units: list[Unit]
    totals: Unit
    notes: list[str] = field(default_factory=list)


def _sheet(data: bytes, sheet: str | int) -> list[list[str]]:
    reader = fastexcel.read_excel(data)
    frame = (
        reader.load_sheet_by_name(sheet, header_row=None, dtypes="string")
        if isinstance(sheet, str)
        else reader.load_sheet(sheet, header_row=None, dtypes="string")
    ).to_polars()
    return [[str(cell or "") for cell in row] for row in frame.iter_rows()]


def _sheets(data: bytes) -> list[str]:
    return list(fastexcel.read_excel(data).sheet_names)


HEAD: Final = re.compile(r"^(voting\s*area|voting\s*station|poll\s*no|poll\s*/|section\s*de\s*vote)", re.IGNORECASE)


def _header(rows: list[list[str]]) -> int | None:
    for index, row in enumerate(rows):
        cells = [_clean(cell) for cell in row if _clean(cell)]
        if cells and HEAD.match(cells[0]):
            return index
    return None


def _trim(rows: list[list[str]], head: int) -> list[list[str]]:
    """The rows without the empty columns before the heading's first cell."""
    start = next(i for i, cell in enumerate(rows[head]) if _clean(cell))
    return [row[start:] for row in rows]


def _candidate(label: str) -> tuple[str, str]:
    found = re.fullmatch(r"(.*?)\s*\(([^()]*)\)\s*", _clean(label))
    if found is None:
        raise ValueError(f"no party in the candidate heading {label!r}")
    return _clean(found.group(1)), _clean(found.group(2))


def wide(rows: list[list[str]], name: str) -> District:
    """One division's sheet of results by voting area: a heading row naming each candidate and their party, one row
    for each voting area or other poll, and a total row."""
    head = _header(rows)
    if head is None:
        raise ValueError(f"{name}: no voting area heading")
    rows = _trim(rows, head)
    columns: dict[str, int] = {}
    candidates: list[tuple[int, str, str]] = []
    for index, cell in enumerate(rows[head]):
        text = _clean(cell).casefold()
        if index == 0 or not text:
            continue
        for label, prefixes in (
            ("place", ("voting place", "centre de scrutin", "voting area")),
            ("rejected", ("rejected", "rejetés")),
            ("declined", ("declined", "refusés")),
            ("total", ("total",)),
            ("electors", ("registered", "électeurs inscrits", "final number", "total registered")),
        ):
            if text.startswith(prefixes):
                if label == "total" and "registered" in text:
                    label = "electors"
                columns[label] = index
                break
        else:
            printed, party = _candidate(cell)
            candidates.append((index, printed, party))
    units: list[Unit] = []
    totals: Unit | None = None
    seen: dict[str, int] = {}
    for row in rows[head + 1 :]:
        row = row + [""] * (len(rows[head]) - len(row))
        place = _clean(row[columns["place"]]) if "place" in columns else ""
        leading = [_clean(cell) for i, cell in enumerate(row[: candidates[0][0]]) if i != columns.get("place")]
        label = next((cell for cell in leading if cell), "")
        if any(len(_clean(row[index])) > 40 for index, _, _ in candidates):
            continue
        counts = [_number(row[index]) for index, _, _ in candidates]
        lowered = (label or place).casefold()
        if lowered.startswith(("sub total", "subtotal")):
            continue
        is_total = lowered.startswith(("total", "final total", "grand total")) or (
            not label and not place and any(count is not None for count in counts)
        )
        if all(count is None for count in counts) and not is_total:
            if lowered.startswith("turnout"):
                break
            continue
        unit = Unit(
            label=label or place,
            votes=[count or 0 for count in counts],
            rejected=_number(row[columns["rejected"]]) or 0,
            declined=_number(row[columns["declined"]]) or 0,
            total=_number(row[columns["total"]]) if "total" in columns else None,
            electors=_number(row[columns["electors"]]) if "electors" in columns else None,
        )
        if is_total:
            totals = unit
            break
        code = unit.label
        if " - " in code:
            code = code.split(" - ", 1)[0]
        code = _clean(code)
        seen[code] = seen.get(code, 0) + 1
        unit.label = code if seen[code] == 1 else f"{code} ({seen[code]})"
        units.append(unit)
    if totals is None:
        raise ValueError(f"{name}: no total row")
    district = District(name, [(printed, party) for _, printed, party in candidates], units, totals)
    for index, (_, printed, _) in enumerate(candidates):
        polls = sum(unit.votes[index] for unit in units)
        if polls != totals.votes[index]:
            district.notes.append(
                f"{_person(printed)}'s votes in the voting areas and other polls add to {polls:,}, and the total row "
                f"prints {totals.votes[index]:,}; the poll figures and the printed total are stored as printed."
            )
    return district


def _long(records: list[dict[str, str]], names: dict[str, str]) -> dict[str, District]:
    """The 1999, 2003, and 2007 tables: one row for each candidate in each poll, with the poll's declined and rejected
    ballots (and, from 2003, its registered voters) repeated on each."""
    found: dict[str, tuple[list[tuple[str, str]], dict[str, Unit]]] = {}
    for record in records:
        division = names[norm(record["division"])]
        candidates, units = found.setdefault(division, ([], {}))
        code = _clean(record["poll"])
        if code.casefold().startswith("psv") and not record["votes"]:
            continue
        unit = units.get(code)
        if unit is None:
            unit = units[code] = Unit(
                label=code,
                votes=[],
                rejected=_number(record["rejected"]) or 0,
                declined=_number(record["declined"]) or 0,
                total=_number(record.get("total")),
                electors=_number(record.get("electors")),
            )
        if not _clean(record["candidate"]).strip(", "):
            continue
        candidate = (_clean(record["candidate"]), _clean(record["party"]) or "Ind.")
        if candidate not in candidates:
            candidates.append(candidate)
        index = candidates.index(candidate)
        unit.votes += [0] * (index + 1 - len(unit.votes))
        unit.votes[index] += _number(record["votes"]) or 0
    districts: dict[str, District] = {}
    for division, (candidates, units) in found.items():
        for unit in units.values():
            unit.votes += [0] * (len(candidates) - len(unit.votes))
        polls = list(units.values())
        totals = Unit(
            "Total",
            [sum(unit.votes[i] for unit in polls) for i in range(len(candidates))],
            sum(unit.rejected for unit in polls),
            sum(unit.declined for unit in polls),
            None,
            None,
        )
        districts[division] = District(division, candidates, polls, totals)
    return districts


LONG_COLUMNS: Final = {
    "ElectoralDivision": "division",
    "PollID": "poll",
    "CandidateName": "candidate",
    "PartyAbbrev": "party",
    "TotalVotes": "votes",
    "Declined": "declined",
    "Rejected": "rejected",
    "RegisteredVoters": "electors",
}
LONG_2007: Final = (
    "division",
    "poll",
    "place",
    "last",
    "first",
    "party",
    "votes",
    "rejected",
    "declined",
    "total",
    "electors",
)


def _members(name: str) -> list[str]:
    return [n for n in zipfile.ZipFile(raw.cached(SOURCE, by_poll_url(int(name))).path).namelist() if n[-1] != "/"]


def _read(year: int, member: str) -> bytes:
    return zipfile.ZipFile(raw.cached(SOURCE, by_poll_url(year)).path).read(member)


def _file_division(member: str) -> str:
    base = member.rsplit("/", 1)[-1]
    return re.split(r"_(?:Votes|Candidate)", base, maxsplit=1)[0]


@functools.cache
def canonical(set_name: str) -> dict[str, str]:
    """Each division's name as Elections Manitoba spells it, keyed by its reduced form."""
    if set_name == "1998":
        data = _read(1999, _members("1999")[0])
        rows = _sheet(data, 0)
        names = {_clean(row[0]) for row in rows[1:] if _clean(row[0])}
    else:
        year = 2011 if set_name == "2008" else 2019
        rows = _sheet(raw.cached(SOURCE, summary_url(year)).path.read_bytes(), 0)
        head = next(i for i, row in enumerate(rows) if _clean(row[0]).casefold().startswith("electoral"))
        names = set()
        for row in rows[head + 1 :]:
            text = _clean(row[0])
            if not text or text.casefold().startswith(("total", "turnout")):
                continue
            names.add(text)
    return {norm(name): name for name in sorted(names)}


@functools.cache
def general(year: int) -> list[District]:
    names = canonical(vintage(GENERAL_DAYS[year]))
    members = _members(str(year))
    districts: dict[str, District] = {}
    if year in (1999, 2003):
        rows = _sheet(_read(year, members[0]), 0)
        heading = rows[0]
        records = [
            {LONG_COLUMNS[h]: cell for h, cell in zip(heading, row, strict=True) if h in LONG_COLUMNS}
            for row in rows[1:]
        ]
        districts = _long(records, names)
    elif year == 2007:
        records = []
        for member in members:
            for row in _sheet(_read(year, member), 0)[1:]:
                if not _clean(row[0]) or len([c for c in row if c]) < 6:
                    continue
                if len(row) > len(LONG_2007) and _clean(row[len(LONG_2007)]):
                    # A polling place's address spilled into the next cell, pushing the rest of the row right.
                    row = [*row[:2], f"{row[2]}, {_clean(row[3])}", *row[4:]]
                if len(row) < len(LONG_2007):
                    row = row + [""] * (len(LONG_2007) - len(row))
                record = dict(zip(LONG_2007, row[: len(LONG_2007)], strict=True))
                if norm(record["division"]) not in names:
                    continue
                record["candidate"] = f"{_clean(record['last'])}, {_clean(record['first'])}"
                records.append(record)
        districts = _long(records, names)
    elif year == 2011:
        data = _read(year, members[0])
        for sheet in _sheets(data):
            if sheet.casefold() == "summary":
                continue
            name = names[norm(sheet)]
            districts[name] = wide(_sheet(data, sheet), name)
    else:
        for member in members:
            data = _read(year, member)
            sheets = _sheets(data)
            chosen = [s for s in sheets if _header(_sheet(data, s)) is not None][-1]
            name = names[norm(_file_division(member))]
            districts[name] = wide(_sheet(data, chosen), name)
    for (fixed, name), (url, why) in FROM_STATEMENT.items():
        if fixed == year:
            replaced = statement(url, name, districts[name])
            replaced.notes.append(
                f"{why}, so the votes by poll come from the statement of votes Elections Manitoba printed for the "
                "election, whose totals agree with its summary of votes received."
            )
            districts[name] = replaced
    if set(districts) != set(names.values()):
        raise ValueError(f"{year}: divisions {sorted(set(names.values()) ^ set(districts))} do not match")
    found = [districts[name] for name in sorted(districts)]
    _check(year, found)
    return found


NUMERIC: Final = re.compile(r"[\d,]+|-")


def statement(url: str, name: str, workbook: District) -> District:
    """One division's table in a printed statement of votes: a row for each poll with each candidate's votes (in the
    order of the workbook's candidates, by surname), the rejected and declined ballots, the ballots cast, and the
    registered voters."""
    result = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    )
    lines = result.stdout.split("\n")
    start = next(i for i, line in enumerate(lines) if line.strip().startswith(name.upper()))
    candidates = sorted(workbook.candidates, key=lambda c: c[0])
    width = len(candidates) + 4
    units: list[Unit] = []
    totals: Unit | None = None
    for line in lines[start:]:
        tokens = line.split()
        if len(tokens) <= width or not all(NUMERIC.fullmatch(t) for t in tokens[-width:]):
            continue
        label = " ".join(tokens[:-width])
        numbers = [_number(n) for n in tokens[-width:]]
        votes = [n or 0 for n in numbers[: len(candidates)]]
        rejected, declined, cast, electors = numbers[len(candidates) :]
        unit = Unit(label, votes, rejected or 0, declined or 0, cast, electors)
        if label.casefold().startswith("total"):
            totals = unit
            break
        code = label.split()[0] if re.match(r"^(\d|Adv-|IMP-|IP-)", label) else label.split(" / ")[0]
        unit.label = code
        if sum(votes) + unit.rejected + unit.declined != cast:
            raise ValueError(f"{name}: poll {code} does not add to its ballots cast")
        units.append(unit)
    if totals is None:
        raise ValueError(f"{name}: no total row in the statement of votes")
    for index in range(len(candidates)):
        if sum(unit.votes[index] for unit in units) != totals.votes[index]:
            raise ValueError(f"{name}: the statement's polls do not add to its total row")
    return District(name, candidates, units, totals)


def by_election(day: date, division: str) -> District:
    path = next(p for d, n, p in BY_ELECTIONS if d == day and n == division)
    data = raw.cached(SOURCE, _url(path)).path.read_bytes()
    sheets = [s for s in _sheets(data) if _header(_sheet(data, s)) is not None]
    names = canonical(vintage(day))
    name = names[norm(division)]
    found = wide(_sheet(data, sheets[-1]), name)
    winner = max(range(len(found.candidates)), key=lambda i: found.totals.votes[i])
    rows = _sheet(data, sheets[-1])
    for row in rows[: _header(rows) or 0]:
        for cell in row:
            if "candidate elected" in cell.casefold():
                elected = _clean(cell.split("\n", 1)[-1])
                if norm(_surname(elected)) != norm(_surname(found.candidates[winner][0])):
                    raise ValueError(
                        f"{day} {division}: {elected} is elected, the most votes are {found.candidates[winner]}"
                    )
    return found


# Party labels as the results files print them, to a code for each party.
LABELS: Final = {
    "pc": "pc",
    "pc manitoba": "pc",
    "ndp": "ndp",
    "npd": "ndp",
    "ndp / npd": "ndp",
    "ndp/npd": "ndp",
    "lib.": "lib",
    "lib": "lib",
    "libéral": "lib",
    "libéeral": "lib",
    "mlp": "lib",
    "mlp/plm": "lib",
    "gpm": "gpm",
    "gpm/pvm": "gpm",
    "gpm/verts": "gpm",
    "parti vert du manitoba": "gpm",
    "cpc-m": "cpcm",
    "cpc_m": "cpcm",
    "cpc": "cpcm",
    "cpc-m/comm.": "cpcm",
    "lpm": "lpm",
    "lpm/plm": "lpm",
    "mp": "mp",
    "fpm": "fpm",
    "kp": "kp",
    "mbfwd": "mbfwd",
    "mf": "mf",
    "ind.": "ind",
    "ind": "ind",
    "indépendant": "ind",
}
# Each party's name and abbreviation, as Elections Manitoba's key to political affiliations gives them.
PARTY_NAMES: Final = {
    "pc": ("Progressive Conservative Party of Manitoba", "PC"),
    "ndp": ("New Democratic Party of Manitoba", "NDP"),
    "lib": ("Manitoba Liberal Party", "Lib."),
    "gpm": ("Green Party of Manitoba", "GPM"),
    "cpcm": ("Communist Party of Canada - Manitoba", "CPC-M"),
    "lpm": ("Libertarian Party of Manitoba", "LPM"),
    "mp": ("Manitoba Party", "MP"),
    "fpm": ("Freedom Party of Manitoba", "FPM"),
    "kp": ("Keystone Party of Manitoba", "KP"),
    "mbfwd": ("Manitoba Forward", "MBFWD"),
    "mf": ("Manitoba First", "MF"),
}


def party_code(label: str) -> str:
    text = _clean(label).casefold()
    code = LABELS.get(text) or LABELS.get(text.split("/")[0].strip())
    if code is None:
        raise ValueError(f"unknown party label {label!r}")
    return code


@functools.cache
def summary(year: int) -> dict[str, tuple[str, dict[str, int], int | None, int | None]]:
    """Each division's line in the summary of a general election: the member elected, each party's votes, the
    registered voters, and the votes cast."""
    rows = _sheet(raw.cached(SOURCE, summary_url(year)).path.read_bytes(), 0)
    head = next(i for i, row in enumerate(rows) if _clean(row[0]).casefold().startswith("electoral"))
    heading = [_clean(cell).casefold() for cell in rows[head]]
    parties: dict[int, str] = {}
    for index, text in enumerate(heading):
        label = re.split(r"\s", text.replace("/ ", "/"))[0] if text else ""
        if label in LABELS:
            parties[index] = LABELS[label]
        elif label.split("/")[0] in LABELS and index > 4:
            parties[index] = LABELS[label.split("/")[0]]
    names = canonical(vintage(GENERAL_DAYS[year]))
    found: dict[str, tuple[str, dict[str, int], int | None, int | None]] = {}
    for row in rows[head + 1 :]:
        text = _clean(row[0])
        if not text or norm(text) not in names:
            continue
        votes: dict[str, int] = {}
        for index, code in parties.items():
            value = _number(row[index]) if index < len(row) else None
            if value:
                votes[code] = votes.get(code, 0) + value
        found[names[norm(text)]] = (_clean(row[1]), votes, _number(row[2 if year < 2011 else 3]), None)
    return found


# The 2019 summary of results holds the unofficial election-night count; its official counterpart is the sheet of
# votes received, so 2019's party votes are checked only through that sheet.
UNOFFICIAL_SUMMARY: Final = {2019}


@functools.cache
def votes_received(year: int) -> dict[str, list[int]]:
    """Each division's candidates' votes, from the authority's summary of votes received for a general election."""
    data = raw.cached(SOURCE, votes_url(year)).path.read_bytes()
    rows = max((_sheet(data, sheet) for sheet in _sheets(data)), key=len)
    names = canonical(vintage(GENERAL_DAYS[year]))
    found: dict[str, list[int]] = {}
    current: str | None = None
    for row in rows[1:]:
        if len(row) < 4:
            continue
        if norm(row[0]) in names:
            current = names[norm(row[0])]
        candidate = _clean(row[1]).casefold()
        votes = _number(row[3]) if re.fullmatch(r"[\d,.]+", _clean(row[3])) else None
        if current is None or votes is None or not candidate or candidate.startswith(("declined", "rejected")):
            continue
        found.setdefault(current, []).append(votes)
    return {name: sorted(votes) for name, votes in found.items()}


# Divisions whose candidates' votes in the summary of votes received differ from the results by poll, the statement
# of votes, and the summary of results, which all agree; the results by poll are kept, and the difference noted.
RECEIVED_DIFFERS: Final = {
    (2003, "Swan River"): (
        "Elections Manitoba's summary of votes received prints 547, 2,252, and 4,816 votes for the three candidates, "
        "more than the results by poll, the statement of votes, and the summary of results, which agree on 538, 2,223, "
        "and 4,701 and list no polls numbered 29 or 30. The results by poll are stored."
    ),
    (2003, "The Maples"): (
        "Elections Manitoba's summary of votes received lists only two of the division's three candidates; the "
        "results by poll and the summary of results list all three."
    ),
}


def _check(year: int, districts: list[District]) -> None:
    """Each division's candidates' votes agree with the authority's summary of votes received, and its party votes
    and member elected with the summary of results."""
    printed = summary(year)
    received = votes_received(year)
    for d in districts:
        if (year, d.name) in RECEIVED_DIFFERS:
            d.notes.append(RECEIVED_DIFFERS[(year, d.name)])
        elif sorted(d.totals.votes) != received[d.name]:
            raise ValueError(f"{year} {d.name}: built {sorted(d.totals.votes)}, received prints {received[d.name]}")
        member, votes, _, _ = printed[d.name]
        got: dict[str, int] = {}
        for (_, party), count in zip(d.candidates, d.totals.votes, strict=True):
            code = party_code(party)
            if count:
                got[code] = got.get(code, 0) + count
        if got != votes and year not in UNOFFICIAL_SUMMARY and (year, d.name) not in FROM_STATEMENT:
            raise ValueError(f"{year} {d.name}: built {got}, the summary prints {votes}")
        winner = max(range(len(d.candidates)), key=lambda i: d.totals.votes[i])
        if norm(_surname(d.candidates[winner][0])) not in norm(member):
            raise ValueError(
                f"{year} {d.name}: the summary elects {member!r}, the most votes are {d.candidates[winner]}"
            )


@functools.cache
def _surnames() -> dict[str, str]:
    """Words of candidates' names as the authority spells them in mixed case, for the files that print surnames in
    capitals."""
    known: dict[str, str] = {}
    for year in (1999, 2003):
        for d in general(year):
            for printed, _ in d.candidates:
                for word in re.split(r"[\s,]+", printed):
                    if word and not word.isupper():
                        known.setdefault(word.casefold(), word)
    return known


def _capitalize(word: str) -> str:
    parts = []
    for piece in re.split(r"([-'’])", word):
        text = piece.capitalize() if piece.isalpha() else piece
        if text.startswith("Mc") and len(text) > 3:
            text = "Mc" + text[2:].capitalize()
        parts.append(text)
    return "".join(parts)


def _surname(printed: str) -> str:
    return printed.partition(",")[0]


def _person(printed: str) -> str:
    """`MAGUIRE, Larry N.` is Larry N. Maguire."""
    last, _, first = printed.partition(",")
    words = []
    for word in _clean(last).split():
        if len(word) > 1 and word.isupper():
            word = _surnames().get(word.casefold()) or _capitalize(word)
        words.append(word)
    return _clean(f"{_clean(first)} {' '.join(words)}")


def area(set_name: str, name: str) -> str:
    return key("area", "ed", set_name, norm(name))


def boundary_set(set_name: str) -> str:
    return key("boundary_set", "ca.mb", set_name)


EM = key("source", "elections_manitoba")
BOUNDARY_SOURCE = key("source", "boundaries")
ASSEMBLY = key("body", "legislative_assembly")
FPTP = key("electoral_system", "fptp")


def _wgs(shape: shapely.Geometry, crs: str) -> shapely.Geometry:
    project = pyproj.Transformer.from_crs(pyproj.CRS.from_wkt(crs), 4326, always_xy=True)
    moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def _districts_2018() -> dict[str, shapely.Geometry]:
    shapes: dict[str, shapely.Geometry] = {}
    for url in BOUNDARY_FILES:
        archive = zipfile.ZipFile(raw.cached(SOURCE, url).path)
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist() if "." in name}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), dbf=io.BytesIO(parts["dbf"]), shx=io.BytesIO(parts["shx"])
        )
        crs = parts["prj"].decode()
        fields = [f[0] for f in reader.fields[1:]]
        column = fields.index("ED")
        merged: dict[str, list[shapely.Geometry]] = {}
        for record, shape in zip(reader.records(), reader.shapes(), strict=True):
            if record is None or shape is None:
                raise ValueError("a boundary record without its shape")
            geometry = shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__))
            merged.setdefault(norm(str(record[column])), []).append(geometry)
        for name, parts_ in merged.items():
            if name in shapes:
                raise ValueError(f"the division {name} is in both boundary files")
            shapes[name] = _wgs(shapely.union_all(parts_), crs)
    return shapes


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/ge-{year}" for year in GENERAL_DAYS),
        *(f"{SOURCE}/be-{day}-{changeset.slug(name)}" for day, name, _ in BY_ELECTIONS),
    ]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Manitoba's electoral divisions: the 57 of each of the 1998, 2008, and 2018 maps, with the 2018 divisions' "
        "boundaries from Elections Manitoba's shapefile",
    )
    p.external |= {MANITOBA, PROVINCE_AREA}
    p.add(
        "source",
        id=BOUNDARY_SOURCE,
        kind="official_body",
        publisher="Elections Manitoba",
        title="Electoral division boundaries, 2018: rural Manitoba and Winnipeg (shapefiles)",
        url="https://electionsmanitoba.ca/En/Resources/Maps",
        license=UNLICENSED,
        license_url=None,
        attribution="Elections Manitoba",
    )
    for url in BOUNDARY_FILES:
        p.artifact(raw.cached(SOURCE, url), BOUNDARY_SOURCE)
    p.add(
        "scheme",
        code="ca.mb",
        polity_id=MANITOBA,
        kind="electoral_district",
        rank=1,
        noun="electoral division",
        code_system=None,
    )
    p.add(
        "scheme",
        code="ca.mb_va",
        polity_id=MANITOBA,
        kind="precinct",
        rank=2,
        noun="voting area",
        code_system=None,
    )
    shapes = _districts_2018()
    for set_name, (since, until) in VINTAGES.items():
        p.add(
            "boundary_set",
            id=boundary_set(set_name),
            scheme_code="ca.mb",
            vintage=set_name,
            valid_from=since,
            valid_to=until,
            as_of=since,
        )
        for name in canonical(set_name).values():
            id = area(set_name, name)
            p.add("area", id=id, scheme_code="ca.mb")
            p.add("area_code", area_id=id, code=f"{set_name}:{norm(name)}", valid_from=since, valid_to=until)
            p.add("area_parent", area_id=id, parent_id=PROVINCE_AREA, valid_from=since, valid_to=until)
            p.name(id, name, "en", "official")
            if set_name == "2018":
                shape = shapes[norm(name)]
                p.add(
                    "area_geometry",
                    area_id=id,
                    valid_from=since,
                    valid_to=until,
                    geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                    label_point=shapely.to_wkb(
                        shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                    ),
                    quality="official",
                    source_id=BOUNDARY_SOURCE,
                )
    return p


def _all_parties() -> set[str]:
    used: set[str] = set()
    for year in GENERAL_DAYS:
        used |= {party_code(party) for d in general(year) for _, party in d.candidates}
    for day, name, _ in BY_ELECTIONS:
        used |= {party_code(party) for _, party in by_election(day, name).candidates}
    return used


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Manitoba's parties, the Legislative Assembly, and its single-member plurality system, from Elections Manitoba",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {MANITOBA}
    p.add(
        "source",
        id=EM,
        kind="official_body",
        publisher="Elections Manitoba",
        title="Election results: results by voting area and summaries of results",
        url=OFFICIAL,
        license=PUBLIC_RECORD,
        license_url=None,
        attribution="Elections Manitoba",
    )
    for year in GENERAL_DAYS:
        for url in (by_poll_url(year), summary_url(year)):
            p.artifact(raw.cached(SOURCE, url), EM)
    for _, _, path in BY_ELECTIONS:
        p.artifact(raw.cached(SOURCE, _url(path)), EM)
    p.add("body", id=ASSEMBLY, polity_id=MANITOBA, kind="unicameral")
    p.name(ASSEMBLY, "Legislative Assembly of Manitoba", "en", "official")
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=MANITOBA,
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
        body_md="One member is elected for each electoral division, by the most votes.",
        lang="en",
    )
    for code in sorted(_all_parties() - {"ind"}):
        official, abbreviation = PARTY_NAMES[code]
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=MANITOBA)
        p.add("external_id", entity_id=id, scheme="ca.mb.party", value=code)
        p.name(id, official, "en", "official")
        p.name(id, abbreviation, "en", "abbreviation", False)
    return p


ACCOUNTING_NOTE: Final = (
    "Elections Manitoba prints, for each voting area and each other poll, each candidate's votes, the rejected "
    "ballots, the declined ballots (a ballot the elector returned unmarked, declining to vote), and, in most years, "
    "the total ballots cast and the registered voters. Valid votes here are the sum of the candidates' votes, "
    "rejected ballots are stored as invalid, declined ballots as blank, and ballots cast as printed or, where a file "
    "does not print them, as the sum of the three. Advance polls, absentee and homebound votes, write-in ballots, and "
    "ballots cast outside the division are reported for the division as a whole and stored as aggregate units; "
    "their registered voters, printed as zero, are not stored."
)


def _kind(code: str) -> str:
    return "standard" if code[:1].isdigit() else "aggregate"


def _overcount(p: Package, found: list[tuple[str, District]]) -> None:
    """Note, for each result set, the polls that cast more ballots than their registered voters."""
    total = 0
    for result_set, d in found:
        over = [unit.label for unit in d.units if unit.electors and _cast(unit) > unit.electors]
        if not over:
            continue
        total += len(over)
        p.add(
            "note",
            id=key("note", result_set, "overcount"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"Elections Manitoba prints more ballots cast than registered voters in {', '.join(over)}; "
            "electors who registered at the poll on election day and ballots cast at a shared polling place can "
            "exceed the voters on the list. The figures are stored as printed.",
            lang="en",
        )
    if total:
        p.accept(
            "accounting.turnout",
            f"Elections Manitoba prints more ballots cast than registered voters in {total} polls of this election; "
            "stored as printed and noted on each result set",
        )


def _mismatched(p: Package, items: list[District]) -> None:
    """Accept the divisions whose total row the authority prints differently from the sum of its polls."""
    off = [
        d.name
        for d in items
        if any(sum(unit.votes[i] for unit in d.units) != d.totals.votes[i] for i in range(len(d.candidates)))
    ]
    if off:
        p.accept(
            "accounting.declared",
            f"Elections Manitoba's total row for {', '.join(off)} is larger than the sum of the polls it prints; the "
            "polls and the printed total are both stored as printed and the difference is noted on the result set",
        )


def _cast(unit: Unit) -> int:
    return unit.total if unit.total is not None else sum(unit.votes) + unit.rejected + unit.declined


def _election_package(
    p: Package,
    day: date,
    election: str,
    items: list[District],
    path: str,
) -> None:
    set_name = vintage(day)
    found: list[tuple[str, District]] = []
    _mismatched(p, items)
    for d in items:
        tag = f"{day}:{norm(d.name)}"
        ed = area(set_name, d.name)
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=ASSEMBLY,
            district_area_id=ed,
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="counted",
            boundary_set_id=boundary_set(set_name),
        )
        p.name(contest, d.name, "en", "official")
        p.add("slug", path=f"{path}/{changeset.slug(d.name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", tag)
        found.append((result_set, d))
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=EM,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=ACCOUNTING_NOTE,
            lang="en",
        )
        for index, text in enumerate(d.notes):
            p.add(
                "note",
                id=key("note", result_set, "source", index),
                entity_id=result_set,
                kind="caveat",
                body_md=text,
                lang="en",
            )
        winner = max(range(len(d.candidates)), key=lambda i: d.totals.votes[i])
        options = []
        for index, (printed, label) in enumerate(d.candidates):
            party = party_code(label)
            party_id = None if party == "ind" else key("party", party)
            option = key("option", tag, index + 1)
            person = key("person", tag, index + 1)
            options.append(option)
            p.add("person", id=person)
            p.name(person, _person(printed), "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party_id,
                ballot_position=index + 1,
                ballot_label=printed,
            )
            p.add(
                "candidacy",
                id=key("candidacy", tag, index + 1),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=printed,
                member_of_party_id=party_id,
                is_independent=party == "ind",
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if index == winner else "lost")
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=d.totals.votes[index])
        for unit in d.units:
            id = key("unit", tag, unit.label)
            kind = _kind(unit.label)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=MANITOBA,
                scheme_code="ca.mb_va",
                code=f"{tag}:{unit.label}",
                area_relation="equals" if kind == "aggregate" else "within",
                area_id=ed,
                kind=kind,
            )
            valid = sum(unit.votes)
            cast = _cast(unit)
            accounting = [
                (2, unit.electors if unit.electors else None),
                (3, cast),
                (4, valid),
                (5, unit.rejected),
                (6, unit.declined),
            ]
            for measure, value in accounting:
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=id,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
            for option, count in zip(options, unit.votes, strict=True):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=id,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=count,
                )
    _overcount(p, found)


def election(year: int) -> Package:
    day = GENERAL_DAYS[year]
    p = Package(
        f"{SOURCE}/ge-{year}",
        SOURCE,
        f"The {year} Manitoba general election by voting area, from Elections Manitoba",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {MANITOBA, PROVINCE_AREA}
    p.uses(raw.cached(SOURCE, by_poll_url(year)))
    p.uses(raw.cached(SOURCE, summary_url(year)))
    p.uses(raw.cached(SOURCE, votes_url(year)))
    for fixed, _ in FROM_STATEMENT:
        if fixed == year:
            p.uses(raw.cached(SOURCE, FROM_STATEMENT[(fixed, _)][0]))
    election_id = key("election", day)
    path = f"ca/mb/{day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=MANITOBA,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{year} Manitoba general election", "en", "official")
    _election_package(p, day, election_id, general(year), path)
    return p


def by_election_package(day: date, division: str) -> Package:
    d = by_election(day, division)
    path_part = changeset.slug(division)
    p = Package(
        f"{SOURCE}/be-{day}-{path_part}",
        SOURCE,
        f"The {d.name} by-election of {day} by voting area, from Elections Manitoba",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {MANITOBA, PROVINCE_AREA}
    file = next(path for d_, n, path in BY_ELECTIONS if d_ == day and n == division)
    p.uses(raw.cached(SOURCE, _url(file)))
    election_id = key("election", day, norm(division))
    path = f"ca/mb/{day}-{path_part}-by-election"
    p.add(
        "election",
        id=election_id,
        polity_id=MANITOBA,
        kind="by_election",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{d.name} by-election, {day:%-d %B %Y}", "en", "official")
    _election_package(p, day, election_id, [d], path)
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for year in GENERAL_DAYS:
        election(year).write()
    for day, name, _ in BY_ELECTIONS:
        by_election_package(day, name).write()


def emit() -> None:
    package.run("load", packages())
