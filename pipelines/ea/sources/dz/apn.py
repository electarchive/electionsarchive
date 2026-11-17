"""Algeria's People's National Assembly elections from the Constitutional Council's and Constitutional Court's
proclamations in the Journal officiel (JORADP): the lists that won seats in each constituency, and each
constituency's registered voters, voters, and valid votes."""

import difflib
import functools
import json
import re
import subprocess
import unicodedata
from collections import Counter
from datetime import date
from itertools import accumulate
from pathlib import Path
from typing import NamedTuple, TypedDict, cast
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "dz.apn"
BASE = "https://www.joradp.dz/FTP/jo-francais"
ALGERIA = str(uuid5(package.NAMESPACE, "polity\x1fDZ"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fDZA"))

# year -> (issue number, election day, seats the proclamation fills)
ELECTIONS: dict[int, tuple[int, date, int]] = {
    1997: (40, date(1997, 6, 5), 380),
    2002: (43, date(2002, 5, 30), 389),
    2007: (45, date(2007, 5, 17), 389),
    2012: (32, date(2012, 5, 10), 462),
    2017: (34, date(2017, 5, 4), 462),
    2021: (51, date(2021, 6, 12), 407),
    2026: (54, date(2026, 7, 2), 407),
}


DIGITIZED = Path(__file__).with_name("apn1997.json")


class Listed(TypedDict):
    label: str
    seats: int
    derived: bool
    page: int
    y: int


class Counted(TypedDict):
    code: str
    registered: int
    voters: int
    abstentions: int
    valid: int
    turnout_page: int
    turnout_y: int
    lists: list[Listed]


class Nationwide(TypedDict):
    label: str
    votes: int
    seats: int


class Scanned(TypedDict):
    issue: int
    constituencies: list[Counted]
    nationwide: list[Nationwide]
    nationwide_total_votes: int
    totals: dict[str, int]


@functools.cache
def digitized() -> Scanned:
    """The 1997 proclamation's figures, read from its scanned pages and checked against its printed totals."""
    return cast(Scanned, json.loads(DIGITIZED.read_text(encoding="utf-8")))


def issue_url(year: int) -> str:
    return f"{BASE}/{year}/F{year}{ELECTIONS[year][0]:03d}.pdf"


def fetch() -> None:
    for year in ELECTIONS:
        raw.fetch(SOURCE, issue_url(year), suffix=".pdf")


class Word(NamedTuple):
    page: int
    x: float
    y: float
    text: str


@functools.cache
def words(year: int) -> list[Word]:
    """Every word of the issue with its page, left edge, and top edge, from poppler's bounding boxes."""
    path = raw.cached(SOURCE, issue_url(year)).path
    xml = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    out: list[Word] = []
    page = 0
    for line in xml.splitlines():
        line = line.strip()
        if line.startswith("<page "):
            page += 1
        elif line.startswith("<word "):
            m = re.match(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="[\d.]+" yMax="[\d.]+">(.*)</word>', line)
            if m:
                text = m.group(3).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                text = text.replace("&quot;", '"').replace("&apos;", "'")
                out.append(Word(page, float(m.group(1)), float(m.group(2)), text))
    return out


@functools.cache
def layout(year: int) -> list[str]:
    """The issue's text, one string per page, with columns kept in place."""
    path = raw.cached(SOURCE, issue_url(year)).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return text.split("\f")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("’", "'").replace("‘", "'"))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def integer(text: str) -> int:
    return int(re.sub(r"[^\d]", "", text))


class Turnout(NamedTuple):
    code: str
    name: str
    registered: int
    voters: int
    valid: int


ABROAD = re.compile(r"^(zone\b|nord france|sud france|maghreb|amerique|communaute|etranger)", re.IGNORECASE)
NUMBER = re.compile(r"^[\d.,]+%?$|^%$")
# Printed cells the layout text breaks across lines, proven by the printed rate and the printed totals.
TURNOUT_FIXES: dict[tuple[int, str], dict[str, int]] = {(2012, "Zone 3 (Tunis)"): {"voters": 18620}}


def _tail(line: str, spaced: bool) -> tuple[str, list[str]] | None:
    """Split a table row into its name and its figures, or None when the line is not a data row."""
    if spaced:
        cells = [c for c in re.split(r"\s{2,}", line.strip()) if c]
        i = len(cells)
        while i > 0 and re.fullmatch(r"[\d ]+(?:,\d+)?\s*%?", cells[i - 1]):
            i -= 1
        figures = cells[i:]
        name = " ".join(cells[:i])
    else:
        tokens = line.split()
        i = len(tokens)
        while i > 0 and NUMBER.match(tokens[i - 1]):
            i -= 1
        figures = tokens[i:]
        name = " ".join(tokens[:i])
    return (name, figures) if len(figures) >= 4 else None


def turnout(year: int) -> tuple[list[Turnout], dict[str, int]]:
    """Each constituency's registered voters, voters, and valid votes, and the printed total."""
    text = "\n".join(layout(year))
    heading = list(re.finditer(r"(?i)(r[ée]sultats du scrutin|participation au scrutin)[^\n]*circonscription", text))[
        -1
    ]
    spaced = year >= 2021
    rows: list[Turnout] = []
    total: dict[str, int] = {}
    for line in text[heading.end() :].splitlines():
        if re.match(r"\s*total\b", line, re.IGNORECASE):
            cells = _tail(re.sub(r"(?i)^\s*total\s*", "TOTAL  ", line), spaced)
            assert cells is not None, line
            figures = cells[1]
            numbers = [integer(f) for f in figures if "%" not in f and "," not in f and "." not in f]
            total = {"registered": numbers[0], "voters": numbers[1], "valid": numbers[-1]}
            break
        parsed = _tail(line, spaced)
        if parsed is None:
            continue
        name, figures = parsed
        name = re.sub(r"^\d{1,2}\s+", "", name).strip()
        zone = re.search(r"(?i)zone\s+\w+.*", name)
        if zone:
            name = zone.group(0)
        if spaced:
            ints = [f for f in figures if "%" not in f and not re.search(r",\d", f)]
            registered, voters, valid = (integer(ints[0]), integer(ints[1]), integer(ints[2]))
        else:
            ints = [f for f in figures if not re.search(r"[%.,]", f)]
            fix = TURNOUT_FIXES.get((year, name), {})
            if len(ints) == 2 and "voters" in fix:
                ints.insert(1, str(fix["voters"]))
            registered, voters, valid = integer(ints[0]), integer(ints[1]), integer(ints[-1])
        rows.append(Turnout("", name, registered, voters, valid))
    return rows, total


class Block(NamedTuple):
    page: int
    y: float
    name: str
    seats: int


def _table_span(year: int, number: int) -> tuple[tuple[int, float], tuple[int, float]]:
    """The first heading of a numbered table and the first heading of the next one, as (page, y)."""
    ws = sorted(words(year), key=lambda w: (w.page, w.y, w.x))
    marks: list[tuple[int, float, int]] = []
    for i, w in enumerate(ws):
        if fold(w.text) == "tableau":
            for follow in ws[i + 1 : i + 5]:
                if follow.page == w.page and abs(follow.y - w.y) < 3 and follow.text.isdigit():
                    marks.append((w.page, w.y, int(follow.text)))
                    break
    start = next(m for m in marks if m[2] == number)
    end = next(m for m in marks if m[2] == number + 1 and (m[0], m[1]) > (start[0], start[1]))
    return (start[0], start[1]), (end[0], end[1])


DANGLING = {"independante", "independantes", "liste", "n", "et", "pour", "la", "le", "les", "de", "du", "des", "l"}


def _open(text: str) -> bool:
    """Whether a printed list name is unfinished: an open bracket or quotation mark, or a trailing article."""
    pairs = (("(", ")"), ("«", "»"), ("“", "”"))
    last = fold(text).split()[-1:] or [""]
    closed = text.rstrip().endswith((")", "»", "”"))
    return any(text.count(a) > text.count(b) for a, b in pairs) or (last[0] in DANGLING and not closed)


def _match(counts: list[float], lines: list[list[float]], tolerance: float = 12.0) -> list[int] | None:
    """Pair each seat count with the list line it sits beside, in order, at the least total vertical distance."""
    if not counts:
        return []
    inf = float("inf")
    best: list[list[float]] = [[inf] * len(lines) for _ in counts]
    back: list[list[int]] = [[-1] * len(lines) for _ in counts]
    for i, cy in enumerate(counts):
        running, at = inf, -1
        for j, ys in enumerate(lines):
            if i > 0 and j > 0 and best[i - 1][j - 1] < running:
                running, at = best[i - 1][j - 1], j - 1
            prior = 0.0 if i == 0 else running
            distance = min(abs(ly - cy) for ly in ys)
            if distance <= tolerance and prior < inf:
                best[i][j] = prior + distance
                back[i][j] = at
    end = min(range(len(lines)), key=lambda j: best[-1][j], default=None)
    if end is None or best[-1][end] == inf:
        return None
    path = [end]
    for i in range(len(counts) - 1, 0, -1):
        path.append(back[i][path[-1]])
    return path[::-1]


def elected(year: int) -> tuple[list[Block], list[int]]:
    """The lists that won seats, in printed order, with each constituency's printed seat count."""
    (p0, y0), (p1, y1) = _table_span(year, 1)
    ws = words(year)
    blocks: list[Block] = []
    magnitudes: list[tuple[int, float, int]] = []
    columns: tuple[float, float, float, float, float] | None = None
    for page in range(p0, p1 + 1):
        here = [w for w in ws if w.page == page and (page != p0 or w.y >= y0) and (page != p1 or w.y < y1)]
        listes = [w for w in here if fold(w.text) == "listes"]
        done = [w for w in here if fold(w.text) == "obtenus"]
        due = [w for w in here if fold(w.text) in {"pourvoir", "pouravoir"}]
        if listes and done and due:
            columns = (listes[0].x, done[0].x, due[0].x, max(done[0].y, due[0].y) + 5, 0.0)
        assert columns is not None, (year, page)
        lx, ox, px, top, _ = columns
        star = [w.y for w in here if w.text.startswith("*") and w.x < 100 and w.y > top]
        bottom = min(star) if star else 1e9
        data = [w for w in here if top < w.y < bottom]
        counts = sorted(
            (w.y, int(w.text)) for w in data if w.text.isdigit() and ox - 10 <= w.x < ox + 50 and int(w.text) < 100
        )
        for w in data:
            if w.text.isdigit() and px - 5 <= w.x < px + 45:
                magnitudes.append((page, w.y, int(w.text)))
        parts = sorted(
            ((w.y, w.x, w.text) for w in data if lx - 45 <= w.x < ox - 10 and not (w.text.isdigit() and w.x < lx - 5)),
        )
        lines: list[tuple[float, list[str]]] = []
        cluster: list[tuple[float, float, str]] = []
        for part in [*parts, (1e9, 0.0, "")]:
            if cluster and abs(cluster[0][0] - part[0]) >= 3.5:
                lines.append((cluster[0][0], [t for _, _, t in sorted(cluster, key=lambda c: c[1])]))
                cluster = []
            cluster.append(part)
        lines = [line for line in lines if [t for t in line[1] if t != "(suite)"]]
        lines = [(y, [t for t in ts if t != "(suite)"]) for y, ts in lines]
        counts = sorted(counts)
        groups: list[list[tuple[float, list[str]]]] | None = None
        units: list[list[tuple[float, list[str]]]] = []
        for line in lines:
            if units and _open(" ".join(t for _, ts in units[-1] for t in ts)):
                units[-1].append(line)
            else:
                units.append([line])
        clusters: list[list[tuple[float, list[str]]]] = []
        for line in lines:
            if clusters and line[0] - clusters[-1][-1][0] <= 15:
                clusters[-1].append(line)
            else:
                clusters.append([line])
        starts = _match([cy for cy, _ in counts], [[y for y, _ in unit] for unit in units])
        if year >= 2021 and len(clusters) == len(counts):
            groups = clusters
        elif starts is not None:
            groups = [
                [line for unit in units[a : (starts[i + 1] if i + 1 < len(starts) else len(units))] for line in unit]
                for i, a in enumerate(starts)
            ]
        if not groups:
            clusters: list[list[tuple[float, list[str]]]] = []
            for line in lines:
                if clusters and line[0] - clusters[-1][-1][0] <= 15:
                    clusters[-1].append(line)
                else:
                    clusters.append([line])
            assert len(clusters) == len(counts), (year, page, len(clusters), len(counts))
            groups = clusters
        for group, (cy, seats) in zip(groups, counts, strict=True):
            name = " ".join(" ".join(t) for _, t in group)
            blocks.append(Block(page, cy, re.sub(r"\s+", " ", name).strip(), seats))
    return blocks, [m[2] for m in sorted(magnitudes)]


def constituencies(year: int) -> list[list[Block]]:
    """The elected lists of each constituency, cut where the running seat total reaches the running seat count."""
    if year == 1997:
        return [
            [Block(item["page"], item["y"], item["label"], item["seats"]) for item in c["lists"]]
            for c in digitized()["constituencies"]
        ]
    blocks, sizes = elected(year)
    out: list[list[Block]] = []
    total = list(accumulate(sizes))
    running = 0
    index = 0
    current: list[Block] = []
    for block in blocks:
        current.append(block)
        running += block.seats
        if running > total[index]:
            raise ValueError(f"{year}: seats overrun constituency {index + 1}: {current}")
        if running == total[index]:
            out.append(current)
            current = []
            index += 1
    if current or index != len(sizes):
        raise ValueError(f"{year}: {index} constituencies cut for {len(sizes)} seat counts; remainder {current}")
    return out


class National(NamedTuple):
    name: str
    votes: int
    seats: int | None


def _region(year: int, number: int) -> str:
    """The layout text from a numbered table's first heading to the next table's heading."""
    text = "\n".join(layout(year))
    head = re.search(r"(?i)tableau\s+n°+\s*" + str(number) + r"\b", text)
    assert head, (year, number)
    tail = re.search(
        r"(?i)(tableau\s+n°+\s*" + str(number + 1) + r"\b|annexe\s+" + str(number + 1) + r"\b)", text[head.end() :]
    )
    assert tail, (year, number)
    return text[head.end() : head.end() + tail.start()]


HEADERS = re.compile(
    r"(?i)JOURNAL OFFICIEL|^\s*\d+\s+\w+\s+\d{4}\s*$|PARTIS POLITIQUES|NOMBRE DE VOIX|OBTENUES|ET LISTES|TABLEAU|SUITE|^\s*[A-B]\.|LISTES INDEP"
)


def national_votes(year: int) -> tuple[list[National], int]:
    """The votes of every list nationwide from the proclamation's table of votes, and the printed total."""
    text = _region(year, 2)
    rows: list[National] = []
    total = 0
    adjacent = False
    for line in text.splitlines():
        if re.match(r"^\s*total\b", line, re.IGNORECASE):
            total = integer(re.split(r"\s{2,}", line.strip())[1])
            break
        m = re.match(r"^\s*(\S.*?)\s{2,}(\d[\d ]*?)\s{2,}([\d.,]+)\s*%?\s*$", line)
        if m and not HEADERS.search(line):
            rows.append(National(re.sub(r"\s+", " ", m.group(1)).strip(), integer(m.group(2)), None))
            adjacent = True
        elif adjacent and line.strip() and not HEADERS.search(line) and not re.search(r"\d{3}", line):
            rows[-1] = rows[-1]._replace(name=rows[-1].name + " " + line.strip())
        else:
            adjacent = False
    return rows, total


def winners(year: int) -> list[National]:
    """The lists that won seats, with votes and seats, from the proclamation's ranked table (2021 and 2026)."""
    out: list[National] = []
    if year == 2017:
        text = "\n".join(layout(2017))
        start = text.index("selon l’ordre ci-après")
        for line in text[start : text.index("Troisièmement", start)].splitlines():
            m = re.match(r"^\s*(\S.*?)\s{2,}(\d+)\s{2,}(\d[\d ]*)\s*$", line)
            if m and not re.search(r"(?i)nombre", line):
                out.append(National(re.sub(r"\s+", " ", m.group(1)), integer(m.group(3)), int(m.group(2))))
        return out
    if year == 2026:
        for line in "\n".join(layout(2026)[6:9]).splitlines():
            m = re.match(r"^\s*(\d+)\s+(\S.*?)\s{2,}(\d+)\s+(\d+)\s*$", line)
            if m:
                out.append(National(re.sub(r"\s+", " ", m.group(2)), int(m.group(3)), int(m.group(4))))
        return out
    ws = [w for w in words(2021) if 6 <= w.page <= 8 and w.y > 100]
    for page in (6, 7, 8):
        here = [w for w in ws if w.page == page]
        seats = sorted((w.y, int(w.text)) for w in here if w.text.isdigit() and 460 <= w.x < 500)
        if page == 8:
            cut = min((w.y for w in words(2021) if w.page == 8 and fold(w.text) == "troisiemement"), default=1e9)
            seats = [s for s in seats if s[0] < cut]
        names: list[tuple[float, list[str]]] = []
        for w in sorted(here, key=lambda w: (w.y, w.x)):
            if w.x < 300 and w.y <= (seats[-1][0] + 30 if seats else 0):
                if names and abs(names[-1][0] - w.y) < 3.5:
                    names[-1][1].append(w.text)
                else:
                    names.append((w.y, [w.text]))
        for i, (sy, count) in enumerate(seats):
            nxt = seats[i + 1][0] if i + 1 < len(seats) else 1e9
            label = " ".join(" ".join(t) for y, t in names if sy - 3 <= y < nxt - 3)
            votes = integer("".join(w.text for w in here if abs(w.y - sy) < 3 and 300 <= w.x < 400))
            out.append(National(re.sub(r"\s+", " ", label).strip(), votes, count))
    return out


PARTY_FIRST = {"front", "rassemblement", "mouvement", "harakat", "tajamoua", "voix", "jil", "alliance"}
ALIASES = {
    "haraket moudjtamaa es silm": "hms",
    "harraket moudjtema essilm": "hms",
    "parti harraket moudjtema essilm": "hms",
    "ahd 54": "ahd",
    "parti du front de liberation nationale": "front de liberation nationale",
    "mouvement ennahda": "mouvement nahda",
    "parti nat pour la solidarite et le developpement": "parti national pour la solidarite et le developpement",
    "haraket moudjtemaa es silm": "hms",
    "harraket moudjtema es silm": "hms",
    "harraket moudjtemaa es silm": "hms",
    "mouvement de la societe pour la paix": "hms",
    "front el moustakbel": "front el moustakbal",
    "mouvement national el bina": "harakat el binaa el watani",
    "harakat el binaa el watani": "harakat el binaa el watani",
    "parti el fadjr el djadid": "parti el fedjr el jadid",
    "parti de liberte et de la justice": "parti de la liberte et de la justice",
    "front pour la justice et le developpement el adala": "front de la justice et du developpement",
    "front pour la justice et le developpement": "front de la justice et du developpement",
    "front de la justice et du devellopement": "front de la justice et du developpement",
    "mouvement de la nahda": "mouvement nahda",
    "national algerien front": "front national algerien",
    "alliances hms": "alliance hms",
    "rassemblement national republicain": "rassemblement patriotique republicain",
    "rassemblement national patriotique republicain": "rassemblement patriotique republicain",
    "l alliance nationale republicaine": "alliance nationale republicaine",
    "mouvement infitah": "mouvement el infitah",
    "mouvement national de l esperance": "mouvement national d esperance",
}
DISPLAY = {
    "hms": "Harakat Moudjtamaa Es-Silm (Movement of Society for Peace)",
    "harakat el binaa el watani": "Harakat El Binaa El Watani (National Building Movement)",
    "front de la justice et du developpement": "Front pour la Justice et le Développement (El Adala)",
}


def independent(label: str) -> bool:
    """Whether a printed list label names an independent list rather than a party."""
    text = fold(label)
    return bool(re.search(r"\b(independante|independant|independants)\b", text)) and "des independants pour" not in text


def party_key(label: str) -> str:
    """The key of the party a printed list label names; spellings and the Parti prefix fold together."""
    tokens = fold(label).split()
    while tokens and tokens[0].isdigit():
        tokens.pop(0)
    if len(tokens) > 2 and tokens[0] == "parti" and tokens[1] in PARTY_FIRST:
        tokens.pop(0)
    if len(tokens) > 1 and tokens[0] in {"le", "la", "les"}:
        tokens.pop(0)
    text = " ".join(tokens)
    return ALIASES.get(text, text)


# ---------------------------------------------------------------- the elections

FIXES: dict[tuple[int, str, int], tuple[str, str]] = {
    # year, constituency code, position of the block in the constituency -> printed label, printed label replaced
    (2021, "19", 2): ("RASSEMBLEMENT NATIONAL DEMOCRATIQUE", "FRONT DE LIBERATION NATIONALE"),
}
# National-table lists with no matching block in the annexed table of elected candidates, as the proclamation prints them.
DISCREPANCIES: dict[int, list[str]] = {
    2026: ["Liste indépendante Conglomération des libres - Sidi Bel Abbès"],
}
# Disclosures of the places where the module stores a figure other than the one printed in a table cell.
CONTEST_NOTES: dict[tuple[int, str], str] = {
    (2012, "2012:3"): (
        "The printed table breaks the voters cell of this row across lines. The voters, 18,620, are the figure that "
        "the printed rate of 30.73% and the printed national total of voters both require."
    ),
    (2017, "25"): (
        "The matrix of votes by list prints 3,569 votes for the Front El Moustakbal here, but its printed rate of "
        "3.3561% and the party's printed national total of 265,667 votes both give 3,469, which is stored."
    ),
    (2017, "39"): (
        "The matrix of votes by list prints 2,552 votes for the Mouvement de l’Entente Nationale here, but its "
        "printed rate of 2.1234% and the party's printed national total of 51,960 votes both give 2,752, which is "
        "stored."
    ),
    (2021, "19"): (
        "The table of elected candidates names the Front de libération nationale twice here, with six and four "
        "seats. The proclamation's national table gives the Front 98 seats and the Rassemblement national "
        "démocratique 58, and with the second block read as the Front the table of elected candidates gives 102 "
        "and 54, so the four-seat block is recorded as the Rassemblement national démocratique."
    ),
    (2026, "22"): (
        "The proclamation's ranked national table credits the independent list Conglomération des libres, with "
        "23,216 votes, with three seats in this constituency, but its table of elected candidates shows one seat "
        "for a list named Takatoul El Ahrar here and one each for lists of that name in Bordj Bou Arréridj and "
        "Aïn Defla. The seats stored here follow the table of elected candidates, and the list's votes are not "
        "stored."
    ),
    (2026, "34"): (
        "The table of elected candidates shows one seat here for a list named Takatol Al Ahrar, but the proclamation's "
        "ranked national table has no such list; it credits the independent list Conglomération des libres in Sidi "
        "Bel Abbès with three seats. The seat is stored as the table of elected candidates shows it."
    ),
    (2026, "44"): (
        "The table of elected candidates shows one seat here for a list named Takatol Al Ahrar, but the proclamation's "
        "ranked national table has no such list; it credits the independent list Conglomération des libres in Sidi "
        "Bel Abbès with three seats. The seat is stored as the table of elected candidates shows it."
    ),
}
SMALL = {"de", "des", "du", "la", "le", "les", "et", "pour", "aux", "à", "en"}


def title(label: str) -> str:
    text = re.sub(r"\s+", " ", label).strip()
    if not text.isupper():
        return text
    out = []
    for i, word in enumerate(text.lower().split(" ")):
        word = re.sub(r"^(l|d)[’']", lambda m: m.group(0).lower(), word)
        out.append(word if (i and word in SMALL) else word[:1].upper() + word[1:])
    return " ".join(out)


class Place(NamedTuple):
    code: str
    kind: str
    name: str
    registered: int
    voters: int
    valid: int


ZONES: dict[int, list[str]] = {
    1997: [
        "Zone 1: France Nord",
        "Zone 2: France Sud",
        "Zone 3: Reste de l’Europe",
        "Zone 4: Maghreb et Afrique",
        "Zone 5: Reste du monde arabe",
        "Zone 6: Amérique, Océanie et Asie",
    ],
    2002: [
        "Zone 1 (Paris)",
        "Zone 2 (Marseille)",
        "Zone 3 (Berlin)",
        "Zone 4 (Tunis)",
        "Zone 5 (Le Caire)",
        "Zone 6 (Washington)",
    ],
    2007: [
        "Zone 1 (Paris)",
        "Zone 2 (Marseille)",
        "Zone 3 (Berlin)",
        "Zone 4 (Tunis)",
        "Zone 5 (Le Caire)",
        "Zone 6 (Washington)",
    ],
    2012: ["Zone 1 (Paris)", "Zone 2 (Marseille)", "Zone 3 (Tunis)", "Zone 4 (Washington)"],
    2017: [
        "Nord France (Paris)",
        "Sud France (Marseille)",
        "Maghreb-Mashrek, Afrique, Asie, Océanie (Tunis)",
        "Amérique, reste de l’Europe (Washington)",
    ],
    2021: [
        "Zone I: Nord France (siège Paris)",
        "Zone II: Sud France (siège Marseille)",
        "Zone III: Maghreb-Mashrek, Afrique, Asie, Océanie (siège Tunis)",
        "Zone IV: Amérique, reste de l’Europe (siège Washington)",
    ],
    2026: [f"Zone {n}" for n in range(1, 9)],
}
WILAYAS = 69


@functools.cache
def places(year: int) -> list[Place]:
    if year == 1997:
        data = digitized()
        found = data["constituencies"]
        total = data["totals"]
        assert sum(c["registered"] for c in found) == total["registered"]
        assert sum(c["voters"] for c in found) == total["voters"]
        assert sum(c["valid"] for c in found) == total["valid"]
        assert sum(c["abstentions"] for c in found) == total["abstentions"]
        assert all(c["registered"] - c["voters"] == c["abstentions"] and c["valid"] <= c["voters"] for c in found)
        out = []
        for i, c in enumerate(found):
            home = i < 48
            name = wilaya_names()[c["code"]] if home else ZONES[year][i - 48]
            out.append(Place(c["code"], "wilaya" if home else "zone", name, c["registered"], c["voters"], c["valid"]))
        return out
    rows, total = turnout(year)
    abroad = [r for r in rows if ABROAD.match(r.name) or r.name.startswith("Zone")]
    home = rows[: len(rows) - len(abroad)]
    assert rows[len(home) :] == abroad and len(abroad) == len(ZONES[year]), (year, len(home), len(abroad))
    assert sum(r.registered for r in rows) == total["registered"], (year, "registered")
    assert sum(r.voters for r in rows) == total["voters"], (year, "voters")
    assert sum(r.valid for r in rows) == total["valid"], (year, "valid")
    assert all(r.valid <= r.voters <= r.registered for r in rows), year
    out = [Place(f"{i:02d}", "wilaya", r.name, r.registered, r.voters, r.valid) for i, r in enumerate(home, 1)]
    out += [
        Place(f"{year}:{i}", "zone", ZONES[year][i - 1], r.registered, r.voters, r.valid)
        for i, r in enumerate(abroad, 1)
    ]
    return out


@functools.cache
def wilaya_names() -> dict[str, str]:
    """Each wilaya's name as the latest proclamation prints it."""
    return {p.code: title(p.name) for p in places(2026) if p.kind == "wilaya"}


def place_name(year: int, place: Place) -> str:
    return wilaya_names()[place.code] if place.kind == "wilaya" else place.name


def _place_fold(year: int, place: Place) -> str:
    return fold(place.name) if place.kind == "wilaya" else fold(place.name.split(":")[0].split(" (")[0])


class Entry(NamedTuple):
    label: str
    party: str | None
    votes: int | None
    fixed: bool


def _similar(a: str, b: str) -> float:
    def strip(t: str) -> str:
        return re.sub(r"^liste independante\s+", "", t)

    a, b = strip(a), strip(b)
    if min(len(a), len(b)) >= 8 and (a.startswith(b) or b.startswith(a)):
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


@functools.cache
def lists(year: int) -> list[list[Entry]]:
    """Each constituency's lists that won seats: label, party key (None for independent lists), votes when known."""
    cons = constituencies(year)
    here = places(year)
    assert len(cons) == len(here), year
    if year == 1997:
        return [
            [Entry(b.name, None if independent(b.name) else party_key(b.name), None, False) for b in group]
            for group in cons
        ]
    seats: Counter[str] = Counter()
    local: list[tuple[str, Place | None, National]] = []
    if year >= 2021:
        entries = winners(year)
        names = [(_place_fold(year, p), p) for p in here]
        parties: set[str] = set()
        for e in entries:
            f = fold(e.name)
            hit = [(n, p) for n, p in names if f.endswith(" " + n) and len(f) > len(n) + 1]
            if year == 2021 and "communaute" in f:
                hit = [("", here[-1])]
            if year == 2026 and not f.startswith("liste independante"):
                hit = []
            if hit:
                n, p = max(hit, key=lambda h: len(h[0]))
                local.append((f.removesuffix(n).strip(), p, e))
            else:
                parties.add(party_key(e.name))
        for e in entries:
            seats[party_key(e.name)] += e.seats or 0
    else:
        table, _ = national_votes(year)
        parties = {party_key(n.name) for n in table if not independent(n.name)}
        if year == 2017:
            local = [(fold(e.name), None, e) for e in winners(year) if party_key(e.name) not in parties]
    blocks: list[tuple[int, int, str, int]] = []
    out: list[list[Entry]] = [[] for _ in cons]
    for pos, (group, place) in enumerate(zip(cons, here, strict=True)):
        for index, block in enumerate(group):
            label = block.name
            fix = FIXES.get((year, place.code, index))
            if fix is not None:
                assert fold(label) == fold(fix[1]), (year, place.code, label)
                label = fix[0]
            key = party_key(label)
            if key in parties and not independent(label):
                votes = party_votes().get((place.code, key)) if year == 2017 else None
                out[pos].append(Entry(label, key, votes, fix is not None))
            else:
                blocks.append((pos, index, label, block.seats))
                out[pos].append(Entry(label, None, None, False))
    scored = sorted(
        (
            (_similar(fold(label), base), i, j)
            for i, (pos, _, label, n) in enumerate(blocks)
            for j, (base, place, e) in enumerate(local)
            if e.seats == n and (place is None or place.code == here[pos].code)
        ),
        reverse=True,
    )
    taken: set[int] = set()
    used: set[int] = set()
    for score, i, j in scored:
        if score >= (0.6 if year >= 2021 else 0.75) and i not in taken and j not in used:
            taken.add(i)
            used.add(j)
            pos, index, label, _ = blocks[i]
            out[pos][index] = Entry(label, None, local[j][2].votes, False)
    unmatched = [local[j][2].name for j in range(len(local)) if j not in used]
    assert unmatched == DISCREPANCIES.get(year, []), (year, unmatched)
    if year >= 2021:
        mine: Counter[str] = Counter()
        for row, group in zip(out, cons, strict=True):
            for entry, block in zip(row, group, strict=True):
                if entry.party:
                    mine[entry.party] += block.seats
        for key, n in mine.items():
            assert seats[key] == n, (year, key, n, seats[key])
    return out


# ---------------------------------------------------------------- packages


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def issue_source(year: int) -> str:
    return key("source", year)


APN = key("body", "apn")
SYSTEM_1997 = key("electoral_system", "apn", 1997)
SYSTEM_2021 = key("electoral_system", "apn", 2021)
PUBLISHER = "Secrétariat général du Gouvernement, Journal officiel de la République algérienne (JORADP)"
SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        SYSTEM_1997,
        date(1997, 3, 6),
        date(2021, 3, 10),
        {
            "formula": "hare",
            "threshold": {"percent": 5, "level": "list", "scope": "contest"},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "Seats are allocated in each constituency, a wilaya or a zone of the community abroad, by the largest "
            "remainder method. Lists that win less than 5% of the valid votes in the constituency are set aside, and "
            "the electoral quotient is the votes of the remaining lists divided by the seats. Lists are closed. A seat "
            "that falls vacant passes to the next candidate on the list. Women's seats under the 2012 organic law are "
            "not modeled."
        ),
    ),
    (
        SYSTEM_2021,
        date(2021, 3, 10),
        None,
        {
            "formula": "hare",
            "threshold": {"percent": 5, "level": "list", "scope": "contest"},
            "lists": {"order": "open", "vacancy": "next_on_list"},
        },
        (
            "Since Ordinance 21-01 of 10 March 2021, seats are allocated in each constituency by the largest remainder "
            "method, among the lists that win at least 5% of the valid votes in the constituency, and voters rank the "
            "candidates of the list they choose with a preferential vote, without crossing between lists. A seat that "
            "falls vacant passes to the next candidate on the list."
        ),
    ),
]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Algeria's wilayas, as the proclamations of the People's National Assembly elections name them, and the "
        "zones of the community abroad that each election's proclamation defines",
    )
    p.external |= {ALGERIA, TERRITORY}
    p.add("scheme", code="dz.wilaya", polity_id=ALGERIA, kind="administrative", rank=1, noun="wilaya")
    p.add("scheme", code="dz.zone", polity_id=ALGERIA, kind="electoral_district", rank=1, noun="zone abroad")
    for code, name in wilaya_names().items():
        id = area("dz.wilaya", code)
        p.add("area", id=id, scheme_code="dz.wilaya")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, name, "fr", "official")
    for year in ELECTIONS:
        for place in places(year):
            if place.kind == "zone":
                id = area("dz.zone", place.code)
                p.add("area", id=id, scheme_code="dz.zone")
                p.add("area_code", area_id=id, code=place.code)
                p.add("area_parent", area_id=id, parent_id=TERRITORY)
                p.name(id, f"{place.name} ({year})", "fr", "official")
    return p


