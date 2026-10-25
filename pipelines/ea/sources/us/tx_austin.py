import html
import re
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.tx_austin"
VERSION = "1"
PLATFORM = "tx_austin"
PAGE = "https://services.austintexas.gov/election/byrecord.cfm?eid={}"
FIPS = "48"
EIDS = range(1, 300)


def cells(fragment: str) -> list[str]:
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", fragment, flags=re.S)]


def fetch() -> None:
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for eid in EIDS:
        raw.fetch(SOURCE, PAGE.format(eid), suffix=".html")


def contests(page: str, kind: str) -> Iterator[dict[str, Any]]:
    tables = re.findall(r"<table.*?</table>", page, flags=re.S)
    ballots = 0
    for table in tables[1:]:
        rows = [cells(row) for row in re.findall(r"<tr.*?</tr>", table, flags=re.S)]
        rows = [row for row in rows if row]
        if not rows:
            continue
        if len(rows[0]) == 1 and len(rows) >= 3 and rows[1][:1] == ["Yes"]:
            ballots += 1
            heading = re.sub(r"(?i)\bprop[o]?sition\b", "Proposition", rows[0][0])
            found = re.match(r"(?i)(?:proposition|ordinance)(?: no\.?)? ?(\w*)", heading)
            label = f"Proposition {found[1].lstrip('0') or found[1]}".strip() if found and found[1] else f"Proposition {ballots}"
            votes = [value for value in rows[2] if re.fullmatch(r"[\d,]+", value)]
            if len(votes) != 2:
                print(f"{heading}: no yes and no totals; skipped")
                continue
            yield {
                "text": f"City of Austin, {label}",
                "vote_for": 1,
                "choices": [
                    {"text": "Yes", "party": None, "total": votes[0].replace(",", ""), "places": []},
                    {"text": "No", "party": None, "total": votes[1].replace(",", ""), "places": []},
                ],
                "cast": None,
            }
            continue
        title = None
        choices: list[dict[str, Any]] = []
        for row in [*rows, ["", ""]]:
            if len(row) == 1:
                if title is not None and choices:
                    yield {"text": f"City of Austin, {title}", "vote_for": 1, "choices": choices, "cast": None}
                title = row[0] or ("Mayor" if "Mayor" in kind else None)
                choices = []
            elif len(row) >= 3 and title is not None and re.fullmatch(r"[\d,]+", row[1]):
                name = row[0]
                if name.count(",") == 1:
                    last, first = name.split(",")
                    name = f"{first.strip()} {last.strip()}"
                choices.append({"text": name, "party": None, "total": row[1].replace(",", ""), "places": []})


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for tenant in registry:
        for eid in EIDS:
            page = raw.cached(SOURCE, PAGE.format(eid)).path.read_text(errors="replace")
            when = re.search(r"Date of Election:</td>\s*<td>([^<]+)", page)
            if when is None:
                continue
            kind = re.search(r"Type of Election:</td>\s*<td>([^<]+)", page)
            kind = kind[1].strip() if kind else ""
            day = datetime.strptime(when[1].strip(), "%B %d, %Y").date()
            nodes = [{**node, "key": str(index), "whole": True} for index, node in enumerate(contests(page, kind))]
            yield {
                "tenant": tenant,
                "election": str(eid),
                "day": day,
                "title": f"City of Austin {kind.replace(',', ', ').replace('Run-Off', 'Runoff')} election".replace("  ", " "),
                "official": True,
                "primary": False,
                "contests": nodes,
            }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant))
