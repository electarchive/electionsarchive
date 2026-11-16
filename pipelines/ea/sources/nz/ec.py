import csv
import functools
import io
import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from uuid import uuid5

import httpx

from ea import package, raw
from ea.package import Package

SOURCE = "nz.ec"
SITE = "https://electionresults.govt.nz"
NEW_ZEALAND = str(uuid5(package.NAMESPACE, "polity\x1fNZ"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
EARLY, ELECTION_DAY = 2, 1
UNIT_SCHEME = "nz.voting_place"
ELECTORATE_SCHEME = "nz.electorate"
NATION_SCHEME = "nz.nation"

# The general elections the authority's results site publishes. From 2017 it publishes a page per electorate and vote
# with the count at each voting place; for 2002 to 2014 the cached statistics give each electorate's party votes.
# An election is built at the finest level its cached files reach.
GENERAL = {
    "2002-07-27": 2002,
    "2005-09-17": 2005,
    "2008-11-08": 2008,
    "2011-11-26": 2011,
    "2014-09-20": 2014,
    "2017-09-23": 2017,
    "2020-10-17": 2020,
    "2023-10-14": 2023,
}
REFERENDUM_DAY = "2011-11-26"
FETCH_YEARS = (2017, 2020, 2023)
E9_YEARS = (2002, 2005, 2008, 2011, 2014)
E9_PARTS = ("e9_part1.csv", "e9_part4.csv", "e9_part6.csv", "e9_part9_1.csv", "e9_part9_2.csv")
SUMMARIES = (
    "overall-results-summary.csv",
    "winning-electorate-candidates.csv",
    "votes-for-registered-parties-by-electorate.csv",
    "party-votes-and-turnout-by-electorate.csv",
    "candidate-votes-and-turnout-by-electorate.csv",
    "voting-place-coordinates.csv",
)
REFERENDUM_URL = f"{SITE}/electionresults_2011/referendum_ppresults.csv"

METHOD = (
    "Counted per voting place and published by the Electoral Commission. A place open for advance voting and again on "
    "election day is two units, one for each channel. Special declaration votes, overseas votes, and the votes "
    "of places where fewer than six votes were taken are reported only above the voting place, so each is an "
    "aggregate unit of its electorate."
)
SPECIAL = re.compile(
    r"^(Overseas Special Votes|Overseas Votes Allowed|Special Votes|Votes Allowed for|Voting places where less than)"
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "electoral-commission")
PARLIAMENT = key("body", "house-of-representatives")
SYSTEM_PARTY = key("electoral_system", "mmp-party-vote")
SYSTEM_ELECTORATE = key("electoral_system", "electorate")
SYSTEM_REFERENDUM = key("electoral_system", "referendum")


@functools.cache
def _held() -> dict[str, str]:
    """The raw cache's entries by the URL the authority serves them from, whether fetched directly or by snapshot."""
    found: dict[str, str] = {}
    for stored in raw.keys(SOURCE):
        original = stored.split("id_/", 1)[1] if "web.archive.org/web/" in stored and "id_/" in stored else stored
        found[original.replace("://www.", "://")] = stored
    return found


def art(url: str) -> raw.Artifact:
    stored = _held().get(url.replace("://www.", "://"))
    if stored is None:
        raise LookupError(f"{url} is not in the {SOURCE} raw cache; run `ea fetch {SOURCE}` first")
    return raw.cached(SOURCE, stored)


def has(url: str) -> bool:
    return url.replace("://www.", "://") in _held()


def page_url(year: int, vote: str, number: int) -> str:
    return f"{SITE}/electionresults_{year}/statistics/{vote}-votes-by-voting-place-{number}.html"


def csv_url(year: int, name: str) -> str:
    return f"{SITE}/electionresults_{year}/statistics/csv/{name}"


def index_url(year: int) -> str:
    return f"{SITE}/electionresults_{year}/statistics/votes-by-voting-place-electorate-index.html"


def e9_url(year: int, name: str) -> str:
    return f"{SITE}/electionresults_{year}/e9/csv/{name}"


def home_url(year: int) -> str:
    return f"{SITE}/electionresults_{year}/index.html"


def fetch() -> None:
    for year in E9_YEARS:
        _try(home_url(year), ".html")
        for name in E9_PARTS:
            _try(e9_url(year, name), ".csv")
    for year in FETCH_YEARS:
        for url in (
            f"{SITE}/electionresults_{year}/index.html",
            f"{SITE}/electionresults_{year}/statistics/index.html",
        ):
            _try(url, ".html")
        _try(index_url(year), ".html")
        for name in SUMMARIES:
            _try(csv_url(year, name), ".csv")
        for vote in ("candidate", "party"):
            for number in range(1, 80):
                _try(page_url(year, vote, number), ".html")
    _try(REFERENDUM_URL, ".csv")


def _try(url: str, suffix: str) -> None:
    if has(url):
        return
    try:
        raw.fetch(SOURCE, url, suffix=suffix)
    except httpx.HTTPStatusError as failure:
        print(f"{url}: {failure.response.status_code}", flush=True)


class Cells(HTMLParser):
    """The rows of every table on a page. A table inside a cell, such as the bar charts the pages draw, keeps its
    own rows, so the outer cell holds only its own text."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: str | None = None
        self.outer: list[tuple[list[str] | None, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.outer.append((self.row, self.cell))
            self.row = self.cell = None
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = ""

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join(self.cell.split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.outer:
            self.row, self.cell = self.outer.pop()

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell += data


def number(text: str) -> int:
    return int(text.replace(",", ""))


@dataclass
class Row:
    section: str
    name: str
    values: list[int]


@dataclass
class Page:
    electorate: str
    number: int
    labels: list[str]
    rows: list[Row] = field(default_factory=list)
    total: list[int] = field(default_factory=list)
    parties: dict[str, str] = field(default_factory=dict)
    winner: str | None = None


def read_page(url: str) -> Page:
    cells = Cells()
    cells.feed(art(url).path.read_text("utf-8"))
    rows = cells.rows
    title = re.fullmatch(r"(.+) (\d+) (?:Party|Candidate) Vote Details", rows[0][0])
    if title is None:
        raise ValueError(f"{url}: unexpected title {rows[0][0]!r}")
    page = Page(title.group(1), int(title.group(2)), rows[0][1:-2])
    width = len(rows[0])
    section = ""
    after = False
    for row in rows[1:]:
        if after:
            won = re.fullmatch(r"(.+) - majority \d+", row[-1]) if row else None
            if won and won.group(1) in page.labels:
                page.winner = won.group(1)
            if len(row) >= 4 and row[0] in page.labels and row[2].replace(",", "").isdigit():
                page.parties[row[0]] = row[1]
            continue
        if len(row) == 1 and row[0]:
            section = row[0]
        elif len(row) == width and row[0].endswith(" Total"):
            page.total = [number(cell) for cell in row[1:]]
            after = True
        elif len(row) == width and row[0] and all(re.fullmatch(r"[\d,]+", cell) for cell in row[1:]):
            page.rows.append(Row(section, row[0], [number(cell) for cell in row[1:]]))
    sums = [sum(row.values[i] for row in page.rows) for i in range(width - 1)]
    if sums != page.total:
        raise ValueError(f"{url}: rows sum to {sums}, but the page prints {page.total}")
    return page


def electorates(year: int) -> dict[int, str]:
    """The electorate names by number, as the index of the voting-place pages lists them."""
    text = art(index_url(year)).path.read_text("utf-8")
    cells = Cells()
    cells.feed(text)
    links = re.findall(r"party-votes-by-voting-place-(\d+)\.html", text)
    rows = [row for row in cells.rows if len(row) >= 6 and row[0]]
    if len(rows) != len(links):
        raise ValueError(f"{year}: the index lists {len(rows)} electorates but {len(links)} party pages")
    return {int(link): row[0] for row, link in zip(rows, links, strict=True)}


def text(url: str) -> str:
    """A cached file's text; the older statistics files are in Windows-1252, the newer ones in UTF-8."""
    data = art(url).path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # The 2005 files write the macron of Māori with the byte that Windows-1252 reads as â.
        return data.decode("cp1252").replace("Mâori", "Māori").replace("MÂORI", "MĀORI")


def rows_of(url: str) -> list[list[str]]:
    return [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text(url)))]


