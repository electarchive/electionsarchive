import csv
import difflib
import io
import itertools
import json
import re
import statistics
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import numpy as np
import polars as pl
from PIL import Image, ImageChops, ImageFilter, ImageOps

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.mo_manual"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Missouri Secretary of State"
BOOK = "Official Manual of the State of Missouri"
IMAGE = "https://mdh.contentdm.oclc.org/iiif/2/bluebook:{}/full/full/0/default.jpg"
TABLES: list[tuple[str, str, list[tuple[int, float, float, float, float]], list[tuple[str, ...]]]] = [
    (
        "1914-11-03",
        "senate",
        [(9882, 0, 1, 0, 1), (9883, 0, 1, 0, 1), (9884, 0, 0.82, 0, 1)],
        [("DEM", "Stone"), ("REP", "Akins"), ("PRG", "Sager"), ("PRO", "Hill"), ("SOC", "Greene"), ("SLP", "Molineux")],
    ),
    (
        "1920-11-02",
        "senate",
        [(11842, 0.38, 1, 0, 1), (11843, 0.1, 0.79, 0, 1)],
        [("REP", "Spencer"), ("DEM", "Long"), ("SOC", "Hodges"), ("SLP", "Trudell"), ("FL", "Mallett")],
    ),
    (
        "1926-11-02",
        "senate",
        [(13612, 0.2, 1, 0, 1), (13613, 0.17, 0.6, 0, 1)],
        [
            ("REP", "Williams", "senate:special"),
            ("DEM", "Hawes", "senate:special"),
            ("REP", "Williams"),
            ("DEM", "Hawes"),
            ("SOC", "Morrison"),
            ("SLP", "Cox"),
            ("PRO", "Faris"),
        ],
    ),
    (
        "1932-11-08",
        "senate",
        [(17054, 0.58, 1, 0, 1), (17055, 0.1, 0.96, 0, 1)],
        [
            ("DEM", "Clark"),
            ("REP", "Kiel"),
            ("SOC", "Hodges"),
            ("SLP", "Oberheu"),
            ("PRO", "Faris"),
            ("COM", "Pollack"),
        ],
    ),
    (
        "1934-11-06",
        "senate",
        [(18050, 0, 1, 0, 1), (18051, 0, 0.52, 0, 1)],
        [("DEM", "Truman"), ("REP", "Patterson"), ("SOC", "Meyer"), ("SLP", "Cox"), ("COM", "Brown")],
    ),
    (
        "1938-11-08",
        "senate",
        [(19824, 0, 1, 0, 1), (19825, 0, 0.57, 0, 1)],
        [("DEM", "Clark"), ("REP", "Caulfield"), ("SOC", "Hodges"), ("SLP", "Oberheu")],
    ),
    (
        "1940-11-05",
        "senate",
        [(22001, 0.75, 1, 0, 1), (22002, 0, 1, 0, 1), (22003, 0.17, 0.38, 0, 1)],
        [
            ("DEM", "Truman"),
            ("REP", "Davis"),
            ("SOC", "Rinck"),
            ("SLP", "Baeff"),
        ],
    ),
    (
        "1946-11-05",
        "senate",
        [(24882, 0, 1, 0, 1), (24883, 0, 0.6, 0, 1)],
        [("DEM", "Briggs"), ("REP", "Kem"), ("SOC", "Rinck"), ("PRO", "Jackson"), ("SLP", "Baef")],
    ),
    (
        "1950-11-07",
        "senate",
        [(27535, 0, 1, 0, 1), (27536, 0, 0.58, 0, 1)],
        [("DEM", "Hennings"), ("REP", "Donnell"), ("SLP", "Genck"), ("CN", "Hamilton")],
    ),
    (
        "1956-11-06",
        "senate",
        [(31683, 0, 0.85, 0, 0.5), (31683, 0, 0.85, 0.5, 1)],
        [("DEM", "Hennings"), ("REP", "Douglas")],
    ),
    (
        "1958-11-04",
        "senate",
        [(33254, 0, 0.85, 0, 0.53), (33254, 0, 0.85, 0.53, 1)],
        [("DEM", "Symington"), ("REP", "Palmer")],
    ),
    (
        "1900-11-06",
        "president",
        [(4237, 0.18, 0.96, 0, 1), (4238, 0.14, 0.8, 0, 1)],
        [
            ("DEM", "Bryan"),
            ("REP", "McKinley"),
            ("PPL", "Barker"),
            ("PRO", "Woolley"),
            ("SOC", "Debs"),
            ("SLP", "Malloney"),
        ],
    ),
    (
        "1912-11-05",
        "president",
        [(8996, 0.12, 0.96, 0, 1), (8997, 0.11, 0.62, 0, 1)],
        [
            ("DEM", "Wilson"),
            ("REP", "Taft"),
            ("PRO", "Chafin"),
            ("SOC", "Debs"),
            ("SLP", "Reimer"),
            ("PRG", "Roosevelt"),
        ],
    ),
    (
        "1936-11-03",
        "president",
        [(18904, 0.14, 0.96, 0, 1), (18905, 0.11, 0.55, 0, 1)],
        [
            ("DEM", "Roosevelt"),
            ("REP", "Landon"),
            ("SOC", "Thomas"),
            ("SLP", "Aiken"),
            ("PRO", "Colvin"),
            ("COM", "Browder"),
            ("UNI", "Lemke"),
        ],
    ),
    (
        "1948-11-02",
        "president",
        [(26104, 0.5, 0.94, 0, 1), (26105, 0.11, 0.92, 0, 1)],
        [("DEM", "Truman"), ("REP", "Dewey"), ("SOC", "Thomas"), ("WPRG", "Wallace")],
    ),
    (
        "1956-11-06",
        "president",
        [(31682, 0.13, 0.87, 0, 0.49), (31682, 0.13, 0.87, 0.49, 1)],
        [("DEM", "Stevenson"), ("REP", "Eisenhower")],
    ),
]
CHECKED: set[tuple[str, str, int]] = {
    ("1926-11-02", "senate", 0),
    ("1926-11-02", "senate", 1),
    ("1938-11-08", "senate", 3),
}
NOTES: list[tuple[str, str, str]] = []
FIXES: dict[tuple[str, str, str, int], int] = {
    ("1934-11-06", "senate", "29123", 2): 5,
    ("1934-11-06", "senate", "29153", 2): 14,
    ("1934-11-06", "senate", "29063", 3): 1,
    ("1934-11-06", "senate", "29085", 3): 1,
    ("1934-11-06", "senate", "29121", 3): 1,
    ("1934-11-06", "senate", "29151", 3): 1,
}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "SOC": "socialist-party-of-america",
    "SLP": "socialist-labor-party-of-america",
    "PRO": "prohibition-party",
    "PPL": "peoples-party",
    "PRG": "progressive-party-1912",
    "WPRG": "MO:progressive-party",
    "LIB": "progressive-party-1924",
    "COM": "MO:communist",
    "UNI": "union-party-of-1936",
    "CN": "MO:christian-nationalist",
    "SDEM": "MO:social-democrat",
    "FW": "MO:farmer-work",
    "NDEM": None,
    "NPRO": None,
    "FL": None,
    "CWL": None,
    "AF": None,
    "NP": None,
    "MO:SOC": "MO:socialist",
    "MO:SLP": "MO:socialist-labor",
}
NAMES: dict[tuple[str, str, str], str] = {
    ("1892-11-08", "Weaver", "PPL"): "James B. Weaver",
    ("1892-11-08", "Bidwell", "PRO"): "John Bidwell",
    ("1896-11-03", "Bryan", "DEM"): "William Jennings Bryan",
    ("1896-11-03", "McKinley", "REP"): "William McKinley",
    ("1896-11-03", "Levering", "PRO"): "Joshua Levering",
    ("1896-11-03", "Matchett", "SLP"): "Charles H. Matchett",
    ("1896-11-03", "Palmer", "NDEM"): "John M. Palmer",
    ("1896-11-03", "Bentley", "NPRO"): "Charles E. Bentley",
    ("1900-11-06", "Bryan", "DEM"): "William Jennings Bryan",
    ("1900-11-06", "McKinley", "REP"): "William McKinley",
    ("1900-11-06", "Barker", "PPL"): "Wharton Barker",
    ("1900-11-06", "Woolley", "PRO"): "John G. Woolley",
    ("1900-11-06", "Debs", "SOC"): "Eugene V. Debs",
    ("1900-11-06", "Malloney", "SLP"): "Joseph F. Malloney",
    ("1904-11-08", "Parker", "DEM"): "Alton B. Parker",
    ("1904-11-08", "Roosevelt", "REP"): "Theodore Roosevelt",
    ("1904-11-08", "Swallow", "PRO"): "Silas C. Swallow",
    ("1904-11-08", "Debs", "SOC"): "Eugene V. Debs",
    ("1904-11-08", "Corrigan", "SLP"): "Charles H. Corregan",
    ("1904-11-08", "Watson", "PPL"): "Thomas E. Watson",
    ("1924-11-04", "Wallace", "CWL"): "William J. Wallace",
    ("1932-11-08", "Reynolds", "SLP"): "Verne L. Reynolds",
    ("1932-11-08", "Upshaw", "PRO"): "William D. Upshaw",
    ("1932-11-08", "Foster", "COM"): "William Z. Foster",
    ("1936-11-03", "Aiken", "SLP"): "John W. Aiken",
    ("1936-11-03", "Colvin", "PRO"): "D. Leigh Colvin",
    ("1936-11-03", "Browder", "COM"): "Earl Browder",
    ("1940-11-05", "Aiken", "SLP"): "John W. Aiken",
    ("1940-11-05", "Babson", "PRO"): "Roger W. Babson",
    ("1944-11-07", "Teichert", "SLP"): "Edward A. Teichert",
    ("1944-11-07", "Watson", "PRO"): "Claude A. Watson",
    ("1952-11-04", "Hoopes", "SOC"): "Darlington Hoopes",
    ("1952-11-04", "MacArthur", "CN"): "Douglas MacArthur",
    ("1952-11-04", "MacArthur", "AF"): "Douglas MacArthur",
}
LANGUAGES = ("eng", "deu", "ita", "spa", "nld", "por", "swe", "pol", "frk")
DEBUG: set[str] = set()


