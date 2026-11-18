"""By-elections to the House of Representatives, from the Electoral Commission's voting-place files.

Each by-election is one electorate contest. The authority publishes the votes of each candidate at each voting place:
a spreadsheet for most (the electorate's candidate table, its summary with the parties, and the turnout tables), a
page's csv file for the later ones, and one csv file for 1998. The elections build into the `nz.ec` pipeline beside the
general elections, share its registry for the main parties, and keep their other parties in a registry of their own.
"""

import csv
import functools
import io
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import xlrd

from ea.package import Package
from ea.sources.nz import ec

SOURCE = ec.SOURCE
SITE = ec.SITE
NEW_ZEALAND = ec.NEW_ZEALAND
BY_REGISTRY = f"{SOURCE}/by-registry"


@dataclass(frozen=True)
class ByElection:
    day: str
    electorate: str
    url: str
    suffix: str


# The by-elections the Electoral Commission's results site holds with voting-place results. The dates are those of the
# polls; the 1998 file prints its own. The 1994 Selwyn by-election is a scanned page without a text layer, and no
# earlier one is published.
BY_ELECTIONS = (
    ByElection("1998-05-02", "Taranaki-King Country", f"{SITE}/1998_taranaki_byelection/partxv.csv", ".csv"),
    ByElection("2004-07-10", "Te Tai Hauāuru", f"{SITE}/2004_byelection/2004-by-election-stats.xls", ".xls"),
    ByElection("2009-06-13", "Mount Albert", f"{SITE}/2009_mt_albert_byelection/2009-by-election-stats.xls", ".xls"),
    ByElection("2010-11-20", "Mana", f"{SITE}/2010_mana_byelection/2010-by-election-stats.xls", ".xls"),
    ByElection("2011-03-05", "Botany", f"{SITE}/2011_botany_byelection/2011-by-election-stats.xls", ".xls"),
    ByElection(
        "2011-06-25",
        "Te Tai Tokerau",
        f"{SITE}/2011_te_tai_tokerau_byelection/2011-Te-Tai-Tokerau-by-election-stats.xls",
        ".xls",
    ),
    ByElection(
        "2013-06-29",
        "Ikaroa-Rāwhiti",
        f"{SITE}/2013_ikaroa_rawhiti_byelection/2013-Ikaroa-R%C4%81whiti-by-election-stats.xls",
        ".xls",
    ),
    ByElection(
        "2013-11-30",
        "Christchurch East",
        f"{SITE}/2013_christchurch_east_byelection/2013-Christchurch-East-by-election-stats.xls",
        ".xls",
    ),
    ByElection(
        "2015-03-28", "Northland", f"{SITE}/2015_northland_byelection/2015-northland-by-election-stats.xls", ".xls"
    ),
    ByElection(
        "2016-12-03",
        "Mount Roskill",
        "https://archive.electionresults.govt.nz/2016_mt_roskill_byelection/2016-mtroskill-by-election-stats.xls",
        ".xls",
    ),
    ByElection(
        "2017-02-25",
        "Mount Albert",
        "https://archive.electionresults.govt.nz/2017_mt_albert_byelection/Mt_Albert_27.xls",
        ".xls",
    ),
    ByElection(
        "2018-06-09",
        "Northcote",
        f"{SITE}/2018_northcote_byelection/statistics/xls/candidate-votes-by-voting-place-34.xls",
        ".xls",
    ),
    ByElection(
        "2022-06-18",
        "Tauranga",
        f"{SITE}/2022_tauranga_byelection/statistics/xls/candidate-votes-by-voting-place-52.xls",
        ".xls",
    ),
    ByElection(
        "2022-12-10",
        "Hamilton West",
        f"{SITE}/2022_hamilton_west_byelection/statistics/csv/candidate-votes-by-voting-place-13.csv",
        ".csv",
    ),
    ByElection(
        "2023-11-25",
        "Port Waikato",
        f"{SITE}/2023_port_waikato_byelection/statistics/csv/candidate-votes-by-voting-place-39.csv",
        ".csv",
    ),
    ByElection(
        "2025-09-06",
        "Tāmaki Makaurau",
        f"{SITE}/2025_tamaki_makaurau_byelection/statistics/spreadsheets/68-statistics.xlsx",
        ".xlsx",
    ),
)
SECTION_ROWS = re.compile(r"^(advance )?voting places$", re.IGNORECASE)