def table(year: int, name: str) -> list[list[str]]:
    return rows_of(csv_url(year, name))


@dataclass
class Summary:
    url: str
    seats: dict[str, int]
    votes: dict[str, int]
    valid: int
    informal: int | None
    magnitude: int


GROUPS = ("Registered Parties with List", "Registered Parties, no List", "Unregistered Parties")


def summary_url(year: int) -> str | None:
    for url in (csv_url(year, "overall-results-summary.csv"), e9_url(year, "e9_part1.csv"), home_url(year)):
        if has(url):
            return url
    return None


def summary(year: int) -> Summary:
    """The national party votes and each party's seats (list and electorate together), as the authority prints them:
    from its summary of overall results, or else from the results home page."""
    url = summary_url(year)
    if url is None:
        raise LookupError(f"{year}: no summary of overall results is cached")
    if url.endswith(".html"):
        return _home_summary(year, url)
    seats: dict[str, int] = {}
    votes: dict[str, int] = {}
    state: dict[str, int] = {}
    totals: list[int] = []
    group = ""
    magnitude = 0
    in_state = False
    for row in rows_of(url):
        if not any(row):
            continue
        if row[0] == "State of the Parties":
            in_state = True
        elif in_state and len(row) >= 5 and not row[0] and row[1] == "Seats":
            magnitude = int(row[4])
            break
        elif in_state and row[0] and len(row) >= 5:
            state[row[0]] = int(row[4])
        elif row[0] in GROUPS:
            group = row[0]
        elif group and row[0] and len(row) >= 7 and row[1].isdigit():
            votes[row[0]] = int(row[2] or 0)
            won = int(row[1]) + int(row[5] or 0)
            if won:
                seats[row[0]] = won
        elif group and not row[0]:
            totals.append(int(next(cell for cell in row if cell)))
    if seats != state or sum(seats.values()) != magnitude:
        raise ValueError(f"{url}: the seats {seats} disagree with the state of the parties {state} ({magnitude})")
    if sum(votes.values()) != totals[1]:
        raise ValueError(f"{url}: the party votes sum to {sum(votes.values())}, but the total is {totals[1]}")
    return Summary(url, seats, votes, totals[1], totals[2], magnitude)


