"""European Parliament elections in Denmark, 1979 to 2004, by nomination district.

Party votes per nomination district (opstillingskreds) come from the official count of each election: Statistics
Denmark's Statistiske Efterretninger for 1979, 1984, and 1989 (scanned issues whose text layer is character
recognition, so each figure keeps its page and box) and the Ministry of the Interior's reports for 1994, 1999, and 2004.
Greenland's seat in 1984 comes from the 1984 issue.
"""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.fi import digitized
from ea.sources.fi.digitized import Box

SOURCE = "dk.ep_hist"
CDN = "https://cdn2.gopublic.dk/indenrigssundhedsminvalg/media"
FILES = {
    1979: "https://www.dst.dk/Site/Dst/Udgivelser/GetSEPdf.aspx?id=12058",
    1984: "https://dst.dk/Site/Dst/SingleFiles/GetArchiveFile.aspx?fi=16056110518&fo=0&ext=befolkning",
    1989: "https://dst.dk/Site/Dst/SingleFiles/GetArchiveFile.aspx?fi=93407110519&fo=0&ext=befolkning",
    1994: f"{CDN}/18833/europa-parlamentetsvalget-den-9-juni-1994.pdf",
    1999: f"{CDN}/6908/europa-parlamentsvalget-den-10-juni-1999.pdf",
    2004: f"{CDN}/7885/europa-parlamentsvalget-den-13-juni-2004.pdf",
}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


# Measures: the codes the archive uses for what a number counts. Invalid ballots exclude the blank ones.
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6


@dataclass(frozen=True)
class Scan:
    """Where a scanned issue prints its columns, as right edges in points: the counts page, then the party-votes page."""

    left: dict[str, float]
    label: float  # words ending left of this are the row's label
    halves: bool  # whether the text layer sets the two halves of a row at different heights
    right: list[float]
    parties: str  # the letter of each column of the party-votes page, "T" for a printed total


@dataclass(frozen=True)
class Typeset:
    """Where a typeset report prints its tables: the counts (pages and columns) and the party votes (pages)."""

    counts: tuple[int, int]
    columns: list[str]
    votes: tuple[int, int]


# Right edges (points) of each printed column on the scanned issues: the counts page, then the party-votes page. A
# figure's last word ends within a point or two of its column's edge, and the groups of thousands before it lie up to 30
# points to the left.
SCANS: dict[int, Scan] = {
    1979: Scan(
        left={
            "registered": 199,
            "cast": 243,
            "brev_out": 274,
            "brev_in": 315,
            "invalid": 348,
            "blank": 381,
            "other": 411,
            "pct": 441,
            "brev_pct": 471,
            "invalid_pct": 501,
            "valid": 546,
        },
        label=175,
        halves=False,
        right=[74, 119, 163, 208, 253, 297, 342, 387, 431, 476, 521],
        parties="ABCEFMNQVYZ",
    ),
    1984: Scan(
        left={
            "registered": 200,
            "cast": 242,
            "brev_out": 275,
            "brev_in": 314,
            "invalid": 346,
            "blank": 382,
            "other": 414,
            "pct": 447,
            "brev_pct": 479,
            "invalid_pct": 512,
            "valid": 555,
        },
        label=168,
        halves=True,
        right=[74, 123, 173, 222, 271, 320, 368, 416, 464, 513],
        parties="ABCFMNQVYZ",
    ),
    1989: Scan(
        left={
            "polling": 183,
            "registered": 224,
            "cast": 266,
            "brev_out": 299,
            "brev_in": 338,
            "invalid": 371,
            "blank": 408,
            "other": 441,
            "pct": 474,
            "brev_pct": 507,
            "invalid_pct": 541,
        },
        label=160,
        halves=False,
        right=[76, 124, 173, 222, 270, 319, 368, 418, 467, 517],
        parties="TABCDFNQVZ",
    ),
}
PAIRS = [(2, 3), (4, 5), (6, 7)]
ROWS = 124  # the country, three areas, three metropolitan and fourteen county groups, and 103 nomination districts
REACH = 30.0
TOLERANCE = 4.0


@dataclass
class Cell:
    value: int | None
    box: Box


@dataclass
class Row:
    page: int
    label: str
    cells: dict[int, Cell] = field(default_factory=dict)

    def get(self, index: int) -> int | None:
        cell = self.cells.get(index)
        return cell.value if cell else None


def cells_of(line: list[Box], edges: list[float], label_limit: float) -> tuple[str, dict[int, Cell]]:
    """Split a printed row into its label and its figures, assigning each word to the column whose edge it ends near."""
    label: list[str] = []
    found: dict[int, list[Box]] = {}
    for word in line:
        near = [i for i, edge in enumerate(edges) if edge - REACH < word.x1 <= edge + 4]
        if word.x1 <= label_limit or not near:
            label.append(word.text)
            continue
        found.setdefault(min(near, key=lambda i: abs(edges[i] - word.x1)), []).append(word)
    out: dict[int, Cell] = {}
    for index, words in found.items():
        text = "".join(w.text for w in words)
        box = Box(
            text, min(w.x0 for w in words), min(w.y0 for w in words), max(w.x1 for w in words), max(w.y1 for w in words)
        )
        out[index] = Cell(digitized.number(text), box)
    return " ".join(label), out


