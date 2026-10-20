import io
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.county_changes import ct_cousub_regions
from ea.store import DATA, connect

SOURCE = "us.eavs"
VERSION = "2"
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


B = "https://www.eac.gov/sites/default/files/eac_assets/1/"
EARLY = {
    2004: (date(2004, 11, 2), [B + "6/State%20Data%20Tables%202004%20UOCAVA%20Survey.zip"]),
    2006: (date(2006, 11, 7), [B + "6/2006%20UOCAVA%20Survey%20All%20Data.zip"]),
    2008: (date(2008, 11, 4), [B + "6/2008%20eavs%20xls%20august%2011%202010.zip"]),
    2010: (
        date(2010, 11, 2),
        [B + "6/2010EAVS_A_Final.xls", B + "28/Final%20EAVS%20Data%20-%20Sections%20C%20to%20F_EXCEL.zip"],
    ),
    2012: (date(2012, 11, 6), [B + "1/2012EAVS_NVRAData.xlsx", B + "6/Excel%20Files-Part%202.zip"]),
    2014: (date(2014, 11, 4), [B + "1/2014_EAVS_Excel_Files1.zip"]),
}
DAYS = {year: day for year, (_, day) in FILES.items()} | {year: day for year, (day, _) in EARLY.items()}
NUMBER = r"^\s*(-?\d+)\s*$"


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


def early_files(year: int) -> dict[str, bytes]:
    files = {}
    for url in EARLY[year][1]:
        path = raw.cached(SOURCE, url).path
        if url.endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                files |= {
                    name: archive.read(name) for name in archive.namelist() if name.lower().endswith((".xls", ".xlsx"))
                }
        else:
            files[url] = path.read_bytes()
    return files


def sheet(data: bytes, **kwargs: object) -> pl.DataFrame:
    return pl.read_excel(io.BytesIO(data), infer_schema_length=0, **kwargs)


def number(column: str) -> pl.Expr:
    return pl.col(column).str.replace_all(",", "").str.extract(NUMBER).cast(pl.Int64, strict=False)


def read_early(year: int) -> pl.DataFrame:
    files = early_files(year)
    if year == 2004:
        parts = []
        for data in files.values():
            registered = (
                sheet(data, sheet_name="Registration", has_header=False)
                .slice(6)
                .filter(
                    pl.col("column_2").str.strip_chars().str.starts_with(pl.col("column_0").str.strip_chars())
                    & pl.col("column_2").str.strip_chars().str.contains(r"^\d{5}(\d{5})?$")
                )
            )
            ballots = sheet(data, sheet_name="Ballots Counted", has_header=False).slice(6)
            parts.append(
                registered.select(
                    state=pl.col("column_1"),
                    code=pl.col("column_2").str.strip_chars(),
                    registered=number("column_5"),
                ).join(
                    ballots.select(code=pl.col("column_2").str.strip_chars(), voters=number("column_7")),
                    on="code",
                    how="left",
                )
            )
        return pl.concat(parts).with_columns(code=pl.col("code").str.slice(0, 5))
    if year == 2006:
        frame = sheet(next(iter(files.values())))
        return frame.select(
            state=pl.col("state"), code=pl.col("id"), registered=number("q022006total"), voters=pl.lit(None, pl.Int64)
        )
    if year == 2008:
        combined = {name.rsplit("_", 1)[-1]: data for name, data in files.items()}
        registered = sheet(combined["SectionA.xls"])
        voters = sheet(combined["SectionF.xls"])
        return (
            registered.select(state=pl.col("STATE_"), code=pl.col("JurisID"), registered=number("A1"))
            .join(voters.select(code=pl.col("JurisID"), voters=number("F1a")), on="code", how="left")
            .filter(pl.col("code").str.len_chars() == 10)
        )
    registered = sheet(
        next(data for name, data in files.items() if name.startswith("https") or name.endswith("Section_A.xlsx"))
    )
    voters = sheet(
        next(
            data for name, data in files.items() if name.endswith(("Section F.xlsx", "Section F.xls", "Section_F.xlsx"))
        )
    )
    return (
        registered.select(
            state=pl.col("State"),
            code=pl.col("FIPSCode"),
            jurisdiction=pl.col("Jurisdiction"),
            registered=number("QA1a"),
        )
        .join(
            voters.select(code=pl.col("FIPSCode"), jurisdiction=pl.col("Jurisdiction"), voters=number("QF1a")),
            on=["code", "jurisdiction"],
            how="left",
        )
        .drop("jurisdiction")
    )