def _home_summary(year: int, url: str) -> Summary:
    cells = Cells()
    cells.feed(text(url))
    seats: dict[str, int] = {}
    votes: dict[str, int] = {}
    inside = False
    for row in cells.rows:
        if len(row) == 6 and row[1] == "Party Votes" and row[5].lower() == "total seats":
            inside = True
        elif inside and len(row) == 6 and row[0] == "Total":
            valid, magnitude = number(row[1]), number(row[5])
            break
        elif inside and len(row) == 6 and row[1] != "-":
            # A party that stood its component parties' candidates prints them below it with no votes of their own.
            votes[row[0]] = number(row[1])
            if row[5] != "-":
                seats[row[0]] = number(row[5])
    else:
        raise ValueError(f"{url}: no party table with a total row")
    if sum(votes.values()) != valid or sum(seats.values()) != magnitude:
        raise ValueError(f"{url}: the party rows do not sum to the printed total")
    return Summary(url, seats, votes, valid, None, magnitude)


def winners(year: int) -> dict[str, str]:
    return {row[0]: row[1] for row in table(year, "winning-electorate-candidates.csv")[2:] if len(row) >= 3}


def party_totals(year: int) -> dict[str, list[int]]:
    """Each electorate's party votes as the electorate table prints them: options, valid total, informal."""
    rows = table(year, "votes-for-registered-parties-by-electorate.csv")
    return {row[0]: [int(cell) for cell in row[1:-1]] for row in rows[2:] if len(row) > 3 and row[1].isdigit()}


def clean(text: str) -> str:
    return " ".join(text.split())


def person_name(text: str) -> str:
    surname, _, given = clean(text).partition(",")
    words = []
    for word in surname.split():
        if word.isupper():
            word = "-".join(part.capitalize() for part in word.split("-"))
            word = re.sub(r"^(Mc|Mac(?=[A-Z]))(.)", lambda m: m.group(1) + m.group(2).upper(), word)
        words.append(word)
    return clean(f"{given.strip()} {' '.join(words)}")


