import base64
import csv
import html
import io
import itertools
import json
import re
import subprocess
from pathlib import Path
from typing import Any, cast

import httpx
import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.store import DATA

SOURCE = "id.kpu_dpr"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
KPU = "Komisi Pemilihan Umum Republik Indonesia"
OPEN_DATA = "https://opendata.kpu.go.id/"
JDIH = "https://jdih.kpu.go.id/data/data_kepkpu/"
CERTIFICATES = (75, 83, 85, 87, 92, 98, 101, 102, 104, 107, 108, 113, 114, 118, 120, 126, 128, 129, 130, 133, 134)
CERTIFICATES += (142, 149, 150, 151, 152, 154, 161, 162, 165, 169, 170, 171, 172, 173, 174, 176, 177)
CANDIDATES = (26, 35, 47, 84, 88, 136, 139, 175)
SEATS = 46
DECREES = {
    "1316": (
        f"{JDIH}KPT%201316%20THN%202019.pdf",
        (
            "Keputusan KPU Nomor 1316/PL.01.9-Kpt/06/KPU/VIII/2019 tentang Penetapan Ambang Batas Perolehan Suara Sah "
            "Secara Nasional dan Penetapan Partai Politik Peserta Pemilihan Umum Tahun 2019 yang Memenuhi dan Tidak "
            "Memenuhi Ambang Batas Perolehan Suara Sah Secara Nasional dalam Penentuan Perolehan Kursi Anggota Dewan "
            "Perwakilan Rakyat dalam Pemilihan Umum Tahun 2019"
        ),
    ),
    "1317": (
        f"{JDIH}KPT%201317%20THN%202019.pdf",
        (
            "Keputusan KPU Nomor 1317/PL.01.9-Kpt/06/KPU/VIII/2019 tentang Penetapan Perolehan Kursi Partai Politik "
            "Peserta Pemilihan Umum Anggota Dewan Perwakilan Rakyat Tahun 2019"
        ),
    ),
    "1318": (
        f"{JDIH}KPT%201318%20THN%202019.pdf",
        (
            "Keputusan KPU Nomor 1318/PL.01.9-Kpt/06/KPU/VIII/2019 tentang Penetapan Calon Terpilih Anggota Dewan "
            "Perwakilan Rakyat dalam Pemilihan Umum Tahun 2019"
        ),
    ),
    "1341": (
        f"{JDIH}KPT%201341%20THN%202019.pdf",
        (
            "Keputusan KPU Nomor 1341/PL.01.9-Kpt/06/KPU/IX/2019 tentang Perubahan atas Keputusan KPU Nomor "
            "1318/PL.01.9-Kpt/06/KPU/VIII/2019 tentang Penetapan Calon Terpilih Anggota Dewan Perwakilan Rakyat dalam "
            "Pemilihan Umum Tahun 2019"
        ),
    ),
    "1395": (
        f"{JDIH}SK%201395%20THN%202018.pdf",
        (
            "Keputusan KPU Nomor 1395/PL.01.4-Kpt/06/KPU/X/2018 tentang Perubahan atas Keputusan KPU Nomor "
            "1129/PL.01.4-Kpt/06/IX/2018 tentang Daftar Calon Tetap Anggota Dewan Perwakilan Rakyat Republik "
            "Indonesia Pemilihan Umum Tahun 2019"
        ),
    ),
}
DCT_1395 = {
    "JAWA TENGAH III": {
        1: "LLPLLPLLP",
        2: "PLLLPLPLL",
        3: "PLLLLPLLP",
        4: "LPLPLLLPL",
        5: "PLLLPLPLL",
        6: "LPPL",
        7: "LPLLPLPLP",
        8: "LLPLPLPL",
        9: "LLPLLPLLP",
        10: "LLPLLPLPP",
        11: "LLPLLPPPL",
        12: "LLPLLPPPL",
        13: "LLPLPL",
        14: "LPPLPLPLL",
        19: "LLPLLPP",
        20: "P",
    },
    "JAWA TENGAH IV": {
        1: "PLLLPPL",
        2: "LLPLPPL",
        3: "LPLPLPL",
        4: "PLPLPPL",
        5: "LLPLLPP",
        6: "LLP",
        7: "LLPPLP",
        8: "LPLLLPP",
        9: "LPLPLPP",
        10: "LLPPLP",
        11: "LLPLPPP",
        12: "PLLPLLP",
        13: "LLPLLP",
        14: "LLPLPLP",
        19: "LLP",
        20: "P",
    },
    "JAWA TENGAH VI": {
        1: "LLPPLLPL",
        2: "LLPLLPLP",
        3: "LLPLLPLP",
        4: "LLPPLPLL",
        5: "LLPLLPLP",
        6: "LLP",
        7: "PLPLLPLP",
        8: "LLPLPPL",
        9: "LPLLPLPL",
        10: "LLPLLPPL",
        11: "PLLLLPLP",
        12: "LLPPLPLL",
        13: "LLPLPL",
        14: "PLLPLLLP",
        19: "LP",
        20: "LP",
    },
    "JAWA TENGAH IX": {
        1: "LPLLLPLP",
        2: "LLPLLPPL",
        3: "LLPPPLLP",
        4: "LLPLPLPL",
        5: "LLPLPPLL",
        6: "LPP",
        7: "LLPLLPLP",
        8: "LLPLPP",
        9: "LPLPLLPP",
        10: "LLPLLP",
        11: "PPLLPLPL",
        12: "LLPLLPPP",
        13: "LLPLLP",
        14: "LPLLPPPL",
        19: "LPL",
    },
    "JAWA TENGAH X": {
        1: "LLPLPPL",
        2: "LLPLPPL",
        3: "LLPLLPP",
        4: "LPLLPPL",
        5: "LLPLPPL",
        7: "LLPLPPL",
        8: "LLPLLPP",
        9: "LLPLPLP",
        10: "LPLLPPL",
        11: "LLPLLPP",
        12: "LLPLLPP",
        13: "LLPLPL",
        14: "PPLPPPP",
        19: "PP",
        20: "P",
    },
    "JAWA TIMUR I": {
        1: "LPLPPLLLPL",
        2: "LLPLPLLLPL",
        3: "LPLPLPLPLP",
        4: "LLPPLLPLPL",
        5: "LLPLLPLLPL",
        6: "LPP",
        7: "LLPLLPLLPL",
        8: "LPLLLPLLPP",
        9: "PLLLLPLLPL",
        10: "LLPLLPLPL",
        11: "LLPLLPLLPL",
        12: "LLPLLPLLPL",
        13: "LLPLPL",
        14: "PPLLLPLPLL",
        19: "LLPLLPP",
    },
    "JAWA TIMUR II": {
        1: "LLPLPLP",
        2: "LLPLPPL",
        3: "LPLLLPP",
        4: "LLPLPPL",
        5: "LLPLLPP",
        6: "PL",
        7: "LLPLLP",
        8: "LLPPPLP",
        9: "LLPLLPP",
        10: "LLPLPLP",
        11: "LPLLPPP",
        12: "PLPLLPL",
        13: "LLPPLL",
        14: "LLPLLPP",
        19: "LLPLP",
        20: "LP",
    },
    "JAWA TIMUR IV": {
        1: "LLPLLPPL",
        2: "LLPLLPPL",
        3: "LLPLLPPL",
        4: "LLPLLPLP",
        5: "LPLPLPLL",
        6: "LPP",
        7: "LLPLLP",
        8: "LLPLPLPL",
        9: "LLPLLPLP",
        10: "LLPLPPL",
        11: "PLLPLPPP",
        12: "PLLLPLPL",
        13: "LPPLLP",
        14: "LLPLLPLP",
        19: "LLPLLP",
        20: "P",
    },
    "JAWA TIMUR VIII": {
        1: "LLPLLPLLPL",
        2: "LLPLLPLLPL",
        3: "LLPLLPLPLL",
        4: "LLPLLPLLPL",
        5: "LLPLLPLPPL",
        6: "LPL",
        7: "PLLPLLLPLL",
        8: "LLPLLPP",
        9: "LLPLLPLPLL",
        10: "LPLLPLPLLL",
        11: "LLPPLPPPPP",
        12: "PLLPLLLLPL",
        13: "PLLPLP",
        14: "LLPLLPLLPL",
        19: "LLPLLPLP",
        20: "P",
    },
    "JAWA TIMUR IX": {
        1: "LPLPLL",
        2: "LLPLPL",
        3: "LLPLPL",
        4: "LPLPLL",
        5: "LLPLLP",
        6: "LP",
        7: "LLPLLP",
        8: "LLPLPL",
        9: "PLLPLP",
        10: "LLPPLL",
        11: "LPPPPP",
        12: "LLPLPL",
        13: "LPL",
        14: "LLPLLP",
        19: "LLPLP",
    },
    "JAWA TIMUR X": {
        1: "LLPLPL",
        2: "LLPLLP",
        3: "LLPLLP",
        4: "LLPLLP",
        5: "LLPLLP",
        6: "LP",
        7: "LLPLPL",
        8: "LLPLLP",
        9: "LLPLLP",
        10: "LPLLPL",
        11: "LPPLPP",
        12: "LLPLPL",
        13: "LLPPPL",
        14: "LLPLP",
        19: "LPLLP",
        20: "P",
    },
    "JAWA TIMUR XI": {
        1: "LLPLLPLP",
        2: "LLPLPPPL",
        3: "LLPLLPLP",
        4: "LLPLLPLP",
        5: "LLPLLPPL",
        6: "LLP",
        7: "LLPLLPLP",
        8: "LLPLLPPL",
        9: "PLLPLLPL",
        10: "LLPLLPLP",
        11: "LPPLPPPP",
        12: "LLPLLPLP",
        13: "PLLP",
        14: "LLPLLPLP",
        19: "LLPLLPLP",
        20: "P",
    },
}
SCANS = {
    "RIAU II": f"{JDIH}987_L.II.9%20RIAU%20II.pdf",
    "JAWA BARAT X": f"{JDIH}987_L.II.30%20JABAR%20X.pdf",
    "KALIMANTAN SELATAN I": f"{JDIH}987_L.II.65%20KALSEL%20I.pdf",
    "KALIMANTAN SELATAN II": f"{JDIH}987_L.II.66%20KALSEL%20II.pdf",
}
LINK = re.compile(
    r'<span style="font-size: 14px;">(.*?)</span>.*?href="(https://opendata\.kpu\.go\.id/uploads/[^"]+)"', re.DOTALL
)