def slope(words: list[Box]) -> float:
    """The tilt of a scanned page, in points of height per point of width: the one that makes the rows of figures line up."""
    figures = [w for w in words if any(c.isdigit() for c in w.text)]
    best = (-1.0, 0.0)
    for step in range(-60, 61):
        tilt = step / 4000
        bins: dict[int, int] = {}
        for w in figures:
            key = round((w.y0 + w.y1) / 2 - tilt * w.x1)
            bins[key] = bins.get(key, 0) + 1
        score = float(sum(n * n for n in bins.values())) - abs(tilt) * 1e-6
        if score > best[0]:
            best = (score, tilt)
    return best[1]


def tilted_rows(words: list[Box], tolerance: float) -> list[list[Box]]:
    """The words of a scanned page grouped into printed rows, after taking out the page's tilt."""
    tilt = slope(words)
    ordered = sorted(words, key=lambda w: (w.y0 + w.y1) / 2 - tilt * w.x1)
    lines: list[list[Box]] = []
    last = None
    for word in ordered:
        height = (word.y0 + word.y1) / 2 - tilt * word.x1
        if last is None or height - last > tolerance:
            lines.append([])
        lines[-1].append(word)
        last = height
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def shifted(words: list[Box], edges: list[float]) -> float:
    """How far a scanned page sits from the nominal column edges: the shift that lines up the most figures' right edges."""
    ends = [w.x1 for w in words if w.text.isdigit() and len(w.text) >= 2]
    best = max(
        (sum(any(abs(x - (edge + shift)) <= 1.5 for edge in edges) for x in ends), -abs(shift), shift)
        for shift in [i / 2 for i in range(-24, 25)]
    )
    return best[2]


def halves(words: list[Box], edges: list[float], limit: float, split: float, minimum: int) -> list[Row]:
    """The rows of one left-hand page. The text layer sets the two halves of a row at slightly different heights, so each
    half is grouped into rows by itself and the halves are paired in printed order."""
    near = [w for w in words if w.x1 <= split]
    far = [w for w in words if w.x1 > split]
    first = [(label, cells) for line in tilted_rows(near, TOLERANCE) for label, cells in [cells_of(line, edges, limit)]]
    second = [cells_of(line, edges, limit)[1] for line in tilted_rows(far, TOLERANCE)]
    first = [(label, cells) for label, cells in first if sum(c.value is not None for c in cells.values()) >= 2]
    second = [cells for cells in second if sum(c.value is not None for c in cells.values()) >= minimum]
    if len(first) != len(second):
        raise ValueError(f"the halves of a page hold {len(first)} and {len(second)} rows")
    return [Row(0, label, {**a, **b}) for (label, a), b in zip(first, second, strict=True)]


def scan_rows(year: int) -> tuple[list[Row], list[Row]]:
    """The left (counts) and right (party votes) rows of a scanned issue, each in printed order."""
    spec = SCANS[year]
    path = raw.cached(SOURCE, FILES[year]).path
    pages = digitized.pages(path, 2, 7)
    left_edges = list(spec.left.values())
    right_edges = list(spec.right)
    lefts: list[Row] = []
    rights: list[Row] = []
    split = (spec.left["brev_out"] + spec.left["brev_in"]) / 2
    for left, right in PAIRS:
        move = shifted(pages[left].words, left_edges)
        edges = [e + move for e in left_edges]
        # the national row's registered voters can run past the reach, so rows are told apart by their later columns
        if spec.halves:
            rows = halves(pages[left].words, edges, spec.label + move, split + move, 4)
        else:
            rows = []
            for line in tilted_rows(pages[left].words, TOLERANCE):
                label, cells = cells_of(line, edges, spec.label + move)
                if sum(c.value is not None for c in cells.values()) >= 6:
                    rows.append(Row(left, label, cells))
        for row in rows:
            row.page = left
        lefts += rows
        move = shifted(pages[right].words, right_edges)
        edges = [e + move for e in right_edges]
        for line in tilted_rows(pages[right].words, TOLERANCE):
            tail = [w for w in line if w.x0 > edges[-1] + 4]
            body = [w for w in line if w.x0 <= edges[-1] + 4]
            _, cells = cells_of(body, edges, -1)
            if sum(c.value is not None for c in cells.values()) >= len(edges) - 3:
                rights.append(Row(right, " ".join(w.text for w in tail), cells))
    return lefts[:ROWS], rights[:ROWS]


