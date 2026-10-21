import json
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

SOURCE = "us.nc_abstract_1988"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Abstract of votes cast for Council of State at the general election held on November 8, 1988"
RECORD = "https://digital.ncdcr.gov/Documents/Detail/4199398"
DAY = date(1988, 11, 8)
HELD = {"Commissioner of Agriculture"}
SHEETS = {
    (1, 2): [
        ("Secretary of State", [("Rufus L. Edmisten", "DEMOCRATIC"), ("John H. Carrington", "REPUBLICAN")]),
        ("Attorney General", [("Lacy H. Thornburg", "DEMOCRATIC"), ("Sam Wilson", "REPUBLICAN")]),
        ("Commissioner of Labor", [("John C. Brooks", "DEMOCRATIC"), ("Richard D. (Dick) Levy", "REPUBLICAN")]),
        ("Treasurer", [("Harlan E. Boyles", "DEMOCRATIC"), ("Nancy Lake Coward", "REPUBLICAN")]),
    ],
    (3, 4): [
        ("Auditor", [("Edward Renfrow", "DEMOCRATIC"), ("Edward Garner, Jr.", "REPUBLICAN")]),
        ("Commissioner of Agriculture", [("James A. (Jim) Graham", "DEMOCRATIC"), ("Leo Tew", "REPUBLICAN")]),
        ("Commissioner of Insurance", [("Jim Long", "DEMOCRATIC"), ("H. L. (Pete) Rednour", "REPUBLICAN")]),
        ("Superintendent of Public Instruction", [("Bob Etheridge", "DEMOCRATIC"), ("Tom Rogers", "REPUBLICAN")]),
    ],
}
METHOD = (
    "County totals come from the State Board of Elections' abstract of votes cast for the Council of State at the 1988 "
    "general election (a dot-matrix scan), transcribed from page images in two independent reads and checked so that "
    "every candidate's county sum equals the printed state total."
)


def url(page: int) -> str:
    return f"{RECORD}#page-{page:02d}"


def addresses() -> list[str]:
    return [url(page) for pair in SHEETS for page in pair]


def fetch() -> None:
    detail = subprocess.run(
        ["curl", "-sL", "-m", "60", "-A", "Mozilla/5.0", RECORD], capture_output=True, text=True, check=True
    ).stdout
    items = json.loads(re.search(r"var viewerItems = (\[.*?\]);\n", detail, re.S)[1])
    for item in items:
        number = int(item["path"].rsplit("-", 1)[1])
        pdf = subprocess.run(
            ["curl", "-sL", "-m", "120", "-A", "Mozilla/5.0", item["pdfUrl"].replace("[IMAGETOKEN]", item["imageToken"])],
            capture_output=True,
            check=True,
        ).stdout
        raw.store(SOURCE, url(number), pdf, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%%'"""
            ).fetchall()
        }
    table = pl.read_csv(Path(__file__).with_suffix(".tsv"), separator="\t")
    rows: list[tuple[str, str, str, str, int]] = []
    for pair, offices in SHEETS.items():
        body = table.filter(pl.col("page").is_in(pair))
        total = body.filter(pl.col("county") == "TOTAL")
        body = body.filter(pl.col("county") != "TOTAL")
        if body.height != 100:
            raise ValueError(f"pages {pair}: {body.height} counties")
        start = 0
        for office, candidates in offices:
            for offset, (candidate, party) in enumerate(candidates):
                column = f"c{start + offset}"
                if office in HELD:
                    continue
                if body[column].sum() != total[column].item():
                    raise ValueError(f"{office} {candidate}: {body[column].sum()} against printed {total[column].item()}")
                for name, votes in zip(body["county"], body[column], strict=True):
                    rows.append((office, candidate, party, codes[re.sub(r"[^a-z]", "", name.lower())], votes))
            start += len(candidates)
    frame = pl.DataFrame(rows, schema=["office", "candidate", "party", "county", "votes"], orient="row").select(
        year=pl.lit(1988, pl.Int32),
        day=pl.lit(str(DAY)),
        state=pl.lit("NC"),
        title="office",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate=pl.col("candidate").str.to_uppercase(),
        party="party",
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(100),
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
                url=RECORD,
                license="Public record",
            )
        for address in addresses():
            cs.artifact(raw.cached(SOURCE, address), source)
        return {"NC": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="North Carolina's 1988 secretary of state, auditor, treasurer, superintendent of public instruction, "
        "attorney general, and commissioners of insurance and labor by county from the State Board of "
        "Elections' abstract of votes cast",
        cite=cite,
        method=METHOD,
    )
