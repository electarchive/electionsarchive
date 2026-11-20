"""Nunavut's territorial general elections and by-elections, from Elections Nunavut.

Nunavut's Legislative Assembly is elected without parties: each electoral district elects one member by plurality,
and the members choose the premier and cabinet among themselves. The authority publishes one results PDF for each
election, with a table for each district by polling station (or, from 2021, by poll), and pages for the latest
elections. The 1999 general election is the territory's first.
"""

import functools
import html
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Final
from uuid import uuid5

import pymupdf

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.licenses import PUBLIC_RECORD

SOURCE: Final = "ca.nu"
SITE: Final = "https://www.elections.nu.ca"
DOCS: Final = SITE + "/sites/default/files/documents/"
INDEX: Final = SITE + "/en/documents/37"
PAGES: Final = (
    SITE + "/en/elections/results_all/2498",
    SITE + "/en/elections/results/2823/data/823",
    SITE + "/en/elections/results/2906/data/831",
)
PDFS: Final = (
    "general-election-16-en.pdf",
    "quttiktuq-17-en.pdf",
    "nanulik-18-en.pdf",
    "general-election-19-en.pdf",
    "tununiq-20-en.pdf",
    "akulliq-47-en.pdf",
    "south-baffin-68-en.pdf",
    "general-election-49-en.pdf",
    "akulliq-71-en.pdf",
    "nattilik-73-en.pdf",
    "iqaluit-west-39-en_0.pdf",
    "pangnirtung-41-en.pdf",
    "tunnuniq-43-en.pdf",
    "general-election-45-en.pdf",
    "rankin-inlet-south-81-en.pdf",
    "Uqqummiut%20By%20Election%20Results.pdf",
    "netsilik-128-en.pdf",
    "general-election-134-en_1.pdf",
    "tununiq-192-en.pdf",
    "2021%20GE%20Results_0.pdf",
)


def fetch() -> None:
    raw.fetch(SOURCE, INDEX, suffix=".html")
    for url in PAGES:
        raw.fetch(SOURCE, url, suffix=".html")
    for name in PDFS:
        raw.fetch(SOURCE, DOCS + name, suffix=".pdf")


@dataclass
class Row:
    label: str
    channel: int
    votes: list[int | None]
    rejected: int | None
    total: int | None
    electors: int | None = None
    final: int | None = None


@dataclass
class Race:
    district: str
    candidates: list[str]  # "Surname, Given" as printed, or "Given Surname"
    rows: list[Row]
    totals: Row
    elected: int | None = None
    acclaimed: bool = False
    tie: bool = False
    notes: list[str] = field(default_factory=list)


