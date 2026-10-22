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
V69_ROWS = [s for s in ORDER if s != "Colorado"]
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
        rows=V69_ROWS,
        y0=1291,
        pitch=28.42,
        total=2352,
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
    "om254": dict(
        mask="om254",
        page=254,
        sha="390fdb7684afb596b0eb8d472ccc6e0d65186cdb0096f3a113e2a80ea0c5e5be",
        rows=FORTIES,
        y0=2065,
        pitch=36.2,
        total=3163,
        cols=[
            (685, 835),
            (838, 1000),
            (1003, 1097),
            (1115, 1275),
            (1278, 1435),
            (1438, 1566),
            (1574, 1736),
            (1739, 1899),
            (1902, 2046),
        ],
        scales=((1, "7"), (2, "7")),
    ),
    "om254t": dict(
        mask="om254",
        page=254,
        sha="390fdb7684afb596b0eb8d472ccc6e0d65186cdb0096f3a113e2a80ea0c5e5be",
        rows=EARLY,
        y0=905,
        pitch=36.3,
        total=1865,
        cols=[
            (675, 805),
            (808, 935),
            (938, 1053),
            (1056, 1168),
            (1182, 1315),
            (1318, 1448),
            (1465, 1605),
            (1608, 1740),
            (1755, 1895),
            (1898, 2035),
        ],
        scales=((1, "7"), (2, "7")),
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


YEARS = (1824,)
ELECTION_DAYS = {
    1824: "1824-10-26",
    1828: "1828-10-31",
    1832: "1832-11-02",
    1836: "1836-11-03",
    1840: "1840-10-30",
    1844: "1844-11-01",
    1848: "1848-11-07",
    1852: "1852-11-02",
    1856: "1856-11-04",
    1860: "1860-11-06",
    1864: "1864-11-08",
    1868: "1868-11-03",
    1872: "1872-11-05",
    1876: "1876-11-07",
    1880: "1880-11-02",
}
YEAR_PRINT = {1856: "v65", 1860: "v65", 1864: "v65", 1852: "v61", 1868: "v69", 1848: "om254", 1844: "om254", 1840: "om254", 1836: "om254t", 1832: "om254t", 1828: "om254t", 1824: "om254t"}
PRINT_VOLUME = {"v65": 1864, "v61": 1860, "v69": 1868, "om254": 1876, "om254t": 1876}
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
    "v69": {
        0: (1868, "Grant"),
        1: (1868, "Seymour"),
    },
    "om254t": {
        0: (1824, "Adams"),
        1: (1824, "Jackson"),
        2: (1824, "Crawford"),
        3: (1824, "Clay"),
        4: (1828, "Adams"),
        5: (1828, "Jackson"),
        6: (1832, "Clay"),
        7: (1832, "Jackson"),
        8: (1836, "Harrison"),
        9: (1836, "Van Buren"),
    },
    "om254": {
        6: (1848, "Taylor"),
        7: (1848, "Cass"),
        8: (1848, "Van Buren"),
        0: (1840, "Harrison"),
        1: (1840, "Van Buren"),
        2: (1840, "Birney"),
        3: (1844, "Clay"),
        4: (1844, "Polk"),
        5: (1844, "Birney"),
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
    "Grant": ("Ulysses S. Grant (Republican)", "republicanparty"),
    "Seymour": ("Horatio Seymour (Democratic)", "democraticparty"),
    "Taylor": ("Zachary Taylor (Whig)", "whigparty"),
    "Cass": ("Lewis Cass (Democratic)", "democraticparty"),
    "Van Buren": ("Martin Van Buren (Free Soil)", "freesoilparty"),
    "Clay": ("Henry Clay (Whig)", "whigparty"),
    "Polk": ("James K. Polk (Democratic)", "democraticparty"),
    "Birney": ("James G. Birney (Liberty)", "libertyparty"),
    "Harrison": ("William Henry Harrison (Whig)", "whigparty"),
    "Jackson": ("Andrew Jackson (Democratic)", "democraticparty"),
    "Adams": ("John Quincy Adams (National Republican)", "nationalrepublicanparty"),
    "Crawford": ("William Crawford (Democratic-Republican)", "democraticrepublicanparty"),
}
EARLY: dict[tuple[int, str, str], int] = {
    (1868, "AL", "Grant"): 76366,
    (1868, "AL", "Seymour"): 72086,
    (1868, "AR", "Grant"): 22152,
    (1868, "AR", "Seymour"): 19078,
    (1868, "CA", "Grant"): 54592,
    (1868, "CA", "Seymour"): 54078,
    (1868, "CT", "Grant"): 50641,
    (1868, "CT", "Seymour"): 47600,
    (1868, "DE", "Grant"): 7623,
    (1868, "DE", "Seymour"): 10980,
    (1868, "GA", "Grant"): 57134,
    (1868, "GA", "Seymour"): 102822,
    (1868, "IA", "Grant"): 120399,
    (1868, "IA", "Seymour"): 74040,
    (1868, "IL", "Grant"): 250293,
    (1868, "IL", "Seymour"): 199143,
    (1868, "IN", "Grant"): 176552,
    (1868, "IN", "Seymour"): 166980,
    (1868, "KS", "Grant"): 31049,
    (1868, "KS", "Seymour"): 14019,
    (1868, "KY", "Grant"): 39566,
    (1868, "KY", "Seymour"): 115889,
    (1868, "LA", "Grant"): 33263,
    (1868, "LA", "Seymour"): 80225,
    (1868, "MA", "Grant"): 136477,
    (1868, "MA", "Seymour"): 59408,
    (1868, "MD", "Grant"): 30438,
    (1868, "MD", "Seymour"): 62357,
    (1868, "ME", "Grant"): 70426,
    (1868, "ME", "Seymour"): 42396,
    (1868, "MI", "Grant"): 128550,
    (1868, "MI", "Seymour"): 97069,
    (1868, "MN", "Grant"): 43542,
    (1868, "MN", "Seymour"): 28072,
    (1868, "MO", "Grant"): 85671,
    (1868, "MO", "Seymour"): 59788,
    (1868, "NC", "Grant"): 96226,
    (1868, "NC", "Seymour"): 84090,
    (1868, "NE", "Grant"): 9729,
    (1868, "NE", "Seymour"): 5439,
    (1868, "NH", "Grant"): 38191,
    (1868, "NH", "Seymour"): 31224,
    (1868, "NJ", "Grant"): 80121,
    (1868, "NJ", "Seymour"): 83001,
    (1868, "NV", "Grant"): 10000,
    (1868, "NV", "Seymour"): 8600,
    (1868, "NY", "Grant"): 419883,
    (1868, "NY", "Seymour"): 429883,
    (1868, "OH", "Grant"): 280128,
    (1868, "OH", "Seymour"): 238700,
    (1868, "OR", "Grant"): 10961,
    (1868, "OR", "Seymour"): 11125,
    (1868, "PA", "Grant"): 342280,
    (1868, "PA", "Seymour"): 313382,
    (1868, "RI", "Grant"): 12993,
    (1868, "RI", "Seymour"): 6548,
    (1868, "SC", "Grant"): 62301,
    (1868, "SC", "Seymour"): 45237,
    (1868, "TN", "Grant"): 56757,
    (1868, "TN", "Seymour"): 26311,
    (1868, "VT", "Grant"): 44167,
    (1868, "VT", "Seymour"): 12045,
    (1868, "WI", "Grant"): 108857,
    (1868, "WI", "Seymour"): 84710,
    (1868, "WV", "Grant"): 29025,
    (1868, "WV", "Seymour"): 20306,
    (1852, "AL", "Scott"): 15038,
    (1852, "AL", "Pierce"): 26881,
    (1852, "AR", "Scott"): 7404,
    (1852, "AR", "Pierce"): 12173,
    (1852, "CA", "Scott"): 34971,
    (1852, "CA", "Pierce"): 39665,
    (1852, "CA", "Hale"): 100,
    (1852, "CT", "Scott"): 30359,
    (1852, "CT", "Pierce"): 33249,
    (1852, "CT", "Hale"): 3160,
    (1852, "DE", "Scott"): 6293,
    (1852, "DE", "Pierce"): 6318,
    (1852, "DE", "Hale"): 62,
    (1852, "FL", "Scott"): 2875,
    (1852, "FL", "Pierce"): 4318,
    (1852, "GA", "Scott"): 16660,
    (1852, "GA", "Pierce"): 34705,
    (1852, "IA", "Scott"): 15856,
    (1852, "IA", "Pierce"): 17763,
    (1852, "IA", "Hale"): 1604,
    (1852, "IL", "Scott"): 64934,
    (1852, "IL", "Pierce"): 80597,
    (1852, "IL", "Hale"): 9966,
    (1852, "IN", "Scott"): 80901,
    (1852, "IN", "Pierce"): 95340,
    (1852, "IN", "Hale"): 6929,
    (1852, "KY", "Scott"): 57068,
    (1852, "KY", "Pierce"): 53806,
    (1852, "KY", "Hale"): 265,
    (1852, "LA", "Scott"): 17255,
    (1852, "LA", "Pierce"): 18647,
    (1852, "MA", "Scott"): 52683,
    (1852, "MA", "Pierce"): 44569,
    (1852, "MA", "Hale"): 28023,
    (1852, "MD", "Scott"): 35066,
    (1852, "MD", "Pierce"): 40020,
    (1852, "MD", "Hale"): 54,
    (1852, "ME", "Scott"): 32543,
    (1852, "ME", "Pierce"): 41609,
    (1852, "ME", "Hale"): 8030,
    (1852, "MI", "Scott"): 33859,
    (1852, "MI", "Pierce"): 41842,
    (1852, "MI", "Hale"): 7237,
    (1852, "MO", "Scott"): 29984,
    (1852, "MO", "Pierce"): 38353,
    (1852, "MS", "Scott"): 17548,
    (1852, "MS", "Pierce"): 26876,
    (1852, "NC", "Scott"): 39058,
    (1852, "NC", "Pierce"): 39744,
    (1852, "NC", "Hale"): 59,
    (1852, "NH", "Scott"): 16147,
    (1852, "NH", "Pierce"): 29997,
    (1852, "NH", "Hale"): 6695,
    (1852, "NJ", "Scott"): 38556,
    (1852, "NJ", "Pierce"): 44305,
    (1852, "NJ", "Hale"): 350,
    (1852, "NY", "Scott"): 234882,
    (1852, "NY", "Pierce"): 262083,
    (1852, "NY", "Hale"): 25329,
    (1852, "OH", "Scott"): 152526,
    (1852, "OH", "Pierce"): 169220,
    (1852, "OH", "Hale"): 31682,
    (1852, "PA", "Scott"): 179174,
    (1852, "PA", "Pierce"): 198568,
    (1852, "PA", "Hale"): 8525,
    (1852, "RI", "Scott"): 7626,
    (1852, "RI", "Pierce"): 8735,
    (1852, "RI", "Hale"): 644,
    (1852, "TN", "Scott"): 58898,
    (1852, "TN", "Pierce"): 57018,
    (1852, "TX", "Scott"): 4995,
    (1852, "TX", "Pierce"): 13552,
    (1852, "VA", "Scott"): 58572,
    (1852, "VA", "Pierce"): 73858,
    (1852, "VT", "Scott"): 22173,
    (1852, "VT", "Pierce"): 13044,
    (1852, "VT", "Hale"): 8621,
    (1852, "WI", "Scott"): 22240,
    (1852, "WI", "Pierce"): 33658,
    (1852, "WI", "Hale"): 8814,
    (1856, "AL", "Buchanan"): 46739,
    (1856, "AL", "Fillmore"): 28552,
    (1856, "AR", "Buchanan"): 21910,
    (1856, "AR", "Fillmore"): 10787,
    (1856, "CA", "Fremont"): 20691,
    (1856, "CA", "Buchanan"): 53365,
    (1856, "CA", "Fillmore"): 36165,
    (1856, "CT", "Fremont"): 42715,
    (1856, "CT", "Buchanan"): 34995,
    (1856, "CT", "Fillmore"): 2615,
    (1856, "DE", "Fremont"): 308,
    (1856, "DE", "Buchanan"): 8004,
    (1856, "DE", "Fillmore"): 6175,
    (1856, "FL", "Buchanan"): 6358,
    (1856, "FL", "Fillmore"): 4833,
    (1856, "GA", "Buchanan"): 56578,
    (1856, "GA", "Fillmore"): 42228,
    (1856, "IL", "Fremont"): 96200,
    (1856, "IL", "Buchanan"): 105298,
    (1856, "IL", "Fillmore"): 37454,
    (1856, "IN", "Fremont"): 94375,
    (1856, "IN", "Buchanan"): 118670,
    (1856, "IN", "Fillmore"): 22386,
    (1856, "IA", "Fremont"): 43954,
    (1856, "IA", "Buchanan"): 36170,
    (1856, "IA", "Fillmore"): 9180,
    (1856, "KY", "Fremont"): 314,
    (1856, "KY", "Buchanan"): 74642,
    (1856, "KY", "Fillmore"): 67416,
    (1856, "LA", "Buchanan"): 22164,
    (1856, "LA", "Fillmore"): 20709,
    (1856, "ME", "Fremont"): 67179,
    (1856, "ME", "Buchanan"): 3980,
    (1856, "ME", "Fillmore"): 3325,
    (1856, "MD", "Fremont"): 281,
    (1856, "MD", "Buchanan"): 39115,
    (1856, "MD", "Fillmore"): 47460,
    (1856, "MA", "Fremont"): 108515,
    (1856, "MA", "Buchanan"): 39287,
    (1856, "MA", "Fillmore"): 19679,
    (1856, "MI", "Fremont"): 71762,
    (1856, "MI", "Buchanan"): 52136,
    (1856, "MI", "Fillmore"): 1660,
    (1856, "MS", "Buchanan"): 35447,
    (1856, "MS", "Fillmore"): 24196,
    (1856, "MO", "Buchanan"): 58164,
    (1856, "MO", "Fillmore"): 48524,
    (1856, "NH", "Fremont"): 38345,
    (1856, "NH", "Buchanan"): 32789,
    (1856, "NH", "Fillmore"): 422,
    (1856, "NJ", "Fremont"): 28338,
    (1856, "NJ", "Buchanan"): 46943,
    (1856, "NJ", "Fillmore"): 24115,
    (1856, "NY", "Fremont"): 276007,
    (1856, "NY", "Buchanan"): 195878,
    (1856, "NY", "Fillmore"): 124604,
    (1856, "NC", "Buchanan"): 48246,
    (1856, "NC", "Fillmore"): 36886,
    (1856, "OH", "Fremont"): 187497,
    (1856, "OH", "Buchanan"): 170874,
    (1856, "OH", "Fillmore"): 28126,
    (1856, "PA", "Fremont"): 148272,
    (1856, "PA", "Buchanan"): 230772,
    (1856, "PA", "Fillmore"): 82202,
    (1856, "RI", "Fremont"): 11467,
    (1856, "RI", "Buchanan"): 6580,
    (1856, "RI", "Fillmore"): 1675,
    (1856, "TN", "Buchanan"): 73638,
    (1856, "TN", "Fillmore"): 66178,
    (1856, "TX", "Buchanan"): 31169,
    (1856, "TX", "Fillmore"): 15639,
    (1856, "VT", "Fremont"): 39563,
    (1856, "VT", "Buchanan"): 10569,
    (1856, "VT", "Fillmore"): 545,
    (1856, "VA", "Fremont"): 291,
    (1856, "VA", "Buchanan"): 89706,
    (1856, "VA", "Fillmore"): 60310,
    (1856, "WI", "Fremont"): 66090,
    (1856, "WI", "Buchanan"): 52843,
    (1856, "WI", "Fillmore"): 579,
    (1864, "CA", "Lincoln"): 58698,
    (1864, "CA", "McClellan"): 42255,
    (1864, "CT", "Lincoln"): 44691,
    (1864, "CT", "McClellan"): 42285,
    (1864, "DE", "Lincoln"): 8155,
    (1864, "DE", "McClellan"): 8767,
    (1864, "IL", "Lincoln"): 189496,
    (1864, "IL", "McClellan"): 158730,
    (1864, "IN", "Lincoln"): 150238,
    (1864, "IN", "McClellan"): 130233,
    (1864, "IA", "Lincoln"): 89075,
    (1864, "IA", "McClellan"): 49596,
    (1864, "KS", "Lincoln"): 16441,
    (1864, "KS", "McClellan"): 3691,
    (1864, "KY", "Lincoln"): 26592,
    (1864, "KY", "McClellan"): 61478,
    (1864, "ME", "Lincoln"): 61803,
    (1864, "ME", "McClellan"): 44211,
    (1864, "MD", "Lincoln"): 40153,
    (1864, "MD", "McClellan"): 32739,
    (1864, "MA", "Lincoln"): 126742,
    (1864, "MA", "McClellan"): 48745,
    (1864, "MI", "Lincoln"): 85352,
    (1864, "MI", "McClellan"): 67370,
    (1864, "MN", "Lincoln"): 25060,
    (1864, "MN", "McClellan"): 17375,
    (1864, "MO", "Lincoln"): 71676,
    (1864, "MO", "McClellan"): 31626,
    (1864, "NV", "Lincoln"): 9826,
    (1864, "NV", "McClellan"): 6594,
    (1864, "NH", "Lincoln"): 36400,
    (1864, "NH", "McClellan"): 32871,
    (1864, "NJ", "Lincoln"): 60723,
    (1864, "NJ", "McClellan"): 68024,
    (1864, "NY", "Lincoln"): 368735,
    (1864, "NY", "McClellan"): 361986,
    (1864, "OH", "Lincoln"): 264975,
    (1864, "OH", "McClellan"): 205557,
    (1864, "OR", "Lincoln"): 9888,
    (1864, "OR", "McClellan"): 8457,
    (1864, "PA", "Lincoln"): 296391,
    (1864, "PA", "McClellan"): 276316,
    (1864, "RI", "Lincoln"): 13692,
    (1864, "RI", "McClellan"): 8470,
    (1864, "VT", "Lincoln"): 42419,
    (1864, "VT", "McClellan"): 13321,
    (1864, "WV", "Lincoln"): 23152,
    (1864, "WV", "McClellan"): 10438,
    (1864, "WI", "Lincoln"): 83458,
    (1864, "WI", "McClellan"): 65884,
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
    (1848, "AL", "Taylor"): 30482,
    (1848, "AL", "Cass"): 31363,
    (1848, "AR", "Taylor"): 7588,
    (1848, "AR", "Cass"): 9300,
    (1848, "CT", "Taylor"): 30314,
    (1848, "CT", "Cass"): 27046,
    (1848, "CT", "Van Buren"): 5005,
    (1848, "DE", "Taylor"): 6421,
    (1848, "DE", "Cass"): 5898,
    (1848, "DE", "Van Buren"): 80,
    (1848, "FL", "Taylor"): 3116,
    (1848, "FL", "Cass"): 1847,
    (1848, "GA", "Taylor"): 47544,
    (1848, "GA", "Cass"): 44802,
    (1848, "IL", "Taylor"): 53047,
    (1848, "IL", "Cass"): 56300,
    (1848, "IL", "Van Buren"): 15774,
    (1848, "IN", "Taylor"): 69907,
    (1848, "IN", "Cass"): 74745,
    (1848, "IN", "Van Buren"): 8100,
    (1848, "IA", "Taylor"): 11084,
    (1848, "IA", "Cass"): 12093,
    (1848, "IA", "Van Buren"): 1126,
    (1848, "KY", "Taylor"): 67141,
    (1848, "KY", "Cass"): 49720,
    (1848, "LA", "Taylor"): 18217,
    (1848, "LA", "Cass"): 15370,
    (1848, "ME", "Taylor"): 35125,
    (1848, "ME", "Cass"): 39880,
    (1848, "ME", "Van Buren"): 12096,
    (1848, "MD", "Taylor"): 37702,
    (1848, "MD", "Cass"): 34528,
    (1848, "MD", "Van Buren"): 125,
    (1848, "MA", "Taylor"): 61070,
    (1848, "MA", "Cass"): 35281,
    (1848, "MA", "Van Buren"): 38058,
    (1848, "MI", "Taylor"): 23940,
    (1848, "MI", "Cass"): 30687,
    (1848, "MI", "Van Buren"): 10389,
    (1848, "MS", "Taylor"): 25922,
    (1848, "MS", "Cass"): 26537,
    (1848, "MO", "Taylor"): 32671,
    (1848, "MO", "Cass"): 40077,
    (1848, "NH", "Taylor"): 14781,
    (1848, "NH", "Cass"): 27763,
    (1848, "NH", "Van Buren"): 7560,
    (1848, "NJ", "Taylor"): 40015,
    (1848, "NJ", "Cass"): 36901,
    (1848, "NJ", "Van Buren"): 829,
    (1848, "NY", "Taylor"): 218603,
    (1848, "NY", "Cass"): 114318,
    (1848, "NY", "Van Buren"): 120510,
    (1848, "NC", "Taylor"): 43550,
    (1848, "NC", "Cass"): 34869,
    (1848, "OH", "Taylor"): 138360,
    (1848, "OH", "Cass"): 154775,
    (1848, "OH", "Van Buren"): 35354,
    (1848, "PA", "Taylor"): 185513,
    (1848, "PA", "Cass"): 171176,
    (1848, "PA", "Van Buren"): 11263,
    (1848, "RI", "Taylor"): 6779,
    (1848, "RI", "Cass"): 3646,
    (1848, "RI", "Van Buren"): 730,
    (1848, "TN", "Taylor"): 64705,
    (1848, "TN", "Cass"): 58419,
    (1848, "TX", "Taylor"): 4509,
    (1848, "TX", "Cass"): 10668,
    (1848, "VT", "Taylor"): 23122,
    (1848, "VT", "Cass"): 10948,
    (1848, "VT", "Van Buren"): 13837,
    (1848, "VA", "Taylor"): 45124,
    (1848, "VA", "Cass"): 46586,
    (1848, "VA", "Van Buren"): 9,
    (1848, "WI", "Taylor"): 13747,
    (1848, "WI", "Cass"): 15001,
    (1848, "WI", "Van Buren"): 10418,
    (1844, "AL", "Clay"): 26084,
    (1844, "AL", "Polk"): 37740,
    (1844, "AR", "Clay"): 5504,
    (1844, "AR", "Polk"): 9546,
    (1844, "CT", "Clay"): 32832,
    (1844, "CT", "Polk"): 29841,
    (1844, "CT", "Birney"): 1943,
    (1844, "DE", "Clay"): 6278,
    (1844, "DE", "Polk"): 5996,
    (1844, "GA", "Clay"): 42100,
    (1844, "GA", "Polk"): 44177,
    (1844, "IL", "Clay"): 45528,
    (1844, "IL", "Polk"): 57920,
    (1844, "IL", "Birney"): 3570,
    (1844, "IN", "Clay"): 67867,
    (1844, "IN", "Polk"): 70181,
    (1844, "IN", "Birney"): 2106,
    (1844, "KY", "Clay"): 61255,
    (1844, "KY", "Polk"): 51988,
    (1844, "LA", "Clay"): 13083,
    (1844, "LA", "Polk"): 13782,
    (1844, "ME", "Clay"): 34378,
    (1844, "ME", "Polk"): 45719,
    (1844, "ME", "Birney"): 4836,
    (1844, "MD", "Clay"): 35984,
    (1844, "MD", "Polk"): 32676,
    (1844, "MA", "Clay"): 67418,
    (1844, "MA", "Polk"): 52846,
    (1844, "MA", "Birney"): 10860,
    (1844, "MI", "Clay"): 24337,
    (1844, "MI", "Polk"): 27759,
    (1844, "MI", "Birney"): 3632,
    (1844, "MS", "Clay"): 19206,
    (1844, "MS", "Polk"): 25126,
    (1844, "MO", "Clay"): 31251,
    (1844, "MO", "Polk"): 41369,
    (1844, "NH", "Clay"): 17866,
    (1844, "NH", "Polk"): 27160,
    (1844, "NH", "Birney"): 4161,
    (1844, "NJ", "Clay"): 38318,
    (1844, "NJ", "Polk"): 37495,
    (1844, "NJ", "Birney"): 131,
    (1844, "NY", "Clay"): 232482,
    (1844, "NY", "Polk"): 237588,
    (1844, "NY", "Birney"): 15812,
    (1844, "NC", "Clay"): 43232,
    (1844, "NC", "Polk"): 39287,
    (1844, "OH", "Clay"): 155057,
    (1844, "OH", "Polk"): 149117,
    (1844, "OH", "Birney"): 8050,
    (1844, "PA", "Clay"): 161203,
    (1844, "PA", "Polk"): 167535,
    (1844, "PA", "Birney"): 3138,
    (1844, "RI", "Clay"): 7322,
    (1844, "RI", "Polk"): 4867,
    (1844, "RI", "Birney"): 107,
    (1844, "TN", "Clay"): 60030,
    (1844, "TN", "Polk"): 59917,
    (1844, "VT", "Clay"): 26770,
    (1844, "VT", "Polk"): 18041,
    (1844, "VT", "Birney"): 3954,
    (1844, "VA", "Clay"): 43677,
    (1844, "VA", "Polk"): 49570,
    (1840, "AL", "Harrison"): 28471,
    (1840, "AL", "Van Buren"): 33991,
    (1840, "AR", "Harrison"): 5160,
    (1840, "AR", "Van Buren"): 6049,
    (1840, "CT", "Harrison"): 31601,
    (1840, "CT", "Van Buren"): 25296,
    (1840, "CT", "Birney"): 174,
    (1840, "DE", "Harrison"): 5967,
    (1840, "DE", "Van Buren"): 4884,
    (1840, "GA", "Harrison"): 40261,
    (1840, "GA", "Van Buren"): 31933,
    (1840, "IL", "Harrison"): 45537,
    (1840, "IL", "Van Buren"): 47476,
    (1840, "IL", "Birney"): 149,
    (1840, "IN", "Harrison"): 65302,
    (1840, "IN", "Van Buren"): 51695,
    (1840, "KY", "Harrison"): 58489,
    (1840, "KY", "Van Buren"): 32616,
    (1840, "LA", "Harrison"): 11296,
    (1840, "LA", "Van Buren"): 7617,
    (1840, "ME", "Harrison"): 46612,
    (1840, "ME", "Van Buren"): 46201,
    (1840, "ME", "Birney"): 194,
    (1840, "MD", "Harrison"): 33528,
    (1840, "MD", "Van Buren"): 28752,
    (1840, "MA", "Harrison"): 72874,
    (1840, "MA", "Van Buren"): 51948,
    (1840, "MA", "Birney"): 1621,
    (1840, "MI", "Harrison"): 22933,
    (1840, "MI", "Van Buren"): 21098,
    (1840, "MI", "Birney"): 321,
    (1840, "MS", "Harrison"): 19518,
    (1840, "MS", "Van Buren"): 16995,
    (1840, "MO", "Harrison"): 22972,
    (1840, "MO", "Van Buren"): 29760,
    (1840, "NH", "Harrison"): 26158,
    (1840, "NH", "Van Buren"): 32670,
    (1840, "NH", "Birney"): 126,
    (1840, "NJ", "Harrison"): 33351,
    (1840, "NJ", "Van Buren"): 31034,
    (1840, "NJ", "Birney"): 69,
    (1840, "NY", "Harrison"): 225817,
    (1840, "NY", "Van Buren"): 212519,
    (1840, "NY", "Birney"): 2798,
    (1840, "NC", "Harrison"): 46376,
    (1840, "NC", "Van Buren"): 34218,
    (1840, "OH", "Harrison"): 148157,
    (1840, "OH", "Van Buren"): 124782,
    (1840, "OH", "Birney"): 903,
    (1840, "PA", "Harrison"): 144021,
    (1840, "PA", "Van Buren"): 143676,
    (1840, "PA", "Birney"): 343,
    (1840, "RI", "Harrison"): 5278,
    (1840, "RI", "Van Buren"): 3301,
    (1840, "RI", "Birney"): 42,
    (1840, "TN", "Harrison"): 60391,
    (1840, "TN", "Van Buren"): 48289,
    (1840, "VT", "Harrison"): 32440,
    (1840, "VT", "Van Buren"): 18009,
    (1840, "VT", "Birney"): 319,
    (1840, "VA", "Harrison"): 42501,
    (1840, "VA", "Van Buren"): 43893,
    (1836, "AL", "Harrison"): 15637,
    (1836, "AL", "Van Buren"): 19068,
    (1836, "AR", "Harrison"): 1238,
    (1836, "AR", "Van Buren"): 2400,
    (1836, "CT", "Harrison"): 18466,
    (1836, "CT", "Van Buren"): 19234,
    (1836, "DE", "Harrison"): 4738,
    (1836, "DE", "Van Buren"): 4155,
    (1836, "GA", "Harrison"): 24930,
    (1836, "GA", "Van Buren"): 22126,
    (1836, "IL", "Harrison"): 14983,
    (1836, "IL", "Van Buren"): 18097,
    (1836, "IN", "Harrison"): 41281,
    (1836, "IN", "Van Buren"): 32480,
    (1836, "KY", "Harrison"): 36955,
    (1836, "KY", "Van Buren"): 33435,
    (1836, "LA", "Harrison"): 3383,
    (1836, "LA", "Van Buren"): 3653,
    (1836, "ME", "Harrison"): 15239,
    (1836, "ME", "Van Buren"): 22300,
    (1836, "MD", "Harrison"): 25852,
    (1836, "MD", "Van Buren"): 22167,
    (1836, "MA", "Harrison"): 41093,
    (1836, "MA", "Van Buren"): 33501,
    (1836, "MI", "Harrison"): 4000,
    (1836, "MI", "Van Buren"): 7360,
    (1836, "MS", "Harrison"): 9688,
    (1836, "MS", "Van Buren"): 9979,
    (1836, "MO", "Harrison"): 8337,
    (1836, "MO", "Van Buren"): 10995,
    (1836, "NH", "Harrison"): 6228,
    (1836, "NH", "Van Buren"): 18722,
    (1836, "NJ", "Harrison"): 26892,
    (1836, "NJ", "Van Buren"): 26347,
    (1836, "NY", "Harrison"): 138543,
    (1836, "NY", "Van Buren"): 166815,
    (1836, "NC", "Harrison"): 23626,
    (1836, "NC", "Van Buren"): 26910,
    (1836, "OH", "Harrison"): 105405,
    (1836, "OH", "Van Buren"): 96948,
    (1836, "PA", "Harrison"): 87111,
    (1836, "PA", "Van Buren"): 91475,
    (1836, "RI", "Harrison"): 2710,
    (1836, "RI", "Van Buren"): 2964,
    (1836, "TN", "Harrison"): 35962,
    (1836, "TN", "Van Buren"): 26120,
    (1836, "VT", "Harrison"): 20991,
    (1836, "VT", "Van Buren"): 14037,
    (1836, "VA", "Harrison"): 23368,
    (1836, "VA", "Van Buren"): 30261,
    (1832, "CT", "Clay"): 17755,
    (1832, "CT", "Jackson"): 11269,
    (1832, "DE", "Clay"): 4276,
    (1832, "DE", "Jackson"): 4110,
    (1832, "GA", "Jackson"): 20750,
    (1832, "IL", "Clay"): 5429,
    (1832, "IL", "Jackson"): 14147,
    (1832, "IN", "Clay"): 15472,
    (1832, "IN", "Jackson"): 31552,
    (1832, "KY", "Clay"): 43396,
    (1832, "KY", "Jackson"): 36247,
    (1832, "LA", "Clay"): 2528,
    (1832, "LA", "Jackson"): 4049,
    (1832, "ME", "Clay"): 27204,
    (1832, "ME", "Jackson"): 33291,
    (1832, "MD", "Clay"): 19160,
    (1832, "MD", "Jackson"): 19156,
    (1832, "MA", "Clay"): 33003,
    (1832, "MA", "Jackson"): 14545,
    (1832, "MS", "Jackson"): 5919,
    (1832, "MO", "Jackson"): 5192,
    (1832, "NH", "Clay"): 19010,
    (1832, "NH", "Jackson"): 25486,
    (1832, "NJ", "Clay"): 23393,
    (1832, "NJ", "Jackson"): 23856,
    (1832, "NY", "Clay"): 154896,
    (1832, "NY", "Jackson"): 168497,
    (1832, "NC", "Clay"): 4563,
    (1832, "NC", "Jackson"): 24862,
    (1832, "OH", "Clay"): 76539,
    (1832, "OH", "Jackson"): 81246,
    (1832, "PA", "Clay"): 56716,
    (1832, "PA", "Jackson"): 90983,
    (1832, "RI", "Clay"): 2810,
    (1832, "RI", "Jackson"): 2126,
    (1832, "TN", "Clay"): 1436,
    (1832, "TN", "Jackson"): 28740,
    (1832, "VT", "Clay"): 11152,
    (1832, "VT", "Jackson"): 7870,
    (1832, "VA", "Clay"): 11451,
    (1832, "VA", "Jackson"): 33609,
    (1828, "CT", "Adams"): 13829,
    (1828, "CT", "Jackson"): 4448,
    (1828, "DE", "Adams"): 4769,
    (1828, "DE", "Jackson"): 4349,
    (1828, "GA", "Jackson"): 18709,
    (1828, "IL", "Adams"): 1581,
    (1828, "IL", "Jackson"): 6763,
    (1828, "IN", "Adams"): 17052,
    (1828, "IN", "Jackson"): 22237,
    (1828, "KY", "Adams"): 31172,
    (1828, "KY", "Jackson"): 39084,
    (1828, "LA", "Adams"): 4097,
    (1828, "LA", "Jackson"): 4605,
    (1828, "ME", "Adams"): 20773,
    (1828, "ME", "Jackson"): 13927,
    (1828, "MD", "Adams"): 25759,
    (1828, "MD", "Jackson"): 24578,
    (1828, "MA", "Adams"): 29836,
    (1828, "MA", "Jackson"): 6019,
    (1828, "MS", "Adams"): 1581,
    (1828, "MS", "Jackson"): 6763,
    (1828, "MO", "Adams"): 3422,
    (1828, "MO", "Jackson"): 8232,
    (1828, "NH", "Adams"): 24076,
    (1828, "NH", "Jackson"): 20692,
    (1828, "NJ", "Adams"): 23758,
    (1828, "NJ", "Jackson"): 21950,
    (1828, "NY", "Adams"): 135413,
    (1828, "NY", "Jackson"): 140763,
    (1828, "NC", "Adams"): 13918,
    (1828, "NC", "Jackson"): 37857,
    (1828, "OH", "Adams"): 63396,
    (1828, "OH", "Jackson"): 67597,
    (1828, "PA", "Adams"): 50848,
    (1828, "PA", "Jackson"): 101652,
    (1828, "RI", "Adams"): 2754,
    (1828, "RI", "Jackson"): 821,
    (1828, "TN", "Adams"): 2240,
    (1828, "TN", "Jackson"): 44090,
    (1828, "VT", "Adams"): 24784,
    (1828, "VT", "Jackson"): 8205,
    (1828, "VA", "Adams"): 12101,
    (1828, "VA", "Jackson"): 26752,
    (1828, "AL", "Adams"): 1938,
    (1828, "AL", "Jackson"): 17138,
    (1824, "AL", "Adams"): 2416,
    (1824, "AL", "Jackson"): 9443,
    (1824, "AL", "Crawford"): 1680,
    (1824, "AL", "Clay"): 67,
    (1824, "CT", "Adams"): 7587,
    (1824, "CT", "Crawford"): 1978,
    (1824, "IL", "Adams"): 1542,
    (1824, "IL", "Jackson"): 1901,
    (1824, "IL", "Crawford"): 219,
    (1824, "IL", "Clay"): 1047,
    (1824, "IN", "Adams"): 3095,
    (1824, "IN", "Jackson"): 7343,
    (1824, "IN", "Clay"): 5315,
    (1824, "KY", "Jackson"): 6453,
    (1824, "KY", "Clay"): 16782,
    (1824, "ME", "Adams"): 6870,
    (1824, "ME", "Jackson"): 2330,
    (1824, "MD", "Adams"): 14632,
    (1824, "MD", "Jackson"): 14523,
    (1824, "MD", "Crawford"): 3646,
    (1824, "MD", "Clay"): 695,
    (1824, "MA", "Adams"): 30687,
    (1824, "MA", "Crawford"): 6616,
    (1824, "MS", "Adams"): 1694,
    (1824, "MS", "Jackson"): 3234,
    (1824, "MS", "Crawford"): 119,
    (1824, "MO", "Adams"): 311,
    (1824, "MO", "Jackson"): 987,
    (1824, "MO", "Clay"): 1401,
    (1824, "NH", "Adams"): 4107,
    (1824, "NH", "Jackson"): 643,
    (1824, "NJ", "Adams"): 9110,
    (1824, "NJ", "Jackson"): 10985,
    (1824, "NJ", "Crawford"): 1196,
    (1824, "NC", "Jackson"): 20415,
    (1824, "NC", "Crawford"): 15621,
    (1824, "OH", "Adams"): 12280,
    (1824, "OH", "Jackson"): 18457,
    (1824, "OH", "Clay"): 19255,
    (1824, "PA", "Adams"): 5440,
    (1824, "PA", "Jackson"): 36100,
    (1824, "PA", "Crawford"): 4206,
    (1824, "PA", "Clay"): 1609,
    (1824, "RI", "Adams"): 2145,
    (1824, "RI", "Crawford"): 200,
    (1824, "TN", "Adams"): 216,
    (1824, "TN", "Jackson"): 20197,
    (1824, "TN", "Crawford"): 312,
    (1824, "VA", "Adams"): 3189,
    (1824, "VA", "Jackson"): 2861,
    (1824, "VA", "Crawford"): 8489,
    (1824, "VA", "Clay"): 416,
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
        for key in {YEAR_PRINT[year] for year in YEARS if year < 1872}:
            spec = PRINTS[key]
            with Image.open(PARSED / "native" / f"{spec['mask']}-mask.png") as mask:
                mask_width, mask_height = mask.size
            half = round(spec["pitch"] * 0.42)
            for col, (year, surname) in PRINT_COLUMNS[key].items():
                if year not in YEARS:
                    continue
                left = spec["cols"][col - 1][1] + 2 if col > 0 else spec["cols"][col][0] - 60
                for index, name in enumerate(spec["rows"]):
                    cell = (year, STATES[name], surname)
                    if cell not in EARLY:
                        continue
                    center = round(spec["y0"] + spec["pitch"] * index)
                    values[cell] = EARLY[cell]
                    boxes[cell] = (
                        (
                            left / mask_width,
                            (center - half) / mask_height,
                            (spec["cols"][col][1] + 4) / mask_width,
                            (center + half) / mask_height,
                        ),
                        key,
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
