import csv
import functools
import io
import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from urllib.parse import unquote
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
    "1999-11-27": 1999,
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
# The polling-place results of 1999 to 2014 are saved from the results site's per-electorate files in one bundle per
# election: each file's text follows a line giving the address of the file it is.
BUNDLE_YEARS = (1999, 2002, 2005, 2008, 2011, 2014)
BUNDLE_MARK = "### SOURCE: "
E9_YEARS = (2002, 2005, 2008, 2011, 2014)
E9_PARTS = ("e9_part1.csv", "e9_part4.csv", "e9_part6.csv", "e9_part9_1.csv", "e9_part9_2.csv")
# The 1999 statistics are named by their part in roman numerals: the summary (I), the party votes by electorate (V),
# the party and informal votes with the electors on the roll (IX), and the two citizens-initiated referendums (XIII, XIV).
FILES_1999 = {"e9_part1.csv": "parti.csv", "e9_part4.csv": "partv.csv"}
PARTS_1999 = ("parti.csv", "partv.csv", "partix.csv", "partxiii.csv", "partxiv.csv")
REFERENDUMS_1999 = (
    ("partxiii.csv", "size", "reduce-house-size", "Reduce the size of the House of Representatives"),
    ("partxiv.csv", "justice", "criminal-justice-reform", "Reform of the criminal justice system"),
)
SUMMARIES = (
    "overall-results-summary.csv",
    "winning-electorate-candidates.csv",
    "votes-for-registered-parties-by-electorate.csv",
    "party-votes-and-turnout-by-electorate.csv",
    "candidate-votes-and-turnout-by-electorate.csv",
    "voting-place-coordinates.csv",
)
REFERENDUM_URL = f"{SITE}/electionresults_2011/referendum_ppresults.csv"
# The 2020 general election's lists of candidates, and its two referendums held on the same day.
LISTS_DAY = "2020-10-17"
LISTS_YEAR = 2020
LISTS_FILES = ("party-lists-of-successful-registered-parties.csv", "party-lists-of-unsuccessful-registered-parties.csv")
REFERENDUMS_PAGE = f"{SITE}/electionresults_2020/referendums-results.html"
REFERENDUMS_SHEET = f"{SITE}/electionresults_2020/referendums-statistics/ElectorateReferendumResults.xlsx"
# The voting-place files of the 2020 general election that a person saved from the results site in a browser are one
# file for all the electorates: each electorate's file in turn, after a line giving the address of the file it is.
SAVED_BUNDLE = re.compile(r"/nz-(\d{4})-(?:candidate|party)-votes-by-voting-place-all-electorates\.csv$")
SAVED_MARK = "# SOURCE: "

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


# The 2023 voting-place files that a person saved from the results site in a browser are keyed by the name of the saved
# file; each is the file the page of the same name links to, under the statistics folder's csv folder.
SAVED_YEAR = 2023


def bundle_year(stored: str) -> int | None:
    found = re.search(r"/nz-(\d{4})-general-polling-place-all-electorates\.csv$", stored)
    return int(found.group(1)) if found else None


@functools.cache
def _held() -> dict[str, str]:
    """The raw cache's entries by the URL the authority serves them from, whether fetched directly, by snapshot, or
    saved from a browser."""
    found: dict[str, str] = {}
    for stored in raw.keys(SOURCE):
        if bundle_year(stored) or SAVED_BUNDLE.search(stored):
            continue
        if stored.startswith("browser://"):
            original = csv_url(SAVED_YEAR, stored.rsplit("/", 1)[1])
        elif "web.archive.org/web/" in stored and "id_/" in stored:
            original = stored.split("id_/", 1)[1]
        else:
            original = stored
        found[original.replace("://www.", "://")] = stored
    return found


def art(url: str) -> raw.Artifact:
    stored = _held().get(url.replace("://www.", "://"))
    if stored is None:
        raise LookupError(f"{url} is not in the {SOURCE} raw cache; run `ea fetch {SOURCE}` first")
    return raw.cached(SOURCE, stored)


def has(url: str) -> bool:
    return url.replace("://www.", "://") in _held()


def page_source(year: int, vote: str, number: int) -> str | None:
    """The cached file for one electorate's page: the page itself, or else the csv file it links to."""
    for url in (page_url(year, vote, number), page_csv_url(year, vote, number)):
        if has(url):
            return url
    return None


def page_url(year: int, vote: str, number: int) -> str:
    return f"{SITE}/electionresults_{year}/statistics/{vote}-votes-by-voting-place-{number}.html"


def page_csv_url(year: int, vote: str, number: int) -> str:
    return csv_url(year, f"{vote}-votes-by-voting-place-{number}.csv")


def csv_url(year: int, name: str) -> str:
    return f"{SITE}/electionresults_{year}/statistics/csv/{name}"


def index_url(year: int) -> str:
    return f"{SITE}/electionresults_{year}/statistics/votes-by-voting-place-electorate-index.html"


def e9_url(year: int, name: str) -> str:
    if year == 1999:
        name = FILES_1999.get(name, name)
    return f"{SITE}/electionresults_{year}/e9/csv/{name}"


def home_url(year: int) -> str:
    return f"{SITE}/electionresults_{year}/index.html"


def fetch() -> None:
    for year in E9_YEARS:
        _try(home_url(year), ".html")
        for name in E9_PARTS:
            _try(e9_url(year, name), ".csv")
    for name in PARTS_1999:
        _try(e9_url(1999, name), ".csv")
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
    for name in LISTS_FILES:
        _try(csv_url(LISTS_YEAR, name), ".csv")
    _try(REFERENDUMS_PAGE, ".html")
    _try(REFERENDUMS_SHEET, ".xlsx")


