import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.sd_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "South Dakota Secretary of State"
URL = "https://sdsos.gov/elections-voting/election-resources/election-history/registration_turnout.aspx"
TITLE = "Voter Registration/Turnout Statistics 1988-2010"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def parse() -> None:
    text = raw.cached(SOURCE, URL).path.read_text(encoding="utf8", errors="replace")
    text = text[text.find("<table") :]
    text = re.sub(r"(?i)</t[dh]>|</tr>", "|", text)
    flat = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    rows = []
    for stage, heading in (("primary", "PRIMARY"), ("general", "GENERAL")):
        block = flat[flat.find(f"{heading} YEAR") :]
        years = [int(y) for y in re.findall(r"\b(?:19|20)\d\d\b", block[: block.find("Republican Reg")])]
        series = {}
        for label, following in (("TOTAL REGISTRATION", "VOTING AGE"), ("TURNOUT", "TURNOUT %")):
            match = re.search(label + r" ?\|(.*?)\|? ?" + following, block)
            series[label] = [c.strip() for c in match.group(1).split("|")][: len(years)]
        for i, year in enumerate(years):
            registered, voters = (int(series[k][i]) if series[k][i].isdigit() else None for k in series)
            rows.append({"year": year, "stage": stage, "registered": registered, "voters": voters})
    frame = pl.DataFrame(rows).filter(pl.col("voters").is_not_null())
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
            summary="South Dakota's registered voters and turnout statewide for its primary and general elections 1988–2024, "
            "from the Secretary of State's voter registration and turnout statistics",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':SD(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
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
                if value is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.state", "46"),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
