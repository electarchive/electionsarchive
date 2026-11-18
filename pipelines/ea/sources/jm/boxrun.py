"""Runs `boxread` over every page of a scanned box-by-box report and writes the raw readings, one JSON file per page."""

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

from ea.sources.jm import boxread, scanned

# The report that prints each station's share of the vote in a column before its electors.
SHARE = "1997-12-18"


def page(args: tuple[str, int, dict[str, int], str] | tuple[str, int, dict[str, int], str, str]) -> dict[str, object]:
    pdf, number, counts, out = args[:4]
    target = Path(out) / f"{number:04d}.json"
    if target.exists() and len(args) == 4:
        return json.loads(target.read_text())
    loaded = boxread.load(Path(pdf), number)
    name = boxread.match(loaded.title, list(counts)) if len(args) == 4 else args[4]
    result: dict[str, object] = {"page": number, "title": loaded.title, "angle": loaded.angle, "name": name}
    if name is not None:
        try:
            read = boxread.table(loaded, counts[name] + 3, out.rstrip("/").endswith(SHARE))
        except ValueError as error:  # a page whose layout the reader cannot follow
            result["error"] = str(error)
            read = None
        if read is not None:
            result["percent"] = read.percent
            result["columns"] = read.columns
            result["rows"] = [
                {
                    "pd": r.pd,
                    "ps": r.ps,
                    "total": r.total_row,
                    "cells": [asdict(c) if c else None for c in r.cells],
                }
                for r in read.rows
            ]
    target.write_text(json.dumps(result))
    return result


def fill(day: str, pdf: str, out: Path) -> None:
    """Reads again, as the constituency of the page before, the pages whose heading named no constituency: the
    continuation pages of a table whose heading the reader garbled."""
    counts = {c.name: len(c.candidates) for c in scanned.read(day).constituencies}
    pages = sorted((json.loads(f.read_text()) for f in out.glob("0*.json")), key=lambda p: p["page"])
    named = [p["page"] for p in pages if p.get("name") and p.get("rows")]
    if not named:
        return
    jobs = []
    previous = None
    for p in pages:
        if p.get("name") and p.get("rows") or p.get("name"):
            previous = p["name"]
        elif previous and named[0] < p["page"] < named[-1] and not p.get("filled"):
            jobs.append((pdf, p["page"], counts, str(out), previous))
    with ProcessPoolExecutor(4) as pool:
        for result in pool.map(page, jobs):
            result["filled"] = True
            (out / f"{result['page']:04d}.json").write_text(json.dumps(result))
            rows = result.get("rows")
            print(result["page"], result["name"], len(rows) if isinstance(rows, list) else None, flush=True)


def main() -> None:
    if sys.argv[1] == "fill":
        fill(sys.argv[2], sys.argv[3], Path(sys.argv[4]))
        return
    if sys.argv[1] == "assemble":
        day, pdf, out = sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
        result = assemble(day, pdf, out)
        (out / "assembled.json").write_text(json.dumps(result))
        write(day, pdf, result)
        return
    day, pdf, out, first, last = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
    counts = {c.name: len(c.candidates) for c in scanned.read(day).constituencies}
    Path(out).mkdir(parents=True, exist_ok=True)
    jobs = [(pdf, n, counts, out) for n in range(first, last + 1)]
    with ProcessPoolExecutor(4) as pool:
        for result in pool.map(page, jobs):
            rows = result.get("rows")
            print(result["page"], result["name"], len(rows) if isinstance(rows, list) else None, flush=True)


def number(text: str | None) -> int | None:
    if text is None or "|" in text:
        return None
    return boxread.value(text)


def balanced(values: list[int | None], n: int) -> bool:
    votes = [v for v in values[:n] if v is not None]
    rejected, total = values[n], values[n + 1]
    return len(votes) == n and rejected is not None and total is not None and sum(votes) + rejected == total


