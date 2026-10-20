import io
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.county_changes import ct_cousub_regions
from ea.store import DATA, connect

SOURCE = "us.eavs_2024"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "U.S. Election Assistance Commission"
URL = "https://www.eac.gov/sites/default/files/2025-06/2024_EAVS_for_Public_Release_nolabel_V1_csv.zip"
TITLE = "2024 Election Administration and Voting Survey"
DAY = date(2024, 11, 5)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".zip")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, URL).path) as archive:
        data = archive.read(next(name for name in archive.namelist() if name.lower().endswith(".csv")))
    frame = pl.read_csv(io.BytesIO(data), infer_schema=False, encoding="utf8-lossy")
    regions = ct_cousub_regions()
    parsed = (
        frame.select(
            state=pl.col("State_Abbr"),
            code=pl.when(pl.col("State_Abbr") == "CT")
            .then(pl.col("FIPSCode").str.zfill(10).replace_strict(regions, default=None))
            .otherwise(pl.col("FIPSCode").str.zfill(10).str.slice(0, 5)),
            registered=pl.col("A1a").str.extract(r"^(-?\d+)").cast(pl.Int64, strict=False),
            voters=pl.col("F1a").str.extract(r"^(-?\d+)").cast(pl.Int64, strict=False),
        )
        .with_columns(
            registered=pl.when(pl.col("registered") >= 0).then("registered"),
            voters=pl.when(pl.col("voters") >= 0).then("voters"),
        )
        .group_by("state", "code")
        .agg(
            pl.col("registered").sum(),
            pl.col("voters").sum(),
            pl.col("registered").null_count().alias("registered_missing"),
            pl.col("voters").null_count().alias("voters_missing"),
        )
        .with_columns(
            registered=pl.when(pl.col("registered_missing") == 0).then("registered"),
            voters=pl.when(pl.col("voters_missing") == 0).then("voters"),
        )
        .drop("registered_missing", "voters_missing")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    parsed.sort("state", "code").write_parquet(PARSED / "counties.parquet")
    print(parsed.height, parsed["registered"].sum(), parsed["voters"].sum())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Registered voters and voters by county for the 2024 general election "
            "from the Election Assistance Commission's Election Administration and Voting Survey",
            polity="US",
        )
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        counties = {
            code
            for (code,) in conn.execute("select code from reporting_unit where scheme_code = 'us.county'").fetchall()
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.eac.gov/research-and-data/datasets-codebooks-and-surveys",
                license="Public domain",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        for (state,), rows in frame.group_by("state", maintain_order=True):
            key = f"{DAY}:{state}"
            if key not in elections:
                print(f"{key}: no election")
                continue
            values = [
                (code, registered, voters)
                for code, registered, voters in rows.select("code", "registered", "voters").iter_rows()
                if code in counties
                and (registered is not None or voters is not None)
                and not (registered is not None and voters is not None and voters > registered * 1.25)
                and not (registered is not None and voters == registered and registered > 100)
            ]
            if rows.height - len(values):
                print(f"{key}: {rows.height - len(values)} jurisdictions dropped")
            if not values:
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            previous = None
            if state == "CT":
                previous, result_set = result_set, cs.key(f"{SOURCE}.result_set", f"{key}:regions")
                cs.add(
                    "result_set",
                    id=previous,
                    election_id=elections[key],
                    contest_id=None,
                    stage="certified",
                    kind="counted",
                    status="superseded",
                    source_id=source,
                    is_default=False,
                    completeness=1,
                )
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[key],
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=elections[key] not in held or state == "CT",
                completeness=round(len(values) / rows.height, 4),
                supersedes_id=previous,
            )
            for code, registered, voters in values:
                for measure, value in ((2, registered), (14, voters)):
                    if value is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=cs.unit("us.county", code),
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
