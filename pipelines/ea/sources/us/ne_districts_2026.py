import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards
from ea.store import DATA

SOURCE = "us.ne_districts_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://sos.nebraska.gov/sites/default/files/doc/elections/2026/Final_Statewide_General_Candidate_Filing_List_9.11.26.pdf"
PUBLISHER = "Nebraska Secretary of State"
TITLE = "Final Statewide General Candidate List, November 3, 2026 General Election"
BODY = re.compile(
    r"^(.*?(?:Natural Resources District|Community College|Public Power District|Public Power and Irrigation District|"
    r"Reclamation District|Educational Service Unit No\. \d+|Metropolitan Utilities District of Omaha|"
    r"Learning Community of Douglas & Sarpy))"
)
KEEP = re.compile(
    r"Natural Resources|Community College|Public Power|Reclamation District|Educational Service Unit|"
    r"Metropolitan Utilities|Learning Community"
)
SEAT = re.compile(
    r"(Subdistrict \d+|Subcouncil \d+|Subdivision \d+|At Large|District \d\d|"
    r"(?:Keya Paha|Gosper County|Kearney County|Lincoln County|Phelps County|Cherry|Rock|West|Buffalo|Dawson|"
    r"Lincoln|North|East|South) Subdivision)"
)
NOISE = re.compile(
    r"\bFor Board(?: of)?\b|\bBoard of\b|\b(?:Directors|Governors|Regents)\b|\bFor\b|\bSubdivision\b|\bPage\b|\bCounties\b"
)
STATUS = re.compile(r"\b(Nonincumbent|Incumbent)\b")
SPLIT = (
    ("Central Nebraska Public Power and", "Central Nebraska Public Power and Irrigation District"),
    ("KBR Rural Public Power District", "KBR Rural Public Power District"),
    ("Wheat Belt Public Power District", "Wheat Belt Public Power District"),
)
ROLES = (
    ("Community College", "Governor", "college"),
    ("Educational Service Unit", "Board Member", "school"),
    ("Learning Community", "Subcouncil Member", "school"),
    ("Natural Resources District", "Director", "natural-resources"),
)


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        content = subprocess.run(
            ["curl", "-sSL", "-m", "180", "-A", "Mozilla/5.0", URL], capture_output=True, check=True
        ).stdout
        raw.store(SOURCE, URL, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def layout(path: Path) -> list[dict[str, str | int | bool]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True, text=True).stdout
    entries: list[dict[str, str | int | list[str]]] = []
    current: dict[str, str | int | list[str]] | None = None
    for line in text.split("\n"):
        stripped = line.strip().lstrip("\f").strip()
        if not stripped or stripped.startswith(("Nebraska Secretary", "Office", "(if applicable)", "November 3")):
            continue
        fields = re.split(r"\s{2,}", stripped)
        term = next((i for i in range(len(fields) - 1) if fields[i].isdigit() and fields[i + 1].isdigit()), None)
        status = next((i for i, field in enumerate(fields) if STATUS.search(field)), None)
        if term is not None and status is not None and status >= term + 3:
            found = re.match(r"^(.*?)\s*\b(Nonincumbent|Incumbent)\b", fields[status])
            assert found is not None
            name_at = status - 1 if found.group(1) else status - 2
            current = {
                "pre": fields[:term],
                "term": int(fields[term]),
                "vote": int(fields[term + 1]),
                "name": fields[name_at],
                "incumbent": found.group(2) == "Incumbent",
                "cont": [],
            }
            entries.append(current)
        elif (
            current is not None
            and term is None
            and status is None
            and len(fields) <= 3
            and "@" not in stripped
            and "(" not in stripped
            and not stripped[0].isdigit()
        ):
            current["cont"].append(stripped)  # ty: ignore[possibly-missing-attribute]
    rows: list[dict[str, str | int | bool]] = []
    for entry in entries:
        pieces = [*entry["pre"], *(c for c in entry["cont"] if "Page " not in c and not re.search(r"NE \d{5}", c))]  # ty: ignore[not-iterable]
        full = " ".join(pieces)
        if not KEEP.search(full):
            continue
        seats = [m.group(1) for m in SEAT.finditer(full)]
        seat = seats[-1] if seats else ""
        body = full
        for part in seats:
            body = body.replace(part, " ", 1)
        body = re.sub(r"\s+of\s*$", "", " ".join(NOISE.sub(" ", body).split()))
        for lead, canon in SPLIT:
            if body.startswith(lead):
                tail = body[len(lead) :].replace("Irrigation District", "").replace(" County", "").strip()
                body = canon
                if lead.startswith("Central"):
                    seat = f"{tail} County Subdivision"
                elif tail:
                    seat = f"{tail} Subdivision"
        body = body.replace("Learning Community of Douglas & Sarpy", "Learning Community of Douglas and Sarpy Counties")
        rows.append(
            {
                "district": body,
                "seat": seat,
                "term": entry["term"],
                "vote": entry["vote"],
                "name": " ".join(str(entry["name"]).split()),
                "incumbent": entry["incumbent"],
            }
        )
    return rows


def parse() -> None:
    rows = layout(raw.cached(SOURCE, URL).path)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["district", "seat", "term", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def role(district: str) -> tuple[str, str | None]:
    for key, word, function in ROLES:
        if key in district:
            return word, function
    return "Director", None


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    full_term = {district: max(group["term"]) for (district,), group in frame.group_by(["district"])}
    roles = [role(district) for district in frame["district"]]
    frame = frame.with_columns(
        remainder=pl.Series(
            [term < full_term[district] for term, district in zip(frame["term"], frame["district"], strict=True)]
        ),
        role=pl.Series([word for word, _ in roles]),
        function=pl.Series([function for _, function in roles]),
        county=pl.lit(None, dtype=pl.Utf8),
    )
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="NE",
        summary="Nebraska natural resources district, public power district, community college, educational service unit, and other district board races on the November 3, 2026 ballot, with candidates from the Secretary of State's final statewide list",
        publisher=PUBLISHER,
        title=TITLE,
        url=URL,
        frame=frame,
    )
