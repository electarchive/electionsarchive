set lock_timeout = '5s';

create or replace function ea_stamp() returns trigger language plpgsql as $$
begin
  if tg_op = 'UPDATE' and current_setting('ea.repointing', true) = 'on' then
    new.changeset_id := old.changeset_id;
  else
    new.changeset_id := ea_current_changeset();
  end if;
  new.recorded_from := now();
  return new;
end $$;

create or replace function ea_stamp_result_set() returns trigger language plpgsql as $$
begin
  if tg_op = 'UPDATE' and (
       current_setting('ea.repointing', true) = 'on'
       or old.is_default is distinct from new.is_default
          and to_jsonb(old) - 'is_default' - 'changeset_id' - 'recorded_from'
            = to_jsonb(new) - 'is_default' - 'changeset_id' - 'recorded_from') then
    new.changeset_id := old.changeset_id;
  else
    new.changeset_id := ea_current_changeset();
  end if;
  new.recorded_from := now();
  return new;
end $$;

-- Data fix. Until this migration, a merge's re-point UPDATE (apply.py, under ea.repointing) restamped every row
-- that referenced a merged entity with the merging changeset, so rows another changeset staged looked like the
-- merging pipeline's own and the owning pipeline's replaced/stale checks and key-provenance lookups missed them.
-- For each row still stamped by a published merging changeset X, this compares the version X wrote with the version
-- just before X (from the history tables). When the only differences are X's re-points (a merged entity replaced by
-- its survivor, plus is_preferred/is_current set false), the stamp goes back to the earlier version's changeset,
-- exactly what the new trigger would have kept. Passes repeat until nothing changes, so chains of merges walk back
-- to the changeset that staged the row. Rows X staged itself keep X: their pre-image carries X's stamp, or is a
-- version X wrote and replaced in its own transaction, which history records with an empty range. A fixpoint that has
-- not converged after ten passes aborts the migration. Triggers are off for the fix, so it writes no history and keeps
-- recorded_from at X's published_at, which revert uses to find the rows X re-pointed.
do $fix$
declare
  rel regclass;
  name text;
  keys text[];
  kinds text[];
  ops text[];
  raw text;
  mapped text;
  joins text;
  on_raw text;
  on_mapped text;
  on_update text;
  selected text;
  n integer;
  pass integer := 0;
  changed bigint;
  total bigint;
