create table entity_merge (
  entity_id uuid primary key references entity (id) on delete cascade,
  into_id uuid not null references entity (id) on delete cascade,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check (entity_id <> into_id)
);
create index entity_merge_into_idx on entity_merge (into_id);

select ea_version('entity_merge'::regclass);