# The main parties are in the registry the general elections share; every other party the by-elections print is made
# in their own registry, once, under its printed name.
SHARED_PARTIES = frozenset(
    {
        "labour party",
        "national party",
        "green party",
        "act new zealand",
        "new zealand first party",
        "māori party",
        "te pāti māori",
    }
)
# Printed with a circumflex where the macron belongs, in the 2004 spreadsheet.
PRINTED = {"Mâori": "Māori"}
# The 1998 file prints each candidate's party in capitals after the name, in the column heading. Each is named here as
# the party is called, with no party for an independent.
PARTIES_1998 = {
    "AOTEAROA LEGALISE CANNABIS PARTY": "Aotearoa Legalise Cannabis Party",
    "NZ NATIONAL PARTY": "National Party",
    "INDEPENDENT": "",
    "ALLIANCE": "Alliance",
    "McGILLICUDDY SERIOUS PARTY": "McGillicuddy Serious Party",
    "NZ PROGRESSIVE PARTY": "NZ Progressive Party",
    "UNITED NEW ZEALAND PARTY": "United New Zealand Party",
    "MANA WAHINE TE IRA TANGATA": "Mana Wahine Te Ira Tangata",
    "SOCIAL CREDIT N.Z.": "Social Credit N.Z.",
    "ACT NEW ZEALAND": "ACT New Zealand",
    "THE GREENS, THE GREEN PARTY OF AOTEAROA/NEW ZEALAND": "The Greens, the Green Party of Aotearoa/New Zealand",
    "ANIMALS FIRST": "Animals First",
    "CHRISTIAN HERITAGE PARTY OF NEW ZEALAND": "Christian Heritage Party of New Zealand",
    "NATURAL LAW PARTY": "Natural Law Party",
    "NEW ZEALAND FIRST PARTY": "New Zealand First Party",
    "LABOUR PARTY": "Labour Party",
    "AOTEAROA N.Z. YOUTH INDEPENDENCE PARTY": "Aotearoa N.Z. Youth Independence Party",
}

METHOD = (
    "Counted per voting place and published by the Electoral Commission. A place open for advance voting and again on "
    "election day is two units, one for each channel. Special declaration votes, overseas votes, and the votes of "
    "places where fewer than six votes were taken are reported only above the voting place, so each is an aggregate "
    "unit of the electorate. The electors on the roll are held on a unit of the electorate."
)


def key(kind: str, *parts: object) -> str:
    return ec.key(f"by-{kind}", *parts)


def party_key(name: str) -> str:
    return key("party", name.casefold())


@dataclass
class Contest:
    """One by-election's electorate contest as its file prints it."""

    names: list[str]  # the candidates, as the file names them, in the order of its columns
    parties: list[str]  # each candidate's party as printed, empty for an independent
    rows: list[ec.Row]  # each place's candidate votes, then its valid and informal votes
    total: list[int]
    winner: str | None
    majority: int | None
    roll: int | None
    fixed: list[str] = field(default_factory=list)


def urls() -> list[ByElection]:
    return [by for by in BY_ELECTIONS if ec.has(by.url)]


def fetch() -> None:
    for by in BY_ELECTIONS:
        ec._try(by.url, by.suffix)


def packages() -> list[str]:
    found = urls()
    return [BY_REGISTRY, *(package_key(by) for by in found)] if found else []


def package_key(by: ByElection) -> str:
    return f"{SOURCE}/{by.day}-by-election"


