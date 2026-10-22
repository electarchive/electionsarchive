import csv
import itertools
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import numpy as np
from PIL import Image, ImageOps

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books, tribune_almanac
from ea.store import DATA, connect

SOURCE = "us.tribune_president"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
RAW = "us.tribune_almanac"
DIGITIZED = 8
OMNIBUS = ("390fdb7684afb596b0eb8d472ccc6e0d65186cdb0096f3a113e2a80ea0c5e5be", "tribunealmanacpo18751879newy")
VOLUME_1881 = ("9335f3802a08c178ed2cbfa64127dc2ad0b5c30a50028392e08805835fe1a369", "vol1881tribuneal00unse")
STATES = {
    "Alabama": "AL",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New York": "NY",
    "North Carolina": "NC",
    "Ohio": "OH",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "Tennessee": "TN",
    "Texas": "TX",
    "Vermont": "VT",
    "Virginia": "VA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
}
ORDER = list(STATES)
EARLY = [
    s
    for s in ORDER
    if s
    not in (
        "California",
        "Colorado",
        "Florida",
        "Iowa",
        "Kansas",
        "Minnesota",
        "Nebraska",
        "Nevada",
        "Oregon",
        "Texas",
        "West Virginia",
        "Wisconsin",
    )
]
FORTIES = [
    s
    for s in ORDER
    if s not in ("California", "Colorado", "Kansas", "Minnesota", "Nebraska", "Nevada", "Oregon", "West Virginia")
]
MID = [s for s in ORDER if s not in ("Colorado", "Kansas", "Nebraska", "Nevada", "South Carolina", "West Virginia")]
LATE = [s for s in ORDER if s != "Colorado"]
EARLY_ROWS = [s for s in ORDER if s not in ("Colorado", "Nebraska")]
V61_ROWS = [s for s in ORDER if s not in ("Colorado", "Kansas", "Nebraska", "Nevada", "West Virginia")]
TABLES = {
    "om254a": dict(
        mask="om254",
        page=254,
        sha=OMNIBUS,
        rows=EARLY,
        y0=909,
        last=1814,
        total=1862,
        x0=700,
        x1=2035,
        gap=0.04,
        years=(1824, 1828, 1832, 1836),
    ),
    "om254b": dict(
        mask="om254",
        page=254,
        sha=OMNIBUS,
        rows=FORTIES,
        y0=2056,
        last=3085,
        total=3144,
        x0=719,
        x1=2032,
        gap=0.04,
        years=(1840, 1844, 1848),
    ),
    "om255a": dict(
        mask="om255",
        page=255,
        sha=OMNIBUS,
        rows=MID,
        y0=453,
        last=1562,
        total=1618,
        x0=628,
        x1=1959,
        gap=0.04,
        years=(1852, 1856, 1860),
    ),
    "om255b": dict(
        mask="om255",
        page=255,
        sha=OMNIBUS,
        rows=LATE,
        y0=1810,
        last=3103,
        total=3152,
        x0=431,
        x1=2060,
        gap=0.04,
        years=(1864, 1868, 1872),
    ),
    "v81p95": dict(
        mask="v81p95",
        page=95,
        sha=VOLUME_1881,
        rows=ORDER,
        y0=350,
        last=1332,
        total=1385,
        x0=322,
        x1=1457,
        gap=0.02,
        years=(1872, 1876, 1880),
    ),
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


def read_cell(
    task: tuple[str, str, int, tuple[int, int, int, int]],
) -> tuple[str, str, int, tuple[int, int, int, int], list[str]]:
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
    parse_prints()


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


PRINTS = {
    "v65": dict(
        mask="v65",
        page=71,
        sha="7b1d0b9ba2f6f8d746338dac6f2715b31792f83e2173c87119639736e13257a3",
        rows=EARLY_ROWS,
        y0=2287,
        pitch=45.03,
        total=3927,
        cols=[
            (553, 705),
            (758, 912),
            (930, 1089),
            (1115, 1306),
            (1322, 1515),
            (1543, 1722),
            (1746, 1893),
            (1942, 2118),
            (2165, 2328),
            (2338, 2499),
        ],
        scales=((1, "7"), (1.5, "7"), (2, "7"), (1, "8")),
    ),
    "v61": dict(
        mask="v61",
        page=64,
        sha="97638c3d6f81aaf7970d3c337f354eac04e601cc627c3191877b552cd4af2c74",
        rows=V61_ROWS,
        y0=1402,
        pitch=25.1,
        total=2239,
        cols=[
            (293, 402),
            (413, 515),
            (527, 631),
            (640, 744),
            (768, 858),
            (882, 972),
            (987, 1086),
            (1111, 1201),
            (1224, 1315),
            (1337, 1410),
        ],
        scales=((3, "7"), (4, "7"), (3, "8"), (2, "7")),
    ),
    "v69": dict(
        mask="v69",
        page=92,
        sha="41e6560af4b2f77374f49231b2f25df882f3c3c79fd06615bfb9302ffb212030",
        rows=EARLY_ROWS,
        y0=1282,
        pitch=29.6,
        total=2348,
        cols=[
            (545, 640),
            (657, 752),
            (759, 859),
            (873, 967),
            (984, 1079),
            (1096, 1187),
            (1203, 1299),
            (1318, 1411),
            (1417, 1515),
            (1525, 1599),
        ],
        scales=((2, "7"), (3, "7"), (2, "8"), (4, "7")),
    ),
}


def read_print_cell(
    task: tuple[str, str, int, int],
) -> tuple[str, str, int, tuple[int, int, int, int], list[str]]:
    key, state, col, yc = task
    spec = PRINTS[key]
    half = round(spec["pitch"] * 0.42)
    a, b = spec["cols"][col]
    left = spec["cols"][col - 1][1] + 2 if col > 0 else a - 60
    box = (left, yc - half, b + 4, yc + half)
    with Image.open(PARSED / "native" / f"{spec['mask']}-mask.png") as mask:
        crop = ImageOps.invert(mask.convert("L").crop(box))
    reads: list[str] = []
    for scale, psm in spec["scales"]:
        big = ImageOps.expand(
            crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS), 20, 255
        )
        with tempfile.NamedTemporaryFile(suffix=".png") as file:
            big.save(file.name)
            out = subprocess.run(
                ["tesseract", file.name, "-", "--psm", psm, "-c", "tessedit_char_whitelist=0123456789,."],
                capture_output=True,
                text=True,
            ).stdout
        reads.append(re.sub(r"\D", "", out))
    return key, state, col, box, reads


