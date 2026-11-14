import csv
import io
import itertools
import re
import struct
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "id.kpu_pilpres"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
KPU = "Komisi Pemilihan Umum Republik Indonesia"
JDIH = "https://jdih.kpu.go.id/data/data_kepkpu/"
RESULTS = {
    2014: (
        f"{JDIH}SK_KPU_535_2272014.pdf",
        (
            "Keputusan KPU Nomor 535/Kpts/KPU/Tahun 2014 tentang Penetapan Rekapitulasi Hasil Penghitungan Perolehan "
            "Suara dan Hasil Pemilihan Umum Presiden dan Wakil Presiden Tahun 2014"
        ),
    ),
    2019: (
        f"{JDIH}KPT%20987%20THN%202019.pdf",
        (
            "Keputusan KPU Nomor 987/PL.01.8-Kpt/06/KPU/V/2019 tentang Penetapan Hasil Pemilihan Umum Presiden dan Wakil "
            "Presiden, Anggota DPR, DPD, DPRD Provinsi, dan DPRD Kabupaten/Kota Secara Nasional dalam Pemilihan Umum "
            "Tahun 2019"
        ),
    ),
    2024: (
        f"{JDIH}2024kpt360.pdf",
        (
            "Keputusan KPU Nomor 360 Tahun 2024 tentang Penetapan Hasil Pemilihan Umum Presiden dan Wakil Presiden, "
            "Anggota DPR, DPD, DPRD Provinsi, dan DPRD Kabupaten/Kota Secara Nasional dalam Pemilihan Umum Tahun 2024"
        ),
    ),
}
WINNERS = {
    2014: (
        f"{JDIH}SK_KPU_536_2272014.pdf",
        (
            "Keputusan KPU Nomor 536/Kpts/KPU/Tahun 2014 tentang Penetapan Pasangan Calon Presiden dan Wakil Presiden "
            "Terpilih dalam Pemilihan Umum Presiden dan Wakil Presiden Tahun 2014"
        ),
        2,
        "53,15",
    ),
    2019: (
        f"{JDIH}KPT%201185%20THN%202019.pdf",
        (
            "Keputusan KPU Nomor 1185/PL.01.9-Kpt/06/KPU/VI/2019 tentang Penetapan Pasangan Calon Presiden dan Wakil "
            "Presiden Terpilih dalam Pemilihan Umum Tahun 2019"
        ),
        1,
        "55,50",
    ),
    2024: (
        f"{JDIH}2024kpt504.pdf",
        (
            "Keputusan KPU Nomor 504 Tahun 2024 tentang Penetapan Pasangan Calon Presiden dan Wakil Presiden Terpilih "
            "dalam Pemilihan Umum Tahun 2024"
        ),
        2,
        "58,59",
    ),
}
CERTIFICATE = (
    f"{JDIH}987_L.I.pdf",
    (
        "Lampiran I Keputusan KPU Nomor 987/PL.01.8-Kpt/06/KPU/V/2019: Sertifikat Rekapitulasi Hasil Penghitungan "
        "Perolehan Suara Pemilihan Umum Presiden dan Wakil Presiden Tingkat Nasional (Model DC1-PPWP)"
    ),
)
OPEN_DATA = (
    "https://opendata.kpu.go.id/uploads/file_dataset/1736221947_9d3a3587d8904792a9b3.xlsx",
    "Hasil Pemilu Presiden dan Wakil Presiden Tahun 2019 per Provinsi (Open Data KPU)",
)
TICKETS = {
    2004: (
        (1, "H. Wiranto, S.H.", "Ir. H. Salahuddin Wahid"),
        (2, "Hj. Megawati Soekarnoputri", "K.H. Hasyim Muzadi"),
        (3, "Prof. Dr. H. Amin Rais", "Dr. Ir. Siswono Yudo Husodo"),
        (4, "H. Susilo Bambang Yudhoyono", "Drs. H. Muhammad Yusuf Kalla"),
        (5, "Dr. H. Hamzah Haz", "H. Agum Gumelar"),
    ),
    2014: (
        (1, "H. Prabowo Subianto", "Ir. H.M. Hatta Rajasa"),
        (2, "Ir. H. Joko Widodo", "Drs. H.M. Jusuf Kalla"),
    ),
    2019: (
        (1, "Ir. H. Joko Widodo", "Prof. Dr. (H.C) KH. Ma'ruf Amin"),
        (2, "H. Prabowo Subianto", "H. Sandiaga Salahuddin Uno"),
    ),
    2024: (
        (1, "H. Anies Rasyid Baswedan, Ph.D.", "Dr. (H.C.) H. A. Muhaimin Iskandar"),
        (2, "H. Prabowo Subianto", "Gibran Rakabuming Raka"),
        (3, "H. Ganjar Pranowo, S.H., M.I.P.", "Prof. Dr. H. M. Mahfud MD"),
    ),
}
PERSONS = {
    "H. Wiranto, S.H.": ("Q731740", "Wiranto"),
    "Ir. H. Salahuddin Wahid": ("Q12511575", "Salahuddin Wahid"),
    "Hj. Megawati Soekarnoputri": ("Q76179", "Megawati Soekarnoputri"),
    "K.H. Hasyim Muzadi": ("Q5680756", "Hasyim Muzadi"),
    "Prof. Dr. H. Amin Rais": ("Q2202270", "Amien Rais"),
    "Dr. Ir. Siswono Yudo Husodo": ("Q13097173", "Siswono Yudo Husodo"),
    "H. Susilo Bambang Yudhoyono": ("Q57405", "Susilo Bambang Yudhoyono"),
    "Drs. H. Muhammad Yusuf Kalla": ("Q76354", "Jusuf Kalla"),
    "Dr. H. Hamzah Haz": ("Q76344", "Hamzah Haz"),
    "H. Agum Gumelar": ("Q4694586", "Agum Gumelar"),
    "H. Prabowo Subianto": ("Q2107268", "Prabowo Subianto"),
    "Ir. H.M. Hatta Rajasa": ("Q4201732", "Hatta Rajasa"),
    "Ir. H. Joko Widodo": ("Q3318231", "Joko Widodo"),
    "Drs. H.M. Jusuf Kalla": ("Q76354", "Jusuf Kalla"),
    "Prof. Dr. (H.C) KH. Ma'ruf Amin": ("Q12497177", "Ma'ruf Amin"),
    "H. Sandiaga Salahuddin Uno": ("Q7416335", "Sandiaga Uno"),
    "H. Anies Rasyid Baswedan, Ph.D.": ("Q4263230", "Anies Baswedan"),
    "Dr. (H.C.) H. A. Muhaimin Iskandar": ("Q12499327", "Muhaimin Iskandar"),
    "Gibran Rakabuming Raka": ("Q18342893", "Gibran Rakabuming Raka"),
    "H. Ganjar Pranowo, S.H., M.I.P.": ("Q12483474", "Ganjar Pranowo"),
    "Prof. Dr. H. M. Mahfud MD": ("Q6892160", "Mahfud MD"),
}
DAYS = {2004: date(2004, 7, 5), 2014: date(2014, 7, 9), 2019: date(2019, 4, 17), 2024: date(2024, 2, 14)}
PROVINCES = {
    "ACEH": "11",
    "SUMATERA UTARA": "12",
    "SUMATERA BARAT": "13",
    "RIAU": "14",
    "JAMBI": "15",
    "SUMATERA SELATAN": "16",
    "BENGKULU": "17",
    "LAMPUNG": "18",
    "KEPULAUAN BANGKA BELITUNG": "19",
    "KEPULAUAN RIAU": "21",
    "DKI JAKARTA": "31",
    "JAWA BARAT": "32",
    "JAWA TENGAH": "33",
    "DAERAH ISTIMEWA YOGYAKARTA": "34",
    "JAWA TIMUR": "35",
    "BANTEN": "36",
    "BALI": "51",
    "NUSA TENGGARA BARAT": "52",
    "NUSA TENGGARA TIMUR": "53",
    "KALIMANTAN BARAT": "61",
    "KALIMANTAN TENGAH": "62",
    "KALIMANTAN SELATAN": "63",
    "KALIMANTAN TIMUR": "64",
    "KALIMANTAN UTARA": "65",
    "SULAWESI UTARA": "71",
    "SULAWESI TENGAH": "72",
    "SULAWESI SELATAN": "73",
    "SULAWESI TENGGARA": "74",
    "GORONTALO": "75",
    "SULAWESI BARAT": "76",
    "MALUKU": "81",
    "MALUKU UTARA": "82",
    "PAPUA": "91",
    "PAPUA BARAT": "92",
    "PAPUA SELATAN": "93",
    "PAPUA TENGAH": "94",
    "PAPUA PEGUNUNGAN": "95",
    "PAPUA BARAT DAYA": "96",
}
SPANNING = {
    "KALIMANTAN TIMUR": (2019, ("64", "65"), "East Kalimantan, including North Kalimantan"),
    "PAPUA": (2024, ("91", "93", "94", "95"), "Papua, including Central, Highland, and South Papua"),
    "PAPUA BARAT": (2024, ("92", "96"), "West Papua, including Southwest Papua"),
}
ABROAD = "LUAR NEGERI"
TOTAL = "NASIONAL"
LAMPIRAN_2024 = (
    f"{JDIH}2024kpt360_L1.pdf",
    (
        "Lampiran I Keputusan KPU Nomor 360 Tahun 2024: Perolehan Suara Sah Calon Presiden dan Wakil Presiden "
        "Peserta Pemilihan Umum Tahun 2024"
    ),
)
COURT = "Mahkamah Konstitusi Republik Indonesia"
RULING_2014 = (
    (
        "https://s.mkri.id/public/content/persidangan/putusan/"
        "putusan_sidang_2045_20140909061631_PUTUSAN_PILPRES_2014__FINAL__wmGABUNG.pdf"
    ),
    (
        "Putusan Mahkamah Konstitusi Nomor 1/PHPU.PRES-XII/2014 tentang Perselisihan Hasil Pemilihan Umum Presiden "
        "dan Wakil Presiden Tahun 2014"
    ),
)
RULING_2014_PAGES = (4580, 4581)
RULING_2004 = (
    "https://s.mkri.id/public/content/persidangan/putusan/062_PHPU-B-II_2004.pdf",
    (
        "Putusan Mahkamah Konstitusi Nomor 062/PHPU-B-II/2004 tentang Perselisihan Hasil Pemilihan Umum Presiden "
        "dan Wakil Presiden Tahun 2004"
    ),
)
RULING_2004_PAGE = 4
RULING_2004_FINDING = 52
ADVANCED_2004 = {2, 4}
ORDER_2014 = (*(label for label in PROVINCES if PROVINCES[label] not in {"65", "93", "94", "95", "96"}), ABROAD)
SHEETS = {
    1: ((11, "registered"), (23, "voters")),
    3: ((0, "1"), (1, "2"), (2, "valid"), (3, "invalid"), (4, "ballots")),
}
FIXES = {(7, 0, 0): 404188, (7, 3, 4): 48470}
NUMBER = re.compile(r"sebanyak\s+(\d{1,3}(?:\.\d{3})+)")


