import io
import re
import zipfile
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.sources.us import ks_sos_1988, medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.ks_sos_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WANTED = {
    (1990, "Attorney General"),
    (1990, "State Treasurer"),
    (1998, "Secretary of State"),
    (1998, "Attorney General"),
    (1998, "State Treasurer"),
    (1998, "Insurance Commissioner"),
}
TITLES = (
    ("secretary", "Secretary of State"),
    ("attorney", "Attorney General"),
    ("state treasurer", "State Treasurer"),
    ("commissioner", "Insurance Commissioner"),
)
PARTIES = {"democrat": "DEMOCRATIC", "republican": "REPUBLICAN", "libertarian": "LIBERTARIAN"}


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '20%%'"""
            ).fetchall()
        }
    rows = []
    for year in (1990, 1998):
        with zipfile.ZipFile(
            io.BytesIO(raw.cached(ks_sos_1988.SOURCE, ks_sos_1988.url(year)).path.read_bytes())
        ) as archive:
            book = fastexcel.read_excel(archive.read(archive.namelist()[0]))
        grid = book.load_sheet_by_name("Statewide", header_row=None).to_polars().rows()
        columns = []
        for i in range(3, len(grid[0])):
            label = clean(grid[2][i]).lower()
            title = next((name for start, name in TITLES if label.startswith(start)), None)
            if title is None or (year, title) not in WANTED:
                continue
            name = "".join(
                clean(grid[r][i]) + ("" if clean(grid[r][i]).endswith(("/", "-")) else " ")
                for r in range(2)
                if clean(grid[r][i])
            ).strip()
            party = clean(grid[3][i]).lower()
            columns.append((i, title, name, PARTIES.get(party, party.upper())))
        printed: dict[tuple[str, str], int] = {}
        summed: dict[tuple[str, str], int] = {}
        for row in grid[4:]:
            text = clean(row[0])
            if text:
                key = re.sub(r"[^a-z]", "", text.lower().replace(" county", ""))
                if key not in counties:
                    raise ValueError(f"{year}: no county {text}")
                for i, title, name, party in columns:
                    value = clean(row[i]).replace(",", "")
                    if value:
                        rows.append((year, title, counties[key], name, party, int(float(value))))
                        summed[(title, name)] = summed.get((title, name), 0) + int(float(value))
            elif clean(row[2]).lower().startswith("totals"):
                for i, title, name, party in columns:
                    printed[(title, name)] = int(float(clean(row[i]).replace(",", "")))
        for key, total in printed.items():
            if summed.get(key) != total:
                raise ValueError(f"{year} {key}: counties sum to {summed.get(key)}, printed {total}")
    days = {1990: date(1990, 11, 6), 1998: date(1998, 11, 3)}
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "title": pl.String,
            "county": pl.String,
            "candidate": pl.String,
            "party": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        day=pl.col("year").replace_strict(days),
        state=pl.lit("KS"),
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        candidate=pl.col("candidate").str.to_uppercase(),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(105),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("year", "title", "candidate", "party").agg(pl.col("votes").sum()).sort("year", "title").rows())


def emit() -> Path:
    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Kansas's 1990 attorney general and treasurer and 1998 secretary of state, attorney general, treasurer, "
        "and insurance commissioner by county from the Secretary of State's workbooks",
        cite=lambda cs: {"KS": cs.source(ks_sos_1988.PUBLISHER, ks_sos_1988.TITLE)},
        method=(
            "County totals come from the Secretary of State's county workbook for the general election, and every "
            "candidate's 105 county values add up to the Totals row printed under the column."
        ),
    )
