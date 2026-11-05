import csv
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from ea.sources.us.enr_2026 import DAY
from ea.store import DATA

HOST = "sos.wyo.gov"
PAGE = f"https://{HOST}/Elections/Docs/{DAY.year}/{DAY.year}GeneralResults.aspx"
PRECINCTS = re.compile(r"/\d{4}_(\w+?)_County_General_PbP\.pdf$")
COUNTIES = 23
STATEWIDE = {"Secretary of State", "State Auditor", "State Treasurer", "Superintendent of Public Instruction"}
TRAILING = {"write-ins": True, "overvotes": False, "undervotes": False}
NUMBER = re.compile(r"-|\d{1,3}(?:,\d{3})*")
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', re.DOTALL)
PAGE_TAG = re.compile(r"<page [^>]*>(.*?)</page>", re.DOTALL)
LEGISLATURE = re.compile(r"(Senate|House) District (\d+)")
PARTY = re.compile(r"\s*\(\s*([A-Z]{1,3})\s*\)$")
OCR_CACHE = DATA / "parsed" / "us.enr_2026" / "wy_ocr"
OCR_DPI = 600
FOOTER = 585
WORD_GAP = 5
CAP_HEIGHT = 7
OUTLINE = re.compile(r'<g id="(glyph-[^"]+)">\s*<path d="([^"]*)"')
NUMERAL = re.compile(r"-?\d+(?:\.\d+)?")
PLACED = re.compile(r'<use xlink:href="#(glyph-[^"]+)" x="([\d.]+)" y="([\d.]+)"')


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    listing = get(client, PAGE)
    if listing is None:
        print(f"WY: no results page at {PAGE}")
        return
    for href in sorted(
        set(re.findall(r"""href=["']([^"']+\.pdf)["']""", listing.decode(errors="replace"), re.IGNORECASE))
    ):
        url = urljoin(PAGE, html.unescape(href))
        if urlparse(url).hostname != HOST or PRECINCTS.search(url) is None:
            continue
        content = get(client, url)
        if content is not None and content.startswith(b"%PDF"):
            store(url, content, ".pdf")


def stamp(path: Path) -> str | None:
    info = subprocess.run(["pdfinfo", "-isodates", str(path)], check=False, capture_output=True, text=True).stdout
    found = re.search(r"^CreationDate:\s+(\S+)", info, re.MULTILINE)
    if found is None:
        return None
    text = found[1].replace("Z", "+00:00")
    if re.search(r"[+-]\d\d$", text):
        text += ":00"
    try:
        created = datetime.fromisoformat(text)
    except ValueError:
        return None
    return created.isoformat() if created.tzinfo is not None and created.date() >= DAY else None


def lines(words: list[tuple[float, float, float, float, str]]) -> list[list[tuple[float, float, float, float, str]]]:
    found: list[list[tuple[float, float, float, float, str]]] = []
    for word in sorted(words, key=lambda w: (w[1], w[0])):
        if found and abs(found[-1][0][1] - word[1]) < 2:
            found[-1].append(word)
        else:
            found.append([word])
    return [sorted(line) for line in found]


def ocr(image: Path, mode: int) -> list[tuple[int, int, int, int, str]]:
    done = subprocess.run(
        ["tesseract", str(image), "stdout", "--psm", str(mode), "--dpi", str(OCR_DPI), "tsv"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    )
    return [
        (
            int(row["left"]),
            int(row["top"]),
            int(row["left"]) + int(row["width"]),
            int(row["top"]) + int(row["height"]),
            row["text"],
        )
        for row in csv.DictReader(done.stdout.splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE)
        if row["text"].strip()
    ]


def scanned(path: Path) -> list[dict[str, list[tuple[int, int, int, int, str]]]]:
    cached = OCR_CACHE / f"{hashlib.sha256(path.read_bytes()).hexdigest()}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(["pdftoppm", "-r", str(OCR_DPI), "-gray", "-png", str(path), f"{folder}/page"], check=True)
        pages = [{"sparse": ocr(image, 11), "block": ocr(image, 6)} for image in sorted(Path(folder).glob("page*.png"))]
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(pages))
    return pages


def ocr_lines(words: list[tuple[int, int, int, int, str]]) -> list[list[tuple[int, int, int, int, str]]]:
    found: list[list[tuple[int, int, int, int, str]]] = []
    for word in sorted(words, key=lambda w: w[1] + w[3]):
        if found and abs(found[-1][0][1] + found[-1][0][3] - word[1] - word[3]) < OCR_DPI / 8:
            found[-1].append(word)
        else:
            found.append([word])
    return [sorted(line) for line in found]


