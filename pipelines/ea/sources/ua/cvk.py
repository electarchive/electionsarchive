import functools
import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin
from uuid import uuid5

import httpx

from ea import package, raw
from ea.package import Package

SOURCE = "ua.cvk"
BASE = "https://www.cvk.gov.ua/pls"
DELAY = 0.4
WORKERS = 4
ABROAD = 226  # the abroad district's number in the site's pages; it votes only in the national list
DISTRICTS = range(1, ABROAD + 1)


@dataclass(frozen=True)
class Parliament:
    site: str  # the results site's folder
    id: int  # the authority's election number within the site
    day: date
    kind: str  # general, by_election


@dataclass(frozen=True)
class President:
    site: str
    id: int
    day: date
    round: int
    layout: str = "modern"  # modern (vp2014, vp2019), stations (vp2010, the vp2004 repeat), districts (vp2004), protocols (vp1999)
    after: date | None = None  # the round this one follows
    annulled: bool = False
    plan: str = ""  # the election whose territorial districts it uses, on the older sites
    national: str = ""  # the page of national totals
    index: str = ""  # the page that lists the precinct, district, or region pages
    abroad: str = ""  # the page that lists the polling stations abroad


PARLIAMENT = [
    Parliament("vnd2012", 900, date(2012, 10, 28), "general"),
    Parliament("vnd2012", 902, date(2013, 7, 7), "by_election"),
    Parliament("vnd2012", 903, date(2013, 12, 15), "by_election"),
    Parliament("vnd2012", 904, date(2014, 5, 25), "by_election"),
    Parliament("vnd2014", 910, date(2014, 10, 26), "general"),
    Parliament("vnd2014", 913, date(2015, 7, 26), "by_election"),
    Parliament("vnd2014", 914, date(2016, 7, 17), "by_election"),
    Parliament("vnd2019", 919, date(2019, 7, 21), "general"),
    Parliament("vnd2019", 920, date(2020, 3, 15), "by_election"),
    Parliament("vnd2019", 921, date(2020, 10, 25), "by_election"),
    Parliament("vnd2019", 922, date(2021, 3, 28), "by_election"),
    Parliament("vnd2019", 923, date(2021, 10, 31), "by_election"),
    Parliament("vnd2019", 924, date(2022, 3, 27), "by_election"),
]
V99 = f"{BASE}/vp1999"
V04 = f"{BASE}/vp2004"
V10 = f"{BASE}/vp2010"
PRESIDENT = [
    President(
        "vp1999",
        100,
        date(1999, 10, 31),
        1,
        "protocols",
        None,
        False,
        "1999",
        f"{V99}/webvp136ce-29.html?kodvib=100",
        f"{V99}/webvp13f1a.html?kodvib=100&rozr1=1",
        f"{V99}/webvp1139f.html?kodvib=100&kodreg=95&rozr1=1",
    ),
    President(
        "vp1999",
        200,
        date(1999, 11, 14),
        2,
        "protocols",
        date(1999, 10, 31),
        False,
        "1999",
        f"{V99}/webvp12a6d-5.html?kodvib=200",
        f"{V99}/webvp1f7ce-2.html?kodvib=200&rozr1=1",
        f"{V99}/webvp1804f.html?kodvib=200&kodreg=95&rozr1=1",
    ),
    President(
        "vp2004",
        1,
        date(2004, 10, 31),
        1,
        "districts",
        None,
        False,
        "2004",
        f"{V04}/wp300e26d.html",
        f"{V04}/wp301e26d.html",
        f"{V04}/wp312e26d.html",
    ),
    President(
        "vp2004",
        2,
        date(2004, 11, 21),
        2,
        "districts",
        date(2004, 10, 31),
        True,
        "2004",
        f"{V04}/wp300927a.html",
        f"{V04}/wp301927a.html",
        f"{V04}/wp312927a.html",
    ),
    President(
        "vp2004",
        3,
        date(2004, 12, 26),
        2,
        "stations",
        date(2004, 10, 31),
        False,
        "2004",
        f"{V04}/wp3002e3e.html",
        f"{V04}/wp3352e3e.html",
    ),
    President(
        "vp2010",
        700,
        date(2010, 1, 17),
        1,
        "stations",
        None,
        False,
        "2010",
        f"{V10}/wp300f0d8.html?PT001F01=700",
        f"{V10}/wp335f0d8.html?PT001F01=700",
    ),
    President(
        "vp2010",
        701,
        date(2010, 2, 7),
        2,
        "stations",
        date(2010, 1, 17),
        False,
        "2010",
        f"{V10}/wp3005191.html?PT001F01=701",
        f"{V10}/WP3355191.html?PT001F01=701",
    ),
    President("vp2014", 702, date(2014, 5, 25), 1),
    President("vp2019", 719, date(2019, 3, 31), 1),
    President("vp2019", 720, date(2019, 4, 21), 2, after=date(2019, 3, 31)),
]
MODERN = [r for r in PRESIDENT if r.layout == "modern"]


def page(site: str, name: str, id: int, extra: str = "") -> str:
    return f"{BASE}/{site}/{name}pt001f01={id}{extra}.html"


def get(url: str) -> raw.Artifact | None:
    """Fetch one page into the raw cache; a page the site does not serve is None."""
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    time.sleep(DELAY)
    try:
        return raw.fetch(SOURCE, url, suffix=".html")
    except httpx.HTTPStatusError as failure:
        if failure.response.status_code == 404:
            return None
        raise


def text(artifact: raw.Artifact) -> str:
    return artifact.path.read_bytes().decode("windows-1251", errors="replace")


class _Tables(HTMLParser):
    """Collect every table of a page as rows of cell texts; the site's markup leaves cells and rows unclosed."""

    def __init__(self) -> None:
        super().__init__()
        self.done: list[list[list[str]]] = []
        self.open: list[list[list[str]]] = []
        self.inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.open.append([])
            self.inside = False
        elif tag == "tr" and self.open:
            self.open[-1].append([])
            self.inside = False
        elif tag in ("td", "th") and self.open:
            if not self.open[-1]:
                self.open[-1].append([])
            self.open[-1][-1].append("")
            self.inside = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.open:
            self.done.append(self.open.pop())
            self.inside = False
        elif tag in ("td", "th"):
            self.inside = False

    def handle_data(self, data: str) -> None:
        if self.inside and self.open and self.open[-1] and self.open[-1][-1]:
            self.open[-1][-1][-1] += data


def tables(artifact: raw.Artifact) -> list[list[list[str]]]:
    parser = _Tables()
    parser.feed(text(artifact))
    return [[[" ".join(cell.split()) for cell in row] for row in table if row] for table in parser.done]


