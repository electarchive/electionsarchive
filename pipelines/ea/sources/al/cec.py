"""Albania's earlier local and parliamentary elections from the Central Election Commission's former site, cec.org.al.

Before kqz.gov.al, the Commission (KQZ, formerly CEC) published each election's results on cec.org.al as PDF files
linked from one page per election. The site now refuses scripted requests, so the files come from the Internet
Archive's copies of the Commission's own URLs, and each is cited under the URL the Commission published it at.

- **Local elections of 12 October 2003.** One file per county (qarku) prints, for each municipality and commune, the
  mayoral candidates' votes, the council lists' votes and seats, and the accounting of the ballots. The copy of the
  Lezhë county file is cut off and cannot be read, so Lezhë's units are missing.
- **Local elections of 8 May 2011.** One file per county prints, for each municipality and commune, the mayoral and
  council results with the accounting of each ballot, first for the unit and then for each voting center (QV). Three
  copies (Gjirokastër, Tirana, and part of others) are cut off; a unit whose pages are missing is stored from what
  remains, as described in the methodology note.
- **Local elections of 30 June 2019.** The Commission's bulletin prints, for each of the 61 municipalities, the
  accounting of both ballots, the mayoral candidates' votes, and the council lists' votes and seats.
- **Parliamentary election of 3 July 2005.** The Commission's table of the national result prints the votes of each
  party in the proportional ballot, and its list of the Assembly's members names each member's party and whether the
  member won a single-member zone or a supplementary seat.
"""

import functools
import itertools
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import polars as pl
import pymupdf
import shapely

from ea import package, raw
from ea.package import Package

