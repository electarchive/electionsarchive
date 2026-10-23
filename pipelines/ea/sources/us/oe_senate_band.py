import io
import re
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_senate_band"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    "MS": [
        ("2006/20061107__ms__general.csv", date(2006, 11, 7), False),
        ("2008/20081104__ms__general.csv", date(2008, 11, 4), False),
    ],
    "NM": [("2000/20001107__nm__general__county.csv", date(2000, 11, 7), False)],
    "RI": [
        ("2012/20121106__ri__general__town.csv", date(2012, 11, 6), True),
        ("2014/20141104__ri__general__town.csv", date(2014, 11, 4), True),
    ],
}
SPELLINGS = {"TRENT LOFT": "TRENT LOTT", "HAROLD M, TAYLOR": "HAROLD M. TAYLOR"}
PUBLISHER = "OpenElections"


def url(state: str, path: str) -> str:
    return openelections.REPOSITORY.format(state=state.lower(), commit=openelections.COMMITS[state], path=path)


def fetch() -> None:
    for state, files in FILES.items():
        for path, *_ in files:
            raw.fetch(SOURCE, url(state, path), suffix=".csv")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False).filter(
            pl.col("USPS") == "RI"
        )
    towns: dict[str, str] = {}
    for geoid, name in gazetteer.select("GEOID", "NAME").iter_rows():
        towns.setdefault(elstats.plain(name), geoid[:5])
        towns.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
    with connect() as conn:
        counties = openelections.counties(conn)
    frames = []
    for state, files in FILES.items():
        for path, day, is_town in files:
            table = pl.read_csv(raw.cached(SOURCE, url(state, path)).path, infer_schema_length=0)
            place = "town" if "town" in table.columns else "county"
            lookup = towns if is_town else counties[state]
            table = table.filter(
                pl.col("office").str.contains("(?i)^(U\\.?S\\.? Senate|UNITED STATES SENATOR|SENATOR IN CONGRESS)$"),
                ~pl.col(place).str.contains("(?i)^total"),
                ~pl.col("candidate").str.contains("(?i)^total"),
            )
            if state == "MS":
                table = table.filter(pl.col("district").is_null() | (pl.col("district") != "unexpired term"))
            frames.append(
                table.group_by(place, "candidate", maintain_order=True)
                .agg(pl.col("votes").cast(pl.Int64).sum())
                .select(
                    day=pl.lit(day),
                    state=pl.lit(state),
                    code=pl.col(place).map_elements(
                        lambda name, lookup=lookup: lookup[elstats.plain(name)], return_dtype=pl.String
                    ),
                    candidate=pl.col("candidate").str.to_uppercase().replace(SPELLINGS),
                    votes="votes",
                )
                .group_by("day", "state", "code", "candidate")
                .agg(pl.col("votes").sum())
            )
    frame = pl.concat(frames)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    with pl.Config(tbl_rows=60):
        print(frame.group_by("day", "state", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("day", "votes"))


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="U.S. Senate generals by county from OpenElections: Mississippi 2006 and 2008, New Mexico 2000, "
            "and Rhode Island 2012 and 2014 (towns summed to counties)",
            polity="US",
        )
        source = cs.source(PUBLISHER, "Election results")
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher=PUBLISHER,
                title="Election results",
                url="https://github.com/openelections",
                license="No license stated: facts published with attribution, raw files not redistributed",
            )
        for state, files in FILES.items():
            for path, *_ in files:
                cs.artifact(raw.cached(SOURCE, url(state, path)), source)
        frame = pl.read_parquet(PARSED / "counties.parquet")
        medsl.attach(
            cs,
            conn,
            frame.with_columns(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber=pl.lit("senate"),
                special=pl.lit(False),
                district=pl.lit(None, pl.Int16),
                writein=pl.col("candidate").str.contains("(?i)write"),
                measure=pl.lit(1, pl.Int16),
                kind=pl.lit("county"),
                source=pl.col("state"),
            ),
            {state: source for state in FILES},
            strict=True,
            extend=True,
        )
        return cs.write()
