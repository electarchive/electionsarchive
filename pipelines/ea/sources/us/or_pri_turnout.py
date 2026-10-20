import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.or_pri_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Oregon Secretary of State"
URL = "https://sos.oregon.gov/elections/Documents/History/Voter-Turnout-History-Primary.pdf"
TITLE = "Voter Turnout History for Primary Elections"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    text = subprocess.run(["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True).stdout
    rows = [
        {"year": int(m[1]), "registered": int(m[2].replace(",", "")), "voters": int(m[3].replace(",", ""))}
        for m in re.finditer(r"^\s*((?:19|20)\d\d)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%", text, re.M)
    ]
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame.height, frame["year"].min(), frame["year"].max())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oregon's registered voters and ballots cast statewide for its primaries 1960–2024, from the Secretary of State's voter turnout history",
            polity="US",
        )
        elections: dict[int, list[tuple[str, str, int]]] = {}
        for value, entity_id, start in conn.execute(
            """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':OR(:[a-z]+)?$' and e.kind = 'primary'"""
        ):
            elections.setdefault(start.year, []).append((value, entity_id, start.month))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL, license="Public record")
        cs.artifact(raw.cached(SOURCE, URL), source)
        for year, registered, voters in frame.select("year", "registered", "voters").iter_rows():
            found = elections.get(year, [])
            if len(found) > 1:
                found = [f for f in found if f[2] in (5, 6)]
            if len(found) != 1:
                print(f"{year}: {len(found)} elections")
                continue
            value_key, election_id, _ = found[0]
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
                    unit_id=cs.unit("us.state", "41"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