def party(pkey: str) -> str:
    return key("party", pkey)


@functools.cache
def party_labels() -> dict[str, str]:
    """Each party key and the name to show for it: the earliest mixed-case print, or a title-cased one."""
    found: dict[str, str] = {}
    for year in sorted(ELECTIONS):
        for row in lists(year):
            for entry in row:
                if entry.party is not None and entry.party not in found:
                    found[entry.party] = entry.label
    for e in national_votes(2017)[0]:
        if not independent(e.name):
            found.setdefault(party_key(e.name), e.name)
    return {
        k: DISPLAY.get(k) or title(re.sub(r"^PARTI (?=FRONT|RASSEMBLEMENT|MOUVEMENT|HARAKAT|VOIX)", "", v))
        for k, v in found.items()
    }


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The People's National Assembly, its electoral systems, and the parties whose lists won seats, from the "
        "proclamations of the results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(ALGERIA)
    for year, (issue, day, _) in ELECTIONS.items():
        p.add(
            "source",
            id=issue_source(year),
            kind="official_body",
            publisher=PUBLISHER,
            title=f"Journal officiel n° {issue} of {year}: proclamation of the results of the {day:%-d %B %Y} election "
            "of the members of the People's National Assembly",
            url=issue_url(year),
            license="Official publication of the Algerian State; no reuse licence stated",
            license_url="https://www.joradp.dz/HFR/Index.htm",
            attribution="Journal officiel de la République algérienne démocratique et populaire, joradp.dz",
        )
        p.artifact(raw.cached(SOURCE, issue_url(year)), issue_source(year))
    p.add("body", id=APN, polity_id=ALGERIA, kind="lower_house")
    p.name(APN, "People's National Assembly", "en", "official")
    p.name(APN, "Assemblée populaire nationale", "fr", "native", False)
    p.name(APN, "المجلس الشعبي الوطني", "ar", "native", False)
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=ALGERIA, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for pkey, label in sorted(party_labels().items()):
        id = party(pkey)
        p.add("party", id=id, kind="party", polity_id=ALGERIA)
        p.add("external_id", entity_id=id, scheme="dz.joradp.party", value=pkey)
        p.name(id, label, "fr", "official")
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/apn-{year}" for year in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in ELECTIONS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def national_note(year: int) -> str:
    rows = places(year)
    lines = [
        (
            f"National totals, as the proclamation prints them: {sum(r.registered for r in rows):,} registered "
            f"voters, {sum(r.voters for r in rows):,} voters, and {sum(r.valid for r in rows):,} valid votes, adding "
            f"up the {len(rows)} constituencies."
        ),
        "",
    ]
    if year == 1997:
        lines += ["| List | Votes nationwide | Seats |", "|---|---:|---:|"]
        lines += [f"| {item['label']} | {item['votes']:,} | {item['seats']} |" for item in digitized()["nationwide"]]
    elif year >= 2021:
        lines += ["| List that won seats | Votes | Seats |", "|---|---:|---:|"]
        lines += [f"| {title(e.name)} | {e.votes:,} | {e.seats} |" for e in winners(year)]
    else:
        table, total = national_votes(year)
        lines += ["| List | Votes nationwide |", "|---|---:|"]
        lines += [f"| {title(e.name)} | {e.votes:,} |" for e in table]
        lines += [f"| Total | {total:,} |"]
    return "\n".join(lines)


