import base64
import csv
import io
import itertools
import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from statistics import median
from uuid import UUID

import numpy as np
import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.sources.id.kpu_dpr import _slug
from ea.sources.id.kpu_pilpres import PROVINCES
from ea.store import DATA

SOURCE = "id.kpu_dpr_2014"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
KPU = "Komisi Pemilihan Umum Republik Indonesia"
JDIH = "https://jdih.kpu.go.id/data/data_kepkpu/"
SEATED = (
    "Keputusan KPU Nomor 416/Kpts/KPU/Tahun 2014 tentang Penetapan Perolehan Kursi Partai Politik dan Penetapan Calon "
    "Terpilih Anggota Dewan Perwakilan Rakyat dalam Pemilihan Umum Tahun 2014"
)
DECREES = {
    "416": (f"{JDIH}SK_KPU_416_Penetapan_Kursi_Calon_Terpilih_1452014.pdf", SEATED),
    "564": (f"{JDIH}564.pdf", f"Keputusan KPU Nomor 564/Kpts/KPU/Tahun 2014 tentang Perubahan atas {SEATED}"),
    "565": (f"{JDIH}565_001.pdf", f"Keputusan KPU Nomor 565/Kpts/KPU/Tahun 2014 tentang Perubahan Kedua atas {SEATED}"),
}
PARTIES = {
    1: "nasdem",
    2: "pkb",
    3: "pks",
    4: "pdip",
    5: "golkar",
    6: "gerindra",
    7: "demokrat",
    8: "pan",
    9: "ppp",
    10: "hanura",
    14: "pbb",
    15: "pkpi",
}
REPLACEMENTS = (
    ("NUSA TENGGARA TIMUR I", 8, 1, "H. SYAHRULAN PUA SAWA", "564", 5),
    ("JAWA TENGAH VI", 9, 1, "KH. MUSLICH ZA", "564", 5),
    ("JAWA BARAT VII", 4, 7, "DANIEL LUMBAN TOBING", "565", 6),
    ("KALIMANTAN TIMUR", 4, 1, "AWANG FERDIAN HIDAYAT, MM", "565", 7),
)
SPANNING = {"KALIMANTAN TIMUR": ("64", "65"), "PAPUA": ("91", "93", "94", "95"), "PAPUA BARAT": ("92", "96")}
ALIASES = {"DI YOGYAKARTA": "DAERAH ISTIMEWA YOGYAKARTA", "BANGKA BELITUNG": "KEPULAUAN BANGKA BELITUNG"}
VOTES = re.compile(r"\d{1,3}(?:\.\d{3})*")
THRESHOLD = 3.5
SEATS = 560
DAPILS = 77
DAY = date(2014, 4, 9)
COLUMNS = (("number", 140), ("province", 230), ("dapil", 335), ("party", 365), ("label", 505), ("dct", 535))
COLUMNS += (("name", 740), ("votes", 790), ("rank", 10_000))
DRIVE = "https://drive.google.com/uc?export=download&id="
GORONTALO = (
    "https://gorontalo.kpu.go.id/public/gorontalo/dmdocument/"
    "1631175987REKAPITULASI-HASIL-PENGHITUNGAN-PEROLEHAN-SUARA-DPR-RI-DAPIL-GORONTALO-TAHUN-2014-UPLOAD.pdf"
)
CERTIFICATE = "Model DC-1 DPR: Sertifikat Rekapitulasi Hasil Penghitungan Perolehan Suara di Tingkat Provinsi"
CERTIFICATE_LICENSE = "Public record: official certificate, no license stated"
CERTIFICATES = {
    "SULAWESI SELATAN I": (
        f"{DRIVE}1oYq0bmhY8MeQav5CBSR4MM1qJCsaKVvW",
        "Komisi Pemilihan Umum Provinsi Sulawesi Selatan",
        CERTIFICATE,
        ("73.71", "73.06", "73.05", "73.04", "73.03", "73.01"),
        (3, 8),
    ),
    "SULAWESI SELATAN II": (
        f"{DRIVE}1KLIKW25BQBJeC6TglOzwJXwu7-cDtaE3",
        "Komisi Pemilihan Umum Provinsi Sulawesi Selatan",
        CERTIFICATE,
        ("73.07", "73.08", "73.09", "73.02", "73.10", "73.11", "73.12", "73.13", "73.72"),
        (3, 8),
    ),
    "SULAWESI SELATAN III": (
        f"{DRIVE}1WX8wiZN7twukjOSp3_wG5KAapguMreRO",
        "Komisi Pemilihan Umum Provinsi Sulawesi Selatan",
        CERTIFICATE,
        ("73.14", "73.16", "73.17", "73.22", "73.24", "73.18", "73.26", "73.15", "73.73"),
        (3, 8),
    ),
    "GORONTALO": (
        GORONTALO,
        "Komisi Pemilihan Umum Provinsi Gorontalo",
        CERTIFICATE,
        ("75.71", "75.03", "75.01", "75.05", "75.02", "75.04"),
        (3, 8),
    ),
    "SUMATERA BARAT I": (
        f"{DRIVE}1w8ZlHa6vVK5xQxtXrFs9tZvyjDV1yMub",
        "Komisi Pemilihan Umum Provinsi Sumatera Barat",
        CERTIFICATE,
        ("13.09", "13.01", "13.71", "13.72", "13.02", "13.11", "13.73", "13.03", "13.10", "13.74", "13.04"),
        (1, 8),
    ),
    "SUMATERA BARAT II": (
        f"{DRIVE}1GEUKXjtsBJTXVF8uCPYbBS4lCdxtmxpE",
        "Komisi Pemilihan Umum Provinsi Sumatera Barat",
        CERTIFICATE,
        ("13.08", "13.12", "13.76", "13.07", "13.75", "13.06", "13.77", "13.05", "", "", ""),
        (1, 8),
    ),
    "BALI": (
        f"{DRIVE}1uee99u5rlGn94f4XbQLTLeLTpuMycPTh",
        "Komisi Pemilihan Umum Provinsi Bali",
        CERTIFICATE,
        ("51.71", "51.03", "51.02", "51.01", "51.08", "51.06", "51.07", "51.05", "51.04"),
        (1, 8),
    ),
    "BANGKA BELITUNG": (
        f"{DRIVE}1hO_Si1NuFdSZNS3FtpOHvUGNtl__vDvC",
        "Komisi Pemilihan Umum Provinsi Kepulauan Bangka Belitung",
        CERTIFICATE,
        ("19.71", "19.04", "19.03", "19.02", "19.06", "19.05", "19.01"),
        (1, 6),
    ),
    "KALIMANTAN BARAT": (
        "https://kalbar.kpu.go.id/public/kalbar/dmdocument/28712401d54f054f82ccac4ad2ac8049_27072026103951.pdf",
        "Komisi Pemilihan Umum Provinsi Kalimantan Barat",
        CERTIFICATE,
        ("61.71", "61.02", "61.12", "61.72", "61.07", "61.01", "61.08", "61.03", "61.09", "61.05", "61.10", "61.06")
        + ("61.04", "61.11"),
        (1, 6),
    ),
    "DI YOGYAKARTA": (
        f"{DRIVE}16BwThFUdoLXwtgjWnW8rhKn8ZptjrdVW",
        "Komisi Pemilihan Umum Daerah Istimewa Yogyakarta",
        "Data Hasil Pemilu Tahun 2014",
        ("34.71", "34.02", "34.01", "34.04", "34.03"),
        (73, 76),
    ),
}
CORRECTIONS = {("DI YOGYAKARTA", 3, 7, "34.04"): (684, 1684)}
EDGE = 230
FIGURE = re.compile(r"\d{1,3}(?:[.,]\d{3})+|\d+|-")
RUNNING = {"DataHasilPemilu2014", "DaerahIstimewaYogyakarta"}
BANNER = re.compile(
    r"Halaman|MODEL DC|PROVINSI|DAERAH PEMILIHAN|NAMA/TANDA|RINCIAN|^\(|KAB\.|JUMLAH AKHIR|DAN SUARA CALON|^NO\.|"
    r"Perolehan Suara|^Calon$|Kota Yk|Kulon|Gunung|Bantul Sleman|^Bantul|^Progo|^Kidul"
)
SUMMARY = (("Sah Seluruh", "valid"), ("Sah dan Tidak", "ballots"), ("Tidak Sah", "invalid"))
SCANS = {
    "SUMATERA BARAT I": (0, 150),
    "SUMATERA BARAT II": (0, 150),
    "BALI": (90, 150),
    "BANGKA BELITUNG": (0, 150),
    "KALIMANTAN BARAT": (0, 200),
}
READER = "claude-haiku-5-5"
PROMPT = "1"
SCALES = (1.0, 1.3)
LONGEST = 1560
SCAN_DPI = 300
CELLS_SYSTEM = (
    "You copy numbers from one row of a scanned Indonesian election certificate (Model DC-1 DPR). The image is a strip "
    "of {count} table cells side by side, separated by vertical rules. Copy each cell's content left to right, exactly "
    "as printed, keeping the thousands dots (1.234). Give an empty string for a blank cell or one holding only a dash "
    "or dots, and ? for a cell you cannot read. Return exactly {count} cells."
)
LABEL_SYSTEM = (
    "You copy the row label from one row of a scanned Indonesian election certificate (Model DC-1 DPR). The image is "
    "the left part of one table row. Give the number printed in the narrow first column (empty when none) and the "
    "rest of the text exactly as printed, left to right, with its capitalization and punctuation (for example a "
    "candidate name with titles, a party name, a line such as Jumlah Suara Sah Partai dan Suara Calon, or a voter "
    "line ending in LK, PR, or JML)."
)
CELLS_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"cells": {"type": "array", "items": {"type": "string"}}},
    "required": ["cells"],
    "additionalProperties": False,
}
LABEL_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"number": {"type": "string"}, "text": {"type": "string"}},
    "required": ["number", "text"],
    "additionalProperties": False,
}
CODES = {"votes": 1, "registered": 2, "ballots": 3, "valid": 4, "invalid": 5, "voters": 14}


