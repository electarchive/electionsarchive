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

SOURCE = "us.sc_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = (
    "Ordinance providing for the November 3, 2026 special sales and use tax referendum, third reading, July 21, 2026"
)
GREENVILLE = "https://www.greenvillecounty.org/Council/_Agenda//Meetings%20of%202026/County%20Council/2026.07.21/Transportation%20Tax%20CLEAN%20for%203rd%20R.pdf"
COUNTIES = {"45045": ((GREENVILLE,), "Greenville County Council")}
ROWS = [
    {
        "county": "45045",
        "entity": "Greenville County",
        "label": "Special Sales and Use Tax, Question 1",
        "url": "https://www.greenvillecounty.org/Council/_Agenda//Meetings%20of%202026/County%20Council/2026.07.21/Transportation%20Tax%20CLEAN%20for%203rd%20R.pdf",
        "answers": "Yes|No",
        "wording": "I approve a special sales and use tax in the amount of one percent to be imposed in Greenville County for not more than eight (8) years, or until a total of $1,100,000,000 in resulting revenue has been collected, whichever occurs first. The sales tax proceeds will be used to fund the following projects: Project (1) For financing the costs of highways, roads, streets, bridges, and other transportation-related projects facilities, and drainage facilities related thereto. $990,000,000. Project (2) For financing the costs of mass transit systems operated by Greenville County or jointly operated by the County and other governmental entities. $33,000,000. Project (3) For financing the costs of greenbelts. $77,000,000.",
    },
    {
        "county": "45045",
        "entity": "Greenville County",
        "label": "General Obligation Bonds, Question 2",
        "url": "https://www.greenvillecounty.org/Council/_Agenda//Meetings%20of%202026/County%20Council/2026.07.21/Transportation%20Tax%20CLEAN%20for%203rd%20R.pdf",
        "answers": "Yes|No",
        "wording": "I approve the issuance of not exceeding $150,000,000 of general obligation bonds of Greenville County, payable from the special sales and use tax described in Question 1 above, maturing over a period not to exceed eight (8) years, to fund completion of projects from among the categories described in Question 1 above.",
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
            summary="South Carolina local referenda on the November 3, 2026 ballot, with official wording",
            polity="US-SC",
        )
        civil = civmint.Civil(conn, cs, "45", "SC")
        found = civil.found
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for county, (urls, publisher) in COUNTIES.items():
            sources[county] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=urls[0])
            for url in urls:
                cs.artifact(raw.cached(SOURCE, url), sources[county])
        parent = cs.key("us.election", f"{DAY}:SC")
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
                    conn, cs, "SC", (county, county_area, county_path, county_code), entity, civil.slugs
                )
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:SC:{code}")
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
            contest_key = f"{DAY}:SC:{code}:measure:{slug(label)}"
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
