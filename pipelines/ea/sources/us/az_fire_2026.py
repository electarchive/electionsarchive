import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards
from ea.store import DATA

SOURCE = "us.az_fire_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://elections.maricopa.gov/candidates/2026-general-election-filers.html"
FILE = DATA / "inbox" / "manual" / "MF-459" / "2026.09.21 General Filers.pdf"
PUBLISHER = "Maricopa County Elections Department"
TITLE = "2026 General Election Candidate Filers"
NAMES = {
    "BUCKEYE VALLEY RURAL FIRE DIST": "Buckeye Valley Rural Fire District",
    "CHANDLER COUNTY ISLAND FD": "Chandler County Island Fire District",
    "CIRCLE CITY-MORRISTOWN FD": "Circle City-Morristown Fire District",
    "CLEARWATER HILLS FD": "Clearwater Hills Fire District",
    "DAISY MOUNTAIN FIRE DISTRICT": "Daisy Mountain Fire District",
    "GILBERT COUNTY ISLAND FD": "Gilbert County Island Fire District",
    "HARQUAHALA VALLEY FIRE DIST": "Harquahala Valley Fire District",
    "NORTH COUNTY FIRE & MEDICAL": "North County Fire and Medical District",
    "QUEEN CREEK COUNTY ISLAND FD": "Queen Creek County Island Fire District",
    "RIO VERDE FIRE DISTRICT": "Rio Verde Fire District",
    "SCOTTSDALE COUNTY ISLAND FD": "Scottsdale County Island Fire District",
    "SOUTH COUNTY FIRE & MEDICAL": "South County Fire and Medical District",
    "SUN CITY FIRE DISTRICT": "Sun City Fire District",
    "TEMPE COUNTY ISLAND FD": "Tempe County Island Fire District",
}


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


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    rows = []
    for line in text.split("\n"):
        fields = re.split(r"\s{2,}", line.strip())
        if len(fields) < 4 or fields[0] != "FIRE BOARD MEMBER":
            continue
        rows.append(
            {
                "district": NAMES[fields[1]],
                "seat": "",
                "role": "Board Member",
                "name": fields[2].strip(),
                "county": "Maricopa",
            }
        )
    assert len(rows) == 27, len(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["district", "seat", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet").with_columns(
        remainder=pl.lit(False),
        url=pl.lit(URL),
        vote=pl.lit(None, dtype=pl.Int64),
        function=pl.lit("fire-protection"),
    )
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="AZ",
        summary="Maricopa County fire district board races on the November 3, 2026 ballot, with candidates from the county elections department's general election filers list",
        sources={URL: (PUBLISHER, TITLE)},
        frame=frame,
    )