def districts(election: Parliament) -> list[int]:
    """The single-member districts that voted: the whole range on a general election, else those the results page names."""
    if election.kind == "general":
        return list(DISTRICTS)
    found: set[int] = set()
    for name in ("wp039", "wp030", "wp306_n"):
        artifact = get(page(election.site, name, election.id))
        if artifact is None:
            continue
        body = text(artifact)
        found |= {int(n) for n in re.findall(r"pf7331=(\d+)", body)}
        found |= {int(n) for n in re.findall(r"ОВО\s*№\s*(\d+)", body)}
    return sorted(found)


def cached(url: str) -> raw.Artifact | None:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return None


def _all(urls: list[str]) -> None:
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(get, urls))


def letters(e: Parliament) -> list[int]:
    """The letter pages of the commission's alphabetical list of elected deputies."""
    artifact = cached(page(e.site, "wp610", e.id))
    return [] if artifact is None else sorted({int(n) for n in re.findall(r"lit=(\d+)", text(artifact))})


def fetch() -> None:
    for election in PARLIAMENT:
        _all(
            [
                page(election.site, name, election.id)
                for name in ("wp300", "wp312", "wp039", "wp306_n", "wp611", "wp610")
            ]
        )
        _all([page(election.site, "wp610", election.id, f"lit={lit}current_row=1") for lit in letters(election)])
        if election.kind == "general":
            _all([page(election.site, "wp603", election.id)])
        numbers = districts(election)
        _all([page(election.site, name, election.id, f"pf7331={n}") for n in numbers for name in ("wp336", "wp338")])
        _all([page(election.site, "wp033", election.id, f"pf7331={n}") for n in numbers if n != ABROAD])
        _all([page(election.site, "wp040", election.id, f"pf7331={n}") for n in numbers if n != ABROAD])
        print(f"{SOURCE}: {election.day} fetched", flush=True)
    for president in MODERN:
        _all([page(president.site, name, president.id) for name in ("wp300", "wp301", "wp312", "wp335")])
        index = cached(page(president.site, "wp335", president.id))
        if index is None:
            raise LookupError(f"{president.site} {president.id} has no precinct index")
        numbers = sorted({int(m) for m in re.findall(r"pt005f01=(\d+)", text(index))})
        _all([page(president.site, "wp336", president.id, f"pt005f01={n}") for n in numbers])
        print(f"{SOURCE}: {president.day} fetched", flush=True)
    for r in PRESIDENT:
        if r.layout != "modern":
            fetch_old(r)
            print(f"{SOURCE}: {r.day} fetched", flush=True)


def links(url: str) -> list[str]:
    """Every address a cached page links to, resolved against the page, in order and without repeats."""
    artifact = cached(url)
    if artifact is None:
        return []
    found = (
        urljoin(url, html.unescape(href)) for href in re.findall(r'href\s*=\s*"([^"]+)"', text(artifact), re.IGNORECASE)
    )
    return list(dict.fromkeys(found))


def linked(url: str, marker: str) -> list[str]:
    """The pages a cached page links to whose file name or query holds the marker."""
    return [link for link in links(url) if marker in link.rsplit("/", 1)[-1].casefold()]


def district_pages(r: President) -> list[str]:
    """The pages of one older election that hold its district and polling station figures, found through its indexes."""
    if r.layout == "stations":
        return linked(r.index, "wp336")
    if r.layout == "districts":
        found: list[str] = []
        for region in linked(r.index, "wp305"):
            _all([region])
            found += linked(region, "wp304")
        return list(dict.fromkeys([*found, r.abroad]))
    found = linked(r.index, "kodokr=")
    for lister in (r.abroad,):
        _all([lister])
        found += linked(lister, "koddil=")
    return list(dict.fromkeys(found))


def fetch_old(r: President) -> None:
    _all([r.national, r.index])
    if r.layout == "districts":
        _all([*linked(r.index, "wp305"), r.abroad])
    elif r.layout == "protocols":
        _all([r.abroad])
    _all(district_pages(r))


# ---------------------------------------------------------------------------------------------------------------------
# Parsing


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


UKRAINE = str(uuid5(package.NAMESPACE, "polity\x1fUA"))
SOURCE_ID = key("source", SOURCE)
RADA = key("body", "rada")
PRESIDENCY = key("office", "president")
LIST_SYSTEM = key("electoral_system", "rada-list")
DISTRICT_SYSTEM = key("electoral_system", "rada-district")
PRESIDENT_SYSTEM = key("electoral_system", "president")
SELF_NOMINATED = "самовисування"
LIST_SEATS = 225
FOREVER = date(1991, 8, 24)

# Where each table kind keeps its fixed columns: the first option column and the column of each measure.
LAYOUTS: dict[str, tuple[int, dict[int, int]]] = {
    "full": (15, {2: 3, 14: 7, 3: 12, 5: 13, 4: 14}),
    "short": (3, {2: 1, 4: 2}),
    "president": (11, {2: 2, 3: 9, 5: 10}),
    "stations": (5, {2: 1, 14: 2, 3: 3, 5: 4}),
    "districts": (5, {2: 1, 14: 2, 3: 3, 5: 4}),
}
MEASURES = {2: "registered", 14: "received a ballot", 3: "voted", 5: "invalid", 4: "valid"}

LIST_METHOD = (
    "Counted by the Central Election Commission from the polling station protocols and published per polling "
    "station on its results site. Registered voters are the voters on the roll, voters are those who received a "
    "ballot, ballots cast are the voters who took part, and valid votes are the votes for the party lists. The "
    "protocols also count ballots received, unused, and not counted; they are not stored here. Polling stations "
    "abroad vote only in the national list."
)
DISTRICT_METHOD = (
    "Counted by the Central Election Commission from the polling station protocols and published per polling "
    "station on its results site. Registered voters are the voters on the roll, voters are those who received a "
    "ballot, ballots cast are the voters who took part, and valid votes are the votes for the candidates."
)
PRESIDENT_METHOD = (
    "Counted by the Central Election Commission from the polling station protocols and published per polling "
    "station on its results site. Registered voters are the voters on the roll, ballots cast are the voters who "
    "took part, and invalid ballots are those declared invalid. The valid votes are the votes for the candidates."
)


def clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def norm(value: str) -> str:
    """The key of a party across elections: its name without case, spacing, or quote style."""
    for old, new in (("’", "'"), ("ʼ", "'"), ("`", "'"), ("“", '"'), ("”", '"'), ("«", '"'), ("»", '"'), ("„", '"')):
        value = value.replace(old, new)
    return " ".join(value.casefold().split())


def squash(value: str) -> str:
    return re.sub(r"\W", "", value).casefold()


