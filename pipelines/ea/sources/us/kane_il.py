import html
import re
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.kane_il"
VERSION = "1"
PLATFORM = "il_kane"
HOST = "https://electionresults.kanecountyil.gov"
CONTEST = re.compile(
    r'<div class="c"[^>]*>\s*<h2>\s*(.*?)\s*(?:<span>\s*-\s*(\w+)\s*</span>)?\s*</h2>(.*?)</table>', re.DOTALL
)
ROW = re.compile(r'<td class="b">(.*?)</td>\s*<td>([\d,]+)</td>')
TAGGED = re.compile(r"^(.*?)\s*\(([A-Za-z .-]+)\)$")


def _days() -> list[str]:
    found = re.findall(r'href="/(\d{4}-\d{2}-\d{2})/Contests/"', raw.cached(SOURCE, f"{HOST}/").path.read_text())
    return sorted({day for day in found if day <= date.today().isoformat()})


def fetch() -> None:
    raw.fetch(SOURCE, f"{HOST}/", suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips="17", layer=layer), suffix=".zip")
    for day in _days():
        raw.fetch(SOURCE, f"{HOST}/{day}/Contests/", suffix=".html")


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for day in _days():
        page = raw.cached(SOURCE, f"{HOST}/{day}/Contests/").path.read_text(errors="replace")
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.search(r"<h1>(.*?)</h1>", page, re.DOTALL)[1]))).strip()
        contests = []
        for index, (text, party, body) in enumerate(CONTEST.findall(page)):
            text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()
            vote_for = re.search(r"\(Vote for (\d+)\)", body)
            choices = []
            for name, votes in ROW.findall(body):
                name = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", name))).strip()
                tagged = TAGGED.match(name)
                choices.append(
                    {
                        "text": tagged[1] if tagged else name,
                        "party": tagged[2] if tagged else None,
                        "total": votes.replace(",", ""),
                        "places": [],
                    }
                )
            contests.append(
                {
                    "key": str(index),
                    "text": f"{text} - {party}" if party else text,
                    "vote_for": int(vote_for[1]) if vote_for else 1,
                    "choices": choices,
                }
            )
        yield {
            "tenant": tenant,
            "election": day,
            "day": date.fromisoformat(day),
            "title": title,
            "official": True,
            "primary": "Primary" in title,
            "contests": contests,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: True)
