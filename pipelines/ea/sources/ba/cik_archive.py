"""Bosnia and Herzegovina's 2006-2014 elections from the Central Election Commission's archived result sites.

Before its JSON results application, the Commission (CIK BiH) published each election's certified results as a
website of its own: the 2006 general election as classic ASP pages, the 2010 and 2014 general elections as an ASP.NET
application driven by form postbacks, and the 2012 local elections as static HTML pages, with later certifications for
a few municipalities on sites of their own. This module walks each site down to the finest unit it reads: polling
stations for 2012 and municipalities for the general elections.
"""

import functools
import html
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from uuid import uuid5

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "ba.cik_archive"
BIH = str(uuid5(package.NAMESPACE, "polity\x1fBA"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBIH"))
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
POSTAL, MOBILE, PROVISIONAL = 3, 6, 5
_IDS = Package(SOURCE, SOURCE, "ids")
SITE = "https://www.izbori.ba"
USER_AGENT = "electionsarchive/0.1 (+https://electionsarchive.com)"


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)


# Reading HTML tables.


@dataclass
class Cell:
    text: str
    href: str | None = None
    titles: list[str] = field(default_factory=list)  # the titles of the images in the cell
    parts: list[str] = field(default_factory=list)  # the text's lines, split at line breaks


@dataclass
class Row:
    cells: list[Cell]
    hidden: bool  # the row or its table is styled display:none

    def texts(self) -> list[str]:
        return [c.text for c in self.cells]


BREAK = "\x1e"  # a line break within a cell
LETTERS = str.maketrans({"Ð": "Đ", "ð": "đ"})  # some pages print the Icelandic eth for the Bosnian dj


class _Rows(HTMLParser):
    """Every table row of a page, with its cells' text, first link, and image titles, innermost table first."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[Row] = []
        self.stack: list[tuple[Row | None, Cell | None, bool]] = []  # per open table: row, cell, hidden
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        hidden = "display:none" in a.get("style", "").replace(" ", "")
        if tag in {"script", "style"}:
            self.skip += 1
        elif tag == "table":
            self.stack.append((None, None, hidden or bool(self.stack and self.stack[-1][2])))
        elif tag == "tr" and self.stack:
            row, _, table_hidden = self.stack[-1]
            if row is not None and row.cells:  # the previous row was left unclosed
                self.rows.append(row)
            self.stack[-1] = (Row([], hidden or table_hidden), None, table_hidden)
        elif tag in {"td", "th"} and self.stack and self.stack[-1][0] is not None:
            row, _, table_hidden = self.stack[-1]
            cell = Cell("")
            assert row is not None
            row.cells.append(cell)
            self.stack[-1] = (row, cell, table_hidden)
        elif tag == "a" and self.stack and self.stack[-1][1] is not None:
            cell = self.stack[-1][1]
            if cell is not None and cell.href is None and a.get("href"):
                cell.href = a["href"]
        elif tag == "img" and self.stack and self.stack[-1][1] is not None:
            cell = self.stack[-1][1]
            if cell is not None:
                cell.titles.append(a.get("title") or a.get("alt") or a.get("src") or "")
        elif tag == "br" and self.stack and self.stack[-1][1] is not None:
            cell = self.stack[-1][1]
            if cell is not None:
                cell.text += BREAK

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        elif tag == "table" and self.stack:
            row, _, _ = self.stack.pop()
            if row is not None and row.cells and row not in self.rows:
                self.rows.append(row)
        elif tag == "tr" and self.stack:
            row, _, table_hidden = self.stack[-1]
            if row is not None and row.cells:
                self.rows.append(row)
            self.stack[-1] = (None, None, table_hidden)
        elif tag in {"td", "th"} and self.stack:
            row, _, table_hidden = self.stack[-1]
            self.stack[-1] = (row, None, table_hidden)

    def handle_data(self, data: str) -> None:
        if self.skip or not self.stack:
            return
        cell = self.stack[-1][1]
        if cell is not None:
            cell.text += data

    def close(self) -> None:
        super().close()
        for row in self.rows:
            for cell in row.cells:
                cell.text = cell.text.translate(LETTERS)
                cell.parts = [" ".join(line.split()) for line in cell.text.split(BREAK) if line.strip()]
                cell.text = " ".join(cell.text.replace(BREAK, " ").split())


def rows(text: str) -> list[Row]:
    parser = _Rows()
    parser.feed(text)
    parser.close()
    return parser.rows


def page_text(text: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def number(text: str) -> int | None:
    """A count as the sites print it, with or without thousands dots."""
    text = text.strip().replace(".", "").replace(" ", "")
    return int(text) if re.fullmatch(r"\d+", text) else None


def read(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_bytes().decode("utf-8", errors="replace")


def maybe_read(url: str) -> str | None:
    try:
        return read(url)
    except LookupError:
        return None


def get_many(urls: list[str]) -> None:
    raw.fetch_many(SOURCE, sorted(set(urls) - raw.keys(SOURCE)), suffix=".html", workers=12)


# The 2012 local elections: static pages, one site per certification.

LOCAL_2012 = date(2012, 10, 7)
SITES_2012 = (  # in the order the Commission certified them; a later site's municipality replaces an earlier one's
    ("RezultatiPotvrdjeni", date(2012, 11, 6)),
    ("RezultatiDopunska0811", date(2012, 11, 8)),
    ("Rezultati28112012", date(2012, 11, 28)),
    ("Rezultati12122012", date(2012, 12, 12)),
)


def site_2012(site: str, page: str) -> str:
    return f"{SITE}/Rezultati/{site}/files/{page}"


def list_2012(site: str, race: str) -> str:
    return site_2012(site, f"Lista_opstina_trka_{race}.html")


def report_2012(site: str, race: str, municipality: str) -> str:
    return site_2012(site, f"Glavni_report_trka_{race}_opstina_{municipality}.html")


def municipalities_2012(site: str, race: str) -> list[tuple[str, str]]:
    """The (code, name) of each municipality a site lists for a race."""
    out = []
    for row in rows(read(list_2012(site, race))):
        for cell in row.cells:
            m = re.fullmatch(r"(?:\d+ \. )?(\d{3}) - (.+)", cell.text)
            if m and cell.href and "Glavni_report" in cell.href:
                out.append((m.group(1), m.group(2)))
    return list(dict.fromkeys(out))


def station_links(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"href='(Biracka_mjesta_trka_\d_opstina_\d+_ps_[^']+\.html)'", text)))


def fetch_2012() -> None:
    for site, _ in SITES_2012:
        get_many([list_2012(site, race) for race in ("8", "9")])
        reports = [report_2012(site, race, code) for race in ("8", "9") for code, _ in municipalities_2012(site, race)]
        get_many(reports)
        stations = [site_2012(site, link) for url in reports for link in station_links(read(url))]
        get_many(stations)
        print(f"{SOURCE}: {site}: {len(reports)} reports, {len(stations)} stations", flush=True)


# The 2010 and 2014 general elections: one ASP.NET application whose every level is a form postback on a session.

GENERAL_2010 = date(2010, 10, 3)
GENERAL_2014 = date(2014, 10, 12)
APPS = {  # election to the application's start page, the Presidency's results
    "2010-10-03": f"{SITE}/Finalni2010/Finalni/PredsjednistvoBiH/Default.aspx",
    "2014-10-12": f"{SITE}/Potvrdjeni2014/Finalni/PredsjednistvoBiH/Default.aspx",
}
MENU_2010 = {  # race to the menu link that opens it; the Presidency is the application's start page
    "1": None,
    "2": "ctl00$linkParlamentSkupstine",
    "4": "ctl00$linkParlamentFederacije",
    "5": "ctl00$linkPredsednikRS",
    "6": "ctl00$linkNarodnaSkupstina",
    "7": "ctl00$linkSkupstineKantona",
}
TOP_2010 = re.compile(r"rightContentPlaceHolder\$(lb\d+|link(FBIH|RS|Bosnjacki|Hrvatski|Srpski))$|imgMap\d+$")
MUNICIPALITY_2010 = re.compile(r"gvMunicipalities\$ctl\d+\$linkRegions$")


def hidden_fields(text: str) -> dict[str, str]:
    out = {}
    for tag in re.findall(r"<input[^>]*type=\"hidden\"[^>]*>", text):
        name = re.search(r'name="([^"]*)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name:
            out[name.group(1)] = html.unescape(value.group(1)) if value else ""
    return out


def postbacks(text: str) -> list[tuple[str, str]]:
    return list(dict.fromkeys(re.findall(r"__doPostBack\('([^']*)','([^']*)'\)", text.replace("&#39;", "'"))))


def path_key(start: str, path: tuple[tuple[str, str], ...]) -> str:
    """The cache key of the page a sequence of postbacks reaches from the application's start page."""
    steps = ">".join(f"{target}|{argument}" if argument else target for target, argument in path)
    return f"{start}#postback:{steps}"


def breadcrumb(text: str) -> str:
    """The page's location as the application prints it above the results ("race -> district -> municipality")."""
    words = page_text(text)
    start = words.find("Skupštine kantona u FBiH")
    words = words[start + len("Skupštine kantona u FBiH") :] if start >= 0 else words
    stop = min(
        (
            i
            for i in (words.find(" Osnovne izborne jedinice"), words.find(" OPĆE INFORMACIJE"), words.find(" IZBORNE"))
            if i >= 0
        ),
        default=len(words),
    )
    return words[:stop].strip()


class Session2010:
    """One browsing session of the application, replaying a path of postbacks from the start page."""

    def __init__(self, start: str) -> None:
        self.start = start
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=120)

    def post(self, url: str, text: str, target: str, argument: str) -> tuple[str, str]:
        for attempt in range(4):
            try:
                response = self.client.post(
                    url, data={**hidden_fields(text), "__EVENTTARGET": target, "__EVENTARGUMENT": argument}
                )
                response.raise_for_status()
                return str(response.url), response.text
            except httpx.HTTPError:
                if attempt == 3:
                    raise
        raise AssertionError

    def walk(self, path: tuple[tuple[str, str], ...]) -> tuple[str, str]:
        self.client.cookies.clear()
        url, text = self.start, self.client.get(self.start).text
        for target, argument in path:
            url, text = self.post(url, text, target, argument)
        return url, text


