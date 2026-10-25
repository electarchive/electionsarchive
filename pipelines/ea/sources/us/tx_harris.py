import html
import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.tx_harris"
VERSION = "1"
PLATFORM = "tx_harris"
ARCHIVE = "https://www.harrisvotes.com/Election-Results/Archives"
PAGE = ARCHIVE + "/page/{}"
PAGES = range(1, 8)
FIPS = "48"
SKIP_ROW = re.compile(r"(?i)primary|provisional|recount|montgomery|\bdem\b|\brep\b")
SKIP_CONTEST = re.compile(
    r"(?i)^proposition\b|^city of houston\b|^harris county (?!(?:mud|m\.u\.d|municipal|utility|emergency|esd|hospital|flood|"
    r"department of education|water|improvement|fresh|conservation|drainage))"
)
COUNTY_OFFICE = re.compile(
    r"(?i)\bjudge\b.*\bcourt\b|\bcounty (?:clerk|judge|tax|commissioner|treasurer|surveyor|attorney|sheriff|school)|"
    r"\bdistrict (?:clerk|attorney)\b|\bconstable\b|\bjustice of the peace\b|\bsheriff\b|\bhcad\b|"
    r"^(?:mayor|council ?(?:member|man)\b.*|councilman.*|trustee.*)$|^city controller|(?:precinct|county|party) chair|flood control|^harris county(?! mud)|woodlands township|\bgccisd"
)
ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7", "VIII": "8", "IX": "9"}
NUMBER_WORDS = {"none": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
HEADING = re.compile(r"^(?P<title>\S.*?)(?:,|\s-)?\s+Vote [Ff]or\s+(?P<n>.+?)\s*$")
PAIR = re.compile(r"(?<![\d,.])([\d,]+)\s+([\d.]+)%")
PAGE_LINE = re.compile(r"\bPage \d+( of \d+)?\s*$")
CAST = re.compile(r"^\s*Cast Votes:")
REMAINDER = re.compile(r"^\s*(?:Over|Under)\s?[Vv]otes:")


def rows() -> Iterator[tuple[date, str, str]]:
    seen: set[str] = set()
    for number in PAGES:
        page = raw.cached(SOURCE, PAGE.format(number)).path.read_text(errors="replace")
        for item in page.split('class="EAL-Item"')[1:]:
            cells = re.findall(r'<div class="EAL-Cell">(.*?)</div>', item, flags=re.S)
            links = [urljoin(ARCHIVE, html.unescape(link)) for link in re.findall(r'href="([^"]*)"', item) if link]
            links = [link for link in links if "Archives/page" not in link and not link.lower().endswith((".htm", ".html"))]
            if len(cells) < 2 or not links:
                continue
            label = html.unescape(re.sub(r"<[^>]+>", "", cells[0])).strip()
            title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cells[1]))).strip()
            if not label or SKIP_ROW.search(title) or links[0] in seen:
                continue
            seen.add(links[0])
            yield datetime.strptime(label, "%B %d, %Y").date(), title, links[0]


def fetch() -> None:
    for number in PAGES:
        raw.fetch(SOURCE, PAGE.format(number), suffix=".html")
    for fips in (FIPS,):
        for layer in enr_clarity.LAYERS:
            raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=fips, layer=layer), suffix=".zip")
    for _, _, link in rows():
        raw.fetch(SOURCE, link, suffix=".pdf")


def contests(text: str) -> Iterator[dict[str, Any]]:
    lines = text.splitlines()
    skipping = False
    node: dict[str, Any] | None = None
    for index, line in enumerate(lines):
        if "\f" in line:
            skipping = True
            line = line.replace("\f", "")
        if skipping:
            if PAGE_LINE.search(line):
                skipping = False
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if node is None:
            heading = HEADING.match(stripped)
            following = next((other.strip() for other in lines[index + 1 :] if other.strip()), "")
            if heading is None and following.startswith("Choice") and not PAIR.search(stripped):
                heading = re.match(r"(?P<title>.+?)\s*$", stripped)
            if heading is None or PAIR.search(stripped) or stripped.startswith(("Choice", "Party")):
                continue
            words = re.findall(r"\d+|none|one|two|three|four|five|six|seven", heading.groupdict().get("n") or "one")
            vote_for = max((int(word) if word.isdigit() else NUMBER_WORDS[word] for word in words), default=1)
            node = {"text": re.sub(r"\s+", " ", heading["title"]).strip(" ,-"), "vote_for": max(vote_for, 1), "choices": []}
            continue
        if CAST.match(line):
            pairs = PAIR.findall(line)
            node["cast"] = int(pairs[-1][0].replace(",", ""))
            yield node
            node = None
            continue
        pairs = list(PAIR.finditer(line))
        if not pairs or stripped.startswith(("Choice", "Party")):
            continue
        name = re.sub(r"\s+", " ", line[: pairs[0].start()]).strip()
        if name:
            node["choices"].append({"text": name, "party": None, "total": pairs[-1][1].replace(",", ""), "places": []})


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for tenant in registry:
        for day, title, link in rows():
            path = raw.cached(SOURCE, link).path
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode(
                errors="replace"
            )
            found = []
            for node in contests(text):
                node["text"] = normalize(node["text"])
                if SKIP_CONTEST.search(node["text"]) or COUNTY_OFFICE.search(node["text"]):
                    continue
                total = sum(int(choice["total"]) for choice in node["choices"])
                if total != node["cast"]:
                    print(f"{day} {node['text']}: candidates add to {total}, printed {node['cast']}; skipped")
                    continue
                found.append({**node, "key": f"{len(found)}"})
            yield {
                "tenant": tenant,
                "election": f"{day}-{path.stem[:8]}",
                "day": day,
                "title": re.sub(r"(?i)\s*and special\b|\bspecial\s+and\b", "", title),
                "official": "Official" in text[:2000],
                "primary": False,
                "contests": found,
            }


def normalize(text: str) -> str:
    text = re.sub(r"\bI\.? ?S\.? ?D\.?(?=[\s,]|$)", "ISD", text, flags=re.I)
    text = re.sub(r"\b(?:HC|H\.C\.) ?MUD\b|\bHCMUD\b", "Harris County MUD", text, flags=re.I)
    text = re.sub(r"\bLSCS\b|\bLone Star College System\b", "Lone Star Community College District", text, flags=re.I)
    text = re.sub(r"\bHCCSN?\b|\bHCC\b|\bHouston Comm(?:unity)? Col(?:lege)? Dist\w*(?: \d+)?", "Houston Community College District", text, flags=re.I)
    text = re.sub(r"\bU D\b", "Utility District", text, flags=re.I)
    text = re.sub(r"\bCity of W University\b", "City of West University", text, flags=re.I)
    text = re.sub(r"\bAlder ?person\b", "Alderman", text, flags=re.I)
    text = re.sub(r"\bREG WA\b", "Regional Water Authority", text, flags=re.I)
    text = re.sub(r"\bDistrict (VIII|VII|VI|IV|IX|III|II|V|I)\b", lambda m: f"District {ROMAN[m[1].upper()]}", text, flags=re.I)
    return text


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
