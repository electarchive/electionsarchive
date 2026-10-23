from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.unopposed"
VERSION = "1"
CITATIONS = {
    "ap-2007": (
        "The Associated Press",
        "Kennedy returns unopposed to Louisiana's treasury department",
        "https://houmatimes.com/news/kennedy-returns-unopposed-to-louisianas-treasury-department/",
    ),
    "american-press-2011": (
        "American Press",
        "2011 elections break new ground",
        "https://americanpress.com/2011/09/22/2011-elections-break-new-ground/",
    ),
    "times-picayune-2023": (
        "The Times-Picayune",
        "Republicans win dozens of Louisiana races unopposed",
        (
            "https://www.nola.com/news/politics/elections/republicans-win-dozens-of-louisiana-races-unopposed/"
            "article_fb4358bc-3dd3-11ee-b899-8b0831f1b7fb.html"
        ),
    ),
}
ROWS = [
    (date(2003, 10, 4), "LA", "Treasurer", "John Kennedy", "democratic-party", "ap-2007"),
    (date(2007, 10, 20), "LA", "Treasurer", "John Kennedy", "republican-party", "ap-2007"),
    (date(2011, 10, 22), "LA", "Treasurer", "John Kennedy", "republican-party", "american-press-2011"),
    (date(2011, 10, 22), "LA", "Attorney General", "Buddy Caldwell", "republican-party", "american-press-2011"),
    (date(2023, 10, 14), "LA", "Commissioner of Insurance", "Tim Temple", "republican-party", "times-picayune-2023"),
    (
        date(2023, 10, 14),
        "LA",
        "Commissioner of Agriculture and Forestry",
        "Mike Strain",
        "republican-party",
        "times-picayune-2023",
    ),
]
CONTEXT = {
    "LA": "Louisiana leaves a candidate with no opponent at the close of qualifying off the ballot and declares that "
    "candidate elected, so no votes were cast in this race."
}


def fetch() -> None:
    for _, _, url in CITATIONS.values():
        raw.fetch(SOURCE, url, suffix=".html")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana's statewide officers declared elected without opposition, 2003–2023",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources = {}
        for key, (publisher, title, url) in CITATIONS.items():
            sources[key] = cs.source(publisher, title)
            if sources[key] in cs.minted:
                cs.add(
                    "source",
                    id=sources[key],
                    kind="news_agency",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license=None,
                )
            cs.artifact(raw.cached(SOURCE, url), sources[key])
        areas = dict(
            conn.execute(
                """select substr(p.iso_code, 4), u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        )
        states = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        )
        two_round = cs.key("us.electoral_system", "two_round")
        for day, state, office, candidate, party, cited in ROWS:
            office_slug = slug(office)
            office_id = cs.key("us.office", f"{office_slug}:{state}")
            election = cs.key("us.election", f"{day}:{state}")
            party_id = cs.key("us.party", party)
            if cs.minted & {office_id, election, party_id, two_round}:
                raise ValueError(f"{day} {state} {office}: the office, election, party, and system must exist")
            contest_key = f"{day}:{state}:{office_slug}"
            contest = cs.key("us.contest", contest_key)
            if contest not in cs.minted:
                raise ValueError(f"{contest_key} is already held")
            found = conn.execute(
                """select cy.person_id from candidacy cy
                   join contest_option o on o.id = cy.option_id
                   join contest c on c.id = o.contest_id and c.office_id = %s
                   join election e on e.id = c.election_id
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where cy.role = 'head' and n.text ilike %s
                   order by e.date_from desc limit 1""",
                [office_id, f"%{candidate.split()[-1]}%"],
            ).fetchall()
            person = found[0][0] if found else cs.key(f"{SOURCE}.person", f"{state}:{slug(candidate)}")
            if person in cs.minted:
                cs.add("person", id=person)
                name(person, candidate)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office_id,
                district_area_id=areas[state],
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=two_round,
                default_result_kind="tacit",
            )
            name(contest, f"{day.year} {states[state]} {office} election")
            cs.add("slug", path=f"us/{state.lower()}/{day}-general/{office_slug}", entity_id=contest)
            option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party_id,
                ballot_label=candidate,
            )
            cs.add(
                "candidacy",
                id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head"),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate,
                is_independent=False,
            )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="tacit",
                status="active",
                source_id=sources[cited],
                is_default=True,
                completeness=None,
            )
            cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            cs.add("note", entity_id=result_set, kind="context", body_md=CONTEXT[state])
        return cs.write()
