"""Folketing elections of 1953 to 2005 by nomination district, from Statistics Denmark's historical election volumes.

Statistics Denmark publishes one volume per election as a scanned PDF with a text layer (VisPub cid 20199 to 20213 and
20327 to 20331). Each volume prints the official count of party votes for every nomination district (opstillingskreds)
with the totals of its amtskreds or storkreds and of the whole country. From 1971 the same table also gives each
district's registered voters, ballots cast, and blank and other invalid ballots; from 1990 those come from a separate
table with the same rows.

Each figure is read from the text layer with its page and box (`dst_folketing_read`). Every row's parties must add up to its
printed valid votes, and every district column must add up to its amtskreds's printed figure. A figure the recognition
garbled is mended only where the sums prove one value, and a figure no sum can prove is a curated constant in
`dst_folketing_volumes`, read from the page image. The national seats of each party are stored as declared. The volumes'
results for the Faroe Islands and Greenland are left out here.
"""

import difflib
import functools
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.dk import dst_folketing_read as read

SOURCE = "dk.dst_folketing"
DENMARK = str(uuid5(package.NAMESPACE, "polity\x1fDK"))
PAGE = "https://www.dst.dk/da/Statistik/udgivelser/VisPub?cid={cid}"
FILE = "https://www.dst.dk/pubfile/{cid}/{name}"
REGISTRY = f"{SOURCE}/registry"
DST_REGISTRY = "dk.dst/registry"
FOLKETING_BODY = str(uuid5(package.NAMESPACE, "dk.dst\x1fbody\x1ffolketing"))
SCHEME = "dk.opstillingskreds"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
DIGITIZED = 8
SKIP = {"pct", "skip"}
ACCOUNTING = {"registered", "cast", "valid", "valid4", "invalid", "blank", "other"}
OTHERS = "others"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "volumes")
SYSTEM_1953_APRIL = key("electoral_system", "1953-04")
SYSTEM_1953 = key("electoral_system", "1953")
SYSTEM_1964 = key("electoral_system", "1964")


@dataclass(frozen=True)
class Part:
    """One page of a table: its columns from left to right and, on the page that prints the row labels, the right
    edge of the label column. `top` and `bottom` bound the table on the page."""

    page: int
    roles: tuple[str, ...]
    left: float = 0.0
    top: float = 0.0
    bottom: float = 1e9


def part(page: int, *roles: str, left: float = 0.0, top: float = 0.0, bottom: float = 1e9) -> Part:
    return Part(page, tuple(roles), left, top, bottom)


@dataclass(frozen=True)
class Volume:
    day: date
    cid: int
    name: str
    title: str
    spreads: tuple[tuple[Part | tuple[Part, ...], ...], ...]  # side by side; a tuple of parts runs over pages
    parties: dict[str, str]  # column role (the party's letter) to the party's name as the volume prints it
    seats: dict[str, int]  # national seats in Denmark proper, by column role
    seats_page: int
    magnitude: int = 175
    system: str = SYSTEM_1964
    after: bool = False  # the amtskreds total follows its districts ("Tilsammen") instead of leading them
    fixes: dict[tuple[int, str], int] = field(default_factory=dict)  # (line, role) to the figure on the page image
    tolerance: float = 3.0

    @property
    def url(self) -> str:
        return FILE.format(cid=self.cid, name=self.name)

    @property
    def slug(self) -> str:
        return f"fv{self.day.year}" if self.day != date(1953, 4, 21) else "fv1953a"


def volumes() -> list[Volume]:
    from ea.sources.dk import dst_folketing_volumes

    return dst_folketing_volumes.VOLUMES


def fetch() -> None:
    for volume in volumes():
        raw.fetch(SOURCE, volume.url, suffix=".pdf")


NATION = re.compile(r"hele\s*landet|^\W*i\s*alt\W*$", re.IGNORECASE)
REGION = re.compile(
    r"^\W*[a-c]\s*[.,]\s|omr\S*de|k\S*benhavn\s*(-|og)?\s*frede|^\W*\S?\S?erne\W*$|^\W*jylland\W*$|^\W*$", re.IGNORECASE
)
AMT = re.compile(r"(stor|amts)\s*-?\s*kr\S*d|storkr\b|mtskr|a\S?ntskr|\sa\S{1,2}skreds", re.IGNORECASE)
ROMAN = re.compile(r"^\W*(II|III|IV|VI{0,3}|IX|XI{0,3})\s*[.,]\s*\D")
TOTAL = re.compile(r"t\S{0,2}l\s*sa\S*men|^\W*i\s*alt\W*$", re.IGNORECASE)
KREDS = re.compile(r"^\W{0,2}(\d{1,2}|[IlJ]|[IlJ]\d|\d[IlJ])\s*[.,:]\s*(.*)$")
HEADER = re.compile(r"(stor|amts)\s*-?\s*kr\S*d|storkr\b|^\W*[IVX]{1,5}\s*[.,]\s*\S", re.IGNORECASE)


@dataclass
class Line:
    kind: str  # nation, region, amt, kreds
    label: str
    cells: dict[str, read.Cell | None]
    amt: int = -1
    number: str = ""
    name: str = ""
    index: int = 0


