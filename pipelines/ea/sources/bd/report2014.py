"""The Bangladesh Election Commission's statistical report on the 5 January 2014 general election to the 10th Jatiya
Sangsad: a Bengali scan with no text layer. Its "constituency-wise analysis of results" prints, for each of the 147
contested constituencies, every candidate's party, symbol, and votes, then the valid, rejected, and cast votes and
the registered voters. Its list of the 300 members elected marks the 153 elected unopposed. Both were read by eye
from the page images into `report2014.json`, each contested constituency checked against its printed sums: the
candidates' votes against the valid votes, the valid and rejected votes against the votes cast, and each against the
same report's separate table of voters, centres, and turnout. This module reads the file and finds each figure's
cell in the ruled table for the value's locator."""

import functools
import itertools
import json
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import numpy as np
from PIL import Image

from ea.store import DATA

URL = "https://asset.ecs.gov.bd/media/uploaded_files/notices/2021/09/PjkqznV62UnbChDNA47y6iZovaHngtTnP6doAfwz.pdf"
TRANSCRIPT = Path(__file__).with_name("report2014.json")
# The PDF pages of the constituency-wise analysis of results, and of the list of members elected.
FIRST, LAST = 105, 122
ELECTED = (82, 91)
DPI = 100
CACHE = DATA / "scan" / "bd.ecs_parliament" / "2014"

type Box = tuple[int, int, int, int]

# The report's table of votes by party (PDF page 36): each party as it prints the name, the English name the
# archive gives it, and the candidates, seats, and valid votes it prints for the party.
PARTIES: tuple[tuple[str, str, int, int, int], ...] = (
    ("বাংলাদেশ আওয়ামী লীগ", "Bangladesh Awami League", 247, 234, 12357374),
    ("জাতীয় পার্টি", "Jatiya Party (Ershad)", 86, 34, 1199727),
    ("বাংলাদেশের ওয়ার্কার্স পার্টি", "Bangladesh Workers Party", 18, 6, 359620),
    ("জাতীয় সমাজতান্ত্রিক দল-জাসদ", "Jatiya Samajtantrik Dal-Jasad", 24, 5, 203799),
    ("বাংলাদেশ তরিকত ফেডারেশন", "Bangladesh Tarikat Federation", 3, 2, 177449),
    ("জাতীয় পার্টি-জেপি", "Jatiya Party (Manju)", 28, 2, 124389),
    ("বাংলাদেশ ন্যাশনালিস্ট ফ্রন্ট (বিএনএফ)", "Bangladesh Nationalist Front (BNF)", 22, 1, 107990),
    ("বাংলাদেশ ন্যাশনাল আওয়ামী পার্টি", "Bangladesh National Awami Party", 6, 0, 7120),
    ("বাংলাদেশ খেলাফত মজলিস", "Bangladesh Khelafat Majlish", 2, 0, 5725),
    ("গণফ্রন্ট", "Gono Front", 1, 0, 2717),
    ("বাংলাদেশ ইসলামী ফ্রন্ট", "Bangladesh Islami Front", 1, 0, 2585),
    ("গণতন্ত্রী পার্টি", "Gonotantri Party", 1, 0, 2031),
    ("স্বতন্ত্র", "Independent", 104, 16, 2579324),
)
# Each party's name as the table of parties prints it, by its English name; the registry gives it to the parties
# the 2008 report does not name in Bengali.
BENGALI = {name: bengali for bengali, name, *_ in PARTIES if name != "Independent"}
# The national totals under the same table: candidates, seats, valid, rejected, and cast votes, and the voters of
# the contested constituencies.
CANDIDATES, SEATS, VALID, REJECTED, CAST, REGISTERED = 543, 300, 17129850, 263037, 17392887, 43943184


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def squash(text: str) -> str:
    """A party name with its spacing and hyphens closed up, for matching."""
    return "".join(nfc(text).replace("\u200c", "").replace("\u200d", "").replace("-", "").split())


# Names a constituency's row prints for a party that differ from the table of parties by more than spacing.
ALIASES = {
    "বাংলাদেশের ওয়ার্কাস পার্টি": "Bangladesh Workers Party",
    "বাংলাদেশ ন্যাশনাল আওয়ামী পার্টি (ন্যাপ)": "Bangladesh National Awami Party",
}


@functools.cache
def english() -> dict[str, str]:
    found = {squash(bengali): name for bengali, name, *_ in PARTIES}
    return found | {squash(k): v for k, v in ALIASES.items()}


