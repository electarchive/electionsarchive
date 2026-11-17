"""The open data files of the elections site that predate its XML, read for a cross-check of the page-based packages.

The site publishes the 2009 legislative results as one SpreadsheetML workbook per constituency, and the 2011 communal
results as one XML file per commune in a zip. Both hold the same commune counts as the pages the packages are built
from, so each can confirm them. Neither holds anything for 1994, and the 2009 workbooks hold no seats.
"""

import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

from ea import raw
from ea.sources.lu.model import Candidate, Entry, Place, Race, Stats, clean, fold

SOURCE = "lu.elections"
BASE = "https://elections.public.lu/dam-assets/opendata"
LEGISLATIVE_2009 = {c: f"{BASE}/legislatives/2009/2009-legislatives-{c}.xls" for c in ("centre", "est", "nord", "sud")}
COMMUNAL_2011 = f"{BASE}/communales/2011/elections-communales-2011.zip"
SHEET = "{urn:schemas-microsoft-com:office:spreadsheet}"


def sheets(text: bytes) -> dict[str, list[list[str | None]]]:
    """Every worksheet of a SpreadsheetML workbook as rows of cell texts, with skipped cells left empty."""
    found: dict[str, list[list[str | None]]] = {}
    for sheet in ElementTree.fromstring(text).iter(f"{SHEET}Worksheet"):
        rows: list[list[str | None]] = []
        for row in sheet.iter(f"{SHEET}Row"):
            cells: list[str | None] = []
            for cell in row.findall(f"{SHEET}Cell"):
                index = cell.get(f"{SHEET}Index")
                while index and len(cells) < int(index) - 1:
                    cells.append(None)
                data = cell.find(f"{SHEET}Data")
                cells.append(data.text if data is not None else None)
            rows.append(cells)
        found[sheet.get(f"{SHEET}Name") or ""] = rows
    return found


def _number(text: str | None) -> int | None:
    return int(float(text)) if text not in (None, "", " ") else None


def _sheet_place(name: str, rows: list[list[str | None]]) -> Place:
    """One commune's or constituency's sheet: the ballots, then one column group of three cells per list."""

    def at(row: int) -> int | None:
        return _number(rows[row][1]) if len(rows[row]) > 1 else None

    blank_invalid = at(3)
    stats = Stats(registered=at(1), cast=at(2), valid=at(4))
    # The sheet prints blank and invalid ballots together; the pages print them apart, so only their sum is compared.
    stats.blank = blank_invalid
    place = Place(name, None, stats)
    heads = rows[6]
    total_row = next(i for i, row in enumerate(rows) if row and row[0] == "Total Liste")
    for column in range(0, len(heads), 3):
        list_name = clean(heads[column])
        if not list_name:
            continue
        entry = Entry(column // 3 + 1, list_name, list_name)
        entry.total = _number(rows[total_row][column + 1])
        entry.listed = _number(rows[total_row + 1][column + 1])
        for row in rows[7:total_row]:
            if len(row) > column + 1 and row[column]:
                entry.candidates.append(
                    Candidate(len(entry.candidates) + 1, clean(row[column]), _number(row[column + 1]), False)
                )
        entry.personal = (entry.total or 0) - (entry.listed or 0)
        place.entries.append(entry)
    return place


def legislative_2009() -> dict[str, tuple[Place, dict[str, Place]]]:
    """Each constituency's own sheet and its communes' sheets, by constituency name."""
    found: dict[str, tuple[Place, dict[str, Place]]] = {}
    for circle, link in LEGISLATIVE_2009.items():
        book = sheets(raw.cached(SOURCE, link).path.read_bytes())
        names = [name for name in book if name != "Sommaire"]
        places = {name: _sheet_place(name, book[name]) for name in names}
        found[circle.title()] = (places[names[0]], {name: places[name] for name in names[1:]})
    return found


@dataclass
class Commune:
    id: str
    name: str
    canton: str
    seats: int
    mode: str
    stats: Stats
    entries: list[Entry]
    candidates: list[Candidate]


def communal_2011() -> dict[str, Commune]:
    found: dict[str, Commune] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, COMMUNAL_2011).path) as archive:
        for name in sorted(archive.namelist()):
            if not name.endswith(".xml"):
                continue
            root = ElementTree.fromstring(archive.read(name))
            header = root.find("result-header")
            progress = root.find("result-progress")
            content = root.find("result-content")
            if header is None or progress is None or content is None:
                raise ValueError(f"{name}: not a commune file")
            voters, ballots = progress.find("electeurs"), progress.find("urne")
            if voters is None or ballots is None:
                raise ValueError(f"{name}: no ballot counts")
            stats = Stats(
                registered=_number(voters.get("inscrit")),
                cast=_number(ballots.get("total")),
                valid=_number(ballots.get("valable")),
                invalid=_number(ballots.get("nul")),
            )
            entries: list[Entry] = []
            candidates: list[Candidate] = []
            for index, element in enumerate(content.iter("liste"), 1):
                votes = element.find("suffrages")
                entry = Entry(
                    index,
                    clean(element.findtext("longname")),
                    clean(element.findtext("shortname")),
                    total=_number(votes.get("total")) if votes is not None else None,
                    listed=_number(votes.get("liste")) if votes is not None else None,
                    seats=_number(element.findtext("mandats")),
                )
                entry.personal = (entry.total or 0) - (entry.listed or 0)
                entry.candidates = [
                    Candidate(i, clean(c.text), _number(c.get("suffrages")), c.get("elu") == "oui")
                    for i, c in enumerate(element.iter("candidat"), 1)
                ]
                entries.append(entry)
            if not entries:
                candidates = [
                    Candidate(i, clean(c.text), _number(c.get("suffrages")), c.get("elu") == "oui")
                    for i, c in enumerate(content.iter("candidat"), 1)
                ]
            found[header.get("id") or name] = Commune(
                header.get("id") or name,
                clean(header.findtext("name")),
                clean(header.findtext("canton")),
                int(header.findtext("conseillers") or 0),
                header.get("type") or "",
                stats,
                entries,
                candidates,
            )
    return found


