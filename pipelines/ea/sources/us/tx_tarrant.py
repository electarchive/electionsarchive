import html
import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

from ea import raw
from ea.sources.us import enr_clarity, tx_harris

SOURCE = "us.tx_tarrant"
VERSION = "1"
PLATFORM = "tx_tarrant"
SITE = "https://www.tarrantcountytx.gov"
LISTING = SITE + "/en/elections/past-election-information.html"
FIPS = "48"
CUMULATIVE = re.compile(r"(?i)(?:web_)?(?:official_)?cumulative(?:_report|_results)?(?:_[a-z]+)?(?:_\d+)?\.pdf")
SLUG = re.compile(r"(?P<month>[a-z]+)-(?P<day>\d+)-+(?P<year>\d{4})-+(?P<rest>.*)\.html$")


def pages() -> Iterator[tuple[str, str]]:
    listing = raw.cached(SOURCE, LISTING).path.read_text(errors="replace")
    seen: set[str] = set()
    for link in re.findall(r'href="([^"]*past-election-information/[^"]*\.html)"', listing):
        link = urljoin(SITE, html.unescape(link))
        if link not in seen and "primary" not in link and SLUG.search(link):
            seen.add(link)
            yield link, link.rsplit("/", 1)[-1]


def reports(page: str) -> list[str]:
    body = raw.cached(SOURCE, page).path.read_text(errors="replace")
    found = [
        urljoin(SITE, html.unescape(link)) for link in re.findall(r'href="([^"]*\.pdf)"', body, flags=re.IGNORECASE)
    ]
    named = [link for link in dict.fromkeys(found) if CUMULATIVE.fullmatch(link.rsplit("/", 1)[-1])]
    return sorted(named, key=lambda link: link.rsplit("/", 1)[-1].lower() != "cumulative.pdf")[:1]


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for link, _ in pages():
        raw.fetch(SOURCE, link, suffix=".html")
    for link, _ in pages():
        for report in reports(link):
            raw.fetch(SOURCE, quote(report, safe=":/%"), suffix=".pdf")


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for tenant in registry:
        for page, name in pages():
            found = SLUG.search(name)
            day = datetime.strptime(f"{found['month']} {found['day']} {found['year']}", "%B %d %Y").date()
            for report in reports(page):
                path = raw.cached(SOURCE, quote(report, safe=":/%")).path
                text = subprocess.run(
                    ["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True
                ).stdout.decode(errors="replace")
                nodes = []
                for node in tx_harris.contests(text):
                    node["text"] = tx_harris.normalize(node["text"])
                    total = sum(int(choice["total"]) for choice in node["choices"])
                    if (
                        tx_harris.COUNTY_OFFICE.search(node["text"])
                        or re.match(r"(?i)city of fort worth city council\b", node["text"])
                        or total != node["cast"]
                    ):
                        if total != node["cast"]:
                            print(f"{day} {node['text']}: candidates add to {total}, printed {node['cast']}; skipped")
                        continue
                    nodes.append({**node, "key": str(len(nodes))})
                yield {
                    "tenant": tenant,
                    "election": f"{day}-{name[:30]}",
                    "day": day,
                    "title": re.sub(r"(?i)\s*and special\b", "", found["rest"].replace("-", " ")),
                    "official": "Official" in text[:2500] and "Unofficial" not in text[:2500],
                    "primary": False,
                    "contests": nodes,
                }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
