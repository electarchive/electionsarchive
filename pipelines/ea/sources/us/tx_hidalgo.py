import html
import re
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ea import raw
from ea.sources.us import enr_clarity, tx_harris

SOURCE = "us.tx_hidalgo"
VERSION = "1"
PLATFORM = "tx_hidalgo"
SITE = "https://www.hidalgocounty.us"
LISTINGS = (SITE + "/948/Cities-and-Schools", SITE + "/560/CitySchool-Elections")
FIPS = "48"
NUMBER = re.compile(r"[\d,]+")
PERCENT = re.compile(r"[\d.]+%")


def anchors(page: str) -> Iterator[tuple[str, str]]:
    for href, label in re.findall(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, flags=re.S):
        yield urljoin(SITE, html.unescape(href)).replace("tx-hidalgocounty.civicplus.com", "www.hidalgocounty.us"), re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(label))).strip()


def election_pages() -> list[str]:
    found: dict[str, None] = {}
    for listing in LISTINGS:
        page = raw.cached(SOURCE, listing).path.read_text(errors="replace")
        for href, label in anchors(page):
            if re.match(re.escape(SITE) + r"/\d{3,4}/", href) and re.search(r"(?i)\b20(?:1[1-9]|2\d)\b.*election|election.*\b20(?:1[1-9]|2\d)\b", label):
                found[href] = None
    return list(found)


def documents() -> Iterator[str]:
    seen: set[str] = set()
    for page_url in election_pages():
        page = raw.cached(SOURCE, page_url).path.read_text(errors="replace")
        for href, label in anchors(page):
            if "/DocumentCenter/View/" in href and "(TEXT)" in label and "PCT" not in label.upper().replace("TEXT", ""):
                key = re.sub(r"(/DocumentCenter/View/\d+).*", r"\1", href)
                if key not in seen:
                    seen.add(key)
                    yield href


def fetch() -> None:
    for listing in LISTINGS:
        raw.fetch(SOURCE, listing, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for page_url in election_pages():
        raw.fetch(SOURCE, page_url, suffix=".html")
    for href in documents():
        raw.fetch(SOURCE, href, suffix=".html")


def contests(page: str) -> Iterator[dict[str, Any]]:
    page = re.sub(r"<style.*?</style>|<script.*?</script>", "", page, flags=re.S)
    cells = [html.unescape(cell).strip() for cell in re.split(r"[\r\n]+", re.sub(r"<[^>]+>", "\n", page)) if cell.strip()]
    index = 0
    while index < len(cells) - 3:
        if cells[index + 1] != "Choice":
            index += 1
            continue
        node: dict[str, Any] = {"text": re.sub(r"\s+", " ", cells[index]), "vote_for": 1, "choices": [], "cast": None}
        index += 2
        while index < len(cells) and cells[index] != "Total":
            index += 1
        index += 1
        name = None
        numbers: list[str] = []
        while index < len(cells) and not cells[index].startswith(("Overvotes", "Undervotes")):
            cell = cells[index]
            index += 1
            if cell.startswith("Cast Votes"):
                pairs = []
                while index < len(cells) and not cells[index].startswith(("Overvotes", "Undervotes")):
                    pairs.append(cells[index])
                    index += 1
                votes = [value for value in pairs if NUMBER.fullmatch(value)]
                node["cast"] = int(votes[-1].replace(",", "")) if votes else None
                break
            if NUMBER.fullmatch(cell) or PERCENT.fullmatch(cell):
                numbers.append(cell)
                continue
            if name is not None and len(numbers) >= 2:
                node["choices"].append({"text": name, "party": None, "total": numbers[-2].replace(",", ""), "places": []})
            name, numbers = cell, []
        if name is not None and len(numbers) >= 2 and node["cast"] is None:
            node["choices"].append({"text": name, "party": None, "total": numbers[-2].replace(",", ""), "places": []})
        if node["choices"]:
            yield node


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for tenant in registry:
        for href in documents():
            page = raw.cached(SOURCE, href).path.read_text(errors="replace")
            found = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s*<?[^<]*?(?:Official|Unofficial)? Results", re.sub(r"<[^>]+>", " ", page))
            stamp = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b", re.sub(r"<[^>]+>", " ", page.split("Run Date", 1)[-1][400:]))
            when = found or stamp
            if when is None:
                print(f"{href}: no election date")
                continue
            day = date(int(when[3]), int(when[1]), int(when[2]))
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
            kind = re.search(r"Hidalgo County\s+(.*?)\s+\d{1,2}/\d{1,2}/\d{4}", title)
            nodes = []
            for node in contests(page):
                node["text"] = tx_harris.normalize(node["text"])
                if tx_harris.COUNTY_OFFICE.search(node["text"]):
                    continue
                total = sum(int(choice["total"]) for choice in node["choices"])
                if node["cast"] is not None and total != node["cast"]:
                    print(f"{day} {node['text']}: candidates add to {total}, printed {node['cast']}; skipped")
                    continue
                nodes.append({**node, "key": str(len(nodes))})
            yield {
                "tenant": tenant,
                "election": re.search(r"/View/(\d+)", href)[1],
                "day": day,
                "title": re.sub(r"(?i)\s*and special\b|\s*&\s*special\b", "", kind[1] if kind else "Local entities election"),
                "official": "Official Results" in title and "Unofficial" not in title,
                "primary": False,
                "contests": nodes,
            }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