def union(pages: list[list[list[read.Word]]], count: int) -> list[float]:
    """Column edges gathered over the pages of one table, for a column no single page fills: each page's columns are
    shifted onto the fullest page's, and a column found on another page is added where the fullest page has none."""
    found = [sorted(sorted(c)[len(c) // 2] for c in read.clusters(rows) if len(c) >= 2) for rows in pages]
    found.sort(key=len, reverse=True)
    base = list(found[0])
    for other in found[1:]:
        shifts = sorted(min((c - b for b in base), key=abs) for c in other)
        shift = shifts[len(shifts) // 2] if shifts else 0.0
        for c in other:
            if all(abs(c - shift - b) > 10 for b in base):
                base.append(c - shift)
        base.sort()
    if len(base) != count:
        raise ValueError(f"found {len(base)} columns over the table's pages, expected {count}")
    return base


def groups(spread: tuple[Part | tuple[Part, ...], ...]) -> list[tuple[Part, ...]]:
    return [item if isinstance(item, tuple) else (item,) for item in spread]


def every(volume: Volume) -> list[Part]:
    return [p for spread in volume.spreads for group in groups(spread) for p in group]


def rows(volume: Volume, pdf: Path) -> list[tuple[str, bool, dict[str, read.Cell | None]]]:
    """Every row of the volume's table: its label, whether it is a heading without figures, and its cells by role."""
    first = min(p.page for p in every(volume))
    last = max(p.page for p in every(volume))
    pages = read.pages(pdf, first, last)
    found: list[tuple[str, bool, dict[str, read.Cell | None]]] = []
    grids: dict[int, list[tuple[str, float, list[read.Word]]]] = {}
    edges: dict[int, list[float]] = {}
    templates: dict[tuple[str, ...], list[float]] = {}
    for p in every(volume):
        left = read.label_edge(pages[p.page], p.top, p.bottom) if p.left < 0 else p.left
        grids[p.page] = read.lines_of(
            pages[p.page],
            left=left,
            top=p.top,
            bottom=p.bottom,
            labels=p.left != 0,
            headers=HEADER if p.left != 0 and volume.after else None,
            tolerance=volume.tolerance,
        )
        full = [read.joined(g, 6.5) for _, _, g in grids[p.page]]
        full = [g for g in full if len(g) >= max(2, len(p.roles) // 2)]
        try:
            edges[p.page] = read.columns(full, len(p.roles))
            templates.setdefault(p.roles, edges[p.page])
        except ValueError:
            pass
    for p in every(volume):
        if p.page not in edges and p.roles not in templates:
            templates[p.roles] = union(
                [
                    [g for g in (read.joined(g, 6.5) for _, _, g in grids[q.page]) if len(g) >= 2]
                    for q in every(volume)
                    if q.roles == p.roles
                ],
                len(p.roles),
            )
        if p.page not in edges:
            full = [g for g in (read.joined(g, 6.5) for _, _, g in grids[p.page]) if len(g) >= 2]
            edges[p.page] = read.columns(full, len(p.roles), templates.get(p.roles))
    for spread in volume.spreads:
        parts: list[tuple[Part, list[read.Row]]] = [
            (group[0], [row for p in group for row in read.place(p.page, grids[p.page], edges[p.page])])
            for group in groups(spread)
        ]
        figures = [[row for row in got if not row.header] for _, got in parts]
        if all(p.left != 0 for p, _ in parts) and len(parts) > 1:
            figures = aligned(figures)
        lengths = [len(f) for f in figures]
        if len(set(lengths)) != 1:
            detail = "; ".join(f"page {p.page}: {n}" for (p, _), n in zip(parts, lengths, strict=True))
            raise ValueError(f"{volume.day}: the pages of a spread hold different numbers of rows ({detail})")
        headers = {id(row) for row in parts[0][1] if row.header}
        position = 0
        for row in [r for r in parts[0][1] if r.header or r in figures[0]]:
            if id(row) in headers:
                found.append((row.label, True, {}))
                continue
            cells: dict[str, read.Cell | None] = {}
            for (p, _), got in zip(parts, figures, strict=True):
                for role, cell in zip(p.roles, got[position].cells, strict=True):
                    if role not in SKIP:
                        cells[role] = cell
            found.append((row.label, False, cells))
            position += 1
    return found


def label_key(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower().replace("kbh", "").replace("kr", ""))[:10]


def aligned(tables: list[list[read.Row]]) -> list[list[read.Row]]:
    """Rows of tables that print the same labels, matched by label. A row the first table holds and another lacks
    gets an empty row there, so its figures are solved from the printed sums or the identities fail; a row only a
    later table holds is dropped."""
    out = [tables[0]]
    for other in tables[1:]:
        a = [label_key(r.label) for r in out[0]]
        b = [label_key(r.label) for r in other]
        n, m = len(a), len(b)
        gap = -0.6
        score = [[0.0] * (m + 1) for _ in range(n + 1)]
        move = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(1, n + 1):
            score[i][0], move[i][0] = score[i - 1][0] + gap, 1
        for j in range(1, m + 1):
            score[0][j], move[0][j] = score[0][j - 1] + gap, 2
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                similar = difflib.SequenceMatcher(None, a[i - 1], b[j - 1]).ratio() if a[i - 1] or b[j - 1] else 1.0
                options = (
                    (score[i - 1][j - 1] + (similar * 2 - 1), 0),
                    (score[i - 1][j] + gap, 1),
                    (score[i][j - 1] + gap, 2),
                )
                score[i][j], move[i][j] = max(options)
        pairs: list[tuple[int, int | None]] = []
        i, j = n, m
        while i > 0 or j > 0:
            step = move[i][j] if i > 0 and j > 0 else (1 if i > 0 else 2)
            if step == 0:
                pairs.append((i - 1, j - 1))
                i, j = i - 1, j - 1
            elif step == 1:
                pairs.append((i - 1, None))
                i -= 1
            else:
                j -= 1
        pairs.reverse()
        empty = len(other[0].cells) if other else 0
        out.append(
            [
                other[j] if j is not None else read.Row(out[0][i].label, other[0].page, 0.0, [None] * empty)
                for i, j in pairs
            ]
        )
    return out


def classify(
    volume: Volume, found: list[tuple[str, bool, dict[str, read.Cell | None]]]
) -> tuple[list[Line], list[str]]:
    """Place each row as the nation, a region, an amtskreds total, or a district; return the lines and amt names."""
    out: list[Line] = []
    names: list[str] = []
    amt = -1
    for label, header, cells in found:
        if not header and sum(c is not None for c in cells.values()) < 2:
            continue  # a page number or a stray mark, not a row of the table
        text = re.sub(r"^(\W{0,2}\d)\s+(\d)\s*([.,])", r"\1\2\3", " ".join(label.split()))
        if header:
            if AMT.search(text) or ROMAN.match(text):
                names.append(text)
                amt += 1
            continue
        if NATION.search(text) and not (
            volume.after and TOTAL.search(text) and amt >= 0 and out and out[-1].kind == "kreds"
        ):
            out.append(Line("nation", text, cells))
        elif volume.after and TOTAL.search(text):
            out.append(Line("amt", text, cells, amt=amt))
        elif not volume.after and (AMT.search(text) or ROMAN.match(text)):
            amt += 1
            names.append(text)
            out.append(Line("amt", text, cells, amt=amt))
        elif REGION.search(text) and not KREDS.match(text):
            out.append(Line("region", text, cells))
        else:
            match = KREDS.match(text)
            if not match or amt < 0:
                raise ValueError(f"{volume.day}: cannot place the row {text!r}")
            number = match.group(1).translate(str.maketrans("IlJ", "111"))
            out.append(Line("kreds", text, cells, amt=amt, number=number, name=match.group(2)))
    for index, line in enumerate(out):
        line.index = index
    return out, names


def roles(volume: Volume) -> list[str]:
    seen: list[str] = []
    for p in every(volume):
        for role in p.roles:
            if role not in SKIP and role not in seen:
                seen.append(role)
    return seen


def options(volume: Volume) -> list[str]:
    return [r for r in roles(volume) if r not in ACCOUNTING]


Term = tuple[Line, str, int]


def constraints(volume: Volume, lines: list[Line]) -> list[list[Term]]:
    """Each printed identity of the table as terms that sum to zero."""
    have = set(roles(volume))
    parties = options(volume)
    found: list[list[Term]] = []
    for line in lines:
        if line.kind == "region":
            continue
        found.append([(line, "valid", 1), *((line, r, -1) for r in parties)])
        if {"cast", "invalid"} <= have:
            found.append([(line, "cast", 1), (line, "valid", -1), (line, "invalid", -1)])
        elif {"cast", "blank", "other"} <= have:
            found.append([(line, "cast", 1), (line, "valid", -1), (line, "blank", -1), (line, "other", -1)])
        if {"invalid", "blank", "other"} <= have:
            found.append([(line, "invalid", 1), (line, "blank", -1), (line, "other", -1)])
        if "valid4" in have:
            found.append([(line, "valid4", 1), (line, "valid", -1)])
    groups: dict[int, list[Line]] = defaultdict(list)
    totals: dict[int, Line] = {}
    nation = [line for line in lines if line.kind == "nation"]
    for line in lines:
        if line.kind == "kreds":
            groups[line.amt].append(line)
        elif line.kind == "amt":
            totals[line.amt] = line
    for role in roles(volume):
        for amt, members in groups.items():
            if amt in totals:
                found.append([(totals[amt], role, 1), *((m, role, -1) for m in members)])
        if nation:
            found.append([(nation[0], role, 1), *((t, role, -1) for t in totals.values())])
    return found


def value(term: Term) -> int | None:
    line, role, _ = term
    if role not in line.cells:
        return 0
    cell = line.cells[role]
    return 0 if cell is None else cell.value


def residual(terms: list[Term]) -> int | None:
    total = 0
    for term in terms:
        v = value(term)
        if v is None:
            return None
        total += term[2] * v
    return total


def mend(line: Line, role: str, new: int, why: str) -> None:
    cell = line.cells.get(role)
    if cell is None:
        line.cells[role] = read.Cell(new, "", -1, (0.0, 0.0, 0.0, 0.0), mended=why)
    else:
        cell.mended = f"{why}: read {cell.text!r}"
        cell.value = new


def reconcile(volume: Volume, lines: list[Line]) -> list[str]:
    """Mend garbled figures where the printed sums prove one value; fail on any identity still broken."""
    for (index, role), fixed in volume.fixes.items():
        mend(lines[index], role, fixed, "read from the page image")
    rules = constraints(volume, lines)
    member: dict[tuple[int, str], list[int]] = defaultdict(list)
    for n, terms in enumerate(rules):
        for line, role, _ in terms:
            member[(line.index, role)].append(n)
    for _ in range(50):
        changed = False
        broken = {n for n, terms in enumerate(rules) if residual(terms) != 0}
        if not broken:
            break
        # A single unknown figure in an identity.
        for n in sorted(broken):
            terms = rules[n]
            unknown = [t for t in terms if value(t) is None]
            if len(unknown) == 1:
                line, role, sign = unknown[0]
                rest = sum(t[2] * (value(t) or 0) for t in terms if t is not unknown[0])
                solved = -rest * sign
                if solved >= 0:
                    mend(line, role, solved, "unreadable; solved from the printed sums")
                    changed = True
                    break
        if changed:
            continue
        # A figure every identity it is in proves wrong by the same amount.
        for (index, role), ns in member.items():
            if len(ns) < 2 or not all(n in broken for n in ns):
                continue
            deltas: set[int] = set()
            for n in ns:
                r = residual(rules[n])
                if r is None:
                    break
                sign = next(t[2] for t in rules[n] if t[0].index == index and t[1] == role)
                deltas.add(-r * sign)
            else:
                if len(deltas) != 1:
                    continue
                line = lines[index]
                old = value((line, role, 1)) or 0
                new = old + deltas.pop()
                if new >= 0:
                    mend(line, role, new, "proved by its row and column sums")
                    changed = True
                    break
        if changed:
            continue
        # A blank cell that holds the only figure that would mend an identity, and keeps every other one whole.
        for n in sorted(broken):
            terms = rules[n]
            r = residual(terms)
            blank = [t for t in terms if t[1] not in t[0].cells or t[0].cells[t[1]] is None]
            if r is None or len(blank) != 1:
                continue
            line, role, sign = blank[0]
            solved = -r * sign
            if solved <= 0:
                continue
            others = [m for m in member[(line.index, role)] if m != n]
            mend(line, role, solved, "not read from the page; solved from the printed sums")

            def settled(m: int) -> bool:
                return residual(rules[m]) == 0 or any(
                    t[1] not in t[0].cells or t[0].cells[t[1]] is None for t in rules[m]
                )

            if all(settled(m) for m in others):
                changed = True
                break
            del line.cells[role]
        if changed:
            continue
        # One identity broken, and one of its figures has a reading that mends it without breaking another.
        for n in sorted(broken):
            r = residual(rules[n])
            if r is None:
                continue
            candidates = []
            for line, role, sign in rules[n]:
                cell = line.cells.get(role)
                if cell is None:
                    continue
                for alternative in cell.alternatives:
                    if cell.value is not None and r + sign * (alternative - cell.value) == 0:
                        others = [m for m in member[(line.index, role)] if m != n]
                        if all(m not in broken for m in others) and not others:
                            candidates.append((line, role, alternative))
            if len(candidates) == 1:
                line, role, alternative = candidates[0]
                mend(line, role, alternative, "another reading of the printed figure that its column sum proves")
                changed = True
                break
        if not changed:
            break
    problems = []
    for terms in rules:
        r = residual(terms)
        if r != 0:
            shown = ", ".join(f"[{t[0].index}] {t[0].label[:24]} {t[1]}={value(t)}" for t in terms[:14])
            problems.append(f"off by {r}: {shown}")
    return problems


def mended(lines: list[Line]) -> list[str]:
    return [
        f"[{line.index}] {line.label} {role}: {cell.value} ({cell.mended})"
        for line in lines
        for role, cell in line.cells.items()
        if cell is not None and cell.mended
    ]


def volume_of(day: date) -> Volume:
    return next(v for v in volumes() if v.day == day)


@functools.cache
def grid(day: date) -> tuple[list[Line], list[str]]:
    volume = volume_of(day)
    pdf = raw.cached(SOURCE, volume.url).path
    lines, names = classify(volume, rows(volume, pdf))
    problems = reconcile(volume, lines)
    if problems:
        raise ValueError(f"{volume.day}: {len(problems)} identities do not hold:\n" + "\n".join(problems[:40]))
    return lines, names


@functools.cache
def sizes(day: date) -> dict[int, tuple[float, float]]:
    volume = volume_of(day)
    pdf = raw.cached(SOURCE, volume.url).path
    pages = read.pages(pdf, min(p.page for p in every(volume)), max(p.page for p in every(volume)))
    return {n: (page.width, page.height) for n, page in pages.items()}


# The amtskredse and storkredse in the order the volumes print them: 23 until the local government reform of 1970,
# 17 after it.
AMTER_1953 = [
    "Søndre Storkreds",
    "Østre Storkreds",
    "Vestre Storkreds",
    "Københavns Amtskreds",
    "Frederiksborg Amtskreds",
    "Holbæk Amtskreds",
    "Sorø Amtskreds",
    "Præstø Amtskreds",
    "Bornholms Amtskreds",
    "Maribo Amtskreds",
    "Odense Amtskreds",
    "Svendborg Amtskreds",
    "Hjørring Amtskreds",
    "Thisted Amtskreds",
    "Aalborg Amtskreds",
    "Viborg Amtskreds",
    "Randers Amtskreds",
    "Aarhus Amtskreds",
    "Skanderborg Amtskreds",
    "Vejle Amtskreds",
    "Ringkøbing Amtskreds",
    "Ribe Amtskreds",
    "Haderslev m.fl. Amters Amtskreds",
]
AMTER_1971 = [
    "Søndre Storkreds",
    "Østre Storkreds",
    "Vestre Storkreds",
    "Københavns Amtskreds",
    "Frederiksborg Amtskreds",
    "Roskilde Amtskreds",
    "Vestsjællands Amtskreds",
    "Storstrøms Amtskreds",
    "Bornholms Amtskreds",
    "Fyns Amtskreds",
    "Sønderjyllands Amtskreds",
    "Ribe Amtskreds",
    "Vejle Amtskreds",
    "Ringkøbing Amtskreds",
    "Århus Amtskreds",
    "Viborg Amtskreds",
    "Nordjyllands Amtskreds",
]


def fold(text: str) -> str:
    text = text.lower().translate(str.maketrans("æøåáéä", "eoaaea"))
    return re.sub(r"[^a-z0-9]", "", text)


def amt_names(day: date, printed: list[str]) -> list[str]:
    """The amtskredse and storkredse of a volume, checked against the headings it prints."""
    names = AMTER_1953 if day.year < 1970 else AMTER_1971
    if len(printed) != len(names):
        raise ValueError(f"{day}: found {len(printed)} amtskredse, expected {len(names)}")
    for name, label in zip(names, printed, strict=True):
        a, b = fold(name.split(" ")[0])[:5], fold(re.sub(r"^\W*[IVXLl1]{1,5}\s*[.,]?\s+", "", label))[:5]
        if difflib.SequenceMatcher(None, a, b).ratio() < 0.4:
            raise ValueError(f"{day}: the heading {label!r} does not read as {name}")
    return names


# The nomination districts of each amtskreds as the volumes name them, before and after the reform of 1970. A printed
# name is matched to the closest of its amtskreds's names, because the text layer misreads æ, ø, and å.
KREDSE_1953 = {
    "Søndre Storkreds": [
        "Sundby",
        "Sundby-nord",
        "Sundby-syd",
        "Christianshavn",
        "Rådhus",
        "Havne",
        "Rosenborg",
        "Blågård",
    ],
    "Østre Storkreds": ["Østerbro", "Ravnsborg", "Østbane", "Nørrebro", "Bispeeng", "Bispebjerg", "Brønshøj", "Ryvang"],
    "Vestre Storkreds": [
        "Vesterbro",
        "Enghave",
        "Valby",
        "Valby-øst",
        "Valby-vest",
        "Frederiksberg, Gl. Kongevej",
        "Frederiksberg, Slots",
        "Frederiksberg, Falkonér",
    ],
    "Københavns Amtskreds": [
        "Gentofte",
        "Lyngby",
        "Roskilde",
        "Køge",
        "Lejre",
        "Hellerup",
        "Gladsaxe",
        "Hvidovre",
        "Amager",
        "Rødovre",
    ],
    "Frederiksborg Amtskreds": ["Helsingør", "Fredensborg", "Hillerød", "Frederiksværk"],
    "Holbæk Amtskreds": ["Holbæk", "Nykøbing", "Vedby", "Kalundborg-Onsbjerg"],
    "Sorø Amtskreds": ["Ringsted", "Sorø", "Slagelse", "Skælskør"],
    "Præstø Amtskreds": ["Store Heddinge", "Præstø", "Næstved", "Stege-Vordingborg"],
    "Bornholms Amtskreds": ["Rønne", "Åkirkeby"],
    "Maribo Amtskreds": ["Nakskov", "Maribo", "Sakskøbing", "Nykøbing", "Stubbekøbing"],
    "Odense Amtskreds": ["Odense 1", "Odense 2", "Kerteminde", "Assens", "Middelfart", "Bogense", "Otterup"],
    "Svendborg Amtskreds": ["Nyborg", "Gudme", "Svendborg", "Fåborg-Ærøskøbing", "Højrup", "Rudkøbing"],
    "Hjørring Amtskreds": ["Frederikshavn", "Sæby", "Hjørring", "Vrå", "Halvrimmen"],
    "Thisted Amtskreds": ["Thisted", "Hurup", "Nykøbing"],
    "Aalborg Amtskreds": ["Ålborg (vest)", "Ålborg (øst)", "Bælum", "Års", "Nibe", "Nørresundby"],
    "Viborg Amtskreds": ["Skive", "Viborg", "Kjellerup", "Sønder Vinge", "Løvel"],
    "Randers Amtskreds": ["Mariager", "Randers", "Borup", "Hornslet", "Grenå", "Ebeltoft"],
    "Aarhus Amtskreds": ["Odder", "Århus (midt)", "Århus (nord)", "Århus (syd)", "Skjoldelev"],
    "Skanderborg Amtskreds": ["Horsens", "Skanderborg", "Østbirk", "Silkeborg"],
    "Vejle Amtskreds": ["Fredericia", "Kolding", "Vejle", "Give", "Bjerre", "Vonsild"],
    "Ringkøbing Amtskreds": ["Ringkøbing", "Lemvig", "Holstebro", "Vinderup", "Herning", "Skjern"],
    "Ribe Amtskreds": ["Varde", "Esbjerg", "Ribe", "Bække", "Guldager"],
    "Haderslev m.fl. Amters Amtskreds": [
        "Haderslev",
        "Åbenrå",
        "Sønderborg",
        "Augustenborg",
        "Tønder",
        "Løgumkloster",
        "Rødding",
    ],
}
KREDSE_1971 = {
    "Søndre Storkreds": ["Christianshavn", "Rådhus", "Sundby", "Blågård", "Amagerbro"],
    "Østre Storkreds": ["Ryvang", "Østbane", "Husum", "Østerbro", "Nørrebro", "Bispeeng", "Bispebjerg", "Brønshøj"],
    "Vestre Storkreds": [
        "Valby",
        "Vesterbro",
        "Enghave",
        "Frederiksberg, Gl. Kongevej",
        "Frederiksberg, Slots",
        "Frederiksberg, Falkonér",
    ],
    "Københavns Amtskreds": [
        "Gentofte",
        "Lyngby",
        "Ballerup",
        "Glostrup",
        "Hellerup",
        "Gladsaxe",
        "Hvidovre",
        "Amager",
        "Rødovre",
    ],
    "Frederiksborg Amtskreds": ["Helsingør", "Fredensborg", "Hillerød", "Frederiksværk"],
    "Roskilde Amtskreds": ["Roskilde", "Køge", "Lejre"],
    "Vestsjællands Amtskreds": ["Holbæk", "Nykøbing Sjælland", "Kalundborg", "Ringsted", "Sorø", "Slagelse"],
    "Storstrøms Amtskreds": ["Præstø", "Næstved", "Vordingborg", "Nakskov", "Maribo", "Nykøbing Falster"],
    "Bornholms Amtskreds": ["Rønne", "Åkirkeby"],
    "Fyns Amtskreds": [
        "Odense Øst",
        "Odense Vest",
        "Odense Syd",
        "Assens",
        "Kerteminde",
        "Middelfart",
        "Otterup",
        "Nyborg",
        "Svendborg",
        "Fåborg",
    ],
    "Sønderjyllands Amtskreds": [
        "Haderslev",
        "Åbenrå",
        "Sønderborg",
        "Augustenborg",
        "Tønder",
        "Løgumkloster",
        "Rødding",
    ],
    "Ribe Amtskreds": ["Varde", "Esbjerg", "Ribe", "Grindsted"],
    "Vejle Amtskreds": ["Fredericia", "Kolding", "Vejle", "Give", "Juelsminde", "Horsens"],
    "Ringkøbing Amtskreds": ["Ringkøbing", "Holstebro", "Herning", "Skjern"],
    "Århus Amtskreds": [
        "Århus Øst",
        "Århus Nord",
        "Århus Syd",
        "Århus Vest",
        "Mariager",
        "Randers",
        "Hammel",
        "Grenå",
        "Skanderborg",
        "Silkeborg",
    ],
    "Viborg Amtskreds": ["Thisted", "Morsø", "Skive", "Viborg", "Kjellerup"],
    "Nordjyllands Amtskreds": [
        "Frederikshavn",
        "Sæby",
        "Hjørring",
        "Fjerritslev",
        "Ålborg Nord",
        "Ålborg Vest",
        "Ålborg Øst",
        "Hobro",
        "Års",
    ],
}


def printed(name: str) -> str:
    """A printed district name without its number, its abbreviations, its code, or its dot leaders."""
    text = re.sub(r"\(\d+\)", "", name)
    text = re.sub(r"Kbh\.?\s*kr\.?|Frb\.?\s*kr\.?|\bkr\.|»|\bKbh\.|\bFrb\.", " ", text)
    text = re.sub(r"-?kr(edsen)?\.*\s*$", "", text.strip())
    text = re.sub(r"[\s.,:;'`_\-]+$", "", text)
    return " ".join(text.split())


def kreds_name(day: date, amt: str, number: str, name: str) -> str:
    names = (KREDSE_1953 if day.year < 1970 else KREDSE_1971)[amt]
    key = fold(re.sub(r"(?<=[-( ])[eo]st\b", "øst", printed(name)))

    def score(index: int) -> float:
        candidate = names[index]
        best = max(
            difflib.SequenceMatcher(None, key, fold(form)).ratio() for form in (candidate, candidate.split(", ")[-1])
        )
        return best

    index = max(range(len(names)), key=score)
    if score(index) < 0.5:
        raise ValueError(f"{day}: the district {number}. {name!r} in {amt} matches no known name")
    return names[index]


ROLE = {"registered": REGISTERED, "cast": CAST, "valid": VALID, "blank": BLANK}
METHOD = (
    "Read from Statistics Denmark's printed volume for this election, a scan with a text layer, in the table of votes "
    "by region, amtskreds, and nomination district (opstillingskreds). Every figure keeps its page and box. Each "
    "district's party votes add up to its printed valid votes, and each district column adds up to its amtskreds's "
    "printed figure, which in turn add up to the printed national figure; a figure the text layer misread was mended "
    "only where those sums prove a single value. Valid votes are the valid votes; where the volume prints them, "
    "registered voters, ballots cast (including admitted postal ballots), blank ballots, and other invalid ballots "
    "are stored too. The votes of the Faroe Islands and Greenland are not part of this count."
)


def party_key(name: str) -> str:
    return fold(name)


def parties() -> dict[str, str]:
    found: dict[str, str] = {}
    for volume in volumes():
        for name in volume.parties.values():
            found.setdefault(party_key(name), name)
    return found


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{v.day}" for v in volumes())]


def parse() -> None:
    registry().write()
    for v in volumes():
        build(v.day).write()


def emit() -> None:
    package.run("load", packages())


SYSTEMS = (
    (
        SYSTEM_1953_APRIL,
        {"formula": "dhondt"},
        (
            "Party-list proportional representation for the Folketing of the Rigsdag under the electoral law of 1948: "
            "constituency seats in each amtskreds and storkreds by the d'Hondt method, and supplementary seats "
            "allotted nationally to parties that won a constituency seat or reached the average number of votes per seat "
            "in one of the three regions."
        ),
    ),
    (
        SYSTEM_1953,
        {"formula": "modified_sainte_lague", "firstDivisor": 1.4},
        (
            "Party-list proportional representation under the electoral law of 1953: 135 constituency seats in the "
            "amtskredse and storkredse by the modified Sainte-Laguë method (divisors 1.4, 3, 5, ...) and 40 supplementary "
            "seats allotted nationally. A party takes part in the supplementary seats if it wins a constituency seat, "
            "reaches in two of the three regions the average number of votes per constituency seat, or wins 60,000 votes "
            "in the whole country; only the method is recorded here."
        ),
    ),
    (
        SYSTEM_1964,
        {
            "formula": "modified_sainte_lague",
            "firstDivisor": 1.4,
            "threshold": {"percent": 2, "level": "party", "scope": "national"},
        },
        (
            "Party-list proportional representation: 135 constituency seats in the amtskredse and storkredse by the "
            "modified Sainte-Laguë method (divisors 1.4, 3, 5, ...) and 40 supplementary seats allotted nationally. A "
            "party takes part in the supplementary seats if it wins a constituency seat, reaches in two of the three "
            "regions the average number of votes per constituency seat, or wins 2 percent of the valid votes in the whole "
            "country; only the 2 percent test is recorded here."
        ),
    ),
)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Danish parties and the Folketing's electoral systems of 1953 to 2005, from Statistics Denmark's historical "
        "election volumes",
    )
    p.external.add(DENMARK)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Danmarks Statistik",
        title="Folketingsvalg (historiske)",
        url="https://www.dst.dk/da/Statistik/udgivelser",
        license="Statistics Denmark data may be reused with attribution to Statistics Denmark as the source",
        attribution="Danmarks Statistik",
        notes="One volume per election, scanned with a text layer: Statistiske Meddelelser and Statistiske "
        "Efterretninger, VisPub cid 20199 to 20213 and 20327 to 20331.",
    )
    for id, rules, text in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=DENMARK, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for slug, name in sorted(parties().items()):
        id = key("party", slug)
        p.add("party", id=id, kind="party", polity_id=DENMARK)
        p.name(id, name, "da", "official")
    return p


def box(volume: Volume, cell: read.Cell) -> tuple[float, float, float, float]:
    width, height = sizes(volume.day)[cell.page]
    x0, y0, x1, y1 = cell.box
    return (round(x0 / width, 4), round(y0 / height, 4), round(x1 / width, 4), round(y1 / height, 4))


def stored(lines: list[Line], line: Line, role: str) -> read.Cell | None:
    """The cell stored for a role: other invalid ballots stand as the invalid count when the volume splits them from
    blank ones, and the printed invalid total stands otherwise."""
    if role == "invalid":
        return line.cells.get("other") if "other" in line.cells else line.cells.get("invalid")
    return line.cells.get(role)


def build(day: date) -> Package:
    volume = volume_of(day)
    lines, headings = grid(day)
    amts = amt_names(day, headings)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Folketing election of {day} by nomination district, from Statistics Denmark's printed volume",
        depends=[REGISTRY, DST_REGISTRY],
    )
    p.external.add(DENMARK)
    pdf = raw.cached(SOURCE, volume.url)
    artifact = p.artifact(pdf, SOURCE_ID)
    election = key("election", volume.slug)
    path = f"dk/{day}-general"
    p.add(
        "election",
        id=election,
        polity_id=DENMARK,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    english = f"{day.strftime('%B')} {day.year}" if day.year == 1953 else str(day.year)
    p.name(election, f"{english} Danish general election", "en", "official")
    p.name(election, f"Folketingsvalget {day.day}. {DANISH_MONTHS[day.month - 1]} {day.year}", "da", "native", False)
    contest = key("contest", volume.slug)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=FOLKETING_BODY,
        magnitude=volume.magnitude,
        round_number=1,
        electoral_system_id=volume.system,
        default_result_kind="counted",
    )
    p.name(contest, f"{english} Folketing election", "en", "official")
    p.name(contest, f"Folketingsvalget {day.year}", "da", "native", False)
    p.add("slug", path=f"{path}/folketing", entity_id=contest, is_current=True)
    result_set = key("result_set", volume.slug)
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
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    fixed = [
        (line, role, cell)
        for line in lines
        for role, cell in line.cells.items()
        if cell is not None and cell.mended and role not in SKIP
    ]
    if fixed:
        text = (
            f"{len(fixed)} figures of the table were misread or lost by the volume's text layer and are stored as "
            "the printed row and column sums prove them: "
            + "; ".join(f"{line.label.strip(' .')} ({role}) {cell.value}" for line, role, cell in fixed)
            + "."
        )
        p.add(
            "note", id=key("note", result_set, "mended"), entity_id=result_set, kind="caveat", body_md=text, lang="en"
        )
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=SOURCE_ID,
        locator=json.dumps(
            {"url": volume.url, "title": volume.title, "pages": sorted({q.page for q in every(volume)})}
        ),
    )
    options: dict[str, str] = {}
    for position, role in enumerate(options_of(volume), 1):
        option = key("option", volume.slug, role)
        options[role] = option
        if role == OTHERS:
            p.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Uden for partierne")
        else:
            name = volume.parties[role]
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=key("party", party_key(name)),
                ballot_label=f"{role}. {name}" if day.year >= 1966 else name,
            )
    page_ids: dict[int, str] = {}
    for number in sorted({q.page for q in every(volume)}):
        width, height = sizes(day)[number]
        page_ids[number] = key("page", volume.slug, number)
        p.add(
            "source_page",
            id=page_ids[number],
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{SOURCE}/{pdf.sha256}-{number}.png",
            width=round(width * 100 / 72),
            height=round(height * 100 / 72),
        )
    values: list[tuple[str, str | None, int, int]] = []
    locators: list[tuple[str, str | None, int, str, float, float, float, float]] = []
    positions: dict[int, int] = defaultdict(int)
    for line in lines:
        if line.kind != "kreds":
            continue
        positions[line.amt] += 1
        amt = amts[line.amt]
        name = kreds_name(day, amt, line.number, line.name)
        code = f"{volume.slug}:{line.amt + 1:02d}{positions[line.amt]:02d}"
        unit = key("unit", volume.slug, line.amt + 1, positions[line.amt])
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=DENMARK,
            scheme_code=SCHEME,
            code=code,
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, f"{name} ({amt})", "da", "official")
        cells: list[tuple[str | None, int, read.Cell | None]] = [
            (None, measure, stored(lines, line, role)) for role, measure in accounting(volume)
        ]
        cells += [(options[role], VOTES, line.cells.get(role)) for role in options]
        for option, measure, cell in cells:
            if cell is None or cell.value is None:
                continue
            values.append((unit, option, measure, cell.value))
            if cell.page > 0:
                locators.append((unit, option, measure, page_ids[cell.page], *box(volume, cell)))
    p.frame(
        "result_value",
        pl.DataFrame(values, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators,
            schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
            orient="row",
        ).with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    nation = next(line for line in lines if line.kind == "nation")
    for role, measure in accounting(volume):
        cell = stored(lines, nation, role)
        if cell is not None and cell.value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=cell.value)
    for role, option in options.items():
        cell = nation.cells.get(role)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=cell.value if cell else 0)
    for role, seats in volume.seats.items():
        p.add("outcome", result_set_id=result_set, option_id=options[role], kind="won", seats=seats)
    if sum(volume.seats.values()) != volume.magnitude:
        raise ValueError(f"{day}: the declared seats add up to {sum(volume.seats.values())}, not {volume.magnitude}")
    return p


ACCOUNTING_MEASURES = (
    ("registered", REGISTERED),
    ("cast", CAST),
    ("valid", VALID),
    ("invalid", INVALID),
    ("blank", BLANK),
)
DANISH_MONTHS = (
    "januar",
    "februar",
    "marts",
    "april",
    "maj",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "december",
)


def accounting(volume: Volume) -> list[tuple[str, int]]:
    have = set(roles(volume))
    if "other" in have:
        have.add("invalid")
    return [(role, measure) for role, measure in ACCOUNTING_MEASURES if role in have]


def options_of(volume: Volume) -> list[str]:
    return [r for r in roles(volume) if r not in ACCOUNTING]
