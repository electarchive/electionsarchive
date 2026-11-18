"""New Zealand district health board elections, 2013, 2016, and 2019: the seven members each board elected by single
transferable vote at the triennial local elections, from the declarations of result and the STV iteration reports of the
electoral officers acting for the boards, checked against the Local Authority Election Statistics.

The authority publishes no board-by-board file, so each board's declaration comes from the electoral officer's own
page or from the report the officer released. A board whose declaration is not in the cache is left out."""

import csv
import functools
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from html import unescape
from pathlib import Path
from typing import Any
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "nz.dhb"
NEW_ZEALAND = str(uuid5(package.NAMESPACE, "polity\x1fNZ"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNZL"))
SCHEME = "nz.health_district"
STATS_PAGE = "https://www.mcert.govt.nz/our-work/local-government/local-elections-and-voting/local-authority-election-statistics/"
STATS_ROOT = (
    "https://www.mcert.govt.nz/assets/Uploads/Local-government-documents/Local-elections-and-voting/"
    "Local-election-statistics/"
)
STATS_URLS = {
    2013: STATS_ROOT + "2013/2013-Local-Authority-Election-Data.csv",
    2016: STATS_ROOT + "2016/2016-Local-Authority-Elections-Results.csv",
    2019: STATS_ROOT + "2019/2019-local-elections-statistics.csv",
}
DAYS = {2013: date(2013, 10, 12), 2016: date(2016, 10, 8), 2019: date(2019, 10, 12)}
YEARS = sorted(DAYS)


@dataclass(frozen=True)
class Board:
    key: str
    name: str
    since: int  # the year of the first election it held
    until: int  # the year of the last election it held


def b(key: str, name: str, since: int = 2001, until: int = 2019) -> Board:
    return Board(key, f"{name} District Health Board", since, until)


BOARDS = [
    b("northland", "Northland"),
    b("waitemata", "Waitemata"),
    b("auckland", "Auckland"),
    b("counties-manukau", "Counties Manukau"),
    b("waikato", "Waikato"),
    b("lakes", "Lakes"),
    b("bay-of-plenty", "Bay of Plenty"),
    b("tairawhiti", "Tairāwhiti"),
    b("taranaki", "Taranaki"),
    b("hawkes-bay", "Hawke's Bay"),
    b("whanganui", "Whanganui"),
    b("midcentral", "MidCentral"),
    b("hutt-valley", "Hutt Valley"),
    b("wairarapa", "Wairarapa"),
    b("capital-coast", "Capital & Coast"),
    b("nelson-marlborough", "Nelson Marlborough"),
    b("west-coast", "West Coast"),
    b("canterbury", "Canterbury"),
    b("south-canterbury", "South Canterbury"),
    b("otago", "Otago", 2001, 2007),
    b("southland", "Southland", 2001, 2007),
    b("southern", "Southern", 2010, 2019),
]
BY_KEY = {board.key: board for board in BOARDS}


def norm(text: str) -> str:
    """A name reduced to its letters, so a board's name matches across the files that print it differently."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace("&", "")
    return re.sub(r"[^a-z]+", "", plain)


# ---- where each declaration lives -------------------------------------------------------------------------------

FYI = "https://fyi.org.nz/request/"
WAYBACK = "https://web.archive.org/web/"
VOTE_AKL = "https://voteauckland.co.nz/content/dam/elections/docs/results/2019/2019-local-elections-results-DHBs.pdf"
WELLINGTON = "https://wellington.govt.nz/"


@dataclass(frozen=True)
class Entry:
    board: str
    year: int
    kind: str  # the layout of the primary file
    url: str
    suffix: str
    publisher: str
    also: tuple[tuple[str, str, str], ...] = ()  # more files of the same declaration: (kind, url, suffix)


ENTRIES = [
    Entry(
        "canterbury",
        2013,
        "declaration",
        WAYBACK + "2019id_/https://edu.cdhb.health.nz/About-CDHB/Who-We-Are/Governance/Documents/"
        "Canterbury%20District%20Health%20Board%202013%20Triennial%20Elections%20-%20Final%20STV%20Result.pdf",
        ".pdf",
        "Canterbury District Health Board, Electoral Officer",
    ),
    Entry(
        "canterbury",
        2016,
        "meek",
        FYI + "4833/response/15831/attach/7/CDHB%20Appendix%201.pdf",
        ".pdf",
        "Canterbury District Health Board, Electoral Officer",
    ),
    Entry(
        "capital-coast",
        2016,
        "meek",
        WAYBACK
        + "20240704043459id_/"
        + WELLINGTON
        + "-/media/your-council/elections/2016/files/ccdhb-2016-iteration.pdf",
        ".pdf",
        "Capital & Coast District Health Board, Electoral Officer",
        (
            (
                "declaration",
                WELLINGTON + "-/media/your-council/elections/2016/files/"
                "capital-coast-district-health-board-2016-triennial-elections-final-stv-result.pdf",
                ".pdf",
            ),
        ),
    ),
    Entry(
        "hutt-valley",
        2016,
        "meek",
        FYI + "4853/response/15607/attach/5/Hutt%20Valley%20District%20Health%20Board%202016%20Triennial%20Elections"
        "%20Final%20STV%20Result%20Report.doc",
        ".doc",
        "Hutt Valley District Health Board, Electoral Officer",
    ),
    Entry(
        "lakes",
        2016,
        "meek",
        FYI + "4855/response/15586/attach/2/Lakes%20District%20Health%20Board%202016%20Triennial%20Elections%20Final"
        "%20STV%20Result%20Report.pdf",
        ".pdf",
        "Lakes District Health Board, Electoral Officer",
    ),
    Entry(
        "nelson-marlborough",
        2016,
        "meek",
        WAYBACK + "20240716155054id_/https://www.nmdhb.govt.nz/assets/Uploads/"
        "Nelson-Marlborough-District-Health-Board-2016-Triennial-Elections-Final-STV-Result-Report.pdf",
        ".pdf",
        "Nelson Marlborough District Health Board, Electoral Officer",
    ),
    Entry(
        "taranaki",
        2016,
        "table",
        FYI + "4860/response/15546/attach/2/Copy%20of%20TarDHB%20Final.pdf",
        ".pdf",
        "Taranaki District Health Board, Electoral Officer",
    ),
    Entry(
        "tairawhiti",
        2016,
        "table",
        FYI + "4861/response/15818/attach/2/Final%20Results%202016.pdf",
        ".pdf",
        "Tairāwhiti District Health Board, Electoral Officer",
    ),
    Entry(
        "waikato",
        2016,
        "meek",
        FYI + "4854/response/15518/attach/2/Waikato%20District%20Health%20Board%202016%20Triennial%20Elections%20Final"
        "%20STV%20Result%20Report.pdf",
        ".pdf",
        "Waikato District Health Board, Electoral Officer",
    ),
    Entry(
        "west-coast",
        2016,
        "meek",
        FYI + "4833/response/15831/attach/6/WCDHB%20Appendix%202.pdf",
        ".pdf",
        "West Coast District Health Board, Electoral Officer",
    ),
    Entry(
        "whanganui",
        2016,
        "table",
        "https://www.whanganui.govt.nz/files/assets/public/v/1/elections/2016/final_stv_result_dhb.pdf",
        ".pdf",
        "Whanganui District Council, Electoral Officer",
    ),
    Entry("auckland", 2019, "auckland", VOTE_AKL, ".pdf", "Auckland Council, Electoral Officer"),
    Entry("counties-manukau", 2019, "auckland", VOTE_AKL, ".pdf", "Auckland Council, Electoral Officer"),
    Entry("waitemata", 2019, "auckland", VOTE_AKL, ".pdf", "Auckland Council, Electoral Officer"),
    Entry(
        "capital-coast",
        2019,
        "wellington-html",
        WELLINGTON + "your-council/elections/past-election-results/local-elections-2019/results/"
        "capital-coast-district-health-board",
        ".html",
        "Wellington City Council, Electoral Officer",
    ),
    Entry(
        "hutt-valley",
        2019,
        "hutt-html",
        WAYBACK + "20191113031042id_/http://www.huttvalleydhb.org.nz/media-and-latest-news/latest-news/"
        "2019-10-17-hutt-valley-dhb-2019-final-election-results/",
        ".html",
        "Hutt Valley District Health Board, Electoral Officer",
    ),
    Entry(
        "whanganui",
        2019,
        "whanganui-html",
        "https://www.whanganui.govt.nz/Your-Council/Local-Elections/Past-Elections/Election-2019/"
        "Final-Results-of-Whanganui-District-Health-Board-Elections-2019",
        ".html",
        "Whanganui District Council, Electoral Officer",
    ),
]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/dhb-{DAYS[y]}" for y in YEARS)]


def files() -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for entry in ENTRIES:
        seen[entry.url] = entry.suffix
        for _, url, suffix in entry.also:
            seen[url] = suffix
    for url in STATS_URLS.values():
        seen[url] = ".csv"
    return list(seen.items())


def fetch() -> None:
    for url, suffix in files():
        raw.fetch(SOURCE, url, suffix=suffix)


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        election(year).write()


# ---- reading the files ------------------------------------------------------------------------------------------

NUM = r"[\d,]+(?:\.\d+)?"


@dataclass
class Candidate:
    name: str
    party: str | None = None
    status: str | None = None
    counts: dict[int, Decimal] = field(default_factory=dict)  # votes at the end of each iteration the file prints


@dataclass
class Result:
    candidates: list[Candidate] = field(default_factory=list)
    informal: int | None = None
    blank: int | None = None
    papers: int | None = None  # the voting documents returned, valid or not
    valid: int | None = None  # the valid voting papers
    first_quota: Decimal | None = None
    last_quota: Decimal | None = None
    files: list[str] = field(default_factory=list)  # every cached file the result reads, the primary first


def number(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def tokens(name: str) -> frozenset[str]:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return frozenset(t for t in re.split(r"[^\w'-]+", name.casefold().replace("’", "'")) if t)


def pdf_lines(url: str) -> list[str]:
    path = raw.cached(SOURCE, url).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def doc_lines(url: str) -> list[str]:
    path = raw.cached(SOURCE, url).path
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "report.doc"
        shutil.copy(path, source)
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "txt:Text", "--outdir", tmp, str(source)],
            capture_output=True,
            check=True,
            timeout=300,
        )
        return (Path(tmp) / "report.txt").read_text(encoding="utf-8").splitlines()


def html_lines(url: str) -> list[str]:
    html = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.DOTALL)
    html = re.sub(r"</(p|div|tr|li|h\d|table|td|th)>|<br\s*/?>", "\n", html)
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    return [line for line in (re.sub(r"[ \t\xa0]+", " ", x).strip() for x in text.splitlines()) if line]


def clean(name: str) -> str:
    return " ".join(unicodedata.normalize("NFC", name).replace("’", "'").split())


def meek(lines: list[str]) -> Result:
    """The STV iteration report: a table of each candidate's status and the iteration it was settled, then every
    candidate's votes at the end of every iteration. Two candidates the report prints under one name cannot be told
    apart in the iteration listing, so each keeps only the count its own table row gives, and its name carries its
    place in the table."""
    out = Result()
    rows: list[tuple[str, str, int, Decimal]] = []
    listing: list[tuple[int, str, Decimal]] = []
    for raw_line in lines:
        line = " ".join(raw_line.split())
        m = re.match(r"^Quota at first iteration: (" + NUM + ")", line)
        if m:
            out.first_quota = number(m[1])
        m = re.match(
            r"^(?P<name>.+?) (?P<status>elected|excluded) (?P<iter>\d+) (?P<votes>" + NUM + r") (?P<ntv>" + NUM + r") "
            r"(?P<quota>" + NUM + r") (?P<keep>\d\.\d+)$",
            line,
        )
        if m:
            rows.append((clean(m["name"]), m["status"], int(m["iter"]), number(m["votes"])))
            if m["status"] == "elected" or out.last_quota is None:
                out.last_quota = number(m["quota"])
            continue
        m = re.match(r"^(?P<iter>\d+) (?P<name>.+?) (?P<votes>" + NUM + r"\.\d+)$", line)
        if m:
            listing.append((int(m["iter"]), clean(m["name"]), number(m["votes"])))
    if not rows:
        raise ValueError("no candidates in the iteration report")
    names = [name for name, *_ in rows]
    twice = {name for name in names if names.count(name) > 1}
    seen: dict[str, int] = {}
    by_name: dict[str, Candidate] = {}
    for name, status, it, votes in rows:
        label = name
        if name in twice:
            seen[name] = seen.get(name, 0) + 1
            label = f"{name} ({seen[name]})"
        c = Candidate(label, None, status, {it: votes} if name in twice else {})
        out.candidates.append(c)
        by_name[name] = c
    for it, name, votes in listing:
        if name in by_name and name not in twice:
            by_name[name].counts[it] = votes
    for (name, _, it, votes), c in zip(rows, out.candidates, strict=True):
        if name not in twice and c.counts.get(it) != votes:
            raise ValueError(f"{name}: the table gives {votes} at iteration {it}, the listing {c.counts.get(it)}")
    return out


LAYOUT_ROW = re.compile(
    r"^(?P<name>\S.*?)(?:\s{2,}(?P<aff>\S.*?))?\s+(?P<no>\d{3})\s+(?P<status>elected|excluded)\s+(?P<quota>[\d.]+)\s+"
    r"(?P<votes>[\d.]+)\s+(?P<keep>\d(?:\.\d+)?)\s+(?P<iter>\d+)$"
)


def table(lines: list[str]) -> Result:
    """The returning officer's final-result table: each candidate's affiliation, status, and votes at the iteration it
    was elected or excluded, with the voting papers, informal papers, and blank papers."""
    out = Result()
    pending: str | None = None
    for raw_line in lines:
        text = raw_line.strip()
        if not text:
            continue
        m = re.match(r"^Quota at 1st iteration\s+(" + NUM + ")$", text)
        if m:
            out.first_quota = number(m[1])
            continue
        m = re.match(r"^No of (voting|informal voting|blank voting) paper\s+(\d[\d,]*)", text)
        if m:
            value = int(m[2].replace(",", ""))
            if m[1] == "voting":
                out.valid = value
            elif m[1] == "informal voting":
                out.informal = value
            else:
                out.blank = value
            continue
        m = LAYOUT_ROW.match(text)
        if m:
            name = clean(m["name"])
            party = clean(m["aff"]) if m["aff"] else None
            if pending and party:
                party = f"{pending} {party}"
            pending = None
            c = Candidate(name, party, m["status"], {int(m["iter"]): number(m["votes"])})
            out.candidates.append(c)
            if c.status == "elected" or out.last_quota is None:
                out.last_quota = number(m["quota"])
        elif not re.search(r"\d", text) and not text.startswith(("Candidate", "Final", "Issue", "Whanganui")):
            pending = text
        else:
            pending = None
    if not out.candidates:
        raise ValueError("no candidates in the final-result table")
    return out


def auckland(lines: list[str], board: Board) -> Result:
    """One board's block of the combined Auckland Council file."""
    out = Result()
    inside = False
    for raw_line in lines:
        text = raw_line.strip()
        m = re.match(r"^Issue:\s+(.+)$", text)
        if m:
            inside = norm(m[1]) == norm(board.name)
            continue
        if not inside:
            continue
        m = re.match(
            r"^(Total Voting Documents|Blanks|Informals|First Iteration Quota|Last Iteration Quota):\s+(\S+)", text
        )
        if m:
            what, value = m[1], number(m[2])
            if what == "Total Voting Documents":
                out.papers = int(value)
            elif what == "Blanks":
                out.blank = int(value)
            elif what == "Informals":
                out.informal = int(value)
            elif what == "First Iteration Quota":
                out.first_quota = value
            else:
                out.last_quota = value
            continue
        m = re.match(
            r"^(?P<id>\d{3})\s+(?P<name>.+?)\s{2,}(?:(?P<aff>.*?)\s{2,})?(?P<iter>\d+)\s+(?P<votes>\d+\.\d+)\s+"
            r"(?P<keep>\d\.\d+)\s+(?P<status>elected|excluded)$",
            text,
        )
        if m:
            out.candidates.append(
                Candidate(
                    clean(m["name"]),
                    clean(m["aff"]) if m["aff"] else None,
                    m["status"],
                    {int(m["iter"]): number(m["votes"])},
                )
            )
    if not out.candidates:
        raise ValueError(f"{board.name}: not in the combined file")
    return out


def declaration(lines: list[str]) -> Result:
    """A declaration of result: the candidates with their labels and status, and the informal and blank votes."""
    out = Result()
    for raw_line in lines:
        text = raw_line.strip()
        if not text:
            continue
        parts = re.split(r"\s{2,}", text)
        if len(parts) >= 2 and parts[-1] in {"elected", "excluded"} and re.match(r"^[A-Z][A-Z'’-]+ ", parts[0]):
            out.candidates.append(Candidate(clean(parts[0]), clean(parts[1]) if len(parts) == 3 else None, parts[-1]))
    words = " ".join(" ".join(lines).split())
    m = re.search(r"There were ([\d,]+) informal votes and ([\d,]+) blank votes", words)
    if m:
        out.informal, out.blank = int(m[1].replace(",", "")), int(m[2].replace(",", ""))
    m = re.search(r"\(final quota\) as determined at the last iteration was ([\d,]+\.\d+)", words)
    if m:
        out.last_quota = number(m[1])
    if not out.candidates:
        raise ValueError("no candidates in the declaration")
    return out


def hutt_html(lines: list[str]) -> Result:
    """The 2019 Hutt Valley notice prints the declaration as one paragraph: each candidate's surname in capitals, the
    given name, a label if any, and the status."""
    text = " ".join(lines)
    m = re.search(r"Hutt Valley District Health Board \(7 vacancies\)(.*?) are declared elected", text)
    if not m:
        raise ValueError("no declaration paragraph")
    body = m[1]
    out = Result()
    pieces = re.split(r"\b(elected|excluded)\b", body)
    for segment, status in zip(pieces[0::2], pieces[1::2], strict=False):
        words = segment.split()
        i = 0
        while i < len(words) and re.fullmatch(r"[A-Z][A-Z'’-]+", words[i]):
            i += 1
        surname, rest = words[:i], words[i:]
        if not surname or not rest:
            raise ValueError(f"cannot read candidate: {segment!r}")
        if rest[-1] == "Independent":
            given, party = rest[:-1], "Independent"
        else:
            given, party = rest[:1], " ".join(rest[1:]) or None
        out.candidates.append(Candidate(clean(" ".join([*surname, *given])), party, status))
    m = re.search(r"There were ([\d,]+) informal votes and ([\d,]+) blank votes", text)
    if m:
        out.informal, out.blank = int(m[1].replace(",", "")), int(m[2].replace(",", ""))
    m = re.search(r"\(final quota\) as determined at the last iteration was ([\d,]+\.\d+)", text)
    if m:
        out.last_quota = number(m[1])
    return out


def wellington_html(lines: list[str]) -> Result:
    """The council's results page: for each candidate the name, the label if any, the status, the iteration it was
    settled, and the votes then."""
    out = Result()
    text = " ".join(lines)
    m = re.search(r"There were ([\d,]+) informal votes and ([\d,]+) blank votes", text)
    if m:
        out.informal, out.blank = int(m[1].replace(",", "")), int(m[2].replace(",", ""))
    m = re.search(r"last iteration was ([\d,]+\.\d+)", text)
    if m:
        out.last_quota = number(m[1])
    start = next(i for i, line in enumerate(lines) if line == "Votes") + 1
    pending: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if line.lower() in {"elected", "not elected"}:
            if not 1 <= len(pending) <= 2:
                raise ValueError(f"cannot read candidate before line {i}: {pending}")
            it, votes = lines[i + 1], lines[i + 2]
            out.candidates.append(
                Candidate(
                    clean(pending[0]),
                    clean(pending[1]) if len(pending) == 2 else None,
                    "elected" if line.lower() == "elected" else "excluded",
                    {int(it): number(votes)},
                )
            )
            pending = []
            i += 3
            continue
        if line.startswith("Elected candidate"):
            break
        pending.append(line)
        i += 1
    return out


def whanganui_html(lines: list[str]) -> Result:
    out = Result()
    for i, line in enumerate(lines):
        if line == "Quota at 1st iteration":
            out.first_quota = number(lines[i + 1])
        elif line == "No of voting paper":
            out.valid = int(lines[i + 1])
        elif line == "No of informal voting paper":
            out.informal = int(lines[i + 1])
        elif line == "No of blank voting paper":
            out.blank = int(lines[i + 1])
    start = next(i for i, line in enumerate(lines) if line == "Iteration") + 1
    pending: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if line in {"elected", "excluded"}:
            quota, votes, _keep, it = lines[i + 1 : i + 5]
            if not 1 <= len(pending) <= 2:
                raise ValueError(f"cannot read candidate before line {i}: {pending}")
            out.candidates.append(
                Candidate(
                    clean(pending[0]),
                    clean(pending[1]) if len(pending) == 2 else None,
                    line,
                    {int(it): number(votes)},
                )
            )
            if line == "elected" or out.last_quota is None:
                out.last_quota = number(quota)
            pending = []
            i += 5
            continue
        if line.startswith("Related Information"):
            break
        pending.append(line)
        i += 1
    return out


def merge(main: Result, extra: Result) -> None:
    """Add the labels, informal votes, and blank votes of a declaration to the iteration report of the same board."""
    labels = {tokens(c.name): c for c in extra.candidates}
    for c in main.candidates:
        found = labels.get(tokens(c.name))
        if found is None:
            raise ValueError(f"{c.name} is in the iteration report but not the declaration")
        if found.status != c.status:
            raise ValueError(f"{c.name}: {found.status} in the declaration, {c.status} in the report")
        c.party = found.party
    if len(labels) != len(main.candidates):
        raise ValueError("the declaration and the report list different candidates")
    main.informal, main.blank = extra.informal, extra.blank
    main.files.extend(extra.files)


@functools.cache
def result_of(board_key: str, year: int) -> Result:
    entry = next(e for e in ENTRIES if e.board == board_key and e.year == year)
    board = BY_KEY[board_key]
    read = {
        "meek": lambda: meek(doc_lines(entry.url) if entry.suffix == ".doc" else pdf_lines(entry.url)),
        "table": lambda: table(pdf_lines(entry.url)),
        "auckland": lambda: auckland(pdf_lines(entry.url), board),
        "declaration": lambda: declaration(pdf_lines(entry.url)),
        "hutt-html": lambda: hutt_html(html_lines(entry.url)),
        "wellington-html": lambda: wellington_html(html_lines(entry.url)),
        "whanganui-html": lambda: whanganui_html(html_lines(entry.url)),
    }
    out = read[entry.kind]()
    out.files = [entry.url]
    for kind, url, _ in entry.also:
        extra = declaration(pdf_lines(url)) if kind == "declaration" else Result()
        extra.files = [url]
        merge(out, extra)
    return out


def entries_of(year: int) -> list[Entry]:
    return [e for e in ENTRIES if e.year == year]


# ---- the statistics ---------------------------------------------------------------------------------------------

# column of each figure in the rows of the district health boards, by year
COLUMNS = {
    2013: {"positions": 20, "candidates": 22, "voters": 47, "informal": 65, "blank": 67},
    2016: {"positions": 15, "candidates": 17, "voters": 40, "informal": 58, "blank": 60},
    2019: {"positions": 3, "candidates": 5, "voters": 7, "informal": 11, "blank": 12},
}


@functools.cache
def statistics(year: int) -> dict[str, dict[str, int]]:
    """Positions, candidates, voters, informal votes, and blank votes of each board in the year's statistics file."""
    rows = list(csv.reader(raw.cached(SOURCE, STATS_URLS[year]).path.read_text(encoding="cp1252").splitlines()))
    columns = COLUMNS[year]
    out: dict[str, dict[str, int]] = {}
    if year == 2019:
        start = next(i for i, r in enumerate(rows) if r and r[0].strip() == "DISTRICT HEALTH BOARDS")
        selected = []
        for r in rows[start + 3 :]:
            if not r or not r[0].strip() or r[0].strip() == "OTHER":
                break
            selected.append((r[0], r))
    else:
        selected = [(r[1], r) for r in rows if len(r) > 5 and r[3].strip() == "DHB" and r[4].strip() == "STV"]
    for name, r in selected:
        figures = {k: int(r[i].replace(",", "")) for k, i in columns.items() if i < len(r) and r[i].strip()}
        into = out.setdefault(norm(name).replace("huttdistrict", "huttvalleydistrict"), {})
        for k, v in figures.items():
            into[k] = into.get(k, 0) + v
    return out


def verify() -> dict[str, Any]:
    """Each declaration's informal and blank votes, voting papers, positions, and candidates, against the statistics."""
    out: dict[str, Any] = {}
    for year in YEARS:
        stats = statistics(year)
        for entry in entries_of(year):
            board = BY_KEY[entry.board]
            result = result_of(entry.board, year)
            want = stats[norm(board.name)]
            elected = sum(c.status == "elected" for c in result.candidates)
            first = {c.name: c.counts.get(1) for c in result.candidates}
            first_total = sum(v for v in first.values() if v is not None) if all(first.values()) else None
            valid = want["voters"] - want["informal"] - want["blank"]
            out[f"{board.key}:{year}"] = {
                "elected": (elected, want["positions"]),
                "candidates": (len(result.candidates), want["candidates"]),
                "informal": (result.informal, want["informal"]),
                "blank": (result.blank, want["blank"]),
                "voting_papers": (result.papers, want["voters"]),
                "valid_papers": (result.valid, valid),
                "first_count_total": (str(first_total) if first_total is not None else None, valid),
            }
    return out


# ---- the packages -------------------------------------------------------------------------------------------------


def area(board: Board) -> str:
    return key("area", SCHEME, board.key)


def body(board: Board) -> str:
    return key("body", board.key)


def source_id(entry: Entry, url: str) -> str:
    return key("source", entry.board, entry.year, url)


def stats_source(p: Package, year: int) -> None:
    sid = key("source", "stats", year)
    p.add(
        "source",
        id=sid,
        kind="official_body",
        publisher="Ministry for Cities, Environment, Regions and Transport",
        title=f"{year} Local Authority Election Statistics",
        url=STATS_PAGE,
        license="CC BY 4.0",
        attribution="Ministry for Cities, Environment, Regions and Transport (formerly Department of Internal Affairs)",
    )
    p.artifact(raw.cached(SOURCE, STATS_URLS[year]), sid)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "New Zealand's district health boards, 2001 to 2019, without boundaries",
    )
    p.external |= {NEW_ZEALAND, COUNTRY}
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=NEW_ZEALAND,
        kind="administrative",
        rank=2,
        noun="district health board",
        code_system=None,
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME, 2001),
        scheme_code=SCHEME,
        vintage="2001",
        as_of=date(2001, 10, 13),
    )
    for board in BOARDS:
        a = area(board)
        p.add("area", id=a, scheme_code=SCHEME)
        p.add("area_code", area_id=a, code=board.key)
        p.add("area_parent", area_id=a, parent_id=COUNTRY)
        p.name(a, board.name, "en", "official")
    return p


