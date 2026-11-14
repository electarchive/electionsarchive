import base64
import csv
import html
import io
import json
import re
import struct
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.changeset import Changeset, slug
from ea.sources.id.kpu_pilpres import DAYS, PROVINCES
from ea.store import DATA, connect

SOURCE = "id.kpu_dpd"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
KPU = "Komisi Pemilihan Umum Republik Indonesia"
OPEN_DATA = "https://opendata.kpu.go.id/"
JDIH = "https://jdih.kpu.go.id/data/data_kepkpu/"
CERTIFICATES = (80, 96, 112, 124, 131, 132, 153, 156, 159)
DECREES = {
    "1315": (
        f"{JDIH}KPT%201315%20THN%202019.pdf",
        (
            "Keputusan KPU Nomor 1315/PL.01.8-Kpt/06/KPU/VIII/2019 tentang Perubahan atas Keputusan KPU Nomor "
            "987/PL.01.8-Kpt/06/KPU/V/2019 tentang Penetapan Hasil Pemilihan Umum Presiden dan Wakil Presiden, Anggota "
            "DPR, DPD, DPRD Provinsi, dan DPRD Kabupaten/Kota Secara Nasional dalam Pemilihan Umum Tahun 2019"
        ),
    ),
    "1319": (
        f"{JDIH}KPT%201319%20THN%202019.pdf",
        (
            "Keputusan KPU Nomor 1319/PL.01.9-Kpt/06/KPU/VIII/2019 tentang Penetapan Calon Terpilih Anggota Dewan "
            "Perwakilan Daerah dalam Pemilihan Umum Tahun 2019"
        ),
    ),
    "360": (
        f"{JDIH}2024kpt360_L4.pdf",
        (
            "Lampiran IV Keputusan KPU Nomor 360 Tahun 2024 tentang Penetapan Hasil Pemilihan Umum Presiden dan Wakil "
            "Presiden, Anggota DPR, DPD, DPRD Provinsi, dan DPRD Kabupaten/Kota Secara Nasional dalam Pemilihan Umum "
            "Tahun 2024: Perolehan Suara Sah dan Peringkat Suara Sah Calon Anggota DPD"
        ),
    ),
    "1207": (
        f"{JDIH}2024kpt1207.pdf",
        (
            "Keputusan KPU Nomor 1207 Tahun 2024 tentang Penetapan Calon Terpilih dan Calon Pengganti Antarwaktu "
            "Anggota Dewan Perwakilan Daerah dalam Pemilihan Umum Tahun 2024"
        ),
    ),
}
SCANS = {
    "DAERAH ISTIMEWA YOGYAKARTA": f"{JDIH}987_L.III.15.DIY.pdf",
    "JAWA TIMUR": f"{JDIH}987_L.III.16.JATIM.pdf",
}
SCAN_TITLE = (
    "Lampiran III Keputusan KPU Nomor 987/PL.01.8-Kpt/06/KPU/V/2019: Sertifikat Rekapitulasi Hasil Penghitungan "
    "Perolehan Suara Calon Anggota DPD dari Setiap Kabupaten/Kota (Model DD1-DPD), Provinsi "
)
LINK = re.compile(
    r'<span style="font-size: 14px;">(.*?)</span>.*?href="(https://opendata\.kpu\.go\.id/uploads/[^"]+)"', re.DOTALL
)


def files(dataset: int) -> list[tuple[str, str]]:
    page = raw.cached(SOURCE, f"{OPEN_DATA}ds/{dataset}").path.read_text(errors="replace")
    return [(html.unescape(label).strip(), url) for label, url in LINK.findall(page)]


