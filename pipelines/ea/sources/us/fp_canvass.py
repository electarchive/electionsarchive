import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ar_abstract_2002, medsl
from ea.store import DATA, connect

SOURCE = "us.fp_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DOCUMENTS = {
    "AR": (
        ar_abstract_2002.SOURCE,
        ar_abstract_2002.URL,
        ar_abstract_2002.PUBLISHER,
        ar_abstract_2002.TITLE,
        "https://sos.arkansas.gov/elections/research/election-results",
    ),
}


def counties(conn: Connection, fips: str) -> dict[str, str]:
    return {
        re.sub(r"[^a-z]", "", text.lower()): code
        for text, code in conn.execute(
            """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like %s""",
            [f"{fips}%"],
        ).fetchall()
    }


def arkansas(conn: Connection) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, "05")
    source, url, *_ = DOCUMENTS["AR"]
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(source, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    line = re.compile(r"^(?:Governor )?(.+?) - (Democrat|Republican|Write-In)\s+([\d,]+)\s+[\d.]+%$")
    county = None
    statewide: dict[str, int] = {}
    rows = []
    reading = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if heading := re.fullmatch(r"Governor(?: \((.+?) County\))?", stripped):
            reading, county = True, heading[1]
            continue
        if not reading:
            continue
        if match := line.match(stripped):
            name = "WRITE-INS" if match[2] == "Write-In" else match[1].upper()
            votes = int(match[3].replace(",", ""))
            if county is None:
                statewide[name] = statewide.get(name, 0) + votes
            else:
                rows.append((date(2002, 11, 5), "governor", codes[re.sub(r"[^a-z]", "", county.lower())], name, votes))
            continue
        if not stripped.startswith(("Over", "Under")):
            reading = False
    for name, total in statewide.items():
        summed = sum(votes for *_, candidate, votes in rows if candidate == name)
        if summed != total:
            raise ValueError(f"AR governor {name}: counties sum to {summed}, statewide {total}")
    if len({code for _, _, code, _, _ in rows}) != 75:
        raise ValueError("AR governor: not 75 counties")
    return rows


def parse() -> None:
    with connect() as conn:
        rows = arkansas(conn)
    frame = (
        pl.DataFrame(rows, schema=["day", "chamber", "code", "candidate", "votes"], orient="row")
        .with_columns(state=pl.lit("AR"))
        .group_by("state", "day", "chamber", "code", "candidate")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            district=pl.lit(None, pl.Int16),
            special=pl.lit(False),
            writein=pl.col("candidate") == "WRITE-INS",
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.col("state"),
        )
    )
    print(frame.group_by("state", "chamber", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("state", "votes"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governor and U.S. Senate generals by county from state canvasses, for contests whose only "
            "county results were Algara and Amlani's or ICPSR's",
            polity="US",
        )
        sources: dict[str, UUID] = {}
        for state in frame["state"].unique().to_list():
            cached, url, publisher, title, home = DOCUMENTS[state]
            sources[state] = cs.source(publisher, title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=home,
                    license="Public record",
                )
            cs.artifact(raw.cached(cached, url), sources[state])
        medsl.attach(cs, conn, frame, sources, strict=True, extend=True)
        return cs.write()
