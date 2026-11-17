"""Hong Kong Legislative Council elections from the results appendices of the Electoral Affairs Commission reports.

Each report prints, for every constituency, the votes each candidate or list received and who was elected: general
elections from 1998 to 2016 and by-elections from 2001 to 2022. The appendices are PDF or Word tables with one row
per candidate, so the parse reads the position of every word and assigns it to the column and row it sits in. The
tables give constituency totals only; they hold no counting-station figures.
"""

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.hk import reo

SOURCE = "hk.eac_legco"
BASE = "https://www.eac.hk/pdf/legco"
PAGE = "https://www.eac.hk/en/elections/legco.html"
INFO = "https://www.info.gov.hk/chinfo/dix-e.pdf"
HONG_KONG = reo.HONG_KONG
LEGCO = reo.LEGCO
SCHEME = reo.CONSTITUENCY_SCHEME
FPTP = reo.FPTP
BLOCK = reo.BLOCK
VOTES = 1
GEOGRAPHICAL = {"LC1": "Hong Kong Island", "LC2": "Kowloon West", "LC3": "Kowloon East"}
SPECIAL = {"HYK", "AF", "IN", "TR", "UC", "RC"}

RESULT = ("Elected", "Uncontested")
HEADER_END = {"Election", "Received", "Obtained", "RO", "Name", "Count", "Votes"}
KEYS = {
    "Candidate",
    "Constituency",
    "Priority",
    "Votes",
    "Received",
    "Obtained",
    "Declared",
    "Given",
    "Result",
    "Count",
}
TITLE = {"Legislative", "Appendix", "(Page", "Results", "Result", "Election:"}
NUMERIC = re.compile(r"\d{1,3}(?:,\d{3})*|\d+")
DASHES = {"–", "-", "—", "─", "‒"}
CODES = {"list": re.compile(r"LC\d|DS"), "candidate": re.compile(r"LC\d|[A-Z]{1,3}")}


@dataclass(frozen=True)
class Table:
    """One results appendix: its URL, how it is laid out, and the constituency when it holds only one."""

    role: str
    url: str
    code: str = ""
    name: str = ""


@dataclass(frozen=True)
class Election:
    day: str
    general: bool
    title: str
    tables: tuple[Table, ...]

    @property
    def year(self) -> int:
        return int(self.day[:4])


def _appendix(folder: str, name: str) -> str:
    return f"{BASE}/{folder}/{name}"


def _tables(folder: str, gc: str, fc: str, dc2: str | None = None, gc_role: str = "gc") -> tuple[Table, ...]:
    tables = [Table(gc_role, _appendix(folder, gc)), Table("fc", _appendix(folder, fc))]
    if dc2:
        tables.append(Table("dc2", _appendix(folder, dc2), "DS", "District Council (second)"))
    return tuple(tables)


