"""New Brunswick's provincial general elections and by-elections, by polling division, from Elections NB.

The authority publishes a report of the Chief Electoral Officer for every general election since 1967 and, for the
elections since 1995, workbooks of poll-by-poll results. Two layouts of workbook exist. Through 2014 (and for the
by-elections through 2013) each electoral district has a sheet of its own, with one row for each polling division,
the electors on the list, each candidate's votes, and the rejected ballots. From 2015 on one workbook holds a block
for each district, with each candidate's votes for each polling division and nothing else. The reports of 1967 to
1991 hold no workbook, and their poll-by-poll tables do not survive the scan's text layer, so those elections are read
from the summary of votes each report prints for each district.
"""

import functools
import io
import re
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import Final
from uuid import uuid5

import fastexcel
import numpy
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.licenses import UNLICENSED

SOURCE: Final = "ca.nb"
SITE: Final = "https://www.electionsnb.ca"
INDEX: Final = SITE + "/content/enb/en/resources/publications/election-results.html"
# Canada's province polity and province area, which the federal pipeline holds under ids it minted itself.
NEW_BRUNSWICK: Final = "01a13fb6-eab7-7501-a0f9-4902d09fd447"
PROVINCE_AREA: Final = "01a13fb6-eab7-7501-a0f9-48f50feff417"

_PDF = "/content/dam/enb/pdf/"
_XLS = "/content/dam/enb/xls/"
# Each general election: the date, the report of the Chief Electoral Officer, and the workbook archive, if any.
GENERALS: Final[dict[int, tuple[date, str, str | None]]] = {
    1967: (
        date(1967, 10, 23),
        _PDF + "Twenty-Sixth-General-Election-1967-Report-of-the-Chief-Electoral-Officer.pdf",
        None,
    ),
    1970: (
        date(1970, 10, 26),
        _PDF + "Twenty-Seventh-General-Election-1970-Report-of-the-Chief-Electoral-Officer.pdf",
        None,
    ),
    1974: (
        date(1974, 11, 18),
        _PDF + "Twenty-Eighth-General-Election-1974-Report-of-the-Chief-Electoral-Officer.pdf",
        None,
    ),
    1978: (
        date(1978, 10, 23),
        _PDF + "Twenty-Ninth-General-Election-1978-Report-of-the-Chief-Electoral-Officer.pdf",
        None,
    ),
    1982: (
        date(1982, 10, 12),
        _PDF + "Thirtieth-General-Election-1982-Report-of-the-Chief-Electoral-Officer.pdf",
        None,
    ),
    1987: (
        date(1987, 10, 13),
        _PDF + "Thirty-First-General%20Election-1987-Report-of-the-Chief-Electoral-Officer.pdf",
        None,
    ),
    1991: (
        date(1991, 9, 23),
        _PDF + "Thirty-Second-General-Election-1991-Report-of-the-Chief-Electoral-Officer.pdf",
        None,
    ),
    1995: (date(1995, 9, 11), _PDF + "1995ProvRpt.pdf", _XLS + "prov1995-09-11/Results-Resultats-1995-09-11.zip"),
    1999: (date(1999, 6, 7), _PDF + "1999ProvRpt.pdf", _XLS + "prov1999-06-07/Results-Resultats-1999-06-07.zip"),
    2003: (date(2003, 6, 9), _PDF + "2003ProvRpt.pdf", _XLS + "prov2003-06-09/Results-Resultats-2003-06-09.zip"),
    2006: (date(2006, 9, 18), _PDF + "2006ProvRpt.pdf", _XLS + "prov2006-09-18/Results-Resultats-2006-09-18.zip"),
    2010: (date(2010, 9, 27), _PDF + "2010ProvRpt.pdf", _XLS + "prov2010-09-27/Results-Resultats-2010-09-27.zip"),
    2014: (date(2014, 9, 22), _PDF + "2014ProvRpt1.pdf", _XLS + "prov2014-09-22/Results-Resultats-2014-09-22.zip"),
    2018: (
        date(2018, 9, 24),
        _PDF + "2018ProvRpt.pdf",
        _XLS + "prov2018-09-24/2018-09-24-PROV-Results-Resultats.zip",
    ),
    2020: (
        date(2020, 9, 14),
        _PDF + "2020-prov-rpt.pdf",
        _XLS + "prov2020-09-14/2020-09-14%20PROV-Results-Resultats.zip",
    ),
    2024: (
        date(2024, 10, 21),
        _PDF + "october-21-2024-forty-first-provincial-general-election.pdf",
        _XLS + "prov-2024-10-21/2024-10-21-Results-Resultats.zip",
    ),
}
# Each by-election day: the report, then the workbook files, a lone workbook or an archive.
BY_ELECTIONS: Final[dict[date, tuple[str, str]]] = {
    date(1997, 6, 23): (_PDF + "1997Jun23VictoriaTobique.pdf", _XLS + "prov/1997-06-23-Recapitulation-ED-CE-51.xls"),
    date(1997, 11, 17): (
        _PDF + "1997Nov17MiramichiBDVTantramar.pdf",
        _XLS + "prov/1997-11-17-Recapitulation-ED-CE-12-19.zip",
    ),
    date(1998, 10, 19): (
        _PDF + "1998Oct19KentMonctonEastFredSouth.pdf",
        _XLS + "prov/1998-10-19-Recapitulation-ED-CE-16-21-45.zip",
    ),
    date(2001, 2, 5): (
        _PDF + "2001Feb05Campbellton-Caraquet.pdf",
        _XLS + "prov/2001-02-05-Recapitulation-ED-CE-02-07.zip",
    ),
    date(2001, 4, 23): (_PDF + "2001Apr23KentSouth-KentSud.pdf", _XLS + "prov/2001-04-23-Recapitulation-ED-CE-17.xls"),
    date(2004, 10, 4): (_PDF + "2004Oct04ShediacCapPele.pdf", _XLS + "prov/2004-10-04-Recapitulation-ED-CE-18.xls"),
    date(2005, 11, 14): (
        _PDF + "2005Nov14SaintJohnHarbour.pdf",
        _XLS + "prov/2005-11-14-Recapitulation-ED-CE-34.xls",
    ),
    date(2007, 3, 5): (_PDF + "2007Mar05MonctonEast.pdf", _XLS + "prov/2007-03-05-Recapitulation-ED-CE-21.xls"),
    date(2008, 11, 3): (
        _PDF + "2008Nov03NewMarylandSunburyWest.pdf",
        _XLS + "prov/2008-11-03-Recapitulation-ED-CE-46.xls",
    ),
    date(2009, 3, 9): (
        _PDF + "2009Mar09Restigouche-La-Vallee.pdf",
        _XLS + "prov/2009-03-09-Recapitulation-ED-CE-53.xls",
    ),
    date(2012, 6, 25): (_PDF + "2012-06-25-Rothesay.pdf", _XLS + "prov/2012-06-25-Recapitulation-ED-CE-32.xlsx"),
    date(2013, 4, 15): (_PDF + "2013-04-15-Kent.pdf", _XLS + "prov/2013-04-15-Recapitulation-ED-CE-15.xlsx"),
    date(2014, 11, 17): (
        _PDF + "2014-11-17SaintJohnEast-Est.pdf",
        _XLS + "prov/2014-11-17-Results-Resultats-ED-CE-30.zip",
    ),
    date(2015, 10, 5): (
        _PDF + "2015-10-05Carleton.pdf",
        _XLS + "prov/2015-10-05%20Results-Resultats-ED-CE-45.zip",
    ),
    date(2022, 6, 20): (
        _PDF + "2022-06-20-miramichi-bay-neguac.pdf",
        _XLS + "prov2022-06-20/2022-06-20%20Results-R%C3%A9sultats.zip",
    ),
    date(2023, 4, 24): (
        _PDF + "2023-04-24-miramichi-bay.pdf",
        _XLS + "prov2023-04-24/2023-04-24%20Results-R%C3%A9sultats.zip",
    ),
    date(2025, 10, 6): (
        _PDF + "2025-10-6-miramichi-west.pdf",
        _XLS + "prov-2025-10-06/2025-10-06-Results-Resultats.zip",
    ),
}


