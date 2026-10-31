import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards
from ea.store import DATA

SOURCE = "us.nv_districts_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.nvsos.gov/sos/elections/election-information/2026-election-information"
FILE = DATA / "inbox" / "y26" / "nv" / "certified.pdf"
PUBLISHER = "Nevada Secretary of State"
TITLE = "2026 General Election: Statewide Certified List of Candidates"
KEPT = re.compile(
    r"(?i)water district|fire (?:protection )?district|fire protection|improvement district|\bGID\b|hospital district|"
    r"\bTV (?:board|district)\b|television district"
)
DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
NAMES = (
    (r"^(.*?) Gid Board Trustee$", r"\1 General Improvement District"),
    (r"^(.*?) District Trustee$", r"\1 District"),
    (r"^(Central Lyon County Fire Protection)$", r"\1 District"),
)
FUNCTIONS = (
    (r"(?i)water", "water"),
    (r"(?i)fire", "fire-protection"),
    (r"(?i)hospital", "hospital"),
    (r"(?i)television|\bTV\b", "television"),
    (r"(?i)improvement", "improvement"),
)


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(
            SOURCE,
            URL,
            FILE.read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp(FILE.stat().st_mtime, UTC),
            media_type="application/pdf",
        )


def split_office(office: str) -> tuple[str, str, str]:
    office = re.sub(r"\s*\((\d) Year Term\)", "", office).strip()
    if "Water District" in office and "," in office:
        place, district = (part.strip() for part in office.split(",", 1))
        return district, place, "Trustee"
    district, _, seat = office.partition(", ")
    for pattern, replacement in NAMES:
        district = re.sub(pattern, replacement, district)
    word = "Director" if seat == "Director" else "Trustee"
    return district, "" if seat == "Director" else seat, word


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    rows = []
    for line in text.split("\n"):
        fields = re.split(r"\s{2,}", line.strip())
        if len(fields) < 6 or not DATE.match(fields[5]) or fields[2] != "Nonpartisan":
            continue
        if not KEPT.search(fields[3]) or re.search(r"(?i)school|judge|court|town", fields[3]):
            continue
        if len(fields) > 6 and "Challenged" in fields[6]:
            continue
        district, seat, word = split_office(fields[3])
        rows.append(
            {
                "district": district,
                "seat": seat,
                "role": word,
                "name": fields[1].strip(),
                "county": fields[4].replace("City Of ", "").strip(),
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["district", "seat", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    frame = frame.with_columns(
        remainder=pl.lit(False),
        vote=pl.lit(None, dtype=pl.Int64),
        function=pl.Series(
            [next((value for pattern, value in FUNCTIONS if re.search(pattern, d)), None) for d in frame["district"]]
        ),
    )
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="NV",
        summary="Nevada water, fire protection, general improvement, hospital, and television district board races on the November 3, 2026 ballot, with candidates from the Secretary of State's certified list",
        publisher=PUBLISHER,
        title=TITLE,
        url=URL,
        frame=frame,
    )
