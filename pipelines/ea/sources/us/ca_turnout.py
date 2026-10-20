import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ca_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "California Secretary of State"
FILES = {
    "general": (
        "https://elections.cdn.sos.ca.gov/sov/2025-special/sov/04-historical-voter-reg-general.pdf",
        "Historical Voter Registration and Participation in Statewide General and Special Elections 1910-2025",
        5,
    ),
    "primary": (
        "https://elections.cdn.sos.ca.gov/sov/2026-primary/sov/04-historical-voter-reg-general.pdf",
        "Historical Voter Registration and Participation in Statewide Primary Elections 1914-2026",
        6,
    ),
}
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
ROW = re.compile(r"^\s*([A-Za-z]+)\.? (\d{1,2}), (\d{4})(?: [A-Z])?\s+(.*)$")


def fetch() -> None:
    for url, _, _ in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    rows = []
    for table, (url, _, position) in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        for line in text.splitlines():
            match = ROW.match(line)
            if match is None or match.group(1)[:3].lower() not in MONTHS:
                continue
            tokens = match.group(4).split()
            if len(tokens) < position + 2:
                continue
            numbers = [int(t.replace(",", "")) if re.fullmatch(r"[\d,]+", t) else None for t in tokens]
            rows.append(
                {
                    "table": table,
                    "day": date(int(match.group(3)), MONTHS.index(match.group(1)[:3].lower()) + 1, int(match.group(2))),
                    "registered": numbers[position - 1],
                    "voters": numbers[position],
                }
            )
    frame = pl.DataFrame(rows).filter(pl.col("voters").is_not_null())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame.group_by("table").len())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's registered voters and turnout statewide for every general, special, and primary election "
            "from 1910 on, from the Secretary of State's historical tables",
            polity="US",
        )
        elections = {}
        for value, entity_id, start in conn.execute(
            """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':CA(:[a-z]+)?$' and e.kind in ('general', 'primary', 'special', 'runoff')"""
        ):
            elections.setdefault(start, []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        for table, (url, title, _) in FILES.items():
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url=url, license="Public record")
            cs.artifact(raw.cached(SOURCE, url), source)
            for day, registered, voters in frame.filter(pl.col("table") == table).select("day", "registered", "voters").iter_rows():
                found = elections.get(day, [])
                if not found:
                    found = [f for d, items in elections.items() if (d.year, d.month) == (day.year, day.month) for f in items]
                found = [f for f in found if (table == "primary") == f[0].endswith(":primary")]
                if len(found) != 1:
                    print(f"{day} {table}: {len(found)} elections")
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
                    if value is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=cs.unit("us.state", "06"),
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
