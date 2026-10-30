import collections
import difflib
import itertools
import re
import struct
import subprocess
import tempfile
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw, scan
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ny_pri_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
OCR = PARSED / "ocr"
DOWNLOAD = DATA / "inbox" / "manual" / "MF-008" / "2000-09-12_Primary.pdf"
URL = "https://www.elections.ny.gov/2000ElectionResults.html"
DAY = date(2000, 9, 12)
PAGE_COUNT = 11
PUBLISHER = "New York State Board of Elections"
TITLE = "September 12, 2000 primary results"
NUMBER = re.compile(r"^\d[\d,]*$")
BLOCK = re.compile(r"^(?:Congress|Senate|Assembly)$")
HOUSE = re.compile(r"^(\d+)-(Dem|Rep)")
PARTIES = {
    "Dem": "democratic",
    "Rep": "republican",
    "Indep": "independence",
    "Rtl": "right-to-life",
    "Green": "green",
}
MINOR = {"INDEPENDENCE": "Indep", "RIGHT TO LIFE": "Rtl", "GREEN": "Green"}


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(
            SOURCE,
            URL,
            DOWNLOAD.read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime(2026, 10, 21, tzinfo=UTC),
            media_type="application/pdf",
        )


def read(pdf: Path) -> list[dict]:
    blocks: list[dict] = []
    for number in range(1, PAGE_COUNT + 1):
        words = scan.words(pdf, number, OCR / f"{number}.tsv")
        slope = scan.skew(words)
        block = None
        title = ""
        for line in scan.lines(words, slope, 14):
            texts = [text for text, _, _, _ in line]
            joined = " ".join(texts)
            if re.search(r"PRIMARY", joined) and re.search(r"(DEMOCRATIC|REPUBLICAN)", joined):
                title = "Dem" if "DEMOCRATIC" in joined else "Rep"
                continue
            if number <= 5 and re.search(r"PRIMARY", joined) and (minor := re.search("|".join(MINOR), joined)):
                title = MINOR[minor[0]]
                continue
            if re.fullmatch(r"U\.?S\.?\s+SENATOR", joined):
                block = {"office": "senate", "district": None, "party": title, "page": number, "lines": []}
                blocks.append(block)
                continue
            if len(texts) >= 2 and BLOCK.match(texts[0]):
                block = None
                if texts[0] == "Congress" and (found := HOUSE.match(texts[1])):
                    block = {
                        "office": "house",
                        "district": int(found.group(1)),
                        "party": found.group(2),
                        "page": number,
                        "lines": [],
                    }
                    blocks.append(block)
                continue
            if block is not None:
                block["lines"].append(line)
    return blocks


def columns(lines: list[list[scan.Word]]) -> tuple[list[float], list[str], int] | None:
    start = next((i for i, line in enumerate(lines) if any(w[0].startswith("Candidate") for w in line)), None)
    if start is None:
        return None
    anchors = [(w[1][0] + w[1][2]) / 2 for w in lines[start] if w[0].startswith(("Candidate", "Scatter"))]
    first = next(
        (
            i
            for i in range(start + 1, len(lines))
            if any(NUMBER.match(w[0]) and w[1][0] >= anchors[0] - 100 for w in lines[i])
        ),
        None,
    )
    if first is None or first == start + 1:
        return None
    names = [""] * len(anchors)
    for text, box, _, _ in lines[first - 1]:
        if re.fullmatch(r"[A-Za-z][A-Za-z'’-]+", text) and len(text) > 2:
            centre = (box[0] + box[2]) / 2
            names[min(range(len(anchors)), key=lambda i: abs(anchors[i] - centre))] = text.upper()
    return anchors, names, first


