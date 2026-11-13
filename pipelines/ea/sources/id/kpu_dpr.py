import base64
import html
import io
import re
import subprocess
from pathlib import Path

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
    return rows


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
            results += [{"province": province, **row} for row in rows]
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
    candidates = pl.DataFrame(listed).join(lists, on=["dapil", "party", "number"], how="left")
    seats = pl.read_excel(_path(next(url for _, url in files(SEATS))), infer_schema_length=0)
    seats = seats.select(
        dapil=pl.col("nama_dapil").str.strip_chars().str.to_uppercase(),
        party=pl.col("nomor_urut").cast(pl.Int64),
        seats=pl.col("perolehan kursi").cast(pl.Int64),
    ).filter(pl.col("seats") > 0)
    if seats["seats"].sum() != 575 or seats["dapil"].n_unique() != 80:
        raise ValueError("seat table does not give 575 seats in 80 dapils")
    if sum(NATIONAL.values()) != NATIONAL_VALID:
        raise ValueError("national party votes do not sum to the valid votes")
    elected = _elected("1318") | _elected("1341")
    votes = pl.DataFrame(results).filter(pl.col("measure") == "votes")
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
    pl.DataFrame(results).write_parquet(PARSED / "results.parquet")
    candidates.write_parquet(PARSED / "candidates.parquet")
    seats.write_parquet(PARSED / "seats.parquet")
    pl.DataFrame([{"party": party, "votes": value} for party, value in NATIONAL.items()]).write_parquet(
        PARSED / "national.parquet"
    )
    pl.DataFrame(winners).write_parquet(PARSED / "elected.parquet")
    print(
        f"{len(dapils)} dapils, {len(results)} results rows, {candidates.height} candidates, "
        f"{len(winners)} elected, {candidates['sex'].null_count()} candidates without a list row"
    )
