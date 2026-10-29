import io
import os
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, nj_federal, nj_general_2024, openelections
from ea.store import DATA, connect

SOURCE = "us.general_2026"
VERSION = "1"
YEAR = int(os.environ.get("GENERAL_YEAR", "2026"))
DAY = {2024: date(2024, 11, 5), 2026: date(2026, 11, 3)}[YEAR]
PARSED = DATA / "parsed" / SOURCE / str(YEAR)
NJ_FILES = [f"{YEAR}/{YEAR}-official-general-results-us-senate.pdf", f"{YEAR}/{YEAR}-official-general-results-us-house.pdf"]
HI_BASE = f"https://elections.hawaii.gov/wp-content/results/{YEAR}%20General/"
HI_COUNTIES = {"coh": "15001", "cch": "15003", "cok": "15007", "com": "15009"}
HI_HEADER = re.compile(r"(President and Vice President|U\.S\. Senator|U\.S\. Representative, Dist (I+)|Governor and Lieutenant Governor)$")
HI_ROMAN = {"I": 1, "II": 2}
OE_BASE = "https://raw.githubusercontent.com/openelections/openelections-data-{state}/master/{year}/{day}__{state}__general__county.csv"
STATES = os.environ.get("GENERAL_STATES", "NJ,HI,MS,WY,ND,TN").split(",")
OE_STATES = [state for state in ("MS", "WY", "ND", "TN") if state in STATES]
OE_OFFICES = {"President": "president", "U.S. Senate": "senate", "U.S. House": "house", "Governor": "governor"}
PUBLISHERS = {
    "NJ": ("New Jersey Division of Elections", "Official general election results", "https://www.nj.gov/state/elections/election-information-results.shtml"),
    "HI": ("Hawaii Office of Elections", "General election summary reports by county", "https://elections.hawaii.gov/resources/election-results/"),
}


def oe_url(state: str) -> str:
    return OE_BASE.format(state=state.lower(), year=YEAR, day=f"{DAY:%Y%m%d}")


def fetch() -> None:
    for path in NJ_FILES if "NJ" in STATES else []:
        raw.fetch(SOURCE, nj_federal.BASE + path, suffix=".pdf")
    for county in HI_COUNTIES if "HI" in STATES else []:
        raw.fetch(SOURCE, f"{HI_BASE}{county}.pdf", suffix=".pdf")
    for state in OE_STATES:
        raw.fetch(SOURCE, oe_url(state), suffix=".csv")


def parse() -> None:
    rows = nj_general_2024.rows(SOURCE, NJ_FILES, DAY) if "NJ" in STATES else []
    for county, code in HI_COUNTIES.items() if "HI" in STATES else []:
        text = subprocess.run(
            ["pdftotext", "-raw", str(raw.cached(SOURCE, f"{HI_BASE}{county}.pdf").path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        office, district, pending = None, None, None
        for line in text.splitlines():
            line = line.strip()
            if header := HI_HEADER.match(line):
                office = "president" if line.startswith("President") else "senate" if "Senator" in line else "governor" if line.startswith("Governor") else "house"
                district = HI_ROMAN[header[2]] if header[2] else None
            elif line.startswith("Over Votes"):
                office = pending = None
            elif office and (found := re.fullmatch(r"\(\w+\) (.+?)(?: ([\d,]+) [\d.]+%)?", line)):
                pending = found[1]
                if found[2]:
                    rows.append((YEAR, office, DAY, False, "HI", district, code, pending.upper(), 1, False, int(found[2].replace(",", ""))))
                    pending = None
            elif office and pending and (found := re.fullmatch(r"([\d,]+) [\d.]+%", line)):
                rows.append((YEAR, office, DAY, False, "HI", district, code, pending.upper(), 1, False, int(found[1].replace(",", ""))))
                pending = None
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    )
    frame = frame.group_by(frame.columns[:-1]).agg(pl.col("votes").sum())
    with connect() as conn:
        fips = openelections.counties(conn)
    oe = []
    for state in OE_STATES:
        csv = pl.read_csv(io.BytesIO(raw.cached(SOURCE, oe_url(state)).path.read_bytes()), infer_schema_length=0)
        oe.append(
            csv.filter(pl.col("office").is_in(list(OE_OFFICES)), pl.col("county").is_not_null())
            .filter(~pl.col("county").str.to_uppercase().str.contains("TOTAL"))
            .select(
                year=pl.lit(YEAR, pl.Int32),
                chamber=pl.col("office").replace_strict(OE_OFFICES),
                day=pl.lit(DAY),
                special=pl.lit(False),
                state=pl.lit(state),
                district=pl.col("district").cast(pl.Int16, strict=False),
                code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").str.replace("^jeffdavis$", "jeffersondavis").replace_strict(fips[state], default=None),
                candidate=pl.col("candidate").str.replace(r" for President and .* for Vice President$", "").str.strip_chars(),
                measure=pl.col("candidate").str.to_uppercase().replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
                writein=pl.col("candidate").str.to_uppercase().is_in([*medsl.POOLED, "MISC."]) | pl.col("candidate").str.contains("(?i)write"),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False),
            )
        )
    oe_frame = (
        pl.concat(oe)
        .filter(pl.col("votes").is_not_null(), pl.col("measure") > 0)
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
    )
    lost = oe_frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost.select('state').unique().rows()}")
    oe_frame = oe_frame.group_by(oe_frame.columns[:-1]).agg(pl.col("votes").sum())
    both = pl.concat([frame, oe_frame.select(frame.columns)]).with_columns(kind=pl.lit("county"), source=pl.col("state"))
    PARSED.mkdir(parents=True, exist_ok=True)
    both.write_parquet(PARSED / "counties.parquet")
    print(both.group_by("state", "chamber").agg(pl.col("votes").sum(), pl.col("code").n_unique()).sort("state", "chamber").rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"{YEAR} general elections by county from state canvass files: New Jersey and Hawaii official reports, and OpenElections for Mississippi, Wyoming, North Dakota, and Tennessee",
            polity="US",
        )
        sources = {}
        for state in frame["state"].unique().sort():
            publisher, title, url = PUBLISHERS.get(state, ("OpenElections", f"{state} general election results by county", f"https://github.com/openelections/openelections-data-{state.lower()}"))
            sources[state] = cs.source(publisher, title)
            if sources[state] in cs.minted:
                cs.add("source", id=sources[state], kind="official_body" if state in PUBLISHERS else "community", publisher=publisher, title=title, url=url, license="Public record" if state in PUBLISHERS else None)
        for path in NJ_FILES if "NJ" in sources else []:
            cs.artifact(raw.cached(SOURCE, nj_federal.BASE + path), sources["NJ"])
        for county in HI_COUNTIES if "HI" in sources else []:
            cs.artifact(raw.cached(SOURCE, f"{HI_BASE}{county}.pdf"), sources["HI"])
        for state in OE_STATES:
            cs.artifact(raw.cached(SOURCE, oe_url(state)), sources[state])
        medsl.attach(cs, conn, frame, sources, strict=True)
        return cs.write()
