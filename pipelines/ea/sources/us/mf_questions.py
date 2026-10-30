import csv
import io
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.mf_questions"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MANUAL = DATA / "inbox" / "manual"
SHEET = "{urn:schemas-microsoft-com:office:spreadsheet}"
INPUTS = {
    "ga14": (
        MANUAL / "MF-271" / "november_4_2014_-_general_election.zip",
        "https://sos.ga.gov/sites/default/files/2026-04/november_4_2014_-_general_election.zip",
        "Georgia Secretary of State",
        "November 4, 2014 General Election: official results by county",
        "13",
    ),
    "oh12": (
        MANUAL / "MF-268" / "2012genissuesbycounty.xlsx",
        "https://data.ohiosos.gov/portal/past-election-results#2012-general-issues",
        "Ohio Secretary of State",
        "November 6, 2012 General Election: statewide issues by county",
        "39",
    ),
    "oh14": (
        MANUAL / "MF-268" / "issuesresults.xlsx",
        "https://data.ohiosos.gov/portal/past-election-results#2014-primary-issues",
        "Ohio Secretary of State",
        "May 6, 2014 primary election: statewide issues by county",
        "39",
    ),
    "pa16p": (
        MANUAL / "MF-269" / "Official_10292026080056AM.CSV",
        "https://www.electionreturns.pa.gov/ReportCenter/Reports#2016-primary-ballot-questions",
        "Pennsylvania Department of State",
        "2016 Presidential Primary: ballot questions by county",
        "42",
    ),
    "pa16g": (
        MANUAL / "MF-269" / "Official_10292026080149AM.CSV",
        "https://www.electionreturns.pa.gov/ReportCenter/Reports#2016-general-ballot-questions",
        "Pennsylvania Department of State",
        "2016 Presidential Election: ballot questions by county",
        "42",
    ),
    "pa17": (
        MANUAL / "MF-269" / "Official_10292026080221AM.CSV",
        "https://www.electionreturns.pa.gov/ReportCenter/Reports#2017-municipal-ballot-questions",
        "Pennsylvania Department of State",
        "2017 Municipal Election: ballot questions by county",
        "42",
    ),
    "wv18": (
        MANUAL / "MF-270" / "detailxls.zip",
        "https://results.enr.clarityelections.com/WV/92360/",
        "West Virginia Secretary of State",
        "November 6, 2018 General Election: detail by county",
        "54",
    ),
}
GA_CONTESTS = {
    "Constitutional Amendment #1": "2014-11-04:GA:question:wikipedia:georgia-income-tax-rate-cap-amendment-a",
    "Constitutional Amendment #2": "2014-11-04:GA:question:wikipedia:georgia-reckless-driving-fines-for-injury-trust-fund-amendment-b",
    "Statewide Referendum A": "2014-11-04:GA:question:wikipedia:georgia-private-college-buildings-tax-exemption-referendum-1",
}
PA_CONTESTS = {
    "pa16p": "2016-04-26:PA:question:wikipedia:pennsylvania-philadelphia-traffic-court-abolition-amendment-proposed-constitutio",
    "pa16g": "2016-11-08:PA:question:wikipedia:pennsylvania-judicial-retirement-age-amendment",
    "pa17": "2017-11-07:PA:question:wikipedia:pennsylvania-allow-local-taxing-authorities-to-exempt-full-value-of-homestead-am",
}
WV_CONTESTS = {
    "Amendment No. 1": "2018-11-06:WV:question:wikipedia:west-virginia-amendment-1-no-right-to-abortion-in-constitution-measure",
    "Amendment No. 2": "2018-11-06:WV:question:wikipedia:west-virginia-amendment-2-legislative-authority-over-budgeting-for-state-judicia",
}


def fetch() -> None:
    for path, url, _, _, _ in INPUTS.values():
        raw.store(
            SOURCE,
            url,
            path.read_bytes(),
            suffix=path.suffix.lower(),
            retrieved_at=datetime(2026, 10, 29, tzinfo=UTC),
            media_type="application/octet-stream",
        )