def _url(path: str) -> str:
    return SITE + urllib.parse.quote(urllib.parse.unquote(path))


def urls() -> list[str]:
    found = [INDEX]
    for _, report, books in GENERALS.values():
        found += [_url(report)] + ([_url(books)] if books else [])
    for report, books in BY_ELECTIONS.values():
        found += [_url(report), _url(books)]
    return [*found, PED_2024]


def fetch() -> None:
    for url in urls():
        raw.fetch(SOURCE, url, suffix=".html" if url == INDEX else "." + url.rsplit(".", 1)[1])


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _number(text: str | None) -> int | None:
    text = _clean(text).replace(",", "")
    if text in ("", "-", "_", "\\"):
        return None
    value = float(text)
    if value != int(value):
        raise ValueError(f"a count with a fraction: {text}")
    return int(value)


@dataclass
class Poll:
    code: str
    name: str
    electors: int | None
    votes: list[int]
    valid: int
    rejected: int | None
    cast: int | None
    kind: str


@dataclass
class District:
    number: int
    name: str
    candidates: list[tuple[str, str]]
    polls: list[Poll]
    totals: Poll
    notes: list[str] = field(default_factory=list)


def members(url: str) -> dict[str, bytes]:
    """The workbooks a download holds, by file name: a lone workbook is its own member."""
    path = raw.cached(SOURCE, url).path
    if url.lower().endswith(".zip"):
        archive = zipfile.ZipFile(path)
        return {n: archive.read(n) for n in archive.namelist() if n.lower().endswith((".xls", ".xlsx"))}
    return {urllib.parse.unquote(url).rsplit("/", 1)[1]: path.read_bytes()}


def sheet_rows(data: bytes, index: int = 0) -> list[list[str]]:
    frame = fastexcel.read_excel(data).load_sheet(index, header_row=None, dtypes="string").to_polars()
    return [[_clean(cell) for cell in row] for row in frame.iter_rows()]


LABEL = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")
_LABELS = r"(?:L|PC|NDP|COR|NLP|IND|GREEN|PVNBGP|KNB|NBNDP(?: / NPDNB)?|PANB(?: / AGNB)?)(?:/[A-Z]+)?"
BARE = re.compile(rf"^(.*\S)\s+({_LABELS})$")
ONLY = re.compile(rf"^{_LABELS}$")
WITHDREW = re.compile(r"\s*\((?:withdrew|withdrawn)[^)]*\)\s*$", re.IGNORECASE)
KEYWORDS = (
    "rejected",
    "total",
    "cast",
    "ayant",
    "net total",
    "number of",
    "official",
    "polling",
    "city, town",
    "cluster",
)


