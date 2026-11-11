alter type changeset_kind add value if not exists 'release';
alter table release add column if not exists changeset_id uuid references changeset (id);
