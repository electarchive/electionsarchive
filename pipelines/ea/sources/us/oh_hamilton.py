import re
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import fastexcel

from ea import raw
from ea.sources.us import enr_clarity
from ea.store import DATA

SOURCE = "us.oh_hamilton"
VERSION = "1"
PLATFORM = "oh_hamilton"
INBOX = DATA / "inbox" / "manual" / "MF-087"
URL = "https://votehamiltoncountyohio.gov/results/#{name}"
FIPS = "39"
FILE = re.compile(r"^(?:General_(?P<long>\d{4})_11|G(?:en)?(?P<short>\d{2})(?!\d))", re.I)
PARTIES = {
    "DEMOCRATIC",
    "REPUBLICAN",
    "GREEN",
    "LIBERTARIAN",
    "INDEPENDENT",
    "NONPARTISAN",
    "NON",
    "WRITE-IN",
    "DEM",
    "REP",
}
SINGLE = re.compile(r"(?i)\b(?:mayor|auditor|treasurer|clerk|president of council|fiscal officer|solicitor|sheriff)\b")
SEATS = re.compile(r"(?i)\(?vote for (\d+|one|two|three|four|five)\)?|\b(\d+) to be e\w*")
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
NOISE = re.compile(r"(?i)\bby (?:ward|district)\b|\b\d+[- ]?years?(?: term)?\b|\s-\s(?=\s|$)")


def files() -> Iterator[tuple[int, str]]:
    for path in sorted(INBOX.iterdir()):
        found = FILE.match(path.name)
        if found is None or path.suffix.lower() not in (".xls", ".xlsx"):
            continue
        year = (
            int(found["long"])
            if found["long"]
            else 2000 + int(found["short"])
            if int(found["short"]) < 50
            else 1900 + int(found["short"])
        )
        if year >= 2000:
            yield year, path.name


def fetch() -> None:
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips=FIPS, layer=layer), suffix=".zip")
    _, index = raw._index(SOURCE)
    for _, name in files():
        url = URL.format(name=name)
        if url not in index:
            raw.store(
                SOURCE,
                url,
                (INBOX / name).read_bytes(),
                suffix=Path(name).suffix.lower(),
                retrieved_at=datetime.now(UTC),
                media_type="application/vnd.ms-excel",
            )


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value not in (None, "") else ""


def number(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return None


def sheet(rows: list[list[Any]]) -> Iterator[dict[str, Any]]:
    start = next(
        (
            n
            for n in range(1, min(len(rows), 8))
            if clean(rows[n][0]).isdigit() and clean(rows[n][1]) and number(rows[n][2]) is not None
        ),
        None,
    )
    if start is None or start < 2:
        return
    width = len(rows[0])
    header = rows[:start]
    names = header[-1]
    middle = header[1:-1]
    party_row = next(
        (
            row
            for row in middle
            if any(clean(c) for c in row[5:]) and all(clean(c).upper() in PARTIES for c in row[5:] if clean(c))
        ),
        None,
    )
    offices = [row for row in middle if row is not party_row]
    groups: list[list[int]] = []
    title = ""
    office = ""
    qualifier = ["" for _ in offices[1:]]
    labels: list[str] = []
    for c in range(5, width):
        name = clean(names[c])
        top = clean(header[0][c])
        started = False
        if top:
            title, office, started = top, "", True
            qualifier = ["" for _ in offices[1:]]
        if offices:
            level = clean(offices[0][c])
            if level:
                office, started = level, True
                qualifier = ["" for _ in offices[1:]]
            for k, row in enumerate(offices[1:]):
                part = clean(row[c])
                if part:
                    qualifier[k], started = part, True
        if not name:
            continue
        if started or not groups:
            groups.append([])
            labels.append(" ".join(part for part in (title, office, *qualifier) if part))
        groups[-1].append(c)
    merged: dict[str, list[Any]] = {}
    for row in rows[start:]:
        if not clean(row[0]).isdigit():
            continue
        label = clean(row[1])
        if not label or re.search(r"(?i)total", label):
            continue
        if label not in merged:
            merged[label] = list(row)
        else:
            for c in range(5, width):
                if number(row[c]) is not None:
                    merged[label][c] = (number(merged[label][c]) or 0) + number(row[c])
    body = list(merged.items())
    seen: dict[str, dict[str, Any]] = {}
    for columns, text in zip(groups, labels, strict=True):
        text = re.sub(r"\s+", " ", text).strip()
        if text in seen:
            seen[text]["columns"].extend(columns)
        else:
            seen[text] = {"text": text, "columns": list(columns)}
    for entry in seen.values():
        columns = entry["columns"]
        inside = [n for n, (_, row) in enumerate(body) if any(number(row[c]) is not None for c in columns)]
        if not inside:
            continue
        votes = {c: [number(body[n][1][c]) or 0 for n in inside] for c in columns}
        if not any(sum(v) for v in votes.values()):
            continue
        text = entry["text"]
        seats = SEATS.search(text)
        count = (seats[1] or seats[2]) if seats else None
        vote_for = (
            (int(count) if count.isdigit() else WORDS[count.lower()]) if count else 1 if SINGLE.search(text) else 0
        )
        text = NOISE.sub(" ", SEATS.sub(" ", text)).replace(" - ", " ")
        text = re.sub(r"(?i)\b(ward \d+) \1\b", r"\1", text)
        choices = []
        for c in columns:
            label = clean(names[c])
            party = clean(party_row[c]).upper() if party_row is not None and clean(party_row[c]) else None
            if re.match(r"(?i)write.?in", label):
                label, party = "Write-ins", None
            choices.append(
                {
                    "text": label,
                    "party": party if party in ("DEMOCRATIC", "REPUBLICAN", "GREEN", "LIBERTARIAN") else None,
                    "total": str(sum(votes[c])),
                    "places": [(body[n][0], str(v)) for n, v in zip(inside, votes[c], strict=True)],
                }
            )
        yield {"text": re.sub(r"\s+", " ", text).strip(), "vote_for": vote_for, "choices": choices}


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    tenant = next(iter(registry))
    seen: dict[int, set[str]] = {}
    for year, name in files():
        book = fastexcel.read_excel(raw.cached(SOURCE, URL.format(name=name)).path.read_bytes())
        nodes = []
        taken = seen.setdefault(year, set())
        for sheet_name in book.sheet_names:
            table = book.load_sheet(sheet_name, header_row=None).to_polars()
            for node in sheet([list(row) for row in table.iter_rows()]):
                if node["text"] in taken:
                    continue
                taken.add(node["text"])
                nodes.append({**node, "key": str(len(nodes))})
        if not nodes:
            print(f"{name}: layout not read")
            continue
        first = date(year, 11, 1)
        day = first + timedelta(days=(7 - first.weekday()) % 7 + 1)
        yield {
            "tenant": tenant,
            "election": f"{year}:{name}",
            "day": day,
            "title": f"{year} general election",
            "official": True,
            "primary": False,
            "contests": nodes,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(
        SOURCE, PLATFORM, lambda link, tenant: link.startswith("https://votehamiltoncountyohio.gov")
    )
