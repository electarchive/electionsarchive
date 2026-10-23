import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mt_leg_pri_2014"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Montana Secretary of State"
TITLE = "2014 legislative primary election canvass"
URL = "https://web.archive.org/web/20140628172109id_/http://sos.mt.gov/Elections/2014/2014-Primary-Official-Legislative-Canvass.pdf"
DAY = date(2014, 6, 3)
PARTIES = {"Democrat": "dp", "Republican": "rp"}
ROW = re.compile(r"(?:(HD|SD) 0*(\d+))?\s+([A-Z][A-Za-z &.']+?)\s{2,}([\d,]+(?:\s+[\d,]+)*)")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]|\band\b", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '30%%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    lines = [
        line for line in text.splitlines() if line.strip() and not re.search(r"Page \d+ of|CANVASS|SECRETARY", line)
    ]
    rows = []
    parties: list[str] = []
    names: list[str] = []
    chamber = ""
    parts: list[tuple[str, list[int]]] = []
    for index, line in enumerate(lines):
        chunks = re.split(r"\s{2,}", line.strip())
        if all(chunk in (*PARTIES, "Libertarian") for chunk in chunks):
            following = re.split(r"\s{2,}", lines[index + 1].strip())
            if (parties, names) != (chunks, following):
                parties, names, parts = chunks, following, []
        elif (row := ROW.fullmatch(line)) and parties:
            votes = [int(number.replace(",", "")) for number in row[4].split()]
            if row[1]:
                chamber = f"{'house' if row[1] == 'HD' else 'senate'}:{int(row[2])}"
            if row[3] != "TOTAL":
                parts.append((counties[re.sub(r"[^a-z]", "", row[3].lower())], votes))
                continue
            sums = [sum(part[column] for _, part in parts) for column in range(len(votes))]
            if not (len(parties) == len(names) == len(votes)) or sums != votes:
                print(f"{chamber}: {parties} {names} sum {sums} printed {votes}, skipped")
            else:
                rows.extend(
                    (f"{chamber}:{PARTIES[party]}", code, name, part[column])
                    for code, part in parts
                    for column, (party, name) in enumerate(zip(parties, names, strict=True))
                    if party in PARTIES
                )
            parties = []
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows, schema={"chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    )
    print(frame.select(pl.col("chamber").n_unique(), pl.col("code").n_unique()))
    frame.with_columns(
        candidate=pl.col("candidate")
        .str.replace(r",?\s+(JR|SR|II|III|IV)\.?$", "")
        .str.replace("BEAR DON'T WALK", "BEARDONTWALK"),
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("MT"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("MT"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Montana's 2014 State House and Senate primaries by county from the Secretary of State's canvass (via the Wayback Machine)",
            polity="US-MT",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://sosmt.gov/elections/results/",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MT": source}, strict=True)
        return cs.write()
