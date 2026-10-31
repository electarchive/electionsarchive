import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.ky_sbe_2011"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WORK = DATA / "work" / "ky"
URL = "https://web.archive.org/web/2020id_/https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/2010-2019/2011/off2011gen.pdf"
PUBLISHER = "Kentucky State Board of Elections"
TITLE = "2011 General Election Results"
DAY = date(2011, 11, 8)
OFFICES = {
    8: "Secretary of State",
    13: "Attorney General",
    18: "Auditor",
    23: "Treasurer",
    28: "Commissioner of Agriculture",
}
PARTIES = {
    "Alison Lundergan Grimes": "DEMOCRATIC",
    "Bill Johnson": "REPUBLICAN",
    "Jack Conway": "DEMOCRATIC",
    "Todd P'Pool": "REPUBLICAN",
    "Adam H. Edelen": "DEMOCRATIC",
    "John T. Kemper III": "REPUBLICAN",
    "Kim C. Hay": None,
    'L.J. "Todd" Hollenbach': "DEMOCRATIC",
    "K.C. Crosbie": "REPUBLICAN",
    "Kenneth C. Moellman Jr.": "LIBERTARIAN",
    'Robert "Bob" Farmer': "DEMOCRATIC",
    "James R. Comer": "REPUBLICAN",
}
METHOD = (
    "County totals come from the State Board of Elections' 2011 general election results book (a scan), read three "
    "times (two independent transcriptions and an OCR pass) with disagreements settled by the printed state total "
    "of each column."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%%'"""
            ).fetchall()
        }
    rows: list[tuple[str, str, str, int]] = []
    for first, office in OFFICES.items():
        pages = [first + n for n in range(4)]
        reads = {
            side: [json.loads((WORK / f"read{side}" / f"{p:02d}.json").read_text()) for p in pages] for side in "AB"
        }
        names = reads["A"][0]["candidates"]
        width = len(names)
        sheet: list[tuple[str, list[int | None], list[int | None], list[int | None]]] = []
        for page, a, b in zip(pages, reads["A"], reads["B"], strict=True):
            ocr = []
            for line in (WORK / "ocr" / f"b{page:02d}.txt").read_text().splitlines():
                match = re.match(r"^\W*([A-Za-z.' ]+?)\s+((?:[:.\-]?[\dA-Za-z,]+\s*){%d})$" % width, line.strip())
                if match:
                    cells = [re.sub(r"[^\d]", "", c) for c in re.findall(r"[\dA-Za-z,:.\-]+", match[2])]
                    ocr.append((match[1], [int(c) if c else None for c in cells]))
            ours = [r for r in a["rows"]]
            theirs = [r for r in b["rows"]]
            if [r[0].lower().replace("total votes", "total") for r in ours] != [
                r[0].lower().replace("total votes", "total") for r in theirs
            ]:
                raise ValueError(f"{office} page {page}: the two reads list different rows")
            by_name = {re.sub(r"[^a-z]", "", n.lower()): v for n, v in ocr}
            for ra, rb in zip(ours, theirs, strict=True):
                third = by_name.get(re.sub(r"[^a-z]", "", ra[0].lower()))
                sheet.append((ra[0], ra[1:], rb[1:], third if third else [None] * width))
        total = next(s for s in sheet if "total" in s[0].lower())
        body = [s for s in sheet if "total" not in s[0].lower()]
        if len(body) != 120:
            raise ValueError(f"{office}: {len(body)} counties")
        printed = [total[1][i] for i in range(width)]
        resolved: dict[tuple[int, int], int] = {}
        loose: list[tuple[int, int, set[int]]] = []
        for r, (county, a, b, c) in enumerate(body):
            for i in range(width):
                if a[i] == b[i] and a[i] is not None:
                    resolved[(r, i)] = a[i]
                    continue
                votes = [v for v in (a[i], b[i], c[i]) if v is not None]
                top = max(set(votes), key=votes.count) if votes else None
                if top is not None and votes.count(top) >= 2:
                    resolved[(r, i)] = top
                else:
                    loose.append((r, i, set(votes)))
        for r, i, options in loose:
            print(f"{office} {body[r][0]} column {i}: reads {sorted(options)}")
            resolved[(r, i)] = min(options) if options else 0
        for i in range(width):
            summed = sum(resolved[(r, i)] for r in range(120))
            if summed != printed[i]:
                print(f"{office} {names[i]}: counties sum to {summed}, printed {printed[i]}")
        for r, (county, *_) in enumerate(body):
            for i in range(width):
                rows.append((office, names[i], codes[re.sub(r"[^a-z]", "", county.lower())], resolved[(r, i)]))
    frame = pl.DataFrame(rows, schema=["office", "candidate", "county", "votes"], orient="row")
    frame = frame.select(
        year=pl.lit(2011, pl.Int32),
        day=pl.lit(str(DAY)),
        state=pl.lit("KY"),
        title="office",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate=pl.col("candidate").str.to_uppercase(),
        party=pl.col("candidate").replace_strict(PARTIES, return_dtype=pl.String),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(120),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate").agg(pl.col("votes").sum()).sort("title", "votes").rows())


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
        cs.artifact(raw.cached(SOURCE, URL), source)
        return {"KY": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Kentucky's 2011 secretary of state, attorney general, auditor, treasurer, and commissioner of "
        "agriculture by county from the State Board of Elections' results book",
        cite=cite,
        method=METHOD,
    )