def parse() -> None:
    if not urls():
        return
    registry().write()
    for by in urls():
        election(by).write()


def _int(text: str) -> int | None:
    found = ec.clean(text).replace(",", "")
    if re.fullmatch(r"\d+(\.0+)?", found):
        return int(float(found))
    return None


def _cell(value: object) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return ec.clean(str(value)) if value is not None else ""


def _book(path: Path) -> dict[str, list[list[str]]]:
    """Every sheet of a workbook as rows of text."""
    if path.suffix == ".xlsx":
        import fastexcel

        book = fastexcel.read_excel(str(path))
        return {
            name: [
                [_cell(cell) for cell in row]
                for row in book.load_sheet_by_name(name, header_row=None).to_polars().iter_rows()
            ]
            for name in book.sheet_names
        }
    workbook = xlrd.open_workbook(str(path))
    return {
        sheet.name: [[_cell(sheet.cell_value(r, c)) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
        for sheet in workbook.sheets()
    }


def _fix(text: str, fixed: list[str]) -> str:
    for printed, right in PRINTED.items():
        if printed in text:
            text = text.replace(printed, right)
            if printed not in fixed:
                fixed.append(printed)
    return text


MAJORITY = re.compile(r"^(.*?) - (?:.* - )?majority ([\d,]+)$")


def _summary(rows: list[list[str]], fixed: list[str]) -> tuple[list[str], list[str], list[int], str | None, int | None]:
    """The candidates of a summary sheet with their parties and votes, and the winner and majority it prints."""
    at = next(
        (r, row.index("Electorate Candidate Valid Votes"))
        for r, row in enumerate(rows)
        if "Electorate Candidate Valid Votes" in row
    )
    names, parties, votes = [], [], []
    for row in rows[at[0] + 1 :]:
        found = _int(row[at[1] + 2]) if len(row) > at[1] + 2 else None
        if row[at[1]] and found is not None:
            names.append(row[at[1]])
            parties.append(_fix(row[at[1] + 1], fixed))
            votes.append(found)
    winner = majority = None
    for row in rows:
        for text in row:
            won = MAJORITY.match(text)
            if won:
                winner, majority = won.group(1), int(won.group(2).replace(",", ""))
    return names, parties, votes, winner, majority


def _table(rows: list[list[str]], url: str) -> tuple[list[str], list[ec.Row], list[int]]:
    """The candidate table of a workbook: the column headings, each place's row, and the electorate's total row."""
    head = next(r for r, row in enumerate(rows) if row and row[0].endswith("Candidate Vote Details"))
    header = rows[head]
    valid = header.index("Total Valid Candidate Votes")
    informal = header.index("Informal Candidate Votes")
    columns = [c for c in range(1, valid) if header[c]]
    first = columns[0]
    wanted = [*columns, valid, informal]
    labels = [header[c] for c in columns]
    section = ""
    found: list[ec.Row] = []
    total: list[int] = []
    for row in rows[head + 1 :]:
        row = [*row, *[""] * (informal + 1 - len(row))]
        values = [_int(row[c]) for c in wanted]
        name = ec.clean(" ".join(c for c in row[:first] if c))
        if all(v is not None for v in values):
            ints = [int(v) for v in values if v is not None]
            if re.search(r"(^|\s)total$", name, re.IGNORECASE):
                total = ints
                break
            area = ec.clean(row[0]) if first == 2 and row[0] and row[1] else ""
            found.append(ec.Row(section, ec.clean(row[1]) if area else name, ints, area))
        elif name and not any(_int(row[c]) is not None for c in wanted):
            if SECTION_ROWS.match(name):
                section = "Advance Voting Places" if name.lower().startswith("advance") else "Voting Places"
    if not total:
        raise ValueError(f"{url}: the candidate table prints no total row")
    return labels, found, total


def _turnout(rows: list[list[str]]) -> tuple[int, int, int]:
    """The valid and informal votes and the electors on the roll that a turnout sheet prints for the electorate."""
    header = next(row for row in rows if any(cell.startswith("Electors on Master Roll") for cell in row))
    roll = next(i for i, cell in enumerate(header) if cell.startswith("Electors on Master Roll"))
    last = next(r for r, row in enumerate(rows) if row is header)
    labels = next(row for row in rows[last : last + 3] if any(cell.startswith("Valid Total") for cell in row))
    valid = next(i for i, cell in enumerate(labels) if cell.startswith("Valid Total"))
    informal = next(i for i, cell in enumerate(labels) if cell.startswith("Informal Total"))
    data = next(
        row
        for row in rows[last + 1 :]
        if row and row[0] and not row[0].startswith("(") and len(row) > roll and _int(row[roll]) is not None
    )
    got = [_int(data[i]) for i in (valid, informal, roll)]
    if any(v is None for v in got):
        raise ValueError("a turnout sheet prints no numbers")
    return int(got[0] or 0), int(got[1] or 0), int(got[2] or 0)


def _sheet(book: dict[str, list[list[str]]], *names: str) -> list[list[str]]:
    for name, rows in book.items():
        if any(name.lower().startswith(want) for want in names):
            return rows
    raise LookupError(f"no sheet starts with {names}")


@functools.cache
def contest(day: str) -> Contest:
    by = next(b for b in BY_ELECTIONS if b.day == day)
    path = ec.art(by.url).path
    if day == "1998-05-02":
        return _contest_1998(by.url, path)
    if by.suffix == ".csv":
        return _contest_page(by.url)
    fixed: list[str] = []
    book = _book(path)
    labels, rows, total = _table(_sheet(book, "candidate", "part viii")[:], by.url)
    summary = _sheet(book, "candidate summ", "candidate sum", "part viii graph")
    names, parties, votes, winner, majority = _summary(summary, fixed)
    if votes != total[:-2] or len(names) != len(labels):
        raise ValueError(f"{by.url}: the summary's candidates and votes differ from the table's total row")
    valid, informal, roll = _turnout(_sheet(book, "2.2", "part ix"))
    if [valid, informal] != total[-2:]:
        raise ValueError(f"{by.url}: the turnout sheet prints {valid} valid and {informal} informal, not {total[-2:]}")
    return _checked(by.url, Contest(names, parties, rows, total, winner, majority, roll, fixed))


def _checked(url: str, found: Contest) -> Contest:
    width = len(found.total)
    sums = [sum(row.values[i] for row in found.rows) for i in range(width)]
    if sums != found.total:
        raise ValueError(f"{url}: the places sum to {sums}, but the file prints {found.total}")
    if found.winner is not None:
        if found.winner not in found.names:
            raise ValueError(f"{url}: the winner {found.winner} is not a candidate")
        ranked = sorted(found.total[: len(found.names)], reverse=True)
        top = found.total[found.names.index(found.winner)]
        if top != ranked[0] or found.majority != ranked[0] - ranked[1]:
            raise ValueError(f"{url}: the winner and majority differ from the votes")
    return found


def _contest_page(url: str) -> Contest:
    page = ec._read_rows(url, ec._csv_rows(url))
    parties = [page.parties[label] for label in page.labels]
    majority = None
    ranked = sorted(page.total[:-2], reverse=True)
    if page.winner is not None:
        majority = ranked[0] - ranked[1]
    return _checked(url, Contest(page.labels, parties, page.rows, page.total, page.winner, majority, None))


def _contest_1998(url: str, path: Path) -> Contest:
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(path.read_text("utf-8-sig")))]
    header = rows[0]
    valid = header.index("Total Valid Votes")
    informal = header.index("Informal Votes")
    columns = [c for c in range(2, valid) if header[c]]
    names: list[str] = []
    parties: list[str] = []
    for c in columns:
        heading = ec.clean(header[c])
        party = next(
            (p for p in sorted(PARTIES_1998, key=len, reverse=True) if heading.lower().endswith(" " + p.lower())), None
        )
        if party is None:
            raise ValueError(f"{url}: no known party ends the heading {heading!r}")
        names.append(heading[: -len(party)].strip())
        parties.append(PARTIES_1998[party])
    wanted = [*columns, valid, informal]
    found: list[ec.Row] = []
    total: list[int] = []
    for row in rows[1:]:
        if len(row) != len(header):
            break
        values = [_int(row[c]) for c in wanted]
        if any(v is None for v in values):
            continue
        ints = [int(v) for v in values if v is not None]
        name = ec.clean(" ".join(cell for cell in row[:2] if cell))
        if not name:
            total = ints
            break
        found.append(ec.Row("", name, ints))
    at = next(r for r, row in enumerate(rows) if "Electors on Master Roll" in row)
    # The turnout table's column headings: the valid total, the informal total, and the roll.
    table = rows[at - 1 : at + 6]
    labels = next(row for row in table if "Valid Total (a)+(b)" in row)
    held = next(
        row
        for row in rows[at:]
        if len(row) == len(labels) and _int(row[labels.index("Valid Total (a)+(b)")]) is not None
    )
    got = [_int(held[labels.index(text)]) for text in ("Valid Total (a)+(b)", "Informal Total (d)+(e)")]
    roll = _int(held[rows[at].index("Electors on Master Roll")])
    if got != total[-2:] or roll is None:
        raise ValueError(f"{url}: the turnout table disagrees with the places' total")
    return _checked(url, Contest(names, parties, found, total, None, None, roll))


