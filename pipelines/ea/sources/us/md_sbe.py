import csv
import io
import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.openelections import HOUSE, SENATE
from ea.store import DATA, connect

SOURCE = "us.md_sbe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = "https://elections.maryland.gov/elections/archive/"
SUMMARIES = [1990, 1994, 1998]
COUNTY_FILES = {
    2000: r"([A-Za-z][A-Za-z_.']*)_General_2000\.csv",
    2004: r"([A-Za-z][A-Za-z_.']*)_General_2004\.csv",
    2006: r"([A-Za-z][A-Za-z_.']*)_County_2006_General\.csv",
    2008: r"([A-Za-z][A-Za-z_.']*)_County_2008_General\.csv",
    2010: r"([A-Za-z][A-Za-z_.']*)_County_2010_General\.csv",
    2012: r"([A-Za-z][A-Za-z_.']*)_County_2012_General\.csv",
    2014: r"([A-Za-z][A-Za-z_.']*)_County_2014_General\.csv",
}
PUBLISHER = "Maryland State Board of Elections"
TITLE = "Election results archive"


def general(year: int) -> date:
    return date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)


def files(year: int) -> list[str]:
    listing = raw.cached(SOURCE, f"{ARCHIVE}{year}/election_data/index.html").path.read_text()
    return sorted(
        {
            match.group(0)
            for match in re.finditer(COUNTY_FILES[year], listing)
            if not re.search("Questions|Precinct|Legislative", match.group(0))
        }
    )


def fetch() -> None:
    for year in SUMMARIES:
        raw.fetch(SOURCE, f"{ARCHIVE}{year}/results_{year}/gaall.csv", suffix=".csv")
    for year in COUNTY_FILES:
        raw.fetch(SOURCE, f"{ARCHIVE}{year}/election_data/index.html", suffix=".html")
        for name in files(year):
            raw.fetch(SOURCE, f"{ARCHIVE}{year}/election_data/{name}", suffix=".csv")


def chamber(office: str) -> str | None:
    office = office.strip().lower()
    if "state" in office:
        return None
    if SENATE.search(office):
        return "senate"
    if HOUSE.search(office):
        return "house"
    return None


def parse() -> None:
    with connect() as conn:
        fips: dict[str, str] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '24%' order by u.code"""
        ).fetchall():
            plain = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower().replace("saint ", "st "))
            if int(code[2:]) >= 500:
                fips[f"{plain}city"] = code
            fips.setdefault(plain, code)

    def county(name: str) -> str:
        plain = re.sub(r"[^a-z]", "", name.lower().replace("saint ", "st ").replace("st.", "st").replace("county", ""))
        return fips[plain]

    rows: list[tuple[int, str, int | None, str, str, int]] = []
    for year in SUMMARIES:
        office: str | None = None
        district: int | None = None
        candidates: list[str] = []
        for line in csv.reader(
            io.StringIO(
                raw.cached(SOURCE, f"{ARCHIVE}{year}/results_{year}/gaall.csv").path.read_text(errors="replace")
            )
        ):
            cells = [cell.strip() for cell in line]
            if not any(cells):
                continue
            if " - Vote For" in cells[0]:
                office = chamber(cells[0].split(" - ")[0])
                found = re.search(r"District: (\d+)", cells[0])
                district = int(found.group(1)) if found else None
                candidates = []
            elif office is not None and cells[0] == "":
                candidates = [re.sub(r"\s*\([^)]*\)\s*(Winner)?$", "", cell) for cell in cells[1:] if cell]
            elif office is not None and candidates:
                for candidate, votes in zip(candidates, cells[1:], strict=False):
                    rows.append((year, office, district, county(cells[0]), candidate.upper(), int(votes or 0)))
    for year, pattern in COUNTY_FILES.items():
        for name in files(year):
            frame = pl.read_csv(
                raw.cached(SOURCE, f"{ARCHIVE}{year}/election_data/{name}").path,
                infer_schema=False,
                encoding="utf8-lossy",
            )
            frame = frame.rename({column: column.strip() for column in frame.columns})
            label = "Candidate Name" if "Candidate Name" in frame.columns else "Candidate"
            unit = county(re.sub(pattern, r"\1", name).replace("_", " "))
            for office, district, candidate, votes in frame.select(
                "Office Name", "Office District", label, "Total Votes"
            ).iter_rows():
                office = chamber(office or "")
                if office is not None and votes != "#NULL#":
                    rows.append(
                        (
                            year,
                            office,
                            int(district) if office == "house" and district else None,
                            unit,
                            (candidate or "").strip().upper(),
                            int(float(votes or 0)),
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "district": pl.Int16,
                "code": pl.String,
                "candidate": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            day=pl.col("year").map_elements(general, return_dtype=pl.Date),
            special=pl.lit(False),
            state=pl.lit("MD"),
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source=pl.lit("MD"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's House and Senate general elections by county, 1990–2014",
            polity="US-MD",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=f"{ARCHIVE}index.html",
            license="Public record",
        )
        for year in SUMMARIES:
            cs.artifact(raw.cached(SOURCE, f"{ARCHIVE}{year}/results_{year}/gaall.csv"), source)
        for year in COUNTY_FILES:
            for name in files(year):
                cs.artifact(raw.cached(SOURCE, f"{ARCHIVE}{year}/election_data/{name}"), source)
        medsl.attach(cs, conn, frame, {"MD": source})
        return cs.write()
