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

SOURCE = "us.sos_measure_files"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual"
SPREADSHEET = "{urn:schemas-microsoft-com:office:spreadsheet}"
OHIO = "https://www.ohiosos.gov/elections/election-results-and-data/"
PENNSYLVANIA = "https://www.electionreturns.pa.gov/Home/ReportCenter#{}"
WEST_VIRGINIA = "https://apps.sos.wv.gov/elections/results/#2018-11-06-detail"
PUBLISHERS = {
    "OH": ("Ohio Secretary of State", "Official county results of statewide issues"),
    "PA": ("Pennsylvania Department of State", "Election Returns: county results of ballot questions"),
    "GA": ("Georgia Secretary of State", "November 4, 2014 General Election: official results by county and precinct"),
    "WV": (
        "West Virginia Secretary of State",
        "2018 General Election: official county results of constitutional amendments",
    ),
}
OHIO_FILES = (
    ("MF-268/2012genissuesbycounty.xlsx", OHIO + "2012-elections-results/", "2012-11-06"),
    ("MF-268/issuesresults.xlsx", OHIO + "2014-elections-results/", "2014-05-06"),
)
OHIO_CONTESTS = {
    ("2012-11-06", "State Issue 1"): "2012-11-06:OH:question:wikipedia:ohio-issue-1-constitutional-convention-question",
    (
        "2012-11-06",
        "State Issue 2",
    ): "2012-11-06:OH:question:wikipedia:ohio-issue-2-redistricting-commission-initiative",
    (
        "2014-05-06",
        "State Issue 1",
    ): "2014-05-06:OH:question:wikipedia:ohio-issue-1-local-public-infrastructure-bond-amendment",
}
PENNSYLVANIA_FILES = {
    "MF-269/Official_10292026080056AM.CSV": "2016-04-26:PA:question:wikipedia:pennsylvania-philadelphia-traffic-court-abolition-amendment-proposed-constitutio",
    "MF-269/Official_10292026080149AM.CSV": "2016-11-08:PA:question:wikipedia:pennsylvania-judicial-retirement-age-amendment",
    "MF-269/Official_10292026080221AM.CSV": "2017-11-07:PA:question:wikipedia:pennsylvania-allow-local-taxing-authorities-to-exempt-full-value-of-homestead-am",
}
WEST_VIRGINIA_CONTESTS = {
    "Amendment No. 1": "2018-11-06:WV:question:wikipedia:west-virginia-amendment-1-no-right-to-abortion-in-constitution-measure",
    "Amendment No. 2": "2018-11-06:WV:question:wikipedia:west-virginia-amendment-2-legislative-authority-over-budgeting-for-state-judicia",
}
GEORGIA = "https://results.enr.clarityelections.com/GA/#2014-11-04"
GEORGIA_CONTESTS = {
    "Constitutional Amendment #1": "2014-11-04:GA:question:wikipedia:georgia-income-tax-rate-cap-amendment-a",
    "Constitutional Amendment #2": "2014-11-04:GA:question:wikipedia:georgia-reckless-driving-fines-for-injury-trust-fund-amendment-b",
    "Statewide Referendum A": "2014-11-04:GA:question:wikipedia:georgia-private-college-buildings-tax-exemption-referendum-1",
}
ARCHIVE = datetime(2026, 10, 29, tzinfo=UTC)


