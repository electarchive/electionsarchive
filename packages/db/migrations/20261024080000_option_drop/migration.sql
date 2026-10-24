create table option_drop (
  option_id uuid primary key,
  contest_id uuid not null references contest (id) on delete cascade,
  reason text not null check (reason <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

select ea_version('option_drop'::regclass);
