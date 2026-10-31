from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.ca_cd48_2005"
VERSION = "1"
PRIMARY_URL = "https://ocvote.gov/fileadmin/user_upload/sov/e26/sov.pdf"
GENERAL_URL = "https://elections.cdn.sos.ca.gov/special-elections/2005-cd48/secd48.doc"
GENERAL_TITLE = (
    "Official canvass, U.S. Representative in Congress, 48th District, Special General Election, December 6, 2005"
)
PUBLISHERS = {
    "primary": (
        "Orange County Registrar of Voters",
        "Statement of Votes, October 4, 2005, 48th Congressional District Primary Election",
        "https://ocvote.gov/data/election-results-archives/archived-statement-of-votes",
        PRIMARY_URL,
    ),
    "general": (
        "California Secretary of State",
        GENERAL_TITLE,
        "https://www.sos.ca.gov/elections/prior-elections",
        GENERAL_URL,
    ),
}
PARTIES = {
    "REP": "republican-party",
    "DEM": "democratic-party",
    "LIB": "libertarian-party",
    "GRN": "green-party",
    "AI": "american-independent-party",
}
PRIMARY = [
    ("John Kelly", "REP", 1070),
    ("John Campbell", "REP", 41420),
    ("David R. Crouch", "REP", 523),
    ("Bruce Cohen", "LIB", 731),
    ("Steve Young", "DEM", 7941),
    ("Don Udall", "REP", 1417),
    ("Bea Foster", "DEM", 2944),
    ("Scott Maccabe", "REP", 397),
    ("Guy E. Mailly", "REP", 153),
    ("Marsha A. Morris", "REP", 351),
    ("Marilyn C. Brewer", "REP", 15595),
    ("Marshall Samuel Sanders", "REP", 110),
    ("Edward A. Suppe", "REP", 101),
    ("Tom Pallow", "DEM", 307),
    ("Bea Tiritilli", "GRN", 790),
    ("John Graham", "DEM", 3667),
    ("Jim Gilchrist", "AI", 13423),
    ("Steven Wesley Blake", "REP", 2),
    ("Delecia Holt", "REP", 11),
]
ADVANCED = {"John Campbell", "Steve Young", "Bruce Cohen", "Bea Tiritilli", "Jim Gilchrist"}
GENERAL = [
    ("John Campbell", "REP", 46184),
    ("Bruce Cohen", "LIB", 974),
    ("Steve Young", "DEM", 28853),
    ("Bea Tiritilli", "GRN", 1430),
    ("Jim Gilchrist", "AI", 26507),
    ("Steven Wesley Blake", "REP", 0),
]
DAYS = {"primary": date(2005, 10, 4), "general": date(2005, 12, 6)}
NOTE = (
    "The district lies wholly within Orange County, so the county's statement of votes is the district total. "
    "Totals are the Grand Totals lines of the county's statement and the official canvass, read from their text "
    "layers; the two named write-in candidates in the primary come from the county's separate write-in tally."
)


def fetch() -> None:
    raw.fetch(SOURCE, PRIMARY_URL, suffix=".pdf")
    raw.fetch(SOURCE, GENERAL_URL, suffix=".doc")


def emit() -> Path:
    if sum(votes for _, _, votes in GENERAL) != 103948:
        raise ValueError("December 6 candidate votes do not sum to the canvass")
    with connect() as conn:
        house = conn.execute(
            "select entity_id from external_id where scheme = 'us.body' and value = 'house'"
        ).fetchone()
        reference = conn.execute(
            """select c.id, c.district_area_id, c.boundary_set_id from contest c
               join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
               where x.value = '2006-11-07:CA:house:48'"""
        ).fetchone()
        if house is None or reference is None:
            raise ValueError("the House body and the 2006 contest must already exist")
        known = {
            slug(text): person
            for text, person in conn.execute(
                """select distinct n.text, c.person_id from candidacy c
                   join contest_option o on o.id = c.option_id join contest ct on ct.id = o.contest_id
                   join name n on n.entity_id = c.person_id and n.is_preferred
                   where ct.district_area_id = %s""",
                [reference[1]],
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's 48th congressional district special election of 2005, the October 4 primary and "
            "December 6 general, from the Orange County Registrar of Voters and the Secretary of State",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources: dict[str, UUID] = {}
        for round_name, (publisher, title, link, url) in PUBLISHERS.items():
            sources[round_name] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[round_name],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=link,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), sources[round_name])
        body = cs.key("us.body", "house")
        system = cs.key("us.electoral_system", "two_round")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        created: set[UUID] = set()
        previous = None
        for number, (round_name, rows) in enumerate((("primary", PRIMARY), ("general", GENERAL)), start=1):
            day = DAYS[round_name]
            key = f"{day}:CA:house:48:special"
            election = cs.key("us.election", f"{day}:CA:special")
            cs.add(
                "election",
                id=election,
                polity_id=cs.polity("US-CA"),
                parent_id=None,
                kind="special",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{day:%B} {day.day}, {day.year} California special {round_name} election")
            cs.add("slug", path=f"us/ca/{day}-special", entity_id=election)
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=reference[1],
                boundary_set_id=reference[2],
                magnitude=1,
                term="remainder",
                round_number=number,
                previous_round_id=previous,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"2005 U.S. House special {round_name} election in California's 48th congressional district")
            cs.add("slug", path=f"us/ca/{day}-special/house-48-special", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[round_name],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            unit = cs.unit("us.cd", "0648", day)
            for text, party_code, votes in rows:
                person = known.get(slug(text)) or cs.key(f"{SOURCE}.person", slug(text))
                if person in cs.minted and person not in created:
                    created.add(person)
                    cs.add("person", id=person)
                    name(person, text)
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties[party_code],
                    ballot_label=text,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=text,
                    is_independent=False,
                    is_incumbent=False,
                )
                elected = round_name == "general" and text == "John Campbell"
                outcome = (
                    "elected" if elected else "advanced" if round_name == "primary" and text in ADVANCED else "lost"
                )
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=1,
                    flags=0,
                    value=votes,
                )
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=4,
                count_round=1,
                flags=0,
                value=sum(votes for _, _, votes in rows),
            )
            previous = contest
        return cs.write()
