import html
import re
import subprocess
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import civmint, govmint
from ea.store import DATA, connect

SOURCE = "us.ia_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Public measures, November 3, 2026 general election"
LOCAL = {
    "henry": DATA / "inbox" / "y26" / "ia" / "counties" / "henry.pdf",
    "feed": DATA / "inbox" / "y26" / "ia" / "feed_127077.zip",
}
LINN = "19113"
SOURCES = {
    "linn": (
        "https://gis.linncountyiowa.gov/web-data/elections/year/2026/20261103GE/all.pdf",
        "Linn County Auditor",
        ".pdf",
    ),
    "johnson": (
        "https://www.johnsoncountyiowa.gov/november-3-2026-general-election",
        "Johnson County Auditor",
        ".html",
    ),
    "desmoines": (
        "https://dmcountyelections.iowa.gov/elections/info/2026_general_election_2026_11_03/",
        "Des Moines County Auditor",
        ".html",
    ),
    "jefferson": (
        "https://jeffersoncounty.iowa.gov/wp-content/uploads/notice-general-2026.pdf",
        "Jefferson County Auditor",
        ".pdf",
    ),
    "keokuk": (
        "https://keokukcounty.iowa.gov/files/public_notice/2026_general_election_56361.pdf",
        "Keokuk County Auditor",
        ".pdf",
    ),
    "butler": (
        "https://butlercounty.iowa.gov/images/docs/departments/elections/CANDIDATE%20FILINGS-to%20ADKINS-2026.pdf",
        "Butler County Auditor",
        ".pdf",
    ),
    "bremer": (
        "https://bremercountyelections.iowa.gov/files/candidate_list/2026_general_election_81883.pdf",
        "Bremer County Auditor",
        ".pdf",
    ),
    "paloalto": (
        "https://paloaltocounty.iowa.gov/wp-content/uploads/2026/09/2026-GENERAL-ELECTION-SAMPLE-BALLOTS.pdf",
        "Palo Alto County Auditor",
        ".pdf",
    ),
    "henry": (
        "https://cap.gmdsolutions.com/viewfile.aspx?id=3669F870D52E468B857E7B006B2DE581&dir=Objects",
        "Henry County Auditor",
        ".pdf",
    ),
    "warren": (
        "https://www.warrencountyia.gov/wp-content/uploads/2026/09/Federal-State-County-Candidates-Public-Measures-General-110326.pdf",
        "Warren County Auditor",
        ".pdf",
    ),
    "cedar": (
        "https://cedarcounty.iowa.gov/files/auditor/2026_candidate_list_89671.pdf",
        "Cedar County Auditor",
        ".pdf",
    ),
    "osceola": (
        "https://osceolacountyia.gov/wp-content/uploads/2026/10/2026-General-Election-Notice.pdf",
        "Osceola County Auditor",
        ".pdf",
    ),
    "pocahontas": (
        "https://pocahontascounty.iowa.gov/wp-content/uploads/2026-General-Election-Sample-Ballots.pdf",
        "Pocahontas County Auditor",
        ".pdf",
    ),
    "allamakee": (
        "https://allamakeecountyelections.iowa.gov/files/candidate_list/2026_general_election_76436.pdf",
        "Allamakee County Auditor",
        ".pdf",
    ),
    "plymouth": ("https://plymouthcountyiowa.gov/elections", "Plymouth County Auditor", ".pdf"),
    "ringgold": (
        "https://ringgoldcountyelections.iowa.gov/files/candidate_list/november_3_2026_50330.pdf",
        "Ringgold County Auditor",
        ".pdf",
    ),
    "dallas": ("https://www.dallascountyiowa.gov/DocumentCenter/View/7620", "Dallas County Auditor", ".pdf"),
    "greene_churdan": ("https://www.greenecounty.iowa.gov/docview.aspx?docid=50371", "Greene County Auditor", ".pdf"),
    "greene_dana": ("https://www.greenecounty.iowa.gov/docview.aspx?docid=50364", "Greene County Auditor", ".pdf"),
    "greene_grandjunction": (
        "https://www.greenecounty.iowa.gov/docview.aspx?docid=50365",
        "Greene County Auditor",
        ".pdf",
    ),
    "greene_jefferson": ("https://www.greenecounty.iowa.gov/docview.aspx?docid=50369", "Greene County Auditor", ".pdf"),
    "greene_paton": ("https://www.greenecounty.iowa.gov/docview.aspx?docid=50372", "Greene County Auditor", ".pdf"),
    "greene_rippey": ("https://www.greenecounty.iowa.gov/docview.aspx?docid=50366", "Greene County Auditor", ".pdf"),
    "greene_scranton": ("https://www.greenecounty.iowa.gov/docview.aspx?docid=50376", "Greene County Auditor", ".pdf"),
    "grundy": (
        "https://www.grundycountyiowa.gov/images/documents/announcements/NOTICE%20OF%20ELECTION--General%202026.pdf",
        "Grundy County Auditor",
        ".pdf",
    ),
    "woodbury": (
        "https://elections.woodburycountyiowa.gov/files/sample_ballots/20261103_precinct_30_sgt_bluff_west_24965.pdf",
        "Woodbury County Auditor",
        ".pdf",
    ),
    "winneshiek_decorah": (
        "https://winneshiekcounty.iowa.gov/wp-content/uploads/2026/09/PCT-3-Decorah-Ward-1.pdf",
        "Winneshiek County Auditor",
        ".pdf",
    ),
    "winneshiek_spillville": (
        "https://winneshiekcounty.iowa.gov/wp-content/uploads/2026/09/PCT-11-City-of-Spillville.pdf",
        "Winneshiek County Auditor",
        ".pdf",
    ),
    "buchanan": (
        "https://www.buchanancounty.iowa.gov/2026%20General%20Ballot%20Notice%20for%20Papers.pdf",
        "Buchanan County Auditor",
        ".pdf",
    ),
    "jasper_pcm": (
        "https://jaspercountyelections.iowa.gov/files/sample_ballots/1201_pcm_des_moines_township_33654.pdf",
        "Jasper County Auditor",
        ".pdf",
    ),
    "feed": (
        "https://electionresults.iowa.gov/IA/127077/382865/reports/detailxml.zip",
        "Iowa Secretary of State",
        ".zip",
    ),
}
BORROWED = ("warren", "cedar", "osceola", "pocahontas", "allamakee", "plymouth", "ringgold", "dallas")
LAYOUT = ("linn", "jefferson", "keokuk", "butler")
PAGES = ("johnson", "desmoines")
TOWNSHIP = (
    "Should the Palo Alto County Board of Supervisors fill the offices of Trustee and Clerk of {} Township by "
    "appointment as the terms of the office of the incumbent township officers expires?"
)
LOST = (
    "Shall the following Public Measure be adopted? A local option sales and services tax shall be imposed in the City "
    "of {} at the Rate of 1% to be effective July 1, 2027, with no sunset. Revenues from the local option sales and "
    "services tax shall be allocated as follows: {}"
)
SCANNED = (
    (
        "paloalto",
        "A",
        "Emmetsburg Community School District",
        (
            "Shall the Board of Directors of the Emmetsburg Community School District in the County of Palo Alto, State "
            "of Iowa, be authorized to contract indebtedness and issue General Obligation Bonds in an amount not to "
            "exceed $25,000,000, to provide funds to build, furnish, and equip a new elementary school, including site "
            "acquisition and site improvements, and for partial demolition of the existing elementary school, and to "
            "repair and improve the remaining portion of the existing elementary building, including site improvements?"
        ),
    ),
    ("paloalto", "B", "Fairfield Township", TOWNSHIP.format("Fairfield")),
    ("paloalto", "C", "Silver Lake Township", TOWNSHIP.format("Silver Lake")),
    ("paloalto", "D", "West Bend Township", TOWNSHIP.format("West Bend")),
    (
        "paloalto",
        "HF",
        "North Union Community School District",
        (
            "Shall the Board of Directors of the North Union Community School District in the Counties of Emmet, "
            "Kossuth and Palo Alto, State of Iowa, be authorized to contract indebtedness and issue General Obligation "
            "Bonds in an amount not to exceed $12,000,000 to provide funds to build, furnish, and equip additions to "
            "the school building, including a gymnasium, multipurpose room, and related spaces, and to improve the site?"
        ),
    ),
    (
        "henry",
        "US",
        "Van Buren County Community School District",
        (
            "Shall the Board of Directors of the Van Buren County Community School District in the Counties of Van "
            "Buren, Davis, Henry and Lee, State of Iowa, be authorized to issue approximately $13,800,000 School "
            "Infrastructure Sales, Services and Use Tax Revenue and Refunding Bonds, which may be issued in one or more "
            "series over multiple fiscal years pursuant to Iowa Code Section 423F.2 and 423E.5, in the approximate "
            "amount of $13,800,000 for the purpose of providing funds to build, furnish, and equip an addition to the "
            "Keosauqua campus, including a secure entrance, classrooms, commons, and office space, and to improve the "
            "site; and to currently refund the outstanding School Infrastructure Sales, Services and Use Tax Revenue "
            "Bonds, Series 2022A, dated July 15, 2022, including costs of issuance and a debt service reserve fund if "
            "required by the purchaser; with any bond proceeds remaining after completion of this project used for other "
            "school infrastructure projects as authorized by the School District's Revenue Purpose Statement?"
        ),
    ),
    (
        "warren",
        "Z",
        "Interstate 35 Community School District",
        (
            "Shall the Board of Directors of the Interstate 35 Community School District in the Counties of Clarke, "
            "Madison, and Warren, State of Iowa, be authorized to contract indebtedness and issue General Obligation "
            "Bonds in an amount not to exceed $12,300,000 to provide funds to remodel, repair, improve, and equip the "
            "school building, including reconfiguration of academic spaces, HVAC, mechanical and exterior improvements; "
            "to build, furnish, and equip athletic facilities, including a new stadium and track, parking, and site "
            "improvements, and for related improvements to repurpose the existing track and field, bleachers, and press "
            "box; and for site and access improvements to the school campus?"
        ),
    ),
    (
        "warren",
        "A",
        "Norwalk Community School District",
        (
            "Shall the Board of Directors of the Norwalk Community School District, in Warren County, State of Iowa, be "
            "authorized to contract indebtedness and issue general obligation bonds in an amount not exceeding "
            "$65,000,000 for the purpose of acquiring, furnishing, equipping, constructing, improving, repairing, and "
            "renovating school buildings and improving the sites of the School District's school buildings and athletic "
            "facilities located in Norwalk, Iowa, including without limitation: (a) a new elementary school building and "
            "site/land improvements; (b) HVAC system replacement and improvements, kitchen ventilation system "
            "improvements, and other mechanical or electrical upgrades to Oviatt Elementary School, Lakewood Elementary "
            "School, and Secondary Campus; (c) building improvements and upgrades to enhance security at all existing "
            "school buildings, including security systems and enhanced building safety measures; (d) a new "
            "transportation building, including maintenance bays, a wash bay, a custodial room, offices, common areas, "
            "and site/land improvements; and (e) improving athletic facilities and related parking areas, including "
            "construction of a new practice field and building improvements and expansions for additional practice "
            "areas, offices, storage space, common areas, and related site improvements?"
        ),
    ),
    (
        "cedar",
        "A",
        "Tipton Community School District",
        (
            "Shall the Board of Directors of the Tipton Community School District in the County of Cedar, State of Iowa, "
            "be authorized to contract indebtedness and issue General Obligation Bonds in an amount not to exceed "
            "$16,100,000 to provide funds to build, furnish, and equip additions to the high school, including a secure "
            "entrance, gymnasium, and commons area; to remodel, repair, improve, and equip the high school, including "
            "educational and administrative spaces; and to improve the site, including parking?"
        ),
    ),
    (
        "osceola",
        "NV",
        "George-Little Rock Community School District",
        (
            "Shall the Board of Directors of the George-Little Rock Community School District in the Counties of Lyon, "
            "Osceola and Sioux, State of Iowa, be authorized to contract indebtedness and issue General Obligation "
            "Bonds in an amount not to exceed $14,950,000 to provide funds to build, furnish and equip additions to its "
            "Elementary School and Middle School/ High School facilities, including related remodeling and improvements; "
            "to remodel, repair, and improve the Elementary School and Middle School/ High School facilities, including "
            "HVAC and bathroom improvements, and site improvements, exterior improvements to the Middle School / High "
            "School; to build, furnish, and equip a new bus barn; and to remodel, repair, and improve the Middle School "
            "/ High School track?"
        ),
    ),
    (
        "pocahontas",
        "RQ",
        "Pocahontas Area Community School District",
        (
            "Shall the Board of Directors of the Pocahontas Area Community School District in the Counties of "
            "Pocahontas, Calhoun and Palo Alto, State of Iowa, be authorized to contract indebtedness and issue "
            "General Obligation Bonds in an amount not to exceed $20,000,000 to provide funds to remodel, repair, "
            "improve, and equip the middle/high school and elementary school, including career and technical education "
            "and classroom spaces; to build, furnish, and equip additions to the middle/high school and elementary "
            "school, including site improvements; for playground improvements at the elementary school; and to build, "
            "furnish, and equip a central office and regional learning center building and a transportation facility, "
            "including site improvements?"
        ),
    ),
    (
        "allamakee",
        "AM",
        "City of Waukon",
        (
            "Shall Chapter 22 Library Board of Trustees of the City of Waukon Code of Ordinances be amended as "
            "follows: Amending the residency requirements of Trustees, which is currently eight resident members and "
            "one nonresident member, to seven resident members, one nonresident member, and one member that could "
            "either be a resident or nonresident member, where resident members would continue to be appointed by the "
            "City Council with any nonresident members continuing to be appointed by the Allamakee County Board of "
            "Supervisors, with Library Trustee terms to be reduced from six (6) years to three (3) years?"
        ),
    ),
    (
        "plymouth",
        "RC",
        "City of Le Mars",
        (
            "Shall the City of Le Mars, in Plymouth County, State of Iowa, enter into a loan agreement and issue "
            "general obligation bonds in an amount not exceeding $8,000,000 for the purpose of paying the cost, to "
            "that extent, of constructing, furnishing and equipping a municipal emergency services building?"
        ),
    ),
    (
        "plymouth",
        "RD",
        "Hinton Community School District",
        (
            "Shall the Board of Directors of the Hinton Community School District in the County of Plymouth, State of "
            "Iowa, be authorized to contract indebtedness and issue General Obligation Bonds in an amount not to "
            "exceed $8,600,000 to provide funds to remodel, repair, improve, furnish and equip its middle / high "
            "school facility, including site improvements?"
        ),
    ),
    (
        "ringgold",
        "GB",
        "Lamoni Community School District",
        (
            "Shall the Board of Directors of the Lamoni Community School District in the Counties of Decatur and "
            "Ringgold, State of Iowa, be authorized to contract indebtedness and issue General Obligation Bonds in an "
            "amount not to exceed $5,750,000 to be used together with sales tax revenue bonds to provide funds to "
            "build, furnish and equip an addition to its Elementary School, with related remodeling and improvements "
            "and related site improvements; to remodel, repair, improve, furnish, and equip the Elementary School, "
            "including HVAC, mechanical, electrical, and plumbing improvements; to remodel, repair, and improve the "
            "Middle School, including exterior façade restoration; and to remodel, repair, improve, and equip the High "
            "School, including fire suppression, HVAC improvements and a new secured entry?"
        ),
    ),
    (
        "dallas",
        "U",
        "City of Urbandale",
        (
            "Shall the City of Urbandale, State of Iowa, issue its General Obligation Bonds or Capital Loan Notes in "
            "an amount not exceeding the amount of $25,000,000 for the purpose of paying costs of designing, "
            "constructing, equipping, and furnishing a new police station, with related site improvements?"
        ),
    ),
    (
        "dallas",
        "BQ",
        "Madrid Community School District",
        (
            "Shall the Board of Directors of the Madrid Community School District in the counties of Boone, Dallas, "
            "and Polk, State of Iowa, be authorized to contract indebtedness and issue General Obligation Bonds in an "
            "amount not to exceed $4,300,000 to provide funds to build, furnish and equip additions to its existing "
            "Junior-Senior High School, including a Career Academy addition, with related remodeling and improvements "
            "and site improvements?"
        ),
    ),
    (
        "greene_churdan",
        "IM",
        "City of Churdan",
        LOST.format(
            "Churdan",
            "25% for property tax relief and 75% for any lawful corporate purpose of the city as determined by the City Council.",
        ),
    ),
    ("greene_dana", "IN", "City of Dana", LOST.format("Dana", "100% for property tax relief.")),
    (
        "greene_grandjunction",
        "II",
        "City of Grand Junction",
        LOST.format(
            "Grand Junction",
            "100% of said local option sales and service tax to be allocated for any costs associated with property tax relief.",
        ),
    ),
    (
        "greene_jefferson",
        "IJ",
        "City of Jefferson",
        LOST.format(
            "Jefferson", "100% for the repair, maintenance, replacement and improvement of Jefferson’s infrastructure."
        ),
    ),
    (
        "greene_paton",
        "IK",
        "City of Paton",
        LOST.format(
            "Paton", "100% for the repair, maintenance, replacement and improvement of Paton's infrastructure."
        ),
    ),
    (
        "greene_rippey",
        "IL",
        "City of Rippey",
        LOST.format(
            "Rippey",
            "100% of said local options sales services tax to be allocated for any costs associated with property tax relief.",
        ),
    ),
    (
        "greene_scranton",
        "GY",
        "City of Scranton",
        LOST.format(
            "Scranton",
            "20% of said local option sales and services tax shall be allocated for any costs associated with operating "
            "expenses of the Scranton Public Library. 30% of said local option sales and services tax shall be allocated "
            "for any costs associated with the general expenses of the City of Scranton. 30% of said local option sales "
            "and services tax shall be allocated for any costs associated with the public works department of the City "
            "of Scranton. 20% said local option sales and services tax shall be allocated for any costs associated with "
            "the water and sewer expenses of the City of Scranton.",
        ),
    ),
    (
        "grundy",
        "IO",
        "City of Dike",
        (
            "Shall the City of Dike, State of Iowa, issue its General Obligation Bonds or Capital Loan Notes in an amount "
            "not exceeding the amount of $1,500,000 for the purpose of paying costs of the acquisition, construction, "
            "demolition, reconstruction, enlargement, improvement, and equipping of swimming pool and pool facilities, "
            "including parking lot and related site improvements?"
        ),
    ),
    (
        "grundy",
        "IP",
        "City of Reinbeck",
        (
            "Shall the City of Reinbeck, in Grundy County, State of Iowa, enter into a loan agreement and issue general "
            "obligation bonds in an amount not exceeding $4,000,000 for the purpose of paying the cost, to that extent, of "
            "constructing, furnishing and equipping a new municipal fire station?"
        ),
    ),
    (
        "woodbury",
        "WD",
        "Sergeant Bluff-Luton Community School District",
        (
            "Shall the Board of Directors of the Sergeant Bluff-Luton Community School District in the County of "
            "Woodbury, State of Iowa, be authorized to contract indebtedness and issue General Obligation Bonds in an "
            "amount not to exceed $38,000,000 to provide funds to build, furnish, and equip a new Primary School building "
            "as an addition to the Elementary building (including demolition of the existing Primary building) and "
            "improve the site?"
        ),
    ),
    (
        "winneshiek_decorah",
        "WD",
        "City of Decorah",
        (
            "Shall the following Public Measure be adopted? Shall the City of Decorah, State of Iowa, issue its General "
            "Obligation Bonds or Capital Loan Notes in an amount not exceeding the amount of $20,900,000, in one or "
            "more series, for the purpose of paying costs of designing, constructing, reconstructing, enlarging, "
            "improving, equipping and furnishing municipal facilities including a new fire station and renovation of "
            "city hall, with related site improvements?"
        ),
    ),
    (
        "winneshiek_spillville",
        "WE",
        "City of Spillville",
        (
            "Shall the following Public Measure be adopted? Summary: To authorize a change in the use of the one "
            "percent (1%) local sales and services tax in the City of Spillville effective July 1, 2027. The use of the "
            "one percent (1%) local option sales and services tax shall be changed in the City of Spillville, "
            "effective July 1, 2027. Revenues from the local option sales and services tax (LOSST) shall be allocated "
            "as follows: Zero percent (0%) for property tax relief, One Hundred percent (100%) will be allocated "
            "annually by the City Council, for any lawful purpose, including but not limited to the following "
            "purposes: Capital equipment purchases and repairs, street, water, sewer and other capital improvement and "
            "maintenance projects, and/or for assistance to the Bily Clocks Museum and Antonin Dvorak Exhibit."
        ),
    ),
    (
        "buchanan",
        "CD",
        "City of Independence",
        (
            "Shall the City of Independence, in Buchanan County, State of Iowa, enter into a loan agreement and issue "
            "general obligation bonds in an amount not exceeding $14,500,000 for the purpose of paying the cost, to "
            "that extent, of undertaking fire protection facilities improvements, including the expansion, "
            "renovation, furnishing and equipping of existing facilities and the acquisition and improvement of real "
            "estate for the development of additional facilities?"
        ),
    ),
    (
        "buchanan",
        "CE",
        "Independence Community School District",
        (
            "Shall the Board of Directors of the Independence Community School District in the Counties of Buchanan "
            "and Benton, State of Iowa, be authorized to contract indebtedness and issue General Obligation Bonds in "
            "an amount not to exceed $24,990,000 to provide funds to build, furnish, and equip additions to East and "
            "West Elementary Schools; and to remodel, repair, improve, and equip East and West Elementary Schools and "
            "to improve the sites, including related demolition at West Elementary?"
        ),
    ),
    (
        "jasper_pcm",
        "LL",
        "PCM Community School District",
        (
            "Shall the Board of Directors of the PCM Community School District in the Counties of Jasper, Polk, and "
            "Marion, State of Iowa, be authorized to contract indebtedness and issue General Obligation Bonds in an "
            "amount not to exceed $14,195,000 to provide funds to remodel, repair, improve, and equip the Monroe "
            "campus, including classroom and career and technical education spaces; to build, furnish, and equip an "
            "addition to the Monroe campus, including a multipurpose performance gymnasium, support spaces, and site "
            "improvements; and to improve the Prairie City campus site, including playgrounds and parking?"
        ),
    ),
)
SHALL = re.compile(r"Public Measure ([A-Z]{1,3})\b[^?]{0,140}?(Shall\b[^?]+\?)")
BREMER = re.compile(r"District - ([A-Z]{1,3})\s+(Shall\b[^?]+\?)")


