import html
import re
import time
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wa_county_archive"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HOST = "https://web.archive.org/web/2024if_/https://www.sos.wa.gov/elections/"
ELECTIONS = {
    3: date(1992, 11, 3),
    17: date(1996, 11, 5),
    20: date(2000, 11, 7),
    67: date(2004, 11, 2),
}
PUBLISHER = "Washington Secretary of State"
OFFICES = {
    "Lt. Governor": "Lieutenant Governor",
    "Secretary of State": "Secretary of State",
    "Attorney General": "Attorney General",
    "State Treasurer": "Treasurer",
    "State Auditor": "Auditor",
    "Commissioner of Public Lands": "Commissioner of Public Lands",
    "Insurance Commissioner": "Insurance Commissioner",
    "Superintendent of Public Instruction": "Superintendent of Public Instruction",
}
PARTIES = {
    "D": "DEMOCRAT",
    "R": "REPUBLICAN",
    "L": "LIBERTARIAN",
    "GRN": "GREEN",
    "CST": "CONSTITUTION",
}
SUMMARY = (
    "Washington's 1992, 1996, 2000, and 2004 statewide executive elections by county from the Secretary of State's "
    "results database"
)
METHOD = (
    "County totals are from the Secretary of State's elections results database ('Show County Breakdown'), read from the "
    "Internet Archive's copies of the pages. Each candidate's county sum equals the state total printed on the election's "
    "results page. The database prints party codes only; minor-party codes other than Democratic, Republican, "
    "Libertarian, Green, and Constitution are not expanded."
)


def pages(election: int) -> tuple[str, list[tuple[str, str]]]:
    url = f"{HOST}results_report.aspx?e={election}"
    return url, [
        (OFFICES[office], f"{HOST}results_report_county_breakdown.aspx?e={election}&t={contest}")
        for office, contest in re.findall(
            r'<h3 class="reset-margin">([^<]*)</h3>.*?results_report_county_breakdown\.aspx\?e=\d+&amp;t=(\d+)',
            raw.cached(SOURCE, f"{HOST}results_report.aspx?e={election}").path.read_text(errors="ignore"),
            re.S,
        )
        if office in OFFICES
    ]


def fetch() -> None:
    for election in ELECTIONS:
        raw.fetch(SOURCE, f"{HOST}results_report.aspx?e={election}", suffix=".html")
        time.sleep(1)
        for _, url in pages(election)[1]:
            raw.fetch(SOURCE, url, suffix=".html")
            time.sleep(1)


def cells(text: str) -> list[str]:
    return [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in re.findall(r"<td[^>]*>.*?</td>", text, re.S)]


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '53%%'"""
            ).fetchall()
        }
    rows: list[tuple[str, str, str, str, str, int]] = []
    for election, day in ELECTIONS.items():
        main = raw.cached(SOURCE, f"{HOST}results_report.aspx?e={election}").path.read_text(errors="ignore")
        for office, url in pages(election)[1]:
            printed: dict[str, int] = {}
            block = re.search(
                r'<h3 class="reset-margin">%s</h3>(.*?)Show County Breakdown' % re.escape(next(k for k, v in OFFICES.items() if v == office)),
                main,
                re.S,
            )[1]
            for line in re.findall(r"<tr><td class=\"ResultData\">.*?</tr>", block, re.S):
                name, _, votes, _ = cells(line)
                printed[name] = int(votes)
            body = raw.cached(SOURCE, url).path.read_text(errors="ignore")
            sums: dict[str, int] = {}
            seen: set[str] = set()
            for county, table in re.findall(r'<h3 class="reset-margin">([^<]*)</h3></td></tr>(.*?)(?=<tr class="info">|$)', body, re.S):
                seen.add(county)
                for line in re.findall(r"<tr><td class=\"ResultData\">.*?</tr>", table, re.S):
                    name, party, votes = cells(line)
                    rows.append((str(day), office, codes[re.sub(r"[^a-z]", "", county.lower())], name, party, int(votes)))
                    sums[name] = sums.get(name, 0) + int(votes)
            if len(seen) != 39 or sums != printed:
                raise ValueError(f"{day} {office}: {len(seen)} counties, sums {sums}, printed {printed}")
    frame = pl.DataFrame(rows, schema=["day", "title", "county", "candidate", "party", "votes"], orient="row")
    frame = frame.select(
        year=pl.col("day").str.slice(0, 4).cast(pl.Int32),
        day="day",
        state=pl.lit("WA"),
        title="title",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate=pl.col("candidate").str.to_uppercase(),
        party=pl.col("party").replace_strict(PARTIES, default=None, return_dtype=pl.String),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(39),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("day", "title").agg(pl.col("votes").sum()).sort("day", "title"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "returns.parquet")
    bases = {office: slug(office) for office in OFFICES.values()}
    with connect() as conn:
        cs = Changeset(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="US")
        source = cs.source(PUBLISHER, "Elections Results Database")
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title="Elections Results Database",
                url="https://www.sos.wa.gov/elections/results_report.aspx",
                license="Public record",
            )
        for election in ELECTIONS:
            url, breakdowns = pages(election)
            for artifact in (url, *(link for _, link in breakdowns)):
                cs.artifact(raw.cached(SOURCE, artifact), source)
        units = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '53%%'"
            ).fetchall()
        )
        contests = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':WA:'")
        )
        for (day, office), rows in frame.group_by("day", "title", maintain_order=True):
            key = f"{day}:WA:{bases[office]}"
            contest = contests[key]
            result_set = cs.key(f"{SOURCE}.result_set", key)
            roots = conn.execute(
                "select id, ballot_label from contest_option where contest_id = %s and parent_option_id is null "
                "and kind = 'candidate'",
                [contest],
            ).fetchall()
            old = conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness, supersedes_id
                   from result_set where contest_id = %s and is_default""",
                [contest],
            ).fetchall()
            ((previous,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            if len(old) != 1:
                raise ValueError(f"{key}: {len(old)} default result sets")
            row = old[0]
            if row[0] != result_set:
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            (
                                "id",
                                "election_id",
                                "contest_id",
                                "stage",
                                "kind",
                                "status",
                                "source_id",
                                "as_of",
                                "completeness",
                                "supersedes_id",
                            ),
                            row,
                            strict=True,
                        )
                    ),
                    is_default=False,
                )
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id where rs.id = %s""",
                [row[0]],
            ).fetchall()
            cs.add(
                "result_set",
                id=result_set,
                election_id=previous,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                supersedes_id=row[0] if row[0] != result_set else row[9],
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            targets: dict[str, UUID] = {}
            for name in rows["candidate"].unique(maintain_order=True):
                surname = re.sub(r"[^a-z]", "", name.split()[-1].lower())
                found = [option for option, label in roots if surname in re.sub(r"[^a-z]", "", (label or "").lower())]
                if len(found) != 1:
                    raise ValueError(f"{key}: {len(found)} options for {name}")
                targets[name] = found[0]
            if len(set(targets.values())) != len(targets) or len(targets) != len(roots):
                raise ValueError(f"{key}: candidates {sorted(targets)} do not match options {roots}")
            for option, kind, seats in outcomes:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            for county, name, votes in rows.select("county", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=units[county],
                    option_id=targets[name],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
