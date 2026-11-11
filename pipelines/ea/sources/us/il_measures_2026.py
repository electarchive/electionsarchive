import html
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import civmint, govmint
from ea.store import DATA, connect

SOURCE = "us.il_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
PORTAL = "https://electionresults.kanecountyil.gov/2026-11-03/Contests/"
PORTAL_ROWS = {
    "Refuse Collection and Disposal Authority": "(ST CHARLES TWSP) PROPOSITION TO AUTHORIZE ENHANCED TOWNSHIP AUTHORITY FOR REGULATION OF REFUSE COLLECTION &amp; DISPOSAL"
}
MANUAL = DATA / "inbox" / "manual" / "MF-376"
TITLE = "Referenda questions, November 3, 2026 general election"
COUNTIES = {
    "17201": (
        "https://wincoil.gov/media/attachments/2026/08/27/questions-nov-3-2026-2.pdf",
        None,
        "Winnebago County Clerk",
        ".pdf",
    ),
    "17119": (
        "https://www.madisoncountyil.gov/document_center/CountyClerk/2026%20Election/REFERENDA%20QUESTIONS.pdf",
        None,
        "Madison County Clerk",
        ".pdf",
    ),
    "17117": (
        "https://assets01.aws.connect.clarityelections.com/Assets/Connect/RootPublish/macoupin-il.connect.clarityelections.com/2026%20Election/26nov3ilmacoupin-Publication.pdf",
        None,
        "Macoupin County Clerk",
        ".pdf",
    ),
    "17031": (
        "https://www.cookcountyclerkil.gov/elections/current-election-referenda",
        "IL-Cook/il-cook-2026-11-03-referenda.html",
        "Cook County Clerk",
        ".html",
    ),
    "17113": (
        "https://www.mcleancountyil.gov/1049/Referenda",
        "IL-McLean/il-mclean-2026-11-03-referenda.html",
        "McLean County Clerk",
        ".html",
    ),
    "17111": (
        "https://www.mchenrycountyil.gov/home/showpublisheddocument/20260824-public-notice-of-referendum",
        "IL-McHenry/20260824 PUBLIC NOTICE OF REFERENDUM.pdf",
        "McHenry County Clerk",
        ".pdf",
    ),
}
MCHENRY_LABELS = (
    ("Home Rule", "Advisory Question: Home Rule and County Executive"),
    ("Zoning", "Advisory Question: Local Zoning Authority"),
    ("Limitation Law", "Increase Limiting Rate"),
    ("Pension", "New Tax Rate for Pension Fund"),
    ("Bonds", "Issue Bonds"),
)
BUNKER_HILL = (
    "Shall the limiting rate under the Property Tax Extension Limitation Law for the Bunker Hill Fire Protection "
    "District, in Macoupin County, Illinois and Madison County, Illinois be increased by an additional amount equal "
    "to .18% above the limiting rate for the purpose of fire department operations for levy year 2026 and be equal "
    "to .43097% of the equalized assessed value of the taxable property therein for levy year 2026?"
)
DATA_CENTERS = "Shall the construction and operation of new data centers be permitted in {} Township?"
SEWER = "Shall Madison County sell Special Service Area No. 1, a sewer {}, to a private company?"
MADISON = (
    (
        "City of Madison",
        "Home Rule",
        "Shall the City of Madison, Madison County, Illinois, become a home rule unit?",
    ),
    (
        "Cottage Hills Fire Protection District",
        "Special Tax for Emergency and Rescue",
        (
            "Shall the Cottage Hills Fire Protection District levy a special tax at a rate of not more than .10% of the "
            "value of all taxable property within the district as equalized or assessed by the Department of Revenue for "
            "the purpose of providing funds to pay for the costs of emergency and rescue crews and equipment?"
        ),
    ),
    (
        "Madison County",
        "Advisory Question: Government-Issued Identification to Vote",
        "Shall the Illinois General Assembly pass legislation to require government-issued identification to vote?",
    ),
    (
        "Madison County",
        "Advisory Question: Local Zoning Control of Wind and Solar Farms",
        (
            "Shall the Illinois General Assembly restore zoning control of wind and solar farm developments to local "
            "governments instead of mandating them from the state level?"
        ),
    ),
    (
        "Madison County",
        "Advisory Question: Property Tax Reform",
        (
            "Shall the Illinois General Assembly pass new legislation to enact comprehensive property tax reform and "
            "dramatically reduce property taxes, such as using a different source to fund public schools, and putting a "
            "cap on property taxes at 1% of the assessed value of a property?"
        ),
    ),
    (
        "Village of Worden",
        "Backyard Chickens",
        "Shall the Village of Worden enact an ordinance allowing and regulating the keeping of backyard chickens?",
    ),
    *(
        (f"{town} Township", "Data Centers", DATA_CENTERS.format(town))
        for town in ("Chouteau", "Granite City", "Nameoki", "Venice")
    ),
    *(
        (
            f"{town} Township",
            "Sale of Special Service Area No. 1",
            SEWER.format("district" if town != "Granite City" else "system"),
        )
        for town in ("Chouteau", "Granite City", "Nameoki", "Venice")
    ),
)
MACOUPIN = (
    (
        "Macoupin County",
        "Advisory Question: Separation From Cook County",
        (
            "Shall the board of Macoupin County correspond with the boards of the other counties of Illinois outside of "
            "Cook County about the possibility of separating from Cook County to form a new state, and to seek admission "
            "to the Union as such, subject to the approval of the people?"
        ),
    ),
    (
        "Macoupin County",
        "Proposition to Create Special Ambulance Service",
        (
            "Should a Special Service Area to provide ambulance service be created in the same area as Medora Ambulance "
            "Service so those residents pay property taxes for the service at a rate of .103%?"
        ),
    ),
    (
        "City of Staunton",
        "Proposition to Arrange for Supply of Electricity",
        (
            "Shall the municipality have the authority to arrange for the supply of electricity for its residential and "
            "small commercial retail customers who have not opted out of such a program?"
        ),
    ),
    ("Bunker Hill Fire Protection District", "Increase Limiting Rate", BUNKER_HILL),
)
SKIPPED = {("17111", "Community Unit School District #300")}
NOTICES = (
    (
        "17043",
        "St Charles Community Unit School District Number 303",
        "School Building Bonds",
        "https://district.d303.org/referendum",
        None,
        "St. Charles Community Unit School District 303",
        (
            "Shall the Board of Education of St. Charles Community Unit School District Number 303, Kane and DuPage "
            "Counties, Illinois, improve the sites of, build and equip additions to and alter, repair and equip school "
            "buildings and facilities, including, but not limited to, replacing roofs, updating mechanical, electrical "
            "and plumbing systems, installing safety and security improvements, increasing accessibility under the "
            "Americans with Disabilities Act (ADA) and expanding space for Career and Technical Education programs; build "
            "and equip a physical education and athletic facility; and issue its bonds to the amount of $282,500,000 for "
            "the purpose of paying the costs thereof?"
        ),
    ),
    (
        "17043",
        "SD 45 DuPage County",
        "Increase Limiting Rate",
        "https://www.d45.org/strengthen45/",
        None,
        "School District 45, DuPage County",
        (
            "Shall the limiting rate under the Property Tax Extension Limitation Law for School District Number 45, DuPage "
            "County, Illinois, be increased by an additional amount equal to 0.2670% above the limiting rate for levy year "
            "2025 for school purposes, including funding instructional programming, student support and services, "
            "operations and maintenance, and safety and security improvements, and be equal to 3.2229% of the equalized "
            "assessed value of the taxable property therein for levy year 2026?"
        ),
    ),
    (
        "17043",
        "DuPage Community High School District Number 88",
        "School Building Bonds",
        "https://www.dupage88.net/site/page/17652",
        None,
        "DuPage High School District 88",
        (
            "Shall the Board of Education of DuPage Community High School District Number 88, DuPage County, Illinois, "
            "improve the sites of, and alter, repair, and equip school buildings and facilities, including, but not "
            "limited to, installing safety and security improvements; increasing accessibility to comply with the "
            "Americans with Disabilities Act; updating roofing, mechanical, plumbing, electrical, heating and cooling "
            "systems; renovating career and technical education (CTE) spaces; and installing energy-efficiency "
            "improvements; and issue bonds of said School District to the amount of $118,000,000 for the purpose of "
            "paying the costs thereof?"
        ),
    ),
    (
        "17043",
        "Westmont Community Unit School District Number 201",
        "School Building Bonds",
        "https://cusd201.community.diligentoneplatform.com/document/3c903a24-27ea-46c1-b9af-9456ab06ff7c/",
        None,
        "Westmont Community Unit School District 201",
        (
            "Shall the Board of Education of Westmont Community Unit School District Number 201, DuPage County, Illinois, "
            "build and equip a school building at the current location of the C.E. Miller Elementary School to replace "
            "the J.T. Manning and C.E. Miller Elementary School Buildings, acquire land, improve the site thereof, "
            "including creating playground space, additional parking and drop-off/pick-up zones, and issue its bonds to "
            "the amount of $68,000,000 for the purpose of paying the costs thereof?"
        ),
    ),
    (
        "17043",
        "Lisle Park District",
        "Park Bonds",
        "https://www.lisleparkdistrict.org/pdfs/transparency-portal/Resolutions/2026/Resolution081026-Referendum.pdf",
        None,
        "Lisle Park District",
        (
            "Shall the Lisle Park District, DuPage County, Illinois, replace the existing Community Center by building and "
            "equipping a new Community Activity Center with a fitness area with cardio and strength training equipment, "
            "an indoor walking/jogging track, multi-court gymnasium, and flexible programming spaces, and issue its bonds "
            "to the amount of $29,750,000 for the purpose of paying the costs thereof?"
        ),
    ),
    (
        "17043",
        "City of Aurora",
        "Mayor Recall Electoral System",
        "https://www.aurora.il.us/files/content/mainsite/v/42/government-and-engagement/elections/city-of-aurora-posting-notice.pdf",
        "IL-Kane/city-of-aurora-posting-notice.pdf",
        "City of Aurora City Clerk",
        (
            "Shall the City of Aurora adopt the following electoral system immediately upon passage of this binding "
            "referendum: the Mayor may be recalled and removed by a majority vote at a recall election that may be "
            "triggered by the filing of a recall petition signed by a number of Aurora electors equal to 20% of the total "
            "votes cast for all candidates for mayor in the immediately preceding mayoral election; and upon the "
            "certification of a valid recall petition, the question “Shall (name of Mayor) be recalled and removed from "
            "the Office of Mayor of the City of Aurora” shall be placed on the ballot at the immediately succeeding "
            "regularly-scheduled election to be held in the City of Aurora; and that in the event the Mayor is recalled "
            "and removed, the City Council shall fill the vacancy in the office of Mayor within 30 days from the date of "
            "the recall election, which appointment shall be for the remainder of the term, except that if more than 13 "
            "months remain in the term, the City Council shall set a date for a special election to be held within 9 "
            "months of the recall election at which time the voters of the City of Aurora shall elect a Mayor to serve "
            "the remaining term in office?"
        ),
    ),
    (
        "17089",
        "St. Charles Township",
        "Refuse Collection and Disposal Authority",
        "https://clerk.kanecountyil.gov/elections/lists/referenda/allitems.aspx",
        None,
        "Kane County Clerk",
        (
            "Shall St. Charles Township, Kane County, Illinois, be authorized to exercise the powers of the “Township "
            "Refuse Collection and Disposal Act” (60 ILCS 1-210 et seq.) to enter into a contract for the collection, "
            "disposal, composting and recycling of garbage and refuse for residents of the unincorporated areas of "
            "St. Charles Township?"
        ),
    ),
)
DOCUMENTS = {
    url: (file, publisher, ".pdf" if url.endswith("pdf") or "diligent" in url else ".html")
    for _, _, _, url, file, publisher, _ in NOTICES
}


