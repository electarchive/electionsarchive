import html
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards
from ea.store import DATA

SOURCE = "us.fl_districts_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "y26" / "fl"
SOURCES = {
    "https://browardvotes.gov/sites/default/files/list-of-candidates-for-november-elections.pdf": (
        "Broward County Supervisor of Elections",
        "List of Candidates and Contests, November 3, 2026 General Election",
        "broward.pdf",
        "Broward",
    ),
    "https://www.votebrevard.gov/Candidate-Information/2026-Candidates": (
        "Brevard County Supervisor of Elections",
        "2026 Candidates",
        "brevard.html",
        "Brevard",
    ),
    "https://www.pascovotes.gov/Documents/Candidates/List%20Of%202026%20Candidates/Tot2026.pdf": (
        "Pasco County Supervisor of Elections",
        "2026 Local Candidates, Pasco County (9/16/2026)",
        "pasco.pdf",
        "Pasco",
    ),
}
DISTRICT = re.compile(
    r"(?i)community development district|water control district|improvement district|drainage district|"
    r"recreation district|soil and water"
)
OUT = re.compile(r"(?i)withdr|defeated|elected in primary|disqualif|did not qualify|no qualified")
FUNCTIONS = (
    (r"(?i)community development", "community-development"),
    (r"(?i)improvement", "improvement"),
)


def fetch() -> None:
    for url, (_, _, file, _) in SOURCES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = INBOX / file
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=path.suffix,
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf" if path.suffix == ".pdf" else "text/html",
            )


def pdf_text(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True, text=True
    ).stdout


def flip(name: str) -> str:
    last, _, first = name.partition(", ")
    return " ".join(f"{first} {last}".split())


def split_title(title: str) -> tuple[str, str]:
    district, _, seat = title.partition(", ")
    return district.strip(), seat.strip()


def broward(url: str) -> list[dict[str, str | int | None]]:
    rows = []
    for line in pdf_text(url).splitlines():
        found = re.match(r"(\S.*?)\s{2,}(\S.*?)\s{2,}(\S.*?)\s*$", line)
        if found is None or not DISTRICT.search(found[1]) or found[2] == "Write-in":
            continue
        district, seat = split_title(found[1])
        rows.append({"district": district, "seat": seat, "vote": 1, "name": found[2], "url": url})
    return rows


def brevard(url: str) -> list[dict[str, str | int | None]]:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    rows = []
    for part in text.split("<h3>")[1:]:
        title = html.unescape(re.sub(r"<[^>]+>", "", part.split("</h3>")[0])).strip()
        if not DISTRICT.search(title):
            continue
        vote = re.search(r"\((\d+) Seats? Available\)", title)
        title = re.sub(r"\s*\(\d+ Seats? Available\)", "", title)
        title = re.sub(r" (?:Supervisor|Trustee)(?=,|$)", "", title)
        district, seat = split_title(title)
        people = []
        for name, status in re.findall(
            r"<tr>\s*<td[^>]*><strong>(.*?)</strong>.*?</td>\s*<td[^>]*>(.*?)</td>", part, re.DOTALL
        ):
            name = " ".join(html.unescape(re.sub(r"<[^>]+>", "", name)).split())
            status = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", status)).split())
            if name != "Candidate" and "Qualified" in status and not OUT.search(status):
                people.append(name)
        if len(people) < 2 and not (vote and people):
            continue
        for name in people:
            rows.append(
                {"district": district, "seat": seat, "vote": int(vote[1]) if vote else 1, "name": name, "url": url}
            )
    return rows


def pasco(url: str) -> list[dict[str, str | int | None]]:
    rows = []
    district = ""
    entry: dict[str, str] | None = None
    for line in pdf_text(url).splitlines():
        stripped = line.strip()
        if re.match(r"^[A-Z][\w .&'-]+ (?:Community Development )?District$", stripped) or stripped.endswith(
            "Development District"
        ):
            district = stripped
            entry = None
            continue
        found = re.match(r"^\s*([A-Z]{3})\s+(\S.*?, .*?)\s{2,}(\S+)\s{2,}(\S.*)$", line)
        if found:
            entry = {"name": flip(found[2]), "status": found[4].strip(), "seat": ""}
            if entry["status"].startswith("Qualified") and district:
                rows.append({"district": district, "seat": "", "vote": 1, "name": entry["name"], "url": url})
                entry["row"] = "1"
            continue
        seat = re.match(r"^\s{20,}Seat (\d+)\s*$", line)
        if seat and entry is not None and entry.get("row"):
            rows[-1]["seat"] = f"Seat {seat[1]}"
    return rows


def parse() -> None:
    rows: list[dict[str, str | int | None]] = []
    for url, (_, _, _, county) in SOURCES.items():
        found = {"broward.pdf": broward, "brevard.html": brevard, "pasco.pdf": pasco}[SOURCES[url][2]](url)
        for row in found:
            row["county"] = county
        rows.extend(found)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["district", "seat", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    roles = []
    for district in frame["district"]:
        if re.search(r"(?i)community development|soil and water|water control|improvement", district):
            roles.append("Supervisor")
        elif "Recreation" in district:
            roles.append("Trustee")
        else:
            roles.append("Commissioner")
    frame = frame.with_columns(
        remainder=pl.lit(False),
        role=pl.Series(roles),
        function=pl.Series(
            [next((value for pattern, value in FUNCTIONS if re.search(pattern, d)), None) for d in frame["district"]]
        ),
    )
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="FL",
        summary="Florida community development, water control, drainage, improvement, and recreation district board races on the November 3, 2026 ballot, with candidates from the Broward, Brevard, and Pasco County supervisors of elections",
        sources={url: (publisher, title) for url, (publisher, title, _, _) in SOURCES.items()},
        frame=frame,
    )