def party(text: str) -> str:
    """The English name of a party as the report prints it."""
    found = english().get(squash(text))
    if found is None:
        raise ValueError(f"2014: unknown party {text!r}")
    return found


class Row(TypedDict):
    name: str
    party: str
    symbol: str
    votes: int
    page: int
    row: int  # the row of the ruled table on its page, from the first below the header


class Entry(TypedDict):
    number: int
    name: str  # Bengali, as printed
    candidates: list[Row]
    valid: int
    rejected: int
    cast: int
    registered: int
    page: int  # the page and row of the constituency's line of totals
    row: int
    centres: int
    suspended: int  # the centres where polling was stopped
    remark: str | None


class Unopposed(TypedDict):
    number: int
    name: str
    winner: str
    party: str
    page: int


class Transcript(TypedDict):
    contested: list[Entry]
    unopposed: list[Unopposed]


@functools.cache
def transcript() -> Transcript:
    return json.loads(TRANSCRIPT.read_text())


# ----------------------------------------------------------------------------------------------------------------
# Locating each figure on its page. The tables are ruled, printed sideways: each page is turned upright, its ruling
# found from the rows and columns of dark pixels, and each figure's cell taken by its row and column. A page whose
# ruling does not give one row per transcribed row has no locators.

# The numeric columns from the right: votes, rejected, cast, the share of valid votes, voters, and turnout.
VOTES_COLUMN, REJECTED_COLUMN, CAST_COLUMN, VOTERS_COLUMN = 0, 1, 2, 4


def render(pdf: Path, page: int) -> np.ndarray:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"p{page}.png"
    if not out.exists():
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", str(pdf)]
            + [str(out.with_suffix(""))],
            check=True,
        )
    return np.asarray(Image.open(out).convert("L"))


def peaks(profile: np.ndarray, threshold: float) -> list[int]:
    found = np.flatnonzero(profile > threshold)
    if not len(found):
        return []
    groups = np.split(found, np.flatnonzero(np.diff(found) > 2) + 1)
    return [round(g.mean()) for g in groups if len(g)]


@dataclass(frozen=True)
class Grid:
    rows: list[tuple[int, int]]  # each body row's top and bottom, in the upright page
    columns: list[tuple[int, int]]  # the six numeric columns' left and right
    height: int  # of the page as stored, which is the upright page's width


def grid(image: np.ndarray) -> Grid | None:
    upright = np.rot90(image, k=-1)
    _height, width = upright.shape
    dark = upright < 160
    across = peaks(dark.sum(axis=1), width * 0.45)
    if len(across) < 3:
        return None
    top, bottom = across[0], across[-1]
    down = [x for x in peaks(dark[top : bottom + 1].sum(axis=0), (bottom - top) * 0.97) if 10 < x < width - 10]
    if len(down) < 7:
        return None
    # The header is the first band taller than three body rows.
    gaps = [b - a for a, b in itertools.pairwise(across)]
    body = next((i + 1 for i, gap in enumerate(gaps) if gap > 40), None)
    if body is None:
        return None
    # A stray mark across a row reads as a rule; a band too thin for a row joins the next.
    lines = [across[body]]
    for line in across[body + 1 :]:
        if line - lines[-1] < 8 and line != across[-1]:
            continue
        lines.append(line)
    rows = list(itertools.pairwise(lines))
    columns = list(zip(down[-7:], down[-6:], strict=False))
    return Grid(rows, columns, image.shape[0])


def box(found: Grid, row: int, column: int) -> Box | None:
    """A cell's box in the page as stored (portrait), in pixels at `DPI`."""
    if row >= len(found.rows):
        return None
    top, bottom = found.rows[row]
    left, right = found.columns[column]
    # The upright page is the stored page turned a quarter clockwise: upright (x, y) is stored (y, height - 1 - x).
    return (top, found.height - right, bottom, found.height - left)


@functools.cache
def grids(pdf: Path) -> dict[int, Grid]:
    found: dict[int, Grid] = {}
    rows: dict[int, int] = {}
    for entry in transcript()["contested"]:
        for c in entry["candidates"]:
            rows[c["page"]] = max(rows.get(c["page"], 0), c["row"] + 1)
        rows[entry["page"]] = max(rows.get(entry["page"], 0), entry["row"] + 1)
    for page in range(FIRST, LAST + 1):
        ruled = grid(render(pdf, page))
        if ruled is not None and len(ruled.rows) == rows.get(page):
            found[page] = ruled
    return found