SOURCE = "al.cec"
SITE = "https://cec.org.al"
ALBANIA = str(uuid5(package.NAMESPACE, "polity\x1fAL"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fALB"))
VOTES, REGISTERED, VALID, INVALID, VOTERS, BALLOTS = 1, 2, 4, 5, 14, 3
_IDS = Package(SOURCE, SOURCE, "ids")
_KQZ = Package("al.kqz", "al.kqz", "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


def kqz(kind: str, *parts: object) -> str:
    """An id the al.kqz pipeline minted; every one used is checked against the reference rows."""
    return _KQZ.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)
SHAPES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ALB"
COMMUNES = f"{SHAPES}/ADM3/geoBoundaries-ALB-ADM3.geojson"
COUNTIES = f"{SHAPES}/ADM1/geoBoundaries-ALB-ADM1.geojson"
SHAPES_ID = key("source", "geoboundaries-adm3")


def clean(text: object) -> str:
    return " ".join(str(text if text is not None else "").replace("\xa0", " ").split())


def fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", clean(text)).upper().split())


def plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def title(text: str) -> str:
    return " ".join("-".join(p.capitalize() for p in word.split("-")) for word in fold(text).split())


def number(text: str) -> int:
    text = text.strip().replace(".", "").replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+", text):
        raise ValueError(f"not a count: {text!r}")
    return int(text)


def is_number(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}([.,]\d{3})*|\d+", text.strip()))


# Reading a page as rows of cells.

Cell = tuple[float, str]
Row = tuple[float, list[Cell]]


def rows(page: pymupdf.Page, *, tolerance: float = 3, gap: float = 4, drop: tuple[str, ...] = ()) -> list[Row]:
    """The page's words grouped into lines by their baseline and into cells by the space between them."""
    words = [w for w in page.get_text("words") if w[4] not in drop]
    words.sort(key=lambda w: (round(w[3]), w[0]))
    lines: list[tuple[float, list[tuple[float, float, float, float, str]]]] = []
    for w in words:
        if lines and abs(lines[-1][0] - w[3]) <= tolerance:
            lines[-1][1].append(w[:5])
        else:
            lines.append((w[3], [w[:5]]))
    out: list[Row] = []
    for y, ws in lines:
        ws.sort(key=lambda w: w[0])
        cells: list[list[float | str]] = []
        for w in ws:
            if cells and w[0] - float(cells[-1][1]) < gap:
                cells[-1][1] = w[2]
                cells[-1][2] = f"{cells[-1][2]} {w[4]}"
            else:
                cells.append([w[0], w[2], w[4]])
        out.append((y, [(float(c[0]), str(c[2])) for c in cells]))
    return out


def document(url: str) -> pymupdf.Document:
    return pymupdf.open(raw.cached(SOURCE, url).path)


# Shared shapes of the parsed results.


@dataclass
class Line:
    position: int
    name: str
    party: str  # the party, coalition, or list label as printed; empty for an independent
    votes: int
    seats: int = 0
    group: str = ""  # the coalition a council list ran in


@dataclass
class Ballot:
    accounting: dict[int, int] = field(default_factory=dict)
    lines: list[Line] = field(default_factory=list)

    def total(self) -> int:
        return sum(x.votes for x in self.lines)


@dataclass
class Unit:
    """One local government unit's mayoral and council results, and its voting centers' results."""

    qarku: str
    kind: str  # BASHKIA or KOMUNA
    name: str
    mayor: Ballot
    council: Ballot
    centers: dict[str, tuple[Ballot, Ballot]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    winner: str = ""


# The local elections of 30 June 2019, from the Commission's bulletin.

DAY_2019 = date(2019, 6, 30)
BULLETIN_2019 = (
    f"{SITE}/wp-content/uploads/2020/01/BULETIN-zgjedhjet-per-organet-e-qeverisjes-vendore-30-QERSHOR-2019-last.pdf"
)
SEATS_2019 = f"{SITE}/wp-content/uploads/2019/07/Mandate-Bashki.pdf"
WINNERS_2019 = f"{SITE}/wp-content/uploads/2019/07/Rez.-Perfund.-Kryetar-Bashkie.pdf"
NATIONAL_2019 = f"{SITE}/wp-content/uploads/2020/02/2019-Tabela-Permbledhese-Final-1.pdf"


def _cells_text(cells: list[Cell]) -> str:
    return " ".join(t for _, t in cells)


Word = tuple[float, float, float, float, str]


def words(page: pymupdf.Page) -> list[Word]:
    """The page's words without the bulletin's watermark (its title down the margin and the year across the page)."""
    out: list[Word] = []
    for w in page.get_text("words"):
        x0, y0, x1, y1, text = w[:5]
        if text == "BULETINI" or (text == "2019" and 310 < x0 < 330 and 680 < y1 < 710):
            continue
        out.append((x0, y0, x1, y1, text))
    return out


def near(ws: list[Word], y: float, span: float = 9) -> list[Word]:
    return sorted((w for w in ws if abs((w[1] + w[3]) / 2 - y) <= span), key=lambda w: w[0])


def mid(w: Word) -> tuple[float, float]:
    return (w[0] + w[2]) / 2, (w[1] + w[3]) / 2


def joined(parts: list[Word]) -> str:
    text = ""
    for w in sorted(parts, key=lambda w: (round(mid(w)[1]), w[0])):
        text = text[:-1] + w[4] if text.endswith("-") else f"{text} {w[4]}"
    return clean(text)


def table(ws: list[Word], top: float, bottom: float) -> list[tuple[Word, Word | None, list[Word]]]:
    """The result rows between two heights, the total row last.

    A row is found by its percentage: the count just left of it is the row's votes and a count just right of it is
    its seats. Every other word goes to the nearest row, which gathers names printed on two lines."""
    region = [w for w in ws if top < mid(w)[1] < bottom]
    marks = [w for w in region if w[4].endswith("%")]
    found: list[tuple[Word, Word | None, list[Word]]] = []
    used: set[Word] = set()
    for m in sorted(marks, key=lambda w: mid(w)[1]):
        line = [w for w in region if abs(mid(w)[1] - mid(m)[1]) < 5 and w is not m]
        left = [w for w in line if is_number(w[4]) and w[2] <= m[0] + 1]
        if not left:
            continue
        votes = max(left, key=lambda w: w[0])
        if m[4] == "%" and votes[4] == "100":
            rest = [w for w in left if w is not votes]
            if not rest:
                continue
            votes = max(rest, key=lambda w: w[0])
        right = [w for w in line if is_number(w[4]) and w[0] >= m[2] - 1]
        seat = min(right, key=lambda w: w[0]) if right else None
        found.append((votes, seat, []))
        used |= {votes, m} | ({seat} if seat else set())
        used |= {w for w in line if w[4] in ("100", "%") and w[0] >= votes[2]}
    for w in region:
        if w in used or not found or w[4].endswith("%"):
            continue
        best = min(found, key=lambda f: abs(mid(f[0])[1] - mid(w)[1]))
        if abs(mid(best[0])[1] - mid(w)[1]) < 16:
            best[2].append(w)
    return found


@functools.cache
def read_2019() -> dict[str, Unit]:
    """Each municipality's summary page in the bulletin: its accounting, mayoral votes, and council votes and seats."""
    doc = document(BULLETIN_2019)
    units: dict[str, Unit] = {}
    for number_ in range(doc.page_count):
        page = doc[number_]
        text = page.get_text()
        if "Zgjedhës në listë" not in text or "Rezultatet e zgjedhjeve per Kryetar" not in text:
            continue
        ws = words(page)
        lines = rows(page, drop=("BULETINI",))
        head = {c[0][1]: _cells_text(c[1:]) for _, c in lines[:3] if c}
        qarku, name = fold(head["Qarku"]), fold(head["Bashkia"])
        mayor, council = Ballot(), Ballot()

        def at(word: str) -> Word:
            return next(w for w in ws if w[4] == word)

        kryetar_x, keshill_x = mid(at("Kryetar"))[0], mid(at("Këshill"))[0]
        y_reg = mid(at("Zgjedhës"))[1]
        registered = [w for w in near(ws, y_reg) if is_number(w[4])]
        mayor.accounting[REGISTERED] = council.accounting[REGISTERED] = number(registered[0][4])
        y_part = mid(at("Pjesëmarrja"))[1]
        counts = [w for w in near(ws, y_part) if re.fullmatch(r"\d+", w[4])]
        mayor.accounting[VOTERS], council.accounting[VOTERS] = number(counts[0][4]), number(counts[1][4])
        for word, measure in (("gjetura", BALLOTS), ("pavlefshme", INVALID), ("vlefshme", VALID)):
            label = at(word)
            y = mid(label)[1] + (
                6 if word == "gjetura" and not any(is_number(w[4]) for w in near(ws, mid(label)[1], 4)) else 0
            )
            found = [w for w in near(ws, y, 10) if is_number(w[4]) and w[0] > label[2]]
            for w in found:
                ballot = mayor if abs(mid(w)[0] - kryetar_x) < abs(mid(w)[0] - keshill_x) else council
                ballot.accounting[measure] = number(w[4])
        y_m = mid(next(w for w in ws if w[4] == "Kryetar" and mid(w)[1] > y_part + 30))[1]
        y_c = max(mid(w)[1] for w in ws if w[4] == "Këshill")
        support_x = at("Koalicioni")[0] - 8
        seat_word = next(w for w in ws if w[4] in ("Mandate", "Man-") and mid(w)[1] > y_c)
        rows_m = table(ws, y_m + 12, y_c - 6)
        rows_c = table(ws, mid(seat_word)[1] + 4, 800)
        if not any(w[4] == "Total:" and mid(w)[1] > y_c for w in ws):
            following = words(doc[number_ + 1])
            end = min(mid(w)[1] for w in following if w[4] == "Total:")
            rows_c += table(following, 0, end + 10)
        *candidates, total_m = rows_m
        for anchor, _, parts in candidates:
            nr = [w for w in parts if re.fullmatch(r"\d+", w[4]) and w[2] < 90]
            who = [w for w in parts if w[0] >= 80 and w[2] < support_x and w not in nr]
            support = [w for w in parts if w[0] >= support_x and w[2] < anchor[0]]
            mayor.lines.append(Line(int(nr[0][4]), fold(joined(who)), joined(support), number(anchor[4])))
        *lists, total_c = rows_c
        group = ""
        entries = []
        for anchor, seat, parts in lists:
            nr = [w for w in parts if re.fullmatch(r"\d+", w[4]) and w[2] < 90]
            label = joined([w for w in parts if w[0] >= 80 and w[2] < anchor[0] and w not in nr])
            entries.append((int(nr[0][4]) if nr else None, label, number(anchor[4]), number(seat[4]) if seat else 0))
        for i, (position, label, votes, seats) in enumerate(entries):
            if position is None:
                members = list(itertools.takewhile(lambda e: e[0] is not None, entries[i + 1 :]))
                if members and sum(e[2] for e in members) == votes:
                    group = label
                    continue
                # an independent candidate, who has no number on the ballot
                council.lines.append(Line(900 + i, fold(label), "", votes, seats, ""))
                group = ""
                continue
            council.lines.append(Line(position, label, label, votes, seats, group))
        independents = sum(x.votes for x in council.lines if not x.party)
        if (
            mayor.total() != mayor.accounting[VALID]
            or mayor.total() != number(total_m[0][4])
            or council.total() != council.accounting[VALID]
            or number(total_c[0][4]) not in (council.total(), council.total() - independents)
        ):
            raise ValueError(
                f"2019 {name}: mayoral votes {mayor.total()} / {mayor.accounting[VALID]}, "
                f"council votes {council.total()} / {council.accounting[VALID]}"
            )
        if name in units:
            raise ValueError(f"2019: {name} printed twice")
        units[name] = Unit(qarku, "BASHKIA", name, mayor, council)
    return units


# The local elections of 8 May 2011, one file per county.

DAY_2011 = date(2011, 5, 8)
FILES_2011 = {
    "BERAT": f"{SITE}/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Berat.pdf",
    "DIBËR": "http://www.cec.org.al/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Dibra.pdf",
    "DURRËS": f"{SITE}/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Durres.pdf",
    "ELBASAN": f"{SITE}/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Elbasan.pdf",
    "FIER": f"{SITE}/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Fier.pdf",
    "GJIROKASTËR": f"{SITE}/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Gjirokaster.pdf",
    "KORÇË": f"{SITE}/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Korce.pdf",
    "KUKËS": "http://www.cec.org.al/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Kukes.pdf",
    "LEZHË": "http://www.cec.org.al/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Lezhe.pdf",
    "SHKODËR": "http://www.cec.org.al/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Shkoder.pdf",
    "TIRANË": f"{SITE}/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Tirana.pdf",
    "VLORË": f"{SITE}/Portals/0/Images/CEC/rez_2011/rezultatet/111118-TTH-Qarqe/Vlore.pdf",
}
ACCOUNTING_2011 = {
    "Zgjedhes ne liste": REGISTERED,
    "Flete votimi te gjetura ne kuti": BALLOTS,
    "Vota te pavlefshme": INVALID,
    "Vota te vlefshme": VALID,
}


@dataclass
class Block:
    """One results table of the 2011 files: a unit's or a voting center's two ballots."""

    mayor: Ballot = field(default_factory=Ballot)
    council: Ballot = field(default_factory=Ballot)
    mayor_total: int | None = None
    council_total: int | None = None
    seats_total: int | None = None

    def mayor_ok(self) -> bool:
        return bool(self.mayor.lines) and self.mayor_total == self.mayor.total()

    def council_ok(self) -> bool:
        return bool(self.council.lines) and self.council_total == self.council.total()

    def complete(self) -> bool:
        return self.mayor_ok() and self.council_ok()


def _row_2011(cells: list[Cell]) -> tuple[int, str, list[Cell], int, int | None] | None:
    """A numbered result row: its number, its first words, its other cells, its votes, and its seats."""
    match = re.fullmatch(r"(\d+) (.+)", cells[0][1])
    marks = [i for i, (_, t) in enumerate(cells) if t.endswith("%")]
    if not match or not marks or marks[-1] == 0 or not is_number(cells[marks[-1] - 1][1]):
        return None
    i = marks[-1]
    after = cells[i + 1 :]
    seats = number(after[0][1]) if after and is_number(after[0][1]) else None
    return int(match.group(1)), match.group(2), cells[1 : i - 1], number(cells[i - 1][1]), seats


def read_2011_file(qarku: str) -> dict[str, tuple[Block, dict[str, Block]]]:
    """The county file's units: each unit's own table and its voting centers' tables, in the order printed."""
    doc = document(FILES_2011[qarku])
    units: dict[str, tuple[Block, dict[str, Block]]] = {}
    block: Block | None = None
    section = ""
    group = ""
    subject_x = 0.0
    for page in doc:
        try:
            lines = rows(page)
        except Exception:  # a page of a cut-off copy
            continue
        for _, cells in lines:
            text = _cells_text(cells)
            first = cells[0][1]
            if re.fullmatch(r"Qarku .+", text):
                continue
            if m := re.fullmatch(r"(Bashkia|Komuna) (.+)", text):
                name = f"{m.group(1).upper()} {fold(m.group(2))}"
                block = Block()
                units[name] = (block, {})
                current = name
                section = ""
                continue
            if first == "QV" and len(cells) == 2 and units:
                block = Block()
                units[current][1][cells[1][1]] = block
                section = ""
                continue
            if block is None:
                continue
            if first in ACCOUNTING_2011 and len(cells) >= 2:
                values = [number(t) for _, t in cells[1:] if is_number(t)]
                if first == "Zgjedhes ne liste":
                    block.mayor.accounting[REGISTERED] = block.council.accounting[REGISTERED] = values[0]
                elif len(values) == 2:
                    block.mayor.accounting[ACCOUNTING_2011[first]] = values[0]
                    block.council.accounting[ACCOUNTING_2011[first]] = values[1]
            elif first.startswith("Pjesmarrja") and len(cells) >= 3:
                voters = [number(t) for _, t in cells[1:] if is_number(t)]
                if voters:
                    block.mayor.accounting[VOTERS] = block.council.accounting[VOTERS] = voters[-1]
            elif text.startswith("Rezultatet e zgjedhjeve per kryetarin"):
                section = "mayor"
            elif text.startswith("Rezultatet e zgjedhjeve per keshillin"):
                section, group = "council", ""
            elif first == "Nr" and section == "mayor":
                subject_x = next((x for x, t in cells if t.startswith("Subjekti")), 280) - 6
            elif section == "mayor" and text.startswith("Shuma:"):
                totals = [t for _, t in cells if is_number(t)]
                block.mayor_total = number(totals[0]) if totals else None
            elif section == "council" and text.startswith("Total:"):
                values = [t for _, t in cells[1:] if is_number(t)]
                block.council_total = number(values[0]) if values else None
                block.seats_total = number(values[1]) if len(values) > 1 else None
                section = ""
            elif section == "council" and text.startswith("Shuma"):
                continue
            elif section and (found := _row_2011(cells)):
                position, start, middle, votes, seats = found
                if section == "mayor":
                    who = [start, *(t for x, t in middle if x < subject_x)]
                    support = " ".join(t for x, t in middle if x >= subject_x)
                    block.mayor.lines.append(Line(position, fold(" ".join(who)), clean(support), votes))
                else:
                    label = clean(" ".join([start, *(t for _, t in middle)]))
                    block.council.lines.append(Line(position, label, label, votes, seats or 0, group))
            elif section == "council" and len(cells) == 1 and not is_number(first):
                group = clean(first)
    return units


def units_2011() -> dict[tuple[str, str], Unit]:
    """The 2011 units with their voting centers; a center keeps only the ballots whose table is whole."""
    out: dict[tuple[str, str], Unit] = {}
    for qarku in FILES_2011:
        for name, (block, centers) in read_2011_file(qarku).items():
            kind, _, short = name.partition(" ")
            unit = Unit(qarku, kind, short, block.mayor, block.council)
            if not block.mayor_ok() or not block.council_ok():
                unit.missing.append("the unit's own table is incomplete")
                continue
            for code, c in centers.items():
                unit.centers[code] = (
                    c.mayor if c.mayor_ok() else Ballot(c.mayor.accounting),
                    c.council if c.council_ok() else Ballot(c.council.accounting),
                )
            out[(qarku, short)] = unit
    return out


# The local elections of 12 October 2003, one file per county.

DAY_2003 = date(2003, 10, 12)
FOLDER_2003 = "http://cec.org.al/Portals/0/Documents/CEC%202013/zgjedhje-vendore/2003/rezultatet_2003"
FILES_2003 = {
    "BERAT": f"{FOLDER_2003}/qarku%20berat.pdf",
    "DURRËS": f"{FOLDER_2003}/qarku%20durres.pdf",
    "ELBASAN": f"{FOLDER_2003}/qarku%20elbasan.pdf",
    "FIER": f"{FOLDER_2003}/qarku%20fier.pdf",
    "GJIROKASTËR": f"{FOLDER_2003}/qarku%20gjirokaster.pdf",
    "KORÇË": f"{FOLDER_2003}/qarku%20korce.pdf",
    "KUKËS": f"{FOLDER_2003}/qarku%20kukes.pdf",
    "SHKODËR": f"{FOLDER_2003}/qarku%20shkoder.pdf",
    "TIRANË": f"{FOLDER_2003}/qarku%20tirane.pdf",
    "VLORË": f"{FOLDER_2003}/qarku%20vlore.pdf",
}
LEFT_OUT_2003 = {
    "DIBËR": f"{FOLDER_2003}/qarku%20diber.pdf",
    "LEZHË": f"{FOLDER_2003}/qarku%20lezhe.pdf",
}


BROKEN_2003: list[str] = []


def _leader(name: str) -> str:
    """A 2003 list name without its leader's initials, which the file prints after it: "PARTIA X (A. B)"."""
    return clean(re.sub(r"\s*\([^()]*\)\s*$", "", name))


def _center_row(unit: Unit, tokens: list[int]) -> None:
    """One row of a 2003 voting center table: the mayoral votes, the council votes, or both, each with its total.

    The council table repeats a row index cut to its last digits, which sits between the two tables."""
    k = len(unit.mayor.lines)
    listed = [x for x in unit.council.lines if x.votes >= 0]
    m = len(listed)
    if len(tokens) < 4:
        return
    code = str(tokens[1])
    mayor, council = unit.centers.get(code, (Ballot(), Ballot()))
    left = tokens[2 : 3 + k]
    right = tokens[len(tokens) - m - 1 :]
    if len(tokens) >= 3 + k + m + 1 and sum(left[:-1]) == left[-1] and sum(right[:-1]) == right[-1]:
        mayor = Ballot({}, [Line(x.position, x.name, x.party, v) for x, v in zip(unit.mayor.lines, left, strict=False)])
        council = Ballot({}, [Line(x.position, x.name, x.party, v) for x, v in zip(listed, right, strict=False)])
    elif len(tokens) == 3 + k and sum(left[:-1]) == left[-1]:
        mayor = Ballot({}, [Line(x.position, x.name, x.party, v) for x, v in zip(unit.mayor.lines, left, strict=False)])
    elif len(tokens) == 2 + m + 1 and sum(right[:-1]) == right[-1]:
        council = Ballot({}, [Line(x.position, x.name, x.party, v) for x, v in zip(listed, right, strict=False)])
    else:
        unit.missing.append(f"center row {tokens}")
        return
    unit.centers[code] = (mayor, council)


def _settle_centers(unit: Unit) -> None:
    """Keep each ballot's center figures only where they add up to the unit's own."""
    for i, ballot in enumerate((unit.mayor, unit.council)):
        counted = {x.position: x.votes for x in ballot.lines if x.votes >= 0}
        summed: dict[int, int] = defaultdict(int)
        for c in unit.centers.values():
            for x in c[i].lines:
                summed[x.position] += x.votes
        if dict(summed) != counted:
            unit.missing.append("mayor centers" if i == 0 else "council centers")
            for code, pair in unit.centers.items():
                unit.centers[code] = (Ballot(), pair[1]) if i == 0 else (pair[0], Ballot())
    unit.centers = {code: c for code, c in unit.centers.items() if c[0].lines or c[1].lines}


def read_2003_file(qarku: str) -> dict[tuple[str, str], Unit]:
    doc = document(FILES_2003[qarku])
    units: dict[tuple[str, str], Unit] = {}
    unit: Unit | None = None
    section = ""
    totals: dict[str, int] = {}
    for page in doc:
        for _, cells in rows(page):
            if len(cells) > 2 and re.fullmatch(r"\d+", cells[0][1]) and not is_number(cells[1][1]):
                cells = [(cells[0][0], f"{cells[0][1]} {cells[1][1]}"), *cells[2:]]
            text = _cells_text(cells)
            first = cells[0][1]
            header = re.fullmatch(r"(BASHKIA|KOMUNA) (.+)", text)
            if header and cells[0][0] > 150 and not any(is_number(t) for _, t in cells):
                unit = Unit(qarku, header.group(1), fold(header.group(2)), Ballot(), Ballot())
                if (qarku, unit.name) in units:
                    raise ValueError(f"2003 {unit.name} printed twice")
                units[(qarku, unit.name)] = unit
                section = ""
                continue
            if unit is None:
                continue
            if first == "Zgjedhes ne Liste" and len(cells) == 2:
                unit.mayor.accounting[REGISTERED] = unit.council.accounting[REGISTERED] = number(cells[1][1])
            elif text.startswith("Rezultatet e zgjedhjeve per kryetar") and "cdo qv" not in text:
                if unit.mayor.lines:  # Tirana's own tables are followed by those of its municipal units
                    BROKEN_2003.append(f"{unit.kind} {unit.name}")
                    units.pop((qarku, unit.name), None)
                    unit = None
                    continue
                section = "mayor"
            elif text.startswith("Rezultatet e zgjedhjeve per anetaret e keshillit"):
                section = "council"
            elif text.startswith("Rezultatet e zgjedhjeve per kandidatet per kryetar ne cdo qv"):
                section = "centers"
            elif first == "FITUES":
                unit.winner = fold(" ".join(t for _, t in cells[1:]))
            elif section in ("mayor", "council") and text.startswith("Total:"):
                values = [t for _, t in cells[1:] if is_number(t)]
                if values:
                    totals[f"{unit.name}:{section}"] = number(values[0])
                section = ""
            elif section in ("mayor", "council") and (m := re.fullmatch(r"(\d+) (.+)", first)):
                position, label = int(m.group(1)), m.group(2)
                marks = [i for i, (_, t) in enumerate(cells) if t.endswith("%")]
                if section == "mayor":
                    i = marks[-1]
                    support = " ".join(t for _, t in cells[1 : i - 1])
                    unit.mayor.lines.append(Line(position, fold(label), clean(support), number(cells[i - 1][1])))
                elif marks and is_number(cells[marks[-1] - 1][1]) and section == "council":
                    i = marks[-1]
                    seats = number(cells[i + 1][1]) if len(cells) > i + 1 and is_number(cells[i + 1][1]) else 0
                    name = _leader(" ".join([label, *(t for _, t in cells[1 : i - 1])]))
                    unit.council.lines.append(Line(position, name, name, number(cells[i - 1][1]), seats))
                elif not marks:  # an independent candidate, printed with a seat and no votes
                    seats = number(cells[-1][1]) if len(cells) > 1 and is_number(cells[-1][1]) else 0
                    unit.council.lines.append(Line(position, fold(label), "", -1, seats))
            elif section == "centers" and re.fullmatch(r"\d+", first) and len(cells) > 3:
                tokens = [number(v) for _, t in cells for v in t.split() if re.fullmatch(r"\d+", v)]
                _center_row(unit, tokens)
    for unit in units.values():
        _settle_centers(unit)
        for section, ballot in (("mayor", unit.mayor), ("council", unit.council)):
            total = totals.get(f"{unit.name}:{section}")
            counted = sum(x.votes for x in ballot.lines if x.votes >= 0)
            if total != counted:
                raise ValueError(f"2003 {unit.name} {section}: the lines add up to {counted}, the table prints {total}")
    return units


# The parliamentary election of 3 July 2005: the national proportional ballot.

DAY_2005 = date(2005, 7, 3)
PARTIES_2005 = f"{SITE}/Portals/0/Documents/CEC%202013/zgjedhje-per-kuvend/2005/parti_rang_vendi.pdf"
MEMBERS_2005 = f"{SITE}/Portals/0/Documents/CEC%202013/zgjedhje-per-kuvend/2005/perberja_emerore_e_kuvendit.pdf"


@dataclass
class National:
    registered: int
    valid: int
    invalid: int
    lines: list[Line]
    total: int


@functools.cache
def read_2005() -> National:
    page = document(PARTIES_2005)[0]
    text = page.get_text()
    head = re.search(r"([\d,]+)\n([\d.]+%)\n([\d,]+)\n([\d,]+)\n", text)
    if not head:
        raise ValueError("2005: no national figures")
    registered, valid, invalid = number(head.group(1)), number(head.group(3)), number(head.group(4))
    found: list[tuple[float, int, str, int]] = []
    total = 0
    for y, cells in rows(page):
        first = cells[0][1]
        if first == "Total:":
            total = number(cells[1][1])
        elif (m := re.fullmatch(r"(\d+)( .*)?", first)) and any(t.endswith(" %") for _, t in cells):
            mark = next(i for i, (_, t) in enumerate(cells) if t.endswith(" %"))
            found.append((y, int(m.group(1)), clean(cells[mark - 2][1]), number(cells[mark - 1][1])))
    # a party's name is printed on one to three lines around its row; each line goes to the nearest row
    names: dict[int, list[Word]] = defaultdict(list)
    for w in words(page):
        if (
            55 < w[0] < 235
            and found[0][0] - 20 < mid(w)[1] < found[-1][0] + 20
            and not re.fullmatch(r"\d+", w[4])
            and w[4] != "Total:"
        ):
            names[min(found, key=lambda f: abs(f[0] - w[3]))[1]].append(w)
    lines = [
        Line(
            position,
            clean(joined(names[position]).replace("Aleanca për Liri, Drejtësi dhe Mirëqenie", "")),
            abbreviation,
            votes,
        )
        for _, position, abbreviation, votes in found
    ]
    if sum(x.votes for x in lines) != total:
        raise ValueError(f"2005: the parties add up to {sum(x.votes for x in lines)}, the table prints {total}")
    return National(registered, valid, invalid, lines, total)


SECTIONS_2005 = {
    "PARTIA DEMOKRATIKE": "PD",
    "PARTIA SOCIALISTE": "PSSH",
    "PARTIA REPUBLIKANE": "PR",
    "PARTIA SOCIALDEMOKRATE": "PSD",
    "LËVIZJA SOCIALISTE PËR INTEGRIM": "LSI",
    "PARTIA DEMOKRATE E RE": "PDR",
    "PARTIA AGRARE AMBJENTALISTE": "PAA",
    "PARTIA ALEANCA DEMOKRATIKE": "AD",
    "PARTIA DEMOKRISTIANE": "PDK",
    "PARTIA BASHKIMI PWR TW DREJTAT E NJERIUT": "PBDNJ",
    "PARTIA DEMOKRACIA SOCIALE": "PDSSH",
    "BASHKIMI LIBERAL DEMOKRAT": "BLD",
    "I PAVARUR": "",
}


@functools.cache
def seats_2005() -> dict[str, int]:
    """The supplementary seats of each party, counted from the list of the Assembly's members by how each was elected."""
    seats: dict[str, int] = defaultdict(int)
    zones = 0
    section = ""
    for page in document(MEMBERS_2005):
        for _, cells in rows(page):
            text = _cells_text(cells)
            if m := re.fullmatch(r"[IVXL]+\. (.+)", text):
                section = SECTIONS_2005[m.group(1)]
                continue
            for x, t in cells:
                if "√" in t:
                    if x < 470:
                        zones += 1
                    else:
                        seats[section] += 1
    if zones != 100 or sum(seats.values()) != 40:
        raise ValueError(f"2005: {zones} zone members and {sum(seats.values())} supplementary members")
    return dict(seats)


# Places.

ALIASES_2019 = {"URA VAJGURORE": "DIMAL"}  # renamed Dimal in 2022


@functools.cache
def towns_2023() -> dict[str, tuple[str, str]]:
    """The al.kqz municipalities of the 2023 local elections, by name: their county and name as al.kqz keys them."""
    names = (
        "BERAT:BERAT BERAT:DIMAL BERAT:KUÇOVË BERAT:POLIÇAN BERAT:SKRAPAR DIBËR:BULQIZË DIBËR:DIBËR DIBËR:KLOS "
        "DIBËR:MAT DURRËS:DURRËS DURRËS:KRUJË DURRËS:SHIJAK ELBASAN:BELSH ELBASAN:CËRRIK ELBASAN:ELBASAN "
        "ELBASAN:GRAMSH ELBASAN:LIBRAZHD ELBASAN:PEQIN ELBASAN:PRRENJAS FIER:DIVJAKË FIER:FIER FIER:LUSHNJE "
        "FIER:MALLAKASTËR FIER:PATOS FIER:ROSKOVEC GJIROKASTËR:DROPULL GJIROKASTËR:GJIROKASTËR GJIROKASTËR:KËLCYRË "
        "GJIROKASTËR:LIBOHOVË GJIROKASTËR:MEMALIAJ GJIROKASTËR:PËRMET GJIROKASTËR:TEPELENË KORÇË:DEVOLL "
        "KORÇË:KOLONJË KORÇË:KORÇË KORÇË:MALIQ KORÇË:POGRADEC KORÇË:PUSTEC KUKËS:HAS KUKËS:KUKËS KUKËS:TROPOJË "
        "LEZHË:KURBIN LEZHË:LEZHË LEZHË:MIRDITË SHKODËR:FUSHË-ARRËZ SHKODËR:MALËSI_E_MADHE SHKODËR:PUKË "
        "SHKODËR:SHKODËR SHKODËR:VAU-DEJËS TIRANË:KAMËZ TIRANË:KAVAJË TIRANË:RROGOZHINË TIRANË:TIRANË TIRANË:VORË "
        "VLORË:DELVINË VLORË:FINIQ VLORË:HIMARË VLORË:KONISPOL VLORË:SARANDË VLORË:SELENICË VLORË:VLORË"
    )
    out: dict[str, tuple[str, str]] = {}
    for item in names.split():
        qarku, name = item.replace("_", " ").split(":")
        out[squash(name)] = (qarku, name)
    return out


def squash(name: str) -> str:
    """A place or party name reduced to its letters, so spellings with and without diacritics or hyphens match."""
    return re.sub(r"[^a-z0-9]", "", plain(fold(name)).replace("ë", "e").replace("ç", "c"))


def town_2023(name: str) -> tuple[str, str]:
    found = towns_2023().get(squash(ALIASES_2019.get(name, name)))
    if found is None:
        found = next((v for k, v in towns_2023().items() if k == squash(name).replace("idejes", "dejes")), None)
    if found is None:
        raise ValueError(f"2019: {name} is not an al.kqz municipality")
    return found


COUNTY_CODES = (
    "BERAT",
    "DIBËR",
    "DURRËS",
    "ELBASAN",
    "FIER",
    "GJIROKASTËR",
    "KORÇË",
    "KUKËS",
    "LEZHË",
    "SHKODËR",
    "TIRANË",
    "VLORË",
)


def county(qarku: str) -> str:
    """The al.kqz county area."""
    found = next(c for c in COUNTY_CODES if squash(c) == squash(qarku))
    return kqz("area", "al.qarku", found)


def commune(qarku: str, kind: str, name: str) -> str:
    return key("area", "al.komuna", squash(qarku), squash(name))


@dataclass(frozen=True)
class Place:
    qarku: str
    kind: str  # BASHKIA or KOMUNA
    name: str  # as the latest file spells it


@functools.cache
def communes() -> dict[str, Place]:
    """The municipalities and communes of 2003 and 2011, by area id; 2011's spelling wins."""
    out: dict[str, Place] = {}
    for units in (local_2003(), local_2011()):
        for u in units.values():
            out[commune(u.qarku, u.kind, u.name)] = Place(u.qarku, u.kind, u.name)
    return out


@functools.cache
def local_2003() -> dict[tuple[str, str], Unit]:
    out: dict[tuple[str, str], Unit] = {}
    for qarku in FILES_2003:
        out.update(read_2003_file(qarku))
    return out


@functools.cache
def local_2011() -> dict[tuple[str, str], Unit]:
    return units_2011()


def shapes() -> dict[tuple[str, str], shapely.Geometry]:
    """The geoBoundaries outlines of the 373 units of 2013, by county and name; a county holds each name once."""
    counties: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, COUNTIES).path.read_bytes())["features"]:
        counties[squash(feature["properties"]["shapeName"])] = shapely.make_valid(
            shapely.geometry.shape(feature["geometry"])
        )
    found: dict[tuple[str, str], list[shapely.Geometry]] = defaultdict(list)
    for feature in json.loads(raw.cached(SOURCE, COMMUNES).path.read_bytes())["features"]:
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        point = shapely.point_on_surface(shape)
        home = next((c for c, outline in counties.items() if outline.contains(point)), None)
        if home is None:
            home = min(counties, key=lambda c: counties[c].distance(point))
        found[(home, squash(feature["properties"]["shapeName"]))].append(shape)
    out: dict[tuple[str, str], shapely.Geometry] = {}
    for k, v in found.items():
        if len(v) != 1:
            continue
        polygons = [
            q
            for part in shapely.get_parts(v[0])
            if shapely.get_type_id(part) in (3, 6)
            for q in shapely.get_parts(part)
        ]
        out[k] = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    return out


def shape_of(place: Place, outlines: dict[tuple[str, str], shapely.Geometry]) -> shapely.Geometry | None:
    q = squash(place.qarku)
    for name in (place.name, re.sub(r"-(.+)$", "", place.name), re.sub(r"^QENDER-?", "QENDER ", place.name)):
        if (q, squash(name)) in outlines:
            return outlines[(q, squash(name))]
    return None


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Albania's municipalities and communes of 2000 to 2015, the units of the local elections of 2003 and 2011, "
        "as the Central Election Commission's former site lists them",
    )
    p.external |= {ALBANIA, COUNTRY, "al.qarku", *(county(c) for c in COUNTY_CODES)}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Komisioni Qendror i Zgjedhjeve",
        title="Election results on the Commission's former site, cec.org.al",
        url=f"{SITE}/",
        license="Official election results of the Republic of Albania, published for public use",
        license_url=f"{SITE}/",
        attribution="Central Election Commission of the Republic of Albania, cec.org.al",
        notes="The Commission's files as the Internet Archive kept them under the Commission's own addresses.",
    )
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen: Albania ADM3 (municipalities and communes, 2013)",
        url="https://www.geoboundaries.org/",
        license="Open Data Commons Open Database License 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), from OpenStreetMap contributors",
        notes="Boundary year 2013: the 373 municipalities and communes before the 2015 reform. Generalized shapes.",
    )
    p.artifact(raw.cached(SOURCE, COMMUNES), SHAPES_ID)
    p.uses(raw.cached(SOURCE, COUNTIES))
    p.add(
        "scheme",
        code="al.komuna",
        polity_id=ALBANIA,
        kind="administrative",
        rank=2,
        noun="municipality or commune",
        code_system=None,
    )
    outlines = shapes()
    matched = 0
    for id, place in sorted(communes().items(), key=lambda item: (item[1].qarku, item[1].name)):
        p.add("area", id=id, scheme_code="al.komuna")
        p.add("area_code", area_id=id, code=f"{squash(place.qarku)}:{squash(place.name)}")
        p.add("area_parent", area_id=id, parent_id=county(place.qarku))
        noun = "Bashkia" if place.kind == "BASHKIA" else "Komuna"
        p.name(id, title(place.name), "sq", "official")
        p.name(id, f"{noun} {title(place.name)}", "sq", "native", False)
        outline = shape_of(place, outlines)
        if outline is None:
            continue
        matched += 1
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=date(2000, 7, 31),
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPES_ID,
        )
    print(f"{SOURCE}/geo: {len(communes())} units, {matched} with a shape", flush=True)
    return p