def store_page(start: str, path: tuple[tuple[str, str], ...], text: str) -> None:
    raw.store(
        SOURCE,
        path_key(start, path),
        text.encode(),
        suffix=".html",
        retrieved_at=datetime.now(UTC),
        media_type="text/html; charset=utf-8",
    )


def crawl_race(start: str, race: str) -> int:
    """Every district and municipality page of one race, stored under the postback path that reaches it."""
    done = raw.keys(SOURCE)
    session = Session2010(start)
    menu = MENU_2010[race]
    root: tuple[tuple[str, str], ...] = ((menu, ""),) if menu else ()
    url, text = session.walk(root)
    if root and path_key(start, root) not in done:
        store_page(start, root, text)
    count = 0
    tops = [(t, a) for t, a in postbacks(text) if TOP_2010.search(t)]
    parents: list[tuple[tuple[tuple[str, str], ...], str, str]] = [(root, url, text)]
    for step in tops:
        path = (*root, step)
        if path_key(start, path) in done:
            parents.append((path, *session.walk(path)))
            continue
        here, page = session.walk(path)
        store_page(start, path, page)
        count += 1
        parents.append((path, here, page))
    for path, _, page in parents:
        for step in [(t, a) for t, a in postbacks(page) if MUNICIPALITY_2010.search(t)]:
            child = (*path, step)
            if path_key(start, child) in done:
                continue
            _, leaf = session.walk(child)
            store_page(start, child, leaf)
            count += 1
    return count


def fetch_apps() -> None:
    from concurrent.futures import ThreadPoolExecutor

    for slug, start in APPS.items():
        with ThreadPoolExecutor(6) as pool:
            for race, n in zip(MENU_2010, pool.map(lambda r: crawl_race(start, r), MENU_2010), strict=True):
                print(f"{SOURCE}: {slug} race {race}: {n} pages", flush=True)


# The 2006 general election: classic ASP pages linked by plain URLs.

GENERAL_2006 = date(2006, 10, 1)
SITE_2006 = f"{SITE}/rezultati/konacni/"
TOTALS_2006 = {
    "/PoOsnovnimJedinicama1.asp?": "/PoOsnovnimJedinicamaUkupno.asp?",
    "/OsnovneJedinicePolling.asp?": "/OsnovneJediniceRezultatiUkupno.asp?",
}
SKIP_2006 = re.compile(r"Polling|PoOsnovnimJedinicama1\.asp|Graf|\.(css|js|swf|gif|jpg|png|pdf)$", re.IGNORECASE)


def links_2006(url: str, text: str) -> list[str]:
    from urllib.parse import urljoin

    out = []
    for href in re.findall(r"href=\"([^\"#]+)\"", text, flags=re.IGNORECASE):
        target = urljoin(url, html.unescape(href).strip())
        for stations, total in TOTALS_2006.items():  # a municipality's station list to its total
            target = target.replace(stations, total)
        if target.startswith(SITE_2006) and not SKIP_2006.search(target):
            out.append(target)
    return list(dict.fromkeys(out))


def fetch_2006() -> None:
    """Every page of the 2006 site above its polling stations, breadth first from the index."""
    seen = {SITE_2006}
    wave = [SITE_2006]
    while wave:
        get_many(wave)
        nxt = []
        for url in wave:
            text = maybe_read(url)
            if text is None:
                continue
            for target in links_2006(url, text):
                if target not in seen:
                    seen.add(target)
                    nxt.append(target)
        print(f"{SOURCE}: 2006: {len(wave)} pages, {len(nxt)} more", flush=True)
        wave = nxt


# One model for every site: contests, their options, and the units they are counted in.

CHANNELS = (
    (POSTAL, "postal", "postal_district"),
    (MOBILE, "absentee", "mobile"),
    (PROVISIONAL, "tendered", "aggregate"),
)


@dataclass
class Option:
    label: str
    code: str | None  # the party's certification number; a candidate's own number for minority seats
    votes: int
    channels: dict[int, int] = field(default_factory=dict)  # channel to votes counted centrally
    seats: int | None = None
    elected: bool = False
    minority: bool = False  # a candidate for a council seat reserved for national minorities


