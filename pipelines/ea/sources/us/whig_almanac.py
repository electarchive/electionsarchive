import base64
import difflib
import io
import itertools
import json
import math
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

from anthropic.types import ImageBlockParam, MessageParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.sources.us import house_books

SOURCE = "us.whig_almanac"
RAW = "us.whig_almanac"
VERSION = "1"
KIND = "compendium"
PUBLISHER = "Horace Greeley and Company"
TITLE = "The Whig Almanac and United States Register for 1849"
LICENSE = "Public domain"
SUMMARY = "Presidential results of 1844 and 1848 by county from the Whig Almanac for 1849"
STATE = None
FIPS = None
ITEM = "vol1849whigalman00whig"
PDF = f"https://archive.org/download/{ITEM}/{ITEM}.pdf"
LANDING = f"https://archive.org/details/{ITEM}"
PAGES = tuple(range(53, 64))
CANDIDATES = {1848: ("Taylor", "Cass", "Van Buren"), 1844: ("Clay", "Polk", "Birney")}
NAMED: dict[tuple[str, str], str] = {}
ITEMS = {
    1844: (ITEM, 1849, date(1844, 11, 1), PAGES),
    1848: (ITEM, 1849, date(1848, 11, 7), PAGES),
}
KEYS = {
    (1844, state, 0): f"1844:{state}:president"
    for state in ("ME", "NH", "VT", "MA", "RI", "CT", "NY", "NJ", "PA", "DE", "MD", "VA", "NC", "GA", "AL", "MS")
    + ("LA", "TN", "KY", "OH", "IN", "IL", "MO", "MI", "AR", "SC")
}
VOLUMES = {year: (PDF, LANDING, day, pages) for year, (_, _, day, pages) in ITEMS.items()}
ERRATA: set[tuple[int, tuple[str, int]]] = set()
NAMES: dict[tuple[str, str], str] = {}
FORMER: dict[tuple[str, str], tuple[str, str, date]] = {}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = False
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 190, 230)
MODEL = "claude-haiku-5-5"
READS = {"c": 300, "d": 400, "e": 350}
JOB = "columns"
SPLITS = {53: 0.461, 55: 0.465, 57: 0.457, 59: 0.459, 61: 0.469}
CROPS = {
    page: (
        {"l": (0.0, 0.03, SPLITS[page] + 0.03, 0.97), "r": (SPLITS[page] - 0.02, 0.03, 0.95, 0.97)}
        if page in SPLITS
        else {"l": (0.04, 0.03, 0.57, 0.97), "r": (0.49, 0.03, 1.0, 0.97)}
    )
    for page in PAGES
}
CROPS[63] = {
    "l": (0.0, 0.03, 0.5, 0.2),
    "r": (0.44, 0.03, 0.95, 0.2),
    "x": (0.0, 0.17, 0.41, 0.48),
    "y": (0.37, 0.17, 0.66, 0.48),
    "z": (0.6, 0.17, 0.95, 0.48),
}
PROMPT = (
    "This is a crop of a page of the Whig Almanac for 1849 that prints election returns by county. Transcribe the "
    "county rows of the presidential tables exactly as printed, digit for digit; do not correct, estimate, or fill "
    "in anything. The crop may cut into a neighboring column at its edges: ignore partial text there. Skip tables "
    "headed Congress, Aggregate Congress Vote, or Congressmen, and all prose. Output plain text only, in this form. "
    "When a state heading is visible, write a line `# XX` with the state's two-letter postal code. Whenever a "
    "column heading line is visible, including a repeated one, write a line `HEAD | label, label, label` that lists "
    "every numeric column heading as printed, left to right (for example `Taylor, Cass, Clay, Polk, Crit., Pow.`). "
    "Each county or parish is one line, `County | n n n n`, with every number in that row left to right, one per "
    "column of the last HEAD, without thousands separators, and `-` for a cell that is empty, dashed, or holds words "
    "such as `new co.` or `new par.`. The dots between numbers are leaders, not digits. Leave out the district "
    "numbers in front of county names. A table that continues from the previous column starts without a heading: "
    "write its rows with no `#` or HEAD line. When a printed Total Vote line for a state is visible, write it as "
    "`TOTAL 1848 | Taylor 67141, Cass 49720` (one line per year, surnames as printed, numbers without commas). "
    "Skip any line you cannot read rather than guessing."
)


def fetch() -> None:
    raw.fetch(RAW, PDF, suffix=".pdf")


