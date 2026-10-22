import json
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ut_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://electionresults.utah.gov/cdn/results/utah/"
FILES = {
    date(2024, 6, 25): "export-primary06252024.json",
}
PARTIES = {"Republican": "republican", "Democratic": "democratic"}
CONTEST = re.compile(r"^(Republican|Democratic) For (?:US (Senate)|US House (\d))\s*$")
PUBLISHER = "Utah Lieutenant Governor's Office of Elections"
TITLE = "Utah Election Results"


def fetch() -> None:
    with httpx.Client(timeout=300, follow_redirects=True) as client:
        for name in FILES.values():
            response = client.get(BASE + name)
            response.raise_for_status()
            raw.store(
                SOURCE,
                BASE + name,
                response.content,
                suffix=".json",
                retrieved_at=datetime.now(UTC),
                media_type="application/json",
            )


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '49%%' order by u.code"""
            ).fetchall()
        }
    rows = []
    for day, name in FILES.items():
        data = json.loads(raw.cached(SOURCE, BASE + name).path.read_bytes())
        for item in data["results"]["ballotItems"]:
            match = CONTEST.match(item["name"])
            if match is None:
                continue
            party, senate, district = match.groups()
            kind = "senate" if senate else "house"
            counted = [0] * len(item["ballotOptions"])
            for county in data["localResults"]:
                local = [b for b in county["ballotItems"] if b["name"] == item["name"]]
                if len(local) != 1:
                    continue
                code = fips[re.sub(r"[^a-z]", "", county["name"].lower().removesuffix("county"))]
                for i, (option, state) in enumerate(zip(local[0]["ballotOptions"], item["ballotOptions"], strict=True)):
                    counted[i] += option["voteCount"]
                    rows.append(
                        (
                            day.year,
                            f"{kind}:{PARTIES[party]}",
                            day,
                            False,
                            "UT",
                            int(district) if district else None,
                            code,
                            state["name"].strip(),
                            1,
                            False,
                            option["voteCount"],
                        )
                    )
            for total, state in zip(counted, item["ballotOptions"], strict=True):
                if total != state["voteCount"]:
                    raise ValueError(f"{day} {item['name']} {state['name']}: counties sum to {total}, state {state['voteCount']}")
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
    ).with_columns(kind=pl.lit("county"), source=pl.lit("UT")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Utah's 2024 U.S. Senate and U.S. House primaries by county",
            polity="US-UT",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://electionresults.utah.gov/",
            license="Public record",
        )
        for name in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"UT": source}, strict=True)
        return cs.write()