def slug(text: str) -> str:
    folded = re.sub(r"[^a-z0-9]+", "-", text.lower().replace("ā", "a").replace("ē", "e").replace("ī", "i"))
    return re.sub(r"-+", "-", folded.replace("ō", "o").replace("ū", "u")).strip("-")


def party_id(name: str) -> str:
    return key("party", name.casefold())


@functools.cache
def pages(year: int, vote: str) -> dict[int, Page]:
    found: dict[int, Page] = {}
    for n in range(1, 80):
        url = page_url(year, vote, n)
        if has(url):
            found[n] = read_page(url)
    return found


def level(year: int) -> str | None:
    """The finest level the cache reaches for a general election: voting places, electorates, or the nation."""
    if summary_url(year) is None:
        return None
    if has(index_url(year)) and pages(year, "candidate") and len(pages(year, "candidate")) == len(electorates(year)):
        return "voting_place"
    if all(has(e9_url(year, name)) for name in ("e9_part1.csv", "e9_part4.csv")):
        return "electorate"
    return "nation"


def days() -> list[str]:
    return [day for day, year in GENERAL.items() if level(year)]


def package_key(day: str) -> str:
    return f"nz.ec/{day}-general" if day == REFERENDUM_DAY else f"nz.ec/{day}"


def packages() -> list[str]:
    return ["nz.ec/registry", f"nz.ec/{REFERENDUM_DAY}", *(package_key(day) for day in days())]


def parse() -> None:
    registry().write()
    referendum().write()
    for day in days():
        general(day).write()


def emit() -> None:
    package.run("load", packages())


@functools.cache
def party_columns(year: int) -> tuple[list[str], dict[str, list[int]], list[int]]:
    """The party columns of the electorate table, named as the summary names them, each electorate's row (party
    votes, then valid and informal), and the combined totals row."""
    rows = rows_of(e9_url(year, "e9_part4.csv"))
    names = list(summary(year).votes)
    columns = []
    for header in rows[1][1:-2]:
        found = [name for name in names if name.casefold().startswith(header.casefold())]
        exact = [name for name in found if name.casefold() == header.casefold()]
        if len(exact or found) != 1:
            raise ValueError(f"{year}: the party column {header!r} matches {found} in the summary")
        columns.append((exact or found)[0])
    if rows[1][-2:] != ["Total Valid Party Votes", "Informal Party Votes"]:
        raise ValueError(f"{year}: unexpected last columns {rows[1][-2:]}")
    width = len(rows[1])
    electorate_rows: dict[str, list[int]] = {}
    combined: list[int] = []
    for row in rows[2:]:
        if len(row) < width or not row[0] or not row[1].isdigit():
            continue
        values = [int(cell) for cell in row[1:width]]
        if row[0] == "Combined Totals":
            combined = values
        elif not row[0].endswith("Totals"):
            electorate_rows[row[0]] = values
    for values in electorate_rows.values():
        if sum(values[:-2]) != values[-2]:
            raise ValueError(f"{year}: an electorate's party votes do not sum to its valid total")
    sums = [sum(values[i] for values in electorate_rows.values()) for i in range(width - 1)]
    if sums != combined:
        raise ValueError(f"{year}: the electorates sum to {sums}, but the combined totals are {combined}")
    return columns, electorate_rows, combined


