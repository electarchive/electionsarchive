import io
import re
import zipfile
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl, state_primaries
from ea.store import DATA, connect

SOURCE = "us.ma_house_pri_2006"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ELECTIONS = {"102626": ("democratic", "JOHN F. TIERNEY", 71530, 73401), "102627": ("republican", "RICHARD W. BARTON", 6051, 6289)}


def parse() -> None:
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False).filter(
            pl.col("USPS") == "MA"
        )
    towns: dict[str, str] = {}
    for geoid, name in gazetteer.select("GEOID", "NAME").iter_rows():
        towns.setdefault(elstats.plain(name), geoid[:5])
        towns.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
    rows = []
    for election, (party, candidate, printed, summed) in ELECTIONS.items():
        table = pl.read_csv(
            raw.cached(state_primaries.SOURCE, state_primaries.MA_DOWNLOAD.format(election)).path,
            infer_schema=False,
            skip_rows_after_header=1,
        ).filter(pl.col("City/Town").is_not_null())
        for town, *cells in table.select("City/Town", pl.col("^(John|Richard).*$"), "All Others").iter_rows():
            if town == "TOTALS":
                continue
            name = re.sub(r"^([NSEW])\. ", lambda found: {"N": "North ", "S": "South ", "E": "East ", "W": "West "}[found[1]], town)
            code = towns.get(elstats.plain(name)) or towns[elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower()))]
            rows.append((party, code, candidate, int(cells[0].replace(",", "")), False))
            rows.append((party, code, "ALL OTHERS", int(cells[1].replace(",", "")), True))
        if sum(row[3] for row in rows if row[0] == party and row[2] == candidate) != summed:
            raise ValueError(f"{election}: the town rows no longer add up to {summed}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["party", "code", "candidate", "votes", "writein"], orient="row").group_by(
        "party", "code", "candidate", "writein"
    ).agg(pl.col("votes").sum()).with_columns(
        chamber=pl.lit("house:") + pl.col("party"),
        day=pl.lit("2006-09-19").str.to_date(),
        year=pl.lit(2006, pl.Int32),
        district=pl.lit(6, pl.Int64),
        special=pl.lit(False),
        state=pl.lit("MA"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("MA"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Massachusetts's 2006 6th District Democratic and Republican primaries by county, from the "
            "Secretary of the Commonwealth's town rows as printed",
            polity="US",
        )
        source = cs.source(elstats.MA_PUBLISHER, elstats.TITLE)
        for election in ELECTIONS:
            cs.artifact(raw.cached(state_primaries.SOURCE, state_primaries.MA_DOWNLOAD.format(election)), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet"),
            {"MA": source},
            authoritative="Source: the Secretary of the Commonwealth's town rows, stored as printed.",
        )
        for party, candidate, printed, summed in ELECTIONS.values():
            cs.add(
                "note",
                entity_id=cs.key(f"{SOURCE}.result_set", f"2006-09-19:MA:house:6:{party}:county"),
                kind="caveat",
                body_md=f"The printed town rows add up to {summed:,} votes for {candidate.title()}, but the state's own "
                f"TOTALS row prints {printed:,}. Bedford shows 497 All Others and 0 blanks, which looks like a "
                "transposition. No cell is corrected; the earlier set carries the declared total.",
            )
        return cs.write()