def key(name: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", name).lower().replace("saint ", "st "))


def number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def locations() -> dict[str, str]:
    return {
        **{path: url for path, url, _ in OHIO_FILES},
        **{path: PENNSYLVANIA.format(Path(path).stem) for path in PENNSYLVANIA_FILES},
        "MF-270/detailxls.zip": WEST_VIRGINIA,
        "MF-271/november_4_2014_-_general_election.zip": GEORGIA,
    }


def fetch() -> None:
    for path, url in locations().items():
        raw.store(
            SOURCE,
            url,
            (INBOX / path).read_bytes(),
            suffix=Path(path).suffix,
            retrieved_at=ARCHIVE,
            media_type="application/octet-stream",
        )


def parse() -> None:
    with connect() as conn:
        fips: dict[str, dict[str, str]] = {}
        for state, prefix in (("GA", "13"), ("OH", "39"), ("PA", "42"), ("WV", "54")):
            fips[state] = {
                key(text): code
                for code, text in conn.execute(
                    """select u.code, n.text from reporting_unit u
                       join name n on n.entity_id = u.area_id and n.kind = 'short'
                       where u.scheme_code = 'us.county' and u.code like %s""",
                    [prefix + "%"],
                ).fetchall()
            }
    rows: list[tuple[str, str, str, int, str]] = []
    for path, url, day in OHIO_FILES:
        sheet = pl.read_excel(raw.cached(SOURCE, url).path, has_header=False).rows()
        titles = [
            (index, str(cell).strip().replace("\n", " "))
            for index, cell in enumerate(sheet[0])
            if cell is not None and "State Issue" in str(cell)
        ]
        for index, title in titles:
            name = re.match(r"State Issue \d+", title)[0]
            for row in sheet[2:]:
                code = fips["OH"].get(key(str(row[0])))
                if code is None:
                    continue
                yes, no = number(row[index]), number(row[index + 1])
                rows += [
                    (OHIO_CONTESTS[(day, name)], code, "YES", yes, path),
                    (OHIO_CONTESTS[(day, name)], code, "NO", no, path),
                ]
    for path, contest in PENNSYLVANIA_FILES.items():
        text = raw.cached(SOURCE, PENNSYLVANIA.format(Path(path).stem)).path.read_text(encoding="utf-8-sig")
        for line in csv.DictReader(io.StringIO(text)):
            code = fips["PA"][key(line["County Name"])]
            rows += [
                (contest, code, "YES", number(line["Yes Votes"]), path),
                (contest, code, "NO", number(line["No Votes"]), path),
            ]
    with zipfile.ZipFile(raw.cached(SOURCE, WEST_VIRGINIA).path) as archive:
        book = ET.fromstring(archive.read("detail.xls").decode("utf-8-sig").encode())
    for sheet in book.iter(SPREADSHEET + "Worksheet"):
        table = [
            [
                (cell.find(SPREADSHEET + "Data").text if cell.find(SPREADSHEET + "Data") is not None else None)
                for cell in row.findall(SPREADSHEET + "Cell")
            ]
            for row in sheet.find(SPREADSHEET + "Table").findall(SPREADSHEET + "Row")
        ]
        title = re.match(r"Amendment No\. \d", (table[0][0] if table and table[0] else None) or "")
        if title is None:
            continue
        for row in table[3:]:
            code = fips["WV"].get(key(row[0] or ""))
            if code is not None:
                contest = WEST_VIRGINIA_CONTESTS[title[0]]
                rows += [
                    (contest, code, "YES", number(row[3]), "MF-270/detailxls.zip"),
                    (contest, code, "NO", number(row[5]), "MF-270/detailxls.zip"),
                ]
    with zipfile.ZipFile(raw.cached(SOURCE, GEORGIA).path) as archive:
        for member in archive.namelist():
            if not member.endswith("-detailtxt.zip"):
                continue
            code = fips["GA"][key(re.match(r"(.+?)_\d", Path(member).name)[1])]
            with zipfile.ZipFile(io.BytesIO(archive.read(member))) as inner:
                lines = inner.read(inner.namelist()[0]).decode("latin-1").splitlines()
            contest = None
            for line in lines:
                header = re.match(r"\s*(Constitutional Amendment #\d|Statewide Referendum A)", line)
                if header is not None:
                    contest = GEORGIA_CONTESTS[header[1]]
                elif contest is not None and line.startswith("Totals:"):
                    figures = [int(text) for text in line.split()[1:]]
                    rows += [(contest, code, "YES", figures[5], "MF-271"), (contest, code, "NO", figures[10], "MF-271")]
                    contest = None
    frame = pl.DataFrame(rows, schema=["contest", "fips", "answer", "votes", "path"], orient="row")
    print(
        frame.group_by("contest", "answer")
        .agg(pl.col("votes").sum(), pl.col("fips").n_unique().alias("counties"))
        .sort("contest", "answer")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    urls = locations()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot questions of Georgia, Ohio, Pennsylvania, and West Virginia by county from each state's official results files",
            polity="US",
        )
        sources = {}
        for state, (publisher, title) in PUBLISHERS.items():
            sources[state] = cs.source(publisher, title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url={
                        "OH": OHIO,
                        "PA": "https://www.electionreturns.pa.gov/",
                        "WV": "https://apps.sos.wv.gov/elections/results/",
                        "GA": "https://results.enr.clarityelections.com/GA/",
                    }[state],
                    license="Public record",
                )
        for path, url in urls.items():
            cs.artifact(
                raw.cached(SOURCE, url),
                sources[{"MF-268": "OH", "MF-269": "PA", "MF-270": "WV", "MF-271": "GA"}[path.split("/")[0]]],
            )
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
            options = dict(
                conn.execute(
                    "select upper(ballot_label), id from contest_option where contest_id = %s", [contest]
                ).fetchall()
            )
            statewide = dict(
                conn.execute(
                    "select upper(o.ballot_label), sum(v.value) from result_value v join contest_option o on o.k = v.option_k where v.result_set_k = %s and v.measure = 1 group by 1",
                    [held],
                ).fetchall()
            )
            counted = dict(
                frame.filter(pl.col("contest") == contest_key).group_by("answer").agg(pl.col("votes").sum()).iter_rows()
            )
            print(contest_key[:60], counted, {answer: int(votes) for answer, votes in statewide.items()})
            result_set = cs.key(f"{SOURCE}.result_set", f"{contest}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                source_id=sources[contest_key.split(":")[1]],
                status="active",
                is_default=False,
                completeness=1.0,
            )
            if {answer: int(votes) for answer, votes in statewide.items()} != counted:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="County totals are the state's official canvass of this question. The statewide totals held for the question, from the English Wikipedia, differ slightly from these county sums, which are the canvassed figures.",
                )
            for fips, answer, votes in (
                frame.filter(pl.col("contest") == contest_key).select("fips", "answer", "votes").iter_rows()
            ):
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
