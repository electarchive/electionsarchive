"""Croatia's local elections of 2001, 2005, and 2009, read from the State Electoral Commission's county reports.

The commission's archive publishes these elections as one typeset PDF per county (Informacija o rezultatima izbora):
for each county assembly, the Zagreb City Assembly, each town and municipal council, and from 2009 each mayoral and
župan contest with its runoffs, the report prints the contest's totals, each list's or candidate's votes, and the seats
and members, or the executive, declared elected. Every figure is read from the text layer with its page and box.

The 2001 reports set much of their text in embedded fonts whose codes are shifted from ASCII by 29, with the
Croatian letters on a few other codes, so their text layer reads as noise; each such span is decoded back to the
printed text here. Figures are set in Helvetica throughout and are read as printed. The 2009 reports print Đ and đ
as ð and ñ.
"""

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path

import polars as pl
import pymupdf

from ea import raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.hr import izbori as z
from ea.sources.hr import lokalni as lk

SOURCE = lk.SOURCE

PLAIN = ("Helvetica", "Arial", "Times", "Courier", "Verdana", "Tahoma")
GLYPHS = {
    "√": "Ć",
    "ⁿ": "ć",
    "Γ": "Š",
    "π": "š",
    "Σ": "Ž",
    "σ": "ž",
    "²": "Č",
    "■": "č",
    "\x08": "Đ",
}
DPI = 100


def shifted(text: str) -> str:
    out = []
    for ch in text:
        if ch in GLYPHS:
            out.append(GLYPHS[ch])
        elif ch != " " and 0x03 <= ord(ch) <= 0x7E - 29:
            out.append(chr(ord(ch) + 29))
        else:
            out.append(ch)
    return "".join(out)


@dataclass(frozen=True)
class Word:
    text: str
    page: int  # 1-based
    box: tuple[float, float, float, float]  # pixels of the page rendered at 100 dpi


@dataclass(frozen=True)
class Line:
    page: int
    y: float
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        """The words joined with one space, or three where the printed gap is wide, as pdftotext's layout mode does."""
        out = ""
        last = None
        for w in self.words:
            if last is not None:
                out += "   " if w.box[0] - last > 12 else " "
            out += w.text
            last = w.box[2]
        return out


def _spans(page: pymupdf.Page) -> list[tuple[str, tuple[float, float, float, float], bool]]:
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                plain = span["font"].startswith(PLAIN)
                chars = span["chars"]
                for ch in chars:
                    text = ch["c"] if plain else shifted(ch["c"])
                    out.append((text, tuple(ch["bbox"]), plain))
    return out


@cache
def lines(path: Path) -> tuple[Line, ...]:
    """Every printed line of a report, top to bottom on each page, split into words with their boxes."""
    doc = pymupdf.open(path)
    out: list[Line] = []
    scale = DPI / 72
    for number in range(1, doc.page_count + 1):
        page = doc[number - 1]
        chars = [(t, b) for t, b, _ in _spans(page) if t]
        rows: list[list[tuple[str, tuple[float, float, float, float]]]] = []
        centers: list[float] = []
        for text, box in sorted(chars, key=lambda c: ((c[1][1] + c[1][3]) / 2, c[1][0])):
            middle = (box[1] + box[3]) / 2
            if centers and abs(middle - centers[-1]) <= 3.5:
                rows[-1].append((text, box))
            else:
                rows.append([(text, box)])
                centers.append(middle)
        for row, middle in zip(rows, centers, strict=True):
            row.sort(key=lambda c: c[1][0])
            words: list[Word] = []
            current = ""
            x0 = y0 = x1 = y1 = 0.0
            last = None
            for text, box in row:
                gap = box[0] - last if last is not None else 0
                if text.isspace() or (current and gap > 2.0):
                    if current:
                        words.append(Word(fix(current), number, (x0 * scale, y0 * scale, x1 * scale, y1 * scale)))
                        current = ""
                    if text.isspace():
                        last = box[2]
                        continue
                if not current:
                    x0, y0, x1, y1 = box
                current += text
                x1, y0, y1 = max(x1, box[2]), min(y0, box[1]), max(y1, box[3])
                last = box[2]
            if current:
                words.append(Word(fix(current), number, (x0 * scale, y0 * scale, x1 * scale, y1 * scale)))
            if words:
                out.append(Line(number, middle * scale, tuple(words)))
    return tuple(out)


def sizes(path: Path) -> dict[int, tuple[int, int]]:
    doc = pymupdf.open(path)
    out = {}
    for n in range(1, doc.page_count + 1):
        rect = doc[n - 1].rect
        out[n] = (round(rect.width * DPI / 72), round(rect.height * DPI / 72))
    return out


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


# The reports' sections: one per council, assembly, or executive contest and round.

