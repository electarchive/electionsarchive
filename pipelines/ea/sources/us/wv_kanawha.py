import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.wv_kanawha"
VERSION = "1"
PLATFORM = "wv_kanawha"
FIPS = "54"
UPLOADS = "https://kanawha.us/wp-content/uploads/"
FILES = {
    "2022/11/Generall-2022-Election-Summary-1.pdf": (date(2022, 11, 8), False, False),
    "2026/05/OFFICIAL-2026-PRIMARY-SUMMARY.pdf": (date(2026, 5, 12), True, True),
}
MUNICIPAL = re.compile(r"^(?:(?P<party>DEM|REP) )?FOR (?P<office>.+?) (?P<place>(?:CITY|TOWN|VILLAGE) OF [A-Z .'-]+)$")
WARD = re.compile(r"^(?:(?P<party>DEM|REP) )?FOR (?P<office>CITY COUNCIL) (?P<place>CHARLESTON) (?P<ward>WARD \d+)$")
CHOICE = re.compile(r"^(?:(?P<party>DEM|REP|LBN|IND|MTN|ACN|GRN) )?(?P<name>\S.*?)\s{2,}(?P<votes>[\d,]+)\s+[\d.]+%")


def fetch() -> None:
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for name in FILES:
        raw.fetch(SOURCE, UPLOADS + name, suffix=".pdf")


def summary(text: str) -> Iterator[dict[str, Any]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        found = re.match(r"^\s*Vote For (\d+)\s*$", line)
        if not found or index == 0:
            continue
        heading = re.sub(r"\s+", " ", lines[index - 1]).strip()
        if ward := WARD.match(heading):
            party, title = ward["party"], f"City of Charleston, {ward['office'].title()} {ward['ward'].title()}"
        elif municipal := MUNICIPAL.match(heading):
            office = municipal["office"].removesuffix(" -").title()
            party, title = municipal["party"], f"{municipal['place'].title().replace(' Of ', ' of ')}, {office}"
        else:
            continue
        choices: list[dict[str, Any]] = []
        cast = None
        for other in lines[index + 1 : index + 60]:
            if other.startswith("Total Votes Cast"):
                cast = int(re.search(r"[\d,]+", other[16:])[0].replace(",", ""))
                break
            if not other.strip():
                if choices and cast is None and "Total Votes Cast" not in text:
                    break
                continue
            if other[0].isspace():
                continue
            if choice := CHOICE.match(other):
                choices.append(
                    {
                        "text": choice["name"].strip(),
                        "party": choice["party"],
                        "total": choice["votes"].replace(",", ""),
                        "places": [],
                    }
                )
        if choices:
            yield {
                "text": f"{party} {title}" if party else title,
                "vote_for": int(found[1]),
                "choices": choices,
                "cast": cast,
            }


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for tenant in registry:
        for name, (day, official, primary) in FILES.items():
            path = raw.cached(SOURCE, UPLOADS + name).path
            text = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True
            ).stdout.decode(errors="replace")
            nodes = []
            for node in summary(text):
                total = sum(int(choice["total"]) for choice in node["choices"])
                if node["cast"] is not None and total != node["cast"]:
                    print(f"{day} {node['text']}: candidates add to {total}, printed {node['cast']}; skipped")
                    continue
                nodes.append({**node, "key": str(len(nodes))})
            yield {
                "tenant": tenant,
                "election": name,
                "day": day,
                "title": "Primary election" if primary else "General election",
                "official": official,
                "primary": primary,
                "contests": nodes,
            }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
