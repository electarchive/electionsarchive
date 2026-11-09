create table entity_merge_drop (
  entity_id uuid not null references entity (id) on delete cascade,
  reason text not null check (reason <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (entity_id, changeset_id)
);

select ea_version('entity_merge_drop'::regclass);
