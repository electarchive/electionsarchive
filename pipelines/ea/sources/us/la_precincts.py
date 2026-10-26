import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import la_sos, medsl, oe_federal
from ea.store import DATA, connect

SOURCE = "us.la_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = [
    date(2016, 11, 8),
    date(2016, 12, 10),
    date(2018, 11, 6),
    date(2018, 12, 8),
    date(2019, 10, 12),
    date(2019, 11, 16),
    date(2020, 11, 3),
    date(2020, 12, 5),
    date(2022, 11, 8),
    date(2022, 12, 10),
    date(2023, 10, 14),
    date(2023, 11, 18),
    date(2024, 11, 5),
    date(2024, 12, 7),
]
TITLE = "Official election results by precinct"


def get(address: str) -> bool:
    try:
        raw.cached(SOURCE, address)
        return True
    except LookupError:
        pass
    for attempt in range(8):
        try:
            raw.fetch(SOURCE, address, suffix=".json")
            time.sleep(0.3)
            return True
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return False
            if error.response.status_code not in (403, 429, 500, 502, 503) or attempt == 7:
                raise
            time.sleep(2**attempt)
    return False


def read(address: str) -> dict:
    return json.loads(raw.cached(SOURCE, address).path.read_text())


def races(day: date) -> dict[str, tuple[str, int | None, dict]]:
    found = {}
    for race in la_sos.many((read(la_sos.blob(day, "RacesCandidates_Multiparish.htm")).get("Races") or {}).get("Race")):
        title = str(race["SpecificTitle"])
        if "Democratic" in title or "Republican" in title:
            continue
        if race["GeneralTitle"] == "U.S. Senator":
            found[race["ID"]] = ("senate", None, race)
        elif district := re.search(r"(\d+)(?:st|nd|rd|th) Congressional", title):
            found[race["ID"]] = ("house", int(district[1]), race)
        elif "President" in title:
            found[race["ID"]] = ("president", None, race)
        elif race["GeneralTitle"] == "Governor" or title == "Governor":
            found[race["ID"]] = ("governor", None, race)
    return found


def key(day: date, chamber: str, district: int | None) -> str:
    return f"{day}:LA:{chamber}" + (f":{district}" if district is not None else "")


def wanted() -> set[str]:
    with connect() as conn:
        return oe_federal.wanted(conn, SOURCE, "LA")


def fetch() -> None:
    gaps = wanted()
    for day in DAYS:
        get(la_sos.blob(day, "RacesCandidates_Multiparish.htm"))
        for race, (chamber, district, _) in races(day).items():
            if key(day, chamber, district) not in gaps:
                continue
            for parish in la_sos.PARISHES:
                get(la_sos.blob(day, f"VotesRaceByPrecinct/Votes_{race}_{parish}.htm"))


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
        for race, (chamber, district, source) in races(day).items():
            described = {choice["ID"]: str(choice["Desc"]).strip() for choice in la_sos.many(source["Choice"])}
            for index, parish in enumerate(la_sos.PARISHES):
                try:
                    listed = read(la_sos.blob(day, f"VotesRaceByPrecinct/Votes_{race}_{parish}.htm"))
                except LookupError:
                    continue
                for precinct in la_sos.many((listed.get("Precincts") or {}).get("Precinct")):
                    name = str(precinct["Precinct"]).strip()
                    whole = not re.match(r"\d", name)
                    for choice in la_sos.many(precinct["Choice"]):
                        text = described[choice["ID"]]
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
                                fips[index] if whole else f"{day.year}:LA:{fips[index]}:SOS:{name}",
                                (found[1] if found else text).strip().upper(),
                                1,
                                False,
                                int(choice["VoteTotal"]),
                                "county" if whole else "precinct",
                                "LA",
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
            "fips": pl.String,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
            "kind": pl.String,
            "source": pl.String,
        },
        orient="row",
    ).write_parquet(PARSED / "precincts.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana's president, U.S. Senate, U.S. House, and governor elections 2016–2024 by precinct from "
            "the Secretary of State's official results, for contests no other source holds by precinct",
            polity="US-LA",
        )
        gaps = oe_federal.wanted(conn, SOURCE, "LA")
        frame = frame.filter(
            pl.struct("day", "chamber", "district")
            .map_elements(lambda row: key(row["day"], row["chamber"], row["district"]), return_dtype=pl.String)
            .is_in(list(gaps))
        )
        print(frame.group_by("day", "chamber", "district").agg(pl.col("votes").sum()).sort("day", "chamber"))
        source = cs.source(la_sos.PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=la_sos.PUBLISHER,
                title=TITLE,
                url="https://voterportal.sos.la.gov/",
                license="Public record",
            )
        for day in sorted(set(frame["day"])):
            cs.artifact(raw.cached(SOURCE, la_sos.blob(day, "RacesCandidates_Multiparish.htm")), source)
            for race, (chamber, district, _) in races(day).items():
                if key(day, chamber, district) in gaps:
                    for parish in la_sos.PARISHES:
                        try:
                            cs.artifact(
                                raw.cached(SOURCE, la_sos.blob(day, f"VotesRaceByPrecinct/Votes_{race}_{parish}.htm")),
                                source,
                            )
                        except LookupError:
                            continue
        medsl.attach(cs, conn, frame, {"LA": source}, grain="precinct", strict=True)
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '22%'"
            ).fetchall()
        }
        minted = (
            frame.filter(pl.col("kind") == "precinct")
            .group_by("code")
            .agg(pl.col("fips").first(), pl.col("day").min())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)))
        )
        cs.frame(
            "reporting_unit",
            minted.select(
                "id",
                polity_id=pl.lit(str(cs.polity("US-LA"))),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("fips").replace_strict({code: str(unit) for code, (unit, _) in counties.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("fips").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{minted.height} precincts")
        return cs.write()