def _candidate(header: str, after: str = "") -> tuple[str, str, bool] | None:
    """A candidate column's name, party label, and whether the candidate withdrew; None for a column that is not a
    candidate's. The workbooks print a label after the name in parentheses (`Ann Smith (PC)`), after it with no
    parentheses (`Smith, Ann PC/PC`), or in the cell beside it (`Ann Smith` then `PC/PC`)."""
    lower = header.lower()
    if not header or header == "_" or lower.startswith("%") or any(word in lower for word in KEYWORDS):
        return None
    withdrew = WITHDREW.search(header) is not None
    header = WITHDREW.sub("", header)
    found = LABEL.match(header)
    if found is not None and not re.search(r"\s/|/\s", found.group(1)):
        name, label = _clean(found.group(1)), _clean(found.group(2))
    elif found is None and (bare := BARE.match(header)) is not None:
        name, label = _clean(bare.group(1)), bare.group(2)
    elif found is None and ONLY.match(after):
        name, label = header, after
    else:
        return None
    return (name, label, withdrew) if name else None


def _title(text: str) -> tuple[int, str]:
    found = re.match(r"^#?\s*(\d+)\s*[-–]?\s*(.*?)\s*/?\s*$", text)
    if found is None:
        raise ValueError(f"no district in the title {text!r}")
    return int(found.group(1)), _clean(found.group(2))


def legacy_district(rows: list[list[str]], number: int | None = None, name: str | None = None) -> District:
    """A district sheet of the workbooks through 2014, or of a by-election's recapitulation sheet."""
    head = next(i for i, row in enumerate(rows) if any("rejected" in cell.lower() for cell in row))
    header = rows[head]
    first = rows[0][0] if rows[0] else ""
    if name is None and re.match(r"^#?\s*\d+\b", first):
        number, name = _title(first)
        if not name and len(rows) > 1:
            name = re.sub(r"\s*/.*$", "", rows[1][0]).strip()
    if number is None or not name:
        raise ValueError("a district sheet with no title and no number")
    columns: dict[str, int] = {}
    candidates: list[tuple[int, str, str]] = []
    empty: list[int] = []
    withdrawn: list[str] = []
    for index, cell in enumerate(header):
        lower = cell.lower()
        found = _candidate(cell, header[index + 1] if index + 1 < len(header) else "")
        if found is not None and found[0].upper().startswith("N/A"):
            empty.append(index)
        elif found is not None:
            candidates.append((index, found[0], found[1]))
            if found[2]:
                withdrawn.append(found[0])
        elif lower.startswith("%") or not cell or "cluster" in lower:
            continue
        elif "rejected" in lower:
            columns["rejected"] = index
        elif lower.startswith(("total votes", "total /", "total/")) or lower == "total":
            columns.setdefault("valid", index)
        elif "votes cast" in lower or "ballots cast" in lower or "electors who cast" in lower:
            columns["cast"] = index
        elif lower.startswith("net total"):
            columns["electors"] = index
        elif lower.startswith(("official list", "number of names")):
            columns.setdefault("official", index)
        elif lower.startswith("polling division") and "station" not in lower:
            columns["code"] = index
        elif lower.startswith(("polling station", "city, town", "polling location")):
            columns["location"] = index
    location = columns.get("location", columns.get("code", 1))
    polls: list[Poll] = []
    totals: Poll | None = None
    label = ""
    section = ""
    seen: set[str] = set()
    for row in rows[head + 1 :]:
        if not any(row):
            continue
        lead = [cell for cell in row[: min(location, candidates[0][0] - 1) + 1] if cell]
        text = " ".join(lead)
        counted = [c[0] for c in candidates] + [columns[k] for k in ("valid", "rejected", "cast") if k in columns]
        has_figures = any(_number(row[i]) is not None for i in counted if i < len(row))
        if not has_figures:
            lowered = text.lower()
            if "advance" in lowered or "anticipation" in lowered:
                section = "Advance poll"
            elif "write" in lowered or "inscription" in lowered:
                section = "Write-in ballots"
            elif text and not label:
                label = text
            continue
        if any(cell.upper().startswith("TOTAL") for cell in lead):
            totals = _poll("TOTAL", text, row, columns, candidates, "total")
            break
        code_cell = row[columns["code"]] if "code" in columns else ""
        if "code" in columns and re.fullmatch(r"\d+_\d+", code_cell):
            code, kind = code_cell.split("_")[1], "standard"
        elif re.fullmatch(r"\d+[A-Za-z]?", row[0]):
            code, kind = _clean(f"{section} {row[0]}"), "standard" if not section else "aggregate"
        else:
            code, kind = _clean(f"{section} {label} {text}"), "aggregate"
            label = ""
            count = 1
            base = code
            while code in seen:
                count += 1
                code = f"{base} {count}"
        if code in seen:
            code = _clean(f"{code} {text}")
        seen.add(code)
        polls.append(_poll(code, text, row, columns, candidates, kind))
    if totals is None:
        raise ValueError(f"district {number}: no total row")
    for row in rows[head + 1 :]:
        if any(_number(row[i]) for i in empty if i < len(row)):
            raise ValueError(f"district {number}: votes for a column marked N/A")
    found_district = District(number, name, [(n, p) for _, n, p in candidates], polls, totals)
    for poll in polls:
        if poll.electors is not None and poll.electors < 0:
            found_district.notes.append(
                f"The authority prints {poll.electors} electors for {poll.code}, a net revision; the figure is not stored"
            )
            poll.electors = None
    for printed in withdrawn:
        found_district.notes.append(f"{printed} withdrew after the close of nominations; the name stayed on the ballot")
    return found_district


