from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.nnv_split"
VERSION = "1"
COLLAPSED = "1824-10:OH:house:0"


def fetch() -> None:
    pass


def parse() -> None:
    pass


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="THEA's 1824 Ohio House election, filed as one at-large contest for 14 seats, gives way to "
            "A New Nation Votes' 14 district contests, with the tenures relinked and THEA's set kept as non-default",
            polity="US",
            kind="correction",
        )
        contest = conn.execute(
            """select c.id from external_id x join contest c on c.id = x.entity_id
               where x.scheme = 'us.contest' and x.value = %s""",
            [COLLAPSED],
        ).fetchall()[0][0]
        districts = dict(
            conn.execute(
                """select cd.person_id, c.id from external_id x join contest c on c.id = x.entity_id
                   join entity n on n.id = c.id join changeset s on s.id = n.changeset_id
                   join result_set r on r.contest_id = c.id and r.is_default
                   join outcome o on o.result_set_id = r.id and o.kind = 'elected'
                   join candidacy cd on cd.option_id = o.option_id and cd.role = 'head'
                   where x.scheme = 'us.contest' and x.value ~ '^1824-[0-9]{2}:OH:house:[0-9]+$'
                     and x.value <> %s and s.pipeline = 'us.nnv' and c.previous_round_id is null""",
                [COLLAPSED],
            ).fetchall()
        )
        names: list[str] = []
        for row in conn.execute(
            """select t.id, t.person_id, t.office_id, t.seat_id, t.body_id, t.from_date, t.from_precision, t.to_date,
                      t.to_precision, t.acquired_via, t.contest_id, t.ended_via, t.resumes_tenure_id,
                      t.succeeds_tenure_id, (select text from name where entity_id = t.person_id and is_preferred limit 1)
               from tenure t where t.contest_id = %s""",
            [contest],
        ).fetchall():
            *values, name = row
            tenure = dict(
                zip(
                    [
                        "id",
                        "person_id",
                        "office_id",
                        "seat_id",
                        "body_id",
                        "from_date",
                        "from_precision",
                        "to_date",
                        "to_precision",
                        "acquired_via",
                        "contest_id",
                        "ended_via",
                        "resumes_tenure_id",
                        "succeeds_tenure_id",
                    ],
                    values,
                    strict=True,
                )
            )
            if tenure["person_id"] not in districts:
                raise ValueError(f"{name} won no NNV district contest of 1824")
            tenure["contest_id"] = districts[tenure["person_id"]]
            cs.add("tenure", **tenure)
            names.append(name)
        if len(names) != 14:
            raise ValueError(f"expected 14 tenures on {COLLAPSED}, found {len(names)}")
        demoted = 0
        for row in conn.execute(
            """select id, election_id, contest_id, stage, kind, source_id, as_of, completeness
               from result_set where contest_id = %s and is_default""",
            [contest],
        ).fetchall():
            result_set, election_id, contest_id, stage, kind, source_id, as_of, completeness = row
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest_id,
                stage=stage,
                kind=kind,
                source_id=source_id,
                as_of=as_of,
                status="superseded",
                is_default=False,
                supersedes_id=None,
                completeness=completeness,
            )
            demoted += 1
        cs.add(
            "note",
            entity_id=contest,
            kind="caveat",
            body_md="THEA files Ohio's 1824 House election as one contest for 14 seats, but Ohio elected its members "
            "by district. A New Nation Votes' returns for the 14 districts hold the election, and the members' "
            "tenures link to them; THEA's set here stays as a non-default record.",
        )
        print(f"tenures relinked to NNV's district contests: {len(names)}")
        print(f"THEA sets made non-default: {demoted}")
        return cs.write()
