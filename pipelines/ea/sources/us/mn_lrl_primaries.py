import difflib
import html
import re
import subprocess
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mn_lrl_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.lrl.mn.gov/archive/sessions/electionresults/{}-p-man.pdf"
DOCUMENTS = {date(2000, 9, 12): (4, 5, 6)}
DPI = 100
ORDINALS = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6, "SEVENTH": 7, "EIGHTH": 8}
PARTIES = {
    "DFL": "minnesota-democratic-farmer-labor",
    "R": "republican",
    "IP": "independence",
    "CP": "constitution",
}
PUBLISHER = "Minnesota Legislative Reference Library"
TITLE = "Minnesota Legislative Manual, state primary results"

type Word = tuple[float, float, float, float, str]


def fetch() -> None:
    for day in DOCUMENTS:
        raw.fetch(SOURCE, URL.format(day), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            name.upper().removesuffix(" COUNTY, MN").replace("ST. ", "SAINT "): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '27%' and u.valid @> '2000-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for day, pages in DOCUMENTS.items():
        pdf = raw.cached(SOURCE, URL.format(day)).path
        district = None
        block = None
        for number in pages:
            page = subprocess.run(
                ["pdftotext", "-bbox", "-f", str(number), "-l", str(number), str(pdf), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            width, height = (
                float(value) for value in re.search(r'<page width="([\d.]+)" height="([\d.]+)"', page).groups()
            )
            words: list[Word] = [
                (float(x0), float(y0), float(x1), float(y1), html.unescape(text))
                for x0, y0, x1, y1, text in re.findall(
                    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', page
                )
            ]
            lines: list[list[Word]] = []
            for word in sorted(words, key=lambda word: word[1]):
                if lines and word[1] - lines[-1][-1][1] < 3.5:
                    lines[-1].append(word)
                else:
                    lines.append([word])
            cells: list[tuple[dict, int, Word | None]] = []
            for line in lines:
                line.sort()
                texts = [word[4] for word in line]
                if len(texts) >= 2 and texts[1] == "DISTRICT" and texts[0] in ORDINALS:
                    district = ORDINALS[texts[0]]
                    block = None
                    continue
                if district is None:
                    continue
                if all(text in PARTIES for text in texts):
                    block = {
                        "district": district,
                        "page": number,
                        "centers": [(word[0] + word[2]) / 2 for word in line],
                        "parties": texts,
                        "names": [[] for _ in texts],
                        "rows": [],
                        "header": True,
                        "width": width,
                        "height": height,
                    }
                    continue
                if block is None:
                    continue
                centers = block["centers"]
                if block["header"]:
                    if texts[0] == "COUNTY":
                        line = line[1:]
                        block["header"] = False
                    phrases: list[list[Word]] = []
                    for word in line:
                        if phrases and word[0] - phrases[-1][-1][2] < 6:
                            phrases[-1].append(word)
                        else:
                            phrases.append([word])
                    for phrase in phrases:
                        center = (phrase[0][0] + phrase[-1][2]) / 2
                        block["names"][min(range(len(centers)), key=lambda i: abs(centers[i] - center))].extend(phrase)
                    continue
                edge = centers[0] - 30
                label = " ".join(word[4] for word in line if word[2] < edge)
                values = [word for word in line if word[2] >= edge]
                if not values or not label:
                    continue
                placed: list[Word | None] = [None] * len(centers)
                for word in values:
                    center = (word[0] + word[2]) / 2
                    index = min(range(len(centers)), key=lambda i: abs(centers[i] - center))
                    if placed[index] is not None:
                        raise ValueError(f"page {number}, district {district}, {label}: two cells in column {index}")
                    placed[index] = word
                if label == "TOTAL":
                    county = None
                else:
                    match = difflib.get_close_matches(label.upper(), counties, n=1, cutoff=0.6)
                    if not match:
                        raise ValueError(f"page {number}, district {district}: no county for {label}")
                    county = counties[match[0]]
                row = {"county": county, "label": label, "y": (line[0][1], line[0][3]), "cells": placed}
                block["rows"].append(row)
                for index, word in enumerate(placed):
                    cells.append((row, index, word))
                if county is None:
                    names = [
                        " ".join(word[4] for word in sorted(column, key=lambda word: (round(word[1]), word[0])))
                        for column in block["names"]
                    ]
                    for index, party in enumerate(block["parties"]):
                        spans = [r["cells"][index] for r in block["rows"] if r["cells"][index] is not None]
                        left = min((word[0] for word in spans), default=centers[index] - 20)
                        right = max((word[2] for word in spans), default=centers[index] + 20)
                        for r in block["rows"]:
                            r.setdefault("spans", {})[index] = (left, right)
                        block.setdefault("columns", []).append((names[index], party))
                    rows.append((day, block))
                    block = None
            bad = [
                (row, index)
                for row, index, word in cells
                if word is None or not re.fullmatch(r"\d{1,3}(,\d{3})*", word[4])
            ]
            read: dict[tuple[int, int], tuple[int, tuple[float, float, float, float]]] = {}
            if bad:
                with tempfile.TemporaryDirectory() as folder:
                    image = scan.rendered(pdf, number, Path(folder))
                    boxes = [
                        (row["spans"][index][0] - 4, row["y"][0] - 2, row["spans"][index][1] + 4, row["y"][1] + 2)
                        for row, index in bad
                    ]
                    scaled = [tuple(round(value * 300 / 72) for value in box) for box in boxes]
                    passes = scan.read(image, [(box, True) for box in scaled], "eng")
                    for (row, index), box, texts in zip(bad, boxes, passes, strict=True):
                        layer = row["cells"][index]
                        texts = [*texts, re.sub(r"\D", "", layer[4]) if layer else ""]
                        counted = Counter(
                            value
                            for value in (text.translate(scan.CONFUSABLE).replace(",", "") for text in texts)
                            if value.isdigit()
                        )
                        if len(counted) != 1 or sum(counted.values()) < 2:
                            raise ValueError(
                                f"page {number}, district {district}, {row['label']}, column {index}: read {texts}"
                            )
                        read[(id(row), index)] = (int(next(iter(counted))), box)
            for row, index, word in cells:
                if (id(row), index) in read:
                    row.setdefault("values", {})[index] = read[(id(row), index)]
                else:
                    row.setdefault("values", {})[index] = (int(word[4].replace(",", "")), word[:4])
    out = []
    for day, block in rows:
        totals = next(row for row in block["rows"] if row["county"] is None)
        for index, (name, party) in enumerate(block["columns"]):
            summed = sum(row["values"][index][0] for row in block["rows"] if row["county"] is not None)
            if summed != totals["values"][index][0]:
                raise ValueError(
                    f"{day} district {block['district']}, {name}: counties sum to {summed}, printed {totals['values'][index][0]}"
                )
            for row in block["rows"]:
                if row["county"] is None:
                    continue
                votes, (x0, y0, x1, y1) = row["values"][index]
                out.append(
                    (day, block["district"], party, row["county"], name, votes, block["page"],
                     x0 / block["width"], y0 / block["height"], x1 / block["width"], y1 / block["height"])
                )  # fmt: skip
    frame = pl.DataFrame(
        out,
        schema={
            "day": pl.Date,
            "district": pl.Int16,
            "party": pl.String,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "page": pl.Int16,
            "x0": pl.Float64,
            "y0": pl.Float64,
            "x1": pl.Float64,
            "y1": pl.Float64,
        },
        orient="row",
    )
    if frame.select("day", "district", "code", "candidate").is_duplicated().any():
        raise ValueError("a county appears twice for a candidate")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("day", "district", "party", "candidate", maintain_order=True)
        .agg(pl.col("votes").sum(), pl.len())
        .rows()
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's U.S. House primaries by county from the Legislative Manual's state primary tables",
            polity="US-MN",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.lrl.mn.gov/mngov/electionresults",
                license="Public record",
            )
        frame = pl.read_parquet(PARSED / "counties.parquet")
        pages: dict[tuple[date, int], str] = {}
        for day, numbers in DOCUMENTS.items():
            cached = raw.cached(SOURCE, URL.format(day))
            artifact = cs.artifact(cached, source)
            for number in numbers:
                target = DATA / "pages" / SOURCE / f"{cached.path.stem}-{number}.png"
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(cached.path), str(target.with_suffix(""))],
                        check=True,
                    )
                with Image.open(target) as rendered:
                    width, height = rendered.size
                page = cs.page(artifact, number)
                pages[(day, number)] = str(page)
                if UUID(str(page)) in cs.minted:
                    cs.add(
                        "source_page",
                        id=page,
                        artifact_id=artifact,
                        page_number=number,
                        image_key=f"pages/{SOURCE}/{target.name}",
                        width=width,
                        height=height,
                    )
        medsl.attach(
            cs,
            conn,
            frame.select(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber=pl.lit("house:") + pl.col("party").replace_strict(PARTIES),
                day="day",
                special=pl.lit(False),
                state=pl.lit("MN"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("MN"),
                page_id=pl.struct("day", "page").map_elements(
                    lambda key: pages[(key["day"], key["page"])], return_dtype=pl.String
                ),
                x0="x0",
                y0="y0",
                x1="x1",
                y1="y1",
            ),
            {"MN": source},
            strict=True,
        )
        return cs.write()
