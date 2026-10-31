import difflib
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ok_stats"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Oklahoma State Election Board"
TITLE = "Election Results Statistics, general elections 1988-1992"
LANDING = "https://digitalprairie.ok.gov/digital/collection/stgovpub/id/9947"
URL = "https://digitalprairie.ok.gov/digital/api/collection/stgovpub/id/{ptr}/download"
POINTERS = {"1988": 13884, "1990": 13883, "1992": 13912}
WANTED = {(1988, "president"), (1990, "governor"), (1990, "senate"), (1992, "president"), (1992, "senate")}
OFFICES = {
    "GOVERNOR": "governor",
    "UNITEDSTATESSENATOR": "senate",
    "PRESIDENT": "president",
    "PRESIDENTIALELECTORS": "president",
}
PARTIES = {"D": "Democrat", "R": "Republican", "I": "Independent", "L": "Libertarian"}


def fetch() -> None:
    for ptr in POINTERS.values():
        raw.fetch(SOURCE, URL.format(ptr=ptr), suffix=".pdf")


def county_of(text: str, names: dict[str, str]) -> str | None:
    letters = re.sub(r"[^A-Za-z]", "", text.translate(str.maketrans("105", "los"))).lower()
    if letters in names:
        return names[letters]
    close = difflib.get_close_matches(letters, list(names), n=2, cutoff=0.8)
    if len(close) == 1:
        return names[close[0]]
    if len(close) == 2:
        first, second = (difflib.SequenceMatcher(None, letters, c).ratio() for c in close)
        if first - second > 0.1:
            return names[close[0]]
    return None


def groups(line: str) -> list[tuple[str, list[str]]] | None:
    found: list[tuple[str, list[str]]] = []
    for token in re.split(r"\s{2,}", line.strip()):
        if re.fullmatch(r"[0-9][0-9, ]*", token):
            if not found:
                return None
            found[-1][1].append(token.replace(" ", ""))
        elif re.search(r"[A-Za-z]", token) and not re.search(r"[^A-Za-z0-9 .:'-]", token):
            found.append((token, []))
        else:
            return None
    return found


def sections(lines: list[str], names: dict[str, str]) -> list[dict[str, object]]:
    found = []
    i = 0
    while i < len(lines):
        heading = re.search(r"OFFICIAL\s+RET\S*\s+GENERAL\s+ELECT\S*.*?(\d{4})\s*$", lines[i])
        if heading is None:
            i += 1
            continue
        year = int(heading[1])
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        title = re.sub(r"[^A-Z]", "", lines[j].upper()) if j < len(lines) else ""
        office = OFFICES.get(title)
        header: list[str] = []
        rows: list[str] = []
        j += 1
        while j < len(lines) and not re.search(
            r"OFFICIAL\s+RET\S*\s+(GENERAL|DEMOCRAT|REPUBLIC|PRIMARY|RUNOFF)", lines[j]
        ):
            parsed = groups(lines[j]) if lines[j].strip() else None
            if parsed and parsed[0][1]:
                rows.append(lines[j])
            elif lines[j].strip() and not rows:
                header.append(lines[j])
            j += 1
        i = j
        if office is not None and (year, office) in WANTED and rows:
            found.append({"year": year, "office": office, "header": header, "rows": rows})
    return found


def labels(header: list[str], first: str, width: int) -> list[tuple[str, str]]:
    chunks = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+(?: \S+)*", first)]
    cells = chunks[1 : 1 + width]
    split = chunks[1 + width][0] if len(chunks) > 1 + width else 10**6
    columns: list[list[str]] = [[] for _ in cells]
    for line in header:
        for m in re.finditer(r"\S+(?: \S+)*", line):
            if m.start() >= split - 2:
                continue
            centre = (m.start() + m.end()) / 2
            nearest = min(range(width), key=lambda k: abs((cells[k][0] + cells[k][1]) / 2 - centre))
            columns[nearest].append(m.group())
    out = []
    for parts in columns:
        text = re.sub(r"\(cont\.?\)\s*", "", " ".join(parts))
        party = re.search(r"\(([A-Z])\)\s*$", text)
        out.append((re.sub(r"\s*\([A-Z]\)\s*$", "", text).strip(), PARTIES.get(party[1], "") if party else ""))
    return out


def parse() -> None:
    with connect() as conn:
        names = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%%'"""
            ).fetchall()
        }
    frames = []
    report = []
    for ptr in POINTERS.values():
        path = raw.cached(SOURCE, URL.format(ptr=ptr)).path
        lines = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=False
        ).stdout.splitlines()
        for section in sections(lines, names):
            year, office = section["year"], section["office"]
            note = f"{year} {office}"
            width = len(groups(section["rows"][0])[0][1])
            records: list[tuple[str, list[int]]] = []
            printed: list[int] | None = None
            ok = True
            for line in section["rows"]:
                for name, values in groups(line):
                    if name.upper().startswith("TOTAL VOTE"):
                        continue
                    if len(values) != width:
                        ok = False
                        continue
                    numbers = [int(v.replace(",", "")) for v in values]
                    if re.fullmatch(r"TOTAL:?", name.upper()):
                        printed = numbers
                        continue
                    if name.upper().startswith("TOTAL"):
                        continue
                    code = county_of(name, names)
                    if code is None:
                        ok = False
                        report.append(f"{note}: no county {name}")
                        continue
                    records.append((code, numbers))
            if not ok or printed is None or len({c for c, _ in records}) != len(records):
                report.append(f"{note}: unreadable or no total ({len(records)} counties, width {width})")
                continue
            sums = [sum(values[k] for _, values in records) for k in range(width)]
            if sums != printed:
                report.append(f"{note}: {len(records)} counties do not tie {sums} against {printed}")
                continue
            names_of = labels(section["header"], section["rows"][0], width)
            report.append(f"{note}: ties, {len(records)} counties, columns {names_of}")
            frames.append(
                pl.DataFrame(
                    [
                        (year, office, day(year), code, names_of[k][0].upper(), names_of[k][1], values[k])
                        for code, values in records
                        for k in range(width)
                    ],
                    schema={
                        "year": pl.Int32,
                        "chamber": pl.String,
                        "day": pl.Date,
                        "code": pl.String,
                        "candidate": pl.String,
                        "party": pl.String,
                        "votes": pl.Int64,
                    },
                    orient="row",
                )
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "report.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    (
        pl.concat(frames)
        .with_columns(
            district=pl.lit(None, pl.Int16),
            writein=pl.col("candidate").str.contains("WRITE"),
            special=pl.lit(False),
            state=pl.lit("OK"),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("OK"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def day(year: int) -> date:
    first = date(year, 11, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7 + 1)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's president, governor, and Senate general elections 1988-1992 by county from the State Election Board's Election Results Statistics",
            polity="US-OK",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license="Public record",
        )
        for ptr in POINTERS.values():
            cs.artifact(raw.cached(SOURCE, URL.format(ptr=ptr)), source)
        authoritative = "The State Election Board's printed official returns for the general election; every column adds up to the printed total."
        medsl.attach(
            cs,
            conn,
            frame.filter(pl.col("chamber") != "president"),
            {"OK": source},
            authoritative=authoritative,
            extend=True,
        )
        medsl.attach(
            cs, conn, frame.filter(pl.col("chamber") == "president"), {"OK": source}, authoritative=authoritative
        )
        return cs.write()