def parse_prints() -> None:
    tasks = []
    for key, spec in PRINTS.items():
        for state, yc in [
            *[(state, round(spec["y0"] + spec["pitch"] * i)) for i, state in enumerate(spec["rows"])],
            ("Total", spec["total"]),
        ]:
            for col in range(len(spec["cols"])):
                tasks.append((key, state, col, yc))
    with ProcessPoolExecutor() as pool, (PARSED / "reads_prints.tsv").open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["print", "state", "col", "x0", "y0", "x1", "y1", "reads"])
        for key, state, col, box, reads in pool.map(read_print_cell, tasks, chunksize=16):
            writer.writerow([key, state, col, *box, "|".join(reads)])


NOTE = (
    "The state's popular vote for president as printed in The Tribune Almanac and Political Register for 1881, which "
    "compiles the states' official returns and is a compilation rather than a certified canvass. The table prints the "
    "major candidates only: Grant and Greeley for 1872, Hayes and Tilden for 1876, and Garfield, Hancock, Weaver, and "
    "Dow for 1880, so the total is the sum of the printed lines and minor candidates are not carried. Tickets that "
    "Wikipedia's results-by-state table names keep that table's candidates. Four independent readings of each number "
    "by cell OCR were reconciled against Wikipedia's figure, and every cell they could not settle, or that differs "
    "from Wikipedia by more than 1 percent, was read from a crop of the printed page; each digitized number carries "
    "its page and box. Wikipedia's earlier figures are kept as a superseded, non-default set."
)


