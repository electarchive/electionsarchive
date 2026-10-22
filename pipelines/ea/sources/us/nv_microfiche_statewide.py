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

SOURCE = "us.nv_microfiche_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Nevada Secretary of State"
TITLE = "1994 General Election Returns"
LICENSE = "Public record"
ITEM = "micro_IA40706953_0068"
URL = f"https://archive.org/download/{ITEM}/{ITEM}.pdf"
LANDING = f"https://archive.org/details/{ITEM}"
DAY = date(1994, 11, 8)
METHOD = (
    "County totals come from the Secretary of State's 1994 General Election Returns, a microfiche scan, read cell by cell by "
    "OCR at several scales. Each row's seventeen county cells add up to the row's printed total; five cells the OCR "
    "could not settle were read from the image and checked the same way."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def image(page: int) -> Path:
    book = raw.cached(SOURCE, URL)
    target = PAGES / f"{book.sha256}-{page}.png"
    if not target.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(book.path), str(target.with_suffix(""))],
            check=True,
        )
    return target


def parse() -> None:
    with connect() as conn:
        fips = dict(
            conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '32%%'"""
            ).fetchall()
        )
    codes = {text.lower().replace(" ", ""): code for text, code in fips.items()}
    scan = pl.read_csv(Path(__file__).with_name("nv_microfiche_statewide.tsv"), separator="\t", schema_overrides={"party": pl.String})
    sums = scan.group_by("office", "name").agg(pl.col("votes").sum(), pl.col("total").first(), pl.len())
    bad = sums.filter((pl.col("votes") != pl.col("total")) | (pl.col("len") != 17))
    if bad.height:
        raise ValueError(f"rows that do not add up: {bad}")
    frame = scan.select(
        year=pl.col("year").cast(pl.Int32),
        day=pl.lit(DAY),
        state=pl.lit("NV"),
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
        counties=pl.lit(17),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate", "party").agg(pl.col("votes").sum()).sort("title", "votes").rows())


def emit() -> Path:
    def pages(cs: Changeset) -> dict[int, tuple[UUID, int, int]]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=LANDING, license=LICENSE
            )
        artifact = cs.artifact(raw.cached(SOURCE, URL), source)
        result: dict[int, tuple[UUID, int, int]] = {}
        for key in sorted(set(pl.read_parquet(PARSED / "returns.parquet")["page"])):
            number = key % 1000
            target = image(number)
            with Image.open(target) as rendered:
                width, height = rendered.size
            page = cs.page(artifact, number)
            result[key] = (page, width, height)
            cs.add(
                "source_page", id=page, artifact_id=artifact, page_number=number,
                image_key=f"pages/{SOURCE}/{target.name}", width=width, height=height,
            )
        return result

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Nevada's 1994 lieutenant governor, secretary of state, treasurer, controller, and attorney general by county "
        "from the Secretary of State's General Election Returns",
        cite=lambda cs: {"NV": cs.source(PUBLISHER, TITLE)},
        method=METHOD,
        pages=pages,
    )
