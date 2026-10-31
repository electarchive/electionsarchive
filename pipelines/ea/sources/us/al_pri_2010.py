import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_pri_2010"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://sos.alabama.gov/sites/default/files/voter-pdfs/2010/2010-06-01-{}PrimaryResults.xls"
DAY = date(2010, 6, 1)
PUBLISHER = "Alabama Secretary of State"
TITLE = "Primary election results"


def fetch() -> None:
    for party in ("Dem", "Rep"):
        raw.fetch(SOURCE, URL.format(party), suffix=".xls")


def key(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def parse() -> None:
    with connect() as conn:
        fips = {
            key(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
        known: dict[str, list[int]] = {}
        for contest, total in conn.execute(
            """select x.value, coalesce(sum(v.value), 0)::bigint
               from external_id x join contest_option o on o.contest_id = x.entity_id and o.kind in ('candidate', 'ticket')
               left join result_set rs on rs.contest_id = x.entity_id and rs.is_default
               left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
               where x.scheme = 'us.contest' and x.value ~ '^2010-06-01:AL:'
               group by x.value, o.id"""
        ).fetchall():
            known.setdefault(contest, []).append(total)
    rows: list[tuple[str, int | None, str, str, int]] = []

    def sheet(party: str, name: str) -> list[tuple[str | None, ...]]:
        return pl.read_excel(
            raw.cached(SOURCE, URL.format(party)).path, sheet_name=name, has_header=False, infer_schema_length=0
        ).rows()

    def num(cell: str | None) -> int:
        return int(float(cell)) if cell else 0

    dem = sheet("Dem", "Statewide")
    for column in (3, 4):
        for line in dem[5:]:
            if line[0] and key(line[0]) in fips:
                rows.append(("senate:democratic", None, dem[1][column], fips[key(line[0])], num(line[column])))
    rep = sheet("Rep", "Statewide")
    for column in (12, 13):
        for line in rep[2:]:
            if line[0] and key(line[0]) in fips:
                rows.append(("senate:republican", None, rep[1][column], fips[key(line[0])], num(line[column])))
    regional = sheet("Rep", "Regional")
    district = None
    for column in range(2, len(regional[0])):
        found = re.fullmatch(r"Representative - +CD (\d+)", regional[0][column] or "")
        if regional[0][column]:
            district = int(found[1]) if found else None
        if district is None:
            continue
        for line in regional[2:]:
            if line[0] and key(line[0]) in fips:
                rows.append(("house:republican", district, regional[1][column], fips[key(line[0])], num(line[column])))
    district_sheet = sheet("Dem", "District")
    current: tuple[int, list[str | None]] | None = None
    for line in district_sheet:
        found = re.fullmatch(r"US Rep Dist (\d+)", (line[0] or "").strip())
        if found:
            current = (int(found[1]), list(line[4:]))
        elif line[0] and line[4] and not re.fullmatch(r"[\d.]+", line[4]) and line[0] != "Office & Ballot Name":
            current = None
        elif current and line[0] and line[2]:
            for county, value in zip(current[1], line[4:], strict=False):
                if county:
                    rows.append(("house:democratic", current[0], line[0].strip(), fips[key(county)], num(value)))
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "chamber": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(day=pl.lit(DAY))
        .group_by("day", "chamber", "district", "candidate", "code")
        .agg(pl.col("votes").sum())
    )
    keep = []
    for (day, chamber, district), lines in frame.group_by("day", "chamber", "district"):
        name, _, party = chamber.partition(":")
        contest = f"{day}:AL:" + (f"house:{district}" if district else name) + f":{party}"
        sums = sorted(t for t in lines.group_by("candidate").agg(pl.col("votes").sum())["votes"].to_list() if t > 0)
        filed = sorted(t for t in known.get(contest, []) if t > 0)
        if sums == filed:
            keep.append(lines)
        else:
            print(f"{contest}: county sums {sums} against the FEC's {filed}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(keep).with_columns(
        year=pl.lit(2010, dtype=pl.Int32),
        special=pl.lit(False),
        state=pl.lit("AL"),
        measure=pl.lit(1, dtype=pl.Int16),
        writein=pl.col("candidate").str.contains("(?i)write"),
        kind=pl.lit("county"),
        source=pl.lit("AL"),
    ).write_parquet(PARSED / "counties.parquet")
    print(f"{len(keep)} contests kept")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2010 U.S. House and Senate primaries by county",
            polity="US-AL",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.alabama.gov/alabama-votes/voter/election-data",
            license="Public record",
        )
        for party in ("Dem", "Rep"):
            cs.artifact(raw.cached(SOURCE, URL.format(party)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AL": source}, strict=True)
        return cs.write()