NUMBER = re.compile(r"^\d+(?:\.\d{3})*$")
PERCENT = re.compile(r"^\d+(?:,\d+)?%$")
HEAD = re.compile(
    r"^(GRADSKO VIJEĆE|OPĆINSKO VIJEĆE|ŽUPANIJSKA SKUPŠTINA|GRADSKA SKUPŠTINA|GRADONAČELNIK|NAČELNIK|ŽUPAN)\b"
)
ROUND = re.compile(r"^\((prvi|drugi|treći) krug", re.IGNORECASE)
ROUNDS = {"prvi": 1, "drugi": 2, "treći": 3}
FOOTER = re.compile(
    r"^(\d+\s+)?(Grad Zagreb|[A-ZČĆŠŽĐ][a-zčćšžđ ]*(-[a-zčćšžđ]+)?\s+županija)(\s+\d+)?$|^(\d+\s+)?Grad Zagreb(\s+\d+)?$"
)


@dataclass
class Figure:
    value: int
    box: Word | None


@dataclass
class Entry:
    rank: int
    lines: list[str]  # the name line and the party lines under it
    votes: Figure
    head: str = ""  # a list's "Nositelj liste"
    deputies: list[str] = field(default_factory=list)


@dataclass
class Member:
    position: int
    name: str


@dataclass
class Block:
    rank: int | None  # the printed number, which is the ballot position in 2005 and 2009 and the order of seats in 2001
    names: list[str]
    seats: int
    votes: int | None = None
    members: list[Member] = field(default_factory=list)


@dataclass
class Section:
    county: str
    title: str
    round: int
    page: int
    stats: dict[str, Figure] = field(default_factory=dict)
    entries: list[Entry] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    elected: str = ""  # an executive contest's winner
    deputies: list[str] = field(default_factory=list)
    advanced: list[str] = field(default_factory=list)

    @property
    def executive(self) -> bool:
        return self.title.startswith(("GRADONAČELNIK", "NAČELNIK", "ŽUPAN ")) and "SKUPŠTINA" not in self.title


def figure(word: Word) -> Figure:
    return Figure(int(word.text.replace(".", "")), word)


def fix(text: str) -> str:
    """The 2009 reports' own stand-ins for Đ and đ."""
    text = text.replace("▒", "-").replace("–", "-")
    return re.sub(
        r"\S*[ðñ]\S*",
        lambda m: re.sub("[ðñ]", "Đ" if m.group(0).replace("ð", "").replace("ñ", "").isupper() else "đ", m.group(0)),
        text,
    )


def noise(text: str) -> bool:
    return (
        text.startswith(("Informacija o", "i općinskih načelnika", "Stranica", "* na izborima"))
        or bool(FOOTER.match(text))
        or bool(re.match(r"^(grad|općina|Grad|Općina)\s+\S", text))
    )


def _trailing(words: tuple[Word, ...]) -> tuple[list[Word], Word | None]:
    """A line's words without its trailing votes, "glasova", and percent, and the votes' word."""
    rest = list(words)
    while rest and (PERCENT.match(rest[-1].text) or rest[-1].text.startswith("glas")):
        rest.pop()
    if len(rest) > 1 and NUMBER.match(rest[-1].text):
        return rest[:-1], rest[-1]
    return rest, None


def sections(path: Path, county: str) -> list[Section]:
    rows = [(line, fix(clean(line.text))) for line in lines(path)]
    rows = [(line, text) for line, text in rows if text and not noise(text)]
    starts = [i for i, (_, text) in enumerate(rows) if text.startswith("Ukupno birača")]
    out: list[Section] = []
    for n, at in enumerate(starts):
        back = at - 1
        number = 1
        marker = ROUND.match(rows[back][1])
        if marker:
            number = ROUNDS[marker.group(1).lower()]
            back -= 1
        heads = [back]
        while not HEAD.match(rows[heads[0]][1]):
            heads.insert(0, heads[0] - 1)
            if len(heads) > 3:
                raise ValueError(f"{path.name}: no heading above line {rows[at][1]!r}: {rows[back][1]!r}")
        title = " ".join(rows[i][1] for i in heads)
        end = starts[n + 1] if n + 1 < len(starts) else len(rows)
        if n + 1 < len(starts):
            # The next section's heading belongs to it.
            while end - 1 > at and (HEAD.match(rows[end - 1][1]) or ROUND.match(rows[end - 1][1])):
                end -= 1
        section = Section(county, title, number, rows[at][0].page)
        _read(section, rows[at:end], path.name)
        # A block that runs onto the next page is sometimes printed again there in full.
        kept: list[Block] = []
        for block in section.blocks:
            if kept and (block.rank, block.names, block.seats, block.members) == (
                kept[-1].rank,
                kept[-1].names,
                kept[-1].seats,
                kept[-1].members,
            ):
                continue
            kept.append(block)
        section.blocks = kept
        out.append(section)
    return out


LEADER = re.compile(r"^(?:I\s+)?No\w+\s+list[ae]\s*[:.\-]\s*(.+)$", re.IGNORECASE)
RANK = re.compile(r"^(\d+)(?:\.\s+|\.(?=[A-ZČĆŠŽĐ])|\s+(?=[A-ZČĆŠŽĐ]))(.*)$")
STATS = {"Ukupno birača": "registered", "Glasovalo birača": "voters", "Važećih listića": "valid"}


