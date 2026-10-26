create table external_id_drop (
  entity_id uuid not null references entity (id) on delete cascade,
  scheme text not null,
  value text not null,
  reason text not null check (reason <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (scheme, value, changeset_id)
);

select ea_version('external_id_drop'::regclass);