def settle(
    image: Path, anchor: float, rows: list[dict], column: int
) -> list[tuple[int, tuple[int, int, int, int] | None]] | None:
    seen: list[tuple[int, tuple[int, int, int, int]] | None] = []
    for row in rows:
        words = row["values"].get(column, [])
        digits = re.sub(r"\D", "", words[0][0]) if len(words) == 1 else ""
        seen.append((int(digits), words[0][1]) if NUMBER.match(words[0][0] if words else "") and digits else None)
    spans = [
        (
            min(w[1][1] for w in row["values"].get(column, [w for ws in row["values"].values() for w in ws])),
            max(w[1][3] for w in row["values"].get(column, [w for ws in row["values"].values() for w in ws])),
        )
        for row in rows
    ]
    margins = {
        margin: scan.read(
            image,
            [((int(anchor) - 130, top - margin, int(anchor) + 130, bottom + margin), True) for top, bottom in spans],
            "eng",
        )
        for margin in (6, 2, 0)
    }
    options = []
    for index in range(len(rows)):
        votes = collections.Counter(
            value
            for margin, read in margins.items()
            if margin or index == len(rows) - 1
            for text in read[index]
            for value, weight in scan.numbers(text).items()
            if weight == 2
        )
        known = {seen[index][0]} if seen[index] is not None else set()
        floor = max((votes[value] for value in known), default=1)
        options.append(
            sorted(known | {value for value, count in votes.items() if count > floor})
            or ([0] if index == len(rows) - 1 else [])
        )
    if any(not option for option in options):
        print("   column", column, "empty", [rows[i]["name"] for i, o in enumerate(options) if not o])
        return None
    varying = [i for i, option in enumerate(options) if len(option) > 1]
    if len(varying) > 10:
        print("   column", column, "many varying", [(rows[i]["name"], options[i]) for i in varying])
        return None
    solutions = []
    for combo in itertools.product(*(options[i] for i in varying)):
        pick = [option[0] for option in options]
        for i, value in zip(varying, combo, strict=True):
            pick[i] = value
        if sum(pick[:-1]) == pick[-1]:
            solutions.append(pick)
    if len(solutions) != 1:
        print(
            "   column",
            column,
            "solutions",
            len(solutions),
            "varying",
            [(rows[i]["name"], options[i]) for i in varying],
        )
        return None
    return [
        (
            value,
            seen[index][1]
            if seen[index] is not None and seen[index][0] == value
            else (int(anchor) - 40, spans[index][0], int(anchor) + 40, spans[index][1]),
        )
        if value or seen[index] is not None
        else (0, None)
        for index, value in enumerate(solutions[0][:-1])
    ]


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '36%%'"""
            ).fetchall()
        }
    OCR.mkdir(parents=True, exist_ok=True)
    out = []
    for block in read(raw.cached(SOURCE, URL).path):
        label = f"{block['office']} {block['district']} {block['party']}"
        if block["party"] not in PARTIES:
            continue
        found = columns(block["lines"])
        if found is None:
            print(f"{label}: no column header")
            continue
        anchors, names, first = found
        scattered = any(w[0].startswith("Scatter") for line in block["lines"][:first] for w in line)
        scatter = len(anchors) - int(scattered)
        if not all(names[:scatter]):
            print(f"{label}: a candidate name is missing {names}")
            continue
        rows = []
        pending = None
        for line in block["lines"][first:]:
            cells = [w for w in line if w[1][0] >= anchors[0] - 100 and re.search(r"\d", w[0])]
            text = " ".join(w[0] for w in line if w[1][0] < anchors[0] - 100 and not NUMBER.match(w[0]))
            text = re.sub(r"[^A-Za-z. ]", "", text)
            name = re.sub(r"[^a-z]", "", re.sub(r"(?i)^part of ", "", text).lower())
            if cells:
                values: dict[int, list[scan.Word]] = {}
                for word in cells:
                    column = min(range(len(anchors)), key=lambda i: abs(anchors[i] - (word[1][0] + word[1][2]) / 2))
                    values.setdefault(column, []).append(word)
                row = {"name": name, "values": values, "span": (min(w[1][1] for w in line), max(w[1][3] for w in line))}
                if not name:
                    pending = row
                rows.append(row)
            elif pending is not None and name:
                pending["name"] = name
                pending = None
        isTotal = lambda row: row["name"].startswith(("total", "statewide"))
        grand = next(
            (row for row in reversed(rows) if isTotal(row) and row["name"] not in ("totaloutsidenyc", "totalnyc")), None
        )
        body = [row for row in rows if not isTotal(row)]
        if grand is None or not body:
            print(f"{label}: no total row")
            continue
        chosen: dict[int, list[tuple[int, tuple[int, int, int, int] | None]]] = {}
        with tempfile.TemporaryDirectory() as folder:
            image = scan.rendered(raw.cached(SOURCE, URL).path, block["page"], Path(folder))
            for column, anchor in enumerate(anchors):
                picks = settle(image, anchor, [*body, grand], column)
                if picks is not None:
                    chosen[column] = picks
        if len(chosen) < scatter or not all(column in chosen for column in range(scatter)):
            print(f"{label}: a candidate's column does not add to its total")
            continue
        for column, picks in chosen.items():
            for row, (value, box) in zip(body, picks, strict=False):
                if box is None:
                    continue
                key = row["name"]
                if key not in counties:
                    near = difflib.get_close_matches(key, list(counties), 1, 0.75)
                    if not near:
                        raise ValueError(f"{label}: no county {key}")
                    key = near[0]
                out.append(
                    {
                        "year": 2000,
                        "chamber": f"{block['office']}:{PARTIES[block['party']]}",
                        "day": DAY,
                        "special": False,
                        "state": "NY",
                        "district": block["district"],
                        "code": counties[key],
                        "candidate": "WRITE-INS" if scattered and column == scatter else names[column],
                        "measure": 1,
                        "writein": scattered and column == scatter,
                        "votes": value,
                        "kind": "county",
                        "source": "NY",
                        "file": 0,
                        "page": block["page"],
                        "x0": box[0],
                        "y0": box[1],
                        "x1": box[2],
                        "y1": box[3],
                    }
                )
        print(f"{label}: {len(body)} counties, loaded")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(out, infer_schema_length=None).with_columns(
        year=pl.col("year").cast(pl.Int32),
        district=pl.col("district").cast(pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's September 12, 2000 U.S. Senate and House primaries by county, read from a scanned report; the file was saved from the Board of Elections' site by a person in a browser",
            polity="US-NY",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.elections.ny.gov/2000ElectionResults.html",
            license="Public record",
        )
        pdf = raw.cached(SOURCE, URL)
        artifact = cs.artifact(pdf, source)
        pages: dict[int, str] = {}
        sizes: dict[int, tuple[int, int]] = {}
        for number in sorted(frame["page"].unique().to_list()):
            image = PAGES / f"{pdf.sha256}-{number}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(pdf.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            page = cs.page(artifact, number)
            pages[number] = str(page)
            sizes[number] = (width, height)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        frame = frame.with_columns(
            page_id=pl.col("page").replace_strict(pages, return_dtype=pl.String),
            **{
                edge: pl.col(edge) / pl.col("page").replace_strict({k: v[axis] for k, v in sizes.items()})
                for edge, axis in (("x0", 0), ("y0", 1), ("x1", 0), ("y1", 1))
            },
        )
        medsl.attach(cs, conn, frame, {"NY": source}, strict=True, restage=True)
        return cs.write()
