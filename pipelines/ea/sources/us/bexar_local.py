import html
import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import bexar, enr_clarity, tx_harris

SOURCE = "us.bexar_local"
VERSION = "1"
PLATFORM = "tx_bexar"
FIPS = "48"
ENTITY = re.compile(
    r"^(?P<title>.*?)\s+(?P<entity>(?:CITY|TOWN|VILLAGE) OF [A-Z0-9 .&'-]+|[A-Z0-9 .&'-]+? (?:ISD|I\.S\.D\.|INDEPENDENT SCHOOL DISTRICT|"
    r"COLLEGE|COMMUNITY COLLEGE DISTRICT|HOSPITAL DISTRICT|DISTRICT)(?: [A-Z0-9 .&'-]*)?)$"
)
SKIP = re.compile(r"(?i)^city of san antonio\b|^for city of san antonio\b|\bcity of san antonio,")


def fetch() -> None:
    raw.fetch(SOURCE, bexar.LISTING, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for doc in bexar.FILES:
        raw.fetch(SOURCE, bexar.FILE.format(doc), suffix=".bin")


def keyed(text: str) -> Iterator[dict[str, Any]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^\s*VOTE FOR\s+(\d+)\s*$", line) or index == 0:
            continue
        title = lines[index - 1].strip()
        choices: list[dict[str, Any]] = []
        over = 0
        for other in lines[index + 1 : index + 30]:
            if not re.search(r"\d{2} = ", other):
                if choices:
                    break
                continue
            for _, name, votes, _ in bexar.CANDIDATE.findall(other):
                name = name.strip()
                if re.fullmatch(r"OVER VOTES|UNDER VOTES", name, re.I):
                    continue
                choices.append({"text": name, "party": None, "total": votes.replace(",", ""), "places": []})
        if choices:
            yield {"text": title, "vote_for": int(re.search(r"\d+", line)[0]), "choices": choices, "cast": None}


def electionware(text: str) -> Iterator[dict[str, Any]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^\s+Vote For (\d+)\s*$", line) or index == 0:
            continue
        title = re.sub(r"^For\s+", "", lines[index - 1].strip())
        choices: list[dict[str, Any]] = []
        cast = None
        for other in lines[index + 1 : index + 40]:
            stripped = other.strip()
            pair = bexar.TALLY.match(other)
            if stripped.startswith("Total Votes Cast"):
                cast = int(pair[2].replace(",", "")) if pair else None
                break
            if pair and not stripped.startswith(("TOTAL", "Day", "Voting")):
                choices.append({"text": pair[1].strip(), "party": None, "total": pair[2].replace(",", ""), "places": []})
        if choices and cast is not None:
            yield {"text": title, "vote_for": int(re.search(r"\d+", line)[0]), "choices": choices, "cast": cast}


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for tenant in registry:
        for doc, day in bexar.FILES.items():
            path = raw.cached(SOURCE, bexar.FILE.format(doc)).path
            content = path.read_bytes()
            if content.startswith(b"%PDF"):
                text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode(
                    errors="replace"
                )
                parser = electionware
            else:
                text = html.unescape(re.sub(r"<[^>]+>", "", content.decode("latin-1")))
                parser = keyed
            nodes = []
            seen: set[str] = set()
            for node in parser(text):
                found = ENTITY.match(node["text"]) if parser is keyed else None
                heading = f"{found['entity'].title()}, {found['title']}" if found else node["text"]
                node["text"] = tx_harris.normalize(heading)
                if SKIP.search(node["text"]) or tx_harris.COUNTY_OFFICE.search(node["text"]) or node["text"] in seen:
                    continue
                total = sum(int(choice["total"]) for choice in node["choices"])
                if node["cast"] is not None and total != node["cast"]:
                    print(f"{day} {node['text']}: candidates add to {total}, printed {node['cast']}; skipped")
                    continue
                seen.add(node["text"])
                nodes.append({**node, "key": str(len(nodes))})
            yield {
                "tenant": tenant,
                "election": str(doc),
                "day": day,
                "title": "Joint runoff election" if day.month == 6 else "Joint election",
                "official": True,
                "primary": False,
                "contests": nodes,
            }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
