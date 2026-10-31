import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.md_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Maryland State Board of Elections"
YEARS = range(1996, 2010, 2)
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '24%'
"""
ROW = re.compile(r"([A-Z][A-Za-z.' ]+?)\s*\|\s*((?:[\d,]+\s*\|\s*)+)[\d.]+%")


def url(year: int) -> str:
    return f"https://2020mdmanual.msa.maryland.gov/msa/mdmanual/41electp/html/{year}p/regis.html"


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i)( county| co\.)$", "", name.strip()).lower())


def fetch() -> None:
    for year in YEARS:
        try:
            raw.cached(SOURCE, url(year))
            continue
        except LookupError:
            pass
        response = httpx.get(url(year), headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=60)
        response.raise_for_status()
        raw.store(
            SOURCE, url(year), response.content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html"
        )


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    codes["baltimore"] = "24005"
    codes["baltimorecity"] = "24510"
    rows = []
    for year in YEARS:
        html = raw.cached(SOURCE, url(year)).path.read_text(errors="replace")
        text = unescape(re.sub(r"<[^>]*>", "|", html))
        text = re.sub(r"\s*\|\s*(?:\|\s*)*", " | ", re.sub(r"\s+", " ", text))
        for match in ROW.finditer(text):
            name = plain(match[1])
            if name not in codes:
                continue
            numbers = [int(t.replace(",", "")) for t in match[2].split("|") if t.strip()]
            rows.append({"year": year, "code": codes[name], "registered": numbers[0], "voters": numbers[-1]})
    frame = pl.DataFrame(rows).unique(["year", "code"])
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("year").agg(pl.len(), pl.col("registered").sum(), pl.col("voters").sum()).sort("year"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's registered voters and ballots cast by county for its primaries of 1996–2008, "
            "from the Maryland Manual's voter participation tables",
            polity="US",
        )
        elections = {
            start.year: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':MD(:[a-z]+)?$' and e.kind = 'primary'"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Voter Participation by County"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://msa.maryland.gov/msa/mdmanual/41electp/html/elepr.html",
                license="Public record",
            )
        for (year,), rows in frame.group_by("year", maintain_order=True):
            if year not in elections:
                print(f"{year}: no election")
                continue
            value_key, election_id = elections[year]
            cs.artifact(raw.cached(SOURCE, url(year)), source)
            result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=round(rows.height / 24, 4),
            )
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", code),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