def split_name(value: str) -> str:
    """The site prints a candidate column header as surname, name, and patronymic run together."""
    return re.sub(r"(?<=[а-яіїєґ'’])(?=[А-ЯІЇЄҐ])", " ", value)


def number(value: str) -> int | None:
    digits = re.sub(r"[\s\xa0]", "", value)
    return int(digits) if digits.isdigit() else None


@dataclass(frozen=True)
class Sheet:
    district: int
    region: str
    kind: str
    header: list[str]
    rows: list[list[str]]

    @property
    def options(self) -> list[tuple[int, str]]:
        first = LAYOUTS[self.kind][0]
        stop = len(self.header) - (1 if self.header[-1].startswith("Дата") else 0)
        return [(column, clean(self.header[column])) for column in range(first, stop)]


@functools.cache
def sheet(site: str, name: str, id: int, extra: str, district: int) -> Sheet | None:
    artifact = cached(page(site, name, id, extra))
    if artifact is None:
        return None
    found = tables(artifact)
    grid = next((t for t in found if t and t[0] and t[0][0] == "№ ВД"), None)
    if grid is None:
        raise ValueError(f"{page(site, name, id, extra)} holds no precinct table")
    header = grid[0]
    kind = "short" if header[1].startswith("1) п.3") else "full" if header[1].startswith("1)") else "president"
    title = next((" ".join(cell for cell in t[0]) for t in found if t and "№" in " ".join(t[0]) and len(t) == 1), "")
    region = re.search(r"№\s*\d+,\s*(.+)$", title)
    return Sheet(
        district=district,
        region=clean(region.group(1)) if region else "",
        kind=kind,
        header=header,
        rows=[row for row in grid[1:] if re.fullmatch(r"\d{6}", row[0]) and len(row) == len(header)],
    )


def list_sheet(e: Parliament, n: int) -> Sheet | None:
    return sheet(e.site, "wp336", e.id, f"pf7331={n}", n)


def district_sheet(e: Parliament, n: int) -> Sheet | None:
    return sheet(e.site, "wp338", e.id, f"pf7331={n}", n)


def held(e: Parliament) -> list[int]:
    """The districts, abroad included, whose pages the cache holds."""
    return [
        n
        for n in DISTRICTS
        if any(found is not None and found.rows for found in (list_sheet(e, n), district_sheet(e, n)))
    ]


MONTHS = {
    "січня": 1,
    "лютого": 2,
    "березня": 3,
    "квітня": 4,
    "травня": 5,
    "червня": 6,
    "липня": 7,
    "серпня": 8,
    "вересня": 9,
    "жовтня": 10,
    "листопада": 11,
    "грудня": 12,
}


@dataclass(frozen=True)
class Candidate:
    name: str
    nominator: str
    born: date | None
    gender: str | None


def roster(e: Parliament, n: int) -> dict[str, Candidate]:
    """Each registered candidate of a district as the commission prints them, keyed by the squashed name."""
    artifact = cached(page(e.site, "wp033", e.id, f"pf7331={n}"))
    found: dict[str, Candidate] = {}
    if artifact is None:
        return found
    for t in tables(artifact):
        if not t or not t[0][0].startswith("Прізвище"):
            continue
        nominated = next(i for i, cell in enumerate(t[0]) if cell.startswith("Висування"))
        for row in t[1:]:
            if len(row) <= nominated:
                continue
            born = re.search(r"народи(вся|лася)\s+(\d+)\s+(\w+)\s+(\d{4})\s+року", row[1])
            month = MONTHS.get(born.group(3)) if born else None
            found[squash(row[0])] = Candidate(
                name=split_name(clean(row[0])),
                nominator=clean(row[nominated]),
                born=date(int(born.group(4)), month, int(born.group(2))) if born and month else None,
                gender=None if born is None else ("female" if born.group(1) == "лася" else "male"),
            )
    return found


def region_key(name: str) -> str:
    return norm(re.sub(r"\s*-\s*\d+$", "", name))


def regions() -> dict[str, str]:
    """Every region the pages name, by its key."""
    found: dict[str, str] = {}
    for e in PARLIAMENT:
        for n in held(e):
            for found_sheet in (list_sheet(e, n), district_sheet(e, n)):
                if found_sheet is not None and found_sheet.region:
                    found.setdefault(region_key(found_sheet.region), re.sub(r"\s*-\s*\d+$", "", found_sheet.region))
    for r in MODERN:
        for name in grouped(r).values():
            found.setdefault(region_key(name), name)
    return found


@functools.cache
def grouped(r: President) -> dict[int, str]:
    """The region of each territorial district of a presidential election, from the precinct index."""
    index = cached(page(r.site, "wp335", r.id))
    found: dict[int, str] = {}
    if index is None:
        return found
    region = ""
    for t in tables(index):
        for row in t:
            if len(row) == 1 and re.search(r"\s-\s*\d+$", row[0]):
                region = re.sub(r"\s*-\s*\d+$", "", row[0])
            elif len(row) >= 4 and row[0].isdigit():
                found[int(row[0])] = region
            elif row[:1] == ["ЗВО"]:
                found[ABROAD] = ""
    return found


AGAINST = "Не підтримали жодного кандидата"
# The measure each fixed column of an older polling station table holds, by the start of its header.
OLD_MEASURES = (("Кількість виборців", 2), ("Отримали бюлетені", 14), ("Взяли участь", 3), ("Недійсних", 5))
# Where the normalized sheets of the older sites keep the code and measures: the candidates follow.
OLD_FIRST = 5


def lines(artifact: raw.Artifact) -> list[str]:
    """The text of a page, one entry per run between tags."""
    body = re.sub(r"(?is)<(script|style).*?</\1>", " ", text(artifact))
    return [line for part in re.split(r"<[^>]*>", body) if (line := clean(html.unescape(part)))]


def after(found: list[str], marker: str, start: int = 0) -> int | None:
    """The first figure that follows the first line holding the marker."""
    for i in range(start, len(found)):
        if marker in found[i]:
            for line in found[i + 1 : i + 4]:
                value = number(line)
                if value is not None:
                    return value
            return None
    return None


@dataclass(frozen=True)
class Protocol:
    title: str
    region: str
    measures: dict[int, int | None]
    votes: dict[str, int]
    against: int | None


