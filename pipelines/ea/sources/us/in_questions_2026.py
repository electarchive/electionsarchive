from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.in_questions_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
DLGF = "https://www.in.gov/dlgf/files/referendum-documentation2/2026-november-referendum-documents/"
PUBLISHER = "Indiana Department of Local Government Finance"
CASINO_URL = "https://www.wane.com/top-stories/what-happens-after-the-northeast-indiana-casino-referendum/amp/"
CASINO_TITLE = "What happens after the Northeast Indiana casino referendum"
CASINO = "Shall inland casino gambling be permitted in {county} County?"

QUESTIONS: list[dict[str, str]] = [
    {
        "path": "us/in/allen-county",
        "label": "Inland casino gambling",
        "text": CASINO.format(county="Allen"),
        "url": CASINO_URL,
    },
    {
        "path": "us/in/dekalb-county",
        "label": "Inland casino gambling",
        "text": CASINO.format(county="DeKalb"),
        "url": CASINO_URL,
    },
    {
        "path": "us/in/steuben-county",
        "label": "Inland casino gambling",
        "text": CASINO.format(county="Steuben"),
        "url": CASINO_URL,
    },
    {
        "path": "us/in/bartholomew-county-school-corporation",
        "label": "Operating referendum renewal",
        "file": "26-013-Bartholomew-Consolidated-School-Corporation-Operating-Determination.pdf",
        "text": "Shall Bartholomew Consolidated School Corporation continue to increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and maintaining competitive compensation for teachers and staff and maintaining student safety by imposing a property tax rate that does not exceed $0.156 and results in a maximum annual amount that does not exceed $10,100,000. If this operating referendum public question is NOT approved by the voters, for a median residence of $250,000, the property's annual tax bill would decrease by $190 per year. If this operating referendum public question is approved by the voters, it would be a renewal of the most recent operating referendum public question passed in 2020 with a property tax rate of $0.156.",
    },
    {
        "path": "us/in/lebanon",
        "label": "Police headquarters capital referendum",
        "file": "26-040-City-of-Lebabon-Documentation.pdf",
        "text": "Shall the City of Lebanon, Indiana increase property taxes paid to the City for no more than 20 years for the purpose of funding all or any portion of the cost of the design, construction and equipping of a new police headquarters in the City, together with the acquisition of related real property, as required, and any related improvements thereto, for which the principal debt amount for the project will cost no more than $52,165,000 and the financing cost including interest and fees will cost no more than an additional $35,918,413 and is estimated to increase the property taxes paid to the City by imposing a property tax rate that results in a maximum annual amount that does not exceed $4,454,550. If this capital referendum public question is approved by the voters, for a median residence of $250,000, the property's annual property tax bill would increase by $216 per year.",
    },
    {
        "path": "us/in/barr-reeve-community-school-corporation",
        "label": "Operating referendum",
        "file": "26-016-Barr-Reeve-Community-Schools-Operating-Determination.pdf",
        "text": "Shall Barr-Reeve Community Schools, Inc. increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and maintaining small class sizes, athletics and extra-curricular programs, elementary music and physical education programs and school safety and mental health resources in response to decreases in property tax revenue by imposing a property tax rate that does not exceed $0.40 and results in a maximum annual amount that does not exceed $2,200,000? If this operating referendum public question is approved by the voters, for a median residence of $250,000, the property's annual property tax bill would increase by $454 per year.",
    },
    {
        "path": "us/in/wayne-township-metropolitan-school-district",
        "label": "Operating referendum",
        "file": "26-025-Metropolitan-School-District-of-Wayne-Township-Operating-Determination.pdf",
        "text": "Shall the Metropolitan School District of Wayne Township increase property taxes paid to the school district for no more than eight (8) years for the purpose of funding the Wayne Forward Initiative to sustain educational and operational stability in response to reductions in property tax revenue, including recruiting and retaining classroom teachers, funding student safety and school security, and maintaining daily educational operations by imposing a property tax rate that does not exceed $0.70 and results in a maximum annual amount that does not exceed $41,019,552. If this operating referendum public question is approved by the voters, for a median residence of $200,000, the property's annual property tax bill would increase by $605 per year. The most recent operating referendum public question proposed by the school district was held in 2019 and passed.",
    },
    {
        "path": "us/in/decatur-township-metropolitan-school-district",
        "label": "Operating referendum renewal",
        "file": "26-039-Metropolitan-School-District-of-Decatur-Township-Operating-Determination.pdf",
        "text": "Shall the Metropolitan School District of Decatur Township increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding the #Decatur Community and School Improvement Program by (1) repealing the existing referendum fund tax levy approved by the voters in 2019, and (2) replacing the existing referendum fund tax levy with a new referendum fund tax levy which will provide funding for student transportation, utility expenses, school safety and security programs, building maintenance and support services by imposing a property tax rate that does not exceed $0.2690 and results in a maximum annual amount that does not exceed $9,000,000. If this operating referendum public question is approved by the voters, for a median residence of $250,000, the property's annual property tax bill would increase by $306 per year. The most recent operating referendum public question proposed by the school corporation was held in 2019 and passed.",
    },
    {
        "path": "us/in/washington-township-metropolitan-school-district",
        "label": "Operating referendum renewal",
        "file": "26-031-MSD-Washington-Township-Operating-Extension-Determination.pdf",
        "text": "Shall M.S.D. of Washington Township continue to increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of repealing the existing referendum fund tax rate and replacing the existing referendum fund tax rate with a new rate that will provide funding for retaining teachers and staffing for improved student safety, increased student support services, as well as expanded academic programming in alignment with the new diploma requirements by imposing a property tax rate that does not exceed $0.25 and results in a maximum annual amount that does not exceed $24,000,000? If this operating referendum public question is NOT approved by the voters, for a median residence of $300,000, the property's annual property tax bill would decrease by $351 per year. If this operating referendum public question is approved by the voters, it would be a renewal of the most recent operating referendum public question passed in 2020 with a property tax rate of $0.25.",
    },
    {
        "path": "us/in/richland-township-monroe-county",
        "label": "Reorganization of Richland Township and the Town of Ellettsville",
        "file": "26-043-Town-of-Ellettsville-Reorganization.pdf",
        "text": "Richland Township of Monroe County, Indiana and the Town of Ellettsville of Monroe County, Indiana have each adopted a Plan of Reorganization that provides for the consolidation of Richland Township into the Town of Ellettsville as a single governmental unit that will be governed and administered as a town with a seven (7) member elected Town Council. Shall Richland Township and the Town of Ellettsville reorganize as a single political subdivision?",
    },
    {
        "path": "us/in/pike-county-school-corporation",
        "label": "Operating referendum",
        "file": "26-004-Pike-County-School-Corporation-Operating-Determination.pdf",
        "text": "Shall Pike County School Corporation increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and maintaining daily educational operations, including maintaining operations at existing school buildings, retaining teachers and staff, supporting essential safety initiatives, and providing academic and CTE programs and opportunities to meet the learning needs of all students in response to reductions in property tax revenue, by imposing a property tax rate that does not exceed $0.25 and results in a maximum annual amount that does not exceed $2,200,000? If this operating referendum public question is approved by the voters, for a median residence of $150,000, the property's annual property tax bill would increase by $149 per year.",
    },
    {
        "path": "us/in/shakamak-schools-metropolitan-school-district",
        "label": "Operating referendum",
        "file": "26-024-Metropolitan-School-District-of-Shakamak-Operating-Determination.pdf",
        "text": "Shall the Metropolitan School District of Shakamak increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and maintaining daily educational operations, including recruiting and retaining teachers and staff, supporting essential safety initiatives, maintaining class sizes, and funding academic programs and opportunities to meet the learning needs of all students, in response to reductions in property tax revenue, by imposing a property tax rate that does not exceed $0.25 and results in a maximum annual amount that does not exceed $499,000. If this operating referendum public question is approved by the voters, for a median residence of $100,000, the property's annual property tax bill would increase by $81 per year.",
    },
    {
        "path": "us/in/west-lafayette-community-school-corporation",
        "label": "Operating referendum",
        "file": "26-006-West-Lafayette-Community-School-Corporation-Operating-Determination.pdf",
        "text": "Shall West Lafayette Community School Corporation increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding academic programming and operating expenditures and sustaining educational and operational stability in response to reductions in property tax revenue by imposing a property tax rate that does not exceed $0.5706 and results in a maximum annual amount that does not exceed $16,100,000? If this operating referendum public question is approved by the voters, for a median residence of $350,000, the property's annual property tax bill would increase by $956 per year. The most recent operating referendum public question proposed by the school corporation was held in 2023 and passed.",
    },
]


