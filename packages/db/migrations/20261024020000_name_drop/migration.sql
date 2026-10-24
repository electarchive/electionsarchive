create table name_drop (
  name_id uuid primary key,
  entity_id uuid not null references entity (id) on delete cascade,
  text text not null,
  reason text not null check (reason <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

select ea_version('name_drop'::regclass);