def party_of(printed: str) -> str | None:
    """The party a printed name stands for: none for an independent, the shared registry's party for a main party,
    else this registry's."""
    name = ec.clean(printed)
    if not name or name.casefold() == "independent":
        return None
    if name.casefold() in SHARED_PARTIES:
        return ec.party_id(name)
    return party_key(name)


def local_parties() -> dict[str, str]:
    """The parties of the by-elections that the main registry does not hold, by id."""
    made: dict[str, str] = {}
    for by in urls():
        for printed in contest(by.day).parties:
            id = party_of(printed)
            if id is not None and ec.clean(printed).casefold() not in SHARED_PARTIES:
                made.setdefault(id, ec.clean(printed))
    return made


def registry() -> Package:
    p = Package(
        BY_REGISTRY,
        SOURCE,
        "The parties that stood candidates in New Zealand's by-elections to the House of Representatives and are not in "
        "the general elections' registry",
    )
    p.external.add(NEW_ZEALAND)
    for id, name in sorted(local_parties().items(), key=lambda item: (item[1].casefold(), item[1])):
        p.add("party", id=id, kind="party", polity_id=NEW_ZEALAND)
        p.name(id, name, "en", "official")
    return p


def _unit(p: Package, made: dict[str, str], by: ByElection, row: ec.Row, channel: int, aggregate: bool) -> str:
    where = f"{row.area}:" if row.area else ""
    name = row.name
    if channel == ec.EARLY:
        code = f"by:{by.day}:advance:{where}{name}"
        if not name.startswith(("Hospital Votes", "Ordinary Votes")):
            name = f"{name}, advance voting"
    else:
        code = f"by:{by.day}:{where}{name}"
    if row.area and row.area not in name:
        name = f"{name}, {row.area}"
    if code not in made:
        unit = made[code] = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NEW_ZEALAND,
            scheme_code=ec.UNIT_SCHEME,
            code=code,
            area_relation="unlocated",
            area_id=None,
            kind="aggregate" if aggregate else "standard",
        )
        p.name(unit, f"{name} ({by.electorate})", "en", "official")
    return made[code]


