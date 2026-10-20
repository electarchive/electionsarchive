import itertools
import json
import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ca_sov_primaries, medsl
from ea.store import DATA, connect

SOURCE = "us.ca_senate94"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
READS = PARSED / "reads"
URL = f"{ca_sov_primaries.SITE}1994-primary/us-senator.pdf"
DAY = "1994-06-07"
PARTIES = {
    "dem": "democratic",
    "rep": "republican",
    "grn": "green",
    "green": "green",
    "lib": "libertarian",
    "p&f": "peace-and-freedom",
    "pf": "peace-and-freedom",
    "ai": "american-independent",
    "a.i.": "american-independent",
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def merged(read: str) -> dict[tuple[str, str], dict[str, int | None]]:
    columns: dict[tuple[str, str], dict[str, int | None]] = {}
    for path in sorted(READS.glob(f"{read}-*.json")):
        for column in json.loads(path.read_text())["columns"]:
            name = re.sub(r"[^a-z]", "", column["name"].lower())
            if name.startswith(("votesnot", "noneof")):
                continue
            key = (PARTIES[column["party"].lower().split("-")[0].strip()], name[:6])
            columns.setdefault(key, {}).update(column["votes"])
    return columns


def parse() -> None:
    first, second = merged("A"), merged("B")
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%%'"""
            ).fetchall()
        }
        official: dict[tuple[str, str], tuple[str, int]] = {}
        for party in set(party for party, _ in first):
            for label, votes in conn.execute(
                """select o.ballot_label, sum(v.value)::bigint from external_id e
                   join contest_option o on o.contest_id = e.entity_id and o.kind = 'candidate'
                   join result_set rs on rs.contest_id = e.entity_id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                   where e.scheme = 'us.contest' and e.value = %s group by 1""",
                [f"{DAY}:CA:senate:{party}"],
            ).fetchall():
                official[
                    (party, re.sub(r"[^a-z]", "", label.lower().split(" ", 1)[0] + label.lower().split(" ")[-1])[:6])
                ] = (
                    label,
                    int(votes),
                )
    print(sorted(official))
    rows: list[dict[str, object]] = []
    for key, cells in first.items():
        other = second.get(key, {})
        label = next(
            (
                (label, votes)
                for (party, short), (label, votes) in official.items()
                if party == key[0] and key[1][:3] in short
            ),
            None,
        )
        if label is None:
            print(f"{key}: no FEC option")
            continue
        counties = [county for county in cells if county != "TOTALS"]
        differing = [county for county in counties if cells[county] != other.get(county)]
        found: list[dict[str, int]] = []
        for picks in itertools.product(*[(cells[c], other.get(c)) for c in differing]):
            if None in picks:
                continue
            chosen = {c: cells[c] for c in counties if c not in differing} | dict(zip(differing, picks, strict=True))
            if sum(chosen.values()) in (
                label[1],
                cells.get("TOTALS") if cells.get("TOTALS") == other.get("TOTALS") else None,
            ):
                found.append(chosen)
        if len(found) != 1:
            print(
                f"{key} {label[0]}: {len(found)} readings match the FEC total {label[1]}; {len(differing)} differing: {differing}"
            )
            continue
        rows.extend(
            {
                "year": 1994,
                "chamber": f"senate:{key[0]}",
                "day": __import__("datetime").date(1994, 6, 7),
                "special": False,
                "state": "CA",
                "district": None,
                "code": fips[re.sub(r"[^a-z]", "", county.lower())],
                "candidate": label[0].upper(),
                "party": key[0],
                "measure": 1,
                "writein": False,
                "votes": votes,
                "kind": "county",
                "source": "CA",
            }
            for county, votes in found[0].items()
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema_overrides={"year": pl.Int32, "district": pl.Int16, "measure": pl.Int16})
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("chamber", "candidate").agg(pl.col("votes").sum()))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's 1994 U.S. Senate primaries by county from the Statement of Vote",
            polity="US-CA",
        )
        source = cs.source(ca_sov_primaries.PUBLISHER, ca_sov_primaries.TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=ca_sov_primaries.PUBLISHER,
            title=ca_sov_primaries.TITLE,
            url=URL,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, frame, {"CA": source}, strict=True)
        return cs.write()
