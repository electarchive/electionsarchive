set lock_timeout = '5s';

-- Data fix for 20261025070000_merge_repoint_stamp. When one publish both edited a row (for example identity demoting a
-- name's is_preferred when it stages a preferred name, apply.py) and then re-pointed it in a merge, history keeps the
-- edited version with an empty range, stamped and superseded by that merge. The earlier fix compared only the first
-- and last versions, saw nothing but re-points, and gave such rows back the stamp they had before the merge. A row
-- whose empty-range version differs from its pre-merge version without any merged-entity re-point was the merge's own
-- write, so its stamp goes back to the merge. Triggers are off, so this writes no history.
do $fix$
declare
  name text;
  n bigint;
  total bigint := 0;
begin
  perform set_config('session_replication_role', 'replica', true);
  perform set_config('lock_timeout', '15min', true);
  create temp table merging on commit drop as
    select c.id, c.published_at as at from changeset c
    where c.status = 'published' and c.id in (select changeset_id from entity_merge);
  alter table merging add primary key (id);
  create temp table moves on commit drop as
    select m.changeset_id as cs, m.entity_id::text as entity, m.into_id::text as target
    from entity_merge m join merging x on x.id = m.changeset_id;
  create index on moves (cs, entity);
  for name in
    select c.relname::text from pg_class c
    where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p')
      and exists (select from pg_attribute a where a.attrelid = c.oid and a.attname = 'id' and not a.attisdropped)
      and exists (select from pg_attribute a where a.attrelid = c.oid and a.attname = 'changeset_id' and not a.attisdropped)
      and exists (select from pg_class h where h.relnamespace = c.relnamespace and h.relname = c.relname || '_history')
    order by 1
  loop
    execute format(
      'update public.%1$I t set changeset_id = x.id
       from %2$I e join merging x on x.id = e.superseded_by
       cross join lateral (
         select to_jsonb(p) - ''recorded'' - ''superseded_by'' - ''changeset_id'' - ''recorded_from'' as j
         from %2$I p where p.id = e.id and p.superseded_by = x.id and not isempty(p.recorded)
         order by lower(p.recorded) limit 1) p
       where isempty(e.recorded) and e.changeset_id = e.superseded_by
         and t.id = e.id and t.recorded_from = x.at and t.changeset_id <> x.id
         and not exists (
           select from jsonb_each(to_jsonb(e) - ''recorded'' - ''superseded_by'' - ''changeset_id'' - ''recorded_from'') c
           join moves m on m.cs = x.id
           where m.entity = p.j ->> c.key and m.target = c.value #>> ''{}'')',
      name, name || '_history');
    get diagnostics n = row_count;
    if n > 0 then
      raise notice '%: % rows', name, n;
    end if;
    total := total + n;
  end loop;
  perform set_config('session_replication_role', 'origin', true);
  raise notice 'demotion restamp: % rows', total;
end $fix$;
