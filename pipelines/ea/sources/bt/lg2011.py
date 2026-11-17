"""Readers for the Election Commission of Bhutan's 2011 Local Government election result PDFs.

Each dzongkhag has a contested file (candidates grouped by gewog, with votes counted on the electronic voting machines
and by post) and an uncontested file (candidates with no opponent, who stand on a yes or no vote). Two more files give
the Thromde Thuemi and the Thromde Tshogpa."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

POSTS = ("Gup", "Mangmi", "Tshogpa", "Thromde Thuemi", "Thromde Tshogpa")
START = re.compile(r"^\s*(\d+)\s+(\d{11})\s+(.*)$")
TAIL = re.compile(
    r"^(?P<head>.*?)\s*(?P<post>Gup|Mangmi|Tshogpa|Thromde Thuemi|Thromde Tshogpa)\s+"
    r"(?P<evm>\d+)(?: \(Yes\))?\s+(?P<postal>\d+)(?: \(Yes\))?\s+(?P<total>\d+)(?: \(Yes\))?\s+(?P<remark>\S.*?)\s*$"
)
NO = re.compile(r"^\s*(\d*) ?\(No\)\s+(\d*) ?\(No\)\s+(\d*) ?\(No\)\s*$")
GEWOG = re.compile(r"^List of Candidates of (.+?)(?: Gewog by Post\.?)?\s*$")
CHUNK = re.compile(r"\s{2,}")


@dataclass(frozen=True)
class Record:
    dzongkhag: str
    gewog: str
    name: str
    chiwog: str
    post: str
    evm: int
    postal: int
    total: int
    elected: bool
    tied: bool
    no: tuple[int, int, int] | None
    uncontested: bool
    page: int


def text(path: Path) -> list[str]:
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return out.split("\n")


def remark(raw: str) -> tuple[bool, bool]:
    """Whether the candidate was elected, and whether the Commission marks the result a tie, from the Remarks cell."""
    tied = raw.endswith(" T")
    word = raw.removesuffix(" T").replace(" ", "")
    if word == "NotElected":
        return False, tied
    if word in {"Elected", "Eected", "Eleected"}:
        return True, tied
    raise ValueError(f"unknown remark {raw!r}")


def records(path: Path, dzongkhag: str | None, *, uncontested: bool) -> list[Record]:
    """Every candidate row of one file, with the gewog (or thromde) heading it falls under and its page."""
    lines = text(path)
    out: list[Record] = []
    gewog = ""
    page = 1
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if "Page " in line and " of " in line and "ELECTION COMMISSION" in line:
            page += 1
            continue
        heading = GEWOG.match(line.strip())
        if heading and not line.startswith(" "):
            gewog = heading[1]
            continue
        title = re.match(r"^(?:Result of )?(.+?) -Uncontested Election\.", line.strip())
        if title:
            dzongkhag = title[1]
            continue
        title = re.match(r"^Gewog wise Result of Candidates of (.+?) Dzongkhag by Post", line.strip())
        if title:
            dzongkhag = title[1]
            continue
        if (
            line
            and not line.startswith(" ")
            and re.fullmatch(r"[A-Za-z ]+", line)
            and i < len(lines)
            and lines[i].startswith("List of Candidates")
        ):
            dzongkhag = line.strip()
            continue
        found = START.match(line)
        if not found:
            continue
        row = line
        while not TAIL.match(row):
            if i >= len(lines) or START.match(lines[i]):
                raise ValueError(f"{path.name}: no votes on the row {line!r}")
            row += " " + lines[i].strip()
            i += 1
        tail = TAIL.match(row)
        assert tail is not None
        no = None
        if uncontested or "(Yes)" in row:
            while i < len(lines) and not lines[i].strip():
                i += 1
            counted = NO.match(lines[i]) if i < len(lines) else None
            if counted is None:
                raise ValueError(f"{path.name}: no 'No' votes under {line!r}")
            no = (int(counted[1]), int(counted[2]), int(counted[3])) if counted[1] else None
            i += 1
        head = re.match(r"^\s*\d+\s+\d{11}\s+(.*)$", tail["head"])
        if head is None:
            raise ValueError(f"{path.name}: no candidate in {line!r}")
        parts = [part for part in CHUNK.split(head[1].strip()) if part]
        if len(parts) != 2:
            raise ValueError(f"{path.name}: cannot split name and chiwog in {line!r}")
        elected, tied = remark(tail["remark"])
        out.append(
            Record(
                dzongkhag or "",
                gewog,
                parts[0],
                parts[1],
                tail["post"],
                int(tail["evm"]),
                int(tail["postal"]),
                int(tail["total"]),
                elected,
                tied,
                no,
                uncontested or "(Yes)" in row,
                page,
            )
        )
    return out
