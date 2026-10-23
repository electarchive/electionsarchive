import csv
import io
import re
import time
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, wa_results
from ea.sources.us.wa_county_archive import HOST, PUBLISHER, cells
from ea.store import DATA, connect

SOURCE = "us.wa_leg_archive"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "Election results database: county breakdowns"
ELECTIONS = {20: date(2000, 11, 7), 4: date(2002, 11, 5), 67: date(2004, 11, 2)}
HEADING = re.compile(r"State (Representative|Senator) District #(\d+)(?: Position #(\d))?(.*)")
RACE = re.compile(r"Legislative District (\d+) State (Representative|Senator)(?: Pos\. (\d))?(.*)")


def races(election: int) -> list[tuple[str, str, str]]:
    main = raw.cached(SOURCE, f"{HOST}results_report.aspx?e={election}").path.read_text(errors="ignore")
    found = []
    for heading, block, contest in re.findall(
        r'<h3 class="reset-margin">([^<]*)</h3>(.*?)results_report_county_breakdown\.aspx\?e=\d+&amp;t=(\d+)',
        main,
        re.DOTALL,
    ):
        if match := HEADING.fullmatch(heading.strip()):
            chamber, district, position, rest = match.groups()
            suffix = "s" if re.search(r"(?i)unexpired|short", rest) else "g"
            key = (
                f"house:{district}:{position}:{suffix}"
                if chamber == "Representative"
                else f"senate:{district}:{suffix}"
            )
            found.append((key, block, f"{HOST}results_report_county_breakdown.aspx?e={election}&t={contest}"))
    return found


def fetch() -> None:
    failed = 0
    for election in ELECTIONS:
        raw.fetch(SOURCE, f"{HOST}results_report.aspx?e={election}", suffix=".html")
        for _, _, url in races(election):
            try:
                raw.cached(SOURCE, url)
                continue
            except LookupError:
                pass
            try:
                raw.fetch(SOURCE, url, suffix=".html")
            except httpx.HTTPError:
                failed += 1
            time.sleep(6)
    for county in wa_results.COUNTIES:
        try:
            raw.cached(SOURCE, wa_results.SITE.format(county))
        except LookupError:
            raw.fetch(SOURCE, wa_results.SITE.format(county), suffix=".csv")
    if failed:
        raise RuntimeError(f"{failed} pages failed; run fetch again")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '53%%'"""
            ).fetchall()
        }
    rows: list[tuple[date, str, str, str, int]] = []
    for election, day in ELECTIONS.items():
        for key, block, url in races(election):
            printed = {
                cells(line)[0]: int(
                    next(cell for cell in cells(line)[1:] if cell.replace(",", "").isdigit()).replace(",", "")
                )
                for line in re.findall(r"<tr><td class=\"ResultData\">.*?</tr>", block, re.DOTALL)
            }
            body = raw.cached(SOURCE, url).path.read_text(errors="ignore")
            sums: dict[str, int] = {}
            part: list[tuple[date, str, str, str, int]] = []
            for county, table in re.findall(
                r'<h3 class="reset-margin">([^<]*)</h3></td></tr>(.*?)(?=<tr class="info">|$)', body, re.DOTALL
            ):
                code = codes.get(re.sub(r"[^a-z]", "", county.lower()))
                if code is None:
                    raise ValueError(f"{url}: no county {county}")
                for line in re.findall(r"<tr><td class=\"ResultData\">.*?</tr>", table, re.DOTALL):
                    name, *_, votes = cells(line)
                    part.append((day, key, code, name, int(votes.replace(",", ""))))
                    sums[name] = sums.get(name, 0) + int(votes.replace(",", ""))
            if not part or sums != printed:
                print(f"{day} {key}: county sums {sums} against printed {printed}, skipped")
                continue
            rows.extend(part)
    for county in wa_results.COUNTIES:
        for line in csv.DictReader(
            io.StringIO(raw.cached(SOURCE, wa_results.SITE.format(county)).path.read_text(encoding="utf-8-sig"))
        ):
            if match := RACE.fullmatch(line["Race"].strip()):
                district, chamber, position, rest = match.groups()
                if rest.strip():
                    raise ValueError(f"{county}: {line['Race']}")
                key = f"house:{district}:{position}:g" if chamber == "Representative" else f"senate:{district}:g"
                rows.append((wa_results.DAY, key, codes[county.lower()], line["Candidate"], int(line["Votes"])))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "chamber", "code", "candidate", "votes"], orient="row").select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber="chamber",
        day="day",
        special=pl.lit(False),
        state=pl.lit("WA"),
        district=pl.lit(None, pl.Int16),
        code="code",
        kind=pl.lit("county"),
        candidate=pl.col("candidate").str.to_uppercase(),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.contains("(?i)write"),
        votes="votes",
        source=pl.when(pl.col("day") == wa_results.DAY).then(pl.lit("WA08")).otherwise(pl.lit("WA")),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's 2000, 2002, 2004, and 2008 State House and Senate generals by county from the "
            "Secretary of State's results database and 2008 results exports",
            polity="US-WA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.wa.gov/elections/results_report.aspx",
            license="Public record",
        )
        for election in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, f"{HOST}results_report.aspx?e={election}"), source)
            for _, _, url in races(election):
                cs.artifact(raw.cached(SOURCE, url), source)
        exports = cs.source(PUBLISHER, wa_results.TITLE)
        if exports in cs.minted:
            raise ValueError(f"no source {wa_results.TITLE}")
        for county in wa_results.COUNTIES:
            cs.artifact(raw.cached(SOURCE, wa_results.SITE.format(county)), exports)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet"),
            {"WA": source, "WA08": exports},
            strict=True,
            authoritative="Source: the Secretary of State's certified county results.",
        )
        return cs.write()
