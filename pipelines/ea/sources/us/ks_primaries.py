import re
import unicodedata
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ks_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://sos.ks.gov/elections/"
FILES = {
    (date(2020, 8, 4), "house"): "20elec/2020_Primary_Election_U.S._House_of_Representatives_results_by_precinct.xlsx",
    (date(2020, 8, 4), "senate"): "20elec/2020_Primary_Election_U.S._Senate_results_by_precinct.xlsx",
    (date(2022, 8, 2), "house"): "22elec/2022-Primary-Election-US-House-of-Representatives-Results-By-Precinct.xlsx",
    (date(2022, 8, 2), "senate"): "22elec/2022-Primary-Election-US-Senate-Results-By-Precinct.xlsx",
}
PUBLISHER = "Kansas Secretary of State"
TITLE = "Primary election results by precinct"


BIG = {"JOHNSON": ("johnson", 3), "SEDGWICK": ("sedgwick", 4), "SHAWNEE": ("shawnee", 2), "WYANDOTTE": ("wyandotte", 3)}
TOTALS = re.compile(r"^(county )?totals?$", re.IGNORECASE)


def number(text: str | None) -> int | None:
    plain = (text or "").replace(",", "").strip()
    return int(plain) if plain.isdigit() else None


def big_county(path: Path, sheet: str, county: str, fallback: int) -> list[tuple[str, str, int | None, str, int]]:
    grid = pl.read_excel(path, sheet_name=sheet, has_header=False, infer_schema_length=0).rows()
    total = next(
        row for row in grid if any(TOTALS.match((cell or "").strip()) for cell in row[:3] if isinstance(cell, str))
    )
    columns = [i for i, cell in enumerate(total) if i > 0 and number(cell) is not None]
    head = [r for r in range(6) if all(grid[r][i] is not None and number(grid[r][i]) is None for i in columns)][-1]
    carried: list[list[str]] = []
    for r in range(head):
        seen = ""
        line = []
        for cell in grid[r]:
            seen = cell.strip() if isinstance(cell, str) and cell.strip() else seen
            line.append(seen)
        carried.append(line)
    out = []
    flipped = False
    for i in columns:
        name = grid[head][i].strip()
        if name == "Total Votes Cast":
            flipped = True
            continue
        text = " ".join(line[i] for line in carried)
        party = None
        for line in carried:
            found = re.match(r"(DEM|REP)\b", line[i], re.IGNORECASE)
            party = found.group(1).upper() if found else party
        if flipped:
            party = "REP"
        office = "senate" if re.search(r"senat", text, re.IGNORECASE) else "house"
        district = re.search(r"(?:Rep\.?|Representative)\s*(\d)|(\d)\w* District", text)
        out.append(
            (
                office,
                "democratic" if party == "DEM" else "republican",
                None if office == "senate" else int(district.group(1) or district.group(2)) if district else fallback,
                name,
                number(total[i]),
            )
        )
    return out


def fetch() -> None:
    for name in FILES.values():
        raw.fetch(SOURCE, f"{BASE}{name}", suffix=".xlsx")


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
    for (day, chamber), name in FILES.items():
        path = raw.cached(SOURCE, f"{BASE}{name}").path
        frame = pl.read_excel(path, sheet_id=1, infer_schema_length=0)
        big = [
            (county.upper(), f"{office}:{party}", district, candidate.upper(), votes)
            for sheet in fastexcel.read_excel(path).sheet_names[1:]
            for county, fallback in [BIG[sheet.split(" - ")[0].upper()]]
            for office, party, district, candidate, votes in big_county(path, sheet, county, fallback)
            if office == chamber
        ]
        if big:
            wide = pl.DataFrame(big, schema=["County", "chamber", "district", "Candidate", "Votes"], orient="row")
            frames.append(
                wide.select(
                    year=pl.lit(day.year, pl.Int32),
                    chamber=pl.col("chamber"),
                    day=pl.lit(day),
                    special=pl.lit(False),
                    state=pl.lit("KS"),
                    district=pl.col("district").cast(pl.Int16),
                    code=pl.col("County").str.to_lowercase().replace_strict(counties, default=None),
                    candidate=pl.col("Candidate"),
                    votes=pl.col("Votes").cast(pl.Int64),
                )
            )
        frames.append(
            frame.select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.lit(chamber) + pl.lit(":") + pl.col("Party").str.to_lowercase(),
                day=pl.lit(day),
                special=pl.lit(False),
                state=pl.lit("KS"),
                district=pl.col("Race").str.extract(r"(\d+)$").cast(pl.Int16),
                code=pl.col("County")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                candidate=pl.col("Candidate").str.to_uppercase().str.strip_chars(),
                votes=pl.col("Votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
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
            summary="Kansas's 2020 and 2022 House and Senate primaries by county",
            polity="US-KS",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=f"{BASE}election-results-data.html",
            license="Public record",
        )
        for name in FILES.values():
            cs.artifact(raw.cached(SOURCE, f"{BASE}{name}"), source)
        medsl.attach(cs, conn, frame, {"KS": source}, strict=True)
        return cs.write()
