create type changeset_entity_role as enum ('merged', 'retired', 'created', 'changed', 'cited');--> statement-breakpoint
create table changeset_entity (
  entity_id uuid not null,
  changeset_id uuid not null references changeset (id) on delete cascade,
  role changeset_entity_role not null,
  primary key (entity_id, changeset_id)
);--> statement-breakpoint
create index changeset_entity_changeset_idx on changeset_entity (changeset_id);--> statement-breakpoint
do $$
declare t text;
begin
  for t in
    select c.relname from pg_class c
    where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p') and not c.relispartition
      and c.relname like '%\_history'
      and exists (select from pg_attribute a where a.attrelid = c.oid and a.attname = 'superseded_by' and not a.attisdropped)
  loop
    execute format('create index if not exists %I on %I (superseded_by) where superseded_by is not null', t || '_superseded_by_idx', t);
  end loop;
  if exists (select from pg_roles where rolname = 'ea_reader') then grant select on changeset_entity to ea_reader; end if;
end $$;
