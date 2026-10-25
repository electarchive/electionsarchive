import html
import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ea import raw
from ea.sources.us import enr_clarity, tx_harris

SOURCE = "us.tx_collin"
VERSION = "1"
PLATFORM = "tx_collin"
SITE = "https://www.collincountytx.gov"
LISTING = SITE + "/elections/election-results-archive"
FIPS = "48"
FILE = re.compile(
    r"/docs/default-source/elections/results-archive/(?P<month>[a-z]+)-(?P<day>\d+)-(?P<year>\d{4})-(?P<rest>[^/?\"]*combined[^/?\"]*accumulated[^/?\"]*)\.pdf"
)
ENTITY = re.compile(r"(?i)\b(?:city|town|village) of\b|\bISD\b|\bschool district\b|\bcollege\b|\bdistrict\b|\bMUD\b|\btownship\b")
PAIR = re.compile(r"(?<![\d,.])([\d,]+)\s+([\d.]+)\s?%")


def files() -> Iterator[tuple[str, str, str]]:
    listing = raw.cached(SOURCE, LISTING).path.read_text(errors="replace")
    seen: set[str] = set()
    for link in re.findall(r'href="([^"]*)"', listing):
        link = html.unescape(link)
        found = FILE.search(link)
        if found is None or "primary" in link:
            continue
        day = datetime.strptime(f"{found['month']} {found['day']} {found['year']}", "%B %d %Y").date().isoformat()
        if day in seen:
            continue
        seen.add(day)
        yield day, found["rest"], urljoin(SITE, link)


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for _, _, link in files():
        raw.fetch(SOURCE, link, suffix=".pdf")


def entity_first(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("–", " - ")).strip()
    found = re.match(r"^(.+?), (City|Town|Village) of - (.+)$", text)
    if found:
        text = f"{found[2]} of {found[1]}, {found[3]}"
    elif " - " in text:
        left, _, right = text.partition(" - ")
        if ENTITY.search(left) and not ENTITY.search(right):
            text = f"{left}, {right}"
        elif ENTITY.search(right) and not ENTITY.search(left):
            text = f"{right}, {left}"
    if not ENTITY.search(text) and re.search(r"(?i)trustee|school board", text):
        text += " ISD"
    words = text.split()
    if len(words) > 3 and words[-1].lower() == words[0].lower():
        text = " ".join(words[:-1])
    return text


def electionware(text: str) -> Iterator[dict[str, Any]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^\s+Vote For (\d+)\s*$", line) or index == 0:
            continue
        vote_for = int(re.search(r"\d+", line)[0])
        title = lines[index - 1].strip()
        choices: list[dict[str, Any]] = []
        cast = None
        for other in lines[index + 1 : index + 40]:
            stripped = other.strip()
            if stripped.startswith("Total Votes Cast"):
                pair = PAIR.search(other)
                cast = int(pair[1].replace(",", "")) if pair else None
                break
            if not stripped or stripped.startswith(("TOTAL", "Day", "Mail", "Voting", "Election")):
                continue
            pair = PAIR.search(other)
            if pair is None:
                continue
            name = re.sub(r"\s+", " ", other[: pair.start()]).strip()
            if name:
                choices.append({"text": name, "party": None, "total": pair[1].replace(",", ""), "places": []})
        if cast is not None and choices:
            yield {"text": title, "vote_for": vote_for, "choices": choices, "cast": cast}


def gems(text: str) -> Iterator[dict[str, Any]]:
    for page in text.split("\f"):
        lines = page.splitlines()
        split = None
        for line in lines:
            first = line.find("Number of Precincts")
            second = line.find("Number of Precincts", first + 1) if first >= 0 else -1
            if second > 0:
                split = second - 2
                break
        columns = [[line[:split] for line in lines], [line[split:] for line in lines]] if split else [lines]
        for column in columns:
            node: dict[str, Any] | None = None
            for index, line in enumerate(column):
                if line[:1].strip() and not line.startswith(("Registered", "Summary", "General", "Early", "Election", "Collin", "Combined")) and index + 1 < len(column) and column[index + 1].strip().startswith("Total"):
                    node = {"text": re.sub(r"\s+\(\d+\)\s*$", "", line.strip()), "vote_for": 1, "choices": [], "cast": None}
                    continue
                if node is None:
                    continue
                stripped = line.strip()
                if stripped.startswith("Vote For"):
                    node["vote_for"] = int(re.search(r"\d+", stripped)[0])
                elif stripped.startswith("Total Votes"):
                    node["cast"] = int(re.search(r"\d+", stripped)[0])
                elif stripped.startswith(("Number", "Precincts", "Times", "Total")) or not stripped:
                    if not stripped and node["choices"]:
                        yield node
                        node = None
                elif node["cast"] is not None:
                    pair = PAIR.search(line)
                    if pair:
                        name = re.sub(r"\s+", " ", line[: pair.start()]).strip()
                        node["choices"].append({"text": name, "party": None, "total": pair[1].replace(",", ""), "places": []})
            if node is not None and node["choices"]:
                yield node


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for tenant in registry:
        for day, rest, link in files():
            path = raw.cached(SOURCE, link).path
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode(
                errors="replace"
            )
            parser = electionware if "Electionware" in text or "Vote For" in text[:6000] and "Summary Results Report" in text[:300] else gems
            nodes = []
            for node in parser(text):
                node["text"] = tx_harris.normalize(entity_first(node["text"]))
                total = sum(int(choice["total"]) for choice in node["choices"])
                if tx_harris.COUNTY_OFFICE.search(node["text"]):
                    continue
                if total != node["cast"]:
                    print(f"{day} {node['text']}: candidates add to {total}, printed {node['cast']}; skipped")
                    continue
                nodes.append({**node, "key": str(len(nodes))})
            yield {
                "tenant": tenant,
                "election": day,
                "day": datetime.fromisoformat(day).date(),
                "title": re.sub(r"(?i)\s*and special\b", "", rest.replace("-", " ")),
                "official": "unofficial" not in rest and "Unofficial" not in text[:3000],
                "primary": False,
                "contests": nodes,
            }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
