import csv
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ak_leg import KINDS
from ea.store import connect

SOURCE = "us.ak_enr"
VERSION = "1"
DAY = date(2024, 11, 5)
URL = "https://www.elections.alaska.gov/results/24GENR/ENRbyPrecinct.csv"
PUBLISHER = "Alaska Division of Elections"
TITLE = "2024 General Election results by precinct"


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        body = subprocess.run(
            ["curl", "-sfkL", "--compressed", "-A", raw.USER_AGENT, URL], capture_output=True, check=True
        ).stdout
        raw.store(SOURCE, URL, body, suffix=".csv", retrieved_at=datetime.now(UTC), media_type="text/csv")


def emit() -> Path:
    rows = []
    for row in csv.DictReader(raw.cached(SOURCE, URL).path.read_text(encoding="utf-8-sig").splitlines()):
        place, name = row["Precinct_name"].strip(), row["candidate_name"].strip()
        if row["Contest_title"] != "U.S. President / Vice President" or not row["total_votes"]:
            continue
        if precinct := re.match(r"(\d\d-\d\d\d) ", place):
            code, kind = f"{DAY.year}:AK:{precinct[1]}", "precinct"
        elif aggregate := re.fullmatch(r"District (\d+)\s*- (.*)", place):
            code, kind = f"AK:{DAY.year}:{int(aggregate[1]):02}:{KINDS[aggregate[2]]}", "aggregate"
        elif place.startswith("HD99"):
            code, kind = "AK:99", "aggregate"
        else:
            raise ValueError(f"unknown place {place}")
        writein = name.lower().startswith("write")
        rows.append(
            (code, kind, "WRITE-INS" if writein else name.split("/")[0].upper(), writein, int(row["total_votes"]))
        )
    frame = pl.DataFrame(
        rows,
        schema={"code": pl.String, "kind": pl.String, "candidate": pl.String, "writein": pl.Boolean, "votes": pl.Int64},
        orient="row",
    ).select(
        year=pl.lit(DAY.year, pl.Int32),
        chamber=pl.lit("president"),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("AK"),
        district=pl.lit(None, pl.Int16),
        code="code",
        kind="kind",
        candidate="candidate",
        measure=pl.lit(1, pl.Int16),
        writein="writein",
        votes="votes",
        source=pl.lit("AK"),
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska's 2024 presidential election by precinct, with absentee, question, early, and overseas votes by House district",
            polity="US-AK",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.elections.alaska.gov/election-results/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(
            cs,
            conn,
            frame,
            {"AK": source},
            grain="precinct",
            strict=True,
            authoritative="Source: the Division of Elections' official results by precinct.",
        )
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        state, parent = conn.execute(
            """select p.id, u.id from reporting_unit u join polity p on p.id = u.polity_id
               where u.scheme_code = 'us.state' and p.iso_code = 'US-AK'"""
        ).fetchone()
        for code, kind in frame.select("code", "kind").unique().iter_rows():
            scheme = "us.precinct" if kind == "precinct" else "us.aggregate"
            unit = cs.unit(scheme, code)
            if str(unit) not in used or unit not in cs.minted:
                continue
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=state,
                scheme_code=scheme,
                code=code,
                parent_id=parent,
                area_relation="within",
                area_id=cs.area("us.sldl", f"AK:{int(code.split(':')[2][:2])}"),
                kind="standard" if kind == "precinct" else "aggregate",
                valid_from=DAY,
                valid_to=DAY + timedelta(days=1),
            )
        return cs.write()