def compare_legislative(races: list[Race]) -> list[str]:
    """Differences between the 2009 workbooks and the page-based races, one line each."""
    out: list[str] = []
    files = legislative_2009()
    for race in races:
        sheet, communes = files[race.name]
        by_name = {fold(name): place for name, place in communes.items()}
        pages_by_name = {fold(place.name): place for place in race.places}
        for missing in sorted(set(by_name) ^ set(pages_by_name)):
            out.append(f"{race.name}: commune {missing} is only in the {'sheet' if missing in by_name else 'pages'}")
        for name in sorted(set(by_name) & set(pages_by_name)):
            out += _place_diff(f"{race.name}/{name}", by_name[name], pages_by_name[name])
        out += _stats_diff(f"{race.name} total", sheet.stats, race.stats, blank=True)
        for entry in sheet.entries:
            match = [e for e in race.entries if fold(e.name) == fold(entry.name) or fold(e.sigle) == fold(entry.sigle)]
            if match and race.entries and match[0].total != entry.total:
                out.append(f"{race.name} {entry.name}: total {entry.total} in the sheet, {match[0].total} on the page")
    return out


def _stats_diff(where: str, sheet: Stats, page: Stats, blank: bool = False) -> list[str]:
    out = []
    for field_ in ("registered", "cast", "valid"):
        a, b = getattr(sheet, field_), getattr(page, field_)
        if b is not None and a != b:
            out.append(f"{where}: {field_} {a} in the file, {b} on the page")
    if (
        blank
        and sheet.blank is not None
        and page.blank is not None
        and page.invalid is not None
        and sheet.blank != page.blank + page.invalid
    ):
        out.append(f"{where}: blank and invalid {sheet.blank} in the file, {page.blank + page.invalid} on the page")
    return out


def _name(text: str) -> str:
    return fold(re.sub(r"\s*-\s*élu d'office$", "", text))


