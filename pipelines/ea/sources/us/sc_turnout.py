import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.sc_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "South Carolina Election Commission"
URL = "https://www.scvotes.gov/wp-content/uploads/2026/10/Voter-Participation-History-1998-2026.pdf"
TITLE = "Voter Participation History 1998-2026"
ROW = re.compile(r"^\s*(?:((?:19|20)\d\d)\s+)?(State Primaries|Presidential Primaries|General Election)\*?\s+(\d.*)$")
YEAR = re.compile(r"^\s*((?:19|20)\d\d)\s*$")
STAGES = {"State Primaries": "state", "Presidential Primaries": "presidential", "General Election": "general"}


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
        return
    except LookupError:
        pass
    response = httpx.get(URL, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=60)
    response.raise_for_status()
    raw.store(
        SOURCE, URL, response.content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
    )


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows = []
    pending = []
    year = 0
    for line in text.splitlines():
        match = YEAR.match(line)
        if match is not None:
            year = int(match[1])
            for row in pending:
                rows.append({**row, "year": year})
            pending = []
            continue
        match = ROW.match(line)
        if match is None:
            continue
        numbers = [int(t.replace(",", "")) for t in match[3].split() if re.fullmatch(r"\d[\d,]*", t)]
        row = {"stage": STAGES[match[2]], "registered": numbers[0], "voters": numbers[-1]}
        if match[1] is not None:
            year = int(match[1])
            for earlier in pending:
                rows.append({**earlier, "year": year})
            pending = []
        if row["stage"] == "general":
            pending.append(row)
        else:
            rows.append({**row, "year": year})
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame.group_by("stage").len())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Carolina's registered voters and ballots cast statewide for its state primaries, presidential primaries, "
            "and generals 1998–2026, from the Election Commission's voter participation history",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':SC(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            if kind == "general" and start.month == 11:
                elections.setdefault((start.year, "general"), []).append((value, entity_id))
            elif kind == "primary" and start.month >= 5:
                elections.setdefault((start.year, "state"), []).append((value, entity_id))
            elif kind == "primary":
                elections.setdefault((start.year, "presidential"), []).append((value, entity_id))
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
                url=URL,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        for year, stage, registered, voters in frame.select("year", "stage", "registered", "voters").iter_rows():
            found = elections.get((year, stage), [])
            if len(found) != 1:
                print(f"{year} {stage}: {len(found)} elections")
                continue
            value_key, election_id = found[0]
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
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.state", "45"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
