import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wi_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://elections.wi.gov/sites/default/files/documents/"
DAY = date(2022, 8, 9)
FILES = [
    "County by County Report - 2022 Partisan Primary - US Senator.xlsx",
    "County by County Report - 2022 Partisan Primary - Representative in Congress.xlsx",
]
BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"
PARTIES = {
    "Democratic": "democratic",
    "Republican": "republican",
    "Libertarian": "libertarian",
    "Constitution": "constitution",
}
NAMES = [
    "Adams",
    "Ashland",
    "Barron",
    "Bayfield",
    "Brown",
    "Buffalo",
    "Burnett",
    "Calumet",
    "Chippewa",
    "Clark",
    "Columbia",
    "Crawford",
    "Dane",
    "Dodge",
    "Door",
    "Douglas",
    "Dunn",
    "Eau_Claire",
    "Florence",
    "Fond_du_Lac",
    "Forest",
    "Grant",
    "Green",
    "Green_Lake",
    "Iowa",
    "Iron",
    "Jackson",
    "Jefferson",
    "Juneau",
    "Kenosha",
    "Kewaunee",
    "La_Crosse",
    "Lafayette",
    "Langlade",
    "Lincoln",
    "Manitowoc",
    "Marathon",
    "Marinette",
    "Marquette",
    "Milwaukee",
    "Monroe",
    "Oconto",
    "Oneida",
    "Outagamie",
    "Ozaukee",
    "Pepin",
    "Pierce",
    "Polk",
    "Portage",
    "Price",
    "Racine",
    "Richland",
    "Rock",
    "Rusk",
    "Saint_Croix",
    "Sauk",
    "Sawyer",
    "Shawano",
    "Sheboygan",
    "Taylor",
    "Trempealeau",
    "Vernon",
    "Vilas",
    "Walworth",
    "Washburn",
    "Washington",
    "Waukesha",
    "Waupaca",
    "Waushara",
    "Winnebago",
    "Wood",
]
CODES = {name.replace("_", " ").upper(): f"55{2 * i + 1:03}" for i, name in enumerate(NAMES)}
CODES["MENOMINEE"] = "55078"
CODES["ST. CROIX"] = CODES.pop("SAINT CROIX")
PUBLISHER = "Wisconsin Elections Commission"
TITLE = "County by County Report"


def fetch() -> None:
    for name in FILES:
        url = BASE + name.replace(" ", "%20")
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(["curl", "-sfL", "-A", BROWSER, url], capture_output=True, check=True).stdout
            raw.store(
                SOURCE,
                url,
                body,
                suffix=".xlsx",
                retrieved_at=datetime.now(UTC),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def parse() -> None:
    import fastexcel

    rows = []
    for name in FILES:
        path = raw.cached(SOURCE, BASE + name.replace(" ", "%20")).path
        for sheet in fastexcel.read_excel(path).sheet_names[1:]:
            grid = pl.read_excel(path, sheet_name=sheet, has_header=False, infer_schema_length=0).rows()
            title = next(str(row[0]) for row in grid if row[0] and " - " in str(row[0]))
            found = re.match(r"(UNITED STATES SENATOR|REPRESENTATIVE IN CONGRESS DISTRICT (\d+)) - (\w+)", title)
            head = next(i for i, row in enumerate(grid) if row[0] == "County")
            names = grid[head + 1]
            if found is None or found[3] not in PARTIES:
                continue
            office = "senate" if found[2] is None else "house"
            for row in grid[head + 2 :]:
                county = (row[0] or "").strip()
                if county.startswith("Office Totals"):
                    break
                for i in range(3, len(row)):
                    if names[i] is None or names[i].strip() == "" or row[i] in (None, ""):
                        continue
                    if names[i].strip().upper() in ("SCATTERING", "WRITE-IN"):
                        continue
                    rows.append(
                        (
                            DAY.year,
                            f"{office}:{PARTIES[found[3]]}",
                            DAY,
                            False,
                            "WI",
                            int(found[2]) if office == "house" else None,
                            CODES[county],
                            names[i].strip().upper(),
                            1,
                            False,
                            int(row[i].replace(",", "")),
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
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
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein").agg(
        pl.col("votes").sum()
    ).with_columns(kind=pl.lit("county"), source=pl.lit("WI")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wisconsin's 2022 U.S. Senate and House primaries by county",
            polity="US-WI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.wi.gov/elections/election-results/results-all",
            license="Public record",
        )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + name.replace(" ", "%20")), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WI": source}, strict=True)
        return cs.write()
