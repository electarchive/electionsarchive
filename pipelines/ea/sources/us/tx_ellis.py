import html
import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ea import raw
from ea.sources.us import enr_clarity, tx_collin, tx_harris

SOURCE = "us.tx_ellis"
VERSION = "1"
PLATFORM = "tx_ellis"
SITE = "https://www.elliscountytx.gov"
LISTING = SITE + "/403/Prior-Election-Results"
FIPS = "48"
DATE = re.compile(r"(?P<month>[A-Z][a-z]+)\.? (?P<day>\d{1,2}),? (?P<year>\d{4})")
DOCUMENT = re.compile(
    r"(?i)^(?!.*(?:precinct|reconcil|zip|other doc|turnout|hand count|audit|index|livevoter)).*"
    r"(?:cumulative|summary results|canvassed result|\bresults$|results \(pdf\)|reports for canvass)"
)
PORTION = re.compile(r"(?i)[\s,-]*ellis (?:co(?:unty)?\.? portion only|and \w+ co(?:unty)?\.? combined res\w*)")


def anchors(page: str) -> Iterator[tuple[str, str]]:
    for href, label in re.findall(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, flags=re.DOTALL):
        yield (
            re.sub(
                r"https?://(?:www\.co\.ellis\.tx\.us|tx-elliscounty3\.civicplus\.com)",
                SITE,
                urljoin(SITE, html.unescape(href)),
            ),
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(label))).strip(),
        )


def year_pages() -> list[str]:
    page = raw.cached(SOURCE, LISTING).path.read_text(errors="replace")
    found = {
        href: None
        for href, label in anchors(page)
        if re.fullmatch(r"(20(?:1[7-9]|2\d)) Election Results", label) and re.search(r"/\d{4}-Election", href)
    }
    return list(found)


def election_pages() -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for year in year_pages():
        page = raw.cached(SOURCE, year).path.read_text(errors="replace")
        for href, label in anchors(page):
            if re.match(re.escape(SITE) + r"/\d{3,4}/", href) and "Election-Results" not in href and DATE.search(label):
                found[href] = label
    return list(found.items())


def documents() -> Iterator[tuple[str, str, str]]:
    seen: set[str] = set()
    for page_url, label in election_pages():
        page = raw.cached(SOURCE, page_url).path.read_text(errors="replace")
        found = DATE.search(label)
        day = datetime.strptime(f"{found['month'][:3]} {found['day']} {found['year']}", "%b %d %Y").date().isoformat()
        for href, text in anchors(page):
            key = re.sub(r"(/DocumentCenter/View/\d+).*", r"\1", href)
            if "/DocumentCenter/View/" in href and DOCUMENT.search(text) and key not in seen:
                seen.add(key)
                yield day, label, href


def group_detail(text: str) -> Iterator[dict[str, Any]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        vote_for = re.match(r"^VOTE FOR (\d+)\s*$", line.strip())
        if vote_for is None:
            continue
        title = next((other.strip() for other in reversed(lines[:index]) if other.strip()), "")
        choices: list[dict[str, Any]] = []
        for other in lines[index + 1 : index + 40]:
            found = re.match(r"^\s*(.+?)[ .]{2,}([\d,]+)\s+[\d.]+\s", other)
            total = re.match(r"^\s*Total[ .]+([\d,]+)", other)
            if total:
                if choices:
                    yield {
                        "text": title,
                        "vote_for": int(vote_for[1]),
                        "choices": choices,
                        "cast": int(total[1].replace(",", "")),
                    }
                break
            if found and not other.strip().startswith(("(", "WITH")):
                choices.append(
                    {
                        "text": re.sub(r"[ .]+$", "", found[1]).strip(),
                        "party": None,
                        "total": found[2].replace(",", ""),
                        "places": [],
                    }
                )


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for year in year_pages():
        raw.fetch(SOURCE, year, suffix=".html")
    for page_url, _ in election_pages():
        raw.fetch(SOURCE, page_url, suffix=".html")
    for _, _, href in documents():
        raw.fetch(SOURCE, href, suffix=".pdf")


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for tenant in registry:
        for day, label, href in documents():
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, href).path), "-"], capture_output=True, check=True
            ).stdout.decode(errors="replace")
            nodes = []
            parsed = list(tx_collin.electionware(re.sub(r"(?m)^(Vote For \d+)\s*$", r"  \1", text))) or list(
                group_detail(text)
            )
            for node in parsed:
                for choice in node["choices"]:
                    choice["text"] = re.sub(r"^(?:REP|DEM|LIB|GRN|IND|NPA|UNA)\s+", "", choice["text"])
                node["text"] = tx_harris.normalize(tx_collin.entity_first(PORTION.sub("", node["text"])))
                total = sum(int(choice["total"]) for choice in node["choices"])
                if tx_harris.COUNTY_OFFICE.search(node["text"]):
                    continue
                if total != node["cast"]:
                    print(f"{day} {node['text']}: candidates add to {total}, printed {node['cast']}; skipped")
                    continue
                nodes.append({**node, "key": str(len(nodes))})
            yield {
                "tenant": tenant,
                "election": f"{day}-{re.search(r'/View/(\d+)', href)[1]}",
                "day": datetime.fromisoformat(day).date(),
                "title": label,
                "official": "Official" in text[:3000] and "Unofficial" not in text[:3000],
                "primary": False,
                "contests": nodes,
            }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