def fetch() -> None:
    for url, *_ in (*RESULTS.values(), *WINNERS.values(), CERTIFICATE, LAMPIRAN_2024):
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, OPEN_DATA[0], suffix=".xlsx")
    raw.fetch(SOURCE, RULING_2014[0], suffix=".pdf")
    raw.fetch(SOURCE, RULING_2004[0], suffix=".pdf")


def _pages(pdf: Path, dpi: int, folder: Path) -> list[Path]:
    subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", str(pdf), str(folder / "p")], check=True)
    return sorted(folder.glob("p-*.png"), key=lambda page: int(page.stem.split("-")[1]))


def _text(url: str) -> str:
    with tempfile.TemporaryDirectory() as folder:
        return "\n".join(
            subprocess.run(["tesseract", str(page), "-"], check=True, capture_output=True, text=True).stdout
            for page in _pages(raw.cached(SOURCE, url).path, 250, Path(folder))
        )


def _tokens(page: Path) -> list[tuple[float, int, int, tuple[int, int, int, int]]]:
    out = subprocess.run(
        ["tesseract", str(page), "-", "--psm", "11", "tsv"], check=True, capture_output=True, text=True
    ).stdout
    tokens = []
    for row in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
        digits = re.sub(r"[^0-9.,]", "", row["text"] or "")
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})*", digits):
            x, y, w, h = (int(row[key]) for key in ("left", "top", "width", "height"))
            tokens.append((y + h / 2, x + w, int(re.sub(r"[.,]", "", digits)), (x, y, x + w, y + h)))
    return sorted(tokens)