def retry(pdf: Path, number_: int, rows: list[dict], n: int, percent: bool, columns: list[list[int]]) -> None:
    """Reads each cell of the rows that fail their checks again, enlarged, and keeps the reading that passes them
    with the fewest changes, if only one does."""
    import itertools

    loaded = _page(pdf, number_)
    for row in rows:
        values = row["values"]
        boxes = [b for b in row["boxes"] if b]
        if not boxes:
            continue
        y0, y1 = min(b[1] for b in boxes) - 6, max(b[3] for b in boxes) + 6
        used = n + 3 if percent and row.get("pct") is not None else n + 2
        options: list[list[int]] = []
        for i in range(used):
            found: list[int] = [] if values[i] is None else [values[i]]
            x0, x1 = columns[i]
            for scale, psm in ((2, "7"), (3, "7"), (2, "8")):
                v = boxread.value(boxread.cell(loaded.image, (x0 + 4, y0, x1 - 4, y1), scale, psm))
                if v is not None and v not in found:
                    found.append(v)
            options.append(found or [0])
        row["options"] = options
        fits = []
        for combo in itertools.product(*options):
            if sum(combo[:n]) + combo[n] != combo[n + 1]:
                continue
            if used > n + 2 and not (combo[n + 2] and abs(100 * combo[n + 1] / combo[n + 2] - row["pct"]) <= 1.0):
                continue
            changes = sum(1 for k, v in enumerate(combo) if options[k][0] != v)
            fits.append((changes, combo))
        if not fits:
            continue
        best = min(f[0] for f in fits)
        winners = {f[1] for f in fits if f[0] == best}
        if len(winners) == 1:
            row["fixed"] = list(next(iter(winners)))


def codes(rows: list[dict]) -> None:
    """Settles the polling-division codes by their order.

    The divisions run 1, 2, 3, ... down a constituency; a division split into several stations repeats its number with
    a letter, and a division without electors prints no figures. The codes are the nondecreasing sequence that agrees
    with the most readings; a code the sequence changes or fills in is marked as inferred.
    """
    import re

    def parse(code: str) -> tuple[int, str] | None:
        found = re.fullmatch(r"(\d{1,3})([A-Z]{0,2})", code or "")
        return (int(found[1]), found[2]) if found else None

    read = [parse(r["pd"]) for r in rows]
    station = [parse(r["ps"]) for r in rows]
    if not rows:
        return
    top = max([x[0] for x in read if x] + [len(rows)]) + 5
    steps = {0: -0.4, 1: 0.0, 2: -0.6, 3: -0.9}
    score = [[float("-inf")] * (top + 1) for _ in rows]
    back = [[0] * (top + 1) for _ in rows]
    hits = [r[0] if r is not None else None for r in read]
    for v in range(1, top + 1):
        score[0][v] = (1.0 if hits[0] == v else 0.0) - (0 if v == 1 else 0.5 + 0.01 * v)
    for i in range(1, len(rows)):
        hit = hits[i]
        for v in range(1, top + 1):
            best, arg = float("-inf"), 0
            for step, cost in steps.items():
                u = v - step
                if u >= 1 and score[i - 1][u] + cost > best:
                    best, arg = score[i - 1][u] + cost, u
            score[i][v] = best + (1.0 if hit == v else 0.0)
            back[i][v] = arg
    v = max(range(1, top + 1), key=lambda x: score[-1][x])
    numbers = [0] * len(rows)
    for i in range(len(rows) - 1, -1, -1):
        numbers[i] = v
        v = back[i][v]
    for i, row in enumerate(rows):
        n = numbers[i]
        s, r = station[i], read[i]
        letters = s[1] if s is not None and s[0] == n else (r[1] if r is not None and r[0] == n else "")
        row["code"] = f"{n}{letters}"
        if hits[i] != n:
            row["inferred"] = True
    by: dict[int, list[int]] = {}
    for i, n in enumerate(numbers):
        by.setdefault(n, []).append(i)
    for n, idx in by.items():
        if len(idx) > 1 and len({rows[i]["code"] for i in idx}) < len(idx):
            for k, i in enumerate(idx):
                if rows[i]["code"] != f"{n}{chr(65 + k)}":
                    rows[i]["code"] = f"{n}{chr(65 + k)}"
                    rows[i]["inferred"] = True


def _share_ok(r: dict, n: int) -> bool:
    total, electors, pct = r["values"][n + 1], r["values"][n + 2], r.get("pct")
    return bool(electors) and pct is not None and abs(100 * total / electors - pct) <= 1.0


_loaded: dict[tuple[str, int], boxread.Page] = {}