@dataclass
class Unit:
    code: str  # the unit's own code: a polling station's, or a municipality's
    municipality: str  # the municipality the unit lies in
    accounting: dict[int, int]
    votes: dict[str, int]  # option label to votes
    kind: str = "standard"


@dataclass
class Contest:
    race: str
    key: str  # unique within the election
    name: str  # the district as the Commission names it
    district: tuple[str, str] | None  # (scheme, code) of the contest's area
    options: list[Option]
    declared: dict[int, int]  # the district's accounting as printed
    units: list[Unit]
    files: list[str]  # the cache keys of the pages read
    magnitude: int | None = None
    compensation: bool = False
    finest: str = "polling station"
    names: dict[str, str] = field(default_factory=dict)  # a candidate's name by label, where the label runs on


def accounting_2012(found: list[Row]) -> dict[str, int]:
    out = {}
    for row in found:
        if len(row.cells) == 2 and not row.hidden:
            value = number(row.cells[1].text)
            if value is not None:
                out.setdefault(row.cells[0].text, value)
    return out


def option_rows(found: list[Row], width: int) -> tuple[list[list[Cell]], list[list[Cell]]]:
    """The party (or candidate) rows and the national-minority rows of a results table, by their column count."""
    main: list[list[Cell]] = []
    minority: list[list[Cell]] = []
    target = main
    for row in found:
        texts = row.texts()
        if texts and texts[0] == "NACIONALNE MANJINE":
            target = minority
        if row.hidden or len(row.cells) != width or texts[0] == "Šifra" or number(texts[2]) is None:
            continue
        target.append(row.cells)
    return main, minority


def report_2012_contest(site: str, race: str, code: str, name: str) -> Contest:
    url = report_2012(site, race, code)
    text = read(url)
    found = rows(text)
    info = accounting_2012(found)
    main, minorities = option_rows(found, 9)
    options = []
    for cells, minority in [*((c, False) for c in main), *((c, True) for c in minorities)]:
        votes = [number(c.text) or 0 for c in cells[2:7]]
        options.append(
            Option(
                label=cells[1].text,
                code=cells[0].text or None,
                votes=votes[0],
                channels={POSTAL: votes[2], MOBILE: votes[3], PROVISIONAL: votes[4]},
                seats=None if race == "8" or minority else number(cells[8].text) or 0,
                elected=bool(cells[8].titles) if race == "8" else bool(minority and number(cells[8].text)),
                minority=minority,
            )
        )
    declared = {
        REGISTERED: info["Ukupan broj glasača"],
        VALID: info["Ukupno važećih glasova"],
        INVALID: info["Nevažeći po drugim kriterijima"] + info["Ukupno nevažećih praznih listića"],
    }
    codes = Counter(o.code for o in options)
    by_code = {o.code: o.label for o in options if codes[o.code] == 1}
    labels = {o.label for o in options}

    def match(code: str, label: str) -> str:
        """The option a station row counts: by its code, which station pages print alike, else by its label."""
        return label if label in labels else by_code.get(code, label)

    units = []
    files = [url]
    for link in station_links(text):
        station_url = site_2012(site, link)
        files.append(station_url)
        page = rows(read(station_url))
        station = accounting_2012(page)
        lists, extra = option_rows(page, 3)
        units.append(
            Unit(
                code=link.rsplit("_ps_", 1)[1].removesuffix(".html"),
                municipality=code,
                accounting={
                    REGISTERED: station["Ukupan broj glasača"],
                    VALID: station["Ukupno važećih glasova"],
                    INVALID: station["Ukupno nevažećih glasova"],
                },
                votes={match(c[0].text, c[1].text): number(c[2].text) or 0 for c in [*lists, *extra]},
            )
        )
    seats = sum(o.seats or 0 for o in options) + sum(o.elected for o in options if o.minority)
    return Contest(
        race,
        f"{race}:{code}",
        name,
        ("municipality", code),
        options,
        declared,
        units,
        files,
        magnitude=1 if race == "8" else seats,
    )


def contests_2012() -> list[Contest]:
    found: dict[tuple[str, str], Contest] = {}
    for site, _ in SITES_2012:
        for race in ("8", "9"):
            for code, name in municipalities_2012(site, race):
                found[race, code] = report_2012_contest(site, race, code, name)
    return [found[k] for k in sorted(found)]


# Reading the 2010 pages.

RACE_TITLES_2010 = {
    "Predsjedništvo Bosne i Hercegovine": "1",
    "Predsjednik i potpredsjednici Republike Srpske": "5",
    "Zastupnički dom Parlamentarne skupštine Bosne i Hercegovine": "2",
    "Zastupnički dom Parlamenta Federacije Bosne i Hercegovine": "4",
    "Narodna skupština Republike Srpske": "6",
    "Skupštine kantona u Federaciji Bosne i Hercegovine": "7",
}
SUMMARIES_2010 = {"501": ("2", "1"), "502": ("2", "2"), "400": ("4", "1"), "300": ("6", "2")}  # race, entity
MEMBERS_2010 = {"linkBosnjacki": "1", "linkHrvatski": "1", "linkSrpski": "2"}  # Presidency member to entity
PRESIDENCY_UNITS_2010 = {"701/702": "1", "703": "2"}


@dataclass
class Page:
    key: str
    rows: list[Row]

    def info(self) -> dict[str, int]:
        return accounting_2012(self.rows)

    def table(self) -> list[list[Cell]]:
        """The results table's rows: those after its header, as wide as it, with a count in the votes column."""
        out: list[list[Cell]] = []
        width = 0
        votes = 0
        for row in self.rows:
            texts = row.texts()
            if row.hidden or not texts:
                continue
            if texts[0] in {"Šifra", "Kandidat"} and len(texts) > 1:
                width, votes = len(texts), texts.index("Broj glasova")
                continue
            if width and len(texts) == width and number(texts[votes]) is not None:
                out.append(row.cells)
            elif width and out:
                break
        return out


@functools.cache
def pages_2010(start: str) -> dict[str, Page]:
    return {k: Page(k, rows(read(k))) for k in sorted(raw.keys(SOURCE)) if k.startswith(f"{start}#postback")}


def where_2010(page: Page) -> list[str]:
    return [part.strip() for part in breadcrumb(read(page.key)).split(" -> ")]


def accounting_2010(page: Page) -> dict[int, int]:
    info = page.info()
    if "Ukupno nevažećih glasova" in info:
        invalid = info["Ukupno nevažećih glasova"]
    else:  # the 2014 district pages split invalid ballots into blank ones and the rest
        invalid = info["Nevažeći po drugim kriterijima"] + info["Ukupno nevažećih praznih listića"]
    return {VALID: info["Ukupno važećih glasova"], INVALID: invalid}


def list_options_2010(page: Page, seats: int | None = None) -> list[Option]:
    """A party table: code, name, votes, share, and the seats in the column `seats` counts from the left."""
    return [
        Option(
            label=cells[1].text,
            code=cells[0].text,
            votes=number(cells[2].text) or 0,
            seats=None if seats is None else number(cells[seats].text) or 0,
        )
        for cells in page.table()
    ]