def fetch() -> None:
    for dataset in CERTIFICATES:
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
    "You transcribe scanned Indonesian election certificates (Model DD1-DPD) exactly. Copy every printed table row "
    "in order, including section headers, candidate rows, and total rows. Numbers use a period as the thousands "
    "separator; copy them digit for digit without separators. Leave a cell empty when it is blank or struck through. "
    "Never compute or guess a figure."
)
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
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
    "required": ["columns", "rows"],
    "additionalProperties": False,
}
TEXT = (
    "Transcribe this certificate page. `columns` are the column headings of the RINCIAN part in order (JUMLAH "
    "PINDAHAN if printed, the regency or city names, then JUMLAH AKHIR if printed), skipping empty shaded columns. "
    "For each row give the printed number (the row's item number or candidate number, or empty), the label (the "
    "candidate name or the row's text), the sex column (LK, PR, JML, or empty), and one cell per entry of `columns` "
    "in the same order."
)


def _scan_pages(province: str) -> list[Path]:
    folder = PARSED / "scans" / re.sub(r"\W+", "_", province.lower())
    if not folder.exists():
        folder.mkdir(parents=True)
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                "200",
                "-gray",
                "-png",
                str(raw.cached(SOURCE, SCANS[province]).path),
                str(folder / "p"),
            ],
            check=True,
        )
    return sorted(folder.glob("p-*.png"))


def transcribe() -> None:
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for province in SCANS:
        for page in _scan_pages(province):
            image = Image.open(page)
            image.thumbnail((2400, 2400))
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            wanted[f"{province}/{page.stem}"] = (
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
FINAL = "JUMLAH AKHIR"


def _number(text: object) -> int | None:
    text = str(text or "").strip()
    return int(text) if text.isdigit() else None


def _certificate(path: Path) -> tuple[str, list[dict[str, object]], dict[int, str]]:
    sheet = pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0)
    province = None
    header: dict[int, str] = {}
    values: dict[tuple[str, str, int | None], int] = {}
    names: dict[int, str] = {}
    for row in sheet.iter_rows():
        if str(row[8] or "").strip() == "PROVINSI" and province is None:
            province = str(row[12]).lstrip(": ").strip()
        tag = str(row[29] or "").strip()
        if tag.startswith("{REKAP_WILNAME}"):
            header = {
                index: str(row[index]).strip()
                for index in range(10, 26)
                if row[index] and str(row[index]).strip() not in ("JUMLAH PINDAHAN", "")
            }
            continue
        if tag in MEASURES:
            key: tuple[str, int | None] = (MEASURES[tag], None)
        elif tag.startswith("{F9}"):
            number = _number(row[0])
            if number is None:
                continue
            names[number] = re.sub(r"\s+", " ", str(row[1])).strip()
            key = ("votes", number)
        else:
            continue
        for index, unit in header.items():
            value = _number(row[index])
            if value is not None:
                values[(unit, *key)] = value
    if province is None:
        raise ValueError(f"{path.name}: no province")
    return province, _checked(province, values, names), names


def _checked(
    province: str, values: dict[tuple[str, str, int | None], int], names: dict[int, str]
) -> list[dict[str, object]]:
    units = {unit for unit, *_ in values} - {FINAL}
    errors: list[str] = []
    for measure, candidate in {(measure, candidate) for _, measure, candidate in values}:
        total = sum(values.get((unit, measure, candidate), 0) for unit in units)
        if total != values.get((FINAL, measure, candidate)):
            errors.append(f"{measure} {candidate}: units {total}, total {values.get((FINAL, measure, candidate))}")
    for unit in (*units, FINAL):
        get = lambda measure, candidate=None: values.get((unit, measure, candidate), 0)
        if sum(get("votes", number) for number in names) != get("valid"):
            errors.append(f"{unit}: candidates {sum(get('votes', n) for n in names)}, valid {get('valid')}")
        if get("valid") + get("invalid") != get("ballots"):
            errors.append(f"{unit}: valid and invalid against ballots {get('ballots')}")
    if errors:
        raise ValueError(f"{province}: " + "; ".join(errors[:8]))
    return [
        {"year": 2019, "province": province, "unit": unit, "measure": measure, "candidate": candidate, "value": value}
        for (unit, measure, candidate), value in values.items()
    ]


ROMAN = re.compile(r"[IVXL]+")
VOTES = re.compile(r"\d{1,3}(?:\.\d{3})*")


