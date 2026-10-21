import csv
import io
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.co_stats import COUNTIES_SQL, PUBLISHER, plain
from ea.store import DATA, connect

SOURCE = "us.co_pres_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://historicalelectiondata.coloradosos.gov/api/download_contest/{}_table.csv?split_party=false"
CONTESTS = {1976: 11498}


def fetch() -> None:
    for contest in CONTESTS.values():
        raw.fetch(SOURCE, URL.format(contest), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    rows = []
    for year, contest in CONTESTS.items():
        table = list(csv.reader(io.StringIO(raw.cached(SOURCE, URL.format(contest)).path.read_text(encoding="utf8"))))
        header = table[0]
        registered_at = header.index("Registered Voters")
        ballots_at = header.index("Total Ballots Cast")
        counties = []
        for line in table:
            if line[0] == "State":
                state = (int(line[registered_at]), int(line[ballots_at]))
            elif line[0] == "County":
                counties.append((codes[plain(line[1])], int(line[registered_at]), int(line[ballots_at])))
        registered = sum(c[1] for c in counties)
        ballots = sum(c[2] for c in counties)
        assert (registered, ballots) == state, (year, registered, ballots, state)
        rows.extend({"year": year, "contest": contest, "code": code, "registered": r, "ballots": b} for code, r, b in counties)
        print(year, len(counties), state)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Colorado's registered voters and ballots cast (votes plus blank votes in the presidential contest) by county for the "
            "1976 general election, from the Secretary of State's historical elections database; each column equals the printed state row",
            polity="US",
        )
        elections = {
            start.year: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':CO$' and e.kind = 'general' and extract(month from e.date_from) = 11"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Historical Elections Database"
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
        for (year,), rows in frame.group_by("year", maintain_order=True):
            if year not in elections:
                print(f"{year}: no election")
                continue
            value_key, election_id = elections[year]
            cs.artifact(raw.cached(SOURCE, URL.format(rows["contest"][0])), source)
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
                completeness=round(rows.height / 63, 4),
            )
            for code, registered, ballots in rows.select("code", "registered", "ballots").iter_rows():
                for measure, value in ((2, registered), (3, ballots)):
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