def fetch() -> None:
    try:
        raw.cached(SOURCE, PORTAL)
    except LookupError:
        raw.fetch(SOURCE, PORTAL, suffix=".html")
    for url, file, _, suffix in [*COUNTIES.values(), *((url, *rest) for url, rest in DOCUMENTS.items())]:
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            if file is None:
                content = subprocess.run(
                    ["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True
                ).stdout
                retrieved = datetime.now(UTC)
            else:
                path = MANUAL / file
                content = path.read_bytes()
                retrieved = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            raw.store(
                SOURCE,
                url,
                content,
                suffix=suffix,
                retrieved_at=retrieved,
                media_type="application/pdf" if suffix == ".pdf" else "text/html",
            )


SCHOOL = re.compile(
    r"((?:[A-Z][\w\-]+ )+?(?:Combined |Community Unit |Township High |Elementary |Unit |Community High )?(?:School|Community College) District (?:Number|No\.) ?\d+\w*)"
)


def full_name(entity: str, wording: str) -> str:
    if not re.search(r"School District|CUSD|College District", entity):
        return entity
    match = SCHOOL.search(wording)
    return re.sub(r"^(Shall|The) ", "", match[1]) if match is not None else entity


def pdf_text(url: str, mode: str = "-layout") -> str:
    return subprocess.run(
        ["pdftotext", mode, str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout


KEY_CODES = {
    "17043:library:bartlett-public-library-district": "17031:district:bartlett-public-library-district",
    "17089:fire-protection:east-dundee-countryside-fire-protection-district": "17031:fire-protection:east-dundee-countryside-fire-protection-district",
}


def listed_rows(county: str, listed: tuple[tuple[str, str, str], ...]) -> list[dict[str, str]]:
    text = re.sub(r"\W+", "", pdf_text(COUNTIES[county][0], "-raw")).lower()
    rows = []
    for entity, label, wording in listed:
        assert re.sub(r"\W+", "", wording).lower() in text, (county, label)
        rows.append({"entity": entity, "label": label, "wording": wording})
    return rows


def page_text(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text()


def notice_rows() -> list[dict[str, str]]:
    rows = []
    for county, entity, label, url, _, _, wording in NOTICES:
        text = (
            pdf_text(url, "-raw")
            if DOCUMENTS[url][2] == ".pdf"
            else re.sub(r"\\u([0-9a-f]{4})", lambda m: chr(int(m[1], 16)), html.unescape(page_text(url)))
        )
        assert re.sub(r"\W+", "", wording).lower() in re.sub(r"\W+", "", text).lower(), label
        rows.append({"county": county, "entity": entity, "label": label, "wording": wording, "url": url})
    return rows


def cook_rows() -> list[dict[str, str]]:
    cells = re.findall(
        r'<span translate="no">(.*?)</span></div><div[^>]*grow="3">(.*?)</div></div>',
        page_text(COUNTIES["17031"][0]),
        re.DOTALL,
    )
    rows = []
    for head, wording in cells:
        entity, label = html.unescape(head).split(", ", 1)
        rows.append({"entity": entity, "label": label, "wording": re.sub(r"\s+", " ", html.unescape(wording)).strip()})
    return rows


def mclean_rows() -> list[dict[str, str]]:
    page = page_text(COUNTIES["17113"][0])
    heads = re.findall(r'data-tabname="([^"]+)"', page)
    wordings = re.findall(r"<p>(.+?)</p><ul[^>]*><li><strong>YES", page, re.DOTALL)
    rows = []
    for head, wording in zip(heads, wordings, strict=True):
        entity, label = html.unescape(head).split(" - ", 1)
        rows.append(
            {
                "entity": entity.strip(),
                "label": label.strip(),
                "wording": re.sub(r"\s+", " ", html.unescape(wording)).strip().strip('"“”'),
            }
        )
    return rows


def mchenry_rows() -> list[dict[str, str]]:
    rows = []
    entity = ""
    text = pdf_text(COUNTIES["17111"][0]).split("SUBDIVISIONS:", 1)[1]
    for block in re.split(r"\n\s*\n", text):
        block = re.sub(r"\s+", " ", block).strip()
        if not block or block.startswith(("I hereby", "Dated")):
            continue
        if block.isupper():
            entity = block.title().replace("Mchenry", "McHenry").replace("Of ", "of ")
        elif not rows or rows[-1]["entity"] != entity or rows[-1]["wording"]:
            if re.match(r"^\d\.|^\(\d\)", block):
                rows[-1]["wording"] += " " + block
            else:
                rows.append({"entity": entity, "label": block, "wording": ""})
        else:
            rows[-1]["wording"] = block
    for row in rows:
        row["label"] = next(label for key, label in MCHENRY_LABELS if key in row["label"])
        row["wording"] = row["wording"].strip('"')
    return rows


def parse() -> None:
    text = pdf_text(COUNTIES["17201"][0])
    text = re.sub(r"(?m)^(\s*)(YES|NO)\s*$", "", text)
    text = re.sub(r"(?m)\s{20,}(YES|NO)\s*$", "", text)
    parts = re.split(
        r"(?m)^\s*(NON-BINDING ADVISORY PUBLIC QUESTION CONCERNING[^\n]*(?:\n\s+[A-Z ]+(?=\n))?|PROPOSITION TO INCREASE THE PROPERTY TAX LIMITING RATE)\s*$",
        text,
    )
    rows = []
    for head, body in zip(parts[1::2], parts[2::2], strict=True):
        quote = re.search(r"[“\"](.+?\?)[”\"]", body, re.DOTALL)
        wording = re.sub(r"\s+", " ", quote[1]).replace("NON- ", "NON-").strip()
        if head.startswith("PROPOSITION"):
            wording = re.sub(r"\s+", " ", body.split("I hereby certify")[0]).strip()
            entity = "Pecatonica Fire Protection District"
            label = "Proposition to Increase the Property Tax Limiting Rate"
        else:
            entity = "Winnebago County"
            label = (
                re.sub(r"\s+", " ", head.replace("NON-BINDING ADVISORY PUBLIC QUESTION CONCERNING", "")).strip().title()
            )
            label = f"Advisory Question: {label}"
        wording = re.sub(r"\s+", " ", re.sub(r"(?<=\s)(YES|NO)(?=\s)", " ", wording)).strip()
        rows.append({"entity": entity, "label": label, "wording": wording})
    tagged = [{**row, "county": "17201"} for row in rows]
    for county, extra in (
        ("17031", cook_rows()),
        ("17113", mclean_rows()),
        ("17111", mchenry_rows()),
        ("17119", listed_rows("17119", MADISON)),
        ("17117", listed_rows("17117", MACOUPIN)),
    ):
        tagged.extend({**row, "county": county} for row in extra if (county, row["entity"]) not in SKIPPED)
    for row in tagged:
        row["entity"] = full_name(row["entity"], row["wording"])
        row["url"] = COUNTIES[row["county"]][0]
    tagged.extend(notice_rows())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(tagged).unique(["county", "entity", "label"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height)


def portal_result(heading: str) -> tuple[datetime, int, int]:
    page = page_text(PORTAL)
    stamp = re.search(r"Current as of \w+, (\w+ \d+, \d{4} \d+:\d+ [AP]M)", page)
    block = re.split(rf"<h2>\s*{re.escape(heading)}", page, maxsplit=1)[1].split('<div class="c"', 1)[0]
    votes = {
        answer.strip(): int(count)
        for answer, count in re.findall(r'<td class="b">(Yes|No) ?</td>\s*<td>(\d+)</td>', block)
    }
    as_of = datetime.strptime(stamp[1], "%B %d, %Y %I:%M %p").replace(tzinfo=ZoneInfo("America/Chicago"))
    return as_of, votes["Yes"], votes["No"]


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois county, municipal, school, and special district referenda (Winnebago, Cook, McLean, McHenry, Madison, Macoupin, and DuPage and Kane districts) on the November 3, 2026 ballot, with official wording",
            polity="US-IL",
        )
        civil = civmint.Civil(conn, cs, "17", "IL")
        found = civil.found
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for county, (url, _, publisher, _) in COUNTIES.items():
            sources[county] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[county])
        for url, (_, publisher, _) in DOCUMENTS.items():
            sources[url] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        parent = cs.key("us.election", f"{DAY}:IL")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            entity = row["entity"]
            county_key = f"us.county:{row['county']}"
            source = sources[row["url"]] if row["url"] in sources else sources[row["county"]]
            option = None
            if re.match(r"(?i)(city|town|village) of ", entity):
                option = civil.place(re.sub(r"(?i)^(city|town|village) of ", "", entity))
            elif county_key in found and entity == found[county_key][2]:
                option = county_key
            elif entity.endswith(" Township"):
                option = civil.town(entity, row["county"][:5])
            elif re.search(r"School District|CUSD|^SD \d", entity):
                option = civil.school(entity)
            elif f"special:{row['county']}:{entity}" in found:
                option = f"special:{row['county']}:{entity}"
            elif county_key in found:
                county, county_area, _, county_path, county_code = found[county_key]
                minted = govmint.special(
                    conn, cs, "IL", (county, county_area, county_path, county_code), entity, civil.slugs
                )
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            code = KEY_CODES.get(code, code)
            election = cs.key("us.election", f"{DAY}:IL:{code}")
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
            contest_key = f"{DAY}:IL:{code}:measure:{slug(label)}"
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
            if label in PORTAL_ROWS:
                as_of, yes, no = portal_result(PORTAL_ROWS[label])
                portal = cs.source("Kane County Clerk", "2026 General Election Results - Unofficial Contest Results")
                cs.add(
                    "source",
                    id=portal,
                    kind="official_body",
                    publisher="Kane County Clerk",
                    title="2026 General Election Results - Unofficial Contest Results",
                    url=PORTAL,
                )
                cs.artifact(raw.cached(SOURCE, PORTAL), portal)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="preliminary",
                    kind="counted",
                    source_id=portal,
                    as_of=as_of,
                    status="active",
                    is_default=True,
                    completeness=1.0,
                )
                for position, (answer, votes) in enumerate((("Yes", yes), ("No", no))):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit(option.split(":")[0], code),
                        option_id=cs.key("us.option", f"{contest_key}:{slug(answer)}"),
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
        print(f"{kept} of {frame.height} referenda")
        return cs.write()
