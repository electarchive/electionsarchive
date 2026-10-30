import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import civmint, govmint
from ea.store import DATA, connect

SOURCE = "us.co_boulder_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Boulder County ballot titles, November 3, 2026 general election"
RES_053 = "https://assets.bouldercounty.gov/wp-content/uploads/2026/08/Resolution_2026-053_setting_and_certifying_ballot_title_early_childcare_and_education.pdf"
RES_056 = "https://assets.bouldercounty.gov/wp-content/uploads/2026/09/Res_2026-056_ballot-measure.pdf"
COUNTIES = {"08013": ((RES_053, RES_056), "Boulder County Board of County Commissioners")}
ROWS = [
    {
        "county": "08013",
        "entity": "Boulder County",
        "label": "Ballot Issue 1A",
        "url": RES_053,
        "answers": "Yes|No",
        "wording": "SHALL BOULDER COUNTY TAXES BE INCREASED $30,000,000 ANNUALLY (IN TAX COLLECTION YEAR 2027), AND BY SUCH ADDITIONAL AMOUNTS RAISED ANNUALLY THEREAFTER, BY A MILL LEVY IMPOSED AT A RATE OF 2.579 MILLS (EQUATING TO APPROXIMATELY $115 PER YEAR ON A $725,000 HOME BASED ON CURRENT RATES OF ASSESSMENT) FOR THE PURPOSE OF ADDRESSING THE SHORTAGE AND HIGH COST OF CHILD CARE AND PRESCHOOL PROGRAMS FOR BOULDER COUNTY FAMILIES WITH YOUNG CHILDREN, INCLUDING: LOWERING THE HIGH COST OF CHILD CARE AND PRESCHOOL FOR BOULDER COUNTY FAMILIES; INCREASING THE COMPENSATION OF CHILD CARE AND PRESCHOOL TEACHERS AND STAFF TO RETAIN AND ATTRACT HIGH-QUALITY EDUCATORS; AND ADDRESSING THE SHORTAGE OF CARE BY EXPANDING CAPACITY FOR MORE CHILDREN AND REDUCING WAITLISTS; AND SHALL THE REVENUES AND EARNINGS ON THE INVESTMENT OF THE PROCEEDS OF SUCH TAX CONSTITUTE A VOTER-APPROVED REVENUE CHANGE UNDER ARTICLE X SECTION 20 OF THE COLORADO CONSTITUTION AND AN EXCEPTION TO THE LIMITATIONS SET FORTH IN SECTION 29-1-301 OF THE COLORADO REVISED STATUTES, ALL AS MORE PARTICULARLY SET FORTH IN BOARD OF COUNTY COMMISSIONERS' RESOLUTION NO. 2026-048?",
    },
    {
        "county": "08013",
        "entity": "Boulder County",
        "label": "Ballot Question 200",
        "url": RES_056,
        "answers": "Yes|No",
        "wording": "Shall the membership of the Boulder County Board of Commissioners be increased from a three member board to a five member board?",
    },
    {
        "county": "08013",
        "entity": "Boulder County",
        "label": "Ballot Question 201",
        "url": RES_056,
        "answers": "Five commissioners resident in districts to be elected only by voters resident in the district|Three commissioners resident in districts to be elected only by voters resident in the district, plus two commissioners resident in any district to be elected by voters of the whole county",
        "wording": "If the membership of the Boulder County Board of Commissioners is increased from a three member board to a five member board, by what method shall the Boulder County Board of Commissioners be elected? (Vote for One) a. Five commissioners resident in districts to be elected only by voters resident in the district; or b. Three commissioners resident in districts to be elected only by voters resident in the district; plus two commissioners resident in any district to be elected by voters of the whole county.",
    },
]


def fetch() -> None:
    for urls, _ in COUNTIES.values():
        for url in urls:
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                content = subprocess.run(["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True).stdout
                raw.store(SOURCE, url, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


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
            summary="Boulder County, Colorado ballot issue and questions on the November 3, 2026 ballot, with official ballot titles",
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
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
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
