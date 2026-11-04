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
    {
        "path": "us/in/southeast-dubois-county-school-corporation",
        "label": "Operating referendum renewal",
        "file": "26-008-Southeast-Dubois-County-School-Corporation-Operating-Determination.pdf",
        "text": "Shall Southeast Dubois County School Corporation continue to increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and maintaining academic and educationally-related programs and opportunities to meet the learning needs of all students, student health and safety initiatives, maintaining class sizes and recruiting and retaining teachers and staff in response to reductions in property tax revenue by imposing a property tax rate that does not exceed $0.19 and results in a maximum annual amount that does not exceed $1,242,788. If this operating referendum public question is NOT approved by the voters, for a median residence of $200,000, the property's annual tax bill would decrease by $164 per year. If this operating referendum public question is approved by the voters, it would be a renewal of the most recent operating referendum public question passed in 2020 with a property tax rate of $0.19.",
    },
    {
        "path": "us/in/tri-creek-school-corporation",
        "label": "Operating referendum",
        "file": "26-028-Tri-Creek-School-Corporation-Operating-Determination.pdf",
        "text": "Shall Tri-Creek School Corporation increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of replacing lost property tax revenue, funding bus transportation, student programs, and school safety by imposing a property tax rate that does not exceed seventeen and thirty-eight hundredths cents ($0.1738) and results in a maximum annual amount that does not exceed three million dollars ($3,000,000)? If this operating referendum public question is approved by the voters, for a median residence of $300,000, the property's annual property tax bill would increase by $245 per year. The most recent operating referendum public question proposed by the school corporation was held in 2023 and failed.",
    },
    {
        "path": "us/in/michigan-city-area-schools",
        "label": "Operating referendum",
        "file": "26-035-Michigan-City-Area-Schools-Operating-Determination.pdf",
        "text": "Shall the Michigan City Area Schools increase property taxes paid to the School Corporation for no more than eight (8) years for the purpose of funding the Maximize Community and Students (MCAS) initiative which will sustain educational and operational funding stability in response to reductions in property tax revenue by recruiting and retaining educators and staff, supporting essential student health and safety initiatives, and maintaining class sizes, academic programs, and opportunities to meet educational needs by imposing a property tax rate that does not exceed $0.2998 and results in a maximum annual amount that does not exceed $10,000,000. If this operating referendum public question is approved by the voters, for a median residence of $200,000, the property's annual property tax bill would increase by $260 per year.",
    },
    {
        "path": "us/in/zionsville-community-schools",
        "label": "Operating referendum",
        "file": "26-001-Zionsville-Community-Schools-Operating-Determination.pdf",
        "text": "Shall Zionsville Community Schools increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and sustaining academic and educationally related programs and managing class sizes which reflect community standards by imposing a property tax rate that does not exceed 0.4128 and results in a maximum annual amount that does not exceed $21,463,325. If this operating referendum public question is approved by the voters, for a median residence of $600,000.00, the property's annual property tax bill would increase by $1,368.00 per year. The most recent property tax referendum proposed by the school corporation was held in 2019 and passed.",
    },
    {
        "path": "us/in/beech-grove-city-schools",
        "label": "Operating referendum",
        "file": "26-002-School-City-of-Beech-Grove-Operating-Determination.pdf",
        "text": "Shall the School City of Beech Grove increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding the Beech Grove City Schools: Believe in our Future initiative, by continuing existing essential student services, including school bus transportation, school facilities maintenance, and student safety and security services, including School Resource Officers, by imposing a property tax rate that does not exceed fifty-seven cents ($0.57) and results in a maximum annual amount that does not exceed $4,400,000? If this operating referendum public question is approved by the voters, for a median residence of $200,000, the property's annual property tax bill would increase by $493 per year.",
    },
    {
        "path": "us/in/sheridan-community-schools",
        "label": "Operating referendum",
        "file": "26-003-Sheridan-Community-School-Corporation-Operating-Determination.pdf",
        "text": "Shall Sheridan Community School Corporation increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and sustaining educational and operational stability in response to reductions in property tax revenue, including the retention and attraction of teachers and staff, academic related programs and operational expenses by imposing a property tax rate that does not exceed $0.40 and results in a maximum annual amount that does not exceed $2,900,000. If this operating referendum public question is approved by the voters, for a median residence of $250,000, the property's annual property tax bill would increase by $454 per year. The most recent operating referendum public question proposed by the school corporation was held in 2023 and passed.",
    },
    {
        "path": "us/in/anderson-community-school-corporation",
        "label": "Operating referendum renewal",
        "file": "26-005-Anderson-Community-School-Corporation-Operating-Determination.pdf",
        "text": "Shall Anderson Community School Corporation continue to increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding daily operational needs and academic and support programs by imposing a property tax rate that does not exceed $0.1077 and results in a maximum annual amount that does not exceed $2,903,078. If this operating referendum public question is NOT approved by the voters, for a median residence of $100,000, the property's annual tax bill would decrease by $54.00 per year. If this operating referendum public question is approved by the voters, it would be a renewal of the most recent operating referendum public question passed in 2018 with a property tax rate of $0.1077.",
    },
    {
        "path": "us/in/westfield-washington-schools",
        "label": "Operating referendum",
        "file": "26-007-Westfield-Washington-Schools-Operating-Determination.pdf",
        "text": "Shall Westfield Washington Schools increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding educational and operational expenses due to reductions in property tax revenue through the Excellent Schools → Excellent Community initiative including student safety by the addition of School Resource Officers in each school; maintaining small class sizes; advancing student health and well-being by adding school counselors, social workers, and mental health supports; advancing academic and essential programs to meet the educational needs of all students; and supporting and retaining teachers and staff by imposing a property tax rate that does not exceed $0.3941 and results in a maximum annual amount that does not exceed $38,000,000? If this operating referendum public question is approved by the voters, for a median residence of $450,000 the property's annual property tax bill would increase by $873 per year. The most recent operating referendum public question proposed by the school corporation was held in 2022 and passed.",
    },
    {
        "path": "us/in/oak-hill-united-school-corporation",
        "label": "Operating referendum",
        "file": "26-009-Oak-Hill-United-School-Corporation-Operating-Determination.pdf",
        "text": "Shall Oak Hill United School Corporation increase property taxes paid to the School Corporation for no more than eight (8) years for the purpose of funding and sustaining educational and operational stability in response to reductions in property tax revenue, including maintaining essential health and safety initiatives including the employment and retention of school resource officers and nurses, supporting, attracting and retaining teachers and staff, maintaining class sizes and student transportation, and funding academic and support programs to meet the educational needs of all students, by imposing a property tax rate that does not exceed $0.3332 and results in a maximum annual amount that does not exceed $1,627,112? If this operating referendum public question is approved by the voters, for a median residence of $150,000, the property's annual property tax bill would increase by $198 per year.",
    },
    {
        "path": "us/in/clark-pleasant-community-school-corporation",
        "label": "Operating referendum renewal",
        "file": "26-010-Clark-Pleasant-Community-Schools-Operating-Extension-Determination.pdf",
        "text": "Shall Clark-Pleasant Community School Corporation continue to increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and maintaining school safety initiatives, including the Clark-Pleasant police department, student support services and programming in response to reductions in property tax revenue by imposing a property tax rate that does not exceed $0.09 and results in a maximum annual amount that does not exceed $4,400,000. If this operating referendum public question is NOT approved by the voters, for a median residence of $300,000, the property's annual property tax bill would decrease by $127 per year. If this operating referendum public question is approved by the voters, it would be a renewal of the most recent operating referendum public question passed in 2018 with a property tax rate of $0.10.",
    },
    {
        "path": "us/in/tri-central-community-schools",
        "label": "Operating referendum",
        "file": "26-011-Tri-Central-Community-Schools-Operating-Determination.pdf",
        "text": "Shall Tri-Central Community Schools increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and maintaining daily educational operations, including recruiting and retaining teachers and staff, supporting essential safety initiatives, and maintaining class sizes, academic programs and opportunities to meet the learning needs of all students in response to reductions in property tax revenue by imposing a property tax rate that does not exceed $0.24 and results in a maximum annual amount that does not exceed $1,100,000. If this operating referendum public question is approved by the voters, for a median residence of $150,000, the property's annual property tax bill would increase by $143 per year.",
    },
    {
        "path": "us/in/whiting-school-city",
        "label": "Operating referendum",
        "file": "26-012-School-City-of-Whiting-Operating-Determination.pdf",
        "text": "Shall School City of Whiting increase property taxes paid to the School Corporation for no more than eight (8) years for the purpose of funding and sustaining educational and operational stability in response to reductions in property tax revenue, supporting essential health and safety services, including retaining and employing school resource officers, attracting and retaining teachers and staff, maintaining class sizes, and funding academic and student support programs to meet the educational needs of all students, by imposing a property tax rate that does not exceed $0.3372 and results in a maximum annual amount that does not exceed $2,500,000? If this operating referendum public question is approved by the voters, for a median residence of $250,000 the property's annual property tax bill would increase by $383 per year. The most recent operating referendum public question proposed by the school corporation was held in 2023 and failed.",
    },
    {
        "path": "us/in/whiting-school-city",
        "label": "Capital referendum",
        "file": "26-014-School-City-of-Whiting-Controlled-Project.pdf",
        "text": "Shall School City of Whiting increase property taxes paid to the School Corporation for no more than twenty-three years for the purpose of funding the School City of Whiting 2026 Student Facilities, Safety, and Transportation Improvement Project which includes health, safety, and security upgrades to all school facilities; renovation and improvements and the potential construction of additions to student learning environments at all school buildings; upgrades to school buildings to support technology-ready classrooms; and construction of and improvements to athletic and support facilities and performing arts and music facilities, including a band room, together with the purchase of equipment, technology and buses, and student safety improvements for which the principal debt amount for the project will cost no more than $39,305,000 and the financing cost including interest and fees will cost no more than an additional $28,810,000 and is estimated to increase the property taxes paid to the School Corporation by imposing a property tax rate that results in a maximum annual amount that does not exceed $3,500,000. If this capital referendum public question is approved by the voters, for a median residence of $250,000, the property's annual property tax bill would increase by $530 per year.",
    },
    {
        "path": "us/in/noblesville-schools",
        "label": "Operating referendum",
        "file": "26-015-Noblesville-Schools-Operating-Determination.pdf",
        "text": "Shall Noblesville Schools increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding safety and security measures in all buildings and maintaining and enhancing academic and educationally-related programs in response to reductions in property tax revenue by imposing a property tax rate that does not exceed $0.57 and results in a maximum annual amount that does not exceed $43,842,578. If this operating referendum public question is approved by the voters, for a median residence of $350,000, the property's annual property tax bill would increase by $955 per year.",
    },
    {
        "path": "us/in/frontier-school-corporation",
        "label": "Operating referendum",
        "file": "26-017-Frontier-School-Corporation-Operating-Determination.pdf",
        "text": "Shall Frontier School Corporation increase property taxes paid to the School Corporation for no more than eight (8) years for the purpose of funding and sustaining educational and operational stability in response to reductions in property tax revenue, supporting essential health and safety initiatives, attracting and retaining teachers and staff, managing class sizes, and funding academic and support programs to meet the educational needs of all students, by imposing a property tax rate that does not exceed $0.25 and results in a maximum annual amount that does not exceed $1,750,000? If this operating referendum public question is approved by the voters, for a median residence of $250,000, the property's annual property tax bill would increase by $284 per year.",
    },
    {
        "path": "us/in/eminence-community-school-corporation",
        "label": "Operating referendum",
        "file": "26-018-Eminence-Community-School-Corporation-Operating-Determination.pdf",
        "text": "Shall Eminence Community School Corporation increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and maintaining instructional support, opportunities for students, daily educational operations and retaining teachers and staff in response to reductions in property tax revenue by imposing a property tax rate that does not exceed $0.38 and results in a maximum annual amount that does not exceed $999,000? If this operating referendum public question is approved by the voters, for a median residence of $200,000, the property's annual property tax bill would increase by $328 per year.",
    },
    {
        "path": "us/in/shelbyville-central-schools",
        "label": "Operating referendum",
        "file": "26-019-Shelbyville-Central-Schools-Operating-Determination.pdf",
        "text": "Shall Shelbyville Central Schools increase property taxes paid to the school corporation for no more than 8 years for the purpose of funding and maintaining current educational and operational programs in response to reductions in property tax revenue, including student transportation, maintaining class sizes, school safety, and recruiting and retaining staff, by imposing a property tax rate that does not exceed $0.22 and results in a maximum annual amount that does not exceed $3,606,064. If this operating referendum public question is approved by the voters, for a median residence of $200,000, this property's annual property tax bill would increase by $191 per year.",
    },
    {
        "path": "us/in/union-township-school-corporation",
        "label": "Operating referendum",
        "file": "26-020-Union-Township-School-Corporation-Operating-Determination.pdf",
        "text": "Shall Union Township School Corporation increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and continuing educational and operational stability in response to reductions in property tax revenue, including attracting and retaining teachers and staff, maintaining class sizes, maintaining academic and educationally related programs, and maintaining essential safety and security standards for the children of Union Township by imposing a property tax rate that does not exceed $0.35 and results in a maximum annual amount that does not exceed $3,374,291. If this operating referendum public question is approved by the voters, for a median residence of $400,000, the property's annual property tax bill would increase by $681 per year.",
    },
    {
        "path": "us/in/mississinewa-community-school-corporation",
        "label": "Operating referendum",
        "file": "26-022-Mississinewa-Community-School-Corporation-Operating-Determination.pdf",
        "text": "Shall the Mississinewa Community School Corporation increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding the Putting Our Kids First Initiative, funding investments in student transportation, school security upgrades, and the competitive compensation needed to recruit and retain teachers and staff, by imposing a property tax rate that does not exceed $0.4950 per $100 of assessed value and results in a maximum annual amount that does not exceed $2,000,000? If this operating referendum public question is approved by the voters, for a median residence of $150,000, the property’s annual property tax bill would increase by $294 per year.",
    },
    {
        "path": "us/in/porter-township-school-corporation",
        "label": "Operating referendum",
        "file": "26-023-Porter-Township-School-Corporation-Operating-Determination.pdf",
        "text": "Shall Porter Township School Corporation increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and sustaining educational and operational funding stability in response to reductions in property tax revenue, including maintaining essential health and safety initiatives, including the employment and retention of school resource officers, supporting and retaining teachers and staff, managing class sizes, maintaining essential student transportation and funding academic and support programs to meet the educational needs of all students, by imposing a property tax rate that does not exceed $0.3576 and results in a maximum annual amount that does not exceed $3,500,000. If this operating referendum public question is approved by the voters, for a median residence of $350,000, the property’s annual property tax bill would increase by $599 per year.",
    },
    {
        "path": "us/in/wa-nee-community-schools",
        "label": "School safety referendum",
        "file": "26-026-Wa-Nee-Community-Schools-Safety-Determination.pdf",
        "text": "Shall the Wa-Nee Community School Corporation increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding improvements for the safety, security, and well-being of Wa-Nee students by imposing a property tax rate that does not exceed $0.10 and results in a maximum annual amount that does not exceed $1,750,000. If this school safety referendum public question is approved by the voters, for a median residence of $250,000, the property’s annual property tax bill would increase by $114 per year.",
    },
    {
        "path": "us/in/oregon-davis-school-corporation",
        "label": "Operating referendum renewal",
        "file": "26-027-Oregon-Davis-School-Corporation-Operating-Determination.pdf",
        "text": "Shall the Oregon-Davis School Corporation continue to increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding current educational and operational expenses by imposing a property tax rate that does not exceed nineteen cents ($0.19) and results in a maximum annual amount that does not exceed $800,000? If this operating referendum is NOT approved by the voters, for a median residence of $150,000, the property's annual tax bill would decrease by $173 per year. If this operating referendum public question is approved by the voters, it would be renewal of the most recent operating referendum public question passed in 2018 with a property tax rate of twenty-nine cents ($0.29).",
    },
    {
        "path": "us/in/indianapolis-public-schools",
        "label": "Operating referendum",
        "file": "26-029-Indianapolis-Public-Education-Corporation-Operating-Determination.pdf",
        "text": "Shall the Indianapolis Public Education Corporation increase property taxes paid to the Indianapolis Public Schools (“IPS”) and participating charter schools for no more than four (4) years for the purpose of funding teacher and staff retention, professional development, and advancing academic programs and opportunities by imposing a property tax rate that does not exceed $0.372 per $100 of assessed value and results in a maximum annual amount that does not exceed $95,000,000? If this operating referendum public question is approved by the voters, for a median residence of $150,000, the property's annual property tax bill would increase by $221 per year.",
    },
    {
        "path": "us/in/carmel-clay-schools",
        "label": "Operating referendum",
        "file": "26-030-Carmel-Clay-Schools-Operating-Determination.pdf",
        "text": "Shall Carmel Clay Schools increase property taxes paid to the School Corporation for no more than eight (8) years for the purpose of funding and sustaining educational and operational stability in response to reductions in property tax revenue by repealing and replacing its existing referendums, including supporting student safety, health, and well-being, including the employment and retention of school resource officers; supporting and retaining teachers and staff; and funding academic and support programs to meet the educational needs of all students, by imposing a property tax rate that does not exceed $0.4274 and results in a maximum annual amount that does not exceed $61,981,519? If this operating referendum public question is approved by the voters, for a median residence of $500,000, the property's annual property tax bill would increase by $1,062 per year. The most recent operating referendum public question proposed by the school corporation was held in 2023 and passed.",
    },
    {
        "path": "us/in/hamilton-southeastern-schools",
        "label": "Operating referendum",
        "file": "26-032-A-Hamilton-Southeastern-Schools-Operating-Determination.pdf",
        "text": "Shall the Hamilton Southeastern Schools increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding the HSE Referendum Program by (1) repealing the existing referendum fund tax levy extended in 2023, and (2) replacing the existing referendum fund tax levy with a new referendum fund tax levy which will provide funding for (a) the salary and benefits of approximately 275 staff members, (b) the behavioral health contract that supports therapists in our school buildings with essential mental health support during the school day, (c) the shared salaries and benefits of school resource officers, (d) the salary and benefits of the Director of School Safety, (e) the salaries and benefits of traffic control and after school event security, and (f) the purchase of safety and security supplies and equipment by imposing a property tax rate that does not exceed $0.3600 and results in a maximum annual amount that does not exceed $47,500,000. If this operating referendum public question is approved by the voters, for a median residence of $400,000, the property's annual property tax bill would increase by $700 per year. The most Page 2 of 3 recent operating referendum public question proposed by the school corporation was held in 2023 and passed.",
    },
    {
        "path": "us/in/elwood-community-school-corporation",
        "label": "Operating referendum",
        "file": "26-033-Elwood-Community-School-Corporation-Operating-Determination.pdf",
        "text": "Shall the Elwood Community School Corporation, Madison County, Indiana, increase property taxes paid to the school corporation for no more than eight (8) years immediately following the holding of the referendum for the purpose of funding teacher and staff retention and recruitment efforts to maintain current class sizes, student transportation, student and staff technology programs, and school safety and security programs and projects by imposing a property tax rate that does not exceed $0.4900 per $100 of net assessed value and results in a maximum annual amount that does not exceed $2,500,000. If this operating referendum public question is approved by the voters, for a median residence of $100,000, the property’s annual property tax bill would increase by $245 per year.",
    },
    {
        "path": "us/in/crown-point-community-school-corporation",
        "label": "Operating referendum",
        "file": "26-034-Crown-Point-Community-School-Corporation-Operating.pdf",
        "text": "Shall Crown Point Community Schools increase property taxes paid to the School Corporation for no more than eight (8) years for the purpose of funding and sustaining educational and operational funding stability in response to reductions in property tax revenue, supporting essential health and safety initiatives, supporting and retaining teachers and staff, maintaining class sizes, student transportation, and funding academic and support programs to meet the educational needs of all students, by imposing a property tax rate that does not exceed $0.3870 and results in a maximum annual amount that does not exceed $19,750,000? If this operating referendum public question is approved by the voters, for a median residence of $350,000 the property's annual property tax bill would increase by $648 per year. The most recent operating referendum public question proposed by the school corporation was held in 2025 and passed.",
    },
    {
        "path": "us/in/tipton-community-school-corporation",
        "label": "Operating referendum",
        "file": "26-036-Tipton-Community-School-Corporation-Operating-Determination.pdf",
        "text": "Shall the Tipton Community School Corporation increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of providing funding to maintain academic programs, class sizes, and student support, health, and safety services, attract and retain teachers and support staff, maintain student transportation services, and sustain educational and operational funding stability by imposing a property tax rate that does not exceed $0.25 and results in a maximum annual amount that does not exceed $2,000,000. If this operating referendum public question is approved by the voters, for a median residence of $200,000, the property's annual property tax bill would increase by $216 per year.",
    },
    {
        "path": "us/in/franklin-community-school-corporation",
        "label": "Operating referendum",
        "file": "26-037-Franklin-Community-Schools-Operating-Determination.pdf",
        "text": "Shall Franklin Community Schools increase property taxes paid to the School Corporation for no more than eight (8) years for the purpose of funding and sustaining educational and operational funding stability in response to reductions in property tax revenue, including supporting and retaining teachers and staff, essential student health and safety initiatives, managing class sizes, academic programs, and opportunities to meet the learning needs of all students, by imposing a property tax rate that does not exceed $0.2900 and results in a maximum annual amount that does not exceed $9,000,000? If this operating referendum public question is approved by the voters, for a median residence of $250,000, the property’s annual property tax bill would increase by $329 per year. The most recent operating referendum public question proposed by the school corporation was held in May 2019 and passed.",
    },
    {
        "path": "us/in/river-forest-community-school-corporation",
        "label": "Operating referendum renewal",
        "file": "26-038-River-Forest-Community-School-Corporation-Operating-Extension-Determination.pdf",
        "text": "Shall the River Forest Community School Corporation continue to increase property taxes paid to the School Corporation for no more than eight (8) years for the purpose of sustaining educational and operational funding stability in response to reductions in property tax revenue, maintaining student transportation, supporting and retaining teachers and staff, and supporting essential health and safety initiatives, including the retention of school resource officers by imposing a property tax rate that does not exceed $0.9912 and results in a maximum annual amount that does not exceed $3,000,000. If this operating referendum public question is NOT approved by the voters, for a median residence of $150,000, the property's annual tax bill would decrease by $589 per year. If this operating referendum public question is approved by the voters, it would be a renewal of the most recent operating referendum public question passed in 2019 with a property tax rate of $1.19.",
    },
    {
        "path": "us/in/culver-community-schools-corporation",
        "label": "Operating referendum",
        "file": "26-041-Culver-CSC-Operating.pdf",
        "text": "Shall Culver Community Schools Corporation increase property taxes paid to the school corporation for no more than eight (8) years for the purpose of funding and sustaining educational and operational funding stability in response to reductions in property tax revenue, including maintaining student safety, health, and well- being initiatives; attracting, retaining, and supporting teachers and staff; maintaining class sizes; and funding academic and support programs to meet the educational needs of all students, by imposing a property tax rate that does not exceed $0.24 and results in a maximum annual amount that does not exceed $2,899,000? If this operating referendum public question is approved by the voters, for a median residence of $200,000, the property’s annual property tax bill would increase by $208 per year. The most recent operating referendum public question proposed by the school corporation was held in November 2018 and passed.",
    },
    {
        "path": "us/in/smith-green-community-schools",
        "label": "Operating referendum renewal",
        "file": "26-042-Smith-Green-Community-Schools-Operating.pdf",
        "text": "Shall Smith-Green Community Schools continue to increase property taxes paid to the School Corporation for no more than eight (8) years for the purpose of funding and sustaining educational and operational funding stability in response to reductions in property tax revenue, maintaining health and safety initiatives, supporting and retaining teachers and staff, maintaining class sizes and transportation services, and funding academic and support programs to meet the educational needs of all students, while changing the current maximum operating referendum tax rate of $0.6280, by imposing a property tax rate that does not exceed $0.4988 and results in a maximum annual amount that does not exceed $2,176,300? If this operating referendum public question is NOT approved by the voters, for a median residence of $200,000, the property’s annual property tax bill would decrease by $431 per year. If this operating referendum public question is approved by the voters, it would be a renewal of the most recent operating referendum public question passed in 2018 with a property tax rate of $0.6280.",
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
