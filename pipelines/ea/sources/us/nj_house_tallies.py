import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.nj_federal import COUNTIES, ORDINALS
from ea.store import DATA, connect

SOURCE = "us.nj_house_tallies"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.nj.gov/state/elections/assets/pdf/election-results/"
LANDING = "https://www.nj.gov/state/elections/election-information-results.shtml"
PUBLISHER = "New Jersey Division of Elections"
TITLE = "Official list: candidate returns for House of Representatives"
FILES = {
    (date(2000, 11, 7), False): "2000/2000-general-elect-house-candidate-tallies.pdf",
    (date(2002, 11, 5), False): "2002/2002gen-elect-us-house-candidate_tally.pdf",
    (date(2004, 11, 2), False): "2004/2004-official_2004_gen_elect_congressional_results.pdf",
    (date(2006, 11, 7), False): "2006/2006_official-house-of-reps_tallies.pdf",
    (date(2006, 11, 7), True): "2006/2006_official-unexpired-term_tallies.pdf",
    (date(2008, 11, 4), False): "2008/2008-official-gen-elect-tallies-house-122908.pdf",
    (date(2010, 11, 2), False): "2010/2010-official-gen-elect-tallies-house-112910-1st-rev.pdf",
}
PLACE = re.compile(r"\s(" + "|".join(COUNTIES) + r")(?: \(part\))?(\s{2,}|$)", re.IGNORECASE)
LINE = re.compile(r"\s(" + "|".join(COUNTIES) + r")(?: \(part\))?\s{2,}.*?\s([\d,]+)\s*$", re.IGNORECASE)


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def parse() -> None:
    rows = []
    for (day, special), path in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        district, name, summed = None, None, 0
        for line in text.splitlines():
            if found := re.match(r"(\w+) Congressional District", line):
                district, name = [*ORDINALS, "Thirteenth"].index(found[1]) + 1, None
            elif found := re.match(r"\s+Total\s+([\d,]+)\s*$", line):
                if name is None or summed != int(found[1].replace(",", "")):
                    raise ValueError(f"{path}: {name} counties sum to {summed}, total {found[1]}")
                name = None
            else:
                if name is None and line[:1].isalpha() and PLACE.search(line):
                    name, summed = re.split(r"\s+\*|\s+\(w\)|\s{2,}", line.strip(), maxsplit=1)[0], 0
                if found := LINE.search(line):
                    votes = int(found[2].replace(",", ""))
                    summed += votes
                    rows.append((day.year, day, special, district, COUNTIES[found[1].upper()], name.upper(), votes))
        if name is not None:
            raise ValueError(f"{path}: {name} has no total")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "day": pl.Date,
            "special": pl.Boolean,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        chamber=pl.lit("house"),
        state=pl.lit("NJ"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("NJ"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's U.S. House generals by county, 2000-2010 (Division of Elections official lists)",
            polity="US-NJ",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(cs, conn, frame, {"NJ": source}, strict=True)
        return cs.write()