def protocol(artifact: raw.Artifact) -> Protocol:
    """A district, polling station, or national page of the first site: one protocol printed as lines of text."""
    found = lines(artifact)
    title = next((line for line in found if line.startswith(("Виборча дільниця", "Територіальний виборчий округ"))), "")
    region = ""
    for i, line in enumerate(found):
        if line.startswith("Територіальний виборчий округ") and i + 1 < len(found):
            hit = re.fullmatch(r"\((.+)\)", found[i + 1])
            region = hit.group(1) if hit else ""
    measures = {
        2: after(found, "Загальна кількість виборців"),
        14: after(found, "отримали виборчі бюлетені"),
        3: after(found, "взяли участь у голосуванні"),
        5: after(found, "визнаних недійсними"),
    }
    votes: dict[str, int] = {}
    against = None
    start = next((i for i, line in enumerate(found) if line.endswith("кандидата:")), None)
    if start is not None:
        i = start + 1
        while i < len(found):
            line = found[i]
            if "не підтримали жодного" in line:
                against = after(found, "не підтримали жодного", i)
                break
            if number(line) is None and not line.endswith("%") and not line.startswith(("Протокол", "function")):
                value = next((n for x in found[i + 1 : i + 4] if (n := number(x)) is not None), None)
                if value is not None:
                    votes[line] = value
            i += 1
    return Protocol(title, region, measures, votes, against)


def pad(code: str, measures: dict[int, int | None], votes: dict[str, int], against: int | None) -> list[str]:
    return [code, *(str(measures.get(m) if measures.get(m) is not None else "") for m in (2, 14, 3, 5))] + [
        *(str(v) for v in votes.values()),
        "" if against is None else str(against),
    ]


@functools.cache
def old_sheets(r: President) -> list[Sheet]:
    """The district or polling station figures of an older election as sheets of one shape: code, the fixed measures, the candidates, and the votes against all."""
    out: list[Sheet] = []
    urls = district_pages(r)
    if r.layout == "protocols":
        parsed = []
        for url in urls:
            artifact = cached(url)
            if artifact is None:
                continue
            district = re.search(r"kodokr=(\d+)", url)
            number_ = int(district.group(1)) if district else ABROAD
            if "koddil=" in url:
                number_ = ABROAD
            code = re.search(r"koddil=(\d+)", url)
            parsed.append((number_, code.group(1) if code else "", protocol(artifact)))
        names = list(dict.fromkeys(name for _, _, found in parsed for name in found.votes))
        for district, code, found in parsed:
            row = pad(code, found.measures, {name: found.votes.get(name, 0) for name in names}, found.against)
            out.append(
                Sheet(
                    district,
                    found.region,
                    "stations" if code else "districts",
                    ["", "", "", "", "", *names, AGAINST],
                    [row],
                )
            )
        return out
    for url in urls:
        artifact = cached(url)
        if artifact is None:
            continue
        found = tables(artifact)
        if r.layout == "stations":
            where = next((n for n, u in station_districts(r).items() if u == url), None)
            grid = next((t for t in found if t and t[0] and t[0][0].startswith("Дільн")), None)
            if grid is None or where is None:
                raise ValueError(f"{url} holds no polling station table")
            roles: list[int | str | None] = []
            for cell in grid[0]:
                head = re.sub(r"^\d+\.\s*", "", cell)
                roles.append(
                    None
                    if cell.startswith(("Дільн", "Дата")) or head.startswith(("Одержано", "Невикор"))
                    else AGAINST
                    if head.startswith("Не підтр")
                    else next((m for start, m in OLD_MEASURES if head.startswith(start)), head)
                )
            names = [role for role in roles if isinstance(role, str) and role != AGAINST]
            rows = []
            for row in grid[1:]:
                if len(row) != len(roles) or not re.fullmatch(r"\d+", row[0]):
                    continue
                cells = dict(zip(roles, row, strict=True))
                rows.append(
                    [
                        row[0],
                        *(cells.get(m, "") for m in (2, 14, 3, 5)),
                        *(cells.get(name, "") for name in names),
                        cells.get(AGAINST, ""),
                    ]
                )
            out.append(Sheet(where, "", "stations", ["", "", "", "", "", *names, AGAINST], rows))
            continue
        region = ""
        district = 0
        voted: int | None = None
        votes: dict[str, int] = {}
        for t in found:
            for row in t:
                if row[:1] == ["Регіон"] and len(row) > 1:
                    region = row[1]
                elif row[0].startswith("Територіальний виборчий округ") and len(row) > 1 and row[1].isdigit():
                    district = int(row[1])
                elif row[0].startswith("Кількість виборців, які взяли участь"):
                    voted = number(row[-1])
                elif len(row) == 4 and number(row[3]) is not None and not row[0].isdigit() and row[0] != "Кандидат":
                    votes[row[0]] = number(row[3]) or 0
        if url == r.abroad:
            district, region = ABROAD, ""
        if district and votes:
            out.append(
                Sheet(
                    district,
                    region,
                    "districts",
                    ["", "", "", "", "", *votes, AGAINST],
                    [pad("", {3: voted}, votes, None)],
                )
            )
    return out


@functools.cache
def station_districts(r: President) -> dict[int, str]:
    """The page of each district's polling stations, from the index rows."""
    found: dict[int, str] = {}
    body = cached(r.index)
    if body is None:
        return found
    for label, href in re.findall(r'<b>(\d+|ЗВО)</b>\s*<td[^>]*><a[^>]*href="([^"]+)"', text(body), re.IGNORECASE):
        found[ABROAD if label == "ЗВО" else int(label)] = urljoin(r.index, html.unescape(href))
    return found


