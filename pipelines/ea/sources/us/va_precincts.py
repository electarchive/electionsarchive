import csv
import io
import json
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.va_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Virginia Department of Elections"
TITLE = "Virginia Elections Database"
LANDING = "https://historical.elections.virginia.gov"
SEARCH = "https://va2.elstats.civera.com/api/download_search.csv?"
TABLE = "https://va2.elstats.civera.com/api/download_contest/{contest}_table.csv?split_party=false"
YEARS = range(2000, 2015)
OFFICES = {
    "President": "president",
    "U.S. Senate": "senate",
    "U.S. House": "house",
    "Governor": "governor",
    "Lieutenant Governor": "lieutenant-governor",
    "Attorney General": "attorney-general",
    "State Senate": "state-senate",
    "State Representative": "state-house",
}


def search(year: int) -> str:
    query = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {
            "candidates": [],
            "divisions": [],
            "offices": [
                {"free": office}
                for office in [
                    "President",
                    "Senate",
                    "U.S. House",
                    "Governor",
                    "Lieutenant Governor",
                    "Attorney General",
                    "State Representative",
                ]
            ],
        },
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return SEARCH + urlencode({"search": json.dumps(query, separators=(",", ":"))})


def contests(year: int) -> dict[str, tuple[str, str, str]]:
    found: dict[str, tuple[str, str, str]] = {}
    for row in csv.DictReader(io.StringIO(raw.cached(SOURCE, search(year)).path.read_text(encoding="utf-8-sig"))):
        if row["election_type"] == "General Election" and row["office_name"] in OFFICES:
            found[row["contest_id"]] = (row["election_date"][:10], OFFICES[row["office_name"]], row["district_name"])
    return found


def fetch() -> None:
    for year in YEARS:
        try:
            raw.cached(SOURCE, search(year))
        except LookupError:
            raw.fetch(SOURCE, search(year), suffix=".csv")
        for contest in contests(year):
            try:
                raw.cached(SOURCE, TABLE.format(contest=contest))
            except LookupError:
                raw.fetch(SOURCE, TABLE.format(contest=contest), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower().removesuffix(", va")): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'official'
                   where u.scheme_code = 'us.county' and u.code like '51%%'"""
            ).fetchall()
        }
    counties["bedfordcounty"] = "51019"
    counties["bedfordcity"] = "51515"
    counties["cliftonforgecity"] = "51005"
    rows = []
    odd = []
    tables: dict[tuple[str, str, str], tuple[int, list[list[str]]]] = {}
    for year in YEARS:
        for contest, (day, office, district) in contests(year).items():
            table = list(
                csv.reader(
                    io.StringIO(raw.cached(SOURCE, TABLE.format(contest=contest)).path.read_text(encoding="utf-8-sig"))
                )
            )
            size = sum(int(value or 0) for row in table[2:3] for value in row[2:])
            if (day, office, district) not in tables or size > tables[(day, office, district)][0]:
                tables[(day, office, district)] = (size, table)
    for (day, office, district), (_, table) in tables.items():
        if True:
            names = table[0][2:]
            groups: list[tuple[list[str], list[list[str]]]] = []
            for row in table[2:]:
                if row[0] == "Locality":
                    groups.append((row, []))
                elif row[0] == "Precinct" and groups:
                    groups[-1][1].append(row)
            for locality, precincts in groups:
                code = counties.get(re.sub(r"[^a-z]", "", locality[1].lower()))
                if code is None:
                    odd.append((year, contest, locality[1]))
                    continue
                total = [int(value or 0) for value in locality[2:]]
                tied = (
                    precincts
                    and [sum(int(row[2 + index] or 0) for row in precincts) for index in range(len(names))] == total
                )
                for row in precincts if tied else [locality]:
                    for name, value in zip(names, row[2:], strict=True):
                        rows.append(
                            (
                                day,
                                office,
                                district,
                                code,
                                locality[1].upper(),
                                row[1] if tied else None,
                                name,
                                int(value or 0),
                                "precinct" if tied else "county",
                            )
                        )
    if odd:
        raise ValueError(f"unmatched localities: {sorted(set(odd))[:10]}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema=["day", "office", "district", "county_code", "locality", "precinct", "candidate", "votes", "kind"],
        orient="row",
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = (
        pl.read_parquet(PARSED / "lines.parquet")
        .with_columns(
            day=pl.col("day").str.to_date(),
            candidate=pl.col("candidate")
            .str.to_uppercase()
            .str.replace(r",\s*(JR|SR|II|III|IV)\.?$", "")
            .str.strip_chars(),
        )
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            district=pl.col("district").str.extract(r"(\d+)", 1).cast(pl.Int16),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .with_columns(
            chamber=pl.when(pl.col("office") == "state-senate")
            .then(pl.concat_str(pl.lit("senate:"), pl.col("district").cast(pl.String), pl.lit(":g")))
            .when(pl.col("office") == "state-house")
            .then(pl.concat_str(pl.lit("house-of-delegates:"), pl.col("district").cast(pl.String), pl.lit(":g")))
            .otherwise("office"),
            code=pl.when(pl.col("kind") == "precinct")
            .then(
                pl.concat_str(
                    pl.col("day").cast(pl.String), pl.lit("VA"), "county_code", "locality", "precinct", separator=":"
                )
            )
            .otherwise("county_code"),
        )
        .group_by("day", "chamber", "district", "code", "county_code", "kind", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("VA"),
            source=pl.lit("VA"),
            district=pl.when(pl.col("chamber") == "house").then("district"),
        )
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virginia's presidential, federal, statewide, and legislative general elections by precinct, 2000 to 2014",
            polity="US-VA",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, search(year)), source)
            for contest in contests(year):
                cs.artifact(raw.cached(SOURCE, TABLE.format(contest=contest)), source)
        cs.scope_units("us.precinct", "____-__-__:VA:%")
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '51%%'"
            ).fetchall()
        }
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status = 'active'
                     and coalesce(r.completeness, 1) >= 0.98
                   join changeset g on g.id = r.changeset_id and g.pipeline <> %s
                   join result_value v on v.result_set_k = r.k
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
                   where x.scheme = 'us.contest' and x.value ~ '^20(0[0-9]|1[0-4])-..-..:VA:'""",
                [SOURCE],
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("VA"),
                pl.when(pl.col("district").is_not_null())
                .then(pl.lit("house:") + pl.col("district").cast(pl.String))
                .otherwise("chamber"),
                separator=":",
            ).is_in(list(filled))
        )
        units = (
            frame.filter(pl.col("kind") == "precinct")
            .group_by("code")
            .agg(pl.col("state").first(), pl.col("county_code").first(), pl.col("day").min())
            .sort("code")
        )
        cs.frame(
            "reporting_unit",
            units.select(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String),
                polity_id=pl.col("state").map_elements(
                    lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                ),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("county_code").replace_strict(
                    {code: str(unit) for code, (unit, _) in counties.items()}
                ),
                area_relation=pl.lit("within"),
                area_id=pl.col("county_code").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        medsl.attach(cs, conn, frame.drop("county_code"), {"VA": source}, grain="precinct")
        return cs.write()
