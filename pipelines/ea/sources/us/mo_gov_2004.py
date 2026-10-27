import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mo_gov_2004"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Missouri Secretary of State"
TITLE = "County reporting, official election returns, November 2, 2004"
URL = "https://web.archive.org/web/2015id_/https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/CountyGeneral2004.pdf"
PART2 = "https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/CountyGeneral2004Part2.pdf"
DAY = date(2004, 11, 2)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")
    try:
        raw.cached(SOURCE, PART2)
    except LookupError:
        response = httpx.get(
            PART2, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0 Safari/537.36"}
        )
        response.raise_for_status()
        raw.store(
            SOURCE, PART2, response.content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
        )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%%'"""
            ).fetchall()
        }
    codes["stlouiscity"] = "29510"
    codes["stlouiscounty"] = "29189"
    codes["kansascity"] = codes["jackson"]
    text = "\n".join(
        subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, address).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for address in (URL, PART2)
    )
    units: dict[str, dict[str, int]] = {}
    county = None
    reading = False
    for line in text.splitlines():
        if heading := re.fullmatch(r" {5}([A-Z][A-Z .']+)", line):
            if heading[1].strip() != "OFFICE CANDIDATE":
                county, reading = heading[1].strip(), False
        elif re.fullmatch(r" {5}Governor(?:\s+Precincts Reporting.*)?", line):
            reading = True
            units[county] = {}
        elif reading and (row := re.fullmatch(r"\s+(\S.*?)\s{2,}([A-Z]{2,3})\s+([\d,]+)\s+[\d.]*%", line)):
            candidate = "WRITE-INS" if row[2] == "WI" else row[1].split(",")[0].upper().replace(" ", "")
            units[county][candidate] = units[county].get(candidate, 0) + int(row[3].replace(",", ""))
        elif reading and (row := re.fullmatch(r"\s+Total Votes\s+([\d,]+)", line)):
            if sum(units[county].values()) != int(row[1].replace(",", "")):
                raise ValueError(f"{county}: rows do not sum to {row[1]}")
            reading = False
    totals: dict[tuple[str, str], int] = {}
    for unit, votes in units.items():
        for candidate, count in votes.items():
            key = (codes[re.sub(r"[^a-z]", "", unit.lower())], candidate)
            totals[key] = totals.get(key, 0) + count
    if len({code for code, _ in totals}) != 115:
        raise ValueError(f"{len({code for code, _ in totals})} counties")
    frame = pl.DataFrame(
        [(DAY, code, candidate, votes) for (code, candidate), votes in totals.items()],
        schema=["day", "code", "candidate", "votes"],
        orient="row",
    )
    print(frame.group_by("candidate").agg(pl.col("votes").sum(), pl.len()))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        chamber=pl.lit("governor"),
        year=pl.lit(2004, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("MO"),
        writein=pl.col("candidate") == "WRITE-INS",
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("MO"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's 2004 governor general by county from the Secretary of State's county reporting of "
            "the official returns",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.sos.mo.gov/elections/results",
                license="Public record",
            )
        for address in (URL, PART2):
            cs.artifact(raw.cached(SOURCE, address), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet"),
            {"MO": source},
            strict=True,
            extend=True,
            authoritative="Algara and Amlani's statewide totals for Blunt and McCaskill do not match the certified returns.",
        )
        return cs.write()