def files(dataset: int) -> list[tuple[str, str]]:
    page = raw.cached(SOURCE, f"{OPEN_DATA}ds/{dataset}").path.read_text(errors="replace")
    return [(html.unescape(label).strip(), url) for label, url in LINK.findall(page)]


def fetch() -> None:
    for dataset in (*CERTIFICATES, *CANDIDATES, SEATS):
        raw.fetch(SOURCE, f"{OPEN_DATA}ds/{dataset}", suffix=".html")
        for label, url in files(dataset):
            try:
                raw.fetch(SOURCE, url, suffix="." + label.rsplit(".", 1)[-1].lower())
            except httpx.HTTPStatusError as error:
                print(f"{label}: {error.response.status_code}")
    for url, _ in DECREES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in SCANS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    transcribe()


MODEL = "claude-haiku-5-5"
SYSTEM = (
    "You transcribe scanned Indonesian election certificates (Model DD1-DPR) exactly. Copy every printed table row "
    "in order, including section headers, party rows, candidate rows, and total rows. Numbers use a period as the "
    "thousands separator; copy them digit for digit without separators. Leave a cell empty when it is blank or "
    "struck through. Never compute or guess a figure."
)
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "dapil": {"type": "string"},
        "columns": {"type": "array", "items": {"type": "string"}},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "string"},
                    "label": {"type": "string"},
                    "sex": {"type": "string"},
                    "cells": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["number", "label", "sex", "cells"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["dapil", "columns", "rows"],
    "additionalProperties": False,
}
TEXT = (
    "Transcribe this certificate page. `columns` are the unit names across the top (regencies or cities) in order, "
    "then JUMLAH AKHIR. For each row give the printed number (party ballot number, candidate list number, or empty), "
    "the label (party or candidate name, or the row's text), the sex column (LK, PR, JML, or empty), and one cell "
    "per column in the same order as `columns`."
)


def _scan_pages(dapil: str) -> list[Path]:
    folder = PARSED / "scans" / re.sub(r"\W+", "_", dapil.lower())
    if not folder.exists():
        folder.mkdir(parents=True)
        subprocess.run(
            ["pdftoppm", "-r", "200", "-gray", "-png", str(_path(SCANS[dapil])), str(folder / "p")], check=True
        )
    return sorted(folder.glob("p-*.png"))


