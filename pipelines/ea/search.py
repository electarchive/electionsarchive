import time

from ea.store import connect

SIZES_SQL = """
create temporary table search_size on commit drop as
with totals as materialized (
    select area_k, result_set_k, sum(value) as total
    from result_rollup_sum
    where measure = 1 and channel = 0 and count_round = 0
    group by area_k, result_set_k
),
votes as (
    select polity_id, max(total) as total from (
        select t.polity_id, sum(r.total) as total
        from polity_territory t join area a on a.id = t.area_id
        join totals r on r.area_k = a.k
        where not exists (select from coverage_government g where g.polity_id = t.polity_id and g.population is not null)
        group by t.polity_id, r.result_set_k
    ) v
    group by polity_id
)
select p.id, coalesce(pop.population, 2.5 * votes.total) as people
from polity p
left join (select polity_id, max(population) as population from coverage_government group by polity_id) pop
    on pop.polity_id = p.id
left join votes on votes.polity_id = p.id
where exists (select from slug s where s.entity_id = p.id and s.is_current)
"""

BUILD_SQL = """
create temporary table search_build on commit drop as
with prom as (
    select person_id, max(prominence) as prominence from (
        select cy.person_id, case
            when o.kind in ('head_of_state', 'head_of_government') and p.kind in ('sovereign_state', 'supranational')
                and el.polity_id = o.polity_id then 3
            when o.kind in ('head_of_state', 'head_of_government', 'governor') and p.kind in ('federal_unit', 'region')
                and el.polity_id = o.polity_id then 1.5
            when b.kind = 'upper_house' and bp.kind = 'sovereign_state' then 1
            when b.kind in ('lower_house', 'unicameral') and bp.kind = 'sovereign_state' then 0.5
            when o.kind = 'mayor' then least(1.5, greatest(0, ln(1 + coalesce(sz.people, 0)) - 11) / 2)
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
        left join search_size sz on sz.id = o.polity_id
        where cy.role = 'head'
        union all
        select t.person_id, case
            when o.kind in ('head_of_state', 'head_of_government') and p.kind in ('sovereign_state', 'supranational') then 3
            when o.kind in ('head_of_state', 'head_of_government', 'governor') then 1.5
            when o.kind = 'mayor' then least(1.5, greatest(0, ln(1 + coalesce(sz.people, 0)) - 11) / 2)
            else 0 end
        from tenure t join office o on o.id = t.office_id join polity p on p.id = o.polity_id
        left join search_size sz on sz.id = o.polity_id
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
            when 'body' then (select polity_id from body where id = e.id)
        end as polity_id,
        string_agg(distinct n.text, ' ') || case when e.type = 'polity' then coalesce(' ' || (
            select string_agg(distinct substr(mn.text, 10), ' ') from office o
            join name mn on mn.entity_id = o.id and mn.is_preferred and mn.text like 'Mayor of %'
            where o.polity_id = e.id and o.kind = 'mayor'
        ), '') else '' end as text,
        case e.type
            when 'polity' then 4 + ln(1 + (select count(*) from election el where el.polity_id = e.id)) / 10
                + coalesce(ln(1 + sz.people) / 3, 0)
            when 'election' then 2 + (select (kind = 'general')::int from election where id = e.id)
            when 'contest' then (select 1 + 2 * (c.allocation_parent_id is null)::int + 0.5 * (el.kind = 'general')::int
                + (exists (select from contest ch where ch.allocation_parent_id = c.id))::int
                from contest c join election el on el.id = c.election_id where c.id = e.id)
            when 'party' then 1 + ln(1 + (select count(*) from contest_option o where o.party_id = e.id)) / 3
            when 'person' then ln(1 + (select count(*) from candidacy cy where cy.person_id = e.id)) / 2
                + (select count(*) from external_id x where x.entity_id = e.id and x.scheme = 'wikidata')
                + coalesce(pr.prominence, 0)
            when 'body' then 2 + ln(1 + (select count(*) from tenure t where t.body_id = e.id)) / 3
        end as rank_boost
    from entity e
    join name n on n.entity_id = e.id
    left join prom pr on pr.person_id = e.id
    left join search_size sz on sz.id = e.id
    where e.type in ('polity', 'body', 'election', 'contest', 'party', 'person')
        and not exists (select from entity_merge m where m.entity_id = e.id)
        and e.retired_by is null
        and (e.type in ('party', 'person') or exists (select from slug s where s.entity_id = e.id and s.is_current)
            or e.type = 'body' and exists (select from body b where b.id = e.id and b.kind <> 'electoral_college'))
    group by e.id, e.type, pr.prominence, sz.people
)
select entity_id, type, polity_id, text, to_tsvector('simple', text) as tsv, rank_boost
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

STALE_SQL = """
delete from search_document d
where not exists (
    select from search_build b
    where b.entity_id = d.entity_id and b.type = d.type and b.polity_id is not distinct from d.polity_id
        and b.text = d.text and b.rank_boost::real = d.rank_boost
)
"""

FRESH_SQL = """
insert into search_document (entity_id, type, polity_id, text, tsv, rank_boost)
select b.entity_id, b.type, b.polity_id, b.text, b.tsv, b.rank_boost
from search_build b join entity e on e.id = b.entity_id and e.retired_by is null
where not exists (select from search_document d where d.entity_id = b.entity_id)
    and not exists (select from entity_merge m where m.entity_id = b.entity_id)
    and (b.polity_id is null or exists (
        select from polity p join entity pe on pe.id = p.id and pe.retired_by is null
        where p.id = b.polity_id and not exists (select from entity_merge m where m.entity_id = p.id)
    ))
"""


def build() -> str:
    started = time.monotonic()
    with connect() as conn:
        for scan in ("indexscan", "indexonlyscan", "bitmapscan"):
            conn.execute(f"set local enable_{scan} = off")
        conn.execute(SIZES_SQL)
        for scan in ("indexscan", "indexonlyscan", "bitmapscan"):
            conn.execute(f"reset enable_{scan}")
        conn.execute("analyze search_size")
        conn.execute(BUILD_SQL)
        conn.execute("analyze search_build")
        conn.execute("select pg_advisory_xact_lock(hashtext('ea.search'))")
        deleted = conn.execute(STALE_SQL).rowcount
        inserted = conn.execute(FRESH_SQL).rowcount
        conn.execute("analyze search_document")
    return f"{deleted} search documents removed and {inserted} written in {time.monotonic() - started:.0f}s"
