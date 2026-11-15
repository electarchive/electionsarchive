import re
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.parish_by"
VERSION = "1"
ARCHIVES = "gb.council_archives"
PAGE = "https://web.archive.org/web/20011123165035id_/http://www.shepway.gov.uk:80/index/news/press-releases/by2.htm"
LAD = "E07000112"
PARISH = "E04005020"
DAY = date(2001, 2, 22)
WARD = "Hythe North"
WARDS = "gb.parish_ward"
VOTES, REGISTERED = 1, 2
PARTIES = {"Con": "Q9626", "Lib Dem": "Q9624", "Lab": "Q9630"}
CONTEXT = (
    "A by-election to fill a vacancy on Hythe Town Council, run by Shepway District Council. Hythe Town Council's "
    "wards are its own, not the district's."
)
CAVEAT = (
    "The press release prints a turnout of {printed}% of the {electorate} electors. The three candidates' votes "
    "total {total}, {actual}% of the electorate, so the printed turnout implies ballot papers the release does not "
    "account for; no ballots or rejected papers are stored."
)


def fetch() -> None:
    raw.cached(ARCHIVES, PAGE)


def read() -> tuple[int, float, list[tuple[str, str, int, bool]]]:
    page = Path(raw.cached(ARCHIVES, PAGE).path).read_text(encoding="latin-1")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&nbsp;", " ", page))
    body = text.split("Results for Hythe Town Council By-Election")[1]
    head = re.search(r"HYTHE NORTH \(eligible to vote: (\d+)\) ONE SEAT \([^)]*\) Turnout: ?([\d.]+)%", body)
    assert head is not None
    rows = [
        (name.strip(), party, int(votes), bool(elected))
        for name, party, votes, elected in re.findall(
            r"(\S+(?: \S+)*?) (Lab|Con|Lib Dem) (\d+)( ELECTED)?", body.split("NAME PARTY VOTES")[1]
        )
    ]
    return int(head.group(1)), float(head.group(2)), rows


def emit() -> Path:
    electorate, turnout, rows = read()
    if len(rows) != 3 or sum(elected for *_, elected in rows) != 1:
        raise ValueError(f"unexpected candidates: {rows}")
    total = sum(votes for _, _, votes, _ in rows)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Hythe Town Council and its Hythe North by-election of 22 February 2001, from Shepway District "
            "Council's press release",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        polity = cs.key("gb.polity", PARISH)
        parish_area = cs.area("gb.parish", PARISH)
        if polity in cs.minted or parish_area in cs.minted:
            raise ValueError("gb.parish must hold Hythe")
        path = conn.execute(
            """select s.path from external_id e join slug s on s.entity_id = e.entity_id and s.is_current
               where e.scheme = 'gb.polity' and e.value = %s""",
            [PARISH],
        ).fetchone()
        assert path is not None
        system = cs.key("gb.electoral_system", "fptp")
        if system in cs.minted:
            raise ValueError("publish gb.parliament first")
        source = cs.source("Shepway District Council", "Shepway local by-election results, 22 February 2001")
        if source in cs.minted:
            raise ValueError("gb.council_archives must hold the press release source")
        body = cs.key("gb.body", PARISH)
        cs.add("body", id=body, polity_id=polity, kind="council")
        name(body, "Hythe Town Council")
        cs.add("citation", entity_id=body, source_id=source)

        code = f"{PARISH}:hythe-north"
        area = cs.area(WARDS, code)
        cs.add("area", id=area, scheme_code=WARDS)
        cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
        cs.add("area_parent", area_id=area, parent_id=parish_area, valid_from=None, valid_to=None)
        name(area, WARD)
        unit = cs.unit(WARDS, code)
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=polity,
            scheme_code=WARDS,
            code=code,
            area_relation="equals",
            area_id=area,
            kind="standard",
            valid_from=None,
        )
        name(unit, WARD)

        election_key = f"{DAY}:pc:{PARISH}"
        election = cs.key("gb.election", election_key)
        cs.add("election", id=election, polity_id=polity, kind="by_election", date_from=DAY, date_to=DAY, status="held")
        name(election, "2001 Hythe Town Council by-election")
        election_path = f"{path[0]}/{DAY}-by-election"
        cs.add("slug", path=election_path, entity_id=election)
        cs.add("note", entity_id=election, kind="context", body_md=CONTEXT)

        contest_key = f"{election_key}:hythe-north"
        contest = cs.key("gb.contest", contest_key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=body,
            district_area_id=area,
            magnitude=1,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        name(contest, "Hythe Town Council, Hythe North ward by-election, 2001")
        cs.add("slug", path=f"{election_path}/hythe-north", entity_id=contest)
        cs.add("citation", entity_id=contest, source_id=source)
        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
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
        cs.add("citation", entity_id=result_set, source_id=source, locator={"url": PAGE})
        cs.add(
            "note",
            entity_id=result_set,
            kind="caveat",
            body_md=CAVEAT.format(
                printed=f"{turnout:g}", electorate=electorate, total=total, actual=f"{100 * total / electorate:.2f}"
            ),
        )
        cs.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=None,
            channel=0,
            measure=REGISTERED,
            count_round=0,
            flags=0,
            value=electorate,
        )
        for position, (printed, party, votes, elected) in enumerate(rows, start=1):
            forenames, surname = printed.rsplit(" ", 1)
            person_name = f"{surname.title()}, {forenames}"
            option_key = f"{contest_key}:{position}"
            person = cs.key(f"{SOURCE}.person", f"{PARISH}:{person_name}")
            cs.add("person", id=person)
            name(person, person_name)
            party_id = cs.key("wikidata", PARTIES[party])
            if party_id in cs.minted:
                raise ValueError(f"{PARTIES[party]} is not a party in the archive")
            option = cs.key(f"{SOURCE}.option", option_key)
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party_id,
                ballot_label=person_name,
            )
            cs.add(
                "candidacy",
                id=cs.key(f"{SOURCE}.candidacy", option_key),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=printed,
                member_of_party_id=party_id,
                is_independent=False,
            )
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=votes,
            )
            if elected:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
