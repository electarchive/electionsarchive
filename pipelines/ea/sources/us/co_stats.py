import csv
import io
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.co_stats"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Colorado Secretary of State"
URL = "https://historicalelectiondata.coloradosos.gov/api/download_contest/{}_table.csv?split_party=false"
STATS = {
    15045: (date(2005, 11, 1), None, "Total Ballots Cast"),
    15039: (date(2011, 11, 1), "Total Voters", None),
    15036: (date(2013, 11, 5), "Total Voters", "Total Ballots Cast"),
    15033: (date(2015, 11, 3), "Registered Voters", "Total Ballots Cast"),
    15041: (date(2010, 8, 10), "Active Voters", "Total Ballots Cast"),
    15038: (date(2012, 6, 26), "Registered Voters", None),
    15035: (date(2014, 6, 24), "Total Voters", "Total Ballots Cast"),
    15031: (date(2016, 6, 28), "Total Voters", None),
    24903: (date(2018, 6, 26), "Registered Voters", "Total Ballots Cast"),
    25287: (date(2020, 3, 3), "Registered Voters", "Total Ballots Cast"),
    25273: (date(2020, 6, 30), "Registered Voters", "Total Ballots Cast"),
    25570: (date(2022, 6, 28), "Active Voters", "Total Ballots Cast"),
    25995: (date(2024, 3, 5), "Active Voters", "Total Ballots Cast"),
    25996: (date(2024, 6, 25), "Registered Voters", "Total Ballots Cast"),
}
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '08%'
"""


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) county$", "", name).lower())


def fetch() -> None:
    for stat in STATS:
        raw.fetch(SOURCE, URL.format(stat), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    rows = []
    for stat, (day, registered_column, ballots_column) in STATS.items():
        table = list(csv.reader(io.StringIO(raw.cached(SOURCE, URL.format(stat)).path.read_text(encoding="utf8"))))
        header = table[0]
        found = 0
        for line in table:
            if line[0] != "County" or plain(line[1]) not in codes:
                continue
            values = {header[i]: int(line[i]) for i in range(2, len(header)) if header[i] and line[i].isdigit()}
            registered = values.get(registered_column) if registered_column else None
            voters = values.get(ballots_column) if ballots_column else None
            if voters is not None and registered is not None and voters >= registered:
                voters = None
            rows.append(
                {"date": day, "stat": stat, "code": codes[plain(line[1])], "registered": registered, "voters": voters}
            )
            found += 1
        print(day, found)
    frame = pl.DataFrame(rows, schema_overrides={"registered": pl.Int64, "voters": pl.Int64})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("date").agg(pl.col("registered").sum(), pl.col("voters").sum()).sort("date"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Colorado's registered (or active) voters and ballots cast by county for the odd-year coordinated elections of 2005, 2011, 2013, "
            "and 2015 and the primaries of 2010 to 2024 (2016 registered only), from the Secretary of State's voting statistics",
            polity="US",
        )
        elections = {
            start: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':CO(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                """select r.election_id from result_set r join changeset c on c.id = r.changeset_id
                   where r.contest_id is null and r.is_default and r.status <> 'withdrawn' and c.pipeline <> %s""",
                [SOURCE],
            )
        }
        title = "Voting Statistics"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://historicalelectiondata.coloradosos.gov",
                license="Public record",
            )
        for (day,), rows in frame.group_by("date", maintain_order=True):
            found = elections.get(day)
            if found is None:
                print(f"{day}: no election")
                continue
            value_key, election_id = found
            cs.artifact(raw.cached(SOURCE, URL.format(rows["stat"][0])), source)
            result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=round(rows.height / 64, 4),
            )
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
                    if value is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=cs.unit("us.county", code),
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
