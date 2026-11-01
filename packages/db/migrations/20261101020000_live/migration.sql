create schema live;--> statement-breakpoint
create table live.feed (
  id text primary key,
  adapter text not null,
  params jsonb not null default '{}',
  publisher text not null,
  url text,
  poller text not null default 'fsn1',
  interval_s integer not null default 30 check (interval_s >= 15),
  enabled boolean not null default true,
  note text
);--> statement-breakpoint
create table live.map (
  feed_id text not null references live.feed (id) on delete cascade,
  kind text not null check (kind in ('contest', 'option', 'unit')),
  scope text not null default '',
  label text not null,
  target uuid,
  primary key (feed_id, kind, scope, label)
);--> statement-breakpoint
create table live.fetch (
  id bigint generated always as identity primary key,
  feed_id text not null references live.feed (id) on delete cascade,
  fetched_at timestamptz not null default now(),
  as_of timestamptz,
  sha256 text,
  path text,
  status text not null check (status in ('frame', 'unchanged', 'rejected', 'error')),
  detail text
);--> statement-breakpoint
create index live_fetch_feed_idx on live.fetch (feed_id, fetched_at desc);--> statement-breakpoint
create table live.frame (
  k bigint generated always as identity primary key,
  feed_id text not null references live.feed (id) on delete cascade,
  contest_id uuid not null,
  as_of timestamptz not null,
  fetch_id bigint not null references live.fetch (id),
  reporting integer,
  expected integer,
  status text not null default 'active' check (status in ('active', 'withdrawn')),
  unique (feed_id, contest_id, as_of)
);--> statement-breakpoint
create index live_frame_contest_idx on live.frame (contest_id, as_of desc) where status = 'active';--> statement-breakpoint
create table live.value (
  frame_k bigint not null references live.frame (k) on delete cascade,
  unit_id uuid not null,
  option_id uuid not null,
  votes numeric not null,
  primary key (frame_k, unit_id, option_id)
);--> statement-breakpoint
create table live.issue (
  id bigint generated always as identity primary key,
  feed_id text not null references live.feed (id) on delete cascade,
  seen_at timestamptz not null default now(),
  kind text not null,
  detail text not null,
  resolved boolean not null default false
);
