"""Parsing of the Commission's results portal for the House of Representatives, 2011 to 2025.

The portal publishes, for each election, a national tally page, a page for each county, a page for each electoral
district, and a page for each voting precinct with a column for each polling place. Each edition was saved as one
file that holds those pages in order, each introduced by a comment (`<!-- EDITION PAGE -->`, `<!-- COUNTY PAGE -->`,
`<!-- DISTRICT district3.php id=21003 -->`, `<!-- PRECINCT precint3.php id=21004 -->`). A precinct the portal could not
serve holds an error message instead of a table.
"""

import functools
import re
from dataclasses import dataclass, field

from ea.sources.lr import president

SOURCE = "lr.nec"
SAVED = "browser://20261119-211323-147cdc/"
EDITION_PAGE = "https://results.necliberia.org/representative.php"
DATES = "https://en.wikipedia.org/wiki/List_of_by-elections_in_Liberia"


@dataclass(frozen=True)
class Edition:
    """One edition of the portal's House results: its polling day and the saved file."""

    day: str
    url: str
    general: bool


EDITIONS = [
    Edition("2011-10-11", SAVED + "lr-2011-house-all-counties-districts-precincts.html", True),
    Edition("2017-10-10", SAVED + "lr-2017-house-all-counties-districts-precincts.html", True),
    Edition("2023-10-10", EDITION_PAGE, True),
    Edition("2018-11-20", SAVED + "lr-2018-house-byelection-districts-precincts.html", False),
    Edition("2020-12-08", SAVED + "lr-2020-house-byelection-districts-precincts.html", False),
    Edition("2021-11-16", SAVED + "lr-2021-house-byelection-districts-precincts.html", False),
    Edition("2024-04-23", SAVED + "lr-2024-house-byelection-districts-precincts.html", False),
    Edition("2025-08-12", SAVED + "lr-2025-house-byelection-nimba-districts-precincts.html", False),
]


def urls() -> list[str]:
    return [edition.url for edition in EDITIONS]


def label(text: str) -> str:
    """A candidate as the portal prints it, with the stray spaces some pages carry taken out."""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"\(\s*Independent\s*\)$", "(IND)", text)
    return re.sub(r"\(\s*([^)]*?)\s*\)\s*$", lambda found: f"({found.group(1)})", text)


@dataclass
class District:
    county: str
    number: int
    registered: int
    received: int
    expected: int
    options: dict[str, int] = field(default_factory=dict)
    valid: int = 0
    invalid: int = 0
    total: int = 0


@dataclass
class Tally:
    """A tally page's figures: polling places received and expected, registered voters, and ballots cast."""

    received: int
    expected: int
    registered: int
    total: int


@dataclass
class Parsed:
    national: Tally
    counties: dict[str, Tally]
    districts: list[District]
    precincts: list[president.Precinct]
    errors: int


def _read(url: str) -> str:
    return president._read(url)


def _district(code: str, body: str) -> District:
    text = president._text(body)
    number = re.search(r"Electoral District\s+(\d+)", text)
    registered = re.search(r"Number of Registered Voters: ([\d,]+)", text)
    received = re.search(r"Received [\d.]+% \(([\d,]+) of ([\d,]+) Polling Places\)", text)
    if number is None or registered is None or received is None:
        raise ValueError(f"district {code}: header not read")
    district = District(
        f"{int(code) // 1000:02d}",
        int(number.group(1)),
        president.number(registered.group(1)),
        president.number(received.group(1)),
        president.number(received.group(2)),
    )
    for name, votes in re.findall(r"^\d+ \|\n\|\n(\S[^\n|]*?) \|\n([\d,]+) \|\n", text, re.MULTILINE):
        found = label(name)
        if found in district.options:
            raise ValueError(f"district {code}: {found!r} twice")
        district.options[found] = president.number(votes)
    for name, value in re.findall(r"^Total( Valid| Invalid|) \|\n([\d,]+) \|", text, re.MULTILINE):
        count = president.number(value)
        if name == " Valid":
            district.valid = count
        elif name == " Invalid":
            district.invalid = count
        else:
            district.total = count
    if not district.options or sum(district.options.values()) != district.valid:
        raise ValueError(f"district {code}: candidates do not add up to the valid votes")
    if district.valid + district.invalid != district.total:
        raise ValueError(f"district {code}: valid and invalid votes do not add up")
    return district


def _edition_tallies(body: str) -> tuple[Tally, dict[str, Tally]]:
    text = president._text(body)
    pattern = (
        r"Received [\d.]+% \(([\d,]+) of ([\d,]+)(?: Polling Places)?\)\n"
        r"Registered Voters: ([\d,]+)\n(?:Turnout|Total Votes \(Valid \+ Invalid\)): ([\d,]+)"
    )

    def tally(found: re.Match[str]) -> Tally:
        return Tally(*(president.number(found.group(i)) for i in range(1, 5)))

    national_at = text.index("National Tally")
    national = re.search(pattern, text[national_at:])
    if national is None:
        raise ValueError("no national tally")
    counties: dict[str, Tally] = {}
    sections = re.split(r"RESULTS\n(\S[^\n]*?)\n\d{4} Elections Results\n", text[national_at:])
    for i in range(1, len(sections), 2):
        found = re.search(pattern, sections[i + 1])
        if found is None:
            raise ValueError(f"no tally for {sections[i]}")
        counties[sections[i]] = tally(found)
    return tally(national), counties


@functools.cache
def parsed(day: str) -> Parsed:
    edition = next(e for e in EDITIONS if e.day == day)
    parts = re.split(r"<!-- (EDITION|COUNTY|DISTRICT|PRECINCT) ([^>]*?) -->", _read(edition.url))
    national: Tally | None = None
    counties: dict[str, Tally] = {}
    districts: dict[tuple[str, int], District] = {}
    precincts: dict[str, president.Precinct] = {}
    errors = 0
    for i in range(1, len(parts), 3):
        kind, what, body = parts[i], parts[i + 1], parts[i + 2]
        if what.startswith("LIST"):
            continue
        if kind == "EDITION":
            national, counties = _edition_tallies(body)
        elif kind == "DISTRICT":
            found = re.search(r"id=(\d+)", what)
            assert found is not None
            district = _district(found.group(1), body)
            districts[(district.county, district.number)] = district
        elif kind == "PRECINCT":
            found = re.search(r"id=(\d+)", what)
            assert found is not None
            code = f"{int(found.group(1)):05d}"
            precinct = president._precinct(code, "", body)
            if precinct is None:
                errors += 1
                continue
            precinct.candidates = {label(name): votes for name, votes in precinct.candidates.items()}
            president._check(precinct)
            precincts[code] = precinct
    if national is None:
        raise ValueError(f"{day}: no national tally")
    return Parsed(
        national,
        counties,
        sorted(districts.values(), key=lambda d: (d.county, d.number)),
        list(precincts.values()),
        errors,
    )


@functools.cache
def misfiled(day: str) -> frozenset[str]:
    """The precincts whose page lists the candidates of another district than the one its heading names.

    In 2011 and 2017 a few precinct pages do: the district pages count none of their candidate votes, though they
    count their invalid votes. The polling-place results of those precincts are not stored as results of the
    district they are headed with, because they name its candidates no more than any other district's."""
    found = parsed(day)
    contests = {(d.county, d.number): set(d.options) for d in found.districts}
    return frozenset(
        precinct.code
        for precinct in found.precincts
        if set(precinct.candidates) != contests[(precinct.county, precinct.district)]
    )
