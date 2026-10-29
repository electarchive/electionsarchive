import csv
import io
import re
import unicodedata
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ga_runoff_2018"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = DATA / "inbox" / "manual" / "MF-267" / "july_24_2018_-_general_primary_runoff_election.zip"
URL = "https://results.enr.clarityelections.com/GA/#2018-07-24-runoff"
PUBLISHER = "Georgia Secretary of State"
TITLE = "July 24, 2018 General Primary Runoff Election: official results by county"
CONTESTS = {
    "Lieutenant Governor - REP": "2018-07-24:GA:lieutenant-governor:republican",
    "U.S. Representative, District 6 - DEM": "2018-07-24:GA:house:6:democratic",
    "U.S. Representative, District 7 - DEM": "2018-07-24:GA:house:7:democratic",
}


def fetch() -> None:
    raw.store(
        SOURCE,
        URL,
        FILE.read_bytes(),
        suffix=".zip",
        retrieved_at=datetime(2026, 10, 29, tzinfo=UTC),
        media_type="application/zip",
    )


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '13%%'"""
            ).fetchall()
        }
    rows: list[tuple[str, str, str, int]] = []
    with zipfile.ZipFile(raw.cached(SOURCE, URL).path) as archive:
        for member in archive.namelist():
            if not member.endswith("-summary.zip"):
                continue
            code = fips[re.sub(r"[^a-z]", "", re.match(r"(.+?)_\d", Path(member).name)[1].lower())]
            with zipfile.ZipFile(io.BytesIO(archive.read(member))) as inner:
                text = inner.read(inner.namelist()[0]).decode("utf-8-sig")
            for line in csv.DictReader(io.StringIO(text)):
                if line["contest name"] in CONTESTS:
                    rows.append((CONTESTS[line["contest name"]], code, line["choice name"], int(line["total votes"])))
    frame = pl.DataFrame(rows, schema=["contest", "fips", "candidate", "votes"], orient="row")
    print(frame.group_by("contest", "candidate").agg(pl.col("votes").sum(), pl.col("fips").n_unique().alias("counties")).sort("contest", "candidate"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia's July 24, 2018 primary runoffs for lieutenant governor and U.S. House districts 6 and 7 by county from the Secretary of State's results",
            polity="US-GA",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://results.enr.clarityelections.com/GA/",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        for contest_key in frame["contest"].unique().sort():
            found = conn.execute(
                """select c.id, rs.election_id, rs.k from contest c
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s
                   join result_set rs on rs.contest_id = c.id and rs.is_default""",
                [contest_key],
            ).fetchall()
            if len(found) != 1:
                raise ValueError(f"{contest_key}: {len(found)} contests matched")
            contest, election, held = found[0]
            options = conn.execute("select id, lower(ballot_label) from contest_option where contest_id = %s", [contest]).fetchall()
            held_totals = dict(
                conn.execute(
                    "select o.id, sum(v.value) from result_value v join contest_option o on o.k = v.option_k where v.result_set_k = %s and v.measure = 1 group by 1",
                    [held],
                ).fetchall()
            )
            picked = {}
            for name in frame.filter(pl.col("contest") == contest_key)["candidate"].unique():
                surname = re.sub(r"\s*\(.*$", "", name).strip().lower().replace(", jr", "").split()[-1]
                found_options = [option for option, label in options if surname in label]
                if len(found_options) != 1:
                    raise ValueError(f"{contest_key}: {len(found_options)} options for {name}")
                picked[name] = found_options[0]
            counted = dict(frame.filter(pl.col("contest") == contest_key).group_by("candidate").agg(pl.col("votes").sum()).iter_rows())
            print(contest_key, {picked[name]: votes for name, votes in counted.items()}, held_totals)
            result_set = cs.key(f"{SOURCE}.result_set", f"{contest}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                source_id=source,
                status="active",
                is_default=False,
                completeness=1.0,
            )
            for fips, name, votes in frame.filter(pl.col("contest") == contest_key).select("fips", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.county", fips),
                    option_id=picked[name],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
