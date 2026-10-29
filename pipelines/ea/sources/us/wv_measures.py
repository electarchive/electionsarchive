import csv
import re
import time
import unicodedata
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import wv_sos
from ea.store import DATA, connect

SOURCE = "us.wv_measures"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = wv_sos.URL.format(24)
TITLE = "Election results by county: 2017 Bond Amendment Election"


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".csv")
    time.sleep(2)


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
    rows: list[tuple[str, str, int]] = []
    totals: dict[str, int] = {}
    for line in csv.DictReader(raw.cached(SOURCE, URL).path.read_text(encoding="utf-8-sig").splitlines()):
        if line["OfficialResults"] != "Yes":
            continue
        answer = "YES" if line["Name"].startswith("For") else "NO"
        code = fips.get(re.sub(r"[^a-z]", "", line["CountyName"].lower()))
        if code is None:
            totals[answer] = int(line["Votes"])
        else:
            rows.append((code, answer, int(line["Votes"])))
    frame = pl.DataFrame(rows, schema=["fips", "answer", "votes"], orient="row")
    sums = dict(frame.group_by("answer").agg(pl.col("votes").sum()).iter_rows())
    print(len(frame["fips"].unique()), "counties", sums, "printed", totals)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="West Virginia's 2017 Roads to Prosperity bond amendment by county from the Secretary of State's results export",
            polity="US-WV",
        )
        source = cs.source(wv_sos.PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=wv_sos.PUBLISHER,
                title=TITLE,
                url=wv_sos.SITE,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        found = conn.execute(
            """select c.id, rs.election_id from contest c
               join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value like '2017-10-07:WV:question:wikipedia:%%'
               join result_set rs on rs.contest_id = c.id and rs.is_default"""
        ).fetchall()
        if len(found) != 1:
            raise ValueError(f"{len(found)} contests matched")
        contest, election = found[0]
        options = dict(
            conn.execute(
                "select upper(ballot_label), id from contest_option where contest_id = %s", [contest]
            ).fetchall()
        )
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
        for fips, answer, votes in frame.iter_rows():
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=cs.unit("us.county", fips),
                option_id=options[answer],
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
        return cs.write()