def _poll(
    code: str, name: str, row: list[str], columns: dict[str, int], candidates: list[tuple[int, str, str]], kind: str
) -> Poll:
    def cell(key: str) -> int | None:
        return _number(row[columns[key]]) if key in columns and columns[key] < len(row) else None

    votes = [_number(row[i]) or 0 for i, _, _ in candidates]
    valid = cell("valid")
    return Poll(
        code,
        name,
        cell("electors") if "electors" in columns else cell("official"),
        votes,
        sum(votes) if valid is None else valid,
        cell("rejected"),
        cell("cast"),
        kind,
    )


TITLE = re.compile(r"MLA\s*/\s*D[ée]put[ée],\s*(\d+)\s+(.*?)\s*\(Vote for", re.IGNORECASE)


def modern_districts(rows: list[list[str]]) -> list[District]:
    """The blocks of a workbook from 2015 on: a title row, a header row of `Name - PARTY` cells, a row for each
    polling division, a row for the votes whose division the count did not determine, and a total row."""
    found: list[District] = []
    index = 0
    while index < len(rows):
        title = TITLE.search(rows[index][0]) if rows[index] else None
        if title is None:
            index += 1
            continue
        number = int(title.group(1))
        name = _clean(title.group(2).split(" / ")[0])
        index += 1
        while not rows[index][0].lower().startswith("poll/sdv"):
            index += 1
        header = rows[index]
        candidates: list[tuple[int, str, str]] = []
        for column, cell in enumerate(header):
            if column == 0 or not cell:
                continue
            printed, _, label = cell.rpartition(" - ")
            if not printed:
                raise ValueError(f"district {number}: the candidate cell {cell!r} has no party")
            candidates.append((column, _clean(printed), _clean(label)))
        voided = [i for i, (_, _, label) in enumerate(candidates) if "withdrew" in label.lower()]
        candidates = [(c, n, re.sub(r"\s*\(.*\)\s*$", "", label)) for c, n, label in candidates]
        polls: list[Poll] = []
        totals: Poll | None = None
        index += 1
        while index < len(rows) and rows[index] and rows[index][0]:
            row = rows[index]
            index += 1
            votes = [_number(row[column]) or 0 for column, _, _ in candidates]
            label = row[0]
            if label.lower().startswith("total"):
                totals = Poll("TOTAL", label, None, votes, sum(votes), None, None, "total")
                break
            code = re.sub(r"^P?\d+-", "", label)
            standard = re.fullmatch(r"P?\d+-\d+[A-Za-z]?", label) is not None
            polls.append(
                Poll(
                    code if standard else label,
                    label,
                    None,
                    votes,
                    sum(votes),
                    None,
                    None,
                    "standard" if standard else "aggregate",
                )
            )
        if totals is None:
            raise ValueError(f"district {number}: no total row")
        district = District(number, name, [(n, p) for _, n, p in candidates], polls, totals)
        for i in voided:
            voided_votes = totals.votes[i]
            for poll in [*polls, totals]:
                poll.valid -= poll.votes[i]
                poll.votes[i] = 0
            district.notes.append(
                f"{candidates[i][1]} withdrew on the eve of the election; the {voided_votes} ballots marked for "
                "the former candidate were considered null and void and counted as rejected, so the option holds none"
            )
        found.append(district)
    return found


def by_election_districts(day: date) -> list[District]:
    """The districts that voted on a by-election day, from the poll-by-poll workbook of each."""
    found: list[District] = []
    for name, data in sorted(members(_url(BY_ELECTIONS[day][1])).items()):
        lower = name.lower()
        if "(tab)" in lower or "per tab" in lower or "xref" in lower:
            continue
        rows = sheet_rows(data)
        if any(TITLE.search(row[0]) for row in rows if row):
            found += modern_districts(rows)
        else:
            number = re.search(r"CE-?(\d+)", name)
            found.append(legacy_district(rows, int(number.group(1)) if number else None, "?"))
    return found


@dataclass
class Recap:
    name: str
    parties: dict[str, int]
    valid: int
    rejected: int | None
    cast: int | None
    electors: int | None


def recapitulation(year: int) -> tuple[dict[int, Recap], Recap]:
    """The authority's recapitulation of a general election: a row for each district and the province's total row."""
    books = members(_url(GENERALS[year][2] or ""))
    data = next(v for k, v in books.items() if k.lower().startswith("recapitulation"))
    rows = sheet_rows(data)
    head = next(i for i, row in enumerate(rows) if any("rejected" in cell.lower() for cell in row))
    header = rows[head]
    kinds: dict[int, str] = {}
    for index, cell in enumerate(header):
        lower = cell.lower()
        if lower.startswith("%") or not cell:
            continue
        if "rejected" in lower:
            kinds[index] = "rejected"
        elif lower.startswith("total") and ("votes" in lower or lower.startswith("total /") or lower == "total"):
            kinds[index] = "valid"
        elif "votes cast" in lower or "voix exprim" in lower or "ayant vot" in lower:
            kinds[index] = "cast"
        elif lower.startswith("net total"):
            kinds[index] = "electors"
        elif ("/" in cell and " " in cell) or lower.startswith(
            ("electoral", "recapitulation", "preliminary", "added", "official", "list", "sworn", "deleted")
        ):
            continue
        else:
            kinds[index] = "party:" + cell

    def read(row: list[str]) -> Recap:
        parties = {
            kind[6:]: _number(row[i]) or 0 for i, kind in kinds.items() if kind.startswith("party:") and i < len(row)
        }
        pick = {kind: _number(row[i]) for i, kind in kinds.items() if not kind.startswith("party:") and i < len(row)}
        text = [cell for cell in row[:4] if cell and not cell.isdigit()]
        name = re.sub(r"\s*/.*$", "", text[0].replace("\n", " ")).strip() if text else ""
        return Recap(
            name, parties, pick.get("valid") or 0, pick.get("rejected"), pick.get("cast"), pick.get("electors")
        )

    districts: dict[int, Recap] = {}
    province: Recap | None = None
    for row in rows[head + 1 :]:
        if row and row[0].isdigit():
            districts[int(row[0])] = read(row)
        elif any(cell.upper().startswith("TOTAL") for cell in row[:3]):
            province = read(row)
            break
    if province is None:
        raise ValueError(f"{year}: no provincial total row")
    return districts, province


