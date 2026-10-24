import csv
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid5

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.va_pri_turnout"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Virginia Department of Elections"
BASE = "https://apps.elections.virginia.gov/SBE_CSV/ELECTIONS/ELECTIONTURNOUT/Turnout-"
FILES = [
    "2007%20June%20Democratic%20Primary",
    "2007%20June%20Republican%20Primary",
    "2008%20February%20Democratic%20Presidential%20Primary",
    "2008%20February%20Republican%20Presidential%20Primary",
    "2008%20June%20Democratic%20Primary",
    "2008%20June%20Republican%20Primary",
    "2009%20June%20Democratic%20Primary",
    "2009%20June%20Republican%20Primary",
    "2010%20June%20Republican%20Primary",
    "2010%20March%20Democratic%20Primary",
    "2011%20August%20Democratic%20Primary",
    "2011%20August%20Republican%20Primary",
    "2012%20June%20Democratic%20Primary",
    "2012%20June%20Republican%20Primary",
    "2012%20March%20Republican%20Presidential%20Primary",
    "2013%20June%20Democratic%20Primary",
    "2013%20June%20Republican%20Primary",
    "2014%20June%20Democratic%20Primary",
    "2014%20June%20Republican%20Primary",
    "2015%20June%20Democratic%20Primary",
    "2015%20June%20Republican%20Primary",
    "2016%20June%20Democratic%20Primary",
    "2016%20June%20Republican%20Primary",
    "2016%20March%20Democratic%20Presidential%20Primary",
    "2016%20March%20Republican%20Presidential%20Primary",
    "2017%20June%20Democratic%20Primary",
    "2017%20June%20Republican%20Primary",
    "2018%20June%20Democratic%20Primary%20",
    "2018%20June%20Republican%20Primary%20",
    "2019%20June%20Democratic%20Primary",
    "2019%20June%20Republican%20Primary",
    "2020%20June%20Democratic%20Primary",
    "2020%20June%20Republican%20Primary",
    "2020%20March%20Democratic%20Presidential%20Primary",
    "2021%20June%20Democratic%20Primary",
    "2021%20June%20Republican%20Primary",
    "2022%20June%20Democratic%20Primary",
    "2022%20June%20Republican%20Primary",
]
STATISTICS = "https://www.elections.virginia.gov/media/registration-statistics/"
SNAPSHOTS = {
    "2007-06-12": "2007/05/Statewide_Statistics_by_Locality_05.pdf",
    "2008-02-12": "2008/02/Statewide_Statistics_by_Locality.pdf",
    "2008-06-10": "2008/06/Statewide_Statistics_by_Locality.pdf",
    "2009-06-09": "2009/06/Statewide_Statistics_by_Locality.pdf",
    "2010-06-08": "2010/05/Statewide_Statistics_by_Locality.pdf",
    "2012-03-06": "2012/02/Registrant_Counts_By_Locality.pdf",
    "2012-06-12": "2012/05/Registrant_Counts_By_Locality.pdf",
    "2013-06-11": "2013/05/LocalityStatistics_2013_06_01_054452.pdf",
}
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
        raw.store(
            SOURCE, url(name), response.content, suffix=".csv", retrieved_at=datetime.now(UTC), media_type="text/csv"
        )
    for path in SNAPSHOTS.values():
        raw.fetch(SOURCE, STATISTICS + path, suffix=".pdf")


def snapshot(path: str) -> tuple[date, dict[str, int]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, STATISTICS + path).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    month, day, year = re.search(r"as of (\d\d?)/(\d\d?)/(\d{4})", text).groups()
    totals: dict[str, int] = {}
    locality = None
    for line in text.splitlines():
        if found := re.search(r"Locality: (\d{3})\b", line):
            locality = f"51{found[1]}"
        elif locality is not None and ("Inactivate/Month" in line or "# of Voters:" in line and "Total #" not in line):
            numbers = [int(value.replace(",", "")) for value in re.findall(r"\d[\d,]*", line.split("Voters:")[-1])]
            totals[locality] = numbers[-1] if "Inactivate/Month" in line else numbers[2]
            locality = None
    return date(int(year), int(month), int(day)), totals


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
    totals = (
        pl.DataFrame(
            [
                {
                    "date": day,
                    "code": codes[key(re.sub(r" (city|county)$", "", locality), locality.endswith(" city"))],
                    "registered": value,
                }
                for (day, locality, _), value in registered.items()
                if key(re.sub(r" (city|county)$", "", locality), locality.endswith(" city")) in codes
            ]
        )
        .group_by("date", "code")
        .agg(pl.col("registered").sum())
    )
    frame = frame.join(totals, on=["date", "code"], how="left").with_columns(as_of=pl.lit(None, pl.Date))
    for day, path in SNAPSHOTS.items():
        as_of, counts = snapshot(path)
        print(f"{day}: {len(counts)} localities as of {as_of}, {sum(counts.values())} registered")
        filled = (pl.col("date") == day) & pl.col("registered").is_null()
        lookup = pl.col("code").replace_strict(counts, default=None, return_dtype=pl.Int64)
        frame = frame.with_columns(
            as_of=pl.when(filled & lookup.is_not_null()).then(pl.lit(as_of)).otherwise("as_of"),
            registered=pl.when(filled).then(lookup).otherwise("registered"),
        )
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
            summary="Virginia's registered voters and ballots cast by locality for the primaries of 2007 to 2022, "
            "from the Department of Elections' election turnout files, with registered voters for the 2007-2013 primaries "
            "from the nearest monthly registration statistics report",
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
                """select r.election_id from result_set r join changeset c on c.id = r.changeset_id
                   where r.contest_id is null and r.is_default and r.status <> 'withdrawn' and c.pipeline <> %s""",
                [SOURCE],
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
        title = "Voter Registration Statistics"
        statistics = cs.source(PUBLISHER, title)
        if statistics in cs.minted:
            cs.add(
                "source",
                id=statistics,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://www.elections.virginia.gov/resultsreports/registration-statistics/",
                license="Public record",
            )
        for path in SNAPSHOTS.values():
            cs.artifact(raw.cached(SOURCE, STATISTICS + path), statistics)
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
                completeness=round(rows.height / (134 if day < "2013-07-01" else 133), 4),
            )
            as_of = rows["as_of"].drop_nulls().first()
            if as_of is not None:
                offset = (as_of - date.fromisoformat(day)).days
                cs.add(
                    "note",
                    id=uuid5(result_set, "registered"),
                    entity_id=result_set,
                    kind="methodology",
                    body_md=f"Registered voters is the Department of Elections' count of all registrants, active and inactive, as of "
                    f"{as_of:%B} {as_of.day}, {as_of.year}, {abs(offset)} day{'s' if abs(offset) != 1 else ''} "
                    f"{'after' if offset > 0 else 'before'} the primary, from its monthly registration statistics.",
                )
            for code, registered, voters, snapped in rows.select("code", "registered", "voters", "as_of").iter_rows():
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
                        flags=2 if measure == 2 and snapped is not None else 0,
                        value=value,
                    )
        return cs.write()
