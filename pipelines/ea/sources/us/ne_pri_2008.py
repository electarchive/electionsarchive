import re
import subprocess
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ne_primaries import COUNTIES
from ea.store import DATA, connect

SOURCE = "us.ne_pri_2008"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://sos.nebraska.gov/sites/default/files/doc/"
FILES = {
    date(2008, 5, 13): "elections/2008/2008%20primary%20official%20results.pdf",
    date(2010, 5, 11): "elections/2010/Canvas%20PR10%20After%20Recount%20Final.pdf",
}
HOUSE_PAGES = (28, 31)
STATEWIDE_PAGES = (9, 26)
PUBLISHER = "Nebraska Secretary of State"
TITLE = "Primary election canvass"
DISTRICTS = {"One": 1, "Two": 2, "Three": 3}
NUMBER = re.compile(r"\d[\d,]*$")
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
NAMES = {c.lower(): c for c in COUNTIES}
PREFIXES = {" ".join(c.lower().split()[: i + 1]) for c in COUNTIES for i in range(len(c.split()) - 1)}


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def number(text: str) -> int:
    return int(text.replace(",", ""))


def house_2008(pdf: Path) -> dict[tuple, dict[str, dict[str, int]]]:
    tallies: dict[tuple, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    party = ""
    for page in range(HOUSE_PAGES[0], HOUSE_PAGES[1] + 1):
        html = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-bbox", str(pdf), "-"], capture_output=True, text=True, check=True
        ).stdout
        words = [(float(a), float(b), float(c), float(d), t) for a, b, c, d, t in WORD.findall(html)]
        words.sort(key=lambda w: (round((w[1] + w[3]) / 2), w[0]))
        top = " ".join(w[4] for w in words if w[1] < 95)
        if "Republican" in top:
            party = "republican"
        elif "Democratic" in top:
            party = "democratic"
        labels, numbers, districts, headers = [], [], [], []
        index = 0
        while index < len(words):
            x0, y0, x1, y1, text = words[index]
            yc = (y0 + y1) / 2
            if text == "District" and index + 1 < len(words) and words[index + 1][4] in DISTRICTS:
                districts.append((x0, yc, DISTRICTS[words[index + 1][4]]))
                index += 2
            elif text == "County":
                line = [w for w in words if abs((w[1] + w[3]) / 2 - yc) < 3 and w[0] > x1]
                if x0 < 300:
                    line = [w for w in line if w[0] < 300]
                else:
                    line = [w for w in line if w[0] >= 300]
                line.sort()
                names, last = [], None
                for w in line:
                    if last is not None and w[0] - last < 6:
                        names[-1] += " " + w[4]
                    else:
                        names.append(w[4])
                    last = w[2]
                headers.append((x0, yc, names))
                index += 1
            elif NUMBER.match(text):
                numbers.append((x0, x1, yc, number(text)))
                index += 1
            else:
                joined = text.lower()
                step = 1
                if index + 1 < len(words) and abs((words[index + 1][1] + words[index + 1][3]) / 2 - yc) < 3:
                    if f"{joined} {words[index + 1][4].lower()}" in NAMES or f"{joined} {words[index + 1][4].lower()}" in PREFIXES:
                        joined, step = f"{joined} {words[index + 1][4].lower()}", 2
                if joined not in NAMES and joined in PREFIXES:
                    joined = next(c.lower() for c in COUNTIES if c.lower().startswith(joined + " "))
                if joined in NAMES:
                    labels.append((x0, yc, NAMES[joined]))
                elif joined == "total":
                    labels.append((x0, yc, "TOTAL"))
                index += step
        left_districts = sorted((d for d in districts if d[0] < 300), key=lambda d: d[1])
        left_headers = sorted((h for h in headers if h[0] < 300), key=lambda h: h[1])
        for label in labels:
            if label[2] == "TOTAL":
                continue
            side = label[0] < 300
            row = sorted((n for n in numbers if abs(n[2] - label[1]) < 4 and n[0] > label[0] and ((n[0] < 300) == side or not side)), key=lambda n: n[0])
            if not side:
                row = [n for n in row if n[0] > label[0]]
            else:
                row = [n for n in row if n[0] < 300]
            above = [d for d in left_districts if d[1] <= label[1]]
            if not side:
                district = left_districts[-1][2]
                header = left_headers[-1][2]
            else:
                district = above[-1][2]
                header = [h for h in left_headers if h[1] <= label[1]][-1][2]
            if len(row) != len(header):
                print(f"page {page} {label[2]} d{district}: {len(row)} numbers for {header}")
                continue
            for name, n in zip(header, row, strict=True):
                tallies[("house", district, party)][name][label[2]] += n[3]
    return tallies