def squash(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKC", text).casefold())


def clean(text: str | None) -> str:
    text = re.sub(r"\xad\s*", "", unicodedata.normalize("NFKC", text or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def number(text: str | None) -> int | None:
    text = clean(text).replace(",", "")
    return int(text) if re.fullmatch(r"\d+", text) else None


@functools.cache
def pdf_path(name: str) -> str:
    return str(raw.cached(SOURCE, DOCS + name).path)


def pdf_text(name: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", pdf_path(name), "-"], check=True, capture_output=True, text=True
    ).stdout


def _kind(label: str) -> int:
    return 2 if "early" in label.casefold() else 1


def pdf_tables(name: str) -> list[tuple[str, list[list[str]]]]:
    """Each table of a results PDF with the text printed above it (a district's title), cell by cell."""
    found: list[tuple[str, list[list[str]]]] = []
    with pymupdf.open(pdf_path(name)) as document:
        for page in document:
            top = 0.0
            for table in sorted(page.find_tables().tables, key=lambda t: t.bbox[1]):
                title = page.get_text(clip=pymupdf.Rect(0, top, page.rect.width, table.bbox[1]))
                top = table.bbox[3]
                rows = [
                    [page.get_text(clip=pymupdf.Rect(cell)) if cell else "" for cell in row.cells] for row in table.rows
                ]
                found.append((title, rows))
    return found


def _candidates(cells: list[str]) -> tuple[list[str], int | None]:
    """The candidates named in a header's cells, and the index of the one marked Elected."""
    text = re.sub(r"Candi-\s*date", "Candidate", clean(" ".join(cells)))
    if "Candidate" in text:
        parts = [part for part in re.split(r"\bCandidate\b", text) if part.strip()]
    else:
        parts = [clean(cell) for cell in cells if clean(cell)]
    names, elected = [], None
    for index, part in enumerate(parts):
        if re.search(r"\bElected\b", part):
            elected = index
        names.append(re.sub(r"\s+,", ",", clean(re.sub(r"\bElected\b", "", part))))
    return names, elected


def _headings(name: str, districts: dict[str, str]) -> list[str]:
    """The districts in the order the PDF prints them."""
    found = []
    lines = [line for line in pdf_text(name).replace("\f", "").split("\n") if line.strip()]
    for index, line in enumerate(lines):
        if re.search(r"\d", line):
            continue
        district = districts.get(squash(line))
        if district is None:
            continue
        for later in lines[index + 1 :]:
            if re.search(r"\d", later):
                break
            if re.search(r"Candidate|Polling|Stations", later):
                found.append(district)
                break
    return found


def candidate_races(name: str, districts: dict[str, str]) -> list[Race]:
    """The tables of 1999 to 2019: a row for each polling station, a column for each candidate. A merged cell moves
    a figure out of its column, so a row is read as the figures it holds in order."""
    tables: list[list[list[str]]] = []
    for _, rows in pdf_tables(name):
        current: list[list[str]] = []
        for cells in rows:
            if clean(cells[0]).startswith("Polling Stations") and current:
                tables.append(current)
                current = []
            current.append(cells)
        tables.append(current)
    order = _headings(name, districts)
    if len(order) != len(tables):
        raise ValueError(f"{name}: {len(tables)} tables for {len(order)} districts {order}")
    races: list[Race] = []
    for district, rows in zip(order, tables, strict=True):
        head = rows[0]
        body = rows[1:]
        if any("Acclaimed" in c for row in body for c in row):
            names, _ = _candidates([c for c in head[1:] if "Voters" not in c and "Candidate" in c or "," in c])
            listed = [n for row in body for c in row[1:] if (n := number(c)) is not None]
            totals = Row("Total", 1, [None], None, None, listed[0], listed[-1])
            label = clean(body[0][0])
            races.append(
                Race(district, names[:1], [Row(label, 1, [None], None, None, listed[0], listed[-1])], totals, 0, True)
            )
            continue
        cut = next((i for i, c in enumerate(head) if re.match(r"Rejected", clean(c))), None)
        if cut is None:
            raise ValueError(f"{name} {district}: no rejected column in {[clean(c) for c in head]}")
        names, elected = _candidates(head[1:cut])
        count = len(names)
        rows_out: list[Row] = []
        totals: Row | None = None
        fragments: list[str] = []
        for cells in body:
            figures = [n for c in cells[1:] if (n := number(c)) is not None]
            label = clean(cells[0])
            if len(figures) < count + 2:
                if label.casefold() in {"a-k", "l-z"} and rows_out:
                    rows_out[-1].label += f" {label}"
                elif label:
                    fragments.append(label)
                continue
            label = clean(" ".join([*fragments, label]))
            fragments = []
            row = Row(
                label,
                _kind(label),
                list(figures[:count]),
                figures[count],
                figures[count + 1],
                figures[count + 2] if len(figures) > count + 2 else None,
                figures[count + 3] if len(figures) > count + 3 else None,
            )
            if label.casefold().startswith("total"):
                totals = row
            else:
                rows_out.append(row)
        if totals is None:
            raise ValueError(f"{name} {district}: no total row")
        previous = ""
        for row in rows_out:
            if row.channel == 2:
                if (
                    "early" not in row.label.casefold().replace("early polls", "", 1)
                    and row.label.casefold() == "early polls"
                ):
                    row.label = f"{previous} Early Polls".strip()
            else:
                previous = re.sub(r"\s*Election Day\s*", " ", row.label).strip() or row.label
        races.append(Race(district, names, rows_out, totals, elected))
    return races


DISTRICTS_1999: Final = (
    "Akulliq", "Amittuq", "Arviat", "Baker Lake", "Cambridge Bay", "Hudson Bay", "Iqaluit Centre", "Iqaluit East",
    "Iqaluit West", "Kugluktuk", "Nanulik", "Nattilik", "Pangnirtung", "Quttiktuq", "Rankin Inlet North",
    "Rankin Inlet South/Whale Cove", "South Baffin", "Tununiq", "Uqqummiut",
)  # fmt: skip
DISTRICTS_2013: Final = (
    "Aggu", "Aivilik", "Amittuq", "Arviat North-Whale Cove", "Arviat South", "Baker Lake", "Cambridge Bay",
    "Gjoa Haven", "Hudson Bay", "Iqaluit-Manirajak", "Iqaluit-Niaqunnguu", "Iqaluit-Sinaa", "Iqaluit-Tasiluk",
    "Kugluktuk", "Netsilik", "Pangnirtung", "Quttiktuq", "Rankin Inlet North-Chesterfield Inlet",
    "Rankin Inlet South", "South Baffin", "Tununiq", "Uqqummiut",
)  # fmt: skip
NAMES: Final = {squash(n): n for n in (*DISTRICTS_1999, *DISTRICTS_2013)} | {squash("Tunnuniq"): "Tununiq"}


def _scan_rows(table: list[list[str]], district: str) -> Race:
    """A table with a row for each candidate and a column for each poll (2021)."""
    head = [clean(c) for c in table[0]]
    polls = head[1:-1]
    names: list[str] = []
    cells: list[list[int]] = []
    rejected: list[int] = []
    totals: list[int] = []
    for row in table[1:]:
        label = clean(row[0])
        figures = [number(c) for c in row[1:]]
        if any(f is None for f in figures):
            raise ValueError(f"{district}: a blank figure in {label}: {figures}")
        values = [int(f) for f in figures if f is not None]
        if label == "Rejected Ballots":
            rejected = values
        elif label == "Total Ballots":
            totals = values
        else:
            names.append(label)
            cells.append(values)
    rows = [
        Row(
            clean(re.sub(r"\s*/\s*", " ", polls[j])),
            _kind(polls[j]),
            [c[j] for c in cells],
            rejected[j],
            totals[j],
        )
        for j in range(len(polls))
    ]
    return Race(district, names, rows, Row("Total", 1, [c[-1] for c in cells], rejected[-1], totals[-1]))


def scan_races(name: str) -> list[Race]:
    """The 2021 results: a table for each contested district, and a line naming the member for each acclaimed one."""
    listed: list[tuple[str, str | None]] = []
    for line in pdf_text(name).replace("\f", "").split("\n"):
        known = (
            NAMES.get(squash(line)) if line.strip() and not re.search(r"\d", line) and line == line.lstrip() else None
        )
        found = re.fullmatch(r"\s+(\S.*?)\s{3,}Acclaimed", line)
        if known:
            listed.append((known, None))
        elif found:
            listed[-1] = (listed[-1][0], clean(found.group(1)))
    tables = [rows for _, rows in pdf_tables(name)]
    contested = [d for d, who in listed if who is None]
    if len(contested) != len(tables):
        raise ValueError(f"{name}: {len(tables)} tables for {contested}")
    by_district = dict(zip(contested, tables, strict=True))
    races = []
    for district, who in listed:
        if who is not None:
            races.append(Race(district, [who], [], Row("Total", 1, [None], None, None), 0, True))
        else:
            races.append(_scan_rows(by_district[district], district))
    return races


def page_tokens(url: str) -> list[str]:
    text = raw.cached(SOURCE, url).path.read_text()
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", text, flags=re.DOTALL)
    text = html.unescape(re.sub(r"<[^>]+>", "|", text))
    return [clean(t) for t in text.split("|") if clean(t)]


def page_races(url: str) -> tuple[list[Race], dict[str, str]]:
    """The district blocks of a results page: the member, the electors, and the poll-by-poll table."""
    tokens = page_tokens(url)
    races: list[Race] = []
    winners: dict[str, str] = {}
    district: str | None = None
    electors: dict[str, int] = {}
    tied = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in NAMES.values() and token in DISTRICTS_2013 and tokens[i + 1 : i + 2] != ["Elected"]:
            district, tied = token, False
            if tokens[i + 1 : i + 2] == ["Acclaimed"]:
                races.append(Race(token, [tokens[i + 2]], [], Row("Total", 1, [None], None, None), 0, True))
                i += 3
                continue
        elif token in DISTRICTS_2013 and tokens[i + 1 : i + 2] == ["Elected"]:
            district, tied = token, False
            winners[token] = tokens[i + 2]
        if token.startswith("Tie vote"):
            tied = True
        if token == "Total voters on list:" and district:
            electors[district] = int(tokens[i + 1].replace(",", ""))
        if token == "Poll by poll results" and district:
            j = i + 1
            polls: list[str] = []
            while tokens[j] != "Total Votes":
                polls.append(tokens[j])
                j += 1
            j += 1
            names: list[str] = []
            grid: list[list[int]] = []
            rejected: list[int] = []
            totals: list[int] = []
            width = len(polls) + 1
            while j < len(tokens):
                label = tokens[j]
                values = [int(v.replace(",", "")) for v in tokens[j + 1 : j + 1 + width] if re.fullmatch(r"[\d,]+", v)]
                if len(values) != width:
                    break
                j += 1 + width
                if label == "Rejected Ballots":
                    rejected = values
                elif label == "Total Votes":
                    totals = values
                    break
                else:
                    names.append(label)
                    grid.append(values)
            rows = [
                Row(
                    polls[k],
                    _kind(polls[k]),
                    [g[k] for g in grid],
                    rejected[k],
                    totals[k],
                )
                for k in range(len(polls))
            ]
            total = Row("Total", 1, [g[-1] for g in grid], rejected[-1], totals[-1], electors.get(district))
            race = Race(district, names, rows, total, tie=tied)
            if district in winners:
                race.elected = next(k for k, n in enumerate(names) if n == winners[district])
            races.append(race)
            district = None
            i = j
            continue
        i += 1
    return races, winners


NUNAVUT: Final = "01a13fb6-eab7-7501-a0f9-490b67698616"
NUNAVUT_AREA: Final = "01a13fb6-eab7-7501-a0f9-48fe85114d27"
NEW_DISTRICTS: Final = date(2013, 10, 28)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


AUTHORITY = key("source", "elections-nunavut")
ASSEMBLY = key("body", "legislative-assembly")
FPTP = key("electoral_system", "fptp")
SET_1999 = key("boundary_set", "ca.nu", "1999")
SET_2013 = key("boundary_set", "ca.nu", "2013")


@dataclass(frozen=True)
class Event:
    day: date
    kind: str  # general or by_election
    files: tuple[str, ...]  # PDF names, or page URLs
    paged: bool = False

    @property
    def slug(self) -> str:
        return f"{'ge' if self.kind == 'general' else 'be'}-{self.day}"

    def urls(self) -> list[str]:
        return list(self.files) if self.paged else [DOCS + name for name in self.files]


EVENTS: Final = (
    Event(date(1999, 2, 15), "general", ("general-election-16-en.pdf",)),
    Event(date(2000, 12, 4), "by_election", ("quttiktuq-17-en.pdf",)),
    Event(date(2003, 9, 2), "by_election", ("nanulik-18-en.pdf",)),
    Event(date(2004, 2, 16), "general", ("general-election-19-en.pdf",)),
    Event(date(2006, 10, 16), "by_election", ("tununiq-20-en.pdf",)),
    Event(date(2008, 10, 27), "general", ("general-election-49-en.pdf",)),
    Event(date(2008, 11, 3), "by_election", ("south-baffin-68-en.pdf",)),
    Event(date(2008, 12, 15), "by_election", ("akulliq-47-en.pdf",)),
    Event(date(2009, 3, 2), "by_election", ("akulliq-71-en.pdf",)),
    Event(date(2010, 4, 26), "by_election", ("nattilik-73-en.pdf",)),
    Event(
        date(2011, 9, 12),
        "by_election",
        ("iqaluit-west-39-en_0.pdf", "pangnirtung-41-en.pdf", "tunnuniq-43-en.pdf"),
    ),
    Event(date(2013, 10, 28), "general", ("general-election-45-en.pdf",)),
    Event(date(2014, 2, 10), "by_election", ("rankin-inlet-south-81-en.pdf",)),
    Event(date(2015, 2, 9), "by_election", ("Uqqummiut%20By%20Election%20Results.pdf",)),
    Event(date(2016, 2, 8), "by_election", ("netsilik-128-en.pdf",)),
    Event(date(2017, 10, 30), "general", ("general-election-134-en_1.pdf",)),
    Event(date(2019, 9, 16), "by_election", ("tununiq-192-en.pdf",)),
    Event(date(2021, 10, 25), "general", ("2021%20GE%20Results_0.pdf",)),
    Event(date(2025, 10, 27), "general", (PAGES[0],), paged=True),
    Event(date(2025, 12, 15), "by_election", (PAGES[1],), paged=True),
)
# Elections the authority's results leave without a declared member: the 2008 Akulliq by-election's PDF marks no
# member (the count was tied after a judicial recount, and a second by-election followed).
NO_MEMBER: Final = {(date(2008, 12, 15), "Akulliq")}
# By-elections the authority's table does not mark Elected although one candidate has the most votes; the member is
# the candidate with the most votes, as the authority's later documents and the register of members confirm.
UNMARKED: Final = {(date(2014, 2, 10), "Rankin Inlet South"), (date(2015, 2, 9), "Uqqummiut")}


@functools.cache
def races(day: date) -> list[Race]:
    event = next(e for e in EVENTS if e.day == day)
    found: list[Race] = []
    for name in event.files:
        if event.paged:
            found += page_races(name)[0]
        elif name.startswith("2021"):
            found += scan_races(name)
        else:
            found += candidate_races(name, NAMES)
    return found


def display(printed: str) -> str:
    """A candidate's name as people say it: the 1999 to 2019 tables print `Surname, Given`."""
    if "," in printed:
        surname, _, given = printed.partition(",")
        return clean(f"{given} {surname}")
    return clean(printed)


def vintage(day: date) -> str:
    return "2013" if day >= NEW_DISTRICTS else "1999"


def area_of(day: date, district: str) -> str:
    return key("area", vintage(day), changeset.slug(district))


def winner(day: date, race: Race) -> int | None:
    """The index of the member the authority declares, or None when it declares none."""
    if race.elected is not None:
        return race.elected
    if race.tie or (day, race.district) in NO_MEMBER:
        return None
    if race.acclaimed:
        return 0
    if not race.rows or race.totals.votes is None:
        return None
    top = max(race.totals.votes, key=lambda v: v or 0)
    leaders = [i for i, v in enumerate(race.totals.votes) if v == top]
    return leaders[0] if len(leaders) == 1 else None


def tied(race: Race) -> list[int]:
    if race.acclaimed:
        return []
    top = max(v or 0 for v in race.totals.votes)
    leaders = [i for i, v in enumerate(race.totals.votes) if v == top]
    return leaders if len(leaders) > 1 else []


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.slug}" for e in EVENTS)]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Nunavut's electoral districts: the 19 of 1999 to 2008 and the 22 since the 2013 election, by name",
    )
    p.external |= {NUNAVUT, NUNAVUT_AREA}
    p.add("scheme", code="ca.nu", polity_id=NUNAVUT, kind="electoral_district", rank=1, noun="electoral district")
    p.add("scheme", code="ca.nu_poll", polity_id=NUNAVUT, kind="precinct", rank=2, noun="polling station")
    p.add(
        "boundary_set",
        id=SET_1999,
        scheme_code="ca.nu",
        vintage="1999",
        valid_from=None,
        valid_to=NEW_DISTRICTS,
        as_of=date(1999, 4, 1),
    )
    p.add(
        "boundary_set",
        id=SET_2013,
        scheme_code="ca.nu",
        vintage="2013",
        valid_from=NEW_DISTRICTS,
        valid_to=None,
        as_of=NEW_DISTRICTS,
    )
    for names, since, until in ((DISTRICTS_1999, None, NEW_DISTRICTS), (DISTRICTS_2013, NEW_DISTRICTS, None)):
        for name in names:
            area = key("area", "2013" if since else "1999", changeset.slug(name))
            p.add("area", id=area, scheme_code="ca.nu")
            p.add("area_parent", area_id=area, parent_id=NUNAVUT_AREA, valid_from=since, valid_to=until)
            p.name(area, name, "en", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Legislative Assembly of Nunavut and its single-member plurality system, and Elections Nunavut as the "
        "source of its results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NUNAVUT}
    p.add(
        "source",
        id=AUTHORITY,
        kind="official_body",
        publisher="Elections Nunavut",
        title="Elections Nunavut: territorial election results",
        url=INDEX,
        license=PUBLIC_RECORD,
        license_url=None,
        attribution="Elections Nunavut",
    )
    for event in EVENTS:
        for url in event.urls():
            p.artifact(raw.cached(SOURCE, url), AUTHORITY)
    p.add("body", id=ASSEMBLY, polity_id=NUNAVUT, kind="unicameral")
    p.name(ASSEMBLY, "Legislative Assembly of Nunavut", "en", "official")
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=NUNAVUT,
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
        body_md="One member is elected for each electoral district, by the most votes. Candidates stand without "
        "parties, and the members form a consensus government.",
        lang="en",
    )
    return p


METHOD: Final = (
    "Elections Nunavut prints, for each polling station (from 2021, each poll), the votes of each candidate and the "
    "rejected ballots, and from 1999 to 2019 the electors on the official voters list. Early polls are stored as "
    "their own units on the early channel. The authority's printed total is stored as ballots cast when it equals "
    "the candidates' votes plus the rejected ballots, and as valid votes when it equals the candidates' votes alone; "
    "some tables print one and some the other. Candidates stand without parties."
)


def _slug(label: str) -> str:
    return changeset.slug(label) or "unit"


def _build(p: Package, event: Event) -> None:
    day = event.day
    election = key("election", day)
    path = f"ca/nu/{day}-{'general' if event.kind == 'general' else 'by-election'}"
    p.add(
        "election",
        id=election,
        polity_id=NUNAVUT,
        kind=event.kind,
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    if event.kind == "general":
        p.name(election, f"{day.year} Nunavut general election", "en", "official")
    else:
        found = races(day)
        place = " and ".join(r.district for r in found)
        p.name(election, f"{place} by-election, {day:%-d %B %Y}", "en", "official")
    boundary = SET_2013 if day >= NEW_DISTRICTS else SET_1999
    seen: set[str] = set()
    for race in races(day):
        area = area_of(day, race.district)
        contest = key("contest", day, race.district)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=ASSEMBLY,
            district_area_id=area,
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="tacit" if race.acclaimed else "counted",
            boundary_set_id=boundary,
        )
        p.name(contest, race.district, "en", "official")
        p.add("slug", path=f"{path}/{changeset.slug(race.district)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, race.district)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="tacit" if race.acclaimed else "counted",
            source_id=AUTHORITY,
            status="active",
            is_default=True,
            completeness=None if race.acclaimed else 1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        member = winner(day, race)
        leaders = tied(race) if member is None else []
        options = []
        for index, printed in enumerate(race.candidates):
            tag = f"{day}:{race.district}:{index + 1}"
            option = key("option", tag)
            person = key("person", tag)
            options.append(option)
            p.add("person", id=person)
            p.name(person, display(printed), "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
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
            )
            if index == member:
                kind = "elected"
            elif index in leaders:
                kind = "tied"
            else:
                kind = "lost"
            if member is not None or leaders or race.rows:
                p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
            if not race.acclaimed and race.totals.votes[index] is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=race.totals.votes[index])
        notes = []
        if member is not None and race.elected is None and not race.acclaimed:
            notes.append("The authority's table marks no member; the member is the candidate with the most votes.")
        if member is None and not race.acclaimed:
            if race.tie:
                notes.append("The candidates with the most votes tied, and a new election followed.")
            elif (day, race.district) in NO_MEMBER:
                notes.append(
                    "The authority's table marks no member. The count was set aside after a judicial recount "
                    "and a by-election was held again."
                )
            else:
                notes.append("The authority's table marks no member, and the candidates with the most votes tied.")
        if race.acclaimed:
            notes.append("The candidate was declared elected without a vote.")
        for number_, text in enumerate(notes):
            p.add(
                "note",
                id=key("note", result_set, "result", number_),
                entity_id=result_set,
                kind="caveat",
                body_md=text,
                lang="en",
            )
        _units(p, day, race, area, result_set, options, seen)


def _units(p: Package, day: date, race: Race, area: str, result_set: str, options: list[str], seen: set[str]) -> None:
    rows = list(race.rows)
    for row in rows:
        base = f"{day}:{race.district}:{_slug(row.label)}"
        code, n = base, 1
        while code in seen:
            n += 1
            code = f"{base}-{n}"
        seen.add(code)
        unit = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NUNAVUT,
            scheme_code="ca.nu_poll",
            code=code,
            area_relation="within",
            area_id=area,
            kind="standard",
        )
        channel = row.channel

        def put(
            measure: int, value: int | None, option: str | None = None, *, unit: str = unit, channel: int = channel
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

        votes = [v for v in row.votes]
        for option, count in zip(options, votes, strict=True):
            put(1, count, option)
        valid = sum(v or 0 for v in votes)
        rejected = row.rejected or 0
        put(5, row.rejected)
        if row.total == valid + rejected:
            put(3, row.total)
        elif row.total == valid:
            put(4, row.total)
        put(2, row.electors)
    if (
        race.acclaimed
        and race.totals.electors is not None
        or not race.acclaimed
        and race.totals.electors is not None
        and not race.rows[0].electors
    ):
        code = f"{day}:{race.district}:list"
        unit = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NUNAVUT,
            scheme_code="ca.nu_poll",
            code=code,
            area_relation="equals",
            area_id=area,
            kind="aggregate",
        )
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=None,
            channel=0,
            measure=2,
            count_round=0,
            flags=0,
            value=race.totals.electors,
        )


