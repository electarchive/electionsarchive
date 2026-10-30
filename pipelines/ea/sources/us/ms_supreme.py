import csv
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

SOURCE = "us.ms_supreme"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-357"
BASE = "https://www.sos.ms.gov/elections-voting/results/manual/"
PUBLISHER = "Mississippi Secretary of State"
TITLE = "Official election results by county"
CSVS = {
    "2020 General Election Official Statewide Recapitulation (CSV).csv": date(2020, 11, 3),
    "2024ElectionRecapSheets.csv": date(2024, 11, 5),
}
PDFS = {
    "Supreme Court- District 1, Position 1.pdf": date(2012, 11, 6),
    "Supreme Court- District 1, Position 2.pdf": date(2012, 11, 6),
    "Supreme Court- District 2, Position 3.pdf": date(2012, 11, 6),
    "Supreme Court- District 3, Position 3.pdf": date(2012, 11, 6),
}
METHOD = (
    "County totals come from the Secretary of State's official statewide recapitulation reports and certified county summations. "
    "Mississippi elects each Supreme Court justice by district and position, so each contest covers only that district's counties."
)


def link(name: str) -> str:
    return f"{BASE}{name.replace(' ', '%20')}"


def fetch() -> None:
    for name in [*CSVS, *PDFS]:
        raw.store(
            SOURCE,
            link(name),
            (INBOX / name).read_bytes(),
            suffix=Path(name).suffix,
            retrieved_at=datetime.fromtimestamp((INBOX / name).stat().st_mtime, UTC),
            media_type="text/csv" if name in CSVS else "application/pdf",
        )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '28%'"""
            ).fetchall()
        }
    codes["jeffdavis"] = codes["jeffersondavis"]
    rows: list[tuple[date, int, int, str, str, int]] = []
    for name, day in CSVS.items():
        sums: dict[tuple[str, str], int] = {}
        printed: dict[tuple[str, str], int] = {}
        text = raw.cached(SOURCE, link(name)).path.read_text(encoding="utf-8-sig")
        for row in csv.DictReader(text.splitlines()):
            found = re.search(r"Supreme Court District (\d)\([A-Za-z]+\) Position (\d)", row["Office"])
            if found is None:
                continue
            candidate = row["Candidate"].strip().upper()
            votes = int(row["County Total"])
            rows.append(
                (
                    day,
                    int(found[1]),
                    int(found[2]),
                    candidate,
                    codes[re.sub(r"[^a-z]", "", row["County"].lower())],
                    votes,
                )
            )
            key = (row["Office"], candidate)
            sums[key] = sums.get(key, 0) + votes
            printed[key] = int(row["State Total"])
        for key, summed in sums.items():
            if printed[key] != summed:
                raise ValueError(f"{name} {key}: counties sum to {summed}, printed total {printed[key]}")
    for name, day in PDFS.items():
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, link(name)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        seat = re.search(r"District (\d) .*?, Position (\d)", " ".join(lines[:4]))
        county = None
        sums: dict[str, int] = {}
        printed: dict[str, int] = {}
        for line in lines:
            if line.startswith("Totals"):
                county = "Totals"
            found = re.match(r"^(?:(\S.*?)\s{2,}|\s+)?(\S.*?)\s{2,}([\d,]+)(?:\s+[\d.]+%)?\s*$", line)
            if found is None or found[2].startswith("Total For"):
                continue
            if found[1] is not None:
                county = found[1].strip()
            if found[2] == "CANDIDATE" or county is None:
                continue
            votes = int(found[3].replace(",", ""))
            candidate = re.sub(r"^Totals\s+", "", found[2]).upper()
            if county == "Totals":
                printed[candidate] = votes
            else:
                rows.append(
                    (day, int(seat[1]), int(seat[2]), candidate, codes[re.sub(r"[^a-z]", "", county.lower())], votes)
                )
                sums[candidate] = sums.get(candidate, 0) + votes
        if printed != sums:
            raise ValueError(f"{name}: counties sum to {sums}, printed totals {printed}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows, schema=["day", "district", "position", "candidate", "county", "votes"], orient="row"
    ).select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        day="day",
        state=pl.lit("MS"),
        title=pl.lit("Justice of the Supreme Court"),
        post=pl.format("DISTRICT {} POSITION {}", "district", "position"),
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
        counties=pl.lit(82),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "post")
        .agg(pl.col("votes").sum(), pl.col("county").n_unique())
        .sort("year", "post")
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
                url="https://www.sos.ms.gov/elections-voting/results",
                license="Public record",
            )
        for name in [*CSVS, *PDFS]:
            cs.artifact(raw.cached(SOURCE, link(name)), source)
        return {"MS": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Mississippi's Supreme Court district elections by county from the Secretary of State's official results",
        cite=cite,
        method=METHOD,
    )