def transcribe() -> None:
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for dapil in SCANS:
        for page in _scan_pages(dapil):
            image = Image.open(page)
            image.thumbnail((2400, 2400))
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            wanted[f"{dapil}/{page.stem}"] = (
                MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=16000,
                    system=SYSTEM,
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
                                TextBlockParam(type="text", text=TEXT),
                            ],
                        }
                    ],
                    output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                ),
                (image.width * image.height / 750 * price_in + 8000 * price_out) / 1e6,
            )
    print(batch.ensure(SOURCE, "scans", wanted)[1])


MEASURES = {
    "pemilih_jml_j": "registered",
    "pengguna_jml_j": "voters",
    "suara_sah": "valid",
    "suara_tidak_sah": "invalid",
    "suara_total": "ballots",
}
TAG = re.compile(r"partai(\d+)(?:caleg(\d+))?$")
ABROAD = "LUAR NEGERI"


def _number(text: object) -> int | None:
    text = str(text or "").strip()
    return int(text) if text.isdigit() else None


def _certificate(path: Path) -> tuple[str, str, list[dict[str, object]], list[dict[str, object]]]:
    sheet = pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0)
    rows = [list(row) for row in sheet.iter_rows()]
    province = dapil = None
    header: dict[int, str] = {}
    totals: dict[tuple[str, int | None, int | None], int] = {}
    pages: dict[str, int] = {}
    values: dict[tuple[str, str, int | None, int | None], int] = {}
    candidates: dict[tuple[int, int], str] = {}
    party: int | None = None
    for row in rows:
        label = str(row[8] or "").strip()
        if label == "PROVINSI" and province is None:
            province = str(row[12]).lstrip(": ").strip()
        if label == "DAERAH PEMILIHAN" and dapil is None:
            dapil = str(row[12]).lstrip(": ").strip()
        tag = str(row[29] or "").strip()
        if tag.startswith("{REKAP_WILNAME}"):
            header = {
                index: str(row[index]).strip()
                for index in range(10, 29)
                if row[index] and str(row[index]).strip() not in ("JUMLAH PINDAHAN", "")
            }
            continue
        if not tag or tag.startswith("{DATAEND"):
            continue
        cells = {header[index]: _number(row[index]) for index in header}
        if tag in MEASURES:
            key: tuple[str, int | None, int | None] = (MEASURES[tag], None, None)
        elif (match := TAG.match(tag)) is not None:
            if match.group(2) is None:
                party = int(str(row[1]))
                pages[match.group(1)] = party
                key = ("votes", party, None)
            else:
                number = _number(row[1])
                if number is None:
                    continue
                key = ("votes", pages[match.group(1)], number)
                candidates[(pages[match.group(1)], number)] = str(row[2]).strip()
        elif tag.startswith("{F9}"):
            key = ("total", party, None)
        else:
            continue
        for unit, value in cells.items():
            if unit == "JUMLAH AKHIR":
                if value is not None:
                    totals[key] = value
            elif value is not None:
                values[(unit, *key)] = value
    if province is None or dapil is None:
        raise ValueError(f"{path.name}: no province or dapil")
    out: list[dict[str, object]] = []
    sums: dict[tuple[str, int | None, int | None], int] = {}
    for (unit, measure, party_, candidate), value in values.items():
        sums[(measure, party_, candidate)] = sums.get((measure, party_, candidate), 0) + value
        if measure != "total":
            out.append(
                {
                    "dapil": dapil,
                    "unit": unit,
                    "measure": measure,
                    "party": party_,
                    "candidate": candidate,
                    "value": value,
                }
            )
    errors = [f"{key}: units {sums.get(key)}, total {value}" for key, value in totals.items() if sums.get(key) != value]
    parties = {party_ for _, party_, _ in totals if party_ is not None}
    units = {unit for unit, *_ in values}
    for unit in units:
        get = lambda measure, party_=None, candidate=None: values.get((unit, measure, party_, candidate), 0)
        for party_ in parties:
            listed = get("votes", party_) + sum(
                value for (u, m, p, c), value in values.items() if u == unit and m == "votes" and p == party_ and c
            )
            if listed != get("total", party_):
                errors.append(f"{unit} party {party_}: {listed} against B {get('total', party_)}")
        if sum(get("total", party_) for party_ in parties) != get("valid"):
            errors.append(f"{unit}: parties sum to {sum(get('total', p) for p in parties)}, valid {get('valid')}")
        if get("valid") + get("invalid") != get("ballots"):
            errors.append(f"{unit}: valid and invalid against ballots {get('ballots')}")
    if errors:
        raise ValueError(f"{dapil}: " + "; ".join(errors[:8]))
    listed: list[dict[str, object]] = [
        {"dapil": dapil, "party": party_, "number": number, "name": name}
        for (party_, number), name in sorted(candidates.items())
    ]
    return province, dapil, out, listed


ORDER = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 19, 20)
SCAN_MEASURES = (
    (re.compile(r"^(?:\d\.\s*)?JUMLAH PEMILIH \(A"), "registered"),
    (re.compile(r"^(?:\d\.\s*)?JUMLAH PENGGUNA HAK PILIH \(B"), "voters"),
    (re.compile(r"^JUMLAH SELURUH SUARA SAH DAN"), "ballots"),
    (re.compile(r"^JUMLAH SELURUH SUARA SAH"), "valid"),
    (re.compile(r"^JUMLAH SUARA TIDAK SAH"), "invalid"),
)


