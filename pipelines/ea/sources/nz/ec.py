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

# General elections with voting-place pages. The authority publishes a page per electorate and vote; an election
# is built once its overall summary is in the raw cache.
GENERAL = {"2017-09-23": 2017, "2020-10-17": 2020}
REFERENDUM_DAY = "2011-11-26"
FETCH_YEARS = (2017, 2020, 2023)
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


def fetch() -> None:
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
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
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


def table(year: int, name: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(art(csv_url(year, name)).path.read_text("utf-8-sig"))))


@dataclass
class Summary:
    seats: dict[str, int]
    votes: dict[str, int]
    valid: int
    informal: int


def summary(year: int) -> Summary:
    rows = table(year, "overall-results-summary.csv")
    seats: dict[str, int] = {}
    votes: dict[str, int] = {}
    group = ""
    totals: list[list[str]] = []
    for row in rows:
        if not row or row[0] == "State of the Parties":
            if row and row[0] == "State of the Parties":
                break
            continue
        if row[0] in ("Registered Parties with List", "Unregistered Parties"):
            group = row[0]
            continue
        if row[0] and group and len(row) >= 9 and row[1].isdigit():
            seats[row[0]] = int(row[1])
            votes[row[0]] = int(row[2])
        elif group and not row[0] and all(cell.isdigit() for cell in row if cell):
            totals.append(row)
    valid, informal = (int(next(cell for cell in totals[i] if cell)) for i in (1, 2))
    return Summary(seats, votes, valid, informal)


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


def pages(year: int, vote: str) -> dict[int, Page]:
    found: dict[int, Page] = {}
    for n in range(1, 80):
        url = page_url(year, vote, n)
        if has(url):
            found[n] = read_page(url)
    return found


def days() -> list[str]:
    return [day for day, year in GENERAL.items() if has(csv_url(year, "overall-results-summary.csv"))]


def packages() -> list[str]:
    return ["nz.ec/registry", f"nz.ec/{REFERENDUM_DAY}", *(f"nz.ec/{day}" for day in days())]


def parse() -> None:
    registry().write()
    referendum().write()
    for day in days():
        general(day).write()


def emit() -> None:
    package.run("load", packages())


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
    names: set[str] = set()
    for day in days():
        year = GENERAL[day]
        for vote in ("candidate", "party"):
            for page in pages(year, vote).values():
                names |= {*page.parties.values(), *(label for label in page.labels if vote == "party")}
    for name in sorted(names - {"Independent"}):
        id = party_id(name)
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


def general(day: str) -> Package:
    year = GENERAL[day]
    first = date.fromisoformat(day)
    p = Package(
        f"nz.ec/{day}",
        SOURCE,
        f"The {year} New Zealand general election by voting place, from the Electoral Commission's results pages",
        depends=["nz.ec/registry"],
    )
    p.external.add(NEW_ZEALAND)
    candidate_pages = pages(year, "candidate")
    party_pages = pages(year, "party")
    names = electorates(year)
    _cite(
        p,
        [
            *(page_url(year, "candidate", n) for n in candidate_pages),
            *(page_url(year, "party", n) for n in party_pages),
            index_url(year),
            csv_url(year, "overall-results-summary.csv"),
            csv_url(year, "winning-electorate-candidates.csv"),
            *(
                [csv_url(year, "votes-for-registered-parties-by-electorate.csv")]
                if set(names) - set(party_pages)
                else []
            ),
        ],
    )
    election = _election(
        p, f"general-{day}", first, "general", f"{year} New Zealand general election", f"nz/{day}-general"
    )
    units = Units(p, str(year))
    summ = summary(year)
    won = winners(year)

    # The party vote.
    seats = sum(summ.seats.values())
    party_contest = _contest(
        p,
        f"party-{day}",
        election,
        "list_allocation",
        f"{year} New Zealand general election, party vote",
        f"nz/{day}-general/party-vote",
        body_id=PARLIAMENT,
        magnitude=seats,
        electoral_system_id=SYSTEM_PARTY,
    )
    party_set = _result_set(p, election, party_contest, f"party-{day}")
    first_page = next(iter(party_pages.values()))
    options: dict[str, str] = {}
    for position, label in enumerate(first_page.labels, 1):
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
        raise ValueError(f"{year}: the summary prints party votes for {sorted(stray)}, which no page lists")
    for label, allocated in summ.seats.items():
        if allocated:
            p.add("outcome", result_set_id=party_set, option_id=options[label], kind="won", seats=allocated)
    missing = sorted(set(names) - set(party_pages))
    fallback = party_totals(year) if missing else {}
    for number, page in party_pages.items():
        if page.labels != first_page.labels:
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
        for label, value in zip(first_page.labels, values, strict=False):
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
    for label, value in summ.votes.items():
        if label in options:
            p.add("declared", result_set_id=party_set, option_id=options[label], measure=VOTES, value=value)
    p.add("declared", result_set_id=party_set, option_id=None, measure=VALID, value=summ.valid)
    p.add("declared", result_set_id=party_set, option_id=None, measure=INVALID, value=summ.informal)

    # The electorate votes.
    for number, page in sorted(candidate_pages.items()):
        name = page.electorate
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
            party = page.parties.get(label)
            independent = party in (None, "Independent")
            p.add("person", id=person)
            p.name(person, person_name(label), "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None if independent else party_id(party or ""),
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
                member_of_party_id=None if independent else party_id(party or ""),
                is_independent=independent,
            )
            if won.get(name) == label:
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
    return p


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