def _read(section: Section, rows: list[tuple[Line, str]], where: str) -> None:
    mode = "stats"
    entry: Entry | None = None
    block: Block | None = None
    heading: list[tuple[str, int | None]] = []
    pending: list[str] = []
    for line, text in rows:
        if mode == "stats":
            label = text.split(":", 1)[0]
            numbers = [w for w in line.words if NUMBER.match(w.text)]
            if label in STATS or label == "Nevažećih listića":
                if not numbers:
                    raise ValueError(f"{where}: {section.title}: no figure on {text!r}")
                section.stats["invalid" if label == "Nevažećih listića" else STATS[label]] = figure(numbers[0])
                continue
        if text.startswith(("Pojedine kandidacijske liste", "Pojedini kandidati")):
            mode, entry = "votes", None
            continue
        if "izabrani su:" in text:
            mode, block, heading = "seats", None, []
            continue
        if re.match(r"^Za .*izabran(a)? je", text):
            mode = "elected"
            continue
        if text.startswith("a za zamjenik"):
            mode = "deputies"
            continue
        if text.startswith(("U drugom krugu izbora sudjeluju", "U trećem krugu izbora sudjeluju")):
            mode = "advanced"
            continue
        if text.startswith("Niti jedan kandidat") or mode == "done":
            mode = "done" if mode != "advanced" else mode
            continue
        if mode == "votes":
            found = RANK.match(text)
            last = section.entries[-1].rank if section.entries else 0
            # The 2001 reports skip a number now and then; a skipped list's votes are missing from the report.
            if found and (
                int(found.group(1)) == last + 1
                or (int(found.group(1)) == last + 2 and _trailing(line.words)[1] is not None and section.entries)
            ):
                rest, votes = _trailing(line.words)
                name = clean(RANK.sub(r"\2", " ".join(w.text for w in rest)))
                entry = Entry(
                    int(found.group(1)),
                    [*pending, name] if name else pending,
                    figure(votes) if votes else Figure(-1, None),
                )
                pending = []
                section.entries.append(entry)
                continue
            if entry is None:
                continue
            if NUMBER.match(text.split(" ")[0]) and entry.votes.value < 0:
                rest, votes = _trailing(line.words)
                if votes is None and NUMBER.match(line.words[0].text):
                    votes = line.words[0]
                if votes is not None:
                    entry.votes = figure(votes)
                    continue
            head = LEADER.match(text)
            if head:
                rest, votes = _trailing(line.words)
                if votes is not None and entry.votes.value < 0:
                    entry.votes = figure(votes)
                    head = LEADER.match(" ".join(w.text for w in rest))
                entry.head = clean(head.group(1)) if head else ""
            elif text.startswith(("Zamjenik kandidata", "Zamjenica kandidata", "Zamjenik kandidatkinje")):
                entry.deputies.append(clean(text.split(" ", 2)[2]))
            elif entry.head:
                # Lines after a list's leader open the next list, whose number may come a line later.
                pending.append(text)
            else:
                rest, votes = _trailing(line.words)
                if votes is not None and entry.votes.value < 0:
                    entry.votes = figure(votes)
                    text = " ".join(w.text for w in rest)
                entry.lines.append(text)
        elif mode == "seats":
            seats = re.match(r"^(\d+)\s+mjest[oa]$", text)
            if seats and (block is None or len(block.members) >= block.seats):
                if not heading:
                    raise ValueError(f"{where}: {section.title}: seats without a list: {text!r}")
                # A list's number is printed beside the middle of its name when the name wraps.
                leads = [re.match(r"^(\d+)\.\s*(.*)$", h) for h, _ in heading]
                rank = next((int(m.group(1)) for m in leads if m), None)
                names = [m.group(2) if m else h for m, (h, _) in zip(leads, heading, strict=True)]
                names = [n for n in names if n]
                votes = next((v for _, v in heading if v is not None), None)
                block = Block(rank, names, int(seats.group(1)), votes)
                section.blocks.append(block)
                heading = []
                continue
            if block is not None and len(block.members) < block.seats:
                member = re.match(r"^(\d+)\.\s*(.+)$", text)
                if member and int(member.group(1)) == len(block.members) + 1:
                    block.members.append(Member(int(member.group(1)), clean(member.group(2))))
                elif block.members:
                    block.members[-1].name = clean(block.members[-1].name + " " + text)
                else:
                    raise ValueError(f"{where}: {section.title}: unexpected line in seats: {text!r}")
                continue
            rest, votes = _trailing(line.words)
            heading.append((clean(" ".join(w.text for w in rest)), figure(votes).value if votes else None))
        elif mode == "elected":
            if not section.elected:
                section.elected = clean(text)
        elif mode == "deputies":
            member = re.match(r"^(\d+)\.\s*(.+)$", text)
            section.deputies.append(clean(member.group(2) if member else text))
        elif mode == "advanced":
            member = re.match(r"^(\d+)\.\s*(.+)$", text)
            if member:
                rest, _ = _trailing(line.words)
                section.advanced.append(clean(re.sub(r"^\d+\.\s*", "", " ".join(w.text for w in rest))))


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9čćšžđ]+", text.lower())) - {"koalicija", "i", "liste", "nositelj"}


