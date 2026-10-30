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

SOURCE = "us.nd_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "2026 Official Sample Ballot, General Election, November 3, 2026"
BURLEIGH = "https://www.burleigh.gov/media/2smdvn0y/2026-official-sample-ballot.pdf"
COUNTIES = {"38015": (BURLEIGH, "Burleigh County Auditor")}
ROWS = [
    {
        "county": "38015",
        "entity": "City of Bismarck",
        "label": "Measure No. 1",
        "wording": "Shall Article 3 of the Home Rule Charter for the City of Bismarck be amended: To amend Article 3, section 17 to continue imposing the 1/2 cent sales, use and gross receipts tax with the proceeds to be dedicated exclusively to street construction and reconstruction of the following: i. 43rd Ave/Ash Coulee Dr from Tyler Parkway to 66th St; ii. Centennial Road from I-94 to 43rd Ave; iii. Main Ave from 26th St to 66th St; iv. State Street from Calgary Ave to ND 1804/71st Ave; v. Washington St from Meridian Dr to South Bismarck Drainage Ditch; vi. Century Ave from Centennial Rd to 66th St; vii. Tyler Parkway from Valley Dr to ND 1804; viii. 57th Ave from River Rd to Centennial Rd; ix. 66th St from Century Ave to 43rd Ave; x. ND 1804 from Yukon Dr to 19th St; xi. Burleigh Ave from Calypso Dr to University Dr; xii. 12th St from Burleigh Ave to Santa Fe Ave; xiii. Divide Ave from Bismarck Expy to 52nd St; xiv. 52nd St from Century Ave to Miriam Ave; xv. 26th St from 43rd Ave to Selland Lp; xvi. Santa Fe Ave from Boston Dr to 3rd St; xvii. Calgary Ave from State St to Centennial Rd; xviii. 19th St from Skyline Blvd to 64th Ave; xix. LaSalle Dr from State St to 19th St; xx. 64th Ave from State St to 19th St; xxi. Yukon Dr from 57th Ave to 64th Ave; xxii. The rural ditch section segments of Arabian Ave, Buckskin Ave, Colt Ave, Ottawa St, Brookside Ln, Lee Ave and Airway Ave; said tax to be discontinued after December 31, 2038, or upon the completion of the project list i-xxii, whichever occurs first.",
    },
    {
        "county": "38015",
        "entity": "Bismarck Public School District No. 1",
        "label": "Measure No. 1",
        "wording": "Shall Bismarck Public School District No. 1 of Burleigh County, North Dakota, issue its general obligation bonds in an amount not to exceed $127,000,000 maturing within a maximum of 20 years, resulting in an estimated additional millage of 10.84 mills, equal to $10.84 on each $1,000 of taxable valuation for the first taxable year after all of the bonds are issued, for the purpose of providing funds, together with any other funds available, for Americans with Disabilities Act (ADA) compliance upgrades, to undertake facility improvements districtwide to address deferred maintenance and capital renewal needs including, but not limited to heating, ventilation, plumbing and electrical system upgrades, building envelope repairs and related infrastructure improvements, including entrances, elevators, restrooms, site circulation and other modifications necessary to improve equitable access to District facilities, together with related site work, equipment, furnishings, and incidental costs.",
    },
    {
        "county": "38015",
        "entity": "Bismarck Public School District No. 1",
        "label": "Measure No. 2",
        "wording": "Shall a record of the proceedings of the Bismarck School Board be published in the official newspaper of the district?",
    },
    {
        "county": "38015",
        "entity": "City of Regan",
        "label": "Measure No. 1",
        "wording": "Shall the City of Regan reduce their current council size down to a Mayor and three (3) council positions all of which are four-year terms, removing one (1) council position that is currently vacated and will expire in 2028?",
    },
    {
        "county": "38015",
        "entity": "City of Wilton",
        "label": "Measure No. 1",
        "wording": "Shall the City of Wilton be allowed to levy an additional one percent (1%) sales tax (pursuant to the same provision, limitations and exemption as in the Home Rule Charter), to use for water and sewer projects and project indebtedness within the City?",
    },
]


def fetch() -> None:
    for url, _ in COUNTIES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            content = subprocess.run(["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True).stdout
            raw.store(SOURCE, url, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame([row | {"url": COUNTIES[row["county"]][0]} for row in ROWS])
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Dakota county, city, and school district measures on the November 3, 2026 ballot, with official wording",
            polity="US-ND",
        )
        civil = civmint.Civil(conn, cs, "38", "ND")
        found = civil.found
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for county, (url, publisher) in COUNTIES.items():
            sources[county] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[county])
        parent = cs.key("us.election", f"{DAY}:ND")
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
                minted = govmint.special(conn, cs, "ND", (county, county_area, county_path, county_code), entity, civil.slugs)
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:ND:{code}")
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
            contest_key = f"{DAY}:ND:{code}:measure:{slug(label)}"
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
            for position, answer in enumerate(("Yes", "No")):
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
