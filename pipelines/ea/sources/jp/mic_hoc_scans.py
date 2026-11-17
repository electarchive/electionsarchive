import base64
import io
import json
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.sources.jp import mic, mic_hoc
from ea.store import DATA

SOURCE = "jp.mic_hoc"
JOB = "sangiin21"
PARSED = DATA / "parsed" / SOURCE
VERSION = "1"
RAW = mic.SOURCE
BASE = f"{mic.DATA_ROOT}/sangiin21"
MODEL = "claude-haiku-5-5"
PROMPT = "1"
READS = 2
LONGEST = 1568
DPI = 200
TILES = {
    "landscape": {"top": (0.03, 0.02, 0.97, 0.56), "bottom": (0.03, 0.44, 0.97, 0.98)},
    "portrait": {
        "top": (0.04, 0.02, 0.96, 0.38),
        "middle": (0.04, 0.32, 0.96, 0.68),
        "bottom": (0.04, 0.62, 0.96, 0.98),
    },
}
TEXT = {"type": "string"}
MAYBE_TEXT = {"type": ["string", "null"]}


def strict(properties: dict[str, object]) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


SCHEMA = strict(
    {
        "tables": {
            "type": "array",
            "items": strict(
                {
                    "party": MAYBE_TEXT,
                    "columns": {"type": "array", "items": TEXT},
                    "rows": {
                        "type": "array",
                        "items": strict({"label": TEXT, "cells": {"type": "array", "items": TEXT}}),
                    },
                }
            ),
        }
    }
)
SYSTEM = (
    "You read a crop of one scanned page of the official tables of the 21st House of Councillors regular election "
    "of 29 July 2007 (参議院議員通常選挙, 選挙期日 平成19年7月29日), published by Japan's Ministry of Internal Affairs and "
    "Communications. Each page holds ruled tables of votes by counting district (開票区) within one prefecture, in "
    "layouts that vary by prefecture, such as "
    "選挙区 候補者別得票数 (votes per prefectural-district candidate, headed by a candidate number, the name, and the "
    "party in parentheses), 開票区別投票総数 (valid, invalid, and total ballots, and voters), 比例代表 名簿届出政党等別"
    "得票数 (per party: 得票総数, 政党等の得票総数, and 名簿登載者の得票総数), or 名簿登載者の得票総数の開票区別一覧 "
    "(votes per list candidate of one party, the party named in a 政党等の名称 box above the table). Figures use "
    "commas as thousands separators and a period before three decimals, as in 1,102.000 or 4.666; keep every digit, "
    "comma, and period exactly as printed, and never confuse the two. Return one entry in tables per ruled table in "
    "the crop, top to bottom. For each table: party is the 政党等の名称 when one is printed above it, else null. "
    "columns has one string per numeric column, left to right, joining that column's header texts from top to bottom "
    "with ' / ' (for example '1 / 羽柴 秀吉 / 無所属' or '2 / 社会民主党 / 政党等の得票総数'); leave columns empty when "
    "the crop shows no header. rows has one entry per printed row, top to bottom, including subtotal rows such as "
    "市 計, 石狩支庁 計, 郡 計, and 合計: label is the row's leftmost text as printed, and cells holds every numeric "
    "column's figure left to right, with an empty string for a blank cell, so every row has as many cells as the "
    "table has numeric columns. Skip the 確定 column of asterisks, but keep the 開票率 column's text when present. "
    "Skip blank spacer rows and rows the crop cuts in half."
)


@dataclass(frozen=True)
class Document:
    prefecture: int
    url: str
    kind: str
    party: str


def documents() -> list[Document]:
    found = []
    for number in range(1, 48):
        page = f"{BASE}/sangiin21_3_14_{number}.html"
        raw.fetch(RAW, page, suffix=".html")
        for href, label in mic.anchors(page):
            if href.startswith("pdf/"):
                kind = "district" if label == "選挙区" else "party" if label == "政党別" else "list"
                found.append(Document(number, f"{BASE}/{href}", kind, "" if kind != "list" else label))
    return found


def fetch() -> None:
    for document in documents():
        raw.fetch(RAW, document.url, suffix=".pdf")