# Parties, councils, and mayors.

ABBREVIATIONS_2019 = {
    "ASE": "Aleanca për Shqipërinë Europiane",
    "BD": "Partia Bindja Demokratike",
    "SH.N": "Shpresa për Ndryshim",
}
INDEPENDENT = ("", "I PAVARUR", "KANDIDAT I PAVARUR")
NO_GROUP = ("", "Jashte Koalicionit", "Subjekte jashte Koalicionit")
VOTERS_GROUP = "Kanditate te propozuar nga zgjedhes"  # independent candidates proposed by voters, 2011


def party(name: str) -> str:
    return key("party", squash(name))


def is_bloc(name: str) -> bool:
    folded = fold(name)
    return "+" in folded or "," in folded or folded.startswith(("ALEANCA PER", "ALEANCA PËR", "KOALICION", "SHPRESA"))


def party_names() -> dict[str, set[str]]:
    """Every party, coalition, and supporting label the packages name, by party id, with its spellings."""
    names: dict[str, set[str]] = defaultdict(set)

    def add(label: str) -> None:
        if label and fold(label) not in INDEPENDENT:
            names[party(label)].add(clean(label))

    for units in (local_2003(), local_2011()):
        for u in units.values():
            for x in u.mayor.lines:
                add(x.party)
            for x in u.council.lines:
                add(x.party if x.votes >= 0 or x.party else "")
                if x.group not in NO_GROUP and x.group != VOTERS_GROUP:
                    add(x.group)
    for u in read_2019().values():
        for x in u.mayor.lines:
            add(ABBREVIATIONS_2019.get(x.party, x.party))
        for x in u.council.lines:
            add(x.party)
            if x.group not in NO_GROUP:
                add(x.group)
    for x in read_2005().lines:
        add(x.name)
    return names


