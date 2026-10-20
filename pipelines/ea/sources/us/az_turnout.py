import csv
import io
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.az_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Arizona Secretary of State"
URL = "https://apps.azsos.gov/election/VoterReg/History/Total.csv"
TITLE = "Voter Registration History, State of Arizona"


def parse() -> None:
    rows = []
    reader = csv.reader(io.StringIO(raw.cached(SOURCE, URL).path.read_text()))
    for line in reader:
        if len(line) < 8 or not line[0].isdigit() or int(line[0]) < 1940:
            continue
        for stage, cell in (("primary", line[7]), ("general", line[16])):
            cell = cell.replace(",", "").strip()
            if cell.isdigit():
                rows.append({"year": int(line[0]), "stage": stage, "registered": int(cell)})
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
            summary="Arizona's registered voters at its generals 1940–2010 and state primaries 1994–2010, from the "
            "Secretary of State's voter registration history",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str, int]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':AZ(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            elections.setdefault((start.year, kind), []).append((value, entity_id, start.month))
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
        for year, stage, registered in frame.select("year", "stage", "registered").iter_rows():
            found = [f for f in elections.get((year, stage), []) if (f[2] == 11 if stage == "general" else f[2] >= 6)]
            if len(found) != 1:
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
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=cs.unit("us.state", "04"),
                option_id=None,
                channel=0,
                measure=2,
                count_round=0,
                flags=0,
                value=registered,
            )
        return cs.write()
