import io
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.eavs"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "U.S. Election Assistance Commission"
FILES = {
    2016: (
        "https://www.eac.gov/sites/default/files/Research/EAVS_2016_Final_Data_for_Public_Release_v2.csv.zip",
        date(2016, 11, 8),
    ),
    2018: (
        "https://www.eac.gov/sites/default/files/Research/EAVS_2018_for_Public_Release_Updates3.csv",
        date(2018, 11, 6),
    ),
    2020: (
        "https://www.eac.gov/sites/default/files/EAVS%202020/2020_EAVS_for_Public_Release_nolabel_V2.csv",
        date(2020, 11, 3),
    ),
    2022: (
        "https://www.eac.gov/sites/default/files/2023-06/2022_EAVS_for_Public_Release_nolabel_V1_CSV.zip",
        date(2022, 11, 8),
    ),
}


def title(year: int) -> str:
    return f"{year} Election Administration and Voting Survey"


def read(year: int) -> pl.DataFrame:
    path = raw.cached(SOURCE, FILES[year][0]).path
    if path.suffix == ".zip" or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            data = archive.read(next(name for name in archive.namelist() if name.lower().endswith(".csv")))
    else:
        data = path.read_bytes()
    return pl.read_csv(io.BytesIO(data), infer_schema=False, encoding="utf8-lossy")


def fetch() -> None:
    for url, _ in FILES.values():
        raw.fetch(SOURCE, url, suffix=".zip" if url.endswith(".zip") else ".csv")


def parse() -> None:
    frames = []
    for year in FILES:
        frame = read(year)
        state = "State_Abbr" if "State_Abbr" in frame.columns else "State"
        frames.append(
            frame.select(
                year=pl.lit(year),
                state=pl.col(state),
                code=pl.col("FIPSCode").str.zfill(10).str.slice(0, 5),
                registered=pl.col("A1a").str.extract(r"^(-?\d+)").cast(pl.Int64, strict=False),
                voters=pl.col("F1a").str.extract(r"^(-?\d+)").cast(pl.Int64, strict=False),
            )
            .with_columns(
                registered=pl.when(pl.col("registered") >= 0).then("registered"),
                voters=pl.when(pl.col("voters") >= 0).then("voters"),
            )
            .group_by("year", "state", "code")
            .agg(
                pl.col("registered").sum().alias("registered"),
                pl.col("voters").sum().alias("voters"),
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
    pl.concat(frames).sort("year", "state", "code").write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Registered voters and voters by county for the 2016, 2018, 2020, and 2022 general elections "
            "from the Election Assistance Commission's Election Administration and Voting Survey",
            polity="US",
        )
        elections = {key: id for key, id in conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()}
        counties = {code for (code,) in conn.execute("select code from reporting_unit where scheme_code = 'us.county'").fetchall()}
        sources = {}
        for year, (url, _) in FILES.items():
            sources[year] = cs.source(PUBLISHER, title(year))
            if sources[year] in cs.minted:
                cs.add(
                    "source",
                    id=sources[year],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title(year),
                    url="https://www.eac.gov/research-and-data/datasets-codebooks-and-surveys",
                    license="Public domain",
                )
            cs.artifact(raw.cached(SOURCE, url), sources[year])
        for (year, state), rows in frame.group_by("year", "state", maintain_order=True):
            day = FILES[year][1]
            key = f"{day}:{state}"
            if key not in elections:
                print(f"{key}: no election")
                continue
            values = [
                (code, registered, voters)
                for code, registered, voters in rows.select("code", "registered", "voters").iter_rows()
                if code in counties
                and (registered is not None or voters is not None)
                and not (registered is not None and voters is not None and voters > registered * 1.25)
            ]
            lost = rows.height - len(values)
            if lost:
                print(f"{key}: {lost} jurisdictions without a county unit, numbers, or with more voters than registered")
            if not values:
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[key],
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=round(len(values) / rows.height, 4),
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
