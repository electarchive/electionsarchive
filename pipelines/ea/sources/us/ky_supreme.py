import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.ky_supreme"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-356"
BASE = "https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/"
PUBLISHER = "Kentucky State Board of Elections"
TITLE = "Official election night tally results by county"
FILES = {
    "00Gen_Statewidebycounty.txt": date(2000, 11, 7),
    "2002statebycounty.txt": date(2002, 11, 5),
    "2004statebyCOUNTY.txt": date(2004, 11, 2),
    "STATEwidebycounty.txt": date(2006, 11, 7),
    "STATEwide by candidate by county gen 08.txt": date(2008, 11, 4),
}
PDFS = {
    "2012genresults.pdf": date(2012, 11, 6),
    "2014 General Election Results.pdf": date(2014, 11, 4),
    "2016 General Election Results.pdf": date(2016, 11, 8),
    "2020 General Election Results.pdf": date(2020, 11, 3),
    "2024 General Election Certification as Amended on December 9th 2024.pdf": date(2024, 11, 5),
}
METHOD = (
    "County totals come from the State Board of Elections' official election night tally reports by county. "
    "Kentucky elects each Supreme Court justice by district, so each contest covers only that district's counties."
)


def link(name: str) -> str:
    return f"{BASE}manual/{name.replace(' ', '%20')}"


def fetch() -> None:
    for name in [*FILES, *PDFS]:
        raw.store(
            SOURCE,
            link(name),
            (INBOX / name).read_bytes(),
            suffix=Path(name).suffix,
            retrieved_at=datetime.fromtimestamp((INBOX / name).stat().st_mtime, UTC),
            media_type="application/pdf" if name in PDFS else "text/plain",
        )