# The party each label the workbooks print stands for. Labels print in English and French, with spaces and without.
PARTIES: Final = {
    "pc": ("Progressive Conservative Party of New Brunswick", "PC", "PC"),
    "lib": ("Liberal Party of New Brunswick", "Liberal", "L"),
    "ndp": ("New Brunswick New Democratic Party", "NDP", "NDP/NPD"),
    "green": ("Parti Vert N.B. Green Party", "Green Party", "PVNBGP"),
    "pa": ("People's Alliance of New Brunswick", "People's Alliance", "PANB/AGNB"),
    "cor": ("Confederation of Regions – N.B.", "COR", "COR"),
    "nlp": ("Natural Law Party of New Brunswick", "Natural Law", "NLP/PLN"),
    "grey": ("The Grey Party of New Brunswick", "Grey Party", "GREY"),
    "knb": ("KISS N.B. Political Party", "KISS NB", "KNB"),
    "lpnb": ("Libertarian Party of New Brunswick", "Libertarian", "LPNB"),
    "cnbp": ("Consensus NB Party", "Consensus NB", "CNBP"),
    "sjpnb": ("Social Justice Party of New Brunswick", "Social Justice", "SJPNB"),
}
LABELS: Final = {
    "l": "lib",
    "l/l": "lib",
    "p": "pc",
    "pc": "pc",
    "pc/pc": "pc",
    "ndp/npd": "ndp",
    "ndp": "ndp",
    "nbndp/npdnb": "ndp",
    "cor": "cor",
    "nlp/pln": "nlp",
    "grey": "grey",
    "green": "green",
    "pvnbgp": "green",
    "panb/agnb": "pa",
    "agnb/panb": "pa",
    "knb": "knb",
    "lpnb/prinb": "lpnb",
    "lpnb/pamb": "lpnb",
    "cnbp": "cnbp",
    "sjpnb/pjsnb": "sjpnb",
    "ind": "ind",
    "ind/ind": "ind",
}


def party_code(label: str) -> str:
    text = re.sub(r"\s*\(.*?\)\s*$", "", label)
    code = LABELS.get(re.sub(r"\s+", "", text).casefold())
    if code is None:
        raise ValueError(f"unknown party label {label!r}")
    return code


# What each report prints for the whole province after any judicial recounts: the votes each party received, and the
# seats it won. The build checks the poll-by-poll workbooks against them. The workbooks of 2014 hold the count before
# the judicial recounts, so the 2014 comparison is reported rather than required to match.
PUBLISHED_VOTES: Final[dict[int, dict[str, int]]] = {
    2018: {"lib": 143791, "pc": 121300, "knb": 366, "ndp": 19039, "green": 45186, "pa": 47860, "ind": 2821},
    2020: {"pc": 147790, "lib": 129025, "knb": 139, "ndp": 6220, "green": 57252, "pa": 34526, "ind": 685},
    2024: {
        "pc": 131332,
        "lib": 180806,
        "cnbp": 278,
        "lpnb": 1710,
        "ndp": 4703,
        "green": 51558,
        "pa": 3265,
        "sjpnb": 476,
        "ind": 533,
    },
}
SEATS: Final[dict[int, dict[str, int]]] = {
    1995: {"lib": 48, "pc": 6, "ndp": 1},
    1999: {"pc": 44, "lib": 10, "ndp": 1},
    2003: {"pc": 28, "lib": 26, "ndp": 1},
    2006: {"lib": 29, "pc": 26},
    2010: {"pc": 42, "lib": 13},
    2014: {"lib": 27, "pc": 21, "green": 1},
    2018: {"pc": 22, "lib": 21, "pa": 3, "green": 3},
    2020: {"pc": 27, "lib": 17, "green": 3, "pa": 2},
    2024: {"lib": 31, "pc": 16, "green": 2},
}


def vintage(day: date) -> int:
    """The year of the first general election held on the districts in force on a day."""
    for first in (2024, 2014, 2006, 1995):
        if day >= GENERALS[first][0]:
            return first
    raise ValueError(f"{day}: before the first workbook")


VINTAGE_ENDS: Final = {1995: date(2006, 9, 18), 2006: date(2014, 9, 22), 2014: date(2024, 10, 21), 2024: None}


@functools.cache
def general(year: int) -> list[District]:
    day, _, books = GENERALS[year]
    found: list[District] = []
    if books is None:
        raise ValueError(f"{year}: no workbook")
    archive = members(_url(books))
    if year >= 2018:
        name = next(n for n in archive if "poll" in n.lower() and "tab" not in n.lower())
        found = modern_districts(sheet_rows(archive[name]))
    else:
        names = {n: r.name for n, r in recapitulation(year)[0].items()}
        for member, data in sorted(archive.items()):
            number = re.search(r"ED-CE-(\d+)", member)
            if number is None or member.lower().startswith("recap"):
                continue
            found.append(legacy_district(sheet_rows(data), int(number.group(1)), names[int(number.group(1))]))
    found.sort(key=lambda d: d.number)
    for d in found:
        for column, (printed, label) in enumerate(d.candidates):
            if label == "P":
                d.notes.append(f"the workbook labels {printed}'s party P; the recapitulation counts the votes for PC")
        _check(day, d)
    return found


