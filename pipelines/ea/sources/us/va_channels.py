import csv
import io
import json
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, va_precincts
from ea.store import DATA, connect

SOURCE = "us.va_channels"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HOUSE_2022 = {
    1: 156400,
    2: 156315,
    3: 156722,
    4: 156380,
    5: 156345,
    6: 156348,
    7: 156343,
    8: 156358,
    9: 156364,
    10: 156422,
    11: 156424,
}
RELABEL = {(2, "Franklin County"): "Franklin City", (4, "Richmond County"): "Richmond City"}
ENR = "https://enr.elections.virginia.gov/cdn/results/Virginia/export-2024NovemberGeneral.json"
ENR_LANDING = "https://enr.elections.virginia.gov/results/public/Virginia/elections/2024NovemberGeneral"
ENR_TITLE = "Official results, November 5, 2024 general election"
GROUPS = {"election day": 1, "early voting": 2, "mailed absentee": 3, "post-election": 3, "provisional": 5}


def fetch() -> None:
    for contest in HOUSE_2022.values():
        raw.fetch(SOURCE, va_precincts.TABLE.format(contest=contest), suffix=".csv")
    raw.fetch(SOURCE, ENR, suffix=".json")


def key(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower().replace("&", "and").removesuffix(", va"))


def channel(precinct: str) -> int:
    name = precinct.strip().lower()
    if "provisional" in name:
        return 5
    if re.match(r"^(##)?ev\b", name):
        return 2
    if re.match(r"^((##)?pe|ab)\b", name):
        return 3
    if re.match(r"^\d", name):
        return 1
    raise ValueError(f"unknown precinct kind: {precinct}")


def contest(name: str) -> tuple[str, int | None] | None:
    if name == "President and Vice President":
        return "president", None
    if name == "Member, United States Senate":
        return "senate", None
    if matched := re.fullmatch(r"Member, House of Representatives \((\d+)\w\w District\)", name):
        return "house", int(matched[1])
    return None


def parse() -> None:
    with connect() as conn:
        counties = {
            key(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'official'
                   where u.scheme_code = 'us.county' and u.code like '51%%'"""
            ).fetchall()
        }
    records = []
    for district, number in HOUSE_2022.items():
        table = list(
            csv.reader(
                io.StringIO(
                    raw.cached(SOURCE, va_precincts.TABLE.format(contest=number)).path.read_text(encoding="utf-8-sig")
                )
            )
        )
        names = table[0][2:]
        groups: list[tuple[list[str], list[list[str]]]] = []
        for row in table[2:]:
            if row[0] == "Locality":
                groups.append((row, []))
            elif row[0] == "Precinct":
                groups[-1][1].append(row)
        for locality, precincts in groups:
            total = [int(value or 0) for value in locality[2:]]
            sums = [sum(int(row[2 + index] or 0) for row in precincts) for index in range(len(names))]
            cast = names.index("Total Votes Cast")
            tied = sums == total or sums[cast] == total[cast] == sum(sums[:cast])
            if not tied:
                print(f"district {district}: {locality[1]} stored as a total, its precincts do not sum")
            for row in precincts if tied else [locality]:
                for name, value in zip(names, row[2:], strict=True):
                    records.append(
                        {
                            "day": date(2022, 11, 8),
                            "chamber": "house",
                            "district": district,
                            "locality": RELABEL.get((district, locality[1]), locality[1]),
                            "choice": name,
                            "channel": channel(row[1]) if tied else 0,
                            "votes": int(value or 0),
                        }
                    )
    export = json.loads(raw.cached(SOURCE, ENR).path.read_bytes())
    for local in export["localResults"]:
        for item in local["ballotItems"]:
            found = contest(item["name"])
            if found is None:
                continue
            for option in item["ballotOptions"]:
                splits = option["groupResults"] or []
                if sum(group["voteCount"] for group in splits) != option["voteCount"]:
                    raise ValueError(f"{local['name']} {item['name']} {option['name']}: groups do not sum")
                for group in splits:
                    records.append(
                        {
                            "day": date(2024, 11, 5),
                            "chamber": found[0],
                            "district": found[1],
                            "locality": local["name"],
                            "choice": option["name"],
                            "channel": GROUPS[group["groupName"].lower()],
                            "votes": int(group["voteCount"] or 0),
                        }
                    )
    frame = pl.DataFrame(records, infer_schema_length=None).with_columns(
        code=pl.col("locality").map_elements(lambda text: counties.get(key(text)), return_dtype=pl.String),
        channel=pl.col("channel").cast(pl.Int16),
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched localities: {lost['locality'].unique().head(5).to_list()}")
    frame = (
        frame.with_columns(
            candidate=pl.col("choice")
            .str.to_uppercase()
            .str.replace(r",\s*(JR|SR|II|III|IV)\.?$", "")
            .str.strip_chars()
        )
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("day", "chamber", "district", "code", "candidate", "measure", "channel")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            district=pl.col("district").cast(pl.Int16),
            special=pl.lit(False),
            state=pl.lit("VA"),
            kind=pl.lit("county"),
        )
        .with_columns(source=pl.concat_str(pl.lit("VA"), pl.col("year").cast(pl.String)))
        .sort("day", "chamber", "district", "code", "candidate", "channel")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "channels.parquet")
    print(frame.group_by("year", "channel").agg(pl.col("votes").sum()).sort("year", "channel").rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "channels.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virginia's 2022 and 2024 general elections by county or city and vote type (election day, early, mail, and provisional), from the Department of Elections' precinct tables (2022) and certified results export (2024)",
            polity="US-VA",
        )
        database = cs.source(va_precincts.PUBLISHER, va_precincts.TITLE)
        if database in cs.minted:
            raise ValueError("no Virginia Elections Database source")
        enr = cs.source(va_precincts.PUBLISHER, ENR_TITLE)
        if enr in cs.minted:
            cs.add(
                "source",
                id=enr,
                kind="official_body",
                publisher=va_precincts.PUBLISHER,
                title=ENR_TITLE,
                url=ENR_LANDING,
                license="Public record",
            )
        for number in HOUSE_2022.values():
            cs.artifact(raw.cached(SOURCE, va_precincts.TABLE.format(contest=number)), database)
        cs.artifact(raw.cached(SOURCE, ENR), enr)
        medsl.attach(cs, conn, frame, {"VA2022": database, "VA2024": enr})
        return cs.write()
