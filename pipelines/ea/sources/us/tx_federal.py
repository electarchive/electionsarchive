import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.tx_federal"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://results.texas-election.com/static/data/election/"
INBOX = DATA / "inbox" / "manual" / "MF-040"
PUBLISHER = "Texas Secretary of State"
TITLE = "Texas Election Results: official county results"
ELECTIONS = {
    47010: (date(2022, 3, 1), ":republican", False, None),
    47011: (date(2022, 3, 1), ":democratic", False, None),
    47292: (date(2022, 5, 24), ":republican", False, None),
    47293: (date(2022, 5, 24), ":democratic", False, None),
    44376: (date(2020, 7, 14), ":republican", False, None),
    44387: (date(2020, 7, 14), ":democratic", False, None),
    47009: (date(2022, 11, 8), "", False, {14}),
    45820: (date(2021, 5, 1), "", True, None),
    46698: (date(2021, 7, 27), ":runoff", True, None),
    47689: (date(2022, 6, 14), "", True, None),
}


def fetch() -> None:
    for election in ELECTIONS:
        url = f"{SITE}{election}/County.json"
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                (INBOX / f"tx_{election}_County.json").read_bytes(),
                suffix=".json",
                retrieved_at=datetime(2026, 10, 23, tzinfo=UTC),
                media_type="application/json",
            )


def parse() -> None:
    rows = []
    for election, (day, suffix, special, districts) in ELECTIONS.items():
        counties = json.loads(raw.cached(SOURCE, f"{SITE}{election}/County.json").path.read_bytes())
        for fips, county in counties.items():
            for race in county["Races"].values():
                name = " ".join(race["ON"].split())
                if name == "U. S. SENATOR":
                    chamber, district = "senate", None
                elif match := re.fullmatch(r"U\. S\. REPRESENTATIVE DISTRICT (\d+)(?: - UNEXPIRED TERM)?", name):
                    chamber, district = "house", int(match.group(1))
                else:
                    continue
                if districts is not None and district not in districts:
                    continue
                for candidate in race["C"].values():
                    rows.append(
                        {
                            "year": day.year,
                            "chamber": f"{chamber}{suffix}",
                            "day": day,
                            "special": special,
                            "state": "TX",
                            "district": district,
                            "code": fips,
                            "candidate": " ".join(candidate["N"].split()).upper(),
                            "votes": int(candidate["V"]),
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
        .with_columns(source=pl.lit("TX"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas's 2020 and 2022 U.S. Senate and House primary runoffs, 2022 primaries, the 2022 TX-14 general, and the 2021 TX-6 and 2022 TX-34 specials by county; the 2021 and 2022 files were saved from the results site by a person in a browser",
            polity="US-TX",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.texas-election.com/",
            license="Public record",
        )
        for election in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, f"{SITE}{election}/County.json"), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"TX": source}, strict=True)
        return cs.write()
