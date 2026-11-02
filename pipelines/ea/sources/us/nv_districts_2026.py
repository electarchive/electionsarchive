import csv
import io
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
FILING_URL = "https://www.nvsos.gov/SOSCandidateServices/AnonymousAccess/CF/Index.aspx"
FILING_FILE = DATA / "inbox" / "y26" / "nv" / "filing.csv"
FILING_TITLE = "Candidate Filing List, 2026 General Election"
PUBLISHER = "Nevada Secretary of State"
TITLE = "2026 General Election: Statewide Certified List of Candidates"
KEPT = re.compile(
    r"(?i)water district|fire (?:protection )?district|fire protection|improvement district|\bGID\b|hospital district|"
    r"\bTV (?:board|district)\b|television district"
)
SAME = frozenset(
    [
        "East Fork Swimming Pool District",
        "Elk Point Sanitation District",
        "Minden-Gardnerville Sanitation District",
        "Gardnerville Ranchos General Improvement District",
        "Lakeridge General Improvement District",
        "Marla Bay General Improvement District",
        "Sierra Estates General Improvement District",
        "Skyland General Improvement District",
        "Zephyr Cove General Improvement District",
        "Zephyr Heights General Improvement District",
        "Gerlach General Improvement District",
        "Stagecoach General Improvement District",
        "Beatty General Improvement District",
        "North Lake Tahoe Fire Protection District",
    ]
)
FILED = (
    (r"^Overton Power District 5, (.*)$", "Overton Power District 5", r"\1", None),
    (r"^Bunkerville, Virgin Valley Water District$", "Virgin Valley Water District", "Bunkerville", None),
    (r"^Cave Rock General Improvement District$", "Cave Rock Estates General Improvement District", "", None),
    (r"^Logan Creek General Improvement District$", "Logan Creek Estates General Improvement District", "", None),
    (r"^Roundhill General Improvement District$", "Round Hill General Improvement District", "", None),
    (r"^Sun Valley General Improvement District \(Vote 2\)$", "Sun Valley General Improvement District", "", 2),
    (r"^Grand View Terrace General Improvement District$", "Grandview Terrace General Improvement District", "", None),
    (r"^Beatty Water & Sanitation District$", "Beatty Water and Sanitation District", "", None),
    (r"^Elko County Convention Board Seat B$", "Elko Convention and Vistors Authority", "Seat B", None),
    (r"^Elko TV Board, (Seat .)$", "Elko Television District", r"\1", None),
    (r"^West Wendover Recreation Board, (Seat \d)$", "West Wendover Recreation District", r"\1", None),
    (r"^Television District, (Seat \w+)$", "Eureka County Television District", r"\1", None),
    (r"^Golconda Fire District, (Seat .)$", "Golconda Fire District", r"\1", None),
    (r"^McDermitt Fire District, (Seat .)$", "McDermitt Fire Protection District", r"\1", None),
    (r"^Winnemucca Rural Fire District, (Seat .)$", "Winnemucca Rural Fire Protection District", r"\1", None),
    (r"^Lander County Gid, (Seat .)$", "Lander County General Improvement District 1", r"\1", None),
    (r"^Lincoln County Power District Trustee$", "Lincoln County Power District 1", "", None),
    (
        r"^Lincoln County Television Trustee, (District D - Caliente)$",
        "Lincoln County Television District",
        r"\1",
        None,
    ),
    (r"^Fernley Swimming Pool Gid Board Trustee$", "Fernley Swimming Pool District", "", None),
    (r"^Smith Valley Fire Protection District, Director$", "Smith Valley Fire Protection District", "", None),
    (r"^Canyon General Improvement District, (Seat \w+)$", "Canyon General Improvement District", r"\1", None),
    (
        r"^McGill-Ruth Sewer/Water General Improvement District, (Seat .)$",
        "McGill-Ruth Water and Sewer General Improvement District",
        r"\1",
        None,
    ),
    (r"^White Pine TV District 1, (Seat .)$", "White Pine County Television District", r"\1", None),
)
DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
NAMES = (
    (r"^(.*?) Gid Board Trustee$", r"\1 General Improvement District"),
    (r"^(.*?) District Trustee$", r"\1 District"),
    (r"^(Central Lyon County Fire Protection)$", r"\1 District"),
    (r"^Elko TV Board$", "Elko Television District"),
    (r"^Verdi TV District$", "Verdi Television District"),
    (r"^Mason Valley Swimming Pool General Improvement District$", "Mason Valley Swimming Pool District"),
)
FUNCTIONS = (
    (r"(?i)water", "water"),
    (r"(?i)fire", "fire-protection"),
    (r"(?i)hospital", "hospital"),
    (r"(?i)television|\bTV\b", "television"),
    (r"(?i)improvement", "improvement"),
)


def fetch() -> None:
    for url, path, suffix, media_type in (
        (URL, FILE, ".pdf", "application/pdf"),
        (FILING_URL, FILING_FILE, ".csv", "text/csv"),
    ):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=suffix,
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type=media_type,
            )


def filed(office: str) -> tuple[str, str, int | None] | None:
    if office in SAME:
        return office, "", None
    for pattern, district, seat, vote in FILED:
        if re.match(pattern, office):
            return re.sub(pattern, district, office), re.sub(pattern, seat, office), vote
    return None


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
                "url": URL,
                "vote": None,
                "tacit": False,
            }
        )
    seen = {(row["district"], row["seat"]) for row in rows}
    for record in csv.DictReader(
        io.StringIO(raw.cached(SOURCE, FILING_URL).path.read_text(encoding="utf-8-sig")), delimiter=","
    ):
        if record["Party or Nonpartisan Office"] != "Nonpartisan" or record["Filed Status"] != "Filed":
            continue
        found = filed(record["Office Sought"])
        if found is None or (found[0], found[1]) in seen:
            continue
        rows.append(
            {
                "district": found[0],
                "seat": found[1],
                "role": "Director" if record["Office Sought"].endswith(", Director") else "Trustee",
                "name": record["Name to Appear on Ballot"].strip(),
                "county": record["Filing Office"].strip(),
                "url": FILING_URL,
                "vote": found[2],
                "tacit": True,
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
        function=pl.Series(
            [next((value for pattern, value in FUNCTIONS if re.search(pattern, d)), None) for d in frame["district"]]
        ),
    )
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="NV",
        summary="Nevada water, fire protection, general improvement, hospital, and television district board races on the November 3, 2026 ballot, with candidates from the Secretary of State's certified list",
        sources={URL: (PUBLISHER, TITLE), FILING_URL: (PUBLISHER, FILING_TITLE)},
        frame=frame,
        retire_stale=True,
    )
