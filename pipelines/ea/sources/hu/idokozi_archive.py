"""Hungary's local and nationality by-elections held before 2015, from the National Election Office's archive.

The office's archive page (valasztas.hu/archiv1) links one index page per half year of by-elections. Each index
lists the elections with their settlement, county, kind, and reason, and links the protocol of each. From 2003 to
2008 the protocols are HTML pages with every candidate's votes; later ones are scans, so for those years only the
declared winners on the office's day pages are available.
"""

import functools
import html
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from typing import cast

ARCHIVE = "https://www.valasztas.hu/archiv1"
BASE = "https://static.valasztas.hu/idokozionk/idokozionk/"

MONTHS = {
    "január": 1,
    "február": 2,
    "március": 3,
    "április": 4,
    "május": 5,
    "június": 6,
    "július": 7,
    "augusztus": 8,
    "szeptember": 9,
    "szepetember": 9,
    "október": 10,
    "november": 11,
    "december": 12,
}


def decode(content: bytes) -> str:
    """The text of a page, which the office served as UTF-8 or as Windows Central European."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1250", errors="replace")


def plain(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).replace("\xa0", " ").strip()


@dataclass
class Cell:
    text: str
    links: list[str]
    rowspan: int
    colspan: int
    parts: list[str]


def cells_of(row: str) -> list[Cell]:
    found: list[Cell] = []
    for match in re.finditer(r"<t[dh]\b([^>]*)>(.*?)(?=<t[dh]\b|</tr|$)", row, flags=re.DOTALL | re.IGNORECASE):
        attributes, body = match[1], match[2]
        span = re.search(r"rowspan\s*=\s*\"?(\d+)", attributes, flags=re.IGNORECASE)
        wide = re.search(r"colspan\s*=\s*\"?(\d+)", attributes, flags=re.IGNORECASE)
        found.append(
            Cell(
                plain(body),
                [
                    html.unescape(link).strip()
                    for link in re.findall(r"href\s*=\s*\"([^\"]*)\"", body, flags=re.IGNORECASE)
                ],
                int(span[1]) if span else 1,
                int(wide[1]) if wide else 1,
                [
                    t
                    for t in (plain(x) for x in re.split(r"<br\s*/?>|</?p\b[^>]*>|</div>", body, flags=re.IGNORECASE))
                    if t
                ],
            )
        )
    return found


def grid(page: str) -> list[list[Cell]]:
    """The table rows of a page with row-spanned cells repeated down the rows they cover."""
    carried: dict[int, tuple[Cell, int]] = {}
    out: list[list[Cell]] = []
    for row in re.findall(r"<tr\b.*?(?=<tr\b|</table|$)", page, flags=re.DOTALL | re.IGNORECASE):
        own = cells_of(row)
        line: list[Cell] = []
        column = 0
        queue = list(own)
        while queue or column in carried:
            if column in carried:
                cell, left = carried[column]
                line.append(cell)
                if left <= 1:
                    del carried[column]
                else:
                    carried[column] = (cell, left - 1)
                column += 1
                continue
            cell = queue.pop(0)
            for offset in range(cell.colspan):
                line.append(cell)
                if cell.rowspan > 1:
                    carried[column + offset] = (cell, cell.rowspan - 1)
            column += cell.colspan
        out.append(line)
    return out


COUNTIES = {
    "bács-kiskun": "03",
    "bács-k": "03",
    "bács": "03",
    "baranya": "02",
    "békés": "04",
    "borsod-abaúj-zemplén": "05",
    "borsod-a-z": "05",
    "borsod": "05",
    "borosd": "05",
    "baz": "05",
    "budapest": "01",
    "főváros": "01",
    "csongrád": "06",
    "fejér": "07",
    "győr-moson-sopron": "08",
    "győr-m-s": "08",
    "győr": "08",
    "hajdú-bihar": "09",
    "hajdú-b": "09",
    "hajdú": "09",
    "heves": "10",
    "jász-nagykun-szolnok": "11",
    "jász-n-sz": "11",
    "jász": "11",
    "szolnok": "11",
    "komárom-esztergom": "12",
    "komárom-e": "12",
    "komárom": "12",
    "nógrád": "13",
    "pest": "14",
    "somogy": "15",
    "szabolcs-szatmár-bereg": "16",
    "szabolcs-sz-b": "16",
    "szabolcs": "16",
    "tolna": "17",
    "vas": "18",
    "veszprém": "19",
    "zala": "20",
}


def county_code(text: str) -> str | None:
    """The office's county code for a county as an index page writes it, in full or abbreviated."""
    low = re.sub(r"[\s.*]", "", text.lower())
    if low in COUNTIES:
        return COUNTIES[low]
    if len(low) >= 7:
        return next((code for full, code in COUNTIES.items() if len(full) > 9 and full.startswith(low)), None)
    return None


