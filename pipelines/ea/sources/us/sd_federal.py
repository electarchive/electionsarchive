import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.sources.us.sd_sos import PUBLISHER, SITE, TITLE, table, url
from ea.store import DATA, connect

SOURCE = "us.sd_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WIDE = {
    "2000/2000_general_election_official_returns_US_house_representatives.aspx": (date(2000, 11, 7), ["house"]),
    "2002/2002_US_representative_official_returns.aspx": (date(2002, 11, 5), ["house"]),
    "2004/2004_general_election_official_returns_us_house_of_representatives.aspx": (date(2004, 11, 2), ["house"]),
    "2006/2006_general_election_official_returns_us_house_of_representatives.aspx": (date(2006, 11, 7), ["house"]),
    "2000/2000_statewide_primaries.aspx": (date(2000, 6, 6), [None, "house:democratic"]),
    "2002/2002_statewide_primary_official_returns.aspx": (
        date(2002, 6, 4),
        ["house:republican", "house:democratic", "senate:democratic"],
    ),
    "2004/2004_democratic_presidential_primary_official_returns.aspx": (date(2004, 6, 1), ["president:democratic"]),
}
LONG = {
    "2008/2008_general_election_statewide_races_county_totals.aspx": (
        date(2008, 11, 4),
        {("United States Representative", ""): "house"},
    ),
    "2010/2010_general_election_statewide_candidates_by_county.aspx": (
        date(2010, 11, 2),
        {("United States Representative", ""): "house"},
    ),
    "2008/2008_primary_statewide_races_by_county.aspx": (
        date(2008, 6, 3),
        {
            ("Presidential Delegates and Alternates", "DEM"): "president:democratic",
            ("Presidential Delegates and Alternates", "REP"): "president:republican",
            ("United States Senate", "REP"): "senate:republican",
        },
    ),
    "2010/2010_primary_statewide_race_returns_by_county.aspx": (
        date(2010, 6, 8),
        {("United States Representative", "REP"): "house:republican"},
    ),
}
PDF = (
    "https://sdsos.gov/elections-voting/assets/Archive/2012%20Assets/2012generalelectionstatewidecandidatesbycounty.pdf"
)
PDF_DAY = date(2012, 11, 6)


def fetch() -> None:
    for path in [*WIDE, *LONG]:
        raw.fetch(SOURCE, url(path), suffix=".html")
    raw.fetch(SOURCE, PDF, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["SD"]
    rows: list[tuple[date, str, str, str, int]] = []
    for path, (day, chambers) in WIDE.items():
        blocks: list[tuple[list[str], list[tuple[str, list[int]]], list[int]]] = []
        for cells in table(path, SOURCE):
            if not cells or not any(cells):
                continue
            if re.match(r"TOTALS?\s*[-:]", cells[0]):
                printed = [int(number) for number in re.findall(r"\d+", " ".join(cells))]
                blocks[-1] = (blocks[-1][0], blocks[-1][1], printed)
            elif cells[0].upper().startswith("PERCENT"):
                continue
            elif all(re.fullmatch(r"\d*", cell) for cell in cells[1:]):
                blocks[-1][1].append(
                    (fips[re.sub(r"[^a-z]", "", cells[0].lower())], [int(cell or 0) for cell in cells[1:]])
                )
            else:
                blocks.append(([re.sub(r"\s+-\s*[A-Za-z]{1,3}$", "", cell) for cell in cells[1:] if cell], [], []))
        if len(blocks) < len(chambers):
            raise ValueError(f"{path}: {len(blocks)} blocks")
        for chamber, (names, counties, printed) in zip(chambers, blocks, strict=False):
            if chamber is None:
                continue
            sums = [sum(values[k] for _, values in counties) for k in range(len(names))]
            if len(counties) != 66 or printed != [sum(sums), *sums]:
                raise ValueError(f"{path} {chamber}: {len(counties)} counties sum to {sums}, printed {printed}")
            rows.extend(
                (day, chamber, code, name.upper(), values[k])
                for code, values in counties
                for k, name in enumerate(names)
            )
    for path, (day, races) in LONG.items():
        for race, county, party, candidate, votes, *_ in table(path, SOURCE)[1:]:
            chamber = races.get((race, party)) or races.get((race, ""))
            if chamber is not None:
                rows.append((day, chamber, fips[re.sub(r"[^a-z]", "", county.lower())], candidate.upper(), int(votes)))
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, PDF).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    names: list[str] = []
    counties: list[tuple[str, list[int]]] = []
    for line in text.splitlines():
        if line.startswith("County "):
            names, counties = re.split(r"\s{2,}", line.strip())[1:-1], []
        elif row := re.match(r"^(\S.*?)\s{2,}(\d+(?:\s+\d+)+)\s*$", line):
            numbers = [int(value) for value in row[2].split()]
            if len(numbers) != len(names) + 1 or sum(numbers[:-1]) != numbers[-1]:
                raise ValueError(f"2012 {names}: {line}")
            if row[1] != "TOTAL":
                counties.append((fips[re.sub(r"[^a-z]", "", row[1].lower())], numbers[:-1]))
                continue
            if [sum(values[k] for _, values in counties) for k in range(len(names))] != numbers[:-1] or len(
                counties
            ) != 66:
                raise ValueError(f"2012 {names}: counties do not add up")
            if names == ["Kristi Noem", "Matt Varilek"]:
                rows.extend(
                    (PDF_DAY, "house", code, name.upper(), values[k])
                    for code, values in counties
                    for k, name in enumerate(names)
                )
    frame = pl.DataFrame(
        rows,
        schema={"day": pl.Date, "chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    )
    with pl.Config(tbl_rows=80):
        print(frame.group_by("day", "chamber", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("day", "chamber"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("SD"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("SD"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Dakota's U.S. House generals 2000-2012 and federal primaries 2000-2010 by county "
            "(Secretary of State election history)",
            polity="US-SD",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=SITE,
                license="Public record",
            )
        for path in [*WIDE, *LONG]:
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        cs.artifact(raw.cached(SOURCE, PDF), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"SD": source}, strict=True)
        return cs.write()
