import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.wa_pri_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://results.vote.wa.gov/results/{day:%Y%m%d}/Turnout.html"
DAYS = [date(2014, 8, 5), date(2016, 8, 2), date(2018, 8, 7), date(2020, 8, 4), date(2022, 8, 2), date(2024, 8, 6)]
ROW = re.compile(r'<td class="nowrap"><a href="[^"]*">([^<]+)</a>(?:<br />)?</td>\s*<td[^>]*>([\d,]+)</td>\s*<td[^>]*>([\d,]+)</td>')
PUBLISHER = "Washington Secretary of State"
TITLE = "Primary election turnout"


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, URL.format(day=day), suffix=".html")


def parse() -> None:
    rows: list[tuple[date, str, int, int]] = []
    for day in DAYS:
        page = raw.cached(SOURCE, URL.format(day=day)).path.read_text(encoding="utf-8", errors="replace")
        found = [(county, int(registered.replace(",", "")), int(ballots.replace(",", ""))) for county, registered, ballots in ROW.findall(page)]
        total = re.search(r"Total</[^>]+>\s*<td[^>]*>([\d,]+)</td>\s*<td[^>]*>([\d,]+)</td>", page)
        if len(found) != 39 or (sum(r for _, r, _ in found), sum(b for _, _, b in found)) != tuple(int(v.replace(",", "")) for v in total.groups()):
            print(f"{day}: {len(found)} counties, sums {sum(r for _, r, _ in found)}, {sum(b for _, _, b in found)}, total {total and total.groups()}")
        rows.extend((day, county, registered, ballots) for county, registered, ballots in found)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "county", "registered", "ballots"], orient="row").write_parquet(PARSED / "turnout.parquet")
    print(f"{len(rows)} county rows")


def emit() -> Path:
    voters = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): unit
            for unit, text in conn.execute(
                """select u.id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '53%%'"""
            ).fetchall()
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's registered voters and ballots counted by county for the primaries of 2014 to 2024",
            polity="US-WA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.vote.wa.gov/results/",
            license="Public record",
        )
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, URL.format(day=day)), source)
            ((election,),) = {
                (election_id,)
                for (election_id,) in conn.execute(
                    """select c.election_id from external_id x join contest c on c.id = x.entity_id
                       where x.scheme = 'us.contest' and x.value like %s""",
                    [f"{day}:WA:%"],
                )
            }
            result_set = cs.key(f"{SOURCE}.turnout", str(day))
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election not in held,
                completeness=1,
            )
            for county, registered, ballots in voters.filter(pl.col("day") == day).select("county", "registered", "ballots").iter_rows():
                for measure, value in ((2, registered), (3, ballots)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=UUID(str(counties[re.sub(r"[^a-z]", "", county.lower())])),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
