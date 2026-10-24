import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nh_ge_2024"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-029" / "2024-ge-governor_3.xls"
URL = "https://www.sos.nh.gov/2024-general-election-results#2024-ge-governor_3.xls"
DAY = date(2024, 11, 5)
PUBLISHER = "New Hampshire Secretary of State"
TITLE = "2024 general election results, governor, by town"
WITHDRAWN = "Withdrawn: the same returns now load by town, the grain the Secretary of State publishes."
ALIASES = {
    "At. & Gil. Academy Grant": "Atkinson and Gilmanton Academy grant",
    "Wentworth's Location": "Wentworth location",
}


def key(text: str) -> str:
    text = re.sub(r",\s*NH$|\s+Ward\s+\d+$", "", ALIASES.get(text.strip(), text.strip()), flags=re.IGNORECASE)
    text = text.lower().replace("&", "and").replace("pur.", "purchase").replace("'", "")
    return re.sub(r"[^a-z]", "", re.sub(r"\s+(township|town|city)$", "", text))


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(
            SOURCE,
            URL,
            INBOX.read_bytes(),
            suffix=".xls",
            retrieved_at=datetime.fromtimestamp(INBOX.stat().st_mtime, UTC),
            media_type="application/vnd.ms-excel",
        )


def parse() -> None:
    path = raw.cached(SOURCE, URL).path
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '33%%'"""
            ).fetchall()
        }
        places = {
            (code[:5] if scheme == "us.town" else None, key(text)): (code, scheme.removeprefix("us."))
            for scheme, code, text in conn.execute(
                """select u.scheme_code, u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'official' and n.is_preferred
                   where (u.scheme_code = 'us.town' or u.scheme_code = 'us.place') and u.code like '33%%'"""
            ).fetchall()
        }
    summary = {
        row[0].strip().lower(): [int(cell) for cell in row[1:]]
        for row in pl.read_excel(path, sheet_name="summary gov", has_header=False, infer_schema_length=0).rows()[3:]
        if row[0] and row[1] and row[0].strip()
    }
    rows = []
    for county in sorted(codes):
        sheet = pl.read_excel(path, sheet_name=f"{county} gov", has_header=False, infer_schema_length=0).rows()
        names = [" ".join(cell.split()) for cell in sheet[2][1:]]
        towns = [row for row in sheet[3:] if row[0] and row[0].strip() and row[1]]
        total = next(row for row in towns if row[0].strip().upper() == "TOTALS")
        body = [[int(cell or 0) for cell in row[1:]] for row in towns if row[0].strip().upper() != "TOTALS"]
        printed = [int(cell) for cell in total[1:]]
        if [sum(column) for column in zip(*body, strict=True)] != printed or printed != summary[county]:
            raise ValueError(f"{county}: towns, county sheet, and summary disagree")
        tallies: dict[tuple[tuple[str, str], str], int] = {}
        for town, values in zip([row[0] for row in towns if row[0].strip().upper() != "TOTALS"], body, strict=True):
            place = places.get((codes[county], key(town))) or places[(None, key(town))]
            for name, votes in zip(names, values, strict=True):
                if name not in {"Undervotes", "Overvotes"}:
                    tallies[(place, name)] = tallies.get((place, name), 0) + votes
        for (place, name), votes in tallies.items():
            candidate = "WRITE-INS" if name == "Write-Ins" else re.sub(r",\s*[dlr]$", "", name).upper()
            rows.append((2024, "governor", DAY, False, "NH", None, *place, candidate, 1, name == "Write-Ins", votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "kind": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(source=pl.lit("NH")).write_parquet(PARSED / "towns.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's 2024 governor election by town, from the Secretary of State's town returns, with city wards summed to their city",
            polity="US-NH",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.nh.gov/2024-general-election-results",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "towns.parquet"), {"NH": source}, grain="town", strict=True)
        columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "supersedes_id", "completeness")
        for row in conn.execute(
            f"""select {", ".join(f"r.{column}" for column in columns)} from result_set r
                join external_id x on x.entity_id = r.id and x.scheme = %s
                where x.value like '%%:county' and (r.status = 'active' or r.is_default)""",
            [f"{SOURCE}.result_set"],
        ).fetchall():
            cs.add("result_set", **dict(zip(columns, row, strict=True)), status="withdrawn", is_default=False)
            cs.add("note", id=uuid5(row[0], "withdrawn"), entity_id=row[0], kind="caveat", body_md=WITHDRAWN)
        for row in conn.execute(
            f"""select {", ".join(f"r.{column}" for column in columns)} from result_set r
                join external_id x on x.entity_id = r.id and x.scheme = %s
                where x.value like '%%:town' and r.status = 'active' and not r.is_default and exists (
                  select from result_set d where d.contest_id = r.contest_id and d.stage = r.stage
                    and d.is_default and d.status = 'withdrawn')""",
            [f"{SOURCE}.result_set"],
        ).fetchall():
            cs.add("result_set", **dict(zip(columns, row, strict=True)), status="active", is_default=True)
        return cs.write()
