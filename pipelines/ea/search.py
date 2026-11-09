import time
from typing import LiteralString

from psycopg import sql

from ea.store import Connection, connect

SIZES_SQL = """
create temporary table {size} on commit drop as
with totals as materialized (
    select area_k, result_set_k, sum(value) as total
    from result_rollup_sum
    where measure = 1 and channel = 0 and count_round = 0 and {areas}
    group by area_k, result_set_k
),
votes as (
    select polity_id, max(total) as total from (
        select t.polity_id, sum(r.total) as total
        from polity_territory t join area a on a.id = t.area_id
        join totals r on r.area_k = a.k
        where not exists (select from coverage_government g where g.polity_id = t.polity_id and g.population is not null)
            and {territory}
        group by t.polity_id, r.result_set_k
    ) v
    group by polity_id
)
select p.id, coalesce(pop.population, 2.5 * votes.total) as people
from polity p
left join (select polity_id, max(population) as population from coverage_government group by polity_id) pop
    on pop.polity_id = p.id
left join votes on votes.polity_id = p.id
where exists (select from slug s where s.entity_id = p.id and s.is_current) and {polity}
"""

BUILD_SQL = """
create temporary table {build} on commit drop as
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
        from {candidates} cy
        join outcome oc on oc.option_id = cy.option_id and oc.kind in ('elected', 'won')
        join contest_option co on co.id = cy.option_id
        join contest c on c.id = co.contest_id and c.party_scope_id is null
        join election el on el.id = c.election_id and el.kind <> 'primary'
        left join office o on o.id = c.office_id
        left join polity p on p.id = o.polity_id
        left join body b on b.id = c.body_id
        left join polity bp on bp.id = b.polity_id
        left join {size} sz on sz.id = o.polity_id
        where cy.role = 'head'
        union all
        select t.person_id, case
            when o.kind in ('head_of_state', 'head_of_government') and p.kind in ('sovereign_state', 'supranational') then 3
            when o.kind in ('head_of_state', 'head_of_government', 'governor') then 1.5
            when o.kind = 'mayor' then least(1.5, greatest(0, ln(1 + coalesce(sz.people, 0)) - 11) / 2)
            else 0 end
        from tenure t join office o on o.id = t.office_id join polity p on p.id = o.polity_id
        left join {size} sz on sz.id = o.polity_id
        where {holder}
    ) x
    group by person_id
),
ents as (
    select e.id, e.type
    from entity e
    left join (select distinct entity_id from slug where is_current and {slug}) sl on sl.entity_id = e.id
    left join (select id from body where kind <> 'electoral_college' and {body}) bd on bd.id = e.id
    left join (select distinct entity_id from entity_merge where {merge}) mg on mg.entity_id = e.id
    where e.type in ('polity', 'body', 'election', 'contest', 'party', 'person') and {entity}
        and mg.entity_id is null
        and e.retired_by is null
        and (e.type in ('party', 'person') or sl.entity_id is not null or e.type = 'body' and bd.id is not null)
),
names as (
    select n.entity_id, string_agg(distinct n.text, ' ') as text
    from name n join ents on ents.id = n.entity_id
    group by n.entity_id
),
mayors as (
    select o.polity_id, string_agg(distinct substr(mn.text, 10), ' ') as text
    from office o
    join name mn on mn.entity_id = o.id and mn.is_preferred and mn.text like 'Mayor of %'
    where o.kind = 'mayor' and {mayor}
    group by o.polity_id
),
election_counts as (select polity_id, count(*) as n from election where {election} group by polity_id),
contest_parents as (select distinct allocation_parent_id as id from contest where allocation_parent_id is not null and {parent}),
party_counts as (select party_id, count(*) as n from contest_option where party_id is not null and {party} group by party_id),
candidacy_counts as (select person_id, count(*) as n from candidacy where {candidacy} group by person_id),
wikidata_counts as (select entity_id, count(*) as n from external_id where scheme = 'wikidata' and {wikidata} group by entity_id),
tenure_counts as (select body_id, count(*) as n from tenure where {seat} group by body_id),
docs as (
    select ents.id as entity_id, ents.type,
        case ents.type
            when 'polity' then ents.id
            when 'election' then ee.polity_id
            when 'contest' then ce.polity_id
            when 'party' then pa.polity_id
            when 'body' then bo.polity_id
        end as polity_id,
        nm.text || case when ents.type = 'polity' then coalesce(' ' || my.text, '') else '' end as text,
        case ents.type
            when 'polity' then 4 + ln(1 + coalesce(ec.n, 0)) / 10 + coalesce(ln(1 + sz.people) / 3, 0)
            when 'election' then 2 + (ee.kind = 'general')::int
            when 'contest' then 1 + 2 * (cc.allocation_parent_id is null)::int + 0.5 * (ce.kind = 'general')::int
                + (cp.id is not null)::int
            when 'party' then 1 + ln(1 + coalesce(pc.n, 0)) / 3
            when 'person' then ln(1 + coalesce(cdc.n, 0)) / 2 + coalesce(wd.n, 0) + coalesce(pr.prominence, 0)
            when 'body' then 2 + ln(1 + coalesce(tc.n, 0)) / 3
        end as rank_boost
    from ents
    join names nm on nm.entity_id = ents.id
    left join election ee on ents.type = 'election' and ee.id = ents.id
    left join contest cc on ents.type = 'contest' and cc.id = ents.id
    left join election ce on ents.type = 'contest' and ce.id = cc.election_id
    left join contest_parents cp on ents.type = 'contest' and cp.id = ents.id
    left join party pa on ents.type = 'party' and pa.id = ents.id
    left join body bo on ents.type = 'body' and bo.id = ents.id
    left join mayors my on ents.type = 'polity' and my.polity_id = ents.id
    left join election_counts ec on ents.type = 'polity' and ec.polity_id = ents.id
    left join party_counts pc on ents.type = 'party' and pc.party_id = ents.id
    left join candidacy_counts cdc on ents.type = 'person' and cdc.person_id = ents.id
    left join wikidata_counts wd on ents.type = 'person' and wd.entity_id = ents.id
    left join tenure_counts tc on ents.type = 'body' and tc.body_id = ents.id
    left join prom pr on pr.person_id = ents.id
    left join {size} sz on sz.id = ents.id
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
from {build} b join entity e on e.id = b.entity_id and e.retired_by is null
where not exists (select from search_document d where d.entity_id = b.entity_id)
    and not exists (select from entity_merge m where m.entity_id = b.entity_id)
    and (b.polity_id is null or exists (
        select from polity p join entity pe on pe.id = p.id and pe.retired_by is null
        where p.id = b.polity_id and not exists (select from entity_merge m where m.entity_id = p.id)
    ))
"""

