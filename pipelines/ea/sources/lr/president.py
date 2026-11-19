"""Parsing of the Commission's results portal for the presidential elections of 2011, 2017, and 2023.

The portal publishes, for each voting precinct, one page with a column for each polling place. The pages were saved
from the portal's own precinct endpoint, one file for each round (a few rounds in two parts), and each file holds one
`<!-- PRECINCT code | County -->` block for each precinct. A precinct the portal could not serve holds an error
message instead of a table. The national and county tallies are the portal's tally pages.
"""

import functools
import re
from dataclasses import dataclass, field

from ea import raw

SOURCE = "lr.nec"
SAVED = "browser://20261117-134037-3e9efd/"
LIVE = "https://results.necliberia.org/"
WAYBACK = "https://web.archive.org/web/"
QUERY = "?&4df8f4a95b9ccaeffba51d2193fe766d30fe57c666ac68e0b618687f8a71b989="


@dataclass(frozen=True)
class Round:
    """One round of a presidential election: its day, the saved precinct files, and the portal's tally page."""

    day: str
    number: int
    files: tuple[str, ...]
    tally: str
    # A round's registered-voter figure on the tally page is stale on a run-off page, so only first rounds use it.
    registered: bool


ROUNDS = [
    Round(
        "2011-10-11",
        1,
        (
            SAVED + "lr-2011-president-round1-precinct-results-part1.html",
            SAVED + "lr-2011-president-round1-precinct-results-part2.html",
        ),
        LIVE + "president.php",
        True,
    ),
    Round("2011-11-08", 2, (SAVED + "lr-2011-president-runoff-precinct-results.html",), LIVE + "president2.php", False),
    Round(
        "2017-10-10",
        1,
        (SAVED + "lr-2017-president-round1-precinct-results.html",),
        WAYBACK + "20250420135433id_/https://results.necliberia.org/president.php" + QUERY + "NTc%3D",
        True,
    ),
    Round(
        "2017-12-26",
        2,
        (SAVED + "lr-2017-president-runoff-precinct-results.html",),
        WAYBACK + "20241214085818id_/https://results.necliberia.org/president2.php" + QUERY + "NTc%3D",
        False,
    ),
    Round(
        "2023-10-10",
        1,
        (
            SAVED + "lr-2023-president-round1-precinct-results-part1.html",
            SAVED + "lr-2023-president-round1-precinct-results-part2.html",
        ),
        WAYBACK + "20250208040558id_/https://results.necliberia.org/president.php" + QUERY + "NDQzNw%3D%3D",
        True,
    ),
]


def urls() -> list[str]:
    found: list[str] = []
    for round in ROUNDS:
        for url in (*round.files, round.tally):
            if url not in found:
                found.append(url)
    return found


@dataclass
class Precinct:
    code: str
    county_name: str
    name: str
    district: int
    registered: int
    turnout: int
    places: list[str]
    candidates: dict[str, list[int]] = field(default_factory=dict)
    valid: list[int] = field(default_factory=list)
    invalid: list[int] = field(default_factory=list)
    total: list[int] = field(default_factory=list)

    @property
    def county(self) -> str:
        return self.code[:2]


def number(text: str) -> int:
    return int(text.replace(",", ""))


def _text(html: str) -> str:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.DOTALL)
    body = re.sub(r"</t[dh]>", " |\n", body)
    body = re.sub(r"<br\s*/?>|</tr>|</p>|</div>|</h\d>|</li>", "\n", body)
    body = re.sub(r"<[^>]+>", "", body).replace("&nbsp;", " ").replace("&amp;", "&")
    return "\n".join(line for line in (re.sub(r"\s+", " ", line).strip() for line in body.split("\n")) if line)