def _like(a: str, b: str) -> float:
    x, y = _words(a), _words(b)
    return len(x & y) / max(1, len(x | y))


TITLES = {"DR", "SCI", "SC", "MR", "ING", "PROF", "DIPL", "MED", "OEC", "IUR", "UNIV", "BACC", "MAG", "STR", "SPEC"}


def _person(name: str) -> set[str]:
    return {w for w in re.findall(r"[A-ZČĆŠŽĐ][A-ZČĆŠŽĐ-]+", name.upper()) if w not in TITLES}


def _same(a: str, b: str) -> float:
    x, y = _person(a), _person(b)
    return len(x & y) / max(1, len(x), len(y))


def link(section: Section) -> dict[int, int]:
    """Each seat block's entry on the ballot: by the list's name, then its leader, its votes, and its number."""
    out: dict[int, int] = {}
    taken: set[int] = set()
    for b, block in enumerate(section.blocks):
        name = " ".join(block.names)
        first = block.members[0].name if block.members else ""
        scored = []
        for e, entry in enumerate(section.entries):
            if e in taken:
                continue
            score = _like(name, " ".join(entry.lines)) * 4
            score += 2 * _same(first, entry.head) if first and entry.head else 0
            score += 2 if block.votes is not None and block.votes == entry.votes.value else 0
            score += 1 if block.rank == entry.rank else 0
            scored.append((score, e))
        if not scored:
            raise ValueError(f"{section.title}: no ballot entry left for {name!r}")
        best = max(scored)
        if best[0] < 2.5 or [s for s, _ in scored].count(best[0]) > 1:
            raise ValueError(f"{section.title}: cannot place {name!r} on the ballot ({sorted(scored)[-3:]})")
        out[b] = best[1]
        taken.add(best[1])
    return out


# Packages: a registry of the councils, offices, parties, and electoral systems, and one package per round.

YEARS = (2001, 2005, 2009)
COUNTIES = tuple(f"{n:02d}" for n in range(1, 22))
DAYS = {
    (2001, 1): date(2001, 5, 20),
    (2005, 1): date(2005, 5, 15),
    (2009, 1): date(2009, 5, 17),
    (2009, 2): date(2009, 5, 31),
    (2009, 3): date(2009, 6, 14),
}
REGISTRY = f"{SOURCE}/registry-2001-2009"
SOURCE_ID = lk.key("source", SOURCE, "county-reports-2001-2009")
REGISTERED, VALID, INVALID, VOTERS, VOTES = 2, 4, 5, 14, 1
# "Glasovalo birača" counts the voters who signed the roll, which can exceed the valid and invalid ballots found.
MEASURES = {"registered": REGISTERED, "voters": VOTERS, "valid": VALID, "invalid": INVALID}
# Councils whose report spells the place differently from the commission's current list of towns and municipalities.
ALIASES = {
    ("04", "ozlja"): "ozalj",
    ("05", "donji martijanec"): "martijanec",
    ("08", "vinodolska"): "vinodolska opcina",
    ("16", "otok vinkovci"): "otok",
    ("16", "otoka vinkovci"): "otok",
}


def report_url(year: int, county: str) -> str:
    if year == 2001:
        return f"{lk.DATA}/2001/rezultati/Z{int(county)}.pdf"
    return f"{lk.DATA}/{year}/izabrani/i_06_{county}_0000.pdf"


def fetch() -> None:
    for year in YEARS:
        for county in COUNTIES:
            raw.fetch(SOURCE, report_url(year, county), suffix=".pdf")


def package_key(year: int, number: int) -> str:
    return f"{SOURCE}/{DAYS[(year, number)]}"


def packages() -> list[str]:
    return [REGISTRY, *(package_key(year, number) for year, number in DAYS)]


@dataclass(frozen=True)
class Contest:
    year: int
    section: Section
    kind: str  # assembly, council, mayor, or prefect
    county: str
    place: str  # the folded name of the town or municipality, or "" for a county
    town: bool  # a town (grad) rather than a municipality (općina)

    @property
    def ident(self) -> tuple[str, str, str]:
        return (self.county, self.place, self.kind)

    @property
    def listing(self) -> bool:
        return self.kind in ("assembly", "council")


def _stem(word: str) -> str:
    return word[: max(3, len(word) - 2)] if len(word) > 3 else word


@cache
def _towns() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for county, folded in z.municipalities():
        out.setdefault(county, []).append(folded)
    return out