def fetch() -> None:
    for url, _ in FILES.values():
        raw.fetch(SOURCE, url, suffix=".zip" if url.endswith(".zip") else ".csv")
    for _, urls in EARLY.values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=".zip" if url.endswith(".zip") else Path(url).suffix)


def parse() -> None:
    regions = ct_cousub_regions()
    frames = []
    for year in FILES:
        frame = read(year)
        state = "State_Abbr" if "State_Abbr" in frame.columns else "State"
        frames.append(
            frame.select(
                year=pl.lit(year),
                state=pl.col(state),
                code=pl.when((pl.col(state) == "CT") & (year >= 2022))
                .then(pl.col("FIPSCode").str.zfill(10).replace_strict(regions, default=None))
                .otherwise(pl.col("FIPSCode").str.zfill(10).str.slice(0, 5)),
                registered=pl.col("A1a").str.extract(r"^(-?\d+)").cast(pl.Int64, strict=False),
                voters=pl.col("F1a").str.extract(r"^(-?\d+)").cast(pl.Int64, strict=False),
            )
        )
    for year in EARLY:
        frame = read_early(year)
        frames.append(
            frame.select(
                year=pl.lit(year),
                state=pl.col("state"),
                code=pl.when(pl.col("code").str.len_chars() <= 5)
                .then(pl.col("code").str.zfill(5))
                .otherwise(pl.col("code").str.zfill(10).str.slice(0, 5)),
                registered=pl.col("registered"),
                voters=pl.col("voters"),
            )
        )
    combined = pl.concat(frames)
    abbreviations = (
        combined.filter(pl.col("state").str.len_chars() == 2)
        .select(prefix=pl.col("code").str.slice(0, 2), abbreviation=pl.col("state"))
        .unique("prefix")
    )
    parsed = (
        combined.with_columns(prefix=pl.col("code").str.slice(0, 2))
        .join(abbreviations, on="prefix", how="left")
        .filter(pl.col("code").str.contains(r"^\d{5}$"))
        .with_columns(
            state=pl.col("abbreviation"),
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
    parsed.sort("year", "state", "code").write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Registered voters and voters by county for the 2004 through 2014 general elections "
            "from the Election Assistance Commission's Election Administration and Voting Survey",
            polity="US",
        )
        elections = {
            key: id
            for key, id in conn.execute(
                "select value, entity_id from external_id where scheme = 'us.election'"
            ).fetchall()
        }
        counties = {
            code
            for (code,) in conn.execute("select code from reporting_unit where scheme_code = 'us.county'").fetchall()
        }
        sources = {}
        emitted = EARLY | {2022: (FILES[2022][1], [FILES[2022][0]])}
        for year, (_, urls) in emitted.items():
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
            for url in urls:
                cs.artifact(raw.cached(SOURCE, url), sources[year])
        for (year, state), rows in frame.filter(
            (pl.col("year") < 2016) | ((pl.col("year") == 2022) & (pl.col("state") == "CT"))
        ).group_by("year", "state", maintain_order=True):
            day = emitted[year][0]
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
                and not (registered is not None and voters == registered and registered > 100)
            ]
            lost = rows.height - len(values)
            if lost:
                print(
                    f"{key}: {lost} jurisdictions without a county unit, numbers, or with more voters than registered"
                )
            if not values:
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            previous = None
            if year == 2022:
                previous, result_set = result_set, cs.key(f"{SOURCE}.result_set", f"{key}:regions")
                cs.add(
                    "result_set",
                    id=previous,
                    election_id=elections[key],
                    contest_id=None,
                    stage="certified",
                    kind="counted",
                    status="superseded",
                    source_id=sources[year],
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
                source_id=sources[year],
                is_default=True,
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
