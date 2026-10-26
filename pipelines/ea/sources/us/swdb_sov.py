import io
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, oe_federal
from ea.store import DATA, connect

SOURCE = "us.swdb_sov"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SWDB = "https://statewidedatabase.org/pub/data/"
ELECTIONS = {
    "G16": date(2016, 11, 8),
    "G18": date(2018, 11, 6),
    "G20": date(2020, 11, 3),
    "G22": date(2022, 11, 8),
    "G24": date(2024, 11, 5),
}
OFFICES = {"PRS": ("president", False), "USS": ("senate", False), "USP": ("senate", True), "GOV": ("governor", False)}
PUBLISHER = "Statewide Database, University of California, Berkeley"
TITLE = "Statement of Vote data by SOV precinct"


def sov(election: str) -> str:
    return f"{SWDB}{election}/state/state_{election.lower()}_sov_data_by_{election.lower()}_svprec.zip"


def codes(election: str) -> str:
    return f"{SWDB}{election}/c001/001.codes"


def fetch() -> None:
    for election in ELECTIONS:
        for address in (sov(election), codes(election)):
            try:
                raw.cached(SOURCE, address)
            except LookupError:
                raw.fetch(SOURCE, address, suffix=".zip" if address.endswith(".zip") else ".txt")


def parse() -> None:
    frames = []
    for election, day in ELECTIONS.items():
        names = {
            code: name
            for code, name, _ in (
                line.split("\t")
                for line in raw.cached(SOURCE, codes(election)).path.read_text(errors="replace").splitlines()
                if line.count("\t") == 2
            )
        }
        with zipfile.ZipFile(raw.cached(SOURCE, sov(election)).path) as archive:
            (member,) = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            frame = pl.read_csv(io.BytesIO(archive.read(member)), infer_schema_length=0)
        columns = [column for column in frame.columns if re.fullmatch(r"(PRS|USS|USP|GOV)[A-Z]{3}\d\d", column)]
        missing = [column for column in columns if column not in names]
        if missing:
            raise ValueError(f"{election}: {missing} have no name in the codes")
        frames.append(
            frame.filter(~pl.col("SVPREC").str.to_uppercase().str.contains("TOT"))
            .unpivot(columns, index=["FIPS", "SVPREC"], variable_name="column", value_name="votes")
            .with_columns(pl.col("votes").replace("***", None).cast(pl.Int64))
            .filter(pl.col("votes").is_not_null())
            .select(
                year=pl.lit(day.year),
                day=pl.lit(day),
                chamber=pl.col("column").str.slice(0, 3).replace_strict({k: v for k, (v, _) in OFFICES.items()}),
                special=pl.col("column").str.slice(0, 3).replace_strict({k: s for k, (_, s) in OFFICES.items()}),
                state=pl.lit("CA"),
                district=pl.lit(None, pl.Int16),
                fips=pl.col("FIPS").str.zfill(5),
                code=pl.concat_str(
                    pl.lit(f"{day.year}:CA:"), pl.col("FIPS").str.zfill(5), pl.lit(":SOV:"), pl.col("SVPREC")
                ),
                candidate=pl.col("column").replace_strict(names).str.to_uppercase(),
                votes="votes",
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).write_parquet(PARSED / "returns.parquet")


def emit() -> Path:
    returns = (
        pl.read_parquet(PARSED / "returns.parquet")
        .with_columns(measure=pl.lit(1, pl.Int16), writein=pl.lit(False), kind=pl.lit("precinct"), source=pl.lit("CA"))
        .group_by(
            "year",
            "chamber",
            "day",
            "special",
            "state",
            "district",
            "code",
            "fips",
            "candidate",
            "measure",
            "kind",
            "source",
        )
        .agg(pl.col("writein").any(), pl.col("votes").sum())
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's president, U.S. Senate, and governor general elections by Statement of Vote precinct "
            "2016–2024 from the Statewide Database, for contests no other source holds by precinct",
            polity="US",
        )
        wanted = {
            key
            for (key,) in conn.execute(
                """select x.value from external_id x
                   where x.scheme = 'us.contest' and x.value ~ %s and x.value like '%%:CA:%%'
                     and not exists (
                       select from result_set r join changeset c on c.id = r.changeset_id
                       where r.contest_id = x.entity_id and r.status <> 'withdrawn' and c.pipeline <> %s
                         and coalesce(r.completeness, 1) >= 0.98
                         and exists (select from coverage_cache_unit cu join reporting_unit u on u.k = cu.unit_k
                                     where cu.result_set_k = r.k and u.scheme_code = 'us.precinct'))""",
                [oe_federal.CONTESTS, SOURCE],
            ).fetchall()
        }
        key = pl.concat_str(
            pl.col("day").cast(pl.String),
            "state",
            "chamber",
            pl.when("special").then(pl.lit("special")),
            separator=":",
            ignore_nulls=True,
        )
        returns = returns.filter(key.is_in(list(wanted)))
        print(returns.group_by("year", "chamber", "special").agg(pl.col("votes").sum()).sort("year"))
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="academic",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://statewidedatabase.org/election.html",
                license="No license stated: facts published with attribution, raw files not redistributed",
            )
        for election, day in ELECTIONS.items():
            if day.year in set(returns["year"]):
                for address in (sov(election), codes(election)):
                    cs.artifact(raw.cached(SOURCE, address), source)
        medsl.attach(cs, conn, returns, {"CA": source}, grain="precinct")
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        minted = (
            returns.group_by("code")
            .agg(pl.col("fips").first(), pl.col("day").min())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)))
        )
        cs.frame(
            "reporting_unit",
            minted.select(
                "id",
                polity_id=pl.lit(str(cs.polity("US-CA"))),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("fips").replace_strict({code: str(unit) for code, (unit, _) in counties.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("fips").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{minted.height} precincts")
        return cs.write()
