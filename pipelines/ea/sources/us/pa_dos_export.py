import csv
import re
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.pa_dos_export"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-034"
URL = "https://www.electionreturns.pa.gov/Home/ReportCenter"
PUBLISHER = "Pennsylvania Department of State"
TITLE = "Election returns by county: {name}"
FILES = {
    "Official_10222026095800PM.CSV": (date(2022, 5, 17), "2022 general primary, U.S. House"),
    "Official_10222026095856PM.CSV": (date(2016, 4, 26), "2016 presidential primary, U.S. House"),
    "Official_10222026095939PM.CSV": (date(2016, 11, 8), "2016 special election, 2nd Congressional District"),
    "Official_10222026100020PM.CSV": (date(2018, 11, 6), "2018 special election, 7th Congressional District"),
    "Official_10222026100049PM.CSV": (date(2018, 11, 6), "2018 special election, 15th Congressional District"),
    "Official_10222026100110PM.CSV": (date(2002, 5, 21), "2002 general primary, U.S. House"),
}


def fetch() -> None:
    for name in FILES:
        try:
            raw.cached(SOURCE, f"{URL}#{name}")
        except LookupError:
            raw.store(
                SOURCE,
                f"{URL}#{name}",
                (INBOX / name).read_bytes(),
                suffix=".csv",
                retrieved_at=datetime.now(UTC),
                media_type="text/csv",
            )


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["PA"]
    rows = []
    for name, (day, _) in FILES.items():
        special = "special" in name or "Special" in FILES[name][1] or "special" in FILES[name][1]
        text = raw.cached(SOURCE, f"{URL}#{name}").path.read_text(encoding="utf-8-sig")
        for line in csv.DictReader(text.splitlines()):
            if line["Office Name"] != "Representative in Congress":
                continue
            district = int(re.match(r"(\d+)", line["District Name"])[1])
            party = line["Party Name"]
            primary = "primary" in FILES[name][1]
            chamber = f"house:{'dp' if party == 'Democratic' else 'rp'}" if primary else "house"
            rows.append(
                (
                    day,
                    chamber,
                    district,
                    special,
                    line["County Name"],
                    line["Candidate Name"].strip().upper(),
                    int(line["Votes"].replace(",", "")),
                )
            )
    frame = pl.DataFrame(
        rows, schema=["day", "chamber", "district", "special", "county", "candidate", "votes"], orient="row"
    ).with_columns(
        code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips, default=None)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    with connect() as conn:
        covered = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x
                   where x.scheme = 'us.contest' and x.value ~ ':PA:house:'
                     and exists (select from result_set rs join result_value v on v.result_set_k = rs.k
                                 join reporting_unit u on u.k = v.unit_k
                                 where rs.contest_id = x.entity_id and rs.status = 'active'
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.precinct'))"""
            )
        }
    keyed = frame.with_columns(
        key=pl.format(
            "{}:PA:{}:{}{}",
            pl.col("day"),
            pl.when(pl.col("chamber") == "house").then(pl.lit("house")).otherwise(pl.lit("house")),
            pl.col("district"),
            pl.when(pl.col("chamber") == "house")
            .then(pl.when(pl.col("special")).then(pl.lit(":special")).otherwise(pl.lit("")))
            .otherwise(pl.concat_str(pl.lit(":"), pl.col("chamber").str.slice(6))),
        )
    )
    gaps = keyed.filter(~pl.col("key").is_in(list(covered)))
    PARSED.mkdir(parents=True, exist_ok=True)
    gaps.select(
        state=pl.lit("PA"),
        day=pl.col("day"),
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber=pl.col("chamber"),
        district=pl.col("district").cast(pl.Int16),
        special=pl.col("special"),
        code=pl.col("code"),
        candidate=pl.col("candidate"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.contains("WRITE"),
        votes=pl.col("votes").cast(pl.Int64),
        kind=pl.lit("county"),
        source=pl.lit("PA"),
    ).write_parquet(PARSED / "counties.parquet")
    print(gaps.group_by("key").agg(pl.len()).sort("key").to_dicts())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Pennsylvania's U.S. House primaries and special elections that lack county results, by county from the Department of State's election returns exports",
            polity="US-PA",
        )
        source = cs.source(PUBLISHER, TITLE.format(name="U.S. House primaries and specials"))
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE.format(name="U.S. House primaries and specials"),
            url=URL,
            license="Public record",
        )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, f"{URL}#{name}"), source)
        medsl.attach(cs, conn, frame, {"PA": source}, extend=True)
        return cs.write()
