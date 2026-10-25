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
PARTIES = {"DEM", "REP", "LIB", "GRE", "GRN", "IND", "CON", "NON", "SOC", "SWP", "NPA", "REF", "WRI"}
SINGLE = re.compile(r"(?i)\b(?:mayor|auditor|treasurer|clerk|president of council|city attorney|solicitor|prosecutor|sheriff|recorder|engineer|coroner|governor|attorney general|secretary of state)\b")


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
        plain = [link for link in found if not re.search(r"(?i)group|detail", link)]
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
        voting = sum(1 for entry in votes if sum(entry))
        vote_for = max(ratios) if ratios and ratios.count(max(ratios)) >= min(2, voting) else 0
        if vote_for == 0 and re.search(r"\(\d{1,2}-\d{1,2}-\d{2}\)", title):
            vote_for = 1
        if not any(sum(entry) for entry in votes):
            continue
        inside = [
            n
            for n, row in enumerate(body)
            if sum(votes[n]) + sum(int(float(row[column] or 0)) for column in (over, under) if column is not None) > 0
        ]
        choices = []
        for index, column in enumerate(candidates):
            name = html.unescape(str(rows[2][column])).strip()
            found = PARTY.match(name)
            choices.append(
                {
                    "text": found[2] if found else name,
                    "party": found[1].upper() if found else None,
                    "total": str(sum(entry[index] for entry in votes)),
                    "places": [(str(body[n][2]).strip(), str(votes[n][index])) for n in inside],
                }
            )
        yield {"text": PLACE.sub(r"\2 \1", re.sub(r"^For ", "", title)), "vote_for": vote_for, "choices": choices}


