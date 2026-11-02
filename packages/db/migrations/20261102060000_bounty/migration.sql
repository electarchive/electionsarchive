create type bounty_status as enum ('open', 'fulfilled', 'closed');--> statement-breakpoint
create table bounty (
  id uuid primary key default uuidv7(),
  number integer generated always as identity unique,
  gap_key text not null,
  polity text not null,
  state text not null default '',
  level text not null default '',
  stage text not null default '',
  years text not null default '',
  grain text not null,
  ids text[] not null default '{}',
  label text not null,
  category text not null check (category in ('known_gap', 'blocked')),
  reason text not null default '',
  sources text not null default '',
  checked date,
  status bounty_status not null default 'open',
  closed_reason text,
  claimed_by uuid references users (id) on delete set null,
  claimed_until timestamptz,
  fulfilled_by_proposal uuid references proposal (id) on delete set null,
  opened_at timestamptz not null default now(),
  closed_at timestamptz,
  check ((claimed_by is null) = (claimed_until is null)),
  check ((status = 'open') = (closed_at is null)),
  check ((status = 'closed') = (closed_reason is not null)),
  check (status <> 'fulfilled' or fulfilled_by_proposal is not null)
);--> statement-breakpoint
create unique index bounty_gap_key_idx on bounty (gap_key);--> statement-breakpoint
create index bounty_open_idx on bounty (polity, grain, checked desc) where status = 'open';--> statement-breakpoint
create index bounty_claimed_by_idx on bounty (claimed_by) where claimed_by is not null;--> statement-breakpoint
create index bounty_fulfilled_idx on bounty (fulfilled_by_proposal) where fulfilled_by_proposal is not null;--> statement-breakpoint
alter table proposal add column bounty_id uuid references bounty (id) on delete set null;--> statement-breakpoint
create index proposal_bounty_id_idx on proposal (bounty_id) where bounty_id is not null;--> statement-breakpoint
alter type activity_kind add value 'bounty_claimed';--> statement-breakpoint
alter type activity_kind add value 'bounty_fulfilled';--> statement-breakpoint
alter type activity_kind add value 'bounty_closed';--> statement-breakpoint
alter table activity add column bounty_id uuid references bounty (id) on delete cascade;--> statement-breakpoint
create index activity_bounty_id_idx on activity (bounty_id) where bounty_id is not null;