def outlines(path: Path, number: int, letters: dict[str, str]) -> list[tuple[float, float, float, float, str]]:
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "page.svg"
        subprocess.run(["pdftocairo", "-svg", "-f", str(number), "-l", str(number), str(path), str(target)], check=True)
        svg = target.read_text()
    shapes = {key: [float(n) for n in NUMERAL.findall(body)] for key, body in OUTLINE.findall(svg)}
    letter = {key: letters.setdefault(body, chr(0xE000 + len(letters))) for key, body in OUTLINE.findall(svg)}
    placed = sorted(
        ((float(y), float(x), key) for key, x, y in PLACED.findall(svg) if shapes.get(key)),
    )
    rows: list[list[tuple[float, float, str]]] = []
    for glyph in placed:
        if rows and glyph[0] - rows[-1][0][0] < 1:
            rows[-1].append(glyph)
        else:
            rows.append([glyph])
    words: list[tuple[float, float, float, float, str]] = []
    for row in rows:
        y = row[0][0]
        fresh = True
        for _, x, key in sorted(row, key=lambda glyph: glyph[1]):
            left = x + min(shapes[key][::2])
            right = x + max(shapes[key][::2])
            if not fresh and left - words[-1][2] < WORD_GAP:
                first = words[-1]
                words[-1] = (first[0], first[1], right, y, first[4] + letter[key])
            else:
                words.append((left, y - CAP_HEIGHT, right, y, letter[key]))
            fresh = False
    return words


def scanned_tables(
    words: list[tuple[float, float, float, float, str]], page: dict[str, list[tuple[int, int, int, int, str]]]
):
    scale = 72 / OCR_DPI
    head = ocr_lines(page["sparse"])
    marked = next((i for i, line in enumerate(head) if "General Election" in " ".join(w[4] for w in line)), None)
    start = next((i for i, line in enumerate(head) if line[0][4] == "Precinct"), None)
    total = next((w for w in page["sparse"] + page["block"] if w[4] == "Total"), None)
    if marked is None or start is None or not marked + 1 < start:
        return None
    if " ".join(w[4] for w in head[marked + 1]) == "UNOFFICIAL RESULTS":
        marked += 1
    top = head[start][0][3] * scale
    bottom = (total[1] + total[3]) / 2 * scale + 6 if total else FOOTER
    rows = lines([word for word in words if top <= (word[1] + word[3]) / 2 < bottom])
    width = Counter(len(line) for line in rows).most_common(1)[0][0] if rows else 0
    if total and rows:
        rows[-1] = [rows[-1][0], *rows[-1][-(width - 1) :]]
    rows = [line for line in rows if len(line) == width]
    votes: dict[str, Counter[str]] = {}
    for word in page["sparse"] + page["block"]:
        x, y = (word[0] + word[2]) / 2 * scale, (word[1] + word[3]) / 2 * scale
        cell = next((c for line in rows for c in line if c[0] <= x <= c[2] and c[1] <= y <= c[3]), None)
        if cell is not None and len(cell[4]) == len(word[4]) and re.fullmatch(r"[\d,-]+", word[4]):
            for coded, read in zip(cell[4], word[4], strict=True):
                votes.setdefault(coded, Counter())[read] += 1
    names: list[list[str]] = [[] for _ in rows[-1][1:]] if rows else []
    edges = [cell[2] / scale for cell in rows[-1][1:]] if rows else []
    for line in head[marked + 2 : start + 1]:
        for word in line:
            column = next((i for i, edge in enumerate(edges) if (word[0] + word[2]) / 2 <= edge), None)
            if word[4] != "Precinct" and column is not None:
                names[column].append(word[4])
    return {
        "title": " ".join(w[4] for w in head[marked + 1]),
        "names": [" ".join(name).replace("(0)", "(D)") for name in names],
        "rows": [[cell[4] for cell in line[1:]] for line in rows],
        "votes": votes,
        "total": total is not None,
    }


def scanned_contests(path: Path):
    letters: dict[str, str] = {}
    tables = [
        table
        for number, page in enumerate(scanned(path), 1)
        if (table := scanned_tables(outlines(path, number, letters), page)) is not None
    ]
    pooled: dict[str, Counter[str]] = {}
    for table in tables:
        for coded, count in table["votes"].items():
            pooled.setdefault(coded, Counter()).update(count)
    carried: list[list[int]] = []
    for table in tables:
        digits = {coded: count.most_common(1)[0][0] for coded, count in pooled.items()}
        decoded = [["".join(digits.get(c, "?") for c in cell) for cell in line] for line in table["rows"]]
        if not decoded or not all(NUMBER.fullmatch(n) for line in decoded for n in line):
            print(f"WY: {path.name}: {table['title']} has unreadable digits after OCR; skipped")
            carried = []
            continue
        numbers = carried + [[0 if n == "-" else int(n.replace(",", "")) for n in line] for line in decoded]
        if not table["total"]:
            carried = numbers
            continue
        carried = []
        columns = list(zip(*numbers, strict=True))
        if any(sum(column[:-1]) != column[-1] for column in columns):
            print(f"WY: {path.name}: {table['title']} does not add up after OCR; skipped")
            continue
        yield table["title"], table["names"], [column[-1] for column in columns]


