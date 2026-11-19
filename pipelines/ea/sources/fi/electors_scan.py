"""Reads the by-commune tables of Statistics Finland's elector volumes from the scans' text layer.

The text layer is machine-read, so it garbles some figures. Each row is anchored on the row number the volume prints at
the margin, its figures are assigned to columns by where they end on the page, and the figures are then checked against
each other (the parties' votes add up to the ballots) and against the constituency rows the volume prints. A figure the
checks flag is corrected by hand from the page image and listed under `fixes` in the year's file.
"""

import itertools
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

Word = tuple[float, float, float, float, str]
DASH = re.compile(r"^[-—–_—–•.~=]+$")
NUMBER = re.compile(r"^[0-9.,:;'`´]+$")
SUBTOTAL = re.compile(
    r"kaupungit|städer|villes|maaseutu|landsbygd|rurales|kauppalat|köpingar|bourgs|yhteens|summa|total|vaalipiiri|valkrets"
    r"|circonscription|kaupunkikunnat|maalaiskunnat|stadskommuner|landskommuner",
    re.IGNORECASE,
)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.words: list[Word] = []
        self.size = (1.0, 1.0)
        self._box: tuple[float, float, float, float] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {
            k: float(v) for k, v in attrs if v is not None and k in {"xmin", "ymin", "xmax", "ymax", "width", "height"}
        }
        if tag == "page":
            self.size = (values["width"], values["height"])
        elif tag == "word":
            self._box = (values["xmin"], values["ymin"], values["xmax"], values["ymax"])

    def handle_data(self, data: str) -> None:
        if self._box is not None:
            self.words.append((*self._box, data.strip()))
            self._box = None


def read(path: Path, page: int) -> tuple[tuple[float, float], list[Word]]:
    out = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-bbox", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    parser = _Page()
    parser.feed(out.stdout)
    return parser.size, parser.words


Box = tuple[float, float, float, float]


@dataclass
class Row:
    page: int
    number: int
    name: str
    y: float
    figs: list[tuple[float, int | None, Box]] = field(default_factory=list)  # right edge in points, value, box
    cells: dict[int, tuple[int, Box]] = field(default_factory=dict)  # by column index
    width: float = 1.0


@dataclass(frozen=True)
class Layout:
    """Where a volume's by-commune table sits on its pages."""

    pages: tuple[int, ...]
    columns: int  # figure columns a row prints, counting the ones that are not stored
    name_end: float  # where a row's name ends and its figures begin, measured from the margin number's left edge
    halves: int = 2  # printed pages on one scan; their rows share the margin numbers
    tolerance: float = 3.4


def _line(tokens: list[Word]) -> str:
    out = ""
    last = None
    for x0, _, x1, _, text in sorted(tokens, key=lambda t: t[0]):
        if last is not None and x0 - last > 1.4:
            out += " "
        out += text
        last = x1
    return out


def _text(tokens: list[Word]) -> str:
    """The words of a name, line by line from the top, with the dot leaders dropped."""
    lines: list[list[Word]] = []
    for w in sorted(tokens, key=lambda t: t[1] + t[3]):
        if lines and abs((w[1] + w[3]) / 2 - (lines[-1][0][1] + lines[-1][0][3]) / 2) < 2.2:
            lines[-1].append(w)
        else:
            lines.append([w])
    out = " ".join(_line(line) for line in lines).replace("\xad ", "").replace("\xad", "")
    return re.sub(r"\s+", " ", re.sub(r"\.{2,}.*$", "", out)).strip(" .—-")


@dataclass
class _Figure:
    x0: float
    y0: float
    x1: float
    y1: float
    digits: str | None
    text: str


def figures(tokens: list[Word]) -> list[tuple[float, float, float, float, int | None]]:
    """The figures among a row's tokens, with thousands split by a space joined; a dash is a zero, junk is None."""
    out: list[_Figure] = []
    for x0, y0, x1, y1, text in sorted(tokens, key=lambda t: t[0]):
        text = text.replace("&apos;", "'")
        digits = re.sub(r"[^0-9]", "", text)
        if NUMBER.match(text) and digits:
            decimal = bool(re.fullmatch(r"\d+\.\d", text))
            prev = out[-1] if out else None
            gap = x0 - prev.x1 if prev else 99.0
            joined = (
                prev is not None and not decimal and prev.digits is not None and not re.search(r"[.\-—–_]", prev.text)
            )
            if (
                prev is not None
                and joined
                and prev.digits is not None
                and (gap < 1.7 or (gap < 3.3 and len(digits) == 3 and len(prev.digits) <= 3))
            ):
                prev.x1, prev.y1 = x1, max(prev.y1, y1)
                prev.digits += digits
                prev.y0 = min(prev.y0, y0)
            else:
                out.append(_Figure(x0, y0, x1, y1, digits, text))
        elif DASH.match(text):
            out.append(_Figure(x0, y0, x1, y1, "0", text))
        else:
            out.append(_Figure(x0, y0, x1, y1, None, text))
    return [(f.x0, f.y0, f.x1, f.y1, None if f.digits is None else int(f.digits)) for f in out]


