from ea.store import Connection

SHOWN = 12

CLASSIFY_SQL = """
with recursive census_polity as (
    select distinct polity_id from census_class
), tree(root, id) as (
    select polity_id, polity_id from census_polity
    union
    select tree.root, pp.polity_id from polity_parent pp
    join tree on pp.parent_id = tree.id
    join polity child on child.id = pp.polity_id
    where child.kind <> 'sovereign_state'
), scoped as (
    select tree.root, c.id from tree join election e on e.polity_id = tree.id join contest c on c.election_id = e.id
    union
    select tree.root, c.id from tree join body b on b.polity_id = tree.id join contest c on c.body_id = b.id
    union
    select tree.root, c.id from tree join office o on o.polity_id = tree.id join contest c on c.office_id = o.id
), held as (
    select scoped.root, c.id as contest_id, c.kind as contest_kind, c.body_id, c.office_id, e.kind as election_kind,
        e.date_from, b.kind as body_kind, o.kind as office_kind, op.id as unit_polity_id, op.kind as unit_polity_kind,
        op.iso_code as unit_iso, a.scheme_code,
        (select ac.code from area_code ac where ac.area_id = c.district_area_id
            order by ac.valid_from desc nulls last limit 1) as area_code,
        (select n.text from name n where n.entity_id = coalesce(c.body_id, c.office_id) and n.is_preferred limit 1) as label
    from scoped
    join contest c on c.id = scoped.id
    join election e on e.id = c.election_id
    left join body b on b.id = c.body_id
    left join office o on o.id = c.office_id
    left join polity op on op.id = coalesce(b.polity_id, o.polity_id, e.polity_id)
    left join area a on a.id = c.district_area_id
    where e.date_from is not null and not c.notional
), hit as (
    select h.root, h.contest_id, k.id as class_id,
        greatest(
            case when m.body_id is not null or m.office_id is not null then 4 else 0 end,
            case when m.name_pattern is not null then 3 else 0 end,
            case when m.schemes is not null then 2 else 0 end,
            1
        ) as rank, m.precedence
    from held h
    join census_class k on k.polity_id = h.root
    join census_match m on m.class_id = k.id
    where (m.election_kinds is null or h.election_kind = any(m.election_kinds))
        and (m.contest_kinds is null or h.contest_kind = any(m.contest_kinds))
        and (m.body_kinds is null or h.body_kind = any(m.body_kinds))
        and (m.office_kinds is null or h.office_kind = any(m.office_kinds))
        and (m.polity_kinds is null or h.unit_polity_kind = any(m.polity_kinds))
        and (m.within_polity_id is null or exists (
            select 1 from polity_parent pp where pp.polity_id = h.unit_polity_id and pp.parent_id = m.within_polity_id
        ) or h.unit_polity_id = m.within_polity_id)
        and (m.iso_pattern is null or coalesce(h.unit_iso, '') = m.iso_pattern
            or (m.iso_pattern like '%%*' and coalesce(h.unit_iso, '') like replace(m.iso_pattern, '*', '%%')))
        and (m.schemes is null or h.scheme_code = any(m.schemes))
        and (m.except_codes is null or not exists (
            select 1 from unnest(m.except_codes) x where coalesce(h.area_code, '') like x || '%%'))
        and (m.name_pattern is null or coalesce(h.label, '') ~* m.name_pattern)
        and (m.body_id is null or h.body_id = m.body_id)
        and (m.office_id is null or h.office_id = m.office_id)
        and (m.valid_from is null or h.date_from >= m.valid_from)
        and (m.valid_to is null or h.date_from < m.valid_to)
), top as (
    select root, contest_id, class_id, rank, precedence,
        rank() over (partition by root, contest_id order by rank desc, precedence desc) as place
    from (select distinct root, contest_id, class_id, rank, precedence from hit) d
)
"""
HELD_SQL = CLASSIFY_SQL + "select class_id, count(distinct contest_id) from top where place = 1 group by class_id"
CLASSIFY_SQL += """
select p.iso_code, 'census.unclassified', array_agg(h.contest_id::text order by h.contest_id), count(*)
from held h
join polity p on p.id = h.root
where not exists (select 1 from hit where hit.root = h.root and hit.contest_id = h.contest_id)
group by p.iso_code
union all
select p.iso_code, 'census.ambiguous', array_agg(t.contest_id::text order by t.contest_id), count(*)
from (
    select root, contest_id from top where place = 1 group by root, contest_id having count(distinct class_id) > 1
) t
join polity p on p.id = t.root
group by p.iso_code
order by 1, 2
"""


def held(conn: Connection) -> dict:
    return dict(conn.execute(HELD_SQL).fetchall())


def problems(conn: Connection) -> list[str]:
    found = []
    for iso, check, ids, total in conn.execute(CLASSIFY_SQL):
        shown = ", ".join(ids[:SHOWN]) + (f", and {total - SHOWN:,} more" if total > SHOWN else "")
        found.append(f"{check} {iso}: {total:,} contests: {shown}")
    return found
