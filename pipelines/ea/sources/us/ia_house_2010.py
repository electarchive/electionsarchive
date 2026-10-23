import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ia_legislature import LANDING, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.ia_house_2010"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "Official Results Report, 2010 General Election: U.S. Representative"
DAY = date(2010, 11, 2)
REPORT = "https://web.archive.org/web/2012id_/http://sos.iowa.gov/elections/pdf/2010/usreporr.pdf"
DISTRICT = re.compile(r"^U\.S\. Representative (\d+)\w\w District")
ROW = re.compile(r"^([A-Z][A-Za-z.' ]*?)\s{2,}(\d+(?:\s+\d+)+)\s*$")


def fetch() -> None:
    try:
        raw.cached(SOURCE, REPORT)
    except LookupError:
        content = subprocess.run(["curl", "-sL", "--compressed", REPORT], capture_output=True, check=True).stdout
        raw.store(SOURCE, REPORT, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '19%%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, REPORT).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    rows = []
    district = None
    centers: list[float] = []
    names: list[list[str]] = []
    parts: list[tuple[str, list[int]]] = []
    for line in text.splitlines():
        if found := DISTRICT.match(line.strip()):
            if district != int(found[1]):
                district, centers, names, parts = int(found[1]), [], [], []
            continue
        if district is None:
            continue
        chunks = list(re.finditer(r"\S+(?: \S+)*", line))
        if not centers and chunks and chunks[0][0] == "County":
            centers = [chunk.start() + len(chunk[0]) / 2 for chunk in chunks[1:-4]]
            names = [[chunk[0]] for chunk in chunks[1:-4]]
            continue
        row = ROW.match(line.strip())
        if row is None:
            if centers and not parts:
                for chunk in chunks:
                    center = chunk.start() + len(chunk[0]) / 2
                    nearest = min(range(len(centers)), key=lambda k: abs(centers[k] - center))
                    if abs(centers[nearest] - center) < 10:
                        names[nearest].append(chunk[0])
            continue
        numbers = [int(value) for value in row[2].split()]
        if len(numbers) != len(centers) + 4 or sum(numbers[:-1]) != numbers[-1]:
            raise ValueError(f"IA-{district} {row[1]}: {numbers} does not add up")
        if row[1] != "Grand Totals":
            parts.append((counties[re.sub(r"[^a-z]", "", row[1].lower())], numbers[: len(centers) + 1]))
            continue
        if [sum(values[k] for _, values in parts) for k in range(len(centers) + 1)] != numbers[: len(centers) + 1]:
            raise ValueError(f"IA-{district}: the counties do not add up to the grand totals")
        labels = [" ".join(words).upper() for words in names] + ["SCATTERING"]
        rows.extend(
            (district, code, labels[k], values[k], k == len(centers))
            for code, values in parts
            for k in range(len(labels))
        )
        district = None
    if len({row[0] for row in rows}) != 5:
        raise ValueError("expected Iowa's five districts")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows,
        schema={
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "writein": pl.Boolean,
        },
        orient="row",
    )
    print(frame.group_by("district", "candidate").agg(pl.col("votes").sum()).sort("district", "votes"))
    frame.with_columns(
        chamber=pl.lit("house"),
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("IA"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("IA"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa's 2010 U.S. House generals by county (Secretary of State official results report, via the Wayback Machine)",
            polity="US-IA",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, REPORT), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"IA": source}, strict=True)
        return cs.write()
