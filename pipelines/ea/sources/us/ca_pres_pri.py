import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.ca_pres_pri"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://elections.cdn.sos.ca.gov/sov/"
PUBLISHER = "California Secretary of State"
FILES = {
    2004: (date(2004, 3, 2), ["2004-primary/pres_dem_formatted.xls", "2004-primary/pres_all.xls"]),
    2008: (date(2008, 2, 5), ["2008-primary/presidential.xls"]),
    2012: (date(2012, 6, 5), ["2012-primary/28-president-formatted.xls"]),
    2020: (date(2020, 3, 3), ["2020-primary/sov/29-presidential-formatted.xls"]),
}
PARTIES = {
    "DEM": "democratic",
    "REP": "republican",
    "AI": "american-independent",
    "GRN": "green",
    "LIB": "libertarian",
    "PF": "peace-and-freedom",
}
SKIPPED = ("votes not cast", "party vote", "non-party vote")


def fetch() -> None:
    for _, names in FILES.values():
        for name in names:
            raw.fetch(SOURCE, SITE + name, suffix=".xls")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["CA"]
    rows = []
    for year, (day, names) in FILES.items():
        for file in names:
            cells = pl.read_excel(
                raw.cached(SOURCE, SITE + file).path,
                engine="calamine",
                has_header=False,
                sheet_id=1,
                infer_schema_length=0,
            ).rows()
            keep = []
            for index, (name, tag) in enumerate(zip(cells[0][1:], cells[1][1:], strict=True)):
                tag = " ".join((tag or "").split())
                if not tag or " ".join((name or "").lower().split()).startswith(SKIPPED) or "Party Vote" in tag or "Non-Party" in tag:
                    continue
                if tag.split()[0] not in PARTIES:
                    raise ValueError(f"{year} {file}: unknown party {tag}")
                candidate = re.sub(r",\s*(Jr\.?|Sr\.?|II|III|IV)$", r" \1", " ".join(name.replace("*", "").split()))
                keep.append((index, PARTIES[tag.split()[0]], "(W/I)" in tag, candidate.upper()))
            counties = [
                (row[0].strip(), [int(row[1 + index] or 0) for index, *_ in keep])
                for row in cells[2:]
                if row[0] and not row[0].strip().startswith("Percent") and row[0].strip() != "State Totals"
            ]
            total = next(
                [int(row[1 + index] or 0) for index, *_ in keep] for row in cells if (row[0] or "").strip() == "State Totals"
            )
            if len(counties) != 58 or [sum(column) for column in zip(*(v for _, v in counties), strict=True)] != total:
                raise ValueError(f"{year} {file}: counties do not add up to the State Totals row")
            for county, votes in counties:
                for (_, party, writein, candidate), value in zip(keep, votes, strict=True):
                    rows.append(
                        (year, f"president:{party}", day, "CA", fips[re.sub(r"[^a-z]", "", county.lower())], candidate, writein, value)
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "state": pl.String,
            "code": pl.String,
            "candidate": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        special=pl.lit(False),
        district=pl.lit(None, pl.Int16),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("CA"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's 2004, 2008, 2012, and 2020 presidential primaries by county from the Secretary of State's Statement of Vote spreadsheets",
            polity="US-CA",
        )
        source = cs.source(PUBLISHER, "Statement of Vote, presidential primary elections")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title="Statement of Vote, presidential primary elections",
            url="https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
            license="Public record",
        )
        for _, names in FILES.values():
            for name in names:
                cs.artifact(raw.cached(SOURCE, SITE + name), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"CA": source}, strict=True)
        return cs.write()