def fetch() -> None:
    for key, (url, _, suffix) in SOURCES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            if key in BORROWED:
                artifact = raw.cached("us.ia_districts_2026", url)
                content = artifact.path.read_bytes()
                retrieved = artifact.retrieved_at
            elif key in LOCAL:
                content = LOCAL[key].read_bytes()
                retrieved = datetime.fromtimestamp(LOCAL[key].stat().st_mtime, UTC)
            else:
                content = subprocess.run(
                    [
                        "curl",
                        "-sSfL",
                        "-m",
                        "120",
                        "-A",
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
                        url,
                    ],
                    capture_output=True,
                    check=True,
                ).stdout
                retrieved = datetime.now(UTC)
            raw.store(
                SOURCE,
                url,
                content,
                suffix=suffix,
                retrieved_at=retrieved,
                media_type="application/pdf" if suffix == ".pdf" else "text/html",
            )


def source_text(key: str) -> str:
    path = raw.cached(SOURCE, SOURCES[key][0]).path
    if path.suffix == ".pdf":
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
    else:
        text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", path.read_text())
        text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    return re.sub(r"\s+", " ", text)


def feed_measures() -> list[tuple[str, str]]:
    with zipfile.ZipFile(raw.cached(SOURCE, SOURCES["feed"][0]).path) as archive:
        text = archive.read("detail.xml").decode()
    found = []
    for title in re.findall(r'<Contest key="\d+" text="([^"]+)"', text):
        match = re.fullmatch(r"(.+?) - Public Measure ([A-Z]{1,3})", html.unescape(title))
        if match is None:
            continue
        name = match[1]
        if name.startswith("City of "):
            entity = name
        elif name.endswith(" School"):
            entity = f"{name.removesuffix(' School')} Community School District"
        else:
            continue
        found.append((match[2], entity))
    return found