def council_body(place: Place) -> str:
    return key("body", "council", squash(place.qarku), squash(place.name))


def mayor_office(place: Place) -> str:
    return key("office", "mayor", squash(place.qarku), squash(place.name))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties and coalitions, and the councils and mayors of the municipalities and communes of 2000 to 2015, "
        "from the Central Election Commission's former site",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ALBANIA, COUNTRY}
    for place in sorted(communes().values(), key=lambda item: (item.qarku, item.name)):
        body, office = council_body(place), mayor_office(place)
        noun, english = ("Bashkiak", "Municipal") if place.kind == "BASHKIA" else ("i Komunës", "Commune")
        head = "Bashkisë" if place.kind == "BASHKIA" else "Komunës"
        nice = title(place.name)
        p.add("body", id=body, polity_id=ALBANIA, kind="council")
        p.name(body, f"{english} Council of {nice}", "en", "official")
        p.name(
            body,
            f"Këshilli {noun} {nice}" if place.kind == "BASHKIA" else f"Këshilli i Komunës {nice}",
            "sq",
            "native",
            False,
        )
        p.add("office", id=office, polity_id=ALBANIA, kind="mayor", body_id=None)
        p.name(office, f"Mayor of {nice}" if place.kind == "BASHKIA" else f"Head of {nice} Commune", "en", "official")
        p.name(office, f"Kryetar i {head} {nice}", "sq", "native", False)
    for id, spellings in sorted(party_names().items()):
        best = max(spellings, key=lambda s: (s != s.upper(), len(s), s))
        p.add("party", id=id, kind="bloc" if any(is_bloc(s) for s in spellings) else "party", polity_id=ALBANIA)
        p.add("external_id", entity_id=id, scheme="al.cec.party", value=squash(best))
        for name in sorted(spellings):
            p.name(id, name, "sq", "official", name == best)
    return p