def _read(dapil: str, page: Path) -> dict[str, object]:
    message = json.loads(raw.cached(SOURCE, f"{dapil}/{page.stem}").path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def _boxes(dapil: str, units: list[str], out: list[dict[str, object]]) -> None:
    seen: dict[str, list[float]] = {}
    for page in _scan_pages(dapil):
        number = int(page.stem.split("-")[1])
        cells = [row for row in out if row["page"] == number]
        if not cells:
            continue
        width, height = Image.open(page).size
        tsv = subprocess.run(
            ["tesseract", str(page), "-", "--psm", "11", "tsv"], check=True, capture_output=True, text=True
        ).stdout
        tokens: dict[int, list[tuple[float, float, tuple[int, int, int, int]]]] = {}
        for word in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
            digits = re.sub(r"[^0-9]", "", word["text"] or "")
            if digits and re.fullmatch(r"[\d.,]+", word["text"].strip()):
                x, y, w, h = (int(word[key]) for key in ("left", "top", "width", "height"))
                tokens.setdefault(int(digits), []).append((x + w, y + h / 2, (x, y, x + w, y + h)))
        found = {
            id(row): tokens[int(str(row["value"]))][0]
            for row in cells
            if len(tokens.get(int(str(row["value"])), [])) == 1 and int(str(row["value"])) >= 10
        }
        columns = {
            unit: sorted(found[id(row)][0] for row in cells if row["unit"] == unit and id(row) in found)
            for unit in units
        }
        for unit, xs in columns.items():
            seen.setdefault(unit, []).extend(xs)
        right = {
            unit: (xs or sorted(seen[unit]))[len(xs or seen[unit]) // 2]
            for unit, xs in columns.items()
            if xs or seen[unit]
        }
        lines: dict[object, list[float]] = {}
        for row in cells:
            if id(row) in found:
                lines.setdefault(row["line"], []).append(found[id(row)][1])
        heights = sorted(box[3] - box[1] for _, _, box in found.values())
        tall = heights[len(heights) // 2] if heights else 20
        spaced = sorted(right.values())
        wide = min((b - a for a, b in itertools.pairwise(spaced)), default=150) * 0.8
        for row in cells:
            x = right.get(str(row["unit"]))
            ys = sorted(lines.get(row["line"], []))
            known = sorted((int(str(line)), sorted(ys_)[len(ys_) // 2]) for line, ys_ in lines.items())
            if x is None or len(known) < 2:
                raise ValueError(f"{dapil} {page.stem}: no position for {row['unit']} line {row['line']}")
            if ys:
                y = ys[len(ys) // 2]
            else:
                line = int(str(row["line"]))
                (a, ya), (b, yb) = sorted(known, key=lambda pair: abs(pair[0] - line))[:2]
                y = ya + (yb - ya) * (line - a) / (b - a)
            near = [
                box
                for right_, middle, box in tokens.get(int(str(row["value"])), [])
                if abs(right_ - x) < wide / 2 and abs(middle - y) < tall
            ]
            x0, y0, x1, y1 = near[0] if near else (x - wide, y - tall / 2 - 4, x + 4, y + tall / 2 + 4)
            row["box"] = [x0 / width, y0 / height, x1 / width, y1 / height]
            row["boxed"] = bool(near)


def _scan(dapil: str) -> tuple[str, str, list[dict[str, object]], list[dict[str, object]]]:
    units: list[str] = []
    values: dict[tuple[str, str, int | None, int | None], tuple[int, int, int]] = {}
    totals: dict[tuple[str, int | None, int | None], int | None] = {}
    names: dict[tuple[int, int], str] = {}
    parties = iter(ORDER)
    party: int | None = None
    candidate = 0
    for page in _scan_pages(dapil):
        read = _read(dapil, page)
        columns = [
            (index, column)
            for index, column in enumerate(cast(list[str], read["columns"]))
            if re.search(r"[A-Z]{3}", column) and "RINCIAN" not in column
        ]
        found = [column for _, column in columns if column != "JUMLAH AKHIR"]
        if units and found != units:
            raise ValueError(f"{dapil} {page.stem}: columns {found}, not {units}")
        units = found
        for line, row in enumerate(cast(list[dict[str, Any]], read["rows"])):
            label = str(row["label"]).strip()
            upper = re.sub(r"^\d+\.?\s+", "", label.upper())
            cells = {column: _number(str(row["cells"][index]).replace(".", "")) for index, column in columns}
            lead = _number(row["number"]) or _number(label.split()[0] if label else "")
            key: tuple[str, int | None, int | None] | None = None
            measure = next((measure for pattern, measure in SCAN_MEASURES if pattern.match(upper)), None)
            if measure is not None:
                if measure in ("registered", "voters") and row["sex"] != "JML":
                    continue
                key = (measure, None, None)
            elif upper.startswith("PARTAI "):
                party, candidate = next(parties), 0
                if lead is not None and lead != party:
                    raise ValueError(f"{dapil} {page.stem}: party {lead} printed where {party} was expected")
                key = ("votes", party, None)
            elif upper.startswith("JUMLAH SUARA SAH PARTAI"):
                key = ("total", party, None)
            elif party is not None and not upper.startswith("NOMOR") and any(v is not None for v in cells.values()):
                candidate += 1
                if lead is not None and lead != candidate:
                    raise ValueError(f"{dapil} {page.stem}: candidate {lead} printed where {candidate} was expected")
                if not re.sub(r"^\d+\s*", "", label) and not any(cells.values()):
                    continue
                key = ("votes", party, candidate)
                names[(party, candidate)] = re.sub(r"^\d+\s+", "", label)
            if key is None:
                continue
            for column, value in cells.items():
                if column == "JUMLAH AKHIR":
                    totals[key] = value
                elif value is not None:
                    values[(column, *key)] = (value, int(page.stem.split("-")[1]), line)
    if next(parties, None) is not None:
        raise ValueError(f"{dapil}: fewer than {len(ORDER)} parties read")
    get = lambda unit, *key: values.get((unit, *key), (0, 0, 0))[0]
    keys = {key[1:] for key in values}
    errors = []
    for unit in units:
        for party_ in ORDER:
            listed = get(unit, "votes", party_, None) + sum(
                get(unit, "votes", party_, number) for _, p, number in keys if p == party_ and number
            )
            if listed != get(unit, "total", party_, None):
                errors.append(f"{unit} party {party_}: {listed} against B {get(unit, 'total', party_, None)}")
        if sum(get(unit, "total", party_, None) for party_ in ORDER) != get(unit, "valid", None, None):
            errors.append(f"{unit}: parties do not sum to the valid votes")
        if get(unit, "valid", None, None) + get(unit, "invalid", None, None) != get(unit, "ballots", None, None):
            errors.append(f"{unit}: valid and invalid against ballots")
        if get(unit, "ballots", None, None) != get(unit, "voters", None, None):
            errors.append(f"{unit}: ballots against voters")
    for key in keys:
        if key[2] is None and sum(get(unit, *key) for unit in units) != totals.get(key):
            errors.append(f"{key}: units against JUMLAH AKHIR {totals.get(key)}")
    for party_ in ORDER:
        sums = sorted(
            sum(get(unit, "votes", party_, number) for unit in units) for _, p, number in keys if p == party_ and number
        )
        read = [value for key, value in totals.items() if key[1] == party_ and key[2] is not None and value is not None]
        if any(sums.count(value) < read.count(value) for value in read) or len(read) < len(sums) - 1:
            errors.append(f"party {party_}: candidate totals {sorted(read)} against units {sums}")
    if errors:
        raise ValueError(f"{dapil}: " + "; ".join(errors[:8]))
    province = re.sub(r"\s+[IVX]+$", "", dapil)
    out: list[dict[str, object]] = [
        {
            "province": province,
            "dapil": dapil,
            "unit": unit,
            "measure": measure,
            "party": party_,
            "candidate": candidate_,
            "value": value,
            "page": page,
            "line": line,
        }
        for (unit, measure, party_, candidate_), (value, page, line) in values.items()
        if measure != "total"
    ]
    listed: list[dict[str, object]] = [
        {"dapil": dapil, "party": party_, "number": number, "name": name}
        for (party_, number), name in sorted(names.items())
    ]
    _boxes(dapil, units, out)
    return province, dapil, out, listed


SEATS_ROW = re.compile(r"^\s*(\d{1,2})\s+\S.*?\s+(\d{1,3}(?:\.\d{3})*)\s+(\d{1,2})\s*$")


PAGE_45: dict[str, tuple[tuple[int, int, int], ...]] = {
    "PAPUA": ((1, 399011, 1), (2, 221282, 1), (3, 368618, 1), (4, 286762, 1), (5, 787753, 3), (8, 80318, 0)),
    "PAPUA BARAT": ((1, 29843, 0), (2, 73961, 0), (3, 101713, 1), (4, 103012, 1), (5, 109157, 1), (8, 16162, 0)),
}
PAGE_45["PAPUA"] += ((10, 45435, 0), (12, 443393, 2), (14, 313485, 1))
PAGE_45["PAPUA BARAT"] += ((10, 8089, 0), (12, 26429, 0), (14, 68147, 0))


def _seats_1317() -> list[dict[str, object]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(_path(DECREES["1317"][0])), "-"], capture_output=True, text=True, check=True
    ).stdout
    dapil = None
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        if (match := re.match(r"\s*DAERAH PEMILIHAN\s*:\s*(.+?)\s*$", line)) is not None:
            dapil = re.sub(r"\s+", " ", match.group(1)).upper()
        elif dapil is not None and (match := SEATS_ROW.match(line)) is not None:
            rows.append(
                {
                    "dapil": dapil,
                    "party": int(match.group(1)),
                    "votes": int(match.group(2).replace(".", "")),
                    "seats": int(match.group(3)),
                }
            )
    rows += [
        {"dapil": dapil, "party": party, "votes": votes, "seats": seats}
        for dapil, parties in PAGE_45.items()
        for party, votes, seats in parties
    ]
    return rows


NATIONAL = {
    1: 13570097,
    2: 17594839,
    3: 27053961,
    4: 17229789,
    5: 12661792,
    6: 702536,
    7: 2929495,
    8: 11493663,
    9: 3738320,
    10: 6323147,
    11: 2650361,
    12: 9572623,
    13: 2161507,
    14: 10876057,
    19: 1099848,
    20: 312775,
}
NATIONAL_VALID = 139970810
ELECTED = re.compile(
    r"^\s*(\d{1,2})\s+(?:([A-Za-z][A-Za-z .]*?)\s+)?(\d{1,2})\s+(?:(\S.*?)\s+)?(\d{1,3}(?:[.,]\d{3})+)\s+(\d{1,2})(?:\s|$)"
)


def _path(url: str) -> Path:
    return raw.cached(SOURCE, url).path


IMAGE_ELECTED = {
    ("1341", "KALIMANTAN BARAT I"): (
        (1, 56335, "DANIEL JOHAN"),
        (1, 35242, "KATHERINE A. OE"),
        (1, 285797, "Drs. CORNELIS, M.H."),
        (3, 33006, "MARIA LESTARI, S.Pd."),
        (1, 108520, "MAMAN ABDURRAHMAN, S.T."),
        (1, 75188, "SYARIF ABDULLAH ALKADRIE, SH., MH"),
        (1, 45516, "H ALIFUDIN, SE, MM"),
        (1, 35910, "H. BOYMAN HARUN, SH"),
    ),
    ("1341", "PAPUA"): (
        (3, 151994, "MARTHEN DOUW"),
        (8, 63586, "YAN PERMENAS MANDENAS, S.Sos., M.Si"),
        (1, 226292, "KAMARUDIN WATUBUN, S.H, M.H."),
        (2, 110045, "TRIFENA M TINAL, B.Sc"),
        (5, 274426, "ROBERTH ROUW"),
        (1, 161898, "H. SULAEMAN L. HAMZAH"),
        (6, 118421, "INA ELISABETH KOBAK, ST"),
        (1, 144120, "JOHN SIFFY MIRIN"),
        (2, 130304, "MESAKH MIRIN"),
        (1, 134299, "WILLEM WANDIK"),
    ),
    ("1318", "PAPUA BARAT"): (
        (2, 66555, "JIMMY DEMIANUS IJIE, S.H."),
        (1, 62303, "ROBERT J. KARDINAL, S.AB"),
        (1, 50415, "RICO SIA"),
    ),
}


def _elected(decree: str) -> dict[str, list[dict[str, object]]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(_path(DECREES[decree][0])), "-"], capture_output=True, text=True, check=True
    ).stdout
    dapil = None
    rows: dict[str, list[dict[str, object]]] = {}
    for line in text.splitlines():
        if (match := re.match(r"\s*DAERAH PEMILIHAN\s*:\s*(.+?)\s*$", line)) is not None:
            dapil = re.sub(r"\s+", " ", match.group(1)).upper()
            rows[dapil] = []
        elif dapil is not None and (match := ELECTED.match(line)) is not None:
            rows[dapil].append(
                {
                    "dapil": dapil,
                    "votes": int(re.sub(r"[.,]", "", match.group(5))),
                    "candidate_number": int(match.group(3)),
                    "name": (match.group(4) or "").strip(),
                    "decree": decree,
                }
            )
    for (source, dapil), members in IMAGE_ELECTED.items():
        if source == decree:
            rows[dapil] = [
                {"dapil": dapil, "votes": votes, "candidate_number": number, "name": name, "decree": decree}
                for number, votes, name in members
            ]
    return rows


THRESHOLD = 4


def _sainte_lague(votes: dict[int, int], seats: int) -> dict[int, int]:
    won: dict[int, int] = {}
    for _ in range(seats):
        best = max(votes, key=lambda party: votes[party] / (2 * won.get(party, 0) + 1))
        won[best] = won.get(best, 0) + 1
    return won


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    listed: list[dict[str, object]] = []
    for dataset in CERTIFICATES:
        for label, url in files(dataset):
            if not label.lower().endswith(".xlsx"):
                continue
            try:
                path = _path(url)
            except LookupError:
                print(f"{label}: not cached")
                continue
            province, dapil, rows, candidates = _certificate(path)
            results += [{"province": province, **row, "page": None, "url": url} for row in rows]
            listed += candidates
    for dapil, url in SCANS.items():
        _, _, rows, candidates = _scan(dapil)
        results += [{**row, "url": url} for row in rows]
        listed += candidates
    dapils = {str(row["dapil"]) for row in results}
    lists = pl.concat(
        [
            pl.read_csv(_path(url), infer_schema_length=0)
            for dataset in CANDIDATES
            for label, url in files(dataset)
            if label.lower().endswith(".csv")
        ]
    )
    lists = lists.select(
        dapil=pl.col("NAMA_DAPIL").str.strip_chars().str.to_uppercase(),
        party=pl.col("NOMOR PARTAI").cast(pl.Int64),
        number=pl.col("NOMOR URUT").cast(pl.Int64),
        listed_name=pl.col("NAMA CALEG").str.strip_chars(),
        sex=pl.col("JENIS_KELAMIN").str.strip_chars(),
    ).unique(["dapil", "party", "number"])
    scanned = pl.DataFrame(
        [
            {"dapil": dapil, "party": party, "number": number, "listed_name": None, "sex": sex}
            for dapil, parties in DCT_1395.items()
            for party, sexes in parties.items()
            for number, sex in enumerate(sexes, 1)
        ],
        schema=lists.schema,
    )
    if scanned.join(lists, on=["dapil", "party", "number"]).height:
        raise ValueError("decree 1395 overlaps the open data candidate lists")
    lists = pl.concat([lists, scanned])
    candidates = pl.DataFrame(listed).join(lists, on=["dapil", "party", "number"], how="left")
    seats = pl.read_excel(_path(next(url for _, url in files(SEATS))), infer_schema_length=0)
    seats = seats.select(
        dapil=pl.col("nama_dapil").str.strip_chars().str.to_uppercase(),
        party=pl.col("nomor_urut").cast(pl.Int64),
        seats=pl.col("perolehan kursi").cast(pl.Int64),
    ).filter(pl.col("seats") > 0)
    if seats["seats"].sum() != 575 or seats["dapil"].n_unique() != 80:
        raise ValueError("seat table does not give 575 seats in 80 dapils")
    declared = pl.DataFrame(_seats_1317())
    if declared["dapil"].n_unique() != 80 or declared["seats"].sum() != 575:
        raise ValueError("decree 1317 does not give 575 seats in 80 dapils")
    if (
        not declared.filter(pl.col("seats") > 0)
        .select("dapil", "party", "seats")
        .sort("dapil", "party")
        .equals(seats.sort("dapil", "party"))
    ):
        raise ValueError("decree 1317 and the seat table disagree")
    party_votes = (
        pl.DataFrame(results, infer_schema_length=None)
        .filter(pl.col("measure") == "votes")
        .group_by("dapil", "party")
        .agg(pl.col("value").sum().alias("certificate"))
    )
    check = declared.join(party_votes, on=["dapil", "party"], how="left").filter(
        pl.col("certificate") != pl.col("votes")
    )
    if check.height:
        raise ValueError(f"decree 1317 party votes differ from the certificates: {check.rows()[:8]}")
    if sum(NATIONAL.values()) != NATIONAL_VALID:
        raise ValueError("national party votes do not sum to the valid votes")
    elected = _elected("1318") | _elected("1341")
    votes = pl.DataFrame(results, infer_schema_length=None).filter(pl.col("measure") == "votes")
    totals = (
        votes.filter(pl.col("candidate").is_not_null())
        .group_by("dapil", "party", "candidate")
        .agg(pl.col("value").sum().alias("votes"))
    )
    winners: list[dict[str, object]] = []
    for dapil in sorted(dapils):
        rows = elected.get(dapil, [])
        expected = seats.filter(pl.col("dapil") == dapil)
        if len(rows) != expected["seats"].sum():
            print(f"{dapil}: {len(rows)} elected rows against {expected['seats'].sum()} seats")
            continue
        pool = totals.filter(pl.col("dapil") == dapil)
        for row in rows:
            match = pool.filter((pl.col("votes") == row["votes"]) & (pl.col("candidate") == row["candidate_number"]))
            if match.height != 1:
                raise ValueError(f"{dapil}: no unique candidate for {row}")
            winners.append({"dapil": dapil, "party": match["party"][0], "candidate": match["candidate"][0], **row})
        won = pl.DataFrame(winners).filter(pl.col("dapil") == dapil).group_by("party").len()
        check = expected.join(won, on="party", how="full", coalesce=True)
        if check.filter(pl.col("seats") != pl.col("len")).height or check.null_count().sum_horizontal()[0]:
            raise ValueError(f"{dapil}: elected members do not match the seat table")
    pl.DataFrame(results, infer_schema_length=None).write_parquet(PARSED / "results.parquet")
    candidates.write_parquet(PARSED / "candidates.parquet")
    seats.write_parquet(PARSED / "seats.parquet")
    pl.DataFrame([{"party": party, "votes": value} for party, value in NATIONAL.items()]).write_parquet(
        PARSED / "national.parquet"
    )
    pl.DataFrame(winners).write_parquet(PARSED / "elected.parquet")
    declared.write_parquet(PARSED / "declared.parquet")
    passed = {party for party, votes in NATIONAL.items() if votes * 100 >= THRESHOLD * NATIONAL_VALID}
    differ = []
    for dapil, group in declared.group_by("dapil"):
        tally = {party: votes for party, votes in group.select("party", "votes").rows() if party in passed}
        if _sainte_lague(tally, int(group["seats"].sum())) != {
            party: seats for party, seats in group.select("party", "seats").rows() if seats
        }:
            differ.append(dapil[0])
    print(f"Sainte-Lague among the {len(passed)} parties over {THRESHOLD} percent differs in {sorted(differ)}")
    print(
        f"{len(dapils)} dapils, {len(results)} results rows, {candidates.height} candidates, "
        f"{len(winners)} elected, {candidates['sex'].null_count()} candidates without a list row"
    )


CERTIFICATE_TITLE = (
    "Sertifikat Rekapitulasi Hasil Penghitungan Perolehan Suara Partai Politik dan Suara Calon Anggota Dewan "
    "Perwakilan Rakyat dari Setiap Kabupaten/Kota di Tingkat Provinsi dalam Pemilihan Umum Tahun 2019 (Model DD1-DPR), "
    "Open Data KPU"
)
SCAN_TITLE = (
    "Lampiran II Keputusan KPU Nomor 987/PL.01.8-Kpt/06/KPU/V/2019: Sertifikat Rekapitulasi Hasil Penghitungan "
    "Perolehan Suara Anggota Dewan Perwakilan Rakyat (Model DD1-DPR), Daerah Pemilihan {dapil}"
)
LISTS_TITLE = "Daftar Calon Tetap Anggota Dewan Perwakilan Rakyat Pemilu 2019, Open Data KPU"
SEATS_TITLE = "Perolehan Kursi DPR RI Pemilu Tahun 2019, Open Data KPU"
OPEN_LICENSE = "Open Data KPU: free use with the source credited and the content unaltered"
DECREE_LICENSE = "Public record: official decree, no license stated"
SYSTEM = (
    "The 575 seats of the House of Representatives (Dewan Perwakilan Rakyat) are filled in 80 electoral districts "
    "(daerah pemilihan) of 3 to 10 seats under UU 7/2017. Only parties with at least 4 percent of the valid votes "
    "nationwide take part in the allocation (art. 414); each district's seats are divided among them by the "
    "Sainte-Laguë method with divisors 1, 3, 5, 7, ... (art. 415), and each party's seats go to its candidates with "
    "the most votes (art. 422). A voter marks a party or one of its candidates; both count toward the party."
)
METHODOLOGY = (
    "Party-only votes and candidate votes, registered voters, voters, valid and invalid votes, and ballots by regency "
    "or city (and abroad in DKI Jakarta II), from the district recapitulation certificates (Model DD1-DPR). 76 "
    "districts come from KPU's open-data workbooks; Riau II, Jawa Barat X, and Kalimantan Selatan I and II have only "
    "the scanned certificates attached to decree 987/2019, read by a vision model. In every district each unit's "
    "party-only and candidate votes sum to the party's printed total, the parties to the valid votes, and valid and "
    "invalid votes to the ballots; every column sums to the certificate's district total; and each party's district "
    "votes equal those in decree 1317/2019. Seats are as declared in decree 1317/2019 and KPU's open-data seat table, "
    "which agree in all 80 districts, and Sainte-Laguë among the nine parties over the national threshold "
    "reproduces them. Elected members are from decree 1318/2019 as amended by decree 1341/2019."
)
REPLACED = (
    "Decree 1341/2019 replaced elected members whom their parties had expelled: in Jawa Barat XI, Jawa Tengah I, "
    "Kalimantan Barat I, and Papua, Gerindra's seats went to the next candidates instead of the ones decree 1318/2019 "
    "first declared, so the elected candidates here are not always the party's top vote-getters."
)
ALIASES = {
    "TOBASAMOSIR": "TOBA",
    "PAHUWATO": "POHUWATO",
    "MALUKUTENGGARABARAT": "KEPULAUANTANIMBAR",
    "KOTAPADANGSIDEMPUAN": "KOTAPADANGSIDIMPUAN",
}
DAY = "2019-04-17"
CODES = {"votes": 1, "registered": 2, "ballots": 3, "valid": 4, "invalid": 5, "voters": 14}


def _squash(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def emit() -> Path:
    from datetime import date
    from uuid import UUID

    from ea.changeset import Changeset
    from ea.sources.id.kpu_pilpres import PROVINCES
    from ea.store import connect

    results = pl.read_parquet(PARSED / "results.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    declared = pl.read_parquet(PARSED / "declared.parquet")
    elected = pl.read_parquet(PARSED / "elected.parquet")
    winners = {(dapil, party, number) for dapil, party, number in elected.select("dapil", "party", "candidate").rows()}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Indonesia's 2019 House of Representatives (DPR) election: party and candidate votes by regency and "
                "city in all 80 districts, declared seats, and elected members"
            ),
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        indonesia = cs.polity("ID")
        election = cs.key("id.election", DAY)
        body = cs.key("id.body", "dpr")
        if election in cs.minted or body in cs.minted:
            raise ValueError("publish id.kpu_pilpres and id.kpu before id.kpu_dpr")
        on = date.fromisoformat(DAY)

        def source(title: str, url: str, license: str, license_url: str) -> UUID:
            found = cs.source(KPU, title)
            cs.add(
                "source",
                id=found,
                kind="official_body",
                publisher=KPU,
                title=title,
                url=url,
                license=license,
                license_url=license_url,
            )
            return found

        certificates = source(CERTIFICATE_TITLE, OPEN_DATA, OPEN_LICENSE, OPEN_DATA)
        lists = source(LISTS_TITLE, OPEN_DATA, OPEN_LICENSE, OPEN_DATA)
        for dataset in CANDIDATES:
            for label, url in files(dataset):
                if label.lower().endswith(".csv"):
                    cs.artifact(raw.cached(SOURCE, url), lists)
        table = source(SEATS_TITLE, f"{OPEN_DATA}ds/{SEATS}", OPEN_LICENSE, OPEN_DATA)
        cs.artifact(raw.cached(SOURCE, next(url for _, url in files(SEATS))), table)
        decrees: dict[str, UUID] = {}
        for key, (url, title) in DECREES.items():
            decrees[key] = source(title, url, DECREE_LICENSE, "https://jdih.kpu.go.id")
            cs.artifact(raw.cached(SOURCE, url), decrees[key])
        scans: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        for url in results["url"].unique().sort().to_list():
            dapil = next((dapil for dapil, scan in SCANS.items() if scan == url), None)
            owner = certificates
            if dapil is not None:
                owner = scans[dapil] = source(
                    SCAN_TITLE.format(dapil=dapil), url, DECREE_LICENSE, "https://jdih.kpu.go.id"
                )
            artifacts[url] = cs.artifact(raw.cached(SOURCE, url), owner)

        pages: dict[tuple[str, int], UUID] = {}

        def page(url: str, number: int) -> UUID:
            if (url, number) not in pages:
                cached = raw.cached(SOURCE, url)
                image = DATA / "pages" / SOURCE / f"{cached.sha256}-{number}.png"
                if not image.exists():
                    image.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(cached.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = Image.open(image).size
                found = pages[url, number] = cs.page(artifacts[url], number)
                cs.add(
                    "source_page",
                    id=found,
                    artifact_id=artifacts[url],
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            return pages[url, number]

        system = cs.key("id.electoral_system", "dpr:2019")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=indonesia,
            rules={
                "formula": "sainte_lague",
                "threshold": {"percent": THRESHOLD, "level": "party", "scope": "national"},
                "lists": {"order": "open", "vacancy": "next_by_votes"},
            },
            rules_version=1,
        )
        name(system, "House of Representatives open-list proportional representation, 2019", "en", "english")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        parties = {
            int(number): cs.key("id.party", key)
            for number, key in conn.execute(
                """select c.locator->>'nomor_urut', e.value from citation c
                   join external_id e on e.entity_id = c.entity_id and e.scheme = 'id.party'
                   where c.locator->>'election' = '2019'"""
            ).fetchall()
        }
        if set(ORDER) - set(parties):
            raise ValueError(f"parties {sorted(set(ORDER) - set(parties))} are not published")

        codes: dict[tuple[str, str], str] = {}
        for code, text in conn.execute(
            """select c.code, n.text from area a
               join area_code c on c.area_id = a.id and c.valid @> %s::date
               join name n on n.entity_id = a.id and n.lang = 'id' and n.kind = 'official'
               where a.scheme_code = 'id.kabkota'""",
            [on],
        ).fetchall():
            bare = re.sub(r"^(Kabupaten Administrasi|Kota Administrasi|Kabupaten|Kota)\s+", "", text)
            city = text.startswith("Kota ") and not text.startswith("Kota Administrasi")
            codes[code[:2], _squash(("Kota " if city else "") + bare)] = code
        units: dict[tuple[str, str], UUID] = {}

        def unit(province: str, label: str) -> UUID:
            if (province, label) in units:
                return units[province, label]
            if label == ABROAD:
                found = cs.unit("id.nation", "LN")
                if found in cs.minted:
                    raise ValueError("publish id.kpu_pilpres before id.kpu_dpr")
            else:
                squashed = _squash(label)
                code = codes[PROVINCES[province], ALIASES.get(squashed, squashed)]
                found = cs.unit("id.kabkota", code)
                if found in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=found,
                        polity_id=indonesia,
                        scheme_code="id.kabkota",
                        code=code,
                        area_relation="equals",
                        area_id=cs.area("id.kabkota", code, on),
                        kind="standard",
                        valid_from=None,
                    )
            units[province, label] = found
            return found

        provinces = results.select("province", "dapil").unique()
        whole = {province for province, count in provinces.group_by("province").len().rows() if count == 1}
        for dapil, province in sorted(provinces.select("dapil", "province").rows()):
            key = f"{DAY}:dpr:{_slug(dapil)}"
            contest = cs.key("id.contest", key)
            seats = declared.filter(pl.col("dapil") == dapil)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=cs.area("id.provinsi", PROVINCES[province], on) if province in whole else None,
                term="full",
                magnitude=int(seats["seats"].sum()),
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            title = (
                dapil.title().replace(" Ii", " II").replace(" Iv", " IV").replace(" Vi", " VI").replace(" Ix", " IX")
            )
            title = re.sub(r"\b(I|V|X)+\b", lambda match: match.group(0).upper(), title, flags=re.IGNORECASE)
            name(contest, f"Dewan Perwakilan Rakyat, {title}")
            name(contest, f"House of Representatives, {title}", "en", "english")
            cs.add("slug", path=f"id/{DAY}-general/dpr/{_slug(dapil)}", entity_id=contest)
            if dapil in ("JAWA BARAT XI", "JAWA TENGAH I", "KALIMANTAN BARAT I", "PAPUA"):
                cs.add("note", entity_id=contest, kind="caveat", body_md=REPLACED)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=scans.get(dapil, certificates),
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHODOLOGY)
            rows = results.filter(pl.col("dapil") == dapil)
            won = dict(seats.select("party", "seats").rows())
            options: dict[tuple[int, int | None], UUID] = {}
            for party in sorted({int(party) for party in rows["party"].drop_nulls()}):
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
                legend = options[party, None] = cs.key("id.option", f"{key}:{party}:party")
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
                for number, printed, listed_name, sex in (
                    candidates.filter((pl.col("dapil") == dapil) & (pl.col("party") == party))
                    .sort("number")
                    .select("number", "name", "listed_name", "sex")
                    .rows()
                ):
                    ballot = listed_name or printed
                    person = cs.key(f"{SOURCE}.person", f"2019:{key}:{party}:{number}")
                    if person in cs.minted or sex is not None:
                        cs.add("person", id=person, gender={"L": "male", "P": "female"}.get(sex))
                    if person in cs.minted:
                        name(person, ballot)
                    option = options[party, number] = cs.key("id.option", f"{key}:{party}:{number}")
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
                    cs.add(
                        "candidacy",
                        id=cs.key("id.candidacy", f"{key}:{party}:{number}"),
                        person_id=person,
                        option_id=option,
                        role="list_member",
                        ballot_name=ballot,
                        is_independent=False,
                        member_of_party_id=parties[party],
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if (dapil, party, number) in winners else "lost",
                        seats=None,
                    )
            for province_, label, measure, party, number, value, url, number_, box in rows.select(
                "province", "unit", "measure", "party", "candidate", "value", "url", "page", "box"
            ).rows():
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit(province_, label),
                    "option_id": None if party is None else options[party, number],
                    "channel": 0,
                    "measure": CODES[measure],
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=0, value=value)
                if number_ is None:
                    cs.add("value_locator", **fact, page_id=None, source_id=certificates)
                else:
                    x0, y0, x1, y1 = box
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=page(url, number_),
                        source_id=None,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                    )
        return cs.write()