def contests(path: Path):
    done = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        print(f"WY: {path.name}: pdftotext exited {done.returncode}; skipped")
        return
    if "UNOFFICIAL" not in done.stdout.split("</head>")[-1]:
        yield from scanned_contests(path)
        return
    for page in PAGE_TAG.findall(done.stdout):
        words = [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in WORD.findall(page)]
        rows = lines(words)
        marked = next((i for i, line in enumerate(rows) if " ".join(w[4] for w in line) == "UNOFFICIAL RESULTS"), None)
        start = next((i + 1 for i, line in enumerate(rows) if line[0][4] == "Precinct"), None)
        total = next((line for line in rows if line[0][4] == "Total"), None)
        if marked is None or start is None or total is None or start <= marked:
            continue
        height = total[0][3] - total[0][1]
        heads = [line for line in rows[marked + 1 : start] if abs(line[0][3] - line[0][1] - height) > 0.05 * height]
        title = " ".join(w[4] for w in heads[0]) if heads else ""
        header = [
            w
            for line in rows[marked + 1 : start]
            if abs(line[0][3] - line[0][1] - height) <= 0.05 * height
            for w in line
            if w[4] != "Precinct"
        ]
        trailing = [w for w in header if w[4].lower() in TRAILING]
        segments: list[dict] = []
        for line in lines([w for w in header if w[4].lower() not in TRAILING]):
            for w in line:
                if segments and segments[-1]["top"] == line[0][1] and w[0] - segments[-1]["right"] < 8:
                    segments[-1].update(right=w[2], text=f"{segments[-1]['text']} {w[4]}")
                else:
                    segments.append({"left": w[0], "right": w[2], "text": w[4], "top": line[0][1]})
        columns: list[dict] = []
        for segment in segments:
            column = next(
                (
                    c
                    for c in columns
                    if c["top"] != segment["top"] and segment["left"] < c["right"] and segment["right"] > c["left"]
                ),
                None,
            )
            if column is None:
                columns.append(dict(segment))
            else:
                column.update(
                    left=min(column["left"], segment["left"]),
                    right=max(column["right"], segment["right"]),
                    text=f"{column['text']} {segment['text']}",
                    top=segment["top"],
                )
        names = [c["text"] for c in sorted(columns, key=lambda c: c["left"])] + [w[4] for w in sorted(trailing)]
        numbers = [w[4] for w in total[1:]]
        if len(numbers) != len(names) or not all(NUMBER.fullmatch(n) for n in numbers):
            print(f"WY: {path.name}: {title} has {len(numbers)} totals for {len(names)} columns; skipped")
            continue
        yield title, names, [None if n == "-" else int(n.replace(",", "")) for n in numbers]


def rows(url: str, path: Path, urls: dict[str, Path]):
    found = PRECINCTS.search(url)
    if found is None:
        return
    county = found[1].replace("_", " ")
    complete = sum(1 for other in urls if PRECINCTS.search(other)) == COUNTIES
    as_of = stamp(path)
    for title, names, counts in contests(path):
        seat = LEGISLATURE.fullmatch(title)
        office, district = (
            ("senate", "")
            if title == "United States Senator"
            else ("house", "0")
            if title == "United States Representative"
            else ("governor", "")
            if title == "Governor"
            else (seat[1].lower(), f"{seat[2]}:g")
            if seat
            else ("state", "")
            if title in STATEWIDE
            else (None, "")
        )
        if office is None or (not seat and not complete) or all(n is None for n in counts):
            continue
        for name, votes in zip(names, counts, strict=True):
            if name.lower() in TRAILING and not TRAILING[name.lower()]:
                continue
            label = PARTY.search(name)
            yield {
                "office": office,
                "contest": title if office == "state" else "",
                "district": district,
                "party": label[1] if label else None,
                "county": county,
                "candidate": PARTY.sub("", name),
                "write_in": name.lower() == "write-ins",
                "votes": votes or 0,
                "as_of": as_of,
                "reporting": 0,
                "total": 0,
            }
