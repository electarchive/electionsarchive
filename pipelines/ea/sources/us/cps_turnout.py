import io
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.cps_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "U.S. Census Bureau"
BASE = "https://www2.census.gov/programs-surveys/cps/tables/time-series/voting-historical-time-series/"
TABLES = {
    "a5a": (
        25,
        "A-5a. Reported Voting for Total and Citizen Voting-Age Populations, by State for Presidential Elections",
    ),
    "a5b": (
        24,
        "A-5b. Reported Registration for Total and Citizen Voting-Age Populations, by State for Presidential Elections",
    ),
    "hst_vote03a": (
        25,
        "A-3a. Reported Voting for Total and Citizen Voting-Age Populations, by State for Congressional Elections",
    ),
    "hst_vote03b": (
        24,
        "A-3b. Reported Registration for Total and Citizen Voting-Age Populations, by State for Congressional Elections",
    ),
}
STATES_SQL = """
select substr(p.iso_code, 4), ac.code, n.text
from polity p
join polity_territory pt on pt.polity_id = p.id
join area a on a.id = pt.area_id and a.scheme_code = 'us.state'
join area_code ac on ac.area_id = a.id
join name n on n.entity_id = p.id and n.is_preferred
where p.iso_code like 'US-%'
"""


def url(table: str) -> str:
    return f"{BASE}{table}.xlsx"


def election_day(year: int) -> date:
    first = date(year, 11, 1)
    monday = first + timedelta(days=(0 - first.weekday()) % 7)
    return monday + timedelta(days=1)


def fetch() -> None:
    for table in TABLES:
        raw.fetch(SOURCE, url(table), suffix=".xlsx")


def parse() -> None:
    with connect() as conn:
        states = {name: (st, fips) for st, fips, name in conn.execute(STATES_SQL)}
    rows = []
    for table, (measure, _) in TABLES.items():
        sheet = pl.read_excel(
            io.BytesIO(raw.cached(SOURCE, url(table)).path.read_bytes()), has_header=False, infer_schema_length=0
        )
        header, kinds = sheet.row(3), sheet.row(4)
        years: dict[int, int] = {}
        year = None
        for index, (label, kind) in enumerate(zip(header, kinds, strict=True)):
            if label and index:
                year = int(label)
            if kind == "Total" and year is not None:
                years[index] = year
        for row in sheet.iter_rows():
            if row[0] not in states:
                continue
            st, fips = states[row[0]]
            for index, year in years.items():
                try:
                    value = float(row[index])
                except TypeError, ValueError:
                    continue
                rows.append(
                    {"table": table, "measure": measure, "year": year, "state": st, "fips": fips, "rate": value}
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).sort("measure", "year", "state")
    frame.write_parquet(PARSED / "states.parquet")
    print(frame.group_by("table").agg(pl.col("year").min().alias("from"), pl.col("year").max().alias("to"), pl.len()))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "states.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Reported voting and registration rates by state for November 1972 through 2024, "
            "survey estimates from the Census Bureau's Current Population Survey",
            polity="US",
        )
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        sources = {}
        for table, (_, name) in TABLES.items():
            title = f"Current Population Survey, November Voting and Registration Supplement, Table {name} (survey estimates)"
            sources[table] = cs.source(PUBLISHER, title)
            if sources[table] in cs.minted:
                cs.add(
                    "source",
                    id=sources[table],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url="https://www.census.gov/topics/public-sector/voting/data/tables.html",
                    license="Public domain",
                )
            cs.artifact(raw.cached(SOURCE, url(table)), sources[table])
        for (table, year), rows in frame.group_by("table", "year", maintain_order=True):
            key = f"{election_day(year)}"
            measure = TABLES[table][0]
            for st, fips, rate in rows.select("state", "fips", "rate").iter_rows():
                election = elections.get(f"{key}:{st}")
                if election is None:
                    continue
                result_set = cs.key(f"{SOURCE}.result_set", f"{table}:{key}:{st}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=None,
                    stage="alternative",
                    kind="counted",
                    status="active",
                    source_id=sources[table],
                    is_default=False,
                    completeness=1,
                )
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.state", fips),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=rate,
                )
        return cs.write()
