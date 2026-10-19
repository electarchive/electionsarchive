import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.elstats_leg_counties"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = {"CT": range(2000, 2015, 2), "NY": range(2000, 2015, 2), "VA": range(2001, 2016, 2)}
YEARS |= {"ID": range(2000, 2015, 2), "NM": range(2000, 2013, 2), "CO": range(2000, 2003, 2)}
TERMS = ["State Senat", "State Representative", "Assembly", "Delegate"]
CHAMBERS = {"NY": "assembly", "VA": "house-of-delegates"}
OFFICE = r"(?i)^(state senat\w*|state representative( [ab])?|member of assembly|member,? house of delegates)$"


def search(state: str, year: int) -> str:
    body = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"free": term} for term in TERMS]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return elstats.SEARCH.format(tenant=elstats.TENANTS[state][0]) + urlencode(
        {"search": json.dumps(body, separators=(",", ":"))}
    )


def fetch() -> None:
    for state, years in YEARS.items():
        for year in years:
            raw.fetch(SOURCE, search(state, year), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        counties: dict[str, dict[str, str]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515' order by u.code"""
        ).fetchall():
            names = counties.setdefault(iso.removeprefix("US-"), {})
            if int(code[2:]) >= 500:
                names[f"{elstats.plain(text)}city"] = code
            names.setdefault(elstats.plain(text), code)
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS") == "CT")
    for geoid, name in gazetteer.select("GEOID", "NAME").iter_rows():
        counties["CT"].setdefault(elstats.plain(name), geoid[:5])
        counties["CT"].setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
    for (state, name), code in elstats.FORMER.items():
        counties.setdefault(state, {})[name] = code
    frames = []
    for state, years in YEARS.items():
        for year in years:
            frame = pl.read_csv(raw.cached(SOURCE, search(state, year)).path, infer_schema=False)
            if "office_name" not in frame.columns or frame.height == 0:
                print(f"{state} {year}: no contests")
                continue
            office = pl.col("office_name").fill_null("").str.strip_chars()
            division = pl.col("division_name").fill_null("").str.replace(r"\s*\(.*\)$", "")
            frames.append(
                frame.filter(
                    office.str.contains(OFFICE),
                    pl.col("election_type").str.contains("(?i)^general"),
                    ~pl.col("office_modifier").fill_null("").str.contains("(?i)special|unexpired"),
                    pl.col("division_type").is_in(["County", "Locality", "City/Town"]),
                ).select(
                    year=pl.lit(year),
                    chamber=pl.concat_str(
                        pl.when(office.str.contains("(?i)senat"))
                        .then(pl.lit("senate"))
                        .otherwise(pl.lit(CHAMBERS.get(state, "house"))),
                        pl.col("district_name").str.extract(r"(\d+)").cast(pl.Int16).cast(pl.String),
                        office.str.extract(r" ([AB])$").replace_strict({"A": "1", "B": "2"}, default=None),
                        pl.lit("g"),
                        separator=":",
                        ignore_nulls=True,
                    ),
                    day=pl.lit(date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)),
                    special=pl.lit(False),
                    state=pl.lit(state),
                    district=pl.lit(None, pl.Int16),
                    code=division.map_elements(elstats.plain, return_dtype=pl.String)
                    .str.replace(r"(county|parish)$", "")
                    .replace_strict(counties[state], default=None),
                    division=division,
                    candidate=pl.col("candidate_name").fill_null("").str.to_uppercase().str.strip_chars(),
                    votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
                )
            )
    frame = pl.concat(frames).filter(
        pl.col("votes").is_not_null(), ~pl.col("candidate").str.contains(openelections.NOT_COUNTED)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        print(lost.group_by("state", "division").agg(pl.col("votes").sum()).sort("votes", descending=True).head(40))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.filter(pl.col("code").is_not_null())
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source="state")
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative general elections by county from the ElectionStats archives of Connecticut "
            "(by town, summed to county), New York, Virginia, Idaho, New Mexico, and Colorado, 2000–2015",
            polity="US",
        )
        covered = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                   join changeset c on c.id = r.changeset_id
                   where r.status = 'active' and c.pipeline <> %s
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.town', 'us.precinct'))""",
                [SOURCE],
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(pl.col("day").cast(pl.String), "state", "chamber", separator=":").is_in(list(covered))
        )
        sources = {}
        for state in frame["state"].unique().sort():
            sources[state] = cs.source(elstats.TENANTS[state][1], elstats.TITLE)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="official_body",
                    publisher=elstats.TENANTS[state][1],
                    title=elstats.TITLE,
                    url=elstats.TENANTS[state][2],
                    license="Public record",
                )
            for year in YEARS[state]:
                cs.artifact(raw.cached(SOURCE, search(state, year)), sources[state])
        medsl.attach(cs, conn, frame, sources)
        return cs.write()
