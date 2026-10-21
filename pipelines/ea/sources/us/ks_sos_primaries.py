import html
import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ks_sos_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://web.archive.org/web/{stamp}id_/http://www.kssos.org/elections/"
LIVE = "https://sos.ks.gov/elections/"
WYANDOTTE = {date(2018, 8, 7): "https://wycovoteskck.gov/2018-primary-election-results"}
FILES = {
    (date(2000, 8, 1), "house"): ("20140113113855", "00elec/2000_primary_uscongress_official_results.xls"),
    (date(2002, 8, 6), "house"): ("20041011130211", "02elec/ELUSHSE02P.xls"),
    (date(2002, 8, 6), "senate"): ("20041011130211", "02elec/ELUSSEN02P.xls"),
    (date(2004, 8, 3), "house"): ("20041217021415", "04elec/ELUSHOU04P.xls"),
    (date(2004, 8, 3), "senate"): ("2016", "04elec/ELUSSEN04P.xls"),
    (date(2006, 8, 1), "house"): ("20060906191042", "06elec/ELUSHOU06P.xls"),
    (date(2008, 8, 5), "house"): ("20100713114754", "08elec/2008_primary_us_house_official_results.xls"),
    (date(2008, 8, 5), "senate"): ("20100613050750", "08elec/2008_primary_us_senate_official_results.xls"),
    (date(2010, 8, 3), "house"): ("20101202033458", "10elec/2010_primary_us_house_official_results.xls"),
    (date(2010, 8, 3), "senate"): ("20101011005454", "10elec/2010_primary_us_senate_official_results.xls"),
    (date(2014, 8, 5), "senate"): ("20141014113038", "14elec/2014_primary_us_senate_official_results.xls"),
    (date(2018, 8, 7), "house"): (None, "18elec/2018_Primary_Election_US_House_of_Representatives_Precinct_Level_Results.xlsx"),
}
PUBLISHER = "Kansas Secretary of State"
TITLE = "Primary election official results by precinct"
LANDING = "https://sos.ks.gov/elections/election-results.html"


def url(key: tuple[date, str]) -> str:
    stamp, name = FILES[key]
    return LIVE + name if stamp is None else BASE.format(stamp=stamp) + name


def fetch() -> None:
    for key in FILES:
        raw.fetch(SOURCE, url(key), suffix=".xlsx" if url(key).endswith("x") else ".xls")
    for page in WYANDOTTE.values():
        raw.fetch(SOURCE, page, suffix=".html")


def long_rows(path: Path) -> pl.DataFrame:
    frame = pl.read_excel(path, sheet_id=1, infer_schema_length=0)
    if "DISTRICT" not in frame.columns:
        frame = frame.with_columns(DISTRICT=pl.lit(None, pl.String))
    return frame.select(
        county=pl.col("COUNTY"),
        party=pl.col("PARTY"),
        district=pl.col("DISTRICT"),
        candidate=pl.concat_str(["FIRST", "MIDDLE", "LAST", "SUFFIX"], separator=" ", ignore_nulls=True)
        .str.replace_all(r"\s+", " ")
        .str.strip_chars(),
        votes=pl.col("VOTES"),
    )


def swapped(name: str) -> str:
    last, _, first = name.partition(",")
    return " ".join((f"{first.strip()} {last}" if first else last).replace("Rep. ", "").split())


def rows_2018(path: Path, day: date) -> pl.DataFrame:
    frame = pl.read_excel(path, sheet_name="PRECINCT", infer_schema_length=0)
    out = [
        (county, party, district, swapped(candidate), votes)
        for county, district, candidate, party, votes in frame.select(
            "County", "District", "Candidate", "Party", "Votes"
        ).rows()
    ]
    cell = lambda text: (text or "").replace(" ", "").replace(",", "")  # noqa: E731
    grid = pl.read_excel(path, sheet_name="JOHNSON", has_header=False, infer_schema_length=0).rows()
    total = next(row for row in grid if row[2] == "COUNTY TOTALS")
    for i in range(3, len(total)):
        out.append(("Johnson", {"DEM": "Democratic", "REP": "Republican"}[grid[1][i]], "3", grid[2][i], cell(total[i])))
    grid = pl.read_excel(path, sheet_name="SEDGWICK", has_header=False, infer_schema_length=0).rows()
    for row in grid[2 : next(r for r, row in enumerate(grid) if row[0] == "Votes by Precinct")]:
        for at, party in ((0, "Democratic"), (4, "Republican")):
            if row[at] is not None and row[at + 1] is not None:
                out.append(("Sedgwick", party, "4", row[at].removeprefix("Rep. "), row[at + 1]))
    grid = pl.read_excel(path, sheet_name="SHAWNEE", has_header=False, infer_schema_length=0).rows()
    total = next(row for row in grid if row[0] == "COUNTY TOTALS")
    for i in range(1, len(total)):
        out.append(
            (
                "Shawnee",
                "Democratic" if grid[1][i] == "DEM" else "Republican",
                "2",
                " ".join(grid[2][i].split()),
                cell(total[i]),
            )
        )
    page = raw.cached(SOURCE, WYANDOTTE[day]).path.read_text()
    text = html.unescape(re.sub(r"<[^>]+>", "\n", page))
    for party, block in re.findall(
        r"\((Democratic|Republican) Party\) \*+\s*United States Representative3rd District\s*\(VOTE FOR\)\s*\d+\s*(.*?)\s*Governor", text, re.S
    ):
        for name, votes in re.findall(r"^(.*?)\s*(?:\.\s*)+\s*([\d,]+)\s+[\d.]+\s*$", block, re.M):
            out.append(("Wyandotte", party, "3", name.strip(), cell(votes)))
    return pl.DataFrame(out, schema=["county", "party", "district", "candidate", "votes"], orient="row")


def wide_rows(path: Path) -> pl.DataFrame:
    grid = pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0).rows()
    out = []
    for row in grid[3:]:
        if row[1] is None or row[2] is not None or row[3] is not None:
            continue
        for i in range(4, len(row)):
            if row[i] is None or grid[0][i] is None:
                continue
            district = re.search(r"District (\d+)", grid[1][i]).group(1)
            if district == row[0]:
                out.append((row[1].removesuffix(" County"), grid[2][i], district, grid[0][i], row[i]))
    return pl.DataFrame(out, schema=["county", "party", "district", "candidate", "votes"], orient="row")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '20%%'"""
            ).fetchall()
        }
    frames = []
    for key in FILES:
        day, chamber = key
        path = raw.cached(SOURCE, url(key)).path
        rows = wide_rows(path) if day.year == 2000 else rows_2018(path, day) if day.year == 2018 else long_rows(path)
        frames.append(
            rows.select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.lit(chamber)
                + pl.lit(":")
                + pl.col("party").str.to_lowercase().str.replace(r"^democrat$", "democratic"),
                day=pl.lit(day),
                special=pl.lit(False),
                state=pl.lit("KS"),
                district=pl.col("district").cast(pl.Int16),
                code=pl.col("county")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                candidate=pl.col("candidate").str.to_uppercase().str.strip_chars(),
                votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
            )
        )
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['candidate'].head(5).to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source=pl.lit("KS"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas's House and Senate primaries by county, 2000-2014 (Secretary of State)",
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
        for key in FILES:
            cs.artifact(raw.cached(SOURCE, url(key)), source)
        medsl.attach(cs, conn, frame, {"KS": source}, strict=True)
        return cs.write()
