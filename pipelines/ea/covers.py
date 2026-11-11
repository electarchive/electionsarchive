from psycopg import sql

from ea.store import Connection

WIDE = """select exists (select from source_history h join source s on s.id = h.id
                        where h.superseded_by = %(id)s and h.license is distinct from s.license)
            or exists (select from body_history h join body b on b.id = h.id
                       where h.superseded_by = %(id)s and h.kind is distinct from b.kind)
            or exists (select from polity_history h join polity p on p.id = h.id
                       where h.superseded_by = %(id)s and h.iso_code is distinct from p.iso_code)"""

STALE = """with recursive
merged(id) as (
  select unnest(array[entity_id, into_id]) from entity_merge where changeset_id = %(id)s
  union select unnest(array[entity_id, into_id]) from entity_merge_history where superseded_by = %(id)s
),
areas(id) as (
  select unnest(%(moved)s::uuid[])
  union select id from merged
  union select id from area_history where superseded_by = %(id)s
  union select area_id from area_parent_history where superseded_by = %(id)s
  union select p.area_id from area_parent p join entity n on n.id = p.area_id
        where p.changeset_id = %(id)s and n.changeset_id is distinct from p.changeset_id
  union select area_id from area_universe_history where superseded_by = %(id)s
  union select area_id from area_universe where changeset_id = %(id)s
),
units(id) as (
  select unnest(%(repointed)s::uuid[])
  union select id from merged
  union select id from reporting_unit_history where superseded_by = %(id)s
  union select unit_id from reporting_unit_area_history where superseded_by = %(id)s
  union select u.id from reporting_unit u join entity n on n.id = u.id
        where u.changeset_id = %(id)s and n.changeset_id is distinct from u.changeset_id
  union select x.unit_id from reporting_unit_area x join entity n on n.id = x.unit_id
        where x.changeset_id = %(id)s and n.changeset_id is distinct from x.changeset_id
),
sets(k) as (
  select k from result_set where changeset_id = %(id)s
  union select unnest(%(sets)s::bigint[])
  union select r.k from result_set_history h join result_set r on r.id = h.id where h.superseded_by = %(id)s
  union select r.k from outcome o join result_set r on r.id = o.result_set_id where o.changeset_id = %(id)s
  union select r.k from outcome_history o join result_set r on r.id = o.result_set_id where o.superseded_by = %(id)s
  union select r.k from result_set r where r.id in ({facts})
  union select x.result_set_k from result_rollup_sum x join area a on a.k = x.area_k where a.id in (select id from areas)
  union select v.result_set_k from result_value v join reporting_unit u on u.k = v.unit_k
        where u.id in (select id from units)
),
elections(id) as (
  select id from election where changeset_id = %(id)s
  union select id from election_history where superseded_by = %(id)s
  union select election_id from contest where changeset_id = %(id)s
  union select election_id from contest_history where superseded_by = %(id)s
  union select e.id from election e where e.polity_id in (
    select polity_id from polity_territory where changeset_id = %(id)s
    union select polity_id from polity_territory_history where superseded_by = %(id)s)
),
seed(id) as (
  select r.contest_id from result_set r where r.k in (select k from sets) and r.contest_id is not null
  union select contest_id from result_set_history where superseded_by = %(id)s and contest_id is not null
  union select id from contest where election_id in (select id from elections)
  union select allocation_parent_id from contest_history
        where superseded_by = %(id)s and allocation_parent_id is not null
),
later(id, depth) as (
  select id, 0 from seed
  union all select c.id, l.depth + 1 from contest c join later l on c.previous_round_id = l.id where l.depth < 8
),
up(id) as (
  select id from later
  union select c.allocation_parent_id from contest c join up on c.id = up.id where c.allocation_parent_id is not null
)
delete from contest_cover where id in (select id from up)"""


def uncover(
    conn: Connection,
    changeset_id: str | None,
    moved: list[str] | None = None,
    repointed: list[str] | None = None,
    sets: list[int] | None = None,
    facts: sql.Composable | None = None,
) -> None:
    if conn.execute("select to_regclass('public.contest_cover') is null").fetchone() == (True,):
        return
    params = {"id": changeset_id, "moved": moved or [], "repointed": repointed or [], "sets": sets or []}
    if changeset_id is not None and conn.execute(WIDE, params).fetchone() == (True,):
        conn.execute("delete from contest_cover")
        return
    conn.execute(sql.SQL(STALE).format(facts=facts or sql.SQL("select null::uuid")), params)
