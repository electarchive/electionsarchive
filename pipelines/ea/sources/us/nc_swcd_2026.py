import csv
import io
import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards, swcd_census
from ea.store import DATA

SOURCE = "us.nc_swcd_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://s3.amazonaws.com/dl.ncsbe.gov/Elections/2026/Candidate%20Filing/Candidate_Listing_2026.csv"
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "2026 Candidate Listing"
SANITARY = {
    "CARTERET - HARKERS ISLAND SANITARY BOARD MEMBER": ("Harkers Island Sanitary District", "Board Member"),
    "FIRST CRAVEN SANITARY DISTRICT BOARD MEMBER": ("First Craven Sanitary District", "Board Member"),
    "HANDY SANITARY DISTRICT SUPERVISOR": ("Handy Sanitary District", "Supervisor"),
    "GREENE - MAURY SANITARY LAND DISTRICT BOARD MEMBER": ("Maury Sanitary Land District", "Board Member"),
    "HAYWOOD - JUNALUSKA SANITARY DISTRICT": ("Junaluska Sanitary District", "Board Member"),
    "HAYWOOD - MAGGIE VALLEY SANITARY DISTRICT": ("Maggie Valley Sanitary District", "Board Member"),
    "HYDE - ENGELHARD SANITARY DISTRICT SUPERVISORS": ("Engelhard Sanitary District", "Supervisor"),
    "HYDE - OCRACOKE SANITARY DISTRICT SUPERVISORS": ("Ocracoke Sanitary District", "Supervisor"),
    "HYDE - SWAN QUARTER SANITARY DISTRICT SUPERVISORS": ("Swan Quarter Sanitary District", "Supervisor"),
    "PERQUIMANS - MINZIES CREEK SANITARY DISTRICT COMMISSIONER": ("Minzies Creek Sanitary District", "Commissioner"),
}
TRIM = re.compile(r"(?i) (?:county )?(?:soil and water conservation|soil conservation|soil and water) dist(?:rict)?$")


def fetch() -> None:
    swcd_census.copy_feed(SOURCE, URL)


def parse() -> None:
    held = swcd_census.conservation_districts("NC")
    rows: list[dict[str, object]] = []
    for record in csv.DictReader(
        io.StringIO(raw.cached(SOURCE, URL).path.read_text(encoding="utf-8", errors="replace"))
    ):
        contest = re.sub(r"\s+", " ", record["contest_name"]).strip()
        if record["election_dt"] != "11/03/2026":
            continue
        county = record["county_name"].strip()
        if "SOIL AND WATER" in contest:
            names = held.get(county, [])
            found = [name for name in names if len(names) == 1 or TRIM.sub("", name).upper() in contest]
            if not names and contest.endswith(" DISTRICT SUPERVISOR"):
                found = [contest.removesuffix(" SUPERVISOR").title().replace(" And ", " and ")]
            if len(found) != 1:
                print("no census district:", county, contest)
                continue
            district, word = found[0], "Supervisor"
        elif contest in SANITARY:
            district, word = SANITARY[contest]
        else:
            continue
        rows.append(
            {
                "district": district,
                "seat": "",
                "role": word,
                "name": record["name_on_ballot"].strip(),
                "county": county.title(),
                "url": URL,
                "vote": int(record["vote_for"] or 1),
                "tacit": False,
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["district", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet").with_columns(
        remainder=pl.lit(False),
        function=pl.when(pl.col("district").str.contains("(?i)soil"))
        .then(pl.lit("soil-and-water"))
        .otherwise(pl.lit("sanitary")),
    )
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="NC",
        summary="North Carolina soil and water conservation district and sanitary district races on the November 3, 2026 ballot, with candidates from the State Board of Elections' candidate listing",
        sources={URL: (PUBLISHER, TITLE)},
        frame=frame,
    )
