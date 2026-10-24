import json
import re
import time
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import la_sos, medsl
from ea.store import DATA, connect

SOURCE = "us.la_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = [
    date(1992, 10, 3),
    date(1992, 11, 3),
    date(1994, 10, 1),
    date(1996, 9, 21),
    date(1996, 11, 5),
    date(1996, 12, 7),
    date(1998, 11, 3),
    date(2002, 11, 5),
    date(2002, 12, 7),
    date(2004, 11, 2),
    date(2004, 12, 4),
    date(2006, 12, 9),
    date(2008, 5, 3),
    date(2008, 11, 4),
    date(2008, 12, 6),
    date(2012, 12, 8),
    date(2014, 11, 4),
    date(2014, 12, 6),
    date(2016, 3, 5),
    date(2016, 12, 10),
    date(2020, 7, 11),
    date(2020, 12, 5),
]


def names() -> list[str]:
    return ["RacesCandidates_Multiparish.htm", "Votes_Multiparish.htm"] + [
        f"VotesParish/Votes_{parish}.htm" for parish in la_sos.PARISHES
    ]


def fetch() -> None:
    for day in DAYS:
        for name in names():
            for attempt in range(8):
                try:
                    raw.fetch(SOURCE, la_sos.blob(day, name), suffix=".json")
                    break
                except httpx.HTTPStatusError as error:
                    if error.response.status_code == 404:
                        break
                    if error.response.status_code not in (403, 429) or attempt == 7:
                        raise
                    time.sleep(2**attempt)


def read(day: date, name: str) -> dict[str, object]:
    return json.loads(raw.cached(SOURCE, la_sos.blob(day, name)).path.read_text())


def parse() -> None:
    with connect() as conn:
        fips = [
            code
            for (code,) in conn.execute(
                """select u.code from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '22%' order by n.text"""
            ).fetchall()
        ]
    if len(fips) != 64:
        raise ValueError("Louisiana needs 64 parishes")
    rows = []
    for day in DAYS:
        wanted = {}
        for race in la_sos.many((read(day, "RacesCandidates_Multiparish.htm").get("Races") or {}).get("Race")):
            title = str(race["SpecificTitle"])
            if race["GeneralTitle"] == "U.S. Senator":
                wanted[race["ID"]] = ("senate", None, race)
            elif found := re.search(r"(\d+)(?:st|nd|rd|th) Congressional", title):
                wanted[race["ID"]] = ("house", int(found[1]), race)
            elif "President" in title:
                wanted[race["ID"]] = ("president", None, race)
            else:
                continue
            print(day, title)
        described = {
            (race["ID"], choice["ID"]): str(choice["Desc"]).strip()
            for _, _, race in wanted.values()
            for choice in la_sos.many(race["Choice"])
        }
        for index, parish in enumerate(la_sos.PARISHES):
            try:
                listed = read(day, f"VotesParish/Votes_{parish}.htm")
            except LookupError:
                continue
            for race in la_sos.many((listed.get("Races") or {}).get("Race")):
                if race["ID"] not in wanted:
                    continue
                chamber, district, source = wanted[race["ID"]]
                title = str(source["SpecificTitle"])
                party = "democratic" if "Democratic" in title else "republican" if "Republican" in title else None
                if party is not None:
                    chamber = f"{chamber}:{party}"
                for choice in la_sos.many(race["Choice"]):
                    text = described[(race["ID"], choice["ID"])]
                    found = re.match(r"^(.*?)\s*\(([A-Z]+)\)$", text)
                    rows.append(
                        (
                            day.year,
                            chamber,
                            day,
                            False,
                            "LA",
                            district,
                            fips[index],
                            (found[1] if found else text).strip().upper(),
                            1,
                            False,
                            int(choice["VoteTotal"]),
                            "county",
                            "LA",
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema=["year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein", "votes", "kind", "source"],
        orient="row",
    ).with_columns(pl.col("year").cast(pl.Int32), pl.col("district").cast(pl.Int16), pl.col("measure").cast(pl.Int16)).write_parquet(
        PARSED / "counties.parquet"
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana's 1992 to 2020 U.S. Senate, House, and presidential primary contests by parish from the Secretary of State's results",
            polity="US-LA",
        )
        source = cs.source(la_sos.PUBLISHER, la_sos.TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=la_sos.PUBLISHER,
            title=la_sos.TITLE,
            url="https://voterportal.sos.la.gov/",
            license="Public record",
        )
        for day in DAYS:
            for name in names():
                try:
                    cs.artifact(raw.cached(SOURCE, la_sos.blob(day, name)), source)
                except LookupError:
                    continue
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"LA": source}, strict=True)
        return cs.write()