def _check(day: date, d: District) -> None:
    for column, total in enumerate(d.totals.votes):
        if sum(p.votes[column] for p in d.polls) != total:
            raise ValueError(f"{day} {d.number} {d.name}: the polls do not add to {d.candidates[column]}'s total")
    if sum(p.valid for p in d.polls) != d.totals.valid:
        raise ValueError(f"{day} {d.number} {d.name}: the polls do not add to the total votes")
    if sum(p.rejected or 0 for p in d.polls) != (d.totals.rejected or 0):
        raise ValueError(f"{day} {d.number} {d.name}: the polls do not add to the rejected ballots")
    if d.totals.valid != sum(d.totals.votes):
        raise ValueError(f"{day} {d.number} {d.name}: the candidates do not add to the total votes")
    codes = [p.code for p in d.polls]
    if len(set(codes)) != len(codes):
        raise ValueError(f"{day} {d.number} {d.name}: a repeated polling division")


def winner(d: District) -> int:
    top = max(d.totals.votes)
    leaders = [i for i, count in enumerate(d.totals.votes) if count == top]
    if len(leaders) != 1:
        raise ValueError(f"{d.number} {d.name}: a tie for the most votes")
    return leaders[0]


def party_votes(districts: list[District]) -> dict[str, int]:
    votes: dict[str, int] = {}
    for d in districts:
        for (_, label), count in zip(d.candidates, d.totals.votes, strict=True):
            code = party_code(label)
            votes[code] = votes.get(code, 0) + count
    return votes


def seats(districts: list[District]) -> dict[str, int]:
    found: dict[str, int] = {}
    for d in districts:
        code = party_code(d.candidates[winner(d)][1])
        found[code] = found.get(code, 0) + 1
    return found


def compare(year: int) -> str:
    """Check a general election against what the authority prints for the province, and say how it compared."""
    districts = general(year)
    built = party_votes(districts)
    if year >= 2018:
        expected = PUBLISHED_VOTES[year]
        source = "the report of the Chief Electoral Officer"
    else:
        _, province = recapitulation(year)
        expected = {}
        for label, count in province.parties.items():
            if count or label.lower() not in ("récapitulation",):
                expected[party_code(label)] = expected.get(party_code(label), 0) + count
        expected = {k: v for k, v in expected.items() if v or k in built}
        source = "the authority's recapitulation"
    if year != 2014 and built != expected:
        raise ValueError(f"{year} votes: built {built}, {source} prints {expected}")
    if seats(districts) != SEATS[year]:
        raise ValueError(f"{year} seats: built {seats(districts)}, expected {SEATS[year]}")
    differ = {
        k: (built.get(k, 0), expected.get(k, 0))
        for k in set(built) | set(expected)
        if built.get(k, 0) != expected.get(k, 0)
    }
    if differ:
        return f"{year}: party votes differ from {source} (built, printed): {differ}"
    return f"{year}: party votes equal {source} for all {len(built)} parties; seats {seats(districts)}"


@functools.cache
def names_of(first: int) -> dict[int, str]:
    """The names of the districts of the plan first used in a general election, as the latest election on it printed
    them."""
    last = max(year for year in GENERALS if GENERALS[year][2] and vintage(GENERALS[year][0]) == first)
    if last >= 2018:
        return {d.number: d.name for d in general(last)}
    return {n: r.name for n, r in recapitulation(last)[0].items()}


@functools.cache
def other_names(first: int) -> dict[int, set[str]]:
    """The other names an election on the same plan gave a district."""
    found: dict[int, set[str]] = {}
    for year in GENERALS:
        if GENERALS[year][2] and vintage(GENERALS[year][0]) == first:
            for number, name in {d.number: d.name for d in general(year)}.items():
                if name != names_of(first)[number]:
                    found.setdefault(number, set()).add(name)
    return found


@functools.cache
def by_election(day: date) -> list[District]:
    plan = names_of(vintage(day))
    found = by_election_districts(day)
    for d in found:
        d.name = plan[d.number]
        _check(day, d)
    return found


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/ge-{year}" for year in GENERALS if GENERALS[year][2]),
        *(f"{SOURCE}/be-{day}" for day in BY_ELECTIONS),
    ]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ENB = key("source", "enb")
BOUNDARY_SOURCE = key("source", "boundaries")
ASSEMBLY = key("body", "legislative_assembly")
FPTP = key("electoral_system", "fptp")
PED_2024 = SITE + "/content/dam/enb/pdf/2023-ped-maps-cep-cartes/2024_Prov_PED_2026.zip"


def area_id(first: int, number: int) -> str:
    return key("area", "ed", first, f"{number:02d}")


def boundary_set(first: int) -> str:
    return key("boundary_set", first)


def poll_unit(day: date, number: int, code: str) -> str:
    return key("unit", f"{day}:{number:02d}:{code}")


def _capitalize(word: str) -> str:
    parts = []
    for piece in re.split(r"([-'’])", word):
        text = piece.capitalize() if piece.isalpha() else piece
        if text.startswith("Mc") and len(text) > 3:
            text = "Mc" + text[2:].capitalize()
        parts.append(text)
    return "".join(parts)


