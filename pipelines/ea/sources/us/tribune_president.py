import csv
import itertools
import re
import subprocess
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import numpy as np
from PIL import Image, ImageOps

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import tribune_almanac
from ea.store import DATA, connect

SOURCE = "us.tribune_president"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
RAW = "us.tribune_almanac"
DIGITIZED = 8
OMNIBUS = ("390fdb7684afb596b0eb8d472ccc6e0d65186cdb0096f3a113e2a80ea0c5e5be", "tribunealmanacpo18751879newy")
VOLUME_1881 = ("9335f3802a08c178ed2cbfa64127dc2ad0b5c30a50028392e08805835fe1a369", "vol1881tribuneal00unse")
STATES = {
    "Alabama": "AL", "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New York": "NY", "North Carolina": "NC", "Ohio": "OH",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "Tennessee": "TN",
    "Texas": "TX", "Vermont": "VT", "Virginia": "VA", "West Virginia": "WV", "Wisconsin": "WI",
}
ORDER = list(STATES)
EARLY = [s for s in ORDER if s not in ("California", "Colorado", "Florida", "Iowa", "Kansas", "Minnesota", "Nebraska", "Nevada", "Oregon", "Texas", "West Virginia", "Wisconsin")]
FORTIES = [s for s in ORDER if s not in ("California", "Colorado", "Kansas", "Minnesota", "Nebraska", "Nevada", "Oregon", "West Virginia")]
MID = [s for s in ORDER if s not in ("Colorado", "Kansas", "Nebraska", "Nevada", "South Carolina", "West Virginia")]
LATE = [s for s in ORDER if s != "Colorado"]
TABLES = {
    "om254a": dict(mask="om254", page=254, sha=OMNIBUS, rows=EARLY, y0=909, last=1814, total=1862, x0=700, x1=2035, gap=0.04, years=(1824, 1828, 1832, 1836)),
    "om254b": dict(mask="om254", page=254, sha=OMNIBUS, rows=FORTIES, y0=2056, last=3085, total=3144, x0=719, x1=2032, gap=0.04, years=(1840, 1844, 1848)),
    "om255a": dict(mask="om255", page=255, sha=OMNIBUS, rows=MID, y0=453, last=1562, total=1618, x0=628, x1=1959, gap=0.04, years=(1852, 1856, 1860)),
    "om255b": dict(mask="om255", page=255, sha=OMNIBUS, rows=LATE, y0=1810, last=3103, total=3152, x0=431, x1=2060, gap=0.04, years=(1864, 1868, 1872)),
    "v81p95": dict(mask="v81p95", page=95, sha=VOLUME_1881, rows=ORDER, y0=350, last=1332, total=1385, x0=322, x1=1457, gap=0.02, years=(1872, 1876, 1880)),
}


def centers(spec: dict) -> list[tuple[str, int]]:
    rows = spec["rows"]
    pitch = (spec["last"] - spec["y0"]) / (len(rows) - 1)
    return [*[(state, round(spec["y0"] + pitch * i)) for i, state in enumerate(rows)], ("Total", spec["total"])]


def columns(im: np.ndarray, spec: dict, top: int, bottom: int) -> list[tuple[int, int]]:
    ink = (im[top:bottom, spec["x0"] : spec["x1"]] < 110).mean(axis=0)
    cuts: list[tuple[int, int]] = []
    start = None
    for x, value in enumerate(ink):
        if value >= spec["gap"] and start is None:
            start = x
        elif value < spec["gap"] and start is not None:
            cuts.append((start, x))
            start = None
    if start is not None:
        cuts.append((start, len(ink)))
    merged: list[tuple[int, int]] = []
    for a, b in cuts:
        if merged and a - merged[-1][1] < 6:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(a + spec["x0"], b + spec["x0"]) for a, b in merged if b - a >= 60]


