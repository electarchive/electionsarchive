import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ea import raw
from ea.sources.us import enr_clarity, kceb, wyco_ks

SOURCE = "us.stlco_old"
VERSION = "2"
PLATFORM = "mo_stlco_old"
ROOT = "https://extcontent.stlouisco.com/BOE/eResults/"
STATE = re.compile(
    r"(?i)^(?:u\.?s\.?|state|st\.?|president|governor|lieutenant|secretary|attorney general|treasurer|auditor|amendment|"
    r"proposition [a-z0-9]+ *$)|\b(?:supreme court|court of appeals|circuit judge|associate circuit|circuit court|appeals)\b|"
    r"\b(?:statutory|constitutional)\b"
)


def _codes() -> list[str]:
    page = raw.cached(SOURCE, ROOT).path.read_text(errors="replace")
    return sorted(
        code for code in set(re.findall(r"eResults/(el\d{6})/", page)) if code[2:4] > "20" or code[2:4] < "20"
    )


def _url(code: str) -> str:
    return f"{ROOT}{code}/Results.pdf"


def fetch() -> None:
    raw.fetch(SOURCE, ROOT, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips="29", layer=layer), suffix=".zip")
    for code in _codes():
        try:
            raw.fetch(SOURCE, _url(code), suffix=".pdf")
        except httpx.HTTPError as error:
            print(f"{code}: {str(error).splitlines()[0]}")


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for code in _codes():
        try:
            path = raw.cached(SOURCE, _url(code)).path
        except Exception:
            continue
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True).stdout.decode(
            errors="replace"
        )
        head = text[:1500]
        found = kceb.DATE.search(head)
        if not found or "SUMMARY REPORT" not in head.upper():
            print(f"{code}: no text layer or unknown layout; skipped")
            continue
        day = datetime.strptime(f"{found[1].title()} {found[2]} {found[3]}", "%B %d %Y").date()
        title = (
            re.sub(
                r"\s+",
                " ",
                re.sub(r"(?i)summary report|official final results|official results", " ", head.splitlines()[0]),
            ).strip()
            or "Election"
        )
        nodes = []
        seen: set[str] = set()
        parts = re.split(r"\*{5,}\s*\((\w+)\)\s*\*{5,}", text)
        sections = [(None, parts[0])] + [(parts[index], parts[index + 1]) for index in range(1, len(parts) - 1, 2)]
        for section, body in sections:
            if section is not None and section.upper() not in ("DEMOCRATIC", "REPUBLICAN", "NONPARTISAN"):
                continue
            for node in wyco_ks.dotted(body, joined=True):
                label = re.sub(r"\*+", "", node["text"]).strip(" -")
                if STATE.search(label):
                    continue
                if section is not None and section.upper() != "NONPARTISAN":
                    label = f"{label} {section.upper()}"
                if label in seen:
                    continue
                seen.add(label)
                choices = []
                for choice in node["choices"]:
                    text = choice["text"]
                    if re.match(r"(?i)invalid write", text):
                        choices.append({**choice, "text": "Write-ins"})
                        continue
                    rest = re.sub(r"(?i)(?:\s*\b(?:w/i|write-?in|votes?|of|0f|each)\b)+\s*$", "", text)
                    pairs = re.findall(r"([A-Za-z][A-Za-z.' ,&()-]*?)\s*(?:\(\s*|-|\s)(\d[\d,]*)\)?(?=[\s,&]|$)", rest)
                    if not pairs or rest == text and not re.fullmatch(r"(?:[A-Za-z' -]+-\d+,?\s*)+", text):
                        choices.append(choice)
                        continue
                    named = []
                    for names, count in pairs:
                        people = [part.strip(" ,.") for part in re.split(r"\s*&\s*", names.strip(" ,&"))]
                        named += [(person, int(count.replace(",", ""))) for person in people if person]
                    rest_votes = int(choice["total"]) - sum(count for _, count in named)
                    choices += [
                        {**choice, "text": person, "party": None, "total": str(count)} for person, count in named
                    ]
                    if rest_votes > 0:
                        choices.append({**choice, "text": "Write-ins", "party": None, "total": str(rest_votes)})
                nodes.append({**node, "choices": choices, "text": label, "key": str(len(nodes))})
        yield {
            "tenant": tenant,
            "election": code,
            "day": day,
            "title": title,
            "official": True,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: True)
