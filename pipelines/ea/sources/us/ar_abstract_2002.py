import re
import subprocess
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.ar_abstract_2002"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.sos.arkansas.gov/uploads/elections/2002_General.pdf"
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Abstract of votes cast at the 2002 general election"
OFFICES = {
    "Lieutenant Governor": "Lieutenant Governor",
    "Secretary of State": "Secretary of State",
    "State Treasurer": "Treasurer",
    "Auditor of State": "Auditor",
    "Commissioner of State Lands": "Commissioner of State Lands",
}
PARTIES = {"Democrat": "DEMOCRATIC", "Republican": "REPUBLICAN", "Independent": "INDEPENDENT", "Green": "GREEN", "Libertarian": "LIBERTARIAN"}
TITLES = r"^(?:Lt\. Governor|Governor|Land Commissioner|Auditor|Representative|Rep\.|Mayor|Senator|Sen\.|Congressman|Attorney General|Judge|Justice|Secretary of State|State Treasurer|Treasurer|State Representative|State Rep\.|Commissioner)\s+"
METHOD = (
    "County totals come from the Secretary of State's abstract of votes cast at the 2002 general election, "
    "each candidate's 75 counties summing to the statewide total printed in the same document."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '05%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    header = re.compile(r"^(" + "|".join(OFFICES) + r")(?: \((.+?) County\))?\s*$")
    candidate = re.compile(r"^(.+?) - (Democrat|Republican|Write-In|Independent|Green|Libertarian)\s+([\d,]+)\s+[\d.]+%\s*$")
    office = county = None
    statewide: dict[tuple[str, str, str], int] = {}
    counties: dict[tuple[str, str, str, str], int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("Over", "Under")):
            continue
        if match := header.match(line):
            office, county = match[1], match[2]
            continue
        if match := candidate.match(line):
            if office is None:
                continue
            name = re.sub(r"\s+", " ", re.sub(TITLES, "", match[1])).upper()
            key = (office, name, match[2])
            votes = int(match[3].replace(",", ""))
            if county is None:
                statewide[key] = votes
            else:
                counties[(*key, re.sub(r"[^a-z]", "", county.lower()))] = votes
            continue
        office = county = None
    rows = []
    for (office, name, party, county), votes in counties.items():
        rows.append((OFFICES[office], codes[county], name, party, votes))
    for key, total in statewide.items():
        summed = sum(votes for (*rest, _), votes in counties.items() if tuple(rest) == key)
        found = len({county for (*rest, county) in counties if tuple(rest) == key})
        if summed != total or found != 75:
            raise ValueError(f"{key}: {found} counties sum to {summed}, statewide {total}")
    if {key for key in (tuple(rest) for (*rest, _) in counties)} != set(statewide):
        raise ValueError("county sections and statewide section name different candidates")
    frame = pl.DataFrame(rows, schema=["title", "county", "candidate", "party", "votes"], orient="row").select(
        year=pl.lit(2002, pl.Int32),
        day=pl.lit("2002-11-05"),
        state=pl.lit("AR"),
        title="title",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate=pl.when(pl.col("party") == "Write-In").then(pl.lit("WRITE-INS")).otherwise("candidate"),
        party=pl.col("party").replace_strict(PARTIES, default=None),
        writein=pl.col("party") == "Write-In",
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    ).group_by(
        "year", "day", "state", "title", "post", "special", "magnitude", "kind", "code", "county", "candidate", "party", "writein", "measure"
    ).agg(pl.col("votes").sum())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(75),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate", "party").agg(pl.col("votes").sum()).sort("title", "votes").rows())


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
        cs.artifact(raw.cached(SOURCE, URL), source)
        return {"AR": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Arkansas's 2002 lieutenant governor, secretary of state, treasurer, auditor, and land commissioner "
        "by county from the Secretary of State's abstract of votes cast",
        cite=cite,
        method=METHOD,
    )