# The elections.


class Facts:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            name: [] for name in ("result_set_id", "unit_id", "option_id", "measure", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        for column, item in zip(self.columns.values(), (result_set, unit, option, measure, value), strict=True):
            column.append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


@dataclass(frozen=True)
class LocalElection:
    day: date
    names: tuple[str, str]
    files: tuple[str, ...]
    method: str


LOCAL_2003 = LocalElection(
    DAY_2003,
    ("2003 Albanian local elections", "Zgjedhjet vendore, 2003"),
    tuple(FILES_2003.values()),
    "The Commission's results file for each county prints, for each municipality and commune, the mayoral "
    "candidates' votes, the council lists' votes and seats, the registered voters, and a table of both ballots' votes "
    "by voting center (QV). Each unit is one contest on each ballot, and its figures are stored as declared. A "
    "ballot's votes are stored by voting center where the center table adds up to the unit's figures, and otherwise "
    "as one aggregate unit for the municipality or commune. The file prints one set of ballot figures (valid, invalid, "
    "irregular, and contested ballots) without saying which ballot it counts, so only the registered voters are "
    "stored. Some councils seat an independent candidate whose votes the file does not print; the seat is stored "
    "without votes. The copies of the Dibër and Lezhë county files cannot be read, and Tirana's own results are "
    "followed by those of its municipal units in a layout this package does not read, so those units are missing.",
)
LOCAL_2011 = LocalElection(
    DAY_2011,
    ("2011 Albanian local elections", "Zgjedhjet vendore, 2011"),
    tuple(FILES_2011.values()),
    "The Commission's results file for each county prints, for each municipality and commune and then for each of "
    "its voting centers (QV), the accounting of the mayoral and council ballots, the mayoral candidates' votes, and "
    "the council lists' votes; the unit's table adds the council seats. Each unit is one contest on each ballot, and "
    "the unit's figures are stored as declared. Each voting center's figures are stored where its table is whole; "
    "what the unit's figures hold beyond its centers is stored as one aggregate unit for the municipality or commune. "
    "Valid votes are the sum of the lines; a few units print a valid-vote count that differs from it. The copies of "
    "the Gjirokastër and Tirana county files are cut off, so the units on their missing pages are not stored; no "
    "mayor is declared in these files, so no mayoral winner is stored.",
)
LOCAL_2019 = LocalElection(
    DAY_2019,
    ("2019 Albanian local elections", "Zgjedhjet vendore, 2019"),
    (BULLETIN_2019, WINNERS_2019),
    "The Commission's bulletin of the 2019 local elections prints, for each of the 61 municipalities, the registered "
    "voters, the voters, the ballots found in the boxes, and the valid and invalid votes of each ballot, the mayoral "
    "candidates' votes, and the council lists' votes and seats. Each municipality is one contest on each ballot, "
    "stored as one aggregate unit. The mayor stored as elected is the one the Commission's decision 1097 of 27 July "
    "2019 names. The bulletin prints no results table for Selenicë, so it is missing. The bulletin's tables by voting "
    "center are not part of this package.",
)


def local_key(e: LocalElection) -> str:
    return f"{SOURCE}/{e.day}"


def local_units(e: LocalElection) -> list[tuple[Unit, str, str, str, str, str]]:
    """Each unit with its area, council, office, slug part, and code."""
    out: list[tuple[Unit, str, str, str, str, str]] = []
    if e.day == DAY_2019:
        for u in read_2019().values():
            qarku, name = town_2023(u.name)
            out.append(
                (
                    u,
                    kqz("area", "al.bashkia", qarku, name),
                    kqz("body", "council", qarku, name),
                    kqz("office", "mayor", qarku, name),
                    f"{plain(qarku)}/{plain(u.name).replace(' ', '-')}",
                    f"{e.day}:{squash(u.name)}",
                )
            )
        return out
    units = local_2003() if e.day == DAY_2003 else local_2011()
    for u in units.values():
        place = communes()[commune(u.qarku, u.kind, u.name)]
        out.append(
            (
                u,
                commune(u.qarku, u.kind, u.name),
                council_body(place),
                mayor_office(place),
                f"{plain(u.qarku)}/{plain(u.name).replace(' ', '-')}",
                f"{e.day}:{squash(u.qarku)}:{squash(u.name)}",
            )
        )
    return out


def winners_2019() -> str:
    return fold(" ".join(page.get_text() for page in document(WINNERS_2019)))


def local_election(e: LocalElection) -> Package:
    p = Package(
        local_key(e),
        SOURCE,
        f"The {e.names[0]} from the Central Election Commission's former site",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ALBANIA, COUNTRY, "al.qv"}
    for address in e.files:
        p.artifact(raw.cached(SOURCE, address), SOURCE_ID)
    election_id = key("election", e.day)
    p.add("election", id=election_id, polity_id=ALBANIA, kind="local", date_from=e.day, date_to=e.day, status="held")
    p.name(election_id, e.names[0], "en", "official")
    p.name(election_id, e.names[1], "sq", "native", False)
    path = f"al/{e.day}-local"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.add("note", id=key("note", election_id), entity_id=election_id, kind="methodology", body_md=e.method, lang="en")
    values = Facts()
    rests: set[str] = set()
    off_valid: list[str] = []
    over: list[str] = []
    elected = winners_2019() if e.day == DAY_2019 else ""
    scheme = "al.bashkia" if e.day == DAY_2019 else "al.komuna"
    for u, area, body, office, slug, code in sorted(local_units(e), key=lambda item: item[5]):
        if e.day == DAY_2019:
            p.external |= {area, body, office, "al.bashkia"}
        nice = title(u.name)
        for ballot_name, ballot in (("mayor", u.mayor), ("council", u.council)):
            mayoral = ballot_name == "mayor"
            contest = key("contest", e.day, ballot_name, code)
            result_set = key("result_set", e.day, ballot_name, code)
            seats = sum(x.seats for x in ballot.lines)
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="office" if mayoral else "list_allocation",
                office_id=office if mayoral else None,
                body_id=None if mayoral else body,
                district_area_id=area,
                magnitude=1 if mayoral else seats,
                round_number=1,
                default_result_kind="counted",
            )
            commune_ = u.kind == "KOMUNA"
            if mayoral:
                p.name(contest, f"{e.names[0]}, {'Head of' if commune_ else 'Mayor of'} {nice}", "en", "official")
                p.name(
                    contest,
                    f"{e.names[1].split(',')[0]}, Kryetar i {'Komunës' if commune_ else 'Bashkisë'} {nice}",
                    "sq",
                    "native",
                    False,
                )
            else:
                p.name(
                    contest, f"{e.names[0]}, {nice} {'Commune' if commune_ else 'Municipal'} Council", "en", "official"
                )
                p.name(
                    contest,
                    f"{e.names[1].split(',')[0]}, Këshilli {'i Komunës' if commune_ else 'Bashkiak'} {nice}",
                    "sq",
                    "native",
                    False,
                )
            p.add("slug", path=f"{path}/{slug}/{ballot_name}", entity_id=contest, is_current=True)
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
            options: dict[int, str] = {}
            for x in ballot.lines:
                option = options[x.position] = key("option", e.day, ballot_name, code, x.position)
                label = ABBREVIATIONS_2019.get(x.party, x.party) if e.day == DAY_2019 and mayoral else x.party
                independent = fold(label) in INDEPENDENT or (not mayoral and x.group == VOTERS_GROUP)
                if mayoral:
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=None if independent else party(label),
                        ballot_position=x.position,
                        ballot_label=x.name,
                    )
                    who = key("person", e.day, code, x.position)
                    p.add("person", id=who)
                    p.name(who, title(x.name), "sq", "official")
                    p.add(
                        "candidacy",
                        id=key("candidacy", e.day, code, x.position),
                        person_id=who,
                        option_id=option,
                        role="head",
                        ballot_name=x.name,
                        member_of_party_id=None if independent else party(label),
                        is_independent=independent,
                    )
                else:
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="independents" if independent else "list",
                        party_id=None if independent else party(label),
                        ballot_position=x.position if x.position < 900 else None,
                        ballot_label=x.name,
                    )
                if x.votes >= 0:
                    p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=x.votes)
                if x.seats:
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=x.seats)
            if mayoral:
                winner = winner_of(e, u, elected)
                if winner is not None:
                    p.add("outcome", result_set_id=result_set, option_id=options[winner], kind="won", seats=1)
            for measure, n in sorted(ballot.accounting.items()):
                if measure != BALLOTS:
                    p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=n)
            counted = sum(x.votes for x in ballot.lines if x.votes >= 0)
            if VALID in ballot.accounting and ballot.accounting[VALID] != counted:
                off_valid.append(f"{nice} {ballot_name} ({counted} votes, {ballot.accounting[VALID]} valid)")
            # the voting centers, then what the unit holds beyond them
            rest_votes = {x.position: x.votes for x in ballot.lines if x.votes >= 0}
            rest_accounting = {m: n for m, n in ballot.accounting.items() if m != BALLOTS}
            here = {center: pair[0] if mayoral else pair[1] for center, pair in u.centers.items()}
            here = {center: c for center, c in here.items() if c.lines}
            summed: dict[int, int] = defaultdict(int)
            for c in here.values():
                for x in c.lines:
                    summed[x.position] += x.votes
            if any(rest_votes.get(position, -1) < n for position, n in summed.items()):
                here = {}  # the centers' lines do not fit the unit's; keep only the unit's own figures
            for center, c in sorted(here.items()):
                unit = key("unit", f"{code}:{center}")
                for measure, n in c.accounting.items():
                    if measure in rest_accounting:
                        values.add(result_set, unit, None, measure, n)
                        rest_accounting[measure] -= n
                for x in c.lines:
                    values.add(result_set, unit, options[x.position], VOTES, x.votes)
                    rest_votes[x.position] -= x.votes
                if VALID in c.accounting and c.accounting[VALID] != c.total():
                    off_valid.append(
                        f"QV {center} of {nice} {ballot_name} ({c.total()} votes, {c.accounting[VALID]} valid)"
                    )
                if c.accounting.get(VOTERS, 0) > c.accounting.get(REGISTERED, 1 << 30):
                    over.append(f"QV {center} of {nice}")
            if any(v < 0 for v in (*rest_votes.values(), *rest_accounting.values())):
                raise ValueError(f"{e.day} {u.name} {ballot_name}: the centers exceed the unit's figures")
            if any((*rest_votes.values(), *rest_accounting.values())):
                unit = key("unit", f"{code}:{ballot_name}" if u.centers else code)
                rests.add(unit)
                for measure, n in rest_accounting.items():
                    values.add(result_set, unit, None, measure, n)
                for position, n in rest_votes.items():
                    values.add(result_set, unit, options[position], VOTES, n)
    for u, area, _, _, _, code in local_units(e):
        for center in sorted(u.centers):
            p.add(
                "reporting_unit",
                id=key("unit", f"{code}:{center}"),
                polity_id=ALBANIA,
                scheme_code="al.qv",
                code=f"{code}:{center}",
                area_relation="within",
                area_id=area,
                kind="standard",
            )
        for ballot_name in ("mayor", "council"):
            if u.centers:
                unit_code = f"{code}:{ballot_name}"
            elif ballot_name == "council":
                continue
            else:
                unit_code = code
            p.add(
                "reporting_unit",
                id=key("unit", unit_code),
                polity_id=ALBANIA,
                scheme_code=scheme,
                code=unit_code,
                area_relation="within",
                area_id=area,
                kind="aggregate",
            )
    frame = values.frame()
    used = set(frame["unit_id"].unique().to_list())
    p.frame("result_value", frame)
    p.rows["reporting_unit"] = [r for r in p.rows["reporting_unit"] if r["id"] in used]
    if off_valid:
        p.accept(
            "accounting.votes_valid",
            "The Commission's own tables print a valid-vote count that differs from the sum of the lines' votes, which "
            "add up to the table's own total: " + ", ".join(off_valid),
        )
    if over:
        p.accept(
            "accounting.turnout",
            "The Commission's own tables print more voters than registered at " + ", ".join(sorted(set(over))),
        )
    print(f"{local_key(e)}: {len(local_units(e))} units, {len(used)} reporting units", flush=True)
    return p