def election(year: int) -> Package:
    _, day, seats = ELECTIONS[year]
    p = Package(
        f"{SOURCE}/apn-{year}",
        SOURCE,
        f"The {year} People's National Assembly election by constituency, from the Journal officiel",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ALGERIA)
    p.uses(raw.cached(SOURCE, issue_url(year)))
    p.accept(
        "accounting.votes_valid",
        "The proclamation does not print the independent lists' votes by constituency, so a constituency's list votes "
        "fall short of its valid votes by what those lists won there."
        if year == 2017
        else "The proclamation prints list votes nationwide, not by constituency, and names only the lists that won "
        "seats, so a constituency's list votes fall short of its valid votes by what the other lists won there.",
    )
    source = issue_source(year)
    eid = key("election", year)
    p.add("election", id=eid, polity_id=ALGERIA, kind="general", date_from=day, date_to=day, status="held")
    p.name(eid, f"{year} Algerian parliamentary election", "en", "official")
    p.name(eid, f"Élection des membres de l’Assemblée populaire nationale de {year}", "fr", "native", False)
    path = f"dz/{day}-general"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    p.add("note", id=key("note", eid), entity_id=eid, kind="context", body_md=national_note(year), lang="en")
    system = next(id for id, since, until, _, _ in SYSTEMS if since <= day and (until is None or day < until))
    cons = constituencies(year)
    rows = lists(year)
    assert sum(b.seats for g in cons for b in g) == seats, year
    method = (
        "Registered voters, voters, and valid votes come from the proclamation's table of results by constituency, "
        "and the lists that won seats, with their seats, from its table of elected candidates. Voters are the "
        "electors who voted, spoiled ballots included."
    )
    if year == 1997:
        method += (
            " The issue is a scan with no text layer, so these figures were read from the page images: the table of "
            "results by constituency on page 21 and the elected lists on pages 5 to 19. Each constituency's "
            "registered voters less its voters equal its printed abstentions, the constituencies add up to the "
            "printed national totals, and each list's seats add up to its printed national seats. Two seat counts "
            "that the scan does not show, those of the Union pour la démocratie et les libertés in Mascara and of "
            "the Rassemblement pour la culture et la démocratie in Zone 6, are the single seat that the national "
            "totals leave them. The text of the proclamation calls the party with 36,374 votes and one seat the "
            "Parti social démocrate, and its tables call it the Parti social libéral."
        )
    if year == 2017:
        gap = (
            "The proclamation prints the votes of each party list in each constituency, including the lists that won "
            "no seat. It prints the independent lists only as one national total and, for each independent list that "
            "won a seat, as that list's own total, so independent lists that won no seat are missing from this result "
            "set."
        )
    elif year >= 2021:
        gap = (
            "The proclamation prints the votes of each party list nationwide, not in each constituency, and names "
            "only the lists that won seats. The votes of lists that won no seat, and the votes of each party list in "
            "this constituency, are not published, so they are missing from this result set. Local lists, which ran "
            "here alone, show their votes."
        )
    else:
        gap = (
            "The proclamation prints the votes of each list nationwide, not in each constituency, and names only the "
            "lists that won seats here. The votes of every list in this constituency, those of the lists that won no "
            "seat included, are not published, so they are missing from this result set."
        )
    for place, group, row in zip(places(year), cons, rows, strict=True):
        name = place_name(year, place)
        contest = key("contest", year, place.code)
        scheme = "dz.wilaya" if place.kind == "wilaya" else "dz.zone"
        magnitude = sum(b.seats for b in group)
        p.add(
            "contest",
            id=contest,
            election_id=eid,
            kind="list_allocation",
            body_id=APN,
            district_area_id=area(scheme, place.code),
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        label = name if place.kind == "wilaya" else f"{place.name}"
        p.name(contest, label, "fr", "official")
        p.add("slug", path=f"{path}/{changeset.slug(label)}", entity_id=contest, is_current=True)
        unit = key("unit", year, place.code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ALGERIA,
            scheme_code=scheme,
            code=f"{day}:{place.code}",
            area_relation="equals",
            area_id=area(scheme, place.code),
            kind="standard",
        )
        result_set = key("result_set", year, place.code)
        p.add(
            "result_set",
            id=result_set,
            election_id=eid,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set, "method"),
            entity_id=result_set,
            kind="methodology",
            body_md=method,
            lang="en",
        )
        p.add("note", id=key("note", result_set, "gap"), entity_id=result_set, kind="caveat", body_md=gap, lang="en")
        if (year, place.code) in CONTEST_NOTES:
            p.add(
                "note",
                id=key("note", result_set, "fix"),
                entity_id=result_set,
                kind="caveat",
                body_md=CONTEST_NOTES[(year, place.code)],
                lang="en",
            )
        for measure, value in ((2, place.registered), (3, place.voters), (4, place.valid)):
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
        for index, (block, entry) in enumerate(zip(group, row, strict=True), 1):
            option = key("option", year, place.code, index)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(entry.party) if entry.party else None,
                ballot_position=None,
                ballot_label=title(entry.label),
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=block.seats)
            if entry.votes is not None:
                _votes(p, result_set, unit, option, entry.votes)
        if year == 2017:
            won = {entry.party for entry in row}
            for (code, pkey), votes in sorted(party_votes().items()):
                if code == place.code and pkey not in won:
                    option = key("option", year, place.code, f"lost-{pkey}")
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party(pkey),
                        ballot_position=None,
                        ballot_label=party_labels()[pkey],
                    )
                    _votes(p, result_set, unit, option, votes)
    return p


