import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.mi_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Michigan Department of State, Bureau of Elections"
BASE = "https://www.michigan.gov/sos/-/media/Project/Websites/sos/Election-Results-and-Statistics/"
URLS = {
    "general": BASE + "General-Voter-Reg-Turnout-Stats.pdf",
    "primary": BASE + "Registered-Voter-Count/Primary-Voter-Registration-Turnout-Statistics.pdf",
}
URL = URLS["general"]
TITLE = "Voter Registration / Turnout Statistics"
ROW = re.compile(r"^\s*((?:19|20)\d\d)\s+(\d[\d,]*,\d{3})(?:\s+(\d{1,3}(?:,\d{3})+))?")


def fetch() -> None:
    for url in URLS.values():
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=60)
        response.raise_for_status()
        raw.store(
            SOURCE, url, response.content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
        )


def parse() -> None:
    rows = []
    for stage, url in URLS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        for line in text.splitlines():
            match = ROW.match(line)
            if match is None:
                continue
            registered = match[3]
            rows.append(
                {
                    "year": int(match[1]),
                    "stage": stage,
                    "registered": int(registered.replace(",", "")) if registered else None,
                    "voters": int(match[2].replace(",", "")),
                }
            )
    frame = pl.DataFrame(rows, schema_overrides={"registered": pl.Int64})
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
            summary="Michigan's registered voters and turnout statewide for its primary and general elections 1948–2024, "
            "from the Secretary of State's voter registration and turnout statistics",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':MI(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
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
        for url in URLS.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        for year, stage, registered, voters in frame.select("year", "stage", "registered", "voters").iter_rows():
            found = elections.get((year, stage), [])
            if len(found) > 1 and stage == "general":
                first = date(year, 11, 1)
                day = date(year, 11, 2 + (7 - first.weekday()) % 7)
                found = [f for f in found if f[0].startswith(day.isoformat())]
            if len(found) > 1 and stage == "primary":
                found = [max(found)]
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
                    unit_id=cs.unit("us.state", "26"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