def split_saved() -> None:
    """Store each electorate's file from a saved all-electorates bundle under the address it says it is, as if fetched
    from there, so that the electorate pages are read as they are for any other year."""
    for stored in sorted(raw.keys(SOURCE)):
        if not SAVED_BUNDLE.search(stored):
            continue
        whole = raw.cached(SOURCE, stored)
        data = whole.path.read_bytes().decode("utf-8-sig")
        for chunk in re.split(rf"^(?={SAVED_MARK})", data, flags=re.MULTILINE):
            if not chunk.startswith(SAVED_MARK):
                continue
            head, _, body = chunk.partition("\n")
            url = head[len(SAVED_MARK) :].strip()
            if has(url):
                continue
            raw.store(
                SOURCE,
                url,
                body.encode("utf-8"),
                suffix=".csv",
                retrieved_at=whole.retrieved_at,
                media_type=whole.media_type or "text/csv",
            )
    _held.cache_clear()


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
    return int(text.replace(",", "").strip().rstrip("*").removesuffix(".00"))


@dataclass
class Row:
    section: str
    name: str
    values: list[int]
    area: str = ""
    group: str = ""


@dataclass
class Page:
    electorate: str
    number: int
    labels: list[str]
    rows: list[Row] = field(default_factory=list)
    total: list[int] = field(default_factory=list)
    parties: dict[str, str] = field(default_factory=dict)
    winner: str | None = None
    cancelled: bool = False


def read_page(url: str) -> Page:
    if url.endswith(".csv"):
        return _read_rows(url, _csv_rows(url))
    cells = Cells()
    cells.feed(art(url).path.read_text("utf-8"))
    return _read_rows(url, cells.rows)


# An area is also printed on a line of its own above the places it holds, which the pages do not show, so only these
# headings are sections.
SECTIONS = ("Advance Voting Places", "Voting Places")


def _csv_rows(url: str) -> list[list[str]]:
    """A voting-place csv file laid out as the page's table rows: the title with the column headings, and a place's
    area and name in one cell."""
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(art(url).path.read_text("utf-8-sig")))]
    if len(rows) < 3 or len(rows[1]) != 2 or rows[2][:2] != ["", ""]:
        raise ValueError(f"{url}: unexpected layout")
    width = len(rows[2])
    laid = [[clean(f"{rows[1][0]} {rows[1][1]}"), *rows[2][2:]]]
    for row in rows[3:]:
        if len(row) == width:
            laid.append([", ".join(cell for cell in row[:2] if cell), *row[2:]])
        elif len(row) == 1:
            if row[0] in SECTIONS:
                laid.append(row)
        elif any(row):
            laid.append(row)
    return laid


def _read_rows(url: str, rows: list[list[str]]) -> Page:
    title = re.fullmatch(r"(.+) (\d+)( \(Poll Cancelled\))? (?:Party|Candidate) Vote Details", rows[0][0])
    if title is None:
        raise ValueError(f"{url}: unexpected title {rows[0][0]!r}")
    page = Page(title.group(1), int(title.group(2)), rows[0][1:-2], cancelled=bool(title.group(3)))
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


# Letters that the saved files lost, in the names they appear in.
LOST = {
    "M\ufffdori": "Māori",
    "T\ufffdNCZOS": "TÁNCZOS",
    "N\ufffdndor": "Nándor",
    "HARR\ufffd": "HARRÉ",
    "Caf\ufffd": "Café",
    "Ren\ufffde": "Renée",
}


@functools.cache
def bundle_art(year: int) -> raw.Artifact | None:
    for stored in sorted(raw.keys(SOURCE)):
        if bundle_year(stored) == year:
            return raw.cached(SOURCE, stored)
    return None


def bundled(year: int) -> bool:
    return year in BUNDLE_YEARS and bundle_art(year) is not None


@functools.cache
def bundle_files(year: int) -> dict[str, list[list[str]]]:
    """The files of a year's bundle, by the address each says it is, as rows of cells."""
    art_ = bundle_art(year)
    if art_ is None:
        raise LookupError(f"{year}: no bundle of polling-place files is cached")
    text_ = art_.path.read_text("utf-8-sig")
    for broken, fixed in LOST.items():
        text_ = text_.replace(broken, fixed)
    # What is left is an apostrophe: the saved files lost it in names such as O'Brien and in places such as St Paul's.
    text_ = re.sub(r"(?<=\w)\ufffd(?=\w)", "'", text_)
    if "\ufffd" in text_:
        raise ValueError(f"{year}: the bundle holds a character that did not survive the save")
    files: dict[str, list[list[str]]] = {}
    for chunk in re.split(rf"^(?={BUNDLE_MARK})", text_, flags=re.MULTILINE):
        if chunk.startswith(BUNDLE_MARK):
            head, _, body = chunk.partition("\n")
            files[head[len(BUNDLE_MARK) :].strip()] = [
                [cell.strip() for cell in row] for row in csv.reader(io.StringIO(body))
            ]
    return files


# 2002 to 2014 name each file e9_part8_cand_N or e9_part8_party_N; 1999 names them N_Electorate_c (candidate votes),
# _cp (their percentages and parties), _p (party votes), and _pp (their percentages).
NEW_FILE = re.compile(r"/e9_part8_(cand|party)_(\d+)\.csv$")
OLD_FILE = re.compile(r"/(\d+)_(.+)_(c|cp|p|pp)\.csv$")
NUMBER_CELL = re.compile(r"[\d,]+\*?")
AGGREGATE = re.compile(
    r"^(Overseas Special Votes|Overseas Votes Allowed|Special Votes|Votes Allowed for|Hospital Votes|"
    r"Ordinary Votes BEFORE|(Voting|Polling) places where less than)",
    re.IGNORECASE,
)


