from pathlib import Path

from ea.changeset import Changeset
from ea.sources.us.nnv import MAJORITY
from ea.store import connect

SOURCE = "us.nnv_trials"
VERSION = "2"
COLUMNS = [
    "id",
    "election_id",
    "kind",
    "office_id",
    "body_id",
    "district_area_id",
    "seat_id",
    "boundary_set_id",
    "magnitude",
    "party_scope_id",
    "round_number",
    "previous_round_id",
    "allocation_parent_id",
    "electoral_system_id",
    "rules_override",
    "default_result_kind",
    "term",
]


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
            summary="THEA's House contests before 1827 in the majority states take the repeated-trials majority rule, "
            "and THEA contests with trials in A New Nation Votes sit in their chain",
            polity="US",
            kind="correction",
        )
        majority = cs.key("us.electoral_system", "majority_trials")
        if majority in cs.minted:
            raise ValueError("us.nnv has not published majority_trials")
        trials: dict[str, list[tuple[int, object]]] = {}
        for key, contest in conn.execute(
            """select x.value, x.entity_id from external_id x join entity n on n.id = x.entity_id
               join changeset s on s.id = n.changeset_id
               where x.scheme = 'us.contest' and s.pipeline = 'us.nnv' and x.value ~ ':house:.*:ballot[0-9]+$'"""
        ).fetchall():
            base, _, number = key.rpartition(":ballot")
            trials.setdefault(base, []).append((int(number), contest))
        rows = conn.execute(
            """select x.value, c.id, c.election_id, c.kind, c.office_id, c.body_id, c.district_area_id, c.seat_id,
                      c.boundary_set_id, c.magnitude, c.party_scope_id, c.round_number, c.previous_round_id,
                      c.allocation_parent_id, c.electoral_system_id, c.rules_override, c.default_result_kind, c.term
               from external_id x join contest c on c.id = x.entity_id
               join entity n on n.id = c.id join changeset s on s.id = n.changeset_id
               where x.scheme = 'us.contest' and s.pipeline = 'us.thea' and x.value like '%%:house:%%'
                 and x.value ~ '^1[78][0-9]{2}' and left(x.value, 4)::int < 1827"""
        ).fetchall()
        systems = chained = 0
        for key, *values in rows:
            row = dict(zip(COLUMNS, values, strict=True))
            numbers = {number: contest for number, contest in trials.get(key, [])}
            changed = False
            if key.split(":")[1] in MAJORITY and row["electoral_system_id"] != majority:
                row["electoral_system_id"] = majority
                systems += 1
                changed = True
            if numbers:
                round_number = next(number for number in range(1, len(numbers) + 2) if number not in numbers)
                if round_number > 1:
                    row["round_number"] = round_number
                    row["previous_round_id"] = numbers[round_number - 1]
                    chained += 1
                    changed = True
            if changed:
                cs.add("contest", **row)
        print(f"THEA contests moved to majority_trials: {systems}")
        print(f"THEA contests chained after earlier NNV trials: {chained}")
        print(f"THEA contests with NNV trials: {sum(1 for key, *_ in rows if key in trials)}")
        return cs.write()
