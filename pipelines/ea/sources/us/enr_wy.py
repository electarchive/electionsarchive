import html
import re
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from ea.sources.us.enr_2026 import DAY

HOST = "sos.wyo.gov"
PAGE = f"https://{HOST}/Elections/Docs/{DAY.year}/{DAY.year}GeneralResults.aspx"
SUMMARY = re.compile(r"Statewide_(?:\w+_)?Candidates_Summary\.pdf$", re.IGNORECASE)
COUNTIES = {
    "Albany",
    "Big Horn",
    "Campbell",
    "Carbon",
    "Converse",
    "Crook",
    "Fremont",
    "Goshen",
    "Hot Springs",
    "Johnson",
    "Laramie",
    "Lincoln",
    "Natrona",
    "Niobrara",
    "Park",
    "Platte",
    "Sheridan",
    "Sublette",
    "Sweetwater",
    "Teton",
    "Uinta",
    "Washakie",
    "Weston",
}
TRAILING = {"write-ins": True, "overvotes": False, "undervotes": False}
NUMBER = re.compile(r"-|\d{1,3}(?:,\d{3})*")
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
PAGE_TAG = re.compile(r"<page [^>]*>(.*?)</page>", re.DOTALL)
LEGISLATURE = re.compile(r"(Senate|House) District (\d+)")
PARTY = re.compile(r"\s*\(\s*([A-Z]{1,3})\s*\)$")


def collect(client: httpx.Client, get: Callable, store: Callable[[str, bytes, str], None]) -> None:
    listing = get(client, PAGE)
    if listing is None:
        print(f"WY: no results page at {PAGE}")
        return
    for href in sorted(
        set(re.findall(r"""href=["']([^"']+\.pdf)["']""", listing.decode(errors="replace"), re.IGNORECASE))
    ):
        url = urljoin(PAGE, html.unescape(href))
        if urlparse(url).hostname != HOST or not SUMMARY.search(url):
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


def contests(path: Path):
    xml = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    for page in PAGE_TAG.findall(xml):
        words = [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in WORD.findall(page)]
        rows = lines(words)
        dated = next((i for i, line in enumerate(rows) if " Election - " in " ".join(w[4] for w in line)), None)
        first = next(
            (
                i
                for i, line in enumerate(rows)
                if " ".join(w[4] for w in line if not NUMBER.fullmatch(w[4])) in COUNTIES
            ),
            None,
        )
        if dated is None or first is None or first < dated + 2:
            continue
        height = rows[first][0][3] - rows[first][0][1]
        heads = [line for line in rows[dated + 1 : first] if abs(line[0][3] - line[0][1] - height) > 0.05 * height]
        title = re.sub(r",\s*Continued$", "", " ".join(w[4] for w in heads[0])) if heads else ""
        party = " ".join(w[4] for w in heads[1]) if len(heads) > 1 else ""
        header = [
            w
            for line in rows[dated + 1 : first]
            if abs(line[0][3] - line[0][1] - height) <= 0.05 * height
            for w in line
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
        values: dict[str, list[int | None]] = {}
        for line in rows[first:]:
            label = " ".join(w[4] for w in line if not NUMBER.fullmatch(w[4]))
            if label not in COUNTIES:
                continue
            numbers = [w[4] for w in line if NUMBER.fullmatch(w[4])]
            if len(numbers) != len(names):
                print(f"WY: {path.name}: {title} {label} has {len(numbers)} values for {len(names)} columns; skipped")
                values = {}
                break
            values[label] = [None if n == "-" else int(n.replace(",", "")) for n in numbers]
        yield title, party, names, values


def rows(url: str, path: Path, urls: dict[str, Path]):
    if not url.endswith(".pdf"):
        return
    as_of = stamp(path)
    for title, party, names, values in contests(path):
        if party:
            continue
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
            else (None, "")
        )
        if office is None:
            continue
        for county, counts in values.items():
            if all(n is None for n in counts):
                continue
            for name, votes in zip(names, counts, strict=True):
                if name.lower() in TRAILING and not TRAILING[name.lower()]:
                    continue
                label = PARTY.search(name)
                yield {
                    "office": office,
                    "contest": "",
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