def _bundle_page(url: str, rows: list[list[str]], name: str | None) -> Page:
    """One electorate's page from a bundle. A name means the 1999 layout, which prints a place's whole name in either
    of the first two cells and no suburbs."""
    if name is not None:
        head = rows[0]
        title = re.fullmatch(r"(.+) (\d+) (?:Party|Candidate) Vote Details", clean(head[0]))
        end = next(i for i in range(2, len(head)) if not head[i])
        labels, first, width = head[2:end], 1, end + 4
        cancelled = False
    else:
        at = next(i for i, row in enumerate(rows) if len(row) > 1 and row[1].endswith(" Vote Details"))
        title = re.fullmatch(r"(.+) (\d+)( \(Poll Cancelled\))?", rows[at][0])
        head = rows[at + 1]
        if head[:2] != ["", ""] or len(head) < 5:
            raise ValueError(f"{url}: unexpected column headings")
        labels, first, width, end = head[2:-2], at + 2, len(head), len(head) - 2
        cancelled = bool(title and title.group(3))
    if title is None:
        raise ValueError(f"{url}: unexpected title {rows[0][0]!r}")
    page = Page(name or title.group(1), int(title.group(2)), labels, cancelled=cancelled)
    group = area = ""
    after = False
    for row in rows[first:]:
        if after:
            cells = [cell for cell in row if cell]
            if name is not None and cells and re.search(r" majority\s+[\d,]+$", cells[-1]):
                won = re.fullmatch(r"(.+?) - .+ majority\s+[\d,]+", cells[-1])
                if won and clean(won.group(1)) in page.labels:
                    page.winner = clean(won.group(1))
            else:
                won = re.fullmatch(r"(.+?)\s*- majority \d+", row[-1]) if row else None
                if won and clean(won.group(1)) in page.labels:
                    page.winner = clean(won.group(1))
            if len(row) >= 4 and row[0] in page.labels and row[2].replace(",", "").isdigit():
                page.parties[row[0]] = row[1]
            continue
        if name is not None:
            if len(row) < width:
                continue
            place = row[0] or row[1]
            if not place:
                continue
            gap = [row[2 + i] for i in range(len(labels))] + [row[end + 1], row[end + 3]]
            if row[0] and not AGGREGATE.match(place):
                group = place
        else:
            if len(row) != width or not row[1]:
                continue
            place, gap = row[1], [*row[2:]]
            if row[0]:
                area = row[0]
        if not all(NUMBER_CELL.fullmatch(cell) for cell in gap):
            continue
        values = [number(cell) for cell in gap]
        if clean(place).endswith(" Total"):
            page.total = values
            after = True
        elif AGGREGATE.match(place):
            page.rows.append(Row("", place, values))
        else:
            page.rows.append(Row("", place, values, "" if name is not None else area, group))
    sums = [sum(row.values[i] for row in page.rows) for i in range(len(labels) + 2)]
    if sums != page.total:
        raise ValueError(f"{url}: rows sum to {sums}, but the page prints {page.total}")
    return page


@functools.cache
def bundle_pages(year: int) -> dict[str, dict[int, Page]]:
    """A bundled year's pages by kind of vote and electorate number."""
    found: dict[str, dict[int, Page]] = {"candidate": {}, "party": {}}
    percents: dict[int, list[list[str]]] = {}
    for url, rows in bundle_files(year).items():
        new = NEW_FILE.search(url)
        old = OLD_FILE.search(url)
        if new:
            page = _bundle_page(url, rows, None)
            found["candidate" if new.group(1) == "cand" else "party"][int(new.group(2))] = page
        elif old and old.group(3) in ("c", "p"):
            vote = "candidate" if old.group(3) == "c" else "party"
            found[vote][int(old.group(1))] = _bundle_page(url, rows, unquote(old.group(2)))
        elif old and old.group(3) == "cp":
            percents[int(old.group(1))] = rows
    for n, rows in percents.items():
        page = found["candidate"][n]
        # The two files space a name differently after the comma.
        spaced = {re.sub(r"\s+", "", label).casefold(): label for label in page.labels}
        for row in rows:
            cells = [cell for cell in row if cell]
            if len(row) >= 5 and re.sub(r"\s+", "", row[1]).casefold() in spaced and row[3].isdigit():
                page.parties[spaced[re.sub(r"\s+", "", row[1]).casefold()]] = row[2]
            elif cells and (won := re.fullmatch(r"(.+?) - .+ majority\s+[\d,]+", cells[-1])):
                page.winner = spaced.get(re.sub(r"\s+", "", won.group(1)).casefold())
    return found


def electorates(year: int) -> dict[int, str]:
    """The electorate names by number, as the index of the voting-place pages lists them."""
    if bundled(year):
        return {n: page.electorate for n, page in sorted(bundle_pages(year)["party"].items())}
    if not has(index_url(year)):
        return {n: page.electorate for n, page in sorted(pages(year, "party").items())}
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
    magnitude: int | None
    # Totals added up from the electorates' printed totals, for a year whose summary of overall results is not held.
    derived: bool = False


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
    if url is None and bundled(year):
        return _derived_summary(year)
    if url is None:
        raise LookupError(f"{year}: no summary of overall results is cached")
    if year == 1999 and url == e9_url(year, "e9_part1.csv"):
        return _summary_1999(url)
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


def _summary_1999(url: str) -> Summary:
    """The 1999 summary (part I): each party's list seats and votes, the electorate seats and votes beside them, the
    valid and informal party votes, and the state of the parties with the seats of both kinds together."""
    seats: dict[str, int] = {}
    votes: dict[str, int] = {}
    state: dict[str, int] = {}
    valid = informal = magnitude = None
    group = ""
    in_state = False
    for row in rows_of(url):
        row = [*row, *[""] * (12 - len(row))]
        name = row[1]
        if name == "State of the Parties":
            in_state = True
        elif in_state and row[3] == "Seats":
            magnitude = number(row[10])
            break
        elif in_state and name:
            state[name] = number(row[10])
        elif name in GROUPS:
            group = name
        elif name == "Valid Votes":
            valid = number(row[3])
        elif name == "Informal Votes":
            informal = number(row[3])
        elif group == GROUPS[0] and name and row[3]:
            votes[name] = number(row[3])
            won = number(row[2] or "0") + number(row[8] or "0")
            if won:
                seats[name] = won
    if valid is None or informal is None or magnitude is None:
        raise ValueError(f"{url}: the summary prints no valid, informal, or seat total")
    if seats != state or sum(seats.values()) != magnitude:
        raise ValueError(f"{url}: the seats {seats} disagree with the state of the parties {state} ({magnitude})")
    if sum(votes.values()) != valid:
        raise ValueError(f"{url}: the party votes sum to {sum(votes.values())}, but the total is {valid}")
    return Summary(url, seats, votes, valid, informal, magnitude)


