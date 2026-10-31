import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.tx_county_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Texas Secretary of State"
BASE = "https://www.sos.state.tx.us/elections/historical/"
TITLE = "County Voter Registration Figures"
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '48%' order by u.code
"""
ROW = re.compile(r"(\d{4})\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|")


def slug(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) county$", "", name).lower())


def counties() -> dict[str, str]:
    with connect() as conn:
        return {code: slug(name) for code, name in conn.execute(COUNTIES_SQL)}


def fetch() -> None:
    for page in counties().values():
        raw.fetch(SOURCE, f"{BASE}{page}.shtml", suffix=".html")


def parse() -> None:
    rows = []
    for code, page in counties().items():
        text = raw.cached(SOURCE, f"{BASE}{page}.shtml").path.read_text(encoding="utf8", errors="replace")
        text = text[text.find("Voter Registration Figures") :]
        text = re.sub(r"(?i)</t[dh]>", "|", text)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        for year, registered, voted in ROW.findall(text):
            rows.append(
                {
                    "code": code,
                    "year": int(year),
                    "registered": int(registered.replace(",", "")),
                    "voted": int(voted.replace(",", "")),
                }
            )
    frame = (
        pl.DataFrame(rows)
        .filter(pl.col("year") % 2 == 0, pl.col("registered") > 0, pl.col("voted") <= pl.col("registered"))
        .unique(["code", "year"])
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("year").agg(pl.len(), pl.col("registered").sum(), pl.col("voted").sum()).sort("year"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    pages = counties()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas's registered voters and ballots cast by county for the general elections of 1988 on, "
            "from the Secretary of State's county voter registration figures",
            polity="US",
        )
        elections = {}
        for value, entity_id in conn.execute(
            """select x.value, x.entity_id from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ '^\\d{4}-\\d\\d-\\d\\d:TX$' and e.kind = 'general'"""
        ):
            elections.setdefault(int(value[:4]), []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=f"{BASE}index.shtml",
                license="Public record",
            )
        for page in pages.values():
            cs.artifact(raw.cached(SOURCE, f"{BASE}{page}.shtml"), source)
        for (year,), rows in frame.group_by("year", maintain_order=True):
            first = date(year, 11, 1)
            day = date(year, 11, 2 + (7 - first.weekday()) % 7).isoformat()
            found = [f for f in elections.get(year, []) if f[0].startswith(day)]
            if len(found) != 1:
                print(f"{year}: {len(found)} elections")
                continue
            value_key, election_id = found[0]
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
                completeness=round(rows.height / len(pages), 4),
            )
            for code, registered, voted in rows.select("code", "registered", "voted").iter_rows():
                for measure, value in ((2, registered), (3, voted)):
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