def fetch() -> None:
    for page in sorted({segment[0] for _, _, segments, _ in TABLES for segment in segments}):
        raw.fetch(SOURCE, IMAGE.format(page), suffix=".jpg")


def words(page: int) -> dict[str, list[tuple[float, float, float, str]]]:
    clean = PARSED / "ocr" / f"{page}-clean.png"
    if not clean.exists():
        pixels = np.array(
            ImageOps.autocontrast(Image.open(raw.cached(SOURCE, IMAGE.format(page)).path).convert("L"), cutoff=2)
        )
        dark = pixels < 215
        above = np.zeros(dark.shape, dtype=np.int32)
        below = np.zeros(dark.shape, dtype=np.int32)
        for y in range(1, dark.shape[0]):
            above[y] = (above[y - 1] + 1) * dark[y]
        for y in range(dark.shape[0] - 2, -1, -1):
            below[y] = (below[y + 1] + 1) * dark[y]
        rule = above + below > 80
        rule = rule | np.roll(rule, 1, 1) | np.roll(rule, -1, 1) | np.roll(rule, 2, 1) | np.roll(rule, -2, 1)
        pixels[rule & (pixels > 100)] = 255
        Image.fromarray(pixels).save(clean)
    source = Image.open(clean)
    double = source.resize((source.width * 2, source.height * 2), Image.Resampling.LANCZOS)
    triple = source.resize((source.width * 3, source.height * 3), Image.Resampling.LANCZOS)
    variants = {
        "x2unsharp": (2.0, double.filter(ImageFilter.UnsharpMask(4, 200, 2))),
        "x2": (2.0, double),
        "x3sharp": (3.0, triple.filter(ImageFilter.SHARPEN)),
        "x2local": (
            2.0,
            ImageChops.subtract(double.filter(ImageFilter.GaussianBlur(40)), double).point(
                lambda value: 255 - min(255, value * 4)
            ),
        ),
        "plain": (1.0, source.filter(ImageFilter.SHARPEN)),
    }
    found: dict[str, list[tuple[float, float, float, str]]] = {}
    for mode, (scale, picture) in variants.items():
        cache = PARSED / "ocr" / f"{page}-{mode}.tsv"
        if not cache.exists():
            buffer = io.BytesIO()
            picture.save(buffer, "PNG")
            cache.write_text(
                subprocess.run(
                    ["tesseract", "stdin", "stdout", "--psm", "6", "tsv"],
                    input=buffer.getvalue(),
                    capture_output=True,
                    check=True,
                ).stdout.decode()
            )
        found[mode] = [
            (
                (int(row["left"]) + int(row["width"])) / scale,
                (int(row["top"]) + int(row["height"]) / 2) / scale,
                int(row["left"]) / scale,
                row["text"].strip(),
            )
            for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE)
            if (row["text"] or "").strip()
        ]
    return found


