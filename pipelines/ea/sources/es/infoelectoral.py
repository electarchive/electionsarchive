import zipfile
from datetime import UTC, datetime
from pathlib import Path

from ea import raw
from ea.store import DATA

SOURCE = "es.infoelectoral"
INBOX = DATA / "inbox" / "manual" / "MF-669"
APLIEXTR = "https://infoelectoral.interior.gob.es/estaticos/docxl/apliextr/"
DOCXL = "https://infoelectoral.interior.gob.es/estaticos/docxl/"
PAGE = "https://infoelectoral.interior.gob.es/es/proceso-electoral/"


def url_for(file: Path) -> str:
    if file.name.startswith("PROV_"):
        return DOCXL + file.name
    if file.suffix == ".html":
        return PAGE
    return APLIEXTR + file.name


def fetch() -> None:
    for file in sorted(INBOX.iterdir()):
        if file.suffix not in {".zip", ".html"}:
            continue
        raw.store(
            SOURCE,
            url_for(file),
            file.read_bytes(),
            suffix=file.suffix,
            retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
            media_type="application/zip" if file.suffix == ".zip" else "text/html",
        )


LAYOUTS: dict[str, tuple[tuple[str, int, int], ...]] = {
    "03": (("list", 9, 14), ("abbrev", 15, 64), ("name", 65, 214), ("province_head", 215, 220)),
    "04": (
        ("round", 9, 9),
        ("province", 10, 11),
        ("district", 12, 12),
        ("municipality", 13, 15),
        ("list", 16, 21),
        ("position", 22, 24),
        ("kind", 25, 25),
        ("given", 26, 50),
        ("surname", 51, 75),
        ("second_surname", 76, 100),
        ("sex", 101, 101),
        ("birth_day", 102, 103),
        ("birth_month", 104, 105),
        ("birth_year", 106, 109),
        ("elected", 120, 120),
    ),
    "05": (
        ("round", 9, 9),
        ("community", 10, 11),
        ("province", 12, 13),
        ("municipality", 14, 16),
        ("municipal_district", 17, 18),
        ("name", 19, 118),
        ("district", 119, 119),
        ("census", 150, 157),
        ("blank", 190, 197),
        ("invalid", 198, 205),
        ("list_votes", 206, 213),
        ("official", 233, 233),
    ),
    "06": (
        ("round", 9, 9),
        ("province", 10, 11),
        ("municipality", 12, 14),
        ("municipal_district", 15, 16),
        ("list", 17, 22),
        ("votes", 23, 30),
        ("seats", 31, 33),
    ),
    "07": (
        ("round", 9, 9),
        ("community", 10, 11),
        ("province", 12, 13),
        ("district", 14, 14),
        ("name", 15, 64),
        ("population", 65, 72),
        ("mesas", 73, 77),
        ("census_ine", 78, 85),
        ("census", 86, 93),
        ("blank", 126, 133),
        ("invalid", 134, 141),
        ("list_votes", 142, 149),
        ("seats", 150, 155),
        ("official", 172, 172),
    ),
    "08": (
        ("round", 9, 9),
        ("community", 10, 11),
        ("province", 12, 13),
        ("district", 14, 14),
        ("list", 15, 20),
        ("votes", 21, 28),
        ("seats", 29, 33),
    ),
    "09": (
        ("round", 9, 9),
        ("community", 10, 11),
        ("province", 12, 13),
        ("municipality", 14, 16),
        ("municipal_district", 17, 18),
        ("section", 19, 22),
        ("mesa", 23, 23),
        ("census_ine", 24, 30),
        ("census", 31, 37),
        ("blank", 66, 72),
        ("invalid", 73, 79),
        ("list_votes", 80, 86),
        ("official", 101, 101),
    ),
    "10": (
        ("round", 9, 9),
        ("community", 10, 11),
        ("province", 12, 13),
        ("municipality", 14, 16),
        ("municipal_district", 17, 18),
        ("section", 19, 22),
        ("mesa", 23, 23),
        ("list", 24, 29),
        ("votes", 30, 36),
    ),
}
COUNTS = {
    "seats",
    "votes",
    "population",
    "mesas",
    "census_ine",
    "census",
    "blank",
    "invalid",
    "list_votes",
    "list",
    "province_head",
    "position",
}


def archive(kind: str, process: str, extent: str) -> Path:
    return raw.cached(SOURCE, f"{APLIEXTR}{kind}{process}_{extent}.zip").path


def processes(kind: str) -> list[str]:
    return sorted({key.rsplit("/", 1)[1][2:8] for key in raw.keys(SOURCE) if key.startswith(f"{APLIEXTR}{kind}")})


def records(kind: str, process: str, extent: str, number: str) -> list[dict[str, str | int]]:
    with zipfile.ZipFile(archive(kind, process, extent)) as bundle:
        name = f"{number}{kind}{process[2:]}.DAT"
        if name not in bundle.namelist():
            return []
        text = bundle.read(name).decode("latin-1")
    rows = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        row: dict[str, str | int] = {}
        for field, start, end in LAYOUTS[number]:
            value = line[start - 1 : end].strip()
            row[field] = int(value or 0) if field in COUNTS else value
        rows.append(row)
    return rows
