import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import civmint, govmint
from ea.store import DATA, connect

SOURCE = "us.co_frpr_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Composite Sample Ballot, November 3, 2026 general election"
URL = "https://www.douglasco.gov/elections/election-information/on-the-ballot/"
FILE = DATA / "inbox" / "manual" / "MF-376" / "CO-Douglas" / "2026-general-election-composite-sample-ballot.pdf"
COUNTIES = {"08035": ((URL,), "Douglas County Clerk and Recorder")}
ROWS = [
    {
        "county": "08035",
        "entity": "Front Range Passenger Rail District",
        "label": "Ballot Issue 7A",
        "url": URL,
        "answers": "Yes|No",
        "wording": "SHALL FRONT RANGE PASSENGER RAIL DISTRICT TAXES BE INCREASED $295,000,000 ANNUALLY AND BY WHATEVER AMOUNTS ARE RAISED ANNUALLY THEREAFTER, AND SHALL FRONT RANGE PASSENGER RAIL DISTRICT DEBT BE INCREASED $580,000,000, WITH A REPAYMENT COST OF $785,000,000; TO CONSTRUCT, OPERATE, AND MAINTAIN COLORADO CONNECTOR (COCO) PASSENGER RAIL SERVICE ON COLORADO'S FRONT RANGE AND CONNECT COMMUNITIES, INCLUDING PUEBLO, COLORADO SPRINGS, STERLING RANCH, LITTLETON, DENVER, WESTMINSTER, BROOMFIELD, LOUISVILLE, BOULDER, LONGMONT, LOVELAND, AND FORT COLLINS; IN ORDER TO: REMOVE VEHICLES FROM HIGHWAYS AND INCREASE TRAVEL CAPACITY; CONNECT TRAVELERS TO EMPLOYMENT CENTERS, COLLEGES, SPORTS ARENAS, AND ENTERTAINMENT HUBS ALONG THE FRONT RANGE; AND INVEST IN STATION AREA IMPROVEMENTS AND LOCAL CONNECTIONS TO RAIL STATIONS; BY ESTABLISHING A 0.333% SALES AND USE TAX (EQUAL TO ONE THIRD OF A PENNY ON A $1 PURCHASE), WITH EXEMPTIONS PROVIDED UNDER COLORADO LAW, INCLUDING THOSE FOR GASOLINE, FOOD, RESIDENTIAL ELECTRICITY AND GAS, PRESCRIPTION DRUGS, AND MEDICAL SUPPLIES; AND TO RETAIN ALL SUCH REVENUES, PUBLIC AND PRIVATE CONTRIBUTIONS, AND ANY INVESTMENT INCOME ON REVENUES AND DEBT PROCEEDS, AS A VOTER-APPROVED REVENUE CHANGE UNDER SECTION 20 OF ARTICLE X OF THE COLORADO CONSTITUTION; AND REQUIRING THAT ALL SUCH DEDICATED REVENUES BE REVIEWED ANNUALLY BY AN INDEPENDENT AUDITOR AND A ROTATING GROUP OF CITIZEN TAXPAYERS WHO LIVE IN THE DISTRICT?",
    },
]


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(SOURCE, URL, FILE.read_bytes(), suffix=".pdf", retrieved_at=datetime.fromtimestamp(FILE.stat().st_mtime, UTC), media_type="application/pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(ROWS)
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Front Range Passenger Rail District Ballot Issue 7A on the November 3, 2026 ballot, with official wording",
            polity="US-CO",
        )
        civil = civmint.Civil(conn, cs, "08", "CO")
        found = civil.found
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for county, (urls, publisher) in COUNTIES.items():
            sources[county] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=urls[0])
            for url in urls:
                cs.artifact(raw.cached(SOURCE, url), sources[county])
        parent = cs.key("us.election", f"{DAY}:CO")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            entity = row["entity"]
            county_key = f"us.county:{row['county']}"
            source = sources[row["county"]]
            option = None
            if re.match(r"(?i)(city|town|village) of ", entity):
                option = civil.place(re.sub(r"(?i)^(city|town|village) of ", "", entity))
            elif county_key in found and entity == found[county_key][2]:
                option = county_key
            elif entity.endswith(" Township"):
                option = civil.town(entity, row["county"][:5])
            elif re.search(r"School District|CUSD", entity):
                option = civil.school(entity)
            elif f"special:{row['county']}:{entity}" in found:
                option = f"special:{row['county']}:{entity}"
            elif county_key in found:
                county, county_area, _, county_path, county_code = found[county_key]
                minted = govmint.special(conn, cs, "CO", (county, county_area, county_path, county_code), entity, civil.slugs)
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:CO:{code}")
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
                cs.add("name", entity_id=election, text=f"{DAY.year} {place} general election", lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            label = row["label"]
            contest_key = f"{DAY}:CO:{code}:measure:{slug(label)}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{slug(label)}"
            if civil.slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            civil.slugs[contest_path] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="initiative" if label.startswith("Initiated") else "mandatory_referendum")
            cs.add("name", entity_id=contest, text=f"{DAY.year} {place}: {label}", lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            for position, answer in enumerate(row["answers"].split("|")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{slug(answer)}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add("citation", entity_id=contest, source_id=source)
            cs.add(
                "ballot_question_text",
                contest_id=contest,
                kind="text",
                lang="en",
                body=row["wording"],
                url=row["url"],
                source_id=source,
            )
        print(f"{kept} of {frame.height} referenda")
        return cs.write()
