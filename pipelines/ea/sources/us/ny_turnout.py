import re
import subprocess
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ny_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New York State Board of Elections"
ENROLLMENT = "https://web.archive.org/web/20120822181953if_/http://www.elections.ny.gov/NYSBOE/enrollment/county/county_nov08.pdf"
RETURNS = "https://web.archive.org/web/20120112033637if_/http://www.elections.ny.gov/NYSBOE/elections/2008/General/PresidentVicePresident08.pdf"
URL = ENROLLMENT
TITLE = "Enrollment by County, Party Affiliation and Status, Voters Registered as of November 1, 2008, and President and Vice-President Election Returns, Nov. 4, 2008"


def fetch() -> None:
    raw.fetch(SOURCE, ENROLLMENT, suffix=".pdf")
    raw.fetch(SOURCE, RETURNS, suffix=".pdf")


def parse() -> None:
    texts = [
        subprocess.run(["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True).stdout
        for url in (ENROLLMENT, RETURNS)
    ]
    registered = next(
        int(re.findall(r"[\d,]+", line)[-1].replace(",", "")) for line in texts[0].splitlines() if re.match(r"\s*Statewide Total\s+Total\s", line)
    )
    voters = [int(re.findall(r"[\d,]+", line)[-1].replace(",", "")) for line in texts[1].splitlines() if line.strip().startswith("Statewide Total")][-1]
    frame = pl.DataFrame([{"year": 2008, "stage": "general", "registered": registered, "voters": voters}])
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
            summary="New York's enrolled voters on November 1, 2008 and the total presidential vote, including blank, void, and scattering ballots, "
            "at the 2008 general election, from the State Board of Elections' enrollment table and certified returns",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':NY(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            elections.setdefault((start.year, kind), []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL, license="Public record")
        cs.artifact(raw.cached(SOURCE, ENROLLMENT), source)
        cs.artifact(raw.cached(SOURCE, RETURNS), source)
        for year, stage, registered, voters in frame.select("year", "stage", "registered", "voters").iter_rows():
            found = elections.get((year, stage), [])
            if len(found) > 1 and stage == "general":
                first = date(year, 11, 1)
                day = date(year, 11, 2 + (7 - first.weekday()) % 7)
                found = [f for f in found if f[0].startswith(day.isoformat())]
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
                if value is None:
                    continue
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.state", "36"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
