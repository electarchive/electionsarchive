import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import leh
from ea.store import connect

SOURCE = "gb.london"
VERSION = "1"
VOTES, INVALID = 1, 5
REPORT_2004 = (
    "https://data.london.gov.uk/download/e585w/94efc8af-b7ce-4d60-9b18-e587084e207f/GLA-mayoral-election-2004.pdf"
)
GLA = "Greater London Authority"
GLRO = "Greater London Returning Officer"
PARTIES = {
    "LAB": "Labour Party",
    "CON": "Conservative Party",
    "LD": "Liberal Democrats",
    "UKIP": "UK Independence Party",
    "R": "Respect",
    "BNP": "British National Party",
    "GRE": "Green Party",
    "CPA": "Christian Peoples Alliance",
    "IWCA": "Independent Working Class Association",
    "PMSS": "Pro-Motorist Small Shop",
    "NLP": "Natural Law Party",
    "CC": "Christian Choice",
    "LL": "Left List",
    "ED": "English Democrats",
}
PEOPLE = {
    "Ken Livingstone": "Q220723",
    "Boris Johnson": "Q180589",
    "Sadiq Khan": "Q334155",
    "Zac Goldsmith": "Q391562",
    "Steven Norris": "Q7615093",
    "Frank Dobson": "Q300182",
    "Susan Kramer": "Q332968",
    "Simon Hughes": "Q334313",
    "George Galloway": "Q332483",
    "Gerard Batten": "Q1510131",
    "Siân Berry": "Q7533470",
}
Candidate = tuple[str, str | None, int, int | None]
ELECTIONS: dict[int, tuple[date, str, str, str, int | None, list[Candidate]]] = {
    2000: (
        date(2000, 5, 4),
        REPORT_2004,
        GLA,
        "The 2004 London Elections",
        None,
        [
            ("Ken Livingstone", None, 667877, 776427),
            ("Steven Norris", "CON", 464434, 564137),
            ("Frank Dobson", "LAB", 223884, None),
            ("Susan Kramer", "LD", 203452, None),
            ("Balram Gidoomal", "CPA", 42060, None),
            ("Darren Johnson", "GRE", 38121, None),
            ("Michael Newland", "BNP", 33569, None),
            ("Nicholas Hockney", "UKIP", 16324, None),
            ("Geoffrey Ben-Nathan", "PMSS", 9956, None),
            ("Ashwinkumar Tanna", None, 9015, None),
            ("Geoffrey Clements", "NLP", 5470, None),
        ],
    ),
    2004: (
        date(2004, 6, 10),
        REPORT_2004,
        GLA,
        "The 2004 London Elections",
        None,
        [
            ("Ken Livingstone", "LAB", 685548, 828390),
            ("Steven Norris", "CON", 542423, 667180),
            ("Simon Hughes", "LD", 284647, None),
            ("Francis Maloney", "UKIP", 115666, None),
            ("Lindsey German", "R", 61731, None),
            ("Julian Leppert", "BNP", 58407, None),
            ("Darren Johnson", "GRE", 57332, None),
            ("Balram Gidoomal", "CPA", 41698, None),
            ("Lorna Reid", "IWCA", 9542, None),
            ("Puvanarani Nagalingam", None, 6692, None),
        ],
    ),
    2008: (
        date(2008, 5, 1),
        "https://data.london.gov.uk/download/2nplx/53d5718d-9e17-47fc-811e-6f95a4d3f293/GLA-mayoral-elections-2008.pdf",
        GLA,
        "The 2008 London Elections",
        None,
        [
            ("Boris Johnson", "CON", 1044067, 1169046),
            ("Ken Livingstone", "LAB", 894317, 1029406),
            ("Brian Paddick", "LD", 236752, None),
            ("Siân Berry", "GRE", 77396, None),
            ("Richard Barnbrook", "BNP", 69753, None),
            ("Alan Craig", "CC", 39266, None),
            ("Gerard Batten", "UKIP", 22435, None),
            ("Lindsey German", "LL", 16803, None),
            ("Matt O'Connor", "ED", 10700, None),
            ("Winston McKenzie", None, 5396, None),
        ],
    ),
    2012: (
        date(2012, 5, 3),
        "https://www.londonelects.org.uk/files/Results%20factsheets%202012.pdf",
        "London Elects",
        "2012 election results for the Mayor of London and the London Assembly",
        None,
        [
            ("Boris Johnson", "Conservative Party", 971931, 1054811),
            ("Ken Livingstone", "Labour Party", 889918, 992273),
            ("Jenny Jones", "Green Party", 98913, None),
            ("Brian Paddick", "Liberal Democrats", 91774, None),
            ("Siobhan Benita", None, 83914, None),
            ("Lawrence Webb", "UK Independence Party", 43274, None),
            ("Carlos Cortiglia", "British National Party", 28751, None),
        ],
    ),
    2016: (
        date(2016, 5, 5),
        "https://www.londonelects.org.uk/files/Mayor%20of%20London%202016.pdf",
        GLRO,
        "Election of the London Mayor: Declaration of Results of Poll, 5 May 2016",
        49871,
        [
            ("Sadiq Khan", "Labour Party", 1148716, 1310143),
            ("Zac Goldsmith", "The Conservative Party Candidate", 909755, 994614),
            ("Siân Berry", "Green Party", 150673, None),
            ("Caroline Pidgeon", "London Liberal Democrats", 120005, None),
            ("Peter Whittle", "UK Independence Party (UKIP)", 94373, None),
            ("Sophie Walker", "Women's Equality Party", 53055, None),
            ("George Galloway", "Respect (George Galloway)", 37007, None),
            ("Paul Golding", "Britain First - Putting British people first", 31372, None),
            ("Lee Harris", "Cannabis is Safer than Alcohol", 20537, None),
            ("David Furness", "British National Party", 13325, None),
            ("Prince Zylinski", None, 13202, None),
            ("Ankit Love", "One Love Party", 4941, None),
        ],
    ),
    2021: (
        date(2021, 5, 6),
        "https://www.londonelects.org.uk/files/Mayoral%20Final%20Results%202021.pdf",
        GLRO,
        "Election of the Mayor of London: Official Declaration of Results of Poll, 6 May 2021",
        114201,
        [
            ("Sadiq Khan", "Labour Party", 1013721, 1206034),
            ("Shaun Bailey", "Conservative Party Candidate", 893051, 977601),
            ("Siân Berry", "Green Party", 197976, None),
            ("Luisa Porritt", "Liberal Democrats", 111716, None),
            ("Niko Omilana", None, 49628, None),
            ("Laurence Fox", "The Reclaim Party", 47634, None),
            ("Brian Rose", "London Real Party", 31111, None),
            ("Richard Hewison", "Rejoin EU", 28012, None),
            ("Count Binface", "Count Binface for Mayor of London", 24775, None),
            ("Mandu Reid", "Vote Women's Equality Party on orange", 21182, None),
            ("Piers Corbyn", "Let London Live", 20604, None),
            ("Vanessa Hudson", "Animal Welfare Party-People, Animals, Environment", 16826, None),
            ("Peter Gammons", "UKIP", 14393, None),
            ("Farah London", None, 11869, None),
            ("David Kurten", "Heritage Party", 11025, None),
            ("Nims Obunge", None, 9682, None),
            ("Steve Kelleher", "Social Democratic Party", 8764, None),
            ("Kam Balayev", "Renew", 7774, None),
            ("Max Fosh", None, 6309, None),
            ("Valerie Brown", "The Burning Pink Party", 5305, None),
        ],
    ),
    2024: (
        date(2024, 5, 2),
        "https://www.londonelects.org.uk/files/total_mayoral_results_2024.pdf",
        GLRO,
        "Election of the Mayor of London: Official Declaration of Result of Poll, 2 May 2024",
        11127,
        [
            ("Sadiq Khan", "Labour Party", 1088225, None),
            ("Susan Hall", "The Conservative Party Candidate", 812397, None),
            ("Rob Blackie", "Liberal Democrat", 145184, None),
            ("Zoë Garbett", "The Green Party", 145114, None),
            ("Howard Cox", "ReformUK – London Deserves Better", 78865, None),
            ("Natalie Campbell", None, 47815, None),
            ("Amy Gallagher", "Social Democratic Party", 34449, None),
            ("Femy Amin", "Animal Welfare Party - People, Animals, Environment", 29280, None),
            ("Andreas Michli", None, 26121, None),
            ("Tarun Ghulati", None, 24702, None),
            ("Count Binface", "Count Binface for Mayor of London", 24260, None),
            ("Nick Scanlon", "Britain First - No To Immigration", 20519, None),
            ("Brian Rose", "London Real Party – Transform London", 7501, None),
        ],
    ),
}
SV_UNTIL = 2021
RESOLVED = {
    "The Conservative Party Candidate": "Conservative Party",
    "Conservative Party Candidate": "Conservative Party",
    "London Liberal Democrats": "Liberal Democrats",
    "Liberal Democrat": "Liberal Democrats",
    "The Green Party": "Green Party",
    "UK Independence Party (UKIP)": "UK Independence Party",
    "UKIP": "UK Independence Party",
    "Respect (George Galloway)": "Respect",
    "Britain First - Putting British people first": "Britain First",
    "Britain First - No To Immigration": "Britain First",
    "ReformUK – London Deserves Better": "Reform UK",
    "Count Binface for Mayor of London": "Count Binface Party",
    "London Real Party – Transform London": "London Real Party",
    "Vote Women's Equality Party on orange": "Women's Equality Party",
    "Animal Welfare Party-People, Animals, Environment": "Animal Welfare Party",
    "Animal Welfare Party - People, Animals, Environment": "Animal Welfare Party",
}