def _derived_summary(year: int) -> Summary:
    pages_ = bundle_pages(year)["party"]
    labels = next(iter(pages_.values())).labels
    votes = {label: sum(page.total[i] for page in pages_.values()) for i, label in enumerate(labels)}
    valid = sum(page.total[-2] for page in pages_.values())
    informal = sum(page.total[-1] for page in pages_.values())
    if sum(votes.values()) != valid:
        raise ValueError(f"{year}: the party votes sum to {sum(votes.values())}, but the valid total is {valid}")
    art_ = bundle_art(year)
    assert art_ is not None
    return Summary(art_.url, {}, votes, valid, informal, None, derived=True)


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


# The 2023 voting-place pages list parties that no summary prints, such as parties that stood only candidates. The
# registry holds the parties that the summaries list, so the 2023 election makes the others itself, under ids of its own.
LOCAL_YEARS = (1999, 2002, 2005, 2008, 2011, 2014, 2023)


def listed(year: int) -> set[str]:
    """The parties the year's summary prints with votes or seats, case-folded."""
    if bundled(year):
        return set()
    summ = summary(year)
    return {name.casefold() for name, votes in summ.votes.items() if votes or name in summ.seats}


def party_for(year: int, name: str) -> str:
    if year in LOCAL_YEARS and name.casefold() not in listed(year):
        return key("party", year, name.casefold())
    return party_id(name)


@functools.cache
def pages(year: int, vote: str) -> dict[int, Page]:
    if bundled(year):
        return bundle_pages(year)[vote]
    found: dict[int, Page] = {}
    for n in range(1, 80):
        url = page_source(year, vote, n)
        if url:
            found[n] = read_page(url)
    return found


def level(year: int) -> str | None:
    """The finest level the cache reaches for a general election: voting places, electorates, or the nation."""
    if bundled(year):
        same = {n: page.electorate for n, page in pages(year, "candidate").items()}
        if same != electorates(year):
            raise ValueError(f"{year}: the candidate and party files list different electorates")
        return "voting_place"
    if summary_url(year) is None:
        return None
    if pages(year, "candidate") and len(pages(year, "candidate")) == len(electorates(year)):
        return "voting_place"
    if all(has(e9_url(year, name)) for name in ("e9_part1.csv", "e9_part4.csv")):
        return "electorate"
    return "nation"


def days() -> list[str]:
    return [day for day, year in GENERAL.items() if level(year)]


def package_key(day: str) -> str:
    return f"nz.ec/{day}-general" if day == REFERENDUM_DAY else f"nz.ec/{day}"


LISTS_KEY = f"nz.ec/{LISTS_DAY}-lists"
REFERENDUMS_KEY = f"nz.ec/{LISTS_DAY}-referendums"


def has_lists() -> bool:
    return all(has(csv_url(LISTS_YEAR, name)) for name in LISTS_FILES) and LISTS_DAY in days()


def has_referendums() -> bool:
    return has(REFERENDUMS_PAGE) and has(REFERENDUMS_SHEET)


def packages() -> list[str]:
    held = [f"nz.ec/{REFERENDUM_DAY}"] if has(REFERENDUM_URL) else []
    more = [*([LISTS_KEY] if has_lists() else []), *([REFERENDUMS_KEY] if has_referendums() else [])]
    return ["nz.ec/registry", *held, *(package_key(day) for day in days()), *more]


def parse() -> None:
    split_saved()
    registry().write()
    if has(REFERENDUM_URL):
        referendum().write()
    for day in days():
        general(day).write()
    if has_lists():
        lists().write()
    if has_referendums():
        referendums().write()


def emit() -> None:
    package.run("load", packages())


@functools.cache
def party_columns(year: int) -> tuple[list[str], dict[str, list[int]], list[int]]:
    """The party columns of the electorate table, named as the summary names them, each electorate's row (party
    votes, then valid and informal), and the combined totals row."""
    rows = rows_of(e9_url(year, "e9_part4.csv"))
    names = list(summary(year).votes)
    if year == 1999:
        return _party_columns_1999(rows, names)
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


def _party_columns_1999(rows: list[list[str]], names: list[str]) -> tuple[list[str], dict[str, list[int]], list[int]]:
    """The 1999 table of party votes by electorate (part V): the party columns are named in capitals, a blank column
    separates them from the valid total, and another from the informal total. The two totals rows that split the
    electorates into general and Māori are checked, and the Māori electorates' total includes the Tangata Whenua votes,
    so the electorates are reconciled against the combined totals."""
    headers = rows[1]
    last = headers.index("Total Valid Party Votes")
    columns = []
    for header in headers[1:last]:
        if not header:
            continue
        found = [name for name in names if name.casefold().startswith(header.casefold())]
        exact = [name for name in found if name.casefold() == header.casefold()]
        if len(exact or found) != 1:
            raise ValueError(f"1999: the party column {header!r} matches {found} in the summary")
        columns.append((exact or found)[0])
    if len(columns) != len(names):
        raise ValueError(f"1999: the table lists {len(columns)} parties, the summary {len(names)}")
    # The file spells its last header "Inforaml Party Votes"; it is the first one after the valid total.
    informal = next(i for i, header in enumerate(headers) if i > last and header)
    at = [*range(1, 1 + len(columns)), last, informal]
    electorate_rows: dict[str, list[int]] = {}
    totals: dict[str, list[int]] = {}
    for row in rows[2:]:
        row = [*row, *[""] * (informal + 1 - len(row))]
        if not row[0] or not row[1].replace(",", "").isdigit():
            continue
        values = [number(row[i]) for i in at]
        if row[0].startswith(("General Electorate Totals", "Maori Electorate Totals", "Combined Totals")):
            totals[row[0].split(" Totals")[0].split()[0]] = values
        else:
            electorate_rows[row[0]] = values
    for values in electorate_rows.values():
        if sum(values[:-2]) != values[-2]:
            raise ValueError("1999: an electorate's party votes do not sum to its valid total")
    sums = [sum(values[i] for values in electorate_rows.values()) for i in range(len(at))]
    parts = [a + b for a, b in zip(totals["General"], totals["Maori"], strict=True)]
    if sums != totals["Combined"] or parts != totals["Combined"]:
        raise ValueError(f"1999: the electorates sum to {sums}, but the combined totals are {totals['Combined']}")
    return columns, electorate_rows, totals["Combined"]


