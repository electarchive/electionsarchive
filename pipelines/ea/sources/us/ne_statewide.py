import difflib
import json
import os
import re
import subprocess

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
            ends = sorted(position for _, _, found in parsed for position, _ in found)
            anchors: list[int] = []
            group: list[int] = []
            for position in ends:
                if group and position - group[-1] > 3:
                    if len(group) >= spec.get("minimum", 8):
                        anchors.append(round(sum(group) / len(group)))
                    group = []
                group.append(position)
            if len(group) >= spec.get("minimum", 8):
                anchors.append(round(sum(group) / len(group)))
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
            for county, index, value in spec.get("fix", []):
                table[re.sub(r"[^a-z]", "", county.lower())][index] = value
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
            for column in spec["columns"]:
                index = column["column"]
                total = sum(row[index] or 0 for row in table.values())
                nearest = [value for position, value in printed if abs(position - anchors[index]) <= 8]
                official = column.get("official", nearest[0] if len(nearest) == 1 else None)
                if total != official or len(table) != len(counties):
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
