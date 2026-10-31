import html
import re
import time
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wa_county_archive"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HOST = "https://web.archive.org/web/2024if_/https://www.sos.wa.gov/elections/"
ELECTIONS = {
    137: 1898,
    132: 1900,
    131: 1904,
    130: 1906,
    129: 1908,
    127: 1910,
    125: 1912,
    123: 1914,
    120: 1916,
    118: 1918,
    116: 1920,
    114: 1922,
    112: 1924,
    108: 1926,
    106: 1928,
    103: 1930,
    102: 1932,
    100: 1934,
    69: 1936,
    97: 1938,
    92: 1940,
    68: 1942,
    94: 1944,
    64: 1946,
    95: 1948,
    63: 1950,
    62: 1952,
    60: 1954,
    58: 1956,
    56: 1958,
    51: 1960,
    49: 1962,
    47: 1964,
    45: 1966,
    43: 1968,
    42: 1970,
    41: 1972,
    39: 1974,
    38: 1976,
    35: 1978,
    33: 1980,
    31: 1982,
    29: 1984,
    27: 1986,
    25: 1988,
    24: 1990,
    3: 1992,
    15: 1994,
    17: 1996,
    10: 1998,
    20: 2000,
    4: 2002,
    67: 2004,
}
DAYS = {
    election: next(day for day in (date(year, 11, n) for n in range(2, 9)) if day.weekday() == 1)
    for election, year in ELECTIONS.items()
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
        (
            OFFICES.get(office) or f"house:{district[2]}",
            f"{HOST}results_report_county_breakdown.aspx?e={election}&t={contest}",
        )
        for office, contest in re.findall(
            r'<h3 class="reset-margin">([^<]*)</h3>.*?results_report_county_breakdown\.aspx\?e=\d+&amp;t=(\d+)',
            raw.cached(SOURCE, f"{HOST}results_report.aspx?e={election}").path.read_text(errors="ignore"),
            re.DOTALL,
        )
        if (
            district := re.fullmatch(r"(U\. S\. Representative District #)(\d+)", office)
            if ELECTIONS[election] >= 1992
            else None
        )
        or office in OFFICES
    ]


def fetch() -> None:
    failed = 0
    for election in ELECTIONS:
        try:
            raw.fetch(SOURCE, f"{HOST}results_report.aspx?e={election}", suffix=".html")
            links = pages(election)[1]
        except httpx.HTTPError, FileNotFoundError:
            failed += 1
            continue
        for _, url in links:
            try:
                raw.cached(SOURCE, url)
                continue
            except LookupError:
                pass
            try:
                raw.fetch(SOURCE, url, suffix=".html")
            except httpx.HTTPError:
                failed += 1
            time.sleep(2)
    if failed:
        raise RuntimeError(f"{failed} pages failed; run fetch again")