def parse() -> None:
    with connect() as conn:
        counties = conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county'"""
        ).fetchall()

    def fips(prefix: str, name: str) -> str:
        key = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", name).lower())
        found = [code for code, text in counties if code.startswith(prefix) and re.sub(r"[^a-z]", "", text.lower()) == key]
        if len(found) != 1:
            raise ValueError(f"{prefix} {name}: {len(found)} counties")
        return found[0]

    rows: list[tuple[str, str, str, str, int]] = []
    path, url, _, _, prefix = INPUTS["ga14"]
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        for member in archive.namelist():
            if not member.endswith("-summary.zip"):
                continue
            code = fips(prefix, re.match(r"(.+?)_\d", Path(member).name)[1])
            with zipfile.ZipFile(io.BytesIO(archive.read(member))) as inner:
                text = inner.read(inner.namelist()[0]).decode("utf-8-sig")
            for line in csv.DictReader(io.StringIO(text)):
                contest = GA_CONTESTS.get(line["contest name"].split("<br>")[0])
                if contest is not None:
                    rows.append(("ga14", contest, code, line["choice name"].title(), int(line["total votes"])))

    for name, contest, yes, no, sheet in (
        ("oh12", "2012-11-06:OH:question:wikipedia:ohio-issue-1-constitutional-convention-question", 6, 7, "State Issues"),
        ("oh12", "2012-11-06:OH:question:wikipedia:ohio-issue-2-redistricting-commission-initiative", 8, 9, "State Issues"),
        ("oh14", "2014-05-06:OH:question:wikipedia:ohio-issue-1-local-public-infrastructure-bond-amendment", 5, 6, "Statewide Issues"),
    ):
        path, url, _, _, prefix = INPUTS[name]
        sheet_rows = pl.read_excel(raw.cached(SOURCE, url).path, sheet_name=sheet, has_header=False, infer_schema_length=0).rows()
        for row in sheet_rows:
            if row[0] is None or row[3] is None or not re.fullmatch(r"\d+", row[yes] or ""):
                continue
            if row[0] in ("County", "County Name", "Total", "Percentage", "Percentage of Votes"):
                continue
            code = fips(prefix, row[0])
            rows.append((name, contest, code, "Yes", int(row[yes])))
            rows.append((name, contest, code, "No", int(row[no])))

    for name, contest in PA_CONTESTS.items():
        _, url, _, _, prefix = INPUTS[name]
        for line in csv.DictReader(io.StringIO(raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig"))):
            code = fips(prefix, line["County Name"])
            rows.append((name, contest, code, "Yes", int(line["Yes Votes"].replace(",", ""))))
            rows.append((name, contest, code, "No", int(line["No Votes"].replace(",", ""))))

    _, url, _, _, prefix = INPUTS["wv18"]
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        workbook = ET.fromstring(archive.read("detail.xls"))
    for sheet in workbook.iter(f"{SHEET}Worksheet"):
        table = [[cell.text or "" for cell in row.iter(f"{SHEET}Data")] for row in sheet.iter(f"{SHEET}Row")]
        title = next((WV_CONTESTS[key] for key in WV_CONTESTS if table and table[0] and table[0][0].startswith(key)), None)
        if title is None:
            continue
        for row in table[3:]:
            if row[0] == "Total:":
                continue
            code = fips(prefix, row[0])
            rows.append(("wv18", title, code, "Yes", int(row[3])))
            rows.append(("wv18", title, code, "No", int(row[5])))

    frame = pl.DataFrame(rows, schema=["input", "contest", "fips", "answer", "votes"], orient="row")
    print(frame.group_by("contest", "answer").agg(pl.col("votes").sum(), pl.col("fips").n_unique().alias("counties")).sort("contest", "answer"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="County results for Georgia's 2014, Ohio's 2012 and 2014, Pennsylvania's 2016 and 2017, and West Virginia's 2018 statewide ballot questions from the states' official results",
            polity="US",
        )
        for name in frame["input"].unique(maintain_order=True):
            _, url, publisher, title, _ = INPUTS[name]
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add("source", id=source, kind="official_body", publisher=publisher, title=title, url=url.split("#")[0], license="Public record")
            cs.artifact(raw.cached(SOURCE, url), source)
            for contest_key in frame.filter(pl.col("input") == name)["contest"].unique().sort():
                found = conn.execute(
                    """select c.id, rs.election_id, rs.k from contest c
                       join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s
                       join result_set rs on rs.contest_id = c.id and rs.is_default""",
                    [contest_key],
                ).fetchall()
                if len(found) != 1:
                    raise ValueError(f"{contest_key}: {len(found)} contests matched")
                contest, election, held = found[0]
                options = dict(conn.execute("select ballot_label, id from contest_option where contest_id = %s", [contest]).fetchall())
                held_totals = dict(
                    conn.execute(
                        """select o.ballot_label, sum(v.value) from result_value v
                           join contest_option o on o.k = v.option_k where v.result_set_k = %s and v.measure = 1 group by 1""",
                        [held],
                    ).fetchall()
                )
                part = frame.filter(pl.col("contest") == contest_key)
                print(contest_key[:70], dict(part.group_by("answer").agg(pl.col("votes").sum()).iter_rows()), held_totals)
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
                for fips, answer, votes in part.select("fips", "answer", "votes").iter_rows():
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