def statewide_2008(pdf: Path) -> tuple[dict[tuple, dict[str, dict[str, int]]], dict[tuple, dict[str, int]]]:
    tallies: dict[tuple, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    totals: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    chamber, party, names = "", "", []
    for page in range(STATEWIDE_PAGES[0], STATEWIDE_PAGES[1] + 1):
        html = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-bbox", str(pdf), "-"], capture_output=True, text=True, check=True
        ).stdout
        words = [(float(a), float(b), float(c), float(d), t) for a, b, c, d, t in WORD.findall(html)]
        words.sort(key=lambda w: (round((w[1] + w[3]) / 2 / 3), w[0]))
        lines: list[list[tuple[float, float, float, float, str]]] = []
        for word in words:
            yc = (word[1] + word[3]) / 2
            if lines and abs(yc - (lines[-1][0][1] + lines[-1][0][3]) / 2) < 3:
                lines[-1].append(word)
            else:
                lines.append([word])
        for line in lines:
            line.sort()
        split = 300 if any(w[0] >= 300 and (w[4].lower() in NAMES or w[4].lower() in PREFIXES) for w in words) else 10**6
        heading = next((i for i, line in enumerate(lines) if any(w[4] == "County" and w[0] < split for w in line)), len(lines))
        top = " ".join(w[4] for line in lines[:heading] for w in line)
        if "President" in top:
            chamber = "president"
        elif "Senate" in top:
            chamber = "senate"
        if found := re.search(r"(Republican|Democratic|Nebraska|Green) Party Nomination", top):
            party = found[1].lower()
        elif found := re.search(r"Nonpartisan (Republican|Democratic|Nebraska|Green) Voters", top):
            party = found[1].lower()
        if heading < len(lines):
            line = lines[heading]
            left = [w for w in line if w[0] < split and w[4] != "County"]
            total = next(entry for entry in lines[heading + 1 :] if any(w[4] == "TOTAL" for w in entry))
            edges = [w[2] for w in total if w[0] < split and NUMBER.match(w[4])]
            names = [""] * len(edges)
            for w in left:
                column = next(k for k, edge in enumerate(edges) if w[2] <= edge + 3)
                names[column] = f"{names[column]} {w[4]}".strip()
            lines[heading] = [w for w in line if w[0] >= split]
        for line in lines[heading:] if heading < len(lines) else lines:
            index = 0
            label, row = None, []

            def close() -> None:
                if label is None:
                    return
                if len(row) != len(names):
                    print(f"page {page} {label}: {len(row)} numbers for {names}")
                    return
                if label == "TOTAL":
                    for name, n in zip(names, row, strict=True):
                        totals[(chamber, party)][name] += n
                else:
                    for name, n in zip(names, row, strict=True):
                        tallies[(chamber, party)][name][label] += n

            while index < len(line):
                text = line[index][4]
                if NUMBER.match(text):
                    row.append(number(text))
                    index += 1
                    continue
                matched = None
                for width in (3, 2, 1):
                    joined = " ".join(w[4] for w in line[index : index + width]).lower()
                    if len(line[index : index + width]) == width and (joined in NAMES or joined == "total"):
                        matched = (joined, width)
                        break
                if matched is None:
                    index += 1
                    continue
                close()
                label, row = ("TOTAL" if matched[0] == "total" else NAMES[matched[0]]), []
                index += matched[1]
            close()
    return tallies, totals


def house_2010(pdf: Path) -> dict[tuple, dict[str, dict[str, int]]]:
    tallies: dict[tuple, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    lines = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Member of the U.S. House of Representatives"))
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "Governor")
    party, district, names = "", None, []
    cell = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(map(re.escape, COUNTIES), key=len, reverse=True)) + r")\s+(\d[\d,]*(?:[ \t]+\d[\d,]*)*)")
    for position in range(start, end):
        line = lines[position]
        if found := re.match(r"\s*(Republican|Democratic) Party Nomination", line):
            party = found[1].lower()
        elif found := re.match(r"\s*District (One|Two|Three)", line):
            district = DISTRICTS[found[1]]
        elif re.match(r"\s+(Republican|Democrat)\s+Nonpartisan", line):
            head = lines[position - 1]
            if split := cell.search(head):
                head = head[: split.start()]
            names = [n for n in re.split(r"\s{2,}", head.strip()) if n]
            for found in [*cell.finditer(lines[position - 1]), *cell.finditer(line)]:
                numbers = [number(v) for v in found[2].split()]
                for k, name in enumerate(names):
                    tallies[("house", district, party)][name][found[1]] += numbers[3 * k + 2]
        elif names and not re.match(r"\s+(Republican|Democrat)\s+Nonpartisan", lines[position + 1]):
            for found in cell.finditer(line):
                numbers = [number(v) for v in found[2].split()]
                if len(numbers) != 3 * len(names):
                    print(f"{district} {party} {found[1]}: {len(numbers)} numbers for {names}")
                    continue
                for k, name in enumerate(names):
                    tallies[("house", district, party)][name][found[1]] += numbers[3 * k + 2]
    return tallies


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    rows = []
    for day, path in FILES.items():
        pdf = raw.cached(SOURCE, BASE + path).path
        tallies = house_2008(pdf) if day.year == 2008 else house_2010(pdf)
        if day.year == 2008:
            for (chamber, party), by_name in statewide_2008(pdf)[0].items():
                tallies[(chamber, None, party)] = by_name
        for (chamber, district, party), by_name in tallies.items():
            for name, by_county in by_name.items():
                for county, votes in by_county.items():
                    code = codes[re.sub(r"[^a-z]", "", county.lower())]
                    rows.append((day.year, f"{chamber}:{party}", day, False, "NE", district, code, name.upper(), 1, False, votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "day": pl.Date,
                "special": pl.Boolean,
                "state": pl.String,
                "district": pl.Int16,
                "code": pl.String,
                "candidate": pl.String,
                "measure": pl.Int16,
                "writein": pl.Boolean,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(kind=pl.lit("county"), source=pl.lit("NE"))
        .write_parquet(PARSED / "counties.parquet")
    )
    print(len(rows), "candidate rows")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska's 2008 Senate and presidential primaries by county",
            polity="US-NE",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.nebraska.gov/elections/election-results",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").filter(pl.col("chamber").str.starts_with("house").not_()),
            {"NE": source},
            strict=True,
        )
        return cs.write()
