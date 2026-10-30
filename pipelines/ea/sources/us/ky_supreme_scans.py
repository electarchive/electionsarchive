import csv
import difflib
import io
import json
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.ky_supreme_scans"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-356"
BASE = "https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/"
PUBLISHER = "Kentucky State Board of Elections"
TITLE = "Official general election results (certified)"
READS = json.loads((Path(__file__).parent / "ky_supreme_reads.json").read_text())
METHOD = (
    "County totals were read from the State Board of Elections' scanned certified results books by two readers: "
    "Tesseract on the page image located each figure's row and column, and a second reader keyed the figures from the image. "
    "Every column's county figures sum to the printed Total Votes, and each figure stores its page and bounding box. "
    "Kentucky elects each Supreme Court justice by district, so each contest covers only that district's counties."
)


def link(name: str) -> str:
    return f"{BASE}manual/{name.replace(' ', '%20')}"


def fetch() -> None:
    for name in sorted({section["pdf"] for section in READS.values()}):
        raw.store(
            SOURCE,
            link(name),
            (INBOX / name).read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp((INBOX / name).stat().st_mtime, UTC),
            media_type="application/pdf",
        )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%'"""
            ).fetchall()
        }
    rows: list[tuple[date, int, str, str, int, int, float, float, float, float]] = []
    for key, section in READS.items():
        day, district = key.split("|")
        sums = [0] * len(section["candidates"])
        for page, counties in section["pages"].items():
            image = subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    "300",
                    "-gray",
                    "-png",
                    "-f",
                    page,
                    "-l",
                    page,
                    "-singlefile",
                    str(raw.cached(SOURCE, link(section["pdf"])).path),
                ],
                capture_output=True,
                check=True,
            ).stdout
            words = [
                (row["text"].strip(), int(row["left"]), int(row["top"]), int(row["width"]), int(row["height"]))
                for row in csv.DictReader(
                    io.StringIO(
                        subprocess.run(
                            ["tesseract", "-", "-", "--psm", "6", "tsv"],
                            input=image,
                            capture_output=True,
                            check=True,
                        ).stdout.decode()
                    ),
                    delimiter="\t",
                    quoting=csv.QUOTE_NONE,
                )
                if row["text"].strip() and row["level"] == "5"
            ]
            heads = sorted((left + width for text, left, _, width, _ in words if text == "Nonpartisan"))
            if len(heads) != len(section["candidates"]):
                raise ValueError(f"{key} page {page}: {len(heads)} column headings")
            first = min(top for text, _, top, _, _ in words if text == "Nonpartisan")
            tops: dict[str, tuple[int, int]] = {}
            cursor = first
            for county in counties:
                match = max(
                    (word for word in words if heads[0] - 1000 < word[1] < heads[0] - 250 and word[2] > cursor),
                    key=lambda word: (
                        difflib.SequenceMatcher(None, word[0].lower(), county.lower()).ratio()
                        - (word[2] - cursor) / 100000
                    ),
                )
                if difflib.SequenceMatcher(None, match[0].lower(), county.lower()).ratio() >= 0.7:
                    tops[county] = (match[2], match[4])
                    cursor = match[2]
            known = [(index, tops[county]) for index, county in enumerate(counties) if county in tops]
            if len(known) < 2 and len(counties) > 1:
                raise ValueError(f"{key} page {page}: row positions not found")
            if len(known) > 1:
                step = (known[-1][1][0] - known[0][1][0]) / (known[-1][0] - known[0][0])
                for index, county in enumerate(counties):
                    if county not in tops:
                        tops[county] = (round(known[0][1][0] + step * (index - known[0][0])), known[0][1][1])
            for county, votes in counties.items():
                for column, count in enumerate(votes):
                    sums[column] += count
                    top, height = tops[county]
                    rows.append(
                        (
                            date.fromisoformat(day),
                            int(district),
                            section["candidates"][column],
                            codes[re.sub(r"[^a-z]", "", county.lower())],
                            count,
                            date.fromisoformat(day).year * 1000 + int(page),
                            (heads[column] - 160) / 3,
                            (top - 4) / 3,
                            (heads[column] + 8) / 3,
                            (top + height + 4) / 3,
                        )
                    )
        if sums != section["total"]:
            raise ValueError(f"{key}: counties sum to {sums}, printed totals {section['total']}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows,
        schema=["day", "district", "candidate", "county", "votes", "page", "x0", "y0", "x1", "y1"],
        orient="row",
    ).select(
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
        page="page",
        x0="x0",
        y0="y0",
        x1="x1",
        y1="y1",
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
        .sort("year", "post")
        .rows()
    )


def emit() -> Path:
    def pages(cs: Changeset) -> dict[int, tuple[UUID, int, int]]:
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
        result: dict[int, tuple[UUID, int, int]] = {}
        PAGES.mkdir(parents=True, exist_ok=True)
        for key in sorted(set(pl.read_parquet(PARSED / "returns.parquet")["page"])):
            year, number = divmod(key, 1000)
            name = next(section["pdf"] for section_key, section in READS.items() if section_key.startswith(str(year)))
            pdf = raw.cached(SOURCE, link(name))
            artifact = cs.artifact(pdf, source)
            image = PAGES / f"{pdf.sha256}-{number}.png"
            if not image.exists():
                subprocess.run(
                    [
                        "pdftoppm",
                        "-r",
                        "100",
                        "-gray",
                        "-png",
                        "-f",
                        str(number),
                        "-l",
                        str(number),
                        "-singlefile",
                        str(pdf.path),
                        str(image.with_suffix("")),
                    ],
                    check=True,
                )
            with Image.open(image) as rendered:
                width, height = rendered.size
            page = cs.page(artifact, number)
            result[key] = (page, width, height)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        return result

    def cite(cs: Changeset) -> dict[str, UUID]:
        return {"KY": cs.source(PUBLISHER, TITLE)}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Kentucky's 2010, 2018, and 2022 Supreme Court district elections by county from the State Board of Elections' scanned certified results",
        cite=cite,
        method=METHOD,
        pages=pages,
    )
