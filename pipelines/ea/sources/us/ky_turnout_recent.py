import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ky_turnout_recent"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Kentucky State Board of Elections"
BASE = "https://elect.ky.gov/SiteCollectionDocuments/Election%20Statistics/turnout/2011-2019"
FILES = {
    (2011, "gen"): BASE + "/2011/TRNSUMMARYGEX11.txt",
    (2012, "pri"): BASE + "/2012/PRI12trnsumm.txt",
    (2015, "gen"): "https://elect.ky.gov/statistics/Documents/voterturnoutcounty-2015G-20160126-031529.pdf",
    (2019, "gen"): "https://elect.ky.gov/Resources/Documents/voterturnoutcounty-2019G-20200323-080539.pdf",
    (2023, "gen"): "https://elect.ky.gov/Resources/Documents/2023G%20Turnout%20by%20County.pdf",
    (2024, "gen"): "https://elect.ky.gov/Resources/Documents/2024G%20Voter%20Turnout%20by%20County.pdf",
}
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '21%'
"""
ROW = re.compile(r"^\s*(?:\d{3}\s+)?([A-Z][A-Z .'-]+?)\s{2,}(\d[\d,]*\s.*)$")


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) county$", "", name).lower())


def fetch() -> None:
    for address in FILES.values():
        try:
            raw.cached(SOURCE, address)
            continue
        except LookupError:
            pass
        response = httpx.get(address, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=60)
        response.raise_for_status()
        raw.store(
            SOURCE,
            address,
            response.content,
            suffix=Path(address).suffix,
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf" if address.endswith(".pdf") else "text/plain",
        )


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    codes["muhlenburg"] = codes["muhlenberg"]
    rows = []
    for (year, stage), address in FILES.items():
        path = raw.cached(SOURCE, address).path
        if address.endswith(".pdf"):
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
        else:
            text = path.read_text(errors="replace")
        seen = set()
        for line in text.splitlines():
            match = ROW.match(line)
            if match is None or plain(match[1]) not in codes:
                continue
            numbers = [int(t.replace(",", "")) for t in match[2].split() if re.fullmatch(r"[\d,]+", t)]
            if len(numbers) < 2 or numbers[1] > numbers[0] or codes[plain(match[1])] in seen:
                print(f"{stage} {year} {match[1]}: {numbers}")
                continue
            seen.add(codes[plain(match[1])])
            rows.append({"year": year, "stage": stage, "code": codes[plain(match[1])], "registered": numbers[0], "voters": numbers[1]})
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("year", "stage").agg(pl.len(), pl.col("registered").sum(), pl.col("voters").sum()).sort("stage", "year"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kentucky's registered voters and ballots cast by county for its 2011 general, 2012 primary, and 2015, 2019, 2023, and 2024 generals, "
            "from the State Board of Elections' voter turnout reports",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':KY(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            if (kind == "general" and start.month == 11) or (kind == "primary" and start.month in (5, 6)):
                elections.setdefault((start.year, "gen" if kind == "general" else "pri"), []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Voter Turnout Reports"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://elect.ky.gov/Resources/Pages/Turnout.aspx",
                license="Public record",
            )
        published = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and source_id = %s and status <> 'withdrawn'", (source,)
            )
        }
        for (year, stage), rows in frame.group_by("year", "stage", maintain_order=True):
            found = elections.get((year, stage), [])
            if len(found) != 1:
                print(f"{year} {stage}: {len(found)} elections")
                continue
            value_key, election_id = found[0]
            if election_id in published:
                continue
            cs.artifact(raw.cached(SOURCE, FILES[(year, stage)]), source)
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
                completeness=round(rows.height / 120, 4),
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
