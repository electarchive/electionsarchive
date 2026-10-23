import io
import re
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.elstats import GAZETTEER, MA_PUBLISHER, MASSACHUSETTS, SUFFIXES, TITLE, plain
from ea.store import DATA, connect

SOURCE = "us.senate_specials"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MA_ELECTIONS = {104711: date(2010, 1, 19), 124797: date(2013, 6, 25)}
ALABAMA = "https://www.sos.alabama.gov/sites/default/files/election-data/2018-03/2017-Special-General-PrecinctLevel.zip"
AL_DAY = date(2017, 12, 12)
AL_TITLE = "2017 special general election: precinct-level results"
AL_PUBLISHER = "Alabama Secretary of State"
AL_LANDING = "https://www.sos.alabama.gov/alabama-votes/voter/election-data"


def ma_url(election: int) -> str:
    return f"{MASSACHUSETTS}download/{election}/precincts_include:0/"


def fetch() -> None:
    raw.fetch(SOURCE, GAZETTEER, suffix=".zip")
    for election in MA_ELECTIONS:
        raw.fetch(SOURCE, ma_url(election), suffix=".csv")
    raw.fetch(SOURCE, ALABAMA, suffix=".zip")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS") == "MA")
    towns: dict[str, str] = {}
    for geoid, name in gazetteer.select("GEOID", "NAME").iter_rows():
        towns.setdefault(plain(name), geoid[:5])
        towns.setdefault(plain(re.sub(SUFFIXES, "", name.lower())), geoid[:5])
    rows: list[tuple[date, str, str, str, int, bool]] = []
    for election, day in MA_ELECTIONS.items():
        table = pl.read_csv(raw.cached(SOURCE, ma_url(election)).path, infer_schema=False, has_header=False).rows()
        names = table[0]
        tallies: dict[tuple[str, str], int] = {}
        printed: list[int] = []
        for row in table[2:]:
            if row[0] is None:
                continue
            values = {
                names[column]: int(row[column].replace(",", ""))
                for column in range(3, len(row))
                if names[column] not in (None, "Blanks", "Total Votes Cast") and row[column] is not None
            }
            if row[0] == "TOTALS":
                printed = list(values.values())
                continue
            code = towns.get(
                plain(
                    re.sub(
                        r"^([NSEW])\. ",
                        lambda hit: {"N": "North ", "S": "South ", "E": "East ", "W": "West "}[hit[1]],
                        row[0],
                    )
                )
            )
            if code is None:
                raise ValueError(f"{election}: no town for {row[0]}")
            for name, votes in values.items():
                tallies[(name, code)] = tallies.get((name, code), 0) + votes
        candidates = [name for name in names[3:] if name not in (None, "Blanks", "Total Votes Cast")]
        if [sum(v for (name, _), v in tallies.items() if name == candidate) for candidate in candidates] != printed:
            raise ValueError(f"{election}: the towns do not add up to the printed totals")
        rows.extend(
            (day, "MA", code, name.upper(), votes, name == "All Others") for (name, code), votes in tallies.items()
        )
    counties: dict[tuple[str, str], int] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, ALABAMA).path) as archive:
        for member in archive.namelist():
            county = re.fullmatch(r"2017-General-(.+)\.xls", member)
            if county is None:
                continue
            sheet = pl.read_excel(io.BytesIO(archive.read(member)), has_header=False, engine="calamine").rows()
            for row in sheet[1:]:
                if (
                    row[0]
                    and row[0].startswith("UNITED STATES SENATOR")
                    and row[2].strip() not in ("Over Votes", "Under Votes")
                ):
                    key = (county[1], row[2].strip())
                    counties[key] = counties.get(key, 0) + sum(int(float(cell)) for cell in row[3:] if cell)
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
    if len({county for county, _ in counties}) != 67:
        raise ValueError("Alabama: expected 67 counties")
    rows.extend(
        (
            AL_DAY,
            "AL",
            fips[re.sub(r"[^a-z]", "", county.lower())],
            "WRITE-INS" if name == "Write-In" else name.upper(),
            votes,
            name == "Write-In",
        )
        for (county, name), votes in counties.items()
    )
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "state": pl.String,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "writein": pl.Boolean,
        },
        orient="row",
    )
    print(frame.group_by("day", "state", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("day", "votes").rows())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.group_by("day", "state", "code", "candidate").agg(
        pl.col("votes").sum(), pl.col("writein").any()
    ).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber=pl.lit("senate"),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(True),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source="state",
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="U.S. Senate specials by county: Massachusetts 2010 and 2013 (town returns from Electionstats, "
            "summed to counties) and Alabama 2017 (Secretary of State precinct files)",
            polity="US",
        )
        sources = {}
        for state, publisher, title, landing in (
            ("MA", MA_PUBLISHER, TITLE, MASSACHUSETTS),
            ("AL", AL_PUBLISHER, AL_TITLE, AL_LANDING),
        ):
            sources[state] = cs.source(publisher, title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=landing,
                    license="Public record",
                )
        for election in MA_ELECTIONS:
            cs.artifact(raw.cached(SOURCE, ma_url(election)), sources["MA"])
        cs.artifact(raw.cached(SOURCE, ALABAMA), sources["AL"])
        frame = pl.read_parquet(PARSED / "counties.parquet")
        medsl.attach(cs, conn, frame.filter(pl.col("state") == "MA"), sources, strict=True)
        medsl.attach(cs, conn, frame.filter(pl.col("state") == "AL"), sources)
        return cs.write()