def url(row: dict[str, str]) -> str:
    return row.get("url") or DLGF + row["file"]


def fetch() -> None:
    for row in QUESTIONS:
        raw.fetch(SOURCE, url(row), suffix=".html" if "url" in row else ".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame([{**row, "url": url(row)} for row in QUESTIONS]).select("path", "label", "text", "url")
    frame.write_parquet(PARSED / "questions.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "questions.parquet")
    with connect() as conn:
        polities: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        for path, polity, area, text, code in conn.execute(
            """select s.path, p.id, a.id, n.text, c.code from slug s
               join polity p on p.id = s.entity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join polity_territory t on t.polity_id = p.id
               join area a on a.id = t.area_id
               join area_code c on c.area_id = a.id
               where s.is_current and s.path like 'us/in/%%'
                 and a.scheme_code in ('us.county', 'us.place', 'us.town', 'us.school_district')"""
        ).fetchall():
            polities[path] = (polity, area, text, path, code)
        held = dict(
            conn.execute(
                "select s.path, x.value from slug s join external_id x on x.entity_id = s.entity_id"
                " and x.scheme = 'us.election' where s.path like 'us/in/%%/2026-11-03-general'"
            ).fetchall()
        )
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/in/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indiana county, city, township, and school district public questions on the November 3, 2026 ballot, with the wording certified by the Department of Local Government Finance",
            polity="US-IN",
        )
        system = cs.key("us.electoral_system", "referendum")
        parent = cs.key("us.election", f"{DAY}:IN")
        sources: dict[str, UUID] = {}
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            if row["path"] not in polities:
                print("no polity:", row["path"])
                continue
            polity, area, place, path, code = polities[row["path"]]
            if row["url"] not in sources:
                news = row["url"] == CASINO_URL
                source = cs.source(
                    "WANE" if news else PUBLISHER, CASINO_TITLE if news else row["url"].rsplit("/", 1)[1]
                )
                cs.add(
                    "source",
                    id=source,
                    kind="news_agency" if news else "official_body",
                    publisher="WANE" if news else PUBLISHER,
                    title=CASINO_TITLE if news else row["url"].rsplit("/", 1)[1],
                    url=row["url"],
                )
                cs.artifact(raw.cached(SOURCE, row["url"]), source)
                sources[row["url"]] = source
            source = sources[row["url"]]
            home = f"{path}/{DAY}-general"
            election = cs.key("us.election", held.get(home) or f"{DAY}:IN:{code}")
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
                cs.add("slug", path=home, entity_id=election)
            label = row["label"]
            stem = slug(label)
            contest_key = f"{DAY}:IN:{code}:question:{stem}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{home}/{stem}"
            if slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            slugs[contest_path] = contest
            kept += 1
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
                    id=cs.key("us.option", f"{contest_key}:{answer.lower()}"),
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
                body=row["text"],
                url=row["url"],
                source_id=source,
            )
        print(f"{kept} of {frame.height} questions")
        return cs.write()
