import itertools
import json
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nj_pri_scans"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
READS = PARSED / "reads"
SITE = "https://www.nj.gov/state/elections/assets/pdf/election-results/"
FILES = {
    "94h": ("1994/1994-primary-election-results-house.pdf", date(1994, 6, 7)),
    "94s": ("1994/1994-primary-election-results-us-senate.pdf", date(1994, 6, 7)),
    "96h": ("1996/1996-primary-election-results-house.pdf", date(1996, 6, 4)),
    "96s": ("1996/1996-primary-election-results-us-senate.pdf", date(1996, 6, 4)),
    "98h": ("1998/1998-pri-elect-results-us-house.pdf", date(1998, 6, 2)),
}
PARTIES = {"rep": "republican", "dem": "democratic", "lib": "libertarian", "grn": "green", "ind": "independent"}
PUBLISHER = "New Jersey Division of Elections"
TITLE = "Official List: Primary Election Returns"


def fetch() -> None:
    for path, _ in FILES.values():
        raw.fetch(SOURCE, SITE + path, suffix=".pdf")


def tables(read: str) -> dict[tuple[str, int | None, str], dict[str, dict[str, object]]]:
    found: dict[tuple[str, int | None, str], dict[str, dict[str, object]]] = {}
    for path in sorted((READS / read).glob("*.json")):
        data = json.loads(path.read_text())
        for table in data.get("tables", [data] if "rows" in data else []):
            for row in table["rows"]:
                name = row["name"].strip().upper()
                if name.startswith("NO NOMINATION") or not any(row["votes"]):
                    continue
                key = (path.stem[:3], table.get("district"), PARTIES[row["party"].lower()[:3]])
                found.setdefault(key, {})[name] = {
                    "counties": [re.sub(r"[^a-z]", "", c.lower()) for c in table["counties"]],
                    "votes": row["votes"],
                    "total": row["total"],
                    "label": row["name"].strip(),
                }
    return found


def parse() -> None:
    first, second = tables("A"), tables("B")
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '34%%'"""
            ).fetchall()
        }
    rows: list[dict[str, object]] = []
    for key, candidates in first.items():
        for name, one in candidates.items():
            two = second.get(key, {}).get(name)
            if two is None or one["counties"] != two["counties"]:
                print(f"{key} {name}: the second read has no matching row")
                continue
            totals = {one["total"], two["total"]}
            differing = [i for i, (a, b) in enumerate(zip(one["votes"], two["votes"], strict=True)) if a != b]
            found = set()
            for picks in itertools.product(*[(one["votes"][i], two["votes"][i]) for i in differing]):
                votes = list(one["votes"])
                for i, value in zip(differing, picks, strict=True):
                    votes[i] = value
                if sum(votes) in totals:
                    found.add(tuple(votes))
            if len(found) != 1 or (len(totals) == 2 and sum(next(iter(found))) not in totals):
                print(f"{key} {name}: {len(found)} readings match the printed total {totals}; differing cells {differing}")
                continue
            if len(totals) == 2:
                print(f"{key} {name}: the reads print totals {totals}; kept the one the counties sum to")
            votes = next(iter(found))
            day = {"94": date(1994, 6, 7), "96": date(1996, 6, 4), "98": date(1998, 6, 2)}[key[0][:2]]
            rows.extend(
                {
                    "year": day.year,
                    "chamber": f"{'house' if key[0][2] == 'h' else 'senate'}:{key[2]}",
                    "day": day,
                    "special": False,
                    "state": "NJ",
                    "district": key[1],
                    "code": fips[county],
                    "candidate": name,
                    "party": key[2],
                    "measure": 1,
                    "writein": False,
                    "votes": value,
                    "kind": "county",
                    "source": "NJ",
                }
                for county, value in zip(one["counties"], votes, strict=True)
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema_overrides={"year": pl.Int32, "district": pl.Int16, "measure": pl.Int16})
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("year", "chamber").agg(pl.col("district").n_unique()).sort("year", "chamber"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's House and Senate primaries of 1994, 1996, and 1998 by county from the Division of Elections' official lists",
            polity="US-NJ",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=f"{SITE}../../election-information-results.shtml",
            license="Public record",
        )
        for path, _ in FILES.values():
            cs.artifact(raw.cached(SOURCE, SITE + path), source)
        medsl.attach(cs, conn, frame, {"NJ": source}, strict=True)
        return cs.write()
