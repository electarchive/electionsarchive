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

SOURCE = "us.mo_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
MANUAL = DATA / "inbox" / "manual" / "MF-376"
TITLE = "Official ballot issues, November 3, 2026 general election"
BALLOTS: dict[str, tuple[str, str | None, str]] = {
    "29077": (
        "https://vote.greenecountymo.gov/wp-content/uploads/2026/09/Candidates_and_Ballot_Issues_Composite_November_2026.pdf",
        "MO-Greene/Candidates_and_Ballot_Issues_Composite_November_2026.pdf",
        "Greene County Clerk",
    ),
    "29209": (
        "https://stonecountyclerk.com/media/uploads/downloads/Stone_Publication_110326.pdf",
        "MO-Stone/Stone_Publication_110326.pdf",
        "Stone County Clerk",
    ),
    "29213": (
        "https://media.taneycounty.org/pdf.php?id=5758",
        "MO-Taney/2026.09.16.-.2026.11.03.-.Clerk.-GE.Comp.Ballot.pdf",
        "Taney County Clerk",
    ),
    "29225": (
        "https://webstercountymo.gov/wp-content/uploads/2026/09/Webster-Publication-110326.pdf",
        "MO-Webster/Webster-Publication-110326.pdf",
        "Webster County Clerk",
    ),
    "29189 CHE-002": (
        "https://stlouiscovotes.maps.arcgis.com/sharing/rest/content/items/61239fd51d444031a976ee05b40aa022/data",
        None,
        "St. Louis County Board of Election Commissioners",
    ),
    "29189 CC-001": (
        "https://stlouiscovotes.maps.arcgis.com/sharing/rest/content/items/8bf13421d1a544e98a4895fe89544b79/data",
        None,
        "St. Louis County Board of Election Commissioners",
    ),
    "29189 FLO-001": (
        "https://stlouiscovotes.maps.arcgis.com/sharing/rest/content/items/0ce85a2c8c6f46ba8476c575e4af38d0/data",
        None,
        "St. Louis County Board of Election Commissioners",
    ),
    "29189 JEF-001": (
        "https://stlouiscovotes.maps.arcgis.com/sharing/rest/content/items/9f13afa8328c4e44aed7bf3bb6ab3aad/data",
        None,
        "St. Louis County Board of Election Commissioners",
    ),
    "29189 CLA-007": (
        "https://stlouiscovotes.maps.arcgis.com/sharing/rest/content/items/0371516e804248eca9159185087a8f8f/data",
        None,
        "St. Louis County Board of Election Commissioners",
    ),
    "29189 MER-007": (
        "https://stlouiscovotes.maps.arcgis.com/sharing/rest/content/items/4f350416cd604e5bba5e5edeee679bd7/data",
        None,
        "St. Louis County Board of Election Commissioners",
    ),
    "29189 AP-016": (
        "https://stlouiscovotes.maps.arcgis.com/sharing/rest/content/items/8a9fbf176add4b8e93d60f1092ff7740/data",
        None,
        "St. Louis County Board of Election Commissioners",
    ),
    "29189 AP-001": (
        "https://stlouiscovotes.maps.arcgis.com/sharing/rest/content/items/64b15a43aef94b638f69efc76f68878c/data",
        None,
        "St. Louis County Board of Election Commissioners",
    ),
    "29095": (
        "https://jcebmo.org/wp-content/uploads/11.03.26-Notice-of-Election-Sample-Ballot-Poster.pdf",
        None,
        "Jackson County Board of Election Commissioners",
    ),
}
LOCAL = [
    (
        "29209",
        "Stone County Public Water Supply District No. 1",
        "Question",
        "Shall Stone County Public Water Supply District No. 1 be dissolved, its water system and related assets sold and transferred, and Missouri-American Water Company become the provider of water service to the customers formerly served by the District?",
    ),
    (
        "29209",
        "Village of Indian Point",
        "Question",
        "Shall the Village of Indian Point impose a city sales tax of one half of one percent (0.5%) for public safety purposes and public improvements?",
    ),
    (
        "29213",
        "City of Rockaway Beach",
        "Question",
        "Shall the City of Rockaway Beach, Missouri impose a local use tax at the same rate as the total local sales tax rate, currently 2 percent (2%), provided that if the local sales tax rate is reduced or raised by voter approval, the local use tax rate shall also be reduced or raised by the same action? A use tax return shall not be required to be filed by persons whose purchases from out-of-state vendors do not in total exceed two thousand dollars in any calendar year.",
    ),
    (
        "29213",
        "Village of Taneyville",
        "Question",
        "Shall the Village of Taneyville, Missouri, impose a property tax levy of Fifty Cents ($0.50) per One Hundred Dollars ($100.00) of assessed valuation upon all taxable real property within the Village for the sole purpose of funding road maintenance and repair, and related transportation infrastructure improvements? The Tax shall remain in effect for a period of five (5) years and shall expire November 7, 2031 unless renewed or extended by approval of the voters. The Village shall be authorized to submit the tax for renewal or extension prior to its expiration.",
    ),
    (
        "29213",
        "Western Taney County Fire Protection District",
        "Question 1",
        "Shall the number of members of the board of directors of the Western Taney County Fire Protection District be increased to five members?",
    ),
    (
        "29225",
        "Webster County",
        "Question One",
        "Shall the County of Webster continue to impose a countywide sales tax at the rate of one-half of one percent for a period of ten years from the date on which such renewed tax is first imposed for the purpose of continuing the fund established for the improvement and construction of roads and bridges, including the purchase of machinery and materials?",
    ),
    (
        "29189 CHE-002",
        "St. Louis County",
        "Proposition A",
        "Shall Sections 2.200 and 2.210 of the Charter of St. Louis County be amended to remove the term of office from the position of County Auditor and as set forth in Exhibit A of Ordinance No. 29,627, on file with the St. Louis County Administrative Director and the St. Louis County Board of Election Commissioners?",
    ),
    (
        "29189 CHE-002",
        "St. Louis County",
        "Proposition S",
        "Shall St. Louis County levy a tax of five cents per each one hundred dollars ($100.00) of assessed valuation for the purpose of providing services to persons sixty years of age or older?",
    ),
    (
        "29189 CHE-002",
        "City of Chesterfield",
        "Proposition A",
        "Shall the City of Chesterfield, Missouri, issue its general obligation bonds in the amount of $34,500,000 for the purpose of reconstructing, renovating, improving, furnishing and equipping the City's Aquatic Center?",
    ),
    (
        "29189 CHE-002",
        "City of Chesterfield",
        "Proposition Q",
        "Shall the City of Chesterfield, Missouri impose a local use tax at the same rate as the City's total local sales tax rate with the intent to commit such local use tax for purposes of reconstructing, renovating, improving, furnishing, and equipping the City's Aquatic Center or for purposes of public safety, all subject to applicable state law, provided that if the City's local sales tax rate is reduced or raised by voter approval, the local use tax rate shall also be reduced or raised by the same action?",
    ),
    (
        "29189 CC-001",
        "City of Creve Coeur",
        "Proposition G",
        "Shall the City of Creve Coeur, Missouri, issue its general obligation bonds in an amount not to exceed $14,000,000 for the purpose of demolishing the existing 75-year-old Government Center and designing, constructing, furnishing, and equipping a new Government Center on the same property? If this proposition is approved, the debt service levy of the city is expected to remain unchanged at the current levy of $0.082 per one hundred dollars assessed valuation of real and personal property.",
    ),
    (
        "29189 FLO-001",
        "City of Florissant",
        "Proposition A",
        "Shall Article VIII of the Charter of the City of Florissant, Missouri be amended to remove certain qualifications for the Director of Public Works, as set forth in Exhibit A of Ordinance No. 9254 on file with the City of Florissant City Clerk and the St. Louis County Board of Election Commissioners?",
    ),
    (
        "29189 FLO-001",
        "City of Florissant",
        "Proposition B",
        "Shall Article III of the Charter of the City of Florissant, Missouri be amended to change the requirements for legislative proceedings regarding Resolutions, as set forth in Exhibit A of Ordinance No. 9255 on file with the City of Florissant City Clerk and the St. Louis County Board of Election Commissioners?",
    ),
    (
        "29189 JEF-001",
        "City of Webster Groves",
        "Amendment 1",
        "Shall Article 3 Section 3.1 of the Charter of the City of Webster Groves be amended by removing the residency requirement for the City Manager?",
    ),
    (
        "29189 MER-007",
        "Meramec Valley R-III School District",
        "Proposition K",
        "For the purpose of renovating and improving school buildings and facilities, constructing, equipping, and furnishing a new middle school with storm shelter, installing safety and security updates, and acquiring school buses, shall the Board of Education of the Meramec Valley R-III School District of Franklin, St. Louis, and Jefferson Counties, Pacific, Missouri, issue general obligation bonds in the amount of $45,000,000? Because previous debt is being retired, if this proposition is approved, the debt service levy of the school district is estimated to remain unchanged from the current debt service levy of $0.95 per one hundred dollars of assessed valuation of real and personal property.",
    ),
    (
        "29189 AP-016",
        "Normandy Schools Collaborative",
        "Proposition N",
        "Shall the Joint Executive Governing Board of Normandy Schools Collaborative of St. Louis County, Missouri, be authorized to increase the operating tax levy ceiling by $0.5500 per $100 of assessed valuation according to the 2027 assessment for the purpose of funding teacher attraction and retention and student achievement and other general operating expenses of the District? If this proposition is approved, the adjusted operating levy of the school district per $100 of assessed valuation (using the 2025 tax rates) is estimated to increase by $0.5500 per $100 of assessed valuation according to the 2027 assessment to $3.4085 for residential real property, $4.0412 for commercial real property, $4.4346 for agricultural real property, and $5.2100 for personal property, however, the school district's overall tax levy is expected to remain unchanged due to an expected decrease of $0.5500 in the debt service levy.",
    ),
    (
        "29189 AP-016",
        "Normandy Schools Collaborative",
        "Proposition B",
        "Shall the Joint Executive Governing Board of Normandy Schools Collaborative of St. Louis County, Missouri borrow money in the amount of $40,000,000 for the purpose of acquiring, constructing, renovating, improving, demolishing, furnishing and equipping school sites, buildings, and related facilities, including the purchase of vehicles, and issue general obligation bonds for the payment thereof? If this proposition is approved, the adjusted debt service levy of the school district is anticipated to remain at the current levy of $1.8000 per one hundred dollars of assessed valuation of real and personal property.",
    ),
    (
        "29189 CC-001",
        "Parkway C-2 School District",
        "Proposition P",
        "In order to retain and attract teachers and support staff, maintain safe and secure schools, sustain academic and extracurricular experiences for students, while maintaining the District's financial stability, shall the Board of Education of the Parkway C-2 School District, St. Louis County, Missouri, be authorized to increase the operating tax levy ceiling of the District by $0.46 per $100 of assessed valuation according to the 2027 assessment for general operating expenses of the District? If this proposition is approved, the adjusted operating levy of the school district is estimated to increase from (based on 2025 tax rates) $2.6736 to $3.1336 per $100 of assessed valuation for residential real property, from $4.4173 to $4.8773 for commercial real property, from $3.1038 to $3.5638 for agricultural real property and from $3.7709 to $4.2309 for personal property.",
    ),
    (
        "29189 AP-001",
        "Ritenour School District",
        "Proposition I",
        "For the purposes of updating safety and security systems and classroom technology, improving, maintaining, repairing and renovating school classrooms and buildings, funding other needed upgrades to facilities including energy efficiency improvements and refinancing existing leases for capital improvements, shall the Board of Education of the Ritenour School District, St. Louis County, Missouri, borrow money in the amount of Forty-Two Million Dollars ($42,000,000) and issue general obligation bonds for the payment thereof, resulting in a zero rate change to the debt service property tax levy? If this proposition is approved, the adjusted debt service levy of the District will remain $0.84 per $100 of assessed valuation of real and personal property. (Note: If Propositions I and N are both approved, the District's adjusted debt service levy is estimated to be $0.69 per $100 of assessed valuation of real and personal property in 2027 and $0.54 per $100 of assessed valuation of real and personal property in 2030.)",
    ),
    (
        "29189 AP-001",
        "Ritenour School District",
        "Proposition N",
        "For purposes of paying salaries and benefits to teachers and other district staff, providing classroom educational material including instructional technology, avoiding cutbacks in educational programming, and providing funds for other operating purposes, shall the Board of Education of the Ritenour School District of St. Louis County, Missouri be authorized to increase the operating tax levy ceiling by $0.15 per $100 of assessed valuation in tax year 2027, and by an additional $0.15 per $100 of assessed valuation in tax year 2030? If this proposition is approved, the total levy of the District is expected to remain unchanged due to the expected voluntary reductions to the District's debt service tax levy of $0.15 per $100 of assessed valuation in tax year 2027 and an additional reduction of $0.15 per $100 of assessed valuation in tax year 2030, resulting in the estimated adjusted operating levy of the District per $100 of assessed valuation to be $3.0384 for residential real property, $3.7558 for commercial real property and $4.5491 for personal property in tax year 2027 and $3.1884 for residential real property, $3.9058 for commercial real property and $4.6991 for personal property in tax year 2030.",
    ),
    (
        "29189 CLA-007",
        "Webster Groves School District",
        "Proposition W",
        "Shall the Board of Education of the Webster Groves School District, St. Louis County, Missouri, be authorized to increase the operating levy of the school district by sixty-five cents ($0.65) per one hundred dollars of assessed valuation for the purpose of supporting high quality educational programs for students, attracting and retaining qualified teachers and staff, maintaining competitive salaries and benefits, and paying general operating expenses of the school district? If this proposition is approved, the adjusted operating levy of the school district is estimated to be $4.0448 per one hundred dollars of assessed valuation.",
    ),
    (
        "29095",
        "Jackson County",
        "Question 1",
        "Shall Jackson County continue its anti-crime sales tax, commonly known as COMBAT, at the rate of one-quarter of one cent for a period of ten years for the purpose of promoting and providing public safety within Jackson County, including the prevention and treatment of drug abuse and addiction and the prevention, investigation, prosecution, and detention of violent criminals and drug dealers? The proceeds of this tax shall be deposited in a special Jackson County Anti-Crime Sales Tax Trust Fund, separate from the general fund or any other county funds.",
    ),
    (
        "29095",
        "City of Blue Springs",
        "Question",
        "Shall the City of Blue Springs, Missouri impose a local use tax at the same rate as the total local sales tax rate, currently 2.5%, provided that if the local sales tax rate is reduced or raised by voter approval, the local use tax rate shall also be reduced or raised by the same action? If this question is approved, the City of Blue Springs would begin collecting a use tax from sales made to Blue Springs buyers by online and out of state vendors that are not currently taxed. The funds derived from the use tax will fund streets and other capital improvements.",
    ),
    (
        "29095",
        "City of Independence",
        "General Obligation Bond Question 1",
        "Shall the City of Independence, Missouri issue its general obligation bonds in the amount of $19,900,000 for the purpose of renovating, improving and equipping historic buildings, facilities and grounds, and for the creation of a 20-year business plan for historic buildings, with priority given to the Bingham-Waggoner Estate, the Vaile Mansion, the Truman Memorial Building, the Old Fire Station One, and the 1827 Log Courthouse?",
    ),
    (
        "29095",
        "City of Lee's Summit",
        "Question",
        "Should the municipality of Lee's Summit, Missouri, provide for residential city-wide solid waste collection services?",
    ),
    (
        "29095",
        "Sni Valley Fire Protection District",
        "Question",
        "Shall Sni Valley Fire Protection District impose a sales tax of one percent (1%) for the purpose of providing revenues for the operation of the Sni Valley Fire Protection District and the total property tax levy on properties in the Sni Valley Fire Protection District shall be reduced annually by an amount which reduces property tax revenues by an amount equal to fifty percent of the previous year's revenue collected from this sales tax?",
    ),
]


