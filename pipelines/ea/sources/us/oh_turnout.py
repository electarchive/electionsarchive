import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.oh_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Ohio Secretary of State"
URL = "https://web.archive.org/web/20200807183608if_/https://www.ohiosos.gov/elections/election-results-and-data/historical-election-comparisons/voter-turnout-in-presidential-election-years/"
TITLE = "Voter Turnout in Presidential Election Years"
ROW = re.compile(r"November \d+, (\d{4})\*?\|(?:([\d,]+)\|)?([\d,]+)\|(?:[\d.]+%\|)?")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def parse() -> None:
    text = re.sub(r"<[^>]+>", "|", raw.cached(SOURCE, URL).path.read_text())
    text = re.sub(r"(\s*\|\s*)+", "|", html.unescape(text))
    rows = [
        {
            "year": int(m[1]),
            "stage": "general",
            "registered": None if m[2] is None else int(m[2].replace(",", "")),
            "voters": int(m[3].replace(",", "")),
        }
        for m in ROW.finditer(text)
    ]
    frame = pl.DataFrame(rows, schema_overrides={"registered": pl.Int64}).unique()
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame.sort("year"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's registered electors (from 1980) and electors voting statewide for the presidential generals of 1948–2016, "
            "from the Secretary of State's turnout table",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':OH(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
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
                    unit_id=cs.unit("us.state", "39"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