SCOPE_SQL = """
create temporary table search_scope on commit drop as
select entity_id as id from name where changeset_id = any(%(changesets)s::uuid[])
union select entity_id from name_history where superseded_by = any(%(changesets)s::uuid[])
union select entity_id from slug where changeset_id = any(%(changesets)s::uuid[])
union select entity_id from slug_history where superseded_by = any(%(changesets)s::uuid[])
union select id from entity where changeset_id = any(%(changesets)s::uuid[])
union select id from entity where retired_by = any(%(changesets)s::uuid[])
union select entity_id from entity_merge where changeset_id = any(%(changesets)s::uuid[])
union select into_id from entity_merge where changeset_id = any(%(changesets)s::uuid[])
union select entity_id from entity_merge_drop where changeset_id = any(%(changesets)s::uuid[])
"""

POLITY_SQL = """
create temporary table search_scope_polity on commit drop as
select id from search_scope
union select o.polity_id from tenure t join office o on o.id = t.office_id
where o.kind = 'mayor' and t.person_id in (select id from search_scope)
union select o.polity_id from candidacy cy
join contest_option co on co.id = cy.option_id
join contest c on c.id = co.contest_id
join office o on o.id = c.office_id
where o.kind = 'mayor' and cy.person_id in (select id from search_scope)
"""

SCANS = ("indexscan", "indexonlyscan", "bitmapscan")


