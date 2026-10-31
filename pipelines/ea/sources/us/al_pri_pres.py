import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_pri_pres"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.sos.alabama.gov/sites/default/files/election-data/2017-06/eapresidentprimary1980-2012.xls"
SHEETS = {
    "Rep.Pri.12": (date(2012, 3, 13), "republican"),
    "Dem.Pri.08": (date(2008, 2, 5), "democratic"),
    "Rep.Pri.08": (date(2008, 2, 5), "republican"),
}
PUBLISHER = "Alabama Secretary of State"
TITLE = "Presidential primary results by county, 1980-2012"


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.fetch(SOURCE, URL, suffix=".xls")


def key(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def parse() -> None:
    with connect() as conn:
        fips = {
            key(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
    rows = []
    for name, (day, party) in SHEETS.items():
        sheet = pl.read_excel(
            raw.cached(SOURCE, URL).path, sheet_name=name, has_header=False, infer_schema_length=0
        ).rows()
        head = next(
            i
            for i, line in enumerate(sheet)
            if key(line[0] or "") == "county" and (line[1] or "").strip() not in ("", "Pres 12")
        )
        names = {}
        for column, cell in enumerate(sheet[head][1:], start=1):
            if key(cell or "") in ("total", ""):
                break
            names[column] = cell.strip()
        for line in sheet[head + 1 :]:
            if line[0] and key(line[0]) in fips:
                for column, candidate in names.items():
                    rows.append(
                        (
                            day,
                            f"president:{party}",
                            candidate.upper(),
                            fips[key(line[0])],
                            int(float(line[column] or 0)),
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={"day": pl.Date, "chamber": pl.String, "candidate": pl.String, "code": pl.String, "votes": pl.Int64},
            orient="row",
        )
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("AL"),
            district=pl.lit(None, pl.Int16),
            measure=pl.lit(1, pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("county"),
            source=pl.lit("AL"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2008 and 2012 presidential primaries by county",
            polity="US-AL",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.alabama.gov/alabama-votes/voter/election-data",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AL": source}, strict=True)
        return cs.write()
