import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.ar_pri_2002"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PATH = "2002/20020521__ar__primary.csv"
DAY = date(2002, 5, 21)
CONTESTS = {
    ("U.S. Senate", "Republican"): ("senate:republican", None),
    ("U.S. Congress District 02", "Democrat"): ("house:democratic", 2),
    ("U.S. Congress District 04", "Republican"): ("house:republican", 4),
}
TITLES = re.compile(r"^(?:Congressman|Congresswoman|Representative|Sen\.|Senator)\s+")
PUBLISHER = "OpenElections"
TITLE = "Arkansas election results"


def url() -> str:
    return openelections.REPOSITORY.format(state="ar", commit=openelections.COMMITS["AR"], path=PATH)


def fetch() -> None:
    raw.fetch(SOURCE, url(), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        codes = openelections.counties(conn)["AR"]
    table = pl.read_csv(raw.cached(SOURCE, url()).path, infer_schema_length=0).filter(
        pl.col("reporting_level") == "county", ~pl.col("candidate").str.starts_with("Total")
    )
    rows = []
    for (office, party), (chamber, district) in CONTESTS.items():
        for county, candidate, votes in (
            table.filter(pl.col("office") == office, pl.col("party") == party)
            .select("jurisdiction", "candidate", "votes")
            .iter_rows()
        ):
            rows.append(
                (
                    chamber,
                    district,
                    codes[re.sub(r"[^a-z]", "", county.lower().removesuffix(" county"))],
                    TITLES.sub("", candidate).upper(),
                    int(votes),
                )
            )
    frame = pl.DataFrame(
        rows,
        schema={
            "chamber": pl.String,
            "district": pl.Int64,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        day=pl.lit(DAY),
        year=pl.lit(2002, pl.Int32),
        special=pl.lit(False),
        state=pl.lit("AR"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("AR"),
    ).write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("chamber", "district", "candidate")
        .agg(pl.col("votes").sum(), pl.len())
        .sort("chamber", "district")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas's May 21, 2002 U.S. Senate Republican and U.S. House 2nd Democratic and 4th Republican "
            "primaries by county from OpenElections",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://github.com/openelections/openelections-data-ar",
                license="No license stated: facts published with attribution, raw files not redistributed",
            )
        cs.artifact(raw.cached(SOURCE, url()), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AR": source}, extend=True)
        cs.add(
            "note",
            entity_id=cs.key(f"{SOURCE}.result_set", "2002-05-21:AR:senate:republican:county"),
            kind="caveat",
            body_md="The county rows add up to 71,567 votes for Tim Hutchinson, nine short of the 71,576 in the state's "
            "contest total. No cell is corrected.",
        )
        return cs.write()
