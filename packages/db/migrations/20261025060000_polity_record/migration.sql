create table polity_record (
  polity_id uuid primary key,
  record jsonb not null,
  built_at timestamptz not null default now()
);
