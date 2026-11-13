import json
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import civmint, govmint
from ea.sources.us.local_2026 import live_key
from ea.store import DATA, connect

SOURCE = "us.ar_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Notice of General Election, November 3, 2026: local measures"
GARLAND = "https://garlandcountyvote.org/uploads/6/3/0/4/63045401/local_issues.pdf"
SALINE = "https://salinecounty.org/Documents/Government/County%20Clerk/Elections/2026%20Elections/Election%20Notice%20for%20General%20Election%202026.pdf"
PULASKI = "https://votepulaskiar.gov/wp-content/uploads/11032026_Notice-of-Election_v1.pdf"
FEED = "https://results.tally-enr.com/results/arkansas/11b07106-031e-4d7c-911a-d01fb473d6dd/FullDataFile.json"
FEED_TITLE = "Arkansas General Election, November 3, 2026: results by contest"
LIVE = DATA / "live" / "raw" / "us.enr_2026.ar"
COUNTIES = {
    "05051": (GARLAND, "Garland County Election Commission"),
    "05119": (PULASKI, "Pulaski County Election Commission"),
    "05125": (SALINE, "Saline County Clerk"),
}
ROWS = [
    {
        "county": "05051",
        "entity": "Garland County",
        "label": "Issue No. 5: Library Millage",
        "wording": "VOTE FOR or AGAINST the levy of a nine tenths of one (.9) mill ad valorem tax on the assessed value of all taxable real and personal property within the County of Garland, Arkansas, with the nine tenths of one (.9) mill tax to be pledged as a continuing debt service tax to the repayment of an issue or issues of bonds not to exceed Thirty-Four Million Eight Hundred Thousand and NO/100 Dollars ($34,800,000) in aggregate principal amount, to finance the acquiring, constructing and equipping of land and additional capital improvements for the public libraries owned and operated by the Garland County Library Board and to authorize the issuance of the bonds on such terms and conditions as shall be approved by the County.",
        "answers": ("For", "Against"),
    },
    {
        "county": "05051",
        "entity": "Garland County",
        "label": "Issue No. 6: 0.625% County Road Tax Renewal",
        "wording": 'Vote FOR REFERRED ORDINANCE NO. O-26-07 or AGAINST REFERRED ORDINANCE NO. O-26-07. COUNTY-WIDE 0.625% SALES AND USE TAX. The continuation of the levy of 0.625% local sales and use Tax within the County to finance all or a portion of the costs of improvements to existing, roads, streets, and related structures, including particularly, without limitation, overpasses, underpasses, bridges and sidewalks, and any necessary land, easements, rights of way and related intersection improvements, drainage and traffic control devices and improvements therefor (collectively, the "Improvements"). If the question is approved, there will be a continuation of the existing levy within the County of 0.625% sales and use tax, the net collections of which after the State of Arkansas deducts its administrative charges and required rebates will be divided per capita among the County and the Municipalities within in the County pursuant to state law. The term of the sales and use tax levy shall be July 1, 2027, through June 30, 2032.',
        "answers": ("For", "Against"),
    },
    {
        "county": "05051",
        "entity": "City of Hot Springs",
        "label": "Issue No. 7: Hot Springs Sunday Alcohol Sales",
        "wording": "FOR THE SALE OF ALCOHOLIC BEVERAGES FOR OFF-PREMISE CONSUMPTION ON SUNDAY WITHIN THE CITY OF HOT SPRINGS, ARKANSAS BETWEEN THE HOURS OF 10:00 A.M. AND 12:00 MIDNIGHT, AS AUTHORIZED BY LAW. AGAINST THE SALE OF ALCOHOLIC BEVERAGES FOR OFF-PREMISE CONSUMPTION ON SUNDAY WITHIN THE CITY OF HOT SPRINGS, ARKANSAS BETWEEN THE HOURS OF 10:00 A.M. AND 12:00 MIDNIGHT, AS AUTHORIZED BY LAW.",
        "answers": ("For", "Against"),
    },
    {
        "county": "05051",
        "entity": "City of Diamondhead",
        "label": "Issue No. 8: Diamondhead Annexation",
        "wording": "On the question of Annexation of Contiguous Territory in Hot Spring County into the City of Diamondhead as described in City Ordinance O2026-007.",
        "answers": ("For", "Against"),
    },
    {
        "county": "05119",
        "entity": "City of Jacksonville",
        "label": "Jacksonville Ward-Based Representation Initiative Ordinance",
        "kind": "initiative",
        "wording": "An initiative ordinance of The People of the City of Jacksonville to abolish the at-large voting system, establish a ward-based voting system, provide for the election of two qualified ward representatives of the City Council to represent each ward, and set forth minimum qualifications for qualified ward representatives on the City Council.",
        "answers": ("For", "Against"),
    },
    {
        "county": "05125",
        "entity": "Saline County",
        "label": "Issue No. 5: Library Millage",
        "wording": "Saline County Ordinance No. 2026-8 Regarding Library Millage. VOTE FOR OR AGAINST REDUCING THE EXISTING LIBRARY AD VALOREM TAX LEVIED FROM THE EXISTING ONE AND SEVEN TENTHS (1.7) MILLS TO A NEW RATE OF ONE AND ONE TENTHS (1.1) MILLS ON THE DOLLAR OF ASSESSED VALUATION OF ALL REAL AND PERSONAL PROPERTY SUBJECT TO TAXATION IN THE COUNTY OF SALINE, ARKANSAS",
        "answers": ("For", "Against"),
    },
]


