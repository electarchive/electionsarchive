create table membership_drop (
  person_id uuid not null references person (id) on delete cascade,
  party_id uuid not null references party (id),
  valid_from date,
  valid_to date,
  reason text not null check (reason <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  constraint membership_drop_key unique nulls not distinct (person_id, party_id, valid_from, valid_to)
);

select ea_version('membership_drop'::regclass);
