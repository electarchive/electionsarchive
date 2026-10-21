from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.fusion"
VERSION = "1"
STATES = ("ct", "de", "id", "ms", "nh", "ny", "or", "sc", "vt")
LINES = """
with fused as (
  select r.id, r.contest_id, array_agg(distinct p.party) as parties
  from contest_option r
  join contest_option ch on ch.parent_option_id = r.id and ch.party_id is not null
  cross join lateral (values (ch.party_id), (r.party_id)) p(party)
  where r.kind in ('candidate', 'ticket') and p.party is not null
  group by r.id, r.contest_id
  having count(distinct ch.party_id) >= 2
)
select distinct cy.id, coalesce(m.into_id, p.party)
from fused f
join contest c on c.id = f.contest_id and c.party_scope_id is null
join election e on e.id = c.election_id and e.kind in ('general', 'special', 'local')
join slug s on s.entity_id = c.id and s.is_current and split_part(s.path, '/', 2) = any(%s)
join candidacy cy on cy.option_id = f.id and cy.role = 'head'
cross join unnest(f.parties) p(party)
left join entity_merge m on m.entity_id = p.party
where not exists (
  select from candidacy_nomination n join changeset x on x.id = n.changeset_id
  where n.candidacy_id = cy.id and n.party_id = coalesce(m.into_id, p.party) and x.pipeline <> %s
)
"""


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Every party line a candidate held in a general or special election in a fusion state (New York, "
                "Connecticut, New Hampshire, South Carolina, Delaware, Oregon, Vermont, Idaho, and Mississippi) as "
                "that party's nomination, read from the published ballot lines"
            ),
            polity="US",
            kind="edit",
        )
        rows = conn.execute(LINES, [list(STATES), SOURCE]).fetchall()
        for candidacy, party in rows:
            cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party, kind="nominated")
        print(f"{len(rows)} nominations for {len({candidacy for candidacy, _ in rows})} candidacies")
        return cs.write()
