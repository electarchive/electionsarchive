import difflib
import json
import os
import re
import subprocess
import tempfile
from collections import Counter

import polars as pl

from ea import raw
from ea.scans import MANIFESTS, WORK
from ea.store import connect

SOURCE = "us.ne_statewide"
OCR = str.maketrans({"B": "8", "O": "0", "o": "0", "S": "5", "I": "1", "l": "1", "&": "6", "]": "1"})
CELL = r"(?<![A-Za-z0-9])[0-9BOoSIl&\]]+(?:[\s,.]{1,3}[0-9BOoSIl&\]]{3})*(?![A-Za-z0-9])"


def manifests() -> list[str]:
    return sorted(path.stem for path in MANIFESTS.glob("ne-statewide-*.json"))


def fetch() -> None:
    for name in manifests():
        raw.fetch(SOURCE, json.loads((MANIFESTS / f"{name}.json").read_text())["file"]["url"], suffix=".pdf")


def cells(line: str) -> list[tuple[int, int]]:
    found = []
    for match in re.finditer(CELL, line):
        digits = re.sub(r"[^0-9]", "", match[0].translate(OCR))
        if digits and (re.search(r"[0-9]", match[0]) or len(match[0]) > 1):
            found.append((match.end(), int(digits)))
    return found


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    for name in manifests():
        if os.environ.get("NE_ONLY", name) != name:
            continue
        manifest = json.loads((MANIFESTS / f"{name}.json").read_text())
        path = raw.cached(manifest["raw_source"], manifest["file"]["url"]).path
        rows = []
        for spec in manifest["pages"]:
            page = str(spec["page"])
            text = subprocess.run(
                ["pdftotext", "-layout", "-f", page, "-l", page, str(path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            words = subprocess.run(
                ["pdftotext", "-bbox", "-f", page, "-l", page, str(path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            size = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', words)
            width, height = float(size[1]), float(size[2])
            placed = [
                (float(x0), float(y0), float(x1), float(y1), word)
                for x0, y0, x1, y1, word in re.findall(
                    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', words
                )
            ]
            lines = text.splitlines()
            end = next(index for index, line in enumerate(lines) if re.match(r"\s*TOTALS?\b", line, re.IGNORECASE))
            offset = lines[end].upper().index("TOTAL") + 5
            printed = [(position + offset, value) for position, value in cells(lines[end][offset:])]
            parsed: list[tuple[str, str, list[tuple[int, int]]]] = []
            for line in lines[:end]:
                match = re.match(r"\s*([A-Za-z][A-Za-z .']*?)(?:\s{2,}|\.{3,}|(?=[0-9]))", line)
                if not match:
                    continue
                key = re.sub(r"[^a-z]", "", match[1].lower())
                close = difflib.get_close_matches(key, counties, 1, 0.6)
                if close:
                    parsed.append(
                        (
                            key,
                            close[0],
                            [(position + match.end(), value) for position, value in cells(line[match.end() :])],
                        )
                    )
            counts = Counter(position for _, _, found in parsed for position, _ in found)
            anchors: list[int] = []
            for position, count in counts.most_common():
                if count >= spec.get("minimum", 8) and all(abs(position - kept) > 3 for kept in anchors):
                    anchors.append(position)
            anchors.sort()
            table: dict[str, list[int | None]] = {}
            heights: dict[str, tuple[float, float]] = {}
            cursor = 0
            for key, county, found in parsed:
                row: list[int | None] = [None] * len(anchors)
                for position, value in found:
                    nearest = min(range(len(anchors)), key=lambda a: abs(anchors[a] - position))
                    if abs(anchors[nearest] - position) <= 4:
                        row[nearest] = value
                table[county] = row
                for index in range(cursor, len(placed)):
                    if (
                        re.sub(r"[^a-z]", "", placed[index][4].lower()).startswith(key[:3])
                        and placed[index][0] < width * 0.3
                    ):
                        heights[county] = (placed[index][1], placed[index][3])
                        cursor = index + 1
                        break
            fixes = spec.get("fix", [])
            if "fix_from" in manifest:
                fixes = next(
                    entry.get("fix", [])
                    for entry in json.loads((MANIFESTS / f"{manifest['fix_from']}.json").read_text())["pages"]
                    if entry["page"] == spec["page"]
                )
            for county, index, value in fixes:
                table.setdefault(re.sub(r"[^a-z]", "", county.lower()), [None] * len(anchors))[index] = value
            widest = max(len(line) for line in lines)
            bands = [((position - 7) / widest * width, (position + 1) / widest * width) for position in anchors]
            known = [heights[county] for county in table if county in heights]
            pitch = (known[-1][0] - known[0][0]) / max(len(known) - 1, 1)
            previous = known[0]
            for county in table:
                if county in heights:
                    previous = heights[county]
                else:
                    previous = heights[county] = (previous[0] + pitch, previous[1] + pitch)
            if os.environ.get("NE_DEBUG"):
                for index in range(len(anchors)):
                    nearest = [value for position, value in printed if abs(position - anchors[index]) <= 8]
                    print("column", index, sum(row[index] or 0 for row in table.values()), nearest)
                    for left, right in ((3, 5), (5, 7), (4, 6), (6, 8), (3, 7)):
                        if index == right:
                            for county, row in table.items():
                                a, b = row[left] or 0, row[right] or 0
                                if abs(a - b) > 0.2 * max(a, b, 1) + 15:
                                    print("   off", left, right, county, row)
                    print(
                        "  top", sorted(((row[index] or 0, county) for county, row in table.items()), reverse=True)[:6]
                    )
            for column in spec["columns"]:
                index = column["column"]
                total = sum(row[index] or 0 for row in table.values())
                nearest = [value for position, value in printed if abs(position - anchors[index]) <= 8]
                official = column.get("official", nearest[0] if len(nearest) == 1 else None)
                if total != official or len(table) != len(counties):
                    if os.environ.get("NE_DEBUG"):
                        for county, row in table.items():
                            check = spec.get("check")
                            bad = check is not None and not 0 <= (row[check[0]] or 0) - sum(
                                row[i] or 0 for i in check[1]
                            ) <= 0.04 * (row[check[0]] or 1)
                            if bad or None in row[: len(anchors)]:
                                print(county, row, "BAD" if bad else "")
                        print([c for c in counties if c not in table], anchors)
                        with tempfile.TemporaryDirectory() as folder:
                            subprocess.run(
                                [
                                    "pdftoppm",
                                    "-r",
                                    "300",
                                    "-gray",
                                    "-png",
                                    "-f",
                                    page,
                                    "-l",
                                    page,
                                    "-singlefile",
                                    str(path),
                                    f"{folder}/p",
                                ],
                                check=True,
                            )
                            scan = subprocess.run(
                                ["tesseract", f"{folder}/p.png", "-", "--psm", "6", "tsv"],
                                capture_output=True,
                                text=True,
                                check=True,
                            ).stdout.splitlines()[1:]
                        seen: dict[tuple[str, str, str], list[tuple[int, str]]] = {}
                        for entry in scan:
                            part = entry.split("\t")
                            if len(part) == 12 and part[11].strip():
                                seen.setdefault(tuple(part[2:5]), []).append((int(part[6]) + int(part[8]), part[11]))
                        reads: dict[str, list[tuple[int, int]]] = {}
                        for words_ in seen.values():
                            words_.sort()
                            lead = [m[0].lower() for w in words_[:3] if (m := re.match(r"[A-Za-z]+", w[1]))]
                            close = next(
                                iter(
                                    sorted(
                                        {
                                            c
                                            for guess in (lead[:1], lead[:2])
                                            if guess
                                            for c in difflib.get_close_matches("".join(guess), counties, 1, 0.8)
                                        }
                                    )
                                ),
                                None,
                            )
                            close = [close] if close else []
                            if close:
                                reads[close[0]] = [
                                    (edge, int(re.sub(r"[^0-9]", "", w)))
                                    for edge, w in words_
                                    if re.fullmatch(r"[0-9][0-9,.]*", w) and re.sub(r"[^0-9]", "", w)
                                ]
                        peaks: list[int] = []
                        for position, count in Counter(e for found in reads.values() for e, _ in found).most_common():
                            if count >= 8 and all(abs(position - kept) > 12 for kept in peaks):
                                peaks.append(position)
                        peaks.sort()
                        print("second read columns", len(peaks), "of", len(anchors))
                        for county, found in reads.items():
                            second: list[int | None] = [None] * len(peaks)
                            for edge, value in found:
                                near = min(range(len(peaks)), key=lambda q: abs(peaks[q] - edge))
                                if abs(peaks[near] - edge) <= 14:
                                    second[near] = value
                            if county in table and len(second) == len(table[county]) and second != table[county]:
                                print(
                                    "DIFF",
                                    county,
                                    [(i, a, b) for i, (a, b) in enumerate(zip(table[county], second)) if a != b],
                                )
                        print("unread", [c for c in table if c not in reads])

                    raise ValueError(
                        f"{name} page {page} column {index}: {len(table)} counties sum to {total}, printed {official}"
                    )
                for county, row in table.items():
                    if row[index] is None:
                        continue
                    rows.append(
                        (
                            f"{column['contest']}|{column['candidate']}",
                            column["contest"],
                            column["candidate"],
                            bool(column.get("writein", False)),
                            spec["page"],
                            str(spec.get("id", spec["page"])),
                            counties[county],
                            row[index],
                            False,
                            bands[index][0] / width,
                            heights[county][0] / height,
                            bands[index][1] / width,
                            heights[county][1] / height,
                            "text",
                            None,
                        )
                    )
        folder = WORK / name
        folder.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            rows,
            schema={
                "series": pl.String,
                "contest": pl.String,
                "candidate": pl.String,
                "writein": pl.Boolean,
                "page": pl.Int64,
                "section": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
                "half": pl.Boolean,
                "x0": pl.Float64,
                "y0": pl.Float64,
                "x1": pl.Float64,
                "y1": pl.Float64,
                "how": pl.String,
                "note": pl.Null,
            },
            orient="row",
        ).write_parquet(folder / "tables.parquet")
        print(name, len(rows), "cells")
