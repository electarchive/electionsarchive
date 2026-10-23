import json
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl, openelections
PUBLISHER = "Colorado Secretary of State"
SITE = "https://historicalelectiondata.coloradosos.gov"
from ea.store import DATA, connect

SOURCE = "us.co_leg_extra"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TENANT = "co.elstats"
YEARS = [2000, 2010, 2014]
TITLE = "Historical election database: General Assembly primaries and generals"
OFFICE = r"(?i)^(state senat\w*|state representative)$"
PREFERENCE = ["g", "gs", "s"]


def search(year: int) -> str:
    body = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {
            "candidates": [],
            "divisions": [],
            "offices": [{"free": term} for term in ["State Senat", "State Representative"]],
        },
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return elstats.SEARCH.format(tenant=TENANT) + urlencode({"search": json.dumps(body, separators=(",", ":"))})


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, search(year), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        counties = {
            elstats.plain(text): code
            for code, text in conn.execute(
                "select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short' "
                "where u.scheme_code = 'us.county' and u.code like '08%' order by u.code"
            ).fetchall()
        } | {name: code for (state, name), code in elstats.FORMER.items() if state == "CO"}
        covered = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                   where r.status = 'active' and x.value like '%%:CO:%%'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.town', 'us.precinct'))"""
            ).fetchall()
        }
        keys = {
            key
            for (key,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value like '%%:CO:%%'"
            ).fetchall()
        }
    frame = pl.concat(
        pl.read_csv(raw.cached(SOURCE, search(year)).path, infer_schema=False, truncate_ragged_lines=True)
        for year in YEARS
    )
    office = pl.col("office_name").fill_null("").str.strip_chars()
    division = pl.col("division_name").fill_null("").str.replace(r"\s*\(.*\)$", "")
    frame = frame.filter(
        office.str.contains(OFFICE),
        pl.col("division_type") == "County",
        pl.col("district_name").str.contains(r"^\d+$"),
    ).select(
        day=pl.col("election_date").str.slice(0, 10),
        chamber=pl.when(office.str.contains("(?i)senat")).then(pl.lit("senate")).otherwise(pl.lit("house")),
        district=pl.col("district_name"),
        party=pl.col("primary_party").fill_null(""),
        code=division.map_elements(elstats.plain, return_dtype=pl.String)
        .str.replace(r"(county|parish)$", "")
        .replace_strict(counties, default=None),
        division=division,
        candidate=pl.col("candidate_name").fill_null("").str.to_uppercase().str.strip_chars().str.replace(r",?\s+(JR|SR|II|III|IV)\.?$", ""),
        votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
    ).filter(pl.col("votes").is_not_null(), ~pl.col("candidate").str.contains(openelections.NOT_COUNTED))
    resolved = []
    for (day, chamber, district, party), lines in frame.group_by("day", "chamber", "district", "party"):
        base = f"{day}:CO:{chamber}:{district}"
        found = {
            key.removeprefix(base).removeprefix(":"): key
            for key in keys
            if key == base or key.startswith(base + ":")
        }
        suffix = {"Republican": "rp", "Democratic": "dp"}.get(party)
        pick = suffix if suffix in found else next((s for s in PREFERENCE if s in found and not party), None)
        if pick is None or found[pick] in covered:
            continue
        resolved.append(lines.with_columns(chamber=pl.lit(f"{chamber}:{district}" + (f":{pick}" if pick else ""))))
    frame = pl.concat(resolved)
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        print(lost.group_by("division").agg(pl.col("votes").sum()).sort("votes", descending=True).head(40))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("code").is_not_null()).with_columns(
        measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
        writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
    ).with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate")).filter(
        pl.col("measure") > 0
    ).group_by("day", "chamber", "code", "candidate", "measure").agg(pl.col("writein").any(), pl.col("votes").sum()).with_columns(
        year=pl.col("day").str.slice(0, 4).cast(pl.Int32),
        day=pl.col("day").str.to_date(),
        special=pl.lit(False),
        state=pl.lit("CO"),
        district=pl.lit(None, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("CO"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Colorado General Assembly primaries and generals by county from the Secretary of State",
            polity="US-CO",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=SITE, license="Public record")
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, search(year)), source)
        medsl.attach(cs, conn, frame, {"CO": source}, strict=True)
        return cs.write()
