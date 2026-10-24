import time

from ea.store import connect

BUILD_SQL = """
with prom as (
    select person_id, max(prominence) as prominence from (
        select cy.person_id, case
            when o.kind in ('head_of_state', 'head_of_government') and p.kind in ('sovereign_state', 'supranational')
                and el.polity_id = o.polity_id then 3
            when o.kind in ('head_of_state', 'head_of_government', 'governor') and p.kind in ('federal_unit', 'region')
                and el.polity_id = o.polity_id then 1.5
            when b.kind = 'upper_house' and bp.kind = 'sovereign_state' then 1
            when b.kind in ('lower_house', 'unicameral') and bp.kind = 'sovereign_state' then 0.5
            else 0 end as prominence
        from candidacy cy
        join outcome oc on oc.option_id = cy.option_id and oc.kind in ('elected', 'won')
        join contest_option co on co.id = cy.option_id
        join contest c on c.id = co.contest_id and c.party_scope_id is null
        join election el on el.id = c.election_id and el.kind <> 'primary'
        left join office o on o.id = c.office_id
        left join polity p on p.id = o.polity_id
        left join body b on b.id = c.body_id
        left join polity bp on bp.id = b.polity_id
        where cy.role = 'head'
        union all
        select t.person_id, case
            when o.kind in ('head_of_state', 'head_of_government') and p.kind in ('sovereign_state', 'supranational') then 3
            when o.kind in ('head_of_state', 'head_of_government', 'governor') then 1.5
            else 0 end
        from tenure t join office o on o.id = t.office_id join polity p on p.id = o.polity_id
    ) x
    group by person_id
),
docs as (
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
            when 'contest' then (select 1 + 2 * (c.allocation_parent_id is null)::int + 0.5 * (el.kind = 'general')::int
                + (exists (select from contest ch where ch.allocation_parent_id = c.id))::int
                from contest c join election el on el.id = c.election_id where c.id = e.id)
            when 'party' then 1 + ln(1 + (select count(*) from contest_option o where o.party_id = e.id)) / 3
            when 'person' then ln(1 + (select count(*) from candidacy cy where cy.person_id = e.id)) / 2
                + (select count(*) from external_id x where x.entity_id = e.id and x.scheme = 'wikidata')
                + coalesce(pr.prominence, 0)
        end as rank_boost
    from entity e
    join name n on n.entity_id = e.id
    left join prom pr on pr.person_id = e.id
    where e.type in ('polity', 'election', 'contest', 'party', 'person')
        and not exists (select from entity_merge m where m.entity_id = e.id)
        and e.retired_by is null
        and (e.type in ('party', 'person') or exists (select from slug s where s.entity_id = e.id and s.is_current))
    group by e.id, e.type, pr.prominence
)
insert into search_document (entity_id, type, polity_id, text, tsv, rank_boost)
select entity_id, type, polity_id, text, to_tsvector('simple', text), rank_boost
from (
    select entity_id, type, polity_id, rank_boost,
        lower(unaccent(text))
            || case when text ~* '\\mu\\.s\\.' then ' united states' else '' end
            || case when text ~* '\\mpresidential\\M' then ' president' else '' end
            || case when text ~* '\\mgubernatorial\\M' then ' governor' else '' end
            || case when text ~* '\\msenatorial\\M' then ' senator' else '' end
            || case when text ~* '\\mcongressional\\M' then ' congress' else '' end
            || case when text ~* '\\mmayoral\\M' then ' mayor' else '' end
            || case when text ~* '\\mparliamentary\\M' then ' parliament' else '' end as text
    from docs
) d
"""


def build() -> str:
    started = time.monotonic()
    with connect() as conn:
        conn.execute("select pg_advisory_xact_lock(hashtext('ea.search'))")
        conn.execute("delete from search_document")
        inserted = conn.execute(BUILD_SQL).rowcount
        conn.execute("analyze search_document")
    return f"{inserted} search documents in {time.monotonic() - started:.0f}s"