def locate(county: str, genitive: str) -> str:
    """The folded name of the town or municipality a report names in the genitive, as the national geography has it."""
    folded = lk.label(genitive)
    if (county, folded) in ALIASES:
        return ALIASES[(county, folded)]
    names = _towns()[county]
    if folded in names:
        return folded
    words = folded.split()
    found = [
        name
        for name in names
        if len(name.split()) == len(words)
        and all(a.startswith(_stem(b)) or b.startswith(_stem(a)) for a, b in zip(words, name.split(), strict=True))
    ]

    def shared(name: str) -> int:
        return sum(len(os.path.commonprefix([a, b])) for a, b in zip(words, name.split(), strict=True))

    best = [name for name in found if shared(name) == max(map(shared, found))]
    if len(best) != 1:
        raise ValueError(f"{county}: no single town or municipality for {genitive!r}: {found}")
    return best[0]


TITLE = re.compile(
    r"^(?:(ŽUPANIJSKA SKUPŠTINA|GRADSKA SKUPŠTINA|ŽUPAN)\s+(.+)"
    r"|(GRADSKO VIJEĆE GRADA?|OPĆINSKO VIJEĆE(?: OPĆIN[EA])?|GRADONAČELNIK GRADA?|NAČELNIK(?: OPĆIN[EA])?)\s+(.+))$"
)


def contest_of(year: int, section: Section) -> Contest:
    found = TITLE.match(section.title)
    if found is None:
        raise ValueError(f"{year} {section.county}: unknown contest {section.title!r}")
    if found.group(1):
        kind = {"ŽUPAN": "prefect"}.get(found.group(1), "assembly")
        return Contest(year, section, kind, section.county, "", False)
    head, name = found.group(3), found.group(4)
    town = head.startswith(("GRADSKO", "GRADONAČELNIK"))
    kind = "council" if "VIJEĆE" in head else "mayor"
    if section.county == lk.ZAGREB and lk.label(name) == "zagreba":
        return Contest(year, section, kind if kind == "mayor" else "assembly", section.county, "", True)
    return Contest(year, section, kind, section.county, locate(section.county, name), town)


@cache
def contests(year: int) -> tuple[Contest, ...]:
    out = []
    for county in COUNTIES:
        path = raw.cached(SOURCE, report_url(year, county)).path
        out += [contest_of(year, s) for s in sections(path, county)]
    return tuple(out)


def all_contests() -> list[Contest]:
    return [c for year in YEARS for c in contests(year)]


def town_name(c: Contest) -> str:
    if not c.place:
        return "Zagreb" if c.county == lk.ZAGREB else lk.county_title(z.counties()[c.county])
    return lk.title(z.municipalities()[(c.county, c.place)][1])


def names_of(c: Contest) -> tuple[str, str, str, str]:
    """English and Croatian names of the body or office, the place's slug, and the contest's slug tail."""
    who = town_name(c)
    where = f"{c.county}-{slug(who)}"
    zagreb = c.county == lk.ZAGREB and not c.place
    if c.kind == "assembly":
        if zagreb:
            return "Zagreb City Assembly", "Gradska skupština Grada Zagreba", where, "assembly"
        return f"{who} County Assembly", f"Županijska skupština: {who} županija", where, "assembly"
    if c.kind == "prefect":
        return f"Prefect of {who} County", f"Župan: {who} županija", where, "prefect"
    if c.kind == "council":
        english = f"{who} {'Town' if c.town else 'Municipal'} Council"
        return english, f"{'Gradsko' if c.town else 'Općinsko'} vijeće: {who}", where, "council"
    if zagreb:
        return "Mayor of Zagreb", "Gradonačelnik Grada Zagreba", where, "mayor"
    return f"Mayor of {who}", f"{'Gradonačelnik' if c.town else 'Načelnik'}: {who}", where, "mayor"


def district(c: Contest) -> str:
    if not c.place:
        return lk.izbori_area("hr.zupanija", c.county)
    return lk.izbori_area("hr.opcina", f"{c.county}:{c.place}")


def body_id(c: Contest) -> str:
    return lk.key("body", "county-reports", *c.ident)


def office_id(c: Contest) -> str:
    return lk.key("office", "county-reports", *c.ident)


# Parties: the lines printed under a list or a candidate, one party to a line.

DROP = re.compile(r"^\(?\s*koalicija\s*\)?$|^(koalicija )?stranaka$|^kandidacijska lista$|^\d+\.?$", re.IGNORECASE)
INDEPENDENT = re.compile(r"NEZAVISN\w* KANDIDAT|KANDIDAT\w* GRUPE BIRAČA|GRUPA BIRAČA", re.IGNORECASE)


def parties(lines: list[str]) -> list[str]:
    """The parties printed under a list or a candidate, one to a line, with wrapped names joined again."""
    out: list[str] = []
    for line in lines:
        text = clean(re.sub(r"\s*-\s*koalicija(\s+stranaka)?\s*$", "", line, flags=re.IGNORECASE))
        if not text or DROP.match(text):
            continue
        if out and (
            out[-1].endswith(",")
            or (" - " in out[-1] and re.fullmatch(r"[A-ZČĆŠŽĐ0-9-]+", text))
            or text.startswith(("I RIJEKE", "OTOKA I", "KOTARA, OTOKA"))
        ):
            out[-1] = clean(f"{out[-1]} {text}").replace(",,", ",")
            continue
        out.append(text)
    return out