def _electorate_table_1999(name: str) -> tuple[list[list[str]], dict[str, list[str]]]:
    """The rows of a 1999 part by electorate, keyed by the electorate's name with punctuation folded, so that the
    hyphen of West Coast-Tasman is no mismatch, and the rows of totals set aside."""
    rows = rows_of(e9_url(1999, name))
    found: dict[str, list[str]] = {}
    for row in rows:
        if (
            row
            and row[0]
            and len(row) > 3
            and row[1]
            and not row[0].startswith(("Electoral", "General", "Maori", "Combined", "Total", "Question"))
        ):
            # Part IX goes on to further tables of the same electorates; the first is the one of party votes.
            found.setdefault(slug(row[0]), row)
    return rows, found


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
        if bundled(year):
            continue
        names |= {name for name, votes in summary(year).votes.items() if votes or name in summary(year).seats}
        if level(year) == "voting_place" and year not in LOCAL_YEARS:
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


def _result_set(p: Package, election: str, contest: str, name: str, method: str = METHOD) -> str:
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
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
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

    def get(
        self,
        number: int,
        electorate: str,
        name: str,
        aggregate: bool,
        channel: int = 0,
        area: str = "",
        group: str = "",
    ) -> str:
        """The unit of a place in one channel; a place open in advance and on the day is two units. A place is named
        with its suburb when the file gives one, and coded with the suburb or the group of places it is printed in."""
        where = f"{group or area}:" if group or area else ""
        if channel == EARLY:
            code = f"{self.prefix}:{number}:advance:{where}{name}"
            if not name.startswith(("Hospital Votes", "Ordinary Votes BEFORE")):
                name = f"{name}, advance voting"
        else:
            code = f"{self.prefix}:{number}:{where}{name}"
        if area and area not in name:
            name = f"{name}, {area}"
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
    if not row.section:
        # The older files list no sections: hospital and ordinary votes taken before the day are advance votes.
        if re.match(r"^(Hospital Votes|Ordinary Votes BEFORE)", row.name):
            return EARLY, True
        if re.match(r"^Polling places where less than", row.name):
            return ELECTION_DAY, True
    if SPECIAL.match(row.name) and not row.name.startswith("Voting places where less than"):
        return 0, True
    aggregate = row.name.startswith("Voting places where less than")
    return (EARLY if row.section.startswith("Advance") else ELECTION_DAY), aggregate


def _cite(p: Package, urls: list[str | None]) -> None:
    for url in urls:
        if url:
            p.uses(art(url))