def proper(printed: str) -> str:
    """A name as people write it: `Gilles LEPAGE` is Gilles Lepage, and `Kennedy, Dr. Larry` is Dr. Larry Kennedy."""
    if "," in printed:
        last, _, rest = printed.partition(",")
        printed = f"{rest.strip()} {last.strip()}"
    words = [_capitalize(w) if len(w) > 1 and w.isupper() and w not in ("II", "III") else w for w in printed.split()]
    return " ".join(words)


def _wgs(shape: shapely.Geometry) -> shapely.Geometry:
    project = pyproj.Transformer.from_crs(2953, 4326, always_xy=True)
    moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


@functools.cache
def shapes_2024() -> dict[int, tuple[str, shapely.Geometry]]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, PED_2024).path)
    parts = {
        name.rsplit(".", 1)[1]: archive.read(name)
        for name in archive.namelist()
        if name.endswith(("shp", "dbf", "shx"))
    }
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), dbf=io.BytesIO(parts["dbf"]), shx=io.BytesIO(parts["shx"]))
    found: dict[int, tuple[str, shapely.Geometry]] = {}
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None:
            raise ValueError("a boundary record without its shape")
        found[int(str(record["DIST_ID"]))] = (
            str(record["PED_Name_E"]),
            shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__)),
        )
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "New Brunswick's electoral districts of the four plans in force from 1995 on, by number and name from the "
        "authority's results, and the outlines of the 49 districts of 2024 from Elections NB's boundary files",
    )
    p.external |= {NEW_BRUNSWICK, PROVINCE_AREA}
    p.add(
        "source",
        id=BOUNDARY_SOURCE,
        kind="official_body",
        publisher="Elections NB",
        title="New Brunswick Electoral Districts, 2024 (shapefile)",
        url=PED_2024,
        license=UNLICENSED,
        license_url=None,
        attribution="Elections NB",
    )
    p.artifact(raw.cached(SOURCE, PED_2024), BOUNDARY_SOURCE)
    p.add(
        "scheme",
        code="ca.nb",
        polity_id=NEW_BRUNSWICK,
        kind="electoral_district",
        rank=1,
        noun="electoral district",
        code_system=None,
    )
    p.add(
        "scheme",
        code="ca.nb_poll",
        polity_id=NEW_BRUNSWICK,
        kind="precinct",
        rank=2,
        noun="polling division",
        code_system=None,
    )
    for first, until in VINTAGE_ENDS.items():
        since = GENERALS[first][0]
        p.add(
            "boundary_set",
            id=boundary_set(first),
            scheme_code="ca.nb",
            vintage=str(first),
            valid_from=since,
            valid_to=until,
            as_of=since,
        )
        for number, name in sorted(names_of(first).items()):
            area = area_id(first, number)
            p.add("area", id=area, scheme_code="ca.nb")
            p.add("area_code", area_id=area, code=f"{number:02d}", valid_from=since, valid_to=until)
            p.add("area_parent", area_id=area, parent_id=PROVINCE_AREA, valid_from=since)
            p.name(area, name, "en", "official")
            for other in sorted(other_names(first).get(number, ())):
                p.name(area, other, "en", "historical", False)
    for number, (printed, shape) in shapes_2024().items():
        if names_of(2024)[number].casefold() != printed.casefold():
            p.add(
                "note",
                id=key("note", "outline", number),
                entity_id=area_id(2024, number),
                kind="caveat",
                body_md=f"The boundary file names this district {printed}; the results name it {names_of(2024)[number]}.",
                lang="en",
            )
        moved = _wgs(shape)
        p.add(
            "area_geometry",
            area_id=area_id(2024, number),
            valid_from=GENERALS[2024][0],
            valid_to=None,
            geom=shapely.to_wkb(moved, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(moved), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=BOUNDARY_SOURCE,
        )
    return p


def used_parties() -> set[str]:
    used: set[str] = set()
    for year in GENERALS:
        if GENERALS[year][2]:
            used |= {party_code(label) for d in general(year) for _, label in d.candidates}
    for day in BY_ELECTIONS:
        used |= {party_code(label) for d in by_election(day) for _, label in d.candidates}
    return used - {"ind"}


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "New Brunswick's parties, Legislative Assembly, and single-member plurality system, from Elections NB",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NEW_BRUNSWICK}
    p.add(
        "source",
        id=ENB,
        kind="official_body",
        publisher="Elections NB",
        title="Provincial election results: reports of the Chief Electoral Officer and poll-by-poll workbooks",
        url=INDEX,
        license=UNLICENSED,
        license_url=None,
        attribution="Elections NB",
    )
    p.artifact(raw.cached(SOURCE, INDEX), ENB)
    p.add("body", id=ASSEMBLY, polity_id=NEW_BRUNSWICK, kind="unicameral")
    p.name(ASSEMBLY, "Legislative Assembly of New Brunswick", "en", "official")
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=NEW_BRUNSWICK,
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
        body_md="One member is elected for each electoral district, by the most votes.",
        lang="en",
    )
    for code in sorted(used_parties()):
        official, short, abbreviation = PARTIES[code]
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=NEW_BRUNSWICK)
        p.add("external_id", entity_id=id, scheme="ca.nb.party", value=code)
        p.name(id, official, "en", "official")
        if short != official:
            p.name(id, short, "en", "short", False)
        if abbreviation not in (official, short):
            p.name(id, abbreviation, "en", "abbreviation", False)
    return p


