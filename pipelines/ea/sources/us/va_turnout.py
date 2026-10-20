import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.va_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Virginia Department of Elections"
URL = "https://www.elections.virginia.gov/resultsreports/registrationturnout-statistics/"
TITLE = "Summary of Virginia Registration & Turnout Statistics"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def parse() -> None:
    text = raw.cached(SOURCE, URL).path.read_text(encoding="utf8", errors="replace")
    text = re.sub(r"(?i)</t[dh]>|</tr>", "|", text)
    flat = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    rows = []
    for match in re.finditer(r"\b((?:19|20)\d\d)\*?\| ([\d,]+)\| [^|]*\| ([\d,]+)\|", flat):
        rows.append({"year": int(match[1]), "registered": int(match[2].replace(",", "")), "voters": int(match[3].replace(",", ""))})
    frame = pl.DataFrame(rows).unique("year").sort("year")
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
            summary="Virginia's registered voters and total voting statewide in the November generals of 1976–2025, from the "
            "Department of Elections' registration and turnout statistics",
            polity="US",
        )
        elections: dict[int, list[tuple[str, str]]] = {}
        for value, entity_id, start in conn.execute(
            """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':VA$' and e.kind = 'general'"""
        ):
            elections.setdefault(start.year, []).append((value, entity_id))
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
                first = date(year, 11, 1)
                day = date(year, 11, 2 + (7 - first.weekday()) % 7)
                found = [f for f in found if f[0].startswith(day.isoformat())]
            if len(found) != 1:
                print(f"{year}: {len(found)} elections")
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
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.state", "51"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
