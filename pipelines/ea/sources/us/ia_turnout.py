import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ia_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Iowa Secretary of State"
YEARS = range(2012, 2026, 2)
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '19%'
"""
ROW = re.compile(r"^\s*([A-Za-z.' ]+?)\s{2,}(\d[\d,]*\s.*)$")


def url(year: int, stage: str) -> str:
    return f"https://sos.iowa.gov/elections/pdf/{year}/{stage}/turnout.pdf"


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) county$", "", name).lower())


def fetch() -> None:
    for year in YEARS:
        for stage in ("general", "primary"):
            raw.fetch(SOURCE, url(year, stage), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    rows = []
    for year in YEARS:
        for stage in ("general", "primary"):
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, url(year, stage)).path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            for line in text.splitlines():
                match = ROW.match(line)
                if match is None or plain(match[1]) not in codes:
                    continue
                numbers = [int(t.replace(",", "")) for t in match[2].split() if re.fullmatch(r"[\d,]+", t)]
                if stage == "general":
                    if len(numbers) < 5 or numbers[0] + numbers[1] != numbers[2]:
                        print(f"{year} general {match[1]}: {numbers}")
                        continue
                    rows.append(
                        {
                            "year": year,
                            "stage": stage,
                            "code": codes[plain(match[1])],
                            "registered": numbers[3] + numbers[4],
                            "voters": numbers[2],
                        }
                    )
                else:
                    blocks = [numbers[i] for i in (0, 3, 6) if i < len(numbers)]
                    rows.append(
                        {
                            "year": year,
                            "stage": stage,
                            "code": codes[plain(match[1])],
                            "registered": None,
                            "voters": sum(blocks),
                        }
                    )
    frame = pl.DataFrame(rows, schema_overrides={"registered": pl.Int64}).unique(["year", "stage", "code"])
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("year", "stage")
        .agg(pl.len(), pl.col("registered").sum(), pl.col("voters").sum())
        .sort("year", "stage")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa's registered voters (active and inactive) and ballots cast by county for the generals of 2012–2024 "
            "and the ballots cast in its primaries, from the Secretary of State's turnout reports",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':IA(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            elections.setdefault((start.year, kind), []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Election Turnout Report"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://sos.iowa.gov/elections/",
                license="Public record",
            )
        for (year, stage), rows in frame.group_by("year", "stage", maintain_order=True):
            found = elections.get((year, stage), [])
            if len(found) > 1 and stage == "general":
                first = date(year, 11, 1)
                day = date(year, 11, 2 + (7 - first.weekday()) % 7)
                found = [f for f in found if f[0].startswith(day.isoformat())]
            if len(found) != 1:
                print(f"{year} {stage}: {len(found)} elections")
                continue
            value_key, election_id = found[0]
            cs.artifact(raw.cached(SOURCE, url(year, stage)), source)
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
                completeness=round(rows.height / 99, 4),
            )
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
                    if value is not None:
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