FEED_ROWS = [
    (
        "05071",
        "City of Clarksville",
        "Question One: Refunding Bonds",
        "Question One (Refunding Bonds) Clarksville",
        None,
    ),
    (
        "05071",
        "City of Clarksville",
        "Question Two: Wastewater Treatment Plant Improvement Bonds",
        "Question Two (Wastewater Treatment Plant Improvement Bonds) Clarksville",
        None,
    ),
    (
        "05071",
        "City of Clarksville",
        "Question Three: Parks and Recreation Improvement Bonds",
        "Question Three (Parks and Recreation Improvement Bonds) Clarksville",
        None,
    ),
    ("05031", "City of Brookland", "Refunding Bonds", "City of Brookland, Refunding Bonds", None),
    (
        "05031",
        "City of Brookland",
        "Municipal Building Improvement Bonds",
        "City of Brookland, Mun. Building Imprv. Bonds",
        None,
    ),
    ("05031", "City of Brookland", "Park Improvement Bonds", "City of Brookland, Park Improvement Bonds", None),
    ("05031", "City of Brookland", "Sewer Improvement Bonds", "City of Brookland, Sewer Improvement Bonds", None),
    ("05031", "City of Brookland", "Street Improvement Bonds", "City of Brookland, Street Improvement Bonds", None),
    (
        "05031",
        "City of Brookland",
        "Emergency Facilities Improvement Bonds",
        "City of Brookland, Emerg. Facil. Imprv. Bonds",
        None,
    ),
    (
        "05031",
        "Craighead County",
        "County Ordinance: Combine the Offices of Treasurer and Collector",
        "County Ordinance",
        None,
    ),
    (
        "05141",
        "City of Fairfield Bay",
        "Alcoholic Beverage Sunday Sales",
        "City of Fairfield Bay Alcoholic Beverage Sunday Sales",
        None,
    ),
    (
        "05141",
        "City of Shirley",
        "Alcoholic Beverage Sunday Sales",
        "City of Shirley Alcoholic Beverage Sunday Sales City of Shirley",
        None,
    ),
    ("05009", "Boone County", "Sales and Use Tax", "Boone County, Sales and Use Tax", None),
    ("05009", "City of Harrison", "Local Sales and Use Tax", "City of Harrison, Local Sales and Use Tax", None),
    ("05093", "City of Blytheville", "Refunding Bonds", "Refunding Bonds Blytheville", None),
    (
        "05093",
        "City of Blytheville",
        "Ordinance No. 2107: Sales and Use Tax Extension",
        "Ordinance No. 2107 Blytheville",
        None,
    ),
    (
        "05093",
        "City of Blytheville",
        "Ordinance 1: Firefighting Improvement Bonds",
        "FIREFIGHTING IMPROVEMENT BONDS ORDINANCE 1",
        None,
    ),
    (
        "05093",
        "City of Wilson",
        "Ordinance No. 2024-02: Four-Year Aldermen Terms",
        "Ordinance No. 2024-02 Wilson",
        None,
    ),
    ("05137", "Town of Fifty Six", "2% Sales and Use Tax", "2% SALES AND USE TAX Town of Fifty Six", None),
    ("05105", "Town of Casa", "Sales Tax Levy", "CITY OF CASA SALES TAX LEVY", None),
    ("05045", "Greenbrier Volunteer Fire Department", "Fire Dues", "Greenbrier FD, Special Election", None),
    ("05015", "City of Holiday Island", "Alcoholic Beverage Sunday Sales", "Holiday Island Sunday Sales", None),
    ("05015", "City of Holiday Island", "2.5% Sales and Use Tax", "Holiday Island 2.5% Sales Tax", None),
    ("05067", "Jackson County", "0.375% Sales and Use Tax", "0.375% SALES AND USE TAX", None),
    (
        "05065",
        "City of Horseshoe Bend",
        "Backyard Poultry Initiative",
        "Horseshoe Bend Poultry Initiative",
        "initiative",
    ),
    (
        "05085",
        "City of Ward",
        "Four-Year Council Member Terms",
        "THE ISSUE OF CHANGING THE TERM LENGTHS OF COUNCIL MEMBERS FROM TWO (2) YEARS TO FOUR (4) YEARS IN THE CITY OF WARD, ARKANSAS Ward City",
        None,
    ),
    (
        "05131",
        "City of Fort Smith",
        "Mayor-Council Form of Government",
        "A PROPOSITION TO ORGANIZE THE CITY OF FORT SMITH, ARKANSAS UNDER THE MAYOR-COUNCIL FORM OF MUNICIPAL GOVERNMENT Fort Smith",
        None,
    ),
    ("05131", "Town of Central City", "1% Sales and Use Tax", "1% Sales and Use Tax Central City", None),
    ("05143", "City of Winslow", "Alcohol Sales", "CITY OF WINSLOW", None),
    ("05143", "City of West Fork", "Alcohol Sales", "CITY OF WEST FORK", None),
    ("05143", "City of West Fork", "Annexation", "CITY OF WEST FORK ANNEXATION", None),
    (
        "05047",
        "City of Ozark",
        "System Improvement Bonds and Replacement Tax",
        "System Improvement Bonds and Replacement Tax Ozark",
        None,
    ),
    (
        "05047",
        "City of Ozark",
        "Refunding Bonds and Replacement Tax",
        "Refunding Bonds and Replacement Tax Ozark",
        None,
    ),
    ("05029", "City of Plumerville", "Sales Tax", "City of Plumerville Tax", None),
    ("05029", "City of Oppelo", "Sales Tax", "City of Oppelo Tax", None),
    ("05029", "Town of Menifee", "Annexation", "Menifee Annexation", None),
    ("05017", "City of Dermott", "Ordinance No. 2026-02: Annexation", "Ordinance No. 2026-02 Annexation Dermott", None),
    ("05007", "NEBCO Volunteer Fire Department", "Fire Dues", "NEBCO-Fire", None),
    ("05007", "NEBCO Volunteer Fire Department", "EMS Dues", "NEBCO-EMS", None),
    ("05101", "Newton County", "1% Sales and Use Tax for Jail Purposes", "1% SALE AND USE TAX FOR JAIL PURPOSES", None),
    ("05039", "Dallas County", "1% Sales and Use Tax", "1% Sales and Use Tax Dallas County", None),
    ("05073", "City of Lewisville", "2.5% Sales and Use Tax", "2.5% Sales and Use Tax Lewisville", None),
    ("05123", "City of Forrest City", "Public Library Millage", "City of Forrest City, Public Library Millage", None),
    ("05069", "City of Pine Bluff", "Alcoholic Beverage Sunday Sales", "City of Pine Bluff, Question", None),
    ("05069", "City of Redfield", "Question", "City of Redfield, Question", None),
    ("05033", "City of Alma", "Special Election Question", "City of Alma, Special Election", None),
    ("05013", "Calhoun County", "1% Road Sales and Use Tax", "1% Road Sales & Use Tax", None),
    (
        "05139",
        "City of El Dorado",
        "Sunday Off-Premises Alcoholic Beverage Sales",
        "SALE OF ALCOHOLIC BEVERAGES FOR OFF-PREMISES CONSUMPTION ON SUNDAY WITHIN THE CITY LIMITS OF EL DORADO, ARKANSAS",
        None,
    ),
]