def registry() -> Package:
    p = Package(
        "nz.ec/registry",
        SOURCE,
        "New Zealand's House of Representatives, its electoral systems, and its parties, from the Electoral "
        "Commission's results pages",
    )
    p.external.add(NEW_ZEALAND)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Electoral Commission of New Zealand",
        title="Official election results and statistics published by the Electoral Commission",
        url=f"{SITE}/",
        license="Official publication; reuse terms are not stated",
        attribution="New Zealand Electoral Commission",
    )
    p.add(
        "scheme",
        code=UNIT_SCHEME,
        polity_id=NEW_ZEALAND,
        kind="precinct",
        rank=2,
        noun="voting place",
    )
    p.add("body", id=PARLIAMENT, polity_id=NEW_ZEALAND, kind="unicameral")
    p.name(PARLIAMENT, "House of Representatives", "en", "official")
    p.name(PARLIAMENT, "Whare Māngai", "mi", "native", False)
    systems = (
        (
            SYSTEM_PARTY,
            {
                "formula": "sainte_lague",
                "threshold": {"percent": 5, "level": "party", "scope": "national", "constituencies": 1},
                "lists": {"order": "closed", "vacancy": "next_on_list"},
                "compensation": "constituency_seats",
            },
            (
                "Each voter casts a party vote. Parties with at least 5 percent of the party vote, or that won an "
                "electorate seat, share the 120 seats of the House in proportion to their party votes by the "
                "Sainte-Laguë method. A party's electorate seats count toward its share, so the list seats make up "
                "the rest, and a party that wins more electorate seats than its share keeps them as overhang. The "
                "declared seats are stored as published."
            ),
        ),
        (
            SYSTEM_ELECTORATE,
            {"formula": "fptp"},
            "Each voter casts an electorate vote. The candidate with the most votes in an electorate wins its seat.",
        ),
        (SYSTEM_REFERENDUM, {"formula": "referendum"}, "A question put to all voters, decided by the votes cast."),
    )
    for id, rules, text in systems:
        p.add("electoral_system", id=id, polity_id=NEW_ZEALAND, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    p.add(
        "scheme",
        code=ELECTORATE_SCHEME,
        polity_id=NEW_ZEALAND,
        kind="electoral_district",
        rank=1,
        noun="electorate",
    )
    p.add("scheme", code=NATION_SCHEME, polity_id=NEW_ZEALAND, kind="synthetic", rank=0, noun="nation")
    names: set[str] = set()
    for day in days():
        year = GENERAL[day]
        names |= {name for name, votes in summary(year).votes.items() if votes or name in summary(year).seats}
        if level(year) == "voting_place":
            for vote in ("candidate", "party"):
                for page in pages(year, vote).values():
                    names |= {*page.parties.values(), *(label for label in page.labels if vote == "party")}
        elif level(year) == "electorate":
            names |= set(party_columns(year)[0])
    # A party printed under two spellings (MANA and Mana) is one party, under the spelling that sorts first.
    latest: dict[str, str] = {}
    for name in sorted(names - {"Independent"}):
        latest.setdefault(party_id(name), name)
    for id, name in sorted(latest.items(), key=lambda item: item[1]):
        p.add("party", id=id, kind="party", polity_id=NEW_ZEALAND)
        p.name(id, name, "en", "official")
    return p


def _put(
    p: Package, result_set: str, unit: str, channel: int, option: str | None, measure: int, value: int | None
) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=channel,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def _result_set(p: Package, election: str, contest: str, name: str) -> str:
    result_set = key("result_set", name)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    return result_set


def _election(p: Package, name: str, day: date, kind: str, english: str, path: str) -> str:
    election = key("election", name)
    p.add("election", id=election, polity_id=NEW_ZEALAND, kind=kind, date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    return election


def _contest(p: Package, name: str, election: str, kind: str, english: str, path: str, **fields: object) -> str:
    contest = key("contest", name)
    row = {"round_number": 1, "default_result_kind": "counted", **fields}
    p.add("contest", id=contest, election_id=election, kind=kind, **row)
    p.name(contest, english, "en", "official")
    p.add("slug", path=path, entity_id=contest, is_current=True)
    return contest


class Units:
    """The voting places and aggregates of one election, made once and shared by its contests."""

    def __init__(self, p: Package, prefix: str) -> None:
        self.p = p
        self.prefix = prefix
        self.made: dict[str, str] = {}

    def get(self, number: int, electorate: str, name: str, aggregate: bool, channel: int = 0) -> str:
        """The unit of a place in one channel; a place open in advance and on the day is two units."""
        if channel == EARLY:
            code, name = f"{self.prefix}:{number}:advance:{name}", f"{name}, advance voting"
        else:
            code = f"{self.prefix}:{number}:{name}"
        if code not in self.made:
            unit = self.made[code] = key("unit", code)
            self.p.add(
                "reporting_unit",
                id=unit,
                polity_id=NEW_ZEALAND,
                scheme_code=UNIT_SCHEME,
                code=code,
                area_relation="unlocated",
                area_id=None,
                kind="aggregate" if aggregate else "standard",
            )
            self.p.name(unit, f"{name} ({electorate})", "en", "official")
        return self.made[code]


def _channel(row: Row) -> tuple[int, bool]:
    if SPECIAL.match(row.name) and not row.name.startswith("Voting places where less than"):
        return 0, True
    aggregate = row.name.startswith("Voting places where less than")
    return (EARLY if row.section.startswith("Advance") else ELECTION_DAY), aggregate


def _cite(p: Package, urls: list[str]) -> None:
    for url in urls:
        p.uses(art(url))


DESCRIPTION = {
    "voting_place": "by voting place, from the Electoral Commission's results pages",
    "electorate": "by electorate, from the Electoral Commission's statistics",
    "nation": "national totals, from the Electoral Commission's results page",
}


def general(day: str) -> Package:
    year = GENERAL[day]
    how = level(year)
    if how is None:
        raise LookupError(f"{year}: nothing is cached")
    first = date.fromisoformat(day)
    p = Package(
        package_key(day),
        SOURCE,
        f"The {year} New Zealand general election {DESCRIPTION[how]}",
        depends=["nz.ec/registry"],
    )
    p.external.add(NEW_ZEALAND)
    summ = summary(year)
    election = _election(
        p, f"general-{day}", first, "general", f"{year} New Zealand general election", f"nz/{day}-general"
    )
    party_contest = _contest(
        p,
        f"party-{day}",
        election,
        "list_allocation",
        f"{year} New Zealand general election, party vote",
        f"nz/{day}-general/party-vote",
        body_id=PARLIAMENT,
        magnitude=summ.magnitude,
        electoral_system_id=SYSTEM_PARTY,
    )
    party_set = _result_set(p, election, party_contest, f"party-{day}")
    if how == "voting_place":
        labels = next(iter(pages(year, "party").values())).labels
    elif how == "electorate":
        labels = party_columns(year)[0]
    else:
        labels = list(summ.votes)
    options: dict[str, str] = {}
    for position, label in enumerate(labels, 1):
        option = options[label] = key("option", f"party-{day}", label)
        p.add(
            "contest_option",
            id=option,
            contest_id=party_contest,
            kind="list",
            party_id=party_id(label),
            ballot_position=position,
            ballot_label=label,
        )
    stray = {label for label, value in summ.votes.items() if label not in options and value}
    if stray:
        raise ValueError(f"{year}: the summary prints party votes for {sorted(stray)}, which no table lists")
    for label, allocated in summ.seats.items():
        p.add("outcome", result_set_id=party_set, option_id=options[label], kind="won", seats=allocated)
    for label, value in summ.votes.items():
        if label in options:
            p.add("declared", result_set_id=party_set, option_id=options[label], measure=VOTES, value=value)
    p.add("declared", result_set_id=party_set, option_id=None, measure=VALID, value=summ.valid)
    if summ.informal is not None:
        p.add("declared", result_set_id=party_set, option_id=None, measure=INVALID, value=summ.informal)
    p.uses(art(summ.url))
    if how == "voting_place":
        _voting_places(p, day, election, party_set, options)
    elif how == "electorate":
        p.uses(art(e9_url(year, "e9_part4.csv")))
        _, electorate_rows, _ = party_columns(year)
        for number, (name, values) in enumerate(electorate_rows.items(), 1):
            code = f"{year}:{name}"
            unit = key("unit", "electorate", code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NEW_ZEALAND,
                scheme_code=ELECTORATE_SCHEME,
                code=code,
                area_relation="unlocated",
                area_id=None,
                kind="standard",
            )
            p.name(unit, name, "en", "official")
            for label, value in zip(labels, values, strict=False):
                _put(p, party_set, unit, 0, options[label], VOTES, value)
            _put(p, party_set, unit, 0, None, VALID, values[-2])
            _put(p, party_set, unit, 0, None, INVALID, values[-1])
        p.add(
            "note",
            id=key("note", party_set, "electorates"),
            entity_id=party_set,
            kind="caveat",
            body_md="The party vote is given by electorate, including each electorate's special votes, from the "
            "Electoral Commission's statistics. The electorate candidate votes and the counts at each polling "
            "place are not yet held.",
            lang="en",
        )
    else:
        code = f"{year}:nation"
        unit = key("unit", "nation", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NEW_ZEALAND,
            scheme_code=NATION_SCHEME,
            code=code,
            area_relation="unlocated",
            area_id=None,
            kind="aggregate",
        )
        p.name(unit, "New Zealand", "en", "official")
        for label, value in summ.votes.items():
            _put(p, party_set, unit, 0, options[label], VOTES, value)
        _put(p, party_set, unit, 0, None, VALID, summ.valid)
        p.add(
            "note",
            id=key("note", party_set, "nation"),
            entity_id=party_set,
            kind="caveat",
            body_md="Only the national party vote and seats, as the Electoral Commission's results page prints "
            "them, are held so far. The votes by electorate and by voting place, and the electorate contests, are "
            "not yet held.",
            lang="en",
        )
    return p


def _voting_places(p: Package, day: str, election: str, party_set: str, options: dict[str, str]) -> None:
    year = GENERAL[day]
    candidate_pages = pages(year, "candidate")
    party_pages = pages(year, "party")
    names = electorates(year)
    won = winners(year) if has(csv_url(year, "winning-electorate-candidates.csv")) else {}
    _cite(
        p,
        [
            *(page_url(year, "candidate", n) for n in candidate_pages),
            *(page_url(year, "party", n) for n in party_pages),
            index_url(year),
            *([csv_url(year, "winning-electorate-candidates.csv")] if won else []),
            *(
                [csv_url(year, "votes-for-registered-parties-by-electorate.csv")]
                if set(names) - set(party_pages)
                else []
            ),
        ],
    )
    units = Units(p, str(year))
    labels = list(options)
    missing = sorted(set(names) - set(party_pages))
    fallback = party_totals(year) if missing else {}
    for number, page in party_pages.items():
        if page.labels != labels:
            raise ValueError(f"{year} electorate {number}: party columns differ from the first page")
        for row in page.rows:
            channel, aggregate = _channel(row)
            unit = units.get(number, page.electorate, row.name, aggregate, channel)
            for label, value in zip(page.labels, row.values, strict=False):
                _put(p, party_set, unit, channel, options[label], VOTES, value)
            _put(p, party_set, unit, channel, None, VALID, row.values[-2])
            _put(p, party_set, unit, channel, None, INVALID, row.values[-1])
    for number in missing:
        electorate = names[number]
        values = fallback[electorate]
        unit = units.get(number, electorate, f"{electorate} total", True)
        for label, value in zip(labels, values, strict=False):
            _put(p, party_set, unit, 0, options[label], VOTES, value)
        _put(p, party_set, unit, 0, None, VALID, values[-2])
        _put(p, party_set, unit, 0, None, INVALID, values[-1])
    if missing:
        text = ", ".join(names[n] for n in missing)
        p.add(
            "note",
            id=key("note", party_set, "electorate-totals"),
            entity_id=party_set,
            kind="caveat",
            body_md=f"The voting-place pages of {text} were not retrieved, so those electorates hold one aggregate "
            "unit each with the electorate's printed party-vote totals.",
            lang="en",
        )

    # The electorate votes.
    for number, page in sorted(candidate_pages.items()):
        name = page.electorate
        if won and won.get(name) != page.winner:
            raise ValueError(f"{year} {name}: the page names {page.winner} the winner, the table {won.get(name)}")
        if page.winner is None or set(page.parties) != set(page.labels):
            raise ValueError(f"{year} {name}: the page lists no winner or misses a candidate's party")
        contest = _contest(
            p,
            f"electorate-{day}-{number}",
            election,
            "body_seats",
            f"{year} New Zealand general election, {name}",
            f"nz/{day}-general/{slug(name)}",
            body_id=PARLIAMENT,
            magnitude=1,
            electoral_system_id=SYSTEM_ELECTORATE,
        )
        result_set = _result_set(p, election, contest, f"electorate-{day}-{number}")
        choice: dict[str, str] = {}
        for position, label in enumerate(page.labels, 1):
            option = choice[label] = key("option", f"electorate-{day}-{number}", label)
            person = key("person", f"{day}:{number}", label)
            party = page.parties[label]
            independent = party == "Independent"
            p.add("person", id=person)
            p.name(person, person_name(label), "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None if independent else party_id(party),
                ballot_position=position,
                ballot_label=label,
            )
            p.add(
                "candidacy",
                id=key("candidacy", f"{day}:{number}", label),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=None if independent else party_id(party),
                is_independent=independent,
            )
            if page.winner == label:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        for row in page.rows:
            channel, aggregate = _channel(row)
            unit = units.get(number, name, row.name, aggregate, channel)
            for label, value in zip(page.labels, row.values, strict=False):
                _put(p, result_set, unit, channel, choice[label], VOTES, value)
            _put(p, result_set, unit, channel, None, VALID, row.values[-2])
            _put(p, result_set, unit, channel, None, INVALID, row.values[-1])
        for label, value in zip(page.labels, page.total, strict=False):
            p.add("declared", result_set_id=result_set, option_id=choice[label], measure=VOTES, value=value)
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=page.total[-2])
        p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=page.total[-1])


