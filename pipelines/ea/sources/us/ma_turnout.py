import html
import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ma_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Massachusetts Secretary of the Commonwealth"
URL = "http://web.archive.org/web/20260816085707id_/https://www.sec.state.ma.us/divisions/elections/research-and-statistics/voter-turnout-statistics.htm"
LIVE = "https://www.sec.state.ma.us/divisions/elections/research-and-statistics/voter-turnout-statistics.htm"
TITLE = "Voter Turnout Statistics"
SECTIONS = (
    ("State Elections State Election Year", "general"),
    ("State Primaries State Primary Year", "primary"),
    ("Presidential Primaries Presidential Primary Year", "presidential"),
)
ROW = re.compile(r"((?:19|20)\d\d) ([\d,]+) ([\d,]+) [\d.]+%")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def parse() -> None:
    text = raw.cached(SOURCE, URL).path.read_text(errors="replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.DOTALL)
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))
    rows = []
    cuts = [text.index(header) for header, _ in SECTIONS]
    cuts.append(len(text))
    for (_, stage), lo, hi in zip(SECTIONS, cuts, cuts[1:]):
        for year, registered, voters in ROW.findall(text[lo:hi]):
            rows.append(
                {
                    "year": int(year),
                    "stage": stage,
                    "registered": int(registered.replace(",", "")),
                    "voters": int(voters.replace(",", "")),
                }
            )
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
            summary="Massachusetts's registered voters and total votes cast statewide for its generals, state primaries, "
            "and presidential primaries 1948–2024, from the Secretary of the Commonwealth's voter turnout statistics",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':MA(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
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
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LIVE,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        for year, stage, registered, voters in frame.select("year", "stage", "registered", "voters").iter_rows():
            found = elections.get((year, "general" if stage == "general" else "primary"), [])
            if stage == "general":
                found = [f for f in found if f[2] == 11]
            elif stage == "primary":
                found = [f for f in found if f[2] >= 6]
            else:
                found = [f for f in found if f[2] < 6]
            if len(found) != 1:
                print(f"{year} {stage}: {len(found)} elections")
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
                    unit_id=cs.unit("us.state", "25"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
