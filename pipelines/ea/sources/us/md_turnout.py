import html
import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.md_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Maryland State Board of Elections"
BASE = "https://elections.maryland.gov/elections/archive/"
PAGES = {1992: "1992/turnout/general_statewide.html", 1996: "1996/turnout/general_statewide.html", 2000: "2000/turnout/general_statewide.html", 2002: "2002/turnout/g_statewide.html"}
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '24%' and u.code <> '24510'
"""


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) co\.?$", "", name).lower())


def fetch() -> None:
    for page in PAGES.values():
        raw.fetch(SOURCE, BASE + page, suffix=".html")


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    codes["baltimorecity"] = "24510"
    rows = []
    for year, page in PAGES.items():
        text = raw.cached(SOURCE, BASE + page).path.read_text(encoding="utf8", errors="replace")
        text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", "", text)
        text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?i)</tr>", "§", re.sub(r"(?i)</t[dh]>", "|", text))))
        for line in text.split("§"):
            cells = [c.strip() for c in line.split("|")]
            if cells[0].strip().lower().startswith("total") and rows and rows[-1]["year"] == year:
                break
            start = next((i for i, c in enumerate(cells) if plain(c) in codes), None)
            if start is None or len(cells) < start + 6:
                continue
            cells = cells[start:]
            numbers = [int(c.replace(",", "")) for c in cells[1:5] if re.fullmatch(r"[\d,]+", c)]
            if len(numbers) != 4:
                continue
            registered, voted = (numbers[3], numbers[2]) if year == 2002 else (numbers[0], numbers[3])
            rows.append({"year": year, "code": codes[plain(cells[0])], "registered": registered, "voted": voted})
    frame = pl.DataFrame(rows).unique(["year", "code"])
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("year").agg(pl.len(), pl.col("registered").sum(), pl.col("voted").sum()).sort("year"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's registered voters and ballots cast by county for the generals of 1992, 1996, 2000, and 2002, "
            "from the State Board of Elections' archive",
            polity="US",
        )
        elections = {}
        for value, entity_id in conn.execute(
            """select x.value, x.entity_id from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ '^\\d{4}-\\d\\d-\\d\\d:MD$' and e.kind = 'general'"""
        ):
            elections[int(value[:4])] = (value, entity_id)
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Voter Turnout, General Elections Archive"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url=BASE, license="Public record")
        for (year,), rows in frame.group_by("year", maintain_order=True):
            if year not in elections:
                print(f"{year}: no election")
                continue
            value_key, election_id = elections[year]
            cs.artifact(raw.cached(SOURCE, BASE + PAGES[year]), source)
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
