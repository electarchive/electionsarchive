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

SOURCE = "us.ga_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Official notices and sample ballots, November 3, 2026 general and special election"
FULTON = "https://www.fultoncountyga.gov/-/media/2026-Election/November-3-Election/Sample-Ballot--Consolidated--Nov-32026-General--Special-Election--1082026.pdf"
DEKALB = "https://dekalbcountyga.gov/sites/default/files/2026-09/sample-booklet.pdf"
COBB = "https://assets.cobbcounty.gov/files/2026-09/SAMPLE%20BALLOT%20DRAFT%20v4%20WEB.pdf"
GWINNETT = "https://www.gwinnettcounty.com/documents/d/gwinnett-county/november-3-special-election-notice-hb-540"
CHATHAM = "https://cms.chathamcountyga.gov/api/assets/elections-website/64985a67-cce4-4146-a8dd-52587318e5f2/call-for-election-tax-referendum-11-3-2026.pdf"
COUNTIES = {
    "13121": ((FULTON,), "Fulton County Department of Registration and Elections"),
    "13089": ((DEKALB,), "DeKalb County Voter Registration and Elections"),
    "13067": ((COBB,), "Cobb County Elections and Registration"),
    "13135": ((GWINNETT,), "Gwinnett County Board of Voter Registrations and Elections"),
    "13051": ((CHATHAM,), "Chatham County Board of Elections"),
}
ROWS = [
    {
        "county": "13121",
        "entity": "Fulton County",
        "label": "Transportation SPLOST",
        "url": "https://www.fultoncountyga.gov/-/media/2026-Election/November-3-Election/Sample-Ballot--Consolidated--Nov-32026-General--Special-Election--1082026.pdf",
        "answers": "Yes|No",
        "wording": "Shall an additional 0.75 percent sales and use tax be imposed in that part of Fulton County, Georgia, outside of the City of Atlanta, for a period not to exceed five years for the purpose of transportation improvements and congestion reduction."
    },
    {
        "county": "13121",
        "entity": "City of Sandy Springs",
        "label": "Redevelopment Powers Referendum",
        "url": "https://www.fultoncountyga.gov/-/media/2026-Election/November-3-Election/Sample-Ballot--Consolidated--Nov-32026-General--Special-Election--1082026.pdf",
        "answers": "Yes|No",
        "wording": "Shall the Act be approved which authorizes the City of Sandy Springs, Georgia, to exercise all redevelopment powers allowed under the ‘Redevelopment Powers Law,’ as it may be amended from time to time."
    },
    {
        "county": "13121",
        "entity": "City of Palmetto",
        "label": "Senior Homestead Exemption",
        "url": "https://www.fultoncountyga.gov/-/media/2026-Election/November-3-Election/Sample-Ballot--Consolidated--Nov-32026-General--Special-Election--1082026.pdf",
        "answers": "Yes|No",
        "wording": "Shall persons who are 65 years of age or older by January 1, 2026 and are a resident of Palmetto, Georgia who own and occupy as a primary residence a home situated in the City of Palmetto qualify for 100% of city ad valorem tax exemption in accordance with O.C.G.A. § 48-5-48.3?"
    },
    {
        "county": "13121",
        "entity": "Fulton County School District",
        "label": "Educational Special Purpose Local Option Sales Tax (ESPLOST)",
        "url": "https://www.fultoncountyga.gov/-/media/2026-Election/November-3-Election/Sample-Ballot--Consolidated--Nov-32026-General--Special-Election--1082026.pdf",
        "answers": "Yes|No",
        "wording": "Shall the special one percent sales and use tax for educational purposes currently imposed in Fulton County be reimposed on July 1 2027 upon the expiration of the currently imposed tax, for not longer than 20 consecutive calendar quarters, to raise not more than $2,095,680,577.00 to be used for the following educational purposes: For the Fulton County School District: (A) for acquiring, reconfiguring and developing land for, constructing and equipping, new and replacement schools and support facilities; (B) adding to, upgrading, reconfiguring, renovating, replacing, modifying and equipping existing and replacement schools, support facilities and athletic facilities; (C) making technology improvements and safety and security improvements for schools and facilities; and (D) replacing, purchasing, upgrading or supplementing capital equipment as needed, including without limitation furniture, multi-year textbooks, instructional resources, physical educational equipment, band and music equipment, audio visual equipment, equipment that supports learning and similar equipment; and (E) acquiring and equipping school buses, school police vehicles, support vehicles, transportation vehicles and other capital equipment; in an amount not to exceed $1,306,195,350.00 for the Fulton County School District, all as more fully described in the Notice of Election. For the Atlanta Independent School System: (A) for acquiring, reconfiguring and developing land for, constructing and equipping, new and replacement schools and support facilities; (B) adding to, upgrading, reconfiguring, renovating, replacing, modifying and equipping existing and replacement schools, support facilities and athletic facilities; (C) making technology improvements and safety and security improvements for schools and facilities; (D) replacing, purchasing, upgrading or supplementing capital equipment as needed, including without limitation furniture, multi-year textbooks, instructional resources, physical educational equipment, band and music equipment, audio visual equipment, equipment that supports learning and similar equipment; and (E) acquiring and equipping school buses, school police vehicles, support vehicles, transportation vehicles and other capital equipment; at a total maximum cost of $789,485,227.00 , for the Atlanta Independent School System as more fully described in the Notice of Election. If the imposition of the tax is approved by the majority of the qualified voters residing in Fulton County, such vote shall also constitute approval of the issuance of general obligation debt of the Atlanta Independent School System in the principal amount of up to $350,000,000.00 payable from its portion of the tax for the purpose of funding all or a portion of the APS Projects located in Fulton County."
    },
    {
        "county": "13089",
        "entity": "DeKalb County School District",
        "label": "Educational Special Purpose Local Option Sales Tax (ESPLOST)",
        "url": "https://dekalbcountyga.gov/sites/default/files/2026-09/sample-booklet.pdf",
        "answers": "Yes|No",
        "wording": "Shall the special one percent sales and use tax for educational purposes currently imposed in DeKalb County be reimposed on July 1, 2027, upon the expiration of the such currently imposed tax, for not longer than 20 consecutive calendar quarters, to raise not more than $1,046,154,324.00 to be used for the following educational purposes: For the DeKalb County School District: (A) adding to, upgrading, reconfiguring, renovating, replacing, modifying and equipping existing and replacement schools, support facilities, athletic fields and physical education facilities; (B) for acquiring and developing land for, constructing and equipping, new and replacement schools and support facilities, as well as additions to existing facilities. (C) for installing capital improvements for various existing and new schools, buildings and facilities (D) for providing hardware, software, and related infrastructure and making technology improvements; and (E) replacing, purchasing, upgrading school buses, support vehicles, and other capital equipment. in an amount not to exceed $946,773,776.00 for the DeKalb County School District, all as more fully described in the Notice of Election. If imposition of the tax is approved by a majority of the voters within the DeKalb County School District, such vote shall also constitute approval of the issuance of general obligation debt of the DeKalb County School District in the principal amount of not to exceed $500,000,000 for the purpose of funding a portion of the above projects of the DeKalb County School District. For the Atlanta Independent School System: (A) for acquiring, reconfiguring and developing land for, constructing and equipping, new and replacement schools and support facilities; (B) replacing, updating, renovating, adding to, modifying, and equipping certain critical systems, infrastructure, safety systems and non- critical if and as needed, based on the condition and identified deficiencies; (C) adding to, upgrading, reconfiguring, renovating, replacing, modifying and equipping existing and replacement schools, playgrounds, support facilities and athletic facilities as needed; (D) making technology improvements and safety and security improvements for schools and facilities; (E) replacing, purchasing, upgrading or supplementing capital equipment as needed, including without limitation furniture, multi-year textbooks, instructional resources, physical educational equipment, kitchen equipment, band and music equipment and equipment that supports learning; and (F) acquiring and equipping school buses, school police vehicles, support vehicles, transportation vehicles and other capital equipment; at a total maximum cost of $41,504,193.00, for the Atlanta Independent School System as more fully described in the Notice of Election. For the City of Decatur School System: (A) for the acquisition, construction, renovation, modification, addition, repair, replacement, demolition of, improvement and equipping of existing and new school buildings and other facilities, including other buildings and greenspace useful or desirable in connection therewith; (B) to acquire and install safety and technology improvements throughout the school system, including computers, systems, sensors, and related hardware and infrastructure; (C) to acquire school buses, school-related vehicles, and transportation equipment, including infrastructure for electric vehicles; (D) for school and facilities improvement, energy conservation, and maintenance projects; (E) to acquire supplemental capital equipment for the school system, including any property, real and personal, useful and desirable in connection with the projects listed in the Notice of Election; and (F) To pay the cost of acquiring certain assets that are being leased or subject to installment purchase by the City Schools of Decatur and to retire all or a portion of any outstanding general obligation debt of the City of Decatur incurred on behalf of the City Schools of Decatur for school projects in an amount not to exceed $57,876,355.00 for the City Schools of Decatur, all as more fully described in the Notice of Election."
    },
    {
        "county": "13089",
        "entity": "City of Brookhaven",
        "label": "Homestead Exemption for Senior Citizens and Disabled Persons (House Bill 1307)",
        "url": "https://dekalbcountyga.gov/sites/default/files/2026-09/sample-booklet.pdf",
        "answers": "Yes|No",
        "wording": "Shall the Act to incorporate the City of Brookhaven be amended so as to increase the current homestead exemption for senior citizens and disabled persons from $160,000.00 to $200,000.00?"
    },
    {
        "county": "13089",
        "entity": "City of Tucker",
        "label": "Annexation (Senate Bill 622)",
        "url": "https://dekalbcountyga.gov/sites/default/files/2026-09/sample-booklet.pdf",
        "answers": "Yes|No",
        "wording": "Shall the Act which annexes certain land into the City of Tucker be approved?"
    },
    {
        "county": "13067",
        "entity": "Cobb County",
        "label": "Special Purpose Local Option Sales Tax (SPLOST)",
        "url": "https://assets.cobbcounty.gov/files/2026-09/SAMPLE%20BALLOT%20DRAFT%20v4%20WEB.pdf",
        "answers": "Yes|No",
        "wording": "Shall a special 1 percent sales and use tax be re-imposed in the special district of Cobb County for a period of time not to exceed six years and for the raising of an estimated amount of $1,150,000,000.00 for the purpose of funding the following for the County and the seven Municipalities in Cobb County from their respective shares, including the Cities of Acworth, Austell, Kennesaw, Mableton, Marietta, Powder Springs and Smyrna? (1) Transportation Improvements including: roadway and street paving and resurfacing; roadway and corridor redevelopment, infrastructure and safety improvements; intersection and railroad crossing improvements; sidewalk and pedestrian facilities and safety improvements; repair/replacement/rehabilitation of bridges, culverts and roadway drainage systems; traffic operations, efficiency and safety improvements; school zone and traffic calming improvements; traffic management and congestion relief improvements; traffic system (ATMS) improvements; signage and street marking improvements; transportation technology improvements, transit capital improvements, public parking improvements; and Cumberland/Town Center Community Improvement Districts transportation infrastructure improvements; and (2) Infrastructure Improvements including: water, stormwater and drainage infrastructure and system improvements and upgrades; Gateway and streetscape, signage and wayfinding improvements; E.V. vehicles and charging stations; and (3) Parks and Recreation Improvements including: parks facilities renovations, additions, improvements and equipment acquisition and upgrades; parks technology, life safety and security improvements; parks buildings renovations and improvements including mechanical system replacements and subsurface infrastructure improvements; resurfacing and repaving of parks access roadways and parking lots; synthetic turf replacement and conversion; sports complex improvements; and improvements to greenways, trails and multi-use trails; and (4) Facility Improvements including: the construction, replacement, renovation, rehabilitation, enhancement or upgrade of libraries; senior centers; public safety facilities; solid waste transfer station(s); community centers; a State Court courthouse; an Adult Detention Center Infirmary Building and related improvements; county and municipal buildings, systems and facilities; fleet replacements including maintenance and sanitation trucks and police cars; and the purchase and acquisition of real property and equipment, including public safety equipment, for such improvements, as needed; and (5) Technology Improvements including: the installation, enhancement or upgrade(s) to infrastructure technology, communication and recordkeeping systems; audio/visual systems; enhanced life safety and security improvements at public facilities; Geographic Information System; security and cyber security protection systems; enterprise systems and financial enterprise software; data management and modernization; digital access and citizen engagement system(s); digital government business systems; public safety technology upgrades; and improvements to the Countywide E-911 system; All such projects are to take place within the special district of Cobb County, including unincorporated Cobb County, and the Cities of Acworth, Austell, Kennesaw, Mableton, Marietta, Powder Springs, and Smyrna."
    },
    {
        "county": "13067",
        "entity": "City of Mableton",
        "label": "Redevelopment Powers Law Referendum",
        "url": "https://assets.cobbcounty.gov/files/2026-09/SAMPLE%20BALLOT%20DRAFT%20v4%20WEB.pdf",
        "answers": "Yes|No",
        "wording": "Shall the Act be approved which authorizes the City of Mableton, Georgia, to exercise all redevelopment powers allowed under the 'Redevelopment Powers Law,' as it may be amended from time to time?"
    },
    {
        "county": "13135",
        "entity": "Gwinnett County School District",
        "label": "Homestead Exemption for Public Service Employees (HB 540)",
        "url": "https://www.gwinnettcounty.com/documents/d/gwinnett-county/november-3-special-election-notice-hb-540",
        "answers": "Yes|No",
        "wording": "Shall the existing additional $2,000 homestead exemption from Gwinnett County School District ad valorem taxes for educational purposes for certain public service employees be expanded to also apply to disabled veterans and all employees of the Gwinnett County School District and the Buford City School District?"
    },
    {
        "county": "13051",
        "entity": "Chatham County School District",
        "label": "School Board Ad Valorem Tax Referendum",
        "url": "https://cms.chathamcountyga.gov/api/assets/elections-website/64985a67-cce4-4146-a8dd-52587318e5f2/call-for-election-tax-referendum-11-3-2026.pdf",
        "answers": "Yes|No",
        "wording": "Shall the Act amending the adjusted base year assessed value homestead exemption from Chatham County and City of Savannah School District ad valorem taxes for educational purposes be approved so as to prevent any future increases in the adjusted base year assessed value of a homestead above the assessed value of such homestead in tax year 2025?"
    }
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
            summary="Georgia local referenda on the November 3, 2026 ballot, with official wording",
            polity="US-GA",
        )
        civil = civmint.Civil(conn, cs, "13", "GA")
        found = civil.found
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for county, (urls, publisher) in COUNTIES.items():
            sources[county] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=urls[0])
            for url in urls:
                cs.artifact(raw.cached(SOURCE, url), sources[county])
        parent = cs.key("us.election", f"{DAY}:GA")
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
                minted = govmint.special(conn, cs, "GA", (county, county_area, county_path, county_code), entity, civil.slugs)
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:GA:{code}")
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
            contest_key = f"{DAY}:GA:{code}:measure:{slug(label)}"
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