def cells(text: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
        for cell in re.findall(r"<td[^>]*>.*?</td>", text, re.DOTALL)
    ]


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
    for election, day in DAYS.items():
        try:
            main = raw.cached(SOURCE, f"{HOST}results_report.aspx?e={election}").path.read_text(errors="ignore")
        except LookupError:
            continue
        for office, url in pages(election)[1]:
            printed: dict[str, int] = {}
            block = re.search(
                r'<h3 class="reset-margin">{}</h3>(.*?)Show County Breakdown'.format(
                    re.escape(
                        f"U. S. Representative District #{office.removeprefix('house:')}"
                        if office.startswith("house:")
                        else next(k for k, v in OFFICES.items() if v == office)
                    )
                ),
                main,
                re.DOTALL,
            )[1]
            for line in re.findall(r"<tr><td class=\"ResultData\">.*?</tr>", block, re.DOTALL):
                printed[cells(line)[0]] = int(next(c for c in cells(line)[1:] if c.isdigit()))
            try:
                body = raw.cached(SOURCE, url).path.read_text(errors="ignore")
            except LookupError:
                print(f"{day} {office}: breakdown page not fetched")
                continue
            sums: dict[str, int] = {}
            seen: set[str] = set()
            part: list[tuple[str, str, str, str, str, int]] = []
            unknown: set[str] = set()
            for county, table in re.findall(
                r'<h3 class="reset-margin">([^<]*)</h3></td></tr>(.*?)(?=<tr class="info">|$)', body, re.DOTALL
            ):
                seen.add(county)
                code = codes.get(re.sub(r"[^a-z]", "", county.lower()))
                if code is None:
                    unknown.add(county)
                for line in re.findall(r"<tr><td class=\"ResultData\">.*?</tr>", table, re.DOTALL):
                    name, *middle, votes = cells(line)
                    party = middle[0] if middle else ""
                    part.append((str(day), office, code, name, party, int(votes)))
                    sums[name] = sums.get(name, 0) + int(votes)
            if unknown or not seen or sums != printed:
                print(
                    f"{day} {office}: held, {len(seen)} counties, unknown {sorted(unknown)}, sums {sums}, printed {printed}"
                )
                continue
            rows.extend(part)
    frame = (
        pl.DataFrame(rows, schema=["day", "title", "county", "candidate", "party", "votes"], orient="row")
        .group_by("day", "title", "county", "candidate", maintain_order=True)
        .agg(pl.col("party").first(), pl.col("votes").sum())
    )
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
        days = set(frame["day"])
        for election, day in DAYS.items():
            if str(day) not in days:
                continue
            url, breakdowns = pages(election)
            for artifact in (url, *(link for _, link in breakdowns)):
                try:
                    cs.artifact(raw.cached(SOURCE, artifact), source)
                except LookupError:
                    pass
        units = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '53%%'"
            ).fetchall()
        )
        contests = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':WA:'")
        )
        split = {
            (day, county)
            for (day, county), titles in frame.filter(pl.col("title").str.starts_with("house:")).group_by(
                "day", "county"
            )
            if titles["title"].n_unique() > 1
        }
        areas = dict(
            conn.execute(
                "select u.code, u.area_id from reporting_unit u where u.scheme_code = 'us.county' and u.code like '53%%'"
            ).fetchall()
        )
        for (day, office), rows in frame.group_by("day", "title", maintain_order=True):
            key = f"{day}:WA:{office if office.startswith('house:') else bases[office]}"
            contest = contests.get(key)
            if contest is None:
                print(f"{key}: no contest in the archive")
                continue
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
                print(f"{key}: {len(old)} default result sets")
                continue
            ((county_rows,),) = conn.execute(
                """select count(*) from result_value v join result_set r on r.k = v.result_set_k
                   join reporting_unit u on u.k = v.unit_k
                   where r.id = %s and u.scheme_code in ('us.county', 'us.county_part')""",
                [old[0][0]],
            ).fetchall()
            if county_rows:
                continue
            row = old[0]
            targets: dict[str, UUID] = {}
            for name in rows["candidate"].unique(maintain_order=True):
                surname = re.sub(r"[^a-z]", "", name.split()[-1].lower())
                found = [option for option, label in roots if surname in re.sub(r"[^a-z]", "", (label or "").lower())]
                if len(found) == 1:
                    targets[name] = found[0]
            if (
                len(set(targets.values())) != len(targets)
                or len(targets) != len(rows["candidate"].unique())
                or len(targets) != len(roots)
            ):
                print(
                    f"{key}: candidates {sorted(rows['candidate'].unique())} do not match options {[label for _, label in roots]}"
                )
                if row[0] == result_set and row[9] is not None:
                    columns = (
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
                    )
                    restored = conn.execute(
                        f"select {', '.join(columns)} from result_set where id = %s", [row[9]]
                    ).fetchone()
                    cs.add("result_set", **{**dict(zip(columns, restored, strict=True)), "is_default": True})
                    cs.add(
                        "result_set",
                        **{**dict(zip(columns, row, strict=True)), "is_default": False, "status": "withdrawn"},
                    )
                    cs.add(
                        "note",
                        entity_id=row[0],
                        kind="caveat",
                        body_md=(
                            "Withdrawn: the county breakdown lists a candidate the statewide results do not, so its rows "
                            "could not be matched to the candidates. The statewide totals remain the default."
                        ),
                    )
                continue
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
            for option, kind, seats in outcomes:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            unit_ids = dict(units)
            if office.startswith("house:"):
                ((district_area,),) = conn.execute(
                    "select district_area_id from contest where id = %s", [contest]
                ).fetchall()
                for county in rows["county"].unique():
                    if (day, county) not in split:
                        continue
                    part = f"{day[:4]}:{county}:{office.removeprefix('house:')}"
                    unit_ids[county] = cs.unit("us.county_part", part)
                    if unit_ids[county] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=unit_ids[county],
                            polity_id=cs.polity("US-WA"),
                            scheme_code="us.county_part",
                            code=part,
                            parent_id=units[county],
                            area_relation="within",
                            area_id=areas[county],
                            kind="standard",
                            valid_from=date.fromisoformat(day),
                            valid_to=date.fromisoformat(day) + timedelta(days=1),
                        )
                        cs.add(
                            "reporting_unit_area",
                            unit_id=unit_ids[county],
                            area_id=district_area,
                            valid_from=date.fromisoformat(day),
                            valid_to=date.fromisoformat(day) + timedelta(days=1),
                        )
            for county, name, votes in rows.select("county", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit_ids[county],
                    option_id=targets[name],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
