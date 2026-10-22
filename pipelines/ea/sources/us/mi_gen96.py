import difflib
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mi_gen96"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = "https://web.archive.org/web/2024id_/https://www.michigan.gov/-/media/Project/Websites/sos/05diljak/1996_GENERAL_RESULTS.pdf"
PUBLISHER = "Michigan Department of State"
TITLE = "General Election, November 5, 1996: Election Results"
DAY = date(1996, 11, 5)
DIGITS = str.maketrans({"l": "1", "I": "1", "O": "0", "o": "0", "S": "5", "s": "5", "B": "8", "Z": "2"})


def fetch() -> None:
    raw.fetch(SOURCE, FILE, suffix=".pdf")


def cells(line: str) -> list[tuple[float, str]]:
    return [(m.start() + len(m.group()) / 2, m.group()) for m in re.finditer(r"\S+(?: \S+)*", line)]


def numbers_of(text: str) -> list[int]:
    return [int(re.sub(r"\D", "", token.translate(DIGITS)) or "0") for _, token in cells(text.replace("' ", "'"))]


def dirty_of(text: str) -> list[bool]:
    return [re.search(r"[A-Za-z$]|\d \d", token) is not None for _, token in cells(text.replace("' ", "'"))]


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st ")): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '26%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, FILE).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    lines = text.splitlines()
    end = next(i for i, line in enumerate(lines) if "STATE REPRESENTATIVE DISTRICT" in line)
    district = None
    columns: list[tuple[float, str]] = []
    rows: list[dict[str, object]] = []
    problems: list[str] = []
    for index in range(end):
        line = lines[index]
        header = re.search(r"CONGRESSIONAL DISTRICT.*?\b06(\d{3})000\b", line)
        if header is not None:
            district = int(header.group(1))
            columns = []
            continue
        if district is None:
            continue
        if re.match(r"\s*COUNTY CODE/NAME", line):
            blank = re.sub(r"^\s*COUNTY CODE/NAME", lambda m: " " * len(m.group()), line)
            parties = [(m.start() + len(m.group()) / 2, m.group()) for m in re.finditer(r"\b[A-Z]{3,4}\b", blank)]
            above = [lines[index - 2], lines[index - 1]]
            names: dict[int, list[str]] = {i: [] for i in range(len(parties))}
            for row in above:
                for centre, name in cells(re.sub(r"^\s*(TOTAL|BY)\b", lambda m: " " * len(m.group()), row)):
                    nearest = min(range(len(parties)), key=lambda i: abs(parties[i][0] - centre))
                    names[nearest].append(name)
            columns = [(0.0, " ".join(names[i])) for i in range(len(parties))]
            continue
        total = re.match(r"\s*TOTALS\s+(\S.*)$", line)
        if total is not None and columns:
            rows.append(
                {
                    "district": district,
                    "county": None,
                    "line": line.strip(),
                    "numbers": numbers_of(total.group(1)),
                    "dirty": dirty_of(total.group(1)),
                    "columns": [name for _, name in columns],
                }
            )
            continue
        match = re.match(r"\s*([\dOolSs]\s?[\dOolSs]?)\s+([A-Z$][A-Za-z$ .'\\&-]+?)\s{2,}(\S.*)$", line)
        if match is None or not columns:
            continue
        fuzzy = re.sub(r"[^a-z]", "", match.group(2).lower().replace("gd.", "grand"))
        best = difflib.get_close_matches(fuzzy, list(counties), n=1, cutoff=0.6)
        if not best:
            problems.append(f"county {match.group(2)!r}")
            continue
        rows.append(
            {
                "district": district,
                "county": counties[best[0]],
                "line": line.strip(),
                "numbers": numbers_of(match.group(3)),
                "dirty": dirty_of(match.group(3)),
                "columns": [name for _, name in columns],
            }
        )
    candidates: list[dict[str, object]] = []
    sums: dict[tuple[int, int], int] = {}
    for row in rows:
        names = row["columns"]
        numbers = list(row["numbers"]) + [0] * (len(names) + 1 - len(row["numbers"]))
        if len(numbers) != len(names) + 1:
            problems.append(f"width {row['line']}")
            continue
        dirty = ([*row["dirty"], *[False] * len(numbers)])[: len(numbers)]
        if numbers[0] != sum(numbers[1:]):
            fixes = [
                (
                    difflib.SequenceMatcher(
                        None, str(numbers[j]), str(numbers[0] - (sum(numbers[1:]) - numbers[j]))
                    ).ratio(),
                    j,
                )
                for j in range(len(numbers))
                if j > 0 and (not any(dirty[1:]) or dirty[j]) and numbers[0] - (sum(numbers[1:]) - numbers[j]) >= 0
            ]
            ratio, j = max(fixes)
            if ratio < 0.7 and not dirty[j]:
                problems.append(f"sum {row['line']}")
                continue
            numbers[j] = numbers[0] - (sum(numbers[1:]) - numbers[j])
        for j, votes in enumerate(numbers[1:], start=1):
            if row["county"] is None:
                sums[(int(row["district"]), j)] = sums.get((int(row["district"]), j), 0) - votes
            else:
                sums[(int(row["district"]), j)] = sums.get((int(row["district"]), j), 0) + votes
                candidates.append(
                    {"district": row["district"], "code": row["county"], "candidate": names[j - 1], "votes": votes}
                )
    problems.extend(f"column {key}: {value}" for key, value in sums.items() if value != 0)
    if problems:
        raise ValueError(problems[:12])
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(candidates)
        .with_columns(
            candidate=pl.col("candidate").str.to_uppercase().str.strip_chars(),
            year=pl.lit(DAY.year, pl.Int32),
            chamber=pl.lit("house"),
            day=pl.lit(DAY),
            special=pl.lit(False),
            state=pl.lit("MI"),
            district=pl.col("district").cast(pl.Int16),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("MI"),
        )
        .with_columns(writein=pl.col("candidate").str.contains("WRITE"))
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .group_by(
            "year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "kind", "source"
        )
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Michigan's 1996 House general elections by county",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.michigan.gov/sos/elections/election-results-and-data",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, FILE), source)
        medsl.attach(cs, conn, frame, {"MI": source}, strict=True)
        return cs.write()