def _read(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_bytes().decode("utf-8", errors="replace")


def _precinct(code: str, county: str, body: str) -> Precinct | None:
    """The precinct's tables, or None when the portal answered with an error."""
    body = re.sub(r"</t([dh])(?=\s*<)", r"</t\1>", body)
    found = re.search(r"Precinct (\d+) - (.*?)\s*</h3>", body, re.DOTALL)
    if found is None:
        return None
    district = re.search(r"Electoral District (\d+)", body)
    registered = re.search(r"Registered Voters:\s*([\d,]+)", body)
    turnout = re.search(r"Precinct Turnout:\s*([\d,]+)", body)
    if district is None or registered is None or turnout is None or int(found.group(1)) != int(code):
        raise ValueError(f"precinct {code}: header not read")
    rows: list[list[str]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)(?=<tr|</tbody|</tfoot|$)", body, re.DOTALL):
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell.replace("&nbsp;", " "))).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        if cells:
            rows.append(cells)
    places = rows[0][1:-1]
    precinct = Precinct(
        code,
        re.sub(r"\s+Voting Precincts.*", "", county).strip(),
        re.sub(r"\s+", " ", found.group(2)),
        int(district.group(1)),
        number(registered.group(1)),
        number(turnout.group(1)),
        places,
    )
    for row in rows[1:]:
        values = [number(cell) for cell in row[1:]]
        if len(values) != len(places) + 1 or sum(values[:-1]) != values[-1]:
            raise ValueError(f"precinct {code}: {row[0]!r} does not add up across its polling places")
        if row[0] == "Valid Votes":
            precinct.valid = values
        elif row[0] == "Invalid Votes":
            precinct.invalid = values
        elif row[0] == "Total":
            precinct.total = values
        else:
            precinct.candidates[row[0]] = values
    return precinct


@functools.cache
def precincts(day: str, number_: int) -> list[Precinct]:
    """Every precinct the portal served for the round, in its own order, each once."""
    round = next(r for r in ROUNDS if r.day == day and r.number == number_)
    found: dict[str, Precinct] = {}
    for url in round.files:
        parts = re.split(r"<!-- PRECINCT (\d+) \| ([^>]*?) -->", _read(url))
        for i in range(1, len(parts), 3):
            precinct = _precinct(parts[i], parts[i + 1].strip(), parts[i + 2])
            if precinct is None:
                continue
            if precinct.code in found and found[precinct.code] != precinct:
                raise ValueError(f"precinct {precinct.code} differs between the parts of {day}")
            found[precinct.code] = precinct
    for precinct in found.values():
        _check(precinct)
    return list(found.values())


def _check(precinct: Precinct) -> None:
    n = len(precinct.places) + 1
    for i in range(n):
        valid = sum(votes[i] for votes in precinct.candidates.values())
        if valid != precinct.valid[i] or precinct.valid[i] + precinct.invalid[i] != precinct.total[i]:
            raise ValueError(f"precinct {precinct.code}: column {i} does not add up")
    if precinct.total[-1] != precinct.turnout:
        raise ValueError(f"precinct {precinct.code}: turnout {precinct.turnout} is not its total {precinct.total[-1]}")


@dataclass
class Tally:
    """A tally page's totals: candidates' votes, the total of valid and invalid votes, and registered voters."""

    candidates: dict[str, int] = field(default_factory=dict)
    total: int | None = None
    registered: int | None = None
    places: int | None = None


def _tally(section: str) -> Tally:
    tally = Tally()
    # A candidate row prints the name, the votes, and the share, each in its own cell.
    head = section.split("Click")[0]
    for name, votes in re.findall(r"^(\S[^\n|]*\([A-Za-z]+\)) \|\n([\d,]+) \|", head, re.MULTILINE):
        tally.candidates[name.strip()] = number(votes)
    total = re.search(r"Total Votes \(Valid \+ Invalid\): ([\d,]+)", section)
    tally.total = number(total.group(1)) if total else None
    received = re.search(r"Received [\d.]+% \(([\d,]+) of", section)
    tally.places = number(received.group(1)) if received else None
    return tally


@functools.cache
def tallies(url: str) -> tuple[Tally, dict[str, Tally]]:
    """The national tally of a tally page, and its county tallies by the county name the page prints."""
    text = _text(_read(url))
    registered = re.search(r"([\d,]+)\nVoters Registered", text)
    parts = re.split(
        r"\n[^\n]*PRESIDENTIAL ELECTION RESULTS\n(\S[^\n]*? County)\nFinal Results", text, flags=re.IGNORECASE
    )
    national = _tally(parts[0].split("National Tally", 1)[-1])
    if registered:
        national.registered = number(registered.group(1))
    counties = {parts[i]: _tally(parts[i + 1]) for i in range(1, len(parts), 2)}
    return national, counties
