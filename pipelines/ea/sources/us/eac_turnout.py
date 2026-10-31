import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.eac_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "U.S. Election Assistance Commission"
BASE = "https://www.eac.gov/sites/default/files/eac_assets/1/6/"
FILES = {
    1996: date(1996, 11, 5),
    1998: date(1998, 11, 3),
    2000: date(2000, 11, 7),
    2002: date(2002, 11, 5),
}
EMIT_YEARS = (2002,)
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+")
STATES_SQL = """
select substr(p.iso_code, 4), ac.code, n.text
from polity p
join polity_territory pt on pt.polity_id = p.id
join area a on a.id = pt.area_id and a.scheme_code = 'us.state'
join area_code ac on ac.area_id = a.id
join name n on n.entity_id = p.id and n.is_preferred
where p.iso_code like 'US-%'
"""


def url(year: int) -> str:
    if year == 2002:
        return f"{BASE}2002%20Voter%20Registration%20and%20Turnout%20Overview.pdf"
    return f"{BASE}{year}%20Voter%20Registration%20and%20Turnout%20by%20State.pdf"


def title(year: int) -> str:
    return f"Voter Registration and Turnout - {year}"


def fetch() -> None:
    for year in FILES:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        states = {name.lower(): (st, fips) for st, fips, name in conn.execute(STATES_SQL)}
    rows = []
    for year in FILES:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(year)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for line in text.splitlines():
            numbers = NUMBER.findall(line)
            if len(numbers) < 2 or len(numbers) > 3:
                continue
            name = re.sub(r"[^a-z ]", "", line[: line.index(numbers[0])].lower()).strip()
            matches = {states[full] for full in states if name and full.startswith(name)}
            if len(matches) != 1:
                continue
            (st, fips) = matches.pop()
            if fips > "56":
                continue
            values = [int(n.replace(",", "")) for n in numbers]
            rows.append(
                {
                    "year": year,
                    "state": st,
                    "fips": fips,
                    "vap": values[0],
                    "registered": values[1] if len(values) == 3 else None,
                    "voters": values[-1],
                }
            )
    frame = pl.DataFrame(rows).with_columns(
        registered=pl.when(pl.col("registered") <= pl.col("vap")).then("registered")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.sort("year", "state").write_parquet(PARSED / "states.parquet")
    print(frame.group_by("year").len().sort("year"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "states.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Registered voters and turnout (the vote for the highest office) by state for the 2002 general election from the Election Assistance Commission's published tables",
            polity="US",
        )
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        for year, day in ((y, d) for y, d in FILES.items() if y in EMIT_YEARS):
            source = cs.source(PUBLISHER, title(year))
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title(year),
                    url=url(year),
                    license="Public domain",
                )
            cs.artifact(raw.cached(SOURCE, url(year)), source)
            for st, fips, registered, voters in (
                frame.filter(pl.col("year") == year).select("state", "fips", "registered", "voters").iter_rows()
            ):
                key = f"{day}:{st}"
                if key not in elections:
                    print(f"{key}: no election")
                    continue
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=elections[key],
                    contest_id=None,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=elections[key] not in held,
                    completeness=1,
                )
                for measure, value in ((2, registered), (14, voters)):
                    if value is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=cs.unit("us.state", fips),
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
