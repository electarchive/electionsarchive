import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ny_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/2020id_/https://www.elections.ny.gov/NYSBOE/"
PUBLISHER = "New York State Board of Elections"
TITLE = "Primary election canvass by county"
FILES = {
    date(2002, 9, 10): ["elections/2002/primary/2002primarycanvass.pdf"],
    date(2004, 9, 14): ["elections/2004/2004PrimaryResults.pdf"],
    date(2006, 9, 12): [
        "elections/2006/Primary/2006PrimaryCD.pdf",
        "elections/2006/Primary/2006PrimaryUSSenDem.pdf",
        "elections/2006/Primary/2006PrimaryUSSenRep.pdf",
    ],
    date(2008, 9, 9): ["Elections/2008/Primary/2008PrimaryReturns.pdf"],
    date(2010, 9, 14): ["elections/2010/Primary/2010PrimaryElectionResults.pdf"],
}
PARTIES = {"rep": "republican", "republican": "republican", "dem": "democratic", "democratic": "democratic"}
NUMBER = re.compile(r"^\d[\d,]*$")
TITLE_HOUSE = re.compile(
    r"^\s*(?:Congress\s+(\d+)\s*(?:st|nd|rd|th)?|(\d+)(?:st|nd|rd|th) CD)\s*-\s*([A-Za-z ]+?)(?:\s{2,}(.*))?$"
)
TITLE_SENATE = re.compile(r"^\s*Statewide\s+(Democratic|Republican)\s+US Senator Primary", re.I)
ROW = re.compile(r"^\s*((?:Part of )?[A-Za-z][A-Za-z. ]*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*$", re.I)


def read(path: Path) -> list[str]:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout.split(
        "\n"
    )


def chunks(line: str) -> list[tuple[float, str]]:
    return [((m.start() + m.end()) / 2, m.group().strip()) for m in re.finditer(r"\S+(?: \S+)*", line)]


def blocks(lines: list[str]) -> list[tuple[str, str, int | None, bool, list[list[tuple[float, str]]], list[tuple[str, list[int]]], list[float]]]:
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        house = TITLE_HOUSE.match(line)
        senate = None if house else TITLE_SENATE.match(line)
        if not house and not senate:
            i += 1
            continue
        head = [chunks(house.group(4))] if house and house.group(4) else []
        if house:
            office, district, party = "house", int(house.group(1) or house.group(2)), house.group(3).strip().lower()
            special = False
        else:
            office, district = "senate", None
            party = senate.group(1).lower()
            special = "unexpired" in line.lower()
        j = i + 1
        names: list[list[tuple[float, str]]] = []
        rows: list[tuple[str, list[int]]] = []
        anchor = None
        while j < len(lines) and j < i + 120:
            current = lines[j]
            if TITLE_HOUSE.match(current) and j > i + 1:
                break
            row = ROW.match(current)
            if row and not re.match(r"^\s*(county|candidate)\b", current, re.I):
                label = row.group(1).strip()
                numbers = [int(n.replace(",", "")) for n in row.group(2).split()]
                if label.lower() in ("total", "statewide total"):
                    break
                if label.lower().startswith("total"):
                    j += 1
                    continue
                if anchor is None:
                    anchor = [(m.start() + m.end()) / 2 for m in re.finditer(r"\d[\d,]*", current)]
                rows.append((label, numbers))
            elif not rows and current.strip():
                if not re.search(r"Statement|REPRESENTATIVE|said State|Primary Vote|Canvass", current):
                    names.append(chunks(current))
            j += 1
        if rows:
            out.append((office, party, district, special, head + names, rows, anchor))
        i = j
    return out


def columns(head: list[list[tuple[float, str]]], anchor: list[float]) -> list[str]:
    names = [""] * len(anchor)
    for line in head:
        kept = [
            (x, text)
            for x, text in line
            if not re.fullmatch(r"(candidate|county)", text, re.I)
            and not re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b", text)
        ]
        if len(kept) > len(anchor):
            continue
        for k, (_, text) in enumerate(kept):
            slot = len(anchor) - 1 if re.search(r"scatter|write", text, re.I) else k
            names[slot] = f"{names[slot]} {text}".strip()
    return names


def fetch() -> None:
    for names in FILES.values():
        for name in names:
            raw.fetch(SOURCE, WAYBACK + name, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '36%%'"""
            ).fetchall()
        }
    rows = []
    for day, names in FILES.items():
        seen = set()
        for name in names:
            for office, party, district, special, head, body in [
                (b[0], b[1], b[2], b[3], b[4], (b[5], b[6])) for b in blocks(read(raw.cached(SOURCE, WAYBACK + name).path))
            ]:
                lines, anchor = body
                party = PARTIES.get(party.split()[0], None)
                if party is None or special or (office, party, district, special) in seen:
                    continue
                seen.add((office, party, district, special))
                labels = columns(head, anchor)
                for county, numbers in lines:
                    key = re.sub(r"[^a-z]", "", county.lower().removeprefix("part of "))
                    if key == "totalnyc":
                        continue
                    if key not in counties:
                        raise ValueError(f"{name}: no county {county}")
                    if len(numbers) != len(labels):
                        raise ValueError(f"{name}: {county} has {len(numbers)} numbers for {labels}")
                    for label, votes in zip(labels, numbers, strict=True):
                        if re.fullmatch(r"sub ?total|blank|void|blank/void", label, re.I) or not label:
                            continue
                        rows.append(
                            (
                                day.year,
                                f"{office}:{party}",
                                day,
                                special,
                                "NY",
                                district,
                                counties[key],
                                "WRITE-INS" if re.search(r"scatter|write", label, re.I) else label.upper(),
                                votes,
                            )
                        )
    frame = pl.DataFrame(
        rows,
        schema=["year", "chamber", "day", "special", "state", "district", "code", "candidate", "votes"],
        orient="row",
    ).with_columns(
        year=pl.col("year").cast(pl.Int32),
        district=pl.col("district").cast(pl.Int16),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate") == "WRITE-INS",
        kind=pl.lit("county"),
        source=pl.lit("NY"),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.group_by(
        "year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "kind", "source"
    ).agg(pl.col("writein").any(), pl.col("votes").sum()).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's 2002 to 2010 House and Senate primaries by county",
            polity="US-NY",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.elections.ny.gov/ElectionResults.html",
            license="Public record",
        )
        for names in FILES.values():
            for name in names:
                cs.artifact(raw.cached(SOURCE, WAYBACK + name), source)
        medsl.attach(cs, conn, frame, {"NY": source}, strict=True)
        return cs.write()