@dataclass
class Entry:
    """One by-election as an index page lists it."""

    day: date
    place: str
    county: str
    kind: str
    ward: str | None
    seats: int | None
    reason: str
    protocol: str | None
    daypage: str | None
    index: str
    status: str = ""
    electorate: dict[str, int | None] = field(default_factory=dict)
    candidates: list[tuple[str, str, int | None, bool]] = field(default_factory=list)


MONTH_PREFIX = {
    "jan": 1,
    "feb": 2,
    "már": 3,
    "ápr": 4,
    "máj": 5,
    "jún": 6,
    "júl": 7,
    "aug": 8,
    "sze": 9,
    "okt": 10,
    "nov": 11,
    "dec": 12,
}


def parse_day(text: str, year: int) -> date | None:
    """A printed election date such as `április 27.`, `jún. 9.`, or `04.25.`."""
    low = text.lower().strip()
    numeric = re.fullmatch(r"(\d\d)\s*\.\s*(\d\d)\s*\.?", low)
    if numeric:
        return date(year, int(numeric[1]), int(numeric[2]))
    found = re.fullmatch(r"(?:(\d{4})\.\s*)?([^\W\d_]+)\.?\s*(\d+)[\s.]*", low)
    if found is None or found[2][:3] not in MONTH_PREFIX:
        return None
    return date(year, MONTH_PREFIX[found[2][:3]], int(found[3]))


def classify(text: str) -> tuple[str, str | None, int | None] | None:
    """The kind of election from the index's type cell: its role, ward number, and seats."""
    low = text.lower()
    seats = re.search(r"\((\d+)\s*fő\)", low)
    count = int(seats[1]) if seats else None
    if "polgármester" in low and "testület" not in low:
        return "mayor", None, None
    if "testület" in low:
        return "council", None, count
    low = re.sub(r"e\.\s*v\.\s*k\.?", "evk", low)
    ward = re.match(r"(\d+)\s*\.?\s*evk", low)
    if ward:
        return "ward", str(int(ward[1])), None
    if "evk" in low:
        found = re.search(r"(\d+)\s*\.?\s*evk", low)
        return "ward", str(int(found[1])) if found else None, None
    if low.strip(" ,") == "képviselő":
        return "council", None, None
    if "kislista" in low or "egyéni lista" in low or "kompenzációs" in low:
        return "list", None, count
    if "kis" in low and "önk" in low or "nemzetiségi" in low or "kisebbségi" in low:
        return "minority", None, None
    return None