def fetch() -> None:
    for url, file, _ in BALLOTS.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            if file is None:
                raw.fetch(SOURCE, url, suffix=".pdf")
                continue
            path = MANUAL / file
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def parse() -> None:
    url = BALLOTS["29077"][0]
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows = []
    for match in re.finditer(r"\n\s+Question (\d)\n\s*\n(Shall .*?\?)\n", text, re.DOTALL):
        rows.append(("29077", "City of Springfield", f"Question {match[1]}", re.sub(r"\s+", " ", match[2])))
    rows.extend(LOCAL)
    frame = pl.DataFrame(
        [
            {"ballot": ballot, "entity": entity, "label": label, "wording": wording, "url": BALLOTS[ballot][0]}
            for ballot, entity, label, wording in rows
        ]
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri county and local questions on the November 3, 2026 ballot, with official wording",
            polity="US-MO",
        )
        civil = civmint.Civil(conn, cs, "29", "MO")
        found = civil.found
        slugs = civil.slugs
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for ballot, (url, _, publisher) in BALLOTS.items():
            source = cs.source(publisher, TITLE)
            if source not in sources.values():
                cs.add("source", id=source, kind="official_body", publisher=publisher, title=TITLE, url=url)
            sources[ballot] = source
            cs.artifact(raw.cached(SOURCE, url), source)
        parent = cs.key("us.election", f"{DAY}:MO")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            entity = row["entity"]
            county = row["ballot"][:5]
            county_key = f"us.county:{county}"
            source = sources[row["ballot"]]
            option = None
            if re.match(r"(?i)(city|town|village) of ", entity):
                option = civil.place(re.sub(r"(?i)^(city|town|village) of ", "", entity))
            elif county_key in found and entity == found[county_key][2]:
                option = county_key
            elif re.search(r"(?i)school district|schools collaborative", entity):
                named = [
                    id for id, lea in civil.school_specs if civmint.school_core(lea) == civmint.school_core(entity)
                ]
                option = civil.mint("us.school_district", named[0], entity) if len(named) == 1 else civil.school(entity)
            elif f"special:{county}:{entity}" in found:
                option = f"special:{county}:{entity}"
            elif county_key in found:
                held = found[county_key]
                minted = govmint.special(conn, cs, "MO", (held[0], held[1], held[3], held[4]), entity, slugs)
                if minted is not None:
                    option = f"special:{county}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:MO:{code}")
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
                    status="held" if DAY <= date.today() else "scheduled",
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
            contest_key = f"{DAY}:MO:{code}:measure:{slug(label)}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{slug(label)}"
            if slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            slugs[contest_path] = contest
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