def winner_of(e: LocalElection, u: Unit, elected: str) -> int | None:
    """The position of the mayor the Commission declared elected, where its files name one."""
    if e.day == DAY_2003 and u.winner:
        found = [x.position for x in u.mayor.lines if u.winner.startswith(x.name)]
        return found[0] if len(found) == 1 else None
    if e.day == DAY_2019:
        best = max(u.mayor.lines, key=lambda x: x.votes)
        surname = best.name.split()[-1]
        return best.position if best.name.split()[0] in elected and surname in elected else None
    return None


ASSEMBLY_2005_KEY = f"{SOURCE}/{DAY_2005}"
NAMES_2005 = ("2005 Albanian parliamentary election", "Zgjedhjet për Kuvendin, 2005")
METHOD_2005 = (
    "In 2005 the Assembly's 140 members were elected in two tiers: 100 in single-member zones and 40 supplementary "
    "seats given to party lists from the votes of the second ballot, counted nationally, so that each party's seats "
    "come close to its share. This contest is that national list ballot. The votes of each party are the Commission's "
    "national result, which leaves out zone 092, where the list ballot was annulled; the registered voters and the "
    "valid and invalid votes it prints are for all 100 zones. The supplementary seats of each party are counted from "
    "the Commission's list of the Assembly's members, which marks how each member was elected. The single-member "
    "zones' results are not part of this package."
)