DESCRIPTION = {
    "voting_place": "by voting place, from the Electoral Commission's results pages",
    "polling_place": "by polling place, from the Electoral Commission's results files",
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
        f"The {year} New Zealand general election {DESCRIPTION['polling_place' if bundled(year) else how]}",
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
        # Only the parties that stood a list or won a seat are options; the others stood electorate candidates only.
        labels = [name for name, votes in summ.votes.items() if votes or name in summ.seats]
    options: dict[str, str] = {}
    for position, label in enumerate(labels, 1):
        option = options[label] = key("option", f"party-{day}", label)
        p.add(
            "contest_option",
            id=option,
            contest_id=party_contest,
            kind="list",
            party_id=party_for(year, label),
            ballot_position=position,
            ballot_label=label,
        )
    stray = {label for label, value in summ.votes.items() if label not in options and value}
    if stray:
        raise ValueError(f"{year}: the summary prints party votes for {sorted(stray)}, which no table lists")
    for label, allocated in summ.seats.items():
        p.add("outcome", result_set_id=party_set, option_id=options[label], kind="won", seats=allocated)
    if not summ.derived:
        for label, value in summ.votes.items():
            if label in options:
                p.add("declared", result_set_id=party_set, option_id=options[label], measure=VOTES, value=value)
        p.add("declared", result_set_id=party_set, option_id=None, measure=VALID, value=summ.valid)
        if summ.informal is not None:
            p.add("declared", result_set_id=party_set, option_id=None, measure=INVALID, value=summ.informal)
        p.uses(art(summ.url))
    if how == "voting_place":
        _local_parties(p, year)
        _voting_places(p, day, election, party_set, options)
        if year == 1999:
            p.accept(
                "accounting.votes_valid",
                "The 1999 East Coast party-vote file prints the valid total of its special votes before polling day "
                "(1,905) on that row and none on the polling-day row, which holds the votes themselves; the "
                "electorate's total row and both rows together agree with the votes.",
            )
    elif how == "electorate":
        p.uses(art(e9_url(year, "e9_part4.csv")))
        _, electorate_rows, _ = party_columns(year)
        roll = _roll_1999(electorate_rows) if year == 1999 else {}
        made: dict[str, str] = {}
        for name, values in electorate_rows.items():
            code = f"{year}:{name}"
            unit = made[name] = key("unit", "electorate", code)
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
            _put(p, party_set, unit, 0, None, REGISTERED, roll.get(name))
        if year == 1999:
            _referendums_1999(p, first, made, roll)
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
        for label in labels:
            _put(p, party_set, unit, 0, options[label], VOTES, summ.votes[label])
        _put(p, party_set, unit, 0, None, VALID, summ.valid)
        if summ.informal is not None:
            _put(p, party_set, unit, 0, None, INVALID, summ.informal)
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


def _roll_1999(electorate_rows: dict[str, list[int]]) -> dict[str, int]:
    """The electors on the master roll of each electorate (part IX), after checking that part's party votes against
    the party table's: valid, informal, and the combined totals."""
    rows, found = _electorate_table_1999("partix.csv")
    if set(found) != {slug(name) for name in electorate_rows}:
        raise ValueError("1999: part IX and the party table list different electorates")
    roll: dict[str, int] = {}
    for name, values in electorate_rows.items():
        row = found[slug(name)]
        valid, informal, on_roll = number(row[3]), number(row[6]), number(row[10])
        if [valid, informal] != values[-2:]:
            raise ValueError(f"1999: part IX gives {name} {valid} valid and {informal} informal, not {values[-2:]}")
        roll[name] = on_roll
    combined = next(row for row in rows if row and row[0] == "Combined Totals")
    if [number(combined[3]), number(combined[6]), number(combined[10])] != [
        sum(values[-2] for values in electorate_rows.values()),
        sum(values[-1] for values in electorate_rows.values()),
        sum(roll.values()),
    ]:
        raise ValueError("1999: part IX's combined totals disagree with the electorates")
    return roll


def _referendums_1999(p: Package, first: date, made: dict[str, str], roll: dict[str, int]) -> None:
    """The two citizens-initiated referendums held with the 1999 general election, by electorate, from parts XIII and
    XIV, each reconciled against its printed total of all votes cast."""
    election = _election(
        p,
        f"referendums-{first}",
        first,
        "referendum",
        "1999 New Zealand citizens-initiated referendums",
        f"nz/{first}-referendums",
    )
    for name, letter, path, english in REFERENDUMS_1999:
        p.uses(art(e9_url(1999, name)))
        rows, found = _electorate_table_1999(name)
        question = next(row[1] for row in rows if row and row[0] == "Question:")
        contest = _contest(
            p,
            f"referendum-{first}-{letter}",
            election,
            "ballot_question",
            f"1999 New Zealand referendum: {english}",
            f"nz/{first}-referendums/{path}",
            electoral_system_id=SYSTEM_REFERENDUM,
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        p.add(
            "note",
            id=key("note", contest, "question"),
            entity_id=contest,
            kind="context",
            body_md=f"The referendum asked the question: \u201c{question}\u201d Voters answered Yes or No.",
            lang="en",
        )
        result_set = _result_set(p, election, contest, f"referendum-{first}-{letter}", REFERENDUM_1999_METHOD)
        options = []
        for position, label in enumerate(("Yes", "No"), 1):
            option = key("option", f"referendum-{first}-{letter}", label)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        if set(found) != {slug(electorate) for electorate in made}:
            raise ValueError(f"{name}: the electorates differ from those of the party table")
        sums = [0] * 5
        for electorate, unit in made.items():
            row = found[slug(electorate)]
            yes, no, valid, informal, cast, on_roll = (number(row[i]) for i in (1, 3, 5, 7, 9, 11))
            if yes + no != valid or valid + informal != cast or on_roll != roll[electorate]:
                raise ValueError(f"{name}: {electorate} does not add up")
            _put(p, result_set, unit, 0, options[0], VOTES, yes)
            _put(p, result_set, unit, 0, options[1], VOTES, no)
            _put(p, result_set, unit, 0, None, CAST, cast)
            _put(p, result_set, unit, 0, None, VALID, valid)
            _put(p, result_set, unit, 0, None, INVALID, informal)
            sums = [a + b for a, b in zip(sums, (yes, no, valid, informal, cast), strict=True)]
        total = next(row for row in rows if row and row[0] == "Total Votes Cast")
        printed = [number(total[i]) for i in (1, 3, 5, 7, 9)]
        if sums != printed:
            raise ValueError(f"{name}: the electorates sum to {sums}, but the printed total is {printed}")
        for option, value in zip(options, printed[:2], strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=printed[2])
        p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=printed[3])
        p.add("declared", result_set_id=result_set, option_id=None, measure=CAST, value=printed[4])


REFERENDUM_1999_METHOD = (
    "Counted by electorate and published by the Electoral Commission. The count of each of the six Māori "
    "electorates includes its share of the Tangata Whenua votes, as the Commission's total for them does."
)


def _local_parties(p: Package, year: int) -> None:
    """The parties of a year's pages that its registry lacks, once each under the spelling that sorts first."""
    if year not in LOCAL_YEARS:
        return
    names: set[str] = set()
    for vote in ("candidate", "party"):
        for page in pages(year, vote).values():
            names |= {*page.parties.values(), *(label for label in page.labels if vote == "party")}
    made: dict[str, str] = {}
    for name in sorted(names - {"Independent"}):
        if name.casefold() not in listed(year):
            made.setdefault(party_for(year, name), name)
    for id, name in made.items():
        p.add("party", id=id, kind="party", polity_id=NEW_ZEALAND)
        p.name(id, name, "en", "official")


def _voting_places(p: Package, day: str, election: str, party_set: str, options: dict[str, str]) -> None:
    year = GENERAL[day]
    candidate_pages = pages(year, "candidate")
    party_pages = pages(year, "party")
    names = electorates(year)
    whole = bundle_art(year) if bundled(year) else None
    won = winners(year) if not whole and has(csv_url(year, "winning-electorate-candidates.csv")) else {}
    if whole:
        p.uses(whole)
    else:
        _cite(
            p,
            [
                *(page_source(year, "candidate", n) for n in candidate_pages),
                *(page_source(year, "party", n) for n in party_pages),
                *([index_url(year)] if has(index_url(year)) else []),
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
            unit = units.get(number, page.electorate, row.name, aggregate, channel, row.area, row.group)
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
    cancelled: list[str] = []
    for number, page in sorted(candidate_pages.items()):
        name = page.electorate
        if page.cancelled:
            # The poll was cancelled on the day and held later, so the page prints no votes and no winner.
            if any(row.values[-2] for row in page.rows) or page.winner:
                raise ValueError(f"{year} {name}: a cancelled poll prints votes")
            cancelled.append(name)
            continue
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
                party_id=None if independent else party_for(year, party),
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
                member_of_party_id=None if independent else party_for(year, party),
                is_independent=independent,
            )
            if page.winner == label:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        for row in page.rows:
            channel, aggregate = _channel(row)
            unit = units.get(number, name, row.name, aggregate, channel, row.area, row.group)
            for label, value in zip(page.labels, row.values, strict=False):
                _put(p, result_set, unit, channel, choice[label], VOTES, value)
            _put(p, result_set, unit, channel, None, VALID, row.values[-2])
            _put(p, result_set, unit, channel, None, INVALID, row.values[-1])
        for label, value in zip(page.labels, page.total, strict=False):
            p.add("declared", result_set_id=result_set, option_id=choice[label], measure=VOTES, value=value)
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=page.total[-2])
        p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=page.total[-1])
    if cancelled:
        p.add(
            "note",
            id=key("note", party_set, "cancelled"),
            entity_id=party_set,
            kind="caveat",
            body_md=f"The electorate poll of {', '.join(cancelled)} was cancelled on election day and held later, so "
            "the results pages print no electorate votes for it and this election holds no contest for it. Its "
            "party votes were counted on election day.",
            lang="en",
        )


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


