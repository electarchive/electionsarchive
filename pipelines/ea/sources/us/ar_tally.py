import json
import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ar_tally"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://results.tally-enr.com/results/arkansas/{}/FullDataFile.json"
CONTESTS = {
    1835: {"U.S. Congress District 2 - REP": ("house:republican", 2)},
    1839: {
        "U.S. President - DEM": ("president:democratic", None),
        "U.S. President - REP": ("president:republican", None),
    },
    1844: {"Governor": ("governor", None)},
}
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Election night reporting full data files"


def fetch() -> None:
    for election in CONTESTS:
        raw.fetch(SOURCE, URL.format(election), suffix=".json")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", name.removesuffix(", AR").removesuffix(" County").lower().replace("st.", "st")): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '05%%' and u.valid @> '2016-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for election, wanted in CONTESTS.items():
        data = json.loads(raw.cached(SOURCE, URL.format(election)).path.read_text())
        day = data["ElectionInfo"]["ElectionDate"][:10]
        for contest in data["ContestData"]:
            if contest["ContestName"] not in wanted:
                continue
            office, district = wanted[contest["ContestName"]]
            counted: dict[tuple[str, str], int] = {}
            for county in contest["Counties"]:
                code = codes[re.sub(r"[^a-z]", "", county["CountyName"].lower())]
                for candidate in county["Candidates"]:
                    key = (code, candidate["Name"])
                    counted[key] = counted.get(key, 0) + candidate["TotalVotes"]
            for candidate in contest["Candidates"]:
                summed = sum(votes for (code, name), votes in counted.items() if name == candidate["Name"])
                if summed != candidate["TotalVotes"]:
                    raise ValueError(
                        f"{day} {contest['ContestName']} {candidate['Name']}: {summed} by county, {candidate['TotalVotes']} reported"
                    )
            for (code, name), votes in counted.items():
                rows.append(
                    (day, office, district, code, re.sub(r"^Congressman\s+", "", name).replace("'", '"'), votes)
                )
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.String,
            "office": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("day", "office", "district", "candidate")
        .agg(pl.col("votes").sum(), pl.len())
        .sort("day", "office")
        .rows()
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas's 2016 Second District Republican primary, 2020 presidential primaries, and 2022 governor by "
            "county from the Secretary of State's election night reporting full data files",
            polity="US-AR",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.sos.arkansas.gov/elections/research/election-results",
                license="Public record",
            )
        for election in CONTESTS:
            cs.artifact(raw.cached(SOURCE, URL.format(election)), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").select(
                year=pl.col("day").str.slice(0, 4).cast(pl.Int32),
                chamber="office",
                day=pl.col("day").str.to_date(),
                special=pl.lit(False),
                state=pl.lit("AR"),
                district="district",
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("AR"),
            ),
            {"AR": source},
            strict=True,
        )
        return cs.write()
