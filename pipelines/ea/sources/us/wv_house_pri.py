import csv
import io
import re
import time
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wv_house_pri"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://apps.sos.wv.gov/elections/results/"
URL = SITE + "readfile.aspx?eid={}&type=StateCountyTotals&format=csv"
PUBLISHER = "West Virginia Secretary of State"
TITLE = "Election results by county, 2008–2014"
ELECTIONS = {3: date(2008, 5, 13), 5: date(2010, 5, 11), 8: date(2012, 5, 8), 14: date(2014, 5, 13)}
PARTIES = {"D": "democratic", "R": "republican"}


def fetch() -> None:
    for election in ELECTIONS:
        try:
            raw.cached(SOURCE, URL.format(election))
        except LookupError:
            raw.fetch(SOURCE, URL.format(election), suffix=".csv")
            time.sleep(2)


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
    rows: list[dict[str, object]] = []
    for election, day in ELECTIONS.items():
        text = raw.cached(SOURCE, URL.format(election)).path.read_text(encoding="utf-8-sig", errors="replace")
        for line in csv.DictReader(io.StringIO(text)):
            if line["OfficeCode"] != "CA" or line["PartyCode"] not in PARTIES or line["CountyName"] == "":
                continue
            rows.append(
                {
                    "year": day.year,
                    "chamber": f"house:{PARTIES[line['PartyCode']]}",
                    "day": day,
                    "special": False,
                    "state": "WV",
                    "district": int(line["District"]),
                    "code": counties[re.sub(r"[^a-z]", "", line["CountyName"].lower())],
                    "candidate": re.sub(r"\s+", " ", line["Name"]).strip().upper(),
                    "official": line["OfficialResults"] == "Yes",
                    "votes": int(line["Votes"]),
                }
            )
    frame = (
        pl.DataFrame(rows)
        .filter(
            pl.col("official")
            == pl.col("official").max().over("year", "chamber", "district", "code")
        )
        .drop("official")
        .with_columns(measure=pl.lit(1, pl.Int16), writein=pl.lit(False), kind=pl.lit("county"), source=pl.lit("WV"))
    )
    doubled = frame.group_by("year", "chamber", "district", "code", "candidate").len().filter(pl.col("len") > 1)
    if doubled.height:
        raise ValueError(f"repeated lines: {doubled}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("year", "chamber", "district").agg(pl.col("votes").sum()).sort("year", "chamber", "district"))


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="West Virginia's 2008–2014 U.S. House primaries by county",
            polity="US-WV",
        )
        source = cs.source(PUBLISHER, TITLE)
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