LIST_SECTIONS = {"successful": LISTS_FILES[0], "unsuccessful": LISTS_FILES[1]}


@dataclass
class Listed:
    party: str
    position: int
    printed: str
    seat: str


def party_lists() -> dict[str, list[Listed]]:
    """Each party's list in order, as the two list files print them. A party's name stands alone on a line above its
    list; a successful party's list gives, for each name that won a seat, the electorate it won or the number of the
    list seat allocated."""
    found: dict[str, list[Listed]] = {}
    for name in LISTS_FILES:
        party = ""
        for row in rows_of(csv_url(LISTS_YEAR, name))[1:]:
            if not any(row):
                continue
            if len(row) == 1:
                party = row[0]
                if party in found:
                    raise ValueError(f"{name}: {party} is listed twice")
                found[party] = []
            elif row[0].isdigit() and party:
                position = int(row[0])
                if position != len(found[party]) + 1:
                    raise ValueError(f"{name}: {party} list is out of order at {position}")
                found[party].append(Listed(party, position, clean(row[1]), clean(row[2]) if len(row) > 2 else ""))
            else:
                raise ValueError(f"{name}: unexpected row {row}")
    return found


def lists() -> Package:
    """The party lists of the 2020 general election, as list candidacies on the party vote's options, with the members
    each party's list returned. The party vote and its options are the general election's own package."""
    general_key = package_key(LISTS_DAY)
    p = Package(
        LISTS_KEY,
        SOURCE,
        "The 2020 New Zealand general election's party lists and the members they returned, from the Electoral "
        "Commission's statistics",
        depends=["nz.ec/registry", general_key],
    )
    p.external.add(NEW_ZEALAND)
    for name in LISTS_FILES:
        p.uses(art(csv_url(LISTS_YEAR, name)))
    summ = summary(LISTS_YEAR)
    p.uses(art(summ.url))
    p.uses(art(csv_url(LISTS_YEAR, "winning-electorate-candidates.csv")))
    labels = next(iter(pages(LISTS_YEAR, "party").values())).labels
    party_set = key("result_set", f"party-{LISTS_DAY}")
    held = party_lists()
    stray = sorted(set(held) - set(labels))
    if stray:
        raise ValueError(f"the lists name parties that stood no list in the party vote: {stray}")
    # The registry is built from the summaries the cache holds; a list's party must be among them.
    registered = {row["id"] for row in registry().rows["party"]}
    unregistered = sorted(label for label in held if party_for(LISTS_YEAR, label) not in registered)
    if unregistered:
        raise ValueError(f"the registry built from this cache lacks the listed parties {unregistered}")
    printed_names = 0
    allocated: list[int] = []
    for label, members in held.items():
        option = key("option", f"party-{LISTS_DAY}", label)
        won = [m for m in members if m.seat]
        for m in won:
            if not re.fullmatch(r"Seat Allocation \d+|.+ Electorate", m.seat):
                raise ValueError(f"{label}: {m.printed} is marked {m.seat!r}")
        if len(won) > summ.seats.get(label, 0):
            raise ValueError(
                f"{label}: the list marks {len(won)} seats won, but the summary gives {summ.seats.get(label, 0)}"
            )
        printed_names += len(members)
        for m in members:
            tag = f"{LISTS_DAY}:{label}:{m.position}"
            person = p.id("person", tag)
            p.add("person", id=person)
            p.name(person, person_name(m.printed), "en", "official")
            candidacy = p.id("candidacy", tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=m.position,
                ballot_name=m.printed,
                member_of_party_id=party_for(LISTS_YEAR, label),
                is_independent=False,
            )
            if m.seat.startswith("Seat Allocation "):
                allocated.append(int(m.seat.rsplit(" ", 1)[1]))
                text = (
                    f"Took a list seat, number {m.seat.rsplit(' ', 1)[1]} in the allocation of the House's 120 seats."
                )
            elif m.seat:
                text = f"Won the {m.seat.removesuffix(' Electorate')} electorate seat."
            else:
                continue
            p.add("note", id=p.id("note", candidacy), entity_id=candidacy, kind="context", body_md=text, lang="en")
    if printed_names != 548:
        raise ValueError(f"the lists hold {printed_names} names, but the summary prints 548")
    if len(allocated) != len(set(allocated)) or len(allocated) != (summ.magnitude or 0) - len(winners(LISTS_YEAR)):
        raise ValueError(f"the lists mark {len(allocated)} list seats; the House has {summ.magnitude} seats")
    p.add(
        "note",
        id=key("note", party_set, "lists"),
        entity_id=party_set,
        kind="context",
        body_md="Each registered party's list is given in the order the party ranked it. A name marked with an "
        "electorate won that electorate's seat, and a name marked with a seat allocation number took a list seat; "
        "the number is the place of that seat in the order the Sainte-Laguë method allocated the House's 120 seats. "
        "The lists give only the members who were also on a list; an electorate winner a party did not list is "
        "not named here.",
        lang="en",
    )
    return p


QUESTIONS = (
    (
        "eol",
        "End of Life Choice",
        "Do you support the End of Life Choice Act 2019 coming into force?",
        (2, 3, 4),
        "End of Life Choice Referendum",
    ),
    (
        "cannabis",
        "Cannabis Legalisation and Control Bill",
        "Do you support the proposed Cannabis Legalisation and Control Bill?",
        (5, 6, 7),
        "Cannabis Referendum",
    ),
)
REFERENDUM_METHOD = (
    "Counted by electorate and published by the Electoral Commission. Votes taken at voting places are reported for "
    "the general electorate they were taken in, and special declaration votes for the electorate of the voter. "
    "Votes cast overseas are included in the special votes."
)