def _votes(p: Package, result_set: str, unit: str, option: str, votes: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=1,
        count_round=0,
        flags=0,
        value=votes,
    )


# Counts the matrix prints that disagree with their own printed rate; each is proven by the printed rate and by the
# party's printed nationwide total.
VOTE_FIXES: dict[tuple[str, str], int] = {
    ("25", "front el moustakbal"): 3469,
    ("39", "mouvement de l entente nationale"): 2752,
}
NUMBER_CELL = re.compile(r"^\d+(,\d+)?$")
ZONE_HEADS = {"nord": "2017:1", "sud": "2017:2", "maghreb": "2017:3", "amerique": "2017:4"}


def _columns(year: int, here: list[Word], top: float) -> list[tuple[float, str]]:
    """The constituency each column of a page of the matrix belongs to, with the column's left edge."""
    names = {"".join(sorted(re.findall(r"[a-z0-9]", fold(p.name)))): p.code for p in places(year) if p.kind == "wilaya"}
    names["".join(sorted(re.findall(r"[a-z0-9]", "sidi bel abbes")))] = names[
        "".join(sorted(re.findall(r"[a-z0-9]", "sidi bel abbes")))
    ]
    heads = sorted(
        (
            w
            for w in here
            if not NUMBER_CELL.match(w.text) and w.y < top - 6 and w.x >= 100 and not (w.y < 113 and w.page == 59)
        ),
        key=lambda w: w.x,
    )
    groups: list[list[Word]] = []
    for w in heads:
        if groups and w.x - groups[-1][-1].x < 14:
            groups[-1].append(w)
        else:
            groups.append([w])
    out: list[tuple[float, str]] = []
    for group in groups:
        text = fold(" ".join(w.text for w in group))
        if text.replace(" ", "") == "sbabbes":
            text = "sidi bel abbes"
        text = re.sub(r"^b b\b", "bordj bou", text)
        code = names.get("".join(sorted(re.findall(r"[a-z0-9]", text))))
        zone = next((v for k, v in ZONE_HEADS.items() if text.startswith(k)), None)
        if code or zone:
            out.append((min(w.x for w in group), code or str(zone)))
    return out