def _channel(row: ec.Row) -> tuple[int, bool]:
    """A row's channel and whether it is an aggregate: votes taken before election day are advance votes, special and
    overseas votes belong to no channel, and each other row is a voting place of its section."""
    name = row.name
    if re.match(r"^(Hospital Votes|Ordinary Votes)", name, re.IGNORECASE) and re.search(r"BEFORE", name, re.IGNORECASE):
        return ec.EARLY, True
    if re.match(r"^(Hospital Votes)", name, re.IGNORECASE):
        return ec.EARLY, True
    if re.match(r"^(Polling places where less than)", name, re.IGNORECASE):
        return ec.ELECTION_DAY, True
    if re.match(r"^(Overseas|Special Votes)", name, re.IGNORECASE):
        return 0, True
    aggregate = bool(re.match(r"^Voting places where less than", name, re.IGNORECASE))
    return (ec.EARLY if row.section.startswith("Advance") else ec.ELECTION_DAY), aggregate


def election(by: ByElection) -> Package:
    found = contest(by.day)
    year = by.day[:4]
    p = Package(
        package_key(by),
        SOURCE,
        f"The {year} {by.electorate} by-election by voting place, from the Electoral Commission's results files",
        depends=[f"{SOURCE}/registry", BY_REGISTRY],
    )
    p.external.add(NEW_ZEALAND)
    p.uses(ec.art(by.url))
    day = date.fromisoformat(by.day)
    english = f"{year} {by.electorate} by-election"
    path = f"nz/{by.day}-by-election"
    poll = ec._election(p, f"by-{by.day}", day, "by_election", english, path)
    race = ec._contest(
        p,
        f"by-{by.day}",
        poll,
        "body_seats",
        english,
        f"{path}/{ec.slug(by.electorate)}",
        body_id=ec.PARLIAMENT,
        magnitude=1,
        electoral_system_id=ec.SYSTEM_ELECTORATE,
    )
    method = METHOD
    if found.fixed:
        method += " The 2004 spreadsheet prints the Māori Party as “Mâori Party”; it is read as the Māori Party."
    if found.winner is None:
        method += (
            " The file prints no winner, so none is declared here; the candidate with the most votes is the one the "
            "plurality rule returns."
        )
    if found.roll is None:
        method += " The file prints no electors on the roll, so none are held."
    result_set = ec._result_set(p, poll, race, f"by-{by.day}", method)
    choice: list[str] = []
    for position, (label, printed) in enumerate(zip(found.names, found.parties, strict=True), 1):
        option = key("option", by.day, label)
        person = key("person", by.day, label)
        party = party_of(printed)
        p.add("person", id=person)
        p.name(person, ec.person_name(label), "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=race,
            kind="candidate",
            party_id=party,
            ballot_position=position,
            ballot_label=label,
        )
        p.add(
            "candidacy",
            id=key("candidacy", by.day, label),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=label,
            member_of_party_id=party,
            is_independent=party is None,
        )
        choice.append(option)
        if found.winner == label:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
    made: dict[str, str] = {}
    for row in found.rows:
        channel, aggregate = _channel(row)
        unit = _unit(p, made, by, row, channel, aggregate)
        for option, value in zip(choice, row.values, strict=False):
            ec._put(p, result_set, unit, channel, option, ec.VOTES, value)
        ec._put(p, result_set, unit, channel, None, ec.VALID, row.values[-2])
        ec._put(p, result_set, unit, channel, None, ec.INVALID, row.values[-1])
    for option, value in zip(choice, found.total, strict=False):
        p.add("declared", result_set_id=result_set, option_id=option, measure=ec.VOTES, value=value)
    p.add("declared", result_set_id=result_set, option_id=None, measure=ec.VALID, value=found.total[-2])
    p.add("declared", result_set_id=result_set, option_id=None, measure=ec.INVALID, value=found.total[-1])
    if found.roll is not None:
        code = f"by:{by.day}:{by.electorate}"
        unit = key("unit", "electorate", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NEW_ZEALAND,
            scheme_code=ec.ELECTORATE_SCHEME,
            code=code,
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, by.electorate, "en", "official")
        ec._put(p, result_set, unit, 0, None, ec.REGISTERED, found.roll)
    return p