def area(scheme: str, code: object) -> str:
    return key("area", scheme, code)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Ukraine's regions, single-member districts, and polling stations as the Central Election Commission names them",
    )
    p.external.add(UKRAINE)
    for code, rank, kind, noun in (
        ("ua.region", 1, "administrative", "region"),
        ("ua.ovo", 2, "electoral_district", "single-member district"),
        ("ua.dilnytsia", 3, "precinct", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=UKRAINE, kind=kind, rank=rank, noun=noun, code_system=None)
    names = regions()
    for code, text in sorted(names.items()):
        id = area("ua.region", code)
        p.add("area", id=id, scheme_code="ua.region")
        p.add("area_code", area_id=id, code=code)
        p.name(id, text, "uk", "official")
    where: dict[int, str] = {}
    for e in PARLIAMENT:
        for n in held(e):
            for found_sheet in (list_sheet(e, n), district_sheet(e, n)):
                if found_sheet is not None and found_sheet.region and n != ABROAD:
                    where.setdefault(n, region_key(found_sheet.region))
    for r in MODERN:
        for n, name in grouped(r).items():
            if name and n != ABROAD:
                where.setdefault(n, region_key(name))
    for n in sorted(where):
        id = area("ua.ovo", n)
        p.add("area", id=id, scheme_code="ua.ovo")
        p.add("area_code", area_id=id, code=str(n))
        p.add("area_parent", area_id=id, parent_id=area("ua.region", where[n]))
        p.name(id, f"Одномандатний виборчий округ № {n}", "uk", "official")
        p.name(id, f"Single-member district {n}", "en", "official", False)
    return p


def nominators(e: Parliament) -> set[str]:
    return {
        c.nominator
        for n in held(e)
        for c in roster(e, n).values()
        if c.nominator and norm(c.nominator) != SELF_NOMINATED
    }


def list_parties(e: Parliament) -> set[str]:
    found: set[str] = set()
    for n in held(e):
        found_sheet = list_sheet(e, n)
        if found_sheet is not None:
            found |= {name for _, name in found_sheet.options}
    return found


def party_names() -> dict[str, str]:
    """Every party name the pages print, by key; the latest spelling wins."""
    found: dict[str, str] = {}
    for e in PARLIAMENT:
        for name in sorted(list_parties(e) | nominators(e)):
            found[norm(name)] = clean(name)
    return found


def party(name: str) -> str:
    return key("party", norm(name))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Ukrainian parties, the Verkhovna Rada, the presidency, and their electoral systems from the Central "
        "Election Commission",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(UKRAINE)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Центральна виборча комісія",
        title="Results of elections, Central Election Commission of Ukraine",
        url="https://www.cvk.gov.ua/",
        license="Free reuse with the source cited, as the commission's results sites state",
        license_url="https://www.cvk.gov.ua/",
        attribution="Центральна виборча комісія",
    )
    p.add("body", id=RADA, polity_id=UKRAINE, kind="unicameral")
    p.name(RADA, "Verkhovna Rada", "en", "official")
    p.name(RADA, "Верховна Рада України", "uk", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=UKRAINE, kind="head_of_state")
    p.name(PRESIDENCY, "President of Ukraine", "en", "official")
    p.name(PRESIDENCY, "Президент України", "uk", "native", False)
    for id, rules, method in (
        (
            LIST_SYSTEM,
            {
                "formula": "hare",
                "threshold": {"level": "list", "scope": "national", "percent": 5},
                "lists": {"order": "closed", "vacancy": "next_on_list"},
            },
            (
                "225 seats are allocated by largest remainder with the Hare quota among the national closed lists "
                "that reach 5% of the valid votes."
            ),
        ),
        (
            DISTRICT_SYSTEM,
            {"formula": "fptp"},
            "225 deputies are elected in single-member districts, each by a plurality of the votes.",
        ),
        (
            PRESIDENT_SYSTEM,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A candidate with more than half of the votes of the voters who took part is elected; otherwise the "
                "two leading candidates go to a second round."
            ),
        ),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=UKRAINE,
            valid_from=date(2012, 1, 1),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for code, text in sorted(party_names().items()):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=UKRAINE)
        p.add("external_id", entity_id=id, scheme="ua.cvk.party", value=code)
        p.name(id, text, "uk", "official")
    return p


def declared_list(e: Parliament, name: str = "wp300") -> dict[str, int]:
    """The list votes of each party as a results page prints them: nationwide (wp300) or abroad (wp312)."""
    artifact = cached(page(e.site, name, e.id))
    found: dict[str, int] = {}
    if artifact is not None:
        for t in tables(artifact):
            if t and t[0][:2] == ["№", "Партія"]:
                for row in t[1:]:
                    votes = number(row[-1])
                    if votes is not None:
                        found[norm(row[1])] = votes
    return found


def seats(e: Parliament) -> dict[str, int]:
    """The mandates each list won, as the page of elected deputies prints them."""
    artifact = cached(page(e.site, "wp611", e.id))
    found: dict[str, int] = {}
    if artifact is not None:
        for t in tables(artifact):
            if t and t[0][:1] == ["Партія"]:
                for row in t[1:]:
                    won = number(row[1]) if len(row) > 1 else None
                    if won is not None:
                        found[norm(row[0])] = won
    return found


def declared_district(e: Parliament, n: int) -> dict[str, int]:
    """Each candidate's votes in a district as its summary page prints them, by squashed name."""
    artifact = cached(page(e.site, "wp040", e.id, f"pf7331={n}"))
    found: dict[str, int] = {}
    if artifact is not None:
        for t in tables(artifact):
            if t and t[0][:1] == ["Кандидат"]:
                for row in t[1:]:
                    votes = number(row[-1])
                    if votes is not None:
                        found[squash(row[0].split(",", 1)[0])] = votes
    return found


def general(e: Parliament) -> Parliament:
    """The general election whose pages list a site's deputies: the lists run across the site's by-elections."""
    return next(x for x in PARLIAMENT if x.site == e.site and x.kind == "general")


def winners(e: Parliament) -> dict[int, set[str]]:
    """For each district, the squashed names the commission declares elected there.

    The site's list of sitting deputies (wp610, by letter) and its list of deputies who left (wp603) both name each
    deputy's district, so together they cover every district's winner, including by-election winners; the by-election
    results pages add the districts whose result column prints "Обран".
    """
    found: dict[int, set[str]] = {}
    base = general(e)
    for lit in [None, *letters(base)]:
        artifact = cached(
            page(base.site, "wp610", base.id)
            if lit is None
            else page(base.site, "wp610", base.id, f"lit={lit}current_row=1")
        )
        if artifact is None:
            continue
        for t in tables(artifact):
            for row in t:
                where = re.search(r"ОВО\s*№\s*(\d+)", row[3]) if len(row) == 4 else None
                if where:
                    found.setdefault(int(where.group(1)), set()).add(squash(row[0]))
    left = cached(page(base.site, "wp603", base.id))
    if left is not None:
        for t in tables(left):
            for row in t:
                where = re.search(r"ОВО\s*№\s*(\d+)", row[4]) if len(row) == 5 else None
                if where:
                    found.setdefault(int(where.group(1)), set()).add(squash(row[0]))
    for name in ("wp039", "wp306_n"):
        artifact = cached(page(e.site, name, e.id))
        if artifact is None:
            continue
        for t in tables(artifact):
            for row in t:
                if len(row) == 6 and row[0].startswith("ОВО") and "Обран" in row[5]:
                    n = number(re.sub(r"\D", "", row[0]))
                    if n is not None:
                        found.setdefault(n, set()).add(squash(row[1].rpartition(" , ")[0] or row[1]))
                elif len(row) == 7 and row[0].isdigit() and "Обран" in row[6]:
                    found.setdefault(int(row[0]), set()).add(squash(row[4]))
    return found


