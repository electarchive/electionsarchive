from datetime import date
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.tenures"
VERSION = "1"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House of Commons tenures for every Parliament from 1832 to the present, derived from the general "
            "election winners",
            polity="GB",
        )
        commons = cs.key("gb.body", "house-of-commons")
        if commons in cs.minted:
            raise ValueError("the House of Commons must already exist")
        elections: list[tuple[UUID, date]] = conn.execute(
            """select distinct e.id, e.date_from from election e join contest c on c.election_id = e.id
               where c.body_id = %s and e.kind = 'general' order by e.date_from""",
            [commons],
        ).fetchall()
        following = {election: elections[index + 1][1] for index, (election, _) in enumerate(elections[:-1])}
        seated = {
            election
            for (election,) in conn.execute(
                "select distinct c.election_id from tenure t join contest c on c.id = t.contest_id where t.body_id = %s",
                [commons],
            ).fetchall()
        }
        winners = conn.execute(
            """select e.id, e.date_from, c.id, cy.person_id, o.party_id from contest c
               join election e on e.id = c.election_id
               join result_set rs on rs.contest_id = c.id and rs.is_default
               join outcome x on x.result_set_id = rs.id and x.kind = 'elected'
               join contest_option o on o.id = x.option_id
               join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               where c.body_id = %s and e.kind = 'general'""",
            [commons],
        ).fetchall()
        for election, start, contest, person, party in winners:
            if election in seated:
                continue
            end = following.get(election)
            tenure = cs.key(f"{SOURCE}.tenure", f"{contest}:{person}")
            cs.add(
                "tenure",
                id=tenure,
                person_id=person,
                body_id=commons,
                from_date=start,
                from_precision="day",
                to_date=end,
                to_precision="day",
                acquired_via="elected",
                contest_id=contest,
                ended_via=None if end is None else "term_end",
            )
            if party is not None:
                cs.add(
                    "tenure_affiliation",
                    tenure_id=tenure,
                    kind="party",
                    party_id=party,
                    valid_from=start,
                    valid_from_precision="day",
                    valid_to=end,
                    valid_to_precision="day",
                )
        return cs.write()
