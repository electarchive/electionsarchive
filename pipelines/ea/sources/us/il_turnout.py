import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.il_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Illinois State Board of Elections"
PAGE = "https://www.elections.il.gov/ElectionOperations/VoterTurnout.aspx"
DROPDOWN = "ctl00$ContentPlaceHolder1$ddlElections"
ELECTIONS = {
    (1998, "primary"): 12,
    (1998, "general"): 11,
    (2000, "primary"): 7,
    (2000, "general"): 8,
    (2002, "primary"): 10,
    (2002, "general"): 13,
    (2004, "primary"): 15,
    (2004, "general"): 16,
    (2006, "primary"): 18,
    (2006, "general"): 19,
    (2008, "primary"): 21,
    (2008, "general"): 22,
    (2010, "primary"): 28,
    (2010, "general"): 29,
    (2012, "primary"): 32,
    (2012, "general"): 33,
    (2014, "primary"): 41,
    (2014, "general"): 43,
    (2016, "primary"): 50,
    (2016, "general"): 51,
    (2018, "primary"): 53,
    (2018, "general"): 54,
    (2020, "primary"): 56,
    (2020, "general"): 58,
    (2022, "primary"): 61,
    (2022, "general"): 62,
    (2024, "primary"): 65,
    (2024, "general"): 66,
    (2026, "primary"): 69,
}
HEADERS = {"User-Agent": "Mozilla/5.0"}


def key(option: int) -> str:
    return f"{PAGE}#election={option}"


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) county$", "", name).lower())


def fetch() -> None:
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=120) as client:
        for option in ELECTIONS.values():
            try:
                raw.cached(SOURCE, key(option))
                continue
            except LookupError:
                pass
            page = client.get(PAGE).text
            fields = {
                name: unescape(value)
                for name, value in re.findall(r'<input[^>]*name="(__[A-Z]+)"[^>]*value="([^"]*)"', page)
            }
            fields.update({"__EVENTTARGET": DROPDOWN, "__EVENTARGUMENT": "", DROPDOWN: str(option)})
            response = client.post(PAGE, data=fields)
            response.raise_for_status()
            if "gvTurnout" not in response.text:
                raise ValueError(f"option {option}: no table")
            raw.store(
                SOURCE,
                key(option),
                response.content,
                suffix=".html",
                retrieved_at=datetime.now(UTC),
                media_type="text/html",
            )


def parse() -> None:
    with connect() as conn:
        codes = {
            plain(name): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '17%'"""
            )
        }
    rows = []
    for (year, stage), option in ELECTIONS.items():
        html = raw.cached(SOURCE, key(option)).path.read_text(errors="replace")
        table = re.search(r"(?s)<table[^>]*gvTurnout.*?</table>", html)[0]
        found = {}
        total = None
        for row in re.findall(r"(?s)<tr.*?</tr>", table):
            cells = [unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"(?s)<t[dh].*?</t[dh]>", row)]
            if len(cells) < 3 or not re.fullmatch(r"[\d,]+", cells[1]):
                continue
            values = (int(cells[1].replace(",", "")), int(cells[2].replace(",", "")))
            if cells[0] == "All Jurisdictions":
                total = values
            elif plain(cells[0]) in codes:
                found[codes[plain(cells[0])]] = values
            else:
                print(f"{year} {stage}: non-county row {cells[0]} {values}")
        registered = sum(v[0] for v in found.values())
        voters = sum(v[1] for v in found.values())
        print(f"{year} {stage}: {len(found)} counties {registered}/{voters} vs {total}")
        if len(found) != 102 or total is None or registered != total[0] or voters != total[1]:
            continue
        for code, (r, v) in found.items():
            rows.append({"year": year, "stage": stage, "code": code, "registered": r, "voters": v})
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois's registered voters and ballots cast by county for the general primaries and general elections of 1998–2026, "
            "from the State Board of Elections' voter turnout tables",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':IL(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            if (kind == "general" and start.month == 11) or (kind == "primary" and start.month <= 6):
                elections.setdefault((start.year, kind), []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Voter Turnout"
        source = cs.source(PUBLISHER, title)
        published = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and source_id = %s and status <> 'withdrawn'",
                (source,),
            )
        }
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=PAGE,
                license="Public record",
            )
        for (year, stage), rows in frame.group_by("year", "stage", maintain_order=True):
            found = elections.get((year, stage), [])
            if len(found) != 1:
                print(f"{year} {stage}: {len(found)} elections")
                continue
            value_key, election_id = found[0]
            if election_id in published:
                continue
            cs.artifact(raw.cached(SOURCE, key(ELECTIONS[(year, stage)])), source)
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
                completeness=1,
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