def entries(page: str, year: int, index: str) -> list[Entry]:
    """Every election one index page lists, in the order the page prints them."""
    found: list[Entry] = []
    for row in grid(page):
        if len(row) < 4:
            continue
        body = row[1:] if re.fullmatch(r"\d+\s*\.*", row[0].text) else row
        day = kind = None
        protocol = daypage = None
        pending: list[Cell] = []
        for cell in body:
            text = cell.text
            if day is None and len(text) < 24 and parse_day(text, year) is not None:
                day = parse_day(text, year)
                daypage = cell.links[0] if cell.links else None
            elif kind is None and classify(text) is not None:
                kind = text
                protocol = cell.links[0] if cell.links else None
            else:
                pending.append(cell)
        if day is None or kind is None:
            continue
        pending = [c for c in pending if c.text]
        if not pending:
            continue
        place = pending[0].text
        county = pending[1].text if len(pending) > 1 and county_code(pending[1].text) is not None else None
        if county is None:
            county = next((c.text for c in pending[1:] if county_code(c.text) is not None), None)
        rest = [c for c in pending[1:] if c.text != county]
        reason = rest[-1].text if rest else ""
        role = classify(kind)
        assert role is not None
        links = next((c.links for c in body if c.text == kind), [])
        name = (place or "").rstrip("* ")
        code = county_code(county or "") or ""
        if "polgármester" in kind.lower() and "testület" in kind.lower():
            found.append(
                Entry(day, name, code, "mayor", None, None, reason or "", links[0] if links else None, daypage, index)
            )
            found.append(
                Entry(
                    day,
                    name,
                    code,
                    "council",
                    None,
                    role[2],
                    reason or "",
                    links[1] if len(links) > 1 else None,
                    daypage,
                    index,
                )
            )
            continue
        found.append(Entry(day, name, code, role[0], role[1], role[2], reason or "", protocol, daypage, index))
    return found


def absolute(link: str, index_url: str) -> str:
    return urllib.parse.urljoin(index_url, link)


# ---------------------------------------------------------------------------------------------------------------
# Protocol pages: the office's per-election final data, with every candidate's votes


@dataclass
class Block:
    """One election's protocol: the roll, the turnout, and the candidates' votes."""

    role: str
    place: str
    ward: str | None
    nation: str | None
    day: date | None
    roll: dict[str, int]
    candidates: list[tuple[str, str, int, bool]]
    status: str


def number(text: str) -> int | None:
    if text.strip() == "-":
        return 0
    found = re.fullmatch(r"([-+]?)\s*(\d[\d\s]*)", text.replace("\xa0", " ").strip())
    return int(found[1].replace("+", "") + re.sub(r"\s", "", found[2])) if found else None


def tokens_of(page: str) -> list[str]:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<[^>]+>", "|", body)
    return [
        t for t in (re.sub(r"\s+", " ", html.unescape(t)).replace("\xa0", " ").strip() for t in body.split("|")) if t
    ]


def block_kind(heading: str) -> tuple[str, str, str | None, str | None] | None:
    """The role, place, ward, and nationality from a protocol heading."""
    ward = re.search(r"^(.*?)\s+(\d+)\.\s*számú egyéni", heading)
    if ward:
        return "ward", ward[1].strip(), str(int(ward[2])), None
    mayor = re.search(r"^(.*?)\s+polgármester választás", heading)
    if mayor:
        return "mayor", mayor[1].strip(), None, None
    small = re.search(r"^(.*?)\s+kislistás választás", heading)
    if small:
        return "council", small[1].strip(), None, None
    minority = re.search(r"^(.*?)\s+(\S+)\s+kisebbségi önkormányzat választás", heading)
    if minority:
        return "minority", minority[1].strip(), None, minority[2].strip()
    council = re.search(r"^(.*?)\s+(?:képviselő-testületi|önkormányzati képviselő) választás", heading)
    if council:
        return "council", council[1].strip(), None, None
    return None