def candidate_options_2010(page: Page) -> list[Option]:
    return [
        Option(label=cells[0].text, code=None, votes=number(cells[1].text) or 0, elected=bool(cells[-1].titles))
        for cells in page.table()
    ]


def unit_votes_2010(page: Page, candidates: bool) -> dict[str, int]:
    if candidates:
        return {cells[0].text: number(cells[1].text) or 0 for cells in page.table()}
    return {cells[0].text: number(cells[2].text) or 0 for cells in page.table()}


@functools.cache
def layout_2010(start: str) -> tuple[dict[tuple[str, str], Page], dict[tuple[str, str], dict[str, tuple[str, Page]]]]:
    """Each race's district pages and, under each district, its municipalities' pages by code."""
    districts: dict[tuple[str, str], Page] = {}
    units: dict[tuple[str, str], dict[str, tuple[str, Page]]] = {}
    for page in pages_2010(start).values():
        parts = where_2010(page)
        race = RACE_TITLES_2010.get(parts[0])
        if race is None:
            continue
        if len(parts) == 2:
            districts.setdefault((race, parts[1]), page)
        elif len(parts) == 3:
            m = re.match(r"(\d{3}) - (.+?)(?: Biračko mjesto|$)", parts[2])
            if m:
                units.setdefault((race, parts[1]), {}).setdefault(m.group(1), (m.group(2), page))
    return districts, units


def units_2010(start: str) -> dict[str, str]:
    _, units = layout_2010(start)
    return {code: name for found in units.values() for code, (name, _) in found.items()}


def magnitudes_2010(start: str) -> dict[str, tuple[str, int]]:
    """Each district's name and seats as the race's start page lists them ("511 - IZBORNA JEDINICA 1 3 ZASTUPNIKA")."""
    out = {}
    for page in pages_2010(start).values():
        if len(where_2010(page)) == 1:
            text = page_text(read(page.key))
            for code, name, seats in re.findall(r"(\d{3}) - (.+?) (\d+) ZASTUPNIKA", text):
                out[code] = (name, int(seats))
    return out


def municipal_units(start: str, race: str, district: str, candidates: bool) -> tuple[list[Unit], list[str]]:
    _, units = layout_2010(start)
    out = []
    files = []
    for code, (_, page) in sorted(units.get((race, district), {}).items()):
        files.append(page.key)
        out.append(Unit(code, code, accounting_2010(page), unit_votes_2010(page, candidates)))
    return out, files


def by_code(options: list[Option], units: list[Unit]) -> None:
    """Re-key unit votes printed by party code to the district's labels."""
    labels = {o.code: o.label for o in options}
    for u in units:
        u.votes = {labels.get(code, code): n for code, n in u.votes.items()}


def contests_2010(start: str) -> list[Contest]:
    districts, _ = layout_2010(start)
    pages = pages_2010(start)
    seats = magnitudes_2010(start)
    out: list[Contest] = []
    members: dict[str, list[tuple[Option, Page]]] = {}
    for page in pages.values():
        last = page.key.rsplit("$", 1)[-1]
        if last in MEMBERS_2010:
            members.setdefault(MEMBERS_2010[last], []).extend((o, page) for o in candidate_options_2010(page))
    for entity, found in sorted(members.items()):
        unit_key = next(k for k, v in PRESIDENCY_UNITS_2010.items() if v == entity)
        units, files = municipal_units(start, "1", unit_key, candidates=True)
        out.append(
            Contest(
                "1",
                f"1:{entity}",
                ENTITY_NAMES[entity],
                ("entity", entity),
                [o for o, _ in found],
                accounting_2010(found[0][1]),
                units,
                sorted({p.key for _, p in found}) + files,
                magnitude=2 if entity == "1" else 1,
                finest="municipality",
            )
        )
    president = pages[path_key(start, ((MENU_2010["5"] or "", ""),))]
    options = candidate_options_2010(president)
    units, files = municipal_units(start, "5", "600", candidates=True)
    out.append(
        Contest(
            "5",
            "5",
            ENTITY_NAMES["2"],
            ("entity", "2"),
            options,
            accounting_2010(president),
            units,
            [president.key, *files],
            magnitude=sum(o.elected for o in options),
            finest="municipality",
        )
    )
    for (race, district), page in sorted(districts.items()):
        if race not in {"2", "4", "6", "7"}:
            continue
        m = re.match(r"(?:KANTON )?(\d{3})", district)
        if not m:
            continue
        code = m.group(1)
        options = list_options_2010(page, seats=4)
        units, files = municipal_units(start, race, district, candidates=False)
        by_code(options, units)
        out.append(
            Contest(
                race,
                f"{race}:{code}",
                seats[code][0] if code in seats else district,
                ("constituency", code),
                options,
                accounting_2010(page),
                units,
                [page.key, *files],
                magnitude=sum(o.seats or 0 for o in options),
                finest="municipality",
            )
        )
    for page in pages.values():
        parts = where_2010(page)
        m = re.match(r"(\d{3}) - .* - Zbirni rezultati$", parts[0])
        if not m or m.group(1) not in SUMMARIES_2010:
            continue
        race, entity = SUMMARIES_2010[m.group(1)]
        options = list_options_2010(page, seats=6)
        out.append(
            Contest(
                race,
                f"{race}:c{entity}",
                ENTITY_NAMES[entity],
                ("entity", entity),
                options,
                accounting_2010(page),
                [
                    Unit(
                        f"entity:{race}:c{entity}",
                        "",
                        accounting_2010(page),
                        {o.label: o.votes for o in options},
                        "aggregate",
                    )
                ],
                [page.key],
                magnitude=sum(o.seats or 0 for o in options),
                compensation=True,
            )
        )
    return out


def race_totals(e: Election) -> list[str]:
    """Each entity's districts summed against the entity-wide totals the Commission prints for the race."""
    lines = []
    for c in everything()[e.slug]:
        if not (c.compensation and c.district):
            continue
        entity = c.district[1]
        parts = [
            d
            for d in everything()[e.slug]
            if d.race == c.race
            and d.district
            and d.district[0] == "constituency"
            and district_entity(d.race, d.district[1]) == entity
        ]
        here = sum(d.declared[VALID] for d in parts)
        verdict = "match" if here == c.declared[VALID] else f"differ by {here - c.declared[VALID]:,}"
        lines.append(
            f"{e.slug} race {c.race} entity {entity}: districts {here:,} valid, entity {c.declared[VALID]:,}, {verdict}"
        )
    return lines


# Reading the 2006 pages.

