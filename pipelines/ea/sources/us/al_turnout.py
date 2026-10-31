import re
import subprocess
from datetime import date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.al_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Alabama Secretary of State"
URL = "https://www.sos.alabama.gov/sites/default/files/election-data/2026-07/Comprehensive%20Voter%20Turnout%201986-2024.pdf"
TITLE = "Comprehensive Voter Turnout 1986-2024"
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
HEADER = re.compile(
    rf"^\s*(.*(?:Primary|General|Special|Run-?off).*?)[,\s-]+({MONTHS})\s*(\d{{1,2}})?,?\s+(\d{{4}})(?:\s+-\s+.*)?\s*$",
    re.IGNORECASE,
)
TURNOUT = re.compile(r"^\s*(?:(\d[\d,]*)\s+)?(?:Total Ballots(?: Cast)?\s+)?(\d[\d,]*) \(([\d.]+)\s*% turnout\)")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows = []
    event = None
    for line in text.splitlines():
        header = HEADER.match(line)
        if header is not None:
            month = datetime.strptime(header[2].title(), "%B").month
            year = int(header[4])
            if header[3] is None:
                first = date(year, month, 1)
                day = date(year, month, 2 + (7 - first.weekday()) % 7) if month == 11 else None
            else:
                day = date(year, month, int(header[3]))
            label = header[1].lower()
            kind = "runoff" if "run" in label else "general" if "general" in label else "primary"
            event = {"date": day, "kind": kind, "done": False}
            continue
        match = TURNOUT.match(line)
        if match is None or event is None or event["done"] or event["date"] is None:
            continue
        event["done"] = True
        rows.append(
            {
                "date": event["date"],
                "kind": event["kind"],
                "registered": int(match[1].replace(",", "")) if match[1] else None,
                "voters": int(match[2].replace(",", "")),
            }
        )
    frame = pl.DataFrame(rows, schema_overrides={"registered": pl.Int64})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame.group_by("kind").len())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's registered voters and turnout statewide for its primaries, runoffs, and generals 1986–2024, from the "
            "Secretary of State's comprehensive voter turnout table",
            polity="US",
        )
        elections = {
            start: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':AL(:[a-z]+)?$' and e.kind in ('general', 'primary', 'runoff')"""
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
                url="https://www.sos.alabama.gov/alabama-votes/voter/election-data",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        for day, registered, voters in frame.select("date", "registered", "voters").iter_rows():
            found = elections.get(day)
            if found is None:
                near = [d for d in elections if abs((d - day).days) <= 2]
                found = elections[near[0]] if len(near) == 1 else None
            if found is None:
                print(f"{day}: no election")
                continue
            value_key, election_id = found
            result_set = cs.key(f"{SOURCE}.result_set", value_key)
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
                completeness=1,
            )
            for measure, value in ((2, registered), (14, voters)):
                if value is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.state", "01"),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
