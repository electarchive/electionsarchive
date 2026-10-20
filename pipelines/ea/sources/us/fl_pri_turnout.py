import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.fl_pri_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Florida Office of Economic and Demographic Research"
URL = "https://www.edr.state.fl.us/Content/constitutional-amendments/2020Ballot/PrimaryVoterRegistrationTurnout.pdf"
TITLE = "Florida Primary Voter Registration and Turnout"
DAYS = (date(2016, 8, 30), date(2018, 8, 28))
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '12%'
"""
CELL = re.compile(r"([A-Za-z.\-' ]+?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+[\d.]+%")


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) county$", "", name).lower())


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows = []
    for line in text.splitlines():
        for column, match in enumerate(CELL.finditer(line)):
            key = plain(match[1])
            if key not in codes or column > 1:
                continue
            registered, voters = (int(match[i].replace(",", "")) for i in (2, 3))
            if voters > registered:
                print(f"{match[1]}: {registered} {voters}")
                continue
            rows.append({"date": DAYS[column], "code": codes[key], "registered": registered, "voters": voters})
    frame = pl.DataFrame(rows).unique(["date", "code"])
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("date").agg(pl.len(), pl.col("registered").sum(), pl.col("voters").sum()))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Florida's registered voters and ballots cast by county for the primaries of 2016 and 2018, from the Office of "
            "Economic and Demographic Research's voter registration and turnout table",
            polity="US",
        )
        elections = {
            start: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':FL(:[a-z]+)?$' and e.kind = 'primary'"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.edr.state.fl.us/Content/constitutional-amendments/index.cfm",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        for (day,), rows in frame.group_by("date", maintain_order=True):
            found = elections.get(day)
            if found is None:
                print(f"{day}: no election")
                continue
            value_key, election_id = found
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
                completeness=round(rows.height / 67, 4),
            )
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
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
