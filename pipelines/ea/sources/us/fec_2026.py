from datetime import date
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.fec_2026"
VERSION = "1"
DAY = date(2026, 11, 3)
REDISTRICTED = {"TX", "CA", "MO", "NC", "OH", "UT"}
CLASS_THREE_SPECIALS = ("OH", "FL")
SYSTEM_OVERRIDES = {"AK": "top_four", "LA": "fptp"}
NOMINEE_PRIMARIES = {"NM": "2026-06-02", "VT": "2026-08-11"}
ORDINALS = {1: "st", 2: "nd", 3: "rd"}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The 2026-11-03 general election: every House and Senate contest and the 36 governor contests as scheduled, with candidates only where the archive's own primary result sets name a nominee",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        us = cs.polity("US")
        national = cs.key("us.election", f"{DAY}")
        cs.add(
            "election",
            id=national,
            polity_id=us,
            parent_id=None,
            kind="general",
            date_from=DAY,
            date_to=DAY,
            status="scheduled",
        )
        name(national, "2026 United States general election")
        cs.add("slug", path=f"us/{DAY}-general", entity_id=national)

        systems = {key: cs.key("us.electoral_system", key) for key in SYSTEM_OVERRIDES.values()}
        elections: dict[str, UUID] = {}

        templates = conn.execute(
            """select x.value, co.kind, co.office_id, co.body_id, co.district_area_id, co.seat_id,
                      co.boundary_set_id, co.magnitude, co.term, co.electoral_system_id,
                      co.default_result_kind, e.polity_id
               from contest co
               join election e on e.id = co.election_id
               join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
               where x.value ~ '^2024-11-05:[A-Z]{2}:house:[0-9]+$'
                  or x.value ~ '^2020-11-03:[A-Z]{2}:senate$'
                  or x.value ~ '^2022-11-08:(OH|FL):senate$'
                  or x.value ~ '^2022-11-08:(?!GU|VI)[A-Z]{2}:governor$'"""
        ).fetchall()
        latest_plan = conn.execute(
            "select id from boundary_set where scheme_code = 'us.cd' order by valid_from desc limit 1"
        ).fetchone()[0]
        state_names = dict(
            conn.execute(
                """select right(p.iso_code, 2), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred
                   where p.iso_code ~ '^US-[A-Z]{2}$'"""
            ).fetchall()
        )

        nominees: dict[str, list[tuple[str, UUID, UUID, UUID, str]]] = {}
        for value, party, ballot, person, party_id in conn.execute(
            """select x.value, split_part(x.value, ':', 4), op.ballot_label, cy.person_id, op.party_id
               from external_id x
               join result_set rs on rs.contest_id = x.entity_id and rs.status = 'active'
               join outcome o on o.result_set_id = rs.id and o.kind = 'won'
               join contest_option op on op.id = o.option_id
               join candidacy cy on cy.option_id = op.id and cy.role = 'head'
               where x.scheme = 'us.contest' and x.value ~ '^2026-[0-9-]+:(NM|VT):governor:[a-z]+$'"""
        ):
            state = value.split(":")[1]
            nominees.setdefault(state, []).append((value, ballot, person, party_id, party))

        counts = {"house": 0, "senate": 0, "governor": 0, "candidates": 0}
        for (
            value,
            kind,
            office,
            body,
            area,
            seat,
            plan,
            magnitude,
            term,
            system,
            result_kind,
            polity,
        ) in sorted(templates):
            _, state, chamber, *rest = value.split(":")
            if chamber == "house" and state in REDISTRICTED:
                continue
            state_name = state_names[state]
            if state not in elections:
                elections[state] = cs.key("us.election", f"{DAY}:{state}")
                cs.add(
                    "election",
                    id=elections[state],
                    polity_id=polity,
                    parent_id=national,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    status="scheduled",
                )
                name(elections[state], f"2026 {state_name} general election")
                cs.add("slug", path=f"us/{state.lower()}/{DAY}-general", entity_id=elections[state])
            key = f"{DAY}:{state}:{chamber}" + (f":{rest[0]}" if chamber == "house" else "")
            contest = cs.key("us.contest", key)
            special = chamber == "senate" and state in CLASS_THREE_SPECIALS
            cs.add(
                "contest",
                id=contest,
                election_id=elections[state],
                kind=kind,
                office_id=office,
                body_id=body,
                district_area_id=area,
                seat_id=seat,
                boundary_set_id=latest_plan if chamber == "house" else None,
                magnitude=magnitude,
                term="remainder" if special else term,
                round_number=1,
                electoral_system_id=systems[SYSTEM_OVERRIDES[state]] if state in SYSTEM_OVERRIDES and chamber != "governor" else system,
                default_result_kind=result_kind,
            )
            counts[chamber] += 1
            if chamber == "house":
                number = int(rest[0])
                suffix = "th" if 10 <= number % 100 <= 20 else ORDINALS.get(number % 10, "th")
                at_large = conn.execute(
                    "select 1 from external_id where scheme = 'us.contest' and value = %s",
                    [f"2024-11-05:{state}:house:2"],
                ).fetchone() is None
                title = (
                    f"2026 U.S. House election in {state_name}'s at-large congressional district"
                    if at_large
                    else f"2026 U.S. House election in {state_name}'s {number}{suffix} congressional district"
                )
                path = f"us/{state.lower()}/{DAY}-general/house-{number}"
            elif chamber == "senate":
                title = f"2026 U.S. Senate {'special ' if special else ''}election in {state_name}"
                path = f"us/{state.lower()}/{DAY}-general/senate"
            else:
                title = f"2026 {state_name} gubernatorial election"
                path = f"us/{state.lower()}/{DAY}-general/governor"
            name(contest, title)
            cs.add("slug", path=path, entity_id=contest)
            if chamber == "governor":
                for _, ballot, person, party_id, party in sorted(nominees.get(state, [])):
                    option = cs.key("us.option", f"{key}:{slug(ballot)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_id,
                        ballot_label=ballot,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{slug(ballot)}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        is_independent=False,
                    )
                    counts["candidates"] += 1
                if state in nominees:
                    cs.add(
                        "note",
                        entity_id=contest,
                        kind="methodology",
                        body_md=f"Candidates are the {state_name} major-party nominees recorded in the archive's own 2026 primary result sets. No ballot list from the state has been confirmed, so third-party and independent candidates are not yet listed.",
                    )
        print(counts)
        return cs.write()