def read_cell(task: tuple[str, str, int, tuple[int, int, int, int]]) -> tuple[str, str, int, tuple[int, int, int, int], list[str]]:
    name, state, col, box = task
    spec = TABLES[name]
    with Image.open(PARSED / "native" / f"{spec['mask']}-mask.png") as mask:
        crop = ImageOps.invert(mask.convert("L").crop(box))
    reads: list[str] = []
    for scale, psm in ((3, "7"), (3, "8"), (4, "7"), (2, "7")):
        big = ImageOps.expand(crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS), 20, 255)
        with tempfile.NamedTemporaryFile(suffix=".png") as file:
            big.save(file.name)
            out = subprocess.run(
                ["tesseract", file.name, "-", "--psm", psm, "-c", "tessedit_char_whitelist=0123456789,."],
                capture_output=True,
                text=True,
            ).stdout
        reads.append(re.sub(r"\D", "", out))
    return name, state, col, box, reads


def parse() -> None:
    tasks = []
    for name, spec in TABLES.items():
        if spec["mask"] == "v81p95":
            continue
        with Image.open(PARSED / "native" / f"{spec['mask']}-mask.png") as mask:
            im = np.array(ImageOps.invert(mask.convert("L")))
        rows = centers(spec)
        cols = columns(im, spec, rows[0][1] - 15, rows[-2][1] + 15)
        print(name, len(rows), "rows", len(cols), "columns", cols)
        half = 14 if spec["mask"] != "v81p95" else 11
        for state, y in rows:
            for col, (a, b) in enumerate(cols):
                tasks.append((name, state, col, (a + 2, y - half, b - 2, y + half)))
    with ProcessPoolExecutor() as pool, (PARSED / "reads.tsv").open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["table", "state", "col", "x0", "y0", "x1", "y1", "reads"])
        for name, state, col, box, reads in pool.map(read_cell, tasks, chunksize=16):
            writer.writerow([name, state, col, box[0], box[1], box[2], box[3], "|".join(reads)])
    print(len(tasks), "cells read")
    parse_1881()


def read_strip(task: tuple[str, int]) -> tuple[str, int, list[list[tuple[str, int, int]]]]:
    state, y = task
    spec = TABLES["v81p95"]
    with Image.open(PARSED / "native" / "v81p95-mask.png") as mask:
        strip = ImageOps.invert(mask.convert("L").crop((300, y - 15, 1470, y + 15)))
    reads: list[list[tuple[str, int, int]]] = []
    for scale in (2, 3, 4):
        big = ImageOps.expand(strip.resize((strip.width * scale, strip.height * scale), Image.LANCZOS), 30, 255)
        with tempfile.NamedTemporaryFile(suffix=".png") as file:
            big.save(file.name)
            out = subprocess.run(
                ["tesseract", file.name, "-", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789,. ", "tsv"],
                capture_output=True,
                text=True,
            ).stdout
        words = []
        for line in out.splitlines()[1:]:
            part = line.split("\t")
            digits = re.sub(r"\D", "", part[11]) if len(part) > 11 else ""
            if digits:
                left, width = int(part[6]), int(part[8])
                words.append((digits, 300 + (left - 30) // scale, 300 + (left - 30 + width) // scale))
        reads.append(words)
    return state, y, reads


def parse_1881() -> None:
    spec = TABLES["v81p95"]
    rows = centers(spec)
    with Image.open(PARSED / "native" / "v81p95-mask.png") as mask:
        im = np.array(ImageOps.invert(mask.convert("L")))
    cols = columns(im, spec, rows[0][1] - 15, rows[-2][1] + 15)
    with ProcessPoolExecutor() as pool, (PARSED / "reads_1881.tsv").open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["state", "col", "x0", "y0", "x1", "y1", "reads"])
        for state, y, reads in pool.map(read_strip, rows, chunksize=4):
            for col, (a, b) in enumerate(cols):
                cell = []
                for words in reads:
                    hit = [w for w in words if a - 6 <= (w[1] + w[2]) / 2 <= b + 6]
                    cell.append(hit[0] if hit else ("", 0, 0))
                boxes = [c for c in cell if c[0]]
                x0 = min((c[1] for c in boxes), default=a)
                x1 = max((c[2] for c in boxes), default=b)
                writer.writerow([state, col, x0, y - 14, x1, y + 14, "|".join(c[0] for c in cell)])