def _page(pdf: Path, number_: int) -> boxread.Page:
    if (str(pdf), number_) not in _loaded:
        _loaded.clear()
        _loaded[(str(pdf), number_)] = boxread.load(pdf, number_)
    return _loaded[(str(pdf), number_)]


def _electors(pdf: Path, page_: dict, r: dict, n: int) -> None:
    """Reads a station's electors again where they disagree with the share of the vote the report prints."""
    box = r["boxes"][n + 2]
    if box is None or r.get("pct") is None:
        return
    x0, x1 = page_["columns"][-1]
    loaded = _page(pdf, page_["page"])
    for scale, psm in ((2, "7"), (3, "7"), (2, "8"), (1, "7"), (3, "8")):
        v = boxread.value(boxread.cell(loaded.image, (x0 + 4, box[1] - 8, x1 - 4, box[3] + 8), scale, psm))
        if v:
            r["values"][n + 2] = v
            if _share_ok(r, n):
                return
    r["values"][n + 2] = None


def _fix_page(args: tuple[str, dict, list[dict], list[dict], int, str]) -> tuple[list[dict], list[dict]]:
    pdf, page_, rows, shares, n, out = args
    cache = Path(out) / f"fix-{page_['page']:04d}.json"
    if cache.exists():
        saved = json.loads(cache.read_text())
        if len(saved) == len(rows):
            for r, x in zip(rows, saved, strict=True):
                r.update(x)
            return rows, shares
    columns = [
        b for k, b in enumerate(page_["columns"]) if not (page_.get("percent") and k == len(page_["columns"]) - 2)
    ]
    if rows:
        retry(Path(pdf), page_["page"], rows, n, bool(page_.get("percent")), columns)
        cache.write_text(json.dumps([{k: r[k] for k in ("options", "fixed") if k in r} for r in rows]))
    for r in shares:
        _electors(Path(pdf), page_, r, n)
    return rows, shares


def _prepare(page_: dict, r: dict, n: int) -> None:
    percent = bool(page_.get("percent"))
    cells = [x for k, x in enumerate(r["cells"]) if not (percent and k == len(r["cells"]) - 2)]
    r["values"] = [number(x["text"]) if x else None for x in cells]
    r["cells_used"] = cells
    share = r["cells"][-2] if percent else None
    r["pct"] = number(share["text"].rstrip("%")) if share and share["text"].endswith("%") else None
    r["boxes"] = [[x["x0"], x["y0"], x["x1"], x["y1"]] if x else None for x in cells]
    r["page"] = page_["page"]
    r["angle"] = page_["angle"]
    if not balanced(r["values"], n) and r["values"][n + 1] is not None:
        # A blank or a dash the reading dropped is a nil: kept only when the row then balances.
        filled = [0 if v is None and k <= n else v for k, v in enumerate(r["values"])]
        if balanced(filled, n):
            r["blank"] = [k for k, v in enumerate(r["values"]) if v is None and k <= n]
            r["values"] = filled


# Digits the reader takes for one another in these scans.
CONFUSED = {"1": "47", "4": "1", "7": "1", "8": "0369", "0": "869", "3": "8", "9": "08", "6": "58", "5": "6", "2": "7"}


def variants(text: str | None) -> set[int]:
    """The figures a reading could be with one digit misread, dropped, or added."""
    if not text:
        return set()
    out: set[int] = set()
    for part in text.split("|"):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            out.add(0)  # a dash
            continue
        out.add(int(digits))
        for i, ch in enumerate(digits):
            for alt in CONFUSED.get(ch, ""):
                out.add(int(digits[:i] + alt + digits[i + 1 :]))
            if len(digits) > 1:
                out.add(int(digits[:i] + digits[i + 1 :]))
        for i in range(len(digits) + 1):
            out.add(int(digits[:i] + "1" + digits[i:]))
    return out


