import html
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from ea.sources.us.enr_2026 import table

LINK = re.compile(r"https://www\.livevoterturnout\.com/ENR/\w+/\d+/en/\w*Index_\d+\.html")
RACE = re.compile(r'<span class="race-title">([^<]+)</span>')
LINE = re.compile(
    r'data-contestant="\d+" data-candidate="true".*?aria-hidden="true">([^<]+)</span>'
    r'.*?data-votes="true" class="content-data">([\d,]*)</td>',
    re.DOTALL,
)
STAMP = re.compile(r'id="lblLastPublishedDateTime"[^>]*>([^<]+)<')
STATEWIDE = re.compile(
    r"(?i)^(governor|lieutenant governor|secretary of state|controller|treasurer|attorney general"
    r"|insurance commissioner|(state )?superintendent of public instruction|member,? state board of equalization"
    r"|state board of equalization|united states|u\.?s\.? |state senator|member of the state assembly"
    r"|member,? state assembly|state assembly|proposition|state measure|prop\.? \d"
    r"|(for )?(associate |chief )?justice|shall .* be elected)"
)
RETENTION = re.compile(r"(?i)court of appeal|supreme court")


def collect(client: httpx.Client, url: str) -> dict[str, tuple[bytes, str]]:
    response = client.get(url)
    response.raise_for_status()
    link = LINK.search(response.text)
    if link and link[0] != url:
        url = link[0]
        response = client.get(url)
        response.raise_for_status()
    return {url: (response.content, ".html")}


def frame(county: str, paths: dict[str, Path]) -> pl.DataFrame:
    found = []
    for url, path in paths.items():
        text = path.read_text()
        stamped = STAMP.search(text)
        stamp = (
            datetime.strptime(stamped[1].strip(), "%m/%d/%Y %I:%M:%S %p")
            .replace(tzinfo=ZoneInfo("America/Los_Angeles"))
            .isoformat()
            if stamped
            else None
        )
        parts = RACE.split(text)
        for title, body in zip(parts[1::2], parts[2::2], strict=True):
            title = html.unescape(title).strip()
            if STATEWIDE.search(title) or RETENTION.search(title):
                continue
            for name, votes in LINE.findall(body):
                name = html.unescape(name).strip().lstrip("*")
                write_in = bool(re.search(r"(?i)write-?in", name))
                found.append(
                    {
                        "state": "CA",
                        "page": url,
                        "scope": "",
                        "contest": title,
                        "office": "other",
                        "district": "",
                        "party": None,
                        "county": county,
                        "candidate": name,
                        "write_in": write_in,
                        "votes": int(votes.replace(",", "") or 0),
                        "as_of": stamp,
                        "reporting": 0,
                        "total": 0,
                    }
                )
    return pl.DataFrame(found, schema=table({}).schema)
