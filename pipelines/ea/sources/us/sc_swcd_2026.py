import csv
import io
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards, swcd_census
from ea.store import DATA

SOURCE = "us.sc_swcd_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://vrems.scvotes.sc.gov/Candidate/CandidateSearch?electionId=22596#ExportSearchResults"
PUBLISHER = "South Carolina Election Commission"
TITLE = "Candidate Listing: 2026 Statewide General Election, November 3, 2026"


def fetch() -> None:
    swcd_census.copy_feed(SOURCE, URL)


def parse() -> None:
    held = swcd_census.conservation_districts("SC")
    rows: list[dict[str, object]] = []
    for record in csv.DictReader(io.StringIO(raw.cached(SOURCE, URL).path.read_text(encoding="utf-8-sig"))):
        if record["Office"].strip() != "Soil and Water District Commission" or record["Status"] != "Active":
            continue
        county = record["Associated Counties"].strip()
        names = held.get(county, [])
        if len(names) != 1:
            print("no census district:", county)
            continue
        rows.append(
            {
                "district": names[0],
                "seat": "",
                "role": "Commissioner",
                "name": f"{record['Ballot Name (first - middle)']} {record['Ballot Name (last - suffix)']}".strip(),
                "county": county.title(),
                "url": URL,
                "vote": None,
                "tacit": False,
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema_overrides={"vote": pl.Int64}).unique(
        ["district", "name"], keep="first", maintain_order=True
    )
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet").with_columns(
        remainder=pl.lit(False), function=pl.lit("soil-and-water")
    )
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="SC",
        summary="South Carolina soil and water conservation district commission races on the November 3, 2026 ballot, with candidates from the State Election Commission's candidate listing",
        sources={URL: (PUBLISHER, TITLE)},
        frame=frame,
    )