def centers(values: list[float], tolerance: float, support: int) -> list[tuple[float, int]]:
    """Cluster right edges: the means of the groups with at least `support` members."""
    groups: list[list[float]] = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] <= tolerance:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [(sum(g) / len(g), len(g)) for g in groups if len(g) >= support]


def _median(values: list[float]) -> float:
    return sorted(values)[len(values) // 2] if values else 0.0


def scan(path: Path, layout: Layout) -> tuple[list[Row], list[str]]:
    """Every numbered row of the layout's pages, with its figures placed in columns."""
    rows: list[Row] = []
    problems: list[str] = []
    for page in layout.pages:
        (width, height), found = read(path, page)

        def margin_numbers(lo: float, hi: float) -> list[Word]:
            """The row numbers printed between two x positions: integers that end at the commonest right edge."""
            ints = [w for w in found if lo <= w[0] <= hi and re.fullmatch(r"[1-9]\d{0,2}", w[4])]
            if not ints:
                return []
            best = max((w[2] for w in ints), key=lambda e: sum(abs(w[2] - e) < 2.2 for w in ints))
            return sorted((w for w in ints if abs(w[2] - best) < 2.2), key=lambda w: w[1])

        halves = layout.halves
        lead = margin_numbers(0, width * 0.2)
        # A number the text layer missed leaves a gap in the sequence: put a mark where it should be.
        filled: list[Word] = []
        for a, b in itertools.pairwise(lead):
            filled.append(a)
            skipped = int(b[4]) - int(a[4]) - 1
            if 0 < skipped <= 3 and b[1] - a[1] > 0:
                step = (b[1] - a[1]) / (skipped + 1)
                for j in range(1, skipped + 1):
                    filled.append((a[0], a[1] + step * j, a[2], a[3] + step * j, str(int(a[4]) + j)))
        if lead:
            filled.append(lead[-1])
        virtual = set(filled) - set(lead)
        lead = filled
        wanted = {int(w[4]) for w in lead}
        marks: list[list[tuple[float, int]]] = [sorted(((w[1] + w[3]) / 2, int(w[4])) for w in lead)]
        markers = set(lead) - virtual
        for h in range(1, halves):
            seen: dict[int, Word] = {}
            for w in margin_numbers(width * (h + 0.78) / halves, width):
                n = int(w[4])
                if n in wanted and (n not in seen or w[0] > seen[n][0]):
                    seen[n] = w
            if len(seen) < len(lead) * 0.5:
                # No numbers on the other half: line its rows up by where its figure lines fall against the first half's.
                lines = sorted(
                    (w[1] + w[3]) / 2 for w in found if w[0] > width * h / halves + 30 and re.fullmatch(r"[\d.]+", w[4])
                )
                seen = {}
                offsets: list[tuple[float, float]] = []
                ys = sorted((l[1] + l[3]) / 2 for l in lead)
                for lo in range(0, len(ys), 10):
                    chunk = ys[lo : lo + 10]
                    best = min(
                        (sum(min(abs(l - (y + d / 4)) for l in lines) for y in chunk), d / 4) for d in range(-40, 41)
                    )
                    offsets.append((sum(chunk) / len(chunk), best[1]))
                mine = []
                for l in lead:
                    y = (l[1] + l[3]) / 2
                    near = min(offsets, key=lambda o: abs(o[0] - y))
                    mine.append((y + near[1], int(l[4])))
                marks.append(sorted(mine))
                continue
            pairs = sorted(
                ((l[1] + l[3]) / 2, (seen[int(l[4])][1] + seen[int(l[4])][3]) / 2) for l in lead if int(l[4]) in seen
            )
            mine = [((w[1] + w[3]) / 2, n) for n, w in seen.items()]
            for l in lead:
                n, y = int(l[4]), (l[1] + l[3]) / 2
                if n not in seen and pairs:
                    near = min(pairs, key=lambda pair: abs(pair[0] - y))
                    mine.append((y + near[1] - near[0], n))
            marks.append(sorted(mine))
            markers.update(seen.values())
        cuts = [width * (i + 1) / halves for i in range(halves - 1)]
        by_row: dict[tuple[int, int], list[Word]] = {}
        orphans: dict[int, list[Word]] = {}
        for word in found:
            if word in markers:
                continue
            half = sum((word[0] + word[2]) / 2 > cut for cut in cuts)
            yc = (word[1] + word[3]) / 2
            near = [(abs(y - yc), i) for i, (y, _) in enumerate(marks[half])]
            if near and min(near)[0] <= layout.tolerance:
                by_row.setdefault((half, min(near)[1]), []).append(word)
            else:
                orphans.setdefault(half, []).append(word)
        # A name that wraps puts its figures on a second line, between its own number and the next row's.
        for half, lines in orphans.items():
            lines.sort(key=lambda w: w[1])
            groups: list[list[Word]] = []
            for w in lines:
                if groups and abs((w[1] + w[3]) / 2 - (groups[-1][-1][1] + groups[-1][-1][3]) / 2) < 2.0:
                    groups[-1].append(w)
                else:
                    groups.append([w])
            for group in groups:
                yc = sum((w[1] + w[3]) / 2 for w in group) / len(group)
                above = [(yc - y, i) for i, (y, _) in enumerate(marks[half]) if 0 < yc - y <= 9.5]
                if above:
                    i = min(above)[1]
                    by_row.setdefault((half, i), []).extend(group)
        # Two lines can fall inside one row's window when a name wraps; the line that is not the row's own belongs to the
        # row above.
        for (half, i), tokens in sorted(by_row.items(), key=lambda kv: kv[0][1], reverse=True):
            lines: list[list[Word]] = []
            for w in sorted(tokens, key=lambda t: t[1] + t[3]):
                if lines and abs((w[1] + w[3]) / 2 - (lines[-1][-1][1] + lines[-1][-1][3]) / 2) < 2.2:
                    lines[-1].append(w)
                else:
                    lines.append([w])
            if len(lines) > 1 and i > 0:
                mark = marks[half][i][0]
                own = min(lines, key=lambda ln: abs(sum((w[1] + w[3]) / 2 for w in ln) / len(ln) - mark))
                for ln in lines:
                    if ln is not own and sum((w[1] + w[3]) / 2 for w in ln) / len(ln) < mark:
                        by_row.setdefault((half, i - 1), []).extend(ln)
                        for w in ln:
                            tokens.remove(w)
        right: dict[int, list[Word]] = {}
        for (half, i), tokens in by_row.items():
            if half:
                right.setdefault(marks[half][i][1], []).extend(tokens)
        page_rows: list[Row] = []
        margin = _median([w[0] for w in lead])
        cut = margin + layout.name_end
        half_start = 0.0
        for i, (y, n) in enumerate(marks[0]):
            tokens = by_row.get((0, i), []) + right.get(n, [])
            row = Row(
                page,
                n,
                _text([t for t in tokens if t[0] < cut and t[0] > margin + 5 and t not in markers]),
                y,
                width=width,
            )
            for x0, y0, x1, y1, v in figures([t for t in tokens if t[0] >= cut or (t[0] >= half_start and False)]):
                row.figs.append((x1, v, (x0 / width, y0 / height, x1 / width, y1 / height)))
            page_rows.append(row)
        full = [r for r in page_rows if len(r.figs) >= layout.columns * 0.7]
        found_edges = centers([x for r in full for x, _, _ in r.figs], 2.6, max(3, len(full) // 3))
        while len(found_edges) > layout.columns:
            found_edges.remove(min(found_edges, key=lambda e: e[1]))
        edges = [e for e, _ in found_edges]
        if len(edges) != layout.columns:
            problems.append(
                f"page {page}: {len(edges)} columns found, {layout.columns} expected: {[round(e) for e in edges]}"
            )
            continue
        for row in page_rows:
            for x, v, box in sorted(row.figs, key=lambda f: f[0]):
                c = min(range(len(edges)), key=lambda k: abs(edges[k] - x))
                if abs(edges[c] - x) > layout.tolerance + 1.5 or v is None:
                    continue
                if c in row.cells:
                    held, held_box = row.cells[c]
                    # A thousands space the text layer set wide: the held figure leads and this one has three digits.
                    if len(str(v)) == 3 and held_box[2] < box[0] + 0.01 and len(str(held)) <= 3:
                        row.cells[c] = (
                            int(str(held) + str(v)),
                            (held_box[0], min(held_box[1], box[1]), box[2], max(held_box[3], box[3])),
                        )
                    else:
                        problems.append(f"page {page} row {row.number} {row.name}: two figures in column {c}")
                    continue
                row.cells[c] = (v, box)
        rows.extend(page_rows)
    return rows, problems


@dataclass(frozen=True)
class Spec:
    """What a volume's by-commune columns hold."""

    layout: Layout
    registered: int
    cast: int
    invalid: int | None
    votes: dict[str, int]  # party key to column
    sums: tuple[tuple[tuple[int, ...], int], ...]  # columns that add up to another column
    valid: int | None = None
    slack: tuple[tuple[int, int], ...] = ()  # a total column and how far above its parts' sum it may sit
    elsewhere: str = r"uista\s+vaalipiir|insända"  # the row that holds ballots sent in from other constituencies
    skip: str = r"^\d+\.\s|^Kaupunki\s*[—–-]"


def repair(cells: dict[int, tuple[int, Box]], spec: Spec) -> dict[int, int]:
    """The row's figures, with a figure the text layer dropped filled in where the row's sums fix it: one missing
    figure is what remains of its sum, and several are zeros when nothing remains (a dash the layer lost)."""
    c = {k: v[0] for k, v in cells.items()}
    for parts, total in spec.sums:
        missing = [k for k in parts if k not in c]
        if total not in c and not missing:
            c[total] = sum(c[k] for k in parts)
        elif total in c and missing:
            rest = c[total] - sum(c[k] for k in parts if k in c)
            if len(missing) == 1 and rest >= 0:
                c[missing[0]] = rest
            elif rest == 0:
                c.update({k: 0 for k in missing})
    if spec.cast in c:
        # A dash is a zero, and the text layer loses some; if a figure that is really missing mattered, the sums fail.
        c.update(
            {k: 0 for k in (*spec.votes.values(), *([spec.invalid] if spec.invalid is not None else [])) if k not in c}
        )
    return c


def variants(value: int) -> set[int]:
    """The figures one misread digit away from `value`: a digit changed, dropped, or added."""
    text = str(value)
    out: set[int] = set()
    for i in range(len(text)):
        for d in "0123456789":
            out.add(int(text[:i] + d + text[i + 1 :]))
        if len(text) > 1:
            out.add(int(text[:i] + text[i + 1 :]))
    for i in range(len(text) + 1):
        for d in "0123456789":
            out.add(int(text[:i] + d + text[i:]))
    out.discard(value)
    return out


def mend(c: dict[int, int], spec: Spec) -> tuple[dict[int, int], list[tuple[int, int, int]]]:
    """A row whose sums fail by one misread figure, with the one figure changed that makes every sum hold. A row with
    no such single fix, or more than one, comes back unchanged."""
    if sound(c, spec):
        return c, []
    found: list[tuple[int, int]] = []
    for k, old in c.items():
        for new in variants(old):
            trial = {**c, k: new}
            if sound(trial, spec):
                found.append((k, new))
    if len(found) == 1:
        k, new = found[0]
        return {**c, k: new}, [(k, c[k], new)]
    return c, []


def alternatives(value: int, seen: int | None) -> set[int]:
    """The readings of one figure: the text layer's, a second reading's, and the text layer's leading digits joined to
    the second reading's last ones (a thousands space makes the second reading drop the thousands)."""
    out = {value}
    if seen is not None:
        out.add(seen)
        text = str(value)
        if len(str(seen)) < len(text):
            out.add(int(text[: len(text) - len(str(seen))] + str(seen)))
    return out


def mend_with(
    c: dict[int, int], spec: Spec, second: dict[int, int | None]
) -> tuple[dict[int, int], list[tuple[int, int, int]]]:
    """The fewest changes, to readings a second look at the page gives, that make every sum hold."""
    options = {k: sorted(alternatives(v, second.get(k)) - {v}) for k, v in c.items() if second.get(k) is not None}
    movable = [k for k in options if options[k]]
    for size in (1, 2):
        found: list[dict[int, int]] = []
        for chosen in itertools.combinations(movable, size):
            for picks in itertools.product(*(options[k] for k in chosen)):
                trial = {**c, **dict(zip(chosen, picks, strict=True))}
                if sound(trial, spec):
                    found.append(dict(zip(chosen, picks, strict=True)))
        if len(found) == 1:
            return {**c, **found[0]}, [(k, c[k], v) for k, v in found[0].items()]
        if found:
            break
    return c, []


def sound(c: dict[int, int], spec: Spec) -> bool:
    need = {spec.registered, spec.cast, *spec.votes.values(), *(k for parts, t in spec.sums for k in (*parts, t))}
    if any(k not in c for k in need):
        return False
    slack = dict(spec.slack)
    return all(0 <= c[t] - sum(c[k] for k in parts) <= slack.get(t, 0) for parts, t in spec.sums)


NAME_SPLIT = re.compile(r"\s*[—–]+\s*[•■]?\s*")
WORDS = {"kp", "lk", "mlk", "k:la", "k;la", "st", "S:t"}


def clean_name(text: str) -> str:
    """A name with the letter spacing of the printed small capitals closed up, and footnote marks and leaders dropped."""
    text = re.sub(r"[•■_]+|\.{2,}", " ", text)
    text = re.sub(r"\s+\d+$", "", text.strip())
    parts = text.split()
    out: list[str] = []
    for part in parts:
        if (
            out
            and part not in WORDS
            and (
                len(part) <= 2
                and part.islower()
                and "." not in part
                and ":" not in part
                or len(out[-1]) == 1
                and out[-1].isupper()
            )
        ):
            out[-1] += part
        else:
            out.append(part)
    return " ".join(out).strip(" .,")


def split_name(text: str) -> tuple[str, str]:
    """A commune's Finnish and Swedish names from the printed 'Finnish — Swedish'; the Swedish is empty when only one is printed."""
    parts = [clean_name(p) for p in NAME_SPLIT.split(text) if clean_name(p)]
    return (parts[0], parts[1]) if len(parts) > 1 else (parts[0] if parts else "", "")


def cell_boxes(row: Row, columns: dict[str, int]) -> dict[str, list[float]]:
    return {k: [round(v, 4) for v in row.cells[c][1]] for k, c in columns.items() if c in row.cells}


def _entry(row: Row, c: dict[int, int], spec: Spec, columns: dict[str, int], name: tuple[str, str]) -> dict[str, Any]:
    entry: dict[str, Any] = {"fi": name[0], "page": row.page, "row": row.number}
    if name[1]:
        entry["sv"] = name[1]
    entry["votes"] = {key: c[col] for key, col in columns.items() if key in spec.votes and col in c}
    entry["totals"] = {key: c[col] for key, col in columns.items() if key not in spec.votes and col in c}
    entry["boxes"] = cell_boxes(row, columns)
    return entry


def extract(
    path: Path,
    spec: Spec,
    districts: list[dict[str, Any]],
    reread: Callable[[int, Box], int | None] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """The communes of each constituency, in print order, with their figures, pages and boxes, and what disagrees."""
    rows, problems = scan(path, spec.layout)
    segments: list[tuple[list[tuple[Row, dict[int, int]]], Row | None]] = []
    current: list[tuple[Row, dict[int, int]]] = []
    for row in rows:
        if re.search(spec.elsewhere, row.name, re.IGNORECASE):
            segments.append((current, row))
            current = []
            continue
        if not row.name or SUBTOTAL.search(row.name) or re.search(spec.skip, row.name, re.IGNORECASE):
            continue
        figures_ = repair(row.cells, spec)
        if spec.registered in figures_ or len(figures_) >= 12:
            current.append((row, figures_))
    if current:
        segments.append((current, None))
    columns = {"registered": spec.registered, "cast": spec.cast, **spec.votes}
    if spec.invalid is not None:
        columns["invalid"] = spec.invalid
    if spec.valid is not None:
        columns["valid"] = spec.valid
    names = {col: key for key, col in columns.items()}
    names.update(
        {1: "registered_men", 2: "registered_women", 4: "voters_own", 5: "voters_other", 6: "voters_away", 7: "voters"}
    )
    if len(segments) != len(districts):
        problems.append(f"{len(segments)} constituency segments found, {len(districts)} expected")
    out: list[dict[str, Any]] = []
    for (members, elsewhere), district in zip(segments, districts, strict=False):
        communes: list[dict[str, Any]] = []
        for row, c in members:
            mended_by: dict[str, list[int]] = {}
            unsound = False
            if not sound(c, spec):
                mended, changes = mend(c, spec)
                if not changes and reread is not None:
                    second = {col: reread(row.page, row.cells[col][1]) for col in row.cells}
                    mended, changes = mend_with(c, spec, second)
                if changes:
                    c = mended
                    mended_by = {names[k]: [old, new] for k, old, new in changes}
                else:
                    unsound = True
            entry = _entry(row, c, spec, columns, split_name(row.name))
            if mended_by:
                entry["mended"] = mended_by
            if unsound:
                entry["unsound"] = True
            communes.append(entry)
        item: dict[str, Any] = {"district": district["fi"], "communes": communes}
        if elsewhere is not None:
            item["elsewhere"] = _entry(
                elsewhere, repair(elsewhere.cells, spec), spec, columns, ("Muista vaalipiireistä lähetettyjä", "")
            )
        out.append(item)
    return out, problems
