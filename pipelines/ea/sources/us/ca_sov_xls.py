import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.ca_sov_xls"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://elections.cdn.sos.ca.gov/sov/"
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote"
FILES = {
    2002: {
        "Lieutenant Governor": "ltgov",
        "Secretary of State": "sos",
        "Controller": "controller",
        "Treasurer": "treasurer",
        "Attorney General": "ag",
        "Insurance Commissioner": "ic",
        "Superintendent of Public Instruction": "spi",
    },
    2006: {
        "Lieutenant Governor": "ltgov",
        "Secretary of State": "sos",
        "Controller": "contr",
        "Treasurer": "treas",
        "Attorney General": "ag",
        "Insurance Commissioner": "inscomm",
    },
}
PARTIES = {
    "DEM": "DEMOCRATIC",
    "REP": "REPUBLICAN",
    "AI": "AMERICAN INDEPENDENT",
    "GRN": "GREEN",
    "LIB": "LIBERTARIAN",
    "PF": "PEACE AND FREEDOM",
    "NL": "NATURAL LAW",
    "REF": "REFORM",
}
METHOD = (
    "County totals come from the California Secretary of State's Statement of Vote spreadsheets. Each candidate's "
    "county votes add up to the State Totals row in the spreadsheet."
)


def url(year: int, name: str) -> str:
    return f"{SITE}{year}-general/{name}.xls"


def fetch() -> None:
    for year, files in FILES.items():
        for name in files.values():
            raw.fetch(SOURCE, url(year, name), suffix=".xls")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["CA"]
    rows = []
    for year, files in FILES.items():
        for title, name in files.items():
            sheet = pl.read_excel(
                raw.cached(SOURCE, url(year, name)).path,
                engine="calamine",
                has_header=False,
                sheet_id=1,
                infer_schema_length=0,
            )
            cells = sheet.rows()
            names = [re.sub(r"\s+", " ", (cell or "").replace("*", "")).strip() for cell in cells[0][1:]]
            parties = [(cell or "").strip() for cell in cells[1][1:]]
            keep = [
                index
                for index, text in enumerate(names)
                if text and not text.lower().startswith("votes not cast") and text.lower() != "total"
            ]
            counties = [
                (row[0].strip(), [int(row[1 + index] or 0) for index in keep])
                for row in cells[2:]
                if row[0] and not row[0].strip().startswith("Percent") and row[0].strip() != "State Totals"
            ]
            total = next(
                [int(row[1 + index] or 0) for index in keep] for row in cells if (row[0] or "").strip() == "State Totals"
            )
            if len(counties) != 58 or [sum(column) for column in zip(*(v for _, v in counties), strict=True)] != total:
                raise ValueError(f"{year} {title}: {len(counties)} counties that do not add up to the State Totals row")
            for county, votes in counties:
                code = fips[re.sub(r"[^a-z]", "", county.lower())]
                for index, value in zip(keep, votes, strict=True):
                    party = PARTIES.get(parties[index]) if parties[index] else None
                    if parties[index] not in ("", "NP", "(W/I)") and party is None:
                        raise ValueError(f"{year} {title}: unknown party {parties[index]}")
                    rows.append((year, title, names[index], party, parties[index] == "(W/I)", value, code))
    year = pl.col("year").cast(pl.Int32)
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "title": pl.String,
            "candidate": pl.String,
            "party": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
            "county": pl.String,
        },
        orient="row",
    ).select(
        year=year,
        day=pl.struct(year=year).map_elements(
            lambda row: date(row["year"], 11, 2 + (-date(row["year"], 11, 1).weekday()) % 7), return_dtype=pl.Date
        ),
        state=pl.lit("CA"),
        title="title",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate=pl.col("candidate").str.to_uppercase(),
        party="party",
        writein="writein",
        measure=pl.lit(1, pl.Int16),
        votes="votes",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(58),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "title", "candidate", "party", "writein")
        .agg(pl.col("votes").sum())
        .sort("year", "title", "votes", descending=[False, False, True])
        .rows()
    )


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
                url="https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
                license="Public record",
            )
        for year, files in FILES.items():
            for name in files.values():
                cs.artifact(raw.cached(SOURCE, url(year, name)), source)
        return {"CA": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="California's lieutenant governor, secretary of state, controller, treasurer, attorney general, "
        "insurance commissioner, and 2002 superintendent of public instruction elections of 2002 and 2006 by county "
        "from the Secretary of State's Statement of Vote spreadsheets",
        cite=cite,
        method=METHOD,
    )