def repair(r: dict, n: int) -> None:
    """Settles a row that fails its check where exactly one cell can be changed to a likely misreading of itself so
    that the row balances (and, where the report prints it, agrees with the share of the vote)."""
    values = r["values"]
    texts = [x["text"] if x else None for x in r["cells_used"]]
    fixes = []
    for k in range(n + 2):
        others = [values[i] for i in range(n + 2) if i != k]
        if any(v is None for v in others):
            continue
        if k == n + 1:
            need = sum(values[:n]) + values[n]
        else:
            need = values[n + 1] - (sum(values[i] for i in range(n + 1) if i != k))
        if need < 0:
            continue
        if values[k] is None or need in variants(texts[k]):
            trial = [*values]
            trial[k] = need
            if r.get("pct") is not None and trial[n + 2] and abs(100 * trial[n + 1] / trial[n + 2] - r["pct"]) > 1.0:
                continue
            fixes.append(trial)
    if len(fixes) == 1:
        r["values"] = fixes[0]
        r["repaired"] = True


def _electors_fix(r: dict, n: int) -> None:
    if r.get("pct") is None or not balanced(r["values"], n) or _share_ok(r, n):
        return
    text = r["cells_used"][n + 2]["text"] if r["cells_used"][n + 2] else None
    total = r["values"][n + 1]
    fits = [v for v in variants(text) if v and abs(100 * total / v - r["pct"]) <= 1.0]
    if len(fits) == 1:
        r["values"][n + 2] = fits[0]
        r["repaired"] = True


def _vectors(r: dict, n: int) -> set[tuple[int, ...]]:
    """The ways a failing row balances when one or two of its cells are taken as misreadings of themselves."""
    values = r["values"][: n + 2]
    texts = [x["text"] if x else None for x in r["cells_used"][: n + 2]]
    pools = []
    for k in range(n + 2):
        pool = set(variants(texts[k]))
        for options in (r.get("options") or [])[k : k + 1]:
            pool.update(options)
        if values[k] is not None:
            pool.add(values[k])
        pools.append(pool)
    electors = r["values"][n + 2] if len(r["values"]) > n + 2 else None

    def fits(trial: list[int]) -> bool:
        if sum(trial[:n]) + trial[n] != trial[n + 1]:
            return False
        return r.get("pct") is None or not electors or abs(100 * trial[n + 1] / electors - r["pct"]) <= 1.0

    out: set[tuple[int, ...]] = set()
    base = [v if v is not None else -1 for v in values]
    for k1 in range(n + 2):
        for k2 in range(n + 2):
            if k2 == k1:
                continue
            for v1 in pools[k1]:
                trial = [*base]
                trial[k1] = v1
                if any(trial[i] < 0 for i in range(n + 2) if i != k2):
                    continue
                if k2 == n + 1:
                    need = sum(trial[:n]) + trial[n]
                else:
                    need = trial[n + 1] - sum(trial[i] for i in range(n + 1) if i != k2)
                if need < 0 or (need != base[k2] and need not in pools[k2]):
                    continue
                trial[k2] = need
                if fits(trial):
                    out.add(tuple(trial))
    return out


def _solve(rows: list[dict], n: int, targets: list[int], rejected: int | None, cast: int | None) -> None:
    """Settles the rows that still fail their checks from the readings of their cells and the summary's totals.

    Each failing row may take any combination of its cells' readings that balances; the combination of rows chosen
    must make every column add up to the summary's total. It is kept only if exactly one combination does.
    """
    import itertools

    if rejected is None or cast is None:
        return
    bad = [r for r in rows if not balanced(r["values"], n) and not r.get("seen")]
    if not bad or len(bad) > 8:
        return
    good = [r for r in rows if balanced(r["values"], n) or r.get("seen")]
    sums = [sum(r["values"][k] or 0 for r in good) for k in range(n + 2)]
    for perm in itertools.permutations(range(n)):
        goal = [0] * (n + 2)
        for i in range(n):
            goal[perm[i]] = targets[i]
        goal[n], goal[n + 1] = rejected, cast
        need = [g - x for g, x in zip(goal, sums, strict=True)]
        if any(v < 0 for v in need):
            continue
        choices: list[list[tuple[int, ...]]] = []
        for r in bad:
            vectors = _vectors(r, n)
            if len(bad) == 1:
                # One failing row: its figures are the totals less the other rows, if that differs from its own
                # reading in one cell at most.
                read = r["values"][: n + 2]
                if sum(1 for x, y in zip(need, read, strict=True) if x != y) <= 1:
                    vectors.add(tuple(need))
            choices.append(sorted(vectors))
        size = 1
        for c in choices:
            size *= max(1, len(c))
        if size > 2_000_000:
            continue
        found: list[tuple[tuple[int, ...], ...]] = []

        def walk(i: int, left: list[int], picked: tuple[tuple[int, ...], ...]) -> None:
            if len(found) > 1:
                return
            if i == len(choices):
                if not any(left):
                    found.append(picked)
                return
            for vector in choices[i]:
                rest = [a - b for a, b in zip(left, vector, strict=True)]
                if all(v >= 0 for v in rest):
                    walk(i + 1, rest, (*picked, vector))

        walk(0, need, ())
        if len(found) == 1:
            for r, vector in zip(bad, found[0], strict=True):
                r["values"][: n + 2] = list(vector)
                r["solved"] = True
            return


