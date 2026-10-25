import html
import re
from collections.abc import Iterator, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import fastexcel

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.oh_franklin"
VERSION = "1"
PLATFORM = "oh_franklin"
SITE = "https://vote.franklincountyohio.gov"
ARCHIVES = SITE + "/election-info/archives"
FIPS = "39"
YEARS = range(2000, 2026)
SECTION = re.compile(r">\s*((?:Primary|General|Special)[A-Za-z ]*Election)\s*<")
LINK = re.compile(r'<a[^>]*href="(/getmedia/[^"]+)"[^>]*>(.*?)</a>', re.S)
UNWANTED = re.compile(
    r"(?i)nvra|maintenance|registration|absentee|readiness|most.?populous|pickerington|recount|summary|overlap"
)
PARTY = re.compile(r"^(Dem|Rep|Lib|Grn|Ind|Con)\s+(.+)$")
PLACE = re.compile(r"^(.*?) ((?:City|Village|Township) of .+)$")
FILLER = {"OVER VOTES", "UNDER VOTES"}


def page(year: int) -> str:
    return f"{SITE}/Election-Info/{year}"


def files() -> Iterator[tuple[int, str]]:
    for year in YEARS:
        text = raw.cached(SOURCE, page(year)).path.read_text(errors="replace")
        section = ""
        found: list[str] = []
        for token in sorted(
            [(m.start(), "section", m[1]) for m in SECTION.finditer(text)]
            + [(m.start(), "link", m) for m in LINK.finditer(text)],
            key=lambda entry: entry[0],
        ):
            if token[1] == "section":
                section = token[2]
                continue
            label = html.unescape(re.sub(r"<[^>]+>|\s+", " ", token[2][2])).strip()
            if (
                section.startswith("General")
                and re.search(r"(?i)\((?:xlsx?)\)", label)
                and not UNWANTED.search(label + token[2][1])
                and not re.search(r"(?i)primary", label + token[2][1])
            ):
                found.append(SITE + token[2][1])
        plain = [link for link in found if "group" not in link.lower()]
        for link in plain or found:
            yield year, link


def fetch() -> None:
    raw.fetch(SOURCE, ARCHIVES, suffix=".html")
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    for year in YEARS:
        raw.fetch(SOURCE, page(year), suffix=".html")
    for _, link in files():
        raw.fetch(SOURCE, link, suffix=".xlsx")


def wide(rows: list[list[Any]]) -> Iterator[dict[str, Any]]:
    if not rows or str(rows[0][0]).strip() != "COUNTY NUMBER":
        return
    merged: dict[str, list[Any]] = {}
    for row in rows[4:]:
        name = str(row[2]).strip() if row[2] else ""
        if not name or name.upper().startswith("COUNTY TOTAL"):
            continue
        if name not in merged:
            merged[name] = [float(cell or 0) if n >= 3 else cell for n, cell in enumerate(row)]
        else:
            for n in range(3, len(row)):
                merged[name][n] += float(row[n] or 0)
    body = list(merged.values())
    ballots = [int(float(row[4] or 0)) for row in body]
    groups: dict[str, list[int]] = {}
    for column in range(6, len(rows[0])):
        groups.setdefault(str(rows[0][column]).strip(), []).append(column)
    for title, columns in groups.items():
        candidates = [column for column in columns if str(rows[2][column]).strip().upper() not in FILLER]
        over = next((column for column in columns if str(rows[2][column]).strip().upper() == "OVER VOTES"), None)
        under = next((column for column in columns if str(rows[2][column]).strip().upper() == "UNDER VOTES"), None)
        votes = [[int(float(row[column] or 0)) for column in candidates] for row in body]
        ratios = []
        if over is not None and under is not None:
            for n, row in enumerate(body):
                if ballots[n] > 0 and int(float(row[over] or 0)) == 0:
                    full, remainder = divmod(sum(votes[n]) + int(float(row[under] or 0)), ballots[n])
                    if remainder == 0 and full > 0:
                        ratios.append(full)
        vote_for = max(ratios) if ratios.count(max(ratios, default=0)) >= 2 else 0
        if vote_for == 0 and re.search(r"\(\d{1,2}-\d{1,2}-\d{2}\)", title):
            vote_for = 1
        if not any(sum(entry) for entry in votes):
            continue
        choices = []
        for index, column in enumerate(candidates):
            name = html.unescape(str(rows[2][column])).strip()
            found = PARTY.match(name)
            choices.append(
                {
                    "text": found[2] if found else name,
                    "party": found[1].upper() if found else None,
                    "total": str(sum(entry[index] for entry in votes)),
                    "places": [(str(row[2]).strip(), str(votes[n][index])) for n, row in enumerate(body)],
                }
            )
        yield {"text": PLACE.sub(r"\2 \1", re.sub(r"^For ", "", title)), "vote_for": vote_for, "choices": choices}


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for year, link in files():
        book = fastexcel.read_excel(raw.cached(SOURCE, link).path.read_bytes())
        rows = [list(row) for row in book.load_sheet(0, header_row=None).to_polars().iter_rows()]
        nodes = [{**node, "key": str(n)} for n, node in enumerate(wide(rows))]
        if not nodes:
            print(f"{year} {link[-50:]}: layout not read")
            continue
        first = date(year, 11, 1)
        day = first + timedelta(days=(7 - first.weekday()) % 7 + 1)
        yield {
            "tenant": tenant,
            "election": f"{year}:{link[-40:]}",
            "day": day,
            "title": f"{year} general election",
            "official": True,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(SITE))
