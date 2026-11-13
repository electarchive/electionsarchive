import csv
import io
import itertools
import re
import subprocess
import tempfile
from pathlib import Path

import polars as pl

from ea import raw
from ea.store import DATA

SOURCE = "id.kpu_pilpres"
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
ABROAD = "LUAR NEGERI"
TOTAL = "NASIONAL"
SHEETS = {
    1: ((11, "registered"), (23, "voters")),
    3: ((0, "1"), (1, "2"), (2, "valid"), (3, "invalid"), (4, "ballots")),
}
FIXES = {(7, 0, 0): 404188, (7, 3, 4): 48470}
NUMBER = re.compile(r"sebanyak\s+(\d{1,3}(?:\.\d{3})+)")


def fetch() -> None:
    for url, *_ in (*RESULTS.values(), *WINNERS.values(), CERTIFICATE):
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, OPEN_DATA[0], suffix=".xlsx")


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
                            "box": None if cell is None else list(cell[1]),
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


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = _certificate()
    _check_2019(rows)
    for year in (2014, 2024):
        rows += _national(year)
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
                "elected": number == winner,
            }
            for year, tickets in TICKETS.items()
            for number, president, vice in tickets
            for winner in [WINNERS[year][2]]
        ]
    ).write_parquet(PARSED / "tickets.parquet")
    print(f"{len(rows)} results rows")
