from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.sources.us import algara, contingent
from ea.store import connect

SOURCE = "us.algara_systems"
VERSION = "1"
COLUMNS = (
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
    "notional",
)


def emit() -> Path | None:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governor elections that needed a majority, else the legislature chose, under their contingent "
            "systems (contests first written by us.algara, whose source is retired)",
            polity="US",
            kind="correction",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        majority = contingent.systems(cs, name)
        changed = 0
        for row in conn.execute(
            f"""select {", ".join(f"c.{column}" for column in COLUMNS)},
                       substr(p.iso_code, 4), extract(year from e.date_from)::int
                from contest c join entity n on n.id = c.id and n.retired_by is null
                join changeset h on h.id = c.changeset_id and h.pipeline in (%s, %s)
                join election e on e.id = c.election_id and e.kind = 'general'
                join office o on o.id = c.office_id and o.kind = 'governor'
                join polity p on p.id = o.polity_id
                where c.party_scope_id is null
                  and not exists (select from entity_merge m where m.entity_id = c.id)
                order by c.id""",
            [algara.SOURCE, SOURCE],
        ).fetchall():
            contest = dict(zip(COLUMNS, row[: len(COLUMNS)], strict=True))
            rule = contingent.governor(row[-2], row[-1])
            if rule is None:
                continue
            contest["electoral_system_id"] = majority(rule)
            cs.add("contest", **contest)
            changed += 1
        print(f"{changed} governor contests under a contingent system")
        return cs.write()
