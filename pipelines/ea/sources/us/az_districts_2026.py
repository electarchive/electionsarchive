import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards
from ea.store import DATA

SOURCE = "us.az_districts_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PIMA = "https://content.civicplus.com/api/assets/az-pimacounty/4e390a40-4306-4cb1-a524-83006e9f11d4"
MARICOPA = "https://elections.maricopa.gov/asset/jcr:1673a1e7-bbed-4d76-94fe-ae0ccc1ce9d5/2026%20General%20Election%20Maricopa%20County%20Candidates%20On%20Ballot%20-%20Remediated.pdf"
MARICOPA_FILE = (
    DATA / "inbox" / "manual" / "MF-153" / "2026 General Election Maricopa County Candidates On Ballot - Remediated.pdf"
)
SOURCES = {
    PIMA: ("Pima County Elections Department", "2026 General Election, Candidates Filed (updated 7/21/2026)"),
    MARICOPA: ("Maricopa County Elections Department", "Official County Candidates, 2026 General Election"),
}
PIMA_FIRE = {
    "Avra Valley Fire District": "Avra Valley Fire District",
    "Corona De Tucson Fire District": "Corona de Tucson Fire District",
    "Golder Ranch Fire District": "Golder Ranch Fire District",
    "Mescal-J6 Fire District": "Mescal J6 Fire District",
    "Northwest Fire District": "Northwest Fire District",
    "Rincon Valley Fire District": "Rincon Valley Fire District 15",
    "Sabino Vista Fire District": "Sabino Vista Fire District",
    "Three Points Fire District": "Three Points Fire District 21",
    "Tucson Country Club Estates Fire District": "Tucson Country Club Estates Fire District 7",
}
HEADING = re.compile(r"^\*?(.+?) #\s*\d+ \((\d+) seats?\)")


def pdf_text(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True, text=True
    ).stdout


def fetch() -> None:
    try:
        raw.cached(SOURCE, PIMA)
    except LookupError:
        content = subprocess.run(
            ["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", PIMA], capture_output=True, check=True
        ).stdout
        raw.store(SOURCE, PIMA, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")
    try:
        raw.cached(SOURCE, MARICOPA)
    except LookupError:
        raw.store(
            SOURCE,
            MARICOPA,
            MARICOPA_FILE.read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp(MARICOPA_FILE.stat().st_mtime, UTC),
            media_type="application/pdf",
        )


def pima() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    district: str | None = None
    seats = 0
    people: list[str] = []

    def close() -> None:
        if district is None:
            return
        for name in people:
            rows.append(
                {
                    "district": district,
                    "seat": "",
                    "vote": seats,
                    "name": name,
                    "county": "Pima",
                    "url": PIMA,
                    "function": "fire-protection",
                    "tacit": len(people) == seats,
                }
            )

    for line in pdf_text(PIMA).splitlines():
        text = line.strip()
        if heading := HEADING.match(text):
            close()
            district = PIMA_FIRE.get(heading[1])
            seats = int(heading[2])
            people = []
        elif district is not None and text and "Write-In" not in text and "No Candidates" not in text:
            if found := re.match(
                r"^([A-Z][A-Za-z.'\- ]+?)(?: \((?:Pinal|Cochise) County Filing\)\s+N/A.*|\s{2,}\d+/\d+/\d{4}\s.*)$",
                text,
            ):
                people.append(found[1].strip())
    close()
    return rows


def maricopa() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in pdf_text(MARICOPA).splitlines():
        found = re.match(r"^MARICOPA COUNTY SPECIAL HEALTHCARE DISTRICT .*?\s{2,}(\d)\s{2,}(\S.*?)\s*$", line)
        if found is None:
            continue
        surname, _, given = found[2].partition(", ")
        rows.append(
            {
                "district": "Maricopa County Special Health Care District",
                "seat": f"District {found[1]}",
                "vote": 1,
                "name": f"{given.title()} {surname.title()}",
                "county": None,
                "url": MARICOPA,
                "function": "hospital",
                "tacit": False,
            }
        )
    return rows


def parse() -> None:
    frame = pl.DataFrame([*pima(), *maricopa()])
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet").with_columns(
        remainder=pl.lit(False), role=pl.lit("Board Member")
    )
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="AZ",
        summary="Arizona special district board races on the November 3, 2026 ballot: Pima County fire districts from the county's list of filed candidates, and the Maricopa County Special Health Care District from the county's candidates on the ballot",
        sources=SOURCES,
        frame=frame,
    )