AREAS = {"København-Frederiksberg": "a", "Øerne": "b", "Jylland": "c"}
STORKREDSE = {"Søndre Storkreds": "S1", "Østre Storkreds": "S2", "Vestre Storkreds": "S3"}
AMTSKREDSE = [
    "Københavns", "Frederiksborg", "Roskilde", "Vestsjællands", "Storstrøms", "Bornholms", "Fyns",
    "Sønderjyllands", "Ribe", "Vejle", "Ringkøbing", "Århus", "Viborg", "Nordjyllands",
]  # fmt: skip
NUMBER = r"(?:-|\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+,\d+)"
TEXT_ROW = re.compile(rf"^(?P<label>\S.*?)\s+(?P<numbers>{NUMBER}(?:\s+{NUMBER})+)\s*$")
DISTRICT = re.compile(r"^(\d{1,2})\. (?:(Kbh|Frb)\. )?(.+?)(?: \(\d+\))?$")


@dataclass
class Slot:
    """One row of the table every election prints in the same order: the country, a group, or a nomination district."""

    kind: str  # country, area, storkreds, amt, or district
    code: str  # the group's code, or the district's own: its group, its numbering, and its number
    group: str | None
    name: str
    number: int = 0


def text_of(year: int, first: int, last: int) -> list[str]:
    """The lines of a typeset report's pages, with the columns kept apart."""
    path = raw.cached(SOURCE, FILES[year]).path
    out = subprocess.run(
        ["pdftotext", "-layout", "-f", str(first), "-l", str(last), str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.splitlines()


def printed(lines: list[str], width: int) -> list[tuple[str, list[str]]]:
    """The rows of a printed table: each label with its `width` figures, where a label whose figures wrap to the next line
    is joined to them."""
    rows: list[tuple[str, list[str]]] = []
    pending: str | None = None
    for line in lines:
        text = line.strip()
        if not text:
            continue
        found = TEXT_ROW.match(text)
        alone = re.fullmatch(rf"{NUMBER}(?:\s+{NUMBER})+", text)
        if alone and pending is not None:
            label, figures = pending, text.split()
        elif found:
            label, figures = found["label"], found["numbers"].split()
        else:
            pending = text if not re.search(r"\d{2}", text) and len(text) < 60 else None
            continue
        pending = None
        if len(figures) == width:
            rows.append((label, figures))
    return rows


def whole(figure: str) -> int:
    return 0 if figure == "-" else int(figure.replace(".", ""))


def layout() -> list[Slot]:
    """The rows every report prints, in order, taken from the 2004 report's table of party votes."""
    slots = [Slot("country", "DK", None, "Hele landet")]
    group: str | None = None
    amts = iter(AMTSKREDSE)
    for label, _ in printed(text_of(2004, 79, 81), 10)[1:]:
        found = DISTRICT.match(label)
        if found:
            series = (found[2] or "kr").lower()
            name = f"{found[2]}. {found[3]}" if found[2] else found[3]
            slots.append(Slot("district", f"{group}:{series}{found[1]}", group, name, int(found[1])))
        elif label in AREAS:
            slots.append(Slot("area", AREAS[label], None, label))
        elif label in STORKREDSE:
            group = STORKREDSE[label]
            slots.append(Slot("storkreds", group, None, label))
        else:
            assert label == f"{next(amts)} Amtskreds"
            group = f"A{AMTSKREDSE.index(label.removesuffix(' Amtskreds')) + 1:02d}"
            slots.append(Slot("amt", group, None, label))
    assert len(slots) == ROWS and sum(s.kind == "district" for s in slots) == 103
    return slots


PARTIES = {
    1979: "ABCEFMNQVYZ",
    1984: "ABCFMNQVYZ",
    1989: "ABCDFNQVZ",
    1994: "ABCDFJNQVZ",
    1999: "ABCDFJNOQVZ",
    2004: "ABCFJKNOV",
}
# Where each typeset report prints its tables: the counts and the party votes, with the figures of each row in order.
TYPESET: dict[int, Typeset] = {
    1994: Typeset(counts=(63, 65), columns=["registered", "cast", "brev", "invalid", "blank", "other", "pct", "brev_pct", "invalid_pct", "valid"], votes=(66, 68)),
    1999: Typeset(counts=(46, 54), columns=["registered", "cast", "brev", "invalid", "blank", "other", "pct", "brev_pct", "invalid_pct", "valid"], votes=(55, 64)),
    2004: Typeset(counts=(76, 78), columns=["polling", "registered", "cast", "brev_out", "brev", "invalid", "blank", "other", "pct", "brev_pct", "invalid_pct", "valid"], votes=(79, 81)),
}  # fmt: skip


@dataclass
class Fig:
    """A printed figure, and for a scanned one the page and box it was read from."""

    value: int | None
    page: int | None = None
    box: Box | None = None


@dataclass
class Printed:
    """One election's printed table: the figures of each row, in the order of `layout()`."""

    year: int
    counts: list[dict[str, Fig]]
    votes: list[dict[str, Fig]]
    notes: list[str] = field(default_factory=list)


def scanned(year: int, slots: list[Slot]) -> Printed:
    spec = SCANS[year]
    lefts, rights = scan_rows(year)
    names = list(spec.left)
    counts: list[dict[str, Fig]] = []
    votes: list[dict[str, Fig]] = []
    for left, right in zip(lefts, rights, strict=True):
        counts.append(
            {
                name: Fig(c.value, left.page, c.box)
                for i, name in enumerate(names)
                if (c := left.cells.get(i)) and not name.endswith("pct")
            }
        )
        votes.append(
            {
                letter: Fig(c.value, right.page, c.box)
                for i, letter in enumerate(spec.parties)
                if (c := right.cells.get(i))
            }
        )
    out = Printed(year, counts, votes)
    for k, (left, right) in enumerate(zip(lefts, rights, strict=True)):
        if slots[k].kind == "district":
            want = re.match(r"\s*(\d+)", re.sub(r"^\D*", "", left.label.replace("l ", "1 ")))
            if want and int(want[1]) != slots[k].number:
                out.notes.append(f"row {k} {slots[k].code}: left label {left.label!r}")
    return out


def typeset(year: int, slots: list[Slot]) -> Printed:
    spec = TYPESET[year]
    (first, last), columns = spec.counts, spec.columns
    counts_rows = printed(text_of(year, first, last), len(columns))
    letters = PARTIES[year]
    votes_rows = printed(text_of(year, *spec.votes), len(letters) + 1)
    counts: list[dict[str, Fig]] = [{} for _ in slots]
    votes: list[dict[str, Fig]] = [{} for _ in slots]
    for rows, store, names in ((counts_rows, counts, columns), (votes_rows, votes, [*letters, "T"])):
        districts = [(label, figures) for label, figures in rows if DISTRICT.match(label)]
        order = [i for i, slot in enumerate(slots) if slot.kind == "district"]
        assert len(districts) == len(order), (year, len(districts))
        for slot_index, (label, figures) in zip(order, districts, strict=True):
            for name, figure in zip(names, figures, strict=True):
                if "," not in figure:
                    store[slot_index][name] = Fig(whole(figure))
        national = next(figures for label, figures in rows if label.upper() == "HELE LANDET")
        for name, figure in zip(names, national, strict=True):
            if "," not in figure:
                store[0][name] = Fig(whole(figure))
        for k, slot in enumerate(slots):
            if slot.kind in ("storkreds", "amt"):
                found = next((f for label, f in rows if label == slot.name), None)
                if found:
                    for name, figure in zip(names, found, strict=True):
                        if "," not in figure:
                            store[k][name] = Fig(whole(figure))
    return Printed(year, counts, votes)


def check(table: Printed, slots: list[Slot]) -> list[str]:
    """Where printed figures do not add up, which is where a scan was misread or a row was misplaced."""
    found: list[str] = []
    letters = PARTIES[table.year]
    for k, slot in enumerate(slots):
        unread = [n for store in (table.counts[k], table.votes[k]) for n, f in store.items() if f.value is None]
        unread = [n for n in unread if not n.endswith("pct") and n not in ("polling", "brev_out", "brev_in", "brev")]
        if unread:
            found.append(f"{table.year} {slot.code} {slot.name}: unreadable {unread}")
        c = {n: f.value for n, f in table.counts[k].items() if f.value is not None}
        v = {n: f.value for n, f in table.votes[k].items() if f.value is not None}
        where = f"{table.year} {slot.code} {slot.name}"
        missing = [x for x in letters if x not in v]
        if missing and not any(table.votes[k].values()):
            continue
        if missing:
            found.append(f"{where}: party figures missing {missing}")
            continue
        total = sum(v[x] for x in letters)
        valid = c.get("valid", v.get("T"))
        if valid is None:
            found.append(f"{where}: no valid votes")
        elif total != valid:
            found.append(f"{where}: parties add to {total}, valid votes {valid}")
        if "T" in v and "valid" in c and v["T"] != c["valid"]:
            found.append(f"{where}: total {v['T']} != valid {c['valid']}")
        if valid is not None and "cast" in c and "invalid" in c and c["cast"] != valid + c["invalid"]:
            found.append(f"{where}: cast {c['cast']} != valid {valid} + invalid {c['invalid']}")
        if "blank" in c and "other" in c and "invalid" in c and c["invalid"] != c["blank"] + c["other"]:
            found.append(f"{where}: invalid {c['invalid']} != blank {c['blank']} + other {c['other']}")
    return found


# Figures the text layer of a scan misread, each settled by looking at the page and by the row's own sums. The key is
# the election year, the row's code, and the column.
CORRECTIONS: dict[tuple[int, str, str], int] = {
    (1979, "A13:kr1", "cast"): 15506,
    (1979, "A13:kr3", "B"): 1661,
    (1984, "A01:kr1", "Y"): 216,
    (1984, "A01:kr8", "V"): 505,
    (1984, "A04:kr3", "B"): 566,
    (1989, "A02:kr2", "B"): 1163,
    (1989, "A12:kr2", "other"): 41,
    (1979, "A04:kr4", "registered"): 37928,
    (1979, "A07:kr2", "registered"): 37847,
    (1984, "A14", "B"): 4954,
    # The country row's figures run past the reach of their column, so the text layer drops their leading digit.
    (1984, "DK", "registered"): 3843947,
    (1984, "DK", "cast"): 2013359,
    (1984, "DK", "valid"): 1990280,
    (1989, "DK", "registered"): 3923549,
    (1989, "DK", "cast"): 1811558,
    (1989, "DK", "T"): 1789395,
}


def corrected(table: Printed, slots: list[Slot]) -> None:
    index = {slot.code: k for k, slot in enumerate(slots)}
    for (year, code, column), value in CORRECTIONS.items():
        if year != table.year:
            continue
        store = table.votes if column in PARTIES[year] or column == "T" else table.counts
        figure = store[index[code]][column]
        assert figure.box is not None
        figure.value = value


def sums(table: Printed, slots: list[Slot]) -> list[str]:
    """Compare each printed group and national row with the sum of the districts under it."""
    found: list[str] = []
    members: dict[int, list[int]] = {}
    group: int | None = None
    for k, slot in enumerate(slots):
        if slot.kind == "district":
            members.setdefault(group or 0, []).append(k)
        elif slot.kind in ("storkreds", "amt"):
            group = k
    for k, rows in members.items():
        slot = slots[k]
        for store, label in ((table.counts, "counts"), (table.votes, "votes")):
            for column, figure in store[k].items():
                if (
                    column.endswith("pct")
                    or column in ("polling", "brev_out", "brev", "brev_in", "other")
                    or figure.value is None
                ):
                    continue
                want = sum(store[m][column].value or 0 for m in rows if column in store[m])
                if want != figure.value:
                    found.append(
                        f"{table.year} {slot.code} {label} {column}: printed {figure.value}, districts add to {want}"
                    )
    total = {}
    for store, label in ((table.counts, "counts"), (table.votes, "votes")):
        for column, figure in store[0].items():
            if (
                column.endswith("pct")
                or column in ("polling", "brev_out", "brev", "brev_in", "other")
                or figure.value is None
            ):
                continue
            want = sum(
                store[m][column].value or 0
                for m, slot in enumerate(slots)
                if slot.kind == "district" and column in store[m]
            )
            total[(label, column)] = want
            if want != figure.value:
                found.append(f"{table.year} country {label} {column}: printed {figure.value}, districts add to {want}")
    return found


# Greenland's own seat in 1984 (the 1979 issue holds no Greenland results): the page of the 1984 issue that prints it, and
# the right edges of its columns.
GREENLAND_PAGE = 24
GREENLAND_COLUMNS: dict[str, float] = {
    "registered": 219, "pct": 261, "cast": 303, "invalid": 345, "blank": 386, "other": 428, "valid": 469,
    "atassut": 510, "siumut": 552,
}  # fmt: skip
GREENLAND_MUNICIPALITIES = [
    "Nanortalik", "Julianehåb", "Narssaq", "Ivigtut", "Frederikshåb", "Godthåb", "Sukkertoppen", "Holsteinsborg",
    "Kangåtsiaq", "Egedesminde", "Christianshåb", "Jakobshavn", "Godhavn", "Umanak", "Upernavik", "Thule",
    "Angmagssalik", "Scoresbysund",
]  # fmt: skip


def greenland() -> list[tuple[str, dict[str, Fig]]]:
    """The 1984 count in Greenland by municipality, the country's own row first."""
    path = raw.cached(SOURCE, FILES[1984]).path
    page = digitized.pages(path, GREENLAND_PAGE, GREENLAND_PAGE)[GREENLAND_PAGE]
    edges = list(GREENLAND_COLUMNS.values())
    move = shifted(page.words, edges)
    edges = [e + move for e in edges]
    rows: list[tuple[str, dict[str, Fig]]] = []
    for line in tilted_rows(page.words, TOLERANCE):
        label, cells = cells_of(line, edges, 190 + move)
        if sum(c.value is not None for c in cells.values()) < 6:
            continue
        figures = {
            name: Fig(c.value, GREENLAND_PAGE, c.box)
            for i, name in enumerate(GREENLAND_COLUMNS)
            if (c := cells.get(i)) and name != "pct"
        }
        rows.append((label, figures))
    names = [label for label, _ in rows]
    assert len(rows) == 19, names
    return rows


# ---------------------------------------------------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------------------------------------------------

DENMARK = str(uuid5(package.NAMESPACE, "polity\x1fDK"))
GREENLAND = str(uuid5(package.NAMESPACE, "polity\x1fGL"))
DIGITIZED = 8
DAYS = {
    1979: date(1979, 6, 7),
    1984: date(1984, 6, 14),
    1989: date(1989, 6, 15),
    1994: date(1994, 6, 9),
    1999: date(1999, 6, 10),
    2004: date(2004, 6, 13),
}
SEATS = {  # seats won in Denmark's single constituency, by party letter
    1979: {"A": 3, "C": 2, "F": 1, "M": 1, "N": 4, "V": 3, "Z": 1},
    1984: {"A": 3, "C": 4, "F": 1, "M": 1, "N": 4, "V": 2},
    1989: {"A": 4, "C": 2, "D": 2, "F": 1, "N": 4, "V": 3},
    1994: {"A": 3, "B": 1, "C": 3, "F": 1, "J": 2, "N": 2, "V": 4},
    1999: {"A": 3, "B": 1, "C": 1, "F": 1, "J": 3, "N": 1, "O": 1, "V": 5},
    2004: {"A": 5, "B": 1, "C": 1, "F": 1, "J": 1, "N": 1, "O": 1, "V": 3},
}
MAGNITUDE = {1979: 15, 1984: 15, 1989: 16, 1994: 16, 1999: 16, 2004: 14}
PARTY_OF = {"M": "cd", "D": "cd", "Q": "kd", "K": "kd"}
PARTY_NAMES = {
    "a": "Socialdemokratiet",
    "b": "Radikale Venstre",
    "c": "Konservative Folkeparti",
    "cd": "Centrum-Demokraterne",
    "e": "Retsforbundet",
    "f": "Socialistisk Folkeparti",
    "j": "JuniBevægelsen",
    "kd": "Kristendemokraterne",
    "n": "Folkebevægelsen mod EU",
    "o": "Dansk Folkeparti",
    "v": "Venstre, Danmarks Liberale Parti",
    "y": "Venstresocialisterne",
    "z": "Fremskridtspartiet",
    "atassut": "Atassut",
    "siumut": "Siumut",
}
LABELS = {  # the name a list carried on that year's ballot where it differs from the party's latest name
    ("kd", 1979): "Kristeligt Folkeparti", ("kd", 1984): "Kristeligt Folkeparti", ("kd", 1989): "Kristeligt Folkeparti",
    ("kd", 1994): "Kristeligt Folkeparti", ("kd", 1999): "Kristeligt Folkeparti",
    ("n", 1979): "Folkebevægelsen mod EF", ("n", 1984): "Folkebevægelsen mod EF", ("n", 1989): "Folkebevægelsen mod EF",
    ("n", 1994): "Folkebevægelsen mod EF-Unionen",
    ("v", 1979): "Venstre", ("v", 1984): "Venstre", ("v", 1989): "Venstre",
    ("j", 1999): "JuniBevægelsen - Mod Union", ("j", 2004): "JuniBevægelsen mod Unionen",
}  # fmt: skip
PUBLICATIONS = {
    1979: (
        "Danmarks Statistik",
        "Statistiske Efterretninger 1979 A 24: Valget til Europa-parlamentet den 7. juni 1979",
    ),
    1984: ("Danmarks Statistik", "Statistiske Efterretninger 1984:13: Valget til Europa-parlamentet den 14. juni 1984"),
    1989: ("Danmarks Statistik", "Statistiske Efterretninger 1989:11: Valget til Europa-parlamentet den 15. juni 1989"),
    1994: ("Indenrigsministeriet", "Europa-Parlamentsvalget den 9. juni 1994"),
    1999: ("Indenrigsministeriet", "Europa-Parlamentsvalget den 10. juni 1999"),
    2004: ("Indenrigs- og Sundhedsministeriet", "Europa-Parlamentsvalget den 13. juni 2004"),
}
LICENSE = "Official statistics of the Danish authorities, published for reuse with attribution to the publisher"
SYSTEM_LIST = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1flist"))
SYSTEM_SEAT = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fseat"))
SCAN_METHOD = (
    "Read from the scanned issue's text layer, which is the scan's character recognition, so every figure keeps the page "
    "and box it was read from. Each row's figures were checked against its own sums: the parties' votes add to the valid "
    "votes, the valid and invalid votes add to the ballots cast, and the districts add to the printed totals of their "
    "county or metropolitan constituency, their area, and the country. Where the text layer garbled a figure, the figure "
    "was read from the page and confirmed by those sums. Invalid ballots are those invalid for a reason other than being blank. A party's votes include the personal votes for its candidates."
)
TEXT_METHOD = (
    "Taken from the typeset tables of the official report. The parties' votes add to the valid votes of each district, "
    "the valid and invalid votes add to the ballots cast, and the districts add to the printed totals of their county or "
    "metropolitan constituency and of the country. Invalid ballots are those invalid for a reason other than being blank. A party's votes include the personal votes for its candidates."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def party_key(year: int, letter: str) -> str:
    return PARTY_OF.get(letter, letter.lower())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Danish parties and the electoral systems of the 1979 to 2004 European Parliament elections",
    )
    p.external.update({DENMARK, GREENLAND})
    p.add(
        "scheme",
        code="dk.storkreds",
        polity_id=DENMARK,
        kind="electoral_district",
        rank=2,
        noun="metropolitan constituency",
    )
    systems = (
        (
            SYSTEM_LIST,
            DENMARK,
            {"formula": "dhondt"},
            (
                "Party-list proportional representation in one nationwide constituency. Parties may form electoral "
                "alliances, whose seats are divided in two steps by the highest-average method, and the seats of a "
                "party go to its candidates by their personal votes, or by the party's own order when it filed a "
                "party list."
            ),
        ),
        (
            SYSTEM_SEAT,
            GREENLAND,
            {"formula": "fptp"},
            "Greenland's one seat in the European Parliament went to the list with the most votes.",
        ),
    )
    for id, polity, rules, text in systems:
        p.add("electoral_system", id=id, polity_id=polity, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for code, name in PARTY_NAMES.items():
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=GREENLAND if code in ("atassut", "siumut") else DENMARK)
        p.name(id, name, "da", "official")
    return p


def source_of(p: Package, year: int) -> str:
    publisher, title = PUBLICATIONS[year]
    id = key("source", year)
    p.add(
        "source",
        id=id,
        kind="official_body",
        publisher=publisher,
        title=title,
        url=FILES[year],
        license=LICENSE,
        attribution=publisher,
    )
    p.artifact(raw.cached(SOURCE, FILES[year]), id)
    return id


def build(year: int) -> Package:
    assert sum(SEATS[year].values()) == MAGNITUDE[year]
    day = DAYS[year]
    slots = layout()
    table = typeset(year, slots) if year >= 1994 else scanned(year, slots)
    corrected(table, slots)
    found = check(table, slots) + sums(table, slots)
    if found:
        raise ValueError("\n".join(found))
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} European Parliament election in Denmark by nomination district",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update({DENMARK, "dk.amt", "dk.opstillingskreds"})
    source = source_of(p, year)
    election = key("election", year)
    p.add("election", id=election, polity_id=DENMARK, kind="general", date_from=day, date_to=day, status="held")
    path = f"dk/{day}-european"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} European Parliament election in Denmark", "en", "official")
    p.name(election, f"Europa-Parlamentsvalget {year}", "da", "native", False)
    scanned_year = year < 1994
    pages: dict[int, str] = {}
    if scanned_year:
        sizes = digitized.pages(raw.cached(SOURCE, FILES[year]).path, 1, 30)
        artifact = str(
            uuid5(package.NAMESPACE, f"source_artifact\x1f{source}\x1f{raw.cached(SOURCE, FILES[year]).storage_key}")
        )

        def page(number: int) -> str:
            if number not in pages:
                pages[number] = key("page", year, number)
                size = sizes[number]
                p.add(
                    "source_page",
                    id=pages[number],
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{raw.cached(SOURCE, FILES[year]).sha256}-{number}.png",
                    width=round(size.width * 100 / 72),
                    height=round(size.height * 100 / 72),
                )
            return pages[number]

    flags = DIGITIZED if scanned_year else 0

    def contest_of(code: str, english: str, danish: str, seats: int, system: str) -> tuple[str, str]:
        contest = key("contest", year, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            electoral_system_id=system,
            round_number=1,
            default_result_kind="counted",
            magnitude=seats,
        )
        p.name(contest, english, "en", "official")
        p.name(contest, danish, "da", "native", False)
        p.add("slug", path=f"{path}/{code}", entity_id=contest, is_current=True)
        result_set = key("result_set", year, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        return contest, result_set

    def put(result_set: str, unit: str, option: str | None, measure: int, figure: Fig) -> None:
        assert figure.value is not None
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=flags,
            value=figure.value,
        )
        if scanned_year:
            assert figure.page is not None and figure.box is not None
            x0, y0, x1, y1 = digitized.pixels(figure.box)
            p.add(
                "value_locator",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                page_id=page(figure.page),
                source_id=None,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
            )

    contest, result_set = contest_of(
        "parliament",
        f"{year} European Parliament election in Denmark",
        f"Europa-Parlamentsvalget {year}",
        MAGNITUDE[year],
        SYSTEM_LIST,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        lang="en",
        body_md=(SCAN_METHOD if scanned_year else TEXT_METHOD)
        + " The count is by nomination district (opstillingskreds), the finest unit these publications print for the "
        "whole country; the parties' votes by municipality and polling district are not stored here.",
    )
    options: dict[str, str] = {}
    for letter in PARTIES[year]:
        code = party_key(year, letter)
        options[letter] = key("option", year, "parliament", letter)
        name = LABELS.get((code, year), PARTY_NAMES[code])
        p.add(
            "contest_option",
            id=options[letter],
            contest_id=contest,
            kind="list",
            parent_option_id=None,
            party_id=key("party", code),
            ballot_position=None,
            ballot_label=name,
        )
    units: dict[str, str] = {}
    for k, slot in enumerate(slots):
        if slot.kind in ("country", "area"):
            continue
        id = key("unit", year, slot.code)
        units[slot.code] = id
        scheme = {"storkreds": "dk.storkreds", "amt": "dk.amt", "district": "dk.opstillingskreds"}[slot.kind]
        p.add(
            "reporting_unit",
            id=id,
            polity_id=DENMARK,
            scheme_code=scheme,
            code=f"ep{year}:{slot.code}",
            parent_id=units.get(slot.group) if slot.group else None,
            area_relation="unlocated",
            area_id=None,
            kind="aggregate" if slot.kind != "district" else "standard",
        )
        p.name(id, slot.name, "da", "official")
        if slot.kind != "district":
            continue
        counts = table.counts[k]
        votes = table.votes[k]
        valid = counts.get("valid") or votes["T"]
        for measure, name, figure in (
            (REGISTERED, "registered", counts["registered"]),
            (CAST, "cast", counts["cast"]),
            (VALID, "valid", valid),
            (INVALID, "other", counts["other"]),
            (BLANK, "blank", counts["blank"]),
        ):
            put(result_set, id, None, measure, figure)
        for letter in PARTIES[year]:
            put(result_set, id, options[letter], VOTES, votes[letter])
    for measure, name in ((REGISTERED, "registered"), (CAST, "cast"), (INVALID, "other"), (BLANK, "blank")):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=table.counts[0][name].value)
    national_valid = table.counts[0].get("valid") or table.votes[0]["T"]
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=national_valid.value)
    for letter in PARTIES[year]:
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=options[letter],
            measure=VOTES,
            value=table.votes[0][letter].value,
        )
    for letter, seats in SEATS[year].items():
        p.add("outcome", result_set_id=result_set, option_id=options[letter], kind="won", seats=seats)
    if year == 1984:
        greenland_contest(p, year, election, path, source, put_gl=put, contest_of=contest_of)
    return p