def _lines(url: str) -> list[list[dict[str, str]]]:
    out = subprocess.run(
        ["pdftotext", "-tsv", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    words = [
        word
        for word in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
        if word["level"] == "5"
    ]
    lines: list[list[dict[str, str]]] = []
    for word in sorted(words, key=lambda word: (int(word["page_num"]), float(word["top"]))):
        first = lines[-1][0] if lines else None
        if first and first["page_num"] == word["page_num"] and float(word["top"]) - float(first["top"]) < 4:
            lines[-1].append(word)
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda word: float(word["left"]))
    return lines


def _box(word: dict[str, str]) -> list[float]:
    left, top, width, height = (float(word[key]) for key in ("left", "top", "width", "height"))
    return [left, top, left + width, top + height]


HEADER = {"NAMA", "CALON", "URUT", "DCT", "NOMOR,", "NO.", "URAIAN", "jdih.kpu.go.id", "PEROLEHAN", "SUARA"}


def _ranked(url: str) -> dict[str, dict[str, object]]:
    provinces: dict[str, dict[str, object]] = {}
    province: dict[str, object] = {}
    rows: list[tuple[dict[str, object], int, float]] = []
    loose: list[tuple[int, float, float, str]] = []
    for words in _lines(url):
        texts = [word["text"] for word in words]
        line = " ".join(texts)
        page = int(words[0]["page_num"])
        if "PROVINSI" in line and ":" in texts:
            name = " ".join(texts[texts.index(":") + 1 :]).replace("P A P U A", "PAPUA")
            province = provinces.setdefault(name, {"province": name, "candidates": {}})
            continue
        if not province:
            continue
        if line.startswith(("JUMLAH SUARA SAH", "TOTAL SUARA")):
            province["valid"] = int(texts[-1].replace(".", ""))
            province["page"] = page
            province["box"] = _box(words[-1])
            continue
        if line.startswith("JUMLAH KURSI TERBAGI"):
            province["seats"] = int(texts[-1])
            continue
        votes = next(
            (
                index
                for index in range(1, len(texts) - 1)
                if VOTES.fullmatch(texts[index]) and ROMAN.fullmatch(texts[index + 1])
            ),
            None,
        )
        if votes is not None and re.fullmatch(r"\d+\.", texts[0]):
            candidates = province["candidates"]
            assert isinstance(candidates, dict)
            row: dict[str, object] = {
                "name": " ".join(texts[1:votes]),
                "votes": int(texts[votes].replace(".", "")),
                "rank": texts[votes + 1],
                "elected": "Terpilih" in texts[votes + 2 :],
                "page": page,
                "box": _box(words[votes]),
            }
            candidates[int(texts[0][:-1])] = row
            rows.append((row, page, float(words[0]["top"])))
        elif not HEADER & set(texts) and not all(text.isdigit() for text in texts):
            for word in words:
                loose.append((page, float(word["top"]), float(word["left"]), word["text"]))
    for row, page, top in rows:
        box = row["box"]
        assert isinstance(box, list)
        parts = [
            (wtop, left, text)
            for wpage, wtop, left, text in loose
            if wpage == page and abs(wtop - top) < 13 and left < box[0] - 30 and left > 60
        ]
        if parts:
            above = " ".join(text for wtop, _, text in sorted(parts) if wtop < top)
            below = " ".join(text for wtop, _, text in sorted(parts) if wtop > top)
            row["name"] = " ".join(part for part in (above, str(row["name"]), below) if part)
    return provinces


ALIASES = {
    "JAKARTA PUSAT": "31.71",
    "JAKARTA UTARA": "31.72",
    "JAKARTA BARAT": "31.73",
    "JAKARTA SELATAN": "31.74",
    "JAKARTA TIMUR": "31.75",
    "KEPULAUAN SERIBU": "31.01",
    "MALUKU TENGGARA BARAT": "81.03",
    "PAHUWATO": "75.04",
    "KOTA PADANG SIDEMPUAN": "12.77",
    "TOBA SAMOSIR": "12.12",
}
ELECTED = re.compile(r"\s*(\d)\s+(.+?)\s{2,}(\d{1,3}(?:\.\d{3})*)\s+(\d)\s*$")
SCANNED_ELECTED = {
    "PAPUA": [962880, 425159, 391237, 228058],
    "PAPUA BARAT": [85062, 83174, 80000, 70323],
}


def _squash(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


def _regencies() -> dict[tuple[str, str], str]:
    with connect() as conn:
        found = conn.execute(
            "select c.code, n.text from area a join area_code c on c.area_id = a.id join name n on n.entity_id = a.id"
            " where a.scheme_code = 'id.kabkota' and c.valid @> %s::date",
            [DAYS[2019]],
        ).fetchall()
    return {
        (code[:2], ("KOTA" if text.startswith("Kota ") else "") + _squash(text.split(" ", 1)[1])): code
        for code, text in found
    }


def _elected_2019() -> dict[str, list[int]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, DECREES["1319"][0]).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    elected: dict[str, list[int]] = dict(SCANNED_ELECTED)
    province = None
    for line in text.splitlines():
        if (match := re.match(r"\s*PROVINSI\s*:\s*(.+?)\s*$", line)) is not None:
            province = match.group(1)
            elected[province] = []
        elif province and (match := ELECTED.match(line)) is not None:
            elected[province].append(int(match.group(3).replace(".", "")))
    return elected


def _sumut_correction(rows: list[dict[str, object]]) -> None:
    lines = subprocess.run(
        ["pdftotext", "-layout", "-f", "5", str(raw.cached(SOURCE, DECREES["1315"][0]).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    held: dict[int, set[int]] = {}
    for row in rows:
        if row["measure"] == "votes":
            held.setdefault(int(str(row["candidate"])), set()).add(int(str(row["value"])))
    printed: dict[int, list[int]] = {}
    for index, line in enumerate(lines[:-1]):
        if (match := re.match(r"\s*(\d{2})\s+[A-Z][^\d]*$", line)) is not None and int(match.group(1)) in held:
            printed.setdefault(int(match.group(1)), []).extend(
                int(number.replace(".", "")) for number in VOTES.findall(lines[index + 1])
            )
    for number, values in held.items():
        shown = printed.get(number, [])
        carried = [value for value in shown if value not in values]
        if sorted(values) != sorted(set(shown) - set(carried)) or any(carried.count(value) != 2 for value in carried):
            raise ValueError(f"North Sumatra candidate {number}: decree 1315 differs from the workbook")


def _read_page(key: str) -> dict[str, Any]:
    message = json.loads(raw.cached(SOURCE, key).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def _figure(text: str) -> int | None:
    text = text.replace(".", "").replace(" ", "")
    return int(text) if text.isdigit() else None


def _scanned(province: str) -> tuple[str, list[dict[str, object]], dict[int, str]]:
    pages = sorted(key for key in raw.keys(SOURCE) if key.startswith(f"{province}/p-"))
    values: dict[tuple[str, str, int | None], int] = {}
    names: dict[int, str] = {}
    section = ""
    errors: list[str] = []
    for key in pages:
        page = _read_page(key)
        columns = [str(column) for column in page["columns"]]
        label = ""
        for row in page["rows"]:
            label = str(row["label"]).strip() or label
            cells = row["cells"]
            if not any(str(cell).strip() for cell in cells):
                if "DATA PEMILIH" in label:
                    section = "registered"
                elif "PENGGUNA HAK PILIH" in label:
                    section = "voters"
                continue
            if len(cells) != len(columns):
                errors.append(f"{key} row {row['number']}: {len(cells)} cells for {len(columns)} columns")
                continue
            number = _number(str(row["number"]).rstrip("."))
            text = re.sub(r"^[abc]\.\s*", "", label.lower())
            sex = str(row["sex"]).strip()
            if number is not None and not text.startswith("jumlah") and sex == "":
                names[number] = re.sub(r"\s+", " ", str(row["label"])).strip()
                measure: tuple[str, int | None] | None = ("votes", number)
            elif text.startswith("jumlah pemilih (a.1+a.2+a.3)") and sex == "JML":
                measure = (section, None)
            elif text.startswith("jumlah seluruh suara sah dan"):
                measure = ("ballots", None)
            elif text.startswith("jumlah seluruh suara sah"):
                measure = ("valid", None)
            elif text.startswith("jumlah suara tidak sah"):
                measure = ("invalid", None)
            else:
                continue
            for column, cell in zip(columns, cells, strict=True):
                if column == "JUMLAH PINDAHAN":
                    continue
                value = _figure(cell)
                if value is None:
                    errors.append(f"{key} {measure} {column}: {cell!r}")
                    continue
                if (column, *measure) in values:
                    errors.append(f"{key} {measure} {column}: read twice")
                values[(column, *measure)] = value
    if errors:
        raise ValueError(f"{province}: " + "; ".join(errors[:12]))
    return province, _checked(province, values, names), names


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    regencies = _regencies()
    elected = _elected_2019()
    results: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    certificates = []
    for dataset in CERTIFICATES:
        for _, url in files(dataset):
            province, rows, names = _certificate(raw.cached(SOURCE, url).path)
            if province == "SUMATERA UTARA":
                _sumut_correction(rows)
            certificates.append((url, province, rows, names))
    for province, url in SCANS.items():
        certificates.append((url, *_scanned(province)))
    created_later = {"PAPUA BARAT DAYA", "PAPUA PEGUNUNGAN", "PAPUA SELATAN", "PAPUA TENGAH"}
    missing = set(PROVINCES) - created_later - {province for _, province, _, _ in certificates}
    if missing:
        raise ValueError(f"2019: no certificate for {sorted(missing)}")
    for url, province, rows, names in certificates:
        code = PROVINCES[province]
        totals = {
            int(str(row["candidate"])): int(str(row["value"]))
            for row in rows
            if row["unit"] == FINAL and row["measure"] == "votes"
        }
        top = sorted(totals, key=lambda number: -totals[number])
        if [totals[number] for number in top[:4]] != elected[province] or totals[top[3]] == totals[top[4]]:
            raise ValueError(f"2019 {province}: decree 1319 names {elected[province]}, the certificate {top[:5]}")
        for row in rows:
            if row["unit"] == FINAL:
                continue
            label = str(row["unit"])
            key = ("KOTA" + _squash(label[5:])) if label.startswith("KOTA ") else _squash(label)
            regency = ALIASES.get(label) or regencies.get((code, key))
            if regency is None or regency[:2] != code:
                raise ValueError(f"2019 {province}: no regency for {label}")
            results.append({**row, "unit": regency, "url": url, "page": None, "box": None})
        candidates += [
            {
                "year": 2019,
                "province": province,
                "number": number,
                "name": name,
                "votes": totals[number],
                "elected": number in top[:4],
            }
            for number, name in sorted(names.items())
        ]
    url = DECREES["1207"][0]
    for province, found in _ranked(url).items():
        listed = found["candidates"]
        assert isinstance(listed, dict)
        votes = {number: int(str(row["votes"])) for number, row in listed.items()}
        if sum(votes.values()) != found["valid"] or found["seats"] != 4:
            raise ValueError(f"2024 {province}: candidates do not sum to the valid votes")
        top = sorted(votes, key=lambda number: -votes[number])
        if sorted(top[:4]) != sorted(number for number, row in listed.items() if row["elected"]):
            raise ValueError(f"2024 {province}: the elected are not the top four")
        for number, row in sorted(listed.items()):
            candidates.append(
                {
                    "year": 2024,
                    "province": province,
                    "number": number,
                    "name": row["name"],
                    "votes": row["votes"],
                    "elected": row["elected"],
                }
            )
            results.append(
                {
                    "year": 2024,
                    "province": province,
                    "unit": PROVINCES[province],
                    "measure": "votes",
                    "candidate": number,
                    "value": row["votes"],
                    "url": url,
                    "page": row["page"],
                    "box": row["box"],
                }
            )
        results.append(
            {
                "year": 2024,
                "province": province,
                "unit": PROVINCES[province],
                "measure": "valid",
                "candidate": None,
                "value": found["valid"],
                "url": url,
                "page": found["page"],
                "box": found["box"],
            }
        )
    original = _ranked(DECREES["360"][0])
    changed = []
    for province, found in original.items():
        listed = found["candidates"]
        assert isinstance(listed, dict)
        declared = {
            row["number"]: row["votes"] for row in candidates if row["year"] == 2024 and row["province"] == province
        }
        if {number: row["votes"] for number, row in listed.items()} != declared:
            changed.append(province)
    if changed != ["SUMATERA BARAT"] or set(PROVINCES) - set(original) != {"PAPUA BARAT DAYA"}:
        raise ValueError(f"2024: decree 1207 and lampiran IV of decree 360 differ in {changed}")
    pl.DataFrame(results, infer_schema_length=None).write_parquet(PARSED / "results.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "candidates.parquet")
    print(f"{len(results)} results rows, {len(candidates)} candidates")


SYSTEM = (
    "Each province elects four members. Voters mark one candidate, and the four candidates with the most valid votes "
    "in the province are elected (UU 7/2017 art. 421 and 422)."
)
NOTES = {
    2019: (
        "Candidate votes, registered voters, voters, and valid and invalid votes by regency and city, from KPU's "
        "open-data workbooks of the provincial recapitulation certificates (Model DD1-DPD). In every province, each "
        "regency's candidate votes sum to its valid votes, its valid and invalid votes sum to its ballots, and every "
        "row sums to the certificate's provincial total. The four candidates with the most votes match the elected "
        "members and their votes in KPU decree 1319/2019. North Sumatra's workbook equals the corrected certificate in "
        "lampiran III.2 of KPU decree 1315/2019 in every regency. Yogyakarta and East Java have no workbook; their scanned "
        "certificates (lampiran III of KPU decree 987/2019) were read by a vision model and pass the same sums."
    ),
    2024: (
        "Candidate votes and valid votes by province, from the ranking of candidates in KPU decree 1207/2024, which "
        "also declares the four elected members of each province. Each province's candidate votes sum to its valid "
        "votes. They equal lampiran IV of KPU decree 360/2024 in every province but West Sumatra, which lampiran IV "
        "lacks for Southwest Papua. No figures below the province are published in these decrees."
    ),
}
WEST_SUMATRA = (
    "The Constitutional Court (decision 03-03/PHPU.DPD-XXII/2024) annulled the West Sumatra count and restored Irman "
    "Gusman to the ballot. KPU held a re-vote across the province on 13 July 2024 (KPU decree 768/2024). These are the "
    "re-vote figures that decree 1207/2024 declares; the February count of 15 candidates in lampiran IV of decree "
    "360/2024 is not loaded."
)


def cs_label(province: str) -> str:
    return province.title().replace("Dki", "DKI")


MEASURE_IDS = {"votes": 1, "registered": 2, "ballots": 3, "valid": 4, "invalid": 5, "voters": 14}


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        spans = dict(
            conn.execute(
                "select c.code, c.valid from area a join area_code c on c.area_id = a.id where a.scheme_code = 'id.kabkota'"
            ).fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Indonesia's Regional Representative Council (DPD) elections of 2019 and 2024: candidates, votes, and "
                "the four elected members per province, 2019 by regency and city, 2024 by province"
            ),
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        indonesia = cs.polity("ID")
        body = cs.key("id.body", "dpd")
        system = cs.key("id.electoral_system", "dpd:sntv-4")
        cs.add("electoral_system", id=system, polity_id=indonesia, rules={"formula": "sntv"}, rules_version=1)
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}

        def source(url: str, title: str) -> UUID:
            if url not in sources:
                sources[url] = cs.source(KPU, title)
                official = url.startswith(JDIH)
                cs.add(
                    "source",
                    id=sources[url],
                    kind="official_body",
                    publisher=KPU,
                    title=title,
                    url=url,
                    license="Public record: official decree, no license stated"
                    if official
                    else "Open data, no license stated",
                    license_url="https://jdih.kpu.go.id" if official else OPEN_DATA,
                )
                artifacts[url] = cs.artifact(raw.cached(SOURCE, url), sources[url])
            return sources[url]

        for url, title in DECREES.values():
            source(url, title)
        workbooks = {
            province: url
            for province, url in results.filter(pl.col("year") == 2019).select("province", "url").unique().rows()
        }

        pages: dict[tuple[str, int], tuple[UUID, int, int]] = {}

        def page(url: str, number: int) -> tuple[UUID, int, int]:
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
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                found = cs.page(artifacts[url], number)
                cs.add(
                    "source_page",
                    id=found,
                    artifact_id=artifacts[url],
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
                pages[url, number] = (found, width, height)
            return pages[url, number]

        units: dict[str, UUID] = {}

        def unit(code: str) -> UUID:
            if code not in units:
                scheme = "id.kabkota" if "." in code else "id.provinsi"
                found = units[code] = cs.unit(scheme, code)
                if found in cs.minted:
                    span = spans.get(code)
                    cs.add(
                        "reporting_unit",
                        id=found,
                        polity_id=indonesia,
                        scheme_code=scheme,
                        code=code,
                        area_relation="equals",
                        area_id=cs.area(scheme, code, DAYS[2019] if scheme == "id.kabkota" else None),
                        kind="standard",
                        valid_from=span.lower if span else None,
                        valid_to=span.upper if span else None,
                    )
            return units[code]

        for year in (2024, 2019):
            day = DAYS[year]
            election = cs.key("id.election", str(day))
            rows = results.filter(pl.col("year") == year)
            for province in sorted(set(candidates.filter(pl.col("year") == year)["province"])):
                code = PROVINCES[province]
                key = f"{day}:dpd:{code}"
                contest = cs.key("id.contest", key)
                district = cs.area("id.provinsi", code)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=district,
                    term="full",
                    magnitude=4,
                    round_number=1,
                    previous_round_id=None,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                label = cs_label(province)
                name(contest, f"{year} Regional Representative Council election in {label}", "en", "english")
                name(contest, f"Pemilihan Umum Anggota DPD Tahun {year} Provinsi {label}")
                cs.add("slug", path=f"id/{day}-general/dpd-{slug(label)}", entity_id=contest)
                origin = workbooks[province] if year == 2019 else DECREES["1207"][0]
                if year == 2019:
                    source(
                        origin,
                        f"Rekapitulasi Hasil Penghitungan Perolehan Suara Calon Anggota DPD Pemilu 2019 (Model DD1-DPD), Provinsi {label}",
                    )
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sources[origin],
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTES[year])
                if province == "SUMATERA BARAT" and year == 2024:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=WEST_SUMATRA)
                options: dict[int, UUID] = {}
                for number, ballot, elected in (
                    candidates.filter((pl.col("year") == year) & (pl.col("province") == province))
                    .select("number", "name", "elected")
                    .rows()
                ):
                    option = options[number] = cs.key("id.option", f"{key}:{number}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=None,
                        ballot_position=number,
                        ballot_label=ballot,
                    )
                    person = cs.key(f"{SOURCE}.person", f"{key}:{number}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, ballot)
                    cs.add(
                        "candidacy",
                        id=cs.key("id.candidacy", f"{key}:{number}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=True,
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if elected else "lost",
                        seats=1 if elected else None,
                    )
                for label_, measure, number, value, url, number_, box in (
                    rows.filter(pl.col("province") == province)
                    .select("unit", "measure", "candidate", "value", "url", "page", "box")
                    .rows()
                ):
                    fact = {
                        "result_set_id": result_set,
                        "unit_id": unit(label_),
                        "option_id": None if number is None else options[number],
                        "channel": 0,
                        "measure": MEASURE_IDS[measure],
                        "count_round": 0,
                    }
                    cs.add("result_value", **fact, flags=0, value=value)
                    if box is None:
                        continue
                    found, width, height = page(url, number_)
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
        return cs.write()