def segment(
    page: int,
    box: tuple[float, float, float, float],
    expected: dict[str, list[int]],
    order: list[str],
) -> dict[str, tuple[int, list[list[int]]]]:
    found = words(page)
    picture = Image.open(PARSED / "ocr" / f"{page}-clean.png")
    top, bottom = box[0] * picture.height, box[1] * picture.height
    left, right = box[2] * picture.width, box[3] * picture.width
    tokens: list[tuple[float, float, int]] = []
    for found_words in found.values():
        pieces = sorted(
            (
                (start, right_edge, middle, re.sub(r"(?<=\d)\.(?=\d{3})", ",", raw_text.strip("|)]}!'([{.")))
                for right_edge, middle, start, raw_text in found_words
                if top <= middle <= bottom and left <= start and right_edge <= right
            )
        )
        runs: list[list[tuple[float, float, float, str]]] = []
        for piece in pieces:
            run = next(
                (
                    run
                    for run in reversed(runs)
                    if re.fullmatch(r"[\d,]+", piece[3])
                    and re.fullmatch(r"[\d,]+", run[-1][3])
                    and -2 < piece[0] - run[-1][1] < 30
                    and abs(piece[2] - run[-1][2]) < (14 if "," in (piece[3], run[-1][3]) else 8)
                ),
                None,
            )
            if run is None:
                runs.append([piece])
            else:
                run.append(piece)
        for run in runs:
            joined = "".join(piece[3] for piece in run).strip(",")
            group = (
                [(run[-1][1], run[-1][2], joined)]
                if len(run) > 1 and re.fullmatch(r"\d{1,3}(,\d{3})+", joined)
                else [(piece[1], piece[2], piece[3].strip(",")) for piece in run]
            )
            for right_edge, middle, text in group:
                if re.fullmatch(r"\d{1,3}(,\d{3})*", text):
                    tokens.append((right_edge, middle, int(text.replace(",", ""))))
    places: dict[int, list[tuple[float, float]]] = {}
    for x, y, value in tokens:
        places.setdefault(value, []).append((x, y))
    size = len(next(iter(expected.values())))

    def anchor(slope: float, tolerance: float) -> dict[str, tuple[float, list[tuple[int, float, float]]]]:
        anchored: dict[str, tuple[float, list[tuple[int, float, float]]]] = {}
        for county, values in expected.items():
            hits = [
                (column, x, y - slope * x)
                for column, value in enumerate(values)
                if value >= 10
                for x, y in places.get(value, [])
            ]
            if not hits:
                continue
            middle = max(
                (hit[2] for hit in hits),
                key=lambda y: len({column for column, _, other in hits if abs(other - y) <= tolerance}),
            )
            support = [hit for hit in hits if abs(hit[2] - middle) <= tolerance]
            columns = {column for column, _, _ in support}
            if len(columns) >= min(2, len([value for value in values if value >= 10])):
                anchored[county] = (statistics.median(y for _, _, y in support), support)
        taken: dict[int, str] = {}
        for county, (middle, support) in sorted(anchored.items(), key=lambda item: -len(item[1][1])):
            if any(abs(middle - other) < 10 for other in (anchored[one][0] for one in taken.values())):
                continue
            taken[len(taken)] = county
        return {county: anchored[county] for county in taken.values()}

    first = anchor(0.0, 14)
    slopes = [
        (b[2] - a[2]) / (b[1] - a[1])
        for _, support in first.values()
        for a, b in itertools.combinations(support, 2)
        if abs(b[1] - a[1]) > 200
    ]
    slope = statistics.median(slopes) if slopes else 0.0
    anchored = anchor(slope, 8)
    if len(anchored) < 3:
        return {}
    matched: dict[int, list[float]] = {}
    for county, (middle, _) in anchored.items():
        for column, value in enumerate(expected[county]):
            if value:
                matched.setdefault(column, []).extend(
                    x for x, y in places.get(value, []) if abs(y - slope * x - middle) <= 8
                )
    rough = {column: statistics.median(xs) for column, xs in matched.items() if xs}
    spacing = statistics.median(
        [
            rough[column + 1] - rough[column]
            for column in rough
            if column + 1 in rough and rough[column + 1] > rough[column]
        ]
        or [140]
    )
    spots: dict[int, float] = {}
    for column in sorted(rough):
        near = [
            x for x in matched[column] if column - 1 not in spots or abs(x - spots[column - 1] - spacing) <= spacing / 2
        ]
        if near:
            spots[column] = statistics.median(near)
    lines = {county: middle for county, (middle, _) in anchored.items()}
    indexes = sorted((order.index(county), middle) for county, middle in lines.items() if county in order[:-1])
    pitch = statistics.median((b[1] - a[1]) / (b[0] - a[0]) for a, b in itertools.pairwise(indexes) if b[0] > a[0])
    for (low, low_y), (high, high_y) in itertools.pairwise(indexes):
        if high - low > 1 and abs(high_y - low_y - (high - low) * pitch) < pitch * 0.4:
            for index in range(low + 1, high):
                lines[order[index]] = low_y + (index - low) * (high_y - low_y) / (high - low)
    for start, step in ((indexes[0], -1), (indexes[-1], 1)):
        for distance in (1, 2):
            index = start[0] + step * distance
            middle = start[1] + step * distance * pitch
            if (
                0 <= index < len(order)
                and order[index] not in lines
                and top <= middle + slope * left <= bottom
                and (
                    distance == 1
                    or any(
                        abs(x - spots.get(0, -99)) <= 14 and abs(y - slope * x - middle) <= pitch * 0.4
                        for x, y, _ in tokens
                    )
                )
            ):
                lines[order[index]] = middle
    labeled = [
        y - slope * x
        for found_words in found.values()
        for _, y, x, text in found_words
        if top <= y <= bottom and left <= x <= right and re.match(r"Totals?\b", text)
    ]
    if labeled and "TOTAL" not in lines:
        lines["TOTAL"] = statistics.median(labeled)
    if DEBUG and "rows" in DEBUG:
        print(
            page,
            slope,
            pitch,
            spots,
            [(order[index], round(middle)) for index, middle in indexes],
            sorted((round(y), c) for c, y in lines.items()),
        )
    gaps: list[list[int]] = []
    for column in range(size):
        if column not in spots:
            if gaps and gaps[-1][-1] == column - 1:
                gaps[-1].append(column)
            else:
                gaps.append([column])
    for run in gaps:
        low = spots.get(run[0] - 1, left) + 40
        high = (
            spots[run[-1] + 1] - 40
            if run[-1] + 1 in spots
            else spots[run[0] - 1] + spacing * (len(run) + 0.5)
            if run[0] - 1 in spots
            else right
        )
        xs = sorted(
            x
            for x, y, _ in tokens
            if low <= x <= high and any(abs(y - slope * x - middle) <= 8 for middle in lines.values())
        )
        clusters: list[list[float]] = []
        for x in xs:
            if clusters and x - clusters[-1][-1] <= 40:
                clusters[-1].append(x)
            else:
                clusters.append([x])
        clusters = sorted(sorted(clusters, key=len)[-len(run) :], key=lambda cluster: cluster[0])
        if len(clusters) == len(run) and all(len(cluster) >= 2 for cluster in clusters):
            for column, cluster in zip(run, clusters, strict=True):
                spots[column] = statistics.median(cluster)
    missing = [column for column in range(size) if column not in spots]
    if missing:
        raise ValueError(f"{page}: no column found for {missing}")
    cache = PARSED / "ocr" / f"{page}-{box[0]}-{box[1]}-{box[2]}-{box[3]}-cells.json"
    known: dict[str, list[str]] = json.loads(cache.read_text()) if cache.exists() else {}
    jobs: dict[str, list[tuple[bytes, str, str]]] = {}
    for middle in lines.values():
        for spot in spots.values():
            key = f"{round(spot)},{round(middle)}"
            if key in known or key in jobs:
                continue
            actual = middle + slope * spot
            crop = picture.crop((int(spot - 150), int(actual - 15), int(spot + 8), int(actual + 10)))
            ink = crop.tobytes()
            inked = [x for x in range(crop.width) if any(ink[y * crop.width + x] < 110 for y in range(6, 13))]
            group = [inked[-1]] if inked and inked[-1] >= crop.width - 30 else []
            for x in reversed(inked[:-1] if group else []):
                if group[-1] - x > 30:
                    break
                group.append(x)
            jobs[key] = []
            if group:
                tight = crop.crop((max(0, group[-1] - 4), 0, min(crop.width, group[0] + 5), crop.height))
                wide = crop.crop((max(0, group[-1] - 8), 0, min(crop.width, group[0] + 6), crop.height))
                double = tight.resize((tight.width * 2, tight.height * 2), Image.Resampling.LANCZOS)
                triple = tight.resize((tight.width * 3, tight.height * 3), Image.Resampling.LANCZOS)
                wide = wide.resize((wide.width * 3, wide.height * 3), Image.Resampling.LANCZOS)
                for variant, psm, language in (
                    (double.point(lambda value: 0 if value < 160 else 255), "7", "eng"),
                    (triple, "10", "eng"),
                    *((wide, "7", language) for language in LANGUAGES),
                ):
                    buffer = io.BytesIO()
                    ImageOps.expand(variant, 30, 255).save(buffer, "PNG")
                    jobs[key].append((buffer.getvalue(), psm, language))
    flat = [(key, index, job) for key, images in jobs.items() for index, job in enumerate(images)]
    batches = [
        chunk
        for _, group in itertools.groupby(sorted(flat, key=lambda item: item[2][1:]), key=lambda item: item[2][1:])
        for chunk in itertools.batched(group, 200)
    ]
    with tempfile.TemporaryDirectory() as folder:

        def read(batch: tuple[tuple[str, int, tuple[bytes, str, str]], ...]) -> list[str]:
            names = []
            for key, index, (image, _, _) in batch:
                path = Path(folder) / f"{key.replace(',', '-')}-{index}.png"
                path.write_bytes(image)
                names.append(str(path))
            listing = Path(folder) / f"{batch[0][0].replace(',', '-')}-{batch[0][1]}.txt"
            listing.write_text("\n".join(names) + "\n")
            _, psm, language = batch[0][2]
            output = subprocess.run(
                [
                    "tesseract",
                    str(listing),
                    "stdout",
                    "-l",
                    language,
                    "--psm",
                    psm,
                    "-c",
                    "tessedit_char_whitelist=0123456789,.",
                ],
                capture_output=True,
                check=True,
            ).stdout.decode()
            return [text.strip() for text in output.split("\f")][: len(batch)]

        with ThreadPoolExecutor(8) as pool:
            for batch, texts in zip(batches, pool.map(read, batches), strict=True):
                if len(texts) != len(batch):
                    raise ValueError(f"{page}: tesseract returned {len(texts)} of {len(batch)} cells")
                for (key, index, _), text in zip(batch, texts, strict=True):
                    known.setdefault(key, [""] * len(jobs[key]))[index] = text
    for key in jobs:
        known.setdefault(key, [])
    cache.write_text(json.dumps(known))
    cells: dict[str, tuple[int, list[list[int]]]] = {}
    for county, middle in lines.items():
        readings: list[list[int]] = []
        for column in range(size):
            spot = spots[column]
            cell = [
                value for x, y, value in tokens if abs(x - spot) <= 14 and abs(y - slope * x - middle) <= pitch * 0.4
            ]
            key = f"{round(spot)},{round(middle)}"
            for found_text in known[key]:
                text = re.sub(r"(?<=\d)\.(?=\d{3})", ",", found_text.strip(" .,"))
                if re.fullmatch(r"\d{1,3}(,\d{3})*", text):
                    cell.append(int(text.replace(",", "")))
            if DEBUG and county in DEBUG:
                print(page, county, column, cell, known[key])
            readings.append(cell)
        cells[county] = (len(anchored[county][1]) if county in anchored else 0, readings)
    return cells