def greenland_contest(p: Package, year: int, election: str, path: str, source: str, put_gl, contest_of) -> None:  # type: ignore[no-untyped-def]
    contest, result_set = contest_of(
        "greenland",
        f"{year} European Parliament election in Greenland",
        f"Europa-Parlamentsvalget {year} i Grønland",
        1,
        SYSTEM_SEAT,
    )
    p.external.update({GREENLAND, "gl.kommune"})
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        lang="en",
        body_md=(
            "Greenland elected one member of the European Parliament in its own constituency in 1979 and 1984, before it "
            "left the Communities in 1985. The issue prints the count by municipality as a preliminary count of the "
            "returns received from Greenland, and each list had one candidate: Konrad Steenholdt for Atassut and Finn "
            "Lynge for Siumut. " + SCAN_METHOD.split(" Each row's")[0]
        ),
    )
    rows = greenland()
    options = {}
    for code in ("atassut", "siumut"):
        options[code] = key("option", year, "greenland", code)
        p.add(
            "contest_option",
            id=options[code],
            contest_id=contest,
            kind="list",
            parent_option_id=None,
            party_id=key("party", code),
            ballot_position=None,
            ballot_label=PARTY_NAMES[code],
        )
    for (label, figures), name in zip(rows[1:], GREENLAND_MUNICIPALITIES, strict=True):
        id = key("unit", year, "gl", name)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=GREENLAND,
            scheme_code="gl.kommune",
            code=f"ep{year}:{name}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(id, name, "da", "official")
        for measure, field_name in (
            (REGISTERED, "registered"),
            (CAST, "cast"),
            (VALID, "valid"),
            (INVALID, "other"),
            (BLANK, "blank"),
        ):
            put_gl(result_set, id, None, measure, figures[field_name])
        for code in ("atassut", "siumut"):
            put_gl(result_set, id, options[code], VOTES, figures[code])
    total = rows[0][1]
    for measure, field_name in (
        (REGISTERED, "registered"),
        (CAST, "cast"),
        (VALID, "valid"),
        (INVALID, "other"),
        (BLANK, "blank"),
    ):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=total[field_name].value)
    for code in ("atassut", "siumut"):
        p.add("declared", result_set_id=result_set, option_id=options[code], measure=VOTES, value=total[code].value)
    p.add("outcome", result_set_id=result_set, option_id=options["siumut"], kind="won", seats=1)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS.values())]


def parse() -> None:
    registry().write()
    for year in DAYS:
        build(year).write()


def emit() -> None:
    package.run("load", packages())