def _create(conn: Connection, size: LiteralString, build: LiteralString, scoped: bool) -> None:
    def within(column: LiteralString, scope: LiteralString = "search_scope") -> sql.Composable:
        if not scoped:
            return sql.SQL("true")
        return sql.SQL("{} in (select id from {})").format(sql.SQL(column), sql.Identifier(scope))

    areas = sql.SQL("area_k in (select a.k from polity_territory t join area a on a.id = t.area_id where {})").format(
        within("t.polity_id", "search_scope_polity")
    )
    for query, fragments in (
        (
            SIZES_SQL,
            {
                "size": sql.Identifier(size),
                "areas": areas if scoped else sql.SQL("true"),
                "territory": within("t.polity_id", "search_scope_polity"),
                "polity": within("p.id", "search_scope_polity"),
            },
        ),
        (
            BUILD_SQL,
            {
                "build": sql.Identifier(build),
                "size": sql.Identifier(size),
                "candidates": sql.SQL(
                    "(select * from candidacy where person_id in (select id from search_scope) offset 0)"
                    if scoped
                    else "candidacy"
                ),
                "holder": within("t.person_id"),
                "slug": within("entity_id"),
                "body": within("id"),
                "merge": within("entity_id"),
                "entity": within("e.id"),
                "mayor": within("o.polity_id"),
                "election": within("polity_id"),
                "parent": within("allocation_parent_id"),
                "party": within("party_id"),
                "candidacy": within("person_id"),
                "wikidata": within("entity_id"),
                "seat": within("body_id"),
            },
        ),
    ):
        if not scoped:
            for scan in SCANS:
                conn.execute(f"set local enable_{scan} = off")
        conn.execute(sql.SQL(query).format(**fragments))
        if not scoped:
            for scan in SCANS:
                conn.execute(f"reset enable_{scan}")
        conn.execute(sql.SQL("analyze {}").format(sql.Identifier(size if query is SIZES_SQL else build)))


def scope(conn: Connection, changesets: list[str]) -> None:
    conn.execute(SCOPE_SQL, {"changesets": changesets})
    conn.execute("analyze search_scope")


def _scoped(conn: Connection) -> bool:
    found = conn.execute("select exists (select from search_scope)").fetchone()
    if found is None or not found[0]:
        conn.execute("drop table search_scope")
        return False
    conn.execute(POLITY_SQL)
    conn.execute("analyze search_scope_polity")
    _create(conn, "search_scope_size", "search_scope_build", scoped=True)
    return True


def _drop(conn: Connection) -> None:
    conn.execute("drop table search_scope, search_scope_polity, search_scope_size, search_scope_build")


def refresh(conn: Connection) -> int:
    if not _scoped(conn):
        return 0
    conn.execute("delete from search_document where entity_id in (select id from search_scope)")
    written = conn.execute(sql.SQL(FRESH_SQL).format(build=sql.Identifier("search_scope_build"))).rowcount
    _drop(conn)
    return written


def build() -> str:
    started = time.monotonic()
    with connect() as conn:
        conn.execute("select pg_advisory_xact_lock_shared(hashtext('ea.search'))")
        seen = [
            str(changeset)
            for (changeset,) in conn.execute("select id from changeset where status = 'published'").fetchall()
        ]
        _create(conn, "search_size", "search_build", scoped=False)
        for _ in range(240):
            taken = conn.execute("select pg_try_advisory_xact_lock(hashtext('ea.search'))").fetchone()
            if taken is not None and taken[0]:
                break
            time.sleep(5)
        else:
            conn.execute("select pg_advisory_xact_lock(hashtext('ea.search'))")
        since = [
            str(changeset)
            for (changeset,) in conn.execute(
                "select id from changeset where status = 'published' and id <> all(%s::uuid[])", [seen]
            ).fetchall()
        ]
        if since:
            scope(conn, since)
        if since and _scoped(conn):
            conn.execute("delete from search_build where entity_id in (select id from search_scope)")
            conn.execute("insert into search_build select * from search_scope_build")
            _drop(conn)
        deleted = conn.execute(STALE_SQL).rowcount
        inserted = conn.execute(sql.SQL(FRESH_SQL).format(build=sql.Identifier("search_build"))).rowcount
        conn.execute("analyze search_document")
    return f"{deleted} search documents removed and {inserted} written in {time.monotonic() - started:.0f}s"