def _accept(p: Package, day: date) -> None:
    """Accept the findings the authority's own printed figures cause, with the count of each."""
    over = off = 0
    for race in races(day):
        if race.acclaimed:
            continue
        over += sum(1 for r in race.rows if r.electors is not None and (r.total or 0) > r.electors)
        for index in range(len(race.candidates)):
            if sum(r.votes[index] or 0 for r in race.rows) != race.totals.votes[index]:
                off += 1
    if over:
        p.accept(
            "accounting.turnout",
            f"Elections Nunavut prints more ballots cast than electors on the official voters list at {over} "
            "polling stations; stored as printed",
        )
    if off:
        p.accept(
            "accounting.declared",
            f"The total Elections Nunavut prints for {off} candidates differs from the sum of the polling station "
            "rows it prints above it; the printed total is stored as declared and the rows as printed",
        )


def election(event: Event) -> Package:
    what = "general election" if event.kind == "general" else "by-election"
    p = Package(
        f"{SOURCE}/{event.slug}",
        SOURCE,
        f"The {event.day} Nunavut {what} by district and polling station, from Elections Nunavut",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NUNAVUT, NUNAVUT_AREA}
    for url in event.urls():
        p.uses(raw.cached(SOURCE, url))
    _build(p, event)
    _accept(p, event.day)
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for event in EVENTS:
        election(event).write()


def emit() -> None:
    package.run("load", packages())
