import zipfile
from pathlib import Path
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.sources.us import district_boards
from ea.sources.us.sc_general_2026 import INBOX, PAGE, PUBLISHER, district_of
from ea.store import DATA

TITLE = "2026 Statewide General Election certified results file, watershed and fire district contests"
SOURCE = "us.sc_districts_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE


def fetch() -> None:
    raw.store(
        SOURCE,
        PAGE,
        INBOX.read_bytes(),
        suffix=".zip",
        retrieved_at=raw.cached("us.sc_general_2026", PAGE).retrieved_at,
        media_type="application/zip",
    )


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, PAGE).path) as archive:
        root = ElementTree.fromstring(archive.read("detail.xml"))
    rows = []
    for contest in root.iter("Contest"):
        found = district_of(contest.get("text") or "")
        if found is None:
            continue
        for choice in contest.findall("Choice"):
            if choice.get("text") == "Write-In":
                continue
            rows.append(
                {
                    "district": found[0],
                    "seat": "",
                    "role": "Commissioner",
                    "name": choice.get("text"),
                    "county": found[1],
                    "url": PAGE,
                    "vote": int(contest.get("voteFor") or 1),
                    "tacit": False,
                    "function": "fire-protection" if "Fire" in found[0] else "conservation",
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet").with_columns(remainder=pl.lit(False))
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="SC",
        summary="South Carolina watershed conservation and fire district commission races on the November 3, 2026 ballot, with candidates from the State Election Commission's certified results file",
        sources={PAGE: (PUBLISHER, TITLE)},
        frame=frame,
    )
