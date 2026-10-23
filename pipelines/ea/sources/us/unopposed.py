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
    "hi-oe-2024": (
        "Hawaii Office of Elections",
        "2024 Candidate Election Status",
        "https://elections.hawaii.gov/wp-content/uploads/Office-of-Elections-_-2024-Candidate-Election-Status.pdf",
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
    *(
        (date(2024, 11, 5), "HI", seat, candidate, party, "hi-oe-2024")
        for seat, candidate, party in [
            ("senate:1", "Lorraine Rodero Inouye", "democratic-party"),
            ("senate:4", "Tim Richards", "democratic-party"),
            ("senate:5:special", "Troy N. Hashimoto", "democratic-party"),
            ("senate:19", "Henry J.C. Aquino", "democratic-party"),
            ("senate:24", "Jarrett Keohokalole", "democratic-party"),
            ("house:1", "Mark M. Nakashima", "democratic-party"),
            ("house:2", "Sue Lee Loy", "democratic-party"),
            ("house:9", "Justin H. Woodson", "democratic-party"),
            ("house:10", "Tyson K. Miyake", "democratic-party"),
            ("house:18", "Gene Ward", "republican-party"),
            ("house:19", "Mark Jun Hashem", "democratic-party"),
            ("house:23", "Ikaika M. Olds", "democratic-party"),
            ("house:25", "Kim Coco Iwamoto", "democratic-party"),
            ("house:26", "Della Au Belatti", "democratic-party"),
            ("house:28", "Daniel Holt", "democratic-party"),
            ("house:33", "Sam Satoru Kong", "democratic-party"),
            ("house:35", "Cory M. Chun", "democratic-party"),
            ("house:36", "Rachele F. Lamosao", "democratic-party"),
            ("house:43", "Kanani Souza", "republican-party"),
            ("house:44", "Darius K. Kila", "democratic-party"),
            ("house:51", "Lisa Marten", "democratic-party"),
        ]
    ),
]
BODIES = {"house": ("House of Representatives", "us.sldl"), "senate": ("Senate", "us.sldu")}
CONTEXT = {
    "LA": "Louisiana leaves a candidate with no opponent at the close of qualifying off the ballot and declares that "
    "candidate elected, so no votes were cast in this race.",
    "HI": "Hawaii declares a nominee elected after the primary when no other candidate remains for the general "
    "election (Haw. Rev. Stat. §12-41(a)), so the seat was not on the general election ballot.",
}


def fetch() -> None:
    for _, _, url in CITATIONS.values():
        raw.fetch(SOURCE, url, suffix="." + url.rsplit(".", 1)[1] if url.endswith(".pdf") else ".html")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana's statewide officers declared elected without opposition, 2003–2023, and Hawaii's "
            "2024 legislators declared elected after the primary",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources = {}
        for key, (publisher, title, url) in CITATIONS.items():
            sources[key] = cs.source(publisher, title)
            if sources[key] in cs.minted:
                official = "Office of Elections" in publisher
                cs.add(
                    "source",
                    id=sources[key],
                    kind="official_body" if official else "news_agency",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record" if official else None,
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
        own = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x join changeset c on c.id = x.changeset_id
                   where x.scheme = 'us.contest' and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        }
        systems = {key: cs.key("us.electoral_system", key) for key in ("two_round", "fptp")}
        for day, state, seat, candidate, party, cited in ROWS:
            chamber, _, rest = seat.partition(":")
            body_seats = chamber in BODIES and rest != ""
            district, _, special = rest.partition(":")
            election = cs.key("us.election", f"{day}:{state}")
            party_id = cs.key("us.party", party)
            if body_seats:
                chamber_name, scheme = BODIES[chamber]
                contest_key = f"{day}:{state}:{chamber}:{district}:{'gs' if special else 'g'}"
                body = cs.key("us.body", f"{state}:{chamber}")
                area = cs.area(scheme, f"{state}:{district}")
                required = {election, party_id, body, area, systems["fptp"]}
                fields = {"kind": "body_seats", "body_id": body, "district_area_id": area}
                system = systems["fptp"]
                where = f"{states[state]} {chamber_name} {'special election' if special else 'election'}, District {district}"
                path = f"state-{chamber}-{district}" + ("-special-election" if special else "")
                prior = "c.body_id = %s and c.district_area_id = %s"
                scope = [body, area]
            else:
                office_id = cs.key("us.office", f"{slug(seat)}:{state}")
                contest_key = f"{day}:{state}:{slug(seat)}"
                required = {office_id, election, party_id, systems["two_round"]}
                fields = {"kind": "office", "office_id": office_id, "district_area_id": areas[state]}
                system = systems["two_round"]
                where = f"{states[state]} {seat} election"
                path = slug(seat)
                prior = "c.office_id = %s"
                scope = [office_id]
            if cs.minted & required:
                raise ValueError(f"{contest_key}: its election, party, district, office or body, and system must exist")
            if contest_key in own:
                continue
            contest = cs.key("us.contest", contest_key)
            if contest not in cs.minted:
                raise ValueError(f"{contest_key} is already held")
            found = conn.execute(
                f"""select cy.person_id from candidacy cy
                    join contest_option o on o.id = cy.option_id
                    join contest c on c.id = o.contest_id and {prior}
                    join election e on e.id = c.election_id
                    join name n on n.entity_id = cy.person_id and n.is_preferred
                    where cy.role = 'head' and n.text ilike %s
                    order by e.date_from desc limit 1""",
                [*scope, f"%{candidate.split()[-1]}%"],
            ).fetchall()
            if not found:
                found = conn.execute(
                    """select distinct cy.person_id from candidacy cy
                       join contest_option o on o.id = cy.option_id
                       join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                       join name n on n.entity_id = cy.person_id and n.is_preferred
                       where cy.role = 'head' and x.value like %s and n.text ilike %s and n.text ilike %s""",
                    [f"%:{state}:%", f"%{candidate.split()[0]}%", f"%{candidate.split()[-1]}%"],
                ).fetchall()
                found = found if len(found) == 1 else []
            person = found[0][0] if found else cs.key(f"{SOURCE}.person", f"{state}:{slug(candidate)}")
            if person in cs.minted:
                cs.add("person", id=person)
                name(person, candidate)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                **fields,
                term="remainder" if special else "full",
                magnitude=1,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="tacit",
            )
            name(contest, f"{day.year} {where}")
            cs.add("slug", path=f"us/{state.lower()}/{day}-general/{path}", entity_id=contest)
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