def _matched(entry: Entry, others: list[Entry], index: int) -> Entry | None:
    """The page's list for a file's list: the one with the same name or abbreviation, else the one in its position."""
    for other in others:
        names = {fold(re.sub(r"^\d+\s*", "", other.name)), fold(other.sigle)}
        if (
            fold(entry.name) in names
            or fold(entry.sigle) in names
            or any(n and fold(entry.sigle).startswith(n) for n in names)
        ):
            return other
    return others[index] if len(others) == len({e.number for e in others}) and index < len(others) else None


def _place_diff(where: str, sheet: Place, page: Place) -> list[str]:
    out = _stats_diff(where, sheet.stats, page.stats, blank=True)
    if len(sheet.entries) != len(page.entries):
        out.append(f"{where}: {len(sheet.entries)} lists in the file, {len(page.entries)} on the page")
    for index, entry in enumerate(sheet.entries):
        other = _matched(entry, page.entries, index)
        if other is None:
            out.append(f"{where}: list {entry.name!r} not on the page")
            continue
        for label, a, b in (
            ("total", entry.total, other.total),
            ("list votes", entry.listed, other.listed),
            ("personal votes", entry.personal, other.personal),
        ):
            if b is not None and a != b:
                out.append(f"{where} {entry.name}: {label} {a} in the file, {b} on the page")
        if entry.seats is not None and other.seats is not None and entry.seats != other.seats:
            out.append(f"{where} {entry.name}: {entry.seats} seats in the file, {other.seats} on the page")
        names = {_name(c.name): c for c in other.candidates}
        for candidate in entry.candidates:
            found = names.get(_name(candidate.name))
            if found is None:
                out.append(f"{where} {entry.name}: candidate {candidate.name!r} not on the page")
            elif found.votes != candidate.votes:
                out.append(
                    f"{where} {entry.name} {candidate.name}: {candidate.votes} in the file, {found.votes} on the page"
                )
            elif sheet.name and candidate.elected != found.elected and (candidate.elected or found.elected):
                out.append(
                    f"{where} {entry.name} {candidate.name}: elected {candidate.elected} in the file, {found.elected} on the page"
                )
    return out


def compare_communal(races: list[Race]) -> list[str]:
    """Differences between the 2011 communal files and the page-based races, one line each."""
    out: list[str] = []
    files = {fold(commune.name): commune for commune in communal_2011().values()}
    pages = {fold(race.name): race for race in races}
    for missing in sorted(set(files) ^ set(pages)):
        out.append(f"commune {missing} is only in the {'file' if missing in files else 'pages'}")
    for name in sorted(set(files) & set(pages)):
        commune, race = files[name], pages[name]
        where = commune.name
        if commune.seats != race.seats:
            out.append(f"{where}: {commune.seats} seats in the file, {race.seats} on the page")
        out += _stats_diff(where, commune.stats, race.stats)
        if (
            commune.stats.invalid is not None
            and race.stats.invalid is not None
            and commune.stats.invalid != race.stats.invalid
        ):
            out.append(f"{where}: invalid {commune.stats.invalid} in the file, {race.stats.invalid} on the page")
        if race.mode == "PROPORTIONNEL" and commune.entries:
            sheet = Place(where, None, commune.stats, commune.entries)
            page = race.places[0]
            out += _place_diff(where, sheet, page)
        elif race.mode == "MAJORITAIRE" or commune.candidates:
            ours = {_name(c.name): c for c in commune.candidates}
            theirs = {_name(c.name): c for c in race.candidates}
            if set(ours) != set(theirs):
                out.append(f"{where}: candidates differ ({len(ours)} in the file, {len(theirs)} on the page)")
            for who in set(ours) & set(theirs):
                if (ours[who].votes or None) != (theirs[who].votes or None) or ours[who].elected != theirs[who].elected:
                    out.append(
                        f"{where} {who}: {ours[who].votes}/{ours[who].elected} in the file, "
                        f"{theirs[who].votes}/{theirs[who].elected} on the page"
                    )
        else:
            out.append(f"{where}: the file is {commune.mode} and the page {race.mode}")
    return out


def report() -> list[str]:
    """Every difference between the open data files and the page-based races (none when they agree)."""
    from ea.sources.lu import elections

    return [
        *compare_legislative(elections.races("legislative-2009")),
        *compare_communal(elections.races("communal-2011")),
    ]
