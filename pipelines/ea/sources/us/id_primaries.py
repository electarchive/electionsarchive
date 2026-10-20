import csv
import io
import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.id_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://canvass.sos.idaho.gov/eng/contests/download/{id}/show_granularity_dt_id:3/.csv"
CONTESTS = {
    5729: (date(2020, 3, 10), "president", None, "democratic"),
    5730: (date(2020, 3, 10), "president", None, "republican"),
    5731: (date(2020, 3, 10), "president", None, "constitution"),
    5733: (date(2020, 6, 2), "senate", None, "democratic"),
    5734: (date(2020, 6, 2), "senate", None, "republican"),
    5735: (date(2020, 6, 2), "house", 1, "democratic"),
    5736: (date(2020, 6, 2), "house", 1, "republican"),
    5737: (date(2020, 6, 2), "house", 2, "democratic"),
    5738: (date(2020, 6, 2), "house", 2, "republican"),
}
PUBLISHER = "Idaho Secretary of State"
TITLE = "State of Idaho Elections Database"


def fetch() -> None:
    for contest in CONTESTS:
        raw.fetch(SOURCE, BASE.format(id=contest), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '16%%' order by u.code"""
            ).fetchall()
        }
    rows = []
    for contest, (day, kind, district, party) in CONTESTS.items():
        table = list(csv.reader(io.StringIO(raw.cached(SOURCE, BASE.format(id=contest)).path.read_text(encoding="utf-8-sig"))))
        names = [" ".join(cell.split("\n")[0].split()).upper() for cell in table[0][1:-1]]
        for row in table[1:]:
            if not row:
                continue
            key = re.sub(r"[^a-z]", "", row[0].lower())
            if key not in fips:
                continue
            for candidate, cell in zip(names, row[1:-1], strict=True):
                rows.append(
                    (
                        day.year,
                        f"{kind}:{party}",
                        day,
                        False,
                        "ID",
                        district,
                        fips[key],
                        candidate,
                        1,
                        candidate.startswith("WRITE"),
                        int(cell.replace(",", "") or 0),
                    )
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(kind=pl.lit("county"), source=pl.lit("ID")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Idaho's 2020 presidential, U.S. Senate, and U.S. House primaries by county",
            polity="US-ID",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://canvass.sos.idaho.gov/",
            license="Public record",
        )
        for contest in CONTESTS:
            cs.artifact(raw.cached(SOURCE, BASE.format(id=contest)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"ID": source}, strict=True)
        return cs.write()
