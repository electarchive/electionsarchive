import io
import re
import zipfile
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl, state_primaries
from ea.store import DATA, connect

SOURCE = "us.ma_senate_pri_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ELECTION = "99009"
CANDIDATE = "CARLA A. HOWELL"


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
    table = pl.read_csv(
        raw.cached(state_primaries.SOURCE, state_primaries.MA_DOWNLOAD.format(ELECTION)).path,
        infer_schema=False,
        skip_rows_after_header=1,
    ).filter(pl.col("City/Town").is_not_null())
    for town, howell, others in table.select("City/Town", "Carla A. Howell", "All Others").iter_rows():
        if town == "TOTALS":
            continue
        name = re.sub(r"^([NSEW])\. ", lambda found: {"N": "North ", "S": "South ", "E": "East ", "W": "West "}[found[1]], town)
        code = towns.get(elstats.plain(name)) or towns[elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower()))]
        rows.append((code, CANDIDATE, int(howell.replace(",", "")), False))
        rows.append((code, "ALL OTHERS", int(others.replace(",", "")), True))
    if sum(row[2] for row in rows if row[1] == CANDIDATE) != 1219:
        raise ValueError("the town rows no longer add up to 1,219 for Howell")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["code", "candidate", "votes", "writein"], orient="row").group_by(
        "code", "candidate", "writein"
    ).agg(pl.col("votes").sum()).with_columns(
        chamber=pl.lit("senate:libertarian"),
        day=pl.lit("2000-09-19").str.to_date(),
        year=pl.lit(2000, pl.Int32),
        district=pl.lit(None, pl.Int64),
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
            summary="Massachusetts's 2000 Libertarian U.S. Senate primary by town, from the Secretary of the "
            "Commonwealth's town rows as printed",
            polity="US",
        )
        source = cs.source(elstats.MA_PUBLISHER, elstats.TITLE)
        cs.artifact(raw.cached(state_primaries.SOURCE, state_primaries.MA_DOWNLOAD.format(ELECTION)), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet"),
            {"MA": source},
            authoritative="Source: the Secretary of the Commonwealth's town rows, stored as printed.",
        )
        cs.add(
            "note",
            entity_id=cs.key(f"{SOURCE}.result_set", "2000-09-19:MA:senate:libertarian:county"),
            kind="caveat",
            body_md="The printed town rows add up to 17 All Others votes, but the TOTALS row prints 18. Carla Howell's "
            "rows add up to the printed 1,219. No cell is corrected.",
        )
        return cs.write()
