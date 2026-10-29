import re
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.sd_pres_pri"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGE = "https://electionresults.sd.gov/ResultsExport.aspx?eid=177"
DAY = date(2016, 6, 7)
PUBLISHER = "South Dakota Secretary of State"
TITLE = "Unofficial results: 2016 Primary Election, statewide export"


def fetch() -> None:
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36"}, timeout=90) as client:
        form = {name: value for name, value in re.findall(r'name="(__[A-Z]+)"[^>]*value="([^"]*)"', client.get(PAGE).text)}
        form["__EVENTTARGET"] = "ctl00$MainContent$Statewide"
        response = client.post(PAGE, data=form)
        response.raise_for_status()
    raw.store(SOURCE, PAGE, response.content, suffix=".xlsx", retrieved_at=datetime.now(UTC), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def parse() -> None:
    with connect() as conn:
        counties = {
            name.removesuffix(", SD").removesuffix(" County").lower(): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '46%%' and u.valid @> '2016-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for sheet in pl.read_excel(raw.cached(SOURCE, PAGE).path, has_header=False, sheet_id=0, infer_schema_length=0).values():
        start = next(i for i, county in enumerate(sheet["column_1"]) if county == "County")
        title = sheet["column_0"][start]
        party = "democratic" if title.startswith("Presidential Delegates") else "republican" if title.startswith("Delegates to Support") else None
        if party is None:
            continue
        names = [name for name in sheet.row(start)[2:] if name]
        for county, *votes in sheet.select(pl.exclude("column_0")).slice(start + 1).iter_rows():
            if county is None or county == "TOTALS":
                continue
            code = counties[re.sub(r"\s*\(.*\)$", "", county).lower()]
            for candidate, value in zip(names, votes, strict=False):
                rows.append((party, code, candidate, int(value)))
    frame = pl.DataFrame(
        rows, schema={"party": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    )
    if frame.select("party", "code", "candidate").is_duplicated().any():
        raise ValueError("a county appears twice for a candidate")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("party").agg(pl.col("code").n_unique()).rows(), frame.group_by("party", "candidate").agg(pl.col("votes").sum()).rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Dakota's 2016 Democratic and Republican presidential primaries by county from the Secretary of State's results export",
            polity="US-SD",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, PAGE), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.lit(DAY.year, pl.Int32),
                chamber=pl.lit("president:") + pl.col("party"),
                day=pl.lit(DAY),
                special=pl.lit(False),
                state=pl.lit("SD"),
                district=pl.lit(None, pl.Int16),
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("candidate").str.contains("(?i)write"),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("SD"),
            ),
            {"SD": source},
            strict=True,
        )
        return cs.write()