def fetch() -> None:
    for url, _ in DECREES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url, *_ in CERTIFICATES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    print(batch.ensure(SOURCE, "dc1", _requests())[1])


def _path(key: str) -> Path:
    return raw.cached(SOURCE, DECREES[key][0]).path


def _int(text: str) -> int:
    return int(text.replace(".", ""))


def _province(text: str) -> str:
    return ALIASES.get(text, text)


def _seats() -> pl.DataFrame:
    text = subprocess.run(
        ["pdftotext", "-f", "6", "-l", "7", "-layout", str(_path("416")), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows: list[dict[str, object]] = []
    totals: list[int] = []
    for line in text.splitlines():
        if match := re.match(r"\s*(\d{1,2})\s+([A-Z][A-Z .]+?)\s{2,}(\d{1,2})\s+((?:\d{1,3}\s+){11}\d{1,3})\s*$", line):
            counts = [int(count) for count in match.group(4).split()]
            for party, seats in zip(PARTIES, counts, strict=True):
                rows.append({"dapil": match.group(2), "magnitude": int(match.group(3)), "party": party, "seats": seats})
        elif match := re.match(r"\s*X\s+JUMLAH PEROLEHAN KURSI\s+([\d\s]+)$", line):
            totals = [int(count) for count in match.group(1).split()]
    seats = pl.DataFrame(rows)
    national = seats.group_by("party").agg(pl.col("seats").sum()).sort("party")
    if national["seats"].to_list() != totals:
        raise ValueError(f"seat columns {national['seats'].to_list()} do not match the printed totals {totals}")
    magnitudes = seats.group_by("dapil").agg(pl.col("magnitude").first(), pl.col("seats").sum())
    if magnitudes.height != DAPILS or magnitudes["magnitude"].sum() != SEATS:
        raise ValueError(f"{magnitudes.height} dapils with {magnitudes['magnitude'].sum()} seats")
    if magnitudes.filter(pl.col("magnitude") != pl.col("seats")).height:
        raise ValueError(f"seats do not fill {magnitudes.filter(pl.col('magnitude') != pl.col('seats')).rows()}")
    return seats


def _elected() -> pl.DataFrame:
    out = subprocess.run(
        ["pdftotext", "-f", "8", "-l", "21", "-tsv", str(_path("416")), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    words = [
        word
        for word in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
        if word["level"] == "5" and word["text"].strip()
    ]
    lines: dict[tuple[int, int], list[dict[str, str]]] = {}
    for word in words:
        lines.setdefault((int(word["page_num"]), round(float(word["top"]))), []).append(word)
    rows: list[dict[str, object]] = []
    for (page, _), line in sorted(lines.items()):
        cells: dict[str, list[dict[str, str]]] = {}
        for word in sorted(line, key=lambda word: float(word["left"])):
            column = next(name for name, edge in COLUMNS if float(word["left"]) < edge)
            cells.setdefault(column, []).append(word)
        number = cells.get("number", [{"text": ""}])[0]["text"]
        if not number.isdigit() or len(cells) != len(COLUMNS):
            continue
        texts = {column: " ".join(word["text"] for word in found) for column, found in cells.items()}
        if not VOTES.fullmatch(texts["votes"]):
            raise ValueError(f"416 page {page}: unread line {texts}")
        box = cells["votes"][0]
        left, top, width, height = (float(box[key]) for key in ("left", "top", "width", "height"))
        rows.append(
            {
                "row": int(number),
                "province": _province(texts["province"]),
                "dapil": texts["dapil"],
                "party": int(texts["party"]),
                "candidate": int(texts["dct"]),
                "name": texts["name"],
                "votes": _int(texts["votes"]),
                "rank": int(texts["rank"]),
                "page": page,
                "box": [left, top, left + width, top + height],
            }
        )
    elected = pl.DataFrame(rows)
    if elected["row"].to_list() != list(range(1, SEATS + 1)):
        raise ValueError(f"416 lists {elected.height} elected members, not rows 1 to {SEATS}")
    return elected.drop("row")


@dataclass
class Word:
    page: int
    box: tuple[float, float, float, float]
    text: str


@dataclass
class Line:
    page: int
    middle: float
    label: list[Word] = field(default_factory=list)
    values: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.label)


def _figure(text: str) -> int:
    return 0 if text == "-" else int(text.replace(".", "").replace(",", ""))


def _running(words: list[Word]) -> set[int]:
    dropped: set[int] = set()
    for start in range(len(words)):
        joined = ""
        for end in range(start, len(words)):
            joined += words[end].text
            if joined in RUNNING:
                dropped.update(range(start, end + 1))
            if len(joined) >= max(len(text) for text in RUNNING):
                break
    return dropped


def _lines(path: Path, first: int, last: int) -> list[Line]:
    out = subprocess.run(
        ["pdftotext", "-f", str(first), "-l", str(last), "-tsv", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    words: list[Word] = []
    for row in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] == "5" and row["text"].strip():
            left, top, width, height = (float(row[key]) for key in ("left", "top", "width", "height"))
            words.append(Word(int(row["page_num"]), (left, top, left + width, top + height), row["text"].strip()))
    grouped: list[list[Word]] = []
    for word in sorted(words, key=lambda word: (word.page, word.box[1], word.box[0])):
        middle = (word.box[1] + word.box[3]) / 2
        if grouped and grouped[-1][0].page == word.page:
            previous = grouped[-1][0]
            if abs((previous.box[1] + previous.box[3]) / 2 - middle) < 3.5:
                grouped[-1].append(word)
                continue
        grouped.append([word])
    lines: list[Line] = []
    for group in grouped:
        group.sort(key=lambda word: word.box[0])
        dropped = _running(group)
        line = Line(group[0].page, (group[0].box[1] + group[0].box[3]) / 2)
        for index, word in enumerate(group):
            if index in dropped or word.box[2] < 60:
                continue
            if word.box[0] < EDGE:
                line.label.append(word)
            elif FIGURE.fullmatch(word.text):
                line.values.append(word)
        if len(line.values) == 1:
            line.values = []
        if (line.label or line.values) and not BANNER.search(line.text):
            lines.append(line)
    return lines


def _certificate(dapil: str, path: Path) -> tuple[list[dict[str, object]], list[str]]:
    _, _, _, codes, (first, last) = CERTIFICATES[dapil]
    width = len(codes) + 1
    lines = _lines(path, 1 if first == 3 else first, last)
    full: dict[int, list[list[float]]] = {}
    for line in lines:
        if len(line.values) == width and not _counting(line):
            full.setdefault(line.page, []).append([word.box[2] for word in line.values])
    rights = {page: [median(column) for column in zip(*rows, strict=True)] for page, rows in full.items()}

    def cells(line: Line) -> dict[int, Word]:
        right = rights[line.page]
        placed: dict[int, Word] = {}
        slack = min(after - before for before, after in itertools.pairwise(right)) / 2
        for word in line.values:
            if word.box[0] > right[-1]:
                continue
            column = min(range(width), key=lambda index: abs(right[index] - word.box[2]))
            if abs(right[column] - word.box[2]) > slack or column in placed:
                raise ValueError(f"{dapil} page {line.page}: cannot place {word.text} in {line.text!r}")
            placed[column] = word
        return placed

    rows: list[dict[str, object]] = []
    problems: list[str] = []

    def emit(measure: str, party: int | None, number: int | None, name: str | None, line: Line) -> None:
        placed = cells(line)
        total = placed.get(width - 1)
        if total is None or sum(_figure(word.text) for column, word in placed.items() if column < width - 1) != _figure(
            total.text
        ):
            problems.append(
                f"{dapil} page {line.page}: {measure} {party}/{number} {name or line.text!r} does not add up"
            )
        for column, code in enumerate((*codes, None)):
            word = placed.get(column)
            rows.append(
                {
                    "dapil": dapil,
                    "unit": code,
                    "measure": measure,
                    "party": party,
                    "candidate": number,
                    "name": name,
                    "value": 0 if word is None else _figure(word.text),
                    "page": line.page,
                    "box": None if word is None else list(word.box),
                }
            )

    start = next(index for index, line in enumerate(lines) if "NOMOR/NAMA" in line.text.upper())
    summary: list[Line] = []
    tallies = 0
    for line in lines[:start]:
        if not line.values or _counting(line):
            continue
        if line.label and line.label[-1].text == "JML":
            tallies += 1
            if tallies in (5, 10):
                emit("registered" if tallies == 5 else "voters", None, None, None, line)
        elif measure := next((measure for phrase, measure in SUMMARY if phrase in line.text), None):
            emit(measure, None, None, None, line)

    blocks: list[list[Line]] = [[]]
    waiting = ""
    for line in lines[start:]:
        text = line.text.lower()
        if _counting(line) or "nomor/nama" in text or ("suara calon" in text and not line.values):
            continue
        if "jumlah" in text and not line.values:
            waiting = text
            continue
        if "seluruh" in waiting and line.values and not line.label:
            summary.append(line)
            break
        blocks[-1].append(line)
        if line.values and ("jumlah" in text or (waiting and not line.label)):
            blocks.append([])
            waiting = ""
    parties = iter(PARTIES)
    for block in blocks:
        if not any(line.values for line in block):
            continue
        party = next(parties)
        anchors: list[tuple[Line, list[Word], list[Line]]] = []
        fragments: list[Line] = []
        for line in block[:-1]:
            number = line.label[0].text.rstrip(".") if line.label and line.label[0].box[0] < 100 else ""
            if line.values and not line.label and anchors and not anchors[-1][1]:
                anchors[-1] = (anchors[-1][0], line.values, anchors[-1][2])
            elif line.values or number.isdigit():
                anchors.append((line, line.values, []))
            elif line.label:
                fragments.append(line)
        for fragment in fragments:
            nearest = min(
                anchors,
                key=lambda anchor: abs(anchor[0].middle - fragment.middle) + (anchor[0].page != fragment.page) * 10_000,
            )
            nearest[2].append(fragment)
        previous = 0
        for position, (line, values, extra) in enumerate(anchors):
            if not values:
                continue
            words_ = [
                word
                for part in sorted([line, *extra], key=lambda part: part.middle)
                for word in sorted(part.label, key=lambda word: word.box[0])
            ]
            numbers = [word for word in words_ if word.box[0] < 100 and word.text.rstrip(".").isdigit()]
            text = " ".join(word.text for word in words_ if word not in numbers)
            expected = previous + 1 if position else party
            printed = int(numbers[0].text.rstrip(".")) if numbers else expected
            if position and not text and not any(_figure(word.text) for word in values):
                previous = printed
                continue
            if printed != expected and not (position and previous < printed <= 12):
                raise ValueError(f"{dapil} page {line.page}: row {printed} {text!r} where {expected} was expected")
            if position:
                previous = printed
            emit("votes", party, printed if position else None, text, Line(line.page, line.middle, line.label, values))
        total = Line(block[-1].page, block[-1].middle, block[-1].label, block[-1].values)
        emit("total", party, None, None, total)
        for code in (*codes, None):
            parts = [row for row in rows if row["party"] == party and row["unit"] == code]
            listed = sum(int(row["value"]) for row in parts if row["measure"] == "votes")  # ty: ignore[invalid-argument-type]
            if listed != next(int(row["value"]) for row in parts if row["measure"] == "total"):  # ty: ignore[invalid-argument-type]
                problems.append(f"{dapil} party {party} column {code}: rows add to {listed}, not the printed total")
    if next(parties, None) is not None:
        raise ValueError(f"{dapil}: fewer than {len(PARTIES)} party blocks")
    for line in summary:
        emit("valid", None, None, None, line)
    return rows, problems


def _counting(line: Line) -> bool:
    words = [*line.label, *line.values]
    if len(words) > 2 and all(word.text.isdigit() and int(word.text) <= 20 for word in words):
        return True
    numbers = [_figure(word.text) for word in line.values if word.text != "-"]
    return len(numbers) > 2 and numbers == list(range(numbers[0], numbers[0] + len(numbers)))


@dataclass
class Band:
    page: int
    top: int
    bottom: int
    label: tuple[int, int]
    columns: list[int]
    inked: bool
    height: int


def _rules(dark: np.ndarray) -> list[int]:
    window = 300
    rows = dark[:-1] | dark[1:]
    cumulative = np.cumsum(rows, axis=1, dtype=np.int32)
    full = (cumulative[:, window:] - cumulative[:, :-window]) >= window - 4
    found: list[list[int]] = []
    for y in np.flatnonzero(full.any(axis=1)):
        if found and y - found[-1][-1] <= 6:
            found[-1].append(int(y))
        else:
            found.append([int(y)])
    return [round(sum(group) / len(group)) for group in found]


def _verticals(dark: np.ndarray, top: int, bottom: int) -> list[int]:
    inner = dark[top + 4 : bottom - 3]
    share = (inner[:, :-2] | inner[:, 1:-1] | inner[:, 2:]).mean(axis=0)
    found: list[list[int]] = []
    for x in np.flatnonzero(share > 0.92):
        if found and x - found[-1][-1] <= 6:
            found[-1].append(int(x))
        else:
            found.append([int(x)])
    return [round(sum(group) / len(group)) + 1 for group in found]


def _lattice(xs: list[int], width: int) -> list[int]:
    best: list[int] = []
    for end in range(len(xs) - 2, width - 2, -1):
        for start in range(end):
            pitch = (xs[end] - xs[start]) / (width - 1)
            if pitch < 100:
                continue
            picked = [min(xs, key=lambda x: abs(x - (xs[start] + pitch * k))) for k in range(width)]
            if len(set(picked)) != width or any(
                abs(x - (xs[start] + pitch * k)) > 0.15 * pitch for k, x in enumerate(picked)
            ):
                continue
            edge = [x for x in xs if picked[-1] + 0.8 * pitch < x < picked[-1] + 2 * pitch]
            if edge and (not best or edge[0] > best[-1]):
                best = [*picked, edge[0]]
        if best:
            return best
    return best


def _scan(dapil: str, page: int) -> tuple[Image.Image, list[Band]]:
    url, _, _, codes, _ = CERTIFICATES[dapil]
    rotate, threshold = SCANS[dapil]
    cached = raw.cached(SOURCE, url)
    image_path = PARSED / "scans" / f"{cached.sha256}-{page}.png"
    if not image_path.exists():
        image_path.parent.mkdir(parents=True, exist_ok=True)
        png = subprocess.run(
            ["pdftoppm", "-r", str(SCAN_DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(cached.path)],
            check=True,
            capture_output=True,
        ).stdout
        Image.open(io.BytesIO(png)).rotate(rotate, expand=True).save(image_path)
    image = Image.open(image_path).convert("L")
    dark = np.asarray(image) < threshold
    ys = _rules(dark)
    spans = [(top, bottom) for top, bottom in itertools.pairwise(ys) if bottom - top >= 25]
    seen = sorted(x for top, bottom in spans for x in _verticals(dark, top, bottom))
    groups: list[list[int]] = []
    for x in seen:
        if groups and x - groups[-1][-1] <= 8:
            groups[-1].append(x)
        else:
            groups.append([x])
    xs = [round(sum(group) / len(group)) for group in groups if len(group) >= 0.3 * len(spans)]
    columns = _lattice(xs, len(codes) + 1)
    if not columns:
        raise ValueError(f"{dapil} page {page}: no value columns among {xs}")
    left = min((x for x in xs if x < columns[0] - 10), default=0)
    bands = []
    for top, bottom in spans:
        ink = [int(dark[top + 6 : bottom - 4, x0 + 6 : x1 - 4].sum()) for x0, x1 in itertools.pairwise(columns)]
        bands.append(Band(page, top, bottom, (left, columns[0]), columns, max(ink) >= 40, image.height))
    return image, bands


def _key(sha256: str, band: Band, part: str, read: int) -> str:
    return f"anthropic-batch:{READER}/{PROMPT}/{sha256}/{band.page}/{band.top}/{part}/{read}"


def _parts(band: Band) -> list[tuple[str, int, int]]:
    count = len(band.columns) - 1
    chunks = math.ceil((band.columns[-1] - band.columns[0]) / (LONGEST / max(SCALES)))
    size = math.ceil(count / chunks)
    parts = [("label", -1, -1)]
    parts += [(f"cells{start}", start, min(start + size, count)) for start in range(0, count, size)]
    return parts


def _requests() -> dict[str, batch.Want]:
    price_in, price_out = batch.PRICES[READER]
    wanted: dict[str, batch.Want] = {}
    for dapil in SCANS:
        url, _, _, _, (first, last) = CERTIFICATES[dapil]
        sha256 = raw.cached(SOURCE, url).sha256
        for page in range(first, last + 1):
            image, bands = _scan(dapil, page)
            for band in bands:
                if not band.inked:
                    continue
                for part, start, end in _parts(band):
                    if part == "label":
                        box = (band.label[0], band.top - 3, band.label[1], band.bottom + 3)
                        system, schema, ask = LABEL_SYSTEM, LABEL_SCHEMA, "Copy this row label."
                    else:
                        box = (band.columns[start], band.top - 3, band.columns[end], band.bottom + 3)
                        system = CELLS_SYSTEM.format(count=end - start)
                        schema, ask = CELLS_SCHEMA, f"Copy the {end - start} cells of this row."
                    for read, scale in enumerate(SCALES):
                        crop = image.crop(box)
                        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)))
                        crop.thumbnail((LONGEST, LONGEST))
                        buffer = io.BytesIO()
                        crop.save(buffer, "PNG")
                        params = MessageCreateParamsNonStreaming(
                            model=READER,
                            max_tokens=1000,
                            system=system,
                            messages=[
                                {
                                    "role": "user",
                                    "content": [
                                        ImageBlockParam(
                                            type="image",
                                            source={
                                                "type": "base64",
                                                "media_type": "image/png",
                                                "data": base64.b64encode(buffer.getvalue()).decode(),
                                            },
                                        ),
                                        TextBlockParam(type="text", text=ask),
                                    ],
                                }
                            ],
                            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                        )
                        estimate = (crop.width * crop.height / 750 * price_in + 600 * price_in + 300 * price_out) / 1e6
                        wanted[_key(sha256, band, part, read)] = (params, estimate)
    return wanted


@dataclass
class Row:
    band: Band
    number: str
    text: str
    cells: list[set[int]]
    blank: list[bool]


def _answer(key: str) -> dict[str, object] | None:
    if key not in _held():
        return None
    message = json.loads(raw.cached(SOURCE, key).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


@cache
def _held() -> set[str]:
    return raw.keys(SOURCE)


FILLER = re.compile(r"[\s.,\-–—_]*")
NUMBER = re.compile(r"\d{1,3}(?:[.,]\d{3})+|\d+")


def _cell(text: str) -> int | None | bool:
    text = text.strip().replace(" ", "")
    if FILLER.fullmatch(text):
        return None
    if NUMBER.fullmatch(text):
        return int(text.replace(".", "").replace(",", ""))
    return False


def _rows(dapil: str) -> list[Row]:
    url, _, _, _, (first, last) = CERTIFICATES[dapil]
    sha256 = raw.cached(SOURCE, url).sha256
    rows: list[Row] = []
    for page in range(first, last + 1):
        _, bands = _scan(dapil, page)
        for band in bands:
            if not band.inked:
                continue
            labels = [_answer(_key(sha256, band, "label", read)) for read in range(len(SCALES))]
            label = next((found for found in labels if found), {"number": "", "text": ""})
            readings: list[list[int | None | bool]] = [[] for _ in band.columns[1:]]
            for part, start, end in _parts(band)[1:]:
                for read in range(len(SCALES)):
                    found = _answer(_key(sha256, band, part, read))
                    cells = found["cells"] if found else []
                    if not isinstance(cells, list) or len(cells) != end - start:
                        continue
                    for index, text in enumerate(cells, start):
                        readings[index].append(_cell(str(text)))
            cells = [{0 if value is None else value for value in found if value is not False} for found in readings]
            unanimous = [len(found) == len(SCALES) and len({*found}) == 1 for found in readings]
            cells = [found if agreed else found | {-1} for found, agreed in zip(cells, unanimous, strict=True)]
            blank = [all(value is None for value in found) for found in readings]
            rows.append(Row(band, str(label["number"]).strip(), " ".join(str(label["text"]).split()), cells, blank))
    return rows


def _solve(rows: list[Row], constraints: list[list[tuple[int, int, int]]]) -> None:
    changed = True
    while changed:
        changed = False
        for terms in constraints:
            open_ = [(row, column) for row, column, _ in terms if len(rows[row].cells[column]) > 1]
            if not open_ or math.prod(len(rows[row].cells[column]) for row, column in open_) > 4096:
                continue
            fits = []
            for choice in itertools.product(*(sorted(rows[row].cells[column]) for row, column in open_)):
                picked = dict(zip(open_, choice, strict=True))
                total = 0
                for row, column, sign in terms:
                    total += sign * picked.get((row, column), next(iter(rows[row].cells[column])))
                if total == 0 and -1 not in choice:
                    fits.append(choice)
            if len(fits) == 1:
                for (row, column), value in zip(open_, fits[0], strict=True):
                    rows[row].cells[column] = {value}
                changed = True


def _scanned(dapil: str) -> tuple[list[dict[str, object]], list[str]]:
    codes = CERTIFICATES[dapil][3]
    rotate, _ = SCANS[dapil]
    width = len(codes)
    rows = [row for row in _rows(dapil) if row.number or row.text or any(not blank for blank in row.blank)]
    constraints = [[(index, column, 1) for column in range(width)] + [(index, width, -1)] for index in range(len(rows))]
    start = next(index for index, row in enumerate(rows) if row.text.upper().startswith("PARTAI"))
    tiers = [index for index in range(start) if _tail(rows[index]) in ("LK", "PR", "JML")]
    for lk, pr, jml in zip(tiers, tiers[1:], tiers[2:], strict=False):
        if [_tail(rows[index]) for index in (lk, pr, jml)] == ["LK", "PR", "JML"]:
            for column in range(width + 1):
                constraints.append([(lk, column, 1), (pr, column, 1), (jml, column, -1)])
    blocks: list[tuple[int, list[int], int]] = []
    head: int | None = None
    members: list[int] = []
    for index in range(start, len(rows)):
        row = rows[index]
        if row.text.upper().startswith("PARTAI"):
            head, members = index, []
        elif head is not None and "jumlah" in row.text.lower():
            blocks.append((head, members, index))
            head = None
        elif head is not None:
            members.append(index)
    for head, members, total in blocks:
        for column in range(width + 1):
            constraints.append([(head, column, 1), *((index, column, 1) for index in members), (total, column, -1)])
    _solve(rows, constraints)
    out: list[dict[str, object]] = []
    problems: list[str] = []

    def emit(measure: str, party: int | None, number: int | None, name: str | None, index: int) -> None:
        row = rows[index]
        for column, code in enumerate((*codes, None)):
            found = row.cells[column]
            if len(found) != 1:
                problems.append(f"{dapil} page {row.band.page} row {row.number} {row.text!r} column {column}: {found}")
                continue
            value = next(iter(found))
            if code == "":
                if value:
                    problems.append(f"{dapil} page {row.band.page} {row.text!r}: blank column {column} holds {value}")
                continue
            x0, x1 = row.band.columns[column], row.band.columns[column + 1]
            box = (x0, row.band.top, x1, row.band.bottom)
            if rotate:
                box = (row.band.height - row.band.bottom, x0, row.band.height - row.band.top, x1)
            out.append(
                {
                    "dapil": dapil,
                    "unit": code,
                    "measure": measure,
                    "party": party,
                    "candidate": number,
                    "name": name,
                    "value": value,
                    "page": row.band.page,
                    "box": None if row.blank[column] else [side * 72 / SCAN_DPI for side in box],
                }
            )

    tallies = 0
    for index in range(start):
        row = rows[index]
        if _tail(row) == "JML":
            tallies += 1
            if tallies in (5, 10):
                emit("registered" if tallies == 5 else "voters", None, None, None, index)
        elif measure := next((measure for phrase, measure in SUMMARY if phrase.lower() in row.text.lower()), None):
            emit(measure, None, None, None, index)
    parties = iter(PARTIES)
    for head, members, total in blocks:
        party = next(parties)
        if rows[head].number.rstrip(".") != str(party):
            problems.append(f"{dapil}: party block {rows[head].number} {rows[head].text!r} where {party} was expected")
        emit("votes", party, None, rows[head].text, head)
        expected = 1
        for index in members:
            row = rows[index]
            if not row.number and all(row.blank):
                continue
            if row.number.rstrip(".") != str(expected):
                problems.append(f"{dapil} party {party}: candidate {row.number} {row.text!r}, {expected} expected")
            emit("votes", party, expected, row.text, index)
            expected += 1
        emit("total", party, None, None, total)
    if next(parties, None) is not None:
        problems.append(f"{dapil}: {len(blocks)} party blocks, not {len(PARTIES)}")
    return out, problems


def _tail(row: Row) -> str:
    words = row.text.upper().replace(".", "").split()
    return words[-1] if words else ""


def _reconcile(dapil: str, frame: pl.DataFrame, elected: pl.DataFrame) -> None:
    def column(measure: str) -> dict[str | None, int]:
        found = frame.filter(pl.col("measure") == measure).group_by("unit").agg(pl.col("value").sum())
        return dict(found.rows())

    valid, invalid, ballots = column("valid"), column("invalid"), column("ballots")
    if valid != column("total"):
        raise ValueError(f"{dapil}: party totals {column('total')} do not add to the valid votes {valid}")
    if ballots and any(ballots[unit] != valid[unit] + invalid[unit] for unit in ballots):
        raise ValueError(f"{dapil}: valid and invalid votes do not add to the ballots")
    totals = frame.filter(pl.col("unit").is_null() & (pl.col("measure") == "votes") & pl.col("candidate").is_not_null())
    for party, number, name, votes in elected.select("party", "candidate", "name", "votes").rows():
        found = totals.filter((pl.col("party") == party) & (pl.col("candidate") == number))
        if found.height != 1:
            raise ValueError(f"{dapil}: elected {party}/{number} {name} is not on the certificate")
        if found["value"][0] != votes:
            print(f"{dapil}: {party}/{number} {name} has {found['value'][0]} on the certificate, {votes} in decree 416")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    seats = _seats()
    elected = _elected()
    unknown = set(elected["province"]) - set(PROVINCES)
    if unknown:
        raise ValueError(f"unknown provinces {sorted(unknown)}")
    tally = elected.group_by("dapil", "party").len("count")
    check = (
        seats.filter(pl.col("seats") > 0)
        .join(tally, on=["dapil", "party"], how="full", coalesce=True)
        .filter(pl.col("seats").ne_missing(pl.col("count")))
    )
    if check.height:
        raise ValueError(f"elected members do not match the seat table: {check.rows()[:8]}")
    for dapil, party, number, _, key, _ in REPLACEMENTS:
        if elected.filter((pl.col("dapil") == dapil) & (pl.col("party") == party)).height < 1:
            raise ValueError(f"{key}: no {party} member in {dapil}")
        if (
            number
            and not elected.filter(
                (pl.col("dapil") == dapil) & (pl.col("party") == party) & (pl.col("candidate") == number)
            ).height
        ):
            raise ValueError(f"{key}: {party}/{number} is not elected in {dapil}")
    seats.write_parquet(PARSED / "seats.parquet")
    elected.write_parquet(PARSED / "elected.parquet")
    certificates: list[dict[str, object]] = []
    for dapil, (url, *_) in CERTIFICATES.items():
        rows, problems = _scanned(dapil) if dapil in SCANS else _certificate(dapil, raw.cached(SOURCE, url).path)
        frame = pl.DataFrame(rows, infer_schema_length=None).with_columns(flags=pl.lit(0, pl.Int16))
        for (where, party, number, code), (printed, value) in CORRECTIONS.items():
            if where != dapil:
                continue
            match = (pl.col("party") == party) & (pl.col("candidate") == number) & (pl.col("unit") == code)
            if frame.filter(match & (pl.col("measure") == "votes"))["value"].to_list() != [printed]:
                raise ValueError(f"{dapil}: {party}/{number} in {code} no longer reads {printed}")
            frame = frame.with_columns(
                value=pl.when(match).then(value).otherwise(pl.col("value")),
                flags=pl.when(match).then(1).otherwise(pl.col("flags")).cast(pl.Int16),
            )
            problems = [problem for problem in problems if f"{party}/{number}" not in problem and code not in problem]
        if problems:
            raise ValueError("\n".join(problems))
        _reconcile(dapil, frame, elected.filter(pl.col("dapil") == dapil))
        certificates.extend(frame.filter(pl.col("unit").is_not_null() & (pl.col("measure") != "total")).to_dicts())
    pl.DataFrame(certificates, infer_schema_length=None).write_parquet(PARSED / "certificates.parquet")
    national = seats.group_by("party").agg(pl.col("seats").sum()).sort("party")
    print(f"{seats['dapil'].n_unique()} dapils, {elected.height} elected; seats {dict(national.rows())}")


DECREE_LICENSE = "Public record: official decree, no license stated"
SYSTEM = (
    "The 560 seats of the House of Representatives (Dewan Perwakilan Rakyat) were filled in 77 electoral districts "
    "(daerah pemilihan) of 3 to 10 seats under UU 8/2012. Only parties with at least 3.5 percent of the valid votes "
    "nationwide took part in the allocation (art. 208). In each district the valid votes of those parties were divided "
    "by the seats to give the quota (bilangan pembagi pemilihan, BPP); each party won one seat per whole quota, and the "
    "seats left went to the largest remainders (art. 212). Each party's seats went to its candidates with the most "
    "votes (art. 215). A voter marked a party or one of its candidates; both counted toward the party."
)
METHODOLOGY = (
    "Declared seats by party and the elected members with their own votes, from KPU decree 416/2014 (Model E-5 DPR and "
    "the recapitulation of Model E-3 DPR). The votes of every party and candidate by district were set in lampiran I "
    "of Model E-1 DPR to KPU decree 411/2014, which the KPU legal database does not carry (its copy holds only the DPD "
    "lampiran), so this set holds the elected members' votes alone and records completeness 0. The ten parties over "
    "the threshold won 560 seats; PBB and PKPI won none."
)
CERTIFIED = (
    "Votes of every party and candidate by regency and city, with the registered voters, turnout, and valid and "
    "invalid votes, from the provincial commission's Model DC-1 DPR, the certificate of the provincial "
    "recapitulation. Declared seats and the elected members come from KPU decree 416/2014, which follows the national "
    "recapitulation of the same figures. A party's own votes (ballots marked for the party alone) sit on its party "
    "line; its total is that line plus its candidates' votes."
)
DIY = (
    "The figures come from the provincial commission's book Data Hasil Pemilu Tahun 2014, which transcribes the "
    "certificate. The book prints 684 votes in Sleman for PKS candidate 7 (Achmad Baihaqi); his printed total of 4,444 "
    "and the party's printed Sleman total of 51,298 both require 1,684, which is stored and flagged as derived."
)
REPLACED = (
    "KPU replaced members it first declared elected in decree 416/2014 who died, resigned, or no longer met the "
    "requirements before taking office on 1 October 2014; the seat went to the party's candidate with the next most "
    "votes. The elected candidates here follow decrees 564/2014 (Nusa Tenggara Timur I and Jawa Tengah VI) and "
    "565/2014 (Jawa Barat VII and Kalimantan Timur), whose lampiran give neither the replacement's ballot number nor "
    "votes."
)


def _title(dapil: str) -> str:
    title = dapil.title().replace("Di ", "DI ").replace("Dki ", "DKI ")
    return re.sub(r"\b[IVX]+\b", lambda match: match.group(0).upper(), title, flags=re.IGNORECASE)


def emit() -> Path:
    from ea.changeset import Changeset
    from ea.store import connect

    seats = pl.read_parquet(PARSED / "seats.parquet")
    elected = pl.read_parquet(PARSED / "elected.parquet")
    day = DAY.isoformat()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Indonesia's 2014 House of Representatives (DPR) election: declared seats in all 77 districts and the "
                "560 elected members with their votes, as amended, and every party's and candidate's votes by regency in "
                f"{len(CERTIFICATES)} districts from the provincial certificates"
            ),
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        indonesia = cs.polity("ID")
        nation = cs.area("id.nation", "ID")
        body = cs.key("id.body", "dpr")
        if body in cs.minted:
            raise ValueError("publish id.kpu before id.kpu_dpr_2014")
        cs.add("body_size", body_id=body, seats=SEATS, valid_from=date(2014, 10, 1), valid_to=date(2019, 10, 1))

        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        for key, (url, title) in DECREES.items():
            found = sources[key] = cs.source(KPU, title)
            cs.add(
                "source",
                id=found,
                kind="official_body",
                publisher=KPU,
                title=title,
                url=url,
                license=DECREE_LICENSE,
                license_url="https://jdih.kpu.go.id",
            )
            artifacts[key] = cs.artifact(raw.cached(SOURCE, url), found)

        certificates = pl.read_parquet(PARSED / "certificates.parquet")
        urls = {key: url for key, (url, _) in DECREES.items()}
        for dapil, (url, publisher, title, *_) in CERTIFICATES.items():
            urls[dapil] = url
            title = f"{title}, Daerah Pemilihan {_title(dapil)}"
            found = sources[dapil] = cs.source(publisher, title)
            cs.add(
                "source",
                id=found,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=CERTIFICATE_LICENSE,
                license_url=None,
            )
            artifacts[dapil] = cs.artifact(raw.cached(SOURCE, url), found)

        pages: dict[tuple[str, int], tuple[UUID, int, int]] = {}

        def page(key: str, number: int) -> tuple[UUID, int, int]:
            if (key, number) not in pages:
                cached = raw.cached(SOURCE, urls[key])
                image = DATA / "pages" / SOURCE / f"{cached.sha256}-{number}.png"
                if not image.exists():
                    image.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(cached.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = Image.open(image).size
                found = cs.page(artifacts[key], number)
                cs.add(
                    "source_page",
                    id=found,
                    artifact_id=artifacts[key],
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
                pages[key, number] = (found, width, height)
            return pages[key, number]

        election = cs.key("id.election", day)
        cs.add(
            "election",
            id=election,
            polity_id=indonesia,
            kind="general",
            date_from=DAY,
            date_to=DAY,
            date_precision="day",
            status="held",
        )
        name(election, "2014 Indonesian legislative election", "en", "english")
        name(election, "Pemilihan Umum Anggota DPR, DPD, dan DPRD Tahun 2014")
        path = f"id/{day}-legislative"
        cs.add("slug", path=path, entity_id=election)

        system = cs.key("id.electoral_system", "dpr:2014")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=indonesia,
            rules={
                "formula": "hare",
                "threshold": {"percent": THRESHOLD, "level": "party", "scope": "national"},
                "lists": {"order": "open", "vacancy": "next_by_votes"},
            },
            rules_version=1,
        )
        name(system, "House of Representatives open-list proportional representation, 2014", "en", "english")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        parties = {number: cs.key("id.party", key) for number, key in PARTIES.items()}
        if any(party in cs.minted for party in parties.values()):
            raise ValueError("publish id.kpu before id.kpu_dpr_2014")
        for number in sorted(set(seats.filter(pl.col("seats") > 0)["party"])):
            cs.add(
                "citation",
                entity_id=parties[number],
                source_id=sources["416"],
                locator={"election": "2014", "nomor_urut": number},
            )

        cs.add(
            "scheme",
            code="id.dapil_dpr",
            polity_id=indonesia,
            kind="electoral_district",
            rank=2,
            noun="electoral district",
            kinds=None,
            code_system=None,
        )
        districts = elected.select("dapil", "province").unique()
        whole = {
            province
            for province, count in districts.group_by("province").len().rows()
            if count == 1 and province not in SPANNING
        }
        replaced = {(dapil, party, number) for dapil, party, number, *_ in REPLACEMENTS}
        kabkota: dict[str, UUID] = {}

        def regency(code: str) -> UUID:
            if code not in kabkota:
                found = kabkota[code] = cs.unit("id.kabkota", code)
                if found in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=found,
                        polity_id=indonesia,
                        scheme_code="id.kabkota",
                        code=code,
                        area_relation="equals",
                        area_id=cs.area("id.kabkota", code, DAY),
                        kind="standard",
                        valid_from=None,
                    )
            return kabkota[code]

        for dapil, province in sorted(districts.rows()):
            key = f"{day}:dpr:{_slug(dapil)}"
            title = _title(dapil)
            area = cs.area("id.provinsi", PROVINCES[province])
            code = f"2014:{_slug(dapil)}"
            unit = cs.unit("id.dapil_dpr", code)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=indonesia,
                scheme_code="id.dapil_dpr",
                code=code,
                area_relation="equals" if province in whole else "within",
                area_id=nation if province in SPANNING else area,
                kind="standard",
                valid_from=None,
            )
            name(unit, f"Daerah Pemilihan {title}")
            name(unit, f"{title} electoral district", "en", "english")
            contest = cs.key("id.contest", key)
            won = dict(seats.filter((pl.col("dapil") == dapil) & (pl.col("seats") > 0)).select("party", "seats").rows())
            magnitude = int(seats.filter(pl.col("dapil") == dapil)["magnitude"][0])
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=area if province in whole else None,
                term="full",
                magnitude=magnitude,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"Dewan Perwakilan Rakyat, {title}")
            name(contest, f"House of Representatives, {title}", "en", "english")
            cs.add("slug", path=f"{path}/dpr/{_slug(dapil)}", entity_id=contest)
            swaps = [row for row in REPLACEMENTS if row[0] == dapil]
            if swaps:
                cs.add("note", entity_id=contest, kind="caveat", body_md=REPLACED)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            counted = certificates.filter(pl.col("dapil") == dapil)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[dapil] if counted.height else sources["416"],
                is_default=True,
                completeness=1 if counted.height else 0,
            )
            if counted.height:
                cs.add("note", entity_id=result_set, kind="methodology", body_md=CERTIFIED)
                cs.add("citation", entity_id=result_set, source_id=sources["416"], locator={"pages": "6-21"})
                if dapil == "DI YOGYAKARTA":
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=DIY)
            else:
                cs.add("note", entity_id=result_set, kind="methodology", body_md=METHODOLOGY)
            members = elected.filter(pl.col("dapil") == dapil)

            def candidate(
                party: int, number: int | None, ballot: str, option_key: str, listed: UUID, outcome: str
            ) -> tuple[UUID, UUID]:
                person = cs.key(f"{SOURCE}.person", f"2014:{key}:{party}:{option_key}")
                if person in cs.minted:
                    cs.add("person", id=person, gender=None)
                    name(person, ballot)
                option = cs.key("id.option", f"{key}:{party}:{option_key}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=listed,
                    party_id=parties[party],
                    ballot_position=number,
                    ballot_label=ballot,
                )
                candidacy = cs.key("id.candidacy", f"{key}:{party}:{option_key}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    ballot_name=ballot,
                    is_independent=False,
                    member_of_party_id=parties[party],
                )
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome, seats=None)
                return option, candidacy

            def record(dapil: str, option: UUID | None, rows: pl.DataFrame) -> None:
                for code, measure, value, flags, number_, box in rows.select(
                    "unit", "measure", "value", "flags", "page", "box"
                ).rows():
                    fact = {
                        "result_set_id": result_set,
                        "unit_id": regency(code),
                        "option_id": option,
                        "channel": 0,
                        "measure": CODES[measure],
                        "count_round": 0,
                    }
                    cs.add("result_value", **fact, flags=flags, value=value)
                    if box is None:
                        cs.add("value_locator", **fact, page_id=None, source_id=sources[dapil])
                        continue
                    found, width, height = page(dapil, number_)
                    x0, y0, x1, y1 = (side * 100 / 72 for side in box)
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=found,
                        source_id=None,
                        x0=x0 / width,
                        y0=y0 / height,
                        x1=x1 / width,
                        y1=y1 / height,
                    )

            if counted.height:
                record(dapil, None, counted.filter(pl.col("party").is_null()))
            listing = sorted(set(counted["party"].drop_nulls())) if counted.height else sorted(won)
            for party in listing:
                listed = cs.key("id.option", f"{key}:{party}")
                cs.add(
                    "contest_option",
                    id=listed,
                    contest_id=contest,
                    kind="list",
                    party_id=parties[party],
                    ballot_position=party,
                    ballot_label=None,
                )
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=listed,
                    kind="won" if won.get(party) else "lost",
                    seats=won.get(party, 0),
                )
                chosen = members.filter(pl.col("party") == party)
                if counted.height:
                    legend = cs.key("id.option", f"{key}:{party}:party")
                    cs.add(
                        "contest_option",
                        id=legend,
                        contest_id=contest,
                        kind="legend",
                        parent_option_id=listed,
                        party_id=parties[party],
                        ballot_position=None,
                        ballot_label=None,
                    )
                    lines = counted.filter(pl.col("party") == party)
                    record(dapil, legend, lines.filter(pl.col("candidate").is_null()))
                    names = dict(chosen.select("candidate", "name").rows())
                    for number in sorted(set(lines["candidate"].drop_nulls())):
                        votes = lines.filter(pl.col("candidate") == number)
                        option, _ = candidate(
                            party,
                            number,
                            names.get(number, votes["name"][0]),
                            str(number),
                            listed,
                            "elected" if number in names and (dapil, party, number) not in replaced else "lost",
                        )
                        record(dapil, option, votes)
                for number, ballot, votes, number_, box in (
                    []
                    if counted.height
                    else chosen.sort("candidate").select("candidate", "name", "votes", "page", "box").rows()
                ):
                    option, _ = candidate(
                        party,
                        number,
                        ballot,
                        str(number),
                        listed,
                        "lost" if (dapil, party, number) in replaced else "elected",
                    )
                    fact = {
                        "result_set_id": result_set,
                        "unit_id": unit,
                        "option_id": option,
                        "channel": 0,
                        "measure": 1,
                        "count_round": 0,
                    }
                    cs.add("result_value", **fact, flags=0, value=votes)
                    found, width, height = page("416", number_)
                    x0, y0, x1, y1 = (side * 100 / 72 for side in box)
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=found,
                        source_id=None,
                        x0=x0 / width,
                        y0=y0 / height,
                        x1=x1 / width,
                        y1=y1 / height,
                    )
                for ballot, decree, number_ in (row[3:] for row in swaps if row[1] == party):
                    _, candidacy = candidate(party, None, ballot, f"r{_slug(ballot)}", listed, "elected")
                    cs.add(
                        "citation",
                        entity_id=candidacy,
                        source_id=sources[decree],
                        locator={"page": number_},
                    )
                    page(decree, number_)
        return cs.write()
