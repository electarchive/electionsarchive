import io
import re
import zipfile
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ks_sos_1988"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Kansas Secretary of State"
TITLE = "General election official results by county, 1988-1998"
LANDING = "https://www.sos.ks.gov/elections/election-results.html"
WAYBACK = "https://web.archive.org/web/2007id_/http://www.kssos.org/elections/elmpast/{yy}genral.exe"
DAYS = {
    1988: date(1988, 11, 8),
    1990: date(1990, 11, 6),
    1992: date(1992, 11, 3),
    1994: date(1994, 11, 8),
    1996: date(1996, 11, 5),
    1998: date(1998, 11, 3),
}
SHEETS = {
    "u.s. pres": "president",
    "us pres vp": "president",
    "us senate": "senate",
    "us house": "house",
    "statewide": "governor",
}
HOUSE_YEARS = {1988, 1990, 1992, 1994, 1996, 1998}


def url(year: int) -> str:
    return WAYBACK.format(yy=f"{year % 100:02d}")


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, url(year), suffix=".exe")


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '20%%'"""
            ).fetchall()
        }
    frames = []
    for year, day in DAYS.items():
        with zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, url(year)).path.read_bytes())) as archive:
            book = fastexcel.read_excel(archive.read(archive.namelist()[0]))
        for sheet in book.sheet_names:
            chamber = SHEETS.get(clean(sheet).lower())
            if chamber is None or (chamber == "house" and year not in HOUSE_YEARS):
                continue
            grid = book.load_sheet_by_name(sheet, header_row=None).to_polars().rows()
            parties = ("democrat", "republican", "libertarian", "independent", "reform", "taxpayers", "write-in")
            party = next(r for r in range(6) if any(clean(cell).lower() in parties for cell in grid[r]))
            labeled = any(
                clean(cell).lower().startswith(("president", "us senate", "us house", "governor"))
                for cell in grid[party - 1]
            )
            office = party - 1 if labeled else party
            start = 4 if chamber == "house" else 3
            columns = []
            for i in range(start, len(grid[0])):
                label = clean(grid[office][i]) if labeled else "US Senate"
                if not clean(grid[party][i]) or (chamber == "governor" and not label.lower().startswith("governor")):
                    continue
                name = "".join(
                    clean(grid[r][i]) + ("" if clean(grid[r][i]).endswith(("/", "-")) else " ")
                    for r in range(office)
                    if clean(grid[r][i])
                ).strip()
                district = re.search(r"District (\d+)", label)
                columns.append((i, name, clean(grid[party][i]), int(district[1]) if district else None, "unexpired" in label.lower()))
            rows = []
            totals: dict[object, list[int]] = {}
            for row in grid[party + 1 :]:
                text = clean(row[1] if chamber == "house" else row[0])
                if text:
                    key = re.sub(r"[^a-z]", "", re.sub(r"(?i)\s*(county)?\s*total.*$", "", text.lower().replace(" county", "")))
                    fips = counties.get(key)
                    if fips is None:
                        raise ValueError(f"{year} {sheet}: no county {text}")
                    for i, name, party, district, unexpired in columns:
                        value = clean(row[i]).replace(",", "")
                        if value:
                            rows.append((year, chamber, day, district, fips, name, party, int(float(value)), unexpired))
                elif any(clean(c).lower().startswith(("district t", "totals")) for c in row[:4]):
                    for i, name, party, district, unexpired in columns:
                        value = clean(row[i]).replace(",", "")
                        if value:
                            totals[(district, name, unexpired)] = int(float(value))
            frame = pl.DataFrame(
                rows,
                schema={
                    "year": pl.Int64,
                    "chamber": pl.String,
                    "day": pl.Date,
                    "district": pl.Int64,
                    "code": pl.String,
                    "candidate": pl.String,
                    "party": pl.String,
                    "votes": pl.Int64,
                    "special": pl.Boolean,
                },
                orient="row",
            )
            for (district, name, unexpired), total in totals.items():
                summed = frame.filter(
                    (pl.col("candidate") == name)
                    & (pl.col("special") == unexpired)
                    & (pl.col("district").is_null() if district is None else pl.col("district") == district)
                )["votes"].sum()
                if summed != total:
                    raise ValueError(f"{year} {sheet} {name}: counties sum to {summed}, printed {total}")
            frames.append(frame)
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.concat(frames)
        .with_columns(
            writein=pl.col("party").str.to_lowercase().str.contains("write"),
            candidate=pl.col("candidate").str.to_uppercase(),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .group_by("year", "chamber", "day", "district", "code", "candidate", "special")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("year").cast(pl.Int32),
            district=pl.col("district").cast(pl.Int16),
            state=pl.lit("KS"),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("KS"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas's president, Senate, governor, and House general elections by county",
            polity="US-KS",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license="Public record",
        )
        for year in DAYS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)
        medsl.attach(
            cs,
            conn,
            frame,
            {"KS": source},
            authoritative="The Secretary of State's county workbook for the general election.",
            extend=True,
        )
        return cs.write()
