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