def _middle(r: dict) -> int:
    boxes = [b for b in r.get("boxes") or [] if b]
    return sorted((b[1] + b[3]) // 2 for b in boxes)[len(boxes) // 2]


def _insert(grouped: dict[str, list[tuple[dict, dict]]], entry: dict) -> None:
    """Adds a station row the reader skipped altogether, beside the row of its page nearest to it in height."""
    for items in grouped.values():
        on = [(p, r) for p, r in items if p["page"] == entry["page"] and not r["total"] and r.get("boxes")]
        if not on:
            continue
        page_, like = min(on, key=lambda m: abs(_middle(m[1]) - entry["y"]))
        shift = entry["y"] - _middle(like)
        row = {
            "pd": "?",
            "ps": "?",
            "total": False,
            "values": list(entry["values"]),
            "pct": None,
            "page": page_["page"],
            "angle": page_["angle"],
            "boxes": [[b[0], b[1] + shift, b[2], b[3] + shift] if b else None for b in like["boxes"]],
            "seen": True,
        }
        later = [
            i
            for i, (p, r) in enumerate(items)
            if p["page"] == page_["page"] and r.get("boxes") and not r["total"] and _middle(r) > entry["y"]
        ]
        items.insert(later[0] if later else len(items), (page_, row))
        return
    raise ValueError(f"page {entry['page']}: no rows to place an added row beside")


def _totals(grouped: dict[str, list[tuple[dict, dict]]], entry: dict) -> None:
    """Sets the figures of a constituency's totals line from the page image, where the reading garbled them."""
    for items in grouped.values():
        for p, r in items:
            ys = [(c["y0"] + c["y1"]) // 2 for c in r["cells"] if c]
            if p["page"] == entry["page"] and r["total"] and ys and abs(sorted(ys)[len(ys) // 2] - entry["y"]) <= 12:
                r["values"] = list(entry["values"])
                return
    raise ValueError(f"page {entry['page']}, y {entry['y']}: no totals line to set")


def _override(day: str, grouped: dict[str, list[tuple[dict, dict]]]) -> None:
    """Puts the rows read by eye from the page images (`boxes/<day>.overrides.json`) in place of the readings."""
    path = Path(__file__).parent / "boxes" / f"{day}.overrides.json"
    if not path.exists():
        return
    for entry in json.loads(path.read_text())["rows"]:
        if entry.get("add"):
            _insert(grouped, entry)
            continue
        if entry.get("totals"):
            _totals(grouped, entry)
            continue
        matches = []
        for items in grouped.values():
            for p, r in items:
                boxes = [b for b in r.get("boxes") or [] if b]
                if p["page"] != entry["page"] or r["total"] or not boxes:
                    continue
                middle = sorted((b[1] + b[3]) // 2 for b in boxes)[len(boxes) // 2]
                if abs(middle - entry["y"]) <= 12:
                    matches.append((abs(middle - entry["y"]), r))
        if not matches:
            raise ValueError(f"{day}: override at page {entry['page']}, y {entry['y']} matches no row")
        if entry["values"] is None:
            for _, r in matches:
                r["dropped"] = True
        else:
            r = min(matches, key=lambda m: m[0])[1]
            r["values"] = list(entry["values"])
            r["seen"] = True


def _spurious(rows: list[dict], n: int, targets: list[int], rejected: int | None, cast: int | None) -> None:
    """Drops the one row whose figures are exactly what the columns exceed the summary's totals by: a line of the
    headings or notes the reader took for a station."""
    import itertools

    if rejected is None or cast is None:
        return
    sums = [sum(r["values"][k] or 0 for r in rows) for k in range(n + 2)]
    for perm in itertools.permutations(range(n)):
        goal = [0] * (n + 2)
        for i in range(n):
            goal[perm[i]] = targets[i]
        goal[n], goal[n + 1] = rejected, cast
        extra = [a - b for a, b in zip(sums, goal, strict=True)]
        if not any(extra):
            return
        same = [r for r in rows if r["values"][: n + 2] == extra]
        if len(same) == 1:
            same[0]["dropped"] = True
            return


def assemble(day: str, pdf: Path, out: Path, fix: bool = True) -> dict:
    import itertools

    report = scanned.read(day)
    pages = sorted(
        (json.loads(f.read_text()) for f in out.glob("*.json") if f.name[0].isdigit()), key=lambda p: p["page"]
    )
    grouped: dict[str, list[tuple[dict, dict]]] = {}
    for page_ in pages:
        flagged = [r for r in page_.get("rows") or [] if r["total"]]
        for r in flagged[:-1]:
            r["total"] = False  # only the last line at the totals label is the totals line
        if page_.get("name") and page_.get("rows"):
            for row in page_["rows"]:
                grouped.setdefault(page_["name"], []).append((page_, row))
    sizes = {c.name: len(c.candidates) for c in report.constituencies}
    tasks: list[tuple[str, dict, list[dict], list[dict], int, str]] = []
    for name, items in grouped.items():
        n = sizes[name]
        by_page: dict[int, tuple[dict, list[dict]]] = {}
        for p, r in items:
            if not r["total"]:
                _prepare(p, r, n)
                by_page.setdefault(p["page"], (p, []))[1].append(r)
        for p, rows in by_page.values():
            failing = [
                r
                for r in rows
                if not balanced(r["values"], n) or (p.get("percent") and r["pct"] is not None and not _share_ok(r, n))
            ]
            shares: list[dict] = []
            if fix and (failing or shares):
                tasks.append((str(pdf), p, failing, shares, n, str(out)))
    if tasks:
        with ProcessPoolExecutor(4) as pool:
            for (_, _, rows, shares, n, _), (fixed, rechecked) in zip(tasks, pool.map(_fix_page, tasks), strict=True):
                for r, f in zip(rows, fixed, strict=True):
                    if "options" in f:
                        r["options"] = f["options"]
                    if "fixed" in f:
                        r["values"][: len(f["fixed"])] = f["fixed"]
                        r["fixed"] = f["fixed"]
                for r, f in zip(shares, rechecked, strict=True):
                    r["values"] = f["values"]
    _override(day, grouped)
    result: dict[str, dict] = {}
    for c in report.constituencies:
        n = len(c.candidates)
        items = [(p, r) for p, r in grouped.get(c.name, []) if not r.get("dropped")]
        stations = [(p, r) for p, r in items if not r["total"]]
        printed = [(p, r) for p, r in items if r["total"]]
        # A row read above the first or below the last station number of a page that does not balance is a reading
        # of the headings or of the notes under the table.
        edge: set[int] = set()
        for number_ in {p["page"] for p, _ in stations}:
            on = [r for p, r in stations if p["page"] == number_]
            for run in (on, list(reversed(on))):
                for r in run:
                    blanks = sum(1 for v in r["values"] if v is None)
                    if balanced(r["values"], n) or (r["ps"] != "?" and blanks < 2):
                        break
                    edge.add(id(r))
        stations = [(p, r) for p, r in stations if id(r) not in edge]
        rows: list[dict] = []
        last = {p["page"]: r for p, r in stations}
        for r in (r for _, r in stations):
            total = r["values"][n + 1]
            before = sum(x["values"][n + 1] or 0 for x in rows)
            if last[r["page"]] is r and len(rows) >= 5 and total is not None and total >= 0.8 * before:
                printed.append(({}, r))  # a totals line whose label the reading missed
                continue
            rows.append(r)
        for r in rows:
            if r.get("seen"):
                continue
            if not balanced(r["values"], n):
                repair(r, n)
            _electors_fix(r, n)
        targets = [x.votes or 0 for x in c.candidates]
        _solve(rows, n, targets, c.rejected, c.cast)
        _spurious(rows, n, targets, c.rejected, c.cast)
        rows = [r for r in rows if not r.get("dropped")]
        codes(rows)
        bad = [r for r in rows if not balanced(r["values"], n) and not r.get("seen")]
        sums = [sum(r["values"][k] or 0 for r in rows) for k in range(n + 3)]
        best: tuple[int, tuple[int, ...]] | None = None
        for perm in itertools.permutations(range(n)):
            diff = sum(abs(sums[perm[i]] - targets[i]) for i in range(n))
            if best is None or diff < best[0]:
                best = (diff, perm)
        if best is None:
            continue
        dup = len({r["code"] for r in rows}) != len(rows)
        totals_printed = []
        for p, r in printed:
            if "values" in r:
                totals_printed.append(r["values"][: n + 2])
            else:
                cells = [x for k, x in enumerate(r["cells"]) if not (p.get("percent") and k == len(r["cells"]) - 2)]
                totals_printed.append([number(x["text"]) if x else None for x in cells][: n + 2])
        # The report's own totals line agrees with its stations where the summary's figures differ.
        own = any(t == sums[: n + 2] for t in totals_printed) and best[0] <= 0.02 * (c.cast or 1)
        result[c.name] = {
            "number": c.number,
            "rows": len(rows),
            "bad": len(bad),
            "votes_diff": best[0],
            "order": best[1],
            "cast": (sums[n + 1], c.cast),
            "rejected": (sums[n], c.rejected),
            "electors": (sums[n + 2], c.electors),
            "ok": bool(rows)
            and not bad
            and not dup
            and ((best[0] == 0 and sums[n + 1] == c.cast and sums[n] == c.rejected) or own),
            "own": own and not (best[0] == 0 and sums[n + 1] == c.cast and sums[n] == c.rejected),
            "electors_ok": sums[n + 2] == c.electors and all(r["values"][n + 2] is not None for r in rows),
            "duplicates": dup,
            "printed": [[number(x["text"]) if x else None for x in r["cells"]] for _, r in printed],
            "stations": rows,
        }
    return result


def unrotate(box: list[int], angle: float, width: int, height: int) -> list[float]:
    """A box read on the straightened page, as a share of the page as scanned."""
    import math

    t = math.radians(angle)
    cx, cy = width / 2, height / 2
    points = []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
        points.append(
            (cx + (x - cx) * math.cos(t) - (y - cy) * math.sin(t), cy + (x - cx) * math.sin(t) + (y - cy) * math.cos(t))
        )
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [
        round(max(0.0, min(xs)) / width, 4),
        round(max(0.0, min(ys)) / height, 4),
        round(min(float(width), max(xs)) / width, 4),
        round(min(float(height), max(ys)) / height, 4),
    ]


def write(day: str, pdf: Path, result: dict) -> None:
    """Writes the constituencies that pass every check to `boxes/<day>.json`."""
    width, height = boxread.render(pdf, 1).size
    kept = []
    for name, c in result.items():
        if not c["ok"]:
            continue
        n = len(c["order"])
        order = c["order"]  # order[i] is the column of the i-th candidate of the summary
        stations = []
        for r in c["stations"]:
            values = r["values"]
            boxes = r["boxes"]
            picked = [order[i] for i in range(n)] + [n, n + 1] + ([n + 2] if c["electors_ok"] else [])
            stations.append(
                {
                    "code": r["code"],
                    "page": r["page"],
                    "votes": [values[k] for k in picked[:n]],
                    "rejected": values[n],
                    "total": values[n + 1],
                    "electors": values[n + 2] if c["electors_ok"] else None,
                    "boxes": [unrotate(boxes[k], r["angle"], width, height) if boxes[k] else None for k in picked],
                    **({"inferred": True} if r.get("inferred") else {}),
                }
            )
        kept.append(
            {
                "number": c["number"],
                "name": name,
                "electors": c["electors_ok"],
                "own_totals": c["own"],
                "stations": stations,
            }
        )
    kept.sort(key=lambda c: c["number"])
    target = Path(__file__).parent / "boxes" / f"{day}.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({"day": day, "constituencies": kept}, separators=(",", ":")) + "\n")
    print(f"{day}: {len(kept)} of {len(result)} constituencies pass")


if __name__ == "__main__":
    main()
