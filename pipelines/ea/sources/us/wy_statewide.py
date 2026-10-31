import re
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

SOURCE = "us.wy_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Wyoming Secretary of State"
TITLE = "1998 Statewide Issues Abstract"
LICENSE = "Public record"
URL = "https://sos.wyo.gov/Elections/Docs/1998/98Results/98General/Statewide_Issues%20Abstract.pdf"
LANDING = "https://sos.wyo.gov/Elections/State/ElectionResults.aspx"
NAMES_URL = (
    "https://sos.wyo.gov/Elections/Docs/1998/98Results/98_Election%20Information/98_General%20Election_Candidates.pdf"
)
DAY = date(1998, 11, 3)
DPI = 150
OFFICES = {
    3: ("Secretary of State", [("Jayne Mockler", "DEMOCRATIC"), ("Joe Meyer", "REPUBLICAN")]),
    4: ("Auditor", [("Max Maxfield", "REPUBLICAN")]),
    5: (
        "Treasurer",
        [
            ("Charyl W. (Butch) Loveridge", "DEMOCRATIC"),
            ("James E. Blomquist", "LIBERTARIAN"),
            ("Cynthia M. Lummis", "REPUBLICAN"),
        ],
    ),
    6: (
        "Superintendent of Public Instruction",
        [("Gene Lane", "DEMOCRATIC"), ("Judy Catchpole", "REPUBLICAN")],
    ),
}
METHOD = (
    "County totals come from the Wyoming Elections Division's Statewide Issues Abstract for the 1998 general election, "
    "read from the PDF's text layer; each candidate's twenty-three county values add up to the printed Official Totals. "
    "The abstract prints surnames and parties only; full names come from the Secretary of State's list of 1998 general "
    "election candidates."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")
    raw.fetch(SOURCE, NAMES_URL, suffix=".pdf")


def image(page: int) -> Path:
    book = raw.cached(SOURCE, URL)
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
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '56%%'"""
            ).fetchall()
        }
    if len(codes) != 23:
        raise ValueError("Wyoming needs 23 counties")
    book = raw.cached(SOURCE, URL).path
    scale = DPI / 72
    rows: list[dict[str, object]] = []
    for page, (office, people) in OFFICES.items():
        html = subprocess.run(
            ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(book), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        lines: dict[float, list[tuple[str, float, float, float, float]]] = {}
        for x0, y0, x1, y1, text in re.findall(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', html
        ):
            key = next((known for known in lines if abs(known - float(y0)) < 3), float(y0))
            lines.setdefault(key, []).append((text, float(x0), float(y0), float(x1), float(y1)))
        printed: list[int] = []
        found: dict[str, list[tuple[int, float, float, float, float]]] = {}
        for words in lines.values():
            words.sort(key=lambda word: word[1])
            if words[0][0] == "Official":
                printed = [int(word[0].replace(",", "")) for word in words if re.fullmatch(r"[\d,]+", word[0])]
                continue
            numbers = [word for word in words if re.fullmatch(r"\d[\d,]*", word[0])]
            name = re.sub(r"[^a-z]", "", "".join(word[0] for word in words if word not in numbers).lower())
            if name in codes and len(numbers) == len(people):
                found[name] = [
                    (int(word[0].replace(",", "")), word[1] * scale, word[2] * scale, word[3] * scale, word[4] * scale)
                    for word in numbers
                ]
        if sorted(found) != sorted(codes) or len(printed) != len(people):
            raise ValueError(f"{office}: counties {sorted(set(found) ^ set(codes))}, printed {printed}")
        for column, (candidate, party) in enumerate(people):
            if sum(found[key][column][0] for key in found) != printed[column]:
                raise ValueError(f"{office} {candidate}: does not add to {printed[column]}")
            rows.extend(
                {
                    "year": 1998,
                    "day": DAY,
                    "state": "WY",
                    "title": office,
                    "post": None,
                    "special": False,
                    "magnitude": 1,
                    "kind": "county",
                    "code": "county",
                    "county": codes[key],
                    "candidate": candidate.upper(),
                    "party": party,
                    "writein": False,
                    "measure": 1,
                    "votes": found[key][column][0],
                    "page": 1998000 + page,
                    "x0": found[key][column][1] - 3,
                    "y0": found[key][column][2] - 2,
                    "x1": found[key][column][3] + 3,
                    "y1": found[key][column][4] + 2,
                }
                for key in found
            )
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(23),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate", "party").agg(pl.col("votes").sum()).sort("title", "votes").rows())


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
                url=LANDING,
                license=LICENSE,
            )
        artifact = cs.artifact(raw.cached(SOURCE, URL), source)
        cs.artifact(raw.cached(SOURCE, NAMES_URL), source)
        result: dict[int, tuple[UUID, int, int]] = {}
        for key in sorted(set(pl.read_parquet(PARSED / "returns.parquet")["page"])):
            number = key % 1000
            target = image(number)
            with Image.open(target) as rendered:
                width, height = rendered.size
            page = cs.page(artifact, number)
            result[key] = (page, width, height)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{target.name}",
                width=width,
                height=height,
            )
        return result

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Wyoming's 1998 secretary of state, auditor, treasurer, and superintendent of public instruction by county "
        "from the Elections Division's Statewide Issues Abstract",
        cite=lambda cs: {"WY": cs.source(PUBLISHER, TITLE)},
        method=METHOD,
        pages=pages,
    )
