import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ca_pri_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://elections.cdn.sos.ca.gov/sov/"
FILES = {
    date(2002, 3, 5): "2002-primary/sov-complete.pdf",
    date(2004, 3, 2): "2004-primary/sov-complete.pdf",
    date(2006, 6, 6): "2006-primary/p5regandpart.pdf",
    date(2008, 2, 5): "2008-primary/04_voter_stats_by_county_feb08.pdf",
    date(2008, 6, 3): "2008-statewide-direct-primary/03_voter_stats_by_county_june_08.pdf",
    date(2010, 6, 8): "2010-primary/pdf/03-voter-reg-stats-by-county.pdf",
    date(2012, 6, 5): "2012-primary/pdf/03-voter-reg-stats-by-county.pdf",
    date(2014, 6, 3): "2014-primary/pdf/03-voter-particpiation-stats-by-county.pdf",
}
ROW = re.compile(r"^\s*([A-Z][A-Za-z. ]+?)\s+((?:[\d,]+\s+){5}[\d,]+)\s+[\d.]+%")
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote, voter participation statistics by county"


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def parse() -> None:
    rows: list[tuple[date, str, int, int]] = []
    for day, path in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        seen: dict[str, tuple[int, int]] = {}
        for line in text.splitlines():
            found = ROW.match(line)
            if found and found[1].strip() not in seen:
                numbers = [int(value.replace(",", "")) for value in found[2].split()]
                seen[found[1].strip()] = (numbers[2], numbers[5])
        total = seen.pop("State Total", None)
        if len(seen) != 58 or (total and (sum(r for r, _ in seen.values()), sum(b for _, b in seen.values())) != total):
            print(
                f"{day}: {len(seen)} counties, sums {sum(r for r, _ in seen.values())}, {sum(b for _, b in seen.values())} against {total}"
            )
        rows.extend((day, county, registered, ballots) for county, (registered, ballots) in seen.items())
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "county", "registered", "ballots"], orient="row").write_parquet(
        PARSED / "turnout.parquet"
    )
    print(f"{len(rows)} county rows")


def emit() -> Path:
    voters = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): unit
            for unit, text in conn.execute(
                """select u.id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%%'"""
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
            summary="California's registered voters and ballots cast by county for the primaries of 2002 to 2014",
            polity="US-CA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        for day in FILES:
            ((election,),) = {
                (election_id,)
                for (election_id,) in conn.execute(
                    """select c.election_id from external_id x join contest c on c.id = x.entity_id
                       where x.scheme = 'us.contest' and x.value like %s""",
                    [f"{day}:CA:%"],
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
            for county, registered, ballots in (
                voters.filter(pl.col("day") == day).select("county", "registered", "ballots").iter_rows()
            ):
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
