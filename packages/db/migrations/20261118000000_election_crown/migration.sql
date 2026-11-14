create table election_crown (
  election_id uuid primary key references election (id) on delete cascade,
  parent_id uuid not null references election (id) on delete cascade,
  office text,
  party_id uuid,
  person_id uuid,
  label text,
  area_id uuid,
  group_id uuid,
  shaped_id uuid,
  is_led boolean,
  votes jsonb,
  chain uuid[],
  built_at timestamptz not null default now()
);--> statement-breakpoint
create index election_crown_parent_idx on election_crown (parent_id);--> statement-breakpoint
create table election_crown_mark (
  id boolean primary key default true check (id),
  published_at timestamptz not null
);