begin
  perform set_config('session_replication_role', 'replica', true);
  perform set_config('lock_timeout', '15min', true);
  create temp table merging on commit drop as
    select c.id, c.published_at as at from changeset c
    where c.status = 'published' and c.id in (select changeset_id from entity_merge);
  alter table merging add primary key (id);
  create temp table moves on commit drop as
    select m.changeset_id as cs, m.entity_id as entity, m.into_id as target, m.entity_id::text as entity_text,
           m.into_id::text as target_text
    from entity_merge m join merging x on x.id = m.changeset_id;
  alter table moves add primary key (cs, entity);
  create index on moves (cs, entity_text);
  create temp table versioned on commit drop as
    select c.oid::regclass as relid, c.relname::text as relation, null::text[] as pk, null::text[] as op from pg_class c
    where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p') and not c.relispartition
      and exists (select from pg_attribute a where a.attrelid = c.oid and a.attname = 'changeset_id' and not a.attisdropped)
      and exists (select from pg_class h where h.relnamespace = c.relnamespace and h.relname = c.relname || '_history');
  for rel, name in select v.relid, v.relation from versioned v order by v.relation loop
    if exists (select from pg_attribute where attrelid = rel and attname = 'id' and not attisdropped) then
      keys := array['id'];
      kinds := array['id'];
      ops := array['='];
    else
      select array_agg(a.attname::text order by k.o), array_agg(format_type(a.atttypid, a.atttypmod) order by k.o),
             array_agg(case when a.attnotnull then '=' else 'is not distinct from' end order by k.o)
        into keys, kinds, ops
        from (select conrelid, conkey from pg_constraint where conrelid = rel and contype in ('p', 'u')
              order by contype, conname limit 1) c
        cross join lateral unnest(c.conkey) with ordinality k(attnum, o)
        join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum;
    end if;
    update versioned set pk = keys, op = ops where relid = rel;
    raw := ''; mapped := ''; joins := '';
    for n in 1 .. array_length(keys, 1) loop
      raw := raw || format(', h.%I as r%s', keys[n], n);
      if kinds[n] = 'uuid' then
        joins := joins || format(' left join moves m%1$s on m%1$s.cs = h.superseded_by and m%1$s.entity = h.%2$I', n, keys[n]);
        mapped := mapped || format(', coalesce(m%s.target, h.%I) as k%s', n, keys[n], n);
      else
        mapped := mapped || format(', h.%I as k%s', keys[n], n);
      end if;
    end loop;
    execute format(
      'create temp table %I on commit drop as
         select h.changeset_id as prev, h.superseded_by, lower(h.recorded) as since,
                to_jsonb(h) - ''recorded'' - ''superseded_by'' - ''changeset_id'' - ''recorded_from'' as j %s %s
         from public.%I h %s
         where not isempty(h.recorded)
           and (h.superseded_by in (select id from merging) or lower(h.recorded) in (select at from merging))',
      'v_' || name, raw, mapped, name || '_history', joins);
    execute format('create index on %I (superseded_by, %s, since)', 'v_' || name,
      (select string_agg('k' || i, ', ') from generate_series(1, array_length(keys, 1)) i));
    execute format('create index on %I (since, %s)', 'v_' || name,
      (select string_agg('r' || i, ', ') from generate_series(1, array_length(keys, 1)) i));
  end loop;
  loop
    pass := pass + 1;
    total := 0;
    for name, keys, ops in select v.relation, v.pk, v.op from versioned v order by v.relation loop
      on_raw := 'true'; on_mapped := 'true'; on_update := 'true'; selected := '';
      for n in 1 .. array_length(keys, 1) loop
        on_raw := on_raw || format(' and w.r%s %s t.%I', n, ops[n], keys[n]);
        on_mapped := on_mapped || format(' and p.k%s %s t.%I', n, ops[n], keys[n]);
        on_update := on_update || format(' and t.%1$I %2$s s.%1$I', keys[n], ops[n]);
        selected := selected || format(', t.%I', keys[n]);
      end loop;
      execute format(
        'update public.%1$I t set changeset_id = s.prev
         from (
           select p.prev %2$s
           from public.%1$I t join merging x on x.id = t.changeset_id
           cross join lateral (
             select case when t.recorded_from = x.at then to_jsonb(t) - ''changeset_id'' - ''recorded_from''
                         else (select w.j from %3$I w where w.since = x.at and %4$s limit 1) end as j) w
           cross join lateral (
             select p.prev, p.j from %3$I p where p.superseded_by = x.id and %5$s order by p.since limit 1) p
           where w.j is not null and p.prev <> x.id
             and exists (
               select from jsonb_each(w.j) e join moves m on m.cs = x.id
               where m.entity_text = p.j ->> e.key and m.target_text = e.value #>> ''{}'')
             and not exists (
               select from jsonb_each(w.j) e
               where e.value is distinct from p.j -> e.key
                 and not (e.key in (''is_preferred'', ''is_current'') and e.value = ''false''::jsonb)
                 and not exists (
                   select from moves m
                   where m.cs = x.id and m.entity_text = p.j ->> e.key and m.target_text = e.value #>> ''{}''))
         ) s
         where %6$s',
        name, selected, 'v_' || name, on_raw, on_mapped, on_update);
      get diagnostics changed = row_count;
      if changed > 0 then
        raise notice 'pass % %: % rows', pass, name, changed;
      end if;
      total := total + changed;
    end loop;
    exit when total = 0;
    if pass = 10 then
      raise exception 'restamp did not converge in 10 passes';
    end if;
  end loop;
  perform set_config('session_replication_role', 'origin', true);
  raise notice 'restamp: % passes', pass;
end $fix$;
