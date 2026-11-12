import re
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.census_decennial"
VERSION = "1"
PUBLISHER = "U.S. Census Bureau"
LICENSE = "Public domain (U.S. government work)."
ROOTS = {
    2020: "https://www2.census.gov/programs-surveys/decennial/2020/data/01-Redistricting_File--PL_94-171/",
    2010: "https://www2.census.gov/census_2010/01-Redistricting_File--PL_94-171/",
}
TITLES = {
    2020: "2020 Census Redistricting Data (Public Law 94-171) Summary File",
    2010: "2010 Census Redistricting Data (Public Law 94-171) Summary File",
}
LEVELS = {"040": "us.state", "050": "us.county", "060": "us.town", "160": "us.place"}
RECODED = {"46113": "46102", "02270": "02158"}
SKIP = {"National", "00-2020-Redistricting-Noisy-Measurement-File"}


def _states(year: int) -> list[str]:
    index = raw.fetch(SOURCE, ROOTS[year], suffix=".html").path.read_text(encoding="latin-1")
    return sorted(set(re.findall(r'href="([A-Z][A-Za-z_]+)/"', index)) - SKIP)


def _zip(year: int, folder: str) -> str:
    listing = raw.fetch(SOURCE, f"{ROOTS[year]}{folder}/", suffix=".html").path.read_text(encoding="latin-1")
    (name,) = set(re.findall(rf'href="([a-z]{{2}}{year}\.pl\.zip)"', listing))
    return f"{ROOTS[year]}{folder}/{name}"


def fetch() -> None:
    for year in ROOTS:
        for folder in _states(year):
            raw.fetch(SOURCE, _zip(year, folder), suffix=".zip")


def _read(year: int, path: Path) -> list[dict[str, str | int]]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        geo = archive.read(next(n for n in names if "geo" in n)).decode("latin-1").splitlines()
        segment = archive.read(next(n for n in names if re.search(rf"00001{year}\.pl$", n))).decode("latin-1")
    counts: dict[str, int] = {}
    for line in segment.splitlines():
        fields = line.split("|" if year == 2020 else ",")
        counts[fields[4]] = int(fields[5])
    rows: list[dict[str, str | int]] = []
    for line in geo:
        if year == 2020:
            fields = line.split("|")
            sumlev, geocomp, logrecno = fields[2], fields[4], fields[7]
            state, county, cousub, place = fields[12], fields[14], fields[17], fields[29]
            funcstat, name = fields[88], fields[87]
        else:
            sumlev, geocomp, logrecno = line[8:11], line[11:13], line[18:25]
            state, county, cousub, place = line[27:29], line[29:32], line[36:41], line[45:50]
            funcstat, name = line[316:317], line[226:316].strip()
        if geocomp != "00" or sumlev not in LEVELS:
            continue
        code = {"040": state, "050": state + county, "060": state + county + cousub, "160": state + place}[sumlev]
        rows.append({"sumlev": sumlev, "code": code, "name": name, "funcstat": funcstat, "value": counts[logrecno]})
    return rows


def emit() -> Path:
    with connect() as conn:
        areas = {
            (scheme, code, year): str(area)
            for year in ROOTS
            for scheme, code, area in conn.execute(
                "select a.scheme_code, c.code, a.id from area a join area_code c on c.area_id = a.id "
                "where a.scheme_code = any(%s) and c.valid @> %s::date",
                [list(LEVELS.values()), date(year, 4, 1)],
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Total population on Census Day 2010 and 2020 of each US state, county, county subdivision, and "
            "place that exists as an area, from the Census Bureau's P.L. 94-171 redistricting summary files, joined "
            "by FIPS code",
            polity="US",
        )
        rows, missing = [], []
        for year, root in ROOTS.items():
            source = cs.source(PUBLISHER, TITLES[year])
            if conn.execute("select from source where id = %s", [source]).fetchone() is None:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=TITLES[year],
                    url=root,
                    license=LICENSE,
                    notes="One zip per state; table P1 (total population), cell P0010001, joined to the geographic "
                    "header by logical record, summary levels 040, 050, 060, and 160, geographic component 00.",
                )
            for folder in _states(year):
                for row in _read(year, raw.cached(SOURCE, _zip(year, folder)).path):
                    scheme = LEVELS[str(row["sumlev"])]
                    area = areas.get((scheme, RECODED.get(str(row["code"]), str(row["code"])), year))
                    if area is None:
                        missing.append({"year": year, **row})
                        continue
                    if int(row["value"]) == 0:
                        continue
                    rows.append(
                        {
                            "area_id": area,
                            "date": date(year, 4, 1),
                            "value": int(row["value"]),
                            "locator": f"P.L. 94-171 {year}, table P1, summary level {row['sumlev']}, "
                            f"GEOID {row['code']}",
                            "source_id": str(source),
                        }
                    )
        frame = pl.DataFrame(
            rows,
            schema={
                "area_id": pl.String,
                "date": pl.Date,
                "value": pl.Int64,
                "locator": pl.String,
                "source_id": pl.String,
            },
        ).with_columns(kind=pl.lit("population"), date_precision=pl.lit("day"))
        cs.frame("area_figure", frame)
        absent = pl.DataFrame(missing)
        folder = raw.parsed(SOURCE)
        folder.mkdir(parents=True, exist_ok=True)
        absent.write_csv(folder / "absent.tsv", separator="\t")
        for year, sumlev, count in (
            frame.with_columns(pl.col("locator").str.extract(r"level (\d+)").alias("sumlev"))
            .group_by(pl.col("date").dt.year(), "sumlev")
            .len()
            .sort("date", "sumlev")
            .iter_rows()
        ):
            print(f"{year} {sumlev}: {count} areas")
        print(absent.group_by("year", "sumlev", "funcstat").len().sort("year", "sumlev", "funcstat"))
        return cs.write()