def assembly_2005() -> Package:
    data, seats = read_2005(), seats_2005()
    p = Package(
        ASSEMBLY_2005_KEY,
        SOURCE,
        f"The national list ballot of the {NAMES_2005[0]} from the Central Election Commission's former site",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    assembly = kqz("body", "assembly")
    p.external |= {ALBANIA, COUNTRY, assembly, "world.country"}
    for address in (PARTIES_2005, MEMBERS_2005):
        p.artifact(raw.cached(SOURCE, address), SOURCE_ID)
    election_id = key("election", DAY_2005)
    p.add(
        "election",
        id=election_id,
        polity_id=ALBANIA,
        kind="general",
        date_from=DAY_2005,
        date_to=DAY_2005,
        status="held",
    )
    p.name(election_id, NAMES_2005[0], "en", "official")
    p.name(election_id, NAMES_2005[1], "sq", "native", False)
    path = f"al/{DAY_2005}-parliamentary"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = key("contest", DAY_2005, "national")
    result_set = key("result_set", DAY_2005, "national")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=assembly,
        district_area_id=COUNTRY,
        magnitude=sum(seats.values()) - seats.get("", 0),
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, f"{NAMES_2005[0]}, supplementary seats", "en", "official")
    p.name(contest, "Zgjedhjet për Kuvendin 2005, mandatet plotësuese", "sq", "native", False)
    p.add("slug", path=f"{path}/national", entity_id=contest, is_current=True)
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
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD_2005, lang="en")
    unit = key("unit", f"{DAY_2005}:national")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ALBANIA,
        scheme_code="world.country",
        code=f"{DAY_2005}:ALB",
        area_relation="within",
        area_id=COUNTRY,
        kind="aggregate",
    )
    values = Facts()
    for measure, n in ((REGISTERED, data.registered), (VALID, data.valid), (INVALID, data.invalid)):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=n)
        values.add(result_set, unit, None, measure, n)
    for x in data.lines:
        option = key("option", DAY_2005, x.position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(x.name),
            ballot_position=x.position,
            ballot_label=x.name,
        )
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=x.votes)
        values.add(result_set, unit, option, VOTES, x.votes)
        if seats.get(x.party):
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats[x.party])
    p.frame("result_value", values.frame())
    p.accept(
        "accounting.votes_valid",
        f"The Commission prints {data.valid} valid votes for all 100 zones, and its party votes add up to {data.total} "
        "because they leave out zone 092, where the list ballot was annulled",
    )
    return p


# The steps.

LOCAL = (LOCAL_2003, LOCAL_2011, LOCAL_2019)


def files() -> list[str]:
    return [
        *FILES_2003.values(),
        *FILES_2011.values(),
        BULLETIN_2019,
        WINNERS_2019,
        PARTIES_2005,
        MEMBERS_2005,
    ]


def fetch() -> None:
    raw.fetch(SOURCE, COUNTIES, suffix=".geojson")
    raw.fetch(SOURCE, COMMUNES, suffix=".geojson")
    for address in files():
        raw.cached(SOURCE, address)  # the Commission's files are staged in the raw cache; see the module's note


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(local_key(e) for e in LOCAL), ASSEMBLY_2005_KEY]


def parse() -> None:
    geo().write()
    registry().write()
    for e in LOCAL:
        local_election(e).write()
    assembly_2005().write()


def emit() -> None:
    package.run("load", packages())
