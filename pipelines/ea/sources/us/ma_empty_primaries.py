import io
import re
import zipfile
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl, state_primaries
from ea.store import DATA, connect

SOURCE = "us.ma_empty_primaries"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE


def fetch() -> None:
    for office in (5, 6):
        for year in state_primaries.MA_DAYS:
            listing = raw.cached(state_primaries.SOURCE, state_primaries.search("MA", office, year)).path.read_text()
            empty = set(state_primaries.MA_EMPTY.findall(listing))
            for election, _, _, stage in elstats.MA_ROW.findall(listing):
                if election in empty and "Special" not in stage:
                    raw.fetch(SOURCE, state_primaries.MA_DOWNLOAD.format(election), suffix=".csv")


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
    for office, body in (5, "house"), (6, "senate"):
        for year, day in state_primaries.MA_DAYS.items():
            listing = raw.cached(state_primaries.SOURCE, state_primaries.search("MA", office, year)).path.read_text()
            empty = set(state_primaries.MA_EMPTY.findall(listing))
            for election, _, district, stage in elstats.MA_ROW.findall(listing):
                if election not in empty or "Special" in stage:
                    continue
                table = pl.read_csv(
                    raw.cached(SOURCE, state_primaries.MA_DOWNLOAD.format(election)).path,
                    infer_schema=False,
                    skip_rows_after_header=1,
                ).filter(pl.col("City/Town").is_not_null())
                party = re.sub(r"[^a-z0-9]+", "-", stage.removesuffix(" Primary").lower())
                printed = 0
                found = 0
                for town, others in table.select("City/Town", "All Others").iter_rows():
                    votes = int(others.replace(",", ""))
                    if town == "TOTALS":
                        printed = votes
                        continue
                    name = re.sub(
                        r"^([NSEW])\. ", lambda hit: {"N": "North ", "S": "South ", "E": "East ", "W": "West "}[hit[1]], town
                    )
                    code = towns.get(elstats.plain(name)) or towns[elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower()))]
                    rows.append((body, party, day, district, code, votes, election))
                    found += votes
                if found != printed:
                    raise ValueError(f"{election}: towns add up to {found}, printed {printed}")
    frame = pl.DataFrame(rows, schema=["body", "party", "day", "district", "code", "votes", "election"], orient="row")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.group_by("body", "party", "day", "district", "code", "election").agg(pl.col("votes").sum()).with_columns(
        chamber=pl.col("body") + pl.lit(":") + pl.col("party"),
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.col("district").str.extract(r"^(\d+)").cast(pl.Int64),
        candidate=pl.lit("ALL OTHERS"),
        writein=pl.lit(True),
        special=pl.lit(False),
        state=pl.lit("MA"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("MA"),
    ).write_parquet(PARSED / "counties.parquet")
    print(frame.select("body", "party", "day", "district").unique().height, "contests")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Massachusetts's congressional primaries with no candidate on the ballot, by county from the "
            "Secretary of the Commonwealth's town rows of write-ins",
            polity="US",
        )
        source = cs.source(elstats.MA_PUBLISHER, elstats.TITLE)
        if source in cs.minted:
            raise ValueError("the Massachusetts source should already exist")
        frame = pl.read_parquet(PARSED / "counties.parquet").with_columns(
            pl.when(pl.col("year") == 2002)
            .then(pl.col("chamber").str.replace("green-rainbow", "green"))
            .otherwise(pl.col("chamber"))
            .alias("chamber"),
            pl.when(pl.col("year") == 2002).then(pl.col("party").str.replace("green-rainbow", "green")).otherwise(pl.col("party")).alias("party"),
        )
        held = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ ':MA:(house:[0-9]+|senate):[a-z-]+$'
                     and exists (select 1 from result_set rs join source s on s.id = rs.source_id
                                 where rs.contest_id = e.entity_id and rs.status = 'active'
                                   and s.publisher <> 'Federal Election Commission')"""
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.when(pl.col("body") == "house")
            .then(pl.concat_str(pl.col("day").cast(pl.String), pl.lit(":MA:house:"), "district", pl.lit(":"), "party"))
            .otherwise(pl.concat_str(pl.col("day").cast(pl.String), pl.lit(":MA:senate:"), "party"))
            .is_in(list(held))
        )
        for election in frame["election"].unique().sort():
            cs.artifact(raw.cached(SOURCE, state_primaries.MA_DOWNLOAD.format(election)), source)
        medsl.attach(
            cs,
            conn,
            frame,
            {"MA": source},
            authoritative="Source: the Secretary of the Commonwealth's town rows.",
            empty=True,
        )
        return cs.write()
