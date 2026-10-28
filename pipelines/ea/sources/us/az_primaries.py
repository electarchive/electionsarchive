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

SOURCE = "us.az_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
OCR = PARSED / "ocr"
DOWNLOADS = DATA / "inbox" / "manual" / "MF-003"
PUBLISHER = "Arizona Secretary of State"
TITLE = "Official canvass of the primary election"
FILES = {
    "canvass2016primary.pdf": (
        date(2016, 8, 30),
        "https://apps.azsos.gov/election/2016/Primary/canvass2016primary.pdf",
        13,
    ),
    "2018_0910_signed_statewide_canvass.pdf": (
        date(2018, 8, 28),
        "https://apps.azsos.gov/election/2018/2018_0910_signed_statewide_canvass.pdf",
        15,
    ),
}
COLUMNS = [
    "apache", "cochise", "coconino", "gila", "graham", "greenlee", "lapaz", "maricopa", "mohave", "navajo", "pima",
    "pinal", "santacruz", "yavapai", "yuma", "total",
]  # fmt: skip
PARTIES = {"DEM": "democratic", "REP": "republican", "LBT": "libertarian", "GRN": "green"}
NUMBER = re.compile(r"^[\d,.'’\"]*\d[\d,.'’\"]*$")
PARTY = re.compile(r"^\((DEM|REP|LBT|GRN|[A-Z]{3})\)$")


def fetch() -> None:
    for name, (_, url, _) in FILES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                (DOWNLOADS / name).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime(2026, 10, 21, tzinfo=UTC),
                media_type="application/pdf",
            )


def anchors(line: list[scan.Word]) -> list[float] | None:
    found: dict[str, float] = {}
    pending = None
    for text, box, _, _ in line:
        key = re.sub(r"[^a-z]", "", text.lower())
        centre = (box[0] + box[2]) / 2
        if key in COLUMNS:
            found[key] = centre
        elif key == "lapaz":
            found["lapaz"] = centre
        elif key == "la":
            pending = centre
        elif key == "paz" and pending is not None:
            found["lapaz"] = (pending + centre) / 2
    if not {"apache", "cochise", "maricopa", "yuma", "total"} <= found.keys():
        return None
    out = []
    for index, key in enumerate(COLUMNS):
        if key in found:
            out.append(found[key])
        else:
            before = next(found[k] for k in reversed(COLUMNS[:index]) if k in found)
            after = next(found[k] for k in COLUMNS[index + 1 :] if k in found)
            out.append((before + after) / 2)
    return out


def read(pdf: Path, pages: int, name: str) -> list[dict]:
    rows = []
    current: tuple[str, int | None] | None = None
    for number in range(1, pages + 1):
        words = scan.words(pdf, number, OCR / f"{name}-{number}.tsv")
        slope = scan.skew(words)
        header = None
        current = None
        for line in scan.lines(words, slope, 14):
            texts = [text for text, _, _, _ in line]
            joined = " ".join(texts)
            if (found := anchors(line)) is not None:
                header = found
                continue
            district = re.search(r"District\s*No\.?\s*(\d+)\s*$", joined)
            if re.search(r"\bU\.?S\.?\s+Senator\b", joined):
                current = ("senate", None)
                continue
            if re.search(r"\bU\.?S\.?\s+Representative\b", joined) and district is not None:
                current = ("house", int(district.group(1)))
                continue
            if re.search(r"\bState\s+(Senator|Representative)\b", joined) or "Legislative" in joined:
                current = None
                continue
            first = next((index for index, text in enumerate(texts[:3]) if PARTY.match(text)), None)
            if (
                first is None
                and header is not None
                and line[0][1][0] < 300
                and re.match(r"[A-Za-z]{4,}", texts[0])
                and texts[0] not in ("Winner", "Page")
            ):
                current = None
            if first is None or current is None or header is None:
                continue
            party = PARTY.match(texts[first]).group(1)
            cut = header[0] - 80
            label = [text for text, box, _, _ in line[first + 1 :] if box[0] < cut]
            cells = [(text, box) for text, box, _, _ in line[first + 1 :] if box[0] >= cut and NUMBER.match(text)]
            rows.append(
                {
                    "office": current[0],
                    "district": current[1],
                    "party": PARTIES.get(party, party),
                    "writein": "(Write-In)" in label,
                    "name": " ".join(text for text in label if text not in ("*", "(Write-In)")).upper(),
                    "page": number,
                    "header": header,
                    "span": (min(box[1] for _, box, _, _ in line), max(box[3] for _, box, _, _ in line)),
                    "cells": [
                        (min(range(len(header)), key=lambda i: abs(header[i] - (box[0] + box[2]) / 2)), text, box)
                        for text, box in cells
                    ],
                }
            )
    return rows


