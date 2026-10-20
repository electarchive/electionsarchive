import io
import re
import zipfile
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.ar_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.arkansas.gov/uploads/elections/"
FILES = {
    "1994-11-08": BASE + "94general_election_results.xls",
    "1998-11-03": BASE + "Gen98Ver5.zip",
}
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Summary of general election results by county"
OFFICES = {
    "Lieutenant Governor": "Lieutenant Governor",
    "Secretary of State": "Secretary of State",
    "Attorney General": "Attorney General",
    "Audior of State": "Auditor",
    "Auditor of State": "Auditor",
}
METHOD = (
    "County totals come from the Secretary of State's summary of the general election results by county, "
    "each candidate's county sum equal to the sheet's printed grand total."
)


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".zip" if url.endswith(".zip") else ".xls")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '05%'"""
            ).fetchall()
        }
    rows = []
    for day, url in FILES.items():
        data = raw.cached(SOURCE, url).path.read_bytes()
        if url.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                data = archive.read(next(name for name in archive.namelist() if name.lower().endswith(".xls")))
        table = fastexcel.read_excel(data).load_sheet("Summary", header_row=None).to_polars().rows()
        header = [str(cell) for cell in table[0]]
        columns = [header.index(name) for name in header if re.sub(r"[^a-z]", "", name.lower()) in codes]
        if len(columns) != 75:
            raise ValueError(f"{day}: {len(columns)} county columns")
        total = header.index("Grand Totals")
        office = None
        for row in table[1:]:
            filled = [cell for cell in row if cell is not None and str(cell).strip()]
            if len(filled) == 1:
                office = OFFICES.get(str(filled[0]).strip())
                continue
            if office is None or row[1] is None or str(row[1]).strip() not in ("Democrat", "Republican", "Independent", "Reform", "Libertarian", "Green", "Write-In"):
                continue
            name = re.sub(
                r"^(?:Lt\. Governor|Secretary of State|Attorney General|Governor|Auditor of State)\s+", "", str(row[0]).strip()
            )
            votes = [int(float(row[column])) for column in columns]
            if sum(votes) != int(float(row[total])):
                raise ValueError(f"{day} {office} {name}: counties sum to {sum(votes)}, printed {row[total]}")
            party = str(row[1]).strip()
            rows.extend(
                (day, office, re.sub(r"\s+", " ", name).upper(), party.upper() if party != "Democrat" else "DEMOCRATIC", header[column], count)
                for column, count in zip(columns, votes, strict=True)
            )
    frame = pl.DataFrame(rows, schema=["day", "title", "candidate", "party", "county", "votes"], orient="row").select(
        year=pl.col("day").str.slice(0, 4).cast(pl.Int32),
        day="day",
        state=pl.lit("AR"),
        title="title",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(codes),
        candidate=pl.when(pl.col("party") == "WRITE-IN").then(pl.lit("WRITE-INS")).otherwise("candidate"),
        party=pl.when(pl.col("party") == "WRITE-IN").then(pl.lit(None, pl.String)).otherwise("party"),
        writein=pl.col("party") == "WRITE-IN",
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(75),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("year", "title", "candidate", "party").agg(pl.col("votes").sum()).sort("year", "title", "votes").rows())


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
                url="https://sos.arkansas.gov/elections/research/election-results",
                license="Public record",
            )
        for url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        return {"AR": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Arkansas's 1994 lieutenant governor, secretary of state, attorney general, and auditor and 1998 "
        "lieutenant governor, secretary of state, and attorney general by county from the Secretary of State's "
        "summaries of the general election results",
        cite=cite,
        method=METHOD,
    )