DISTRICT_PAGES_2006 = {
    "2": "parlament_bih/bihMainPage.asp",
    "4": "parlament_fbih/entitiesMainPage.asp",
    "6": "narodna_skupstina_rs/entitiesMainPage.asp",
    "7": "kantoni/cantonsMainPage.asp",
}
SUMMARIES_2006 = {
    "parlament_bih/FBiH_rezultati.asp": ("2", "1"),
    "parlament_bih/RS_rezultati.asp": ("2", "2"),
    "parlament_fbih/FBiH_rezultati.asp": ("4", "1"),
    "narodna_skupstina_rs/RS_rezultati.asp": ("6", "2"),
}
MEMBERS_2006 = {  # Presidency member pages and the entity whose ballot they are on
    "predsjednistvo_bih/Predsjednistvo.asp?nivo=701&nivo1=702&entitet=A": "1",
    "predsjednistvo_bih/Predsjednistvo.asp?nivo=702&nivo1=701&entitet=A": "1",
    "predsjednistvo_bih/Predsjednistvo.asp?nivo=703&nivo1=703&entitet=B": "2",
}
UNIT_LEVELS_2006 = {"1": "701", "2": "703"}  # entity to the level whose municipality pages hold its ballot
PRESIDENT_2006 = "predsjednik_rs/RSPredsjednik.asp?nivo=600"
CHANNELS_2006 = {"Poštom": POSTAL, "Odsustvo": MOBILE, "Potvrđeni": PROVISIONAL}


def party_code(text: str) -> str:
    return text.strip().zfill(5)


@functools.cache
def pages_2006() -> dict[str, Page]:
    return {k[len(SITE_2006) :]: Page(k, rows(read(k))) for k in sorted(raw.keys(SOURCE)) if k.startswith(SITE_2006)}


def query(path: str) -> dict[str, str]:
    from urllib.parse import parse_qs, urlsplit

    return {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}


def accounting_2006(page: Page) -> dict[int, int]:
    info = page.info()
    out = {VALID: info["Ukupno važećih glasova"]}
    if "Ukupno nevažećih glasova" in info:
        out[INVALID] = info["Ukupno nevažećih glasova"]
    return out


def numbered(page: Page, width: int) -> list[list[Cell]]:
    """The rows of a results table whose first column numbers them ("1.", "2", ...)."""
    return [
        row.cells
        for row in page.rows
        if not row.hidden and len(row.cells) == width and re.fullmatch(r"\d+\.?", row.cells[0].text)
    ]


def candidates_2006(page: Page) -> list[Option]:
    """Candidates with their votes by channel: each candidate's row is followed by one row per channel."""
    out: list[Option] = []
    for row in page.rows:
        texts = row.texts()
        if row.hidden:
            continue
        if len(texts) == 5 and re.fullmatch(r"\d+", texts[0]) and number(texts[2]) is not None:
            out.append(Option(texts[1], None, number(texts[2]) or 0, elected=bool(texts[4].strip())))
            out[-1].code = row.cells[1].parts[-1] if len(row.cells[1].parts) > 1 else None  # the party, for now
        elif len(texts) == 2 and out and texts[0] in CHANNELS_2006 and number(texts[1]) is not None:
            out[-1].channels[CHANNELS_2006[texts[0]]] = number(texts[1]) or 0
    return out


def candidate_name_2006(page_rows: list[Row], label: str) -> str:
    for row in page_rows:
        for cell in row.cells:
            if cell.text == label and len(cell.parts) > 1:
                return cell.parts[0]
    return label


def unit_pages_2006(prefix: str, level: str) -> dict[str, Page]:
    """The municipality total pages under one level ("nivo"), by municipality code."""
    out = {}
    for path, page in pages_2006().items():
        if path.startswith(prefix) and "Ukupno.asp" in path:
            q = query(path)
            if q.get("nivo") == level and "kod" in q:
                out.setdefault(q["kod"], page)
    return out


def units_from_2006(found: dict[str, Page], candidates: bool) -> tuple[list[Unit], list[str]]:
    out = []
    for code, page in sorted(found.items()):
        if candidates:
            votes = {cells[1].text: number(cells[2].text) or 0 for cells in numbered(page, 3)}
        else:
            votes = {party_code(cells[1].text): number(cells[3].text) or 0 for cells in numbered(page, 4)}
        out.append(Unit(code, code, {VALID: sum(votes.values())}, votes))
    return out, [p.key for _, p in sorted(found.items())]


def units_2006() -> dict[str, str]:
    out = {}
    for path, page in pages_2006().items():
        if "Ukupno.asp" in path and "kod" in query(path):
            first = next((r.texts()[0] for r in page.rows if r.texts() and r.texts()[0]), "")
            out.setdefault(query(path)["kod"], first)
    return out


def contests_2006() -> list[Contest]:
    pages = pages_2006()
    out: list[Contest] = []
    for entity in ("1", "2"):
        members = [pages[p] for p, e in MEMBERS_2006.items() if e == entity]
        options = [o for page in members for o in candidates_2006(page)]
        names = {o.label: candidate_name_2006(page.rows, o.label) for page in members for o in candidates_2006(page)}
        for o in options:
            o.code = None
        units, files = units_from_2006(unit_pages_2006("predsjednistvo_bih/", UNIT_LEVELS_2006[entity]), True)
        c = Contest(
            "1",
            f"1:{entity}",
            ENTITY_NAMES[entity],
            ("entity", entity),
            options,
            {VALID: sum(accounting_2006(page)[VALID] for page in members)},
            units,
            [page.key for page in members] + files,
            magnitude=len(members),
            finest="municipality votes",
        )
        c.names = names
        out.append(c)
    president = pages[PRESIDENT_2006]
    options = candidates_2006(president)
    names = {o.label: candidate_name_2006(president.rows, o.label) for o in options}
    for o in options:
        o.code = None
    units, files = units_from_2006(unit_pages_2006("predsjednik_rs/", "600"), True)
    c = Contest(
        "5",
        "5",
        ENTITY_NAMES["2"],
        ("entity", "2"),
        options,
        accounting_2006(president),
        units,
        [president.key, *files],
        magnitude=sum(o.elected for o in options),
        finest="municipality votes",
    )
    c.names = names
    out.append(c)
    for race, prefix in DISTRICT_PAGES_2006.items():
        for path, page in sorted(pages.items()):
            if not path.startswith(prefix + "?jed="):
                continue
            code = query(path)["jed"]
            heading = next(r.texts()[0] for r in page.rows if r.texts() and r.texts()[0].startswith(code))
            options = [
                Option(
                    cells[2].text,
                    party_code(cells[1].text),
                    number(cells[3].text) or 0,
                    seats=number(cells[5].text) or 0,
                )
                for cells in numbered(page, 6)
            ]
            units, files = units_from_2006(unit_pages_2006("PoOsnovnimJedinicamaUkupno", code), False)
            by_code(options, units)
            name = (
                magnitudes_2010(APPS["2010-10-03"]).get(code, (heading.split(" - ")[-1], 0))[0]
                if race == "7"
                else heading
            )
            out.append(
                Contest(
                    race,
                    f"{race}:{code}",
                    name.split(" - ", 1)[-1] if race != "7" else name,
                    ("constituency", code),
                    options,
                    accounting_2006(page),
                    units,
                    [page.key, *files],
                    magnitude=page.info().get("Broj mjesta za dodjelu"),
                    finest="municipality votes",
                )
            )
    for path, (race, entity) in SUMMARIES_2006.items():
        page = pages[path]
        options = [
            Option(
                cells[2].text, party_code(cells[1].text), number(cells[3].text) or 0, seats=number(cells[7].text) or 0
            )
            for cells in numbered(page, 8)
        ]
        out.append(
            Contest(
                race,
                f"{race}:c{entity}",
                ENTITY_NAMES[entity],
                ("entity", entity),
                options,
                accounting_2006(page),
                [
                    Unit(
                        f"entity:{race}:c{entity}",
                        "",
                        accounting_2006(page),
                        {o.label: o.votes for o in options},
                        "aggregate",
                    )
                ],
                [page.key],
                magnitude=sum(o.seats or 0 for o in options),
                compensation=True,
            )
        )
    return out


