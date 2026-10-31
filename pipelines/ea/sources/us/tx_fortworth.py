import html
import re
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.tx_fortworth"
VERSION = "1"
PLATFORM = "tx_fortworth"
PAGE = "https://www.fortworthtexas.gov/departments/citysecretary/elections/election-history"
FIPS = "48"
DATE = re.compile(r"(?P<month>[A-Z][a-z]+)\.?\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})")
NUMBER = re.compile(r"[\d,]+")
SKIP = re.compile(r"(?i)crime control")


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")


def text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ")).strip()


def contests(section: str) -> Iterator[dict[str, Any]]:
    title = None
    choices: list[dict[str, Any]] = []
    for underlined, row in re.findall(r"underline;?\"?>(.*?)</span>|<tr[^>]*>(.*?)</tr>", section, flags=re.DOTALL):
        if underlined:
            if title is not None and choices:
                yield {"text": title, "vote_for": 1, "choices": choices, "cast": None}
            title, choices = text(underlined), []
            continue
        cells = [text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)]
        if not cells or cells[0] in ("Candidate", ""):
            continue
        underlined_row = re.search(r"underline", row)
        if underlined_row:
            if title is not None and choices:
                yield {"text": title, "vote_for": 1, "choices": choices, "cast": None}
            title, choices = cells[0], []
            continue
        if title is None or len(cells) < 2 or not NUMBER.fullmatch(cells[-1]):
            continue
        choices.append({"text": cells[0], "party": None, "total": cells[-1].replace(",", ""), "places": []})
    if title is not None and choices:
        yield {"text": title, "vote_for": 1, "choices": choices, "cast": None}


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    page = raw.cached(SOURCE, PAGE).path.read_text(errors="replace")
    parts = re.split(r"(?=<h[34][^>]*>)", page)
    for tenant in registry:
        for part in parts:
            head = re.match(r"<h[34][^>]*>(.*?)</h[34]>", part, flags=re.DOTALL)
            if head is None:
                continue
            label = text(head[1])
            found = DATE.search(label)
            if found is None or SKIP.search(label):
                continue
            day = datetime.strptime(f"{found['month'][:3]} {found['day']} {found['year']}", "%b %d %Y").date()
            nodes = []
            for node in contests(part[head.end() :].split("<h2")[0]):
                heading = node["text"]
                node["text"] = (
                    f"City of Fort Worth City Council, {heading}"
                    if re.match(r"(?i)mayor|district \d+", heading)
                    else f"City of Fort Worth, {heading}"
                )
                nodes.append({**node, "key": str(len(nodes)), "whole": True})
            yield {
                "tenant": tenant,
                "election": f"{day}-{len(nodes)}",
                "day": day,
                "title": re.sub(r"[,]?\s*" + re.escape(found[0]) + r".*$", "", label),
                "official": True,
                "primary": False,
                "contests": nodes,
            }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
