import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.wv_sos import PUBLISHER, SITE, URL
from ea.store import DATA, connect

SOURCE = "us.wv_leg"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "Election results by county: State Senate and House of Delegates"
ELECTIONS = {4: date(2008, 11, 4), 6: date(2010, 11, 2), 13: date(2012, 11, 6)}
OFFICES = {"KA": "senate", "LA": "house-of-delegates"}


def fetch() -> None:
    for election in ELECTIONS:
        raw.fetch(SOURCE, URL.format(election), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
        seats: dict[date, dict[tuple[str, str], set[str]]] = {}
        for value, label in conn.execute(
            """select x.value, o.ballot_label from external_id x
               left join contest_option o on o.contest_id = x.entity_id and o.kind = 'candidate'
               where x.scheme = 'us.contest' and x.value ~ '^20(08|10|12)-11-..:WV:senate:[0-9]+:[0-9]:g$'"""
        ).fetchall():
            held, _, _, district, seat, _ = value.split(":")
            seats.setdefault(date.fromisoformat(held), {}).setdefault((district, seat), set()).update(
                [re.sub(r",? (Jr|Sr|II|III)\.?$", "", label).split()[-1].upper()] if label else []
            )
    frames = []
    for election, day in ELECTIONS.items():
        frame = pl.read_csv(raw.cached(SOURCE, URL.format(election)).path, infer_schema=False)
        frames.append(
            frame.filter(pl.col("OfficeCode").is_in(list(OFFICES)) & (pl.col("CountyName") != "")).with_columns(
                day=pl.lit(day)
            )
        )
    frame = pl.concat(frames)
    frame = frame.filter(
        pl.col("OfficialResults") == pl.col("OfficialResults").max().over("day", "OfficeCode", "District", "CountyName")
    )
    rows = []
    for day, office, district, county, name, votes in frame.select(
        "day", "OfficeCode", "District", "CountyName", "Name", "Votes"
    ).iter_rows():
        if office == "KA":
            found = {
                key: surnames for key, surnames in seats.get(day, {}).items() if key[0] == str(int(district))
            } or seats.get(day, {})
            if len(found) > 1:
                surname = re.sub(r",? (Jr|Sr|II|III)\.?$", "", re.sub(r"\s+", " ", name).strip()).split()[-1].upper()
                found = {key: surnames for key, surnames in found.items() if surname in surnames}
            if len(found) != 1:
                continue
            chamber = ":".join(["senate", *next(iter(found)), "g"])
        else:
            chamber = f"house-of-delegates:{int(district)}:g"
        rows.append(
            (
                day,
                chamber,
                counties[re.sub(r"[^a-z]", "", county.lower())],
                re.sub(r"\s+", " ", name).strip(),
                int(votes),
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    parsed = pl.DataFrame(
        rows,
        schema={"day": pl.Date, "chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    )
    print(
        parsed.group_by("day", pl.col("chamber").str.split(":").list.first().alias("office")).agg(
            pl.col("chamber").n_unique()
        )
    )
    parsed.group_by("day", "chamber", "code", "candidate").agg(pl.col("votes").sum()).with_columns(
        candidate=pl.col("candidate").str.to_uppercase().str.replace(r",?\s+(JR|SR|II|III|IV)\.?$", ""),
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("WV"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("WV"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="West Virginia's State Senate and House of Delegates generals by county, 2008-2012 (Secretary of State)",
            polity="US-WV",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=SITE,
                license="Public record",
            )
        for election in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, URL.format(election)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WV": source}, strict=True)
        return cs.write()
