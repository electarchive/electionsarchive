import time

from ea.store import connect

BUILD_SQL = """
with docs as (
    select e.id as entity_id, e.type,
        case e.type
            when 'polity' then e.id
            when 'election' then (select polity_id from election where id = e.id)
            when 'contest' then (select el.polity_id from contest c join election el on el.id = c.election_id where c.id = e.id)
            when 'party' then (select polity_id from party where id = e.id)
        end as polity_id,
        string_agg(distinct n.text, ' ') as text,
        case e.type
            when 'polity' then 4 + ln(1 + (select count(*) from election el where el.polity_id = e.id)) / 10
            when 'election' then 2 + (select (kind = 'general')::int from election where id = e.id)
            when 'contest' then 1 + (select (allocation_parent_id is null)::int from contest where id = e.id)
            when 'party' then 1 + ln(1 + (select count(*) from contest_option o where o.party_id = e.id)) / 3
            when 'person' then ln(1 + (select count(*) from candidacy cy where cy.person_id = e.id)) / 2
                + (select count(*) from external_id x where x.entity_id = e.id and x.scheme = 'wikidata')
        end as rank_boost
    from entity e
    join name n on n.entity_id = e.id
    where e.type in ('polity', 'election', 'contest', 'party', 'person')
        and not exists (select from entity_merge m where m.entity_id = e.id)
        and e.retired_by is null
        and (e.type in ('party', 'person') or exists (select from slug s where s.entity_id = e.id and s.is_current))
    group by e.id, e.type
)
insert into search_document (entity_id, type, polity_id, text, tsv, rank_boost)
select entity_id, type, polity_id, lower(unaccent(text)), to_tsvector('simple', lower(unaccent(text))), rank_boost
from docs
"""


def build() -> str:
    started = time.monotonic()
    with connect() as conn:
        conn.execute("select pg_advisory_xact_lock(hashtext('ea.search'))")
        conn.execute("delete from search_document")
        inserted = conn.execute(BUILD_SQL).rowcount
        conn.execute("analyze search_document")
    return f"{inserted} search documents in {time.monotonic() - started:.0f}s"
