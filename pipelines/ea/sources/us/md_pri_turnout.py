import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.md_pri_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Maryland State Board of Elections"
BASE = "https://elections.maryland.gov/press_room/"
ELECTIONS = {
    date(2022, 7, 19): (
        "2022_stats/Official_CountyAndCanvass.pdf",
        "2022_stats/Eligible%20Active%20Voters%20by%20County%20-%20GP22.pdf",
    ),
    date(2024, 5, 14): (
        "2024_stats/Official_CountyAndCanvass.pdf",
        "2024_stats/Eligible%20Active%20Voters%20by%20County%20-%20PP24.pdf",
    ),
}
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '24%'
"""


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i)( county| co\.)$", "", name.strip()).lower().replace("saint", "st"))


def fetch() -> None:
    for paths in ELECTIONS.values():
        for path in paths:
            try:
                raw.cached(SOURCE, BASE + path)
                continue
            except LookupError:
                pass
            response = httpx.get(BASE + path, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=120)
            response.raise_for_status()
            raw.store(
                SOURCE,
                BASE + path,
                response.content,
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def table(path: str, codes: dict[str, str]) -> dict[str, int]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    out = {}
    for line in text.split("\n"):
        match = re.match(r"^([A-Za-z.' ]+?)\s{2,}(.*\d)\s*$", line)
        if match and plain(match[1]) in codes:
            out[codes[plain(match[1])]] = int(re.findall(r"[\d,]+", match[2].replace("%", " "))[-1].replace(",", ""))
    return out


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    codes["baltimore"] = "24005"
    codes["baltimorecity"] = "24510"
    rows = []
    for day, (voted, eligible) in ELECTIONS.items():
        ballots = table(voted, codes)
        registered = table(eligible, codes)
        if len(ballots) != 24 or len(registered) != 24:
            raise ValueError(f"{day}: {len(ballots)} ballots, {len(registered)} registered")
        rows += [{"date": day, "code": c, "registered": registered[c], "voters": ballots[c]} for c in ballots]
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("date").agg(pl.col("registered").sum(), pl.col("voters").sum()))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's active registered voters and ballots cast by county for the primaries of 2022 and 2024, "
            "from the State Board of Elections' turnout and eligible voter reports",
            polity="US",
        )
        elections = {
            start: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':MD(:[a-z]+)?$' and e.kind = 'primary'"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Voter Turnout and Eligible Active Voters"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=BASE + "index.html",
                license="Public record",
            )
        for paths in ELECTIONS.values():
            for path in paths:
                cs.artifact(raw.cached(SOURCE, BASE + path), source)
        for (day,), rows in frame.group_by("date", maintain_order=True):
            value_key, election_id = elections[day]
            result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:county")
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
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", code),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