def _sheet(name: str) -> list[list[str]]:
    import fastexcel

    book = fastexcel.read_excel(str(art(REFERENDUMS_SHEET).path))
    frame = book.load_sheet_by_name(name, header_row=None).to_polars()
    return [[clean(str(cell)) if cell is not None else "" for cell in row] for row in frame.iter_rows()]


def _referendum_rows(name: str, total_label: str) -> tuple[list[tuple[str, str, list[int]]], list[int]]:
    """The electorate rows of one sheet as (numbers, name, [yes, no, informal for each question]), and the sheet's
    printed totals row."""
    rows: list[tuple[str, str, list[int]]] = []
    total: list[int] | None = None
    for row in _sheet(name):
        if len(row) != 9:
            raise ValueError(f"{name}: unexpected width {len(row)}")
        if row[1].startswith(total_label):
            total = [int(cell) for cell in row[2:8]]
            if int(row[8]) != total[0] + total[1] + total[2]:
                raise ValueError(f"{name}: the total row's own total is wrong")
        elif re.fullmatch(r"\d+(/\d+)*", row[0]) and row[2].isdigit():
            values = [int(cell) for cell in row[2:8]]
            if int(row[8]) != sum(values[:3]) or int(row[8]) != sum(values[3:]):
                raise ValueError(f"{name}: {row[1]} does not add up to its total")
            rows.append((row[0], row[1], values))
    if total is None:
        raise ValueError(f"{name}: no total row")
    sums = [sum(values[i] for _, _, values in rows) for i in range(6)]
    if sums != total:
        raise ValueError(f"{name}: the electorates sum to {sums}, but the total row prints {total}")
    return rows, total


def _referendum_page() -> list[list[int]]:
    """The national count of each question, in the order the results page gives them: yes, no, informal, and all
    votes."""
    cells = Cells()
    cells.feed(text(REFERENDUMS_PAGE))
    found: list[list[int]] = []
    for row in cells.rows:
        row = [clean(cell) for cell in row]
        if row[:1] == ["VOTES"]:
            found.append([])
        elif found and len(row) == 3 and row[0] in ("Yes", "No", "Informal votes*", "TOTAL VOTES"):
            found[-1].append(number(row[1]))
    if len(found) != len(QUESTIONS) or any(len(counts) != 4 for counts in found):
        raise ValueError(f"{REFERENDUMS_PAGE}: expected {len(QUESTIONS)} tables of four rows, found {found}")
    return found


def referendums() -> Package:
    first = date.fromisoformat(LISTS_DAY)
    p = Package(
        REFERENDUMS_KEY,
        SOURCE,
        "The two 2020 New Zealand referendums, on end of life choice and on cannabis, by electorate from the Electoral "
        "Commission's results",
        depends=["nz.ec/registry"],
    )
    p.external.add(NEW_ZEALAND)
    p.uses(art(REFERENDUMS_PAGE))
    p.uses(art(REFERENDUMS_SHEET))
    national = _referendum_page()
    election = _election(
        p,
        f"referendums-{LISTS_DAY}",
        first,
        "referendum",
        "2020 New Zealand referendums",
        f"nz/{LISTS_DAY}-referendums",
    )
    ordinary, ordinary_total = _referendum_rows("Ordinary Votes", "Ordinary Votes Referendum")
    special, special_total = _referendum_rows("Special Votes", "Special Votes Referendums")
    units: list[tuple[str, str, str, str, list[int]]] = []
    for numbers, name, values in ordinary:
        units.append((f"ordinary:{numbers}", f"{name}, ordinary votes", "standard", numbers, values))
    for numbers, name, values in special:
        units.append((f"special:{numbers}", f"{name}, special votes", "aggregate", numbers, values))
    made: dict[str, str] = {}
    for code, name, kind, _, _ in units:
        unit = made[code] = key("unit", f"{LISTS_DAY}-referendums", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NEW_ZEALAND,
            scheme_code=ELECTORATE_SCHEME,
            code=f"{LISTS_DAY}-referendums:{code}",
            area_relation="unlocated",
            area_id=None,
            kind=kind,
        )
        p.name(unit, name, "en", "official")
    for index, (letter, english, question, _, heading) in enumerate(QUESTIONS):
        contest = _contest(
            p,
            f"referendum-{LISTS_DAY}-{letter}",
            election,
            "ballot_question",
            f"2020 New Zealand referendum, {english}",
            f"nz/{LISTS_DAY}-referendums/{letter.replace('eol', 'end-of-life-choice')}",
            electoral_system_id=SYSTEM_REFERENDUM,
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        p.add(
            "note",
            id=key("note", contest, "question"),
            entity_id=contest,
            kind="context",
            body_md=f"The referendum asked the question: \u201c{question}\u201d Voters answered Yes or No.",
            lang="en",
        )
        result_set = _result_set(p, election, contest, f"referendum-{LISTS_DAY}-{letter}", REFERENDUM_METHOD)
        options = []
        for position, label in enumerate(("Yes", "No"), 1):
            option = key("option", f"referendum-{LISTS_DAY}-{letter}", label)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        for code, _, _, _, values in units:
            yes, no, informal = values[3 * index : 3 * index + 3]
            unit = made[code]
            _put(p, result_set, unit, 0, options[0], VOTES, yes)
            _put(p, result_set, unit, 0, options[1], VOTES, no)
            _put(p, result_set, unit, 0, None, CAST, yes + no + informal)
            _put(p, result_set, unit, 0, None, VALID, yes + no)
            _put(p, result_set, unit, 0, None, INVALID, informal)
        yes, no, informal, everything = national[index]
        got = [ordinary_total[3 * index + i] + special_total[3 * index + i] for i in range(3)]
        if got != [yes, no, informal] or yes + no + informal != everything:
            raise ValueError(f"{heading}: the sheets total {got}, but the results page prints {national[index]}")
        for option, value in zip(options, (yes, no), strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
        p.add("declared", result_set_id=result_set, option_id=None, measure=CAST, value=everything)
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=yes + no)
        p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=informal)
    return p
