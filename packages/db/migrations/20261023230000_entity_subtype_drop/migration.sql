create table entity_subtype_drop (
  entity_id uuid not null references entity (id) on delete cascade,
  subtype text not null,
  reason text not null check (reason <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (entity_id, subtype)
);

select ea_version('entity_subtype_drop'::regclass);

create or replace function ea_entity() returns trigger language plpgsql as $$
begin
  insert into entity (id, type, changeset_id)
  values (new.id, tg_argv[0]::entity_type, ea_current_changeset())
  on conflict (id) do nothing;
  if exists (select from entity where id = new.id and type <> tg_argv[0]::entity_type) then
    raise exception '% is not a %', new.id, tg_argv[0];
  end if;
  return new;
end $$;
