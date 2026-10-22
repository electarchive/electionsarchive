import re
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.me_federal import COUNTIES, PUBLISHER, TITLE
from ea.store import DATA, connect

SOURCE = "us.me_pri_leg"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://web.archive.org/web/2016id_/http://www.maine.gov/sos/cec/elec/results/2014-15/"
DAY = date(2014, 6, 10)
FILES = [
    ("srdemprim", "house", "dp"),
    ("srrepprim", "house", "rp"),
    ("statesenatedemprim", "senate", "dp"),
    ("ssrepprim", "senate", "rp"),
]
ROSTER = "primarycandidates"


def fetch() -> None:
    for name in [*(name for name, *_ in FILES), ROSTER]:
        raw.fetch(SOURCE, f"{BASE}{name}.xls", suffix=".xls")


def sheet(name: str) -> list[list[str]]:
    return [
        [(cell or "").strip() for cell in row]
        for row in fastexcel.read_excel(raw.cached(SOURCE, f"{BASE}{name}.xls").path)
        .load_sheet(0, header_row=None, dtypes="string")
        .to_polars()
        .rows()
    ]


def parse() -> None:
    homes: dict[str, set[str]] = {}
    roster = sheet(ROSTER)
    columns = roster[0]
    for row in roster[1:]:
        cells = dict(zip(columns, row, strict=True))
        if cells["Office"] in ("SR", "SS") and cells["Last Name"] and cells["First Name"]:
            key = " ".join(f"{cells['Last Name']}, {cells['First Name']}".lower().split())
            homes.setdefault(key, set()).add(cells["Residence Municipality"].lower())
    lines: list[tuple[str, int, str, str | None, str, str, int]] = []
    for name, chamber, party in FILES:
        rows = sheet(name)
        if rows[0][0] == "TOWN":
            block: list[list[str]] = []
            shaped: list[list[str]] = []
            for row in rows:
                if found := re.fullmatch(r"(?i)(?:total\s+)?d[a-z]{5,7}\s+(\d+)(?:\s+totals)?", row[0]):
                    shaped += [[found[1] if line[0] not in ("", "TOWN") else "", "", *line] for line in block]
                    block = []
                else:
                    block.append(row)
            rows = shaped
        heading: dict[int, str] = {}
        expect = True
        for cells in rows:
            if len(cells) > 3 and re.fullmatch(r"\d+(?:\.0)?", cells[0]):
                if cells[2].lower().startswith("total"):
                    continue
                for column, candidate in heading.items():
                    if column < len(cells) and cells[column]:
                        votes = int(float(cells[column].replace(",", "")))
                        lines.append(
                            (chamber, int(float(cells[0])), party, cells[1] or None, cells[2], candidate, votes)
                        )
                expect = True
                continue
            texts = {
                column: cell
                for column, cell in enumerate(cells)
                if column >= 3 and cell and not re.fullmatch(r"[\d,.%]+", cell)
            }
            if expect and texts:
                heading = {}
                for column, cell in texts.items():
                    if re.fullmatch(r"(?i)blank|total ballots cast|tbc|total votes cast", cell):
                        continue
                    candidate = re.split(r"\s{2,}|(?i:\s*\(declared write-in\))", cell)[0]
                    places = {
                        home for key, found in homes.items() if candidate.lower().startswith(key) for home in found
                    }
                    heading[column] = next(
                        (candidate[: -len(home) - 1] for home in places if candidate.lower().endswith(f" {home}")),
                        candidate,
                    )
                expect = False
    frame = pl.DataFrame(
        lines,
        schema={
            "chamber": pl.String,
            "district": pl.Int16,
            "party": pl.String,
            "county": pl.String,
            "town": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        uocava=pl.col("town").str.to_uppercase().str.contains("UOCAVA"),
        place=pl.col("town").str.to_uppercase().str.replace_all(r"[^A-Z0-9]", ""),
    )
    seat = ["chamber", "district", "place"]
    local = frame.filter(pl.col("county").is_not_null()).select(*seat, "county").unique()
    local = local.filter(pl.len().over(seat) == 1).rename({"county": "local"})
    known = frame.filter(pl.col("county").is_not_null()).select("place", "county").unique()
    known = known.filter(pl.len().over("place") == 1).rename({"county": "home"})
    frame = (
        frame.join(local, on=seat, how="left")
        .join(known, on="place", how="left")
        .with_columns(county=pl.coalesce("county", "local", "home"))
    )
    lost = frame.filter(pl.col("county").is_null(), ~pl.col("uocava"))
    if lost.height:
        raise ValueError(f"no county for {lost['town'].unique().to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.select(
            year=pl.lit(DAY.year, pl.Int32),
            chamber=pl.concat_str("chamber", pl.col("district").cast(pl.String), "party", separator=":"),
            day=pl.lit(DAY),
            special=pl.lit(False),
            state=pl.lit("ME"),
            district=pl.lit(None, pl.Int16),
            code=pl.when("uocava").then(pl.lit("ME:uocava")).otherwise(pl.col("county").replace_strict(COUNTIES)),
            kind=pl.when("uocava").then(pl.lit("aggregate")).otherwise(pl.lit("county")),
            candidate=pl.col("candidate").str.to_uppercase(),
            measure=pl.lit(1, pl.Int16),
            writein=pl.lit(False),
            votes="votes",
        )
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "kind", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(source=pl.lit("ME"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's 2014 State House and Senate Democratic and Republican primaries by county",
            polity="US-ME",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            raise ValueError(f"no source {TITLE}")
        for name in [*(name for name, *_ in FILES), ROSTER]:
            cs.artifact(raw.cached(SOURCE, f"{BASE}{name}.xls"), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"ME": source}, strict=True)
        return cs.write()
