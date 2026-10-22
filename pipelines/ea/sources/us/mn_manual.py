import difflib
import html
import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mn_manual"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.lrl.mn.gov/archive/sessions/electionresults/2000-11-07-g-man.pdf"
DAY = date(2000, 11, 7)
PAGES = (17, 18)
DPI = 100
DISTRICTS = {
    "FIRST": (1, ["Mary Rieder", "Gil Gutknecht", "Rich Osness"]),
    "SECOND": (2, ["Dennis A. Burda", "Gerald W. Brekke", "David Minge", "Mark Kennedy", "Ron Helwig"]),
    "THIRD": (3, ["Arne Niska", "Sue Shuff", "Jim Ramstad", "Bob Odden"]),
    "FOURTH": (4, ["Nicholas Skrivanek", "Tom Foley", "Betty McCollum", "Linda Runbeck"]),
    "FIFTH": (5, ["Renee Lavoi", "Rob Tomich", "Martin Olav Sabo", "Frank Taylor", "Chuck P. Charnstrom"]),
    "SIXTH": (6, ["Ralph A. Hubbard", "Bill Luther", "John Kline"]),
    "SEVETH": (7, ["Owen Sivertson", "Collin C. Peterson", "Glen Menze"]),
    "EIGHTH": (8, ["James L. Oberstar", "Bob Lemen", "Mike Darling"]),
}
PUBLISHER = "Minnesota Legislative Reference Library"
TITLE = "Minnesota Legislative Manual 2001–2002, election results"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    pdf = raw.cached(SOURCE, URL).path
    with connect() as conn:
        counties = {
            name.upper().removesuffix(" COUNTY, MN").replace("ST. ", "SAINT "): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '27%' and u.valid @> '2000-11-07'::date"""
            ).fetchall()
        }
    rows = []
    districts: dict[bool, tuple[int, list[str]] | None] = {False: None, True: None}
    for number in PAGES:
        page = subprocess.run(
            ["pdftotext", "-bbox", "-f", str(number), "-l", str(number), str(pdf), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        width, height = (
            float(value) for value in re.search(r'<page width="([\d.]+)" height="([\d.]+)"', page).groups()
        )
        words = [
            (float(x0), float(y0), float(x1), float(y1), html.unescape(text))
            for x0, y0, x1, y1, text in re.findall(
                r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', page
            )
            if float(x0) < width - 30
        ]
        for side in (False, True):
            lines: list[list[tuple[float, float, float, float, str]]] = []
            for word in sorted((word for word in words if (word[0] >= width * 0.44) == side), key=lambda word: word[1]):
                if lines and word[1] - lines[-1][-1][1] < 3.5:
                    lines[-1].append(word)
                else:
                    lines.append([word])
            for line in lines:
                line.sort()
                texts = [word[4] for word in line]
                if len(texts) >= 2 and texts[1] == "DISTRICT" and texts[0] in DISTRICTS:
                    districts[side] = DISTRICTS[texts[0]]
                    continue
                district = districts[side]
                numbers = [word for word in line if re.fullmatch(r"[\d,]+", word[4])]
                label = " ".join(
                    word[4] for word in line if word not in numbers and re.fullmatch(r"[A-Za-z.]+", word[4])
                )
                if district is None or not numbers or not label:
                    continue
                if len(numbers) != len(district[1]):
                    raise ValueError(
                        f"page {number}, district {district[0]}, {label}: {len(numbers)} numbers for {len(district[1])} candidates"
                    )
                if label == "TOTAL":
                    county = None
                else:
                    match = difflib.get_close_matches(label.upper(), counties, n=1, cutoff=0.6)
                    if not match:
                        raise ValueError(f"district {district[0]}: no county for {label}")
                    county = counties[match[0]]
                for candidate, (x0, y0, x1, y1, text) in zip(district[1], numbers, strict=True):
                    rows.append(
                        (district[0], county, label, candidate, int(text.replace(",", "")), number,
                         x0 / width, y0 / height, x1 / width, y1 / height)
                    )  # fmt: skip
    frame = pl.DataFrame(
        rows,
        schema={
            "district": pl.Int16,
            "code": pl.String,
            "label": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "page": pl.Int16,
            "x0": pl.Float64,
            "y0": pl.Float64,
            "x1": pl.Float64,
            "y1": pl.Float64,
        },
        orient="row",
    )
    totals = frame.filter(pl.col("code").is_null())
    sums = frame.filter(pl.col("code").is_not_null()).group_by("district", "candidate").agg(pl.col("votes").sum())
    checked = totals.join(sums, on=["district", "candidate"], how="full", suffix="_sum")
    if (
        checked.height != sum(len(names) for _, names in DISTRICTS.values())
        or (checked["votes"] != checked["votes_sum"]).any()
    ):
        raise ValueError(f"county rows do not add up to the printed totals:\n{checked}")
    if frame.filter(pl.col("code").is_not_null()).select("district", "code", "candidate").is_duplicated().any():
        raise ValueError("a county appears twice in a district")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("code").is_not_null()).drop("label").write_parquet(PARSED / "counties.parquet")
    print(totals.select("district", "candidate", "votes").rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's 2000 U.S. House generals by county from the Legislative Manual's canvass tables",
            polity="US-MN",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.lrl.mn.gov/mngov/electionresults",
                license="Public record",
            )
        artifact = cs.artifact(raw.cached(SOURCE, URL), source)
        pages = {}
        for number in PAGES:
            target = DATA / "pages" / SOURCE / f"{raw.cached(SOURCE, URL).path.stem}-{number}.png"
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(raw.cached(SOURCE, URL).path), str(target.with_suffix(""))],
                    check=True,
                )
            with Image.open(target) as rendered:
                width, height = rendered.size
            pages[number] = str(cs.page(artifact, number))
            if UUID(pages[number]) in cs.minted:
                cs.add(
                    "source_page",
                    id=UUID(pages[number]),
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{target.name}",
                    width=width,
                    height=height,
                )
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet")
            .filter(pl.col("votes").sum().over("district", "code") > 0)
            .select(
                year=pl.lit(2000, pl.Int32),
                chamber=pl.lit("house"),
                day=pl.lit(DAY),
                special=pl.lit(False),
                state=pl.lit("MN"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("MN"),
                page_id=pl.col("page").replace_strict(pages, return_dtype=pl.String),
                x0="x0",
                y0="y0",
                x1="x1",
                y1="y1",
            ),
            {"MN": source},
            strict=True,
        )
        return cs.write()