def entity_of(wording: str) -> str:
    patterns = (
        (r"^Shall the City of ([^,]+),", "City of {}"),
        (r"^Shall the County of ([^,]+),", "{} County"),
        (r"^Shall the Board of Directors of ?the ?(.+? School District) in", "{}"),
        (r"^Shall (.+? Community College) \(", "{}"),
    )
    for pattern, template in patterns:
        match = re.search(pattern, wording)
        if match is not None:
            return template.format(match[1])
    raise ValueError(wording)


def topic(entity: str, wording: str) -> str:
    if "Township" in entity:
        return "Appoint Township Trustee and Clerk"
    if "local option sales and services tax" in wording.lower():
        return "Local Option Sales and Services Tax"
    if "revenue" in wording.lower() and "bonds" in wording.lower() and "School Infrastructure" in wording:
        return "School Infrastructure Revenue Bonds"
    return "General Obligation Bonds" if "bonds" in wording.lower() else "Question"


def parse() -> None:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    cited: dict[tuple[str, str], list[str]] = {}

    def add(key: str, code: str, wording: str, entity: str | None = None) -> None:
        wording = re.sub(r"\s+", " ", wording).strip()
        entity = entity or entity_of(wording)
        rows.setdefault(
            (code, entity),
            {
                "code": code,
                "entity": entity,
                "label": f"Public Measure {code}",
                "topic": topic(entity, wording),
                "wording": wording,
                "source": key,
                "url": SOURCES[key][0],
            },
        )
        cited.setdefault((code, entity), []).append(key)

    for key in (*LAYOUT, *PAGES):
        for match in SHALL.finditer(source_text(key)):
            add(key, match[1], match[2])
    for match in BREMER.finditer(source_text("bremer")):
        add("bremer", match[1], match[2])
    for key, code, entity, wording in SCANNED:
        add(key, code, wording, entity)
    for code, entity in feed_measures():
        lead = entity.removeprefix("City of ").split()[0]
        if not any(held == code and lead in name for held, name in rows):
            add("feed", code, "", entity)
    frame = [{**row, "sources": ",".join(dict.fromkeys(cited[key]))} for key, row in rows.items()]
    PARSED.mkdir(parents=True, exist_ok=True)
    result = pl.DataFrame(frame)
    result.write_parquet(PARSED / "measures.parquet")
    print(result.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa city, county, school, community college, and township public measures on the November 3, 2026 ballot, with official wording from county auditors' notices and sample ballots",
            polity="US-IA",
        )
        civil = civmint.Civil(conn, cs, "19", "IA")
        found = civil.found
        system = cs.key("us.electoral_system", "referendum")
        source_ids: dict[str, UUID] = {}
        added: set[UUID] = set()
        for key, (url, publisher, _) in SOURCES.items():
            source_ids[key] = cs.source(publisher, TITLE)
            if source_ids[key] not in added:
                added.add(source_ids[key])
                cs.add("source", id=source_ids[key], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), source_ids[key])
        parent = cs.key("us.election", f"{DAY}:IA")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            entity = row["entity"]
            option = None
            if entity.startswith("City of "):
                option = civil.place(entity.removeprefix("City of "))
            elif entity.endswith(" County"):
                option = next(
                    (key for key, value in found.items() if key.startswith("us.county:") and value[2] == entity), None
                )
            elif entity.endswith(" Township"):
                option = civil.town(entity, "19147")
            elif "School District" in entity:
                option = civil.school(entity)
                if option is None:
                    lea = entity.replace(" Community ", " Comm ")
                    leaids = [leaid for leaid, name in civil.school_specs if name == lea]
                    if len(leaids) == 1:
                        option = civil.mint("us.school_district", leaids[0], entity)
            elif entity == "Kirkwood Community College":
                county_key = f"us.county:{LINN}"
                if county_key in found:
                    county, county_area, _, county_path, county_code = found[county_key]
                    minted = govmint.special(
                        conn, cs, "IA", (county, county_area, county_path, county_code), entity, civil.slugs, "college"
                    )
                    if minted is not None:
                        option = f"special:{LINN}:{entity}"
                        found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:IA:{code}")
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
            label = f"{row['label']}: {row['topic']}"
            contest_key = f"{DAY}:IA:{code}:measure:{slug(row['label'])}"
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
            for position, answer in enumerate(("Yes", "No")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{slug(answer)}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            for key in row["sources"].split(","):
                cs.add("citation", entity_id=contest, source_id=source_ids[key])
            if row["wording"]:
                cs.add(
                    "ballot_question_text",
                    contest_id=contest,
                    kind="text",
                    lang="en",
                    body=row["wording"],
                    url=row["url"],
                    source_id=source_ids[row["source"]],
                )
        print(f"{kept} of {frame.height} measures")
        return cs.write()