def parse() -> None:
    (PARSED / "ocr").mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%' and u.code <> '29510'"""
            ).fetchall()
        }
    if len(codes) != 114:
        raise ValueError("Missouri needs 114 county names")
    jackson = codes["jackson"]
    base = [
        codes[key]
        for key in sorted(
            codes,
            key=lambda key: re.sub(
                r"^mc", "mac", re.sub(r"^ste?(?=charles|clair|genevieve|francois|louis)", "saint", key)
            ),
        )
    ] + ["29510", "TOTAL"]
    rows: list[dict[str, object]] = []
    caveats: list[dict[str, object]] = [{"day": day, "office": office, "note": note} for day, office, note in NOTES]
    for day, office, segments, candidates in TABLES:
        with connect() as conn:
            offices = [candidate[2] if len(candidate) > 2 else office for candidate in candidates]
            diagnostic: dict[tuple[str, int], int] = {}
            shared = Counter((offices[column], candidate[1]) for column, candidate in enumerate(candidates))
            for each in set(offices):
                for county, text, votes in conn.execute(
                    """select u.code, o.ballot_label, v.value::int from result_value v
                       join result_set rs on rs.k = v.result_set_k join source s on s.id = rs.source_id
                       join contest_option o on o.k = v.option_k join reporting_unit u on u.k = v.unit_k
                       join external_id x on x.entity_id = rs.contest_id and x.scheme = 'us.contest'
                       where x.value = %s and (s.title like '%%(ICPSR 1)' or s.title like '%%(ICPSR 13)')
                       and v.measure = 1""",
                    [f"{day}:MO:{each}"],
                ).fetchall():
                    words = [
                        word
                        for word in re.sub(r"\([^)]*\)|[^a-z ()]", "", (text or "").lower()).split()
                        if word not in {"jr", "sr", "ii", "iii"}
                    ] or [""]
                    surname = re.sub(r"[^a-z]", "", words[-1])
                    column = next(
                        (
                            column
                            for column, candidate in enumerate(candidates)
                            if offices[column] == each
                            and shared[(each, candidate[1])] == 1
                            and difflib.SequenceMatcher(
                                None, surname, re.sub(r"[^a-z]", "", candidate[1].lower())
                            ).ratio()
                            >= 0.8
                        ),
                        None,
                    )
                    if column is not None:
                        diagnostic[(county, column)] = diagnostic.get((county, column), 0) + votes
            totals: dict[int, int] = {}
            for each in set(offices):
                for text, votes in conn.execute(
                    """select o.ballot_label, v.value::int from result_value v
                       join result_set rs on rs.k = v.result_set_k join source s on s.id = rs.source_id
                       join contest_option o on o.k = v.option_k
                       join external_id x on x.entity_id = rs.contest_id and x.scheme = 'us.contest'
                       where x.value = %s and s.title like 'Statistics of the %%' and v.measure = 1""",
                    [f"{day}:MO:{each}"],
                ).fetchall():
                    words = [
                        word
                        for word in re.sub(r"\([^)]*\)|[^a-z ()]", "", (text or "").lower()).split()
                        if word not in {"jr", "sr", "ii", "iii"}
                    ] or [""]
                    surname = re.sub(r"[^a-z]", "", words[-1])
                    for column, candidate in enumerate(candidates):
                        if (
                            offices[column] == each
                            and difflib.SequenceMatcher(None, surname, candidate[1].lower()).ratio() >= 0.8
                        ):
                            totals[column] = votes
        informed = {column for _, column in diagnostic}
        expected = {
            county: [diagnostic.get((county, column), 0) for column in range(len(candidates))]
            for county in {county for county, _ in diagnostic}
        }
        expected["TOTAL"] = [
            totals.get(column, sum(values[column] for values in expected.values())) for column in range(len(candidates))
        ]
        if "29510" not in expected:
            expected.pop("29189", None)
        swapped = list(base)
        francois, genevieve = swapped.index(codes["stfrancois"]), swapped.index(codes["stegenevieve"])
        swapped[francois], swapped[genevieve] = swapped[genevieve], swapped[francois]
        readings: dict[str, tuple[int, list[list[int]]]] = {}
        missing: list[str] = []
        for order in (
            arrangement
            for each in (base, swapped)
            for arrangement in (each, each[: each.index(jackson) + 1] + ["KC"] + each[each.index(jackson) + 1 :])
        ):
            readings = {}
            for page, top, bottom, left, right in segments:
                for county, (support, cells) in segment(page, (top, bottom, left, right), expected, order).items():
                    if county not in readings or support > readings[county][0]:
                        readings[county] = (support, cells)
            missing = sorted((set(base) - set(readings)) - ({jackson} if "KC" in readings else set()))
            if not missing and (jackson in readings and readings[jackson][0] or "KC" in readings):
                break
        if missing:
            raise ValueError(f"{day} {office}: missing {missing}")
        order = sorted(set(readings) - {"TOTAL"})
        chosen: dict[str, list[int]] = {}
        for county, (_, cells) in readings.items():
            chosen[county] = []
            for column, cell in enumerate(cells):
                hint = (
                    diagnostic.get((county, column), 0)
                    if county in expected
                    and county != "TOTAL"
                    and column in informed
                    and not (county == jackson and "KC" in readings)
                    else None
                )
                majority, support = next(
                    ((value, count) for value, count in Counter(cell).most_common() if count >= 2), (0, 0)
                )
                chosen[county].append(hint if hint in cell or (hint == 0 and support <= 3) else majority)
        for (when, what, county, column), votes in FIXES.items():
            if (when, what) == (day, office):
                chosen[county][column] = votes
        for column, (_, label, *_) in enumerate(candidates):
            printed = {chosen["TOTAL"][column], *readings["TOTAL"][1][column]}
            total = sum(chosen[county][column] for county in order)
            if total in printed:
                continue
            gap = chosen["TOTAL"][column] - total
            others = sorted(
                {
                    (county, value)
                    for county in order
                    for value in [
                        *readings[county][1][column],
                        0,
                        *([expected[county][column]] if county in expected else []),
                    ]
                    if value != chosen[county][column] and (day, office, county, column) not in FIXES
                }
            )
            swaps = [
                picked
                for size in (1, 2)
                for picked in itertools.combinations(others, size)
                if len({county for county, _ in picked}) == size
                and sum(value - chosen[county][column] for county, value in picked) == gap
            ]
            if (day, office, column) in CHECKED or (
                column in informed
                and all(
                    chosen[county][column] == expected[county][column]
                    for county in order
                    if county in expected and not (county == jackson and "KC" in readings)
                )
            ):
                caveats.append(
                    {
                        "day": day,
                        "office": offices[column],
                        "note": f"The Manual's printed total for {label} is {chosen['TOTAL'][column]:,}; "
                        f"its county figures sum to {total:,}.",
                    }
                )
                continue
            swaps = [one for one in swaps if len(one) == len(swaps[0])]
            if len(swaps) > 1:
                swaps = [
                    one
                    for one in swaps
                    if all(county in expected and expected[county][column] == value for county, value in one)
                ] or swaps
            if len(swaps) != 1:
                raise ValueError(
                    f"{day} {office} {label}: off by {gap}, "
                    f"other readings {[(county, value) for county, value in others if value]}, "
                    f"unlike ICPSR {[(county, chosen[county][column], expected[county][column]) for county in order if county in expected and chosen[county][column] != expected[county][column]]}"
                )
            for county, value in swaps[0]:
                chosen[county][column] = value
        for county in order:
            for (party, label, *_), each, votes in zip(candidates, offices, chosen[county], strict=True):
                rows.append(
                    {
                        "day": day,
                        "office": each,
                        "county": jackson if county == "KC" else county,
                        "label": label,
                        "party": party,
                        "votes": votes,
                    }
                )
    (
        pl.DataFrame(rows)
        .group_by("day", "office", "county", "label", "party", maintain_order=True)
        .agg(pl.col("votes").sum())
        .write_parquet(PARSED / "lines.parquet")
    )
    pl.DataFrame(caveats, schema={"day": pl.String, "office": pl.String, "note": pl.String}).write_parquet(
        PARSED / "caveats.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    notes: dict[tuple[str, str], list[str]] = {}
    for day, office, note in pl.read_parquet(PARSED / "caveats.parquet").iter_rows():
        notes.setdefault((day, office), []).append(note)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's president, governor, and U.S. Senate elections by county from the Official Manual",
            polity="US",
        )
        book = cs.source(PUBLISHER, BOOK)
        cs.add(
            "source",
            id=book,
            kind="official_body",
            publisher=PUBLISHER,
            title=BOOK,
            url="https://mdh.contentdm.oclc.org/digital/collection/bluebook",
            license="Public record",
        )
        for _, _, segments, _ in TABLES:
            for page in {segment[0] for segment in segments}:
                cs.artifact(raw.cached(SOURCE, IMAGE.format(page)), book)
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '29%%'"
            ).fetchall()
        )
        if len(counties) != 115:
            raise ValueError("Missouri needs 115 counties")
        parties = {code: cs.key("us.party", party) if party else None for code, party in PARTIES.items()}
        if cs.minted & set(parties.values()) - {None}:
            raise ValueError("the parties must exist")

        def surname(text: str) -> str:
            words = [
                word
                for word in re.sub(r"[^a-z ]", "", re.sub(r"\([^)]*\)", " ", text.lower())).split()
                if word not in {"jr", "sr", "ii", "iii"}
            ]
            return words[-1] if words else ""

        born: set[UUID] = set()
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':MO:'")
        )
        columns = (
            "id",
            "election_id",
            "contest_id",
            "stage",
            "kind",
            "status",
            "source_id",
            "as_of",
            "completeness",
            "supersedes_id",
        )
        for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
            key = f"{day}:MO:{office}"
            contest = contests[key]
            result_set = cs.key(f"{SOURCE}.result_set", key)
            ((election_id,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            options = conn.execute(
                """select id, ballot_label, party_id from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket')""",
                [contest],
            ).fetchall()
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                if row[0] != result_set:
                    cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            on_file = dict(
                conn.execute(
                    """select o.id, sum(v.value)::bigint from result_value v
                       join result_set rs on rs.k = v.result_set_k and rs.is_default
                       join contest_option o on o.k = v.option_k
                       where rs.contest_id = %s and v.measure = 1 group by 1""",
                    [contest],
                ).fetchall()
            )
            picked: dict[tuple[str, str], UUID] = {}
            totals: dict[UUID, int] = {}
            for label, party, votes in (
                lines.group_by("label", "party", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                wanted = surname(label)
                code = party if office == "president" else f"MO:{party}" if f"MO:{party}" in parties else party
                matches = [option for option, text, _ in options if surname(text or "") == wanted] or [
                    option
                    for option, text, _ in options
                    if surname(text or "")
                    in difflib.get_close_matches(wanted, [surname(other or "") for _, other, _ in options], 1, 0.8)
                ]
                if len(matches) > 1:
                    matches = [option for option, _, owner in options if option in matches and owner == parties[code]]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {label}")
                if matches:
                    option = matches[0]
                else:
                    full = NAMES.get((day, label, party))
                    if full is None:
                        raise ValueError(f"{key}: no option or name for {label} ({party})")
                    known = [
                        person
                        for person, text in conn.execute(
                            """select distinct cd.person_id, n.text from candidacy cd
                               join contest_option o on o.id = cd.option_id
                               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                               join name n on n.entity_id = cd.person_id and n.is_preferred
                               where x.value ~ %s and left(x.value, 4)::int between %s and %s""",
                            [":president$" if office == "president" else ":MO:", int(day[:4]) - 12, int(day[:4]) + 12],
                        ).fetchall()
                        if surname(text) == wanted and text[:1].lower() == full[:1].lower()
                    ]
                    if len(set(known)) > 1:
                        raise ValueError(f"{key}: {len(set(known))} people for {full}")
                    person = (
                        known[0] if known else cs.key(f"{SOURCE}.person", f"MO:{re.sub(r'[^a-z]+', '-', full.lower())}")
                    )
                    if person in cs.minted and person not in born:
                        born.add(person)
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=full, lang="en", kind="official", is_preferred=True)
                    option = cs.key("us.option", f"{key}:{person}:{party}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="ticket" if office == "president" else "candidate",
                        party_id=parties[code],
                        ballot_label=full,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:{party}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=full,
                    )
                if option in on_file and abs(on_file[option] - votes) > max(10, on_file[option] // 200):
                    print(f"{key}: {label} has {votes}, {on_file[option]} on file")
                picked[(label, party)] = option
                totals[option] = totals.get(option, 0) + votes
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=book,
                is_default=True,
                completeness=1,
            )
            if (day, office) in notes:
                prior = conn.execute(
                    "select id from note where entity_id = %s and kind = 'caveat'", [result_set]
                ).fetchone()
                cs.add(
                    "note",
                    **({"id": prior[0]} if prior else {}),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=" ".join(notes[(day, office)]),
                )
            if conn.execute("select from result_set where id = %s", [result_set]).fetchone() is None:
                for county, label, party, votes in lines.select("county", "label", "party", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[county],
                        option_id=picked[(label, party)],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            for option, kind, seats in outcomes:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not outcomes:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=max(totals, key=totals.__getitem__),
                    kind="elected",
                    seats=1,
                )
        return cs.write()
