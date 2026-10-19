import io
import zipfile

import fastexcel
import polars as pl

from ea import raw
from ea.store import DATA

SOURCE = "us.cog"
URL = "https://www2.census.gov/programs-surveys/gus/datasets/2022/govt_units_2022.ZIP"
SHEETS = {
    "General Purpose": None,
    "Special District": "special",
    "School District": "school",
    "DEP School Dist": "dependent_school",
}
TYPES = {"1 - COUNTY": "county", "2 - MUNICIPAL": "municipality", "3 - TOWNSHIP": "township"}
COUNTS = {"county": 3031, "municipality": 19491, "township": 16214, "special": 39555, "school": 12546}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".zip")


def parse() -> None:
    book = fastexcel.read_excel(
        zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, URL).path.read_bytes())).read("Govt_Units_2022_Final.xlsx")
    )
    frames = []
    for sheet, kind in SHEETS.items():
        frame = book.load_sheet_by_name(sheet, dtypes="string").to_polars()
        population = next((c for c in ("POPULATION", "ENROLLMENT") if c in frame.columns), None)
        frames.append(
            frame.select(
                pl.coalesce("CENSUS_ID_GIDID", pl.lit("pid6:") + pl.col("CENSUS_ID_PID6")).alias("id"),
                (pl.col("UNIT_TYPE").replace_strict(TYPES) if kind is None else pl.lit(kind)).alias("kind"),
                pl.col("UNIT_NAME").str.strip_chars().alias("name"),
                pl.col("FIPS_STATE").alias("state_fips"),
                pl.col("FIPS_COUNTY").alias("county_fips"),
                (pl.col("FIPS_PLACE") if "FIPS_PLACE" in frame.columns else pl.lit(None, pl.String)).alias("place_fips"),
                (pl.col("FUNCTION_NAME") if "FUNCTION_NAME" in frame.columns else pl.lit(None, pl.String)).alias(
                    "function"
                ),
                (pl.lit(None) if population is None else pl.col(population).cast(pl.Float64))
                .cast(pl.Int64, strict=False)
                .alias("population"),
                (pl.col("IS_ACTIVE") == "Y").alias("active"),
            )
        )
    governments = pl.concat(frames)
    if governments["id"].n_unique() != governments.height:
        raise ValueError("duplicate government IDs")
    held = dict(governments.group_by("kind").len().iter_rows())
    for kind, count in COUNTS.items():
        if held.get(kind) != count:
            raise ValueError(f"{kind}: {held.get(kind)} units, the Census Bureau reports {count}")
    out = DATA / "parsed" / SOURCE
    out.mkdir(parents=True, exist_ok=True)
    governments.write_parquet(out / "governments.parquet")