def reread(pdf: Path, row: dict) -> list[set[int]]:
    top, bottom = row["span"]
    with tempfile.TemporaryDirectory() as folder:
        reads = scan.read(
            scan.rendered(pdf, row["page"], Path(folder)),
            [((int(x) - 76, top - 6, int(x) + 76, bottom + 6), True) for x in row["header"]],
            "eng",
        )
    return [{value for text in texts for value, weight in scan.numbers(text).items() if weight == 2} for texts in reads]


def settle(pdf: Path, row: dict) -> dict[int, tuple[int, tuple[int, int, int, int]]] | None:
    seen = {column: (int(re.sub(r"\D", "", text)), box) for column, text, box in row["cells"]}
    options = []
    for column, found in enumerate(reread(pdf, row)):
        known = {seen[column][0]} if column in seen else set()
        options.append(sorted(known | found) or [0])
    solutions = [pick for pick in itertools.islice(itertools.product(*options), 200000) if sum(pick[:15]) == pick[15]]
    if len(solutions) != 1:
        return None
    top, bottom = row["span"]
    return {
        column: (
            value,
            seen[column][1]
            if column in seen
            else (int(row["header"][column]) - 40, top, int(row["header"][column]) + 40, bottom),
        )
        for column, value in enumerate(solutions[0])
        if value or column in seen
    }


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '04%%'"""
            ).fetchall()
        }
    OCR.mkdir(parents=True, exist_ok=True)
    frames = []
    for file, (day, url, pages) in enumerate(FILES.values()):
        contests: dict[tuple[str, int | None, str], list[dict]] = {}
        for row in read(raw.cached(SOURCE, url).path, pages, Path(url).name):
            contests.setdefault((row["office"], row["district"], row["party"]), []).append(row)
        for (office, district, party), rows in contests.items():
            parsed = []
            for row in rows:
                slots = [column for column, _, _ in row["cells"]]
                values = {column: (int(re.sub(r"\D", "", text)), box) for column, text, box in row["cells"]}
                counted = sum(value for column, (value, _) in values.items() if column != 15)
                if len(slots) != len(set(slots)) or 15 not in values or counted != values[15][0]:
                    values = settle(raw.cached(SOURCE, url).path, row)
                    if values is None:
                        print(
                            f"{day} {office} {district} {party} {row['name']} (page {row['page']}): cells do not add to the total, row dropped"
                        )
                        continue
                    print(
                        f"{day} {office} {district} {party} {row['name']} (page {row['page']}): cells read again to match the total"
                    )
                parsed.append((row, values))
            for row, values in parsed:
                for column, (votes, box) in values.items():
                    if column == 15:
                        continue
                    frames.append(
                        {
                            "year": day.year,
                            "chamber": f"{office}:{party}",
                            "day": day,
                            "special": False,
                            "state": "AZ",
                            "district": district,
                            "code": counties[COLUMNS[column]],
                            "candidate": "WRITE-INS" if row["name"] == "" else row["name"],
                            "measure": 1,
                            "writein": row["writein"],
                            "votes": votes,
                            "kind": "county",
                            "source": "AZ",
                            "file": file,
                            "page": row["page"],
                            "x0": box[0],
                            "y0": box[1],
                            "x1": box[2],
                            "y1": box[3],
                        }
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(frames, infer_schema_length=None).with_columns(
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
            summary="Arizona's 2016 and 2018 U.S. House and Senate primaries by county, read from scanned canvass reports; the files were saved from the Secretary of State's site by a person in a browser",
            polity="US-AZ",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://azsos.gov/elections/results-data",
            license="Public record",
        )
        pages: dict[tuple[int, int], str] = {}
        sizes: dict[tuple[int, int], tuple[int, int]] = {}
        for file, (name, (_, url, _)) in enumerate(FILES.items()):
            pdf = raw.cached(SOURCE, url)
            artifact = cs.artifact(pdf, source)
            for number in sorted(frame.filter(pl.col("file") == file)["page"].unique().to_list()):
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
                pages[(file, number)] = str(page)
                sizes[(file, number)] = (width, height)
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
            page_id=pl.struct("file", "page").map_elements(
                lambda r: pages[(r["file"], r["page"])], return_dtype=pl.String
            ),
            **{
                edge: pl.struct("file", "page", edge).map_elements(
                    lambda r, edge=edge, axis=axis: r[edge] / sizes[(r["file"], r["page"])][axis],
                    return_dtype=pl.Float64,
                )
                for edge, axis in (("x0", 0), ("y0", 1), ("x1", 0), ("y1", 1))
            },
        )
        medsl.attach(cs, conn, frame, {"AZ": source}, strict=True, restage=True)
        return cs.write()
