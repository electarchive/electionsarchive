from uuid import UUID

from psycopg import Connection

from ea.changeset import Changeset

TWINS = """with named as (
             select distinct f.id, f.polity_id, f.kind, f.body_id, lower(n.text) as name
             from office f join name n on n.entity_id = f.id and n.is_preferred
             where not exists (select from entity_merge m where m.entity_id = f.id)
           ),
           twin as (
             select n.* from named n
             join (select polity_id, kind, body_id, name from named group by 1, 2, 3, 4 having count(*) > 1) g
               on g.polity_id = n.polity_id and g.kind = n.kind and g.body_id is not distinct from n.body_id
                  and g.name = n.name
           )
           select array_agg(t.id order by exists (select from entity_merge m where m.into_id = t.id) desc,
                            (select count(*) from contest c where c.office_id = t.id)
                            + (select count(*) from tenure u where u.office_id = t.id)
                            + (select count(*) from government v where v.office_id = t.id)
                            + (select count(*) from statewide_office_year y where y.office_id = t.id) desc,
                            t.id)
           from twin t group by t.polity_id, t.kind, t.body_id, t.name"""


class Offices:
    def __init__(self, conn: Connection) -> None:
        self.known: dict[tuple[UUID, str, str], str] = {}
        for polity, kind, name, value in conn.execute(
            """select distinct on (f.polity_id, f.kind, lower(n.text)) f.polity_id, f.kind::text, lower(n.text), x.value
               from office f
               join name n on n.entity_id = f.id and n.is_preferred
               join external_id x on x.entity_id = f.id and x.scheme = 'us.office'
               where f.body_id is null and not exists (select from entity_merge m where m.entity_id = f.id)
               order by f.polity_id, f.kind, lower(n.text), f.id, x.value"""
        ).fetchall():
            self.known[(polity, kind, name)] = value

    def key(self, cs: Changeset, value: str, polity: UUID, kind: str, title: str) -> UUID:
        existing = self.known.get((polity, kind, title.lower()), value)
        if existing == value:
            return cs.key("us.office", value)
        cs.renamed[value] = existing
        try:
            return cs.key("us.office", value)
        finally:
            del cs.renamed[value]