def protocol_blocks(page: str) -> list[Block]:
    """Every election a protocol page reports, in the order it prints them."""
    tokens = tokens_of(page)
    starts = [i for i, t in enumerate(tokens) if t.startswith("Végleges adatok")]
    blocks: list[Block] = []
    day = None
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(tokens)
        # The date line precedes the first heading, or the later ones.
        before = tokens[max(0, start - 3) : start]
        for i, t in enumerate(before):
            if t.startswith("Időközi önkormányzati választás") and i + 1 < len(before):
                found = re.match(r"(\d{4})\. (\d+)\. (\d+)", before[i + 1])
                if found:
                    day = date(int(found[1]), int(found[2]), int(found[3]))
        part = tokens[start:end]
        heading_end = next((i for i, t in enumerate(part) if "eredményéről" in t), None)
        if heading_end is None:
            continue
        heading = re.sub(r"\s+", " ", " ".join(part[: heading_end + 1]).replace("Végleges adatok", "", 1))
        heading = heading.replace(" eredményéről", "").strip()
        kind = block_kind(heading)
        if kind is None:
            continue
        runs: list[list[int]] = []
        i = heading_end
        while i < len(part):
            if re.fullmatch(r"[A-K]", part[i]):
                width = 0
                while i < len(part) and re.fullmatch(r"[A-K]", part[i]):
                    width += 1
                    i += 1
                values = [number(t) for t in part[i : i + width]]
                if all(v is not None for v in values):
                    runs.append(cast(list[int], values))
                i += width
            else:
                i += 1
        roll: dict[str, int] = {}
        if len(runs) >= 2:
            electorate, voting = runs[0], runs[1]
            roll["registered"] = electorate[-2] if len(electorate) in (4, 5) else electorate[-1]
            roll["refused"] = electorate[-1]
            roll["voters"], roll["cast"], roll["invalid"] = voting[0], voting[1], voting[3]
            roll["valid"] = voting[4]
            if len(voting) == 6:
                roll["votes"] = voting[5]
        candidates: list[tuple[str, str, int, bool]] = []
        table = next((i for i, t in enumerate(part) if t == "%"), None)
        status = ""
        if table is not None:
            rest = part[table + 1 :]
            expected = 1
            i = 0
            percent = re.compile(r"\d+[,.]\d+[\s,.]*%?|\d+ ?%|100|0")
            while i < len(rest):
                start = i + 1 if rest[i].rstrip(".") == str(expected) else i
                for j in range(start + 2, len(rest)):
                    if percent.fullmatch(rest[j]) and number(rest[j - 1]) is not None:
                        after = j + 1
                        note = ""
                        if after < len(rest) and "ompenzációs listán megválasztott" in rest[after]:
                            note = rest[after]
                            after += 1
                        candidates.append(
                            (
                                rest[start],
                                " ".join(rest[start + 1 : j - 1]),
                                cast(int, number(rest[j - 1])),
                                bool(note),
                            )
                        )
                        expected += 1
                        i = after
                        break
                else:
                    break
            status = " ".join(rest[i:])
        blocks.append(Block(kind[0], kind[1], kind[2], kind[3], day, roll, candidates, status))
    return blocks


# ---------------------------------------------------------------------------------------------------------------
# Day pages: the declared winners


@dataclass
class Row:
    """One line of a day page: an election, a declared winner, and who nominated them."""

    place: str
    county: str
    kind: str
    name: str
    nominator: str
    figures: list[int]