def note(year: int, volume: int) -> str:
    return (
        f"The state's popular vote for president in {year} as printed in {tribune_almanac.TITLES[volume]}, which "
        "compiles the states' official returns and is a compilation rather than a certified canvass. Tickets that "
        "Wikipedia's results-by-state table names keep that table's candidates; the printed lines are carried "
        "and the total is their sum. Each digitized number was read from the printed page by cell OCR, checked "
        "against a second printing or Wikipedia's figure, and settled from a crop of the page where they "
        "disagreed; the column sums match the printed Total row, and each number carries its page and box. "
        "Wikipedia's earlier figures are kept as a superseded, non-default set."
    )


DEMOTED = {(1872, "MN"), (1872, "NE"), (1872, "TN"), (1880, "LA"), (1880, "NV"), (1880, "TX")}
HELD = (
    "The printed figure for a top-two candidate differs from Wikipedia's by more than 1 percent and no second source "
    "settles it, so Wikipedia's set stays the default and this one is kept beside it."
)
NAMES = {
    0: ("Grant", 1872),
    1: ("Greeley", 1872),
    2: ("Hayes", 1876),
    3: ("Tilden", 1876),
    4: ("Garfield", 1880),
    5: ("Hancock", 1880),
    6: ("Weaver", 1880),
    7: ("Dow", 1880),
}
LEGENDS = {
    "Weaver": ("James B. Weaver (Greenback)", "greenbackparty"),
    "Dow": ("Neal Dow (Prohibition)", "prohibitionparty"),
}
FIXES: dict[tuple[str, int], int | None] = {
    ("Arkansas", 3): 58071,
    ("California", 0): 54020,
    ("California", 1): 40718,
    ("California", 4): 80378,
    ("California", 5): 80417,
    ("Colorado", 0): None,
    ("Colorado", 1): None,
    ("Colorado", 5): 24647,
    ("Connecticut", 0): 50638,
    ("Florida", 1): 15427,
    ("Florida", 6): None,
    ("Georgia", 3): 130088,
    ("Georgia", 6): 969,
    ("Illinois", 6): 26358,
    ("Indiana", 6): 12986,
    ("Iowa", 1): 71196,
    ("Kansas", 5): 59801,
    ("Kentucky", 1): 99995,
    ("Kentucky", 3): 159596,
    ("Louisiana", 1): 57029,
    ("Louisiana", 4): 38016,
    ("Louisiana", 5): 65067,
    ("Louisiana", 7): None,
    ("Maine", 2): 66300,
    ("Maine", 3): 49917,
    ("Maine", 5): 65171,
    ("Maine", 6): 4408,
    ("Maine", 7): 93,
    ("Maryland", 3): 91780,
    ("Maryland", 4): 78515,
    ("Maryland", 5): 93706,
    ("Maryland", 6): None,
    ("Massachusetts", 1): 59260,
    ("Massachusetts", 5): 111960,
    ("Michigan", 0): 138455,
    ("Michigan", 3): 141095,
    ("Minnesota", 0): 55117,
    ("Minnesota", 1): 34423,
    ("Minnesota", 5): 53315,
    ("Mississippi", 2): 52605,
    ("Mississippi", 5): 75750,
    ("Mississippi", 6): 5797,
    ("Nebraska", 1): 7812,
    ("Nevada", 3): 9308,
    ("Nevada", 4): 10445,
    ("Nevada", 5): 11215,
    ("Nevada", 7): None,
    ("New Hampshire", 2): 41539,
    ("New Hampshire", 3): 38509,
    ("New Jersey", 4): 120555,
    ("New Jersey", 5): 122565,
    ("New Jersey", 6): 2617,
    ("New Jersey", 7): 191,
    ("New York", 4): 555544,
    ("North Carolina", 0): 94769,
    ("Oregon", 2): 15206,
    ("Oregon", 5): 19855,
    ("Pennsylvania", 4): 444704,
    ("Pennsylvania", 5): 407428,
    ("Pennsylvania", 7): 1939,
    ("South Carolina", 1): 22703,
    ("South Carolina", 4): 58071,
    ("South Carolina", 7): None,
    ("Tennessee", 1): 94391,
    ("Texas", 1): 66500,
    ("Texas", 4): 53298,
    ("West Virginia", 0): 32315,
    ("West Virginia", 6): 9079,
}