def _grid(page: Path, columns: int) -> list[list[tuple[int, tuple[int, int, int, int]] | None]]:
    lines: list[list[tuple[float, int, int, tuple[int, int, int, int]]]] = []
    for token in _tokens(page):
        if lines and token[0] - lines[-1][-1][0] <= 25:
            lines[-1].append(token)
        else:
            lines.append([token])
    lines = [sorted(line, key=lambda token: token[1]) for line in lines]
    full = [line for line in lines if len(line) == columns]
    edges = [sorted(line[index][1] for line in full)[len(full) // 2] for index in range(columns)]
    width = min(b - a for a, b in itertools.pairwise(edges))
    grid = []
    for line in lines:
        cells: list[tuple[int, tuple[int, int, int, int]] | None] = [None] * columns
        for _, right, value, box in line:
            index = min(range(columns), key=lambda index: abs(edges[index] - right))
            if abs(edges[index] - right) < width / 3 and (cells[index] is None or value >= 10):
                cells[index] = (value, box)
        if sum(cell is not None for cell in cells) >= columns - 2:
            grid.append(cells)
    return grid


def _certificate() -> list[dict[str, object]]:
    provinces = pl.read_excel(raw.cached(SOURCE, OPEN_DATA[0]).path)["NAMA_PROV"].to_list()[:34]
    layout = (provinces[:15], [None, *provinces[15:29]], [None, *provinces[29:34], ABROAD])
    rows = []
    with tempfile.TemporaryDirectory() as folder:
        for number, page in enumerate(_pages(raw.cached(SOURCE, CERTIFICATE[0]).path, 400, Path(folder)), 1):
            sheet, part = divmod(number - 1, 3)
            if (wanted := SHEETS.get(sheet + 1)) is None:
                continue
            units = [*layout[part], TOTAL if part == 2 else None]
            grid = _grid(page, len(units))
            for line, measure in wanted:
                for column, unit in enumerate(units):
                    if unit is None:
                        continue
                    fixed = FIXES.get((number, line, column))
                    cell = grid[line][column]
                    if fixed is None and cell is None:
                        raise ValueError(f"page {number} line {line} column {column} ({unit}) unread")
                    rows.append(
                        {
                            "year": 2019,
                            "unit": unit,
                            "measure": "votes" if measure.isdigit() else measure,
                            "ticket": int(measure) if measure.isdigit() else None,
                            "value": fixed if fixed is not None else cell[0] if cell else None,
                            "url": CERTIFICATE[0],
                            "page": number,
                            "box": None if cell is None else [value * 72 / 400 for value in cell[1]],
                            "fixed": fixed is not None,
                        }
                    )
    return rows


def _check_2019(rows: list[dict[str, object]]) -> None:
    value: dict[tuple[object, object, object], int] = {
        (row["unit"], row["measure"], row["ticket"]): int(str(row["value"])) for row in rows
    }
    sheet = pl.read_excel(raw.cached(SOURCE, OPEN_DATA[0]).path)
    errors = []
    for unit, first, second in sheet.iter_rows():
        unit = ABROAD if unit == "+Luar Negeri" else unit
        for ticket, votes in ((1, first), (2, second)):
            if value[(unit, "votes", ticket)] != votes:
                errors.append(f"{unit} {ticket}: certificate {value[(unit, 'votes', ticket)]}, open data {votes}")
    units = {row["unit"] for row in rows}
    for unit in units:
        valid, invalid, ballots = (value[(unit, measure, None)] for measure in ("valid", "invalid", "ballots"))
        if value[(unit, "votes", 1)] + value[(unit, "votes", 2)] != valid or valid + invalid != ballots:
            errors.append(f"{unit}: votes, valid {valid}, invalid {invalid}, ballots {ballots} disagree")
    for measure, ticket in ((m, None) for m in ("registered", "voters", "valid", "invalid", "ballots")):
        total = sum(value[(unit, measure, ticket)] for unit in units - {TOTAL})
        if total != value[(TOTAL, measure, ticket)]:
            errors.append(f"{measure}: units sum to {total}, national {value[(TOTAL, measure, ticket)]}")
    if errors:
        raise ValueError("\n".join(errors))


def _national(year: int) -> list[dict[str, object]]:
    url = RESULTS[year][0]
    text = _text(url)
    found = [int(number.replace(".", "")) for number in NUMBER.findall(text)]
    tickets = TICKETS[year]
    votes = found[: len(tickets)]
    winner_url, _, winner, share = WINNERS[year]
    winner_text = _text(winner_url)
    if f"{votes[winner - 1]:,}".replace(",", ".") not in winner_text or share not in winner_text:
        raise ValueError(f"{year}: winner decree does not state {votes[winner - 1]} votes and {share} percent")
    if f"{100 * votes[winner - 1] / sum(votes):.2f}".replace(".", ",") != share:
        raise ValueError(f"{year}: {votes} do not give the winner {share} percent")
    return [
        {
            "year": year,
            "unit": TOTAL,
            "measure": "votes",
            "ticket": number,
            "value": value,
            "url": url,
            "page": None,
            "box": None,
            "fixed": False,
        }
        for (number, *_), value in zip(tickets, votes, strict=True)
    ]


def _lampiran_2024() -> list[dict[str, object]]:
    out = subprocess.run(
        ["pdftotext", "-tsv", str(raw.cached(SOURCE, LAMPIRAN_2024[0]).path), "-"],
        check=True,
        capture_output=True,
        text=True,
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
    tickets = [number for number, *_ in TICKETS[2024]]
    rows: list[dict[str, object]] = []
    for words in lines:
        words.sort(key=lambda word: float(word["left"]))
        page = int(words[0]["page_num"])
        numbers = [word for word in words[1:] if re.fullmatch(r"\d{1,3}(?:\.\d{3})*", word["text"])]
        labels = {name.replace(" ", ""): name for name in (*PROVINCES, ABROAD, "TOTAL")}
        label = labels.get("".join(word["text"] for word in words if not re.fullmatch(r"[\d.]+", word["text"])))
        if len(numbers) != len(tickets) + 1 or label is None:
            continue
        for ticket, word in zip((*tickets, None), numbers, strict=True):
            left, top, width, height = (float(word[key]) for key in ("left", "top", "width", "height"))
            rows.append(
                {
                    "year": 2024,
                    "unit": TOTAL if label == "TOTAL" else label,
                    "measure": "votes" if ticket else "valid",
                    "ticket": ticket,
                    "value": int(word["text"].replace(".", "")),
                    "url": LAMPIRAN_2024[0],
                    "page": page,
                    "box": [left, top, left + width, top + height],
                    "fixed": False,
                }
            )
    value = {(row["unit"], row["ticket"]): int(str(row["value"])) for row in rows}
    units = {str(row["unit"]) for row in rows}
    if units != {*PROVINCES, ABROAD, TOTAL} or len(rows) != len(units) * (len(tickets) + 1):
        raise ValueError(f"2024 lampiran I: {len(rows)} cells, missing {sorted({*PROVINCES, ABROAD, TOTAL} - units)}")
    for unit in units:
        if sum(value[(unit, ticket)] for ticket in tickets) != value[(unit, None)]:
            raise ValueError(f"2024 {unit}: ticket votes do not sum to the total")
    for ticket in (*tickets, None):
        if sum(value[(unit, ticket)] for unit in units - {TOTAL}) != value[(TOTAL, ticket)]:
            raise ValueError(f"2024 ticket {ticket}: units do not sum to the national total")
    return rows


def _ruling_2004() -> list[dict[str, object]]:
    path = str(raw.cached(SOURCE, RULING_2004[0]).path)
    page = str(RULING_2004_PAGE)
    out = subprocess.run(
        ["pdftotext", "-f", page, "-l", page, "-tsv", path, "-"], check=True, capture_output=True, text=True
    ).stdout
    words = sorted(
        (
            word
            for word in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
            if word["level"] == "5" and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", word["text"])
        ),
        key=lambda word: float(word["top"]),
    )
    tickets = [number for number, *_ in TICKETS[2004]]
    if len(words) != len(tickets) + 1:
        raise ValueError(f"2004 ruling page {page}: {len(words)} figures, expected {len(tickets) + 1}")
    rows: list[dict[str, object]] = []
    for ticket, word in zip((*tickets, None), words, strict=True):
        left, top, width, height = (float(word[key]) for key in ("left", "top", "width", "height"))
        rows.append(
            {
                "year": 2004,
                "unit": TOTAL,
                "measure": "votes" if ticket else "valid",
                "ticket": ticket,
                "value": int(word["text"].replace(".", "")),
                "url": RULING_2004[0],
                "page": RULING_2004_PAGE,
                "box": [left, top, left + width, top + height],
                "fixed": False,
            }
        )
    votes = {row["ticket"]: int(str(row["value"])) for row in rows}
    if sum(votes[ticket] for ticket in tickets) != votes[None]:
        raise ValueError("2004: ticket votes do not sum to the valid votes")
    if set(sorted(tickets, key=lambda ticket: -votes[ticket])[:2]) != ADVANCED_2004:
        raise ValueError("2004: the two leading tickets are not the ones sent to the second round")
    if votes[4] * 2 > votes[None]:
        raise ValueError("2004: a ticket passed 50 percent in the first round")
    finding = subprocess.run(
        ["pdftotext", "-f", str(RULING_2004_FINDING), "-l", str(RULING_2004_FINDING), path, "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for ticket in (1, 2):
        if f"{votes[ticket]:,}".replace(",", ".") not in finding:
            raise ValueError(f"2004: the court's finding does not state ticket {ticket}'s {votes[ticket]} votes")
    return rows


def _ruling_2014() -> list[dict[str, object]]:
    first, last = RULING_2014_PAGES
    out = subprocess.run(
        ["pdftotext", "-f", str(first), "-l", str(last), "-tsv", str(raw.cached(SOURCE, RULING_2014[0]).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    words = [
        word
        for word in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
        if word["level"] == "5" and re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", word["text"])
    ]
    lines: list[list[dict[str, str]]] = []
    for word in sorted(words, key=lambda word: (int(word["page_num"]), float(word["top"]))):
        first_word = lines[-1][0] if lines else None
        if (
            first_word
            and first_word["page_num"] == word["page_num"]
            and abs(float(word["top"]) - float(first_word["top"])) < 4
        ):
            lines[-1].append(word)
        else:
            lines.append([word])
    lines = [sorted(line, key=lambda word: float(word["left"])) for line in lines if len(line) == 4]
    if len(lines) != len(ORDER_2014) + 1:
        raise ValueError(f"2014 ruling: {len(lines)} table rows, expected {len(ORDER_2014) + 1}")
    rows: list[dict[str, object]] = []
    for label, line in zip((*ORDER_2014, TOTAL), lines, strict=True):
        for (measure, ticket), word in zip((("votes", 1), ("votes", 2), ("valid", None), ("invalid", None)), line):
            left, top, width, height = (float(word[key]) for key in ("left", "top", "width", "height"))
            rows.append(
                {
                    "year": 2014,
                    "unit": label,
                    "measure": measure,
                    "ticket": ticket,
                    "value": int(re.sub(r"[.,]", "", word["text"])),
                    "url": RULING_2014[0],
                    "page": int(word["page_num"]),
                    "box": [left, top, left + width, top + height],
                    "fixed": False,
                }
            )
    value = {(row["unit"], row["measure"], row["ticket"]): int(str(row["value"])) for row in rows}
    for label in (*ORDER_2014, TOTAL):
        if value[label, "votes", 1] + value[label, "votes", 2] != value[label, "valid", None]:
            raise ValueError(f"2014 {label}: ticket votes do not sum to the valid votes")
    for key in (("votes", 1), ("votes", 2), ("valid", None), ("invalid", None)):
        if sum(value[label, *key] for label in ORDER_2014) != value[TOTAL, *key]:
            raise ValueError(f"2014 {key}: provinces do not sum to the national row")
    return rows


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = _certificate()
    _check_2019(rows)
    declared_2014 = {row["ticket"]: row["value"] for row in _national(2014)}
    rows += _ruling_2014()
    rows += _ruling_2004()
    declared = {row["ticket"]: row["value"] for row in _national(2024)}
    rows += _lampiran_2024()
    for row in rows:
        if row["unit"] != TOTAL or not row["ticket"] or row["year"] not in (2014, 2024):
            continue
        if {2014: declared_2014, 2024: declared}[int(str(row["year"]))][row["ticket"]] != row["value"]:
            raise ValueError(f"{row['year']} ticket {row['ticket']}: the provincial table and the decree disagree")
    _, _, winner, share = WINNERS[2019]
    votes = [int(str(row["value"])) for row in rows if row["year"] == 2019 and row["unit"] == TOTAL and row["ticket"]]
    if f"{100 * votes[winner - 1] / sum(votes):.2f}".replace(".", ",") != share:
        raise ValueError(f"2019: {votes} do not give the winner {share} percent")
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / "results.parquet")
    pl.DataFrame(
        [
            {
                "year": year,
                "ticket": number,
                "president": president,
                "vice_president": vice,
                "outcome": (
                    "elected"
                    if number == winner
                    else "lost"
                    if winner
                    else "advanced"
                    if number in ADVANCED_2004
                    else "eliminated"
                ),
            }
            for year, tickets in TICKETS.items()
            for number, president, vice in tickets
            for winner in [WINNERS[year][2] if year in WINNERS else None]
        ]
    ).write_parquet(PARSED / "tickets.parquet")
    print(f"{len(rows)} results rows")


SYSTEM_TWO = (
    "Two tickets stood, so a majority of the valid votes elects. UU 7/2017 art. 416 (UU 42/2008 art. 159 in 2014) "
    "also asks for at least 20 percent in more than half of the provinces, but the Constitutional Court held in "
    "decision 50/PUU-XII/2014 that this condition does not apply when only two tickets stand."
)
RULES_SPREAD = {
    "formula": "two_round",
    "majority": 0.5,
    "advance": 2,
    "spread": {"scheme": "id.provinsi", "share": 0.2, "units": {"share": 0.5, "strict": True}},
}
SYSTEM_SPREAD = (
    "A ticket is elected with over 50 percent of the valid votes and at least 20 percent of the valid votes in more "
    "than half of the provinces (UU 23/2003 art. 66(2) in 2004, UU 7/2017 art. 416(1) from 2019); otherwise the two "
    "tickets with the most votes meet in a second round, which the most votes win. The Constitutional Court held in "
    "decision 50/PUU-XII/2014 that the provincial condition does not apply when only two tickets stand."
)
NOTES = {
    2004: (
        "National ticket votes and valid votes as declared in KPU decree 79/SK/KPU/Tahun 2004 of 26 July 2004, which "
        "Constitutional Court ruling 062/PHPU-B-II/2004 quotes (page 4 of the ruling's PDF). The court's findings "
        "(page 52) restate the first and second tickets' votes and rejected the challenge. The ticket votes sum to the "
        "valid votes. The ruling reproduces no provincial table of ticket votes, so only national figures are loaded."
    ),
    2014: (
        "Ticket votes, valid votes, and invalid votes by province and abroad from KPU's national recapitulation "
        "(Model DD1-PPWP, 22 July 2014), which KPU's legal documentation site does not attach to decree 535/2014. KPU "
        "set the table out in its answer in Constitutional Court ruling 1/PHPU.PRES-XII/2014 (pages 4580 and 4581 of "
        "the ruling's PDF), which rejected the challenge. The table prints row 23 as Kalimantan Tengah and row 26 as "
        "Sulawesi Tengah; by their place in KPU's province order they are East Kalimantan and South Sulawesi. Each row "
        "sums to its valid votes, each column to the national row, and the national row equals decree 535/2014 and "
        "the 53.15 percent share in the winner decree 536/2014. East Kalimantan includes North Kalimantan, and Papua "
        "and West Papua include the provinces split from them in 2022, so those three rows lie within the nation "
        "rather than equal one of today's provinces."
    ),
    2019: (
        "Votes, registered voters, voters, valid and invalid votes, and ballots by province and abroad, from the "
        "national recapitulation certificate (Model DC1-PPWP) in lampiran I of KPU decree 987/2019, a scan read by "
        "OCR at 400 dpi. Every ticket's votes equal KPU's open-data workbook of the 2019 result by province, each "
        "unit's ticket votes sum to its valid votes and its valid and invalid votes to its ballots, and every column "
        "sums to the certificate's national figure. Two cells the scan left unreadable (Aceh's votes for ticket 1 "
        "and Jambi's invalid votes) are taken from those sums and the workbook. The winner decree 1185/2019 states "
        "the 55.50 percent share these figures give. Papua and West Papua include the provinces split from them in "
        "2022."
    ),
    2024: (
        "Ticket votes and valid votes by province and abroad from lampiran I of KPU decree 360/2024 (a text PDF). "
        "Each row sums to its total, each column to the national row, and the national row equals the decree's "
        "figures and the 58.59 percent share in the winner decree 504/2024. The lampiran gives no registered voters, "
        "turnout, or invalid votes."
    ),
}
MEASURES = {"votes": 1, "registered": 2, "ballots": 3, "valid": 4, "invalid": 5, "voters": 14}


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
    tickets = pl.read_parquet(PARSED / "tickets.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Indonesia's direct presidential elections of 2014, 2019, and 2024 from KPU's national result decrees: "
                "by province and abroad, 2014 from KPU's recapitulation as the Constitutional Court's ruling reproduces it"
            ),
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        indonesia = cs.polity("ID")
        office = cs.key("id.office", "presiden")
        nation = cs.area("id.nation", "ID")
        two = cs.key("id.electoral_system", "presiden:two-tickets")
        cs.add("electoral_system", id=two, polity_id=indonesia, rules={"formula": "two_round"}, rules_version=1)
        cs.add("note", entity_id=two, kind="methodology", body_md=SYSTEM_TWO)
        spread = cs.key("id.electoral_system", "presiden:spread")
        cs.add("electoral_system", id=spread, polity_id=indonesia, rules=RULES_SPREAD, rules_version=1)
        cs.add("note", entity_id=spread, kind="methodology", body_md=SYSTEM_SPREAD)

        units: dict[str, UUID] = {}

        def unit(label: str, year: int) -> UUID:
            spans = label in SPANNING and year < SPANNING[label][0]
            if spans:
                scheme, code = "id.provinsi", "+".join(SPANNING[label][1])
            elif label in PROVINCES:
                scheme, code = "id.provinsi", PROVINCES[label]
            else:
                scheme, code = "id.nation", "LN" if label == ABROAD else "ID"
            if code not in units:
                found = units[code] = cs.key(f"{SOURCE}.unit", f"{scheme}:{code}")
                if found in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=found,
                        polity_id=indonesia,
                        scheme_code=scheme,
                        code=code,
                        area_relation="within" if label == ABROAD or spans else "equals",
                        area_id=cs.area(scheme, code) if label in PROVINCES and not spans else nation,
                        kind="abroad_mission" if label == ABROAD else "standard",
                        valid_from=None,
                    )
                    if label == ABROAD:
                        name(found, "Luar Negeri")
                        name(found, "Abroad", "en", "english")
                    if spans:
                        name(found, label.title())
                        name(found, SPANNING[label][2], "en", "english")
            return units[code]

        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        for url, title in (
            *((url, title) for url, title in RESULTS.values()),
            *((url, title) for url, title, *_ in WINNERS.values()),
            CERTIFICATE,
            LAMPIRAN_2024,
            RULING_2014,
            RULING_2004,
        ):
            publisher = COURT if url in {RULING_2014[0], RULING_2004[0]} else KPU
            sources[url] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=f"Public record: official {'court ruling' if publisher == COURT else 'decree'}, no license stated",
                license_url="https://www.mkri.id" if publisher == COURT else "https://jdih.kpu.go.id",
            )
            artifacts[url] = cs.artifact(raw.cached(SOURCE, url), sources[url])

        pages: dict[tuple[str, int], tuple[UUID, int, int]] = {}

        def page(url: str, number: int) -> tuple[UUID, int, int]:
            if (url, number) not in pages:
                cached = raw.cached(SOURCE, url)
                image = DATA / "pages" / SOURCE / f"{cached.sha256}-{number}.png"
                if not image.exists():
                    image.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        [
                            "pdftoppm",
                            "-r",
                            "100",
                            "-png",
                            "-f",
                            str(number),
                            "-l",
                            str(number),
                            "-singlefile",
                            str(cached.path),
                            str(image.with_suffix("")),
                        ],
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

        persons: dict[str, UUID] = {}
        for ballot, (item, common) in PERSONS.items():
            persons[ballot] = cs.key("wikidata", item)
            if persons[ballot] in cs.minted and persons[ballot] not in list(persons.values())[:-1]:
                cs.add("person", id=persons[ballot])
                name(persons[ballot], common)

        for year, day in sorted(DAYS.items(), reverse=True):
            rows = results.filter(pl.col("year") == year)
            general = year not in (2004, 2014)
            election = cs.key("id.election", str(day))
            cs.add(
                "election",
                id=election,
                polity_id=indonesia,
                kind="general",
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            if general:
                name(election, f"{year} Indonesian general election", "en", "english")
                name(election, f"Pemilihan Umum Tahun {year}")
                path = f"id/{day}-general"
            else:
                name(election, f"{year} Indonesian presidential election", "en", "english")
                name(election, f"Pemilihan Umum Presiden dan Wakil Presiden Tahun {year}")
                path = f"id/{day}-presidential"
            cs.add("slug", path=path, entity_id=election)
            contest_key = f"{day}:presiden"
            contest = cs.key("id.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=nation,
                term="full",
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=spread if year in (2004, 2024) else two,
                default_result_kind="counted",
            )
            first = ", first round" if year == 2004 else ""
            name(contest, f"{year} Indonesian presidential election{first}", "en", "english")
            name(
                contest, f"Pemilihan Umum Presiden dan Wakil Presiden Tahun {year}{' Putaran Pertama' if first else ''}"
            )
            cs.add("slug", path=f"{path}/president", entity_id=contest)
            source = {2004: RULING_2004[0], 2014: RULING_2014[0], 2019: CERTIFICATE[0], 2024: LAMPIRAN_2024[0]}[year]
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[source],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTES[year])
            options: dict[int, UUID] = {}
            for number, president, vice, outcome in (
                tickets.filter(pl.col("year") == year).select("ticket", "president", "vice_president", "outcome").rows()
            ):
                option = options[number] = cs.key("id.option", f"{contest_key}:{number}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None,
                    ballot_position=number,
                    ballot_label=f"{PERSONS[president][1]} and {PERSONS[vice][1]}",
                )
                for role, ballot in (("head", president), ("running_mate", vice)):
                    cs.add(
                        "candidacy",
                        id=cs.key("id.candidacy", f"{contest_key}:{number}:{role}"),
                        person_id=persons[ballot],
                        option_id=option,
                        role=role,
                        ballot_name=ballot,
                        is_independent=False,
                    )
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=outcome,
                    seats=None,
                )
            if rows["unit"].n_unique() > 1:
                rows = rows.filter(pl.col("unit") != TOTAL)
            for label, measure, ticket, value, url, number, box in rows.select(
                "unit", "measure", "ticket", "value", "url", "page", "box"
            ).rows():
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit(label, year),
                    "option_id": None if ticket is None else options[ticket],
                    "channel": 0,
                    "measure": MEASURES[measure],
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=0, value=value)
                if box is None:
                    cs.add("value_locator", **fact, page_id=None, source_id=sources[url])
                    continue
                found, width, height = page(url, number)
                x0, y0, x1, y1 = (value * 100 / 72 for value in box)
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
            if year == 2024:
                winner = tickets.filter((pl.col("year") == year) & (pl.col("outcome") == "elected"))["ticket"].item()
                cells = {
                    (label, ticket): value for label, ticket, value in rows.select("unit", "ticket", "value").rows()
                }
                low = [label for label in PROVINCES if cells[label, winner] < 0.2 * cells[label, None]]
                if low:
                    raise ValueError(f"2024: the winner is under 20 percent in {low}")
        for note, entity, body in conn.execute(
            """select n.id, n.entity_id, n.body_md from note n
               join contest c on c.id = n.entity_id
               join changeset w on w.id = n.changeset_id and w.pipeline = %s
               where n.kind = 'caveat'""",
            [SOURCE],
        ).fetchall():
            cs.add(
                "note_drop",
                note_id=note,
                entity_id=entity,
                body_md=body,
                reason="the contest now carries an electoral system with the provincial condition",
            )
        return cs.write()
