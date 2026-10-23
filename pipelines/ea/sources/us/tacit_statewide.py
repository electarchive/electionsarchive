import re
import subprocess
from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.tacit_statewide"
VERSION = "1"
CITATIONS = {
    "ok-2002": (
        "Oklahoma State Election Board",
        "2002 Unopposed Candidates",
        "https://oklahoma.gov/content/dam/ok/en/elections/election-results/2002-election-results/2002-unopposed-candidates/02unopp.pdf",
    ),
    "sapulpa-murphy": (
        "Sapulpa Times",
        "Dana Murphy, Oklahoma Corporation Commission",
        "https://sapulpatimes.com/dana-murphy-oklahoma-corporation-commission/",
    ),
    "kgou-2014": (
        "KGOU",
        "Now Official: In Many Uncontested Races, Candidates Have Been Winners For Awhile",
        "https://www.kgou.org/politics-and-government/2014-11-04/now-official-in-many-uncontested-races-candidates-have-been-winners-for-awhile",
    ),
    "ok-2022": (
        "Oklahoma State Election Board",
        "2022 Candidate List Book",
        "https://oklahoma.gov/content/dam/ok/en/elections/candidate-filing-archives/2022-candidate-filing-archives/2022listbook-redacted.pdf",
    ),
    "nondoc-2022": (
        "NonDoc",
        "Who's running for statewide offices in Oklahoma?",
        "https://nondoc.com/2022/04/16/statewide-offices-oklahoma/",
    ),
    "eoa-beebe": (
        "Encyclopedia of Arkansas",
        "Mike Beebe (1946–)",
        "https://encyclopediaofarkansas.net/entries/mickey-dale-8718/",
    ),
}
OFFICIAL = {"ok-2002", "ok-2022"}
ROWS = [
    (date(2002, 11, 5), "OK", "Treasurer", "Robert Butkin", "democratic-party", "ok-2002"),
    (date(2010, 11, 2), "OK", "Corporation Commissioner", "Dana Murphy", "republican-party", "sapulpa-murphy"),
    (date(2014, 11, 4), "OK", "Attorney General", "Scott Pruitt", "republican-party", "kgou-2014"),
    (date(2014, 11, 4), "OK", "Treasurer", "Ken Miller", "republican-party", "kgou-2014"),
    (date(2014, 11, 4), "OK", "Auditor and Inspector", "Gary Jones", "republican-party", "kgou-2014"),
    (date(2014, 11, 4), "OK", "Insurance Commissioner", "John Doak", "republican-party", "kgou-2014"),
    (date(2014, 11, 4), "OK", "Corporation Commissioner", "Todd Hiett", "republican-party", "kgou-2014"),
    (date(2022, 11, 8), "OK", "Auditor and Inspector", "Cindy Byrd", "republican-party", "ok-2022"),
    (date(2022, 11, 8), "OK", "Insurance Commissioner", "Glen Mulready", "republican-party", "nondoc-2022"),
    (date(2002, 11, 5), "AR", "Attorney General", "Mike Beebe", "democratic-party", "eoa-beebe"),
]
CONTEXT = {
    "OK": "Oklahoma leaves an office off the general election ballot when only one candidate remains after the "
    "primaries, and that candidate is deemed elected, so no votes were cast in this race.",
    "AR": "Arkansas leaves an unopposed candidate's race off the general election ballot, so no votes were cast in "
    "this race; the Secretary of State's 2002 certification has no attorney general line.",
}


def fetch() -> None:
    for _, _, url in CITATIONS.values():
        raw.fetch(SOURCE, url, suffix=".pdf" if "download" in url or url.endswith(".pdf") else ".html")


def check() -> None:
    for key, (_, _, url) in CITATIONS.items():
        path = raw.cached(SOURCE, url).path
        text = (
            subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True, text=True).stdout
            if path.suffix == ".pdf"
            else re.sub(r"<[^>]+>", " ", path.read_text(errors="replace"))
        )
        for _, _, _, candidate, _, cited in ROWS:
            if cited == key and candidate.split()[-1].upper() not in text.upper():
                raise ValueError(f"{key}: {candidate} is not in {url}")


def emit() -> Path:
    check()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's statewide officers deemed elected without a general election opponent in 2002, 2010, "
            "2014, and 2022, and Arkansas's 2002 attorney general, as tacit results",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources = {}
        for key, (publisher, title, url) in CITATIONS.items():
            sources[key] = cs.source(publisher, title)
            if sources[key] in cs.minted:
                official = key in OFFICIAL
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
        system = cs.key("us.electoral_system", "fptp")
        for day, state, title, candidate, party, cited in ROWS:
            office = cs.key("us.office", f"{slug(title)}:{state}")
            election = cs.key("us.election", f"{day}:{state}")
            if cs.minted & {office, election, system}:
                raise ValueError(f"{day} {state} {title}: its election, office, and system must exist")
            contest_key = f"{day}:{state}:{slug(title)}"
            contest = cs.key("us.contest", contest_key)
            if contest not in cs.minted:
                continue
            surname = candidate.split()[-1]
            found = conn.execute(
                """select cy.person_id from candidacy cy
                   join contest_option o on o.id = cy.option_id
                   join contest c on c.id = o.contest_id and c.office_id = %s
                   join election e on e.id = c.election_id
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where cy.role = 'head' and n.text ilike %s and n.text ilike %s
                   order by e.date_from desc limit 1""",
                [office, f"%{surname}%", f"%{candidate.split()[0]}%"],
            ).fetchall()
            person = found[0][0] if found else cs.key(f"{SOURCE}.person", f"{state}:{slug(candidate)}")
            if person in cs.minted:
                cs.add("person", id=person)
                name(person, candidate)
            party_id = cs.key("us.party", party)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=areas[state],
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="tacit",
            )
            name(contest, f"{day.year} {states[state]} {title} election")
            cs.add("slug", path=f"us/{state.lower()}/{day}-general/{slug(title)}", entity_id=contest)
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