def party_key(name: str) -> str:
    """A party's spelling without its abbreviation, which the 2001 reports mostly leave out."""
    return lk.label(re.sub(r"\s+-\s*[^-]{1,30}$", "", clean(name)))


def party_id(name: str) -> str:
    return lk.key("party", "county-reports", party_key(name))


def bloc_id(names: list[str]) -> str:
    return lk.key("party", "county-reports", "bloc", *sorted(party_key(n) for n in names))


def option_parties(c: Contest, entry: Entry) -> list[str]:
    found = parties(entry.lines if c.listing else entry.lines[1:])
    return [
        n
        for n in found
        if lk.party_kind(n) and not lk.label(n).startswith(("nezavisn", "neovisn")) and not INDEPENDENT.search(n)
    ]


def option_party(c: Contest, entry: Entry) -> str | None:
    names = option_parties(c, entry)
    if len(names) == 1:
        return party_id(names[0])
    if len(names) > 1:
        return bloc_id(names)
    return None


SYSTEMS = [
    (
        lk.key("electoral_system", "county-reports", "council"),
        {"formula": "dhondt", "threshold": {"level": "list", "scope": "contest", "percent": 5}},
        (
            "From 2001 to 2009 a county assembly, the Zagreb City Assembly, and each town and municipal council was "
            "elected from closed lists in one constituency, with seats divided by the D'Hondt method among the lists "
            "that won at least 5% of the votes (Zakon o izboru članova predstavničkih tijela jedinica lokalne i "
            "područne (regionalne) samouprave, NN 33/2001). The seats each list won and the members elected are as the "
            "commission's county reports declare them."
        ),
    ),
    (
        lk.key("electoral_system", "county-reports", "executive"),
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "From 2009 mayors and župans were elected directly: a candidate who won more than half of the votes of "
            "those who voted was elected, and otherwise the two leading candidates met in a second round two weeks "
            "later (Zakon o izboru općinskih načelnika, gradonačelnika, župana i gradonačelnika Grada Zagreba, NN "
            "109/2007). A tied second round was repeated."
        ),
    ),
]


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The county assemblies, town and municipal councils, mayoral and župan offices, electoral systems, and parties "
        "of Croatia's local elections of 2001, 2005, and 2009, and the commission's county reports they are read from",
    )
    p.external.add(lk.CROATIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Državno izborno povjerenstvo Republike Hrvatske",
        title="Arhiva izbora: lokalni izbori 2001, 2005 i 2009, informacije o rezultatima izbora po županijama",
        url=f"{lk.SITE}/",
        license="Official results of the Republic of Croatia, published for public use; reuse with the source "
        "acknowledged",
        license_url=f"{lk.SITE}/",
        attribution="Državno izborno povjerenstvo Republike Hrvatske (DIP), izbori.hr",
        notes="One PDF report per county and year with each contest's totals, the votes of each list or candidate, "
        "and the members or executives elected.",
    )
    for id, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=lk.CROATIA,
            valid_from=date(2001, 1, 1) if "dhondt" in str(rules) else date(2009, 1, 1),
            valid_to=date(2013, 1, 1),
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=lk.key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    seen: set[str] = set()
    for c in sorted(all_contests(), key=lambda c: -c.year):
        id = body_id(c) if c.listing else office_id(c)
        if id in seen:
            continue
        seen.add(id)
        english, native, _, _ = names_of(c)
        if c.listing:
            p.add("body", id=id, polity_id=lk.CROATIA, kind="council")
        else:
            p.add("office", id=id, polity_id=lk.CROATIA, kind="governor" if c.kind == "prefect" else "mayor")
        p.name(id, english, "en", "official")
        p.name(id, native, "hr", "native", False)
    spellings: dict[str, Counter[str]] = {}
    blocs: dict[str, list[str]] = {}
    for c in all_contests():
        for entry in c.section.entries:
            names = option_parties(c, entry)
            for n in names:
                spellings.setdefault(party_id(n), Counter())[n] += 1
            if len(names) > 1:
                blocs.setdefault(bloc_id(names), names)
    for id, counted in sorted(spellings.items()):
        # The fullest common spelling, which carries the abbreviation where the reports print one.
        name = max(counted.items(), key=lambda item: (item[1] if lk.abbreviation(item[0]) else 0, item[1]))[0]
        p.add("party", id=id, kind="party", polity_id=lk.CROATIA)
        p.name(id, name, "hr", "official")
        short = lk.abbreviation(name)
        if short:
            p.name(id, short, "hr", "abbreviation", False)
    for id, members in sorted(blocs.items()):
        p.add("party", id=id, kind="bloc", polity_id=lk.CROATIA)
        names = sorted({spellings_name(spellings, m) for m in members})
        p.name(id, ", ".join(names), "hr", "official")
    return p


def spellings_name(spellings: dict[str, Counter[str]], name: str) -> str:
    counted = spellings[party_id(name)]
    return max(counted.items(), key=lambda item: (item[1] if lk.abbreviation(item[0]) else 0, item[1]))[0]


