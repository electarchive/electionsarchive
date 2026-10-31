from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import polars as pl

from ea import raw
from ea.sources.us import statewide_totals
from ea.store import DATA

SOURCE = "us.statewide_stragglers"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-373"
SD_FILE = "South Dakota 2026/Results for Runoff.pdf"
SD_URL = "https://sdsos.gov/elections-voting/upcoming-elections/general-information/2026%20Election%20Information/default.aspx#MF-373/Results%20for%20Runoff.pdf"
IN_TITLE = "2000 Indiana Attorney General election"
IN_URL = "https://en.wikipedia.org/w/api.php?" + urlencode(
    {"action": "parse", "page": IN_TITLE, "prop": "wikitext", "format": "json", "formatversion": 2, "redirects": 1}
)
BOOK = {
    "sd-runoff-2026": {
        "publisher": "South Dakota Secretary of State",
        "title": "Official results of the 2026 gubernatorial runoff election, press release of August 5, 2026",
        "url": SD_URL,
        "landing": "https://sdsos.gov/elections-voting/upcoming-elections/general-information/2026%20Election%20Information/default.aspx",
        "license": "Public record",
        "page_offset": 0,
        "kind": "official_body",
    },
    "in-ag-2000": {
        "publisher": "Wikipedia",
        "title": IN_TITLE,
        "url": IN_URL,
        "landing": f"https://en.wikipedia.org/wiki/{quote(IN_TITLE.replace(' ', '_'))}",
        "license": "CC BY-SA 4.0",
        "page_offset": 0,
        "kind": "community",
    },
}
NOTE = (
    "Statewide totals only. South Dakota's are the candidate totals in the Secretary of State's press release after "
    "the State Board of Canvassers certified the runoff; the county canvass is not online. Indiana's are the election "
    "box in the English Wikipedia article, which cites the US Election Atlas; the Election Division posts no 2000 "
    "results online, and the candidate rows sum to the box's total of 2,102,164."
)
LINES = [
    ("sd-runoff-2026", "SD", "2026-07-28", "governor", "Governor", "Larry Rhoden", "Republican", 83590, 1, 1, "runoff"),
    ("sd-runoff-2026", "SD", "2026-07-28", "governor", "Governor", "Toby Doeden", "Republican", 34435, 0, 1, "runoff"),
    (
        "in-ag-2000",
        "IN",
        "2000-11-07",
        "attorney-general",
        "Attorney General",
        "Steve Carter",
        "Republican",
        1077961,
        1,
        None,
        "general",
    ),
    (
        "in-ag-2000",
        "IN",
        "2000-11-07",
        "attorney-general",
        "Attorney General",
        "Karen Freeman-Wilson",
        "Democratic",
        978713,
        0,
        None,
        "general",
    ),
    (
        "in-ag-2000",
        "IN",
        "2000-11-07",
        "attorney-general",
        "Attorney General",
        "N. Sean Harshey",
        "Libertarian",
        45490,
        0,
        None,
        "general",
    ),
]


def fetch() -> None:
    try:
        raw.cached(SOURCE, SD_URL)
    except LookupError:
        path = INBOX / SD_FILE
        raw.store(
            SOURCE,
            SD_URL,
            path.read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            media_type="application/pdf",
        )
    raw.fetch(SOURCE, IN_URL, suffix=".json")


def parse() -> None:
    frame = pl.DataFrame(
        LINES,
        schema=["source", "state", "date", "office", "title", "name", "party", "votes", "winner", "page", "stage"],
        orient="row",
        schema_overrides={"page": pl.Int64, "votes": pl.Int64, "winner": pl.Int64},
    ).with_columns(
        scope=pl.when(pl.col("stage") == "runoff").then(pl.col("party")),
        first=pl.when(pl.col("stage") == "runoff").then(pl.lit("2026-06-02:SD:governor:republican")),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("state", "office").agg(pl.col("votes").sum()))


def emit() -> Path:
    return statewide_totals.emit(
        SOURCE,
        VERSION,
        "South Dakota's 2026 Republican gubernatorial runoff and Indiana's 2000 attorney general race by state total",
        pl.read_parquet(PARSED / "lines.parquet"),
        BOOK,
        NOTE,
    )