def crops(name: str) -> dict[str, tuple[bytes, tuple[int, int]]]:
    book = raw.cached(RAW, PDF)
    out: dict[str, tuple[bytes, tuple[int, int]]] = {}
    for page in PAGES:
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(
                ["pdftoppm", "-r", str(READS[name]), "-gray", "-png", "-f", str(page), "-l", str(page)]
                + ["-singlefile", str(book.path), f"{folder}/p"],
                check=True,
            )
            image = Image.open(f"{folder}/p.png").convert("L")
        width, height = image.size
        for side, (left, top, right, bottom) in CROPS[page].items():
            part = image.crop((round(left * width), round(top * height), round(right * width), round(bottom * height)))
            buffer = io.BytesIO()
            part.save(buffer, "PNG")
            out[f"p{page}{side}"] = (buffer.getvalue(), part.size)
    return out


def key(name: str, label: str) -> str:
    return f"{SOURCE}/{name}/{label}"


def ensure() -> bool:
    wanted: dict[str, batch.Want] = {}
    for name in READS:
        for label, (data, (width, height)) in crops(name).items():
            content: list[ImageBlockParam | TextBlockParam] = [
                ImageBlockParam(
                    type="image",
                    source={"type": "base64", "media_type": "image/png", "data": base64.b64encode(data).decode()},
                ),
                TextBlockParam(type="text", text=PROMPT),
            ]
            params = MessageCreateParamsNonStreaming(
                model=MODEL, max_tokens=16000, messages=[MessageParam(role="user", content=content)]
            )
            wanted[key(name, label)] = (params, (width * height / 750 * 0.05 + 6000 * 0.25) / 1e6)
    ready, note = batch.ensure(SOURCE, JOB, wanted)
    print(note)
    return ready


def _plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _candidate(year: int, label: str) -> str | None:
    plain = _plain(label)
    for name in CANDIDATES[year]:
        if plain[:2] == _plain(name)[:2] or (name == "Van Buren" and plain.startswith("v")):
            return name
    return None


def _columns(head: list[str]) -> dict[int, dict[str, int]]:
    plain = [_plain(label) for label in head]
    taylor = next((i for i, p in enumerate(plain) if p.startswith("ta")), None)
    clay = next((i for i, p in enumerate(plain) if p.startswith("cl")), None)
    out: dict[int, dict[str, int]] = {1848: {}, 1844: {}}
    if taylor is not None:
        out[1848]["Taylor"] = taylor
        if taylor + 1 < len(plain) and plain[taylor + 1].startswith("cas"):
            out[1848]["Cass"] = taylor + 1
        if taylor + 2 < len(plain) and plain[taylor + 2].startswith("v"):
            out[1848]["Van Buren"] = taylor + 2
    if clay is not None:
        out[1844]["Clay"] = clay
        if clay + 1 < len(plain) and plain[clay + 1].startswith("po"):
            out[1844]["Polk"] = clay + 1
            if clay + 2 < len(plain) and plain[clay + 2][:1] in ("b", "d"):
                out[1844]["Birney"] = clay + 2
    return out


def _read(name: str) -> tuple[list[tuple[int, str, str, list[int | None], list[str]]], dict[tuple[str, int, str], int]]:
    rows: list[tuple[int, str, str, list[int | None], list[str]]] = []
    totals: dict[tuple[str, int, str], int] = {}
    state = ""
    head: list[str] = []
    for page in PAGES:
        for side in CROPS[page]:
            message = json.loads(raw.cached(RAW, key(name, f"p{page}{side}")).path.read_text())
            text = "".join(block.get("text", "") for block in message["content"] if block.get("type") == "text")
            for line in text.splitlines():
                line = line.strip().strip("`")
                if match := re.fullmatch(r"#\s*([A-Z]{2})", line):
                    state = match[1]
                elif line.startswith("HEAD") and "|" in line:
                    head = [label.strip() for label in line.split("|", 1)[1].replace("|", ",").split(",")]
                elif match := re.fullmatch(r"TOTAL\s+(\d{4})\s*\|(.*)", line):
                    for part in match[2].split(",") if int(match[1]) in CANDIDATES else ():
                        if found := re.fullmatch(r"\s*(.*?)\s+([\d,]+)\s*", part):
                            candidate = _candidate(int(match[1]), found[1])
                            if candidate is not None:
                                totals[(state, int(match[1]), candidate)] = int(found[2].replace(",", ""))
                elif "|" in line and state and head:
                    county, rest = (part.strip() for part in line.split("|", 1))
                    county = re.sub(r"\s*[*\u2020\u2021]+$", "", county)
                    cells = rest.replace("|", " ").split()
                    if not county or len(cells) != len(head) or not all(re.fullmatch(r"\d+|-", c) for c in cells):
                        continue
                    rows.append((page, state, county, [None if c == "-" else int(c) for c in cells], head))
    return rows, totals


