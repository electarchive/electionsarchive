import csv
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.va_pri_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Virginia Department of Elections"
BASE = "https://apps.elections.virginia.gov/SBE_CSV/ELECTIONS/ELECTIONTURNOUT/Turnout-"
FILES = [
    "2008%20February%20Democratic%20Presidential%20Primary",
    "2008%20February%20Republican%20Presidential%20Primary",
    "2012%20March%20Republican%20Presidential%20Primary",
    "2016%20March%20Democratic%20Presidential%20Primary",
    "2016%20March%20Republican%20Presidential%20Primary",
    "2020%20March%20Democratic%20Presidential%20Primary",
]
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '51%'
"""


def url(name: str) -> str:
    return f"{BASE}{name}.csv"


def key(name: str, city: bool) -> tuple[str, bool]:
    return re.sub(r"[^a-z]", "", name.lower().replace("&", "and")), city


def fetch() -> None:
    for name in FILES:
        try:
            raw.cached(SOURCE, url(name))
            continue
        except LookupError:
            pass
        response = httpx.get(url(name), headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=120)
        response.raise_for_status()
        raw.store(SOURCE, url(name), response.content, suffix=".csv", retrieved_at=datetime.now(UTC), media_type="text/csv")


def parse() -> None:
    with connect() as conn:
        codes = {key(name, int(code) >= 51500): code for code, name in conn.execute(COUNTIES_SQL)}
    votes: dict[tuple[str, str, str, str], int] = {}
    registered: dict[tuple[str, str, str], int] = {}
    for name in FILES:
        with raw.cached(SOURCE, url(name)).path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                day, locality = row["election_date"][:10], row["locality"].strip().lower()
                at = (name, day, locality, row["precinct"])
                votes[at] = max(votes.get(at, 0), int(row["TotalVoteTurnout"] or 0))
                if row.get("TotalRegisteredVoters"):
                    shared = (day, locality, row["precinct"])
                    registered[shared] = max(registered.get(shared, 0), int(row["TotalRegisteredVoters"]))
    rows = []
    for (name, day, locality, precinct), voters in votes.items():
        code = codes.get(key(re.sub(r" (city|county)$", "", locality), locality.endswith(" city")))
        if code is None:
            print(f"{name}: {locality}")
            continue
        rows.append({"date": day, "code": code, "precinct": precinct, "voters": voters})
    frame = pl.DataFrame(rows).group_by("date", "code").agg(pl.col("voters").sum())
    totals = pl.DataFrame(
        [
            {"date": day, "code": codes[key(re.sub(r" (city|county)$", "", locality), locality.endswith(" city"))], "registered": value}
            for (day, locality, _), value in registered.items()
            if key(re.sub(r" (city|county)$", "", locality), locality.endswith(" city")) in codes
        ]
    ).group_by("date", "code").agg(pl.col("registered").sum())
    frame = frame.join(totals, on=["date", "code"], how="left")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("date").agg(pl.len(), pl.col("registered").sum(), pl.col("voters").sum()).sort("date"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virginia's registered voters and ballots cast by locality for the presidential primaries of 2008, 2012, 2016, and 2020, "
            "from the Department of Elections' election turnout files",
            polity="US",
        )
        elections = {
            start.isoformat(): (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':VA(:[a-z]+)?$' and e.kind = 'primary'"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Election Turnout"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://apps.elections.virginia.gov/SBE_CSV/ELECTIONS/ELECTIONTURNOUT/",
                license="Public record",
            )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, url(name)), source)
        for (day,), rows in frame.group_by("date", maintain_order=True):
            if day not in elections:
                print(f"{day}: no election")
                continue
            value_key, election_id = elections[day]
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
                completeness=round(rows.height / 133, 4),
            )
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
                    if value is None or (measure == 2 and value == 0):
                        continue
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