# Packages.


@dataclass(frozen=True)
class Election:
    slug: str
    day: date
    kind: str
    title: str


ELECTIONS = (
    Election("2006-10-01", date(2006, 10, 1), "general", "2006 Bosnian general election"),
    Election("2010-10-03", GENERAL_2010, "general", "2010 Bosnian general election"),
    Election("2012-10-07", LOCAL_2012, "local", "2012 Bosnian local elections"),
    Election("2014-10-12", GENERAL_2014, "general", "2014 Bosnian general election"),
)
RACES = {
    "1": ("Presidency of Bosnia and Herzegovina", "Predsjedništvo Bosne i Hercegovine"),
    "2": (
        "House of Representatives of the Parliamentary Assembly of Bosnia and Herzegovina",
        "Predstavnički dom Parlamentarne skupštine Bosne i Hercegovine",
    ),
    "4": (
        "House of Representatives of the Parliament of the Federation of Bosnia and Herzegovina",
        "Predstavnički dom Parlamenta Federacije Bosne i Hercegovine",
    ),
    "5": ("President of Republika Srpska", "Predsjednik Republike Srpske"),
    "6": ("National Assembly of Republika Srpska", "Narodna skupština Republike Srpske"),
    "7": ("Cantonal assembly", "Skupština kantona"),
    "8": ("Mayor", "Načelnik/gradonačelnik"),
    "9": ("Municipal council", "Općinsko vijeće/skupština opštine"),
}
CANDIDATE_RACES = {"1", "5", "8"}
ENTITY_NAMES = {"1": "Federation of Bosnia and Herzegovina", "2": "Republika Srpska"}
SCHEMES = {  # the schemes this source's areas belong to
    "entity": ("ba.entitet", 1, "administrative", "entity"),
    "municipality": ("ba.opcina", 2, "administrative", "municipality"),
    "constituency": ("ba.izborna_jedinica", 2, "electoral_district", "electoral district"),
}
STATIONS = "ba.station"  # the polling station scheme the Commission's later results already use
BRCKO = "200"
MOSTAR = "199"
MUNICIPALITY_OF = {  # electoral units that are part of a municipality rather than one of their own
    "027": BRCKO,  # Brčko District voters on the Federation's rolls
    "028": BRCKO,  # Brčko District voters on Republika Srpska's rolls
    **{code: MOSTAR for code in ("151", "152", "153", "154", "155", "156", "157")},  # the City of Mostar's areas
}
METHOD = {
    "polling station": (
        "Votes by polling station as the Central Election Commission's certified results site publishes them. The "
        "Commission counts postal ballots, absentee and mobile-team ballots, and tendered (confirmed) ballots "
        "centrally and reports them only for the whole district, by option, so each district has one unit for each "
        "of those channels. The polling station pages print invalid ballots as one number, so invalid ballots here "
        "include blank ones. Registered voters and invalid ballots that the district total holds beyond its "
        "stations' are in one centrally counted unit."
    ),
    "municipality": (
        "Votes by municipality (basic electoral unit) as the Central Election Commission's certified results site "
        "publishes them. Votes that the district total holds beyond its municipalities', which are the postal, "
        "absentee, mobile-team, and tendered ballots the Commission counts centrally, are in one centrally counted "
        "unit for the district."
    ),
    "municipality votes": (
        "Votes by municipality (basic electoral unit) as the Central Election Commission's certified results site "
        "publishes them. The 2006 municipality pages print only each option's votes, so a municipality's valid "
        "ballots here are the sum of its option votes, and the district's invalid ballots, which the site prints only "
        "for the whole district, are in one centrally counted unit together with the postal, absentee, and tendered "
        "ballots the Commission counts centrally."
    ),
}
COMPENSATION = (
    "Compensation seats are allocated over the whole entity from the entity-wide list votes, so this contest is "
    "counted only at the entity level, with the votes and seats the Commission prints for the entity."
)


def package_key(e: Election) -> str:
    return f"{SOURCE}/{e.slug}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(e) for e in ELECTIONS)]


def area(kind: str, code: str) -> str:
    return key("area", SCHEMES[kind][0], code)


def municipality_area(code: str) -> str:
    return area("municipality", MUNICIPALITY_OF.get(code, code))


def title(text: str) -> str:
    return " ".join(w.capitalize() if len(w) > 2 or i == 0 else w.lower() for i, w in enumerate(text.split()))


def contests(e: Election) -> list[Contest]:
    if e.slug == "2012-10-07":
        return contests_2012()
    if e.slug in APPS:
        return contests_2010(APPS[e.slug])
    return contests_2006()


@functools.cache
def everything() -> dict[str, list[Contest]]:
    return {e.slug: contests(e) for e in ELECTIONS}


@functools.cache
def municipality_names() -> dict[str, str]:
    """Each municipality's name as the latest site prints it, from the councils' and assemblies' unit lists."""
    names: dict[str, str] = {}
    for e in ELECTIONS:
        for c in everything()[e.slug]:
            if c.district and c.district[0] == "municipality":
                names[c.district[1]] = c.name
    for e in ELECTIONS:
        for code, name in municipality_lists().get(e.slug, {}).items():
            if code not in MUNICIPALITY_OF:
                names.setdefault(code, name)
    names.setdefault(MOSTAR, "GRAD MOSTAR")
    names.setdefault(BRCKO, "BRČKO DISTRIKT BiH")
    return names


def municipality_lists() -> dict[str, dict[str, str]]:
    return {**{slug: units_2010(start) for slug, start in APPS.items()}, "2006-10-01": units_2006()}


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Centralna izborna komisija Bosne i Hercegovine",
        title="Certified election results, 2006-2014 result sites",
        url="https://www.izbori.ba/Default.aspx?CategoryID=48&Lang=3",
        license="Official election results of Bosnia and Herzegovina, published for public use",
        license_url="https://www.izbori.ba/",
        attribution="Central Election Commission of Bosnia and Herzegovina, izbori.ba",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bosnia and Herzegovina's entities, municipalities, and electoral districts as the Central Election "
        "Commission's 2006-2014 result sites name them",
    )
    p.external |= {BIH, COUNTRY, STATIONS}
    source_row(p)
    for code, rank, kind, noun in SCHEMES.values():
        p.add("scheme", code=code, polity_id=BIH, kind=kind, rank=rank, noun=noun, code_system=None)
    for code, text in ENTITY_NAMES.items():
        id = area("entity", code)
        p.add("area", id=id, scheme_code=SCHEMES["entity"][0])
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, text, "en", "official")
    for code, text in sorted(municipality_names().items()):
        id = area("municipality", code)
        p.add("area", id=id, scheme_code=SCHEMES["municipality"][0])
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, title(text), "bs", "official")
    for code, versions in sorted(district_plans().items()):
        for i, (since, text, race, _) in enumerate(versions):
            id = district_id(code, since if len(versions) > 1 else None)
            until = versions[i + 1][0] - timedelta(days=1) if i + 1 < len(versions) else None
            p.add("area", id=id, scheme_code=SCHEMES["constituency"][0])
            if len(versions) > 1:
                p.add("area_code", area_id=id, code=code, valid_from=since if i else None, valid_to=until)
            else:
                p.add("area_code", area_id=id, code=code)
            p.add("area_parent", area_id=id, parent_id=area("entity", district_entity(race, code)))
            p.name(id, f"{title(text)} ({RACES[race][0]})", "en", "official")
            p.name(id, title(text), "bs", "native", False)
    return p