def count(path: Path) -> int:
    info = subprocess.run(["pdfinfo", path], capture_output=True, text=True, check=True).stdout
    match = re.search(r"Pages:\s+(\d+)", info)
    if match is None:
        raise ValueError(path)
    return int(match[1])


def stem(url: str) -> str:
    return url.rsplit("/", 1)[1].removesuffix(".pdf")


def tiles(url: str, page: int) -> dict[str, tuple[tuple[int, int, int, int], Image.Image]]:
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "page"
        path = raw.cached(RAW, url).path
        subprocess.run(
            ["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(DPI), "-gray", "-png", "-singlefile", path, out],
            check=True,
        )
        image = Image.open(out.with_suffix(".png")).convert("L")
        image.load()
    found = {}
    for tile, (left, top, right, bottom) in TILES["landscape" if image.width > image.height else "portrait"].items():
        box = (int(image.width * left), int(image.height * top), int(image.width * right), int(image.height * bottom))
        crop = image.crop(box)
        crop.thumbnail((LONGEST, LONGEST))
        found[tile] = (box, crop)
    return found


def read_key(url: str, page: int, tile: str, read: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/sangiin21/{stem(url)}/{page}/{tile}/{read}"


def read(url: str, page: int, tile: str, number: int) -> dict[str, Any]:
    message = json.loads(raw.cached(SOURCE, read_key(url, page, tile, number)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def request(crop: Image.Image, read: int) -> batch.Want:
    buffer = io.BytesIO()
    crop.quantize(4).save(buffer, "PNG", optimize=True)
    price_in, price_out = batch.PRICES[MODEL]
    params = MessageCreateParamsNonStreaming(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    ImageBlockParam(
                        type="image",
                        source={
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(buffer.getvalue()).decode(),
                        },
                    ),
                    TextBlockParam(
                        type="text",
                        text="Transcribe the tables in this crop."
                        if read == 1
                        else "Transcribe this crop, row by row.",
                    ),
                ],
            }
        ],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    return params, (crop.width * crop.height / 750 * price_in + 5000 * price_out) / 1e6


def pages(document: Document) -> int:
    return count(raw.cached(RAW, document.url).path)


def requests() -> dict[str, batch.Want]:
    """Every request for the volume: two reads of every band of every page."""
    todo = [(document, page) for document in documents() for page in range(1, pages(document) + 1)]

    def build(item: tuple[Document, int]) -> dict[str, batch.Want]:
        document, page = item
        return {
            read_key(document.url, page, tile, number): request(crop, number)
            for tile, (_, crop) in tiles(document.url, page).items()
            for number in range(1, READS + 1)
        }

    wanted: dict[str, batch.Want] = {}
    with ThreadPoolExecutor(4) as pool:
        for part in pool.map(build, todo):
            wanted.update(part)
    return wanted


def estimate(wanted: dict[str, batch.Want]) -> tuple[int, float]:
    return len(wanted), sum(cost for _, cost in wanted.values())


def approval(wanted: dict[str, batch.Want]) -> str:
    """The line that lets the batch run, with a 50% margin over the request estimate."""
    total, dollars = estimate(wanted)
    return f"ea batch approve {SOURCE}/{JOB} {max(1.0, round(dollars * 1.5)):.0f}   # {total} requests, estimate ${dollars:.2f}"


def ocr() -> None:
    """Send the requests through the batch. It stops before sending until the approval line has run."""
    wanted = requests()
    print(approval(wanted))
    print(batch.ensure(SOURCE, JOB, wanted)[1])


# ---------------------------------------------------------------------------------------------------------------
# Parsing: two reads of every band become one table per PDF, checked against its own subtotals and tied to the
# 結果調 totals.


SUBTOTAL = re.compile(r"計$")
TOP = re.compile(r"(合計|[都道府県]計)$")
SKIP_COLUMN = re.compile(r"率|時刻|確定|残票")
CANDIDATE = re.compile(r"^\s*\d+\s*(?:/|$)")
MAX_OVERLAP = 30
MAX_TRIES = 3


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\s☆★※◎○●*＊]+", "", text).replace("ケ", "ヶ")


def figure(text: str) -> Decimal | None:
    """A printed cell as a number; None for a blank, 0 for a dash."""
    text = unicodedata.normalize("NFKC", text).replace(",", "").replace(" ", "")
    if text in {"", "\u3000"}:
        return None
    if text in {"-", "－", "―", "ー", "|"}:
        return Decimal(0)
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


@dataclass
class Row:
    label: str
    seen: list[list[str]]


@dataclass
class Strip:
    columns: list[str]
    rows: list[Row]
    alt: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def strip_of(table: dict[str, Any]) -> Strip:
    return Strip(
        [str(column) for column in table["columns"]],
        [Row(norm(row["label"]), [list(row["cells"])]) for row in table["rows"] if norm(row["label"])],
    )


def labels(rows: list[Row]) -> list[str]:
    return [row.label for row in rows]


def overlap(top: Strip, bottom: Strip) -> tuple[int, int, int] | None:
    """Where the tail of the upper band and the head of the lower band show the same row labels."""
    above = labels(top.rows)[-MAX_OVERLAP:]
    below = labels(bottom.rows)[:MAX_OVERLAP]
    hit = SequenceMatcher(None, above, below, autojunk=False).find_longest_match(0, len(above), 0, len(below))
    if hit.size == 0 or hit.a + hit.size < len(above) - 1 or hit.b > 1:
        return None
    return len(top.rows) - len(above) + hit.a, hit.b, hit.size


def join(top: Strip, bottom: Strip) -> bool:
    """Append the lower band's table to the upper band's when it continues it; the shared rows keep both reads."""
    found = overlap(top, bottom)
    if found is None:
        if bottom.columns:
            return False
        top.notes.append("bands share no row label")
        top.rows += bottom.rows
        return True
    start, head, size = found
    for offset in range(size):
        top.rows[start + offset].seen += bottom.rows[head + offset].seen
    top.rows += bottom.rows[head + size :]
    if not top.columns:
        top.columns = bottom.columns
    return True


def stitch(bands: list[list[Strip]]) -> list[Strip]:
    """The bands of one page, top to bottom, as that page's tables."""
    out: list[Strip] = []
    for band in bands:
        for index, table in enumerate(band):
            if index == 0 and out and join(out[-1], table):
                continue
            out.append(table)
    return out


def similar(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return True
    return len(left) == len(right) and SequenceMatcher(None, "/".join(left), "/".join(right)).ratio() > 0.6


def combine(first: list[Strip], second: list[Strip]) -> list[Strip]:
    """Pair the tables of the two reads of a page and pool each row's observations by row label."""
    if len(first) != len(second):
        pairs = []
        left, right = list(first), list(second)
        for table in left:
            match = next((other for other in right if similar(table.columns, other.columns)), None)
            if match is not None:
                right.remove(match)
            pairs.append((table, match))
        pairs += [(other, None) for other in right]
    else:
        pairs = list(zip(first, second, strict=True))
    out = []
    for one, two in pairs:
        if two is None:
            out.append(one)
            continue
        if one.columns != two.columns:
            one.alt = two.columns
        blocks = SequenceMatcher(None, labels(one.rows), labels(two.rows), autojunk=False).get_opcodes()
        rows: list[Row] = []
        for tag, a0, a1, b0, b1 in blocks:
            if tag == "equal" or (tag == "replace" and a1 - a0 == b1 - b0):
                for x, y in zip(one.rows[a0:a1], two.rows[b0:b1], strict=True):
                    x.seen += y.seen
                    rows.append(x)
                if tag == "replace":
                    one.notes.append(f"row labels differ: {labels(one.rows[a0:a1])} against {labels(two.rows[b0:b1])}")
            else:
                rows += one.rows[a0:a1] + two.rows[b0:b1]
                one.notes.append(f"reads disagree on rows {labels(one.rows[a0:a1])} {labels(two.rows[b0:b1])}")
        one.rows = rows
        out.append(one)
    return out


def tables_of(held: set[str], url: str, page: int, number: int) -> list[Strip] | None:
    bands: list[list[Strip]] = []
    found = [tile for tile in ("top", "middle", "bottom") if read_key(url, page, tile, number) in held]
    if not found:
        return None
    for tile in found:
        bands.append([strip_of(table) for table in read(url, page, tile, number)["tables"]])
    return stitch(bands)


@dataclass
class Grid:
    """One PDF: every column strip of every page joined on the row labels."""

    columns: list[str] = field(default_factory=list)
    alt: dict[str, str] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    cells: list[dict[int, list[list[str]]]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def assemble(found: list[Strip]) -> Grid:
    """Tables with the same header continue each other downward. Tables with other headers over the same rows are
    further columns of the same rows."""
    groups: list[list[Strip]] = []
    for table in found:
        match = next((g for g in groups if similar(g[0].columns, table.columns)), None)
        if match is None:
            groups.append([table])
        else:
            match.append(table)
    grid = Grid()
    keys: dict[tuple[str, int], int] = {}
    for group in sorted(groups, key=lambda g: -sum(len(t.rows) for t in g)):
        seen: Counter[str] = Counter()
        base = len(grid.columns)
        head = group[0]
        for index, name in enumerate(head.columns or [f"#{len(grid.columns)}"]):
            name = name if name not in grid.columns else f"{name}#{base + index}"
            grid.columns.append(name)
            if head.alt:
                grid.alt[name] = head.alt[index] if index < len(head.alt) else ""
        for table in group:
            grid.notes += table.notes
            for row in table.rows:
                seen[row.label] += 1
                key = (row.label, seen[row.label])
                if key not in keys:
                    keys[key] = len(grid.labels)
                    grid.labels.append(row.label)
                    grid.cells.append({})
                grid.cells[keys[key]][base] = row.seen
    return grid


def observations(grid: Grid, row: int, column: int) -> list[str]:
    """Every read of one cell, from whichever strip holds its column."""
    start = max(base for base in grid.cells[row] if base <= column)
    out = []
    for cells in grid.cells[row][start]:
        if column - start < len(cells):
            out.append(cells[column - start])
    return out


def is_subtotal(label: str) -> bool:
    return bool(SUBTOTAL.search(label))


def tolerance(values: list[Decimal | None], size: int) -> Decimal:
    return Decimal("0.001") * size if any(v is not None and v != v.to_integral() for v in values) else Decimal(0)


def window(values: list[Decimal | None], order: list[int], target: Decimal, step: Decimal) -> bool:
    total = Decimal(0)
    for size, index in enumerate(order, 1):
        total += values[index] or Decimal(0)
        if abs(total - target) <= step * size:
            return True
    return False


def column_failures(names: list[str], values: list[Decimal | None]) -> list[int]:
    """Rows whose subtotal differs from the leaf rows it should add up from."""
    subtotal = [is_subtotal(name) for name in names]
    leaves = [i for i, flag in enumerate(subtotal) if not flag]
    step = tolerance(values, 1)
    failed = []
    for index, flag in enumerate(subtotal):
        target = values[index]
        if not flag or target is None or not leaves:
            continue
        if TOP.search(names[index]):
            total = sum((values[i] or Decimal(0) for i in leaves), Decimal(0))
            if abs(total - target) <= step * len(leaves):
                continue
        after = [i for i in leaves if i > index]
        before = [i for i in reversed(leaves) if i < index]
        if not (window(values, after, target, step) or window(values, before, target, step)):
            failed.append(index)
    return failed


def row_rules(columns: list[str]) -> list[tuple[int, list[int], str]]:
    """Identities a printed row must satisfy: a total column and the columns that add up to it."""
    rules: list[tuple[int, list[int], str]] = []
    found = {
        key: i for i, name in enumerate(columns) for key in ("有効投票数", "無効投票数", "投票総数") if key in name
    }
    if len(found) == 3:
        rules.append((found["投票総数"], [found["有効投票数"], found["無効投票数"]], "ballots"))
    candidates = [i for i, name in enumerate(columns) if CANDIDATE.match(name)]
    totals = [i for i, name in enumerate(columns) if norm(name).endswith("合計") and i not in candidates]
    if candidates and totals:
        rules.append((totals[0], candidates, "candidates"))
    heads: dict[str, dict[str, int]] = {}
    for i, name in enumerate(columns):
        head, _, tail = name.rpartition(" / ")
        if not head:
            continue
        part = (
            "list" if "名簿登載者" in tail else "party" if "政党等の" in tail else "total" if "得票総数" in tail else ""
        )
        if part:
            heads.setdefault(head, {})[part] = i
    for parts in heads.values():
        if len(parts) == 3:
            rules.append((parts["total"], [parts["party"], parts["list"]], "party"))
    return rules


def row_failed(values: list[Decimal | None], rule: tuple[int, list[int], str]) -> bool:
    target, parts, _ = rule
    printed = values[target]
    if printed is None:
        return False
    total = sum((values[i] or Decimal(0) for i in parts), Decimal(0))
    return abs(total - printed) > tolerance([*values], len(parts) + 1)


def reconcile(grid: Grid) -> tuple[list[list[Decimal | None]], list[dict[str, Any]]]:
    """Resolve every cell: take the value most reads agree on, then let the printed subtotals and row identities
    overrule it where the reads disagree. Cells the checks cannot settle go to review."""
    rows, width = len(grid.labels), len(grid.columns)
    options: dict[tuple[int, int], list[tuple[Decimal | None, int]]] = {}
    values: list[list[Decimal | None]] = [[None] * width for _ in range(rows)]
    for r in range(rows):
        for c in range(width):
            tally = Counter(figure(text) for text in observations(grid, r, c))
            ranked = tally.most_common()
            if ranked:
                values[r][c] = ranked[0][0]
            if len(ranked) > 1:
                options[r, c] = [(value, count) for value, count in ranked]
    rules = row_rules(grid.columns)
    skip = [bool(SKIP_COLUMN.search(name)) for name in grid.columns]

    def bad(r: int, c: int) -> int:
        count = sum(row_failed(values[r], rule) for rule in rules if c == rule[0] or c in rule[1])
        if not skip[c]:
            count += len(column_failures(grid.labels, [row[c] for row in values]))
        return count

    for _ in range(MAX_TRIES):
        moved = False
        for (r, c), choices in options.items():
            if skip[c]:
                continue
            scored = []
            for value, count in choices:
                values[r][c] = value
                scored.append((bad(r, c), -count, value))
            scored.sort(key=lambda item: item[:2])
            best = scored[0]
            if values[r][c] != best[2]:
                moved = True
            values[r][c] = best[2]
        if not moved:
            break
    review: list[dict[str, Any]] = []
    for r, name in enumerate(grid.labels):
        for rule in rules:
            if row_failed(values[r], rule):
                review.append({"row": name, "check": f"row {rule[2]}", "column": grid.columns[rule[0]]})
    for c, column in enumerate(grid.columns):
        if skip[c]:
            continue
        for r in column_failures(grid.labels, [row[c] for row in values]):
            review.append({"row": grid.labels[r], "check": "subtotal", "column": column})
        for (r, cc), choices in options.items():
            if cc == c and len({value for value, _ in choices}) > 1 and values[r][c] is None:
                review.append({"row": grid.labels[r], "check": "unread", "column": column})
    return values, review


def read_pdf(
    held: set[str], document: Document
) -> tuple[Grid, list[list[Decimal | None]], list[dict[str, Any]]] | None:
    """One PDF as a checked grid, or None when none of its pages has been read."""
    found: list[Strip] = []
    for page in range(1, pages(document) + 1):
        reads = [tables_of(held, document.url, page, number) for number in range(1, READS + 1)]
        have = [r for r in reads if r is not None]
        if not have:
            return None
        pair = have[0]
        for other in have[1:]:
            pair = combine(pair, other)
        found += pair
    grid = assemble(found)
    values, review = reconcile(grid)
    return grid, values, review


def totals_row(grid: Grid) -> int | None:
    return next((i for i, name in enumerate(grid.labels) if TOP.search(name)), None)


def person(text: str) -> str:
    head = text.split(" / ")
    return norm(head[1] if len(head) > 2 else head[-1])


def printed(book: str, names: set[str], votes: Decimal) -> str:
    """Whether the 結果調 prints these votes, and next to the candidate's name."""
    text = mic.plain(unicodedata.normalize("NFKC", book))
    found = False
    for form in mic.printed(votes):
        for hit in re.finditer(re.escape(form) + r"(?![\d.,])", text):
            found = True
            if any(name in text[max(0, hit.start() - 120) : hit.start()] for name in names):
                return "tied"
    return "votes_only" if found else "miss"


def tie(results: dict[str, tuple[Document, Grid, list[list[Decimal | None]]]]) -> dict[str, Any]:
    """Compare the prefecture totals of the scans with the national figures of the 結果調."""
    found = mic_hoc.listing(21)
    held = mic_hoc.ELECTIONS[21][0]
    parties, people = mic_hoc.national_lists(raw.cached(RAW, found.lists).path, held)
    book = mic_hoc.book_for(21)
    report: dict[str, Any] = {
        "candidates": {"tied": [], "votes_only": [], "miss": []},
        "parties": {},
        "people": {"tied": 0, "miss": []},
    }
    sums: dict[str, dict[str, Decimal]] = {}
    for key, (document, grid, values) in results.items():
        top = totals_row(grid)
        if top is None:
            continue
        for c, name in enumerate(grid.columns):
            value = values[top][c]
            if value is None:
                continue
            if document.kind == "district" and CANDIDATE.match(name):
                names = {person(name), person(grid.alt.get(name, ""))} - {""}
                report["candidates"][printed(book, names, value)].append([key, name, str(value)])
            elif document.kind == "party" and CANDIDATE.match(name):
                head, _, tail = name.rpartition(" / ")
                part = "candidate_votes" if "名簿登載者" in tail else "party_votes" if "政党等の" in tail else "total"
                sums.setdefault(mic_hoc.party_name(norm(head.split(" / ")[-1])), {}).setdefault(part, Decimal(0))
                sums[mic_hoc.party_name(norm(head.split(" / ")[-1]))][part] += value
            elif document.kind == "list" and CANDIDATE.match(name):
                sums.setdefault(f"{document.party}\x1f{person(name)}", {}).setdefault("votes", Decimal(0))
                sums[f"{document.party}\x1f{person(name)}"]["votes"] += value
    for record in parties:
        got = sums.get(record["party"], {})
        diff = {
            part: [str(got.get(part)), record[part]]
            for part in ("total", "party_votes", "candidate_votes")
            if part in record and got.get(part) != Decimal(record[part])
        }
        report["parties"][record["party"]] = diff or "tied"
    for row in people:
        got = sums.get(f"{mic_hoc.party_name(row['party'])}\x1f{norm(row['name'])}")
        if got is not None and got["votes"] == Decimal(row["votes"]):
            report["people"]["tied"] += 1
        else:
            report["people"]["miss"].append([row["party"], row["name"], row["votes"], str(got and got["votes"])])
    return report


def parse() -> Path:
    """Parse every cached read into per-unit tables and write the review list and the ties."""
    held = raw.keys(SOURCE)
    results: dict[str, tuple[Document, Grid, list[list[Decimal | None]]]] = {}
    rows: list[dict[str, str]] = []
    review: list[dict[str, Any]] = []
    unread = 0
    for document in documents():
        done = read_pdf(held, document)
        if done is None:
            unread += 1
            continue
        grid, values, flags = done
        name = stem(document.url)
        results[name] = (document, grid, values)
        review += [{"pdf": name, **flag} for flag in flags]
        review += [{"pdf": name, "check": "note", "row": note} for note in dict.fromkeys(grid.notes)]
        for r, label in enumerate(grid.labels):
            for c, column in enumerate(grid.columns):
                if values[r][c] is not None and not SKIP_COLUMN.search(column):
                    level = "total" if TOP.search(label) else "subtotal" if is_subtotal(label) else "leaf"
                    rows.append(
                        {
                            "prefecture": str(document.prefecture),
                            "kind": document.kind,
                            "party": document.party,
                            "unit": label,
                            "level": level,
                            "column": column,
                            "votes": str(values[r][c]),
                        }
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    import polars as pl

    pl.DataFrame(rows).write_parquet(PARSED / f"counting-v{VERSION}.parquet")
    ties = tie(results) if results else {}
    ties["unread_pdfs"] = unread
    (PARSED / f"review-v{VERSION}.json").write_text(json.dumps(review, ensure_ascii=False, indent=1))
    (PARSED / f"ties-v{VERSION}.json").write_text(json.dumps(ties, ensure_ascii=False, indent=1))
    return PARSED
