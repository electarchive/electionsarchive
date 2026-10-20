import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ak_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Alaska Division of Elections"
ELECTIONS = {(2022, "primary"): "22PRIM", (2022, "general"): "22GENR", (2024, "primary"): "24PRIM", (2024, "general"): "24GENR"}
TOTAL = re.compile(r"^\s*State - Total\s+([\d,]+)(?:\s+[\d,]+)?\s+([\d,]+)\s+[\d.]+%", re.M)


def url(folder: str, district: int) -> str:
    return f"https://www.elections.alaska.gov/results/{folder}/HD{district}.pdf"


def fetch() -> None:
    def get(address: str) -> None:
        try:
            raw.cached(SOURCE, address)
        except LookupError:
            body = subprocess.run(["curl", "-sfL", "-A", "Mozilla/5.0", address], capture_output=True, check=True).stdout
            raw.store(SOURCE, address, body, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(get, [url(folder, d) for folder in ELECTIONS.values() for d in range(1, 41)]))


def parse() -> None:
    rows = []
    for (year, stage), folder in ELECTIONS.items():
        registered = voters = 0
        for district in range(1, 41):
            text = subprocess.run(["pdftotext", "-layout", str(raw.cached(SOURCE, url(folder, district)).path), "-"], capture_output=True, text=True, check=True).stdout
            match = TOTAL.search(text)
            if match is None:
                raise ValueError(f"{folder} HD{district}: no total")
            registered += int(match[1].replace(",", ""))
            voters += int(match[2].replace(",", ""))
        rows.append({"year": year, "stage": stage, "registered": registered, "voters": voters})
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska's registered voters and voters cast statewide for the primaries and generals of 2022 and 2024, "
            "summed from the Division of Elections' statements of votes cast for its 40 House districts",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':AK(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            if (kind == "general" and start.month == 11) or (kind == "primary" and start.month == 8):
                elections.setdefault((start.year, kind), []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Statements of Votes Cast by House District"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url="https://www.elections.alaska.gov/results/", license="Public record")
        for year, stage, registered, voters in frame.select("year", "stage", "registered", "voters").iter_rows():
            found = elections.get((year, stage), [])
            if len(found) != 1:
                print(f"{year} {stage}: {len(found)} elections")
                continue
            value_key, election_id = found[0]
            for district in range(1, 41):
                cs.artifact(raw.cached(SOURCE, url(ELECTIONS[(year, stage)], district)), source)
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
                    unit_id=cs.unit("us.state", "02"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
