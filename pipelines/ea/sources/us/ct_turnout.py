import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ct_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SERIES = {
    "CT": (
        "Connecticut Secretary of the State",
        "Statewide Turnout at Connecticut State and Municipal Elections 1988 to 2003",
        "https://portal.ct.gov/-/media/sots/electionservices/electionresults/1998_electionresults/turnoutgenelec1988onhtm.htm",
    )
}
FIPS = {"CT": "09"}


def number(text: str) -> int:
    return int(text.replace(",", ""))


def fetch() -> None:
    for *_, url in SERIES.values():
        raw.fetch(SOURCE, url, suffix=".html")


def parse() -> None:
    text = raw.cached(SOURCE, SERIES["CT"][2]).path.read_text(encoding="utf8", errors="replace")
    flat = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?i)</t[dh]>", "|", text))))
    rows = []
    for year, registered, voters in re.findall(r"\b((?:19|20)\d\d) ?\| ?([\d,]{9}) ?\| ?([\d,]{7,9}) ?\|", flat):
        if int(year) % 2 == 0:
            rows.append(
                {
                    "state": "CT",
                    "year": int(year),
                    "stage": "general",
                    "registered": number(registered),
                    "voters": number(voters),
                }
            )
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
            summary="Connecticut's active registered voters and voters checked as having voted statewide in the generals of 1988–2004, "
            "from the Secretary of the State's turnout table",
            polity="US",
        )
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        for state, (publisher, title, url) in SERIES.items():
            elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
            for value, entity_id, kind, start in conn.execute(
                """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ %s and e.kind in ('general', 'primary')""",
                [f":{state}(:[a-z]+)?$"],
            ):
                elections.setdefault((start.year, kind), []).append((value, entity_id))
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, url), source)
            for year, stage, registered, voters in (
                frame.filter(pl.col("state") == state).select("year", "stage", "registered", "voters").iter_rows()
            ):
                found = elections.get((year, stage), [])
                if len(found) > 1 and stage == "general":
                    first = date(year, 11, 1)
                    day = date(year, 11, 2 + (7 - first.weekday()) % 7)
                    found = [f for f in found if f[0].startswith(day.isoformat())]
                if len(found) != 1:
                    print(f"{state} {year} {stage}: {len(found)} elections")
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
                            unit_id=cs.unit("us.state", FIPS[state]),
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