def parse() -> None:
    with connect() as conn:
        names = sorted(
            (re.sub(r"^mc", "m!", re.sub(r"[^a-z]", "", text.lower())), code)
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%'"""
            ).fetchall()
        )
    texts = {name: raw.cached(SOURCE, link(name)).path.read_text(errors="replace").splitlines() for name in FILES}
    abbreviations: list[str] = []
    office = None
    for line in texts["2004statebyCOUNTY.txt"]:
        if match := re.search(r"OFFICE: (\w\d\d)/", line):
            office = match[1]
        elif office == "A01" and "*" in line and not line[:30].strip():
            abbreviations.extend(re.findall(r"\*(\S+)", line))
    if len(abbreviations) != 120 or len(set(abbreviations)) != 120:
        raise ValueError(f"{len(abbreviations)} county columns")
    mapped = {}
    for index, abbreviation in enumerate(abbreviations):
        if not names[index][0].replace("m!", "mc", 1).startswith(abbreviation[:3].lower()):
            raise ValueError(f"{abbreviation} lines up with {names[index][0]}")
        mapped[abbreviation] = names[index][1]
    rows = []
    for name, day in FILES.items():
        district = None
        columns: list[str] = []
        totals: dict[tuple[int, str], int] = {}
        found: dict[tuple[int, str], int] = {}
        for line in texts[name]:
            if match := re.search(r"OFFICE: (\w\d\d)/(\d+)/\d+\s+(.*?)\s*$", line):
                district = int(match[2]) if match[1] == "J15" and match[3].startswith("SUPREME JUSTICE") else None
                columns = []
                continue
            if (
                district is None
                or not line.strip()
                or re.search(r"KENTUCKY STATE BOARD|REPORT OF|TIME OF REPORT", line)
            ):
                continue
            if "*" in line and not line[:30].strip():
                columns = re.findall(r"\*(\S+)", line)
                continue
            split = re.match(r"\s*(.*?\S)\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$", line)
            if split is None:
                raise ValueError(f"{name} {district}: unreadable line {line!r}")
            cells = split[2].split()
            candidate = re.sub(r"\s+", " ", split[1]).upper()
            if len(cells) == 1 and len(columns) != 1:
                totals[(district, candidate)] = int(cells[0].replace(",", ""))
                continue
            if len(cells) != len(columns):
                raise ValueError(f"{name} {district} {candidate}: {len(cells)} cells for {len(columns)} counties")
            for abbreviation, votes in zip(columns, cells, strict=True):
                rows.append((day, district, candidate, mapped[abbreviation], int(votes.replace(",", ""))))
                found[(district, candidate)] = found.get((district, candidate), 0) + int(votes.replace(",", ""))
        for key, summed in found.items():
            if totals.get(key, summed) != summed:
                raise ValueError(f"{name} {key}: counties sum to {summed}, printed total {totals[key]}")
    codes = dict(names)
    for name, day in PDFS.items():
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, link(name)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        for start, line in enumerate(lines):
            heading = re.fullmatch(r"\s*(\d+)(?:st|nd|rd|th) Supreme Court District\s*", line)
            if heading is None or not any(
                "Justice of the Supreme Court" in text for text in lines[max(0, start - 2) : start]
            ):
                continue
            header: list[str] = []
            body: list[str] = []
            for text in lines[start + 1 :]:
                if re.match(r"\s*Total Votes", text):
                    body.append(text)
                    break
                if re.fullmatch(r"\s*[A-Za-z][A-Za-z .'-]*?\s{2,}[\d,]+(?:\s+[\d,]+)*\s*", text):
                    body.append(text)
                elif not body and text.strip():
                    header.append(text)
            first = re.search(r"\s{2,}", body[0].strip())
            cells = [
                (m.start(), m.end())
                for m in re.finditer(r"[\d,]+", body[0][len(body[0]) - len(body[0].lstrip()) + first.end() :])
            ]
            offset = len(body[0]) - len(body[0].lstrip()) + first.end()
            centers = [offset + (a + b) / 2 for a, b in cells]
            labels: list[list[str]] = [[] for _ in centers]
            for text in header:
                for token in re.finditer(r"\S+(?: \S+)*", text):
                    if token[0] == "Nonpartisan":
                        continue
                    middle = (token.start() + token.end()) / 2
                    labels[min(range(len(centers)), key=lambda i: abs(centers[i] - middle))].append(token[0])
            candidates = [re.sub(r"\s+", " ", " ".join(label)).upper() for label in labels]
            totals = None
            sums = [0] * len(centers)
            for row in body:
                if re.match(r"\s*Total Votes", row):
                    totals = [int(v.replace(",", "")) for v in re.findall(r"[\d,]+", row)]
                    continue
                county, values = re.match(r"\s*(.*?\S)\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$", row).groups()
                votes = [int(v.replace(",", "")) for v in values.split()]
                if len(votes) != len(candidates):
                    raise ValueError(
                        f"{name} {heading[1]} {county}: {len(votes)} cells for {len(candidates)} candidates"
                    )
                for index, (candidate, count) in enumerate(zip(candidates, votes, strict=True)):
                    rows.append(
                        (
                            day,
                            int(heading[1]),
                            candidate,
                            codes[re.sub(r"^mc", "m!", re.sub(r"[^a-z]", "", county.lower()))],
                            count,
                        )
                    )
                    sums[index] += count
            if totals != sums:
                raise ValueError(f"{name} district {heading[1]}: counties sum to {sums}, printed totals {totals}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["day", "district", "candidate", "county", "votes"], orient="row").select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        day="day",
        state=pl.lit("KY"),
        title=pl.lit("Justice of the Supreme Court"),
        post=pl.format("DISTRICT {}", "district"),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate="candidate",
        party=pl.lit(None, pl.String),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(120),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "post", "candidate")
        .agg(pl.col("votes").sum(), pl.col("county").n_unique())
        .sort("year", "post", "votes")
        .rows()
    )


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://elect.ky.gov/results/",
                license="Public record",
            )
        for name in [*FILES, *PDFS]:
            cs.artifact(raw.cached(SOURCE, link(name)), source)
        return {"KY": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Kentucky's 2000–2024 Supreme Court district elections by county from the State Board of Elections' official results",
        cite=cite,
        method=METHOD,
    )
