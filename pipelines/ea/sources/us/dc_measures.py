import csv
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.dc_measures"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://electionresults.dcboe.org/Downloads/Reports/"
PUBLISHER = "District of Columbia Board of Elections"
TITLE = "Election results"
FILES = {
    date(2018, 6, 19): (BASE + "June_19_2018_Primary_Election_Certified_Results.csv", "2018-06-19:DC:question:wikipedia:washington-d-c-initiative-77-minimum-wage-increase-for-tipped-workers", "INITIATIVE MEASURE NO. 77", "2018-06-19:DC:{}"),
    date(2020, 11, 3): (BASE + "November_3_2020_General_Election_Certified_Results.csv", "2020-11-03:DC:question:wikipedia:washington-d-c-initiative-81-entheogenic-plants-and-fungus-measure", "INITIATIVE MEASURE NO. 81", "2020:DC:{}"),
}


def fetch() -> None:
    for url, *_ in FILES.values():
        raw.fetch(SOURCE, url, suffix=".csv")


def parse() -> None:
    rows: list[tuple[date, str, str, int]] = []
    for day, (url, _, name, code) in FILES.items():
        text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig").splitlines()
        for line in csv.DictReader(text):
            if line["ContestName"].strip() == name and line["Candidate"].startswith(("YES", "NO")):
                rows.append((day, code.format(line["PrecinctNumber"]), line["Candidate"][:3].strip(", ").upper(), int(line["Votes"])))
    frame = pl.DataFrame(rows, schema=["day", "code", "answer", "votes"], orient="row")
    print(frame.group_by("day", "answer").agg(pl.col("votes").sum(), pl.col("code").n_unique().alias("precincts")).sort("day", "answer"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The District of Columbia's Initiative 77 (2018) and Initiative 81 (2020) by precinct from the Board of Elections' certified results files",
            polity="US-DC",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://electionresults.dcboe.org/",
                license="Public record",
            )
        ((county, county_area),) = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = '11001'"
        ).fetchall()
        for day, (url, contest_key, _, _) in FILES.items():
            cs.artifact(raw.cached(SOURCE, url), source)
            ((contest, election, held),) = conn.execute(
                """select c.id, rs.election_id, rs.k from contest c
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s
                   join result_set rs on rs.contest_id = c.id and rs.is_default""",
                [contest_key],
            ).fetchall()
            options = {
                label: option
                for option, label in conn.execute("select id, upper(ballot_label) from contest_option where contest_id = %s", [contest]).fetchall()
            }
            rows = frame.filter(pl.col("day") == day)
            counted = dict(rows.group_by("answer").agg(pl.col("votes").sum()).iter_rows())
            print(day, counted, conn.execute("select sum(value) from result_value where result_set_k = %s", [held]).fetchone())
            result_set = cs.key(f"{SOURCE}.result_set", f"{contest}:precinct")
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
            existing = {code for (code,) in conn.execute("select code from reporting_unit where scheme_code = 'us.precinct' and code like %s", [f"{rows['code'][0].rsplit(':', 1)[0]}:%"]).fetchall()}
            for code in rows["code"].unique():
                if code not in existing:
                    cs.add(
                        "reporting_unit",
                        id=cs.unit("us.precinct", code),
                        polity_id=cs.polity("US-DC"),
                        scheme_code="us.precinct",
                        code=code,
                        parent_id=county,
                        area_relation="within",
                        area_id=county_area,
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
            for code, answer, votes in rows.select("code", "answer", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.precinct", code),
                    option_id=options[answer],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
