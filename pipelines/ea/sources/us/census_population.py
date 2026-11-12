import io
import zipfile
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.cog import SAIPE, TYPES, URL
from ea.store import connect

SOURCE = "us.census_population"
VERSION = "1"
PUBLISHER = "U.S. Census Bureau"
UNITS = "2022 Census of Governments: Government Units List"
DISTRICTS = "Small Area Income and Poverty Estimates: 2022 School District Estimates"
SCHEMES = {"county": "us.county", "municipality": "us.place", "township": "us.town"}


def governments() -> pl.DataFrame:
    book = fastexcel.read_excel(
        zipfile.ZipFile(io.BytesIO(raw.cached("us.cog", URL).path.read_bytes())).read("Govt_Units_2022_Final.xlsx")
    )
    frame = book.load_sheet_by_name("General Purpose", dtypes="string").to_polars()
    return frame.select(
        pl.col("CENSUS_ID_GIDID").alias("id"),
        pl.col("UNIT_TYPE").replace_strict(TYPES).alias("kind"),
        pl.col("FIPS_STATE").alias("state"),
        pl.col("FIPS_COUNTY").alias("county"),
        pl.col("FIPS_PLACE").alias("place"),
        pl.col("POPULATION").cast(pl.Float64).round().cast(pl.Int64).alias("value"),
        pl.col("POPULATION_YEAR").cast(pl.Int32).alias("year"),
    )


def districts() -> pl.DataFrame:
    frame = (
        fastexcel.read_excel(raw.cached("us.cog", SAIPE).path.read_bytes())
        .load_sheet(0, header_row=2, dtypes="string")
        .to_polars()
    )
    return frame.select(
        (pl.col("State FIPS Code") + pl.col("District ID")).alias("code"),
        pl.col("Estimated Total Population").cast(pl.Int64).alias("value"),
    )


def emit() -> Path:
    with connect() as conn:
        codes: dict[tuple[str, str], list[tuple[str, date | None, date | None]]] = {}
        for scheme, code, area, start, end in conn.execute(
            "select a.scheme_code, c.code, c.area_id::text, c.valid_from, c.valid_to from area_code c "
            "join area a on a.id = c.area_id where a.scheme_code = any(%s)",
            [[*SCHEMES.values(), "us.school_district"]],
        ).fetchall():
            codes.setdefault((scheme, code), []).append((area, start, end))

        def area(scheme: str, code: str, day: date) -> str | None:
            found = [
                a
                for a, start, end in codes.get((scheme, code), [])
                if (start or day) <= day and (end is None or day < end)
            ]
            return found[0] if len(found) == 1 else None

        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Census Bureau population estimates for US counties, incorporated places, and towns and townships "
            "from the 2022 Government Units List, and for school districts from the 2022 SAIPE school district "
            "estimates, joined by FIPS code",
            polity="US",
        )
        units = cs.source(PUBLISHER, UNITS)
        cs.add(
            "source",
            id=units,
            kind="official_body",
            publisher=PUBLISHER,
            title=UNITS,
            url=URL,
            license="Public domain (U.S. government work, 17 U.S.C. § 105).",
            notes="Govt_Units_2022_Final.xlsx, sheet General Purpose: one row per county, municipal, and township "
            "government with its FIPS state, county, and place codes and the Census Bureau's population estimate "
            "for the year in POPULATION_YEAR (2021 for nearly all).",
        )
        saipe = cs.source(PUBLISHER, DISTRICTS)
        cs.add(
            "source",
            id=saipe,
            kind="official_body",
            publisher=PUBLISHER,
            title=DISTRICTS,
            url=SAIPE,
            license="Public domain (U.S. government work, 17 U.S.C. § 105).",
            notes="ussd22.xls: estimated total population on July 1, 2022 of each school district by state FIPS code "
            "and NCES district ID.",
        )
        rows, missing = [], []
        for gid, kind, st, county, place, value, year in governments().iter_rows():
            if value is None or value <= 0:
                continue
            day = date(year, 7, 1)
            code = st + county if kind == "county" else st + place if kind == "municipality" else st + county + place
            found = area(SCHEMES[kind], code, day)
            if found is None:
                missing.append(kind)
                continue
            rows.append(
                {
                    "area_id": found,
                    "date": day,
                    "date_precision": "day" if year == 2021 else "year",
                    "value": value,
                    "source_id": str(units),
                    "locator": f"Govt_Units_2022_Final.xlsx, General Purpose, CENSUS_ID_GIDID {gid}",
                }
            )
        day = date(2022, 7, 1)
        for code, value in districts().iter_rows():
            found = area("us.school_district", code, day)
            if found is None or not value:
                missing.append("school")
                continue
            rows.append(
                {
                    "area_id": found,
                    "date": day,
                    "date_precision": "day",
                    "value": value,
                    "source_id": str(saipe),
                    "locator": f"ussd22.xls, district {code}",
                }
            )
        frame = pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Date), kind=pl.lit("population"))
        clash = (
            frame.group_by("area_id", "date", "source_id")
            .agg(pl.col("value").n_unique().alias("n"))
            .filter(pl.col("n") > 1)
        )
        if clash.height:
            print(f"{clash.height} areas carry two different figures, left out")
        frame = frame.join(clash, on=["area_id", "date", "source_id"], how="anti").unique(
            ["area_id", "date", "source_id"], keep="first", maintain_order=True
        )
        cs.frame("area_figure", frame)
        print(pl.Series(missing).value_counts(sort=True))
        for scheme, count, total in (
            frame.join(
                pl.DataFrame(
                    [(a, s) for (s, _), found in codes.items() for a, _, _ in found],
                    schema=["area_id", "scheme"],
                    orient="row",
                ),
                on="area_id",
            )
            .group_by("scheme")
            .agg(pl.len(), pl.col("value").sum())
            .sort("scheme")
            .iter_rows()
        ):
            print(f"{scheme}: {count} areas, {total:,} people")
        return cs.write()
