import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ak_hist_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Alaska Division of Elections"
TITLE = "Alaska Voter Turnout Statistics"
PAGES = {
    "general": "http://web.archive.org/web/20260411110731id_/https://www.elections.alaska.gov/statistics/GENRvoterturnout.php",
    "primary": "http://web.archive.org/web/20260411110727id_/https://www.elections.alaska.gov/statistics/PRIMvoterturnout.php",
}
LIVE = "https://www.elections.alaska.gov/statistics/{}voterturnout.php"


def fetch() -> None:
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")


def parse() -> None:
    rows = []
    for stage, url in PAGES.items():
        text = raw.cached(SOURCE, url).path.read_text(errors="replace")
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.S | re.I):
            cells = [re.sub(r"<[^>]+>|\s+", " ", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)]
            if len(cells) != 4 or not re.fullmatch(r"(19|20)\d\d", cells[0]):
                continue
            rows.append(
                {
                    "year": int(cells[0]),
                    "stage": stage,
                    "registered": int(cells[1].replace(",", "")) if cells[1][:1].isdigit() else None,
                    "voters": int(cells[2].replace(",", "")),
                }
            )
    frame = pl.DataFrame(rows, schema={"year": pl.Int64, "stage": pl.String, "registered": pl.Int64, "voters": pl.Int64})
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
            summary="Alaska's registered voters and voters statewide for its primary and general elections 1958–2018, "
            "from the Division of Elections' turnout statistics pages",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':AK(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            elections.setdefault((start.year, kind), []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        sources = {}
        for stage, url in PAGES.items():
            source = cs.source(PUBLISHER, f"{TITLE}, {stage} elections")
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=f"{TITLE}, {stage} elections",
                    url=LIVE.format("GENR" if stage == "general" else "PRIM"),
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, url), source)
            sources[stage] = source
        for year, stage, registered, voters in frame.select("year", "stage", "registered", "voters").iter_rows():
            found = elections.get((year, stage), [])
            if len(found) > 1 and stage == "general":
                first = date(year, 11, 1)
                day = date(year, 11, 2 + (7 - first.weekday()) % 7)
                found = [f for f in found if f[0].startswith(day.isoformat())]
            if len(found) != 1:
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
                source_id=sources[stage],
                is_default=election_id not in held,
                completeness=1,
            )
            for measure, value in ((2, registered), (14, voters)):
                if value is None:
                    continue
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