@functools.cache
def party_votes(year: int = 2017) -> dict[tuple[str, str], int]:
    """Each party's votes in each constituency, from the 2017 proclamation's matrix of party lists by constituency."""
    ws = words(year)
    pages = sorted(
        {
            w.page
            for w in ws
            if fold(w.text) == "tableau" and any(v.page == w.page and abs(v.y - w.y) < 3 and v.text == "5" for v in ws)
        }
    )
    valid = {p.code: p.valid for p in places(year)}
    out: dict[tuple[str, str], int] = {}
    for page in pages:
        here = [w for w in ws if w.page == page and w.y > 100]
        cells = [w for w in here if NUMBER_CELL.match(w.text) and w.x >= 100]
        top = min(w.y for w in cells) - 4
        columns = _columns(year, here, top)
        labels = sorted(
            (w for w in here if w.x < 140 and w.y > top - 6 and not (NUMBER_CELL.match(w.text) and w.x >= 100)),
            key=lambda w: (w.y, w.x),
        )
        lines: list[tuple[float, list[str]]] = []
        row: list[Word] = []
        for w in [*labels, Word(0, 0.0, 1e9, "")]:
            if row and abs(row[0].y - w.y) >= 3:
                lines.append((row[0].y, [v.text for v in sorted(row, key=lambda v: v.x)]))
                row = []
            row.append(w)
        blocks: list[tuple[float, list[str]]] = []
        for y, tokens in lines:
            if blocks and y - blocks[-1][0] <= 11.5:
                blocks[-1] = (y, [*blocks[-1][1], *tokens])
            else:
                blocks.append((y, tokens))
        starts = [lines_y for lines_y, _ in _block_starts(lines)]
        by_column: dict[str, list[Word]] = {}
        for cell in cells:
            column = min(columns, key=lambda c: abs(cell.x - (c[0] - 5)), default=None)
            assert column is not None and abs(cell.x - (column[0] - 5)) < 14, (page, cell, columns)
            by_column.setdefault(column[1], []).append(cell)
        for code, found in by_column.items():
            found.sort(key=lambda w: w.y)
            assert len(found) % 2 == 0, (page, code, found)
            for rate, count in zip(found[::2], found[1::2], strict=True):
                assert "," in rate.text and 8 < count.y - rate.y < 12, (page, code, rate, count)
                owner = [(y, t) for y, t in _block_starts(lines) if -4 <= rate.y - y <= 2.5]
                assert len(owner) == 1, (page, rate, owner)
                key_ = (code, party_key(owner[0][1]))
                assert key_ not in out, key_
                amount = integer(count.text)
                if abs(amount / valid[code] * 100 - float(rate.text.replace(",", "."))) >= 0.01:
                    amount = VOTE_FIXES[key_]
                    assert abs(amount / valid[code] * 100 - float(rate.text.replace(",", "."))) < 0.01, key_
                out[key_] = amount
        assert starts is not None
    return out


def _block_starts(lines: list[tuple[float, list[str]]]) -> list[tuple[float, str]]:
    """The first line's top edge and the full text of each printed row label."""
    blocks: list[tuple[float, float, list[str]]] = []
    for y, tokens in lines:
        if blocks and y - blocks[-1][1] <= 11.5:
            blocks[-1] = (blocks[-1][0], y, [*blocks[-1][2], *tokens])
        else:
            blocks.append((y, y, tokens))
    return [(first, " ".join(tokens)) for first, _, tokens in blocks]