def fetch() -> None:
    folder = max(LIVE.iterdir())
    index = json.loads((folder / "index.json").read_text())
    raw.store(
        SOURCE,
        FEED,
        (folder / index[FEED]).read_bytes(),
        suffix=".json",
        retrieved_at=datetime.strptime(folder.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC),
        media_type="application/json",
    )
    for url, _ in COUNTIES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            content = subprocess.run(
                ["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests = {
        contest["ContestName"]: contest
        for contest in json.loads(raw.cached(SOURCE, FEED).path.read_bytes())["ContestData"]
    }
    rows = [
        {"kind": None}
        | {key: value for key, value in row.items() if key != "answers"}
        | {"url": COUNTIES[row["county"]][0]}
        for row in ROWS
    ]
    for county, entity, label, name, kind in FEED_ROWS:
        choices = [choice["Name"].strip() for choice in contests[name]["Choices"]]
        wording = (
            " ".join(choice if choice.endswith(".") else f"{choice}." for choice in choices)
            if all(len(choice) > 40 for choice in choices)
            else None
        )
        rows.append(
            {
                "county": county,
                "entity": entity,
                "label": label,
                "wording": wording,
                "url": FEED,
                "kind": kind,
                "ballot": name,
            }
        )
    frame = pl.DataFrame(rows, schema_overrides={"wording": pl.String, "kind": pl.String, "ballot": pl.String})
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas county and city ballot issues on the November 3, 2026 ballot, with official wording",
            polity="US-AR",
        )
        civil = civmint.Civil(conn, cs, "05", "AR")
        found = civil.found
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for county, (url, publisher) in COUNTIES.items():
            sources[url] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        sources[FEED] = cs.source("Arkansas Secretary of State", FEED_TITLE)
        cs.add(
            "source",
            id=sources[FEED],
            kind="official_body",
            publisher="Arkansas Secretary of State",
            title=FEED_TITLE,
            url=FEED,
        )
        cs.artifact(raw.cached(SOURCE, FEED), sources[FEED])
        parent = cs.key("us.election", f"{DAY}:AR")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            entity = row["entity"]
            county_key = f"us.county:{row['county']}"
            source = sources[row["url"]]
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
                    conn, cs, "AR", (county, county_area, county_path, county_code), entity, civil.slugs
                )
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election, _ = live_key(cs, "us.election", f"{DAY}:AR:{code}")
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
            contest_key = f"{DAY}:AR:{code}:measure:{slug(label)}"
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
            cs.add("ballot_question", contest_id=contest, type=row["kind"] or "mandatory_referendum")
            cs.add(
                "name",
                entity_id=contest,
                text=f"{DAY.year} {place}: {label}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            if row["ballot"] is not None:
                cs.add("name", entity_id=contest, text=row["ballot"], lang="en", kind="ballot", is_preferred=False)
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            for position, answer in enumerate(("For", "Against")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{slug(answer)}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add("citation", entity_id=contest, source_id=source)
            if row["wording"] is None:
                continue
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