ELECTIONS = (
    Election("1998-05-24", True, "1998 Hong Kong Legislative Council general election", (Table("text", INFO),)),
    Election(
        "2000-09-10",
        True,
        "2000 Hong Kong Legislative Council general election",
        (
            Table("gc", _appendix("2000/en", "e15_appendix_xv.doc")),
            Table("fc", _appendix("2000/en", "e16_appendix_xvi.doc")),
            Table("ec", _appendix("2000/en", "e17_appendix_xvii.doc"), "EC", "Election Committee"),
        ),
    ),
    Election(
        "2001-09-16",
        False,
        "2001 Hong Kong Legislative Council by-election",
        (Table("ec", _appendix("2001/en", "eng_appIII_3.doc"), "EC", "Election Committee"),),
    ),
    Election(
        "2004-09-12",
        True,
        "2004 Hong Kong Legislative Council general election",
        _tables("2004/en/report", "lc2004appendix11_e.pdf", "lc2004appendix13_e.pdf"),
    ),
    Election(
        "2007-12-02",
        False,
        "2007 Hong Kong Legislative Council by-election, Hong Kong Island geographical constituency",
        (Table("single", _appendix("en/2007", "2007lcbe_hki_appendix4_e.pdf"), "LC1", "Hong Kong Island"),),
    ),
    Election(
        "2008-09-07",
        True,
        "2008 Hong Kong Legislative Council general election",
        _tables("2008/en/report", "2008lce_appendix8_e.pdf", "2008lce_appendix10_e.pdf"),
    ),
    Election(
        "2010-05-16",
        False,
        "2010 Hong Kong Legislative Council by-election",
        (Table("gcb", _appendix("en/2010", "2010lcbe_appendix4_e.pdf")),),
    ),
    Election(
        "2012-09-09",
        True,
        "2012 Hong Kong Legislative Council general election",
        _tables("2012LCE_Report/en", "2012lce_appendix8.pdf", "2012lce_appendix10a.pdf", "2012lce_appendix10b.pdf"),
    ),
    Election(
        "2016-02-28",
        False,
        "2016 Hong Kong Legislative Council by-election, New Territories East geographical constituency",
        (
            Table(
                "single",
                _appendix("2016lcbe_nte_report/en", "2016lcbe_nte_appendix4.pdf"),
                "LC5",
                "New Territories East",
            ),
        ),
    ),
    Election(
        "2016-09-04",
        True,
        "2016 Hong Kong Legislative Council general election",
        _tables("2016LCE_Report/en", "2016lce_appendix8.pdf", "2016lce_appendix10a.pdf", "2016lce_appendix10b.pdf"),
    ),
    Election(
        "2018-03-11",
        False,
        "2018 Hong Kong Legislative Council by-election",
        (
            Table("gcb", _appendix("2018lcbe_report/en", "2018lcbe_appendix4a(Eng).pdf")),
            Table("fc", _appendix("2018lcbe_report/en", "2018lcbe_appendix4b(Eng).pdf")),
        ),
    ),
    Election(
        "2018-11-25",
        False,
        "2018 Hong Kong Legislative Council by-election, Kowloon West geographical constituency",
        (Table("single", _appendix("2018lcbe_kw_report/en", "2018lcbe_kw_appendix4(Eng).pdf"), "LC2", "Kowloon West"),),
    ),
    Election(
        "2022-12-18",
        False,
        "2022 Hong Kong Legislative Council by-election, Election Committee constituency",
        (Table("ec", _appendix("2022LCBE_Report/en", "2022lcbe_appendix3.pdf"), "EC", "Election Committee"),),
    ),
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


EAC_SOURCE = key("source", "eac")
HARE = key("electoral_system", "list-hare")
IRV = key("electoral_system", "preferential-elimination")


def urls() -> list[str]:
    return [table.url for election in ELECTIONS for table in election.tables]


def fetch() -> None:
    for url in urls():
        raw.fetch(SOURCE, url, suffix=".doc" if url.endswith(".doc") else ".pdf")


# --- reading the tables -------------------------------------------------------------------------------------


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Row:
    label: str
    name: str
    number: str
    votes: int | None
    dash: bool
    elected: bool
    uncontested: bool
    page: int
    cy: float


@dataclass
class Member:
    label: str
    name: str
    elected: bool


@dataclass
class Entry:
    """A ballot line: a list with its members, or one candidate."""

    number: str
    votes: int | None
    uncontested: bool
    page: int
    cy: float
    members: list[Member] = field(default_factory=list)

    @property
    def seats(self) -> int:
        """The members returned; a candidate returned without a poll counts as elected."""
        return sum(m.elected or self.uncontested for m in self.members)


@dataclass
class Constituency:
    code: str
    name: str
    entries: list[Entry]

    @property
    def seats(self) -> int:
        return sum(e.seats for e in self.entries)


def pdf_of(path: Path) -> Path:
    """The PDF to read: the file itself, or a copy that LibreOffice makes from a Word document."""
    if path.suffix != ".doc":
        return path
    out = Path(tempfile.mkdtemp(prefix="hk-eac-"))
    subprocess.run(
        [
            "soffice",
            f"-env:UserInstallation=file://{out}/profile",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out),
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return out / f"{path.stem}.pdf"


def pages(path: Path) -> list[list[Word]]:
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    result: list[list[Word]] = []
    for page in re.split(r"<page ", out)[1:]:
        found = re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', page)
        result.append([Word(float(a), float(b), float(c), float(d), unescape(t)) for a, b, c, d, t in found])
    return result


def _numeric(text: str) -> bool:
    return NUMERIC.fullmatch(text) is not None


def _figure(words: list[Word]) -> tuple[int | None, bool]:
    if all(w.text in DASHES for w in words):
        return None, True
    return int("".join(w.text.replace(",", "") for w in words)), False


def _lines(words: list[Word]) -> list[list[Word]]:
    result: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.cy):
        if result and abs(result[-1][0].cy - w.cy) <= 3:
            result[-1].append(w)
        else:
            result.append([w])
    return result


def _body(words: list[Word]) -> list[Word]:
    """The words of a page below its title and outside its column headings, which a Word table can repeat mid-page."""
    keys = [w.cy for w in words if w.text in KEYS]
    top = min(keys) if keys else 0.0
    ends = [w.cy for w in words if w.text in HEADER_END and w.cy >= top - 3]
    bottom = max([*keys, *ends]) if keys else 0.0
    kept: list[Word] = []
    for line in _lines(words):
        if any(w.text in TITLE for w in line) or (len(line) == 1 and _numeric(line[0].text) and line[0].cy < 70):
            continue  # the title, or the page number a few lines from the top of the page
        kept += [w for w in line if not (top - 4 <= w.cy <= bottom + 4)]
    return kept


def _nearest(anchors: list[Word], y: float) -> int:
    return min(range(len(anchors)), key=lambda i: abs(anchors[i].cy - y))


def _page_rows(number: int, words: list[Word], mode: str) -> tuple[list[Row], list[Word]]:
    """The candidate rows of one page and its leftover label words. Mode `list` anchors each row on its priority
    letter; mode `candidate` anchors it on the candidate number."""
    body = _body(words)
    if mode == "list":
        found = [w for w in body if re.fullmatch(r"[a-z]", w.text)]
        if not found:
            return [], []
        priority = [w for w in words if w.text == "Priority"]
        column = priority[0].cx if priority else sorted(w.cx for w in found)[len(found) // 2]
        anchors = sorted((w for w in found if abs(w.cx - column) <= 9), key=lambda w: w.cy)
        left = column - 9
    else:
        heads = [w for w in words if w.text == "Number"]
        if not heads:
            return [], []
        column = min(heads, key=lambda w: w.x0).cx
        anchors = sorted(
            (
                w
                for w in body
                if abs(w.cx - column) <= 10 and (w.text.isdigit() or re.fullmatch(r"[A-Z]", w.text) or w.text in DASHES)
            ),
            key=lambda w: w.cy,
        )
        left = column - 10
    if not anchors:
        return [], []
    parts: list[dict[str, list[Word]]] = [{"name": [], "vote": [], "result": [], "number": []} for _ in anchors]
    labels: list[Word] = []
    for w in body:
        if any(w is a for a in anchors):
            continue
        in_number_zone = mode == "list" and column - 75 < w.cx < left and _numeric(w.text)
        if w.x1 <= left and not in_number_zone:
            labels.append(w)
            continue
        if in_number_zone:
            kind = "number"
        elif w.text.startswith(RESULT):
            kind = "result"
        elif (_numeric(w.text) or w.text in DASHES) and w.x0 > column + (9 if mode == "list" else 12):
            kind = "vote"
        elif w.x0 > column + 9:
            kind = "name"
        else:
            labels.append(w)
            continue
        if kind == "name" or mode != "list":
            index = _nearest(anchors, w.cy)
        else:  # a list's number, votes, and result sit on its head row, which can be the first line of a wrapped name
            index = next((i for i, a in enumerate(anchors) if a.cy >= w.cy - 3), len(anchors) - 1)
        parts[index][kind].append(w)
    rows = []
    for anchor, got in zip(anchors, parts, strict=True):
        votes, dash = _figure(sorted(got["vote"], key=lambda w: w.x0)) if got["vote"] else (None, False)
        name = " ".join(w.text for w in sorted(got["name"], key=lambda w: (round(w.cy / 4), w.x0)))
        rows.append(
            Row(
                anchor.text,
                name,
                " ".join(w.text for w in sorted(got["number"], key=lambda w: w.x0)),
                votes,
                dash,
                any(w.text.startswith("Elected") for w in got["result"]),
                any(w.text.startswith("Uncontested") for w in got["result"]),
                number,
                anchor.cy,
            )
        )
    return rows, labels


def _successor(previous: str, current: str) -> bool:
    if previous.isdigit() and current.isdigit():
        return int(current) == int(previous) + 1
    return len(previous) == 1 == len(current) and ord(current) == ord(previous) + 1


def _split(run: list[Entry], marks: list[float]) -> list[list[Entry]]:
    """Divide consecutive uncontested rows among the constituency codes that sit beside them."""
    k, n = len(marks), len(run)
    if k <= 1:
        return [run]
    if k > n:
        raise ValueError("more constituency codes than uncontested rows")

    def cost(i: int, j: int, mark: float) -> float:
        centre = (run[i].cy + run[j - 1].cy) / 2
        return min(abs(centre - mark), abs(run[i].cy - mark))

    best: dict[tuple[int, int], tuple[float, list[int]]] = {(0, 0): (0.0, [])}
    for c in range(1, k + 1):
        for j in range(c, n + 1):
            options = [
                (best[(c - 1, i)][0] + cost(i, j, marks[c - 1]), best[(c - 1, i)][1] + [i])
                for i in range(c - 1, j)
                if (c - 1, i) in best
            ]
            if options:
                best[(c, j)] = min(options, key=lambda t: t[0])
    cuts = [*best[(k, n)][1], n]
    return [run[cuts[x] : cuts[x + 1]] for x in range(k)]


def read(path: Path, mode: str, single: tuple[str, str] | None = None) -> list[Constituency]:
    """The constituencies of one results appendix, or its only one when `single` gives the code and name."""
    path = pdf_of(path)
    entries: list[Entry] = []
    labels: list[tuple[int, Word]] = []
    for number, words in enumerate(pages(path)):
        rows, found = _page_rows(number, words, mode)
        labels += [(number, w) for w in found]
        for row in rows:
            if mode == "list":
                if row.label == "a":
                    entries.append(Entry(row.number, row.votes, False, number, row.cy))
                elif not entries:
                    raise ValueError(f"{path.name}: a list member comes before any list")
                entries[-1].members.append(Member(row.label, row.name, row.elected))
            else:
                entry = Entry(row.label, row.votes, row.uncontested or row.dash, number, row.cy)
                entry.members.append(Member("", row.name, row.elected))
                entries.append(entry)
    if single is not None:
        return [Constituency(single[0], single[1], entries)]
    code = CODES[mode]
    code_words = [(p, w) for p, w in labels if code.fullmatch(w.text)]
    groups: list[list[Entry]] = []
    last: Entry | None = None
    i = 0
    while i < len(entries):
        entry = entries[i]
        if mode == "candidate" and entry.number in DASHES:
            j = i
            while j < len(entries) and entries[j].number in DASHES and entries[j].page == entry.page:
                j += 1
            run = entries[i:j]
            marks = [w for p, w in code_words if p == entry.page and run[0].cy - 8 <= w.cy <= run[-1].cy + 8]
            groups += _split(run, [w.cy for w in marks])
            last = run[-1]
            i = j
            continue
        if last is None:
            starts = True
        elif mode == "list":
            starts = not (entry.number.isdigit() and last.number.isdigit() and int(entry.number) > int(last.number))
        else:
            starts = last.number in DASHES or not _successor(last.number, entry.number)
        if starts:
            groups.append([])
        groups[-1].append(entry)
        last = entry
        i += 1
    ordered: list[tuple[int, Word]] = []
    seen: set[str] = set()
    for p, w in sorted(code_words, key=lambda t: (t[0], t[1].cy)):
        if w.text not in seen or mode != "list":
            seen.add(w.text)
            ordered.append((p, w))
    if len(ordered) != len(groups):
        raise ValueError(f"{path.name}: {len(groups)} constituencies but codes {[w.text for _, w in ordered]}")
    starts_at = [(g[0].page, g[0].cy) for g in groups]
    flush = sum(
        abs(w.cy - s[1]) < 2.5 and p == s[0] for (p, w), s in zip(ordered, starts_at, strict=True)
    ) >= 0.8 * len(groups)
    named: list[list[tuple[int, Word]]] = [[] for _ in groups]
    for q, w in labels:
        if code.fullmatch(w.text):
            continue
        if flush:  # the label starts on the first row of its constituency and runs down
            owners = [i for i, s in enumerate(starts_at) if s <= (q, w.cy + 2.5)]
            if owners:
                named[owners[-1]].append((q, w))
            continue
        here = [i for i, (p, _) in enumerate(code_words) if p == q]
        if here:  # the label is centered on its code
            near = code_words[min(here, key=lambda i: abs(code_words[i][1].cy - w.cy))][1].text
            named[next(i for i, (_, c) in enumerate(ordered) if c.text == near)].append((q, w))
    result = []
    for (_, mark), group, words in zip(ordered, groups, named, strict=True):
        first = min((q for q, _ in words), default=0)
        text = " ".join(
            w.text for q, w in sorted(words, key=lambda t: (t[0], round(t[1].cy / 4), t[1].x0)) if q == first
        )
        result.append(Constituency(mark.text, re.sub(r"^(?:Code and Name|Constituency)\s*", "", text).strip(), group))
    return result


def read_1998(path: Path) -> list[Constituency]:
    """The 1998 results from the text of the report's Appendix XII, which prints one line per candidate."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=False
    ).stdout.splitlines()
    start = next(i for i, line in enumerate(text) if "Results of the 1998 Legislative Council General Election" in line)
    end = next(i for i, line in enumerate(text) if i > start and re.search(r"Appendix XIII\s*$", line))
    section = [line for line in text[start:end] if line.strip()]
    result: list[Constituency] = []
    mode = ""
    labels: dict[str, str] = {}
    current: Constituency | None = None
    entry: Entry | None = None
    for line in section:
        if re.match(r"^Geographical Constituency", line):
            mode = "gc"
            continue
        if re.match(r"^Functional Constituencies", line):
            mode = "fc"
            continue
        if re.match(r"^Election Committee Constituency", line):
            mode = "ec"
            current = Constituency("EC", "Election Committee", [])
            result.append(current)
            continue
        if re.search(r"^\s*(?:- \d+ -|\(Page \d+\)|Appendix XII)\s*$", line) or "Candidate" in line or "Votes" in line:
            continue
        if re.match(
            r"^\s*(?:Declared by RO|Code and Name|Constituency|Number of|Given as|Value of|at the Final|RO\b)", line
        ):
            continue
        if mode == "gc":
            found = re.match(
                r"^(LC\d)\s+([A-Z ]+?)\s{2,}(\d+)\s+([a-z])\s+(.+?)(?:\s{2,}(\d+))?(?:\s{2,}(Elected))?\s*$", line
            )
            if found:
                code, name = found.group(1), found.group(2).title()
                current = Constituency(code, name, [])
                result.append(current)
                number, letter, who, votes, elected = found.groups()[2:]
            else:
                found = re.match(r"^\s{10,}(?:(\d+)\s+)?([a-z])\s+(.+?)(?:\s{2,}(\d+))?(?:\s{2,}(Elected))?\s*$", line)
                if not found or current is None:
                    raise ValueError(f"unread 1998 line {line!r}")
                number, letter, who, votes, elected = found.groups()
            if letter == "a":
                entry = Entry(number or "", int(votes) if votes else None, False, 0, 0.0)
                current.entries.append(entry)
            if entry is None:
                raise ValueError(f"1998 list member without a list: {line!r}")
            entry.members.append(Member(letter, who.strip(), bool(elected)))
        elif mode == "fc":
            label, rest = line[:28], line[28:]
            code = re.match(r"^([A-Z]{1,3})\s+(.*)$", label)
            if code:
                labels[code.group(1)] = code.group(2).strip()
                current = Constituency(code.group(1), code.group(2).strip(), [])
                result.append(current)
            elif label.strip() and current is not None and not rest.strip():
                current.name = f"{current.name} {label.strip()}"
                continue
            elif label.strip() and current is not None:
                current.name = f"{current.name} {label.strip()}"
            found = re.match(
                r"^\s*(?:([A-Z]|\d)\s+)?([A-Z][A-Z .'()\-,]*?)\s{2,}(\d+|-)(?:\s+(Elected|Uncontested:?))?\s*$", rest
            )
            if found and current is not None:
                number, who, votes, outcome = found.groups()
                contested = votes != "-"
                entry = Entry(number or "-", int(votes) if contested else None, not contested, 0, 0.0)
                entry.members.append(
                    Member("", who.strip(), outcome is not None and outcome.startswith("Elected") or not contested)
                )
                current.entries.append(entry)
            elif rest.strip() == "Elected" and current is not None:
                continue
        elif mode == "ec" and current is not None:
            found = re.match(r"^(\d+)\s+(.+?)\s{2,}(\d+)(?:\s+(Elected))?\s*$", line)
            if found:
                entry = Entry(found.group(1), int(found.group(3)), False, 0, 0.0)
                entry.members.append(Member("", found.group(2).strip(), found.group(4) is not None))
                current.entries.append(entry)
    return [c for c in result if c.entries]


# --- the archive's rows -------------------------------------------------------------------------------------


def _art(p: Package, url: str) -> Path:
    artifact = raw.cached(SOURCE, url)
    p.artifact(artifact, EAC_SOURCE)
    return artifact.path


def parse_election(p: Package, election: Election) -> list[tuple[str, Constituency]]:
    """Every constituency the election's appendices print, each tagged with its kind of constituency."""
    found: list[tuple[str, Constituency]] = []
    for table in election.tables:
        path = _art(p, table.url)
        if table.role == "text":
            found += [
                ("gc" if c.code.startswith("LC") else "ec" if c.code == "EC" else "fc", c) for c in read_1998(path)
            ]
        elif table.role in ("gc", "gcb"):
            found += [
                ("gc" if table.role == "gc" else "gcb", c)
                for c in read(path, "list" if table.role == "gc" else "candidate")
            ]
        elif table.role == "fc":
            found += [("fc", c) for c in read(path, "candidate")]
        elif table.role == "dc2":
            found += [("dc2", c) for c in read(path, "list", (table.code, table.name))]
        else:
            found += [
                (table.role if table.role != "single" else "gcb", c)
                for c in read(path, "candidate", (table.code, table.name))
            ]
    return found


METHOD = {
    "gc": (
        "Each elector voted for one list. The Electoral Affairs Commission's report prints each list's votes and the "
        "members the lists returned. Seats go to lists by the largest remainder of votes after the Hare quota. The "
        "appendices give constituency totals only, not counting-station figures."
    ),
    "dc2": (
        "Each elector in the District Council (second) functional constituency voted for one list. The Electoral "
        "Affairs Commission's report prints each list's votes and the members returned. The appendices give "
        "constituency totals only."
    ),
    "gcb": (
        "Each elector voted for one candidate. The Electoral Affairs Commission's report prints each candidate's votes "
        "and who was returned. The appendices give constituency totals only."
    ),
    "fc": (
        "The Electoral Affairs Commission's report prints each candidate's votes and who was returned. In the four "
        "special functional constituencies the votes are those at the final count of the preferential elimination "
        "count. A candidate returned without a poll received no votes and is recorded as returned unopposed. The "
        "appendices give constituency totals only."
    ),
    "ec": (
        "Election Committee members voted for as many candidates as there were seats. The Electoral Affairs "
        "Commission's report prints each candidate's votes as declared by the returning officer and who was "
        "returned. The appendices give totals only."
    ),
}


def name_of(election: Election, kind: str, c: Constituency) -> str:
    year = election.year
    what = "general election" if election.general else "by-election"
    head = f"{year} Legislative Council {what}"
    if kind in ("gc", "gcb"):
        name = c.name or GEOGRAPHICAL.get(c.code, c.code)
        return f"{head}, {name} geographical constituency ({c.code})"
    if kind == "dc2":
        return f"{head}, District Council (second) functional constituency"
    if kind == "ec":
        return f"{head}, Election Committee constituency"
    return f"{head}, {c.name} functional constituency"


def slug_of(kind: str, c: Constituency) -> str:
    if kind in ("gc", "gcb"):
        return c.code.lower()
    if kind == "dc2":
        return "dc2"
    if kind == "ec":
        return "ec"
    return f"fc-{c.code.lower()}"


def system_of(kind: str, c: Constituency, general: bool) -> str:
    if kind in ("gc", "dc2"):
        return HARE
    if kind == "fc" and general and c.code in SPECIAL:
        return IRV
    return FPTP if c.seats <= 1 else BLOCK


def ballot_position(label: str) -> int | None:
    if label.isdigit():
        return int(label)
    if re.fullmatch(r"[A-Za-z]", label):
        return ord(label.upper()) - 64
    return None


def build_election(election: Election) -> Package:
    p = Package(
        f"{SOURCE}/{election.day}",
        SOURCE,
        f"{election.title}, results by constituency from the Electoral Affairs Commission's report",
        depends=[f"{SOURCE}/registry", "hk.reo/registry"],
    )
    p.external.add(HONG_KONG)
    day = election.day
    first = date.fromisoformat(day)
    constituencies = parse_election(p, election)
    event = p.id("election", day)
    p.add(
        "election",
        id=event,
        polity_id=HONG_KONG,
        kind="general" if election.general else "by_election",
        date_from=first,
        date_to=first,
        status="held",
    )
    p.name(event, election.title, "en", "official")
    p.add("slug", path=f"hk/{day}-legco", entity_id=event, is_current=True)
    for kind, c in constituencies:
        _contest(p, election, event, kind, c)
    return p


def _contest(p: Package, election: Election, event: str, kind: str, c: Constituency) -> None:
    day = election.day
    code = slug_of(kind, c)
    contest = p.id("contest", day, code)
    seats = c.seats
    p.add(
        "contest",
        id=contest,
        election_id=event,
        kind="body_seats",
        body_id=LEGCO,
        magnitude=seats,
        round_number=1,
        electoral_system_id=system_of(kind, c, election.general),
        default_result_kind="counted",
    )
    title = name_of(election, kind, c)
    p.name(contest, title, "en", "official")
    p.add("slug", path=f"hk/{day}-legco/{code}", entity_id=contest, is_current=True)
    unit = p.id("unit", day, code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=HONG_KONG,
        scheme_code=SCHEME,
        code=f"{day}:{code}",
        area_relation="unlocated",
        area_id=None,
        kind="aggregate",
    )
    p.name(unit, title.split(", ", 1)[1] if ", " in title else title, "en", "official")
    contested = any(e.votes is not None for e in c.entries)
    result_set = p.id("result_set", day, code)
    p.add(
        "result_set",
        id=result_set,
        election_id=event,
        contest_id=contest,
        stage="official",
        kind="counted" if contested else "tacit",
        source_id=EAC_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0 if contested else None,
    )
    p.add(
        "note",
        id=p.id("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD[kind if kind in METHOD else "gcb"],
        lang="en",
    )
    for index, entry in enumerate(c.entries, 1):
        _entry(p, day, code, contest, result_set, unit, kind, entry, contested, index)


def _entry(
    p: Package,
    day: str,
    code: str,
    contest: str,
    result_set: str,
    unit: str,
    kind: str,
    entry: Entry,
    contested: bool,
    index: int,
) -> None:
    tag = f"{code}:{index}:{entry.number}"
    option = p.id("option", day, tag)
    head = entry.members[0]
    is_list = kind in ("gc", "dc2")
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="list" if is_list else "candidate",
        ballot_position=ballot_position(entry.number),
        ballot_label=head.name,
    )
    for position, member in enumerate(entry.members, 1):
        person = p.id("person", day, tag, member.label)
        candidacy = p.id("candidacy", day, tag, member.label)
        p.add("person", id=person)
        p.name(person, member.name, "en", "official")
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="list_member" if is_list else "head",
            list_position=position if is_list else None,
            ballot_name=member.name,
        )
        elected = member.elected or entry.uncontested
        if elected and is_list:
            p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
        elif elected:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
    if is_list and entry.seats:
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry.seats)
    if contested and entry.votes is not None:
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=entry.votes)
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=VOTES,
            count_round=0,
            flags=0,
            value=entry.votes,
        )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Electoral Affairs Commission as a source, and the list and preferential elimination systems of the "
        "Hong Kong Legislative Council",
        depends=["hk.reo/registry"],
    )
    p.external.add(HONG_KONG)
    p.add(
        "source",
        id=EAC_SOURCE,
        kind="official_body",
        publisher="Electoral Affairs Commission, Hong Kong",
        title="Reports of the Electoral Affairs Commission on the Legislative Council elections",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Electoral Affairs Commission, Hong Kong Special Administrative Region Government",
    )
    systems = (
        (
            HARE,
            {"formula": "hare"},
            (
                "Each elector votes for one list. The number of valid votes divided by the seats is the quota; each "
                "list wins a seat for each full quota, and the remaining seats go to the lists with the largest "
                "remainders. Seats go to a list's candidates in the order of its ranking."
            ),
        ),
        (
            IRV,
            {"formula": "irv"},
            (
                "Each elector ranks the candidates. A candidate with an absolute majority is elected; otherwise the "
                "candidate with the fewest votes is eliminated and those ballots pass to the next preference. The "
                "Legislative Council used it in the special functional constituencies."
            ),
        ),
    )
    for id, rules, text in systems:
        p.add("electoral_system", id=id, polity_id=HONG_KONG, rules=rules, rules_version=1)
        p.add("note", id=p.id("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{e.day}" for e in ELECTIONS)]


def parse() -> None:
    registry().write()
    for election in ELECTIONS:
        build_election(election).write()


def emit() -> None:
    package.run("load", packages())
