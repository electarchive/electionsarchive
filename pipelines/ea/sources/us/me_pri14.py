import re
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.me_pri14"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://web.archive.org/web/2016id_/https://www.maine.gov/sos/cec/elec/results/2014-15/"
DAY = date(2014, 6, 10)
FILES = [
    ("ussendemprim", "senate:democratic"),
    ("ussenaterepprim", "senate:republican"),
    ("repcongdemprim", "house:democratic"),
    ("repcongrepprim", "house:republican"),
]
COUNTIES = {
    "ANDROSCOGGIN": "23001",
    "AROOSTOOK": "23003",
    "CUMBERLAND": "23005",
    "FRANKLIN": "23007",
    "HANCOCK": "23009",
    "KENNEBEC": "23011",
    "KNOX": "23013",
    "LINCOLN": "23015",
    "OXFORD": "23017",
    "PENOBSCOT": "23019",
    "PISCATAQUIS": "23021",
    "SAGADAHOC": "23023",
    "SOMERSET": "23025",
    "WALDO": "23027",
    "WASHINGTON": "23029",
    "YORK": "23031",
}
PUBLISHER = "Maine Secretary of State"
TITLE = "Election results: primary tabulations by municipality"


def fetch() -> None:
    for name, _ in FILES:
        raw.fetch(SOURCE, f"{BASE}{name}.xls", suffix=".xls")


def parse() -> None:
    rows = []
    for name, chamber in FILES:
        workbook = fastexcel.read_excel(raw.cached(SOURCE, f"{BASE}{name}.xls").path)
        for sheet in [0] if chamber.startswith("senate") else [0, 1]:
            table = workbook.load_sheet(sheet, header_row=None, dtypes="string").to_polars().rows()
            names = {
                i: re.split(r"\s{2,}", cell.strip())[0].upper()
                for i, cell in enumerate(table[0])
                if i > 0 and cell and not cell.strip().startswith("%") and cell.strip().upper() != "TOTAL BALLOTS CAST"
            }
            block = []
            for row in table[1:]:
                label = " ".join((row[0] or "").split()).upper()
                if not label:
                    continue
                found = next((c for c in COUNTIES if c in label and "COUNT" in label), None) or next(
                    (c for c in COUNTIES if label.startswith(f"TOTAL {c}")), None
                )
                if found is not None:
                    for i, candidate in names.items():
                        printed = int(float(row[i] or 0))
                        counted = sum(int(float(r[i])) for r in block if r[i] not in (None, ""))
                        if printed != counted:
                            print(chamber, sheet, found, candidate, "towns", counted, "printed", printed)
                    for r in block:
                        for i, candidate in names.items():
                            if r[i] not in (None, ""):
                                rows.append(
                                    (
                                        2014,
                                        chamber,
                                        DAY,
                                        False,
                                        "ME",
                                        sheet + 1 if chamber.startswith("house") else None,
                                        COUNTIES[found],
                                        candidate,
                                        int(float(r[i])),
                                        "county",
                                    )
                                )
                    block = []
                elif "UOCAVA" in label:
                    for i, candidate in names.items():
                        if row[i] not in (None, ""):
                            rows.append(
                                (
                                    2014,
                                    chamber,
                                    DAY,
                                    False,
                                    "ME",
                                    sheet + 1 if chamber.startswith("house") else None,
                                    "ME:uocava",
                                    candidate,
                                    int(float(row[i])),
                                    "aggregate",
                                )
                            )
                elif not label.startswith(("TOTAL", "STATEWIDE", "REPRESENTATIVE")):
                    block.append(row)
            if block:
                print(chamber, sheet, "rows without a county total:", len(block))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
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
                "votes": pl.Int64,
                "kind": pl.String,
            },
            orient="row",
        )
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("OTHERS|WRITE"),
        )
        .filter(pl.col("measure") > 0)
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
            summary="Maine's 2014 U.S. Senate and House primaries by county",
            polity="US-ME",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.maine.gov/sos/cec/elec/results/results14-15.html",
            license="Public record",
        )
        for name, _ in FILES:
            cs.artifact(raw.cached(SOURCE, f"{BASE}{name}.xls"), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"ME": source}, strict=True)
        return cs.write()
