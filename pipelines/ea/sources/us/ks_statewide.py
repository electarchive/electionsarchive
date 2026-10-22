import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.ks_statewide"
RAW = "us.sri"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Kansas Secretary of State"
TITLE = "Election Statistics, State of Kansas, {year}"
LICENSE = "Public record"
ITEMS = {1990: "micro_IA40706938_0450", 1994: "micro_IA40706952_0371"}
URLS = {year: f"https://archive.org/download/{item}/{item}.pdf" for year, item in ITEMS.items()}
LANDINGS = {year: f"https://archive.org/details/{item}" for year, item in ITEMS.items()}
DAYS = {1990: date(1990, 11, 6), 1994: date(1994, 11, 8)}
DPI = 150
METHOD = (
    "County totals come from the Secretary of State's Election Statistics volume for the year, a microfiche scan, "
    "transcribed from the page images by two independent reads (OCR and a vision model) that were reconciled "
    "against each other; every candidate's 105 county values add up to the TOTAL row printed under the column, "
    "and each value carries a box on its page."
)


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(RAW, url, suffix=".pdf")


def image(year: int, page: int) -> Path:
    book = raw.cached(RAW, URLS[year])
    target = PAGES / f"{book.sha256}-{page}.png"
    if not target.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(book.path), str(target.with_suffix(""))],
            check=True,
        )
    return target


def parse() -> None:
    with connect() as conn:
        fips = dict(
            conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '20%%'"""
            ).fetchall()
        )
    codes = {text.lower().replace(" ", ""): code for text, code in fips.items()}
    scan = pl.read_csv(Path(__file__).with_name("ks_statewide.tsv"), separator="\t", schema_overrides={"party": pl.String})
    sums = scan.group_by("year", "office", "name").agg(pl.col("votes").sum(), pl.col("total").first(), pl.len())
    bad = sums.filter((pl.col("votes") != pl.col("total")) | (pl.col("len") != 105))
    if bad.height:
        raise ValueError(f"rows that do not add up: {bad}")
    frame = scan.select(
        year=pl.col("year").cast(pl.Int32),
        day=pl.col("year").replace_strict(DAYS),
        state=pl.lit("KS"),
        title="office",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county=pl.col("county").str.to_lowercase().replace_strict(codes),
        candidate=pl.col("name").str.to_uppercase(),
        party=pl.col("party").fill_null("").replace("", None),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
        page=pl.col("year") * 1000 + pl.col("page"),
        x0="x0",
        y0="y0",
        x1="x1",
        y1="y1",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(105),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate", "party").agg(pl.col("votes").sum()).sort("title", "votes").rows())


def emit() -> Path:
    def pages(cs: Changeset) -> dict[int, tuple[UUID, int, int]]:
        result: dict[int, tuple[UUID, int, int]] = {}
        artifacts: dict[int, UUID] = {}
        for year, url in URLS.items():
            title = TITLE.format(year=year)
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add(
                    "source", id=source, kind="official_body", publisher=PUBLISHER, title=title,
                    url=LANDINGS[year], license=LICENSE,
                )
            artifacts[year] = cs.artifact(raw.cached(RAW, url), source)
        for key in sorted(set(pl.read_parquet(PARSED / "returns.parquet")["page"])):
            year, number = divmod(key, 1000)
            target = image(year, number)
            with Image.open(target) as rendered:
                width, height = rendered.size
            page = cs.page(artifacts[year], number)
            result[key] = (page, width, height)
            cs.add(
                "source_page", id=page, artifact_id=artifacts[year], page_number=number,
                image_key=f"pages/{SOURCE}/{target.name}", width=width, height=height,
            )
        return result

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Kansas's 1990 and 1994 secretary of state, attorney general, insurance commissioner, and treasurer by "
        "county from the Secretary of State's Election Statistics volumes",
        cite=lambda cs: {"KS": cs.source(PUBLISHER, TITLE.format(year=1994))},
        method=METHOD,
        pages=pages,
    )
