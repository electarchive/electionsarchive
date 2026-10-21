import html
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nh_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New Hampshire Secretary of State"
REGISTRATION = "https://web.archive.org/web/20260709183931if_/https://www.sos.nh.gov/party-registration-history-1970-2026"
BALLOTS = {2008: "https://web.archive.org/web/20090730165712if_/http://sos.nh.gov/general2008/sumballots08.htm"}
URL = "https://www.sos.nh.gov/party-registration-history-1970-2026"
TITLE = "Party Registration History 1970-2026, and Summary Ballots Cast, State General Election, November 4, 2008"


def fetch() -> None:
    raw.fetch(SOURCE, REGISTRATION, suffix=".html")
    for url in BALLOTS.values():
        raw.fetch(SOURCE, url, suffix=".html")


def cells(markup: str) -> list[str]:
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip() for cell in re.findall(r"<t[dh].*?</t[dh]>", markup, re.S)]


def parse() -> None:
    text = raw.cached(SOURCE, REGISTRATION).path.read_text(errors="ignore")
    snapshots: dict[date, int] = {}
    for row in re.findall(r"<tr.*?</tr>", text, re.S):
        found = [c for c in cells(row) if c]
        match = re.match(r"November (\d+),\s*(\d{4})$", found[0]) if found else None
        if match is None:
            continue
        total = re.match(r"[\d,]+", found[-1])
        if total is None:
            continue
        snapshots[date(int(match[2]), 11, int(match[1]))] = int(total[0].replace(",", ""))
    rows = []
    for year in range(1970, 2026, 2):
        first = date(year, 11, 1)
        day = first + timedelta(days=(7 - first.weekday()) % 7 + 1)
        near = [(abs((d - day).days), d) for d in snapshots if -3 <= (d - day).days <= 7]
        if near:
            rows.append({"year": year, "date": day, "registered": snapshots[min(near)[1]], "voters": None})
    for year, url in BALLOTS.items():
        text = raw.cached(SOURCE, url).path.read_text(errors="ignore")
        totals = [c for c in cells(text) if c]
        index = next(i for i, c in enumerate(totals) if c == "Totals")
        voters = int(totals[index + 3].replace(",", ""))
        for row in rows:
            if row["year"] == year:
                row["voters"] = voters
    frame = pl.DataFrame(rows, schema={"year": pl.Int32, "date": pl.Date, "registered": pl.Int64, "voters": pl.Int64})
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
            summary="New Hampshire's statewide registered voters at each November general election 1970-2024 (checklist total in the Secretary of State's "
            "party registration history, the snapshot dated within three days before to seven days after election day) and ballots cast in 2008",
            polity="US",
        )
        elections = {
            value: entity_id
            for value, entity_id in conn.execute(
                """select x.value, x.entity_id from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':NH(:[a-z]+)?$' and e.kind = 'general'"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL, license="Public record")
        cs.artifact(raw.cached(SOURCE, REGISTRATION), source)
        for url in BALLOTS.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        for day, registered, voters in frame.select("date", "registered", "voters").iter_rows():
            found = [(v, i) for v, i in elections.items() if v.startswith(day.isoformat())]
            if len(found) != 1:
                print(f"{day}: {len(found)} elections")
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
                    unit_id=cs.unit("us.state", "33"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
