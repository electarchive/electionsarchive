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

SOURCE = "us.co_denver_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Ballot Information Booklet and Ballot Issue Notice, November 3, 2026 general election"
BIB = "https://www.denvergov.org/files/assets/public/v/1/clerk-and-recorder/documents/elections-division/2026/documents/26-general-election/2026-eng-ballot-info-book_accessible.pdf"
TAB = "https://www.denvergov.org/files/assets/public/v/2/clerk-and-recorder/documents/elections-division/2026/documents/26-general-election/tabor_eng-text.pdf"
COUNTIES = {"08031": ((BIB, TAB), "Denver Clerk and Recorder, Elections Division")}
ROWS = [
    {
        "county": "08031",
        "entity": "City and County of Denver",
        "label": "Referred Question 2H",
        "url": BIB,
        "answers": "Yes|No",
        "wording": "Shall the Charter of the City and County of Denver be amended to expressly clarify that the Auditor’s Office may continue to enforce wage laws, worker protection laws, and other workplace rights laws in accordance with ordinances passed by City Council?",
    },
    {
        "county": "08031",
        "entity": "City and County of Denver",
        "label": "Referred Question 2I",
        "url": BIB,
        "answers": "Yes|No",
        "wording": "Shall the Charter of the City and County of Denver be amended to adjust the City and County’s budget and appropriation process, and in connection therewith, allowing the City Council to adopt a two-year budget planning and adoption process and modernizing or repealing certain charter sections?",
    },
    {
        "county": "08031",
        "entity": "City and County of Denver",
        "label": "Referred Question 2J",
        "url": BIB,
        "answers": "Yes|No",
        "wording": "Shall the Charter of the City and County of Denver be amended to modernize the Civil Service Commission and Department of Safety by removing outdated language, giving City Council the ability to add duties via code, and providing for an extension of probation when required for medical, military, or other approved leave?",
    },
    {
        "county": "08031",
        "entity": "City and County of Denver",
        "label": "Referred Question 2K",
        "url": BIB,
        "answers": "Yes|No",
        "wording": "Shall the City and County of Denver approve the terms of a twenty-year franchise contract, starting January 1, 2027, with Public Service Company of Colorado (Xcel Energy) to continue its program for undergrounding overhead electrical lines, maintaining infrastructure safety, and partnering with the City for emergency management coordination, and to require that Xcel Energy continue to pay the City for reasonable use of public rights-of-way and other City property necessary to provide safe and reliable utility services for the benefit of the City, its residents, and its businesses?",
    },
    {
        "county": "08031",
        "entity": "City and County of Denver",
        "label": "Initiated Ordinance 311",
        "url": BIB,
        "answers": "Yes|No",
        "wording": "Shall the voters of the City and County of Denver adopt an ordinance prohibiting the force-feeding of birds and, in connection therewith, prohibiting the sale of products derived from force-feeding birds, including foie gras?",
    },
    {
        "county": "08031",
        "entity": "Denver Public Schools",
        "label": "Ballot Issue 4A",
        "url": TAB,
        "answers": "Yes|No",
        "wording": "SHALL DENVER PUBLIC SCHOOLS (SCHOOL DISTRICT NO. 1) TAXES BE INCREASED UP TO $44 MILLION IN 2026 (FOR COLLECTION IN 2027) AND ANNUALLY THEREAFTER FOR EDUCATIONAL PURPOSES, INCLUDING, BUT NOT LIMITED TO: • INCREASING EDUCATOR PAY; • OFFERING STUDENTS MORE CAREER AND TECHNICAL EDUCATION COURSES IN HIGH DEMAND FIELDS; • STUDENT MENTAL HEALTH AND SPECIAL EDUCATION; WITH ALL SPENDING OF SUCH REVENUES REPORTED IN THE DISTRICT’S ANNUAL INDEPENDENT AUDIT PUBLISHED ON THE DISTRICT’S WEBSITE; BY THE IMPOSITION OF A MILL LEVY THAT, WHEN COMBINED WITH OTHER MILL LEVY OVERRIDES APPROVED BY DENVER VOTERS FOR THE DISTRICT’S GENERAL FUND, EQUALS THE AMOUNT PERMITTED BY SECTION 22-54-108(3)(B)(VI), BY THE LEVY OF PROPERTY TAXES AT A RATE SUFFICIENT TO PRODUCE SUCH AMOUNTS; WHICH TAXES SHALL BE DEPOSITED INTO THE GENERAL FUND OF THE DISTRICT AND SHALL BE IN ADDITION TO THE PROPERTY TAXES THAT OTHERWISE WOULD BE LEVIED FOR THE GENERAL FUND; AND SHALL INVESTMENT EARNINGS ON THE PROCEEDS OF SUCH TAXES BE TREATED AS A VOTER-APPROVED REVENUE CHANGE UNDER ARTICLE X, SECTION 20 OF THE COLORADO CONSTITUTION?",
    },
]


def fetch() -> None:
    for urls, _ in COUNTIES.values():
        for url in urls:
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                content = subprocess.run(
                    ["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True
                ).stdout
                raw.store(
                    SOURCE, url, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
                )


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
            summary="Denver referred questions, initiated ordinance, and Denver Public Schools ballot issue on the November 3, 2026 ballot, with official wording",
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
                minted = govmint.special(
                    conn, cs, "CO", (county, county_area, county_path, county_code), entity, civil.slugs
                )
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
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{DAY.year} {place} general election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
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
            cs.add(
                "ballot_question",
                contest_id=contest,
                type="initiative" if label.startswith("Initiated") else "mandatory_referendum",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{DAY.year} {place}: {label}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
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
