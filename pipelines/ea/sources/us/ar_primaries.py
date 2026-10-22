import re
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ar_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.arkansas.gov/uploads/elections/"
FILES = {
    "0710_2k_primary.xls": date(2000, 5, 23),
    "0710_2k_primaryrunoff.xls": date(2000, 6, 13),
}
PARTIES = {"D": "democratic", "R": "republican"}
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
TITLES = re.compile(
    r"^(?:Congressman|Congresswoman|State Senator|Senator|Representative|Judge|Governor|Vice President|Mayor)\s+"
)
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Official primary election results by county and precinct"


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, BASE + name, suffix=Path(name).suffix)


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"\W", "", name.removesuffix(", AR").removesuffix(" County")).lower().replace("saint", "st"): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '05%%' and u.valid @> '2000-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for name, day in FILES.items():
        book = fastexcel.read_excel(raw.cached(SOURCE, BASE + name).path)
        for sheet in book.sheet_names:
            if sheet.startswith("Summary"):
                continue
            county = codes[sheet[:-1].lower().replace("saint", "st")]
            party = PARTIES[sheet[-1]]
            cells = list(book.load_sheet(sheet, header_row=None, dtypes="string").to_polars().iter_rows())
            total = max(index for index, text in enumerate(cells[0]) if text and re.match(r"(?i)totals", text))
            subtotals = {
                index for index, text in enumerate(cells[0]) if text and re.match(r"(?i)(sub)?totals?\s*$", text)
            }
            district = None
            for row in cells[1:]:
                label = (row[0] or "").strip()
                values = [
                    text.strip()
                    for index, text in enumerate(row[:total])
                    if index >= 2 and index not in subtotals and text and text.strip()
                ]
                if not label:
                    district = None
                    continue
                if not values and (row[total] or "").strip() == "":
                    found = re.search(r"(?i)congress", label) and (
                        re.search(r"\b0?(\d)\b", label)
                        or re.search(r"(?i)\b(first|second|third|fourth|1st|2nd|3rd|4th)\b", label)
                    )
                    district = (int(found[1]) if found[1].isdigit() else ORDINALS[found[1].lower()]) if found else None
                    continue
                if district is None:
                    continue
                counted = [int(float(text)) for text in values if re.fullmatch(r"\d+(\.0)?", text)]
                printed = int(float(row[total]))
                if any(counted) and sum(counted) != printed:
                    raise ValueError(f"{name} {sheet} {label}: precincts sum to {sum(counted)}, printed {printed}")
                rows.append(
                    (day, party, district, county, TITLES.sub("", label.title() if label.isupper() else label), printed)
                )
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "party": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    if frame.select("day", "district", "code", "candidate").is_duplicated().any():
        raise ValueError("a county appears twice for a candidate")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("day", "party", "district", "candidate", maintain_order=True)
        .agg(pl.col("votes").sum(), pl.len())
        .sort("day", "district")
        .rows()
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas's U.S. House primaries and runoffs by county from the Secretary of State's official primary results",
            polity="US-AR",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.sos.arkansas.gov/elections/research/election-results",
                license="Public record",
            )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber=pl.lit("house:") + pl.col("party"),
                day="day",
                special=pl.lit(False),
                state=pl.lit("AR"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("AR"),
            ),
            {"AR": source},
            strict=True,
        )
        return cs.write()