def fetch() -> None:
    for _, url, _, _, _, _ in ELECTIONS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mayor of London elections 2000-2024, London-wide first and second rounds from the Greater London "
            "Returning Officer's declarations and the GLA's election reports",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        gb = cs.polity("GB")
        london = cs.key("gb.polity", "E12000007")
        region = cs.area("gb.region", "E12000007")
        if region in cs.minted:
            raise ValueError("publish gb.regions first: no London region area")
        cs.add("polity", id=london, kind="region", iso_code=None)
        cs.add("polity_parent", polity_id=london, parent_id=cs.polity("GB-ENG"))
        cs.add("polity_territory", polity_id=london, area_id=region)
        name(london, "Greater London")
        cs.add("slug", path="gb/london", entity_id=london)
        office = cs.key("gb.office", "mayor:london")
        cs.add("office", id=office, polity_id=london, body_id=None, kind="mayor")
        name(office, "Mayor of London")
        unit = cs.unit("gb.region", "E12000007")
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=london,
            scheme_code="gb.region",
            code="E12000007",
            area_relation="equals",
            area_id=region,
            kind="standard",
            valid_from=None,
        )
        name(unit, "London")
        fptp = cs.key("gb.electoral_system", "fptp")
        sv = cs.key("gb.electoral_system", "sv")
        if fptp in cs.minted or sv in cs.minted:
            raise ValueError("publish gb.parliament and gb.mayors first")
        parties: dict[str, UUID] = {}
        persons: dict[str, UUID] = {}
        sources: dict[str, UUID] = {}
        for year, (day, url, publisher, title, rejected, candidates) in ELECTIONS.items():
            artifact = raw.cached(SOURCE, url)
            text = subprocess.run(
                ["pdftotext", "-layout", str(artifact.path), "-"], check=True, capture_output=True
            ).stdout.decode(errors="replace")
            printed = set(re.findall(r"\d+", re.sub(r"(?<=\d)[,.](?=\d{3})", "", text)))
            figures = {votes for _, _, first, final in candidates for votes in (first, final) if votes is not None}
            if rejected is not None:
                figures.add(rejected)
            if missing := sorted(votes for votes in figures if str(votes) not in printed):
                raise ValueError(f"{year}: {missing} not printed in {url}")
            if url not in sources:
                sources[url] = cs.source(publisher, title)
                cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
                cs.artifact(artifact, sources[url])
            source = sources[url]
            national = conn.execute(
                "select entity_id from external_id where scheme = 'gb.election' and value = %s", [f"{day}:local:gb"]
            ).fetchone()
            election = cs.key("gb.election", f"{day}:gla")
            cs.add(
                "election",
                id=election,
                polity_id=london,
                parent_id=None if national is None else national[0],
                kind="general",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{year} London elections")
            cs.add("slug", path=f"gb/london/{day}-general", entity_id=election)
            supplementary = year <= SV_UNTIL
            contest_key = f"{day}:mayor:london"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                body_id=None,
                district_area_id=region,
                magnitude=1,
                round_number=1,
                electoral_system_id=sv if supplementary else fptp,
                default_result_kind="counted",
                term="full",
            )
            name(contest, f"Mayor of London, {year}")
            cs.add("slug", path=f"gb/london/{day}-general/mayor", entity_id=contest)
            result_set = cs.key("gb.london.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("citation", entity_id=result_set, source_id=source)
            first_round = 1 if supplementary else 0
            if rejected is not None:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=INVALID,
                    count_round=first_round,
                    flags=0,
                    value=rejected,
                )
            winner = max(candidates, key=lambda row: (row[3] or 0) if supplementary else row[2])[0]
            for full, party_code, first, final in candidates:
                label = PARTIES.get(party_code, party_code) if year <= 2008 else party_code
                owner = None
                if label is not None:
                    label = RESOLVED.get(label, label)
                    if label not in parties:
                        found = cs.key(*leh.RESOLVED[label]) if label in leh.RESOLVED else known.get(leh.fold(label))
                        if found is None:
                            found = cs.key("gb.party", slug(label))
                            if found in cs.minted:
                                cs.add("party", id=found, kind="party", polity_id=gb)
                                name(found, label)
                        parties[label] = found
                    owner = parties[label]
                if full not in persons:
                    persons[full] = (
                        cs.key("wikidata", PEOPLE[full]) if full in PEOPLE else cs.key("gb.london.person", full)
                    )
                    if persons[full] in cs.minted:
                        if full in PEOPLE:
                            raise ValueError(f"{full}: no person with {PEOPLE[full]}")
                        cs.add("person", id=persons[full])
                        name(persons[full], full)
                option_key = f"{contest_key}:{slug(full)}"
                option = cs.key("gb.london.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=owner,
                    ballot_label=full,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("gb.london.candidacy", option_key),
                    person_id=persons[full],
                    option_id=option,
                    role="head",
                    ballot_name=full,
                    member_of_party_id=owner,
                    is_independent=owner is None,
                )
                rounds = {first_round: first}
                if supplementary:
                    rounds[2] = final or 0
                for number, votes in rounds.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=number,
                        flags=0,
                        value=votes,
                    )
                if full == winner:
                    kinds = ("won", "elected")
                elif supplementary and final is None:
                    kinds = ("eliminated",)
                else:
                    kinds = ("lost",)
                for kind in kinds:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        return cs.write()