def day_rows(page: str) -> list[Row]:
    """The declared winners of a day page, one row for each person."""
    rows: list[Row] = []
    for line in grid(page):
        if len(line) < 5:
            continue
        start = 1 if re.fullmatch(r"\d+\s*\.*", line[0].text) else 0
        cells = line[start:]
        found = next((i for i, c in enumerate(cells) if i >= 2 and classify(c.text) is not None), None)
        if found is None or county_code(cells[found - 1].text) is None:
            continue
        county_at = found - 1
        place = cells[county_at - 1].text
        after = cells[found + 1 :]
        # Skip a printed date and then take the figures, the winner, and the nominating organization.
        while after and (parse_day(after[0].text, 2000) is not None or re.fullmatch(r"\d\d\.\d\d\.?", after[0].text)):
            after = after[1:]
        figures: list[int] = []
        while after and re.fullmatch(r"[\d\s]+(?:\s*(?:fő|db))?", after[0].text) and len(figures) < 4:
            figures.append(cast(int, number(re.sub(r"\s*(fő|db)$", "", after[0].text))))
            after = after[1:]
        if not after:
            continue
        role = classify(cells[found].text)
        names = after[0].parts or [after[0].text]
        nominators = after[1].parts if len(after) > 1 else []
        if role is not None and role[0] in ("council", "list", "minority") and len(names) > 1:
            if len(nominators) == len(names):
                pairs = list(zip(names, nominators, strict=True))
            else:
                shared = re.sub(r"^\d+\s*fő\s*", "", nominators[0]) if len(nominators) == 1 else ""
                pairs = [(n, shared) for n in names]
        else:
            pairs = [(" ".join(names), " ".join(nominators))]
        for name, nominator in pairs:
            rows.append(
                Row(
                    place.rstrip("* "),
                    cells[county_at].text,
                    cells[found].text,
                    name,
                    nominator,
                    figures,
                )
            )
    return rows


# ---------------------------------------------------------------------------------------------------------------
# Fetching and merging

SOURCE = "hu.idokozi"
LEGACY = {"valido1.htm": 1999, "valido2.htm": 2000, "iov01.htm": 2001, "idokozi2002.htm": 2002}
NOTE = re.compile(
    r"eredménytelen|érvénytelen|bíróság|választás|\btagú?\b|testület|\bfő\b|jelölt|szavazat|elmarad|megismétl|mind ",
    re.IGNORECASE,
)


def index_urls() -> list[tuple[str, int]]:
    """Every index page the archive links, with the year it covers."""
    from ea import raw

    page = decode(raw.cached(SOURCE, ARCHIVE).path.read_bytes())
    found: dict[str, int] = {}
    for link in re.findall(r"href=\"(https://static\.valasztas\.hu/idokozionk/idokozionk/[^\"]+\.html?)\"", page):
        name = link.rsplit("/", 1)[-1]
        year = LEGACY.get(name)
        if year is None and re.match(r"20\d\d", name):
            year = int(name[:4])
        if year is not None:
            found[link] = year
    found[BASE + "valido1.htm"] = 1999
    return sorted(found.items(), key=lambda item: (item[1], item[0]))


def get(url: str) -> bytes | None:
    import httpx

    from ea import raw

    for _ in range(5):
        try:
            response = httpx.get(url, headers={"User-Agent": raw.USER_AGENT}, timeout=60, follow_redirects=True)
        except httpx.TransportError:
            continue
        if response.status_code == 404:
            return None
        if response.status_code >= 500:
            continue
        response.raise_for_status()
        if "Hibakód: 404" in decode(response.content[:6000]):
            return None
        return response.content
    raise RuntimeError(f"{url} failed five times")


@functools.cache
def all_entries() -> list[Entry]:
    """The elections of every index page, the chronological and settlement orderings merged, with absolute links."""
    from ea import raw

    merged: dict[tuple[date, str, str, str | None, str], Entry] = {}
    for url, year in index_urls():
        entries_ = entries(decode(raw.cached(SOURCE, url).path.read_bytes()), year, url)
        for entry in entries_:
            entry.protocol = absolute(entry.protocol, url) if entry.protocol else None
            entry.daypage = absolute(entry.daypage, url) if entry.daypage else None
            key = (entry.day, fold_place(entry.place), entry.kind, entry.ward, entry.index.rsplit("/", 1)[-1][:4])
            old = merged.get(key)
            if old is None or (old.protocol is None and entry.protocol is not None):
                merged[key] = entry
    return sorted(merged.values(), key=lambda e: (e.day, e.place, e.kind, e.ward or ""))


def fold_place(name: str) -> str:
    from ea.sources.hu import valasztas as hv

    return hv.fold(re.sub(r"\s*\(.*\)", "", name))


def crawl_key(day: date) -> str:
    return f"{BASE}{day}#crawl"