def unit_for(p: Package, e_day: date, precinct: str, district: int, units: dict[str, str]) -> str:
    # Stations numbered below 100 are local to their district and repeat across districts.
    code = f"{e_day}:{district}:{precinct}" if precinct.startswith("0000") else f"{e_day}:{precinct}"
    if code not in units:
        id = units[code] = key("unit", code)
        abroad = district == ABROAD
        p.add(
            "reporting_unit",
            id=id,
            polity_id=UKRAINE,
            scheme_code="ua.dilnytsia",
            code=code,
            area_relation="unlocated" if abroad else "within",
            area_id=None if abroad else area("ua.ovo", district),
            kind="abroad_mission" if abroad else "standard",
        )
    return units[code]


TVO_SCHEME = "ua.tvo"


def tvo(plan: str, district: int) -> str:
    return area(TVO_SCHEME, f"{plan}:{district}")


def old_unit(p: Package, day: date, plan: str, precinct: str, district: int, units: dict[str, str], kind: str) -> str:
    """The reporting unit of an older election: a polling station of a territorial district, or the district itself."""
    code = f"{day}:{district}:{precinct}" if precinct else f"{day}:{district}"
    if code not in units:
        id = units[code] = key("unit", code)
        abroad = district == ABROAD
        p.add(
            "reporting_unit",
            id=id,
            polity_id=UKRAINE,
            scheme_code="ua.dilnytsia" if kind == "stations" else TVO_SCHEME,
            code=code,
            area_relation="unlocated" if abroad else "within" if kind == "stations" else "equals",
            area_id=None if abroad else tvo(plan, district),
            kind="abroad_mission" if abroad else "standard",
        )
    return units[code]


def values(
    p: Package,
    result_set: str,
    units: dict[str, str],
    day: date,
    found: Sheet,
    options: dict[str, str],
    plan: str = "",
) -> list[tuple[int | None, ...]]:
    """Stage one sheet's accounting and option values; return each precinct's printed measures for the overcount check."""
    layout = LAYOUTS[found.kind][1]
    kept: list[tuple[int | None, ...]] = []
    for row in found.rows:
        unit = (
            old_unit(p, day, plan, row[0], found.district, units, found.kind)
            if plan
            else unit_for(p, day, row[0], found.district, units)
        )
        measures = {measure: number(row[column]) for measure, column in layout.items()}
        for measure, value in measures.items():
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
        for column, name in found.options:
            value = number(row[column])
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=options[name],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        kept.append((measures.get(3), measures.get(4), measures.get(5), measures.get(2)))
    return kept


def parliament(e: Parliament) -> Package:
    word = "general" if e.kind == "general" else "by-election"
    p = Package(
        f"{SOURCE}/rada-{e.day}",
        SOURCE,
        f"The {e.day} Ukrainian parliamentary {'election' if e.kind == 'general' else 'by-election'} by polling "
        "station from the Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(UKRAINE)
    election = key("election", "rada", e.day)
    p.add(
        "election",
        id=election,
        polity_id=UKRAINE,
        kind=e.kind,
        date_from=e.day,
        date_to=e.day,
        status="held",
    )
    path = f"ua/{e.day}-{word}"
    if e.kind == "general":
        p.name(election, f"{e.day.year} Ukrainian parliamentary election", "en", "official")
        p.name(election, f"Вибори народних депутатів України {e.day.year}", "uk", "native", False)
    else:
        p.name(election, f"{e.day} Ukrainian parliamentary by-election", "en", "official")
        p.name(election, f"Проміжні вибори народних депутатів України {e.day:%d.%m.%Y}", "uk", "native", False)
    p.add("slug", path=path, entity_id=election, is_current=True)
    units: dict[str, str] = {}
    pages = [page(e.site, name, e.id) for name in ("wp300", "wp611", "wp039", "wp306_n")]
    for n in held(e):
        pages += [page(e.site, name, e.id, f"pf7331={n}") for name in ("wp336", "wp338")]
        pages.append(page(e.site, "wp033", e.id, f"pf7331={n}"))
    base = general(e)
    pages += [page(base.site, "wp610", base.id), page(base.site, "wp603", base.id)]
    pages += [page(base.site, "wp610", base.id, f"lit={lit}current_row=1") for lit in letters(base)]
    for url in dict.fromkeys(pages):
        artifact = cached(url)
        if artifact is not None:
            p.artifact(artifact, SOURCE_ID)
    numbers = held(e)
    over: list[tuple[int | None, ...]] = []
    declared = declared_list(e)
    parties = party_names()
    sheets = [s for n in numbers if (s := list_sheet(e, n)) is not None and s.rows]
    if sheets:
        contest = key("contest", "rada", e.day, "list")
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=RADA,
            magnitude=LIST_SEATS,
            round_number=1,
            electoral_system_id=LIST_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{e.day.year} Verkhovna Rada national list", "en", "official")
        p.name(contest, "Загальнодержавний багатомандатний виборчий округ", "uk", "native", False)
        p.add("slug", path=f"{path}/national-list", entity_id=contest, is_current=True)
        result_set = key("result_set", "rada", e.day, "list")
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
        p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=LIST_METHOD, lang="en"
        )
        options: dict[str, str] = {}
        for name in sorted({name for s in sheets for _, name in s.options}, key=norm):
            option = options[name] = key("option", "rada", e.day, "list", norm(name))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(name) if norm(name) in parties else None,
                ballot_label=name,
            )
        won = seats(e)
        for name, option in options.items():
            if norm(name) in won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won[norm(name)])
            if norm(name) in declared:
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=declared[norm(name)])
        for found_sheet in sheets:
            over += values(p, result_set, units, e.day, found_sheet, options)
        if ABROAD not in numbers:
            abroad_votes(p, e, result_set, options)
    leaders = winners(e)
    for n in numbers:
        found_sheet = district_sheet(e, n)
        if found_sheet is None or not found_sheet.rows:
            continue
        contest = key("contest", "rada", e.day, "district", n)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=RADA,
            district_area_id=area("ua.ovo", n),
            magnitude=1,
            round_number=1,
            electoral_system_id=DISTRICT_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Single-member district {n}", "en", "official")
        p.name(contest, f"Одномандатний виборчий округ № {n}", "uk", "native", False)
        p.add("slug", path=f"{path}/district-{n}", entity_id=contest, is_current=True)
        result_set = key("result_set", "rada", e.day, "district", n)
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
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=DISTRICT_METHOD,
            lang="en",
        )
        listed = roster(e, n)
        options = {}
        by_name: dict[str, str] = {}
        for _, header in found_sheet.options:
            known = listed.get(squash(header))
            printed = known.name if known else split_name(header)
            nominator = known.nominator if known else ""
            option = options[header] = key("option", "rada", e.day, "district", n, squash(header))
            by_name[squash(header)] = option
            p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=printed)
            person = key("person", "rada", e.day, n, squash(header))
            p.add(
                "person",
                id=person,
                birth_date=known.born if known else None,
                birth_precision="day" if known and known.born else None,
                gender=known.gender if known else None,
            )
            p.name(person, printed, "uk", "official")
            independent = norm(nominator) == SELF_NOMINATED
            candidacy = key("candidacy", "rada", e.day, n, squash(header))
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=printed,
                member_of_party_id=party(nominator)
                if nominator and not independent and norm(nominator) in parties
                else None,
                is_independent=independent if nominator else None,
            )
        printed = declared_district(e, n)
        # A candidate who lost here can win the district's later by-election, so the deputy lists may name two of
        # this contest's candidates; the one with the most votes here is the one this contest elected.
        named = sorted(leaders.get(n, set()) & by_name.keys(), key=lambda name: -printed.get(name, 0))
        if named:
            p.add("outcome", result_set_id=result_set, option_id=by_name[named[0]], kind="elected")
        for squashed, total in printed.items():
            if squashed in by_name:
                p.add("declared", result_set_id=result_set, option_id=by_name[squashed], measure=1, value=total)
        over += values(p, result_set, units, e.day, found_sheet, options)
    note_overcount(p, over)
    return p