def district_entity(race: str, code: str) -> str:
    return "2" if race == "6" or (race == "2" and code.startswith("52")) else "1"


def district_id(code: str, since: date | None) -> str:
    return area("constituency", code) if since is None else key("area", SCHEMES["constituency"][0], code, since)


@functools.cache
def district_plans() -> dict[str, list[tuple[date, str, str, frozenset[str]]]]:
    """Each electoral district code's plans: the first election of each set of municipalities it has held, its name,
    and its race. The Commission reused Republika Srpska's codes 301-306 when it redrew six districts as nine in 2014,
    so a code whose municipalities change is a new area from the first election that uses them."""
    out: dict[str, list[tuple[date, str, str, frozenset[str]]]] = {}
    for e in sorted(ELECTIONS, key=lambda e: e.day):
        for c in everything()[e.slug]:
            if not (c.district and c.district[0] == "constituency"):
                continue
            members = frozenset(MUNICIPALITY_OF.get(u.municipality, u.municipality) for u in c.units)
            versions = out.setdefault(c.district[1], [])
            if versions and versions[-1][3] == members:
                versions[-1] = (versions[-1][0], c.name, c.race, members)
            else:
                versions.append((e.day, c.name, c.race, members))
    return out


def district_area(e: Election, c: Contest) -> str:
    if not c.district:
        return COUNTRY
    if c.district[0] != "constituency":
        return area(*c.district)
    versions = district_plans()[c.district[1]]
    since = max(v[0] for v in versions if v[0] <= e.day)
    return district_id(c.district[1], since if len(versions) > 1 else None)


def body(race: str, district: str | None) -> str | None:
    if race in {"2", "4", "6"}:
        return key("body", race)
    if race in {"7", "9"}:
        return key("body", race, district)
    return None


def office(race: str, district: str | None) -> str | None:
    if race in {"1", "5"}:
        return key("office", race)
    if race == "8":
        return key("office", race, district)
    return None


def party(code: str) -> str:
    return key("party", code)


@functools.cache
def list_parties() -> dict[str, Counter[str]]:
    out: dict[str, Counter[str]] = {}
    for e in ELECTIONS:
        for c in everything()[e.slug]:
            if c.race not in CANDIDATE_RACES:
                for o in c.options:
                    if o.code and not o.minority:
                        out.setdefault(o.code, Counter())[o.label] += 1
    return out


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Bosnia and Herzegovina's legislatures, councils, and elected offices, and the parties and lists on the "
        "Central Election Commission's 2006-2014 ballots",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {BIH}
    councils: dict[str, str] = {}
    mayors: dict[str, str] = {}
    cantons: dict[str, str] = {}
    for e in ELECTIONS:
        for c in everything()[e.slug]:
            if c.race == "9" and c.district:
                councils[c.district[1]] = c.name
            if c.race == "8" and c.district:
                mayors[c.district[1]] = c.name
            if c.race == "7" and c.district:
                cantons[c.district[1]] = c.name
    for race, kind in (("2", "lower_house"), ("4", "lower_house"), ("6", "unicameral")):
        p.add("body", id=key("body", race), polity_id=BIH, kind=kind)
        p.name(key("body", race), RACES[race][0], "en", "official")
        p.name(key("body", race), RACES[race][1], "bs", "native", False)
    for code, text in sorted(cantons.items()):
        id = key("body", "7", code)
        p.add("body", id=id, polity_id=BIH, kind="unicameral")
        p.name(id, f"Assembly of {title(text)}", "en", "official")
        p.name(id, f"Skupština {title(text)}", "bs", "native", False)
    for code, text in sorted(councils.items()):
        id = key("body", "9", code)
        p.add("body", id=id, polity_id=BIH, kind="council")
        p.name(id, f"Municipal council of {title(text)}", "en", "official")
    for code, text in sorted(mayors.items()):
        id = key("office", "8", code)
        p.add("office", id=id, polity_id=BIH, kind="mayor")
        p.name(id, f"Mayor of {title(text)}", "en", "official")
    if any(c.race == "1" for cs in everything().values() for c in cs):
        p.add("office", id=key("office", "1"), polity_id=BIH, kind="head_of_state")
        p.name(key("office", "1"), "Member of the Presidency of Bosnia and Herzegovina", "en", "official")
        p.name(key("office", "1"), "Član Predsjedništva Bosne i Hercegovine", "bs", "native", False)
        p.add("office", id=key("office", "5"), polity_id=BIH, kind="other")
        p.name(key("office", "5"), RACES["5"][0], "en", "official")
        p.name(key("office", "5"), RACES["5"][1], "bs", "native", False)
    for code, spellings in sorted(list_parties().items()):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=BIH)
        p.add("external_id", entity_id=id, scheme=f"{SOURCE}.party", value=code)
        best = spellings.most_common(1)[0][0]
        for text in sorted(spellings):
            p.name(id, text, "bs", "official", text == best)
    return p


class Facts:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            name: [] for name in ("result_set_id", "unit_id", "option_id", "channel", "measure", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int, channel: int = 0) -> None:
        row = (result_set, unit, option, channel, measure, int(value))
        for column, item in zip(self.columns.values(), row, strict=True):
            column.append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


def candidate(text: str) -> tuple[str, str | None]:
    """A candidate's name and the party printed after it ("NAME - PARTY")."""
    name, _, rest = text.partition(" - ")
    return name.strip(), rest.strip() or None


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    values: int = 0
    contests: int = 0


def contest_name(c: Contest) -> tuple[str, str]:
    en, bs = RACES[c.race]
    place = title(c.name)
    if c.race == "1":
        entity = ENTITY_NAMES[c.district[1]] if c.district else ""
        return f"{en}, members elected in the {entity}", f"{bs}, {place}"
    if c.race == "5":
        return en, bs
    if c.compensation:
        entity = ENTITY_NAMES[c.district[1]] if c.district else ""
        return f"{en}, compensation seats in the {entity}", f"{bs}, kompenzacijski mandati, {place}"
    if c.race == "8":
        return f"Mayor of {place}", f"{bs} {place}"
    if c.race == "9":
        return f"Municipal council of {place}", f"{bs} {place}"
    if c.race == "7":
        return f"Assembly of {place}", f"Skupština {place}"
    return f"{en}, {place}", f"{bs}, {place}"


