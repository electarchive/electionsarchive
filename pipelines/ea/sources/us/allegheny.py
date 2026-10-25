import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.sources.us import ca_precincts
from ea.store import DATA

SOURCE = "us.allegheny"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Allegheny County Division of Elections"
TITLE = "Election results by precinct (Clarity detail XML)"
LISTING = "https://data.wprdc.org/dataset/election-results"
PACKAGE = "https://data.wprdc.org/api/3/action/package_show?id=election-results"
METHOD = (
    "Pittsburgh's wards and divisions from the county's own election night reporting export for the election, "
    "published on the Western Pennsylvania Regional Data Center; election day, absentee, and provisional ballots are "
    "summed per division and write-in votes are left out."
)
CITY = re.compile(r"(?i)^Pittsburgh Ward (\d+) Dist (\d+)$")


def zips() -> list[str]:
    package = json.loads(raw.cached(SOURCE, PACKAGE).path.read_bytes())
    return [
        resource["url"]
        for resource in package["result"]["resources"]
        if resource["format"] == "ZIP" and "detailxml" in resource["url"]
    ]


def fetch() -> None:
    raw.fetch(SOURCE, PACKAGE, suffix=".json")
    for url in zips():
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    records: list[tuple[object, ...]] = []
    for url in zips():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive, archive.open("detail.xml") as stream:
            day = None
            contest = None
            candidate = None
            channel = None
            for event, element in ElementTree.iterparse(stream, events=("start", "end")):
                if event == "start":
                    if element.tag == "Contest":
                        contest = element.get("text")
                    elif element.tag == "Choice":
                        candidate = element.get("text")
                    elif element.tag == "VoteType":
                        channel = element.get("name")
                    continue
                if element.tag == "ElectionDate":
                    month, number, year = (element.text or "").strip().split("/")
                    day = f"{year}-{int(month):02d}-{int(number):02d}"
                elif element.tag == "Precinct" and candidate is not None and channel != "regVotersCounty":
                    found = CITY.match(element.get("name") or "")
                    if found is not None:
                        records.append(
                            (
                                day,
                                url,
                                contest,
                                f"Ward {found[1]} Dist {found[2]}",
                                candidate,
                                int(element.get("votes") or 0),
                            )
                        )
                elif element.tag == "Choice":
                    candidate = None
                    element.clear()
                elif element.tag == "Contest":
                    contest = None
                    element.clear()
    rows = (
        pl.DataFrame(records, schema=["day", "url", "contest", "precinct", "candidate", "votes"], orient="row")
        .with_columns(pl.col("day").str.to_date(), pl.col("candidate").str.replace_all(r"\s+", " ").str.strip_chars())
        .group_by("day", "url", "contest", "precinct", "candidate", maintain_order=True)
        .agg(pl.col("votes").sum())
        .with_columns(registered=pl.lit(None, pl.Int64), ballots=pl.lit(None, pl.Int64))
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    rows.write_parquet(PARSED / "rows.parquet")
    print(rows.group_by("day").agg(pl.col("contest").n_unique(), pl.col("precinct").n_unique()).sort("day"))


def emit() -> Path:
    return ca_precincts.emit(
        pipeline=SOURCE,
        version=VERSION,
        summary="Pittsburgh's contests by ward and division from Allegheny County's election results exports",
        rows=pl.read_parquet(PARSED / "rows.parquet").filter(~pl.col("candidate").str.contains(r"(?i)write.?in")),
        fips="42003",
        place="PITTSBURGH",
        publisher=PUBLISHER,
        title=TITLE,
        listing=LISTING,
        method=METHOD,
        state_code="PA",
        names_only=True,
    )