def build() -> None:
    reads = {name: _read(name) for name in READS}
    labeled: dict[tuple[int, str, int], str] = {}
    for page in PAGES:
        best = max(READS, key=lambda n: len({r[1] for r in reads[n][0] if r[0] == page}))
        seen_n: Counter[tuple[int, str]] = Counter()
        for row in reads[best][0]:
            if row[0] == page:
                seen_n[(page, _plain(row[2]))] += 1
                labeled[(page, _plain(row[2]), seen_n[(page, _plain(row[2]))])] = row[1]
    for name, (rows, totals) in reads.items():
        seen_n = Counter()
        fixed = []
        for row in rows:
            at = (row[0], _plain(row[2]))
            seen_n[at] += 1
            fixed.append((row[0], labeled.get((*at, seen_n[at]), row[1]), *row[2:]))
        reads[name] = (fixed, totals)
    first = next(iter(READS))
    order: list[tuple[str, str]] = []
    pages: dict[tuple[str, str], int] = {}
    cells: dict[tuple[str, str], dict[str, dict[tuple[int, str], int | None]]] = {}
    for name, (rows, _) in reads.items():
        seen: set[tuple[str, str]] = set()
        for page, state, county, values, head in rows:
            row = (state, _plain(county))
            if row not in cells:
                near = difflib.get_close_matches(row[1], [c for s, c in order if s == state], 1, 0.85)
                if near and (state, near[0]) not in seen:
                    row = (state, near[0])
            if row in seen:
                continue
            seen.add(row)
            if row not in cells:
                order.append(row)
                cells[row] = {}
                pages[row] = page
                NAMED[row] = county
            if name == first:
                NAMED[row] = county
            columns = _columns(head)
            cells[row][name] = {
                (year, candidate): values[index]
                for year, picked in columns.items()
                for candidate, index in picked.items()
            }
    printed: dict[tuple[str, int, str], int] = {}
    for key_ in {k for _, totals in reads.values() for k in totals}:
        votes = Counter(totals[key_] for _, totals in reads.values() if key_ in totals).most_common(1)[0]
        if votes[1] >= 2:
            printed[key_] = votes[0]
    settled: dict[tuple[str, str, int, str], int | None] = {}
    open_: dict[tuple[str, int, str], list[tuple[str, list[int | None]]]] = {}
    for row in order:
        if len(cells[row]) < 2:
            print(f"read once, left out: {row}")
            continue
        for cell in {k for read in cells[row].values() for k in read}:
            seen_ = [read[cell] for read in cells[row].values() if cell in read]
            value, count = Counter(seen_).most_common(1)[0]
            if count >= 2:
                settled[(*row, *cell)] = value
            else:
                open_.setdefault((row[0], *cell), []).append((row[1], list(dict.fromkeys(seen_))))
    for (state, year, candidate), loose in open_.items():
        total = printed.get((state, year, candidate))
        fixed = sum(v or 0 for (s, _, y, c), v in settled.items() if (s, y, c) == (state, year, candidate))
        hits = (
            [
                choice
                for choice in itertools.product(*(options for _, options in loose))
                if fixed + sum(v or 0 for v in choice) == total
            ]
            if total is not None and math.prod(len(options) for _, options in loose) <= 1 << 16
            else []
        )
        if len(hits) == 1:
            for (county, _), value in zip(loose, hits[0], strict=True):
                settled[(state, county, year, candidate)] = value
        else:
            for county, options in loose:
                print(f"unsettled {state} {year} {candidate} {NAMED[(state, county)]}: {options}")
    folder = raw.parsed(SOURCE) / "model"
    folder.mkdir(parents=True, exist_ok=True)
    for year, names in CANDIDATES.items():
        text: list[str] = []
        states = list(dict.fromkeys(state for state, _ in order))
        for state in states:
            rows = [row for row in order if row[0] == state]
            used = [c for c in names if any(settled.get((*row, year, c)) is not None for row in rows)]
            if not used:
                continue
            kept = [
                row
                for row in rows
                if all((*row, year, c) in settled for c in used)
                and any(settled[(*row, year, c)] is not None for c in used)
            ]
            for row in rows:
                if not all((*row, year, c) in settled for c in used):
                    print(f"{year} {state} {NAMED[row]}: left out, a cell is not settled")
            page = None
            for row in kept:
                if pages[row] != page:
                    page = pages[row]
                    text += [f"@{page}", f"# {state}", f"0 | {', '.join(used)}"]
                text.append(f"{NAMED[row]} | {' '.join(str(settled[(*row, year, c)] or 0) for c in used)}")
            sums = [sum(settled[(*row, year, c)] or 0 for row in kept) for c in used]
            given = [printed.get((state, year, c)) for c in used]
            if all(g is not None for g in given) and sums == given:
                text.append(f"Totals | {' '.join(str(g) for g in given)}")
            else:
                print(f"{year} {state}: county sums {sums}, printed totals {given}")
        for suffix in ("txt", "v2.txt"):
            (folder / f"{year}.{suffix}").write_text("\n".join(text) + "\n")


def parse() -> None:
    if not ensure():
        return
    build()
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
