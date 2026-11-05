import html
import re
from collections.abc import Callable
from pathlib import Path

import httpx
import polars as pl

from ea.sources.us.enr_2026 import FIPS, table

URL = "https://thenevadaindependent.com/elections/2026/general/results/statewide"
MARKER = "The results below are from the"
OFFICES = {
    "Governor": ("governor", ""),
    "Lieutenant Governor": ("state", "Lieutenant Governor"),
    "Attorney General": ("state", "Attorney General"),
    "Secretary Of State": ("state", "Secretary of State"),
    "Treasurer": ("state", "Treasurer"),
    "Controller": ("state", "Controller"),
}
CARD = re.compile(r"<h3\b[^>]*><span>([^<]+)</span>(.*?)(?=<h3\b|$)", re.DOTALL)
LINE = re.compile(r'truncate">([^<]+)</span><span class="[^"]*font-black[^"]*">([\d,]+)</span>')


def collect(client: httpx.Client, store: Callable[[str, bytes, str], None]) -> None:
    response = client.get(URL)
    response.raise_for_status()
    if MARKER in response.text:
        store(URL, response.content, ".html")


def frame(paths: dict[str, Path]) -> pl.DataFrame:
    found = []
    text = paths[URL].read_text()
    for title, body in CARD.findall(text.partition(MARKER)[2]):
        if title not in OFFICES:
            continue
        office, contest = OFFICES[title]
        for label, votes in LINE.findall(body):
            last, _, first = html.unescape(label).partition(", ")
            found.append(
                {
                    "state": "NV",
                    "page": URL,
                    "scope": "",
                    "contest": contest,
                    "office": office,
                    "district": "",
                    "party": None,
                    "county": FIPS["NV"],
                    "candidate": f"{first} {last}".strip(),
                    "write_in": False,
                    "votes": int(votes.replace(",", "")),
                    "as_of": None,
                    "reporting": 0,
                    "total": 0,
                }
            )
    return pl.DataFrame(found, schema=table({}).schema)
