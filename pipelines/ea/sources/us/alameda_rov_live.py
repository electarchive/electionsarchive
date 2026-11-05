import html
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from ea.sources.us.enr_2026 import table
from ea.sources.us.livevoter_live import RETENTION, STATEWIDE

URL = "https://alamedacountyca.gov/rovresults/260/index.htm"
RACE = re.compile(r'<h\d class="racePanelHeading">([^<]+)</h\d>')
LINE = re.compile(r'<TH class="resultsRow[^"]*" scope="row">([^<]+)</TH>\s*<TD[^>]*>\s*([\d,]+)\s*</TD>')
STAMP = re.compile(r"Last Updated: \w+, (\w+ \d+, \d+); (\d+:\d+ [AP]M)")
SKIP = re.compile(r"(?i)registration|^propositions$")


def collect(client: httpx.Client) -> dict[str, tuple[bytes, str]]:
    response = client.get(URL)
    response.raise_for_status()
    return {URL: (response.content, ".html")}


def frame(paths: dict[str, Path]) -> pl.DataFrame:
    found = []
    for url, path in paths.items():
        text = path.read_text()
        stamped = STAMP.search(text)
        stamp = (
            datetime.strptime(f"{stamped[1]} {stamped[2]}", "%B %d, %Y %I:%M %p")
            .replace(tzinfo=ZoneInfo("America/Los_Angeles"))
            .isoformat()
            if stamped
            else None
        )
        parts = RACE.split(text)
        for title, body in zip(parts[1::2], parts[2::2], strict=True):
            title = html.unescape(title).removesuffix("(RCV)").strip()
            if SKIP.search(title) or STATEWIDE.search(title) or RETENTION.search(title):
                continue
            for name, votes in LINE.findall(body):
                name = html.unescape(name).strip()
                found.append(
                    {
                        "state": "CA",
                        "page": url,
                        "scope": "",
                        "contest": title,
                        "office": "other",
                        "district": "",
                        "party": None,
                        "county": "Alameda",
                        "candidate": name,
                        "write_in": bool(re.search(r"(?i)write-?in", name)),
                        "votes": int(votes.replace(",", "")),
                        "as_of": stamp,
                        "reporting": 0,
                        "total": 0,
                    }
                )
    return pl.DataFrame(found, schema=table({}).schema)