def relative(url: str) -> str:
    return url.removeprefix(BASE)


def fetch() -> None:
    import io
    import zipfile
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime

    from ea import raw

    raw.fetch(SOURCE, ARCHIVE, suffix=".html")
    for url, _ in index_urls():
        raw.fetch(SOURCE, url, suffix=".html")
    by_day: dict[date, set[str]] = defaultdict(set)
    for entry in all_entries():
        for link in (entry.protocol, entry.daypage):
            if link and not link.lower().endswith((".pdf", ".docx", ".doc")):
                by_day[entry.day].add(link)

    def crawl_day(item: tuple[date, set[str]]) -> None:
        day, links = item
        if crawl_key(day) in raw.keys(SOURCE):
            return
        files: dict[str, bytes] = {}
        for link in sorted(links):
            content = get(link)
            if content is not None:
                files[relative(link)] = content
        if not files:
            return
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
        raw.store(
            SOURCE,
            crawl_key(day),
            buffer.getvalue(),
            suffix=".zip",
            retrieved_at=datetime.now(UTC),
            media_type="application/zip",
        )

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(crawl_day, sorted(by_day.items())))
    print(f"{sum(len(v) for v in by_day.values())} pages on {len(by_day)} dates")


# ---------------------------------------------------------------------------------------------------------------
# One election's merged record

NATIONS = {
    "cigány": ("Roma", "Cigány"),
    "német": ("German", "Német"),
    "lengyel": ("Polish", "Lengyel"),
    "horvát": ("Croatian", "Horvát"),
    "szlovák": ("Slovak", "Szlovák"),
    "román": ("Romanian", "Román"),
    "szerb": ("Serb", "Szerb"),
    "szlovén": ("Slovene", "Szlovén"),
    "görög": ("Greek", "Görög"),
    "bolgár": ("Bulgarian", "Bolgár"),
    "örmény": ("Armenian", "Örmény"),
    "ruszin": ("Rusyn", "Ruszin"),
    "ukrán": ("Ukrainian", "Ukrán"),
}


@dataclass
class Candidate:
    name: str
    nominator: str
    votes: int | None
    elected: bool


@dataclass
class Result:
    """One election at one settlement: its roll and turnout, candidates, and the winners the office declared."""

    day: date
    role: str
    place: str
    maz: str
    ward: str | None
    nation: str | None
    seats: int | None
    reason: str
    roll: dict[str, int]
    candidates: list[Candidate]
    winners: list[tuple[str, str]]
    status: str
    sources: list[str]
    multiple: bool = False
    scan: str | None = None


def nation_of(kind: str) -> tuple[str, str] | None:
    word = kind.lower().strip().split()[0] if kind.strip() else ""
    for prefix, names in NATIONS.items():
        if word.startswith(prefix):
            return names
    return None


