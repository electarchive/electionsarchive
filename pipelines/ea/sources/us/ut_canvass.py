import re
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ut_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://vote.utah.gov/wp-content/uploads/"
FILES = {
    "2023/09/2000Pri.xls": (date(2000, 6, 27), ["2000 Primary Election"]),
    "2023/09/2010-Primary-Canvass-Reports.xls": (date(2010, 6, 22), ["Primary Multi County Races"]),
    "2023/09/2012-Primary-Canvass-Reports-1.xlsx": (date(2012, 6, 26), ["Primary Multi County Races"]),
    "2023/09/2014-Primary-Canvass-Reports-1.xlsx": (date(2014, 6, 24), ["Primary Multi County Races"]),
    "2023/09/2016-Primary-Election-State-Canvass-2-1.xlsx": (date(2016, 6, 28), ["Federal & Constitution Offices"]),
    "2023/09/2018-Primary-Election-State-Canvass-1.xlsx": (date(2018, 6, 26), ["Federal Offices"]),
    "2021/02/2020-Primary-Election-State-Canvass.xlsx": (date(2020, 6, 30), ["Federal Offices"]),
    "2021/02/2020-Presidential-Primary-Election-State-Canvass.xlsx": (date(2020, 3, 3), ["Democratic", "Republican"]),
}
OFFICE = re.compile(
    r"(?i)\s*(?:(united states senate|u\.?s\.? senate)|(?:congressional district|u\.?s\.? house district)\s*(\d)|(president))\b"
)
PARTIES = {"R": "Republican", "D": "Democratic", "L": "Libertarian", "C": "Constitution"}
PUBLISHER = "Utah Lieutenant Governor's Office of Elections"
TITLE = "Official canvass by county"


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, BASE + name, suffix=Path(name).suffix)


def parse() -> None:
    with connect() as conn:
        codes = {
            name.removesuffix(", UT").removesuffix(" County").upper(): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '49%%' and u.valid @> '2000-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for name, (day, sheets) in FILES.items():
        book = fastexcel.read_excel(raw.cached(SOURCE, BASE + name).path)
        for sheet in sheets:
            cells = [
                [re.sub(r"\s+", " ", str(cell)).strip() if cell is not None else "" for cell in row]
                for row in book.load_sheet(sheet, header_row=None, dtypes="string").to_polars().iter_rows()
            ]
            first = next(index for index, row in enumerate(cells) if row[0].upper().startswith("BEAVER"))
            last = next(index for index, row in enumerate(cells) if row[0].upper().startswith("TOTAL"))
            header = cells[first - 1]
            labels: dict[int, str] = {}
            for row in cells[: first - 1]:
                for column, text in enumerate(row):
                    if text and column > 0:
                        labels.setdefault(column, "")
                        labels[column] = f"{labels[column]} {text}".strip()
            for column, candidate in enumerate(header):
                if column == 0 or not candidate or re.search(r"(?i)regist|ballots|voting|% of|percent", candidate):
                    continue
                label = labels[max((start for start in labels if start <= column), default=0)] if labels else ""
                text = f"{label} {sheet}"
                found = OFFICE.match("president") if "Presidential" in name else OFFICE.match(label)
                if found is None:
                    continue
                quoted = re.search(r'"(\w+)"', candidate)
                party = next(
                    (party for party in ("Republican", "Democratic", "Libertarian", "Constitution") if party in text),
                    PARTIES.get(quoted[1]) if quoted else None,
                )
                if party is None:
                    raise ValueError(f"{name} {sheet} {candidate}: no party in {text!r}")
                counties = {
                    codes[row[0].upper().removesuffix(" COUNTY")]: int(float(row[column]))
                    for row in cells[first:last]
                    if column < len(row) and row[column] and row[0]
                }
                printed = int(float(cells[last][column] or 0))
                if sum(counties.values()) != printed:
                    raise ValueError(
                        f"{name} {sheet} {candidate}: counties sum to {sum(counties.values())}, printed {printed}"
                    )
                office = "senate" if found[1] else "president" if found[3] else "house"
                for code, votes in counties.items():
                    rows.append(
                        (day, f"{office}:{party.lower()}", int(found[2]) if found[2] else None, code,
                         re.sub(r'\s*"\w+"', "", candidate), votes)
                    )  # fmt: skip
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("day", "chamber", "district", "candidate", maintain_order=True)
        .agg(pl.col("votes").sum(), pl.len())
        .rows()
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Utah's U.S. House, Senate, and presidential primaries by county from the Office of Elections' official canvasses",
            polity="US-UT",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://vote.utah.gov/historical-election-results/",
                license="Public record",
            )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber="chamber",
                day="day",
                special=pl.lit(False),
                state=pl.lit("UT"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("candidate").str.contains("(?i)write"),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("UT"),
            ),
            {"UT": source},
            strict=True,
        )
        return cs.write()
