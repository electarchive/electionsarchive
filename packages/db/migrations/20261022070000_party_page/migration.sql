create table party_page (
  party_id uuid primary key,
  page jsonb not null,
  built_at timestamptz not null default now()
);