def results(day: date, files: dict[str, bytes]) -> list[Result]:
    """Every election of one date: protocol blocks where the office published them, else the day page's winners."""
    from ea.sources.hu import valasztas as hv

    todays = [e for e in all_entries() if e.day == day]
    pages: dict[str, list[Row]] = {}
    out: list[Result] = []
    seen_blocks: set[tuple[str, int]] = set()
    covered: set[tuple[str, str, str | None]] = set()
    for entry in todays:
        sources = [entry.index]
        if entry.daypage and relative(entry.daypage) in files:
            rows = pages.get(entry.daypage)
            if rows is None:
                rows = pages[entry.daypage] = day_rows(decode(files[relative(entry.daypage)]))
            sources.append(entry.daypage)
        link = entry.protocol
        blocks: list[Block] = []
        if link and relative(link) in files:
            blocks = protocol_blocks(decode(files[relative(link)]))
            sources.append(link)
        made = False
        for n, block in enumerate(blocks):
            if (link or "", n) in seen_blocks:
                continue
            seen_blocks.add((link or "", n))
            nation = nation_of(block.nation or "") if block.role == "minority" else None
            out.append(
                Result(
                    day,
                    block.role,
                    entry.place,
                    entry.county,
                    block.ward,
                    nation[1] if nation else None,
                    entry.seats,
                    entry.reason,
                    block.roll,
                    [Candidate(name, nominator, votes, flag) for name, nominator, votes, flag in block.candidates],
                    [],
                    block.status,
                    sources,
                    multiple=len(blocks) > 1,
                )
            )
            covered.add((fold_place(entry.place), block.role, block.ward))
            made = True
        if (
            not made
            and (fold_place(entry.place), "council" if entry.kind == "list" else entry.kind, entry.ward) not in covered
        ):
            nation = nation_of(entry.kind) if entry.kind == "minority" else None
            role = "council" if entry.kind == "list" else entry.kind
            out.append(
                Result(
                    day,
                    role,
                    entry.place,
                    entry.county,
                    entry.ward,
                    nation[1] if nation else None,
                    entry.seats,
                    entry.reason,
                    {},
                    [],
                    [],
                    "",
                    sources,
                    scan=link if link and link.lower().endswith(".pdf") else None,
                )
            )
            covered.add((fold_place(entry.place), role, entry.ward))
    # Declared winners from the day pages.
    for url, rows in pages.items():
        by_key: dict[tuple[str, str, str | None], list[Row]] = {}
        for row in rows:
            kind = classify(row.kind)
            if kind is None:
                continue
            role = "council" if kind[0] == "list" else kind[0]
            by_key.setdefault((fold_place(row.place), role, kind[1]), []).append(row)
        for (place, role, ward), group in list(by_key.items()):
            mayor = by_key.get((place, "mayor", None))
            if (
                role == "council"
                and mayor
                and [(r.name, r.figures) for r in group] == [(r.name, r.figures) for r in mayor]
            ):
                group = [Row(r.place, r.county, r.kind, "", "", []) for r in group[:1]]
                by_key[(place, role, ward)] = group
        for (place, role, ward), group in by_key.items():
            targets = [
                r for r in out if fold_place(r.place) == place and r.role == role and (r.ward == ward or not ward)
            ]
            if role == "ward" and ward is None:
                targets = [r for r in out if fold_place(r.place) == place and r.role == "ward"][:1]
            if not targets:
                first = group[0]
                nation = nation_of(first.kind) if role == "minority" else None
                result = Result(
                    day,
                    role,
                    first.place,
                    county_code(first.county) or "",
                    ward,
                    nation[1] if nation else None,
                    (classify(first.kind) or ("", None, None))[2],
                    "",
                    {},
                    [],
                    [],
                    "",
                    [url],
                )
                out.append(result)
                targets = [result]
                if role == "council":
                    out[:] = [
                        r
                        for r in out
                        if not (
                            r.role == "ward" and fold_place(r.place) == place and not r.candidates and not r.winners
                        )
                    ]
            for result in targets[:1] if not ward or len(targets) == 1 else targets:
                if result.winners or result.status.startswith("declared"):
                    continue
                notes: list[str] = []
                for row in group:
                    if not row.name.strip():
                        continue
                    if NOTE.search(row.name) and not re.search(r"\b(dr|id|ifj)\.", row.name.lower()):
                        notes.append(row.name)
                        found = re.search(r"(\d+)\s*tagú", row.name)
                        if found:
                            result.seats = int(found[1])
                    else:
                        result.winners.append((row.name, row.nominator))
                    if row.figures and not result.roll and len(row.figures) == 4:
                        result.roll = {
                            "registered": row.figures[1],
                            "voters": row.figures[2],
                            "valid": row.figures[3],
                        }
                if notes:
                    result.status = (result.status + " " + "; ".join(notes)).strip()
                if url not in result.sources:
                    result.sources.append(url)
                if len(group) > 1 and role in ("council", "minority"):
                    result.seats = result.seats or len(result.winners)
    _ = hv
    return out