def abroad_votes(p: Package, e: Parliament, result_set: str, options: dict[str, str]) -> None:
    """Where the site prints no station pages for the abroad district, store its printed list totals as one aggregate unit."""
    abroad = declared_list(e, "wp312")
    if not abroad:
        return
    unit = key("unit", f"{e.day}:abroad")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=UKRAINE,
        scheme_code="ua.dilnytsia",
        code=f"{e.day}:abroad",
        area_relation="unlocated",
        area_id=None,
        kind="aggregate",
    )
    artifact = cached(page(e.site, "wp312", e.id))
    if artifact is not None:
        p.artifact(artifact, SOURCE_ID)
    for name, option in options.items():
        if norm(name) in abroad:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=abroad[norm(name)],
            )
    p.add(
        "note",
        id=key("note", result_set, "abroad"),
        entity_id=result_set,
        kind="caveat",
        body_md="The results site prints no polling station pages for the polling stations abroad at this election, "
        "so the votes cast abroad are stored as one aggregate unit, from the page it prints for the abroad district.",
        lang="en",
    )


def note_overcount(p: Package, rows: list[tuple[int | None, ...]]) -> None:
    """Accept the accounting findings the protocols themselves cause, and say how many precincts they touch."""
    cast = sum(
        1
        for voted, valid, invalid, _ in rows
        if voted is not None and valid is not None and valid + (invalid or 0) != voted
    )
    turnout = sum(
        1 for voted, _, _, registered in rows if voted is not None and registered is not None and voted > registered
    )
    if cast:
        p.accept(
            "accounting.ballots_sum",
            f"{cast} polling station protocols print valid and invalid ballots that do not add to the voters who took "
            "part; stored as printed",
        )
    if turnout:
        p.accept(
            "accounting.turnout",
            f"{turnout} polling station protocols print more voters who took part than the roll holds, which the "
            "authority allows for voters added on election day; stored as printed",
        )


def declared_president(r: President) -> tuple[dict[str, tuple[str, int]], int | None]:
    """Each candidate's printed name and national votes, by squashed name, and the voters who took part."""
    artifact = cached(r.national or page(r.site, "wp300", r.id))
    found: dict[str, tuple[str, int]] = {}
    voters: int | None = None
    if artifact is not None:
        for t in tables(artifact):
            for row in t:
                if row[:1] == ["Кандидат"]:
                    continue
                if len(row) == 4 and number(row[3]) is not None and not row[0].isdigit():
                    found[squash(row[0])] = (row[0], number(row[3]) or 0)
                elif len(row) == 2 and row[0].startswith("Кількість виборців, які взяли участь"):
                    voters = number(row[1])
    return found, voters


def printed_for(declared: dict[str, tuple[str, int]], header: str) -> tuple[str, int | None]:
    """The candidate's printed name and national votes: a sheet may print the surname alone."""
    key_ = squash(header)
    hits = [d for d in declared if d == key_] or [d for d in declared if d.startswith(key_)]
    if len(hits) == 1:
        return declared[hits[0]][0], declared[hits[0]][1]
    return split_name(header), None


def declared_old(r: President) -> tuple[dict[str, tuple[str, int]], dict[int, int]]:
    """The national totals an older election prints: each candidate's name and votes, and the accounting figures."""
    if r.layout != "protocols":
        found, voters = declared_president(r)
        return found, {} if voters is None else {3: voters}
    artifact = cached(r.national)
    if artifact is None:
        return {}, {}
    national = protocol(artifact)
    accounting = {m: v for m, v in national.measures.items() if v is not None}
    found = {squash(name): (name, votes) for name, votes in national.votes.items()}
    if national.against is not None:
        found[squash(AGAINST)] = (AGAINST, national.against)
    return found, accounting


OLD_NOTE = {
    "stations": "",
    "districts": (
        "The commission's results site prints this round only by territorial district, so each district is one "
        "reporting unit. It prints the districts' votes for the candidates and their voters who took part; the "
        "invalid ballots and the votes against all candidates appear only as percentages, which are not stored."
    ),
    "protocols": (
        "The commission's results site prints this round by territorial district, and by polling station abroad, so "
        "each district is one reporting unit and each polling station abroad is another. The valid votes are the "
        "votes for the candidates, and the votes against all candidates are stored as their own option."
    ),
}


