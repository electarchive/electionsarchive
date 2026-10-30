import csv
import io
import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda, govmint, local_2026
from ea.store import DATA, connect

SOURCE = "us.wa_districts_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
URL = local_2026.FEEDS["WA"][0]
PUBLISHER, TITLE = local_2026.FEEDS["WA"][1], local_2026.FEEDS["WA"][2]
TYPES = {"public utility": "Public Utility", "port": "Port", "fire": "Fire"}
NUMBERED = {
    ("Mason", "PUBLIC UTILITY DIST 1"): "Mason County Public Utility District No. 1",
    ("Mason", "Public Utility Dist 3"): "Mason County Public Utility District No. 3",
    ("Whatcom", "Public Utility District No. 1"): "Whatcom County Public Utility District No. 1",
    ("Clallam", "Public Utility District No. 1"): "Clallam County Public Utility District No. 1",
    ("Kitsap", "Public Utility District No. 1"): "Kitsap County Public Utility District No. 1",
    ("Island", "PUBLIC UTILITY DISTRICT NO. 1"): "Snohomish County Public Utility District No. 1",
    ("Snohomish", "PUBLIC UTILITY DISTRICT NO. 1"): "Snohomish County Public Utility District No. 1",
    ("Spokane", "PUBLIC UTILITY DISTRICT 1"): "Stevens County Public Utility District No. 1",
    ("Stevens", "PUBLIC UTILITY DISTRICT 1"): "Stevens County Public Utility District No. 1",
    ("Franklin", "PASCO PORT DISTRICT 3"): "Port of Pasco",
    ("Whatcom", "Port of Bellingham"): "Port of Bellingham",
    ("Douglas", "RIMROCK MEADOWS FIRE PROTECTION DISTRICT No. 9"): "Rimrock Meadows Fire Protection District No. 9",
}
HOMES = {"Island": "Snohomish", "Spokane": "Stevens"}
SKIPPED = {("Okanogan", "PUD (COUNTYWIDE)")}


def seat(race: str) -> str:
    tail = re.search(
        r"(?:dist(?:rict)?\.?|pos\.?|no\.?|#|\(|commissioner|comm)\s*#?0*(\d+|[A-Za-z])\b[^0-9]*$", race, re.IGNORECASE
    )
    return tail[1].upper() if tail else slug(race)


def parse() -> None:
    listings = json.loads(raw.cached(local_2026.SOURCE, URL).path.read_text())
    rows = []
    for county, listing in listings.items():
        for row in csv.DictReader(io.StringIO(listing)):
            kind = TYPES.get(row["District Type"].lower())
            if kind is None or (county, row["District"]) in SKIPPED or row["Status"] != "Active":
                continue
            display = NUMBERED.get((county, row["District"]))
            if display is None:
                if kind != "Public Utility":
                    print("unnamed:", county, row["District"])
                    continue
                display = f"{county} County Public Utility District"
            rows.append(
                {
                    "county": HOMES.get(county, county),
                    "district": display,
                    "seat": seat(row["Race"]),
                    "race": " ".join(row["Race"].split()),
                    "name": " ".join(row["Name"].split()),
                    "unexpired": row["Term Type"] == "Unexpired",
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["district", "seat", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        counties: dict[str, tuple[UUID, UUID, str, str]] = {}
        for polity, area, text, path, code in conn.execute(
            """select t.polity_id, a.id, n.text, s.path, c.code from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join name n on n.entity_id = t.polity_id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = t.polity_id and s.is_current
               where a.scheme_code = 'us.county' and left(c.code, 2) = '53'"""
        ).fetchall():
            counties[ceda._core(text)] = (polity, area, path, code)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/wa/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington public utility, port, and fire district commissioner races on the November 3, 2026 ballot, with candidates from the Secretary of State's candidate list",
            polity="US-WA",
        )
        system = cs.key("us.electoral_system", "fptp")
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=URL,
                license="Public record",
            )
        cs.artifact(raw.cached(local_2026.SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:WA")
        if parent in cs.minted:
            raise SystemExit("state election missing")
        made: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        elections: set[UUID] = set()
        kept = 0
        for (district, seat_label), people in frame.group_by(["district", "seat"], maintain_order=True):
            first = people.row(0, named=True)
            county = counties.get(ceda._core(f"{first['county']} County"))
            if county is None:
                print("no county:", first["county"])
                continue
            if district not in made:
                special = govmint.special(conn, cs, "WA", county, district, slugs)
                if special is None:
                    print("no district:", district)
                    continue
                made[district] = special
            polity, area, place, path, code = made[district]
            election = cs.key("us.election", f"{DAY}:WA:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="scheduled",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{DAY.year} {place} general election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            title = f"{place} Commissioner {seat_label}"
            owner = cs.key("us.office", f"WA:{code}:commissioner-{slug(seat_label)}")
            contest_key = f"{DAY}:WA:{code}:commissioner-{slug(seat_label)}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/commissioner-{slug(seat_label)}"
            if slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            slugs[contest_path] = contest
            if owner in cs.minted:
                cs.add("office", id=owner, polity_id=polity, kind="other")
                cs.add("name", entity_id=owner, text=title, lang="en", kind="official", is_preferred=True)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=owner,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
                term="remainder" if first["unexpired"] else "full",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{DAY.year} {title} election",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source, locator=None)
            for person_row in people.iter_rows(named=True):
                ballot = person_row["name"]
                token = slug(ballot)
                person = cs.key(f"{SOURCE}.person", f"{contest_key}:{token}")
                option = cs.key("us.option", f"{contest_key}:{token}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=ballot, lang="en", kind="official", is_preferred=True)
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=ballot)
                candidacy = cs.key("us.candidacy", f"{contest_key}:{token}:head")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    is_independent=True,
                )
                cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
                kept += 1
        print(f"{kept} candidates")
        return cs.write()
