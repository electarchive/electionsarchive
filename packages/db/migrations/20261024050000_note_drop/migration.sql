create table note_drop (
  note_id uuid primary key,
  entity_id uuid not null references entity (id) on delete cascade,
  body_md text not null,
  reason text not null check (reason <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

select ea_version('note_drop'::regclass);