def legacy(rows: list[list[Any]]) -> Iterator[dict[str, Any]]:
    def integer(cell: Any) -> int:
        try:
            return int(float(str(cell).replace(",", "")))
        except ValueError:
            return 0

    def number(cell: Any) -> float | None:
        try:
            return float(str(cell).replace(",", ""))
        except ValueError:
            return None

    start = next(
        (
            n
            for n in range(1, min(len(rows), 12))
            if sum(1 for cell in rows[n][3:60] if number(cell) is not None) >= 5
            and not any(str(cell).strip().upper() in ("PRECINCT NAME", "TOTAL VOTE") for cell in rows[n][:30])
        ),
        None,
    )
    if start is None:
        return
    head = rows[:start]
    width = len(rows[0])
    labels = [[str(row[c]).strip() if row[c] not in (None, "") else "" for row in head] for c in range(width)]
    name_col = next((c for c in range(min(width, 30)) if any(v.upper() == "PRECINCT NAME" for v in labels[c])), None)
    if name_col is None:
        return
    sub = next((n for n, row in enumerate(head) if sum(1 for c in range(width) if str(row[c]).strip().upper() in ("TOTAL", "TOTAL VOTE")) >= 3), None)

    def row_party(n: int) -> bool:
        cells = [str(head[n][c]).strip().upper() for c in range(name_col + 1, width) if head[n][c] not in (None, "")]
        return len(cells) > 3 and sum(1 for cell in cells if cell in PARTIES or cell == "WRITE-IN") >= 0.8 * len(cells)

    parties = next((n for n in range(len(head)) if n != sub and row_party(n)), None)
    contested = [n for n in range(len(head)) if n not in (sub, parties)]
    def count(n: int) -> int:
        return sum(1 for c in range(name_col + 1, width) if head[n][c] not in (None, ""))
    names = max(contested, key=lambda n: (sum(1 for c in range(name_col + 1, width) if str(head[n][c]).strip().upper() in FILLER | {"RACE TOTAL"}), count(n)), default=None)
    titles = next((n for n in contested if n != names and count(n) > 0 and max((len(str(head[n][c])) for c in range(width) if head[n][c] not in (None, "")), default=0) > 14), None)
    if names is None or titles is None:
        return
    meta = re.compile(r"(?i)registered|ballots|voters|precinct|\bsos\b|total|location|district no|ward")
    first = next(
        (c for c in range(name_col + 1, width) if head[titles][c] not in (None, "") and not meta.search(str(head[titles][c]))),
        width,
    )
    cells = [c for c in range(first, width) if head[names][c] not in (None, "")]
    ballots_labels = [c for c in range(width) if re.search(r"(?i)^\s*(?:total voters|ballots cast(?: -)? ?total|ballots cast)\s*$", " ".join(labels[c][:3]))]
    ballots = ballots_labels[0] if ballots_labels else None
    def block_total(c: int, end: int) -> int:
        if sub is None:
            return c
        return next(
            (k for k in range(c, end) if str(head[sub][k]).strip().upper() in ("TOTAL", "TOTAL VOTE")),
            c,
        )
    if ballots is not None and sub is not None:
        nxt = next((c for c in range(ballots + 1, width) if head[names][c] not in (None, "") or head[titles][c] not in (None, "")), width)
        ballots = block_total(ballots, nxt)
    candidates = []
    for index, c in enumerate(cells):
        label = str(head[names][c]).strip()
        if re.search(r"(?i)ballots cast|registered|voters", label) or label.upper() == "RACE TOTAL":
            continue
        end = cells[index + 1] if index + 1 < len(cells) else width
        candidates.append((c, label, block_total(c, end)))
    real = [c for c, label, _ in candidates if label.upper() not in FILLER]
    repeating = sum(1 for c in real if head[titles][c] not in (None, "")) >= 0.8 * max(len(real), 1)
    current = ""
    groups: list[list[tuple[int, str, int]]] = []
    previous_filler = True
    title_at: dict[int, str] = {}
    for c, label, total in candidates:
        cell = str(head[titles][c]).strip() if head[titles][c] not in (None, "") else ""
        if repeating:
            fresh = previous_filler or (cell != "" and cell != current)
        else:
            fresh = cell != "" or not groups
        if cell:
            current = cell
        if fresh:
            groups.append([])
        title_at[len(groups) - 1] = current
        groups[-1].append((c, label, total))
        previous_filler = label.upper() in FILLER
    merged: dict[str, list[Any]] = {}
    for row in rows[start:]:
        name = str(row[name_col]).strip() if row[name_col] not in (None, "") else ""
        if not name or re.search(r"(?i)total|overlap|filtered|^none$", name):
            continue
        if name not in merged:
            merged[name] = [number(cell) or 0 if number(cell) is not None else cell for cell in row]
        else:
            for n in range(len(row)):
                if isinstance(merged[name][n], float | int) and number(row[n]) is not None:
                    merged[name][n] += number(row[n])
    body = list(merged.values())
    for index, group in enumerate(groups):
        people = [(c, label, total) for c, label, total in group if label.upper() not in FILLER]
        if not people:
            continue
        votes = [[integer(body_row[total]) for _, _, total in people] for body_row in body]
        if not any(sum(entry) for entry in votes):
            continue
        over = next((total for _, label, total in group if label.upper() == "OVER VOTES"), None)
        under = next((total for _, label, total in group if label.upper() == "UNDER VOTES"), None)
        vote_for = 0
        if over is not None and under is not None and ballots is not None:
            ratios = []
            for n, body_row in enumerate(body):
                cast = integer(body_row[ballots])
                if cast > 0 and integer(body_row[over]) == 0:
                    full, remainder = divmod(sum(votes[n]) + integer(body_row[under]), cast)
                    if remainder == 0 and full > 0:
                        ratios.append(full)
            voting = sum(1 for entry in votes if sum(entry))
            vote_for = max(ratios) if ratios and ratios.count(max(ratios)) >= min(2, voting) else 0
        title = re.sub(r"\s+", " ", title_at[index]).strip()
        seats = re.search(r"(?i)\(?vote for (\d+)\)?", title)
        if seats:
            vote_for = vote_for or int(seats[1])
            title = re.sub(r"\s+", " ", re.sub(r"(?i)\(?vote for \d+\)?", " ", title)).strip()
        if vote_for == 0 and (SINGLE.search(title) or re.search(r"\(\d{1,2}-\d{1,2}-\d{2}\)", title)):
            vote_for = 1
        inside = [
            n
            for n, body_row in enumerate(body)
            if sum(votes[n]) + sum(integer(body_row[k]) for k in (over, under) if k is not None) > 0
        ]
        choices = []
        for position, (c, label, total) in enumerate(people):
            plain = html.unescape(label).strip()
            plain = re.sub(r"\s+", " ", plain)
            found = PARTY.match(plain)
            code = (
                found[1].upper()
                if found
                else str(head[parties][c]).strip().upper()
                if parties is not None and head[parties][c] not in (None, "")
                else None
            )
            if re.match(r"(?i)write.?in", plain):
                plain, code = "Write-ins", None
            elif found:
                plain = found[2]
            choices.append(
                {
                    "text": plain,
                    "party": None if code in (None, "NON", "NPA", "WRI") else code,
                    "total": str(sum(entry[position] for entry in votes)),
                    "places": [(str(merged_name), str(votes[n][position])) for n, merged_name in enumerate(merged) if n in inside],
                }
            )
        text = re.sub(r"^For ", "", title.replace(" - ", " "))
        text = re.sub(r"^(\S+(?: \S+)?) For ", r"\1 ", text)
        yield {"text": PLACE.sub(r"\2 \1", text), "vote_for": vote_for, "choices": choices}


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    for year, link in files():
        book = fastexcel.read_excel(raw.cached(SOURCE, link).path.read_bytes())
        rows = [list(row) for row in book.load_sheet(0, header_row=None).to_polars().iter_rows()]
        nodes = [{**node, "key": str(n)} for n, node in enumerate(wide(rows) if rows and str(rows[0][0]).strip() == "COUNTY NUMBER" else legacy(rows))]
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
