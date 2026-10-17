alter table entity add column retired_by uuid references changeset (id);
create index entity_retired_by_idx on entity (retired_by) where retired_by is not null;

alter type note_kind add value 'retired';

create table entity_retirement (
  entity_id uuid primary key references entity (id) on delete cascade,
  reason text not null check (reason <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

select ea_version('entity_retirement'::regclass);
