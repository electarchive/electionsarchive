import html
import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ea import raw
from ea.sources.us import enr_clarity, tx_collin

SOURCE = "us.tx_frisco"
VERSION = "1"
PLATFORM = "tx_frisco"
SITE = "https://www.friscotexas.gov"
PAGE = SITE + "/1849/Election-History"
FIPS = "48"
HEADING = re.compile(r"^\s{10,}(MAYOR|CITY COUNCIL,? PLACE (?:NO\.? )?(\d+))\s*$")
CANDIDATE = re.compile(r"^\s{4,}([A-Z][^\d]*?)\s{2,}([\d,]+)(?:\s+[\d.]+\s*%?)?\s*$")


def rows() -> Iterator[tuple[str, str, str]]:
    page = raw.cached(SOURCE, PAGE).path.read_text(errors="replace")
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.DOTALL)[1:]:
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
        ]
        link = re.search(r'href="([^"]+)"', row)
        if (
            link is not None
            and len(cells) > 2
            and re.search(r"Mayor|City Council", cells[2])
            and "Cancelled" not in cells[1]
        ):
            yield cells[0], cells[1], urljoin(SITE, html.unescape(link[1]))


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for _, _, link in rows():
        raw.fetch(SOURCE, link, suffix=".pdf")


def contests(text: str) -> Iterator[dict[str, Any]]:
    node: dict[str, Any] | None = None
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            if node is not None and node["choices"]:
                yield node
            node = {
                "text": "Mayor" if heading[1] == "MAYOR" else f"Place {heading[2]}",
                "vote_for": 1,
                "choices": [],
                "cast": None,
            }
            continue
        if node is None:
            continue
        if re.match(r"\s*WHEREAS|\s*NOW, THEREFORE|\s*SECTION", line):
            if node["choices"]:
                yield node
            node = None
            continue
        found = CANDIDATE.match(line)
        if found and not re.search(r"(?i)votes|percentage|page", found[1]):
            node["choices"].append(
                {
                    "text": re.sub(r"\s+", " ", found[1]).strip(),
                    "party": None,
                    "total": found[2].replace(",", ""),
                    "places": [],
                }
            )
    if node is not None and node["choices"]:
        yield node


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for tenant in registry:
        for date, kind, link in rows():
            day = datetime.strptime(date, "%B %d, %Y").date()
            path = raw.cached(SOURCE, link).path
            text = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True
            ).stdout.decode(errors="replace")
            found = list(contests(text))
            if not found:
                titles: set[str] = set()
                for node in tx_collin.electionware(text):
                    place = re.search(r"Place (\d+)", node["text"])
                    if place is not None and place[0] not in titles:
                        titles.add(place[0])
                        found.append({**node, "text": place[0]})
            nodes = []
            for node in found:
                node["text"] = f"City of Frisco City Council, {node['text']}"
                nodes.append({**node, "key": str(len(nodes)), "whole": True})
            yield {
                "tenant": tenant,
                "election": f"{day}-{kind}",
                "day": day,
                "title": f"City of Frisco {kind} election",
                "official": True,
                "primary": False,
                "contests": nodes,
            }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