METHOD = (
    "Read from the State Electoral Commission's reports on the results of the local elections, one PDF per county "
    "(Informacija o rezultatima izbora). For each county assembly, the Zagreb City Assembly, each town and municipal "
    "council, and from 2009 each mayoral and župan contest, the report prints the registered voters, the voters who "
    "voted, the invalid ballots (and from 2005 the valid ballots), each list's or candidate's votes, and the seats and "
    "members, or the executive, declared elected. Every figure keeps its page and box in the report. The report gives "
    "no breakdown below the town or municipality."
)
CAVEAT_2001 = (
    "The 2001 report prints no count of valid ballots, so none is stored; the lists' votes are as printed and do not "
    "always add up to the voters less the invalid ballots."
)


class Values:
    def __init__(self) -> None:
        self.values: list[tuple[str, str, str | None, int, int]] = []
        self.locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []

    def add(self, result_set: str, unit: str, option: str | None, measure: int, found: Figure, c: Contest) -> None:
        self.values.append((result_set, unit, option, measure, found.value))
        if found.box is not None:
            x0, y0, x1, y1 = (round(x, 1) for x in found.box.box)
            page = page_id(c.year, c.county, found.box.page)
            self.locators.append((result_set, unit, option, measure, page, x0, y0, x1, y1))

    def write(self, p: Package) -> None:
        p.frame(
            "result_value",
            pl.DataFrame(
                self.values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        p.frame(
            "value_locator",
            pl.DataFrame(
                self.locators,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "page_id": pl.String,
                    "x0": pl.Float64,
                    "y0": pl.Float64,
                    "x1": pl.Float64,
                    "y1": pl.Float64,
                },
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), source_id=pl.lit(None, pl.String)
            ),
        )


def page_id(year: int, county: str, number: int) -> str:
    return lk.key("page", raw.cached(SOURCE, report_url(year, county)).sha256, number)


def _pages(p: Package, year: int, county: str) -> None:
    stored = raw.cached(SOURCE, report_url(year, county))
    artifact = p.artifact(stored, SOURCE_ID)
    for number, (width, height) in sorted(sizes(stored.path).items()):
        p.add(
            "source_page",
            id=page_id(year, county, number),
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
            width=width,
            height=height,
        )


def election_names(year: int, number: int) -> tuple[str, str, str]:
    day = DAYS[(year, number)]
    if number == 1:
        return f"{year} Croatian local elections", f"Lokalni izbori {year}.", f"hr/{day}-local"
    if number == 2:
        return (
            f"{year} Croatian local elections, second round",
            f"Lokalni izbori {year}., drugi krug",
            f"hr/{day}-local-runoff",
        )
    return (
        f"{year} Croatian local elections, third round",
        f"Lokalni izbori {year}., treći krug",
        f"hr/{day}-local-third-round",
    )


def _elected(c: Contest, name: str) -> int:
    """The ballot entry of a declared candidate, matched on the words of the name."""
    scored = [(_same(name, e.lines[0]), i) for i, e in enumerate(c.section.entries)]
    best = max(scored, default=(0.0, -1))
    if best[0] < 0.5 or [s for s, _ in scored].count(best[0]) > 1:
        raise ValueError(f"{c.year} {c.section.title}: cannot find {name!r} on the ballot")
    return best[1]


def person_key(c: Contest, name: str) -> str:
    """A candidate's person, the same in every round of a contest; a later round may spell the name a little
    differently, so it is matched to the first round's ballot."""
    if c.section.round > 1:
        first = next(x for x in contests(c.year) if x.ident == c.ident and x.section.round == 1)
        best = max(first.section.entries, key=lambda e: _same(name, e.lines[0]))
        if _same(name, best.lines[0]) >= 0.5:
            name = best.lines[0]
    return lk.key("person", "county-reports", c.year, *c.ident, " ".join(sorted(_person(name))))