def election(e: Election, report: Report) -> Package:
    p = Package(
        package_key(e),
        SOURCE,
        f"The {e.title} from the Central Election Commission's certified results site",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {BIH, STATIONS}
    parties = set(list_parties())
    election_id = key("election", e.slug)
    p.add("election", id=election_id, polity_id=BIH, kind=e.kind, date_from=e.day, date_to=e.day, status="held")
    p.name(election_id, e.title, "en", "official")
    path = f"ba/{e.slug}-{changeset.slug(e.kind)}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    files: set[str] = set()
    values = Facts()
    units: dict[str, str] = {}

    def unit(code: str, kind: str, place: str, relation: str = "within") -> str:
        id = key("unit", e.slug, code)
        if code not in units:
            units[code] = id
            p.add(
                "reporting_unit",
                id=id,
                polity_id=BIH,
                scheme_code=STATIONS,
                code=f"{e.slug}:{code}",
                area_relation=relation,
                area_id=place,
                kind=kind,
            )
        return id

    seen_slugs: set[str] = set()
    people: set[str] = set()
    over: list[str] = []
    for c in everything()[e.slug]:
        files |= set(c.files)
        contest = key("contest", e.slug, c.key)
        result_set = key("result_set", e.slug, c.key)
        en, bs = contest_name(c)
        district = c.district[1] if c.district else None
        place = district_area(e, c)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office" if c.race in CANDIDATE_RACES else "list_allocation",
            office_id=office(c.race, district),
            body_id=body(c.race, district),
            district_area_id=place if c.district else None,
            magnitude=c.magnitude,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, en, "en", "official")
        p.name(contest, bs, "bs", "native", False)
        tail = changeset.slug(en)
        if tail in seen_slugs:
            tail = f"{tail}-{changeset.slug(c.key)}"
        seen_slugs.add(tail)
        p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        method = COMPENSATION if c.compensation else METHOD[c.finest]
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        report.contests += 1
        options: dict[str, str] = {}
        for position, o in enumerate(c.options, 1):
            option = key("option", e.slug, c.key, o.label)
            if o.label in options:
                raise ValueError(f"{e.slug} {c.key}: {o.label} is listed twice")
            options[o.label] = option
            if c.race in CANDIDATE_RACES or o.minority:
                name = c.names.get(o.label) or candidate(o.label)[0]
                linked = party(o.code) if o.code in parties and not o.minority else None
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=linked,
                    ballot_position=position,
                    ballot_label=o.label,
                )
                person = key("person", c.race, district if c.race in {"8", "9"} else "", name)
                if person not in people:
                    people.add(person)
                    p.add("person", id=person)
                    p.name(person, title(name), "bs", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", e.slug, c.key, o.label),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=linked,
                    is_independent=None,
                )
                if o.elected:
                    p.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected",
                        seats=1 if o.minority else None,
                    )
            else:
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=party(o.code) if o.code in parties else None,
                    ballot_position=position,
                    ballot_label=o.label,
                )
                if o.seats:
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=o.seats)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=o.votes)
        for measure, value in sorted(c.declared.items()):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        summed: Counter[str] = Counter()
        accounting: Counter[int] = Counter()
        for u in c.units:
            municipal = c.finest.startswith("municipality")
            relation = "equals" if municipal and u.code not in MUNICIPALITY_OF else "within"
            id = unit(u.code, u.kind, municipality_area(u.municipality) if u.municipality else place, relation)
            if u.accounting.get(BALLOTS, 0) > u.accounting.get(REGISTERED, u.accounting.get(BALLOTS, 0)):
                over.append(f"{u.code} ({en})")
            for measure, value in sorted(u.accounting.items()):
                values.add(result_set, id, None, measure, value)
                accounting[measure] += value
            for text, n in u.votes.items():
                if text not in options:
                    report.problems.append(f"{e.slug} {c.key}: unit {u.code} names {text}, not on the district list")
                    continue
                values.add(result_set, id, options[text], VOTES, n)
                summed[text] += n
        for channel, what, kind in CHANNELS:
            counted = {o.label: o.channels[channel] for o in c.options if o.channels.get(channel)}
            if not counted:
                continue
            id = unit(f"{what}:{c.key}", kind, place)
            for text, n in counted.items():
                values.add(result_set, id, options[text], VOTES, n, channel=channel)
                summed[text] += n
            values.add(result_set, id, None, VALID, sum(counted.values()), channel=channel)
            accounting[VALID] += sum(counted.values())
        rest = {o.label: o.votes - summed[o.label] for o in c.options}
        for text, n in rest.items():
            if n < 0:
                report.problems.append(f"{e.slug} {c.key}: {text} has {-n} more votes in the units than printed")
        central = {m: v - accounting[m] for m, v in c.declared.items()}
        if any(n > 0 for n in rest.values()) or any(n > 0 for n in central.values()):
            id = unit(f"central:{c.key}", "aggregate", place)
            for text, n in rest.items():
                if n > 0:
                    values.add(result_set, id, options[text], VOTES, n)
            for measure, n in sorted(central.items()):
                if n > 0:
                    values.add(result_set, id, None, measure, n)
        for measure, n in central.items():
            if n < 0:
                report.problems.append(f"{e.slug} {c.key}: the units' measure {measure} exceeds the district's by {-n}")
                p.rows["declared"] = [
                    row
                    for row in p.rows["declared"]
                    if not (
                        row["result_set_id"] == result_set and row["option_id"] is None and row["measure"] == measure
                    )
                ]
    cited: set[str] = set()
    for url in sorted(files):
        artifact = raw.cached(SOURCE, url)
        if artifact.storage_key in cited:
            p.uses(artifact)
        else:
            cited.add(artifact.storage_key)
            p.artifact(artifact, SOURCE_ID)
    if over:
        p.accept(
            "accounting.turnout",
            "The Commission's own results print more ballots cast than registered voters at " + "; ".join(over),
        )
    frame = values.frame()
    report.values += frame.height
    p.frame("result_value", frame)
    return p


def totals(e: Election) -> list[str]:
    """Each race's districts summed, for comparison with the race-wide totals the Commission prints."""
    lines = []
    for race in sorted({c.race for c in everything()[e.slug]}):
        parts = [c for c in everything()[e.slug] if c.race == race and not c.compensation]
        valid = sum(c.declared.get(VALID, 0) for c in parts)
        votes = sum(o.votes for c in parts for o in c.options)
        lines.append(f"{e.slug} race {race}: {len(parts)} districts, {valid:,} valid ballots, {votes:,} option votes")
    lines += race_totals(e)
    return lines


def parse(only: str | None = None) -> None:
    if not only:
        geo().write()
        registry().write()
    for e in ELECTIONS:
        if only and e.slug != only:
            continue
        report = Report()
        election(e, report).write()
        for line in totals(e):
            print(f"  {line}", flush=True)
        for line in report.problems[:20]:
            print(f"  {line}", flush=True)
        print(f"{e.slug}: {report.contests} contests, {report.values:,} values, {len(report.problems)} problems")


def fetch() -> None:
    fetch_2012()
    fetch_apps()
    fetch_2006()


def emit() -> None:
    raise SystemExit(f"{SOURCE} builds packages; load them with `ea package load`")
