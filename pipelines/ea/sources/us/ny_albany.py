import html
import re
from collections.abc import Iterator, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.ny_albany"
VERSION = "1"
PLATFORM = "ny_albany"
SITE = "https://apps.albanycounty.com/boe/electionresults/"
FIPS = "36"
TYPE = re.compile(r"default\.asp\?type=(20\d\d(?:general|g))\b")
TITLE = re.compile(r"(?:General|Special) Election:\s*([A-Z][a-z]+)(?:&nbsp;|\s)+(\d+),\s*(\d{4})")
LINE = re.compile(r"^\s*(?P<name>.+?)\s*(?:\.\s+)+\.?\s*(?P<votes>[\d,]+)\s+(?P<pct>[\d.]+)\s*$")
PARTY = re.compile(r"\s*\(([A-Z]{2,4})\)\s*$")


def pages() -> list[str]:
    index = raw.cached(SOURCE, SITE).path.read_text(errors="replace")
    return sorted({SITE + found.group(0) for found in TYPE.finditer(index)})


def fetch() -> None:
    raw.fetch(SOURCE, SITE, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for link in pages():
        raw.fetch(SOURCE, link, suffix=".html")


def contests(text: str) -> Iterator[dict[str, Any]]:
    lines = text.split("\n")
    for index, line in enumerate(lines):
        found = re.match(r"^\s*\(VOTE FOR\)\s+(\d+)", line)
        if found is None:
            continue
        title = re.sub(r"\s+", " ", lines[index - 1]).strip()
        if re.match(r"(?i)(?:common council|city treasurer|chief city auditor|city council)", title):
            title = "City of Albany " + title
        lines_by_candidate: dict[str, tuple[str | None, int]] = {}
        totals: dict[str, int] = {}
        in_totals = False
        for other in lines[index + 2 :]:
            if "Totals by Candidate" in other:
                in_totals = True
                continue
            if other.strip().startswith("****") or not other.strip():
                if in_totals or (lines_by_candidate and not other.strip().startswith("(WITH")):
                    break
                continue
            row = LINE.match(other)
            if row is None:
                continue
            name = row["name"].strip()
            votes = int(row["votes"].replace(",", ""))
            if in_totals:
                totals[name] = votes
                continue
            party = PARTY.search(name)
            base = PARTY.sub("", name).strip()
            first = lines_by_candidate.get(base)
            lines_by_candidate[base] = (
                first[0] if first else (party[1] if party else None),
                (first[1] if first else 0) + votes,
            )
        if not lines_by_candidate:
            continue
        choices = [
            {"text": name, "party": party, "total": str(totals.get(name, votes)), "places": []}
            for name, (party, votes) in lines_by_candidate.items()
        ]
        yield {"text": title, "vote_for": int(found[1]), "choices": choices}


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for link in pages():
        page = raw.cached(SOURCE, link).path.read_text(errors="replace")
        stamp = TITLE.search(page)
        body = re.search(r"<pre>(.*?)</pre>", page, re.DOTALL | re.IGNORECASE)
        if body is None:
            continue
        if stamp:
            day = datetime.strptime(f"{stamp[1]} {stamp[2]} {stamp[3]}", "%B %d %Y").date()
        else:
            first = date(int(link.rsplit("=", 1)[1][:4]), 11, 1)
            day = first + timedelta(days=(7 - first.weekday()) % 7 + 1)
        text = html.unescape(re.sub(r"<[^>]+>", "", body[1]))
        nodes = [{**node, "key": str(n)} for n, node in enumerate(contests(text))]
        yield {
            "tenant": tenant,
            "election": link.rsplit("=", 1)[1],
            "day": day,
            "title": f"{day.year} general election",
            "official": True,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(SITE))