def president(r: President) -> Package:
    first = r.round == 1
    p = Package(
        f"{SOURCE}/president-{r.day}",
        SOURCE,
        f"The {r.day} Ukrainian presidential {'election' if first else 'runoff'} by polling station from the Central "
        "Election Commission",
        depends=[
            f"{SOURCE}/geo",
            f"{SOURCE}/registry",
            *(f"{SOURCE}/president-{x.day}" for x in PRESIDENT if x.day == r.after),
            *([f"{SOURCE}/geo-tvo"] if r.plan else []),
        ],
    )
    p.external.add(UKRAINE)
    election = key("election", "president", r.day)
    p.add(
        "election",
        id=election,
        polity_id=UKRAINE,
        kind="general" if first else "runoff",
        date_from=r.day,
        date_to=r.day,
        status="annulled" if r.annulled else "held",
    )
    path = f"ua/{r.day}-presidential" + ("" if first else "-runoff")
    suffix = "" if first else ", second round"
    p.name(election, f"{r.day.year} Ukrainian presidential election{suffix}", "en", "official")
    p.name(election, f"Вибори Президента України {r.day.year}, {r.round}-й тур", "uk", "native", False)
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", "president", r.day)
    previous = key("contest", "president", r.after) if r.after else None
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=r.round,
        previous_round_id=previous,
        electoral_system_id=PRESIDENT_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{r.day.year} Ukrainian presidential election, round {r.round}", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", "president", r.day)
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
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=PRESIDENT_METHOD,
        lang="en",
    )
    old = r.layout != "modern"
    if old:
        sheets = old_sheets(r)
        urls = [r.national, r.index, r.abroad, *district_pages(r)]
        if r.layout == "districts":
            urls += linked(r.index, "wp305")
        declared, accounting = declared_old(r)
        voters = accounting.get(3)
    else:
        numbers = sorted(grouped(r))
        sheets = [s for n in numbers if (s := sheet(r.site, "wp336", r.id, f"pt005f01={n}", n)) is not None]
        urls = [
            *(page(r.site, name, r.id) for name in ("wp300", "wp301", "wp312", "wp335")),
            *(page(r.site, "wp336", r.id, f"pt005f01={n}") for n in numbers),
        ]
        declared, voters = declared_president(r)
        accounting = {}
    for url in dict.fromkeys(url for url in urls if url):
        artifact = cached(url)
        if artifact is not None:
            p.artifact(artifact, SOURCE_ID)
    options: dict[str, str] = {}
    votes: dict[str, int] = {}
    totals: dict[str, int] = {}
    for found_sheet in sheets:
        for column, name in found_sheet.options:
            for row in found_sheet.rows:
                totals[name] = totals.get(name, 0) + (number(row[column]) or 0)
    for header in sorted({name for s in sheets for _, name in s.options}):
        if header == AGAINST:
            option = options[header] = key("option", "president", r.day, "against")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="none_of_the_above",
                ballot_label=AGAINST,
            )
            if (against := declared.get(squash(AGAINST))) is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=against[1])
            continue
        printed, total = printed_for(declared, header)
        printed = split_name(printed) if " " not in printed else printed
        option = options[header] = key("option", "president", r.day, squash(header))
        person = key("person", "president", r.day, squash(header))
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=printed)
        p.add("person", id=person)
        p.name(person, printed, "uk", "official")
        p.add(
            "candidacy",
            id=key("candidacy", "president", r.day, squash(header)),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=printed,
        )
        if total is not None:
            votes[header] = total
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=total)
    for measure, total in accounting.items():
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=total)
    if voters is not None and not old:
        p.add("declared", result_set_id=result_set, option_id=None, measure=3, value=voters)
    final = not any(x.after == r.day for x in PRESIDENT)
    ranked = votes or {name: total for name, total in totals.items() if name != AGAINST}
    for place, header in enumerate(sorted((h for h in options if h != AGAINST), key=lambda h: -ranked.get(h, 0))):
        if not ranked:
            break
        if final:
            kind = "elected" if place == 0 else "lost"
        else:
            kind = "advanced" if place < 2 else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=options[header], kind=kind)
    if old and OLD_NOTE[r.layout]:
        p.add(
            "note",
            id=key("note", result_set, "layout"),
            entity_id=result_set,
            kind="caveat",
            body_md=OLD_NOTE[r.layout],
            lang="en",
        )
    if r.annulled:
        p.add(
            "note",
            id=key("note", election, "annulled"),
            entity_id=election,
            kind="caveat",
            body_md="The Supreme Court annulled the results of this round on 3 December 2004 and ordered a repeat "
            "of the runoff, held on 26 December 2004. The figures stored are the ones the commission published.",
            lang="en",
        )
    units: dict[str, str] = {}
    over: list[tuple[int | None, ...]] = []
    for found_sheet in sheets:
        over += values(p, result_set, units, r.day, found_sheet, options, r.plan)
    note_overcount(p, over)
    return p


@functools.cache
def station_regions(r: President) -> dict[int, str]:
    """The region of each district from the index of an older election's polling station pages."""
    found: dict[int, str] = {}
    body = cached(r.index)
    if body is None:
        return found
    region = ""
    for t in tables(body):
        for row in t:
            if len(row) == 1 and re.search(r"\s-\s*\d+$", row[0]):
                region = re.sub(r"\s*-\s*\d+$", "", row[0])
            elif len(row) >= 4 and row[0].isdigit():
                found[int(row[0])] = region
    return found


def canonical_regions() -> dict[str, str]:
    """The region names the archive already holds, by their squashed spelling: the names the 2010 index prints."""
    return {squash(name): name for name in station_regions(next(x for x in PRESIDENT if x.site == "vp2010")).values()}


def tvo_regions(r: President) -> dict[int, str]:
    """The region of each domestic territorial district of an older election, spelled as the archive's regions are."""
    names = canonical_regions()
    if r.layout == "stations":
        found = station_regions(r)
    else:
        found = {s.district: s.region for s in old_sheets(r) if s.region and s.district != ABROAD}

    def canon(name: str) -> str | None:
        if squash(name) in names:
            return names[squash(name)]
        hits = [full for short, full in names.items() if short.startswith(squash(name))]
        return hits[0] if len(hits) == 1 else None

    return {n: full for n, name in found.items() if (full := canon(name)) is not None}


def geo_tvo() -> Package:
    p = Package(
        f"{SOURCE}/geo-tvo",
        SOURCE,
        "The territorial districts of Ukraine's presidential elections from 1999 to 2010 as the Central Election "
        "Commission numbers them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(UKRAINE)
    p.add(
        "scheme",
        code=TVO_SCHEME,
        polity_id=UKRAINE,
        kind="electoral_district",
        rank=2,
        noun="territorial electoral district",
        code_system=None,
    )
    for plan in dict.fromkeys(r.plan for r in PRESIDENT if r.plan):
        where: dict[int, str] = {}
        for r in PRESIDENT:
            if r.plan == plan:
                for n, name in tvo_regions(r).items():
                    where.setdefault(n, name)
        for n in sorted(where):
            id = tvo(plan, n)
            p.add("area", id=id, scheme_code=TVO_SCHEME)
            p.add("area_code", area_id=id, code=f"{plan}:{n}")
            p.add("area_parent", area_id=id, parent_id=area("ua.region", region_key(where[n])))
            p.name(id, f"Територіальний виборчий округ № {n} ({plan})", "uk", "official")
            p.name(id, f"Territorial electoral district {n} ({plan})", "en", "official", False)
    return p


def counted() -> list[Parliament]:
    """The parliamentary elections the commission published polling station results for."""
    return [e for e in PARLIAMENT if held(e)]


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        f"{SOURCE}/geo-tvo",
        *(f"{SOURCE}/rada-{e.day}" for e in counted()),
        *(f"{SOURCE}/president-{r.day}" for r in PRESIDENT),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    geo_tvo().write()
    for e in counted():
        parliament(e).write()
    for r in PRESIDENT:
        president(r).write()


def emit() -> None:
    package.run("load", packages())