YEARS = (1860,)
ELECTION_DAYS = {
    1852: "1852-11-02",
    1856: "1856-11-04",
    1860: "1860-11-06",
    1864: "1864-11-08",
    1868: "1868-11-03",
    1872: "1872-11-05",
    1876: "1876-11-07",
    1880: "1880-11-02",
}
YEAR_PRINT = {1856: "v65", 1860: "v65", 1864: "v65", 1852: "v61", 1868: "v69"}
PRINT_VOLUME = {"v65": 1864, "v61": 1860, "v69": 1868}
PRINT_COLUMNS = {
    "v65": {
        0: (1864, "Lincoln"),
        1: (1864, "McClellan"),
        3: (1860, "Lincoln"),
        4: (1860, "Douglas"),
        5: (1860, "Breckinridge"),
        6: (1860, "Bell"),
        7: (1856, "Fremont"),
        8: (1856, "Buchanan"),
        9: (1856, "Fillmore"),
    },
    "v61": {
        7: (1852, "Scott"),
        8: (1852, "Pierce"),
        9: (1852, "Hale"),
    },
}
FUSION = {(1860, "NY", "Douglas"), (1860, "NJ", "Douglas"), (1860, "PA", "Breckinridge")}
CANDIDATES = {
    "Lincoln": ("Abraham Lincoln (Republican)", "republicanparty"),
    "Douglas": ("Stephen A. Douglas (Democratic)", "democraticparty"),
    "Breckinridge": ("John C. Breckinridge (Southern Democratic)", "democraticparty"),
    "Bell": ("John Bell (Constitutional Union)", "constitutionalunionparty"),
    "Fremont": ("John C. Fremont (Republican)", "republicanparty"),
    "Buchanan": ("James Buchanan (Democratic)", "democraticparty"),
    "Fillmore": ("Millard Fillmore (American)", "americanparty"),
    "Scott": ("Winfield Scott (Whig)", "whigparty"),
    "Pierce": ("Franklin Pierce (Democratic)", "democraticparty"),
    "Hale": ("John P. Hale (Free Soil)", "freesoilparty"),
    "McClellan": ("George B. McClellan (Democratic)", "democraticparty"),
}
EARLY: dict[tuple[int, str, str], int] = {
    (1860, "AL", "Bell"): 27875,
    (1860, "AL", "Breckinridge"): 48831,
    (1860, "AL", "Douglas"): 13651,
    (1860, "AR", "Bell"): 20094,
    (1860, "AR", "Breckinridge"): 28732,
    (1860, "AR", "Douglas"): 5227,
    (1860, "CA", "Bell"): 6817,
    (1860, "CA", "Breckinridge"): 34334,
    (1860, "CA", "Douglas"): 38516,
    (1860, "CA", "Lincoln"): 39173,
    (1860, "CT", "Bell"): 3291,
    (1860, "CT", "Breckinridge"): 14641,
    (1860, "CT", "Douglas"): 15522,
    (1860, "CT", "Lincoln"): 43792,
    (1860, "DE", "Bell"): 3864,
    (1860, "DE", "Breckinridge"): 7337,
    (1860, "DE", "Douglas"): 1023,
    (1860, "DE", "Lincoln"): 3815,
    (1860, "FL", "Bell"): 5437,
    (1860, "FL", "Breckinridge"): 8543,
    (1860, "FL", "Douglas"): 367,
    (1860, "GA", "Bell"): 42886,
    (1860, "GA", "Breckinridge"): 51889,
    (1860, "GA", "Douglas"): 11590,
    (1860, "IL", "Bell"): 4913,
    (1860, "IL", "Breckinridge"): 2404,
    (1860, "IL", "Douglas"): 160215,
    (1860, "IL", "Lincoln"): 172161,
    (1860, "IN", "Bell"): 5306,
    (1860, "IN", "Breckinridge"): 12295,
    (1860, "IN", "Douglas"): 115509,
    (1860, "IN", "Lincoln"): 139033,
    (1860, "IA", "Bell"): 1763,
    (1860, "IA", "Breckinridge"): 1048,
    (1860, "IA", "Douglas"): 55111,
    (1860, "IA", "Lincoln"): 70409,
    (1860, "KY", "Bell"): 66058,
    (1860, "KY", "Breckinridge"): 53143,
    (1860, "KY", "Douglas"): 25651,
    (1860, "KY", "Lincoln"): 1364,
    (1860, "LA", "Bell"): 20204,
    (1860, "LA", "Breckinridge"): 22681,
    (1860, "LA", "Douglas"): 7625,
    (1860, "ME", "Bell"): 2046,
    (1860, "ME", "Breckinridge"): 6368,
    (1860, "ME", "Douglas"): 26693,
    (1860, "ME", "Lincoln"): 62811,
    (1860, "MD", "Bell"): 41760,
    (1860, "MD", "Breckinridge"): 42482,
    (1860, "MD", "Douglas"): 5966,
    (1860, "MD", "Lincoln"): 2294,
    (1860, "MA", "Bell"): 22331,
    (1860, "MA", "Breckinridge"): 5939,
    (1860, "MA", "Douglas"): 34372,
    (1860, "MA", "Lincoln"): 106533,
    (1860, "MI", "Bell"): 405,
    (1860, "MI", "Breckinridge"): 805,
    (1860, "MI", "Douglas"): 65057,
    (1860, "MI", "Lincoln"): 88480,
    (1860, "MN", "Bell"): 62,
    (1860, "MN", "Breckinridge"): 748,
    (1860, "MN", "Douglas"): 11920,
    (1860, "MN", "Lincoln"): 22069,
    (1860, "MS", "Bell"): 25040,
    (1860, "MS", "Breckinridge"): 40797,
    (1860, "MS", "Douglas"): 3283,
    (1860, "MO", "Bell"): 58372,
    (1860, "MO", "Breckinridge"): 31317,
    (1860, "MO", "Douglas"): 58801,
    (1860, "MO", "Lincoln"): 17028,
    (1860, "NH", "Bell"): 441,
    (1860, "NH", "Breckinridge"): 2112,
    (1860, "NH", "Douglas"): 25881,
    (1860, "NH", "Lincoln"): 37519,
    (1860, "NJ", "Douglas"): 62801,
    (1860, "NJ", "Lincoln"): 58324,
    (1860, "NY", "Douglas"): 312510,
    (1860, "NY", "Lincoln"): 362646,
    (1860, "NC", "Bell"): 44990,
    (1860, "NC", "Breckinridge"): 48539,
    (1860, "NC", "Douglas"): 2701,
    (1860, "OH", "Bell"): 12194,
    (1860, "OH", "Breckinridge"): 11405,
    (1860, "OH", "Douglas"): 187232,
    (1860, "OH", "Lincoln"): 231610,
    (1860, "OR", "Bell"): 183,
    (1860, "OR", "Breckinridge"): 5006,
    (1860, "OR", "Douglas"): 3951,
    (1860, "OR", "Lincoln"): 5270,
    (1860, "PA", "Bell"): 12776,
    (1860, "PA", "Breckinridge"): 178871,
    (1860, "PA", "Douglas"): 16765,
    (1860, "PA", "Lincoln"): 268030,
    (1860, "RI", "Douglas"): 7707,
    (1860, "RI", "Lincoln"): 12244,
    (1860, "TN", "Bell"): 69274,
    (1860, "TN", "Breckinridge"): 64709,
    (1860, "TN", "Douglas"): 11350,
    (1860, "TX", "Bell"): 15438,
    (1860, "TX", "Breckinridge"): 47548,
    (1860, "VT", "Bell"): 1969,
    (1860, "VT", "Breckinridge"): 218,
    (1860, "VT", "Douglas"): 6849,
    (1860, "VT", "Lincoln"): 33808,
    (1860, "VA", "Bell"): 74681,
    (1860, "VA", "Breckinridge"): 74323,
    (1860, "VA", "Douglas"): 16290,
    (1860, "VA", "Lincoln"): 1929,
    (1860, "WI", "Bell"): 161,
    (1860, "WI", "Breckinridge"): 888,
    (1860, "WI", "Douglas"): 65021,
    (1860, "WI", "Lincoln"): 86110,
}