def election(year: int, number: int) -> Package:
    english, native, path = election_names(year, number)
    day = DAYS[(year, number)]
    depends = [f"{z.SOURCE}/geo", REGISTRY]
    if number > 1:
        depends += [package_key(year, n) for n in range(1, number)]
    p = Package(
        package_key(year, number),
        SOURCE,
        f"The {english} by town, municipality, and county from the State Electoral Commission's county reports",
        depends,
    )
    p.external.add(lk.CROATIA)
    here = [c for c in contests(year) if c.section.round == number]
    for county in sorted({c.county for c in here}):
        if number == 1:
            _pages(p, year, county)
        else:
            p.uses(raw.cached(SOURCE, report_url(year, county)))
    election_id = lk.key("election", "county-reports", year, number)
    p.add(
        "election",
        id=election_id,
        polity_id=lk.CROATIA,
        kind="local" if number == 1 else "runoff",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, english, "en", "official")
    p.name(election_id, native, "hr", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.add("note", id=lk.key("note", election_id), entity_id=election_id, kind="methodology", body_md=METHOD, lang="en")
    values = Values()
    units: dict[str, str] = {}
    people: set[str] = set()
    if number > 1:
        people = {
            person_key(c, e.lines[0]) for c in contests(year) if c.section.round < number for e in c.section.entries
        }
    for c in here:
        s = c.section
        english_name, native_name, where, tail = names_of(c)
        contest_id = lk.key("contest", "county-reports", year, number, *c.ident)
        result_set = lk.key("result_set", "county-reports", year, number, *c.ident)
        seats_total = sum(b.seats for b in s.blocks)
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="list_allocation" if c.listing else "office",
            office_id=None if c.listing else office_id(c),
            body_id=body_id(c) if c.listing else None,
            district_area_id=district(c),
            magnitude=seats_total if c.listing else 1,
            round_number=number,
            previous_round_id=lk.key("contest", "county-reports", year, number - 1, *c.ident) if number > 1 else None,
            electoral_system_id=SYSTEMS[0][0] if c.listing else SYSTEMS[1][0],
            default_result_kind="counted",
        )
        p.name(contest_id, english_name, "en", "official")
        p.name(contest_id, native_name, "hr", "native", False)
        p.add("slug", path=f"{path}/{where}/{tail}", entity_id=contest_id, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        if year == 2001:
            p.add(
                "note",
                id=lk.key("note", result_set),
                entity_id=result_set,
                kind="caveat",
                body_md=CAVEAT_2001,
                lang="en",
            )
        code = f"{day}:{c.county}:{c.place}" if c.place else f"{day}:{c.county}"
        unit = units.get(code)
        if unit is None:
            unit = units[code] = lk.key("unit", "county-reports", code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=lk.CROATIA,
                scheme_code="hr.opcina" if c.place else "hr.zupanija",
                code=code,
                area_relation="equals",
                area_id=district(c),
                kind="standard",
            )
            p.name(unit, town_name(c), "hr", "official")
        for key_, found in s.stats.items():
            values.add(result_set, unit, None, MEASURES[key_], found, c)
        options: list[str] = []
        for entry in s.entries:
            option = lk.key("option", "county-reports", year, number, *c.ident, entry.rank)
            options.append(option)
            party = option_party(c, entry)
            label = " ".join(parties(entry.lines)) if c.listing else entry.lines[0]
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="list" if c.listing else "candidate",
                party_id=party,
                ballot_position=entry.rank,
                ballot_label=label,
            )
            values.add(result_set, unit, option, VOTES, entry.votes, c)
            if not c.listing:
                who = person_key(c, entry.lines[0])
                if who not in people:
                    people.add(who)
                    p.add("person", id=who)
                    p.name(who, lk.title(lk.bare(entry.lines[0])), "hr", "official")
                p.add(
                    "candidacy",
                    id=lk.key("candidacy", "county-reports", year, number, *c.ident, entry.rank),
                    person_id=who,
                    option_id=option,
                    role="head",
                    ballot_name=entry.lines[0],
                    member_of_party_id=party,
                    is_independent=party is None,
                )
        if c.listing:
            linked = {e: b for b, e in link(s).items()}
            for e, entry in enumerate(s.entries):
                block = s.blocks[linked[e]] if e in linked else None
                seats = block.seats if block is not None else 0
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[e],
                    kind="won" if seats else "lost",
                    seats=seats,
                )
                for member in block.members if block is not None else []:
                    who = lk.key("person", "county-reports", year, *c.ident, entry.rank, member.position)
                    candidacy = lk.key("candidacy", "county-reports", year, *c.ident, entry.rank, member.position)
                    p.add("person", id=who)
                    p.name(who, lk.title(lk.bare(member.name)), "hr", "official")
                    party = option_party(c, entry)
                    p.add(
                        "candidacy",
                        id=candidacy,
                        person_id=who,
                        option_id=options[e],
                        role="list_member",
                        ballot_name=member.name,
                        member_of_party_id=party,
                        is_independent=party is None,
                    )
                    p.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[e],
                        candidacy_id=candidacy,
                        kind="elected",
                    )
        else:
            verdict: dict[int, str] = {}
            if s.elected:
                verdict[_elected(c, s.elected)] = "elected"
            for name in s.advanced:
                verdict[_elected(c, name)] = "advanced"
            if not verdict:
                raise ValueError(f"{year} {s.title} round {number}: no declared outcome")
            for e, option in enumerate(options):
                p.add("outcome", result_set_id=result_set, option_id=option, kind=verdict.get(e, "lost"))
    values.write(p)
    short = [
        f"{names_of(c)[0]} ({sum(e.votes.value for e in c.section.entries):,} votes, {c.section.stats['valid'].value:,} "
        "valid ballots)"
        for c in here
        if "valid" in c.section.stats
        and sum(e.votes.value for e in c.section.entries) != c.section.stats["valid"].value
    ]
    if short:
        p.accept(
            "accounting.votes_valid",
            "The county report prints list votes that do not add up to the valid ballots it prints: "
            + "; ".join(short),
        )
    return p


def parse() -> None:
    registry().write()
    for year, number in DAYS:
        election(year, number).write()
