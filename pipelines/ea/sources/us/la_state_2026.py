import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.la_state_2026"
VERSION = "1"
LISTING = (
    "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=20261103/RacesCandidates_Multiparish.htm"
)
PUBLISHER = "Louisiana Secretary of State"
TITLE = "Election Results Data: November 3, 2026 races and candidates"
SITE = "https://voterportal.sos.la.gov/ElectionResults/"
RACE = re.compile(r"(PSC|BESE) -- District (\d+)")
BOARDS = {
    "PSC": ("public-service-commission", "Public Service Commission"),
    "BESE": ("bese", "Board of Elementary and Secondary Education"),
}
PARTIES = {"DEM": "democratic-party", "REP": "republican-party", "LBT": "libertarian-party"}


def fetch() -> None:
    content = httpx.get(
        LISTING, follow_redirects=True, timeout=120, headers={"User-Agent": "electionsarchive.com"}
    ).content
    raw.store(SOURCE, LISTING, content, suffix=".json", retrieved_at=datetime.now(UTC), media_type="application/json")


def emit() -> Path:
    listing = json.loads(raw.cached(SOURCE, LISTING).path.read_bytes())["Races"]["Race"]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana Public Service Commission and BESE district races on November 3, 2026, from the Secretary of State's race and candidate list",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=SITE, license=None)
        cs.artifact(raw.cached(SOURCE, LISTING), source)
        state_polity = cs.polity("US-LA")
        state_area = conn.execute(
            """select u.area_id from polity p
               join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-LA'"""
        ).fetchone()[0]
        election = cs.key("us.election", "2026-11-03:LA")
        fptp = cs.key("us.electoral_system", "fptp")
        counts = {"contests": 0, "candidates": 0}
        for race in listing:
            found = RACE.fullmatch(race["SpecificTitle"].strip())
            if found is None:
                continue
            board, number = found[1], found[2]
            tail_board, board_name = BOARDS[board]
            heading = f"{board_name}, District {number}"
            tail = f"LA:{tail_board}:district-{number}"
            code = f"22:{tail_board}:district-{number}"
            area = cs.area("us.local_district", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="us.local_district")
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=state_area)
                name(area, f"{heading}, LA")
            office = cs.key("us.office", tail)
            if office in cs.minted:
                cs.add("office", id=office, polity_id=state_polity, kind="other")
                name(office, heading)
            key = f"2026-11-03:{tail}"
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                body_id=None,
                district_area_id=area,
                seat_id=None,
                boundary_set_id=None,
                magnitude=int(race["NumberToBeElected"] or 1),
                term="full",
                round_number=1,
                previous_round_id=None,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"2026 {heading} election")
            cs.add("slug", path=f"us/la/2026-11-03-general/{slug(heading)}", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source, locator=None)
            counts["contests"] += 1
            seen: set[str] = set()
            for choice in race["Choice"]:
                match = re.fullmatch(r"(.*?)\s*\((\w+)\)", choice["Desc"].strip())
                text, party_code = (match[1], match[2]) if match else (choice["Desc"].strip(), "")
                text = text.replace('"', "").strip() if text.count('"') == 2 and text.startswith('"') else text
                if slug(text) in seen:
                    continue
                seen.add(slug(text))
                person = cs.key(f"{SOURCE}.person", f"{key}:{slug(text)}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, text)
                party = PARTIES.get(party_code)
                option = cs.key("us.option", f"{key}:{slug(text)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party) if party else None,
                    ballot_label=text,
                )
                candidacy = cs.key("us.candidacy", f"{key}:{slug(text)}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=text,
                    is_independent=party_code == "NOPTY",
                )
                cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
                counts["candidates"] += 1
        print(counts)
        return cs.write()