METHOD: Final = (
    "Elections NB prints, for each polling division, each candidate's votes. Through 2014 it also prints the electors "
    "on the final list, the total votes, and the rejected ballots; those are stored as registered voters, valid "
    "votes, and rejected ballots, and the ballots cast are the valid votes and the rejected ballots together. From "
    "2015 on the poll-by-poll workbook holds the candidates' votes alone. Advance polls, special ballots, write-in "
    "ballots, and the votes whose polling division the count could not determine are reported for the district and "
    "stored as aggregate units. Votes are those counted on election night and in the official addition, before any "
    "judicial recount."
)


def _kind(poll: Poll) -> str:
    if poll.kind == "standard" and "mobile" in poll.name.lower():
        return "mobile"
    return "standard" if poll.kind == "standard" else "aggregate"


def _overcount(p: Package, found: list[tuple[str, District]]) -> None:
    total = 0
    for result_set, d in found:
        over = [
            poll.code for poll in d.polls if poll.electors is not None and (poll.cast or poll.valid) > poll.electors
        ]
        if not over:
            continue
        total += len(over)
        listed = ", ".join(over)
        p.add(
            "note",
            id=key("note", result_set, "overcount"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The authority prints more ballots cast than electors on the final list in polling division(s) "
            f"{listed}. The figures are stored as printed.",
            lang="en",
        )
    if total:
        p.accept(
            "accounting.turnout",
            f"Elections NB prints more ballots cast than electors on the final list in {total} polling divisions; "
            "stored as printed",
        )


def _contests(
    p: Package, day: date, election: str, items: list[District], path: str, first: int, area: dict[int, str]
) -> None:
    found: list[tuple[str, District]] = []
    for d in items:
        ed = area[d.number]
        contest = key("contest", day, d.number)
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
            boundary_set_id=boundary_set(first),
        )
        p.name(contest, d.name, "en", "official")
        p.add("slug", path=f"{path}/{changeset.slug(d.name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, d.number)
        found.append((result_set, d))
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ENB,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD + "".join(" " + note + "." for note in d.notes),
            lang="en",
        )
        top = winner(d)
        options = []
        for index, (printed, label) in enumerate(d.candidates):
            party = party_code(label)
            tag = f"{day}:{d.number}:{index + 1}"
            option = key("option", tag)
            person = key("person", tag)
            options.append(option)
            p.add("person", id=person)
            p.name(person, proper(printed), "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None if party == "ind" else key("party", party),
                ballot_position=index + 1,
                ballot_label=printed,
            )
            p.add(
                "candidacy",
                id=key("candidacy", tag),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=printed,
                member_of_party_id=None if party == "ind" else key("party", party),
                is_independent=party == "ind",
            )
            withdrew = any(printed in note and "withdrew" in note for note in d.notes)
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if index == top else ("withdrew" if withdrew else "lost"),
            )
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=d.totals.votes[index])
        for poll in d.polls:
            unit = poll_unit(day, d.number, poll.code)
            kind = _kind(poll)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NEW_BRUNSWICK,
                scheme_code="ca.nb_poll",
                code=f"{day}:{d.number:02d}:{poll.code}",
                area_relation="equals" if kind == "aggregate" else "within",
                area_id=ed,
                kind=kind,
            )
            cast = (
                poll.cast
                if poll.cast is not None
                else (poll.valid + poll.rejected if poll.rejected is not None else None)
            )
            accounting = [(2, poll.electors), (3, cast), (4, poll.valid), (5, poll.rejected)]
            for measure, value in accounting:
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
            for option, count in zip(options, poll.votes, strict=True):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=count,
                )
    _overcount(p, found)


def _uses(p: Package, urls_: list[str]) -> None:
    for url in urls_:
        p.artifact(raw.cached(SOURCE, url), ENB)


def election(year: int) -> Package:
    day, report, books = GENERALS[year]
    p = Package(
        f"{SOURCE}/ge-{year}",
        SOURCE,
        f"The {year} New Brunswick general election by polling division, from Elections NB",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEW_BRUNSWICK, PROVINCE_AREA}
    _uses(p, [_url(report), _url(books or "")])
    first = vintage(day)
    election_id = key("election", day)
    path = f"ca/nb/{day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=NEW_BRUNSWICK,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{year} New Brunswick general election", "en", "official")
    items = general(year)
    if year == 2014:
        p.add(
            "note",
            id=key("note", election_id),
            entity_id=election_id,
            kind="caveat",
            body_md="The poll-by-poll workbook holds the count before the judicial recounts. The authority's "
            "recapitulation, after the recounts, differs by a few votes in seven districts.",
            lang="en",
        )
    _contests(p, day, election_id, items, path, first, {d.number: area_id(first, d.number) for d in items})
    return p


def by_election_package(day: date) -> Package:
    items = by_election(day)
    names = [d.name for d in items]
    p = Package(
        f"{SOURCE}/be-{day}",
        SOURCE,
        f"The by-election of {day} in {', '.join(names)} by polling division, from Elections NB",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEW_BRUNSWICK, PROVINCE_AREA}
    report, books = BY_ELECTIONS[day]
    _uses(p, [_url(report), _url(books)])
    first = vintage(day)
    election_id = key("election", day)
    path = f"ca/nb/{day}-by-election"
    p.add(
        "election",
        id=election_id,
        polity_id=NEW_BRUNSWICK,
        kind="by_election",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    p.name(election_id, f"{joined} by-election{'s' if len(names) > 1 else ''}, {day:%-d %B %Y}", "en", "official")
    _contests(p, day, election_id, items, path, first, {d.number: area_id(first, d.number) for d in items})
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for year in GENERALS:
        if GENERALS[year][2]:
            print(compare(year), flush=True)
            election(year).write()
    for day in BY_ELECTIONS:
        by_election_package(day).write()


def emit() -> None:
    package.run("load", packages())