STV = key("electoral_system", "stv")


def party(label: str) -> str:
    return key("party", label)


def independent(label: str | None) -> bool:
    return not label or label.lower().startswith("independent")


def labels() -> list[str]:
    return sorted(
        {
            c.party
            for entry in ENTRIES
            for c in result_of(entry.board, entry.year).candidates
            if c.party and not independent(c.party)
        }
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The district health boards, their electoral system, and the local tickets of their elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NEW_ZEALAND}
    for board in BOARDS:
        p.add("body", id=body(board), polity_id=NEW_ZEALAND, kind="council")
        p.name(body(board), board.name, "en", "official")
    p.add(
        "electoral_system",
        id=STV,
        polity_id=NEW_ZEALAND,
        valid_from=date(2001, 1, 1),
        valid_to=None,
        rules={"formula": "stv", "quota": "droop", "surplus": "meek"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", STV),
        entity_id=STV,
        kind="methodology",
        body_md="Single transferable vote, counted by the Meek method with a Droop quota (Local Electoral Regulations "
        "2001). The first three elections, in 2001, 2004, and 2007, were counted by the Gregory method on the "
        "returning officers' software; this entry carries the rule the regulations set from 2004.",
        lang="en",
    )
    for label in labels():
        p.add("party", id=party(label), kind="party", polity_id=NEW_ZEALAND)
        p.name(party(label), label, "en", "official")
    return p


METHOD = (
    "Single transferable vote for seven members, counted by the Meek method. The votes stored under each count round "
    "are each candidate's votes at the end of that iteration of the count, as the electoral officer's report prints "
    "them; a file that prints only the iteration at which a candidate was elected or excluded gives that one count. "
    "Valid, informal, and blank voting papers are as printed by the electoral officer."
)
SOURCES = {
    "meek": "the electoral officer's STV iteration report",
    "table": "the electoral officer's final-result table",
    "auckland": "the electoral officer's final results for the district health boards",
    "declaration": "the electoral officer's declaration of result",
    "hutt-html": "the board's notice of the declaration of result",
    "wellington-html": "the council's page of final results",
    "whanganui-html": "the council's page of final results",
}


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/dhb-{day}",
        SOURCE,
        f"The {day:%-d %B %Y} district health board elections, from the electoral officers' declarations of result",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEW_ZEALAND, COUNTRY}
    stats_source(p, year)
    path = f"nz/{day}-district-health-boards"
    el = key("election", day)
    p.add(
        "election",
        id=el,
        polity_id=NEW_ZEALAND,
        kind="local",
        date_from=day,
        date_to=day,
        original_text=f"{day:%-d %B %Y}",
        status="held",
    )
    p.name(el, f"{year} New Zealand district health board elections", "en", "official")
    p.add("slug", path=path, entity_id=el, is_current=True)
    made: set[str] = set()
    for entry in entries_of(year):
        board = BY_KEY[entry.board]
        result = result_of(entry.board, year)
        for url in result.files:
            sid = source_id(entry, url)
            if sid in made:
                continue
            made.add(sid)
            p.add(
                "source",
                id=sid,
                kind="official_body",
                publisher=entry.publisher,
                title=f"{board.name}: {SOURCES[entry.kind if url == entry.url else 'declaration']}, {year} triennial elections",
                url=url,
                license="No license stated; published by the electoral officer for public use",
                attribution=entry.publisher,
            )
            p.artifact(raw.cached(SOURCE, url), sid)
        sid = source_id(entry, entry.url)
        tag = board.key
        c = key("contest", day, tag)
        p.add(
            "contest",
            id=c,
            election_id=el,
            kind="body_seats",
            body_id=body(board),
            district_area_id=area(board),
            magnitude=7,
            round_number=1,
            electoral_system_id=STV,
            default_result_kind="counted",
        )
        p.name(c, f"{board.name}, {year}", "en", "official")
        p.add("slug", path=f"{path}/{board.key}", entity_id=c, is_current=True)
        rs = key("result_set", day, tag)
        p.add(
            "result_set",
            id=rs,
            election_id=el,
            contest_id=c,
            stage="official",
            kind="counted",
            source_id=sid,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
        unit = key("unit", day, tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NEW_ZEALAND,
            scheme_code=SCHEME,
            code=f"{day}:{tag}",
            area_relation="equals",
            area_id=area(board),
            kind="standard",
        )
        for candidate in result.candidates:
            name = candidate.name
            option = key("option", day, tag, *sorted(tokens(name)))
            person = key("person", day, tag, *sorted(tokens(name)))
            solo = independent(candidate.party)
            member = None if candidate.party is None or solo else party(candidate.party)
            p.add("person", id=person)
            p.name(person, name, "en", "official")
            p.add("contest_option", id=option, contest_id=c, kind="candidate", party_id=member, ballot_label=name)
            p.add(
                "candidacy",
                id=key("candidacy", day, tag, *sorted(tokens(name))),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=member,
                is_independent=solo,
            )
            for count_round, votes in sorted(candidate.counts.items()):
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=count_round,
                    flags=0,
                    value=votes,
                )
            if candidate.status == "elected":
                p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
        for measure, value in ((3, result.papers), (4, result.valid), (5, result.informal), (6, result.blank)):
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
    return p
