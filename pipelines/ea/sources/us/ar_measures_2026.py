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

SOURCE = "us.ar_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Notice of General Election, November 3, 2026: local measures"
GARLAND = "https://garlandcountyvote.org/uploads/6/3/0/4/63045401/local_issues.pdf"
SALINE = "https://salinecounty.org/Documents/Government/County%20Clerk/Elections/2026%20Elections/Election%20Notice%20for%20General%20Election%202026.pdf"
COUNTIES = {
    "05051": (GARLAND, "Garland County Election Commission"),
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
        "wording": "Vote FOR REFERRED ORDINANCE NO. O-26-07 or AGAINST REFERRED ORDINANCE NO. O-26-07. COUNTY-WIDE 0.625% SALES AND USE TAX. The continuation of the levy of 0.625% local sales and use Tax within the County to finance all or a portion of the costs of improvements to existing, roads, streets, and related structures, including particularly, without limitation, overpasses, underpasses, bridges and sidewalks, and any necessary land, easements, rights of way and related intersection improvements, drainage and traffic control devices and improvements therefor (collectively, the \"Improvements\"). If the question is approved, there will be a continuation of the existing levy within the County of 0.625% sales and use tax, the net collections of which after the State of Arkansas deducts its administrative charges and required rebates will be divided per capita among the County and the Municipalities within in the County pursuant to state law. The term of the sales and use tax levy shall be July 1, 2027, through June 30, 2032.",
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
        "county": "05125",
        "entity": "Saline County",
        "label": "Issue No. 5: Library Millage",
        "wording": "Saline County Ordinance No. 2026-8 Regarding Library Millage. VOTE FOR OR AGAINST REDUCING THE EXISTING LIBRARY AD VALOREM TAX LEVIED FROM THE EXISTING ONE AND SEVEN TENTHS (1.7) MILLS TO A NEW RATE OF ONE AND ONE TENTHS (1.1) MILLS ON THE DOLLAR OF ASSESSED VALUATION OF ALL REAL AND PERSONAL PROPERTY SUBJECT TO TAXATION IN THE COUNTY OF SALINE, ARKANSAS",
        "answers": ("For", "Against"),
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
    frame = pl.DataFrame([{key: value for key, value in row.items() if key != "answers"} | {"url": COUNTIES[row["county"]][0]} for row in ROWS])
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
            sources[county] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[county])
        parent = cs.key("us.election", f"{DAY}:AR")
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
                minted = govmint.special(conn, cs, "AR", (county, county_area, county_path, county_code), entity, civil.slugs)
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:AR:{code}")
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
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            cs.add("name", entity_id=contest, text=f"{DAY.year} {place}: {label}", lang="en", kind="official", is_preferred=True)
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
