import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ga_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://results.sos.ga.gov/results/public/api/elections/Georgia/"
ELECTIONS = {
    "2022MayGenPri": date(2022, 5, 24),
    "2022JunGenPriRun": date(2022, 6, 21),
}
PUBLISHER = "Georgia Secretary of State"
TITLE = "Georgia Election Results: county results"
PARTIES = {"REP": "republican", "DEM": "democratic"}
RACES = [
    re.compile(r"^US Senate - (?P<party>Rep|Dem)$"),
    re.compile(r"^(?P<party>REP|DEM) - US Senate$"),
    re.compile(r"^US House of Representatives - District (?P<district>\d+) - (?P<party>Rep|Dem)$"),
    re.compile(r"^(?P<party>REP|DEM) - US House Dist (?P<district>\d+)$"),
]


def listing(election: str) -> list[dict]:
    return json.loads(raw.fetch(SOURCE, f"{API}{election}/ballot-items", suffix=".json").path.read_bytes())["data"]


def fetch() -> None:
    for election in ELECTIONS:
        for item in listing(election):
            if any(race.match(item["name"][0]["text"]) for race in RACES):
                raw.fetch(SOURCE, f"{API}{election}/ballot-items/{item['id']}", suffix=".json")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '13%%'"""
            ).fetchall()
        }
    rows = []
    for election, day in ELECTIONS.items():
        for item in listing(election):
            name = item["name"][0]["text"]
            match = next((m for race in RACES if (m := race.match(name))), None)
            if match is None:
                continue
            fields = match.groupdict()
            party = PARTIES[fields["party"].upper()]
            office = "senate" if "Senate" in name else "house"
            detail = json.loads(raw.cached(SOURCE, f"{API}{election}/ballot-items/{item['id']}").path.read_bytes())
            for county in detail["breakdownResults"]:
                locality = county["locality"]["name"][0]["text"]
                code = counties.get(re.sub(r"[^a-z]", "", locality.lower().removesuffix(" county")))
                if code is None:
                    raise ValueError(f"unmatched county: {locality}")
                for option in county["ballotOptions"]:
                    rows.append(
                        {
                            "year": day.year,
                            "chamber": f"{office}:{party}",
                            "day": day,
                            "special": False,
                            "state": "GA",
                            "district": int(fields["district"]) if fields.get("district") else None,
                            "code": code,
                            "candidate": " ".join(option["name"][0]["text"].split()).upper(),
                            "votes": int(option["voteCount"]),
                            "kind": "county",
                        }
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows, schema_overrides={"district": pl.Int16, "year": pl.Int32})
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "kind", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(source=pl.lit("GA"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia's 2022 U.S. Senate and House primaries and runoffs by county",
            polity="US-GA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.sos.ga.gov/results/public/Georgia",
            license="Public record",
        )
        for election in ELECTIONS:
            for item in listing(election):
                if any(race.match(item["name"][0]["text"]) for race in RACES):
                    cs.artifact(raw.cached(SOURCE, f"{API}{election}/ballot-items/{item['id']}"), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"GA": source}, strict=True)
        return cs.write()