def referendum() -> Package:
    first = date.fromisoformat(REFERENDUM_DAY)
    p = Package(
        f"nz.ec/{REFERENDUM_DAY}",
        SOURCE,
        "The 2011 New Zealand referendum on the voting system by polling place, from the Electoral Commission's "
        "results file",
        depends=["nz.ec/registry"],
    )
    p.external.add(NEW_ZEALAND)
    p.uses(art(REFERENDUM_URL))
    rows = list(csv.reader(io.StringIO(art(REFERENDUM_URL).path.read_text("utf-8-sig"))))
    election = _election(
        p,
        f"referendum-{REFERENDUM_DAY}",
        first,
        "referendum",
        "2011 New Zealand referendum on the voting system",
        f"nz/{REFERENDUM_DAY}-referendum",
    )
    parts = (
        ("a", "Part A: keep the MMP voting system?", ("Keep", "Change"), 3, 5),
        ("b", "Part B: which voting system would you choose?", ("FPP", "PV", "STV", "SM"), 6, 10),
    )
    contests: dict[str, tuple[str, str, list[str]]] = {}
    for letter, english, answers, start, informal in parts:
        contest = _contest(
            p,
            f"referendum-{letter}",
            election,
            "ballot_question",
            f"2011 New Zealand referendum, {english}",
            f"nz/{REFERENDUM_DAY}-referendum/part-{letter}",
            electoral_system_id=SYSTEM_REFERENDUM,
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        result_set = _result_set(p, election, contest, f"referendum-{letter}")
        options = []
        for position, label in enumerate(answers, 1):
            option = key("option", f"referendum-{letter}", label)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        contests[letter] = (contest, result_set, options)
    units = Units(p, "2011-referendum")
    total: list[int] | None = None
    for row in rows[5:]:
        if row[0] == "Total":
            total = [int(cell) for cell in row[2:]]
            continue
        if len(row) != 11 or not row[0]:
            continue
        place = clean(row[1])
        electorate_number, _, electorate = row[0].partition(" ")
        unit = units.get(int(electorate_number), electorate, place, False)
        values = [int(cell) for cell in row[2:]]
        for letter, _, answers, start, informal in parts:
            _, result_set, options = contests[letter]
            valid = values[start - 2 : start - 2 + len(answers)]
            for option, value in zip(options, valid, strict=True):
                _put(p, result_set, unit, 0, option, VOTES, value)
            _put(p, result_set, unit, 0, None, CAST, values[0])
            _put(p, result_set, unit, 0, None, VALID, sum(valid))
            _put(p, result_set, unit, 0, None, INVALID, values[informal - 2])
    if total is None:
        raise ValueError("the referendum file prints no total row")
    for letter, _, answers, start, informal in parts:
        _, result_set, options = contests[letter]
        valid = total[start - 2 : start - 2 + len(answers)]
        for option, value in zip(options, valid, strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
        p.add("declared", result_set_id=result_set, option_id=None, measure=CAST, value=total[0])
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=sum(valid))
        p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=total[informal - 2])
    return p
