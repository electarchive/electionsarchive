create table if not exists contest_top (
  contest_id uuid primary key,
  area_k integer not null,
  tops jsonb not null,
  inferred uuid[] not null default '{}',
  refs uuid[] not null default '{}',
  built_at timestamptz not null default now()
);--> statement-breakpoint
create index if not exists contest_top_inferred_idx on contest_top using gin (inferred);--> statement-breakpoint
create index if not exists contest_top_refs_idx on contest_top using gin (refs);