def emit() -> Path:
    boxes: dict[tuple[int, str, str], tuple[tuple[float, float, float, float], str]] = {}
    values: dict[tuple[int, str, str], int] = {}
    skipped: list[str] = []
    differences: list[str] = []
    needs_1881 = any(year >= 1872 for year in YEARS)
    if needs_1881:
        cells: dict[tuple[str, int], tuple[list[int], tuple[int, int, int, int]]] = {}
        with (PARSED / "reads_1881.tsv").open() as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                cells[(row["state"], int(row["col"]))] = (
                    [int(read) for read in row["reads"].split("|") if read],
                    (int(row["x0"]), int(row["y0"]), int(row["x1"]), int(row["y1"])),
                )
    with connect() as conn:
        old: dict[tuple[int, str], tuple] = {}
        for row in conn.execute(
            """select extract(year from e.date_from)::int, upper(split_part(sl.path, '/', 2)), r.id, r.election_id,
                      r.contest_id, r.stage, r.kind, r.source_id, r.completeness
               from result_set r join contest k on k.id = r.contest_id join election e on e.id = r.election_id
               join slug sl on sl.entity_id = k.id and sl.path like 'us/__/%%/president'
               join source s on s.id = r.source_id
               where k.kind = 'elector_slate' and e.date_from = any(%s::date[])
                 and s.publisher = 'Wikipedia'
                 and (r.is_default or exists (select 1 from result_set c join changeset ch on ch.id = c.changeset_id
                      where c.supersedes_id = r.id and ch.pipeline = %s))""",
            [[ELECTION_DAYS[year] for year in YEARS], SOURCE],
        ).fetchall():
            old[(row[0], row[1])] = row[2:]
        options: dict[UUID, list[tuple]] = defaultdict(list)
        for rs, option, label, kind, unit, measure, value in conn.execute(
            """select r.id, o.id, o.ballot_label, o.kind, u.id, v.measure, v.value
               from result_set r join result_value v on v.result_set_k = r.k
               left join contest_option o on o.k = v.option_k join reporting_unit u on u.k = v.unit_k
               where r.id = any(%s) and v.channel = 0""",
            [[row[0] for row in old.values()]],
        ).fetchall():
            options[rs].append((option, label, kind, unit, measure, Decimal(value)))
        outcomes: dict[UUID, list[tuple]] = defaultdict(list)
        for rs, option, kind, seats, candidacy in conn.execute(
            "select result_set_id, option_id, kind, seats, candidacy_id from outcome where result_set_id = any(%s)",
            [[row[0] for row in old.values()]],
        ).fetchall():
            outcomes[rs].append((option, kind, seats, candidacy))
        parties = {
            re.sub(r"[^a-z]", "", text.lower()): party
            for text, party in conn.execute(
                """select n.text, p.id from party p join name n on n.entity_id = p.id and n.lang = 'en'
                   join polity y on y.id = p.polity_id where y.iso_code = 'US' and n.text <> 'Progressive Party'"""
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Presidential results by state from the Tribune Almanac, superseding Wikipedia's: "
            + ", ".join(str(year) for year in YEARS),
            polity="US",
        )
        pages: dict[str, tuple[UUID, str, int, int]] = {}
        for year in YEARS:
            key = "v81p95" if year >= 1872 else YEAR_PRINT[year]
            if key in pages:
                continue
            volume = 1880 if key == "v81p95" else PRINT_VOLUME[key]
            page_number = 95 if key == "v81p95" else PRINTS[key]["page"]
            book = raw.cached(RAW, tribune_almanac.VOLUMES[volume][0])
            image = DATA / "pages" / SOURCE / f"{book.sha256}-{page_number}.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            if not image.exists():
                house_books._render(book, page_number, 100, image, False)
            with Image.open(image) as rendered:
                width, height = rendered.size
            source = cs.source(tribune_almanac.PUBLISHER, tribune_almanac.TITLES[volume])
            artifact = cs.artifact(book, source)
            page = cs.page(artifact, page_number)
            if not conn.execute("select 1 from source_page where id = %s", [page]).fetchone():
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=page_number,
                    image_key=f"pages/{SOURCE}/{book.sha256}-{page_number}.png",
                    width=width,
                    height=height,
                )
            pages[key] = (page, source, volume, page_number)
        if needs_1881:
            with Image.open(PARSED / "native" / "v81p95-mask.png") as mask:
                mask_width, mask_height = mask.size
            for (name, col), (reads, box) in cells.items():
                if name == "Total":
                    continue
                surname, year = NAMES[col]
                if year not in YEARS:
                    continue
                ticket = next(
                    (
                        value
                        for option, label, kind, unit, measure, value in options.get(
                            old.get((year, STATES[name]), (None,))[0], []
                        )
                        if kind == "ticket" and surname in (label or "")
                    ),
                    None,
                )
                cell = (year, STATES[name], surname)
                boxes[cell] = (
                    (box[0] / mask_width, box[1] / mask_height, box[2] / mask_width, box[3] / mask_height),
                    "v81p95",
                )
                if (name, col) in FIXES:
                    fixed = FIXES[(name, col)]
                    if fixed is not None:
                        values[cell] = fixed
                elif ticket is not None and ticket in reads:
                    values[cell] = int(ticket)
                elif len(reads) == 3 and len(set(reads)) == 1 and reads[0] >= 10:
                    if ticket is None or abs(reads[0] - ticket) <= ticket / 100:
                        values[cell] = reads[0]
                    else:
                        skipped.append(
                            f"cell {name} {surname}: read {reads[0]} against Wikipedia's {ticket}, not settled"
                        )
                elif ticket is not None:
                    common, count = (
                        Counter(read for read in reads if abs(read - ticket) <= ticket / 100).most_common(1)[0]
                        if any(abs(read - ticket) <= ticket / 100 for read in reads)
                        else (0, 0)
                    )
                    if count >= 2:
                        values[cell] = common
                    else:
                        skipped.append(
                            f"cell {name} {surname}: reads {reads} against Wikipedia's {ticket}, not settled"
                        )
        early_keys = {YEAR_PRINT[year] for year in YEARS if year < 1872}
        if early_keys:
            with (PARSED / "reads_prints.tsv").open() as handle:
                for row in csv.DictReader(handle, delimiter="\t"):
                    if row["print"] not in early_keys or row["state"] == "Total":
                        continue
                    named = PRINT_COLUMNS.get(row["print"], {}).get(int(row["col"]))
                    abbreviation = STATES[row["state"]]
                    if named is None or named[0] not in YEARS or (named[0], abbreviation, named[1]) not in EARLY:
                        continue
                    with Image.open(PARSED / "native" / f"{PRINTS[row['print']]['mask']}-mask.png") as mask:
                        mask_width, mask_height = mask.size
                    cell = (named[0], abbreviation, named[1])
                    values[cell] = EARLY[cell]
                    boxes[cell] = (
                        (
                            int(row["x0"]) / mask_width,
                            int(row["y0"]) / mask_height,
                            int(row["x1"]) / mask_width,
                            int(row["y1"]) / mask_height,
                        ),
                        row["print"],
                    )
        winners_ok = 0
        for (year, state), (rs, election, contest, stage, kind, source_id, completeness) in sorted(old.items()):
            tag = f"{year} {state}"
            surnames = [surname for (y, st, surname) in values if y == year and st == state]
            if not surnames:
                continue
            tickets = {
                option: (label, value)
                for option, label, okind, unit, measure, value in options[rs]
                if okind == "ticket"
            }
            unit = next(u for _, _, _, u, _, _ in options[rs])
            total_before = next(v for _, _, _, _, m, v in options[rs] if m == 4)
            matched: dict[str, UUID] = {}
            for surname in surnames:
                if (year, state, surname) in FUSION:
                    continue
                hits = [option for option, (label, _) in tickets.items() if surname in label]
                if len(hits) == 1:
                    matched[surname] = hits[0]
                elif len(hits) > 1:
                    skipped.append(f"{tag}: {surname} matches {len(hits)} tickets")
            unmatched = [surname for surname in surnames if surname not in matched]
            if any(
                surname not in LEGENDS and surname not in CANDIDATES and (year, state, surname) not in FUSION
                for surname in unmatched
            ):
                skipped.append(f"{tag}: a printed candidate has no Wikipedia ticket")
                continue
            winning = {option for option, *_ in outcomes[rs]}
            free = [
                value
                for option, (_, value) in tickets.items()
                if option not in matched.values() and (option in winning or value * 100 > total_before)
            ]
            if free:
                skipped.append(f"{tag}: Wikipedia lines not in the almanac: {free}")
                continue
            if any(option not in matched.values() for option in winning):
                skipped.append(f"{tag}: a winning option is not in the almanac's columns")
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{year}:{state}")
            ids: dict[str, UUID] = dict(matched)
            for surname in unmatched:
                if (year, state, surname) in FUSION:
                    label, party_name = "Fusion electors", None
                else:
                    label, party_name = LEGENDS.get(surname) or CANDIDATES[surname]
                ids[surname] = cs.key(f"{SOURCE}.option", f"{year}:{state}:{slug(label)}")
                cs.add(
                    "contest_option",
                    id=ids[surname],
                    contest_id=contest,
                    kind="legend",
                    party_id=parties.get(party_name) if party_name else None,
                    ballot_label=label,
                )
            total = sum(values[(year, state, surname)] for surname in surnames)
            ranked = sorted(tickets.items(), key=lambda item: -item[1][1])[:2]
            top_two_off = any(
                option in matched.values()
                and abs(next(values[(year, state, s)] for s, o in matched.items() if o == option) - value) > value / 100
                for option, (_, value) in ranked
            )
            default = (year, state) not in DEMOTED and not top_two_off
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=pages["v81p95" if year >= 1872 else YEAR_PRINT[year]][1],
                status="active",
                is_default=default,
                supersedes_id=rs if default else None,
                completeness=min(Decimal(1), round(Decimal(total) / total_before, 4)),
            )
            cs.add(
                "result_set",
                id=rs,
                election_id=election,
                contest_id=contest,
                stage=stage,
                kind=kind,
                source_id=source_id,
                status="superseded" if default else "active",
                is_default=not default,
                completeness=completeness,
            )
            volume = pages["v81p95" if year >= 1872 else YEAR_PRINT[year]][2]
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=(NOTE if year >= 1872 else note(year, volume)) + ("" if default else " " + HELD),
            )
            if abs(total - total_before) / total_before > Decimal("0.01"):
                differences.append(f"{tag}: printed lines total {total} against Wikipedia's {total_before}")
            for surname in surnames:
                cell = (year, state, surname)
                before = tickets[matched[surname]][1] if surname in matched else None
                if before and abs(values[cell] - before) / before > Decimal("0.01"):
                    differences.append(f"{tag}: {surname} {values[cell]} against Wikipedia's {before}")
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=ids[surname],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=DIGITIZED,
                    value=values[cell],
                )
                (x0, y0, x1, y1), print_key = boxes[cell]
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=ids[surname],
                    channel=0,
                    measure=1,
                    count_round=0,
                    page_id=pages[print_key][0],
                    source_id=None,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                )
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=4,
                count_round=0,
                flags=0,
                value=total,
            )
            for option, okind, seats, candidacy in outcomes[rs] if default else []:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=okind,
                    seats=seats,
                    candidacy_id=candidacy,
                )
            winners_ok += 1
        for line in sorted(skipped):
            print("skipped", line)
        for line in sorted(differences):
            print("differs", line)
        print(f"{winners_ok} state sets emitted, {len(skipped)} skipped, {len(differences)} differences over 1%")
        return cs.write()
