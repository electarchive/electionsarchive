import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mi_canvass_2020"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.legislature.mi.gov/documents/2021-2022/michiganmanual/2021-MM-P0715-P0718.pdf"
DAY = date(2020, 8, 4)
PUBLISHER = "Michigan Legislature"
TITLE = "Michigan Manual 2021-2022: August 4, 2020 primary election, U.S. House by county"
CITATION = "Source: the Board of State Canvassers' canvass of the August 4, 2020 primary as printed in the 2021-2022 Michigan Manual (pages 715-718), 1st Congressional District."
ROW = re.compile(r"^\s*([A-Z][A-Za-z. ]*?)(?:\s*\.)*\s{2,}([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st ")): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '26%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    block = text[text.index("1st Congressional District") : text.index("2nd Congressional District")]
    rows = []
    totals: list[int] = []
    for line in block.splitlines():
        match = ROW.match(line)
        if match is None:
            continue
        name = re.sub(r"[\s.]+$", "", match.group(1)).strip()
        values = [int(value.replace(",", "")) for value in match.groups()[1:]]
        if name == "Total":
            totals = values
            continue
        if values[0] != sum(values[1:]):
            raise ValueError(f"{name}: candidates sum to {sum(values[1:])}, county total {values[0]}")
        code = counties[re.sub(r"[^a-z]", "", name.lower())]
        rows.extend(
            (party, candidate, code, count)
            for (party, candidate), count in zip(
                (("republican", "JACK BERGMAN"), ("democratic", "DANA FERGUSON"), ("democratic", "LINDA O'DELL")),
                values[1:],
                strict=True,
            )
        )
    frame = pl.DataFrame(rows, schema=["party", "candidate", "code", "votes"], orient="row")
    sums = frame.group_by("candidate").agg(pl.col("votes").sum())
    if sorted(sums["votes"].to_list()) != sorted(totals[1:]):
        raise ValueError(f"counties sum to {sums.rows()}, printed {totals}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        year=pl.lit(2020, pl.Int32),
        chamber=pl.concat_str(pl.lit("house:"), "party"),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("MI"),
        district=pl.lit(1, pl.Int16),
        code="code",
        candidate="candidate",
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        votes=pl.col("votes").cast(pl.Int64),
        kind=pl.lit("county"),
        source=pl.lit("MI"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Michigan's August 4, 2020 1st District primaries by county from the Board of State Canvassers' canvass in the Michigan Manual",
            polity="US-MI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, frame, {"MI": source}, strict=True, authoritative=CITATION)
        return cs.write()
